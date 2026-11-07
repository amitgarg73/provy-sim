"""The Multi Vendor Scenario: grading, the runner's lifecycle and cleanup, and the estate client, against a fake estate that behaves like the real one."""
import dataclasses

import pytest

from multi_vendor.agent_request import make_open_request
from multi_vendor.estate import Estate, EstateError
from multi_vendor.grade import grade
from multi_vendor.runner import SYNC_JOB, WORKER_JOB, Timeout, run
from multi_vendor.scenarios import BY_KEY, SCENARIOS


from tests.fake_estate import FakeEstate  # noqa: E402


def _clock():
    t = [0.0]
    return (lambda s: t.__setitem__(0, t[0] + s)), (lambda: t[0])


def test_grade_says_what_is_left():
    ok = grade({"account_active": False, "groups_held": 0.0, "access_remaining": 0.0, "systems_checked": 4.0})
    assert ok.held and ok.failures == []
    bad = grade({"account_active": True, "groups_held": 1.0, "access_remaining": 5.0, "systems_checked": 4.0})
    assert not bad.held and len(bad.failures) == 3 and "4 systems" in bad.failures[2]


def test_grade_unknown_account_state_is_not_held():
    assert not grade({"groups_held": 0, "access_remaining": 0}).held       # a missing field is never read as "ended"


@pytest.mark.parametrize("scn", SCENARIOS, ids=lambda s: s.key)
def test_every_scenario_outcome_matches_what_it_claims(scn):
    r = run(scn, FakeEstate(), "direct", sleep=lambda s: None)
    assert r.mode == "estate-only"
    assert r.verdict.held == scn.should_hold and r.matches_expectation


def test_there_is_a_control_and_there_are_failures():
    assert any(s.should_hold for s in SCENARIOS) and sum(not s.should_hold for s in SCENARIOS) >= 3
    assert len({s.key for s in SCENARIOS}) == len(SCENARIOS)


@pytest.mark.parametrize("scn", SCENARIOS, ids=lambda s: s.key)
def test_cleanup_leaves_a_clean_estate_even_after_a_run(scn):
    e = FakeEstate(); run(scn, e, "direct", sleep=lambda s: None)
    assert e.fault == {} and e.jobs[SYNC_JOB] is False and (e.active, e.groups, e.left) == (True, 1, 5)


def test_cleanup_runs_after_a_timeout():
    e = FakeEstate(); sleep, now = _clock()
    with pytest.raises(Timeout):
        run(BY_KEY["clean_leaver"], e, "servicely", open_request=lambda: "REQ1", wait=30, poll=10, sleep=sleep, now=now)
    assert e.fault == {} and e.jobs[SYNC_JOB] is False and e.active is True


def test_servicely_trigger_opens_a_request_waits_for_the_estate_and_turns_the_worker_on():
    e = FakeEstate(apply_changes_on_wait=True); sleep, now = _clock(); opened = []
    r = run(BY_KEY["vpn_missed"], e, "servicely", open_request=lambda: opened.append(1) or "REQ9", wait=120, poll=10, sleep=sleep, now=now)
    assert opened == [1] and r.mode == "end-to-end" and "request REQ9" in r.notes[0]
    assert ("job", WORKER_JOB, True) in e.log and r.matches_expectation and not r.verdict.held


def test_servicely_trigger_without_a_request_opener_is_refused():
    with pytest.raises(ValueError):
        run(BY_KEY["clean_leaver"], FakeEstate(), "servicely")
    with pytest.raises(ValueError):
        run(BY_KEY["clean_leaver"], FakeEstate(), "carrier-pigeon")


def test_resync_that_never_happens_times_out_rather_than_passing():
    class NoSync(FakeEstate):
        def set_job_active(self, name, on): self.jobs[name] = on
    sleep, now = _clock()
    with pytest.raises(Timeout):
        run(BY_KEY["recreated_by_sync"], NoSync(), "direct", wait=30, poll=10, sleep=sleep, now=now)


def test_a_wrong_expectation_is_reported_not_swallowed():
    wrong = dataclasses.replace(BY_KEY["vpn_missed"], should_hold=True)
    assert not run(wrong, FakeEstate(), "direct", sleep=lambda s: None).matches_expectation


# --- the estate client, against a scripted transport -------------------------------------------
def _transport(routes, seen):
    def http(method, url, headers, body):
        seen.append((method, url, dict(headers), body))
        for key, resp in routes:
            if key in url:
                return resp
        return 404, "no route"
    return http


def test_estate_discovers_door_and_token_and_never_echoes_the_secret():
    seen = []
    http = _transport([
        ("sys_ws_definition", (200, {"result": [{"base_uri": "/api/777/provy_iam"}]})),
        ("name%3Dprovy.iam.read_token", (200, {"result": [{"sys_id": "p1", "value": "SECRET-READ-TOKEN"}]})),
        ("/readings", (200, {"result": {"account_active": False}})),
    ], seen)
    e = Estate("x.service-now.com", "u", "pw", http)
    assert e.readings("provy.iam.spike01") == {"account_active": False}
    door = [s for s in seen if "/readings" in s[1]][0]
    assert door[1].startswith("https://x.service-now.com/api/777/provy_iam/readings") and door[2]["Authorization"] == "Bearer SECRET-READ-TOKEN"
    assert "SECRET-READ-TOKEN" not in repr(e) and "pw" not in repr(e)


def test_estate_errors_name_the_call_and_never_the_body():
    e = Estate("x", "u", "pw", _transport([("sys_ws_definition", (200, {"result": []})), ("sys_properties", (200, {"result": [{"sys_id": "p", "value": "t"}]}))], []))
    with pytest.raises(EstateError, match="not installed"):
        e.seed("p")
    e2 = Estate("x", "u", "pw", _transport([("sys_properties", (401, "nope"))], []))
    with pytest.raises(EstateError, match="401"):
        e2.get_property("provy.iam.fault")


def test_open_request_copies_ids_from_an_existing_poc_request_and_refuses_without_one():
    class C:
        def __init__(self, rows): self.rows, self.created = rows, []
        def query(self, table, params=None):
            if table == "SystemAgentPersona": return [{"id": "p1", "Name": "Provy IAM POC: Access orchestrator"}]
            if table == "User": return [{"id": "u0"}, {"id": "agent-user", "AgentPersona": "p1"}]
            return self.rows
        def create(self, table, body): self.created.append((table, body)); return {"Number": "REQ5"}
    tpl = {"ShortDescription": "[provy-iam-poc] seed", "Requestor": "r", "RequestedFor": "f", "AssignmentGroup": "g", "Classification": "c"}
    c = C([{"ShortDescription": "unrelated"}, tpl])
    assert make_open_request(c, "provy.iam.spike01", "vpn_missed")() == "REQ5"
    table, body = c.created[0]
    assert table == "ITSMRequest" and body["ShortDescription"].startswith("[provy-iam-poc] Leaver (vpn_missed)") and body["AssignmentGroup"] == "g"
    assert "Systems: directory, sso, vpn, saas_token" in body["Description"] and "Person: provy.iam.spike01" in body["Description"]
    assert body["Assignee"] == "agent-user"                 # an unassigned request is never picked up
    with pytest.raises(RuntimeError, match="raise one request by hand"):
        make_open_request(C([{"ShortDescription": "unrelated"}]), "p", "k")()


def test_resync_needs_a_queued_event_and_stale_events_are_cleared_before_and_after():
    e = FakeEstate(); run(BY_KEY["recreated_by_sync"], e, "direct", sleep=lambda s: None)
    kinds = [x[0] for x in e.log]
    assert kinds.count("events_cleared") == 2 and "resync_queued" in kinds
    assert kinds.index("events_cleared") < kinds.index("seed")           # cleared before the first seed
    assert e.pending is False


def test_the_sync_scenario_gets_longer_than_a_request_does():
    from multi_vendor.runner import SYNC_WAIT
    class Slow(FakeEstate):
        def set_job_active(self, name, on):
            self.jobs[name] = on
            if name == SYNC_JOB and on: self._arm = 0
        def readings(self, p):
            if hasattr(self, "_arm"):
                self._arm += 1
                if self._arm >= 20: self.active = True       # re-created after about 200 seconds of polling
            return super().readings(p)
    sleep, now = _clock()
    r = run(BY_KEY["recreated_by_sync"], Slow(), "direct", wait=30, poll=10, sleep=sleep, now=now)
    assert r.matches_expectation and SYNC_WAIT > 200


def test_desk_request_is_a_plain_ticket_for_the_desk_with_no_access_team_marker():
    from multi_vendor.agent_request import make_open_desk_request
    class C:
        created = []
        def query(self, table, params=None):
            if table == "SystemAgentPersona": return [{"id": "pd", "Name": "Service Desk Agent"}]
            if table == "User": return [{"id": "desk-user", "AgentPersona": "pd"}]
            return [{"ShortDescription": "[provy-iam-poc] seed", "Requestor": "r", "RequestedFor": "f", "AssignmentGroup": "g", "Classification": "c"}]
        def create(self, table, body): self.created.append((table, body)); return {"data": {"Number": "REQ77"}}
    c = C()
    assert make_open_desk_request(c, "provy.iam.spike01", "vpn_missed")() == "REQ77"
    table, body = c.created[0]
    assert table == "ITSMRequest" and body["Assignee"] == "desk-user" and body["ShortDescription"].startswith("[provy-servicely] Leaver: provy.iam.spike01") and "vpn_missed" not in body["ShortDescription"] + body["Description"]
    assert "[provy-iam-poc]" not in body["ShortDescription"] + body["Description"]      # the desk must recognise it, not be told
    assert "Systems: directory, sso, vpn, saas_token" in body["Description"]


def test_agentic_direct_goes_through_the_agents_door_not_the_plain_apply():
    e = FakeEstate()
    r = run(BY_KEY["vpn_missed"], e, "direct", agentic=True, sleep=lambda s: None)
    assert ("agent_apply", "provy-iam-apply-change", "scenario-vpn_missed") in e.log
    assert r.matches_expectation and "ServiceNow-side agents" in r.notes[0]


def test_the_cli_always_switches_the_flag_back_off(monkeypatch):
    """Even when a run raises, provy.iam.agentic must not be left on."""
    import importlib.util, os, sys
    spec = importlib.util.spec_from_file_location("rmv", os.path.join(os.path.dirname(__file__), "..", "scripts", "run_multi_vendor.py"))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    flags = []
    class E(FakeEstate):
        def set_agentic(self, on): flags.append(on)
        def agent_apply(self, *a, **k): raise RuntimeError("boom")
    monkeypatch.setattr(mod, "_estate", lambda: E())
    monkeypatch.setattr(sys, "argv", ["x", "run", "clean_leaver", "--agentic"])
    with pytest.raises(RuntimeError):
        mod.main()
    assert flags == [True, False]
