#!/usr/bin/env python3
"""Install the ServiceNow side of the ITSM demo into the instance.

Two artifacts, both living in servicenow/ as reviewable files rather than as
script typed into a form that nobody can diff:

  verification_sweep.js  -> a scheduled job that decides whether each resolution
                            held, reopens the ones that did not, and closes the rest
  outcome_push.js        -> a business rule that pushes the settled outcome to Provy
                            when a demo incident closes

Idempotent: matches on name and updates in place, so running it again ships the
current file contents rather than creating a second copy.

Nothing here invents data. The sweep reads only what the agent wrote, and the push
sends only fields ServiceNow maintains plus two comparisons against the answer key
the generator recorded at creation.

    python scripts/install_servicenow_lifecycle.py --dry-run
    python scripts/install_servicenow_lifecycle.py
    python scripts/install_servicenow_lifecycle.py --run-sweep
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.servicenow import MARKER, ServiceNowError, client_from_env

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SN_DIR = os.path.join(HERE, "servicenow")

SWEEP_NAME = "Provy demo - verification sweep"
PUSH_NAME = "Provy demo - outcome push"

# Properties the demo reads. Created blank where a value is a secret: the installer
# never writes a credential into the instance, that is a deliberate one-time manual
# step so a secret cannot leak through a script run or a log.
PROPERTIES = [
    ("provy.ingest.key", "", "Provy ingest key for the ServiceNow demo fleet. Blank disables the push."),
    ("provy.ingest.url", "https://provydev.vercel.app/api/ingest/outcome",
     "Provy outcome ingest endpoint. Pre-prod only: nothing from this demo goes to production."),
    ("provy.vercel.bypass", "",
     "Vercel deployment-protection bypass token. Without it the push is blocked by Vercel "
     "before it ever reaches Provy, which looks like a Provy fault and is not one."),
    ("provy.demo.reopen_after_min", "3",
     "Minutes after resolution before verification runs. Compresses the real world's days."),
    ("provy.demo.reopen_pct", "6",
     "Overall percentage of resolved demo incidents that come back; each ticket moves around "
     "this figure based on how it was actually resolved. A real ServiceNow instance reopens "
     "1.1% (see servicenow/BENCHMARK.md). This demo desk is deliberately worse so 500 "
     "incidents yield ~30 reopens instead of 5, and the real figure gets quoted alongside it."),
    ("provy.demo.tickets_per_run", "3", "How many demo incidents the generator creates per run."),
]


def read_script(filename: str) -> str:
    with open(os.path.join(SN_DIR, filename)) as f:
        return f.read()


def upsert(sn, table: str, name: str, payload: dict, dry_run: bool) -> str:
    existing = sn.query(table, f"name={name}", ["sys_id", "name"], limit=1)
    if dry_run:
        return f"would {'update' if existing else 'create'} {table}: {name}"
    if existing:
        sn.update(table, existing[0]["sys_id"], payload)
        return f"updated {table}: {name}"
    sn.create(table, dict(payload, name=name))
    return f"created {table}: {name}"


# Hosts that serve production Provy. This demo is pre-prod only, so a push aimed at
# any of them is corrected rather than kept: real customer data lives there, and a
# demo instance writing into it is not a mistake anybody would notice quickly.
PROD_HOSTS = ("provy.ai", "provyai.vercel.app")
PREPROD_INGEST = "https://provydev.vercel.app/api/ingest/outcome"


def ensure_properties(sn, dry_run: bool) -> list[str]:
    out = []
    for name, value, description in PROPERTIES:
        existing = sn.query("sys_properties", f"name={name}", ["sys_id", "name", "value"], limit=1)
        if existing:
            current = existing[0]["value"]
            # One exception to keeping what is already there. Everything else is left
            # alone, but an ingest URL pointing at production is corrected on sight.
            if name == "provy.ingest.url" and any(h in current for h in PROD_HOSTS):
                out.append(f"CORRECTED sys_properties: {name} pointed at PRODUCTION ({current}). "
                           f"This demo is pre-prod only.")
                if not dry_run:
                    sn.update("sys_properties", existing[0]["sys_id"], {"value": PREPROD_INGEST})
                continue
            # Never overwrite anything else already in the instance. The ingest key and
            # the bypass token are set by hand once, and a re-install must not wipe them.
            out.append(f"kept sys_properties: {name}"
                       f"{' (blank, still needs a value)' if not current else ''}")
            continue
        if dry_run:
            out.append(f"would create sys_properties: {name}")
            continue
        sn.create("sys_properties", {
            "name": name, "value": value, "description": description,
            "type": "string", "suffix": "", "write_roles": "admin", "read_roles": "admin",
        })
        out.append(f"created sys_properties: {name}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report what would change, change nothing")
    ap.add_argument("--run-sweep", action="store_true",
                    help="execute the verification sweep once, now, instead of waiting for the schedule")
    ap.add_argument("--interval-min", type=int, default=5, help="how often the sweep runs")
    args = ap.parse_args()

    sn = client_from_env()
    try:
        sn.require()
    except ServiceNowError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(f"instance={sn.instance}")
    for line in ensure_properties(sn, args.dry_run):
        print("  " + line)

    # The business rule that pushes a closed incident's outcome to Provy.
    print("  " + upsert(sn, "sys_script", PUSH_NAME, {
        "collection": "incident",
        "when": "after",
        "action_update": "true",
        "active": "true",
        "order": "1000",
        # Only this demo's incidents, only on the transition into Closed.
        "filter_condition": f"correlation_id={MARKER}^state=7^EQ",
        "condition": "previous.state != current.state",
        "script": read_script("outcome_push.js"),
        "description": "Pushes the settled outcome of a Provy demo incident to Provy pre-prod. "
                       "ServiceNow pushes; Provy never reads this instance.",
    }, args.dry_run))

    # The scheduled job that runs verification.
    print("  " + upsert(sn, "sysauto_script", SWEEP_NAME, {
        "active": "true",
        "run_type": "periodically",
        "run_period": f"1970-01-01 00:{args.interval_min:02d}:00",
        "script": read_script("verification_sweep.js"),
    }, args.dry_run))

    if args.run_sweep and not args.dry_run:
        print("\nrunning the sweep once now...")
        result = sn.create("sys_trigger", {
            "name": f"{SWEEP_NAME} (manual run)",
            "trigger_type": "0",
            "state": "0",
            "next_action": "1970-01-01 00:00:00",
            "job_id": sn.query("sysauto_script", f"name={SWEEP_NAME}", ["sys_id"], limit=1)[0]["sys_id"],
            "document": "sysauto_script",
            "document_key": sn.query("sysauto_script", f"name={SWEEP_NAME}", ["sys_id"], limit=1)[0]["sys_id"],
        })
        print(f"  queued trigger {result.get('sys_id', '?')}")

    print(f"\nrest_calls={sn.calls}")
    if not args.dry_run:
        print("\nStill needed by hand, once, in the instance (secrets never go through this script):")
        print("  provy.ingest.key    <- the ITSM fleet's Provy ingest key")
        print("  provy.vercel.bypass <- the Vercel protection bypass token")
        print("Until both are set the push logs that it is disabled and sends nothing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
