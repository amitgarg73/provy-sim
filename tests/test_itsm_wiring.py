"""The ITSM fleet is re-onboarded often, and every re-onboard mints a new ingest key.

Everything that makes the instance behave as the system of record hangs off that key, so the run
that the Sim Control console kicks has to re-point the instance itself. It did not, and the failure
mode is silent in both directions: the push logs that it is disabled and sends nothing, or the batch
bursts through in seconds and the response-target condition grades nothing at all.

These tests hold the CI path to the four things a fresh key needs. They read the workflow files as
text on purpose: pyyaml is not a dependency here, and what is being asserted is that a specific
command reaches the runner, which the text carries exactly.
"""
import collections
import random
from pathlib import Path

import pytest

from scripts.seed_itsm_incidents import (PRIORITY_MIXES, arrival_gaps,
                                         build_incident, pick_priority,
                                         to_create)

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


# ── arrivals, so waiting time is not just queue position ────────────────────
def test_arrival_gaps_are_not_a_metronome():
    """A fixed interval is as bad as a single burst: both make how long a ticket waited a function
    of where it sat in the queue, which is what made every miss identical."""
    gaps = arrival_gaps(random.Random(3), 40, 35.0)
    assert len(set(round(g, 3) for g in gaps)) > 30


def test_arrival_gaps_average_near_the_requested_mean():
    gaps = arrival_gaps(random.Random(5), 4000, 35.0)
    assert 28 < sum(gaps) / len(gaps) < 36


def test_arrival_gaps_clip_the_long_tail():
    """An untruncated exponential occasionally stalls a short run entirely."""
    assert max(arrival_gaps(random.Random(11), 3000, 20.0)) <= 80.0


# ── priority spread, so one delay does not decide every ticket ──────────────
def test_benchmark_mix_matches_the_measured_instance():
    """94% P3 is what a real desk looks like and it stays the default."""
    counts = collections.Counter(pick_priority(random.Random(i), "benchmark") for i in range(2000))
    assert counts["3"] / 2000 > 0.9


def test_spread_mix_exercises_every_response_target():
    """The compressed targets are 15s/1m/4m/8m. A batch that is all one priority can only ever
    breach on elapsed time, which is the artefact this removes."""
    counts = collections.Counter(pick_priority(random.Random(i), "spread") for i in range(2000))
    for priority in ("1", "2", "3", "4"):
        assert counts[priority] / 2000 > 0.05, f"P{priority} too rare to exercise its target"


def test_every_mix_stays_inside_the_slas_that_exist():
    """There is no P5 SLA installed, so seeding a P5 would create a ticket graded on nothing."""
    for mix in PRIORITY_MIXES:
        assert {p for p, _ in PRIORITY_MIXES[mix]} <= {"1", "2", "3", "4"}


def test_seeded_incidents_carry_their_priority_on_arrival():
    """Set at creation, not at triage: the response clock starts when the ticket opens, so a
    priority the agent assigns later is too late to pick the target it is graded against."""
    payload = build_incident(random.Random(1), [], "spread")
    assert payload["impact"] in {"1", "2", "3"}
    assert payload["urgency"] in {"1", "2", "3"}


# ── the CI path a new key depends on ────────────────────────────────────────
def test_the_run_wires_the_instance_to_the_dispatched_key():
    """Without this the instance still pushes to the PREVIOUS tenant's key, and the new one sits
    at traces-with-no-outcomes while everything reports success."""
    assert "scripts/wire_itsm_fleet.py --provy-url" in SHARED
    assert "PROVY_INGEST_KEY: ${{ inputs.ingest_key }}" in SHARED


def test_the_run_opens_with_a_backlog_and_feeds_the_rest_in():
    """The pack invents no work items, so something has to put them there. Only the opening backlog
    is seeded up front; the rest arrive while the agent is already working."""
    assert "--top-up" in SHARED
    assert "--arrivals" in SHARED
    assert "--mean-gap" in SHARED


def test_arrivals_run_alongside_the_batch_not_before_it():
    """Seeding then running is the burst behaviour under another name."""
    assert "&\n" in SHARED.split("--arrivals")[1].split("python scripts/run_batch.py")[0]


def test_the_runs_exit_code_survives_the_background_arrivals():
    """A failed batch must not be masked by a background seeder that exited cleanly."""
    assert "RC=$?" in SHARED and "exit $RC" in SHARED


def test_itsm_asks_for_the_spread_priority_mix():
    assert "priority_mix: ${{ inputs.priority_mix }}" in RUN_ITSM
    line = next(ln for ln in RUN_ITSM.splitlines()
                if ln.strip().startswith("priority_mix:") and "default" in ln)
    assert "default: 'spread'" in line


def test_itsm_prep_steps_are_pack_guarded():
    """The other eight packs have no ServiceNow instance and must not run either step."""
    assert SHARED.count("if: inputs.pack == 'itsm'") == 2


def test_prep_steps_carry_the_servicenow_credentials():
    """Both steps call the instance directly, so neither can rely on the run step's env."""
    assert SHARED.count("SERVICENOW_PASSWORD: ${{ secrets.SERVICENOW_PASSWORD }}") == 3


def test_the_batch_is_paced():
    assert '--pace "${{ inputs.pace }}"' in SHARED


def test_itsm_does_not_pace_because_the_journey_provides_the_waiting():
    """Pacing was the stand-in for a model that had no time in it. Now a ticket waits because it
    sat in the wrong team's queue or was parked on the caller. Pacing on top would put the old
    positional delay back alongside the modelled one, and the two would be indistinguishable."""
    line = next(ln for ln in RUN_ITSM.splitlines() if ln.strip().startswith("pace:") and "default" in ln)
    assert "default: '0'" in line


def test_itsm_works_several_tickets_at_once():
    """Sequentially, a ticket held five minutes delays every ticket behind it, so the delay lands
    on the wrong tickets and position decides the outcome again."""
    line = next(ln for ln in RUN_ITSM.splitlines()
                if ln.strip().startswith("concurrency:") and "default" in ln)
    assert "default: '4'" in line
    assert '--concurrency "${{ inputs.concurrency }}"' in SHARED


def test_the_shared_workflow_still_defaults_to_no_pacing():
    line = next(ln for ln in SHARED.splitlines() if ln.strip().startswith("pace:") and "default" in ln)
    assert "default: '0'" in line
