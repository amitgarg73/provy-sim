"""
Structural (layer 3) checks, derived from the run itself.

⛔ THE SIMULATOR HAS NEVER EMITTED ONE. EvalResult defaults to layer 4 and eval_pass() never
overrode it, so every sim fleet graded output quality and nothing else. Provy seeds a structural
catalogue for each fleet and has NO server-side evaluator for it — layer 3 results must be SENT.
So the catalogue sat enabled and ungraded on every sim tenant, which is exactly the state argus#671
now makes visible and argus#675 records on the live trading fleet.

⛔ THESE ARE DERIVED, NEVER DECLARED. Every check below is computed from the traces the run already
produced, so a lever that breaks the run breaks the check with no extra wiring. A pack that declares
its own structural results would drift from what it actually emitted, which is the defect this
module exists to stop simulating.

⛔ AND THEY ARE DOMAIN-FREE. No agent name, no pack name, no metric that only makes sense for one
vertical. A three-agent support fleet and a nine-agent claims fleet both get the same four, computed
the same way. Anything domain-specific belongs in the pack's contract, not here.

The four mirror what Provy's own seeded catalogue names, so the eval_name a sim fleet sends matches
the config row already sitting on it:

  pipeline_completion  session  every agent in the roster produced at least one step
  tool_success_rate    agent    share of that agent's tool calls that did not error
  decision_made        agent    the deciding agent actually recorded a decision
  exit_quality         session  the run did not end on a terminal the engine sets when it breaks
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from engine.types import EvalResult

if TYPE_CHECKING:                                  # pragma: no cover
    from engine.types import RunResult, AgentSpec

# ⛔ A WHITELIST OF GOOD EXITS CANNOT BE GENERIC, AND I SHIPPED ONE. The first version listed the
# terminal reasons that count as success ("completed", "resolved", "converged"...). Run against the
# teameight pack it failed exit_quality on 25 of 25 runs, because that pack's success terminal is
# "followed_up" and no English word list can hold every fleet's vocabulary. A support fleet says
# "resolved", a trading fleet says "intraday_entries_placed", a claims fleet says "paid". Provy's own
# rule is that the tenant owns its nouns.
#
# So the test is inverted: an exit is bad only when THE ENGINE ITSELF marked it broken. Those strings
# the simulator controls, so the list is closed and correct by construction. Anything else is the
# fleet's own vocabulary and is assumed good, which is the honest default — a terminal reason Provy
# has never seen is not evidence of failure.
BAD_EXITS = frozenset({"pipeline_break", "error", "crashed", "timeout", "aborted"})

ERROR_OUTCOMES = frozenset({"error", "failed", "timeout"})

# ⛔ A SKIP IS A RECORD OF NOT RUNNING, AND IT IS STILL A STEP. engine/levers._skip_propagation
# removes the bailing agent's real traces and APPENDS a skip step for it and for everyone downstream.
# So "the agent has at least one trace" is true for every agent in a fully broken pipeline, and
# pipeline_completion passed 60 of 60 runs while skips were being injected. Measured, not reasoned:
# the unit test used ABSENT steps, which is not the shape the simulator actually produces.
NON_RUN_STEP_TYPES = frozenset({"skip"})
NON_RUN_OUTCOMES = frozenset({"skipped"})


def _actually_ran(step) -> bool:
    """A step that is evidence the agent did work, rather than a record of it not doing any."""
    return (step.step_type or "") not in NON_RUN_STEP_TYPES and (step.outcome or "") not in NON_RUN_OUTCOMES

TOOL_SUCCESS_FLOOR = 0.8
STRUCTURAL_LAYER = 3


def _r(agent: str, name: str, score: float, passed: bool, reasoning: str, entity_id: str) -> EvalResult:
    return EvalResult(
        agent=agent, eval_name=name, score=round(score, 4), passed=passed,
        detail={"reasoning": reasoning, "observed": round(score, 4)},
        entity_id=entity_id, layer=STRUCTURAL_LAYER,
    )


def drop_evals_for_agents_that_did_not_run(result: "RunResult") -> list[EvalResult]:
    """
    Evals whose agent actually did work, plus every session-scoped one.

    ⛔ A PACK BUILDS ITS EVAL LIST FROM THE CLEAN RUN, AND A LEVER THAT REMOVES AN AGENT'S TRACES
    DOES NOT REMOVE ITS EVALS. Measured on a real teameight session: seven agents whose only step was
    a skip each carried a PASSING 0.9 quality eval. The session then scored 0.9 overall, which is
    what made argus#677 invisible — Provy's own break detector is gated on session quality, so a
    fully broken pipeline looked like a good one.

    ⛔ THIS IS THE SAME RULE THE STRUCTURAL CHECKS FOLLOW: a check that cannot run writes NO ROW. A
    fabricated pass for an agent that never executed is how a blind spot comes to look like health,
    and it is worse here because the sim is supposed to be generating ground truth.

    Session-scoped evals are kept: they describe the run, not an agent.
    """
    worked = {s.agent for s in result.traces if s.agent and _actually_ran(s)}
    return [e for e in result.evals if e.agent in worked or e.agent in ("session", "", None)]


def structural_evals(result: "RunResult", agents: list["AgentSpec"]) -> list[EvalResult]:
    """Every structural check this run supports. Order is stable for reproducible fixtures."""
    out: list[EvalResult] = []
    eid = result.entity_id
    steps = result.traces

    # ── pipeline_completion ────────────────────────────────────────────────────────────────────
    # ⛔ THE ROSTER IS THE DENOMINATOR, NOT THE AGENTS THAT HAPPENED TO RUN. Counting only agents
    # present makes a skipped agent invisible, which is the single most common real failure and the
    # one this check exists to catch.
    roster = [a.name for a in agents]
    ran = {s.agent for s in steps if s.agent and _actually_ran(s)}
    present = [a for a in roster if a in ran]
    coverage = len(present) / len(roster) if roster else 1.0
    missing = [a for a in roster if a not in ran]
    out.append(_r(
        "session", "pipeline_completion", coverage, not missing,
        f"{len(present)} of {len(roster)} agents ran"
        + (f"; {', '.join(missing)} did not" if missing else ""),
        eid,
    ))

    # ── tool_success_rate, per agent that used a tool ──────────────────────────────────────────
    # ⛔ AN AGENT THAT CALLED NO TOOL GETS NO ROW. A check that cannot run must write NOTHING rather
    # than a passing 1.0: a fabricated pass is how a blind spot comes to look like health, which is
    # the whole failure family behind argus#671.
    by_agent: dict[str, list] = {}
    for s in steps:
        if s.step_type == "tool_call" and s.agent:
            by_agent.setdefault(s.agent, []).append(s)
    for agent in roster:
        calls = by_agent.get(agent, [])
        if not calls:
            continue
        ok = sum(1 for c in calls if (c.outcome or "ok") not in ERROR_OUTCOMES)
        rate = ok / len(calls)
        out.append(_r(
            agent, "tool_success_rate", rate, rate >= TOOL_SUCCESS_FLOOR,
            f"{ok} of {len(calls)} tool calls succeeded", eid,
        ))

    # ── decision_made, on the last agent in the roster ─────────────────────────────────────────
    # The decider is the last agent by sort order: the one whose output the contract is graded on.
    if roster:
        decider = roster[-1]
        msgs = [s for s in steps if s.agent == decider and s.step_type == "agent_message"]
        decided = [m for m in msgs if (m.outcome or "") and (m.outcome or "") not in ERROR_OUTCOMES]
        out.append(_r(
            decider, "decision_made", 1.0 if decided else 0.0, bool(decided),
            f"{decider} recorded a decision" if decided
            else f"{decider} produced no decision ({len(msgs)} message(s))",
            eid,
        ))

    # ── exit_quality ───────────────────────────────────────────────────────────────────────────
    term = (result.terminal_reason or "").strip()
    # ⛔ AN UNSET TERMINAL IS NOT GOOD. A run that never recorded how it ended cannot claim a clean
    # exit, and that is distinct from one whose terminal is simply a word Provy has not seen before.
    good = bool(term) and term not in BAD_EXITS
    out.append(_r(
        "session", "exit_quality", 1.0 if good else 0.0, good,
        f"ended on '{term or 'unset'}'"
        + ("" if good else (", which the pipeline sets when it breaks" if term else ", no terminal reason recorded")),
        eid,
    ))
    return out
