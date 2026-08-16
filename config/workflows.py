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
# A realistic fleet does not fail the same way every time.
#
# These two fleets previously carried ONLY their commitment injectors, so every miss on the demo
# tenants had the identical cause and Provy had one story to tell over and over: five sessions,
# five "Refund actually settled with the customer", no shared cause, nothing to attribute. A
# prospect looking at that sees a fixture, not a product.
#
# The generic chaos levers are folded back in at low rates so the miss mix is MIXED: some runs fail
# because a tool went wrong inside the run (traceable, attributable), some because the promise did
# not hold downstream (only reconciliation can see it), and most succeed. That is what a real fleet
# looks like, and it is the only way the attribution demo has anything to attribute.
#
# Rates are deliberately LOW in total. The lever engine allows one primary cause per run, and the
# generic levers are evaluated before the pack's settlement feed, so a high generic total silently
# crowds the commitment story out entirely: a first pass at 30% produced 24 runs with zero
# settlement failures. Roughly 14% generic against ~16% commitment keeps both families visible.
_MIXED_GENERIC = {
    "tool_fault":                {"rate": 0.04},   # a tool returns junk mid-run -> deterministic culprit
    "silent_staleness":          {"rate": 0.02},   # stale read the agent acted on
    "silent_incomplete":         {"rate": 0.02},   # partial work reported as done
    "silent_wrong":              {"rate": 0.02},   # confidently wrong answer
    "confidence_miscalibration": {"rate": 0.02},   # sure and wrong / unsure and right
    "sla_breach":                {"rate": 0.01},
    "overt_error":               {"rate": 0.01},   # a run that visibly breaks
}

_STRIPE_RATES = {
    "unsettled_insufficient": {"rate": 0.08},
    "unsettled_bank_return":  {"rate": 0.03},
    "wrong_amount":           {"rate": 0.03},
    "duplicate":              {"rate": 0.02},
    **_MIXED_GENERIC,
    **_L1L2_RATES,
}

_TRAVEL_RATES = {
    "not_ticketed":     {"rate": 0.06},
    "segment_reversed": {"rate": 0.03},
    "wrong_fare":       {"rate": 0.03},
    "double_booked":    {"rate": 0.02},
    **_MIXED_GENERIC,
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

# Agentic AIOps (Edwin-shaped). Two rates are the pack's own injectors, both aimed at claims the
# product makes and cannot check itself:
#   change_blind      the change lookup came back empty on a change-caused fault and the RCA was
#                     written anyway. Only fires on the change-caused half of the fault library, so
#                     its effective rate across a batch is roughly half the number below.
#   correlation_split one fault raised two insights, so the noise-reduction claim failed upward.
#
# silent_wrong leads the generic set because a wrong root cause is this product's signature failure,
# and confidence_miscalibration is deliberately the highest overlay: the investigation panel shows a
# probable cause with no confidence attached, so "sure and wrong" is invisible on their surface and
# is the single most useful thing to demonstrate.
#
# silent_staleness is retargeted at metrics_agent. The default target is the retriever, which here is
# the knowledge agent, but the stale read that actually misleads an RCA is a metric window that
# already rolled, not an old runbook.
# ⛔ THE TWO PACK RATES ARE CONDITIONAL, NOT BATCH RATES, so the number here is not the number you
# get. change_blind is rolled only on runs where no generic lever already fired AND where the fault
# is one a change actually caused (about half the library). Measured over 1000 runs across 40 seeds:
# 0.22 here lands at 10.4% of a batch and a 38.6% fleet failure rate, which sits with support (34.9)
# and claims (36.8) rather than above them. 0.60 gave 22.7% and pushed the fleet to 51.8% failing,
# which reads as a broken fleet rather than a real one.
#
# ⛔ AND DO NOT TUNE THIS ON ONE BATCH. A single 25-run seed put change_blind at 36% when its true
# rate was 22.7%. The sweep across seeds is the only honest reading.
_EDWIN_RATES = {
    "change_blind":              {"rate": 0.22},
    "correlation_split":         {"rate": 0.12},
    "silent_wrong":              {"rate": 0.06},
    "silent_unsupported":        {"rate": 0.04},
    "silent_staleness":          {"rate": 0.03, "target": "metrics_agent"},
    "confidence_miscalibration": {"rate": 0.14},
    "tool_fault":                {"rate": 0.04},
    "quality_degrade":           {"rate": 0.04},
    "policy_violation":          {"rate": 0.04},
    "sla_breach":                {"rate": 0.04},
    "overt_error":               {"rate": 0.02},
    "skip_propagation":          {"rate": 0.02},
    "silent_drift":              {"rate": 1.0, "params": {"onset": 20, "mode": "quality"}},
    **_L1L2_RATES,
}

# ITSM is the odd one out and its rates mean something different. These are not
# chaos levers that rewrite the outcome: the ITSM pack never writes a real signal,
# because ServiceNow settles those. They are the AGENT'S OWN ERROR RATES, applied
# to actions the agent really takes against the instance. Whether a misrouted or
# weakly-fixed ticket then fails is ServiceNow's verdict, not ours.
_ITSM_RATES = {
    "misclassify":   {"rate": 0.10},   # picks the wrong category off the incident text
    "misroute":      {"rate": 0.09},   # assigns a group that does not own that category
    "weak_fix":      {"rate": 0.14},   # workaround, advice, or "no fault found" instead of a fix
    "overconfidence": {"rate": 0.10},  # sure and wrong: high confidence on a weak resolution
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
    "edwin":   WorkflowConfig("edwin",   "PROVY_KEY_EDWIN",   dict(_EDWIN_RATES)),
    "itsm":    WorkflowConfig("itsm",    "PROVY_KEY_ITSM",    dict(_ITSM_RATES)),
}


def get_workflow(name: str) -> WorkflowConfig:
    if name not in WORKFLOWS:
        raise KeyError(f"unknown workflow '{name}'. Known: {', '.join(WORKFLOWS)}")
    return WORKFLOWS[name]
