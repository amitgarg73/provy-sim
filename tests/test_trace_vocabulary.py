"""The agents' vocabulary must not be the contract's vocabulary.

A real tenant instruments its agents long before anyone writes an outcome contract, so what the
traces carry is the agents' own field names. The sim used to key the trace-side signals by the
CONTRACT's own name, which made every pack identity-by-construction: Provy's mapping step had
nothing to solve, because the answer was already in the data.

These tests pin the property so it cannot quietly come back.
"""
import random

import pytest

from conftest import make_ctx


def _run_and_payloads(pack, seed=5):
    """Run one full pipeline. Returns (run, merged payload across every agent message, by-agent map).

    ⛔ THIS USED TO READ THE REVIEWER'S MESSAGE ONLY, and that assumption is what `signal_owners()`
    broke. Estimated signals are now stamped on the agent that OWNS each one, because the agent whose
    trace carries the field is the agent Provy attributes the failure to. The vocabulary property
    these tests exist for is about the whole trace, not about one step of it."""
    rng = random.Random(seed)
    item, gt = pack.generate_work_item(rng)
    ctx = make_ctx(seed=seed)
    run = pack.run_pipeline(item, gt, ctx)
    by_agent: dict[str, dict] = {}
    merged: dict = {}
    for t in run.traces:
        if t.step_type == "agent_message" and t.payload_extra:
            by_agent.setdefault(t.agent, {}).update(t.payload_extra)
            merged.update(t.payload_extra)
    return run, merged, by_agent


def _reviewer_payload(pack, seed=5):
    """Back-compat shim for the tests that genuinely mean 'somewhere on the trace'."""
    run, merged, _ = _run_and_payloads(pack, seed)
    return run, merged


def test_aliases_only_rename_signals_the_contract_actually_has(pack):
    """An alias for a signal no condition grades is dead config that will rot."""
    signals = {c.signal for c in pack.contract()}
    for src in pack.trace_aliases():
        assert src in signals, f"{pack.workflow}: alias for '{src}', which is not a contract signal"


def test_aliases_do_not_collide(pack):
    """Two contract signals renamed to one field would make the pair unmappable, not just aliased."""
    out = list(pack.trace_aliases().values())
    assert len(out) == len(set(out)), f"{pack.workflow}: two signals share one emitted name"


def test_alias_is_not_the_contract_name(pack):
    """A no-op alias reads as 'we renamed this' while handing over the contract's word."""
    for src, dst in pack.trace_aliases().items():
        assert src != dst, f"{pack.workflow}: '{src}' aliased to itself"


def test_grading_still_uses_contract_vocabulary(pack):
    """⛔ Renaming is a TRACE-side change only.

    `estimated_signals` and `real_signals` are what grading and the outcome push read, and they must
    keep speaking the contract's language. If the rename leaked into them, every condition would
    silently stop grading and the run would look clean because nothing was measured."""
    run, _ = _reviewer_payload(pack)
    for c in pack.contract():
        present = c.signal in run.estimated_signals or c.signal in run.real_signals
        assert present, f"{pack.workflow}: {c.signal} vanished from the graded signals"
    for dst in pack.trace_aliases().values():
        assert dst not in run.estimated_signals, f"{pack.workflow}: emitted name '{dst}' leaked into grading"


def test_trace_carries_the_agents_names_not_the_contracts(pack):
    """The point of the whole change: what lands on the trace is the agents' vocabulary."""
    aliases = pack.trace_aliases()
    if not aliases:
        pytest.skip(f"{pack.workflow} emits the contract's names, which is a deliberate choice")
    run, payload = _reviewer_payload(pack)
    if not payload:
        pytest.skip(f"{pack.workflow} stamps no reviewer payload")
    for src, dst in aliases.items():
        if src in run.estimated_signals:
            assert dst in payload, f"{pack.workflow}: trace missing the agent's name '{dst}'"
            assert src not in payload, (
                f"{pack.workflow}: trace still carries the contract's name '{src}' — "
                "identity is being handed to Provy")


def test_every_pack_stamps_something(pack):
    """A pack that stamps nothing cannot name a cause for any condition, which is argus#446."""
    run, payload = _reviewer_payload(pack)
    if not run.estimated_signals:
        pytest.skip(f"{pack.workflow} has no estimated signals on this run")
    assert payload, f"{pack.workflow}: nothing stamped on any agent message"


def test_owned_signals_land_on_their_owner(pack):
    """⛔ WHICH AGENT CARRIES THE FIELD IS WHICH AGENT GETS BLAMED.

    Provy's ingest registry records a source agent per emitted signal key, and `bindingOf()` returns
    that agent as the condition's cause. So a signal stamped on the wrong step produces a confident,
    well-formed, WRONG attribution, which is worse than no attribution at all.

    Measured on edwin before `signal_owners()` existed: all six contract signals registered against
    the reviewer, so the condition that exists to catch the change agent would have named the agent
    that merely summarised its work."""
    owners = pack.signal_owners()
    if not owners:
        pytest.skip(f"{pack.workflow} declares no owners; everything falls to the reviewer by design")
    aliases = pack.trace_aliases()
    agents = {a.name for a in pack.agents()}
    run, _, by_agent = _run_and_payloads(pack)

    for signal, owner in owners.items():
        assert owner in agents, f"{pack.workflow}: '{signal}' owned by '{owner}', which is not an agent"
        if signal not in run.estimated_signals:
            continue
        emitted_as = aliases.get(signal, signal)
        # The owner spoke on this run, so the field must be on ITS message and on no other.
        if owner not in by_agent:
            continue   # lever skipped the owner; the helper falls back to the reviewer on purpose
        assert emitted_as in by_agent[owner], (
            f"{pack.workflow}: '{emitted_as}' is owned by {owner} but is not on its trace")
        strays = [a for a, p in by_agent.items() if a != owner and emitted_as in p]
        assert not strays, (
            f"{pack.workflow}: '{emitted_as}' also appears on {strays}, so attribution is ambiguous")


def test_unowned_signals_still_reach_the_trace(pack):
    """Ownership must not lose a signal. Anything unowned falls to the reviewer, as before."""
    run, merged, _ = _run_and_payloads(pack)
    aliases = pack.trace_aliases()
    graded = {c.signal for c in pack.contract() if c.side in ('trace', 'both')}
    for signal in graded:
        if signal not in run.estimated_signals:
            continue
        emitted_as = aliases.get(signal, signal)
        assert emitted_as in merged, (
            f"{pack.workflow}: '{emitted_as}' reached no trace at all, so its Estimated side is "
            "unreadable and the condition can never name a cause")


def test_no_aliased_signal_ever_reaches_a_trace(pack):
    """⛔ THE WHOLE POINT, AND IT TOOK TWO PASSES TO GET RIGHT.

    The first attempt aliased only the reviewer's stamped summary. The settlement feed in
    `engine/commitment.py` — and a second copy inside the Stripe pack — went on writing the
    CONTRACT's own field names onto the booker's tool call, so Provy auto-bound every condition and
    the mapping step still had nothing to solve. Provy's contract page said "the same name your
    contract uses, so nothing had to be recorded" on all five conditions.

    A signal that has an alias must appear NOWHERE on a trace under its contract name, on either the
    clean path or the failure path. Signals with no alias are untouched, which is deliberate: a real
    tenant has some names that happen to match.
    """
    aliased = set(pack.trace_aliases())
    if not aliased:
        pytest.skip(f"{pack.workflow} emits the contract's names, which is a deliberate choice")

    leaked: set[str] = set()
    for seed in range(6):
        rng = random.Random(seed)
        item, gt = pack.generate_work_item(rng)
        run = pack.run_pipeline(item, gt, make_ctx(seed=seed))
        for t in run.traces:
            keys = set((t.tool_output or {}).keys()) | set((t.payload_extra or {}).keys())
            leaked |= keys & aliased
    assert not leaked, (
        f"{pack.workflow}: {sorted(leaked)} reached a trace under the contract's own name — "
        "identity is being handed to Provy again")
