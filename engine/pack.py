"""DomainPack interface and the shared BasePack machinery.

A pack supplies four things: a work-item generator with ground truth, an agent
pipeline, a signal-mapped contract, and a lever manifest plus a CLEAN baseline
run (all agents correct, all evals pass, all real signals good). Everything
else — applying chaos, computing outcomes, emitting, recording truth — is
shared. run_pipeline() is the protocol method: it builds the clean baseline and
then applies the levers, so a pack never re-implements chaos.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from . import levers as L
from .types import (AgentSpec, Criterion, EvalResult, LeverManifest,
                    RunContext, RunResult, TraceStep)

# llama-3.3-70b-versatile list price (per token): ~$0.59/M input, $0.79/M output. A clean
# agent call runs a fraction of a cent, so the L2 LLM Cost check sits far under its budget
# until the llm_cost lever inflates a run. Keeps the "LLM Calls" tile showing real dollars.
_PRICE_IN_PER_TOK = 0.59e-6
_PRICE_OUT_PER_TOK = 0.79e-6


def llm_cost_usd(tokens_in: int, tokens_out: int) -> float:
    return round(tokens_in * _PRICE_IN_PER_TOK + tokens_out * _PRICE_OUT_PER_TOK, 6)


@runtime_checkable
class DomainPack(Protocol):
    workflow: str

    def generate_work_item(self, rng) -> tuple[Any, dict]: ...
    def agents(self) -> list[AgentSpec]: ...
    def contract(self) -> list[Criterion]: ...
    def lever_manifest(self) -> LeverManifest: ...
    def build_clean_run(self, item: Any, ground_truth: dict, ctx: RunContext) -> RunResult: ...
    def run_pipeline(self, item: Any, ground_truth: dict, ctx: RunContext) -> RunResult: ...


class BasePack:
    """Concrete packs subclass this and implement generate_work_item,
    agents, contract, lever_manifest, and build_clean_run."""

    workflow: str = "base"
    session_type: str = "task"

    # ── to implement ──────────────────────────────────────────────────────────
    def generate_work_item(self, rng) -> tuple[Any, dict]:
        raise NotImplementedError

    def agents(self) -> list[AgentSpec]:
        raise NotImplementedError

    def contract(self) -> list[Criterion]:
        raise NotImplementedError

    def lever_manifest(self) -> LeverManifest:
        raise NotImplementedError

    def build_clean_run(self, item: Any, ground_truth: dict, ctx: RunContext) -> RunResult:
        raise NotImplementedError

    def failure_cost(self) -> dict:
        """Per-occurrence dollar cost of each failure (lever), domain-specific and illustrative.
        Drives the 'value at risk' the scoreboard shows. Levers absent here cost 0 (e.g. drift,
        calibration, which have no clean per-run dollar figure)."""
        return {}

    # ── shared ──────────────────────────────────────────────────────────────
    def entity_id(self, item: Any) -> str:
        return item["id"] if isinstance(item, dict) else str(item)

    def session_id(self, item: Any) -> str:
        return f"sim-{self.workflow}-{self.entity_id(item)}"

    def run_pipeline(self, item: Any, ground_truth: dict, ctx: RunContext) -> RunResult:
        result = self.build_clean_run(item, ground_truth, ctx)
        m = self.lever_manifest()
        L.apply(result, ground_truth, m, self.contract(), ctx.levers, ctx)
        # Stamp the post-lever Estimated signals onto the reviewer's closing
        # message so the Estimated (trace) side of every 'both'/'trace' condition
        # is readable on a real trace payload, per the worked example in the spec.
        for t in result.traces:
            if t.agent == m.reviewer_agent and t.step_type == "agent_message":
                t.payload_extra.update(result.estimated_signals)
                t.payload_extra["confidence"] = result.confidence
                break
        return result

    # ── helpers packs can use when building the clean baseline ───────────────
    def clean_signals(self) -> dict:
        """All contract signals at their good value."""
        from . import contract as C
        good = {}
        for c in self.contract():
            good[c.signal] = C.good_value(c)
        return good

    def base_result(self, item: Any) -> RunResult:
        good = self.clean_signals()
        return RunResult(
            entity_id=self.entity_id(item),
            session_type=self.session_type,
            session_id=self.session_id(item),
            estimated_signals=dict(good),
            real_signals=dict(good),
            outcome_label="success",
            outcome_value=1.0,
            confidence=0.9,
        )

    def agent_step(self, ctx: RunContext, agent: AgentSpec, item: Any, decision: str,
                   entity_id: str, tokens=(300, 60), payload_extra: dict | None = None) -> TraceStep:
        reasoning = ctx.llm.reason(agent.name, agent.role, self._summ(item), decision)
        return TraceStep(
            agent=agent.name, step_type="agent_message", outcome=decision,
            agent_reasoning=reasoning, entity_id=entity_id,
            tokens_input=tokens[0], tokens_output=tokens[1], model="llama-3.3-70b-versatile",
            cost_usd=llm_cost_usd(tokens[0], tokens[1]),
            latency_ms=ctx.rng.randint(400, 1800),
            payload_extra=payload_extra or {},
        )

    def tool_step(self, ctx: RunContext, agent: AgentSpec, tool_name: str,
                  tool_input: dict, tool_output: dict, entity_id: str) -> TraceStep:
        return TraceStep(
            agent=agent.name, step_type="tool_call", tool_name=tool_name,
            tool_input=tool_input, tool_output=tool_output, entity_id=entity_id,
            outcome="ok", latency_ms=ctx.rng.randint(80, 500),
        )

    def eval_pass(self, agent: str, eval_name: str, entity_id: str,
                  reasoning: str, score: float = 0.9) -> EvalResult:
        return EvalResult(agent=agent, eval_name=eval_name, score=score, passed=score >= 0.7,
                          detail={"reasoning": reasoning}, entity_id=entity_id)

    @staticmethod
    def _summ(item: Any) -> str:
        if isinstance(item, dict):
            keep = {k: v for k, v in item.items() if k not in ("id",)}
            return "; ".join(f"{k}={v}" for k, v in list(keep.items())[:5])
        return str(item)
