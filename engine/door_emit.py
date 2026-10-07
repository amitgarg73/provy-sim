"""Three customer-style senders of ONE planned run, each following web/lib/context-guidance.ts for its door
(argus#1505 test plan, step 7, "a new customer onboards"). New file; nothing else in provy-sim changes.

  RestDoor   the direct REST call: the same calls the integration guide shows (session/open, one POST
             /api/ingest/trace per step with a `context` object at the top level, a span_id generated ONCE per
             step and reused on every retry, session/close).
  SdkDoor    the PUBLISHED provy-sdk package (`pip install provy-sdk`, 0.10.0), used as its README and the guide
             show: ProvyClient, open_session, trace(... output_json={"provy_context": {...}}), eval, close_session.
             The published SDK has no `context=` argument and no context helper (context-guidance.ts says so), so
             the manifest rides in output_json under `provy_context`, which is the guidance for that door.
  OtlpDoor   standard OpenTelemetry (opentelemetry-sdk plus the OTLP/HTTP protobuf exporter), the guide's
             "wrap each run in a root span", `span.set_attribute("provy.context", json.dumps(manifest))` on the
             span of the step that made the decision. No REST session/open or close: an OTLP fleet never calls them.

Every door sends the SAME facts for a step (agent, step type, outcome, entity, tool, tokens, cost, model, input
edges, the pack's claim and extras); only the wire form differs. The context manifest is whatever the planned run
holds in `TraceStep.context`, untouched.

Evals go over each door's own eval path where it has one (REST /api/ingest/eval, SDK client.eval); the OTLP door
sends them over REST /api/ingest/eval, which is where an OTLP customer's check verdicts also go. Outcomes go over
the one outcome door (REST /api/ingest/outcome; the SDK door uses client.report_outcome).

Pre-prod only. The base URL must be on an allow list and the deployment must answer environment=preprod before the
first write. PROVY_ALLOW_PROD is ignored. No key is read from a file here: the caller passes it.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests

from engine.emitter import ProductionTargetRefused, _agent_base
from engine.structural import drop_evals_for_agents_that_did_not_run, structural_evals
from engine.targets import is_production_target, target_host

# The hosts a step-7 run may post to. A local server pointed at pre-prod is allowed because the deployment is asked
# what it is (environment must be preprod) before the first write.
ALLOWED_HOSTS = frozenset({"dev.provy.ai", "provydev.vercel.app", "localhost", "127.0.0.1"})


def assert_preprod(base: str, key: str) -> None:
    host = target_host(base)
    if is_production_target(base) or host not in ALLOWED_HOSTS:
        raise ProductionTargetRefused(f"step 7 posts only to pre-prod hosts {sorted(ALLOWED_HOSTS)}; refusing {base!r}")
    r = requests.get(base.rstrip("/") + "/api/health/env", headers={"x-provy-key": key}, timeout=120)
    env = (r.json() or {}).get("environment", "")
    if env != "preprod":
        raise ProductionTargetRefused(f"{base} says environment={env!r}, not 'preprod'")


# ── the reply log: what every call answered, notes included (task 3, ii) ─────────────────────────
class Replies:
    """Thread-safe record of each call's status and the notes in its reply. Never holds a key."""
    def __init__(self, path: Optional[str] = None):
        self.path, self._lock, self.rows = path, threading.Lock(), []

    def add(self, door: str, path: str, status: Any, body: Any, session: str = "", extra: Optional[dict] = None) -> None:
        notes = []
        if isinstance(body, dict):
            for k in ("notes", "context_notes"):
                v = body.get(k)
                if isinstance(v, list):
                    notes += [str(x) for x in v]
        row = {"door": door, "path": path, "status": status, "session": session, "notes": notes, **(extra or {})}
        with self._lock:
            self.rows.append(row)
            if self.path:
                with open(self.path, "a") as f:
                    f.write(json.dumps(row) + "\n")


class Wire:
    """requests with a retry on 5xx and on a connection error, SENDING THE SAME BODY each time (so a span_id
    generated once is reused). A 4xx is the caller's bug, so it is not retried, as the guide's own _post does."""
    def __init__(self, base: str, key: str, door: str, replies: Replies, attempts: int = 4, timeout: float = 120):
        self.base, self.key, self.door, self.replies, self.attempts, self.timeout = base.rstrip("/"), key, door, replies, attempts, timeout
        self.s = requests.Session()
        self.headers = {"x-provy-key": key, "Content-Type": "application/json"}

    def post(self, path: str, body: Any, session: str = "", extra_headers: Optional[dict] = None, method: str = "POST") -> dict:
        last: Any = None
        for i in range(self.attempts):
            try:
                r = self.s.request(method, self.base + path, data=json.dumps(body, default=str), headers={**self.headers, **(extra_headers or {})}, timeout=self.timeout)
                try:
                    j = r.json()
                except Exception:                                  # noqa: BLE001
                    j = {"raw": r.text[:200]}
                if r.status_code < 500:
                    self.replies.add(self.door, path, r.status_code, j, session, {"attempt": i + 1})
                    return {"status": r.status_code, "json": j}
                last = {"status": r.status_code, "json": j}
            except Exception as e:                                 # noqa: BLE001
                last = {"status": 0, "json": {"error": str(e)[:200]}}
            time.sleep(min(2 ** i, 8))
        self.replies.add(self.door, path, last["status"], last["json"], session, {"attempt": self.attempts, "gave_up": True})
        return last


# ── one planned session, as steps with their input edges ─────────────────────────────────────────
@dataclass
class StepPlan:
    i: int
    step: Any
    at: str                 # when the step ran, ISO 8601 UTC (the work clock)
    inputs: list            # indices of the steps whose output this one consumed


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def plan_steps(result, occurred_at: str) -> list[StepPlan]:
    """Same input-edge rule as engine.emitter.ProvyEmitter.trace: a pipeline is linear, so a step names the newest
    step of the previous agent (the first agent names none). Each step runs one second after the one before it."""
    t0 = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    order: list[str] = []
    last: dict[str, int] = {}
    out: list[StepPlan] = []
    for i, s in enumerate(result.traces):
        base = _agent_base(s.agent)
        if base not in order:
            order.append(base)
        idx = order.index(base)
        inputs = [last[order[idx - 1]]] if idx > 0 and order[idx - 1] in last else []
        last[base] = i
        out.append(StepPlan(i, s, iso(t0 + timedelta(seconds=i)), inputs))
    return out


def evals_of(result, agents) -> list:
    evs = drop_evals_for_agents_that_did_not_run(result)
    if agents:
        evs.extend(structural_evals(result, agents))
    return evs


def eval_body(result, ev) -> dict:
    return {"session_id": result.session_id, "eval_name": ev.eval_name, "agent": ev.agent, "layer": ev.layer, "score": ev.score,
            "passed": ev.passed, "detail": ev.detail, "entity_id": ev.entity_id}


def close_body(result) -> dict:
    return {"session_id": result.session_id, "terminal_reason": result.terminal_reason,
            "total_tokens_in": sum(s.tokens_input or 0 for s in result.traces),
            "total_tokens_out": sum(s.tokens_output or 0 for s in result.traces),
            "total_cost_usd": round(sum(s.cost_usd or 0.0 for s in result.traces), 6),
            "metadata": {**result.metadata, "total_steps": len(result.traces), "confidence": result.confidence,
                         "estimated_signals": result.estimated_signals}}


def outcome_body(rec: dict) -> dict:
    post = rec.get("outcome_post") or {}
    return {"entity_id": rec["entity_id"], "session_id": rec["session_id"], "label": "success" if rec.get("outcome_label") == "success" else "fail",
            "value": rec.get("outcome_value"), "source": "confirmed", "occurred_at": post.get("occurred_at") or rec.get("ts"),
            "signals": post.get("signals", rec.get("real_signals", {}))}


# ── the three doors ──────────────────────────────────────────────────────────────────────────────
class RestDoor:
    name = "rest"

    def __init__(self, base: str, key: str, replies: Replies):
        self.base, self.key = base, key
        self.w = Wire(base, key, self.name, replies)

    def step_body(self, result, p: StepPlan, span_id: str, span_ids: list) -> dict:
        s = p.step
        body: dict[str, Any] = {"session_id": result.session_id, "agent": s.agent, "step_type": s.step_type, "outcome": s.outcome,
                                "span_id": span_id, "occurred_at": p.at}
        if p.inputs:
            body["input_span_ids"] = [span_ids[j] for j in p.inputs]
        if s.tool_name is not None:     body["tool_name"] = s.tool_name
        if s.latency_ms:                body["latency_ms"] = s.latency_ms
        if s.tokens_input:              body["tokens_input"] = s.tokens_input
        if s.tokens_output:             body["tokens_output"] = s.tokens_output
        if s.cost_usd:                  body["cost_usd"] = s.cost_usd
        if s.model:                     body["model"] = s.model
        if s.error is not None:         body["error"] = s.error
        if s.entity_id is not None:     body["entity_id"] = s.entity_id
        blob: dict[str, Any] = {}
        if s.agent_reasoning is not None: blob["agent_reasoning"] = s.agent_reasoning
        if s.tool_input is not None:      blob["tool_input"] = s.tool_input
        if s.tool_output is not None:     blob["tool_output"] = s.tool_output
        for k, v in (s.payload_extra or {}).items():
            blob[k] = v
        if blob:
            body["payload"] = blob
        if s.context is not None:
            body["context"] = s.context                 # the guide: a `context` object at the top level of the step
        return body

    def send_session(self, result, occurred_at: str, agents, resend_first_step: bool = False, resend_all: bool = False) -> dict:
        sid = result.session_id
        self.w.post("/api/ingest/session/open", {"session_id": sid, "session_type": result.session_type, "is_simulated": False,
                                                 "started_at": occurred_at, "metadata": {"date": occurred_at[:10], "entity_id": result.entity_id}}, sid)
        plans = plan_steps(result, occurred_at)
        span_ids = [uuid.uuid4().hex[:16] for _ in plans]           # generated ONCE per step; Wire reuses the body on a retry
        bodies = [self.step_body(result, p, span_ids[p.i], span_ids) for p in plans]
        for b in bodies:
            self.w.post("/api/ingest/trace", b, sid)
        if resend_first_step and bodies:
            self.w.post("/api/ingest/trace", bodies[0], sid)        # a lost reply: the same step, the same span_id, again
        if resend_all:
            for b in bodies:                                        # every reply lost: each step sent a second time with the same span_id
                self.w.post("/api/ingest/trace", b, sid)
        for ev in evals_of(result, agents):
            self.w.post("/api/ingest/eval", eval_body(result, ev), sid)
        self.w.post("/api/ingest/session/close", close_body(result), sid)
        return {"span_ids": span_ids}

    def close_again(self, session_id: str, terminal_reason: str) -> dict:
        return self.w.post("/api/ingest/session/close", {"session_id": session_id, "terminal_reason": terminal_reason}, session_id)

    def send_outcome(self, rec: dict) -> dict:
        return self.w.post("/api/ingest/outcome", outcome_body(rec), rec["session_id"])


class SdkDoor:
    """The published provy-sdk, as `pip install provy-sdk` gives it. PROVY_URL and PROVY_EMIT must be in the
    environment BEFORE `provy` is imported (the package reads PROVY_URL at import), which the caller does."""
    name = "sdk"

    def __init__(self, base: str, key: str, replies: Replies):
        import provy                                            # the installed package, not a path
        self.provy, self.base, self.key, self.replies = provy, base, key, replies
        self.wire = Wire(base, key, self.name, replies)         # only for the outcome-less re-close and the log of replies
        # A tap, not a change: the SDK hides every reply from its caller, so the reply's notes are written down on the way
        # back through requests.post. The response is returned to the SDK untouched.
        orig = requests.post

        def tap(url, **kw):
            r = orig(url, **kw)
            try:
                j = r.json()
            except Exception:                                      # noqa: BLE001
                j = {"raw": (r.text or "")[:200]}
            body = kw.get("json")
            first = body[0] if isinstance(body, list) and body else body
            sid = (first or {}).get("session_id", "") if isinstance(first, dict) else ""
            path = url[len(base.rstrip("/")):] if url.startswith(base.rstrip("/")) else url
            if path.startswith("/api/ingest"):
                replies.add("sdk", path, r.status_code, j, str(sid), {"spans_in_request": len(body) if isinstance(body, list) else 1})
            return r
        requests.post = tap

    def send_session(self, result, occurred_at: str, agents, resend_first_step: bool = False) -> dict:
        ProvyClient = self.provy.ProvyClient
        client = ProvyClient(ingest_key=self.key, base_url=self.base, enabled=True)   # buffered by default, as a customer has it
        server_sid = client.open_session(result.session_type, external_id=result.session_id,
                                         metadata={"date": occurred_at[:10], "entity_id": result.entity_id}, started_at=occurred_at)
        plans = plan_steps(result, occurred_at)
        span_ids: list[str] = []
        for p in plans:
            s = p.step
            out: dict[str, Any] = {}
            claim = None
            for k, v in (s.payload_extra or {}).items():
                if k == "provy_claim":
                    claim = v
                else:
                    out[k] = v
            if s.tool_output is not None:
                out["tool_output"] = s.tool_output
            if s.context is not None:
                out["provy_context"] = s.context                # the guidance: output_json={"provy_context": {...}}
            sp = client.trace(session_id=server_sid, agent=s.agent, step_type=s.step_type, outcome=s.outcome or "",
                              tool_name=s.tool_name, latency_ms=s.latency_ms or None, tokens_in=s.tokens_input or None,
                              tokens_out=s.tokens_output or None, cost_usd=s.cost_usd or None, error=s.error,
                              output_json=out or None, claim=claim, entity_id=s.entity_id,
                              inputs=[span_ids[j] for j in p.inputs] if p.inputs else None, model=s.model, occurred_at=p.at)
            span_ids.append(sp)
        for ev in evals_of(result, agents):
            client.eval(session_id=server_sid, eval_name=ev.eval_name, agent=ev.agent, score=ev.score, passed=ev.passed,
                        layer=ev.layer, entity_id=ev.entity_id, detail=ev.detail)
        client.close_session(server_sid, terminal_reason=result.terminal_reason)
        stats = client.buffer_stats
        self.replies.add(self.name, "sdk:session", 200, {}, result.session_id, {"buffer": stats, "server_session": server_sid})
        return {"span_ids": span_ids, "server_session": server_sid, "buffer": stats}

    def close_again(self, session_id: str, terminal_reason: str) -> dict:
        return self.wire.post("/api/ingest/session/close", {"session_id": session_id, "terminal_reason": terminal_reason}, session_id)

    def send_outcome(self, rec: dict) -> dict:
        b = outcome_body(rec)
        return self.wire.post("/api/ingest/outcome", b, rec["session_id"])


class OtlpDoor:
    """Standard OpenTelemetry. One TracerProvider per run, a root span wrapping the run, one child span per step,
    exported as OTLP/HTTP protobuf. The span clock is the work clock (start and end times are set explicitly)."""
    name = "otlp"

    def __init__(self, base: str, key: str, replies: Replies):
        self.base, self.key, self.replies = base.rstrip("/"), key, replies
        self.rest = RestDoor(base, key, replies)               # evals, outcomes and re-close go over REST, as for any OTLP customer
        self.rest.w.door = "otlp-rest"

    def send_session(self, result, occurred_at: str, agents, resend_first_step: bool = False) -> dict:
        from opentelemetry import trace as ot
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.trace import Link, StatusCode

        replies, door = self.replies, self.name
        # The exporter is the real, unmodified one. It is handed a requests Session with a response hook that writes down what the
        # gateway answered, which an exporter never shows anyone, so the reply's notes can be read.
        sess = requests.Session()

        def _record(resp, *_a, **_k):
            try:
                j = resp.json()
            except Exception:                                      # noqa: BLE001
                j = {"raw": (resp.text or "")[:200]}
            replies.add(door, "/api/otlp/v1/traces", resp.status_code, j, result.session_id, {"request_bytes": len(resp.request.body or b"")})
        sess.hooks["response"].append(_record)
        exporter = OTLPSpanExporter(endpoint=self.base + "/api/otlp/v1/traces", headers={"x-provy-key": self.key}, timeout=120, session=sess)
        provider = TracerProvider(resource=Resource.create({"service.name": result.session_type}))
        provider.add_span_processor(BatchSpanProcessor(exporter, max_export_batch_size=512, schedule_delay_millis=60_000))
        tracer = provider.get_tracer("onboard-otel-co")

        t0 = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        ns = lambda dt: int(dt.timestamp() * 1_000_000_000)        # noqa: E731
        plans = plan_steps(result, occurred_at)
        run_end = t0 + timedelta(seconds=len(plans) + 1)
        root = tracer.start_span(f"process_{result.session_type}", start_time=ns(t0), attributes={
            "provy.session_id": result.session_id, "provy.entity_id": result.entity_id, "provy.step_type": "decision"})
        ctx = ot.set_span_in_context(root)
        spans: list = []
        for p in plans:
            s = p.step
            at = datetime.fromisoformat(p.at.replace("Z", "+00:00"))
            a: dict[str, Any] = {"provy.session_id": result.session_id, "provy.agent": s.agent, "provy.step_type": s.step_type,
                                 "provy.outcome": s.outcome or ""}
            if s.entity_id is not None: a["provy.entity_id"] = s.entity_id
            if s.tool_name is not None: a["provy.tool_name"] = s.tool_name
            if s.tokens_input:          a["provy.tokens_in"] = int(s.tokens_input)
            if s.tokens_output:         a["provy.tokens_out"] = int(s.tokens_output)
            if s.cost_usd:              a["provy.cost_usd"] = float(s.cost_usd)
            if s.model:                 a["provy.model"] = s.model
            if s.error is not None:     a["provy.error"] = str(s.error)
            if s.agent_reasoning is not None: a["provy.agent_reasoning"] = s.agent_reasoning
            for k, v in (s.payload_extra or {}).items():
                if k == "provy_claim":
                    a["provy.claim"] = json.dumps(v)
                elif isinstance(v, (str, int, float, bool)):
                    a[f"provy.payload.{k}"] = v
                else:
                    a[f"provy.payload.{k}"] = json.dumps(v)
            if s.context is not None:
                a["provy.context"] = json.dumps(s.context)      # the guidance: a JSON STRING on the span of the decision
            links = [Link(spans[j].get_span_context()) for j in p.inputs] or None
            sp = tracer.start_span(s.tool_name or s.agent, context=ctx, start_time=ns(at), attributes=a, links=links)
            if s.error is not None:
                sp.set_status(StatusCode.ERROR, str(s.error))
            sp.end(end_time=ns(at + timedelta(milliseconds=max(1, s.latency_ms or 1))))
            spans.append(sp)
        root.end(end_time=ns(run_end))
        provider.force_flush(120_000)
        provider.shutdown()
        for ev in evals_of(result, agents):
            self.rest.w.post("/api/ingest/eval", eval_body(result, ev), result.session_id)
        return {"span_ids": [format(sp.get_span_context().span_id, "016x") for sp in spans]}

    def close_again(self, session_id: str, terminal_reason: str) -> dict:
        return self.rest.close_again(session_id, terminal_reason)

    def send_outcome(self, rec: dict) -> dict:
        return self.rest.send_outcome(rec)


class LogDoor:
    """The log door: one POST of log lines for the whole session, as a pipeline that already writes JSON logs would send it.

    mode "line": beside each model step's event line, a `{"provy_context": {...}}` line carrying the manifest (the guidance for a
    team that can add a log line). mode "map": no manifest line at all; the retrieval rides on the event as an array a fleet
    DECLARES a field map for (`context_log_fields`, config.context_sets.DECLARATIONS), bound to the agent's first event of the
    session. The map has no field for a fingerprint, a version or an instruction: a documented limit of that door, so the manifest
    is cut to what the map can say before it is written. Outcomes and evals go over REST, as for any log customer."""
    name = "log"

    def __init__(self, base: str, key: str, replies: Replies, mode: str = "line"):
        assert mode in ("line", "map")
        self.mode = mode
        self.name = "log" if mode == "line" else "log_map"
        self.rest = RestDoor(base, key, replies)
        self.rest.w.door = self.name + "-rest"
        self.w = Wire(base, key, self.name, replies)

    def body(self, result, occurred_at: str) -> dict:
        plans = plan_steps(result, occurred_at)
        first_event: dict[str, int] = {}
        for p in plans:
            first_event.setdefault(p.step.agent, p.i)
        lines: list[str] = []
        for p in plans:
            s = p.step
            ev: dict[str, Any] = {"ts": p.at, "level": "INFO", "agent": s.agent, "event": s.step_type, "outcome": s.outcome or "",
                                  "entity_id": result.entity_id}
            if s.tool_name:
                ev["tool"] = s.tool_name
            if s.model:
                ev["model"] = s.model
            if s.tokens_input:
                ev["tokens_in"] = s.tokens_input
            if s.tokens_output:
                ev["tokens_out"] = s.tokens_output
            if s.cost_usd:
                ev["cost_usd"] = s.cost_usd
            if self.mode == "map" and first_event.get(s.agent) == p.i:
                owner = next((q.step for q in plans if q.step.agent == s.agent and q.step.step_type in ("decision", "agent_message") and q.step.context), None)
                if owner is not None:
                    docs = [it for it in owner.context.get("items", []) if it.get("kind") == "document"]
                    ev["retrieved"] = [{"index": i["source"], "doc": i["id"], "updated": i.get("as_of"), "cited": bool(i.get("used"))} for i in docs]
            lines.append(json.dumps(ev, separators=(",", ":")))
            if self.mode == "line" and s.context is not None and s.step_type in ("decision", "agent_message"):
                lines.append(json.dumps({"provy_context": {"agent": s.agent, "step_type": s.step_type, **s.context}}, separators=(",", ":")))
        return {"session_type": result.session_type, "session_id": result.session_id, "entity_id": result.entity_id,
                "occurred_at": occurred_at, "logs": "\n".join(lines)}

    def send_session(self, result, occurred_at: str, agents, resend_first_step: bool = False, resend_all: bool = False) -> dict:
        r = self.w.post("/api/ingest/log", self.body(result, occurred_at), result.session_id)
        if (r.get("status") or 0) >= 300 or not r.get("status"):
            # The log door reads the lines with a model; a server with none answers 503. A session that was not accepted is not sent.
            raise RuntimeError(f"log door answered {r.get('status')}: {str(r.get('json'))[:120]}")
        return {"status": r.get("status"), "reply": r.get("json")}

    def close_again(self, session_id: str, terminal_reason: str) -> dict:
        return self.rest.close_again(session_id, terminal_reason)

    def send_outcome(self, rec: dict) -> dict:
        return self.rest.send_outcome(rec)


DOORS = {"rest": RestDoor, "sdk": SdkDoor, "otlp": OtlpDoor, "log": lambda b, k, r: LogDoor(b, k, r, "line"),
         "log_map": lambda b, k, r: LogDoor(b, k, r, "map")}
