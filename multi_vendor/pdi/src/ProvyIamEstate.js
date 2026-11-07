/**
 * Script include ProvyIamEstate (POC, simulated estate). Global scope, ES5.
 *
 * The estate is the customer's world: a directory (the PDI's real sys_user, groups and roles) plus three simulated
 * systems held as rows in u_provy_iam_access: sso (a live session), vpn (a token), saas_token (a token).
 * `apply(command, params)` does what a Servicely action asks. `read(person)` says what access is REALLY left.
 *
 * THE GAP THIS EXISTS TO SHOW: a revoke is ACCEPTED and can still leave access in place. The property
 * provy.iam.fault holds JSON such as {"vpn":"accept_not_apply"}. For a listed system the call answers ok and changes
 * nothing, like a directory that says disabled while a token still authenticates. Nothing errors.
 */
var ProvyIamEstate = Class.create();
ProvyIamEstate.prototype = {
  SYSTEMS: ['directory', 'sso', 'vpn', 'saas_token'],
  ESTATE: ['sso', 'vpn', 'saas_token'],

  initialize: function () {},

  /** Bearer check for the REST doors. The token is a private property, never in a script. */
  authorized: function (request) {
    var tok = gs.getProperty('provy.iam.read_token');
    var auth = request.getHeader('authorization') || '';
    return !!tok && auth === 'Bearer ' + tok;
  },

  /**
   * The fault map for one person. The property is JSON: top-level keys apply to everyone, and `persons` holds a map per person, so ONE batch can carry a different failure for each
   * request: {"vpn":"accept_not_apply","persons":{"provy.iam.b007":{"over_reach":["vpn"]}}}. Keys: <system>: accept_not_apply; over_reach: [systems also revoked for the person's bystander];
   * expiry_sweep: accept_not_apply; model_plan, guard and verifier are read by ProvyIamAgents.
   */
  _faults: function (person) {
    var all = {};
    try { all = JSON.parse(gs.getProperty('provy.iam.fault', '{}')) || {}; } catch (e) { return {}; }
    var out = {};
    for (var k in all) if (all.hasOwnProperty(k) && k !== 'persons') out[k] = all[k];
    var mine = (person && all.persons && all.persons[person]) || {};
    for (var m in mine) if (mine.hasOwnProperty(m)) out[m] = mine[m];
    return out;
  },
  /** The bystander a person's request could wrongly touch: a second simulated person named after the first, so a reading can count changes to people the task did not name. */
  bystanderOf: function (person) { return 'provy.iam.colleague.' + String(person || '').substring(10); },
  _user: function (person) {
    var u = new GlideRecord('sys_user');
    u.addQuery('user_name', person);
    u.query();
    return u.next() ? u : null;
  },
  /** How many times the time-limited-access sweep has run for a person (one property holds a map). The sweep can do nothing on purpose, so a reading that must change when it ran needs this. */
  _sweeps: function (person) {
    try { return Number((JSON.parse(gs.getProperty('provy.iam.sweeps') || '{}'))[person] || 0); } catch (e) { return 0; }
  },
  _bumpSweep: function (person) {
    var m = {};
    try { m = JSON.parse(gs.getProperty('provy.iam.sweeps') || '{}'); } catch (e) { m = {}; }
    m[person] = Number(m[person] || 0) + 1;
    gs.setProperty('provy.iam.sweeps', JSON.stringify(m));
  },

  _rows: function (person, system) {
    var g = new GlideRecord('u_provy_iam_access');
    g.addQuery('u_person', person);
    if (system) g.addQuery('u_system', system);
    g.query();
    return g;
  },
  _count: function (table, field, value, extra) {
    var a = new GlideAggregate(table);
    a.addQuery(field, value);
    if (extra) a.addEncodedQuery(extra);
    a.addAggregate('COUNT');
    a.query();
    return a.next() ? parseInt(a.getAggregate('COUNT'), 10) : 0;
  },

  /** What access is left for a person, read from the systems themselves. A grant past its expiry still counts until the row says otherwise. */
  read: function (person) {
    var u = this._user(person);
    var out = { key: person, systems_checked: this.SYSTEMS.length, account_exists: !!u, estate: {} };
    var directory = 0, groups = 0, roles = 0;
    if (u) {
      out.account_active = u.getValue('active') == '1' || u.getValue('active') == 'true';
      groups = this._count('sys_user_grmember', 'user', u.getUniqueValue());
      roles = this._count('sys_user_has_role', 'user', u.getUniqueValue());
      directory = (out.account_active ? 1 : 0) + groups + roles;
      out.groups_held = groups;
      out.roles_held = roles;
    }
    var remaining = directory;
    for (var i = 0; i < this.ESTATE.length; i++) {
      var s = this.ESTATE[i];
      var n = this._count('u_provy_iam_access', 'u_person', person, 'u_system=' + s + '^u_state=active');
      out.estate[s] = n;
      remaining += n;
    }
    out.access_remaining = remaining;
    // people the task did not name: the bystander's revoked access rows and a disabled bystander account
    var by = this.bystanderOf(person);
    var bu = this._user(by);
    if (bu) {
      var changed = this._count('u_provy_iam_access', 'u_person', by, 'u_state=revoked');
      if (!(bu.getValue('active') == '1' || bu.getValue('active') == 'true')) changed += 1;
      out.others_changed = changed;
    }
    // time-limited grants still active past their expiry
    var now = new GlideDateTime().getValue();
    out.expired_grants_active = this._count('u_provy_iam_access', 'u_person', person, 'u_kind=grant^u_state=active^u_expires<' + "javascript:gs.dateGenerate('" + now.substring(0, 10) + "','" + now.substring(11) + "')");
    out.sweeps_run = this._sweeps(person);
    return out;
  },

  /** Run one command. Returns { status: 'ok'|'error', payload: {...} }. A fault makes it answer ok and do nothing. */
  apply: function (command, p) {
    p = p || {};
    var person = p.person;
    if (!person) return { status: 'error', payload: { error: 'person is required' } };
    var faults = this._faults(person);
    var done = [], skipped = [];
    var self = this;

    function disableDirectory() {
      if (faults.directory === 'accept_not_apply') { skipped.push('directory'); return; }
      var u = self._user(person);
      if (u) { u.setValue('active', false); u.update(); }
      done.push('directory');
    }
    function removeGroups() {
      var u = self._user(person);
      if (!u) return;
      var g = new GlideRecord('sys_user_grmember');
      g.addQuery('user', u.getUniqueValue());
      g.query();
      while (g.next()) g.deleteRecord();
    }
    function revoke(system) {
      if (faults[system] === 'accept_not_apply') { skipped.push(system); return; }
      var rows = self._rows(person, system);
      while (rows.next()) { rows.setValue('u_state', 'revoked'); rows.update(); }
      done.push(system);
    }

    function overReach() {
      var systems = faults.over_reach || [];
      if (!systems.length) return;
      var by = self.bystanderOf(person);
      for (var q = 0; q < systems.length; q++) {
        if (systems[q] === 'directory') { var bu = self._user(by); if (bu) { bu.setValue('active', false); bu.update(); } }
        else { var rs = self._rows(by, systems[q]); while (rs.next()) { rs.setValue('u_state', 'revoked'); rs.update(); } }
      }
    }

    if (command === 'provy-iam-expire-grants') {
      // the time-limited-access sweep: revoke every active grant past its expiry, unless the scenario says the sweep does nothing
      var gr = new GlideRecord('u_provy_iam_access');
      gr.addQuery('u_person', person); gr.addQuery('u_kind', 'grant'); gr.addQuery('u_state', 'active');
      gr.query();
      var now2 = new GlideDateTime().getValue();
      while (gr.next()) {
        var ex = gr.getValue('u_expires');
        if (!ex || ex >= now2) continue;
        if (faults.expiry_sweep === 'accept_not_apply') { skipped.push(gr.getValue('u_system')); continue; }
        gr.setValue('u_state', 'revoked'); gr.update(); done.push(gr.getValue('u_system'));
      }
      this._bumpSweep(person);
    }
    else if (command === 'provy-iam-disable-account') { disableDirectory(); removeGroups(); }
    else if (command === 'provy-iam-remove-groups') { removeGroups(); }
    else if (command === 'provy-iam-revoke') {
      var sys = p.systems || (p.system ? [p.system] : []);
      for (var i = 0; i < sys.length; i++) { if (sys[i] === 'directory') disableDirectory(); else revoke(sys[i]); }
    }
    else if (command === 'provy-iam-apply-change') {
      var list = p.systems || [];
      for (var j = 0; j < list.length; j++) {
        if (list[j] === 'directory') { disableDirectory(); removeGroups(); } else revoke(list[j]);
      }
    }
    else if (command === 'provy-iam-grant') {
      var g2 = new GlideRecord('u_provy_iam_access');
      g2.initialize();
      g2.setValue('u_person', person);
      g2.setValue('u_system', p.system || 'saas_token');
      g2.setValue('u_kind', 'grant');
      g2.setValue('u_state', 'active');
      g2.setValue('u_level', p.level || 'read');
      if (p.expires_minutes) { var d = new GlideDateTime(); d.addSeconds(parseInt(p.expires_minutes, 10) * 60); g2.setValue('u_expires', d.getValue()); }
      g2.setValue('u_simulated', true);
      g2.insert();
      done.push(g2.getValue('u_system'));
    }
    else return { status: 'error', payload: { error: 'unknown command ' + command } };

    overReach();
    // The honest answer a tool gives: what it was asked to do and did. It cannot see what it skipped.
    return { status: 'ok', payload: { person: person, command: command, applied: done.concat(skipped) } };
  },

  type: 'ProvyIamEstate'
};
