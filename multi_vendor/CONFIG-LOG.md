# Config log: every change made to the two instances

Purpose: if either instance is lost, this and the scripts beside it rebuild the POC. Instances are named by role only (the PDI, the Servicely demo instance). No credential, token value or full hostname is recorded here. Record ids are not secrets.

Rebuild order: PDI with `servicenow/setup_pdi_estate.py`, Servicely with `servicely/setup_actions.py` then `servicely/setup_personas.py`, then the three hand steps in `servicely/PERSONAS.md`. To remove everything: `servicenow/pdi_tools.py teardown --apply` (PDI) and delete the records listed under Servicely below.

## Scope: not a scoped application

Everything on the PDI is **Global scope**, named with a prefix (`provy_iam`, `ProvyIam`, `u_provy_iam_`, `provy.iam`). Checked on 5 Nov 2026 by reading the scope of each record. A scoped application was not used because the API cannot create one (it needs Studio). Converting later would take: create the application in Studio, move each artifact into it with Transfer to Application, and accept that the REST namespace in the endpoint address changes, so Provy's pull URL must be set after the move, not before. `servicenow/export/` holds the record XML for a reload without the scripts.

## The PDI

| When | What | Record id | Why | Undo |
|---|---|---|---|---|
| 5 Nov, spike | Scripted REST API "Provy IAM Readings (POC)" | 660d8ff6831c1f1058589a96feaad39b | What Provy's outcome pull reads | `pdi_tools.py teardown --apply` |
| 5 Nov | Resource GET /readings | 870dcff6831c1f1058589a96feaad33e | The reading: account, groups, access left, systems checked | same |
| 5 Nov | Resource POST /apply | 1ed41fb6835c1f1058589a96feaad3d1 | Test door that applies a command to the estate | same |
| 5 Nov | Resource POST /seed | b6d45fb6835c1f1058589a96feaad33c | Test door that seeds a person | same |
| 5 Nov | Script include ProvyIamEstate | d1d4dbb6835c1f1058589a96feaad3a3 | The simulated estate and its read | same |
| 5 Nov | Script include ProvyIamWorker | 65d4dbb6835c1f1058589a96feaad3cc | Polls the Servicely queue and applies tasks | same |
| 5 Nov | Table u_provy_iam_access | 4ac4d7b6835c1f1058589a96feaad3e4 | One row per person and simulated system | same (drops its rows) |
| 5 Nov | Table u_provy_iam_hr_event | d4d49bb6835c1f1058589a96feaad342 | HR feed events | same |
| 5 Nov | Scheduled job provy_iam_worker (POC), INACTIVE | a3d45fb6835c1f1058589a96feaad3e3 | Runs the worker every minute once switched on | same |
| 5 Nov | Scheduled job provy_iam_hr_sync (POC), INACTIVE | cc1d033a831c1f1058589a96feaad36c | Re-creates an account (the sync scenario) | same |
| 5 Nov | Properties provy.iam.read_token (private), .fault, .queue, .servicely.url, .servicely.token (private) | 210d4ff6.., a6b493b6.., ebb4d3b6.., 64c417b6.., 95c497b6.. | Configuration, never in code | same |
| 5 Nov | Test people provy.iam.spike01 and provy.iam.test02, group "Provy IAM Spike Group", a membership | ab0dcff6.., 53e413f6.., 3f0d033a.. | Leaver test subjects | same |
| 5 Nov, evening | Property provy.iam.queue set to the queue name; provy.iam.servicely.url set to the Servicely base URL | as above | The worker's non-secret settings | clear the values |

Not ours, left alone: the job "Provy demo - verification sweep" and every sample record (641 active sample users).

## The Servicely demo instance

All created over the API on 5 Nov 2026 (evening). Every record is named "Provy IAM POC: ...". Nothing is switched on.

| What | Record id | State | Undo |
|---|---|---|---|
| Action provider instance "Provy IAM POC queue" (type async_integration, the queue name provy-iam-poc) | 621b390bd97811f1a174e2f805287795 | Active (it only matters once an Action uses it) | delete the record |
| Action provy-iam-disable-account | 636e1f05d97811f1a174e2f805287795 | inactive | delete |
| Action provy-iam-remove-groups | 63c8c151d97811f1a174e2f805287795 | inactive | delete |
| Action provy-iam-revoke | 641f1ecdd97811f1a174e2f805287795 | inactive | delete |
| Action provy-iam-apply-change | 64729618d97811f1a174e2f805287795 | inactive | delete |
| Action provy-iam-grant | 64c6f7c3d97811f1a174e2f805287795 | inactive | delete |
| Sub-agent Intake | a5cc7a5ed97811f1a174e2f805287795 | inactive | delete |
| Sub-agent Entitlements | a626594bd97811f1a174e2f805287795 | inactive | delete |
| Sub-agent Access agent | a67fc408d97811f1a174e2f805287795 | inactive | delete |
| Sub-agent Reviewer | a6d5fa75d97811f1a174e2f805287795 | inactive | delete |
| Persona Access orchestrator (its four sub-agents attached) | a72cf432d97811f1a174e2f805287795 | Paused, high-risk acknowledgement NOT set | delete |
| Persona Infrastructure agent | a78d63dbd97811f1a174e2f805287795 | Paused, high-risk acknowledgement NOT set | delete |
| System API token "Provy IAM POC: queue worker" (scope async_queue, path /controller/AsyncIntegration only, administrator privileges withheld) | 3f913687d97811f1a174e2f805287795 | Active, but it has NO SECRET yet | delete |

Two things done and not repeated: a probe action was created and deleted on 5 Nov (spike). Nothing existing on the instance was changed: no role, no user, no existing persona, no existing token.

## Found while doing this

- The token API creates the record and returns no secret, and there is no writable secret field (Token, TokenValue, Secret and Value all answer "The field does not exist"). The secret is generated in the Servicely UI, so that one step is Amit's.
- There is no built-in tool that runs an Action. The Action library has `Action.queueSubmit(actionId, record, options)`, so a custom tool script could call it. It is not created: the payload an Action produces is unknown until a real queue row is read.
- The Action executor controller is the UI path that queues an Action. A token needs that path to use it.


## ServiceNow-side agents (6 Nov 2026, evening)

Everything below is on the PDI, Global scope, and switched OFF. Nothing existing was changed except the worker script include (a flag branch that is not taken while the flag is false), the HR sync job body (an optional trace call that does nothing until tracing is configured) and the setup script.

| What | Why | Undo |
|---|---|---|
| Script includes ProvyIamPlan, ProvyIamModel, ProvyIamTrace, ProvyIamAgents | The applier, the verifier, the model client, the trace emitter | `pdi_tools.py teardown --apply`, or delete the four records |
| Scripted REST resources POST /agent-apply and POST /plan-check (same API, same bearer token) | Run the agentic path without Servicely; run the shared test vectors against the deployed copy | delete the two resources |
| Properties provy.iam.agentic (false), provy.iam.model.mode (stub), provy.iam.model.url, provy.iam.model.key (private), provy.iam.model.name, provy.iam.trace.url, provy.iam.trace.key (private), provy.iam.trace.mode | Configuration, never in code. The two private ones are set by hand | clear the values |
| ProvyIamWorker updated: takes the agent path only when provy.iam.agentic is true | The switch | set the property to false (the plain path then runs exactly as before) |
| Job provy_iam_hr_sync body updated: reports to the trace as its own actor when tracing is configured | A named actor on the map | none needed |

**Found while doing this**
- Rerunning `pdi/setup_pdi_estate.py` used to set both scheduled jobs inactive, which would have stopped the running worker. It now creates a job inactive and, on a rerun, updates the script and period but never the active flag. Checked: the worker stayed active and the sync job stayed inactive through several reruns.
- The instance hands an empty JSON list to a script as a different kind of list (no `length`). The plan check now accepts both, and the plan-check door caught it: one shared test vector disagreed on the instance until it was fixed.
- A trace parent is an opaque `parent_session` string from the task payload, passed through unchanged. The PDI never derives a parent (the collector names sessions by conversation id, which this instance cannot know).
