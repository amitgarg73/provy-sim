#!/usr/bin/env python3
"""Create realistic incidents in the ServiceNow demo instance for the ITSM fleet.

These are the work items the AI agent will pick up. They are modelled on ITIL
practice rather than mined from the instance, because the instance cannot be
mined: 67 seeded incidents with 12 state changes between them, a median
resolution time of 85 days, and 27 of them P1. That is filler, not a lived-in
service desk. So the shape here comes from how desks actually run (weighted to
P3/P4, resolved in hours, a long tail of password and access noise) and every
value is bound to the instance's own vocabulary so the records reconcile.

THE ANSWER KEY. Each incident records what it really is in `correlation_display`
(cat=<category>;grp=<group>). The agent never reads that field: it classifies
from the text like a real agent would, and ServiceNow keeps the answer. That is
what makes `predicted_category` and `recommended_group` falsifiable instead of
self-graded.

Examples:
    python scripts/seed_itsm_incidents.py --count 5 --dry-run
    python scripts/seed_itsm_incidents.py --count 20
    python scripts/seed_itsm_incidents.py --count 500 --sleep 0.3
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.servicenow import (CONTACT_TYPES, CORRECT_GROUP, MARKER,
                               ServiceNowError, client_from_env)

# Weighted the way a real desk arrives: mostly access noise and software, a
# steady trickle of network, hardware rarely, database rarest.
CATEGORY_WEIGHTS = [
    ("password_reset", 22),
    ("software", 26),
    ("inquiry", 18),
    ("network", 20),
    ("hardware", 10),
    ("database", 4),
]

TEMPLATES = {
    "password_reset": [
        ("Locked out of my account after too many attempts",
         "I mistyped my password a few times this morning and now the account is locked. I cannot sign in from my laptop or my phone."),
        ("Password expired and the reset link does not arrive",
         "My password expired overnight. I requested a reset twice and the email never arrives, checked junk as well."),
        ("MFA prompt not appearing on my phone",
         "Signing in asks me to approve on my phone but no prompt ever comes through, so I cannot get past the login screen."),
        ("Cannot sign in to the portal after returning from leave",
         "Back from three weeks leave and my credentials are rejected on every internal site. Colleagues can sign in normally."),
        ("Need password reset for the shared team account",
         "Nobody on the team can sign into the shared account. The person who owned it has left."),
    ],
    "software": [
        ("Outlook crashes on launch since this morning",
         "Outlook opens then closes immediately. Nothing changed on my side. Restarting the laptop did not help."),
        ("Excel will not open files from the shared drive",
         "Every spreadsheet on the shared drive opens as read only or hangs. Local files are fine."),
        ("Application crash when generating the monthly report",
         "The reporting application closes without an error message at about halfway through the monthly report."),
        ("Need the finance software installed on my new laptop",
         "New laptop arrived without the finance application. I cannot start the close until it is installed."),
        ("Licence warning on the design software",
         "A licence expiry warning appears every time I open the design software and it now refuses to save."),
        ("Windows update failed and keeps retrying",
         "The operating system update fails at 80 percent then rolls back. It has retried four times since yesterday."),
        ("Email client stuck offline",
         "The email client sits in offline mode. Webmail works, so the mailbox itself is reachable."),
    ],
    "inquiry": [
        ("How do I request access to the reporting dashboard",
         "I have been asked to review the weekly numbers but I do not know the process for requesting access."),
        ("Question about the new expense procedure",
         "Guidance please on which system to use for expenses now that the old one is retired."),
        ("Who owns the customer data export process",
         "I need a monthly export and I cannot work out which team to ask."),
        ("Request access to the shared project folder",
         "Joining the project this week and I need access to the shared folder to pick up the handover notes."),
        ("Training needed on the new ticketing screens",
         "The layout changed and I am not sure where to record time worked."),
    ],
    "network": [
        ("VPN drops every ten minutes when working from home",
         "The VPN connection drops roughly every ten minutes. Reconnecting works but it breaks every call and file transfer."),
        ("Cannot reach internal sites, DNS looks wrong",
         "Internal addresses do not resolve from my machine. Public sites are fine, so it looks like a name resolution problem."),
        ("Wireless keeps disconnecting in the east meeting rooms",
         "Wi-Fi drops for everyone in the east meeting rooms. It is fine at the desks on the same floor."),
        ("No IP address on the desk network port",
         "My machine gets no IP address from the desk port. It works over wireless, so it is the wired connection."),
        ("Severe latency to the London office",
         "Connections to the London file shares take minutes to open. Other sites are normal."),
        ("Firewall appears to block the vendor portal",
         "The vendor portal times out from the office network but loads fine from a personal connection."),
        ("Site down: no network on the second floor",
         "Nobody on the second floor has a network connection. This is affecting all users on that floor."),
    ],
    "hardware": [
        ("Laptop battery no longer charges",
         "The battery stopped charging. The laptop only runs while plugged in and shuts down the moment it is unplugged."),
        ("Second monitor not detected after docking",
         "The docking station only drives one monitor since this week. Both monitors work when plugged in directly."),
        ("Keyboard keys unresponsive",
         "Several keys on the laptop keyboard have stopped responding. An external keyboard works."),
        ("Disk almost full and the machine is unusable",
         "Warnings that the disk is full and the machine now takes several minutes to open anything."),
        ("Printer on level three jams on every job",
         "The printer jams on every job. Cleared the paper path twice and it jams again immediately."),
    ],
    "database": [
        ("Reporting queries timing out against the Oracle instance",
         "Queries that ran in seconds last week now time out. This is blocking the finance reporting run."),
        ("Deadlocks on the SQL Server order tables",
         "Repeated deadlock errors on the order tables during the overnight batch."),
        ("Replica is hours behind the primary",
         "The read replica is several hours behind, so the dashboards are showing yesterday's numbers."),
        ("Tablespace full on the DB2 instance",
         "The DB2 instance reports a full tablespace and inserts are failing."),
    ],
}

# A few of these mention that more than one person is affected, which is what
# drives urgency and impact up. The agent has to read that out of the text.

# ServiceNow computes priority from (impact, urgency), so the ticket arrives carrying the response
# target it will be graded against. Seeding without these left every ticket on the instance default,
# which is P3: one target for the whole batch, so the response condition reduced to a stopwatch and
# every miss looked identical.
_IMPACT_URGENCY = {"1": ("1", "1"), "2": ("1", "2"), "3": ("2", "2"), "4": ("3", "2")}

PRIORITY_MIXES = {
    # What a real desk looks like: 24,918 incidents on a live instance came out P3 94.2%, P4 3.1%,
    # P2 1.6%, P1 1.1% (servicenow/BENCHMARK.md). Keep this as the default so the honest shape is
    # what you get unless you ask for something else.
    "benchmark": [("1", 1), ("2", 2), ("3", 94), ("4", 3)],
    # For a short demo run. The compressed targets are 15s/1m/4m/8m by priority, so a single-priority
    # batch can only ever breach on elapsed time. A spread means the same delay passes one ticket and
    # breaches another, which is the point: the verdict starts depending on the ticket.
    "spread": [("1", 12), ("2", 26), ("3", 42), ("4", 20)],
}


def pick_category(rng: random.Random) -> str:
    pool = [c for c, w in CATEGORY_WEIGHTS for _ in range(w)]
    return rng.choice(pool)


def pick_priority(rng: random.Random, mix: str) -> str:
    pool = [p for p, w in PRIORITY_MIXES[mix] for _ in range(w)]
    return rng.choice(pool)


def build_incident(rng: random.Random, callers: list[dict], mix: str = "benchmark") -> dict:
    category = pick_category(rng)
    short, desc = rng.choice(TEMPLATES[category])
    impact, urgency = _IMPACT_URGENCY[pick_priority(rng, mix)]
    payload = {
        "short_description": short,
        "description": desc,
        "contact_type": rng.choice(CONTACT_TYPES),
        "impact": impact,
        "urgency": urgency,
        # The marker the agent and the reporting both filter on.
        "correlation_id": MARKER,
        # The answer key, held by the system of record. The agent never reads it.
        "correlation_display": f"cat={category};grp={CORRECT_GROUP[category]}",
    }
    if callers:
        payload["caller_id"] = rng.choice(callers)["sys_id"]
    return payload


def arrival_gaps(rng: random.Random, n: int, mean_gap: float) -> list[float]:
    """Seconds to wait before each arrival, drawn from an exponential distribution.

    Tickets do not arrive on a metronome and they do not all arrive at once. Both of those produce
    the same artefact in the end: how long a ticket waits becomes a function of its position in the
    queue, so the response condition passes for the first N and fails for the rest, every run, with
    nothing about the ticket or the agent deciding it. Exponential gaps are what an arrival process
    actually looks like, and they make the wait vary per ticket instead of counting upward.

    The tail is clipped at four times the mean. Untruncated it is occasionally long enough that the
    agent stands idle for most of a short run, which is realistic and useless in a demo.
    """
    return [min(rng.expovariate(1.0 / mean_gap), mean_gap * 4) for _ in range(n)]


def to_create(target: int, waiting: int) -> int:
    """How many incidents a top-up must create to leave `target` waiting to be worked.

    Seeding a flat count on every run is wrong once the same fleet is run repeatedly: whatever the
    last run left unworked stays in the backlog, so the queue grows without bound and a run works
    tickets opened days ago instead of the ones it just seeded. Topping up creates only the
    shortfall, which makes a repeated run idempotent in the only sense that matters here.
    """
    return max(0, target - max(0, waiting))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--top-up", type=int, default=0, metavar="N",
                    help="ensure N incidents are waiting to be worked, creating only the shortfall. "
                         "Use this instead of --count on a fleet that is run more than once.")
    ap.add_argument("--arrivals", type=int, default=0, metavar="N",
                    help="create N incidents DURING a run, at random intervals, instead of seeding "
                         "them all up front. Run this alongside the batch, not before it.")
    ap.add_argument("--mean-gap", type=float, default=35.0, metavar="SECONDS",
                    help="average seconds between arrivals. Set it near the time the agent takes "
                         "per incident: much faster and a queue just piles up, much slower and the "
                         "agent stands idle.")
    ap.add_argument("--priority-mix", choices=sorted(PRIORITY_MIXES), default="benchmark",
                    help="benchmark matches a real instance (94%% P3). spread widens it so the four "
                         "response targets are all exercised in a short run.")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.25,
                    help="seconds between creates. The PDI's rate limit is the binding "
                         "constraint on a large seed, not cost, so pace it rather than burst.")
    ap.add_argument("--dry-run", action="store_true", help="build the payloads, create nothing")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    sn = client_from_env()

    if args.dry_run:
        for _ in range(args.count):
            print(build_incident(rng, [], args.priority_mix))
        return 0

    try:
        sn.require()
    except ServiceNowError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    count = args.count
    if args.top_up:
        # Ask for exactly the target: the query is capped at that limit, and all we need to know is
        # whether the backlog already reaches it. This is the same query the pack works from, so a
        # ticket that is counted here is a ticket the run can actually pick up.
        waiting = len(sn.open_demo_incidents(limit=args.top_up))
        count = to_create(args.top_up, waiting)
        print(f"top-up: {waiting} waiting, target {args.top_up}, creating {count}")
        if count == 0:
            print("backlog already deep enough; nothing to create")
            return 0

    gaps = None
    if args.arrivals:
        count = args.arrivals
        gaps = arrival_gaps(rng, count, args.mean_gap)
        print(f"arrivals: {count} over ~{sum(gaps) / 60:.1f} min, mean gap {args.mean_gap:.0f}s")

    callers = sn.query("sys_user", "active=true^emailISNOTEMPTY", ["sys_id", "name"], limit=25)
    print(f"instance={sn.instance} callers={len(callers)} creating={count} "
          f"priority_mix={args.priority_mix}")

    created = []
    for i in range(count):
        # Wait BEFORE creating, so the first arrival lands after the run has already started on the
        # opening backlog rather than alongside it.
        if gaps:
            time.sleep(gaps[i])
        payload = build_incident(rng, callers, args.priority_mix)
        try:
            row = sn.create("incident", payload)
        except ServiceNowError as e:
            print(f"  failed at {i + 1}/{count}: {e}", file=sys.stderr)
            print(f"  created {len(created)} before failing: {', '.join(created[-5:])}")
            return 1
        created.append(row.get("number", "?"))
        if gaps:
            print(f"  arrival {i + 1}/{count}: {created[-1]} (after {gaps[i]:.0f}s)", flush=True)
        elif (i + 1) % 25 == 0 or i + 1 == count:
            print(f"  {i + 1}/{count} created (latest {created[-1]})")
        if args.sleep and not gaps:
            time.sleep(args.sleep)

    print(f"done. {len(created)} incidents tagged correlation_id={MARKER}")
    print(f"first={created[0]} last={created[-1]} rest_calls={sn.calls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
