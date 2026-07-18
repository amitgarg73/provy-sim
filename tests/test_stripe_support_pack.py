"""Commitment-integrity support pack: the promise-vs-settlement divergence.

The agent always claims success (an OK refund receipt). Reality, read from the
mock system of record, may silently disagree. These tests pin that behaviour:
a clean run keeps the promise; each mock-Stripe injector breaks exactly one Real
signal while the Estimated claim stays good; the resolver is always the culprit.
"""
import random

from engine.contract import grade
from engine.levers import LeverConfig
from engine.mock_sor import MockStripe
from engine.reconcile import reconcile_pending
from engine.emitter import ProvyEmitter
from engine.groundtruth import GroundTruthLedger
from engine.runner import BatchRunner
from conftest import make_ctx
from packs import get_pack


def _run(levers, seed=1):
    pack = get_pack("stripe_support")
    ctx = make_ctx(levers=LeverConfig(levers or {}), seed=seed)
    item, gt = pack.generate_work_item(random.Random(seed))
    return pack, pack.run_pipeline(item, gt, ctx), item


# ── the mock system of record ────────────────────────────────────────────────

def test_receipt_always_ok_even_when_it_will_not_settle():
    """The agent's receipt looks like success regardless of the settled fate."""
    sor = MockStripe(random.Random(0), {"unsettled_insufficient": 1.0})
    receipt = sor.refund("ORD-1", 40.0)
    assert receipt.ok is True                     # the agent believes it worked
    st = sor.settlement("ORD-1")
    assert st.settled is False                    # reality: it never cleared
    assert st.injector == "unsettled_insufficient"


def test_clean_settlement_when_no_injector():
    sor = MockStripe(random.Random(0), {})        # no injectors configured
    sor.refund("ORD-2", 40.0)
    st = sor.settlement("ORD-2")
    assert st.settled is True and st.injector is None and st.reason == "cleared"


# ── the pack ──────────────────────────────────────────────────────────────────

def test_clean_run_keeps_the_promise():
    pack, r, _ = _run({})                          # no failures configured
    assert r.faults == []
    assert r.outcome_label == "success"
    g = grade(pack.contract(), r.estimated_signals, r.real_signals)
    assert g["met"] == g["total"]                  # every condition met
    assert r.real_signals["refund_settled"] is True


def test_unsettled_refund_diverges_and_blames_the_resolver():
    pack, r, _ = _run({"unsettled_insufficient": 1.0})
    # The claim stayed good; reality did not.
    assert r.estimated_signals["refund_settled"] is True
    assert r.real_signals["refund_settled"] is False
    assert r.outcome_label == "fail" and r.diverged() is True
    # One fault, attributed to the agent that made the promise.
    assert len(r.faults) == 1
    f = r.faults[0]
    assert f.lever == "commitment_unsettled" and f.agent == "resolver"
    # The 'both' promise condition is graded as diverged.
    g = grade(pack.contract(), r.estimated_signals, r.real_signals)
    c1 = next(c for c in g["per_condition"] if c["id"] == "c1")
    assert c1["estimated_met"] is True and c1["real_met"] is False and c1["diverged"] is True


def test_bank_return_also_unsettles():
    _, r, _ = _run({"unsettled_bank_return": 1.0})
    assert r.real_signals["refund_settled"] is False
    assert r.faults[0].lever == "commitment_unsettled"
    assert r.faults[0].params["reason"] == "bank_returned"


def test_wrong_amount_breaks_only_the_amount_signal():
    _, r, _ = _run({"wrong_amount": 1.0})
    assert r.real_signals["amount_correct"] is False
    assert r.real_signals["refund_settled"] is True      # it settled, just wrong
    assert r.faults[0].lever == "commitment_wrong_amount"
    assert r.faults[0].params["settled_amount"] != r.faults[0].params["promised_amount"]


def test_duplicate_breaks_only_the_duplicate_signal():
    _, r, _ = _run({"duplicate": 1.0})
    assert r.real_signals["no_duplicate"] is False
    assert r.real_signals["refund_settled"] is True
    assert r.faults[0].lever == "commitment_duplicate"


def test_every_step_is_self_explaining():
    """Each trace step carries a plain-language narration for the demo."""
    _, r, _ = _run({"unsettled_insufficient": 1.0})
    narrated = [t for t in r.traces if t.payload_extra.get("narration")]
    assert len(narrated) >= 4                        # triage, order check, action, settlement
    settle = next(t for t in r.traces if t.tool_name == "stripe.settlement")
    assert "reality disagrees" in settle.payload_extra["narration"]


# ── superset: the generic + L1/L2 levers now run on this fleet too ─────────────

def test_generic_silent_lever_runs_on_the_stripe_fleet():
    """The fleet is a superset now: a generic silent lever fires here just like on the other
    packs, corrupting the promise on the Real side while the claim stays good."""
    pack, r, _ = _run({"silent_wrong": 1.0})
    levers = [f.lever for f in r.faults]
    assert levers == ["silent_wrong"]                 # the settlement feed stood down
    assert r.faults[0].agent == "resolver"
    assert r.estimated_signals["refund_settled"] is True
    assert r.real_signals["refund_settled"] is False
    assert r.diverged() is True and all(e.passed for e in r.evals)


def test_settlement_and_a_generic_lever_are_mutually_exclusive():
    """One primary cause per run: when a generic phase-A lever fires, the settlement feed does
    not stack a commitment fault on the same run, so attribution stays 1:1."""
    _, r, _ = _run({"silent_wrong": 1.0, "unsettled_insufficient": 1.0})
    levers = [f.lever for f in r.faults]
    assert levers == ["silent_wrong"], levers
    assert not any(lv.startswith("commitment_") for lv in levers)


def test_l1_l2_overlays_run_on_the_stripe_fleet():
    """Tool-latency and LLM-cost overlays layer on a Stripe run without breaking the promise."""
    _, r, _ = _run({"tool_latency": {"rate": 1.0, "params": {"latency_ms": 12000}},
                    "llm_cost": {"rate": 1.0, "params": {"cost_usd": 0.4}}})
    levers = {f.lever for f in r.faults}
    assert {"tool_latency", "llm_cost"} <= levers
    assert r.outcome_label == "success" and r.diverged() is False   # the refund still settled
    assert any(t.step_type == "tool_call" and t.latency_ms >= 12000 for t in r.traces)
    assert any(t.step_type == "agent_message" and t.cost_usd >= 0.4 for t in r.traces)


def test_outcome_post_carries_fail_label_and_signals(tmp_path, monkeypatch):
    """A diverged run posts label=fail plus the settled signals to the one door."""
    monkeypatch.delenv("PROVY_EMIT", raising=False)
    pack = get_pack("stripe_support")
    ledger = GroundTruthLedger(str(tmp_path / "gt.jsonl"))
    em = ProvyEmitter(ingest_key="provy_fake", is_simulated=False)
    runner = BatchRunner(pack, LeverConfig({"unsettled_insufficient": 1.0}),
                         emitter=em, ledger=ledger, seed=3)
    runner.run_batch(4)
    res = reconcile_pending(ledger, em, workflow="stripe_support")
    assert res["posted"] == 4
    posts = [c["payload"] for c in em.sent if c["path"] == "/api/ingest/outcome"]
    assert len(posts) == 4
    for p in posts:
        assert p["label"] == "fail"
        assert p["signals"]["refund_settled"] is False
        assert p["entity_id"].startswith("TKT-")
