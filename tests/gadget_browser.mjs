// The gadget design tool in headless Chrome: node tests/gadget_browser.mjs <build dir> [--shots dir]
//
// Opens <build dir>/index.html from file:// (the way a person opens a build), and prints one
// JSON line with what it saw:
//   errors    console errors and uncaught exceptions (must be none)
//   levels    for top -> row -> pair -> tile -> memory -> station: the heading, the HUD line,
//             and how long one frame took
//   editor    group row 0 with its factory into a new composite gadget, a refused
//             connection, undo/redo, and the exported design JSON
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { pathToFileURL } from "node:url";

const args = process.argv.slice(2);
const dir = path.resolve(args[0]);
const shots = args.includes("--shots") ? args[args.indexOf("--shots") + 1] : null;
const CHROME = process.env.CHROME || [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "/usr/bin/google-chrome", "/usr/bin/chromium-browser", "/usr/bin/chromium",
].find((p) => fs.existsSync(p));
if (!CHROME) { console.error("no Chrome found; set CHROME"); process.exit(2); }

const port = 9800 + Math.floor(Math.random() * 150);
const udd = fs.mkdtempSync(path.join(os.tmpdir(), "qccd-gadget-"));
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
const shot = async (name) => {
  if (!shots) return;
  const r = await send("Page.captureScreenshot", { format: "png" });
  fs.writeFileSync(path.join(shots, name + ".png"), Buffer.from(r.result.data, "base64"));
};

const out = { errors, levels: [], editor: null };
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
  const paths = ["", "r0", "r0.p0", "r0.p0.t0", "r0.p0.t0.mem", "r0.p0.st", "fac0"];
  for (const p of paths) {
    const level = await evaluate(`(() => { const G = window.GADGETS; G.go(${JSON.stringify(p)});
      G.seek(G.M.makespan * 0.12); G.fit();
      const t0 = performance.now(); G.drawStage(); G.drawTimeline(); const ms = performance.now() - t0;
      return { path: ${JSON.stringify(p)}, heading: document.getElementById("hudLvl").textContent,
               what: document.getElementById("hudWhat").textContent,
               step: document.getElementById("hudStep").hidden ? "" : document.getElementById("hudStep").textContent,
               crumbs: document.getElementById("crumbs").textContent, ms }; })()`);
    out.levels.push(level);
    await shot("level_" + (p || "top").replace(/\./g, "_"));
  }
  out.editor = await evaluate(`(() => {
    const E = window.GADGET_EDITOR, G = window.GADGETS;
    E.open(G.M.top);
    E.select(["r0", "fac0"]);
    E.group("row_with_factory");
    const grouped = { top: E.master().instances.map((i) => i.name).sort(), problems: E.problems(),
      ports: E.lib().masters["row_with_factory"].ports.map((p) => p.name + "=" + p.bind) };
    E.open("pair_bb72");
    const n = E.master().channels.length;
    E.connect("t0.bus", "st.b");
    const refused = { unchanged: E.master().channels.length === n, message: E.E.message };
    E.open(G.M.top);
    E.undo();
    const undone = E.master().instances.map((i) => i.name).sort();
    E.redo();
    const json = E.json();
    E.close();
    return { grouped, refused, undone, json };
  })()`);
  await shot("editor");
} catch (e) {
  errors.push(String(e && e.stack || e));
} finally {
  console.log(JSON.stringify(out));
  ws.close();
  chrome.kill();
}
