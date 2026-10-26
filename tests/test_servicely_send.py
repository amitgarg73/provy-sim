"""Offline tests for the Servicely send mode. A stub client only: no network, no secrets."""
import json
import re
from pathlib import Path

import pytest

from engine import servicely_send as ss
from engine.servicely import ServicelyError
from packs.servicely import scenarios as sc

ROOT = Path(__file__).resolve().parent.parent


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += max(s, 0.001)


class Stub:
    """Stands in for ServicelyClient. Records every call; tickets can be steered through states."""

    def __init__(self, usage_rows=None, open_rows=0, fail_on_create=None, fail_code=None, states=None):
        self.calls = []
        self.usage_rows = usage_rows if usage_rows is not None else []
        self.open_rows = open_rows
        self.fail_on_create = fail_on_create
        self.states = states or {}
        self.tickets = {}
        self.payloads = []
        self.events = []

    def require(self):
        pass

    def query(self, table, params=None, page_size=100):
        self.calls.append(("query", table))
        if table == "SystemAISpendRule":
            return [{"MonthlyLimit": 1000}]
        if table == "Group":
            return [{"Name": "Support", "id": "g1"}, {"Name": "Network Operations", "id": "g2"}]
        if table == "User":
            return [{"id": "u1", "Email": "a@example.com"}]
        if table == "Classification":
            return [{"Name": "Other", "id": "c1"}]
        if table == "Usage":
            off = int((params or {}).get("offset", 0))
            return self.usage_rows[off:off + 200]
        if table == "Incident":
            return [{"ShortDescription": "[provy-servicely] x", "WorkflowStatus": "in_progress"} for _ in range(self.open_rows)]
        return []

    def demo_incidents(self, limit=50):
        self.calls.append(("demo_incidents",))
        return self.query("Incident")

    def create_incident(self, short, requestor, requested_for, cls, group, description="", **extra):
        self.calls.append(("create",))
        n = len(self.tickets)
        if self.fail_on_create is not None and n >= self.fail_on_create:
            raise ServicelyError("POST /v1/Incident -> HTTP 422: nope")
        body = {"ShortDescription": f"[provy-servicely] {short}", "Description": description, "RequestedFor": requested_for, **extra}
        self.payloads.append(body)
        rec = {"id": f"id{n}", "Number": f"INC{n}"}
        self.tickets[rec["id"]] = {"state": "in_progress", "polls": 0, "journal": [], "closure": None}
        return rec

    def get_incident(self, rid, fields=None):
        self.calls.append(("get", rid))
        t = self.tickets[rid]
        t["polls"] += 1
        seq = self.states.get("seq", ["in_progress", "awaiting_client_action"])
        t["state"] = t["state"] if t["state"] in ("resolved",) else seq[min(t["polls"] - 1, len(seq) - 1)]
        if t["journal"] and self.states.get("resolve_after_reply"):
            t["state"] = "resolved"
        if t["closure"] and self.states.get("resolve_on_closure"):
            t["state"] = "resolved"
        return {"WorkflowStatus": {"value": t["state"]}}

    def update(self, table, rid, payload):
        self.calls.append(("update", rid))
        self.payloads.append(payload)
        t = self.tickets[rid]
        if "ClientJournal" in payload:
            t["journal"].append(payload["ClientJournal"])
            self.events.append(("journal", len(self.calls)))
        if "ClosureCode" in payload:
            t["closure"] = payload["ClosureCode"]
            self.events.append(("closure", len(self.calls)))
        return {}


def cfg(**over):
    env = {"INPUT_AGENT_USER": "agent-x", "INPUT_REQUESTED_FOR": "api-user-x", "INPUT_DRY_RUN": "false", "INPUT_USAGE_TABLE": "Usage", "INPUT_COUNT": "4",
           "INPUT_SETTLE_WAIT": "5", "INPUT_ARRIVAL_GAP": "1", "INPUT_CLOSE_DELAY": "0"}
    env.update({f"INPUT_{k.upper()}": str(v) for k, v in over.items()})
    return ss.read_inputs(env)


def go(stub, c, tmp_path):
    clk = FakeClock()
    out = tmp_path / "ledger.jsonl"
    rc = ss.run(c, str(out), client=stub, sleep=clk.sleep, clock=clk.now, poll=5)
    lines = [json.loads(x) for x in out.read_text().splitlines()]
    return rc, lines


# ── scenarios ───────────────────────────────────────────────────────────────
def test_every_scenario_is_complete():
    cat = sc.catalogue()
    assert len(cat) > 60
    for s in cat:
        assert s["variants"] and all(a and b for a, b in s["variants"]), s["id"]
        assert s["expected_behaviour"], s["id"]
        gt = s["ground_truth"]
        assert gt["team"] in ("Support", "Network Operations") and gt["end_state"] and "should_reopen" in gt, s["id"]
        assert s["reply_plan"], s["id"]
        assert s["customer"]["tone"], s["id"]


def test_catalogue_covers_ten_topics_and_every_behaviour():
    assert len(sc.TOPICS) == 10
    assert set(sc.KINDS) >= {"clear", "vague", "missing_detail", "wrong_category", "tempting_wrong_article", "no_article",
                             "angry_urgent", "repeat_contact", "reply_correct", "reply_wrong", "abandon", "did_not_work", "duplicate"}
    t = sc.scenario("tempting_wrong_article", "planted")
    assert t["ground_truth"]["forbidden_article"] == "Wi-Fi slow: reinstall the VPN client"
    assert sc.scenario("no_article", "printer_jam")["ground_truth"]["end_state"] == "handed_to_person"
    assert sc.scenario("did_not_work", "vpn")["ground_truth"]["should_reopen"] is True


def test_plan_is_deterministic_per_seed():
    a = [(p["scenario"]["id"], p["short"]) for p in sc.build_plan(7, 20)]
    b = [(p["scenario"]["id"], p["short"]) for p in sc.build_plan(7, 20)]
    c = [(p["scenario"]["id"], p["short"]) for p in sc.build_plan(8, 20)]
    assert a == b and a != c


def test_duplicate_copies_the_previous_topic():
    plan = sc.build_plan(3, 150, sc.parse_mix("duplicate=5,clear=1"))
    dups = [p for p in plan if p["scenario"]["kind"] == "duplicate"]
    assert dups
    for p in dups:
        assert plan[p["index"] - 1]["scenario"]["topic_key"] == p["scenario"]["topic_key"]


def test_mix_parsing():
    assert sc.parse_mix("default")["clear"] > 0
    assert sc.parse_mix("clear=2,abandon=1") == {"clear": 2.0, "abandon": 1.0}
    with pytest.raises(ValueError):
        sc.parse_mix("nonsense=1")
    with pytest.raises(ValueError):
        sc.parse_mix("clear=x")


# ── dry run ─────────────────────────────────────────────────────────────────
def test_dry_run_creates_nothing_and_calls_nothing(tmp_path, capsys):
    stub = Stub()
    out = tmp_path / "l.jsonl"
    rc = ss.main([str(out)], env={"INPUT_COUNT": "6"}, client=stub)
    assert rc == 0 and stub.calls == []
    lines = [json.loads(x) for x in out.read_text().splitlines()]
    assert lines[0]["run"]["dry_run"] is True and lines[0]["run"]["label"].startswith("dry_run")
    assert len(lines) == 7 and all(x["label"] == "dry_run" and x["would_create"] for x in lines[1:])
    assert "DRY RUN" in capsys.readouterr().out


def test_dry_run_is_the_default_when_nothing_is_set(tmp_path):
    stub = Stub()
    assert ss.main([str(tmp_path / "l")], env={}, client=stub) == 0 and stub.calls == []


# ── count caps ──────────────────────────────────────────────────────────────
def test_count_cap_and_confirm_large():
    assert cfg(count=25)["count"] == 25
    with pytest.raises(ss.Refused):
        cfg(count=26)
    assert cfg(count=150, confirm_large="yes")["count"] == 150
    with pytest.raises(ss.Refused):
        cfg(count=151, confirm_large="yes")
    with pytest.raises(ss.Refused):
        cfg(count=0)


def test_main_exits_2_on_a_refused_count(tmp_path):
    assert ss.main([str(tmp_path / "l")], env={"INPUT_COUNT": "26"}, client=Stub()) == 2


# ── spend guard ─────────────────────────────────────────────────────────────
def month_rows(costs):
    from datetime import datetime, timezone
    m = datetime.now(timezone.utc).strftime("%Y-%m")
    return [{"CreatedOn": f"{m}-01 10:00:00", "ApproximateCost": c} for c in costs]


def test_guard_passes_with_headroom(tmp_path):
    stub = Stub(usage_rows=month_rows([100, 200]))
    rc, lines = go(stub, cfg(count=2), tmp_path)
    assert rc == 0 and lines[0]["run"]["guard"]["headroom_usd"] == 700


def test_guard_refuses_on_low_headroom(tmp_path):
    stub = Stub(usage_rows=month_rows([950]))
    rc, lines = go(stub, cfg(count=2), tmp_path)
    assert rc == 2 and ("create",) not in stub.calls and "headroom" in lines[0]["run"]["refused"]


def test_guard_refuses_on_too_many_open(tmp_path):
    stub = Stub(usage_rows=month_rows([1]), open_rows=16)
    rc, lines = go(stub, cfg(count=2), tmp_path)
    assert rc == 2 and ("create",) not in stub.calls and "still open" in lines[0]["run"]["refused"]


def test_guard_refuses_when_headroom_cannot_be_computed(tmp_path):
    stub = Stub()
    rc, lines = go(stub, cfg(count=2, usage_table=""), tmp_path)
    assert rc == 2 and ("create",) not in stub.calls and "cannot be computed" in lines[0]["run"]["refused"]


def test_guard_refuses_when_usage_read_may_be_truncated(tmp_path):
    from datetime import datetime, timezone
    m = datetime.now(timezone.utc).strftime("%Y-%m")
    stub = Stub(usage_rows=[{"CreatedOn": f"{m}-01 00:00:00", "ApproximateCost": 0.01}] * (ss.USAGE_PAGE * ss.USAGE_MAX_PAGES + 1))
    rc, _ = go(stub, cfg(count=2), tmp_path)
    assert rc == 2 and ("create",) not in stub.calls


def test_guard_reads_every_page_of_the_month(tmp_path):
    stub = Stub(usage_rows=month_rows([0.5] * 450))  # three pages; 225 USD spent
    rc, lines = go(stub, cfg(count=2), tmp_path)
    g = lines[0]["run"]["guard"]
    assert rc == 0 and g["spent_usd"] == 225.0 and g["headroom_usd"] == 775.0


def test_zero_token_row_without_cost_counts_as_zero(tmp_path):
    from datetime import datetime, timezone
    m = datetime.now(timezone.utc).strftime("%Y-%m")
    rows = month_rows([1.0]) + [{"CreatedOn": f"{m}-02 10:00:00", "Model": "bge-m3", "InputTokens": "0", "OutputTokens": "0"}]
    rc, lines = go(Stub(usage_rows=rows), cfg(count=2), tmp_path)
    assert rc == 0 and lines[0]["run"]["guard"]["spent_usd"] == 1.0


def test_row_with_tokens_and_no_cost_refuses(tmp_path):
    from datetime import datetime, timezone
    m = datetime.now(timezone.utc).strftime("%Y-%m")
    rows = [{"CreatedOn": f"{m}-02 10:00:00", "Model": "x", "InputTokens": "120", "OutputTokens": "30"}]
    stub = Stub(usage_rows=rows)
    rc, _ = go(stub, cfg(count=2), tmp_path)
    assert rc == 2 and ("create",) not in stub.calls


def test_explicit_override_allows_the_run(tmp_path):
    stub = Stub()
    rc, lines = go(stub, cfg(count=2, usage_table="", headroom_override_usd=500), tmp_path)
    assert rc == 0 and lines[0]["run"]["guard"]["basis"] == "override"


def test_real_run_needs_agent_user(tmp_path):
    stub = Stub()
    rc, _ = go(stub, cfg(agent_user=""), tmp_path)
    assert rc == 2 and stub.calls == []


# ── stop rules ──────────────────────────────────────────────────────────────
def test_stops_on_first_persistent_failure_and_writes_ledger(tmp_path):
    stub = Stub(usage_rows=month_rows([1]), fail_on_create=2)
    rc, lines = go(stub, cfg(count=6), tmp_path)
    assert rc == 1
    assert sum(1 for c in stub.calls if c == ("create",)) == 3  # two made, the third failed, nothing after
    assert len(lines) == 3 and lines[0]["run"]["stopped"] and lines[1]["ticket_number"] == "INC0"


# ── customer replies ────────────────────────────────────────────────────────
def test_replies_are_capped_by_max_exchanges(tmp_path):
    stub = Stub(usage_rows=month_rows([1]), states={"seq": ["awaiting_client_action"]})
    c = cfg(count=3, max_exchanges=1, scenario_mix="reply_wrong=1", settle_wait=10)
    rc, lines = go(stub, c, tmp_path)
    assert rc == 0
    for t in lines[1:]:
        assert len(t["replies_played"]) <= 1
    assert max(len(t["replies_played"]) for t in lines[1:]) == 1


def test_confirm_sets_the_closure_code_and_reject_is_a_reply(tmp_path):
    stub = Stub(usage_rows=month_rows([1]), states={"seq": ["awaiting_client_action"], "resolve_after_reply": True})
    rc, lines = go(stub, cfg(count=2, scenario_mix="clear=1", settle_wait=10), tmp_path)
    assert rc == 0 and all(t["final_state"] == "resolved" for t in lines[1:])
    assert all(t["closure"] == "Solution provided" for t in stub.tickets.values())
    stub2 = Stub(usage_rows=month_rows([1]), states={"seq": ["awaiting_client_action"], "resolve_after_reply": True})
    rc, lines = go(stub2, cfg(count=1, scenario_mix="did_not_work=1", settle_wait=10), tmp_path)
    assert all(t["closure"] is None for t in stub2.tickets.values())
    assert any("not" in j or "still" in j or "No change" in j for t in stub2.tickets.values() for j in t["journal"])


def test_abandoned_ticket_gets_no_reply_and_is_recorded(tmp_path):
    stub = Stub(usage_rows=month_rows([1]), states={"seq": ["awaiting_client_action"]})
    rc, lines = go(stub, cfg(count=2, scenario_mix="abandon=1", settle_wait=2), tmp_path)
    assert rc == 0 and all(t["journal"] == [] for t in stub.tickets.values())
    assert all(t["final_state"] == "abandoned" for t in lines[1:])


# ── the answer key stays home ───────────────────────────────────────────────
def test_ground_truth_never_in_any_payload(tmp_path):
    stub = Stub(usage_rows=month_rows([1]), states={"seq": ["awaiting_client_action"], "resolve_after_reply": True})
    rc, _ = go(stub, cfg(count=25, settle_wait=20), tmp_path)
    blob = json.dumps(stub.payloads)
    secrets_ = {"handed_to_person", "resolved_as_duplicate", "forbidden_article", "should_reopen", "ground_truth",
                "expected_behaviour", "article_topic", "reply_plan", "scenario_id"}
    for s in secrets_:
        assert s not in blob
    for s in sc.catalogue()[:60]:
        assert s["expected_behaviour"] not in blob and s["id"] not in blob
    assert "seed" not in {k for p in stub.payloads for k in p}
    # assigned to the agent user, marked, requested for the directory user
    first = stub.payloads[0]
    assert first["Assignee"] == "agent-x" and first["ShortDescription"].startswith("[provy-servicely] ")


# ── workflow file ───────────────────────────────────────────────────────────
def test_workflow_declares_exactly_the_inputs_the_runner_reads():
    text = (ROOT / ".github/workflows/run-servicely-send.yml").read_text()
    block = text.split("inputs:", 1)[1].split("jobs:", 1)[0]
    declared = set(re.findall(r"^      ([a-z_]+):\s*\{", block, re.M))
    assert declared == set(ss.INPUT_DEFAULTS)
    for name, default in ss.INPUT_DEFAULTS.items():
        m = re.search(rf"^      {name}:.*default: '([^']*)'", block, re.M)
        if name in ("agent_user", "requested_for"):
            # The two demo user ids live ONLY as workflow defaults (the repository is private), so the console never handles ids.
            assert m and re.fullmatch(r"[0-9a-f]{32}", m.group(1)), name
            continue
        assert m and m.group(1) == default, name
    env_names = set(re.findall(r"INPUT_([A-Z_]+):", text))
    assert env_names == {n.upper() for n in ss.INPUT_DEFAULTS}
    assert "dry_run" in declared and ss.INPUT_DEFAULTS["dry_run"] == "true"
    assert "upload-artifact" in text


def test_real_run_needs_requested_for(tmp_path):
    stub = Stub()
    rc, _ = go(stub, cfg(requested_for=""), tmp_path)
    assert rc == 2
    assert stub.tickets == {}


def test_requested_for_is_the_customer_on_every_ticket(tmp_path):
    stub = Stub()
    go(stub, cfg(), tmp_path)
    made = [b for b in stub.payloads if "ShortDescription" in b]
    assert made and all(b["RequestedFor"] == "api-user-x" for b in made)


def test_ten_clear_tickets_cover_ten_different_topics():
    from packs.servicely import scenarios as sc
    plan = sc.build_plan(7, 10, sc.parse_mix("clear=1"))
    assert len({p["scenario"]["topic_key"] for p in plan}) == 10
    assert plan == sc.build_plan(7, 10, sc.parse_mix("clear=1"))   # still deterministic for a seed


# ── the closure code waits for the agent to react (26 Oct) ───────────────────
def test_closure_code_is_held_back_after_a_confirming_reply(tmp_path):
    stub = Stub(usage_rows=month_rows([1]), states={"seq": ["awaiting_client_action"], "resolve_on_closure": True})
    rc, lines = go(stub, cfg(count=1, scenario_mix="clear=1", settle_wait=10, close_delay=30), tmp_path)
    kinds = [k for k, _ in stub.events]
    assert rc == 0 and kinds == ["journal", "closure"]
    gap = stub.events[1][1] - stub.events[0][1]
    assert gap >= 5, f"only {gap} calls between the reply and the closure code"  # polled for the whole delay (30 s at 5 s a poll), not written in the same breath
    assert lines[1]["final_state"] == "resolved"


def test_no_delay_keeps_the_old_back_to_back_behaviour(tmp_path):
    stub = Stub(usage_rows=month_rows([1]), states={"seq": ["awaiting_client_action"], "resolve_on_closure": True})
    go(stub, cfg(count=1, scenario_mix="clear=1", settle_wait=10, close_delay=0), tmp_path)
    assert [k for k, _ in stub.events] == ["journal", "closure"] and stub.events[1][1] - stub.events[0][1] <= 1


def test_ticket_resolved_by_someone_else_in_the_wait_gets_no_closure_code(tmp_path):
    stub = Stub(usage_rows=month_rows([1]), states={"seq": ["awaiting_client_action"], "resolve_after_reply": True})
    go(stub, cfg(count=1, scenario_mix="clear=1", settle_wait=10, close_delay=30), tmp_path)
    assert [k for k, _ in stub.events] == ["journal"]
