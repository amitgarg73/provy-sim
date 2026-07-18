"""Domain pack — Claims payout (commitment integrity).

An insurance-claims agent approves a claim and triggers the payout, then reports
"approved and paid $X." The disbursement call returns OK, every eval passes, and the
trace looks clean. Later the payment ledger reads what actually settled: the
disbursement never posted, the statutory prompt-pay clock lapsed, a different amount
posted (claims leakage), it paid a stale lienholder, or it cleared twice on an
un-voided reissue. That gap is the divergence Provy catches by reconciling the stated
decision against the settled ledger.

The failure EMERGES from the mock system of record; the adjudicator made the
commitment, so the adjudicator is the culprit. Superset pack: it also runs the full
generic + L1/L2 lever set. Distinct from the generic `claims` pack, which asks "was
the decision correct?" — this one asks "did the money actually move as promised?"
"""
from __future__ import annotations

from engine.commitment import CommitmentPack, Injector
from engine.types import AgentSpec, Criterion, LeverManifest, RunContext, RunResult


class ClaimsPayoutPack(CommitmentPack):
    workflow = "claims_payout"
    session_type = "claim"

    def agents(self) -> list[AgentSpec]:
        return [
            AgentSpec("intake", "Claim Intake",
                      "Reads the claim and pins down the claimant, the coverage, and the amount.", "📥", 0),
            AgentSpec("verifier", "Coverage Check",
                      "Confirms the policy is in force and the claim is covered before any payout.", "🔎", 1),
            AgentSpec("adjudicator", "Adjudicate & Pay",
                      "Approves the claim, triggers the disbursement, then tells the claimant it is paid.", "💵", 2),
            AgentSpec("reviewer", "Payment Review",
                      "Reviews the payout against policy limits before the claimant is notified.", "✅", 3),
        ]

    def contract(self) -> list[Criterion]:
        return [
            Criterion("c1", "The approved payout actually disbursed", "both", "payout_settled", "eq", True),
            Criterion("c2", "Paid the approved amount", "outcome", "amount_correct", "eq", True),
            Criterion("c3", "No duplicate payout", "outcome", "no_duplicate_payout", "eq", True),
            Criterion("c4", "Paid within the prompt-pay deadline", "outcome", "sla_met", "eq", True),
            Criterion("c5", "Paid the correct payee", "both", "correct_payee", "eq", True),
        ]

    def failure_cost(self) -> dict:
        return {"commitment_unsettled": 1200.0, "commitment_wrong_amount": 800.0,
                "commitment_wrong_target": 1500.0, "commitment_duplicate": 2000.0}

    def lever_manifest(self) -> LeverManifest:
        return LeverManifest(
            resolver_agent="adjudicator", retriever_agent="verifier", reviewer_agent="reviewer",
            first_agent="intake", downstream_agent="adjudicator",
            correctness_signal="payout_settled", policy_signal="payout_settled", sla_signal="sla_met",
            drift_agent="adjudicator",
        )

    def injectors(self) -> list[Injector]:
        return [
            Injector("not_disbursed", "unsettled", "disbursement_never_posted",
                     "the approved payout never posted to the ledger, so the claimant was not paid"),
            Injector("prompt_pay_lapsed", "unsettled", "prompt_pay_clock_lapsed",
                     "the statutory prompt-pay clock lapsed before the payout settled"),
            Injector("claims_leakage", "wrong_amount", "amount_mismatch",
                     "the amount that settled is not the approved amount (claims leakage)"),
            Injector("stale_lienholder", "wrong_target", "paid_stale_payee",
                     "the payout went to a stale lienholder, not the correct payee"),
            Injector("duplicate_payment", "duplicate", "duplicate_disbursement",
                     "the payout cleared twice on an un-voided reissue, so it paid double"),
        ]

    def settle_map(self) -> dict:
        return {"promise": "payout_settled", "wrong_amount": "amount_correct",
                "wrong_target": "correct_payee", "duplicate": "no_duplicate_payout"}

    def commit_amount(self, item) -> float:
        return float(item["amount"])

    def clean_narration(self, amount: float) -> str:
        return f"Ledger check: the disbursement posted ${amount:.2f} to the correct payee. Promise kept."

    def generate_work_item(self, rng) -> tuple[dict, dict]:
        n = rng.randint(100000, 999999)
        amount = rng.choice([850.00, 1500.00, 3200.00, 6400.00, 12000.00, 24500.00])
        item = {
            "id": f"CLM-{n}",
            "policy_id": f"POL-{rng.randint(10000, 99999)}",
            "coverage": "auto_collision",
            "amount": amount,
            "text": f"Approve and pay the ${amount:.2f} auto-collision claim.",
        }
        ground_truth = {"amount": amount, "covered": True}
        return item, ground_truth

    def build_clean_run(self, item: dict, gt: dict, ctx: RunContext) -> RunResult:
        r = self.base_result(item)
        eid = r.entity_id
        amt = item["amount"]
        A = {a.name: a for a in self.agents()}

        r.traces.append(self.agent_step(
            ctx, A["intake"], item,
            decision=f"Claim on {item['policy_id']} for ${amt:.2f}, {item['coverage']}.",
            entity_id=eid,
            payload_extra={"narration": f"Read the claim: ${amt:.2f} on {item['policy_id']}."}))

        r.traces.append(self.tool_step(
            ctx, A["verifier"], "policy.lookup",
            tool_input={"policy_id": item["policy_id"]},
            tool_output={"policy_id": item["policy_id"], "in_force": True, "covered": True,
                         "limit": amt * 3, "as_of": ctx.now.date().isoformat()},
            entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["verifier"], item,
            decision=f"Policy {item['policy_id']} is in force and the claim is covered.",
            entity_id=eid,
            payload_extra={"narration": f"Confirmed {item['policy_id']} is in force and covered."}))

        r.traces.append(self.tool_step(
            ctx, A["adjudicator"], "payments.disburse",
            tool_input={"policy_id": item["policy_id"], "amount": amt},
            tool_output={"ok": True, "payment_id": f"pay_{item['policy_id']}", "amount": amt},
            entity_id=eid))
        told = f"Your ${amt:.2f} claim is approved and paid."
        r.traces.append(self.agent_step(
            ctx, A["adjudicator"], item,
            decision=f"Approved and disbursed ${amt:.2f}; received OK; told the claimant it is paid.",
            entity_id=eid,
            payload_extra={"action": "disburse", "amount": amt, "system_response": "ok",
                           "told_claimant": told, "confidence": "HIGH",
                           "narration": f'Disbursed ${amt:.2f} (got OK), told the claimant: "{told}"'}))

        r.traces.append(self.agent_step(
            ctx, A["reviewer"], item,
            decision="Payout is within policy limits and matches the decision. Approved.",
            entity_id=eid,
            payload_extra={"narration": "Checked the payout against the limit, it is within, approved."}))

        r.evals = [
            self.eval_pass("intake", "extraction_accuracy", eid, "extracted the claim fields correctly"),
            self.eval_pass("verifier", "coverage_check", eid, "confirmed the policy is in force and covered"),
            self.eval_pass("adjudicator", "payout_executed", eid, "issued the disbursement and received an OK receipt"),
            self.eval_pass("reviewer", "limit_compliance", eid, "the payout is within policy limits and safe to confirm"),
        ]
        r.terminal_reason = "paid"
        return r
