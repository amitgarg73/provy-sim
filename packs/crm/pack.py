"""Domain pack 3 — CRM inbound lead qualification.

One lead = one work item (entity_id = lead_id) = one Provy session.
Agents: Enricher -> Scorer -> Router -> QA.
Ground truth: the true qualification (MQL/SQL/unqualified), the correct owner
(territory), and whether the contact already exists (duplicate). Every contract
condition is signal-mapped so all five grade deterministically.
"""
from __future__ import annotations

from engine.pack import BasePack
from engine.types import AgentSpec, Criterion, LeverManifest, RunContext, RunResult

SOURCES = ["webinar", "content_download", "demo_request", "cold_inbound", "referral"]
TERRITORIES = ["amer_east", "amer_west", "emea", "apac"]
SIZES = ["smb", "midmarket", "enterprise"]


class CRMPack(BasePack):
    workflow = "crm"
    session_type = "lead"

    def agents(self) -> list[AgentSpec]:
        return [
            AgentSpec("enricher", "Enricher", "fills in firmographics and contact data", "🧩", 0),
            AgentSpec("scorer", "Scorer", "qualifies the lead as MQL/SQL/unqualified", "🎯", 1),
            AgentSpec("router", "Router", "routes to the correct territory owner", "🧭", 2),
            AgentSpec("qa", "QA", "checks the routing and dedupe before handoff", "✅", 3),
        ]

    def contract(self) -> list[Criterion]:
        return [
            Criterion("c1", "Correct qualification vs truth", "both", "qualification_correct", "eq", True),
            Criterion("c2", "Routed to the correct owner", "outcome", "routed_correct", "eq", True),
            Criterion("c3", "Data enriched correctly", "trace", "enriched_correct", "eq", True),
            Criterion("c4", "No duplicate contact", "outcome", "duplicate_contact", "eq", False),
            Criterion("c5", "Followed up within SLA", "outcome", "sla_met", "eq", True),
        ]

    def lever_manifest(self) -> LeverManifest:
        return LeverManifest(
            resolver_agent="scorer",
            retriever_agent="enricher",
            reviewer_agent="qa",
            first_agent="enricher",
            downstream_agent="router",
            correctness_signal="qualification_correct",
            policy_signal="routed_correct",
            sla_signal="sla_met",
            secondary_bad_signal="duplicate_contact",
            drift_agent="scorer",
        )

    def generate_work_item(self, rng) -> tuple[dict, dict]:
        n = rng.randint(100000, 999999)
        source = rng.choice(SOURCES)
        size = rng.choice(SIZES)
        territory = rng.choice(TERRITORIES)
        # Objective qualification: intent + fit.
        intent = rng.random()
        if source in ("demo_request", "referral") and size in ("midmarket", "enterprise"):
            qualification = "SQL"
        elif intent > 0.5 or source == "webinar":
            qualification = "MQL"
        else:
            qualification = "unqualified"
        is_duplicate = False   # a fresh contact in the clean baseline

        item = {
            "id": f"LEAD-{n}",
            "source": source,
            "company_size": size,
            "territory": territory,
            "intent": round(intent, 2),
            "text": f"{size} lead from {source} in {territory}, intent {round(intent, 2)}.",
        }
        ground_truth = {
            "qualification": qualification,
            "correct_owner": territory,
            "is_duplicate": is_duplicate,
        }
        return item, ground_truth

    def build_clean_run(self, item: dict, gt: dict, ctx: RunContext) -> RunResult:
        r = self.base_result(item)
        eid = r.entity_id
        A = {a.name: a for a in self.agents()}

        r.traces.append(self.tool_step(
            ctx, A["enricher"], "firmographic_lookup",
            tool_input={"company_size": item["company_size"], "territory": item["territory"]},
            tool_output={"employees": {"smb": 40, "midmarket": 400, "enterprise": 4000}[item["company_size"]],
                         "as_of": ctx.now.date().isoformat()},
            entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["enricher"], item, decision="enriched firmographics + contact", entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["scorer"], item, decision=f"qualification={gt['qualification']}", entity_id=eid,
            payload_extra={"qualification": gt["qualification"], "confidence": "HIGH"}))
        r.traces.append(self.agent_step(
            ctx, A["router"], item, decision=f"routed to {gt['correct_owner']} owner", entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["qa"], item, decision="approved: correct owner, no duplicate", entity_id=eid))

        r.evals = [
            self.eval_pass("enricher", "enrichment_accuracy", eid, "firmographics filled correctly"),
            self.eval_pass("scorer", "qualification_accuracy", eid, "qualification matches intent and fit"),
            self.eval_pass("router", "routing_accuracy", eid, "routed to the correct territory owner"),
            self.eval_pass("qa", "handoff_quality", eid, "routing and dedupe are correct"),
        ]
        r.terminal_reason = "qualified"
        return r
