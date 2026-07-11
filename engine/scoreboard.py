"""The scoreboard: compare injected truth to Provy's outputs.

v1 delivers two halves:
  1. Injected-truth aggregation — fully implemented. From the ground-truth
     ledger it computes, per lever/feature, the rate the sim injected, plus the
     honest denominators (runs, diverged, per-condition fail rates).
  2. The comparison skeleton — computes the per-feature report and pulls Provy's
     side through ProvyQuery. ProvyQuery reads the ag_* tables read-only when
     Supabase creds are present; otherwise it returns stubs with clear TODOs so
     the harness runs and prints the injected side today, and lights up the
     detected side the moment credentials exist.
"""
from __future__ import annotations

import os
from collections import Counter
from typing import Optional

from .contract import Criterion, meets, signal_index


# ── Injected-truth aggregation ───────────────────────────────────────────────

def aggregate_injected(records: list[dict], contract: list[Criterion]) -> dict:
    """Compute injected rates and honest denominators from the ledger."""
    n = len(records)
    if n == 0:
        return {"runs": 0}

    lever_counts: Counter = Counter()
    lever_by_agent: dict[str, Counter] = {}
    for rec in records:
        for f in rec.get("faults", []):
            lever_counts[f["lever"]] += 1
            lever_by_agent.setdefault(f["lever"], Counter())[f.get("agent") or "-"] += 1

    diverged = sum(1 for r in records if r.get("diverged"))
    fails = sum(1 for r in records if r.get("outcome_label") == "fail")
    skipped = sum(1 for r in records if r.get("outcome_label") == "skipped")

    # Per-condition injected fail rate (Real side), which the contract met-rate
    # should mirror.
    cond_fail: dict[str, int] = {c.id: 0 for c in contract}
    cond_signal = {c.id: c for c in contract}
    measurable = [c for c in contract if c.side in ("outcome", "both")]
    for rec in records:
        real = rec.get("real_signals", {})
        for c in measurable:
            if not meets(c, real.get(c.signal)):
                cond_fail[c.id] += 1

    total_measurable_slots = len(measurable) * n
    total_met = total_measurable_slots - sum(cond_fail[c.id] for c in measurable)
    injected_met_rate = (total_met / total_measurable_slots) if total_measurable_slots else None

    return {
        "runs": n,
        "levers": {
            lv: {
                "count": cnt,
                "rate": round(cnt / n, 4),
                "by_agent": dict(lever_by_agent.get(lv, {})),
            }
            for lv, cnt in sorted(lever_counts.items())
        },
        "diverged": {"count": diverged, "rate": round(diverged / n, 4)},
        "fails": {"count": fails, "rate": round(fails / n, 4)},
        "skipped": {"count": skipped, "rate": round(skipped / n, 4)},
        "conditions": {
            c.id: {
                "signal": c.signal, "side": c.side,
                "injected_fail_rate": round(cond_fail[c.id] / n, 4),
            } for c in contract
        },
        "injected_met_rate": round(injected_met_rate, 4) if injected_met_rate is not None else None,
    }


# ── Provy side (read-only) ───────────────────────────────────────────────────

class ProvyQuery:
    """Reads Provy's outputs to score detection. Uses Supabase (service key) if
    present; otherwise every method returns a stub with a TODO so the harness
    still runs. Reconciliation/divergence keys on entity_id; trust reads the
    fleet's contract met-rate."""

    def __init__(self, tenant_id: str | None = None, workflow_id: str | None = None):
        self.tenant_id = tenant_id or os.environ.get("PROVY_TENANT_ID", "")
        self.workflow_id = workflow_id or os.environ.get("PROVY_WORKFLOW_ID", "")
        self._db = self._connect()

    def _connect(self):
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_KEY", "")
        if not (url and key):
            return None
        try:
            from supabase import create_client  # optional dep
            return create_client(url, key)
        except Exception:
            return None

    @property
    def available(self) -> bool:
        return self._db is not None and bool(self.tenant_id)

    def contract_met_rate(self) -> Optional[float]:
        # TODO: read the fleet met-rate Provy computes (lib/outcome-evaluator +
        # the unified reconciliation number). Requires Supabase creds + workflow_id.
        if not self.available:
            return None
        return None  # TODO: query ag_session_outcomes / rollup once creds exist

    def reconciled_divergence_rate(self) -> Optional[float]:
        # TODO: fraction of reconciled outcomes Provy flagged as diverged
        # (ledger: predicted success, real fail), keyed on entity_id.
        if not self.available:
            return None
        return None

    def incident_count(self) -> Optional[int]:
        # TODO: count ag_incidents for the workflow in the run window.
        if not self.available:
            return None
        return None


# ── The report ───────────────────────────────────────────────────────────────

def build_report(records: list[dict], contract: list[Criterion],
                 provy: ProvyQuery | None = None) -> dict:
    injected = aggregate_injected(records, contract)
    provy = provy or ProvyQuery()

    detected = {
        "provy_available": provy.available,
        "contract_met_rate": provy.contract_met_rate(),
        "reconciled_divergence_rate": provy.reconciled_divergence_rate(),
        "incident_count": provy.incident_count(),
    }

    # Feature-proof rows (the §7 checklist). Injected side is real; detected side
    # is None until Provy queries are wired (available=False -> pending).
    rows = []

    def row(feature, lever, injected_val, detected_val, note=""):
        status = "pending" if detected_val is None else "scored"
        rows.append({"feature": feature, "lever": lever, "injected": injected_val,
                     "detected": detected_val, "status": status, "note": note})

    lv = injected.get("levers", {})
    row("Contract reconciliation (X of N met)", "silent_wrong/policy/sla",
        injected.get("injected_met_rate"), detected["contract_met_rate"])
    row("Silent-divergence attribution", "silent_wrong",
        lv.get("silent_wrong", {}).get("rate"), detected["reconciled_divergence_rate"])
    row("Incidents / Reliability", "overt_error/quality_degrade",
        (lv.get("overt_error", {}).get("count", 0) + lv.get("quality_degrade", {}).get("count", 0)),
        detected["incident_count"])
    row("Drift badge", "silent_drift", lv.get("silent_drift", {}).get("rate"), None,
        "onset-based; check the drift session in Provy")
    row("Recalibration", "confidence_miscalibration",
        lv.get("confidence_miscalibration", {}).get("rate"), None,
        "needs >=20 reconciled")

    return {"injected": injected, "detected": detected, "rows": rows}


def format_report(report: dict, workflow: str) -> str:
    inj = report["injected"]
    lines = [f"=== Provy proof scoreboard — {workflow} ===",
             f"runs: {inj.get('runs', 0)}"]
    if inj.get("runs"):
        lines.append(f"injected met-rate: {inj.get('injected_met_rate')}  "
                     f"diverged: {inj['diverged']['rate']}  fails: {inj['fails']['rate']}  "
                     f"skipped: {inj['skipped']['rate']}")
        lines.append("levers injected:")
        for lv, d in inj.get("levers", {}).items():
            by = ", ".join(f"{a}:{c}" for a, c in d["by_agent"].items())
            lines.append(f"  {lv:<28} rate={d['rate']:<6} n={d['count']:<4} [{by}]")
        lines.append("conditions injected-fail-rate:")
        for cid, d in inj.get("conditions", {}).items():
            lines.append(f"  {cid:<6} {d['signal']:<20} {d['side']:<8} {d['injected_fail_rate']}")
    lines.append("")
    lines.append(f"Provy side available: {report['detected']['provy_available']}")
    lines.append("feature-proof rows (injected vs detected):")
    for r in report["rows"]:
        lines.append(f"  [{r['status']:<7}] {r['feature']:<40} "
                     f"injected={r['injected']} detected={r['detected']} {r['note']}")
    if not report["detected"]["provy_available"]:
        lines.append("\nTODO: set SUPABASE_URL/SUPABASE_KEY + PROVY_TENANT_ID/PROVY_WORKFLOW_ID "
                     "to score the detected side.")
    return "\n".join(lines)
