"""Domain pack - Teameight / post-call sales follow-up (commitment integrity).

Shaped after the Phase 1 post-call workflow Teameight sent us on 19 Aug 2026. A call ends, a
transcript lands, and a pipeline analyses it, resolves the opportunity and its owning AE, writes
normalised fields back to Salesforce, logs a CRM activity, drafts a follow-up email, notifies the
AE, and closes itself out as "Workflow Complete / Audited".

That last step is the whole reason this pack exists. The workflow reports success because it
reached the end of its own diagram, not because anything outside it agreed. Salesforce and the
AE's mailbox are what actually settle a follow-up, and this pack reconciles the claim against them.

⛔ THREE ASSUMPTIONS, MADE BY US AND NOT CONFIRMED BY TEAMEIGHT. They are printed on the console's
pack banner (provy-sim-control `lib/pack-assumptions.ts`) so nobody reads a fleet's numbers without
seeing them:

  1. EVERY STEP IS ITS OWN AGENT with its own trace identity. If Teameight actually runs all seven
     under one orchestrator, Provy can still grade a run but cannot honestly name a step, and every
     attribution in this fleet would be fiction. This is the assumption that most changes the demo.
  2. THE SEND IS RECORDED. Their diagram logs the CRM activity when the agent DRAFTS, several steps
     before the AE sends, so as drawn nothing records the outcome that matters most. We assume a
     send record exists, which is what makes c1, c6 and c8 gradeable at all.
  3. THE OPPORTUNITY SCORECARD IS OUT OF THE CONTRACT. Their own diagram marks it "open item
     (output format)". The `scorer` agent runs and is judged at the eval level, but nothing is
     contracted against an output whose shape is undecided.

The star condition is c6, "the email that went out is the one the agent drafted". It is the signal
Teameight already produces for free and does not read: an AE who rewrites every draft before
sending is telling you the agent is failing, while the email goes out every time and every
completion dashboard scores it green.
"""
from __future__ import annotations

from engine.commitment import CommitmentPack, Injector
from engine.types import (AgentSpec, Criterion, InjectedFault, LeverManifest, RunContext,
                          RunResult, TraceStep)


class TeameightPack(CommitmentPack):
    workflow = "teameight"
    session_type = "meeting"

    # ── pipeline ──────────────────────────────────────────────────────────────
    def agents(self) -> list[AgentSpec]:
        return [
            AgentSpec("analyzer", "Transcript Analysis",
                      "Reads the call transcript and pulls out what was discussed, agreed and promised.", "\U0001F4DD", 0),
            AgentSpec("resolver", "Context Resolution",
                      "Matches the call to an opportunity and account, and works out which AE owns it.", "\U0001F50E", 1),
            AgentSpec("summarizer", "Meeting Summary",
                      "Turns the transcript into the structured meeting summary the CRM will carry.", "\U0001F4C4", 2),
            AgentSpec("scorer", "Opportunity Scorecard",
                      "Scores the opportunity from the call so the deal desk can see where it stands.", "\U0001F3AF", 3),
            AgentSpec("patcher", "CRM Update",
                      "Builds the semantic patch, writes the normalised fields to Salesforce and logs the activity.", "\U0001F58A️", 4),
            AgentSpec("drafter", "Follow-up Draft",
                      "Drafts the follow-up email from the call and hands it to the AE to send.", "✉️", 5),
            AgentSpec("closer", "Workflow Audit",
                      "Closes the run out and reports the post-call workflow complete.", "✅", 6),
        ]

    def contract(self) -> list[Criterion]:
        return [
            Criterion("c1", "The follow-up actually went out", "both", "followup_sent", "eq", True),
            Criterion("c2", "The CRM update actually landed", "both", "crm_write_landed", "eq", True),
            Criterion("c3", "It updated the right opportunity", "both", "opportunity_correct", "eq", True),
            Criterion("c4", "The AE notified owns the opportunity", "both", "recipient_correct", "eq", True),
            Criterion("c5", "The activity was logged against the opportunity", "outcome", "activity_logged", "eq", True),
            Criterion("c6", "The email that went out is the one the agent drafted", "both", "sent_as_drafted", "eq", True),
            Criterion("c7", "No duplicate follow-up", "outcome", "no_duplicate_followup", "eq", True),
            Criterion("c8", "The follow-up went out within the window", "outcome", "sla_met", "eq", True),
        ]

    def failure_cost(self) -> dict:
        """Per-occurrence cost, illustrative. A follow-up that never goes out is the expensive one:
        the deal keeps its value on the forecast and nobody is working it."""
        return {
            "commitment_unsettled": 420.0,     # no follow-up at all, the deal goes quiet
            "commitment_wrong_target": 260.0,  # the wrong AE was told, so nobody acts
            "commitment_wrong_amount": 180.0,  # the CRM never got the update, the forecast is stale
            "commitment_duplicate": 90.0,      # the buyer got the same follow-up twice
            "condition_miss": 140.0,           # includes the rewritten draft, see c6
        }

    # ── vocabulary ────────────────────────────────────────────────────────────
    def trace_aliases(self) -> dict[str, str]:
        """What Teameight's own agents would emit, which is not the contract's vocabulary.

        The pattern throughout is the same one the contract exists to catch: the agent records the
        ACKNOWLEDGEMENT it received, never the thing that had to be true. `email_dispatch_ack` is
        the mail API accepting the handoff; it is not the AE sending it. `sfdc_write_ack` is
        Salesforce accepting a write; it is not the field holding the new value afterwards.

        `sla_met` genuinely matches. Some names do."""
        return {
            "followup_sent":       "email_dispatch_ack",
            "crm_write_landed":    "sfdc_write_ack",
            "opportunity_correct": "opp_match_ok",
            "recipient_correct":   "ae_resolved_ok",
            "sent_as_drafted":     "draft_final",
        }

    def signal_owners(self) -> dict[str, str]:
        """Which agent's work decides each signal, and so who a failure is attributed to.

        ⛔ WITHOUT THIS EVERY CONDITION BLAMES THE CLOSER, which is the worst possible answer here:
        the closer is the step that says "Workflow Complete / Audited", so a fleet with no ownership
        map would blame the self-congratulation step for every failure in the pipeline.

        The drafter makes the outward commitment, so the follow-up is its promise. Which opportunity
        and which AE both come out of the resolver's lookup, not out of whoever used the answer.
        `sla_met` is deliberately unowned: elapsed time is a property of the whole run, so it falls
        to the closer, which is the honest answer rather than a convenient one."""
        return {
            "followup_sent":          "drafter",
            "sent_as_drafted":        "drafter",
            "no_duplicate_followup":  "drafter",
            "crm_write_landed":       "patcher",
            "activity_logged":        "patcher",
            "opportunity_correct":    "resolver",
            "recipient_correct":      "resolver",
        }

    def lever_manifest(self) -> LeverManifest:
        return LeverManifest(
            resolver_agent="drafter",       # makes the outward commitment: the follow-up
            retriever_agent="resolver",     # the lookup that can serve stale or weak matches
            reviewer_agent="closer",        # stamps the closing claim
            first_agent="analyzer",
            downstream_agent="drafter",
            correctness_signal="followup_sent",
            policy_signal="crm_write_landed",
            sla_signal="sla_met",
            # ⛔ Anything not reachable by the four slots above or by a settlement injector can never
            # fail, and a condition that never fails cannot be demonstrated. c3, c5 and c6 live here.
            other_signals={
                "opportunity_correct": "resolver",
                "activity_logged":     "patcher",
                "sent_as_drafted":     "drafter",
            },
            drift_agent="drafter",
        )

    # ── settlement ────────────────────────────────────────────────────────────
    def injectors(self) -> list[Injector]:
        return [
            Injector("followup_never_sent", "unsettled", "draft_never_dispatched",
                     "the draft was created and handed over, and the follow-up never went out"),
            Injector("wrong_ae_notified", "wrong_target", "notified_non_owner",
                     "the notification went to someone who does not own the opportunity, so nobody acted on it"),
            Injector("crm_write_dropped", "wrong_amount", "sfdc_write_not_persisted",
                     "Salesforce acknowledged the write and the field still reads its old value"),
            Injector("duplicate_followup", "duplicate", "duplicate_send",
                     "the follow-up went out twice, so the buyer got the same email from the same rep"),
        ]

    def settle_map(self) -> dict:
        return {
            "promise":      "followup_sent",
            "wrong_target": "recipient_correct",
            "wrong_amount": "crm_write_landed",
            "duplicate":    "no_duplicate_followup",
        }

    def commit_ref(self, item) -> str:
        return item["opp_id"]

    def commit_amount(self, item) -> float:
        """The opportunity value. It is what makes a missed follow-up cost a number instead of a
        count, which is the only way the Ledger says anything a sales leader cares about."""
        return float(item["deal_value"])

    def clean_narration(self, amount: float) -> str:
        return ("Send record: the follow-up went out from the AE, unedited, inside the window. "
                "Salesforce carries the update and the activity. Promise kept.")

    # ── work items ────────────────────────────────────────────────────────────
    _TOPICS = [
        ("pricing and packaging", "send the enterprise pricing sheet"),
        ("the security review", "send the SOC 2 report and the DPA"),
        ("the technical validation", "book a working session with their platform team"),
        ("procurement timing", "send the order form for legal review"),
        ("the pilot scope", "confirm the success criteria in writing"),
        ("integration effort", "send the API docs and a reference architecture"),
    ]
    _STAGES = ["Discovery", "Technical Validation", "Proposal", "Negotiation"]

    def generate_work_item(self, rng) -> tuple[dict, dict]:
        n = rng.randint(100000, 999999)
        deal = rng.choice([18000, 35000, 60000, 110000, 180000, 320000])
        topic, commitment = rng.choice(self._TOPICS)
        stage = rng.choice(self._STAGES)
        attendees = rng.randint(2, 6)
        minutes = rng.choice([22, 30, 45, 52, 60])
        item = {
            "id": f"MTG-{n}",
            "opp_id": f"OPP-{rng.randint(10000, 99999)}",
            "account": f"ACC-{rng.randint(1000, 9999)}",
            "ae": f"ae-{rng.randint(10, 60)}@teameight-customer.com",
            "deal_value": deal,
            "stage": stage,
            "attendees": attendees,
            "duration_min": minutes,
            "text": (f"{minutes}-minute call with {attendees} attendees about {topic}. "
                     f"Agreed next step: {commitment}."),
        }
        ground_truth = {
            "deal_value": deal, "stage": stage, "commitment": commitment,
            "topic": topic, "ae": item["ae"],
        }
        return item, ground_truth

    # ── the clean run ─────────────────────────────────────────────────────────
    def build_clean_run(self, item: dict, gt: dict, ctx: RunContext) -> RunResult:
        r = self.base_result(item)
        eid = r.entity_id
        A = {a.name: a for a in self.agents()}
        opp, ae, deal = item["opp_id"], item["ae"], item["deal_value"]

        r.traces.append(self.agent_step(
            ctx, A["analyzer"], item,
            decision=f"Call covered {gt['topic']}. Agreed next step: {gt['commitment']}.",
            entity_id=eid,
            payload_extra={"narration": f"Read the transcript: {gt['topic']}, next step is to {gt['commitment']}."}))

        r.traces.append(self.tool_step(
            ctx, A["resolver"], "sfdc.query_opportunity",
            tool_input={"account": item["account"], "meeting": eid},
            tool_output={"opp_id": opp, "owner_email": ae, "stage": item["stage"],
                         "amount": deal, "as_of": ctx.now.date().isoformat(), "match_score": 0.94},
            entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["resolver"], item,
            decision=f"Matched the call to {opp}; the record's owner is {ae}.",
            entity_id=eid,
            payload_extra={"narration": f"Matched to {opp}, owner {ae}, so the follow-up goes to them."}))

        r.traces.append(self.agent_step(
            ctx, A["summarizer"], item,
            decision=f"Structured summary written: {gt['topic']}, {item['attendees']} attendees, next step agreed.",
            entity_id=eid,
            payload_extra={"narration": "Wrote the structured meeting summary for the CRM."}))

        # ⛔ Scored but NOT contracted. Their diagram marks the scorecard "open item (output format)",
        # so there is no stable shape to grade. It is judged at the eval level and nowhere else.
        r.traces.append(self.agent_step(
            ctx, A["scorer"], item,
            decision=f"Scored {opp} at stage {item['stage']} off the call.",
            entity_id=eid,
            payload_extra={"narration": f"Scored the opportunity at stage {item['stage']} (format still open)."}))

        r.traces.append(self.tool_step(
            ctx, A["patcher"], "sfdc.update_fields",
            tool_input={"opp_id": opp, "stage": item["stage"], "amount": deal},
            tool_output={"status": "SUCCESS", "id": opp, "errors": []},
            entity_id=eid))
        r.traces.append(self.tool_step(
            ctx, A["patcher"], "sfdc.log_activity",
            tool_input={"opp_id": opp, "type": "Meeting", "subject": f"Call summary {eid}"},
            tool_output={"status": "SUCCESS", "activity_id": f"ACT-{eid[-6:]}"},
            entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["patcher"], item,
            decision=f"Wrote the normalised fields to {opp} and logged the activity.",
            entity_id=eid,
            payload_extra={"action": "crm_update", "system_response": "SUCCESS",
                           "narration": f"Patched {opp} and logged the meeting activity."}))

        told = f"Follow-up drafted for {opp} and sent to {ae} to review."
        r.traces.append(self.tool_step(
            ctx, A["drafter"], "email.create_draft",
            tool_input={"opp_id": opp, "to_owner": ae, "next_step": gt["commitment"]},
            tool_output={"status": "QUEUED", "draft_id": f"DR-{eid[-6:]}", "accepted": True},
            entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["drafter"], item,
            decision=f"Drafted the follow-up covering {gt['commitment']}; handed to {ae}.",
            entity_id=eid,
            payload_extra={"action": "followup_draft", "told_rep": told, "confidence": "HIGH",
                           "narration": f'Drafted the follow-up and handed it over: "{told}"'}))

        r.traces.append(self.agent_step(
            ctx, A["closer"], item,
            decision="Post-call workflow complete. Every step returned success.",
            entity_id=eid,
            payload_extra={"narration": "Closed the run out: every step reported success."}))

        r.evals = [
            self.eval_pass("analyzer", "transcript_accuracy", eid, "captured the topic and the agreed next step"),
            self.eval_pass("resolver", "context_relevance", eid, "matched the right opportunity and its owner"),
            self.eval_pass("summarizer", "summary_faithfulness", eid, "the summary reflects what was said on the call"),
            self.eval_pass("scorer", "scorecard_quality", eid, "scored the opportunity consistently with the call"),
            self.eval_pass("patcher", "write_executed", eid, "issued the field update and the activity log"),
            self.eval_pass("drafter", "draft_grounded", eid, "the draft promises only what the call actually agreed"),
            self.eval_pass("closer", "audit_quality", eid, "every step is accounted for before the run is closed"),
        ]
        r.terminal_reason = "followed_up"
        return r

    # ── the rewritten draft: a pack-local lever ───────────────────────────────
    def _rate(self, ctx: RunContext, name: str, default: float) -> float:
        """Read a rate off the lever config, treating an explicit 0 as 0.

        Deliberately NOT `LeverConfig.get()`, which reports rate 0 as "not set". That is right for a
        chaos lever where 0 means off, and wrong here: it would swap an operator's explicit 0 for the
        built-in default and make the dial impossible to turn off. Same helper ITSM uses.
        """
        setting = ctx.levers.settings.get(name)
        return setting.rate if setting is not None else default

    def _apply_rewritten_draft(self, r: RunResult, ctx: RunContext) -> None:
        """The AE rewrote the draft before sending it.

        ⛔ THIS IS A PACK-LOCAL LEVER, NOT A SETTLEMENT INJECTOR. CommitmentPack carries exactly four
        settlement shapes (unsettled, wrong_amount, wrong_target, duplicate) and this is none of
        them: the send SUCCEEDED. Nothing errored, nothing was late, the buyer got an email. The only
        thing that happened is that the human did not use what the agent wrote, which is why every
        completion-shaped check in Teameight's own diagram scores this run green.

        Fires only on a run no other lever has already shaped, so attribution stays one cause per
        run, and only when the follow-up actually went out: a draft that was never sent cannot also
        have been rewritten.
        """
        if r.faults or not bool(r.real_signals.get("followup_sent", True)):
            return
        if ctx.rng.random() >= self._rate(ctx, "draft_rewritten", 0.0):
            return
        r.real_signals["sent_as_drafted"] = False
        r.metadata["draft_rewritten"] = True
        r.faults.append(InjectedFault(
            "draft_rewritten", "drafter", "draft_not_used",
            {"signal": "sent_as_drafted", "side": "both"}))

    # ── the send record ───────────────────────────────────────────────────────
    def run_pipeline(self, item, gt, ctx: RunContext) -> RunResult:
        """Adds the send record on top of the shared commitment pipeline.

        ⛔ THIS TRACE STEP IS THE ASSUMPTION MADE VISIBLE. It is the record their flow does not
        currently produce (they log the CRM activity at DRAFT time, before the AE sends), and every
        gradeable statement about what happened to the email rests on it existing. It reads the
        post-lever real signals, so it narrates what actually happened rather than what was claimed.

        The rewritten-draft case is the one worth watching. Nothing failed, the email went out, and
        the only thing that changed is that the AE did not use what the agent wrote."""
        r = super().run_pipeline(item, gt, ctx)
        self._apply_rewritten_draft(r, ctx)
        real = r.real_signals
        sent = bool(real.get("followup_sent", True))
        as_drafted = bool(real.get("sent_as_drafted", True))
        within = bool(real.get("sla_met", True))

        if not sent:
            note = "The draft is still sitting unsent. Nothing went to the buyer."
        elif not as_drafted:
            note = ("The AE rewrote the draft before sending. The email went out, so every "
                    "completion check passes, and the agent's own draft was not used.")
        elif not within:
            note = "The follow-up went out, but after the window had closed."
        else:
            note = "The follow-up went out as drafted, inside the window."

        r.traces.append(TraceStep(
            agent="drafter", step_type="tool_call", tool_name="email.send_record",
            tool_input={"draft_id": f"DR-{r.entity_id[-6:]}"},
            # ⛔ UNDER THE FLEET'S OWN NAME (`draft_final`), NEVER THE CONTRACT'S. Emitting
            # `sent_as_drafted` here would let Provy auto-bind c6 with nobody confirming the
            # mapping, which is not how a real tenant behaves and would skip the human decision
            # this whole fleet is meant to exercise. Guarded by test_trace_vocabulary.
            tool_output={"sent": sent, self.trace_aliases()["sent_as_drafted"]: as_drafted,
                         "within_window": within},
            outcome="ok", entity_id=r.entity_id,
            payload_extra={"narration": f"Send record: {note}"}))
        return r
