"""Provy proof-simulation harness — shared engine.

Domain-pack interface plus the shared machinery: emitter, LLM helper, levers,
ground-truth ledger, scoreboard, runner, reconcile.
"""
from .types import (AgentSpec, Criterion, EvalResult, InjectedFault,
                    LeverManifest, RunContext, RunResult, TraceStep)
from .pack import BasePack, DomainPack
from .levers import LeverConfig, LeverSetting, apply, finalize
from .llm import LLM
from .emitter import ProvyEmitter, emit_enabled
from .groundtruth import GroundTruthLedger, build_record
from .runner import BatchRunner, RunOutput
from . import contract, scoreboard, reconcile

__all__ = [
    "AgentSpec", "Criterion", "EvalResult", "InjectedFault", "LeverManifest",
    "RunContext", "RunResult", "TraceStep", "BasePack", "DomainPack",
    "LeverConfig", "LeverSetting", "apply", "finalize", "LLM", "ProvyEmitter",
    "emit_enabled", "GroundTruthLedger", "build_record", "BatchRunner",
    "RunOutput", "contract", "scoreboard", "reconcile",
]
