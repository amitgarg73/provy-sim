"""Context manifests for the simulator (#1505, lane L4). ADDS this file to provy-sim/engine/.

What it does. It builds, for every simulated session, a PLAN: the six steps the session takes, the
manifest each decision step carries, the faults and decoys planted in it, the outcome it settles to,
and what Provy is expected to say about it. The plan IS the ground truth. It is written to
data/groundtruth_context_<SET>.jsonl and committed with its SHA-256 BEFORE any emitter call, and the
emitters (engine/context_emit.py) only replay it. The scorer reads the same file and never the
platform's output to decide what was injected.

The manifest shape is SPEC section 2 and nothing else. Hashing is the spec's: `sha256:` plus 64 hex
over the UTF-8 bytes of the text exactly as given (SPEC 1.1). No document text is ever put in a plan:
text is a pure function of (source, id, version) so the emitter can regenerate it for the one route
that sends content (OpenInference retriever spans, whose gateway hashes it and discards it).

What a route can carry. A fault is only planted where the route can express it (ROUTE_CAPS). A fault
a route could not carry would be a recall miss that is the simulator's doing, not Provy's.

Domain. The simulated business is a request desk. None of its words reach Provy: sources, ids,
agents and steps use the spec's generic wording (tests/test_context_groundtruth.py scans for it).
"""
from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from config import context_sets as CS

# ── what a route can carry ───────────────────────────────────────────────────────────────────────
# `exact_time`: the step's recorded time equals the planned time to the second. The log door's times
# come from a model reading log lines, so an at-the-limit decoy (which depends on the second) is only
# planted where the time is exact.
ROUTES = ("rest", "sdk", "otlp_native", "otlp_convention", "log_line", "log_map")

_ALL_KINDS = ("document", "memory", "tool_result", "input", "other")
ROUTE_CAPS: dict[str, dict] = {
    "rest":            {"kinds": _ALL_KINDS, "item_fields": ("kind", "source", "id", "as_of", "used", "hash", "score", "version", "tokens"),
                        "instruction": True, "tokens_in": ("total", "context"), "exact_time": True, "door": "rest"},
    "sdk":             {"kinds": _ALL_KINDS, "item_fields": ("kind", "source", "id", "as_of", "used", "hash", "score", "version", "tokens"),
                        "instruction": True, "tokens_in": ("total", "context"), "exact_time": True, "door": "sdk"},
    "otlp_native":     {"kinds": _ALL_KINDS, "item_fields": ("kind", "source", "id", "as_of", "used", "hash", "score", "version", "tokens"),
                        "instruction": True, "tokens_in": ("total", "context"), "exact_time": True, "door": "otlp"},
    # OpenInference retriever documents plus the GenAI instruction attribute. A document has an id, a
    # score, content (hashed by the gateway) and metadata, from which only source, as_of and version
    # are read (SPEC 4.3). `used` and `tokens` have no home there.
    "otlp_convention": {"kinds": ("document",), "item_fields": ("kind", "source", "id", "as_of", "hash", "score", "version"),
                        "instruction": True, "tokens_in": ("total",), "exact_time": True, "door": "otlp"},
    "log_line":        {"kinds": _ALL_KINDS, "item_fields": ("kind", "source", "id", "as_of", "used", "hash", "score", "version", "tokens"),
                        "instruction": True, "tokens_in": ("total", "context"), "exact_time": False, "door": "log"},
    # The fleet-declared field map names a source, id, as-of, used flag and one fixed kind.
    "log_map":         {"kinds": ("document",), "item_fields": ("kind", "source", "id", "as_of", "used"),
                        "instruction": False, "tokens_in": (), "exact_time": False, "door": "log"},
}

CAPTURED_BY = {
    "rest": "rest", "sdk": "sdk", "otlp_native": "otlp:provy", "log_line": "log:line", "log_map": "log:map",
    # the convention route is two sources: the retriever span and the instruction span
    "otlp_convention": ("otlp:openinference", "otlp:genai"),
}

# Where each decoy may be planted, decided by what the route can carry.
DECOY_ROUTES = {
    "at_limit": [r for r in ROUTES if ROUTE_CAPS[r]["exact_time"]],
    "stale_unused": ["rest", "sdk", "otlp_native", "log_line", "log_map"],
    "unapproved_unused": ["rest", "sdk", "otlp_native", "log_line", "log_map"],
    "case_source": list(ROUTES),
    "input_old": ["rest", "sdk", "otlp_native", "log_line"],
    "oi_empty_unknown": ["otlp_convention"],
}

AGENTS = ("triage", "resolver", "reviewer")
STEP_TYPE = {"triage": "decision", "resolver": "agent_message", "reviewer": "decision"}
LOOKUP_TOOL = "kb_search"
# (source, (min, max) items) per agent. Sources of kind memory and tool_result are dropped on a route
# that carries documents only.
PROFILE = {
    "triage":   [("policy-kb", (1, 2)), ("faq-kb", (1, 1)), ("agent-memory", (0, 1))],
    "resolver": [("policy-kb", (1, 2)), ("records-api", (1, 1)), ("agent-memory", (0, 1))],
    "reviewer": [("policy-kb", (1, 1)), ("faq-billing", (1, 1))],
}
SOURCE_KIND = {"policy-kb": "document", "faq-kb": "document", "faq-billing": "document",
               "records-api": "tool_result", "agent-memory": "memory"}
UNAPPROVED = ("forum-scrape", "legacy-wiki", "shared-drive")
CASE_VARIANT = {"policy-kb": "Policy-KB", "agent-memory": "Agent-Memory"}
DOC_POOL = {"policy-kb": [f"policy-{n}" for n in range(101, 109)],
            "faq-kb": [f"guide-{n}" for n in range(201, 209)],
            "faq-billing": [f"guide-{n}" for n in range(301, 307)],
            "forum-scrape": [f"page-{n}" for n in range(401, 407)],
            "legacy-wiki": [f"page-{n}" for n in range(501, 507)],
            "shared-drive": [f"file-{n}" for n in range(601, 607)]}

CHECK_FOR_KIND = {"stale": "context_freshness", "unapproved": "source_not_approved",
                  "empty": "retrieval_empty", "instruction": "instructions_changed"}
CHECK_NAMES = tuple(CHECK_FOR_KIND.values())

# step offsets in seconds from the session's start: lookup then decision, three times
_OFFSETS = {"triage": (1, 3), "resolver": (6, 9), "reviewer": (12, 15)}


# ── hashing and text ─────────────────────────────────────────────────────────────────────────────
def hash_content(text: str) -> str:
    """The spec's one hash: `sha256:` + 64 lowercase hex over the UTF-8 bytes exactly as given."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


_CONTENT_SOURCE = {v: k for k, v in CASE_VARIANT.items()}


def content_source(source: str) -> str:
    """The source a body was generated under. A case-variant decoy relabels an item's source and leaves its
    content alone, so its hash is the hash of the ORIGINAL source's text (the content did not change, only
    the label the caller wrote). The emitter needs this to regenerate the text for the OpenInference route."""
    return _CONTENT_SOURCE.get(source, source)


def item_text(source: str, item_id: str, version: str) -> str:
    """Synthetic body for an item. At least 32 characters (the SDK's hashing floor). Not real text."""
    return f"[simulated body] source={source} id={item_id} version={version}. Placeholder text for hashing only."


def instruction_text(agent: str, version: str) -> str:
    return f"[simulated instructions] agent={agent} version={version}. Placeholder text for hashing only."


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.000Z").replace(tzinfo=timezone.utc)


def span_id(session_id: str, span: str) -> str:
    return hashlib.sha256(f"{session_id}:{span}".encode()).hexdigest()[:16]


def _version_for(as_of: datetime) -> str:
    return "v" + str(1 + as_of.toordinal() % 9)


# ── one item ─────────────────────────────────────────────────────────────────────────────────────
def make_item(rng: random.Random, source: str, at: datetime, *, age: Optional[timedelta] = None, used: Optional[bool] = None,
              item_id: Optional[str] = None, kind: Optional[str] = None, entity: str = "") -> dict:
    kind = kind or SOURCE_KIND.get(source, "document")
    if item_id is None:
        if kind == "tool_result":
            item_id = f"record-{entity.split('-')[-1] if entity else rng.randint(100000, 999999)}"
        elif kind == "memory":
            item_id = f"note-{rng.randint(1000, 9999)}"
        else:
            item_id = rng.choice(DOC_POOL.get(source, [f"doc-{rng.randint(100, 999)}"]))
    if age is None:
        if kind == "tool_result":
            age = timedelta(minutes=rng.randint(5, 600))
        elif kind == "memory":
            age = timedelta(days=rng.randint(1, 20), seconds=rng.randint(0, 86399))
        else:
            age = timedelta(days=rng.randint(1, 24), seconds=rng.randint(0, 86399))
    as_of = at - age
    as_of = as_of.replace(microsecond=0)
    version = _version_for(as_of)
    item = {
        "kind": kind, "source": source, "id": item_id, "as_of": iso(as_of),
        "used": (rng.random() > 0.2) if used is None else used,
        "hash": hash_content(item_text(source, item_id, version)),
        "score": round(rng.uniform(0.55, 0.97), 4), "version": version, "tokens": rng.randint(80, 600),
    }
    return item


def build_manifest(items: Optional[list[dict]], returned: Optional[int], instruction: Optional[dict], tokens_in: Optional[dict]) -> dict:
    """The logical manifest (SPEC 2), WITHOUT `v` and `captured_by`: the server writes both."""
    m: dict[str, Any] = {}
    if items is not None:
        m["items"] = items
    if returned is not None:
        m["retrieval"] = {"returned": returned}
    if instruction is not None:
        m["instruction"] = instruction
    if tokens_in is not None:
        m["tokens_in"] = tokens_in
    return m


def instruction_for(agent: str, version: str) -> dict:
    return {"version": f"{agent}-{version}", "hash": hash_content(instruction_text(agent, version))}


# ── projection onto a route ──────────────────────────────────────────────────────────────────────
def project_item(item: dict, route: str) -> dict:
    keep = ROUTE_CAPS[route]["item_fields"]
    return {k: item[k] for k in keep if k in item}


def route_manifests(step: dict, route: str) -> dict[str, dict]:
    """What Provy should STORE for this decision step on this route, by the span it sits on.

    Keys are plan span keys (`s2`...). For every route but the convention route that is one manifest on
    the decision span. The convention route splits it: items and the retrieval count sit on the
    retriever span (OpenInference), the instruction and the token count on the decision span (GenAI).
    `captured_by` is included so the equivalence test can assert it per route.
    """
    m = step.get("manifest")
    if m is None:
        return {}
    caps = ROUTE_CAPS[route]
    out: dict[str, dict] = {}
    items = m.get("items")
    projected_items = [project_item(i, route) for i in items if i["kind"] in caps["kinds"]] if items is not None else None
    ret = m.get("retrieval")
    instr = m.get("instruction") if caps["instruction"] else None
    tin = None
    if m.get("tokens_in") and caps["tokens_in"]:
        tin = {k: v for k, v in m["tokens_in"].items() if k in caps["tokens_in"]}
    if route == "otlp_convention":
        lookup_span = step["input_spans"][0]
        if projected_items is not None or ret is not None:
            doc: dict[str, Any] = {}
            if projected_items is not None:
                doc["items"] = projected_items
            if ret is not None:
                doc["retrieval"] = {"returned": ret["returned"]}
            doc["captured_by"] = "otlp:openinference"
            out[lookup_span] = doc
        dec: dict[str, Any] = {}
        if instr:
            dec["instruction"] = instr
        if tin:
            dec["tokens_in"] = tin
        if dec:
            dec["captured_by"] = "otlp:genai"
            out[step["span"]] = dec
        return out
    doc = {}
    if projected_items is not None:
        doc["items"] = projected_items
    if ret is not None:
        doc["retrieval"] = {"returned": ret["returned"]}
    if instr:
        doc["instruction"] = instr
    if tin:
        doc["tokens_in"] = tin
    if route == "log_map" and projected_items is not None and ret is None:
        doc["retrieval"] = {"returned": len(projected_items)}
    if doc:
        doc["captured_by"] = CAPTURED_BY[route]
        # The map line has no step type, so it binds to the agent's `decision` event, or to the agent's first
        # event when it has none (SPEC 4.4). For an agent_message agent that first event is the lookup.
        key = step["input_spans"][0] if (route == "log_map" and step["step_type"] != "decision") else step["span"]
        out[key] = doc
    return out


def wire_manifest(step: dict, route: str) -> Optional[dict]:
    """The manifest object a REST, SDK, native OTLP or whole-line log caller sends for a decision step.
    `captured_by` is not sent (the server discards it) and neither is `v`."""
    got = route_manifests(step, route).get(step["span"])
    if not got:
        return None
    return {k: v for k, v in got.items() if k != "captured_by"}


# ── planning ─────────────────────────────────────────────────────────────────────────────────────
class _Fleet:
    """Mutable state while one fleet's sessions are planned."""

    def __init__(self, set_name: str, spec: dict, rng: random.Random, start: datetime, with_manifests: bool = True):
        self.set_name, self.spec, self.rng, self.with_manifests = set_name, spec, rng, with_manifests
        self.key = spec["key"]
        self.n = spec["sessions"]
        self.start = start
        self.entities: set[str] = set()


def _session_times(n: int, start: datetime, days: int, rng: random.Random) -> list[datetime]:
    gap = (days * 86400) // n
    return [start + timedelta(seconds=i * gap + rng.randint(0, min(900, gap // 3))) for i in range(n)]


def _pool(rng: random.Random, candidates: list[int], k: int, taken: set[int]) -> list[int]:
    free = [c for c in candidates if c not in taken]
    if len(free) < k:
        raise ValueError(f"cannot place {k} items among {len(free)} free sessions")
    pick = rng.sample(free, k)
    taken.update(pick)
    return sorted(pick)


def _clean_step_manifest(fleet: _Fleet, agent: str, route: str, at: datetime, entity: str, instr_version: Optional[str],
                         with_instruction: bool) -> dict:
    rng = fleet.rng
    caps = ROUTE_CAPS[route]
    items: list[dict] = []
    for source, (lo, hi) in PROFILE[agent]:
        if SOURCE_KIND[source] not in caps["kinds"]:
            continue
        for _ in range(rng.randint(lo, hi)):
            items.append(make_item(rng, source, at, entity=entity))
    # keep ids within a step unique per source
    seen: set[tuple[str, str]] = set()
    uniq = []
    for it in items:
        k = (it["source"], it["id"])
        if k not in seen:
            seen.add(k)
            uniq.append(it)
    items = uniq
    instruction = instruction_for(agent, instr_version) if (with_instruction and instr_version and caps["instruction"]) else None
    total = rng.randint(900, 3200)
    tokens_in = {"total": total, "context": min(total - 100, sum(i["tokens"] for i in items))}
    return build_manifest(items, len(items), instruction, tokens_in)


def _bindable_agents(route: str, agents: tuple) -> tuple:
    """Agents a fault or decoy may be planted on, for this route.

    A fleet-declared field map names no step type, so the log door binds its line to the agent's `decision`
    event, and where the agent has none to that agent's FIRST event (SPEC 4.4: "if the agent has no event of
    that type in the batch, the k-th event of that agent"). An agent whose deciding step is an agent_message
    therefore gets the map's manifest on its retrieval step, not on the step that decided. A fault planted
    there would be judged on the wrong step by the spec's own rule, so none is planted: faults and decoys on
    the map route go only to agents whose deciding step is a `decision`."""
    if route != "log_map":
        return agents
    return tuple(a for a in agents if STEP_TYPE[a] == "decision")


def _decision_time(start: datetime, agent: str) -> datetime:
    return start + timedelta(seconds=_OFFSETS[agent][1])


def _fault_stale(fleet: _Fleet, step: dict, at: datetime) -> dict:
    rng = fleet.rng
    m = step["manifest"]
    docs = [i for i in m["items"] if i["kind"] == "document" and i["source"] in ("policy-kb", "faq-kb", "faq-billing")]
    item = rng.choice(docs)
    age = timedelta(days=CS.LIMIT_DAYS + rng.randint(3, 90), seconds=rng.randint(0, 86399))
    item["as_of"] = iso((at - age).replace(microsecond=0))
    item["version"] = _version_for(parse_iso(item["as_of"]))
    item["hash"] = hash_content(item_text(item["source"], item["id"], item["version"]))
    item["used"] = True
    age_days = round((at - parse_iso(item["as_of"])).total_seconds() / 86400, 3)
    return {"kind": "stale", "agent": step["agent"], "step": step["step_type"], "source": item["source"], "id": item["id"],
            "as_of": item["as_of"], "age_days": age_days, "limit_days": CS.LIMIT_DAYS}


def _fault_unapproved(fleet: _Fleet, step: dict, at: datetime, entity: str) -> dict:
    rng = fleet.rng
    m = step["manifest"]
    source = rng.choice(UNAPPROVED)
    new = make_item(rng, source, at, kind="document", used=True, entity=entity)
    pos = rng.randrange(len(m["items"]) + 1)
    m["items"].insert(pos, new)
    m["retrieval"] = {"returned": len(m["items"])}
    return {"kind": "unapproved", "agent": step["agent"], "step": step["step_type"], "source": source, "id": new["id"]}


def _make_empty(step: dict) -> None:
    m = step["manifest"]
    m["items"] = []
    m["retrieval"] = {"returned": 0}
    if "tokens_in" in m:
        m["tokens_in"]["context"] = 0


def _fault_empty(step: dict) -> dict:
    _make_empty(step)
    return {"kind": "empty", "agent": step["agent"], "step": step["step_type"]}


def _decoy_at_limit(fleet: _Fleet, step: dict, at: datetime) -> dict:
    m = step["manifest"]
    item = next(i for i in m["items"] if i["kind"] == "document" and i["source"] == "policy-kb")
    item["as_of"] = iso(at - timedelta(days=CS.LIMIT_DAYS))
    item["version"] = _version_for(parse_iso(item["as_of"]))
    item["hash"] = hash_content(item_text(item["source"], item["id"], item["version"]))
    item["used"] = True
    return {"kind": "at_limit", "agent": step["agent"], "source": item["source"], "id": item["id"], "age_days": float(CS.LIMIT_DAYS)}


def _decoy_stale_unused(fleet: _Fleet, step: dict, at: datetime) -> dict:
    rng = fleet.rng
    m = step["manifest"]
    item = next(i for i in m["items"] if i["kind"] == "document")
    item["as_of"] = iso((at - timedelta(days=CS.LIMIT_DAYS + rng.randint(10, 80), seconds=rng.randint(0, 86399))).replace(microsecond=0))
    item["version"] = _version_for(parse_iso(item["as_of"]))
    item["hash"] = hash_content(item_text(item["source"], item["id"], item["version"]))
    item["used"] = False
    return {"kind": "stale_unused", "agent": step["agent"], "source": item["source"], "id": item["id"]}


def _decoy_unapproved_unused(fleet: _Fleet, step: dict, at: datetime, entity: str) -> dict:
    rng = fleet.rng
    m = step["manifest"]
    source = rng.choice(UNAPPROVED)
    new = make_item(rng, source, at, kind="document", used=False, entity=entity)
    m["items"].append(new)
    m["retrieval"] = {"returned": len(m["items"])}
    return {"kind": "unapproved_unused", "agent": step["agent"], "source": source, "id": new["id"]}


def _decoy_case_source(fleet: _Fleet, step: dict) -> dict:
    m = step["manifest"]
    item = next(i for i in m["items"] if i["source"] in CASE_VARIANT)
    original = item["source"]
    item["source"] = CASE_VARIANT[original]
    item["used"] = True
    return {"kind": "case_source", "agent": step["agent"], "source": item["source"], "approved_as": original, "id": item["id"]}


def _decoy_input_old(fleet: _Fleet, step: dict, at: datetime) -> dict:
    m = step["manifest"]
    it = {"kind": "input", "source": "request-intake", "id": "request-body", "as_of": iso(at - timedelta(days=400)),
          "used": True, "hash": hash_content(item_text("request-intake", "request-body", "v1")), "score": 0.9, "version": "v1", "tokens": 120}
    m["items"].append(it)
    m["retrieval"] = {"returned": len(m["items"])}
    return {"kind": "input_old", "agent": step["agent"], "source": "request-intake", "id": "request-body"}


def _decoy_oi_empty_unknown(step: dict) -> dict:
    m = step["manifest"]
    m.pop("items", None)
    m.pop("retrieval", None)
    if "tokens_in" in m:
        m["tokens_in"]["context"] = 0
    step["retriever_silent"] = True      # the retriever span carries no documents and no count
    return {"kind": "oi_empty_unknown", "agent": step["agent"]}


def plan_fleet(set_name: str, spec: dict, rng: random.Random, start: datetime, days: int = 39,
               with_manifests: bool = True, forced_routes: Optional[list[str]] = None, warmup: int = CS.WARMUP_SESSIONS) -> list[dict]:
    """Plan every session of one fleet. Deterministic from `rng`."""
    fleet = _Fleet(set_name, spec, rng, start, with_manifests)
    n = spec["sessions"]
    pattern = forced_routes or spec["routes"]
    routes = [pattern[i % len(pattern)] for i in range(n)]
    times = _session_times(n, start, days, rng)
    counts = spec["faults"]
    decoy_counts = spec.get("decoys", {})
    taken: set[int] = set()
    instr_plan = {a: [] for a in AGENTS}                    # agent -> [(from_session, version, effect)]
    version_of = {a: "v1" for a in AGENTS}

    # ── instruction changes first: they change what every later session carries ────────────────
    changes = []
    n_change = counts.get("instruction", 0)
    if n_change:
        lo1 = max(warmup + 5, int(0.28 * n))
        windows = [(lo1, max(lo1 + 3, int(0.45 * n))), (int(0.55 * n), int(0.68 * n)), (int(0.76 * n), int(0.86 * n))]
        effects = ["worse", "none", "none"]
        rng.shuffle(effects)
        agents = list(AGENTS)
        rng.shuffle(agents)
        for j in range(n_change):
            lo, hi = windows[j % len(windows)]
            k = rng.randint(lo, hi)
            agent = agents[j % len(agents)]
            capable = [i for i in range(k, n) if ROUTE_CAPS[routes[i]]["instruction"]]
            frm = capable[0]
            changes.append({"agent": agent, "requested": k, "from_session": frm, "effect": effects[j % len(effects)], "from_version": "v1", "to_version": "v2"})
            taken.add(frm)
            instr_plan[agent].append((frm, "v2"))

    def version_at(agent: str, i: int) -> str:
        if spec.get("quiet") and agent == "resolver":
            return "v1" if i % 2 == 0 else "v2"          # a standing A/B: both versions in use from the start
        v = "v1"
        for frm, ver in instr_plan[agent]:
            if i >= frm:
                v = ver
        return v

    # ── choose sessions for faults and decoys ───────────────────────────────────────────────────
    eligible = [i for i in range(warmup, n)]
    fault_slots: dict[str, list[int]] = {}
    for kind in ("stale", "unapproved", "empty"):
        fault_slots[kind] = _pool(rng, eligible, counts.get(kind, 0), taken)
    decoy_slots: dict[str, list[int]] = {}
    for dk, cnt in decoy_counts.items():
        ok = [i for i in range(0, n) if routes[i] in DECOY_ROUTES[dk]]
        if not ok:
            continue          # no route of this fleet can carry the decoy: it does not apply here
        decoy_slots[dk] = _pool(rng, ok, cnt, taken)

    # ── outcome plan ────────────────────────────────────────────────────────────────────────────
    settles: dict[int, str] = {}
    for kind in ("stale", "unapproved", "empty"):
        slots = list(fault_slots[kind])
        bad = rng.sample(slots, round(CS.FAULT_BAD_SETTLE_SHARE * len(slots))) if slots else []
        for i in slots:
            settles[i] = "bad" if i in bad else "good"
    # the first repeat of a new hash on a capable route, after each change
    first_repeat: dict[int, dict] = {}
    for ch in changes:
        later = [i for i in range(ch["from_session"] + 1, n) if ROUTE_CAPS[routes[i]]["instruction"]]
        if later:
            first_repeat[later[0]] = ch
    worse_window: dict[int, dict] = {}
    for ch in changes:
        if ch["effect"] == "worse":
            for i in range(ch["from_session"], min(n, ch["from_session"] + 40)):
                worse_window[i] = ch
    fault_set = {i for v in fault_slots.values() for i in v} | {ch["from_session"] for ch in changes}
    background_n = round(CS.BACKGROUND_FAIL_RATE * n)
    bg_candidates = [i for i in range(n) if i not in fault_set and i not in worse_window]
    background = set(rng.sample(bg_candidates, background_n)) if background_n else set()
    worse_fail = {i for i in worse_window if i not in fault_set and rng.random() < CS.WORSE_CHANGE_FAIL_RATE}
    for ch in changes:
        settles[ch["from_session"]] = "bad" if ch["effect"] == "worse" and rng.random() < CS.WORSE_CHANGE_FAIL_RATE + 0.2 else "good"

    # Work-item ids are drawn up front from the structural stream, and every session's CONTENT (items,
    # scores, which agent a fault lands on) comes from its own stream. That keeps two plans that share
    # a seed (the two capture fleets) identical in times, ids and outcomes while one of them carries
    # no manifest at all.
    entities: list[str] = []
    while len(entities) < n:
        e = f"REQ-{rng.randint(100000, 999999)}"
        if e not in entities:
            entities.append(e)

    content_seed = rng.getrandbits(64)
    fault_kind_at = {i: k for k, v in fault_slots.items() for i in v}
    fault_kind_at.update({ch["from_session"]: "instruction" for ch in changes})
    plans: list[dict] = []
    for i in range(n):
        route = routes[i]
        caps = ROUTE_CAPS[route]
        t0 = times[i]
        session_id = f"{set_name.lower()}-{spec['key']}-{i:04d}"
        entity = entities[i]
        crng = random.Random(f"{content_seed}:{i}")
        fleet.rng = crng
        doc_only = caps["kinds"] == ("document",)
        steps: list[dict] = []
        faults: list[dict] = []
        decoys: list[dict] = []
        n_span = 0

        def next_span() -> str:
            nonlocal n_span
            n_span += 1
            return f"s{n_span}"

        decision_steps: dict[str, dict] = {}
        for agent in AGENTS:
            lk_off, dc_off = _OFFSETS[agent]
            lk_span, dc_span = next_span(), next_span()
            lookup = {"span": lk_span, "agent": agent, "step_type": "tool_call", "tool_name": LOOKUP_TOOL,
                      "at": iso(t0 + timedelta(seconds=lk_off)), "outcome": "success", "input_spans": []}
            at = t0 + timedelta(seconds=dc_off)
            in_session_instr = version_at(agent, i)
            manifest = _clean_step_manifest(fleet, agent, route, at, entity, in_session_instr, caps["instruction"]) if with_manifests else None
            step = {"span": dc_span, "agent": agent, "step_type": STEP_TYPE[agent], "tool_name": None,
                    "at": iso(at), "outcome": "classified" if agent == "triage" else ("drafted" if agent == "resolver" else "approved"),
                    "input_spans": [lk_span], "manifest": manifest}
            if agent == "reviewer":
                step["claim"] = {"signal": "work_held_up", "value": True, "confidence": 0.9}
            steps.extend([lookup, step])
            decision_steps[agent] = step

        # ── plant ────────────────────────────────────────────────────────────────────────────
        if with_manifests:
            for kind in ("stale", "unapproved", "empty"):
                if i in fault_slots[kind]:
                    agent = crng.choice(_bindable_agents(route, AGENTS))
                    st = decision_steps[agent]
                    at = parse_iso(st["at"])
                    if kind == "stale":
                        f = _fault_stale(fleet, st, at)
                    elif kind == "unapproved":
                        f = _fault_unapproved(fleet, st, at, entity)
                    else:
                        f = _fault_empty(st)
                    f["settles"] = settles[i]
                    faults.append(f)
            for ch in changes:
                if ch["from_session"] == i:
                    faults.append({"kind": "instruction", "agent": ch["agent"], "step": STEP_TYPE[ch["agent"]], "from_version": ch["from_version"],
                                   "to_version": ch["to_version"], "effect": ch["effect"], "settles": settles[i]})
            for dk, slots in decoy_slots.items():
                if i not in slots:
                    continue
                agent = crng.choice(_bindable_agents(route, AGENTS))
                st = decision_steps[agent]
                at = parse_iso(st["at"])
                if dk == "at_limit":
                    ag = "triage"  # policy-kb is on every agent's profile; triage is the stable choice
                    st = decision_steps[ag]
                    d = _decoy_at_limit(fleet, st, parse_iso(st["at"]))
                elif dk == "stale_unused":
                    d = _decoy_stale_unused(fleet, st, at)
                elif dk == "unapproved_unused":
                    d = _decoy_unapproved_unused(fleet, st, at, entity)
                elif dk == "case_source":
                    st = decision_steps[crng.choice(_bindable_agents(route, ("triage", "resolver")))]
                    d = _decoy_case_source(fleet, st)
                elif dk == "input_old":
                    d = _decoy_input_old(fleet, st, at)
                elif dk == "oi_empty_unknown":
                    d = _decoy_oi_empty_unknown(st)
                else:
                    raise ValueError(dk)
                decoys.append(d)
            if spec.get("quiet"):
                for agent in _bindable_agents(route, ("triage", "resolver")):
                    if i not in {x for ss in decoy_slots.values() for x in ss} and crng.random() < CS.QUIET_EMPTY_RATE:
                        st = decision_steps[agent]
                        _make_empty(st)
                        decoys.append({"kind": "quiet_empty", "agent": agent})

        # ── outcome ────────────────────────────────────────────────────────────────────────────
        if i in settles:
            label = "fail" if settles[i] == "bad" else "success"
            kind0 = fault_kind_at.get(i)
            cause = "context" if (kind0 in ("stale", "unapproved", "empty") and settles[i] == "bad") else (
                "instruction_change" if (kind0 == "instruction" and settles[i] == "bad") else None)
        elif i in background:
            label, cause = "fail", "background"
        elif i in worse_fail:
            label, cause = "fail", "instruction_change"
        else:
            label, cause = "success", None

        # ── classification and expectations ────────────────────────────────────────────────────
        if faults:
            cls = "fault"
        elif decoys:
            cls = "decoy"
        elif i in first_repeat:
            cls = "post_change_first_repeat"
        else:
            cls = "clean"
        acceptable = sorted({f["kind"] for f in faults})
        if i in worse_window:
            acceptable = sorted(set(acceptable) | {"instruction"})
        bar_excluded = None
        if spec.get("quiet") and i < 6:
            bar_excluded = "ab_warmup"
        if any(d["kind"] == "case_source" for d in decoys):
            bar_excluded = "case_source_spec_conflict"
        if cls == "post_change_first_repeat":
            bar_excluded = "post_change_first_repeat"
        expect = _expectations(faults, label, cause, with_manifests, acceptable)
        plans.append({
            "v": 1, "set": set_name, "fleet": spec["key"], "fleet_label": spec["label"], "session_index": i, "session_id": session_id,
            "entity_id": entity, "session_type": "request", "occurred_at": iso(t0), "route": route if with_manifests else "none",
            "door": caps["door"] if with_manifests else "none",
            "wire": ("kv" if (route == "log_line" and (sum(1 for r in routes[:i] if r == "log_line") % 2 == 1)) else "json") if route == "log_line" else None,
            "class": cls, "faults": faults, "decoys": decoys, "bar_excluded": bar_excluded,
            "outcome": {"label": label, "cause": cause, "signals": {"work_held_up": label == "success"}},
            "steps": steps, "expect": expect, "fleet_quiet": bool(spec.get("quiet")),
        })
    return plans


def _expectations(faults: list[dict], label: str, cause: Optional[str], with_manifests: bool, acceptable: list[str]) -> dict:
    exp: dict[str, Any] = {"failed_checks": [], "context_cause": None, "quote_contains": [], "incident_from_cause_route": False,
                           "acceptable_causes": acceptable if with_manifests else []}
    for f in faults:
        exp["failed_checks"].append(CHECK_FOR_KIND[f["kind"]])
    exp["failed_checks"] = sorted(set(exp["failed_checks"]))
    if faults and faults[0]["kind"] in ("stale", "unapproved", "empty") and label == "fail":
        f = faults[0]
        exp["context_cause"] = f["kind"]
        exp["incident_from_cause_route"] = True
        if f["kind"] == "stale":
            exp["quote_contains"] = [f"{f['source']}/{f['id']}", f"{f['limit_days']} days", f["agent"]]
        elif f["kind"] == "unapproved":
            exp["quote_contains"] = [f"{f['source']}/{f['id']}", "approved list", f["agent"]]
        else:
            exp["quote_contains"] = ["retrieved 0 items", f["agent"]]
    return exp


# ── set generation ───────────────────────────────────────────────────────────────────────────────
SET_START = datetime(2026, 8, 20, 6, 0, 0, tzinfo=timezone.utc)


def generate_set(set_name: str) -> list[dict]:
    if set_name in CS.SETS:
        spec = CS.SETS[set_name]
        out: list[dict] = []
        for fleet in spec["fleets"]:
            rng = random.Random(f"{spec['seed']}:{fleet['key']}")
            out.extend(plan_fleet(set_name, fleet, rng, SET_START))
        return out
    if set_name in CS.CAPTURE:
        return generate_capture(set_name)
    raise KeyError(set_name)


def generate_capture(set_name: str) -> list[dict]:
    spec = CS.CAPTURE[set_name]
    fleet = {"key": "capture", "label": spec["label"], "routes": ["rest"], "sessions": spec["sessions"], "faults": spec["faults"],
             "decoys": {"at_limit": 2, "stale_unused": 2, "unapproved_unused": 2, "case_source": 1, "input_old": 1}, "quiet": False}
    # CAP-A and CAP-B share one seed on purpose: the same sessions, the same times, the same
    # outcomes. B carries no manifest and no fault, so every surface that must say "unknown" has the
    # same work item to say it about.
    rng = random.Random(f"{spec['seed']}:capture")
    plans = plan_fleet(set_name, fleet, rng, SET_START + timedelta(days=5), days=30, with_manifests=spec["manifests"], warmup=25,
                       forced_routes=["rest"])
    if spec["manifests"]:
        # A real fleet does not send a manifest on every step, and a manifest does not always say how old a fact is.
        # Some clean sessions send none (unknown, never a pass) and some send items with no as_of (no age to judge),
        # so the coverage card has a headline, a dates line and a gap to show. Chosen from a stream of their own so
        # the structure shared with CAP-B does not move. Faults, decoys and the post-change session are left whole.
        srng = random.Random(f"{spec['seed']}:capture-shape")
        clean = [i for i, p in enumerate(plans) if p["class"] == "clean"]
        picks = srng.sample(clean, spec.get("silent", 0) + spec.get("undated", 0))
        for i in picks[: spec.get("silent", 0)]:
            for s in plans[i]["steps"]:
                s["manifest"] = None
            plans[i]["shape"] = "silent"
        for i in picks[spec.get("silent", 0):]:
            for s in plans[i]["steps"]:
                for it in (s.get("manifest") or {}).get("items", []):
                    it.pop("as_of", None)
            plans[i]["shape"] = "undated"
    for p in plans:
        p["span_ids"] = {s["span"]: span_id(p["session_id"], s["span"]) for s in p["steps"]}
    return plans


# ── files and hashes ─────────────────────────────────────────────────────────────────────────────
def dumps_plans(plans: list[dict]) -> bytes:
    """Canonical bytes: one JSON object per line, sorted keys, compact separators, newline-terminated."""
    return b"".join(json.dumps(p, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n" for p in plans)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def load_plans(path: str) -> list[dict]:
    with open(path, "rb") as f:
        return [json.loads(line) for line in f.read().decode().splitlines() if line.strip()]


# ── what Provy is expected to count ──────────────────────────────────────────────────────────────
def expected_coverage(plans: list[dict], as_of: Optional[datetime] = None, window_days: Optional[int] = None) -> dict:
    """Coverage by ingest door, as SPEC 9 defines it, from the plan alone.

    decision step = step_type decision or agent_message. withManifest = the span's own context is not
    null. withAges = at least one item has as_of. withRetrieval = retrieval.returned recorded.
    withInstruction = instruction recorded. viaLinks = no own manifest and the declared input step has
    one. [unverified: L3's exact reading of withAges for a manifest that sits on the linked span.]
    """
    tally: dict[str, dict] = {}
    for p in plans:
        if p["door"] == "none":
            door = "none"
        else:
            door = p["door"]
        if as_of is not None and window_days is not None:
            if parse_iso(p["occurred_at"]) < as_of - timedelta(days=window_days):
                continue
        by_span = {s["span"]: s for s in p["steps"]}
        stored = {}
        for s in p["steps"]:
            if s["step_type"] in ("decision", "agent_message"):
                stored.update(route_manifests(s, p["route"]) if p["route"] != "none" else {})
        for s in p["steps"]:
            if s["step_type"] not in ("decision", "agent_message"):
                continue
            t = tally.setdefault(door, {"door": door, "decisionSteps": 0, "withManifest": 0, "withAges": 0, "withRetrieval": 0,
                                        "withInstruction": 0, "viaLinks": 0})
            t["decisionSteps"] += 1
            own = stored.get(s["span"])
            if own:
                t["withManifest"] += 1
                if any("as_of" in i for i in own.get("items", [])):
                    t["withAges"] += 1
                if "retrieval" in own:
                    t["withRetrieval"] += 1
                if "instruction" in own:
                    t["withInstruction"] += 1
            else:
                # the log door's events are written by a model and declare no input links, so nothing is via-linked there
                links = [] if p["route"].startswith("log_") else s["input_spans"]
                if any(stored.get(sp) for sp in links):
                    t["viaLinks"] += 1
    return tally
