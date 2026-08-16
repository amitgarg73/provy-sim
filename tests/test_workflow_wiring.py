"""Every pack must be runnable from the Sim Control console, and must actually EMIT when it runs.

⛔ WHY THIS FILE EXISTS. On 2026-08-16 the edwin pack was registered in provy-sim AND in the console,
and had its own `run-edwin.yml`, and a dispatched run still sent nothing. `_run.yml` maps the caller's
ingest key into a per-pack `PROVY_KEY_<PACK>` variable through an explicit list, and edwin was not on
it. The key resolved to empty, `emit_enabled()` correctly refused to send, and the job printed
"emit=OFF (dry run)" and exited 0.

Everything downstream looked like success: a green Actions tick, "reconcile: posted 4, errors 0", and
a run recorded against the fleet. Nothing distinguished it from a real run except the fleet not
growing. A silent no-op that reports success is worse than a failure, so it gets a test.

Both halves are checked, because either one alone is enough to break a pack:
  - a missing run-<pack>.yml   -> the console's dispatch 404s and the run never starts
  - a missing PROVY_KEY_<PACK> -> the run starts, goes green, and emits nothing
"""
import os
import re

import pytest
import yaml

from packs import PACKS

WORKFLOWS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".github", "workflows")


def _run_batch_env() -> dict:
    with open(os.path.join(WORKFLOWS, "_run.yml")) as f:
        spec = yaml.safe_load(f)
    steps = spec["jobs"]["run"]["steps"]
    batch = [s for s in steps if str(s.get("name", "")).startswith("Run batch")]
    assert batch, "_run.yml has no 'Run batch' step; the wiring below cannot be checked"
    return batch[0]["env"]


@pytest.mark.parametrize("pack", sorted(PACKS))
def test_pack_has_a_dispatchable_workflow(pack):
    """The console dispatches run-<pack>.yml by name. Without the file the Kick button 404s."""
    path = os.path.join(WORKFLOWS, f"run-{pack}.yml")
    assert os.path.exists(path), (
        f"no run-{pack}.yml, so the Sim Control console cannot start this pack"
    )
    with open(path) as f:
        spec = yaml.safe_load(f)
    assert spec["jobs"]["run"]["with"]["pack"] == pack, (
        f"run-{pack}.yml dispatches a different pack than its filename claims"
    )


@pytest.mark.parametrize("pack", sorted(PACKS))
def test_pack_key_reaches_the_runner(pack):
    """⛔ THE SILENT ONE. config/workflows.py resolves the ingest key from PROVY_KEY_<PACK>, and
    _run.yml is what sets it. A pack absent from that list runs green and emits nothing."""
    env = _run_batch_env()
    var = f"PROVY_KEY_{pack.upper()}"
    assert var in env, (
        f"{var} is not set in _run.yml's 'Run batch' step. A dispatched {pack} run would go GREEN "
        f"and send NOTHING, because the key resolves empty and emit_enabled() refuses."
    )
    # And it must be gated on THIS pack, not copy-pasted from a neighbour.
    assert re.search(rf"inputs\.pack\s*==\s*'{re.escape(pack)}'", str(env[var])), (
        f"{var} is not gated on inputs.pack == '{pack}'; it looks copy-pasted from another pack"
    )


def test_no_orphan_key_mappings():
    """A PROVY_KEY_* for a pack that no longer exists is dead wiring that reads as coverage."""
    env = _run_batch_env()
    mapped = {k.replace("PROVY_KEY_", "").lower() for k in env if k.startswith("PROVY_KEY_")}
    orphans = mapped - set(PACKS)
    assert not orphans, f"_run.yml maps keys for packs that do not exist: {sorted(orphans)}"
