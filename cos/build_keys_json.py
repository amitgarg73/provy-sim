"""Build the COS_FLEET_KEYS_JSON secret and copy it to the clipboard. It prints no key.

The founder runs this on the laptop:   python3 cos/build_keys_json.py
It reads ~/Claude Projects/provy-sim-state-cos/tenant.json, picks the ingest key of each of the 11 fleets,
and puts one JSON map ({"cos": "...", ...}) on the clipboard with pbcopy. The screen shows only the fleet
names and a count, never a value. Then paste it into the GitHub secret (see cos/README.md).

The state file's exact layout was not read when this was written, so the lookup is tolerant: for each
fleet it accepts the fleet's entry at the top level or under a "fleets" map, either as the key text itself
or as an object with a field named ingest_key, key, api_key or token. If a fleet cannot be found the script
stops and names the fleet, and nothing is copied.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

FLEETS = ["cos", "status", "meetings", "cross", "sched", "wms", "kestrel", "pledge", "candidate", "twin", "sender"]
FIELDS = ("ingest_key", "key", "api_key", "token")
STATE = Path.home() / "Claude Projects" / "provy-sim-state-cos" / "tenant.json"


def _key_of(entry):
    if isinstance(entry, str):
        return entry.strip() or None
    if isinstance(entry, dict):
        for f in FIELDS:
            v = entry.get(f)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def build(state: dict) -> dict:
    """Return {fleet: key} for all 11 fleets, or raise SystemExit naming the fleets that are missing."""
    pools = [state]
    for name in ("fleets", "keys", "ingest_keys", "workflows"):
        if isinstance(state.get(name), dict):
            pools.append(state[name])
    out, missing = {}, []
    for f in FLEETS:
        k = next((_key_of(p[f]) for p in pools if f in p and _key_of(p[f])), None)
        if k and "\n" not in k and "\r" not in k:
            out[f] = k
        else:
            missing.append(f)
    if missing:
        raise SystemExit("Could not find a key for: " + ", ".join(missing) + ". Nothing was copied.")
    return out


def main() -> int:
    if not STATE.exists():
        print("State file not found: " + str(STATE))
        return 1
    try:
        state = json.loads(STATE.read_text())
    except Exception:
        print("The state file is not valid JSON. Nothing was copied.")
        return 1
    if not isinstance(state, dict):
        print("The state file is not a JSON object. Nothing was copied.")
        return 1
    keys = build(state)
    subprocess.run(["pbcopy"], input=json.dumps(keys, separators=(",", ":")).encode(), check=True)
    print("Copied COS_FLEET_KEYS_JSON to the clipboard: " + str(len(keys)) + " fleets (" + ", ".join(keys) + ").")
    print("Paste it into the secret, then clear the clipboard (copy any other text).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
