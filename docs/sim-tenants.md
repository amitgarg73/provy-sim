# The sim tenants: a persistent pre-prod test bed

Written 7 October 2026. Pre-prod only. Nothing here touches production, Third Eye, the d1 and d2 demo tenants, the Onboard and walk tenants, or the ITSM tenant and pack.

## What this is

Twelve small workspaces on pre-prod, each built to be one thing the product has to get right. Each has a ground-truth file that says what the product should show for it, so a script can check the answer instead of a person looking at a screen. Names start with `Sim ` and logins are on `demo.provy.ai`. The nightly sweep deletes only users on `@argustest.com` and a tenant named `Nightly Certification`, so these survive nights with no change to argus.

The set exists because the simulators were built before the context manifest, readiness, fleet plans, notices, pilots and claims-first work. Before this set, one simulated tenant sent context records at all (the Onboard tenants, which belong to another job), none sent a claim with a stated confidence, none had an order, and none could show a fleet that sends nothing.

## The twelve

| Key | Tenant | What it is for | Door | Sessions | What the product should say |
|---|---|---|---|---|---|
| H1 | Sim H1 Healthy | A healthy, fully instrumented fleet on a Growth fleet plan, with a notice recipient who is not the owner and a raised upper ceiling | JSON | 60 | Ingestion ready, 6 of 6. All 60 work items settled. Expected side is the agent's own claim. Calibration table matches the plan. Growth, 150,000 steps. |
| H2 | Sim H2 Thin Context | The Third Eye shape: record on model steps only, one agent runs code and no model, dates on some items, no instruction fingerprint. Enterprise fleet plan, small pool, 100 percent internal discount | JSON | 60 | Thin, 4 of 6. 5 of 20 steps not counted. Source age partly sent. Instruction fingerprint not sent. |
| H3 | Sim H3 No Context | The customer who never sent a record. Pilot ending in 14 days | JSON | 40 | Thin, 0 of 6, never ready. No context check writes any verdict, none passes. Pilot ending notice at 14 days. |
| H4 | Sim H4 Planted Faults | Stale sources, unapproved sources and empty searches, with a declared 30 day limit and approved list. No order | SDK | 50 | Every planted stale and unapproved item is flagged on its session and nothing else is. |
| H5 | Sim H5 Instruction Change | A real change, an A-B-A-B rollout and a same-label fingerprint change on three agents. Pilot ending in 3 days | OpenTelemetry | 50 | Findings exactly as the learned rule gives them: change once, went back once, back and forth once, then quiet. A fourth agent never changes and is never flagged. |
| H6 | Sim H6 Declared Checks | Declared freshness and approved-source rules; freshness switched off for a phase, then on; the learned empty-search check switched off. Per-agent order | JSON | 60 | Freshness verdicts exist in phases 1 and 3 and not in phase 2. Approved-source verdicts in all three. No empty-search verdict after the switch-off. |
| H7 | Sim H7 Outcomes Duo | Two fleets: one agent whose stated confidence is about right, one that states 0.9 and holds about half the time. A contract condition that can never be measured on each. Only the first fleet has a declared limit. Startup plan | JSON | 40 + 40 | Each fleet's calibration table matches its plan. Declarations land on the first fleet only. |
| H8 | Sim H8 Doors | One planned story sent five ways: SDK, OpenTelemetry, JSON, log line, log field map | all | 5 x 16 | Ready on the first four. Thin, 0 of 6, on the field map: it has no field for three of the six signals (a documented limit of that door), and it binds a retrieval to the agent's first event, so an agent that looks something up first has its record on the lookup and its decision step reads as no record (the other three signals are partly sent). |
| H9 | Sim H9 Agent Roster | A retired agent and an agent that sent data and was never accepted. Every step sent twice | JSON | 40 | Steps stored once. Roster shows one retired, one not accepted. |
| P1 | Sim P1 Pilot Ended | Pilot ended 5 days ago, not converted | JSON | 20 | State ended, 5 days since. |
| P2 | Sim P2 Pilot Converted | Pilot ended and a later paid order | JSON | 20 | Converted. |
| P3 | Sim P3 Pilot Extended | Pilot moved from 2 days out to 20 days out by staff | JSON | 20 | Running, 20 days left. |

Ground truth for each lives in `ground_truth/sim_tenants/<key>/`: `expect.json` (what the product should say, with the clock and nonce the plan was built on, the order calls with absolute dates, and the SHA-256 of each truth file) and `<fleet>.truth.jsonl` (one line per work item: its steps, items, instruction, claim, planted faults, outcome). Neither is ever sent to Provy; a test scans every body for it.

The spec is `config/sim_tenants.json`. The scenarios are `engine/sim_scenarios.py`. The expectations are written from the plan, independently of the product, from its documented rules (`docs/readiness-contract.md`, the learned-check rules in argus `docs/operations/context-default-checks-runbook.md`). A disagreement is a finding about one of the two.

## Where they are (7 October 2026)

Tenant ids, fleet ids and logins. No password and no key is here: they are in a 0600 file, `credentials.json`, in the session scratchpad
(`/private/tmp/claude-501/-Users-amitgarg/df44c595-ffd9-40ef-8535-927a02d2085a/scratchpad/sim-tenants/credentials.json`), and nowhere else. R2: the
tenants were ingested through a local server with the bucket names empty, so every one has 0 objects under `traces/<tenant id>/` (counted read-only on the day).

| Key | Tenant | Tenant id | Fleet | Fleet id | Login | Steps |
|---|---|---|---|---|---|---|
| H1 | Sim H1 Healthy | 9dcd69b0-11a1-4e5b-90e4-5a6d016166f1 | Sim H1 Claims Desk | 547b8ad8-62b7-4c2e-ad12-7f2ee12d9da7 | sim-h1@demo.provy.ai | 300 |
| H2 | Sim H2 Thin Context | 0c587584-4191-46d0-851d-035ffb5c0bab | Sim H2 Thin Desk | 6b18b0f7-c4e3-4a4f-bd61-a837e7e4b581 | sim-h2@demo.provy.ai | 300 |
| H3 | Sim H3 No Context | 01448857-5432-4a86-a941-51d84a70aac9 | Sim H3 Access Desk | f975f759-3223-40ca-bfa1-604ca9119701 | sim-h3@demo.provy.ai | 274 |
| H4 | Sim H4 Planted Faults | a3962c45-2caf-4a55-bab9-37ff5a6e696c | Sim H4 Access Desk | 0a611dc5-87b8-423b-9704-7e067d65d9bd | sim-h4@demo.provy.ai | 337 |
| H5 | Sim H5 Instruction Change | 7ceb2bd6-e6fc-48d8-a18b-df97d8ec8b5c | Sim H5 Access Desk | 6695a0af-bdf1-4b43-99ba-648460ec8db4 | sim-h5@demo.provy.ai | 390 |
| H6 | Sim H6 Declared Checks | 6b70b829-11f6-4cab-8d76-75a209c642a3 | Sim H6 Claims Desk | 3643378d-6637-4f3a-a794-1d5f8512ac25 | sim-h6@demo.provy.ai | 300 |
| H7 | Sim H7 Outcomes Duo | 0ae4bfbd-1b36-4fe1-b5f2-9f8d559d843d | Sim H7 Calibrated Desk | 25a49338-ef3a-4bf5-a04e-2979597bedc5 | sim-h7@demo.provy.ai | 200 |
| H7 | Sim H7 Outcomes Duo | 0ae4bfbd-1b36-4fe1-b5f2-9f8d559d843d | Sim H7 Overconfident Desk | defeab7d-9831-444b-9c5f-3e95835d4c59 | h7-overconfident@demo.provy.ai | 200 |
| H8 | Sim H8 Doors | 7e1aa411-c650-465b-b2df-6676ff7e73cb | Sim H8 via SDK | eb0ff5df-db73-4a1b-9ae1-a680f1741190 | sim-h8@demo.provy.ai | 80 |
| H8 | Sim H8 Doors | 7e1aa411-c650-465b-b2df-6676ff7e73cb | Sim H8 via OpenTelemetry | a3134293-f501-4a08-9347-89455530c2c4 | h8-otlp@demo.provy.ai | 96 |
| H8 | Sim H8 Doors | 7e1aa411-c650-465b-b2df-6676ff7e73cb | Sim H8 via JSON | eb22f331-2684-40a4-a93c-e7c8c2e04244 | h8-rest@demo.provy.ai | 80 |
| H8 | Sim H8 Doors | 7e1aa411-c650-465b-b2df-6676ff7e73cb | Sim H8 via log line | 64de3917-4841-4ef5-9d69-5221c300f6ed | h8-log@demo.provy.ai | 80 |
| H8 | Sim H8 Doors | 7e1aa411-c650-465b-b2df-6676ff7e73cb | Sim H8 via log field map | 52d6b7cf-421c-4013-a7ad-8686aaf34cbd | h8-logmap@demo.provy.ai | 80 |
| H9 | Sim H9 Agent Roster | 1c6f8911-da70-484f-b250-9d8480bf7ca0 | Sim H9 Claims Desk | ee7cc2a3-40b6-44e1-b719-5a8830c1adcd | sim-h9@demo.provy.ai | 200 |
| P1 | Sim P1 Pilot Ended | 7323da4f-a21e-4ce7-be5d-c24d68baa3ee | Sim P1 Claims Desk | 9f770f3b-aec5-4ace-abbc-82ae7effbab1 | sim-p1@demo.provy.ai | 100 |
| P2 | Sim P2 Pilot Converted | 8ea59d70-d256-4e4a-bf66-aebb91283801 | Sim P2 Claims Desk | a198a0f7-5556-4da8-a0a6-dc22d0d036de | sim-p2@demo.provy.ai | 100 |
| P3 | Sim P3 Pilot Extended | 62aeb864-423f-4832-a23f-2401a20925f9 | Sim P3 Claims Desk | f24e93c8-3e68-43a9-8bf4-0767515a2415 | sim-p3@demo.provy.ai | 100 |

Last run of `sim_assert.py` against pre-prod, 7 October 2026: 512 comparisons, 512 pass, 0 fail, 0 no-data (`docs/sim-evidence/assert.json`, one row per comparison with expected and observed).
One screenshot per tenant purpose, with the page text beside it, is in `docs/sim-evidence/`.

## What building it found

No mismatch between the product and its documented rules survived. Every defect found was in the simulator or its tooling, and each is fixed here with a test:

- A claim about a remapped signal is never graded. The first tenant's agent claims named `decision_correct`, which the claims pack remaps for the trace, so the product fell back to its forecast on 60 of 60 ledger rows. Claims now name a signal the product can grade without a human (`within_limit`), and a test holds that.
- The packs date an outcome at the moment the work began, before the decision it answers. Outcomes are now dated 30 to 90 minutes after the last step.
- A random work-item draw can collide: H4 planned 50 sessions and the product stored 49, correctly. A plan with a collision is now refused.
- `provisionFleet` writes the roster and no `accepted` event, so every provisioned agent read as never accepted and "Agents counting" read 0. The set accepts its agents at setup through `ag_record_agent_events`, the same function the product's routes use.
- The log door answers 503 without a working model key. A session it did not accept is no longer marked sent, and no outcome is posted for it.
- The provy-sim-control levers page did not typecheck: the seven context levers had no entry in its two maps.
- `scripts/fleet_doctor.py` opens `provy.config` directly and reads ServiceNow and GitHub secrets. It was not run. The console-key check is a SELECT comparing hashes.

Behaviour that is by design and now has an assertion: the OpenTelemetry root-span wrapper reads as one more agent, never accepted, and adds a stored step per session (Help says so); the field map binds a retrieval to the agent's first event.

One thing seen once and not reproduced: on the first build of H2, 1 of 60 work items took the forecast although the agent's claim was stored on the session. The rebuilt tenant passes all 39 comparisons. If it comes back, it is a race between the session close and the claim being readable.

## Which server

Use a local server started from the argus checkout at `provydev`, pointed at pre-prod. On 7 October 2026 the deployed pre-prod (`dev.provy.ai`) was running a build older than `provydev`: it accepted an order with fleet terms and stored it as a per-agent order with no terms, and it had no pricing, upper-ceiling or staff readiness route. The local server also holds the four R2 names empty, so the sim stores trace bodies in the database and writes nothing to the bucket the pre-prod and production share.

    cd "$HOME/Claude Projects/argus"; git status --short | grep -v '^??'      # must print nothing
    git switch --detach provydev; node scripts/check-local-dev.mjs
    cd web && scripts/with-secrets WAITLIST_ADMIN_KEY_PREVIEW ADMIN_SECRET_PREVIEW -- sh -c \
      'WAITLIST_ADMIN_KEY="$WAITLIST_ADMIN_KEY_PREVIEW" ADMIN_SECRET="$ADMIN_SECRET_PREVIEW" RESEND_API_KEY= R2_ACCOUNT_ID= R2_ACCESS_KEY_ID= R2_SECRET_ACCESS_KEY= R2_BUCKET= PORT=3100 npm run dev'
    # when done: git switch provydev, and confirm git status is clean

(Port 3000 is the Sales OS front end on this machine.) Teardown is the one call that goes to `dev.provy.ai`, because only that deployment holds the R2 names.

## Rules

- Pre-prod only. Every client refuses another project or host, and asks the database and the deployment which environment they are in.
- Secrets by name, through `argus/scripts/with-secrets`. Passwords and ingest keys live in one file with mode 0600, `credentials.json` in the session scratchpad. It is never printed or committed, and a looser mode is refused.
- Workspaces come from `provisionFleet` (provy-sim-control `scripts/provision-sim-set.mts`), not from sign-up. The 20 a day sign-up cap is not touched.
- The ITSM pack is refused by the spec check. Another job owns it and the PDI.
- Volume is bounded: 80 to 480 steps a tenant. This is a test bed, not a load test.

## Build, check, tear down

Under `with-secrets SUPABASE_URL SUPABASE_KEY WAITLIST_ADMIN_KEY_PREVIEW`, with the scratchpad venv that has `provy-sdk`, `opentelemetry-sdk` and `requests`:

    python scripts/sim_set.py plan --now <ISO clock, not ahead of now>      # writes the truth and expectation files, no network
    # provision (provy-sim-control worktree):
    npx vite-node --config vite.node.config.mts scripts/provision-sim-set.mts --spec <path>/config/sim_tenants.json --out <credentials file> [--only H1,H2]
    python scripts/sim_set.py build H1 --creds <file>      # declarations, send in phases where the fleet has them, then order, notices, ceiling, roster
    PROVY_SIM_CONTROL=<provy-sim-control worktree> python scripts/sim_assert.py --creds <file> --out docs/sim-evidence/assert.json
    node scripts/shoot_sim_tenants.mjs --creds <file> --out docs/sim-evidence

To rebuild one tenant: tear it down, forget its credentials, provision it again, plan with the same clock, build. `scripts/sim_teardown.py H1 --creds <file> --apply` does the teardown in the right order: the product's own delete empties the tenant's R2 objects first (counted before and after with a read-only list of `traces/<tenant id>/` only), then the rows, then this script clears the console's own rows (`sim_control_config`, `sim_control_runs`, `sim_pending_outcomes`), which have no foreign key to the workflow. Orders and the audit log outlive a workspace by design.

Never delete a tenant by domain. Only by tenant id, from the credentials file, with a name that starts with `Sim `.

## How often

- After every deploy of argus to pre-prod, and before a release: `sim_assert.py`. It is read-only and takes about a minute.
- Every two weeks, rebuild the pilot tenants (H3, H5, P1, P2, P3). Their states are dated: 14 days out, 3 days out, ended 5 days ago. The expected state is computed from the absolute dates on the day the check runs, so the check stays true, but the scenario each one was built for drifts.
- When the product changes what it counts (readiness window, learned-check thresholds, tiers), update the expectation function first, read the diff, then rebuild.

## What the product cannot be asked to do here

- The hourly roll's pilot pause and the usage notices run from the scheduler with `CRON_SECRET`, a production-tier secret. This set does not hold it. So P1 shows the ended pilot and the staff finding, but the roll has not paused its optional model work, and no extra-usage notice has been claimed for H2 even though it is over its pool. Running the roll for these tenants is the founder's call.
- A sealed month, a month boundary and a pilot that ends by the clock need the billing-clock hook that only `ent-walkb-*` workspaces have.
- The deployed pre-prod (`dev.provy.ai`) is behind `provydev` until it is next deployed.

## Where the simulators stood on 7 October 2026 (the gap list this set closes)

Read from pre-prod by SELECT and from the repos. No customer content was read.

| Area | Before | Evidence | Now |
|---|---|---|---|
| Existing sim workspaces | 8 shells from August (Northwind Commerce, NorthPeak, Teameight, Claude Code Live, Meridian Mutual, Harborline Insurance, Vantage Group, Weekone Travel): provisioned, never run, 0 steps, 0 sessions. Wiring is healthy (the console's key matches the product's for every one) | `sim_control_config` joined to `ag_traces` and `ag_ingest_keys`, hash compared in SQL, no value read | Left alone. They are not part of the set. Tear them down when the founder says |
| ITSM Demo | 910 steps, 84 sessions, 0 steps carrying a context record. 5 ingest keys on the fleet and one matches the console. Another job owns it | same | Not touched. Gaps listed in the final report |
| Onboard SDK, OTel, JSON, Missed | The only sim tenants with context records (482, 480, 480, 328 steps). Another job's | same | Not touched |
| C-series customers | Harbourline Customs, Tidewater Customs, Bluebonnet (on `@argustest.com`, not in the sweep's exempt list), Anchorfield and Cascade (exempt), Kestrel and Finch; all marked contaminated (#1136), none with a context record | tenant list, `lib/test-tenant-sweep.ts` | Left alone. Three are exposed to the sweep |
| Context records on every door | Only through the one-off context sets and the onboarding runner, on the iam and claims packs. No pack run through the console sent one | `engine/context_levers.py` PROFILES has two packs | Every fleet in this set goes through a door that sends them, by REST, SDK, OpenTelemetry, a log line and a field map |
| The six signals | Built whole or cut by hand | n/a | Whole (H1, H8), partial by design (H2, H8 field map), none (H3) |
| Steps that ran no model | None. Every decision step carried a model | pack code | H2: one agent runs code and no model |
| Agent claims with a stated confidence | Every claim at 0.9. The first claim this set sent was never graded (remapped signal), so the product fell back to its forecast on 60 of 60 rows | ledger rows on the first build | One graded claim per work item at 0.5, 0.7 or 0.9 (H1, H2, H7); overconfident agent (H7) |
| Settled outcomes | Dated at the moment the work began, before the decision it answers | `engine/groundtruth.py` | Dated 30 to 90 minutes after the last step |
| Retries and step ids | The console emitter sends a fresh random span id on every call | `engine/emitter.py` | H9 sends every step twice with the same id |
| Retired and never-accepted agents | None | n/a | H9 |
| Orders: fleet plan, tier, Enterprise, discount, per agent | None. Every sim workspace has the empty per-agent order the migration wrote | `ag_workspace_orders` | H1 Growth, H2 Enterprise with 100 percent discount, H3 Startup, H5 Scale, H7 Startup, P1 to P3 Startup, H6 per agent |
| Pilots | None | n/a | 14 days, 3 days, ended unconverted, converted, extended |
| Notices recipients | None | `ag_usage_settings` has 3 rows | H1 and H2 name a second login |
| Upper ceiling | Platform figure only | `GET .../spend-ceiling` | H1 raised to 175 dollars a day with a reason |
| Declarations (limit, approved list, field map) | Two keys, sent by hand | `scripts/declare_context.py` | H4, H7 (first fleet only), H8 field map; Guardrails rows in H6 |
| Learned default checks | Never established on a sim fleet that was also kept | n/a | Established on H1 (60 sessions, no fault, nothing flagged) |
| Switched-off checks | None | n/a | H6 |
| Multi-fleet workspace | None | n/a | H7 (two fleets), H8 (five) |
| Previous months before the meter | Every tenant shows them; none was built to | meter began 2026-10-04 | Same for all; not separately built |
| Staff Activity rows | Only from the walk tenants | `ag_audit_log` | Every order, pilot change and ceiling change here writes one |
| fleet_doctor | Reads provy.config directly, and reads ServiceNow and GitHub secrets | `scripts/fleet_doctor.py` | Not run (secret door rule, and ServiceNow is off limits). `sim_assert.py` and the console-key hash query above do the wiring check by SELECT |
