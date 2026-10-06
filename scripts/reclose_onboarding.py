#!/usr/bin/env python3
"""Close a step 7 fleet's sessions again, oldest first, after its learned-state row was cleared, so the first close rebuilds
the state from the whole run (a real fleet has 20 hours; a replay of six days has an hour). Same body the step 2 script sent:
session id and terminal reason from the ledger. Pre-prod only. The key is read from the file and never printed.

    python scripts/reclose_onboarding.py --ledger RUN/sdk.ledger.jsonl --key-file KEYS/sdk.json [--workers 3]
"""
import argparse, json, os, sys
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import door_emit as D

ap = argparse.ArgumentParser()
ap.add_argument("--ledger", required=True); ap.add_argument("--key-file", required=True)
ap.add_argument("--base", default=os.environ.get("PROVY_URL", "http://localhost:3100")); ap.add_argument("--workers", type=int, default=3)
a = ap.parse_args()
key = json.load(open(a.key_file))["ingest_key"]
D.assert_preprod(a.base, key)
door = D.RestDoor(a.base, key, D.Replies(a.ledger.replace(".ledger.jsonl", ".reclose.replies.jsonl")))
recs = sorted((json.loads(l) for l in open(a.ledger)), key=lambda r: r["ts"])
seen, uniq = set(), []
for r in recs:
    if r["session_id"] not in seen:
        seen.add(r["session_id"]); uniq.append(r)
first = door.close_again(uniq[0]["session_id"], uniq[0]["terminal_reason"])      # alone: it rebuilds the state
with ThreadPoolExecutor(max_workers=a.workers) as ex:
    out = [first] + list(ex.map(lambda r: door.close_again(r["session_id"], r["terminal_reason"]), uniq[1:]))
bad = [o for o in out if o.get("status") != 200]
print(f"closed again: {len(out)}, not 200: {len(bad)}", bad[:2])
