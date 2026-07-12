"""Domain pack 1 — customer support resolution (the fully worked example).

One ticket = one work item (entity_id = ticket_id) = one Provy session.
Agents: Classifier -> Retriever -> Resolver -> Reviewer.
The generator produces a ticket AND its ground truth (correct category, whether
policy allows the action, the correct resolution). Every contract condition maps
to a scalar signal the run emits, so all five grade deterministically.
"""
from __future__ import annotations

from engine.pack import BasePack
from engine.types import AgentSpec, Criterion, LeverManifest, RunContext, RunResult

CATEGORIES = ["billing", "account_access", "technical", "refund_request", "complaint"]
PRIORITIES = ["low", "medium", "high", "urgent"]

# category -> the action the customer is asking for, and the resolution that grants it
_ACTION = {
    "billing":        ("adjust_charge", "issue_credit"),
    "account_access": ("reset_access",  "reset_credentials"),
    "technical":      ("fix_defect",    "apply_fix"),
    "refund_request": ("refund",        "issue_refund"),
    "complaint":      ("escalate",      "escalate_to_manager"),
}


class SupportPack(BasePack):
    workflow = "support"
    session_type = "ticket"

    def agents(self) -> list[AgentSpec]:
        return [
            AgentSpec("classifier", "Classifier", "reads the ticket, sets category and priority", "🗂️", 0),
            AgentSpec("retriever", "Retriever", "pulls the policy article and account context", "🔎", 1),
            AgentSpec("resolver", "Resolver", "decides the resolution and drafts the reply", "🛠️", 2),
            AgentSpec("reviewer", "Reviewer", "checks the draft against policy before it sends", "✅", 3),
        ]

    def contract(self) -> list[Criterion]:
        return [
            Criterion("c1", "Resolved without escalation", "outcome", "escalated", "eq", False),
            Criterion("c2", "Policy followed", "both", "policy_followed", "eq", True),
            Criterion("c3", "Resolved within SLA", "outcome", "sla_met", "eq", True),
            Criterion("c4", "No reopen within 7 days", "outcome", "reopened_7d", "eq", False),
            Criterion("c5", "Correct category", "both", "category_correct", "eq", True),
        ]

    # No dollar figures for support: the contract's conditions are pass/fail with no monetary value,
    # so any $ would be a fabricated assumption. The scoreboard shows no value band for this fleet.
    def failure_cost(self) -> dict:
        return {}

    def lever_manifest(self) -> LeverManifest:
        return LeverManifest(
            resolver_agent="resolver",
            retriever_agent="retriever",
            reviewer_agent="reviewer",
            first_agent="classifier",
            downstream_agent="resolver",
            correctness_signal="reopened_7d",     # a silently-wrong resolution comes back
            policy_signal="policy_followed",
            sla_signal="sla_met",
            secondary_bad_signal="policy_followed",  # and quietly broke policy
            drift_agent="resolver",
        )

    # ── generator with ground truth ──────────────────────────────────────────
    def generate_work_item(self, rng) -> tuple[dict, dict]:
        n = rng.randint(100000, 999999)
        category = rng.choice(CATEGORIES)
        priority = rng.choice(PRIORITIES)
        action, grant_resolution = _ACTION[category]
        # Objective policy check: is the requested action allowed?
        days_since_purchase = rng.randint(1, 120)
        account_tier = rng.choice(["free", "pro", "enterprise"])
        if category == "refund_request":
            policy_allows = days_since_purchase <= 30
        elif category == "complaint":
            policy_allows = False   # complaints route to a human by policy
        else:
            policy_allows = rng.random() < 0.8
        correct_resolution = grant_resolution if policy_allows else (
            "escalate_to_manager" if category == "complaint" else "deny_with_reason"
        )
        needs_human = category == "complaint" or (priority == "urgent" and not policy_allows)

        item = {
            "id": f"TKT-{n}",
            "category": category,
            "priority": priority,
            "requested_action": action,
            "account_tier": account_tier,
            "days_since_purchase": days_since_purchase,
            "text": f"Customer ({account_tier}) requests {action} — {category} issue, {priority} priority.",
        }
        ground_truth = {
            "category": category,
            "policy_allows": policy_allows,
            "correct_resolution": correct_resolution,
            "needs_human": needs_human,
        }
        return item, ground_truth

    # ── clean baseline ───────────────────────────────────────────────────────
    def build_clean_run(self, item: dict, gt: dict, ctx: RunContext) -> RunResult:
        r = self.base_result(item)
        eid = r.entity_id
        r.real_signals["csat"] = 5     # informational (not in contract)
        r.estimated_signals["csat"] = 5
        A = {a.name: a for a in self.agents()}

        r.traces.append(self.agent_step(
            ctx, A["classifier"], item,
            decision=f"category={gt['category']}, priority={item['priority']}", entity_id=eid))
        r.traces.append(self.tool_step(
            ctx, A["retriever"], "kb_lookup",
            tool_input={"query": item["requested_action"], "account": item["account_tier"]},
            tool_output={"article_id": "POL-" + gt["category"][:3].upper(),
                         "policy_allows": gt["policy_allows"],
                         "as_of": ctx.now.date().isoformat()},
            entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["retriever"], item, decision="retrieved policy + account context", entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["resolver"], item,
            decision=f"resolution={gt['correct_resolution']}", entity_id=eid,
            payload_extra={"resolution_code": gt["correct_resolution"], "confidence": "HIGH"}))
        r.traces.append(self.agent_step(
            ctx, A["reviewer"], item, decision="approved: within policy", entity_id=eid))

        r.evals = [
            self.eval_pass("classifier", "category_accuracy", eid,
                           "category matches the ticket content"),
            self.eval_pass("retriever", "retrieval_relevance", eid,
                           "pulled the correct policy article and account context"),
            self.eval_pass("resolver", "resolution_correctness", eid,
                           "resolution follows the retrieved policy"),
            self.eval_pass("reviewer", "policy_compliance", eid,
                           "draft obeys policy; safe to send"),
        ]
        r.terminal_reason = "resolved"
        return r
