# What Provy does with each evidence lever

Measured 2026-09-19T05:48:17Z against PRE-PROD, one lever at a time at rate 1.0,
n=20 per lever, each scored over its own window.

⛔ Read the confidence column, not the method. A low-confidence candidate that the base-rate
guard already qualified is not an accusation. Counting the two together is the defect Provy
sells against.

⛔ **READ THIS BEFORE THE DIVERGENCE COLUMN IN THIS FILE.** Every lever in this run used
`--seed 91`, so every one generated the same entity ids. The ledger is keyed on
`(entity_id, business_date)` and `writeLedgerPredictions` refuses an already-settled row, so only
the FIRST lever created ledger rows. Measured after the fact: teameight took 30 ledger rows at
05:00 and none afterwards.

**So in this file the divergence and contract met-rate columns are VOID for every lever after
`ok_but_empty`.** Incidents and attributions are keyed per session and are unaffected, which is
exactly what made the artefact hard to see: most of each row stayed correct.

Fixed in `scripts/measure_levers.sh` with a seed per lever, and the seed is now printed beside each
result. The corrected re-run of the remaining levers is in `lever-measurements-part2.md`.

## `ok_but_empty` on `teameight` (n=20)

| measure | value |
|---|---|
| contract met-rate (Provy) | 0.875 |
| divergence rate, by work item | 1.0 |
| incidents opened | 0 |
| **cause named, high/medium confidence** | **0** |
| cause offered, low confidence only | 20 |
| refused to name a cause | 0 |

method / confidence breakdown:

| method / confidence | n | base-rate verdict |
|---|---|---|
| `empty_output/low` | 17 | uninformative |

## `retry_loop` on `teameight` (n=20)

| measure | value |
|---|---|
| contract met-rate (Provy) | 0.8816 |
| divergence rate, by work item | None |
| incidents opened | 20 |
| **cause named, high/medium confidence** | **0** |
| cause offered, low confidence only | 0 |
| refused to name a cause | 0 |

## `agent_paralysis` on `teameight` (n=20)

| measure | value |
|---|---|
| contract met-rate (Provy) | 0.878 |
| divergence rate, by work item | None |
| incidents opened | 0 |
| **cause named, high/medium confidence** | **0** |
| cause offered, low confidence only | 0 |
| refused to name a cause | 0 |

## `context_truncated` on `teameight` (n=20)

| measure | value |
|---|---|
| contract met-rate (Provy) | 0.8771 |
| divergence rate, by work item | None |
| incidents opened | 0 |
| **cause named, high/medium confidence** | **0** |
| cause offered, low confidence only | 0 |
| refused to name a cause | 0 |

