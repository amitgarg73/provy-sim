"""Import shim so these tests run both inside provy-sim (after the tree is copied across) and from
the argus repo, where the tree sits under docs/evidence/context-manifest/sim/.

Outside provy-sim there is no `engine/emitter.py` beside this file. The shim then puts the real provy-sim
checkout on sys.path READ ONLY (nothing is written to it) and extends the `engine`, `config` and `packs`
packages with this tree's directories, which is what copying the files across would do.

Set PROVY_SIM_ROOT to point at a different checkout.
"""
from __future__ import annotations

import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TREE = os.path.dirname(HERE)
SIM_ROOT = os.environ.get("PROVY_SIM_ROOT", "/Users/amitgarg/Claude Projects/provy-sim")


def install() -> None:
    if os.path.exists(os.path.join(TREE, "engine", "emitter.py")):
        # Running inside provy-sim itself.
        if TREE not in sys.path:
            sys.path.insert(0, TREE)
        return
    if not os.path.isdir(SIM_ROOT):
        raise RuntimeError(f"provy-sim not found at {SIM_ROOT}; set PROVY_SIM_ROOT")
    if SIM_ROOT not in sys.path:
        sys.path.insert(0, SIM_ROOT)
    sys.dont_write_bytecode = True
    for pkg in ("engine", "config", "packs"):
        mod = importlib.import_module(pkg)
        extra = os.path.join(TREE, pkg)
        if extra not in mod.__path__:
            mod.__path__.append(extra)
    # scripts is not a package in provy-sim; the tree's scripts are imported by path.
    if TREE not in sys.path:
        sys.path.append(TREE)


install()
