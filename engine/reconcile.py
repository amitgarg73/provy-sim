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


def reconcile_pending(ledger: GroundTruthLedger, emitter: ProvyEmitter,
                      workflow: Optional[str] = None, mark: bool = True) -> dict:
    pending = ledger.pending_outcomes(workflow)
    posted = 0
    for rec in pending:
        r = _minimal_result(rec)
        occurred = (rec.get("outcome_post", {}) or {}).get("occurred_at")
        emitter.outcome(r, occurred_at=occurred)
        rec["reconciled"] = True
        posted += 1
    if mark and posted:
        _rewrite(ledger)
    return {"pending": len(pending), "posted": posted, "emit_enabled": emitter.enabled}


def _rewrite(ledger: GroundTruthLedger) -> None:
    """Rewrite the JSONL with reconciled flags flipped. Small-scale ledger; a
    full rewrite is fine here and keeps the file the single source of truth."""
    rows = ledger.read()
    tmp = ledger.path + ".tmp"
    with open(tmp, "w") as f:
        for rec in rows:
            f.write(json.dumps(rec, default=str) + "\n")
    os.replace(tmp, ledger.path)


def backfill_server_judge(base_url: str, key: str) -> dict:
    """Trigger Provy's server judge for the fleet's most recent closed sessions
    (idempotent server-side). Best-effort; never raises."""
    if not (base_url and key):
        return {"skipped": True}
    if os.environ.get("PROVY_EMIT", "").strip().lower() not in ("1", "true", "yes", "on") \
            and os.environ.get("GITHUB_ACTIONS", "").strip().lower() != "true":
        return {"skipped": "emit off"}
    try:
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/api/compute/judge",
            data=json.dumps({}).encode(),
            headers={"Content-Type": "application/json", "x-provy-key": key},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=120)
        return {"ok": True, "at": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        return {"error": str(e)}
