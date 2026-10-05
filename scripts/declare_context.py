#!/usr/bin/env python3
"""Tell a fleet what its context is allowed to be, through the product's own door: PUT /api/compute/context-declarations
with the fleet's ingest key (integration guide, "Telling Provy what your context is allowed to be"). Pre-prod only.

    python scripts/declare_context.py --pack iam --key-file KEYS/sdk.json [--base http://localhost:3100]

The limit is 30 days (720 hours) and the approved list is the pack's own (engine.context_levers.PROFILES), the same limit
and list the step 2 baseline declared. The key is read from the file and never printed.
"""
import argparse, json, os, sys
import requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.context_levers import PROFILES

ap = argparse.ArgumentParser()
ap.add_argument("--pack", required=True, choices=sorted(PROFILES))
ap.add_argument("--key-file", required=True)
ap.add_argument("--base", default="http://localhost:3100")
a = ap.parse_args()
key = json.load(open(a.key_file))["ingest_key"]
H = {"x-provy-key": key, "Content-Type": "application/json"}
env = requests.get(a.base + "/api/health/env", timeout=120).json().get("environment")
assert env == "preprod", f"environment is {env!r}"
prof = PROFILES[a.pack]
body = {"context_max_age": {"hours": prof.limit_days * 24}, "approved_sources": {"sources": list(prof.approved)}}
r = requests.put(a.base + "/api/compute/context-declarations", headers=H, data=json.dumps(body), timeout=120)
print("PUT", r.status_code, r.text[:400])
g = requests.get(a.base + "/api/compute/context-declarations", headers=H, timeout=120)
print("GET", g.status_code, g.text[:600])
