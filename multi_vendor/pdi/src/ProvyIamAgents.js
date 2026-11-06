/**
 * Script include ProvyIamAgents (POC). Global scope, ES5. The PDI's agents, behind the property provy.iam.agentic (default false; the plain worker is unchanged while it is).
 *
 *   ESTATE APPLIER   Takes the queued task, asks the model client for a plan, VALIDATES it against the allow-list (ProvyIamPlan), applies it through the estate, replies.
 *                    A plan that is refused is never applied: the task's own plan is used instead (what the plain worker does), and the refusal is in the trace.
 *   ESTATE VERIFIER  After the applier, reads the estate back and says, per system, whether the access really changed. THE VERDICT IS COMPUTED, NEVER MODELLED: the model only
 *                    words the finding. The verdict goes to the trace, NOT into the reply to the asking system: the asking system's agent still reports what it was told, which is the
 *                    gap this POC exists to show and for Provy to catch.
 *   HR SYNC          Stays a plain scheduled job (hr_sync.js); it reports to the trace as its own named actor.
 *
 * process(subject, params, queueItemId) never throws and returns { result, verification, plan_source } where `result` has the same shape the plain worker replies with.
 */
var ProvyIamAgents = Class.create();
ProvyIamAgents.prototype = {
  APPLIER: 'access_applier',
  VERIFIER: 'access_verifier',
  WORKER: 'access_worker',

  initialize: function () {
    this.estate = new global.ProvyIamEstate();
    this.plan = new global.ProvyIamPlan();
    this.model = new global.ProvyIamModel();
  },

  enabled: function () { return String(gs.getProperty('provy.iam.agentic') || 'false').toLowerCase() === 'true'; },

  /** The applier's prompt: the task, the rules and the answer shape. No secret and no free text from outside goes into it beyond the task's own fields. */
  _planPrompt: function (subject, params) {
    var named = this.plan.taskSystems(params);
    return 'Task: apply the command ' + subject + ' for the person ' + params.person + '.\n' +
      'Systems named: ' + (named.length ? named.join(', ') : 'none (all of: ' + this.plan.SYSTEMS.join(', ') + ')') + '.\n' +
      'Allowed commands: ' + this.plan.COMMANDS.join(', ') + '. Use only the named systems and only this person.\n' +
      'Answer with ONLY a JSON object {"steps":[{"command":"...","params":{"person":"...","systems":["..."]}}]} and nothing else.';
  },

  _decide: function (subject, params, trace) {
    var task = { command: subject, params: params };
    var own = this.plan.taskPlan(subject, params);
    var answer = this.model.complete(this._planPrompt(subject, params), 'plan', { stub: JSON.stringify(own), system: 'You plan access changes. Reply with JSON only.' });
    var proposed = answer.ok ? this.plan.parsePlan(answer.text) : null;
    var source = 'model', rejected = null, chosen = proposed;
    if (!answer.ok) { source = 'task'; rejected = 'the model did not answer (' + answer.error + ')'; chosen = own; }
    else if (!proposed) { source = 'task'; rejected = 'the model answer held no plan'; chosen = own; }
    else {
      var v = this.plan.validate(proposed, task);
      if (!v.ok) { source = 'task'; rejected = v.reason; chosen = own; }
    }
    if (answer.mode === 'stub' && !rejected) source = 'stub';
    // a DECISION step: the agent chose a plan from the task. Provy counts steps of type decision or agent_message that carry a decision object.
    trace.step(this.APPLIER, 'decision', 'success', { phase: 'plan', plan_source: source, model_mode: answer.mode, plan: chosen, rejected_reason: rejected }, { model: this.model.label(), decision: this.plan.decisionOf(chosen, source, rejected) });
    return { plan: chosen, source: source, rejected: rejected };
  },

  _apply: function (plan, trace) {
    var applied = [], failed = null;
    for (var i = 0; i < plan.steps.length; i++) {
      var st = plan.steps[i], res;
      try { res = this.estate.apply(st.command, st.params); }
      catch (e) { res = { status: 'error', payload: { error: String(e).substring(0, 160) } }; }
      var items = (res.payload && res.payload.applied) || [];
      trace.step(this.APPLIER, 'tool_call', res.status === 'ok' ? 'success' : 'error',
        { phase: 'apply', command: st.command, systems: items, status: res.status }, { tool_input: st.params, tool_output: res.payload });
      if (res.status !== 'ok') { failed = res; break; }
      for (var k = 0; k < items.length; k++) applied.push(items[k]);
    }
    return { applied: applied, failed: failed };
  },

  /**
   * The per-system verdicts as they go into the trace. NO KEY HERE MAY REPEAT A TOP-LEVEL NAME OF THE STEP: Provy reads a step's fields by leaf name at any depth and the last one wins,
   * so a nested `held` overwrote the step's own `held` and a failed run was read as "estimated held yes" (6 Nov 2026). Per system the field is `ended`.
   */
  _forTrace: function (systems) {
    var out = {};
    for (var n in systems) if (systems.hasOwnProperty(n)) out[n] = { ended: !!systems[n].held, detail: systems[n].detail };
    return out;
  },

  _verify: function (subject, params, trace) {
    var reading = this.estate.read(params.person);
    var verdict = this.plan.verdict(subject, params, reading);
    var wording = this.model.complete('State in one plain sentence what this reading means for the access change. Reading: ' + JSON.stringify(verdict.systems), 'finding', { stub: verdict.summary, system: 'You word a finding in one sentence. Do not change the facts.' });
    var finding = (wording.ok && wording.text) ? String(wording.text).substring(0, 400) : verdict.summary;
    trace.step(this.VERIFIER, 'message', 'success', { phase: 'verify', held: verdict.held, checked: verdict.checked, systems: this._forTrace(verdict.systems), finding: finding, summary: verdict.summary },
      { model: this.model.label() });
    return { held: verdict.held, systems: verdict.systems, checked: verdict.checked, summary: verdict.summary, finding: finding, reading: reading };
  },

  process: function (subject, params, queueItemId) {
    var trace = new global.ProvyIamTrace();
    var p = params || {};
    var result;
    var verification = null, source = null;
    try {
      // parent_session is passed through unchanged (see ProvyIamTrace.open); item is only a plain reference kept in the session's metadata
      trace.open('pdi-' + String(queueItemId || 'direct-' + new Date().getTime()), 'access_provisioning', p.parent_session, { command: subject, person: p.person, item: p.item || null, correlation: p.correlation || null, agentic: true }, this.plan.handoffIn(p));
      var decided = this._decide(subject, p, trace);
      source = decided.source;
      var done = this._apply(decided.plan, trace);
      if (done.failed) result = { status: 'error', payload: done.failed.payload };
      else result = { status: 'ok', payload: { person: p.person, command: subject, applied: done.applied } };
      verification = this._verify(subject, p, trace);
      trace.step(this.WORKER, 'tool_call', result.status === 'ok' ? 'success' : 'error', { phase: 'reply', replied: result.status === 'ok' ? 'success' : 'fail', applied: (result.payload && result.payload.applied) || [] });
      trace.close(verification.summary, verification.held ? 'as_asked' : 'not_as_asked');
      // the system of record settles the work item: one outcome per run, from the estate's own reading, keyed on the request number
      try {
        var nowIso = new GlideDateTime().getValue().replace(' ', 'T') + 'Z';
        var outcome = this.plan.outcomeBody(subject, { held: verification.held, systems: verification.systems, checked: verification.checked }, p.correlation, nowIso.substring(0, 10), nowIso);
        if (outcome) trace.outcome(outcome);
      } catch (e3) { gs.warn('provy_iam agents: the outcome was not sent: ' + String(e3).substring(0, 100)); }
    } catch (e) {
      gs.warn('provy_iam agents: ' + String(e).substring(0, 160));
      if (!result) result = { status: 'error', payload: { error: 'the agents failed: ' + String(e).substring(0, 120) } };
      try { trace.close('the agents failed', 'error'); } catch (e2) { /* never throws */ }
    }
    return { result: result, verification: verification, plan_source: source, trace: trace.sent, trace_mode: trace.mode };
  },

  type: 'ProvyIamAgents'
};
