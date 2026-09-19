"""The scenario and journey catalogue.

⛔ WHY THIS FILE EXISTS, AND WHAT IT REPLACES.

The simulator's unit was a lever: a fault injected inside one work item. Every lever in
`docs/provy-simulation-proof-harness.md` §6 carries a column headed "Proves", and every entry in it
names a Provy feature. The levers were derived from what needed demonstrating rather than from what
happens to people, which is why a harness built to prove the product could never find what the
product misses.

A JOURNEY is the unit a customer recognises: an ordered walk through real work, ending at a
settlement that happens somewhere else, later. Every one of the seven below shares that shape, and it
is Provy's whole argument:

    the run finishes in seconds; the outcome is decided elsewhere, days or weeks afterwards.

A SCENARIO is a mechanism attached to a journey step, and it may only exist here if it carries
evidence that it happens to somebody: a verbatim complaint, a link, a date, and who was speaking.
⛔ A SCENARIO WITHOUT A SOURCE IS AN INVENTION AND DOES NOT BELONG IN THE CATALOGUE.

⛔ AND EVERY SCENARIO CARRIES TWO SEPARATE FIELDS: what Provy is EXPECTED to do, and what Provy was
last MEASURED doing, with a date. They are different claims. The mapping document had only the first
and called it the second, and on 19 Sep 2026 the first live measurement disagreed with it. A scenario
whose expectation and measurement disagree is the most valuable object in this system; the shape of
this file exists so one can be held.

Sources: docs/failure-research/. Journeys and complaints from the 18 Sep 2026 research pass;
mechanisms from the 20-21 Aug pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Source:
    """Where a claim comes from. `tier` ranks how much weight it carries."""
    quote: str
    who: str
    url: str
    date: str
    tier: str = "practitioner"   # practitioner | postmortem | study | first-party | vendor | government


@dataclass(frozen=True)
class Step:
    id: str
    name: str
    owner: str                    # the agent or human that owns it


@dataclass(frozen=True)
class Journey:
    key: str
    name: str
    pack: Optional[str]           # the sim pack that runs it, None when nothing does yet
    steps: tuple
    settles_where: str
    settlement_lag: str           # ⛔ the field that matters most. Provy exists in this gap.
    sources: tuple = ()


@dataclass(frozen=True)
class Scenario:
    key: str
    journey: str
    step: str                     # the Step.id it breaks
    lever: Optional[str]          # None means the mechanism has no lever yet
    mechanism: str                # one sentence a practitioner would recognise
    evidence: Source
    expected: str                 # what Provy SHOULD do
    measured: Optional[str] = None   # what Provy was last measured doing
    measured_on: Optional[str] = None


# ── Journeys ────────────────────────────────────────────────────────────────

JOURNEYS: dict[str, Journey] = {
    "support_refund": Journey(
        key="support_refund", name="Customer support and refunds", pack="stripe_support",
        steps=(
            Step("s1", "Customer submits a request", "customer"),
            Step("s2", "Routing and assignment", "router"),
            Step("s3", "Agent investigates, may ask for more information", "resolver"),
            Step("s4", "Refund decision under merchant policy", "resolver"),
            Step("s5", "Refund instruction reaches the payment system", "action tool"),
            Step("s6", "Ticket marked Solved", "resolver"),
            Step("s7", "Payment rails settle the refund", "Stripe / issuing bank"),
            Step("s8", "Customer reopens, or does not", "customer"),
        ),
        settles_where="Two layers. The support outcome is the ticket reaching Solved. The financial "
                      "outcome is money credited at the customer's bank, and only the second one is real.",
        settlement_lag="5 to 10 business days",
        sources=(
            Source("Refunds can take 5-10 business days to show up in a customer's account.",
                   "Stripe support documentation",
                   "https://support.stripe.com/questions/where-is-my-customers-refund",
                   "accessed 2026-09-18", "first-party"),
            Source("This doesn't guarantee that the refund has landed on the customer's side.",
                   "Stripe, Understanding refund statuses",
                   "https://support.stripe.com/questions/understanding-refund-statuses",
                   "accessed 2026-09-18", "first-party"),
        ),
    ),
    "itsm_incident": Journey(
        key="itsm_incident", name="ITSM and incident resolution", pack="itsm",
        steps=(
            Step("s1", "Incident raised", "reporter"),
            Step("s2", "Categorise and prioritise", "classifier"),
            Step("s3", "Retrieve knowledge, diagnose", "resolver"),
            Step("s4", "Propose or apply remediation", "resolver"),
            Step("s5", "Mark Resolved", "resolver"),
            Step("s6", "Service observed healthy", "monitoring"),
            Step("s7", "Closed, or reopened", "system of record"),
        ),
        settles_where="When service is restored AND stays restored. Resolved is explicitly not the "
                      "same state as Closed.",
        settlement_lag="hours to days, and a reopen can arrive later still",
    ),
    "claims_adjudication": Journey(
        key="claims_adjudication", name="Insurance claims adjudication and payout", pack="claims",
        steps=(
            Step("s1", "Claim submitted", "claimant"),
            Step("s2", "Eligibility and coverage checked", "eligibility agent"),
            Step("s3", "Adjudication decision", "adjudicator"),
            Step("s4", "Decision communicated", "notifier"),
            Step("s5", "Payout, or denial", "payment system"),
            Step("s6", "Internal appeal", "claimant"),
            Step("s7", "External review", "independent reviewer"),
        ),
        settles_where="The first decision is frequently not the settled outcome. An appeal can "
                      "overturn it entirely, and the run that made it was defensible at the time.",
        settlement_lag="internal appeal up to 60 days, external review up to 45 days",
        sources=(
            Source("If your insurance company still denies your claim, you can file for an external review.",
                   "HealthCare.gov, federal consumer guidance",
                   "https://www.healthcare.gov/appeal-insurance-company-decision/internal-appeals/",
                   "date not displayed", "government"),
        ),
    ),
    "identity_access": Journey(
        key="identity_access", name="Identity and access operations", pack="iam",
        steps=(
            Step("s1", "Access requested", "requester"),
            Step("s2", "Entitlement resolved", "identity agent"),
            Step("s3", "Approval decision", "approver"),
            Step("s4", "Provisioning to the directory", "provisioning connector"),
            Step("s5", "Downstream applications provisioned", "SCIM"),
            Step("s6", "Access review", "reviewer"),
            Step("s7", "Revocation propagates, or does not", "SCIM"),
        ),
        settles_where="Effective access, not the governance decision. A removed assignment starts "
                      "deprovisioning; whether access actually stopped is a separate fact.",
        settlement_lag="minutes to a quarterly access review",
    ),
    "coding_agent": Journey(
        key="coding_agent", name="Software engineering agents in a repository", pack="claude_code",
        steps=(
            Step("s1", "Task or issue assigned", "requester"),
            Step("s2", "Repository context gathered", "coding agent"),
            Step("s3", "Changes written", "coding agent"),
            Step("s4", "Tests run", "CI"),
            Step("s5", "Pull request opened", "coding agent"),
            Step("s6", "Human review", "reviewer"),
            Step("s7", "Merged", "maintainer"),
            Step("s8", "Behaves correctly in production, or is reverted", "production"),
        ),
        settles_where="Review is required before merge and is not guaranteed to catch everything. "
                      "The real outcome is whether the change survives production.",
        settlement_lag="minutes to hours for review, weeks for a production revert",
    ),
    "aiops_rca": Journey(
        key="aiops_rca", name="AIOps and root-cause investigation", pack="edwin",
        steps=(
            Step("s1", "Alert fires", "monitoring"),
            Step("s2", "Signals and recent changes gathered", "rca_agent"),
            Step("s3", "Root cause proposed", "rca_agent"),
            Step("s4", "Mitigation applied", "operator"),
            Step("s5", "Condition observed to clear", "monitoring"),
            Step("s6", "Stays healthy, or recurs", "production"),
        ),
        settles_where="The root-cause text is not the outcome. It settles when the production "
                      "condition clears and stays clear.",
        settlement_lag="minutes to the next recurrence",
        sources=(
            Source("the agent incorrectly diagnosed the root cause and failed to mitigate it.",
                   "Google SRE, AI Operator evaluation",
                   "https://sre.google/resources/practices-and-processes/ai-engineering-reliable-operations/",
                   "publication date not displayed", "first-party"),
        ),
    ),
    "revops_lead": Journey(
        key="revops_lead", name="Revenue operations and lead qualification", pack="revops",
        steps=(
            Step("s1", "Lead captured", "form / import"),
            Step("s2", "Enriched and scored", "qualification agent"),
            Step("s3", "Qualified or disqualified", "qualification agent"),
            Step("s4", "Routed and assigned", "assignment rules"),
            Step("s5", "Rep works the lead", "sales rep"),
            Step("s6", "Opportunity created, or not", "CRM"),
            Step("s7", "Closed won or lost", "CRM"),
        ),
        settles_where="Routing is immediate in the CRM. Whether the qualification was correct is not "
                      "visible until a rep works it or an opportunity closes.",
        settlement_lag="days to a full sales cycle",
    ),
}


# ── Scenarios ───────────────────────────────────────────────────────────────
#
# ⛔ `measured` IS DELIBERATELY None ON EVERY ROW THAT HAS NOT BEEN MEASURED. Do not fill it from the
# mapping table: those are expectations. It is filled by scripts/measure_levers.sh, which runs one
# lever at a time against a live tenant and reads what Provy wrote.

SCENARIOS: dict[str, Scenario] = {

    "fabricated_refund_policy": Scenario(
        key="fabricated_refund_policy", journey="support_refund", step="s4",
        lever="fabricated_policy",
        mechanism="The agent states a refund rule confidently and cites a document no retrieval in "
                  "the run ever returned.",
        evidence=Source("it 100% told her we could issue a refund without the physical item.",
                        "Retail employee describing a customer-facing chatbot",
                        "https://www.reddit.com/r/CustomerService/comments/1vf0k4w/my_companys_ai_chatbot_spread_false_information/",
                        "2026-08-04"),
        expected="Catchable: the cited document id can be checked against what was retrieved.",
    ),

    "escalation_refused": Scenario(
        key="escalation_refused", journey="support_refund", step="s3",
        lever="escalation_refused",
        mechanism="The work item needed a human. The agent kept it and closed it, and the deflection "
                  "metric improved while the person stayed stuck.",
        evidence=Source("I typed agent four times and it would not let me through.",
                        "Consumer complaint, the single most-cited failure in AI support",
                        "docs/failure-research/00-evidence.md", "2026-08 (Aug research pass)"),
        expected="Nothing today. The handoff requests sit in the trace and nothing reads them.",
    ),

    "refund_never_lands": Scenario(
        key="refund_never_lands", journey="support_refund", step="s7",
        lever=None,
        mechanism="The ticket is Solved and the refund was instructed, and the money never reaches "
                  "the customer's bank. Two outcome layers disagree, a week apart.",
        evidence=Source("This doesn't guarantee that the refund has landed on the customer's side.",
                        "Stripe, Understanding refund statuses",
                        "https://support.stripe.com/questions/understanding-refund-statuses",
                        "accessed 2026-09-18", "first-party"),
        expected="⭐ The purest case Provy has. No run-time defect exists even in principle: the run "
                 "was correct and the outcome settled elsewhere, later, differently.",
    ),

    "ticket_resolved_not_fixed": Scenario(
        key="ticket_resolved_not_fixed", journey="itsm_incident", step="s5",
        lever="reversed_on_appeal",
        mechanism="Resolved is recorded, the underlying condition is untouched, and it recurs.",
        evidence=Source("the agent completed successfully by the metrics we're tracking but the "
                        "output was wrong",
                        "SRE describing production LLM-agent operations",
                        "https://www.reddit.com/r/Observability/comments/1tkfkul/automating_root_cause_analysis_for_ai_agent/",
                        "2026-05-22"),
        expected="Reconciliation catches this. Provy is genuinely built for it.",
    ),

    "orchestrator_never_ran": Scenario(
        key="orchestrator_never_ran", journey="itsm_incident", step="s4",
        lever="agent_paralysis",
        mechanism="A step that was explicitly called out never executed. No call, no output, no error. "
                  "Only absence.",
        evidence=Source("'Next action recommendation AI agent' not running even though it is "
                        "explicitly called out",
                        "ServiceNow practitioner",
                        "https://www.servicenow.com/community/now-assist-forum/generate-resolution-plan-agentic-workflow-is-not-working-oob/td-p/3519458",
                        "2026-04-03"),
        expected="Nothing to find in the trace. Anything reading spans for a defect sees a short, "
                 "clean, cheap run.",
    ),

    "denial_overturned": Scenario(
        key="denial_overturned", journey="claims_adjudication", step="s3",
        lever="reversed_on_appeal",
        mechanism="The decision was final, defensible and complete when made. An appeal reverses it "
                  "weeks later.",
        evidence=Source("If your insurance company still denies your claim, you can file for an "
                        "external review.",
                        "HealthCare.gov, federal consumer guidance",
                        "https://www.healthcare.gov/appeal-insurance-company-decision/internal-appeals/",
                        "date not displayed", "government"),
        expected="⭐ The single best fit in the product. Nothing in the run is wrong, so nothing at "
                 "run time can catch it.",
    ),

    "duplicate_identity": Scenario(
        key="duplicate_identity", journey="identity_access", step="s5",
        lever=None,
        mechanism="Provisioning creates a second account instead of matching the existing one. Both "
                  "exist, both look valid, and entitlements split between them.",
        evidence=Source("creates a user every time instead of updating when the user already exists.",
                        "SCIM implementer",
                        "https://github.com/AzureAD/SCIMReferenceCode/issues/63", "2021-08-16"),
        expected="Unknown. Identity resolution is the hard part of the 2029 thesis and nothing "
                 "measures it today.",
    ),

    "stale_edit_reapplied": Scenario(
        key="stale_edit_reapplied", journey="coding_agent", step="s3",
        lever="context_truncated",
        mechanism="The agent contradicts a state it established earlier in the same run, reapplying "
                  "a superseded revision.",
        evidence=Source("older, superseded revisions of the code [are] reapplied",
                        "GitHub Copilot user",
                        "https://github.com/microsoft/vscode/issues/265794", "2025-09-09"),
        expected="The contradiction is in the trace and nothing reads across steps.",
    ),

    "obeyed_and_wrong": Scenario(
        key="obeyed_and_wrong", journey="coding_agent", step="s3",
        lever="overliteral_constraint",
        mechanism="The agent honours 'DO NOT MODIFY: tests, configuration' and ships a wrong result. "
                  "It reads as compliance, not error.",
        evidence=Source("the largest failure cluster in the empirical study of failed agent PRs is "
                        "over-literal instruction following",
                        "arXiv study of failed agent pull requests",
                        "docs/failure-research/00-evidence.md", "2026-08 (Aug research pass)", "study"),
        expected="Nothing. There is no defect in the trace. Only reconciliation can see it.",
    ),

    "rca_named_by_proximity": Scenario(
        key="rca_named_by_proximity", journey="aiops_rca", step="s3",
        lever="correlation_as_cause",
        mechanism="The nearest correlated change is named as the cause, ranked by proximity rather "
                  "than evidence.",
        evidence=Source("the agent incorrectly diagnosed the root cause and failed to mitigate it.",
                        "Google SRE, AI Operator evaluation",
                        "https://sre.google/resources/practices-and-processes/ai-engineering-reliable-operations/",
                        "publication date not displayed", "first-party"),
        expected="Should refuse to confirm the named cause. Provy making the same argument about "
                 "itself.",
        measured="10 of 10 diverged. ZERO causes named at high or medium confidence. 7 low-confidence "
                 "candidates, all base_rate_verdict=insufficient, and 3 outright refusals. On a clean "
                 "control arm: no attributions at all. Provy committed to nothing, which is the "
                 "expectation met. ⚠ n=10 against MIN_GROUP=6, so 'insufficient' was the only verdict "
                 "this sample size could return.",
        measured_on="2026-09-19",
    ),

    "tool_returns_200_and_nothing": Scenario(
        key="tool_returns_200_and_nothing", journey="aiops_rca", step="s2",
        lever="ok_but_empty",
        mechanism="A tool answers HTTP 200 with a hollow body. Status-based checks read it as healthy "
                  "and the agent reasons on nothing.",
        evidence=Source("Tools return HTTP 200 while delivering empty or malformed payloads",
                        "Openlayer production failure taxonomy",
                        "docs/failure-research/00-evidence.md", "2026-08 (Aug research pass)",
                        "vendor"),
        expected="empty_output may catch it, may not: the existing tool_fault:empty sets an error and "
                 "this deliberately does not. Worth measuring before the story is told.",
    ),

    "approval_that_never_happened": Scenario(
        key="approval_that_never_happened", journey="identity_access", step="s3",
        lever=None,
        mechanism="Malformed tool-call JSON is swallowed and replaced with an empty object before an "
                  "approval predicate runs. The check evaluates {}, returns false, and the run "
                  "proceeds without the human approval it required.",
        evidence=Source("invalid tool-call JSON is swallowed and replaced with `{}` before the "
                        "predicate runs",
                        "OpenAI Agents Python, issue 3863",
                        "https://github.com/openai/openai-agents-python/issues/3863", "2026-07-17"),
        expected="⭐ THE STRONGEST NEW SCENARIO IN THE 18 SEP RESEARCH. A governance control that did "
                 "not fire, leaving no error and no defect anywhere in the run. Nothing at run time "
                 "can see it, which is the exact shape Provy exists for. No lever yet.",
    ),

    "empty_tool_arguments": Scenario(
        key="empty_tool_arguments", journey="coding_agent", step="s2",
        lever=None,
        mechanism="The model emits a tool call whose arguments are an empty object while the tool "
                  "declares required parameters.",
        evidence=Source("33 of 267 `tool_use` blocks (12.4%) had a completely empty `input: {}`",
                        "n8n practitioner, measured at the provider boundary through a proxy",
                        "https://github.com/n8n-io/n8n/issues/37916", "2026-09"),
        expected="No lever. ⛔ AND 12.4% IS ONE PRACTITIONER'S SAMPLE OF 267 CALLS IN ONE SETUP, NOT A "
                 "PRODUCTION RATE. The '3 to 15%' figure the August mapping used has no primary "
                 "source at all.",
    ),

    "ownership_changed_underneath": Scenario(
        key="ownership_changed_underneath", journey="revops_lead", step="s4",
        lever=None,
        mechanism="Record ownership changes automatically after the agent routed it, so the routing "
                  "that is audited is not the routing that happened.",
        evidence=Source("Lead and Case record ownership can change automatically",
                        "Salesforce troubleshooting documentation",
                        "https://help.salesforce.com/s/articleView?id=000385929&language=en_US&type=1",
                        "2026-07-04", "vendor"),
        expected="Unknown. No pack models this and no lever exists.",
    ),
}


# ── Lookups ─────────────────────────────────────────────────────────────────

def scenarios_for(journey_key: str) -> list[Scenario]:
    return [s for s in SCENARIOS.values() if s.journey == journey_key]


def unmeasured() -> list[Scenario]:
    """⛔ The honest backlog: scenarios whose expectation has never been checked."""
    return [s for s in SCENARIOS.values() if s.measured is None]


def without_lever() -> list[Scenario]:
    """Mechanisms with evidence and no way to reproduce them yet."""
    return [s for s in SCENARIOS.values() if s.lever is None]
