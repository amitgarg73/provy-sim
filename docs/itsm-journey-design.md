# ITSM: model the ticket's journey, not a single decision

## The problem

The agent triages, routes and resolves in one continuous action. Nothing it does costs time. A
misroute writes a wrong assignment group into the record and the ticket carries on as if nothing
happened.

So the only thing that can make a response target breach is the wall clock of the batch itself.
Measured on the 28 July run: tickets in positions 1-7 met the target and 8-12 breached it, a clean
step function, because every ticket opened in the same six seconds and the agent worked them at a
fixed cadence. The failure was decided by queue position and nothing else.

That is why the incident card says "no shared cause yet" and cannot do better. A condition settled
by position has no cause in the data. Tuning arrival rates cannot fix it: it changes which tickets
breach without ever making a breach mean something.

## The principle

**Time must pass for a reason, and the reason must be a decision the agent made.**

On a real desk a misroute causes the breach. The ticket goes to the wrong group, sits in that
queue, someone notices, it gets reassigned, and the response target is gone. The cause is then
visible twice: as a reassignment in the ServiceNow record, and as a routing decision in the trace.
That is the root cause the product cannot currently name.

## The journey

Every state below is a real ServiceNow state the instance already timestamps.

```
New ─▸ Triage ─▸ Assigned to group ─▸ In Progress ─▸ Resolved ─▸ Closed
                        │                  │                  └▸ Reopened ─▸ second line
                        │                  └▸ On Hold (awaiting caller) ─┘
                        └▸ [wrong group: waits, then reassigned]
```

Time is spent in three places, and each one is bought by an agent decision:

| Stage | Well handled | Badly handled | What bought the delay |
|---|---|---|---|
| Group queue | picked up quickly | sits, then reassigned | routing decision |
| In Progress | worked straight through | on hold awaiting the caller | diagnosis depth |
| After resolve | stays closed | comes back | fix quality |

## The attribution map

Every failure the contract can grade traces to a tagged step in the run. This is the table the
whole design exists to produce.

| Agent decision (tagged in the trace) | Journey consequence | Evidence in the record | Condition it breaks |
|---|---|---|---|
| routed to the wrong group | queues in that group, then reassigned | `reassignment_count` > 1, long time to first response | response target, handled-without-handoff |
| shallow diagnosis, skipped the procedure | goes on hold awaiting caller detail it could have found | hold duration, later resolution | response and resolution targets |
| weak fix (workaround, advice, no fault found) | resolution does not hold | `reopen_count` > 0, `close_code` | stays resolved, genuine fix |
| could not fix it, escalated | second line picks it up | `reassignment_count` > 1 | handled without handoff |

Read the other way: a breached response target now has a cause the product can name, because the
ticket sat in the wrong queue and the trace says which agent put it there.

## What is NOT being built

**Reopen causality already exists** and is not being rebuilt. `servicenow/verification_sweep.js`
derives the reopen probability from the record: non-fix 3.0x, workaround 1.8x, wrong team 1.6x,
wrong category 1.3x, and 0.3x when it was done properly. It is driven by what the agent actually
did. The base rate (`provy.demo.reopen_pct`) is the only thing to revisit.

**The simulation still reports no outcomes.** ServiceNow settles every ticket and pushes the result.
Nothing here changes that, and nothing here may.

## The one consequential decision: tickets run concurrently

Waiting is real elapsed time, because the SLA engine computes from the record's own timestamps and
there is no honest way to fake them.

Sequentially that is unaffordable and wrong. A ticket held for five minutes would block every ticket
behind it, so the wait would land on the wrong tickets and the position artefact would come back in
a new form. A desk does not work that way: several tickets are in flight and their waits overlap.

So the ITSM run becomes a small scheduler. Each in-flight ticket carries the time its next action is
due; the runner advances whichever is due next and sleeps when none are. Wall clock for a run
becomes the longest single journey rather than the sum of all of them.

Consequences to accept:
- a run takes roughly 10-15 minutes instead of 7
- sessions overlap in wall-clock time, which Provy already supports
- the generic sequential runner is untouched; this lives in the ITSM path only

## Timings

Anchored to the compressed response targets (P1 15s, P2 1m, P3 4m, P4 8m), so a delay is measured
against what the ticket actually promised rather than a number picked to look right.

| Delay | Length | Effect |
|---|---|---|
| right group, queue | 0.15x the target | comfortably inside |
| wrong group, queue before someone notices | 1.2-2.5x the target | breaches, and the reassignment says why |
| on hold awaiting caller | 0.8-1.5x the target | breaches on a tight target, survives on a loose one |
| working the ticket | seconds | unchanged |

A P4 misroute may still come in under its 8-minute target while a P2 misroute blows its 1-minute one.
That is correct: the same mistake costs more on a tighter promise.
