"""servicenow/outcome_push.js: a push that does not land is retried, kept and driven again (argus#1649).

The rule runs inside ServiceNow and nothing here can import it, so it is run in node against a fake instance
(tests/js/outcome_push_harness.js). On 29 Sep 2026 five pushes came back 'HTTP 0' in one sweep pass and were lost for good,
because the rule fires once and kept nothing. These tests hold the forward fix: nothing is written to an incident, a payload
refusal is not kept (it would loop), and a ticket is not lost without a PENDING line.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent / "js" / "outcome_push_harness.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

OK = {"status": 200, "body": '{"reconciled":1,"reconciliation":"matched"}'}
NO_ANSWER = {"status": 0, "body": "", "error": "Connection reset"}


def run(**scenario):
    out = subprocess.run(["node", str(HARNESS), json.dumps(scenario)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


CUR = {"number": "INC0010186"}


def lines(r, level):
    return [m for lv, m in r["log"] if lv == level]


def test_a_push_that_lands_is_one_request_and_keeps_nothing():
    r = run(current=CUR, answers=[OK])
    assert r["sent"] == ["INC0010186"]
    assert r["props"].get("provy.push.pending", "") == ""
    assert any("pushed INC0010186" in m for m in lines(r, "info"))


def test_the_payload_is_what_it_was_before_the_fix():
    r = run(current=dict(CUR, reopen=0, reassign=1), answers=[OK])
    p = r["bodies"][0]
    assert p["entity_id"] == "INC0010186" and p["label"] == "success" and p["source"] == "confirmed"
    assert p["business_date"] == "2026-09-29"
    s = p["signals"]
    assert s["resolution_genuine"] is True and s["resolution_persists"] is True and s["self_resolved"] is True
    assert s["first_response_time_met"] is True and s["resolution_time_met"] is True
    assert s["routing_correct"] is True and s["category_correct"] is True


def test_the_settlement_time_is_when_the_instance_closed_the_ticket():
    r = run(current=dict(CUR, closed_at="2026-09-29 13:41:24"), answers=[OK])
    assert r["bodies"][0]["occurred_at"] == "2026-09-29T13:41:24Z"


def test_a_record_without_a_close_stamp_falls_back_to_the_clock():
    r = run(current=dict(CUR, closed_at=""), answers=[OK])
    assert r["bodies"][0]["occurred_at"] == "2026-10-07T12:00:00Z"


def test_no_answer_twice_then_an_answer_lands_on_the_third_attempt():
    r = run(current=CUR, answers=[NO_ANSWER, NO_ANSWER, OK])
    assert r["sent"] == ["INC0010186"] * 3
    assert r["sleeps"] == [2000, 6000]
    assert r["props"].get("provy.push.pending", "") == ""
    assert any("Connection reset" in m for m in lines(r, "warn")), "the error message ServiceNow holds is logged"


def test_the_29_september_case_three_silences_keep_the_ticket_for_later():
    r = run(current=CUR, answers=[NO_ANSWER] * 3)
    assert r["sent"] == ["INC0010186"] * 3
    assert r["props"]["provy.push.pending"] == "INC0010186"
    assert any("PUSH PENDING INC0010186" in m for m in lines(r, "error"))


def test_a_later_closure_pushes_the_pending_ticket_again_and_clears_it():
    r = run(current={"number": "INC0010200"}, props={"provy.push.pending": "INC0010186"},
            incidents={"INC0010186": {"number": "INC0010186", "reopen": 0}}, answers=[OK, OK])
    assert r["sent"] == ["INC0010200", "INC0010186"]
    assert r["props"]["provy.push.pending"] == ""
    again = r["bodies"][1]
    assert again["entity_id"] == "INC0010186" and again["occurred_at"] == "2026-09-29T13:41:24Z"


def test_a_pending_ticket_that_still_does_not_land_stays_pending_and_is_tried_once():
    r = run(current={"number": "INC0010200"}, props={"provy.push.pending": "INC0010186"},
            incidents={"INC0010186": {"number": "INC0010186"}}, answers=[OK, NO_ANSWER])
    assert r["sent"] == ["INC0010200", "INC0010186"]
    assert r["props"]["provy.push.pending"] == "INC0010186"


def test_a_refused_key_is_kept_so_it_can_be_driven_after_the_key_is_fixed():
    r = run(current=CUR, answers=[{"status": 401, "body": "no"}])
    assert r["sent"] == ["INC0010186"], "401 is not retried: the same key fails the same way"
    assert r["props"]["provy.push.pending"] == "INC0010186"
    assert any("did not accept the ingest key" in m for m in lines(r, "error"))


def test_a_vercel_block_is_named_and_kept():
    r = run(current=CUR, answers=[{"status": 401, "body": "Authentication Required: Protected deployment"}])
    assert any("BLOCKED BY VERCEL" in m for m in lines(r, "error"))
    assert r["props"]["provy.push.pending"] == "INC0010186"


def test_a_refused_payload_is_not_kept_because_it_would_fail_the_same_way_for_ever():
    r = run(current=CUR, answers=[{"status": 422, "body": "bad signals"}])
    assert r["sent"] == ["INC0010186"]
    assert r["props"].get("provy.push.pending", "") == ""
    assert any("push failed for INC0010186: HTTP 422" in m for m in lines(r, "error"))


def test_a_pending_ticket_refused_for_good_is_dropped():
    r = run(current={"number": "INC0010200"}, props={"provy.push.pending": "INC0010186"},
            incidents={"INC0010186": {"number": "INC0010186"}}, answers=[OK, {"status": 422, "body": "bad"}])
    assert r["props"]["provy.push.pending"] == ""
    assert any("refused for good" in m for m in lines(r, "error"))


def test_a_pending_ticket_that_is_not_a_closed_demo_incident_is_dropped():
    r = run(current={"number": "INC0010200"}, props={"provy.push.pending": "INC0000001"}, incidents={}, answers=[OK])
    assert r["sent"] == ["INC0010200"]
    assert r["props"]["provy.push.pending"] == ""


def test_at_most_five_pending_tickets_are_driven_per_run():
    nums = [f"INC00200{i:02d}" for i in range(8)]
    r = run(current={"number": "INC0010200"}, props={"provy.push.pending": ",".join(nums)},
            incidents={n: {"number": n} for n in nums}, answers=[OK] * 10)
    assert len(r["sent"]) == 1 + 5
    assert r["props"]["provy.push.pending"] == ",".join(nums[5:])


def test_the_pending_list_never_grows_past_its_cap():
    many = ",".join(f"INC1{i:05d}" for i in range(100))
    r = run(current=CUR, props={"provy.push.pending": many}, incidents={}, answers=[NO_ANSWER] * 3)
    left = r["props"]["provy.push.pending"].split(",") if r["props"]["provy.push.pending"] else []
    assert len(left) <= 100


def test_a_thrown_error_is_treated_as_no_answer():
    r = run(current=CUR, answers=[{"throws": "boom"}, OK])
    assert r["sent"] == ["INC0010186"] * 2


def test_a_disabled_rule_does_nothing_and_says_so():
    r = run(current=CUR, props={"provy.ingest.key": ""})
    assert r["sent"] == [] and any("outcome push disabled" in m for m in lines(r, "info"))


def test_the_rule_still_writes_nothing_to_an_incident():
    src = (Path(__file__).resolve().parent.parent / "servicenow" / "outcome_push.js").read_text()
    for forbidden in (".update(", ".insert(", ".deleteRecord(", "work_notes", "setValue("):
        assert forbidden not in src, forbidden
