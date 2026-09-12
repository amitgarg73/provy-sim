"""A lever must break a DIFFERENT contract condition than the others, or a contract cannot tell
mechanisms apart.

⛔ THIS IS THE SETTLEMENT-SIDE TWIN OF `test_each_lever_leaves_a_distinct_trace_signature`, AND ITS
ABSENCE COST US A CUSTOMER-FACING ARTEFACT (#825). That test proved every lever leaves a distinct
TRACE. Nobody checked the side a contract actually grades. Measured 11 Sep 2026 on all 11 runnable
packs: in `support`, EIGHT different mechanisms failed `reopened_7d` and nothing else, so a brief
claiming four distinct failures were each caught was really one condition tested twenty-six times.
`claims_payout` was worse: 16 levers, 2 distinct outcomes.

⛔ IT IS A RATCHET, NOT A BAN, because some collapsing is genuinely right. Six flavours of "the agent
was quietly wrong" all settling as a reopened ticket is the truth about support, not a modelling bug.
What must never happen again is a NEW mechanism silently joining an existing pile, or a pile growing.
So the current shape is frozen in `settlement-inventory.json` and may only SHRINK.

Regenerate deliberately, never to clear a failure you did not intend:

    INVENTORY_WRITE=1 .venv/bin/python -m pytest tests/test_levers_settle_distinctly.py

⛔ `itsm` IS EXCLUDED AND CANNOT BE COVERED. It has no offline mode and needs a live ServiceNow, so
it cannot run in CI at all. Its levers are unguarded; that is a known hole, not an oversight.
"""
import collections
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.emitter import ProvyEmitter
from engine.levers import LeverConfig, _PHASE_A
from engine.llm import LLM
from engine.runner import BatchRunner
from packs import PACKS, get_pack

INVENTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settlement-inventory.json")
NEEDS_A_LIVE_SYSTEM_OF_RECORD = {"itsm"}
PACK_NAMES = sorted(set(PACKS) - NEEDS_A_LIVE_SYSTEM_OF_RECORD)
N = 80
SEED = 11


def _settlements(name: str) -> dict:
    """lever -> the set of contract signals it drives bad, as a stable string."""
    pack = get_pack(name)
    clean = dict(pack.clean_signals())
    em = ProvyEmitter(ingest_key="", base_url="http://localhost:0", capture=True)
    runner = BatchRunner(pack, LeverConfig({lv: 0.18 for lv in _PHASE_A}), emitter=em,
                         ledger=None, llm=LLM(offline=True), seed=SEED)
    seen = collections.defaultdict(collections.Counter)
    for o in runner.run_batch(N):
        r = o.result
        # The PRIMARY phase-A fault. Phase-B overlays (drift, calibration) ride on top of a run and
        # are not the mechanism under test; counting them would label most runs as drift.
        primary = next((f.lever for f in r.faults if f.lever in _PHASE_A), None)
        if primary is None:
            continue
        real = r.real_signals or {}
        bad = tuple(sorted(k for k, v in real.items() if k in clean and clean[k] != v))
        seen[primary][bad or ("<none>",)] += 1

    out = {}
    for lever, counter in seen.items():
        out[lever] = "/".join(counter.most_common(1)[0][0])
    return out


def _grouped(settlements: dict) -> dict:
    g = collections.defaultdict(list)
    for lever, sig in settlements.items():
        g[sig].append(lever)
    return {sig: sorted(levers) for sig, levers in g.items()}


def _measure_all() -> dict:
    return {name: _grouped(_settlements(name)) for name in PACK_NAMES}


if os.environ.get("INVENTORY_WRITE"):
    def test_regenerate_inventory():
        json.dump(_measure_all(), open(INVENTORY, "w"), indent=1, sort_keys=True)
        pytest.skip(f"rewrote {INVENTORY}")
else:
    @pytest.fixture(scope="module")
    def measured():
        return _measure_all()

    @pytest.mark.parametrize("name", PACK_NAMES)
    def test_no_new_or_larger_collapse(name, measured):
        frozen = json.load(open(INVENTORY)).get(name, {})
        now = measured[name]
        problems = []
        for sig, levers in now.items():
            was = frozen.get(sig)
            if was is None and len(levers) > 1:
                problems.append(
                    f"NEW collapse on '{sig}': {levers}. These mechanisms are indistinguishable to a "
                    f"contract. Aim one at the condition it actually breaks (see _settles_on), or "
                    f"regenerate deliberately if they genuinely settle the same way.")
            elif was is not None and len(levers) > len(was):
                added = sorted(set(levers) - set(was))
                problems.append(
                    f"'{sig}' grew from {len(was)} levers to {len(levers)}; {added} joined an "
                    f"existing pile instead of failing its own condition.")
        assert not problems, f"{name}:\n  " + "\n  ".join(problems)

    def test_the_inventory_covers_every_runnable_pack():
        frozen = json.load(open(INVENTORY))
        missing = sorted(set(PACK_NAMES) - set(frozen))
        assert not missing, f"no frozen settlement shape for {missing}; regenerate the inventory"

    def test_itsm_is_excluded_on_purpose_and_stays_named():
        # If itsm ever gains an offline mode, delete it from the exclusion set rather than leaving a
        # pack permanently unguarded because nobody remembered why it was skipped.
        assert NEEDS_A_LIVE_SYSTEM_OF_RECORD == {"itsm"}
