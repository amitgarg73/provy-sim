#!/usr/bin/env python3
"""Assert what the product says about each sim tenant against the tenant's ground truth. SELECT-only on pre-prod, plus reads of the product's own functions.

    with-secrets SUPABASE_URL SUPABASE_KEY WAITLIST_ADMIN_KEY_PREVIEW -- python scripts/sim_assert.py --creds FILE [--only H1,H2] [--out docs/sim-evidence/assert.json]

Where the observations come from (nothing is written):
  * the product's own functions, run read-only on the SOURCE of the build under test by provy-sim-control `scripts/observe-fleets.mts` (vite-node):
    the Ingestion answer (readiness), the Outcomes answer, claim calibration;
  * SELECTs through PostgREST: stored steps and sessions (counted from paged reads, never from one response), check verdicts, config, orders;
  * the staff routes' GETs on the local server (workspace entitlement view: order, pilot, notices, ceiling).
Each comparison is in engine/sim_assert.py. Exit code 0 only when no row is a FAIL or no-data.

⛔ A MISMATCH IS A FINDING. File it (gh issue create, label bug) with this file's row as the evidence; do not edit the expectation to match.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import sim_assert as A      # noqa: E402
from engine import sim_set as X         # noqa: E402
from engine import sim_plan as P        # noqa: E402

SIM_CONTROL = os.environ.get("PROVY_SIM_CONTROL", os.path.expanduser("~/Claude Projects/provy-sim-control"))
DOOR_TAG = {"rest": "rest", "sdk": "rest", "otlp": "otlp", "log": "log", "log_map": "log"}


def observe(creds_path: str, only: str | None) -> dict:
    cmd = ["npx", "vite-node", "--config", "vite.observe.config.mts", "scripts/observe-fleets.mts", "--creds", creds_path]
    if only:
        cmd += ["--only", only]
    p = subprocess.run(cmd, cwd=SIM_CONTROL, capture_output=True, text=True, timeout=900, env=os.environ.copy())
    if p.returncode != 0:
        raise SystemExit("the observer failed: " + (p.stderr or p.stdout)[-500:])
    return json.loads(p.stdout.strip().splitlines()[-1])


def load_truth(path: str) -> list[dict]:
    return [json.loads(l) for l in open(path) if l.strip()]


def db_view(pg: X.Pg, wf: str, exp: dict, truth: list[dict]) -> tuple[dict, list[dict], dict, dict]:
    sess = pg.select("ag_sessions", {"select": "id,external_id", "workflow_id": f"eq.{wf}"})
    steps = pg.select("ag_traces", {"select": "id,span_id,context,ingest_door,agent,step_type", "workflow_id": f"eq.{wf}"})
    evals = pg.select("ag_evals", {"select": "id,session_id,eval_name,agent,passed,layer,detail", "workflow_id": f"eq.{wf}"})
    ext_of = {s["id"]: s["external_id"] for s in sess}
    planned_ctx = sum(1 for r in truth for s in r["steps"] if s["manifest_sent"])
    db = {"sessions": len(sess), "steps": len(steps), "spans": len({s["span_id"] for s in steps if s["span_id"]}),
          "ctx": sum(1 for s in steps if s["context"]), "expected_ctx": planned_ctx,
          "doors": sorted({s["ingest_door"] for s in steps if s["ingest_door"]}) and sorted({s["ingest_door"] for s in steps if s["ingest_door"]})[0],
          "door_tag": {exp["door"]: DOOR_TAG[exp["door"]]}}
    return db, evals, ext_of, {s["id"]: s for s in steps}


def tenant_checks(rows: A.Rows, key: str, exp: dict, creds: X.Creds, pg: X.Pg, adm: X.Admin | None) -> None:
    tid = creds.tenant_id(key)
    today = date.today()
    order = exp.get("order")
    ws = None
    if adm is not None:
        st, ws = adm.get(f"/api/admin/entitlements/workspace?workspaceId={tid}")
        if st != 200:
            ws = None
    if order:
        plan = order["plan"]
        o = (ws or {}).get("order") or {}
        if plan["mode"] == "fleet":
            rows.add(key, "-", "order: pricing mode", "fleet", o.get("pricingMode") if ws else None)
            rows.add(key, "-", "order: tier", plan["tier"], o.get("tier") if ws else None)
            rows.add(key, "-", "order: steps included for the whole fleet", plan["included_steps"], o.get("fleetIncludedSteps") if ws else None)
            rows.add(key, "-", "order: fee (staff only)", plan["fee_cents"], o.get("fleetFeeCents") if ws else None)
        else:
            o_ = order["spec"]
            rows.add(key, "-", "order: per agent, no fleet tier", "no tier", ("no tier" if not o.get("tier") else o.get("tier")) if ws else None)
            rows.add(key, "-", "order: seats", o_["included_agents"], o.get("seats") if ws else None)
            rows.add(key, "-", "order: steps per agent", o_["steps_per_agent"], o.get("stepsPerAgent") if ws else None)
            rows.add(key, "-", "order: price per agent (staff only, cents)", int(float(o_["price_per_agent"]) * 100), o.get("priceCents") if ws else None)
        if order.get("discount_percent"):
            lines = o.get("discountLines") or []
            rows.add(key, "-", "order: discount percent", order["discount_percent"], lines[0].get("percent") if lines else None)
        pilot_rows = order["order_rows"]
        if any(r.get("pilot_ends_on") for r in pilot_rows):
            want = X.pilot_state(pilot_rows, today)
            got = (ws or {}).get("pilot") or {}
            words = {"running": "in_pilot", "ended": "ended", "converted": "none", "none": "none"}      # the staff view's own words for the state
            rows.add(key, "-", f"pilot state today ({today}): {want['state']}", words[want["state"]], got.get("state") if ws else None)
            if want["state"] in ("running", "ended"):
                rows.add(key, "-", "pilot end date", want["endsOn"], got.get("endsOn") if ws else None)
            if want["state"] == "running":
                kind = (got.get("end") or {}).get("kind") if ws else None
                rows.add(key, "-", "pilot ending notice shown (within 14 days)", want["mark"] is not None, (kind == "ending") if ws else None)
                if want["mark"] is not None:
                    rows.add(key, "-", "pilot days left in the staff headline", f"{want['daysLeft']} days left.", ((got.get("end") or {}).get("headline", "").split(": ")[-1]) if ws else None)
    else:
        rows.add(key, "-", "no order on file", True, (ws or {}).get("order") is None if ws else None)
    notices = exp.get("notices")
    if notices:
        named = [x.lower() for x in (((ws or {}).get("notices") or {}).get("named") or [])]
        rows.add(key, "-", "notices go to the added recipients", sorted(p.lower() for p in notices["people"]), sorted(named) if ws else None)
    if exp.get("spend_ceiling_usd"):
        c = (ws or {}).get("ceiling") or {}
        rows.add(key, "-", "upper ceiling raised for this workspace (USD a day)", exp["spend_ceiling_usd"], c.get("upperUsd") if ws else None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--creds", required=True)
    ap.add_argument("--only")
    ap.add_argument("--out")
    a = ap.parse_args()
    spec = X.load_spec()
    creds = X.Creds(a.creds)
    pg = X.Pg()
    try:
        adm = X.Admin()
    except X.Refused as e:
        print("staff routes not reachable, order and pilot checks will read as no-data:", e)
        adm = None
    keys = [k.strip() for k in a.only.split(",")] if a.only else [t["key"] for t in spec["tenants"]]
    obs = observe(a.creds, ",".join(keys) if a.only else None)
    rows = A.Rows()
    for t in spec["tenants"]:
        if t["key"] not in keys:
            continue
        exp = json.load(open(os.path.join(P.TRUTH_DIR, t["key"], "expect.json")))
        for f in t["fleets"]:
            fe = exp["fleets"][f["key"]]
            truth = load_truth(os.path.join(os.path.dirname(os.path.dirname(P.TRUTH_DIR)), fe["truth_file"]))
            wf = creds.workflow_id(t["key"], f["key"])
            o = obs.get(t["key"], {}).get(f["key"], {})
            db, evals, ext_of, _ = db_view(pg, wf, fe, truth)
            A.compare_counts(rows, t["key"], f["key"], fe, db)
            A.compare_readiness(rows, t["key"], f["key"], fe, o.get("readiness"))
            A.compare_outcomes(rows, t["key"], f["key"], fe, o.get("outcomes"))
            A.compare_calibration(rows, t["key"], f["key"], fe, o.get("calibration"))
            A.compare_unmeasurable(rows, t["key"], f["key"], fe, o.get("outcomes"))
            cfg = pg.select("ag_pipeline_config", {"select": "key,value", "workflow_id": f"eq.{wf}", "key": "eq.context_declarations"})
            A.compare_declarations(rows, t["key"], f["key"], fe, sorted((cfg[0]["value"] or {}).keys()) if cfg else [])
            A.compare_declared(rows, t["key"], f["key"], fe, evals, ext_of)
            A.compare_phases(rows, t["key"], f["key"], fe, evals, ext_of)
            A.compare_faults(rows, t["key"], f["key"], fe, truth, evals, ext_of)
            A.compare_instruction(rows, t["key"], f["key"], fe, evals, ext_of)
            A.compare_no_context(rows, t["key"], f["key"], fe, evals, ext_of)
        tenant_checks(rows, t["key"], exp, creds, pg, adm)
    summary = {"summary": rows.summary(), "by_tenant": rows.by_tenant(), "at": datetime.now(timezone.utc).isoformat(), "rows": rows.rows}
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        json.dump(summary, open(a.out, "w"), indent=1, default=str)
    for r in rows.rows:
        if r["verdict"] != A.PASS:
            print(f"{r['verdict']:8} {r['tenant']}/{r['fleet']}: {r['check']}: expected {json.dumps(r['expected'], default=str)[:160]} observed {json.dumps(r['observed'], default=str)[:160]}")
    print("SUMMARY", json.dumps(rows.summary()), json.dumps(rows.by_tenant()))
    return 0 if not rows.summary()[A.FAIL] and not rows.summary()[A.NODATA] else 1


if __name__ == "__main__":
    sys.exit(main())
