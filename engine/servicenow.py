"""ServiceNow Table API client — the ITSM pack's system of record.

This is the one place the simulation talks to a system it does not own. Every
other pack invents its work items AND their outcomes, which is the circularity a
sharp buyer attacks first. Here the incidents are real records in a real
ServiceNow instance, the agent's actions are real writes, and the outcome comes
back from ServiceNow's own lifecycle.

Credentials come from the environment (SERVICENOW_INSTANCE / SERVICENOW_USER /
SERVICENOW_PASSWORD), the same three names the keep-alive workflow uses. There is
deliberately NO offline fallback that invents incidents: a pack that quietly
synthesised its own tickets when the instance was unreachable would look like it
worked and would prove nothing. Unconfigured means a loud error.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

# Marker written to correlation_id on every incident this demo creates, so the
# generator, the agent and the reporting can all find exactly their own records
# and leave the instance's pre-seeded filler alone.
MARKER = "provy-itsm"

# The instance's real vocabulary, read off dev217748 on 2026-07-27. Everything
# the agent writes has to come from these lists or the record will not reconcile.
CATEGORIES = ["inquiry", "software", "hardware", "network", "database", "password_reset"]

SUBCATEGORIES = {
    "network": ["vpn", "dns", "wireless", "dhcp", "ip address"],
    "software": ["os", "email"],
    "hardware": ["disk", "monitor", "cpu", "keyboard", "memory", "mouse"],
    "database": ["db2", "oracle", "sql server"],
    "inquiry": ["antivirus", "email", "internal application"],
    "password_reset": [],
}

# category -> the group that should own it. Objective, so "misrouted" is a fact
# about the record rather than an opinion: the ticket ended up somewhere else.
CORRECT_GROUP = {
    "network": "Network",
    "database": "Database",
    "software": "Software",
    "hardware": "Hardware",
    "inquiry": "Service Desk",
    "password_reset": "Service Desk",
}

CONTACT_TYPES = ["phone", "self-service", "email", "walk-in"]

# All ten are active choices, so a new ticket can be closed with any of them.
GENUINE_FIX_CODES = [
    "Solution provided",
    "Resolved by change",
    "Resolved by problem",
    "Workaround provided",
]
NON_FIX_CODES = [
    "No resolution provided",
    "Duplicate",
    "Resolved by caller",
    "User error",
    "Known error",
    "Resolved by request",
]

STATE_NEW = "1"
STATE_IN_PROGRESS = "2"
STATE_ON_HOLD = "3"
STATE_RESOLVED = "6"
STATE_CLOSED = "7"


class ServiceNowError(RuntimeError):
    pass


class ServiceNowClient:
    """Thin Table API wrapper. Basic auth, no SDK, no framework lock-in."""

    def __init__(self, instance: str | None = None, user: str | None = None,
                 password: str | None = None, timeout: int = 30,
                 min_interval_s: float = 0.2):
        self.instance = (instance or os.environ.get("SERVICENOW_INSTANCE", "")).strip().rstrip("/")
        if self.instance and not self.instance.startswith("http"):
            self.instance = f"https://{self.instance}"
        self.user = user if user is not None else os.environ.get("SERVICENOW_USER", "")
        self.password = password if password is not None else os.environ.get("SERVICENOW_PASSWORD", "")
        self.timeout = timeout
        # A PDI's binding constraint is its rate limit, not money. Space the calls.
        self.min_interval_s = min_interval_s
        self._last_call = 0.0
        self.calls = 0
        self._group_ids: dict[str, str] = {}
        self._group_names: dict[str, str] = {}

    @property
    def configured(self) -> bool:
        return bool(self.instance and self.user and self.password)

    def require(self) -> None:
        if not self.configured:
            raise ServiceNowError(
                "ServiceNow is not configured. Set SERVICENOW_INSTANCE, SERVICENOW_USER and "
                "SERVICENOW_PASSWORD. The ITSM pack has no offline mode on purpose: its whole "
                "point is that the outcomes come from a system the simulation does not own."
            )

    # ── low level ───────────────────────────────────────────────────────────
    def _headers(self) -> dict:
        token = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        return {
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last_call
        if gap < self.min_interval_s:
            time.sleep(self.min_interval_s - gap)
        self._last_call = time.monotonic()

    def _call(self, method: str, path: str, payload: Optional[dict] = None,
              attempts: int = 3) -> Any:
        """One Table API call, retrying only what is worth retrying.

        A PDI's rate limit is the binding constraint on a 500-incident run, and a
        single 429 partway through should not take the batch down. Retries cover
        429 and 5xx; a 401 or a 400 is a real defect and fails immediately.
        """
        self.require()
        last: Exception | None = None
        for attempt in range(attempts):
            self._throttle()
            self.calls += 1
            req = urllib.request.Request(
                f"{self.instance}{path}",
                data=json.dumps(payload).encode() if payload is not None else None,
                headers=self._headers(),
                method=method,
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read().decode()
                return json.loads(body).get("result") if body else None
            except urllib.error.HTTPError as e:
                detail = e.read().decode()[:400]
                err = ServiceNowError(f"{method} {path} -> HTTP {e.code}: {detail}")
                if e.code not in (429, 500, 502, 503, 504):
                    raise err from None
                last = err
            except Exception as e:
                last = ServiceNowError(f"{method} {path} -> {e}")
            if attempt < attempts - 1:
                time.sleep(2 ** attempt)
        raise last  # type: ignore[misc]

    # ── table operations ────────────────────────────────────────────────────
    def query(self, table: str, sysparm_query: str = "", fields: Optional[list[str]] = None,
              limit: int = 100, offset: int = 0) -> list[dict]:
        params = {
            "sysparm_query": sysparm_query,
            "sysparm_limit": str(limit),
            "sysparm_offset": str(offset),
            # RAW values, not display values. Display mode turns state into "In Progress" and
            # made_sla into "Yes", which then have to be un-translated before anything can be
            # compared or graded. Reference fields come back as sys_ids and group_name() resolves
            # the two or three we actually show a human.
            "sysparm_display_value": "false",
            "sysparm_exclude_reference_link": "true",
        }
        if fields:
            params["sysparm_fields"] = ",".join(fields)
        return self._call("GET", f"/api/now/table/{table}?{urllib.parse.urlencode(params)}") or []

    def create(self, table: str, payload: dict) -> dict:
        return self._call("POST", f"/api/now/table/{table}", payload) or {}

    def update(self, table: str, sys_id: str, payload: dict) -> dict:
        params = urllib.parse.urlencode({
            "sysparm_display_value": "false",
            "sysparm_exclude_reference_link": "true",
        })
        return self._call("PATCH", f"/api/now/table/{table}/{sys_id}?{params}", payload) or {}

    # ── incident helpers the pack and the scripts share ─────────────────────
    INCIDENT_FIELDS = [
        "sys_id", "number", "short_description", "description", "category", "subcategory",
        "priority", "impact", "urgency", "state", "assignment_group", "assigned_to",
        "contact_type", "caller_id", "opened_at", "made_sla", "reopen_count",
        "reassignment_count", "close_code", "close_notes", "resolved_at", "correlation_id",
    ]

    def open_demo_incidents(self, limit: int = 10) -> list[dict]:
        """Incidents this demo created that are still waiting to be worked.

        Ordered oldest first so a paced run works the backlog the way a desk does.
        """
        q = f"correlation_id={MARKER}^stateIN{STATE_NEW},{STATE_IN_PROGRESS}^ORDERBYopened_at"
        return self.query("incident", q, self.INCIDENT_FIELDS, limit=limit)

    def group_sys_id(self, name: str) -> str:
        cached = self._group_ids.get(name)
        if cached:
            return cached
        rows = self.query("sys_user_group", f"name={name}", ["sys_id", "name"], limit=1)
        if not rows:
            raise ServiceNowError(f"assignment group '{name}' not found in the instance")
        sys_id = rows[0]["sys_id"]
        self._group_ids[name] = sys_id
        self._group_names[sys_id] = name
        return sys_id

    def group_name(self, sys_id: str) -> str:
        """Resolve a group sys_id back to its name, for anything a human reads."""
        if not sys_id:
            return ""
        cached = self._group_names.get(sys_id)
        if cached:
            return cached
        rows = self.query("sys_user_group", f"sys_id={sys_id}", ["sys_id", "name"], limit=1)
        name = rows[0]["name"] if rows else sys_id
        self._group_names[sys_id] = name
        return name


def client_from_env() -> ServiceNowClient:
    return ServiceNowClient()
