// A `decode` on the Design canvas, in headless Chrome:
//     node tests/gadget_decode_browser.mjs <algorithm build dir> [screenshot.png]
//
// Decoding is an instruction (qccd/gadget/algorithm.py): a place that measures keeps its
// syndrome, and `decode` sends it down the dashed wire to the decoder, whose frame update
// goes down another wire to the classical memory.  The page lights those wires while the
// instruction runs.  This asks the PICTURE whether it does, because a highlight that never
// switches on passes every count the page keeps about itself:
//
//   decodes     each `decode` instruction: its span, the wires it lights, the windows it sends
//   pixels      lit-colour pixels on the stage canvas half a microsecond BEFORE each decode
//               and DURING it: nothing on the stage moves in between, so what the count gains
//               is the highlight (`quiet` is the same count far from every decode)
//   packets     syndrome packets in flight on lit wires at the moment the page chose to show
//   hud         the stage's decode badge, inside and outside
//   program     the decode line is lit in the Program pane while it runs
//   playback    played from just before a decode, the playhead stops IN it (it does not jump
//               a few-microsecond instruction in one frame), is slowed there, and leaves it
//   inspector   the decode instruction's inspector lists the windows it sends
//   errors      console errors and uncaught exceptions (must be none)
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { pathToFileURL } from "node:url";

const dir = path.resolve(process.argv[2]);
const shot = process.argv[3] || null;
const CHROME = process.env.CHROME || [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "/usr/bin/google-chrome", "/usr/bin/chromium-browser", "/usr/bin/chromium",
].find((p) => fs.existsSync(p));
if (!CHROME) { console.error("no Chrome found; set CHROME"); process.exit(2); }

const port = 9990 + Math.floor(Math.random() * 40);
const udd = fs.mkdtempSync(path.join(os.tmpdir(), "qccd-decode-"));
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

// Pixels on the stage close to a colour, counted in the page.  Returns [lit, glow].
const PIXELS = `(() => {
  const G = window.GADGETS, c = G.stage, x = c.getContext("2d");
  const d = x.getImageData(0, 0, c.width, c.height).data;
  const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const want = [hex(G.WIRE_LIT), hex(G.WIRE_GLOW)], n = [0, 0];
  for (let i = 0; i < d.length; i += 4) {
    for (let k = 0; k < 2; k++) {
      const w = want[k];
      if (Math.abs(d[i] - w[0]) + Math.abs(d[i + 1] - w[1]) + Math.abs(d[i + 2] - w[2]) < 40) n[k]++;
    }
  }
  return n;
})()`;
const at = (t) => `(() => { const G = window.GADGETS; G.S.playing = false; G.seek(${t}); G.drawStage(); G.drawTimeline(); })()`;

const out = { errors };
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
  await evaluate(`(() => { const G = window.GADGETS; G.go(""); G.fit(); })()`);
  await sleep(300);
  out.decodes = await evaluate(`window.GADGETS.DECODES.map((d) => ({ id: d.id, text: d.text.trim(),
    t0: d.t0, t1: d.t1, show: d.show, wires: [...d.nets], senders: [...d.senders],
    windows: d.windows.length, bits: d.bits }))`);
  out.pixels = [];
  out.packets = [];
  out.hud = [];
  out.program = [];
  // well clear of every decode: half-way through the longest gap between two of them
  const quiet = await evaluate(`(() => { const G = window.GADGETS, D = G.DECODES, T = G.M.makespan;
    const cuts = [0].concat(D.flatMap((d) => [d.t0, d.t1])).concat([T]);
    let best = [0, 0];
    for (let i = 0; i + 1 < cuts.length; i += 2) if (cuts[i + 1] - cuts[i] > best[1] - best[0]) best = [cuts[i], cuts[i + 1]];
    return (best[0] + best[1]) / 2; })()`);
  await evaluate(at(quiet));
  out.quiet = { t: quiet, pixels: await evaluate(PIXELS),
                hud: await evaluate(`document.getElementById("hudDecode").hidden ? "" : document.getElementById("hudDecode").textContent`) };
  for (const d of out.decodes) {
    // A/B: the same picture half a microsecond before the decode starts -- nothing on the
    // stage has moved, so every lit-colour pixel the decode adds is the highlight's
    await evaluate(at(d.t0 - 0.5));
    await sleep(50);
    const before = await evaluate(PIXELS);
    await evaluate(at(d.show));
    await sleep(50);
    out.pixels.push({ id: d.id, before, during: await evaluate(PIXELS) });
    out.packets.push({ id: d.id, packets: await evaluate(`(() => { const G = window.GADGETS; const lit = G.litAt(G.S.t);
      return lit ? [...lit.nets].reduce((a, n) => a + G.messagesOn(n, G.S.t).filter((m) => m[9] === "syndrome").length, 0) : 0; })()`) });
    out.hud.push({ id: d.id, text: await evaluate(`document.getElementById("hudDecode").hidden ? "" : document.getElementById("hudDecode").textContent`) });
    // two seeks 30 ms apart and then NOTHING: the Program pane refreshes at most every
    // 180 ms, and a refresh the throttle skipped used to be dropped rather than owed
    await evaluate(at(quiet));
    await sleep(30);
    await evaluate(at(d.show));
    await sleep(500);
    out.program.push({ id: d.id, live: await evaluate(`[...document.querySelectorAll("#progBody .pl")]
      .filter((e) => e.dataset.ids.split(",").includes("${d.id}")).map((e) => e.className)`),
      t: await evaluate(`window.GADGETS.S.t`), span: await evaluate(`window.GADGETS.M.insSpan[${d.id}]`),
      active: await evaluate(`window.GADGETS.M.activeInstructions(window.GADGETS.S.t)`),
      makespan: await evaluate(`window.GADGETS.M.makespan`) });
  }
  if (shot && out.decodes.length) {
    const d = out.decodes[0];
    await evaluate(at(d.show));
    await sleep(400);                          // the loop redraws the panels on its own clock
    const png = await send("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(shot, Buffer.from(png.result.data, "base64"));
    out.screenshot = shot;
  }
  // PLAYBACK: from 40 ms of simulated time before the first decode, at the default rate
  if (out.decodes.length) {
    const d = out.decodes[0];
    await evaluate(`(() => { const G = window.GADGETS; G.seek(${Math.max(0, d.t0 - 40000)}); G.S.rate = 10000; G.S.playing = true; })()`);
    const samples = [];
    for (let i = 0; i < 40; i++) {
      await sleep(150);
      samples.push(await evaluate(`[window.GADGETS.S.t, window.GADGETS.S.slowed]`));
    }
    await evaluate(`window.GADGETS.S.playing = false`);
    const inside = samples.filter(([t]) => t >= d.t0 - 1e-6 && t <= d.t1 + 1e-6);
    out.playback = {
      decode: d.id, frames_sampled: samples.length,
      samples_inside: inside.length,
      slowed_max: Math.max(...samples.map((s) => s[1] || 0)),
      reached_after: samples.some(([t]) => t > d.t1),
      wall_s_inside_estimate: inside.length * 0.15,
    };
  }
  if (out.decodes.length) {
    out.inspector = await evaluate(`(() => { const G = window.GADGETS; G.select({ kind: "ins", id: ${out.decodes[0].id} });
      G.renderInspector(); return document.getElementById("inspBody").innerText; })()`);
  }
} catch (e) {
  out.fatal = String(e && e.stack || e);
}
console.log(JSON.stringify(out));
ws.close();
chrome.kill();
try { fs.rmSync(udd, { recursive: true, force: true }); } catch (e) { /* Windows holds it a moment */ }
process.exit(0);
