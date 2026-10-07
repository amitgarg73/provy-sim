"""The sim tenant set: scenarios, ground truth and what the product is expected to say (argus#1505 follow-on, 7 Oct 2026).

WHAT THIS IS. config/sim_tenants.json names twelve persistent pre-prod workspaces. This module turns one FLEET entry of it into a
planned run (built offline, deterministic from seed, nonce and clock), applies the scenario that makes the fleet what it is for
(healthy, thin, no context, planted faults, instruction stories, declared checks, claims, doors, roster) and writes down, BEFORE
anything is sent, what the product is expected to say. The expectations are an independent reading of the product's documented
rules (docs/readiness-contract.md, the learned-check rules in docs/operations/context-default-checks-runbook.md), computed from the
PLAN and never from the product's output, so a disagreement is a finding about one of the two.

⛔ GROUND TRUTH IS NEVER SENT. Nothing in `truth` or `expect` is put in a payload. A test scans for it.

⛔ A SIGNAL THAT IS NOT SENT IS NOT KNOWN, NEVER A PASS. The no-context and thin scenarios exist to hold that line: their expected
answers contain no pass, only unknown, learning or not sent.

Reuses, does not reinvent: the pack runner and levers (engine.runner, engine.levers, engine.context_levers), the manifest builders
(engine.context), the three customer-style doors (engine.door_emit) and the scorer's check names (scripts.score_context_levers).
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from engine import context as C
from engine.context_levers import PROFILES, approved_match
from engine.levers import LeverConfig
from engine.llm import LLM
from engine.runner import BatchRunner
from packs import get_pack

SCENARIOS = ("healthy", "thin", "none", "faults", "instruction", "declared", "outcomes", "doors", "roster")
WINDOW = 20                       # READINESS_WINDOW_STEPS in the product
WARMUP = 25                       # sessions with no planted fault, so learned checks have history
FAIL_RATE = 0.8
BACKGROUND = 0.2
DECISION_TYPES = ("decision", "agent_message")
# ag_counts_as_step in the database: the step types the meter counts (a skip and an error are not steps).
COUNTED_STEP_TYPES = ("agent_message", "agent_reasoning", "agent_step", "decision", "llm_call", "tool", "tool_call")
MODEL_STEP_TYPES = DECISION_TYPES

# What a way of sending cannot carry (web/lib/readiness-report.ts CANNOT_SEND). A limit of the door, not something the customer missed.
DOOR_LIMITS = {"log_map": ["item_fingerprint", "instruction_version", "instruction_fingerprint"]}

# ⛔ THE CLAIM IS ABOUT A SIGNAL THE PRODUCT CAN GRADE WITHOUT A HUMAN. A claim is graded only against a condition whose reading is confirmed or is
# identity shaped (same name on both sides, op eq, no remapped trace signal: web/lib/trace-rule.ts identityShaped). `decision_correct` is remapped by
# the claims pack to `adjudication_valid`, so a claim under either name was never graded and every ledger row fell to Provy's forecast (found 7 Oct 2026
# on the first tenant: 60 of 60 rows provy_forecast). `within_limit` (claims) and `entitlements_correct` (iam) are identity shaped.
# What each pack gives the scenarios: who states the claim, about which contract signal, which agent is code-only in the thin
# scenario, and which sources carry a date in the thin scenario.
PACK_ROLES = {
    "claims": {"claimant": "adjudicator", "claim_signal": "within_limit", "code_only": "validator",
               "dated_sources": ["coverage-kb"], "extra_signal": "customer_confirmed"},
    "iam":    {"claimant": "mapper", "claim_signal": "entitlements_correct", "code_only": "mapper",
               "dated_sources": ["access-policy-kb"], "extra_signal": "requester_confirmed"},
}

# Claim profiles: the confidences an agent states and the share of claims that hold at each.
CLAIM_PROFILES = {
    "calibrated": {"confidences": [0.9, 0.7, 0.5], "hold": lambda c: c},
    "overconfident": {"confidences": [0.9], "hold": lambda c: 0.5},
}

# The instruction stories of the `instruction` scenario, by agent. Session index is the plan's own order.
INSTRUCTION_STORIES = {
    "iam": {
        "intake": {"story": "change", "at": 30},
        "mapper": {"story": "alternate", "start": 14, "block": 6},
        "applier": {"story": "same_label", "at": 25},
        "reviewer": {"story": "constant"},
    },
}


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ── the plan ─────────────────────────────────────────────────────────────────────────────────────
def lever_config(scenario: str, spec: dict, warm: bool) -> LeverConfig:
    base: dict[str, Any] = {"context_manifest": 1.0, "background_failure": BACKGROUND}
    if scenario in ("faults", "declared") and not warm:
        rates = spec.get("fault_rates") or {}
        for lever, kind in (("ctx_stale_source", "stale"), ("ctx_unapproved_source", "unapproved"), ("ctx_empty_retrieval", "empty")):
            if rates.get(kind):
                base[lever] = {"rate": rates[kind], "params": {"fail_rate": FAIL_RATE}}
    return LeverConfig(base)


def build_runs(spec: dict, now: datetime, nonce: str) -> list:
    """The planned run of one fleet: one RunOutput per session, oldest first. Same arguments, same plan."""
    pack = get_pack(spec["pack"])
    count, days = int(spec["sessions"]), float(spec["days"])
    start = now - timedelta(days=days)
    every = timedelta(seconds=days * 86400 / count)
    warm, steady = lever_config(spec["scenario"], spec, True), lever_config(spec["scenario"], spec, False)
    runner = BatchRunner(pack, warm, emitter=None, ledger=None, llm=LLM(offline=True), seed=int(spec["seed"]), run_id=nonce,
                         starts_at=start, every=every)
    outs = []
    warmup = int(spec.get("warmup", WARMUP))
    for i in range(count):
        runner.levers = warm if i < warmup else steady
        outs.append(runner.run_one())
    ids = [o.result.session_id for o in outs]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        # A session id is derived from the work item id, which a pack draws at random: two draws can collide, and the product then stores ONE
        # session where the plan says two (found 7 Oct 2026 on H4: 50 planned, 49 stored). A plan with a collision is not sent; change the seed.
        raise ValueError(f"seed {spec['seed']} makes the same work item twice ({dup[:3]}); choose another seed")
    return outs


def _model_steps(result) -> list:
    return [t for t in result.traces if t.step_type in MODEL_STEP_TYPES]


# ── scenario transforms (each writes only TraceStep fields; nothing here reaches a payload as truth) ─────────────────
def apply_none(outs) -> None:
    """The customer who never sent a manifest: no step carries a record."""
    for o in outs:
        for t in o.result.traces:
            t.context = None


def apply_thin(outs, pack: str) -> None:
    """Thin context, the Third Eye shape: one agent runs code and no model (no record, no model, no tokens, no cost), the other
    model steps carry source, fingerprint, search count and instruction version, but a date on the dated sources only and no
    instruction fingerprint."""
    role = PACK_ROLES[pack]
    for o in outs:
        for t in o.result.traces:
            if t.step_type in DECISION_TYPES and t.agent == role["code_only"]:
                t.model, t.tokens_input, t.tokens_output, t.cost_usd, t.context = None, 0, 0, 0.0, None
                continue
            if t.context is None:
                continue
            for it in t.context.get("items", []):
                if it["source"] not in role["dated_sources"]:
                    it.pop("as_of", None)
            ins = t.context.get("instruction")
            if ins:
                ins.pop("hash", None)


def apply_claims(outs, pack: str, profile: str, seed: int) -> None:
    """One claim per work item, stated on the claimant agent's step, about the contract's own signal, with a stated confidence.

    The claim HOLDS when the label it implies equals the planned outcome. A calibrated agent holds at about the confidence it
    states; an overconfident one states 0.9 and holds about half the time. The shares are drawn from a seeded stream, so the plan
    is reproducible and the truth file lists every claim."""
    role = PACK_ROLES[pack]
    prof = CLAIM_PROFILES[profile]
    rng = random.Random(seed * 104729 + 7)
    for o in outs:
        r = o.result
        for t in r.traces:
            t.payload_extra.pop("provy_claim", None)
        step = next((t for t in r.traces if t.agent == role["claimant"] and t.step_type == "agent_message"), None)
        if step is None:
            continue
        conf = rng.choice(prof["confidences"])
        holds = rng.random() < prof["hold"](conf)
        actual = r.outcome_label
        implied = actual if holds else ("fail" if actual == "success" else "success")
        step.payload_extra["provy_claim"] = [{"signal": role["claim_signal"], "value": implied == "success", "confidence": conf,
                                              "entity_id": r.entity_id}]
        r.metadata_claims = {"confidence": conf, "implied": implied, "holds": holds}      # kept for the truth file only


def instruction_hash(agent: str, version: str) -> str:
    return C.hash_content(C.instruction_text(agent, version))


def _instruction_version_at(agent: str, idx: int, story: dict) -> tuple[str, str]:
    """(label, hash) the agent's steps carry in session `idx`."""
    kind = story["story"]
    if kind == "constant":
        return f"{agent}-v1", instruction_hash(agent, "v1")
    if kind == "change":
        v = "v2" if idx >= story["at"] else "v1"
        return f"{agent}-{v}", instruction_hash(agent, v)
    if kind == "alternate":
        block = story["block"]
        if idx < story["start"]:
            v = "v1"
        else:
            v = "v2" if ((idx - story["start"]) // block) % 2 == 0 else "v1"
        return f"{agent}-{v}", instruction_hash(agent, v)
    if kind == "same_label":
        v = "v1-edited" if idx >= story["at"] else "v1"
        return f"{agent}-v1", instruction_hash(agent, v)
    raise ValueError(kind)


def apply_instruction_stories(outs, pack: str) -> dict:
    stories = INSTRUCTION_STORIES[pack]
    for idx, o in enumerate(outs):
        for t in _model_steps(o.result):
            story = stories.get(t.agent)
            if story is None or t.context is None:
                continue
            label, h = _instruction_version_at(t.agent, idx, story)
            t.context["instruction"] = {"version": label, "hash": h}
    return stories


# ── the truth file ───────────────────────────────────────────────────────────────────────────────
def step_time(o, i: int) -> datetime:
    """When a step runs: the session's time plus one second per step (engine.door_emit.plan_steps)."""
    t0 = datetime.fromisoformat(o.record["ts"].replace("Z", "+00:00"))
    return t0 + timedelta(seconds=i)


def truth_rows(fleet_key: str, spec: dict, outs) -> list[dict]:
    from engine.context_levers import truth_record, write_truth  # noqa: F401
    rows = []
    for idx, o in enumerate(outs):
        r = truth_record(fleet_key, "sim", spec["pack"], o.result, o.record["ts"])
        r["index"] = idx
        r["door"] = spec["door"]
        r["steps"] = [{"i": i, "agent": t.agent, "step_type": t.step_type, "at": iso(step_time(o, i)),
                       "ran": bool(t.model or t.tokens_input or t.tokens_output or t.cost_usd),
                       "manifest_sent": t.context is not None,
                       "instruction": (t.context or {}).get("instruction"),
                       "items": [{"source": it.get("source"), "id": it.get("id"), "as_of": it.get("as_of"), "kind": it.get("kind"),
                                  "hash": it.get("hash")} for it in (t.context or {}).get("items", [])],
                       "returned": ((t.context or {}).get("retrieval") or {}).get("returned"),
                       "claim": (t.payload_extra.get("provy_claim") or [None])[0]}
                      for i, t in enumerate(o.result.traces)]
        mc = getattr(o.result, "metadata_claims", None)
        if mc:
            r["claim"] = mc
        r["counted_steps"] = sum(1 for t in o.result.traces if t.step_type.lower().strip() in COUNTED_STEP_TYPES)
        rows.append(r)
    return rows


# ── expectations: an independent reading of the product's rules ─────────────────────────────────
SIGNAL_IDS = ("source", "item_fingerprint", "instruction_version", "source_age", "search_result", "instruction_fingerprint")
_HASH_OK = lambda v: isinstance(v, str) and len(v) == 71 and v.startswith("sha256:") and all(c in "0123456789abcdef" for c in v[7:])  # noqa: E731


def signals_of_manifest(m: Optional[dict]) -> dict:
    """The six signals one step sends, exactly as docs/readiness-contract.md section 1 reads them."""
    items = m.get("items") if m and isinstance(m.get("items"), list) else None
    ins = m.get("instruction") if m and isinstance(m.get("instruction"), dict) else None
    ret = (m or {}).get("retrieval")
    returned = ret.get("returned") if isinstance(ret, dict) else None

    def every(ok):
        return bool(items) and all(isinstance(i, dict) and ok(i) for i in items)

    def parsable(v):
        if not isinstance(v, str):
            return False
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
            return True
        except ValueError:
            return False

    ne = lambda v: isinstance(v, str) and v.strip() != ""     # noqa: E731
    return {
        "source": every(lambda i: ne(i.get("source"))),
        "item_fingerprint": every(lambda i: _HASH_OK(i.get("hash"))),
        "instruction_version": bool(ins) and ne(ins.get("version")),
        "source_age": every(lambda i: parsable(i.get("as_of"))),
        "search_result": isinstance(returned, int) and not isinstance(returned, bool) and returned >= 0,
        "instruction_fingerprint": bool(ins) and _HASH_OK(ins.get("hash")),
    }


def project_for_door(manifest: Optional[dict], door: str) -> Optional[dict]:
    """What the product stores of a manifest on a door. Only the field map cuts anything: it has no field for a fingerprint or
    an instruction (readiness-report.ts CANNOT_SEND), and carries documents only."""
    if manifest is None or door != "log_map":
        return manifest
    items = [{k: it[k] for k in ("kind", "source", "id", "as_of", "used") if k in it} for it in manifest.get("items", []) if it.get("kind") == "document"]
    return {"items": items, "retrieval": {"returned": len(items)}}


def expected_readiness(outs, door: str) -> dict:
    """The readiness report the product should give for a fleet whose planned sessions have all arrived.

    Window: the latest 20 decision steps (decision or agent_message), newest first, over the whole fleet. A step with no record, no
    model, no tokens and no cost is left out when at least one other step of the window ran a model (learned). The six signals and
    "sent a record" are judged over the steps that remain. State: too_early under 20 steps or with nothing to judge, ready when all
    six are sent on every step, otherwise thin. Never a pass for a signal that is not sent."""
    steps = []
    for si, o in enumerate(outs):
        for i, t in enumerate(o.result.traces):
            if t.step_type in DECISION_TYPES:
                steps.append((step_time(o, i), si, i, t))
    steps.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    window = steps[:WINDOW]
    total = len(window)
    ran = [bool(t.model or t.tokens_input or t.tokens_output or t.cost_usd) for *_, t in window]
    manifests = [project_for_door(t.context, door) for *_, t in window]
    any_ran = any(ran)
    left_out = [(m is None) and any_ran and not r for m, r in zip(manifests, ran)]
    given = [m for m, lo in zip(manifests, left_out) if not lo]
    n = len(given)
    counts = {k: 0 for k in SIGNAL_IDS}
    for m in given:
        s = signals_of_manifest(m)
        for k in SIGNAL_IDS:
            counts[k] += 1 if s[k] else 0
    some_dated = sum(1 for m in given if m and any(isinstance(i.get("as_of"), str) for i in m.get("items", [])))
    states = {}
    for k in SIGNAL_IDS:
        c = counts[k]
        extra = (some_dated - c) if k == "source_age" else 0
        states[k] = "not_known" if n == 0 else ("sent" if c == n else ("not_sent" if c == 0 and extra <= 0 else "partly_sent"))
    sent = sum(1 for v in states.values() if v == "sent")
    early = total < WINDOW or n == 0
    state = "too_early" if early else ("ready" if sent == 6 else "thin")
    return {"state": state, "stepsCounted": total, "givenSteps": n, "notGivenSteps": sum(left_out),
            "manifestSteps": sum(1 for m in given if m is not None), "signals": states, "signalsSent": sent}


def expected_declared_findings(outs, limit_hours: Optional[float], approved: Optional[list], agents: Optional[list] = None) -> dict:
    """What the two declared rules should say, per session: items older than the limit when the step ran (kinds document and
    memory, a date in the future reads unknown) and items from a source off the approved list (every kind). A rule cannot judge a
    step with no record: that is unknown, so it appears in neither list."""
    out = {}
    for idx, o in enumerate(outs):
        stale, bad = [], []
        for i, t in enumerate(o.result.traces):
            if t.step_type not in DECISION_TYPES or t.context is None:
                continue
            if agents and t.agent not in agents:
                continue
            at = step_time(o, i)
            for it in t.context.get("items", []):
                if limit_hours is not None and it.get("kind") in ("document", "memory") and it.get("as_of"):
                    age = (at - datetime.fromisoformat(it["as_of"].replace("Z", "+00:00"))).total_seconds() / 3600
                    if age > limit_hours:
                        stale.append({"agent": t.agent, "source": it["source"], "id": it["id"], "age_hours": round(age, 1)})
                if approved is not None and it.get("source") is not None and not approved_match(it["source"], approved):
                    bad.append({"agent": t.agent, "source": it["source"], "id": it["id"]})
        if stale or bad:
            out[o.result.session_id] = {"index": idx, "stale": stale, "unapproved": bad}
    return out


def expected_instruction_findings(outs, pack: str) -> list[dict]:
    """Where the learned instructions_changed check should FAIL, from the product's rule (lib/context-learned.ts judgeInstructions):
    a change is any difference from the step's previous fingerprint; a return is told as 'went back'; the third switch between the same
    two fingerprints is one 'back and forth' finding; the fourth and later are quiet. A first instruction is not a change."""
    stories = INSTRUCTION_STORIES[pack]
    findings = []
    for agent in stories:
        prev = None
        pair_counts: dict = {}
        seen: set = set()
        for idx, o in enumerate(outs):
            step = next((t for t in _model_steps(o.result) if t.agent == agent and t.context and t.context.get("instruction")), None)
            if step is None:
                continue
            h = step.context["instruction"]["hash"]
            if prev is not None and h != prev:
                pair = frozenset((prev, h))
                before = pair_counts.get(pair, 0)
                returned = h in seen
                quiet = returned and before >= 3
                if not quiet:
                    findings.append({"agent": agent, "session_index": idx, "session_id": o.result.session_id,
                                     "kind": "back_and_forth" if (returned and before == 2) else ("went_back" if returned else "changed"),
                                     "from": prev, "to": h})
                pair_counts[pair] = before + 1
            seen.add(h)
            prev = h
    return findings


def wilson_lower(k: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    p = k / n
    d = 1 + z * z / n
    return (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d


def expected_calibration(truth: list[dict]) -> dict:
    """Settled agent claims by stated confidence: how many, how many held. A bucket under 10 claims reads 'too few to judge'."""
    by: dict = {}
    for r in truth:
        c = r.get("claim")
        if not c:
            continue
        b = by.setdefault(round(c["confidence"], 1), {"claims": 0, "held": 0})
        b["claims"] += 1
        b["held"] += 1 if c["holds"] else 0
    return {str(k): {**v, "rate": round(v["held"] / v["claims"], 3), "too_few": v["claims"] < 10} for k, v in sorted(by.items())}


def expected_outcome_counts(truth: list[dict]) -> dict:
    return {"work_items": len(truth), "success": sum(1 for r in truth if r["outcome"] == "success"), "fail": sum(1 for r in truth if r["outcome"] == "fail")}


def expected_counted_steps(truth: list[dict]) -> int:
    return sum(r["counted_steps"] for r in truth)


def digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
