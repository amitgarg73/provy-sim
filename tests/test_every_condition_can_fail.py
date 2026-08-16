"""Every contract condition must be able to FAIL, or it cannot be demonstrated.

⛔ WHY THIS EXISTS. A pack advertises a contract, and a demo shows the fleet breaking its promises.
A condition no lever can reach passes on every single run: it is on screen, it looks graded, and it
is decorative. Measured 16 Aug 2026 over 2700 dry runs, SEVEN conditions across five packs had a 0%
failure rate, and three more could not fail because their pack never configured the lever aimed at
them.

The two causes were different and both were invisible:

  1. `LeverManifest` aims four levers at four signals (correctness, secondary, policy, sla). A five
     or six condition contract orphans the rest. Fixed by `other_signals`, which names the orphans
     and the agent that owns each, plus the `condition_miss` lever that breaks one at a time.
  2. claims_payout, legal and revops never carried the generic levers, while stripe_support and
     travel did, so their own SLA/deadline conditions were unreachable.

This runs the packs offline and asserts the property directly, because nothing else does: the suite
was fully green the whole time this was true.
"""
import random
from collections import Counter
from datetime import datetime, timezone

import pytest

from conftest import make_ctx
from config.workflows import get_workflow
from engine.contract import meets
from engine.levers import LeverConfig
from engine.llm import LLM
from engine.types import RunContext
from packs import PACKS, get_pack

EXTERNAL = {"itsm"}          # settled by a real ServiceNow instance; cannot run offline
# ⛔ SIZED FOR THE RAREST LEVER, NOT FOR SPEED. revops' `duplicate_opportunity` runs at 0.02 and is
# phase-A exclusive, so its effective rate is nearer 1.5%. At 150 runs it fails to appear about a
# fifth of the time, which would make this test flaky and get it deleted. 500 runs puts the chance of
# a false failure under 1%. The whole sweep is offline and takes a few seconds per pack.
SEEDS = 20
COUNT = 25


def _sweep(name: str):
    rates = dict(get_workflow(name).lever_rates)
    contract = get_pack(name).contract()
    fails, passes, levers = Counter(), Counter(), Counter()
    for seed in range(SEEDS):
        pack = get_pack(name)
        pack.run_nonce = f"t{seed}"
        rng = random.Random(seed)
        for i in range(COUNT):
            item, gt = pack.generate_work_item(rng)
            ctx = RunContext(llm=LLM(offline=True), rng=rng, levers=LeverConfig(rates),
                             session_index=i, workflow=name,
                             now=datetime.now(timezone.utc), offline=True)
            r = pack.run_pipeline(item, gt, ctx)
            for f in r.faults:
                levers[f.lever] += 1
            for c in contract:
                val = (r.estimated_signals if c.side == "trace" else r.real_signals).get(c.signal)
                (passes if meets(c, val) else fails)[c.id] += 1
    return contract, fails, passes, levers


@pytest.mark.parametrize("name", sorted(p for p in PACKS if p not in EXTERNAL))
def test_every_condition_can_both_pass_and_fail(name):
    contract, fails, passes, _ = _sweep(name)
    unreachable = [f"{c.id} ({c.signal})" for c in contract if fails[c.id] == 0]
    assert not unreachable, (
        f"{name}: these conditions NEVER fail across {SEEDS * COUNT} runs, so the fleet can show the "
        f"promise but never show it broken: {unreachable}")
    never_pass = [f"{c.id} ({c.signal})" for c in contract if passes[c.id] == 0]
    assert not never_pass, f"{name}: these conditions never pass, which is broken not chaotic: {never_pass}"


@pytest.mark.parametrize("name", sorted(p for p in PACKS if p not in EXTERNAL))
def test_declared_signal_owners_are_real(name):
    """`other_signals` drives ATTRIBUTION, so a typo would name an agent that does not exist."""
    pack = get_pack(name)
    agents = {a.name for a in pack.agents()}
    signals = {c.signal for c in pack.contract()}
    for sig, agent in (pack.lever_manifest().other_signals or {}).items():
        assert sig in signals, f"{name}: other_signals names '{sig}', which is not a contract signal"
        assert agent in agents, f"{name}: '{sig}' is owned by '{agent}', which is not an agent"


@pytest.mark.parametrize("name", sorted(p for p in PACKS if p not in EXTERNAL))
def test_the_fleet_still_mostly_succeeds(name):
    """A fleet failing most of its work reads as broken rather than as a fleet worth watching."""
    contract, fails, passes, _ = _sweep(name)
    total = sum(fails[c.id] + passes[c.id] for c in contract) / max(1, len(contract))
    worst = max(fails[c.id] for c in contract) / max(1, total)
    assert worst < 0.75, f"{name}: one condition fails {worst:.0%} of the time, which is not a fleet"
