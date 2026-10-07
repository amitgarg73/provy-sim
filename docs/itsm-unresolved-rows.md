# The five unresolved ITSM Demo rows

Written 7 October 2026 for argus#1649. Read only: nothing in ServiceNow or in Provy was changed to write this.

## What they are

ITSM Demo (pre-prod tenant cb58d3cd-249e-474c-8b6c-487f2ab8f1ef) holds 84 work items. 53 matched, 26 diverged, 5 are unresolved. The five are INC0010186 and INC0010188, INC0010189, INC0010190, INC0010191. All five are closed in ServiceNow. Provy has the session and the agent's forecast for each, and no outcome.

| Incident | Closed in ServiceNow (UTC) | Reopen count | Provy ledger |
|---|---|---|---|
| INC0010186 | 2026-09-29 13:41:24 | 1 | unresolved, no outcome time |
| INC0010188 | 2026-09-29 13:40:55 | 0 | unresolved |
| INC0010189 | 2026-09-29 13:41:21 | 0 | unresolved |
| INC0010190 | 2026-09-29 13:41:23 | 0 | unresolved |
| INC0010191 | 2026-09-29 13:41:21 | 0 | unresolved |

## Why the push never landed

ServiceNow tells Provy how a ticket ended with a business rule ("Provy demo - outcome push", the file `servicenow/outcome_push.js`). It fires once, when a ticket changes to Closed.

The instance log for 29 September shows that it did fire for all five, and that every push failed in the same way:

    13:41:20  [provy] push failed for INC0010188: HTTP 0
    13:41:21  [provy] push failed for INC0010189: HTTP 0
    13:41:23  [provy] push failed for INC0010191: HTTP 0
    13:41:24  [provy] push failed for INC0010190: HTTP 0
    13:41:25  [provy] push failed for INC0010186: HTTP 0
    13:41:25  [provy] verification sweep: reopened=0 closed=4 finished_by_human=1

HTTP 0 is what ServiceNow reports when it got no answer from the other side at all (no connection, a reset, a timeout). It is not a refusal. A refused key reads 401 and a blocked deployment says so by name; neither appears. Provy agrees: for the whole window there is no refused request for the fleet, no held outcome, no outcome row and no row in the "not applied" table for any of the five. The requests never arrived.

The rule had no second chance. It logged one line, did not ask ServiceNow for the error text, kept no record of the ticket and nothing ever looked at the ticket again. Provy cannot fetch an outcome itself by design (it is never given access to the instance), so the work item stays unresolved for ever.

It was a moment and not a fault in the rule or the key:

- The sixth ticket of the same batch, INC0010187, closed twenty minutes later (14:00:55). Its push landed on the first try and it reads "matched".
- In the push log the instance still holds (9 September to 6 October), the only failures are these five (HTTP 0, 29 September) and 21 on 10 September, when the ingest key had been replaced and the instance still held the old one (HTTP 401, fixed the same day). Every other push landed on the first try.
- The key held by the instance, the key in the simulator console and the one active key in Provy are the same key today (`scripts/fleet_doctor.py`, run read only on 7 October).

What the evidence does not show is why there was no answer at that minute. The old rule never read the error message and neither side logged the connection. Five failures in five seconds, one after another, points at something on the path being unreachable for a few seconds and not at five separate problems.

## What was fixed, forward

`servicenow/outcome_push.js` in the repo now:

1. tries a push that got no answer (or a 408, 429 or 5xx) up to three times, with a short pause, and logs the error text ServiceNow holds;
2. keeps a ticket whose push still did not land, or was refused for a reason a person can fix later (401, 403, a Vercel block), in the property `provy.push.pending`, and logs `PUSH PENDING`; a refusal of the payload itself (400, 404, 422) is not kept, because it would fail the same way for ever;
3. on every later closure, first pushes up to five pending tickets again from the record as it stands, and removes each one that lands. A push that had in fact arrived is harmless, Provy answers "already settled".

The rule still writes nothing to an incident. Tests: `tests/test_outcome_push_rule.py` runs the rule in node against a fake instance (18 cases; the old rule fails 11 of them).

**This is not applied to the instance.** The file in the repo and the rule in ServiceNow now differ. To apply it, someone runs `scripts/install_servicenow_lifecycle.py` against the instance, which is a founder decision (and the instance is due to be reclaimed after 8 October 2026 unless kept).

## Why the five rows stay as they are

Pushing them now would mean reopening five closed tickets and changing their reopen counts, or posting an outcome by hand that ServiceNow did not send. Either one is the simulation marking its own homework, which is the thing this fleet exists to avoid. The recommendation in the issue was to leave them, and they are left. They are honest history: five work items whose result Provy was never told.

What the five look like on screen: unresolved, no outcome, counted as waiting, not as failures. The headline is not affected beyond a denominator of 79 settled out of 84.

Do not rerun the fleet to "fix" them (that makes more unsettled sessions and changes nothing) and do not edit the tickets.
