# Provy proof-simulation harness

A synthetic-but-real multi-agent workflow that runs on a free LLM (Groq), emits
full telemetry into Provy, injects controlled failures across every dimension
(silent ones first), and — because the simulation owns ground truth — records
exactly what it broke so a scoreboard can score whether Provy caught it.

This is issue #350 / epic #347. Full design:
`../argus/docs/provy-simulation-proof-harness.md`.

It is a real Provy tenant with synthetic-but-real work: `is_simulated=false`, so
Provy's incident and pattern engines fire. Nothing is emitted until you set
`PROVY_EMIT=1` and an ingest key, so building and testing is always safe.

## Layout

```
engine/                 shared machinery (domain-free)
  types.py              dataclasses crossing the engine <-> pack boundary
  contract.py           signal-mapped grading (good/bad value, "X of N met")
  pack.py               DomainPack protocol + BasePack.run_pipeline
  llm.py                Groq (OpenAI-compatible) helper + deterministic offline stub
  levers.py             the 9 chaos levers + LeverConfig + apply()
  emitter.py            Provy REST emitter (x-provy-key, PROVY_EMIT gate, capture)
  groundtruth.py        append-only JSONL ledger of injected truth
  scoreboard.py         injected-truth aggregation + Provy-side comparison skeleton
  runner.py             BatchRunner: generate -> run -> emit -> record
  structural.py         layer-3 checks derived from the run itself (see below)
  reconcile.py          EOD: post the day's real outcomes + judge backfill
packs/
  support/  claims/  crm/    one DomainPack each (generator + agents + contract + manifest)
config/workflows.py     per-workflow ingest-key env, lever rates, cadence
onboarding/             onboard.py (prints seed-evals + contract) + README
scripts/run_batch.py    CLI: run a batch, reconcile, print the scoreboard
tests/                  pytest for the pure logic
```

Each pack is a **workflow (fleet)** with its own ingest key. The trust number is
per fleet; there is no cross-fleet aggregate.

## Structural (layer 3) checks

Packs emit output-quality results (layer 4). `engine/structural.py` adds the deterministic layer-3
set, derived from the traces the run already produced, so a lever that breaks a run breaks the check
with no extra wiring:

| check | scope | fails when |
|---|---|---|
| `pipeline_completion` | session | an agent in the roster produced no real step |
| `tool_success_rate` | agent | more than 20% of that agent's tool calls errored |
| `decision_made` | agent | the deciding agent recorded no decision |
| `exit_quality` | session | the run ended on a terminal the engine sets when it breaks |

⛔ **PROVY DOES NOT COMPUTE THESE.** It has no server-side rule evaluator: a layer-3 result is graded
only if the fleet SENDS it. Provy seeds a structural catalogue per fleet, so before this existed
every sim tenant carried a catalogue that was enabled and permanently ungraded.

⛔ **THEY ARE DERIVED AT `emit_run`, THE ONE SEAM BOTH RUN PATHS SHARE.** `runner.run_one` and
`desk._finish` both end there. Deriving them in either caller alone leaves the other fleet ungraded
with nothing reporting the difference.

⛔ **A SKIP IS A STEP.** `skip_propagation` removes an agent's real traces and appends a *skip* step
for it and everyone downstream, so "has at least one trace" is true even in a fully broken pipeline.
`pipeline_completion` therefore counts only steps that are not skips. It passed 60 of 60 runs before
this was found, and the unit test could not have caught it: the fixture used absent steps, a shape
the simulator never produces.

⛔ **NO WHITELIST OF GOOD TERMINAL REASONS.** The tenant owns its nouns: support says `resolved`,
teameight says `followed_up`, trading says `intraday_entries_placed`. `exit_quality` fails only on
the closed set of terminals the engine itself sets when it breaks a run. A whitelist failed 25 of 25
real teameight runs before this was inverted.

⛔ **A CHECK THAT CANNOT RUN WRITES NO ROW.** An agent that called no tool gets no
`tool_success_rate`. A fabricated pass is how a blind spot comes to look like health.

⛔ **A FLEET WITH NO PIPELINE-BREAKING LEVER CANNOT FAIL TWO OF THESE.** `skip_propagation` was absent
from teameight, making `pipeline_completion` and `decision_made` decorative there: 0 failures over 40
emitted runs. When adding a pack, check its lever set can actually reach every check on it.

## The nine levers

Per-agent, per-dimension, with known injection rates and a seeded RNG. Silent
levers lead because they are the differentiator.

`silent_wrong` (★ confident, well-formed, L4-passing, actually wrong),
`confidence_miscalibration` (★ HIGH on the wrong runs), `silent_drift` (★ slow
degrade after an onset session), `tool_fault` (errored/empty/fallback/stale tool
output), `overt_error`, `quality_degrade` (failing L4 with reasoning),
`policy_violation`, `sla_breach`, `skip_propagation`.

Every lever aims at a specific agent via the pack's `LeverManifest`, and derives
the "bad value" for a signal from the contract — so the same nine levers work
for all three packs unchanged.

## Quick start — a dry batch (emits nothing)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# Dry run: builds every trace/eval/outcome payload + records ground truth, sends nothing.
.venv/bin/python scripts/run_batch.py --pack support --count 8 --seed 1 --reconcile --scoreboard
```

No Groq key is needed: the LLM falls back to a deterministic offline stub, so the
pipeline runs end to end. Correctness is decided by the work item and the levers,
never by what the model says — the model only dresses the reasoning prose.

Run the tests:

```bash
.venv/bin/python -m pytest
```

## Emitting for real

Emission is off by default even with prod credentials (dev runs must not pollute
prod). Turn it on with `PROVY_EMIT=1` and the fleet's ingest key:

```bash
PROVY_EMIT=1 PROVY_KEY_SUPPORT=provy_xxx GROQ_API_KEY=gsk_xxx \
  .venv/bin/python scripts/run_batch.py --pack support --count 8 --reconcile
```

`GITHUB_ACTIONS=true` auto-enables emission, so a scheduled workflow reports with
no extra config. See `../argus/docs/provy-simulation-proof-harness.md` §10 for the
24x7 GitHub Actions cadence.

## Onboarding (the normal customer path, no backdoor)

1. Join the waitlist at `provy.ai/waitlist`.
2. Get approved in `/admin/waitlist` -> invite email with `/signup?token=...`.
3. Sign up with `judge_tier: 'free'` (Groq), go through the wizard.
4. Register agents + eval configs and seed the contract (see `onboarding/README.md`):
   ```bash
   python onboarding/onboard.py --pack support                 # preview payloads
   python onboarding/onboard.py --pack support --seed-evals --key provy_xxx
   ```
5. Set `PROVY_KEY_SUPPORT` to the ingest key. From then on it is a normal external
   tenant emitting over `x-provy-key`.

## What is complete vs stubbed

- **Complete:** engine, all three packs (Support end to end), the nine levers,
  the emitter (matches the live Provy contract — base `https://provyai.vercel.app`,
  header `x-provy-key`, outcome post carries **both** label/value and the signals
  bag, `is_simulated=false`), the ground-truth ledger, the reconcile path, the
  injected-truth aggregation, the CLI, and the tests.
- **Stubbed with clear TODOs:** the **detected** side of the scoreboard
  (`ProvyQuery`). It reads the `ag_*` tables read-only when `SUPABASE_URL/KEY` +
  `PROVY_TENANT_ID/WORKFLOW_ID` are present; without creds it returns `None` and
  the feature-proof rows show `pending`. The injected side is fully real today.
- **Not built here (later phases):** fix-loop automation (Phase 3), and the
  GoDaddy ANS third-party-agent boundary (Phase 5).
