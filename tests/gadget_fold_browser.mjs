// A folded run (GIR 0.3, qccd/gadget/fold.py) on the Design canvas, in headless Chrome:
//     node tests/gadget_fold_browser.mjs <folded build dir>
//
// The page never holds a folded run whole: it loads each gadget's macro from `macro/<id>.js`
// when the time on screen needs it and draws a window of the schedule around the playhead.
// This asks the page, at several times across the run, what it is showing:
//
//   boot       the first window, before any seek
//   seeks      per time asked for: the window it drew, whether every macro it needed had
//              arrived, how many calls it expanded, what runs at the playhead, the
//              instruction the Stack describes and its call chain, the program line lit
//   played     after playing across a window's edge: the playhead moved on, and the
//              window moved with it
//   errors     console errors and uncaught exceptions (must be none)
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { pathToFileURL } from "node:url";
import { findChrome } from "./chrome_path.mjs";

const dir = path.resolve(process.argv[2]);
const CHROME = findChrome();
if (!CHROME) { console.error("no Chrome found; set CHROME"); process.exit(2); }

const port = 9940 + Math.floor(Math.random() * 40);
const udd = fs.mkdtempSync(path.join(os.tmpdir(), "qccd-fold-"));
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${udd}`,
  "--disable-extensions", "--no-first-run", "--no-default-browser-check", "--window-size=1600,1000",
  "--allow-file-access-from-files", "about:blank"], { stdio: "ignore" });
const getJSON = (u) => new Promise((res, rej) => http.get(u, (r) => { let s = ""; r.on("data", (d) => (s += d)); r.on("end", () => { try { res(JSON.parse(s)); } catch (e) { rej(e); } }); }).on("error", rej));
let targets = null;
for (let i = 0; i < 100 && !targets; i++) {
  try { const t = await getJSON(`http://127.0.0.1:${port}/json/list`); if (t.length) targets = t; } catch (e) { /* not up yet */ }
  if (!targets) await new Promise((r) => setTimeout(r, 100));
}
const ws = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r, { once: true }));
let seq = 0;
const pending = new Map();
const errors = [];
ws.addEventListener("message", (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  if (msg.method === "Runtime.exceptionThrown") errors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
  if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") errors.push(msg.params.args.map((a) => a.value ?? a.description).join(" "));
});
const send = (method, params = {}) => new Promise((resolve) => { const id = ++seq; pending.set(id, resolve); ws.send(JSON.stringify({ id, method, params })); });
const evaluate = async (expr) => {
  const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.result.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text);
  return r.result.result.value;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const LOOK = `(() => { const G = window.GADGETS, M = G.M, w = M.gir.window || {};
  const ins = M.gir.program.instructions;
  const live = M.activeInstructions(G.S.t).filter((i) => !ins[i].refresh);
  const f = live.length ? ins[Math.min(...live)] : null;
  return { t: G.S.t, a: w.a, b: w.b, missing: w.missing, calls: w.calls, makespan: M.makespan,
    events: M.gir.events.length,
    running: M.gir.events.filter((e) => e[1] <= G.S.t && G.S.t < e[2]).length,
    // every row knows the module line it is written on: [gadget or null, kind, id]
    unplaced: M.gir.events.filter((e) => !e.hw || (e.hw[0] !== null && !(M.gir.hw || {}).gadgets[e.hw[0]])).length,
    inGadgets: M.gir.events.filter((e) => e.hw && e.hw[0] !== null).length,
    focus: f ? { text: f.text, chain: (f.call || []).map((c) => c.gate), line: f.line, src: f.src || null } : null,
    lit: [...document.querySelectorAll("#progBody .pl.live")].length,
    stack: (document.getElementById("stkNow") || {}).innerText || "" };
})()`;

const out = { errors, seeks: [] };
try {
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: pathToFileURL(path.join(dir, "index.html")).href });
  let ready = false;
  for (let i = 0; i < 200 && !ready; i++) {
    try { ready = await evaluate("!!(window.GADGETS && window.GADGETS.ready())"); } catch (e) { /* loading */ }
    if (!ready) await sleep(100);
  }
  out.boot = await evaluate(LOOK);
  const T = out.boot.makespan;
  // across the run, and into the middle of its first call and of its longest
  const inCalls = await evaluate(`(() => { const F = window.GADGETS.FOLD, cs = F.g.calls;
    const mid = (c) => c.t0 + 0.5 * F.spanOf(c);
    const longest = cs.reduce((a, c) => (F.spanOf(c) > F.spanOf(a) ? c : a), cs[0]);
    return cs.length ? [mid(cs[0]), mid(longest)] : []; })()`);
  for (const at of [0.1 * T, 0.35 * T, 0.6 * T, 0.85 * T, ...inCalls]) {
    await evaluate(`(() => { const G = window.GADGETS; G.S.playing = false; G.seek(${at}); })()`);
    let r;
    for (let k = 0; k < 40; k++) { await sleep(150); r = await evaluate(LOOK); if (!r.missing) break; }
    await sleep(400);                       // the panels refresh every 180 ms
    out.seeks.push(Object.assign({ asked: at }, await evaluate(LOOK)));
  }
  await evaluate(`(() => { const G = window.GADGETS; G.seek(${0.2 * T}); G.S.rate = 2e6; G.S.playing = true; })()`);
  await sleep(1500);
  out.played = await evaluate(LOOK);
  await evaluate(`window.GADGETS.S.playing = false`);
} catch (e) {
  out.fatal = String(e && e.stack || e);
}
ws.close();
chrome.kill();
console.log(JSON.stringify(out));
process.exit(0);
