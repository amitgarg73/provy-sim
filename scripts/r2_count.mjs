// Count the R2 objects under traces/<tenant>/ for each tenant id given, READ-ONLY. The bucket is shared with production, so only a
// tenant-prefixed listing is ever made, never the bucket root and never another tenant's prefix.
//   node scripts/r2_count.mjs <tenantId> [<tenantId> ...]          (run from anywhere; ARGUS_REPO names the argus checkout)
// R2 credentials come from the environment (run it under argus/scripts/with-secrets R2_ACCOUNT_ID R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_BUCKET).
import path from 'node:path';
const ARGUS = process.env.ARGUS_REPO || path.join(process.env.HOME, 'Claude Projects/argus');
const { listAll } = await import(path.join(ARGUS, 'scripts/r2.mjs'));
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const out = {};
for (const id of process.argv.slice(2)) {
  if (!UUID.test(id)) { console.error(`skip ${id}: not a tenant id`); continue; }
  out[id] = (await listAll(`traces/${id}/`)).length;
}
console.log(JSON.stringify(out));
