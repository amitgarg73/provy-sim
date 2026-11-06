"""Runs one Multi Vendor Scenario: reset the estate, inject the scenario's fault, make the change happen, read the truth, grade it, clean up.

Two ways to make the change happen ("triggers"). Both end in the same place, the estate's own reading:
  direct    the runner calls the estate's apply door itself. Proves the estate and the grading. It does NOT go through the other vendor's agent
            platform, so it is labelled as estate-only in the result and must never be reported as the whole flow.
  servicely the runner opens a Request on the agent platform and waits. An agent there queues the Action, the PDI worker applies it, the estate
            changes. This is the real flow, and it works only once an agent can queue the Action (see README, "What is not wired").
⛔ A SCENARIO CLEANS UP IN A `finally`: the fault is cleared, the person is re-seeded and the sync job is switched off, so the next run starts clean
even after a timeout or an error.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .estate import Estate
from .grade import Verdict, grade
from .scenarios import Scenario

SYNC_JOB = "provy_iam_hr_sync (POC)"
WORKER_JOB = "provy_iam_worker (POC)"
SYNC_WAIT = 480          # the sync job's period is 5 minutes


class Timeout(RuntimeError):
    pass


@dataclass
class Result:
    scenario: str
    mode: str                      # "estate-only" or "end-to-end"
    claim: str
    reading: dict
    verdict: Verdict
    matches_expectation: bool
    notes: list[str] = field(default_factory=list)


def _wait(pred: Callable[[], bool], wait: float, poll: float, sleep: Callable[[float], None], now: Callable[[], float], what: str) -> None:
    end = now() + wait
    while True:
        if pred():
            return
        if now() >= end:
            raise Timeout(f"timed out after {int(wait)}s waiting for {what}")
        sleep(poll)


def run(scn: Scenario, estate: Estate, trigger: str = "direct", *, agentic: bool = False, open_request: Optional[Callable[[], str]] = None,
        wait: float = 240, poll: float = 10, sleep: Callable[[float], None] = time.sleep, now: Callable[[], float] = time.monotonic) -> Result:
    if trigger not in ("direct", "servicely"):
        raise ValueError("trigger must be direct or servicely")
    if trigger == "servicely" and open_request is None:
        raise ValueError("the servicely trigger needs open_request")
    notes: list[str] = []
    try:
        estate.set_fault({})
        estate.clear_hr_events(scn.person)
        estate.seed(scn.person)
        baseline = estate.readings(scn.person)
        estate.set_fault(scn.fault)
        if trigger == "direct" and agentic:
            out = estate.agent_apply(scn.command, {"person": scn.person, "systems": list(scn.systems)}, queue_item=f"scenario-{scn.key}")
            v = (out or {}).get("verification") or {}
            notes.append(f"applied through the ServiceNow-side agents (plan from {out.get('plan_source')}, trace {out.get('trace_mode')}); the verifier said "
                         f"{'as asked' if v.get('held') else 'NOT as asked'}: {v.get('summary')}")
        elif trigger == "direct":
            estate.apply(scn.command, {"person": scn.person, "systems": list(scn.systems)})
            notes.append("applied through the estate's own door; the agent platform was not involved")
        else:
            estate.set_job_active(WORKER_JOB, True)
            notes.append("request " + open_request())
            _wait(lambda: estate.readings(scn.person) != baseline, wait, poll, sleep, now, "the estate to change after the request")
        if scn.resync:
            estate.queue_resync(scn.person)
            estate.set_job_active(SYNC_JOB, True)
            # the sync job runs every few minutes on the instance, so it gets longer than the request does
            _wait(lambda: estate.readings(scn.person).get("account_active") is True, max(wait, SYNC_WAIT), poll, sleep, now, "the HR sync to re-create the account")
            notes.append("the HR sync re-created the account")
        reading = estate.readings(scn.person)
        verdict = grade(reading)
        return Result(scn.key, "estate-only" if trigger == "direct" else "end-to-end", "all systems changed", reading, verdict,
                      verdict.held == scn.should_hold, notes)
    finally:
        for step in (lambda: estate.set_job_active(SYNC_JOB, False), lambda: estate.set_fault({}), lambda: estate.clear_hr_events(scn.person), lambda: estate.seed(scn.person)):
            try:
                step()
            except Exception:  # noqa: BLE001 - cleanup must try every step
                pass
