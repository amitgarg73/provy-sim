"""Full pipeline dry-run: the emitter must build trace/eval/close/outcome
payloads and send NOTHING (PROVY_EMIT unset), and ground truth is recorded."""
import json
import os

from engine.emitter import ProvyEmitter
from engine.groundtruth import GroundTruthLedger
from engine.levers import LeverConfig
from engine.reconcile import reconcile_pending
from engine.runner import BatchRunner
from packs import get_pack


def test_emitter_is_noop_without_emit(monkeypatch):
    monkeypatch.delenv("PROVY_EMIT", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    em = ProvyEmitter(ingest_key="provy_fake", base_url="https://provyai.vercel.app")
    assert em.enabled is False
    assert em.base == "https://provyai.vercel.app"
    assert em.key == "provy_fake"


def test_dry_run_builds_all_payloads(tmp_path, monkeypatch):
    monkeypatch.delenv("PROVY_EMIT", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    pack = get_pack("support")
    ledger = GroundTruthLedger(str(tmp_path / "gt.jsonl"))
    em = ProvyEmitter(ingest_key="provy_fake", is_simulated=False)
    runner = BatchRunner(pack, LeverConfig({"silent_wrong": 0.5}), emitter=em,
                         ledger=ledger, seed=1)
    outs = runner.run_batch(6)
    assert len(outs) == 6

    paths = [c["path"] for c in em.sent]
    assert "/api/ingest/session/open" in paths
    assert "/api/ingest/trace" in paths
    assert "/api/ingest/eval" in paths
    assert "/api/ingest/session/close" in paths

    # session/open must carry is_simulated=false and the x-provy-key contract
    opens = [c for c in em.sent if c["path"] == "/api/ingest/session/open"]
    assert all(c["payload"]["is_simulated"] is False for c in opens)

    # ground truth recorded for every run
    records = ledger.read("support")
    assert len(records) == 6
    assert all("faults" in r for r in records)


def test_dry_run_outcome_posts_label_and_signals(tmp_path, monkeypatch):
    monkeypatch.delenv("PROVY_EMIT", raising=False)
    pack = get_pack("support")
    ledger = GroundTruthLedger(str(tmp_path / "gt.jsonl"))
    em = ProvyEmitter(ingest_key="provy_fake", is_simulated=False)
    runner = BatchRunner(pack, LeverConfig({"silent_wrong": 1.0}), emitter=em,
                         ledger=ledger, seed=2)
    runner.run_batch(4)

    res = reconcile_pending(ledger, em, workflow="support")
    assert res["posted"] == 4
    outcome_calls = [c for c in em.sent if c["path"] == "/api/ingest/outcome"]
    assert len(outcome_calls) == 4
    for c in outcome_calls:
        p = c["payload"]
        # BOTH label/value (ledger) and the signals bag (contract) are posted
        assert p["label"] in ("success", "fail")
        assert "value" in p
        assert isinstance(p["signals"], dict) and p["signals"], "signals bag must be present"
        assert p["entity_id"].startswith("TKT-")


def test_no_network_calls_in_dry_run(tmp_path, monkeypatch):
    """urlopen must never be invoked when emission is off."""
    monkeypatch.delenv("PROVY_EMIT", raising=False)
    called = {"n": 0}
    import engine.emitter as emmod

    def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("network call attempted during dry run")

    monkeypatch.setattr(emmod.urllib.request, "urlopen", boom)
    pack = get_pack("claims")
    ledger = GroundTruthLedger(str(tmp_path / "gt.jsonl"))
    em = ProvyEmitter(ingest_key="provy_fake")
    runner = BatchRunner(pack, LeverConfig({"silent_wrong": 0.5}), emitter=em, ledger=ledger, seed=1)
    runner.run_batch(5)
    reconcile_pending(ledger, em, workflow="claims")
    assert called["n"] == 0
