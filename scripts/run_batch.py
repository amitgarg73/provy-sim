#!/usr/bin/env python3
"""Run a batch for one workflow, then optionally reconcile and print the scoreboard.

Dry run (default, PROVY_EMIT unset): builds every payload and records ground
truth, sends nothing. Add PROVY_EMIT=1 + the ingest key to emit for real.

Examples:
    python scripts/run_batch.py --pack support --count 8 --seed 1
    python scripts/run_batch.py --pack support --count 8 --reconcile --scoreboard
    PROVY_EMIT=1 PROVY_KEY_SUPPORT=provy_xxx python scripts/run_batch.py --pack support
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.workflows import get_workflow
from engine.emitter import ProvyEmitter
from engine.groundtruth import GroundTruthLedger
from engine.llm import LLM
from engine.reconcile import backfill_server_judge, reconcile_pending
from engine.runner import BatchRunner, chunk_sizes
from engine.scoreboard import ProvyQuery, aggregate_injected, build_report, format_report
from engine.control_client import post_injected
from packs import PACKS, get_pack


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True, choices=sorted(PACKS))
    ap.add_argument("--count", type=int, default=8)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--start-index", type=int, default=0)
    ap.add_argument("--ledger", default=None, help="ground-truth JSONL path")
    ap.add_argument("--reconcile", action="store_true", help="post the day's outcomes")
    ap.add_argument("--reconcile-every", type=int, default=0, metavar="N",
                    help="post outcomes every N runs instead of only at the end, so outcomes stream "
                         "in alongside the runs (0 = at the end, the old behaviour)")
    ap.add_argument("--settle-lag", type=float, default=0.0, metavar="SECONDS",
                    help="wait this long before posting a chunk's outcomes, modelling the gap between "
                         "a decision and its real-world outcome settling")
    ap.add_argument("--scoreboard", action="store_true", help="print the scoreboard")
    ap.add_argument("--show", type=int, default=2, help="print N run summaries")
    args = ap.parse_args()

    wf = get_workflow(args.pack)
    pack = get_pack(args.pack)
    ledger_path = args.ledger or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", f"groundtruth_{args.pack}.jsonl")
    ledger = GroundTruthLedger(ledger_path)
    emitter = ProvyEmitter(ingest_key=wf.ingest_key, is_simulated=False)
    llm = LLM()

    print(f"pack={args.pack} count={args.count} seed={args.seed} "
          f"emit={'ON' if emitter.enabled else 'OFF (dry run)'} "
          f"llm={'groq' if not llm.offline else 'offline-stub'}")

    runner = BatchRunner(pack, wf.lever_config(), emitter=emitter, ledger=ledger,
                         llm=llm, seed=args.seed, start_index=args.start_index)

    def flush(chunk, final: bool):
        """Judge, then post this chunk's outcomes.

        Order matters: the server judge writes the trace-based predictions, and an outcome can only
        reconcile against a prediction that already exists. Judge FIRST, naming exactly this chunk's
        sessions so every one gets a prediction rather than just the most-recent 20.

        Retry budget is deliberately asymmetric. reconcile_pending waits `backoff` seconds between
        retries for outcomes whose prediction is not visible yet, which is right ONCE but disastrous
        per chunk: at the default 5 retries x 20s, a five-chunk batch could spend ten minutes asleep
        (observed on a 20-run batch that finished emitting long before the job ended). An intermediate
        chunk does not need to wait, because reconcile_pending drains ALL pending outcomes for the
        workflow — anything it leaves unmatched is retried by the next chunk, and the final sweep
        keeps the full budget as the backstop.
        """
        if args.settle_lag > 0:
            time.sleep(args.settle_lag)
        sids = [o.result.session_id for o in chunk]
        print(f"  judge backfill: {backfill_server_judge(emitter.base, emitter.key, session_ids=sids)}")
        kw = {} if final else {"retries": 0}
        print(f"  reconcile: {reconcile_pending(ledger, emitter, workflow=args.pack, **kw)}")

    # Chunked so outcomes stream in with the runs. Without --reconcile-every this is a single chunk
    # and the behaviour is unchanged.
    sizes = chunk_sizes(args.count, args.reconcile_every if args.reconcile else 0)
    outputs = []
    for i, size in enumerate(sizes):
        chunk = runner.run_batch(size)
        outputs.extend(chunk)
        if args.reconcile and len(sizes) > 1:
            print(f"chunk {i + 1}/{len(sizes)} ({size} runs):")
            flush(chunk, final=False)

    for o in outputs[:args.show]:
        r = o.result
        faults = ", ".join(f["lever"] for f in o.record["faults"]) or "none"
        print(f"  {r.entity_id}: outcome={r.outcome_label} diverged={r.diverged()} "
              f"terminal={r.terminal_reason} faults=[{faults}]")

    # Single-chunk runs reconcile once at the end; chunked runs already flushed as they went, but a
    # final sweep catches anything whose prediction was not yet visible when its chunk posted.
    if args.reconcile:
        if len(sizes) > 1:
            print("final sweep:")
        flush(outputs, final=True)

    # Post the injected-truth summary to the console (best-effort, offline-safe) so its scoreboard
    # has the injected side: lever rates, per-entity attribution truth, and value at risk.
    records = ledger.read(workflow=args.pack)
    injected = aggregate_injected(records, pack.contract(), pack.failure_cost())
    print(f"post injected → console: {post_injected(args.pack, injected)}")

    if args.scoreboard:
        report = build_report(records, pack.contract(), ProvyQuery(), pack.failure_cost())
        print()
        print(format_report(report, args.pack))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
