# The simulator: verdict, and the plan to make it real

Written 19 Sep 2026. Every number here was measured against pre-prod on the night, not carried from
an earlier document. Where something is unmeasured it says so.

---

## The verdict, in one paragraph

The simulator is an excellent machine for demonstrating features that already work, and it was built
on purpose to be exactly that. It cannot currently tell you anything you did not already believe.
Ten evidence-derived levers were built in August from real production failures and **not one has
ever run against a live Provy tenant**. The harness that was supposed to score Provy's side of the
comparison is a stub. And the first live measurement of one of those levers, run tonight, shows
Provy naming a cause that the scenario never injected. The research is good, the levers are good,
the wiring between them and reality was never finished.

---

## Five findings, each measured tonight

### 1. The levers have never run anywhere real

`config/workflows.py` defines twelve rate profiles. Exactly one, `_SUPPORT_RATES`, carries any of
the ten evidence levers above zero, and it carries five of them at 1.2 to 3%.

⛔ **The `support` pack has no tenant.** `sim_control_config` holds eight fleets: `claims` ×2,
`claude_code`, `edwin`, `iam`, `itsm`, `stripe_support`, `teameight`. No `support`.

So the only profile where an evidence lever is switched on belongs to a pack that never runs, and
the other eleven profiles have the levers at zero or absent. The August handoff said "no fleet
behaves differently yet." That is still true, four weeks later, and it is stronger than it sounds:
**five of the ten levers are not dialled anywhere at all**, not even as a proposal.

### 2. The proof harness cannot score Provy's half. It never could.

`engine/scoreboard.py` prints a feature-proof table with an injected side and a detected side. Every
detected cell reads `[pending]`, and the footer blames missing credentials.

That is not why. `contract_met_rate()` is:

```python
if not self.available:
    return None
return None  # TODO: query ag_session_outcomes / rollup once creds exist
```

The detected side returns `None` **with** credentials. `_connect()` also imports `supabase` inside a
bare `except Exception`, so a missing optional dependency reports as "no credentials" too.

⛔ **So the harness built to prove Provy works has never once compared Provy's answer to the truth
it injected.** Every green scoreboard anyone has read was the injected side talking to itself.

### 3. The first live measurement, and the finding I withdrew

Ten runs of `correlation_as_cause` on the empty `edwin` fleet. Provy reconciled 10 of 10 as
`diverged` and attributed 4 `ignored_signal`, 3 `empty_output`, 3 `undetermined`.

⛔ **I first wrote this up as a defect: Provy naming a tool fault for a scenario that injects none.
That was wrong, and it was wrong three separate ways. Every correction came from the codebase, not
from the data.**

1. **I quoted the method and omitted the confidence.** All ten are `confidence: low`, and seven
   carry `base_rate_verdict: insufficient`. `applyBaseRateDiscipline` ran, found the evidence thin,
   and forced confidence down. That is the guard doing its job, and confidence is the one qualifier
   this subsystem is built around.
2. **The attribution was factually correct.** The stored evidence reads
   `output_excerpt: {"changes":[]}`. The `change_lookup` tool really did return an empty result. A
   real output was received and it really was empty.
3. ⛔ **And my supporting query was an artefact.** I ran `payload->'tool_output' IS NULL` against
   `ag_traces` and read "no output was reported". **All 30 tool steps have `body_key` set and
   `payload IS NULL` in Postgres**: bodies are offloaded to R2 and the attributor reads the hydrated
   span. I measured the stripped proxy and drew a conclusion about the thing.

The near-miss is worth recording. #972 is Amit's own ruling of 15 Sep, "an output never sent is not
an empty one", and `if (!outputReported(t)) continue;` implements it. Had I filed this, it would
have read as that fix regressing, and the next session would have gone round the same loop.

⛔ **THE DESIGN CONSTRAINT THIS BUYS, AND IT IS THE USEFUL OUTPUT: THE MEASUREMENT HARNESS MUST READ
WHAT THE ATTRIBUTOR READ.** Attribution correctness cannot be scored by querying `ag_traces.payload`
in SQL, because on any fleet with R2 offload that column is null and every absent-key test silently
returns true. Phase 0 reads through the same hydration funnel the product uses, or it will
manufacture exactly this finding again, automatically and at scale.

**What stands after all that:** nothing here is a Provy defect. The honest remaining question is
narrower and still open: on a scenario whose mechanism is a reasoning error, is naming a real but
incidental empty tool result the most useful thing to surface, even at low confidence? That is a
product question about ranking, not a correctness bug, and it needs the control arm and more than
ten runs before it is worth asking out loud.

⚠️ **The control arm is still running.** With `MIN_GROUP` at 6 and n=10, `insufficient` was the only
verdict this experiment could ever have produced. The design was too small to answer the question it
was posed. Phase 0 sizes runs against `MIN_GROUP` rather than against convenience.

### 4. The Guardrail finding I filed, and then closed as not a defect

⛔ **Filed as #1027 at P1, from SQL alone, and wrong.** I claimed nine of ten enabled checks never
grade anything and that nothing reports it. Opening the page ended it: Guardrails carries a group
headed `Not running · 4 checks, 4 have never run` with a `Retire 4` button that names each one, and
prints `nothing measured`, `Still calibrating`, `Never caught anything 8` and `Separating nothing 4`
elsewhere.

The last survivor was `method_conformance`, enabled five times and rendered nowhere. It writes to
`ag_agent_conformance`, never to `ag_evals`, so the query asked the wrong table. Production holds
463 rows, newest landing within a second of its `7 5 * * *` cron. Pre-prod is stale because crons
run against production only.

**Three corrections, all in the same direction: the product was right and the query was wrong.** The
one real trap is that `ag_eval_configs` holds two kinds of row, a check that grades and a record
that stores a threshold, with nothing in the schema to tell them apart. That belongs in a doc.

### 5. The contract side is healthy, and the contrast is the useful part

Every condition on every live fleet gets graded. The weak spot is different and milder: conditions
that are measurable and have **never once failed**. A condition nothing can break is one the
simulator cannot exercise, and `_SUPPORT_RATES` already records the shape in a comment:
`category_correct` never failed in 500 runs until its rate was raised.

**Measured, then fixed by running it.** Identity went 2 of 5 to 5 of 5, Refund 3 of 5 to 5 of 5,
AIOps 0 of 6 to 6 of 6. See the rate section at the end of this document: not one of those gaps
needed a new lever.

⛔ **So "test every check and every contract condition" has two different answers.** Contracts need
a reachability test. Checks need a liveness test, and they need it urgently.

---

## Why the simulator is unrealistic, stated precisely

Not "it is synthetic." Synthetic is fine. Three specific things:

**It was designed backwards.** `docs/provy-simulation-proof-harness.md` §6 gives every lever a column
headed "Proves", and every entry names a Provy feature. The levers were derived from what needed
demonstrating, not from what happens to people. §1 says so outright: "a measurable proof harness,
not a demo." That was a reasonable choice when the question was whether a feature worked at all.

**So it cannot produce a failure Provy misses.** Four of the sim's five tool shapes match Provy's
detector set name for name. A harness that can only emit faults the product already detects can
never measure what the product is blind to, and blindness is the thing worth knowing.

**And the unit is a lever, not a journey.** A lever fires inside one work item. Nothing in the
simulator models a refund reversed by a chargeback six weeks later, an access grant caught in a
quarterly review, or a PR reverted after a production incident. Those are the cases Provy exists for,
and the research that arrived on 18 Sep documents all three with sources.

---

## The plan

### Phase 0: stop measuring nothing (prerequisite, small)

1. **Implement the detected side of the scoreboard.** It is the difference between a demo and a
   measurement. Read the ledger, the incidents, the attributions and the criterion results for the
   fleet and window, and compare them to the injected ground truth the run already records.
2. **Make a missing dependency loud.** The bare `except Exception` around the Supabase import has to
   go; "not installed" and "not configured" must not print the same sentence.
3. **Run every one of the ten levers isolated, at rate 1.0, with a control arm.** Produce a real
   version of the "Provy today" column in `01-mapping.md`, replacing claims with counts.

⛔ **Do this before building any catalogue.** A catalogue built on the current mapping would be a
catalogue of assumptions, and tonight's first measurement already contradicts one of them.

### Phase 1: scenarios, each anchored to a real complaint

A scenario is a lever plus the evidence that it happens. It carries, as data rather than prose:

- the mechanism, in one sentence a practitioner would recognise
- the **verbatim complaint, its link, its date and who is speaking**
- the journey step it breaks
- what Provy is expected to do, and separately, **what Provy was last measured doing**, with a date

That last pair is the whole point. A scenario whose expectation and measurement disagree is the most
valuable object in the system, and today nothing can hold one.

The 18 Sep research supplies fourteen sourced complaints, each already mapped to a journey step, plus
two new mechanisms worth levers of their own:

- ⭐ **The fail-open approval.** Malformed tool-call JSON is swallowed and replaced with `{}` before
  an approval predicate runs, so the check evaluates an empty object, returns false, and the run
  proceeds **without the human approval it required**. No error, no defect, nothing at run time to
  find. (openai/openai-agents-python#3863)
- **The empty tool-call argument.** 33 of 267 Anthropic `tool_use` blocks carried an empty `input:
  {}` while declaring required parameters, measured at the provider boundary. (n8n#37916)

### Phase 2: journeys, which are what a customer recognises

A journey is an ordered walk through a real workflow with a settlement point, and scenarios attach to
its steps. The research gives seven, each with the field that matters most already answered: **where
the outcome is genuinely determined, and how long after the run.**

Both must be runnable: a journey end to end, or a single scenario in isolation. Isolation is how you
measure; the journey is how you demonstrate.

### Phase 3: the two trigger tests, which are not the same test

- **Checks need liveness.** Every enabled check has produced at least one result in the window, or
  the fleet is not certified. That would have caught #1027 the day it appeared.
- **Contracts need reachability.** Every condition has both passed and failed at least once across
  the catalogue, or no scenario can break it and it is decorative.

### Phase 4: rates, last

Rates are the last thing, not the first. ⛔ **Phase-A levers are exclusive: the first to fire claims
the run**, so configured rates are upper bounds and list order decides who starves. Setting a mix
before the isolated measurements exist means tuning numbers whose meaning is unknown.

---

## What this plan refuses to do

- **Claim the simulator is broken.** It does what it was built to do. It was built to answer a
  different question from the one now being asked.
- **Build the catalogue first.** It is the visible half and the tempting one. On today's evidence it
  would encode assumptions, three of which tonight's single measurement already unsettled.
- **Quote a production tool-calling failure rate.** The "3 to 15%" in `01-mapping.md` has no primary
  source; see the correction in that file. One practitioner's 12.4% on 267 calls is what exists, and
  it is a sample, not a rate.

---

## Added 19 Sep 2026: configured rate is not effective rate, and nobody knows the ratio

Running `edwin` with exactly two levers, each configured at **0.5**, and nothing else enabled:

| lever | configured | actually fired |
|---|---:|---:|
| `change_blind` | 0.50 | **0.069** (4 of 58) |
| `correlation_split` | 0.50 | **0.086** (5 of 58) |

Together they claimed 15.5% of runs against a configured 100%. ⛔ **Phase-A exclusivity does not
explain this**, because no other lever was enabled to starve them. Something else gates the pack
injector and it has not been traced.

**Why it matters more than it looks.** Every rate in `config/workflows.py` is written as though it
were the frequency a mechanism occurs. If the delivered rate is six or seven times lower, then
`change_blind` at its configured 0.22 fires on roughly 3% of runs, and `reprovisioned_by_sync` at
0.015 fires on roughly two runs in a thousand. That is the arithmetic behind the reachability
result: three fleets had conditions that had never once failed, and in every case the mechanism
existed and was configured.

⛔ **So the rates in that file cannot be reasoned about until the ratio is measured.** Phase 4 of
the plan above says rates come last. This is why: setting a mix today means choosing numbers whose
delivered meaning is unknown, and the only honest way to pick one is to run a lever alone at 1.0,
observe what fraction actually fires, and calibrate from that.

**The reachability sweep that produced this**, after exercising each gap:

| Fleet | Conditions reachable both ways |
|---|---|
| ITSM Incident Resolution | 7 of 7 |
| Strategy C Trading | 6 of 6 |
| Identity Operations | 5 of 5, was 2 of 5 |
| Refund Operations | 5 of 5, was 3 of 5 |
| AIOps Investigations | 6 of 6, was 0 of 6 |
| Claims Adjudication | 3 of 5 and 2 of 5, untouched |
| Post-call follow-up | 2 of 8, untouched |

⛔ **AND NOT ONE OF THOSE GAPS NEEDED A NEW LEVER.** Every mechanism already existed in its pack,
wired to the right condition, at a rate too low to ever fire. The simulator's coverage problem was a
calibration problem the whole time, and nothing measured it because nothing asked whether a
condition could fail.
