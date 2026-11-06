// POC test door: runs one task through the AGENTIC path (plan, validate, apply, verify, trace), so it can be tested without Servicely. Same bearer token as readings.
// Answers 409 while provy.iam.agentic is not true, so the plain path stays the only one until it is switched on on purpose.
(function process(request, response) {
  var E = new global.ProvyIamEstate();
  if (!E.authorized(request)) { response.setStatus(401); response.setBody({ error: 'unauthorized' }); return; }
  var A = new global.ProvyIamAgents();
  if (!A.enabled()) { response.setStatus(409); response.setBody({ error: 'the agentic path is off (provy.iam.agentic is not true)' }); return; }
  var b = request.body.data || {};
  response.setBody(A.process(String(b.command || ''), b.params || {}, b.queue_item || null));
})(request, response);
