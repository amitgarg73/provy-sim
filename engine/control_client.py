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


def post_injected(pack: str, injected: dict, runs: Optional[int] = None, timeout: float = 8.0) -> bool:
    """Post an injected-truth run summary (engine.scoreboard.aggregate_injected output) to the
    console so its scoreboard has the injected side. Best-effort: returns False on any failure
    (no env, no workflow id, network down) and never raises."""
    base = os.environ.get("CONTROL_URL", "").strip().rstrip("/")
    if not base:
        return False
    wf_id = _workflow_id_for(pack)
    if not wf_id:
        return False
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("CONTROL_LEVERS_TOKEN", "").strip()
    if token:
        headers["x-control-token"] = token
    body = json.dumps({
        "pack": pack,
        "runs": runs if runs is not None else injected.get("runs"),
        "injected": injected,
    }).encode()
    try:
        req = urllib.request.Request(f"{base}/api/runs/{wf_id}", data=body, headers=headers, method="POST")
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except Exception:
        return False


def post_pending_outcomes(pack: str, records: list, emitter, timeout: float = 10.0) -> bool:
    """Hand the console the ground truth for a batch this run did NOT reconcile.

    ⛔ WHY THIS EXISTS. The ledger lives in data/groundtruth_<pack>.jsonl, which is gitignored and
    never uploaded as an artifact, so it is written inside the Actions runner and destroyed with it.
    A later run starts empty and cannot settle anything an earlier one produced. That is why the
    "delayed outcome" scenario has always been blocked, and why a green un-reconciled batch could
    never be followed by those same items settling.

    ⛔ THE SIM BUILDS THE PAYLOAD, THE CONSOLE ONLY RELAYS IT. What is sent here is exactly what
    emitter.outcome() would have posted. The console stores the bytes and forwards them unchanged, so
    /api/ingest/outcome keeps ONE implementation, in this file, covered by this repo's tests. A second
    one in TypeScript is the drift the SDK contract rule exists to prevent.

    Best-effort: returns False on any failure and never raises. A run must not fail because the
    console is unreachable.
    """
    base = os.environ.get("CONTROL_URL", "").strip().rstrip("/")
    if not base:
        return False
    wf_id = _workflow_id_for(pack)
    if not wf_id:
        return False

    payloads = []
    for rec in records:
        try:
            # build_only: the emitter constructs the payload and posts nothing. The outcome must not
            # reach Provy now — the entire point is that it arrives later.
            payloads.append(emitter.outcome_payload(_result_for_payload(rec), occurred_at=None))
        except Exception:
            continue
    if not payloads:
        return False

    headers = {"Content-Type": "application/json"}
    token = os.environ.get("CONTROL_LEVERS_TOKEN", "").strip()
    if token:
        headers["x-control-token"] = token
    body = json.dumps({"outcomes": payloads}, default=str).encode()
    try:
        req = urllib.request.Request(
            f"{base}/api/pending-outcomes/{wf_id}", data=body, headers=headers, method="POST")
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except Exception:
        return False


def _result_for_payload(rec: dict):
    """The ledger record as the emitter's outcome builder expects it. Same shape reconcile.py rebuilds
    for a live post, so a stored outcome and a posted one can never describe different things."""
    from engine.reconcile import _minimal_result
    return _minimal_result(rec)
