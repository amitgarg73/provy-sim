#!/usr/bin/env python3
"""Onboarding helper — prints the exact payloads a pack registers with Provy.

This does NOT bypass the normal customer path (waitlist -> invite -> signup).
It only builds, from the pack itself, the two artifacts you seed after signup:
  1. the agents + eval configs to POST to /api/onboarding/seed-evals
  2. the outcome contract JSON to insert as the fleet's one active contract

Run it to preview (prints JSON). Add --seed-evals with an ingest key to actually
POST the seed-evals payload. The contract is printed for you to insert during
onboarding (there is no public contract-ingest endpoint yet).

    python onboarding/onboard.py --pack support
    python onboarding/onboard.py --pack support --seed-evals --key provy_xxx
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.contract import contract_json
from packs import get_pack

BASE = os.environ.get("PROVY_URL", "https://provyai.vercel.app").rstrip("/")


def seed_evals_payload(pack) -> dict:
    """The body for POST /api/onboarding/seed-evals: agents + eval criteria."""
    agents = [
        {"agent_name": a.name, "display_name": a.display_name,
         "emoji": a.emoji or None, "sort_order": a.sort_order}
        for a in pack.agents()
    ]
    # One L4 semantic criterion per agent (matches build_clean_run eval_names).
    criteria = []
    seen = set()
    for spec in pack.agents():
        # eval names are defined in the pack's clean run; mirror them here.
        pass
    # Derive eval names deterministically from a dry clean run.
    import random
    from datetime import datetime, timezone
    from engine.levers import LeverConfig
    from engine.llm import LLM
    from engine.types import RunContext
    rng = random.Random(0)
    item, gt = pack.generate_work_item(rng)
    ctx = RunContext(llm=LLM(offline=True), rng=rng, levers=LeverConfig(),
                     session_index=0, workflow=pack.workflow, now=datetime.now(timezone.utc))
    run = pack.build_clean_run(item, gt, ctx)
    for ev in run.evals:
        key = (ev.agent, ev.eval_name)
        if key in seen:
            continue
        seen.add(key)
        criteria.append({
            "eval_name": ev.eval_name,
            "agent": ev.agent,
            "layer": 4,
            "eval_type": "semantic",
            "threshold": 0.7,
            "description": f"Judge whether the {ev.agent} output meets: {ev.eval_name.replace('_', ' ')}.",
        })
    return {"agents": agents, "criteria": criteria}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True, choices=["support", "claims", "crm"])
    ap.add_argument("--seed-evals", action="store_true", help="POST the seed-evals payload")
    ap.add_argument("--key", default=os.environ.get("PROVY_KEY", ""), help="ingest key")
    args = ap.parse_args()

    pack = get_pack(args.pack)
    payload = seed_evals_payload(pack)
    contract = contract_json(pack.contract())

    print("=== 1. POST /api/onboarding/seed-evals (agents + eval configs) ===")
    print(json.dumps(payload, indent=2))
    print("\n=== 2. Outcome contract (insert as the fleet's one active contract) ===")
    print(json.dumps(contract, indent=2))

    if args.seed_evals:
        if not args.key:
            print("\n[seed-evals] no ingest key (--key or PROVY_KEY). Skipped.")
            return 1
        req = urllib.request.Request(
            f"{BASE}/api/onboarding/seed-evals",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "x-provy-key": args.key},
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=20)
            print(f"\n[seed-evals] {resp.status}: {resp.read().decode()}")
        except Exception as e:
            print(f"\n[seed-evals] failed: {e}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
