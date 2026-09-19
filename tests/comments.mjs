// THE COMMENTS WALKTHROUGH: the admin invites a reader, she follows the link in the mail
// and the link makes her account; she pins a note to a paragraph, and the note stays on
// that paragraph through scrolling, a reload and a narrower window; the admin then removes
// it, and closes her account.
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
// asked from here rather than from the page, so a refusal is not a console error there
const apiPost = (p, body) => new Promise((res) => {
  const data = JSON.stringify(body);
  const r = http.request({ host: '127.0.0.1', port: apiPort, path: p, method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) } },
    x => { x.resume(); res(x.statusCode); });
  r.on('error', () => res(null)); r.write(data); r.end();
});
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
async function drag(x0, y0, x1, y1) {
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: x0, y: y0 });
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: x0, y: y0, button: 'left', buttons: 1, clickCount: 1 });
  for (let i = 1; i <= 6; i++) {
    await send('Input.dispatchMouseEvent', { type: 'mouseMoved', button: 'left', buttons: 1,
      x: x0 + (x1 - x0) * i / 6, y: y0 + (y1 - y0) * i / 6 });
  }
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: x1, y: y1, button: 'left', buttons: 0, clickCount: 1 });
  await new Promise(r => setTimeout(r, 150));
}
async function dblclick(x, y) {
  for (const clickCount of [1, 2]) {
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', buttons: 1, clickCount });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', buttons: 0, clickCount });
  }
  await new Promise(r => setTimeout(r, 150));
}
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
async function signIn(u) {
  await until("!!document.getElementById('qc-signin')", 'the Sign in control');
  await evaluate("document.getElementById('qc-signin').click()");
  await until("!!document.getElementById('qc-auth')", 'the sign-in dialog');
  // real typing into each field
  await evaluate("document.getElementById('qc-email').focus()"); await type(u.email);
  await evaluate("document.getElementById('qc-pass').focus()"); await type(u.password);
  await evaluate("document.getElementById('qc-submit').click()");
  await until("!!document.getElementById('qc-add')", 'the + Comment control after signing in');
}
async function openMenu(item) {
  await evaluate("document.getElementById('qc-me').click()");
  await until(`!!document.getElementById('${item}')`, 'the menu item ' + item);
  await evaluate(`document.getElementById('${item}').click()`);
}
async function signOut() {
  await openMenu('qc-signout');
  await until("!!document.getElementById('qc-signin')", 'Sign in after signing out');
}

try {
  const url = BASE + PAGE;
  await open(url);
  const s0 = await state();
  step('signed out: Sign in offered, nothing shown', await evaluate("!!document.getElementById('qc-signin')") && !(await evaluate("document.querySelectorAll('.qc-pin').length")) && s0 && s0.me === null);
  // asked from here, not from the page: Chrome logs a 401 as a console error
  const st401 = await new Promise(res => http.get(BASE + 'api/threads?page=/', r => { r.resume(); res(r.statusCode); }).on('error', () => res(null)));
  step('signed out: the API refuses the thread list', st401 === 401, { status: st401 });

  // the admin invites the reader: nobody here makes their own account
  await signIn(ADMIN);
  await openMenu('qc-people-btn');
  await until("!!document.getElementById('qc-people') && document.querySelectorAll('#qc-users li').length === 1", 'the people panel');
  step('admin: the panel lists the one account there is',
       (await evaluate("document.querySelector('#qc-users li').textContent")).includes(ADMIN.email));
  await evaluate("document.getElementById('qc-inv-email').focus()"); await type(READER.email);
  await evaluate("document.getElementById('qc-inv-name').focus()"); await type(READER.name);
  await evaluate("document.getElementById('qc-invite').click()");
  await until("!!document.querySelector('#qc-people .qc-linkbox')", 'the invitation link');
  const link = await evaluate("document.querySelector('#qc-people .qc-linkbox').textContent");
  const okmsg = await evaluate("document.getElementById('qc-invite-ok').textContent");
  step('admin: invited the reader, and the link came back to copy',
       /\?invite=[A-Za-z0-9_-]{20,}$/.test(link) && okmsg.includes(READER.email), { link, okmsg });
  await until("document.querySelectorAll('#qc-invites li[data-email]').length === 1", 'the invitation in the list');
  step('admin: the invitation is waiting in the list',
       (await evaluate("document.querySelector('#qc-invites li[data-email]').textContent")).includes('waiting'));
  await shot('01_invite');
  await evaluate("document.querySelector('#qc-people > .qc-row .qc-btn').click()");
  await signOut();

  // there is no way into a registration form without a link
  await evaluate("document.getElementById('qc-signin').click()");
  await until("!!document.getElementById('qc-auth')", 'the sign-in dialog');
  step('signed out: the dialog offers no way to make an account',
       !(await evaluate("!!document.getElementById('qc-switch')"))
       && (await evaluate("document.getElementById('qc-auth').textContent")).includes('by invitation'));
  await evaluate("document.querySelector('.qc-modal').click()");

  // the reader follows the link she was mailed
  const token = link.split('invite=')[1];
  await open(url + '?invite=' + token);
  await until("!!document.getElementById('qc-auth') && document.getElementById('qc-auth').getAttribute('data-mode') === 'register'", 'the invitation form');
  const search = await evaluate('location.search');
  step('the link opened the invitation form for that address, and left the URL',
       (await evaluate("document.getElementById('qc-email').value")) === READER.email
       && (await evaluate("document.getElementById('qc-email').readOnly")) === true
       && !/invite=/.test(search), { search });
  await evaluate("document.getElementById('qc-name').value = ''; document.getElementById('qc-name').focus()"); await type(READER.name);
  await evaluate("document.getElementById('qc-pass').focus()"); await type(READER.password);
  await evaluate("document.getElementById('qc-submit').click()");
  await until("!!document.getElementById('qc-add')", 'the + Comment control after accepting');
  const s1 = await state();
  step('the invitation made her account and signed her in',
       s1 && s1.me && s1.me.name === READER.name && s1.me.email === READER.email && s1.me.admin === false && s1.threads.length === 0, { me: s1 && s1.me });
  // and it is spent: the same link opens nothing now
  const spent = await new Promise(res => http.get(`http://127.0.0.1:${apiPort}/api/invite?token=` + token, r => { r.resume(); res(r.statusCode); }).on('error', () => res(null)));
  step('the link is spent once it has made an account', spent === 409, { status: spent });

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

  // getting the note out of the way of the words it covers: minimise it, or move it.
  // `shown` asks whether the box is really on the page, not whether it carries the class:
  // the class was once set with no rule to hide it, so folding did nothing a reader saw.
  const shown = () => evaluate("(function(){ var b = document.querySelector('.qc-box[data-id]'); return !!(b && b.offsetParent && b.getBoundingClientRect().height > 0); })()");
  await evaluate("document.querySelector('.qc-box[data-id] [id^=qc-fold-]').click()");
  step('minimised from its header: the note leaves the page and the pin stays',
       (await shown()) === false && (await state()).threads[0].folded === true
       && await evaluate("!!document.querySelector('.qc-pin[data-id]')"));
  await evaluate("document.querySelector('.qc-pin[data-id]').click()");
  step('the pin opens it again', (await shown()) === true);
  await evaluate("document.querySelector('.qc-pin[data-id]').click()");
  step('and the pin folds it again', (await shown()) === false);
  await evaluate("document.querySelector('.qc-pin[data-id]').click()");
  await until("document.querySelector('.qc-box[data-id]').getBoundingClientRect().height > 0", 'the note open again');

  // dragged by its header, with a real mouse, off the text it was covering
  const r0 = await rectOf('.qc-box[data-id]');
  const h0 = await rectOf('.qc-box[data-id] .qc-head');
  await drag(h0.l + 60, h0.t + 12, h0.l + 200, h0.t + 102);
  const r1 = await rectOf('.qc-box[data-id]');
  step('dragged by its header: the note moved with the mouse',
       near(r1.l, r0.l + 140, 4) && near(r1.t, r0.t + 90, 4), { before: r0, after: r1 });

  // it is still pinned to the paragraph: the scroll carries it, it is not stuck to the window
  const canScroll2 = await evaluate('document.documentElement.scrollHeight > window.innerHeight + 100');
  if (canScroll2) {
    await evaluate('window.scrollTo(0, 200)'); await new Promise(r => setTimeout(r, 200)); await evaluate('window.QCCOMMENTS.place()');
    const r2 = await rectOf('.qc-box[data-id]');
    step('a moved note still belongs to its paragraph', r2.sy > 100 && near(r2.t, r1.t - r2.sy, 4), { before: r1.t, after: r2.t, scrolled: r2.sy });
    await evaluate('window.scrollTo(0, 0)'); await new Promise(r => setTimeout(r, 200)); await evaluate('window.QCCOMMENTS.place()');
  } else step('a moved note still belongs to its paragraph (page too short, skipped)', true);

  // and where the reader put it is where it is after a reload
  await open(url);
  await until("document.querySelector('.qc-box[data-id]')", 'the note after the reload');
  const r3 = await rectOf('.qc-box[data-id]');
  step('the move survives a reload', near(r3.l, r1.l, 4) && near(r3.t, r1.t, 4), { before: r1, after: r3 });
  await shot('04_moved');

  // double-clicking the header puts it back on its spot
  const h3 = await rectOf('.qc-box[data-id] .qc-head');
  await dblclick(h3.l + 60, h3.t + 12);
  const r4 = await rectOf('.qc-box[data-id]');
  step('double-clicking the header puts it back', near(r4.l, r0.l, 4) && near(r4.t, r0.t, 4) && (await state()).threads[0].moved === null,
       { back: r4, was: r0 });

  // folded, the pin is the whole note -- and it is what there is to take hold of
  await evaluate("document.querySelector('.qc-box[data-id] [id^=qc-fold-]').click()");
  const p0 = await rectOf('.qc-pin[data-id]');
  await drag(p0.l + 15, p0.t + 15, p0.l + 15 - 180, p0.t + 15 + 120);
  const p1 = await rectOf('.qc-pin[data-id]');
  step('folded: the pin itself drags around the page',
       (await shown()) === false && near(p1.l, p0.l - 180, 4) && near(p1.t, p0.t + 120, 4), { before: p0, after: p1 });
  step('the drag did not count as the click that opens it', (await state()).threads[0].folded === true);
  await shot('05_folded_pin');
  await evaluate("document.querySelector('.qc-pin[data-id]').click()");
  await until("document.querySelector('.qc-box[data-id]').getBoundingClientRect().height > 0", 'the note open again');
  const p2 = await rectOf('.qc-pin[data-id]'), b2 = await rectOf('.qc-box[data-id]');
  step('a click still opens it, and the note comes back beside the pin where it now stands',
       near(p2.l, p1.l, 2) && b2.l > p2.l && b2.l - p2.l < 60, { pin: p2, box: b2 });
  await open(url);
  await until("document.querySelector('.qc-pin[data-id]')", 'the pin after the reload');
  const p3 = await rectOf('.qc-pin[data-id]');
  step('the moved pin is where it was left after a reload', near(p3.l, p2.l, 4) && near(p3.t, p2.t, 4), { before: p2, after: p3 });

  // and the menu puts the whole lot back
  await openMenu('qc-putback');
  await new Promise(r => setTimeout(r, 200));
  const p4 = await rectOf('.qc-pin[data-id]');
  step('the menu puts every note back on its spot',
       near(p4.l, p0.l, 4) && near(p4.t, p0.t, 4) && (await state()).threads[0].movedPin === null, { back: p4, was: p0 });

  // sign out: everything disappears
  await signOut();
  step('signed out: no pins, no boxes', !(await evaluate("document.querySelectorAll('.qc-pin, .qc-box').length")) && (await state()).me === null);

  // the admin sees the reader's thread, and may delete it
  await signIn(ADMIN);
  await until("document.querySelectorAll('.qc-pin[data-id]').length === 1", "the reader's pin for the admin");
  const s3 = await state();
  step('admin: signed in, sees the thread, has the delete control', s3.me.admin === true && s3.threads.length === 1 && await evaluate("!!document.querySelector('.qc-box[data-id] .qc-delthread')"));
  // ADDRESSED, without deleting: the note stays, greys out, and says who dealt with it
  await evaluate("document.querySelector('.qc-box[data-id] .qc-resolve').click()");
  await until("document.querySelectorAll('.qc-pin.qc-done').length === 1", 'the pin marked addressed');
  const doneNote = await evaluate("(document.querySelector('.qc-done-note') || {}).textContent || ''");
  const doneCount = await evaluate("(document.getElementById('qc-count') || {}).textContent || ''");
  const doneState = await state();
  step('admin: marked the note addressed; it is still there, attributed, and counted',
       doneNote.includes('Addressed by ' + ADMIN.name) && doneCount === '0 open · 1 addressed'
       && doneState.threads.length === 1 && !!doneState.threads[0].resolved,
       { note: doneNote, count: doneCount });
  await shot('05b_addressed');
  // hiding them takes the pin off the page; showing them brings it back
  await openMenu('qc-toggle-done');
  await until("document.querySelectorAll('.qc-pin[data-id]').length === 0", 'the addressed pin hidden');
  await openMenu('qc-toggle-done');
  await until("document.querySelectorAll('.qc-pin.qc-done').length === 1", 'the addressed pin back');
  // and it reopens
  await evaluate("document.querySelector('.qc-pin[data-id]').click()");
  await until("!!document.querySelector('.qc-box[data-id] .qc-resolve')", 'the note open again');
  await evaluate("document.querySelector('.qc-box[data-id] .qc-resolve').click()");
  await until("document.querySelectorAll('.qc-pin.qc-done').length === 0", 'the note reopened');
  step('admin: reopening it puts it back as it was', !(await state()).threads[0].resolved);

  await openMenu('qc-admin');
  await until("document.querySelectorAll('#qc-all .qc-list li').length === 1", 'the all-comments list');
  const listed = await evaluate("document.querySelector('#qc-all .qc-list li').textContent");
  step('admin: the all-comments list names the reader and the note', listed.includes(READER.name) && listed.includes(NOTE));
  await shot('06_admin_list');
  await evaluate("window.confirm = function(){ return true; }; document.querySelector('#qc-all .qc-list li .qc-btn').click()");
  await until("document.querySelectorAll('.qc-pin[data-id]').length === 0", 'the thread gone');
  const left = await evaluate("fetch('/api/admin/threads').then(r => r.json()).then(d => d.threads.length)");
  step('admin: deleted the thread; the API has none left', left === 0 && (await state()).threads.length === 0);

  // closing her account: she is out at once, and refused at the door afterwards
  await evaluate("document.querySelector('#qc-all > .qc-row .qc-btn').click()");
  await openMenu('qc-people-btn');
  await until("document.querySelectorAll('#qc-users li').length === 2", 'both accounts in the panel');
  const who = `#qc-users li[data-email="${READER.email}"]`;
  await evaluate(`window.confirm = function(){ return true; }; document.querySelector('${who} .qc-btn').click()`);
  await until(`/closed/.test(document.querySelector('${who}').textContent)`, 'the closed tag');
  step("admin: closed the reader's account", true);
  await shot('07_closed');
  const refused = await apiPost('/api/login', { email: READER.email, password: READER.password });
  step('a closed account is refused at the door', refused === 403, { status: refused });
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
