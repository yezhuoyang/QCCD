// THE COURSE'S PROGRESS IN A REAL BROWSER: the Learn page's lesson cards and progress panel,
// and the account sync (`qccd/site/progress.py`) over the real accounts API.
//
//   node tests/progress.mjs <site dir> [--python <exe>]
//
// <site dir> holds `learn/index.html` (the course section through the site's `with_nav`) and
// `studio.html` (a page carrying the sync script and a stand-in for the studio's EDITOR).
// The API (`qccd/site/comments_api.py`) runs on a temporary database with two readers, and
// `/api/` is proxied to it.  One JSON line per step on stdout, then a summary; exit 1 on any
// failed step, uncaught exception or console error.
import fs from 'fs';
import http from 'http';
import os from 'os';
import path from 'path';
import { spawn } from 'child_process';
import { fileURLToPath } from 'url';
import { findChrome } from './chrome_path.mjs';

const args = process.argv.slice(2);
const site = path.resolve(args[0] || 'site');
const opt = (k) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : null; };
const PYTHON = opt('--python') || process.env.PYTHON || 'python';
const HERE = path.dirname(fileURLToPath(import.meta.url));
const API_PY = path.join(HERE, '..', 'qccd', 'site', 'comments_api.py');
const CHROME = findChrome();
if (!CHROME) { console.error('no Chrome found; set CHROME'); process.exit(2); }
const ADA = { email: 'ada@example.org', password: 'ada-pass-1234', name: 'Ada Lovelace' };
const BOB = { email: 'bob@example.org', password: 'bob-pass-1234', name: 'Bob Builder' };

// -------- the API on a temporary database --------------------------------------------
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'qccd-progress-'));
const db = path.join(tmp, 'comments.db');
const run = (a) => new Promise((res, rej) => { const p = spawn(PYTHON, a, { stdio: ['ignore', 'pipe', 'pipe'] });
  let out = '', err = ''; p.stdout.on('data', d => out += d); p.stderr.on('data', d => err += d);
  p.on('close', c => c === 0 ? res(out) : rej(new Error(err || out))); });
for (const u of [ADA, BOB]) await run([API_PY, 'passwd', '--db', db, u.email, '--name', u.name, '--password', u.password]);
const apiPort = 8600 + Math.floor(Math.random() * 400);
const api = spawn(PYTHON, [API_PY, 'serve', '--db', db, '--port', String(apiPort)], { stdio: ['ignore', 'pipe', 'pipe'] });
let apiLog = ''; api.stdout.on('data', d => apiLog += d); api.stderr.on('data', d => apiLog += d);
const getJSON = (u) => new Promise((res, rej) => http.get(u, r => { let s = ''; r.on('data', d => s += d); r.on('end', () => { try { res(JSON.parse(s)); } catch (e) { rej(e); } }); }).on('error', rej));
let up = false;
for (let i = 0; i < 100 && !up; i++) { try { up = (await getJSON(`http://127.0.0.1:${apiPort}/api/health`)).ok; } catch { await new Promise(r => setTimeout(r, 100)); } }
if (!up) { console.error('the API did not start:\n' + apiLog); api.kill(); process.exit(2); }
// what the account holds, read from the database through the CLI, never through the page
async function accountStars(u) {
  const out = await run([API_PY, 'progress', '--db', db, u.email]);
  const s = {};
  for (const line of out.split(/\r?\n/)) { const m = /^([A-Z]\d+)\s+(\*+)/.exec(line); if (m) s[m[1]] = m[2].length; }
  return s;
}
let posts = 0;

// -------- a static server with /api/ proxied -----------------------------------------
const MIME = { '.html': 'text/html; charset=utf-8', '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml' };
const server = http.createServer((req, res) => {
  const u = decodeURIComponent(req.url.split('?')[0].split('#')[0]);
  if (u.startsWith('/api/')) {
    if (u === '/api/progress' && req.method === 'POST') posts++;
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
const udd = fs.mkdtempSync(path.join(os.tmpdir(), 'qccd-progress-chrome-'));
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${udd}`,
  '--disable-extensions', '--no-first-run', '--no-default-browser-check', '--window-size=1400,900', '--hide-scrollbars', 'about:blank'], { stdio: 'ignore' });
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
on('Log.entryAdded', p => { if (p.entry.level === 'error') errors.push('console: ' + p.entry.text + ' ' + (p.entry.url || '')); });
let loaded = null; on('Page.loadEventFired', () => { if (loaded) loaded(); });
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
async function evaluate(expr) {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error('probe: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
  return r.result.value;
}
async function until(expr, what, ms = 8000) {
  for (let t = 0; t < ms; t += 100) { if (await evaluate(expr)) return true; await sleep(100); }
  throw new Error('timed out waiting for ' + what);
}
async function open(rel) {
  const p = new Promise(r => { loaded = r; });
  await send('Page.navigate', { url: BASE + rel });
  await Promise.race([p, sleep(20000)]); loaded = null;
  if (rel.startsWith('learn')) await until("!!window.QCCDPROGRESS && QCCDPROGRESS.state().status !== 'loading'", 'the progress to load');
  await sleep(300);
}
async function type(text) { await send('Input.insertText', { text }); }
const setLocal = (stars, extra = {}) => evaluate(`localStorage.setItem('qccd.studio.tutorial', ${JSON.stringify(JSON.stringify({ stars, best: {}, ...extra }))}); true`);
const localStars = () => evaluate(`(JSON.parse(localStorage.getItem('qccd.studio.tutorial') || '{}').stars) || {}`);
const panel = () => evaluate(`({ who: document.getElementById('you-who').textContent, acct: document.getElementById('you-acct').textContent,
  sign: !document.getElementById('you-signin').hidden, pct: document.getElementById('you-pct').textContent,
  go: document.getElementById('you-go').getAttribute('href'), nw: document.getElementById('you-new').hidden ? null : document.getElementById('you-new').textContent,
  done: Array.from(document.querySelectorAll('.lesson.done')).map(function(c){ return c.dataset.lesson; }),
  next: Array.from(document.querySelectorAll('.lesson.next')).map(function(c){ return c.dataset.lesson; }),
  fresh: Array.from(document.querySelectorAll('.lesson.new')).map(function(c){ return c.dataset.lesson; }) })`);
const same = (a, b) => JSON.stringify(Object.entries(a).sort()) === JSON.stringify(Object.entries(b).sort());
async function signInWithTheButton(u) {
  await until("!!document.getElementById('qc-signin') && !document.getElementById('you-signin').hidden", 'the Learn page to offer Sign in');
  await evaluate("document.getElementById('you-signin').click()");
  await until("!!document.getElementById('qc-auth')", 'the sign-in dialog');
  await evaluate("document.getElementById('qc-email').focus()"); await type(u.email);
  await evaluate("document.getElementById('qc-pass').focus()"); await type(u.password);
  await evaluate("document.getElementById('qc-submit').click()");
}
const signOut = () => evaluate("fetch('/api/logout', { method: 'POST', credentials: 'same-origin' }).then(function(r){ return r.status; })");
const signIn = (u) => evaluate(`fetch('/api/login', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
  body: ${JSON.stringify(JSON.stringify({ email: u.email, password: u.password }))} }).then(function(r){ return r.status; })`);

// -------- the story -------------------------------------------------------------------
const steps = [];
let failed = 0;
function step(name, ok, detail) {
  const rec = { step: name, ok: !!ok, ...(detail ? { detail } : {}) };
  if (!ok) failed++;
  steps.push(rec); console.log(JSON.stringify(rec));
}
try {
  await open('learn/');
  await evaluate('localStorage.clear(); true');
  await open('learn/');

  // 1. the layout the reader asked for: a card per lesson, and never two on one line
  const lay = await evaluate(`(function(){ var cs = Array.from(document.querySelectorAll('.lesson')), out = { n: cs.length, rows: true, diagrams: 0, ids: [] };
    for(var i = 0; i < cs.length; i++){ var r = cs[i].getBoundingClientRect();
      if(cs[i].querySelector('.av svg.dg')) out.diagrams++;
      if(i && cs[i].parentNode === cs[i - 1].parentNode && r.top < cs[i - 1].getBoundingClientRect().bottom) out.rows = false; }
    var seen = {}, dup = [];
    document.querySelectorAll('[id]').forEach(function(e){ if(seen[e.id]) dup.push(e.id); seen[e.id] = 1; });
    out.dup = dup; out.widths = Array.from(new Set(cs.map(function(c){ return Math.round(c.getBoundingClientRect().width); })));
    return out; })()`);
  step('one card per lesson, each on its own line', lay.n >= 30 && lay.rows && lay.widths.length <= 1, lay);
  step('every card carries its diagram, and no id is used twice', lay.diagrams === lay.n && lay.dup.length === 0, { diagrams: lay.diagrams, dup: lay.dup });

  // 2. signed out: the first lesson is next, the progress is this browser's, Sign in is offered
  let p = await panel();
  step('signed out: the first lesson is next, and Sign in is offered', p.next[0] === 'A1' && p.sign && /this browser/.test(p.acct) && p.pct === '0%', p);

  // 3. stars earned in this browser before signing in go to the account at sign-in
  await setLocal({ A1: 1, A2: 1, B1: 2 }, { current: 'B2' });
  await open('learn/');
  p = await panel();
  step('signed out: the cards show this browser\'s stars', same(Object.fromEntries(p.done.map(k => [k, 1])), { A1: 1, A2: 1, B1: 1 }) && p.next[0] === 'B2', p);
  await signInWithTheButton(ADA);
  await until("/Saved to your account/.test(document.getElementById('you-acct').textContent)", 'the panel to say the progress is saved', 10000);
  p = await panel();
  let acct = await accountStars(ADA);
  step('signing in from the Learn page saves what this browser earned to the account', same(acct, { A1: 1, A2: 1, B1: 2 }), acct);
  step('the panel greets the reader and says where the progress is kept', /Ada Lovelace/.test(p.who) && !p.sign && /Saved to your account/.test(p.acct), p);

  // 4. the account's stars come down into a fresh browser
  await evaluate("localStorage.removeItem('qccd.studio.tutorial'); true");
  await open('learn/');
  let ls = await localStars();
  p = await panel();
  step('a browser with nothing gets the account\'s stars', same(ls, { A1: 1, A2: 1, B1: 2 }) && p.done.length === 3, { ls, done: p.done });

  // 5. a stale browser never lowers what the account has
  await setLocal({ A1: 1, B1: 1 });
  const before = posts;
  await open('learn/');
  acct = await accountStars(ADA); ls = await localStars();
  step('a stale browser takes nothing away and is brought up to date', acct.B1 === 2 && ls.B1 === 2 && ls.A2 === 1 && posts === before, { acct, ls, posts: posts - before });

  // 6. progress made elsewhere is celebrated on the next visit
  await evaluate(`fetch('/api/progress', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: '{"stars":{"B2":1,"B3":1}}' }).then(function(r){ return r.status; })`);
  await open('learn/');
  p = await panel();
  step('lessons finished elsewhere are marked new on the next visit', same(Object.fromEntries(p.fresh.map(k => [k, 1])), { B2: 1, B3: 1 }) && /B2, B3/.test(p.nw || ''), p);
  await open('learn/');
  p = await panel();
  step('and only once', p.fresh.length === 0 && p.nw === null, p);

  // 6b. the Lean parts are the same tutorial: an exercise solved there counts in the same panel
  if (await evaluate("!!document.querySelector('.lesson[data-chapter]') && !!window.QCCDBOOK")) {
    const before6 = await evaluate("+document.getElementById('you-done').textContent");
    const chap = await evaluate("document.querySelector('.lesson[data-chapter]').getAttribute('data-chapter')");
    const ex = await evaluate(`(function(){ var s = document.scripts, m; for (var i = 0; i < s.length; i++) { m = /"ex":\\["([^"]+)"/.exec(s[i].textContent); if (m) return m[1]; } return null; })()`);
    await evaluate(`QCCDBOOK.mark(${JSON.stringify(ex)}); true`);
    await until(`+document.getElementById('you-done').textContent === ${before6 + 1}`, 'the panel to count the Lean exercise');
    const card = await evaluate(`document.querySelector('#lesson-${chap} .lex').textContent + ' | ' + document.querySelector('.prog[data-part="${chap[0]}"]').textContent`);
    let onAccount = false;
    for (let t = 0; t < 6000 && !onAccount; t += 250) { await sleep(250); onAccount = (await run([API_PY, 'progress', '--db', db, ADA.email])).includes(ex); }
    step('a Lean exercise counts in the one panel, on its chapter card and part, and on the account',
         /^1 of \d+ exercises \| 1 of \d+ exercises$/.test(card) && onAccount, { ex, card, onAccount });
  }

  // 7. a star the course awards in the studio reaches the account
  await open('studio.html');
  await until("!!window.QCCDPROGRESS && QCCDPROGRESS.state().status === 'synced'", 'the studio page to match the account');
  const refreshed0 = await evaluate('window.__refreshed || 0');
  await evaluate(`(function(){ var p = JSON.parse(localStorage.getItem('qccd.studio.tutorial')); p.stars.C1 = 1; localStorage.setItem('qccd.studio.tutorial', JSON.stringify(p)); return true; })()`);
  let got = false;
  for (let t = 0; t < 6000 && !got; t += 250) { await sleep(250); got = (await accountStars(ADA)).C1 === 1; }
  step('a star earned in the studio reaches the account within a few seconds', got, await accountStars(ADA));
  // the studio's own Learn tab is told to read the stars again when the account brought some
  await evaluate("localStorage.setItem('qccd.studio.tutorial', JSON.stringify({ stars: {}, best: {} })); true");
  await open('studio.html');
  await until("QCCDPROGRESS.state().status === 'synced'", 'the studio page to match the account');
  step("the studio's Learn tab is refreshed with the account's stars", (await evaluate('window.__refreshed || 0')) >= 1 && (await localStars()).C1 === 1,
       { refreshed: await evaluate('window.__refreshed || 0'), before: refreshed0 });

  // 8. another reader on the same browser starts from their own progress -- also when they
  // open a book page first: the book keeps its own owner and must not vouch for the course's
  await signOut(); await signIn(BOB);
  const b0 = posts;
  const book = fs.existsSync(path.join(site, 'book.html'));
  if (book) {
    const p8 = new Promise(r => { loaded = r; });
    await send('Page.navigate', { url: BASE + 'book.html' });
    await Promise.race([p8, sleep(20000)]); loaded = null;
    await until("!!window.QCCDBOOK && QCCDBOOK.state().status !== 'loading'", 'the book page to match the account');
  }
  await open('learn/');
  ls = await localStars();
  const bob = await accountStars(BOB);
  p = await panel();
  step("another reader on this browser does not inherit the last reader's stars" + (book ? ' (a book page opened first)' : ''),
       Object.keys(bob).length === 0 && Object.keys(ls).length === 0 && posts === b0 && /Bob Builder/.test(p.who) && p.next[0] === 'A1', { bob, ls, p });

  // 9. signed out again: the page works from the browser, and sends nothing
  await signOut();
  const s0 = posts;
  await setLocal({ D1: 1 });
  await open('learn/');
  await sleep(2500);
  p = await panel();
  step('signed out: nothing is sent, and the page shows this browser', posts === s0 && p.sign && p.done.join() === 'D1', { posts: posts - s0, p });
} catch (e) {
  let where = null;
  try { where = await evaluate(`({ url: location.href, progress: window.QCCDPROGRESS ? QCCDPROGRESS.state() : null,
    me: window.QCCOMMENTS ? QCCOMMENTS.state().me : 'no layer', dialog: !!document.getElementById('qc-auth'),
    acct: (document.getElementById('you-acct') || {}).textContent || null })`); } catch {}
  step('the story ran to the end', false, { error: String(e && e.stack || e), where, api: apiLog.slice(-1500) });
}
const errs = errors.filter(e => !/favicon/.test(e));
step('no uncaught exception or console error', errs.length === 0, errs);
console.log(JSON.stringify({ ok: failed === 0, steps: steps.length, failed }));
try { ws.close(); } catch {}
chrome.kill(); api.kill(); server.close();
process.exit(failed ? 1 : 0);
