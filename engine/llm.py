"""Groq LLM helper (OpenAI-compatible), with a deterministic offline stub.

The agents call this only to dress their reasoning in realistic prose. The
SIMULATION owns ground truth: correctness is decided by the work item and the
levers, never by what the model says. So when GROQ_API_KEY is absent (dry-run,
tests, CI without a key), we return a deterministic templated string and the
pipeline still runs end to end.

Groq is free. Model: llama-3.3-70b-versatile. Endpoint:
https://api.groq.com/openai/v1/chat/completions.
"""
from __future__ import annotations

import json
import os
import urllib.request

GROQ_BASE = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")


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
            return text.strip() or self._stub(agent, role, context, decision)
        except Exception:
            return self._stub(agent, role, context, decision)

    # ── internals ────────────────────────────────────────────────────────────

    def _stub(self, agent: str, role: str, context: str, decision: str) -> str:
        ctx = context.replace("\n", " ").strip()
        if len(ctx) > 90:
            ctx = ctx[:90] + "..."
        return f"[{agent}] As the {role}, I reviewed the item ({ctx}) and concluded: {decision}."

    def _complete(self, system: str, user: str, max_tokens: int = 120) -> str:
        body = json.dumps({
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.4,
        }).encode()
        req = urllib.request.Request(
            f"{GROQ_BASE}/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=self.timeout)
        data = json.loads(resp.read().decode())
        return data["choices"][0]["message"]["content"]
