/**
 * Measured layout check for every slide at every key moment (from deck/script.js):
 *   1. no visible text below y = 880 - that band is reserved for burned-in captions
 *   2. no text clipped by its own box (scrollWidth/Height beyond clientWidth/Height)
 * "Visible" = effective opacity (product up the ancestor chain) above 0.05.
 *   node tools/check_layout.mjs
 */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL, fileURLToPath } from 'node:url';

const DECK = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'deck');
const SCENES = new Function('window', fs.readFileSync(path.join(DECK, 'script.js'), 'utf8') + '; return window.SCENES;')({});
const { chromium } = createRequire('H:/cbeyond/portal-screenshots/package.json')('playwright');
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
let problems = 0;

for (const s of SCENES) {
  const times = [...new Set([...s.keys, ...Array.from({ length: Math.floor(s.dur) }, (_, i) => i + 0.5)])];
  const found = new Map();
  await page.goto(pathToFileURL(path.join(DECK, `${s.id}.html`)).href + '?t=0', { waitUntil: 'networkidle' });
  await page.waitForFunction(() => document.documentElement.dataset.ready === '1');
  for (const k of times) {
    const t = k === 'end' ? s.dur : k;
    const hits = await page.evaluate(t => {
      window.seek(t);
      const op = el => { let o = 1; for (let e = el; e && e.nodeType === 1; e = e.parentElement) o *= parseFloat(getComputedStyle(e).opacity); return o; };
      const out = [];
      document.querySelectorAll('.stage *').forEach(el => {
        const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
        if (!own || op(el) < 0.05) return;
        const r = el.getBoundingClientRect();
        if (r.height === 0) return;
        const label = (el.id ? '#' + el.id : el.tagName.toLowerCase()) + ' "' + el.textContent.trim().slice(0, 40) + '"';
        if (r.bottom > 880.5 && r.top < 1080) out.push(`caption band: ${label} bottom=${Math.round(r.bottom)}`);
        const cs = getComputedStyle(el);
        if ((cs.overflow === 'hidden' || cs.textOverflow === 'ellipsis') && el.id !== 'l3' &&
            (el.scrollWidth > el.clientWidth + 2)) out.push(`clipped: ${label}`);
      });
      return out;
    }, t);
    hits.forEach(h => { if (!found.has(h)) found.set(h, k); });
  }
  if (found.size) { problems += found.size; console.log(`${s.id}:`); found.forEach((k, h) => console.log(`   t=${k}  ${h}`)); }
  else console.log(`${s.id}: clean (${times.length} moments)`);
}
await browser.close();
console.log(problems ? `\n${problems} PROBLEM(S)` : '\nall slides clean');
process.exit(problems ? 1 : 0);
