#!/usr/bin/env python3
"""Score what Provy raised for a context-manifest set against its committed ground truth (#1505, L4).

    python3 scripts/score_context.py CM-A --export raised-cm-a.json
    python3 scripts/score_context.py CM-B --export raised-cm-b.json --confirm-one-shot

The export is the engine harness's export format (see engine/context_score.py for the keys read). The
ground-truth file's SHA-256 is checked against the recorded one first. A hold-out (CM-B, CM-C, CM-D) is read
ONCE: it needs --confirm-one-shot, the read is written to data/holdout_reads.jsonl, and a second read is
refused unless --allow-reread, which stamps the result NOT CLEAN. The acceptance bars of SPEC 12.5 are printed
beside the numbers. Offline: no network, no credential, no write other than the ledger and --out.
"""
import argparse
import json
import os
import sys

TREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TREE)

from engine import context_score as S  # noqa: E402


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("set_name")
    ap.add_argument("--export", required=True)
    ap.add_argument("--out", help="write the full result as JSON")
    ap.add_argument("--ledger")
    ap.add_argument("--confirm-one-shot", action="store_true")
    ap.add_argument("--allow-reread", action="store_true")
    ap.add_argument("--include-case-variants", action="store_true",
                    help="count the case-variant source sessions (a spec conflict, see PROVENANCE.md) in the false alarm bar")
    a = ap.parse_args(argv)
    try:
        result, text = S.run_scoring(a.set_name, a.export, os.path.join(TREE, "data"), ledger_path=a.ledger,
                                     confirm_one_shot=a.confirm_one_shot, allow_reread=a.allow_reread,
                                     include_case_variants=a.include_case_variants)
    except (S.HoldOutRefused, Exception) as e:                  # noqa: BLE001
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    print(text)
    if a.out:
        with open(a.out, "w") as f:
            json.dump(result, f, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
