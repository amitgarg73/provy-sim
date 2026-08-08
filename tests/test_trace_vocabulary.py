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


def _reviewer_payload(pack, seed=5):
    """Run one full pipeline and return the payload stamped on the reviewer's closing message."""
    rng = random.Random(seed)
    item, gt = pack.generate_work_item(rng)
    ctx = make_ctx(seed=seed)
    run = pack.run_pipeline(item, gt, ctx)
    reviewer = pack.lever_manifest().reviewer_agent
    for t in run.traces:
        if t.agent == reviewer and t.step_type == "agent_message" and t.payload_extra:
            return run, t.payload_extra
    return run, {}


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
    assert payload, f"{pack.workflow}: nothing stamped on the reviewer message"
