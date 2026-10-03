#!/usr/bin/env python3
"""Replay a committed context-manifest set against PRE-PROD (#1505, L4). Read SIM-RUNBOOK.md first.

    PROVY_EMIT=1 PROVY_URL=https://dev.provy.ai \
    PROVY_KEY_CM_B_REST=... PROVY_KEY_CM_B_OTLP_NATIVE=... \
    python3 scripts/emit_context_set.py CM-B [--fleet rest --fleet log] [--limit 5]

Without PROVY_EMIT it is a dry run: every request is built and counted, nothing is sent. The key for
each fleet comes from the environment variable named by config/context_sets.key_env_name (the value is
never in this tree and this script reads no file for it).

It refuses to run when the ground-truth file's SHA-256 is not the one recorded, when the target is not an
allow-listed pre-prod host, and when the deployment does not say environment == preprod. Nothing here
can be pointed at production: PROVY_ALLOW_PROD is ignored.
"""
import argparse
import os
import sys

TREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TREE)      # run from provy-sim after copying the tree across: engine/ is then the real package

from engine import context_emit as E  # noqa: E402


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("set_name")
    ap.add_argument("--fleet", action="append", help="only these fleet keys (rest, otlp_native, otlp_conv, log, capture)")
    ap.add_argument("--limit", type=int, help="at most N sessions per fleet (a smoke test)")
    ap.add_argument("--no-outcomes", action="store_true", help="do not post outcomes (post them later, after the close-time pass)")
    args = ap.parse_args(argv)
    url = os.environ.get("PROVY_URL") or "https://dev.provy.ai"
    data = os.path.join(TREE, "data")

    def make(fleet):
        return E.ContextEmitter(ingest_key=E.fleet_key(args.set_name, fleet), base_url=url)

    counts = E.emit_set(args.set_name, data, make, fleets=args.fleet, limit=args.limit, with_outcomes=not args.no_outcomes,
                        progress=print)
    live = os.environ.get("PROVY_EMIT", "").lower() in ("1", "true", "yes", "on")
    print(("SENT" if live else "DRY RUN (nothing sent)"), args.set_name, counts)


if __name__ == "__main__":
    main(sys.argv[1:])
