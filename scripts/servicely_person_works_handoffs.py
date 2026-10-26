#!/usr/bin/env python3
"""Plays the person who picks up the tickets the agent handed over: writes the closure code on each named ticket, as a person would.

The agent hands a ticket to a person and stops. In a demo nobody works that queue, so the tickets stay "waiting" in Provy for ever and the Ledger
reads Watch. This is the missing human step, as a repeatable script instead of a hand-run write.

Narrow on purpose: only tickets named in --numbers, only if the short description carries our marker, only if the ticket is still in work in
progress. Dry run unless --apply. Every write is appended to the --ledger file.

    cd "$HOME/Claude Projects/argus"
    scripts/with-secrets SERVICELY_INSTANCE_URL SERVICELY_API_TOKEN -- python3 PATH/servicely_person_works_handoffs.py --numbers INC0000191,INC0000195 [--apply]
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from engine.servicely import ServicelyClient, DEFAULT_MARKER  # noqa: E402


def plan(client, numbers: list[str]) -> list[dict]:
    out = []
    for n in numbers:
        rows = client.query("Incident", {"Number": n}, page_size=1)
        row = rows[0] if rows else None
        if not row:
            out.append({"number": n, "action": "skip", "why": "not found"}); continue
        if DEFAULT_MARKER not in (row.get("ShortDescription") or ""):
            out.append({"number": n, "action": "skip", "why": "not one of our test tickets"}); continue
        if row.get("WorkflowStatus") != "work_in_progress":
            out.append({"number": n, "action": "skip", "why": f"state is {row.get('WorkflowStatus')}"}); continue
        out.append({"number": n, "action": "resolve", "id": row.get("id")})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--numbers", required=True, help="comma-separated ticket numbers")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--ledger", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "person_resolve_ledger.jsonl"))
    a = ap.parse_args(argv)
    client = ServicelyClient()
    client.require()
    steps = plan(client, [x.strip() for x in a.numbers.split(",") if x.strip()])
    for s in steps:
        print(("RESOLVE " if s["action"] == "resolve" else "skip    ") + s["number"] + ("" if s["action"] == "resolve" else f"  ({s['why']})"))
    todo = [s for s in steps if s["action"] == "resolve"]
    if not a.apply:
        print(f"DRY RUN: {len(todo)} would be resolved as a person, nothing written.")
        return 0
    os.makedirs(os.path.dirname(os.path.abspath(a.ledger)), exist_ok=True)
    with open(a.ledger, "a") as f:
        for s in todo:
            client.update("Incident", s["id"], {"ClosureCode": "Solution provided"})
            f.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "resolved_as_person": s["number"]}) + "\n")
            print("resolved", s["number"], flush=True)
    print(f"done: {len(todo)} resolved as a person.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
