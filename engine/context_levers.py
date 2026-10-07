"""Context FAULT levers (argus#1505 test plan, steps 1 and 2).

The simulator sends a context manifest (SPEC 2: names, ages and fingerprints, never text) on every
model-run step of a pack, and these levers break that context in five ways that each have a KNOWN
ANSWER:

  ctx_stale_source        a retrieved fact is older than the fleet's freshness limit
  ctx_empty_retrieval     a step that always retrieves retrieved nothing (items [] and returned 0)
  ctx_unapproved_source   an item came from a source that is not on the fleet's approved list
  ctx_instruction_change  the step's instruction version and fingerprint change on a known date
  ctx_manifest_missing    one step sent no manifest (a coverage gap)

Two more levers are not faults. `context_manifest` turns manifests on with no fault (the control
fleets, which must be clean), and `background_failure` fails work items for OTHER causes so that a
failed item is not by itself evidence of a context fault.

HOW A FAULT SETTLES (the part the test depends on):
  * Every context fault is exclusive with the other phase-A levers, so a work item has at most one
    primary cause.
  * When a context fault fires, the work item's outcome fails with probability `fail_rate`
    (default 0.8, per lever in `params`). With probability 1 - fail_rate the fault fires and the
    work item still succeeds, which is the case that proves a check does not need a failure to speak.
  * On a work item with no fault, `background_failure` fails it with probability `rate` (default
    use 0.2) for a cause that is NOT context, drawn from the pack's existing non-context levers.

WHAT IS NEVER SENT. The fault record lives in `InjectedFault.params` and goes only to the ground-truth
ledger and the context truth file. It is not put in `result.metadata` (that is sent to Provy on
session close) and not in a trace payload. A test scans every payload the emitter built.

The manifest shape and hashing are engine.context's (`make_item`, `build_manifest`, `instruction_for`),
reused so there is one definition of a manifest in the simulator.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

# engine.context is imported inside the functions that need it: it pulls config, which imports levers.
from engine.types import InjectedFault, RunResult, TraceStep

# lever name -> the fault kind recorded in the ground truth
CONTEXT_LEVERS = {
    "ctx_stale_source": "stale",
    "ctx_empty_retrieval": "empty",
    "ctx_unapproved_source": "unapproved",
    "ctx_instruction_change": "instruction",
    "ctx_manifest_missing": "missing",
}
DEFAULT_FAIL_RATE = 0.8
MODEL_STEP_TYPES = ("agent_message", "decision")
# The non-context causes `background_failure` draws from. Each one fails the work item's outcome.
BACKGROUND_POOL = ("silent_policy", "policy_violation", "sla_breach", "silent_incomplete")


# ── what each pack's agents are given ─────────────────────────────────────────────────────────────
@dataclass
class ContextProfile:
    """The sources a pack's agents read, the fleet's approved list and its freshness limit.

    `agents` maps an agent name to (source, kind, (min_items, max_items)). Every agent keeps a minimum
    of at least one item, so an empty retrieval is a departure from the step's usual and not its habit.
    """
    agents: dict
    approved: list
    limit_days: int = 30
    unapproved: tuple = ("forum-scrape", "legacy-wiki", "shared-drive")


PROFILES: dict[str, ContextProfile] = {
    # Identity and access: a change request, an entitlement lookup, an apply, a review.
    "iam": ContextProfile(
        agents={
            "intake":   [("access-policy-kb", "document", (1, 2)), ("agent-memory", "memory", (0, 1))],
            "mapper":   [("role-catalog", "document", (1, 2)), ("directory-api", "tool_result", (1, 1)),
                         ("agent-memory", "memory", (0, 1))],
            "applier":  [("access-policy-kb", "document", (1, 2)), ("directory-api", "tool_result", (1, 1))],
            "reviewer": [("access-policy-kb", "document", (1, 1)), ("role-catalog", "document", (1, 1))],
        },
        approved=["access-policy-kb", "role-catalog", "directory-api", "agent-memory"],
    ),
    # Insurance claims: intake, validation, adjudication, review.
    "claims": ContextProfile(
        agents={
            "intake":      [("coverage-kb", "document", (1, 2)), ("claims-faq-core", "document", (1, 1))],
            "validator":   [("claims-api", "tool_result", (1, 1)), ("coverage-kb", "document", (1, 2)),
                            ("agent-memory", "memory", (0, 1))],
            "adjudicator": [("coverage-kb", "document", (1, 2)), ("claims-faq-limits", "document", (1, 1)),
                            ("agent-memory", "memory", (0, 1))],
            "reviewer":    [("coverage-kb", "document", (1, 1)), ("claims-faq-limits", "document", (1, 1))],
        },
        approved=["coverage-kb", "claims-faq-*", "claims-api", "agent-memory"],
    ),
}


def approved_match(source: str, approved: list) -> bool:
    """The product's rule (SPEC 6.1): exact, or by prefix when the entry ends in `*`."""
    return any(source == a or (a.endswith("*") and source.startswith(a[:-1])) for a in approved)


# ── small helpers ─────────────────────────────────────────────────────────────────────────────────
def _now(ctx) -> datetime:
    n = ctx.now
    return n if n.tzinfo else n.replace(tzinfo=timezone.utc)


def _fail_rate(s) -> float:
    return float(s.params.get("fail_rate", DEFAULT_FAIL_RATE))


def _model_steps(result: RunResult, profile: ContextProfile) -> list[TraceStep]:
    return [t for t in result.traces if t.step_type in MODEL_STEP_TYPES and t.agent in profile.agents]


def _pick_agent(result: RunResult, s, ctx, profile: ContextProfile, default: Optional[str] = None) -> Optional[str]:
    if s.target and s.target in profile.agents:
        return s.target
    agents = sorted({t.agent for t in _model_steps(result, profile)})
    if not agents:
        return None
    return default if default in agents else ctx.rng.choice(agents)


def _settle(result: RunResult, contract, m, s, ctx) -> bool:
    """Roll the fault's own failure. True when the work item now fails because of it.

    The Real side goes bad and the Estimated side stays good: the agent claimed success and was wrong,
    which is the shape a context fault takes in a real fleet (no error, a confident answer)."""
    from engine.levers import _corrupt_correctness
    if ctx.rng.random() < _fail_rate(s):
        return bool(_corrupt_correctness(result, contract, m))
    return False


def _profile_for(ctx) -> Optional[ContextProfile]:
    return PROFILES.get(ctx.workflow)


# ── the levers ────────────────────────────────────────────────────────────────────────────────────
def _fault(lever, agent, step_type, caused, **params) -> InjectedFault:
    kind = CONTEXT_LEVERS[lever]
    return InjectedFault(lever, agent, f"context_{kind}",
                         {"kind": kind, "step_type": step_type, "caused_failure": caused, **params})


def _step_type_of(result, agent) -> Optional[str]:
    for t in result.traces:
        if t.agent == agent and t.step_type in MODEL_STEP_TYPES:
            return t.step_type
    return None


def ctx_stale_source(result, gt, m, contract, s, ctx) -> Optional[InjectedFault]:
    prof = _profile_for(ctx)
    agent = _pick_agent(result, s, ctx, prof) if prof else None
    if agent is None:
        return None
    extra = int(s.params.get("extra_days", ctx.rng.randint(15, 90)))
    age_days = prof.limit_days + extra
    caused = _settle(result, contract, m, s, ctx)
    return _fault("ctx_stale_source", agent, _step_type_of(result, agent), caused,
                  age_days=age_days, limit_days=prof.limit_days)


def ctx_unapproved_source(result, gt, m, contract, s, ctx) -> Optional[InjectedFault]:
    prof = _profile_for(ctx)
    agent = _pick_agent(result, s, ctx, prof) if prof else None
    if agent is None:
        return None
    source = s.params.get("source") or ctx.rng.choice(list(prof.unapproved))
    caused = _settle(result, contract, m, s, ctx)
    return _fault("ctx_unapproved_source", agent, _step_type_of(result, agent), caused, source=source)


def ctx_empty_retrieval(result, gt, m, contract, s, ctx) -> Optional[InjectedFault]:
    prof = _profile_for(ctx)
    agent = _pick_agent(result, s, ctx, prof) if prof else None
    if agent is None:
        return None
    caused = _settle(result, contract, m, s, ctx)
    return _fault("ctx_empty_retrieval", agent, _step_type_of(result, agent), caused)


def ctx_instruction_change(result, gt, m, contract, s, ctx) -> Optional[InjectedFault]:
    """From each date in `change_dates` on, `rate` of the work items run the target agent under the
    next version (v2 after the first date, v3 after the second, ...). Each version is a fingerprint
    the step has never carried, so each date is one event a learned check can see.

    A single `change_date` is accepted as a list of one. No date means no change: a lever with no date
    is a no-op, never a guess. The first work item to carry each new version is the event, and the
    ground-truth writer marks it."""
    prof = _profile_for(ctx)
    dates = list(s.params.get("change_dates") or ([s.params["change_date"]] if s.params.get("change_date") else []))
    if not prof or not dates:
        return None
    today = _now(ctx).date()
    passed = sum(1 for d in dates if today >= date.fromisoformat(d[:10]))
    if passed == 0:
        return None
    agent = s.target if s.target in prof.agents else m.resolver_agent
    if agent not in prof.agents or _step_type_of(result, agent) is None:
        return None
    caused = _settle(result, contract, m, s, ctx)
    return _fault("ctx_instruction_change", agent, _step_type_of(result, agent), caused,
                  old_version="v1", new_version=f"v{1 + passed}", change_date=sorted(d[:10] for d in dates)[passed - 1])


def ctx_manifest_missing(result, gt, m, contract, s, ctx) -> Optional[InjectedFault]:
    prof = _profile_for(ctx)
    agent = _pick_agent(result, s, ctx, prof) if prof else None
    if agent is None:
        return None
    caused = _settle(result, contract, m, s, ctx)
    return _fault("ctx_manifest_missing", agent, _step_type_of(result, agent), caused)


def background_failure(result, gt, m, contract, s, ctx) -> Optional[InjectedFault]:
    """Fail the work item for a cause that is not context. Only reached when no fault claimed the item."""
    from engine import levers as L
    pool = list(BACKGROUND_POOL)
    ctx.rng.shuffle(pool)
    for name in pool:
        before = set(result.metadata)
        f = L._LEVER_FNS[name](result, gt, m, contract, L.LeverSetting(rate=1.0), ctx)
        # The other levers leave a marker in the session metadata, which is sent. A background cause
        # should look like any unexplained failure, not like a label, so the marker is removed.
        for k in set(result.metadata) - before:
            del result.metadata[k]
        L.finalize(result, contract)
        if f is not None and result.outcome_label == "fail":
            return InjectedFault("background_failure", f.agent, "background_other_cause",
                                 {"cause": f.lever, "caused_failure": True})
    return None


# ── the manifests ────────────────────────────────────────────────────────────────────────────────
def _enabled(config) -> bool:
    return bool(config.get("context_manifest")) or any(config.get(n) for n in CONTEXT_LEVERS)


def _clean_items(rng, prof: ContextProfile, agent: str, at: datetime, entity: str) -> list[dict]:
    from engine import context as C
    items = []
    for source, kind, (lo, hi) in prof.agents[agent]:
        for _ in range(rng.randint(lo, hi)):
            items.append(C.make_item(rng, source, at, kind=kind, entity=entity))
    return items


def _stale_item(rng, prof, agent, at, age_days, entity) -> dict:
    from engine import context as C
    docs = [(s, k) for s, k, _ in prof.agents[agent] if k == "document" and approved_match(s, prof.approved)]
    source = docs[0][0] if docs else prof.agents[agent][0][0]
    return C.make_item(rng, source, at, kind="document", age=timedelta(days=age_days, seconds=rng.randint(0, 3600)),
                       used=True, entity=entity)


def attach(result: RunResult, faults: list, config, ctx) -> None:
    """Build a manifest for every model-run step and apply the context faults that fired.

    Called once, at the end of levers.apply, so it sees the faults. It writes `TraceStep.context`
    only. Nothing here touches result.metadata or a payload."""
    from engine import context as C
    prof = _profile_for(ctx)
    if prof is None or not _enabled(config):
        return
    at = _now(ctx)
    rng = ctx.rng
    fired = {f.agent: [] for f in faults if f.lever in CONTEXT_LEVERS}
    for f in faults:
        if f.lever in CONTEXT_LEVERS:
            fired[f.agent].append(f)

    for step in _model_steps(result, prof):
        items = _clean_items(rng, prof, step.agent, at, result.entity_id)
        version = "v1"
        manifest_missing = False
        returned = None
        for f in fired.get(step.agent, []):
            p = f.params
            if f.lever == "ctx_stale_source":
                docs = [i for i, it in enumerate(items) if it["kind"] == "document"]
                new = _stale_item(rng, prof, step.agent, at, p["age_days"], result.entity_id)
                if docs:
                    items[docs[0]] = new
                else:
                    items.append(new)
                p["source"], p["item_id"], p["as_of"] = new["source"], new["id"], new["as_of"]
            elif f.lever == "ctx_unapproved_source":
                new = C.make_item(rng, p["source"], at, kind="document", used=True, entity=result.entity_id)
                items.append(new)
                p["item_id"] = new["id"]
            elif f.lever == "ctx_empty_retrieval":
                items, returned = [], 0
            elif f.lever == "ctx_instruction_change":
                version = p["new_version"]
            elif f.lever == "ctx_manifest_missing":
                manifest_missing = True
        p_total = (step.tokens_input or 300)
        if returned is None:
            returned = len(items)
        if manifest_missing:
            step.context = None
            continue
        step.context = C.build_manifest(
            items, returned, C.instruction_for(step.agent, version),
            {"total": p_total, "context": min(p_total, sum(i.get("tokens", 0) for i in items))})


# ── the ground truth, one line per work item, never sent to Provy ───────────────────────────────
def truth_record(fleet: str, role: str, pack: str, result: RunResult, occurred_at: str) -> dict:
    """What the simulator knows about one work item: every context fault, the cause of a failure, and
    which steps actually carried a manifest. Built from the run, after the fact."""
    ctx_faults = []
    background = None
    for f in result.faults:
        if f.lever in CONTEXT_LEVERS:
            ctx_faults.append({"fault": f.params["kind"], "lever": f.lever, "agent": f.agent,
                               "step_type": f.params.get("step_type"), **{k: v for k, v in f.params.items()
                                                                          if k not in ("kind", "step_type")}})
        elif f.lever == "background_failure":
            background = f.params["cause"]
    failed = result.outcome_label == "fail"
    if failed and any(x["caused_failure"] for x in ctx_faults):
        cause = "context:" + next(x["fault"] for x in ctx_faults if x["caused_failure"])
    elif failed and background:
        cause = "background:" + background
    elif failed:
        cause = "other"
    else:
        cause = None
    steps = [{"agent": t.agent, "step_type": t.step_type, "manifest_sent": t.context is not None,
              "instruction_version": (t.context or {}).get("instruction", {}).get("version"),
              "items": len((t.context or {}).get("items", []))}
             for t in result.traces if t.step_type in MODEL_STEP_TYPES]
    return {"fleet": fleet, "role": role, "pack": pack, "entity_id": result.entity_id,
            "session_id": result.session_id, "date": occurred_at[:10], "occurred_at": occurred_at,
            "faults": ctx_faults, "outcome": result.outcome_label, "failure_cause": cause, "steps": steps}


def mark_first_after_change(records: list[dict]) -> None:
    """Flag, per (fleet, agent, version), the first work item in time order that carried that instruction version.

    That is the event a learned check can see (SPEC 6.3); the later ones carry a hash that is already
    known after two sessions. Done after the run so the lever itself keeps no state."""
    seen = set()
    for r in sorted(records, key=lambda r: r["occurred_at"]):
        for f in r["faults"]:
            if f["fault"] == "instruction":
                key = (r["fleet"], f["agent"], f["new_version"])
                f["first_after_change"] = key not in seen
                seen.add(key)


def write_truth(path: str, records: list[dict]) -> str:
    """Write the file and return its SHA-256. The file stays on this machine."""
    import hashlib
    mark_first_after_change(records)
    body = "".join(json.dumps(r, sort_keys=True) + "\n" for r in records).encode()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(body)
    return hashlib.sha256(body).hexdigest()
