"""Groq LLM helper (OpenAI-compatible), with a deterministic offline stub.

The agents call this only to dress their reasoning in realistic prose. The
SIMULATION owns ground truth: correctness is decided by the work item and the
levers, never by what the model says. So when GROQ_API_KEY is absent (dry-run,
tests, CI without a key), we return a deterministic templated string and the
pipeline still runs end to end.

Groq is free. Model: openai/gpt-oss-120b (checked against Groq's model list on 6 Nov 2026; the earlier default, llama-3.3-70b-versatile, no
longer exists there and answers 404, which this helper's blanket except turned into the stub silently). Endpoint:
https://api.groq.com/openai/v1/chat/completions.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

GROQ_BASE = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")


class LLM:
    """One helper the agents call. `offline` forces the deterministic stub."""

    def __init__(self, api_key: str | None = None, offline: bool | None = None, timeout: int = 20):
        self.api_key = api_key if api_key is not None else os.environ.get("GROQ_API_KEY", "")
        # Offline unless a key is present. Explicit override wins.
        self.offline = (not self.api_key) if offline is None else offline
        self.timeout = timeout
        self.calls = 0

    def reason(self, agent: str, role: str, context: str, decision: str, max_words: int = 45) -> str:
        """Return a short first-person reasoning string for one agent step."""
        self.calls += 1
        if self.offline:
            return self._stub(agent, role, context, decision)
        prompt = (
            f"You are the {agent} agent ({role}). Given this work item:\n{context}\n\n"
            f"You decided: {decision}\n\n"
            f"Write ONE concise sentence (max {max_words} words) of first-person "
            f"reasoning explaining that decision. No preamble."
        )
        try:
            text = self._complete(
                system="You are a specialist agent in a business workflow. Be concise and confident.",
                user=prompt,
                max_tokens=120,
            )
            if text.strip():
                return text.strip()
            self._say_once("the model returned empty text")
            return self._stub(agent, role, context, decision)
        except Exception as e:
            self._say_once(f"{type(e).__name__}: {str(e)[:80]}")
            return self._stub(agent, role, context, decision)

    # ── internals ────────────────────────────────────────────────────────────

    def _say_once(self, why: str) -> None:
        """A live call failed and the stub was used. Said ONCE per helper (stderr), never per call: the stub is the right fallback, silence about it was the bug."""
        if not getattr(self, "_said", False):
            self._said = True
            sys.stderr.write(f"[llm] live call failed, using the offline stub from here on ({why})\n")

    def _stub(self, agent: str, role: str, context: str, decision: str) -> str:
        ctx = context.replace("\n", " ").strip()
        if len(ctx) > 90:
            ctx = ctx[:90] + "..."
        return f"[{agent}] As the {role}, I reviewed the item ({ctx}) and concluded: {decision}."

    def _complete(self, system: str, user: str, max_tokens: int = 120) -> str:
        payload = {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.4,
        }
        # ⛔ gpt-oss IS A REASONING MODEL. Under a tight max_tokens it spends the whole budget on reasoning and returns EMPTY text (finish_reason "length"), which `reason` then
        # turns into the stub without a word. A low reasoning effort keeps it short. Only for this family: a model that does not know the field answers 400.
        if GROQ_MODEL.startswith("openai/gpt-oss"):
            payload["reasoning_effort"] = "low"
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{GROQ_BASE}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                # ⛔ Groq sits behind Cloudflare, which rejects Python's default urllib identity with a 403 (error 1010). Without a User-Agent EVERY call failed
                # and `reason` swallowed it into the stub, so the "live" reasoning was never live.
                "User-Agent": "provy-sim/1.0",
            },
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=self.timeout)
        data = json.loads(resp.read().decode())
        return data["choices"][0]["message"]["content"]
