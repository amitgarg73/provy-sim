"""The Chief of Staff workflow cannot be pointed anywhere but dev, and cannot print a key.

A static read of .github/workflows/run-cos-demo.yml (the plan's section 0). The workflow runs in a
PUBLIC repository, so a slip here is public at once.
"""
import os
import re

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, ".github", "workflows", "run-cos-demo.yml")
LITERAL = "https://dev.provy.ai"

CONTRACT_INPUTS = {"scenario", "runs", "lever_set", "levers_json", "mode", "model", "budget_usd", "seed",
                   "dry_run", "outcome_path", "label", "no_cert_running"}
ADDRESS_WORDS = ("url", "host", "target", "environment", "env", "base", "endpoint", "address", "domain", "server", "origin", "uri", "site")
FLEETS = ["cos", "status", "meetings", "cross", "sched", "wms", "kestrel", "pledge", "candidate", "twin", "sender"]


@pytest.fixture(scope="module")
def text():
    return open(PATH).read()


@pytest.fixture(scope="module")
def spec(text):
    return yaml.safe_load(text)


@pytest.fixture(scope="module")
def steps(spec):
    return spec["jobs"]["cos"]["steps"]


def _triggers(spec):
    return spec.get("on", spec.get(True))


def _run_text(step):
    return str(step.get("run", ""))


def test_workflow_dispatch_is_the_only_trigger(spec):
    assert set(_triggers(spec)) == {"workflow_dispatch"}


def test_inputs_are_exactly_the_contract(spec):
    assert set(_triggers(spec)["workflow_dispatch"]["inputs"]) == CONTRACT_INPUTS


def test_every_input_is_a_string(spec):
    for name, d in _triggers(spec)["workflow_dispatch"]["inputs"].items():
        assert d.get("type") == "string", name


def test_no_input_is_named_like_an_address(spec):
    for name in _triggers(spec)["workflow_dispatch"]["inputs"]:
        for w in ADDRESS_WORDS:
            assert w not in name.lower(), f"input {name} looks like an address"


def test_the_base_url_is_the_literal(text):
    assert LITERAL in text
    # The only other addresses allowed are the production hosts fed to the guard on purpose, to prove it refuses.
    self_proof = {"provy.ai", "dev.provy.ai@provy.ai"}
    for m in re.finditer(r"https?://([A-Za-z0-9.@-]+)", text):
        assert m.group(1) == "dev.provy.ai" or m.group(1) in self_proof, f"unexpected address in the workflow: {m.group(0)}"
    assert "PROVY_URL" not in text and "provy_url" not in text


def test_no_secret_name_is_production_or_certification(text):
    names = set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text))
    assert names, "the workflow should use secrets"
    for n in names:
        assert not re.search(r"PROD|CERTIFY", n.upper()), n
    assert names <= {"COS_FLEET_KEYS_JSON", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "ARGUS_HARNESS_READ", "COS_RESULTS_PASSPHRASE"}


def test_no_servicely_anything(text):
    assert "servicely" not in text.lower()


def test_masks_come_before_the_export(steps):
    load = [s for s in steps if str(s.get("name", "")).startswith("Load the fleet keys")]
    assert len(load) == 1
    body = _run_text(load[0])
    assert "::add-mask::" in body and "GITHUB_ENV" in body
    assert body.index("::add-mask::") < body.index("GITHUB_ENV")
    # every key is masked in a loop over the whole fleet list, and exported under PROVY_KEY_<FLEET>
    assert 'for f in FLEETS:\n    print("::add-mask::"' in body.replace("          ", "")
    assert "PROVY_KEY_" in body
    for f in FLEETS:
        assert f'"{f}"' in body


def test_key_loading_is_skipped_on_a_dry_run(steps):
    load = [s for s in steps if str(s.get("name", "")).startswith("Load the fleet keys")][0]
    assert "dry_run != 'true'" in str(load.get("if"))
    for s in steps:
        if s is not load:
            assert "COS_FLEET_KEYS_JSON" not in str(s)


def test_no_step_prints_the_environment_or_traces(steps, text):
    assert "set -x" not in text and "set -o xtrace" not in text and "xtrace" not in text
    for s in steps:
        for line in _run_text(s).splitlines():
            code = line.strip()
            assert not re.match(r"^(env|printenv|export\s+-p|declare\s+-x)\b", code), code
            assert not re.search(r"\b(env|printenv)\s*($|\||;|&)", code), code
            assert not re.match(r"^echo\b.*(PROVY_KEY|KEYS_JSON|PASSPHRASE|API_KEY)", code), code
    assert "ACTIONS_STEP_DEBUG" not in text


def test_first_step_validates_and_second_group_runs_the_guard(steps):
    assert steps[0]["name"] == "Check the inputs"
    names = [s.get("name", "") for s in steps]
    guard = next(i for i, n in enumerate(names) if n.startswith("Refuse any address but dev"))
    load = next(i for i, n in enumerate(names) if n.startswith("Load the fleet keys"))
    run = next(i for i, n in enumerate(names) if n.startswith("Run the Chief of Staff demo"))
    assert 0 < guard < load < run
    body = _run_text(steps[guard])
    assert "cos/guard.py https://dev.provy.ai" in body
    assert "Refused" in body and "https://provy.ai" in body


def test_input_validation_covers_every_cap(steps):
    body = _run_text(steps[0])
    for needle in ("-gt 30", "1500", "2.00", "0.25", "no_cert_running=true", "mock|live", "paid|free", "push", "{1,40}"):
        assert needle in body, needle


def test_inputs_reach_the_shell_only_through_env(steps):
    # An input pasted into a run: script is a shell-injection door. Inputs travel in env: and are quoted.
    for s in steps:
        assert "${{ inputs." not in _run_text(s)
        assert "${{ secrets." not in _run_text(s)


def test_the_run_step_uses_the_literal_and_send_or_dry_run(steps):
    run = [s for s in steps if str(s.get("name", "")).startswith("Run the Chief of Staff demo")][0]
    body = _run_text(run)
    assert "--base-url https://dev.provy.ai" in body
    assert "--dry-run" in body and "--send" in body and "harness.py" in body and "cos-run" in body
    assert "--outcome-path push" in body


def test_harness_checkout_is_sparse_pinned_and_fails_closed(steps):
    pin = [s for s in steps if s.get("id") == "pin"][0]
    assert "[0-9a-f]{40}" in _run_text(pin)
    fetch = [s for s in steps if str(s.get("name", "")).startswith("Fetch the harness")][0]
    w = fetch["with"]
    assert w["repository"] == "amitgarg73/argus"
    assert w["ref"] == "${{ steps.pin.outputs.sha }}"
    assert w["sparse-checkout"].strip() == "reference-harness"
    assert w["token"] == "${{ secrets.ARGUS_HARNESS_READ }}"
    assert steps.index(pin) < steps.index(fetch)


def test_the_pin_file_is_a_real_sha_or_a_placeholder_that_fails_closed():
    v = open(os.path.join(ROOT, "cos", "HARNESS_PIN")).read().strip()
    assert re.fullmatch(r"[0-9a-f]{40}", v) or "REPLACE" in v


def test_runtimes_timeout_environment_concurrency_and_run_name(spec, text):
    job = spec["jobs"]["cos"]
    assert job["timeout-minutes"] == 330
    assert job["environment"] == "cos-dev"
    assert spec["concurrency"]["group"] == "cos-demo"
    assert spec["concurrency"]["cancel-in-progress"] is False
    assert "python-version: '3.11'" in text and "node-version: '20'" in text
    rn = spec["run-name"]
    for part in ("cos | ", "inputs.label", "scenario=", "runs=", "levers=", "mode=", "budget=$"):
        assert part in rn
    assert spec["permissions"] == {"contents": "read"}


def test_results_are_encrypted_before_upload_and_cleanup_always_runs(steps, text):
    assert "openssl enc -aes-256-cbc -pbkdf2" in text
    up = [s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact")][0]
    assert str(up["with"]["path"]).endswith(".enc")
    assert "always()" in str(up["if"])
    last = steps[-1]
    assert last["name"] == "Clean up" and last["if"] == "always()"
    enc = [s for s in steps if s.get("name") == "Encrypt the results"][0]
    assert steps.index(enc) < steps.index(up)


def test_existing_workflows_are_not_referenced(text):
    assert "run-access-send" not in text and "_run.yml" not in text
