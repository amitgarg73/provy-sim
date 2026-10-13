"""The ITSM pack's roster and contract must stay readable by the Provy setup wizard (argus#1699).

The wizard's code reader (argus web/app/api/onboarding/analyze-repo/route.ts) skips any file of
50,000 bytes or more and reads only the first 8,000 characters of each file it keeps. packs/itsm/pack.py
is about 64,000 bytes, so on 13 Oct 2026 the wizard read nothing real and guessed a generic roster.
The roster and contract live in packs/itsm/pipeline.py, which is small. This test fails if that file
grows past either limit, or if the roster and contract the wizard would see stop matching the pack.
"""
import re
from pathlib import Path

from packs.itsm import pipeline
from packs.itsm.pack import ItsmPack

PIPELINE = Path(pipeline.__file__)
WIZARD_MAX_BYTES = 50_000       # files at or above this are skipped by the wizard's reader
WIZARD_MAX_CHARS = 8_000        # characters read from each file it keeps


def wizard_names(content: str, budget: int = 2000) -> set[str]:
    """A port of argus web/lib/code-structure.ts collectNameLiterals: short name-shaped strings."""
    out: list[str] = []
    used = 0
    for _quote, value in re.findall(r"""(['"`])([^'"`\n\\]{1,32})\1""", content):
        v = value.strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9 _.]{0,31}", v):
            continue
        if re.search(r"\d{6,}", v) or "@" in v or "://" in v or len(v) < 2 or v in out:
            continue
        if used + len(v) + 2 > budget:
            break
        out.append(v)
        used += len(v) + 2
    return set(out)


def test_pipeline_file_is_under_both_wizard_limits():
    text = PIPELINE.read_text()
    assert len(text.encode()) < WIZARD_MAX_BYTES
    assert len(text) < WIZARD_MAX_CHARS


def test_pack_builds_its_roster_and_contract_from_the_file_the_wizard_reads():
    pack = ItsmPack()
    assert [(a.name, a.display_name, a.role, a.emoji, a.sort_order) for a in pack.agents()] \
        == [tuple(r) for r in pipeline.roster()]
    assert [(c.id, c.text, c.side, c.signal, c.op, c.threshold) for c in pack.contract()] \
        == [tuple(r) for r in pipeline.conditions()]


def test_every_agent_and_signal_survives_the_wizards_name_filter_inside_the_read_window():
    names = wizard_names(PIPELINE.read_text()[:WIZARD_MAX_CHARS])
    for agent_name, display_name, *_ in pipeline.roster():
        assert agent_name in names and display_name in names, (agent_name, display_name)
    for _id, _text, _side, signal, *_ in pipeline.conditions():
        assert signal in names, signal
