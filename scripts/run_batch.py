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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.workflows import get_workflow
from engine.emitter import ProvyEmitter
from engine.groundtruth import GroundTruthLedger
from engine.llm import LLM
from engine.reconcile import backfill_server_judge, reconcile_pending
from engine.runner import BatchRunner
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
    outputs = runner.run_batch(args.count)

    for o in outputs[:args.show]:
        r = o.result
        faults = ", ".join(f["lever"] for f in o.record["faults"]) or "none"
        print(f"  {r.entity_id}: outcome={r.outcome_label} diverged={r.diverged()} "
              f"terminal={r.terminal_reason} faults=[{faults}]")

    if args.reconcile:
        # Order matters: the server judge writes the trace-based predictions, and an outcome can only
        # reconcile against a prediction that already exists. Judge FIRST (naming THIS batch's sessions
        # so every one gets a prediction, not just the most-recent 20), then post the outcomes.
        sids = [o.result.session_id for o in outputs]
        print(f"judge backfill: {backfill_server_judge(emitter.base, emitter.key, session_ids=sids)}")
        res = reconcile_pending(ledger, emitter, workflow=args.pack)
        print(f"reconcile: {res}")

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
