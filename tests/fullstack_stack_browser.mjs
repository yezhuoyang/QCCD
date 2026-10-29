// The Studio's Stack view (qccd/fullstack/web/stack.js), in headless Chrome:
//     node tests/fullstack_stack_browser.mjs <page.html> [width] [height] [screenshot.png]
//
//   cols       each layer column shown: width, height, the line lit in it and whether it is in
//              view, how many statement tags it drew; and the pane's own height
//   note       a tag in view in the QEC column, hovered: the note shown under it
//   hardware   the Hardware column switched on: the Studio's listing stands in the row, its
//              executing instruction lit and in view
//   views      Both and Hardware take the listing home; Stack brings it back
//   errors     console errors and uncaught exceptions (must be none)
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { pathToFileURL } from "node:url";
import { findChrome } from "./chrome_path.mjs";

const page = path.resolve(process.argv[2]);
const W = +(process.argv[3] || 1920), H = +(process.argv[4] || 1080);
const shot = process.argv[5] || null;
const CHROME = findChrome();
if (!CHROME) { console.error("no Chrome found; set CHROME"); process.exit(2); }
const port = 9500 + Math.floor(Math.random() * 200);
const udd = fs.mkdtempSync(path.join(os.tmpdir(), "qccd-stack-"));
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${udd}`,
  "--disable-extensions", "--no-first-run", "--no-default-browser-check", `--window-size=${W},${H}`,
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

const LOOK = `(() => {
  const cols = [...document.querySelectorAll("#pStack .fs-col:not([hidden]), #pStack #pHw")].map((c) => {
    const b = c.getBoundingClientRect(), box = c.querySelector(".fs-lines, .lst"), cb = box.getBoundingClientRect();
    const cur = c.querySelector(".fs-cur, .ln.cur"), cr = cur ? cur.getBoundingClientRect() : null;
    return { key: c.getAttribute("data-layer") || c.id, w: Math.round(b.width), h: Math.round(b.height),
             head: (c.querySelector("header, .ph") || {}).innerText.replace(/\\s+/g, " ").trim(),
             lit: cur ? cur.innerText.replace(/\\s+/g, " ").trim().slice(0, 120) : null,
             inView: cr ? cr.top >= cb.top - 1 && cr.bottom <= cb.bottom + 1 : null,
             tags: c.querySelectorAll(".tg").length };
  });
  const pp = document.getElementById("paneP").getBoundingClientRect();
  return { view: document.getElementById("paneP").getAttribute("data-view"), paneH: Math.round(pp.height),
           W: innerWidth, scrollW: document.documentElement.scrollWidth, cols };
})()`;
const WHERE = `(() => ({ view: document.getElementById("paneP").getAttribute("data-view"),
  home: document.getElementById("pHw").parentNode.id === "paneP",
  shown: document.getElementById("pHw").getBoundingClientRect().width > 0 }))()`;

const out = { errors };
try {
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: pathToFileURL(page).href });
  for (let i = 0; i < 100; i++) { await sleep(100); if (await evaluate("!!window.FULLSTACK")) break; }
  await sleep(500);
  // a quarter into the run, where an instruction executes a statement of every layer
  out.frame = await evaluate(`(() => { const F = window.FULLSTACK; const n = P.frames.length;
    for (let i = Math.floor(n / 4); i < n; i++) { const c = F.chainOf(P.frames[i]); if (c && c.E[8]) { F.seek(i); return i; } }
    return null; })()`);
  await sleep(600);
  out.stack = await evaluate(LOOK);
  const pos = await evaluate(`(() => { const box = document.querySelector('.fs-lines[data-layer="qec"]'), cb = box.getBoundingClientRect();
    const w = [...box.querySelectorAll(".tg[data-w]")].find((e) => { const b = e.getBoundingClientRect(); return b.width && b.top > cb.top + 2 && b.bottom < cb.bottom - 2; });
    if (!w) return null; const b = w.getBoundingClientRect(); return [b.left + 2, b.top + b.height / 2, w.textContent]; })()`);
  if (pos) {
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: pos[0], y: pos[1] });
    await sleep(150);
    out.note = { word: pos[2], tip: await evaluate(`(() => { const e = document.querySelector(".fs-tip"); return e.style.display === "block" ? e.textContent : ""; })()`) };
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: 2, y: H - 2 });
  }
  await evaluate(`window.FULLSTACK.setCol("hw", true)`);
  await sleep(500);
  out.withHw = await evaluate(LOOK);
  out.views = {};
  for (const v of ["both", "hw", "stack"]) {
    await evaluate(`setProgView("${v}")`);
    await sleep(250);
    out.views[v] = await evaluate(WHERE);
  }
  await evaluate(`window.FULLSTACK.setCol("hw", false)`);
  await sleep(250);
  out.views.stackNoHw = await evaluate(WHERE);
  // the columns show what runs: no row of a specification (a `spec NAME:` block, a
  // `*.spec.prog` file)
  out.spec = await evaluate(`[...document.querySelectorAll("#pStack .fs-ln .fs-tx")].filter((r) => { const t = r.textContent.trim(); return t.startsWith("spec ") && t.includes(":"); }).length +
    [...document.querySelectorAll("#pStack .fs-fileh")].filter((f) => f.textContent.includes(".spec.")).length`);
  // every boundary dragged with the mouse: between the first two columns, and the pane's own
  // left edge -- each column's width before and after
  const dragBy = async (sel, dx) => {
    const at = await evaluate(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e || e.hidden) return null;
      const b = e.getBoundingClientRect(); return b.width && b.height ? [b.left + b.width / 2, b.top + b.height / 2] : null; })()`);
    if (!at) return false;
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: at[0], y: at[1] });
    await send("Input.dispatchMouseEvent", { type: "mousePressed", x: at[0], y: at[1], button: "left", buttons: 1, clickCount: 1 });
    for (let k = 1; k <= 6; k++) await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: at[0] + dx * k / 6, y: at[1], button: "left", buttons: 1 });
    await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: at[0] + dx, y: at[1], button: "left", buttons: 0, clickCount: 1 });
    await sleep(350);
    return true;
  };
  const WIDTHS = `[...document.querySelectorAll("#pStack .fs-cols > .fs-col:not([hidden])")].map((c) => Math.round(c.getBoundingClientRect().width))`;
  out.drag = {};
  let b0 = await evaluate(WIDTHS);
  if (await dragBy("#pStack .fs-col:not([hidden]) ~ .fs-col:not([hidden]) > .fs-edge", 60)) out.drag.between = { before: b0, after: await evaluate(WIDTHS) };
  b0 = await evaluate(WIDTHS);
  if (await dragBy("#pStack > .fs-pane-edge", -100)) out.drag.pane = { before: b0, after: await evaluate(WIDTHS) };
  out.afterDrag = await evaluate(LOOK);
  if (shot) {
    await evaluate(`window.FULLSTACK.setCol("hw", true)`);
    await sleep(400);
    const png = await send("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(shot, Buffer.from(png.result.data, "base64"));
  }
} catch (e) {
  out.fatal = String(e && e.stack || e);
}
ws.close();
chrome.kill();
console.log(JSON.stringify(out));
process.exit(0);
