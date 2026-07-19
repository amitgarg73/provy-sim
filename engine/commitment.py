"""Generic commitment-integrity engine, shared by the CI packs (Travel, RevOps,
Claims payout, Legal).

Same idea as the Stripe pack's MockStripe: the agent makes an outward commitment
and gets an OK receipt, so the agent and its trace believe the promise succeeded.
The settled system of record, read later via settlement(), may silently disagree —
the action never landed, settled a wrong amount, went to the wrong target, or posted
twice. Which of those happens EMERGES from behavioral injectors seeded off the run's
RNG, so even the harness must reconcile against the mock system of record to know the
truth. That is what makes each failure a real promise-vs-settlement divergence.

Domain-parameterized: each pack supplies its own injector table and the contract
signal each settlement shape corrupts, so one engine covers every domain. At most one
injector fires per commitment, so a broken run has one clear cause the scoreboard can
grade Provy's attribution against.

CommitmentPack layers the FULL generic + L1/L2 lever set on top (via L.apply, with the
settlement feed as the pack's own phase-A injector), so every CI pack is a superset:
its commitment-integrity failures plus everything the generic packs can inject.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from . import contract as C
from . import levers as L
from .pack import BasePack
from .types import InjectedFault, RunContext, RunResult, TraceStep

# The four settlement shapes and the scoreboard fault each maps to. Shared across domains so
# the scoreboard scores every CI pack's attribution the same way (see engine/scoreboard.py).
SHAPE_FAULT = {
    "unsettled":    "commitment_unsettled",     # the committed action never landed
    "wrong_amount": "commitment_wrong_amount",  # it settled, but for a different amount
    "wrong_target": "commitment_wrong_target",  # it settled, but to the wrong recipient/record
    "duplicate":    "commitment_duplicate",     # it posted twice
}


# How a settlement failure is GROUNDED in the run, and who is truly at fault. Picked per diverged
# run (seeded RNG), DECOUPLED from the settlement shape on purpose: if the shape implied the culprit,
# attribution would be a lookup, not a test. Each cause plants the footprint Provy's own two-tier
# attribution can legitimately resolve, so the true culprit varies across agents AND is fair to score:
#   retriever_stale / retriever_fallback -> the upstream lookup served bad data. Footprint on the
#       retriever's tool call; Provy's deterministic (Tier-1) scan should name the retriever.
#   resolver_ignored -> the lookup surfaced a weak/ambiguous signal the decision agent ignored.
#       Footprint = a low match score; Provy's judge (Tier-2) should name the resolver.
#   blind_spot -> nothing in the run points anywhere (the classic "the world just disagreed"). No
#       footprint; the true culprit is undetermined, and the honest expectation is that Provy says so
#       rather than fabricating one.
_CAUSE_WEIGHTS = [
    ("retriever_stale", 0.25),
    ("retriever_fallback", 0.20),
    ("resolver_ignored", 0.30),
    ("blind_spot", 0.25),
]


def _pick_cause(rng) -> str:
    r = rng.random()
    acc = 0.0
    for name, w in _CAUSE_WEIGHTS:
        acc += w
        if r < acc:
            return name
    return _CAUSE_WEIGHTS[-1][0]


def _find_step(result: RunResult, agent: str, step_type: str) -> Optional[TraceStep]:
    for s in result.traces:
        if s.agent == agent and s.step_type == step_type:
            return s
    return None


@dataclass
class Injector:
    name: str          # lever name, e.g. 'not_ticketed'
    shape: str         # one of SHAPE_FAULT
    reason: str        # machine reason, e.g. 'ticket_never_issued'
    plain: str         # plain-language narration for the demo


@dataclass
class SoRSettlement:
    """What actually settled, read later by the settlement feed. The truth."""
    settled: bool
    amount_settled: float
    shape: Optional[str]
    reason: str
    injector: Optional[str]


class MockSoR:
    """Domain-parameterized mock system of record. Deterministic given the RNG. Rolls at
    most one injector per ref (one clear cause per run); everything else settles clean."""

    def __init__(self, rng, rates: dict[str, float], injectors: list[Injector]):
        self.rng = rng
        self.rates = rates
        self.injectors = injectors
        self._store: dict[str, SoRSettlement] = {}

    def commit(self, ref: str, amount: float) -> None:
        """Make the commitment. The receipt is always OK to the caller; the settled fate is
        decided now (behaviorally) but only visible later via settlement()."""
        inj = self._roll()
        if inj is None:
            s = SoRSettlement(True, amount, None, "cleared", None)
        elif inj.shape == "unsettled":
            s = SoRSettlement(False, 0.0, "unsettled", inj.reason, inj.name)
        elif inj.shape == "wrong_amount":
            bad = round(amount * self.rng.choice([0.5, 0.9, 1.1, 1.25]), 2)
            s = SoRSettlement(True, bad, "wrong_amount", inj.reason, inj.name)
        else:  # wrong_target or duplicate: it settled, but to the wrong place or twice
            s = SoRSettlement(True, amount, inj.shape, inj.reason, inj.name)
        self._store[ref] = s

    def settlement(self, ref: str) -> SoRSettlement:
        return self._store.get(ref, SoRSettlement(False, 0.0, "unsettled", "not_found", None))

    def _roll(self) -> Optional[Injector]:
        for inj in self.injectors:
            rate = self.rates.get(inj.name, 0.0)
            if rate > 0 and self.rng.random() < rate:
                return inj
        return None


class CommitmentPack(BasePack):
    """Base for commitment-integrity packs. The pack builds a clean claim (all agents correct,
    all evals pass, the commitment reported OK); this base then applies the full generic +
    L1/L2 lever set with the settlement feed folded into the one-primary-cause-per-run
    exclusion, exactly like the Stripe pack.

    A concrete pack implements the domain surface — generate_work_item, agents, contract,
    lever_manifest, build_clean_run — plus:
      injectors()   -> its settlement injector table
      settle_map()  -> {'promise': <the 'both' settled signal>, '<shape>': <signal that shape corrupts>}
    and, optionally, commit_ref/commit_amount and the narration hooks.
    """
    settle_tool: str = "settlement"

    # ── to implement ──────────────────────────────────────────────────────────
    def injectors(self) -> list[Injector]:
        raise NotImplementedError

    def settle_map(self) -> dict:
        raise NotImplementedError

    # ── optional overrides ──────────────────────────────────────────────────────
    def commit_ref(self, item) -> str:
        return self.entity_id(item)

    def commit_amount(self, item) -> float:
        return float(item.get("amount", 0.0)) if isinstance(item, dict) else 0.0

    def settle_agent(self) -> str:
        # The agent that made the outward commitment is the culprit the scoreboard scores.
        return self.lever_manifest().resolver_agent

    def clean_narration(self, amount: float) -> str:
        return "Settlement check: the commitment cleared. Promise kept."

    def fault_narration(self, st: SoRSettlement, cause: str = "blind_spot") -> str:
        inj = next((i for i in self.injectors() if i.name == st.injector), None)
        plain = inj.plain if inj else st.reason
        why = {
            "retriever_stale": " The upstream lookup had served stale data.",
            "retriever_fallback": " The upstream lookup had served a fallback value.",
            "resolver_ignored": " The decision agent had ignored a weak-match warning.",
            "blind_spot": " Nothing in the run pointed at a cause.",
        }.get(cause, "")
        return f"Settlement check: {plain}. The agent reported success, reality disagrees.{why}"

    # ── shared run + settle ───────────────────────────────────────────────────
    def _sor_rates(self, levers) -> dict[str, float]:
        out: dict[str, float] = {}
        for i in self.injectors():
            s = levers.get(i.name)
            out[i.name] = s.rate if s else 0.0
        return out

    def run_pipeline(self, item, gt, ctx: RunContext) -> RunResult:
        r = self.build_clean_run(item, gt, ctx)
        m = self.lever_manifest()
        ref = self.commit_ref(item)
        amount = self.commit_amount(item)
        sor = MockSoR(ctx.rng, self._sor_rates(ctx.levers), self.injectors())

        def _settlement_injector(result: RunResult, _ctx: RunContext,
                                 primary_fired: bool) -> Optional[InjectedFault]:
            # A generic lever already shaped this run, so the settlement feed stands down
            # (one primary cause per run keeps attribution 1:1). Otherwise roll the settled
            # fate and let reality disagree with the claim.
            if primary_fired:
                return None
            sor.commit(ref, amount)
            return self._settle(result, ref, amount, sor, _ctx)

        L.apply(r, gt, m, self.contract(), ctx.levers, ctx, pack_injector=_settlement_injector)

        # Stamp the Estimated signals on the reviewer's closing message so the Estimated side
        # of every 'both' condition is readable on a real trace.
        for t in r.traces:
            if t.agent == m.reviewer_agent and t.step_type == "agent_message":
                t.payload_extra.update(r.estimated_signals)
                t.payload_extra["confidence"] = r.confidence
                break
        return r

    def _settle(self, r: RunResult, ref: str, amount: float, sor: MockSoR,
                ctx: RunContext) -> Optional[InjectedFault]:
        eid = r.entity_id
        m = self.lever_manifest()
        promise_agent = self.settle_agent()   # owns the settlement-check trace (the promise-maker)
        st = sor.settlement(ref)

        if st.injector is None:
            r.traces.append(TraceStep(
                agent=promise_agent, step_type="tool_call", tool_name=self.settle_tool,
                tool_input={"ref": ref},
                tool_output={"settled": True, "amount_settled": st.amount_settled, "reason": st.reason},
                outcome="ok", entity_id=eid,
                payload_extra={"narration": self.clean_narration(amount)}))
            return None

        # A settlement failure emerged. Flip the affected Real signal only; the Estimated side
        # (the claim) stays good, which is exactly the divergence. bad_value handles either
        # phrasing of the condition (eq True vs eq False).
        smap = self.settle_map()
        idx = C.signal_index(self.contract())
        target_signal = smap["promise"] if st.shape == "unsettled" else smap[st.shape]
        c = idx.get(target_signal)
        if c is not None:
            r.real_signals[target_signal] = C.bad_value(c)

        # Ground the failure in the trace and pick the TRUE culprit, varied across agents so
        # attribution is a real test (blind spot -> culprit None).
        cause, culprit = self._plant_cause(r, m, ctx)

        r.traces.append(TraceStep(
            agent=promise_agent, step_type="tool_call", tool_name=self.settle_tool,
            tool_input={"ref": ref},
            tool_output={"settled": st.settled, "amount_settled": st.amount_settled, "reason": st.reason},
            outcome="ok", entity_id=eid,
            payload_extra={"narration": self.fault_narration(st, cause)}))

        return InjectedFault(
            SHAPE_FAULT[st.shape], culprit, "commitment_integrity",
            {"reason": st.reason, "shape": st.shape, "cause": cause,
             "promised_amount": amount, "settled_amount": st.amount_settled})

    def _plant_cause(self, r: RunResult, m, ctx: RunContext) -> tuple[str, Optional[str]]:
        """Ground the settlement failure in the trace so Provy's attribution is TESTABLE, and vary
        the true culprit across agents. Returns (cause, culprit_agent | None for a blind spot). The
        footprint lands on the upstream retriever's tool call, which every CI pack emits."""
        cause = _pick_cause(ctx.rng)
        tool = _find_step(r, m.retriever_agent, "tool_call")
        if tool is None:                       # no lookup to ground it on -> honest blind spot
            return "blind_spot", None
        if cause == "retriever_stale":
            as_of = (ctx.now - timedelta(days=400)).date().isoformat()
            tool.tool_output = {**(tool.tool_output or {}), "as_of": as_of, "note": "stale index"}
            return cause, m.retriever_agent
        if cause == "retriever_fallback":
            tool.tool_output = {**(tool.tool_output or {}), "from_cache": True, "note": "served fallback"}
            return cause, m.retriever_agent
        if cause == "resolver_ignored":
            tool.tool_output = {**(tool.tool_output or {}), "match_score": 0.28, "note": "weak match"}
            msg = _find_step(r, m.resolver_agent, "agent_message")
            if msg is not None:
                msg.payload_extra["confidence"] = "HIGH"
            return cause, m.resolver_agent
        return "blind_spot", None
