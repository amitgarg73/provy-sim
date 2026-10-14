"""Offline tests for engine/servicely.py. No network: _call is replaced."""
import pytest

from engine import servicely as sv


def _client(**kw):
    c = sv.ServicelyClient(instance="https://x.example", token="t", min_interval_s=0, **kw)
    return c


def test_unconfigured_is_loud():
    c = sv.ServicelyClient(instance="", token="")
    with pytest.raises(sv.ServicelyError, match="not configured"):
        c.query("Incident")


def test_repr_never_carries_the_token():
    assert "secret-token" not in repr(sv.ServicelyClient(instance="https://x.example", token="secret-token"))


def test_create_prefixes_marker_and_sends_the_required_fields():
    c = _client()
    seen = {}

    def fake(method, path, payload=None, attempts=3):
        seen.update(method=method, path=path, payload=payload)
        return {"Number": "INC1"}

    c._call = fake
    c.create_incident("MFA reset", "u1", "u2", "cls", "grp", description="d")
    assert seen["method"] == "POST" and seen["path"] == "/v1/Incident"
    assert seen["payload"]["ShortDescription"].startswith(sv.MARKER + " ")
    assert {"Requestor", "RequestedFor", "Classification", "AssignmentGroup"} <= set(seen["payload"])


def test_transition_refuses_rather_than_pretending():
    with pytest.raises(sv.ServicelyError, match="system managed"):
        _client().transition("abc", "resolve_from_new")


def test_history_is_filtered_client_side_and_ordered():
    c = _client()
    rows = [
        {"RelatedTable": "other:Incident", "CreatedOn": "3"},
        {"RelatedTable": "abc:Incident", "CreatedOn": "2"},
        {"RelatedTable": "abc:Incident", "CreatedOn": "1"},
    ]
    c._call = lambda *a, **k: rows
    got = c.workflow_history("abc")
    assert [r["CreatedOn"] for r in got] == ["1", "2"]


def test_settled_facts_reopen_agreement_and_disagreement():
    cont = "c0a8016c68101bc98168115db86200e6"
    inc = {"Number": "INC2", "ResolutionCount": 2, "WorkflowStatus": {"value": "resolved"}}
    ok = sv.settled_facts(inc, [{"Transition": cont}])
    assert ok["reopened"] and not ok["counter_disagrees"]
    odd = sv.settled_facts({"Number": "INC3", "ResolutionCount": 1, "WorkflowStatus": "resolved"}, [{"Transition": cont}])
    assert odd["reopened"] and odd["counter_disagrees"]
    clean = sv.settled_facts({"Number": "INC4", "ResolutionCount": 1, "WorkflowStatus": "resolved"}, [])
    assert not clean["reopened"] and not clean["counter_disagrees"]


def test_marker_env_must_be_isolated_shape():
    assert sv._marker_from_env({}) == sv.DEFAULT_MARKER
    assert sv._marker_from_env({"PROVY_SERVICELY_MARKER": "[provy-servicely-valid]"}) == "[provy-servicely-valid]"
    with pytest.raises(ValueError):
        sv._marker_from_env({"PROVY_SERVICELY_MARKER": "[someone-elses]"})
