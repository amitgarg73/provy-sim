"""A fake Provy that applies the SPEC section 2 normalizer rules, for testing the emitters (#1505, L4).

This is NOT L1's code. The L1 and L1b branches carried no normalizer when this lane was built
(integration/fix-round-2026-10-01-context-manifest-l1 and -l1b have no commits beyond the spec), so this
is a small, independent reading of SPEC 2.1, 2.2, 2.4, 2.5, 4.2, 4.3 and 4.4. Where the spec is silent
the choice is marked `# ASSUMED`. When L1's web/lib/context-capture.ts lands, the lead should run the
emitters' output through it as well (SIM-RUNBOOK step "rerun the equivalence test against L1's code").

It is deliberately strict about nothing the spec does not say, and it never calls a model: the log
door's event extraction is replaced by reading the JSON event lines the emitter writes.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

CAP_ITEMS = 20
CAP_BYTES = 4096
KINDS = {"document", "memory", "tool_result", "input", "other"}
HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ITEM_KEYS = ("kind", "source", "id", "as_of", "used", "hash", "score", "version", "tokens")
FLOOR = datetime(2000, 1, 1, tzinfo=timezone.utc)
SKEW = timedelta(minutes=5)


class Bad400(Exception):
    pass


def _hash(text: str) -> str:
    """SPEC 1.1, written out again here on purpose: not imported from the simulator."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _utc_ms(s: Any) -> Optional[str]:
    if not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def _int(v: Any, notes: list, what: str) -> Optional[int]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or (isinstance(v, float) and not math.isfinite(v)):
        notes.append(f"{what} was not a number and was dropped")
        return None
    n = int(v)
    if n < 0 or n > 1_000_000_000:
        notes.append(f"{what} was clamped")
        n = max(0, min(n, 1_000_000_000))
    return n


def normalize_context(raw: Any, captured_by: str, now: datetime) -> tuple[Optional[dict], list[str]]:
    notes: list[str] = []
    if not isinstance(raw, dict):
        return None, ["context was not an object and was not stored"]
    unknown = False
    out: dict[str, Any] = {"v": 1}
    for k in raw:
        if k not in ("v", "items", "retrieval", "instruction", "tokens_in", "truncated", "captured_by"):
            unknown = True
    truncated = False
    if "items" in raw:
        if not isinstance(raw["items"], list):
            notes.append("items was not a list and was dropped")
        else:
            src = raw["items"]
            if len(src) > CAP_ITEMS:
                notes.append(f"items were cut to {CAP_ITEMS}")
                src = src[:CAP_ITEMS]
                truncated = True
            items = []
            for it in src:
                if not isinstance(it, dict):
                    notes.append("an item was not an object and was dropped")
                    continue
                if any(k not in ITEM_KEYS for k in it):
                    unknown = True
                o: dict[str, Any] = {}
                kind = it.get("kind")
                if kind in KINDS:
                    o["kind"] = kind
                else:
                    o["kind"] = "other"
                    notes.append("an item kind was not recognised and was stored as other")
                for f, cap in (("source", 120), ("id", 120), ("version", 64)):
                    if f in it:
                        if isinstance(it[f], str):
                            o[f] = it[f][:cap]
                        else:
                            notes.append(f"item {f} was not text and was dropped")
                if "as_of" in it:
                    ms = _utc_ms(it["as_of"])
                    d = datetime.fromisoformat(ms.replace("Z", "+00:00")) if ms else None
                    if d is None or d < FLOOR or d > now + SKEW:
                        notes.append("an item as_of was unreadable, in the future or before 2000 and was dropped")
                    else:
                        o["as_of"] = ms
                if "used" in it:
                    if isinstance(it["used"], bool):
                        o["used"] = it["used"]
                    else:
                        notes.append("item used was not true or false and was dropped")
                if "hash" in it:
                    if isinstance(it["hash"], str) and HASH_RE.match(it["hash"]):
                        o["hash"] = it["hash"]
                    else:
                        notes.append("an item hash was not sha256: plus 64 hex and was dropped")
                if "score" in it:
                    s = it["score"]
                    if isinstance(s, (int, float)) and not isinstance(s, bool) and math.isfinite(s):
                        o["score"] = round(float(s), 4)
                    else:
                        notes.append("item score was not a finite number and was dropped")
                if "tokens" in it:
                    t = _int(it["tokens"], notes, "item tokens")
                    if t is not None:
                        o["tokens"] = t
                items.append(o)
            out["items"] = items
    if "retrieval" in raw:
        r = raw["retrieval"]
        if isinstance(r, dict) and "returned" in r:
            n = _int(r["returned"], notes, "retrieval.returned")
            if n is not None:
                out["retrieval"] = {"returned": n}
        else:
            notes.append("retrieval was not {returned: number} and was dropped")
    if "instruction" in raw:
        r = raw["instruction"]
        if isinstance(r, dict):
            o = {}
            if isinstance(r.get("version"), str):
                o["version"] = r["version"][:64]
            if "hash" in r:
                if isinstance(r["hash"], str) and HASH_RE.match(r["hash"]):
                    o["hash"] = r["hash"]
                else:
                    notes.append("the instruction hash was not sha256: plus 64 hex and was dropped")
            if o:
                out["instruction"] = o
        else:
            notes.append("instruction was not an object and was dropped")
    if "tokens_in" in raw:
        r = raw["tokens_in"]
        if isinstance(r, dict):
            o = {}
            for f in ("total", "context"):
                if f in r:
                    n = _int(r[f], notes, f"tokens_in.{f}")
                    if n is not None:
                        o[f] = n
            if o:
                out["tokens_in"] = o
    if unknown:
        notes.append("unrecognised fields were ignored")
    if not any(k in out for k in ("items", "retrieval", "instruction", "tokens_in")):
        return None, notes + ["nothing readable was left in the context, so none was stored"]
    # 2.4: cut order over 4,096 bytes: items from the end, then version and tokens of the rest from the last back
    def size(o):
        return len(json.dumps(o, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    out["captured_by"] = captured_by
    while size(out) > CAP_BYTES and out.get("items"):
        out["items"].pop()
        truncated = True
        notes.append("items were cut to fit 4096 bytes")
    if size(out) > CAP_BYTES:
        for it in reversed(out.get("items", [])):
            it.pop("version", None)
            it.pop("tokens", None)
            if size(out) <= CAP_BYTES:
                break
    if truncated:
        out["truncated"] = True
    ordered = {"v": 1}
    for k in ("items", "retrieval", "instruction", "tokens_in", "truncated", "captured_by"):
        if k in out:
            ordered[k] = out[k]
    return ordered, sorted(set(notes))


# ── the server ────────────────────────────────────────────────────────────────────────────────────
class FakeProvy:
    """Collects what the three span-writing doors would store: one row per span."""

    def __init__(self, now: Optional[datetime] = None, empty_retriever_means_zero: bool = False, log_field_map: Optional[dict] = None):
        self.now = now or datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
        self.empty_means_zero = empty_retriever_means_zero
        self.field_map = log_field_map
        self.spans: list[dict] = []
        self.sessions: dict[str, dict] = {}
        self.outcomes: list[dict] = []
        self.requests: list[tuple[str, dict, dict]] = []
        self.notes: list[str] = []
        self.model_calls = 0          # the log door's model: must stay at zero here, nothing in the emitters calls one

    # transport entry point, shaped like ContextEmitter's transport
    def __call__(self, path: str, payload: dict, headers: dict) -> dict:
        self.requests.append((path, payload, headers))
        if path == "/api/ingest/trace":
            return self._trace(payload, headers)
        if path == "/api/otlp/v1/traces":
            return self._otlp(payload)
        if path == "/api/ingest/log":
            return self._log(payload)
        if path == "/api/ingest/session/open":
            self.sessions[payload["session_id"]] = {"open": payload}
            return {"ok": True}
        if path == "/api/ingest/session/close":
            self.sessions.setdefault(payload["session_id"], {})["close"] = payload
            return {"ok": True}
        if path == "/api/ingest/outcome":
            self.outcomes.append(payload)
            return {"ok": True}
        raise AssertionError(f"unexpected path {path}")

    def _store(self, **row) -> None:
        self.spans.append(row)

    # REST, SDK-shaped REST ----------------------------------------------------------------------
    def _trace(self, body: Any, headers: dict) -> dict:
        items = body if isinstance(body, list) else [body]
        door = "sdk" if str(headers.get("x-provy-client", "")).startswith("provy-sdk/") else "rest"
        for b in items:
            raw = b.get("context")
            if raw is None and isinstance(b.get("payload"), dict):
                raw = b["payload"].get("provy_context")
            if raw is not None and not isinstance(raw, dict):
                raise Bad400("context must be an object")
        notes: list[str] = []
        for b in items:
            raw = b.get("context")
            ctx = None
            if raw is not None:
                ctx, n = normalize_context(raw, door, self.now)
                notes += n
            self._store(session_id=b["session_id"], span_id=b.get("span_id"), agent=b["agent"], step_type=b["step_type"],
                        created_at=b.get("occurred_at"), context=ctx, ingest_door=door, input_span_ids=b.get("input_span_ids"),
                        tool_name=b.get("tool_name"), entity_id=b.get("entity_id"))
        self.notes += notes
        return {"ok": True, "notes": sorted(set(notes))}

    # OTLP ---------------------------------------------------------------------------------------
    @staticmethod
    def _attr(attrs: list, key: str) -> Optional[str]:
        for a in attrs or []:
            if a["key"] == key and a.get("value"):
                v = a["value"]
                for f in ("stringValue", "intValue", "doubleValue", "boolValue"):
                    if f in v:
                        return str(v[f])
        return None

    def _otlp(self, body: dict) -> dict:
        notes: list[str] = []
        for rs in body.get("resourceSpans", []):
            for ss in rs.get("scopeSpans", []):
                for sp in ss.get("spans", []):
                    at = sp.get("attributes", [])
                    A = lambda k: self._attr(at, k)       # noqa: E731
                    session = A("provy.session_id") or sp.get("traceId")
                    kind = A("openinference.span.kind")
                    step_type = A("provy.step_type") or ("tool_call" if kind == "RETRIEVER" else "tool_call")
                    ctx = None
                    raw_ctx = A("provy.context")
                    if raw_ctx is not None:
                        if len(raw_ctx) > 256 * 1024:
                            notes.append("provy.context was over 256 KB and was dropped")
                        else:
                            try:
                                parsed = json.loads(raw_ctx)
                            except ValueError:
                                notes.append("provy.context was not valid JSON and was dropped")
                                parsed = None
                            if parsed is not None:
                                ctx, n = normalize_context(parsed, "otlp:provy", self.now)
                                notes += n
                    elif kind == "RETRIEVER":
                        docs: dict[int, dict] = {}
                        for a in at:
                            m = re.match(r"^retrieval\.documents\.(\d+)\.document\.(id|score|metadata|content)$", a["key"])
                            if m:
                                docs.setdefault(int(m.group(1)), {})[m.group(2)] = a["value"].get("stringValue", a["value"].get("doubleValue", a["value"].get("intValue")))
                        raw: dict[str, Any] = {}
                        if docs:
                            items = []
                            for i in sorted(docs):
                                d = docs[i]
                                it: dict[str, Any] = {"kind": "document"}
                                if "id" in d:
                                    it["id"] = d["id"]
                                if "score" in d:
                                    it["score"] = float(d["score"])
                                if "content" in d:
                                    it["hash"] = _hash(d["content"])      # hashed in memory and discarded
                                if "metadata" in d:
                                    md = json.loads(d["metadata"])
                                    if "source" in md:
                                        it["source"] = md["source"]
                                    for k in ("as_of", "updated_at", "last_modified"):
                                        if k in md:
                                            it["as_of"] = md[k]
                                            break
                                    if "version" in md:
                                        it["version"] = md["version"]
                                items.append(it)
                            raw["items"] = items
                            raw["retrieval"] = {"returned": len(items)}
                        else:
                            declared = A("provy.retrieval.returned")
                            if declared is not None:
                                raw["retrieval"] = {"returned": int(declared)}
                                raw["items"] = []
                            elif self.empty_means_zero:
                                raw["retrieval"] = {"returned": 0}
                                raw["items"] = []
                        if raw:
                            ctx, n = normalize_context(raw, "otlp:openinference", self.now)
                            notes += n
                    elif A("gen_ai.system_instructions") is not None:
                        raw = {"instruction": {"hash": _hash(A("gen_ai.system_instructions"))}}   # text discarded
                        if A("provy.prompt_version"):
                            raw["instruction"]["version"] = A("provy.prompt_version")
                        if A("gen_ai.usage.input_tokens") is not None:
                            raw["tokens_in"] = {"total": int(A("gen_ai.usage.input_tokens"))}
                        ctx, n = normalize_context(raw, "otlp:genai", self.now)
                        notes += n
                    start = int(sp["startTimeUnixNano"]) / 1e9
                    created = datetime.fromtimestamp(start, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                    self._store(session_id=session, span_id=sp.get("spanId"), agent=A("provy.agent"), step_type=step_type,
                                created_at=created, context=ctx, ingest_door="otlp",
                                input_span_ids=[l["spanId"] for l in sp.get("links", [])] or None, tool_name=A("provy.tool_name"),
                                entity_id=A("provy.entity_id"))
        self.notes += notes
        return {"partialSuccess": {}, "notes": sorted(set(notes))}

    # the log door, with the model replaced by reading the emitter's JSON event lines ------------------
    def _log(self, body: dict) -> dict:
        text = body["logs"] if isinstance(body["logs"], str) else "\n".join(body["logs"])
        kept: list[str] = []
        lifted: list[tuple[str, dict, str]] = []     # (agent, raw manifest, step_type)
        notes: list[str] = []
        for line in text.split("\n"):
            lifted_line = self._extract(line)
            if lifted_line is not None:
                lifted.append(lifted_line)
                continue
            kept.append(line)
            if self.field_map:
                m = self._map_line(line)
                if m is not None:
                    lifted.append(m)
        # the "model": events are the JSON lines of the cleaned text that carry an event key
        events = []
        for line in kept:
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if isinstance(o, dict) and "event" in o:
                events.append(o)
        per_agent_count: dict[str, int] = {}
        bound: dict[int, dict] = {}
        for agent, raw, step_type in lifted:
            k = per_agent_count.get((agent, step_type), 0)
            per_agent_count[(agent, step_type)] = k + 1
            same = [i for i, e in enumerate(events) if e.get("agent") == agent and e.get("event") == step_type]
            if k >= len(same):
                same = [i for i, e in enumerate(events) if e.get("agent") == agent]
            if k < len(same):
                bound[same[k]] = raw
            else:
                notes.append(f"a context line named agent {agent} and matched no step")
        for i, e in enumerate(events):
            ctx = None
            if i in bound:
                raw, cb = bound[i]["raw"], bound[i]["captured_by"]
                ctx, n = normalize_context(raw, cb, self.now)
                notes += n
            self._store(session_id=body.get("session_id"), span_id=f"log-{len(self.spans)}", agent=e["agent"], step_type=e["event"],
                        created_at=e["ts"], context=ctx, ingest_door="log", input_span_ids=None, tool_name=e.get("tool"),
                        entity_id=e.get("entity_id"))
        self.notes += notes
        return {"ok": True, "context_notes": sorted(set(notes)), "model_text": "\n".join(kept)}

    def _extract(self, line: str):
        s = line.strip()
        obj = None
        if s.startswith("{"):
            try:
                o = json.loads(s)
            except ValueError:
                o = None
            if isinstance(o, dict) and "provy_context" in o and isinstance(o["provy_context"], dict):
                obj = o["provy_context"]
        if obj is None and "provy_context=" in line:
            start = line.index("provy_context=") + len("provy_context=")
            depth, end = 0, None
            for j, ch in enumerate(line[start:]):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = start + j + 1
                        break
            if end:
                try:
                    obj = json.loads(line[start:end])
                except ValueError:
                    obj = None
        if obj is None:
            return None
        obj = dict(obj)
        agent = obj.pop("agent", None)
        step_type = obj.pop("step_type", "decision")
        if not agent:
            return None
        return agent, {"raw": obj, "captured_by": "log:line"}, step_type

    def _map_line(self, line: str):
        s = line.strip()
        if not s.startswith("{"):
            return None
        try:
            o = json.loads(s)
        except ValueError:
            return None
        fm = self.field_map
        if not isinstance(o, dict) or fm["array"] not in o or not isinstance(o[fm["array"]], list):
            return None
        items = []
        for el in o[fm["array"]]:
            it: dict[str, Any] = {"kind": fm.get("kind", "document")}
            for tgt in ("source", "id", "as_of", "used"):
                if tgt in fm and fm[tgt] in el:
                    it[tgt] = el[fm[tgt]]
            items.append(it)
        agent = o.get(fm["agent_field"])
        if not agent:
            return None
        return agent, {"raw": {"items": items, "retrieval": {"returned": len(items)}}, "captured_by": "log:map"}, "decision"

    # reading back ---------------------------------------------------------------------------------
    def effective_context(self, session_id: str, agent: str, step_type_in: tuple = ("decision", "agent_message")) -> Optional[dict]:
        """The context of a decision step: its own, plus its one-hop input spans' (SPEC 7, merged by source and id)."""
        rows = [r for r in self.spans if r["session_id"] == session_id]
        step = next((r for r in rows if r["agent"] == agent and r["step_type"] in step_type_in), None)
        if step is None:
            return None
        parts = [step["context"]] if step["context"] else []
        for sid in step.get("input_span_ids") or []:
            for r in rows:
                if r["span_id"] == sid and r["context"]:
                    parts.append(r["context"])
        if not parts:
            return None
        merged: dict[str, Any] = {}
        items, seen = [], set()
        cbs = []
        for p in parts:
            cbs.append(p["captured_by"])
            for it in p.get("items", []):
                key = (it.get("source"), it.get("id"))
                if key not in seen:
                    seen.add(key)
                    items.append(it)
            for k in ("retrieval", "instruction", "tokens_in"):
                if k in p:
                    merged[k] = p[k]
            if "items" in p and not p["items"] and "items" not in merged:
                merged["items_present"] = True
        if items or any("items" in p for p in parts):
            merged["items"] = items
        merged["captured_by"] = cbs
        return merged
