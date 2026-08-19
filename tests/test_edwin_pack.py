"""The Edwin pack's own two failure modes, and the one structural thing it does that no other
pack does: run a single agent many times inside one session.

The shared suite already drives this pack through signal mapping, clean-run grading, trace
vocabulary and lever behaviour (it is in the `pack` fixture). What is here is only what is specific
to an agentic-AIOps fleet.
"""
import random

import pytest

from conftest import make_ctx
from engine.contract import grade
from engine.levers import LeverConfig
from packs.edwin.pack import FAULTS, EdwinPack


def _run(levers, seed=0, index=0):
    pack = EdwinPack()
    rng = random.Random(seed)
    item, gt = pack.generate_work_item(rng)
    ctx = make_ctx(levers=LeverConfig(levers), seed=seed, index=index, workflow="edwin")
    return pack, item, gt, pack.run_pipeline(item, gt, ctx)


def _seed_where(change_caused: bool, limit=400):
    """Find a seed whose generated work item is (or is not) caused by a change."""
    pack = EdwinPack()
    for s in range(limit):
        _, gt = pack.generate_work_item(random.Random(s))
        if gt["change_caused"] is change_caused:
            return s
    raise AssertionError("no such seed")


def test_clean_run_meets_every_condition():
    pack, _, _, r = _run({})
    g = grade(pack.contract(), r.estimated_signals, r.real_signals)
    assert g["met"] == g["total"] == 6
    assert r.outcome_label == "success"


def test_change_blind_needs_a_change_caused_fault():
    """⛔ THE POINT OF THE LEVER. An investigation that skipped the change lookup on a fault no
    change caused has not made a mistake, so firing there would manufacture a failure the product
    could not have made and would inflate the injected-truth count the scoreboard scores against."""
    seed = _seed_where(change_caused=False)
    _, _, gt, r = _run({"change_blind": {"rate": 1.0}}, seed=seed)
    assert gt["change_caused"] is False
    assert not any(f.lever == "change_blind" for f in r.faults)


def test_change_blind_diverges_estimated_from_real():
    seed = _seed_where(change_caused=True)
    pack, _, gt, r = _run({"change_blind": {"rate": 1.0}}, seed=seed)
    assert gt["change_caused"] is True
    assert [f.lever for f in r.faults if f.lever == "change_blind"] == ["change_blind"]

    # Reality: the named cause was wrong and the incident came back.
    assert r.real_signals["rca_correct"] is False
    assert r.real_signals["reopened_7d"] is True
    assert r.outcome_label == "fail"

    # The trace admits only the missing change context, and it does so in the fleet's own words.
    assert r.estimated_signals["change_data_used"] is False
    msg = next(t for t in r.traces if t.agent == "change_agent" and t.step_type == "agent_message")
    assert msg.payload_extra["change_context_included"] is False
    assert "change_data_used" not in msg.payload_extra

    # And the RCA still went out confident, which is what makes it worth catching.
    rca = next(t for t in r.traces if t.agent == "rca_agent" and t.step_type == "agent_message")
    assert rca.payload_extra["confidence"] == "HIGH"

    # The evals still pass: nothing inside the run knows it was wrong.
    assert all(e.passed for e in r.evals)


def test_correlation_split_breaks_only_the_correlation_condition():
    pack, _, _, r = _run({"correlation_split": {"rate": 1.0}}, seed=_seed_where(change_caused=False))
    assert any(f.lever == "correlation_split" for f in r.faults)
    assert r.real_signals["correlation_held"] is False
    assert r.real_signals["rca_correct"] is True     # the cause was right, the folding was not
    per = {c["id"]: c for c in grade(pack.contract(), r.estimated_signals, r.real_signals)["per_condition"]}
    assert per["c5"]["met"] is False
    assert per["c1"]["met"] is True


def test_edwin_injectors_defer_to_a_generic_lever():
    """One primary cause per run. A run that a generic lever already shaped must not also carry an
    Edwin injector, or attribution stops being 1:1 and the scoreboard scores a run twice."""
    seed = _seed_where(change_caused=True)
    _, _, _, r = _run({"silent_wrong": {"rate": 1.0},
                       "change_blind": {"rate": 1.0},
                       "correlation_split": {"rate": 1.0}}, seed=seed)
    primary = [f.lever for f in r.faults
               if f.lever in ("silent_wrong", "change_blind", "correlation_split")]
    assert primary == ["silent_wrong"]


def test_one_agent_runs_many_times_in_one_session():
    """The chatbot half, at the level that needs no engine change. Every other pack runs each agent
    exactly once; the operator conversation runs insights_agent repeatedly inside a single session,
    and every turn carries the same entity and session."""
    _, _, _, r = _run({})
    chat = [t for t in r.traces if t.agent == "insights_agent"]
    assert len(chat) >= 2
    assert [t.payload_extra["chat_turn"] for t in chat] == list(range(1, len(chat) + 1))
    assert all(t.entity_id == r.entity_id for t in chat)
    assert all(t.payload_extra.get("operator_question") for t in chat)


def test_the_conversation_stays_inside_one_work_item():
    """⛔ THE LIMIT, ASSERTED SO IT CANNOT DRIFT. This pack does NOT answer the multi-work-item
    chatbot question: one session still holds exactly one entity. If a future change makes a session
    carry several work items, this test is the one that should fail first and be rewritten
    deliberately, rather than the grain moving by accident."""
    _, _, _, r = _run({})
    entities = {t.entity_id for t in r.traces if t.entity_id}
    assert entities == {r.entity_id}


@pytest.mark.parametrize("fault,expected_group", [(k, v[1]) for k, v in FAULTS.items()])
def test_every_fault_has_an_owning_group(fault, expected_group):
    assert expected_group and isinstance(expected_group, str)


# The console-parity check that used to live here (contract, agent roster, trace aliases)
# now runs for ALL TEN packs in tests/test_console_contract_parity.py. It was edwin-only for
# weeks, and edwin was the only pack that never drifted (argus#638).


def test_change_caused_faults_are_a_real_share_of_the_library():
    """change_blind can only fire on the change-caused half. If the library drifted to mostly
    non-change faults the lever would quietly stop producing its signature failure."""
    caused = sum(1 for _, _, c in FAULTS.values() if c)
    assert 2 <= caused <= len(FAULTS) - 2
