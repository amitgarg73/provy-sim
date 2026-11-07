"""Read one Servicely agent run and turn it into a RunResult the emitter already knows how to send.

WHY THIS EXISTS. Servicely streams no traces. Its agents leave a run record in ordinary tables on the
instance (conversation, activity log, model usage, hand-off, journal), joined on the ticket id. This reads
that record, read-only, and hands it to engine/emitter.ProvyEmitter, so there is ONE ingest path and the
outcome the instance's own trigger already pushed settles against the run as soon as it is graded.

WHAT IT READS (all GET, nothing is written to the instance):
  Incident, User (assignee) -> SystemAgentPersona (which agent ran), SystemAgentConversationHistory
  (RelatedRecord = "<ticket id>:Incident"), SystemAIActivityLog (Conversation), SystemLLMUsage
  (Conversation), SystemAgentConversationMessage (Conversation), SystemTicketHandoff (RelatedRecord).

THE CLAIM IS DERIVED, AND SAID SO. The agent never states "I expect this to hold" in a field. The estimated
signals are read off what it did: it cited an article it loaded, it posted that article's steps to the
customer, it opened a hand-off or it did not. `claim_source` on the session says so. A reader that invented
a confident claim would be grading its own guess.

Filtered queries on this platform return 404 when nothing matches, so a 404 on a list is an empty list.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from engine.types import RunResult, TraceStep


def _filter(field: str, value: str) -> str:
    return urllib.parse.quote(json.dumps({"and": [{"fieldName": field, "operator": "=", "value": value}]}))


def _utc(stamp: str) -> str:
    return datetime.fromisoformat(stamp).astimezone(timezone.utc).isoformat()


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "agent"


def _failed(summary: str) -> bool:
    s = summary or ""
    return bool(re.search(r'"success"\s*:\s*false|"error"\s*:|not found|Parameter specified as non-null', s, re.I))


def make_getter(instance: str, token: str) -> Callable[[str], Any]:
    base = instance.rstrip("/")
    if not base.startswith("http"):
        base = "https://" + base

    def get(path: str) -> Any:
        req = urllib.request.Request(base + path, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)["data"]
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []
            raise

    return get


def read_run(get: Callable[[str], Any], number: str) -> Optional[RunResult]:
    """The first agent run on a ticket, or None when no agent has touched it."""
    rows = get(f"/v1/Incident?limit=1&query={_filter('Number', number)}")
    if not rows:
        return None
    inc = rows[0]
    iid = inc["id"]
    convs = sorted(get(f"/v1/SystemAgentConversationHistory?limit=10&query={_filter('RelatedRecord', iid + ':Incident')}"),
                   key=lambda c: c["CreatedOn"])
    if not convs:
        return None
    conv = convs[0]

    agent = "agent"
    assignee = inc.get("Assignee")
    if assignee:
        u = get(f"/v1/User/{assignee}")
        u = u[0] if isinstance(u, list) else u
        if u and u.get("AgentPersona"):
            p = get(f"/v1/SystemAgentPersona/{u['AgentPersona']}")
            p = p[0] if isinstance(p, list) else p
            agent = _slug(p.get("Name", "agent"))

    acts = sorted(get(f"/v1/SystemAIActivityLog?limit=200&query={_filter('Conversation', conv['id'])}"), key=lambda a: a["CreatedOn"])
    llm = sorted(get(f"/v1/SystemLLMUsage?limit=100&query={_filter('Conversation', conv['id'])}"), key=lambda x: x["CreatedOn"])
    msgs = sorted(get(f"/v1/SystemAgentConversationMessage?limit=100&query={_filter('Conversation', conv['id'])}"), key=lambda m: m["CreatedOn"])
    handoffs = get(f"/v1/SystemTicketHandoff?limit=10&query={_filter('RelatedRecord', iid + ':Incident')}")

    steps: list[TraceStep] = []
    li = 0
    for a in acts:
        kind, key, out = a["EntryType"], a.get("ToolOrAgentKey"), (a.get("OutputSummary") or "")
        if kind == "reasoning_step":
            step = TraceStep(agent=agent, step_type="agent_message", outcome="ok", payload_extra={"source_row_id": a["id"]},
                             agent_reasoning=re.sub(r"\s*\[requested tools:[^\]]*\]", "", out).strip() or (f"requested: {key}" if key else None))
            if li < len(llm):
                u = llm[li]; li += 1
                step.model = u.get("Model")
                step.tokens_input = int(u.get("InputTokens") or 0)
                step.tokens_output = int(u.get("OutputTokens") or 0)
                step.cost_usd = float(u.get("ApproximateCost") or 0.0)
                step.latency_ms = int(u.get("TotalTimeMilliseconds") or 0)
            steps.append(step)
        elif kind in ("tool_invocation", "context_fetch"):
            bad = _failed(out)
            steps.append(TraceStep(agent=agent, step_type="tool_call", outcome="error" if bad else "ok", tool_name=key, payload_extra={"source_row_id": a["id"]},
                                   tool_output={"summary": out[:1500]}, error=out[:300] if bad else None))
        else:   # risk_policy_evaluated, escalation: a decision the platform made about the agent's action
            steps.append(TraceStep(agent=agent, step_type="decision", outcome="ok", tool_name=key, payload_extra={"source_row_id": a["id"]},
                                   agent_reasoning=f"{kind}: {out}"[:400] if out else kind))

    keys = [s.tool_name for s in steps if s.step_type == "tool_call" and s.outcome == "ok"]
    loaded = "tool_load_knowledge_article" in keys
    posted = "tool_itsm_update_client_journal" in keys
    handed = bool(handoffs)
    est = {
        "kb_article_valid": loaded,
        "procedure_followed": loaded and posted,
        "close_code": "Solution provided" if (loaded and posted and not handed) else "No resolution",
        "reopen_count": 0,
        "reassignment_count": 1 if handed else 0,
    }
    est_ok = loaded and posted and not handed   # a solution claim needs the article it came from
    confidence = 0.9 if est_ok else 0.3

    # The claim rides on the span that made it, the way the simulator carries it (`provy_claim`, argus#747),
    # and as a plain payload key so the estimated side of each condition reads it. The closing message is the
    # span where the agent states what it did, so it owns the claim.
    closing = next((s for s in reversed(steps) if s.step_type == "agent_message"), None)
    if closing is not None:
        for signal, value in est.items():
            closing.payload_extra[signal] = value
            closing.payload_extra.setdefault("provy_claim", []).append(
                {"signal": signal, "value": value, "confidence": confidence, "entity_id": number})
        closing.payload_extra["confidence"] = confidence
    final = next((m["MessageContent"] for m in reversed(msgs)
                  if m["MessageType"] == "ai_reply" and not m["MessageContent"].startswith("[requested")), "")

    return RunResult(
        entity_id=number,
        session_type="incident",
        session_id=f"servicely-{number}-{conv['id'][:8]}",
        traces=steps,
        terminal_reason="resolved" if est_ok else "escalated",
        estimated_signals=est,
        confidence=confidence,
        metadata={
            "system_of_record": "servicely",
            "servicely_id": iid,
            "run_source": "servicely_agent_log",
            "claim_source": "derived from the run record: article loaded, answer posted, hand-off opened or not",
            "estimated_success": est_ok,
            "agent_persona": agent,
            "final_reply": re.sub(r"\s+", " ", final)[:600],
            "runs_on_ticket": len(convs),
        },
    )
