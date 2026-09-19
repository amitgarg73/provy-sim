# What Provy does with each evidence lever

Measured 2026-09-19T05:48:17Z against PRE-PROD, one lever at a time at rate 1.0,
n=20 per lever, each scored over its own window.

⛔ Read the confidence column, not the method. A low-confidence candidate that the base-rate
guard already qualified is not an accusation. Counting the two together is the defect Provy
sells against.

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

