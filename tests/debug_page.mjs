// The Debug page in a real browser: open a program, step, watch, check rules, see the crosses.
//
//   node tests/debug_page.mjs <site dir> <studio page under it> [--csp "<policy>"] [--shots <dir>]
//   node tests/debug_page.mjs --base https://qccd.academy/ <studio page>      (the live site)
//
// Serves the directory (with the MIME types a module worker and WebAssembly need), opens
// /debug/ in headless Chrome over the DevTools protocol (no npm) and drives the page the way
// an agent does, through window.QCCD_DEBUG.  Prints one JSON line of what it measured and exits
// 0 when every expectation held.  `--csp` serves every file under a Content-Security-Policy
// (the workspace mirror's), so the page is held to the policy it will meet there.
// tests/test_debug.py runs it.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { spawn } from 'node:child_process';
import { findChrome } from './chrome_path.mjs';

const argv = process.argv.slice(2);
const opt = (k) => { const i = argv.indexOf(k); return i >= 0 ? argv[i + 1] : null; };
const CSP = opt('--csp'), SHOTS = opt('--shots'), BASE = opt('--base');
const pos = argv.filter((a, i) => !a.startsWith('--') && !(i > 0 && argv[i - 1].startsWith('--')));
const site = BASE ? null : pos[0], page = BASE ? pos[0] : pos[1];
// served as qccd.academy's nginx (1.18) serves them: no type for .mjs or .wasm (application/octet-stream),
// so the page is held to working from a server that knows neither
const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript',
  '.json': 'application/json', '.zip': 'application/zip', '.css': 'text/css',
  '.png': 'image/png', '.svg': 'image/svg+xml', '.ico': 'image/x-icon' };
const server = http.createServer((req, res) => {
  let p = decodeURIComponent(new URL(req.url, 'http://x').pathname);
  if (p.endsWith('/')) p += 'index.html';
  fs.readFile(path.join(site, p), (err, data) => {
    if (err) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { 'Content-Type': MIME[path.extname(p)] || 'application/octet-stream', ...(CSP ? { 'Content-Security-Policy': CSP } : {}) });
    res.end(data);
  });
});
if (!BASE) await new Promise((r) => server.listen(0, '127.0.0.1', r));
const CHROME = findChrome();
if (!CHROME) { console.error('no Chrome found; set CHROME'); process.exit(2); }
const cport = 9500 + Math.floor(Math.random() * 400);
const udd = fs.mkdtempSync(path.join(os.tmpdir(), 'qccd-debug-'));
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${cport}`, `--user-data-dir=${udd}`,
  '--disable-extensions', '--no-first-run', '--window-size=1600,1000', 'about:blank'], { stdio: 'ignore' });
let list = null;
for (let i = 0; i < 150 && !list; i++) {
  try { list = await (await fetch(`http://127.0.0.1:${cport}/json/list`)).json(); } catch { await new Promise((r) => setTimeout(r, 100)); }
}
const ws = new WebSocket(list.find((t) => t.type === 'page').webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let seq = 0; const wait = {}; const errors = [];
ws.onmessage = (m) => {
  const d = JSON.parse(m.data);
  if (d.id && wait[d.id]) { wait[d.id](d); delete wait[d.id]; }
  if (d.method === 'Runtime.exceptionThrown') errors.push(d.params.exceptionDetails.exception?.description || d.params.exceptionDetails.text);
};
const send = (method, params = {}) => new Promise((r) => { const id = ++seq; wait[id] = r; ws.send(JSON.stringify({ id, method, params })); });
const ev = async (expr) => {
  const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
  if (r.result.exceptionDetails) throw new Error(expr.slice(0, 60) + ': ' + (r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text));
  return r.result.result.value;
};
const shot = async (name) => {
  if (!SHOTS) return;
  const r = await send('Page.captureScreenshot', { format: 'png' });
  fs.mkdirSync(SHOTS, { recursive: true });
  fs.writeFileSync(path.join(SHOTS, name), Buffer.from(r.result.data, 'base64'));
};
await send('Runtime.enable'); await send('Page.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });

const out = {}, fails = [];
const expect = (ok, what) => { if (!ok) fails.push(what); };
const INPAGE = argv.includes('--inpage');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const until = async (expr, ms = 30000) => {
  for (const t0 = Date.now(); Date.now() - t0 < ms; await sleep(150)) if (await ev(expr).catch(() => false)) return true;
  return false;
};
if (INPAGE) {
  // the debugger in the Studio page itself: its Debug button, the panel beside the program
  try {
    const url = BASE ? new URL(page, BASE).href : `http://127.0.0.1:${server.address().port}/${page}`;
    await send('Page.navigate', { url });
    expect(await until('!!(window.EDITOR && EDITOR.ready && EDITOR.ready())'), 'the Studio page booted');
    expect(await ev("!!document.getElementById('qdbgBtn')"), 'the Studio\'s transport has a Debug button');
    const w0 = await ev("document.querySelector('main').getBoundingClientRect().width");
    await ev('seek(2, {})');
    // an agent's verb before the panel exists: it opens the panel and runs
    const st = await ev('QCCD_DEBUG.status()');
    out.status = { host: st.host, open: st.panel_open, step: st.step };
    expect(st.panel_open === true && st.step === 3, 'the first verb opened the panel at the reader\'s step (3)');
    const w1 = await ev("document.querySelector('main').getBoundingClientRect().width");
    expect(w1 < w0 - 300, 'the Studio gave the panel its width');
    const chk = await ev("QCCD_DEBUG.check({rules: 'standard', assertions: 'for all s in sites, t in instants: occupancy(s, t) <= 1'})");
    out.failing = chk.failing; out.check_seconds = chk.seconds;
    expect(Array.isArray(chk.failing) && chk.failing.indexOf('A1') >= 0, 'the assertion fails');
    const nf = await ev('QCCD_DEBUG.next_failure()');
    await sleep(300);
    out.crosses = await ev("document.querySelectorAll('#qdbg .qdbg-x').length");
    expect(nf.step !== null && out.crosses > 0, 'the failure is crossed out on the Studio\'s own picture');
    expect(/#debug&step=/.test(await ev('QCCD_DEBUG.link().link')), 'the link reopens this page in debug mode');
    await shot('debug_inpage.png');
    // a review, as an agent does one: the rules, the first failure's cases, a note, a near miss
    const rv = await ev('QCCD_DEBUG.review()');
    const r7 = rv.rules.find((r) => r.id === 'R7');
    expect(r7 && r7.verdict === 'fails' && r7.first_step === 7 && !!r7.fix, 'review gives R7, the step it first fails at and the guide\'s fix');
    await ev('QCCD_DEBUG.goto({step: 7})');
    await sleep(250);
    out.banner = await ev("(document.getElementById('dErr') || {}).textContent || ''");
    expect(/R7 broken at step 7/.test(out.banner) && !(await ev("document.getElementById('dErr').hidden")), 'stepping onto the failure stops on it, as on an exception');
    const ins = await ev("QCCD_DEBUG.inspect({rule: 'R7', step: 7})");
    expect(ins && ins.failing === 1 && ins.shown.length === 2, 'inspect gives both cases of R7 at step 7');
    expect(await until("document.querySelectorAll('#pWhy .q-case').length === 2", 10000), 'the Why tab shows the person the same two cases');
    out.gauges = await ev("document.querySelectorAll('#qdbg .qdbg-gauge').length");
    expect(out.gauges >= 1, 'a gauge of heat against its budget is drawn on the picture');
    const nt = await ev("QCCD_DEBUG.note({step: 7, rule: 'R7', text: 'q0 enters the MS gate hot: cool it first', by: 'Test agent'})");
    expect(nt.note && nt.note.step === 7, 'a note is pinned to step 7');
    await sleep(200);
    await shot('debug_review_step7.png');
    expect(await ev("QCCD_DEBUG.report().markdown.indexOf('q0 enters the MS gate hot') >= 0"), 'the note is in the report');
    const cl = await ev("QCCD_DEBUG.closest({rule: 'R13'})");
    expect(cl.found && cl.room === 13, 'the closest case of R13 has room 13');
    const why = await ev("QCCD_DEBUG.rules_at({step: 6})");
    expect(why.rules.some((r) => r.id === 'R7' && r.here === 'nothing to judge'), 'every rule at step 6: R7 judges nothing there');
    await shot('debug_review.png');
    // on the website itself there is no chat: the button says how to open this page with one's agent
    await ev("document.querySelector('#dTabs [data-tab=rules]').click(), document.getElementById('rAgent').click(), true");
    const help = await ev("(document.querySelector('.q-agenthelp a') || {}).href || ''");
    out.agent_link = help;
    expect(help.indexOf('http://127.0.0.1:47100/web/' + page) === 0,
           'without a chat, Review with my agent links this page through the person\'s workspace');
    await ev('QCCD_DEBUG.goto({step: 7})');
    await ev("document.querySelector('[data-rule=R7].q-rbtn').click(), document.getElementById('eFix').click(), true");
    await sleep(200);
    expect(await ev("!!document.querySelector('#dErr .q-guide')"), 'How to fix it shows the guide\'s row in the error');
    await shot('debug_rules_agent.png');
    await ev("document.getElementById('qdbgBtn').click()");
    expect(await until("!document.getElementById('dbg') && !document.getElementById('qdbg')", 5000), 'the Debug button closes the panel and its marks');
    expect(await ev("document.querySelector('main').getBoundingClientRect().width") === w0, 'the Studio has its width back');
    await ev("document.getElementById('qdbgBtn').click()");
    expect(await until("!!document.getElementById('dbg') && QCCD_DEBUG_INPAGE.active", 10000), 'and opens it again');
  } catch (e) { fails.push('error: ' + e.message); }
  out.errors = errors.slice(0, 5);
  if (errors.length) fails.push('page errors: ' + errors[0]);
  out.fails = fails;
  console.log(JSON.stringify(out));
  ws.close(); chrome.kill(); if (!BASE) server.close();
  process.exit(fails.length ? 1 : 0);
}
try {
  await send('Page.navigate', { url: BASE ? new URL('debug/', BASE).href : `http://127.0.0.1:${server.address().port}/debug/` });
  for (let i = 0; i < 100 && !(await ev('!!window.QCCD_DEBUG').catch(() => false)); i++) await new Promise((r) => setTimeout(r, 100));
  const offered = await ev(`QCCD_DEBUG.sources({limit: 100000}).programs.map(function (p) { return p.src; })`);
  expect(offered.indexOf(page) >= 0, `the Open list offers ${page}`);
  const opened = await ev(`QCCD_DEBUG.open({src: ${JSON.stringify(page)}})`);
  out.steps = opened.steps;
  expect(opened.steps > 2, 'the program opened with its steps');
  const s3 = await ev('QCCD_DEBUG.goto({step: 3})');
  const back = await ev('QCCD_DEBUG.step({delta: -1})');
  out.stepped = [s3.step, back.step];
  expect(s3.step === 3 && back.step === 2, 'goto and one step back');
  expect(await ev("document.getElementById('dFrame').contentWindow.__QCCD_DEBUG_BRIDGE.frame") === 1, 'the Studio\'s own animation followed (frame 1 = step 2)');
  const ion = await ev("Object.keys(document.getElementById('dFrame').contentWindow.__QCCD_DEBUG_BRIDGE.states[0].pos)[0]");
  const w = await ev(`QCCD_DEBUG.watch({add: [${JSON.stringify(ion)}]})`);
  expect(w.watch.length === 1 && w.watch[0].kind === 'ion', 'an ion is watched');
  const tr = await ev(`QCCD_DEBUG.trace({entity: ${JSON.stringify(ion)}})`);
  expect(tr.events.length >= 1, 'its trace has events');
  const chk = await ev("QCCD_DEBUG.check({rules: 'standard', assertions: 'for all s in sites, t in instants: occupancy(s, t) <= 1'})");
  out.failing = chk.failing; out.check_seconds = chk.seconds;
  expect(Array.isArray(chk.failing) && chk.failing.indexOf('A1') >= 0, 'the assertion fails (two ions share a site somewhere)');
  const nf = await ev('QCCD_DEBUG.next_failure()');
  await new Promise((r) => setTimeout(r, 300));
  out.crosses = await ev("document.getElementById('dFrame').contentDocument.querySelectorAll('#qdbg .qdbg-x').length");
  expect(nf.step !== null && out.crosses > 0, 'the next failure is crossed out on the picture');
  await shot('debug_failure.png');
  const f = await ev(`QCCD_DEBUG.find({condition: 'occupancy("${await ev("QCCD_DEBUG.failures({rule: 'A1', limit: 1}).failures[0].where[0].id")}", t) >= 2', backward: true, go: false})`);
  out.find = f;
  expect(f.found === true || f.found === false, 'a conditional breakpoint is answered');
  const rep = await ev('QCCD_DEBUG.report()');
  expect(/src=/.test(rep.link) && /step=/.test(rep.link) && rep.markdown.indexOf('A1') >= 0, 'the report links back to exactly here');
  // a review on the Debug page: the same verbs, the same panels, the gauges on the framed picture
  const ins = await ev("QCCD_DEBUG.inspect({rule: 'R7', step: 7})");
  expect(ins && ins.failing === 1, 'inspect gives R7\'s failing case at step 7');
  expect(await until("document.querySelectorAll('#pWhy .q-case').length === 2", 10000), 'the Why tab shows both cases');
  out.banner = await ev("(document.getElementById('dErr') || {}).textContent || ''");
  expect(/R7 broken at step 7/.test(out.banner), 'the error is said over the stage');
  out.gauges = await ev("document.getElementById('dFrame').contentDocument.querySelectorAll('#qdbg .qdbg-gauge').length");
  expect(out.gauges >= 1, 'a gauge is drawn on the framed picture');
  await shot('debug_review_page.png');
} catch (e) { fails.push('error: ' + e.message); }
out.errors = errors.slice(0, 5);
if (errors.length) fails.push('page errors: ' + errors[0]);
out.fails = fails;
console.log(JSON.stringify(out));
ws.close(); chrome.kill(); if (!BASE) server.close();
process.exit(fails.length ? 1 : 0);
