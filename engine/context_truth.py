"""Ground-truth files and their recorded hash (#1505, lane L4). ADDS this file to provy-sim/engine/.

A set is generated once, written to data/groundtruth_context_<SET>.jsonl, and its SHA-256 is recorded
in data/groundtruth_context_hashes.json. Both are committed BEFORE any emitter call. The emitters call
verify_ground_truth first and refuse to run on a file whose hash is not the recorded one; the scorer
reads the file and never Provy's output to decide what was injected.
"""
from __future__ import annotations

import json
import os

from config import context_sets as CS
from engine import context as C

HASH_FILE = "groundtruth_context_hashes.json"


class GroundTruthHashMismatch(RuntimeError):
    """The file on disk is not the file whose hash was recorded before any emission."""


def truth_filename(set_name: str) -> str:
    return f"groundtruth_context_{set_name}.jsonl"


def summarise(plans: list[dict]) -> dict:
    kinds: dict[str, int] = {}
    decoys: dict[str, int] = {}
    classes: dict[str, int] = {}
    routes: dict[str, int] = {}
    for p in plans:
        classes[p["class"]] = classes.get(p["class"], 0) + 1
        routes[p["route"]] = routes.get(p["route"], 0) + 1
        for f in p["faults"]:
            kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
        for d in p["decoys"]:
            decoys[d["kind"]] = decoys.get(d["kind"], 0) + 1
    return {"sessions": len(plans), "faults": kinds, "faults_total": sum(kinds.values()), "decoys": decoys,
            "classes": classes, "routes": routes, "fleets": sorted({p["fleet"] for p in plans})}


def write_ground_truth(set_name: str, data_dir: str, code_dir: str) -> dict:
    """Generate one set, write its file, and return the record for the hash file.

    Refuses when a hash was already recorded for the set and the generator now produces different
    bytes: a hold-out is generated once, and a changed generator must not quietly change it."""
    plans = C.generate_set(set_name)
    body = C.dumps_plans(plans)
    digest = C.sha256_bytes(body)
    hashes_path = os.path.join(data_dir, HASH_FILE)
    recorded = json.load(open(hashes_path)) if os.path.exists(hashes_path) else {}
    old = recorded.get(set_name)
    if old and old["sha256"] != digest:
        raise GroundTruthHashMismatch(
            f"{set_name}: the generator now produces {digest} but {old['sha256']} was recorded. A set is generated once. "
            f"If the generator changed on purpose, record the change in PROVENANCE.md and use a new set name.")
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, truth_filename(set_name)), "wb") as f:
        f.write(body)
    spec = CS.SETS.get(set_name) or CS.CAPTURE[set_name]
    return {
        "file": truth_filename(set_name), "sha256": digest, "bytes": len(body), "role": spec["role"], "hold_out": spec["hold_out"],
        **summarise(plans),
        "generator_sha256": {n: C.sha256_bytes(open(os.path.join(code_dir, n), "rb").read())
                             for n in ("engine/context.py", "config/context_sets.py")},
    }


def verify_ground_truth(set_name: str, data_dir: str) -> str:
    """Raise unless the file's SHA-256 equals the recorded one. Returns the hash."""
    path = os.path.join(data_dir, truth_filename(set_name))
    hashes_path = os.path.join(data_dir, HASH_FILE)
    if not os.path.exists(path) or not os.path.exists(hashes_path):
        raise GroundTruthHashMismatch(f"{set_name}: ground truth or its hash record is missing in {data_dir}")
    want = json.load(open(hashes_path)).get(set_name)
    if not want:
        raise GroundTruthHashMismatch(f"{set_name}: no hash was recorded")
    got = C.sha256_bytes(open(path, "rb").read())
    if got != want["sha256"]:
        raise GroundTruthHashMismatch(f"{set_name}: file hash {got} differs from the recorded {want['sha256']}")
    return got
