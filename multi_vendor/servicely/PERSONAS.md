# Personas and hand steps for the leaver scenario

For Amit. Most of this is now created (see below). The harness refuses permission grants from me, so the roles, the high-risk acknowledgement and the token secret stay yours. Names are all "Provy IAM POC: ...".

## The personas

| Persona or agent | Does | Record type | Tools (existing ones, by name) |
|---|---|---|---|
| **Access orchestrator** (a persona) | Takes a leaver, mover or joiner request, picks the next agent, closes the request | Request (same record type as the Service Desk Agent) | its four sub-agents below, "(General) Fetch any record" |
| Intake (sub-agent) | Reads the request, pins down who and what kind | none of its own | "(User Management) Lookup User by Name", "(General) Fetch any record" |
| Entitlements (sub-agent) | Works out which systems hold access for this person | none | "(General) Fetch any record" |
| Access agent (sub-agent) | Queues the change: disable the account, revoke in the named systems | none | the Actions "Provy IAM POC: provy-iam-disable-account", "...revoke", "...apply-change" |
| Reviewer (sub-agent) | Checks the result against the request before closing | none | "(General) Fetch any record" |
| **Infrastructure agent** (a persona) | Takes a change raised by the orchestrator and queues it | Request or Change | the Action "Provy IAM POC: provy-iam-apply-change" |

Short system prompts (paste as written):
- Orchestrator: "You handle joiner, mover and leaver requests. Delegate to Intake, then Entitlements, then the Access agent, then the Reviewer. Never close a request yourself before the Reviewer has answered. If any agent fails, hand the request to a person and say which one failed."
- Intake: "Read the request. Return the person, the kind of change (joiner, mover, leaver), the effective date and the systems named, as plain fields."
- Entitlements: "For the person, list the systems that hold access. Return the list and nothing else."
- Access agent: "Apply the change to every system in the list you were given. Use the matching action once per system or once for all. Report exactly what you queued."
- Reviewer: "Compare what was queued with the request. If they differ, say so. Do not claim the access is gone: you have not looked at the systems."
- Infrastructure agent: "Queue the change you were given for the named systems. Report what you queued."

## Done by script on 5 Nov 2026 (evening)

`setup_actions.py --apply` and `setup_personas.py --apply` created, all named "Provy IAM POC": the queue provider instance, five Actions (inactive), four sub-agents (inactive), and the two personas (Paused, the orchestrator with its four sub-agents attached, high-risk acknowledgement not set). The worker's token RECORD exists with the narrowest limits. Ids and undo steps are in `../CONFIG-LOG.md`. The PDI's queue name and Servicely URL properties are set.

## Hand steps still needed, in order

1. **Generate the worker token's secret.** Servicely, System API tokens, "Provy IAM POC: queue worker": generate it and copy the secret once. Put it in the PDI system property `provy.iam.servicely.token` (private) and nowhere else, never in chat. Why it is yours: the API creates the record and has no field for the secret.
2. **Switch on the worker.** On the PDI, activate the scheduled job "provy_iam_worker (POC)". Tell me and I test it end to end.
3. **Queue one real task.** Activate the Action "Provy IAM POC: provy-iam-apply-change", open a Request whose short description starts with [provy-iam-poc], and run the action on it once from the UI. I read the queue row it makes (read only) to learn the payload, then build the one tool that lets a persona queue an Action, and test the whole loop.
4. **Agent users, roles and acknowledgement.** For each persona (Access orchestrator, Infrastructure agent): create its agent user, give that user the roles `itsm_agent`, `knowledge_manager` and `administrator`, tick the high-risk acknowledgement, set auto-approve, then set the persona to Active and the four sub-agents to Active. Do this last: editing a persona's tools or agents clears the acknowledgement. I did not create the users because that step is only useful with the roles.
5. **For the re-created-by-a-sync scenario only:** activate "provy_iam_hr_sync (POC)" on the PDI.

## What I will not do
Grant roles, tick the acknowledgement, copy any token, or touch the demo users. If a step above fails, I stop and say which.
