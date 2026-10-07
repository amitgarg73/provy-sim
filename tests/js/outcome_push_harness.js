// Runs servicenow/outcome_push.js against a fake ServiceNow (gs, GlideRecord, GlideDateTime, GlideDuration, sn_ws)
// and prints what it did as JSON. Driven by tests/test_outcome_push_rule.py. No network, no instance.
const fs = require('fs'), vm = require('vm'), path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', '..', 'servicenow', 'outcome_push.js'), 'utf8');
const scenario = JSON.parse(process.argv[2]);

function run(sc) {
  const props = Object.assign({'provy.ingest.url': 'https://dev.provy.ai/api/ingest/outcome', 'provy.ingest.key': 'k', 'provy.vercel.bypass': ''}, sc.props || {});
  const log = [], sent = [], sleeps = [];
  const answers = (sc.answers || []).slice();
  const gs = {
    getProperty: (n, d) => (n in props ? props[n] : d),
    setProperty: (n, v) => { props[n] = v; },
    info: m => log.push(['info', m]), warn: m => log.push(['warn', m]), error: m => log.push(['error', m]),
    sleep: ms => sleeps.push(ms),
  };
  const mk = (rec) => ({
    number: rec.number, sys_id: rec.number + '-id', close_code: rec.close_code || 'Solution provided', reopen_count: String(rec.reopen || 0),
    reassignment_count: String(rec.reassign == null ? 1 : rec.reassign), time_worked: '', priority: '3', category: 'network', opened_at: rec.opened_at || '2026-09-29 13:32:20',
    correlation_display: 'cat=network;grp=Network', category_: 'network',
    assignment_group: { getDisplayValue: () => 'Network' },
    getValue: f => (f === 'closed_at' ? (rec.closed_at === undefined ? '2026-09-29 13:41:24' : rec.closed_at) : ''),
    closed_at: rec.closed_at === undefined ? '2026-09-29 13:41:24' : rec.closed_at,
  });
  const incidents = (sc.incidents || {});
  class GlideRecord {
    constructor(t) { this.t = t; this.q = {}; this.rows = []; this.i = -1; }
    addQuery(k, v) { this.q[k] = v; }
    query() {
      if (this.t === 'task_sla') this.rows = (sc.sla || [{ target: 'response', breached: false }, { target: 'resolution', breached: false }]).map(s => ({ has_breached: String(s.breached), sla: { target: s.target } }));
      else if (this.t === 'incident') { const r = incidents[this.q.number]; this.rows = r && String(r.state || '7') === String(this.q.state) ? [mk(r)] : []; }
    }
    next() { this.i++; if (this.i < this.rows.length) { Object.assign(this, this.rows[this.i]); return true; } return false; }
  }
  class GlideDateTime { constructor(v) { this.v = v || '2026-10-07 12:00:00'; } getValue() { return this.v; } }
  class GlideDuration { constructor(v) { this.v = v; } getNumericValue() { return 0; } }
  const sn_ws = { RESTMessageV2: class {
    constructor() { this.body = null; }
    setEndpoint() {} setHttpMethod() {} setRequestHeader() {} setHttpTimeout() {}
    setRequestBody(b) { this.body = JSON.parse(b); }
    execute() {
      sent.push(this.body.entity_id);
      const a = answers.length ? answers.shift() : { status: 200, body: '{"reconciled":1}' };
      if (a.throws) throw new Error(a.throws);
      sent.bodies = (sent.bodies || []); sent.bodies.push(this.body);
      return { getStatusCode: () => a.status, getBody: () => a.body == null ? '' : a.body, getErrorMessage: () => a.error || '' };
    }
  } };
  const current = mk(sc.current);
  const sandbox = { gs, GlideRecord, GlideDateTime, GlideDuration, sn_ws, current, previous: null, JSON, parseInt, Math };
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox);
  return { props, log, sent: sent.slice(), bodies: sent.bodies || [], sleeps };
}
console.log(JSON.stringify(run(scenario)));
