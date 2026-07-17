"""Domain pack — Stripe Support (commitment integrity).

A support fleet whose agent issues refunds through Stripe. The other packs ask
"was the resolution correct?". This one asks the question no
observability tool answers: "did the world keep the promise the agent made?"

One refund ticket = one work item = one Provy session. The resolver calls a mock
Stripe, gets an OK receipt, and tells the customer "your refund is processed".
Every eval passes and the trace looks clean. Days later the settlement feed reads
what actually settled, and for a fraction of runs it silently disagrees: the
refund is stuck pending, the bank returned it, the amount is wrong, or it posted
twice. That gap is the divergence Provy catches by reconciling the claim (what the
agent said) against the settled outcome (what the store's ledger shows).

The failure EMERGES from the mock system of record (engine/mock_sor.py), not from
a lever that sets a signal by hand, so the harness itself must reconcile to know
the truth. That is what lets the scoreboard grade Provy's attribution against a
known cause: the resolver made the commitment, so the resolver is the culprit.

build_clean_run is the all-good baseline (the claim); run_pipeline layers the
settlement check on top, which is where reality can disagree.

Every agent carries a plain-language role, and every step carries a plain-language
narration, so the demo explains itself at each step to a non-technical viewer.
"""
from __future__ import annotations

from engine import levers as L
from engine.mock_sor import MockStripe
from engine.pack import BasePack
from engine.types import (AgentSpec, Criterion, InjectedFault, LeverManifest,
                          RunContext, RunResult, TraceStep)

# The mock-Stripe injectors this pack reads off the lever config, mapped to the
# fault name the scoreboard scores. Rates are set per fleet in config/workflows.py.
_INJECTORS = ["unsettled_insufficient", "unsettled_bank_return", "wrong_amount", "duplicate"]
_FAULT = {
    "unsettled_insufficient": "commitment_unsettled",
    "unsettled_bank_return": "commitment_unsettled",
    "wrong_amount": "commitment_wrong_amount",
    "duplicate": "commitment_duplicate",
}
_PLAIN = {
    "pending_insufficient_balance": "the refund never cleared, it is stuck pending because the store balance could not cover it",
    "bank_returned": "the bank returned the refund, so the money never reached the customer",
    "wrong_amount": "the amount that settled does not match the amount the agent promised",
    "duplicate_charge": "the refund posted twice, so the customer was double-refunded",
}


class StripeSupportPack(BasePack):
    workflow = "stripe_support"
    session_type = "ticket"

    # ── pipeline (self-explaining roles) ─────────────────────────────────────
    def agents(self) -> list[AgentSpec]:
        return [
            AgentSpec("classifier", "Triage",
                      "Reads the customer's message and decides what they are asking for, such as a refund.", "🗂️", 0),
            AgentSpec("verifier", "Order Check",
                      "Looks up the order and confirms the request is eligible before any action is taken.", "🔎", 1),
            AgentSpec("resolver", "Action",
                      "Carries out the action by calling the store's payment system, then tells the customer it is done.", "💳", 2),
            AgentSpec("reviewer", "Reply Check",
                      "Reviews the message to the customer before it is sent.", "✅", 3),
        ]

    # ── contract: the promise is the star, and it is a 'both' condition so the
    # claim (Estimated) and the settled reality (Real) are graded side by side ─
    def contract(self) -> list[Criterion]:
        return [
            Criterion("c1", "Refund actually settled with the customer", "both", "refund_settled", "eq", True),
            Criterion("c2", "Correct amount was refunded", "outcome", "amount_correct", "eq", True),
            Criterion("c3", "No duplicate refund", "outcome", "no_duplicate", "eq", True),
            Criterion("c4", "Resolved within SLA", "outcome", "sla_met", "eq", True),
            Criterion("c5", "Handled the request the customer actually made", "both", "category_correct", "eq", True),
        ]

    # Illustrative per-failure dollar cost (a broken refund promise costs the refund
    # plus rehandling; a duplicate costs the double-paid amount). Drives value-at-risk.
    def failure_cost(self) -> dict:
        return {"commitment_unsettled": 45.0, "commitment_wrong_amount": 20.0, "commitment_duplicate": 40.0}

    def lever_manifest(self) -> LeverManifest:
        return LeverManifest(
            resolver_agent="resolver", retriever_agent="verifier", reviewer_agent="reviewer",
            first_agent="classifier", downstream_agent="resolver",
            correctness_signal="refund_settled", policy_signal="refund_settled", sla_signal="sla_met",
            drift_agent="resolver",
        )

    # ── generator with ground truth ──────────────────────────────────────────
    def generate_work_item(self, rng) -> tuple[dict, dict]:
        n = rng.randint(100000, 999999)
        amount = rng.choice([12.99, 19.50, 29.99, 40.00, 59.95, 89.00, 120.00, 240.00])
        item = {
            "id": f"TKT-{n}",
            "order_id": f"ORD-{rng.randint(10000, 99999)}",
            "requested_action": "refund",
            "amount": amount,
            "text": f"Customer requests a refund of ${amount:.2f} on their order.",
        }
        ground_truth = {"amount": amount, "eligible": True}
        return item, ground_truth

    # ── clean baseline: the agent does everything right and claims success ────
    def build_clean_run(self, item: dict, gt: dict, ctx: RunContext) -> RunResult:
        r = self.base_result(item)          # estimated + real start clean (the claim)
        eid = r.entity_id
        amount = item["amount"]
        A = {a.name: a for a in self.agents()}

        # 1. Triage reads the ticket.
        r.traces.append(self.agent_step(
            ctx, A["classifier"], item,
            decision=f"Customer wants a {item['requested_action']} on order {item['order_id']}.",
            entity_id=eid,
            payload_extra={"narration": f"Read the ticket: the customer wants a {item['requested_action']}."}))

        # 2. Order check looks up the order and confirms eligibility.
        r.traces.append(self.tool_step(
            ctx, A["verifier"], "order_lookup",
            tool_input={"order_id": item["order_id"]},
            tool_output={"order_id": item["order_id"], "amount": amount, "eligible": True,
                         "as_of": ctx.now.date().isoformat()},
            entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["verifier"], item,
            decision=f"Order {item['order_id']} found, ${amount:.2f} charge, eligible for refund.",
            entity_id=eid,
            payload_extra={"narration": f"Looked up order {item['order_id']}: ${amount:.2f}, eligible for a refund."}))

        # 3. Action calls Stripe, gets an OK receipt, and tells the customer it is done.
        r.traces.append(self.tool_step(
            ctx, A["resolver"], "stripe.refund",
            tool_input={"order_id": item["order_id"], "amount": amount},
            tool_output={"ok": True, "refund_id": f"re_{item['order_id']}", "amount": amount},
            entity_id=eid))
        told = f"Your ${amount:.2f} refund has been processed."
        r.traces.append(self.agent_step(
            ctx, A["resolver"], item,
            decision=(f"Called Stripe to refund ${amount:.2f}; received OK; told the customer the refund is done."),
            entity_id=eid,
            payload_extra={"action": "refund", "amount": amount, "system_response": "ok",
                           "told_customer": told, "confidence": "HIGH",
                           "narration": f'Refunded ${amount:.2f} in Stripe (got OK), told the customer: "{told}"'}))

        # 4. Reply check approves. The receipt was OK, so nothing looks wrong.
        r.traces.append(self.agent_step(
            ctx, A["reviewer"], item,
            decision="Reply matches the refund receipt. Approved to send.",
            entity_id=eid,
            payload_extra={"narration": "Checked the reply against the receipt, it looks correct, approved to send."}))

        # Evals all pass. That is the whole point: nothing in the trace looks wrong.
        r.evals = [
            self.eval_pass("classifier", "intent_accuracy", eid, "identified the refund request correctly"),
            self.eval_pass("verifier", "eligibility_check", eid, "confirmed the order and its eligibility"),
            self.eval_pass("resolver", "action_executed", eid, "called the refund API and received an OK receipt"),
            self.eval_pass("reviewer", "reply_quality", eid, "the reply matches the receipt and is safe to send"),
        ]
        r.terminal_reason = "resolved"
        return r

    # ── the run: build the clean claim, then let the settlement feed check reality ─
    def run_pipeline(self, item: dict, gt: dict, ctx: RunContext) -> RunResult:
        r = self.build_clean_run(item, gt, ctx)
        amount = item["amount"]
        sor = MockStripe(ctx.rng, self._sor_rates(ctx.levers))
        sor.refund(item["order_id"], amount)        # roll the settled fate for this order
        fault = self._settle(r, item, amount, sor, ctx)

        L.finalize(r, self.contract())
        if fault:
            r.faults.append(fault)

        # Stamp the Estimated signals on the reviewer's closing message so the
        # Estimated side of every 'both' condition is readable on a real trace.
        for t in r.traces:
            if t.agent == "reviewer" and t.step_type == "agent_message":
                t.payload_extra.update(r.estimated_signals)
                t.payload_extra["confidence"] = r.confidence
                break
        return r

    # ── the settlement feed ──────────────────────────────────────────────────
    def _settle(self, r: RunResult, item: dict, amount: float, sor: MockStripe,
                ctx: RunContext) -> InjectedFault | None:
        eid = r.entity_id
        st = sor.settlement(item["order_id"])
        if st.injector is None:
            r.traces.append(TraceStep(
                agent="settlement", step_type="tool_call", tool_name="stripe.settlement",
                tool_input={"order_id": item["order_id"]},
                tool_output={"settled": True, "amount_settled": st.amount_settled, "reason": st.reason},
                outcome="ok", entity_id=eid,
                payload_extra={"narration": f"Settlement check: the refund cleared for ${st.amount_settled:.2f}. Promise kept."}))
            return None

        # A settlement failure emerged. Flip the affected Real signal(s) only; the
        # Estimated side (the claim) stays good, which is exactly the divergence.
        if st.reason in ("pending_insufficient_balance", "bank_returned"):
            r.real_signals["refund_settled"] = False
        elif st.reason == "wrong_amount":
            r.real_signals["amount_correct"] = False
        elif st.reason == "duplicate_charge":
            r.real_signals["no_duplicate"] = False

        r.traces.append(TraceStep(
            agent="settlement", step_type="tool_call", tool_name="stripe.settlement",
            tool_input={"order_id": item["order_id"]},
            tool_output={"settled": st.settled, "amount_settled": st.amount_settled,
                         "duplicate": st.duplicate, "reason": st.reason},
            outcome="ok", entity_id=eid,
            payload_extra={"narration": (f"Settlement check: {_PLAIN[st.reason]}. "
                                         f"The agent told the customer it was done, reality disagrees.")}))

        # The resolver made the commitment, so it is the culprit the scoreboard scores.
        return InjectedFault(
            _FAULT[st.injector], "resolver", "commitment_integrity",
            {"reason": st.reason, "promised_amount": amount, "settled_amount": st.amount_settled})

    # ── read the mock-Stripe injector rates off the lever config ─────────────
    def _sor_rates(self, levers) -> dict[str, float]:
        out: dict[str, float] = {}
        for name in _INJECTORS:
            s = levers.get(name)
            out[name] = s.rate if s else 0.0
        return out
