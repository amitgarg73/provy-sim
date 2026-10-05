"""The context fault levers (argus#1505 test plan, step 1): each has a known answer, settles at the
configured rate, is recorded in a ground-truth file, and is never sent to Provy."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from engine.context_levers import (CONTEXT_LEVERS, PROFILES, approved_match, mark_first_after_change,
                                   truth_record, write_truth)
from engine.emitter import ProvyEmitter
from engine.levers import LeverConfig
from engine.runner import BatchRunner
from packs import get_pack

PACKS_UNDER_TEST = ("iam", "claims")
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def _run(pack, rates, n=1, seed=0, emitter=None):
    runner = BatchRunner(get_pack(pack), LeverConfig(rates), emitter=emitter, seed=seed,
                         starts_at=NOW - timedelta(days=10), every=timedelta(hours=1))
    return runner.run_batch(n)


def _model_steps(result):
    return [t for t in result.traces if t.step_type in ("agent_message", "decision")]


def _age_days(item, at):
    from engine.context import parse_iso
    return (at - parse_iso(item["as_of"])).total_seconds() / 86400


# ── manifests on a clean run ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("pack", PACKS_UNDER_TEST)
def test_clean_manifests_are_on_every_model_step_and_pass_every_declared_check(pack):
    prof = PROFILES[pack]
    outs = _run(pack, {"context_manifest": 1.0}, n=30, seed=1)
    for o in outs:
        assert o.result.faults == [] and o.result.outcome_label == "success"
        for t in o.result.traces:
            if t.step_type == "tool_call":
                assert t.context is None            # a tool call is not a model-run step
            else:
                m = t.context
                assert m is not None and m["items"], "a step that always retrieves has items"
                assert m["retrieval"]["returned"] == len(m["items"])
                assert m["instruction"]["version"] == f"{t.agent}-v1"
                assert m["instruction"]["hash"].startswith("sha256:") and len(m["instruction"]["hash"]) == 71
                for it in m["items"]:
                    assert approved_match(it["source"], prof.approved)
                    if it["kind"] in ("document", "memory"):
                        assert _age_days(it, o.record and datetime.fromisoformat(o.record["ts"])) <= prof.limit_days


@pytest.mark.parametrize("pack", PACKS_UNDER_TEST)
def test_a_manifest_holds_names_and_fingerprints_and_never_text(pack):
    o = _run(pack, {"context_manifest": 1.0}, n=1, seed=2)[0]
    allowed_item = {"kind", "source", "id", "as_of", "used", "hash", "score", "version", "tokens"}
    for t in _model_steps(o.result):
        assert set(t.context) <= {"items", "retrieval", "instruction", "tokens_in"}
        for it in t.context["items"]:
            assert set(it) <= allowed_item
            assert all(len(str(v)) <= 80 for v in it.values()), "a long value would be text"


def test_no_lever_configured_means_no_manifest_at_all():
    o = _run("iam", {}, n=5, seed=3)
    assert all(t.context is None for x in o for t in x.result.traces)


def test_two_pack_shapes_that_are_not_trading_exist_and_differ():
    assert set(PROFILES) >= {"iam", "claims"}
    assert PROFILES["iam"].approved != PROFILES["claims"].approved
    assert set(PROFILES["iam"].agents) != set(PROFILES["claims"].agents)


# ── one test per lever: the known answer ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("pack", PACKS_UNDER_TEST)
def test_stale_source_puts_an_item_past_the_limit_on_the_named_step_only(pack):
    prof = PROFILES[pack]
    for o in _run(pack, {"ctx_stale_source": {"rate": 1.0, "params": {"fail_rate": 1.0}}}, n=20, seed=4):
        f = o.result.faults[0]
        assert f.lever == "ctx_stale_source" and f.params["kind"] == "stale"
        at = datetime.fromisoformat(o.record["ts"])
        old = {t.agent: [i for i in t.context["items"] if _age_days(i, at) > prof.limit_days]
               for t in _model_steps(o.result)}
        assert [a for a, v in old.items() if v] == [f.agent], "exactly the named step carries a stale item"
        assert old[f.agent][0]["used"] is True and old[f.agent][0]["id"] == f.params["item_id"]
        assert o.result.outcome_label == "fail"


@pytest.mark.parametrize("pack", PACKS_UNDER_TEST)
def test_unapproved_source_adds_a_source_off_the_list_on_the_named_step_only(pack):
    prof = PROFILES[pack]
    for o in _run(pack, {"ctx_unapproved_source": {"rate": 1.0, "params": {"fail_rate": 1.0}}}, n=20, seed=5):
        f = o.result.faults[0]
        off = {t.agent: [i for i in t.context["items"] if not approved_match(i["source"], prof.approved)]
               for t in _model_steps(o.result)}
        assert [a for a, v in off.items() if v] == [f.agent]
        assert off[f.agent][0]["source"] == f.params["source"] and off[f.agent][0]["used"] is True


@pytest.mark.parametrize("pack", PACKS_UNDER_TEST)
def test_empty_retrieval_sends_items_empty_and_returned_zero_on_the_named_step_only(pack):
    for o in _run(pack, {"ctx_empty_retrieval": {"rate": 1.0, "params": {"fail_rate": 1.0}}}, n=20, seed=6):
        f = o.result.faults[0]
        empties = [t.agent for t in _model_steps(o.result) if t.context["items"] == [] and t.context["retrieval"]["returned"] == 0]
        assert empties == [f.agent]


@pytest.mark.parametrize("pack", PACKS_UNDER_TEST)
def test_instruction_change_moves_the_version_and_hash_only_from_the_date(pack):
    cfg = {"ctx_instruction_change": {"rate": 1.0, "params": {"fail_rate": 1.0, "change_dates": ["2026-10-01", "2026-10-04"]}}}
    outs = _run(pack, cfg, n=240, seed=7)
    seen = {}
    for o in outs:
        day = o.record["ts"][:10]
        for t in _model_steps(o.result):
            seen.setdefault((day, t.agent), set()).add(t.context["instruction"]["version"] if t.context else None)
        fired = [f for f in o.result.faults if f.lever == "ctx_instruction_change"]
        if day < "2026-10-01":
            assert not fired
        else:
            assert fired and fired[0].params["new_version"] == ("v2" if day < "2026-10-04" else "v3")
            step = next(t for t in _model_steps(o.result) if t.agent == fired[0].agent)
            assert step.context["instruction"]["version"] == f"{fired[0].agent}-{fired[0].params['new_version']}"
    # the other agents never change, and before the date the agent is on v1
    agent = next(f.agent for o in outs for f in o.result.faults)
    others = {v for (d, a), vs in seen.items() if a != agent for v in vs}
    assert others == {f"{a}-v1" for (d, a) in seen if a != agent} or all(v.endswith("-v1") for v in others)


def test_instruction_change_with_no_date_is_a_no_op_not_a_guess():
    outs = _run("iam", {"ctx_instruction_change": {"rate": 1.0}}, n=10, seed=8)
    assert all(o.result.faults == [] for o in outs)


@pytest.mark.parametrize("pack", PACKS_UNDER_TEST)
def test_manifest_missing_sends_no_manifest_on_the_named_step_only(pack):
    for o in _run(pack, {"ctx_manifest_missing": {"rate": 1.0, "params": {"fail_rate": 1.0}}}, n=20, seed=9):
        f = o.result.faults[0]
        missing = [t.agent for t in _model_steps(o.result) if t.context is None]
        assert missing == [f.agent]


# ── settling: the configured rate, and the background ────────────────────────────────────────────
@pytest.mark.parametrize("lever", sorted(CONTEXT_LEVERS))
def test_fail_rate_one_always_fails_and_zero_never_fails(lever):
    params = {"change_dates": ["2026-09-01"]}
    for fr, want in ((1.0, "fail"), (0.0, "success")):
        outs = _run("claims", {lever: {"rate": 1.0, "params": {**params, "fail_rate": fr}}}, n=25, seed=10)
        assert {o.result.outcome_label for o in outs} == {want}
        assert all(o.result.faults[0].params["caused_failure"] == (fr == 1.0) for o in outs)


def test_default_fail_rate_is_eighty_percent():
    outs = _run("iam", {"ctx_stale_source": 1.0}, n=500, seed=11)
    rate = sum(o.result.outcome_label == "fail" for o in outs) / 500
    assert abs(rate - 0.8) < 0.06, rate


@pytest.mark.parametrize("pack", PACKS_UNDER_TEST)
def test_background_failure_fails_unfaulted_items_at_its_rate_with_a_non_context_cause(pack):
    outs = _run(pack, {"context_manifest": 1.0, "background_failure": 0.2}, n=600, seed=12)
    failed = [o for o in outs if o.result.outcome_label == "fail"]
    assert abs(len(failed) / 600 - 0.2) < 0.05
    for o in failed:
        assert [f.lever for f in o.result.faults] == ["background_failure"]
        assert not set(o.result.metadata) & {"silent_policy", "silent_incomplete", "silent_wrong"}, "no label is sent"
    assert all(o.result.outcome_label == "success" for o in outs if not o.result.faults)


def test_a_fault_claims_the_item_so_the_background_cannot_stack_a_second_cause():
    outs = _run("iam", {"ctx_stale_source": {"rate": 1.0, "params": {"fail_rate": 0.0}}, "background_failure": 1.0}, n=50, seed=13)
    assert all([f.lever for f in o.result.faults] == ["ctx_stale_source"] and o.result.outcome_label == "success" for o in outs)


def test_runs_are_reproducible_from_the_seed():
    cfg = {"context_manifest": 1.0, "ctx_stale_source": 0.3, "background_failure": 0.2}
    a = [json.dumps([t.context for t in o.result.traces], sort_keys=True) for o in _run("iam", cfg, n=15, seed=14)]
    b = [json.dumps([t.context for t in o.result.traces], sort_keys=True) for o in _run("iam", cfg, n=15, seed=14)]
    assert a == b


# ── the ground truth, and what is never sent ─────────────────────────────────────────────────────
def test_truth_record_names_item_fault_step_date_and_cause_of_failure():
    o = _run("iam", {"ctx_stale_source": {"rate": 1.0, "params": {"fail_rate": 1.0}}}, n=1, seed=15)[0]
    r = truth_record("iam-fault", "fault", "iam", o.result, o.record["ts"])
    assert r["entity_id"] == o.result.entity_id and r["date"] == o.record["ts"][:10]
    assert r["faults"][0]["fault"] == "stale" and r["faults"][0]["agent"] == o.result.faults[0].agent
    assert r["faults"][0]["step_type"] in ("agent_message", "decision")
    assert r["outcome"] == "fail" and r["failure_cause"] == "context:stale"
    assert all("manifest_sent" in s for s in r["steps"])


def test_truth_says_background_for_a_failure_with_no_context_fault_and_none_for_a_success():
    outs = _run("claims", {"context_manifest": 1.0, "background_failure": 0.5}, n=40, seed=16)
    causes = {truth_record("f", "control", "claims", o.result, o.record["ts"])["failure_cause"] for o in outs}
    assert None in causes and any(c and c.startswith("background:") for c in causes)
    assert not any(c and c.startswith("context:") for c in causes)


def test_nothing_about_a_fault_is_in_any_payload_the_emitter_builds():
    em = ProvyEmitter(ingest_key="", base_url="https://dev.provy.ai", is_simulated=False)
    cfg = {"context_manifest": 1.0, "ctx_stale_source": 0.3, "ctx_unapproved_source": 0.3, "ctx_empty_retrieval": 0.3,
           "ctx_manifest_missing": 0.3, "background_failure": 0.5,
           "ctx_instruction_change": {"rate": 0.5, "params": {"change_dates": ["2026-09-01"]}}}
    outs = _run("iam", cfg, n=60, seed=17, emitter=em)
    assert any(o.result.faults for o in outs) and em.sent
    blob = json.dumps(em.sent)
    for banned in ("ctx_", "caused_failure", "background_failure", "failure_cause", "fail_roll", "age_days",
                   "first_after_change", "silent_policy", "policy_violation", "sla_breach"):
        assert banned not in blob, banned
    carried = [s for s in em.sent if s["path"] == "/api/ingest/trace" and "context" in s["payload"]]
    assert carried and all(s["payload"]["step_type"] != "tool_call" for s in carried)


def test_emitter_sends_context_at_the_top_level_of_the_trace_body():
    em = ProvyEmitter(ingest_key="", base_url="https://dev.provy.ai")
    _run("claims", {"context_manifest": 1.0}, n=1, seed=18, emitter=em)
    bodies = [s["payload"] for s in em.sent if s["path"] == "/api/ingest/trace"]
    with_ctx = [b for b in bodies if "context" in b]
    assert len(with_ctx) == 4 and all(set(b["context"]) >= {"items", "retrieval", "instruction"} for b in with_ctx)
    assert all("provy_context" not in json.dumps(b.get("payload", {})) for b in bodies)


def test_first_after_change_marks_only_the_first_session_of_each_version(tmp_path):
    recs = []
    for i, v in enumerate(["v2", "v2", "v3", "v2", "v3"]):
        recs.append({"fleet": "f", "occurred_at": f"2026-10-0{i + 1}T00:00:00Z",
                     "faults": [{"fault": "instruction", "agent": "applier", "new_version": v}]})
    digest = write_truth(str(tmp_path / "t.jsonl"), recs)
    marks = [r["faults"][0]["first_after_change"] for r in recs]
    assert marks == [True, False, True, False, False] and len(digest) == 64


def test_control_fleet_has_no_fault_and_the_same_background_rate():
    from run_context_fleet import run_fleet, summarise
    _, truth, _ = run_fleet("iam", "iam-control", "control", 300, 10, 21, now=NOW)
    s = summarise(truth)
    assert s["faults"] == {} and s["no_fault_items"] == 300
    assert abs(s["failed"] / 300 - 0.2) < 0.06
    assert all(st["manifest_sent"] for r in truth for st in r["steps"])


def test_fault_fleet_has_no_fault_in_the_warmup_and_every_kind_after_it():
    from run_context_fleet import run_fleet, summarise
    _, truth, dates = run_fleet("claims", "claims-fault", "fault", 400, 14, 22, now=NOW)
    assert not any(r["faults"] for r in truth[:40])
    s = summarise(truth)
    assert set(s["faults"]) == {"stale", "unapproved", "empty", "missing", "instruction"}
    assert len(dates) == 3
