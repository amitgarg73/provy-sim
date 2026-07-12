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


def test_skip_propagation_sets_terminal_reason():
    pack = get_pack("support")
    outs = _run_with(pack, {"skip_propagation": 1.0}, n=10, seed=3)
    for o in outs:
        assert o.result.terminal_reason == "skip_propagated"
        assert o.result.outcome_label == "skipped"
        assert any(t.step_type == "skip" for t in o.result.traces)
        assert any(f.lever == "skip_propagation" for f in o.result.faults)


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


def test_silent_unsupported_pins_resolver_via_soft_signal():
    pack = get_pack("claims")
    m = pack.lever_manifest()
    outs = _run_with(pack, {"silent_unsupported": 1.0}, n=12, seed=21)
    for o in outs:
        f = _fault(o, "silent_unsupported")
        assert f is not None and f.agent == m.resolver_agent
        assert o.result.diverged() is True and all(e.passed for e in o.result.evals)
        step = next(t for t in o.result.traces
                    if t.agent == m.retriever_agent and t.step_type == "tool_call")
        assert step.tool_output.get("match_score") == 0.28         # soft signal, not a hard defect


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


def test_silent_missed_action_is_a_clean_looking_omission():
    pack = get_pack("claims")
    m = pack.lever_manifest()
    outs = _run_with(pack, {"silent_missed_action": 1.0}, n=10, seed=24)
    for o in outs:
        f = _fault(o, "silent_missed_action")
        assert f is not None and f.agent == m.reviewer_agent
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
