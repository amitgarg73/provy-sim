"""The status demo workflow cannot be pointed anywhere but dev, cannot print a key, and cannot print a per-session planted answer.

A static read of .github/workflows/run-status-demo.yml. It runs in a PUBLIC repository, so a slip here is public at once.
"""
import os
import re

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, ".github", "workflows", "run-status-demo.yml")
LITERAL = "https://dev.provy.ai"
CONTRACT_INPUTS = {"action", "journey", "count", "seed", "mix", "outcome", "dry_run", "label"}
ADDRESS_WORDS = ("url", "host", "target", "environment", "base", "endpoint", "address", "domain", "server", "origin", "uri", "site")


@pytest.fixture(scope="module")
def text():
    return open(PATH).read()


@pytest.fixture(scope="module")
def spec(text):
    return yaml.safe_load(text)


@pytest.fixture(scope="module")
def steps(spec):
    return spec["jobs"]["status-demo"]["steps"]


def _triggers(spec):
    return spec.get("on", spec.get(True))


def _run(step):
    return str(step.get("run", ""))


def test_workflow_dispatch_is_the_only_trigger(spec):
    assert set(_triggers(spec)) == {"workflow_dispatch"}


def test_inputs_are_exactly_the_contract_and_strings(spec):
    inputs = _triggers(spec)["workflow_dispatch"]["inputs"]
    assert set(inputs) == CONTRACT_INPUTS
    for name, d in inputs.items():
        assert d.get("type") == "string", name
        for w in ADDRESS_WORDS:
            assert w not in name.lower(), name


def test_the_address_is_the_literal_and_only_proof_addresses_appear(text):
    assert LITERAL in text
    for m in re.finditer(r"https?://([A-Za-z0-9.@-]+)", text):
        assert m.group(1) in ("dev.provy.ai", "provy.ai", "dev.provy.ai@provy.ai"), m.group(0)


def test_secrets_are_the_three_dev_ones(text):
    names = set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text))
    assert names == {"STATUS_DEMO_KEY", "ARGUS_HARNESS_DEPLOY_KEY", "COS_RESULTS_PASSPHRASE"}
    assert not any(re.search(r"PROD|CERTIFY", n.upper()) for n in names)


def test_the_key_is_masked_before_export_and_only_loaded_on_a_real_run(steps):
    load = [s for s in steps if str(s.get("name", "")).startswith("Load the fleet key")][0]
    body = _run(load)
    assert body.index("::add-mask::") < body.index("GITHUB_ENV")
    assert "dry_run != 'true'" in str(load.get("if"))
    for s in steps:
        if s is not load:
            assert "STATUS_DEMO_KEY" not in str(s)


def test_inputs_reach_the_shell_only_through_env(steps):
    for s in steps:
        assert "${{ inputs." not in _run(s)
        assert "${{ secrets." not in _run(s)


def test_the_run_hides_per_session_answers_and_uses_the_literal(steps):
    run = [s for s in steps if s.get("name") == "Fire the status demo"][0]
    body = _run(run)
    assert "--base-url https://dev.provy.ai" in body
    assert "--hide-truth" in body
    assert "--send" in body and "status_demo.py" in body


def test_no_step_prints_the_environment_or_a_key(steps, text):
    assert "set -x" not in text and "xtrace" not in text and "ACTIONS_STEP_DEBUG" not in text
    for s in steps:
        for line in _run(s).splitlines():
            code = line.strip()
            assert not re.match(r"^(env|printenv|export\s+-p)\b", code), code
            assert not re.match(r"^echo\b.*(PROVY_KEY|PASSPHRASE|DEMO_KEY)", code), code


def test_validation_covers_every_cap(steps):
    body = _run(steps[0])
    assert steps[0]["name"] == "Check the inputs"
    for needle in ("J[1-7]", "B[12]", "-le 100", "-le 50", "{1,40}", "now|later", "true|false"):
        assert needle in body, needle


def test_guard_pin_and_fetch_come_in_order_and_fail_closed(steps):
    names = [s.get("name", "") for s in steps]
    guard = next(i for i, n in enumerate(names) if n.startswith("Refuse any address but dev"))
    pin = next(i for i, s in enumerate(steps) if s.get("id") == "pin")
    fetch = next(i for i, n in enumerate(names) if n.startswith("Fetch the harness"))
    run = next(i for i, n in enumerate(names) if n == "Fire the status demo")
    assert 0 < guard < pin < fetch < run
    assert "[0-9a-f]{40}" in _run(steps[pin])
    assert steps[fetch]["with"]["repository"] == "amitgarg73/argus"
    assert steps[fetch]["with"]["sparse-checkout"].strip() == "reference-harness"
    assert "cos/guard.py https://dev.provy.ai" in _run(steps[guard]) and "Refused" in _run(steps[guard])


def test_the_pin_is_a_real_sha_or_a_placeholder_that_fails_closed():
    v = open(os.path.join(ROOT, "status-demo", "HARNESS_PIN")).read().strip()
    assert re.fullmatch(r"[0-9a-f]{40}", v) or "REPLACE" in v


def test_runtime_environment_concurrency_and_results(spec, steps, text):
    job = spec["jobs"]["status-demo"]
    assert job["environment"] == "cos-dev" and job["timeout-minutes"] == 60
    assert spec["concurrency"] == {"group": "status-demo", "cancel-in-progress": False}
    assert spec["permissions"] == {"contents": "read"}
    assert "openssl enc -aes-256-cbc -pbkdf2" in text
    up = [s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact")][0]
    assert str(up["with"]["path"]).endswith(".enc") and "always()" in str(up["if"])
    assert steps[-1]["name"] == "Clean up" and steps[-1]["if"] == "always()"


def test_no_other_workflow_or_vendor_is_referenced(text):
    assert "servicely" not in text.lower() and "run-access-send" not in text and "COS_FLEET_KEYS_JSON" not in text
