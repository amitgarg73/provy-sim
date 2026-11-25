"""Domain pack 2 — insurance claims adjudication.

One claim = one work item (entity_id = claim_id) = one Provy session.
Agents: Intake -> Validator -> Adjudicator -> Reviewer.
Ground truth: whether the claim is valid, the correct decision, whether it is
within the policy limit, whether it is a duplicate. Every contract condition is
signal-mapped so all five grade deterministically.
"""
from __future__ import annotations

import hashlib

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


# What each agent's model call normally reads and writes (tokens in, out). The adjudicator reads the
# most (claim, policy, history) and writes the reasoning, intake the least. Priced by the shared
# `llm_cost_usd`, so one session costs a fraction of a cent to about a cent, and the per-agent
# `cost_breakdown` the emitter writes at close reconciles to the session total.
_TOKEN_PROFILE = {
    "intake": (900, 220),
    "validator": (1500, 260),
    "adjudicator": (3200, 650),
    "reviewer": (1800, 300),
}


def _tokens(agent: str, entity_id: str) -> tuple[int, int]:
    """The agent's usual token use on this claim, varied by up to 25 percent either way. Derived from
    the claim id and agent (not the run's RNG), so a seed always gives the same figures and the extra
    variety does not shift any other draw the run makes."""
    base_in, base_out = _TOKEN_PROFILE[agent]
    h = int(hashlib.sha256(f"{entity_id}|{agent}".encode()).hexdigest()[:8], 16)
    f_in = 0.75 + (h % 1000) / 2000.0
    f_out = 0.75 + ((h // 1000) % 1000) / 2000.0
    return int(base_in * f_in), int(base_out * f_out)


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
            # 'both': the adjudicator claims the payout respects the limit AND reality confirms it.
            # This is the policy_signal, so an open policy break (policy_violation) fails the Estimated
            # side too and reads as a VISIBLE failure, while silent_policy corrupts only Real and diverges.
            Criterion("c3", "Within policy limit", "both", "within_limit", "eq", True),
            Criterion("c4", "No duplicate payout", "outcome", "duplicate_payout", "eq", False),
            Criterion("c5", "Within SLA", "outcome", "sla_met", "eq", True),
        ]

    # Per-occurrence dollar cost — claims carry real payout exposure, so the figures are larger.
    def failure_cost(self) -> dict:
        return {
            "silent_wrong": 4000, "silent_staleness": 3000, "silent_unsupported": 3500,
            "silent_incomplete": 5000, "silent_policy": 6000, "silent_missed_action": 4500,
            "tool_fault": 2000, "quality_degrade": 300, "policy_violation": 6000,
            "sla_breach": 150, "overt_error": 80, "skip_propagation": 200,
        }

    def trace_aliases(self) -> dict[str, str]:
        """Adjudication vocabulary predates the contract. `within_limit` genuinely matches.

        Anything not listed is emitted under the contract's own name. Only the trace side is
        renamed; grading and the outcome push keep the contract vocabulary."""
        return {
            "decision_correct": "adjudication_valid",
            "duplicate_payout": "dup_pay_flag",
            "sla_met": "cycle_time_ok",
        }

    def signal_owners(self) -> dict[str, str]:
        """Which agent's work decides each signal, and therefore who a failure is attributed to.

        ⛔ WITHOUT THIS EVERY CONDITION BLAMES THE REVIEWER. The shared helper stamps every
        contract signal on the reviewer's closing message unless a pack says otherwise, so the
        whole contract registers against one agent. Measured on the Servicely legal fleet before
        this map existed: all three trace-side signals carried `source_hint: reviewer`, and 13
        scenarios produced incidents on 2 of its 4 agents.

        The adjudicator decides the claim and the limit. Whether the documents were there at
        all is the validator's check.

        Timeliness signals are deliberately unowned: an SLA is a property of the whole run rather
        than of any one step, so it falls to the reviewer, which is the honest answer."""
        return {
            "decision_correct":  "adjudicator",
            "within_limit":      "adjudicator",
            "duplicate_payout":  "adjudicator",
            "docs_present":      "validator",
        }

    def lever_manifest(self) -> LeverManifest:
        return LeverManifest(
            resolver_agent="adjudicator",
            retriever_agent="validator",
            # The adjudicator and reviewer call tools too, so a fleet can show several tools in
            # different health (aim a lever at one with e.g. "tool_errors:check_payout_limits").
            extra_tools={"fetch_claim_history": "adjudicator", "check_payout_limits": "adjudicator",
                         "fetch_review_checklist": "reviewer"},
            reviewer_agent="reviewer",
            first_agent="intake",
            downstream_agent="adjudicator",
            correctness_signal="decision_correct",
            policy_signal="within_limit",
            sla_signal="sla_met",
            secondary_bad_signal="duplicate_payout",
            drift_agent="adjudicator",
        
            # Conditions with no other lever aimed at them, and the agent that owns each.
            # Without this they pass on every run and cannot be demonstrated.
            other_signals={
                "docs_present": "validator",
            },
        )

    def generate_work_item(self, rng) -> tuple[dict, dict]:
        n = rng.randint(100000, 999999)
        claim_type = rng.choice(CLAIM_TYPES)
        amount = rng.randint(500, 40000)
        policy_limit = rng.choice([10000, 25000, 50000])
        within_limit = amount <= policy_limit
        docs_required = DOC_SETS[claim_type]
        # The clean baseline is a genuinely complete, first-time claim: every required document is
        # present and it is not a duplicate. Those are the levers' job to break (silent_incomplete,
        # duplicate_payout via silent_wrong), not the baseline's — so docs_present and duplicate_payout
        # are honestly good here. Decision variety still comes from within_limit (over-limit -> deny).
        docs_complete = True
        is_duplicate = False   # objective: a legitimate, first-time claim in the clean baseline
        valid = docs_complete and within_limit and not is_duplicate
        correct_decision = "approve" if valid else "deny"

        item = {
            "id": f"CLM-{n}",
            "claim_type": claim_type,
            "amount": amount,
            "policy_limit": policy_limit,
            "docs_submitted": docs_required,
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
            entity_id=eid, tokens=_tokens("intake", eid)))
        r.traces.append(self.tool_step(
            ctx, A["validator"], "policy_lookup",
            tool_input={"claim_type": item["claim_type"]},
            tool_output={"limit": item["policy_limit"], "docs_required": DOC_SETS[item["claim_type"]],
                         "as_of": ctx.now.date().isoformat()},
            entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["validator"], item,
            decision=(f"all {len(DOC_SETS[item['claim_type']])} required documents present; "
                      f"policy active"),
            entity_id=eid, tokens=_tokens("validator", eid)))
        r.traces.append(self.tool_step(
            ctx, A["adjudicator"], "fetch_claim_history",
            tool_input={"claim_id": eid},
            tool_output={"prior_claims": 0, "duplicate_found": False}, entity_id=eid))
        r.traces.append(self.tool_step(
            ctx, A["adjudicator"], "check_payout_limits",
            tool_input={"claim_type": item["claim_type"], "amount": item["amount"]},
            tool_output={"limit": item["policy_limit"], "within_limit": gt["within_limit"]},
            entity_id=eid))
        # Cite the FACT as amount_within_limit (like support cites 'allows='), distinct from the
        # within_limit contract signal, so the trace never collides with the Estimated signal value.
        r.traces.append(self.agent_step(
            ctx, A["adjudicator"], item,
            decision=(f"Checked ${item['amount']} against the ${item['policy_limit']} limit "
                      f"(amount_within_limit={gt['within_limit']}) with {len(item['docs_submitted'])} of "
                      f"{len(DOC_SETS[item['claim_type']])} required documents present; "
                      f"therefore decision={gt['correct_decision']}."),
            entity_id=eid, tokens=_tokens("adjudicator", eid),
            payload_extra={"decision": gt["correct_decision"], "policy_limit": item["policy_limit"],
                           "amount": item["amount"], "amount_within_limit": gt["within_limit"],
                           "confidence": "HIGH"}))
        r.traces.append(self.tool_step(
            ctx, A["reviewer"], "fetch_review_checklist",
            tool_input={"claim_type": item["claim_type"]},
            tool_output={"checks": ["limit_respected", "no_duplicate", "documents_complete"]},
            entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["reviewer"], item, decision="approved: within policy limit, no duplicate", entity_id=eid,
            tokens=_tokens("reviewer", eid)))

        r.evals = [
            self.eval_pass("intake", "extraction_accuracy", eid, "all claim fields extracted correctly"),
            self.eval_pass("validator", "validation_completeness", eid, "checked every required document"),
            self.eval_pass("adjudicator", "decision_soundness", eid, "decision follows policy and evidence"),
            self.eval_pass("reviewer", "limit_compliance", eid, "payout is within the policy limit"),
        ]
        r.terminal_reason = "adjudicated"
        return r
