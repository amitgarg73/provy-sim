"""Compare what the product says about each sim tenant with the tenant's ground truth.

Pure comparison functions: they take the expectation (ground_truth/sim_tenants/<T>/expect.json) and an observation (what the product's own
functions and the database returned) and give rows of (tenant, fleet, check, expected, observed, verdict). Nothing here talks to a network, so the
tests can hold the comparisons on fabricated observations, and scripts/sim_assert.py gathers the real ones.

⛔ A MISMATCH IS A FINDING, NOT A FAILURE OF THE TEST. A row is `pass` when observed equals expected, `FAIL` when it does not, and `info` when the
expectation says the product may legitimately differ (it names why). The summary counts all three and never turns a FAIL into an info.
⛔ ABSENT IS NOT A PASS. A check the product could not run (no observation) is `no-data`, which counts against the tenant.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

PASS, FAIL, INFO, NODATA = "pass", "FAIL", "info", "no-data"


class Rows:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, tenant: str, fleet: str, check: str, expected: Any, observed: Any, verdict: Optional[str] = None, note: str = "") -> None:
        if verdict is None:
            verdict = NODATA if observed is None else (PASS if expected == observed else FAIL)
        self.rows.append({"tenant": tenant, "fleet": fleet, "check": check, "expected": expected, "observed": observed, "verdict": verdict, "note": note})

    def summary(self) -> dict:
        out: dict[str, int] = {PASS: 0, FAIL: 0, INFO: 0, NODATA: 0}
        for r in self.rows:
            out[r["verdict"]] += 1
        return out

    def by_tenant(self) -> dict:
        t: dict[str, dict] = {}
        for r in self.rows:
            d = t.setdefault(r["tenant"], {PASS: 0, FAIL: 0, INFO: 0, NODATA: 0})
            d[r["verdict"]] += 1
        return t


# ── the comparisons ──────────────────────────────────────────────────────────────────────────────
def compare_readiness(rows: Rows, t: str, f: str, exp: dict, obs: Optional[dict]) -> None:
    e = exp["readiness"]
    if not obs or "error" in obs:
        rows.add(t, f, "readiness read", "readable", None)
        return
    rows.add(t, f, "readiness state", e["state"], obs["state"])
    rows.add(t, f, "decision steps in window", e["stepsCounted"], obs["window"]["stepsCounted"])
    rows.add(t, f, "steps given context (judged)", e["givenSteps"], obs["window"]["givenSteps"])
    rows.add(t, f, "steps not counted (no model, no record)", e["notGivenSteps"], obs["window"]["notGivenSteps"])
    rows.add(t, f, "signals sent on every step", e["signalsSent"], sum(1 for s in obs["signals"].values() if s["state"] == "sent"))
    for sig, want in e["signals"].items():
        rows.add(t, f, f"signal {sig}", want, obs["signals"][sig]["state"])


def compare_counts(rows: Rows, t: str, f: str, exp: dict, db: dict) -> None:
    rows.add(t, f, "sessions stored", exp["sessions"], db.get("sessions"))
    rows.add(t, f, "steps stored once (planned steps, plus the root span an OpenTelemetry run adds)", exp.get("stored_steps", exp["steps"]), db.get("steps"))
    rows.add(t, f, "distinct span ids (a resent step is one step)", db.get("steps"), db.get("spans"))
    rows.add(t, f, "steps carrying a context record", db.get("expected_ctx"), db.get("ctx"))
    if exp["door"] in db.get("door_tag", {}):
        rows.add(t, f, "ingest door recorded", db["door_tag"][exp["door"]], db.get("doors"))


def compare_outcomes(rows: Rows, t: str, f: str, exp: dict, obs: Optional[dict]) -> None:
    if not obs or "error" in obs:
        rows.add(t, f, "outcomes read", "readable", None)
        return
    c = obs["counts"]
    rows.add(t, f, "work items settled", exp["outcomes"]["work_items"], c["settled"])
    rows.add(t, f, "work items waiting", 0, c["waiting"])
    if "calibration" in exp:
        held = sum(b["held"] for b in exp["calibration"].values())
        claims = sum(b["claims"] for b in exp["calibration"].values())
        rows.add(t, f, "expected side is the agent's own claim (unpredicted 0)", 0, c["unpredicted"])
        rows.add(t, f, "claims that held (matched)", held, c["matched"])
        rows.add(t, f, "claims that did not hold (diverged)", claims - held, c["diverged"])


def compare_calibration(rows: Rows, t: str, f: str, exp: dict, obs: Optional[dict]) -> None:
    if "calibration" not in exp:
        return
    if obs is None:
        rows.add(t, f, "claim calibration read", "readable", None)
        return
    seen = {round(r["stated"], 1): r for r in obs.get("rows", [])}
    for k, want in exp["calibration"].items():
        got = seen.get(round(float(k), 1))
        rows.add(t, f, f"calibration at {k}: claims", want["claims"], got["claims"] if got else 0)
        rows.add(t, f, f"calibration at {k}: held", want["held"], got["held"] if got else 0)
        rows.add(t, f, f"calibration at {k}: too few to judge", want["too_few"], got["tooFew"] if got else None)
    rows.add(t, f, "claims with no stated confidence", 0, obs.get("unstated"))


def failing_sessions(evals: list[dict], names: Callable[[str], bool], ext_of: dict) -> dict:
    """Failing verdicts by session external id: {ext: [agent...]}. A passing row is not returned."""
    out: dict[str, list] = {}
    for e in evals:
        if names(e["eval_name"]) and e.get("passed") is False:
            ext = ext_of.get(e["session_id"])
            if ext:
                out.setdefault(ext, []).append(e.get("agent"))
    return out


def measured_sessions(evals: list[dict], names: Callable[[str], bool], ext_of: dict) -> set:
    return {ext_of[e["session_id"]] for e in evals if names(e["eval_name"]) and e["session_id"] in ext_of}


FRESH = lambda n: n.lower().startswith("context freshness") or n == "context_freshness"            # noqa: E731
SOURCES = lambda n: n.lower().startswith("context approved sources") or n == "source_not_approved"  # noqa: E731
EMPTY = lambda n: n == "retrieval_empty"                                                            # noqa: E731
INSTR = lambda n: n == "instructions_changed"                                                       # noqa: E731
ANY_CONTEXT = lambda n: FRESH(n) or SOURCES(n) or EMPTY(n) or INSTR(n)                              # noqa: E731


def compare_declared(rows: Rows, t: str, f: str, exp: dict, evals: list[dict], ext_of: dict) -> None:
    d = exp.get("declared")
    if not d:
        return
    fresh_fail = set(failing_sessions(evals, FRESH, ext_of))
    src_fail = set(failing_sessions(evals, SOURCES, ext_of))
    rows.add(t, f, "sessions with a stale source flagged", sorted(d["stale_sessions"]), sorted(fresh_fail & set(exp["session_ids"])))
    rows.add(t, f, "sessions with an unapproved source flagged", sorted(d["unapproved_sessions"]), sorted(src_fail & set(exp["session_ids"])))


def compare_phases(rows: Rows, t: str, f: str, exp: dict, evals: list[dict], ext_of: dict) -> None:
    ph = exp.get("phases")
    if not ph:
        return
    sp = ph["session_phase"]
    fresh_measured = measured_sessions(evals, FRESH, ext_of)
    empty_measured = measured_sessions(evals, EMPTY, ext_of)
    for n in range(1, ph["count"] + 1):
        ids = {s for s, p in sp.items() if p == n}
        want_fresh = ph["checks"][str(n)]["fresh"]
        rows.add(t, f, f"phase {n}: freshness rule wrote a verdict for its sessions", want_fresh, bool(fresh_measured & ids))
        if not ph["checks"][str(n)]["empty"]:
            rows.add(t, f, f"phase {n}: learned empty-search check wrote no verdict (switched off)", False, bool(empty_measured & ids))


def expected_empty_catches(truth: list[dict]) -> dict:
    """Which planted empty retrievals the learned check should flag: those on a step it can judge. A step is judged when at least 20 OTHER sessions
    recorded a retrieval for that agent, 95 percent or more of them non-empty, with a Wilson lower bound of 0.8 or more (docs/operations/context-default-checks-runbook.md)."""
    from engine.sim_scenarios import wilson_lower
    out = {}
    by_agent: dict[str, list] = {}
    for r in truth:
        for s in r["steps"]:
            if s["returned"] is not None and s["step_type"] in ("decision", "agent_message"):
                by_agent.setdefault(s["agent"], []).append((r["session_id"], s["returned"]))
    for r in truth:
        for fl in r["faults"]:
            if fl["fault"] != "empty":
                continue
            recs = [(sid, n) for sid, n in by_agent.get(fl["agent"], []) if sid != r["session_id"]]
            n = len(recs)
            non = sum(1 for _, k in recs if k > 0)
            judged = n >= 20 and non / n >= 0.95 and wilson_lower(non, n) >= 0.8
            out[r["session_id"]] = {"agent": fl["agent"], "judged": judged, "others": n, "non_empty": non}
    return out


def compare_faults(rows: Rows, t: str, f: str, exp: dict, truth: list[dict], evals: list[dict], ext_of: dict) -> None:
    if exp["scenario"] not in ("faults", "declared"):
        return
    emp = expected_empty_catches(truth)
    caught = set(failing_sessions(evals, EMPTY, ext_of))
    judged = {sid for sid, v in emp.items() if v["judged"]}
    if exp["scenario"] == "faults":
        rows.add(t, f, "planted empty retrievals the learned check can judge are flagged", sorted(judged), sorted(caught & set(emp)))
        rows.add(t, f, "no clean session is flagged for an empty search", [], sorted(caught - set(emp)))


def compare_instruction(rows: Rows, t: str, f: str, exp: dict, evals: list[dict], ext_of: dict) -> None:
    want = exp.get("instruction_findings")
    if want is None:
        return
    got = set()
    for e in evals:
        if INSTR(e["eval_name"]) and e.get("passed") is False and e["session_id"] in ext_of:
            got.add((e.get("agent"), ext_of[e["session_id"]]))
    exp_set = {(w["agent"], w["session_id"]) for w in want}
    rows.add(t, f, "instruction changes flagged (agent, session)", sorted(exp_set), sorted(got))
    for agent in sorted({w["agent"] for w in want}):
        rows.add(t, f, f"instruction story of {agent}: findings", sum(1 for w in want if w["agent"] == agent), sum(1 for a, _ in got if a == agent))
    stories = exp.get("instruction_stories", {})
    for agent, story in stories.items():
        if story["story"] == "constant":
            rows.add(t, f, f"{agent} never changed: nothing flagged", 0, sum(1 for a, _ in got if a == agent))


def compare_no_context(rows: Rows, t: str, f: str, exp: dict, evals: list[dict], ext_of: dict) -> None:
    """The customer who never sent a record: no context check may write a PASS. A check that has nothing to read writes no row."""
    if exp["scenario"] != "none":
        return
    ctx = [e for e in evals if ANY_CONTEXT(e["eval_name"]) and e["session_id"] in ext_of]
    rows.add(t, f, "context checks that wrote a verdict (all unknown, so none)", 0, len(ctx))
    rows.add(t, f, "context verdicts that PASSED (never a pass)", 0, sum(1 for e in ctx if e.get("passed") is True))


def compare_unmeasurable(rows: Rows, t: str, f: str, exp: dict, obs: Optional[dict]) -> None:
    """A condition no work item ever sends a signal for: the Outcomes answer must not call it met. It reads waiting or off, with nothing settled on it,
    next to conditions that are measured and on."""
    sig = exp.get("unmeasurable_condition")
    if not sig or not obs or "error" in obs:
        return
    conds = obs.get("conditions", [])
    extra = [c for c in conds if "satisfied" in (c.get("wording") or "")]
    measured = [c for c in conds if "satisfied" not in (c.get("wording") or "")]
    rows.add(t, f, "the unmeasurable condition is listed", 1, len(extra))
    if extra:
        rows.add(t, f, "the unmeasurable condition settled nothing", 0, extra[0]["settled"])
        rows.add(t, f, "the unmeasurable condition is not shown as on", True, extra[0]["status"] != "on")
    rows.add(t, f, "the measurable conditions are on", True, bool(measured) and all(c["status"] == "on" for c in measured))


def compare_declarations(rows: Rows, t: str, f: str, exp: dict, declared_keys: Optional[list]) -> None:
    """A declaration lands on the fleet it was sent for and no other (#1630): the stored keys are exactly the planned ones."""
    if declared_keys is None:
        rows.add(t, f, "declarations read", "readable", None)
        return
    rows.add(t, f, "declarations stored on this fleet (and only what was declared)", exp.get("declares", []), sorted(k for k in declared_keys if k in ("context_max_age", "approved_sources", "context_log_fields", "retrieval_expected", "steps_given_nothing", "sources_without_age") and k != "approved_sources"))


def compare_live_itsm(rows: Rows, t: str, f: str, exp: dict, steps: list[dict]) -> None:
    """The ITSM pack's own promises (argus#1649), from the stored steps. `steps` are the fleet's rows: agent, step_type, model, context, claim.

    A step that ran a model carries a record; a step that ran code only carries neither a model nor a record; every ticket states claims with a
    confidence; and the outcomes the product holds are the ones the instance's rule pushed, none posted by the simulation."""
    i = exp.get("itsm")
    if not i:
        return
    decisions = [s for s in steps if s.get("step_type") in ("agent_message", "decision")]
    ran = [s for s in decisions if s.get("model")]
    code = [s for s in decisions if not s.get("model")]
    rows.add(t, f, "model-run decision steps stored", i["model_steps"], len(ran))
    rows.add(t, f, "model-run decision steps carrying a context record", i["model_steps_with_record"], sum(1 for s in ran if s.get("context")))
    rows.add(t, f, "code-only agents are exactly those planned", i["code_only_agents"], sorted({s["agent"] for s in code}))
    rows.add(t, f, "code-only decision steps stored (no model)", i["code_only_steps"], len(code))
    rows.add(t, f, "code-only steps carry no record", 0, sum(1 for s in code if s.get("context")))
    claims = [s for s in steps if s.get("claim")]
    rows.add(t, f, "steps carrying a claim", i["claim_steps"], len(claims))
    conf = []
    for s in claims:
        for c in (s["claim"] if isinstance(s["claim"], list) else [s["claim"]]):
            conf.append(c.get("confidence"))
    rows.add(t, f, "claims stored", i["claims"], len(conf))
    rows.add(t, f, "claims with no stated confidence", 0, sum(1 for c in conf if not isinstance(c, (int, float))))
    rows.add(t, f, "the stated confidences are the ones sent", i["claim_confidences"], sorted({round(c, 4) for c in conf if isinstance(c, (int, float))}))
