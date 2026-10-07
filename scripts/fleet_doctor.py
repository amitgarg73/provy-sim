#!/usr/bin/env python3
"""fleet_doctor — one command that answers "is this fleet actually wired up?".

⛔ WHY THIS EXISTS. On 10 Sep 2026 the ITSM fleet's seed ran green in GitHub Actions, created 23 real
incidents in ServiceNow, and landed NOTHING in Provy. Every emit came back 401 and the job still
exited 0. Diagnosing it took an hour and three separate credential stores, and the cause was that a
PDI rebuild on 9 Sep minted a new ingest key into Provy and updated exactly one of the places that
key is kept.

A FLEET'S IDENTITY LIVES IN FOUR PLACES AND NOTHING MADE THEM AGREE:

  1. Provy            ag_ingest_keys.key_hash        the only authority on what is valid
  2. Sim console      sim_control_config.ingest_key  what a dispatched run authenticates with
  3. ServiceNow       property provy.ingest.key      what the business rule pushes outcomes with
  4. GitHub secrets   SERVICENOW_INSTANCE/USER/PASSWORD on provy-sim, so the runner can reach the PDI

⛔ THE KEY IS HASHED IN PROVY AND CANNOT BE READ BACK, so this compares SHA-256 digests, never
plaintext. Nothing here prints a secret and nothing here writes one.

⛔ A STALE ENTRY IS SILENT IN EVERY ONE OF THEM. Provy answers 401, the console reports a successful
dispatch, ServiceNow logs into a table nobody reads, and the workflow goes green. That is four
independent ways to look like it worked.

Usage (the names come from the environment; the secret door puts them there, #1648):

    cd "$HOME/Claude Projects/argus" && scripts/with-secrets CERTIFY_DB_URL SERVICENOW_INSTANCE \\
        SERVICENOW_USER SERVICENOW_PASSWORD -- python3 <path to>/scripts/fleet_doctor.py [--pack itsm]

⛔ THIS SCRIPT NEVER OPENS A CREDENTIAL FILE. It reads four names from its own environment and nothing else:
CERTIFY_DB_URL (or PROVY_DB_URL, the same pre-prod address under the name engine/scoreboard.py uses),
and, only when an ITSM fleet is among those checked, SERVICENOW_INSTANCE / SERVICENOW_USER /
SERVICENOW_PASSWORD. A name that is missing stops the run with the name and the exact command above, never
a value. A pack other than itsm needs no ServiceNow name at all. The GitHub secrets (place 4) are not
readable by anyone, by design; the runner using the same three SERVICENOW_* names is checked by the login
below, and `gh secret list --repo amitgarg73/provy-sim` shows only that the names exist and when they changed.

Exit code is 1 when any fleet has a mismatch, so it can gate a seed. Exit 2 when a name is missing or the
database address is not the pre-prod project.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

# ⛔ PRE-PROD, ALWAYS. A sim fleet pointed at production writes simulated work into the real ledger,
# which happened on 27 Jul 2026. Anything else here is a finding, not a preference.
EXPECTED_PROVY_HOST = "dev.provy.ai"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _short(digest: str | None) -> str:
    return (digest[:10] + "…") if digest else "—"


PREPROD_REF = "fpuyabfxtrzwciehfetk"
PROD_REF = "eckthcvacrkfjihluubt"

DOOR_DIR = "$HOME/Claude Projects/argus"
DB_NAMES = ("CERTIFY_DB_URL", "PROVY_DB_URL")
SERVICENOW_NAMES = ("SERVICENOW_INSTANCE", "SERVICENOW_USER", "SERVICENOW_PASSWORD")


class MissingSecret(Exception):
    """One or more names are not in the environment. Carries NAMES, never values."""

    def __init__(self, names: list[str]):
        self.names = names
        super().__init__(", ".join(names))


def door_command(names: list[str]) -> str:
    """The exact command that would supply these names. Printed in errors; it holds no value."""
    return (f'cd "{DOOR_DIR}" && scripts/with-secrets {" ".join(names)} -- '
            f'python3 "$HOME/Claude Projects/provy-sim/scripts/fleet_doctor.py"')


def _env(environ, name: str) -> str:
    return (environ.get(name) or "").strip()


def database_url(environ) -> str:
    """The pre-prod connection string from the environment, or MissingSecret / ValueError.

    ⛔ FAILS CLOSED. An address that does not name the pre-prod project is refused, and an address that
    names production is refused loudly: a read-only doctor must never be pointed at the real ledger.
    """
    for name in DB_NAMES:
        url = _env(environ, name)
        if url:
            break
    else:
        raise MissingSecret([DB_NAMES[0]])
    if PROD_REF in url:
        raise ValueError("the database address names the PRODUCTION project; refusing. "
                         "This tool is pre-prod only.")
    if PREPROD_REF not in url:
        raise ValueError(f"the database address does not name the pre-prod project ({PREPROD_REF}); refusing")
    return url


def servicenow_credentials(environ) -> tuple[str, str, str]:
    """(instance URL, user, password) from the environment, or MissingSecret listing every absent name."""
    missing = [n for n in SERVICENOW_NAMES if not _env(environ, n)]
    if missing:
        raise MissingSecret(missing)
    instance = _env(environ, "SERVICENOW_INSTANCE")
    if "://" not in instance:
        instance = "https://" + instance
    return instance, _env(environ, "SERVICENOW_USER"), _env(environ, "SERVICENOW_PASSWORD")


def _db(url: str):
    """Direct Postgres, because the console's own API cannot tell you the console is wrong."""
    import psycopg2
    return psycopg2.connect(url)


def _rows(conn, sql: str, args: tuple = ()) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def servicenow_property(instance: str, user: str, password: str, name: str) -> str | None:
    """One instance property, or None when the instance refuses us.

    ⛔ A 401 HERE IS THE WHOLE POINT. The GitHub secrets went stale on 28 Jul and nothing noticed
    until a seed had already created 23 incidents it could not report on.
    """
    url = (f"{instance.rstrip('/')}/api/now/table/sys_properties"
           f"?sysparm_query=name%3D{name}&sysparm_limit=1&sysparm_fields=name,value")
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {token}",
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.load(r).get("result") or []
    except urllib.error.HTTPError as e:
        return f"__HTTP_{e.code}__"
    except Exception:
        return None
    return result[0]["value"] if result else None


def check(conn, pack: str | None, environ=None, property_reader=None) -> int:
    environ = os.environ if environ is None else environ
    property_reader = property_reader or servicenow_property
    # ⛔ THE PACK NAME DOES NOT IDENTIFY A FLEET AND PRINTING IT ALONE READS AS A DUPLICATE.
    # Two tenants run the claims pack, Meridian Mutual and Harborline Insurance, and both name their
    # workflow "Claims Adjudication" because that is what it is. Nothing is wrong with that: a fleet
    # is a tenant's workflow, not a pack. The header carries the operator so the two cannot be
    # mistaken for one row written twice, which is exactly how they were read on 18 Sep 2026.
    fleets = _rows(conn, """
        select sc.pack, sc.workflow_id, sc.tenant_id, sc.ingest_key, sc.provy_url, sc.outcomes_wired,
               sc.operator_email, sc.display_name
        from sim_control_config sc order by sc.pack, sc.operator_email
    """)
    if pack:
        fleets = [f for f in fleets if f[0] == pack]
    if not fleets:
        print(f"no sim fleet registered{f' for pack {pack}' if pack else ''}")
        return 1

    problems = 0
    for (pack_name, workflow_id, tenant_id, console_key, provy_url, wired,
         operator, display_name) in fleets:
        who = operator or "(no operator recorded)"
        print(f"\n── {pack_name} · {who}  ({workflow_id})")
        if display_name:
            print(f"   fleet   {display_name}")
        console_hash = _sha(console_key) if console_key else None

        live = _rows(conn, """
            select key_hash, label, created_at from ag_ingest_keys
            where workflow_id = %s and revoked_at is null order by created_at
        """, (workflow_id,))
        live_hashes = {h for h, _, _ in live}

        print(f"   provy   {len(live)} active key(s): "
              + (", ".join(_short(h) for h, _, _ in live) or "NONE"))
        # ⛔ MORE THAN ONE ACTIVE KEY IS THE AMBIGUITY THAT HID THE 10 Sep FAILURE. Both authenticate,
        # so "the console's key works" and "ServiceNow's key works" can be true of DIFFERENT keys and
        # every surface still reads green. A warning, not a failure: a deliberate rotation runs two
        # for a few minutes.
        if len(live) > 1:
            print(f"   ⚠ {len(live)} keys are valid at once — revoke the one nothing uses, "
                  f"or a stale copy somewhere will keep working and hide a drift")
        print(f"   console key {_short(console_hash)}", end="")
        if not live:
            print("   ⛔ PROVY HAS NO ACTIVE KEY FOR THIS FLEET")
            problems += 1
        elif console_hash in live_hashes:
            print("   ✓ matches an active Provy key")
        else:
            print("   ⛔ NOT AN ACTIVE PROVY KEY — every dispatched run will 401")
            problems += 1

        # Where the runs are pointed. A sim fleet on production is the one unrecoverable mistake.
        host = (provy_url or "").replace("https://", "").replace("http://", "").rstrip("/")
        if not provy_url:
            print(f"   target  unset (the emitter's own default applies)")
        elif host == EXPECTED_PROVY_HOST:
            print(f"   target  {provy_url}   ✓ pre-prod")
        else:
            print(f"   target  {provy_url}   ⛔ NOT {EXPECTED_PROVY_HOST}")
            problems += 1

        # ⛔ ITSM ALONE HAS A THIRD COPY. Its outcomes are pushed BY the instance, so the key in
        # ServiceNow is what settles every work item. It can be stale while the other two agree.
        if pack_name == "itsm":
            creds = None
            try:
                creds = servicenow_credentials(environ)
            except MissingSecret as e:
                print(f"   servicenow  not checked: {', '.join(e.names)} not in the environment")
                print(f"               run: {door_command(list(DB_NAMES[:1]) + list(SERVICENOW_NAMES))}")
                problems += 1
            if creds:
                instance, user, password = creds
                sn_key = property_reader(instance, user, password, "provy.ingest.key")
                sn_url = property_reader(instance, user, password, "provy.ingest.url")
                if isinstance(sn_key, str) and sn_key.startswith("__HTTP_"):
                    code = sn_key.replace("__HTTP_", "").rstrip("_")
                    print(f"   servicenow  HTTP {code} — the instance refused these credentials")
                    print("               the runner uses the SERVICENOW_* secrets on provy-sim; "
                          "a rebuilt PDI needs all of them reset")
                    problems += 1
                else:
                    sn_hash = _sha(sn_key) if sn_key else None
                    ok = bool(sn_hash) and sn_hash in live_hashes
                    verdict = ("✓ matches an active Provy key" if ok
                               else "⛔ NOT AN ACTIVE PROVY KEY — outcomes will never settle")
                    print(f"   servicenow  key {_short(sn_hash)}   {verdict}")
                    if not ok:
                        problems += 1
                    sn_host = (sn_url or "").replace("https://", "").rstrip("/")
                    if not sn_url:
                        print("   servicenow  provy.ingest.url is MISSING — the rule pushes nowhere")
                        problems += 1
                    elif not sn_host.startswith(EXPECTED_PROVY_HOST):
                        print(f"   servicenow  pushes to {sn_url}   ⛔ NOT {EXPECTED_PROVY_HOST}")
                        problems += 1
                    else:
                        print(f"   servicenow  pushes to {sn_url}   ✓")

        # What actually landed, which is the only claim that matters.
        n = _rows(conn, "select count(*) from ag_sessions where workflow_id = %s", (workflow_id,))[0][0]
        settled = _rows(conn, """
            select count(distinct entity_id) from ag_outcomes where tenant_id = %s
        """, (tenant_id,))[0][0]
        print(f"   landed  {n} session(s), {settled} settled work item(s)"
              + ("   (outcomes come from the system of record)" if not wired else ""))

    print(f"\n{'OK: every fleet agrees' if problems == 0 else f'{problems} PROBLEM(S)'}")
    return 1 if problems else 0


def main(argv=None, environ=None) -> int:
    environ = os.environ if environ is None else environ
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pack", help="check one pack instead of all of them")
    args = ap.parse_args(argv)

    try:
        url = database_url(environ)
    except MissingSecret as e:
        print(f"fleet_doctor: {', '.join(e.names)} is not in the environment.\n"
              f"Run it under the secret door:\n  {door_command(list(DB_NAMES[:1]) + list(SERVICENOW_NAMES))}",
              file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"fleet_doctor: {e}", file=sys.stderr)
        return 2
    try:
        conn = _db(url)
    except ImportError:
        print("fleet_doctor: install psycopg2 (pip install psycopg2-binary)", file=sys.stderr)
        return 2
    try:
        return check(conn, args.pack, environ)
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
