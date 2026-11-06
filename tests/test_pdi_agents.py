"""The ServiceNow-side agents' pure logic: the allow-list for a model's plan, the task's own plan, and the deterministic verdict.
One set of vectors (tests/pdi_vectors.json) runs against the Python twin AND the real ProvyIamPlan.js in node. The deployed copy is checked by scripts/verify_pdi_agents.py."""
import json
import os
import shutil
import subprocess

import pytest

from multi_vendor import pdi_reference as ref

HERE = os.path.dirname(os.path.abspath(__file__))
VECTORS = json.load(open(os.path.join(HERE, "pdi_vectors.json")))


def _subset(expect, got):
    for k, v in expect.items():
        if k == "systems":
            for s, held in v.items():
                if s not in got["systems"] or got["systems"][s]["held"] != held:
                    return False
        elif got.get(k) != v:
            return False
    return True


def _run_python(v):
    if v["kind"] == "validate":
        return _subset(v["expect"], ref.validate(v["plan"], v["task"]))
    if v["kind"] == "taskPlan":
        return ref.task_plan(v["command"], v["params"]) == v["expect"]
    if v["kind"] == "verdict":
        return _subset(v["expect"], ref.verdict(v["command"], v["params"], v["reading"]))
    if v["kind"] == "parsePlan":
        return ref.parse_plan(v["text"]) == v["expect"]
    if v["kind"] == "call":
        import re as _re
        fn = getattr(ref, _re.sub(r"([A-Z])", lambda m: "_" + m.group(1).lower(), v["fn"]).lstrip("_"))
        return fn(*v["args"]) == v["expect"]
    if v["kind"] == "outcomeBody":
        return ref.outcome_body(v["command"], v["verdict"], v["correlation"], v["businessDate"], v["occurredAt"]) == v["expect"]
    raise AssertionError(v["kind"])


@pytest.mark.parametrize("v", VECTORS, ids=lambda v: f"{v['kind']}: {v['name']}")
def test_python_twin_passes_every_vector(v):
    assert _run_python(v)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_real_javascript_file_passes_the_same_vectors():
    out = subprocess.run(["node", os.path.join(HERE, "js", "run_plan_vectors.js")], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[:500]
    results = json.loads(out.stdout)
    assert len(results) == len(VECTORS)
    failed = [r["name"] for r in results if not r["ok"]]
    assert failed == [], failed


def test_the_vectors_cover_the_rules_that_matter():
    names = " | ".join(v["name"] for v in VECTORS)
    for must in ("a different person", "a command that is not allowed", "a system the task did not name", "more than ten steps",
                 "a person who is not a provy.iam person", "a clean leaver is held", "vpn kept its access", "an unknown directory state is not held"):
        assert must in names


def test_a_model_can_never_make_a_failed_leaver_look_held():
    """The verdict takes only the reading. There is no argument through which a model's wording can reach it."""
    import inspect
    assert list(inspect.signature(ref.verdict).parameters) == ["command", "params", "reading"]
    bad = {"account_active": True, "groups_held": 2, "roles_held": 0, "estate": {"sso": 1, "vpn": 1, "saas_token": 1}}
    assert ref.verdict("provy-iam-apply-change", {"person": "provy.iam.x"}, bad)["held"] is False


def test_a_refused_plan_falls_back_to_the_tasks_own_plan_which_is_always_valid():
    """The fallback must never itself be refused, for any task the plain worker would have accepted."""
    for cmd, params in [("provy-iam-apply-change", {"person": "provy.iam.a"}), ("provy-iam-revoke", {"person": "provy.iam.a", "systems": ["vpn"]}),
                        ("provy-iam-disable-account", {"person": "provy.iam.a"}), ("provy-iam-remove-groups", {"person": "provy.iam.a"}),
                        ("provy-iam-grant", {"person": "provy.iam.a", "system": "vpn", "level": "write", "expires_minutes": 30})]:
        plan = ref.task_plan(cmd, params)
        assert ref.validate(plan, {"command": cmd, "params": params}) == {"ok": True}, (cmd, ref.validate(plan, {"command": cmd, "params": params}))


def test_the_outcome_is_sent_after_the_session_closes_and_only_from_the_built_body():
    """The wiring, read from the deployed file: the outcome goes out after trace.close, built by ProvyIamPlan.outcomeBody from the verifier's verdict, never from model wording."""
    src = open(os.path.join(HERE, "..", "multi_vendor", "pdi", "src", "ProvyIamAgents.js")).read()
    assert src.index("trace.close(verification.claim_summary") < src.index("this.plan.outcomeBody(") < src.index("trace.outcome(outcome)")
    block = src[src.index("this.plan.outcomeBody("):src.index("trace.outcome(outcome)")]
    assert "finding" not in block and "wording" not in block


def test_the_verify_step_never_repeats_a_top_level_name_inside_its_systems():
    """Provy reads a step's fields by leaf name at any depth and the last wins, so a nested `held` made a failed run read as estimated held. The trace carries `ended` per system."""
    import re
    src = open(os.path.join(HERE, "..", "multi_vendor", "pdi", "src", "ProvyIamAgents.js")).read()
    step = next(l for l in src.splitlines() if "phase: 'verify'" in l)
    assert "systems: this._forTrace(claim.systems)" in step and "systems: verdict.systems" not in step and "systems: claim.systems" not in step
    helper = src[src.index("_forTrace: function"):src.index("_verify: function")]
    assert "ended:" in helper and not re.search(r"\bheld\s*:", helper.split("function (systems)")[1])
    assert "estate_" not in src


def test_the_plan_step_is_a_decision_step_and_the_session_opens_with_what_was_received():
    src = open(os.path.join(HERE, "..", "multi_vendor", "pdi", "src", "ProvyIamAgents.js")).read()
    plan_step = next(l for l in src.splitlines() if "phase: 'plan'" in l)
    assert "'decision'" in plan_step and "decision: this.plan.decisionOf(" in plan_step
    assert "this.plan.handoffIn(p)" in src
    trace = open(os.path.join(HERE, "..", "multi_vendor", "pdi", "src", "ProvyIamTrace.js")).read()
    assert "body.handoff_in = handoffIn" in trace


# ---- the failure levers (6 Nov 2026): semantics that do not depend on the Python twin being right -------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["other_person", "extra_system", "bad_command", "too_many_steps"])
def test_every_policy_breaking_plan_is_refused_by_the_allow_list(kind):
    task = {"command": "provy-iam-revoke", "params": {"person": "provy.iam.b001", "systems": ["vpn"]}}
    plan = ref.violating_plan(kind, task)
    assert ref.validate(plan, task)["ok"] is False, kind


def test_a_task_with_nothing_to_overreach_on_never_violates_by_accident():
    task = {"command": "provy-iam-apply-change", "params": {"person": "provy.iam.b001"}}
    assert ref.validate(ref.violating_plan("extra_system", task), task) == {"ok": True}
    assert ref.validate(ref.violating_plan("not-a-kind", task), task) == {"ok": True}


def test_an_optimistic_claim_never_changes_the_verdict_the_outcome_is_built_from():
    reading = {"account_active": True, "groups_held": 0, "roles_held": 0, "estate": {"sso": 0, "vpn": 1, "saas_token": 0}}
    v = ref.verdict("provy-iam-apply-change", {"person": "provy.iam.b001"}, reading)
    assert v["held"] is False and ref.claim_of(v, True)["held"] is True and v["held"] is False
    body = ref.outcome_body("provy-iam-apply-change", v, "REQ1", "2026-11-06", "t", {"others_changed": 0})
    assert body["label"] == "fail" and body["signals"]["offboarded_all"] is False


def test_the_agents_use_the_computed_verdict_for_the_outcome_and_the_claim_for_the_session_only():
    src = open(os.path.join(HERE, "..", "multi_vendor", "pdi", "src", "ProvyIamAgents.js")).read()
    block = src[src.index("this.plan.outcomeBody("):src.index("trace.outcome(outcome)")]
    assert "claim" not in block.lower()
