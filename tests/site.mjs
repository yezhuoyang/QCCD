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
// the rendered-page path probe: `PROBE` is taken under another name because this
// file already has one, and the two ask different questions of the same page
import { PROBE as PATH_PROBE, describe as describePaths, PLANT, UNPLANT }
  from './_rendered_paths.mjs';
import { findChrome } from './chrome_path.mjs';
//: The studio's pane buttons, read out of the page so a new pane is covered the day it is
//: added.  `OPEN_PANES` from the module clicks them all at once, which is the wrong shape
//: here -- see the loop in `visit`.
const TABS = "nav#tabs button.tab, nav.tabs button.tab";
const TAB_COUNT = `document.querySelectorAll('${TABS}').length`;
const CLICK_TAB = (i) =>
  `(function(){ var b = document.querySelectorAll('${TABS}')[${i}];
     if (b) { try { b.click(); } catch (e) { return 'threw'; } return true; } return false; })()`;

const args = process.argv.slice(2);
const site = path.resolve(args[0] || 'site');
const opt = (k) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : null; };
const SHOTS = opt('--shots'), ONLY = opt('--only'), QUIET = args.includes('--quiet');
const LIVE = opt('--base'), RESOLVE = opt('--resolve');
const CALIBRATE = args.includes('--calibrate');
const CHROME = findChrome();
if (!CHROME) { console.error('no Chrome found; set CHROME'); process.exit(2); }

// -------- a static server ------------------------------------------------------------
const MIME = { '.html': 'text/html; charset=utf-8', '.json': 'application/json', '.gif': 'image/gif',
               '.png': 'image/png', '.svg': 'image/svg+xml', '.css': 'text/css', '.js': 'text/javascript' };
const server = http.createServer((req, res) => {
  let u = decodeURIComponent(req.url.split('?')[0].split('#')[0]);
  // the comment layer asks /api/me on every page; this copy has no accounts service, so
  // answer as one with nobody signed in (a 404 would be a console error on every page)
  if (u.startsWith('/api/')) {
    const body = u === '/api/me' ? '{"user":null}' : '{"error":"sign in first"}';
    res.writeHead(u === '/api/me' ? 200 : 401, { 'content-type': 'application/json' }); return res.end(body);
  }
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
  // about:blank fires its own load event; wait it out, or it would stand in for the page's
  // and a slow page (over the network) would be probed before its footer images arrived
  const blank = new Promise(r => { loaded = r; });
  await send('Page.navigate', { url: 'about:blank' });
  await Promise.race([blank, new Promise(r => setTimeout(r, 2000))]);
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

// AN ION IS A COLOURED MARK, NOT A WHITE DISC.  Each ion is a circle with a white halo
// stroke; a stroke is centred on the edge, so once it is as wide as the radius it hides
// half the fill, and at twice the radius all of it.  A crowded trap (16 ions to a QCCDSim
// trap) drew 2 px marks with a 4 px halo, and every ion read as plain white on every
// reproduced QCCDSim page (2026-10-08, found by the user, not by this walk).  The probe
// plants one too-wide halo first and must catch it, so its silence means something.
const HALO = `(function(){
  if (typeof IONP === 'undefined') return null;
  function scan(){ var out = [], n = 0;
    for (var k in IONP){ var c = IONP[k].c;
      if (!c || c.getAttribute('display') === 'none') continue;
      var r = +c.getAttribute('r'), w = +c.getAttribute('stroke-width');
      if (!(r > 0)) continue; n++;
      if (w > r) out.push(k + ': halo ' + w.toFixed(2) + ' on radius ' + r.toFixed(2)); }
    return { n: n, bad: out }; }
  var k0 = null;
  for (var k in IONP){ var c = IONP[k].c;
    if (c && c.getAttribute('display') !== 'none' && +c.getAttribute('r') > 0){ k0 = k; break; } }
  var caught = null;
  if (k0 !== null){ var c0 = IONP[k0].c, old = c0.getAttribute('stroke-width');
    c0.setAttribute('stroke-width', String(3 * +c0.getAttribute('r')));
    caught = scan().bad.some(function(s){ return s.indexOf(k0 + ':') === 0; });
    c0.setAttribute('stroke-width', old); }
  var a = scan();
  // A TRAP SHOWS EVERY PLACE AN ION CAN SIT: one slot ring per unit of capacity (up to the
  // stage's 24), so the free places can be counted.  The rings were clamped at six, and a
  // 16-place trap showed six rings under thirteen ions (2026-10-08, the user).  Planted: a
  // removed ring must be caught.
  function rings(){ var out = [], n = 0;
    if (typeof NODEEL === 'undefined' || typeof nodeById === 'undefined') return { n: 0, bad: out };
    for (var id in NODEEL){ var e = NODEEL[id]; if (!e || e.kind !== 'site' || !e.grp) continue;
      var cap = Math.max(1, Math.trunc((nodeById[id] || {}).cap || 1)), want = cap <= 24 ? cap : 0;
      var got = e.grp.querySelectorAll('circle').length; n++;
      if (got !== want) out.push(id + ': ' + got + ' rings for ' + cap + ' places'); }
    return { n: n, bad: out }; }
  var r0 = null, rcaught = null;
  for (var id in (typeof NODEEL === 'undefined' ? {} : NODEEL)){
    var e = NODEEL[id]; if (e && e.kind === 'site' && e.grp && e.grp.querySelector('circle')){ r0 = e; break; } }
  if (r0){ var victim = r0.grp.querySelector('circle'), parent = victim.parentNode, next = victim.nextSibling;
    parent.removeChild(victim); rcaught = rings().bad.length > 0; parent.insertBefore(victim, next); }
  var rr = rings();
  return { ions: a.n, nbad: a.bad.length, bad: a.bad.slice(0, 3), planted_caught: caught,
           sites: rr.n, nring_bad: rr.bad.length, ring_bad: rr.bad.slice(0, 3), ring_planted_caught: rcaught };
})()`;

const PROBE = `(async function(){
  function embedStatus(){ var f = document.querySelector('.clip iframe.live'); if(!f) return null;
    try { var d = f.contentDocument; if(!(d && d.body && d.body.getAttribute('data-embed') === '1')) return false;
          var st = d.getElementById('status'); return st ? st.textContent : 'no status'; } catch(e){ return false; } }
  var e1 = embedStatus(); if(e1) await new Promise(function(r){ setTimeout(r, 1500); }); var e2 = embedStatus();
  // DID THE PAGE ACTUALLY RENDER, or is it hidden behind something?  Zero console errors
  // says nothing about this: an <svg> appended to <body> inherited the studio's
  // the studio's own svg background rule and painted an opaque sheet over every app page
  // site, silently, and the walk passed it (2026-09-18, found by a reader, not by us).
  // The rule the corpus supports: the ONLY body-level element that may cover three
  // quarters of the viewport is <main>, and it is transparent.  Anything else that big
  // with a painted background is on top of the page.
  var covered = (function(){
    var vw = innerWidth, vh = innerHeight, a = vw * vh, bad = [];
    var kids = document.querySelectorAll('body > *');
    for(var i = 0; i < kids.length; i++){
      var el = kids[i];
      if(el.tagName === 'MAIN' || el.tagName === 'SCRIPT' || el.tagName === 'STYLE') continue;
      var r = el.getBoundingClientRect();
      if(r.width * r.height < 0.75 * a) continue;
      var cs = getComputedStyle(el);
      if(cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) < 0.05) continue;
      var bg = String(cs.backgroundColor || '');
      // NO BACKSLASH ESCAPES HERE: this probe is a template literal, so a regex
      // written /\s/ arrives as /s/ and strips the letter s instead of spaces --
      // which reported every TRANSPARENT overlay as painted (caught against the
      // live board, where the QEC layer reads rgba(0, 0, 0, 0)).
      var flat = bg.split(' ').join('');
      if(!bg || bg === 'transparent' || flat === 'rgba(0,0,0,0)' || flat.indexOf('rgba(') === 0 && flat.slice(-3) === ',0)') continue;
      bad.push(el.tagName + (el.id ? '#' + el.id : '') + ' ' + bg);
    }
    return bad;
  })();
  var nav = document.getElementById('sitenav');
  var cur = nav ? nav.querySelector('a[aria-current="page"]') : null;
  var sw = document.documentElement.scrollWidth, iw = document.documentElement.clientWidth;
  var hits = (window.SITENAV ? window.SITENAV.search('steane').length : -1);
  return { title: document.title, nav: !!nav, covered: covered, active: cur ? cur.textContent : null, search_hits: hits,
           signin: !!document.getElementById('qc-signin'), pins: document.querySelectorAll('.qc-pin').length,
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
  ['compilation/index.html', '13_compilation'],
  ['physics/index.html', '15_physics'], ['people/index.html', '16_people'], ['publications/index.html', '17_publications'],
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
    const halo = await evaluate(HALO);
    if (halo && halo.nbad)
      problems.push(`an ion's halo hides its colour (${halo.nbad} of ${halo.ions}): ${halo.bad.join('; ')}`);
    if (halo && halo.ions && halo.planted_caught !== true)
      problems.push('the halo probe did not see a planted halo: it is blind here');
    if (halo && halo.nring_bad)
      problems.push(`a trap does not show its places (${halo.nring_bad} of ${halo.sites}): ${halo.ring_bad.join('; ')}`);
    // null = no site on the page draws a ring (every one is past 24 places), so there was
    // nothing to remove; the counts above still hold each site to zero rings
    if (halo && halo.sites && halo.ring_planted_caught === false)
      problems.push('the slot-ring probe did not see a planted missing ring: it is blind here');
    // a page with examples shown in place: the first frame must load and enter embed mode
    if (await evaluate("!!document.querySelector('.runbox iframe.live')")) {
      await evaluate("document.querySelector('.runbox iframe.live').scrollIntoView({block: 'center'})");
      await new Promise(r => setTimeout(r, 4500));
      const ran = await evaluate(`(function(){ var f = document.querySelector('.runbox iframe.live');
        try { var d = f.contentDocument; if(!(d && d.body && d.body.getAttribute('data-embed') === '1')) return 'not in embed mode';
              return d.getElementById('play') ? 'ready' : 'no transport'; } catch(e){ return 'unreadable'; } })()`);
      if (ran !== 'ready') problems.push('the first example did not load: ' + ran);
    }
    // WHAT A READER CAN SEE, after every pane is opened -- text behind a tab is text a
    // reader can reach.  `OPEN_PANES` returning 0 on a page that has panes means its
    // selector has gone stale, which is a broken check reading as a clean one, so that is
    // reported rather than passed over.
    // ONE PANE AT A TIME.  Clicking every tab and then looking leaves only the LAST pane
    // open, and the probe skips hidden text -- so a path in any other pane is invisible to
    // it.  That is not hypothetical: the provenance footer lives in the Program pane, and
    // probing after opening all seven found nothing on a page that shows it.
    const panes = await evaluate(TAB_COUNT);
    if (isStudio && !panes) problems.push('no pane buttons found: the path probe opened nothing');
    const paths = [], seenPath = new Set();
    for (let i = 0; i < Math.max(1, panes); i++) {
      if (panes) await evaluate(CLICK_TAB(i));
      for (const h of (await evaluate(PATH_PROBE)) || [])
        if (!seenPath.has(h.path)) { seenPath.add(h.path); paths.push(h); }
    }
    if (paths.length) problems.push(describePaths(paths));
    // --calibrate: prove the probe can SEE, on this page, before its silence is believed.
    // A check that has never caught anything is not known to work; this is how to find out
    // without waiting for a real regression to tell you.
    if (CALIBRATE) {
      await evaluate(PLANT);
      const planted = await evaluate(PATH_PROBE);
      await evaluate(UNPLANT);
      const caught = (planted || []).some(h => h.path.indexOf('does_not_exist') >= 0);
      const foot = await evaluate(`(function(){ var e = document.getElementById('pFoot');
        if (!e) return 'no #pFoot';
        var cs = getComputedStyle(e), box = e.getBoundingClientRect();
        var par = e.parentElement, pcs = par ? getComputedStyle(par) : null;
        return { text: (e.textContent||'').trim().slice(0,90), display: cs.display,
                 vis: cs.visibility, w: box.width, h: box.height,
                 parent: par ? (par.id||par.tagName) : null,
                 parent_display: pcs ? pcs.display : null }; })()`);
      console.log(JSON.stringify({ calibrate: rel, panes: panes,
                                   as_served: (paths || []).map(h => h.path),
                                   planted_caught: caught, pFoot: foot }));
      if (!caught) problems.push('the path probe did not see a planted path: it is blind here');
    }
    if (!probe.nav) problems.push('no navigation bar');
    if (probe.covered && probe.covered.length)
      problems.push('an opaque element covers the page: ' + probe.covered.join(', '));
    if (probe.search_hits < 1) problems.push('site search finds nothing for "steane"');
    // signed out: the bar offers Sign in and the page shows no comment pins
    if (!probe.signin) problems.push('no Sign in control in the bar');
    if (probe.pins) problems.push('comment pins shown to a reader who is not signed in');
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

if (SHOTS) { await open(BASE + 'index.html'); await evaluate("window.SITENAV.search('steane')"); await shot('14_search'); }
ws.close(); chrome.kill(); if (!LIVE) server.close();
try { fs.rmSync(udd, { recursive: true, force: true }); } catch {}
console.log(JSON.stringify({ pages: results.length, failed, shots: SHOTS ? walkthrough.length + 1 : 0 }));
process.exit(failed ? 1 : 0);
