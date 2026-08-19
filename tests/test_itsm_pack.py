"""The ITSM pack works real tickets in a real ServiceNow instance, so these tests
run it against a fake instance and check the two things that matter:

  1. the agent really does write triage, routing and resolution to the record, and
  2. the simulation never reports how any of it turned out.

The second is the point of the whole fleet. If the sim ever posted an ITSM
outcome, every number in the demo would be self-graded again and nothing would
look different from the outside, which is why it is asserted from several angles.
"""
import collections
import inspect
import random
import re
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

# The push script runs INSIDE ServiceNow, so it can only be read as text from here.
_PUSH = Path(__file__).resolve().parent.parent / "servicenow" / "outcome_push.js"


def push_source() -> str:
    return _PUSH.read_text()

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
        # `is None`, not a falsy check: an empty instance is a state worth testing, and
        # `incidents or _INCIDENTS` quietly handed back the default three instead.
        self.incidents = [dict(i) for i in (_INCIDENTS if incidents is None else incidents)]
        self.updates = []
        self.queries = []

    def open_demo_incidents(self, limit=25):
        self.queries.append(("open_demo_incidents", limit))
        return [dict(i) for i in self.incidents[:limit]]

    def group_sys_id(self, name):
        return f"grp-{name.lower().replace(' ', '-')}"

    def still_waiting(self, sys_id):
        """Answered from what this fake actually holds, not hardcoded True.

        The real client re-reads the record before the desk works it (provy-sim#6). A fake that always
        said yes would let the skip rot untested, which is the whole failure it exists to prevent: a
        ticket another desk resolved being dragged back to In Progress and counted as a reopen.

        Absent `state` reads as waiting, so the default fixtures keep their existing behaviour.
        """
        row = next((i for i in self.incidents if i["sys_id"] == sys_id), None)
        return bool(row) and str(row.get("state", "1")) == "1"

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
    """Triage, routing and resolution are the three writes every ticket gets. A journey may add
    more (a reassignment after a misroute, a hold while the caller is chased), so this pins the
    three that are always there and their order rather than the total."""
    pack, item, r = run_one(rates={"misroute": 0.0, "misclassify": 0.0, "shallow_diagnosis": 0.0})
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
    # NEW only. This used to be `stateIN1,2`, which also pulled tickets another desk already had in
    # hand. Harmless with one desk, and the cause of the 14 Aug collision with eleven: they worked six
    # incidents between them and left twenty-three untouched (provy-sim#6).
    assert "state=1" in captured["q"]
    assert "stateIN" not in captured["q"]


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
    """ServiceNow counts the agent's own routing as a reassignment, so the clean case is 1 and the
    threshold has to allow it. A threshold of 0 would silently fail every single ticket.

    ⛔ THIS THRESHOLD MOVED. It used to sit on a contract condition here (argus#638). The live
    contract grades `self_resolved`, which servicenow/outcome_push.js DERIVES as
    `reassignCount <= 1`, so the instance-side script is the only place the number now lives and
    the only place worth asserting it."""
    push = push_source()
    assert "self_resolved:           reassignCount <= 1" in push, (
        "outcome_push.js no longer derives self_resolved as reassignCount <= 1; a clean ticket "
        "lands on 1, so any stricter threshold fails every ticket on the live instance")
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


# What outcome_push.js derives each contract condition from. ⛔ THE RUN DOES NOT SPEAK THE
# CONTRACT'S VOCABULARY AND IS NOT SUPPOSED TO: the agent records ServiceNow's own field names, the
# instance-side push derives the contract's names from them, and a human binds the two on the
# mapping screen (the live fleet has resolution_genuine<-close_code and
# resolution_persists<-reopen_count, both confirmed). This map is that derivation, written down.
DERIVED_FROM = {
    "resolution_genuine":      ("close_code", "reopen_count"),
    "resolution_persists":     ("reopen_count",),
    "self_resolved":           ("reassignment_count",),
    "first_response_time_met": ("first_response_time_met",),
    "resolution_time_met":     ("resolution_time_met",),
}


def test_every_contract_condition_is_derivable_from_what_the_run_claims():
    """Each condition either reads a field the run records, or is derived from fields it records.

    The one exception is the honest one: procedure_grounded. ServiceNow settles no such fact, the
    push deliberately does not send it, and it grades unmeasurable on every run. That is why the
    fleet reads 5 of 6 conditions covered rather than 6."""
    pack, _, r = run_one()
    for c in pack.contract():
        if c.signal == "procedure_grounded":
            assert c.signal not in DERIVED_FROM, (
                "procedure_grounded must stay underivable; sending a value nothing observes "
                "would be inventing the outcome")
            continue
        sources = DERIVED_FROM.get(c.signal)
        assert sources, f"condition {c.id} reads {c.signal}, which nothing derives"
        for field in sources:
            assert field in r.estimated_signals, (
                f"condition {c.id} needs {field}, which the run never claims")


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
    assert len(conditions) == 6
    for c in conditions:
        assert c.signal and c.side == "outcome", (
            f"{c.id} must be settled by ServiceNow; a condition read off the agent's own trace "
            f"would mean this fleet partly marks its own homework")
        assert c.op == "eq"
        assert meets(c, good_value(c)) is True, f"{c.id} good value must pass"
        assert meets(c, bad_value(c)) is False, f"{c.id} bad value must fail"


def test_genuine_fix_close_codes_are_the_only_ones_that_count():
    """⛔ WHICH CODES COUNT AS A FIX MOVED with the contract (argus#638): resolution_genuine is
    derived by outcome_push.js, so GENUINE_FIX_CODES is the list and nothing else is."""
    for code in ["No resolution provided", "Duplicate", "Resolved by caller",
                 "User error", "Known error", "Resolved by request"]:
        assert code not in GENUINE_FIX_CODES, code


def test_legacy_close_codes_do_not_count_as_a_fix():
    """The 40 pre-seeded incidents carry legacy values. They must not grade as fixes,
    and they are excluded from the report rather than counted against it."""
    assert "Solved (Permanently)" not in GENUINE_FIX_CODES
    assert "Closed/Resolved by Caller" not in GENUINE_FIX_CODES


def test_the_instance_script_and_this_repo_agree_on_a_genuine_fix():
    """⛔ A THIRD COPY OF THE SAME LIST. outcome_push.js runs INSIDE ServiceNow, so nothing here
    imports it and a drift between the two is invisible until the demo grades wrongly."""
    block = push_source().split("var GENUINE = [", 1)[1].split("]", 1)[0]
    assert re.findall(r"'([^']+)'", block) == GENUINE_FIX_CODES, (
        "outcome_push.js and engine/servicenow.py disagree on which close codes are a genuine fix")


# ── an idle queue is a wait, not a crash ────────────────────────────────────
class EmptyThenFilling(FakeServiceNow):
    """Hands back nothing until it has been asked `after` times, the way an instance behaves when
    the next ticket has not been raised yet."""

    def __init__(self, after):
        super().__init__()
        self.after = after
        self.asked = 0

    def open_demo_incidents(self, limit=25):
        self.asked += 1
        if self.asked <= self.after:
            return []
        return super().open_demo_incidents(limit)


def test_the_agent_waits_for_a_ticket_that_has_not_arrived_yet(monkeypatch):
    """Incidents arrive during a run now, so the agent catches up with the backlog and has to wait.
    Treating that as a fatal error would kill the run at exactly the moment a real desk is idle."""
    monkeypatch.setattr("packs.itsm.pack._IDLE_POLL_S", 0)
    client = EmptyThenFilling(after=3)
    pack = ItsmPack(client=client)
    item, _ = pack.generate_work_item(random.Random(1))
    assert item["id"] == "INC0010001"
    assert client.asked == 4


def test_a_queue_that_never_fills_still_fails(monkeypatch):
    """The wait is bounded. An instance with nothing in it must fail fast and say so, rather than
    hanging a CI job until the job timeout."""
    monkeypatch.setattr("packs.itsm.pack._IDLE_POLL_S", 0)
    monkeypatch.setattr("packs.itsm.pack._IDLE_TIMEOUT_S", 0)
    with pytest.raises(RuntimeError, match="no open incidents"):
        ItsmPack(client=FakeServiceNow(incidents=[])).generate_work_item(random.Random(1))


# ── response vs resolution SLA (the split that fixed a live lie) ─────────────

def test_contract_grades_response_and_resolution_separately():
    """A ticket carries two SLA targets and they must be graded as two conditions.

    Before this, both conditions read one roll-up boolean (`made_sla`, true only when NOTHING
    breached), so a ticket answered in seconds and fixed three days late reported that it had missed
    FIRST RESPONSE. Every such ticket had been reporting the wrong failure since the script shipped.
    """
    conditions = get_pack("itsm").contract()
    signals = {c.signal for c in conditions}

    assert "first_response_time_met" in signals
    assert "resolution_time_met" in signals
    # The roll-up must not be graded by anything. It cannot distinguish which target was missed.
    assert "made_sla" not in signals


def test_resolution_time_is_what_makes_journey_delay_gradeable():
    """The journey model's delays land on the resolution clock, not the response clock.

    Without a resolution condition every queue wait and caller hold produced no contract signal at
    all: visible in ServiceNow, invisible to Provy, so attribution had nothing to attribute.
    """
    c6 = next(c for c in get_pack("itsm").contract() if c.signal == "resolution_time_met")
    assert c6.side == "outcome"       # the instance settles it; the run only claims it up front
    assert meets(c6, True) is True
    assert meets(c6, False) is False


def test_the_agent_claims_both_targets_up_front():
    """The run must claim BOTH clocks, or the response and resolution targets cannot be told apart.

    ⛔ THIS USED TO LOOP OVER `side == "both"` CONDITIONS. itsm now has none (every condition is
    settled by ServiceNow, argus#638), so that loop would have passed while checking nothing. What
    it was really about is the estimated side, which is where it now looks."""
    _, _, r = run_one()
    for signal in ("first_response_time_met", "resolution_time_met"):
        assert signal in r.estimated_signals, (
            f"the run never claims {signal}, so its target cannot be graded against the instance")


def test_resolve_carries_the_sla_the_agent_could_read(monkeypatch):
    """⛔ argus#544 — a tool output is EVIDENCE, an agent message is a CLAIM.

    Every ITSM condition was outcome-side only, and the agents' readings lived on the reviewer's
    agent_message. Provy's deterministic attribution scans TOOL OUTPUTS, so it could never see them
    and 15 of 16 misses came back "nothing in the run accounts for these".

    Making Provy read messages instead would have been the wrong fix: in a silent failure the claim
    is good while reality is bad, so it would match nothing, and where it did match it would blame an
    agent for correctly reporting bad news.

    So the resolve response carries the SLA state ServiceNow shows AT THAT MOMENT — which a resolver
    about to close a ticket can genuinely see.
    """
    src = inspect.getsource(ItsmPack._work_ticket)
    assert '"first_response_time_met": elapsed["s"] <= response_target' in src
    assert '"resolution_time_met": elapsed["s"] <= resolution_target' in src
    # It has to ride on the resolve tool call, not only the reviewer's message.
    assert 'extra_output={"close_code": d["close_code"], **sla}' in src


def test_reopen_is_never_read_back_during_the_run():
    """⛔ THE LIMIT OF THE FIX, AND IT MATTERS MORE THAN THE FIX.

    A ticket reopens AFTER the run has ended. Surfacing `reopen_count` in a tool output would be
    inventing evidence that could not have existed, which is the simulation marking its own homework
    — the exact thing this pack was built to avoid. Those misses stay honest blind spots, and Provy
    naming no cause for them is the right answer rather than a missing one.
    """
    src = inspect.getsource(ItsmPack._work_ticket)
    i = src.index('sla = {')
    block = src[i:src.index('resolve_patch,', i)]
    assert 'reopen' not in block, 'reopen must never appear in a tool output during the run'


def test_a_ticket_another_desk_already_resolved_is_skipped_not_reworked():
    """The collision that made 14 Aug 2026 unreadable (provy-sim#6).

    Eleven runs shared one queue. Each fetched a batch and worked it over the following minutes, by
    which time other runs had resolved some of those tickets. Working one anyway dragged a RESOLVED
    incident back to In Progress, and ServiceNow counts that as a reopen: INC0010192 changed state
    twelve times, INC0010196 reached reopen_count 3, where every earlier run sits at 0 or 1.

    That is not cosmetic. The ITSM contract binds `resolution_persists` to reopen_count with
    threshold [0,1], so the collision fails a promise no agent broke.
    """
    incidents = [dict(_INCIDENTS[0]), dict(_INCIDENTS[1])]
    incidents[0]["state"] = "6"  # another desk resolved it after this run queued it
    pack = ItsmPack(client=FakeServiceNow(incidents))
    item, _ = pack.generate_work_item(random.Random(1))

    assert item["id"] == incidents[1]["number"], "took the resolved ticket instead of the waiting one"


def test_a_fully_stale_batch_goes_back_for_more_rather_than_failing():
    """Every ticket in the batch was taken by someone else. That is an empty queue in substance, and
    a run with work available must not die on it."""
    incidents = [dict(_INCIDENTS[0])]
    incidents[0]["state"] = "7"
    pack = ItsmPack(client=FakeServiceNow(incidents))

    # Nothing waiting anywhere: the non-blocking path reports that plainly instead of handing back a
    # ticket nobody should touch.
    assert pack.try_generate_work_item() is None
