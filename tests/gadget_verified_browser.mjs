// A verified algorithm page in headless Chrome: node tests/gadget_verified_browser.mjs <build dir>
//
// Opens <build dir>/index.html (a `qccd.gadget.verified.build_algorithm` output) from file://
// and prints one JSON line with what it saw:
//   errors     console errors and uncaught exceptions (must be none)
//   town       the top level: HUD line, legend categories, frame time, timeline rows
//   signoff    the Verification panel's text
//   place      drilled into the busiest place while its op runs: HUD step, inspector logic column
//   library    the library overlay's category headings
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { pathToFileURL } from "node:url";

const dir = path.resolve(process.argv[2]);
const CHROME = process.env.CHROME || [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "/usr/bin/google-chrome", "/usr/bin/chromium-browser", "/usr/bin/chromium",
].find((p) => fs.existsSync(p));
if (!CHROME) { console.error("no Chrome found; set CHROME"); process.exit(2); }

const port = 9950 + Math.floor(Math.random() * 40);
const udd = fs.mkdtempSync(path.join(os.tmpdir(), "qccd-verified-"));
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

const out = { errors };
try {
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: pathToFileURL(path.join(dir, "index.html")).href });
  let ready = false;
  for (let i = 0; i < 200 && !ready; i++) {
    try { ready = await evaluate("!!(window.GADGETS && window.GADGETS.ready())"); } catch (e) { /* loading */ }
    if (!ready) await new Promise((r) => setTimeout(r, 100));
  }
  out.ready = ready;
  out.town = await evaluate(`(() => { const G = window.GADGETS; G.go(""); G.seek(G.M.makespan * 0.3); G.fit();
    const t0 = performance.now(); G.drawStage(); G.drawTimeline(); const ms = performance.now() - t0;
    const legend = [...document.querySelectorAll("#legend .lg-cats span")].map((s) => s.textContent);
    const shapes = Object.fromEntries(Object.entries(G.CATS).map(([k, c]) => [k, c.shape]));
    return { what: document.getElementById("hudWhat").textContent, legend, ms, shapes,
             distinct: new Set(Object.values(shapes)).size, categories: Object.keys(G.CATS).length }; })()`);
  out.signoff = await evaluate(`document.getElementById("verifBody").innerText`);
  out.place = await evaluate(`(() => { const G = window.GADGETS; const M = G.M;
    const e = M.gir.events.filter((e) => M.masters[M.gir.leaves[e[3]][0]].family !== "road" && M.masters[M.gir.leaves[e[3]][0]].family !== "depot")
      .sort((a, b) => (b[2] - b[1]) - (a[2] - a[1]))[0];
    G.go(e[3]); G.seek(e[1] + (e[2] - e[1]) * 0.5); G.fit(); G.drawStage();
    G.select({ kind: "inst", path: e[3] });
    return { path: e[3], op: e[4], step: document.getElementById("hudStep").textContent,
             what: document.getElementById("hudWhat").textContent,
             inspector: document.getElementById("inspBody").innerText }; })()`);
  out.library = await evaluate(`(() => { const G = window.GADGETS; G.go(""); G.renderLibrary();
    return [...document.querySelectorAll("#library .libcat")].map((h) => h.textContent.trim()); })()`);
} catch (e) {
  errors.push(String(e && e.stack || e));
} finally {
  console.log(JSON.stringify(out));
  ws.close();
  chrome.kill();
}
