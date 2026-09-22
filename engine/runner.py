"""Batch runner: generate -> run pipeline (agents on Groq, levers applied) ->
emit traces + L4 evals -> close session -> record ground truth.

Deterministic: one seeded RNG per batch, so a (seed, count) pair reproduces the
exact same runs and injected faults. A dry run (PROVY_EMIT unset) still builds
every payload and records ground truth; nothing is sent.
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from .emitter import ProvyEmitter
from .groundtruth import GroundTruthLedger, build_record
from .levers import LeverConfig
from .llm import LLM
from .pack import BasePack
from .types import RunContext


class BatchRunner:
    def __init__(self, pack: BasePack, lever_config: LeverConfig,
                 emitter: Optional[ProvyEmitter] = None,
                 ledger: Optional[GroundTruthLedger] = None,
                 llm: Optional[LLM] = None, seed: int = 0,
                 start_index: int = 0, run_id: Optional[str] = None,
                 starts_at: Optional[datetime] = None, every: Optional[timedelta] = None):
        self.pack = pack
        self.levers = lever_config
        self.emitter = emitter
        self.ledger = ledger
        self.llm = llm or LLM()
        self.rng = random.Random(seed)
        self.index = start_index
        # Per-batch nonce so every run emits fresh session ids (fresh Provy sessions with real
        # timestamps), even when the seed repeats the work-item ids. NON-deterministic on purpose —
        # not drawn from the seeded rng — so re-runs don't collide. Pass run_id to pin it (tests).
        pack.run_nonce = run_id or uuid.uuid4().hex[:8]
        # ⛔ A SIMULATED CLOCK, BECAUSE A PACK REPRESENTING WEEKS LANDED AS ONE AFTERNOON (argus#1072).
        #
        # Every run used to take `datetime.now()`, so a 143-run pack seeded in one sitting produced
        # 143 sessions inside five minutes. Measured on pre-prod: Post-call 157 sessions across 1 day,
        # Claims 108 across 2, Refund 106 across 3. Only Strategy C has real spread and only because
        # it genuinely ran on a cron for six weeks. Every activity chart, drift window and "running
        # less than usual" read off a simulated fleet was measuring the seeding run.
        #
        # `starts_at` with `every` walks the clock forward one run at a time. Both omitted keeps the
        # old behaviour exactly, so a live run is unchanged.
        self._starts_at = starts_at
        self._every = every

    def _clock(self) -> datetime:
        """When this run happened. The wall clock unless a simulated one was given."""
        if self._starts_at is None:
            return datetime.now(timezone.utc)
        at = self._starts_at + (self._every or timedelta(0)) * self.index
        # ⛔ NEVER AHEAD OF NOW. The server refuses a future timestamp and falls back to arrival, so a
        # pack whose span overshoots would silently land half its sessions on the seeding day and look
        # like it worked. Clamping here keeps the whole batch on one clock.
        now = datetime.now(timezone.utc)
        return at if at <= now else now

    def run_one(self) -> "RunOutput":
        item, gt = self.pack.generate_work_item(self.rng)
        ran_at = self._clock()
        ctx = RunContext(
            llm=self.llm,
            rng=self.rng,
            levers=self.levers,
            session_index=self.index,
            workflow=self.pack.workflow,
            now=ran_at,
            offline=self.llm.offline,
        )
        result = self.pack.run_pipeline(item, gt, ctx)
        if self.emitter is not None:
            # The same instant reaches the session, every trace, the ledger record and the outcome,
            # so nothing in a seeded fleet disagrees about when the work happened.
            self.emitter.emit_run(result, self.pack.agents(), occurred_at=ran_at.isoformat())
        record = build_record(self.pack.workflow, result, self.index,
                              occurred_at=ran_at.isoformat())
        if self.ledger is not None:
            self.ledger.append(record)
        self.index += 1
        return RunOutput(item=item, ground_truth=gt, result=result, record=record)

    def run_batch(self, n: int) -> list["RunOutput"]:
        return [self.run_one() for _ in range(n)]


def chunk_sizes(count: int, every: int) -> list[int]:
    """Split a batch into reconcile chunks.

    Outcomes used to be posted only after the WHOLE batch finished, so Provy saw a wall of runs with
    nothing reconciled and then every outcome at once. For a demo that means the trust score, the
    ledger and the divergence surfaces say nothing until the run is over. Chunking lets outcomes
    stream in alongside the runs that produced them.

    every <= 0 keeps the old behaviour (one chunk, reconcile at the end), so nothing changes unless
    asked. A remainder becomes its own final chunk rather than being dropped or merged, so the last
    few runs still reconcile.
    """
    if count <= 0:
        return []
    if every <= 0 or every >= count:
        return [count]
    full, rest = divmod(count, every)
    return [every] * full + ([rest] if rest else [])


class RunOutput:
    def __init__(self, item, ground_truth, result, record):
        self.item = item
        self.ground_truth = ground_truth
        self.result = result
        self.record = record
