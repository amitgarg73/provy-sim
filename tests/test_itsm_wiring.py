"""The ITSM fleet is re-onboarded often, and every re-onboard mints a new ingest key.

Everything that makes the instance behave as the system of record hangs off that key, so the run
that the Sim Control console kicks has to re-point the instance itself. It did not, and the failure
mode is silent in both directions: the push logs that it is disabled and sends nothing, or the batch
bursts through in seconds and the response-target condition grades nothing at all.

These tests hold the CI path to the four things a fresh key needs. They read the workflow files as
text on purpose: pyyaml is not a dependency here, and what is being asserted is that a specific
command reaches the runner, which the text carries exactly.
"""
from pathlib import Path

import pytest

from scripts.seed_itsm_incidents import to_create

REPO = Path(__file__).resolve().parents[1]
RUN_ITSM = (REPO / ".github" / "workflows" / "run-itsm.yml").read_text()
SHARED = (REPO / ".github" / "workflows" / "_run.yml").read_text()


# ── topping up the backlog ──────────────────────────────────────────────────
@pytest.mark.parametrize("target,waiting,expected", [
    (12, 0, 12),    # empty instance: seed the lot
    (12, 5, 7),     # a partly worked backlog: seed only the shortfall
    (12, 12, 0),    # already deep enough: create nothing
    (12, 30, 0),    # deeper than asked: still create nothing, never delete
    (0, 5, 0),
])
def test_top_up_creates_only_the_shortfall(target, waiting, expected):
    assert to_create(target, waiting) == expected


def test_top_up_survives_a_nonsense_count():
    """A negative reading must not turn into a burst of creates against a live instance."""
    assert to_create(12, -3) == 12


# ── the CI path a new key depends on ────────────────────────────────────────
def test_the_run_wires_the_instance_to_the_dispatched_key():
    """Without this the instance still pushes to the PREVIOUS tenant's key, and the new one sits
    at traces-with-no-outcomes while everything reports success."""
    assert "scripts/wire_itsm_fleet.py --provy-url" in SHARED
    assert "PROVY_INGEST_KEY: ${{ inputs.ingest_key }}" in SHARED


def test_the_run_seeds_work_before_working_it():
    """The pack invents no work items. An unseeded instance raises rather than running empty."""
    assert "scripts/seed_itsm_incidents.py --top-up" in SHARED


def test_itsm_prep_steps_are_pack_guarded():
    """The other eight packs have no ServiceNow instance and must not run either step."""
    assert SHARED.count("if: inputs.pack == 'itsm'") == 2


def test_prep_steps_carry_the_servicenow_credentials():
    """Both steps call the instance directly, so neither can rely on the run step's env."""
    assert SHARED.count("SERVICENOW_PASSWORD: ${{ secrets.SERVICENOW_PASSWORD }}") == 3


def test_the_batch_is_paced():
    assert '--pace "${{ inputs.pace }}"' in SHARED


def test_itsm_paces_by_default():
    """0 is the right default for the self-contained packs and wrong for this one: an unpaced ITSM
    run answers every ticket within seconds, so no response target breaches and the condition that
    carries the whole demo grades nothing."""
    assert "pace: ${{ inputs.pace }}" in RUN_ITSM
    line = next(ln for ln in RUN_ITSM.splitlines() if ln.strip().startswith("pace:") and "default" in ln)
    assert "default: '20'" in line


def test_the_shared_workflow_still_defaults_to_no_pacing():
    line = next(ln for ln in SHARED.splitlines() if ln.strip().startswith("pace:") and "default" in ln)
    assert "default: '0'" in line
