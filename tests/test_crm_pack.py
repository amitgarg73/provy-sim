"""CRM pack parity checks: every lever maps 1:1 to the right crm culprit, silent
runs diverge while the evals stay green, skip cascades from the first agent, and a
clean run grades 5/5. Mirrors the support/claims lever coverage so the crm fleet
onboards to Provy with no debugging."""
from engine.contract import grade
from engine.levers import LeverConfig
from engine.runner import BatchRunner
from packs import get_pack


def _run(rates, n=12, seed=1, start_index=0):
    pack = get_pack("crm")
    runner = BatchRunner(pack, LeverConfig(rates), emitter=None, ledger=None,
                         seed=seed, start_index=start_index)
    return pack, runner.run_batch(n)


def _fault(o, lever):
    return next((f for f in o.result.faults if f.lever == lever), None)


def test_crm_clean_run_grades_five_of_five():
    pack, outs = _run({}, n=10, seed=2)
    ct = pack.contract()
    for o in outs:
        r = o.result
        assert r.outcome_label == "success"
        assert r.diverged() is False
        assert all(e.passed for e in r.evals)
        g = grade(ct, r.estimated_signals, r.real_signals)
        assert g["met"] == g["total"] == len(ct) == 5


def test_crm_scorer_decision_cites_the_enriched_context():
    """The decision agent (scorer) must ground its call in the enriched
    firmographics it used, and the employees it cites must match the enricher's
    tool output — otherwise Provy's quality judge has nothing to verify against."""
    pack, outs = _run({}, n=10, seed=2)
    for o in outs:
        enr = next(t for t in o.result.traces
                   if t.agent == "enricher" and t.step_type == "tool_call")
        scorer = next(t for t in o.result.traces
                      if t.agent == "scorer" and t.step_type == "agent_message")
        emp = enr.tool_output["employees"]
        assert scorer.payload_extra["employees"] == emp
        assert f"{emp} employees" in scorer.outcome          # cites enriched firmographics
        assert f"source={o.item['source']}" in scorer.outcome
        assert f"intent={o.item['intent']}" in scorer.outcome
        assert f"qualification={o.ground_truth['qualification']}" in scorer.outcome


def test_crm_silent_levers_pin_the_right_culprit_and_diverge():
    pack = get_pack("crm")
    m = pack.lever_manifest()
    expected = {
        "silent_wrong": m.resolver_agent,          # scorer: confident but wrong qualification
        "silent_staleness": m.retriever_agent,     # enricher: acted on stale firmographics
        "silent_unsupported": m.resolver_agent,    # scorer ignored the weak-match warning
        "silent_incomplete": m.resolver_agent,     # scorer claimed done, skipped a step
        "silent_policy": m.reviewer_agent,         # qa approved routing that violated the rule
        "silent_missed_action": m.resolver_agent,  # scorer took the quiet path
    }
    for lever, culprit in expected.items():
        _, outs = _run({lever: 1.0}, n=12, seed=20)
        for o in outs:
            f = _fault(o, lever)
            assert f is not None and f.agent == culprit, (lever, f and f.agent)
            assert o.result.outcome_label == "fail", lever
            assert o.result.diverged() is True, lever
            assert all(e.passed for e in o.result.evals), lever   # silent: evals stay green


def test_crm_skip_propagation_cascades_from_the_enricher():
    pack, outs = _run({"skip_propagation": 1.0}, n=10, seed=3)
    m = pack.lever_manifest()
    for o in outs:
        f = _fault(o, "skip_propagation")
        assert f is not None and f.agent == m.first_agent          # enricher bailed
        assert o.result.terminal_reason == "pipeline_break"
        assert o.result.outcome_label == "fail"
        assert o.result.diverged() is False                        # visible, not a silent divergence
        assert any(t.step_type == "skip" for t in o.result.traces)
        downstream = {m.resolver_agent, m.downstream_agent, m.reviewer_agent}
        ran = [t.agent for t in o.result.traces
               if t.agent in downstream and t.step_type != "skip"]
        assert ran == [], ran


def test_crm_visible_levers_fire_without_faking_a_divergence():
    """tool_fault and overt_error hit the enricher and surface a defect, but they
    do not flip the outcome on their own (no silent divergence to mis-score)."""
    pack = get_pack("crm")
    m = pack.lever_manifest()
    _, tf = _run({"tool_fault": {"rate": 1.0, "params": {"shape": "stale"}}}, n=6, seed=6)
    for o in tf:
        step = next(t for t in o.result.traces
                    if t.agent == m.retriever_agent and t.step_type == "tool_call")
        assert "as_of" in step.tool_output
        assert _fault(o, "tool_fault").agent == m.retriever_agent
    _, oe = _run({"overt_error": 1.0}, n=6, seed=4)
    for o in oe:
        assert any(t.step_type == "error" for t in o.result.traces)
        assert _fault(o, "overt_error").agent == m.retriever_agent


def test_crm_policy_violation_is_visible_and_pins_the_router():
    """routed_correct is the router's output, so a policy break is the router's fault and fails
    BOTH sides (a visible failure) — separable from silent_policy, which corrupts Real only."""
    pack, outs = _run({"policy_violation": 1.0}, n=10, seed=11)
    m = pack.lever_manifest()
    for o in outs:
        f = _fault(o, "policy_violation")
        assert f is not None and f.agent == m.policy_agent == "router"
        assert o.result.estimated_signals[m.policy_signal] is False
        assert o.result.real_signals[m.policy_signal] is False
        assert o.result.diverged() is False   # visible, not a silent divergence
