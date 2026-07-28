#!/usr/bin/env python3
"""Wire a freshly onboarded Provy fleet to the ServiceNow demo instance, in one command.

Onboarding a fleet through the Provy UI gives you an ingest key and nothing else. Everything that
makes ServiceNow behave as the system of record — the verification sweep, the outcome-push business
rule, the compressed SLA targets, and the three properties the push reads — still had to be installed
and pointed by hand, across two scripts, with the key pasted into one of them. That is four manual
steps too many, and getting any of them wrong fails silently: the push simply logs that it is disabled
and sends nothing.

    PROVY_INGEST_KEY='provy_...' python scripts/wire_itsm_fleet.py
    PROVY_INGEST_KEY='provy_...' python scripts/wire_itsm_fleet.py --provy-url https://provydev.vercel.app
    python scripts/wire_itsm_fleet.py --check          # verify only, change nothing

What it does, in order:

  1. installs/updates the ServiceNow artifacts (idempotent — ships the current file contents)
  2. sets provy.ingest.key from the environment, never from a command-line argument
  3. sets provy.ingest.url, refusing to leave it pointing at production by accident
  4. sets provy.vercel.bypass when VERCEL_PROTECTION_BYPASS is present, since pre-prod sits behind
     Vercel's deployment protection and the push cannot reach it without the header
  5. reads all of it back and reports what the instance actually holds

The read-back is the point. Every step here has failed quietly at least once, and a script that only
prints what it intended to do is worth very little.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.servicenow import ServiceNowError, client_from_env

HERE = os.path.dirname(os.path.abspath(__file__))

# Hosts that must never end up in provy.ingest.url for a demo run. A demo pointed at production
# writes real outcomes into the real ledger, which is exactly what happened on 2026-07-27.
PROD_HOSTS = ("provy.ai", "provyai.vercel.app")

DEFAULT_URL = "https://provydev.vercel.app/api/ingest/outcome"


def _run(script: str, *args: str, env_extra: dict[str, str] | None = None) -> bool:
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    res = subprocess.run([sys.executable, os.path.join(HERE, script), *args], env=env)
    return res.returncode == 0


def _properties(client) -> dict[str, str]:
    rows = client.query("sys_properties", "nameSTARTSWITHprovy.", ["name", "value"], limit=50)
    return {r["name"]: r["value"] for r in rows}


def _mask(v: str, keep: int = 12) -> str:
    if not v:
        return "(empty)"
    return v if len(v) <= keep else f"{v[:keep]}… ({len(v)} chars)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provy-url", default=os.environ.get("PROVY_URL", "").rstrip("/"),
                    help="Provy base URL for this fleet (default: PROVY_URL, else provydev)")
    ap.add_argument("--check", action="store_true", help="report the instance's state and change nothing")
    ap.add_argument("--allow-prod", action="store_true",
                    help="permit an ingest URL on a production host (refused by default)")
    args = ap.parse_args()

    try:
        client = client_from_env()
    except ServiceNowError as e:
        print(f"servicenow: {e}", file=sys.stderr)
        print("set SERVICENOW_INSTANCE / SERVICENOW_USER / SERVICENOW_PASSWORD", file=sys.stderr)
        return 2

    if args.check:
        props = _properties(client)
        print(f"instance={client.instance}")
        for name in ("provy.ingest.url", "provy.ingest.key", "provy.vercel.bypass",
                     "provy.demo.tickets_per_run", "provy.demo.reopen_pct", "provy.demo.reopen_after_min"):
            print(f"  {name:30} {_mask(props.get(name, ''))}")
        missing = [n for n in ("provy.ingest.key", "provy.ingest.url") if not props.get(n)]
        if missing:
            print(f"\nNOT WIRED: {', '.join(missing)} is blank. The push is disabled and sends nothing.")
            return 1
        print("\nwired.")
        return 0

    key = os.environ.get("PROVY_INGEST_KEY", "").strip()
    if not key:
        print("PROVY_INGEST_KEY is required. Copy it from the fleet's Connection panel in Provy.",
              file=sys.stderr)
        print("  PROVY_INGEST_KEY='provy_...' python scripts/wire_itsm_fleet.py", file=sys.stderr)
        return 2
    if not key.startswith("provy_"):
        print(f"PROVY_INGEST_KEY does not look like a Provy key (got {key[:8]}…).", file=sys.stderr)
        return 2

    base = (args.provy_url or "https://provydev.vercel.app").rstrip("/")
    url = base if base.endswith("/api/ingest/outcome") else f"{base}/api/ingest/outcome"
    if any(h in url for h in PROD_HOSTS) and not args.allow_prod:
        print(f"refusing to point the demo at production ({url}). Pass --allow-prod if you mean it.",
              file=sys.stderr)
        return 2

    print(f"instance={client.instance}")
    print(f"target={url}\n")

    print("1/4 installing the sweep, the push rule and the compressed SLAs")
    if not _run("install_servicenow_lifecycle.py"):
        print("lifecycle install failed", file=sys.stderr)
        return 1

    print("\n2/4 pointing the instance at this fleet")
    if not _run("set_servicenow_property.py", "provy.ingest.key", env_extra={"PROP_VALUE": key}):
        return 1

    print("\n3/4 setting the ingest URL")
    if not _run("set_servicenow_property.py", "provy.ingest.url", "--show-prefix", "40",
                env_extra={"PROP_VALUE": url}):
        return 1

    bypass = os.environ.get("VERCEL_PROTECTION_BYPASS", "").strip()
    if bypass:
        print("\n4/4 setting the Vercel bypass token")
        if not _run("set_servicenow_property.py", "provy.vercel.bypass", env_extra={"PROP_VALUE": bypass}):
            return 1
    else:
        print("\n4/4 VERCEL_PROTECTION_BYPASS not set; leaving provy.vercel.bypass as it is.")
        print("    A pre-prod target behind Vercel protection needs it or the push cannot reach Provy.")

    # Read back what the instance actually holds. Everything above reports its own intent; only this
    # reports the result.
    print("\nverifying")
    props = _properties(client)
    ok = True
    for name, want in (("provy.ingest.key", key), ("provy.ingest.url", url)):
        got = props.get(name, "")
        mark = "ok " if got == want else "BAD"
        if got != want:
            ok = False
        print(f"  {mark} {name:22} {_mask(got)}")
    if bypass:
        got = props.get("provy.vercel.bypass", "")
        print(f"  {'ok ' if got == bypass else 'BAD'} provy.vercel.bypass    {_mask(got)}")
        ok = ok and got == bypass

    if not ok:
        print("\nthe instance does not hold what we set. Nothing will push until that is resolved.",
              file=sys.stderr)
        return 1

    print("\nwired. Open the queue, then run the pack with arrivals alongside it:")
    print("  python scripts/seed_itsm_incidents.py --top-up 2 --priority-mix spread")
    print("  python scripts/seed_itsm_incidents.py --arrivals 10 --mean-gap 35 --priority-mix spread &")
    print("  PROVY_EMIT=1 PROVY_KEY_ITSM=$PROVY_INGEST_KEY python scripts/run_batch.py --pack itsm --count 12 --pace 20")
    print("\nSeeding the whole batch up front instead makes the response condition a stopwatch:")
    print("every ticket opens at the same moment, so the first N pass and the rest fail, every run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
