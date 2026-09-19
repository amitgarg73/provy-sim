# Research prompt — agentic user journeys and linkable complaints

Hand this to a research assistant (ChatGPT with browsing, or similar). It deliberately targets the
gaps `MORNING-SUMMARY.md` records as unfilled, and the journey layer that does not exist yet.

---

## The prompt

You are researching how AI agents fail in production, for a team building a simulator that has to
reproduce real customer situations rather than invented ones. Accuracy matters far more than volume.
A short answer with five well-sourced findings beats a long one with thirty plausible ones.

### Hard rules on sourcing, read these first

1. **Every factual claim needs a primary source you actually opened**, with a working URL, the
   publication date, and a short verbatim quote of the sentence that supports the claim.
2. **Never state a statistic you have not seen on the page itself.** Do not carry a number from a
   blog post that attributes it to a study. Open the study. If you cannot reach the primary source,
   report the claim as UNVERIFIED and say where you saw it.
3. **"I could not find this" is a valuable answer.** Say it plainly. Do not substitute an adjacent
   finding or a vendor marketing page for missing evidence.
4. Prefer, in this order: practitioner complaints in their own words (forum posts, GitHub issues,
   Reddit, Hacker News, support communities) > incident writeups and postmortems > peer-reviewed or
   arXiv studies > vendor engineering blogs. **Rank vendor marketing last and label it as such.**
5. For each complaint, capture a **direct link and a quotable snippet**, because the end consumer of
   this research needs to show a reader the original.

### What is already covered, so do not spend time here

These are already sourced and in hand. Only add to them if you find something materially new:
agents refusing to escalate to a human; tools answering HTTP 200 with an empty body; insurance
denials reversed on appeal; coding agents obeying a constraint over-literally and shipping a wrong
result; a retrieved document ignored in favour of the model's prior; fabricated policy citations;
root cause named by proximity rather than evidence; an agent producing no output and no error;
retry loops on identical input; context truncation causing self-contradiction.

### Part 1 — End-to-end journeys (the main ask)

For each domain below, describe **how the work actually flows end to end** when a team runs agents
on it. Not the failure, the journey: the steps, who or what hands off to whom, which external
systems are touched, where a human enters, and where the outcome is finally settled and by what
system of record.

Domains: customer support and refunds; IT service management and incident resolution; insurance
claims adjudication and payout; identity and access operations (provisioning, access review);
software engineering agents working in a repository; AIOps and root-cause investigation; revenue
operations and lead qualification.

For each journey, give:
- The steps in order, with the agent or human that owns each
- The tools and systems of record involved
- **Where the outcome is genuinely determined, and how long after the run it settles.** Minutes,
  hours, weeks? This is the single most important field.
- The decision points where the journey can branch
- Which steps a team would realistically automate first, and which they hold back

### Part 2 — Complaints tied to journey steps

For each journey, find **real complaints from people running or receiving this work**, and tie each
to the step it breaks. For every one: the quote, the link, the date, who is speaking (practitioner,
end customer, engineer), and which step of the journey above it maps to.

Bias hard toward things people actually say, over taxonomies of things that could go wrong.

### Part 3 — Three specific gaps, each previously searched for and not found

1. **AIOps naming the wrong root cause.** Documented cases of an AIOps or incident-response tool
   confidently naming a cause that turned out to be wrong, and what the consequence was. A previous
   search found nothing. If it genuinely is not documented publicly, say so and explain what you
   searched.
2. **Malformed tool-call arguments.** The failure rate of LLM tool or function calls producing
   arguments that do not match the tool's schema, in production. Any credible measured rate, with
   its denominator and methodology. Also: what a malformed call looks like when it fails silently
   rather than erroring.
3. **Agent observability and alerting in practice.** What teams running agents actually alert on
   today, what those alerts miss, and complaints about alert fatigue or absent signals. Include
   concrete alert conditions people say they configure.

### Part 4 — What gets noticed late

Across every domain: which failures are discovered **long after the run finished**, and by whom.
A refund reversed by a chargeback weeks later, a ticket reopened, an access grant caught in a
quarterly review, a claim overturned on appeal, a PR reverted after a production incident. For each:
how it surfaced, how long it took, and who bore the cost.

### Output format

A markdown document, structured as:

- **Journeys** — one section per domain, steps as a numbered list, with the settlement timing called
  out explicitly.
- **Complaints** — a table: quote | who | link | date | journey step it breaks | source tier
  (practitioner / postmortem / study / vendor).
- **The three gaps** — findings, or an explicit statement of what was searched and not found.
- **Late discovery** — a table: what failed | how it surfaced | lag | who paid.
- **UNVERIFIED** — a final section listing every claim you could not trace to a primary source, so
  it can be discarded rather than accidentally used.

Do not summarise or editorialise at the end. The tables and the sourcing are the deliverable.

---

## Why these three gaps specifically

`MORNING-SUMMARY.md` records them as known holes:

> "⛔ I could not find documented complaints of AIOps naming a wrong root cause. I searched for it
> directly. The `edwin` blind-spot story is supported by our own fleet only."

> "The malformed-argument lever is not built — tool-calling fails 3–15% of the time in production
> and it is the one big gap left."

And the sourcing rules above exist because two fabricated statistics were caught during the August
pass, both clean, plausible and fully attributed to pages that did not contain them.
