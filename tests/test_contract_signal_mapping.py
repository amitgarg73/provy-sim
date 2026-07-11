"""Every contract condition must be signal-mapped (no prose-only condition) so
it grades method='deterministic'. And every signal must be emitted by a clean
run so the Real/Estimated sides are actually readable."""
import random

import pytest

from engine.contract import bad_value, good_value, meets
from conftest import make_ctx

VALID_SIDES = {"outcome", "trace", "both"}
VALID_OPS = {"eq", "gt", "gte", "lt", "lte"}


def test_every_condition_is_signal_mapped(pack):
    for c in pack.contract():
        assert c.signal and isinstance(c.signal, str), f"{c.id} has no signal (prose-only)"
        assert c.side in VALID_SIDES, f"{c.id} bad side {c.side}"
        assert c.op in VALID_OPS, f"{c.id} bad op {c.op}"
        assert c.threshold is not None


def test_good_bad_values_grade_correctly(pack):
    for c in pack.contract():
        # eq works with booleans-as-1/0 too
        assert meets(c, good_value(c)) is True, f"{c.id} good value must pass"
        assert meets(c, bad_value(c)) is False, f"{c.id} bad value must fail"


def test_clean_run_emits_every_signal(pack):
    rng = random.Random(3)
    item, gt = pack.generate_work_item(rng)
    ctx = make_ctx(seed=3)
    run = pack.build_clean_run(item, gt, ctx)
    for c in pack.contract():
        present = c.signal in run.estimated_signals or c.signal in run.real_signals
        assert present, f"clean run does not emit signal {c.signal} for {c.id}"
    # A clean run passes everything.
    for c in pack.contract():
        if c.side in ("outcome", "both"):
            assert meets(c, run.real_signals.get(c.signal))
        if c.side in ("trace", "both"):
            assert meets(c, run.estimated_signals.get(c.signal))


def test_eval_names_are_slugs(pack):
    """Eval names must match the ag_eval_configs slug pattern used at onboarding."""
    import re
    slug = re.compile(r"^[a-z0-9_]{1,50}$")
    ctx = make_ctx(seed=1)
    item, gt = pack.generate_work_item(random.Random(1))
    run = pack.build_clean_run(item, gt, ctx)
    assert run.evals, "clean run must emit L4 evals"
    for ev in run.evals:
        assert slug.match(ev.eval_name), f"eval_name {ev.eval_name} is not a valid slug"
        assert slug.match(ev.agent)
