"""The claims pack spends believable, per-agent money; the per-agent breakdown a session closes with
reconciles to its total; and `agent_overspend` moves one agent's share of that spend (argus#845)."""
from collections import defaultdict

from engine.levers import LeverConfig
from engine.runner import BatchRunner
from packs import get_pack

AGENTS = {"intake", "validator", "adjudicator", "reviewer"}


def _runs(rates, n=30, seed=7):
    pack = get_pack("claims")
    return BatchRunner(pack, LeverConfig(rates), emitter=None, ledger=None, seed=seed, start_index=0).run_batch(n)


def _spend(outs):
    by = defaultdict(float)
    for o in outs:
        for t in o.result.traces:
            by[t.agent] += t.cost_usd or 0.0
    return by


def test_every_claims_agent_spends_and_each_session_is_a_fraction_of_a_cent_to_a_cent():
    outs = _runs({})
    by = _spend(outs)
    assert set(by) == AGENTS and all(v > 0 for v in by.values())
    for o in outs:
        total = sum(t.cost_usd or 0.0 for t in o.result.traces)
        assert 0.001 < total < 0.02
    # The adjudicator reads and writes the most, intake the least.
    assert by["adjudicator"] > by["reviewer"] > by["intake"]


def test_same_seed_same_money():
    a = [[(t.agent, t.tokens_input, t.tokens_output, t.cost_usd) for t in o.result.traces] for o in _runs({}, 10, 3)]
    b = [[(t.agent, t.tokens_input, t.tokens_output, t.cost_usd) for t in o.result.traces] for o in _runs({}, 10, 3)]
    assert a == b


def test_agent_overspend_moves_one_agents_share_without_changing_outcomes():
    base = _spend(_runs({}))
    hot = _runs({"agent_overspend": {"rate": 1.0, "target": "reviewer", "params": {"multiplier": 8}}})
    for o in hot:
        assert any(f.lever == "agent_overspend" and f.agent == "reviewer" for f in o.result.faults)
        assert o.result.outcome_label == "success" and o.result.diverged() is False
    after = _spend(hot)
    share = lambda d, a: d[a] / sum(d.values())
    assert share(base, "reviewer") < 0.30 and share(after, "reviewer") > 0.55
    assert round(after["reviewer"] / base["reviewer"]) == 8


def test_breakdown_at_close_reconciles_per_agent_and_in_total():
    from tests.test_cost_breakdown_close import _close_body
    for o in _runs({"agent_overspend": {"rate": 0.5, "target": "adjudicator"}}, 12, 5):
        body = _close_body(o.result.traces)
        bd = body["metadata"]["cost_breakdown"]
        assert set(bd) == AGENTS
        assert abs(sum(v["cost_usd"] for v in bd.values()) - body["total_cost_usd"]) < 1e-5
        assert all(v["model"] and v["input"] > 0 and v["output"] > 0 for v in bd.values())
