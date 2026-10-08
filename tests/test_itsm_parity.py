"""The ITSM pack sends what the current product expects (argus#1649): a context manifest on every step that runs a model, a
code-only router, claims with a stated confidence, and a retired agent for a roster with history.

Run against a fake instance, like tests/test_itsm_pack.py. What is NOT changed is as important as what is added, so the grading
(the contract, the estimated signals, the forecasts, the outcome the simulation never reports) is held equal with the new parts on and off.
"""
from __future__ import annotations

import json
import random

import pytest

from conftest import make_ctx
from engine import context as C
from engine.emitter import ProvyEmitter
from engine.levers import LeverConfig
from engine.servicenow import DEFAULT_MARKER, _marker_from_env
from packs.itsm.pack import (CODE_ONLY_AGENTS, CONTEXT_AGENTS, CONTEXT_SOURCES, DECLARED_APPROVED,
                             DECLARED_LIMIT_DAYS, KB_ARTICLE, ItsmPack)
from scripts.seed_itsm_incidents import build_incident
from test_itsm_pack import FakeServiceNow

N = 40


class Desk(FakeServiceNow):
    """A fake instance whose queue empties as tickets are worked, the way the real one does."""

    def open_demo_incidents(self, limit=25):
        return [dict(i) for i in self.incidents if str(i.get("state", "1")) == "1"][:limit]


def tickets(n=N, seed=5):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        b = build_incident(rng, [], "spread")
        out.append({"sys_id": f"s{i}", "number": f"INC00200{i:02d}", "state": "1", "category": "inquiry", "priority": "3",
                    "impact": b["impact"], "urgency": b["urgency"], "contact_type": b["contact_type"],
                    "short_description": b["short_description"], "description": b["description"],
                    "opened_at": "2026-10-07 12:00:00"})
    return out


def run_all(pack=None, n=N, seed=11, rates=None):
    pack = pack or ItsmPack(client=Desk(tickets(n)))
    out = []
    for i in range(n):
        ctx = make_ctx(levers=LeverConfig(rates or {}), seed=seed + i, index=i, workflow="itsm")
        item, gt = pack.generate_work_item(random.Random(seed + i))
        out.append((item, pack.run_pipeline(item, gt, ctx)))
    return pack, out


@pytest.fixture(scope="module")
def batch():
    return run_all()


def model_steps(r):
    return [t for t in r.traces if t.step_type == "agent_message" and t.model is not None]


# ── a manifest on every step that ran a model ───────────────────────────────────────────────────
def test_every_model_step_carries_a_manifest_and_nothing_else_does(batch):
    _, runs = batch
    seen = 0
    for _, r in runs:
        for t in r.traces:
            if t.step_type == "agent_message" and t.model is not None:
                assert t.context is not None, f"{t.agent} ran a model and sent no record"
                seen += 1
            else:
                assert t.context is None, f"{t.agent}/{t.step_type} ran no model and must send no record"
    assert seen >= N * 4, "triage, knowledge, resolver and reviewer on every ticket"


def test_the_agents_that_are_given_context_are_the_four_that_run_a_model(batch):
    _, runs = batch
    for _, r in runs:
        assert {t.agent for t in model_steps(r)} == set(CONTEXT_AGENTS)


def test_the_router_is_code_only_on_every_step_it_takes(batch):
    _, runs = batch
    router_steps = [t for _, r in runs for t in r.traces if t.agent == "router" and t.step_type == "agent_message"]
    assert len(router_steps) >= N
    for t in router_steps:
        assert (t.model, t.tokens_input, t.tokens_output, t.cost_usd, t.context, t.system, t.user) == (None, 0, 0, 0.0, None, None, None)
        assert t.agent_reasoning.startswith("Rules table:")
    assert CODE_ONLY_AGENTS == ("router",)


def test_the_router_step_still_says_where_it_sent_the_ticket(batch):
    _, runs = batch
    for _, r in runs:
        first = next(t for t in r.traces if t.agent == "router" and t.step_type == "agent_message")
        assert first.payload_extra["recommended_group"] and first.outcome.startswith("assign to ")


# ── the manifest is honest and holds no text ────────────────────────────────────────────────────
def test_the_manifest_names_only_declared_sources_and_every_document_is_inside_the_limit(batch):
    _, runs = batch
    for _, r in runs:
        for t in model_steps(r):
            m = t.context
            assert m["retrieval"]["returned"] == len(m["items"]) and m["items"]
            assert m["instruction"]["version"] == f"{t.agent}-v1" and len(m["instruction"]["hash"]) > 20
            for it in m["items"]:
                assert it["source"] in CONTEXT_SOURCES and it["source"] in DECLARED_APPROVED
                assert set(it) >= {"kind", "source", "id", "as_of", "used", "hash", "score", "version", "tokens"}
    assert DECLARED_LIMIT_DAYS == 30


def test_a_document_is_never_older_than_the_declared_limit():
    pack, runs = run_all(n=20)
    ctx_now = make_ctx().now
    for _, r in runs:
        for t in model_steps(r):
            for it in t.context["items"]:
                if it["kind"] == "document":
                    age_days = (ctx_now - C.parse_iso(it["as_of"])).total_seconds() / 86400
                    assert 0 <= age_days <= DECLARED_LIMIT_DAYS - 1


def test_the_incident_record_is_the_ticket_the_agent_is_working(batch):
    _, runs = batch
    for item, r in runs:
        for t in model_steps(r):
            rec = [i for i in t.context["items"] if i["source"] == "incident-record"]
            if t.agent in ("triage", "resolver", "reviewer"):
                assert [i["id"] for i in rec] == [r.entity_id] and rec[0]["kind"] == "tool_result"


def test_the_same_article_has_the_same_fingerprint_everywhere_it_is_given(batch):
    _, runs = batch
    seen = {}
    for _, r in runs:
        for t in model_steps(r):
            for it in t.context["items"]:
                if it["source"] == "itsm-knowledge-base":
                    seen.setdefault((it["id"], it["version"]), set()).add(it["hash"])
    assert seen and all(len(h) == 1 for h in seen.values())


def test_when_the_cited_article_does_not_exist_the_model_was_handed_a_real_one_that_covers_something_else():
    _, runs = run_all(n=200, rates={})
    bad = 0
    for _, r in runs:
        kb = next(t for t in r.traces if t.tool_name == "kb_search")
        if kb.tool_output["exists_in_kb"]:
            continue
        bad += 1
        cited = kb.tool_output["article_id"]
        for t in model_steps(r):
            ids = [i["id"] for i in t.context["items"] if i["source"] == "itsm-knowledge-base"]
            assert cited not in ids, "an id the knowledge base does not hold was never retrieved"
            assert all(i in KB_ARTICLE.values() for i in ids)
        first = next(t for t in model_steps(r) if t.agent == "knowledge").context["items"][0]
        assert first["score"] < 0.5
    assert bad >= 3, "the bad-article path was not exercised"


def test_no_incident_text_reaches_a_manifest_or_the_wire_as_context(batch):
    pack, runs = batch
    em = ProvyEmitter(ingest_key="", base_url="https://dev.provy.ai", capture=True)
    item, r = runs[0]
    em.emit_run(r, pack.agents())
    sent = [p for p in em.sent if p["path"] == "/api/ingest/trace" and "context" in p["payload"]]
    assert len(sent) == len(model_steps(r))
    blob = json.dumps([p["payload"]["context"] for p in sent])
    for text in (item["short_description"], item["description"]):
        assert text not in blob
    assert all("model" not in p["payload"] for p in em.sent if p["payload"].get("agent") == "router" and p["path"] == "/api/ingest/trace" and p["payload"].get("step_type") == "agent_message")


# ── nothing about the grading moved ─────────────────────────────────────────────────────────────
def test_switching_the_new_parts_off_gives_the_same_forecasts_signals_and_outcome():
    on = ItsmPack(client=Desk(tickets(12)))
    off = ItsmPack(client=Desk(tickets(12)))
    off.send_context, off.code_router = False, False
    _, a = run_all(on, 12)
    _, b = run_all(off, 12)
    for (_, ra), (_, rb) in zip(a, b):
        assert ra.estimated_signals == rb.estimated_signals
        assert ra.metadata["forecasts"] == rb.metadata["forecasts"]
        assert (ra.real_signals, ra.outcome_label, ra.outcome_value) == ({}, "skipped", None) == (rb.real_signals, rb.outcome_label, rb.outcome_value)
        assert [t.context for t in rb.traces] == [None] * len(rb.traces)
        assert any(t.agent == "router" and t.model for t in rb.traces), "with the switch off the router is a model step again"


def test_the_pack_still_reports_no_outcome_of_its_own(batch):
    _, runs = batch
    for _, r in runs:
        assert r.real_signals == {} and r.outcome_label == "skipped" and r.metadata["outcome_source"] == "servicenow_push"


# ── claims with a stated confidence ─────────────────────────────────────────────────────────────
def test_every_ticket_states_its_claims_with_a_confidence(batch):
    pack, runs = batch
    confs = []
    for _, r in runs:
        rec = pack.truth_record(r)
        assert rec["claims"], "a ticket with no claim"
        for c in rec["claims"]:
            assert isinstance(c["confidence"], float) and 0.3 < c["confidence"] <= 0.97
            confs.append(c["confidence"])
        assert {c["signal"] for c in rec["claims"]} >= {"first_response_time_met", "resolution_time_met", "procedure_followed", "kb_article_valid"}
    assert len({round(c, 1) for c in confs}) >= 4, "the stated confidences should spread across the table"


def test_a_claim_sits_on_the_step_of_the_agent_that_owns_its_signal(batch):
    pack, runs = batch
    owners = pack.signal_owners()
    for _, r in runs:
        for c in pack.truth_record(r)["claims"]:
            if c["signal"] in owners:
                assert c["agent"] == owners[c["signal"]] or c["agent"] == "reviewer"


# ── the truth record and the roster ─────────────────────────────────────────────────────────────
def test_the_truth_record_says_what_was_sent(batch):
    pack, runs = batch
    for _, r in runs:
        rec = pack.truth_record(r, "2026-10-07T12:00:00Z")
        assert rec["entity_id"] == r.entity_id and rec["session_id"] == r.session_id
        assert rec["code_only_agents"] == ["router"]
        sent = [s for s in rec["steps"] if s["manifest_sent"]]
        assert len(sent) == len(model_steps(r)) and all(s["model_run"] for s in sent)
        assert all(not s["model_run"] and not s["manifest_sent"] for s in rec["steps"] if s["agent"] == "router")
        json.dumps(rec)


def test_a_retired_agent_is_offered_for_the_roster_and_is_not_one_of_the_working_five():
    p = ItsmPack(client=FakeServiceNow())
    retired = [a.name for a in p.retired_roster()]
    assert retired == ["triage_v1"]
    assert not set(retired) & {a.name for a in p.agents()}
    assert len(p.agents()) == 5


# ── the second desk's marker ────────────────────────────────────────────────────────────────────
def test_the_marker_is_the_default_unless_a_variant_is_named():
    assert _marker_from_env({}) == DEFAULT_MARKER == "provy-itsm"
    assert _marker_from_env({"PROVY_ITSM_MARKER": ""}) == "provy-itsm"
    assert _marker_from_env({"PROVY_ITSM_MARKER": "provy-itsm-parity"}) == "provy-itsm-parity"


@pytest.mark.parametrize("bad", ["provy-itsm", "provy-itsm-", "provy-itsm-UPPER", "other", "provy-itsm-a b", "provy-itsm-" + "x" * 40, "x-provy-itsm-a"])
def test_a_marker_that_could_aim_the_desk_at_somebody_elses_tickets_is_refused(bad):
    with pytest.raises(ValueError):
        _marker_from_env({"PROVY_ITSM_MARKER": bad})
