"""What a correct reading looks like. Pure: a reading in, a verdict out.

The reading is what the estate says about a person AFTER the change: is the account active, how many groups are held, how much access is left.
"Held" means the access really ended. This is the independent truth the agent's own report cannot give, and the thing Provy's outcome pull reads.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Verdict:
    held: bool
    failures: list[str] = field(default_factory=list)


def grade(reading: dict, kind: str = "leaver") -> Verdict:
    """A leaver held when the account is off, no group is held and no access is left, in every system the estate checked, and no other person's access changed.
    A time-limited ("jit") case held when no grant is past its expiry and still active."""
    if kind == "jit":
        left = reading.get("expired_grants_active")
        if not isinstance(left, (int, float)) or isinstance(left, bool):
            return Verdict(False, ["the state of the time-limited grants is unknown"])
        return Verdict(left == 0, [] if left == 0 else [f"{int(left)} time-limited grant(s) are past their expiry and still active"])
    failures: list[str] = []
    if reading.get("account_active") is not False:
        failures.append("the account is still active")
    if float(reading.get("groups_held") or 0) > 0:
        failures.append(f"{int(float(reading['groups_held']))} group(s) still held")
    if float(reading.get("access_remaining") or 0) > 0:
        failures.append(f"{int(float(reading['access_remaining']))} access item(s) left across {int(float(reading.get('systems_checked') or 0))} systems")
    others = reading.get("others_changed")
    if isinstance(others, (int, float)) and not isinstance(others, bool) and others > 0:
        failures.append(f"{int(others)} change(s) landed on people the request did not name")
    return Verdict(held=not failures, failures=failures)
