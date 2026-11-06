"""Opens the Request an agent on the other platform will pick up. The only place that knows the agent platform's table and field names.

Copies who-asked, the classification and the assignment group from the first existing POC Request ("[provy-iam-poc]"), so a rebuilt instance needs
one seed Request made by hand and nothing here to edit.
"""
from __future__ import annotations

import datetime
from typing import Any

MARKER = "[provy-iam-poc]"
TABLE = "ITSMRequest"
PERSONA = "Provy IAM POC: Access orchestrator"


def agent_user_id(client: Any) -> str:
    """The agent user paired with the orchestrator persona. A Request assigned to it is the one the agent works; an unassigned one sits at "new" for ever."""
    persona = next((p for p in client.query("SystemAgentPersona") if p.get("Name") == PERSONA), None)
    if not persona:
        raise RuntimeError(f"persona {PERSONA!r} not found: run servicely/setup_personas.py")
    user = next((u for u in client.query("User") if u.get("AgentPersona") == persona["id"]), None)
    if not user:
        raise RuntimeError(f"no agent user is paired with {PERSONA!r}: create one by hand (servicely/PERSONAS.md, step 4)")
    return user["id"]


def make_open_request(client: Any, person: str, scenario_key: str, systems: tuple = ("directory", "sso", "vpn", "saas_token")):
    def open_request() -> str:
        rows = [r for r in client.query(TABLE) if str(r.get("ShortDescription", "")).startswith(MARKER)]
        if not rows:
            raise RuntimeError(f"no existing {MARKER} Request to copy ids from: create one by hand first (see README)")
        t = rows[0]
        body = {"ShortDescription": f"{MARKER} Leaver ({scenario_key}): disable account and remove access for {person}",
                "Requestor": t["Requestor"], "RequestedFor": t["RequestedFor"], "AssignmentGroup": t["AssignmentGroup"],
                "Assignee": agent_user_id(client),
                # what the HR feed would carry: who, what kind of change, from when, and which systems it has to reach
                "Description": f"Kind: leaver\nPerson: {person}\nEffective: {datetime.date.today().isoformat()}\nSystems: {', '.join(systems)}"}
        if t.get("Classification"):
            body["Classification"] = t["Classification"]
        made = client.create(TABLE, body)
        return str(made.get("Number") or made.get("id") or "created")
    return open_request


DESK_MARKER = "[provy-servicely]"
DESK_PERSONA = "Service Desk Agent"


def desk_user_id(client: Any) -> str:
    persona = next((p for p in client.query("SystemAgentPersona") if p.get("Name") == DESK_PERSONA), None)
    user = next((u for u in client.query("User") if persona and u.get("AgentPersona") == persona["id"]), None)
    if not user:
        raise RuntimeError(f"no agent user is paired with the {DESK_PERSONA!r} persona")
    return user["id"]


def make_open_desk_request(client: Any, person: str, scenario_key: str, systems: tuple = ("directory", "sso", "vpn", "saas_token")):
    """The FRONT DOOR: a plain request, worded as an HR feed or a manager would word it, assigned to the service desk's agent. It names no Access agent and carries no
    [provy-iam-poc] marker: the desk has to recognise an access change and raise the subtask itself. Copies who-asked and the group from the first POC request."""
    def open_request() -> str:
        rows = [r for r in client.query(TABLE) if str(r.get("ShortDescription", "")).startswith(MARKER)]
        if not rows:
            raise RuntimeError(f"no existing {MARKER} Request to copy ids from: create one by hand (see README)")
        t = rows[0]
        body = {"ShortDescription": f"{DESK_MARKER} Please offboard {person} ({scenario_key})",
                "Requestor": t["Requestor"], "RequestedFor": t["RequestedFor"], "AssignmentGroup": t["AssignmentGroup"], "Assignee": desk_user_id(client),
                "Description": f"Leaver: please disable the account and remove all access for {person}, effective {datetime.date.today().isoformat()}.\nSystems: {', '.join(systems)}"}
        if t.get("Classification"):
            body["Classification"] = t["Classification"]
        made = client.create(TABLE, body)
        made = made.get("data") or made
        return str(made.get("Number") or made.get("id") or "created")
    return open_request
