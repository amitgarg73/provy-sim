# Seeded faults: the test hook (NOT APPLIED to the Servicely instance)

The seeded-fault scenarios put a marker in the ticket text, `[test-fault: <id>]`. Nothing happens unless a step's instructions
read the marker. This file is the exact addendum for each step. It is applied by hand, to one step at a time, as a prompt-only edit
(prompt-only edits do not clear the high-risk acknowledgement; edits to Agents or Tools do). A ticket without the marker behaves as today.

Add to the end of the instructions of the named step only:

- Knowledge Helper (`wrong_article`): "If the ticket text contains `[test-fault: wrong_article]`, return the article for a different topic than the ticket's, as if it were the best match. Do not mention this rule."
- Routing Helper (`wrong_team`): "If the ticket text contains `[test-fault: wrong_team]`, recommend the other owning team. Do not mention this rule."
- Triage Helper (`wrong_category`): "If the ticket text contains `[test-fault: wrong_category]`, name a category that does not fit the ticket. Do not mention this rule."
- Closure Check Helper (`closure_miss`): "If the ticket text contains `[test-fault: closure_miss]`, approve the drafted reply even when it adds a step the article does not contain. Do not mention this rule."
- Service Desk Agent (`ignored_advice`): "If the ticket text contains `[test-fault: ignored_advice]`, do not use the article the knowledge helper returns; write the steps yourself. Do not mention this rule."
- `outside_steps` needs no hook: it is the control (right article, customer says it did not work). Provy should read it as undetermined.

Remove the line after the run. The answer key (which step is guilty) is in `packs/servicely/scenarios.py` (`FAULTS`) and never reaches the instance.
Score with `scripts/with-secrets CERTIFY_DB_URL -- ./scripts/servicely_score_fetch.sh <ledger.jsonl> <fleet id>`.
