# What Provy does with each evidence lever

Measured 2026-09-19T07:06:46Z against PRE-PROD, one lever at a time at rate 1.0,
n=16 per lever, each scored over its own window.

⛔ Read the confidence column, not the method. A low-confidence candidate that the base-rate
guard already qualified is not an accusation. Counting the two together is the defect Provy
sells against.

## `parametric_override` on `teameight` (n=16, seed=4200)

| measure | value |
|---|---|
| contract met-rate (Provy) | 0.8763 |
| divergence rate, by work item | 1.0 |
| incidents opened | 0 |
| **cause named, high/medium confidence** | **0** |
| cause offered, low confidence only | 0 |
| refused to name a cause | 16 |

method / confidence breakdown:

| method / confidence | n | base-rate verdict |
|---|---|---|
| `undetermined/low` | 16 | none recorded |

## `escalation_refused` on `teameight` (n=16, seed=4337)

| measure | value |
|---|---|
| contract met-rate (Provy) | 0.8763 |
| divergence rate, by work item | 1.0 |
| incidents opened | 0 |
| **cause named, high/medium confidence** | **0** |
| cause offered, low confidence only | 0 |
| refused to name a cause | 16 |

method / confidence breakdown:

| method / confidence | n | base-rate verdict |
|---|---|---|
| `undetermined/low` | 16 | none recorded |

## `fabricated_policy` on `teameight` (n=16, seed=4474)

| measure | value |
|---|---|
| contract met-rate (Provy) | 0.8763 |
| divergence rate, by work item | 1.0 |
| incidents opened | 0 |
| **cause named, high/medium confidence** | **0** |
| cause offered, low confidence only | 16 |
| refused to name a cause | 0 |

method / confidence breakdown:

| method / confidence | n | base-rate verdict |
|---|---|---|
| `empty_output/low` | 16 | uninformative |

## `overliteral_constraint` on `claude_code` (n=16, seed=4611)

| measure | value |
|---|---|
| contract met-rate (Provy) | 0.8333 |
| divergence rate, by work item | 1.0 |
| incidents opened | 0 |
| **cause named, high/medium confidence** | **0** |
| cause offered, low confidence only | 0 |
| refused to name a cause | 16 |

method / confidence breakdown:

| method / confidence | n | base-rate verdict |
|---|---|---|
| `undetermined/low` | 16 | none recorded |

## `reversed_on_appeal` on `edwin` (n=16, seed=4748)

| measure | value |
|---|---|
| contract met-rate (Provy) | 0.693 |
| divergence rate, by work item | None |
| incidents opened | 0 |
| **cause named, high/medium confidence** | **0** |
| cause offered, low confidence only | 0 |
| refused to name a cause | 0 |

