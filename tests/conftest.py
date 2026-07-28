import os
import random
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from engine.levers import LeverConfig
from engine.llm import LLM
from engine.types import RunContext
from packs import PACKS, get_pack


# Packs whose work items AND outcomes come from a system the simulation does not
# own. The shared fixture below drives every pack through tests that assume the
# sim owns truth: it can generate a work item offline, its clean run emits real
# signals, and that run passes the contract. All three are deliberately false for
# an external-system fleet, which has its own test module instead. Excluding a
# pack here is only legitimate if it declares owns_outcome = False, and
# test_itsm_pack.py enforces exactly that so this list cannot become a way to
# dodge a failing test.
EXTERNAL_PACKS = {"itsm"}


@pytest.fixture(params=[p for p in PACKS if p not in EXTERNAL_PACKS])
def pack(request):
    return get_pack(request.param)


def make_ctx(levers=None, seed=0, index=0, workflow="test"):
    rng = random.Random(seed)
    return RunContext(
        llm=LLM(offline=True),
        rng=rng,
        levers=levers or LeverConfig(),
        session_index=index,
        workflow=workflow,
        now=datetime.now(timezone.utc),
        offline=True,
    )
