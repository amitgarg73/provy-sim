"""scripts/itsm_parity.py, its spec, its rule runner and the ITSM assertions (argus#1649). No network, no instance: the pure parts are held
here, the live run is in docs/sim-evidence/itsm-parity/."""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from engine import sim_assert as A

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec_ = importlib.util.spec_from_file_location("itsm_parity", ROOT / "scripts" / "itsm_parity.py")
IP = importlib.util.module_from_spec(spec_)
spec_.loader.exec_module(IP)
SPEC = IP.load_spec()
T, F = IP.fleet_of(SPEC)


# ── the spec cannot aim at the protected tenant or the default desk ─────────────────────────────
def test_the_spec_is_a_live_fleet_on_its_own_marker():
    assert F["pack"] == "itsm" and F["live"] == "servicenow"
    assert F["marker"].startswith("provy-itsm-") and F["marker"] != "provy-itsm"


def test_the_names_and_logins_keep_it_out_of_the_nightly_sweep_and_away_from_other_tenants():
    assert T["name"].startswith(SPEC["name_prefix"]) and T["name"] not in ("ITSM Demo", "Nightly Certification", "Third Eye Trading")
    emails = [T["email"]] + [u["email"] for u in T["extra_users"]]
    assert all(e.endswith("@demo.provy.ai") and not e.endswith("@argustest.com") for e in emails)
    assert set(T["notices"]["people"]) <= set(emails), "the product accepts a recipient only when the person is in the workspace"


def test_the_batch_is_inside_the_cap_and_the_tag_is_usable():
    assert 10 <= F["sessions"] <= IP.MAX_INCIDENTS == 30
    assert IP.re.fullmatch(r"[A-Za-z0-9-]{1,40}", F["tag"])


def test_the_persistent_set_still_has_no_itsm_fleet():
    persistent = json.load(open(ROOT / "config" / "sim_tenants.json"))
    assert all(f["pack"] != "itsm" for t in persistent["tenants"] for f in t["fleets"])


def test_the_run_refuses_unless_the_environment_names_the_fleets_own_marker():
    with pytest.raises(SystemExit):
        IP.require_own_marker(SPEC, {})
    with pytest.raises(SystemExit):
        IP.require_own_marker(SPEC, {"PROVY_ITSM_MARKER": "provy-itsm"})
    assert IP.require_own_marker(SPEC, {"PROVY_ITSM_MARKER": F["marker"]}) == F["marker"]


def test_a_spec_that_named_the_default_desk_is_refused():
    bad = json.loads(json.dumps(SPEC))
    bad["tenants"][0]["fleets"][0]["marker"] = "provy-itsm"
    with pytest.raises(SystemExit):
        IP.require_own_marker(bad, {"PROVY_ITSM_MARKER": "provy-itsm"})


def test_the_script_never_mentions_the_instances_ingest_key_property_as_something_to_write():
    src = (ROOT / "scripts" / "itsm_parity.py").read_text()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert 'sys_properties' not in code and 'provy.ingest.key' not in code.split('"""', 2)[2], "this tool never writes or reads the instance's ingest key"


# ── session placement ───────────────────────────────────────────────────────────────────────────
NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)


def test_a_session_starts_after_the_previous_one_ends_and_on_the_tickets_day():
    a = IP.session_start(None, "2026-10-07 11:55:00", NOW, 14.0)
    assert a == NOW - timedelta(minutes=6)
    b = IP.session_start(a + timedelta(seconds=20), "2026-10-07 11:55:30", NOW, 14.0)
    assert b == a + timedelta(seconds=20)


def test_a_session_is_never_placed_in_the_future():
    with pytest.raises(SystemExit):
        IP.session_start(NOW - timedelta(seconds=5), "2026-10-07 11:59:00", NOW, 14.0)


def test_a_ticket_opened_the_day_before_cannot_be_matched_and_is_refused():
    # run began the day before and is still going after midnight: a session after the previous one's end falls on the new day
    with pytest.raises(SystemExit):
        IP.session_start(datetime(2026, 10, 7, 0, 0, 5, tzinfo=timezone.utc), "2026-10-06 23:59:00", datetime(2026, 10, 7, 0, 20, 0, tzinfo=timezone.utc), 14.0)


def test_a_run_that_crosses_midnight_stays_on_the_tickets_day_when_it_can():
    s = IP.session_start(None, "2026-10-06 23:59:00", datetime(2026, 10, 7, 0, 20, 0, tzinfo=timezone.utc), 14.0)
    assert s.date().isoformat() == "2026-10-06"


# ── the rule, run over real-shaped records ──────────────────────────────────────────────────────
def rec(**kw):
    base = {"number": "INC1", "sys_id": "a", "close_code": "Solution provided", "reopen_count": "0", "reassignment_count": "1", "priority": "3", "category": "network",
            "opened_at": "2026-10-07 12:00:00", "closed_at": "2026-10-07 12:30:00", "correlation_display": "cat=network;grp=Network;tag=x", "assignment_group_name": "Network"}
    base.update(kw)
    return base


def test_the_rule_file_itself_produces_the_outcome_bodies():
    out = IP.payloads_from_rule([rec(), rec(number="INC2", close_code="No resolution provided"), rec(number="INC3", reassignment_count="2", assignment_group_name="Software")])
    p = {o["number"]: o["payload"] for o in out}
    assert p["INC1"]["label"] == "success" and p["INC1"]["signals"]["resolution_genuine"] is True
    assert p["INC2"]["label"] == "fail" and p["INC2"]["signals"]["resolution_genuine"] is False
    assert p["INC3"]["signals"]["self_resolved"] is False and p["INC3"]["signals"]["routing_correct"] is False
    assert p["INC1"]["occurred_at"] == "2026-10-07T12:30:00Z" and p["INC1"]["business_date"] == "2026-10-07"
    assert "first_response_time_met" not in p["INC1"]["signals"], "no SLA target was committed to, so no verdict is invented"


def test_the_rule_runner_fails_loudly_if_the_rule_logs_an_error():
    # a record the rule can only fail on would be a rule bug; the runner must not swallow it. An unparsable close stamp is not one, so
    # check the guard directly: the runner turns gs.error into an exception.
    src = (ROOT / "scripts" / "js" / "outcome_push_payloads.js").read_text()
    assert "error(m) { throw new Error('rule logged an error: ' + m); }" in src


# ── the expectation ─────────────────────────────────────────────────────────────────────────────
def planned(n=24):
    """A run's recorded steps, shaped as cmd_run saves them: triage, router (code), knowledge, resolver, reviewer, each model step with a full record."""
    import random
    from packs.itsm.pack import ItsmPack
    from test_itsm_parity import Desk, tickets, make_ctx, LeverConfig
    pack = ItsmPack(client=Desk(tickets(n)))
    truth, steps, ts = [], [], []
    t0 = datetime(2026, 10, 7, 11, 0, 0, tzinfo=timezone.utc)
    for i in range(n):
        item, gt = pack.generate_work_item(random.Random(i))
        r = pack.run_pipeline(item, gt, make_ctx(levers=LeverConfig({}), seed=i, index=i, workflow="itsm"))
        stamp = IP.iso(t0 + timedelta(seconds=30 * i))
        rec_ = pack.truth_record(r, stamp)
        truth.append(rec_)
        steps.append([{"agent": s.agent, "step_type": s.step_type, "model_run": s.model is not None, "context": s.context} for s in r.traces])
        ts.append(stamp)
    return truth, {"ts": ts, "steps": steps}


def test_the_expectation_is_ready_and_counts_what_the_pack_says_it_sent():
    truth, run = planned()
    pushed = [{"number": r["entity_id"], "status": 200, "label": "success" if i % 3 else "fail", "signals": {}} for i, r in enumerate(truth)]
    outs = IP.outs_from_run(run, truth)
    exp = IP.build_expect(SPEC, truth, outs, pushed, "2026-10-07T12:00:00+00:00", "2026-10-07")
    fe = exp["fleets"]["itsm"]
    assert fe["sessions"] == 24 and fe["scenario"] == "live" and fe["door"] == "rest"
    assert fe["outcomes"]["work_items"] == 24 and fe["outcomes"]["success"] + fe["outcomes"]["fail"] == 24
    r = fe["readiness"]
    assert r["state"] == "ready" and r["signalsSent"] == 6
    assert r["notGivenSteps"] > 0, "the code-only router steps in the window are not counted"
    assert r["givenSteps"] + r["notGivenSteps"] == r["stepsCounted"] == 20
    assert fe["itsm"]["code_only_agents"] == ["router"]
    assert fe["itsm"]["model_steps"] == fe["itsm"]["model_steps_with_record"] > 0
    assert fe["itsm"]["claims"] >= 24 * 5 and len(fe["itsm"]["claim_confidences"]) >= 4
    assert fe["roster"]["retire"] == ["triage_v1"] and "triage_v1" in fe["roster"]["agents"]
    assert exp["order"]["plan"]["tier"] == "startup" and exp["order"]["plan"]["included_steps"] == 50000
    assert fe["itsm"]["never_posted_by_the_simulation"] is True


def stored(truth, run):
    out = []
    for r, ss in zip(truth, run["steps"]):
        for s in ss:
            out.append({"agent": s["agent"], "step_type": s["step_type"], "model": "m" if s["model_run"] else None, "context": s["context"], "claim": None})
    return out


def exp_and_steps():
    truth, run = planned()
    pushed = [{"number": r["entity_id"], "status": 200, "label": "success", "signals": {}} for r in truth]
    exp = IP.build_expect(SPEC, truth, IP.outs_from_run(run, truth), pushed, "2026-10-07T12:00:00+00:00", "2026-10-07")["fleets"]["itsm"]
    rows_ = stored(truth, run)
    # attach the claims to the first stored step of each claiming agent, as the product stores them
    k = 0
    for r, ss in zip(truth, run["steps"]):
        by_agent = {}
        for c in r["claims"]:
            by_agent.setdefault(c["agent"], []).append({"signal": c["signal"], "value": c["value"], "confidence": c["confidence"]})
        for j, s in enumerate(ss):
            if s["agent"] in by_agent and s["step_type"] == "agent_message":
                rows_[k + j]["claim"] = by_agent.pop(s["agent"])
        k += len(ss)
    return exp, rows_


def run_compare(exp, rows_):
    rows = A.Rows()
    A.compare_live_itsm(rows, "I1", "itsm", exp, rows_)
    return rows


def test_the_assertions_pass_on_what_the_pack_sent():
    exp, rows_ = exp_and_steps()
    rows = run_compare(exp, rows_)
    assert rows.rows and rows.summary()["FAIL"] == 0 and rows.summary()["no-data"] == 0, [r for r in rows.rows if r["verdict"] != "pass"]


def test_a_model_step_that_lost_its_record_is_a_finding():
    exp, rows_ = exp_and_steps()
    victim = next(s for s in rows_ if s["model"] and s["context"])
    victim["context"] = None
    assert any(r["verdict"] == "FAIL" and "carrying a context record" in r["check"] for r in run_compare(exp, rows_).rows)


def test_a_code_only_step_that_gained_a_model_or_a_record_is_a_finding():
    exp, rows_ = exp_and_steps()
    router = next(s for s in rows_ if s["agent"] == "router" and s["step_type"] == "agent_message")
    router["model"] = "llama"
    verdicts = {r["check"]: r["verdict"] for r in run_compare(exp, rows_).rows}
    assert verdicts["code-only decision steps stored (no model)"] == "FAIL"


def test_a_claim_with_no_stated_confidence_is_a_finding():
    exp, rows_ = exp_and_steps()
    claimed = next(s for s in rows_ if s["claim"])
    claimed["claim"] = [{**claimed["claim"][0], "confidence": None}]
    assert any(r["verdict"] == "FAIL" and "no stated confidence" in r["check"] for r in run_compare(exp, rows_).rows)


def test_nothing_is_asserted_when_the_expectation_has_no_itsm_section():
    rows = A.Rows()
    A.compare_live_itsm(rows, "H1", "claims", {"scenario": "healthy"}, [])
    assert rows.rows == []
