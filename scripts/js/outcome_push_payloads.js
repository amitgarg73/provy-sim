// Runs servicenow/outcome_push.js, the SAME file the instance runs, over closed incident records read from the instance, and prints the outcome
// bodies it would push. It exists so a validation tenant can be settled by the rule's own logic without pointing the instance's single ingest key
// at it (that key belongs to ITSM Demo's fleet). Only the TRANSPORT is replaced: sn_ws here hands the body back instead of posting it, and the caller
// posts it to the throwaway fleet. Nothing is invented: every field is read from the record, and task_sla (not read here) reports no SLA targets.
//
//   node scripts/js/outcome_push_payloads.js < records.json   ->   [{number, payload}, ...]
const fs = require('fs'), vm = require('vm'), path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', '..', 'servicenow', 'outcome_push.js'), 'utf8');
const records = JSON.parse(fs.readFileSync(0, 'utf8'));
const out = [];
for (const rec of records) {
  const props = { 'provy.ingest.url': 'https://stub.invalid/api/ingest/outcome', 'provy.ingest.key': 'stub', 'provy.vercel.bypass': '' };
  let body = null;
  const gs = { getProperty: (n, d) => (n in props ? props[n] : d), setProperty: (n, v) => { props[n] = v; }, info() {}, warn() {}, error(m) { throw new Error('rule logged an error: ' + m); }, sleep() {} };
  class GlideRecord { constructor() { this.rows = []; this.i = -1; } addQuery() {} query() {} next() { return false; } }
  class GlideDateTime { constructor(v) { this.v = v || new Date().toISOString().slice(0, 19).replace('T', ' '); } getValue() { return this.v; } }
  class GlideDuration { constructor(v) { this.v = v; } getNumericValue() { return 0; } }
  const sn_ws = { RESTMessageV2: class {
    setEndpoint() {} setHttpMethod() {} setRequestHeader() {} setHttpTimeout() {}
    setRequestBody(b) { body = JSON.parse(b); }
    execute() { return { getStatusCode: () => 200, getBody: () => '{}', getErrorMessage: () => '' }; }
  } };
  const current = {
    number: rec.number, sys_id: rec.sys_id, close_code: rec.close_code || '', reopen_count: String(rec.reopen_count || 0),
    reassignment_count: String(rec.reassignment_count || 0), time_worked: rec.time_worked || '', priority: String(rec.priority || ''), category: rec.category || '',
    opened_at: rec.opened_at || '', correlation_display: rec.correlation_display || '',
    assignment_group: { getDisplayValue: () => rec.assignment_group_name || '' },
    getValue: f => (f === 'closed_at' ? (rec.closed_at || '') : ''), closed_at: rec.closed_at || '',
  };
  vm.runInContext(src, vm.createContext({ gs, GlideRecord, GlideDateTime, GlideDuration, sn_ws, current, previous: null, JSON, parseInt, Math }));
  out.push({ number: rec.number, payload: body });
}
console.log(JSON.stringify(out));
