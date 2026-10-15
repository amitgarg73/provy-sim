"""The Servicely pack, offline. A fake client stands in for the instance, so no ticket is raised anywhere."""
import random

import pytest

from conftest import make_ctx
from engine.levers import LeverConfig
from engine.servicely import MARKER, ServicelyError
from engine.types import Wait
from packs import PACKS, get_pack
from packs.servicely.pack import SV_GROUP, ServicelyPack
from packs.itsm.pack import WriteRejected  # noqa: F401


class FakeServicely:
    """Just enough of ServicelyClient for the pack: directory reads, create and update."""

    def __init__(self, reject_closure=False):
        self.writes: list[tuple[str, str, dict]] = []
        self.reject_closure = reject_closure
        self._n = 0

    def query(self, table, params=None, page_size=100):
        if table == "Group":
            return [{"Name": "Support", "id": "g-support"}, {"Name": "Network Operations", "id": "g-net"},
                    {"Name": "CAB", "id": "g-cab"}]
        if table == "User":
            return [{"Email": "aisha.khan@example.com", "id": "u-aisha"}, {"Email": "amit@provy.ai", "id": "u-amit"}]
        if table == "Classification":
            return [{"Name": "Other", "id": "c-other"}]
        return []

    def create_incident(self, short_description, requestor_id, requested_for_id, classification_id,
                        assignment_group_id, description="", **extra):
        self._n += 1
        self.writes.append(("create", "Incident", {"ShortDescription": f"{MARKER} {short_description}",
                                                   "AssignmentGroup": assignment_group_id}))
        return {"id": f"rec{self._n}", "Number": f"INC{self._n:07d}"}

    def update(self, table, record_id, payload):
        if self.reject_closure and "ClosureCode" in payload:
            raise ServicelyError("HTTP 422: ClosureCode refused")
        self.writes.append(("update", record_id, dict(payload)))
        return {"Number": "INC0000001", "WorkflowStatus": {"value": "work_in_progress"}}


def drive(pack, item, ctx):
    waits, journey = [], pack.journey(item, {}, ctx)
    while True:
        try:
            waits.append(journey.send(None))
        except StopIteration as done:
            return waits, done.value


def run(rates=None, seed=3, client=None):
    client = client or FakeServicely()
    pack = ServicelyPack(client=client)
    ctx = make_ctx(levers=LeverConfig(rates or {}), seed=seed, workflow="servicely")
    item, gt = pack.generate_work_item(random.Random(seed))
    waits, result = drive(pack, item, ctx)
    return pack, client, item, gt, waits, result


def test_the_pack_is_registered_and_is_the_same_five_agents_without_sla_conditions():
    assert "servicely" in PACKS
    p = get_pack("servicely")
    assert [a.name for a in p.agents()] == ["triage", "router", "knowledge", "resolver", "reviewer"]
    signals = {c.signal for c in p.contract()}
    assert signals == {"resolution_genuine", "resolution_persists", "self_resolved",
                       "procedure_followed", "kb_article_valid"}
    assert not signals & {"first_response_time_met", "resolution_time_met"}


def test_it_reports_nothing_about_the_outcome():
    _, _, _, _, _, r = run()
    assert r.real_signals == {}
    assert r.outcome_label == "skipped"
    assert r.metadata["outcome_source"] == "servicely_push"
    assert ServicelyPack.owns_outcome is False


def test_the_resolution_is_a_closure_code_write_and_the_ticket_is_the_pack_own():
    _, client, item, _, _, r = run()
    creates = [w for w in client.writes if w[0] == "create"]
    assert len(creates) == 1 and creates[0][2]["ShortDescription"].startswith("[provy-servicely] ")
    closure = [w for w in client.writes if w[0] == "update" and "ClosureCode" in w[2]]
    assert len(closure) == 1 and closure[0][1] == item["rec_id"]
    assert r.entity_id == item["id"]
    # state is never written: the record API refuses it and the instance moves the ticket
    assert not any("WorkflowStatus" in w[2] for w in client.writes if w[0] == "update")


def test_the_answer_key_is_not_in_the_item_the_agent_reads_or_in_any_trace_input():
    pack, client, item, gt, _, r = run()
    assert set(item) == {"id", "rec_id", "short_description", "description", "opened_at"}
    assert not ({"category", "group"} & set(item))
    for t in r.traces:
        assert not ({"category", "group", "_truth", "answer_key"} & set(t.tool_input or {}))
    assert pack._truth[item["id"]] == gt   # held by the pack, never handed to the decision


def test_a_misroute_sends_it_to_the_other_group_and_is_corrected_with_a_wait_that_has_a_cause():
    pack, client, item, gt, waits, r = run(rates={"misroute": 1.0, "misclassify": 0.0})
    assigned = [w[2]["AssignmentGroup"] for w in client.writes if w[0] == "update" and "AssignmentGroup" in w[2]]
    wrong_group_id = {"Support": "g-net", "Network Operations": "g-support"}[gt["group"]]
    assert assigned[0] == wrong_group_id
    assert assigned[-1] != wrong_group_id and len(assigned) == 2
    assert any(isinstance(w, Wait) and w.cause for w in waits)


def test_a_correct_route_is_written_once_with_no_handoff():
    _, client, _, gt, waits, _ = run(rates={"misroute": 0.0, "misclassify": 0.0})
    assigned = [w for w in client.writes if w[0] == "update" and "AssignmentGroup" in w[2]]
    assert len(assigned) == 1
    assert not any(w.cause for w in waits)


def test_a_refused_write_fails_this_ticket_and_says_so():
    _, _, _, _, _, r = run(client=FakeServicely(reject_closure=True))
    assert r.terminal_reason == "tool_error"
    assert r.metadata["estimated_success"] is False
    assert r.traces[-1].outcome == "error"


def test_routing_is_always_between_groups_the_instance_has():
    assert set(SV_GROUP.values()) <= {"Support", "Network Operations"}


def test_the_router_speaks_so_its_signal_is_not_credited_to_the_reviewer():
    """reassignment_count is the router's reading. It is stamped on the router's first agent_message, and only
    falls back to the reviewer when the router never spoke."""
    _, _, _, _, _, r = run()
    router_msgs = [t for t in r.traces if t.agent == "router" and t.step_type == "agent_message"]
    assert router_msgs, "the router has no agent_message, so its signals fall to the reviewer"
    owned = [t for t in router_msgs
             if any(c.get("signal") == "reassignment_count"
                    for c in (t.payload_extra or {}).get("provy_claim", []))]
    assert owned, "reassignment_count was not stamped on the router's own message"
