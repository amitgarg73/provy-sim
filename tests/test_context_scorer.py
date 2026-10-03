"""The scorer (#1505, L4), driven by exports made from the committed ground truth.

Each export here is SYNTHETIC: it is built from the plan to say what a perfect, a partial or a noisy Provy would
have raised, so the scorer's arithmetic and the bars can be tested. None of it is a measurement of Provy.
"""
import copy
import json
import os
import shutil

import pytest

import _context_path  # noqa: F401
from engine import context as C
from engine import context_score as S
from engine import context_truth as T

DATA = os.path.join(_context_path.TREE, "data")


def plans(name):
    return C.load_plans(os.path.join(DATA, T.truth_filename(name)))


def quote(f):
    if f["kind"] == "stale":
        return (f'The {f["agent"]} agent\'s {f["step"]} step used "{f["source"]}/{f["id"]}", current as of {f["as_of"][:10]}. '
                f"This fleet's limit is {f['limit_days']} days; it was {int(f['age_days'])} days old when the step ran.")
    if f["kind"] == "unapproved":
        return f'The {f["agent"]} agent\'s {f["step"]} step used "{f["source"]}/{f["id"]}", which is not on this fleet\'s approved list.'
    if f["kind"] == "empty":
        return f"The {f['agent']} agent's {f['step']} step retrieved 0 items. It retrieved at least one in 97 of its last 100 recorded retrievals."
    return f"The {f['agent']} agent's {f['step']} step ran under instructions that were new on 2026-09-01 (version v2, never seen before this session)."


def perfect(truth, drop=lambda t, f: False, noise=lambda t: None):
    """What a flawless Provy raises: every fault a failed check, every settled-bad context fault an incident with its quote."""
    sessions, checks, incidents = [], [], []
    for n, t in enumerate(truth):
        sid = f"uuid-{n}"
        sessions.append({"id": sid, "ext": t["session_id"]})
        for f in t["faults"]:
            if drop(t, f):
                continue
            checks.append({"name": C.CHECK_FOR_KIND[f["kind"]], "agent": f["agent"], "session": sid, "reason": quote(f)})
            if f["kind"] != "instruction" and f["settles"] == "bad":
                incidents.append({"id": f"inc-{n}", "session": sid, "agent": f["agent"], "cause": quote(f), "basis_kind": "outcome_miss"})
        extra = noise(t)
        if extra:
            checks += [dict(c, session=sid) for c in extra.get("checks", [])]
            incidents += [dict(i, session=sid, id=f"x-{n}") for i in extra.get("incidents", [])]
    return {"sessions": sessions, "failed_checks": checks, "incidents": incidents}


def run(name, ex, **kw):
    return S.score(plans(name), ex, **kw)


# ── a perfect export passes every bar that can be measured ───────────────────────────────────────
def test_a_flawless_export_passes_every_bar():
    r = run("CM-B", perfect(plans("CM-B")))
    for k, b in r["bars"].items():
        if not k.startswith(("5_", "6_")):
            assert b["pass"] is True, (k, b)
    assert r["bars"]["2a_recall_over_60_percent_failed_check"]["k"] == 64
    assert r["false_alarms"]["n"] == 0 and r["not_found"] == 0
    assert r["bars"]["5_coverage_is_exact"]["pass"] is None and "NOT MEASURED" in r["bars"]["5_coverage_is_exact"]["note"]
    assert r["bars"]["6_no_model_call_touched_a_manifest"]["pass"] is None


def test_counts_per_kind_and_per_route_add_up_to_the_ground_truth():
    truth = plans("CM-B")
    r = run("CM-B", perfect(truth))
    assert sum(v["injected"] for v in r["by_kind"].values()) == 76
    assert {k: v["injected"] for k, v in r["by_kind"].items()} == {"stale": 24, "unapproved": 24, "empty": 16, "instruction": 12}
    assert sum(v["injected"] for v in r["by_route"].values()) == 76
    assert sum(v["injected"] for v in r["by_route_kind"].values()) == 76
    assert all(v["detected"] == v["injected"] for v in r["by_route"].values())


# ── recall ───────────────────────────────────────────────────────────────────────────────────────
def test_recall_falls_when_checks_are_missing_and_the_bar_fails_at_exactly_sixty_percent():
    truth = plans("CM-B")
    three = [f for t in truth for f in t["faults"] if f["kind"] != "instruction"]
    keep = set(id(f) for f in three[: int(0.6 * len(three))])      # 38 of 64 = 59.4%
    ex = perfect(truth, drop=lambda t, f: f["kind"] != "instruction" and id(f) not in keep)
    r = run("CM-B", ex)
    b = r["bars"]["2a_recall_over_60_percent_failed_check"]
    assert (b["k"], b["n"]) == (38, 64) and b["pass"] is False
    keep = set(id(f) for f in three[:39])                          # 39 of 64 = 60.9%
    r = run("CM-B", perfect(truth, drop=lambda t, f: f["kind"] != "instruction" and id(f) not in keep))
    assert r["bars"]["2a_recall_over_60_percent_failed_check"]["pass"] is True


def test_a_check_on_the_wrong_agent_is_not_a_detection_and_is_counted_apart():
    truth = plans("CM-B")
    ex = perfect(truth)
    for c in ex["failed_checks"]:
        if c["name"] == "context_freshness":
            c["agent"] = "no-such-agent"
    r = run("CM-B", ex)
    assert r["by_kind"]["stale"]["detected"] == 0 and r["by_kind"]["stale"]["detected_wrong_agent"] == 24


def test_a_fleet_wide_rule_is_written_by_the_product_under_the_agent_name_fleet_and_that_is_a_detection():
    """L5 (#1505): a Guardrails rule that selects no agent (the simulation's declared rules select a step type only) is written by the product with agent 'fleet'
    (web/lib/context-checks-store.ts declaredRows). The scorer read only none, empty and '*' as fleet-wide, so against a live export every detection read as zero."""
    truth = plans("CM-B")
    ex = perfect(truth)
    for c in ex["failed_checks"]:
        if c["name"] in ("context_freshness", "source_not_approved"):
            c["agent"] = "fleet"
    r = run("CM-B", ex)
    assert r["by_kind"]["stale"]["detected"] == r["by_kind"]["stale"]["injected"]
    assert r["by_kind"]["unapproved"]["detected"] == r["by_kind"]["unapproved"]["injected"]
    assert r["by_kind"]["stale"]["detected_wrong_agent"] == 0


def test_instruction_changes_are_scored_on_detection_alone_and_the_bar_is_ninety_percent():
    truth = plans("CM-B")
    ex = perfect(truth, drop=lambda t, f: f["kind"] == "instruction" and t["session_index"] % 7 == 0)
    r = run("CM-B", ex)
    b = r["bars"]["2c_instruction_changes_detected_over_90_percent"]
    assert b["n"] == 12 and b["pass"] is (b["k"] / 12 > 0.9)
    assert "named" not in r["by_kind"]["instruction"] or r["by_kind"]["instruction"]["named"] == 0


def test_cause_naming_is_read_over_the_faults_that_settled_badly_and_needs_the_right_family():
    truth = plans("CM-B")
    ex = perfect(truth)
    # every incident names the wrong family for its fault: the quote says "empty" for a stale fault
    for i in ex["incidents"]:
        i["cause"] = "The triage agent's decision step retrieved 0 items."
    r = run("CM-B", ex)
    assert r["by_kind"]["empty"]["named"] > 0 and r["by_kind"]["stale"]["named"] == 0
    assert r["bars"]["2b_recall_over_60_percent_named_as_cause"]["pass"] is False
    assert len(r["wrong_cause_named"]) > 0                      # an empty-retrieval cause on a stale session is not allowed


def test_the_right_cause_needs_the_item_the_limit_and_the_agent_in_the_quote():
    truth = plans("CM-B")
    ex = perfect(truth)
    for i in ex["incidents"]:
        if "limit is" in i["cause"]:
            i["cause"] = "This fleet's limit is 30 days; it was 40 days old when the step ran."      # right family, no item, no agent
    r = run("CM-B", ex)
    assert r["by_kind"]["stale"]["named"] == r["by_kind"]["stale"]["settled_bad"] > 0
    assert r["by_kind"]["stale"]["right"] == 0 and r["by_kind"]["unapproved"]["right"] == r["by_kind"]["unapproved"]["settled_bad"]


# ── false alarms ─────────────────────────────────────────────────────────────────────────────────
def noisy(frac, name="CM-B"):
    truth = plans(name)
    clean = [t["session_id"] for t in truth if t["class"] == "clean"]
    picked = set(clean[:: max(1, round(1 / frac))])
    return perfect(truth, noise=lambda t: {"checks": [{"name": "context_freshness", "agent": "triage", "reason": "x"}]} if t["session_id"] in picked else None)


def test_false_alarm_rate_is_failed_checks_or_incidents_over_clean_sessions_and_the_bar_is_five_percent():
    r = run("CM-B", noisy(0.10))
    fa = r["false_alarms"]
    assert fa["n"] > 0 and fa["of"] > 500 and r["bars"]["1_false_alarms_under_5_percent"]["pass"] is False
    r = run("CM-B", noisy(0.02))
    assert r["bars"]["1_false_alarms_under_5_percent"]["pass"] is True and r["false_alarms"]["n"] > 0


def test_an_observation_is_not_a_false_alarm_and_a_non_context_check_is_ignored():
    truth = plans("CM-B")
    ex = perfect(truth)
    sid = ex["sessions"][next(i for i, t in enumerate(truth) if t["class"] == "clean")]["id"]
    ex["failed_checks"].append({"name": "tool_error_rate", "agent": "*", "session": sid, "reason": "x"})
    ex["observations"] = [{"session": sid, "signal": "context_stale"}]
    assert run("CM-B", ex)["false_alarms"]["n"] == 0


def test_a_context_incident_on_a_clean_session_is_a_false_alarm_and_a_wrong_cause():
    truth = plans("CM-B")
    ex = perfect(truth)
    i = next(i for i, t in enumerate(truth) if t["class"] == "clean")
    ex["incidents"].append({"id": "z", "session": ex["sessions"][i]["id"], "agent": "triage", "cause": "The triage agent's decision step retrieved 0 items.",
                            "basis_kind": "outcome_miss"})
    r = run("CM-B", ex)
    assert r["false_alarms"]["n"] == 1 and len(r["wrong_cause_named"]) == 1


def test_the_quiet_fleet_and_the_clean_fleet_are_their_own_roles_in_the_false_alarm_split():
    d = run("CM-D", perfect(plans("CM-D")))
    assert "quiet" in d["false_alarms"]["by_role"] and d["false_alarms"]["by_role"]["quiet"]["sessions"] > 100
    c = run("CM-C", perfect(plans("CM-C")))
    assert c["false_alarms"]["by_role"]["pure_clean_fleet"]["sessions"] > 100


def test_a_quiet_day_empty_that_raises_is_a_false_alarm_on_the_quiet_fleet():
    truth = plans("CM-D")
    qe = [t for t in truth if any(d["kind"] == "quiet_empty" for d in t["decoys"])]
    assert len(qe) > 20
    ids = {t["session_id"] for t in qe[:10]}
    ex = perfect(truth, noise=lambda t: {"checks": [{"name": "retrieval_empty", "agent": "triage", "reason": "x"}]} if t["session_id"] in ids else None)
    r = run("CM-D", ex)
    assert r["false_alarms"]["by_role"]["quiet"]["raised"] == 10
    assert r["decoys"]["quiet_empty"]["raised"] == 10


# ── the case-variant decoy is a spec conflict, outside every bar by default ───────────────────────
def test_case_variant_sessions_are_reported_apart_and_enter_the_bar_only_when_asked():
    truth = plans("CM-B")
    cv = {t["session_id"] for t in truth if any(d["kind"] == "case_source" for d in t["decoys"])}
    assert len(cv) == 12
    ex = perfect(truth, noise=lambda t: {"checks": [{"name": "source_not_approved", "agent": "triage", "reason": "x"}]} if t["session_id"] in cv else None)
    r = run("CM-B", ex)
    assert r["false_alarms"]["n"] == 0 and r["excluded_from_bars"]["case_source_spec_conflict"] == {"sessions": 12, "raised": 12}
    r = run("CM-B", ex, include_case_variants=True)
    assert r["false_alarms"]["n"] == 12


# ── quotes ───────────────────────────────────────────────────────────────────────────────────────
def test_a_context_cause_without_a_quote_fails_the_quote_bar():
    truth = plans("CM-B")
    ex = perfect(truth)
    for i in ex["incidents"]:
        i["anomaly_kind"] = {"stale": "stale_context", "unapproved": "unapproved_source", "empty": "empty_retrieval"}[family(i)]
        i["cause"] = ""
    r = run("CM-B", ex)
    b = r["bars"]["3_context_causes_carry_a_quote"]
    assert b["n"] > 20 and b["k"] == 0 and b["pass"] is False


def family(i):
    return S.family_of({"quote": i["cause"]}) or ("stale" if "limit" in i["cause"] else "unapproved" if "approved" in i["cause"] else "empty")


# ── settled well ─────────────────────────────────────────────────────────────────────────────────
def test_an_incident_from_the_cause_route_on_a_fault_that_settled_well_fails_bar_four():
    truth = plans("CM-B")
    ex = perfect(truth)
    n, t = next((n, t) for n, t in enumerate(truth) if t["faults"] and t["faults"][0]["kind"] == "stale" and t["faults"][0]["settles"] == "good")
    ex["incidents"].append({"id": "g", "session": ex["sessions"][n]["id"], "agent": "triage", "cause": quote(t["faults"][0]), "basis_kind": "outcome_miss"})
    r = run("CM-B", ex)
    b = r["bars"]["4_no_incident_from_the_cause_route_for_a_fault_that_settled_well"]
    assert b["value"] == 1 and b["pass"] is False


def test_a_declared_rule_incident_on_a_fault_that_settled_well_is_decision_three_and_is_counted_apart():
    truth = plans("CM-B")
    ex = perfect(truth)
    n, t = next((n, t) for n, t in enumerate(truth) if t["faults"] and t["faults"][0]["kind"] == "stale" and t["faults"][0]["settles"] == "good")
    ex["incidents"].append({"id": "g", "session": ex["sessions"][n]["id"], "agent": "triage", "cause": quote(t["faults"][0]), "basis_kind": "deterministic_check"})
    r = run("CM-B", ex)
    assert r["bars"]["4_no_incident_from_the_cause_route_for_a_fault_that_settled_well"]["pass"] is True
    assert r["declared_rule_incidents_on_settled_well_faults"] == 1


# ── reading the export ───────────────────────────────────────────────────────────────────────────
def test_an_incident_is_placed_by_session_by_id_through_all_incidents_or_by_entity():
    truth = plans("CM-B")
    n, t = next((n, t) for n, t in enumerate(truth) if t["faults"] and t["faults"][0]["kind"] == "stale" and t["faults"][0]["settles"] == "bad")
    f = t["faults"][0]
    base = {"sessions": [{"id": "u", "ext": t["session_id"]}], "failed_checks": [{"name": "context_freshness", "agent": f["agent"], "session": "u"}]}
    by_session = dict(base, incidents=[{"id": "1", "session": "u", "cause": quote(f)}])
    by_id = dict(base, incidents=[{"id": "1", "cause": quote(f)}], all_incidents=[{"id": "1", "session": "u"}])
    by_entity = dict(base, incidents=[{"id": "1", "entity": t["entity_id"], "cause": quote(f)}])
    for ex in (by_session, by_id, by_entity):
        r = run("CM-B", ex)
        assert r["by_kind"]["stale"]["named"] == 1, ex


def test_the_replay_harness_decisions_format_is_read_too():
    truth = plans("CM-B")
    n, t = next((n, t) for n, t in enumerate(truth) if t["faults"] and t["faults"][0]["kind"] == "stale" and t["faults"][0]["settles"] == "bad")
    f = t["faults"][0]
    ex = {"decisions": [{"ext": t["session_id"], "raise": [{"basis": {"kind": "outcome_miss"}, "quote": quote(f), "rootCauseAgent": f["agent"]}]}]}
    r = run("CM-B", ex)
    assert r["by_kind"]["stale"]["named"] == 1


def test_sessions_missing_from_the_export_are_counted_and_nothing_is_invented_for_them():
    r = run("CM-B", {"sessions": [], "failed_checks": [], "incidents": []})
    assert r["not_found"] == 720 and r["bars"]["2a_recall_over_60_percent_failed_check"]["k"] == 0


# ── coverage ─────────────────────────────────────────────────────────────────────────────────────
def test_coverage_is_compared_per_door_to_the_step_and_a_difference_is_named():
    truth = plans("CM-B")
    as_of = "2026-09-29T00:00:00Z"
    want = C.expected_coverage(truth, as_of=C.parse_iso("2026-09-29T00:00:00.000Z"), window_days=30)
    ex = perfect(truth)
    ex["coverage"] = {"windowDays": 30, "asOf": as_of, "byDoor": list(want.values())}
    assert run("CM-B", ex)["bars"]["5_coverage_is_exact"]["pass"] is True
    bad = copy.deepcopy(ex)
    bad["coverage"]["byDoor"][0]["withManifest"] -= 1
    b = run("CM-B", bad)["bars"]["5_coverage_is_exact"]
    assert b["pass"] is False and b["differences"][0]["field"] == "withManifest"


def _function_answer(want):
    """An answer shaped like ag_context_coverage after L11: every figure per door, and the fleet totals."""
    doors = [{**{k: v for k, v in d.items() if k != "door"}, "door": None if d["door"] == "none" else d["door"], "withCut": 0} for d in want.values()]
    total = {f: sum(d[f] for d in want.values()) for f in S.COVERAGE_FIELDS}
    return {**total, "withCut": 0, "byDoor": doors}


def test_bar_5_is_computed_against_the_functions_own_per_route_answer_and_names_what_it_never_said():
    truth = plans("CM-B")
    as_of = "2026-09-29T00:00:00Z"
    want = C.expected_coverage(truth, as_of=C.parse_iso("2026-09-29T00:00:00.000Z"), window_days=30)
    raw = _function_answer(want)
    ex = perfect(truth)
    ex["coverage"] = S.coverage_from_function(raw, 30, as_of)
    r = run("CM-B", ex)["bars"]["5_coverage_is_exact"]
    assert r["pass"] is True and r["comparisons"] == 6 * len(want) + 6
    # every one of the six figures is compared PER DOOR: a difference in a figure the old function never returned per door is named
    for field in ("withRetrieval", "withInstruction", "viaLinks"):
        bad = copy.deepcopy(ex)
        bad["coverage"]["byDoor"][0][field] += 1
        b = run("CM-B", bad)["bars"]["5_coverage_is_exact"]
        assert b["pass"] is False and {d["field"] for d in b["differences"] if d["door"] != "(sum of doors against fleet)"} == {field}
    # a function that predates the per-door figures does not pass as exact, and is not read as zero either
    old = copy.deepcopy(raw)
    for d in old["byDoor"]:
        for f in ("withRetrieval", "withInstruction", "viaLinks", "withCut"):
            d.pop(f)
    ex_old = copy.deepcopy(ex)
    ex_old["coverage"] = S.coverage_from_function(old, 30, as_of)
    o = run("CM-B", ex_old)["bars"]["5_coverage_is_exact"]
    assert o["pass"] is False and all(d["got"] is None for d in o["differences"] if d["door"] != "(sum of doors against fleet)")
    # doors that do not sum to the fleet are named
    skew = copy.deepcopy(ex)
    skew["coverage"]["fleet"]["decisionSteps"] += 1
    k = run("CM-B", skew)["bars"]["5_coverage_is_exact"]
    assert k["pass"] is False and k["differences"][0]["door"] == "(sum of doors against fleet)"


def test_the_window_matters_to_the_expected_coverage():
    truth = plans("CM-B")
    wide = C.expected_coverage(truth, as_of=C.parse_iso("2026-09-29T00:00:00.000Z"), window_days=90)
    narrow = C.expected_coverage(truth, as_of=C.parse_iso("2026-09-29T00:00:00.000Z"), window_days=10)
    assert sum(t["decisionSteps"] for t in wide.values()) == 3 * 720
    assert 0 < sum(t["decisionSteps"] for t in narrow.values()) < 3 * 720


# ── the report and the one-read rule ─────────────────────────────────────────────────────────────
def write_export(tmp_path, name, ex):
    p = tmp_path / f"raised-{name}.json"
    p.write_text(json.dumps(ex))
    return str(p)


def sandbox(tmp_path):
    d = tmp_path / "data"
    shutil.copytree(DATA, d)
    return str(d)


def test_the_report_states_every_bar_and_puts_n_beside_every_percentage(tmp_path):
    d = sandbox(tmp_path)
    ex = write_export(tmp_path, "CM-A", perfect(plans("CM-A")))
    result, text = S.run_scoring("CM-A", ex, d)
    for token in ("ACCEPTANCE BARS (SPEC 12.5)", "false_alarms_under_5_percent", "recall_over_60_percent_failed_check", "named_as_cause",
                  "instruction_changes_detected_over_90_percent", "carry_a_quote", "settled_well", "coverage_is_exact", "no_model_call",
                  "PER FAULT KIND", "PER ROUTE", "95% interval"):
        assert token in text, token
    assert "NOT MEASURED" in text and "PASS" in text


def test_a_hold_out_needs_confirmation_is_read_once_and_a_reread_is_stamped_not_clean(tmp_path):
    d = sandbox(tmp_path)
    ex = write_export(tmp_path, "CM-B", perfect(plans("CM-B")))
    ledger = str(tmp_path / "reads.jsonl")
    with pytest.raises(S.HoldOutRefused):
        S.run_scoring("CM-B", ex, d, ledger_path=ledger)
    assert not os.path.exists(ledger)
    result, text = S.run_scoring("CM-B", ex, d, ledger_path=ledger, confirm_one_shot=True)
    assert result["clean_read"] is True and "first read of this set" in text
    assert len(open(ledger).read().strip().splitlines()) == 1
    with pytest.raises(S.HoldOutRefused):
        S.run_scoring("CM-B", ex, d, ledger_path=ledger, confirm_one_shot=True)
    result, text = S.run_scoring("CM-B", ex, d, ledger_path=ledger, confirm_one_shot=True, allow_reread=True)
    assert result["clean_read"] is False and "NOT CLEAN" in text
    assert len(open(ledger).read().strip().splitlines()) == 2


def test_the_development_set_is_not_one_shot(tmp_path):
    d = sandbox(tmp_path)
    ex = write_export(tmp_path, "CM-A", perfect(plans("CM-A")))
    for _ in range(2):
        result, _text = S.run_scoring("CM-A", ex, d)
        assert result["clean_read"] is True
    assert not os.path.exists(os.path.join(d, "holdout_reads.jsonl"))


def test_the_scorer_refuses_a_ground_truth_file_that_is_not_the_recorded_one(tmp_path):
    d = sandbox(tmp_path)
    p = os.path.join(d, T.truth_filename("CM-A"))
    open(p, "ab").write(b"\n")
    ex = write_export(tmp_path, "CM-A", perfect(plans("CM-A")))
    with pytest.raises(T.GroundTruthHashMismatch):
        S.run_scoring("CM-A", ex, d)


def test_the_cli_refuses_with_a_message_and_a_nonzero_exit(tmp_path, monkeypatch, capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("score_context_cli", os.path.join(_context_path.TREE, "scripts", "score_context.py"))
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    ex = write_export(tmp_path, "CM-B", perfect(plans("CM-B")))
    assert cli.main(["CM-B", "--export", ex, "--ledger", str(tmp_path / "l.jsonl")]) == 2
    assert "REFUSED" in capsys.readouterr().err
    out = tmp_path / "out.json"
    assert cli.main(["CM-A", "--export", write_export(tmp_path, "CM-A", perfect(plans("CM-A"))), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["set"] == "CM-A"
