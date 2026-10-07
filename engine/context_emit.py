"""Context emitters, one per ingestion route (#1505, lane L4). ADDS this file to provy-sim/engine/.

It replays a ground-truth plan (engine/context.py) over the route the plan names:

  rest             POST /api/ingest/trace, `context` on the step
  sdk              the same body with the header `x-provy-client: provy-sdk/<version>`
  otlp_native      POST /api/otlp/v1/traces, attribute `provy.context` (a JSON object string)
  otlp_convention  OTLP with an OpenInference retriever span (`retrieval.documents.N.document.*`) and a
                   GenAI instruction attribute (`gen_ai.system_instructions`) on the decision span
  log_line         POST /api/ingest/log, a `provy_context` line (whole-line JSON, or key=value)
  log_map          POST /api/ingest/log, a retrieval line shaped by a fleet-declared field map

THE SAME LOGICAL MANIFEST goes over every route. What a route cannot carry is dropped by one function
(engine.context.route_manifests) that the emitters and the expected-value side both use, so a test can
assert what Provy should store per route without reading the emitter.

ProvyEmitter is the base on purpose: its production refusal and its environment probe (it asks the
deployment what it is before the first write) apply unchanged. This class adds a stricter rule on top:
the base URL must be on an allowlist of pre-prod hosts, and PROVY_ALLOW_PROD is ignored. The simulator
posts only to pre-prod and the check is in the code that posts.

No key is read here. A fleet's ingest key comes from the environment variable named in
config/context_sets.key_env_name, set by whoever runs the set.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from engine import context as C
from engine.emitter import ProductionTargetRefused, ProvyEmitter, request_headers
from engine.targets import is_production_target, target_host

# Hosts the context simulation may post to. A deny list cannot see through an alias, so this is an
# allow list; the base class then probes the deployment for environment == preprod as well.
PREPROD_HOSTS = frozenset({"dev.provy.ai", "provydev.vercel.app"})
SDK_CLIENT_HEADER = "provy-sdk/0.10.0"


def assert_preprod_target(url: str) -> None:
    host = target_host(url)
    if is_production_target(url) or host not in PREPROD_HOSTS:
        raise ProductionTargetRefused(
            f"the context simulation posts only to pre-prod ({', '.join(sorted(PREPROD_HOSTS))}); refusing {url!r}. "
            f"PROVY_ALLOW_PROD does not apply to this emitter.")


# ── wire builders: pure functions of a plan, no network ─────────────────────────────────────────
def _span_id(plan: dict, span: str) -> str:
    ids = plan.get("span_ids")
    return ids[span] if ids and span in ids else C.span_id(plan["session_id"], span)


def _step_by_span(plan: dict) -> dict[str, dict]:
    return {s["span"]: s for s in plan["steps"]}


wire_manifest = C.wire_manifest


def rest_body(plan: dict, step: dict, route: str, include_context: bool = True) -> dict:
    body: dict[str, Any] = {
        "session_id": plan["session_id"], "agent": step["agent"], "step_type": step["step_type"], "outcome": step["outcome"],
        "span_id": _span_id(plan, step["span"]), "occurred_at": step["at"], "entity_id": plan["entity_id"],
    }
    if step["input_spans"]:
        body["input_span_ids"] = [_span_id(plan, s) for s in step["input_spans"]]
    if step.get("tool_name"):
        body["tool_name"] = step["tool_name"]
    if step.get("claim"):
        body["payload"] = {"provy_claim": [dict(step["claim"], entity_id=plan["entity_id"])]}
    if include_context and step["step_type"] != "tool_call":
        m = wire_manifest(step, route)
        if m is not None:
            body["context"] = m
    return body


def _sattr(key: str, value: str) -> dict:
    return {"key": key, "value": {"stringValue": value}}


def _iattr(key: str, value: int) -> dict:
    return {"key": key, "value": {"intValue": str(value)}}


def _dattr(key: str, value: float) -> dict:
    return {"key": key, "value": {"doubleValue": value}}


def _ns(iso_s: str) -> str:
    return str(int(C.parse_iso(iso_s).timestamp()) * 1_000_000_000)


def _trace_id(plan: dict) -> str:
    return hashlib.sha256(plan["session_id"].encode()).hexdigest()[:32]


def _instruction_text_for(step: dict) -> str:
    label = step["manifest"]["instruction"]["version"]          # "<agent>-<version>"
    version = label[len(step["agent"]) + 1:]
    text = C.instruction_text(step["agent"], version)
    assert C.hash_content(text) == step["manifest"]["instruction"]["hash"], "instruction hash does not match its text"
    return text


def otlp_body(plan: dict, route: str, include_context: bool = True) -> dict:
    spans = []
    tid = _trace_id(plan)
    for step in plan["steps"]:
        sid = _span_id(plan, step["span"])
        attrs = [_sattr("provy.session_id", plan["session_id"]), _sattr("provy.agent", step["agent"]),
                 _sattr("provy.outcome", step["outcome"]), _sattr("provy.entity_id", plan["entity_id"])]
        if step.get("tool_name"):
            attrs.append(_sattr("provy.tool_name", step["tool_name"]))
        if step.get("claim"):
            attrs.append(_sattr("provy.claim", json.dumps(dict(step["claim"], entity_id=plan["entity_id"]), separators=(",", ":"))))
        is_lookup = step["step_type"] == "tool_call"
        if route == "otlp_convention" and is_lookup:
            attrs.append(_sattr("openinference.span.kind", "RETRIEVER"))
            if include_context:
                attrs += _retriever_attrs(plan, step)
        else:
            attrs.append(_sattr("provy.step_type", step["step_type"]))
            if include_context and not is_lookup:
                if route == "otlp_native":
                    m = wire_manifest(step, route)
                    if m is not None:
                        attrs.append(_sattr("provy.context", json.dumps(m, separators=(",", ":"))))
                elif route == "otlp_convention" and step.get("manifest") and "instruction" in step["manifest"]:
                    attrs.append(_sattr("gen_ai.system_instructions", _instruction_text_for(step)))
                    attrs.append(_sattr("provy.prompt_version", step["manifest"]["instruction"]["version"]))
                    tin = step["manifest"].get("tokens_in")
                    if tin:
                        attrs.append(_iattr("gen_ai.usage.input_tokens", tin["total"]))
        span = {
            "traceId": tid, "spanId": sid, "name": step.get("tool_name") or step["agent"],
            "startTimeUnixNano": _ns(step["at"]), "endTimeUnixNano": str(int(_ns(step["at"])) + 500_000_000),
            "status": {"code": 1}, "attributes": attrs,
        }
        if step["input_spans"]:
            span["links"] = [{"traceId": tid, "spanId": _span_id(plan, s)} for s in step["input_spans"]]
        spans.append(span)
    return {"resourceSpans": [{"resource": {"attributes": [_sattr("service.name", plan["fleet_label"])]},
                               "scopeSpans": [{"scope": {"name": "provy-sim-context"}, "spans": spans}]}]}


def _retriever_attrs(plan: dict, step: dict) -> list[dict]:
    """OpenInference documents for a retriever span. The decision step that reads this lookup holds the
    manifest; its items are the documents. Content is the synthetic body (the gateway hashes it and throws
    it away), metadata carries only what SPEC 4.3 says is read: source, as_of, version."""
    decision = next(s for s in plan["steps"] if s["input_spans"] == [step["span"]])
    # what this route carries for the lookup span, from the same projection the expected side uses
    m = C.route_manifests(decision, "otlp_convention").get(step["span"]) or {}
    attrs: list[dict] = []
    items = m.get("items")
    if items:
        for n, it in enumerate(items):
            p = f"retrieval.documents.{n}.document"
            text = C.item_text(C.content_source(it["source"]), it["id"], it["version"])
            assert C.hash_content(text) == it["hash"], "item hash does not match its text"
            attrs += [_sattr(f"{p}.id", it["id"]), _dattr(f"{p}.score", it["score"]), _sattr(f"{p}.content", text),
                      _sattr(f"{p}.metadata", json.dumps({"source": it["source"], "as_of": it["as_of"], "version": it["version"]},
                                                         separators=(",", ":")))]
    elif m.get("retrieval", {}).get("returned") == 0:
        # an exporter that exports documents and returned none says so (SPEC 4.3, the explicit form).
        # A silent retriever (no manifest entry at all) sends nothing and reads as UNKNOWN.
        attrs.append(_iattr("provy.retrieval.returned", 0))
    return attrs


def log_body(plan: dict, route: str) -> dict:
    """A request for the log door. One JSON event line per step, so the door's event extraction has
    something plain to read, and the context line (or the map-shaped retrieval line) beside it."""
    lines: list[str] = []
    wire = plan.get("wire") or "json"
    for step in plan["steps"]:
        ev = {"ts": step["at"], "level": "INFO", "agent": step["agent"], "event": step["step_type"], "outcome": step["outcome"],
              "entity_id": plan["entity_id"]}
        if step.get("tool_name"):
            ev["tool"] = step["tool_name"]
        if route == "log_map" and step["step_type"] == "tool_call":
            decision = next(s for s in plan["steps"] if s["input_spans"] == [step["span"]])
            m = next(iter(C.route_manifests(decision, "log_map").values()), None)
            if m is not None:
                ev["retrieved"] = [{"index": i["source"], "doc": i["id"], "updated": i["as_of"], "cited": i["used"]} for i in m.get("items", [])]
        lines.append(json.dumps(ev, separators=(",", ":")))
        if route == "log_line" and step["step_type"] != "tool_call":
            m = wire_manifest(step, "log_line")
            if m is not None:
                obj = {"agent": step["agent"], "step_type": step["step_type"], **m}
                if wire == "kv":
                    lines.append(f"{step['at']} INFO {step['agent']} provy_context={json.dumps(obj, separators=(',', ':'))}")
                else:
                    lines.append(json.dumps({"provy_context": obj}, separators=(",", ":")))
    return {"session_type": plan["session_type"], "session_id": plan["session_id"], "entity_id": plan["entity_id"],
            "occurred_at": plan["occurred_at"], "logs": "\n".join(lines)}


# ── the emitter ─────────────────────────────────────────────────────────────────────────────────
class ContextEmitter(ProvyEmitter):
    def __init__(self, ingest_key: Optional[str] = None, base_url: Optional[str] = None,
                 transport: Optional[Callable[[str, Any, dict], dict]] = None, capture: bool = True):
        super().__init__(ingest_key=ingest_key, base_url=base_url, is_simulated=False, capture=capture)
        assert_preprod_target(self.base)
        self.transport = transport

    def _send(self, path: str, payload: Any, extra_headers: Optional[dict] = None) -> dict:
        headers = {**request_headers(self.key), **(extra_headers or {})}
        if self.capture:
            self.sent.append({"path": path, "method": "POST", "payload": payload, "headers": {k: v for k, v in headers.items() if k != "x-provy-key"}})
        if self.transport is not None:
            return self.transport(path, payload, headers)
        if not self.enabled:
            return {"skipped": True}
        self._verify_environment()
        try:
            req = urllib.request.Request(f"{self.base}{path}", data=json.dumps(payload, default=str).encode(), headers=headers, method="POST")
            body = urllib.request.urlopen(req, timeout=30).read().decode()
            try:
                return json.loads(body)
            except Exception:
                return {"ok": True}
        except Exception as e:                                  # noqa: BLE001
            return {"error": str(e)}

    def emit_plan(self, plan: dict, route: Optional[str] = None, include_context: bool = True, wire: Optional[str] = None) -> None:
        """Replay one planned session over its route. `route` overrides the plan's (the equivalence test
        sends one logical session over every route); a capture plan with route 'none' is sent as REST."""
        route = route or plan["route"]
        if route == "none":
            route = "rest"
        if wire:
            plan = {**plan, "wire": wire}
        if route in ("rest", "sdk", "otlp_native", "otlp_convention"):
            self._send("/api/ingest/session/open", {"session_id": plan["session_id"], "session_type": plan["session_type"],
                                                    "is_simulated": False, "started_at": plan["occurred_at"],
                                                    "metadata": {"date": plan["occurred_at"][:10], "entity_id": plan["entity_id"]}})
        if route in ("rest", "sdk"):
            extra = {"x-provy-client": SDK_CLIENT_HEADER} if route == "sdk" else None
            for step in plan["steps"]:
                self._send("/api/ingest/trace", rest_body(plan, step, route, include_context), extra)
        elif route in ("otlp_native", "otlp_convention"):
            self._send("/api/otlp/v1/traces", otlp_body(plan, route, include_context))
        elif route in ("log_line", "log_map"):
            body = log_body(plan, route) if include_context else log_body(_without_context(plan), "log_line")
            self._send("/api/ingest/log", body)
        else:
            raise ValueError(route)
        if route not in ("log_line", "log_map"):
            self._send("/api/ingest/session/close", {"session_id": plan["session_id"], "terminal_reason": "completed",
                                                      "metadata": {"total_steps": len(plan["steps"])}})

    def emit_capture(self, plan: dict) -> None:
        """A capture-fleet session as the CURRENT build can store it: spans, work items, no manifest.
        The old build would drop the field anyway; sending none makes the 'before' state exact."""
        self.emit_plan(plan, route="rest", include_context=False)

    def outcome_body(self, plan: dict) -> dict:
        last = max(C.parse_iso(s["at"]) for s in plan["steps"]) + timedelta(hours=1)
        o = plan["outcome"]
        return {"entity_id": plan["entity_id"], "session_id": plan["session_id"], "label": o["label"], "value": None,
                "source": "confirmed", "occurred_at": C.iso(last), "signals": o["signals"]}

    def emit_outcome(self, plan: dict) -> dict:
        return self._send("/api/ingest/outcome", self.outcome_body(plan))


def _without_context(plan: dict) -> dict:
    steps = [{**s, "manifest": None} for s in plan["steps"]]
    return {**plan, "steps": steps}


def emit_set(set_name: str, data_dir: str, make_emitter: Callable[[str], ContextEmitter], fleets: Optional[list[str]] = None,
             limit: Optional[int] = None, with_outcomes: bool = True, progress: Optional[Callable[[str], None]] = None) -> dict:
    """Replay a committed set. Refuses unless the file's hash is the recorded one (verify_ground_truth).

    `make_emitter(fleet_key)` returns the emitter for a fleet (one ingest key per fleet). Sessions are
    emitted in plan order, fleet by fleet; the outcome follows its session. Capture-A plans are sent WITHOUT
    their manifests (the build live at capture time cannot store them): see ContextEmitter.emit_capture.
    Returns counts per fleet."""
    from engine import context_truth as T
    T.verify_ground_truth(set_name, data_dir)
    plans = C.load_plans(os.path.join(data_dir, T.truth_filename(set_name)))
    capture = set_name.startswith("CAP")
    counts: dict[str, int] = {}
    emitters: dict[str, ContextEmitter] = {}
    total = len(plans)
    for n, plan in enumerate(plans, 1):
        if fleets and plan["fleet"] not in fleets:
            continue
        if limit is not None and counts.get(plan["fleet"], 0) >= limit:
            continue
        em = emitters.setdefault(plan["fleet"], make_emitter(plan["fleet"]))
        if capture:
            em.emit_capture(plan)
        else:
            em.emit_plan(plan)
        if with_outcomes:
            em.emit_outcome(plan)
        counts[plan["fleet"]] = counts.get(plan["fleet"], 0) + 1
        if progress and n % 20 == 0:
            progress(f"{set_name}: {n}/{total} sessions planned, {sum(counts.values())} sent")
    return counts


def fleet_key(set_name: str, fleet: str) -> str:
    """The ingest key for a fleet, from the environment variable NAMED in config. Never from a file."""
    from config import context_sets as CS
    return os.environ.get(CS.key_env_name(set_name, fleet), "")
