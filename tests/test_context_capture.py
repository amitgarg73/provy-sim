"""The two capture fleets for the UX lane (#1505, L4, SPEC 12.6)."""
import json
import os

import pytest

import _context_path  # noqa: F401
from config import context_sets as CS
from engine import context as C
from engine import context_emit as E
from engine import context_truth as T

DATA = os.path.join(_context_path.TREE, "data")


def plans(name):
    return C.load_plans(os.path.join(DATA, T.truth_filename(name)))


def seed_rows():
    return [json.loads(line) for line in open(os.path.join(DATA, "capture_seed_rows_CAP-A.jsonl")) if line.strip()]


def test_the_fleet_names_say_simulated():
    for spec in CS.CAPTURE.values():
        assert "(simulated)" in spec["label"]
    for p in plans("CAP-A") + plans("CAP-B"):
        assert "(simulated)" in p["fleet_label"]


def test_capture_a_has_every_fault_kind_clean_runs_and_a_shape_that_gives_the_card_something_to_say():
    a = plans("CAP-A")
    kinds = T.summarise(a)["faults"]
    assert kinds == {"stale": 6, "unapproved": 4, "empty": 3, "instruction": 1}
    shapes = [p.get("shape") for p in a]
    assert shapes.count("silent") == 9 and shapes.count("undated") == 5
    cov = C.expected_coverage(a)["rest"]
    assert cov["decisionSteps"] == 180
    assert 0.70 < cov["withManifest"] / 180 < 0.95 and cov["withAges"] < cov["withManifest"]
    # some settled badly with a context cause, and some missed for no recorded reason (the unchanged honest line)
    assert any(p["outcome"]["cause"] == "context" for p in a) and any(p["outcome"]["cause"] == "background" for p in a)
    assert any(f["settles"] == "good" for p in a for f in p["faults"] if f["kind"] != "instruction")


def test_silent_and_undated_sessions_are_clean_and_say_nothing_they_should_not():
    for p in plans("CAP-A"):
        if p.get("shape") == "silent":
            assert p["class"] == "clean" and all(s.get("manifest") is None for s in p["steps"])
        if p.get("shape") == "undated":
            assert p["class"] == "clean"
            assert all("as_of" not in i for s in p["steps"] for i in (s.get("manifest") or {}).get("items", []))


def test_capture_b_is_the_same_work_with_no_manifest_at_all():
    a, b = plans("CAP-A"), plans("CAP-B")
    assert [(p["entity_id"], p["occurred_at"], p["outcome"]["label"]) for p in a] == [(p["entity_id"], p["occurred_at"], p["outcome"]["label"]) for p in b]
    assert all(s.get("manifest") is None for p in b for s in p["steps"])
    assert C.expected_coverage(b)["none"]["withManifest"] == 0


def test_the_seed_rows_are_exactly_the_decision_steps_that_carry_a_manifest():
    a = plans("CAP-A")
    rows = seed_rows()
    want = sum(1 for p in a for s in p["steps"] if s["step_type"] != "tool_call" and s.get("manifest"))
    assert len(rows) == want == C.expected_coverage(a)["rest"]["withManifest"]
    by_span = {(p["session_id"], p["span_ids"][s["span"]]): (p, s) for p in a for s in p["steps"]}
    for r in rows:
        p, s = by_span[(r["session_id"], r["span_id"])]
        assert r["context"] == E.wire_manifest(s, "rest") and r["ingest_door"] == "rest"
        assert "captured_by" not in r["context"] and "v" not in r["context"]
    assert len({(r["session_id"], r["span_id"]) for r in rows}) == len(rows)


def test_no_row_is_written_for_the_control_fleet_and_no_text_is_in_a_row():
    text = open(os.path.join(DATA, "capture_seed_rows_CAP-A.jsonl")).read()
    assert "CAP-B" not in text and "cap-b-" not in text and "[simulated" not in text


def test_the_seed_rows_file_is_what_the_script_makes():
    import importlib.util
    spec = importlib.util.spec_from_file_location("capture_seed_rows", os.path.join(_context_path.TREE, "scripts", "capture_seed_rows.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    made = [json.dumps(r, sort_keys=True, separators=(",", ":")) for r in mod.rows("CAP-A", DATA)]
    assert made == [line.rstrip("\n") for line in open(os.path.join(DATA, "capture_seed_rows_CAP-A.jsonl")) if line.strip()]


def test_declared_rows_for_the_capture_fleets_follow_the_spec():
    a, b = CS.CAPTURE_DECLARATIONS["CAP-A"], CS.CAPTURE_DECLARATIONS["CAP-B"]
    # One freshness row and one approved-list row per agent (L10); fleet B only the freshness rows.
    assert [r["check"]["kind"] for r in a] == ["context_fresh", "context_sources_in"] * 3
    assert [r["check"]["kind"] for r in b] == ["context_fresh"] * 3
    assert a[0]["check"]["max_age_hours"] == 720


def test_capture_emission_carries_the_planned_span_ids_and_no_context():
    from context_fake_server import FakeProvy
    fake = FakeProvy()
    em = E.ContextEmitter(ingest_key="k", base_url="https://dev.provy.ai", transport=fake)
    for p in plans("CAP-A"):
        em.emit_capture(p)
    ids = {(r["session_id"], r["span_id"]) for r in fake.spans}
    assert ids == {(p["session_id"], i) for p in plans("CAP-A") for i in p["span_ids"].values()}
    assert all(r["context"] is None for r in fake.spans)
