# Sim pack rewiring — read this first

Run overnight, 20–21 Aug 2026. Everything below is committed and pushed to `provy-sim` and
`provy-sim-control`. **No fleet behaves differently yet** — every new lever ships at rate 0.

---

## You asked what a silent failure is. Here is the honest answer.

**Before tonight it meant the simulator writing two different numbers into two different fields.**

Five of the six `silent_*` levers call one helper, `_corrupt_correctness`: bad value into
`real_signals`, good value left in `estimated_signals`. Seven force `confidence = 0.9`. They differ
only in which trace field gets a decorative note — an `as_of` for staleness, a `match_score` for
unsupported, a `_skipped_step` for incomplete.

That is a definition by construction. It describes nothing that happens to anybody, which is why
every silent-failure demo felt like the same demo. It *was* the same demo, six times.

## And there is a worse structural problem underneath it

| Provy's verifiable detectors | The sim's tool shapes |
|---|---|
| `errored` · `empty_output` · `fallback` · `stale` · `reported_negative` | `errored` · `empty` · `fallback` · `stale` |

**Four of five, name for name.** The simulator was built to emit exactly the faults Provy already
knows how to detect. So "Provy found the culprit" on a sim fleet was close to tautological — and the
thing the product argues hardest for, **refusing to name a cause when the evidence does not support
one**, could almost never be shown, because the sim could not manufacture a fault outside the
detector set.

One honest exception: `silent_unsupported` plants a weak match score that only the judge tier reads.
It was the only lever that exercised a blind spot.

---

## What I did

Ten new levers, each taken from a documented production failure, each leaving a **different** trace
signature. Sources are in `00-evidence.md`; the full complaint-to-lever mapping is in `01-mapping.md`.

| Lever | The complaint it comes from |
|---|---|
| `escalation_refused` | ⭐ "I typed *agent* four times and it would not let me through." Reported at **47.6%** of consumers. **We had nothing for the single most-cited complaint in AI support** |
| `ok_but_empty` | A tool answers **200 with a hollow body**; the agent reasons on nothing. Called the most damaging failure type in practice |
| `reversed_on_appeal` | ⭐ **82% overturn rate on appeal** for Medicare Advantage denials. Correct at run time, reversed weeks later |
| `overliteral_constraint` | ⭐ The largest cluster in the empirical study of failed agent PRs: obeying "DO NOT MODIFY: tests, config" and shipping a wrong result. **Obedience as a failure mode** |
| `parametric_override` | The right document was retrieved, fresh and high-scoring, and the agent answered from its prior instead |
| `fabricated_policy` | A rule stated confidently, citing a document that no retrieval in the run returned |
| `correlation_as_cause` | The nearest change named as the cause, ranked by proximity, not evidence |
| `agent_paralysis` | No tool call, no output, **no error**. Only absence |
| `retry_loop` | The same call on identical input, no recognition, budget burning |
| `context_truncated` | The agent contradicts a fact it established earlier in the same run |

**Four of these have no run-time defect to find at all** — `reversed_on_appeal`, `agent_paralysis`,
`overliteral_constraint`, `parametric_override`. That is deliberate. A simulator that can only
produce detectable faults can never demonstrate a blind spot.

### Driven, not just tested

120 runs on a realistic support mix: 29 diverged, and **all 29 failed with no trace-side defect at
all.** That is the honest shape and it is new.

---

## Two decisions I need from you

**1. Which packs get which mix, and at what rates.** Everything is at rate 0. I did not dial
anything on unattended, because changing a live fleet's failure mix overnight would have moved the
numbers on the ITSM demo and on pre-prod with nobody watching. The mapping proposes rates per
domain; they are proposals.

**2. `ok_but_empty` needs measuring before we trust the story.** I claim Provy's `empty_output`
detector may or may not catch a 200-with-hollow-body, because the existing `tool_fault:empty` sets
an error and this deliberately does not. **I have not measured it.** If it catches it, the lever is
a nice-to-have. If it does not, that is a product finding.

---

## ⛔ What I have NOT done, stated plainly

- **Nothing has been run against a live Provy tenant.** The levers are proven to fire and to leave
  distinct traces. **What Provy actually DOES with each one is a claim in the mapping table and is
  unmeasured.** That is the part that could still embarrass us, and it is the next session's work.
- **No pack contract, roster or alias was touched**, so `packs.ts` is untouched and both parity
  guards stay green. New levers reuse existing `LeverManifest` fields only.
- **The malformed-argument lever is not built** — tool-calling fails 3–15% of the time in production
  and it is the one big gap left. It needs a schema on the tool definitions, which the packs do not
  carry.
- **Prompt injection and memory poisoning are not modelled.** Both are in the published taxonomies;
  both are a security story rather than an outcome story, so I left them out on purpose.
- **⛔ I could not find documented complaints of AIOps naming a wrong root cause.** I searched for it
  directly. The `edwin` blind-spot story is supported by our own fleet only, and I have written that
  down rather than dressing a vendor guide up as evidence.

## ⛔ And a warning about the research itself

**Two fabricated statistics turned up tonight**, both clean, plausible and fully attributed:

- "Arize 2026 field analysis: context blindness 31.6%, rogue actions 30.3%, silent degradation
  24.9%…" — **not on the Arize page.** Fetched it; there are no percentages anywhere on it. I nearly
  weighted the sim's failure mix on those numbers.
- Earlier the same day, "41% of enterprises report at least one production rollback" — also absent
  from the page it was attributed to.

Both are recorded as dead in `00-evidence.md`. **The rule that saved it both times was fetching the
primary source before letting a number into anything.**

Also worth knowing: the "over 50% of bot-resolved tickets get reopened" figure is a single secondary
source with no denominator. It is in the evidence log flagged **do not print**.

---

## Files

- `docs/failure-research/00-evidence.md` — every source, what it supports, what is dead
- `docs/failure-research/01-mapping.md` — complaint → mechanism → lever → what Provy should do
- `engine/levers.py` — the ten levers, each with its source in the docstring
- `tests/test_evidence_levers.py` — 25 tests. The load-bearing one is
  `test_each_lever_leaves_a_distinct_trace_signature`, which fails the moment a lever becomes
  another alias for corrupting a signal. Mutation-tested by rewriting `parametric_override` as a
  bare `_corrupt_correctness` call: caught.
- `provy-sim-control/lib/levers.ts` + the fleet lever page — so they can actually be dialled
