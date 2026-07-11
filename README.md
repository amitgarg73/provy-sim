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
