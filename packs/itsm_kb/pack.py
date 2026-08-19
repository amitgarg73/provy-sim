"""An ITSM desk with the knowledge lookup split out of the resolver.

⛔ WHY THIS IS NOT A CHANGE TO `itsm`. That pack's contract must stay equal, character for
character, to the six conditions running on the live ServiceNow PDI and confirmed by a human
there (packs.ts says so, and argus#638 put a parity test behind it). It also takes its work
items FROM that instance and sets owns_outcome=False, so it cannot settle anything locally.
This pack is the same desk with the knowledge step separated, settling its own outcomes, so the
question "who does Provy blame when the article was wrong" can be answered without touching the
live demo.

⛔ THE QUESTION THIS PACK EXISTS TO ANSWER. A reopened incident where knowledge was involved is
not one failure, it is four, and they need different answers:

  kb_hallucinated     the knowledge agent cited an article that does not exist   -> KNOWLEDGE
  kb_wrong_article    it cited a real article, wrong one for the symptom         -> KNOWLEDGE
  kb_stale_article    it cited the RIGHT article and the article itself is wrong -> NOBODY
  resolver_ungrounded right article retrieved, resolver wrote steps it does not
                      support                                                    -> RESOLVER

The third one is the interesting case and the reason to run this before claiming anything. The
agent did nothing wrong; the knowledge base did. Provy attributes a failed condition to whichever
agent's trace carried the signal, so a case with no failing agent-owned signal has no honest
culprit. What Provy actually does there is the finding, not something to assert in advance.

⛔ THIS PACK DOES NOT RUN THE SHARED LEVER ENGINE. Every run is one of five named scenarios,
assigned in rotation, so a ten-session batch contains each case a known number of times. A demo
whose faults arrive by dice cannot answer "did it blame the right agent for THIS case".
"""
from __future__ import annotations

from typing import Any

from engine.types import (AgentSpec, Criterion, InjectedFault, LeverManifest,
                          RunContext, RunResult)
from engine.pack import BasePack

# The knowledge base this desk actually has. Article -> the category it covers.
KB = {
    "KB0010021": "email",
    "KB0010042": "vpn",
    "KB0010067": "password",
    "KB0010088": "printer",
    "KB0010105": "storage",
}
CATEGORY_ARTICLE = {v: k for k, v in KB.items()}

SYMPTOMS = {
    "email":    ("Outlook will not send", "Mail sits in the outbox and never leaves. Started this morning."),
    "vpn":      ("VPN drops every few minutes", "Client reconnects on its own but the session dies mid-call."),
    "password": ("Locked out after password change", "Changed the password yesterday, now nothing accepts it."),
    "printer":  ("Print jobs queue and never print", "Jobs stack up in the queue on the third floor device."),
    "storage":  ("Shared drive is read only", "Cannot save to the team share, says read only since Friday."),
}

GROUP = {"email": "Messaging", "vpn": "Network", "password": "Identity",
         "printer": "End User Compute", "storage": "Platform"}

# One rotation of scenarios, ten long. Every case appears exactly once in a ten-session batch, so
# a demo can be read by hand, and the clean runs outnumber any single failure.
#
# ⛔ THE CLEANS ARE NOT PADDING. A fleet where the same condition fails most of the time reads as
# broken rather than as a fleet worth watching, and tests/test_every_condition_can_fail.py enforces
# it at 75%. It also matters for the demo itself: four failures against six clean runs is evidence,
# four failures against nothing is a rigged screen.
ROTATION = [
    "clean",
    "kb_hallucinated",
    "clean",
    "kb_wrong_article",
    "clean",
    "kb_stale_article",
    "resolver_ungrounded",
    "sla_missed",
    "escalated",
    "clean",
]

# What each scenario breaks. True means the condition still holds. Absent keys default to holding.
_HOLDS = {"kb_article_valid": True, "procedure_grounded": True, "resolution_persists": True,
          "first_response_time_met": True, "self_resolved": True}


def _sc(story, blame, **broken):
    return {**_HOLDS, **broken, "blame": blame, "story": story}


SCENARIOS = {
    "clean": _sc("Right article, followed correctly, fix held.", None),
    "kb_hallucinated": _sc(
        "Knowledge agent cited an article that does not exist in the knowledge base.",
        "knowledge", kb_article_valid=False, resolution_persists=False),
    "kb_wrong_article": _sc(
        "Knowledge agent cited a real article written for a different symptom.",
        "knowledge", kb_article_valid=False, resolution_persists=False),
    "kb_stale_article": _sc(
        "Correct article retrieved and followed. The article's own steps are out of date.",
        None, resolution_persists=False),
    "resolver_ungrounded": _sc(
        "Correct article retrieved. The resolver wrote steps the article does not support.",
        "resolver", procedure_grounded=False, resolution_persists=False),
    # Two ordinary desk failures that have nothing to do with knowledge. They are here so the
    # knowledge findings are not the only thing that can go wrong on this fleet, which is what
    # makes "it named the Knowledge agent" mean something.
    "sla_missed": _sc("First response missed the response target. Nothing wrong with the fix.",
                      "triage", first_response_time_met=False),
    "escalated": _sc("Resolver could not finish it and handed off to another team.",
                     "resolver", self_resolved=False),
}


class ItsmKbPack(BasePack):
    workflow = "itsm_kb"
    session_type = "incident"
    owns_outcome = True

    def __init__(self) -> None:
        self._n = 0

    def agents(self) -> list[AgentSpec]:
        return [
            AgentSpec("triage", "Triage",
                      "Reads the incident and decides what it is and how urgent it is.", "🗂️", 0),
            AgentSpec("router", "Routing",
                      "Decides which support group should own the incident.", "🧭", 1),
            AgentSpec("knowledge", "Knowledge",
                      "Searches the knowledge base and returns the article the fix should follow.", "📚", 2),
            AgentSpec("resolver", "Resolution",
                      "Diagnoses the incident from the retrieved article, applies a fix, resolves the ticket.", "🛠️", 3),
            AgentSpec("reviewer", "Closure Check",
                      "Checks the resolution before it is committed and records what it expects to happen.", "✅", 4),
        ]

    def contract(self) -> list[Criterion]:
        """The live ITSM six, minus the two this desk has no clock for, plus the two knowledge ones.

        c5 and c6 are side 'both' deliberately. On the live ITSM fleet `procedure_grounded` is
        declared 'outcome', which means only ServiceNow could ever settle it, and ServiceNow does
        not report it, so it grades unmeasurable on every run forever. Groundedness is a property
        of the agent's own trace: the retrieved article and the written diagnosis are both in it.
        Declared 'both' it produces the Estimated-vs-Real pair instead of nothing.
        """
        return [
            Criterion("c1", "Incident is resolved with a genuine fix on first attempt, not "
                      "reopened or marked cannot-reproduce", "outcome",
                      "resolution_genuine", "eq", True),
            Criterion("c2", "First response is delivered within the agreed response time target",
                      "outcome", "first_response_time_met", "eq", True),
            Criterion("c3", "Incident stays resolved and is not reopened after closure", "outcome",
                      "resolution_persists", "eq", True),
            Criterion("c4", "Agent resolves the incident without escalation or handoff to another "
                      "team", "outcome", "self_resolved", "eq", True),
            Criterion("c5", "Diagnosis is grounded in the retrieved procedure and follows its "
                      "resolution steps", "both", "procedure_grounded", "eq", True),
            Criterion("c6", "The procedure the fix followed is a real article that covers this "
                      "symptom", "both", "kb_article_valid", "eq", True),
        ]

    def signal_owners(self) -> dict[str, str]:
        """Which agent's work decides each signal, and therefore who a failure is attributed to.

        The split this pack exists to test is the last two. Whether the ARTICLE was the right one
        is the knowledge agent's call. Whether the WRITTEN FIX followed it is the resolver's.
        Separating them is what lets a reopen name one or the other instead of always the resolver.

        ⛔ THERE IS NO OWNER FOR "THE ARTICLE ITSELF WAS WRONG" AND THAT IS THE POINT. No agent
        decides whether the knowledge base is accurate, so no agent should be named for it. The
        engine has no way to say that: an unowned signal falls back to the reviewer rather than to
        nobody. So the kb_stale_article scenario is left to fail on resolution_persists alone and
        whatever Provy names there is the honest measurement.
        """
        return {
            "first_response_time_met": "triage",
            "resolution_genuine":      "resolver",
            "resolution_persists":     "resolver",
            "self_resolved":           "resolver",
            "procedure_grounded":      "resolver",
            "kb_article_valid":        "knowledge",
        }

    def lever_manifest(self) -> LeverManifest:
        # Required by the interface. This pack does not run the shared lever engine: run_pipeline
        # is overridden and builds one named scenario per run instead.
        return LeverManifest(
            resolver_agent="resolver",
            retriever_agent="knowledge",
            reviewer_agent="reviewer",
            first_agent="triage",
            downstream_agent="resolver",
            correctness_signal="resolution_persists",
            policy_signal="procedure_grounded",
            sla_signal="first_response_time_met",
            drift_agent="resolver",
            other_signals={"kb_article_valid": "knowledge",
                           "resolution_genuine": "resolver",
                           "self_resolved": "resolver"},
        )

    def failure_cost(self) -> dict:
        # Same reasoning as the ITSM pack: a reopened incident costs rework, and inventing a rate
        # card is the exact thing the report exists to stop.
        return {}

    # ── work items ───────────────────────────────────────────────────────────
    def generate_work_item(self, rng) -> tuple[dict, dict]:
        scenario = ROTATION[self._n % len(ROTATION)]
        category = sorted(SYMPTOMS)[self._n % len(SYMPTOMS)]
        self._n += 1
        short, desc = SYMPTOMS[category]
        # ⛔ THE NUMBER IS RANDOM, NOT THE COUNTER. A counter gives every batch the same ten
        # incident numbers, so a second run collides with the first on entity_id and the outcome
        # push cannot tell which session it settles: /api/ingest/outcome answers
        # `sessionId: null, status: held_awaiting_prediction` and the work item never reconciles.
        # Measured here on 18 Aug: 20 sessions, 0 reconciled, every cause undetermined. The
        # scenario rotation still runs off the counter, so the batch shape stays deterministic.
        item = {
            "id": f"INC{rng.randint(1000000, 9999999)}",
            "category": category,
            "priority": rng.choice(["2", "3", "3", "4"]),
            "short_description": short,
            "description": desc,
        }
        gt = {"category": category, "scenario": scenario,
              "correct_article": CATEGORY_ARTICLE[category],
              **{k: v for k, v in SCENARIOS[scenario].items() if k != "story"}}
        return item, gt

    # ── the run ──────────────────────────────────────────────────────────────
    def run_pipeline(self, item: dict, gt: dict, ctx: RunContext) -> RunResult:
        """One named scenario, start to finish. No levers: see the module docstring."""
        r = self.build_clean_run(item, gt, ctx)
        self.stamp_estimated(r, "reviewer", r.confidence)
        return r

    def build_clean_run(self, item: dict, gt: dict, ctx: RunContext) -> RunResult:
        scenario = gt["scenario"]
        spec = SCENARIOS[scenario]
        r = self.base_result(item)
        eid = r.entity_id
        A = {a.name: a for a in self.agents()}
        correct = gt["correct_article"]

        # What the knowledge agent actually returned, per scenario.
        if scenario == "kb_hallucinated":
            cited, cited_covers, exists = "KB0019994", None, False
        elif scenario == "kb_wrong_article":
            other = [c for c in sorted(CATEGORY_ARTICLE) if c != gt["category"]][0]
            cited, cited_covers, exists = CATEGORY_ARTICLE[other], other, True
        else:
            cited, cited_covers, exists = correct, gt["category"], True

        r.traces.append(self.agent_step(
            ctx, A["triage"], item,
            decision=f"category={gt['category']}, priority={item['priority']}", entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["router"], item,
            decision=f"assigned to {GROUP[gt['category']]}", entity_id=eid))

        # The retrieval itself, as the tool call it is. A demo that cannot show WHICH article was
        # cited cannot attribute a knowledge failure to anything.
        r.traces.append(self.tool_step(
            ctx, A["knowledge"], "kb_search",
            tool_input={"query": item["short_description"], "category": gt["category"]},
            tool_output={"article_id": cited, "exists_in_kb": exists,
                         "covers_category": cited_covers,
                         "title": f"Resolving {cited_covers or 'unknown'} incidents",
                         "last_reviewed": "2023-02-11" if scenario == "kb_stale_article" else "2026-07-30"},
            entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["knowledge"], item,
            decision=(f"cited {cited}" + ("" if exists else " (no such article)")),
            entity_id=eid,
            payload_extra={"article_id": cited, "article_exists": exists}))

        if scenario == "resolver_ungrounded":
            fix = (f"Followed {cited} in name only: rebuilt the user profile, which {cited} "
                   f"does not mention and does not support.")
        else:
            fix = f"Applied the resolution steps in {cited} for the reported {gt['category']} fault."
        r.traces.append(self.agent_step(
            ctx, A["resolver"], item, decision=fix, entity_id=eid,
            payload_extra={"followed_article": cited, "close_code": "Solved (Permanently)"}))
        r.traces.append(self.agent_step(
            ctx, A["reviewer"], item,
            decision="approved for closure", entity_id=eid))

        # Estimated is what the desk BELIEVED on closing. It always believes the fix holds: an
        # agent that predicted its own reopen would not have closed the ticket.
        r.estimated_signals.update({
            # The desk knows at closing time whether it answered late and whether it handed the
            # ticket off: both are its own actions, so it claims them honestly.
            "first_response_time_met": spec["first_response_time_met"],
            "resolution_genuine": True,
            "resolution_persists": True,
            "self_resolved": spec["self_resolved"],
            "procedure_grounded": spec["procedure_grounded"],
            "kb_article_valid": spec["kb_article_valid"],
        })
        # Real is what happened. Only the reopen and the two knowledge checks move.
        r.real_signals.update({
            "first_response_time_met": spec["first_response_time_met"],
            "resolution_genuine": spec["resolution_persists"],
            "resolution_persists": spec["resolution_persists"],
            "self_resolved": spec["self_resolved"],
            "procedure_grounded": spec["procedure_grounded"],
            "kb_article_valid": spec["kb_article_valid"],
        })
        held = spec["resolution_persists"] and spec["self_resolved"] and spec["first_response_time_met"]
        r.outcome_label = "success" if held else "fail"
        r.outcome_value = 1.0 if held else 0.0
        r.confidence = 0.91 if scenario == "clean" else 0.86
        r.terminal_reason = "resolved"
        r.metadata = {
            "estimated_success": True,      # the desk always closes believing it worked
            "scenario": scenario,
            "expected_blame": spec["blame"] or "nobody",
            "cited_article": cited,
            "correct_article": correct,
            "system_of_record": "simulated_itsm",
        }
        if spec["blame"] or not held:  # noqa: E501 - ground truth for the report
            r.faults.append(InjectedFault(
                lever=scenario, agent=spec["blame"],
                dimension="knowledge" if scenario.startswith("kb_") else "resolution",
                params={"cited_article": cited, "correct_article": correct}))

        r.evals = [
            self.eval_pass("triage", "classification_confidence", eid, "category matches the symptom"),
            self.eval_pass("router", "routing_confidence", eid, f"{GROUP[gt['category']]} owns this fault"),
            self.eval_pass("knowledge", "retrieval_relevance", eid,
                           "cited article exists and covers the symptom" if spec["kb_article_valid"]
                           else "cited article does not cover this symptom",
                           score=0.92 if spec["kb_article_valid"] else 0.31),
            self.eval_pass("resolver", "resolution_completeness", eid,
                           "written fix follows the cited article" if spec["procedure_grounded"]
                           else "written fix is not supported by the cited article",
                           score=0.9 if spec["procedure_grounded"] else 0.28),
            self.eval_pass("reviewer", "closure_check", eid, "resolution recorded and closed"),
        ]
        return r
