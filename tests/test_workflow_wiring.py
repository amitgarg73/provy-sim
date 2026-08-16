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


def _run_batch_step() -> dict:
    with open(os.path.join(WORKFLOWS, "_run.yml")) as f:
        spec = yaml.safe_load(f)
    steps = spec["jobs"]["run"]["steps"]
    batch = [s for s in steps if str(s.get("name", "")).startswith("Run batch")]
    assert batch, "_run.yml has no 'Run batch' step; the wiring below cannot be checked"
    return batch[0]


def _run_batch_env() -> dict:
    return _run_batch_step()["env"]


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


def test_the_ingest_key_is_derived_not_enumerated():
    """⛔ THE SILENT ONE, NOW UNSPELLABLE (provy-sim#7).

    config/workflows.py resolves the ingest key from PROVY_KEY_<PACK>. That variable used to be set
    by ten hand-written conditionals, and a pack missing from the list ran green and emitted nothing:
    the key resolved empty, emit_enabled() correctly refused, and the job exited 0 while every
    downstream signal reported success.

    The list is gone. The name is derived from the pack, so no per-pack line exists to forget. This
    test pins the derivation rather than any pack's presence, because a test that enumerates packs
    would reintroduce exactly the maintenance burden the change removed."""
    step = _run_batch_step()
    env = step["env"]
    script = str(step.get("run", ""))

    assert "PACK_INGEST_KEY" in env, "_run.yml no longer passes the ingest key into the step"
    assert re.search(r"PROVY_KEY_\$\{?PACK_UPPER", script), (
        "the step does not derive PROVY_KEY_<PACK> from the pack name"
    )
    assert re.search(r"tr\s+'\[:lower:\]'\s+'\[:upper:\]'", script), (
        "the pack name is not upper-cased, so PROVY_KEY_<PACK> would not match config/workflows.py"
    )


def test_an_empty_ingest_key_aborts_instead_of_dry_running():
    """A run that emits nothing must FAIL, not pass quietly. The whole cost of the original bug was
    that it exited 0 and looked identical to a real run."""
    script = str(_run_batch_step().get("run", ""))
    assert re.search(r'if\s+\[\s+-z\s+"\$\{?PACK_INGEST_KEY', script), (
        "no guard on an empty ingest key; a dispatch without one would dry-run and report success"
    )
    assert "exit 1" in script, "the empty-key guard does not fail the job"


def test_no_leftover_per_pack_key_lines():
    """A stray PROVY_KEY_<PACK> line would silently win over the derived one for that pack, and
    would be dead wiring that reads as coverage for every other pack."""
    env = _run_batch_env()
    leftovers = [k for k in env if k.startswith("PROVY_KEY_")]
    assert not leftovers, f"_run.yml still enumerates per-pack keys: {sorted(leftovers)}"
