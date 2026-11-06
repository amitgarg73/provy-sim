# The ServiceNow-side agents

Until now the PDI side was one scheduled job that took a task from Servicely's queue and applied it. This adds real agents on the same job, behind a switch. The switch is off,
so nothing described here runs until someone turns it on.

## What exists

| Piece | What it is | Uses a model |
|---|---|---|
| **Estate applier** | Takes the queued task, asks the model client for a plan, checks the plan against an allow-list, applies it, replies to the queue | yes, proposes only |
| **Estate verifier** | After the applier, reads the estate back and says, per system, whether the access really changed | wording only |
| **HR sync** | The scheduled job that can undo an offboarding. Unchanged, but it now reports to Provy as its own named actor | no |
| **Model client** | One place a model is called from. Any OpenAI-compatible endpoint, or an offline stub | n/a |
| **Trace emitter** | Reports a session to Provy with a step per action. Does nothing until configured, and never fails a task | n/a |

Script includes (`pdi/src/`): `ProvyIamPlan` (the allow-list, the task's own plan and the verdict, all pure), `ProvyIamModel`, `ProvyIamTrace`, `ProvyIamAgents`.
Two test doors on the readings API: `POST /agent-apply` (the agentic path, answers 409 while the flag is off) and `POST /plan-check` (runs the shared test vectors against the deployed copy).

## The two rules that make a model safe here

1. **A model only proposes.** A plan is applied only if it passes the allow-list: this POC's five commands, the task's own `provy.iam.*` person, and only the systems the task named
   (all four when it named none). A refused plan is never applied. The task's own plan is used instead, which is exactly what the plain worker does, and the refusal is written in the trace.
2. **A model never decides the outcome.** Held or not held is computed from the estate's own reading. The model may only word the finding. A missing field is never read as "ended".

## What the verifier's answer does NOT do

It goes to the trace and nowhere else. It is deliberately **not** put into the reply to Servicely. The Servicely agents keep reporting what they were told, and the gap between that and
what the verifier read is what this proof exists to show, and what Provy is there to catch.

## Switches (system properties on the PDI; none is a secret except the two marked)

| Property | Default | Meaning |
|---|---|---|
| `provy.iam.agentic` | `false` | `true` runs each queued task through the agents. `false` is the plain worker, unchanged |
| `provy.iam.model.mode` | `stub` | `stub` is offline and deterministic. `live` calls the provider below; with any of url, key or name unset it falls back to stub and says so in the log |
| `provy.iam.model.url` | empty | chat-completions URL of an OpenAI-compatible provider |
| `provy.iam.model.key` | empty | **private.** Set by hand |
| `provy.iam.model.name` | empty | model name |
| `provy.iam.trace.url` | empty | Provy's base URL, no path |
| `provy.iam.trace.key` | empty | **private.** The ingest key of the "Access provisioning" workflow. Set by hand |
| `provy.iam.trace.mode` | empty | `live`, `log` or `off`. Empty means live when url and key are set. `log` writes what would be sent to the system log, never the key |

To try it with no keys: set `provy.iam.agentic` to `true`, run `python scripts/run_multi_vendor.py run vpn_missed --agentic` (it switches the flag on for the run and always back off),
and read the answer. Set `provy.iam.trace.mode` to `log` to see the traces it would send in the instance's system log.

## What the Servicely side must supply (the main session applies this)

The worker reads two optional fields from the task payload, which today is `{person, systems}`. Both come from the Action's **parameter script** on the Servicely side:

- **`parent_session`** (opaque string): the asking system's session id **in the exact form Provy's collector names it**, `pull-servicely-<conversation id>`. It is passed through unchanged as
  `parent_external_id`, and **no parent is sent when it is absent**. Nothing is derived on the PDI: this instance does not know how another system names its sessions.
  Until the Servicely script can supply it, PDI sessions open with no parent and no chain is drawn.
- **`item`** (plain string): the Servicely work item id (for example `current.ID()`), kept only as a reference in the session's metadata.

## Tests and how they fit together

The PDI cannot run a server script from outside, so the rule that decides what may be applied is written twice and tested three ways against one set of vectors (`tests/pdi_vectors.json`, 33 cases):

1. `multi_vendor/pdi_reference.py`, the Python twin: `tests/test_pdi_agents.py`.
2. The real `ProvyIamPlan.js` run in node: the same test file.
3. The copy deployed on the instance, through the plan-check door: `scripts/verify_pdi_agents.py`.

If the three ever disagree, the vectors name the case. Not covered by a test, because it needs a key: a live model's plan and a live Provy ingest. The stub and the log mode cover the shapes.
