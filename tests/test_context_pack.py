import os

import _context_path  # noqa: F401
from engine import context as C
from engine import context_truth as T
from packs.context_support.pack import ContextSupportPack

DATA = os.path.join(_context_path.TREE, "data")


def test_roster_and_contract_match_what_the_plans_emit():
    pack = ContextSupportPack()
    assert tuple(a.name for a in pack.agents()) == C.AGENTS
    crit = pack.contract()[0]
    plan = C.load_plans(os.path.join(DATA, T.truth_filename("CAP-A")))[0]
    assert crit.signal in plan["outcome"]["signals"] and crit.op == "eq" and crit.threshold is True
    assert pack.session_type == plan["session_type"]
    claim = next(s["claim"] for s in plan["steps"] if s.get("claim"))
    assert claim["signal"] == crit.signal


def test_the_pack_is_not_registered_in_the_real_registry():
    import packs
    assert "context_support" not in packs.PACKS
