// THE HINT WALK-THROUGH, FOLLOWED: every lesson, solved by doing exactly what Hint shows.
//
//   node tests/guide.mjs <studio.html> [--only A3,B2] [--shots <dir>] [--size 1440x900]
//
// For each lesson the page is loaded, the lesson opened, and the real Hint button pressed.
// Then, until the lesson passes, the harness reads `EDITOR.guideState()` -- the one step the
// guide shows: its gesture, the holes cut in the dimming, a drag's two ends, the text to
// type -- and makes that gesture with REAL input over the DevTools protocol: a left or a
// right click at the hole, a shift-drag between the two ends, keystrokes into the field.
// Before each press it asks the page what is under the point, so a hole the card covers,
// or a target that is not where the ring is, fails here rather than in a reader's hands.
// A lesson whose guide stalls -- the same step after three tries -- fails with that step.
// This is what keeps a guide, and the lesson text beside it, honest about the page.
//
// A lesson that runs on a compiled page (`page`) is followed on `<page>.html` beside the
// studio page.  Exit 1 on any failure, uncaught exception or console error; one JSON line
// per lesson on stdout, then a summary.  No npm: Node's own WebSocket speaks CDP.
import fs from 'fs';
import http from 'http';
import os from 'os';
import path from 'path';
import { spawn } from 'child_process';

const args = process.argv.slice(2);
const opt = (k) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : null; };
const PAGE = path.resolve(args[0] || 'out/tutorial/studio.html');
const ONLY = opt('--only') ? opt('--only').split(',') : null;
const SHOTS = opt('--shots');
const [W, H] = (opt('--size') || '1440x900').split('x').map(Number);
const CHROME = process.env.CHROME || [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium-browser', '/usr/bin/chromium',
].find(p => fs.existsSync(p));
if (!CHROME) { console.error('no Chrome found; set CHROME'); process.exit(2); }
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });

// -------- the page's directory over HTTP --------------------------------------------------
const DIR = path.dirname(PAGE);
const server = http.createServer((req, res) => {
  const u = decodeURIComponent(req.url.split('?')[0].split('#')[0]);
  // a page built by the site carries the comment layer, which asks who is signed in: nobody
  if (u.startsWith('/api/')) { res.writeHead(200, { 'content-type': 'application/json' }); return res.end('{"user":null}'); }
  const p = path.join(DIR, u);
  if (u === '/favicon.ico' || !fs.existsSync(p) || fs.statSync(p).isDirectory()) { res.writeHead(404); return res.end(); }
  res.writeHead(200, { 'content-type': p.endsWith('.html') ? 'text/html; charset=utf-8' : 'application/octet-stream' });
  fs.createReadStream(p).pipe(res);
});
await new Promise(r => server.listen(0, '127.0.0.1', r));
const BASE = `http://127.0.0.1:${server.address().port}/`;

// -------- Chrome ---------------------------------------------------------------------------
const getJSON = (u) => new Promise((res, rej) => http.get(u, r => { let s = ''; r.on('data', d => s += d); r.on('end', () => { try { res(JSON.parse(s)); } catch (e) { rej(e); } }); }).on('error', rej));
const prof = fs.mkdtempSync(path.join(os.tmpdir(), 'qccd-guide-'));
const port = 9200 + Math.floor(Math.random() * 600);
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${prof}`,
  '--disable-extensions', '--no-first-run', '--no-default-browser-check', `--window-size=${W},${H}`, 'about:blank'], { stdio: 'ignore' });
let tabs = null;
for (let i = 0; i < 150 && !tabs; i++) { try { tabs = await getJSON(`http://127.0.0.1:${port}/json/list`); } catch { await new Promise(r => setTimeout(r, 100)); } }
const ws = new WebSocket(tabs.find(t => t.type === 'page').webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener('open', r));
let seq = 0; const pend = {}; let errors = [];
ws.addEventListener('message', e => {
  const m = JSON.parse(e.data);
  if (m.id && pend[m.id]) { pend[m.id](m); delete pend[m.id]; }
  if (m.method === 'Runtime.exceptionThrown') errors.push('exception: ' + (m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text));
  if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') errors.push('console: ' + m.params.args.map(a => a.value || a.description).join(' '));
});
const send = (method, params = {}) => new Promise(r => { const id = ++seq; pend[id] = r; ws.send(JSON.stringify({ id, method, params })); });
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function evaluate(expr) {
  const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.result?.exceptionDetails) throw new Error('page: ' + (r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text));
  return r.result?.result?.value;
}
async function shot(name) {
  if (!SHOTS) return;
  const r = await send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(path.join(SHOTS, name + '.png'), Buffer.from(r.result.data, 'base64'));
}
const mouse = (type, x, y, o = {}) => send('Input.dispatchMouseEvent', { type, x, y, button: o.button || 'left',
  buttons: type === 'mouseReleased' ? 0 : (o.button === 'right' ? 2 : 1), clickCount: 1, modifiers: o.modifiers || 0 });
async function click(p, o = {}) {
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: p.x, y: p.y, buttons: 0, modifiers: o.modifiers || 0 });
  await mouse('mousePressed', p.x, p.y, o); await mouse('mouseReleased', p.x, p.y, o);
}
async function drag(a, b, shift) {
  const mod = shift ? 8 : 0;
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: a.x, y: a.y, buttons: 0, modifiers: mod });
  await mouse('mousePressed', a.x, a.y, { modifiers: mod });
  for (let i = 1; i <= 14; i++) {
    await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: a.x + (b.x - a.x) * i / 14, y: a.y + (b.y - a.y) * i / 14,
      button: 'left', buttons: 1, modifiers: mod });
  }
  await mouse('mouseReleased', b.x, b.y, { modifiers: mod });
}
async function open(url) {
  await send('Page.navigate', { url });
  for (let i = 0; i < 100; i++) {
    await sleep(150);
    try { if (await evaluate("typeof EDITOR !== 'undefined' && !!EDITOR.guideState && document.readyState === 'complete'")) return; } catch { /* loading */ }
  }
  throw new Error('the page did not load: ' + url);
}
await send('Page.enable'); await send('Runtime.enable');
await send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: false });

// -------- one lesson, by the guide alone ---------------------------------------------------
// what is under a point, as the page's own hit test sees it: inside the guide's layer means
// the press would land on the dimming or the card, not on the thing the ring is round
const under = (p) => evaluate(`(function(){ var e = document.elementFromPoint(${p.x}, ${p.y}); if (!e) return 'nothing';
  var g = e.closest ? e.closest('#gdRoot') : null; return g ? 'guide:' + (e.id || e.className || e.tagName) : (e.id || e.tagName.toLowerCase()); })()`);
async function follow(id, file) {
  errors = [];
  await open(BASE + file);
  // a clean start: no progress and no remembered snap from the lesson before
  await evaluate(`(function(){ try { localStorage.clear(); } catch (e) {} return 1; })()`);
  await open(BASE + file);
  const loaded = await evaluate(`EDITOR.lessonLoad(${JSON.stringify(id)})`);
  if (!loaded || loaded.ok === false) return { id, ok: false, why: 'the lesson did not load' };
  // the Learn tab, pressed as a reader presses it (the website's studio opens with its
  // panel dock folded, and the tab is what unfolds it)
  // -- and only when Learn is not already showing, since a tab pressed again folds its pane
  const shown = await evaluate(`(function(){ var b = document.getElementById('lnHint'); return !!b && b.getBoundingClientRect().width > 0; })()`);
  const tab = shown ? null : await evaluate(`(function(){ var b = document.getElementById('tabL'); var r = b && b.getBoundingClientRect();
    return r && r.width ? { x: r.left + r.width / 2, y: r.top + r.height / 2 } : null; })()`);
  if (tab) { await click(tab); await sleep(350); }
  const hint = await evaluate(`(function(){ var b = document.getElementById('lnHint'); if (!b) return { why: 'no Hint button' };
    if (b.disabled) return { why: 'no Hint to press: this step has no guide' };
    var r = b.getBoundingClientRect(); if (!r.width) return { why: 'the Hint button is not on the screen' };
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
  if (hint.why) return { id, ok: false, why: hint.why };
  await click(hint);
  await sleep(400);
  const trail = [];
  let last = '', same = 0;
  for (let n = 0; n < 90; n++) {
    const st = await evaluate('JSON.stringify(EDITOR.guideState())').then(JSON.parse);
    if (st.passed) { await shot(`${id}_done`); return { id, ok: true, steps: trail.length, trail }; }
    if (!st.active) return { id, ok: false, why: 'the guide closed before the lesson passed', trail };
    if (!st.say) { await sleep(300); continue; }
    const sig = JSON.stringify([st.stage, st.goal, st.say, st.range, st.from && [Math.round(st.from.x), Math.round(st.from.y)]]);
    same = sig === last ? same + 1 : 0; last = sig;
    if (same >= 3) { await shot(`${id}_stuck`); return { id, ok: false, why: 'stuck at: ' + st.say, step: st, trail }; }
    trail.push(`${st.stage}:${st.goal} ${st.gesture} ${st.say}`);
    await shot(`${id}_${String(trail.length).padStart(2, '0')}`);
    const p = st.from;
    if (st.next) {
      const nb = await evaluate(`(function(){ var b = document.getElementById('gdNext'); var r = b && b.getBoundingClientRect(); return r ? { x: r.left + r.width / 2, y: r.top + r.height / 2 } : null; })()`);
      if (!nb) return { id, ok: false, why: 'a step to read has no Next button', trail };
      await click(nb);
    } else if (!p) {
      return { id, ok: false, why: 'the guide shows no place to act: ' + st.say, trail };
    } else {
      // the press must land on the thing the ring is round: the page's own hit test says so,
      // and anything in the way -- the guide's card, a panel, a scroll edge -- says no
      if (!st.reachable) {
        await shot(`${id}_unreachable`);
        return { id, ok: false, why: `the ring for "${st.say}" is not pressable (under it: ${await under(p)})`, trail };
      }
      if (st.gesture === 'click') await click(p);
      else if (st.gesture === 'right') await click(p, { button: 'right' });
      else if (st.gesture === 'shiftdrag' || st.gesture === 'drag') await drag(p, st.to, st.gesture === 'shiftdrag');
      else if (st.gesture === 'type') {
        await click(p);
        if (st.range) await evaluate(`(function(){ var t = document.querySelector(${JSON.stringify(st.el || '#pwText')}); t.focus(); t.setSelectionRange(${st.range[0]}, ${st.range[1]}); return 1; })()`);
        else await evaluate(`(function(){ var t = document.querySelector(${JSON.stringify(st.el)}); t.focus(); t.select(); return 1; })()`);
        await send('Input.insertText', { text: st.range ? st.text : String(st.fill) });
      } else if (st.gesture === 'pick') {
        await click(p);
        await evaluate(`(function(){ var s = document.querySelector(${JSON.stringify(st.el)}); s.value = ${JSON.stringify(st.pick)};
          s.dispatchEvent(new Event('change', { bubbles: true })); return 1; })()`);
      } else return { id, ok: false, why: 'a gesture this harness cannot make: ' + st.gesture, trail };
    }
    await sleep(st.gesture === 'type' ? 450 : 650);
  }
  return { id, ok: false, why: 'more than 90 steps', trail };
}

const results = [];
try {
  await open(BASE + path.basename(PAGE));
  const lessons = await evaluate('EDITOR.lessonList().map(function (l) { return { id: l.id, page: l.page || null }; })');
  for (const les of lessons) {
    if (ONLY && !ONLY.includes(les.id)) continue;
    let file = path.basename(PAGE);
    if (les.page) {
      if (!fs.existsSync(path.join(DIR, les.page + '.html'))) { results.push({ id: les.id, ok: false, why: `its page ${les.page}.html is not beside the studio page` }); continue; }
      file = les.page + '.html';
    }
    let r;
    try { r = await follow(les.id, file); } catch (e) { r = { id: les.id, ok: false, why: String(e.message || e) }; }
    if (errors.length) { r.ok = false; r.errors = errors.slice(0, 5); }
    results.push(r);
    console.log(JSON.stringify({ id: r.id, ok: r.ok, steps: r.steps, why: r.why, errors: r.errors }));
  }
} finally {
  ws.close(); chrome.kill(); server.close();
  await sleep(300);
  try { fs.rmSync(prof, { recursive: true, force: true }); } catch { /* still held */ }
}
const failed = results.filter(r => !r.ok);
console.log(JSON.stringify({ lessons: results.length, failed: failed.length, ok: failed.length === 0 }));
process.exit(failed.length ? 1 : 0);
