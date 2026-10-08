# The ITSM pack against the current product

Written 7 October 2026 for argus#1649. Pre-prod and the ServiceNow PDI dev193647 only. ITSM Demo was not touched: its 84 sessions, 910 steps, 84 ledger rows, 16 held outcomes and 1,659 outcome rows are the same before and after, and so are the 230 incidents on its marker and the six `provy.*` properties on the instance.

## What the pack sent before, and now

Counted from the stored steps on pre-prod. "Before" is ITSM Demo as it stands (84 sessions); "now" is the validation tenant (24 sessions). The pack's work per ticket is the same, so compare the shares.

| | Before (ITSM Demo) | Now (parity tenant) |
|---|---|---|
| Steps | 910 | 244 |
| Steps carrying a context record | 0 | 100 (every step that ran a model) |
| Steps that ran a model | 476 | 100 |
| Steps that ran code only (no model, no record) | 0 | 32 (the router, which is a rules-table lookup) |
| Steps carrying a claim with a stated confidence | 420 | 120 (168 claims, 15 distinct confidences from 0.64 to 0.97) |
| Agents on the roster | 5 | 5 working plus 1 retired (`triage_v1`) |
| Order, declared limit and approved list, notice recipient | none | Startup fleet plan, 30 day limit and four approved sources, one named recipient |

Claims already carried a stated confidence on ITSM Demo (420 steps); the issue's reading that there were none is not what pre-prod holds. What changed for claims is that each one is now listed in the ground truth, and the assertions check them.

What the product says about the new tenant (`docs/sim-evidence/itsm-parity/assert.json`): 34 comparisons, 34 pass. Ingestion reads ready, 6 of 6 signals sent, 15 of the newest 20 decision steps counted and 5 not counted (the router's). All 24 work items settled, none waiting. The order reads as a Startup fleet plan with 50,000 steps included. The roster shows the retired agent. The notice goes to the added recipient.

## How it was validated

Tenant "Sim ITSM Parity Check" (I1), tenant id 44566a73-0da7-4a30-bc0a-490f7b0a1062, fleet id baf8c547-d183-4ed9-b593-60d2aed44d6f, login sim-i1@demo.provy.ai (so the nightly sweep misses it). No password or key is in the repo; they are in a 0600 file in the session scratchpad.

1. `scripts/itsm_parity.py run` created 24 incidents (INC0010239 to INC0010262, listed in `docs/sim-evidence/itsm-parity/incident-ids.txt`) tagged `tag=itsm-parity-20261007` on the marker `provy-itsm-parity`, let the pack work them, and sent each session through the REST door.
2. `close` moved the 24 from Resolved to Closed.
3. `push` read the 24 closed records, ran `servicenow/outcome_push.js` (the file itself) over them for the outcome bodies, and posted them to this fleet. All 24 answered 200: 21 matched, 3 diverged.
4. `sim_assert.py --spec config/sim_tenants_itsm.json` compared the product's answers with `ground_truth/sim_tenants/I1/`.

## Two choices to know about

**A different marker.** The instance's sweep, SLA targets and outcome push are all keyed to `provy-itsm`, and the push uses one ingest key that belongs to ITSM Demo's fleet. Tickets on that marker would have landed their outcomes in the protected tenant. Pointing the instance's key at the new fleet instead was ruled out because the key cannot be read back to restore it. So this desk has its own marker, and the rule was run outside the instance over the closed records. The consequence: these tickets have no SLA targets (no response or resolution verdicts, and the claims about them are not graded), and none was reopened, since the sweep does not see them.

**The deployed pre-prod server.** Traces went to dev.provy.ai, which stores their bodies in the R2 bucket shared with production (244 objects under `traces/<tenant id>/`). The persistent set avoids that with a local server; this run did not start one. Teardown empties them first.

## The tenant was kept

Rebuilding needs 24 more PDI incidents and the PDI may be reclaimed after 8 October 2026, so keeping it is cheaper than rebuilding. To remove it:

    scripts/with-secrets SUPABASE_URL SUPABASE_KEY WAITLIST_ADMIN_KEY_PREVIEW -- python scripts/sim_teardown.py I1 --creds <file> --spec config/sim_tenants_itsm.json --apply

(run from the argus checkout, as `docs/sim-tenants.md` describes; add `--apply` only after the listing without it looks right. It calls the product's own delete, which empties R2 before the rows; it needs the staff key and the R2 names.)
