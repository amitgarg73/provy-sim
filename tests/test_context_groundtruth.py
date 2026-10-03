"""Ground truth for the context-manifest sets (#1505, L4): what the plan promises and what is committed.

These tests read the COMMITTED files in data/. They are the guard on the discipline: a hold-out is
generated once, its hash is recorded, and a changed generator cannot silently change it.
"""
import json
import os
import re

import pytest

import _context_path  # noqa: F401  (import shim, must come first)
from config import context_sets as CS
from engine import context as C
from engine import context_truth as T

DATA = os.path.join(_context_path.TREE, "data")
HOLD_OUTS = ["CM-B", "CM-C", "CM-D"]
ALL = ["CM-A", "CM-B", "CM-C", "CM-D", "CAP-A", "CAP-B"]

# the words the platform's domain-word guard bans (web/harness/domain-word-scan.ts DOMAIN_TERMS) plus the
# simulated request desk's own business words: none of them may appear in a string Provy sees.
BANNED = re.compile(r"ticker|bracket|pre_?market|intraday|\beod\b|\btrades?\b|trading|portfolio|broker|\bpnl\b|staleness|learner|"
                    r"refund|return(s|ed)?\b|warranty|shipping|invoice|claim(s)?\b(?!\")|loan|patient|ticket|customer|order", re.I)


def plans(name):
    return C.load_plans(os.path.join(DATA, T.truth_filename(name)))


def test_generator_is_deterministic():
    a = C.dumps_plans(C.generate_set("CM-B"))
    b = C.dumps_plans(C.generate_set("CM-B"))
    assert a == b


@pytest.mark.parametrize("name", ALL)
def test_committed_file_matches_recorded_hash(name):
    # failing first: nothing is committed until gen_context_set.py has run
    assert T.verify_ground_truth(name, DATA)


@pytest.mark.parametrize("name", ALL)
def test_committed_file_is_what_the_generator_makes(name):
    on_disk = open(os.path.join(DATA, T.truth_filename(name)), "rb").read()
    assert on_disk == C.dumps_plans(C.generate_set(name))


def test_changed_file_is_refused(tmp_path):
    src = os.path.join(DATA, T.truth_filename("CM-B"))
    (tmp_path / T.truth_filename("CM-B")).write_bytes(open(src, "rb").read() + b"\n")
    (tmp_path / T.HASH_FILE).write_text(open(os.path.join(DATA, T.HASH_FILE)).read())
    with pytest.raises(T.GroundTruthHashMismatch):
        T.verify_ground_truth("CM-B", str(tmp_path))


def test_a_recorded_set_is_not_regenerated_with_different_bytes(tmp_path):
    # the hash file says CM-B is X; a generator that now makes Y must refuse to overwrite
    (tmp_path / T.HASH_FILE).write_text(json.dumps({"CM-B": {"sha256": "0" * 64}}))
    with pytest.raises(T.GroundTruthHashMismatch):
        T.write_ground_truth("CM-B", str(tmp_path), _context_path.TREE)
    assert not (tmp_path / T.truth_filename("CM-B")).exists()


@pytest.mark.parametrize("name", HOLD_OUTS)
def test_every_hold_out_has_at_least_40_faults_and_300_clean_sessions(name):
    p = plans(name)
    faults = sum(len(x["faults"]) for x in p)
    clean = sum(1 for x in p if x["class"] == "clean")
    assert faults >= 40, faults
    assert clean >= 300, clean


def test_the_four_fault_kinds_are_in_every_hold_out_with_enough_of_each():
    for name in HOLD_OUTS:
        kinds = T.summarise(plans(name))["faults"]
        for k in ("stale", "unapproved", "empty", "instruction"):
            assert kinds.get(k, 0) >= 8, (name, k, kinds)


def test_clean_fleet_and_quiet_fleet_exist_where_the_spec_says():
    c = plans("CM-C")
    assert [x for x in c if x["fleet"] == "log" and (x["faults"])] == []
    d = plans("CM-D")
    quiet = [x for x in d if x["fleet_quiet"]]
    assert len(quiet) == CS.FLEET_SESSIONS
    empties = [dc for x in quiet for dc in x["decoys"] if dc["kind"] == "quiet_empty"]
    # about 6% of the quiet fleet's retrieval steps (two retrieving agents per session)
    share = len(empties) / (2 * len(quiet))
    assert 0.03 < share < 0.10, share
    assert not any(f for x in quiet for f in x["faults"])


@pytest.mark.parametrize("name", [n for n in ALL if n.startswith("CM")])
def test_every_fault_is_planted_where_its_route_can_carry_it(name):
    for x in plans(name):
        caps = C.ROUTE_CAPS[x["route"]]
        for f in x["faults"]:
            if f["kind"] == "instruction":
                assert caps["instruction"], (x["session_id"], x["route"])
            if f["kind"] == "stale":
                st = next(s for s in x["steps"] if s["agent"] == f["agent"] and s["step_type"] != "tool_call")
                it = next(i for i in st["manifest"]["items"] if i["id"] == f["id"] and i["source"] == f["source"])
                assert it["kind"] in caps["kinds"] and "as_of" in caps["item_fields"]
        for d in x["decoys"]:
            if d["kind"] in C.DECOY_ROUTES:
                assert x["route"] in C.DECOY_ROUTES[d["kind"]], (d, x["route"])


@pytest.mark.parametrize("name", [n for n in ALL if n.startswith("CM")])
def test_nothing_is_planted_on_the_map_route_where_the_log_door_would_bind_it_to_the_wrong_step(name):
    # a field map names no step type, so SPEC 4.4 binds its line to the agent's `decision` event, or to the agent's
    # first event when it has none. Only agents whose deciding step is a `decision` get a fault or a decoy there.
    seen = 0
    for x in plans(name):
        if x["route"] != "log_map":
            continue
        for item in x["faults"] + x["decoys"]:
            assert C.STEP_TYPE[item["agent"]] == "decision", (x["session_id"], item)
            seen += 1
    assert seen >= 0


@pytest.mark.parametrize("name", [n for n in ALL if n.startswith("CM")])
def test_faults_are_planted_after_the_warmup(name):
    for x in plans(name):
        if x["faults"]:
            assert x["session_index"] >= CS.WARMUP_SESSIONS


def test_the_decoy_kinds_all_exist_in_a_hold_out():
    seen = set()
    for name in HOLD_OUTS:
        for x in plans(name):
            for d in x["decoys"]:
                seen.add(d["kind"])
    assert {"at_limit", "stale_unused", "unapproved_unused", "case_source", "input_old", "oi_empty_unknown", "quiet_empty"} <= seen


def test_at_limit_decoy_is_exactly_at_the_limit_and_stale_fault_is_beyond_it():
    for x in plans("CM-B"):
        for d in x["decoys"]:
            if d["kind"] == "at_limit":
                st = next(s for s in x["steps"] if s["agent"] == d["agent"] and s["step_type"] != "tool_call")
                it = next(i for i in st["manifest"]["items"] if i["id"] == d["id"] and i["source"] == d["source"])
                age = C.parse_iso(st["at"]) - C.parse_iso(it["as_of"])
                assert age.total_seconds() == CS.LIMIT_DAYS * 86400
        for f in x["faults"]:
            if f["kind"] == "stale":
                assert f["age_days"] >= CS.LIMIT_DAYS + 3


def test_unapproved_sources_are_not_on_the_list_and_case_decoy_differs_only_in_case():
    def approved(src):
        return any(src == a or (a.endswith("*") and src.startswith(a[:-1])) for a in CS.APPROVED_SOURCES)
    for x in plans("CM-B"):
        for f in x["faults"]:
            if f["kind"] == "unapproved":
                assert not approved(f["source"])
        for d in x["decoys"]:
            if d["kind"] == "case_source":
                assert d["source"] != d["approved_as"] and d["source"].lower() == d["approved_as"].lower()
                assert approved(d["approved_as"])
                assert x["bar_excluded"] == "case_source_spec_conflict"


def test_every_fault_pairs_with_a_stated_settlement_and_some_settle_well():
    for name in HOLD_OUTS:
        settles = [f["settles"] for x in plans(name) for f in x["faults"] if f["kind"] != "instruction"]
        assert "bad" in settles and "good" in settles
        for x in plans(name):
            for f in x["faults"]:
                assert (x["outcome"]["label"] == "fail") == (f["settles"] == "bad")


def test_expectations_come_from_the_plan_not_from_provy():
    for x in plans("CM-B"):
        for f in x["faults"]:
            assert C.CHECK_FOR_KIND[f["kind"]] in x["expect"]["failed_checks"]
        if x["class"] == "clean":
            assert x["expect"]["failed_checks"] == [] and x["expect"]["context_cause"] is None


def test_what_provy_sees_carries_no_domain_word_and_no_prose():
    for name in ALL:
        for x in plans(name):
            seen = [x["session_type"], x["entity_id"]]
            for s in x["steps"]:
                seen += [s["agent"], s["step_type"], s.get("tool_name") or "", s["outcome"]]
                m = s.get("manifest") or {}
                for i in m.get("items", []):
                    seen += [i["source"], i["id"], i["kind"], i["version"]]
                if "instruction" in m:
                    seen.append(m["instruction"]["version"])
            for f in x["faults"] + x["decoys"]:
                seen += [str(v) for k, v in f.items() if k in ("source", "id", "agent")]
            for text in seen:
                assert not BANNED.search(text), (name, x["session_id"], text)


def test_manifest_hashes_and_shapes_follow_the_spec():
    h = re.compile(r"^sha256:[0-9a-f]{64}$")
    for x in plans("CM-A"):
        for s in x["steps"]:
            m = s.get("manifest")
            if not m:
                continue
            assert "v" not in m and "captured_by" not in m          # the server writes both
            assert len(m.get("items", [])) <= 20
            for i in m.get("items", []):
                assert h.match(i["hash"]) and i["as_of"].endswith(".000Z")
            if "instruction" in m:
                assert h.match(m["instruction"]["hash"])


def test_item_text_is_long_enough_to_hash_and_the_hash_is_the_specs():
    t = C.item_text("policy-kb", "policy-101", "v3")
    assert len(t) >= 32
    import hashlib
    assert C.hash_content(t) == "sha256:" + hashlib.sha256(t.encode()).hexdigest()
    # vector shared with the SDK's own test (SPEC 1.1): the hash of the empty string
    assert C.hash_content("") == "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_set_roles_and_sizes_are_stated_in_the_hash_record():
    rec = json.load(open(os.path.join(DATA, T.HASH_FILE)))
    for name in ALL:
        assert rec[name]["sha256"] and rec[name]["sessions"] > 0
    assert rec["CM-A"]["hold_out"] is False
    assert all(rec[n]["hold_out"] for n in HOLD_OUTS)


def test_capture_fleets_share_work_and_differ_only_in_manifests():
    a, b = plans("CAP-A"), plans("CAP-B")
    assert len(a) == len(b) == CS.CAPTURE_SESSIONS
    for x, y in zip(a, b):
        assert (x["session_id"].replace("cap-a", "cap-b"), x["entity_id"], x["occurred_at"], x["outcome"]["label"]) == \
               (y["session_id"], y["entity_id"], y["occurred_at"], y["outcome"]["label"])
        assert all(s.get("manifest") is None for s in y["steps"])
        assert y["faults"] == [] and y["route"] == "none"
    kinds = T.summarise(a)["faults"]
    assert all(kinds.get(k, 0) >= 1 for k in ("stale", "unapproved", "empty", "instruction"))
    assert all(x["route"] == "rest" for x in a)
    # a miss with no context finding must exist in the capture fleet, for the unchanged "no recorded step" line
    assert any(x["outcome"]["label"] == "fail" and x["outcome"]["cause"] == "background" for x in a)
    assert any(x["outcome"]["cause"] == "context" for x in a)
    assert all(len(x["span_ids"]) == 6 for x in a + b)
