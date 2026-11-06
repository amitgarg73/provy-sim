#!/usr/bin/env python3
"""Multi Vendor Scenario: a leaver that has to land in another vendor's systems. List, run one, run all, or reset the estate.

    ../argus/scripts/with-secrets SERVICENOW_INSTANCE SERVICENOW_USER SERVICENOW_PASSWORD -- python scripts/run_multi_vendor.py run-all
    ../argus/scripts/with-secrets SERVICENOW_INSTANCE SERVICENOW_USER SERVICENOW_PASSWORD SERVICELY_INSTANCE_URL SERVICELY_API_TOKEN -- \\
        python scripts/run_multi_vendor.py run vpn_missed --trigger servicely

--trigger direct (default) proves the estate and the grading only. --trigger servicely is the real flow and needs an agent that can queue the Action.
Exit code is 1 when any scenario's outcome differs from what it should be. Prints states and counts, never a credential.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from multi_vendor.agent_request import make_open_desk_request, make_open_request
from multi_vendor.estate import Estate, EstateError
from multi_vendor.runner import Timeout, run
from multi_vendor.scenarios import BY_KEY, SCENARIOS


def _estate() -> Estate:
    try:
        return Estate(os.environ["SERVICENOW_INSTANCE"], os.environ["SERVICENOW_USER"], os.environ["SERVICENOW_PASSWORD"])
    except KeyError as e:
        sys.exit(f"missing {e.args[0]}: run this through scripts/with-secrets (see the docstring)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["list", "run", "run-all", "reset"])
    ap.add_argument("key", nargs="?")
    ap.add_argument("--trigger", choices=["direct", "servicely"], default="direct")
    ap.add_argument("--start", choices=["desk", "orchestrator"], default="desk",
                    help="with --trigger servicely: desk raises a plain request at the service desk (the real front door); orchestrator hands it straight to the Access orchestrator")
    ap.add_argument("--agentic", action="store_true",
                    help="with --trigger direct: send the task through the ServiceNow-side agents (plan, validate, apply, verify, trace). Switches provy.iam.agentic on for the run and ALWAYS back off")
    ap.add_argument("--wait", type=int, default=240, help="seconds to wait for the estate to change")
    a = ap.parse_args()
    if a.cmd == "list":
        for s in SCENARIOS:
            print(f"{s.key:20} {'holds' if s.should_hold else 'FAILS':6} {s.title}")
        return 0
    est = _estate()
    if a.cmd == "reset":
        est.set_fault({}); est.set_job_active("provy_iam_hr_sync (POC)", False)
        for p in {s.person for s in SCENARIOS}:
            est.seed(p)
        print("estate reset: faults cleared, sync job off, people re-seeded")
        return 0
    todo = SCENARIOS if a.cmd == "run-all" else [BY_KEY[a.key]] if a.key in BY_KEY else sys.exit(f"unknown scenario {a.key!r}: try list")
    client = None
    if a.trigger == "servicely":
        from engine.servicely import client_from_env
        client = client_from_env(); client.require()
    if a.agentic and a.trigger != "direct":
        sys.exit("--agentic is for --trigger direct; with --trigger servicely the worker uses the flag as it is set on the instance")
    bad = 0
    if a.agentic:
        est.set_agentic(True)
    try:
        for s in todo:
            try:
                r = run(s, est, a.trigger, agentic=a.agentic, wait=a.wait, open_request=(make_open_desk_request if a.start == 'desk' else make_open_request)(client, s.person, s.key, s.systems) if client else None)
            except (Timeout, EstateError) as e:
                print(f"{s.key:20} ERROR  {e}"); bad += 1; continue
            word = "as expected" if r.matches_expectation else "UNEXPECTED"
            print(f"{s.key:20} {'held ' if r.verdict.held else 'FAILED'} {word:12} [{r.mode}] " + ("; ".join(r.verdict.failures) or "access ended everywhere"))
            bad += 0 if r.matches_expectation else 1
            for n in r.notes:
                print(f"{'':20} {n}")

    finally:
        if a.agentic:
            est.set_agentic(False)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
