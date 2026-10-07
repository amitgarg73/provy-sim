"""The persistent pre-prod sim tenant set: spec, plans, ground truth, expectations, doors and the comparison rows.

Two NAMED REGRESSION CASES live here, as the founder asked on 7 Oct 2026:

  test_customer_missed_it_no_manifest          a customer who never sent a context record: nothing breaks, everything reads unknown, nothing is a pass
  test_thin_manifest_case_third_eye_shape      a thin record (code steps with none, dates on some items, no instruction fingerprint): thin, never ready

Both also run the PRODUCT's own readiness function (web/lib/readiness-report.ts through provy-sim-control's vite-node) on the plan and require the same
answer as the expectation, when that tooling is checked out beside this repo; they are skipped, not passed, when it is not.
"""
import json
import os
import re
import shutil
import subprocess
from datetime import date, datetime, timezone

import pytest

from engine import sim_assert as A
from engine import sim_plan as P
from engine import sim_scenarios as S
from engine import sim_set as X
from engine.context_levers import PROFILES
from packs import get_pack

NOW = datetime(2026, 10, 7, 14, 0, tzinfo=timezone.utc)
SPEC = X.load_spec()
TENANTS = {t["key"]: t for t in SPEC["tenants"]}


def fleet(t, f):
    return next(x for x in TENANTS[t]["fleets"] if x["key"] == f)


def plan(t, f):
    return P.plan_fleet(TENANTS[t], fleet(t, f), NOW)


# ── the spec ─────────────────────────────────────────────────────────────────────────────────────
def test_the_set_has_twelve_tenants_and_a_stable_prefix():
    assert len(SPEC["tenants"]) == 12
    assert all(t["name"].startswith("Sim ") for t in SPEC["tenants"])


def test_no_login_is_on_the_domain_the_nightly_sweep_deletes():
    emails = [t["email"] for t in SPEC["tenants"]] + [u["email"] for t in SPEC["tenants"] for u in t.get("extra_users", [])]
    assert emails and all(e.endswith("@demo.provy.ai") for e in emails)
    assert not any("argustest" in e for e in emails)
    assert len(set(emails)) == len(emails)


def test_no_name_is_one_the_sweep_or_another_job_owns():
    names = {t["name"] for t in SPEC["tenants"]}
    for foreign in ("Nightly Certification", "Third Eye Trading", "ITSM Demo", "Larkspur Lending", "Tidewater Outfitters"):
        assert foreign not in names


def test_the_itsm_pack_is_not_in_the_set():
    assert all(f["pack"] != "itsm" for t in SPEC["tenants"] for f in t["fleets"])


def test_every_scenario_and_door_is_one_the_engine_knows():
    from engine import door_emit as D
    for t in SPEC["tenants"]:
        for f in t["fleets"]:
            assert f["scenario"] in S.SCENARIOS
            assert f["door"] in D.DOORS
            get_pack(f["pack"])


def test_notice_recipients_are_users_of_the_workspace():
    for t in SPEC["tenants"]:
        n = t.get("notices")
        if n:
            users = {t["email"]} | {u["email"] for u in t.get("extra_users", [])}
            assert set(n["people"]) <= users


@pytest.mark.parametrize("key", sorted(TENANTS))
def test_volume_per_tenant_is_bounded_to_roughly_100_to_400_steps(key):
    total = sum(len(plan(key, f["key"]).truth) and sum(len(r["steps"]) for r in plan(key, f["key"]).truth) for f in TENANTS[key]["fleets"])
    assert 80 <= total <= 480, (key, total)


# ── plans are deterministic and ground truth is never sent ───────────────────────────────────────
def test_the_same_clock_and_nonce_give_the_same_plan():
    a, b = plan("H4", "iam"), plan("H4", "iam")
    assert S.digest(a.truth) == S.digest(b.truth)
    assert a.expect["session_ids"] == b.expect["session_ids"]


def test_a_different_seed_gives_a_different_plan():
    other = None
    for seed in range(2000, 2010):                       # a seed whose draws collide is refused, so take the first that does not
        try:
            other = P.plan_fleet(TENANTS["H4"], dict(fleet("H4", "iam"), seed=seed), NOW)
            break
        except ValueError:
            continue
    assert other is not None
    assert S.digest(other.truth) != S.digest(plan("H4", "iam").truth)


def test_the_door_bodies_carry_no_ground_truth():
    from engine import door_emit as D
    p = plan("H4", "iam")
    door = D.RestDoor("http://localhost:3100", "k", D.Replies())
    bodies = []
    for o in p.outs[:30]:
        plans = D.plan_steps(o.result, o.record["ts"])
        ids = [f"{i:016x}" for i in range(len(plans))]
        bodies += [json.dumps(door.step_body(o.result, pl, ids[pl.i], ids)) for pl in plans]
    blob = "\n".join(bodies)
    for leak in ("ctx_stale", "ctx_unapproved", "ctx_empty", "caused_failure", "background_failure", "planted", "expect"):
        assert leak not in blob


# ── the scenarios ────────────────────────────────────────────────────────────────────────────────
def test_customer_missed_it_no_manifest():
    """NAMED REGRESSION CASE. The customer who never sent a manifest: nothing breaks, everything reads unknown, and nothing is a pass."""
    p = plan("H3", "iam")
    assert all(t.context is None for o in p.outs for t in o.result.traces), "no step may carry a record in this scenario"
    r = p.expect["readiness"]
    assert r["state"] == "thin" and r["signalsSent"] == 0, "work arrived, so it is judged; with nothing sent it is thin with 0 of 6"
    assert all(v == "not_sent" for v in r["signals"].values()), "every signal reads not sent, never sent"
    assert r["state"] != "ready"
    # nothing breaks: every session still settles and counts
    assert p.expect["outcomes"]["work_items"] == len(p.outs)
    assert p.expect["counted_steps"] == p.expect["steps"]
    # the expectation has no pass anywhere: no declared findings, no instruction story, no claim table to read as healthy
    assert "declared" not in p.expect and "instruction_findings" not in p.expect
    _product_equals_expected(p)


def test_thin_manifest_case_third_eye_shape():
    """NAMED REGRESSION CASE. A thin record, the Third Eye shape: one agent runs code and no model, dates on some items only, no instruction fingerprint."""
    p = plan("H2", "claims")
    r = p.expect["readiness"]
    assert r["state"] == "thin"
    assert r["notGivenSteps"] > 0, "the code-only step is left out, not counted as a missing record"
    assert r["signals"]["source_age"] == "partly_sent"
    assert r["signals"]["instruction_fingerprint"] == "not_sent"
    assert r["signals"]["source"] == r["signals"]["item_fingerprint"] == r["signals"]["search_result"] == "sent"
    code_only = S.PACK_ROLES["claims"]["code_only"]
    for o in p.outs:
        for t in o.result.traces:
            if t.agent == code_only and t.step_type in S.DECISION_TYPES:
                assert t.model is None and not t.tokens_input and not t.cost_usd and t.context is None
    _product_equals_expected(p)


def test_healthy_fleet_sends_all_six_signals_on_every_model_step():
    p = plan("H1", "claims")
    assert p.expect["readiness"]["state"] == "ready" and p.expect["readiness"]["signalsSent"] == 6
    for o in p.outs:
        for t in o.result.traces:
            if t.step_type in S.DECISION_TYPES:
                assert all(S.signals_of_manifest(t.context).values())
    _product_equals_expected(p)


def test_a_fleet_below_twenty_decision_steps_is_too_early_never_ready():
    f = dict(fleet("H1", "claims"), sessions=4)
    outs = S.build_runs(f, NOW, "tiny")
    assert S.expected_readiness(outs, "rest")["state"] == "too_early"


def test_the_claim_is_about_a_signal_the_product_can_grade():
    """The first tenant built fell to Provy's forecast on all 60 rows because the claim named a remapped signal (found 7 Oct 2026)."""
    for pack, role in S.PACK_ROLES.items():
        pk = get_pack(pack)
        crit = {c.signal: c for c in pk.contract()}
        c = crit[role["claim_signal"]]
        assert c.op == "eq" and c.type == "success"
        assert role["claim_signal"] not in pk.trace_aliases(), "a remapped signal is not graded without a human confirming its reading"


def test_calibrated_agent_holds_about_as_often_as_it_says_and_overconfident_does_not():
    cal = plan("H7", "calibrated").expect["calibration"]
    over = plan("H7", "overconfident").expect["calibration"]
    assert set(over) == {"0.9"}
    assert over["0.9"]["rate"] < 0.75, over
    total = sum(b["claims"] for b in cal.values())
    held = sum(b["held"] for b in cal.values())
    assert 0.5 <= held / total <= 0.9
    assert all(not b["too_few"] for b in cal.values()), "each stated confidence has at least 10 claims, so a rate is judged"


def test_one_claim_per_work_item_and_it_carries_a_stated_confidence():
    for o in plan("H1", "claims").outs:
        claims = [t.payload_extra["provy_claim"] for t in o.result.traces if "provy_claim" in t.payload_extra]
        assert len(claims) == 1 and len(claims[0]) == 1
        assert 0 < claims[0][0]["confidence"] <= 1


def test_planted_faults_are_planted_after_the_warmup_only():
    p = plan("H4", "iam")
    assert sum(p.expect["planted"].values()) > 0
    for r in p.truth:
        if r["faults"]:
            assert r["index"] >= S.WARMUP


def test_declared_findings_come_from_the_manifests_not_from_the_fault_labels():
    p = plan("H4", "iam")
    found = p.expect["declared"]["findings"]
    planted_stale = {r["session_id"] for r in p.truth for f in r["faults"] if f["fault"] == "stale"}
    assert planted_stale <= set(p.expect["declared"]["stale_sessions"])
    planted_bad = {r["session_id"] for r in p.truth for f in r["faults"] if f["fault"] == "unapproved"}
    assert planted_bad <= set(p.expect["declared"]["unapproved_sessions"])
    assert found


def test_the_instruction_stories_are_a_change_an_alternating_rollout_and_a_same_label_change():
    p = plan("H5", "iam")
    f = p.expect["instruction_findings"]
    by = {}
    for x in f:
        by.setdefault(x["agent"], []).append(x["kind"])
    assert by["intake"] == ["changed"]
    assert by["mapper"] == ["changed", "went_back", "back_and_forth"], "the fourth and later flips are quiet"
    assert by["applier"] == ["changed"], "same label, new fingerprint: still a change"
    assert "reviewer" not in by
    # same label really means same label
    labels = {t.context["instruction"]["version"] for o in p.outs for t in o.result.traces if t.agent == "applier" and t.context}
    hashes = {t.context["instruction"]["hash"] for o in p.outs for t in o.result.traces if t.agent == "applier" and t.context}
    assert len(labels) == 1 and len(hashes) == 2


def test_the_declared_checks_fleet_has_three_phases_with_the_rule_off_in_the_second():
    p = plan("H6", "claims")
    ph = p.expect["phases"]
    assert ph["count"] == 3
    assert ph["checks"]["2"]["fresh"] is False if "2" in ph["checks"] else ph["checks"][2]["fresh"] is False
    for sid, f in p.expect["declared"]["findings"].items():
        if f["phase"] == 2:
            assert not f["stale"]


def test_the_door_fleets_tell_one_story_and_only_the_field_map_loses_signals():
    base = [plan("H8", k) for k in ("sdk", "otlp", "rest", "log", "logmap")]
    assert len({S.digest([r["entity_id"] for r in p.truth]) for p in base}) == 1, "the same work items on every door"
    for p in base[:4]:
        assert p.expect["readiness"]["state"] == "ready"
    lm = base[4].expect
    assert lm["readiness"]["state"] == "thin"
    sig = lm["readiness"]["signals"]
    assert all(sig[k] == "not_sent" for k in S.DOOR_LIMITS["log_map"]), "the door's documented limit"
    # the field map binds a retrieval to the agent's first event: for an agent that looks something up first that is the lookup, so its decision step has no record
    assert all(sig[k] == "partly_sent" for k in ("source", "source_age", "search_result"))
    assert lm["door_limit"] == S.DOOR_LIMITS["log_map"]


def test_the_roster_fleet_resends_every_step_and_names_who_is_retired_and_never_accepted():
    p = plan("H9", "claims")
    assert p.expect["roster"]["retire"] == ["reviewer"] and p.expect["roster"]["never_accepted"] == ["validator"]


# ── doors ────────────────────────────────────────────────────────────────────────────────────────
def test_the_log_line_door_writes_a_context_line_beside_each_model_step():
    from engine import door_emit as D
    p = plan("H8", "log")
    o = p.outs[0]
    body = D.LogDoor("http://localhost:3100", "k", D.Replies(), "line").body(o.result, o.record["ts"])
    lines = [json.loads(l) for l in body["logs"].splitlines()]
    ctx = [l for l in lines if "provy_context" in l]
    assert len(ctx) == sum(1 for t in o.result.traces if t.step_type in S.DECISION_TYPES and t.context)
    assert all("hash" in it for l in ctx for it in l["provy_context"]["items"])


def test_the_field_map_door_sends_no_fingerprint_and_no_instruction():
    from engine import door_emit as D
    p = plan("H8", "logmap")
    o = p.outs[0]
    body = D.LogDoor("http://localhost:3100", "k", D.Replies(), "map").body(o.result, o.record["ts"])
    assert "provy_context" not in body["logs"] and "sha256" not in body["logs"]
    lines = [json.loads(l) for l in body["logs"].splitlines()]
    mapped = [l for l in lines if "retrieved" in l]
    assert mapped and all(set(d) == {"index", "doc", "updated", "cited"} for l in mapped for d in l["retrieved"])


def test_the_rest_door_can_resend_every_step_with_the_same_span_id():
    from engine import door_emit as D

    class Spy(D.Wire):
        def __init__(self):
            self.sent = []

        def post(self, path, body, session="", extra_headers=None, method="POST"):
            self.sent.append((path, body))
            return {"status": 200, "json": {}}

    door = D.RestDoor("http://localhost:3100", "k", D.Replies())
    door.w = Spy()
    o = plan("H9", "claims").outs[0]
    door.send_session(o.result, o.record["ts"], None, resend_all=True)
    traces = [b["span_id"] for p, b in door.w.sent if p == "/api/ingest/trace"]
    assert len(traces) == 2 * len(o.result.traces) and len(set(traces)) == len(o.result.traces)


# ── orders and pilots ────────────────────────────────────────────────────────────────────────────
TODAY = date(2026, 10, 7)


def test_pilot_states_follow_the_day_rule():
    o = {"starts_on": "2026-09-20", "pilot_ends_on": "2026-10-21", "paid": False, "has_pricing": True}
    assert X.pilot_state([o], TODAY) == {"state": "running", "endsOn": "2026-10-21", "daysLeft": 14, "mark": 14}
    assert X.pilot_state([dict(o, pilot_ends_on="2026-10-10")], TODAY)["mark"] == 3
    assert X.pilot_state([dict(o, pilot_ends_on="2026-10-07")], TODAY)["daysLeft"] == 0, "the end date is the last free day"
    ended = X.pilot_state([dict(o, pilot_ends_on="2026-10-02")], TODAY)
    assert ended["state"] == "ended" and ended["daysSince"] == 5
    conv = X.pilot_state([dict(o, pilot_ends_on="2026-10-02"), {"starts_on": "2026-10-03", "pilot_ends_on": None, "paid": True, "has_pricing": True}], TODAY)
    assert conv["state"] == "converted" and conv["via"] == "new_order"
    assert X.pilot_state([dict(o, pilot_ends_on="2026-10-02", paid=True)], TODAY)["via"] == "paid"
    assert X.pilot_state([{"starts_on": "2026-09-20", "pilot_ends_on": None, "paid": False, "has_pricing": True}], TODAY)["state"] == "none"


def test_each_pilot_tenant_lands_in_its_planned_state_on_the_build_day():
    want = {"H3": ("running", 14), "H5": ("running", 3), "P1": ("ended", None), "P2": ("converted", None), "P3": ("running", None)}
    for key, (state, mark) in want.items():
        e = P.order_expect(TENANTS[key], TODAY)
        got = e["pilot_as_of_build"]
        assert got["state"] == state, (key, got)
        if mark:
            assert got["mark"] == mark
    assert P.order_expect(TENANTS["P3"], TODAY)["pilot_as_of_build"]["daysLeft"] == 20, "extended: the end date moved out"
    assert P.order_expect(TENANTS["P1"], TODAY)["pilot_as_of_build"]["daysSince"] == 5


def test_orders_say_fleet_plan_per_agent_and_enterprise_with_a_discount():
    h1 = X.order_bodies(TENANTS["H1"]["order"], TODAY)[0][1]["terms"]
    assert h1 == {"pricingMode": "fleet", "tier": "growth"}
    h2 = X.order_bodies(TENANTS["H2"]["order"], TODAY)[0][1]["terms"]
    assert h2["tier"] == "enterprise" and h2["fleetFee"] == "2500.00" and h2["fleetSteps"] == 40
    assert h2["discountLines"] == [{"kind": "all", "percent": 100, "note": "internal customer (sim)"}]
    h6 = X.order_bodies(TENANTS["H6"]["order"], TODAY)[0][1]["terms"]
    assert h6["pricePerAgent"] == "300.00" and "pricingMode" not in h6
    assert X.fleet_plan_words(TENANTS["H1"]["order"]) == {"mode": "fleet", "tier": "growth", "name": "Growth", "included_steps": 150000, "fee_cents": 119900}


def test_extension_is_a_second_call_and_conversion_a_second_order():
    p3 = X.order_bodies(TENANTS["P3"]["order"], TODAY)
    assert [c[0] for c in p3] == ["/api/admin/orders", "/api/admin/entitlements/pilot"]
    p2 = X.order_bodies(TENANTS["P2"]["order"], TODAY)
    assert [c[0] for c in p2] == ["/api/admin/orders", "/api/admin/orders"]
    assert p2[1][1]["paid"] is True and "pilotEndsOn" not in p2[1][1]["terms"]


# ── safety of the tools ──────────────────────────────────────────────────────────────────────────
def test_pg_refuses_a_project_that_is_not_pre_prod():
    with pytest.raises(X.Refused):
        X.Pg(url="https://eckthcvacrkfjihluubt.supabase.co", key="x")


def test_admin_refuses_a_host_that_is_not_pre_prod():
    with pytest.raises(X.Refused):
        X.Admin(base="https://provy.ai", key="x")


def test_credentials_file_must_be_private(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"tenants": {}}')
    os.chmod(p, 0o644)
    with pytest.raises(X.Refused):
        X.Creds(str(p))
    os.chmod(p, 0o600)
    X.Creds(str(p))


def test_the_clock_may_not_be_ahead_of_the_real_one():
    r = subprocess.run(["python3", "scripts/sim_set.py", "plan", "--only", "H1", "--now", "2099-01-01T00:00:00+00:00"], capture_output=True, text=True,
                       cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert r.returncode != 0


# ── the comparison rows ──────────────────────────────────────────────────────────────────────────
def test_readiness_comparison_marks_a_mismatch_fail_and_a_missing_read_no_data():
    p = plan("H1", "claims")
    good = {"state": "ready", "window": {"stepsCounted": 20, "givenSteps": 20, "notGivenSteps": 0}, "signals": {k: {"state": "sent"} for k in S.SIGNAL_IDS}}
    rows = A.Rows()
    A.compare_readiness(rows, "H1", "claims", p.expect, good)
    assert rows.summary()["FAIL"] == 0 and rows.summary()["pass"] > 5
    bad = json.loads(json.dumps(good))
    bad["signals"]["source_age"]["state"] = "partly_sent"
    rows = A.Rows()
    A.compare_readiness(rows, "H1", "claims", p.expect, bad)
    assert rows.summary()["FAIL"] >= 1
    rows = A.Rows()
    A.compare_readiness(rows, "H1", "claims", p.expect, None)
    assert rows.summary()["no-data"] == 1


def test_no_context_comparison_fails_when_any_check_passes_on_nothing():
    p = plan("H3", "iam")
    ext = {"u1": p.expect["session_ids"][0]}
    rows = A.Rows()
    A.compare_no_context(rows, "H3", "iam", p.expect, [{"eval_name": "retrieval_empty", "session_id": "u1", "passed": True}], ext)
    assert rows.summary()["FAIL"] == 2
    rows = A.Rows()
    A.compare_no_context(rows, "H3", "iam", p.expect, [], ext)
    assert rows.summary()["FAIL"] == 0


def test_expected_empty_is_judged_only_on_a_step_that_is_usually_non_empty():
    p = plan("H4", "iam")
    e = A.expected_empty_catches(p.truth)
    for sid, v in e.items():
        assert v["judged"] == (v["others"] >= 20 and v["non_empty"] / v["others"] >= 0.95 and S.wilson_lower(v["non_empty"], v["others"]) >= 0.8)


# ── the product's own readiness function agrees with the expectation (skipped when the tooling is not beside this repo) ──────────────────────
CONTROL = os.environ.get("PROVY_SIM_CONTROL", os.path.expanduser("~/Claude Projects/provy-sim-control"))
ARGUS_WEB = os.environ.get("ARGUS_WEB", os.path.expanduser("~/Claude Projects/argus/web"))
HAVE_PRODUCT = os.path.exists(os.path.join(CONTROL, "node_modules/.bin/vite-node")) and os.path.exists(os.path.join(ARGUS_WEB, "lib/readiness-report.ts")) and shutil.which("npx")


def _product_equals_expected(p):
    if not HAVE_PRODUCT:
        pytest.skip("the product's readiness function is not available beside this repo (needs provy-sim-control node_modules and argus web)")
    steps = []
    for si, o in enumerate(p.outs):
        for i, t in enumerate(o.result.traces):
            if t.step_type in S.DECISION_TYPES:
                steps.append({"at": S.step_time(o, i).timestamp(), "manifest": S.project_for_door(t.context, p.fleet["door"]),
                              "agent": t.agent, "ran": bool(t.model or t.tokens_input or t.tokens_output or t.cost_usd)})
    steps.sort(key=lambda s: -s["at"])
    cfg = os.path.join(CONTROL, "vite.observe.config.mts")
    script = os.path.join(CONTROL, "scripts/readiness-of-steps.mts")
    if not os.path.exists(script):
        pytest.skip("scripts/readiness-of-steps.mts is not in provy-sim-control")
    r = subprocess.run(["npx", "vite-node", "--config", cfg, script], cwd=CONTROL, input=json.dumps({"steps": steps, "door": "rest"}), capture_output=True, text=True,
                       env={**os.environ, "ARGUS_WEB": ARGUS_WEB}, timeout=300)
    assert r.returncode == 0, r.stderr[-400:]
    got = json.loads(r.stdout.strip().splitlines()[-1])
    e = p.expect["readiness"]
    assert got["state"] == e["state"]
    assert got["givenSteps"] == e["givenSteps"] and got["notGivenSteps"] == e["notGivenSteps"]
    assert got["signals"] == e["signals"]


def test_no_fleet_plans_the_same_work_item_twice():
    """A colliding draw would store one session where the plan says two (H4 on 7 Oct 2026). build_runs refuses such a plan."""
    for t in SPEC["tenants"]:
        for f in t["fleets"]:
            ids = plan(t["key"], f["key"]).expect["session_ids"]
            assert len(ids) == len(set(ids)), (t["key"], f["key"])
