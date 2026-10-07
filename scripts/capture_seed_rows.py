#!/usr/bin/env python3
"""Write the rows the UX lane (L6) seeds into the capture fleet from its committed ground truth (#1505, L4).

    python3 scripts/capture_seed_rows.py            # writes data/capture_seed_rows_CAP-A.jsonl

SPEC 12.6 step 3: the capture fleet is emitted with the CURRENT build, which cannot store a manifest, and L6's
seed script (web/scripts/context-seed-sql.ts) then writes `ag_traces.context` for those spans by an UPDATE keyed on
(session_id, span_id). This file is that key and the manifest to write, one row per decision step that carries one,
shaped exactly as a REST caller would have sent it. L6 runs each `context` through the real normalizeContext before
it prints SQL, so the seed cannot differ from what ingest would store. Fleet B (CAP-B) has no rows, by design.

No network, no database, no credential.
"""
import json
import os
import sys

TREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TREE)

from engine import context as C  # noqa: E402
from engine import context_truth as T  # noqa: E402


def rows(set_name: str, data_dir: str) -> list[dict]:
    T.verify_ground_truth(set_name, data_dir)
    out = []
    for p in C.load_plans(os.path.join(data_dir, T.truth_filename(set_name))):
        for s in p["steps"]:
            if s["step_type"] == "tool_call":
                continue
            m = C.wire_manifest(s, "rest")
            if m is None:
                continue
            out.append({"session_id": p["session_id"], "span_id": p["span_ids"][s["span"]], "agent": s["agent"], "step_type": s["step_type"],
                        "at": s["at"], "ingest_door": "rest", "context": m,
                        "fault_kinds": sorted({f["kind"] for f in p["faults"]}), "decoy_kinds": sorted({d["kind"] for d in p["decoys"]}),
                        "shape": p.get("shape")})
    return out


def main():
    data = os.path.join(TREE, "data")
    rs = rows("CAP-A", data)
    path = os.path.join(data, "capture_seed_rows_CAP-A.jsonl")
    with open(path, "w") as f:
        for r in rs:
            f.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")
    print(f"{len(rs)} rows -> {path}")


if __name__ == "__main__":
    main()
