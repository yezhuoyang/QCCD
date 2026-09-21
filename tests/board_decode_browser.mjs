// A `decode` on a studio page with the site's classical layer, in headless Chrome:
//     node tests/board_decode_browser.mjs <page.html> [screenshot.png]
//
// Decoding is a TSIR instruction (docs/tsir.md): the outcomes of the ions it names go down
// the wires from where each was MEASURED to the decoder, and the decoder's answer on to the
// classical memory.  The studio's QEC-cycle layer (qccd/site/qec_cycle.js) lights exactly
// those wires on a decode frame.  This asks the PICTURE, because a highlight that never
// switches on passes every count a page keeps about itself:
//
//   decodes      every decode frame: its index, how many ions it reads, from how many sites
//   listing      the Program pane's row for it says DECODE
//   pixels       the stage rasterised, lit-colour pixels on the frame BEFORE the decode and
//                ON it -- the ions have not moved in between, so what the count gains is
//                the highlight
//   rings        every site the decode reads from is ringed, and nothing else is
//   registration each ring's centre against its site's own mark, in screen pixels, before
//                and after a zoom: the layer is in the stage, so this must stay at zero
//   playback     played at the default speed from two frames before, the playhead lands ON
//                the decode frame and the layer is lit while it is there
//   errors       console errors and uncaught exceptions (must be none)
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { pathToFileURL } from "node:url";

const page = path.resolve(process.argv[2]);
const shot = process.argv[3] || null;
const CHROME = process.env.CHROME || [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "/usr/bin/google-chrome", "/usr/bin/chromium-browser", "/usr/bin/chromium",
].find((p) => fs.existsSync(p));
if (!CHROME) { console.error("no Chrome found; set CHROME"); process.exit(2); }

const port = 9600 + Math.floor(Math.random() * 60);
const udd = fs.mkdtempSync(path.join(os.tmpdir(), "qccd-board-decode-"));
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

// The stage, rasterised in the page, and its pixels near the two lit colours counted.
const PIXELS = `(async () => {
  const svg = document.getElementById("svg"), r = svg.getBoundingClientRect();
  const xml = new XMLSerializer().serializeToString(svg);
  const img = new Image();
  img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(xml);
  await img.decode();
  const c = document.createElement("canvas");
  c.width = Math.round(r.width); c.height = Math.round(r.height);
  const x = c.getContext("2d");
  x.drawImage(img, 0, 0, c.width, c.height);
  const d = x.getImageData(0, 0, c.width, c.height).data;
  const W = (window.QEC_CYCLE.cfg.places || {}).wire || {};
  const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const want = [hex(W.lit || "#b45309"), hex(W.glow || "#fcd34d")], n = [0, 0];
  for (let i = 0; i < d.length; i += 4) for (let k = 0; k < 2; k++) {
    const w = want[k];
    if (Math.abs(d[i] - w[0]) + Math.abs(d[i + 1] - w[1]) + Math.abs(d[i + 2] - w[2]) < 40) n[k]++;
  }
  return n;
})()`;
const at = (f, ph) => `(() => { if (typeof stop === "function") { try { stop(); } catch (e) {} }
  frame = ${f}; phase = ${ph}; draw(); return [frame, phase]; })()`;
// every ring's centre against its site's own mark, in screen px
const REG = `(() => {
  const info = window.QEC_CYCLE.lit.parts && window.QEC_CYCLE.lit.parts.info;
  if (!info) return null;
  const rings = [...document.querySelectorAll("#qcLit circle")];
  let worst = 0, n = 0;
  info.sites.forEach((s, k) => {
    const ring = rings[k], mark = NODEEL[s.node] && NODEEL[s.node].el;
    if (!ring || !mark) return;
    const a = ring.getBoundingClientRect(), b = mark.getBoundingClientRect();
    const d = Math.hypot((a.x + a.width / 2) - (b.x + b.width / 2), (a.y + a.height / 2) - (b.y + b.height / 2));
    worst = Math.max(worst, d); n++;
  });
  return { rings: rings.length, sites: info.sites.length, measured: n, worst_px: +worst.toFixed(3) };
})()`;

const out = { errors };
try {
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: pathToFileURL(page).href });
  let ready = false;
  for (let i = 0; i < 300 && !ready; i++) {
    try { ready = await evaluate(`!!(window.QEC_CYCLE && typeof P !== "undefined" && P.frames && P.frames.length && document.getElementById("qcLayer"))`); } catch (e) { /* loading */ }
    if (!ready) await sleep(100);
  }
  out.ready = ready;
  out.decodes = await evaluate(`P.frames.map((f, i) => f.type === "decode" ? i : -1).filter((i) => i >= 0)
    .map((i) => { const s = window.QEC_CYCLE.decodeSites(window.QEC_CYCLE.studioAt(), i);
      return { frame: i, id: P.frames[i].id, ions: (P.frames[i].ions || []).length, sites: s.sites.length }; })`);
  if (out.decodes.length) {
    const d = out.decodes[0];
    out.listing = await evaluate(`(() => { try { return opText(P.frames[${d.frame}]); } catch (e) { return "?" + e; } })()`);
    await evaluate(at(d.frame - 1, 1));
    await sleep(80);
    const before = await evaluate(PIXELS);
    const litBefore = await evaluate(`document.querySelectorAll("#qcLit *").length`);
    await evaluate(at(d.frame, 0.3));
    await sleep(80);
    const during = await evaluate(PIXELS);
    out.pixels = { before, during, lit_elements_before: litBefore,
                   lit_elements_during: await evaluate(`document.querySelectorAll("#qcLit *").length`) };
    out.registration = { before_zoom: await evaluate(REG) };
    // zoom in on the stage's centre, the way a wheel does, then measure again
    await evaluate(`(() => { const r = document.getElementById("svg").getBoundingClientRect();
      zoomAt(r.x + r.width * 0.45, r.y + r.height * 0.45, -600); draw(); })()`);
    await sleep(150);
    out.registration.after_zoom = await evaluate(REG);
    out.caption = await evaluate(`(() => { const t = document.querySelector("#qcLit text"); return t ? t.textContent : ""; })()`);
    if (shot) {
      await evaluate(`(() => { if (typeof fit === "function") fit(); })()`);
      await evaluate(at(d.frame, 0.3));
      await sleep(300);
      const png = await send("Page.captureScreenshot", { format: "png" });
      fs.writeFileSync(shot, Buffer.from(png.result.data, "base64"));
      out.screenshot = shot;
    }
    // PLAYBACK at the page's default speed, from two frames before the decode
    await evaluate(at(Math.max(0, d.frame - 2), 1));
    await evaluate(`document.getElementById("play").click ? document.getElementById("play").click() : playBtn.onclick()`);
    const samples = [];
    for (let i = 0; i < 60; i++) {
      await sleep(40);
      samples.push(await evaluate(`[frame, document.querySelectorAll("#qcLit *").length]`));
    }
    await evaluate(`(() => { try { stop(); } catch (e) {} })()`);
    const on = samples.filter(([f]) => f === d.frame);
    out.playback = { decode_frame: d.frame, samples: samples.length, samples_on_decode: on.length,
                     lit_while_on: on.filter(([, n]) => n > 0).length,
                     lit_while_off: samples.filter(([f, n]) => f !== d.frame && n > 0).length,
                     passed_it: samples.some(([f]) => f > d.frame),
                     last_frame: await evaluate(`P.frames.length - 1`) };
  }
} catch (e) {
  out.fatal = String(e && e.stack || e);
}
console.log(JSON.stringify(out));
ws.close();
chrome.kill();
try { fs.rmSync(udd, { recursive: true, force: true }); } catch (e) { /* Windows holds it a moment */ }
process.exit(0);
