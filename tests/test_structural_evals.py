"""
Structural (layer 3) checks derived from the run itself.

⛔ THE SIMULATOR NEVER EMITTED ONE. EvalResult defaults to layer 4, so every sim fleet graded output
quality and nothing else, while Provy's seeded structural catalogue sat enabled and ungraded. That is
the state argus#671 now makes visible.
"""
from engine.structural import structural_evals, TOOL_SUCCESS_FLOOR
from engine.types import RunResult, TraceStep, AgentSpec


def A(*names):
    return [AgentSpec(name=n, display_name=n.title(), role="r", sort_order=i) for i, n in enumerate(names)]


def step(agent, step_type="agent_message", outcome="ok", tool_name=None):
    return TraceStep(agent=agent, step_type=step_type, outcome=outcome, tool_name=tool_name)


def run(traces, terminal="completed"):
    return RunResult(entity_id="E1", session_type="s", session_id="sess-1",
                     traces=traces, terminal_reason=terminal)


def by_name(evs, name):
    return [e for e in evs if e.eval_name == name]


def test_every_result_is_layer_three():
    evs = structural_evals(run([step("a")]), A("a"))
    assert evs and all(e.layer == 3 for e in evs)


def test_carries_observed_so_provy_can_judge_reachability():
    # argus#674 reads detail.observed to decide whether a limit can ever fire. A structural result
    # without it is invisible to that check.
    evs = structural_evals(run([step("a")]), A("a"))
    assert all("observed" in e.detail for e in evs)


class TestPipelineCompletion:
    def test_passes_when_every_agent_ran(self):
        evs = structural_evals(run([step("a"), step("b")]), A("a", "b"))
        assert by_name(evs, "pipeline_completion")[0].passed

    # ⛔ THE ROSTER IS THE DENOMINATOR. Using the agents that DID run makes a skipped agent invisible,
    # which is the single failure this check exists to catch.
    def test_fails_and_names_the_agent_that_did_not_run(self):
        e = by_name(structural_evals(run([step("a")]), A("a", "b", "c")), "pipeline_completion")[0]
        assert not e.passed
        assert e.score == round(1 / 3, 4)
        assert "b" in e.detail["reasoning"] and "c" in e.detail["reasoning"]

    def test_a_skipped_agent_cannot_be_hidden_by_another_running_twice(self):
        e = by_name(structural_evals(run([step("a"), step("a")]), A("a", "b")), "pipeline_completion")[0]
        assert not e.passed


class TestToolSuccessRate:
    def test_fails_when_tools_error(self):
        traces = [step("a", "tool_call", "error", "t"), step("a", "tool_call", "ok", "t")]
        e = by_name(structural_evals(run(traces), A("a")), "tool_success_rate")[0]
        assert e.score == 0.5 and not e.passed

    # ⛔ A CHECK THAT CANNOT RUN WRITES NOTHING. A fabricated 1.0 for an agent that called no tool is
    # how a blind spot comes to look like health.
    def test_an_agent_with_no_tool_calls_gets_no_row(self):
        evs = structural_evals(run([step("a"), step("b")]), A("a", "b"))
        assert by_name(evs, "tool_success_rate") == []

    def test_is_scoped_per_agent_not_per_run(self):
        traces = [step("a", "tool_call", "error", "t"), step("b", "tool_call", "ok", "t")]
        evs = by_name(structural_evals(run(traces), A("a", "b")), "tool_success_rate")
        assert {e.agent: e.passed for e in evs} == {"a": False, "b": True}

    def test_the_floor_must_be_reached_not_merely_approached(self):
        n = 10
        errs = int(round(n * (1 - TOOL_SUCCESS_FLOOR)))
        traces = [step("a", "tool_call", "error", "t")] * errs + [step("a", "tool_call", "ok", "t")] * (n - errs)
        assert by_name(structural_evals(run(traces), A("a")), "tool_success_rate")[0].passed
        traces.append(step("a", "tool_call", "error", "t"))
        assert not by_name(structural_evals(run(traces), A("a")), "tool_success_rate")[0].passed


class TestDecisionMade:
    def test_is_asked_of_the_last_agent_in_the_roster(self):
        e = by_name(structural_evals(run([step("a"), step("z")]), A("a", "z")), "decision_made")[0]
        assert e.agent == "z" and e.passed

    def test_fails_when_the_decider_errored_instead_of_deciding(self):
        traces = [step("a"), step("z", outcome="error")]
        e = by_name(structural_evals(run(traces), A("a", "z")), "decision_made")[0]
        assert not e.passed

    def test_fails_when_the_decider_never_spoke(self):
        e = by_name(structural_evals(run([step("a")]), A("a", "z")), "decision_made")[0]
        assert not e.passed

    def test_a_tool_call_is_not_a_decision(self):
        traces = [step("z", "tool_call", "ok", "t")]
        assert not by_name(structural_evals(run(traces), A("z")), "decision_made")[0].passed


class TestExitQuality:
    # ⛔ FOUND BY EMITTING TO A REAL SIM TENANT. The first version whitelisted good terminal reasons
    # and failed exit_quality on 25 of 25 teameight runs, whose success terminal is "followed_up".
    # No English word list holds every fleet's vocabulary: support says "resolved", trading says
    # "intraday_entries_placed", claims says "paid". The tenant owns its nouns.
    def test_a_fleet_vocabulary_provy_has_never_seen_is_not_a_failure(self):
        for term in ("followed_up", "intraday_entries_placed", "paid", "no_candidates", "whatever_this_fleet_calls_it"):
            assert by_name(structural_evals(run([step("a")], term), A("a")), "exit_quality")[0].passed, term

    def test_the_terminal_the_engine_sets_when_it_breaks_does_fail(self):
        e = by_name(structural_evals(run([step("a")], "pipeline_break"), A("a")), "exit_quality")[0]
        assert not e.passed and "pipeline_break" in e.detail["reasoning"]

    def test_an_unset_exit_is_not_silently_good(self):
        e = by_name(structural_evals(run([step("a")], ""), A("a")), "exit_quality")[0]
        assert not e.passed
        # ⛔ AND IT SAYS SOMETHING DIFFERENT from a broken exit: "we do not know" is not "it broke".
        assert "no terminal reason" in e.detail["reasoning"]


def test_a_clean_run_passes_everything():
    traces = [step("a", "tool_call", "ok", "t"), step("a"), step("b")]
    assert all(e.passed for e in structural_evals(run(traces), A("a", "b")))


def test_a_broken_run_fails_more_than_one_check():
    # A lever that breaks a run should light up several structural checks at once, the way a real
    # failure does. If only ever one fails, the checks are measuring the same thing.
    traces = [step("a", "tool_call", "error", "t")]
    failed = [e.eval_name for e in structural_evals(run(traces, "pipeline_break"), A("a", "b")) if not e.passed]
    assert set(failed) == {"pipeline_completion", "tool_success_rate", "decision_made", "exit_quality"}


class TestBothRunPathsEmitThem:
    """
    ⛔ TWO PATHS REACH emit_run AND ONLY ONE IS OBVIOUS. runner.run_one is the plain fleet loop;
    desk._finish is the journey/wait loop the desk fleets use. Deriving structural checks in either
    caller alone would leave the other fleet ungraded and nothing would report the difference.
    This asserts the seam, not the callers.
    """

    def _emitted_layers(self, agents):
        from engine.emitter import ProvyEmitter
        em = ProvyEmitter(ingest_key="provy_fake", is_simulated=False, capture=True)
        em.emit_run(run([step("a", "tool_call", "error", "t"), step("a")], "exploded"), agents)
        return [c["payload"] for c in em.sent if c["path"] == "/api/ingest/eval"]

    def test_a_roster_produces_structural_results(self):
        payloads = self._emitted_layers(A("a", "b"))
        assert payloads, "no evals were emitted at all"
        assert {p["layer"] for p in payloads} == {3}
        assert {p["eval_name"] for p in payloads} >= {
            "pipeline_completion", "tool_success_rate", "decision_made", "exit_quality"}

    # ⛔ NO ROSTER MEANS NO pipeline_completion, NOT A FABRICATED PASS. Its denominator is the agents
    # that SHOULD have run, and without one there is no honest answer.
    def test_no_roster_emits_nothing_structural_rather_than_guessing(self):
        assert self._emitted_layers(None) == []


class TestASkipIsNotARun:
    """
    ⛔ FOUND BY RUNNING THE SIMULATOR, NOT BY THE SUITE. pipeline_completion passed 60 of 60 runs
    while skip_propagation was firing, because that lever REMOVES an agent's real traces and APPENDS
    a skip step for it and every agent downstream. "Has at least one trace" is therefore true even in
    a fully broken pipeline. The original test used ABSENT steps, a shape the simulator never
    produces, so it could not have caught this.
    """

    def test_an_agent_whose_only_step_is_a_skip_did_not_run(self):
        traces = [step("a"), TraceStep(agent="b", step_type="skip", outcome="skipped")]
        e = by_name(structural_evals(run(traces), A("a", "b")), "pipeline_completion")[0]
        assert not e.passed
        assert "b" in e.detail["reasoning"]

    def test_the_whole_downstream_of_a_propagated_skip_counts_as_missing(self):
        traces = [step("a"),
                  TraceStep(agent="b", step_type="skip", outcome="skipped"),
                  TraceStep(agent="c", step_type="skip", outcome="skipped")]
        e = by_name(structural_evals(run(traces, "pipeline_break"), A("a", "b", "c")), "pipeline_completion")[0]
        assert e.score == round(1 / 3, 4)

    def test_an_agent_that_skipped_then_really_ran_still_counts(self):
        traces = [TraceStep(agent="a", step_type="skip", outcome="skipped"), step("a")]
        assert by_name(structural_evals(run(traces), A("a")), "pipeline_completion")[0].passed
