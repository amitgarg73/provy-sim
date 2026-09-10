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


def test_request_headers_adds_vercel_bypass_only_when_set(monkeypatch):
    from engine.emitter import request_headers
    monkeypatch.delenv("VERCEL_PROTECTION_BYPASS", raising=False)
    h = request_headers("provy_k")
    assert h["x-provy-key"] == "provy_k" and "x-vercel-protection-bypass" not in h
    monkeypatch.setenv("VERCEL_PROTECTION_BYPASS", "tok123")
    h = request_headers("provy_k")
    assert h["x-vercel-protection-bypass"] == "tok123"
    # Never ask for the bypass cookie. Vercel answers that request with a 307 redirect instead of
    # running the route, which silently turned every emit into a no-op: the run went green and not
    # one session landed. Verified against provydev — token alone 200, token plus this header 307.
    assert "x-vercel-set-bypass-cookie" not in h


# ── the ledger's reconciled flag (argus#812) ────────────────────────────────

class _StubEmitter:
    """Posts nothing. `enabled = False` is the dry-run path, which reconcile_pending counts as
    settled, so these tests exercise the ledger bookkeeping without a network."""
    enabled = False
    base = "https://dev.provy.ai"
    key = ""

    def __init__(self):
        self.posts = []

    def outcome(self, r, occurred_at=None):
        self.posts.append(r.session_id)
        return {"skipped": True}


def _ledger(tmp_path, n=3):
    from engine.groundtruth import GroundTruthLedger
    led = GroundTruthLedger(str(tmp_path / "gt.jsonl"))
    for i in range(n):
        led.append({"workflow": "demo", "session_id": f"s{i}", "entity_id": f"E{i}",
                    "outcome_label": "success", "outcome_value": 1.0,
                    "real_signals": {"x": True},
                    "outcome_post": {"entity_id": f"E{i}", "session_id": f"s{i}",
                                     "label": "success", "value": 1.0,
                                     "signals": {"x": True}, "occurred_at": None}})
    return led


def test_a_settled_outcome_is_actually_marked_in_the_file(tmp_path):
    """⛔ IT REPORTED `posted: N` AND WROTE NOTHING, FOR EVERY PACK, FOREVER.

    `pending_outcomes` re-parses the file and returns fresh dicts; `_rewrite` re-parsed it again. So
    the flag was set on objects nobody wrote and the ledger was rewritten from disk unchanged.
    Measured 10 Sep 2026 across all 13 ledgers: 674 rows, not one ever marked reconciled."""
    led = _ledger(tmp_path)
    out = R.reconcile_pending(led, _StubEmitter(), workflow="demo")
    assert out["posted"] == 3
    assert led.pending_outcomes("demo") == [], "the ledger still says every outcome is pending"
    assert all(r["reconciled"] for r in led.read())


def test_it_does_not_post_the_same_outcome_twice(tmp_path):
    """The consequence of the flag never sticking: every re-run re-posted every outcome the pack had
    ever produced. teameight would have re-sent 271."""
    led = _ledger(tmp_path)
    R.reconcile_pending(led, _StubEmitter(), workflow="demo")
    second = _StubEmitter()
    out = R.reconcile_pending(led, second, workflow="demo")
    assert second.posts == []
    assert out == {"pending": 0, "posted": 0, "unmatched": 0, "errors": 0, "emit_enabled": False}


def test_a_row_appended_while_posting_is_not_lost(tmp_path):
    """⛔ THE REWRITE REPLACES THE FILE, AND `--reconcile-every` APPENDS WHILE IT RUNS. Marking the
    caller's own dicts and writing those back would drop anything the batch added in between, which
    is why the flags are applied against a fresh read instead."""
    led = _ledger(tmp_path, n=2)
    late = {"workflow": "demo", "session_id": "s-late", "entity_id": "E-late",
            "outcome_label": "success", "outcome_value": 1.0, "real_signals": {},
            "outcome_post": {"entity_id": "E-late", "session_id": "s-late", "label": "success",
                             "value": 1.0, "signals": {}, "occurred_at": None}}

    class _AppendingEmitter(_StubEmitter):
        def outcome(self, r, occurred_at=None):
            if r.session_id == "s1":
                led.append(late)          # the batch writes its next chunk mid-post
            return super().outcome(r, occurred_at=occurred_at)

    R.reconcile_pending(led, _AppendingEmitter(), workflow="demo")
    rows = led.read()
    assert [r["session_id"] for r in rows] == ["s0", "s1", "s-late"]
    assert [r["session_id"] for r in led.pending_outcomes("demo")] == ["s-late"]


def test_an_outcome_that_did_not_settle_stays_pending(tmp_path):
    """A post that errored, or came back unmatched, must remain in the ledger to be retried."""
    led = _ledger(tmp_path, n=2)

    class _FailingEmitter(_StubEmitter):
        enabled = True
        def outcome(self, r, occurred_at=None):
            self.posts.append(r.session_id)
            return {"error": "boom"} if r.session_id == "s1" else {"reconciliation": "matched"}

    out = R.reconcile_pending(led, _FailingEmitter(), workflow="demo", retries=0)
    assert out["posted"] == 1 and out["errors"] == 1
    assert [r["session_id"] for r in led.pending_outcomes("demo")] == ["s1"]


def test_an_outcome_is_dated_by_when_the_work_ran(tmp_path):
    """⛔ `occurred_at` IS NULL ON EVERY LEDGER ROW WRITTEN TO DATE and the emitter defaults a null
    to the wall clock. Same-day that is minutes out and invisible; on a backfill it stamps
    fortnight-old work as today."""
    led = _ledger(tmp_path, n=1)
    rows = led.read()
    rows[0]["ts"] = "2026-08-25T12:39:04.724057+00:00"
    with open(led.path, "w") as f:
        import json as _j
        f.write(_j.dumps(rows[0]) + "\n")

    seen = []

    class _DatingEmitter(_StubEmitter):
        def outcome(self, r, occurred_at=None):
            seen.append(occurred_at)
            return {"skipped": True}

    R.reconcile_pending(led, _DatingEmitter(), workflow="demo")
    assert seen == ["2026-08-25T12:39:04.724057+00:00"]
