"""Shared dataclasses for the Provy proof-simulation harness.

These are the only structures that cross the engine <-> pack boundary. A pack
produces work items with ground truth, a clean baseline run, a contract, and a
lever manifest; the engine applies chaos, emits telemetry, and records truth.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


# ── Pipeline description ────────────────────────────────────────────────────

@dataclass
class AgentSpec:
    """One agent in a pack's pipeline."""
    name: str            # slug, matches ag_pipeline_agents.agent_name and ag_eval_configs.agent
    display_name: str
    role: str            # short description used to seed reasoning prose
    emoji: str = ""
    sort_order: int = 0


@dataclass
class Criterion:
    """One contract condition. Positively phrased: op is TRUE on a good run.

    Every criterion MUST be signal-mapped (signal set) so it grades
    method='deterministic'. side is 'outcome' (Real only), 'trace' (Estimated
    only), or 'both' (the genuine Estimated-vs-Real pair).
    """
    id: str
    text: str
    side: str            # 'outcome' | 'trace' | 'both'
    signal: str          # the scalar signal this condition reads
    op: str              # 'eq' | 'gt' | 'gte' | 'lt' | 'lte'
    threshold: Any
    type: str = "success"  # positive polarity: held -> met. Provy inverts only explicit failure/risk;
                           # a missing type used to grade every held condition as violated (the Proved-0% bug).

    def to_contract_json(self) -> dict:
        return {
            "id": self.id, "text": self.text, "side": self.side,
            "signal": self.signal, "op": self.op, "threshold": self.threshold,
            "type": self.type,
        }


# ── Run output ──────────────────────────────────────────────────────────────

@dataclass
class TraceStep:
    agent: str
    step_type: str                      # tool_call | agent_message | decision | error | skip
    outcome: Optional[str] = None
    tool_name: Optional[str] = None
    tool_input: Optional[dict] = None
    tool_output: Optional[dict] = None
    agent_reasoning: Optional[str] = None
    entity_id: Optional[str] = None
    latency_ms: int = 0
    error: Optional[str] = None
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    model: Optional[str] = None
    payload_extra: dict = field(default_factory=dict)   # extra scalar payload fields (estimated signals, confidence)


@dataclass
class EvalResult:
    agent: str
    eval_name: str
    score: float                        # 0..1
    passed: bool
    detail: dict = field(default_factory=dict)          # {reasoning: "..."}
    entity_id: Optional[str] = None
    layer: int = 4


@dataclass
class InjectedFault:
    """One lever firing on one run. The ground-truth record of what we broke."""
    lever: str
    agent: Optional[str]
    dimension: str                      # human label of the affected dimension/signal
    params: dict = field(default_factory=dict)


@dataclass
class RunResult:
    entity_id: str
    session_type: str
    session_id: str
    traces: list[TraceStep] = field(default_factory=list)
    evals: list[EvalResult] = field(default_factory=list)
    terminal_reason: str = "completed"
    estimated_signals: dict = field(default_factory=dict)   # what agents claimed (emitted on a close trace payload)
    real_signals: dict = field(default_factory=dict)        # what actually happened (posted to outcome endpoint)
    outcome_label: str = "success"                          # 'success' | 'fail'
    outcome_value: Optional[float] = None
    confidence: float = 0.9
    faults: list[InjectedFault] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def diverged(self) -> bool:
        """Estimated said success, reality said fail (or vice versa)."""
        est_ok = self.metadata.get("estimated_success", self.outcome_label == "success")
        return est_ok and self.outcome_label == "fail"


@dataclass
class LeverManifest:
    """Names the agents and signals each lever aims at, so the generic lever
    logic in engine/levers.py stays domain-free. Every field is a slug that
    already exists in the pack's agents()/contract()."""
    resolver_agent: str          # the decision agent: silent_wrong + quality_degrade target
    retriever_agent: str         # tool-using agent: tool_fault target
    reviewer_agent: str          # emits estimated signals on its closing message
    first_agent: str             # upstream agent that can skip
    downstream_agent: str        # agent that cannot proceed after an upstream skip
    correctness_signal: str      # the primary correctness signal silent_wrong corrupts
    policy_signal: str           # e.g. 'policy_followed'
    sla_signal: str              # e.g. 'sla_met'
    secondary_bad_signal: Optional[str] = None   # extra signal silent_wrong also corrupts (e.g. reopened_7d)
    drift_agent: Optional[str] = None            # defaults to resolver_agent when None
    policy_agent: Optional[str] = None           # agent that owns policy_signal; defaults to resolver_agent


@dataclass
class RunContext:
    llm: Any                     # engine.llm.LLM
    rng: random.Random
    levers: Any                  # engine.levers.LeverConfig
    session_index: int
    workflow: str
    now: datetime
    offline: bool = True
