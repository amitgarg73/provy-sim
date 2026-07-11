"""Provy REST emitter — copied/adapted from trading-agent-c/trace/logger.py.

Raw REST over the x-provy-key header, no framework lock-in. Base URL is
Provy production (https://provyai.vercel.app), NOT the retired argusobs. Honors
the PROVY_EMIT gotcha: a no-op unless PROVY_EMIT is truthy (or GITHUB_ACTIONS
is true) AND a url + key are present. Every payload is also captured in memory
so a dry-run can inspect exactly what WOULD have been sent.

Emits, in order per run:
  session/open  (is_simulated=false — Provy's incident/pattern engines skip
                 is_simulated=true, and we want incidents to fire)
  trace         (tool_call L1, agent_message w/ reasoning+entity_id L3,
                 error / skip)
  eval          (layer 4, score 0..1, passed, entity_id, detail.reasoning)
  session/close (terminal_reason, metadata)
  outcome       (POST /api/ingest/outcome — entity_id, label/value, signals bag)
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

from .types import RunResult

DEFAULT_BASE_URL = "https://provyai.vercel.app"


def emit_enabled(url: str, key: str) -> bool:
    """Whether telemetry may be sent. Requires url+key AND an explicit opt-in
    (PROVY_EMIT truthy) or GitHub Actions. Default off so dev/test never writes
    to production Provy just because a .env carries prod credentials."""
    if not (url and key):
        return False
    if os.environ.get("PROVY_EMIT", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    if os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true":
        return True
    return False


class ProvyEmitter:
    def __init__(self, ingest_key: str | None = None, base_url: str | None = None,
                 is_simulated: bool = False, capture: bool = True):
        self.key = ingest_key if ingest_key is not None else os.environ.get("PROVY_KEY", "")
        self.base = (base_url or os.environ.get("PROVY_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.is_simulated = is_simulated
        self.capture = capture
        self.sent: list[dict] = []       # {path, method, payload} for every call built

    @property
    def enabled(self) -> bool:
        return emit_enabled(self.base, self.key)

    # ── low-level ────────────────────────────────────────────────────────────
    def _post(self, path: str, payload: dict) -> dict:
        if self.capture:
            self.sent.append({"path": path, "method": "POST", "payload": payload})
        if not self.enabled:
            return {"skipped": True}
        try:
            req = urllib.request.Request(
                f"{self.base}{path}",
                data=json.dumps(payload, default=str).encode(),
                headers={"Content-Type": "application/json", "x-provy-key": self.key},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=15)
            body = resp.read().decode()
            try:
                return json.loads(body)
            except Exception:
                return {"ok": True}
        except Exception as e:
            return {"error": str(e)}

    # ── high-level ───────────────────────────────────────────────────────────
    def open_session(self, result: RunResult) -> dict:
        # Do NOT send a separate external_id: Provy resolves later trace/eval/close/outcome calls by
        # matching external_id to the session id we send here. If external_id differed from session_id
        # (e.g. the entity id), those later calls would miss and spawn a duplicate session. The work-item
        # id lives in metadata and on each trace instead, which is what reconciliation keys off.
        return self._post("/api/ingest/session/open", {
            "session_id": result.session_id,
            "session_type": result.session_type,
            "is_simulated": self.is_simulated,
            "metadata": {
                "date": datetime.now(timezone.utc).date().isoformat(),
                "entity_id": result.entity_id,
            },
        })

    def trace(self, result: RunResult, step) -> dict:
        payload: dict[str, Any] = {
            "session_id": result.session_id,
            "agent": step.agent,
            "step_type": step.step_type,
            "outcome": step.outcome,
        }
        if step.tool_name is not None:      payload["tool_name"] = step.tool_name
        if step.latency_ms:                 payload["latency_ms"] = step.latency_ms
        if step.tokens_input:               payload["tokens_input"] = step.tokens_input
        if step.tokens_output:              payload["tokens_output"] = step.tokens_output
        if step.model:                      payload["model"] = step.model
        if step.error is not None:          payload["error"] = step.error
        if step.entity_id is not None:      payload["entity_id"] = step.entity_id

        # Assemble the payload blob the ingest route stores on ag_traces.payload
        blob: dict[str, Any] = {}
        if step.agent_reasoning is not None: blob["agent_reasoning"] = step.agent_reasoning
        if step.tool_input is not None:      blob["tool_input"] = step.tool_input
        if step.tool_output is not None:     blob["tool_output"] = step.tool_output
        if step.entity_id is not None:       blob["entity_id"] = step.entity_id
        for k, v in (step.payload_extra or {}).items():
            blob[k] = v
        if blob:
            payload["payload"] = blob
        return self._post("/api/ingest/trace", payload)

    def eval(self, result: RunResult, ev) -> dict:
        return self._post("/api/ingest/eval", {
            "session_id": result.session_id,
            "eval_name": ev.eval_name,
            "agent": ev.agent,
            "layer": ev.layer,
            "score": ev.score,
            "passed": ev.passed,
            "detail": ev.detail,
            "entity_id": ev.entity_id,
        })

    def close_session(self, result: RunResult) -> dict:
        return self._post("/api/ingest/session/close", {
            "session_id": result.session_id,
            "terminal_reason": result.terminal_reason,
            "metadata": {
                **result.metadata,
                "total_steps": len(result.traces),
                "confidence": result.confidence,
                # Estimated signals live on close metadata AND on the reviewer's
                # closing trace payload, so the Estimated (trace) side can read them.
                "estimated_signals": result.estimated_signals,
            },
        })

    def outcome(self, result: RunResult, occurred_at: str | None = None) -> dict:
        """Post the real outcome to the ONE reconciliation door. label/value
        reconcile today; the signals bag is forward-compatible (Provy #341)."""
        payload = {
            "entity_id": result.entity_id,
            "session_id": result.session_id,
            "label": "success" if result.outcome_label == "success" else "fail",
            "value": result.outcome_value,
            "source": "confirmed",
            "occurred_at": occurred_at or datetime.now(timezone.utc).isoformat(),
            "signals": result.real_signals,
        }
        return self._post("/api/ingest/outcome", payload)

    # ── convenience: emit a whole run except the outcome (that's EOD reconcile) ─
    def emit_run(self, result: RunResult) -> None:
        # We use OUR OWN session id throughout (id-agnostic ingest, #165): Provy stores it as external_id
        # and resolves every later call by it. No need to capture Provy's internal uuid.
        self.open_session(result)
        for step in result.traces:
            self.trace(result, step)
        for ev in result.evals:
            self.eval(result, ev)
        self.close_session(result)
