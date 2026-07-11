"""Per-workflow configuration: ingest-key env var, lever rates, cadence.

Structure is decided at onboard, not in code. Each pack is a workflow (fleet)
with its own ingest key. The key env var name is the only wiring: PROVY_KEY_SUPPORT
resolves (tenant_id, workflow_id) on Provy's side. No secrets live here.

Lever rates are the "levers you can pull": dial one up, watch it appear in Provy,
dial it off to fix. Rates below are tuned to cross the §7 thresholds within a few
days at a batch-every-15-30-min cadence. silent_drift uses an onset session index
rather than a per-run rate.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from engine.levers import LeverConfig


@dataclass
class WorkflowConfig:
    workflow: str
    key_env: str                      # env var holding the Provy ingest key
    lever_rates: dict = field(default_factory=dict)
    batch_size: int = 8               # work items per scheduled batch
    cadence_minutes: int = 20         # scheduler interval (documented; scheduler is external)

    @property
    def ingest_key(self) -> str:
        return os.environ.get(self.key_env, "")

    def lever_config(self) -> LeverConfig:
        return LeverConfig(self.lever_rates)


# Default lever manifest per workflow. Silent levers lead — they are the
# differentiator. Overt levers are lower so the fleet still mostly succeeds.
_DEFAULT_RATES = {
    "silent_wrong":              {"rate": 0.12},
    "confidence_miscalibration": {"rate": 0.10},
    "tool_fault":                {"rate": 0.08},
    "quality_degrade":           {"rate": 0.08},
    "policy_violation":          {"rate": 0.05},
    "sla_breach":                {"rate": 0.06},
    "overt_error":               {"rate": 0.04},
    "skip_propagation":          {"rate": 0.03},
    "silent_drift":              {"rate": 1.0, "params": {"onset": 20, "mode": "quality"}},
}

WORKFLOWS = {
    "support": WorkflowConfig("support", "PROVY_KEY_SUPPORT", dict(_DEFAULT_RATES)),
    "claims":  WorkflowConfig("claims",  "PROVY_KEY_CLAIMS",  dict(_DEFAULT_RATES)),
    "crm":     WorkflowConfig("crm",     "PROVY_KEY_CRM",     dict(_DEFAULT_RATES)),
}


def get_workflow(name: str) -> WorkflowConfig:
    if name not in WORKFLOWS:
        raise KeyError(f"unknown workflow '{name}'. Known: {', '.join(WORKFLOWS)}")
    return WORKFLOWS[name]
