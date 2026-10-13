"""The ITSM desk as plain data: who the agents are and what a good incident looks like.

An AI service desk works real incidents in a ServiceNow instance. Five agents triage, route, search
the knowledge base, resolve and check the ticket. ServiceNow decides whether each resolution held.

Kept deliberately small. pack.py is over the setup wizard's 50,000-byte read limit, so the wizard
could not see the roster and guessed a generic one (argus#1699). Build agents() and contract() from
here; do not copy these rows anywhere else. provy-sim-control lib/packs.ts is the console's copy and
tests/test_console_contract_parity.py fails if the two separate.
"""


def roster() -> list[tuple]:
    """(agent_name, display_name, what it does, emoji, sort_order), in pipeline order."""
    return [
        ("triage", "Triage", "Reads the incident and decides what it is and how urgent it is.", "🗂️", 0),
        ("router", "Routing", "Decides which support group should own the incident.", "🧭", 1),
        ("knowledge", "Knowledge",
         "Searches the knowledge base and returns the article the fix should follow.", "📚", 2),
        ("resolver", "Resolution",
         "Diagnoses the incident from the retrieved article, applies a fix, and resolves the ticket.", "🛠️", 3),
        ("reviewer", "Closure Check",
         "Checks the resolution before it is committed and records what it expects to happen.", "✅", 4),
    ]


def conditions() -> list[tuple]:
    """(id, text, side, signal, op, threshold). Six are settled by ServiceNow, two are read off the trace."""
    return [
        ("c1", "Incident is resolved with a genuine fix on first attempt, not reopened or marked "
               "cannot-reproduce", "outcome", "resolution_genuine", "eq", True),
        ("c2", "First response is delivered within the agreed response time target",
         "outcome", "first_response_time_met", "eq", True),
        ("c3", "Incident stays resolved and is not reopened after closure",
         "outcome", "resolution_persists", "eq", True),
        ("c4", "Agent resolves the incident without escalation or handoff to another team",
         "outcome", "self_resolved", "eq", True),
        ("c5", "Diagnosis is grounded in a documented procedure and follows clear resolution steps",
         "trace", "procedure_followed", "eq", True),
        ("c6", "Incident is resolved within the agreed resolution time target",
         "outcome", "resolution_time_met", "eq", True),
        ("c7", "The procedure the fix followed is a real article that covers this symptom",
         "trace", "kb_article_valid", "eq", True),
    ]
