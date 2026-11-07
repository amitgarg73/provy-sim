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


def seed_request(client: Any) -> dict:
    """The request whose who-asked, classification and group the new ones copy: the first "[provy-iam-poc]" one, else any existing request that carries a requestor and a group."""
    rows = list(client.query(TABLE))
    pick = next((r for r in rows if str(r.get("ShortDescription", "")).startswith(MARKER)), None) \
        or next((r for r in rows if r.get("Requestor") and r.get("RequestedFor") and r.get("AssignmentGroup")), None)
    if not pick:
        raise RuntimeError(f"no existing {TABLE} to copy who-asked and the group from: raise one request by hand (see README)")
    return pick


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
        t = seed_request(client)
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


# How different people ask for the same thing. The wording never names a scenario: the audience must not be able to read the answer off the ticket.
# Every variant names the person by user name (the desk agent only acts on a provy.iam.* name) and says "leaver", so the desk recognises it.
DESK_WORDINGS = (
    ("Leaver: {person}, last day {date}", "Leaver: please disable the account and remove all access for {person}, effective {date}.\nSystems: {systems}"),
    ("Please offboard {person}", "Hi, {person} is leaving the company and today is their last day. Can you remove their access everywhere? {date}.\nSystems: {systems}"),
    ("Access removal for leaver {person}", "HR notification: {person} has left. Effective {date}. Please remove access to the following: {systems}."),
    ("{person} has resigned, please remove access", "{person} handed in their notice and finishes on {date}. Please disable their account and take away all access ({systems})."),
    ("Termination: remove access for {person}", "Termination effective {date} for {person}. Disable the account, groups and tokens. Systems: {systems}."),
    ("Offboarding request - {person}", "Please start the offboarding for {person}, leaving on {date}. We need the account disabled and access removed from {systems}."),
)


# A time-limited grant that has run out. It goes through the same desk, with the one system name "temporary_access", which the instance's worker turns into the expiry sweep.
DESK_TEMPORARY_WORDINGS = (
    ("Temporary access for {person} has ended", "The temporary access we gave {person} for the incident has run out on {date}. Please remove it.\nSystems: temporary_access"),
    ("Please remove the time-limited access for {person}", "{person} was given time-limited access that expired on {date}. Can you take it away now?\nSystems: temporary_access"),
    ("Expired temporary access - {person}", "Access review: {person} still has temporary access past its end date ({date}). Please revoke it.\nSystems: temporary_access"),
    ("{person}: end the temporary access", "The work that {person} needed the temporary access for is finished as of {date}. Please end that access.\nSystems: temporary_access"),
)


def desk_wording(person: str, systems: tuple, today: str, n: int = 0, kind: str = "leaver") -> tuple:
    """(subject, description) for the n-th request, stable for the same inputs so a seeded batch reads the same every time."""
    pool = DESK_TEMPORARY_WORDINGS if kind == "jit" else DESK_WORDINGS
    subject, description = pool[n % len(pool)]
    fill = {"person": person, "date": today, "systems": ", ".join(systems)}
    return f"{DESK_MARKER} " + subject.format(**fill), description.format(**fill)


def make_open_desk_request(client: Any, person: str, scenario_key: str, systems: tuple = ("directory", "sso", "vpn", "saas_token"), n: int = 0, kind: str = "leaver"):
    """The FRONT DOOR: a plain request, worded as an HR feed, a manager or the leaver's colleague would word it, assigned to the service desk's agent. It names no Access agent,
    carries no [provy-iam-poc] marker and no scenario key: the desk has to recognise an access change and raise the subtask itself. Copies who-asked and the group from the
    first POC request. `n` picks the wording."""
    def open_request() -> str:
        t = seed_request(client)
        subject, description = desk_wording(person, systems, datetime.date.today().isoformat(), n, kind)
        body = {"ShortDescription": subject,
                "Requestor": t["Requestor"], "RequestedFor": t["RequestedFor"], "AssignmentGroup": t["AssignmentGroup"], "Assignee": desk_user_id(client),
                "Description": description}
        if t.get("Classification"):
            body["Classification"] = t["Classification"]
        made = client.create(TABLE, body)
        made = made.get("data") or made
        return str(made.get("Number") or made.get("id") or "created")
    return open_request
