#!/usr/bin/env python3
"""Close a fleet's sessions again, in time order, so the server judges them against a learned state built from the whole run.

WHY. The server rebuilds its learned context state at most once every 20 hours, and a replay of two weeks of
work arrives in about an hour, so the state is built from the first sessions only and every later session is
judged against it. A real fleet has the 20 hours. This sends `session/close` again for each session of the
run, oldest first, after the state row was cleared, so the first close rebuilds the state from everything sent.
The close body repeats what the first one said (terminal reason from the ledger), so nothing about the work changes.

    PROVY_EMIT=1 PROVY_URL=https://dev.provy.ai PROVY_KEY_CTX=... python3 scripts/reclose_context_fleet.py \
        --truth DIR/<fleet>.jsonl --ledger DIR/<fleet>.ledger.jsonl [--limit N] [--workers 6]

Pre-prod only, by the same allow list the emitter uses. The key is read from the environment.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.context_emit import assert_preprod_target
from engine.emitter import DEFAULT_BASE_URL, ProvyEmitter


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--key-env", default="PROVY_KEY_CTX")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--first-alone", action="store_true", help="close the oldest session alone first (it rebuilds the state), then the rest")
    a = ap.parse_args()
    base = (os.environ.get("PROVY_URL") or DEFAULT_BASE_URL).rstrip("/")
    assert_preprod_target(base)
    em = ProvyEmitter(ingest_key=os.environ.get(a.key_env, ""), base_url=base, is_simulated=False, capture=False)
    if not em.enabled:
        print("emit is off: set PROVY_EMIT=1 and the key variable")
        return 2
    recs = sorted((json.loads(l) for l in open(a.ledger)), key=lambda r: r["ts"])
    if a.limit:
        recs = recs[:a.limit]

    def close(r):
        return em._post("/api/ingest/session/close", {"session_id": r["session_id"], "terminal_reason": r["terminal_reason"]})

    em._verify_environment()
    out = []
    if a.first_alone and recs:
        out.append(close(recs[0]))
        recs = recs[1:]
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        out += list(ex.map(close, recs))
    errors = [o for o in out if o.get("error")]
    print(f"closed again: {len(out)}, errors: {len(errors)}", errors[:2])
    return 0


if __name__ == "__main__":
    sys.exit(main())
