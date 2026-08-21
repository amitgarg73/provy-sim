# Complaint → mechanism → lever → what Provy must do

Built 20–21 Aug 2026. Evidence and sources: `00-evidence.md`. Implementation: `engine/levers.py`,
`provy-sim-control/lib/levers.ts`, tests in `tests/test_evidence_levers.py`.

---

## 1. What "silent failure" meant in Provy before tonight

**It meant a bookkeeping act inside the simulator.**

Five of the six `silent_*` levers call the same helper, `_corrupt_correctness`, which writes a bad
value into `real_signals` and leaves `estimated_signals` good. Seven levers force
`confidence = 0.9`. They differ only in which trace field gets a decorative note: an `as_of` for
staleness, a `match_score` for unsupported, a `_skipped_step` for incomplete.

So the answer to *"what is a silent failure and what does it represent?"* was: **it represents the
simulator writing two different numbers into two different fields.** It is a definition by
construction. No practitioner would recognise it, because it describes nothing that happens to
anybody.

That is not a naming problem. It is why every silent-failure demo felt the same: it *was* the same.

## 2. And the simulator was a mirror, not a test

| Provy's verifiable detectors (`web/lib/attribution-methods.ts`) | The sim's tool shapes (`_TOOL_SHAPES`) |
|---|---|
| `errored` | `errored` |
| `empty_output` | `empty` |
| `fallback` | `fallback` |
| `stale` | `stale` |
| `reported_negative` | — |

**Four of five, name for name.** The simulator was built to produce exactly the faults Provy already
knows how to find. So "Provy named the cause" on a sim fleet was close to tautological, and the one
thing the product argues hardest for — **refusing to name a cause when the evidence does not support
one** — could almost never be demonstrated, because the sim could not manufacture a fault outside
the detector set.

⚠️ One honest exception: `silent_unsupported` plants a `match_score: 0.28` that only the judge tier
reads, and Provy treats `ignored_signal` as a hypothesis rather than a confident cause. That lever
did exercise a blind spot. It was the only one.

---

## 3. The mapping

Every row is a complaint or finding from a documented source, not an invention. **"Provy today"** is
my read of what the product would currently do with it, and each is a claim to be tested, not a
result.

### Support and ITSM — `support`, `stripe_support`, `itsm`, `teameight`

| What people actually complain about | Mechanism | New lever | Provy today |
|---|---|---|---|
| ⭐ "I typed *agent* four times and it would not let me through" (reported at 47.6% of consumers) | The item needed a human; the agent kept it and closed it. Deflection metrics improve while the person is stuck | `escalation_refused` | **Nothing.** No lever, no signal, no surface. The handoff requests sit in the trace and nothing reads them |
| "The ticket was closed and nothing changed" | Close recorded, underlying problem untouched, comes back later | `reversed_on_appeal`, and ITSM's existing `reopen_count` | Reconciliation catches it. This one Provy is genuinely built for |
| "It told me a refund policy that does not exist" | Retrieval returned nothing relevant; the agent asserted a specific rule and cited a document id never retrieved | `fabricated_policy` | Should catch: the cited id is checkable against what was retrieved. **Untested** |
| "It answered from an old article" | Source existed, was stale | `silent_staleness` (existing) | `stale` detector. Already covered |

### Coding agents — `claude_code`

| Finding | Mechanism | New lever | Provy today |
|---|---|---|---|
| ⭐ The largest failure cluster in the empirical study of failed agent PRs is **over-literal instruction following**, dominated by the harness line "DO NOT MODIFY: tests, configuration files" | The agent honours the constraint and ships a wrong result. It reads as compliance, not error | `overliteral_constraint` | **Nothing.** There is no defect in the trace. Only reconciliation can see it |
| "Bugs that do not show up until production, weeks after the AI swore the code was perfect" | Delayed settlement | `reversed_on_appeal` | The settlement-lag case. Provy's core argument |

### Insurance and claims — `claims`, `claims_payout`

| Finding | Mechanism | New lever | Provy today |
|---|---|---|---|
| ⭐ An **82% overturn rate on appeal** for Medicare Advantage prior-authorisation denials | The decision was final, defensible and complete when made. Reality arrives weeks later and reverses it | `reversed_on_appeal` | **The single best fit in the product.** Nothing in the run is wrong, so nothing at run time can catch it |
| Denials issued with no human reviewer; four states legislated against it in 2025 | Governance, not correctness | — | Out of scope for a lever; belongs in the contract |

### AIOps and RCA — `edwin`

| Finding | Mechanism | New lever | Provy today |
|---|---|---|---|
| "AI is a pattern-matching engine, not a reasoning engine" — good at surfacing what changed, cannot say which change caused it | The agent names the nearest correlated change and commits | `correlation_as_cause` | Should refuse to confirm the named cause. **This is Provy making the same argument about itself** |

⚠️ **I could not find documented practitioner complaints of AIOps naming a wrong root cause.**
Searched for it directly. The edwin blind-spot story is currently supported by our own fleet only,
and the summary says so rather than dressing a vendor guide up as evidence.

### Domain-independent — every pack

| Finding | Mechanism | New lever | Provy today |
|---|---|---|---|
| ⭐ "Tools return HTTP 200 while delivering empty or malformed payloads" — called the most damaging failure type in practice | A well-formed envelope with a hollow body. Status-based checks read it as healthy | `ok_but_empty` | ⚠️ `empty_output` may catch it, **may not**: the existing `tool_fault:empty` sets an error, and this deliberately does not. **Worth measuring first** |
| Retrieval returned the RIGHT document and the agent answered from its prior ("parametric bias") | The correct answer is in the trace, unused | `parametric_override` | **Would probably blame the retriever, which would be wrong.** The retriever did its job perfectly |
| Same call fires repeatedly on identical input | No recognition of repetition; token budget burns | `retry_loop` | Cost and token overlays should fire. Step-similarity is not measured |
| Contradictory signals, so the agent does nothing. No call, no output, **no error** | Absence is the only signal | `agent_paralysis` | **Nothing to find.** Anything reading spans for a defect sees a short, clean, cheap run |
| Long run pushes an earlier constraint out of context | The agent contradicts a fact it established itself, in the same run | `context_truncated` | The contradiction is in the trace and nothing reads across steps |
| Tool-calling fails **3–15%** of the time in production | Malformed arguments, wrong types, missing fields | *not built* | See open items |

---

## 4. What this changes about the demo

Before, every silent failure resolved to "Provy found the culprit", because every fault was one
Provy had a detector for. The new set deliberately contains cases where **the honest answer is that
there is nothing to find**:

- `reversed_on_appeal` — no run-time defect exists, even in principle
- `agent_paralysis` — nothing is emitted
- `overliteral_constraint` — the run looks like compliance
- `parametric_override` — the obvious culprit is the innocent one

**A simulator that can only produce detectable faults can never show a blind spot**, and refusing to
name a cause is the thing Provy does that nothing else does. Those four make the refusal
demonstrable for the first time.

---

## 5. Open, and deliberately not done tonight

1. **No pack's default rates were changed.** Every new lever ships at rate 0 until a fleet dials it.
   Nothing that runs today behaves differently. That was the safe scope for an unattended night.
2. **The malformed-argument lever is not built** (3–15% of production tool calls). It needs a schema
   on the tool definition, which the packs do not carry.
3. **⛔ Rates are not independent.** Phase-A levers are exclusive — the first to fire claims the run
   — so configured rates are upper bounds, and order in `_PHASE_A` decides who gets starved. New
   levers were appended, not inserted, so existing mixes are untouched.
4. **Prompt injection and memory poisoning are not modelled.** Both are in the published taxonomies.
   They are a security story rather than an outcome story, so they were left out on purpose.
5. **⛔ Nothing here has been run against a live Provy tenant.** The levers are proven to fire and to
   leave distinct traces. **What Provy actually DOES with each one is a claim in the table above and
   is unmeasured.** That is the next session's work, and it is the part that could still embarrass us.
