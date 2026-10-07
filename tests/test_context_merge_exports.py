import importlib.util
import os

import pytest

import _context_path  # noqa: F401

spec = importlib.util.spec_from_file_location("merge_exports", os.path.join(_context_path.TREE, "scripts", "merge_exports.py"))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


def ex(sid, door="rest", n=3, window=30, as_of="2026-10-02T00:00:00Z"):
    return {"sessions": [{"id": sid, "ext": "e-" + sid}], "failed_checks": [{"name": "context_freshness", "session": sid}], "incidents": [],
            "coverage": {"windowDays": window, "asOf": as_of, "byDoor": [{"door": door, "decisionSteps": n, "withManifest": n - 1, "withAges": 1}]}}


def test_lists_are_joined_and_coverage_is_summed_per_door():
    m = M.merge([ex("a", "rest"), ex("b", "rest", 5), ex("c", "otlp")])
    assert len(m["sessions"]) == 3 and len(m["failed_checks"]) == 3
    by = {d["door"]: d for d in m["coverage"]["byDoor"]}
    assert by["rest"]["decisionSteps"] == 8 and by["rest"]["withManifest"] == 6 and by["otlp"]["decisionSteps"] == 3


def test_a_fleet_exported_twice_is_refused():
    with pytest.raises(ValueError):
        M.merge([ex("a"), ex("a")])


def test_coverage_read_at_different_windows_is_refused():
    with pytest.raises(ValueError):
        M.merge([ex("a"), ex("b", window=60)])
    with pytest.raises(ValueError):
        M.merge([ex("a"), ex("b", as_of="2026-10-03T00:00:00Z")])


def test_exports_without_coverage_merge_without_it():
    a, b = ex("a"), ex("b")
    a.pop("coverage"), b.pop("coverage")
    assert "coverage" not in M.merge([a, b])


def test_a_figure_a_door_never_carried_merges_as_unknown_not_zero_and_the_fleet_totals_are_summed():
    a, b = ex("a", "rest", 3), ex("b", "rest", 5)
    for e in (a, b):
        e["coverage"]["byDoor"][0].update({"withRetrieval": 2, "withInstruction": 1, "viaLinks": 0})
        e["coverage"]["fleet"] = {"decisionSteps": e["coverage"]["byDoor"][0]["decisionSteps"], "withRetrieval": 2}
    row = M.merge([a, b])["coverage"]["byDoor"][0]
    assert row["withRetrieval"] == 4 and row["withInstruction"] == 2 and row["viaLinks"] == 0
    assert M.merge([a, b])["coverage"]["fleet"] == {"decisionSteps": 8, "withRetrieval": 4}
    c = ex("c", "rest", 2)  # an export from a function that predates the per-door figures
    merged = M.merge([a, c])["coverage"]
    assert merged["byDoor"][0]["withRetrieval"] is None and "fleet" not in merged
