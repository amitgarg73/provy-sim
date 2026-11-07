"""The console's list of access cases (provy-sim-control/lib/accessSend.ts) is a copy of the simulator's scenarios and batch weights. This stops the two separating:
a case the console offers that the simulator does not know would fail a real run half an hour in. Skipped when the console repo is not checked out beside this one."""
import os
import re

import pytest

from multi_vendor.batch import DEFAULT_WEIGHTS
from multi_vendor.scenarios import BY_KEY

CONSOLE = os.environ.get("PROVY_SIM_CONTROL", os.path.expanduser("~/Claude Projects/provy-sim-control"))
TS = os.path.join(CONSOLE, "lib", "accessSend.ts")

pytestmark = pytest.mark.skipif(not os.path.exists(TS), reason="provy-sim-control is not checked out beside provy-sim")


def _cases():
    src = open(TS).read()
    return [(m.group(1), int(m.group(2)), m.group(3) == "true")
            for m in re.finditer(r"\{ id: '(\w+)', weight: (\d+), holds: (true|false),", src)]


def test_the_console_offers_exactly_the_cases_the_simulator_has():
    assert {c[0] for c in _cases()} == set(BY_KEY) == set(DEFAULT_WEIGHTS)


def test_the_console_weights_and_expectations_match_the_simulator():
    for key, weight, holds in _cases():
        assert weight == int(DEFAULT_WEIGHTS[key]), key
        assert holds == BY_KEY[key].should_hold, key


def test_every_case_the_console_offers_can_run_through_the_servicely_desk():
    for key, _, _ in _cases():
        assert BY_KEY[key].servicely_ready, key
