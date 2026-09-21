"""Provy REST emitter — copied/adapted from trading-agent-c/trace/logger.py.

Raw REST over the x-provy-key header, no framework lock-in. Base URL is
Provy pre-prod (https://provydev.vercel.app), where simulated fleets live, NOT production. Honors
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
import uuid
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

from .types import RunResult

from engine.targets import is_production_target


def _agent_base(agent: str) -> str:
    """`research_GILD` -> `research`.

    Mirrors `agentBase()` in the product. Fan-out per work item is the SAME logical step, so the
    per-entity children must not each be treated as a separate pipeline stage: that is exactly what
    made production's 729 `research -> research_<TICKER>` edges look cross-agent when they were not.
    """
    return agent.split("_", 1)[0] if "_" in agent else agent


class ProductionTargetRefused(RuntimeError):
    """Raised when a simulator run is pointed at a production Provy host."""


# ⛔ THE CUSTOM DOMAIN, NOT THE VERCEL ONE. provydev.vercel.app sits behind Vercel deployment
# protection, so every emit to it returns the SSO redirect instead of the API unless
# VERCEL_PROTECTION_BYPASS happens to be set. Nobody sets it by hand, which is how a batch can run to
# completion and land nothing. dev.provy.ai is the same pre-prod deployment under a custom domain,
# and custom domains are exempt from that protection.
DEFAULT_BASE_URL = "https://dev.provy.ai"


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


def request_headers(key: str) -> dict:
    """Standard ingest headers, plus the Vercel deployment-protection bypass when
    VERCEL_PROTECTION_BYPASS is set. provydev sits behind Vercel SSO, so a headless run needs this
    to reach the ingest routes at all: without it every emit gets the SSO redirect, not the API.
    Since simulated fleets target provydev, CI sets it too."""
    headers = {"Content-Type": "application/json", "x-provy-key": key}
    bypass = os.environ.get("VERCEL_PROTECTION_BYPASS", "").strip()
    if bypass:
        headers["x-vercel-protection-bypass"] = bypass
        # Deliberately NOT x-vercel-set-bypass-cookie. That header asks Vercel to hand back a
        # cookie, which it does by answering 307 instead of running the route, so every emit
        # became a redirect and no data ever landed. It is for browsers keeping a session; a
        # headless caller sends the token on each request and wants the route to run.
    return headers


class ProvyEmitter:
    def __init__(self, ingest_key: str | None = None, base_url: str | None = None,
                 is_simulated: bool = False, capture: bool = True):
        self.key = ingest_key if ingest_key is not None else os.environ.get("PROVY_KEY", "")
        self.base = (base_url or os.environ.get("PROVY_URL") or DEFAULT_BASE_URL).rstrip("/")

        # ⛔ THE SIMULATOR NEVER WRITES TO PRODUCTION. Until 2026-09-09 the only production guards
        # lived in wire_itsm_fleet.py and install_servicenow_lifecycle.py, which are SETUP scripts.
        # This class is the hot path: it is what actually posts sessions, traces and evals. Setting
        # PROVY_URL to a production host was enough to write simulated work into the real ledger,
        # which is what happened on 2026-07-27. The check belongs where the writing happens.
        #
        # PROVY_ALLOW_PROD=1 is the deliberate escape hatch, matching --allow-prod on the setup
        # scripts. It has to be typed on purpose; nothing defaults to it.
        if is_production_target(self.base) and os.environ.get("PROVY_ALLOW_PROD") != "1":
            raise ProductionTargetRefused(
                f"refusing to emit simulated work to production ({self.base}). "
                f"Point PROVY_URL at {DEFAULT_BASE_URL}, or set PROVY_ALLOW_PROD=1 if you truly mean it."
            )
        self._env_verified = False
        # ⛔ SPAN IDENTITY AND INPUT EDGES, PER SESSION (argus#1009). The emitter sent no span_id at
        # all, so every simulated span was undedupable AND the fleet could express no relationship
        # between steps. Measured before this: 5,731 pre-prod spans, zero edges between two agents —
        # the simulator could not produce the very shape the product's victim attribution needs.
        #
        # Keyed by session so a long run cannot leak one session's spans into the next one's inputs.
        self._last_span: dict[str, dict[str, str]] = {}   # session_id -> agent -> newest span id
        self._agent_order: dict[str, list[str]] = {}      # session_id -> agents in first-seen order
        self.is_simulated = is_simulated
        self.capture = capture
        self.sent: list[dict] = []       # {path, method, payload} for every call built

    @property
    def enabled(self) -> bool:
        return emit_enabled(self.base, self.key)

    # ── low-level ────────────────────────────────────────────────────────────
    def _verify_environment(self) -> None:
        """Ask the deployment what it is, once, before anything is written.

        ⛔ A HOSTNAME ALLOWLIST CANNOT SEE THROUGH AN ALIAS. dev.provy.ai is pre-prod by convention,
        not by construction: it carries no branch pin, and on 2026-09-09 a production deploy claimed
        it, so the dev hostname served the production database for about forty minutes. The name
        looked fine the whole time. This asks the deployment instead, which is the only thing that
        actually knows.

        Refuses when it cannot tell. An unreachable probe means the write is likely to fail anyway,
        so failing closed costs almost nothing and guessing costs the production ledger.
        """
        if self._env_verified:
            return
        url = f"{self.base}/api/health/env"
        try:
            req = urllib.request.Request(url, headers=request_headers(self.key))
            with urllib.request.urlopen(req, timeout=20) as r:
                env = (json.loads(r.read().decode()) or {}).get("environment", "")
        except Exception as e:                                  # noqa: BLE001
            raise ProductionTargetRefused(
                f"cannot confirm {self.base} is pre-prod ({e}). Refusing to emit rather than guess."
            ) from e
        if env != "preprod" and os.environ.get("PROVY_ALLOW_PROD") != "1":
            raise ProductionTargetRefused(
                f"{self.base} reports environment={env!r}, not 'preprod'. Refusing to emit simulated "
                f"work. If a deploy has claimed the hostname, repair the alias before running again."
            )
        self._env_verified = True

    def _post(self, path: str, payload: dict) -> dict:
        if self.capture:
            self.sent.append({"path": path, "method": "POST", "payload": payload})
        if not self.enabled:
            return {"skipped": True}
        self._verify_environment()
        try:
            req = urllib.request.Request(
                f"{self.base}{path}",
                data=json.dumps(payload, default=str).encode(),
                headers=request_headers(self.key),
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
    def open_session(self, result: RunResult, occurred_at: str | None = None) -> dict:
        # Do NOT send a separate external_id: Provy resolves later trace/eval/close/outcome calls by
        # matching external_id to the session id we send here. If external_id differed from session_id
        # (e.g. the entity id), those later calls would miss and spawn a duplicate session. The work-item
        # id lives in metadata and on each trace instead, which is what reconciliation keys off.
        when = occurred_at or datetime.now(timezone.utc).isoformat()
        return self._post("/api/ingest/session/open", {
            "session_id": result.session_id,
            "session_type": result.session_type,
            "is_simulated": self.is_simulated,
            # argus#1072: when the work ran, not when we seeded it. The server refuses a future time
            # and falls back to arrival, so a pack that dates itself wrong degrades to today.
            "started_at": when,
            "metadata": {
                "date": when[:10],
                "entity_id": result.entity_id,
            },
        })

    def trace(self, result: RunResult, step, occurred_at: str | None = None) -> dict:
        span_id = uuid.uuid4().hex[:16]
        payload: dict[str, Any] = {
            "session_id": result.session_id,
            "agent": step.agent,
            "step_type": step.step_type,
            "outcome": step.outcome,
            "span_id": span_id,
        }

        # ⛔ THE INPUT EDGE IS CLAIMED ONLY WHERE THE SIMULATOR ACTUALLY KNOWS IT.
        #
        # Every pack here is a linear pipeline — intake feeds validator feeds adjudicator feeds
        # reviewer — declared by the pack itself, so "this step read the previous agent's output" is
        # a fact about the simulation, not a guess from wall-clock order. That distinction is the
        # whole point of the field: Provy has twice had to remove logic that inferred involvement
        # from position, and a simulator that fakes the edge would train the attribution code on a
        # relationship no real fleet reports.
        #
        # The FIRST agent in a session declares nothing rather than [], because "nothing upstream
        # exists yet" is not the same claim as "I read nothing".
        order = self._agent_order.setdefault(result.session_id, [])
        base = _agent_base(step.agent)
        if base not in order:
            order.append(base)
        seen = self._last_span.setdefault(result.session_id, {})
        idx = order.index(base)
        if idx > 0:
            upstream = seen.get(order[idx - 1])
            if upstream:
                payload["input_span_ids"] = [upstream]
        seen[base] = span_id
        if step.tool_name is not None:      payload["tool_name"] = step.tool_name
        if step.latency_ms:                 payload["latency_ms"] = step.latency_ms
        if step.tokens_input:               payload["tokens_input"] = step.tokens_input
        if step.tokens_output:              payload["tokens_output"] = step.tokens_output
        if step.cost_usd:                   payload["cost_usd"] = step.cost_usd
        if step.model:                      payload["model"] = step.model
        if step.error is not None:          payload["error"] = step.error
        if step.entity_id is not None:      payload["entity_id"] = step.entity_id

        # Assemble the payload blob the ingest route stores on ag_traces.payload
        blob: dict[str, Any] = {}
        if step.agent_reasoning is not None: blob["agent_reasoning"] = step.agent_reasoning
        if step.system is not None:          blob["system"] = step.system
        if step.user is not None:            blob["user"] = step.user
        if step.tool_input is not None:      blob["tool_input"] = step.tool_input
        if step.tool_output is not None:     blob["tool_output"] = step.tool_output
        if step.entity_id is not None:       blob["entity_id"] = step.entity_id
        for k, v in (step.payload_extra or {}).items():
            blob[k] = v
        if blob:
            payload["payload"] = blob
        # argus#1072: a step that ran last Tuesday says so, or the server stamps it as arriving now.
        if occurred_at:
            payload["occurred_at"] = occurred_at
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
        # Roll the per-step token/cost usage up to the session, the way a real SDK client does at close.
        # The trace steps already carry tokens_input/tokens_output/cost_usd; without this the session's
        # total_* stay 0 and the Sessions list + Command Center spend read "no cost data".
        tok_in = sum(s.tokens_input or 0 for s in result.traces)
        tok_out = sum(s.tokens_output or 0 for s in result.traces)
        cost = round(sum(s.cost_usd or 0.0 for s in result.traces), 6)
        return self._post("/api/ingest/session/close", {
            "session_id": result.session_id,
            "terminal_reason": result.terminal_reason,
            "total_tokens_in": tok_in,
            "total_tokens_out": tok_out,
            "total_cost_usd": cost,
            "metadata": {
                **result.metadata,
                "total_steps": len(result.traces),
                "confidence": result.confidence,
                # Estimated signals live on close metadata AND on the reviewer's
                # closing trace payload, so the Estimated (trace) side can read them.
                "estimated_signals": result.estimated_signals,
            },
        })

    def outcome_payload(self, result: RunResult, occurred_at: str | None = None) -> dict:
        """Build the outcome body WITHOUT sending it.

        ⛔ THE ONE PLACE AN OUTCOME IS CONSTRUCTED. Split out of outcome() so a batch that is not
        reconciled now can hand its ground truth to the console and have it delivered later
        (control_client.post_pending_outcomes). The console stores this verbatim and forwards it
        unchanged; if it built a body of its own there would be two implementations of
        /api/ingest/outcome in two languages, and the one this repo's tests never touch is the one
        that would drift.
        """
        return {
            "entity_id": result.entity_id,
            "session_id": result.session_id,
            "label": "success" if result.outcome_label == "success" else "fail",
            "value": result.outcome_value,
            "source": "confirmed",
            "occurred_at": occurred_at or datetime.now(timezone.utc).isoformat(),
            "signals": result.real_signals,
        }

    def outcome(self, result: RunResult, occurred_at: str | None = None) -> dict:
        """Post the real outcome to the ONE reconciliation door. label/value
        reconcile today; the signals bag is forward-compatible (Provy #341)."""
        return self._post("/api/ingest/outcome", self.outcome_payload(result, occurred_at))

    # ── convenience: emit a whole run except the outcome (that's EOD reconcile) ─
    def emit_run(self, result: RunResult, agents: list | None = None,
                 occurred_at: str | None = None) -> None:
        """Emit a whole run.

        `occurred_at` is when the work ran, ISO 8601. Omit it for a live run.

        ⛔ WITHOUT IT EVERY SESSION IS STAMPED AT THE MOMENT OF SEEDING (argus#1072). The outcome side
        has dated itself correctly since #812 ("fall back to when the work ran, never to now"), and
        the session side never did, so a pack representing weeks of business landed as one afternoon.
        Measured on pre-prod: Post-call 157 sessions across 1 day, Claims 108 across 2, Refund 106
        across 3, every one of them the sitting that seeded it. Only Strategy C has real spread, and
        only because it genuinely ran on a cron for six weeks.

        So every activity chart, drift window and "running less than usual" ever read off a simulated
        fleet was measuring the seeding run rather than the simulated business.
        """
        # We use OUR OWN session id throughout (id-agnostic ingest, #165): Provy stores it as external_id
        # and resolves every later call by it. No need to capture Provy's internal uuid.
        #
        # ⛔ STRUCTURAL CHECKS ARE DERIVED HERE, AT THE ONE SEAM BOTH RUN PATHS SHARE. runner.run_one
        # and desk._finish both end at emit_run; deriving them in either caller alone would give the
        # desk fleets no structural grading and nothing would report the difference.
        #
        # ⛔ WITHOUT A ROSTER, pipeline_completion CANNOT FAIL. Its denominator is the agents that
        # SHOULD have run; falling back to the agents that DID run makes a skipped agent invisible,
        # which is the failure it exists to catch. So it is computed only when a roster is passed,
        # and the caller that has one always passes it.
        self.open_session(result, occurred_at)
        for step in result.traces:
            self.trace(result, step, occurred_at)
        # ⛔ AN EVAL FOR AN AGENT THAT DID NOT RUN IS A FABRICATED PASS, and it is what made a total
        # pipeline break read as a 0.9-quality session (argus#677). Dropped here, at the same seam
        # the structural checks are derived, so every pack gets it.
        from engine.structural import drop_evals_for_agents_that_did_not_run, structural_evals
        evals = drop_evals_for_agents_that_did_not_run(result)
        if agents:
            evals.extend(structural_evals(result, agents))
        for ev in evals:
            self.eval(result, ev)
        self.close_session(result)
