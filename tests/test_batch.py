"""The failure levers and the random batch: what each lever does, that a batch is repeatable, that its mix is honoured, and that a bad item or a timeout never leaves the estate dirty."""
from collections import Counter

import pytest

from multi_vendor.batch import DEFAULT_WEIGHTS, BatchItem, parse_mix, person_for, plan_batch, run_batch
from multi_vendor.grade import grade
from multi_vendor.runner import SYNC_JOB, WORKER_JOB, run
from multi_vendor.scenarios import BY_KEY, SCENARIOS
from tests.fake_estate import FakeEstate


def _run(key, estate=None, **kw):
    e = estate or FakeEstate()
    return e, run(BY_KEY[key], e, "direct", sleep=lambda s: None, **kw)


# ---- the levers --------------------------------------------------------------------------------------------------------------------------------------------------------------

def test_wrong_person_leaves_the_named_person_with_access_and_changes_a_bystander():
    e, r = _run("wrong_person")
    assert not r.verdict.held and r.reading["account_active"] is True
    assert r.reading["others_changed"] >= 1 and any("did not name" in f for f in r.verdict.failures)


def test_over_reach_ends_the_target_and_still_fails_because_someone_else_changed():
    e, r = _run("over_reach")
    assert r.reading["account_active"] is False and r.reading["access_remaining"] == 0     # the work LOOKS done
    assert not r.verdict.held and r.reading["others_changed"] == 2


def test_the_guard_refusing_a_policy_breaking_plan_leaves_everyone_right():
    e, r = _run("policy_refused")
    assert r.verdict.held and r.reading["others_changed"] == 0
    assert ("agentic", True) in e.log and e.log[-1] != ("agentic", True)                     # switched on for the run and off again


def test_the_same_plan_through_a_bypassed_guard_hurts_the_wrong_person():
    e, r = _run("policy_bypassed")
    assert not r.verdict.held and r.reading["account_active"] is True and r.reading["others_changed"] >= 1


def test_a_grant_that_did_not_expire_fails_and_the_control_passes():
    assert not _run("jit_not_expired")[1].verdict.held
    assert _run("jit_expired")[1].verdict.held


def test_an_optimistic_verifier_does_not_change_what_the_estate_shows():
    e, r = _run("verifier_claims_ok")
    assert not r.verdict.held and r.reading["access_remaining"] == 1


def test_the_jit_grade_is_about_expiry_not_about_a_leaver():
    assert grade({"expired_grants_active": 0}, "jit").held
    assert not grade({"expired_grants_active": 2}, "jit").held
    assert not grade({}, "jit").held                                                           # unknown is never held
    assert not grade({"account_active": False, "groups_held": 0, "access_remaining": 0, "systems_checked": 4, "others_changed": 1}).held


def test_every_failure_case_has_a_reason_it_fails_and_the_set_still_has_controls():
    assert all(s.why for s in SCENARIOS)
    assert sum(s.should_hold for s in SCENARIOS) >= 3 and sum(not s.should_hold for s in SCENARIOS) >= 7


def test_a_case_whose_servicely_half_is_not_built_refuses_the_real_flow_loudly():
    with pytest.raises(ValueError, match="not wired for the Servicely flow"):
        run(BY_KEY["wrong_person"], FakeEstate(), "servicely", open_request=lambda: "R", sleep=lambda s: None)


# ---- the planner -------------------------------------------------------------------------------------------------------------------------------------------------------------

def test_the_same_seed_gives_the_same_batch_and_another_seed_does_not():
    assert plan_batch(40, seed=7) == plan_batch(40, seed=7)
    assert [i.key for i in plan_batch(40, seed=7)] != [i.key for i in plan_batch(40, seed=8)]


@pytest.mark.parametrize("total", [1, 7, 30, 101])
def test_the_counts_always_add_up_to_the_total_and_people_are_distinct(total):
    b = plan_batch(total, seed=3)
    assert len(b) == total and len({i.person for i in b}) == total
    assert all(i.person.startswith("provy.iam.") for i in b)


def test_the_mix_is_honoured_within_a_rounding_step():
    b = plan_batch(100, {"clean_leaver": 70, "vpn_missed": 30}, seed=1)
    c = Counter(i.key for i in b)
    assert c == {"clean_leaver": 70, "vpn_missed": 30}


def test_zero_weights_and_unknown_scenarios_are_refused_in_plain_words():
    with pytest.raises(ValueError, match="unknown scenario"):
        plan_batch(5, {"clean_leaver": 1, "nonsense": 1})
    with pytest.raises(ValueError, match="no scenario with a weight"):
        plan_batch(5, {"clean_leaver": 0})
    with pytest.raises(ValueError, match="at least one"):
        plan_batch(0)


def test_the_default_mix_names_only_real_scenarios_and_has_failures_of_each_family():
    assert set(DEFAULT_WEIGHTS) <= set(BY_KEY)
    b = plan_batch(60, seed=2)
    keys = {i.key for i in b}
    assert {"clean_leaver", "vpn_missed", "over_reach"} <= keys
    assert sum(1 for i in b if not BY_KEY[i.key].should_hold) >= 6                             # enough failures for a cause to be found


def test_parse_mix_reads_weights_and_names_the_bad_part():
    assert parse_mix("clean_leaver=50, vpn_missed=2.5") == {"clean_leaver": 50.0, "vpn_missed": 2.5}
    with pytest.raises(ValueError, match="bad mix entry 'oops'"):
        parse_mix("clean_leaver=1,oops")


# ---- running a batch --------------------------------------------------------------------------------------------------------------------------------------------------------

def test_a_batch_runs_every_item_with_its_own_fault_and_every_outcome_matches():
    items = plan_batch(30, seed=5)
    e = FakeEstate()
    # the fake's resync needs the queued event; give the batch a generous wait on a clock that never runs out
    res = run_batch(items, e, "direct", sleep=lambda s: None)
    sm = res.summary()
    assert sm["total"] == 30 and sm["errors"] == 0 and sm["unexpected"] == 0
    assert sm["held"] + sm["failed"] == 30 and sm["failed"] >= 6 and sm["held"] >= 6
    assert any(x[0] == "person_faults" and len(x[1]) >= 5 for x in e.log)                       # different failures carried for different people in ONE map


def test_a_batch_cleans_up_every_person_and_the_flags_even_when_an_item_breaks():
    items = [BatchItem(1, "clean_leaver", person_for(1)), BatchItem(2, "policy_refused", person_for(2)), BatchItem(3, "vpn_missed", person_for(3))]
    class Boom(FakeEstate):
        def agent_apply(self, command, params, queue_item=None):
            if params["person"] == person_for(2):
                raise RuntimeError("the door answered 500")
            return super().agent_apply(command, params, queue_item)
    e = Boom()
    res = run_batch(items, e, "direct", sleep=lambda s: None)
    assert set(res.errors) == {2} and "500" in res.errors[2]                                     # one bad item is reported, the others still ran
    assert 1 in res.verdicts and 3 in res.verdicts
    assert e.fault == {} and e.person_faults == {} and e.jobs[SYNC_JOB] is False and e.agentic is False
    assert all(p["active"] and p["left"] == 5 for k, p in e.people.items() if not k.startswith("provy.iam.bystander"))


def test_a_batch_that_needs_the_agents_switches_them_on_once_and_off_again():
    items = [BatchItem(1, "policy_refused", person_for(1)), BatchItem(2, "verifier_claims_ok", person_for(2))]
    e = FakeEstate()
    run_batch(items, e, "direct", sleep=lambda s: None)
    assert [x for x in e.log if x[0] == "agentic"] == [("agentic", True), ("agentic", False)]


def test_the_servicely_batch_refuses_cases_it_cannot_run_for_real_and_names_them():
    items = [BatchItem(1, "clean_leaver", person_for(1)), BatchItem(2, "wrong_person", person_for(2)), BatchItem(3, "jit_expired", person_for(3))]
    with pytest.raises(ValueError, match="jit_expired, wrong_person"):
        run_batch(items, FakeEstate(), "servicely", open_request=lambda it, s: (lambda: "R"), sleep=lambda s: None)


def test_the_servicely_batch_opens_one_request_per_item_and_waits_for_all_of_them():
    items = [BatchItem(1, "clean_leaver", person_for(1)), BatchItem(2, "vpn_missed", person_for(2))]
    e = FakeEstate()
    opened = []
    def opener(it, scn):
        def go():
            opened.append(it.person)
            e._apply("provy-iam-apply-change", {"person": it.person})                            # the worker applies it
            return "REQ"
        return go
    res = run_batch(items, e, "servicely", open_request=opener, sleep=lambda s: None, wait=5, poll=1)
    assert opened == [person_for(1), person_for(2)] and not res.errors and res.summary()["unexpected"] == 0
    assert e.jobs[WORKER_JOB] is True                                                            # left as the single-run path leaves it: the scenario owns the worker job


def test_a_direct_agentic_run_mints_a_unique_work_item_id_so_its_outcome_can_settle():
    e = FakeEstate()
    seen = []
    orig = e.agent_apply
    e.agent_apply = lambda c, p, queue_item=None: (seen.append(p.get("correlation")), orig(c, p, queue_item))[1]
    run(BY_KEY["clean_leaver"], e, "direct", agentic=True, sleep=lambda s: None)
    run_batch(plan_batch(3, {"clean_leaver": 1}, 1), e, "direct", agentic=True, sleep=lambda s: None)
    assert seen[0].startswith("SIM") and seen[0].endswith("-clean_leaver")
    assert len(set(seen[1:])) == 3 and all(x.startswith("SIM") for x in seen)


def test_a_batch_uses_the_agents_by_default_and_can_be_told_not_to():
    only_clean = plan_batch(3, {"clean_leaver": 1}, 1)
    on = FakeEstate(); run_batch(only_clean, on, "direct", sleep=lambda s: None)
    assert ("agentic", True) in on.log and any(x[0] == "agent_apply" for x in on.log)
    off = FakeEstate(); run_batch(only_clean, off, "direct", agents=False, sleep=lambda s: None)
    assert not any(x[0] in ("agentic", "agent_apply") for x in off.log)
