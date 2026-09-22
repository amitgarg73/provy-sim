"""
A pack that represents weeks of business must land as weeks (argus#1072).

⛔ EVERY SEEDED FLEET WE HAVE IS BUNCHED INTO THE SITTING THAT CREATED IT. Measured on pre-prod,
days of session spread against days of outcome spread:

    Post-call follow-up   157 sessions    1 day
    Claims Adjudication   108 sessions    2 days
    Refund Operations     106 sessions    3 days
    Strategy C Trading    193 sessions   42 days

Only Strategy C has real spread, and only because it genuinely ran on a cron for six weeks. So every
activity chart, drift window and "running less than usual" ever read off a simulated fleet was
measuring the seeding run rather than the simulated business.

The outcome side has been dated correctly since #812, whose comment says it plainly: "fall back to
when the work ran, never to now". The session side never was.
"""
from datetime import datetime, timedelta, timezone

from engine.levers import LeverConfig
from engine.runner import BatchRunner
from packs import get_pack


def _runner(**kw):
    return BatchRunner(get_pack("travel"), LeverConfig({}), seed=1, **kw)


class TestTheClockWalksForward:
    def test_a_batch_spans_the_days_it_was_given(self):
        start = datetime(2026, 9, 8, 6, 0, tzinfo=timezone.utc)
        outs = _runner(starts_at=start, every=timedelta(hours=6)).run_batch(12)
        stamps = [datetime.fromisoformat(o.record["ts"]) for o in outs]

        assert stamps[0] == start
        # 12 runs six hours apart from 06:00 covers 66 hours, so it touches four calendar days.
        assert len({s.date() for s in stamps}) == 4
        assert stamps[-1] == start + timedelta(hours=66)
        assert stamps == sorted(stamps), "a later run must never be stamped before an earlier one"

    def test_the_ledger_record_carries_it_too(self):
        """
        ⛔ `ts` USED TO BE THE WALL CLOCK EVEN WHEN A CALLER SAID OTHERWISE. `build_record` already
        took `occurred_at` and spent it only on the outcome payload, so the field reconcile.py reads
        as its fallback was the one field that ignored the simulated clock.
        """
        start = datetime(2026, 9, 8, 6, 0, tzinfo=timezone.utc)
        out = _runner(starts_at=start, every=timedelta(hours=1)).run_batch(1)[0]
        assert out.record["ts"] == start.isoformat()

    def test_the_run_sees_the_simulated_time_as_its_own_now(self):
        """A pack that reads ctx.now must see the simulated instant, not the wall clock."""
        start = datetime(2026, 9, 8, 6, 0, tzinfo=timezone.utc)
        out = _runner(starts_at=start, every=timedelta(hours=1)).run_batch(3)[-1]
        assert datetime.fromisoformat(out.record["ts"]).date() == start.date()


class TestItStaysOutOfTheFuture:
    """
    ⛔ THE SERVER REFUSES A FUTURE TIMESTAMP AND FALLS BACK TO ARRIVAL. A pack whose span overshoots
    would then land half its sessions on the seeding day and still look like it had worked, which is
    the failure mode that is hardest to notice. Clamping here keeps one batch on one clock.
    """

    def test_a_span_that_overshoots_is_clamped_to_now(self):
        start = datetime.now(timezone.utc) - timedelta(hours=1)
        outs = _runner(starts_at=start, every=timedelta(days=30)).run_batch(3)
        stamps = [datetime.fromisoformat(o.record["ts"]) for o in outs]
        assert all(s <= datetime.now(timezone.utc) + timedelta(seconds=5) for s in stamps)

    def test_a_backdated_span_is_left_alone(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        outs = _runner(starts_at=start, every=timedelta(days=1)).run_batch(3)
        assert datetime.fromisoformat(outs[0].record["ts"]) == start


class TestALiveRunIsUnchanged:
    """Both arguments omitted must behave exactly as before, or every existing caller moves."""

    def test_no_clock_given_means_the_wall_clock(self):
        before = datetime.now(timezone.utc)
        out = _runner().run_batch(1)[0]
        after = datetime.now(timezone.utc)
        assert before <= datetime.fromisoformat(out.record["ts"]) <= after
