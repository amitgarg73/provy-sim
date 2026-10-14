"""Offline tests for the Servicely instance scripts and their installer. No network."""
import os
import shutil
import subprocess

import pytest

from scripts import install_servicely_lifecycle as inst

SERVICELY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "servicely")


class FakeClient:
    def __init__(self, tables=None):
        self.tables = tables or {}

    def query(self, table, params=None, page_size=100):
        return self.tables.get(table, [])


def test_fresh_instance_gets_two_triggers_one_job_and_the_properties():
    changes = inst.plan(FakeClient())
    kinds = [(a, t) for a, t, _, _ in changes]
    assert kinds.count(("create", "Trigger")) == 2
    assert kinds.count(("create", "Job")) == 1
    assert kinds.count(("create", "ApplicationProperty")) == len(inst.PROPERTIES)


def test_second_run_is_a_no_op_when_the_instance_holds_the_current_files():
    first = inst.plan(FakeClient())
    tables = {"Trigger": [], "Job": [], "ApplicationProperty": []}
    for i, (a, t, label, body) in enumerate(first):
        tables[t].append(dict(body, id=str(i)))
    assert inst.plan(FakeClient(tables)) == []


def test_a_changed_script_is_an_update_not_a_duplicate():
    first = inst.plan(FakeClient())
    tables = {"Trigger": [], "Job": [], "ApplicationProperty": []}
    for i, (a, t, label, body) in enumerate(first):
        row = dict(body, id=str(i))
        if t == "Trigger" and label.endswith("outcome push"):
            row["Script"] = "// stale"
        tables[t].append(row)
    again = inst.plan(FakeClient(tables))
    assert [(a, t, l) for a, t, l, _ in again] == [("update", "Trigger", "Provy demo - outcome push")]


def test_an_existing_property_value_is_never_overwritten():
    tables = {"ApplicationProperty": [{"Key": "provy.push.mode", "Value": "live", "id": "p"}]}
    keys = [l for a, t, l, _ in inst.plan(FakeClient(tables)) if t == "ApplicationProperty"]
    assert "provy.push.mode" not in keys


def test_no_secret_is_in_the_installer_or_the_scripts():
    keys = {k for k, *_ in inst.PROPERTIES}
    assert "provy.ingest.key" not in keys
    for name in ("resolve_trigger.js", "outcome_push_trigger.js", "verification_sweep_job.js"):
        src = open(os.path.join(SERVICELY, name), encoding="utf-8").read()
        assert "provy_" not in src.replace("provy.", "")  # no ingest key literal


def test_the_scripts_only_act_on_marked_tickets():
    for name in ("resolve_trigger.js", "outcome_push_trigger.js"):
        src = open(os.path.join(SERVICELY, name), encoding="utf-8").read()
        assert "indexOf(marker) === 0" in src
    sweep = open(os.path.join(SERVICELY, "verification_sweep_job.js"), encoding="utf-8").read()
    assert "STARTS_WITH('ShortDescription', marker)" in sweep


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("name", ["resolve_trigger.js", "outcome_push_trigger.js", "verification_sweep_job.js"])
def test_scripts_parse(name):
    assert subprocess.run(["node", "--check", os.path.join(SERVICELY, name)], capture_output=True).returncode == 0
