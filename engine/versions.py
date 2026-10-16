"""The same prompt label the published SDK makes (provy-sdk 0.11.0, `provy.prompt_hash`), so the simulator's labels read like a real customer's (argus#1735).

The simulator has its own emitter and does not import provy-sdk, so the few lines are repeated here. `tests/test_prompt_version.py` fails if they ever differ.
"""
from __future__ import annotations

import hashlib


def prompt_hash(text: str) -> str:
    """`p-` and ten hex characters of the SHA-256 of the prompt, whitespace collapsed first."""
    return "p-" + hashlib.sha256(" ".join(str(text).split()).encode("utf-8")).hexdigest()[:10]
