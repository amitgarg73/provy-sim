"""Contract grading helpers, shared by levers, runner, and scoreboard.

A contract is a flat list of equal Criterion conditions. Grading is
deterministic: read the signal, compare with op/threshold, count "X of N met".
Good/bad values are derived from the contract so lever logic never hardcodes a
polarity.
"""
from __future__ import annotations

from typing import Any

from .types import Criterion


def signal_index(contract: list[Criterion]) -> dict[str, Criterion]:
    """Map signal -> the (first) criterion that reads it."""
    idx: dict[str, Criterion] = {}
    for c in contract:
        idx.setdefault(c.signal, c)
    return idx


def good_value(c: Criterion) -> Any:
    """The value of this signal on a passing run."""
    if c.op == "eq":
        return c.threshold
    if c.op in ("gt", "gte"):
        return c.threshold + 1
    if c.op in ("lt", "lte"):
        return c.threshold - 1
    return c.threshold


def bad_value(c: Criterion) -> Any:
    """A value of this signal that fails the condition."""
    if c.op == "eq":
        if isinstance(c.threshold, bool):
            return not c.threshold
        return None  # a distinct value fails an eq
    if c.op in ("gt", "gte"):
        return c.threshold - 1
    if c.op in ("lt", "lte"):
        return c.threshold + 1
    return None


def meets(c: Criterion, value: Any) -> bool:
    if value is None:
        return False
    if c.op == "eq":
        return value == c.threshold
    if c.op == "gt":
        return value > c.threshold
    if c.op == "gte":
        return value >= c.threshold
    if c.op == "lt":
        return value < c.threshold
    if c.op == "lte":
        return value <= c.threshold
    return False


def grade(contract: list[Criterion], estimated: dict, real: dict) -> dict:
    """Grade a run against the contract.

    For side 'outcome' read the real signals; for 'trace' read the estimated
    signals; for 'both' the condition must hold on the real side (reality wins)
    and we also record whether estimated diverged from real.
    Returns {met, total, per_condition:[{id,met,side,estimated_met,real_met}]}.
    """
    per = []
    met = 0
    for c in contract:
        real_val = real.get(c.signal)
        est_val = estimated.get(c.signal)
        real_met = meets(c, real_val)
        est_met = meets(c, est_val)
        if c.side == "trace":
            cond_met = est_met
        else:  # 'outcome' or 'both' — reality decides whether the condition is met
            cond_met = real_met
        if cond_met:
            met += 1
        per.append({
            "id": c.id, "signal": c.signal, "side": c.side, "met": cond_met,
            "estimated_met": est_met, "real_met": real_met,
            "diverged": (c.side == "both" and est_met != real_met),
        })
    return {"met": met, "total": len(contract), "per_condition": per}


def contract_json(contract: list[Criterion]) -> list[dict]:
    return [c.to_contract_json() for c in contract]
