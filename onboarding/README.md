# Onboarding a sim pack as a Provy fleet

Each pack is onboarded exactly like any external customer — no backdoor. This
also dogfoods the waitlist -> invite -> signup funnel and proves the "any
framework, no rewrite" claim.

## 1. Normal customer path

1. **Join the waitlist** at `provy.ai/waitlist`.
2. **Approve** in `/admin/waitlist` -> invite email with `/signup?token=...`.
3. **Sign up** with `judge_tier: 'free'` (the L4 judge runs on Groq, no Anthropic
   cost), and go through the onboarding wizard.
4. You receive the **ingest key** once. A key resolves `(tenant_id, workflow_id)`.

Structure is chosen here, not in code: onboard the three packs as three fleets
under one "SimCo" tenant, or as separate tenants, by choosing which keys you
mint. The runner points at one key per workflow either way.

## 2. Register agents + eval configs

`onboarding/onboard.py` builds both artifacts straight from the pack, so they can
never drift from what the pipeline actually emits.

```bash
# Preview (prints the seed-evals body AND the outcome contract JSON):
python onboarding/onboard.py --pack support

# Actually POST the agents + eval configs to Provy:
python onboarding/onboard.py --pack support --seed-evals --key provy_xxx
```

This POSTs `{agents, criteria}` to `POST /api/onboarding/seed-evals`, which
registers `ag_pipeline_agents` and `ag_eval_configs` for the fleet. The
`eval_name`s match the L4 evals the pack emits (one semantic criterion per agent).

## 3. Seed the outcome contract

There is no public contract-ingest endpoint yet, so the one active contract is
inserted during onboarding (via the UI or directly into
`ag_outcome_contracts.criteria`). `onboard.py` prints the exact JSON — for
Support:

```json
[
 {"id":"c1","text":"Resolved without escalation","side":"outcome","signal":"escalated","op":"eq","threshold":false},
 {"id":"c2","text":"Policy followed","side":"both","signal":"policy_followed","op":"eq","threshold":true},
 {"id":"c3","text":"Resolved within SLA","side":"outcome","signal":"sla_met","op":"eq","threshold":true},
 {"id":"c4","text":"No reopen within 7 days","side":"outcome","signal":"reopened_7d","op":"eq","threshold":false},
 {"id":"c5","text":"Correct category","side":"both","signal":"category_correct","op":"eq","threshold":true}
]
```

Every condition is signal-mapped, so the fleet grades `method='deterministic'`.
The run emits each signal: the Estimated side on the reviewer's closing trace
payload (and session-close metadata), the Real side in the outcome post's
`signals` bag.

## 4. Point the runner at the key

Set the workflow's key env var (see `config/workflows.py`):

```bash
export PROVY_KEY_SUPPORT=provy_xxx   # or PROVY_KEY_CLAIMS / PROVY_KEY_CRM
export PROVY_EMIT=1
export GROQ_API_KEY=gsk_xxx
python scripts/run_batch.py --pack support --count 8 --reconcile
```

## Gotchas (from the code)

- **`PROVY_EMIT=1`** — emission is off by default even with prod credentials. A
  silent tenant is almost always this. `GITHUB_ACTIONS=true` auto-opts-in.
- **`is_simulated=false`** — the emitter sets it, so Provy's incident and pattern
  engines fire (they skip `is_simulated=true`).
- **Deterministic-only trust** — held-rate and the Outcomes factor count only
  `method='deterministic'`. Every contract condition is signal-mapped and the
  real outcome signal names match, so no condition is left as prose.
