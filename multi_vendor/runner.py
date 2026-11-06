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


def seed_for(scn: Scenario, estate: Estate, person: Optional[str] = None) -> None:
    """Put a scenario's people in their starting state: the person (with an expired grant when the case needs one) and the bystander when the case has one."""
    who = person or scn.person
    estate.seed(who, expired_grant=True) if scn.expired_grant else estate.seed(who)
    if scn.bystander:
        estate.seed(estate.bystander_of(who))


def run_tag(clock: Callable[[], float] = time.time) -> str:
    """A short token that is different for every run, so the work-item ids a direct run mints never collide with an earlier run's on the same day."""
    return time.strftime("%d%H%M%S", time.gmtime(clock()))


def task_params(scn: Scenario, estate: Estate, person: Optional[str] = None, correlation: Optional[str] = None) -> dict:
    """What the task carries. `acts_on_bystander` is the wrong-person case: the request names the person and the change is aimed at someone else.
    `correlation` is the work-item id a real request would carry (its request number). A direct run has no request, so it mints one."""
    who = person or scn.person
    target = estate.bystander_of(who) if scn.acts_on_bystander else who
    params = {"person": target} if scn.kind == "jit" else {"person": target, "systems": list(scn.systems)}
    if correlation:
        params["correlation"] = correlation
    return params


def run(scn: Scenario, estate: Estate, trigger: str = "direct", *, agentic: bool = False, open_request: Optional[Callable[[], str]] = None,
        wait: float = 240, poll: float = 10, sleep: Callable[[float], None] = time.sleep, now: Callable[[], float] = time.monotonic) -> Result:
    if trigger not in ("direct", "servicely"):
        raise ValueError("trigger must be direct or servicely")
    if trigger == "servicely" and open_request is None:
        raise ValueError("the servicely trigger needs open_request")
    if trigger == "servicely" and not scn.servicely_ready:
        raise ValueError(f"{scn.key} is not wired for the Servicely flow yet (its Servicely-side half is not built); run it with --trigger direct")
    notes: list[str] = []
    turned_agents_on = False
    try:
        estate.set_fault({})
        estate.clear_hr_events(scn.person)
        seed_for(scn, estate)
        baseline = estate.readings(scn.person)
        estate.set_person_faults({scn.person: scn.fault}) if scn.fault else estate.set_fault({})
        if scn.needs_agents and not agentic:
            estate.set_agentic(True); turned_agents_on = True; agentic = True
            notes.append("this case needs the ServiceNow-side agents (the lever lives there), so they were switched on for the run and back off after")
        if trigger == "direct" and agentic:
            out = estate.agent_apply(scn.command, task_params(scn, estate, correlation=f"SIM{run_tag()}-{scn.key}"), queue_item=f"scenario-{scn.key}")
            v = (out or {}).get("verification") or {}
            notes.append(f"applied through the ServiceNow-side agents (plan from {out.get('plan_source')}, trace {out.get('trace_mode')}); the verifier said "
                         f"{'as asked' if v.get('held') else 'NOT as asked'}: {v.get('summary')}")
        elif trigger == "direct":
            estate.apply(scn.command, task_params(scn, estate))
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
        verdict = grade(reading, scn.kind)
        return Result(scn.key, "estate-only" if trigger == "direct" else "end-to-end", "all systems changed", reading, verdict,
                      verdict.held == scn.should_hold, notes)
    finally:
        steps = [lambda: estate.set_job_active(SYNC_JOB, False), lambda: estate.set_fault({}), lambda: estate.clear_hr_events(scn.person),
                 lambda: estate.seed(scn.person)]
        if scn.bystander:
            steps.append(lambda: estate.seed(estate.bystander_of(scn.person)))
        if turned_agents_on:
            steps.append(lambda: estate.set_agentic(False))
        for step in steps:
            try:
                step()
            except Exception:  # noqa: BLE001 - cleanup must try every step
                pass
