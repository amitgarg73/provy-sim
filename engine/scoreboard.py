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

# Sentinel for "no window was given": earlier than any row this system can hold.
_ALL_TIME = "1970-01-01T00:00:00+00:00"

# The silent family: levers that leave the estimate green and diverge only on reality.
# Their culprit truth is what the console scores Provy's attribution against. The
# commitment_* faults are the mock-SoR settlement failures (support_ci): the agent
# claimed the refund was done, the settled ledger silently disagreed.
_SILENT_LEVERS = {
    "silent_wrong", "silent_staleness", "silent_unsupported",
    "silent_incomplete", "silent_policy", "silent_missed_action",
    "commitment_unsettled", "commitment_wrong_amount", "commitment_wrong_target",
    "commitment_duplicate",
}


# ── Injected-truth aggregation ───────────────────────────────────────────────

def aggregate_injected(records: list[dict], contract: list[Criterion], costs: dict | None = None) -> dict:
    """Compute injected rates and honest denominators from the ledger. When `costs` (a per-lever
    dollar map) is given, also compute the value at risk = sum(count * cost)."""
    n = len(records)
    if n == 0:
        return {"runs": 0}
    costs = costs or {}

    lever_counts: Counter = Counter()
    lever_by_agent: dict[str, Counter] = {}
    # Per-entity culprit truth for diverged runs, so the console can score Provy's
    # attribution (its named agent) against the agent the sim actually targeted.
    attribution_truth: list[dict] = []
    for rec in records:
        diverged = bool(rec.get("diverged"))
        for f in rec.get("faults", []):
            lever_counts[f["lever"]] += 1
            lever_by_agent.setdefault(f["lever"], Counter())[f.get("agent") or "-"] += 1
            if diverged and f["lever"] in _SILENT_LEVERS and f.get("agent") and rec.get("entity_id"):
                attribution_truth.append({
                    "entity_id": rec["entity_id"], "lever": f["lever"], "agent": f["agent"],
                })

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
        # Capped so the run summary stays small; the console joins these by entity_id.
        "attribution_truth": attribution_truth[:1000],
        # Value at risk = count * per-failure cost. Empty when no cost map is supplied.
        "value": {
            "at_risk": round(sum(lever_counts[lv] * costs.get(lv, 0) for lv in lever_counts), 2),
            "by_lever": {lv: round(lever_counts[lv] * costs[lv], 2) for lv in lever_counts if costs.get(lv)},
        },
    }


# ── Provy side (read-only) ───────────────────────────────────────────────────

class ProvyQuery:
    """Reads Provy's own conclusions so the harness can score detection.

    ⛔ THIS COMPARES INJECTED TRUTH TO PROVY'S CONCLUSION. IT NEVER RE-DERIVES A CONCLUSION FROM THE
    TRACES. That rule is written here because breaking it produced a false finding on 19 Sep 2026: a
    query asked `ag_traces.payload->'tool_output' IS NULL` and read the answer as "no output was
    reported", when in fact every span had been offloaded to R2 and the whole `payload` column was
    null. Provy had read the hydrated body, seen `{"changes":[]}`, and been right. The harness had
    read a stripped proxy and been wrong.

    So: read what Provy WROTE (`ag_outcome_ledger`, `ag_tool_attributions`, `ag_incidents`,
    `ag_outcome_criterion_results`). Those rows are Provy's answer. `ag_traces` is its input, is
    lossy at rest, and is not the harness's business.

    ⛔ AND EVERY ATTRIBUTION CARRIES A CONFIDENCE. Scoring `method` alone reports a fleet as
    confidently wrong when `applyBaseRateDiscipline` has already qualified the lead down to `low`.
    Counting a low-confidence candidate as a confident accusation is the same defect Provy sells
    against, committed by its own test harness.
    """

    def __init__(self, tenant_id: str | None = None, workflow_id: str | None = None,
                 since: str | None = None):
        self.tenant_id = tenant_id or os.environ.get("PROVY_TENANT_ID", "")
        self.workflow_id = workflow_id or os.environ.get("PROVY_WORKFLOW_ID", "")
        # ⛔ A WINDOW IS NOT OPTIONAL ON A FLEET WITH HISTORY. Without it the score mixes this run
        # with every earlier one and the number quietly describes neither.
        #
        # ⛔ AND THE DEFAULT MUST NOT BE "NOW". The first draft of this line defaulted to the current
        # time, which scores a window containing nothing and reports zeros as if they were findings.
        # An absent window means all time, and the report says so out loud rather than implying a
        # run-scoped number it did not compute.
        self.since = since or os.environ.get("PROVY_SCORE_SINCE") or _ALL_TIME
        self.windowed = self.since != _ALL_TIME
        self.error: str | None = None
        self._db = self._connect()

    def _connect(self):
        """Postgres directly, the way scripts/fleet_doctor.py does.

        ⛔ A MISSING DEPENDENCY AND A MISSING CREDENTIAL MUST NOT PRINT THE SAME SENTENCE. The old
        version imported inside a bare `except Exception`, so an uninstalled driver reported as
        "no credentials" and the fix people reached for was never the one they needed.
        """
        url = os.environ.get("PROVY_DB_URL") or os.environ.get("CERTIFY_DB_URL", "")
        if not url:
            self.error = ("set PROVY_DB_URL to the PRE-PROD connection string "
                          "(the project ref is fpuyabfxtrzwciehfetk)")
            return None
        try:
            import psycopg2  # noqa: F401
        except ImportError:
            self.error = "psycopg2 is not installed: .venv/bin/pip install psycopg2-binary"
            return None
        try:
            import psycopg2
            return psycopg2.connect(url)
        except Exception as e:                                    # noqa: BLE001
            self.error = f"could not connect to the Provy database: {e}"
            return None

    @property
    def available(self) -> bool:
        return self._db is not None and bool(self.workflow_id)

    def _rows(self, sql: str, args: tuple):
        with self._db.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()

    def contract_met_rate(self) -> Optional[float]:
        """Conditions Provy graded as met, over conditions it could measure, in the window."""
        if not self.available:
            return None
        rows = self._rows(
            """select count(*) filter (where cr.measurable and cr.passed),
                      count(*) filter (where cr.measurable)
                 from ag_outcome_criterion_results cr
                 join ag_outcome_evaluations e on e.id = cr.evaluation_id
                 join ag_sessions s on s.id = e.session_id
                where s.workflow_id = %s and cr.created_at >= %s""",
            (self.workflow_id, self.since))
        met, measurable = rows[0] if rows else (0, 0)
        return round(met / measurable, 4) if measurable else None

    def reconciled_divergence_rate(self) -> Optional[float]:
        """Diverged over settled, keyed the way §6 of the attribution doc insists: by work item."""
        if not self.available:
            return None
        rows = self._rows(
            """select count(distinct entity_id) filter (where reconciliation = 'diverged'),
                      count(distinct entity_id) filter (where reconciliation in ('diverged','matched'))
                 from ag_outcome_ledger
                where workflow_id = %s and created_at >= %s""",
            (self.workflow_id, self.since))
        diverged, settled = rows[0] if rows else (0, 0)
        return round(diverged / settled, 4) if settled else None

    def incident_count(self) -> Optional[int]:
        if not self.available:
            return None
        rows = self._rows(
            "select count(*) from ag_incidents where workflow_id = %s and created_at >= %s",
            (self.workflow_id, self.since))
        return rows[0][0] if rows else None

    def attribution_mix(self) -> Optional[dict]:
        """What Provy concluded about cause, split by confidence.

        The split is the point. "Provy named a tool" and "Provy offered a low-confidence candidate
        it had already flagged as unsupported" are different claims, and only one of them is a
        problem worth reporting.
        """
        if not self.available:
            return None
        rows = self._rows(
            """select a.method, a.confidence,
                      a.evidence->>'base_rate_verdict', count(*)
                 from ag_tool_attributions a
                 join ag_sessions s on s.id = a.session_id
                where s.workflow_id = %s and a.created_at >= %s
                group by 1,2,3""",
            (self.workflow_id, self.since))
        out: dict = {"by_method": {}, "named_with_confidence": 0, "named_low_only": 0,
                     "refused": 0}
        for method, confidence, verdict, n in rows:
            key = f"{method}/{confidence}"
            out["by_method"][key] = {"n": n, "base_rate_verdict": verdict}
            if method == "undetermined":
                out["refused"] += n
            elif confidence in ("high", "medium"):
                out["named_with_confidence"] += n
            else:
                out["named_low_only"] += n
        return out

    def silent_checks(self) -> Optional[list]:
        """Checks that are enabled and have produced nothing, ever (#1027).

        Not windowed on purpose: "has never graded anything" is a claim about the check's whole
        life, and a window would make a dormant check look merely quiet.
        """
        if not self.available:
            return None
        rows = self._rows(
            """select c.layer, c.eval_name
                 from ag_eval_configs c
                where c.workflow_id = %s and c.enabled
                  and not exists (select 1 from ag_evals e
                                   where e.workflow_id = c.workflow_id
                                     and e.eval_name = c.eval_name)
                group by 1,2 order by 1,2""",
            (self.workflow_id,))
        return [{"layer": r[0], "eval_name": r[1]} for r in rows]


# ── The report ───────────────────────────────────────────────────────────────

def build_report(records: list[dict], contract: list[Criterion],
                 provy: ProvyQuery | None = None, costs: dict | None = None) -> dict:
    injected = aggregate_injected(records, contract, costs)
    provy = provy or ProvyQuery()

    detected = {
        "provy_available": provy.available,
        "why_unavailable": provy.error,
        "window_since": provy.since,
        "contract_met_rate": provy.contract_met_rate(),
        "reconciled_divergence_rate": provy.reconciled_divergence_rate(),
        "incident_count": provy.incident_count(),
        "attribution_mix": provy.attribution_mix(),
        "silent_checks": provy.silent_checks(),
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
    det = report["detected"]
    if not det["provy_available"]:
        lines.append("\n⛔ The detected side did not run, so every row above is the injected side "
                     "talking to itself.")
        lines.append(f"   reason: {det.get('why_unavailable') or 'PROVY_WORKFLOW_ID is not set'}")
        return "\n".join(lines)

    if det.get("window_since") == _ALL_TIME:
        lines.append("\n⚠ scored against ALL of this fleet's history, not just this run.\n"
                     "   Set PROVY_SCORE_SINCE to the batch start time to scope it.")
    else:
        lines.append(f"\nscored against Provy since {det['window_since']}")

    mix = det.get("attribution_mix")
    if mix:
        lines.append("what Provy concluded about cause:")
        lines.append(f"  named with high/medium confidence : {mix['named_with_confidence']}")
        lines.append(f"  offered only as low confidence    : {mix['named_low_only']}")
        lines.append(f"  refused to name one               : {mix['refused']}")
        for key, d in sorted(mix["by_method"].items()):
            verdict = d["base_rate_verdict"] or "no base rate recorded"
            lines.append(f"    {key:<30} n={d['n']:<4} {verdict}")
        # ⛔ The only line here that is evidence of a problem. Everything above is description.
        if mix["named_with_confidence"] == 0 and mix["named_low_only"]:
            lines.append("  ⚠ every cause this run was low confidence: Provy committed to nothing")

    silent = det.get("silent_checks")
    if silent:
        lines.append(f"⛔ {len(silent)} enabled check(s) on this fleet have NEVER produced a result "
                     f"(#1027). An unfired check is not a passing one:")
        for c in silent:
            lines.append(f"    L{c['layer']}  {c['eval_name']}")
    elif silent == []:
        lines.append("every enabled check on this fleet has produced at least one result")

    return "\n".join(lines)
