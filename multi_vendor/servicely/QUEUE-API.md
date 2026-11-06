# Servicely asynchronous queue: how an outside system takes work and replies (5 Nov 2026)

Why this matters: a Servicely agent cannot make an outbound call from a tool script (nothing found). It acts on an outside system by queueing an Action. That is the whole mechanism. The outside system polls the queue over REST, does the work, and posts a result. The ServiceNow PDI worker in this POC is that outside system.

Each fact is marked. **Observed** (read from the demo instance, or from the published Node-RED package source, version 0.1.4) or **inferred** (reasoned, not tested). Nothing was written to the instance for this note. No token value is recorded here.

## How an Action becomes a queued task

- **observed:** an Action record names a provider instance, a `Command`, a table, `SideEffects`, `ResultStatusKey` and `ResultTextKey`. The only provider is "Asynchronous queue" ("allows integration from any system supporting HTTP requests"), with one instance "Node-RED (Default)" of connection type `async_integration`.
- **observed (Action script library):** the provider instance's `ConnectionString` is used as the **queue** name. The Action's `Command` is used as the **subject**. The task is a row in the `AsyncQueue` table with `Queue`, `Subject`, `Payload` and a state. The payload is built from the action's parameters.
- **observed:** the demo action "Remote ping" has Command `ping-demo`, table Incident, `SideEffects` true, `ResultStatusKey` `status`, `ResultTextKey` `payload`.
- **inferred:** for our own work we create our own provider instance (type `async_integration`, our own queue name) and one Action per command. Creating an Action over the API works (tested 5 Nov, created and deleted an inactive one). Creating a provider instance has not been tested.

## The endpoint

- **observed (Node-RED source):** `POST <instance base URL>/controller/AsyncIntegration`, JSON body, JSON reply. One URL serves dequeue and all replies; the body's `action` field chooses.
- **observed (instance):** the controller is a system controller named `AsyncIntegration`, described as "Controller to manage the Async Integration API". It answers `400 Unsupported action [x]` for anything but the four actions below, and returns errors as `{"_error": "..."}`.

## Dequeue (take work)

Request body (observed, Node-RED source and controller script):

```
{ "action": "dequeue", "queue": "<queue>", "subject": "<command>", "identifier": "<who is polling>", "request_count": 1 }
```

- `queue`, `subject` and `identifier` are required (400 if missing). `request_count` defaults to 1, must be a whole number of at least 1, and is capped at 1000.
- A combined form takes `"subjects": [ {subject, identifier, request_count}, ... ]` for several commands in one call (limits: 50 subjects, 1000 actions in total). Instances that predate it ignore `subjects`.
- Reply (observed): `{ "data": [ { id, queue, subject, source, payload, state, sequence, created_on, claimed_by, claimed_at }, ... ] }`. `data` is a list. The oldest ready action is claimed first and moves to state `processing`.
- `payload` is text. When it starts with `{` or `[` the Node-RED node parses it as JSON (observed).
- A dequeue takes a per-queue lock for up to 10 seconds. If it cannot, the reply is `503 busy, nothing was claimed` (observed). Just retry.
- The queue `webhook.outbound` belongs to the platform. A dequeue on it gets 403 (observed).

## Reply (give the result)

Request body (observed, Node-RED source):

```
{ "action": "success" | "fail" | "status", "reply_to": "<the data[].id>", "identifier": "<claimed_by>", "status": "<text>", "payload": <text or JSON>, "error": "<text, for fail>" }
```

- `reply_to` is required. `success` and `fail` finish the action. `status` records progress. The action stays claimed.
- An object or array payload should be sent as JSON text (observed: an Intelligent Action fails on an object or array).
- `fail` takes its description from `error`, else from `payload` (observed).
- A reply to an unknown id is 404. A reply to an action that already timed out, or is not being worked on, is 409 (observed in the library). The Node-RED package notes the instance can answer an unknown id with a 500 script error that does not say so (observed in package source).
- A claimed action with no reply or status update inside the claim timeout is failed with reason "timeout". The default is 60 minutes (`async.integration.claim.timeout.minutes`, observed).
- A claimed action can be handed back with a release (library function `_releaseClaim`, observed). It is used internally when a dequeue fails part way.

## Authentication

- **observed (Node-RED source):** the package supports HTTP Basic, `Authorization: Bearer <token>`, and HMAC headers over the date or the body.
- **observed (instance):** inbound credentials are System API tokens. Nothing else. Each has `URLAllowedPaths`, `ValidationType`, `ImpersonateUser` and `AdministratorPrivileges`. The three existing Provy tokens are limited to `/_webhook/v2/provy_pull`, `/_webhook/v2/provy_receipt` and `/v1/**`.
- **observed (tested 5 Nov):** the existing read token (`/v1/**` only) gets an empty `401` from `/controller/AsyncIntegration`, which is how the instance answers an unauthorised path.
- **inferred:** the worker needs its own System API token whose allowed paths include `/controller/AsyncIntegration`, acting as a user that may work the queue. Creating a token is a credential grant, so it is a hand step for Amit (see PERSONAS.md). It must be stored on the PDI in a private system property and never in a script.

## What is not known

Short list.

- Whether a bearer token with `ValidationType` none is accepted on the controller (the package supports it; not tried).
- The exact parameter names an Action passes into `payload` for a command with inputs. Create one action and read the `AsyncQueue` row it produces.
- Whether an empty queue answers `{"data": []}` or a 404. The Node-RED package treats a missing or non-list `data` as an error, so an empty list is the likely answer.
- How the persona's tool list reaches an Action (which tool triggers it), and whether `SideEffects` true makes the platform ask for the high-risk acknowledgement. Check when the first persona is built.

## Source

- Instance: system controller `AsyncIntegration`, script library `AsyncIntegration`, script library `Action`, the `ActionProviderInstance` and `Action` tables.
- Package: `node-red-contrib-servicely` 0.1.4 on the npm registry (`servicely-queue.js`, `servicely-common.js`), read as text and never run.
