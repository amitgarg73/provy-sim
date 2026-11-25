"""The close payload carries the per-agent cost breakdown, reconciled to total_cost_usd (argus#845)."""
from engine.emitter import ProvyEmitter  # noqa: F401
from engine.types import TraceStep, RunResult


def _close_body(steps, metadata=None):
    from engine import emitter as em
    captured = {}

    class E(em.ProvyEmitter):
        def __init__(self): pass
        def _post(self, path, body): captured.update(body); return {}

    r = RunResult.__new__(RunResult)
    r.traces = steps; r.session_id = "s"; r.terminal_reason = "done"; r.metadata = metadata or {}
    r.confidence = 0.9; r.estimated_signals = {}
    E().close_session(r)
    return captured


def test_breakdown_sums_per_agent_and_reconciles():
    body = _close_body([TraceStep(agent="a", step_type="agent_message", cost_usd=0.01, tokens_input=10, tokens_output=5, model="m"),
                        TraceStep(agent="a", step_type="tool_call", cost_usd=0.02),
                        TraceStep(agent="b", step_type="agent_message", cost_usd=0.03),
                        TraceStep(agent="c", step_type="skip")])
    bd = body["metadata"]["cost_breakdown"]
    assert set(bd) == {"a", "b"}
    assert round(sum(v["cost_usd"] for v in bd.values()), 6) == body["total_cost_usd"]
    assert bd["a"]["model"] == "m"


def test_no_cost_no_breakdown_and_pack_breakdown_wins():
    assert "cost_breakdown" not in _close_body([TraceStep(agent="a", step_type="skip")])["metadata"]
    own = {"x": {"cost_usd": 1.0, "input": 1, "output": 1}}
    assert _close_body([TraceStep(agent="a", step_type="agent_message", cost_usd=0.5)], {"cost_breakdown": own})["metadata"]["cost_breakdown"] == own
