"""A client for the simulated estate on the other vendor's instance (the PDI's Scripted REST door), and for the settings that drive it.

Reads nothing but the estate's own test door and a handful of `provy.iam.*` properties. Never prints or returns a credential. The door's address and
read token are discovered, not configured, so a rebuilt instance needs no edit here.
"""
from __future__ import annotations

import base64
import json
import datetime
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional

Http = Callable[[str, str, dict, Optional[dict]], tuple[int, Any]]


class EstateError(RuntimeError):
    pass


def _urllib(method: str, url: str, headers: dict, body: Optional[dict]) -> tuple[int, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode() or "null"
            return r.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


class Estate:
    DOOR_NAME = "Provy IAM Readings (POC)"

    def __init__(self, instance: str, user: str, password: str, http: Http = _urllib):
        inst = instance.strip()
        self.base = (inst if inst.startswith("http") else "https://" + inst).rstrip("/")
        self._basic = {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode(), "Accept": "application/json"}
        self._http = http
        self._door: Optional[str] = None
        self._token: Optional[str] = None

    def __repr__(self) -> str:
        return f"Estate({self.base})"

    # --- the instance's own table API (admin) -------------------------------------------------
    def _table(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        status, data = self._http(method, self.base + path, {**self._basic, "Content-Type": "application/json"}, body)
        if status >= 400 or not isinstance(data, dict):
            raise EstateError(f"{method} {path.split('?')[0]} answered {status}")
        return data.get("result")

    def _prop_row(self, name: str) -> dict:
        rows = self._table("GET", "/api/now/table/sys_properties?sysparm_fields=sys_id,value&sysparm_query=" + urllib.parse.quote(f"name={name}"))
        if not rows:
            raise EstateError(f"property {name} not found: run pdi/setup_pdi_estate.py")
        return rows[0]

    def get_property(self, name: str) -> str:
        return self._prop_row(name)["value"] or ""

    def set_property(self, name: str, value: str) -> None:
        self._table("PATCH", f"/api/now/table/sys_properties/{self._prop_row(name)['sys_id']}", {"value": value})

    def set_job_active(self, name: str, active: bool) -> None:
        rows = self._table("GET", "/api/now/table/sysauto_script?sysparm_fields=sys_id&sysparm_query=" + urllib.parse.quote(f"name={name}"))
        if not rows:
            raise EstateError(f"scheduled job {name} not found: run pdi/setup_pdi_estate.py")
        self._table("PATCH", f"/api/now/table/sysauto_script/{rows[0]['sys_id']}", {"active": "true" if active else "false"})

    def job_active(self, name: str) -> bool:
        rows = self._table("GET", "/api/now/table/sysauto_script?sysparm_fields=active&sysparm_query=" + urllib.parse.quote(f"name={name}"))
        return bool(rows) and str(rows[0]["active"]).lower() == "true"

    def queue_resync(self, person: str) -> None:
        """A due 'reprovision' event in the HR feed: what the sync job acts on. Due now, so the next run of the job picks it up."""
        due = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self._table("POST", "/api/now/table/u_provy_iam_hr_event", {"u_person": person, "u_action": "reprovision", "u_due": due, "u_done": "false"})

    def clear_hr_events(self, person: str) -> None:
        """Marks every pending event for the person done, so a stale one can never fire in a later run."""
        q = urllib.parse.quote(f"u_person={person}^u_done=false")
        for row in self._table("GET", f"/api/now/table/u_provy_iam_hr_event?sysparm_fields=sys_id&sysparm_query={q}") or []:
            self._table("PATCH", f"/api/now/table/u_provy_iam_hr_event/{row['sys_id']}", {"u_done": "true"})

    # --- the estate's test door ---------------------------------------------------------------
    def _door_base(self) -> str:
        if self._door is None:
            rows = self._table("GET", "/api/now/table/sys_ws_definition?sysparm_fields=base_uri&sysparm_query=" + urllib.parse.quote(f"name={self.DOOR_NAME}"))
            if not rows:
                raise EstateError("the readings door is not installed: run pdi/setup_pdi_estate.py")
            self._door = rows[0]["base_uri"] if rows[0]["base_uri"].startswith("http") else self.base + rows[0]["base_uri"]
        return self._door.rstrip("/")

    def _call(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        if self._token is None:
            self._token = self.get_property("provy.iam.read_token")
        headers = {"Accept": "application/json", "Content-Type": "application/json", "Authorization": "Bearer " + self._token}
        status, data = self._http(method, self._door_base() + path, headers, body)
        if status >= 400 or not isinstance(data, dict):
            raise EstateError(f"{method} {path.split('?')[0]} answered {status}")
        return data.get("result")

    def seed(self, person: str) -> dict:
        return self._call("POST", "/seed", {"person": person})

    def readings(self, person: str) -> dict:
        return self._call("GET", f"/readings?key={urllib.parse.quote(person)}")

    def apply(self, command: str, params: dict) -> dict:
        return self._call("POST", "/apply", {"command": command, "params": params})

    def agent_apply(self, command: str, params: dict, queue_item: Optional[str] = None) -> dict:
        """One task through the agentic path (plan, validate, apply, verify, trace). The door answers 409 while provy.iam.agentic is not true."""
        return self._call("POST", "/agent-apply", {"command": command, "params": params, "queue_item": queue_item})

    def plan_check(self, vectors: list) -> dict:
        """Runs shared test vectors against the DEPLOYED ProvyIamPlan. No estate is read or changed."""
        return self._call("POST", "/plan-check", {"vectors": vectors})

    def set_agentic(self, on: bool) -> None:
        self.set_property("provy.iam.agentic", "true" if on else "false")

    def set_fault(self, fault: dict) -> None:
        self.set_property("provy.iam.fault", json.dumps(fault))
