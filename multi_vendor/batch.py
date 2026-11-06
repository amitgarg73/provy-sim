"""A batch: many requests at once, each with its own scenario, drawn at random from a mix. The way to show Provy a realistic spread of passes and failures.

⛔ A BATCH IS A MIX AND A SEED. `plan_batch(total, weights, seed)` is PURE and repeatable: the same seed gives the same list, so a walk can be replayed. The mix is a weight per scenario;
counts are rounded so they add up to the total, and the order is shuffled so failures are not clustered at the end.
⛔ ONE FAULT MAP PER PERSON. Every item has its own person (`provy.iam.b001`...), so a single batch can carry a different failure for each request through one instance property.
⛔ CLEANUP IS IN A `finally`, like a single run: the faults are cleared, every person is re-seeded and the sync job is off.
Generic: a batch knows scenarios only by key and by the data on their rows.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .estate import Estate
from .grade import Verdict, grade
from .runner import SYNC_JOB, SYNC_WAIT, WORKER_JOB, Timeout, run_tag, seed_for, task_params
from .scenarios import BY_KEY, SCENARIOS, Scenario

# how often each kind of case turns up unless the caller says otherwise: mostly clean, a realistic tail of the different ways it goes wrong
DEFAULT_WEIGHTS: dict[str, float] = {
    "clean_leaver": 50, "vpn_missed": 12, "directory_missed": 7, "recreated_by_sync": 4, "wrong_person": 4, "over_reach": 6,
    "policy_refused": 5, "policy_bypassed": 3, "verifier_claims_ok": 5, "jit_not_expired": 2, "jit_expired": 2,
}


@dataclass(frozen=True)
class BatchItem:
    n: int
    key: str
    person: str


@dataclass
class BatchResult:
    items: list[BatchItem]
    verdicts: dict[int, Verdict] = field(default_factory=dict)
    matched: dict[int, bool] = field(default_factory=dict)
    errors: dict[int, str] = field(default_factory=dict)

    def summary(self) -> dict:
        by: dict[str, dict] = {}
        for it in self.items:
            row = by.setdefault(it.key, {"count": 0, "held": 0, "failed": 0, "as_expected": 0, "errors": 0})
            row["count"] += 1
            if it.n in self.errors:
                row["errors"] += 1
                continue
            v = self.verdicts.get(it.n)
            if v is not None:
                row["held" if v.held else "failed"] += 1
                row["as_expected"] += 1 if self.matched.get(it.n) else 0
        return {"total": len(self.items), "held": sum(r["held"] for r in by.values()), "failed": sum(r["failed"] for r in by.values()),
                "unexpected": sum(1 for it in self.items if it.n not in self.errors and not self.matched.get(it.n, False)), "errors": len(self.errors), "by_scenario": by}


def person_for(n: int) -> str:
    return f"provy.iam.b{n:03d}"


def plan_batch(total: int, weights: Optional[dict] = None, seed: int = 1, scenarios: Optional[dict] = None) -> list[BatchItem]:
    """The list of items for `total` requests: counts follow the weights (largest remainder, so they sum to the total), order is shuffled by the seed."""
    if total < 1:
        raise ValueError("a batch needs at least one request")
    pool = scenarios or BY_KEY
    w = {k: float(v) for k, v in (weights or DEFAULT_WEIGHTS).items() if float(v) > 0}
    unknown = [k for k in w if k not in pool]
    if unknown:
        raise ValueError(f"unknown scenario in the mix: {', '.join(sorted(unknown))}")
    if not w:
        raise ValueError("the mix has no scenario with a weight above zero")
    whole = sum(w.values())
    exact = {k: total * v / whole for k, v in w.items()}
    counts = {k: int(x) for k, x in exact.items()}
    for k in sorted(exact, key=lambda k: (exact[k] - counts[k], k), reverse=True)[: total - sum(counts.values())]:
        counts[k] += 1
    keys = [k for k in sorted(counts) for _ in range(counts[k])]
    random.Random(seed).shuffle(keys)
    return [BatchItem(i + 1, k, person_for(i + 1)) for i, k in enumerate(keys)]


def parse_mix(text: str) -> dict:
    """'clean_leaver=50,vpn_missed=10' -> weights. Anything else is refused with the part that was wrong."""
    out: dict = {}
    for part in [p for p in text.split(",") if p.strip()]:
        k, _, v = part.partition("=")
        try:
            out[k.strip()] = float(v)
        except ValueError:
            raise ValueError(f"bad mix entry {part!r}: write it as scenario=weight") from None
    return out


def run_batch(items: list[BatchItem], estate: Estate, trigger: str = "direct", *, agentic: bool = False, agents: bool = True,
              open_request: Optional[Callable[[BatchItem, Scenario], Callable[[], str]]] = None, wait: float = 240, poll: float = 10,
              sleep: Callable[[float], None] = time.sleep, now: Callable[[], float] = time.monotonic) -> BatchResult:
    if trigger not in ("direct", "servicely"):
        raise ValueError("trigger must be direct or servicely")
    if trigger == "servicely" and open_request is None:
        raise ValueError("the servicely trigger needs open_request")
    scns = {it.n: BY_KEY[it.key] for it in items}
    if trigger == "servicely":
        notready = sorted({s.key for s in scns.values() if not s.servicely_ready})
        if notready:
            raise ValueError("not wired for the Servicely flow yet: " + ", ".join(notready) + " (run the batch with --trigger direct, or leave them out of the mix)")
    res = BatchResult(items)
    agents_on = False
    try:
        estate.set_fault({})
        for it in items:
            estate.clear_hr_events(it.person)
            seed_for(scns[it.n], estate, it.person)
        baselines = {it.n: estate.readings(it.person) for it in items}
        faults = {it.person: scns[it.n].fault for it in items if scns[it.n].fault}
        estate.set_person_faults(faults) if faults else estate.set_fault({})
        # a batch exists to put sessions and outcomes in front of Provy, and those come from the ServiceNow-side agents, so they are on for the run (and off after) unless told otherwise
        if not agentic and (agents or any(scns[it.n].needs_agents for it in items)):
            estate.set_agentic(True); agentic = agents_on = True
        tag = run_tag()
        if trigger == "direct":
            for it in items:
                scn = scns[it.n]
                try:
                    params = task_params(scn, estate, it.person, correlation=f"SIM{tag}-{it.n:03d}" if agentic else None)
                    if agentic:
                        estate.agent_apply(scn.command, params, queue_item=f"batch-{it.n}-{scn.key}")
                    else:
                        estate.apply(scn.command, params)
                except Exception as e:  # noqa: BLE001 - one bad item must not stop the batch
                    res.errors[it.n] = str(e)[:160]
        else:
            estate.set_job_active(WORKER_JOB, True)
            for it in items:
                try:
                    open_request(it, scns[it.n])()
                except Exception as e:  # noqa: BLE001
                    res.errors[it.n] = str(e)[:160]
            pending = {it.n for it in items if it.n not in res.errors}
            end = now() + wait
            while pending and now() < end:
                pending = {n for n in pending if estate.readings(next(i.person for i in items if i.n == n)) == baselines[n]}
                if pending:
                    sleep(poll)
            for n in pending:
                res.errors[n] = f"timed out after {int(wait)}s waiting for the estate to change"
        resync = [it for it in items if scns[it.n].resync and it.n not in res.errors]
        if resync:
            for it in resync:
                estate.queue_resync(it.person)
            estate.set_job_active(SYNC_JOB, True)
            end = now() + max(wait, SYNC_WAIT)
            waiting = {it.n for it in resync}
            while waiting and now() < end:
                waiting = {n for n in waiting if estate.readings(next(i.person for i in items if i.n == n)).get("account_active") is not True}
                if waiting:
                    sleep(poll)
            for n in waiting:
                res.errors[n] = "the HR sync never re-created the account"
        for it in items:
            if it.n in res.errors:
                continue
            scn = scns[it.n]
            v = grade(estate.readings(it.person), scn.kind)
            res.verdicts[it.n], res.matched[it.n] = v, v.held == scn.should_hold
        return res
    finally:
        steps = [lambda: estate.set_job_active(SYNC_JOB, False), lambda: estate.set_fault({})]
        for it in items:
            steps.append(lambda it=it: estate.clear_hr_events(it.person))
            steps.append(lambda it=it: estate.seed(it.person))
            if scns[it.n].bystander:
                steps.append(lambda it=it: estate.seed(estate.bystander_of(it.person)))
        if agents_on:
            steps.append(lambda: estate.set_agentic(False))
        for step in steps:
            try:
                step()
            except Exception:  # noqa: BLE001 - cleanup must try every step
                pass
