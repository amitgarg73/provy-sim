// Runs tests/pdi_vectors.json against the REAL pdi/src/ProvyIamPlan.js in node, so the file that is deployed to the instance is the file that is tested.
// Usage: node tests/js/run_plan_vectors.js  -> prints a JSON array of {name, ok, got} for each vector.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const root = path.resolve(__dirname, '..', '..');
const sandbox = { Class: { create: () => function () { this.initialize && this.initialize.apply(this, arguments); } }, console };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(root, 'multi_vendor', 'pdi', 'src', 'ProvyIamPlan.js'), 'utf8') + '\nthis.ProvyIamPlan = ProvyIamPlan;', sandbox);
const P = new sandbox.ProvyIamPlan();
const vectors = JSON.parse(fs.readFileSync(path.join(root, 'tests', 'pdi_vectors.json'), 'utf8'));
function subset(expect, got) {
  for (const k of Object.keys(expect)) {
    if (k === 'systems') { for (const s of Object.keys(expect.systems)) if (!got.systems[s] || got.systems[s].held !== expect.systems[s]) return false; continue; }
    if (JSON.stringify(got[k]) !== JSON.stringify(expect[k])) return false;
  }
  return true;
}
const out = vectors.map(v => {
  let got, ok;
  if (v.kind === 'validate') { got = P.validate(v.plan, v.task); ok = subset(v.expect, got); }
  else if (v.kind === 'taskPlan') { got = P.taskPlan(v.command, v.params); ok = JSON.stringify(got) === JSON.stringify(v.expect); }
  else if (v.kind === 'verdict') { got = P.verdict(v.command, v.params, v.reading); ok = subset(v.expect, got); }
  else if (v.kind === 'parsePlan') { got = P.parsePlan(v.text); ok = JSON.stringify(got) === JSON.stringify(v.expect); }
  else { got = null; ok = false; }
  return { name: v.name, kind: v.kind, ok, got };
});
console.log(JSON.stringify(out));
