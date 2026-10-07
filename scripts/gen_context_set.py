#!/usr/bin/env python3
"""Generate context-manifest ground-truth sets and record their SHA-256 (#1505, L4).

    python3 scripts/gen_context_set.py CM-A CM-B CM-C CM-D CAP-A CAP-B

Writes data/groundtruth_context_<SET>.jsonl and merges the set's record into
data/groundtruth_context_hashes.json. COMMIT BOTH BEFORE ANY EMISSION: the emitters refuse to run
unless the file's hash equals the recorded one, and the scorer reads the file, never Provy's output,
to decide what was injected.

A set that already has a recorded hash is not rewritten with different bytes.
This script makes no network call and reads no credential.
"""
import json
import os
import sys

TREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TREE)

from engine import context_truth as T  # noqa: E402


def main(argv):
    names = argv or ["CM-A", "CM-B", "CM-C", "CM-D", "CAP-A", "CAP-B"]
    data = os.path.join(TREE, "data")
    hashes_path = os.path.join(data, T.HASH_FILE)
    rec = json.load(open(hashes_path)) if os.path.exists(hashes_path) else {}
    for n in names:
        rec[n] = T.write_ground_truth(n, data, TREE)
        print(f"{n}: {rec[n]['sessions']} sessions, {rec[n]['faults_total']} faults {rec[n]['faults']}, sha256 {rec[n]['sha256']}")
    with open(hashes_path, "w") as f:
        json.dump(rec, f, indent=2, sort_keys=True)
        f.write("\n")


if __name__ == "__main__":
    main(sys.argv[1:])
