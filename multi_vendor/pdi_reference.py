"""Python twin of pdi/src/ProvyIamPlan.js: the allow-list for a model's plan, the task's own plan, and the deterministic verdict.

WHY A TWIN: the PDI cannot run a server script from here, so the logic that decides what may be applied and whether it took effect is written twice, and one set of vectors
(tests/pdi_vectors.json) is run against BOTH the Python and the real JavaScript file (tests/js/run_plan_vectors.js), and against the deployed copy through the instance's
plan-check door (scripts/verify_pdi_agents.py). If the three ever disagree the vectors say where.
⛔ Keep this file and ProvyIamPlan.js identical in behaviour. A change to one is a change to the other and to the vectors.
"""
from __future__ import annotations

import json
from typing import Any, Optional

COMMANDS = ["provy-iam-disable-account", "provy-iam-remove-groups", "provy-iam-revoke", "provy-iam-apply-change", "provy-iam-grant"]
SYSTEMS = ["directory", "sso", "vpn", "saas_token"]
GRANT_SYSTEMS = ["sso", "vpn", "saas_token"]
GRANT_LEVELS = ["read", "write", "admin"]
PERSON_PREFIX = "provy.iam."
MAX_STEPS = 10
MAX_EXPIRES_MINUTES = 1440


def task_systems(params: Optional[dict]) -> list:
    p = params or {}
    raw = p.get("systems") or ([p["system"]] if p.get("system") else [])
    out: list = []
    for s in raw:
        s = str(s)
        if s in SYSTEMS and s not in out:
            out.append(s)
    return out


def task_plan(command: str, params: Optional[dict]) -> dict:
    p = params or {}
    step: dict = {"command": str(command), "params": {"person": p.get("person")}}
    if command == "provy-iam-grant":
        step["params"]["system"] = p.get("system") or "saas_token"
        step["params"]["level"] = p.get("level") or "read"
        if p.get("expires_minutes"):
            step["params"]["expires_minutes"] = p["expires_minutes"]
    elif command in ("provy-iam-disable-account", "provy-iam-remove-groups"):
        pass
    else:
        sys = task_systems(p)
        step["params"]["systems"] = sys if sys else list(SYSTEMS)
    return {"steps": [step]}


def _is_int_in_range(v: Any, lo: int, hi: int) -> bool:
    try:
        n = int(str(v).split(".")[0]) if not isinstance(v, bool) else None
    except (TypeError, ValueError):
        return False
    return n is not None and lo <= n <= hi


def validate(plan: Any, task: dict) -> dict:
    if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list):
        return {"ok": False, "reason": "the plan has no steps list"}
    steps = plan["steps"]
    if len(steps) < 1:
        return {"ok": False, "reason": "the plan has no steps"}
    if len(steps) > MAX_STEPS:
        return {"ok": False, "reason": f"the plan has more than {MAX_STEPS} steps"}
    tparams = task.get("params") or {}
    person = str(tparams.get("person") or "")
    if not person.startswith(PERSON_PREFIX):
        return {"ok": False, "reason": f"the task person is not a {PERSON_PREFIX}* person"}
    named = task_systems(tparams)
    allowed = named if named else SYSTEMS
    for i, st in enumerate(steps, 1):
        if not isinstance(st, dict):
            return {"ok": False, "reason": f"step {i} is not an object"}
        if st.get("command") not in COMMANDS:
            return {"ok": False, "reason": f"step {i} uses a command that is not allowed"}
        p = st.get("params") or {}
        if str(p.get("person") or "") != person:
            return {"ok": False, "reason": f"step {i} names a different person"}
        cmd = st["command"]
        if cmd == "provy-iam-grant":
            if p.get("system") not in GRANT_SYSTEMS:
                return {"ok": False, "reason": f"step {i} grants on a system that is not allowed"}
            if p.get("system") not in allowed:
                return {"ok": False, "reason": f"step {i} grants on a system the task did not name"}
            if p.get("level") not in GRANT_LEVELS:
                return {"ok": False, "reason": f"step {i} grants a level that is not allowed"}
            if "expires_minutes" in p and not _is_int_in_range(p["expires_minutes"], 1, MAX_EXPIRES_MINUTES):
                return {"ok": False, "reason": f"step {i} has an expiry outside 1 to {MAX_EXPIRES_MINUTES} minutes"}
        elif cmd in ("provy-iam-revoke", "provy-iam-apply-change"):
            sys = p.get("systems") or ([p["system"]] if p.get("system") else [])
            if not sys:
                return {"ok": False, "reason": f"step {i} names no systems"}
            for s in sys:
                if s not in SYSTEMS:
                    return {"ok": False, "reason": f"step {i} names an unknown system"}
                if s not in allowed:
                    return {"ok": False, "reason": f"step {i} names a system the task did not name"}
        else:
            if "directory" not in allowed:
                return {"ok": False, "reason": f"step {i} touches the directory, which the task did not name"}
    return {"ok": True}


def parse_plan(text: Optional[str]) -> Optional[dict]:
    if not text:
        return None
    s = str(text)
    start = s.find("{")
    while start >= 0:
        depth = 0
        for i in range(start, len(s)):
            c = s[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start:i + 1])
                    except ValueError:
                        break
        start = s.find("{", start + 1)
    return None


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def verdict(command: str, params: Optional[dict], reading: Optional[dict]) -> dict:
    r = reading or {}
    p = params or {}
    granting = command == "provy-iam-grant"
    names = [str(p.get("system") or "saas_token")] if granting else task_systems(p)
    if command in ("provy-iam-disable-account", "provy-iam-remove-groups"):
        names = ["directory"]
    elif not names:
        names = list(SYSTEMS)
    systems: dict = {}
    for n in names:
        if n == "directory":
            known = r.get("account_active") is True or r.get("account_active") is False
            left = (1 if r.get("account_active") is True else 0) + _num(r.get("groups_held")) + _num(r.get("roles_held"))
            held = known and left == 0
            if not known:
                detail = "the directory state is unknown"
            elif left == 0:
                detail = "the account is off and no group or role is held"
            else:
                detail = f"the account is {'still active' if r.get('account_active') else 'off'}, {int(_num(r.get('groups_held')))} group(s) and {int(_num(r.get('roles_held')))} role(s) held"
        else:
            est = r.get("estate") or {}
            if n not in est or est[n] is None:
                held, detail = False, f"the state of {n} is unknown"
            else:
                count = _num(est[n])
                if granting:
                    held = count > 0
                    detail = f"{n} now holds the access" if held else f"{n} does not hold the access that was granted"
                else:
                    held = count == 0
                    detail = f"{n} holds no access" if held else f"{n} still holds {int(count)} access item(s)"
        systems[n] = {"held": held, "detail": detail}
    bad = [n for n in names if not systems[n]["held"]]
    return {"held": not bad, "systems": systems, "checked": names,
            "summary": "every system checked is as asked" if not bad else "not as asked in: " + ", ".join(bad)}


def outcome_body(command: str, verdict: Optional[dict], correlation: Optional[str], business_date: str, occurred_at: str) -> Optional[dict]:
    """The outcome Provy's contract is settled with, from the verdict only. None when it does not apply (no work item, a grant, or not every system checked)."""
    if not correlation or command == "provy-iam-grant" or not verdict or not verdict.get("checked") or not verdict.get("systems"):
        return None
    if len(verdict["checked"]) != len(SYSTEMS):
        return None
    signals: dict = {"offboarded_all": bool(verdict["held"]), "systems_checked": len(verdict["checked"])}
    ended, bad = 0, []
    for n in verdict["checked"]:
        ok = bool(verdict["systems"].get(n, {}).get("held"))
        signals[f"{n}_ended"] = ok
        if ok:
            ended += 1
        else:
            bad.append(n)
    signals["systems_ended"] = ended
    if bad:
        signals["reason_code"] = "access_left_in_" + "_and_".join(bad)
    return {"entity_id": str(correlation), "business_date": business_date, "label": "success" if verdict["held"] else "fail",
            "source": "confirmed", "occurred_at": occurred_at, "signals": signals}


def _plan_line(plan: Optional[dict]) -> str:
    out = []
    for st in (plan or {}).get("steps", []):
        sys = (st.get("params") or {}).get("systems") or []
        out.append(st["command"] + (f" [{', '.join(sys)}]" if sys else ""))
    return "; ".join(out)


def decision_of(chosen: Optional[dict], source: str, rejected: Optional[str]) -> dict:
    """The decision the applier made, as Provy's decision object. The refusal reason is the allow-list's own words."""
    d: dict = {"chosen": _plan_line(chosen), "alternatives": []}
    if source == "task" and rejected:
        d["alternatives"].append("the model's plan")
        d["rationale"] = f"the model's plan was refused ({str(rejected)[:160]}), so the task's own plan was used"
    elif source == "stub":
        d["rationale"] = "offline stub: the task's own plan"
    else:
        d["rationale"] = "the model's plan passed the allow-list"
    return d


def handoff_in(params: Optional[dict]) -> dict:
    """What this system received at the handoff: the person, and the systems when the task named any."""
    p = params or {}
    fields: dict = {}
    if p.get("person"):
        fields["person"] = str(p["person"])
    sys = task_systems(p)
    if sys:
        fields["systems"] = sys
    return {"fields": fields}
