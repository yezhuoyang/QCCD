// THE SITE WALKTHROUGH: every page the site build wrote, opened in a real headless Chrome.
//
//   node tests/site.mjs <site dir> [--shots <dir>] [--only <substring>] [--quiet]
//                       [--base <url> [--resolve <host>=<ip>]]   walk a LIVE copy instead
//
// The site is served over HTTP at the root, as a web server would (or, with --base, the
// live server is walked, resolving its name to an IP before DNS moves), and each page is loaded,
// its uncaught exceptions and console errors collected, the navigation bar and its search
// probed, and -- for the studio -- the deep links #learn, #learn=B2 and #design asserted
// to open what they name.  With --shots the walkthrough pages are captured as PNGs.  No
// npm: Chrome speaks the DevTools protocol over Node's built-in WebSocket.
//
// Exit 1 on any exception, console error, missing bar, or failed probe.  One JSON line per
// page on stdout unless --quiet, then a summary.
import fs from 'fs';
import http from 'http';
import os from 'os';
import path from 'path';
import { spawn } from 'child_process';

const args = process.argv.slice(2);
const site = path.resolve(args[0] || 'site');
const opt = (k) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : null; };
const SHOTS = opt('--shots'), ONLY = opt('--only'), QUIET = args.includes('--quiet');
const LIVE = opt('--base'), RESOLVE = opt('--resolve');
const CHROME = process.env.CHROME || [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium-browser', '/usr/bin/chromium',
].find(p => fs.existsSync(p));
if (!CHROME) { console.error('no Chrome found; set CHROME'); process.exit(2); }

// -------- a static server ------------------------------------------------------------
const MIME = { '.html': 'text/html; charset=utf-8', '.json': 'application/json', '.gif': 'image/gif',
               '.png': 'image/png', '.svg': 'image/svg+xml', '.css': 'text/css', '.js': 'text/javascript' };
const server = http.createServer((req, res) => {
  let u = decodeURIComponent(req.url.split('?')[0].split('#')[0]);
  let p = path.join(site, u);
  if (u.endsWith('/')) p = path.join(p, 'index.html');
  if (!fs.existsSync(p) || fs.statSync(p).isDirectory()) { res.writeHead(404); return res.end('not found'); }
  res.writeHead(200, { 'content-type': MIME[path.extname(p)] || 'application/octet-stream' });
  fs.createReadStream(p).pipe(res);
});
if (!LIVE) await new Promise(r => server.listen(0, '127.0.0.1', r));
const BASE = LIVE ? LIVE.replace(/\/?$/, '/') : `http://127.0.0.1:${server.address().port}/`;

// -------- Chrome over CDP ---------------------------------------------------------------
const port = 9300 + Math.floor(Math.random() * 500);
const udd = fs.mkdtempSync(path.join(os.tmpdir(), 'qccd-site-'));
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${udd}`,
  '--disable-extensions', '--no-first-run', '--no-default-browser-check', '--window-size=1440,900',
  '--hide-scrollbars', ...(RESOLVE ? [`--host-resolver-rules=MAP ${RESOLVE.split('=')[0]} ${RESOLVE.split('=')[1]}`] : []),
  'about:blank'], { stdio: 'ignore' });
const getJSON = (u) => new Promise((res, rej) => http.get(u, r => { let s = ''; r.on('data', d => s += d); r.on('end', () => { try { res(JSON.parse(s)); } catch (e) { rej(e); } }); }).on('error', rej));
let targets = null;
for (let i = 0; i < 100 && !targets; i++) {
  try { targets = await getJSON(`http://127.0.0.1:${port}/json/list`); } catch { await new Promise(r => setTimeout(r, 100)); }
}
const page = targets.find(t => t.type === 'page');
const ws = new WebSocket(page.webSocketDebuggerUrl);
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

let errors = [];
on('Runtime.exceptionThrown', p => errors.push('exception: ' + (p.exceptionDetails.exception?.description || p.exceptionDetails.text).split('\n')[0]));
on('Log.entryAdded', p => { if (p.entry.level === 'error') errors.push('console: ' + p.entry.text); });
on('Runtime.consoleAPICalled', p => { if (p.type === 'error') errors.push('console.error: ' + p.args.map(a => a.value || a.description).join(' ')); });
let loaded = null;
on('Page.loadEventFired', () => { if (loaded) loaded(); });

async function open(url) {
  errors = [];
  await send('Page.navigate', { url: 'about:blank' });
  const p = new Promise(r => { loaded = r; });
  await send('Page.navigate', { url });
  await Promise.race([p, new Promise(r => setTimeout(r, 30000))]);
  loaded = null;
  await new Promise(r => setTimeout(r, 400));
}
async function evaluate(expr) {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error('probe: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
  return r.result.value;
}
async function shot(name) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  // a document page is captured whole (capped), an app page as its viewport
  let clip = null;
  try { const m = await evaluate('({h: document.documentElement.scrollHeight, w: document.documentElement.clientWidth, app: document.body.style.overflow === "hidden" || getComputedStyle(document.body).overflow === "hidden"})');
        if (!m.app && m.h > 900) clip = { x: 0, y: 0, width: m.w, height: Math.min(m.h, 4200), scale: 1 }; } catch {}
  const r = await send('Page.captureScreenshot', clip ? { format: 'png', captureBeyondViewport: true, clip } : { format: 'png' });
  fs.writeFileSync(path.join(SHOTS, name + '.png'), Buffer.from(r.data, 'base64'));
}

const PROBE = `(async function(){
  function embedStatus(){ var f = document.querySelector('iframe.live'); if(!f) return null;
    try { var d = f.contentDocument; if(!(d && d.body && d.body.getAttribute('data-embed') === '1')) return false;
          var st = d.getElementById('status'); return st ? st.textContent : 'no status'; } catch(e){ return false; } }
  var e1 = embedStatus(); if(e1) await new Promise(function(r){ setTimeout(r, 1500); }); var e2 = embedStatus();
  var nav = document.getElementById('sitenav');
  var cur = nav ? nav.querySelector('a[aria-current="page"]') : null;
  var sw = document.documentElement.scrollWidth, iw = document.documentElement.clientWidth;
  var hits = (window.SITENAV ? window.SITENAV.search('steane').length : -1);
  return { title: document.title, nav: !!nav, active: cur ? cur.textContent : null, search_hits: hits,
           embed: e1, embed_moving: (e1 && e2) ? e1 !== e2 : null,
           wide: sw > iw + 1, sw: sw, iw: iw,
           over: Array.prototype.slice.call(document.querySelectorAll('body *')).filter(function(e){ var r = e.getBoundingClientRect(); return r.right > iw + 1 && r.width > 0; }).slice(0, 4).map(function(e){ return e.tagName + (e.id ? '#' + e.id : '') + '@' + Math.round(e.getBoundingClientRect().right); }),
           learn_on: (function(){ var p = document.getElementById('paneL'), d = document.getElementById('dock');
                       return p ? (/\\bon\\b/.test(p.className) && !(d && d.getAttribute('data-collapsed') === '1')) : null; })(),
           logos: (function(){ var im = document.querySelectorAll('#sitefoot img'); if(!im.length) return null; for(var i = 0; i < im.length; i++) if(!(im[i].complete && im[i].naturalWidth > 0)) return false; return im.length; })(),
           lesson: (window.EDITOR && EDITOR.lessonState) ? EDITOR.lessonState().id : null,
           ready: (window.EDITOR && EDITOR.ready) ? EDITOR.ready() : null };
})()`;

// -------- the pages ---------------------------------------------------------------------
const pages = [];
(function walk(d) { for (const f of fs.readdirSync(d)) { const p = path.join(d, f);
  if (fs.statSync(p).isDirectory()) walk(p); else if (f.endsWith('.html')) pages.push(path.relative(site, p).replace(/\\/g, '/')); } })(site);
pages.sort();
// the walkthrough: the four parts and the deep links, captured when --shots is given
const walkthrough = [
  ['index.html', '01_landing'], ['learn/index.html', '02_learn'], ['studio.html#learn', '03_studio_learn'],
  ['studio.html#learn=B2', '04_studio_lesson_B2'], ['studio.html#design', '05_studio_design'],
  ['board/index.html', '06_board'], ['docs/rules/index.html', '09_docs_rules'], ['discuss/index.html', '10_discuss'],
  ['language/index.html', '11_language'], ['rules/index.html', '12_rules'],
];
const firstBoard = pages.find(p => /^board\/[^/]+\/index\.html$/.test(p));
if (firstBoard) walkthrough.push([firstBoard, '07_board_task']);
const firstEntry = pages.find(p => /^board\/[^/]+\/\d\d_.*\.html$/.test(p));
if (firstEntry) walkthrough.push([firstEntry + '#step=12', '08_board_entry']);

const results = [];
let failed = 0;
async function visit(rel, shotName) {
  if (ONLY && !rel.includes(ONLY)) return;
  const url = BASE + rel;
  const isStudio = /studio\.html|\/\d\d_[^/]*\.html/.test(rel);
  let probe = null, problems = [];
  try {
    await open(url);
    // a redirect page (design/) lands on the studio: give the second load its time
    const redirect = /http-equiv="refresh"/.test(fs.readFileSync(path.join(site, rel.split('#')[0]), 'utf8'));
    const embeds = /iframe class="live"/.test(fs.readFileSync(path.join(site, rel.split('#')[0]), 'utf8'));
    if (isStudio || redirect || embeds) await new Promise(r => setTimeout(r, redirect ? 2500 : embeds ? 3000 : 800));
    if (shotName) await shot(shotName);
    probe = await evaluate(PROBE);
    // a page with runnable examples: the first Run button must bring up a moving embed
    if (await evaluate("!!document.querySelector('button.run')")) {
      await evaluate("document.querySelector('button.run').click()");
      await new Promise(r => setTimeout(r, 3500));
      const ran = await evaluate(`(async function(){ var f = document.querySelector('.runbox iframe.live'); if(!f) return 'no iframe';
        function st(){ try { var d = f.contentDocument; if(!(d && d.body && d.body.getAttribute('data-embed') === '1')) return false; var e = d.getElementById('status'); return e ? e.textContent : 'no status'; } catch(e){ return false; } }
        var a = st(); await new Promise(function(r){ setTimeout(r, 1500); }); var b = st(); if(a === false) return 'not in embed mode'; var m = /\\/ (\\d+)/.exec(a || ''); return (a !== b || (m && +m[1] <= 3)) ? 'moving' : 'still: ' + a; })()`);
      if (ran !== 'moving') problems.push('the first example did not run: ' + ran);
    }
    if (!probe.nav) problems.push('no navigation bar');
    if (probe.search_hits < 1) problems.push('site search finds nothing for "steane"');
    if (probe.logos === false) problems.push('a footer logo did not load');
    if (probe.embed === false) problems.push('the embedded page did not enter embed mode');
    if (probe.embed && probe.embed_moving === false) problems.push('the embedded page is not playing: ' + probe.embed);
    if (probe.wide) problems.push('page scrolls sideways: ' + probe.sw + ' > ' + probe.iw + ' ' + JSON.stringify(probe.over));
    if (rel.includes('studio.html') && probe.ready === false) problems.push('editor not ready');
    if (/#learn(=|$)/.test(rel) && probe.learn_on !== true) problems.push('#learn did not open the Learn pane');
    const m = /#learn=([A-Z]\d+)/.exec(rel);
    if (m && probe.lesson !== m[1]) problems.push(`#learn=${m[1]} loaded lesson ${probe.lesson}`);
    if (/#design$/.test(rel) && probe.learn_on === true) problems.push('#design opened the Learn pane');
  } catch (e) { problems.push(String(e.message || e)); }
  problems.push(...errors);
  const rec = { page: rel, ok: problems.length === 0, title: probe && probe.title, active: probe && probe.active, problems };
  if (!rec.ok) failed++;
  results.push(rec);
  if (!QUIET || !rec.ok) console.log(JSON.stringify(rec));
}
const shotFor = new Map(walkthrough.map(([p, n]) => [p, n]));
for (const rel of pages) await visit(rel, shotFor.get(rel) && !rel.includes('#') ? shotFor.get(rel) : null);
for (const [p, n] of walkthrough) if (p.includes('#')) await visit(p, n);

if (SHOTS) { await open(BASE + 'index.html'); await evaluate("window.SITENAV.search('steane')"); await shot('13_search'); }
ws.close(); chrome.kill(); if (!LIVE) server.close();
try { fs.rmSync(udd, { recursive: true, force: true }); } catch {}
console.log(JSON.stringify({ pages: results.length, failed, shots: SHOTS ? walkthrough.length + 1 : 0 }));
process.exit(failed ? 1 : 0);
