"""Domain pack — RevOps / sales (commitment integrity).

A deal-desk agent applies an approved discount and updates the CRM, then reports
"done: the CRM is updated and the discount is applied." The batch write returns a
top-level 200/COMPLETE, every eval passes, and the trace looks clean. Later a
re-query of the record reads what actually landed: the write failed inside the
batch and never persisted, a sync-window lag left it blank, the wrong discount
applied, the wrong record was touched, or a duplicate opportunity was created.
That gap is the divergence Provy catches by reconciling the claim against the
settled CRM record.

The cleanest deterministic "trace says success, settled state disagrees" primitive
in any domain (HubSpot-style batch 200 with per-record numErrors). The failure
EMERGES from the mock system of record; the updater made the commitment, so the
updater is the culprit. Superset pack: runs the full generic + L1/L2 lever set too.
"""
from __future__ import annotations

from engine.commitment import CommitmentPack, Injector
from engine.types import AgentSpec, Criterion, LeverManifest, RunContext, RunResult


class RevOpsPack(CommitmentPack):
    workflow = "revops"
    session_type = "deal"

    def agents(self) -> list[AgentSpec]:
        return [
            AgentSpec("intake", "Request Intake",
                      "Reads the rep's request and pins down the deal, the record, and the approved discount.", "📨", 0),
            AgentSpec("lookup", "Record Lookup",
                      "Finds the opportunity and account in the CRM and confirms the approved discount.", "🔎", 1),
            AgentSpec("updater", "CRM Update",
                      "Writes the discount and deal stage to the CRM, then reports the record is updated.", "🖊️", 2),
            AgentSpec("reviewer", "Change Review",
                      "Checks the change against the request before the rep is told it is done.", "✅", 3),
        ]

    def contract(self) -> list[Criterion]:
        return [
            Criterion("c1", "The CRM update actually landed", "both", "write_committed", "eq", True),
            Criterion("c2", "Applied the approved discount amount", "outcome", "amount_correct", "eq", True),
            Criterion("c3", "No duplicate opportunity", "outcome", "no_duplicate_record", "eq", True),
            Criterion("c4", "Updated within SLA", "outcome", "sla_met", "eq", True),
            Criterion("c5", "Updated the right record", "both", "routed_correct", "eq", True),
        ]

    def failure_cost(self) -> dict:
        return {"commitment_unsettled": 90.0, "commitment_wrong_amount": 60.0,
                "commitment_wrong_target": 80.0, "commitment_duplicate": 70.0}

    def lever_manifest(self) -> LeverManifest:
        return LeverManifest(
            resolver_agent="updater", retriever_agent="lookup", reviewer_agent="reviewer",
            first_agent="intake", downstream_agent="updater",
            correctness_signal="write_committed", policy_signal="write_committed", sla_signal="sla_met",
            drift_agent="updater",
        )

    def injectors(self) -> list[Injector]:
        return [
            Injector("write_not_landed", "unsettled", "batch_partial_failure",
                     "the batch write returned COMPLETE but this record failed inside it, so the update never landed"),
            Injector("sync_lag", "unsettled", "sync_window_never_synced",
                     "the update sat in the sync window and never propagated, so the record still reads the old value"),
            Injector("wrong_discount", "wrong_amount", "discount_mismatch",
                     "the discount that saved is not the approved discount"),
            Injector("wrong_record", "wrong_target", "wrong_record_updated",
                     "the update landed on the wrong opportunity"),
            Injector("duplicate_opportunity", "duplicate", "duplicate_record",
                     "a second opportunity was created, so the deal is duplicated in the CRM"),
        ]

    def settle_map(self) -> dict:
        return {"promise": "write_committed", "wrong_amount": "amount_correct",
                "wrong_target": "routed_correct", "duplicate": "no_duplicate_record"}

    def commit_amount(self, item) -> float:
        return float(item["discount"])

    def clean_narration(self, amount: float) -> str:
        return f"Record check: the CRM re-query shows the update landed (${amount:.2f} discount applied). Promise kept."

    def generate_work_item(self, rng) -> tuple[dict, dict]:
        n = rng.randint(100000, 999999)
        deal = rng.choice([12000, 28000, 45000, 90000, 150000, 240000])
        discount = round(deal * rng.choice([0.05, 0.10, 0.15, 0.20]), 2)
        item = {
            "id": f"DEAL-{n}",
            "opp_id": f"OPP-{rng.randint(10000, 99999)}",
            "deal_value": deal,
            "discount": discount,
            "text": f"Apply the approved ${discount:.0f} discount to opportunity and move it to Negotiation.",
        }
        ground_truth = {"deal_value": deal, "discount": discount, "approved": True}
        return item, ground_truth

    def build_clean_run(self, item: dict, gt: dict, ctx: RunContext) -> RunResult:
        r = self.base_result(item)
        eid = r.entity_id
        disc = item["discount"]
        A = {a.name: a for a in self.agents()}

        r.traces.append(self.agent_step(
            ctx, A["intake"], item,
            decision=f"Rep wants a ${disc:.0f} approved discount on {item['opp_id']}.",
            entity_id=eid,
            payload_extra={"narration": f"Read the request: apply a ${disc:.0f} discount to {item['opp_id']}."}))

        r.traces.append(self.tool_step(
            ctx, A["lookup"], "crm.get_opportunity",
            tool_input={"opp_id": item["opp_id"]},
            tool_output={"opp_id": item["opp_id"], "deal_value": item["deal_value"],
                         "approved_discount": disc, "as_of": ctx.now.date().isoformat()},
            entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["lookup"], item,
            decision=f"Found {item['opp_id']}; the ${disc:.0f} discount is approved.",
            entity_id=eid,
            payload_extra={"narration": f"Looked up {item['opp_id']}: the ${disc:.0f} discount is approved."}))

        r.traces.append(self.tool_step(
            ctx, A["updater"], "crm.batch_write",
            tool_input={"opp_id": item["opp_id"], "discount": disc, "stage": "Negotiation"},
            tool_output={"status": "COMPLETE", "numErrors": 0, "opp_id": item["opp_id"]},
            entity_id=eid))
        told = f"Done: {item['opp_id']} updated with the ${disc:.0f} discount and moved to Negotiation."
        r.traces.append(self.agent_step(
            ctx, A["updater"], item,
            decision=f"Wrote the ${disc:.0f} discount and stage to the CRM; batch returned COMPLETE; reported done.",
            entity_id=eid,
            payload_extra={"action": "crm_update", "discount": disc, "system_response": "COMPLETE",
                           "told_rep": told, "confidence": "HIGH",
                           "narration": f'Wrote the update (batch COMPLETE), reported: "{told}"'}))

        r.traces.append(self.agent_step(
            ctx, A["reviewer"], item,
            decision="Change matches the request and the write receipt. Approved.",
            entity_id=eid,
            payload_extra={"narration": "Checked the change against the request, it matches, approved."}))

        r.evals = [
            self.eval_pass("intake", "request_accuracy", eid, "captured the record and discount correctly"),
            self.eval_pass("lookup", "record_relevance", eid, "found the opportunity and confirmed the approval"),
            self.eval_pass("updater", "write_executed", eid, "issued the CRM write and received a COMPLETE receipt"),
            self.eval_pass("reviewer", "change_quality", eid, "the change matches the request and is safe to confirm"),
        ]
        r.terminal_reason = "updated"
        return r
