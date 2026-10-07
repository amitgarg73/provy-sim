#!/usr/bin/env python3
"""Build, send to, configure and check the persistent pre-prod sim tenant set (docs/sim-tenants.md says how and why).

    # no network: write every fleet's truth file and each tenant's expectation file
    python scripts/sim_set.py plan [--only H1,H2] [--now 2026-10-07T20:00:00+00:00]

    # under with-secrets; passwords and ingest keys come from the 0600 credentials file and are never printed
    with-secrets SUPABASE_URL SUPABASE_KEY WAITLIST_ADMIN_KEY_PREVIEW -- python scripts/sim_set.py send H1 --creds FILE [--limit 5]
    with-secrets ...                                              -- python scripts/sim_set.py configure H1 --creds FILE [--step orders|notices|declarations|...]
    with-secrets SUPABASE_URL SUPABASE_KEY                        -- python scripts/sim_set.py status --creds FILE

`send` rebuilds the SAME plan the truth file describes (the clock and nonce are in the expectation file) and sends it through the fleet's door.
It can be stopped and run again: a ledger of sent sessions is kept in --run-dir, and the product stores a resent step once (the span id is the key).

⛔ PRE-PROD ONLY. Every client refuses anything else (engine.sim_set), and the doors ask the deployment which environment it is before the first write.
⛔ THE SIGN-UP CAP IS NOT TOUCHED. Workspaces come from provisionFleet (provy-sim-control scripts/provision-sim-set.mts), not from sign-up.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import context_sets as CS                                             # noqa: E402
from engine import sim_plan as P                                                  # noqa: E402
from engine import sim_set as X                                                   # noqa: E402
from engine.context_levers import PROFILES                                        # noqa: E402

BASE = os.environ.get("PROVY_URL", "http://localhost:3100")
RUN_DIR = os.environ.get("SIM_RUN_DIR", "/private/tmp/claude-501/-Users-amitgarg/df44c595-ffd9-40ef-8535-927a02d2085a/scratchpad/sim-tenants/run")


def tenants_of(spec: dict, only: str | None) -> list[dict]:
    keys = [k.strip() for k in only.split(",")] if only else None
    out = [t for t in spec["tenants"] if not keys or t["key"] in keys]
    if keys and len(out) != len(keys):
        raise SystemExit(f"unknown tenant in {only}")
    return out


def load_expect(key: str) -> dict:
    return json.load(open(os.path.join(P.TRUTH_DIR, key, "expect.json")))


# ── plan ─────────────────────────────────────────────────────────────────────────────────────────
def cmd_plan(a) -> int:
    spec = X.load_spec()
    now = datetime.fromisoformat(a.now) if a.now else X.utc_now_floor()
    if now > datetime.now(timezone.utc):
        raise SystemExit("the clock may not be ahead of the real one")
    today = now.date()
    for t in tenants_of(spec, a.only):
        plans = {f["key"]: P.plan_fleet(t, f, now) for f in t["fleets"]}
        doc = P.write_tenant(t, plans, now, today)
        for fk, e in doc["fleets"].items():
            r = e["readiness"]
            print(f"{t['key']}/{fk}: {e['sessions']} sessions, {e['steps']} steps, readiness {r['state']} ({r['signalsSent']}/6), truth {e['truth_sha256'][:12]}")
    return 0


# ── send ─────────────────────────────────────────────────────────────────────────────────────────
def build_door(name: str, base: str, key: str, replies):
    from engine import door_emit as D
    return D.DOORS[name](base, key, replies)


def rebuild(tenant: dict, fleet: dict, exp: dict) -> P.FleetPlan:
    now = datetime.fromisoformat(exp["meta"]["built_at_clock"])
    plan = P.plan_fleet(tenant, fleet, now, exp["meta"]["nonces"][fleet["key"]])
    want = exp["fleets"][fleet["key"]]["truth_sha256"]
    got = __import__("hashlib").sha256("".join(json.dumps(r, sort_keys=True) + "\n" for r in plan.truth).encode()).hexdigest()
    if got != want:
        raise SystemExit(f"{tenant['key']}/{fleet['key']}: the rebuilt plan does not match the recorded truth file (sha256 {got[:12]} vs {want[:12]}); refusing to send")
    return plan


def send_fleet(plan: P.FleetPlan, creds: X.Creds, tkey: str, only_phase: int | None, limit: int | None, workers: int, resend_all: bool) -> dict:
    from engine import door_emit as D
    fleet = plan.fleet
    key = creds.fleet_key(tkey, fleet["key"])
    os.environ["PROVY_URL"], os.environ["PROVY_EMIT"] = BASE, "1"          # the SDK reads these when it is imported
    D.assert_preprod(BASE, key)
    os.makedirs(RUN_DIR, exist_ok=True)
    replies = D.Replies(os.path.join(RUN_DIR, f"{tkey}.{fleet['key']}.replies.jsonl"))
    door = build_door(fleet["door"], BASE, key, replies)
    agents = __import__("packs").get_pack(fleet["pack"]).agents()
    sent_path = os.path.join(RUN_DIR, f"{tkey}.{fleet['key']}.sent.jsonl")
    done = {json.loads(l)["session_id"] for l in open(sent_path) if l.strip()} if os.path.exists(sent_path) else set()
    phases = fleet.get("phases")
    todo = []
    for i, o in enumerate(plan.outs):
        if o.result.session_id in done:
            continue
        if only_phase and P.phase_of(i, len(plan.outs), phases or 1) != only_phase:
            continue
        todo.append((i, o))
    if limit:
        todo = todo[:limit]
    print(f"{tkey}/{fleet['key']} via {fleet['door']}: sending {len(todo)} sessions ({len(done)} already sent)", flush=True)
    t0, n = time.time(), [0]
    kw_ok = fleet["door"] == "rest"

    def one(item):
        i, o = item
        try:
            ts = o.record["ts"]
            if kw_ok:
                door.send_session(o.result, ts, agents, resend_first_step=(i % 25 == 3), resend_all=resend_all)
            else:
                door.send_session(o.result, ts, agents)
            with open(sent_path, "a") as f:
                f.write(json.dumps({"session_id": o.result.session_id, "i": i}) + "\n")
            n[0] += 1
        except Exception as e:                                                     # noqa: BLE001
            print(f"  session {o.result.session_id} failed: {type(e).__name__}: {str(e)[:160]}", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, todo))
    recs = [settled_later(o, i) for i, o in todo]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        res = list(ex.map(door.send_outcome, recs))
    ok = sum(1 for r in res if r.get("status") == 200)
    print(f"  sessions sent {n[0]}/{len(todo)} in {time.time() - t0:.0f}s; outcomes accepted {ok}/{len(res)}", flush=True)
    return {"sent": n[0], "outcomes": ok, "of": len(todo)}


def settled_later(o, i: int) -> dict:
    """The outcome record with a settlement time AFTER the work: its last step plus 30 to 90 minutes (never ahead of the real clock).
    The packs date an outcome at the moment the work began, which puts the answer before the decision it answers: the Outcomes latency
    then reads an outcome that arrived first, and a claim looks as if it were made after the answer was known (found 7 Oct 2026)."""
    rec = json.loads(json.dumps(o.record, default=str))
    last = datetime.fromisoformat(rec["ts"].replace("Z", "+00:00")) + __import__("datetime").timedelta(seconds=len(o.result.traces))
    at = min(last + __import__("datetime").timedelta(minutes=30 + (i % 7) * 10), datetime.now(timezone.utc) - __import__("datetime").timedelta(minutes=1))
    rec["outcome_post"]["occurred_at"] = at.isoformat().replace("+00:00", "Z")
    return rec


def cmd_send(a) -> int:
    spec = X.load_spec()
    creds = X.Creds(a.creds)
    for t in tenants_of(spec, a.tenant):
        exp = load_expect(t["key"])
        for f in t["fleets"]:
            if a.fleet and f["key"] != a.fleet:
                continue
            plan = rebuild(t, f, exp)
            send_fleet(plan, creds, t["key"], a.phase, a.limit, a.workers, resend_all=(f["scenario"] == "roster"))
    return 0


# ── configure ────────────────────────────────────────────────────────────────────────────────────
def guardrail_rows(tenant_id: str, workflow_id: str, fleet: dict) -> list[dict]:
    """The two declared Guardrails rules, one pair per agent, selecting by agent only (config.context_sets.guardrail_rows says why)."""
    approved = P.approved_for(fleet)
    rows = []
    for agent in PROFILES[fleet["pack"]].agents:
        rows.append({"tenant_id": tenant_id, "workflow_id": workflow_id, "eval_name": f"context freshness: {agent}", "agent": agent, "layer": 3, "eval_type": "rule", "threshold": 1, "enabled": True,
                     "config": {"check": {"kind": "context_fresh", "select": {"agent": agent}, "max_age_hours": fleet["declare"]["limit_days"] * 24, "kinds": ["document", "memory"]}}})
        rows.append({"tenant_id": tenant_id, "workflow_id": workflow_id, "eval_name": f"context approved sources: {agent}", "agent": agent, "layer": 3, "eval_type": "rule", "threshold": 1, "enabled": True,
                     "config": {"check": {"kind": "context_sources_in", "select": {"agent": agent}, "allowed": approved, "kinds": ["document", "memory", "tool_result"]}}})
    return rows


def put_declarations(key: str, fleet: dict) -> dict:
    import requests
    d = fleet["declare"]
    body: dict = {}
    if d.get("limit_days"):
        body["context_max_age"] = {"hours": d["limit_days"] * 24}
        body["approved_sources"] = {"sources": P.approved_for(fleet)}
    if fleet["door"] == "log_map":
        body["context_log_fields"] = CS.DECLARATIONS["fleet_declarations"]["log"]["context_log_fields"]
    H = {"x-provy-key": key, "Content-Type": "application/json"}
    r = requests.put(BASE + "/api/compute/context-declarations", headers=H, data=json.dumps(body), timeout=120)
    g = requests.get(BASE + "/api/compute/context-declarations", headers=H, timeout=120)
    return {"put": r.status_code, "get": g.status_code}


def cmd_configure(a) -> int:
    spec = X.load_spec()
    creds = X.Creds(a.creds)
    steps = a.step.split(",") if a.step else ["declarations", "orders", "notices", "ceiling", "roster"]
    pg = X.Pg()
    for t in tenants_of(spec, a.tenant):
        exp = load_expect(t["key"])
        tid = creds.tenant_id(t["key"])
        today = date.fromisoformat(exp["meta"]["today"])
        if "declarations" in steps:
            for f in t["fleets"]:
                d = f.get("declare")
                if d and d.get("as") != "guardrails":
                    print(f"{t['key']}/{f['key']}: declarations", put_declarations(creds.fleet_key(t["key"], f["key"]), f))
                elif d and d.get("as") == "guardrails":
                    wf = creds.workflow_id(t["key"], f["key"])
                    have = pg.select("ag_eval_configs", {"select": "id", "workflow_id": f"eq.{wf}", "eval_name": "like.context*"})
                    if have:
                        print(f"{t['key']}/{f['key']}: guardrail rows already there ({len(have)})")
                    else:
                        pg.insert("ag_eval_configs", guardrail_rows(tid, wf, f))
                        print(f"{t['key']}/{f['key']}: declared Guardrails rows written")
        if "orders" in steps and t.get("order"):
            adm = X.Admin()
            have = pg.select("ag_workspace_orders", {"select": "id", "tenant_id": f"eq.{tid}", "created_by": "neq.migration 1340"})
            if have:
                print(f"{t['key']}: {len(have)} order(s) already on file, left alone")
            else:
                for route, body in X.order_bodies(t["order"], today):
                    st, js = adm.post(route, {"workspaceId": tid, **body})
                    print(f"{t['key']}: POST {route} -> {st} {'' if st < 300 else json.dumps(js)[:200]}")
                    if st >= 300:
                        return 1
        if "notices" in steps and t.get("notices"):
            n = t["notices"]
            row = {"tenant_id": tid, "notify_50": n.get("notify50", True), "notify_75": n.get("notify75", True), "notify_people": [p.lower() for p in n["people"]], "updated_by": "sim set"}
            pg.insert("ag_usage_settings", row, upsert_on="tenant_id")
            print(f"{t['key']}: notices recipients {len(n['people'])} written to ag_usage_settings")
        if "ceiling" in steps and t.get("spend_ceiling_usd"):
            adm = X.Admin()
            st, js = adm.post("/api/admin/entitlements/spend-ceiling", {"workspaceId": tid, "action": "set", "usd": t["spend_ceiling_usd"], "reason": "Sim set: upper ceiling raised for the healthy fleet"})
            print(f"{t['key']}: upper ceiling -> {st} {'' if st < 300 else json.dumps(js)[:200]}")
            if st == 409:
                print("   (already at that figure: left alone)")
        if "roster" in steps:
            for f in t["fleets"]:
                if f["scenario"] == "roster":
                    roster(pg, tid, creds.workflow_id(t["key"], f["key"]), f)
    return 0


def roster(pg: X.Pg, tenant_id: str, wf: str, fleet: dict) -> None:
    """Retire one agent and leave another unaccepted, through the same writes the product's own roster routes make."""
    now = datetime.now(timezone.utc).isoformat()
    for name in fleet.get("retire", []):
        pg.patch("ag_pipeline_agents", {"workflow_id": wf, "agent_name": name}, {"retired_at": now, "retired_reason": "Sim set: retired on purpose"})
        pg.rpc("ag_record_agent_events", {"p_events": [{"tenant_id": tenant_id, "workflow_id": wf, "agent_name": name, "event": "retired", "path": "retire", "actor": "sim set"}]})
        print(f"roster: retired {name}")
    for name in fleet.get("never_accepted", []):
        pg.patch("ag_pipeline_agents", {"workflow_id": wf, "agent_name": name}, {"acknowledged": False})
        print(f"roster: {name} left unaccepted")


# ── build: configure, send (in phases where the fleet has them) and finish, for one tenant ─────────
def wait_for_checks(pg: X.Pg, wf: str, seconds: int = 45) -> None:
    """The context checks run when a session closes. Wait until the number of check verdicts of the fleet stops moving."""
    last, stable = -1, 0
    t0 = time.time()
    while time.time() - t0 < seconds:
        n = len(pg.select("ag_evals", {"select": "id", "workflow_id": f"eq.{wf}", "layer": "eq.3"}))
        stable = stable + 1 if n == last else 0
        last = n
        if stable >= 2:
            return
        time.sleep(6)


def h6_toggle(pg: X.Pg, tid: str, wf: str, phase: int) -> None:
    """Switch the declared freshness rules off before phase 2 and back on before phase 3; the learned empty-search check goes off in phase 2 and stays off.
    The same writes the Guardrails screen's Switch off and Switch on make (POST /api/evals/config): a row's `enabled`, and a switch-only row named for the learned check."""
    if phase == 2:
        for r in pg.select("ag_eval_configs", {"select": "id,eval_name", "workflow_id": f"eq.{wf}", "eval_name": "like.context freshness*"}):
            pg.patch("ag_eval_configs", {"id": r["id"]}, {"enabled": False})
        if not pg.select("ag_eval_configs", {"select": "id", "workflow_id": f"eq.{wf}", "eval_name": "eq.retrieval_empty"}):
            pg.insert("ag_eval_configs", {"tenant_id": tid, "workflow_id": wf, "eval_name": "retrieval_empty", "agent": "*", "layer": 3, "eval_type": "rule", "threshold": 1, "enabled": False, "config": None})
        print("  H6: freshness rules switched off, learned empty-search check switched off")
    elif phase == 3:
        for r in pg.select("ag_eval_configs", {"select": "id,eval_name", "workflow_id": f"eq.{wf}", "eval_name": "like.context freshness*"}):
            pg.patch("ag_eval_configs", {"id": r["id"]}, {"enabled": True})
        print("  H6: freshness rules switched back on (the learned empty-search check stays off)")


def cmd_build(a) -> int:
    spec = X.load_spec()
    creds = X.Creds(a.creds)
    pg = X.Pg()
    for t in tenants_of(spec, a.tenant):
        exp = load_expect(t["key"])
        tid = creds.tenant_id(t["key"])
        print(f"== {t['key']} {t['name']}", flush=True)
        a.step = "declarations"
        cmd_configure(argparse.Namespace(tenant=t["key"], creds=a.creds, step="declarations"))
        for f in t["fleets"]:
            plan = rebuild(t, f, exp)
            wf = creds.workflow_id(t["key"], f["key"])
            phases = f.get("phases") or 1
            for ph in range(1, phases + 1):
                if phases > 1:
                    if ph > 1:
                        wait_for_checks(pg, wf)
                        h6_toggle(pg, tid, wf, ph)
                    print(f"  phase {ph} of {phases}", flush=True)
                send_fleet(plan, creds, t["key"], ph if phases > 1 else None, None, a.workers, resend_all=(f["scenario"] == "roster"))
            wait_for_checks(pg, wf)
        cmd_configure(argparse.Namespace(tenant=t["key"], creds=a.creds, step="orders,notices,ceiling,roster"))
    return 0


# ── status ───────────────────────────────────────────────────────────────────────────────────────
def cmd_status(a) -> int:
    spec = X.load_spec()
    creds = X.Creds(a.creds)
    pg = X.Pg()
    for t in tenants_of(spec, a.tenant):
        try:
            tid = creds.tenant_id(t["key"])
        except X.Refused:
            print(f"{t['key']} {t['name']}: not provisioned")
            continue
        for f in t["fleets"]:
            wf = creds.workflow_id(t["key"], f["key"])
            steps = pg.select("ag_traces", {"select": "id", "workflow_id": f"eq.{wf}"})
            sess = pg.select("ag_sessions", {"select": "id", "workflow_id": f"eq.{wf}"})
            print(f"{t['key']}/{f['key']}: tenant {tid[:8]} fleet {wf[:8]} sessions {len(sess)} steps {len(steps)}")
    return 0


def cmd_ids(a) -> int:
    """The tenants' ids as a table: tenant id, fleet ids, login emails. NO password and NO key: the credentials file holds those and is never read for them here."""
    spec = X.load_spec()
    creds = X.Creds(a.creds)
    pg = X.Pg()
    lines = ["| Key | Tenant | Tenant id | Fleet | Fleet id | Login | Steps |", "|---|---|---|---|---|---|---|"]
    for t in spec["tenants"]:
        try:
            c = creds.tenant(t["key"])
        except X.Refused:
            continue
        for f in t["fleets"]:
            fl = c["fleets"][f["key"]]
            n = len(pg.select("ag_traces", {"select": "id", "workflow_id": f"eq.{fl['workflow_id']}"}))
            lines.append(f"| {t['key']} | {t['name']} | {c['tenant_id']} | {f['name']} | {fl['workflow_id']} | {fl['operator_email']} | {n} |")
    text = "\n".join(lines) + "\n"
    if a.out:
        open(a.out, "w").write(text)
    print(text)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan"); p.add_argument("--only"); p.add_argument("--now"); p.set_defaults(fn=cmd_plan)
    s = sub.add_parser("send"); s.add_argument("tenant"); s.add_argument("--fleet"); s.add_argument("--creds", required=True); s.add_argument("--limit", type=int); s.add_argument("--phase", type=int); s.add_argument("--workers", type=int, default=3); s.set_defaults(fn=cmd_send)
    b = sub.add_parser("build"); b.add_argument("tenant"); b.add_argument("--creds", required=True); b.add_argument("--workers", type=int, default=3); b.set_defaults(fn=cmd_build)
    c = sub.add_parser("configure"); c.add_argument("tenant"); c.add_argument("--creds", required=True); c.add_argument("--step"); c.set_defaults(fn=cmd_configure)
    i = sub.add_parser("ids"); i.add_argument("--creds", required=True); i.add_argument("--out"); i.set_defaults(fn=cmd_ids)
    st = sub.add_parser("status"); st.add_argument("tenant", nargs="?"); st.add_argument("--creds", required=True); st.set_defaults(fn=cmd_status)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
