"""The simulated request desk used by the context-manifest sets (#1505, L4). ADDS packs/context_support/.

Not a lever-driven pack. The other packs generate work, apply chaos levers and settle outcomes from a random
stream; this one REPLAYS a committed plan (engine/context.py), so the faults, the manifests and the outcomes are
fixed before anything is emitted. What it gives onboarding is the roster and the contract:

  agents    triage -> resolver -> reviewer, a retrieval step before each decision
  contract  one condition, "the work held up", read from the outcome signal `work_held_up`

It is deliberately NOT registered in packs/__init__.py (that file belongs to provy-sim and is not replaced by this
tree). To register it, add the import and `"context_support": ContextSupportPack` to PACKS; nothing in the context
sets needs it, because scripts/emit_context_set.py reads the plan files directly.

The fleets are labelled SIMULATED in their names. No real customer, tenant or record is behind any of it.
"""
from __future__ import annotations

from engine.types import AgentSpec, Criterion

from engine import context as C


class ContextSupportPack:
    workflow = "context_support"
    session_type = "request"
    owns_outcome = True

    def agents(self) -> list[AgentSpec]:
        return [
            AgentSpec("triage", "Triage", "reads the request and sets its route", "", 0),
            AgentSpec("resolver", "Resolver", "decides and drafts the answer from what it retrieved", "", 1),
            AgentSpec("reviewer", "Reviewer", "checks the draft before it goes out", "", 2),
        ]

    def contract(self) -> list[Criterion]:
        return [Criterion("c1", "The work held up", "outcome", "work_held_up", "eq", True)]

    def plan_agents(self) -> tuple:
        return C.AGENTS
