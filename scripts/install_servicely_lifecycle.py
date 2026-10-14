#!/usr/bin/env python3
"""Install the Provy demo lifecycle on a Servicely instance, from git, idempotently.

The Servicely counterpart of scripts/install_servicenow_lifecycle.py. It writes three things, all
keyed by NAME so a second run updates in place and never duplicates:

  Trigger  "Provy demo - apply agent resolution"  servicely/resolve_trigger.js
  Trigger  "Provy demo - outcome push"            servicely/outcome_push_trigger.js
  Job      "Provy demo - verification sweep"      servicely/verification_sweep_job.js

and the non-secret ApplicationProperty rows they read. The ingest KEY is never written here: that is
scripts/wire_servicely_fleet.py, which reads it from the environment.

    scripts/with-secrets SERVICELY_INSTANCE_URL SERVICELY_API_TOKEN -- python scripts/install_servicely_lifecycle.py
    ... --check        report what the instance holds and change nothing

Starts in provy.push.mode = 'capture' so nothing is sent anywhere until a fleet is wired.
Every script acts only on tickets whose ShortDescription starts with the marker; the rest of the
instance is untouched.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.servicely import ServicelyError, client_from_env  # noqa: E402
from engine.targets import is_production_target  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SERVICELY_DIR = os.path.join(os.path.dirname(HERE), "servicely")

# The application the instance's own Incident triggers belong to (read from "Incident - Set closure fields").
APPLICATION = "c0a8016c680c1e3481680c31239b0004"
DEFAULT_INGEST_URL = "https://dev.provy.ai/api/ingest/outcome"

TRIGGERS = [
    # (name, file, order, description)
    ("Provy demo - apply agent resolution", "resolve_trigger.js", 9000,
     "Moves a marked demo incident to Resolved when the agent has written a closure code."),
    ("Provy demo - outcome push", "outcome_push_trigger.js", 9100,
     "Pushes the settled outcome of a marked demo incident to Provy when it is closed."),
]
JOBS = [
    ("Provy demo - verification sweep", "verification_sweep_job.js", "0 0/2 * ? * * *",
     "Reopens or closes marked demo incidents that were resolved a few minutes ago."),
]
PROPERTIES = [
    # (key, value, data type, description). No secret belongs in this list.
    ("provy.marker", "[provy-servicely]", "string", "Prefix on the ShortDescription of every ticket the Provy demo raises."),
    ("provy.demo.reopen_pct", "6", "integer", "Base percentage of resolved demo tickets that come back."),
    ("provy.demo.reopen_after_min", "3", "integer", "Minutes a demo ticket stays resolved before the sweep decides."),
    ("provy.push.mode", "capture", "string", "capture writes the payload into ClosureNotes and sends nothing; live sends it."),
    ("provy.ingest.url", DEFAULT_INGEST_URL, "string", "Where the outcome push posts. Never a production host."),
    ("provy.sweep.last_run", "never", "string", "Written by the sweep job: time and counts of its last pass."),
    ("provy.sweep.last_error", "", "string", "Written by the sweep job: its last failure, if any."),
]


def _read(name: str) -> str:
    with open(os.path.join(SERVICELY_DIR, name), encoding="utf-8") as f:
        return f.read()


def _by(rows: list[dict], field: str) -> dict[str, dict]:
    return {str(r.get(field)): r for r in rows}


def plan(client) -> list[tuple[str, str, str, dict]]:
    """What would change: (action, table, label, payload). Pure given the client's reads."""
    out: list[tuple[str, str, str, dict]] = []
    trig = _by(client.query("Trigger", page_size=300), "Name")
    for name, fname, order, desc in TRIGGERS:
        body = {"Name": name, "Table": "Incident", "TriggerWhen": "before", "OnCreate": False, "OnUpdate": True,
                "OnDelete": False, "Order": order, "Active": True, "StopOnException": False,
                "Application": APPLICATION, "Description": desc, "Script": _read(fname)}
        cur = trig.get(name)
        if cur is None:
            out.append(("create", "Trigger", name, body))
        elif any(cur.get(k) != body[k] for k in ("Script", "Order", "Active", "TriggerWhen", "OnUpdate", "OnCreate")):
            out.append(("update", "Trigger", name, dict(body, id=cur["id"])))
    jobs = _by(client.query("Job", page_size=300), "Name")
    for name, fname, cron, desc in JOBS:
        body = {"Name": name, "Schedule": cron, "Active": True, "Application": APPLICATION,
                "Description": desc, "Script": _read(fname)}
        cur = jobs.get(name)
        if cur is None:
            out.append(("create", "Job", name, body))
        elif any(cur.get(k) != body[k] for k in ("Script", "Schedule", "Active")):
            out.append(("update", "Job", name, dict(body, id=cur["id"])))
    props = _by(client.query("ApplicationProperty", page_size=500), "Key")
    for key, value, dtype, desc in PROPERTIES:
        cur = props.get(key)
        if cur is None:
            out.append(("create", "ApplicationProperty", key, {
                "Key": key, "Value": value, "DataType": dtype, "Description": desc,
                "ClientSideAccessible": False, "Application": APPLICATION}))
        # An existing value is never overwritten: it may be a deliberate setting (the mode, a reopen rate).
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report and change nothing")
    args = ap.parse_args()
    if is_production_target(DEFAULT_INGEST_URL):
        print("refusing: the default ingest URL points at production", file=sys.stderr)
        return 2
    try:
        client = client_from_env()
        client.require()
        changes = plan(client)
    except ServicelyError as e:
        print(f"servicely: {e}", file=sys.stderr)
        return 2
    if not changes:
        print("nothing to do: the instance already holds the current scripts and properties")
        return 0
    for action, table, label, body in changes:
        print(f"  {'would ' if args.check else ''}{action} {table}: {label}")
        if args.check:
            continue
        if action == "create":
            client.create(table, body)
        else:
            rid = body.pop("id")
            client.update(table, rid, body)
    print("checked." if args.check else "installed. Mode is 'capture' until a fleet is wired.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
