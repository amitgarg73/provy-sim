#!/usr/bin/env python3
"""Instrument one fleet's execution paths: emit N sessions, timing every HTTP call bucketed by
endpoint, then time the server-side judge and the reconcile fan-out separately, and report
p50/p95/p99/max per stage. Warms the functions first so cold starts don't pollute the numbers.

Runs a normal reconciled batch (same paths as run_batch), so the SAME execution also writes the
ground-truth ledger with the varied injected culprits — one run measures latency AND attribution.

    PROVY_EMIT=1 PROVY_KEY_TRAVEL=provy_xxx PROVY_URL=https://provyai.vercel.app \
        python scripts/instrument.py --pack travel --count 40 --warmup 3
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.workflows import get_workflow
from engine.emitter import ProvyEmitter
from engine.groundtruth import GroundTruthLedger
from engine.llm import LLM
from engine.reconcile import backfill_server_judge, reconcile_pending
from engine.runner import BatchRunner
from packs import PACKS, get_pack


def pctl(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * len(s) + 0.5)) - 1))
    return s[k]


def stat_line(name: str, xs: list[float]) -> str:
    if not xs:
        return f"  {name:<28} n=0"
    return (f"  {name:<28} n={len(xs):<4} "
            f"p50={pctl(xs,50):7.0f}  p95={pctl(xs,95):7.0f}  p99={pctl(xs,99):7.0f}  "
            f"max={max(xs):7.0f}  (ms)")


def bucket(timings: list[tuple[str, float]]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for path, ms in timings:
        out.setdefault(path, []).append(ms)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True, choices=sorted(PACKS))
    ap.add_argument("--count", type=int, default=40)
    ap.add_argument("--warmup", type=int, default=3)
    ap.add_argument("--out", default=None, help="write the timing report as JSON here")
    args = ap.parse_args()

    wf = get_workflow(args.pack)
    pack = get_pack(args.pack)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ledger_path = os.path.join(root, "data", f"instrument_{args.pack}.jsonl")
    if os.path.exists(ledger_path):
        os.remove(ledger_path)                      # fresh ledger so attribution truth is clean
    ledger = GroundTruthLedger(ledger_path)
    emitter = ProvyEmitter(ingest_key=wf.ingest_key, is_simulated=False)
    if not emitter.enabled:
        print("emit is OFF — set PROVY_EMIT=1 and the ingest key. Aborting.", file=sys.stderr)
        return 2

    # Wrap _post to time every ingest call by endpoint.
    timings: list[tuple[str, float]] = []
    orig_post = emitter._post

    def timed(path: str, payload: dict) -> dict:
        t0 = time.perf_counter()
        try:
            return orig_post(path, payload)
        finally:
            timings.append((path, (time.perf_counter() - t0) * 1000.0))

    emitter._post = timed  # type: ignore[method-assign]
    llm = LLM()

    # ── warmup (discarded) ────────────────────────────────────────────────────
    warm = BatchRunner(pack, wf.lever_config(), emitter=emitter, ledger=None, llm=llm, seed=999)
    for _ in range(args.warmup):
        warm.run_one()
    timings.clear()

    # ── measured ingest write path ────────────────────────────────────────────
    runner = BatchRunner(pack, wf.lever_config(), emitter=emitter, ledger=ledger, llm=llm, seed=7)
    t0 = time.perf_counter()
    runner.run_batch(args.count)
    ingest_wall = time.perf_counter() - t0
    ingest = bucket(timings)
    timings.clear()

    # ── judge (one server-side batch grade call) ──────────────────────────────
    t0 = time.perf_counter()
    jb = backfill_server_judge(emitter.base, emitter.key)
    judge_wall = time.perf_counter() - t0

    # ── reconcile fan-out (outcome posts do re-grade + attribute + patterns) ───
    timings.clear()
    t0 = time.perf_counter()
    rc = reconcile_pending(ledger, emitter, workflow=args.pack)
    reconcile_wall = time.perf_counter() - t0
    reconcile = bucket(timings)

    # ── report ────────────────────────────────────────────────────────────────
    order = ["/api/ingest/session/open", "/api/ingest/trace", "/api/ingest/eval",
             "/api/ingest/session/close"]
    print(f"\n=== instrumented run — {args.pack}  N={args.count}  (warmup={args.warmup}, base={emitter.base}) ===")
    print("ingest write path (client -> Vercel fn -> PostgREST -> Postgres), per call:")
    for p in order:
        if p in ingest:
            print(stat_line(p.split("/api/ingest/")[-1], ingest[p]))
    for p in ingest:
        if p not in order:
            print(stat_line(p.split("/api/")[-1], ingest[p]))
    total_ingest_calls = sum(len(v) for v in ingest.values())
    print(f"  ingest wall={ingest_wall:6.1f}s   calls={total_ingest_calls}   "
          f"per-session={ingest_wall/max(1,args.count)*1000:6.0f} ms over {args.count} sessions")

    print("\njudge (server-side batch grading, /api/compute/judge):")
    print(f"  one call = {judge_wall*1000:7.0f} ms   result={jb}")

    print("\nreconcile fan-out (/api/ingest/outcome = re-grade + attribute + patterns), per call:")
    for p, xs in reconcile.items():
        print(stat_line(p.split("/api/ingest/")[-1], xs))
    print(f"  reconcile wall={reconcile_wall:6.1f}s   result={rc}")

    report = {
        "pack": args.pack, "count": args.count, "warmup": args.warmup, "base": emitter.base,
        "ingest": {p: {"n": len(v), "p50": pctl(v, 50), "p95": pctl(v, 95), "p99": pctl(v, 99), "max": max(v)}
                   for p, v in ingest.items()},
        "ingest_wall_s": round(ingest_wall, 2),
        "judge_wall_ms": round(judge_wall * 1000, 1), "judge_result": jb,
        "reconcile": {p: {"n": len(v), "p50": pctl(v, 50), "p95": pctl(v, 95), "p99": pctl(v, 99), "max": max(v)}
                      for p, v in reconcile.items()},
        "reconcile_wall_s": round(reconcile_wall, 2), "reconcile_result": rc,
        "ledger_path": ledger_path,
    }
    if args.out:
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\ntiming JSON -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
