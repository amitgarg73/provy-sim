# Agentic User Journeys and Linkable Complaints
**Research date:** 2026-09-18  
**Scope:** customer support/refunds; ITSM; insurance claims; identity/access; software engineering agents; AIOps/RCA; revenue operations/lead qualification.

## Method
This report follows the supplied research brief: factual claims are tied to primary or first-party sources where available; practitioner complaints are quoted directly; unsupported statistics are excluded or moved to **UNVERIFIED**. Where a page does not display a publication date, that is stated rather than guessed.

---

# Journeys

## 1. Customer support and refunds

### End-to-end journey
1. **Customer submits a support request** through chat, email, web form, or another channel. In Zendesk, the request becomes a ticket in `New`.
2. **Routing/assignment** assigns the ticket to an agent or queue; the ticket becomes `Open`.
3. **AI or human support agent investigates** the issue, may query order/customer data, and may ask the customer for more information. The ticket can move to `Pending` or `On-hold`.
4. **Refund decision** is made under merchant policy. An AI support agent may recommend or initiate the refund only if the business has exposed the necessary action/tool.
5. **Refund instruction reaches the payment system.** With Stripe, a merchant can refund a non-disputed payment through the Dashboard or API.
6. **Ticket is marked Solved**, but that is not necessarily the true financial outcome.
7. **Payment rails settle the refund to the customer.** Stripe says refunds generally take **5–10 business days** to appear in the customer's account.
8. **Customer can reopen the support outcome.** A reply to a solved Zendesk ticket reopens it; after a ticket is closed, a new follow-up ticket is created instead.

### Systems involved
- Support system of record: Zendesk ticket (or equivalent CRM/support platform)
- Merchant/order system
- Payment processor: Stripe (example)
- Card network / issuing bank
- Customer bank statement: the final externally visible evidence that money landed

### Where the outcome is genuinely determined
There are **two outcome layers**:
- **Support outcome:** the ticket reaches Solved/Closed in the support system.
- **Financial outcome:** the refund is actually credited/reversed at the customer's bank.

For a Stripe card refund, the agent run may finish in seconds or minutes while the customer-visible financial outcome settles **5–10 business days later**.

### Branch points
- Answer vs. ask follow-up
- AI handles vs. human handoff
- Refund approved vs. denied
- Full vs. partial refund
- Refund pending because payment has not settled, bank processing is incomplete, or merchant balance is insufficient
- Customer accepts resolution vs. reopens ticket

### What teams automate first
Realistic early automation: classification, FAQ/KB retrieval, status lookups, drafting, routing, eligibility checks, and low-risk refund initiation under deterministic policy.  
Held back longer: exceptions, high-value refunds, suspected fraud, ambiguous policy, chargeback-sensitive decisions.

### Primary sources
- Zendesk, **Lesson 1: From support requests to tickets** (date not displayed; accessed 2026-09-18)  
  https://support.zendesk.com/hc/en-us/articles/4408881925786-Lesson-1-From-support-requests-to-tickets  
  Quote: “When the agent resolves the issue, they will set the ticket to Solved.”
- Stripe, **Where is my customer's refund?** (date not displayed; accessed 2026-09-18)  
  https://support.stripe.com/questions/where-is-my-customers-refund  
  Quote: “Refunds can take 5-10 business days to show up in a customer’s account.”
- Stripe, **Understanding refund statuses** (date not displayed; accessed 2026-09-18)  
  https://support.stripe.com/questions/understanding-refund-statuses  
  Quote: “This doesn't guarantee that the refund has landed on the customer’s side.”

---

## 2. IT service management and incident resolution

### End-to-end journey
1. **Incident enters the ITSM system** from a user, monitoring tool, service desk, or integration.
2. **Triage and classification** set service, category, CI, assignment group, and priority.
3. **Investigation** uses ticket history, CMDB, telemetry, knowledge articles, previous incidents, change/deployment information, and human SME knowledge.
4. **Agent or human proposes a resolution plan** and may execute safe remediation or ask for approval.
5. **Service restoration** occurs.
6. **Incident is marked Resolved** with resolution code and notes.
7. **Caller reviews the result.** ServiceNow documents that the caller can close the incident if satisfied; otherwise the process can continue.
8. **Incident closes automatically or manually** according to configured policy.
9. **If the cause is not actually fixed**, the organization may create a Problem or Change record, or the user may later return/reopen/escalate.

### Systems involved
- ITSM system of record: ServiceNow Incident
- CMDB
- Monitoring/logging/tracing tools
- Knowledge base
- Change management / deployment systems
- Pager/chat/incident collaboration systems

### Where the outcome is genuinely determined
The run-time agent may finish after generating a plan or applying a remediation, but the **operational outcome is determined when service is restored and remains restored**. In ServiceNow, “Resolved” is explicitly not identical to “Closed”; the caller can review the resolution first.

Settlement can therefore be **minutes to hours**, with later reopening/problem creation revealing that the apparent resolution was incomplete.

### Branch points
- Correct assignment vs. reassignment
- Known error / KB match vs. novel incident
- Automated remediation vs. approval/human execution
- Resolved vs. create Problem
- Resolved vs. generate Change
- Caller accepts vs. rejects/reopens

### What teams automate first
First: categorization, summarization, KB retrieval, similar-incident search, next-step recommendations, drafting resolution notes.  
Later: production changes, broad remediation, closure, high-severity actions.

### Primary sources
- ServiceNow, **Incident resolution and closure**, updated 2026-03-12  
  https://www.servicenow.com/docs/r/it-service-management/incident-management/c_IncidentResolutionAndRecovery.html  
  Quote: “If the caller is satisfied with the resolution, the caller can close the incident.”
- ServiceNow Community practitioner post, **Unexpected response from Agentic AI**, 2025-04-01  
  https://www.servicenow.com/community/servicenow-otto-forum/unexpected-response-from-agentic-ai/td-p/3224010  
  Quote: “today when I tested it is showing unexpected response.”

---

## 3. Insurance claims adjudication and payout

### End-to-end journey
1. **Claim is filed** by the member or provider.
2. **Eligibility/coverage and coding checks** occur.
3. **Plan adjudicates** whether to pay, partially pay, request information, or deny.
4. **Decision is communicated** through an EOB/denial notice or remittance advice.
5. **Payment is issued** if approved.
6. **Internal appeal** may be filed if denied.
7. **External review** may follow an unsuccessful internal appeal.
8. **Independent reviewer can overturn the insurer**, and the plan must accept the external-review decision under the federal process described by HealthCare.gov.

### Systems involved
- Payer claims adjudication platform
- Member/provider records
- Medical policy and benefit rules
- EOB/remittance systems
- Payment system
- Internal appeal system
- Independent external-review organization

### Where the outcome is genuinely determined
The first claim decision is **not always the settled business outcome**. For non-urgent already-received services, HealthCare.gov says an internal appeal can take up to **60 days**. Standard external review can take up to **45 days** after the request is received. A denial can therefore appear “complete” in the adjudication system yet reverse weeks or months later.

### Branch points
- Pay vs. deny vs. partial pay
- Administrative/coding problem vs. medical-necessity decision
- Internal appeal filed vs. abandoned
- Internal appeal upheld vs. reversed
- External review upheld vs. overturned

### What teams automate first
First: intake, document extraction, code validation, coverage checks, routine straight-through adjudication, correspondence drafting.  
Held back: ambiguous medical necessity, high-cost exceptions, adverse decisions with incomplete evidence, final appeal decisions.

### Primary sources
- HealthCare.gov, **Internal appeals** (date not displayed; accessed 2026-09-18)  
  https://www.healthcare.gov/appeal-insurance-company-decision/internal-appeals/  
  Quote: “Your internal appeals must be completed within 60 days” for services already received.
- HealthCare.gov, **External Review** (date not displayed; accessed 2026-09-18)  
  https://www.healthcare.gov/appeal-insurance-company-decision/external-review/  
  Quote: “no later than 45 days after the request was received.”
- CMS, **Original Medicare Appeals** (page current in 2026)  
  https://www.cms.gov/medicare/appeals-grievances/fee-for-service  
  Quote: “There are five levels in the Medicare Part A and Part B appeals process.”

---

## 4. Identity and access operations

### End-to-end journey
1. **Joiner/mover/leaver event** or access request enters the identity system.
2. **Entitlement decision** is made based on group, role, access package, policy, manager/resource-owner approval, or workflow.
3. **Provisioning service** transforms the decision into create/update/delete operations.
4. **SCIM or another connector** writes identity/group changes to the target application.
5. **Target application applies access**; effective access can lag the identity-system record.
6. **Periodic access review** asks an owner, manager, delegate, administrator, or user to attest whether access should continue.
7. **Review result is applied.**
8. **Deprovisioning propagates to the target application.**
9. **Session/token lifetime may delay the user's actual loss of access.**

### Systems involved
- Microsoft Entra ID / identity governance
- HR/source identity
- SCIM/LDAP/SQL/REST/SOAP connector
- Target SaaS/application
- Groups/roles/access packages
- Access-review records and audit logs

### Where the outcome is genuinely determined
The identity-governance decision is not necessarily the same as effective access. Microsoft states that denied users can have application-role assignments removed in **a few minutes**, after which provisioning begins deprovisioning. It also warns that effective access can persist depending on application session lifetime, token lifetime, or Kerberos ticket expiry.

For periodic reviews, discovery of stale access can occur **days to quarters later**, depending on review cadence.

### Branch points
- Automatic vs. approval-required provisioning
- Provision success vs. connector error/quarantine
- Continue vs. deny access
- Auto-apply vs. manual apply
- Direct role removal vs. downstream deprovisioning
- Access removed in directory vs. session still active in target

### What teams automate first
First: standard joiner/leaver actions, low-risk group assignment, reminders, review scheduling, recommendation generation.  
Held back: privileged access, ambiguous entitlement, cross-system exceptions, automatic removal where business ownership is unclear.

### Primary sources
- Microsoft Learn, **How Application Provisioning works in Microsoft Entra ID** (date not displayed; accessed 2026-09-18)  
  https://learn.microsoft.com/en-us/entra/identity/app-provisioning/how-provisioning-works  
  Quote: “automatic provisioning includes the maintenance and removal of user identities as status or roles change.”
- Microsoft Learn, **Plan a Microsoft Entra access reviews deployment** (date not displayed; accessed 2026-09-18)  
  https://learn.microsoft.com/en-us/entra/id-governance/deploy-access-reviews  
  Quote: “Automate review outcomes, such as removing users' access to resources.”
- Microsoft Entra docs source, **Prepare access review of application**, 2026-03-12 family of docs  
  https://learn.microsoft.com/en-us/entra/id-governance/access-reviews-application-preparation  
  Quote: “how long a user...can continue...depends upon the application's own session lifetime.”

---

## 5. Software engineering agents working in a repository

### End-to-end journey
1. **Human creates or selects an issue/task** and assigns it to a coding agent.
2. **Agent reads repository context** and works on a branch or cloud environment.
3. **Agent edits files, runs commands/tests, and creates commits.**
4. **Agent opens a pull request** and requests human review.
5. **CI, security scanning, automated review, and human review** evaluate the change.
6. **Agent iterates** based on PR comments.
7. **Human approves and merges.**
8. **Deployment pipeline moves the code toward production.**
9. **Real production behavior** becomes the ultimate outcome; regressions may be discovered only after merge/deploy.

### Systems involved
- GitHub issue
- Repository/branch
- Agent session
- Pull request
- GitHub Actions/CI
- Code review
- Deployment system
- Production telemetry / incident system

### Where the outcome is genuinely determined
The agent's run ends when it has produced changes/PR, but GitHub explicitly requires review before merge and warns Copilot is not guaranteed to find every issue. The business/engineering outcome may therefore settle:
- **minutes-hours** at CI/review,
- **hours-days** at deployment,
- or later if a production regression is discovered.

No universal post-merge settlement time was found in a primary source.

### Branch points
- Task needs clarification vs. autonomous execution
- Tests pass vs. fail
- Human accepts vs. requests changes
- Merge vs. abandon
- Deploy succeeds vs. rollback/revert

### What teams automate first
First: scoped code changes, tests, refactors, docs, dependency updates, PR creation, code review suggestions.  
Held back: direct production mutation, security-sensitive changes, broad migrations, unreviewed merge.

### Primary sources
- GitHub Docs, **Kick off a task with Copilot agents** (date not displayed; accessed 2026-09-18)  
  https://docs.github.com/en/copilot/how-tos/copilot-on-github/use-copilot-agents/kick-off-a-task  
  Quote: “Copilot works on the task and requests your review when it finishes.”
- GitHub Docs, **Review output from Copilot** (date not displayed; accessed 2026-09-18)  
  https://docs.github.com/en/copilot/how-tos/copilot-on-github/use-copilot-agents/review-copilot-output  
  Quote: “check the pull request thoroughly before merging.”
- GitHub Docs, **About GitHub Copilot code review** (date not displayed; accessed 2026-09-18)  
  https://docs.github.com/en/copilot/concepts/agents/code-review  
  Quote: “Copilot is not guaranteed to spot all problems or issues in a pull request.”

---

## 6. AIOps and root-cause investigation

### End-to-end journey
1. **Monitoring alert fires.**
2. **Agent gathers context** from metrics, logs, traces, deployment/change history, dependencies, runbooks, and incident history.
3. **Agent forms hypotheses** and runs parallel or sequential investigations.
4. **Agent identifies a proposed root cause.**
5. **Agent selects a mitigation** from permitted actions.
6. **Human reviews critical mitigations or agent executes minor safe mitigations**, depending on autonomy level.
7. **System waits and checks whether the alert clears.**
8. **If not resolved, investigation restarts or escalates to human.**
9. **Evaluation/postmortem** compares the path and final action against expert/golden incident data.

### Systems involved
- Monitoring/alerting
- Logs, metrics, traces
- Change/deployment history
- Dependency/topology data
- Incident management UI
- Runbooks
- Production actuation/guardrail system
- Human SRE/on-call

### Where the outcome is genuinely determined
The root-cause text itself is not the outcome. Google's documented AI Operator waits after mitigation and checks whether the incident resolves. The outcome therefore settles when the production condition clears and remains healthy, generally **minutes to hours**, with postmortem/evaluation later.

### Branch points
- Enough evidence vs. escalate
- Hypothesis confirmed vs. rejected
- Human approval vs. autonomous action
- Alert clears vs. new investigation
- Correct root cause vs. incorrect root cause

### What teams automate first
First: alert enrichment, context gathering, correlation, similar-incident retrieval, hypothesis generation, read-only investigation.  
Held back: high-blast-radius remediation, ambiguous RCA, irreversible changes.

### Primary sources
- Google SRE, **AI in SRE: How Google is Engineering the Future of Reliable Operations** (page does not display a publication date; external references identify it as a Google SRE paper)  
  https://sre.google/resources/practices-and-processes/ai-engineering-reliable-operations/  
  Quote: “the agent incorrectly diagnosed the root cause and failed to mitigate it.”
- Datadog Engineering, **How we built a real-world evaluation platform for autonomous SRE agents at scale**, 2026-04-07  
  https://www.datadoghq.com/blog/engineering/bits-ai-eval-platform/  
  Quote: “Then other investigations started getting worse.”

---

## 7. Revenue operations and lead qualification

### End-to-end journey
1. **Lead is captured** from web form, event, list, campaign, partner, product signal, or agent.
2. **Enrichment/qualification** fills company/person attributes, intent, fit, and priority.
3. **Scoring/qualification decision** decides whether the lead should be worked.
4. **Routing** assigns the lead to a user or queue based on criteria such as geography, tier, product, or specialization.
5. **Sales rep reviews and contacts the lead.**
6. **Lead is converted** into account/contact/opportunity when qualified under the organization's process.
7. **Opportunity progresses to closed-won/lost.**
8. **Revenue/booking is the true delayed business outcome** of the upstream qualification/routing decision.

### Systems involved
- Marketing automation / lead-capture source
- Enrichment provider
- Salesforce Lead record
- Assignment rules / queues
- Account/contact/opportunity records
- Sales engagement system

### Where the outcome is genuinely determined
The routing outcome is immediate in CRM, but the business outcome is not. Salesforce assignment rules only determine ownership/queue. Whether qualification was correct may not become clear until a rep works the lead or an opportunity closes. Settlement can therefore be **hours to months**; no universal primary-source timing was found.

### Branch points
- Qualified vs. disqualified
- Human rep vs. nurture
- Owner/territory/queue
- Convert vs. recycle
- Opportunity won vs. lost

### What teams automate first
First: enrichment, dedupe, scoring, prioritization, routing, drafting outreach, queue management.  
Held back: high-value account qualification, exception routing, autonomous disqualification where false negatives are costly.

### Primary sources
- Salesforce Help, **Guidelines for Assignment Rules** (date not displayed; accessed 2026-09-18)  
  https://help.salesforce.com/s/articleView?id=sf.customize_leadrules.htm&language=en_US&type=5  
  Quote: “Use lead assignment rules to specify how leads are assigned to users or queues.”
- Salesforce Help, **Lead or Case Ownership Changed Automatically in Salesforce**, 2026-07-04  
  https://help.salesforce.com/s/articleView?id=000385929&language=en_US&type=1  
  Quote: “Lead and Case record ownership can change automatically.”

---

# Complaints

| Quote | Who | Link | Date | Journey step it breaks | Source tier |
|---|---|---|---|---|---|
| “it 100% told her we could issue a refund without the physical item.” | Retail employee describing a customer-facing chatbot | https://www.reddit.com/r/CustomerService/comments/1vf0k4w/my_companys_ai_chatbot_spread_false_information/ | 2026-08-04 | Support: policy interpretation / refund eligibility | practitioner |
| “today when I tested it is showing unexpected response.” | ServiceNow practitioner building an incident-resolution AI agent | https://www.servicenow.com/community/servicenow-otto-forum/unexpected-response-from-agentic-ai/td-p/3224010 | 2025-04-01 | ITSM: KB retrieval / resolution recommendation | practitioner |
| “'Next action recommendation AI agent' not running even though it is explicitly called out” | ServiceNow practitioner | https://www.servicenow.com/community/now-assist-forum/generate-resolution-plan-agentic-workflow-is-not-working-oob/td-p/3519458 | 2026-04-03 | ITSM: orchestration/handoff | practitioner |
| “creates a user every time instead of updating when the user already exists.” | SCIM implementer | https://github.com/AzureAD/SCIMReferenceCode/issues/63 | 2021-08-16 | IAM: provisioning / identity matching | practitioner |
| “older, superseded revisions of the code [are] reapplied” | GitHub Copilot user | https://github.com/microsoft/vscode/issues/265794 | 2025-09-09 | Coding: edit state / PR implementation | practitioner |
| “Agent reverted only one file and didn't know that he had changed others” | GitHub Copilot user | https://github.com/orgs/community/discussions/164133 | 2025-06-25 | Coding: rollback / recovery | practitioner |
| “Selection is silently lost.” | VS Code Copilot user | https://github.com/microsoft/vscode/issues/317276 | 2026 | Coding: agent identity/context continuity | practitioner |
| “the request...included editorContext for page-02.html, and the agent edited page-02.html instead.” | VS Code Copilot user | https://github.com/microsoft/vscode/issues/316051 | 2026-05-12 | Coding: context/target selection | practitioner |
| “This is critical for production monitoring, and is a blocker for adoption” | Langfuse user discussing monitors | https://github.com/orgs/langfuse/discussions/3997 | 2026-02-18 comment | Agent observability: alerting/monitoring capability | practitioner |
| “the agent completed successfully by the metrics we’re tracking but the output was wrong” | SRE describing production LLM-agent operations | https://www.reddit.com/r/Observability/comments/1tkfkul/automating_root_cause_analysis_for_ai_agent/ | 2026-05-22 | Agent observability: false-green run | practitioner |
| “Then other investigations started getting worse.” | Datadog engineering team, describing Bits evaluation | https://www.datadoghq.com/blog/engineering/bits-ai-eval-platform/ | 2026-04-07 | AIOps: quality regression across investigations | postmortem/engineering |
| “the agent incorrectly diagnosed the root cause and failed to mitigate it.” | Google SRE, AI Operator evaluation | https://sre.google/resources/practices-and-processes/ai-engineering-reliable-operations/ | publication date not displayed | AIOps: RCA + mitigation | first-party engineering |
| “Lead and Case record ownership can change automatically” | Salesforce troubleshooting article | https://help.salesforce.com/s/articleView?id=000385929&language=en_US&type=1 | 2026-07-04 | RevOps: routing/owner correctness | vendor support |
| “If your insurance company still denies your claim, you can file for an external review.” | Federal consumer guidance | https://www.healthcare.gov/appeal-insurance-company-decision/internal-appeals/ | date not displayed | Insurance: adjudication outcome challenged later | government |

---

# The three gaps

## Gap 1 — AIOps naming the wrong root cause

### Finding: documented first-party case exists
Google SRE's AI Operator paper explicitly documents an evaluation example where an AI Operator run **incorrectly diagnosed the root cause and failed to mitigate the incident**. The same paper says AI Operator has run across **thousands of incidents**, with execution traces stored for evaluation.

Source:  
Google SRE, **AI in SRE: How Google is Engineering the Future of Reliable Operations**  
https://sre.google/resources/practices-and-processes/ai-engineering-reliable-operations/  
Publication date: not displayed on the primary page.  
Quote: “the agent incorrectly diagnosed the root cause and failed to mitigate it.”

### Important limitation
The public paper does **not** disclose:
- the incident's identity,
- the exact wrong root cause,
- the production consequence,
- or a measured wrong-RCA rate across all incidents.

So the previously empty gap can now be filled as **“documented occurrence exists”**, but not as a quantitative production rate.

### Corroborating engineering evidence
Datadog describes a different failure shape in its evaluation platform: a feature improved one investigation class while degrading others, with “Nothing crashed. No tests failed.” That is evidence that run success does not imply investigation-quality success.

---

## Gap 2 — Malformed tool-call arguments

### Finding: measured practitioner sample exists
An n8n GitHub issue published in September 2026 reports a direct provider-boundary measurement:

- **267** Anthropic `tool_use` blocks observed through a transparent proxy
- **33** had completely empty `input: {}` even though required parameters were declared
- observed rate: **12.4%**
- the reporter states this was measured at the provider boundary
- in the reported n8n behavior, one schema-invalid call terminated the workflow

Source:  
n8n issue **AI Agent (V1): a schema-invalid tool call from the model terminates the whole execution instead of being returned as a recoverable error**  
https://github.com/n8n-io/n8n/issues/37916  
Date: 2026-09 (search index: last week relative to 2026-09-18)  
Quote: “33 of 267 `tool_use` blocks (12.4%) had a completely empty `input: {}`”

### What malformed calls look like
Primary issue examples found:
- Required argument omitted: `input: {}`
- Invalid JSON / missing closing brace
- `arguments: null`
- `arguments: ""` for a no-required-parameter tool
- Truncated JSON after session compression

### Silent/fail-open failure
OpenAI Agents Python issue #3863 documents a particularly dangerous silent path:
1. malformed JSON fails parsing,
2. SDK substitutes `{}`,
3. approval predicate evaluates the empty object,
4. a typical content-based approval check returns `False`,
5. the run may proceed **without the intended human approval**.

Source:  
https://github.com/openai/openai-agents-python/issues/3863  
Date: 2026-07-17  
Quote: “invalid tool-call JSON is swallowed and replaced with `{}` before the predicate runs”

### Critical limitation
**12.4% is not a general production tool-calling failure rate.** It is one practitioner's measured sample of 267 Anthropic tool-use blocks in a specific n8n setup. No primary source was found supporting the broader “3–15% of tool calls fail in production” claim across vendors/workloads.

---

## Gap 3 — Agent observability and alerting in practice

### What is commonly observable today
First-party/primary tool documentation shows teams can trace or monitor:
- LLM generations
- tool calls
- handoffs
- guardrails
- custom events
- errors
- latency
- cost
- evaluator scores / quality scores

OpenAI Agents SDK tracing records LLM generations, tool calls, handoffs, guardrails and custom events.  
Source: https://github.com/openai/openai-agents-python/blob/main/docs/tracing.md

Langfuse's June 2026 monitors announcement says threshold alerts can be configured on:
- cost
- scores
- latency
and routed to:
- Slack
- webhooks
- GitHub Actions

Source: https://github.com/orgs/langfuse/discussions/3997

### What those alerts miss
The strongest practitioner complaint found is outcome blindness:
> “the agent completed successfully by the metrics we’re tracking but the output was wrong”

That complaint specifically contrasts downstream wrong output with dashboards containing spans, latency, token counts, and normal-looking run completion.

Source:  
https://www.reddit.com/r/Observability/comments/1tkfkul/automating_root_cause_analysis_for_ai_agent/  
Date: 2026-05-22

### Concrete alert conditions found
- latency threshold
- cost threshold
- evaluator/score threshold
- runtime errors
- failed tool/API spans
- guardrail failures
- workflow/run failure

### Alert-fatigue evidence
No strong primary-source study or practitioner post with a measured **agent-specific alert-fatigue rate** was found in this pass. General SRE alert-fatigue literature exists, but substituting it would violate the scope of this gap.

---

# Late discovery

| What failed | How it surfaced | Lag | Who paid |
|---|---|---:|---|
| Support bot gave incorrect refund/return policy | Customer arrived at store expecting an invalid refund | Same day to days; exact lag not stated | Customer + store staff |
| Refund marked initiated but money not yet received | Customer bank statement still lacks credit/reversal | 5–10 business days is the normal Stripe settlement window; >10 days triggers bank tracing | Customer support + finance ops + customer |
| ITSM incident marked Resolved but user still has issue | Caller rejects resolution / incident reopens or follow-up occurs | Minutes to days; policy-dependent | User + service desk/on-call |
| Health claim denied incorrectly | Internal appeal or external independent review overturns decision | Up to 60 days internal appeal; up to 45 additional days standard external review | Patient/provider/payer |
| Stale/incorrect access remains | Periodic access review or deprovisioning verification identifies it | Minutes after review for role assignment; potentially weeks/quarters until review; session/token expiry can extend effective access | Security/IAM + resource owner |
| Coding agent changed wrong/stale files | Human review, failing tests, rollback attempt, or later regression | Minutes to post-deploy; no universal primary timing found | Developer/team |
| AIOps agent chooses wrong RCA | Mitigation fails; evaluation/postmortem compares to golden/human path | Minutes during incident; later in evaluation/postmortem | On-call/SRE + affected service |
| Lead routed/qualified incorrectly | Rep discovers bad fit/owner or later opportunity outcome contradicts qualification | Hours to months; organization-specific | SDR/AE + RevOps + lost response time |

---

# UNVERIFIED

1. **“Tool-calling fails 3–15% of the time in production.”**  
   Not verified as a general claim. A real measured **12.4% (33/267)** sample was found in one n8n/Anthropic setup, but this cannot support a cross-vendor range.

2. **A general production rate for AIOps wrong-root-cause diagnoses.**  
   Google publicly documents at least one incorrect RCA evaluation case, but no fleet-wide wrong-RCA percentage was found.

3. **A quantified agent-specific alert-fatigue rate.**  
   No primary source with a denominator/methodology was found.

4. **A universal settlement time for software-engineering-agent outcomes.**  
   The agent may finish at PR creation, while correctness may settle at review, CI, deployment, or production. No single defensible timing applies.

5. **A universal settlement time for lead qualification.**  
   CRM routing happens immediately, but whether the qualification was commercially correct may take until rep disposition or closed-won/lost.

6. **A claim that support-ticket “Solved” equals customer success.**  
   Zendesk explicitly permits reopening after Solved, so this should not be used as the final outcome without downstream confirmation.

7. **A claim that access-review “deny” equals immediate loss of effective access.**  
   Microsoft explicitly notes provisioning, target-system propagation, and session/token lifetime can delay effective removal.