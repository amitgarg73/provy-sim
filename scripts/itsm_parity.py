#!/usr/bin/env python3
"""Validate the ITSM pack end to end on a disposable pre-prod workspace (argus#1649). Pre-prod and the ServiceNow PDI only.

    # under with-secrets SERVICENOW_INSTANCE SERVICENOW_USER SERVICENOW_PASSWORD (+ SUPABASE_URL SUPABASE_KEY WAITLIST_ADMIN_KEY_PREVIEW for configure)
    PROVY_ITSM_MARKER=provy-itsm-parity python scripts/itsm_parity.py run       --creds FILE --ids FILE [--count 24]
    PROVY_ITSM_MARKER=provy-itsm-parity python scripts/itsm_parity.py close     --creds FILE --ids FILE
    PROVY_ITSM_MARKER=provy-itsm-parity python scripts/itsm_parity.py push      --creds FILE
    python scripts/itsm_parity.py configure --creds FILE [--step declarations,orders,notices,roster]
    python scripts/itsm_parity.py expect    --creds FILE                 # writes ground_truth/sim_tenants/I1/ from the run, no network

What it does, and the two things it deliberately does NOT do:

  * run     creates at most 30 incidents in the PDI on this fleet's OWN desk marker (config/sim_tenants_itsm.json) with a tag in the answer key,
            lets the ITSM pack work them (real writes to those tickets), and sends each session through the REST door to the throwaway fleet.
  * close   moves the throwaway tickets from Resolved to Closed. The instance's sweep does this for the default desk; it does not see this marker.
  * push    reads the closed throwaway tickets, runs servicenow/outcome_push.js (the rule file itself) over them to get the outcome bodies, and posts
            those to the throwaway fleet. The rule's logic is the rule's; only the transport is ours.

⛔ IT NEVER USES THE DEFAULT MARKER, AND IT NEVER TOUCHES THE INSTANCE'S ONE INGEST KEY. The instance's sweep, SLA targets and outcome push are keyed to
`provy-itsm` and push with a single key that belongs to ITSM Demo's fleet. A ticket on that marker would be closed by the sweep and its outcome would land
in the protected tenant as a held outcome. Swapping that key to test here would also be unrecoverable (it cannot be read back). So this desk has its own
marker, sees none of that machinery, and settles through the rule's code run outside the instance. Consequences, stated rather than hidden: these tickets
carry no SLA targets, so the response and resolution signals are never pushed and never reopen (no sweep), and the claims about them are not graded.
⛔ NO CREDENTIAL IS PRINTED. The ingest key is read from the 0600 credentials file and goes only to the host it belongs to.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC_PATH = os.path.join(os.path.dirname(HERE), "config", "sim_tenants_itsm.json")
TRUTH_DIR = os.path.join(os.path.dirname(HERE), "ground_truth", "sim_tenants")
BASE = os.environ.get("PROVY_URL", "https://dev.provy.ai")
MAX_INCIDENTS = 30                      # the most this tool will ever create in the instance
TENANT_KEY, FLEET_KEY = "I1", "itsm"


def load_spec(path: str = SPEC_PATH) -> dict:
    return json.load(open(path))


def fleet_of(spec: dict) -> tuple[dict, dict]:
    t = next(t for t in spec["tenants"] if t["key"] == TENANT_KEY)
    return t, next(f for f in t["fleets"] if f["key"] == FLEET_KEY)


def require_own_marker(spec: dict, environ=None) -> str:
    """The desk marker for this run: the fleet's own, and only when the environment already says so (engine.servicenow reads it at import)."""
    environ = os.environ if environ is None else environ
    _, f = fleet_of(spec)
    want = f["marker"]
    if want == "provy-itsm" or not re.fullmatch(r"provy-itsm-[a-z0-9][a-z0-9-]{0,30}", want):
        raise SystemExit(f"the spec's marker {want!r} is the default desk or malformed; refusing")
    if environ.get("PROVY_ITSM_MARKER") != want:
        raise SystemExit(f"set PROVY_ITSM_MARKER={want} before running (engine.servicenow reads it at import, and the default marker belongs to ITSM Demo's machinery)")
    return want


# ── times ────────────────────────────────────────────────────────────────────────────────────────
def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_sn(v: str) -> datetime:
    return datetime.strptime(v[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def session_start(prev_end: datetime | None, opened_at: str, now: datetime, span_s: float) -> datetime:
    """When this ticket's session begins: after the previous session ends (no two sessions' steps interleave, so the readiness window of the newest
    steps is unambiguous), on the same UTC day the ticket was opened (the outcome is matched on that business date), and early enough that its last
    step is not in the future (the product refuses a future time)."""
    opened = parse_sn(opened_at)
    midnight = opened.replace(hour=0, minute=0, second=10, microsecond=0)
    preferred = now - timedelta(minutes=6)
    if preferred.date() != opened.date():
        preferred = midnight                    # the run crossed midnight: stay on the ticket's day rather than on the clock's
    start = max(prev_end or datetime.min.replace(tzinfo=timezone.utc), preferred)
    if start + timedelta(seconds=span_s) > now:
        raise SystemExit(f"cannot place a session of {span_s:.0f} s before now ({iso(now)}); wait a few minutes and run again")
    if start.date() != opened.date():
        raise SystemExit(f"session day {start.date()} is not the ticket's day {opened.date()}; the outcome would not match. Run again after midnight settles")
    return start


class Out:
    """The shape engine.sim_scenarios expects of a planned run: .result and .record['ts']."""
    def __init__(self, result, ts: str):
        self.result, self.record = result, {"ts": ts}


# ── run ──────────────────────────────────────────────────────────────────────────────────────────
def cmd_run(a) -> int:
    spec = load_spec()
    marker = require_own_marker(spec)
    t, f = fleet_of(spec)
    count = a.count or int(f["sessions"])
    if not 1 <= count <= MAX_INCIDENTS:
        raise SystemExit(f"--count must be 1 to {MAX_INCIDENTS}")

    from engine import door_emit as D
    from engine import sim_set as X
    from engine.levers import LeverConfig
    from engine.llm import LLM
    from engine.servicenow import MARKER, client_from_env
    from engine.types import RunContext
    from packs.itsm.pack import ItsmPack
    from scripts.seed_itsm_incidents import build_incident

    assert MARKER == marker, "engine.servicenow did not pick up the marker"
    creds = X.Creds(a.creds)
    key = creds.fleet_key(TENANT_KEY, FLEET_KEY)
    D.assert_preprod(BASE, key)
    sn = client_from_env()
    sn.require()
    existing = sn.query("incident", f"correlation_id={MARKER}", ["number", "state"], limit=100)
    if existing and not a.again:
        raise SystemExit(f"{len(existing)} incident(s) already carry {MARKER}; this tool creates a batch once. Close them with `close`, or pass --again for a second batch (the total stays under {MAX_INCIDENTS})")
    if len(existing) + count > MAX_INCIDENTS:
        raise SystemExit(f"{len(existing)} exist and {count} more would pass the cap of {MAX_INCIDENTS}")

    rng = random.Random(int(f["seed"]))
    callers = sn.query("sys_user", "active=true^emailISNOTEMPTY", ["sys_id", "name"], limit=25)
    ids = []
    for i in range(count):
        row = sn.create("incident", build_incident(rng, callers, "benchmark", f["tag"]))
        ids.append(row["number"])
        with open(a.ids, "a") as fh:
            fh.write(row["number"] + "\n")
        time.sleep(0.25)
    print(f"created {len(ids)} incidents on {MARKER}: {ids[0]} .. {ids[-1]} (every number is in {a.ids})", flush=True)

    pack = ItsmPack(client=sn)
    llm = LLM(offline=True)
    replies = D.Replies(os.path.join(os.path.dirname(a.ids) or ".", "itsm.replies.jsonl"))
    door = D.DOORS["rest"](BASE, key, replies)
    outs, truth, prev_end = [], [], None
    for i in range(count):
        item, gt = pack.generate_work_item(rng)
        now = datetime.now(timezone.utc)
        probe_ctx = RunContext(llm=llm, rng=random.Random(int(f["seed"]) * 31 + i), levers=LeverConfig({}), session_index=i, workflow="itsm", now=now, offline=True)
        # The session is placed after the work, so its context ages are read against the time it is dated at (never after "now").
        start = session_start(prev_end, item["opened_at"], now, 14.0)
        probe_ctx.now = start
        result = pack.run_pipeline(item, gt, probe_ctx)
        ts = iso(start)
        door.send_session(result, ts, pack.agents())
        prev_end = start + timedelta(seconds=len(result.traces) + 1)
        outs.append(Out(result, ts))
        rec = pack.truth_record(result, ts)
        rec["index"] = i
        truth.append(rec)
        print(f"  {i + 1}/{count} {item['id']} {len(result.traces)} steps", flush=True)
    os.makedirs(os.path.join(TRUTH_DIR, TENANT_KEY), exist_ok=True)
    path = os.path.join(TRUTH_DIR, TENANT_KEY, "itsm.truth.jsonl")
    with open(path, "w") as fh:
        for r in truth:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    state = {"ids": ids, "sessions": [o.result.session_id for o in outs], "ts": [o.record["ts"] for o in outs], "marker": marker, "tag": f["tag"],
             # every step of every session, as the readiness and counting expectations read it. Kept outside the repo.
             "steps": [[{"agent": tr.agent, "step_type": tr.step_type, "model_run": tr.model is not None, "context": tr.context} for tr in o.result.traces] for o in outs]}
    json.dump(state, open(os.path.join(os.path.dirname(a.ids) or ".", "itsm.run.json"), "w"), indent=1)
    print(f"worked {len(outs)} tickets; truth file {path}")
    return 0


# ── close ────────────────────────────────────────────────────────────────────────────────────────
def cmd_close(a) -> int:
    spec = load_spec()
    marker = require_own_marker(spec)
    _, f = fleet_of(spec)
    from engine.servicenow import MARKER, STATE_CLOSED, STATE_RESOLVED, client_from_env
    assert MARKER == marker
    sn = client_from_env()
    sn.require()
    ours = {x.strip() for x in open(a.ids) if x.strip()}
    rows = sn.query("incident", f"correlation_id={MARKER}^correlation_displayLIKEtag={f['tag']}^state={STATE_RESOLVED}", ["number", "sys_id"], limit=100)
    n = 0
    for r in rows:
        if r["number"] not in ours:
            print(f"  {r['number']} carries the marker and tag but is not in {a.ids}; left alone")
            continue
        sn.update("incident", r["sys_id"], {"state": STATE_CLOSED})
        n += 1
        time.sleep(0.2)
    print(f"closed {n} of our {len(ours)} tickets")
    return 0


# ── push ─────────────────────────────────────────────────────────────────────────────────────────
def closed_records(sn, marker: str, tag: str, ours: set) -> list[dict]:
    fields = ["number", "sys_id", "close_code", "reopen_count", "reassignment_count", "time_worked", "priority", "category", "opened_at", "closed_at",
              "correlation_display", "assignment_group", "state"]
    rows = sn.query("incident", f"correlation_id={marker}^correlation_displayLIKEtag={tag}^state=7", fields, limit=100)
    out = []
    for r in rows:
        if r["number"] not in ours:
            continue
        r = dict(r)
        r["assignment_group_name"] = sn.group_name(r["assignment_group"]) if r.get("assignment_group") else ""
        out.append(r)
    return out


def payloads_from_rule(records: list[dict]) -> list[dict]:
    p = subprocess.run(["node", os.path.join(HERE, "js", "outcome_push_payloads.js")], input=json.dumps(records), capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise SystemExit("the rule could not be run: " + p.stderr[-400:])
    return json.loads(p.stdout)


def cmd_push(a) -> int:
    spec = load_spec()
    marker = require_own_marker(spec)
    _, f = fleet_of(spec)
    import requests
    from engine import door_emit as D
    from engine import sim_set as X
    from engine.servicenow import MARKER, client_from_env
    assert MARKER == marker
    creds = X.Creds(a.creds)
    key = creds.fleet_key(TENANT_KEY, FLEET_KEY)
    D.assert_preprod(BASE, key)
    sn = client_from_env()
    sn.require()
    ours = {x.strip() for x in open(a.ids) if x.strip()}
    recs = closed_records(sn, MARKER, f["tag"], ours)
    out = payloads_from_rule(recs)
    ok, results = 0, []
    for o in out:
        r = requests.post(BASE + "/api/ingest/outcome", headers={"x-provy-key": key, "Content-Type": "application/json"}, data=json.dumps(o["payload"]), timeout=120)
        body = {}
        try:
            body = r.json()
        except Exception:                                    # noqa: BLE001
            pass
        results.append({"number": o["number"], "status": r.status_code, "reconciliation": body.get("reconciliation"), "label": o["payload"]["label"],
                        "signals": {k: o["payload"]["signals"].get(k) for k in ("resolution_genuine", "resolution_persists", "self_resolved", "reopen_count", "close_code", "reassignment_count")}})
        ok += 1 if r.status_code == 200 else 0
        print(f"  {o['number']} -> HTTP {r.status_code} {body.get('reconciliation')}", flush=True)
    json.dump(results, open(os.path.join(os.path.dirname(a.ids) or ".", "itsm.push.json"), "w"), indent=1)
    print(f"pushed {ok} of {len(out)} outcomes (closed tickets read: {len(recs)})")
    return 0 if ok == len(out) else 1


# ── configure ────────────────────────────────────────────────────────────────────────────────────
def cmd_configure(a) -> int:
    from datetime import date as _d
    import requests
    from engine import sim_set as X
    from packs.itsm.pack import ItsmPack
    spec = load_spec()
    t, f = fleet_of(spec)
    creds = X.Creds(a.creds)
    steps = a.step.split(",") if a.step else ["declarations", "orders", "notices", "roster"]
    pg = X.Pg()
    tid, wf, key = creds.tenant_id(TENANT_KEY), creds.workflow_id(TENANT_KEY, FLEET_KEY), creds.fleet_key(TENANT_KEY, FLEET_KEY)
    today = _d.today()
    if "declarations" in steps:
        d = f["declare"]
        body = {"context_max_age": {"hours": d["limit_days"] * 24}, "approved_sources": {"sources": d["approved"]}}
        H = {"x-provy-key": key, "Content-Type": "application/json"}
        r = requests.put(BASE + "/api/compute/context-declarations", headers=H, data=json.dumps(body), timeout=120)
        g = requests.get(BASE + "/api/compute/context-declarations", headers=H, timeout=120)
        print("declarations: put", r.status_code, "get", g.status_code)
    if "orders" in steps:
        adm = X.Admin(base=os.environ.get("SIM_ADMIN_BASE", BASE))
        have = pg.select("ag_workspace_orders", {"select": "id", "tenant_id": f"eq.{tid}", "created_by": "neq.migration 1340"})
        if have:
            print(f"order: {len(have)} already on file, left alone")
        else:
            for route, body in X.order_bodies(t["order"], today):
                st, js = adm.post(route, {"workspaceId": tid, **body})
                print(f"order: POST {route} -> {st} {'' if st < 300 else json.dumps(js)[:200]}")
                if st >= 300:
                    return 1
    if "notices" in steps:
        n = t["notices"]
        pg.insert("ag_usage_settings", {"tenant_id": tid, "notify_50": n.get("notify50", True), "notify_75": n.get("notify75", True),
                                        "notify_people": [p.lower() for p in n["people"]], "updated_by": "sim set"}, upsert_on="tenant_id")
        print(f"notices: {len(n['people'])} recipient(s) written (pre-prod notices are in shadow: nothing is sent)")
    if "roster" in steps:
        now = datetime.now(timezone.utc).isoformat()
        pack = ItsmPack(client=object())
        names = [x.name for x in pack.agents()]
        pg.rpc("ag_record_agent_events", {"p_events": [{"tenant_id": tid, "workflow_id": wf, "agent_name": n, "event": "accepted", "path": "onboarding", "actor": "sim set"} for n in names]})
        print(f"roster: {len(names)} agents accepted")
        for ag in pack.retired_roster():
            if ag.name not in f.get("retire_extra", []):
                continue
            if not pg.select("ag_pipeline_agents", {"select": "id", "workflow_id": f"eq.{wf}", "agent_name": f"eq.{ag.name}"}):
                pg.insert("ag_pipeline_agents", {"tenant_id": tid, "workflow_id": wf, "agent_name": ag.name, "display_name": ag.display_name, "emoji": ag.emoji,
                                                 "sort_order": ag.sort_order, "acknowledged": True, "retired_at": now, "retired_reason": "Sim set: retired on purpose (replaced by triage)"})
            pg.rpc("ag_record_agent_events", {"p_events": [{"tenant_id": tid, "workflow_id": wf, "agent_name": ag.name, "event": "retired", "path": "retire", "actor": "sim set"}]})
            print(f"roster: {ag.name} added and retired")
    return 0


# ── expect: the ground truth, from the run ───────────────────────────────────────────────────────
def build_expect(spec: dict, truth: list[dict], outs: list, pushed: list[dict], built_at: str, today: str) -> dict:
    """The expectation for the throwaway tenant, written from what the pack knows it sent and what the rule pushed, never from the product."""
    from engine import sim_scenarios as S
    t, f = fleet_of(spec)
    steps_total = sum(r["steps_total"] for r in truth)
    claim_steps = sum(len({c["agent"] for c in r["claims"]}) for r in truth)
    fe = {
        "door": "rest", "scenario": "live", "pack": "itsm", "sessions": len(truth), "steps": steps_total, "stored_steps": steps_total,
        "counted_steps": sum(1 for o in outs for tr in o.result.traces if tr.step_type.lower().strip() in S.COUNTED_STEP_TYPES),
        "outcomes": {"work_items": sum(1 for p in pushed if p["status"] == 200), "success": sum(1 for p in pushed if p["label"] == "success"),
                     "fail": sum(1 for p in pushed if p["label"] == "fail")},
        "readiness": S.expected_readiness(outs, "rest"),
        "session_ids": [r["session_id"] for r in truth],
        "declares": ["context_max_age"],
        "roster": {"agents": sorted({s["agent"] for r in truth for s in r["steps"]} | set(f.get("retire_extra", []))), "retire": list(f.get("retire_extra", [])), "never_accepted": []},
        "truth_file": os.path.join("ground_truth", "sim_tenants", TENANT_KEY, "itsm.truth.jsonl"),
        "itsm": {
            "marker": f["marker"], "tag": f["tag"],
            "code_only_agents": sorted({a for r in truth for a in r["code_only_agents"]}),
            "model_steps": sum(1 for r in truth for s in r["steps"] if s["model_run"]),
            "code_only_steps": sum(1 for r in truth for s in r["steps"] if not s["model_run"]),
            "model_steps_with_record": sum(1 for r in truth for s in r["steps"] if s["model_run"] and s["manifest_sent"]),
            "claim_steps": claim_steps,
            "claims": sum(len(r["claims"]) for r in truth),
            "claim_confidences": sorted({c["confidence"] for r in truth for c in r["claims"]}),
            "pushed_by_the_rule": len(pushed),
            "never_posted_by_the_simulation": True,
        },
    }
    order = t["order"]
    return {
        "meta": {"built_at_clock": built_at, "today": today, "spec": os.path.basename(SPEC_PATH)},
        "order": {"plan": X_plan_words(order), "discount_percent": order.get("discount_percent"),
                  "order_rows": [{"starts_on": order["starts_on"], "pilot_ends_on": None, "paid": False, "has_pricing": True}]},
        "notices": {"people": t["notices"]["people"]},
        "fleets": {FLEET_KEY: fe},
    }


def X_plan_words(order: dict) -> dict:
    from engine import sim_set as X
    return X.fleet_plan_words(order)


def outs_from_run(run: dict, truth: list[dict]) -> list:
    from engine.types import RunResult, TraceStep
    outs = []
    for r, ts, steps in zip(truth, run["ts"], run["steps"]):
        res = RunResult(entity_id=r["entity_id"], session_type="incident", session_id=r["session_id"])
        for s in steps:
            res.traces.append(TraceStep(agent=s["agent"], step_type=s["step_type"], model="m" if s["model_run"] else None,
                                        tokens_input=1 if s["model_run"] else 0, context=s["context"]))
        outs.append(Out(res, ts))
    return outs


def cmd_expect(a) -> int:
    spec = load_spec()
    require_own_marker(spec)
    d = os.path.dirname(a.ids) or "."
    truth = [json.loads(l) for l in open(os.path.join(TRUTH_DIR, TENANT_KEY, "itsm.truth.jsonl")) if l.strip()]
    run = json.load(open(os.path.join(d, "itsm.run.json")))
    pushed = json.load(open(os.path.join(d, "itsm.push.json")))
    # rebuild the planned-run shape the readiness and counting expectations read, from the steps the run recorded
    outs = outs_from_run(run, truth)
    exp = build_expect(spec, truth, outs, pushed, datetime.now(timezone.utc).isoformat(), date.today().isoformat())
    path = os.path.join(TRUTH_DIR, TENANT_KEY, "expect.json")
    exp["fleets"][FLEET_KEY]["truth_sha256"] = hashlib.sha256(open(os.path.join(TRUTH_DIR, TENANT_KEY, "itsm.truth.jsonl"), "rb").read()).hexdigest()
    json.dump(exp, open(path, "w"), indent=1, sort_keys=True)
    print("wrote", path)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("run", cmd_run), ("close", cmd_close), ("push", cmd_push), ("configure", cmd_configure), ("expect", cmd_expect)):
        p = sub.add_parser(name)
        p.add_argument("--creds", required=(name != "close"))
        p.add_argument("--ids", default="")
        if name == "run":
            p.add_argument("--count", type=int, default=0)
            p.add_argument("--again", action="store_true")
        if name == "configure":
            p.add_argument("--step")
        p.set_defaults(fn=fn)
    a = ap.parse_args()
    if a.cmd in ("run", "close", "push", "expect") and not a.ids:
        raise SystemExit("--ids FILE is required (one incident number per line, kept outside the repo)")
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
