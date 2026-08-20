# Wiring real Claude Code telemetry to Provy

What it would take, what is already there, and the one part that is genuine work. Written 20 Aug 2026
alongside the `claude_code` sim pack, which shows what the RESULT looks like without needing any of
this.

⛔ **No change to the Provy codebase is required or proposed.** Everything below sits outside it.

## What Claude Code already emits

Claude Code has built-in OpenTelemetry support, configured per machine and centrally manageable
through `managed-settings.json`. Turning it on is environment variables, not code:

```bash
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
```

That gets you spans and metrics into any OTLP collector. Pointing them at OpenObserve or similar is a
documented five-minute setup, and it is free. **Capture is not the hard part and is not worth
building.**

## Why it cannot point straight at Provy

Provy ingests REST on five endpoints (`/api/ingest/session/open`, `/trace`, `/eval`,
`/session/close`, `/outcome`), authenticated with `x-provy-key`. Claude Code speaks OTLP. Nothing
today translates one into the other, so a collector exporter has to sit in between.

That translator is small. **The judgement inside it is not**, and it is the whole exercise:

### 1. Which spans are one work item

⛔ **The work item is what the outcome settles on, never the conversation boundary.** A session in
Claude Code is a conversation, and a conversation is not a verdict: one conversation can land three
changes and abandon two more. Grouping by conversation gives you a journey, not something that can
be graded.

The grouping that works is **one engineering task that ends in a change**. In practice that means
grouping spans by the branch or commit they produced.

### 2. Capture the change artifact or the join is unrecoverable

⛔ Every emitted work item must carry **branch, commit sha, changed paths and cwd**. Conversation
boundaries can always be re-derived later from stored spans. GitHub can never tell you afterwards
which commit came from which session. Miss this at capture time and the join to the repository is
gone permanently.

### 3. What settles it

Provy grades a claim against a system of record. Here that is the repository and CI, read back
after the fact:

| condition | settled by |
|---|---|
| the change landed on the branch | `git log` on the branch |
| a check ran before the claim | the presence of a test/build/lint span before the completion span |
| the tests passed on the commit | CI status for that sha |
| it survived without a revert | `git log --grep=revert`, or the PR being reverted |
| it touched only the files in scope | the commit diff against the task |

### 4. ⛔ The mapping mistake that would poison the numbers

**A validation failure must never be mapped to `outcome: 'failed'` or reach `stepErrorRate`.**

An agent that runs its tests and finds them red did its job. An agent that never ran them and
claimed done did not. If the adapter maps a non-zero exit from `pytest` to an agent error, then the
agent that checks its work scores WORSE than the one that skips checking, and every number on the
screen still looks plausible. Reliability is 0.3 of the trust blend, so this is not a rounding
difference.

Keep them as two separate conditions, which is what the sim pack does (c2 and c3).

### 5. "Not observable" is not zero

If the adapter cannot see whether a check ran, it must emit **nothing** for that condition rather
than `false`. Three states, not two. A confident zero on a signal you cannot see is the exact defect
Provy exists to catch, and it would be embarrassing to ship it in our own adapter.

## ⛔ PII

`user.email` rides on every Claude Code span. Provy's egress redaction is narrow and does not remove
personal data, so anything the adapter forwards lands in the database as-is. Strip or hash it in the
collector, before it leaves the machine.

## What this is and is not

⛔ **Internal instrumentation for our own fleet. Not a product line.** The Agent Journey work was
parked on 17 Aug 2026 on competitive evidence: Anthropic ships a Claude Code analytics dashboard AND
an API, and PR revert rate / code survival rate are the category's standard metrics with published
2026 benchmarks (AI-written code survives ~65% vs ~92% human) that we do not have. The value that
survives is running this on our own fleet to get real numbers for the benchmark, which does not
depend on anyone buying it.

See `project_provy_agent_journey_parked` before reopening any of it.
