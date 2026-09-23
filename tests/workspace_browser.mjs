// Drive a LIVE workspace Studio page in real headless Chrome over the DevTools protocol.
//
//   node tests/workspace_browser.mjs <script.json>   ->  one JSON line on stdout
//
// No npm packages: Chrome is launched with --remote-debugging-port and spoken to over
// Node's global WebSocket.  The script is
//   {"chrome": "<path?>", "pages": {"a": "<url>", ...}, "steps": [ ... ]}
// and each step is one of
//   {"page": "a", "eval": "<js expression>"}                     record the value
//   {"page": "a", "frame": "<url part>", "eval"|"wait": ...}     the same, inside that frame
//   {"page": "a", "wait": "<js predicate>", "timeout": 15000}    poll until truthy
//   {"page": "a", "shot": "<png path>"}
//   {"sleep": 500}
//   {"http": {"method": "POST", "url": "...", "headers": {}, "body": {}}}   a raw request
// Every page is its own tab (its own view), so multi-tab behaviour is testable.
import { spawn } from 'child_process';
import fs from 'fs';
import os from 'os';
import path from 'path';

const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const CHROME = spec.chrome || process.env.CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const port = 9300 + Math.floor(Math.random() * 500);
const prof = fs.mkdtempSync(path.join(os.tmpdir(), 'qccd-cdp-'));
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${prof}`,
  '--disable-extensions', '--no-first-run', '--no-default-browser-check', '--window-size=1500,950', 'about:blank'],
  { stdio: 'ignore' });
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function json(u) { const r = await fetch(u); return r.json(); }
async function target() {
  for (let i = 0; i < 100; i++) {
    try { await json(`http://127.0.0.1:${port}/json/version`); return; } catch (e) { await sleep(100); }
  }
  throw new Error('chrome did not start');
}
class Tab {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); this.logs = []; this.net = []; this.ctx = new Map();
    ws.onmessage = m => { const d = JSON.parse(m.data);
      if (d.id && this.pending.has(d.id)) { this.pending.get(d.id)(d); this.pending.delete(d.id); }
      else if (d.method === 'Runtime.executionContextCreated') {
        const c = d.params.context; if (c.auxData && c.auxData.isDefault) this.ctx.set(c.auxData.frameId, c.id); }
      else if (d.method === 'Runtime.consoleAPICalled') this.logs.push(d.params.type + ': ' + d.params.args.map(a => a.value ?? a.description).join(' '));
      else if (d.method === 'Runtime.exceptionThrown') this.logs.push('EXC: ' + JSON.stringify(d.params.exceptionDetails.exception?.description || d.params.exceptionDetails.text));
      else if (d.method === 'Log.entryAdded' && d.params.entry.level !== 'verbose') {
        // CSP and other browser messages with the console; failed requests apart (a test may provoke them)
        const e = d.params.entry, line = 'LOG ' + e.level + ': ' + e.text + (e.url ? ' @ ' + e.url : '');
        (e.source === 'network' ? this.net : this.logs).push(line);
      }
    }; }
  send(method, params = {}) { const id = ++this.id; this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise(r => this.pending.set(id, r)); }
  async frameCtx(part) {
    const t = await this.send('Page.getFrameTree');
    const find = n => (n.frame.url.includes(part) ? n.frame.id : (n.childFrames || []).map(find).find(Boolean));
    const id = t.result && find(t.result.frameTree);
    if (!id || !this.ctx.has(id)) throw new Error('no frame matching ' + part);
    return this.ctx.get(id);
  }
  async eval(expr, frame) {
    const p = { expression: expr, awaitPromise: true, returnByValue: true };
    if (frame) { try { p.contextId = await this.frameCtx(frame); } catch (e) { return { error: String(e.message) }; } }
    const r = await this.send('Runtime.evaluate', p);
    if (r.result?.exceptionDetails) return { error: r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text };
    return { value: r.result?.result?.value };
  }
}
async function open(url) {
  const t = await (await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(url)}`, { method: 'PUT' })).json();
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise(r => ws.onopen = r);
  const tab = new Tab(ws);
  await tab.send('Runtime.enable'); await tab.send('Page.enable'); await tab.send('Log.enable');
  return tab;
}

const out = { steps: [], logs: {}, net: {} };
try {
  await target();
  const tabs = {};
  for (const [k, u] of Object.entries(spec.pages || {})) { tabs[k] = await open(u); }
  for (const step of spec.steps) {
    const rec = { step };
    const tab = tabs[step.page || 'a'];
    try {
      if (step.sleep) await sleep(step.sleep);
      else if (step.open) { tabs[step.open] = await open(step.url); }
      else if (step.eval) { Object.assign(rec, await tab.eval(step.eval, step.frame)); }
      else if (step.wait) {
        const t0 = Date.now(); let last;
        while (Date.now() - t0 < (step.timeout || 15000)) {
          last = await tab.eval(step.wait, step.frame);
          if (last.value) break;
          await sleep(150);
        }
        rec.ok = !!(last && last.value); rec.ms = Date.now() - t0; if (last && last.error) rec.error = last.error;
      }
      else if (step.shot) {
        const r = await tab.send('Page.captureScreenshot', { format: 'png' });
        fs.writeFileSync(step.shot, Buffer.from(r.result.data, 'base64')); rec.ok = true;
      }
      else if (step.http) {
        const h = step.http; const r = await fetch(h.url, { method: h.method || 'GET', headers: h.headers || {},
          body: h.body === undefined ? undefined : JSON.stringify(h.body) });
        rec.status = r.status; rec.body = await r.text();
      }
    } catch (e) { rec.error = String(e && e.stack || e); }
    out.steps.push(rec);
    if (step.stopOnFail && (rec.error || rec.ok === false)) break;
  }
  for (const [k, t] of Object.entries(tabs)) { out.logs[k] = t.logs.slice(-40); out.net[k] = t.net.slice(-40); }
} catch (e) { out.fatal = String(e && e.stack || e); }
finally {
  chrome.kill();
  try { fs.rmSync(prof, { recursive: true, force: true }); } catch (e) { /* locked on Windows */ }
}
console.log(JSON.stringify(out));
