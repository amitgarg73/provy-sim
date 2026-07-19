"""The commitment-integrity packs (Travel, RevOps, Claims payout, Legal).

Each is built on the shared engine (engine/commitment.py): the agent makes an outward
commitment and reports success; the settled system of record may silently disagree.
These tests pin, across all four packs, that a clean run keeps the promise, each
injector breaks exactly one Real signal while the Estimated claim stays good, the
commitment agent is always the culprit, and each pack is a superset that also runs the
generic + L1/L2 levers.
"""
import random

import pytest

from engine.commitment import SHAPE_FAULT, CommitmentPack, MockSoR
from engine.contract import grade
from engine.emitter import ProvyEmitter
from engine.groundtruth import GroundTruthLedger
from engine.levers import LeverConfig
from engine.reconcile import reconcile_pending
from engine.runner import BatchRunner
from packs import get_pack

CI_PACKS = ["travel", "revops", "claims_payout", "legal"]


def _one(pack_name, levers, seed=1):
    pack = get_pack(pack_name)
    r = BatchRunner(pack, LeverConfig(levers or {}), seed=seed).run_batch(1)[0].result
    return pack, r


# ── the shared mock system of record ──────────────────────────────────────────

def test_mock_sor_is_deterministic_and_one_cause_per_ref():
    from engine.commitment import Injector
    injs = [Injector("a", "unsettled", "r_a", "a broke"),
            Injector("b", "duplicate", "r_b", "b broke")]
    sor = MockSoR(random.Random(0), {"a": 1.0, "b": 1.0}, injs)
    sor.commit("REF-1", 100.0)
    st = sor.settlement("REF-1")
    assert st.injector == "a"          # rolled in order, first hit wins
    assert st.settled is False and st.shape == "unsettled"


def test_mock_sor_clean_when_no_injector():
    sor = MockSoR(random.Random(0), {}, [])
    sor.commit("REF-2", 40.0)
    st = sor.settlement("REF-2")
    assert st.settled is True and st.injector is None and st.reason == "cleared"


# ── the packs ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pack_name", CI_PACKS)
def test_is_a_commitment_pack_with_a_both_promise(pack_name):
    pack, r = _one(pack_name, {})
    assert isinstance(pack, CommitmentPack)
    promise = pack.settle_map()["promise"]
    c1 = next(c for c in pack.contract() if c.signal == promise)
    assert c1.side == "both", "the promise condition must be graded on both the claim and reality"


@pytest.mark.parametrize("pack_name", CI_PACKS)
def test_clean_run_keeps_the_promise(pack_name):
    pack, r = _one(pack_name, {})
    assert r.faults == [] and r.outcome_label == "success"
    g = grade(pack.contract(), r.estimated_signals, r.real_signals)
    assert g["met"] == g["total"]
    promise = pack.settle_map()["promise"]
    assert r.real_signals[promise] is True
    # A clean run must still emit the settlement-check trace.
    assert any(t.tool_name == pack.settle_tool for t in r.traces)


@pytest.mark.parametrize("pack_name", CI_PACKS)
def test_each_injector_diverges_with_a_grounded_culprit(pack_name):
    from engine.contract import bad_value
    pack = get_pack(pack_name)
    m = pack.lever_manifest()
    for inj in pack.injectors():
        # run several so different (RNG-picked) causes surface for the same injector.
        for o in BatchRunner(pack, LeverConfig({inj.name: 1.0}), seed=7).run_batch(12):
            r = o.result
            assert len(r.faults) == 1, f"{pack_name}/{inj.name}: {[f.lever for f in r.faults]}"
            f = r.faults[0]
            assert f.lever == SHAPE_FAULT[inj.shape]
            assert r.diverged() is True and all(e.passed for e in r.evals)   # silent divergence
            # exactly the mapped signal went bad on the Real side.
            sig = pack.settle_map()["promise"] if inj.shape == "unsettled" else pack.settle_map()[inj.shape]
            c = next(c for c in pack.contract() if c.signal == sig)
            assert r.real_signals[sig] == bad_value(c)
            # the true culprit is grounded in a footprint Provy can resolve, or an honest blind spot.
            tool = next((t for t in r.traces if t.agent == m.retriever_agent and t.step_type == "tool_call"), None)
            out = (tool.tool_output if tool else {}) or {}
            cause = f.params.get("cause")
            if cause == "retriever_stale":
                assert f.agent == m.retriever_agent and out.get("note") == "stale index"
            elif cause == "retriever_fallback":
                assert f.agent == m.retriever_agent and out.get("from_cache") is True
            elif cause == "resolver_ignored":
                assert f.agent == m.resolver_agent and out.get("match_score") == 0.28
            else:
                assert cause == "blind_spot" and f.agent is None


@pytest.mark.parametrize("pack_name", CI_PACKS)
def test_culprit_varies_across_agents(pack_name):
    """The whole point of the multi-agent fix: over a batch the true culprit is spread across the
    retriever, the resolver, and honest blind spots — so attribution accuracy is a real signal, not
    a constant Provy could satisfy by always naming one agent."""
    pack = get_pack(pack_name)
    m = pack.lever_manifest()
    inj = pack.injectors()[0].name
    outs = BatchRunner(pack, LeverConfig({inj: 1.0}), seed=3).run_batch(80)
    culprits = {f.agent for o in outs for f in o.result.faults if f.lever.startswith("commitment_")}
    assert m.retriever_agent in culprits
    assert m.resolver_agent in culprits
    assert None in culprits          # blind spots occur — the sim does not fabricate a culprit


@pytest.mark.parametrize("pack_name", CI_PACKS)
def test_superset_generic_and_l1l2_levers_run(pack_name):
    # A generic silent lever fires here just like on the base packs, and the settlement feed
    # stands down (one primary cause per run).
    pack, r = _one(pack_name, {"silent_wrong": 1.0}, seed=8)
    assert [f.lever for f in r.faults] == ["silent_wrong"]
    assert r.diverged() is True
    # L1/L2 overlays layer on without breaking the promise.
    pack, r = _one(pack_name, {"tool_latency": {"rate": 1.0, "params": {"latency_ms": 12000}},
                               "llm_cost": {"rate": 1.0, "params": {"cost_usd": 0.4}}}, seed=9)
    levers = {f.lever for f in r.faults}
    assert {"tool_latency", "llm_cost"} <= levers
    assert r.outcome_label == "success" and r.diverged() is False
    assert any(t.step_type == "tool_call" and t.latency_ms >= 12000 for t in r.traces)
    assert any(t.step_type == "agent_message" and t.cost_usd >= 0.4 for t in r.traces)


@pytest.mark.parametrize("pack_name", CI_PACKS)
def test_settlement_and_a_generic_lever_are_mutually_exclusive(pack_name):
    pack = get_pack(pack_name)
    inj = pack.injectors()[0].name
    _, r = _one(pack_name, {"silent_wrong": 1.0, inj: 1.0}, seed=10)
    levers = [f.lever for f in r.faults]
    assert levers == ["silent_wrong"], levers
    assert not any(lv.startswith("commitment_") for lv in levers)


@pytest.mark.parametrize("pack_name", CI_PACKS)
def test_diverged_run_posts_fail_label_and_signals(pack_name, tmp_path, monkeypatch):
    monkeypatch.delenv("PROVY_EMIT", raising=False)
    pack = get_pack(pack_name)
    ledger = GroundTruthLedger(str(tmp_path / "gt.jsonl"))
    em = ProvyEmitter(ingest_key="provy_fake", is_simulated=False)
    inj = pack.injectors()[0].name   # an 'unsettled' injector on every pack
    runner = BatchRunner(pack, LeverConfig({inj: 1.0}), emitter=em, ledger=ledger, seed=3)
    runner.run_batch(4)
    res = reconcile_pending(ledger, em, workflow=pack_name)
    assert res["posted"] == 4
    posts = [c["payload"] for c in em.sent if c["path"] == "/api/ingest/outcome"]
    assert len(posts) == 4
    promise = pack.settle_map()["promise"]
    for p in posts:
        assert p["label"] == "fail"
        assert p["signals"][promise] is False
