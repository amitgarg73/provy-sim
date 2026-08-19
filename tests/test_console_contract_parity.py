"""Every pack's contract is defined twice. This is the test that stops the two separating.

provy-sim RUNS a fleet; provy-sim-control PROVISIONS it, and it keeps its own copy of the contract,
the agent roster and the trace aliases in lib/packs.ts. `provisionFleet` and "Install contract"
both seed from THAT copy, so it is the authoritative one and these packs follow it. A fleet
provisioned from a drifted console copy grades against conditions the runner never emits, and the
failure looks like a Provy bug rather than a copy-paste one.

⛔ THIS EXISTED FOR EXACTLY ONE PACK AND ONLY THAT PACK NEVER DRIFTED (argus#638). The edwin-only
version of this test kept edwin honest for weeks while claims and crm quietly separated on `side`
and itsm separated almost entirely: itsm graded close_code / reopen_count / reassignment_count here
while the live PDI graded six differently-named conditions. Generalising the guard is the whole fix.

Skipped rather than failed when the console repo is not checked out beside this one, so the sim
stays testable on its own.
"""
import os
import re

import pytest

from packs import PACKS, get_pack

CONSOLE = os.environ.get(
    "PROVY_SIM_CONTROL",
    os.path.expanduser("~/Claude Projects/provy-sim-control"),
)
PACKS_TS = os.path.join(CONSOLE, "lib", "packs.ts")

# One condition, exactly as the console writes it. Deliberately strict: every one of the 52
# conditions is `op: 'eq'` with a boolean threshold today, and a line this does not match is a
# console change nobody here has accounted for. The count assertion below turns that into a
# failure instead of a silent skip.
_ROW = re.compile(
    r"\{ id: '(c\d+)', text: '([^']*)', side: '(\w+)', "
    r"signal: '(\w+)', op: '(\w+)', threshold: (true|false) \}"
)


def _console_block(name: str) -> str:
    """The console's source for one pack, from its key to the end of its definition."""
    if not os.path.exists(PACKS_TS):
        pytest.skip(f"provy-sim-control not checked out at {CONSOLE}")
    ts = open(PACKS_TS).read()
    key = f"  {name}: {{"
    if key not in ts:
        pytest.fail(f"provy-sim-control/lib/packs.ts has no {name} pack; provisioning would reject it")
    return ts.split(key, 1)[1]


def _console_contract(block: str) -> list[tuple]:
    contract_block = block.split("leverManifest", 1)[0]
    rows = [
        (i, t, s, sig, op, th == "true")
        for i, t, s, sig, op, th in _ROW.findall(contract_block)
    ]
    # ⛔ A PARSER THAT MISSES IS NOT A PASSING TEST. If the console adds a numeric threshold or a
    # second op this stops matching, and without this the pack would look identical to an empty
    # parse. Count the condition lines independently and insist they were all understood.
    declared = len(re.findall(r"\{ id: 'c\d+',", contract_block))
    assert declared == len(rows), (
        f"parsed {len(rows)} of {declared} console conditions; lib/packs.ts uses a shape this test "
        f"does not understand, so it can no longer prove anything"
    )
    return rows


@pytest.mark.parametrize("name", sorted(PACKS))
def test_console_copy_of_the_contract_has_not_drifted(name):
    block = _console_block(name)
    console = _console_contract(block)
    mine = [(c.id, c.text, c.side, c.signal, c.op, c.threshold) for c in get_pack(name).contract()]
    assert console == mine, f"the console's copy of the {name} contract has drifted from this one"


@pytest.mark.parametrize("name", sorted(PACKS))
def test_console_agent_roster_has_not_drifted(name):
    block = _console_block(name)
    roster = re.findall(r"agent_name: '(\w+)'", block.split("evalConfigs", 1)[0])
    assert roster == [a.name for a in get_pack(name).agents()], (
        f"the console's {name} agent roster has drifted from this one"
    )


@pytest.mark.parametrize("name", sorted(PACKS))
def test_console_trace_aliases_have_not_drifted(name):
    """⛔ THE ALIASES ARE THE THIRD THING THAT MUST MATCH. The console seeds them as `traceSignal`
    at provision, and a drifted copy binds a condition to a field the runs never emit, which
    produces a confidently wrong claim rather than an absent one."""
    block = _console_block(name)
    parts = block.split("traceAliases: {", 1)
    console = (
        dict(re.findall(r"(\w+):\s*'([^']+)'", parts[1].split("}", 1)[0]))
        if len(parts) > 1 else {}
    )
    assert console == get_pack(name).trace_aliases(), (
        f"the console's copy of the {name} trace aliases has drifted from this one"
    )
