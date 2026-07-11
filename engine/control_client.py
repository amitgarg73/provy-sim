"""Optional bridge to the Provy Sim Control console.

The console (a separate Vercel app) lets an operator dial the chaos levers per
fleet and stores the config. When CONTROL_URL is set, this module fetches that
config at run time so the console can drive the sim. It is fully optional:
any failure (no env, network down, unknown fleet) returns None and the caller
falls back to the local defaults in config/workflows.py. The sim always runs
offline.

Env:
  CONTROL_URL                    e.g. https://provy-sim-control.vercel.app
  CONTROL_LEVERS_TOKEN           shared token (matches the console's CONTROL_LEVERS_TOKEN)
  CONTROL_WORKFLOW_ID_<PACK>     the Provy workflow id for that pack's fleet
                                 (e.g. CONTROL_WORKFLOW_ID_SUPPORT). Falls back to
                                 the generic CONTROL_WORKFLOW_ID if the per-pack
                                 one is unset.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Optional


def _workflow_id_for(pack: str) -> str:
    return (
        os.environ.get(f"CONTROL_WORKFLOW_ID_{pack.upper()}", "")
        or os.environ.get("CONTROL_WORKFLOW_ID", "")
    ).strip()


def fetch_lever_rates(pack: str, timeout: float = 8.0) -> Optional[dict]:
    """Return the lever config dict for a pack's fleet, or None to fall back.

    The dict is exactly what engine.levers.LeverConfig(settings) expects:
      { "<lever>": {"rate": float, "target": str|None, "params": {...}}, ... }
    """
    base = os.environ.get("CONTROL_URL", "").strip().rstrip("/")
    if not base:
        return None
    wf_id = _workflow_id_for(pack)
    if not wf_id:
        return None

    headers = {}
    token = os.environ.get("CONTROL_LEVERS_TOKEN", "").strip()
    if token:
        headers["x-control-token"] = token

    url = f"{base}/api/levers/{wf_id}"
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = json.loads(resp.read().decode())
        levers = data.get("levers")
        if isinstance(levers, dict) and levers:
            return levers
        return None
    except Exception:
        # Offline / unreachable / unauthorized -> caller uses local defaults.
        return None
