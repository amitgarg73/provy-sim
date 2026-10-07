"""The context-manifest simulation sets (#1505, lane L4). ADDS a file; replaces nothing in provy-sim.

Everything about a set that is a CHOICE lives here: its role, its seed, its fleets, how many faults
and decoys each fleet plants. engine/context.py turns a spec into a session plan, and the plan is the
ground truth. Nothing here is read by Provy and nothing here is a threshold.

The sets, and what each one is for (PROVENANCE.md says which are clean hold-outs):

  CM-A  development. May be inspected and tuned on.
  CM-B  hold-out, broad: every fault kind on every route family. The recall read.
  CM-C  hold-out, clean-heavy: three fault fleets and one fleet with no fault at all.
  CM-D  hold-out, quiet-day: one fleet whose retrieval is empty on about 6% of steps by design.
  CAP-A, CAP-B  the two capture fleets for the UX lane (L6): manifests with every fault kind, and none.

SIZING ARITHMETIC (the acceptance bars in SPEC 12.5 need this to mean something):

  A hold-out fleet runs FLEET_SESSIONS = 180 sessions. A fault fleet plants per fleet
  stale 6 + unapproved 6 + empty 4 + instruction 3 = 19 faults, so
    CM-B: 4 fault fleets            = 76 faults
    CM-C: 3 fault fleets + 1 clean  = 57 faults
    CM-D: 3 fault fleets + 1 quiet  = 57 faults
  Each is above the 40 the brief asks for. A bar of "over 60%" on 40 faults moves by one fault at
  2.5 points, and the per-kind counts (about 16 to 24 per set for the three check kinds) are
  stated in the scorer's output so nobody reads a percentage without its n.
  Clean sessions (no fault, decoys not counted): 720 - 76 = 644 in CM-B, 720 - 57 = 663 in CM-C and
  CM-D. A 5% false alarm bar on 644 sessions is 32 sessions; the 95% upper bound of a zero
  count is 3/644 = 0.5%, so a build with no false alarm can be told from one at 5% by a wide margin.

  The learned checks need history before they can speak: retrieval_empty wants at least 20 other
  recorded retrievals on the step, instructions_changed wants a hash on two earlier sessions. Every
  fault is therefore planted at session index WARMUP_SESSIONS (40) or later, and an empty is planted
  on a step that retrieves non-empty on at least 97% of the fleet's sessions, so the learner's own
  95% line is not crossed by the plan.
"""
from __future__ import annotations

WARMUP_SESSIONS = 40
FLEET_SESSIONS = 180
DEV_FLEET_SESSIONS = 120
CAPTURE_SESSIONS = 60

# The fleet's declared limit and approved list: the two things a customer states on Guardrails
# (SPEC 5). The simulator needs them to PLANT a fault; Provy never sees these constants, the lead
# declares them on the fleet from DECLARATIONS below.
LIMIT_DAYS = 30
APPROVED_SOURCES = ["policy-kb", "faq-*", "records-api", "agent-memory"]

# A share of faulted sessions that settle badly (the rest settle well, so a stale fact with a good
# outcome exists to prove it raises no incident from the cause route), and a background miss rate
# with no context cause behind it.
FAULT_BAD_SETTLE_SHARE = 0.7
BACKGROUND_FAIL_RATE = 0.06
WORSE_CHANGE_FAIL_RATE = 0.30     # after an instruction change whose stated effect is "worse"

KINDS = ("stale", "unapproved", "empty", "instruction")
FAULT_COUNTS = {"stale": 6, "unapproved": 6, "empty": 4, "instruction": 3}
DEV_FAULT_COUNTS = {"stale": 10, "unapproved": 10, "empty": 6, "instruction": 3}

# Decoys, per fleet. Each LOOKS like a fault and is not. The route a decoy may use is decided in
# engine/context.py (DECOY_ROUTES) because it depends on what a route can carry.
FLEET_DECOYS = {
    "at_limit": 4,            # a fact exactly at the limit: the check is "older than", so it passes
    "stale_unused": 4,        # an old fact the agent retrieved and set aside (used = false)
    "unapproved_unused": 3,   # an unlisted source, set aside (used = false)
    "case_source": 3,         # an approved source spelled in a different case (see PROVENANCE: spec conflict)
    "input_old": 2,           # an old item of kind input: the request itself has no age to judge
    "oi_empty_unknown": 4,    # a retriever span with no documents and nothing saying zero: UNKNOWN, not empty
}
QUIET_EMPTY_RATE = 0.06      # CM-D quiet fleet: share of retrieval steps that are legitimately empty

# One fleet per door family. A route is a finer thing than a door: it is the wire form. Weights are
# a repeating pattern, so a fleet exercises every route of its family in rotation.
FLEET_ROUTES = {
    "rest": ["rest", "sdk"],
    "otlp_native": ["otlp_native"],
    "otlp_conv": ["otlp_convention"],
    "log": ["log_line", "log_line", "log_map", "log_line"],
}
FLEET_LABELS = {
    "rest": "Context Sim REST (SIMULATED)",
    "otlp_native": "Context Sim OTLP native (SIMULATED)",
    "otlp_conv": "Context Sim OTLP conventions (SIMULATED)",
    "log": "Context Sim log door (SIMULATED)",
}


def _fleet(key, sessions, counts, decoys=True, quiet=False):
    return {
        "key": key, "label": FLEET_LABELS[key], "routes": FLEET_ROUTES[key], "sessions": sessions,
        "faults": dict(counts), "decoys": dict(FLEET_DECOYS) if decoys else {}, "quiet": quiet,
    }


NO_FAULTS = {k: 0 for k in KINDS}

SETS = {
    "CM-A": {
        "role": "development", "hold_out": False, "seed": "cm-a-2026-10-02",
        "fleets": [_fleet(k, DEV_FLEET_SESSIONS, DEV_FAULT_COUNTS) for k in FLEET_ROUTES],
    },
    "CM-B": {
        "role": "hold-out: recall on every fault kind and route", "hold_out": True, "seed": "cm-b-2026-10-02",
        "fleets": [_fleet(k, FLEET_SESSIONS, FAULT_COUNTS) for k in FLEET_ROUTES],
    },
    "CM-C": {
        "role": "hold-out: clean-heavy, one fleet with no fault at all", "hold_out": True, "seed": "cm-c-2026-10-02",
        "fleets": [_fleet("rest", FLEET_SESSIONS, FAULT_COUNTS), _fleet("otlp_native", FLEET_SESSIONS, FAULT_COUNTS),
                   _fleet("otlp_conv", FLEET_SESSIONS, FAULT_COUNTS), _fleet("log", FLEET_SESSIONS, NO_FAULTS)],
    },
    "CM-D": {
        "role": "hold-out: quiet-day fleet that must raise nothing", "hold_out": True, "seed": "cm-d-2026-10-02",
        "fleets": [_fleet("rest", FLEET_SESSIONS, FAULT_COUNTS), _fleet("otlp_native", FLEET_SESSIONS, FAULT_COUNTS),
                   _fleet("otlp_conv", FLEET_SESSIONS, FAULT_COUNTS), _fleet("log", FLEET_SESSIONS, NO_FAULTS, quiet=True)],
    },
}

# The capture fleets for L6 (SPEC 12.6). Same shape, same session count, the second one with NO
# manifests. Both are REST-routed so the seed SQL can say ingest_door = 'rest' for every row.
CAPTURE = {
    "CAP-A": {
        "role": "capture: manifests, every fault kind, clean runs", "hold_out": False, "seed": "cap-a-2026-10-02",
        "label": "Context Capture (simulated)", "sessions": CAPTURE_SESSIONS, "manifests": True,
        "faults": {"stale": 6, "unapproved": 4, "empty": 3, "instruction": 1}, "silent": 9, "undated": 5,
    },
    "CAP-B": {
        "role": "capture control: the same shape, NO manifests", "hold_out": False, "seed": "cap-a-2026-10-02",
        "label": "Context Capture Control (simulated)", "sessions": CAPTURE_SESSIONS, "manifests": False,
        "faults": {"stale": 6, "unapproved": 4, "empty": 3, "instruction": 1}, "silent": 9, "undated": 5,
    },
}

# What the lead declares on each simulated fleet before emitting (names only, no key values).
# Guardrails rows are config.check of a layer 3 rule (SPEC 6.1); the two fleet_declarations keys are
# SPEC 5. The key env var is the NAME of the variable that holds the fleet's ingest key.
# The agents the generator plants on (engine/context.py AGENTS). A product rule has to name at least one thing to select by, and an agent is the thing a customer names.
GUARDRAIL_AGENTS = ("triage", "resolver", "reviewer")


def guardrail_rows(kinds=("context_fresh", "context_sources_in")):
    """The Guardrails rows to set on a fleet: one freshness row and one approved-list row PER AGENT, each selecting by agent ONLY.

    NO STEP TYPE IS NAMED, ON PURPOSE (L10, the founder's decision of 3 Oct 2026). A context check that names no step type and no tool reads the DECISION STEPS, `decision` and
    `agent_message`, the same steps the Context card's coverage counts. The generator plants the resolver's faults on an `agent_message` step and the others on `decision` steps, so
    these rows only find them all if the product's default is the one definition. They used to say `step_type: decision`, which missed the resolver's faults (L5 finding 4: 62 of 104).
    Nothing here changes what the generator makes: the ground-truth files and their SHA-256 hashes do not read this list.
    """
    rows = []
    for agent in GUARDRAIL_AGENTS:
        if "context_fresh" in kinds:
            rows.append({"check": {"kind": "context_fresh", "select": {"agent": agent}, "max_age_hours": LIMIT_DAYS * 24,
                                   "kinds": ["document", "memory"]}})
        if "context_sources_in" in kinds:
            rows.append({"check": {"kind": "context_sources_in", "select": {"agent": agent}, "allowed": APPROVED_SOURCES,
                                   "kinds": ["document", "memory", "tool_result"]}})
    return rows


DECLARATIONS = {
    "guardrails": guardrail_rows(),
    "fleet_declarations": {
        "log": {"context_log_fields": {"array": "retrieved", "agent_field": "agent", "source": "index", "id": "doc",
                                        "as_of": "updated", "used": "cited", "kind": "document"}},
        "otlp_conv": {"context_otlp": {"empty_retriever_means_zero": False}},
    },
}


# SPEC 12.6 step 5: the declared check rows for the capture fleets, set AFTER the BEFORE capture so the old
# build never draws a rule row it cannot run. Fleet B gets only the freshness rows.
CAPTURE_DECLARATIONS = {
    "CAP-A": guardrail_rows(),
    "CAP-B": guardrail_rows(("context_fresh",)),
}


def key_env_name(set_name: str, fleet_key: str) -> str:
    """The environment variable NAME that holds a fleet's ingest key. The value is never in this tree."""
    return "PROVY_KEY_" + (set_name + "_" + fleet_key).upper().replace("-", "_")
