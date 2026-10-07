#!/usr/bin/env python3
"""Tear down sim tenants: R2 before rows, by tenant id, through the product's own delete.

    python scripts/sim_teardown.py H1 [H2 ...] --creds FILE            # lists what would go: rows, stored bodies, R2 objects
    python scripts/sim_teardown.py H1 --creds FILE --apply             # does it (needs the staff key and the R2 names in the environment)

Order, and why (memory project_provy_tenant_teardown): the product's `DELETE /api/admin/tenants` (hardDeleteTenant) empties the tenant's R2
objects FIRST, while ag_traces still names the keys, then deletes the tenant and lets the database cascade. This script then clears the
console's own rows (sim_control_config, sim_control_runs, sim_pending_outcomes), which have no foreign key to the workflow, and proves zero.

⛔ A tenant is only torn down when its NAME starts with the set's prefix and its id is the one in the credentials file. The bucket is
SHARED with production: only `traces/<tenant id>/` is ever listed. The route is called on dev.provy.ai, the host whose deployment holds the R2 names
(a local server started for ingest has them unset, on purpose).
Kept by design (the product keeps them): ag_workspace_orders and ag_audit_log outlive a workspace.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import sim_set as X  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WITH_SECRETS = os.path.expanduser("~/Claude Projects/argus/scripts/with-secrets")


def r2_counts(ids: list[str]) -> dict:
    p = subprocess.run([WITH_SECRETS, "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "--", "node", os.path.join(HERE, "r2_count.mjs"), *ids],
                       capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        raise SystemExit("could not count R2 objects: " + p.stderr[-300:])
    return json.loads(p.stdout.strip().splitlines()[-1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tenants", nargs="+")
    ap.add_argument("--creds", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--spec", help="a spec file other than the persistent set's (config/sim_tenants_itsm.json for the ITSM parity tenant)")
    a = ap.parse_args()
    spec = X.load_spec(a.spec) if a.spec else X.load_spec()
    creds = X.Creds(a.creds)
    pg = X.Pg()
    prefix = spec["name_prefix"]
    targets = []
    for k in a.tenants:
        t = next((t for t in spec["tenants"] if t["key"] == k), None)
        if t is None or not t["name"].startswith(prefix):
            raise SystemExit(f"{k}: not a tenant of this set")
        c = creds.tenant(k)
        row = pg.select("tenants", {"select": "id,name", "id": f"eq.{c['tenant_id']}"})
        if not row or row[0]["name"] != t["name"]:
            print(f"{k}: no such tenant in pre-prod (already gone?)")
            continue
        targets.append((k, t, c))
    ids = [c["tenant_id"] for _, _, c in targets]
    before = r2_counts(ids) if ids else {}
    for k, t, c in targets:
        n = len(pg.select("ag_traces", {"select": "id", "tenant_id": f"eq.{c['tenant_id']}"}))
        print(f"{k} {t['name']} {c['tenant_id']}: stored steps {n}, R2 objects {before.get(c['tenant_id'])}, fleets {len(c['fleets'])}")
    if not a.apply:
        print("listing only. Add --apply to remove.")
        return 0
    adm = X.Admin(base="https://dev.provy.ai")
    for k, t, c in targets:
        r = requests.delete(adm.base + "/api/admin/tenants", headers=adm._h(), data=json.dumps({"id": c["tenant_id"], "name": t["name"], "reason": "Sim set teardown"}), timeout=300)
        print(f"{k}: DELETE tenant -> HTTP {r.status_code} {r.text[:160]}")
        if r.status_code != 200:
            return 1
        for f in c["fleets"].values():
            for table in ("sim_control_config", "sim_control_runs", "sim_pending_outcomes"):
                try:
                    pg.delete(table, {"workflow_id": f["workflow_id"]})
                except Exception as e:                                       # noqa: BLE001
                    print(f"   {table}: {str(e)[:120]}")
    after = r2_counts(ids)
    left = {i: n for i, n in after.items() if n}
    print("R2 objects left:", left or "none")
    for k, t, c in targets:
        n = len(pg.select("tenants", {"select": "id", "id": f"eq.{c['tenant_id']}"}))
        print(f"{k}: tenant rows left {n}")
    return 0 if not left else 1


if __name__ == "__main__":
    sys.exit(main())
