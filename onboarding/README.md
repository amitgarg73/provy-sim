# Onboarding a sim pack as a Provy fleet

Each pack is onboarded exactly like any external customer, with no backdoor. This
dogfoods the waitlist to invite to signup funnel and proves the "any framework,
no rewrite" claim. The whole loop is browser-driven through two surfaces: the
Provy wizard and the Sim Control console (`provysim.vercel.app`). No CLI is
required; the direct CLI path is kept at the bottom as an alternative.

## The loop at a glance

```
Provy wizard        Sim Control console          Provy Standards
(get ingest key)  → (Connect + Kick run)   →   (confirm the contract)  → grades
```

1. Onboard the fleet in the Provy wizard and copy its ingest key.
2. Connect that key in the console and kick a run. Traces and outcomes flow.
3. Confirm the contract Provy drafts from those traces in Standards.
4. Kick another run. It grades against the contract. Validate in Provy and the
   console scoreboard.

## 1. Onboard in the Provy wizard

1. **Join the waitlist** at `provy.ai/waitlist` (for example SimCo).
2. **Approve** in `/admin/waitlist`, which mints an invite `/signup?token=...`.
3. **Sign up** with `judge_tier: 'free'` (the L4 judge runs on Groq, no Anthropic
   cost).
4. In the wizard, pick **GitHub Repo** and point it at `amitgarg73/provy-sim`.
   Provy reads the pack's agents and drafts eval criteria. Review, trim to the
   pack you are onboarding, and seed.
5. On **Connect**, copy the **ingest key** once. The key resolves
   `(tenant_id, workflow_id)`, so it is the only address the sim needs.

Structure is chosen here, not in code: onboard the three packs as three fleets
under one SimCo tenant, or as separate tenants, by minting one key per workflow.

## 2. Connect and run in the Sim Control console

Open `provysim.vercel.app`, unlock with `CONTROL_ADMIN_KEY`, and go to the pack's
tab:

1. **Connect** — paste the ingest key. The console resolves the fleet from the
   key hash and stores it. The raw key never leaves the console.
2. **Kick run** — dispatches the pack's GitHub Actions workflow
   (`run-<pack>.yml`) with the key and URL as inputs. The sim runs on Groq, emits
   traces and the outcome `signals` bag, and reconciles.

The sim already emits the payload. The Support pack defines its conditions and
produces the matching `real_signals` in the same file, then posts them to
`/api/ingest/outcome`. Nothing needs to be wired by hand; the emit ships in the
repo.

## 3. Confirm the contract in Standards

The contract is authored in Provy, not seeded from here. Once a run has flowed:

1. Open the fleet's **Standards** page in Provy.
2. Provy drafts the success contract from the signals it just received (their
   names match the pack's `real_signals` by construction). Review and confirm it.
3. If you author a condition for a signal the pack does not emit, the
   "How to send outcomes" card names exactly what to add. For the built-in packs
   every condition is already emitted, so there is nothing to wire.

For reference, the Support pack's contract is:

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

## 4. Validate

- **In Provy**: the fleet's Evaluation History (per-run grading), the Reliability
  "held up" number, and the contract rollup.
- **In the console**: the fleet's Scoreboard, which sets Provy's detected side
  next to the sim's injected ground truth.

## Alternative: run the CLI directly

The console is the operator surface, but a batch can be run straight from a shell.
`onboarding/onboard.py` also POSTs the exact agents and eval configs if you want
to bypass the wizard's inferred version:

```bash
# Optional: seed agents + eval configs exactly as the pack emits them.
python onboarding/onboard.py --pack support --seed-evals --key provy_xxx

# Run a batch.
export PROVY_KEY_SUPPORT=provy_xxx   # or PROVY_KEY_CLAIMS / PROVY_KEY_CRM
export PROVY_EMIT=1
export GROQ_API_KEY=gsk_xxx
python scripts/run_batch.py --pack support --count 8 --reconcile
```

## Gotchas (from the code)

- **`PROVY_EMIT=1`** — emission is off by default even with prod credentials. A
  silent tenant is almost always this. `GITHUB_ACTIONS=true` auto-opts-in, so the
  console's kicked runs emit without setting it.
- **`is_simulated=false`** — the emitter sets it, so Provy's incident and pattern
  engines fire (they skip `is_simulated=true`).
- **Deterministic-only trust** — held-rate and the Outcomes factor count only
  `method='deterministic'`. Every contract condition is signal-mapped and the
  real outcome signal names match, so no condition is left as prose.
