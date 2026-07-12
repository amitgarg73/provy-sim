"""The failure levers (the chaos config) and their application.

Per-agent, per-dimension, with KNOWN injection rates and a seeded RNG for
reproducibility. Each lever mutates a CLEAN baseline RunResult and returns the
injected-truth record of exactly what it broke. Silent levers are first-class:
they are the differentiator.

Design: lever logic here is domain-free. It reads the pack's LeverManifest
(which agents/signals to aim at) and the pack's contract (to derive the bad
value for a signal). So the same nine levers work for Support, Claims, and CRM
without change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from . import contract as C
from .types import (Criterion, InjectedFault, LeverManifest, RunContext,
                    RunResult, TraceStep)


# ── Config ──────────────────────────────────────────────────────────────────

@dataclass
class LeverSetting:
    rate: float = 0.0
    target: Optional[str] = None
    params: dict = field(default_factory=dict)


class LeverConfig:
    """Maps lever name -> LeverSetting. Accepts floats or dicts for convenience."""

    def __init__(self, settings: Optional[dict] = None):
        self.settings: dict[str, LeverSetting] = {}
        for name, val in (settings or {}).items():
            self.settings[name] = self._coerce(val)

    @staticmethod
    def _coerce(val) -> LeverSetting:
        if isinstance(val, LeverSetting):
            return val
        if isinstance(val, (int, float)):
            return LeverSetting(rate=float(val))
        if isinstance(val, dict):
            return LeverSetting(
                rate=float(val.get("rate", 0.0)),
                target=val.get("target"),
                params=val.get("params", {}),
            )
        return LeverSetting()

    def get(self, name: str) -> Optional[LeverSetting]:
        s = self.settings.get(name)
        return s if s and s.rate > 0 else None


# Phase A — the outcome-shaping failures. At most ONE fires per run (see apply): a run is either
# clean or has exactly one primary failure, so a visible lever can never mask a silent divergence
# (a skip turns the run "skipped"; a policy break fails the estimate too) and every diverged run
# maps to exactly one injected cause for 1:1 attribution scoring. The silent family is listed FIRST
# so it wins ties — it's the focus of the harness. Calibration + drift are phase-B overlays.
_PHASE_A = ["silent_wrong", "silent_staleness", "silent_unsupported", "silent_incomplete",
            "silent_policy", "silent_missed_action",
            "skip_propagation", "overt_error", "tool_fault",
            "quality_degrade", "policy_violation", "sla_breach"]


# ── Trace helpers ────────────────────────────────────────────────────────────

def _find_step(result: RunResult, agent: str, step_type: str) -> Optional[TraceStep]:
    for s in result.traces:
        if s.agent == agent and s.step_type == step_type:
            return s
    return None


def _agent_message(result: RunResult, agent: str) -> Optional[TraceStep]:
    return _find_step(result, agent, "agent_message")


# ── The levers ───────────────────────────────────────────────────────────────

def _skip_propagation(result, gt, m, contract, s, ctx) -> Optional[InjectedFault]:
    up = s.target or m.first_agent
    down = m.downstream_agent
    # Upstream agent skips.
    result.traces = [t for t in result.traces if t.agent != down]
    result.traces.append(TraceStep(agent=up, step_type="skip", outcome="skipped",
                                    payload_extra={"reason": "missing_input", "skip_type": "propagated"}))
    result.traces.append(TraceStep(agent=down, step_type="skip", outcome="skipped",
                                    payload_extra={"reason": f"blocked by {up} skip", "skip_type": "propagated"}))
    result.terminal_reason = "skip_propagated"
    result.metadata["skipped"] = True
    return InjectedFault("skip_propagation", up, "upstream_gap",
                         {"upstream": up, "downstream": down})


def _overt_error(result, gt, m, contract, s, ctx) -> Optional[InjectedFault]:
    agent = s.target or m.retriever_agent
    msg = s.params.get("message", "downstream tool call raised")
    result.traces.append(TraceStep(agent=agent, step_type="error", outcome="error",
                                    error=msg, entity_id=result.entity_id))
    if s.params.get("fatal"):
        result.terminal_reason = "error"
        cs = C.signal_index(contract).get(m.correctness_signal)
        if cs is not None:
            result.real_signals[m.correctness_signal] = C.bad_value(cs)
    return InjectedFault("overt_error", agent, "reliability", {"message": msg})


_TOOL_SHAPES = {
    "errored":  lambda now: {"error": "retriever backend 500"},
    "empty":    lambda now: {},
    "fallback": lambda now: {"from_cache": True, "note": "served stale fallback"},
    "stale":    lambda now: {"as_of": (now - timedelta(days=400)).date().isoformat(), "note": "stale index"},
}


def _tool_fault(result, gt, m, contract, s, ctx) -> Optional[InjectedFault]:
    agent = s.target or m.retriever_agent
    shape = s.params.get("shape") or ctx.rng.choice(list(_TOOL_SHAPES))
    step = _find_step(result, agent, "tool_call")
    bad_output = _TOOL_SHAPES[shape](ctx.now)
    if step is None:
        step = TraceStep(agent=agent, step_type="tool_call", tool_name="retrieve",
                         tool_input={}, entity_id=result.entity_id)
        result.traces.insert(0, step)
    step.tool_output = bad_output
    step.outcome = "error" if shape == "errored" else "ok"
    return InjectedFault("tool_fault", agent, "tool_defect", {"shape": shape})


def _quality_degrade(result, gt, m, contract, s, ctx) -> Optional[InjectedFault]:
    agent = s.target or m.resolver_agent
    reason = s.params.get("reason", "reasoning was shallow and skipped the key policy check")
    hit = False
    for e in result.evals:
        if e.agent == agent:
            e.score = min(e.score, 0.35)
            e.passed = False
            e.detail = {"reasoning": reason}
            hit = True
    if not hit:
        result.evals.append(EvalResultDefault(agent, result.entity_id, reason))
    return InjectedFault("quality_degrade", agent, "quality", {"reason": reason})


def _policy_violation(result, gt, m, contract, s, ctx) -> Optional[InjectedFault]:
    idx = C.signal_index(contract)
    c = idx.get(m.policy_signal)
    if c is None:
        return None
    bad = C.bad_value(c)
    result.estimated_signals[m.policy_signal] = bad
    result.real_signals[m.policy_signal] = bad
    return InjectedFault("policy_violation", s.target or m.resolver_agent, "policy",
                         {"signal": m.policy_signal})


def _sla_breach(result, gt, m, contract, s, ctx) -> Optional[InjectedFault]:
    idx = C.signal_index(contract)
    c = idx.get(m.sla_signal)
    if c is None:
        return None
    result.real_signals[m.sla_signal] = C.bad_value(c)
    latency = int(s.params.get("latency_ms", 42000))
    for t in result.traces:
        t.latency_ms = max(t.latency_ms, latency // max(1, len(result.traces)))
    result.metadata["sla_latency_ms"] = latency
    return InjectedFault("sla_breach", None, "sla", {"latency_ms": latency})


def _silent_wrong(result, gt, m, contract, s, ctx) -> Optional[InjectedFault]:
    """The star. Confident, well-formed, L4-passing output that is actually
    wrong on ground truth. Estimated stays good; reality diverges."""
    agent = s.target or m.resolver_agent
    idx = C.signal_index(contract)
    corrupted = []
    for sig in [m.correctness_signal, m.secondary_bad_signal]:
        if not sig:
            continue
        c = idx.get(sig)
        if c is None:
            continue
        # Real side goes bad; estimated (trace) side stays good on purpose.
        result.real_signals[sig] = C.bad_value(c)
        if c.side == "trace":
            # a trace-only signal can't diverge on the outcome; skip so it stays honest
            result.real_signals[sig] = C.good_value(c)
            continue
        corrupted.append(sig)
    # Evals still pass — that is the whole point. Confidence stays high.
    result.confidence = max(result.confidence, 0.9)
    msg = _agent_message(result, agent)
    if msg is not None:
        msg.payload_extra["confidence"] = "HIGH"
    result.metadata["silent_wrong"] = True
    return InjectedFault("silent_wrong", agent, "silent_divergence", {"signals": corrupted})


def _corrupt_correctness(result, contract, m) -> list[str]:
    """Set the primary correctness signal bad on the Real side only, leaving Estimated
    good so evals still pass. Returns the signals corrupted. Shared by the silent family."""
    idx = C.signal_index(contract)
    corrupted = []
    c = idx.get(m.correctness_signal)
    if c is not None and c.side != "trace":
        result.real_signals[m.correctness_signal] = C.bad_value(c)
        corrupted.append(m.correctness_signal)
    return corrupted


def _silent_staleness(result, gt, m, contract, s, ctx) -> Optional[InjectedFault]:
    """Acting on stale information. The retriever serves believable but stale data (an old
    as_of, no error), the answer is built on it, and reality diverges. Estimated stays good;
    Provy's deterministic scan flags the stale tool output and pins the retriever."""
    agent = s.target or m.retriever_agent
    as_of = (ctx.now - timedelta(days=int(s.params.get("age_days", 400)))).date().isoformat()
    step = _find_step(result, agent, "tool_call")
    if step is None:
        step = TraceStep(agent=agent, step_type="tool_call", tool_name="retrieve",
                         tool_input={}, entity_id=result.entity_id)
        result.traces.insert(0, step)
    step.tool_output = {**(step.tool_output or {}), "as_of": as_of, "note": "from index"}
    step.outcome = "ok"  # no error — it looks fine
    corrupted = _corrupt_correctness(result, contract, m)
    result.confidence = max(result.confidence, 0.9)
    result.metadata["silent_staleness"] = True
    return InjectedFault("silent_staleness", agent, "silent_staleness",
                         {"as_of": as_of, "signals": corrupted})


def _silent_unsupported(result, gt, m, contract, s, ctx) -> Optional[InjectedFault]:
    """Cited but unsupported. The retriever surfaces a weak-support signal (a low match score) that
    the RESOLVER builds a confident answer on anyway; reality is wrong. No hard tool defect, so this
    exercises Provy's judge tier ('ignored a red flag'). The culprit is the resolver — the decision
    agent that ignored the weak-match warning — not the retriever that correctly surfaced it."""
    resolver = s.target or m.resolver_agent
    step = _find_step(result, m.retriever_agent, "tool_call")
    if step is None:
        step = TraceStep(agent=m.retriever_agent, step_type="tool_call", tool_name="retrieve",
                         tool_input={}, entity_id=result.entity_id)
        result.traces.insert(0, step)
    # A soft signal only a judge would weigh — deliberately NOT a fallback/stale key.
    step.tool_output = {**(step.tool_output or {}), "match_score": 0.28, "note": "weak match"}
    step.outcome = "ok"
    corrupted = _corrupt_correctness(result, contract, m)
    msg = _agent_message(result, resolver)
    if msg is not None:
        msg.payload_extra["confidence"] = "HIGH"
    result.confidence = max(result.confidence, 0.9)
    result.metadata["silent_unsupported"] = True
    return InjectedFault("silent_unsupported", resolver, "silent_unsupported",
                         {"match_score": 0.28, "signals": corrupted})


def _silent_incomplete(result, gt, m, contract, s, ctx) -> Optional[InjectedFault]:
    """Looks done, isn't. The resolver marks the work complete and the reviewer approves, but a
    required sub-step was silently skipped, so reality diverges. Estimated stays good; the
    culprit is the agent that claimed done."""
    agent = s.target or m.resolver_agent
    skipped = s.params.get("step", "verification")
    msg = _agent_message(result, agent)
    if msg is not None:
        msg.payload_extra["completed"] = True
        msg.payload_extra["_skipped_step"] = skipped
    corrupted = _corrupt_correctness(result, contract, m)
    result.confidence = max(result.confidence, 0.9)
    result.metadata["silent_incomplete"] = True
    return InjectedFault("silent_incomplete", agent, "silent_incomplete",
                         {"skipped_step": skipped, "signals": corrupted})


def _silent_policy(result, gt, m, contract, s, ctx) -> Optional[InjectedFault]:
    """Compliant on paper. The policy check passes on the Estimated side and the reviewer
    approves, but the action actually violated policy and the audit/outcome says so (policy
    signal bad on the Real side only). Contrast policy_violation, which fails the check openly."""
    agent = s.target or m.reviewer_agent
    idx = C.signal_index(contract)
    c = idx.get(m.policy_signal)
    if c is None:
        return None
    result.estimated_signals[m.policy_signal] = C.good_value(c)  # looks compliant
    result.real_signals[m.policy_signal] = C.bad_value(c)        # reality: violated
    msg = _agent_message(result, agent)
    if msg is not None:
        msg.payload_extra["review"] = "approved: within policy"
    result.confidence = max(result.confidence, 0.9)
    result.metadata["silent_policy"] = True
    return InjectedFault("silent_policy", agent, "silent_policy", {"signal": m.policy_signal})


def _silent_missed_action(result, gt, m, contract, s, ctx) -> Optional[InjectedFault]:
    """Should have acted, didn't. A case that needed escalation or a flag; the resolver took the
    quiet path instead of acting on the policy signal, and 'did nothing' reads as a clean pass.
    Reality diverges. The culprit is the resolver — the decision agent that ignored the signal."""
    agent = s.target or m.resolver_agent
    corrupted = _corrupt_correctness(result, contract, m)
    result.metadata["needed_action"] = True
    result.metadata["silent_missed_action"] = True
    result.confidence = max(result.confidence, 0.9)
    return InjectedFault("silent_missed_action", agent, "silent_missed_action",
                         {"signals": corrupted})


def _confidence_miscalibration(result, gt, m, contract, s, ctx) -> Optional[InjectedFault]:
    """Report HIGH confidence on the runs it is wrong on, LOW on the right ones."""
    wrong = result.outcome_label == "fail"
    result.confidence = 0.9 if wrong else 0.25
    msg = _agent_message(result, s.target or m.resolver_agent)
    if msg is not None:
        msg.payload_extra["confidence"] = "HIGH" if wrong else "LOW"
    return InjectedFault("confidence_miscalibration", s.target or m.resolver_agent,
                         "calibration", {"reported": "HIGH" if wrong else "LOW", "wrong": wrong})


def _silent_drift(result, gt, m, contract, s, ctx) -> Optional[InjectedFault]:
    """Gradually degrade an agent over sessions while the surface looks stable."""
    onset = int(s.params.get("onset", 20))
    if ctx.session_index < onset:
        return None
    mode = s.params.get("mode", "quality")
    severity = ctx.session_index - onset + 1
    agent = s.target or m.drift_agent or m.resolver_agent
    if mode == "quality":
        drop = min(0.06 * severity, 0.6)
        for e in result.evals:
            if e.agent == agent:
                e.score = max(0.0, e.score - drop)
                e.passed = e.score >= 0.7
                e.detail = {"reasoning": "gradual quality decay (drift)"}
    elif mode == "schema":
        msg = _agent_message(result, agent)
        if msg is not None:
            msg.payload_extra.pop("confidence", None)
            msg.payload_extra["_dropped_key"] = "resolution_code"
    elif mode == "volume":
        keep = [t for t in result.traces if t.agent != agent]
        one = _agent_message(result, agent)
        result.traces = keep + ([one] if one else [])
    result.metadata["drift"] = {"agent": agent, "mode": mode, "severity": severity}
    return InjectedFault("silent_drift", agent, f"drift_{mode}",
                         {"onset": onset, "severity": severity, "mode": mode})


def EvalResultDefault(agent, entity_id, reason):
    from .types import EvalResult
    return EvalResult(agent=agent, eval_name="reasoning_quality", score=0.3,
                      passed=False, detail={"reasoning": reason}, entity_id=entity_id)


_LEVER_FNS = {
    "skip_propagation": _skip_propagation,
    "overt_error": _overt_error,
    "tool_fault": _tool_fault,
    "quality_degrade": _quality_degrade,
    "policy_violation": _policy_violation,
    "sla_breach": _sla_breach,
    "silent_wrong": _silent_wrong,
    "silent_staleness": _silent_staleness,
    "silent_unsupported": _silent_unsupported,
    "silent_incomplete": _silent_incomplete,
    "silent_policy": _silent_policy,
    "silent_missed_action": _silent_missed_action,
    "confidence_miscalibration": _confidence_miscalibration,
    "silent_drift": _silent_drift,
}


# ── Finalize ─────────────────────────────────────────────────────────────────

def finalize(result: RunResult, contract: list[Criterion]) -> None:
    """Recompute outcome_label from the real signals and record what the
    estimate claimed, so divergence is well-defined."""
    est_ok = all(
        C.meets(c, result.estimated_signals.get(c.signal))
        for c in contract if c.side in ("trace", "both")
    )
    real_ok = all(
        C.meets(c, result.real_signals.get(c.signal))
        for c in contract if c.side in ("outcome", "both")
    )
    result.metadata["estimated_success"] = est_ok
    if result.metadata.get("skipped"):
        result.outcome_label = "skipped"
        result.outcome_value = None
        return
    result.outcome_label = "success" if real_ok else "fail"
    result.outcome_value = 1.0 if real_ok else -1.0


# ── Apply ────────────────────────────────────────────────────────────────────

def apply(result: RunResult, gt, manifest: LeverManifest,
          contract: list[Criterion], config: LeverConfig, ctx: RunContext) -> list[InjectedFault]:
    """Roll each configured lever against the seeded RNG and mutate the run.
    Returns the list of injected faults (ground truth)."""
    faults: list[InjectedFault] = []
    primary_fired = False   # at most one phase-A (outcome-shaping) failure per run

    def _fire(name: str, exclusive: bool = True) -> None:
        nonlocal primary_fired
        s = config.get(name)
        if not s:
            return
        if exclusive and primary_fired:
            return
        if ctx.rng.random() >= s.rate:
            return
        f = _LEVER_FNS[name](result, gt, manifest, contract, s, ctx)
        if f is not None:
            faults.append(f)
            if exclusive:
                primary_fired = True

    for name in _PHASE_A:
        _fire(name)

    finalize(result, contract)

    # Calibration + drift are overlays: they don't reshape a single run's outcome, so they layer on
    # freely (a run can be both silently wrong AND overconfident, or in a drifting window).
    _fire("confidence_miscalibration", exclusive=False)
    _fire("silent_drift", exclusive=False)

    result.faults = faults
    return faults
