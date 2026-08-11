"""Ground truth that outlives the runner (sim-control #8).

The ledger lives in a gitignored file inside the Actions runner and dies with it, so a batch could
never be settled by a later run. That is why "delayed outcome" was blocked, and why a green
un-reconciled batch could not be followed by those same items settling.
"""
import json
from engine.emitter import ProvyEmitter
from engine.reconcile import _minimal_result


def _rec(entity_id="CLM-1", label="success", value=1.0):
    return {"entity_id": entity_id, "session_id": "sess-" + entity_id,
            "outcome_label": label, "outcome_value": value,
            "real_signals": {"payout_settled": 1}}


def test_stored_payload_is_identical_to_a_posted_one():
    """⛔ THE POINT OF THE WHOLE DESIGN. The console stores what the sim built and forwards it
    unchanged. If the builder and the poster could differ, a settled outcome would describe something
    the live path never sends, and only the delayed path would be wrong."""
    em = ProvyEmitter(ingest_key="provy_test", is_simulated=False, capture=True)
    result = _minimal_result(_rec())

    built = em.outcome_payload(result, occurred_at="2026-08-11T00:00:00+00:00")
    em.outcome(result, occurred_at="2026-08-11T00:00:00+00:00")
    posted = em.sent[-1]["payload"]

    assert built == posted
    assert em.sent[-1]["path"] == "/api/ingest/outcome"


def test_payload_carries_what_provy_pairs_on():
    em = ProvyEmitter(ingest_key="provy_test", is_simulated=False, capture=True)
    p = em.outcome_payload(_minimal_result(_rec("CLM-77")))
    # Provy pairs by entity id, not by date, which is what lets an outcome arrive days later.
    assert p["entity_id"] == "CLM-77"
    assert p["label"] == "success"
    assert p["source"] == "confirmed"
    # Must survive JSON: the console stores it as jsonb and replays the bytes.
    assert json.loads(json.dumps(p, default=str))["entity_id"] == "CLM-77"


def test_building_a_payload_sends_nothing():
    """The entire feature is that the outcome arrives LATER. A builder that posted would settle the
    batch the moment it was handed over and delete the demo."""
    em = ProvyEmitter(ingest_key="provy_test", is_simulated=False, capture=True)
    em.outcome_payload(_minimal_result(_rec()))
    assert em.sent == []


def test_a_failure_is_carried_through_as_fail():
    em = ProvyEmitter(ingest_key="provy_test", is_simulated=False, capture=True)
    p = em.outcome_payload(_minimal_result(_rec(label="fail", value=0.0)))
    assert p["label"] == "fail"


def test_which_packs_the_sim_owns_outcomes_for_is_mirrored_in_the_console():
    """⛔ TWO REPOS, ONE FACT. The console decides which scenarios to OFFER from its own copy of this
    flag (lib/packs.ts, ownsOutcome). If a pack stops owning its outcome here and the console is not
    told, it will offer the un-reconciled baseline and the settle step on a fleet where neither can
    work: nothing to withhold, nothing held back to deliver.

    If this fails, update `ownsOutcome` in provy-sim-control/lib/packs.ts to match, and its
    scenarios test which asserts the same list.
    """
    from packs import PACKS, get_pack
    not_owned = sorted(n for n in PACKS if not getattr(get_pack(n), "owns_outcome", True))
    assert not_owned == ["itsm"], (
        f"outcome ownership changed: {not_owned}. Mirror it in provy-sim-control/lib/packs.ts."
    )


def test_every_pack_that_owns_its_outcome_can_build_a_payload_to_hand_over():
    """The hand-over has to work for every pack, not just the one it was built against."""
    from packs import PACKS, get_pack
    em = ProvyEmitter(ingest_key="provy_test", is_simulated=False, capture=True)
    for name in PACKS:
        if not getattr(get_pack(name), "owns_outcome", True):
            continue
        p = em.outcome_payload(_minimal_result(_rec(f"{name}-1")))
        assert p["entity_id"] == f"{name}-1", name
        assert p["source"] == "confirmed", name
