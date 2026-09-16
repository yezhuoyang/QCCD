// THE COMMENTS WALKTHROUGH: a reader signs in, pins a note to a paragraph, and the note
// stays on that paragraph through scrolling, a reload and a narrower window; an admin
// then removes it.
//
//   node tests/comments.mjs <site dir> <page.html> [--target <selector>] [--python <exe>]
//                           [--shots <dir>]
//
// The directory is served over HTTP with `/api/` proxied to a fresh comments API
// (`qccd/site/comments_api.py` on a temporary database, one admin made through its
// command line), and headless Chrome is driven over the DevTools protocol -- real mouse
// clicks for the placing, real typing for the text.  No npm.
//
// Exit 1 on any failed step, uncaught exception or console error.  One JSON line per step
// on stdout, then a summary line.
import fs from 'fs';
import http from 'http';
import os from 'os';
import path from 'path';
import { spawn } from 'child_process';
import { fileURLToPath } from 'url';

const args = process.argv.slice(2);
const site = path.resolve(args[0] || 'site');
const PAGE = args[1] || 'index.html';
const opt = (k) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : null; };
const TARGET = opt('--target') || 'main p';
const PYTHON = opt('--python') || process.env.PYTHON || 'python';
const SHOTS = opt('--shots');
const HERE = path.dirname(fileURLToPath(import.meta.url));
const API_PY = path.join(HERE, '..', 'qccd', 'site', 'comments_api.py');
const CHROME = process.env.CHROME || [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium-browser', '/usr/bin/chromium',
].find(p => fs.existsSync(p));
if (!CHROME) { console.error('no Chrome found; set CHROME'); process.exit(2); }

const ADMIN = { email: 'admin@example.org', password: 'admin-pass-123', name: 'Site Admin' };
const READER = { email: 'ada@example.org', password: 'reader-pass-123', name: 'Ada Reader' };
const NOTE = 'This sentence contradicts the rule above.';

// -------- the API on a temporary database --------------------------------------------
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'qccd-comments-'));
const db = path.join(tmp, 'comments.db');
const run = (a) => new Promise((res, rej) => { const p = spawn(PYTHON, a, { stdio: ['ignore', 'pipe', 'pipe'] });
  let out = '', err = ''; p.stdout.on('data', d => out += d); p.stderr.on('data', d => err += d);
  p.on('close', c => c === 0 ? res(out) : rej(new Error(err || out))); });
await run([API_PY, 'passwd', '--db', db, ADMIN.email, '--name', ADMIN.name, '--password', ADMIN.password]);
const apiPort = 8600 + Math.floor(Math.random() * 400);
const api = spawn(PYTHON, [API_PY, 'serve', '--db', db, '--port', String(apiPort), '--admins', ADMIN.email], { stdio: ['ignore', 'pipe', 'pipe'] });
let apiLog = ''; api.stdout.on('data', d => apiLog += d); api.stderr.on('data', d => apiLog += d);
const getJSON = (u) => new Promise((res, rej) => http.get(u, r => { let s = ''; r.on('data', d => s += d); r.on('end', () => { try { res(JSON.parse(s)); } catch (e) { rej(e); } }); }).on('error', rej));
let up = false;
for (let i = 0; i < 100 && !up; i++) { try { up = (await getJSON(`http://127.0.0.1:${apiPort}/api/health`)).ok; } catch { await new Promise(r => setTimeout(r, 100)); } }
if (!up) { console.error('the API did not start:\n' + apiLog); api.kill(); process.exit(2); }

// -------- a static server with /api/ proxied -----------------------------------------
const MIME = { '.html': 'text/html; charset=utf-8', '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml', '.css': 'text/css', '.js': 'text/javascript' };
const server = http.createServer((req, res) => {
  const u = decodeURIComponent(req.url.split('?')[0].split('#')[0]);
  if (u.startsWith('/api/')) {
    const p = http.request({ host: '127.0.0.1', port: apiPort, path: req.url, method: req.method, headers: req.headers }, r => { res.writeHead(r.statusCode, r.headers); r.pipe(res); });
    p.on('error', () => { res.writeHead(502); res.end('api down'); });
    req.pipe(p); return;
  }
  if (u === '/favicon.ico') { res.writeHead(204); return res.end(); }
  let p = path.join(site, u);
  if (u.endsWith('/')) p = path.join(p, 'index.html');
  if (!fs.existsSync(p) || fs.statSync(p).isDirectory()) { res.writeHead(404); return res.end('not found'); }
  res.writeHead(200, { 'content-type': MIME[path.extname(p)] || 'application/octet-stream' });
  fs.createReadStream(p).pipe(res);
});
await new Promise(r => server.listen(0, '127.0.0.1', r));
const BASE = `http://127.0.0.1:${server.address().port}/`;

// -------- Chrome over CDP -------------------------------------------------------------
const port = 9300 + Math.floor(Math.random() * 500);
const udd = fs.mkdtempSync(path.join(os.tmpdir(), 'qccd-comments-chrome-'));
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${udd}`,
  '--disable-extensions', '--no-first-run', '--no-default-browser-check', '--window-size=1440,900', '--hide-scrollbars', 'about:blank'], { stdio: 'ignore' });
let targets = null;
for (let i = 0; i < 100 && !targets; i++) { try { targets = await getJSON(`http://127.0.0.1:${port}/json/list`); } catch { await new Promise(r => setTimeout(r, 100)); } }
const tab = targets.find(t => t.type === 'page');
const ws = new WebSocket(tab.webSocketDebuggerUrl);
await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
let seq = 0; const pending = new Map(); const listeners = new Map();
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { const { res, rej } = pending.get(m.id); pending.delete(m.id); m.error ? rej(new Error(m.error.message)) : res(m.result); }
  else if (m.method && listeners.has(m.method)) listeners.get(m.method).forEach(f => f(m.params));
};
const send = (method, params = {}) => new Promise((res, rej) => { const id = ++seq; pending.set(id, { res, rej }); ws.send(JSON.stringify({ id, method, params })); });
const on = (method, f) => { if (!listeners.has(method)) listeners.set(method, []); listeners.get(method).push(f); };
await send('Page.enable'); await send('Runtime.enable'); await send('Log.enable');
const errors = [];
on('Runtime.exceptionThrown', p => errors.push('exception: ' + (p.exceptionDetails.exception?.description || p.exceptionDetails.text).split('\n')[0]));
on('Log.entryAdded', p => { if (p.entry.level === 'error') errors.push('console: ' + p.entry.text); });
on('Runtime.consoleAPICalled', p => { if (p.type === 'error') errors.push('console.error: ' + p.args.map(a => a.value || a.description).join(' ')); });
let loaded = null;
on('Page.loadEventFired', () => { if (loaded) loaded(); });
async function open(url) {
  const p = new Promise(r => { loaded = r; });
  await send('Page.navigate', { url });
  await Promise.race([p, new Promise(r => setTimeout(r, 30000))]);
  loaded = null;
  await new Promise(r => setTimeout(r, 300));
}
async function evaluate(expr) {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error('probe: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
  return r.result.value;
}
async function until(expr, what, ms = 8000) {
  const t0 = Date.now();
  for (;;) { const v = await evaluate(expr); if (v) return v; if (Date.now() - t0 > ms) throw new Error('timed out waiting for ' + what); await new Promise(r => setTimeout(r, 60)); }
}
async function click(x, y) {
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
}
async function type(text) { await send('Input.insertText', { text }); }
async function shot(name) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  const r = await send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(path.join(SHOTS, name + '.png'), Buffer.from(r.data, 'base64'));
}
const rectOf = (sel) => evaluate(`(function(){ var e = document.querySelector(${JSON.stringify(sel)}); if(!e) return null; var r = e.getBoundingClientRect(); return {l: r.left, t: r.top, w: r.width, h: r.height, r: r.right, b: r.bottom, sx: window.scrollX, sy: window.scrollY}; })()`);
const state = () => evaluate('window.QCCOMMENTS ? window.QCCOMMENTS.state() : null');

// -------- the story -------------------------------------------------------------------
const steps = [];
let failed = 0;
function step(name, ok, detail) {
  const rec = { step: name, ok: !!ok, ...(detail ? { detail } : {}) };
  if (!ok) failed++;
  steps.push(rec); console.log(JSON.stringify(rec));
}
const near = (a, b, eps = 2.5) => Math.abs(a - b) <= eps;
async function signIn(u, mode) {
  await until("!!document.getElementById('qc-signin')", 'the Sign in control');
  await evaluate("document.getElementById('qc-signin').click()");
  await until("!!document.getElementById('qc-auth')", 'the sign-in dialog');
  if (mode === 'register') { await evaluate("document.getElementById('qc-switch').click()"); await until("document.getElementById('qc-auth').getAttribute('data-mode') === 'register'", 'the create-account form'); }
  // real typing into each field
  if (mode === 'register') { await evaluate("document.getElementById('qc-name').focus()"); await type(u.name); }
  await evaluate("document.getElementById('qc-email').focus()"); await type(u.email);
  await evaluate("document.getElementById('qc-pass').focus()"); await type(u.password);
  await evaluate("document.getElementById('qc-submit').click()");
  await until("!!document.getElementById('qc-add')", 'the + Comment control after signing in');
}

try {
  const url = BASE + PAGE;
  await open(url);
  const s0 = await state();
  step('signed out: Sign in offered, nothing shown', await evaluate("!!document.getElementById('qc-signin')") && !(await evaluate("document.querySelectorAll('.qc-pin').length")) && s0 && s0.me === null);
  // asked from here, not from the page: Chrome logs a 401 as a console error
  const st401 = await new Promise(res => http.get(BASE + 'api/threads?page=/', r => { r.resume(); res(r.statusCode); }).on('error', () => res(null)));
  step('signed out: the API refuses the thread list', st401 === 401, { status: st401 });

  await signIn(READER, 'register');
  const s1 = await state();
  step('create account: signed in as the reader', s1 && s1.me && s1.me.name === READER.name && s1.me.admin === false && s1.threads.length === 0, { me: s1 && s1.me });

  // placing: press + Comment, click the middle of the target paragraph
  await evaluate("document.getElementById('qc-add').click()");
  step('placing mode: on, with the hint', (await state()).placing === true && await evaluate("!!document.getElementById('qc-hintbar') && document.body.classList.contains('qc-placing')"));
  const tr = await rectOf(TARGET);
  if (!tr) throw new Error('no target element for ' + TARGET);
  await click(tr.l + tr.w / 2, tr.t + tr.h / 2);
  await until("!!document.getElementById('qc-draft')", 'the draft box');
  step('a click opened a draft at the spot and left placing mode', (await state()).placing === false && await evaluate("document.activeElement && document.activeElement.id === 'qc-draft-text'"));
  await type(NOTE);
  await shot('01_draft');
  await evaluate("document.getElementById('qc-post').click()");
  await until("document.querySelectorAll('.qc-pin[data-id]').length === 1", 'the posted pin');
  const s2 = await state();
  const th = s2.threads[0];
  const same = await evaluate(`(function(){ var a = ${JSON.stringify(th.anchor)}; var e = document.querySelector(a.sel); return e === document.querySelector(${JSON.stringify(TARGET)}); })()`);
  step('posted: the anchor names the paragraph clicked, at its middle', same && near(th.anchor.ox, 0.5, 0.05) && near(th.anchor.oy, 0.5, 0.1) && th.n === 1, { anchor: th.anchor });
  const pin0 = await rectOf('.qc-pin[data-id]');
  step('the pin stands on that spot', near(pin0.l, tr.l + tr.w / 2) && near(pin0.b, tr.t + tr.h / 2), { pin: pin0, target: tr });
  const boxText = await evaluate("(function(){ var b = document.querySelector('.qc-box[data-id]'); return b ? b.textContent : ''; })()");
  step('the chat box shows the name and the note', boxText.includes(READER.name) && boxText.includes(NOTE));
  const colour = await evaluate("getComputedStyle(document.querySelector('.qc-box[data-id] .qc-head')).backgroundColor");
  step('the box is coloured', /^rgb\(/.test(colour) && colour !== 'rgba(0, 0, 0, 0)' && colour !== 'rgb(255, 255, 255)', { colour });
  await shot('02_posted');

  // scrolling: the pin moves with the page (it is in the document, not fixed to the viewport)
  const canScroll = await evaluate('document.documentElement.scrollHeight > window.innerHeight + 100');
  if (canScroll) {
    await evaluate('window.scrollTo(0, 250)'); await new Promise(r => setTimeout(r, 200)); await evaluate('window.QCCOMMENTS.place()');
    const pin1 = await rectOf('.qc-pin[data-id]');
    // the pin's viewport position drops by exactly what the window scrolled (a fixed box would not move)
    step('scrolled: the pin moved with the text, not with the window', pin1.sy > 100 && near(pin1.t, pin0.t - pin1.sy), { before: pin0.t, after: pin1.t, scrolled: pin1.sy });
    await evaluate('window.scrollTo(0, 0)');
  } else step('scrolled: page too short to scroll (skipped)', true);

  // reload: still signed in, the note is back on the same words
  await open(url);
  await until("document.querySelectorAll('.qc-pin[data-id]').length === 1", 'the pin after a reload');
  const tr2 = await rectOf(TARGET), pin2 = await rectOf('.qc-pin[data-id]');
  step('reloaded: signed in, the pin is back on the spot', (await state()).me.name === READER.name && near(pin2.l, tr2.l + tr2.w / 2) && near(pin2.b, tr2.t + tr2.h / 2), { pin: pin2, target: tr2 });

  // a narrower window reflows the paragraph; the pin follows it
  await send('Emulation.setDeviceMetricsOverride', { width: 720, height: 900, deviceScaleFactor: 1, mobile: false });
  await new Promise(r => setTimeout(r, 400)); await evaluate('window.QCCOMMENTS.place()');
  const tr3 = await rectOf(TARGET), pin3 = await rectOf('.qc-pin[data-id]');
  step('narrower window: the pin follows the reflowed paragraph', near(pin3.l, tr3.l + tr3.w / 2) && near(pin3.b, tr3.t + tr3.h / 2) && (tr3.h !== tr2.h || tr3.w !== tr2.w), { pin: pin3, target: tr3 });
  await shot('03_narrow');
  await send('Emulation.clearDeviceMetricsOverride');
  await new Promise(r => setTimeout(r, 200)); await evaluate('window.QCCOMMENTS.place()');

  // a reply, then deleting it
  await evaluate("var t = document.querySelector('.qc-box[data-id] .qc-reply textarea'); t.focus();"); await type('A reply from the same reader.');
  await evaluate("document.querySelector('.qc-box[data-id] .qc-send').click()");
  await until("document.querySelectorAll('.qc-box[data-id] .qc-msg').length === 2", 'the reply');
  step('replied: two messages in the thread', (await state()).threads[0].n === 2);
  await evaluate("var d = document.querySelectorAll('.qc-box[data-id] .qc-msg .qc-del'); d[d.length - 1].click()");
  await until("document.querySelectorAll('.qc-box[data-id] .qc-msg').length === 1", 'the reply gone');
  step('deleted own reply: one message again', (await state()).threads[0].n === 1);

  // fold to the pin, and unfold
  await evaluate("document.querySelector('.qc-pin[data-id]').click()");
  step('folded: the box hides behind the pin', await evaluate("document.querySelector('.qc-box[data-id]').classList.contains('qc-fold')"));
  await evaluate("document.querySelector('.qc-pin[data-id]').click()");
  step('unfolded again', !(await evaluate("document.querySelector('.qc-box[data-id]').classList.contains('qc-fold')")));

  // sign out: everything disappears
  await evaluate("document.getElementById('qc-me').click()");
  await until("!!document.getElementById('qc-signout')", 'the menu');
  await evaluate("document.getElementById('qc-signout').click()");
  await until("!!document.getElementById('qc-signin')", 'Sign in after signing out');
  step('signed out: no pins, no boxes', !(await evaluate("document.querySelectorAll('.qc-pin, .qc-box').length")) && (await state()).me === null);

  // the admin sees the reader's thread, and may delete it
  await signIn(ADMIN, 'login');
  await until("document.querySelectorAll('.qc-pin[data-id]').length === 1", "the reader's pin for the admin");
  const s3 = await state();
  step('admin: signed in, sees the thread, has the delete control', s3.me.admin === true && s3.threads.length === 1 && await evaluate("!!document.querySelector('.qc-box[data-id] .qc-delthread')"));
  await evaluate("document.getElementById('qc-me').click()");
  await until("!!document.getElementById('qc-admin')", 'the admin menu item');
  await evaluate("document.getElementById('qc-admin').click()");
  await until("document.querySelectorAll('#qc-all .qc-list li').length === 1", 'the all-comments list');
  const listed = await evaluate("document.querySelector('#qc-all .qc-list li').textContent");
  step('admin: the all-comments list names the reader and the note', listed.includes(READER.name) && listed.includes(NOTE));
  await shot('04_admin_list');
  await evaluate("window.confirm = function(){ return true; }; document.querySelector('#qc-all .qc-list li .qc-btn').click()");
  await until("document.querySelectorAll('.qc-pin[data-id]').length === 0", 'the thread gone');
  const left = await evaluate("fetch('/api/admin/threads').then(r => r.json()).then(d => d.threads.length)");
  step('admin: deleted the thread; the API has none left', left === 0 && (await state()).threads.length === 0);
} catch (e) {
  step('story', false, { error: String(e.message || e) });
}
if (errors.length) step('no exceptions or console errors', false, { errors });
else step('no exceptions or console errors', true);

ws.close(); chrome.kill(); server.close(); api.kill();
try { fs.rmSync(udd, { recursive: true, force: true }); } catch {}
try { fs.rmSync(tmp, { recursive: true, force: true }); } catch {}
console.log(JSON.stringify({ steps: steps.length, failed, ok: failed === 0 }));
process.exit(failed ? 1 : 0);
