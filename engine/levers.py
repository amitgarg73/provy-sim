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


def _ensure_tool_call(result: RunResult, agent: str) -> TraceStep:
    """The agent's tool_call span, creating a placeholder one if the pack didn't emit any."""
    step = _find_step(result, agent, "tool_call")
    if step is None:
        step = TraceStep(agent=agent, step_type="tool_call", tool_name="retrieve",
                         tool_input={}, entity_id=result.entity_id)
        result.traces.insert(0, step)
    return step


# ── The levers ───────────────────────────────────────────────────────────────

def _skip_propagation(result, gt, m, contract, s, ctx) -> Optional[InjectedFault]:
    up = s.target or m.first_agent
    # An agent bails, so EVERY agent after it is blocked — a sequential pipeline can't run without its
    # input. (Before, only the one named downstream agent was skipped, which left middle agents
    # illogically still running.) Derive pipeline order from the clean run's traces.
    order: list[str] = []
    for t in result.traces:
        if t.agent not in order:
            order.append(t.agent)
    up_i = order.index(up) if up in order else 0
    blocked = order[up_i + 1:]                       # everyone downstream of the bailing agent
    result.traces = [t for t in result.traces if t.agent not in order[up_i:]]
    result.traces.append(TraceStep(agent=up, step_type="skip", outcome="skipped",
                                    payload_extra={"reason": "missing_input", "skip_type": "propagated"}))
    for d in blocked:
        result.traces.append(TraceStep(agent=d, step_type="skip", outcome="skipped",
                                        payload_extra={"reason": f"blocked by {up} skip", "skip_type": "propagated"}))
    # A dropped work item is a FAILURE for these domains, not a benign stand-down (the trading
    # "skip = no opportunity" convention does not apply — a support ticket must be handled). Make it a
    # VISIBLE pipeline break, not a silent divergence: fail the correctness signal on the Real side,
    # and a trace/both signal on BOTH sides (so the estimate can't look clean when the pipeline broke).
    # Terminal reason is deliberately not a recognized benign skip, so Provy grades + flags it.
    idx = C.signal_index(contract)
    c = idx.get(m.correctness_signal)
    if c is not None:
        result.real_signals[m.correctness_signal] = C.bad_value(c)
    for cc in contract:
        if cc.side in ("trace", "both"):
            result.estimated_signals[cc.signal] = C.bad_value(cc)
            result.real_signals[cc.signal] = C.bad_value(cc)
            break
    result.terminal_reason = "pipeline_break"
    result.metadata["pipeline_break"] = {"upstream": up, "blocked": blocked}
    return InjectedFault("skip_propagation", up, "upstream_gap",
                         {"upstream": up, "blocked": blocked})


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
    # The culprit is the agent that owns the policy signal (e.g. crm's router owns routed_correct),
    # not necessarily the decision agent — fall back to the resolver when the pack doesn't say.
    return InjectedFault("policy_violation", s.target or m.policy_agent or m.resolver_agent, "policy",
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


# ── L1 / L2 overlay levers ────────────────────────────────────────────────────
# These aim at a single tool call (L1 Tool Activity) or model call (L2 LLM Calls).
# They are non-outcome-shaping: no signal moves, so they layer on any run and only
# light up Provy's L1/L2 activity checks (which feed reliability, never the outcome,
# quality, or trust aggregates). That is why they are phase-B overlays, not phase-A.

def _tool_latency(result, gt, m, contract, s, ctx) -> Optional[InjectedFault]:
    """One tool call runs far slower than the latency budget (Provy L1 Tool Latency).
    Latency is not an attribution signal, so this can co-fire with a divergence
    without masking its cause."""
    agent = s.target or m.retriever_agent
    latency = int(s.params.get("latency_ms", 12000))
    step = _ensure_tool_call(result, agent)
    step.latency_ms = max(step.latency_ms, latency)
    return InjectedFault("tool_latency", agent, "tool_latency", {"latency_ms": latency})


def _tool_errors(result, gt, m, contract, s, ctx) -> Optional[InjectedFault]:
    """An agent's tool call errors, pushing its tool error rate over the ceiling
    (Provy L1 Tool Error Rate). Deliberately does NOT set a terminal error or corrupt
    a signal, so the session outcome is untouched and the run never diverges from this
    alone. apply() only fires it on runs with no outcome-shaping fault, so the errored
    span can't be mistaken for a silent divergence's culprit."""
    agent = s.target or m.retriever_agent
    msg = s.params.get("message", "tool backend returned 500")
    step = _ensure_tool_call(result, agent)
    step.outcome = "error"
    step.error = msg
    return InjectedFault("tool_errors", agent, "tool_error_rate", {"message": msg})


def _llm_cost(result, gt, m, contract, s, ctx) -> Optional[InjectedFault]:
    """An agent's model call costs far more than the session budget (Provy L2 LLM Cost) —
    a runaway prompt or an oversized model. Non-outcome-shaping."""
    agent = s.target or m.resolver_agent
    cost = float(s.params.get("cost_usd", 0.35))
    step = _agent_message(result, agent)
    if step is None:
        return None
    step.cost_usd = max(step.cost_usd, cost)
    return InjectedFault("llm_cost", agent, "llm_cost_budget", {"cost_usd": cost})


def _llm_tokens(result, gt, m, contract, s, ctx) -> Optional[InjectedFault]:
    """An agent's model call burns far more tokens than the session budget (Provy L2 LLM
    Tokens — off until a tenant sets a token budget, so this proves the check once one is
    configured). Non-outcome-shaping; leaves cost alone so the two L2 checks stay independent."""
    agent = s.target or m.resolver_agent
    tokens = int(s.params.get("tokens", 60000))
    step = _agent_message(result, agent)
    if step is None:
        return None
    step.tokens_input = max(step.tokens_input, tokens)
    return InjectedFault("llm_tokens", agent, "llm_token_budget", {"tokens": tokens})


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
    "tool_latency": _tool_latency,
    "tool_errors": _tool_errors,
    "llm_cost": _llm_cost,
    "llm_tokens": _llm_tokens,
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
          contract: list[Criterion], config: LeverConfig, ctx: RunContext,
          pack_injector=None) -> list[InjectedFault]:
    """Roll each configured lever against the seeded RNG and mutate the run.
    Returns the list of injected faults (ground truth).

    pack_injector, when given, is a pack-specific phase-A outcome-shaping injector
    (e.g. the Stripe settlement feed). It joins the same exclusion as the generic
    phase-A levers: it may only cause a failure when no generic phase-A lever fired,
    so every run still has at most one primary cause and 1:1 attribution holds. Its
    signature is (result, ctx, primary_fired) -> InjectedFault | None."""
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

    # The pack's own phase-A injector runs after the generic ones and defers to them:
    # it is told whether a primary already fired so it can stand down (show a clean,
    # promise-kept outcome) instead of stacking a second cause on the same run.
    if pack_injector is not None:
        f = pack_injector(result, ctx, primary_fired)
        if f is not None:
            faults.append(f)
            primary_fired = True

    finalize(result, contract)

    # Calibration + drift are overlays: they don't reshape a single run's outcome, so they layer on
    # freely (a run can be both silently wrong AND overconfident, or in a drifting window).
    _fire("confidence_miscalibration", exclusive=False)
    _fire("silent_drift", exclusive=False)

    # L1/L2 overlays. Latency, cost, and tokens are not attribution signals, so they layer on any
    # run without masking a cause. An errored tool span IS an attribution signal, so tool_errors
    # only fires on a run with no divergence to attribute — it still exercises the L1 error-rate
    # check on clean runs without stealing a silent culprit's blame.
    for name in ("tool_latency", "llm_cost", "llm_tokens"):
        _fire(name, exclusive=False)
    if not primary_fired:
        _fire("tool_errors", exclusive=False)

    result.faults = faults
    return faults
