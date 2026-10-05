#!/usr/bin/env python3
"""Step 7: run ONE onboarding fleet through ONE door, as a new customer would send it.

    # the three door fleets get the SAME planned run (same seed, nonce, clock), so what differs is only the wire
    python scripts/run_onboarding_fleet.py --door sdk  --fleet sdk  --scenario doors --pack iam --count 120 --days 6 --seed 71 --nonce a71 --now 2026-10-05T23:00:00+00:00 --key-file KEYS/sdk.json --out DIR
    python scripts/run_onboarding_fleet.py --door otlp --fleet otel ... (same plan arguments)
    python scripts/run_onboarding_fleet.py --door rest --fleet json ...

    # the customer who missed context: some sessions send none, some a partial manifest, some a full one
    python scripts/run_onboarding_fleet.py --door rest --fleet missed --scenario missed --pack claims --count 120 --days 6 --seed 72 ...

The plan is deterministic from (pack, seed, nonce, now). The ground truth (`DIR/<fleet>.jsonl`, `<fleet>.ledger.jsonl`) is
written here and is NEVER sent. A run can be stopped and resumed: `DIR/<fleet>.sent.jsonl` lists the sessions already sent.

Pre-prod only (door_emit.assert_preprod). The ingest key is read from the JSON file named by --key-file and never printed.
Run it with the interpreter of the venv that holds the door's library (the published provy-sdk for --door sdk, the
OpenTelemetry packages for --door otlp).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.context_levers import truth_record, write_truth
from engine.groundtruth import GroundTruthLedger
from engine.levers import LeverConfig
from engine.llm import LLM
from engine.runner import BatchRunner
from packs import get_pack

DOOR_RATES = {"ctx_stale_source": 0.10, "ctx_unapproved_source": 0.10, "ctx_empty_retrieval": 0.06}
MISSED_RATES = {"ctx_stale_source": 0.10, "ctx_unapproved_source": 0.10, "ctx_empty_retrieval": 0.06}
MISSED_SHARE = {"none": 0.30, "partial": 0.30, "full": 0.40}
INSTRUCTION_SHARE = 0.3
FAIL_RATE = 0.8
BACKGROUND = 0.2
WARMUP = 25
CHANGE_DAYS = (0.5, 0.7, 0.9)


def build_plan(pack_name: str, fleet: str, count: int, days: float, seed: int, nonce: str, now: datetime, rates: dict):
    """The planned run: (outs, truth records, change dates). Same arguments, same plan, every time."""
    start = now - timedelta(days=days)
    every = timedelta(seconds=days * 86400 / count)
    change_dates = [(start + timedelta(days=days * d)).date().isoformat() for d in CHANGE_DAYS]
    base = {"context_manifest": 1.0, "background_failure": BACKGROUND}
    warm = LeverConfig(dict(base))
    steady = dict(base)
    for name, share in rates.items():
        steady[name] = {"rate": share, "params": {"fail_rate": FAIL_RATE}}
    steady["ctx_instruction_change"] = {"rate": INSTRUCTION_SHARE, "params": {"fail_rate": FAIL_RATE, "change_dates": change_dates}}
    steady = LeverConfig(steady)
    pack = get_pack(pack_name)
    runner = BatchRunner(pack, warm, emitter=None, ledger=None, llm=LLM(offline=True), seed=seed, run_id=nonce, starts_at=start, every=every)
    outs = []
    for i in range(count):
        runner.levers = warm if i < WARMUP else steady
        outs.append(runner.run_one())
    return outs, change_dates, pack


# ── the customer who missed context ─────────────────────────────────────────────────────────────
PARTIAL_ITEM_KEYS = ("kind", "source", "id", "used")


def partial_manifest(m: dict) -> dict:
    """What a team that only half-instrumented sends: the item names and sources, no dates, no hashes, no
    retrieval count, no instruction. `items` stays a list when the step retrieved nothing."""
    out = {}
    if "items" in m:
        out["items"] = [{k: it[k] for k in PARTIAL_ITEM_KEYS if k in it} for it in m["items"]]
    return out


def assign_modes(outs, seed: int, share: dict) -> list[str]:
    rng = random.Random(seed * 7919 + 1)
    names, weights = list(share), [share[n] for n in share]
    return [rng.choices(names, weights)[0] for _ in outs]


def apply_mode(result, mode: str) -> None:
    for t in result.traces:
        if t.context is None:
            continue
        if mode == "none":
            t.context = None
        elif mode == "partial":
            t.context = partial_manifest(t.context)


def observable(kind: str, mode: str) -> bool:
    """Could ANY check have seen this fault given what was sent? stale needs the date, empty needs the retrieval count,
    an instruction change needs the instruction fingerprint, an unapproved source needs only the source name."""
    if mode == "none":
        return False
    if kind == "unapproved":
        return True
    return mode == "full"


def ts_of(o) -> str:
    return o.record["ts"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--door", required=True, choices=["rest", "sdk", "otlp"])
    ap.add_argument("--fleet", required=True)
    ap.add_argument("--scenario", required=True, choices=["doors", "missed"])
    ap.add_argument("--pack", required=True, choices=["iam", "claims"])
    ap.add_argument("--count", type=int, default=120)
    ap.add_argument("--days", type=float, default=6)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--nonce", required=True)
    ap.add_argument("--now", required=True, help="the planned run's clock, ISO 8601 with offset; pin it so a restart makes the same plan")
    ap.add_argument("--key-file", required=True, help="JSON file holding ingest_key (read, never printed)")
    ap.add_argument("--base", default=os.environ.get("PROVY_URL", "http://localhost:3100"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="send only the first N sessions (smoke test)")
    ap.add_argument("--no-outcomes", action="store_true")
    ap.add_argument("--plan-only", action="store_true", help="write the ground truth and send nothing")
    a = ap.parse_args()

    now = datetime.fromisoformat(a.now)
    rates = DOOR_RATES if a.scenario == "doors" else MISSED_RATES
    outs, change_dates, pack = build_plan(a.pack, a.fleet, a.count, a.days, a.seed, a.nonce, now, rates)

    modes = None
    if a.scenario == "missed":
        modes = assign_modes(outs, a.seed, MISSED_SHARE)
        for o, m in zip(outs, modes):
            apply_mode(o.result, m)

    os.makedirs(a.out, exist_ok=True)
    truth = []
    for i, o in enumerate(outs):
        r = truth_record(a.fleet, "fault", a.pack, o.result, ts_of(o))
        r["door"] = a.door
        if modes:
            r["sent_mode"] = modes[i]
            for f in r["faults"]:
                f["observable"] = observable(f["fault"], modes[i])
        truth.append(r)
    truth_path = os.path.join(a.out, f"{a.fleet}.jsonl")
    digest = write_truth(truth_path, truth)
    ledger_path = os.path.join(a.out, f"{a.fleet}.ledger.jsonl")
    if os.path.exists(ledger_path):
        os.remove(ledger_path)
    ledger = GroundTruthLedger(ledger_path)
    for o in outs:
        ledger.append(o.record)
    print(f"fleet={a.fleet} door={a.door} scenario={a.scenario} pack={a.pack} sessions={len(outs)} change dates {change_dates} truth sha256 {digest}", flush=True)
    kinds: dict = {}
    for r in truth:
        for f in r["faults"]:
            kinds[f["fault"]] = kinds.get(f["fault"], 0) + 1
    print("faults planted:", kinds, "| failed items:", sum(r["outcome"] == "fail" for r in truth), flush=True)
    if modes:
        print("modes:", {m: modes.count(m) for m in set(modes)}, flush=True)
    if a.plan_only:
        return 0

    # the door, with its library imported only now (the SDK reads PROVY_URL at import)
    os.environ["PROVY_URL"] = a.base
    os.environ["PROVY_EMIT"] = "1"
    key = json.load(open(a.key_file))["ingest_key"]
    from engine import door_emit as D
    D.assert_preprod(a.base, key)
    replies = D.Replies(os.path.join(a.out, f"{a.fleet}.replies.jsonl"))
    door = D.DOORS[a.door](a.base, key, replies)
    agents = pack.agents()

    sent_path = os.path.join(a.out, f"{a.fleet}.sent.jsonl")
    done = set()
    if os.path.exists(sent_path):
        done = {json.loads(l)["session_id"] for l in open(sent_path) if l.strip()}
    todo = [(i, o) for i, o in enumerate(outs) if o.result.session_id not in done]
    if a.limit:
        todo = todo[:a.limit]
    print(f"to send: {len(todo)} (already sent {len(done)})", flush=True)

    t0 = time.time()
    n = [0]

    def one(item):
        i, o = item
        try:
            door.send_session(o.result, ts_of(o), agents, resend_first_step=(a.door == "rest" and i % 25 == 3))
            with open(sent_path, "a") as f:
                f.write(json.dumps({"session_id": o.result.session_id, "i": i}) + "\n")
        except Exception as e:                                      # noqa: BLE001
            print(f"session {o.result.session_id} failed: {type(e).__name__}: {str(e)[:160]}", flush=True)
            return
        n[0] += 1
        if n[0] % 10 == 0:
            print(f"{n[0]}/{len(todo)} sent, {time.time() - t0:.0f}s", flush=True)

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        list(ex.map(one, todo))
    print(f"sessions sent: {n[0]} in {time.time() - t0:.0f}s", flush=True)

    if not a.no_outcomes:
        recs = [o.record for _, o in todo]
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            res = list(ex.map(door.send_outcome, recs))
        ok = sum(1 for r in res if r.get("status") == 200)
        print(f"outcomes posted: {ok}/{len(res)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
