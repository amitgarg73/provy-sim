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


@pytest.fixture(params=list(PACKS))
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
