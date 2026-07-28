"""The ITSM pack works real tickets in a real ServiceNow instance, so these tests
run it against a fake instance and check the two things that matter:

  1. the agent really does write triage, routing and resolution to the record, and
  2. the simulation never reports how any of it turned out.

The second is the point of the whole fleet. If the sim ever posted an ITSM
outcome, every number in the demo would be self-graded again and nothing would
look different from the outside, which is why it is asserted from several angles.
"""
import collections
import random
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import EXTERNAL_PACKS, make_ctx
from engine.contract import bad_value, good_value, meets
from engine.levers import LeverConfig
from engine.servicenow import (GENUINE_FIX_CODES, MARKER, ServiceNowClient,
                               ServiceNowError)
from packs import PACKS, get_pack
from packs.itsm.pack import ItsmPack

REPO = Path(__file__).resolve().parents[1]

_INCIDENTS = [
    {"sys_id": "s1", "number": "INC0010001", "state": "1",
     "short_description": "VPN drops every ten minutes when working from home",
     "description": "The VPN connection drops roughly every ten minutes.",
     "category": "inquiry", "priority": "5", "impact": "3", "urgency": "3",
     "contact_type": "phone", "opened_at": "2026-07-27 10:00:00"},
    {"sys_id": "s2", "number": "INC0010002", "state": "1",
     "short_description": "Locked out of my account after too many attempts",
     "description": "I mistyped my password a few times and now the account is locked.",
     "category": "inquiry", "priority": "5", "impact": "3", "urgency": "3",
     "contact_type": "self-service", "opened_at": "2026-07-27 10:05:00"},
    {"sys_id": "s3", "number": "INC0010003", "state": "1",
     "short_description": "Deadlocks on the SQL Server order tables",
     "description": "Repeated deadlock errors during the overnight batch.",
     "category": "inquiry", "priority": "5", "impact": "3", "urgency": "3",
     "contact_type": "email", "opened_at": "2026-07-27 10:09:00"},
]


class FakeServiceNow:
    """Records every write instead of making it. Nothing is invented: it hands back
    incidents that were put into it, the way the real instance hands back records
    that were created in it."""

    def __init__(self, incidents=None):
        self.incidents = [dict(i) for i in (incidents or _INCIDENTS)]
        self.updates = []
        self.queries = []

    def open_demo_incidents(self, limit=25):
        self.queries.append(("open_demo_incidents", limit))
        return [dict(i) for i in self.incidents[:limit]]

    def group_sys_id(self, name):
        return f"grp-{name.lower().replace(' ', '-')}"

    def update(self, table, sys_id, payload):
        self.updates.append({"table": table, "sys_id": sys_id, "payload": dict(payload)})
        row = next((i for i in self.incidents if i["sys_id"] == sys_id), {})
        row.update(payload)
        return {"number": row.get("number", ""), "state": payload.get("state", row.get("state")),
                "sys_updated_on": "2026-07-27 10:30:00"}


def run_one(seed=1, incidents=None, rates=None):
    pack = ItsmPack(client=FakeServiceNow(incidents))
    ctx = make_ctx(levers=LeverConfig(rates or {}), seed=seed, workflow="itsm")
    item, gt = pack.generate_work_item(random.Random(seed))
    return pack, item, pack.run_pipeline(item, gt, ctx)


# ── the anti-circularity guarantees ─────────────────────────────────────────

def test_pack_does_not_own_its_outcome():
    assert get_pack("itsm").owns_outcome is False


def test_every_excluded_pack_declares_it_does_not_own_its_outcome():
    """The shared pack fixture skips these. That is only legitimate for a fleet whose
    outcome comes from elsewhere, never a way to quiet a failing test."""
    for name in EXTERNAL_PACKS:
        assert name in PACKS, f"{name} is excluded from the fixture but is not a pack"
        assert get_pack(name).owns_outcome is False, (
            f"{name} is excluded from the shared pack tests but claims to own its outcome")


def test_run_reports_no_outcome_of_its_own():
    _, _, r = run_one()
    assert r.real_signals == {}, "the simulation must not claim to know what happened"
    assert r.outcome_label == "skipped"
    assert r.outcome_value is None
    assert r.metadata["outcome_source"] == "servicenow_push"


def test_ledger_never_offers_an_itsm_outcome_to_post(tmp_path):
    from engine.groundtruth import GroundTruthLedger, build_record
    _, _, r = run_one()
    ledger = GroundTruthLedger(str(tmp_path / "gt.jsonl"))
    ledger.append(build_record("itsm", r, 0))
    assert ledger.pending_outcomes("itsm") == [], (
        "an ITSM run must never appear as an outcome the sim can post")


def test_run_batch_refuses_to_reconcile_itsm():
    out = subprocess.run(
        [sys.executable, "scripts/run_batch.py", "--pack", "itsm", "--count", "1", "--reconcile"],
        cwd=REPO, capture_output=True, text=True)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "not valid" in out.stderr


def test_the_answer_key_is_not_in_what_the_agent_reads():
    """The instance holds what each incident really is in correlation_display. The
    agent classifies from the text, so that field must not be in its field list."""
    assert "correlation_display" not in ServiceNowClient.INCIDENT_FIELDS
    source = (REPO / "packs" / "itsm" / "pack.py").read_text()
    assert "correlation_display" not in source


def test_no_offline_fallback():
    """Unconfigured must fail loudly. A pack that invented its own tickets when the
    instance was unreachable would look like it worked and would prove nothing."""
    sn = ServiceNowClient(instance="", user="", password="")
    assert sn.configured is False
    with pytest.raises(ServiceNowError) as e:
        sn.require()
    assert "SERVICENOW_INSTANCE" in str(e.value)


# ── the agent really works the ticket ───────────────────────────────────────

def test_agent_writes_triage_routing_and_resolution_to_the_record():
    pack, item, r = run_one()
    sn = pack.client
    assert len(sn.updates) == 3, [u["payload"] for u in sn.updates]
    triage, route, resolve = (u["payload"] for u in sn.updates)

    assert triage["state"] == "2"
    assert triage["category"] in ("inquiry", "software", "hardware", "network",
                                  "database", "password_reset")
    assert triage["urgency"] in ("1", "2", "3") and triage["impact"] in ("1", "2", "3")
    assert route["assignment_group"].startswith("grp-")
    assert resolve["state"] == "6"
    assert resolve["close_code"]
    assert resolve["close_notes"]
    assert all(u["sys_id"] == item["sys_id"] for u in sn.updates)


def test_routing_clears_the_individual_assignee():
    """When triage raises the priority, the instance's assignment rules put a named
    person on the incident, and its stock 'Abort changes on group' rule then rejects
    any group change that leaves them assigned to a group they are not in. Observed
    live as an HTTP 403. Moving a ticket between teams has to clear the assignee."""
    pack, _, _ = run_one()
    route = next(u["payload"] for u in pack.client.updates if "assignment_group" in u["payload"])
    assert route.get("assigned_to") == "", "routing must clear assigned_to or the instance refuses it"


def test_a_refused_write_fails_one_ticket_not_the_batch():
    class Refusing(FakeServiceNow):
        def update(self, table, sys_id, payload):
            if "assignment_group" in payload:
                raise ServiceNowError("PATCH -> HTTP 403: aborted by Business Rule")
            return super().update(table, sys_id, payload)

    pack = ItsmPack(client=Refusing())
    ctx = make_ctx(levers=LeverConfig({}), seed=1, workflow="itsm")
    item, gt = pack.generate_work_item(random.Random(1))
    r = pack.run_pipeline(item, gt, ctx)   # must not raise

    assert r.terminal_reason == "tool_error"
    assert r.estimated_signals == {}, "a run that never finished must claim nothing"
    assert r.metadata["estimated_success"] is False
    assert "403" in r.metadata["rejected_by_system_of_record"]
    error_step = next(t for t in r.traces if t.outcome == "error")
    assert error_step.error and error_step.tool_output["rejected"] is True
    # And it still reports no outcome of its own.
    assert r.real_signals == {} and r.outcome_label == "skipped"


def test_work_notes_are_written_at_every_step():
    pack, _, _ = run_one()
    assert all("work_notes" in u["payload"] for u in pack.client.updates)


def test_entity_id_is_the_incident_number():
    _, item, r = run_one()
    assert r.entity_id == item["id"] == "INC0010001"
    assert r.session_id.startswith("sim-itsm-INC0010001")


def test_the_resolving_tool_output_carries_the_contract_signal_name():
    """Attribution can only find the source of a signal if the tool that produced it
    carries the contract's own name for it."""
    _, _, r = run_one()
    resolve = next(t for t in r.traces if t.tool_name == "servicenow.resolve_incident")
    assert "close_code" in resolve.tool_output


def test_incidents_are_scoped_to_this_demo():
    sn = ServiceNowClient(instance="https://x", user="u", password="p")
    captured = {}
    sn.query = lambda table, q="", fields=None, limit=100, offset=0: captured.setdefault(
        "q", q) and [] or []
    sn.open_demo_incidents(limit=5)
    assert f"correlation_id={MARKER}" in captured["q"]
    assert "stateIN1,2" in captured["q"]


def test_the_agent_never_gets_a_second_go_at_the_same_incident():
    """A reopened ticket returns to In Progress. Without excluding it, the agent would
    work it again and emit a second session for the same entity on the same
    business_date, which is the ledger's unique key: the two runs would fight over one
    row. A ticket that came back belongs to second line."""
    sn = ServiceNowClient(instance="https://x", user="u", password="p")
    captured = {}

    def fake_query(table, q="", fields=None, limit=100, offset=0):
        captured["q"] = q
        return []

    sn.query = fake_query
    sn.open_demo_incidents(limit=5)
    assert "reopen_count=0" in captured["q"]


# ── the forecasts ───────────────────────────────────────────────────────────

FORECASTS = ["predicted_category", "predicted_priority", "recommended_group",
             "predicted_close_code", "predicted_sla_result", "predicted_reopen_risk",
             "agent_confidence"]


def test_seven_forecasts_ride_on_the_reviewer_trace_and_the_close():
    _, _, r = run_one()
    reviewer = next(t for t in r.traces if t.agent == "reviewer" and t.step_type == "agent_message")
    for f in FORECASTS:
        assert f in reviewer.payload_extra, f"{f} missing from the reviewer trace"
        assert f in r.metadata["forecasts"], f"{f} missing from close metadata"
    assert len(r.metadata["forecasts"]) == 7


def test_a_cleanly_handled_ticket_still_counts_as_one_assignment():
    """ServiceNow counts the agent's own routing as a reassignment, so the clean case
    is 1 and the condition has to allow it. Asserted here because a threshold of 0
    would silently fail every single ticket."""
    c4 = next(c for c in get_pack("itsm").contract() if c.signal == "reassignment_count")
    assert meets(c4, 1) is True, "the agent routing the ticket itself must not fail the condition"
    assert meets(c4, 2) is False, "being passed to a second team must fail it"
    _, _, r = run_one()
    assert r.estimated_signals["reassignment_count"] == 1


def test_priority_mix_matches_the_real_servicenow_benchmark():
    """Measured over 24,918 incidents of a real instance: P3 94.2%, P4 3.1%, P2 1.6%,
    P1 1.1%, no P5 (servicenow/BENCHMARK.md). A desk being almost entirely P3 is what
    ITIL priority assignment produces, not a quirk of that company. Asserted on the
    distribution rather than on a handful of draws, which would be flaky at these
    proportions."""
    from packs.itsm.pack import _PRIORITY_MATRIX
    rng = random.Random(1)
    counts = collections.Counter()
    for _ in range(20000):
        u, i = ItsmPack._urgency_impact("Excel will not open files from the shared drive", rng)
        counts[_PRIORITY_MATRIX[(i, u)]] += 1
    pct = {k: 100 * v / 20000 for k, v in counts.items()}

    assert pct.get("5", 0) == 0, "the real instance issues no P5 at all"
    assert 92 <= pct["3"] <= 96, f"P3 should dominate at ~94%, got {pct['3']:.1f}%"
    assert 1.5 <= pct["4"] <= 5, f"P4 ~3%, got {pct['4']:.1f}%"
    assert 0.5 <= pct["2"] <= 3.5, f"P2 ~1.6%, got {pct['2']:.1f}%"
    assert 0.3 <= pct["1"] <= 2.5, f"P1 ~1.1%, got {pct['1']:.1f}%"


def test_forecasts_are_falsifiable_values_not_prose():
    _, _, r = run_one()
    f = r.metadata["forecasts"]
    assert f["predicted_category"] in ("inquiry", "software", "hardware", "network",
                                       "database", "password_reset")
    assert f["predicted_priority"] in ("1", "2", "3", "4", "5")
    assert f["predicted_sla_result"] in ("met", "missed")
    assert f["predicted_reopen_risk"] in ("low", "medium", "high")
    assert 0.0 < f["agent_confidence"] <= 1.0


def test_estimated_signals_use_the_contract_names():
    pack, _, r = run_one()
    for c in pack.contract():
        assert c.signal in r.estimated_signals, (
            f"condition {c.id} reads {c.signal}, which the run never claims")


def test_the_close_code_the_agent_wrote_is_the_one_it_forecast():
    pack, _, r = run_one()
    written = next(u["payload"]["close_code"] for u in pack.client.updates
                   if "close_code" in u["payload"])
    assert written == r.metadata["forecasts"]["predicted_close_code"]
    assert written == r.estimated_signals["close_code"]


# ── the agent's judgement ───────────────────────────────────────────────────

def test_classifier_reads_the_incident_text():
    pack = ItsmPack(client=FakeServiceNow())
    assert pack.classify("VPN drops every ten minutes") == ("network", True)
    assert pack.classify("Deadlocks on the SQL Server order tables") == ("database", True)
    assert pack.classify("Locked out of my account") == ("password_reset", True)
    # Nothing matched: the agent falls back and knows it is unsure.
    assert pack.classify("Something is wrong") == ("inquiry", False)


def test_error_rates_come_off_the_lever_config():
    """Turning every error rate to zero must produce a correctly routed, genuinely
    fixed ticket, so the rates are really what drives the failures."""
    clean = {"misclassify": 0.0, "misroute": 0.0, "weak_fix": 0.0, "overconfidence": 0.0}
    for seed in range(6):
        pack, _, r = run_one(seed=seed, rates=clean)
        f = r.metadata["forecasts"]
        from engine.servicenow import CORRECT_GROUP
        assert f["recommended_group"] == CORRECT_GROUP[f["predicted_category"]]
        assert f["predicted_close_code"] in GENUINE_FIX_CODES


def test_errors_actually_occur_at_the_configured_rates():
    """And with the rates dialled up, the ticket really is misrouted in the instance,
    not just flagged as such in a metadata field."""
    hot = {"misclassify": 0.0, "misroute": 1.0, "weak_fix": 0.0, "overconfidence": 0.0}
    from engine.servicenow import CORRECT_GROUP
    pack, _, r = run_one(seed=4, rates=hot)
    f = r.metadata["forecasts"]
    assert f["recommended_group"] != CORRECT_GROUP[f["predicted_category"]]
    written_group = next(u["payload"]["assignment_group"] for u in pack.client.updates
                         if "assignment_group" in u["payload"])
    assert written_group == f"grp-{f['recommended_group'].lower().replace(' ', '-')}"


def test_confidence_is_lower_when_the_text_was_ambiguous():
    ambiguous = [dict(_INCIDENTS[0], short_description="Something is wrong", description="Please help")]
    clean = {"misclassify": 0.0, "misroute": 0.0, "weak_fix": 0.0, "overconfidence": 0.0}
    _, _, sure = run_one(seed=2, rates=clean)
    _, _, unsure = run_one(seed=2, incidents=ambiguous, rates=clean)
    assert unsure.confidence < sure.confidence


# ── contract ────────────────────────────────────────────────────────────────

def test_contract_conditions_are_signal_mapped_and_gradeable():
    pack = get_pack("itsm")
    conditions = pack.contract()
    assert len(conditions) == 4
    for c in conditions:
        assert c.signal and c.side in ("outcome", "trace", "both")
        assert c.op in ("eq", "in", "lte")
        assert meets(c, good_value(c)) is True, f"{c.id} good value must pass"
        assert meets(c, bad_value(c)) is False, f"{c.id} bad value must fail"


def test_genuine_fix_close_codes_grade_and_the_rest_do_not():
    c3 = next(c for c in get_pack("itsm").contract() if c.signal == "close_code")
    for code in GENUINE_FIX_CODES:
        assert meets(c3, code) is True, code
    for code in ["No resolution provided", "Duplicate", "Resolved by caller",
                 "User error", "Known error", "Resolved by request"]:
        assert meets(c3, code) is False, code


def test_legacy_close_codes_do_not_count_as_a_fix():
    """The 40 pre-seeded incidents carry legacy values. They must not grade as fixes,
    and they are excluded from the report rather than counted against it."""
    c3 = next(c for c in get_pack("itsm").contract() if c.signal == "close_code")
    assert meets(c3, "Solved (Permanently)") is False
    assert meets(c3, "Closed/Resolved by Caller") is False
