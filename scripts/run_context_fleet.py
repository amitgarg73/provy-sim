#!/usr/bin/env python3
"""Run one simulated fleet that sends context manifests, with or without context faults.

    # a fault fleet: IAM, 180 sessions over 14 days, faults start after a 40-session warmup
    PROVY_EMIT=1 PROVY_URL=https://dev.provy.ai PROVY_KEY=<ingest key> \
        python3 scripts/run_context_fleet.py --pack iam --fleet iam-fault --role fault \
        --count 400 --days 14 --seed 11 --truth data/context_truth_iam-fault.jsonl

    # its control: same pack, same background failure rate, manifests on, no fault at all
    ... --fleet iam-control --role control

Dry run by default (PROVY_EMIT unset): builds and records everything, sends nothing. With PROVY_EMIT=1
the base URL must be on the pre-prod allow list and the deployment must say `preprod` (the emitter
asks it before the first write). PROVY_ALLOW_PROD is ignored.

The key is read from the environment variable named by --key-env (default PROVY_KEY) and is never
printed or written. The ground-truth file is written here and is never sent anywhere.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.context_emit import assert_preprod_target
from engine.context_levers import CONTEXT_LEVERS, DEFAULT_FAIL_RATE, truth_record, write_truth
from engine.emitter import DEFAULT_BASE_URL, ProvyEmitter
from engine.groundtruth import GroundTruthLedger
from engine.levers import LeverConfig
from engine.llm import LLM
from engine.reconcile import backfill_server_judge, reconcile_pending
from engine.runner import BatchRunner
from packs import get_pack

# Fault shares per work item, after the warmup. Chosen so an empty retrieval stays under the learned
# check's own 95 percent line (SPEC 6.2) and so every kind has enough rows to count (see the report).
DEFAULT_FAULT_RATES = {"ctx_stale_source": 0.08, "ctx_unapproved_source": 0.08, "ctx_empty_retrieval": 0.03,
                       "ctx_manifest_missing": 0.06}
DEFAULT_INSTRUCTION_SHARE = 0.3
DEFAULT_WARMUP = 40


def build_configs(role: str, fail_rate: float, background_rate: float, change_dates: list,
                  rates: dict, instruction_share: float) -> tuple[LeverConfig, LeverConfig]:
    """(warmup config, steady config). Both send manifests and carry the same background failure rate.
    Only the steady config of a FAULT fleet carries a context fault."""
    base = {"context_manifest": 1.0, "background_failure": background_rate}
    warm = LeverConfig(dict(base))
    if role == "control":
        return warm, LeverConfig(dict(base))
    steady = dict(base)
    for name, share in rates.items():
        steady[name] = {"rate": share, "params": {"fail_rate": fail_rate}}
    steady["ctx_instruction_change"] = {"rate": instruction_share,
                                        "params": {"fail_rate": fail_rate, "change_dates": change_dates}}
    return warm, LeverConfig(steady)


def run_fleet(pack_name: str, fleet: str, role: str, count: int, days: float, seed: int,
              fail_rate: float = DEFAULT_FAIL_RATE, background_rate: float = 0.2,
              rates: dict | None = None, instruction_share: float = DEFAULT_INSTRUCTION_SHARE,
              warmup: int = DEFAULT_WARMUP, workers: int = 1, change_days: tuple = (0.5, 0.7, 0.9), emitter: ProvyEmitter | None = None,
              ledger: GroundTruthLedger | None = None, now: datetime | None = None):
    """Run the fleet and return (outputs, truth records). Pure of any file or network unless handed one."""
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    every = timedelta(seconds=days * 86400 / count)
    change_date = [(start + timedelta(days=days * d)).date().isoformat() for d in change_days]
    warm, steady = build_configs(role, fail_rate, background_rate, change_date,
                                 rates if rates is not None else DEFAULT_FAULT_RATES, instruction_share)
    pack = get_pack(pack_name)
    runner = BatchRunner(pack, warm, emitter=emitter if workers <= 1 else None, ledger=ledger, llm=LLM(offline=True), seed=seed,
                         starts_at=start, every=every)
    outs, truth = [], []
    for i in range(count):
        runner.levers = warm if i < warmup else steady
        o = runner.run_one()
        outs.append(o)
        truth.append(truth_record(fleet, role, pack_name, o.result, o.record["ts"]))
    if emitter is not None and emitter.enabled and workers > 1:
        # The run above built every payload and sent none (the runner was given no emitter). Send the
        # sessions now, in time order, several at a time: a session close is slow on the server and one
        # at a time a 400-session fleet takes hours. Order within a few sessions does not matter to the
        # checks, which read the stored time of each step.
        from concurrent.futures import ThreadPoolExecutor
        agents = pack.agents()
        emitter._verify_environment()     # one probe, before the threads start
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(lambda o: emitter.emit_run(o.result, agents, occurred_at=o.record["ts"]), outs))
    return outs, truth, change_date


def summarise(truth: list[dict]) -> dict:
    kinds: dict[str, dict] = {}
    for r in truth:
        for f in r["faults"]:
            k = kinds.setdefault(f["fault"], {"items": 0, "failed_from_it": 0})
            k["items"] += 1
            k["failed_from_it"] += bool(r["failure_cause"] and r["failure_cause"] == "context:" + f["fault"])
    clean = [r for r in truth if not r["faults"]]
    return {"work_items": len(truth), "faults": kinds, "no_fault_items": len(clean),
            "no_fault_failed": sum(r["outcome"] == "fail" for r in clean),
            "failed": sum(r["outcome"] == "fail" for r in truth)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True, choices=["iam", "claims", "support", "crm"])
    ap.add_argument("--fleet", required=True, help="a label for the truth file, e.g. iam-fault")
    ap.add_argument("--role", choices=["fault", "control"], required=True)
    ap.add_argument("--count", type=int, default=400)
    ap.add_argument("--days", type=float, default=14)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--fail-rate", type=float, default=DEFAULT_FAIL_RATE, help="share of faulted items that fail (0.8)")
    ap.add_argument("--background-rate", type=float, default=0.2, help="share of unfaulted items that fail for another cause (0.2)")
    ap.add_argument("--warmup", type=int, default=DEFAULT_WARMUP)
    ap.add_argument("--instruction-share", type=float, default=DEFAULT_INSTRUCTION_SHARE)
    ap.add_argument("--rates", default=None, help='JSON, e.g. {"ctx_stale_source": 0.06}')
    ap.add_argument("--truth", required=True, help="ground-truth JSONL path (never sent)")
    ap.add_argument("--key-env", default="PROVY_KEY", help="NAME of the env var holding the fleet's ingest key")
    ap.add_argument("--workers", type=int, default=1, help="sessions sent at once (the run itself is built first)")
    ap.add_argument("--no-reconcile", action="store_true")
    args = ap.parse_args()

    base = (os.environ.get("PROVY_URL") or DEFAULT_BASE_URL).rstrip("/")
    assert_preprod_target(base)
    emitter = ProvyEmitter(ingest_key=os.environ.get(args.key_env, ""), base_url=base, is_simulated=False)
    ledger = GroundTruthLedger(args.truth.replace(".jsonl", "") + ".ledger.jsonl")
    rates = json.loads(args.rates) if args.rates else None
    print(f"fleet={args.fleet} pack={args.pack} role={args.role} count={args.count} days={args.days} "
          f"emit={'ON' if emitter.enabled else 'OFF (dry run)'}")
    outs, truth, change_date = run_fleet(args.pack, args.fleet, args.role, args.count, args.days, args.seed,
                                         args.fail_rate, args.background_rate, rates, args.instruction_share,
                                         args.warmup, workers=args.workers, emitter=emitter, ledger=ledger)
    digest = write_truth(args.truth, truth)
    print(f"instruction change dates {change_date}; truth sha256 {digest}")
    print(json.dumps(summarise(truth), indent=1))
    if emitter.enabled and not args.no_reconcile:
        sids = [o.result.session_id for o in outs]
        print("judge:", backfill_server_judge(emitter.base, emitter.key, session_ids=sids))
        print("reconcile:", reconcile_pending(ledger, emitter, workflow=args.pack))
    return 0


if __name__ == "__main__":
    sys.exit(main())
