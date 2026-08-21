"""The evidence-derived levers (21 Aug 2026).

⛔ THE DEFECT THESE WERE WRITTEN AGAINST. The original `silent_*` family is six names for one
mechanism: five call `_corrupt_correctness`, seven force `confidence = 0.9`, and they differ only in
which trace field carries a decorative note. So a "silent failure" demo showed the simulator writing
two different values into two fields, not anything a practitioner would recognise.

⛔ AND THE SIM WAS A MIRROR. `_TOOL_SHAPES` is errored/empty/fallback/stale; Provy's verifiable
detector set is errored/empty_output/fallback/stale/reported_negative. Four of five, name for name.
The sim produced exactly what Provy could already find.

So the load-bearing test here is not "does each lever fire" — it is
`test_each_lever_leaves_a_distinct_trace_signature`, which fails the moment a new lever becomes
another alias for corrupting a signal.
"""
import pytest

from engine.levers import LeverConfig
from engine.runner import BatchRunner
from packs import get_pack

NEW_LEVERS = [
    "ok_but_empty", "escalation_refused", "fabricated_policy", "overliteral_constraint",
    "reversed_on_appeal", "retry_loop", "agent_paralysis", "correlation_as_cause",
    "context_truncated", "parametric_override",
]


def _run_with(pack_name, rates, n=6, seed=11):
    pack = get_pack(pack_name)
    runner = BatchRunner(pack, LeverConfig(rates), emitter=None, ledger=None, seed=seed)
    return runner.run_batch(n)


@pytest.mark.parametrize("lever", NEW_LEVERS)
def test_lever_fires_and_diverges(lever):
    outs = _run_with("support", {lever: 1.0})
    for o in outs:
        assert lever in [f.lever for f in o.result.faults], f"{lever} did not fire"
        assert o.result.outcome_label == "fail", f"{lever} did not reach the real outcome"


@pytest.mark.parametrize("lever", NEW_LEVERS)
def test_every_pack_can_carry_every_lever(lever):
    """A lever that only works on one pack is a demo, not a mechanism."""
    for name in ("support", "claims", "crm", "claude_code", "legal", "travel", "revops"):
        outs = _run_with(name, {lever: 1.0}, n=2)
        assert any(lever in [f.lever for f in o.result.faults] for o in outs), \
            f"{lever} never fired on {name}"


def _signature(result):
    """What a reader could actually SEE about this run, ignoring the corrupted signal itself."""
    keys = set()
    for t in result.traces:
        for k in (t.tool_output or {}):
            keys.add(f"tool.{k}")
        for k in (t.payload_extra or {}):
            keys.add(f"msg.{k}")
        if t.error:
            keys.add("tool.error")
    keys.add(f"steps~{min(len(result.traces) // 4, 4)}")
    return frozenset(keys)


def test_each_lever_leaves_a_distinct_trace_signature():
    """⛔ THE ANTI-MONOCULTURE GUARD. This is the whole point of the rewiring.

    Mutating any new lever into a copy of another (or into a bare `_corrupt_correctness` call)
    collapses two signatures together and fails here. Without this the levers drift back into
    aliases for one another, which is exactly how the `silent_*` family ended up as six names for
    one mechanism.
    """
    seen = {}
    for lever in NEW_LEVERS:
        out = _run_with("support", {lever: 1.0}, n=1)[0]
        sig = _signature(out.result)
        clash = [k for k, v in seen.items() if v == sig]
        assert not clash, f"{lever} is indistinguishable from {clash[0]} in the trace"
        seen[lever] = sig
    assert len(set(seen.values())) == len(NEW_LEVERS)


def test_reversed_on_appeal_is_clean_at_run_time():
    """⛔ THE ONLY HONEST SETTLEMENT-LAG LEVER, and the reason the whole exercise matters.

    Nothing is wrong while the run is running: the claimed side is right, every check passes, the
    trace carries no defect. The real signal flips only when reconciliation arrives. A run-time
    supervisor cannot catch this even in principle.
    """
    outs = _run_with("claims", {"reversed_on_appeal": 1.0}, n=8)
    for o in outs:
        r = o.result
        assert r.outcome_label == "fail"
        assert r.metadata.get("estimated_success") is True, "the claimed side must look correct"
        assert all(e.passed for e in r.evals), "every quality check must still pass"
        assert not any(t.error for t in r.traces), "no trace-side defect may be present"
        assert r.metadata.get("reversal_days", 0) > 0


def test_agent_paralysis_emits_nothing_to_find():
    """Absence is the signal. If this ever grows an error span it has stopped being paralysis."""
    outs = _run_with("support", {"agent_paralysis": 1.0}, n=5)
    for o in outs:
        assert not any(t.error for t in o.result.traces)
        assert o.result.outcome_label == "fail"


def test_ok_but_empty_is_not_tool_fault_empty():
    """The distinction the whole lever exists for: a 200 with a hollow body reads as a healthy call
    to anything checking status, where `tool_fault:empty` sets an error and is trivially detectable.
    """
    ok = _run_with("support", {"ok_but_empty": 1.0}, n=3)
    for o in ok:
        hits = [t for t in o.result.traces if (t.tool_output or {}).get("status") == 200
                and (t.tool_output or {}).get("count") == 0]
        assert hits, "expected a 200 response carrying an empty payload"
        assert all(t.error is None for t in hits), "a silent empty must carry no error"
        assert all(t.outcome == "ok" for t in hits), "a silent empty must report ok"


def test_parametric_override_keeps_the_right_answer_in_the_trace():
    """The correct answer is retrieved, scored high and fresh, and ignored. Blaming the retriever
    here would be wrong, and the evidence to see that is present."""
    outs = _run_with("support", {"parametric_override": 1.0}, n=3)
    for o in outs:
        good = [t for t in o.result.traces
                if any(r.get("match_score", 0) > 0.9 for r in (t.tool_output or {}).get("results", []))]
        assert good, "the correct retrieval must be visible in the trace"
        assert all(t.error is None and t.outcome == "ok" for t in good)
