"""Batch runner: generate -> run pipeline (agents on Groq, levers applied) ->
emit traces + L4 evals -> close session -> record ground truth.

Deterministic: one seeded RNG per batch, so a (seed, count) pair reproduces the
exact same runs and injected faults. A dry run (PROVY_EMIT unset) still builds
every payload and records ground truth; nothing is sent.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone
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
                 start_index: int = 0):
        self.pack = pack
        self.levers = lever_config
        self.emitter = emitter
        self.ledger = ledger
        self.llm = llm or LLM()
        self.rng = random.Random(seed)
        self.index = start_index

    def run_one(self) -> "RunOutput":
        item, gt = self.pack.generate_work_item(self.rng)
        ctx = RunContext(
            llm=self.llm,
            rng=self.rng,
            levers=self.levers,
            session_index=self.index,
            workflow=self.pack.workflow,
            now=datetime.now(timezone.utc),
            offline=self.llm.offline,
        )
        result = self.pack.run_pipeline(item, gt, ctx)
        if self.emitter is not None:
            self.emitter.emit_run(result)   # traces + evals + close (NOT the outcome)
        record = build_record(self.pack.workflow, result, self.index)
        if self.ledger is not None:
            self.ledger.append(record)
        self.index += 1
        return RunOutput(item=item, ground_truth=gt, result=result, record=record)

    def run_batch(self, n: int) -> list["RunOutput"]:
        return [self.run_one() for _ in range(n)]


class RunOutput:
    def __init__(self, item, ground_truth, result, record):
        self.item = item
        self.ground_truth = ground_truth
        self.result = result
        self.record = record
