/**
 * Render every slide's key moments to PNG (1920x1080) and report any page errors.
 *   node tools/snap_deck.mjs <outDir> [s01,s05,...]
 * Key moments come from deck/script.js, so the stills match the review page exactly.
 */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL, fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DECK = path.resolve(HERE, '..', 'deck');
const out = process.argv[2];
const only = process.argv[3] ? process.argv[3].split(',') : null;
fs.mkdirSync(out, { recursive: true });

const src = fs.readFileSync(path.join(DECK, 'script.js'), 'utf8');
const SCENES = new Function('window', src + '; return window.SCENES;')({});
const { chromium } = createRequire('H:/cbeyond/portal-screenshots/package.json')('playwright');
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
const errors = [];
page.on('pageerror', e => errors.push(`${page.url().split('/').pop()}: ${e.message}`));
page.on('console', m => { if (m.type() === 'error') errors.push(`${page.url().split('/').pop()}: ${m.text()}`); });

for (const s of SCENES) {
  if (only && !only.includes(s.id)) continue;
  for (const k of s.keys) {
    const url = pathToFileURL(path.join(DECK, `${s.id}.html`)).href + `?t=${k}`;
    await page.goto(url, { waitUntil: 'networkidle' });
    await page.waitForFunction(() => document.documentElement.dataset.ready === '1');
    await page.evaluate(() => Promise.all([...document.images].map(i => i.complete ? 0 : new Promise(r => i.onload = i.onerror = r))));
    const f = path.join(out, `${s.id}_${k}.png`);
    await page.screenshot({ path: f });
    console.log('  ' + path.basename(f));
  }
}
await browser.close();
console.log(errors.length ? `\n${errors.length} PAGE ERROR(S):\n  ` + errors.join('\n  ') : '\nno page errors');
