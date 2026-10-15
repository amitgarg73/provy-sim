"""Domain pack: an AI service desk working REAL incidents in a Servicely instance.

The Servicely counterpart of packs/itsm. The incidents are real records, the agent's routing and resolution
are real writes, and the outcome is settled by Servicely's own lifecycle and pushed to Provy by the
instance (servicely/outcome_push_trigger.js). This pack reports NOTHING about how a ticket turned out.

What differs from the ITSM pack, and why:
  * The record API refuses WorkflowStatus, so the resolver writes ClosureCode and ClosureNotes and an
    instance trigger moves the ticket (servicely/resolve_trigger.js). The write IS the agent's decision.
  * Only two usable groups exist on the demo instance (Support, Network Operations), so routing is a
    two-way choice and a misroute is always the other group.
  * No SLA signals: nothing on the instance settles a response or resolution time.
  * The pack raises its own incidents (marker prefix) instead of reading a pre-seeded queue.
It subclasses ItsmPack for the agent's judgement (_decide, classify) and the context manifests, and
overrides everything that names a ServiceNow field.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

from engine.pack import BasePack  # noqa: F401  (documents the interface this class fulfils)
from engine.servicely import ServicelyClient, ServicelyError, client_from_env
from engine.types import AgentSpec, Criterion, RunContext, RunResult, Wait

from packs.itsm.pack import ItsmPack, WriteRejected, KB_ARTICLE

from .pipeline import conditions, roster

# Category the agent decides on -> the group that should own it, on THIS instance.
SV_GROUP = {"network": "Network Operations", "database": "Support", "software": "Support",
            "hardware": "Support", "inquiry": "Support", "password_reset": "Support"}
OTHER_GROUP = {"Support": "Network Operations", "Network Operations": "Support"}
_URGENCY_WORD = {"1": "high", "2": "medium", "3": "low"}

# Symptom texts, each written to point at exactly one category for the agent's keyword classifier.
SYMPTOMS = {
    "network": [("VPN drops every hour for the whole floor", "Users on level 3 lose their VPN connection about hourly and must reconnect."),
                ("Wi-Fi in the east wing very slow", "Wireless latency is high in the east wing and video calls break up."),
                ("Cannot reach internal sites, DNS failing", "Internal hostnames do not resolve from the new branch office network.")],
    "database": [("Oracle report job hangs overnight", "The nightly Oracle reporting query never finishes and holds a deadlock."),
                 ("SQL replica lag growing on the orders database", "The read replica is minutes behind and the tablespace is nearly full."),
                 ("DB2 connection pool exhausted", "The application cannot get a database connection after 9am.")],
    "software": [("Outlook keeps crashing on startup", "The email client closes straight after the splash screen since the patch."),
                 ("Excel licence message after Windows update", "A licence error appears when opening workbooks after the operating system patch."),
                 ("Install of the design software fails", "The installer stops at 80 percent with a software error.")],
    "hardware": [("Laptop battery drains in an hour", "The battery lasts about an hour and the docking station does not charge it."),
                 ("External monitor flickers", "The monitor flickers on the docking station and the cable has been swapped."),
                 ("Keyboard keys unresponsive", "Several keys on the keyboard do not register.")],
    "password_reset": [("Locked out of my account after travel", "My account locked and I cannot sign in, please reset my password."),
                       ("MFA prompt never arrives", "I cannot sign in because the MFA code does not arrive."),
                       ("Password expired while on leave", "I need my password reset, I am locked out of everything.")],
    "inquiry": [("How do I request access to the shared drive?", "A question about guidance for requesting access for a new starter."),
                ("Who owns the expense approval process?", "I have a question: who owns the process and where is the guidance?")],
}


class ServicelyPack(ItsmPack):
    workflow = "servicely"
    session_type = "incident"
    owns_outcome = False

    def __init__(self, client: Optional[ServicelyClient] = None):
        super().__init__(None)
        self._sv_client = client
        self._directory: Optional[dict] = None
        self._truth: dict[str, dict] = {}
        import random
        self._rng = random.Random()

    # ── the system of record ────────────────────────────────────────────────
    @property
    def client(self) -> ServicelyClient:  # type: ignore[override]
        if self._sv_client is None:
            self._sv_client = client_from_env()
        return self._sv_client

    def agents(self) -> list[AgentSpec]:
        return [AgentSpec(*row) for row in roster()]

    def contract(self) -> list[Criterion]:
        return [Criterion(*row) for row in conditions()]

    def retired_roster(self) -> list[AgentSpec]:
        return []

    def signal_owners(self) -> dict[str, str]:
        return {
            "resolution_genuine": "resolver", "resolution_persists": "resolver", "self_resolved": "resolver",
            "procedure_followed": "resolver", "reopen_count": "resolver", "close_code": "resolver",
            "reassignment_count": "router", "kb_article_valid": "knowledge",
        }

    # ── work items: the pack raises its own incidents ───────────────────────
    def _ids(self) -> dict:
        """Group, user and classification ids by name, read once. Names come from the instance, never from this file."""
        if self._directory is None:
            groups = {g["Name"]: g["id"] for g in self.client.query("Group", {}, page_size=100)}
            missing = [n for n in ("Support", "Network Operations") if n not in groups]
            if missing:
                raise ServicelyError(f"the instance has no group named {missing}: routing has nowhere to send work")
            users = [u for u in self.client.query("User", {}, page_size=100)
                     if "@example.com" in str(u.get("Email") or "")]
            # The user the tickets are raised as. SERVICELY_REQUESTOR_EMAIL names one; unset means the first example user.
            want = os.environ.get("SERVICELY_REQUESTOR_EMAIL", "").strip().lower()
            named = [u for u in self.client.query("User", {}, page_size=100)
                     if want and str(u.get("Email") or "").lower() == want]
            cls = [c for c in self.client.query("Classification", {}, page_size=100) if c.get("Name") == "Other"]
            if not users or not cls:
                raise ServicelyError("the instance has no example users or no 'Other' classification to raise incidents with")
            self._directory = {"groups": groups, "users": users, "classification": cls[0]["id"],
                               "requestor": (named or users)[0]["id"]}
        return self._directory

    def generate_work_item(self, rng) -> tuple[dict, dict]:
        ids = self._ids()
        category = rng.choice(sorted(SYMPTOMS))
        short, desc = rng.choice(SYMPTOMS[category])
        who = rng.choice(ids["users"])["id"]
        rec = self.client.create_incident(short, ids["requestor"], who, ids["classification"],
                                          ids["groups"]["Support"], description=desc)
        number = rec["Number"]
        opened = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        item = {"id": number, "rec_id": rec["id"], "short_description": short, "description": desc,
                "opened_at": opened}
        # The answer key stays here, in the pack's own process. The agent's decision reads `item`, never this.
        gt = {"category": category, "group": SV_GROUP[category]}
        self._truth[number] = gt
        return item, gt

    def try_generate_work_item(self):
        """The desk asks for work without waiting. This pack raises its own incidents, so there is always some.
        Its own rng: the agent's behaviour runs on the run's seeded rng, the choice of symptom does not need to."""
        return self.generate_work_item(self._rng)

    # ── the agent's judgement: ItsmPack's, mapped onto this instance's two groups ──
    def _decide(self, item: dict, ctx: RunContext) -> dict:
        d = super()._decide(item, ctx)
        right = SV_GROUP[d["category"]]
        d["recommended_group"] = OTHER_GROUP[right] if d["misrouted"] else right
        return d

    # ── the journey ─────────────────────────────────────────────────────────
    def _sv_step(self, ctx: RunContext, agent: AgentSpec, tool: str, rec_id: str, patch: dict,
                 entity_id: str, extra_output: Optional[dict] = None):
        """One real write, recorded as the tool call it is. A refusal fails THIS ticket and is shown as one."""
        try:
            updated = self.client.update("Incident", rec_id, patch)
        except ServicelyError as e:
            step = self.tool_step(ctx, agent, tool, {"id": rec_id, **patch},
                                  {"rejected": True, "detail": str(e)[:400]}, entity_id)
            step.outcome = "error"
            step.error = str(e)[:400]
            raise WriteRejected(step) from None
        ws = updated.get("WorkflowStatus")
        out = {"number": updated.get("Number", entity_id),
               "state": ws.get("value") if isinstance(ws, dict) else ws}
        out.update(extra_output or {})
        return self.tool_step(ctx, agent, tool, {"id": rec_id, **patch}, out, entity_id)

    def journey(self, item: dict, gt: dict, ctx: RunContext):
        d = self._decide(item, ctx)
        r = RunResult(entity_id=self.entity_id(item), session_type=self.session_type,
                      session_id=self.session_id(item), real_signals={}, outcome_label="skipped",
                      outcome_value=None, confidence=d["confidence"])
        try:
            return (yield from self._work_ticket(r, item, d, ctx))
        except WriteRejected as rejected:
            r.traces.append(rejected.step)
            r.terminal_reason = "tool_error"
            r.estimated_signals = {}
            r.metadata = {"system_of_record": "servicely", "servicely_id": item["rec_id"],
                          "outcome_source": "servicely_push", "estimated_success": False,
                          "rejected_by_system_of_record": rejected.step.error}
            return r

    def _work_ticket(self, r: RunResult, item: dict, d: dict, ctx: RunContext):
        A = {a.name: a for a in self.agents()}
        eid, rec = r.entity_id, item["rec_id"]
        rng = ctx.rng
        groups = self._ids()["groups"]
        truth = self._truth.get(eid, {})

        yield Wait("waiting_to_be_picked_up", rng.uniform(1.0, 4.0), cause=None)

        # 1. Triage
        r.traces.append(self.agent_step(
            ctx, A["triage"], item,
            decision=(f"category={d['category']}, urgency={d['urgency']}, impact={d['impact']} "
                      f"(text {'clearly indicated one area' if d['unambiguous'] else 'was ambiguous'})"),
            entity_id=eid, payload_extra={"predicted_category": d["category"],
                                          "predicted_priority": d["predicted_priority"]}))
        r.traces.append(self._sv_step(
            ctx, A["triage"], "servicely.update_incident", rec,
            {"Impact": _URGENCY_WORD.get(d["impact"], "medium"), "Urgency": _URGENCY_WORD.get(d["urgency"], "medium")},
            eid))

        # 2. Routing: a rules lookup (no model). A group the instance does not have is an error, not a guess.
        group = d["recommended_group"]
        r.traces.append(self._sv_step(ctx, A["router"], "servicely.assign_incident", rec,
                                      {"AssignmentGroup": groups[group]}, eid,
                                      extra_output={"assignment_group": group}))
        landed_wrong = bool(truth) and group != truth.get("group")
        if landed_wrong:
            # Somebody notices and sends it where it belongs: the cost of a misroute is the handoff.
            yield Wait("sat_in_wrong_group", rng.uniform(2.0, 6.0), cause="routed to the wrong group")
            right = truth["group"]
            r.traces.append(self._sv_step(ctx, A["router"], "servicely.reassign_incident", rec,
                                          {"AssignmentGroup": groups[right]}, eid,
                                          extra_output={"assignment_group": right, "reassigned": True}))

        # 3. Knowledge: a read, not a write.
        r.traces.append(self.tool_step(
            ctx, A["knowledge"], "kb_search", {"query": item["short_description"], "predicted_category": d["category"]},
            {"article": d["cited_article"], "match_score": 0.92 if d["article_valid"] else 0.31}, eid))
        r.traces.append(self.agent_step(
            ctx, A["knowledge"], item,
            decision=f"cite {d['cited_article']} for {d['category']}", entity_id=eid,
            payload_extra={"article": d["cited_article"], "article_valid": d["article_valid"]}))

        # 4. Resolution: the write that the instance's trigger turns into a state change.
        r.traces.append(self.agent_step(
            ctx, A["resolver"], item,
            decision=f"approach={d['approach']} ({'shallow' if d['shallow'] else 'followed the procedure'})",
            entity_id=eid, payload_extra={"approach": d["approach"], "close_code": d["close_code"]}))
        r.traces.append(self._sv_step(
            ctx, A["resolver"], "servicely.resolve_incident", rec,
            {"ClosureCode": d["close_code"], "ClosureNotes": f"[AI resolution] {d['approach']}, article {d['cited_article']}."},
            eid, extra_output={"close_code": d["close_code"]}))

        # 5. Closure check: the claim set, in the contract's vocabulary.
        forecasts = {"predicted_category": d["category"], "recommended_group": group,
                     "predicted_close_code": d["close_code"], "predicted_reopen_risk": d["reopen_risk"],
                     "agent_confidence": d["confidence"]}
        r.traces.append(self.agent_step(
            ctx, A["reviewer"], item,
            decision=f"resolution accepted; reopen risk {d['reopen_risk']}",
            entity_id=eid, payload_extra=dict(forecasts)))
        r.estimated_signals = {
            "reopen_count": 0 if d["reopen_risk"] == "low" else 1,
            "close_code": d["close_code"],
            "reassignment_count": 1,
            "kb_article_valid": d["article_valid"],
            "procedure_followed": not d["shallow"],
        }
        r.metadata = {
            "forecasts": forecasts,
            "estimated_success": d["reopen_risk"] == "low" and d["close_code"] in (
                "Solution provided", "Resolved by change", "Resolved by problem", "Workaround provided"),
            "system_of_record": "servicely", "servicely_id": rec, "outcome_source": "servicely_push",
        }
        self._attach_context(r, item, d, ctx)
        r.evals = [
            self.eval_pass("triage", "classification_confidence", eid,
                           f"classified as {d['category']} from the incident text",
                           score=0.9 if d["unambiguous"] else 0.72),
            self.eval_pass("router", "routing_confidence", eid, f"{group} owns {d['category']} incidents", score=0.88),
            self.eval_pass("knowledge", "retrieval_relevance", eid,
                           f"{d['cited_article']} covers {d['category']} incidents" if d["article_valid"]
                           else f"{d['cited_article']} does not cover this symptom",
                           score=0.9 if d["article_valid"] else 0.3),
            self.eval_pass("resolver", "resolution_completeness", eid,
                           f"resolution recorded with close code '{d['close_code']}'",
                           score=0.9 if d["approach"] in ("root_cause_fix", "change_applied") else 0.74),
            self.eval_pass("reviewer", "closure_check", eid,
                           "resolution and close notes are present and consistent", score=0.91),
        ]
        self.stamp_estimated(r, self.lever_manifest().reviewer_agent, d["confidence"])
        r.terminal_reason = "resolved"
        return r
