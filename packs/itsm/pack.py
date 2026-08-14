"""Domain pack — AI-assisted incident triage and resolution on a REAL ServiceNow instance.

Every other pack in this harness invents the work item AND decides its outcome.
That is circular: Provy marks its own homework, and it is the first thing a sharp
buyer attacks. This pack breaks the circle. The incidents are real records in a
ServiceNow developer instance, the agent's triage, routing and resolution are
real writes to that instance, and the outcome is settled by ServiceNow's own
lifecycle and pushed to Provy from there. The simulation never reports a result.

What the simulation still owns is the AGENT'S BEHAVIOUR, which is legitimate: a
real agent has an error rate too. It classifies from the incident text, routes,
resolves, and commits to seven falsifiable forecasts. Whether those forecasts
held is not the simulation's to say.

The seven forecasts, all settled by fields ServiceNow maintains itself:

  predicted_category         vs the category the record ends up carrying
  predicted_priority         vs the priority the record ends up carrying
  recommended_group          vs whether the ticket had to be reassigned
  predicted_close_code       vs the close code on the closed record
  predicted_sla_result       vs first_response_time_met + resolution_time_met
  predicted_reopen_risk      vs reopen_count
  agent_confidence           vs whether the run succeeded at all

The contract grades four of those against reality (see contract()). The rest ride
along on the trace so the report can settle them without new platform code.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any, Optional

from engine.pack import BasePack
from engine.servicenow import (CORRECT_GROUP, DEMO_RESOLUTION_TARGET_S,
                               DEMO_RESPONSE_TARGET_S,
                               GENUINE_FIX_CODES, MARKER, STATE_IN_PROGRESS,
                               STATE_ON_HOLD, STATE_RESOLVED, SUBCATEGORIES,
                               ServiceNowClient, ServiceNowError, client_from_env)
from engine.types import (AgentSpec, Criterion, LeverManifest, RunContext,
                          RunResult, TraceStep, Wait)

# How many incidents to pull per read, so a batch is not one REST call per ticket.
_FETCH_SIZE = 25
# How long the agent will wait for a ticket to arrive before treating the queue as genuinely empty,
# and how often it looks. Long enough to cover a quiet stretch in a paced arrival stream, short
# enough that a truly unseeded instance still fails fast rather than hanging a CI job.
_IDLE_TIMEOUT_S = 120
_IDLE_POLL_S = 5

# Keyword -> category. This is the agent's classifier: crude on purpose, because a
# crude classifier is what produces the honest mix of confident-and-right,
# confident-and-wrong, and unsure-and-right that the confidence sweep needs.
_KEYWORDS = {
    "network": ["vpn", "wifi", "wi-fi", "wireless", "dns", "dhcp", "ip address", "network",
                "connection drops", "latency", "firewall"],
    "database": ["database", "db2", "oracle", "sql", "query", "deadlock", "tablespace", "replica"],
    "software": ["outlook", "email client", "excel", "application crash", "install", "licence",
                 "license", "patch", "operating system", "windows", "macos", "software"],
    "hardware": ["laptop", "monitor", "keyboard", "mouse", "disk", "memory", "cpu", "docking",
                 "battery", "printer", "hardware"],
    "password_reset": ["password", "locked out", "account locked", "reset my", "mfa", "cannot sign in"],
    "inquiry": ["how do i", "question", "request access", "guidance", "training", "who owns"],
}

# Resolution approach the agent takes, and the close code it writes for each. The
# weak approaches are the ones that come back: ServiceNow decides that, not us.
_APPROACH_CODE = {
    "root_cause_fix": "Solution provided",
    "change_applied": "Resolved by change",
    "known_problem": "Resolved by problem",
    "workaround": "Workaround provided",
    "advised_caller": "Resolved by caller",
    "no_fault_found": "No resolution provided",
}


class WriteRejected(Exception):
    """The instance refused a write. Carries the error trace step that records it."""

    def __init__(self, step: TraceStep):
        super().__init__(step.error or "write rejected")
        self.step = step



class ItsmPack(BasePack):
    workflow = "itsm"
    session_type = "incident"

    # ServiceNow settles these incidents and pushes the result itself. If the
    # simulation ever posted an outcome for this fleet, the demo would be back to
    # marking its own homework while looking exactly the same from outside.
    owns_outcome = False

    def __init__(self, client: Optional[ServiceNowClient] = None):
        self._client = client
        self._queue: deque[dict] = deque()
        self._fetched = 0
        # Tickets this run has already picked up. The desk keeps several open at once, and an open
        # ticket still matches the waiting-work query, so without this the same incident is handed
        # out again the moment the queue runs dry. Two journeys then work one ticket, write over
        # each other in the instance, and collide on the session id, so the run reports fewer
        # sessions than it worked and nobody is told why.
        self._issued: set = set()

    # ── the system of record ────────────────────────────────────────────────
    @property
    def client(self) -> ServiceNowClient:
        if self._client is None:
            self._client = client_from_env()
        return self._client

    # ── pipeline ────────────────────────────────────────────────────────────
    def agents(self) -> list[AgentSpec]:
        return [
            AgentSpec("triage", "Triage",
                      "Reads the incident and decides what it is and how urgent it is.", "🗂️", 0),
            AgentSpec("router", "Routing",
                      "Decides which support group should own the incident.", "🧭", 1),
            AgentSpec("resolver", "Resolution",
                      "Diagnoses the incident, applies a fix, and resolves the ticket.", "🛠️", 2),
            AgentSpec("reviewer", "Closure Check",
                      "Checks the resolution before it is committed and records what it expects to happen.", "✅", 3),
        ]

    # ── contract: five conditions, all settled by real ServiceNow records ───
    def contract(self) -> list[Criterion]:
        return [
            # NOT made_sla. That field is a roll-up across every SLA attached to the ticket, so a
            # ticket answered in seconds and fixed three days late graded as having MISSED first
            # response. outcome_push.js now reads contract_sla.target and reports the two
            # separately, which is what makes c5 below possible at all.
            Criterion("c1", "Met the response commitment", "both",
                      "first_response_time_met", "eq", True),
            Criterion("c2", "The resolution held", "both", "reopen_count", "eq", 0),
            Criterion("c3", "The resolution was a genuine fix", "outcome", "close_code", "in",
                      GENUINE_FIX_CODES),
            # Threshold 1, not 0, and this is not a fudge. ServiceNow increments
            # reassignment_count on every assignment_group change, including the
            # agent's own routing, so a perfectly handled ticket always lands on 1.
            # Anything above that is what the condition is actually about: the
            # ticket was passed on to somebody else. Verified on the live instance,
            # where four cleanly resolved tickets all came back with 1.
            Criterion("c4", "Handled without being passed to another team", "outcome",
                      "reassignment_count", "lte", 1),
            # The condition the journey model exists for. Every delay it generates (queued with the
            # wrong team, waiting on the caller, a second attempt after a handoff) lands on the
            # RESOLUTION clock, and until this condition existed none of it produced a contract
            # signal: the delay was visible in ServiceNow and invisible to Provy, so attribution had
            # nothing to attribute. ServiceNow already reports resolution time; what it cannot say is
            # which agent step spent it.
            Criterion("c5", "Resolved within the agreed time", "both",
                      "resolution_time_met", "eq", True),
        ]

    def lever_manifest(self) -> LeverManifest:
        # Required by the DomainPack interface. This pack does NOT run the shared
        # lever engine: every outcome-shaping lever writes real_signals, and real
        # signals here belong to ServiceNow. The agent's own error rates are read
        # off the same lever config by name (misclassify, misroute, weak_fix,
        # overconfidence), so the Sim Control console can still dial this fleet.
        return LeverManifest(
            resolver_agent="resolver",
            retriever_agent="resolver",
            reviewer_agent="reviewer",
            first_agent="triage",
            downstream_agent="resolver",
            correctness_signal="reopen_count",
            policy_signal="close_code",
            sla_signal="first_response_time_met",
            drift_agent="resolver",
        )

    def failure_cost(self) -> dict:
        # No dollar figure. A reopened incident costs rework hours, and this demo
        # reports touches and work minutes from the record rather than inventing a
        # rate card. Fabricating a per-ticket dollar cost is the exact thing the
        # report is meant to stop.
        return {}

    # ── work items come from ServiceNow, not from the generator ─────────────
    def generate_work_item(self, rng) -> tuple[dict, dict]:
        # Loops because a fetched batch can turn out to be entirely stale: every ticket in it was
        # picked up by another desk between the fetch and now. That is an empty queue in substance,
        # so it goes back for more rather than failing a run that has work available (provy-sim#6).
        while True:
            if not self._fresh_queued():
                # An empty queue is not necessarily an error. When incidents arrive during the run
                # rather than all up front, the agent catches up with the backlog and waits, which is
                # what a desk does between tickets. Only a queue that stays empty is a real problem.
                batch = self._await_work()
                self._fetched += len(batch)
                if not batch:
                    raise RuntimeError(
                        "no open incidents tagged '%s' arrived within %ds. Seed some first: "
                        "python scripts/seed_itsm_incidents.py --count 20" % (MARKER, _IDLE_TIMEOUT_S)
                    )
                self._queue.extend(batch)
            taken = self._take_queued()
            if taken is not None:
                return taken

    def _take_queued(self):
        """The next ticket still worth working, or None when the queue holds no such ticket.

        ⛔ CONFIRM EACH ONE IS STILL WAITING (provy-sim#6). This queue was fetched minutes ago and
        another desk may have resolved a ticket since. Working it anyway drags a resolved incident
        back to In Progress, ServiceNow counts that as a reopen, and the contract binds
        `resolution_persists` to reopen_count, so the promise fails for something no agent did.

        Skipping is done HERE rather than by the callers because they disagree about what an empty
        queue means: one blocks and raises, the other returns immediately. Returning None lets each
        keep its own answer instead of teaching this method both.
        """
        while self._queue:
            inc = self._queue.popleft()
            # Marked issued before the check, so a ticket that lost the race is not re-taken later.
            self._issued.add(inc.get("sys_id"))
            if self.client.still_waiting(inc["sys_id"]):
                return self._as_item(inc)
        return None

    def _as_item(self, inc: dict) -> tuple[dict, dict]:
        item = {
            "id": inc["number"],
            "sys_id": inc["sys_id"],
            "short_description": inc.get("short_description", ""),
            "description": inc.get("description", ""),
            "category": inc.get("category", ""),
            "priority": inc.get("priority", ""),
            "impact": inc.get("impact", ""),
            "urgency": inc.get("urgency", ""),
            "contact_type": inc.get("contact_type", ""),
            "opened_at": inc.get("opened_at", ""),
            "state": inc.get("state", ""),
        }
        # The simulation has NO ground truth here. Whether the agent was right is
        # settled by ServiceNow, which holds the answer in a field the agent never
        # reads. Returning {} keeps that honest and makes it impossible for pack
        # code to peek.
        return item, {}

    def try_generate_work_item(self):
        """Take a ticket if one is waiting, or return None immediately.

        The desk needs this. Blocking here would freeze every ticket already in flight while the
        run waits for the next arrival, so their holds would stop running down and the delay would
        land on whatever happened to be open at the time. Waiting is the caller's decision.
        """
        if not self._fresh_queued():
            batch = self._fresh(self.client.open_demo_incidents(limit=_FETCH_SIZE))
            self._fetched += len(batch)
            if not batch:
                return None
            self._queue.extend(batch)
        return self._take_queued()

    def _fresh(self, rows: list) -> list:
        """Drop anything this run is already working. See `_issued`."""
        return [r for r in rows if r.get("sys_id") not in self._issued]

    def _fresh_queued(self) -> bool:
        while self._queue and self._queue[0].get("sys_id") in self._issued:
            self._queue.popleft()
        return bool(self._queue)

    def _await_work(self) -> list:
        """Poll for waiting incidents, giving arrivals time to land before calling it empty.

        The wait is real elapsed time and it counts against the response targets of everything
        already in the queue, which is the honest behaviour: a desk that is idle is not a desk that
        is ahead. It only ever waits when there is nothing at all to work on.
        """
        deadline = time.monotonic() + _IDLE_TIMEOUT_S
        while True:
            batch = self._fresh(self.client.open_demo_incidents(limit=_FETCH_SIZE))
            if batch or time.monotonic() >= deadline:
                return batch
            time.sleep(_IDLE_POLL_S)

    def entity_id(self, item: Any) -> str:
        return item["id"]

    # ── the agent's own judgement ───────────────────────────────────────────
    def classify(self, text: str) -> tuple[str, bool]:
        """Return (category, unambiguous). Unambiguous means exactly one category
        matched the text, which is the only evidence the agent has for how sure to be."""
        low = (text or "").lower()
        hits = [cat for cat, words in _KEYWORDS.items() if any(w in low for w in words)]
        if len(hits) == 1:
            return hits[0], True
        if hits:
            return sorted(hits)[0], False
        return "inquiry", False

    def _rate(self, ctx: RunContext, name: str, default: float) -> float:
        """Read an error rate off the lever config.

        Deliberately NOT LeverConfig.get(), which treats rate 0 as "not set" and
        returns None. That is right for a chaos lever, where 0 means off and the
        caller skips it, but here it would silently swap an operator's explicit 0
        for the built-in default and make the fleet impossible to dial clean.
        Absent means default; present means exactly what was set.
        """
        setting = ctx.levers.settings.get(name)
        return setting.rate if setting is not None else default

    def _decide(self, item: dict, ctx: RunContext) -> dict:
        """Everything the agent concludes, before it touches the instance."""
        rng = ctx.rng
        text = f"{item['short_description']} {item['description']}"
        category, unambiguous = self.classify(text)

        # The agent's error rates. Exposed as levers so the Sim Control console can
        # dial them without a code change. Errors are likelier when the text did not
        # clearly point at one category, which is what makes confidence informative
        # rather than decorative.
        miss_rate = self._rate(ctx, "misclassify", 0.10) * (1.0 if unambiguous else 2.2)
        misroute_rate = self._rate(ctx, "misroute", 0.09) * (1.0 if unambiguous else 2.0)
        weak_fix_rate = self._rate(ctx, "weak_fix", 0.14) * (1.0 if unambiguous else 1.8)
        overconfident_rate = self._rate(ctx, "overconfidence", 0.10)

        misclassified = rng.random() < miss_rate
        if misclassified:
            others = [c for c in CORRECT_GROUP if c != category]
            category = rng.choice(others)
        predicted_category = category

        # Priority: the agent proposes impact/urgency and lets ServiceNow compute
        # priority, the way the platform actually works.
        urgency, impact = self._urgency_impact(text, rng)
        predicted_priority = _PRIORITY_MATRIX[(impact, urgency)]

        right_group = CORRECT_GROUP[predicted_category]
        misrouted = rng.random() < misroute_rate
        if misrouted:
            wrong = [g for g in sorted(set(CORRECT_GROUP.values())) if g != right_group]
            recommended_group = rng.choice(wrong)
        else:
            recommended_group = right_group

        # Whether the ticket landed with a team that can actually work it. The pack knows this from
        # the faults it INJECTED, never from what the instance holds: a misclassification routes
        # "correctly" for the wrong category, which is just as wrong in the queue. Reading the
        # instance's own record of what each incident really is would let the simulation grade its
        # own routing, which is the one thing this fleet exists to make impossible.
        landed_wrong = misrouted or misclassified

        # Diagnosis depth. A shallow diagnosis is not a wrong answer, it is an unfinished one: the
        # agent resolves without establishing what it needed, so the ticket goes on hold waiting for
        # detail from the caller that a documented procedure would have told it to collect up front.
        shallow = rng.random() < self._rate(ctx, "shallow_diagnosis", 0.18) * (1.0 if unambiguous else 1.6)

        # Resolution approach. A weak approach is a real, visible thing in the
        # record: a workaround, advice to the caller, or nothing found.
        if rng.random() < weak_fix_rate:
            approach = rng.choice(["workaround", "advised_caller", "no_fault_found"])
        else:
            approach = rng.choice(["root_cause_fix", "root_cause_fix", "change_applied", "known_problem"])
        close_code = _APPROACH_CODE[approach]

        # Confidence from evidence the agent can actually see. Overconfidence is a
        # separate, deliberate failure mode: sure and wrong.
        confidence = 0.88 if unambiguous else 0.62
        if predicted_priority in ("1", "2"):
            confidence -= 0.10
        if approach in ("workaround", "advised_caller", "no_fault_found"):
            confidence -= 0.16
        confidence += rng.uniform(-0.06, 0.06)
        if rng.random() < overconfident_rate:
            confidence = min(0.97, confidence + 0.28)
        confidence = round(min(0.97, max(0.35, confidence)), 2)

        # Forecasts about what happens next, from the agent's own point of view.
        reopen_risk = "low" if confidence >= 0.75 else ("medium" if confidence >= 0.55 else "high")
        predicted_sla_result = "met" if confidence >= 0.5 else "missed"

        return {
            "category": predicted_category,
            "subcategory": self._subcategory(predicted_category, text, rng),
            "urgency": urgency,
            "impact": impact,
            "predicted_priority": predicted_priority,
            "recommended_group": recommended_group,
            "approach": approach,
            "close_code": close_code,
            "confidence": confidence,
            "reopen_risk": reopen_risk,
            "predicted_sla_result": predicted_sla_result,
            "unambiguous": unambiguous,
            # The injected faults, carried forward so the journey can charge real time for them.
            "landed_wrong": landed_wrong,
            "misrouted": misrouted,
            "misclassified": misclassified,
            "shallow": shallow,
        }

    @staticmethod
    def _urgency_impact(text: str, rng) -> tuple[str, str]:
        """Return (urgency, impact) on ServiceNow's 1=High .. 3=Low scale.

        Calibrated to a real ServiceNow instance rather than to a rule of thumb:
        P3 94.2%, P4 3.1%, P2 1.6%, P1 1.1%, and no P5 at all, measured over 24,918
        incidents (see servicenow/BENCHMARK.md). A desk being almost entirely P3 is
        not a quirk of that company, it is what ITIL priority assignment produces.
        The invented spread this replaces put a third of tickets at P4 and P5, which
        is not a desk anybody runs.
        """
        low = (text or "").lower()
        if any(w in low for w in ("outage", "site down", "everyone", "all users", "no network")):
            return "1", "1"
        if any(w in low for w in ("team", "department", "several users", "blocked", "everyone on")):
            return rng.choice([("1", "2"), ("2", "2"), ("2", "2")])
        # P1 1%, P2 2%, P3 94%, P4 3%. The (urgency, impact) pairs below map through
        # ServiceNow's stock matrix, which is keyed the other way round.
        pairs = ([("1", "1")] * 1                          # P1
                 + [("1", "2"), ("2", "1")] * 1            # P2
                 + [("2", "2")] * 40 + [("3", "1")] * 40 + [("1", "3")] * 14   # P3
                 + [("2", "3")] * 2 + [("3", "2")] * 1)    # P4
        return rng.choice(pairs)

    @staticmethod
    def _subcategory(category: str, text: str, rng) -> str:
        options = SUBCATEGORIES.get(category) or []
        if not options:
            return ""
        low = (text or "").lower()
        for opt in options:
            if opt in low:
                return opt
        return rng.choice(options)

    # ── run: the agent works the real ticket ────────────────────────────────
    def build_clean_run(self, item: dict, gt: dict, ctx: RunContext) -> RunResult:
        # ITSM has no "clean baseline then chaos" shape: the agent's behaviour IS the
        # run, and reality is somebody else's to report. run_pipeline does the work.
        return self.run_pipeline(item, gt, ctx)

    def run_pipeline(self, item: dict, gt: dict, ctx: RunContext) -> RunResult:
        """Work the ticket start to finish with no waiting. Kept for tests and for a quick run.

        The real fleet runs `journey()` instead, which is the same work with the desk's waits in it.
        """
        journey = self.journey(item, gt, ctx)
        try:
            while True:
                next(journey)
        except StopIteration as done:
            return done.value

    def journey(self, item: dict, gt: dict, ctx: RunContext):
        """The ticket's path through the desk, yielding a Wait wherever real time passes.

        A generator rather than a straight function so the caller owns the clock. The desk runs
        several of these at once and sleeps on whichever is due next, which is what a service desk
        does and what keeps one held ticket from delaying the ones behind it.

        Every wait here is bought by a decision the agent made, and that is the whole point: a
        response target that breaches because the ticket sat in the wrong group has a cause the
        record and the trace both carry. A target that breaches because of where the run happened
        to be in a loop has no cause at all, which is what this replaces.
        """
        d = self._decide(item, ctx)
        r = RunResult(
            entity_id=self.entity_id(item),
            session_type=self.session_type,
            session_id=self.session_id(item),
            # The simulation reports NOTHING about how this turned out. ServiceNow
            # pushes the outcome; until it does, this run is unsettled.
            real_signals={},
            outcome_label="skipped",
            outcome_value=None,
            confidence=d["confidence"],
        )
        try:
            return (yield from self._work_ticket(r, item, d, ctx))
        except WriteRejected as rejected:
            # The instance refused one of the agent's writes. That is a failed run,
            # not a missing one: the session still goes to Provy carrying the error,
            # and the agent claims nothing because it never got to finish.
            r.traces.append(rejected.step)
            r.terminal_reason = "tool_error"
            r.estimated_signals = {}
            r.metadata = {
                "system_of_record": "servicenow",
                "sn_sys_id": item["sys_id"],
                "outcome_source": "servicenow_push",
                "estimated_success": False,
                "rejected_by_system_of_record": rejected.step.error,
            }
            return r

    def _targets(self, item: dict) -> tuple[float, float]:
        """The (response, resolution) targets this ticket arrived carrying, in seconds.

        Waits are sized against these rather than against fixed seconds, so the same mistake costs
        what it should: it blows a P2's tight promise and may still come in under a P4's loose one.

        Two targets, not one, because they are ended by different things. The response clock stops
        when the ticket reaches a team; the resolution clock stops when it is actually resolved. A
        delay is only worth modelling against the promise it can still threaten.
        """
        p = str(item.get("priority") or "3")
        return (float(DEMO_RESPONSE_TARGET_S.get(p, 60)),
                float(DEMO_RESOLUTION_TARGET_S.get(p, 360)))

    def _work_ticket(self, r: RunResult, item: dict, d: dict, ctx: RunContext) -> RunResult:
        A = {a.name: a for a in self.agents()}
        eid = r.entity_id
        sys_id = item["sys_id"]
        rng = ctx.rng
        response_target, resolution_target = self._targets(item)

        # ⛔ THE CLOCK, TRACKED DURING THE RUN AND NOT ONLY AFTER IT.
        #
        # The desk records `journey.elapsed_s` when the ticket finishes, which is too late to be
        # something an agent could have READ. A real ITSM agent about to close a ticket can see how
        # long it has been open, because ServiceNow shows it. Accumulating the waits as they are
        # yielded gives the same number at the moment the agent would have had it.
        elapsed = {"s": 0.0}

        def wait(reason: str, seconds: float, cause=None) -> Wait:
            elapsed["s"] += seconds
            return Wait(reason, seconds, cause=cause)

        # 0. The ticket sits in the queue until the desk gets to it. This is the ONLY wait that can
        # touch the response target, whose clock stops the moment a group is assigned, and it is
        # not bought by any decision: it is how busy the desk was. The desk's own capacity supplies
        # most of it, so what is added here is just the ordinary moment before anyone picks up.
        yield wait("waiting_to_be_picked_up", rng.uniform(0.05, 0.35) * response_target, cause=None)

        # 1. Triage: decide, then write it to the incident.
        r.traces.append(self.agent_step(
            ctx, A["triage"], item,
            decision=(f"category={d['category']}, urgency={d['urgency']}, impact={d['impact']} "
                      f"(text {'clearly indicated one area' if d['unambiguous'] else 'was ambiguous'})"),
            entity_id=eid,
            payload_extra={"predicted_category": d["category"],
                           "predicted_priority": d["predicted_priority"]}))
        triage_patch = {
            "category": d["category"],
            "subcategory": d["subcategory"],
            "urgency": d["urgency"],
            "impact": d["impact"],
            "state": STATE_IN_PROGRESS,
            "work_notes": (f"[AI triage] Classified as {d['category']}"
                           f"{'/' + d['subcategory'] if d['subcategory'] else ''}. "
                           f"Urgency {d['urgency']}, impact {d['impact']}."),
        }
        r.traces.append(self._sn_step(ctx, A["triage"], "servicenow.update_incident",
                                      sys_id, triage_patch, eid))

        # 2. Routing: assign the group it believes owns this.
        r.traces.append(self.agent_step(
            ctx, A["router"], item,
            decision=f"assign to {d['recommended_group']}", entity_id=eid,
            payload_extra={"recommended_group": d["recommended_group"]}))
        group_id = self.client.group_sys_id(d["recommended_group"])
        route_patch = {
            "assignment_group": group_id,
            # Clearing the individual assignee is not a workaround, it is what moving
            # a ticket between teams means: whoever was on it is not on the new team.
            # The instance enforces exactly that. When triage raises the priority, an
            # assignment rule puts a named person on the incident, and the stock
            # "Abort changes on group" rule then rejects any group change that would
            # leave them assigned to a group they are not a member of. Observed live:
            # a P2 picked up Fred Luddy and the routing write came back HTTP 403.
            "assigned_to": "",
            "work_notes": f"[AI routing] Assigned to {d['recommended_group']}.",
        }
        r.traces.append(self._sn_step(
            ctx, A["router"], "servicenow.assign_incident", sys_id, route_patch,
            eid, extra_output={"assignment_group_name": d["recommended_group"]}))

        # 2a. The ticket now SITS in whatever queue the agent put it in. This is where a routing
        # mistake becomes expensive: the right team picks it up well inside the promise, the wrong
        # team leaves it until somebody notices it is not theirs.
        #
        # Sized against RESOLUTION, not response. The response clock stopped on the line above, when
        # the group was set, so nothing here can breach it. What a misroute actually costs is time
        # to resolve, and that is the promise this has to be measured against.
        if d["landed_wrong"]:
            yield wait("queued_with_the_wrong_team", rng.uniform(0.8, 1.5) * resolution_target,
                       cause="router")
            # Somebody notices. The reassignment is a real second touch on the record, which is
            # what the handled-without-handoff condition reads.
            fixed_group = CORRECT_GROUP[self.classify(
                f"{item['short_description']} {item['description']}")[0]]
            r.traces.append(self.agent_step(
                ctx, A["router"], item,
                decision=(f"{d['recommended_group']} did not own this; reassigned to {fixed_group} "
                          f"after it sat in their queue"),
                entity_id=eid,
                payload_extra={"reassigned_from": d["recommended_group"],
                               "reassigned_to": fixed_group,
                               "reason": "misroute" if d["misrouted"] else "misclassification"}))
            r.traces.append(self._sn_step(
                ctx, A["router"], "servicenow.reassign_incident", sys_id,
                {"assignment_group": self.client.group_sys_id(fixed_group), "assigned_to": "",
                 "work_notes": f"[service desk] Not owned by {d['recommended_group']}. "
                               f"Reassigned to {fixed_group}."},
                eid, extra_output={"assignment_group_name": fixed_group}))
        else:
            yield wait("queued_with_the_right_team", rng.uniform(0.05, 0.2) * resolution_target,
                       cause=None)

        # 2b. A shallow diagnosis shows up as a ticket parked on the caller. The agent resolves
        # without having established what it needed, so it has to go back and ask.
        if d["shallow"]:
            r.traces.append(self.agent_step(
                ctx, A["resolver"], item,
                decision="no documented procedure followed; asked the caller for detail before continuing",
                entity_id=eid,
                payload_extra={"procedure_followed": False, "awaiting_caller": True}))
            r.traces.append(self._sn_step(
                ctx, A["resolver"], "servicenow.hold_incident", sys_id,
                {"state": STATE_ON_HOLD,
                 "work_notes": "[AI resolution] Awaiting further detail from the caller."},
                eid))
            yield wait("awaiting_the_caller", rng.uniform(0.5, 1.1) * resolution_target,
                       cause="resolver")
            r.traces.append(self._sn_step(
                ctx, A["resolver"], "servicenow.resume_incident", sys_id,
                {"state": STATE_IN_PROGRESS,
                 "work_notes": "[AI resolution] Caller responded; continuing."},
                eid))

        # 3. Resolution: work it, then resolve the real ticket.
        r.traces.append(self.agent_step(
            ctx, A["resolver"], item,
            decision=(f"applied {d['approach'].replace('_', ' ')}; "
                      f"resolving with close code '{d['close_code']}'"),
            entity_id=eid,
            payload_extra={"resolution_approach": d["approach"], "close_code": d["close_code"]}))
        close_notes = _CLOSE_NOTES[d["approach"]]
        resolve_patch = {
            "state": STATE_RESOLVED,
            "close_code": d["close_code"],
            "close_notes": close_notes,
            "work_notes": f"[AI resolution] {close_notes}",
        }
        # ⛔ READ THE TICKET BEFORE CLOSING IT, AND CARRY WHAT THE READ SHOWS (argus#544).
        #
        # Every condition on this contract was outcome-side only, and the agents' readings lived on
        # the reviewer's agent_message. Provy's deterministic attribution scans TOOL OUTPUTS, so it
        # could never see them and 15 of 16 misses came back "nothing in the run accounts for these".
        #
        # A message is a CLAIM; a tool output is EVIDENCE. Making Provy read messages instead would
        # have been the wrong fix: in a silent failure the claim is good while reality is bad, so it
        # would match nothing, and where it did match it would blame an agent for correctly reporting
        # bad news.
        #
        # ⛔ ONLY WHAT THE AGENT COULD ACTUALLY KNOW AT THIS MOMENT. The SLA clocks have run and
        # ServiceNow shows them, so a resolver that checks before closing sees the breach. `reopen`
        # is deliberately ABSENT: the ticket reopens after this run ends, and reading it back here
        # would be inventing evidence that could not exist — the simulation marking its own homework.
        # Those misses stay honest blind spots.
        sla = {
            "first_response_time_met": elapsed["s"] <= response_target,
            "resolution_time_met": elapsed["s"] <= resolution_target,
            "age_seconds": round(elapsed["s"], 1),
            "response_target_seconds": round(response_target, 1),
            "resolution_target_seconds": round(resolution_target, 1),
        }
        # ⛔ ON THE RESOLVE RESPONSE, NOT A SEPARATE READ. The first attempt added a
        # `read_incident_sla` step through `_sn_step`, which performs a real WRITE — so a "read" was
        # PATCHing the live instance with an empty body. Two tests caught it. ServiceNow returns the
        # updated record on the resolve call anyway, and its SLA fields ride along on that, so this
        # is both honest and one fewer round trip.
        r.traces.append(self._sn_step(ctx, A["resolver"], "servicenow.resolve_incident",
                                      sys_id, resolve_patch, eid,
                                      # The contract reads close_code, so the tool that
                                      # produced it has to carry that exact name or
                                      # attribution cannot find the source.
                                      extra_output={"close_code": d["close_code"], **sla}))

        # 4. Closure check: the reviewer commits to the forecast set.
        forecasts = {
            "predicted_category": d["category"],
            "predicted_priority": d["predicted_priority"],
            "recommended_group": d["recommended_group"],
            "predicted_close_code": d["close_code"],
            "predicted_sla_result": d["predicted_sla_result"],
            "predicted_reopen_risk": d["reopen_risk"],
            "agent_confidence": d["confidence"],
        }
        r.traces.append(self.agent_step(
            ctx, A["reviewer"], item,
            decision=(f"resolution accepted; expects SLA {d['predicted_sla_result']}, "
                      f"reopen risk {d['reopen_risk']}"),
            entity_id=eid, payload_extra=dict(forecasts)))

        # The Estimated side of the contract: what the agent claims, in the
        # contract's own signal names, so Estimated and Real grade side by side.
        r.estimated_signals = {
            "first_response_time_met": d["predicted_sla_result"] == "met",
            # The agent commits to finishing inside the resolution target too. Claiming it up front
            # is the point: the Estimated side is what it BELIEVED, and the gap to the settled
            # result is what the fleet is graded on.
            "resolution_time_met": d["predicted_sla_result"] == "met",
            "reopen_count": 0 if d["reopen_risk"] == "low" else 1,
            "close_code": d["close_code"],
            # The agent routed it once and expects that to be the end of it.
            "reassignment_count": 1,
        }
        r.metadata = {
            "forecasts": forecasts,
            "estimated_success": all([
                r.estimated_signals["first_response_time_met"],
                r.estimated_signals["resolution_time_met"],
                r.estimated_signals["reopen_count"] == 0,
                d["close_code"] in GENUINE_FIX_CODES,
            ]),
            "system_of_record": "servicenow",
            "sn_sys_id": sys_id,
            # Said out loud on every run: this fleet's outcome is not ours to report.
            "outcome_source": "servicenow_push",
        }

        r.evals = [
            self.eval_pass("triage", "classification_confidence", eid,
                           f"classified as {d['category']} from the incident text",
                           score=0.9 if d["unambiguous"] else 0.72),
            self.eval_pass("router", "routing_confidence", eid,
                           f"{d['recommended_group']} owns {d['category']} incidents", score=0.88),
            self.eval_pass("resolver", "resolution_completeness", eid,
                           f"resolution recorded with close code '{d['close_code']}'",
                           score=0.9 if d["approach"] in ("root_cause_fix", "change_applied") else 0.74),
            self.eval_pass("reviewer", "closure_check", eid,
                           "resolution and close notes are present and consistent", score=0.91),
        ]
        # Stamp the Estimated signals onto the reviewer's closing message, exactly as
        # BasePack.run_pipeline does for every other pack.
        #
        # This fleet overrides run_pipeline because it works real tickets in a real instance, and
        # the override silently dropped this step. The consequence was invisible for weeks and only
        # showed up in Provy: the contract grades `first_response_time_met` and `resolution_time_met`
        # and NOTHING in the traces carried either name, so a failed run could never be traced back
        # to the agent that caused it. Provy read 0 of 6 conditions able to name a cause on the one
        # fleet used for demos (argus#446).
        self.stamp_estimated(r, self.lever_manifest().reviewer_agent, d["confidence"])

        r.terminal_reason = "resolved"
        return r

    # ── helpers ─────────────────────────────────────────────────────────────
    def _sn_step(self, ctx: RunContext, agent: AgentSpec, tool: str, sys_id: str,
                 patch: dict, entity_id: str, extra_output: Optional[dict] = None) -> TraceStep:
        """One real write to the instance, recorded as the tool call it is.

        A rejected write fails THIS ticket, not the batch. The instance enforces its
        own rules and will refuse things: on a 500-incident run, one refusal taking
        the whole run down would lose 499 good sessions. A refusal is also a real
        agent failure and belongs in the trace as one, so the step is recorded as an
        error and the run ends there rather than being quietly dropped.
        """
        try:
            updated = self.client.update("incident", sys_id, patch)
        except ServiceNowError as e:
            step = self.tool_step(ctx, agent, tool,
                                  tool_input={"sys_id": sys_id, **patch},
                                  tool_output={"rejected": True, "detail": str(e)[:400]},
                                  entity_id=entity_id)
            step.outcome = "error"
            step.error = str(e)[:400]
            raise WriteRejected(step) from None
        output = {
            "number": updated.get("number", entity_id),
            "state": updated.get("state"),
            "sys_updated_on": updated.get("sys_updated_on"),
        }
        output.update(extra_output or {})
        return self.tool_step(ctx, agent, tool,
                              tool_input={"sys_id": sys_id, **patch},
                              tool_output=output, entity_id=entity_id)


# ServiceNow's stock priority matrix (impact x urgency -> priority).
_PRIORITY_MATRIX = {
    ("1", "1"): "1", ("1", "2"): "2", ("1", "3"): "3",
    ("2", "1"): "2", ("2", "2"): "3", ("2", "3"): "4",
    ("3", "1"): "3", ("3", "2"): "4", ("3", "3"): "5",
}

_CLOSE_NOTES = {
    "root_cause_fix": "Identified the underlying fault and corrected it. Verified with the caller's affected function.",
    "change_applied": "Raised and applied a standard change to correct the fault.",
    "known_problem": "Matched to an existing known problem record and applied the documented resolution.",
    "workaround": "Applied a temporary workaround so the caller can continue. Underlying fault not corrected.",
    "advised_caller": "Advised the caller on the correct procedure. No change made to the environment.",
    "no_fault_found": "Could not reproduce the reported behaviour. No fault found at this time.",
}
