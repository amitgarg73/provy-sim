#!/usr/bin/env python3
"""Point a Servicely instance's outcome push at a Provy fleet, and turn the push on.

The counterpart of scripts/wire_itsm_fleet.py. Run AFTER scripts/install_servicely_lifecycle.py.

    PROVY_INGEST_KEY='provy_...' scripts/with-secrets SERVICELY_INSTANCE_URL SERVICELY_API_TOKEN VERCEL_PROTECTION_BYPASS -- \\
        python scripts/wire_servicely_fleet.py --provy-url https://dev.provy.ai
    python scripts/wire_servicely_fleet.py --check          # report only, change nothing

Sets, as non-client-accessible ApplicationProperty rows: provy.ingest.key (from the environment, never a
command-line argument), provy.ingest.url, provy.vercel.bypass (when VERCEL_PROTECTION_BYPASS is present) and
provy.push.mode = live. Refuses a production ingest URL. Reads everything back and prints it MASKED: a
script that only prints what it meant to do is worth very little.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.servicely import ServicelyError, client_from_env  # noqa: E402
from engine.targets import is_production_target  # noqa: E402

APPLICATION = "c0a8016c680c1e3481680c31239b0004"
WATCHED = ("provy.ingest.key", "provy.ingest.url", "provy.vercel.bypass", "provy.push.mode", "provy.marker")


def _all_properties(client) -> dict[str, list[dict]]:
    """Every provy.* row, read page by page. An equality filter on a dotted key returns an empty 404."""
    out: dict[str, list[dict]] = {}
    for page in range(1, 21):
        rows = client.query("ApplicationProperty", {"page": page}, page_size=100)
        if not rows:
            break
        for r in rows:
            k = str(r.get("Key", ""))
            if k.startswith("provy."):
                out.setdefault(k, []).append(r)
    return out


def _mask(v: str, keep: int = 12) -> str:
    if not v:
        return "(empty)"
    return v if len(v) <= keep else f"{v[:keep]}... ({len(v)} chars)"


def set_property(client, props: dict, key: str, value: str, description: str) -> str:
    rows = props.get(key, [])
    if len(rows) > 1:
        raise ServicelyError(f"{len(rows)} rows hold {key}; remove the extras first")
    if rows:
        client.update("ApplicationProperty", rows[0]["id"], {"Value": value})
        return "updated"
    client.create("ApplicationProperty", {"Key": key, "Value": value, "DataType": "string",
                                          "Description": description, "ClientSideAccessible": False,
                                          "Application": APPLICATION})
    return "created"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provy-url", default=os.environ.get("PROVY_URL", "").rstrip("/"),
                    help="Provy base URL of this fleet, e.g. https://dev.provy.ai")
    ap.add_argument("--check", action="store_true", help="report and change nothing")
    ap.add_argument("--allow-prod", action="store_true", help="permit a production ingest URL (refused by default)")
    args = ap.parse_args()
    try:
        client = client_from_env()
        client.require()
        props = _all_properties(client)
        if not args.check:
            key = os.environ.get("PROVY_INGEST_KEY", "").strip()
            if not key:
                print("PROVY_INGEST_KEY is not set: it is read from the environment, never an argument", file=sys.stderr)
                return 2
            if not args.provy_url:
                print("--provy-url (or PROVY_URL) is required", file=sys.stderr)
                return 2
            url = args.provy_url.rstrip("/") + "/api/ingest/outcome"
            if is_production_target(url) and not args.allow_prod:
                print(f"refusing: {url} is a production host (--allow-prod to override)", file=sys.stderr)
                return 2
            print("provy.ingest.key", set_property(client, props, "provy.ingest.key", key, "The fleet's ingest key."))
            print("provy.ingest.url", set_property(client, props, "provy.ingest.url", url, "Where the outcome push posts."))
            bypass = os.environ.get("VERCEL_PROTECTION_BYPASS", "").strip()
            if bypass:
                print("provy.vercel.bypass", set_property(client, props, "provy.vercel.bypass", bypass,
                                                          "Pre-prod deployment protection bypass."))
            print("provy.push.mode", set_property(client, props, "provy.push.mode", "live", "capture or live."))
        after = _all_properties(client)
    except ServicelyError as e:
        print(f"servicely: {e}", file=sys.stderr)
        return 2
    print("\ninstance now holds:")
    for k in WATCHED:
        rows = after.get(k, [])
        print(f"  {k:22s} {_mask(str(rows[0].get('Value', ''))) if rows else '(not set)'}"
              f"{'  [DUPLICATE ROWS]' if len(rows) > 1 else ''}")
    live = (after.get("provy.push.mode") or [{}])[0].get("Value") == "live"
    print("\nlive: the push sends to Provy when a marked ticket closes." if live
          else "\nnot live: provy.push.mode is not 'live'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
