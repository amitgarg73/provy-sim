"""argus#1735: the Servicely pack labels every model step with the version of its instructions.

On production 0 of 10,614 spans carried a `prompt_version`, so "what changed before it broke" could not be answered. The simulator's Servicely fleet is the first real
design-partner shape, so it sends the label the way a customer using provy-sdk 0.11.0's `set_prompt` would.
"""
import importlib.util
import random

import pytest

from engine.versions import prompt_hash


def test_the_label_is_stable_and_ignores_whitespace():
    assert prompt_hash("You are the router.\n  Route it.") == prompt_hash("You are the router. Route it.")
    assert prompt_hash("a") != prompt_hash("b")


@pytest.mark.skipif(importlib.util.find_spec("provy") is None or not hasattr(__import__("provy"), "prompt_hash"), reason="provy-sdk 0.11.0 is not installed")
def test_it_is_the_same_label_the_published_sdk_makes():
    import provy
    for text in ("You are the resolver agent.", "x  y\nz"):
        assert prompt_hash(text) == provy.prompt_hash(text)


def test_the_servicely_pack_labels_model_steps_and_leaves_tool_steps_alone():
    from packs.servicely.pack import ServicelyPack
    from engine.types import TraceStep
    pack = ServicelyPack(client=object())
    agent = pack.agents()[0]

    class _LLM:
        def reason(self, *a, **k):
            return "because"

    class _Ctx:
        workflow = "servicely"
        llm = _LLM()
        rng = random.Random(1)

    step = pack.agent_step(_Ctx(), agent, {"number": "INC1"}, "ok", "INC1")
    assert step.prompt_version == prompt_hash(step.system)
    tool = pack.tool_step(_Ctx(), agent, "PATCH", {}, {}, "INC1")
    assert isinstance(tool, TraceStep) and tool.prompt_version is None


def test_the_emitter_sends_the_label_only_when_the_step_has_one():
    from engine.emitter import ProvyEmitter as ArgusEmitter
    sent = []
    em = ArgusEmitter.__new__(ArgusEmitter)
    em._agent_order, em._last_span = {}, {}
    em._post = lambda path, payload: sent.append(payload) or {}
    from engine.types import TraceStep, RunResult
    r = RunResult(entity_id="E", session_type="incident", session_id="s")
    em.trace(r, TraceStep(agent="router", step_type="agent_message", prompt_version="p-1"))
    em.trace(r, TraceStep(agent="router", step_type="tool_call"))
    assert sent[0]["prompt_version"] == "p-1" and "prompt_version" not in sent[1]
