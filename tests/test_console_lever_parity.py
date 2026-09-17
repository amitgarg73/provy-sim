"""The levers a pack READS and the levers the console OFFERS must be the same set.

⛔ WHY THIS EXISTS (argus#644). The console had no `itsm` entry in `_CI_DEFAULTS`, so
`defaultLeverConfig('itsm')` fell through to the generic block and `/api/levers/[workflowId]`
served it. The sim REPLACES its local defaults with whatever the console returns
(`if remote: return LeverConfig(remote)` in config/workflows.py), so every ITSM lever fell back to
the hardcoded default inside `_rate()`.

Nothing looked wrong, and that is the point: those hardcoded defaults happen to equal the numbers in
`_ITSM_RATES`, so the fleet behaved identically whether the console was in the loop or not. The only
thing broken was the ability to CHANGE it, and eight scenario presets that wrote levers the pack
never read came back as clean runs.

The contract has this guard already (test_console_contract_parity.py). Levers did not.

Skipped rather than failed when the console repo is not checked out beside this one.
"""
import os
import subprocess
import re

import pytest

from config.workflows import WORKFLOWS
from packs import PACKS

CONSOLE = os.environ.get(
    "PROVY_SIM_CONTROL",
    os.path.expanduser("~/Claude Projects/provy-sim-control"),
)
LEVERS_TS = os.path.join(CONSOLE, "lib", "levers.ts")

# The lever names a pack's Python actually reads, which is the only definition that matters at run
# time. ITSM asks for its own error rates by name through `_rate()`; every other pack goes through
# the shared engine and its lever_rates dict is the list.
_RATE_CALL = re.compile(r'_rate\(\s*ctx\s*,\s*"(\w+)"')


def _console_lever_names() -> set:
    if not os.path.exists(LEVERS_TS):
        pytest.skip(f"provy-sim-control not checked out at {CONSOLE}")
    ts = open(LEVERS_TS).read()
    block = ts[ts.index("const LEVER_NAMES"):ts.index("] as const;")]
    return set(re.findall(r"'(\w+)'", block))


def _names_read_by(pack: str) -> set:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "packs", pack, "pack.py")
    read = set(_RATE_CALL.findall(open(path).read())) if os.path.exists(path) else set()
    return read | set(WORKFLOWS[pack].lever_rates) if pack in WORKFLOWS else read


def _console_reachable_on(pack: str) -> set:
    """The levers `defaultLeverConfig(pack)` actually returns, via the console's own TypeScript.

    ⛔ PARSING THE TS WOULD BE A FOURTH COPY OF THE RULES. `defaultLeverConfig` branches on itsm, on
    the commitment-integrity packs and on support, and reimplementing that branching here to check it
    is the bug this test exists to catch, written twice. So the console answers for itself.
    """
    out = subprocess.run(
        ["npx", "tsx", "-e",
         "import{defaultLeverConfig}from'./lib/levers';"
         f"console.log(Object.keys(defaultLeverConfig('{pack}')).join(' '))"],
        cwd=CONSOLE, capture_output=True, text=True, timeout=180,
    )
    if out.returncode != 0:
        pytest.skip(f"could not run the console's own lever config: {out.stderr[-200:]}")
    return set(out.stdout.split())


@pytest.mark.parametrize("pack", sorted(PACKS))
def test_every_lever_a_pack_reads_is_reachable_on_that_pack(pack):
    """⛔ DECLARED IS NOT REACHABLE, AND THE OLD TEST ONLY CHECKED DECLARED.

    `LEVER_NAMES` is the console's vocabulary; `defaultLeverConfig(pack)` is what the lever editor
    renders, what `packCapabilities` reads, and what decides whether a scenario or journey aimed at a
    lever is offered or refused. A name can sit in the vocabulary and be reachable on nothing.

    It did. All five of support's evidence levers — escalation_refused, fabricated_policy,
    ok_but_empty, reversed_on_appeal, retry_loop — were in LEVER_NAMES, so the test below passed,
    while support's own block never listed them. The pack has read them the whole time; the console
    could not dial one, and a scenario aimed at one was refused as "this pack has no mechanism for
    it" when it has had one all along.
    """
    missing = sorted(_names_read_by(pack) - _console_reachable_on(pack))
    assert not missing, (
        f"{pack} reads levers the console cannot reach on {pack}, so they cannot be dialled there "
        f"and any scenario or journey aimed at them is refused: {missing}"
    )


@pytest.mark.parametrize("pack", sorted(PACKS))
def test_every_lever_a_pack_reads_is_declared_in_the_console(pack):
    """A lever the pack reads but the console does not know is a dial that cannot be moved."""
    declared = _console_lever_names()
    missing = sorted(_names_read_by(pack) - declared)
    assert not missing, (
        f"{pack} reads levers the console has never heard of, so they cannot be dialled and any "
        f"scenario aimed at them does nothing: {missing}"
    )


def test_itsm_reads_its_own_error_rates_and_none_of_the_shared_engine():
    """⛔ THE SPECIFIC SHAPE OF #644. ITSM does not run the shared lever engine at all.

    If this ever starts failing because ITSM reads `silent_wrong`, the console's special case in
    `defaultLeverConfig` is wrong too and both move together.
    """
    read = _names_read_by("itsm")
    assert {"misclassify", "misroute", "weak_fix", "overconfidence", "bad_article"} <= read, read
    shared = {"silent_wrong", "silent_drift", "quality_degrade", "tool_fault", "sla_breach"}
    assert not (read & shared), f"itsm should read none of the shared engine, reads {read & shared}"
