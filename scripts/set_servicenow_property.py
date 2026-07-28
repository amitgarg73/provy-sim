#!/usr/bin/env python3
"""Set a sys_properties value in the ServiceNow demo instance.

Exists for the two secrets the lifecycle installer deliberately will not write:
the Provy ingest key and the Vercel bypass token. Both get rotated sooner or
later, and clicking through the instance's property form each time is how a demo
quietly ends up pointing at the wrong place.

The value is read from an environment variable, never a command-line argument, so
it does not land in shell history, in `ps` output, or in this repo. Nothing is
echoed back but a length and a short prefix, enough to confirm the right thing
landed without printing the secret.

    PROP_VALUE='provy_...' python scripts/set_servicenow_property.py provy.ingest.key
    PROP_VALUE='...'       python scripts/set_servicenow_property.py provy.vercel.bypass --show-prefix 6
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.servicenow import ServiceNowError, client_from_env

# Only the demo's own properties. A typo in a property name should fail here
# rather than create a new orphan property that silently does nothing, or worse,
# overwrite an unrelated platform setting.
ALLOWED = {
    "provy.ingest.key",
    "provy.vercel.bypass",
    "provy.ingest.url",
    "provy.demo.reopen_after_min",
    "provy.demo.reopen_pct",
    "provy.demo.tickets_per_run",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name", help=f"property to set, one of: {', '.join(sorted(ALLOWED))}")
    ap.add_argument("--show-prefix", type=int, default=4,
                    help="characters of the value to echo back for confirmation")
    args = ap.parse_args()

    if args.name not in ALLOWED:
        print(f"error: {args.name} is not one of this demo's properties. Allowed: "
              f"{', '.join(sorted(ALLOWED))}", file=sys.stderr)
        return 2

    value = os.environ.get("PROP_VALUE")
    if value is None:
        print("error: set the value in the PROP_VALUE environment variable, not on the "
              "command line, so it stays out of shell history and process listings.",
              file=sys.stderr)
        return 2
    value = value.strip()
    if not value:
        print("error: PROP_VALUE is empty. To deliberately blank a property, pass "
              "PROP_VALUE=' ' is not enough either: edit it in the instance.", file=sys.stderr)
        return 2

    sn = client_from_env()
    try:
        sn.require()
        rows = sn.query("sys_properties", f"name={args.name}", ["sys_id", "name", "value"], limit=1)
        if not rows:
            print(f"error: property {args.name} does not exist. Run "
                  f"scripts/install_servicenow_lifecycle.py first.", file=sys.stderr)
            return 1
        had_value = bool(rows[0]["value"])
        sn.update("sys_properties", rows[0]["sys_id"], {"value": value})
        check = sn.query("sys_properties", f"name={args.name}", ["value"], limit=1)[0]["value"]
    except ServiceNowError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if check != value:
        print(f"error: {args.name} did not take the new value. The instance still holds "
              f"something else, length {len(check)}.", file=sys.stderr)
        return 1

    prefix = value[:max(0, args.show_prefix)]
    print(f"{args.name} set ({'replaced a previous value' if had_value else 'was blank'}): "
          f"{prefix}… length {len(value)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
