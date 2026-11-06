// POC test door: runs one estate command directly, so the estate can be tested without Servicely. Same bearer token as readings.
(function process(request, response) {
  var E = new global.ProvyIamEstate();
  if (!E.authorized(request)) { response.setStatus(401); response.setBody({ error: 'unauthorized' }); return; }
  var b = request.body.data || {};
  response.setBody(E.apply(String(b.command || ''), b.params || {}));
})(request, response);
