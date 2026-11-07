/**
 * Script include ProvyIamWorker (POC). Global scope, ES5.
 *
 * The outside system for Servicely's asynchronous queue. Each run it asks the queue for work, applies each task to the
 * estate, and posts the result back. It is the only thing here that talks to Servicely, over the controller documented in
 * ../servicely/QUEUE-API.md (POST <base>/controller/AsyncIntegration, actions dequeue and success or fail).
 *
 * Configuration lives in system properties on this instance, never in code:
 *   provy.iam.servicely.url    the Servicely base URL
 *   provy.iam.servicely.token  a System API token allowed on /controller/AsyncIntegration (PRIVATE, set by hand)
 *   provy.iam.queue            the queue name (the provider instance's connection string)
 *   provy.iam.subjects         comma separated commands to poll (defaults below)
 *   provy.iam.agentic          true runs each task through the agents (ProvyIamAgents: plan, validate, apply, verify, trace); false (the default) is the plain path below, unchanged
 * Unconfigured, a run does nothing and says so in the log.
 */
var ProvyIamWorker = Class.create();
ProvyIamWorker.prototype = {
  DEFAULT_SUBJECTS: 'provy-iam-disable-account,provy-iam-remove-groups,provy-iam-revoke,provy-iam-apply-change,provy-iam-grant',
  IDENTIFIER: 'provy-iam-pdi-worker',

  initialize: function () {
    this.base = (gs.getProperty('provy.iam.servicely.url') || '').replace(/\/+$/, '');
    this.token = gs.getProperty('provy.iam.servicely.token') || '';
    this.queue = gs.getProperty('provy.iam.queue') || '';
    this.subjects = (gs.getProperty('provy.iam.subjects') || this.DEFAULT_SUBJECTS).split(',');
    this.estate = new global.ProvyIamEstate();
  },

  _post: function (body) {
    var r = new sn_ws.RESTMessageV2();
    r.setEndpoint(this.base + '/controller/AsyncIntegration');
    r.setHttpMethod('POST');
    r.setRequestHeader('Authorization', 'Bearer ' + this.token);
    r.setRequestHeader('Content-Type', 'application/json');
    r.setRequestHeader('Accept', 'application/json');
    r.setRequestBody(JSON.stringify(body));
    r.setHttpTimeout(20000);
    var resp = r.execute();
    var text = resp.getBody() || '';
    var json = null;
    try { json = JSON.parse(text); } catch (e) { /* not json */ }
    return { code: resp.getStatusCode(), json: json, text: text };
  },

  /** The agents, or null when the flag is off or they cannot be made: the plain path below then runs exactly as it always has. */
  _agents: function () {
    try {
      var a = new global.ProvyIamAgents();
      return a.enabled() ? a : null;
    } catch (e) {
      gs.warn('provy_iam worker: the agents could not be loaded, using the plain path: ' + String(e).substring(0, 100));
      return null;
    }
  },

  /** One pass over every subject. Returns how many tasks were worked. Never throws: a failure is logged and the next run tries again. */
  run: function () {
    if (!this.base || !this.token || !this.queue) {
      gs.info('provy_iam worker: not configured (url, token and queue are needed), nothing polled');
      return 0;
    }
    var worked = 0;
    for (var i = 0; i < this.subjects.length; i++) {
      var subject = this.subjects[i].trim();
      if (!subject) continue;
      var got = this._post({ action: 'dequeue', queue: this.queue, subject: subject, identifier: this.IDENTIFIER, request_count: 5 });
      if (got.code === 503) { gs.info('provy_iam worker: queue busy for ' + subject + ', will retry next run'); continue; }
      if (got.code !== 200 || !got.json || !got.json.data) {
        gs.warn('provy_iam worker: dequeue for ' + subject + ' answered http ' + got.code);
        continue;
      }
      var items = got.json.data;
      for (var k = 0; k < items.length; k++) { this._work(subject, items[k]); worked++; }
    }
    return worked;
  },

  _work: function (subject, item) {
    var params = {};
    try {
      var pl = item.payload;
      params = (typeof pl === 'string' && (pl.charAt(0) === '{' || pl.charAt(0) === '[')) ? JSON.parse(pl) : (pl && typeof pl === 'object' ? pl : { raw: pl });
    } catch (e) { params = {}; }
    // A request for temporary access to END arrives through the leaver and mover flow with "Systems: temporary_access". Whichever action the access agent chose, it is the expiry sweep.
    if (params && params.systems && params.systems.length === 1 && String(params.systems[0]).toLowerCase() === 'temporary_access') { subject = 'provy-iam-expire-grants'; delete params.systems; }
    var result;
    var agents = this._agents();
    if (agents) {
      // the agentic path never throws and answers in the same shape; the verdict of the verifier goes to the trace, never into this reply
      result = agents.process(subject, params, item.id).result;
    } else {
      try { result = this.estate.apply(subject, params); }
      catch (e2) { result = { status: 'error', payload: { error: String(e2) } }; }
    }
    var reply = { reply_to: item.id, identifier: item.claimed_by || this.IDENTIFIER };
    if (result.status === 'ok') {
      reply.action = 'success'; reply.status = 'ok'; reply.payload = JSON.stringify(result.payload);
    } else {
      reply.action = 'fail'; reply.status = 'error'; reply.payload = JSON.stringify(result.payload); reply.error = (result.payload && result.payload.error) || 'failed';
    }
    var posted = this._post(reply);
    if (posted.code >= 400) gs.warn('provy_iam worker: reply for ' + item.id + ' answered http ' + posted.code);
  },

  type: 'ProvyIamWorker'
};
