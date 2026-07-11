"""Append-only ground-truth ledger (JSONL).

Every run's injected faults, the true outcome, and the outcome payload to post
later are recorded here so the scoreboard can score Provy's detection against
what the simulation actually did. The sim owns truth; this file is that truth.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Iterator, Optional

from .types import RunResult


def build_record(workflow: str, result: RunResult, session_index: int,
                 occurred_at: Optional[str] = None) -> dict:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "workflow": workflow,
        "session_index": session_index,
        "session_id": result.session_id,
        "entity_id": result.entity_id,
        "terminal_reason": result.terminal_reason,
        "outcome_label": result.outcome_label,
        "outcome_value": result.outcome_value,
        "confidence": result.confidence,
        "diverged": result.diverged(),
        "estimated_success": result.metadata.get("estimated_success"),
        "estimated_signals": result.estimated_signals,
        "real_signals": result.real_signals,
        "faults": [asdict(f) for f in result.faults],
        # what reconcile.py will post
        "outcome_post": {
            "entity_id": result.entity_id,
            "session_id": result.session_id,
            "label": "success" if result.outcome_label == "success" else "fail",
            "value": result.outcome_value,
            "signals": result.real_signals,
            "occurred_at": occurred_at,
        },
        "reconciled": False,
    }


class GroundTruthLedger:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def append(self, record: dict) -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def read(self, workflow: Optional[str] = None) -> list[dict]:
        return list(self.iter(workflow))

    def iter(self, workflow: Optional[str] = None) -> Iterator[dict]:
        if not os.path.exists(self.path):
            return
        with open(self.path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if workflow is None or rec.get("workflow") == workflow:
                    yield rec

    def pending_outcomes(self, workflow: Optional[str] = None) -> list[dict]:
        """Records whose outcome has not been posted (and that have a real outcome)."""
        out = []
        for rec in self.iter(workflow):
            if rec.get("reconciled"):
                continue
            if rec.get("outcome_label") == "skipped":
                continue
            out.append(rec)
        return out
