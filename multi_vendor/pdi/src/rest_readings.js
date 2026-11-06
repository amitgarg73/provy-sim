(function process(request, response) {
  var E = new global.ProvyIamEstate();
  if (!E.authorized(request)) { response.setStatus(401); response.setBody({ error: 'unauthorized' }); return; }
  var key = request.queryParams.key ? String(request.queryParams.key) : '';
  if (!key) { response.setStatus(400); response.setBody({ error: 'key is required' }); return; }
  response.setBody(E.read(key));
})(request, response);
