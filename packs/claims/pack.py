"""Domain pack 2 — insurance claims adjudication.

One claim = one work item (entity_id = claim_id) = one Provy session.
Agents: Intake -> Validator -> Adjudicator -> Reviewer.
Ground truth: whether the claim is valid, the correct decision, whether it is
within the policy limit, whether it is a duplicate. Every contract condition is
signal-mapped so all five grade deterministically.
"""
from __future__ import annotations

from engine.pack import BasePack
from engine.types import AgentSpec, Criterion, LeverManifest, RunContext, RunResult

CLAIM_TYPES = ["auto_collision", "property_damage", "medical", "theft", "liability"]
DOC_SETS = {
    "auto_collision": ["police_report", "photos", "estimate"],
    "property_damage": ["photos", "estimate", "proof_of_ownership"],
    "medical": ["itemized_bill", "diagnosis"],
    "theft": ["police_report", "proof_of_ownership"],
    "liability": ["incident_report", "witness_statement"],
}


class ClaimsPack(BasePack):
    workflow = "claims"
    session_type = "claim"

    def agents(self) -> list[AgentSpec]:
        return [
            AgentSpec("intake", "Intake", "reads the claim, extracts fields", "📥", 0),
            AgentSpec("validator", "Validator", "checks required documents and policy status", "📋", 1),
            AgentSpec("adjudicator", "Adjudicator", "decides approve or deny and the payout", "⚖️", 2),
            AgentSpec("reviewer", "Reviewer", "checks the decision against policy limits", "✅", 3),
        ]

    def contract(self) -> list[Criterion]:
        return [
            Criterion("c1", "Correct decision vs ground truth", "both", "decision_correct", "eq", True),
            Criterion("c2", "Required documents present", "trace", "docs_present", "eq", True),
            Criterion("c3", "Within policy limit", "outcome", "within_limit", "eq", True),
            Criterion("c4", "No duplicate payout", "outcome", "duplicate_payout", "eq", False),
            Criterion("c5", "Within SLA", "outcome", "sla_met", "eq", True),
        ]

    def lever_manifest(self) -> LeverManifest:
        return LeverManifest(
            resolver_agent="adjudicator",
            retriever_agent="validator",
            reviewer_agent="reviewer",
            first_agent="intake",
            downstream_agent="adjudicator",
            correctness_signal="decision_correct",
            policy_signal="within_limit",
            sla_signal="sla_met",
            secondary_bad_signal="duplicate_payout",
            drift_agent="adjudicator",
        )

    def generate_work_item(self, rng) -> tuple[dict, dict]:
        n = rng.randint(100000, 999999)
        claim_type = rng.choice(CLAIM_TYPES)
        amount = rng.randint(500, 40000)
        policy_limit = rng.choice([10000, 25000, 50000])
        within_limit = amount <= policy_limit
        docs_required = DOC_SETS[claim_type]
        docs_complete = rng.random() < 0.85
        is_duplicate = False   # objective: a legitimate, first-time claim in the clean baseline
        valid = docs_complete and within_limit and not is_duplicate
        correct_decision = "approve" if valid else "deny"

        item = {
            "id": f"CLM-{n}",
            "claim_type": claim_type,
            "amount": amount,
            "policy_limit": policy_limit,
            "docs_submitted": docs_required if docs_complete else docs_required[:-1],
            "text": f"{claim_type} claim for ${amount} against a ${policy_limit} limit policy.",
        }
        ground_truth = {
            "valid": valid,
            "correct_decision": correct_decision,
            "within_limit": within_limit,
            "docs_complete": docs_complete,
            "is_duplicate": is_duplicate,
        }
        return item, ground_truth

    def build_clean_run(self, item: dict, gt: dict, ctx: RunContext) -> RunResult:
        r = self.base_result(item)
        eid = r.entity_id
        A = {a.name: a for a in self.agents()}

        r.traces.append(self.agent_step(
            ctx, A["intake"], item, decision=f"extracted claim_type={item['claim_type']}, amount={item['amount']}",
            entity_id=eid))
        r.traces.append(self.tool_step(
            ctx, A["validator"], "policy_lookup",
            tool_input={"claim_type": item["claim_type"]},
            tool_output={"limit": item["policy_limit"], "docs_required": DOC_SETS[item["claim_type"]],
                         "as_of": ctx.now.date().isoformat()},
            entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["validator"], item, decision="documents complete; policy active", entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["adjudicator"], item, decision=f"decision={gt['correct_decision']}", entity_id=eid,
            payload_extra={"decision": gt["correct_decision"], "confidence": "HIGH"}))
        r.traces.append(self.agent_step(
            ctx, A["reviewer"], item, decision="approved: within limit, no duplicate", entity_id=eid))

        r.evals = [
            self.eval_pass("intake", "extraction_accuracy", eid, "all claim fields extracted correctly"),
            self.eval_pass("validator", "validation_completeness", eid, "checked every required document"),
            self.eval_pass("adjudicator", "decision_soundness", eid, "decision follows policy and evidence"),
            self.eval_pass("reviewer", "limit_compliance", eid, "payout is within the policy limit"),
        ]
        r.terminal_reason = "adjudicated"
        return r
