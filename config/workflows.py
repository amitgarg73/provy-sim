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
        """Load levers, preferring the Sim Control console when CONTROL_URL is
        set. Fully optional: if the console is unreachable or unconfigured, fall
        back to the local defaults so the sim always runs offline."""
        try:
            from engine.control_client import fetch_lever_rates
            remote = fetch_lever_rates(self.workflow)
            if remote:
                return LeverConfig(remote)
        except Exception:
            pass
        return LeverConfig(self.lever_rates)


# Default lever manifest per workflow. Silent levers lead — they are the
# differentiator. Overt levers are lower so the fleet still mostly succeeds.

# L1/L2 activity levers (Provy Tool Activity + LLM Calls checks) that EVERY fleet carries.
# Overlays: they don't reshape the outcome, they just breach a single tool/model call's budget.
_L1L2_RATES = {
    "tool_latency":              {"rate": 0.05},
    "tool_errors":               {"rate": 0.05},
    "llm_cost":                  {"rate": 0.05},
    "llm_tokens":                {"rate": 0.0},   # off by default (Provy LLM Tokens check needs a budget set)
}

_DEFAULT_RATES = {
    "silent_wrong":              {"rate": 0.12},
    "silent_staleness":          {"rate": 0.05},
    "silent_unsupported":        {"rate": 0.04},
    "silent_incomplete":         {"rate": 0.03},
    "silent_policy":             {"rate": 0.03},
    "silent_missed_action":      {"rate": 0.03},
    "confidence_miscalibration": {"rate": 0.10},
    "tool_fault":                {"rate": 0.08},
    "quality_degrade":           {"rate": 0.08},
    "policy_violation":          {"rate": 0.05},
    "sla_breach":                {"rate": 0.06},
    "overt_error":               {"rate": 0.04},
    "skip_propagation":          {"rate": 0.03},
    "silent_drift":              {"rate": 1.0, "params": {"onset": 20, "mode": "quality"}},
    **_L1L2_RATES,
}

# Commitment-integrity fleets. Their signature failures come from a mock system of record
# (engine/mock_sor.py for Stripe, engine/commitment.py for the rest) — the injectors below.
# Each is also a superset: the generic chaos levers run on it too (the pack calls the shared
# lever engine), available at rate 0 for an operator to dial up. Defaults keep the
# commitment-integrity story plus the L1/L2 overlays on.
_STRIPE_RATES = {
    "unsettled_insufficient": {"rate": 0.08},
    "unsettled_bank_return":  {"rate": 0.03},
    "wrong_amount":           {"rate": 0.03},
    "duplicate":              {"rate": 0.02},
    **_L1L2_RATES,
}

_TRAVEL_RATES = {
    "not_ticketed":     {"rate": 0.06},
    "segment_reversed": {"rate": 0.03},
    "wrong_fare":       {"rate": 0.03},
    "double_booked":    {"rate": 0.02},
    **_L1L2_RATES,
}

_REVOPS_RATES = {
    "write_not_landed":     {"rate": 0.06},
    "sync_lag":             {"rate": 0.03},
    "wrong_discount":       {"rate": 0.03},
    "wrong_record":         {"rate": 0.02},
    "duplicate_opportunity": {"rate": 0.02},
    **_L1L2_RATES,
}

_CLAIMS_PAYOUT_RATES = {
    "not_disbursed":     {"rate": 0.06},
    "prompt_pay_lapsed": {"rate": 0.03},
    "claims_leakage":    {"rate": 0.03},
    "stale_lienholder":  {"rate": 0.02},
    "duplicate_payment": {"rate": 0.02},
    **_L1L2_RATES,
}

_LEGAL_RATES = {
    "esign_incomplete":  {"rate": 0.05},
    "filing_bounced":    {"rate": 0.03},
    "deadline_lapsed":   {"rate": 0.03},
    "wrong_counterparty": {"rate": 0.02},
    "duplicate_filing":  {"rate": 0.02},
    **_L1L2_RATES,
}

WORKFLOWS = {
    "support": WorkflowConfig("support", "PROVY_KEY_SUPPORT", dict(_DEFAULT_RATES)),
    "stripe_support": WorkflowConfig("stripe_support", "PROVY_KEY_STRIPE_SUPPORT", dict(_STRIPE_RATES)),
    "claims":  WorkflowConfig("claims",  "PROVY_KEY_CLAIMS",  dict(_DEFAULT_RATES)),
    "crm":     WorkflowConfig("crm",     "PROVY_KEY_CRM",     dict(_DEFAULT_RATES)),
    "travel":  WorkflowConfig("travel",  "PROVY_KEY_TRAVEL",  dict(_TRAVEL_RATES)),
    "revops":  WorkflowConfig("revops",  "PROVY_KEY_REVOPS",  dict(_REVOPS_RATES)),
    "claims_payout": WorkflowConfig("claims_payout", "PROVY_KEY_CLAIMS_PAYOUT", dict(_CLAIMS_PAYOUT_RATES)),
    "legal":   WorkflowConfig("legal",   "PROVY_KEY_LEGAL",   dict(_LEGAL_RATES)),
}


def get_workflow(name: str) -> WorkflowConfig:
    if name not in WORKFLOWS:
        raise KeyError(f"unknown workflow '{name}'. Known: {', '.join(WORKFLOWS)}")
    return WORKFLOWS[name]
