// The Debug page in a real browser: open a program, step, watch, check rules, see the crosses.
//
//   node tests/debug_page.mjs <site dir> <studio page under it> [--csp "<policy>"] [--shots <dir>]
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
const site = argv[0], page = argv[1];
const opt = (k) => { const i = argv.indexOf(k); return i >= 0 ? argv[i + 1] : null; };
const CSP = opt('--csp'), SHOTS = opt('--shots');
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
await new Promise((r) => server.listen(0, '127.0.0.1', r));
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
try {
  await send('Page.navigate', { url: `http://127.0.0.1:${server.address().port}/debug/` });
  for (let i = 0; i < 100 && !(await ev('!!window.QCCD_DEBUG').catch(() => false)); i++) await new Promise((r) => setTimeout(r, 100));
  const offered = await ev(`QCCD_DEBUG.sources({}).programs.map(function (p) { return p.src; })`);
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
} catch (e) { fails.push('error: ' + e.message); }
out.errors = errors.slice(0, 5);
if (errors.length) fails.push('page errors: ' + errors[0]);
out.fails = fails;
console.log(JSON.stringify(out));
ws.close(); chrome.kill(); server.close();
process.exit(fails.length ? 1 : 0);
