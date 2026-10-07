"""The context emitters, route by route, against a fake server that applies the SPEC 2 rules (#1505, L4).

The fake (tests/context_fake_server.py) is an independent reading of the spec, not L1's code (L1 had no
normalizer on its branch when this was written). What these tests prove is that every route delivers the
SAME logical manifest, that what a route cannot carry is dropped by one function, and that the emitters
cannot be pointed at production.
"""
import copy
import json
import os
import re

import pytest

import _context_path  # noqa: F401
from context_fake_server import FakeProvy, normalize_context
from engine import context as C
from engine import context_emit as E
from engine import context_truth as T
from engine.emitter import ProductionTargetRefused

DATA = os.path.join(_context_path.TREE, "data")
TREE = _context_path.TREE
ROUTES = list(C.ROUTES)


def plans(name):
    return C.load_plans(os.path.join(DATA, T.truth_filename(name)))


def emitter(fake, key="k"):
    return E.ContextEmitter(ingest_key=key, base_url="https://dev.provy.ai", transport=fake)


def fake_for(route):
    return FakeProvy(log_field_map=__import__("config.context_sets", fromlist=["x"]).DECLARATIONS["fleet_declarations"]["log"]["context_log_fields"])


def stored_by_span(fake, plan, route):
    """plan span key -> the stored row. REST and OTLP carry the span id; the log door mints its own, so a
    log session is matched by (agent, step type), which is unique within a session."""
    rows = [r for r in fake.spans if r["session_id"] == plan["session_id"]]
    out = {}
    for s in plan["steps"]:
        if route in ("log_line", "log_map"):
            out[s["span"]] = next(r for r in rows if r["agent"] == s["agent"] and r["step_type"] == s["step_type"])
        else:
            out[s["span"]] = next(r for r in rows if r["span_id"] == E._span_id(plan, s["span"]))
    return out


def expected_for(plan, route):
    exp = {}
    for s in plan["steps"]:
        if s["step_type"] != "tool_call":
            for span, m in C.route_manifests(s, route).items():
                exp[span] = {"v": 1, **m}
    return exp


# ── production can never be the target ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("url", ["https://provy.ai", "https://www.provy.ai/api/ingest/trace", "https://provyai.vercel.app",
                                  "https://provy-amit-garg-s-projects.vercel.app", "https://provy-git-main-amit-garg-s-projects.vercel.app",
                                  "https://example.com", "http://localhost:3000", "provy.ai"])
def test_emitter_refuses_anything_that_is_not_an_allowlisted_preprod_host(url, monkeypatch):
    monkeypatch.delenv("PROVY_ALLOW_PROD", raising=False)
    with pytest.raises(ProductionTargetRefused):
        E.ContextEmitter(ingest_key="k", base_url=url)


def test_the_prod_escape_hatch_of_the_base_class_does_not_apply(monkeypatch):
    monkeypatch.setenv("PROVY_ALLOW_PROD", "1")
    with pytest.raises(ProductionTargetRefused):
        E.ContextEmitter(ingest_key="k", base_url="https://provy.ai")


@pytest.mark.parametrize("url", ["https://dev.provy.ai", "https://provydev.vercel.app"])
def test_preprod_hosts_are_accepted(url):
    assert E.ContextEmitter(ingest_key="k", base_url=url).base == url


def test_no_production_host_or_key_is_written_in_the_tree():
    prod = re.compile(r"(?<!dev\.)\bprovy\.ai\b|provyai\.vercel\.app")
    keyish = re.compile(r"eyJ[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{20,}|pk_(live|test)_|xox[bap]-|AKIA[0-9A-Z]{16}|provy_[a-z]{2,6}_[A-Za-z0-9]{24,}")
    offenders = []
    # Only the context-manifest files. Inside provy-sim TREE is the whole repo, and provy-sim's own tests name the production host in order to refuse it,
    # so the guard must not sweep them in (found on 3 Oct 2026 when the tree was first copied across, #1513).
    def is_context_file(rel):
        return "context" in rel or os.path.basename(rel) in ("capture_seed_rows.py", "merge_exports.py")
    for base, _, files in os.walk(TREE):
        if "__pycache__" in base or os.sep + ".venv" in base or os.sep + ".git" in base or os.sep + "node_modules" in base:
            continue
        for f in files:
            rel = os.path.relpath(os.path.join(base, f), TREE)
            if f.endswith((".py", ".md", ".json")) and f != "test_context_emitters.py" and is_context_file(rel):
                text = open(os.path.join(base, f), errors="ignore").read()
                if f.endswith(".py") and prod.search(text):
                    offenders.append((f, "production host"))
                if keyish.search(text):
                    offenders.append((f, "key-shaped string"))
    assert offenders == []


def test_the_emitter_reads_no_key_from_a_file_and_names_its_variable_only():
    from config import context_sets as CS
    assert CS.key_env_name("CM-B", "otlp_native") == "PROVY_KEY_CM_B_OTLP_NATIVE"
    src = open(os.path.join(TREE, "engine", "context_emit.py")).read()
    assert "provy.config" not in src and "provy.preprod.env" not in src
    assert not re.search(r"open\([^)]*(config|\.env|secret)", src)


# ── every session of every set, over its own route, stores what the spec says ─────────────────────
@pytest.mark.parametrize("name", ["CM-A", "CM-B", "CM-C", "CM-D"])
def test_every_planned_session_is_stored_exactly_as_its_route_should_carry_it(name):
    for plan in plans(name):
        fake = fake_for(plan["route"])
        emitter(fake).emit_plan(plan)
        got = stored_by_span(fake, plan, plan["route"])
        exp = expected_for(plan, plan["route"])
        for span, row in got.items():
            step = next(s for s in plan["steps"] if s["span"] == span)
            if span in exp:
                assert row["context"] == exp[span], (plan["session_id"], plan["route"], span)
            else:
                assert row["context"] is None, (plan["session_id"], plan["route"], span, row["context"])
            door = C.ROUTE_CAPS[plan["route"]]["door"]
            assert row["ingest_door"] == ("otlp" if door == "otlp" else door if door != "sdk" else "sdk")
            if C.ROUTE_CAPS[plan["route"]]["exact_time"]:
                assert row["created_at"] == step["at"]


# ── the same logical manifest on every route ──────────────────────────────────────────────────────
def _pick(name, pred):
    return next(p for p in plans(name) if pred(p))


def _common(m):
    """Only what every route can carry: (kind, source, id, as_of) in order, and the returned count."""
    if m is None:
        return None
    items = [(i["kind"], i["source"], i["id"], i["as_of"]) for i in m.get("items", [])]
    return {"items": items, "returned": (m.get("retrieval") or {}).get("returned")}


def _documents_only(plan):
    """The same session with the document items only, so all six routes can carry every item."""
    p = copy.deepcopy(plan)
    for s in p["steps"]:
        m = s.get("manifest")
        if m and "items" in m:
            m["items"] = [i for i in m["items"] if i["kind"] == "document"]
            m["retrieval"] = {"returned": len(m["items"])}
    return p


def _merged_stored(fake, plan, route):
    """Per agent, the effective context of the decision step (own plus one hop of input spans)."""
    out = {}
    for s in plan["steps"]:
        if s["step_type"] != "tool_call":
            out[s["agent"]] = fake.effective_context(plan["session_id"], s["agent"])
    return out


def test_one_logical_session_over_all_six_routes_stores_the_same_items():
    base = _documents_only(_pick("CM-B", lambda p: p["class"] == "clean" and p["route"] == "rest" and p["session_index"] > 50))
    results = {}
    for route in ROUTES:
        fake = fake_for(route)
        emitter(fake).emit_plan(base, route=route)
        got = _merged_stored(fake, base, route)
        results[route] = {a: _common(m) for a, m in got.items()}
    ref = results["rest"]
    assert all(v["items"] for v in ref.values())
    for route, got in results.items():
        if route == "log_map":
            # the map line has no step type, so the resolver (whose deciding step is an agent_message) gets its
            # manifest on the retrieval step, which a log-door span cannot link to: SPEC 4.4 binding, see
            # test_the_map_route_binds_an_agent_message_agent_to_its_retrieval_step
            assert got["resolver"] is None
            got = {a: v for a, v in got.items() if a != "resolver"}
            assert got == {a: v for a, v in ref.items() if a != "resolver"}
            continue
        assert got == ref, route
    # log_line in both of its forms
    for wire in ("json", "kv"):
        fake = fake_for("log_line")
        emitter(fake).emit_plan(base, route="log_line", wire=wire)
        got = {a: _common(m) for a, m in _merged_stored(fake, base, "log_line").items()}
        assert got == ref, wire


def test_the_remaining_fields_are_carried_exactly_where_the_route_map_says():
    base = _documents_only(_pick("CM-B", lambda p: p["class"] == "clean" and p["route"] == "rest" and p["session_index"] > 50))
    for route in ROUTES:
        fake = fake_for(route)
        emitter(fake).emit_plan(base, route=route)
        caps = C.ROUTE_CAPS[route]
        for agent, m in _merged_stored(fake, base, route).items():
            if m is None:
                assert route == "log_map" and agent == "resolver"
                continue
            for it in m["items"]:
                assert set(it) <= set(caps["item_fields"]), (route, set(it) - set(caps["item_fields"]))
                assert set(it) == set(caps["item_fields"]), (route, set(caps["item_fields"]) - set(it))
            assert ("instruction" in m) == caps["instruction"], route


def test_item_hash_is_the_same_whether_the_caller_computed_it_or_the_gateway_did():
    base = _documents_only(_pick("CM-B", lambda p: p["class"] == "clean" and p["route"] == "rest" and p["session_index"] > 50))
    by_route = {}
    for route in ("rest", "otlp_convention", "log_line"):
        fake = fake_for(route)
        emitter(fake).emit_plan(base, route=route)
        by_route[route] = {a: [(i["id"], i["hash"]) for i in m["items"]] for a, m in _merged_stored(fake, base, route).items()}
    assert by_route["rest"] == by_route["otlp_convention"] == by_route["log_line"]
    # the instruction hash too: computed by the caller (REST, line) or by the gateway from the GenAI attribute
    ins = {}
    for route in ("rest", "otlp_native", "otlp_convention", "log_line"):
        fake = fake_for(route)
        emitter(fake).emit_plan(base, route=route)
        ins[route] = {a: m["instruction"] for a, m in _merged_stored(fake, base, route).items()}
    assert ins["rest"] == ins["otlp_native"] == ins["otlp_convention"] == ins["log_line"]


def test_content_never_reaches_a_stored_value():
    base = _documents_only(_pick("CM-B", lambda p: p["class"] == "clean" and p["route"] == "rest" and p["session_index"] > 50))
    fake = fake_for("otlp_convention")
    emitter(fake).emit_plan(base, route="otlp_convention")
    assert "[simulated body]" not in json.dumps(fake.spans) and "[simulated instructions]" not in json.dumps(fake.spans)
    # and the REST body never carried text at all
    fake = fake_for("rest")
    em = emitter(fake)
    em.emit_plan(base, route="rest")
    assert "[simulated" not in json.dumps(em.sent)


# ── empty is a statement, unknown is not ──────────────────────────────────────────────────────────
def _empty_plan():
    p = copy.deepcopy(_pick("CM-B", lambda p: p["class"] == "clean" and p["route"] == "rest" and p["session_index"] > 50))
    p = _documents_only(p)
    st = next(s for s in p["steps"] if s["agent"] == "triage" and s["step_type"] == "decision")
    st["manifest"]["items"] = []
    st["manifest"]["retrieval"] = {"returned": 0}
    return p


@pytest.mark.parametrize("route", ROUTES)
def test_an_empty_retrieval_is_recorded_as_returned_zero_on_every_route(route):
    p = _empty_plan()
    fake = fake_for(route)
    emitter(fake).emit_plan(p, route=route)
    m = fake.effective_context(p["session_id"], "triage")
    assert m["retrieval"] == {"returned": 0}, route
    assert m.get("items") == []


def test_a_silent_retriever_is_unknown_not_empty_unless_the_fleet_declares_otherwise():
    p = _empty_plan()
    st = next(s for s in p["steps"] if s["agent"] == "triage" and s["step_type"] == "decision")
    st["retriever_silent"] = True
    st["manifest"].pop("items"), st["manifest"].pop("retrieval")
    fake = fake_for("otlp_convention")
    emitter(fake).emit_plan(p, route="otlp_convention")
    m = fake.effective_context(p["session_id"], "triage")
    assert "retrieval" not in m and "items" not in m
    # the one server rule that changes it: the fleet declares an empty retriever means zero (SPEC 5)
    declared = FakeProvy(empty_retriever_means_zero=True)
    emitter(declared).emit_plan(p, route="otlp_convention")
    assert declared.effective_context(p["session_id"], "triage")["retrieval"] == {"returned": 0}


def test_every_oi_empty_unknown_decoy_in_the_sets_is_silent_on_the_wire():
    seen = 0
    for p in plans("CM-B"):
        if any(d["kind"] == "oi_empty_unknown" for d in p["decoys"]):
            fake = fake_for("otlp_convention")
            emitter(fake).emit_plan(p)
            m = fake.effective_context(p["session_id"], next(d for d in p["decoys"])["agent"])
            assert "retrieval" not in (m or {})
            seen += 1
    assert seen == 4


# ── the log door ──────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("wire", ["json", "kv"])
def test_lifted_context_lines_never_reach_the_text_the_model_reads(wire):
    p = _pick("CM-B", lambda p: p["route"] == "log_line")
    fake = fake_for("log_line")
    emitter(fake).emit_plan(p, wire=wire)
    last = next(r for r in reversed(fake.requests) if r[0] == "/api/ingest/log")
    assert "provy_context" in last[1]["logs"]                       # it was sent
    reply = FakeProvy(log_field_map=None)._log(last[1])             # what the door would hand the model
    assert "provy_context" not in reply["model_text"]


def test_a_map_line_stays_in_the_text_and_no_model_is_called():
    p = _pick("CM-B", lambda p: p["route"] == "log_map")
    fake = fake_for("log_map")
    em = emitter(fake)
    em.emit_plan(p)
    log_requests = [r for r in fake.requests if r[0] == "/api/ingest/log"]
    assert len(log_requests) == 1
    assert '"retrieved"' in log_requests[0][1]["logs"] and "provy_context" not in log_requests[0][1]["logs"]
    assert fake.model_calls == 0


def test_a_log_session_opens_no_rest_session_and_a_rest_session_does():
    p = _pick("CM-B", lambda p: p["route"] == "log_line")
    fake = fake_for("log_line")
    emitter(fake).emit_plan(p)
    assert [r[0] for r in fake.requests] == ["/api/ingest/log"]
    q = _pick("CM-B", lambda p: p["route"] == "rest")
    fake = fake_for("rest")
    emitter(fake).emit_plan(q)
    paths = [r[0] for r in fake.requests]
    assert paths[0] == "/api/ingest/session/open" and paths[-1] == "/api/ingest/session/close" and paths.count("/api/ingest/trace") == 6


# ── SDK-shaped REST ───────────────────────────────────────────────────────────────────────────────
def test_sdk_route_sends_the_client_header_and_rest_does_not():
    base = _pick("CM-B", lambda p: p["route"] == "rest")
    for route, door in (("sdk", "sdk"), ("rest", "rest")):
        fake = fake_for(route)
        emitter(fake).emit_plan(base, route=route)
        traces = [r for r in fake.requests if r[0] == "/api/ingest/trace"]
        assert all(("x-provy-client" in r[2]) == (route == "sdk") for r in traces)
        assert {r["ingest_door"] for r in fake.spans} == {door}
        assert {r["context"]["captured_by"] for r in fake.spans if r["context"]} == {door}


def test_the_key_is_sent_in_the_ingest_header_and_never_recorded_in_what_the_emitter_keeps():
    base = _pick("CM-B", lambda p: p["route"] == "rest")
    fake = fake_for("rest")
    em = E.ContextEmitter(ingest_key="SENTINEL-KEY-VALUE", base_url="https://dev.provy.ai", transport=fake)
    em.emit_plan(base)
    assert all(r[2].get("x-provy-key") == "SENTINEL-KEY-VALUE" for r in fake.requests)
    assert "SENTINEL-KEY-VALUE" not in json.dumps(em.sent)


# ── one fixture of defects, the same notes on every door ──────────────────────────────────────────
def _defective(plan):
    p = copy.deepcopy(plan)
    st = next(s for s in p["steps"] if s["agent"] == "triage" and s["step_type"] == "decision")
    items = st["manifest"]["items"]
    while len(items) < 22:
        items.append(copy.deepcopy(items[0]) | {"id": f"extra-{len(items)}"})
    items[0]["hash"] = "sha256:nothex"
    items[1]["as_of"] = "2099-01-01T00:00:00.000Z"
    return p, st


def test_the_same_defects_give_the_same_notes_and_the_same_cut_on_every_door():
    p, st = _defective(_pick("CM-B", lambda p: p["route"] == "rest"))
    seen = {}
    for route in ("rest", "sdk", "otlp_native", "log_line"):
        fake = fake_for(route)
        emitter(fake).emit_plan(p, route=route)
        row = next(r for r in fake.spans if r["agent"] == "triage" and r["step_type"] == "decision")
        # 22 items cut to 20 by the item cap, then by the 4,096-byte cap (an item with every field is about 250 bytes)
        assert row["context"]["truncated"] is True and 10 < len(row["context"]["items"]) <= 20
        assert "hash" not in row["context"]["items"][0] and "as_of" not in row["context"]["items"][1]
        seen[route] = sorted(set(n for n in fake.notes if "hash" in n or "as_of" in n or "cut" in n))
        stored = {k: v for k, v in row["context"].items() if k != "captured_by"}
        seen[route + ":ctx"] = stored
    notes = [v for k, v in seen.items() if not k.endswith(":ctx")]
    assert all(n == notes[0] for n in notes) and len(notes[0]) == 4
    ctxs = [v for k, v in seen.items() if k.endswith(":ctx")]
    assert all(c == ctxs[0] for c in ctxs)


def test_rest_answers_400_only_for_a_context_that_is_not_an_object():
    fake = FakeProvy()
    from context_fake_server import Bad400
    with pytest.raises(Bad400):
        fake("/api/ingest/trace", {"session_id": "s", "agent": "a", "step_type": "decision", "context": "oops"}, {})
    ok = fake("/api/ingest/trace", {"session_id": "s", "agent": "a", "step_type": "decision", "context": {"items": "x", "retrieval": {"returned": 2}}}, {})
    assert ok["ok"] and fake.spans[-1]["context"]["retrieval"] == {"returned": 2}
    assert normalize_context({"captured_by": "x"}, "rest", fake.now)[0] is None


# ── decoys and faults on the wire ─────────────────────────────────────────────────────────────────
def test_planted_faults_arrive_with_the_values_the_ground_truth_states():
    n = 0
    for p in plans("CM-B"):
        for f in p["faults"]:
            if f["kind"] not in ("stale", "unapproved"):
                continue
            fake = fake_for(p["route"])
            emitter(fake).emit_plan(p)
            m = fake.effective_context(p["session_id"], f["agent"])
            hit = [i for i in m["items"] if i["source"] == f["source"] and i["id"] == f["id"]]
            assert len(hit) == 1
            if f["kind"] == "stale":
                assert hit[0]["as_of"] == f["as_of"]
            n += 1
    assert n == 48


def test_at_limit_decoys_are_exactly_the_limit_old_on_the_wire():
    n = 0
    for p in plans("CM-B"):
        for d in p["decoys"]:
            if d["kind"] != "at_limit":
                continue
            fake = fake_for(p["route"])
            emitter(fake).emit_plan(p)
            row = next(r for r in fake.spans if r["agent"] == d["agent"] and r["step_type"] in ("decision", "agent_message"))
            it = next(i for i in fake.effective_context(p["session_id"], d["agent"])["items"] if i["id"] == d["id"])
            age = C.parse_iso(row["created_at"]) - C.parse_iso(it["as_of"])
            assert age.total_seconds() == 30 * 86400
            n += 1
    assert n == 12


def test_claims_ride_the_reviewer_step_on_rest_and_otlp():
    p = _pick("CM-B", lambda p: p["route"] == "rest")
    body = E.rest_body(p, next(s for s in p["steps"] if s["agent"] == "reviewer" and s["step_type"] == "decision"), "rest")
    assert body["payload"]["provy_claim"][0]["signal"] == "work_held_up"
    o = E.otlp_body(p, "otlp_native")
    claims = [a for s in o["resourceSpans"][0]["scopeSpans"][0]["spans"] for a in s["attributes"] if a["key"] == "provy.claim"]
    assert len(claims) == 1


# ── capture fleets and replay ─────────────────────────────────────────────────────────────────────
def test_capture_sessions_are_sent_without_any_manifest_and_with_the_planned_span_ids():
    for name in ("CAP-A", "CAP-B"):
        fake = FakeProvy()
        em = emitter(fake)
        for p in plans(name)[:10]:
            em.emit_capture(p)
        assert all("context" not in r[1] for r in fake.requests if r[0] == "/api/ingest/trace")
        assert all(r["context"] is None for r in fake.spans)
        p0 = plans(name)[0]
        assert {r["span_id"] for r in fake.spans if r["session_id"] == p0["session_id"]} == set(p0["span_ids"].values())


def test_emit_set_replays_a_committed_set_and_counts_it():
    fake = FakeProvy()
    counts = E.emit_set("CAP-A", DATA, lambda f: emitter(fake), with_outcomes=True)
    assert counts == {"capture": 60}
    assert len(fake.outcomes) == 60 and len([r for r in fake.requests if r[0] == "/api/ingest/trace"]) == 360
    bad = [o for o in fake.outcomes if o["label"] == "fail"]
    assert bad and all(o["signals"]["work_held_up"] is False for o in bad)


def test_emit_set_refuses_a_file_that_is_not_the_recorded_one(tmp_path):
    import shutil
    shutil.copy(os.path.join(DATA, T.HASH_FILE), tmp_path / T.HASH_FILE)
    body = open(os.path.join(DATA, T.truth_filename("CAP-A")), "rb").read().replace(b"REQ-", b"REQX", 1)
    (tmp_path / T.truth_filename("CAP-A")).write_bytes(body)
    fake = FakeProvy()
    with pytest.raises(T.GroundTruthHashMismatch):
        E.emit_set("CAP-A", str(tmp_path), lambda f: emitter(fake))
    assert fake.requests == []


def test_dry_run_sends_nothing_without_the_emit_switch(monkeypatch):
    monkeypatch.delenv("PROVY_EMIT", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    em = E.ContextEmitter(ingest_key="k", base_url="https://dev.provy.ai")
    em.emit_plan(plans("CM-B")[0])
    assert em.sent and not em.enabled


def test_coverage_the_scorer_will_expect_is_exact_to_the_step():
    p = plans("CM-B")
    cov = C.expected_coverage(p)
    assert sum(t["decisionSteps"] for t in cov.values()) == 3 * len(p)
    # every decision step carries its own manifest except the resolver on the map route (bound to its retrieval step)
    map_sessions = sum(1 for x in p if x["route"] == "log_map")
    assert sum(t["withManifest"] for t in cov.values()) == 3 * len(p) - map_sessions
    assert cov["log"]["viaLinks"] == 0 and map_sessions > 0
    # the log map route carries no instruction, so its door has fewer instruction-bearing steps than steps
    assert cov["log"]["withInstruction"] < cov["log"]["withManifest"]


def test_the_map_route_binds_an_agent_message_agent_to_its_retrieval_step():
    """A finding about the spec, pinned: SPEC 4.4 binds a field-map line to the agent's `decision` event or, with none,
    to the agent's first event. The resolver's deciding step is an agent_message, so its manifest lands on the retrieval
    step. The planted faults avoid it (test_context_groundtruth); this test records the behaviour so a change is noticed."""
    p = _pick("CM-B", lambda p: p["route"] == "log_map")
    fake = fake_for("log_map")
    emitter(fake).emit_plan(p)
    rows = {(r["agent"], r["step_type"]): r for r in fake.spans if r["session_id"] == p["session_id"]}
    assert rows[("resolver", "tool_call")]["context"] is not None
    assert rows[("resolver", "agent_message")]["context"] is None
    assert rows[("triage", "decision")]["context"] is not None and rows[("triage", "tool_call")]["context"] is None
