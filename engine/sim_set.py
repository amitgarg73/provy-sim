"""The sim tenant set: spec, credentials, the two clients it writes through, orders and expected state.

Everything here is pre-prod only and prints no secret. A secret is read from the environment by NAME (the caller runs the command under
`argus/scripts/with-secrets`), is held in memory, and goes only to the host it belongs to.

  Pg         PostgREST with the service key (the same write path provy-sim-control's provisionFleet uses). Reads page past the 1000-row cut.
  Admin      the staff routes of dev.provy.ai with the pre-prod staff key (`x-provy-key`), the way scripts/teardown-walk.mjs does.
  Creds      the 0600 credentials file: tenant ids, fleet ids, ingest keys, logins, passwords. It is never printed and never committed.

⛔ PRE-PROD ONLY. Pg refuses a URL that is not the pre-prod project and then asks the database (`app_environment`). Admin refuses any host but
dev.provy.ai and asks the deployment which environment it is. A clock the caller supplies is never ahead of the real one.
"""
from __future__ import annotations

import json
import os
import stat
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import requests

PREPROD_REF = "fpuyabfxtrzwciehfetk"
ADMIN_HOSTS = ("dev.provy.ai", "localhost")
ADMIN_HOST = "dev.provy.ai"
SPEC_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "sim_tenants.json")
TIERS = {"startup": ("Startup", 49900, 50000), "growth": ("Growth", 119900, 150000), "scale": ("Scale", 199900, 300000)}


class Refused(RuntimeError):
    pass


def load_spec(path: str = SPEC_PATH) -> dict:
    return json.load(open(path))


# ── credentials ──────────────────────────────────────────────────────────────────────────────────
class Creds:
    """The credentials file. Mode 0600 is enforced on read and on write; a looser file is refused rather than used."""

    def __init__(self, path: str):
        self.path = path
        if os.path.exists(path):
            mode = stat.S_IMODE(os.stat(path).st_mode)
            if mode & 0o077:
                raise Refused(f"{path} is readable by others (mode {oct(mode)}); fix it with chmod 600 before use")
            self.data = json.load(open(path))
        else:
            self.data = {"tenants": {}}

    def tenant(self, key: str) -> dict:
        t = self.data["tenants"].get(key)
        if not t:
            raise Refused(f"no credentials for tenant {key}: provision it first")
        return t

    def fleet_key(self, tenant: str, fleet: str) -> str:
        return self.tenant(tenant)["fleets"][fleet]["ingest_key"]

    def workflow_id(self, tenant: str, fleet: str) -> str:
        return self.tenant(tenant)["fleets"][fleet]["workflow_id"]

    def tenant_id(self, tenant: str) -> str:
        return self.tenant(tenant)["tenant_id"]


# ── the database, through PostgREST ──────────────────────────────────────────────────────────────
class Pg:
    def __init__(self, url: Optional[str] = None, key: Optional[str] = None):
        self.url = (url or os.environ.get("SUPABASE_URL", "")).rstrip("/")
        self._key = key or os.environ.get("SUPABASE_KEY", "")
        if PREPROD_REF not in self.url:
            raise Refused(f"SUPABASE_URL is not the pre-prod project ({PREPROD_REF}); refusing")
        if not self._key:
            raise Refused("SUPABASE_KEY is not set: run under with-secrets SUPABASE_URL SUPABASE_KEY")
        self.s = requests.Session()
        self.s.headers.update({"apikey": self._key, "Authorization": f"Bearer {self._key}", "Content-Type": "application/json"})
        env = self.select("app_environment", {"select": "name"})
        if not env or env[0].get("name") != "preprod":
            raise Refused("app_environment does not say preprod; refusing")

    def _req(self, method: str, url: str, **kw):
        """One request, retried on a gateway error (502 to 504) or a connection error: PostgREST sits behind a proxy that drops one now and then.
        Only used for calls that are safe to repeat: a read, an upsert, a patch, a delete, an rpc the caller marks idempotent."""
        last = None
        for i in range(4):
            try:
                r = self.s.request(method, url, timeout=120, **kw)
                if r.status_code not in (502, 503, 504):
                    return r
                last = r
            except requests.RequestException as e:                  # noqa: PERF203
                last = e
            time.sleep(2 ** i)
        if isinstance(last, Exception):
            raise last
        return last

    def _u(self, table: str, params: Optional[dict] = None) -> str:
        q = ("?" + urllib.parse.urlencode(params, safe="(),.*")) if params else ""
        return f"{self.url}/rest/v1/{table}{q}"

    def select(self, table: str, params: Optional[dict] = None, page: int = 1000, limit: Optional[int] = None) -> list[dict]:
        """Every row, paged: PostgREST cuts a read at 1000 silently, so a count is never taken from one response."""
        out: list[dict] = []
        start = 0
        while True:
            hi = start + page - 1
            r = self._req("GET", self._u(table, params), headers={"Range-Unit": "items", "Range": f"{start}-{hi}"})
            if r.status_code not in (200, 206):
                raise RuntimeError(f"select {table}: HTTP {r.status_code} {r.text[:200]}")
            rows = r.json()
            out.extend(rows)
            if len(rows) < page or (limit and len(out) >= limit):
                return out[:limit] if limit else out
            start += page

    def insert(self, table: str, rows: Any, upsert_on: Optional[str] = None) -> list[dict]:
        h = {"Prefer": "return=representation" + (",resolution=merge-duplicates" if upsert_on else "")}
        u = self._u(table, {"on_conflict": upsert_on} if upsert_on else None)
        r = self._req("POST", u, data=json.dumps(rows), headers=h) if upsert_on else self.s.post(u, data=json.dumps(rows), headers=h, timeout=120)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"insert {table}: HTTP {r.status_code} {r.text[:300]}")
        return r.json()

    def patch(self, table: str, match: dict, values: dict) -> list[dict]:
        r = self._req("PATCH", self._u(table, {k: f"eq.{v}" for k, v in match.items()}), data=json.dumps(values), headers={"Prefer": "return=representation"})
        if r.status_code != 200:
            raise RuntimeError(f"patch {table}: HTTP {r.status_code} {r.text[:300]}")
        return r.json()

    def delete(self, table: str, match: dict) -> int:
        r = self._req("DELETE", self._u(table, {k: f"eq.{v}" for k, v in match.items()}), headers={"Prefer": "return=representation"})
        if r.status_code != 200:
            raise RuntimeError(f"delete {table}: HTTP {r.status_code} {r.text[:300]}")
        return len(r.json())

    def rpc(self, fn: str, args: dict) -> Any:
        r = self.s.post(f"{self.url}/rest/v1/rpc/{fn}", data=json.dumps(args), timeout=120)
        if r.status_code not in (200, 201, 204):
            raise RuntimeError(f"rpc {fn}: HTTP {r.status_code} {r.text[:300]}")
        return r.json() if r.text else None


# ── the staff routes ─────────────────────────────────────────────────────────────────────────────
class Admin:
    """The staff routes with the pre-prod staff key. Every use of that key is counted and noticed by the product (a break-glass use).

    WHICH SERVER. The deployed pre-prod (dev.provy.ai) can run a build older than provydev: on 7 Oct 2026 it ignored fleet terms on an order
    (stored per agent, no terms) and had no pricing, spend-ceiling or staff-readiness route. The default is therefore a local server on :3100 started
    from the argus checkout at provydev with the pre-prod environment (docs/sim-tenants.md says how). A local server holds the R2 names UNSET on
    purpose, so teardown, which must empty R2, is the one call made against dev.provy.ai."""

    def __init__(self, base: Optional[str] = None, key: Optional[str] = None):
        self.base = (base or os.environ.get("SIM_ADMIN_BASE") or "http://localhost:3100").rstrip("/")
        host = urllib.parse.urlparse(self.base).hostname
        if host not in ADMIN_HOSTS:
            raise Refused(f"staff routes are used on {', '.join(ADMIN_HOSTS)} only; refusing {self.base}")
        self._key = key or os.environ.get("WAITLIST_ADMIN_KEY_PREVIEW", "")
        if not self._key:
            raise Refused("WAITLIST_ADMIN_KEY_PREVIEW is not set: run under with-secrets WAITLIST_ADMIN_KEY_PREVIEW")
        env = requests.get(self.base + "/api/health/env", timeout=60).json().get("environment")
        if env != "preprod":
            raise Refused(f"{self.base} says environment={env!r}, not preprod; refusing")

    def _h(self) -> dict:
        return {"x-provy-key": self._key, "Content-Type": "application/json"}

    def post(self, path: str, body: dict) -> tuple[int, dict]:
        r = requests.post(self.base + path, headers=self._h(), data=json.dumps(body), timeout=120)
        try:
            return r.status_code, r.json()
        except Exception:                                        # noqa: BLE001
            return r.status_code, {"raw": r.text[:300]}

    def get(self, path: str) -> tuple[int, dict]:
        r = requests.get(self.base + path, headers=self._h(), timeout=120)
        try:
            return r.status_code, r.json()
        except Exception:                                        # noqa: BLE001
            return r.status_code, {"raw": r.text[:300]}


# ── orders ───────────────────────────────────────────────────────────────────────────────────────
def day_of(d: date, offset: int) -> str:
    return (d + timedelta(days=offset)).isoformat()


def order_bodies(order: dict, today: date) -> list[tuple[str, dict]]:
    """The staff calls that put a tenant's order on file, in order: (route, body without workspaceId). Dates are absolute and are
    written into the ground truth, so the expected state on any later day is a calculation, not a memory."""
    starts = order.get("starts_on") or day_of(today, order.get("starts_on_offset_days", 0))
    terms: dict[str, Any] = {}
    if order["kind"] == "fleet":
        terms.update({"pricingMode": "fleet", "tier": order["tier"]})
        if order["tier"] == "enterprise":
            terms.update({"fleetFee": order["fleet_fee"], "fleetSteps": order["fleet_steps"]})
    else:
        terms.update({"pricePerAgent": order["price_per_agent"], "stepsPerAgent": order["steps_per_agent"], "overagePer1000": order["overage_per_1000"]})
    if order.get("discount_percent"):
        terms["discountLines"] = [{"kind": "all", "percent": order["discount_percent"], "note": order.get("discount_note", "")}]
    if "pilot_offset_days" in order:
        terms["pilotEndsOn"] = day_of(today, order["pilot_offset_days"])
    body: dict[str, Any] = {"plan": order["plan"], "startsOn": starts, "terms": terms, "reason": order["reason"], "paid": bool(order.get("paid"))}
    if order.get("included_agents"):
        body["includedAgents"] = order["included_agents"]
    calls: list[tuple[str, dict]] = [("/api/admin/orders", body)]
    nxt = order.get("then")
    if nxt:
        t2 = {k: v for k, v in terms.items() if k != "pilotEndsOn"}
        calls.append(("/api/admin/orders", {"plan": nxt["plan"], "startsOn": day_of(today, nxt["starts_on_offset_days"]), "terms": t2, "reason": nxt["reason"], "paid": bool(nxt.get("paid"))}))
    if "extend_to_offset_days" in order:
        calls.append(("/api/admin/entitlements/pilot", {"pilotEndsOn": day_of(today, order["extend_to_offset_days"]), "reason": order["extend_reason"]}))
    return calls


def pilot_state(orders: list[dict], today: date) -> dict:
    """Where a pilot stands on `today`, from the order history (a port of web/lib/entitlement/pilot-end.ts pilotEndOf, written from its
    documented rules). `orders` are rows oldest first with starts_on, pilot_ends_on, paid, has_pricing."""
    if not orders:
        return {"state": "none"}
    in_force = None
    for o in orders:
        if date.fromisoformat(o["starts_on"]) <= today:
            in_force = o
    if in_force is None:
        return {"state": "none"}
    if not in_force.get("pilot_ends_on"):
        former = [o for o in orders if o is not in_force and o.get("pilot_ends_on")]
        return {"state": "converted", "via": "new_order", "endsOn": former[-1]["pilot_ends_on"]} if former else {"state": "none"}
    if not in_force.get("has_pricing", True):
        return {"state": "none"}
    end = date.fromisoformat(in_force["pilot_ends_on"])
    if in_force.get("paid"):
        return {"state": "converted", "via": "paid", "endsOn": in_force["pilot_ends_on"]}
    if end >= today:
        left = (end - today).days
        return {"state": "running", "endsOn": in_force["pilot_ends_on"], "daysLeft": left, "mark": 3 if left <= 3 else 14 if left <= 14 else None}
    return {"state": "ended", "endsOn": in_force["pilot_ends_on"], "daysSince": (today - end).days}


def fleet_plan_words(order: dict) -> dict:
    """What the order says about the plan: tier name and the steps included for the whole fleet (never a price to a customer)."""
    if order["kind"] != "fleet":
        return {"mode": "per_agent", "steps_per_agent": order.get("steps_per_agent")}
    if order["tier"] == "enterprise":
        return {"mode": "fleet", "tier": "enterprise", "name": "Enterprise", "included_steps": order["fleet_steps"], "fee_cents": int(round(float(order["fleet_fee"]) * 100))}
    name, fee, steps = TIERS[order["tier"]]
    return {"mode": "fleet", "tier": order["tier"], "name": name, "included_steps": steps, "fee_cents": fee}


def utc_now_floor(minutes_back: int = 2) -> datetime:
    n = datetime.now(timezone.utc) - timedelta(minutes=minutes_back)
    return n.replace(second=0, microsecond=0)
