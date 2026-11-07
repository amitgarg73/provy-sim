#!/usr/bin/env python3
"""Runs Provy's linking pass on a PRE-PROD host, so the chain view connects the sessions a run just produced.

WHY: the linking pass is a Vercel cron, and Vercel runs crons only on production. Dev has no scheduler, so sessions that name a parent in another workflow stay unconnected
(the chain stops after the first system) until something calls the pass. The access workflow calls this at the end of a run; run it by hand after any other run on dev.

    ../argus/scripts/with-secrets CRON_SECRET_PREVIEW -- python3 scripts/link_chains.py [--base https://dev.provy.ai] [--rounds 3] [--wait 60]

Reads CRON_SECRET_PREVIEW from the environment, never from a flag. Refuses any host that is not a pre-prod one. Prints counts, never the secret.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

PREPROD_HOSTS = ("dev.provy.ai",)


def link_once(base: str, secret: str) -> dict:
    req = urllib.request.Request(f"{base}/api/compute/flow-links?mode=pending", headers={"Authorization": f"Bearer {secret}"})
    try:
        with urllib.request.urlopen(req, timeout=170) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "body": e.read().decode()[:160]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://dev.provy.ai")
    ap.add_argument("--rounds", type=int, default=3, help="how many times to run the pass (sessions can arrive a little after the run ends)")
    ap.add_argument("--wait", type=int, default=60, help="seconds between rounds")
    a = ap.parse_args()
    host = a.base.split("//", 1)[-1].split("/", 1)[0]
    if host not in PREPROD_HOSTS:
        print(f"REFUSED: {host} is not a pre-prod host; this script only links on {', '.join(PREPROD_HOSTS)}")
        return 2
    secret = os.environ.get("CRON_SECRET_PREVIEW", "").strip()
    if not secret:
        print("SKIPPED: CRON_SECRET_PREVIEW is not set, so the chains were not linked. Run scripts/link_chains.py with it, or wait for production's own schedule.")
        return 0
    bad = 0
    for i in range(max(1, a.rounds)):
        out = link_once(a.base.rstrip("/"), secret)
        if out.get("ok"):
            print(f"link pass {i + 1}: created {out.get('created')}, parents resolved {out.get('parentsResolved')}, left {out.get('left')}, failed {out.get('failed')}")
        else:
            print(f"link pass {i + 1}: {out}")
            bad += 1
        if i + 1 < a.rounds:
            time.sleep(a.wait)
    return 1 if bad == a.rounds else 0


if __name__ == "__main__":
    sys.exit(main())
