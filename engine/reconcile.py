"""EOD reconcile: post the day's real outcomes (with the signals bag) to the
ONE reconciliation door, POST /api/ingest/outcome.

Reads pending records from the ground-truth ledger, posts each outcome, and
(optionally) rewrites the ledger marking them reconciled. Also triggers the
server-judge backfill so sessions that never got an L4 pass are scored — same
pattern as trading-agent-c/evals/outcomes.backfill_server_judge.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from .emitter import ProvyEmitter
from .groundtruth import GroundTruthLedger
from .types import RunResult


def _minimal_result(rec: dict) -> RunResult:
    """Rebuild the fields the emitter.outcome() call needs from a ledger record."""
    post = rec.get("outcome_post", {})
    r = RunResult(
        entity_id=rec["entity_id"],
        session_type="reconcile",
        session_id=rec["session_id"],
        outcome_label=rec.get("outcome_label", "success"),
        outcome_value=rec.get("outcome_value"),
        real_signals=post.get("signals", rec.get("real_signals", {})),
    )
    return r


def _matched(resp: dict) -> bool:
    """True when /api/ingest/outcome reconciled the post against a prediction."""
    if not isinstance(resp, dict):
        return False
    if resp.get("reconciliation") in ("matched", "diverged"):
        return True
    return isinstance(resp.get("reconciled"), int) and resp["reconciled"] >= 1


def reconcile_pending(ledger: GroundTruthLedger, emitter: ProvyEmitter,
                      workflow: Optional[str] = None, mark: bool = True,
                      retries: int = 5, backoff: float = 20.0) -> dict:
    """Post the day's real outcomes and reconcile them against the trace-based predictions.

    A prediction is written by the server judge; an outcome posted before its prediction is visible
    comes back unmatched. So post all, then retry only the unmatched ones after a short wait, up to a
    budget. Predictions land within a minute in practice; the tick's 30-minute cadence covers any tail.
    """
    pending = ledger.pending_outcomes(workflow)
    remaining = list(pending)
    posted = 0
    # ⛔ THE SESSION IDS THAT SETTLED, NOT THE DICTS THEY CAME IN (argus#812).
    #
    # `pending_outcomes` re-parses the file and hands back FRESH dicts, and `_rewrite` used to
    # re-parse it AGAIN. So `rec["reconciled"] = True` was set on objects nothing ever wrote, the
    # ledger was rewritten from disk with every row still pending, and the call returned
    # `posted: N` over it. Measured 10 Sep 2026: 674 rows across all 13 packs, not one of them ever
    # marked reconciled, and every re-run re-posting every outcome the pack had ever produced.
    settled: set[str] = set()
    errors: list[str] = []
    for attempt in range(retries + 1):
        still: list[dict] = []
        for rec in remaining:
            r = _minimal_result(rec)
            # ⛔ FALL BACK TO WHEN THE WORK RAN, NEVER TO NOW (argus#812). `occurred_at` is null on
            # every one of the 674 ledger rows written to date, and the emitter defaults a null to
            # the wall clock. In a same-day batch those are minutes apart and it never showed; on a
            # backfill it dates a fortnight-old work item as today, which is the one thing every
            # customer surface is supposed to be protected from. `ts` is when the run finished, so
            # it is right in both cases.
            post = rec.get("outcome_post", {}) or {}
            occurred = post.get("occurred_at") or rec.get("ts")
            resp = emitter.outcome(r, occurred_at=occurred)
            if isinstance(resp, dict) and resp.get("error"):
                errors.append(f"{r.entity_id}: {resp['error']}")
                still.append(rec)
            elif (not emitter.enabled) or (isinstance(resp, dict) and resp.get("skipped")) or _matched(resp):
                # Dry run (nothing sent) counts as posted; otherwise it must have reconciled.
                rec["reconciled"] = True
                settled.add(rec["session_id"])
                posted += 1
            else:
                still.append(rec)  # prediction not visible yet — retry after a wait
        remaining = still
        if not remaining:
            break
        if attempt < retries:
            time.sleep(backoff)
    if mark and settled:
        _rewrite(ledger, settled)
    out = {"pending": len(pending), "posted": posted, "unmatched": len(remaining),
           "errors": len(errors), "emit_enabled": emitter.enabled}
    if errors:
        out["error_detail"] = errors[:5]
    return out


def _rewrite(ledger: GroundTruthLedger, settled: set[str]) -> None:
    """Rewrite the JSONL with reconciled flags flipped on the sessions that settled.

    ⛔ THE FLAGS ARE APPLIED HERE, AGAINST A FRESH READ, AND THAT IS DELIBERATE. Marking the caller's
    own dicts and writing those back would lose any row the batch appended while this was posting,
    which is exactly what `--reconcile-every` does. Reading again and marking by session id keeps
    both: late rows survive and the settled ones are recorded."""
    rows = ledger.read()
    for rec in rows:
        if rec.get("session_id") in settled:
            rec["reconciled"] = True
    tmp = ledger.path + ".tmp"
    with open(tmp, "w") as f:
        for rec in rows:
            f.write(json.dumps(rec, default=str) + "\n")
    os.replace(tmp, ledger.path)


def _post_judge(base_url: str, key: str, payload: dict) -> dict:
    from .emitter import request_headers
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/compute/judge",
        data=json.dumps(payload).encode(),
        headers=request_headers(key),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        try:
            return json.loads(resp.read().decode() or "{}")
        except Exception:
            return {}


def backfill_server_judge(base_url: str, key: str,
                          session_ids: list[str] | None = None, chunk: int = 25) -> dict:
    """Trigger Provy's server judge. With session_ids, judge EXACTLY those (the batch just emitted),
    chunked to stay under the request timeout, so every session gets a prediction before reconcile —
    Provy's default judge only covers the most-recent 20, which silently drops the tail of a larger
    batch. Without session_ids, fall back to that bounded default. Idempotent server-side; best-effort,
    never raises."""
    if not (base_url and key):
        return {"skipped": True}
    if os.environ.get("PROVY_EMIT", "").strip().lower() not in ("1", "true", "yes", "on") \
            and os.environ.get("GITHUB_ACTIONS", "").strip().lower() != "true":
        return {"skipped": "emit off"}
    try:
        if session_ids:
            totals = {"sessions": 0, "evals_written": 0, "predictions_written": 0}
            step = max(1, chunk)
            for i in range(0, len(session_ids), step):
                r = _post_judge(base_url, key, {"session_ids": session_ids[i:i + step]})
                for k in totals:
                    totals[k] += int(r.get(k) or 0)
            return {"ok": True, **totals, "at": datetime.now(timezone.utc).isoformat()}
        _post_judge(base_url, key, {})
        return {"ok": True, "at": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        return {"error": str(e)}
