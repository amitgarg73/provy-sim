# What actually goes wrong with agents in production — evidence log

Gathered 20–21 Aug 2026 for the sim-pack rewiring. **Fetched or it is not verified.**

---

## ⛔ DEAD — do not use

### D1. "Arize 2026 field analysis: context blindness 31.6%, rogue actions 30.3%, silent
### degradation 24.9%, memory corruption 8.1%, runaway execution 5.1%"

**THESE NUMBERS DO NOT EXIST.** Returned twice by search summaries, confidently attributed to
Arize. Fetched `arize.com/blog/common-ai-agent-failures/` directly: **no percentages appear
anywhere on the page**, for these or any failure class. The page says only that Arize "spent the
past year analyzing AI agent behavior in production" across "millions of decision paths", with no
incident count, no period, no methodology.

⛔ **DO NOT WEIGHT THE SIM'S FAILURE MIX ON THEM.** This is the second confabulated statistic in
two days (the first was "41% of enterprises report at least one production rollback"). Both were
clean, plausible and fully attributed. **Fetch the primary source before any number enters a
lever config.**

---

## ✅ VERIFIED — Openlayer production taxonomy (fetched, July 2026)

Mechanism-level, which is what a simulator needs. Their words for the mechanism, my note on
whether provy-sim can currently produce it.

| Failure mode | Mechanism | In the sim today? |
|---|---|---|
| **Tool-calling errors** | wrong arg types, bad key names, missing required fields, non-existent tools. API rejects **or silently misinterprets**; agent retries blindly or proceeds on null | ❌ no malformed-argument lever |
| **Silent failures** | **tool returns HTTP 200 with an empty or malformed payload**; agent treats failed retrieval as success and reasons on missing data | ⚠️ `_TOOL_SHAPES["empty"]` exists but only under the OVERT `tool_fault` |
| **Context window truncation** | long history pushes tool definitions and prior outputs out of attention; redundant or contradictory calls | ❌ |
| **Infinite retry loops** | same call fires repeatedly on semantically identical input, no recognition of repetition | ❌ |
| **Agent paralysis** | contradictory tool signals, no action satisfies the criteria. **No tool call, no output, no error — only silence** | ❌ |
| **Error propagation** | one agent's flawed output passed downstream **as fact**; factual drift, scratchpad poisoning, cascading retries | ⚠️ `skip_propagation` models a SKIP, not a corrupted fact carried forward |
| **Context degradation / spec drift** | over long sessions the task representation compresses, early constraints deprioritised | ⚠️ `silent_drift` exists, mechanism differs |
| **Direct / indirect prompt injection** | injected text treated as authoritative; no trust boundary between data and instructions | ❌ |

**Their quantified figure, usable:** tool-calling fails **3–15%** of the time in production
depending on model size and task complexity (July 2026).

**Their line that is the whole thesis:** *"The agent completed the task. The output was
well-formatted. The status was 200. And the result was wrong."*

---

## ✅ VERIFIED — Arize failure classes (fetched; taxonomy real, percentages not)

| Class | Mechanism |
|---|---|
| **Retrieval noise** | agent **ignores correct retrieved documents** ("Lost in the Middle") |
| **Hallucinated arguments** | agent **invents API parameters** from training patterns |
| **Recursive loops** | polls inefficiently instead of using webhooks |
| **Guardrail failures** | prompts lack rigidity against adversarial input |
| **Parametric bias** | **pre-training knowledge overrides retrieved context** |
| **Schema drift** | agent misinterprets an API error and hallucinates a solution |
| **Instruction drift** | system prompt weight diminishes over long conversations |
| **Code generation** | agent generates destructive commands |

⭐ **`parametric bias` and `retrieval noise` are the two the sim has no analogue for**, and both
are the honest version of what `silent_unsupported` gestures at.

---

## ✅ SUPPORT / ITSM — what users actually complain about

Practitioner piece (Eli Weiss, fetched) plus trade coverage. **The complaints are behavioural, not
statistical**, and none of them is "the model was wrong".

1. ⭐ **THE BOT REFUSES TO ESCALATE.** Customers type "agent" / "human" / "representative"
   repeatedly to get out. Reported at **47.6%** of consumers having done this. **This is the single
   most-cited complaint and provy-sim has no lever for it at all.**
2. ⭐ **THE TICKET IS CLOSED AND NOTHING CHANGED.** *"No actual change to the underlying problem
   while tickets sit in the closed column."* Reported as **over 50% of bot-resolved tickets get
   reopened** — ⚠️ **single secondary source, no denominator or population given. Do not print it.**
3. **THE BOT INVENTS POLICY.** Wrong information about billing, refunds and returns, stated
   confidently. Not retrieval failure — fabrication of a rule that does not exist.
4. **DEFLECTION IS THE OPTIMISATION TARGET**, so "resolved" means "did not reach a human", which is
   a metric the supplier defines and the buyer cannot challenge.
5. ⭐ **HOW IT WAS FOUND: customer surveys and sentiment, NOT monitoring.** *"Efficiency metrics look
   incredible on paper"* while customers churn. **That sentence is Provy's whole thesis, arriving
   from outside Provy.**

Other reported figures, same caveat: ~1/3 have abandoned a brand over a bad AI support experience;
15.9% welcome AI for financial transactions against 51.7% demanding a human.

---

## ✅ CODING AGENTS — `claude_code`

Empirical study of failed agent PRs (arXiv 2601.15195) plus trade coverage.

- ⭐ **THE LARGEST FAILURE CLUSTER IS OVER-LITERAL INSTRUCTION FOLLOWING**, dominated by one
  instruction: the harness boilerplate **"DO NOT MODIFY: tests, configuration files."** The agent
  obeys the constraint and produces a wrong result. **Obedience as a failure mode**, which is the
  opposite of the hallucination story everyone tells.
- Not-merged PRs are **larger, touch more files, draw more reviewer revisions and fail CI**.
- **Performance and bug-fix work has the lowest acceptance rate** of any category.
- *"Bugs that won't show up until production, weeks after the AI swore the code was perfect."*

---

## ✅ INSURANCE CLAIMS — `claims`, `claims_payout`

⭐ **THE REAL FAILURE IS NOT A WRONG DECISION. IT IS A DECISION REVERSED LATER.** An **82% overturn
rate on appeal** is reported for Medicare Advantage prior-authorisation denials. The decision looks
final, defensible and complete at the moment it is made; reality arrives weeks later and reverses
it. **That is the settlement-lag shape Provy exists for, in a regulated domain, from outside.**

- Agentic systems gather records, interpret policy language and **issue denials with no human
  reviewer**.
- Clerical or format errors on the form cause automatic denial.
- ⚠️ The widely-quoted "90 percent error rate" is an **allegation in a filed class action**, not a
  measurement. **Never state it as a finding.**
- Four states legislated in 2025 to prohibit AI-only medical-necessity denials; human oversight is
  becoming the floor.

---

## ⚠️ AIOps / RCA — `edwin` — HONEST NEGATIVE

**I could not find documented complaints of AI naming a wrong root cause or a false correlation
causing reopened incidents.** Searched directly for it; the results were vendor guides, not
practitioner reports. **Do not invent this evidence — the edwin blind-spot story is currently
supported by our own fleet only.**

What IS supported: *"AI functions as a pattern-matching engine, not a reasoning engine… excellent at
surfacing context but requires human judgment to understand why a specific change caused a specific
failure."* → **correlation presented as causation** is the defensible mechanism for edwin, and it is
the same thing Provy's own attribution-honesty work refuses to do.
