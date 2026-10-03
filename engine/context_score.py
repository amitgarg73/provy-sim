"""Scorer for the context-manifest sets (#1505, lane L4). ADDS this file to provy-sim/engine/.

Given what Provy raised for a set (an export, below) and the committed ground truth, it computes recall,
false alarms, quote coverage and the right-cause rate per fault kind and per route, and states the
acceptance bars of SPEC 12.5 beside the numbers. It decides nothing from Provy's output about what was
injected: the plan file is the only source of that, and its hash is checked first (scripts/score_context.py).

THE EXPORT. A JSON object in the engine harness's export format (web/harness/engine-v1/export-live.ts and
analyse.ts), read for these keys. Anything else in the file is ignored, so a full engine export works as is.

  sessions        [{id, ext}]       session uuid and its external id (the plan's session_id)
  failed_checks   [{name, agent, session, reason, detail}]   failed check verdicts; the four context checks are
                                    context_freshness, source_not_approved, retrieval_empty, instructions_changed
  incidents       [{id, session | entity, agent, cause | quote, basis_kind, anomaly_kind}]
                                    the incidents raised. `cause` is the incident's root cause text (its quote).
                                    basis_kind is outcome_miss or deterministic_check; anomaly_kind is the
                                    engine's context family (stale_context, unapproved_source, empty_retrieval,
                                    instruction_changed) when it says one
  all_incidents   [{id, session}]   maps an incident id to its session when the incident row carries none
  decisions       [{ext, raise: [{basis: {kind}, quote, rootCauseAgent}]}]   the replay harness's own output
  coverage        {windowDays, asOf, byDoor: [{door, decisionSteps, withManifest, withAges, ...}]}   optional

A session the plan names and the export does not contain is counted `not_found` and scored as nothing was
raised for it, which is the strict reading for recall and the generous one for false alarms; the number is
printed so a short export is seen.

WHAT IS NOT SCORED HERE. Bar 6 (no model call touched a manifest) is the sentinel test plus the spend meter.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from engine import context as C

BARS = {
    "false_alarm_max": 0.05,
    "recall_min": 0.60,
    "instruction_min": 0.90,
    "quote_min": 0.90,
}

# the quote templates of SPEC 7, one pattern each. A family is read from the words of the quote and from
# the engine's own anomaly_kind when the export carries it.
FAMILY_PATTERNS = {
    "stale": re.compile(r"limit is \d+(\.\d+)? (days|hours)|days? old when the step ran|hours? old when the step ran"),
    "unapproved": re.compile(r"not on this fleet's approved list"),
    "empty": re.compile(r"retrieved 0 items"),
    "instruction": re.compile(r"ran under instructions that"),
}
ANOMALY_KIND = {"stale_context": "stale", "unapproved_source": "unapproved", "empty_retrieval": "empty", "instruction_changed": "instruction"}
KIND_FOR_CHECK = {v: k for k, v in C.CHECK_FOR_KIND.items()}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def pct(k: int, n: int) -> Optional[float]:
    return None if n == 0 else k / n


def family_of(incident: dict) -> Optional[str]:
    if incident.get("anomaly_kind") in ANOMALY_KIND:
        return ANOMALY_KIND[incident["anomaly_kind"]]
    quote = incident.get("quote") or incident.get("cause") or ""
    for fam, pat in FAMILY_PATTERNS.items():
        if pat.search(quote):
            return fam
    return None


# ── reading the export ───────────────────────────────────────────────────────────────────────────
def read_export(ex: dict, truth: list[dict]) -> dict:
    id_to_ext = {s["id"]: s["ext"] for s in ex.get("sessions", []) if s.get("id") and s.get("ext")}
    inc_session = {i["id"]: i.get("session") for i in ex.get("all_incidents", []) if i.get("id")}
    entity_ext = {t["entity_id"]: t["session_id"] for t in truth}
    present = set(id_to_ext.values())
    checks: dict[str, list[dict]] = {}
    for fc in ex.get("failed_checks", []):
        if fc.get("name") not in C.CHECK_NAMES:
            continue
        ext = fc.get("ext") or id_to_ext.get(fc.get("session"))
        if ext:
            checks.setdefault(ext, []).append({"name": fc["name"], "agent": fc.get("agent"), "reason": fc.get("reason")})
    incidents: dict[str, list[dict]] = {}
    for inc in ex.get("incidents", []):
        ext = inc.get("ext") or id_to_ext.get(inc.get("session")) or id_to_ext.get(inc_session.get(inc.get("id"))) or entity_ext.get(inc.get("entity"))
        if ext:
            incidents.setdefault(ext, []).append({"agent": inc.get("agent"), "quote": inc.get("quote") or inc.get("cause") or "",
                                                  "basis_kind": inc.get("basis_kind"), "anomaly_kind": inc.get("anomaly_kind")})
    for d in ex.get("decisions", []):
        ext = d["ext"]
        present.add(ext)
        for r in d.get("raise", []):
            incidents.setdefault(ext, []).append({"agent": r.get("rootCauseAgent"), "quote": r.get("quote") or (r.get("basis") or {}).get("quote") or "",
                                                  "basis_kind": (r.get("basis") or {}).get("kind"), "anomaly_kind": r.get("anomaly_kind")})
    return {"checks": checks, "incidents": incidents, "present": present, "coverage": ex.get("coverage")}


def _agent_ok(check_agent: Optional[str], fault_agent: str) -> bool:
    # "fleet" is the name the product writes for a rule that selects no agent (lib/context-checks-store.ts declaredRows); none, empty and "*" are the other fleet-wide spellings.
    return check_agent in (None, "", "*", "fleet") or check_agent == fault_agent


# ── scoring ──────────────────────────────────────────────────────────────────────────────────────
def score(truth: list[dict], ex: dict, include_case_variants: bool = False) -> dict:
    R = read_export(ex, truth)
    by_kind: dict[str, dict] = {}
    by_route: dict[str, dict] = {}
    by_route_kind: dict[str, dict] = {}

    def tally(d: dict, key: str) -> dict:
        return d.setdefault(key, {"injected": 0, "detected": 0, "detected_wrong_agent": 0, "settled_bad": 0, "named": 0, "right": 0, "quote": 0,
                                  "settled_good": 0, "settled_good_incident": 0})

    not_found = 0
    clean_n = 0
    false_alarms: list[dict] = []
    false_by_role: dict[str, dict] = {}
    excluded: dict[str, dict] = {}
    decoy_kinds: dict[str, dict] = {}
    cause_route_on_good = 0
    allowed_declared_on_good = 0
    wrong_cause: list[dict] = []
    named_total = 0
    named_with_quote = 0
    named_right_quote = 0

    for t in truth:
        ext = t["session_id"]
        if ext not in R["present"]:
            not_found += 1
        checks = R["checks"].get(ext, [])
        incs = R["incidents"].get(ext, [])
        fam_incs = [(i, family_of(i)) for i in incs]
        ctx_incs = [(i, f) for i, f in fam_incs if f]
        acceptable = set(t["expect"].get("acceptable_causes") or [])
        for i, f in ctx_incs:
            named_total += 1
            if i["quote"].strip():
                named_with_quote += 1
            if f not in acceptable:
                wrong_cause.append({"session": ext, "family": f, "agent": i["agent"]})

        if t["faults"]:
            for f in t["faults"]:
                kind = f["kind"]
                rows = [tally(by_kind, kind), tally(by_route, t["route"]), tally(by_route_kind, f"{t['route']}|{kind}")]
                names = [c for c in checks if c["name"] == C.CHECK_FOR_KIND[kind]]
                hit = any(_agent_ok(c["agent"], f["agent"]) for c in names)
                for r in rows:
                    r["injected"] += 1
                    if hit:
                        r["detected"] += 1
                    elif names:
                        r["detected_wrong_agent"] += 1
                if kind == "instruction":
                    continue
                bad = f["settles"] == "bad"
                match = [(i, fam) for i, fam in ctx_incs if fam == kind]
                for r in rows:
                    if bad:
                        r["settled_bad"] += 1
                        if match:
                            r["named"] += 1
                        if any(all(s in i["quote"] for s in t["expect"]["quote_contains"]) for i, _ in match):
                            r["right"] += 1
                        if any(i["quote"].strip() for i, _ in match):
                            r["quote"] += 1
                    else:
                        r["settled_good"] += 1
                if match and any(all(s in i["quote"] for s in (t["expect"]["quote_contains"] or [])) for i, _ in match) and bad:
                    named_right_quote += 1
                if not bad:
                    for i, fam in ctx_incs:
                        if i["basis_kind"] in (None, "outcome_miss"):
                            cause_route_on_good += 1
                            for r in rows:
                                r["settled_good_incident"] += 1
                        else:
                            allowed_declared_on_good += 1
            continue

        # a session with no fault
        reason = t.get("bar_excluded")
        found = bool(checks) or bool(ctx_incs)
        if t["decoys"]:
            for dk in sorted({d["kind"] for d in t["decoys"]}):          # sessions, not decoy entries
                row = decoy_kinds.setdefault(dk, {"sessions": 0, "raised": 0})
                row["sessions"] += 1
                row["raised"] += 1 if found else 0
        if reason and not (reason == "case_source_spec_conflict" and include_case_variants):
            row = excluded.setdefault(reason, {"sessions": 0, "raised": 0})
            row["sessions"] += 1
            row["raised"] += 1 if found else 0
            continue
        clean_n += 1
        role = "quiet" if t["fleet_quiet"] else "other"
        rr = false_by_role.setdefault(role, {"sessions": 0, "raised": 0})
        rr["sessions"] += 1
        if found:
            rr["raised"] += 1
            false_alarms.append({"session": ext, "class": t["class"], "route": t["route"], "checks": sorted({c["name"] for c in checks}),
                                 "families": sorted({f for _, f in ctx_incs})})

    # a fleet with no fault in the whole set is its own role
    fault_fleets = {t["fleet"] for t in truth if t["faults"]}
    if len(fault_fleets) < len({t["fleet"] for t in truth}):
        clean_fleets = {t["fleet"] for t in truth} - fault_fleets
        fa_clean = [s for s in truth if s["fleet"] in clean_fleets and not s["faults"] and not s.get("bar_excluded") and not s["fleet_quiet"]]
        raised_clean = sum(1 for s in fa_clean if R["checks"].get(s["session_id"]) or any(family_of(i) for i in R["incidents"].get(s["session_id"], [])))
        false_by_role["pure_clean_fleet"] = {"sessions": len(fa_clean), "raised": raised_clean}

    three = ("stale", "unapproved", "empty")
    inj = sum(by_kind.get(k, {}).get("injected", 0) for k in three)
    det = sum(by_kind.get(k, {}).get("detected", 0) for k in three)
    bad = sum(by_kind.get(k, {}).get("settled_bad", 0) for k in three)
    nam = sum(by_kind.get(k, {}).get("named", 0) for k in three)
    rig = sum(by_kind.get(k, {}).get("right", 0) for k in three)
    quo = sum(by_kind.get(k, {}).get("quote", 0) for k in three)
    ins_inj = by_kind.get("instruction", {}).get("injected", 0)
    ins_det = by_kind.get("instruction", {}).get("detected", 0)
    fa = len(false_alarms)

    cov = _coverage(truth, R["coverage"])
    out = {
        "sessions": len(truth), "not_found": not_found, "clean_sessions_scored": clean_n,
        "by_kind": by_kind, "by_route": by_route, "by_route_kind": by_route_kind,
        "false_alarms": {"n": fa, "of": clean_n, "rate": pct(fa, clean_n), "by_role": false_by_role, "sessions": false_alarms[:50]},
        "excluded_from_bars": excluded, "decoys": decoy_kinds,
        "wrong_cause_named": wrong_cause,
        "context_named_incidents": {"n": named_total, "with_quote": named_with_quote, "right_cause_and_quote_on_settled_bad": named_right_quote},
        "cause_route_incidents_on_settled_well_faults": cause_route_on_good,
        "declared_rule_incidents_on_settled_well_faults": allowed_declared_on_good,
        "bars": {},
    }
    lo = lambda k, n: round(wilson(k, n)[0], 3)           # noqa: E731
    hi = lambda k, n: round(wilson(k, n)[1], 3)           # noqa: E731
    out["bars"] = {
        "1_false_alarms_under_5_percent": {
            "bar": f"under {BARS['false_alarm_max']:.0%} of clean sessions carry any context finding (failed check or incident; observations not counted)",
            "value": pct(fa, clean_n), "k": fa, "n": clean_n, "wilson95": [lo(fa, clean_n), hi(fa, clean_n)],
            "pass": (fa / clean_n < BARS["false_alarm_max"]) if clean_n else None},
        "2a_recall_over_60_percent_failed_check": {
            "bar": f"over {BARS['recall_min']:.0%} of injected stale, unapproved and empty faults appear as a failed context check",
            "value": pct(det, inj), "k": det, "n": inj, "wilson95": [lo(det, inj), hi(det, inj)], "pass": (det / inj > BARS["recall_min"]) if inj else None},
        "2b_recall_over_60_percent_named_as_cause": {
            "bar": f"over {BARS['recall_min']:.0%} of those that settled badly are named as a context cause in an incident",
            "value": pct(nam, bad), "k": nam, "n": bad, "wilson95": [lo(nam, bad), hi(nam, bad)], "pass": (nam / bad > BARS["recall_min"]) if bad else None},
        "2c_instruction_changes_detected_over_90_percent": {
            "bar": f"over {BARS['instruction_min']:.0%} of instruction changes are detected on the first session with the new hash (a count, not a judgement)",
            "value": pct(ins_det, ins_inj), "k": ins_det, "n": ins_inj, "wilson95": [lo(ins_det, ins_inj), hi(ins_det, ins_inj)],
            "pass": (ins_det / ins_inj > BARS["instruction_min"]) if ins_inj else None},
        "3_context_causes_carry_a_quote": {
            "bar": f"{BARS['quote_min']:.0%} of context-named causes carry a quote, and the quote matches the injected item and limit",
            "value": pct(named_with_quote, named_total), "k": named_with_quote, "n": named_total,
            "right_quote_share_of_settled_bad_faults": pct(rig, bad), "right_quote_k": rig, "right_quote_n": bad,
            "pass": (named_with_quote / named_total >= BARS["quote_min"]) if named_total else None},
        "4_no_incident_from_the_cause_route_for_a_fault_that_settled_well": {
            "bar": "0 of the settled-well stale and unapproved faults raise an incident from the cause-candidate route (a declared-rule incident, decision 3, is counted apart)",
            "value": cause_route_on_good, "n": sum(by_kind.get(k, {}).get("settled_good", 0) for k in three), "pass": cause_route_on_good == 0},
        "5_coverage_is_exact": cov,
        "6_no_model_call_touched_a_manifest": {"bar": "the sentinel test and the spend meter for the sim tenants", "pass": None, "note": "NOT SCORED HERE"},
    }
    return out


COVERAGE_FIELDS = ("decisionSteps", "withManifest", "withAges", "withRetrieval", "withInstruction", "viaLinks")


def coverage_from_function(raw: dict, window_days: int, as_of: str) -> dict:
    """The `coverage` block of an export, from the answer of `ag_context_coverage` (migration 1508, the version of L11 that carries every figure per door).

    Every per-door figure the function returns is carried as it came, and the fleet totals ride along as `fleet`, so bar 5 can also check that the doors sum to the fleet.
    A door entry that lacks a figure is carried without it (not as zero): `_coverage` names the absence, so a function that predates the figures cannot pass as exact.
    """
    doors = []
    for d in raw.get("byDoor", []):
        row = {"door": d.get("door") or "none"}
        row.update({f: d[f] for f in COVERAGE_FIELDS + ("withCut",) if f in d})
        doors.append(row)
    return {"windowDays": window_days, "asOf": as_of, "byDoor": doors, "fleet": {f: raw.get(f) for f in COVERAGE_FIELDS + ("withCut",) if f in raw}}


def _coverage(truth: list[dict], got: Optional[dict]) -> dict:
    bar = "the coverage numbers equal the count of steps the generator wrote with and without a manifest, per door, to the step"
    if not got:
        return {"bar": bar, "pass": None, "note": "NOT MEASURED: the export carries no `coverage` (the output of ag_context_coverage)"}
    window = got.get("windowDays")
    as_of = datetime.fromisoformat(got["asOf"].replace("Z", "+00:00")) if got.get("asOf") else None
    want = C.expected_coverage(truth, as_of=as_of, window_days=window if as_of else None)
    have = {d["door"] or "none": d for d in got.get("byDoor", [])}
    diffs = []
    compared = 0
    for door in sorted(set(want) | set(have)):
        for field in COVERAGE_FIELDS:
            w = want.get(door, {}).get(field, 0)
            # A door entry that does not carry the figure is NOT a zero: "the function never said" is a difference of its own, never a pass.
            h = have[door].get(field) if door in have and field in have[door] else (None if door in have else 0)
            compared += 1
            if w != h:
                diffs.append({"door": door, "field": field, "expected": w, "got": h})
    # The doors, the unrouted group included, must sum to the fleet's own totals when the export carries them.
    fleet = got.get("fleet")
    if isinstance(fleet, dict):
        for field in COVERAGE_FIELDS:
            if field not in fleet:
                continue
            compared += 1
            total = sum(d.get(field) or 0 for d in have.values())
            if total != fleet[field]:
                diffs.append({"door": "(sum of doors against fleet)", "field": field, "expected": fleet[field], "got": total})
    return {"bar": bar, "pass": not diffs, "comparisons": compared, "differences": diffs[:40], "window": {"days": window, "asOf": got.get("asOf")}}


# ── the report ───────────────────────────────────────────────────────────────────────────────────
def _row(label: str, r: dict) -> str:
    def p(k, n):
        return "  n/a" if n == 0 else f"{100 * k / n:5.1f}%"
    return (f"  {label:<26} injected {r['injected']:>3}  failed check {r['detected']:>3} {p(r['detected'], r['injected'])}"
            f"   settled bad {r['settled_bad']:>3}  named {r['named']:>3} {p(r['named'], r['settled_bad'])}"
            f"  right {r['right']:>3} {p(r['right'], r['settled_bad'])}")


def report(set_name: str, role: str, result: dict, clean_read: bool, truth_sha: str) -> str:
    L = []
    L.append(f"CONTEXT MANIFEST SCORE  set {set_name}  ({role})")
    L.append(f"  ground truth sha256 {truth_sha}")
    L.append(f"  read status: {'first read of this set' if clean_read else 'REREAD. NOT CLEAN: this set was read before, so these numbers are development numbers'}")
    L.append(f"  sessions {result['sessions']}, not found in the export {result['not_found']}, clean sessions scored {result['clean_sessions_scored']}")
    L.append("")
    L.append("PER FAULT KIND (n beside every percentage)")
    for k in ("stale", "unapproved", "empty", "instruction"):
        if k in result["by_kind"]:
            L.append(_row(k, result["by_kind"][k]))
    L.append("")
    L.append("PER ROUTE (all fault kinds together; instruction changes counted for check only)")
    for k in sorted(result["by_route"]):
        L.append(_row(k, result["by_route"][k]))
    L.append("")
    L.append("FALSE ALARMS")
    fa = result["false_alarms"]
    L.append(f"  {fa['n']} of {fa['of']} clean sessions carried a context finding" + ("" if fa["rate"] is None else f" ({100 * fa['rate']:.2f}%)"))
    for role_, r in fa["by_role"].items():
        L.append(f"    {role_:<18} {r['raised']} of {r['sessions']}")
    for reason, r in result["excluded_from_bars"].items():
        L.append(f"  excluded from every bar, reported: {reason}: {r['raised']} of {r['sessions']} sessions raised something")
    for kind, r in sorted(result["decoys"].items()):
        L.append(f"  decoy {kind:<20} {r['raised']} of {r['sessions']} raised something")
    L.append("")
    L.append("ACCEPTANCE BARS (SPEC 12.5), measured once on CM-B and CM-C, false alarms also on CM-D")
    for k, b in result["bars"].items():
        verdict = "NOT MEASURED" if b.get("pass") is None else ("PASS" if b["pass"] else "FAIL")
        val = ""
        if "k" in b and b.get("n") and b.get("wilson95"):
            val = f"  {b['k']} of {b['n']} = {100 * b['value']:.1f}%  (95% interval {100 * b['wilson95'][0]:.0f} to {100 * b['wilson95'][1]:.0f}%)"
        elif "k" in b and b.get("n"):
            val = f"  {b['k']} of {b['n']} = {100 * b['value']:.1f}%"
        elif "value" in b and b["value"] is not None:
            val = f"  {b['value']}" + (f" of {b['n']} settled-well faults" if b.get("n") else "")
        L.append(f"  [{verdict:<12}] {k}")
        L.append(f"                 bar: {b['bar']}")
        if val:
            L.append(f"                {val}")
        if b.get("note"):
            L.append(f"                 {b['note']}")
        if b.get("differences"):
            L.append(f"                 {len(b['differences'])} differences, first: {b['differences'][0]}")
    if result["wrong_cause_named"]:
        L.append("")
        L.append(f"A context cause was named that the plan does not allow, {len(result['wrong_cause_named'])} times; first: {result['wrong_cause_named'][0]}")
    L.append("")
    L.append("If a bar is missed the build does not ship. The miss is written up as a finding. A threshold is not moved after reading a hold-out.")
    return "\n".join(L)


# ── running it on a set, with the hold-out ledger ────────────────────────────────────────────────
class HoldOutRefused(RuntimeError):
    pass


def run_scoring(set_name: str, export_path: str, data_dir: str, ledger_path: Optional[str] = None, confirm_one_shot: bool = False,
                allow_reread: bool = False, include_case_variants: bool = False) -> tuple[dict, str]:
    """Verify the ground truth, apply the hold-out discipline, score, and return (result, text report).

    A hold-out is read once. The first read is recorded in the ledger; a second is refused unless the caller says
    --allow-reread, and then the result is stamped NOT CLEAN. A hold-out also needs --confirm-one-shot, so a read is
    never an accident. A development set needs neither."""
    import hashlib
    import os
    from engine import context_truth as T
    truth_sha = T.verify_ground_truth(set_name, data_dir)
    rec = json.load(open(os.path.join(data_dir, T.HASH_FILE)))[set_name]
    ledger_path = ledger_path or os.path.join(data_dir, "holdout_reads.jsonl")
    export_bytes = open(export_path, "rb").read()
    prior = []
    if os.path.exists(ledger_path):
        prior = [json.loads(line) for line in open(ledger_path) if line.strip()]
        prior = [r for r in prior if r["set"] == set_name]
    clean_read = True
    if rec["hold_out"]:
        if not confirm_one_shot:
            raise HoldOutRefused(f"{set_name} is a hold-out and is read once. Pass --confirm-one-shot when the build is frozen and the bars are being checked.")
        if prior and not allow_reread:
            raise HoldOutRefused(f"{set_name} was already read on {prior[0]['read_at']}. A hold-out is not read twice. --allow-reread scores it anyway and stamps it NOT CLEAN.")
        clean_read = not prior
    truth = C.load_plans(os.path.join(data_dir, T.truth_filename(set_name)))
    result = score(truth, json.loads(export_bytes), include_case_variants=include_case_variants)
    result["set"], result["clean_read"], result["ground_truth_sha256"] = set_name, clean_read, truth_sha
    result["export_sha256"] = hashlib.sha256(export_bytes).hexdigest()
    if rec["hold_out"]:
        with open(ledger_path, "a") as f:
            f.write(json.dumps({"set": set_name, "read_at": datetime.now(timezone.utc).isoformat(), "export_sha256": result["export_sha256"],
                                "ground_truth_sha256": truth_sha, "clean": clean_read}) + "\n")
    return result, report(set_name, rec["role"], result, clean_read, truth_sha)
