// POC test door: runs test vectors (tests/pdi_vectors.json in the sim repo) against the DEPLOYED ProvyIamPlan, so the copy on the instance is proven to behave like the tested files.
// No estate is read or changed. Same bearer token as readings.
(function process(request, response) {
  var E = new global.ProvyIamEstate();
  if (!E.authorized(request)) { response.setStatus(401); response.setBody({ error: 'unauthorized' }); return; }
  var P = new global.ProvyIamPlan();
  var vectors = (request.body.data || {}).vectors || [];
  function subset(expect, got) {
    for (var k in expect) {
      if (!expect.hasOwnProperty(k)) continue;
      if (k === 'systems') { for (var s in expect.systems) if (!got.systems[s] || got.systems[s].held !== expect.systems[s]) return false; continue; }
      if (JSON.stringify(got[k]) !== JSON.stringify(expect[k])) return false;
    }
    return true;
  }
  var out = [];
  for (var i = 0; i < vectors.length; i++) {
    var v = vectors[i], got, ok;
    if (v.kind === 'validate') { got = P.validate(v.plan, v.task); ok = subset(v.expect, got); }
    else if (v.kind === 'taskPlan') { got = P.taskPlan(v.command, v.params); ok = JSON.stringify(got) === JSON.stringify(v.expect); }
    else if (v.kind === 'verdict') { got = P.verdict(v.command, v.params, v.reading); ok = subset(v.expect, got); }
    else if (v.kind === 'parsePlan') { got = P.parsePlan(v.text); ok = JSON.stringify(got) === JSON.stringify(v.expect); }
    else { got = null; ok = false; }
    out.push(ok ? { name: v.name, kind: v.kind, ok: true } : { name: v.name, kind: v.kind, ok: false, got: got });
  }
  response.setBody({ results: out });
})(request, response);
