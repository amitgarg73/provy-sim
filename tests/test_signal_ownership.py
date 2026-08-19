"""Who a failure is attributed to, per pack.

⛔ WITHOUT signal_owners() EVERY CONDITION BLAMES THE REVIEWER. The shared helper in engine/pack.py
stamps every contract signal onto the reviewer's closing message unless the pack says otherwise, and
Provy attributes a condition to whichever agent's trace carried the field. So a pack with no owner
map registers its whole contract against one agent.

Measured on the live Servicely legal fleet before this map existed: all three trace-side signals
carried `source_hint: reviewer`, and a full sweep of 13 scenarios over 70 sessions produced incidents
on 2 of its 4 agents. Only `edwin` declared owners; the other nine packs did not.
"""
import pytest


def test_pack_declares_signal_owners(pack):
    """A pack with no owner map cannot spread blame past the reviewer."""
    assert pack.signal_owners(), (
        f"{type(pack).__name__} declares no signal_owners, so every condition it grades will be "
        f"attributed to the reviewer"
    )


def test_every_owner_is_a_real_agent(pack):
    """⛔ An owner naming an agent that does not exist is worse than no owner: the stamp silently
    falls back to the reviewer and the map reads as though it were working."""
    roster = {a.name for a in pack.agents()}
    for signal, agent in pack.signal_owners().items():
        assert agent in roster, (
            f"{type(pack).__name__}: signal_owners maps {signal!r} to {agent!r}, which is not one of "
            f"{sorted(roster)}"
        )


def test_owners_cover_the_contract_except_timeliness(pack):
    """Every condition gets an owner unless it is a whole-run property.

    An SLA belongs to the run rather than to any one step, so it is deliberately unowned and falls
    to the reviewer, which is the honest answer. Anything else being unowned is an oversight.
    """
    owned = set(pack.signal_owners())
    unowned = [c.signal for c in pack.contract()
               if c.signal not in owned and not _is_timeliness(c.signal)]
    assert not unowned, (
        f"{type(pack).__name__}: {unowned} have no owner and are not timeliness signals, so a failure "
        f"there will be blamed on the reviewer"
    )


def test_blame_is_not_concentrated_on_one_agent(pack):
    """⛔ THE POINT OF THE MAP. A map that sends every signal to the same agent spreads nothing and
    would pass every other test in this file."""
    owners = pack.signal_owners()
    distinct = set(owners.values())
    assert len(distinct) >= 2, (
        f"{type(pack).__name__}: every owned signal points at {distinct}, so the contract still blames one "
        f"agent"
    )


#: Signals that measure WHEN the work landed rather than what it did. These are a property of the
#: whole run, so they are allowed to stay unowned and fall to the reviewer. Listed explicitly rather
#: than matched by substring: a fuzzy rule would quietly excuse a real signal that happened to
#: contain the word, which is how a test stops protecting anything.
TIMELINESS = {"sla_met", "deadline_met", "first_response_time_met", "resolution_time_met"}


def _is_timeliness(signal: str) -> bool:
    return signal in TIMELINESS
