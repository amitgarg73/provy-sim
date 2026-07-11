"""Scoreboard scoring math over synthetic ground-truth records."""
from engine.scoreboard import aggregate_injected, build_report, format_report
from packs import get_pack


def _rec(entity, faults, real_signals, outcome, diverged=False):
    return {
        "workflow": "support", "entity_id": entity, "faults": faults,
        "real_signals": real_signals, "outcome_label": outcome, "diverged": diverged,
    }


def test_aggregate_lever_rates():
    contract = get_pack("support").contract()
    good = {"escalated": False, "policy_followed": True, "sla_met": True,
            "reopened_7d": False, "category_correct": True}
    bad = dict(good, reopened_7d=True, policy_followed=False)
    records = []
    # 10 records: 3 have silent_wrong (and diverge/fail), 7 clean
    for i in range(7):
        records.append(_rec(f"E{i}", [], good, "success"))
    for i in range(3):
        records.append(_rec(f"W{i}", [{"lever": "silent_wrong", "agent": "resolver"}],
                            bad, "fail", diverged=True))
    agg = aggregate_injected(records, contract)
    assert agg["runs"] == 10
    assert agg["levers"]["silent_wrong"]["count"] == 3
    assert abs(agg["levers"]["silent_wrong"]["rate"] - 0.3) < 1e-9
    assert agg["levers"]["silent_wrong"]["by_agent"] == {"resolver": 3}
    assert agg["diverged"]["count"] == 3
    assert agg["fails"]["count"] == 3


def test_injected_met_rate_math():
    contract = get_pack("support").contract()
    good = {"escalated": False, "policy_followed": True, "sla_met": True,
            "reopened_7d": False, "category_correct": True}
    bad = dict(good, reopened_7d=True, policy_followed=False)
    # 4 outcome/both conditions are measurable (c1,c2,c3,c4,c5 -> c1..c5 minus none;
    # c2/c5 both, c1/c3/c4 outcome => 5 measurable). 1 bad record fails 2 of them.
    records = [_rec("A", [], good, "success"), _rec("B", [], bad, "fail")]
    agg = aggregate_injected(records, contract)
    measurable = [c for c in contract if c.side in ("outcome", "both")]
    total_slots = len(measurable) * 2
    # bad fails policy_followed (c2) and reopened_7d (c4) => 2 failures
    expected_met = (total_slots - 2) / total_slots
    assert abs(agg["injected_met_rate"] - round(expected_met, 4)) < 1e-9


def test_build_report_pending_without_provy():
    contract = get_pack("support").contract()
    good = {"escalated": False, "policy_followed": True, "sla_met": True,
            "reopened_7d": False, "category_correct": True}
    records = [_rec("A", [{"lever": "silent_wrong", "agent": "resolver"}],
                    dict(good, reopened_7d=True), "fail", diverged=True)]
    report = build_report(records, contract)   # no Supabase creds -> detected side None
    assert report["detected"]["provy_available"] is False
    assert all(r["status"] == "pending" for r in report["rows"] if r["detected"] is None)
    text = format_report(report, "support")
    assert "Provy proof scoreboard" in text
    assert "silent_wrong" in text


def test_empty_ledger():
    contract = get_pack("support").contract()
    assert aggregate_injected([], contract) == {"runs": 0}
