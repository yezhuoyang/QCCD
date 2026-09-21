// Putting the classical half away, on a studio page with the site's classical layer:
//     node tests/classical_toggle_browser.mjs <page.html>
//
// The studio draws the wires from the measuring sites, the decoder and the classical memory
// over the device (qccd/site/qec_cycle.js), and a reader may not want them.  The "Classical"
// box in the tools bar puts ALL of it away -- a decode's lit path included, since a lit wire
// to a place that is not drawn reads as a bug -- remembers that per viewer, and gives the
// frame back to the device unless the reader has moved the camera.  This asks the PICTURE:
//
//   shown        on load: the box is ticked, the layer is drawn and it extends the frame
//   hidden       after unticking, ON A DECODE FRAME and at the camera the layer was shown
//                at, the stage rasterises to exactly the device alone: the same page with
//                both of the layer's groups cut out of it.  `visible_px` is how many pixels
//                the layer paints when shown, so a zero here is a real zero
//   refit        unticking gave the frame back to the device (`STAGE_EXTENT` is null and
//                the view is the device's own fit)
//   remembered   after a reload the box is still unticked and nothing is drawn
//   panel        the QEC cycle panel's own box is the same setting, both ways
//   zoomed       a reader who has zoomed in keeps their view when the layer goes or comes
//   errors       console errors and uncaught exceptions (must be none)
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { pathToFileURL } from "node:url";

const page = path.resolve(process.argv[2]);
const CHROME = process.env.CHROME || [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "/usr/bin/google-chrome", "/usr/bin/chromium-browser", "/usr/bin/chromium",
].find((p) => fs.existsSync(p));
if (!CHROME) { console.error("no Chrome found; set CHROME"); process.exit(2); }

const port = 9660 + Math.floor(Math.random() * 60);
const udd = fs.mkdtempSync(path.join(os.tmpdir(), "qccd-classical-"));
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

// The stage rasterised in the page and kept there under a name; `bare` cuts the classical
// layer's two groups out of a copy first, which is what "the device alone" looks like.
const RASTER = (name, bare) => `(async () => {
  const svg = document.getElementById("svg"), r = svg.getBoundingClientRect();
  const copy = svg.cloneNode(true);
  if (${bare ? "true" : "false"}) copy.querySelectorAll("#qcLayer, #qcLit").forEach((g) => g.remove());
  const img = new Image();
  img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(new XMLSerializer().serializeToString(copy));
  await img.decode();
  const c = document.createElement("canvas");
  c.width = Math.round(r.width); c.height = Math.round(r.height);
  c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
  (window.__R = window.__R || {})[${JSON.stringify(name)}] = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
  return c.width * c.height;
})()`;
const DIFF = (a, b) => `(() => { const A = window.__R[${JSON.stringify(a)}], B = window.__R[${JSON.stringify(b)}];
  if (!A || !B || A.length !== B.length) return -1;
  let n = 0;
  for (let i = 0; i < A.length; i += 4)
    if (Math.abs(A[i] - B[i]) + Math.abs(A[i + 1] - B[i + 1]) + Math.abs(A[i + 2] - B[i + 2]) > 24) n++;
  return n; })()`;
const at = (f, ph) => `(() => { if (typeof stop === "function") { try { stop(); } catch (e) {} }
  frame = ${f}; phase = ${ph}; draw(); return [frame, phase]; })()`;
const cam = `[VB.x, VB.y, VB.w, VB.h].map((v) => +v.toFixed(4)).join(",")`;
const setCam = (s) => `(() => { const v = ${JSON.stringify(s)}.split(",").map(Number);
  VB = { x: v[0], y: v[1], w: v[2], h: v[3] }; applyVB(); draw(); })()`;
const STATE = `(() => ({
  box: document.getElementById("qcShowOn") ? document.getElementById("qcShowOn").checked : null,
  layer: document.querySelectorAll("#qcLayer *").length,
  lit: document.querySelectorAll("#qcLit *").length,
  extent: !!(window.STAGE_EXTENT && window.STAGE_EXTENT()),
  stored: (() => { try { return localStorage.getItem("qccd.classical"); } catch (e) { return "?"; } })(),
  camera: ${cam} }))()`;
const waitReady = async () => {
  for (let i = 0; i < 300; i++) {
    try { if (await evaluate(`!!(window.QEC_CYCLE && typeof P !== "undefined" && P.frames && P.frames.length && document.getElementById("qcShowOn"))`)) return true; } catch (e) { /* loading */ }
    await sleep(100);
  }
  return false;
};

const out = { errors };
try {
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: pathToFileURL(page).href });
  out.ready = await waitReady();
  await sleep(500);
  // a decode frame, so the lit path is part of what has to go
  const d = await evaluate(`P.frames.findIndex((f) => f.type === "decode")`);
  out.decode_frame = d;
  const f = d >= 0 ? d : 0, ph = d >= 0 ? 0.3 : 1;
  await evaluate(at(f, ph));
  await sleep(100);
  out.shown = await evaluate(STATE);
  const shownCam = out.shown.camera;
  await evaluate(RASTER("on", false));
  await evaluate(RASTER("bare", true));
  const visible = await evaluate(DIFF("on", "bare"));

  // untick it
  await evaluate(`document.getElementById("qcShowOn").click()`);
  await sleep(150);
  const off = await evaluate(STATE);
  await evaluate(`(() => { const e = window.STAGE_EXTENT; window.STAGE_EXTENT = null; fit(); window.__deviceFit = ${cam};
    window.STAGE_EXTENT = e; })()`);
  const deviceFit = await evaluate(`window.__deviceFit`);
  await evaluate(setCam(off.camera));
  // the picture, at the camera the layer was shown at
  await evaluate(setCam(shownCam));
  await evaluate(at(f, ph));
  await sleep(100);
  await evaluate(RASTER("off", false));
  out.hidden = { state: off, visible_px: visible, left_px: await evaluate(DIFF("off", "bare")) };
  out.refit = { camera: off.camera, device_fit: deviceFit, shown_camera: shownCam };

  // a reload remembers
  await send("Page.reload", {});
  await sleep(300);
  await waitReady();
  await sleep(500);
  await evaluate(at(f, ph));
  await sleep(100);
  out.remembered = await evaluate(STATE);

  // the panel's box is the same setting, both ways
  await evaluate(`window.QEC_CYCLE.toggle()`);
  await sleep(100);
  const panelBefore = await evaluate(`document.getElementById("qcLayerOn") ? document.getElementById("qcLayerOn").checked : null`);
  await evaluate(`document.getElementById("qcLayerOn").click()`);
  await sleep(150);
  out.panel = { before: panelBefore, after: await evaluate(STATE),
                panel_after: await evaluate(`document.getElementById("qcLayerOn").checked`) };
  await evaluate(`window.QEC_CYCLE.toggle()`);

  // zoomed in, the reader keeps the view both ways
  await evaluate(`(() => { const r = document.getElementById("svg").getBoundingClientRect();
    zoomAt(r.x + r.width * 0.45, r.y + r.height * 0.45, -600); draw(); })()`);
  await sleep(100);
  const zoomedCam = await evaluate(cam);
  await evaluate(`document.getElementById("qcShowOn").click()`);
  await sleep(150);
  const zOff = await evaluate(STATE);
  await evaluate(`document.getElementById("qcShowOn").click()`);
  await sleep(150);
  const zOn = await evaluate(STATE);
  out.zoomed = { camera: zoomedCam, off: zOff, on: zOn };
} catch (e) {
  out.fatal = String(e && e.stack || e);
}
console.log(JSON.stringify(out));
ws.close();
chrome.kill();
process.exit(0);
