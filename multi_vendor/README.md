# Multi Vendor Scenario

One change that has to land in systems owned by a different vendor than the agent that was asked to make it. The agent runs on one platform (Servicely, the
demo instance). The systems it changes sit on another (a ServiceNow PDI that stands in for the directory, single sign-on, the VPN and a SaaS token).
The agent's report is always "all four done". The truth is in the other vendor's estate. Each scenario is one way that truth can differ from the report.

## The flow (end to end)

1. The runner resets the estate and injects the scenario's fault.
2. The runner opens a Request on the agent platform.
3. An agent there queues an Action (the asynchronous queue provider, queue `provy-iam-poc`).
4. The PDI worker (a scheduled job, every minute) takes the task from the queue, applies it to the estate, and replies success or fail.
5. The runner reads the estate's own reading, which is the independent truth, and grades it: account off, no group held, no access left.
6. Provy's outcome pull reads that same reading and settles the work item. The scenario says whether Provy should call it held or not.

Step 3 is not wired yet. See "What is not wired".

## Scenarios (data, in `scenarios.py`)

| Key | What happens | Should hold |
|---|---|---|
| `clean_leaver` | Every system changes. The control case | yes |
| `vpn_missed` | The VPN answers OK and keeps the access | no |
| `directory_missed` | The directory answers OK and the account stays active | no |
| `recreated_by_sync` | Clean leaver, then a later HR sync re-creates the account | no |

Add a case by adding a row. The runner has no branch per scenario.

## Run it

```
../argus/scripts/with-secrets SERVICENOW_INSTANCE SERVICENOW_USER SERVICENOW_PASSWORD -- python scripts/run_multi_vendor.py list
../argus/scripts/with-secrets SERVICENOW_INSTANCE SERVICENOW_USER SERVICENOW_PASSWORD -- python scripts/run_multi_vendor.py run-all
../argus/scripts/with-secrets SERVICENOW_INSTANCE SERVICENOW_USER SERVICENOW_PASSWORD SERVICELY_INSTANCE_URL SERVICELY_API_TOKEN -- \
    python scripts/run_multi_vendor.py run vpn_missed --trigger servicely
../argus/scripts/with-secrets SERVICENOW_INSTANCE SERVICENOW_USER SERVICENOW_PASSWORD -- python scripts/run_multi_vendor.py reset
```

`--trigger direct` (default) calls the estate's own door. It proves the estate and the grading, is labelled `[estate-only]`, and must never be reported as the
whole flow. `--trigger servicely` opens a real Request and waits. Every run cleans up in a `finally`: fault cleared, pending HR events cleared, person
re-seeded, sync job off. Exit code 1 when any outcome differs from what the scenario says it should be. The sync scenario takes about a minute (the job runs
every five); it waits up to eight.

## The ServiceNow-side agents (built, switched off)

The PDI worker can now run each task through real agents: an applier that gets a plan from a model and applies only what the allow-list lets through, and a verifier that reads the
estate back and computes whether the access really changed. They report to Provy as the workflow **Access provisioning**, with the HR sync as its own named actor. Everything is behind the
property `provy.iam.agentic`, which is `false`, and the model defaults to an offline stub, so nothing changes until someone turns it on and nothing needs a key to test. Details,
switches and what the Servicely side must supply: `PDI-AGENTS.md`. Try it with no keys: `python scripts/run_multi_vendor.py run vpn_missed --agentic`.
Check the deployed copy against the shared test vectors: `python scripts/verify_pdi_agents.py`.

## What is proven (6 Nov 2026)

- Direct mode, all four scenarios, against the real PDI: outcomes as designed, estate clean afterwards. 21 tests with a fake estate.
- The worker polls the Servicely queue every minute with a working token (200, empty queue).

## What is not wired

- **Nothing queues the Action without a person.** No API or screen runs an Action by hand, and the queue table cannot be written from outside. Only an
  agent can queue one. Needs the Access orchestrator and its agent user, roles and acknowledgement (`servicely/PERSONAS.md`, step 4) and a tool that calls
  `Action.queueSubmit`. Until then `--trigger servicely` creates the Request and times out.
- The worker's reply path and Servicely's real payload shape have never been exercised. The first real queued row decides the payload.
- Provy's side (onboarding the fleet, the outcome pull on the estate's reading) is not scripted here yet.

## Rebuild

`pdi/setup_pdi_estate.py`, then `servicely/setup_actions.py` and `servicely/setup_personas.py`, then the hand steps in `servicely/PERSONAS.md`. Then one more
hand step that cost an hour: **the worker's token must have NO Allowed Scopes** and its URL Allowed Paths must be exactly `/controller/AsyncIntegration`.
With the "Async Queue" scope set, Servicely answers 401 to every request. Everything done to the instances is in `CONFIG-LOG.md`.
