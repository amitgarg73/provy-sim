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

| Agent decision (tagged in the trace) | Journey consequence | Evidence in the record | Target it breaks |
|---|---|---|---|
| routed to the wrong group | queues in that group, then reassigned | `reassignment_count` > 1 | **resolution**, handled-without-handoff |
| shallow diagnosis, skipped the procedure | goes on hold awaiting caller detail it could have found | hold duration, later resolution | **resolution** |
| weak fix (workaround, advice, no fault found) | resolution does not hold | `reopen_count` > 0, `close_code` | stays resolved, genuine fix |
| *nothing the agent decides* | the ticket waits to be picked up | time to assignment | **response** |

**Corrected 28 July, after the first run falsified the original table.** It said a misroute breaks
the RESPONSE target. It does not. The response SLA stops on `Assignment group is not empty`, which
routing satisfies within seconds of pickup, so every wait after that point happens on a clock that
has already stopped. Measured: a ticket that sat 461 seconds with the wrong team used **2%** of its
response target.

What a mistake made after assignment actually costs is resolution time. Response time is decided by
how long the ticket waited to be picked up, and that is capacity, not a decision. Both facts are
worth stating plainly rather than pretending every miss has an agent behind it.

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

One compression factor, 240x, applied to the instance's own targets, so every ratio a real desk has
survives. Both families are installed: response alone was compressed at first, which left the
mistakes made after assignment with nothing they could breach.

| Priority | Response | Resolution |
|---|---|---|
| P1 | 5s | 1 min |
| P2 | 15s | 2 min |
| P3 | 60s | 6 min |
| P4 | 2 min | 12 min |

Each wait is sized against the promise it can still threaten: the wait before pickup against
response, everything after assignment against resolution.

| Delay | Length | Effect |
|---|---|---|
| waiting to be picked up | 0.05-0.35x the response target | inside, unless the desk is saturated |
| right group, queue | 0.05-0.2x the resolution target | comfortably inside |
| wrong group, before someone notices | 0.8-1.5x the resolution target | breaches, and the reassignment says why |
| on hold awaiting caller | 0.5-1.1x the resolution target | breaches on a tight target, survives on a loose one |

A P4 misroute may still come in under its 12-minute target while a P2 misroute blows its 2-minute
one. That is correct: the same mistake costs more on a tighter promise.

## Known wrong, measured on the 28 July runs

- **P1 response is unusable at this compression.** 15 minutes becomes 5 seconds, and nothing is ever
  picked up that fast, so every P1 breaches automatically (observed: 6240% of target). Needs a floor.
- **The desk works one journey at a time.** The waits overlap but the REST and LLM calls do not, and
  that serialised work is roughly 300s across a run. It is unattributed, and on a P1's 60-second
  resolution target it alone blows the promise.
- **Leftover backlog distorts the next run.** Tickets a previous run left unworked are picked up
  oldest-first and carry their full age, so they breach on staleness rather than on anything the
  current run did.
