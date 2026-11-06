"""The reader turns a Servicely agent run into a claim. The claim must follow what the agent did, and
re-reading the same run must give the same span ids (so a re-emit cannot duplicate it)."""
from engine.servicely_runs import read_run


def _platform(tools, handoff=False):
    t = "2026-10-20T01:00:00.000+11:00"
    acts = [{"id": f"{i:032d}", "CreatedOn": t, "EntryType": "tool_invocation", "ToolOrAgentKey": k, "OutputSummary": "{\"success\": true}"}
            for i, k in enumerate(tools)]
    data = {
        "/v1/Incident": [{"id": "inc1", "Number": "INC9", "Assignee": "u1"}],
        "/v1/User/u1": [{"AgentPersona": "p1"}],
        "/v1/SystemAgentPersona/p1": [{"Name": "Test Agent"}],
        "/v1/SystemAgentConversationHistory": [{"id": "c" * 32, "CreatedOn": t}],
        "/v1/SystemAIActivityLog": acts,
        "/v1/SystemLLMUsage": [],
        "/v1/SystemAgentConversationMessage": [],
        "/v1/SystemTicketHandoff": [{"id": "h1"}] if handoff else [],
    }

    def get(path):
        return next(v for k, v in data.items() if path.startswith(k))
    return get


def test_solution_claim_needs_the_article_loaded_and_the_answer_posted():
    r = read_run(_platform(["tool_load_knowledge_article", "tool_itsm_update_client_journal"]), "INC9")
    assert r.estimated_signals["close_code"] == "Solution provided"
    assert r.estimated_signals["kb_article_valid"] is True and r.metadata["estimated_success"] is True


def test_an_answer_with_no_article_loaded_is_not_claimed_as_a_solution():
    r = read_run(_platform(["tool_itsm_update_client_journal"]), "INC9")
    assert r.estimated_signals["kb_article_valid"] is False
    assert r.estimated_signals["close_code"] == "No resolution" and r.metadata["estimated_success"] is False


def test_a_handoff_is_a_reassignment_and_not_a_success_claim():
    r = read_run(_platform(["tool_load_knowledge_article", "tool_itsm_update_client_journal"], handoff=True), "INC9")
    assert r.estimated_signals["reassignment_count"] == 1 and r.metadata["estimated_success"] is False


def test_every_step_carries_the_platforms_own_row_id_so_a_re_read_can_be_matched():
    a = read_run(_platform(["tool_load_knowledge_article"]), "INC9")
    b = read_run(_platform(["tool_load_knowledge_article"]), "INC9")
    ids = [s.payload_extra["source_row_id"] for s in a.traces if s.step_type == "tool_call"]
    assert ids == [s.payload_extra["source_row_id"] for s in b.traces if s.step_type == "tool_call"] and all(ids)


def test_no_agent_run_is_none():
    def get(path):
        return [{"id": "inc1", "Number": "INC9"}] if path.startswith("/v1/Incident") else []
    assert read_run(get, "INC9") is None
