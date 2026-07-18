"""Domain pack — Legal (commitment integrity).

A legal-ops agent sends a contract for signature and files it, then reports
"executed and filed." The e-sign and filing calls return OK, every eval passes,
and the trace looks clean. Later the systems of record read what actually happened:
the e-signature never completed, the filing bounced, the filing deadline lapsed, it
went to the wrong counterparty, or it filed twice. That gap is the divergence Provy
catches by reconciling the claim against the settled execution/filing state.

Slower and more subjective ground truth than the other domains, so this is the
partial pack of the set — but the shape is identical: a fast, success-looking action
whose settled state silently diverges. The filer made the commitment, so the filer is
the culprit. Superset pack: it also runs the full generic + L1/L2 lever set.
"""
from __future__ import annotations

from engine.commitment import CommitmentPack, Injector
from engine.types import AgentSpec, Criterion, LeverManifest, RunContext, RunResult


class LegalPack(CommitmentPack):
    workflow = "legal"
    session_type = "matter"

    def agents(self) -> list[AgentSpec]:
        return [
            AgentSpec("intake", "Matter Intake",
                      "Reads the request and pins down the matter, the document, and the counterparty.", "📁", 0),
            AgentSpec("drafter", "Document Prep",
                      "Assembles the document and confirms the counterparty and the filing venue.", "🔎", 1),
            AgentSpec("filer", "Execute & File",
                      "Sends the document for e-signature and files it, then reports it is executed and filed.", "🖋️", 2),
            AgentSpec("reviewer", "Compliance Review",
                      "Checks the execution and filing against the matter before it is reported done.", "✅", 3),
        ]

    def contract(self) -> list[Criterion]:
        return [
            Criterion("c1", "The document was actually executed and filed", "both", "execution_confirmed", "eq", True),
            Criterion("c2", "Sent to the correct counterparty", "outcome", "sent_to_correct_party", "eq", True),
            Criterion("c3", "No duplicate filing", "outcome", "no_duplicate_filing", "eq", True),
            Criterion("c4", "Filed before the deadline", "outcome", "deadline_met", "eq", True),
            Criterion("c5", "Handled the matter that was requested", "both", "matter_correct", "eq", True),
        ]

    def failure_cost(self) -> dict:
        return {"commitment_unsettled": 700.0, "commitment_wrong_target": 900.0,
                "commitment_duplicate": 300.0}

    def lever_manifest(self) -> LeverManifest:
        return LeverManifest(
            resolver_agent="filer", retriever_agent="drafter", reviewer_agent="reviewer",
            first_agent="intake", downstream_agent="filer",
            correctness_signal="execution_confirmed", policy_signal="execution_confirmed",
            sla_signal="deadline_met", drift_agent="filer",
        )

    def injectors(self) -> list[Injector]:
        return [
            Injector("esign_incomplete", "unsettled", "esignature_never_completed",
                     "the e-signature never completed, so the document is not executed"),
            Injector("filing_bounced", "unsettled", "filing_rejected",
                     "the filing bounced at the venue, so nothing is on record"),
            Injector("deadline_lapsed", "unsettled", "filing_deadline_lapsed",
                     "the filing deadline lapsed before the document was executed"),
            Injector("wrong_counterparty", "wrong_target", "wrong_counterparty",
                     "the document went to the wrong counterparty"),
            Injector("duplicate_filing", "duplicate", "duplicate_filing",
                     "the document filed twice, so there are duplicate records"),
        ]

    def settle_map(self) -> dict:
        return {"promise": "execution_confirmed",
                "wrong_target": "sent_to_correct_party", "duplicate": "no_duplicate_filing"}

    def clean_narration(self, amount: float) -> str:
        return "Execution check: the e-signature completed and the filing is on record. Promise kept."

    def generate_work_item(self, rng) -> tuple[dict, dict]:
        n = rng.randint(100000, 999999)
        doc = rng.choice(["NDA", "MSA", "SOW", "amendment", "assignment"])
        party = rng.choice(["Acme Corp", "Globex", "Initech", "Umbrella LLC", "Wayne Ent"])
        item = {
            "id": f"MAT-{n}",
            "matter_id": f"M-{rng.randint(10000, 99999)}",
            "document": doc,
            "counterparty": party,
            "text": f"Send the {doc} to {party} for signature and file it.",
        }
        ground_truth = {"document": doc, "counterparty": party}
        return item, ground_truth

    def build_clean_run(self, item: dict, gt: dict, ctx: RunContext) -> RunResult:
        r = self.base_result(item)
        eid = r.entity_id
        doc, party = item["document"], item["counterparty"]
        A = {a.name: a for a in self.agents()}

        r.traces.append(self.agent_step(
            ctx, A["intake"], item,
            decision=f"Matter {item['matter_id']}: send the {doc} to {party} and file it.",
            entity_id=eid,
            payload_extra={"narration": f"Read the request: send the {doc} to {party} and file it."}))

        r.traces.append(self.tool_step(
            ctx, A["drafter"], "docs.assemble",
            tool_input={"matter_id": item["matter_id"], "document": doc},
            tool_output={"matter_id": item["matter_id"], "document": doc, "counterparty": party,
                         "venue_ok": True, "as_of": ctx.now.date().isoformat()},
            entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["drafter"], item,
            decision=f"Assembled the {doc} for {party}; venue confirmed.",
            entity_id=eid,
            payload_extra={"narration": f"Assembled the {doc} for {party}, venue confirmed."}))

        r.traces.append(self.tool_step(
            ctx, A["filer"], "esign.send_and_file",
            tool_input={"document": doc, "counterparty": party},
            tool_output={"ok": True, "envelope_id": f"env_{item['matter_id']}", "filed": True},
            entity_id=eid))
        told = f"The {doc} is executed by {party} and filed."
        r.traces.append(self.agent_step(
            ctx, A["filer"], item,
            decision=f"Sent the {doc} for signature and filed it; received OK; reported executed and filed.",
            entity_id=eid,
            payload_extra={"action": "execute_file", "system_response": "ok",
                           "told_requester": told, "confidence": "HIGH",
                           "narration": f'Sent and filed the {doc} (got OK), reported: "{told}"'}))

        r.traces.append(self.agent_step(
            ctx, A["reviewer"], item,
            decision="Execution and filing match the matter. Approved.",
            entity_id=eid,
            payload_extra={"narration": "Checked the execution and filing against the matter, it matches, approved."}))

        r.evals = [
            self.eval_pass("intake", "request_accuracy", eid, "captured the matter, document, and counterparty"),
            self.eval_pass("drafter", "prep_completeness", eid, "assembled the document and confirmed the venue"),
            self.eval_pass("filer", "execution_executed", eid, "sent for e-sign and filed, received an OK receipt"),
            self.eval_pass("reviewer", "compliance_quality", eid, "the execution and filing match the matter"),
        ]
        r.terminal_reason = "filed"
        return r
