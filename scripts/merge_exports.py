#!/usr/bin/env python3
"""Merge the per-fleet exports of one set into the single export the scorer reads (#1505, L4).

    python3 scripts/merge_exports.py raised-cm-b.json export-cm-b-rest.json export-cm-b-otlp_native.json ...

A set is four fleets, and an engine export is one workflow. The merge concatenates the lists the scorer reads
(sessions, failed_checks, incidents, all_incidents, decisions) and SUMS the coverage per door across fleets, which
is what the set-level expected coverage is. It refuses to merge exports taken at different windows or as-of times,
because a sum over different windows is not a number. Offline.
"""
import json
import sys

LISTS = ("sessions", "failed_checks", "incidents", "all_incidents", "decisions")
FIELDS = ("decisionSteps", "withManifest", "withAges", "withRetrieval", "withInstruction", "viaLinks")


def merge(exports: list[dict]) -> dict:
    out: dict = {k: [] for k in LISTS}
    seen_ids: set = set()
    for ex in exports:
        for k in LISTS:
            out[k].extend(ex.get(k, []))
        for s in ex.get("sessions", []):
            if s["id"] in seen_ids:
                raise ValueError(f"session {s['id']} appears in two exports: a fleet was exported twice")
            seen_ids.add(s["id"])
    covs = [ex["coverage"] for ex in exports if ex.get("coverage")]
    if covs:
        if len({(c.get("windowDays"), c.get("asOf")) for c in covs}) != 1:
            raise ValueError("coverage was read at different windows or times; read every fleet with the same days and as-of")
        if len(covs) != len(exports):
            raise ValueError("some exports carry coverage and some do not")
        doors: dict = {}
        for c in covs:
            for d in c.get("byDoor", []):
                row = doors.setdefault(d["door"] or "none", {"door": d["door"] or "none", **{f: 0 for f in FIELDS}})
                for f in FIELDS:
                    # a door entry that never carried the figure makes the merged figure unknown (None), never a zero the scorer would take for a count
                    row[f] = None if (row[f] is None or f not in d) else row[f] + d[f]
        out["coverage"] = {"windowDays": covs[0].get("windowDays"), "asOf": covs[0].get("asOf"), "byDoor": list(doors.values())}
        if all(isinstance(c.get("fleet"), dict) for c in covs):
            out["coverage"]["fleet"] = {f: sum(c["fleet"].get(f) or 0 for c in covs) for f in FIELDS if all(f in c["fleet"] for c in covs)}
    return out


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    merged = merge([json.load(open(p)) for p in argv[1:]])
    with open(argv[0], "w") as f:
        json.dump(merged, f)
    print(f"{argv[0]}: {len(merged['sessions'])} sessions, {len(merged['failed_checks'])} failed checks, {len(merged['incidents'])} incidents")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
