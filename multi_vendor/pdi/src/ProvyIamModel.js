/**
 * Script include ProvyIamModel (POC). Global scope, ES5. THE ONE PLACE A MODEL IS CALLED FROM, with a provider-neutral shape: complete(prompt, schema, options) -> { ok, mode, text, error }.
 *
 * Configuration lives in system properties, never in code:
 *   provy.iam.model.mode   stub (default) or live. Stub never touches the network and returns options.stub, so everything is testable with no key.
 *   provy.iam.model.url    the chat-completions URL of any OpenAI-compatible provider (live only)
 *   provy.iam.model.key    the provider key (PRIVATE, set by hand, never in a script)
 *   provy.iam.model.name   the model name (live only)
 * `live` with any of url, key or name missing behaves as stub and says so in the log, so a half-configured instance never calls out.
 *
 * ⛔ A MODEL ONLY PROPOSES (see ProvyIamPlan): nothing it returns is applied without the allow-list, and no model decides an outcome.
 * ⛔ NEVER LOGS A KEY OR A PROMPT. The log carries the mode, the schema name, a status code and lengths.
 */
var ProvyIamModel = Class.create();
ProvyIamModel.prototype = {
  initialize: function () {
    this.requested = String(gs.getProperty('provy.iam.model.mode') || 'stub').toLowerCase();
    this.url = gs.getProperty('provy.iam.model.url') || '';
    this.key = gs.getProperty('provy.iam.model.key') || '';
    this.name = gs.getProperty('provy.iam.model.name') || '';
    this.mode = (this.requested === 'live' && this.url && this.key && this.name) ? 'live' : 'stub';
    if (this.requested === 'live' && this.mode === 'stub') gs.warn('provy_iam model: mode is live but the url, key or name is not set, so the stub is used and nothing is sent');
  },

  /** The model name that goes into a trace step. */
  label: function () { return this.mode === 'live' ? this.name : 'stub'; },

  complete: function (prompt, schema, options) {
    var opt = options || {};
    if (this.mode !== 'live') return { ok: true, mode: 'stub', text: opt.stub !== undefined ? String(opt.stub) : '' };
    try {
      var r = new sn_ws.RESTMessageV2();
      r.setEndpoint(this.url);
      r.setHttpMethod('POST');
      r.setRequestHeader('Authorization', 'Bearer ' + this.key);
      r.setRequestHeader('Content-Type', 'application/json');
      r.setRequestHeader('Accept', 'application/json');
      r.setRequestBody(JSON.stringify({
        model: this.name, temperature: 0,
        messages: [{ role: 'system', content: opt.system || 'You are a careful assistant. Answer exactly as asked and nothing else.' }, { role: 'user', content: String(prompt) }]
      }));
      r.setHttpTimeout(25000);
      var resp = r.execute();
      var code = resp.getStatusCode();
      var body = resp.getBody() || '';
      if (code !== 200) { gs.warn('provy_iam model: ' + schema + ' answered http ' + code); return { ok: false, mode: 'live', text: '', error: 'http ' + code }; }
      var j = JSON.parse(body);
      var text = j && j.choices && j.choices[0] && j.choices[0].message ? String(j.choices[0].message.content || '') : '';
      gs.info('provy_iam model: ' + schema + ' answered, ' + text.length + ' chars');
      return { ok: true, mode: 'live', text: text };
    } catch (e) {
      gs.warn('provy_iam model: ' + schema + ' failed: ' + String(e).substring(0, 120));
      return { ok: false, mode: 'live', text: '', error: 'call failed' };
    }
  },

  type: 'ProvyIamModel'
};
