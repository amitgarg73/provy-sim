/**
 * Script include ProvyIamTrace (POC). Global scope, ES5. Reports what the PDI's agents did to Provy, as a session with a step per action, so the work shows up as its own
 * workflow and, through `parent_external_id`, as the next hop of a chain that started in another system.
 *
 * Configuration lives in system properties:
 *   provy.iam.trace.url            Provy's base URL, no path (for example the dev deployment)
 *   provy.iam.trace.key            the workflow's ingest key (PRIVATE, set by hand)
 *   provy.iam.trace.mode           live | log | off. Unset means live when url and key are set, otherwise off.
 *                                  `log` writes what WOULD be sent to the system log (never the key): the way to check the shapes with no key.
 *
 * ⛔ NEVER THROWS INTO THE WORKER. A tracing failure is logged and the task goes on: a missed trace is a gap in a picture, never a failed access change.
 * ⛔ NEVER SENDS A SECRET OR A PROMPT: steps carry names, systems, counts and verdicts.
 * Doors used (all `x-provy-key`): POST /api/ingest/session/open, POST /api/ingest/trace (an array of steps), POST /api/ingest/session/close.
 */
var ProvyIamTrace = Class.create();
ProvyIamTrace.prototype = {
  initialize: function () {
    this.url = String(gs.getProperty('provy.iam.trace.url') || '').replace(/\/+$/, '');
    this.key = gs.getProperty('provy.iam.trace.key') || '';
    var m = String(gs.getProperty('provy.iam.trace.mode') || '').toLowerCase();
    this.mode = m === 'log' ? 'log' : (m === 'off' ? 'off' : ((this.url && this.key) ? 'live' : 'off'));
    this.sid = null;
    this.steps = [];
    this.sent = [];
    this.t0 = new Date().getTime();
    this.n = 0;
  },

  enabled: function () { return this.mode === 'live' || this.mode === 'log'; },

  _send: function (path, body) {
    try {
      if (this.mode === 'log') {
        this.sent.push({ path: path, body: body });
        gs.info('provy_iam trace [log] ' + path + ' ' + JSON.stringify(body).substring(0, 1500));
        return true;
      }
      var r = new sn_ws.RESTMessageV2();
      r.setEndpoint(this.url + path);
      r.setHttpMethod('POST');
      r.setRequestHeader('x-provy-key', this.key);
      r.setRequestHeader('Content-Type', 'application/json');
      r.setRequestBody(JSON.stringify(body));
      r.setHttpTimeout(15000);
      var code = r.execute().getStatusCode();
      if (code >= 300) { gs.warn('provy_iam trace: ' + path + ' answered http ' + code); return false; }
      this.sent.push({ path: path, ok: true });
      return true;
    } catch (e) {
      gs.warn('provy_iam trace: ' + path + ' failed: ' + String(e).substring(0, 100));
      return false;
    }
  },

  /**
   * Open the session. `parentSession` is an OPAQUE string: the asking system's own external id of the session this one continues, exactly as that system's reporter names it.
   * It is passed through UNCHANGED as parent_external_id, and NO parent is sent when it is absent. Nothing is derived here: this instance does not know how another system names sessions.
   */
  open: function (externalId, sessionType, parentSession, meta) {
    if (!this.enabled()) { gs.info('provy_iam trace: not configured (url and key, or mode log), nothing is reported'); return false; }
    this.sid = String(externalId);
    // the CORRELATION id (the root request that started the whole flow) travels beside every step as its record id, so a reader that joins on shared record ids can propose the link by itself
    this.correlation = meta && meta.correlation ? String(meta.correlation) : null;
    var body = { session_type: sessionType, external_id: this.sid, started_at: new GlideDateTime().getValue().replace(' ', 'T') + 'Z', metadata: meta || {} };
    if (parentSession) body.parent_external_id = String(parentSession);
    return this._send('/api/ingest/session/open', body);
  },

  /** One step. Buffered and sent together at close, so a slow door cannot slow the work. */
  step: function (agent, stepType, outcome, payload, extra) {
    if (!this.sid) return;
    this.n += 1;
    var s = { session_id: this.sid, agent: agent, step_type: stepType, outcome: outcome, payload: payload || {}, span_id: this.sid + '-' + this.n };
    if (this.correlation) s.entity_id = this.correlation;
    var x = extra || {};
    for (var k in x) if (x.hasOwnProperty(k)) s[k] = x[k];
    this.steps.push(s);
  },

  close: function (summary, reason) {
    if (!this.sid) return false;
    if (this.steps.length) this._send('/api/ingest/trace', this.steps);
    return this._send('/api/ingest/session/close', { session_id: this.sid, result_summary: summary, terminal_reason: reason, ended_at: new GlideDateTime().getValue().replace(' ', 'T') + 'Z' });
  },

  /**
   * Settle the work item: ONE post of the outcome body that ProvyIamPlan.outcomeBody builds from the estate's own reading (the verifier's verdict, computed, never modelled).
   * Door: POST /api/ingest/outcome with the workflow's key, keyed on entity_id (the correlation id). Sent after the session is closed. Same rules as every send here: never throws, never sends a secret.
   */
  outcome: function (body) {
    if (!body || !this.enabled()) return false;
    return this._send('/api/ingest/outcome', body);
  },

  type: 'ProvyIamTrace'
};
