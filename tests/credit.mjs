// THE CREDIT PAGE: a signed-in collaborator sees who has commented, and a comment that has
// been addressed still counts.
//
//   node tests/credit.mjs <site dir> [--python <exe>] [--shots <dir>]
//
// The page is served over HTTP with `/api/` proxied to a fresh comments API on a temporary
// database, seeded through the API itself: two people comment, one comment is deleted, and
// the page must still show it -- marked addressed rather than gone.  No npm.
//
// Exit 1 on any failed step, uncaught exception or console error.  One JSON line per step.
import fs from 'fs';
import http from 'http';
import os from 'os';
import path from 'path';
import { spawn } from 'child_process';
import { fileURLToPath } from 'url';

const args = process.argv.slice(2);
const site = path.resolve(args[0] || 'site');
const opt = (k) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : null; };
const PYTHON = opt('--python') || process.env.PYTHON || 'python';
const SHOTS = opt('--shots');
const HERE = path.dirname(fileURLToPath(import.meta.url));
const API_PY = path.join(HERE, '..', 'qccd', 'site', 'comments_api.py');
const CHROME = process.env.CHROME || [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium-browser', '/usr/bin/chromium',
].find(p => fs.existsSync(p));
if (!CHROME) { console.error('no Chrome found; set CHROME'); process.exit(2); }

const ADA = { email: 'ada@example.org', password: 'reader-pass-123', name: 'Ada Reader' };
const BOB = { email: 'bob@example.org', password: 'other-pass-123', name: 'Bob Other' };

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'qccd-credit-'));
const db = path.join(tmp, 'comments.db');
const run = (a) => new Promise((res, rej) => { const p = spawn(PYTHON, a, { stdio: ['ignore', 'pipe', 'pipe'] });
  let out = '', err = ''; p.stdout.on('data', d => out += d); p.stderr.on('data', d => err += d);
  p.on('close', c => c === 0 ? res(out) : rej(new Error(err || out))); });
for (const u of [ADA, BOB]) await run([API_PY, 'passwd', '--db', db, u.email, '--name', u.name, '--password', u.password]);
const apiPort = 8700 + Math.floor(Math.random() * 300);
const api = spawn(PYTHON, [API_PY, 'serve', '--db', db, '--port', String(apiPort)], { stdio: ['ignore', 'pipe', 'pipe'] });
let apiLog = ''; api.stdout.on('data', d => apiLog += d); api.stderr.on('data', d => apiLog += d);
const getJSON = (u) => new Promise((res, rej) => http.get(u, r => { let s = ''; r.on('data', d => s += d); r.on('end', () => { try { res(JSON.parse(s)); } catch (e) { rej(e); } }); }).on('error', rej));
let up = false;
for (let i = 0; i < 100 && !up; i++) { try { up = (await getJSON(`http://127.0.0.1:${apiPort}/api/health`)).ok; } catch { await new Promise(r => setTimeout(r, 100)); } }
if (!up) { console.error('the API did not start:\n' + apiLog); api.kill(); process.exit(2); }

// -------- seed through the API, as a reader would ------------------------------------
function call(method, p, body, cookie) {
  return new Promise((res, rej) => {
    const data = body === undefined ? null : JSON.stringify(body);
    const headers = { Origin: `http://127.0.0.1:${apiPort}` };
    if (data) { headers['Content-Type'] = 'application/json';
      // BaseHTTPRequestHandler reads Content-Length and cannot read a chunked body:
      // without this Node chunks it, the server never consumes it, and the leftover
      // bytes are parsed as the next response.
      headers['Content-Length'] = Buffer.byteLength(data); }
    if (cookie) headers.Cookie = cookie;
    const r = http.request({ host: '127.0.0.1', port: apiPort, path: p, method, headers }, x => {
      let s = ''; x.on('data', d => s += d); x.on('end', () => {
        const sc = (x.headers['set-cookie'] || []).find(v => v.startsWith('qccd_session='));
        res({ status: x.statusCode, body: s ? JSON.parse(s) : null, cookie: sc ? sc.split(';')[0] : cookie });
      });
    });
    r.on('error', rej); if (data) r.write(data); r.end();
  });
}
const anchor = { sel: '#p1', tag: 'p', ox: 0.5, oy: 0.5 };
let ada = (await call('POST', '/api/login', { email: ADA.email, password: ADA.password })).cookie;
let bob = (await call('POST', '/api/login', { email: BOB.email, password: BOB.password })).cookie;
const th = (await call('POST', '/api/threads', { page: '/compilation/', anchor, text: 'the electrodes should be rectangular' }, ada)).body.thread;
const reply = (await call('POST', `/api/threads/${th.id}/comments`, { text: 'agreed, and the colour is wrong too' }, bob)).body.comment;
await call('POST', '/api/threads', { page: '/rules/', anchor, text: 'this rule needs an example' }, bob);
await call('DELETE', `/api/comments/${reply.id}`, undefined, bob);     // addressed: off the page

// -------- serve the page with /api/ proxied ------------------------------------------
const MIME = { '.html': 'text/html; charset=utf-8', '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml', '.css': 'text/css', '.js': 'text/javascript' };
const server = http.createServer((req, res) => {
  const u = decodeURIComponent(req.url.split('?')[0].split('#')[0]);
  if (u.startsWith('/api/')) {
    const p = http.request({ host: '127.0.0.1', port: apiPort, path: req.url, method: req.method, headers: req.headers }, r => { res.writeHead(r.statusCode, r.headers); r.pipe(res); });
    p.on('error', () => { res.writeHead(502); res.end('api down'); });
    req.pipe(p); return;
  }
  let p = path.join(site, u);
  if (u.endsWith('/')) p = path.join(p, 'index.html');
  if (!fs.existsSync(p) || fs.statSync(p).isDirectory()) { res.writeHead(404); return res.end('not found'); }
  res.writeHead(200, { 'content-type': MIME[path.extname(p)] || 'application/octet-stream' });
  fs.createReadStream(p).pipe(res);
});
await new Promise(r => server.listen(0, '127.0.0.1', r));
const BASE = `http://127.0.0.1:${server.address().port}/`;

// -------- Chrome ----------------------------------------------------------------------
const port = 9800 + Math.floor(Math.random() * 300);
const udd = fs.mkdtempSync(path.join(os.tmpdir(), 'qccd-credit-chrome-'));
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${udd}`,
  '--disable-extensions', '--no-first-run', '--no-default-browser-check', '--window-size=1440,1000', '--hide-scrollbars', 'about:blank'], { stdio: 'ignore' });
let targets = null;
for (let i = 0; i < 100 && !targets; i++) { try { targets = await getJSON(`http://127.0.0.1:${port}/json/list`); } catch { await new Promise(r => setTimeout(r, 100)); } }
const ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
let seq = 0; const pending = new Map(); const listeners = new Map();
ws.onmessage = (ev) => { const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { const { res, rej } = pending.get(m.id); pending.delete(m.id); m.error ? rej(new Error(m.error.message)) : res(m.result); }
  else if (m.method && listeners.has(m.method)) listeners.get(m.method).forEach(f => f(m.params)); };
const send = (method, params = {}) => new Promise((res, rej) => { const id = ++seq; pending.set(id, { res, rej }); ws.send(JSON.stringify({ id, method, params })); });
const on = (m, f) => { if (!listeners.has(m)) listeners.set(m, []); listeners.get(m).push(f); };
await send('Page.enable'); await send('Runtime.enable'); await send('Log.enable');
const errors = [];
on('Runtime.exceptionThrown', p => errors.push('exception: ' + (p.exceptionDetails.exception?.description || p.exceptionDetails.text).split('\n')[0]));
const EXPECTED = [
  /401 \(Unauthorized\)/,   // the signed-out page asks /api/credit and is refused: that is the feature
  /404 \(Not Found\)/,      // this harness serves the one page, without favicon or footer logos
];
on('Log.entryAdded', p => {
  if (p.entry.level !== 'error') return;
  if (EXPECTED.some(rx => rx.test(p.entry.text))) return;
  errors.push('console: ' + p.entry.text);
});
let loaded = null;
on('Page.loadEventFired', () => { if (loaded) loaded(); });
async function open(url) { const p = new Promise(r => { loaded = r; }); await send('Page.navigate', { url }); await Promise.race([p, new Promise(r => setTimeout(r, 30000))]); loaded = null; await new Promise(r => setTimeout(r, 500)); }
async function evaluate(expr) { const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error('probe: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text)); return r.result.value; }
async function until(expr, what, ms = 8000) { const t0 = Date.now();
  for (;;) { const v = await evaluate(expr); if (v) return v; if (Date.now() - t0 > ms) throw new Error('timed out waiting for ' + what); await new Promise(r => setTimeout(r, 60)); } }
async function shot(name) { if (!SHOTS) return; fs.mkdirSync(SHOTS, { recursive: true });
  const m = await send('Page.getLayoutMetrics');
  await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: Math.min(Math.ceil(m.cssContentSize.height), 4000), deviceScaleFactor: 1, mobile: false });
  await new Promise(r => setTimeout(r, 400));
  const r = await send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(path.join(SHOTS, name + '.png'), Buffer.from(r.data, 'base64'));
  await send('Emulation.clearDeviceMetricsOverride'); }

const steps = []; let failed = 0;
function step(name, ok, detail) { const rec = { step: name, ok: !!ok, ...(detail ? { detail } : {}) }; if (!ok) failed++; steps.push(rec); console.log(JSON.stringify(rec)); }

try {
  // signed out: the page exists and asks for a sign-in rather than showing anything
  await open(BASE + 'credit/');
  await until("!!document.getElementById('crmsg')", 'the credit page');
  step('signed out: the page says to sign in and shows no names',
       (await evaluate("document.getElementById('crmsg').hidden")) === false
       && (await evaluate("document.getElementById('crbody').hidden")) === true
       && (await evaluate("document.getElementById('crmsg').textContent")).indexOf('Sign in') >= 0);

  // sign in as Ada, in the page, the way the bar does
  await evaluate(`fetch('/api/login', {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'},
                  body: JSON.stringify({email: ${JSON.stringify(ADA.email)}, password: ${JSON.stringify(ADA.password)}})}).then(r => r.status)`);
  await open(BASE + 'credit/');
  await until("document.querySelectorAll('#crpeople tr').length > 0", 'the people table');

  const rows = await evaluate(`Array.from(document.querySelectorAll('#crpeople tr')).map(function(tr){
    return { who: tr.querySelector('.who span:not(.av)').textContent.trim(),
             initials: tr.querySelector('.av').textContent.trim(),
             total: +tr.children[1].textContent.trim() }; })`);
  step('signed in: both collaborators are listed, most comments first',
       rows.length === 2 && rows[0].who === 'Bob Other' && rows[0].total === 2 && rows[0].initials === 'BO'
       && rows[1].who === 'Ada Reader' && rows[1].total === 1, { rows });

  const items = await evaluate(`Array.from(document.querySelectorAll('#critems li')).map(function(li){
    return { tag: li.querySelector('.tag').textContent, text: li.querySelector('p').textContent }; })`);
  step('the deleted comment is still on the page, marked addressed',
       items.length === 3 && items.filter(i => i.tag === 'addressed').length === 1
       && items.some(i => i.tag === 'addressed' && i.text.indexOf('the colour is wrong') >= 0), { items });

  step('it is off the page it was pinned to',
       (await evaluate("fetch('/api/threads?page=/compilation/', {credentials:'same-origin'}).then(r => r.json()).then(d => d.threads[0].comments.length)")) === 1);

  const bar = await evaluate(`(function(){ var b = document.querySelector('#crpeople tr .crbar'); return b ? b.title : null; })()`);
  step('the bar says how many are addressed and how many are open', /1 addressed, 1 still open/.test(bar), { bar });
  await shot('credit');

  step('no exceptions or console errors', errors.length === 0, errors.length ? { errors } : undefined);
} catch (e) {
  step('story', false, { error: String(e.message || e) });
  if (errors.length) step('no exceptions or console errors', false, { errors });
}

ws.close(); chrome.kill(); server.close(); api.kill();
try { fs.rmSync(udd, { recursive: true, force: true }); } catch {}
try { fs.rmSync(tmp, { recursive: true, force: true }); } catch {}
console.log(JSON.stringify({ steps: steps.length, failed, ok: failed === 0 }));
process.exit(failed ? 1 : 0);
