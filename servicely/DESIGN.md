# Servicely pack: design and what is proven

Status 15 Oct 2026. Branch `feat/servicely-client-scaffold`. Nothing here is wired to a Provy tenant yet.

## Shape (same as the ServiceNow ITSM pack)

| Piece | ServiceNow | Servicely |
|---|---|---|
| Generator raises real incidents | `engine/servicenow.py` Table API, `correlation_id` marker | `engine/servicely.py`, `POST /v1/Incident`, marker prefix on `ShortDescription` |
| Work and resolve them | agent writes via Table API | BLOCKED over REST (see question 1) |
| SoR pushes the settled outcome | business rule `servicenow/outcome_push.js`, `x-provy-key` | outbound webhook on Incident (table trigger), payload shape to confirm |
| Reopens for something to detect | `servicenow/verification_sweep.js` | scheduled script or trigger, same blocker as transitions |
| Provy never reads the SoR | yes | yes: the webhook pushes; REST reads here are for the sim and for evidence |

## Proven on the demo instance (15 Oct 2026)

- Inbound token (`SERVICELY_API_TOKEN`) with `/v1/**` and admin works as Bearer. Basic auth does not (passkey MFA).
- `POST /v1/Incident` creates a record (INC0000003).
- Resolve then continue-work in the UI leaves `ResolutionCount = 2`, `PreviousWorkflowStatus`, `PreviousTransition`, `CompletedAt/By`, and one `WorkflowHistory` row per transition.
- `WorkflowStatus` PATCH returns 422 "System managed field". `WorkflowTransition` and `Transition` are not fields.
- Outbound webhook form: Name, Key, Active, Trigger type (Table), Table, Request URL, Http method, Outbound token, Trigger configuration, Payload. Delivery log table is `WebhookDeliveryLog` (empty).
- SoFi auto-resolve classifiers are active; no resolution has run (ActionResult empty).

## Signals the push can carry (derivable from the record)

`entity_id` = `Number`; `business_date` = day the agent did the work (CreatedOn), not the push day; `occurred_at` UTC with Z from `CompletedAt`;
`resolution_persists` = not reopened (`ResolutionCount <= 1` and no `continue_work_from_resolved`);
`self_resolved` = `ReassignmentCountGroup <= 1`; `resolution_genuine` needs a closure code (field exists on the Closure tab, optional).
Not pushed because the instance does not settle them: SLA verdicts until SLAs are configured here, procedure grounding.

## Open questions (asked of Servicely engineering)

1. Supported way to execute a workflow transition from outside (server script via inbound webhook or system controller).
2. Outbound webhook payload: previous status and transition name; delivery visibility.
3. What makes SoFi auto-resolve run end to end on this instance.
4. Whether a reopen can carry a reason.

## Not built, on purpose

- No pack.py and no console copy of the contract: the contract is defined twice (`provy-sim-control/lib/packs.ts`), and the Provy tenant does not exist yet. Build both copies together and run `tests/test_console_contract_parity.py`.
- No transition code: `ServicelyClient.transition` raises until question 1 is answered.


## Update 15 Oct: the mechanism exists on the instance (read from REST, nothing written yet)

The state-change blocker is not a blocker for the SoR-side pattern. The instance runs server scripts, and they are REST-writable config:

| Need | Servicely object (REST table) | Fields | Evidence |
|---|---|---|---|
| The platform applies a resolution / the "business rule" | `Trigger` (146 rows) | Name, Table, TriggerWhen (before/after/async), OnCreate/OnUpdate/OnDelete, Order, Script, Active, StopOnException | 11 Incident triggers read; syntax `current.Field()`, `previous.Field()`, `operation`, `.hasChanged()` |
| Scheduled sweep (reopen, close) | `Job` (26 rows) | Name, Schedule (cron), Script, Active | Job API doc; Administration > Configuration > Jobs |
| Outbound push to Provy | `HTTP.post(url).header(k,v).body(s).execute()` inside a Trigger or script library; response `getStatus()/getBody()` | | read from `SofiExport` / `SofiChat` libraries |
| Where the ingest key and URL live | `ApplicationProperty` (244 rows), `PropertySet` (7) | | |
| State move from a script | `Workflow.forceToActivity(name, current, explicitUpdate, enableTriggers)` | | Workflow API doc (only documented method) |

Platform facts worth reusing: a before-update trigger "Incident - Resolution Count / First contact resolution behaviour" increments `ResolutionCount` when WorkflowStatus becomes resolved, so ResolutionCount is a platform-maintained resolution counter and a value above 1 means resolved, then back, then resolved again.

Revised flow: the sim POSTs incidents and writes the agent's resolution fields over REST (works), an instance Trigger moves the state, a Trigger pushes the settled outcome to Provy, a Job reopens a share of resolved tickets. Still unverified: `forceToActivity` behaviour on an Incident, and the outbound call from a trigger on this instance.
