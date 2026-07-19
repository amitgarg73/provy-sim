"""backfill_server_judge must name a batch's sessions and chunk them, so every session gets a
prediction before reconcile (Provy's default judge only covers the most-recent 20)."""
import engine.reconcile as R


def test_backfill_chunks_named_session_ids(monkeypatch):
    monkeypatch.setenv("PROVY_EMIT", "1")
    calls = []

    def fake_post(base, key, payload):
        calls.append(payload)
        n = len(payload.get("session_ids", []))
        return {"sessions": n, "evals_written": 2 * n, "predictions_written": n}

    monkeypatch.setattr(R, "_post_judge", fake_post)
    sids = [f"sim-x-{i}" for i in range(30)]
    res = R.backfill_server_judge("https://x", "provy_k", session_ids=sids, chunk=25)

    assert [len(c["session_ids"]) for c in calls] == [25, 5]     # chunked
    assert res["ok"] is True
    assert res["sessions"] == 30                                 # totals summed across chunks
    assert res["predictions_written"] == 30


def test_backfill_without_ids_uses_the_bounded_default(monkeypatch):
    monkeypatch.setenv("PROVY_EMIT", "1")
    calls = []
    monkeypatch.setattr(R, "_post_judge", lambda b, k, p: calls.append(p) or {})
    res = R.backfill_server_judge("https://x", "provy_k")
    assert calls == [{}]                                        # empty body -> server's most-recent judge
    assert res["ok"] is True


def test_backfill_skips_when_emit_off(monkeypatch):
    monkeypatch.delenv("PROVY_EMIT", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    res = R.backfill_server_judge("https://x", "provy_k", session_ids=["a"])
    assert res == {"skipped": "emit off"}
