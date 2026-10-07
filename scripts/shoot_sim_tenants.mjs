// One screenshot per tenant purpose, from the local server (argus checkout at provydev, pre-prod database), signed in as the tenant's own owner login.
//   node scripts/shoot_sim_tenants.mjs --creds <0600 file> --out docs/sim-evidence [--only H1,H2] [--base http://localhost:3100]
//
// ⛔ The password is read from the credentials file and typed by the browser. It is never printed, never written to a screenshot name, and the file is
// never copied. Each screenshot carries the tenant key and the screen in its file name; the page text of the screen is saved beside it (.txt) so a test can
// read what the page said without reading pixels. Headless Chromium of its own, from argus's playwright (read only: nothing here writes to the product).
import { createRequire } from 'node:module';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import path from 'node:path';

const HOME = process.env.HOME;
const ARGUS = process.env.ARGUS_REPO || path.join(HOME, 'Claude Projects/argus');
const require = createRequire(path.join(ARGUS, 'web', 'package.json'));
const { chromium } = require('playwright');
const arg = n => { const i = process.argv.indexOf(`--${n}`); return i >= 0 ? process.argv[i + 1] : undefined; };
const BASE = (arg('base') || 'http://localhost:3100').replace(/\/$/, '');
if (!/^http:\/\/localhost:\d+$/.test(BASE) && !/^https:\/\/dev\.provy\.ai$/.test(BASE)) { console.error('REFUSED: not a pre-prod host'); process.exit(2); }
const out = arg('out') || 'docs/sim-evidence';
mkdirSync(out, { recursive: true });
const creds = JSON.parse(readFileSync(arg('creds'), 'utf8'));
const only = (arg('only') || '').split(',').filter(Boolean);

// What each tenant is shown at. The route is a screen of the customer's own workspace; `wait` is for the page's own reads.
const SHOTS = {
  H1: [['readiness', '/fleets', 'ingestion panel: ready, six of six', { click: 'Manage', selector: '#readiness-slot' }], ['usage', '/settings/usage', 'Growth fleet plan and notice recipient']],
  H2: [['readiness', '/fleets', 'ingestion panel: thin, steps not counted', { click: 'Manage', selector: '#readiness-slot' }], ['usage', '/settings/usage', 'Enterprise fleet plan, extra usage']],
  H3: [['readiness', '/fleets', 'ingestion panel: nothing sent, not a pass', { click: 'Manage', selector: '#readiness-slot' }], ['usage', '/settings/usage', 'pilot ending in 14 days']],
  H4: [['guardrails', '/evals', 'context checks with planted faults']],
  H5: [['guardrails', '/evals', 'instruction changes'], ['usage', '/settings/usage', 'pilot ending in 3 days']],
  H6: [['guardrails', '/evals', 'declared checks, one switched off']],
  H7: [['outcomes', '/outcomes', 'claims and calibration, two fleets']],
  H8: [['fleets', '/fleets', 'five fleets, one per door']],
  H9: [['agents', '/agents', 'roster with a retired and an unaccepted agent']],
  P1: [['usage', '/settings/usage', 'pilot ended, not converted']],
  P2: [['usage', '/settings/usage', 'pilot ended, converted']],
  P3: [['usage', '/settings/usage', 'pilot extended']],
};

const browser = await chromium.launch({ headless: true });
const results = [];
for (const [key, shots] of Object.entries(SHOTS)) {
  if (only.length && !only.includes(key)) continue;
  const t = creds.tenants[key];
  if (!t) { console.log(`${key}: no credentials, skipped`); continue; }
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await ctx.newPage();
  page.setDefaultTimeout(60000);
  try {
    await page.goto(`${BASE}/login`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(10000);                 // let the form hydrate before typing: typed earlier, the button never enables
    await page.locator('input[type=email]').pressSequentially(t.email, { delay: 8 });
    await page.locator('input[type=password]').pressSequentially(t.password, { delay: 8 });
    await page.waitForFunction(() => { const b = [...document.querySelectorAll('button')].find(x => /sign in/i.test(x.textContent || '')); return b && !b.disabled; }, null, { timeout: 120000 });
    await page.locator('button:has-text("Sign in")').click();
    await page.waitForURL(u => !/\/login/.test(u.pathname), { timeout: 90000 });
    for (const [name, route, what, opts = {}] of shots) {
      await page.goto(`${BASE}${route}`, { waitUntil: 'domcontentloaded', timeout: 120000 });
      await page.waitForTimeout(9000);
      if (opts.click) { await page.getByText(opts.click, { exact: false }).first().click(); await page.waitForTimeout(12000); }
      const file = `${out}/${key}-${name}`;
      const target = opts.selector ? page.locator(opts.selector).first() : null;
      if (target) { await target.scrollIntoViewIfNeeded(); await target.screenshot({ path: `${file}.png` }); } else await page.screenshot({ path: `${file}.png`, fullPage: true });
      const text = target ? await target.innerText() : await page.evaluate(() => (document.querySelector('main') || document.body).innerText);
      writeFileSync(`${file}.txt`, text);
      results.push({ tenant: key, screen: name, what, png: `${file}.png`, landed: new URL(page.url()).pathname, chars: text.length });
      console.log(`${key} ${name}: ${new URL(page.url()).pathname}, ${text.length} chars`);
    }
  } catch (e) { console.log(`${key}: ${String(e.message).slice(0, 160)}`); results.push({ tenant: key, error: String(e.message).slice(0, 160) }); }
  await ctx.close();
}
await browser.close();
writeFileSync(`${out}/index.json`, JSON.stringify(results, null, 1));
