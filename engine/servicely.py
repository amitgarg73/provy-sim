"""Servicely REST client: the second system of record the ITSM-shaped pack can run against.

Mirrors engine/servicenow.py on purpose. The incidents are real records in a real Servicely instance,
the outcome comes from the platform's own lifecycle, and there is NO offline fallback that invents
tickets: unconfigured means a loud error.

Credentials come from the environment, filled by `scripts/with-secrets` in the argus repo:
  SERVICELY_INSTANCE_URL   https://<your-instance>.servicely.ai/
  SERVICELY_API_TOKEN      an inbound System API token (Bearer). The passkey login cannot use basic auth.

WHAT THE INSTANCE ALLOWS (probed 15 Oct 2026, see servicely/DESIGN.md):
  - POST /v1/Incident works.
  - WorkflowStatus is "System managed field": PATCH is refused with 422, so a state change cannot be
    sent as a record update. `transition()` therefore raises until Servicely tells us the supported
    way (a server script behind an inbound webhook or a system controller). The pack must not
    pretend it can resolve a ticket from here.
  - A resolve then a reopen leaves ResolutionCount = 2 on the record and one WorkflowHistory row per
    transition. Filtering WorkflowHistory by RelatedTable returns 404 on this instance, so history is
    read newest-first and filtered here.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

DEFAULT_MARKER = "[provy-servicely]"


def _marker_from_env(environ=None) -> str:
    """The prefix written on every short description this desk raises, so the generator and the reporting find
    exactly their own records. Servicely has no correlation_id we have confirmed, so the marker lives in text.
    PROVY_SERVICELY_MARKER may name a second isolated desk and must look like [provy-servicely-<name>]."""
    v = ((environ if environ is not None else os.environ).get("PROVY_SERVICELY_MARKER") or "").strip()
    if not v:
        return DEFAULT_MARKER
    if not re.fullmatch(r"\[provy-servicely-[a-z0-9][a-z0-9-]{0,30}\]", v):
        raise ValueError("PROVY_SERVICELY_MARKER must look like [provy-servicely-<name>] (lowercase letters, digits, hyphens)")
    return v


MARKER = _marker_from_env()

# Workflow transition ids on the Incident workflow of the demo instance, read from WorkflowTransition.
# They are instance data: another instance has other ids. Kept here only so a recorded transition can be named.
TRANSITION_NAMES = {
    "c0a8016c68101bc98168115a1f5c00c5": "start_work_from_new",
    "c0a8016c68101bc98168115bb5f400ca": "resolve_from_new",
    "c0a8016c68101bc98168115c056100ce": "close_from_resolved",
    "c0a8016c68101bc98168115c7b3e00d2": "resolve_from_in_progress",
    "c0a8016c68101bc98168115db86200e6": "continue_work_from_resolved",
}


class ServicelyError(RuntimeError):
    pass


class ServicelyClient:
    """Thin JSON REST wrapper. Bearer token, no SDK."""

    def __init__(self, instance: str | None = None, token: str | None = None,
                 timeout: int = 30, min_interval_s: float = 0.2):
        self.instance = (instance or os.environ.get("SERVICELY_INSTANCE_URL", "")).strip().rstrip("/")
        if self.instance and not self.instance.startswith("http"):
            self.instance = f"https://{self.instance}"
        self._token = token if token is not None else os.environ.get("SERVICELY_API_TOKEN", "")
        self.timeout = timeout
        self.min_interval_s = min_interval_s
        self._last_call = 0.0
        self.calls = 0

    def __repr__(self) -> str:  # never print the token
        return f"ServicelyClient(instance={self.instance!r}, configured={self.configured})"

    @property
    def configured(self) -> bool:
        return bool(self.instance and self._token)

    def require(self) -> None:
        if not self.configured:
            raise ServicelyError(
                "Servicely is not configured. Set SERVICELY_INSTANCE_URL and SERVICELY_API_TOKEN "
                "(scripts/with-secrets in the argus repo does it). There is no offline mode on purpose: "
                "the outcomes must come from a system the simulation does not own.")

    def _throttle(self) -> None:
        gap = time.monotonic() - self._last_call
        if gap < self.min_interval_s:
            time.sleep(self.min_interval_s - gap)
        self._last_call = time.monotonic()

    def _call(self, method: str, path: str, payload: Optional[dict] = None, attempts: int = 3) -> Any:
        """One call. Retries 429 and 5xx only; a 401, 403 or 422 is a defect and fails at once."""
        self.require()
        last: Exception | None = None
        for attempt in range(attempts):
            self._throttle()
            self.calls += 1
            req = urllib.request.Request(
                f"{self.instance}{path}",
                data=json.dumps(payload).encode() if payload is not None else None,
                headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json",
                         "Content-Type": "application/json"},
                method=method,
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read().decode()
                return json.loads(body).get("data") if body else None
            except urllib.error.HTTPError as e:
                detail = e.read().decode()[:400]
                # ⛔ AN EMPTY RESULT IS A 404 ON THIS API ({"data": []}), not an error. Read as a failure it
                # hides "no such row" behind an exception, and a caller that swallows it reads "nothing exists".
                if e.code == 404 and method == "GET":
                    try:
                        if json.loads(detail).get("data") == []:
                            return None
                    except ValueError:
                        pass
                err = ServicelyError(f"{method} {path} -> HTTP {e.code}: {detail}")
                if e.code not in (429, 500, 502, 503, 504):
                    raise err from None
                last = err
            except Exception as e:
                last = ServicelyError(f"{method} {path} -> {e}")
            if attempt < attempts - 1:
                time.sleep(2 ** attempt)
        raise last  # type: ignore[misc]

    # ── table operations ────────────────────────────────────────────────────
    def query(self, table: str, params: Optional[dict] = None, page_size: int = 100) -> list[dict]:
        q = {"page_size": str(page_size)}
        q.update({k: str(v) for k, v in (params or {}).items()})
        return self._call("GET", f"/v1/{table}?{urllib.parse.urlencode(q)}") or []

    def create(self, table: str, payload: dict) -> dict:
        return self._call("POST", f"/v1/{table}", payload) or {}

    def update(self, table: str, record_id: str, payload: dict) -> dict:
        return self._call("PATCH", f"/v1/{table}/{record_id}", payload) or {}

    def delete(self, table: str, record_id: str) -> None:
        self._call("DELETE", f"/v1/{table}/{record_id}")

    def create_incident(self, short_description: str, requestor_id: str, requested_for_id: str,
                        classification_id: str, assignment_group_id: str,
                        description: str = "", **extra: Any) -> dict:
        body = {
            "ShortDescription": f"{MARKER} {short_description}",
            "Requestor": requestor_id,
            "RequestedFor": requested_for_id,
            "Classification": classification_id,
            "AssignmentGroup": assignment_group_id,
        }
        if description:
            body["Description"] = description
        body.update(extra)
        return self._call("POST", "/v1/Incident", body) or {}

    def get_incident(self, incident_id: str, fields: Optional[list[str]] = None) -> dict:
        path = f"/v1/Incident/{incident_id}"
        if fields:
            path += "?" + urllib.parse.urlencode({"fields": ",".join(fields)})
        return self._call("GET", path) or {}

    def demo_incidents(self, limit: int = 50) -> list[dict]:
        """Incidents this desk raised: marker prefix on the short description, newest first."""
        rows = self.query("Incident", {"order_desc": "CreatedOn"}, page_size=max(limit, 100))
        return [r for r in rows if str(r.get("ShortDescription", "")).startswith(MARKER)][:limit]

    def workflow_history(self, incident_id: str, scan: int = 200) -> list[dict]:
        """Transition rows for one incident, oldest first. The RelatedTable filter 404s on this instance, so the
        newest `scan` rows are read and filtered here. RelatedTable is `<record id>:Incident`."""
        rows = self.query("WorkflowHistory", {"order_desc": "CreatedOn"}, page_size=scan)
        mine = [r for r in rows if str(r.get("RelatedTable", "")).startswith(f"{incident_id}:")]
        return sorted(mine, key=lambda r: r.get("CreatedOn", ""))

    def transition(self, incident_id: str, name: str) -> None:
        """NOT SUPPORTED OVER REST. WorkflowStatus is system managed and PATCH returns 422. Left raising so no
        pack can silently skip a state change. Replace once Servicely names the supported route."""
        raise ServicelyError(
            "Servicely refuses a state change over the record API (WorkflowStatus is system managed). "
            "A state change needs a server script behind an inbound webhook or a system controller; "
            "see servicely/DESIGN.md, open question 1.")


def settled_facts(incident: dict, history: list[dict]) -> dict:
    """The facts a closed or resolved ticket settles, built only from the record and its history.

    reopened is derived two ways on purpose and they must agree: ResolutionCount above 1 (the platform's own
    counter) and a Resolved -> Work in progress transition in the history. A disagreement is returned as
    `counter_disagrees` rather than resolved quietly."""
    res_count = int(incident.get("ResolutionCount") or 0)
    ws = incident.get("WorkflowStatus")
    status = ws.get("value") if isinstance(ws, dict) else ws
    reopen_transitions = sum(
        1 for r in history if TRANSITION_NAMES.get(r.get("Transition", "")) == "continue_work_from_resolved")
    reopened_by_counter = res_count > 1
    reopened_by_history = reopen_transitions > 0
    return {
        "entity_id": incident.get("Number"),
        "status": status,
        "resolution_count": res_count,
        "reopen_transitions": reopen_transitions,
        "reopened": reopened_by_counter or reopened_by_history,
        "counter_disagrees": reopened_by_counter != reopened_by_history,
        "completed_at": incident.get("CompletedAt"),
        "closed": bool(incident.get("Closed")),
    }


def client_from_env() -> ServicelyClient:
    return ServicelyClient()
