// The design page at the machine level, in headless Chrome:
//   node tests/gadget_machine_browser.mjs <build dir> [shot.png]
//
// Opens <build dir>/index.html (a build of a program on a floorplan, `qccd.gadget.verified`)
// and prints one JSON line with what it saw:
//   errors     console errors and uncaught exceptions (must be none)
//   verdict    the verification panel's first line, and how many of its lines are ticks
//   rules      the checks panel: rule ids in order, which hold, and the badge
//   opened     a rule clicked open: does it show its own assertion?
//   traffic    the traffic panel's rows
//   floorplan  the Floorplan button: does it open the floorplan's text, and its download name
//   exported   the floorplan text the editor writes for the top gadget
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { pathToFileURL } from "node:url";
import { findChrome } from "./chrome_path.mjs";

const dir = path.resolve(process.argv[2]);
const shot = process.argv[3] || null;
const CHROME = findChrome();
if (!CHROME) { console.error("no Chrome found; set CHROME"); process.exit(2); }
const port = 9950 + Math.floor(Math.random() * 40);
const udd = fs.mkdtempSync(path.join(os.tmpdir(), "qccd-machine-"));
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
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  if (m.method === "Runtime.exceptionThrown") errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
  if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") errors.push(m.params.args.map((a) => a.value || a.description).join(" "));
});
const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
const evaluate = async (expr) => {
  const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.result.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description || "evaluate failed");
  return r.result.result.value;
};
await send("Runtime.enable");
await send("Page.enable");
await send("Page.navigate", { url: pathToFileURL(path.join(dir, "index.html")).href });
for (let i = 0; i < 100; i++) {
  if (await evaluate("!!document.getElementById('checksBody') && document.getElementById('checksBody').children.length > 0").catch(() => false)) break;
  await new Promise((r) => setTimeout(r, 100));
}
const out = { errors };
try {
  out.verdict = await evaluate(`(() => { const b = document.getElementById('verifBody');
    return { first: b.querySelector('.so')?.textContent || '', ticks: b.querySelectorAll('.sol.pass').length,
             crosses: b.querySelectorAll('.sol.fail').length, note: b.querySelector('.note')?.textContent || '' }; })()`);
  out.rules = await evaluate(`(() => { const rows = [...document.querySelectorAll('#checksBody .ck b')];
    return { ids: rows.map((b) => b.textContent), hold: rows.filter((b) => b.className === 'pass').map((b) => b.textContent),
             count: document.getElementById('checksCount').textContent, badge: document.getElementById('checksBadge').textContent }; })()`);
  out.opened = await evaluate(`(() => { const row = [...document.querySelectorAll('#checksBody .ck')].find((d) => d.querySelector('b')?.textContent === 'G13');
    if (!row) return null; row.click(); const pre = row.querySelector('.t pre');
    return { open: row.classList.contains('open'), visible: !!pre && pre.offsetHeight > 0, text: pre ? pre.textContent : '' }; })()`);
  out.traffic = await evaluate(`[...document.querySelectorAll('#trafficBody .k')].map((k) => k.textContent + ': ' + k.nextElementSibling.textContent)`);
  out.floorplan = await evaluate(`(() => { const b = document.getElementById('bFloor'); if (b.hidden) return { button: false };
    b.click(); const v = document.getElementById('floorView');
    return { button: true, open: v.classList.contains('open') && v.offsetHeight > 0, head: document.getElementById('fvText').textContent.slice(0, 4000).split('\\n').find((l) => l.startsWith('floorplan ')) || '',
             download: document.getElementById('fvSave').download }; })()`);
  // the editor writes the floorplan language: open it on the top gadget and ask for the text
  out.exported = await evaluate(`(() => { const E = window.GADGET_EDITOR; if (!E) return null;
    E.open(window.GADGETS.M.gir.top); const t = E.floorplan(); E.close(); return t; })()`);
  if (shot) {
    const s = await send("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(shot, Buffer.from(s.result.data, "base64"));
    await evaluate("document.getElementById('fvClose').click()");
  }
} catch (e) {
  out.fatal = String(e);
}
console.log(JSON.stringify(out));
ws.close();
chrome.kill();
try { fs.rmSync(udd, { recursive: true, force: true }); } catch (e) { /* Chrome may still hold it */ }
