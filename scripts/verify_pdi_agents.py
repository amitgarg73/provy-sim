#!/usr/bin/env python3
"""Runs the shared test vectors (tests/pdi_vectors.json) against the ProvyIamPlan DEPLOYED on the PDI, through its plan-check door. Reads and changes no estate state.

    ../argus/scripts/with-secrets SERVICENOW_INSTANCE SERVICENOW_USER SERVICENOW_PASSWORD -- python scripts/verify_pdi_agents.py

Exit 1 when any vector disagrees. Together with tests/test_pdi_agents.py (the Python twin and the real JavaScript file in node) this proves all three copies of the rule behave the same.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from multi_vendor.estate import Estate

vectors = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "pdi_vectors.json")))
e = Estate(os.environ["SERVICENOW_INSTANCE"], os.environ["SERVICENOW_USER"], os.environ["SERVICENOW_PASSWORD"])
res = e.plan_check(vectors)["results"]
bad = [r for r in res if not r["ok"]]
print(f"{len(res) - len(bad)} of {len(res)} vectors agree on the instance")
for r in bad:
    print("  DISAGREES:", r["kind"], "-", r["name"], "| the instance answered:", json.dumps(r.get("got"))[:200])
sys.exit(1 if bad or len(res) != len(vectors) else 0)
