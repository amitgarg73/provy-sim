"""Offline tests for the Servicely instance scripts and their installer. No network."""
import os
import shutil
import subprocess

import pytest

from scripts import install_servicely_lifecycle as inst
from engine.servicely import ServicelyError

SERVICELY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "servicely")


class FakeClient:
    def __init__(self, tables=None):
        self.tables = tables or {}

    def query(self, table, params=None, page_size=100):
        rows = self.tables.get(table, [])
        page = int((params or {}).get("page", 1))
        return rows[(page - 1) * page_size: page * page_size]


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


def test_duplicate_rows_stop_the_installer_instead_of_guessing():
    tables = {"ApplicationProperty": [{"Key": "provy.marker", "id": "a"}, {"Key": "provy.marker", "id": "b"}]}
    with pytest.raises(ServicelyError, match="remove the extras"):
        inst.plan(FakeClient(tables))


def test_wire_script_sets_the_key_from_the_environment_and_masks_its_output(monkeypatch, capsys):
    from scripts import wire_servicely_fleet as wire

    class C(FakeClient):
        def __init__(self):
            super().__init__({"ApplicationProperty": [{"Key": "provy.push.mode", "Value": "capture", "id": "m"}]})
            self.created, self.updated = [], []
            self.configured = True

        def require(self):
            pass

        def create(self, table, body):
            self.created.append(body)
            self.tables["ApplicationProperty"].append(dict(body, id=str(len(self.created))))

        def update(self, table, rid, body):
            self.updated.append((rid, body))
            for r in self.tables["ApplicationProperty"]:
                if r["id"] == rid:
                    r.update(body)

    c = C()
    monkeypatch.setattr(wire, "client_from_env", lambda: c)
    monkeypatch.setenv("PROVY_INGEST_KEY", "provy_" + "a" * 32)
    monkeypatch.setattr("sys.argv", ["wire", "--provy-url", "https://dev.provy.ai"])
    assert wire.main() == 0
    out = capsys.readouterr().out
    assert "a" * 32 not in out                       # the key is never printed in full
    assert any(b["Key"] == "provy.ingest.key" and b["ClientSideAccessible"] is False for b in c.created)
    assert ("m", {"Value": "live"}) in c.updated
    assert "live:" in out


def test_wire_script_refuses_a_production_url_and_a_missing_key(monkeypatch):
    from scripts import wire_servicely_fleet as wire
    monkeypatch.setattr(wire, "client_from_env", lambda: type("X", (), {"require": lambda s: None,
                                                                    "query": lambda s, *a, **k: []})())
    monkeypatch.delenv("PROVY_INGEST_KEY", raising=False)
    monkeypatch.setattr("sys.argv", ["wire", "--provy-url", "https://dev.provy.ai"])
    assert wire.main() == 2
    monkeypatch.setenv("PROVY_INGEST_KEY", "provy_x")
    monkeypatch.setattr("sys.argv", ["wire", "--provy-url", "https://provy.ai"])
    assert wire.main() == 2
