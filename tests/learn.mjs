// THE LEARN PAGES, in a real headless Chrome: the glossary card opens on hover with the word's
// explanation and a link to its definition; a poster, pressed, loads the Studio page and the
// programme PLAYS (the slider moves on its own); the hardware labels are drawn in the embed,
// readable; the story strip, the summary and the recap are on every page.
//
//   node tests/learn.mjs <site dir> [--base <url>] [--only <page>] [--shots <dir>]
//
// Exit 1 on any failed probe, uncaught exception or console error.  No npm: Chrome speaks
// the DevTools protocol over Node's built-in WebSocket (as tests/site.mjs does).
import fs from 'fs';
import http from 'http';
import os from 'os';
import path from 'path';
import { spawn } from 'child_process';
import { findChrome } from './chrome_path.mjs';

const args = process.argv.slice(2);
const site = path.resolve(args[0] || 'site');
const opt = (k) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : null; };
const LIVE = opt('--base'), ONLY = opt('--only'), SHOTS = opt('--shots');
const PAGES = ['physics/', 'machine/', 'language/', 'rules/', 'compilation/', 'evaluation/'].filter(p => !ONLY || p.includes(ONLY));
const CHROME = findChrome();
if (!CHROME) { console.error('no Chrome found; set CHROME'); process.exit(2); }

const MIME = { '.html': 'text/html; charset=utf-8', '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml', '.css': 'text/css', '.js': 'text/javascript' };
const server = http.createServer((req, res) => {
  let u = decodeURIComponent(req.url.split('?')[0].split('#')[0]);
  if (u.startsWith('/api/')) { res.writeHead(200, { 'content-type': 'application/json' }); return res.end('{"user":null,"progress":null}'); }
  let p = path.join(site, u);
  if (u.endsWith('/')) p = path.join(p, 'index.html');
  if (!fs.existsSync(p) || fs.statSync(p).isDirectory()) { res.writeHead(404); return res.end('not found'); }
  res.writeHead(200, { 'content-type': MIME[path.extname(p)] || 'application/octet-stream' });
  fs.createReadStream(p).pipe(res);
});
if (!LIVE) await new Promise(r => server.listen(0, '127.0.0.1', r));
const BASE = LIVE ? LIVE.replace(/\/?$/, '/') : `http://127.0.0.1:${server.address().port}/`;

const port = 9400 + Math.floor(Math.random() * 400);
const udd = fs.mkdtempSync(path.join(os.tmpdir(), 'qccd-learn-'));
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${udd}`,
  '--disable-extensions', '--no-first-run', '--window-size=1440,900', '--hide-scrollbars', 'about:blank'], { stdio: 'ignore' });
const getJSON = (u) => new Promise((res, rej) => http.get(u, r => { let s = ''; r.on('data', d => s += d); r.on('end', () => { try { res(JSON.parse(s)); } catch (e) { rej(e); } }); }).on('error', rej));
let targets = null;
for (let i = 0; i < 100 && !targets; i++) { try { targets = await getJSON(`http://127.0.0.1:${port}/json/list`); } catch { await new Promise(r => setTimeout(r, 100)); } }
const page = targets.find(t => t.type === 'page');
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
let seq = 0; const pending = new Map(); const listeners = new Map();
ws.onmessage = (ev) => { const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { const { res, rej } = pending.get(m.id); pending.delete(m.id); m.error ? rej(new Error(m.error.message)) : res(m.result); }
  else if (m.method && listeners.has(m.method)) listeners.get(m.method).forEach(f => f(m.params)); };
const send = (method, params = {}) => new Promise((res, rej) => { const id = ++seq; pending.set(id, { res, rej }); ws.send(JSON.stringify({ id, method, params })); });
const on = (method, f) => { if (!listeners.has(method)) listeners.set(method, []); listeners.get(method).push(f); };
await send('Page.enable'); await send('Runtime.enable'); await send('Log.enable');
let errors = [];
on('Runtime.exceptionThrown', p => errors.push('exception: ' + (p.exceptionDetails.exception?.description || p.exceptionDetails.text).split('\n')[0]));
on('Log.entryAdded', p => { if (p.entry.level === 'error') errors.push('console: ' + p.entry.text); });
on('Runtime.consoleAPICalled', p => { if (p.type === 'error') errors.push('console.error: ' + p.args.map(a => a.value || a.description).join(' ')); });
let loaded = null; on('Page.loadEventFired', () => { if (loaded) loaded(); });
async function open(url) {
  errors = [];
  const blank = new Promise(r => { loaded = r; }); await send('Page.navigate', { url: 'about:blank' });
  await Promise.race([blank, new Promise(r => setTimeout(r, 2000))]);
  const p = new Promise(r => { loaded = r; }); await send('Page.navigate', { url });
  await Promise.race([p, new Promise(r => setTimeout(r, 40000))]); loaded = null;
  await new Promise(r => setTimeout(r, 500));
}
async function ev(expr) {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error('probe: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
  return r.result.value;
}
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
async function shot(name) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  const s = await send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(path.join(SHOTS, name), Buffer.from(s.data, 'base64'));
}

let failed = 0;
function check(ok, what) { if (!ok) { failed++; console.log('  FAIL ' + what); } else console.log('  ok   ' + what); }

for (const pg of PAGES) {
  console.log(pg);
  await open(BASE + pg);
  // the furniture
  const f = await ev(`(function(){ var m=document.querySelector('main'); var s=m.querySelector('nav.story');
    return {story: s ? s.querySelectorAll('a').length : 0, current: s ? s.querySelectorAll('a[aria-current=page]').length : 0,
      sumBeforeH2: (function(){ var sum=m.querySelector('.sum'), h2=m.querySelector('h2'); return !!(sum && h2 && (sum.compareDocumentPosition(h2) & Node.DOCUMENT_POSITION_FOLLOWING)); })(),
      recap: !!m.querySelector('.recap'), gl: m.querySelectorAll('a.gl').length, posters: m.querySelectorAll('.runbox .poster').length,
      iframes: m.querySelectorAll('iframe').length}; })()`);
  check(f.story === 6 && f.current === 1, `story strip: 6 pages, this one marked (${f.story}/${f.current})`);
  check(f.sumBeforeH2, 'the summary stands before the first section');
  check(f.recap, 'the recap is there');
  check(f.gl > 10, `glossary marks: ${f.gl}`);
  check((f.posters > 0 || pg === 'evaluation/') && f.iframes === 0, `${f.posters} posters, ${f.iframes} iframes before anyone pressed Play`);
  // the glossary card, on hover
  const term = await ev(`(function(){ var a=document.querySelector('main a.gl'); a.scrollIntoView({block:'center', behavior:'instant'}); var r=a.getBoundingClientRect(); return {x:r.left+r.width/2, y:r.top+r.height/2, key:a.getAttribute('data-gl'), href:a.getAttribute('href')}; })()`);
  await sleep(300);
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: term.x, y: term.y });
  await sleep(400);
  const card = await ev(`(function(){ var c=document.getElementById('glcard'); if(!c) return null; var r=c.getBoundingClientRect();
    return {open:c.getAttribute('data-open'), title:(c.querySelector('b')||{}).textContent, text:(c.querySelector('p')||{}).textContent, link:(c.querySelector('a')||{}).getAttribute && c.querySelector('a').getAttribute('href'), h:r.height, w:r.width, onscreen: r.top>=0 && r.bottom<=innerHeight}; })()`);
  check(card && card.open === '1' && card.title && card.text && card.text.length > 30, `hover on "${term.key}" opens the card: ${card && card.title}`);
  check(card && card.link === term.href && card.onscreen, `the card links to the definition (${term.href}) and is on screen`);
  await shot(pg.replace('/', '') + '-card.png');
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: 5, y: 5 });
  await sleep(400);
  check((await ev(`document.getElementById('glcard').getAttribute('data-open')`)) === '0', 'the card closes when the pointer leaves');
  // a poster, pressed: the page loads and PLAYS.  The Evaluation page has no animations;
  // a page whose first example has nothing to play (p.init alone) is probed on the next one
  if (!f.posters) { check(errors.length === 0, 'no exceptions or console errors'); continue; }
  const k = (pg === 'language/') ? 4 : 0;     // language: p.gate on the register (the first two have nothing to play, or a dense lattice)
  const pos = await ev(`(function(){ var p=document.querySelectorAll('main .runbox .poster')[${k}]; p.scrollIntoView({block:'center', behavior:'instant'}); var r=p.querySelector('.pp').getBoundingClientRect(); return {x:r.left+r.width/2, y:r.top+r.height/2}; })()`);
  await sleep(400);
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: pos.x, y: pos.y });
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: pos.x, y: pos.y, button: 'left', clickCount: 1 });
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: pos.x, y: pos.y, button: 'left', clickCount: 1 });
  await sleep(300);
  const pressed = await ev(`(function(){ var b=document.querySelectorAll('main .runbox')[${k}]; var e=document.elementFromPoint(${pos.x}, ${pos.y});
    return {live: b.getAttribute('data-live'), under: e && (e.tagName + '.' + (e.className && e.className.baseVal !== undefined ? e.className.baseVal : e.className))}; })()`);
  check(pressed.live === '1', `the real click reached the poster (under the pointer: ${pressed.under})`);
  let st = null;
  for (let t = 0; t < 40; t++) {
    await sleep(500);
    st = await ev(`(function(){ var f=document.querySelectorAll('main .runbox')[${k}].querySelector('iframe'); if(!f) return {iframe:false};
      var d; try { d=f.contentDocument; } catch(e){ return {iframe:true, err:String(e)}; }
      if(!d || !d.getElementById('slider')) return {iframe:true, ready:false};
      var w=f.contentWindow, s=d.getElementById('slider'), p=d.getElementById('play');
      // HWT and HWL_SHOWN are top-level const/let of the page's script: bindings of its realm, not window properties
      var labels=[], shown=null; try { labels = w.eval("Object.keys(HWT).map(function(id){ var r=HWT[id].getBoundingClientRect(); return [id, HWT[id].getAttribute('display')||'shown', Math.round(r.height)]; })"); shown = w.eval('HWL_SHOWN'); } catch(e){ labels=['eval failed: '+e]; }
      return {iframe:true, ready:true, v:+s.value, max:+s.max, play:p.textContent, embed:d.body.getAttribute('data-embed'), shown: shown, labels: labels.slice(0,6), poster: !!document.querySelectorAll('main .runbox')[${k}].querySelector('.poster')}; })()`);
    if (st.ready && st.v > 0 && st.poster === false) break;
  }
  check(st && st.iframe && st.ready, 'pressing the poster loads the Studio page in its place' + (st && st.err ? ' (' + st.err + ')' : ''));
  check(st && st.ready && st.v > 0, `the programme plays by itself (slider ${st && st.v} / ${st && st.max}, ${st && st.play})`);
  check(st && st.embed === '1' && st.poster === false, 'the page is in embed mode and the poster is gone');
  check(st && st.shown === true && st.labels && st.labels.length && st.labels.every(l => l[1] === 'shown' && l[2] >= 9),
        `the hardware labels are drawn, readable: ${JSON.stringify(st && st.labels && st.labels.slice(0, 3))}`);
  await shot(pg.replace('/', '') + '-play.png');
  check(errors.length === 0, 'no exceptions or console errors' + (errors.length ? ': ' + errors.slice(0, 3).join(' | ') : ''));
}
console.log(failed ? `\n${failed} probe(s) failed` : '\nall probes passed');
ws.close(); chrome.kill(); server.close();
process.exit(failed ? 1 : 0);
