"""The Servicely desk as plain data: who the agents are and what a good incident looks like.

The same five agents as the ITSM desk working real incidents in a Servicely instance. Servicely decides
whether each resolution held. Kept small on purpose, like packs/itsm/pipeline.py: the setup wizard reads
this file and not pack.py. provy-sim-control lib/packs.ts is the console's copy and
tests/test_console_contract_parity.py fails if the two separate.

No SLA conditions: the demo instance has no SLA targets configured, so Servicely settles no response or
resolution time. A condition nobody can settle would show as a permanent gap, not a finding.
"""
from packs.itsm.pipeline import roster as _itsm_roster


def roster() -> list[tuple]:
    """(agent_name, display_name, what it does, emoji, sort_order), in pipeline order."""
    return list(_itsm_roster())


def conditions() -> list[tuple]:
    """(id, text, side, signal, op, threshold). Three are settled by Servicely, two are read off the trace."""
    return [
        ("c1", "Incident is resolved with a genuine fix on first attempt, not reopened or marked "
               "cannot-reproduce", "outcome", "resolution_genuine", "eq", True),
        ("c3", "Incident stays resolved and is not reopened after closure",
         "outcome", "resolution_persists", "eq", True),
        ("c4", "Agent resolves the incident without escalation or handoff to another team",
         "outcome", "self_resolved", "eq", True),
        ("c5", "Diagnosis is grounded in a documented procedure and follows clear resolution steps",
         "trace", "procedure_followed", "eq", True),
        ("c7", "The procedure the fix followed is a real article that covers this symptom",
         "trace", "kb_article_valid", "eq", True),
    ]
