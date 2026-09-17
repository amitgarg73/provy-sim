"""
The emitter declares which agent's output each step consumed — argus#1009.

⛔ WHY IT MATTERS THAT THE SIMULATOR DOES THIS. Provy's victim attribution needs a traced input
edge; without one the only downstream signal is position, and "ran later" is not "was affected by".
Measured before this shipped: 5,731 pre-prod spans and ZERO edges between two agents, so the
simulator could not produce the shape the product exists to reason about. Production was no better:
729 cross-agent-looking edges, every one of them `research -> research_<TICKER>`, one agent fanning
out per work item.
"""
from engine.emitter import ProvyEmitter, _agent_base


class _Step:
    def __init__(self, agent, step_type="agent_step", outcome="ok"):
        self.agent = agent
        self.step_type = step_type
        self.outcome = outcome
        self.tool_name = self.error = self.entity_id = self.model = None
        self.latency_ms = self.tokens_input = self.tokens_output = self.cost_usd = 0
        self.agent_reasoning = self.system = self.user = None
        self.tool_input = self.tool_output = self.payload_extra = None


class _Result:
    def __init__(self, session_id="s1"):
        self.session_id = session_id
        self.entity_id = "E1"


def _emitter(monkeypatch):
    e = ProvyEmitter(ingest_key="provy_test", base_url="http://example.invalid")
    sent = []
    monkeypatch.setattr(e, "_post", lambda path, payload: sent.append(payload) or {})
    return e, sent


def test_agent_base_collapses_per_entity_fan_out():
    assert _agent_base("research_GILD") == "research"
    assert _agent_base("research") == "research"
    assert _agent_base("risk") == "risk"


def test_every_span_carries_an_id(monkeypatch):
    e, sent = _emitter(monkeypatch)
    e.trace(_Result(), _Step("intake"))
    assert sent[0]["span_id"] and len(sent[0]["span_id"]) == 16


def test_each_step_names_the_previous_agent_as_its_input(monkeypatch):
    e, sent = _emitter(monkeypatch)
    r = _Result()
    e.trace(r, _Step("intake"))
    e.trace(r, _Step("validator"))
    e.trace(r, _Step("adjudicator"))
    assert sent[1]["input_span_ids"] == [sent[0]["span_id"]]
    assert sent[2]["input_span_ids"] == [sent[1]["span_id"]]


# ⛔ "NOTHING UPSTREAM EXISTS YET" IS NOT "I READ NOTHING". The first agent declares nothing at all,
# because an empty array is a positive claim about the pipeline's shape and the first step is not in
# a position to make it.
def test_the_first_agent_declares_nothing(monkeypatch):
    e, sent = _emitter(monkeypatch)
    e.trace(_Result(), _Step("intake"))
    assert "input_span_ids" not in sent[0]


def test_fan_out_children_do_not_become_pipeline_stages(monkeypatch):
    e, sent = _emitter(monkeypatch)
    r = _Result()
    e.trace(r, _Step("research"))
    e.trace(r, _Step("research_GILD"))
    e.trace(r, _Step("research_AAPL"))
    e.trace(r, _Step("risk"))
    # The per-entity children are the same logical step, so none of them is an upstream stage and
    # risk reads research, not whichever ticker happened to run last.
    assert "input_span_ids" not in sent[1]
    assert "input_span_ids" not in sent[2]
    assert sent[3]["input_span_ids"] == [sent[2]["span_id"]]


def test_sessions_do_not_leak_into_each_other(monkeypatch):
    e, sent = _emitter(monkeypatch)
    a, b = _Result("sA"), _Result("sB")
    e.trace(a, _Step("intake"))
    e.trace(b, _Step("validator"))
    # b's first step is the first agent IN ITS OWN SESSION, so it must not inherit a's span.
    assert "input_span_ids" not in sent[1]
