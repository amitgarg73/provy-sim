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
