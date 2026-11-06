"""The Groq helper: a failed live call falls back to the stub AND says so once; the request carries a User-Agent (Groq's Cloudflare rejects Python's default with a 403);
a reasoning model gets a low reasoning effort; the trace's model label and price follow GROQ_MODEL."""
import io
import json
import sys

import engine.llm as llm
from engine.llm import LLM


def test_a_failed_live_call_uses_the_stub_and_says_so_once(monkeypatch):
    err = io.StringIO(); monkeypatch.setattr(sys, "stderr", err)
    l = LLM(api_key="k", offline=False)
    monkeypatch.setattr(l, "_complete", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("HTTP Error 403")))
    a = l.reason("Intake", "reads", "ctx", "route")
    b = l.reason("Intake", "reads", "ctx", "route")
    assert a == b and a.startswith("[Intake] As the reads")
    assert err.getvalue().count("[llm] live call failed") == 1 and "403" in err.getvalue()


def test_empty_model_text_also_falls_back_and_says_so(monkeypatch):
    err = io.StringIO(); monkeypatch.setattr(sys, "stderr", err)
    l = LLM(api_key="k", offline=False); monkeypatch.setattr(l, "_complete", lambda *a, **k: "   ")
    assert l.reason("A", "r", "c", "d").startswith("[A] As the r")
    assert "empty text" in err.getvalue()


def test_the_request_has_a_user_agent_and_low_reasoning_for_gpt_oss(monkeypatch):
    seen = {}
    class R:
        def read(self): return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
    def fake_open(req, timeout=0):
        seen["ua"] = req.get_header("User-agent"); seen["body"] = json.loads(req.data.decode()); return R()
    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(llm, "GROQ_MODEL", "openai/gpt-oss-120b")
    assert LLM(api_key="k", offline=False)._complete("s", "u") == "ok"
    assert seen["ua"] and "python-urllib" not in seen["ua"].lower()
    assert seen["body"]["reasoning_effort"] == "low"
    monkeypatch.setattr(llm, "GROQ_MODEL", "some/other-model")
    LLM(api_key="k", offline=False)._complete("s", "u")
    assert "reasoning_effort" not in seen["body"]          # a model that does not know the field would answer 400


def test_the_trace_model_label_follows_the_model_helper_calls():
    import engine.pack as pack
    assert pack.GROQ_MODEL == llm.GROQ_MODEL
    assert pack.llm_cost_usd(1_000_000, 0) == 0.15 and pack.llm_cost_usd(0, 1_000_000) == 0.6
