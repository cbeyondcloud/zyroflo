/* Render each deck slide to an MP4 by stepping its deterministic clock.
 *
 * scene.js exposes window.seek(t); this walks t = 0 .. D at a fixed frame rate and
 * pipes every PNG straight into ffmpeg's stdin. Nothing large touches the disk --
 * a 1080p frame sequence for a five-minute video is tens of gigabytes, and we only
 * ever need the encoded clip.
 *
 * Each clip runs to D = (measured narration) + TAIL, so a cut never clips the voice.
 *
 *   node tools/render_frames.mjs                # all scenes
 *   node tools/render_frames.mjs s02 s05        # just these
 */
import { createRequire } from 'module';
import { spawn, execSync } from 'child_process';
import { pathToFileURL } from 'url';
import fs from 'fs';
import path from 'path';

const require = createRequire(import.meta.url);
// Playwright lives in a neighbouring install -- this repo has no node_modules.
const { chromium } = createRequire('H:/cbeyond/portal-screenshots/package.json')('playwright');

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname.slice(1)), '..');
const DECK = path.join(ROOT, 'deck');
const OUT = path.join(ROOT, 'dist', 'scenes');

const FPS = 30;
const TAIL = 0.85;          // matches render_voice.py / the TokenLean recipe

// imageio-ffmpeg ships a real ffmpeg binary inside the Python env -- no admin, no PATH.
const FFMPEG = execSync(
  'python -c "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())"',
).toString().trim();

function loadScenes() {
  const src = fs.readFileSync(path.join(DECK, 'script.js'), 'utf8');
  const window = {};
  new Function('window', src)(window);
  return window.SCENES;
}

function encodeClip(outPath) {
  const args = [
    '-y', '-f', 'image2pipe', '-framerate', String(FPS), '-i', '-',
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
    '-pix_fmt', 'yuv420p', '-r', String(FPS), outPath,
  ];
  const ff = spawn(FFMPEG, args, { stdio: ['pipe', 'ignore', 'pipe'] });
  let err = '';
  ff.stderr.on('data', d => { err += d.toString(); });
  ff.on('close', code => { if (code !== 0) console.error(err.slice(-2000)); });
  return ff;
}

const only = process.argv.slice(2).filter(a => /^s\d\d$/.test(a));
const scenes = loadScenes().filter(s => !only.length || only.includes(s.id));
const durations = JSON.parse(fs.readFileSync(path.join(ROOT, 'dist/voice/durations.json'), 'utf8'));

fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
const problems = [];
page.on('pageerror', e => problems.push(`${e}`));

const manifest = [];
for (const s of scenes) {
  const D = +(durations[s.id] + TAIL).toFixed(3);
  const nframes = Math.round(D * FPS);
  const url = pathToFileURL(path.join(DECK, `${s.id}.html`)).href;

  await page.goto(url, { waitUntil: 'networkidle' });
  await page.waitForFunction(() => document.documentElement.dataset.ready === '1');
  await page.evaluate(() => Promise.all(
    [...document.images].map(i => (i.complete ? 0 : new Promise(r => { i.onload = i.onerror = r; })))));

  // The slide is authored against its own timeline; the clip runs for as long as the
  // narration actually took. Map one onto the other so every beat lands at the same
  // FRACTION of the scene the author placed it at -- rendering 1:1 instead would leave
  // the animation unfinished when the voice stops (or frozen once it has).
  const authored = await page.evaluate(() => window.T.dur);
  const scale = authored / D;

  const outPath = path.join(OUT, `${s.id}.mp4`);
  const ff = encodeClip(outPath);
  const done = new Promise(res => ff.on('close', res));

  process.stdout.write(`  ${s.id}  ${D.toFixed(2)}s  ${nframes} frames `
    + (Math.abs(scale - 1) > 0.005 ? `(x${scale.toFixed(3)} from ${authored}s) ` : ''));
  for (let i = 0; i < nframes; i++) {
    await page.evaluate(t => window.seek(t), (i / FPS) * scale);
    const buf = await page.screenshot({ type: 'png' });
    if (!ff.stdin.write(buf)) await new Promise(r => ff.stdin.once('drain', r));
    if (i % 300 === 0) process.stdout.write('.');
  }
  ff.stdin.end();
  await done;
  console.log(` -> ${path.basename(outPath)}`);
  manifest.push({ id: s.id, voice: durations[s.id], tail: TAIL, D, nframes });
}

await browser.close();

// Merge, don't clobber: re-rendering ONE scene must not wipe the other six out of the
// manifest -- assemble.py reads this to lay out the whole timeline.
const manifestPath = path.join(ROOT, 'dist/scenes/clips.json');
const prior = fs.existsSync(manifestPath)
  ? JSON.parse(fs.readFileSync(manifestPath, 'utf8')).clips ?? []
  : [];
const byId = new Map(prior.map(c => [c.id, c]));
manifest.forEach(c => byId.set(c.id, c));
const order = loadScenes().map(s => s.id);
const merged = order.filter(id => byId.has(id)).map(id => byId.get(id));
fs.writeFileSync(manifestPath, JSON.stringify({ fps: FPS, tail: TAIL, clips: merged }, null, 2));

if (problems.length) {
  console.log('\nPAGE ERRORS:');
  problems.forEach(p => console.log('  ' + p));
  process.exit(1);
}
console.log('\nno page errors');
