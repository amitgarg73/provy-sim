"""Domain pack - Claude Code / coding agent (commitment integrity).

What Provy looks like out of the box if somebody points Claude Code telemetry at it. A coding agent
takes an engineering task, explores the repo, edits files, runs a check, and reports the work done.
The repository and CI settle whether any of that was true.

⛔ THIS IS INTERNAL INSTRUMENTATION, NOT A PRODUCT LINE. The Agent Journey work was parked on 17 Aug
2026 on competitive evidence: Anthropic ships a Claude Code analytics dashboard AND an API, trace
capture via OpenObserve is a free five-minute setup, and PR revert rate / code survival rate are the
category's standard metrics with published benchmarks we do not have. See
`project_provy_agent_journey_parked`. This pack exists to answer "what would it look like", which is
a different question from "should we sell it".

⛔ FOUR DESIGN CONCLUSIONS FROM THAT SPEC, ALL LOAD-BEARING HERE:

  1. THE WORK ITEM IS WHAT THE OUTCOME SETTLES ON, NEVER THE CONVERSATION BOUNDARY. One engineering
     task that ends in a change is one session. A conversation is not a verdict, and prompt
     boundaries can draw a journey but cannot draw a work item.
  2. CAPTURE THE CHANGE ARTIFACT OR THE JOIN IS UNRECOVERABLE. Every run carries branch, commit,
     paths and cwd on its trace. Conversation boundaries can be re-derived from stored spans later;
     GitHub can never tell you afterwards which commit came from which session.
  3. ⛔ A VALIDATION FAILURE MUST NEVER BE SCORED AS AN AGENT ERROR. An agent that runs its tests and
     finds them red did its job. `tests_green` and `validation_ran` are deliberately SEPARATE
     conditions for this reason: conflating them makes the agent that checks its work score worse
     than the one that does not, with every number still looking plausible.
  4. "NOT OBSERVABLE" IS NOT ZERO. `validation_ran` has three states here, not two.

The headline condition is c2. Measured on prod while the spec was written: the trading fleet had
6,201 spans and NO validation step type at all. For real fleets today the finding is not retry loops,
it is the absence of any check before the claim.
"""
from __future__ import annotations

from engine.commitment import CommitmentPack, Injector
from engine.types import (AgentSpec, Criterion, InjectedFault, LeverManifest, RunContext,
                          RunResult, TraceStep)


class ClaudeCodePack(CommitmentPack):
    workflow = "claude_code"
    session_type = "task"

    def agents(self) -> list[AgentSpec]:
        return [
            AgentSpec("planner", "Plan",
                      "Reads the request and decides what has to change.", "\U0001F5FA️", 0),
            AgentSpec("explorer", "Explore",
                      "Searches and reads the codebase to find the files the change touches.", "\U0001F50E", 1),
            AgentSpec("editor", "Edit",
                      "Writes the change into the working tree.", "✏️", 2),
            AgentSpec("checker", "Check",
                      "Runs the tests, the build or the linter against what was written.", "\U0001F9EA", 3),
            AgentSpec("committer", "Commit",
                      "Commits the change to the branch and reports the task done.", "\U0001F4E6", 4),
        ]

    def contract(self) -> list[Criterion]:
        return [
            Criterion("c1", "The change actually landed on the branch", "both", "change_committed", "eq", True),
            Criterion("c2", "It ran a check before reporting the task done", "both", "validation_ran", "eq", True),
            Criterion("c3", "The tests passed on the committed change", "outcome", "tests_green", "eq", True),
            Criterion("c4", "The change survived without being reverted", "outcome", "not_reverted", "eq", True),
            Criterion("c5", "It touched only the files the task called for", "both", "scope_correct", "eq", True),
            Criterion("c6", "No duplicate or conflicting commit", "outcome", "no_duplicate_commit", "eq", True),
        ]

    def failure_cost(self) -> dict:
        """Engineering time, at a loaded rate. A change that never landed is the cheap one because
        somebody notices. A reverted change is expensive because it shipped first."""
        return {
            "commitment_unsettled":    150.0,   # reported done, nothing on the branch, redo it
            "commitment_wrong_target": 320.0,   # touched files outside the task, review and unpick
            "commitment_wrong_amount": 260.0,   # committed with the tests red
            "commitment_duplicate":    180.0,   # a conflicting second commit to untangle
            "no_check_before_claim":   400.0,   # claimed done having verified nothing at all
        }

    def trace_aliases(self) -> dict[str, str]:
        """What a Claude Code span actually carries, which is not the contract's vocabulary.

        `tool_ok` is the recurring trap and the reason conclusion 3 exists: the agent records that
        its Bash call EXITED, which is not the same as a check having been run, and neither is the
        same as the check passing. Three different facts, one field in the telemetry."""
        return {
            "change_committed": "commit_tool_ok",
            "validation_ran":   "ran_bash_check",
            "scope_correct":    "paths_in_scope",
        }

    def signal_owners(self) -> dict[str, str]:
        """⛔ WITHOUT THIS EVERY FAILURE BLAMES THE COMMITTER, which is the last step and almost
        never the cause. Which files were in scope came out of the explore step. Whether a check ran
        is the checker's job and nobody else's."""
        return {
            "change_committed":    "committer",
            "no_duplicate_commit": "committer",
            "validation_ran":      "checker",
            "tests_green":         "editor",     # red tests mean the code is wrong, not the check
            # A revert IS attributable, unlike an SLA. It occupies the manifest's sla slot for
            # mechanical reasons, and "this change was bad enough to undo" points at whoever wrote
            # it, so leaving it unowned would blame the committer for the editor's work.
            "not_reverted":        "editor",
            "scope_correct":       "explorer",
        }

    def lever_manifest(self) -> LeverManifest:
        return LeverManifest(
            resolver_agent="committer", retriever_agent="explorer", reviewer_agent="committer",
            first_agent="planner", downstream_agent="editor",
            correctness_signal="change_committed",
            policy_signal="validation_ran",
            sla_signal="not_reverted",
            other_signals={"validation_ran": "checker", "scope_correct": "explorer"},
            drift_agent="editor",
        )

    def injectors(self) -> list[Injector]:
        return [
            Injector("change_never_committed", "unsettled", "nothing_on_branch",
                     "the agent reported the task done and the branch has no such commit"),
            Injector("wrong_files_touched", "wrong_target", "out_of_scope_paths",
                     "the change landed, and it edited files the task never asked about"),
            Injector("committed_red", "wrong_amount", "tests_failing_on_commit",
                     "the commit is on the branch with the tests red"),
            Injector("conflicting_commit", "duplicate", "duplicate_commit",
                     "a second, conflicting commit landed for the same task"),
        ]

    def settle_map(self) -> dict:
        return {"promise": "change_committed", "wrong_target": "scope_correct",
                "wrong_amount": "tests_green", "duplicate": "no_duplicate_commit"}

    def commit_ref(self, item) -> str:
        return item["branch"]

    def commit_amount(self, item) -> float:
        return float(item["est_minutes"])

    def clean_narration(self, amount: float) -> str:
        return ("Repository check: the commit is on the branch, the tests are green on it, and it "
                "was not reverted. Promise kept.")

    # ── the check that never ran ──────────────────────────────────────────────
    def _rate(self, ctx: RunContext, name: str, default: float) -> float:
        setting = ctx.levers.settings.get(name)
        return setting.rate if setting is not None else default

    def _apply_no_check(self, r: RunResult, ctx: RunContext) -> None:
        """The agent reported the task done without running any check at all.

        ⛔ THIS IS THE ONE THAT MATTERS AND THE ONE NOTHING ELSE MEASURES. It is not a tool error, so
        no reliability metric sees it. The commit is on the branch, so completion looks fine. The
        tests might even be green by luck, which is precisely why c2 and c3 are separate conditions:
        an agent that skipped the check and got away with it is still an agent that skipped the
        check, and a fleet that scores those two together can never tell you so.
        """
        if r.faults or not bool(r.real_signals.get("change_committed", True)):
            return
        if ctx.rng.random() >= self._rate(ctx, "no_check_before_claim", 0.0):
            return
        r.real_signals["validation_ran"] = False
        r.metadata["no_check_before_claim"] = True
        # Remove the checker's step entirely: the honest telemetry of a check that never happened is
        # an absent span, not a span recording a failure.
        r.traces = [t for t in r.traces if t.agent != "checker"]
        r.faults.append(InjectedFault(
            "no_check_before_claim", "checker", "claim_without_verification",
            {"signal": "validation_ran", "side": "both"}))

    _TASKS = [
        ("fix", "Fix the off-by-one in the pagination helper", ["lib/paginate.ts", "lib/__tests__/paginate.test.ts"], 35),
        ("fix", "Stop the retry loop from swallowing the original error", ["src/retry.py", "tests/test_retry.py"], 45),
        ("feature", "Add a --dry-run flag to the import command", ["cli/import.ts", "cli/__tests__/import.test.ts"], 60),
        ("mockup", "Build the settings page mockup as a standalone HTML file", ["docs/mockups/settings.html"], 50),
        ("mockup", "Mock up the empty state for the fleet list", ["docs/mockups/fleet-empty.html"], 30),
        ("tests", "Add test cases for the date-range parser edge cases", ["tests/test_daterange.py"], 40),
        ("tests", "Cover the webhook signature verification path", ["tests/test_webhooks.py"], 55),
        ("refactor", "Pull the duplicated auth check into one helper", ["src/auth.py", "src/routes/admin.py"], 70),
        ("chore", "Bump the lockfile and fix the two type errors it surfaces", ["package-lock.json", "src/types.d.ts"], 25),
    ]

    def generate_work_item(self, rng) -> tuple[dict, dict]:
        kind, text, paths, minutes = rng.choice(self._TASKS)
        n = rng.randint(1000, 9999)
        item = {
            "id": f"TASK-{n}",
            # ⛔ Conclusion 2: the change artifact rides on the work item, so the join to the
            # repository is recoverable later. Without it nothing can tell you which commit came
            # from which session.
            "branch": f"claude/{kind}-{n}",
            "repo": "provy",
            "cwd": "/Users/dev/work/provy",
            "kind": kind,
            "paths": paths,
            "est_minutes": minutes,
            "text": text,
        }
        gt = {"kind": kind, "paths": paths, "text": text, "est_minutes": minutes}
        return item, gt

    def build_clean_run(self, item: dict, gt: dict, ctx: RunContext) -> RunResult:
        r = self.base_result(item)
        eid = r.entity_id
        A = {a.name: a for a in self.agents()}
        paths, branch = item["paths"], item["branch"]
        check_cmd = {"mockup": "npx playwright screenshot", "tests": "pytest -q",
                     "chore": "npm run typecheck"}.get(item["kind"], "pytest -q")

        r.traces.append(self.agent_step(
            ctx, A["planner"], item,
            decision=f"{item['text']}. Expect to touch {len(paths)} file(s).",
            entity_id=eid,
            payload_extra={"branch": branch, "cwd": item["cwd"], "repo": item["repo"],
                           "narration": f"Planned the change: {item['text']}."}))

        r.traces.append(self.tool_step(
            ctx, A["explorer"], "Grep",
            tool_input={"pattern": item["text"].split()[1], "path": item["cwd"]},
            tool_output={"matches": len(paths), "files": paths, "truncated": False},
            entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["explorer"], item,
            decision=f"The change belongs in {', '.join(paths)}.",
            entity_id=eid,
            payload_extra={"paths": paths, "narration": f"Located the change in {', '.join(paths)}."}))

        r.traces.append(self.tool_step(
            ctx, A["editor"], "Edit",
            tool_input={"files": paths, "branch": branch},
            tool_output={"applied": True, "files_changed": len(paths)},
            entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["editor"], item,
            decision=f"Wrote the change across {len(paths)} file(s).",
            entity_id=eid,
            payload_extra={"narration": f"Made the edit in {len(paths)} file(s)."}))

        r.traces.append(self.tool_step(
            ctx, A["checker"], "Bash",
            tool_input={"command": check_cmd, "cwd": item["cwd"]},
            tool_output={"exit_code": 0, "passed": True, "summary": "all checks green"},
            entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["checker"], item,
            decision=f"Ran `{check_cmd}`; it passed.",
            entity_id=eid,
            payload_extra={"narration": f"Ran `{check_cmd}` before claiming anything. Green."}))

        told = f"Done: {item['text'].lower()}, committed to {branch}."
        r.traces.append(self.tool_step(
            ctx, A["committer"], "Bash",
            tool_input={"command": f"git commit -am '{item['text']}'", "cwd": item["cwd"]},
            tool_output={"exit_code": 0, "commit": f"{eid[-4:]}abc12", "branch": branch},
            entity_id=eid))
        r.traces.append(self.agent_step(
            ctx, A["committer"], item,
            decision=f"Committed to {branch} and reported the task done.",
            entity_id=eid,
            payload_extra={"action": "commit", "branch": branch, "told_user": told,
                           "confidence": "HIGH", "narration": f'Committed and reported: "{told}"'}))

        r.evals = [
            self.eval_pass("planner", "plan_accuracy", eid, "the plan matches what the task asked for"),
            self.eval_pass("explorer", "search_relevance", eid, "found the files the change belongs in"),
            self.eval_pass("editor", "edit_quality", eid, "the change is coherent and in the right place"),
            self.eval_pass("checker", "verification_ran", eid, "a real check ran against the change"),
            self.eval_pass("committer", "commit_quality", eid, "the commit matches the work described"),
        ]
        r.terminal_reason = "committed"
        return r

    def run_pipeline(self, item, gt, ctx: RunContext) -> RunResult:
        """Adds the repository read on top of the shared commitment pipeline.

        ⛔ THREE STATES ON `validation_ran`, NOT TWO (conclusion 4). `ran: true` means a check
        happened, `false` means the agent claimed done having run nothing, and a fleet whose adapter
        cannot see checks at all should report neither rather than a confident zero. This sim always
        knows, so it emits the first two; the third is a real-adapter concern and is documented
        rather than faked here."""
        r = super().run_pipeline(item, gt, ctx)
        self._apply_no_check(r, ctx)
        real = r.real_signals
        committed = bool(real.get("change_committed", True))
        checked = bool(real.get("validation_ran", True))
        green = bool(real.get("tests_green", True))

        if not committed:
            note = f"Nothing is on {item['branch']}. The task was reported done anyway."
        elif not checked:
            note = ("The commit is on the branch and no check was ever run against it. "
                    "Nothing errored, so no reliability metric sees this.")
        elif not green:
            note = "The commit is on the branch with the tests red."
        else:
            note = "Commit on the branch, checks green, not reverted."

        r.traces.append(TraceStep(
            agent="committer", step_type="tool_call", tool_name="repo.read_back",
            tool_input={"branch": item["branch"], "repo": item["repo"]},
            tool_output={"committed": committed, self.trace_aliases()["validation_ran"]: checked,
                         "tests_green": green},
            outcome="ok", entity_id=r.entity_id,
            payload_extra={"narration": f"Repository check: {note}"}))
        return r
