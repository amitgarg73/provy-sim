"""scripts/fleet_doctor.py gets every name from its environment (#1648), and checks the same things as before.

A synthetic environment and a fake database stand in for the real ones: nothing here touches a network, a
credential file or pre-prod. The secret values below are made-up sentinels; a test proves none of them is ever printed.
"""
from __future__ import annotations

import hashlib
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fleet_doctor", ROOT / "scripts" / "fleet_doctor.py")
fd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fd)

PREPROD_URL = "postgresql://postgres.fpuyabfxtrzwciehfetk:SENTINEL-DB-PASSWORD@pooler.example:5432/postgres"
PROD_URL = "postgresql://postgres.eckthcvacrkfjihluubt:SENTINEL-PROD-PASSWORD@pooler.example:5432/postgres"
SN_USER, SN_PASSWORD, SN_INSTANCE = "svc-sentinel-user", "SENTINEL-SN-PASSWORD", "https://sentinel.service-now.com"
SENTINELS = ["SENTINEL-DB-PASSWORD", "SENTINEL-PROD-PASSWORD", SN_PASSWORD, "console-key-sentinel", "sn-key-sentinel"]


def sha(v):
    return hashlib.sha256(v.encode()).hexdigest()


FULL_ENV = {"CERTIFY_DB_URL": PREPROD_URL, "SERVICENOW_INSTANCE": SN_INSTANCE,
            "SERVICENOW_USER": SN_USER, "SERVICENOW_PASSWORD": SN_PASSWORD}


class FakeCursor:
    def __init__(self, db):
        self.db, self.out = db, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, args=()):
        s = " ".join(sql.split())
        if "from sim_control_config" in s:
            self.out = self.db.fleets
        elif "from ag_ingest_keys" in s:
            self.out = self.db.keys
        elif "from ag_sessions" in s:
            self.out = [(self.db.sessions,)]
        elif "from ag_outcomes" in s:
            self.out = [(self.db.settled,)]
        else:
            raise AssertionError("unexpected query: " + s)

    def fetchall(self):
        return self.out


class FakeDb:
    def __init__(self, fleets, keys, sessions=84, settled=79):
        self.fleets, self.keys, self.sessions, self.settled = fleets, keys, sessions, settled
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


def itsm_fleet(console_key="console-key-sentinel", url="https://dev.provy.ai"):
    return [("itsm", "wf-1", "tenant-1", console_key, url, False, "ops@example.test", "ITSM Incident Resolution")]


def live_key(secret, label="ITSM key"):
    return (sha(secret), label, "2026-09-10")


def reader(key, url="https://dev.provy.ai/api/ingest/outcome"):
    calls = []

    def read(instance, user, password, name):
        calls.append((instance, user, password, name))
        return {"provy.ingest.key": key, "provy.ingest.url": url}[name]
    read.calls = calls
    return read


def run(db, env, pack=None, rd=None):
    return fd.check(db, pack, env, rd)


# ── the happy path: all four places agree ───────────────────────────────────────────────────────
def test_a_fleet_that_agrees_everywhere_passes(capsys):
    db = FakeDb(itsm_fleet(), [live_key("console-key-sentinel")])
    rd = reader("console-key-sentinel")
    assert run(db, FULL_ENV, rd=rd) == 0
    out = capsys.readouterr().out
    assert "✓ matches an active Provy key" in out and "OK: every fleet agrees" in out
    assert "84 session(s), 79 settled" in out
    # the instance and credentials the doctor logs in with are exactly the environment's
    assert rd.calls[0] == (SN_INSTANCE, SN_USER, SN_PASSWORD, "provy.ingest.key")


def test_no_secret_value_is_ever_printed(capsys):
    db = FakeDb(itsm_fleet(), [live_key("console-key-sentinel")])
    run(db, FULL_ENV, rd=reader("sn-key-sentinel"))
    out = capsys.readouterr()
    for s in SENTINELS:
        assert s not in out.out and s not in out.err
    assert sha("sn-key-sentinel")[:10] in out.out   # the short digest is the only trace


# ── the same problems are still found (behaviour is unchanged) ──────────────────────────────────
def test_a_servicenow_key_that_is_not_active_is_a_problem(capsys):
    db = FakeDb(itsm_fleet(), [live_key("console-key-sentinel")])
    assert run(db, FULL_ENV, rd=reader("sn-key-sentinel")) == 1
    assert "NOT AN ACTIVE PROVY KEY — outcomes will never settle" in capsys.readouterr().out


def test_a_console_key_that_is_not_active_is_a_problem(capsys):
    db = FakeDb(itsm_fleet(console_key="stale-console"), [live_key("console-key-sentinel")])
    assert run(db, FULL_ENV, rd=reader("console-key-sentinel")) == 1
    assert "every dispatched run will 401" in capsys.readouterr().out


def test_two_active_keys_warn_but_do_not_fail(capsys):
    db = FakeDb(itsm_fleet(), [live_key("console-key-sentinel"), live_key("another")])
    assert run(db, FULL_ENV, rd=reader("console-key-sentinel")) == 0
    assert "2 keys are valid at once" in capsys.readouterr().out


def test_a_fleet_pointed_at_anything_but_preprod_is_a_problem(capsys):
    db = FakeDb(itsm_fleet(url="https://www.provy.ai"), [live_key("console-key-sentinel")])
    assert run(db, FULL_ENV, rd=reader("console-key-sentinel")) == 1
    assert "NOT dev.provy.ai" in capsys.readouterr().out


def test_servicenow_refusing_the_login_is_a_problem(capsys):
    db = FakeDb(itsm_fleet(), [live_key("console-key-sentinel")])
    assert run(db, FULL_ENV, rd=reader("__HTTP_401__")) == 1
    assert "HTTP 401" in capsys.readouterr().out


def test_an_instance_url_without_a_scheme_gets_one():
    env = dict(FULL_ENV, SERVICENOW_INSTANCE="sentinel.service-now.com")
    assert fd.servicenow_credentials(env)[0] == "https://sentinel.service-now.com"


# ── missing names: the error names the NAME and the command, never a value ──────────────────────
def test_missing_servicenow_names_are_listed_with_the_command_to_run(capsys):
    env = {"CERTIFY_DB_URL": PREPROD_URL, "SERVICENOW_INSTANCE": SN_INSTANCE}
    db = FakeDb(itsm_fleet(), [live_key("console-key-sentinel")])
    rd = reader("console-key-sentinel")
    assert run(db, env, rd=rd) == 1
    out = capsys.readouterr().out
    assert "SERVICENOW_USER, SERVICENOW_PASSWORD not in the environment" in out
    assert "scripts/with-secrets CERTIFY_DB_URL SERVICENOW_INSTANCE SERVICENOW_USER SERVICENOW_PASSWORD --" in out
    assert rd.calls == [], "no login is attempted with half a credential"
    assert "landed  84 session(s)" in out, "the rest of the report still runs"
    assert SN_PASSWORD not in out


def test_a_pack_that_is_not_itsm_needs_no_servicenow_name(capsys):
    fleets = [("claims", "wf-2", "tenant-2", "k", "https://dev.provy.ai", True, "o@example.test", "Claims")]
    db = FakeDb(fleets, [live_key("k")])
    assert run(db, {"CERTIFY_DB_URL": PREPROD_URL}) == 0
    assert "servicenow" not in capsys.readouterr().out


def test_main_without_a_database_name_stops_with_the_name_and_command(capsys):
    assert fd.main([], environ={}) == 2
    err = capsys.readouterr().err
    assert "CERTIFY_DB_URL is not in the environment" in err
    assert 'cd "$HOME/Claude Projects/argus" && scripts/with-secrets CERTIFY_DB_URL SERVICENOW_INSTANCE' in err


def test_the_other_name_for_the_same_address_is_accepted():
    assert fd.database_url({"PROVY_DB_URL": PREPROD_URL}) == PREPROD_URL


def test_a_production_database_address_is_refused_without_echoing_it(capsys):
    assert fd.main([], environ={"CERTIFY_DB_URL": PROD_URL}) == 2
    err = capsys.readouterr().err
    assert "PRODUCTION" in err and "SENTINEL-PROD-PASSWORD" not in err


def test_an_address_that_names_neither_project_is_refused_too(capsys):
    assert fd.main([], environ={"CERTIFY_DB_URL": "postgresql://u:SENTINEL-X@host/db"}) == 2
    assert "SENTINEL-X" not in capsys.readouterr().err


def test_main_runs_the_check_and_closes_the_connection(monkeypatch, capsys):
    db = FakeDb(itsm_fleet(), [live_key("console-key-sentinel")])
    monkeypatch.setattr(fd, "_db", lambda url: db)
    monkeypatch.setattr(fd, "servicenow_property", reader("console-key-sentinel"))
    assert fd.main(["--pack", "itsm"], environ=FULL_ENV) == 0
    assert db.closed


def test_no_fleet_registered_is_still_exit_1(capsys):
    assert run(FakeDb([], []), FULL_ENV) == 1


def test_the_script_has_no_path_to_a_credential_file():
    src = (ROOT / "scripts" / "fleet_doctor.py").read_text()
    code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    import re
    body = code.split('"""', 2)[2]
    for banned in (r"(?<![.\w])open\(", "expanduser", "readlines"):
        assert not re.search(banned, body), banned
