#!/usr/bin/env python3
"""24x7 tick: ask the Sim Control console which fleets are running and run one batch for each.

Called by .github/workflows/tick.yml on a schedule. Reads the running fleets (pack + ingest key +
Provy URL + reconcile flag) from CONTROL_URL/api/scheduler/due, gated by CONTROL_LEVERS_TOKEN, and runs
scripts/run_batch.py per fleet. Fleets are started/stopped from the console; a stopped fleet is simply
not in the list. Emits nothing when no fleet is running.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request


def due_fleets() -> list[dict]:
    base = os.environ.get("CONTROL_URL", "").strip().rstrip("/")
    token = os.environ.get("CONTROL_LEVERS_TOKEN", "").strip()
    if not base or not token:
        print("[tick] CONTROL_URL or CONTROL_LEVERS_TOKEN not set; nothing to do")
        return []
    req = urllib.request.Request(f"{base}/api/scheduler/due", headers={"x-control-token": token})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()).get("fleets", [])


def run_fleet(fleet: dict, count: str) -> int:
    pack = fleet["pack"]
    env = dict(os.environ)
    env[f"PROVY_KEY_{pack.upper()}"] = fleet["ingest_key"]
    env["PROVY_URL"] = fleet.get("provy_url") or "https://provyai.vercel.app"
    env["PROVY_EMIT"] = "1"
    # So run_batch can post its injected summary back to the console for this fleet.
    if fleet.get("workflow_id"):
        env[f"CONTROL_WORKFLOW_ID_{pack.upper()}"] = fleet["workflow_id"]
    args = ["python", "scripts/run_batch.py", "--pack", pack, "--count", count]
    if fleet.get("reconcile"):
        args.append("--reconcile")
    print(f"[tick] running {pack}: count={count} reconcile={bool(fleet.get('reconcile'))}")
    return subprocess.run(args, env=env).returncode


def main() -> int:
    fleets = due_fleets()
    if not fleets:
        print("[tick] no running fleets")
        return 0
    count = os.environ.get("TICK_COUNT", "6")
    failures = 0
    for f in fleets:
        try:
            if run_fleet(f, count) != 0:
                failures += 1
        except Exception as e:  # one fleet failing must not stop the others
            print(f"[tick] fleet {f.get('pack')} failed: {e}")
            failures += 1
    print(f"[tick] ran {len(fleets)} fleet(s), {failures} failure(s)")
    return 0  # never fail the whole tick on one fleet


if __name__ == "__main__":
    sys.exit(main())
