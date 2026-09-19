#!/usr/bin/env python3
"""Browse and run the scenario/journey catalogue.

    python3 scripts/catalog.py                          # everything, at a glance
    python3 scripts/catalog.py --journey support_refund # one journey and its scenarios
    python3 scripts/catalog.py --scenario denial_overturned
    python3 scripts/catalog.py --gaps                   # what is unmeasured, what has no lever

    python3 scripts/catalog.py --run-scenario rca_named_by_proximity --count 20
    python3 scripts/catalog.py --run-journey support_refund --count 20

⛔ RUNNING A SCENARIO AND RUNNING A JOURNEY ARE DIFFERENT EXPERIMENTS, AND THE DIFFERENCE MATTERS.
A scenario runs ONE lever at rate 1.0, which is how you measure: Phase-A levers are exclusive, so in
a mix the first to fire claims the run and the rest starve. A journey runs its scenarios at their
configured rates together, which is how you demonstrate, and its numbers cannot be attributed to any
single mechanism.

⛔ NEITHER PRINTS A SCORE. Emitting is one job; reading what Provy concluded is another, and running
them together is how a harness ends up grading its own homework. Score with engine.scoreboard, whose
ProvyQuery reads what Provy WROTE and never re-derives a verdict from the traces.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.catalog import (JOURNEYS, SCENARIOS, scenarios_for,  # noqa: E402
                            unmeasured, without_lever)

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"


def _w(text: str, width: int = 92, indent: str = "    ") -> str:
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(line); line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return "\n".join(indent + l for l in out)


def show_scenario(s, verbose=True):
    lever = f"`{s.lever}`" if s.lever else "NO LEVER YET"
    mark = "" if s.measured else "  ·  unmeasured"
    print(f"\n{BOLD}{s.key}{RESET}  [{s.journey} / {s.step}]  {lever}{mark}")
    print(_w(s.mechanism))
    if not verbose:
        return
    e = s.evidence
    print(f"{DIM}    \"{e.quote}\"{RESET}")
    print(f"{DIM}      {e.who} · {e.date} · {e.tier}{RESET}")
    print(f"{DIM}      {e.url}{RESET}")
    print(f"    expected: ", end="")
    print(_w(s.expected, indent="").replace("\n", "\n              "))
    if s.measured:
        print(f"    {BOLD}measured ({s.measured_on}):{RESET} ", end="")
        print(_w(s.measured, indent="").replace("\n", "\n              "))


def show_journey(j, with_scenarios=True):
    print(f"\n{BOLD}{j.name}{RESET}  ({j.key}, pack={j.pack or 'none'})")
    for st in j.steps:
        hits = [s.key for s in scenarios_for(j.key) if s.step == st.id]
        flag = f"   <- {', '.join(hits)}" if hits else ""
        print(f"    {st.id}  {st.name:<48} {DIM}{st.owner}{RESET}{flag}")
    print(f"\n    {BOLD}settles:{RESET} " + _w(j.settles_where, indent="").replace("\n", "\n             "))
    print(f"    {BOLD}lag:{RESET}     {j.settlement_lag}")
    for src in j.sources:
        print(f'{DIM}    "{src.quote}" — {src.who}, {src.url}{RESET}')
    if with_scenarios:
        for s in scenarios_for(j.key):
            show_scenario(s, verbose=False)


def run_batch(pack, levers_json, count, seed):
    """Emit. Nothing here reads Provy back; see the module docstring."""
    since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    cmd = [".venv/bin/python", "scripts/run_batch.py", "--pack", pack,
           "--count", str(count), "--seed", str(seed), "--reconcile", "--show", "0"]
    if levers_json:
        cmd += ["--levers", levers_json]
    print(f"{DIM}$ {' '.join(cmd)}{RESET}")
    rc = subprocess.call(cmd)
    print(f"\n{BOLD}score this window with:{RESET}")
    print(f"    PROVY_SCORE_SINCE='{since}' PROVY_WORKFLOW_ID=<id> \\\n"
          f"      .venv/bin/python -c 'from engine.scoreboard import ProvyQuery as Q; "
          f"q=Q(); print(q.attribution_mix())'")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--journey"); ap.add_argument("--scenario")
    ap.add_argument("--gaps", action="store_true")
    ap.add_argument("--run-scenario"); ap.add_argument("--run-journey")
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--seed", type=int, default=91)
    a = ap.parse_args()

    if a.run_scenario:
        s = SCENARIOS.get(a.run_scenario)
        if not s:
            print(f"no such scenario: {a.run_scenario}"); return 1
        if not s.lever:
            print(f"⛔ `{s.key}` has evidence but no lever, so it cannot be reproduced yet.")
            print(_w(s.mechanism)); return 2
        pack = JOURNEYS[s.journey].pack
        if not pack:
            print(f"⛔ journey `{s.journey}` has no pack, so nothing can run it."); return 2
        show_scenario(s)
        print()
        return run_batch(pack, f'{{"{s.lever}":{{"rate":1.0}}}}', a.count, a.seed)

    if a.run_journey:
        j = JOURNEYS.get(a.run_journey)
        if not j:
            print(f"no such journey: {a.run_journey}"); return 1
        if not j.pack:
            print(f"⛔ `{j.key}` has no pack, so nothing can run it."); return 2
        show_journey(j)

        # ⛔ A JOURNEY FIRES ITS OWN SCENARIOS, NOT THE PACK'S DEFAULT MIX.
        # The first version of this ran the pack's configured rates, which is a different experiment
        # wearing this one's name: the journey would have reported numbers produced by levers that
        # are not in its catalogue, and omitted the scenarios that are. A journey is the bundle of
        # the mechanisms attached to its steps, so that bundle is what runs.
        scens = scenarios_for(j.key)
        runnable = [s for s in scens if s.lever]
        blocked = [s for s in scens if not s.lever]
        if not runnable:
            print(f"\n⛔ every scenario on `{j.key}` is missing a lever, so the journey cannot run.")
            for s in blocked:
                print(f"    {s.key}")
            return 2

        rate = round(1.0 / len(runnable), 4)
        levers = "{" + ",".join(f'"{s.lever}":{{"rate":{rate}}}' for s in runnable) + "}"
        print(f"\n{BOLD}firing this journey's {len(runnable)} scenario(s){RESET} at {rate} each:")
        for s in runnable:
            print(f"    {s.step}  {s.key:<34} {s.lever}")
        if blocked:
            # ⛔ Silence here would let a journey look complete while a step was never exercised.
            print(f"\n⚠ {len(blocked)} scenario(s) on this journey have no lever and will NOT fire, "
                  f"so those steps go untested:")
            for s in blocked:
                print(f"    {s.step}  {s.key}")
        print(f"\n{DIM}⛔ Phase-A levers are exclusive: the first to fire claims the run, so these "
              f"rates are upper bounds and the result cannot be attributed to any one mechanism. "
              f"To measure a mechanism, run it alone with --run-scenario.{RESET}\n")
        return run_batch(j.pack, levers, a.count, a.seed)

    if a.scenario:
        s = SCENARIOS.get(a.scenario)
        if not s:
            print(f"no such scenario: {a.scenario}"); return 1
        show_scenario(s); print(); return 0

    if a.journey:
        j = JOURNEYS.get(a.journey)
        if not j:
            print(f"no such journey: {a.journey}"); return 1
        show_journey(j); print(); return 0

    if a.gaps:
        um, nl = unmeasured(), without_lever()
        print(f"\n{BOLD}{len(um)} of {len(SCENARIOS)} scenarios have never been measured{RESET}")
        print(f"{DIM}Their 'expected' is somebody's reasoning, not a result.{RESET}")
        for s in um:
            print(f"    {s.key:<34} {s.lever or 'NO LEVER'}")
        print(f"\n{BOLD}{len(nl)} carry evidence and cannot be reproduced at all{RESET}")
        for s in nl:
            print(f"    {s.key:<34} {s.journey}/{s.step}")
        print()
        return 0

    print(f"\n{BOLD}Journeys{RESET}  {DIM}every one ends at a settlement somewhere else, later{RESET}")
    for j in JOURNEYS.values():
        n = len(scenarios_for(j.key))
        print(f"    {j.key:<22} {j.name:<44} {n} scenario(s)")
        print(f"      {DIM}settles after: {j.settlement_lag}{RESET}")
    um = unmeasured()
    print(f"\n{BOLD}Scenarios{RESET}  {len(SCENARIOS)} total, {DIM}{len(um)} never measured, "
          f"{len(without_lever())} with no lever{RESET}")
    for s in SCENARIOS.values():
        tick = " " if s.measured else "?"
        print(f"  {tick} {s.key:<34} {s.journey}/{s.step:<4} {s.lever or '(no lever)'}")
    print(f"\n{DIM}--journey KEY · --scenario KEY · --gaps · --run-scenario KEY · --run-journey KEY{RESET}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
