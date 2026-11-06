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
