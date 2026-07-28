"""The ticket's journey, and the rule the whole model exists to enforce:

    time passes for a reason, and the reason is a decision the agent made.

Before this, the agent triaged, routed and resolved in one continuous action and nothing it did
cost any time. The only thing that could breach a response target was the wall clock of the batch,
so on the 28 July run positions 1-7 met the target and 8-12 missed it, a clean step function with
nothing about the ticket deciding it. A condition settled that way has no cause to find, which is
exactly what the incident card kept reporting.

These tests use a fake clock, so a five-minute hold costs nothing to assert.
"""
import inspect
import random

import pytest

from conftest import make_ctx
from engine.desk import Desk
from engine.levers import LeverConfig
from engine.servicenow import DEMO_RESPONSE_TARGET_S
from engine.types import Wait
from packs.itsm.pack import ItsmPack
from test_itsm_pack import FakeServiceNow, _INCIDENTS


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        if seconds > 0:
            self.slept.append(seconds)
            self.t += seconds


def drive(pack, item, ctx):
    """Run a journey to completion, collecting its waits without waiting for any of them."""
    waits, journey = [], pack.journey(item, {}, ctx)
    while True:
        try:
            waits.append(journey.send(None))
        except StopIteration as done:
            return waits, done.value


def journey_for(seed=1, rates=None, priority="3"):
    incidents = [dict(_INCIDENTS[0], priority=priority)]
    pack = ItsmPack(client=FakeServiceNow(incidents))
    ctx = make_ctx(levers=LeverConfig(rates or {}), seed=seed, workflow="itsm")
    item, _ = pack.generate_work_item(random.Random(seed))
    return pack, item, ctx


# ── the rule ────────────────────────────────────────────────────────────────
def test_every_wait_is_either_caused_or_ordinary():
    """A wait with no cause is the work taking as long as work takes. A wait WITH one is the
    thing the incident card needs to name. Nothing may be uncategorised."""
    pack, item, ctx = journey_for()
    waits, _ = drive(pack, item, ctx)
    assert waits
    for w in waits:
        assert isinstance(w, Wait)
        assert w.seconds > 0
        assert w.cause in (None, "router", "resolver")


def test_a_clean_run_never_spends_enough_to_breach():
    """Routed right, diagnosed properly: the ticket must come in well inside its promise, or the
    condition would fail for tickets nobody mishandled."""
    pack, item, ctx = journey_for(rates={"misroute": 0.0, "misclassify": 0.0,
                                         "shallow_diagnosis": 0.0})
    waits, _ = drive(pack, item, ctx)
    assert sum(w.seconds for w in waits) < DEMO_RESPONSE_TARGET_S["3"]
    assert all(w.cause is None for w in waits)


def test_a_misroute_costs_more_than_the_promise():
    """The point of the whole design: the ticket sits in the wrong team's queue long enough to
    blow the target, so the breach has a cause instead of a position."""
    pack, item, ctx = journey_for(rates={"misroute": 1.0, "shallow_diagnosis": 0.0})
    waits, result = drive(pack, item, ctx)
    assert sum(w.seconds for w in waits) > DEMO_RESPONSE_TARGET_S["3"]
    assert any(w.cause == "router" for w in waits)
    assert any(w.reason == "queued_with_the_wrong_team" for w in waits)
    # And it is visible in the record, not only in the trace.
    tools = [t.tool_name for t in result.traces if t.tool_name]
    assert "servicenow.reassign_incident" in tools


def test_a_shallow_diagnosis_parks_the_ticket_on_the_caller():
    pack, item, ctx = journey_for(rates={"misroute": 0.0, "misclassify": 0.0,
                                         "shallow_diagnosis": 1.0})
    waits, result = drive(pack, item, ctx)
    assert any(w.reason == "awaiting_the_caller" and w.cause == "resolver" for w in waits)
    tools = [t.tool_name for t in result.traces if t.tool_name]
    assert "servicenow.hold_incident" in tools
    assert "servicenow.resume_incident" in tools


def test_the_same_mistake_costs_more_on_a_tighter_promise():
    """A misroute should blow a P2's one-minute promise and may still come in under a P4's eight
    minutes. Delays are sized against the ticket's own target for exactly this reason."""
    rates = {"misroute": 1.0, "shallow_diagnosis": 0.0}
    p2, item2, ctx2 = journey_for(rates=rates, priority="2")
    p4, item4, ctx4 = journey_for(rates=rates, priority="4")
    assert sum(w.seconds for w in drive(p2, item2, ctx2)[0]) \
        < sum(w.seconds for w in drive(p4, item4, ctx4)[0])


def test_the_pack_never_reads_the_answer_key_to_size_a_delay():
    """A misclassified ticket lands with the wrong team even when routing 'succeeded', and the pack
    knows that from the fault it INJECTED. Reading correlation_display here would let the
    simulation grade its own routing, which is the one thing this fleet exists to prevent."""
    pack, item, ctx = journey_for(rates={"misclassify": 1.0, "misroute": 0.0,
                                         "shallow_diagnosis": 0.0})
    waits, _ = drive(pack, item, ctx)
    assert any(w.reason == "queued_with_the_wrong_team" for w in waits)
    # The delay is decided from the injected fault alone. test_itsm_pack guards the source itself
    # against ever reading what the instance holds; this asserts the behaviour that guard protects.
    assert "landed_wrong" in inspect.getsource(ItsmPack._decide)


def test_run_pipeline_still_works_without_a_desk():
    """The sequential path stays usable for tests and quick runs; it just does not wait."""
    pack, item, ctx = journey_for()
    result = pack.run_pipeline(item, {}, ctx)
    assert result.terminal_reason == "resolved"


# ── the desk ────────────────────────────────────────────────────────────────
def test_the_desk_overlaps_waits_instead_of_stacking_them():
    """The reason concurrency is not optional. Run four tickets that each hold for a long time: a
    sequential runner spends the sum, a desk spends about the longest one. If this ever regressed,
    the delay would land on whichever tickets happened to be behind the slow one and position
    would decide the outcome all over again."""
    incidents = [dict(_INCIDENTS[i % len(_INCIDENTS)], sys_id=f"s{i}", number=f"INC{i:07d}")
                 for i in range(4)]
    pack = ItsmPack(client=FakeServiceNow(incidents))
    runner = _StubRunner(pack, rates={"misroute": 1.0, "shallow_diagnosis": 1.0})
    clock = FakeClock()
    outputs = Desk(runner, concurrency=4, clock=clock).run(4)

    assert len(outputs) == 4
    per_ticket = [o.result.metadata["journey"]["elapsed_s"] for o in outputs]
    assert clock.t < sum(per_ticket), "waits stacked; the desk is running sequentially"


def test_the_desk_records_where_each_ticket_lost_its_time():
    pack = ItsmPack(client=FakeServiceNow([dict(_INCIDENTS[0])]))
    runner = _StubRunner(pack, rates={"misroute": 1.0, "shallow_diagnosis": 0.0})
    out = Desk(runner, concurrency=2, clock=FakeClock()).run(1)[0]
    journey = out.result.metadata["journey"]
    assert journey["delayed_by_agent"] == ["queued_with_the_wrong_team"]
    assert journey["waits"][0]["cause"] == "router"


def test_a_clean_ticket_is_not_reported_as_delayed_by_anyone():
    pack = ItsmPack(client=FakeServiceNow([dict(_INCIDENTS[0])]))
    runner = _StubRunner(pack, rates={"misroute": 0.0, "misclassify": 0.0,
                                      "shallow_diagnosis": 0.0})
    out = Desk(runner, concurrency=2, clock=FakeClock()).run(1)[0]
    assert out.result.metadata["journey"]["delayed_by_agent"] is None


def test_the_desk_rejects_a_journey_that_yields_junk():
    """A yielded number instead of a Wait would silently lose the cause, which is the only thing
    that makes the delay worth modelling."""
    class Bad(ItsmPack):
        def journey(self, item, gt, ctx):
            yield 5
            return None

    pack = Bad(client=FakeServiceNow([dict(_INCIDENTS[0])]))
    with pytest.raises(TypeError, match="expected Wait"):
        Desk(_StubRunner(pack), concurrency=1, clock=FakeClock()).run(1)


class _StubRunner:
    """The parts of BatchRunner the desk touches, with nothing emitted anywhere."""

    def __init__(self, pack, rates=None):
        from engine.llm import LLM
        self.pack = pack
        self.rng = random.Random(1)
        self.levers = LeverConfig(rates or {})
        self.llm = LLM()
        self.emitter = None
        self.ledger = None
        self.index = 0
        pack.run_nonce = "test"


def test_an_empty_queue_does_not_freeze_the_tickets_already_open():
    """The desk must never block waiting for the next arrival. If it did, the holds on every open
    ticket would stop running down while the run stood still, and the delay would land on whichever
    tickets happened to be in flight — the positional artefact again, in a new place."""
    class DriesUp(ItsmPack):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.handed_out = 0

        def try_generate_work_item(self):
            # One ticket, then the queue is empty for a while, then one more.
            self.handed_out += 1
            if self.handed_out == 2:
                return None
            return super().try_generate_work_item()

    incidents = [dict(_INCIDENTS[i], sys_id=f"s{i}", number=f"INC{i:07d}") for i in range(2)]
    pack = DriesUp(client=FakeServiceNow(incidents))
    clock = FakeClock()
    outputs = Desk(_StubRunner(pack), concurrency=2, clock=clock, idle_poll_s=1).run(2)
    assert len(outputs) == 2


def test_a_desk_that_never_gets_work_fails_rather_than_spinning():
    class NeverAny(ItsmPack):
        def try_generate_work_item(self):
            return None

    pack = NeverAny(client=FakeServiceNow([]))
    with pytest.raises(RuntimeError, match="no incidents are arriving"):
        Desk(_StubRunner(pack), concurrency=2, clock=FakeClock(),
             idle_timeout_s=10, idle_poll_s=1).run(3)
