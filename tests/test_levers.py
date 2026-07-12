"""Lever application must produce the expected injected-truth records at the
configured rate over a seeded batch, and each lever's signature effect."""
import random

from engine.levers import LeverConfig
from engine.runner import BatchRunner
from packs import get_pack
from conftest import make_ctx


def _run_with(pack, rates, n=1, seed=0, start_index=0):
    runner = BatchRunner(pack, LeverConfig(rates), emitter=None, ledger=None,
                         seed=seed, start_index=start_index)
    return runner.run_batch(n)


def test_silent_wrong_always_diverges():
    pack = get_pack("support")
    outs = _run_with(pack, {"silent_wrong": 1.0}, n=25, seed=1)
    for o in outs:
        levers = [f.lever for f in o.result.faults]
        assert "silent_wrong" in levers
        assert o.result.outcome_label == "fail"
        assert o.result.diverged() is True
        # Evals still pass — that is the whole point of a silent-wrong run.
        assert all(e.passed for e in o.result.evals)


def test_no_levers_is_clean():
    pack = get_pack("claims")
    outs = _run_with(pack, {}, n=20, seed=2)
    for o in outs:
        assert o.result.faults == []
        assert o.result.outcome_label == "success"
        assert o.result.diverged() is False


def test_injection_rate_matches_config():
    pack = get_pack("crm")
    n = 600
    outs = _run_with(pack, {"silent_wrong": 0.3}, n=n, seed=5)
    fired = sum(1 for o in outs if any(f.lever == "silent_wrong" for f in o.result.faults))
    rate = fired / n
    assert abs(rate - 0.3) < 0.06, f"observed {rate}, expected ~0.30"


def test_skip_propagation_is_a_visible_failure_not_a_benign_skip():
    pack = get_pack("support")
    m = pack.lever_manifest()
    outs = _run_with(pack, {"skip_propagation": 1.0}, n=10, seed=3)
    for o in outs:
        # A dropped work item fails — not a benign "correctly stood down" skip.
        assert o.result.terminal_reason == "pipeline_break"
        assert o.result.outcome_label == "fail"
        assert o.result.diverged() is False   # visible (estimate fails too), not a silent divergence
        assert any(t.step_type == "skip" for t in o.result.traces)
        assert any(f.lever == "skip_propagation" for f in o.result.faults)
        # The first agent bailed, so EVERY downstream agent is blocked — none may have a real step.
        downstream = {m.retriever_agent, m.resolver_agent, m.reviewer_agent}
        ran = [t.agent for t in o.result.traces if t.agent in downstream and t.step_type != "skip"]
        assert ran == [], f"downstream agents ran despite the upstream skip: {ran}"


def test_overt_error_emits_error_trace():
    pack = get_pack("claims")
    outs = _run_with(pack, {"overt_error": 1.0}, n=8, seed=4)
    for o in outs:
        assert any(t.step_type == "error" for t in o.result.traces)


def test_tool_fault_shapes():
    pack = get_pack("support")
    for shape in ("errored", "empty", "fallback", "stale"):
        outs = _run_with(pack, {"tool_fault": {"rate": 1.0, "params": {"shape": shape}}}, n=3, seed=6)
        for o in outs:
            step = next(t for t in o.result.traces
                        if t.agent == pack.lever_manifest().retriever_agent and t.step_type == "tool_call")
            if shape == "errored":
                assert "error" in step.tool_output
            elif shape == "empty":
                assert step.tool_output == {}
            elif shape == "fallback":
                assert step.tool_output.get("from_cache") is True
            elif shape == "stale":
                assert "as_of" in step.tool_output


def test_quality_degrade_fails_eval_with_reasoning():
    pack = get_pack("crm")
    outs = _run_with(pack, {"quality_degrade": 1.0}, n=6, seed=7)
    m = pack.lever_manifest()
    for o in outs:
        degraded = [e for e in o.result.evals if e.agent == m.resolver_agent]
        assert degraded and all(not e.passed for e in degraded)
        assert all(e.detail.get("reasoning") for e in degraded)


def test_confidence_miscalibration_inverts():
    pack = get_pack("support")
    outs = _run_with(pack, {"silent_wrong": 1.0, "confidence_miscalibration": 1.0}, n=10, seed=8)
    for o in outs:
        # wrong run reports HIGH confidence
        assert o.result.outcome_label == "fail"
        assert o.result.confidence >= 0.85
        assert any(f.lever == "confidence_miscalibration" for f in o.result.faults)


def test_silent_drift_only_after_onset():
    pack = get_pack("support")
    rates = {"silent_drift": {"rate": 1.0, "params": {"onset": 20, "mode": "quality"}}}
    before = _run_with(pack, rates, n=1, seed=9, start_index=5)[0]
    after = _run_with(pack, rates, n=1, seed=9, start_index=30)[0]
    assert not any(f.lever == "silent_drift" for f in before.result.faults)
    assert any(f.lever == "silent_drift" for f in after.result.faults)


def test_policy_violation_breaks_both_sides():
    pack = get_pack("support")
    outs = _run_with(pack, {"policy_violation": 1.0}, n=8, seed=10)
    m = pack.lever_manifest()
    for o in outs:
        assert o.result.estimated_signals[m.policy_signal] is False
        assert o.result.real_signals[m.policy_signal] is False


def _fault(o, lever):
    return next((f for f in o.result.faults if f.lever == lever), None)


def test_silent_staleness_pins_retriever_and_diverges():
    pack = get_pack("support")
    m = pack.lever_manifest()
    outs = _run_with(pack, {"silent_staleness": 1.0}, n=12, seed=20)
    for o in outs:
        f = _fault(o, "silent_staleness")
        assert f is not None and f.agent == m.retriever_agent
        assert o.result.outcome_label == "fail" and o.result.diverged() is True
        assert all(e.passed for e in o.result.evals)           # silent: evals still pass
        step = next(t for t in o.result.traces
                    if t.agent == m.retriever_agent and t.step_type == "tool_call")
        assert "as_of" in step.tool_output and step.outcome == "ok"   # stale but not an error


def test_silent_unsupported_pins_the_resolver_who_ignored_the_weak_match():
    pack = get_pack("claims")
    m = pack.lever_manifest()
    outs = _run_with(pack, {"silent_unsupported": 1.0}, n=12, seed=21)
    for o in outs:
        f = _fault(o, "silent_unsupported")
        # The retriever surfaced the weak match correctly; the resolver ignored it -> resolver.
        assert f is not None and f.agent == m.resolver_agent
        assert o.result.diverged() is True and all(e.passed for e in o.result.evals)
        step = next(t for t in o.result.traces
                    if t.agent == m.retriever_agent and t.step_type == "tool_call")
        assert step.tool_output.get("match_score") == 0.28         # soft signal, not a hard defect


_PHASE_A_LEVERS = ("silent_wrong", "silent_staleness", "silent_unsupported", "silent_incomplete",
                   "silent_policy", "silent_missed_action", "skip_propagation", "overt_error",
                   "tool_fault", "quality_degrade", "policy_violation", "sla_breach")


def test_at_most_one_primary_failure_per_run():
    pack = get_pack("support")
    # Every phase-A lever at 100% — exactly one fires per run (silent wins ties, it is listed first).
    outs = _run_with(pack, {lv: 1.0 for lv in _PHASE_A_LEVERS}, n=30, seed=40)
    for o in outs:
        primary = [f for f in o.result.faults if f.lever in _PHASE_A_LEVERS]
        assert len(primary) == 1, f"expected exactly one primary fault, got {[f.lever for f in primary]}"
        assert primary[0].lever in _PHASE_A_LEVERS[:6]  # a silent one


def test_a_visible_lever_never_masks_a_silent_divergence():
    pack = get_pack("support")
    # silent_wrong + skip_propagation both at 100%. Without one-per-run, the skip would turn the run
    # "skipped" and erase the silent divergence. Now only silent_wrong fires and the run diverges.
    outs = _run_with(pack, {"silent_wrong": 1.0, "skip_propagation": 1.0}, n=20, seed=41)
    for o in outs:
        levers = [f.lever for f in o.result.faults]
        assert levers == ["silent_wrong"], levers
        assert o.result.diverged() is True
        assert o.result.terminal_reason != "skip_propagated"


def test_silent_incomplete_marks_completed_but_diverges():
    pack = get_pack("crm")
    m = pack.lever_manifest()
    outs = _run_with(pack, {"silent_incomplete": 1.0}, n=10, seed=22)
    for o in outs:
        f = _fault(o, "silent_incomplete")
        assert f is not None and f.agent == m.resolver_agent
        assert o.result.diverged() is True and all(e.passed for e in o.result.evals)
        msg = next(t for t in o.result.traces
                   if t.agent == m.resolver_agent and t.step_type == "agent_message")
        assert msg.payload_extra.get("completed") is True


def test_silent_policy_compliant_on_paper():
    pack = get_pack("support")
    m = pack.lever_manifest()
    outs = _run_with(pack, {"silent_policy": 1.0}, n=10, seed=23)
    for o in outs:
        f = _fault(o, "silent_policy")
        assert f is not None and f.agent == m.reviewer_agent
        # Estimated compliant, Real violated — the divergence a check-only tool never sees.
        assert o.result.estimated_signals[m.policy_signal] is True
        assert o.result.real_signals[m.policy_signal] is False
        assert o.result.diverged() is True and all(e.passed for e in o.result.evals)


def test_claims_policy_violation_fails_visibly_not_silently():
    """On claims the policy_signal (within_limit) is a 'both' condition, so an open policy break
    fails the Estimated side too — a VISIBLE failure, distinct from silent_policy which diverges.
    This is what keeps policy_violation and silent_policy attributable 1:1 rather than collapsing."""
    pack = get_pack("claims")
    m = pack.lever_manifest()
    outs = _run_with(pack, {"policy_violation": 1.0}, n=10, seed=30)
    for o in outs:
        assert any(f.lever == "policy_violation" for f in o.result.faults)
        assert o.result.estimated_signals[m.policy_signal] is False
        assert o.result.real_signals[m.policy_signal] is False
        assert o.result.outcome_label == "fail"
        assert o.result.diverged() is False   # visible: the estimate fails too, not a silent divergence


def test_claims_silent_policy_diverges_where_policy_violation_does_not():
    """Contrast: silent_policy keeps the Estimated side compliant and only corrupts Real -> diverges."""
    pack = get_pack("claims")
    m = pack.lever_manifest()
    outs = _run_with(pack, {"silent_policy": 1.0}, n=10, seed=31)
    for o in outs:
        f = _fault(o, "silent_policy")
        assert f is not None and f.agent == m.reviewer_agent
        assert o.result.estimated_signals[m.policy_signal] is True
        assert o.result.real_signals[m.policy_signal] is False
        assert o.result.diverged() is True and all(e.passed for e in o.result.evals)


def test_claims_clean_run_has_no_contradictory_traces():
    """A clean claims run must not contradict itself: docs are genuinely complete and no trace
    reports a within_limit value that clashes with the True Estimated signal (the payload cites the
    amount check as amount_within_limit, a distinct key). A self-contradicting clean run would make
    Provy flag a fault where none was injected, breaking 1:1 attribution."""
    from packs.claims.pack import DOC_SETS
    pack = get_pack("claims")
    outs = _run_with(pack, {}, n=40, seed=32)
    for o in outs:
        assert len(o.item["docs_submitted"]) == len(DOC_SETS[o.item["claim_type"]])
        for t in o.result.traces:
            # No trace may carry within_limit=False on a clean run; the amount fact lives under
            # its own key so it can never collide with the contract signal.
            assert t.payload_extra.get("within_limit") in (None, True)


def test_silent_missed_action_is_a_clean_looking_omission():
    pack = get_pack("claims")
    m = pack.lever_manifest()
    outs = _run_with(pack, {"silent_missed_action": 1.0}, n=10, seed=24)
    for o in outs:
        f = _fault(o, "silent_missed_action")
        # The resolver took the quiet path instead of acting on the signal -> resolver.
        assert f is not None and f.agent == m.resolver_agent
        assert o.result.diverged() is True and all(e.passed for e in o.result.evals)
        assert o.result.metadata.get("needed_action") is True
        # No error/skip trace — "did nothing" looks like a clean pass.
        assert not any(t.step_type in ("error", "skip") for t in o.result.traces)


def test_faults_recorded_in_ground_truth_record():
    pack = get_pack("claims")
    outs = _run_with(pack, {"silent_wrong": 1.0}, n=3, seed=11)
    for o in outs:
        rec = o.record
        assert rec["faults"], "ground-truth record must log the injected faults"
        assert rec["diverged"] is True
        assert rec["outcome_post"]["label"] == "fail"
