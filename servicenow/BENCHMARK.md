# What a real ServiceNow desk actually looks like

The ITSM fleet's incident mix was invented from ITIL rules of thumb. It is now
calibrated against measured data instead.

**Source:** Incident Management Process Enriched Event Log, UCI Machine Learning
Repository, dataset 498. An event log from the audit system of a real ServiceNow
instance at an IT company: 141,712 events across 24,918 incidents.
Licence CC BY 4.0, so it can be used commercially with attribution.
<https://archive.ics.uci.edu/dataset/498/incident+management+process+enriched+event+log>

Figures below are the final state of each incident, one row per incident.

| Measure | Real instance | What the sim assumed before |
|---|---|---|
| Priority mix | **P3 94.2%**, P4 3.1%, P2 1.6%, P1 1.1%, no P5 | P3 40%, P4 30%, P5 15%, P2 12%, P1 3% |
| SLA attainment | **63.4% met, 36.6% missed** | 100% met |
| Reopen rate | **1.1%** (245 of 24,918) | 30% |
| Reassignment 0 / 1 / 2+ | **54.4% / 25.0% / 20.6%** | every ticket 1 |
| Contact type | 99.1% phone | four-way spread |

## What was changed, and what was deliberately not

**Priority mix: matched.** A service desk being almost entirely P3 is not a quirk
of this company, it is what ITIL priority assignment produces. The old spread put
a third of tickets at P4 and P5, which is not a desk anybody runs.

**Reopen rate: NOT matched, and this is a judgement call worth defending.** One
percent is the real figure, and at 500 incidents it would yield five reopens,
which is noise rather than a demo. The rate is set to 6%: a desk performing worse
than the benchmark, which is the honest thing for a demo of an agent whose
resolutions are meant to be scrutinised, and which yields around 30 reopens at
500 incidents. **Say the real figure out loud when showing this.** A prospect who
knows ITSM will know 30% was fiction, and quoting the benchmark alongside our
dialled-up rate turns a weakness into a credibility marker.

**Contact type: NOT matched.** 99.1% phone is this one company in the mid-2010s,
not service desks in general, and self-service has since taken over most of that
volume. Copying it would import a dated artifact of one organisation rather than
a property of the domain.

**Close codes: cannot be matched.** They are anonymised in the log ("code 6",
"code 7"), so they carry no mapping to the ten out-of-the-box codes the instance
actually uses.

**Categories and ticket text: cannot be taken from here at all.** The log is
anonymised and the authors deliberately excluded every free-text attribute, so
categories read "Category 42". The agent classifies from the incident text, so
that text still comes from the templates in `scripts/seed_itsm_incidents.py`,
written against the instance's own six categories.

## The SLA number is the interesting one

A real desk misses SLA on **more than a third** of its incidents. This demo
currently reports every single incident as having met it, because a compressed
run resolves tickets in seconds and the instance's response targets run from 15
minutes for a P1 to 40 hours for a P5. Nothing can breach a four-hour target in
four seconds.

That is a real gap, not a cosmetic one: `made_sla` is one of the four contract
conditions, and right now it can never fail, so it grades nothing.

Two honest ways to close it, and one dishonest one:

1. **Real elapsed time.** Seed the backlog, let it age past its response target,
   then work it. Genuine, and it needs the run paced over hours.
2. **A demo SLA definition with a compressed target** (a "Provy demo response"
   SLA of a few minutes) alongside the compressed reopen loop. The SLA engine
   still computes the result; only the target changes, and an aggressive target
   is an ordinary customer configuration choice. This keeps the whole timeline
   consistently compressed.
3. **Writing `made_sla` directly.** Never do this. It is the one field a third
   party computes for us, and faking it removes the only reason this demo exists.
