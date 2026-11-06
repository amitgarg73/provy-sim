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
from multi_vendor.batch import DEFAULT_WEIGHTS, parse_mix, plan_batch, run_batch
from multi_vendor.runner import Timeout, run
from multi_vendor.scenarios import BY_KEY, SCENARIOS


def _estate() -> Estate:
    try:
        return Estate(os.environ["SERVICENOW_INSTANCE"], os.environ["SERVICENOW_USER"], os.environ["SERVICENOW_PASSWORD"])
    except KeyError as e:
        sys.exit(f"missing {e.args[0]}: run this through scripts/with-secrets (see the docstring)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["list", "run", "run-all", "reset", "batch"])
    ap.add_argument("key", nargs="?")
    ap.add_argument("--trigger", choices=["direct", "servicely"], default="direct")
    ap.add_argument("--start", choices=["desk", "orchestrator"], default="desk",
                    help="with --trigger servicely: desk raises a plain request at the service desk (the real front door); orchestrator hands it straight to the Access orchestrator")
    ap.add_argument("--agentic", action="store_true",
                    help="with --trigger direct: send the task through the ServiceNow-side agents (plan, validate, apply, verify, trace). Switches provy.iam.agentic on for the run and ALWAYS back off")
    ap.add_argument("--wait", type=int, default=240, help="seconds to wait for the estate to change")
    ap.add_argument("--n", type=int, default=20, help="batch: how many requests")
    ap.add_argument("--seed", type=int, default=1, help="batch: the same seed gives the same list, so a walk can be replayed")
    ap.add_argument("--mix", default="", help="batch: scenario=weight,scenario=weight (default: mostly clean, a realistic tail of failures)")
    ap.add_argument("--plan-only", action="store_true", help="batch: print the list and touch nothing")
    ap.add_argument("--estate-only", action="store_true", help="batch: apply through the estate's own door, with no ServiceNow-side agents (so no sessions or outcomes reach Provy)")
    a = ap.parse_args()
    if a.cmd == "list":
        for s in SCENARIOS:
            tags = ("agents" if s.needs_agents else "") + (" direct-only" if not s.servicely_ready else "")
            print(f"{s.key:20} {'holds' if s.should_hold else 'FAILS':6} {s.title}" + (f"   [{tags.strip()}]" if tags.strip() else ""))
        print(f"\nbatch mix by default: " + ", ".join(f"{k}={int(v)}" for k, v in DEFAULT_WEIGHTS.items()))
        return 0
    if a.cmd == "batch":
        try:
            items = plan_batch(a.n, parse_mix(a.mix) if a.mix else None, a.seed)
        except ValueError as e:
            sys.exit(str(e))
        counts: dict = {}
        for it in items:
            counts[it.key] = counts.get(it.key, 0) + 1
        print(f"batch of {len(items)} (seed {a.seed}): " + ", ".join(f"{k} x{n}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1])))
        if a.plan_only:
            for it in items:
                print(f"  {it.n:3} {it.person:20} {it.key}")
            return 0
        est = _estate()
        client = None
        if a.trigger == "servicely":
            from engine.servicely import client_from_env
            client = client_from_env(); client.require()
        opener = (lambda it, scn: (make_open_desk_request if a.start == "desk" else make_open_request)(client, it.person, scn.key, scn.systems)) if client else None
        try:
            res = run_batch(items, est, a.trigger, agentic=a.agentic, agents=not a.estate_only, open_request=opener, wait=a.wait)
        except ValueError as e:
            sys.exit(str(e))
        sm = res.summary()
        print(f"\nheld {sm['held']}  failed {sm['failed']}  unexpected {sm['unexpected']}  errors {sm['errors']}  (of {sm['total']})")
        for k, row in sorted(sm["by_scenario"].items()):
            print(f"  {k:20} x{row['count']:<3} held {row['held']:<3} failed {row['failed']:<3} as expected {row['as_expected']:<3}" + (f" errors {row['errors']}" if row["errors"] else ""))
        for n, why in sorted(res.errors.items()):
            print(f"  item {n}: ERROR {why}")
        return 1 if sm["unexpected"] or sm["errors"] else 0
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
