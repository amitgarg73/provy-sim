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
from engine.scoreboard import ProvyQuery, build_report, format_report
from packs import get_pack


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True, choices=["support", "claims", "crm"])
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
        res = reconcile_pending(ledger, emitter, workflow=args.pack)
        print(f"reconcile: {res}")
        print(f"judge backfill: {backfill_server_judge(emitter.base, emitter.key)}")

    if args.scoreboard:
        records = ledger.read(workflow=args.pack)
        report = build_report(records, pack.contract(), ProvyQuery())
        print()
        print(format_report(report, args.pack))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
