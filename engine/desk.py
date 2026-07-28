"""A desk: several tickets in flight at once, each on its own clock.

The sequential runner works one item start to finish before touching the next. That is fine for a
pack that invents its own work and settles it instantly, and wrong for one where real time passes
inside a run. A ticket held five minutes waiting on its caller would stall every ticket behind it,
so the delay would land on the wrong tickets, and how long anything waited would once again come
down to its position in the loop. That is the artefact this exists to remove, not to relocate.

So the desk holds N journeys open, advances whichever is due next, and sleeps only when nothing is
ready. Waits overlap, exactly as they do on a desk where one person is not the whole team. Wall
clock for a run becomes the longest single journey rather than the sum of all of them.

The clock is injected. Tests drive a fake one and assert the ORDER things happened in without
waiting for any of it.
"""
from __future__ import annotations

import heapq
import itertools
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Optional

from .groundtruth import build_record
from .types import RunContext, Wait


@dataclass(order=True)
class _InFlight:
    """One journey waiting for its next action. Ordered by when that action is due."""
    due: float
    seq: int
    journey: Any = field(compare=False)
    item: Any = field(compare=False)
    gt: Any = field(compare=False)
    started: float = field(compare=False, default=0.0)
    waits: list = field(compare=False, default_factory=list)


class Clock:
    """Real time. Swapped for a fake one in tests."""

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class Desk:
    def __init__(self, runner, concurrency: int = 4, clock: Optional[Clock] = None,
                 on_complete: Optional[Callable[[Any], None]] = None,
                 log: Optional[Callable[[str], None]] = None,
                 idle_timeout_s: float = 180.0, idle_poll_s: float = 5.0):
        self.runner = runner
        self.pack = runner.pack
        # How many tickets one desk works at once. Too low and journeys queue behind each other
        # again; too high and a short run finishes before its own waits have meant anything.
        self.concurrency = max(1, concurrency)
        self.clock = clock or Clock()
        self.on_complete = on_complete
        self.log = log or (lambda _m: None)
        self.idle_timeout_s = idle_timeout_s
        self.idle_poll_s = idle_poll_s

    def _take_work(self):
        """One work item if the pack has one to hand, else None. Never waits."""
        try_get = getattr(self.pack, "try_generate_work_item", None)
        if callable(try_get):
            return try_get()
        return self.pack.generate_work_item(self.runner.rng)

    def run(self, count: int) -> list:
        """Work `count` tickets, returning their RunOutputs in completion order."""
        from .runner import RunOutput  # local: runner imports nothing from here

        pending = count
        in_flight: list[_InFlight] = []
        seq = itertools.count()
        done = []

        idle_since = None
        while pending > 0 or in_flight:
            # Fill the desk, but never wait here. Blocking for the next arrival would freeze every
            # ticket already open: their holds would stop running down while the run stood still,
            # so the delay would land on whatever happened to be in flight at the time. That is the
            # positional artefact all over again, in a new place.
            while pending > 0 and len(in_flight) < self.concurrency:
                got = self._take_work()
                if got is None:
                    break
                item, gt = got
                journey = self.pack.journey(item, gt, self._context())
                entry = _InFlight(due=self.clock.now(), seq=next(seq), journey=journey,
                                  item=item, gt=gt, started=self.clock.now())
                heapq.heappush(in_flight, entry)
                pending -= 1

            if not in_flight:
                # Nothing open and nothing to pick up: the desk is genuinely idle, waiting for the
                # next ticket to be raised. Bounded, so an instance with nothing in it fails rather
                # than spinning until the CI job times out.
                if pending <= 0:
                    break
                idle_since = idle_since if idle_since is not None else self.clock.now()
                if self.clock.now() - idle_since >= self.idle_timeout_s:
                    raise RuntimeError(
                        f"desk idle {self.idle_timeout_s:.0f}s with {pending} still to work: "
                        f"no incidents are arriving")
                self.clock.sleep(self.idle_poll_s)
                continue
            idle_since = None

            entry = heapq.heappop(in_flight)
            # Nothing to do until this one is due. Sleeping here rather than inside the journey is
            # what lets the other tickets' waits run down at the same time.
            self.clock.sleep(max(0.0, entry.due - self.clock.now()))

            try:
                wait = next(entry.journey)
            except StopIteration as finished:
                out = self._finish(finished.value, entry, RunOutput)
                done.append(out)
                if self.on_complete:
                    self.on_complete(out)
                continue

            if not isinstance(wait, Wait):
                raise TypeError(f"journey yielded {type(wait).__name__}, expected Wait")
            entry.waits.append(wait)
            entry.due = self.clock.now() + wait.seconds
            self.log(f"  {entry.item.get('id', '?')}: {wait.reason} "
                     f"({wait.seconds:.0f}s{'' if not wait.cause else ', ' + wait.cause})")
            heapq.heappush(in_flight, entry)

        return done

    def _context(self) -> RunContext:
        r = self.runner
        return RunContext(
            llm=r.llm, rng=r.rng, levers=r.levers, session_index=r.index,
            workflow=self.pack.workflow, now=datetime.now(timezone.utc), offline=r.llm.offline,
        )

    def _finish(self, result, entry: _InFlight, RunOutput):
        """Emit the finished journey exactly as the sequential runner would.

        The waits go on the result as metadata, so the run carries its own account of where its
        time went. Provy never has to infer it, and neither does anyone reading the trace.
        """
        r = self.runner
        held = [w for w in entry.waits if w.cause]
        result.metadata = dict(result.metadata or {})
        result.metadata["journey"] = {
            "elapsed_s": round(self.clock.now() - entry.started, 1),
            "waits": [{"reason": w.reason, "seconds": round(w.seconds, 1), "cause": w.cause}
                      for w in entry.waits],
            # Named separately because this is the whole question: was the time spent because
            # somebody got something wrong, or was it just the work taking as long as work takes.
            "delayed_by_agent": [w.reason for w in held] or None,
        }
        if r.emitter is not None:
            r.emitter.emit_run(result)
        record = build_record(self.pack.workflow, result, r.index)
        if r.ledger is not None:
            r.ledger.append(record)
        r.index += 1
        return RunOutput(item=entry.item, ground_truth=entry.gt, result=result, record=record)


def supports_journey(pack) -> bool:
    return callable(getattr(pack, "journey", None))
