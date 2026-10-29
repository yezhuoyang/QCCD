// The program columns of a gadget page, in headless Chrome:
//     node tests/gadget_columns_browser.mjs <build dir> [width] [height] [screenshot.png]
//
// Every program of a run stands in a column of its own beside the animation (app.js, "the
// columns").  This asks the page, at its real layout:
//
//   cols       each column shown: its width and height, the line lit in it, whether that line
//              is in view, how many words carry a note and how many statement tags it drew
//   app        the page's own height, and whether it scrolls sideways (it must not)
//   notes      a tag in view in each column, hovered: the note the page shows under it
//   hardware   the Hardware column switched on: the TSIR line it lights, and the hardware
//              instruction the page says is running (the line must be that instruction's)
//   wrap       the Wrap button: long lines stop wrapping, and wrap again
//   fold       the right-hand panel folded and opened: the stage gains the room, and gives it back
//   spec       rows of a layer column that are its specification (`spec NAME:`, `*.spec.prog`):
//              there must be none
//   drag       every boundary dragged with the mouse: between two columns, the stage's edge, the
//              right panel's edge, a section of it, the timeline's edge, the left panel's edge --
//              each the widths or heights before and after
//   kept       the page reloaded: the columns and the right panel as the drags left them
//   errors     console errors and uncaught exceptions (must be none)
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { pathToFileURL } from "node:url";
import { findChrome } from "./chrome_path.mjs";

const dir = path.resolve(process.argv[2]);
const W = +(process.argv[3] || 1600), H = +(process.argv[4] || 900);
const shot = process.argv[5] || null;
const CHROME = findChrome();
if (!CHROME) { console.error("no Chrome found; set CHROME"); process.exit(2); }

const port = 9700 + Math.floor(Math.random() * 200);
const udd = fs.mkdtempSync(path.join(os.tmpdir(), "qccd-cols-"));
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

const COLS = `(() => [...document.querySelectorAll(".pcol")].filter((c) => !c.hidden).map((c) => {
  const b = c.getBoundingClientRect(), body = c.querySelector(".pc-b"), cb = body.getBoundingClientRect();
  const cur = c.querySelector(".cl.cur, .pl.live"), cr = cur ? cur.getBoundingClientRect() : null;
  return { key: c.dataset.col, w: Math.round(b.width), h: Math.round(b.height),
           head: c.querySelector("h3").innerText.replace(/\\s+/g, " ").trim(),
           lit: cur ? cur.innerText.replace(/\\s+/g, " ").trim().slice(0, 160) : null,
           litInView: cr ? cr.top >= cb.top - 1 && cr.bottom <= cb.bottom + 1 : null,
           noted: c.querySelectorAll("[data-w]").length, tags: c.querySelectorAll(".tg").length,
           none: (c.querySelector(".pc-none:not([hidden])") || {}).innerText || "" };
}))()`;
const STAGE_W = `Math.round(document.getElementById("stage").getBoundingClientRect().width)`;
const WIDTHS = `[...document.querySelectorAll(".pcol")].filter((c) => !c.hidden).map((c) => Math.round(c.getBoundingClientRect().width))`;
const SIZE = (sel) => `(() => { const b = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return [Math.round(b.width), Math.round(b.height)]; })()`;
// (string tests, not regexes: a backslash in this template is the template's, not the page's)
const SPEC = `[...document.querySelectorAll(".pcol .cl .tx")].filter((r) => { const t = r.textContent.trim(); return t.startsWith("spec ") && t.includes(":"); }).length +
  [...document.querySelectorAll(".pcol .cl-file")].filter((f) => f.textContent.includes(".spec.")).length`;
const ROOM = `[${STAGE_W}, Math.round(document.getElementById("cols").getBoundingClientRect().width)]`;

const out = { errors };
try {
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: W, height: H, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: pathToFileURL(path.join(dir, "index.html")).href });
  let ready = false;
  for (let i = 0; i < 200 && !ready; i++) {
    try { ready = await evaluate("!!(window.GADGETS && window.GADGETS.ready())"); } catch (e) { /* loading */ }
    if (!ready) await sleep(100);
  }
  // somewhere a place is at work: the first instant after a third of the run where the
  // panels name a hardware instruction
  out.at = await evaluate(`(() => { const G = window.GADGETS; G.S.playing = false;
    const T = G.M.makespan;
    for (let k = 0; k < 400; k++) { const t = T * (0.33 + 0.6 * k / 400); G.seek(t); const f = G.focusOp(); if (f && f.ins !== null) return t; }
    G.seek(T / 3); return T / 3; })()`);
  for (let k = 0; k < 40; k++) { await sleep(150); if (!(await evaluate(`(window.GADGETS.M.gir.window || {}).missing`))) break; }
  await sleep(700);
  out.app = await evaluate(`({ W: innerWidth, scrollW: document.documentElement.scrollWidth, appH: Math.round(document.getElementById("app").getBoundingClientRect().height),
    colsH: Math.round(document.getElementById("cols").getBoundingClientRect().height), barH: Math.round(document.getElementById("colsBar").getBoundingClientRect().height),
    cls: document.getElementById("app").className, bar: document.getElementById("colsBar").innerText.replace(/\\s+/g, " ").trim(), stage: ${STAGE_W} })`);
  out.cols = await evaluate(COLS);
  out.state = await evaluate(`window.GADGETS.cols()`);
  // a tag in view in each column, hovered
  out.notes = [];
  for (const c of out.cols) {
    const pos = await evaluate(`(() => { const c = document.querySelector('.pcol[data-col="${c.key}"]');
      const cb = c.querySelector(".pc-b").getBoundingClientRect();
      const w = [...c.querySelectorAll(".tg[data-w]")].find((e) => { const b = e.getBoundingClientRect(); return b.width > 0 && b.top > cb.top + 2 && b.bottom < cb.bottom - 2 && b.left > cb.left && b.right < cb.right; });
      if (!w) return null; const b = w.getBoundingClientRect(); return [b.left + 2, b.top + b.height / 2, w.textContent]; })()`);
    if (!pos) { out.notes.push({ key: c.key, word: null }); continue; }
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: pos[0], y: pos[1] });
    await sleep(120);
    out.notes.push({ key: c.key, word: pos[2], tip: await evaluate(`(() => { const t = document.getElementById("tip"); return t.style.display === "block" ? t.textContent : ""; })()`) });
  }
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: 5, y: H - 5 });
  // the Hardware column, switched on: the line it lights is the instruction running
  const hasHw = await evaluate(`!!document.querySelector('#colsBar button[data-col="hw"]')`);
  if (hasHw) {
    await evaluate(`(() => { const s = window.GADGETS.cols().find((c) => c.key === "hw"); if (!s.on) document.querySelector('#colsBar button[data-col="hw"]').click(); })()`);
    let hw = null;
    for (let k = 0; k < 60; k++) {
      await sleep(150);
      hw = await evaluate(`(() => { const c = document.querySelector('.pcol[data-col="hw"]'), cur = c.querySelector(".cl.cur");
        return cur ? { lit: cur.innerText.replace(/\\s+/g, " ").trim(), head: c.querySelector("h3").innerText.replace(/\\s+/g, " ").trim(),
                       focus: window.GADGETS.focusOp(), state: window.GADGETS.cols().find((x) => x.key === "hw") } : null; })()`);
      if (hw) break;
    }
    out.hardware = hw;
    out.colsWithHw = await evaluate(COLS);
  }
  // Wrap off and on
  await evaluate(`document.getElementById("colsWrap").click()`);
  await sleep(100);
  out.wrap = { off: await evaluate(`document.getElementById("cols").classList.contains("nowrap")`) };
  await evaluate(`document.getElementById("colsWrap").click()`);
  await sleep(100);
  out.wrap.on = !(await evaluate(`document.getElementById("cols").classList.contains("nowrap")`));
  out.spec = await evaluate(SPEC);
  // the right-hand panel folded, then opened: [stage, columns] widths (a full-stack page opens
  // with it folded: open it first)
  if (await evaluate(`document.getElementById("app").classList.contains("noright")`)) {
    await evaluate(`document.getElementById("rightRail").click()`);
    await sleep(400);
  }
  const before = await evaluate(ROOM);
  await evaluate(`document.getElementById("rightFold").click()`);
  await sleep(400);
  const folded = await evaluate(ROOM);
  await evaluate(`document.getElementById("rightRail").click()`);
  await sleep(400);
  out.fold = { before, folded, after: await evaluate(ROOM) };
  // every boundary, dragged with the mouse
  const dragBy = async (sel, dx, dy) => {
    const at = await evaluate(`(() => { const e = document.querySelector(${JSON.stringify(sel)}); if (!e || e.hidden) return null;
      const b = e.getBoundingClientRect(); return b.width && b.height ? [b.left + b.width / 2, b.top + b.height / 2] : null; })()`);
    if (!at) return false;
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: at[0], y: at[1] });
    await send("Input.dispatchMouseEvent", { type: "mousePressed", x: at[0], y: at[1], button: "left", buttons: 1, clickCount: 1 });
    for (let k = 1; k <= 6; k++) await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: at[0] + dx * k / 6, y: at[1] + dy * k / 6, button: "left", buttons: 1 });
    await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: at[0] + dx, y: at[1] + dy, button: "left", buttons: 0, clickCount: 1 });
    await sleep(350);
    return true;
  };
  out.drag = {};
  const two = ".pcol:not([hidden]) ~ .pcol:not([hidden]) > .pc-edge";
  let b0 = await evaluate(WIDTHS);
  if (await dragBy(two, 80, 0)) out.drag.between = { before: b0, after: await evaluate(WIDTHS) };
  b0 = [await evaluate(WIDTHS), await evaluate(ROOM)];
  if (await dragBy("#vsplit", -100, 0)) out.drag.stage = { before: b0[0], after: await evaluate(WIDTHS), room: [b0[1], await evaluate(ROOM)] };
  b0 = await evaluate(SIZE("#right"));
  if (await dragBy("#rightEdge", -60, 0)) out.drag.right = { before: b0, after: await evaluate(SIZE("#right")) };
  b0 = await evaluate(SIZE("#verif"));
  if (await dragBy("#verif > .edge", 0, 40)) out.drag.section = { before: b0, after: await evaluate(SIZE("#verif")) };
  b0 = await evaluate(SIZE("#tlWrap"));
  if (await dragBy("#split", 0, -50)) out.drag.timeline = { before: b0, after: await evaluate(SIZE("#tlWrap")) };
  if (await evaluate(`document.getElementById("app").classList.contains("noleft")`)) {
    await evaluate(`document.getElementById("leftRail").click()`);
    await sleep(400);
  }
  b0 = await evaluate(SIZE("#left"));
  if (await dragBy("#leftEdge", 40, 0)) out.drag.left = { before: b0, after: await evaluate(SIZE("#left")) };
  // kept: the page reloaded opens as the drags left it
  const left = { cols: await evaluate(WIDTHS), right: await evaluate(SIZE("#right")), left: await evaluate(SIZE("#left")) };
  await send("Page.reload", {});
  let again = false;
  for (let i = 0; i < 200 && !again; i++) {
    await sleep(100);
    try { again = await evaluate("!!(window.GADGETS && window.GADGETS.ready())"); } catch (e) { /* loading */ }
  }
  await sleep(600);
  out.kept = { left, now: { cols: await evaluate(WIDTHS), right: await evaluate(SIZE("#right")), left: await evaluate(SIZE("#left")) } };
  if (shot) {
    await sleep(300);
    const png = await send("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(shot, Buffer.from(png.result.data, "base64"));
    out.screenshot = shot;
  }
} catch (e) {
  out.fatal = String(e && e.stack || e);
}
ws.close();
chrome.kill();
console.log(JSON.stringify(out));
process.exit(0);
