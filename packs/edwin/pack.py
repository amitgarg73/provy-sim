"""Domain pack 10 - agentic AIOps, shaped after LogicMonitor's Edwin AI.

One AI Investigation on one insight = one work item (entity_id = insight id) = one Provy session.

Why this shape. Edwin's chat surface (an agent homepage, plus context panes on an insight) is
prompted by a human, so it has no unprompted verdict to reconcile. Its AI Investigation does: Edwin
fires one automatically on every critical or major insight and emits a claim (a title, a summary, a
root cause, an impact, a timeline, and suggested remediation). That is one claim per insight, which
is exactly the grain the engine already has.

The gap this pack demonstrates is the vendor's own. Their root cause is labelled "most probable
cause", their documentation tells the operator to reference change requests "to confirm whether that
root cause is accurate", and their agent guidance says "always verify results before taking action".
Nothing in the product closes that loop. The incident record does, days later, and reconciling the
two is the thing Provy is for.

Agents mirror the real orchestrator-plus-specialists architecture: an orchestrator picks which
specialists to run, rather than a fixed pipeline.

⛔ THE OPERATOR CHAT TURNS ARE NOT DECORATION. `insights_agent` runs MANY TIMES in one session,
which no other pack does (all nine others run each agent exactly once). It is deliberately the
weakest form of the chatbot question: the conversation sits INSIDE one work item, so the session
grain is untouched. A real conversational pack, where one session holds SEVERAL work items, needs a
change in engine/ and is not this. See reference_provy_sim_pack_anatomy.
"""
from __future__ import annotations

from engine import levers as L
from engine.pack import BasePack
from engine.types import (AgentSpec, Criterion, InjectedFault, LeverManifest,
                          RunContext, RunResult)

# The fault library. Each entry is (true cause, the group that owns the fix, whether a change
# request caused it). The change-caused half is what makes the change_agent load-bearing: skip its
# lookup on one of those and the root cause named is confidently wrong.
FAULTS = {
    "bgp_session_flap":   ("link flap on the core uplink", "Network", False),
    "disk_exhaustion":    ("log rotation stopped after the 4.2 deploy", "Unix", True),
    "memory_leak":        ("memory leak shipped in payments release 8.1", "App Support", True),
    "cert_expiry":        ("TLS certificate expired on the edge proxy", "Security", False),
    "db_pool_exhausted":  ("connection pool drained by a runaway report", "DBA", False),
    "pod_eviction":       ("node memory pressure evicted the pods", "Platform", True),
    "dns_resolution":     ("a bad zone push poisoned the resolver cache", "Network", True),
    "storage_latency":    ("array controller failover degraded IO", "Storage", False),
}

SITES = ["us-east-dc1", "us-west-dc2", "emea-lon3", "apac-sg1"]
SERVICES = ["payments-api", "checkout-web", "ledger-core", "identity-idp", "reporting-batch"]

# What an operator actually asks the chat agent after reading an RCA, and the shape of the answer.
# The last one is the circle-back: a human explicitly questioning the finding, which is the exchange
# worth looking at once the incident settles the other way.
CHAT_TURNS = [
    ("What else is impacted by this?", "listed the downstream services on the same CI"),
    ("Show me similar incidents.", "surfaced past incidents matching this alert signature"),
    ("Has anything changed on this CI recently?", "answered from the change context in the investigation"),
    ("Are you sure that is the cause?", "restated the root cause and its confidence"),
]


class EdwinPack(BasePack):
    workflow = "edwin"
    session_type = "investigation"

    def agents(self) -> list[AgentSpec]:
        return [
            AgentSpec("orchestrator", "Orchestrator",
                      "decides which specialist agents this insight needs, and who to route it to",
                      "🧭", 0),
            AgentSpec("correlator", "Correlator",
                      "folds the raw alerts into a single insight", "🧩", 1),
            AgentSpec("metrics_agent", "Metrics Agent",
                      "pulls metric context for the impacted configuration items", "📈", 2),
            AgentSpec("change_agent", "Change Request Agent",
                      "looks for recent changes on the impacted configuration items", "🔧", 3),
            AgentSpec("knowledge_agent", "Knowledge Agent",
                      "searches vendor documentation and the internal runbooks", "📚", 4),
            AgentSpec("rca_agent", "IT Ops Agent",
                      "names the probable root cause and drafts the remediation", "🧠", 5),
            AgentSpec("reviewer", "Reviewer",
                      "writes the operator-facing summary and states confidence", "✅", 6),
            AgentSpec("insights_agent", "Insights Agent",
                      "answers the operator's follow-up questions about the insight", "💬", 7),
        ]

    def contract(self) -> list[Criterion]:
        """Six conditions. c1 is the Estimated-vs-Real pair and the whole point: Edwin says what
        caused it, the incident record says what actually did.

        c4 is the quiet one. An investigation that never read the change data looks identical on
        screen to one that did, and it is the vendor's own stated way of confirming an RCA."""
        return [
            Criterion("c1", "Named the actual root cause", "both", "rca_correct", "eq", True),
            Criterion("c2", "No repeat incident within 7 days", "outcome", "reopened_7d", "eq", False),
            Criterion("c3", "Routed to the group that owns the fix", "outcome", "routed_correct", "eq", True),
            Criterion("c4", "Investigation included the change context", "trace", "change_data_used", "eq", True),
            Criterion("c5", "Correlated into one incident, not many", "outcome", "correlation_held", "eq", True),
            Criterion("c6", "Investigation delivered before escalation", "outcome", "sla_met", "eq", True),
        ]

    def failure_cost(self) -> dict:
        """Per-occurrence cost, illustrative and deliberately conservative.

        ⛔ NOT MTTR. Minutes saved is the number this vendor sells on and the number a wrong-but-fast
        RCA games hardest, so pricing the demo on it would reward the failure being measured. These
        price the rework instead: an engineer sent down the wrong path, a second incident opened for
        one fault, a fix handed to a team that does not own it."""
        return {
            "silent_wrong": 4200.0,        # a whole shift spent on the wrong cause
            "silent_unsupported": 4200.0,
            "silent_staleness": 3100.0,
            "change_blind": 4800.0,        # the change that caused it sat unread in the record
            "correlation_split": 900.0,    # a duplicate incident worked twice
            "policy_violation": 1500.0,    # wrong assignment group, one full bounce
            "overt_error": 600.0,
            "tool_fault": 600.0,
        }

    def trace_aliases(self) -> dict[str, str]:
        """The fleet's own vocabulary. A LogicMonitor shop instrumented its agents in LogicMonitor's
        words long before anyone wrote an outcome contract, so the trace carries their names and the
        contract carries ours. Only the trace side is renamed."""
        return {
            "rca_correct": "root_cause_confirmed",
            "correlation_held": "insight_correlation_held",
            "routed_correct": "assignment_group_correct",
            "change_data_used": "change_context_included",
            "sla_met": "investigation_within_target",
            "reopened_7d": "incident_recurred_7d",
        }

    def signal_owners(self) -> dict[str, str]:
        """Which agent's work decides each signal, and therefore who a failure is attributed to.

        ⛔ WITHOUT THIS EVERY CONDITION BLAMES THE REVIEWER. Measured on this fleet before the map
        existed: all six signals registered against `reviewer`, because the shared helper stamped
        them all on its closing message. c4 exists to say the change agent never read the change
        record, and bound that way it would have named the agent that only summarised it.

        `sla_met` is deliberately unowned. Timeliness is a property of the whole investigation
        rather than of any one step, so it falls to the reviewer, which is the honest answer."""
        return {
            "rca_correct": "rca_agent",
            "reopened_7d": "rca_agent",          # a wrong cause is why it comes back
            "change_data_used": "change_agent",
            "correlation_held": "correlator",
            "routed_correct": "orchestrator",    # the orchestrator picks the assignment group
            "alerts_folded": "correlator",       # informational, but it is the correlator's number
        }

    def lever_manifest(self) -> LeverManifest:
        return LeverManifest(
            resolver_agent="rca_agent",          # the agent that names the cause
            retriever_agent="knowledge_agent",   # tool user: vendor docs and runbooks
            reviewer_agent="reviewer",
            first_agent="correlator",
            downstream_agent="rca_agent",
            correctness_signal="rca_correct",
            policy_signal="routed_correct",
            sla_signal="sla_met",
            secondary_bad_signal="reopened_7d",  # a wrong cause means it comes back
            drift_agent="rca_agent",
            policy_agent="orchestrator",         # the orchestrator picks the assignment group
        )

    # ── generator with ground truth ──────────────────────────────────────────
    def generate_work_item(self, rng) -> tuple[dict, dict]:
        n = rng.randint(100000, 999999)
        fault = rng.choice(list(FAULTS))
        true_cause, correct_group, change_caused = FAULTS[fault]
        severity = rng.choice(["critical", "major"])   # Edwin auto-investigates exactly these two
        ci = f"{rng.choice(SERVICES)}-{rng.randint(1, 9):02d}"
        # The alert count is the noise the correlator folded away. It is the vendor's headline claim,
        # so the pack states it per run rather than asserting a blanket reduction percentage.
        alerts_folded = rng.randint(4, 60)

        item = {
            "id": f"INS-{n}",
            "fault": fault,
            "severity": severity,
            "ci": ci,
            "site": rng.choice(SITES),
            "alerts_folded": alerts_folded,
            "text": (f"{severity} insight on {ci}: {fault.replace('_', ' ')} "
                     f"correlated from {alerts_folded} alerts."),
        }
        ground_truth = {
            "true_cause": true_cause,
            "correct_group": correct_group,
            "change_caused": change_caused,
            "fault": fault,
            "alerts_folded": alerts_folded,
        }
        return item, ground_truth

    # ── clean baseline ───────────────────────────────────────────────────────
    def build_clean_run(self, item: dict, gt: dict, ctx: RunContext) -> RunResult:
        r = self.base_result(item)
        eid = r.entity_id
        A = {a.name: a for a in self.agents()}
        # Informational only, not in the contract. The vendor sells on noise reduction, so the number
        # is on the run where anyone can check it against the incident record.
        r.real_signals["alerts_folded"] = gt["alerts_folded"]
        r.estimated_signals["alerts_folded"] = gt["alerts_folded"]

        r.traces.append(self.agent_step(
            ctx, A["orchestrator"], item,
            decision=(f"{item['severity']} insight, so run the full investigation: metrics, change, "
                      f"knowledge, then root cause."),
            entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["correlator"], item,
            decision=f"folded {gt['alerts_folded']} alerts on {item['ci']} into one insight",
            entity_id=eid, payload_extra={"alerts_folded": gt["alerts_folded"]}))

        r.traces.append(self.tool_step(
            ctx, A["metrics_agent"], "metric_query",
            tool_input={"ci": item["ci"], "window": "60m"},
            tool_output={"cpu_pct": 91, "mem_pct": 88, "as_of": ctx.now.isoformat()},
            entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["metrics_agent"], item,
            decision="metrics confirm the resource pressure at the reported time", entity_id=eid))

        # The change lookup. On a change-caused fault this call is what makes the RCA correct, which
        # is why skipping it is its own failure mode rather than a variant of a bad retrieval.
        change_hit = ({"change_id": f"CHG{ctx.rng.randint(10000, 99999)}",
                       "closed_at": ctx.now.date().isoformat(), "ci": item["ci"]}
                      if gt["change_caused"] else {"changes": []})
        r.traces.append(self.tool_step(
            ctx, A["change_agent"], "change_lookup",
            tool_input={"ci": item["ci"], "window_days": 7},
            tool_output=change_hit, entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["change_agent"], item,
            decision=("a recent change on this CI overlaps the alert window"
                      if gt["change_caused"] else "no recent change on this CI"),
            entity_id=eid, payload_extra={"change_context_included": True}))

        r.traces.append(self.tool_step(
            ctx, A["knowledge_agent"], "kb_search",
            tool_input={"query": gt["fault"], "sources": ["vendor_docs", "internal_runbooks"]},
            tool_output={"article_id": "KB-" + gt["fault"][:6].upper(), "match_score": 0.91,
                         "as_of": ctx.now.date().isoformat()},
            entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["knowledge_agent"], item,
            decision="pulled the runbook and the vendor note for this signature", entity_id=eid))

        r.traces.append(self.agent_step(
            ctx, A["rca_agent"], item,
            decision=(f"Root cause: {gt['true_cause']}. Immediate fix drafted; "
                      f"route to {gt['correct_group']}."),
            entity_id=eid,
            payload_extra={"root_cause": gt["true_cause"],
                           "assignment_group": gt["correct_group"],
                           "change_correlated": gt["change_caused"],
                           "confidence": "HIGH"}))
        r.traces.append(self.agent_step(
            ctx, A["reviewer"], item,
            decision="investigation complete: cause, impact and remediation are consistent",
            entity_id=eid))

        # ── the operator conversation, inside this one work item ──────────────
        # One agent, several turns. The engine has never emitted this before, and it is the point of
        # the chat half: the follow-ups are on the same session as the claim, so when the incident
        # settles the other way, the record shows a human asked and shipped it anyway.
        turns = ctx.rng.randint(2, 4)
        for i, (question, answer) in enumerate(CHAT_TURNS[:turns], start=1):
            step = self.agent_step(
                ctx, A["insights_agent"], item,
                decision=answer, entity_id=eid, tokens=(220, 90),
                payload_extra={"chat_turn": i, "operator_question": question})
            step.user = f"Operator asks about {eid}: {question}"
            r.traces.append(step)

        r.evals = [
            self.eval_pass("correlator", "correlation_precision", eid,
                           "every folded alert belongs to this fault"),
            self.eval_pass("change_agent", "change_context_recall", eid,
                           "change window searched and the result carried into the analysis"),
            self.eval_pass("knowledge_agent", "retrieval_relevance", eid,
                           "runbook and vendor note match the alert signature"),
            self.eval_pass("rca_agent", "root_cause_plausibility", eid,
                           "named cause is consistent with metrics, change and knowledge"),
            self.eval_pass("reviewer", "summary_faithfulness", eid,
                           "summary states only what the investigation supports"),
            self.eval_pass("insights_agent", "answer_groundedness", eid,
                           "follow-up answers cite the investigation, not new claims"),
        ]
        r.terminal_reason = "investigation_complete"
        return r

    # ── Edwin's own two failure modes ────────────────────────────────────────
    def run_pipeline(self, item: dict, gt: dict, ctx: RunContext) -> RunResult:
        """The generic levers plus two failures specific to this product's shape.

        Both defer to the generic set through the same one-primary-cause-per-run exclusion, so
        attribution stays 1:1 and a run is either clean or has exactly one cause."""
        r = self.build_clean_run(item, gt, ctx)
        m = self.lever_manifest()

        def _edwin_injector(result: RunResult, _ctx: RunContext,
                            primary_fired: bool) -> InjectedFault | None:
            if primary_fired:
                return None
            cfg = _ctx.levers

            # change_blind: the change lookup came back empty on a fault a change actually caused,
            # and the RCA was written anyway. The investigation reads as complete, the named cause is
            # wrong, and the incident comes back. This is the vendor's own documented confirmation
            # step going unread, so it is the failure their product cannot see by construction.
            s = cfg.get("change_blind")
            if s and gt["change_caused"] and _ctx.rng.random() < s.rate:
                step = L._find_step(result, "change_agent", "tool_call")
                if step is not None:
                    step.tool_output = {"changes": []}
                    step.outcome = "ok"
                msg = L._agent_message(result, "change_agent")
                if msg is not None:
                    # The fleet's own field name, not the contract's. Same rule as trace_aliases:
                    # this trace is what the agent really logged, and it never saw our vocabulary.
                    msg.payload_extra["change_context_included"] = False
                    msg.outcome = "no recent change on this CI"
                # Trace side admits the gap; the RCA and the reopen are what reality disagrees on.
                result.estimated_signals["change_data_used"] = False
                result.real_signals["rca_correct"] = False
                result.real_signals["reopened_7d"] = True
                rca = L._agent_message(result, "rca_agent")
                if rca is not None:
                    rca.payload_extra["confidence"] = "HIGH"
                result.confidence = max(result.confidence, 0.9)
                result.metadata["change_blind"] = True
                return InjectedFault("change_blind", "change_agent", "silent_divergence",
                                     {"signals": ["rca_correct", "reopened_7d"],
                                      "change_caused": True})

            # correlation_split: the same fault raised two insights, so two incidents were opened and
            # worked. The claim the vendor leads with is noise reduction, and this is that claim
            # failing in the direction nobody checks.
            s = cfg.get("correlation_split")
            if s and _ctx.rng.random() < s.rate:
                result.real_signals["correlation_held"] = False
                msg = L._agent_message(result, "correlator")
                if msg is not None:
                    msg.payload_extra["insights_opened"] = 2
                result.metadata["correlation_split"] = True
                return InjectedFault("correlation_split", "correlator", "correlation",
                                     {"signals": ["correlation_held"], "insights_opened": 2})
            return None

        L.apply(r, gt, m, self.contract(), ctx.levers, ctx, pack_injector=_edwin_injector)
        self.stamp_estimated(r, m.reviewer_agent)
        return r
