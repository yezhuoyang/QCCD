// Putting the classical half away on the Design canvas, in headless Chrome:
//     node tests/gadget_classical_browser.mjs <algorithm build dir>
//
// The "Classical" button in the stage tools (key C) hides the decoder, the classical memory,
// the wires to them and a decode's glow, together -- a wire drawn to a place that is not
// there reads as a bug -- and it is one setting with the studio's "Classical" box
// (`localStorage["qccd.classical"]`).  This asks the canvas, at a moment a `decode` is
// running (the most the classical half ever draws), against what the device ALONE looks
// like, drawn by a route that does not go through the switch at all: the same level, camera
// and instant with the classical places and wires taken out of the MODEL and no decode
// running, and the switch left on.
//
//   on           the button's state; `visible_px`, how much the classical half paints
//   off          the button's state; `left_px`, what differs from the device alone (0)
//   remembered   after a reload: still put away, and still nothing left (`left_px`)
//   key          C brings it back (`visible_px` again)
//   removed      how many places and wires the device-alone drawing took out
//   errors       console errors and uncaught exceptions (must be none)
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

const port = 9940 + Math.floor(Math.random() * 40);
const udd = fs.mkdtempSync(path.join(os.tmpdir(), "qccd-gclassical-"));
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

// In the page: keep the stage canvas under a name, compare two, and draw the device alone.
const HELPERS = `(() => {
  const G = window.GADGETS;
  window.__C = {};
  window.__capture = (name) => {
    const c = G.stage;
    window.__C[name] = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
  };
  window.__diff = (a, b) => {
    const A = window.__C[a], B = window.__C[b];
    if (!A || !B || A.length !== B.length) return -1;
    let n = 0;
    for (let i = 0; i < A.length; i += 4)
      if (Math.abs(A[i] - B[i]) + Math.abs(A[i + 1] - B[i + 1]) + Math.abs(A[i + 2] - B[i + 2]) > 24) n++;
    return n;
  };
  window.__bare = (name) => {
    const m = G.M.master(G.S.path), inst = m.instances, chans = m.channels || [];
    const decs = G.DECODES.splice(0), was = G.S.showClassical;
    m.instances = inst.filter((i) => !["decoder", "archive"].includes(G.M.masters[i.master].family));
    m.channels = chans.filter((c) => c.kind !== "wire");
    const removed = [inst.length - m.instances.length, chans.length - m.channels.length];
    G.S.showClassical = true;
    try { G.drawStage(); window.__capture(name); }
    finally { m.instances = inst; m.channels = chans; G.DECODES.push(...decs); G.S.showClassical = was; G.drawStage(); }
    return removed;
  };
  return true;
})()`;
const at = (t) => `(() => { const G = window.GADGETS; G.S.playing = false; G.seek(${t}); G.drawStage(); G.drawTimeline(); })()`;
const STATE = `(() => ({ shown: window.GADGETS.S.showClassical, button: document.getElementById("bClassical").className,
  stored: (() => { try { return localStorage.getItem("qccd.classical"); } catch (e) { return "?"; } })(),
  legend: document.getElementById("legend").textContent }))()`;
const load = async () => {
  await send("Page.navigate", { url: pathToFileURL(path.join(dir, "index.html")).href });
  for (let i = 0; i < 200; i++) {
    try { if (await evaluate("!!(window.GADGETS && window.GADGETS.ready())")) break; } catch (e) { /* loading */ }
    await sleep(100);
  }
  await evaluate(`(() => { const G = window.GADGETS; G.go(""); G.fit(); })()`);
  await sleep(300);
  await evaluate(HELPERS);
};
// the picture now, against the device alone at the same camera and instant
const look = async (t, name) => {
  await evaluate(at(t));
  await sleep(80);
  await evaluate(`window.__capture(${JSON.stringify(name)})`);
  const removed = await evaluate(`window.__bare(${JSON.stringify(name + "_bare")})`);
  return { state: await evaluate(STATE), removed,
           differs_px: await evaluate(`window.__diff(${JSON.stringify(name)}, ${JSON.stringify(name + "_bare")})`) };
};

const out = { errors };
try {
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  await load();
  const t = await evaluate(`window.GADGETS.DECODES.length ? window.GADGETS.DECODES[0].show : window.GADGETS.M.makespan / 2`);
  out.t = t;
  out.on = await look(t, "on");
  await evaluate(`document.getElementById("bClassical").click()`);
  out.off = await look(t, "off");
  await load();
  out.remembered = await look(t, "rem");
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "c", code: "KeyC", text: "c" });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "c", code: "KeyC" });
  out.key = await look(t, "key");
} catch (e) {
  out.fatal = String(e && e.stack || e);
}
console.log(JSON.stringify(out));
ws.close();
chrome.kill();
process.exit(0);
