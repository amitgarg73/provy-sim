/**
 * Script include ProvyIamPlan (POC). Global scope, ES5. PURE: no database, no network, no clock, so it can be tested in node and on the instance with the same vectors
 * (tests/pdi_vectors.json, multi_vendor/pdi_reference.py is the Python twin, scripts/verify_pdi_agents.py runs the vectors against the deployed copy through the plan-check door).
 *
 * THE TWO RULES THAT MAKE A MODEL SAFE HERE
 *   1. A MODEL ONLY PROPOSES. `validate` is the allow-list: only this POC's commands, only the task's own provy.iam.* person, only the systems the task named. A plan that fails
 *      is never applied; the applier falls back to the task's own plan (`taskPlan`), which is exactly what the plain worker does today.
 *   2. A MODEL NEVER DECIDES THE OUTCOME. `verdict` reads the estate's own reading and says held or not, per system. A model may only word the finding.
 */
var ProvyIamPlan = Class.create();
ProvyIamPlan.prototype = {
  COMMANDS: ['provy-iam-disable-account', 'provy-iam-remove-groups', 'provy-iam-revoke', 'provy-iam-apply-change', 'provy-iam-grant'],
  SYSTEMS: ['directory', 'sso', 'vpn', 'saas_token'],
  GRANT_SYSTEMS: ['sso', 'vpn', 'saas_token'],
  GRANT_LEVELS: ['read', 'write', 'admin'],
  PERSON_PREFIX: 'provy.iam.',
  MAX_STEPS: 10,
  MAX_EXPIRES_MINUTES: 1440,

  initialize: function () {},

  /** A list's length, or -1 when it is not a list. The instance's REST parser can hand over an empty list as a Java list (no `length`, but `size()`), so both are accepted. */
  _len: function (x) {
    if (x === null || x === undefined || typeof x !== 'object') return -1;
    if (typeof x.length === 'number') return x.length;
    if (typeof x.size === 'function') return Number(x.size());
    return -1;
  },

  _has: function (list, v) { for (var i = 0; i < list.length; i++) if (list[i] === v) return true; return false; },

  /** The systems a task names, cleaned: known systems only, in the order given, no repeats. An empty or missing list means all four. */
  taskSystems: function (params) {
    var raw = (params && params.systems) || (params && params.system ? [params.system] : []);
    var out = [];
    for (var i = 0; i < raw.length; i++) {
      var s = String(raw[i]);
      if (this._has(this.SYSTEMS, s) && !this._has(out, s)) out.push(s);
    }
    return out;
  },

  /** What the task itself asks for, as a one-step plan: the same thing the plain worker does. */
  taskPlan: function (command, params) {
    var p = params || {};
    var step = { command: String(command), params: { person: p.person } };
    if (command === 'provy-iam-grant') {
      step.params.system = p.system || 'saas_token';
      step.params.level = p.level || 'read';
      if (p.expires_minutes) step.params.expires_minutes = p.expires_minutes;
    } else if (command === 'provy-iam-disable-account' || command === 'provy-iam-remove-groups') {
      // these two act on the directory only and take no system list
    } else {
      var sys = this.taskSystems(p);
      step.params.systems = sys.length ? sys : this.SYSTEMS.slice();
    }
    return { steps: [step] };
  },

  /**
   * The allow-list. Returns { ok: true } or { ok: false, reason }. `task` is { command, params } as queued.
   * A plan may only repeat the task's own person, use this POC's commands, and touch systems the task named (all four when it named none).
   */
  validate: function (plan, task) {
    var count = plan ? this._len(plan.steps) : -1;
    if (count < 0) return { ok: false, reason: 'the plan has no steps list' };
    if (count < 1) return { ok: false, reason: 'the plan has no steps' };
    if (count > this.MAX_STEPS) return { ok: false, reason: 'the plan has more than ' + this.MAX_STEPS + ' steps' };
    var person = String((task.params && task.params.person) || '');
    if (person.indexOf(this.PERSON_PREFIX) !== 0) return { ok: false, reason: 'the task person is not a ' + this.PERSON_PREFIX + '* person' };
    var named = this.taskSystems(task.params || {});
    var allowedSystems = named.length ? named : this.SYSTEMS;
    for (var i = 0; i < count; i++) {
      var st = plan.steps[i];
      if (!st || typeof st !== 'object') return { ok: false, reason: 'step ' + (i + 1) + ' is not an object' };
      if (!this._has(this.COMMANDS, st.command)) return { ok: false, reason: 'step ' + (i + 1) + ' uses a command that is not allowed' };
      var p = st.params || {};
      if (String(p.person || '') !== person) return { ok: false, reason: 'step ' + (i + 1) + ' names a different person' };
      if (st.command === 'provy-iam-grant') {
        if (!this._has(this.GRANT_SYSTEMS, p.system)) return { ok: false, reason: 'step ' + (i + 1) + ' grants on a system that is not allowed' };
        if (!this._has(allowedSystems, p.system)) return { ok: false, reason: 'step ' + (i + 1) + ' grants on a system the task did not name' };
        if (!this._has(this.GRANT_LEVELS, p.level)) return { ok: false, reason: 'step ' + (i + 1) + ' grants a level that is not allowed' };
        if (p.expires_minutes !== undefined) {
          var n = parseInt(p.expires_minutes, 10);
          if (!(n >= 1 && n <= this.MAX_EXPIRES_MINUTES)) return { ok: false, reason: 'step ' + (i + 1) + ' has an expiry outside 1 to ' + this.MAX_EXPIRES_MINUTES + ' minutes' };
        }
      } else if (st.command === 'provy-iam-revoke' || st.command === 'provy-iam-apply-change') {
        var sys = p.systems || (p.system ? [p.system] : []);
        if (!sys.length) return { ok: false, reason: 'step ' + (i + 1) + ' names no systems' };
        for (var k = 0; k < sys.length; k++) {
          if (!this._has(this.SYSTEMS, sys[k])) return { ok: false, reason: 'step ' + (i + 1) + ' names an unknown system' };
          if (!this._has(allowedSystems, sys[k])) return { ok: false, reason: 'step ' + (i + 1) + ' names a system the task did not name' };
        }
      } else if (this._has(['provy-iam-disable-account', 'provy-iam-remove-groups'], st.command)) {
        if (!this._has(allowedSystems, 'directory')) return { ok: false, reason: 'step ' + (i + 1) + ' touches the directory, which the task did not name' };
      }
    }
    return { ok: true };
  },

  /** A deterministic stand-in for a model: the task's own plan. Used by the offline stub and as the fallback when a model's plan is refused. */
  stubPlan: function (command, params) { return this.taskPlan(command, params); },

  /** Extract a plan object from a model's text: the first {...} block that parses. Returns null when there is none. */
  parsePlan: function (text) {
    if (!text) return null;
    var s = String(text);
    var start = s.indexOf('{');
    while (start >= 0) {
      var depth = 0;
      for (var i = start; i < s.length; i++) {
        var c = s.charAt(i);
        if (c === '{') depth++;
        else if (c === '}') { depth--; if (depth === 0) { try { return JSON.parse(s.substring(start, i + 1)); } catch (e) { break; } } }
      }
      start = s.indexOf('{', start + 1);
    }
    return null;
  },

  /**
   * THE VERDICT. Deterministic, from the estate's own reading. `reading` is what ProvyIamEstate.read returns:
   * { account_active, groups_held, roles_held, estate: {sso, vpn, saas_token}, ... }.
   * For a leaver (every command but grant) a system is HELD when no access is left in it; for a grant it is held when the access now exists.
   * A missing field is never read as "ended": an unknown directory state is not held.
   */
  verdict: function (command, params, reading) {
    var r = reading || {};
    var systems = {};
    var names = (command === 'provy-iam-grant') ? [String((params && params.system) || 'saas_token')] : this.taskSystems(params || {});
    if (command === 'provy-iam-disable-account' || command === 'provy-iam-remove-groups') names = ['directory'];
    else if (!names.length) names = this.SYSTEMS.slice();
    var granting = command === 'provy-iam-grant';
    for (var i = 0; i < names.length; i++) {
      var n = names[i], held, detail;
      if (n === 'directory') {
        var known = r.account_active === true || r.account_active === false;
        var left = (r.account_active === true ? 1 : 0) + (Number(r.groups_held) || 0) + (Number(r.roles_held) || 0);
        held = known && left === 0;
        detail = !known ? 'the directory state is unknown' : (left === 0 ? 'the account is off and no group or role is held' : 'the account is ' + (r.account_active ? 'still active' : 'off') + ', ' + (Number(r.groups_held) || 0) + ' group(s) and ' + (Number(r.roles_held) || 0) + ' role(s) held');
      } else {
        var count = r.estate && r.estate[n] !== undefined ? Number(r.estate[n]) : NaN;
        if (isNaN(count)) { held = false; detail = 'the state of ' + n + ' is unknown'; }
        else if (granting) { held = count > 0; detail = held ? n + ' now holds the access' : n + ' does not hold the access that was granted'; }
        else { held = count === 0; detail = held ? n + ' holds no access' : n + ' still holds ' + count + ' access item(s)'; }
      }
      systems[n] = { held: held, detail: detail };
    }
    var all = true, bad = [];
    for (var k = 0; k < names.length; k++) { if (!systems[names[k]].held) { all = false; bad.push(names[k]); } }
    return { held: all, systems: systems, checked: names, summary: all ? 'every system checked is as asked' : 'not as asked in: ' + bad.join(', ') };
  },

  /**
   * The outcome Provy's contract is settled with, built ONLY from the verdict, which is computed from the estate's own reading. Returns null when it does not apply:
   * no correlation (no work item to settle), a grant, or a command that did not check every system (a directory-only change does not answer "off-boarded from all systems").
   * One boolean per system (`<system>_ended`), the count, `offboarded_all`, and a `reason_code` when it failed so Provy can tell kinds of failure apart.
   */
  outcomeBody: function (command, verdict, correlation, businessDate, occurredAt) {
    if (!correlation || command === 'provy-iam-grant' || !verdict || !verdict.checked || !verdict.systems) return null;
    if (verdict.checked.length !== this.SYSTEMS.length) return null;
    var signals = { offboarded_all: !!verdict.held, systems_checked: verdict.checked.length };
    var ended = 0, bad = [];
    for (var i = 0; i < verdict.checked.length; i++) {
      var n = verdict.checked[i];
      var ok = !!(verdict.systems[n] && verdict.systems[n].held);
      signals[n + '_ended'] = ok;
      if (ok) ended++; else bad.push(n);
    }
    signals.systems_ended = ended;
    if (bad.length) signals.reason_code = 'access_left_in_' + bad.join('_and_');
    return { entity_id: String(correlation), business_date: businessDate, label: verdict.held ? 'success' : 'fail', source: 'confirmed', occurred_at: occurredAt, signals: signals };
  },

  type: 'ProvyIamPlan'
};
