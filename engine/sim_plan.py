"""Plan the sim tenant set: one deterministic planned run per fleet, its truth file, and the tenant's expectation file.

`plan_fleet` is pure: same spec, clock and nonce give the same sessions, the same manifests, the same claims. The truth and the
expectations are written BEFORE anything is sent and are never sent. scripts/sim_set.py `send` rebuilds the same plan from the
recorded clock and nonce, so what is sent is exactly what the truth file describes (the file's SHA-256 is recorded).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from engine import sim_scenarios as S
from engine import sim_set as X
from engine.context_levers import PROFILES

TRUTH_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ground_truth", "sim_tenants")
H6_PHASE_CHECKS = {1: {"fresh": True, "sources": True, "empty": True}, 2: {"fresh": False, "sources": True, "empty": False}, 3: {"fresh": True, "sources": True, "empty": False}}


class FleetPlan:
    def __init__(self, tenant: dict, fleet: dict, outs: list, truth: list, expect: dict, nonce: str):
        self.tenant, self.fleet, self.outs, self.truth, self.expect, self.nonce = tenant, fleet, outs, truth, expect, nonce


def nonce_for(tenant: dict, fleet: dict, now: datetime) -> str:
    return f"s{tenant['key']}{fleet['key']}{now:%m%d%H}".lower()


def phase_of(index: int, sessions: int, phases: int) -> int:
    return min(phases, 1 + index * phases // sessions)


def approved_for(fleet: dict) -> list:
    d = fleet.get("declare") or {}
    if d.get("approved") == "profile":
        return list(PROFILES[fleet["pack"]].approved)
    return list(d.get("approved") or [])


def plan_fleet(tenant: dict, fleet: dict, now: datetime, nonce: str | None = None) -> FleetPlan:
    nonce = nonce or nonce_for(tenant, fleet, now)
    outs = S.build_runs(fleet, now, nonce)
    sc, pack = fleet["scenario"], fleet["pack"]
    if sc == "thin":
        S.apply_thin(outs, pack)
    elif sc == "none":
        S.apply_none(outs)
    elif sc == "instruction":
        S.apply_instruction_stories(outs, pack)
    if fleet.get("claims"):
        S.apply_claims(outs, pack, fleet["claims"]["profile"], int(fleet["seed"]))
    truth = S.truth_rows(fleet["key"], fleet, outs)
    expect: dict[str, Any] = {
        "door": fleet["door"], "scenario": sc, "pack": pack, "sessions": len(outs), "steps": sum(len(o.result.traces) for o in outs),
        # The OpenTelemetry door wraps each run in a root span (the guide's own advice) and the product stores it as a step: one more per session.
        "stored_steps": sum(len(o.result.traces) for o in outs) + (len(outs) if fleet["door"] == "otlp" else 0),
        "counted_steps": S.expected_counted_steps(truth), "outcomes": S.expected_outcome_counts(truth),
        "readiness": S.expected_readiness(outs, fleet["door"]),
        "session_ids": [o.result.session_id for o in outs],
    }
    if fleet.get("claims"):
        expect["calibration"] = S.expected_calibration(truth)
        expect["claims"] = len([r for r in truth if r.get("claim")])
    expect["declares"] = sorted(k for k in (("context_max_age" if (fleet.get("declare") or {}).get("limit_days") else None), ("context_log_fields" if fleet["door"] == "log_map" else None)) if k)
    if fleet.get("extra_criteria"):
        expect["unmeasurable_condition"] = S.PACK_ROLES[pack]["extra_signal"]
    d = fleet.get("declare")
    if d and d.get("limit_days"):
        limit_h = d["limit_days"] * 24
        approved = approved_for(fleet)
        agents = list(PROFILES[pack].agents)
        found = S.expected_declared_findings(outs, limit_h, approved, agents)
        if d.get("as") == "guardrails" and fleet.get("phases"):
            for sid, f in found.items():
                ph = phase_of(f["index"], len(outs), fleet["phases"])
                f["phase"] = ph
                if not H6_PHASE_CHECKS[ph]["fresh"]:
                    f["stale"] = []                       # the freshness rule is switched off in this phase: it writes nothing
            found = {k: v for k, v in found.items() if v["stale"] or v["unapproved"]}
        expect["declared"] = {"limit_hours": limit_h, "approved": approved, "findings": found,
                              "stale_sessions": sorted(k for k, v in found.items() if v["stale"]),
                              "unapproved_sessions": sorted(k for k, v in found.items() if v["unapproved"])}
    if fleet.get("phases"):
        expect["phases"] = {"count": fleet["phases"], "checks": H6_PHASE_CHECKS,
                            "session_phase": {o.result.session_id: phase_of(i, len(outs), fleet["phases"]) for i, o in enumerate(outs)}}
    if sc == "instruction":
        expect["instruction_findings"] = S.expected_instruction_findings(outs, pack)
        expect["instruction_stories"] = S.INSTRUCTION_STORIES[pack]
    if sc == "faults":
        expect["planted"] = {k: sum(1 for r in truth for f in r["faults"] if f["fault"] == k) for k in ("stale", "unapproved", "empty")}
    if sc == "roster":
        expect["roster"] = {"retire": fleet.get("retire", []), "never_accepted": fleet.get("never_accepted", []),
                            "agents": sorted({t.agent for o in outs for t in o.result.traces})}
    if sc == "doors":
        expect["door_limit"] = list(S.DOOR_LIMITS.get(fleet["door"], []))
    return FleetPlan(tenant, fleet, outs, truth, expect, nonce)


def order_expect(tenant: dict, today) -> dict | None:
    o = tenant.get("order")
    if not o:
        return None
    calls = X.order_bodies(o, today)
    out: dict[str, Any] = {"spec": o, "calls": [{"route": r, "body": b} for r, b in calls], "plan": X.fleet_plan_words(o)}
    # extension is its own order row: the pilot route writes a new order from the day it is made
    exp_rows = []
    for route, body in calls:
        if route == "/api/admin/orders":
            t = body.get("terms", {})
            exp_rows.append({"starts_on": body["startsOn"], "pilot_ends_on": t.get("pilotEndsOn"), "paid": body.get("paid", False), "has_pricing": True})
        else:
            prev = dict(exp_rows[-1])
            prev["starts_on"] = today.isoformat()
            prev["pilot_ends_on"] = body["pilotEndsOn"]
            exp_rows.append(prev)
    out["order_rows"] = exp_rows
    out["pilot_as_of_build"] = X.pilot_state(exp_rows, today)
    out["discount_percent"] = o.get("discount_percent")
    return out


def write_tenant(tenant: dict, plans: dict, now: datetime, today, outdir: str = TRUTH_DIR) -> dict:
    d = os.path.join(outdir, tenant["key"])
    os.makedirs(d, exist_ok=True)
    meta = {"key": tenant["key"], "name": tenant["name"], "email": tenant["email"], "purpose": tenant["purpose"],
            "built_at_clock": now.isoformat(), "today": today.isoformat(), "nonces": {k: p.nonce for k, p in plans.items()}}
    fleets = {}
    for fk, p in plans.items():
        path = os.path.join(d, f"{fk}.truth.jsonl")
        body = "".join(json.dumps(r, sort_keys=True) + "\n" for r in p.truth).encode()
        open(path, "wb").write(body)
        e = dict(p.expect)
        e["truth_file"] = os.path.relpath(path, os.path.dirname(os.path.dirname(TRUTH_DIR)))
        e["truth_sha256"] = __import__("hashlib").sha256(body).hexdigest()
        fleets[fk] = e
    doc = {"meta": meta, "order": order_expect(tenant, today), "notices": tenant.get("notices"), "spend_ceiling_usd": tenant.get("spend_ceiling_usd"),
           "fleets": fleets}
    with open(os.path.join(d, "expect.json"), "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write("\n")
    return doc
