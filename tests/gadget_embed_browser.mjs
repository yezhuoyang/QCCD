// The tutorial's gadget figures, framed the way the tutorial frames them, in headless Chrome:
//   node tests/gadget_embed_browser.mjs <dir> <page.html>...
//
// Serves <dir> with the headers of a cross-origin-isolated page (COOP same-origin, COEP
// require-corp, CORP same-origin on every file) and opens, for each page, a parent page that
// holds it in an <iframe>.  Prints one JSON line per page with what it saw:
//   isolated   the parent is cross-origin isolated (else the test proves nothing)
//   errors     console errors and uncaught exceptions in either frame (must be none)
//   requests   every URL the figure asked for beyond itself (must be none)
//   handle     window.GADGET's keys, if the page has one
//   ready      GADGET.ready() after load
//   focus      for each id given in FOCUS: the answer, and what the page then shows
//   seek       GADGET.seek(us): the clock it reports
//   hash       the figure's own address after #f=&t= (read on load)
//   top        the parent's address at the end (a figure must never navigate it)
//   paths      repository paths a reader could see, rendered text (tests/_rendered_paths.mjs),
//              after load, after each focus and with every pane open (must be none); and
//              `planted`, the probe finding a path planted to prove it can
// SHOT=<dir> also writes a picture of each framed figure there.
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { findChrome } from "./chrome_path.mjs";
import { OPEN_PANES, PLANT, PROBE, UNPLANT } from "./_rendered_paths.mjs";

const dir = path.resolve(process.argv[2]);
const pages = process.argv.slice(3);
const FOCUS = (process.env.FOCUS || "").split(",").filter(Boolean);
const CHROME = findChrome();
if (!CHROME) { console.error("no Chrome found; set CHROME"); process.exit(2); }

const ISOLATED = { "Cross-Origin-Opener-Policy": "same-origin", "Cross-Origin-Embedder-Policy": "require-corp",
  "Cross-Origin-Resource-Policy": "same-origin" };
const server = http.createServer((req, res) => {
  const u = new URL(req.url, "http://x");
  if (u.pathname === "/__parent") {
    const src = u.searchParams.get("src");
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8", ...ISOLATED });
    res.end(`<!doctype html><meta charset="utf-8"><title>parent</title>
<body style="margin:0"><iframe id="fig" src="${src}" style="width:1200px;height:760px;border:0"></iframe></body>`);
    return;
  }
  const f = path.join(dir, decodeURIComponent(u.pathname));
  if (!f.startsWith(dir) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { "Content-Type": "text/html; charset=utf-8", ...ISOLATED });
  fs.createReadStream(f).pipe(res);
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const origin = `http://127.0.0.1:${server.address().port}`;

const port = 9990 + Math.floor(Math.random() * 40);
const udd = fs.mkdtempSync(path.join(os.tmpdir(), "qccd-embed-"));
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${udd}`,
  "--disable-extensions", "--no-first-run", "--no-default-browser-check", "--window-size=1300,900",
  "about:blank"], { stdio: "ignore" });
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
let errors = [], requests = [];
ws.addEventListener("message", (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  if (m.method === "Runtime.exceptionThrown") errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
  if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") errors.push(m.params.args.map((a) => a.value || a.description).join(" "));
  if (m.method === "Network.requestWillBeSent") requests.push(m.params.request.url);
  if (m.method === "Network.loadingFailed" && !m.params.canceled) errors.push(`load failed: ${m.params.errorText} ${m.params.blockedReason || ""}`);
});
const send = (method, params = {}) => new Promise((res) => { const id = ++seq; pending.set(id, res); ws.send(JSON.stringify({ id, method, params })); });
const evaluate = async (expr) => {
  const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.result.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description || "evaluate failed");
  return r.result.result.value;
};
await send("Runtime.enable");
await send("Page.enable");
await send("Network.enable");

const W = "document.getElementById('fig').contentWindow";
for (const page of pages) {
  errors = []; requests = [];
  const parent = `${origin}/__parent?src=${encodeURIComponent(page + (FOCUS.length ? "#f=" + encodeURIComponent(FOCUS[0]) + "&t=" + (process.env.AT || "1000") : ""))}`;
  await send("Page.navigate", { url: parent });
  for (let i = 0; i < 150; i++) {
    const ok = await evaluate(`(() => { try { const w = ${W}; return w && w.document.readyState === 'complete' && w.document.body && w.document.body.children.length > 0; } catch (e) { return false; } })()`).catch(() => false);
    if (ok) break;
    await new Promise((r) => setTimeout(r, 100));
  }
  await new Promise((r) => setTimeout(r, 600));
  const out = { page };
  const paths = {};
  const probe = async () => {
    for (const h of await evaluate(`${W}.eval(${JSON.stringify(PROBE)})`)) paths[h.path] = h;
  };
  await probe();
  out.isolated = await evaluate("window.crossOriginIsolated");
  out.hash = await evaluate(`${W}.location.hash`);
  out.handle = await evaluate(`${W}.GADGET ? Object.keys(${W}.GADGET) : null`);
  if (out.handle) {
    out.ready = await evaluate(`${W}.GADGET.ready()`);
    out.atLoad = await evaluate(`(() => { const G = ${W}.GADGETS; return { sel: G.S.sel, path: G.S.path, t: G.S.t }; })()`);
    out.focus = {};
    for (const id of FOCUS) {
      out.focus[id] = await evaluate(`(() => { const ok = ${W}.GADGET.focus(${JSON.stringify(id)}); const G = ${W}.GADGETS;
        return { ok, sel: G.S.sel, path: G.S.path, t: Math.round(G.S.t),
                 inspector: (${W}.document.getElementById('inspBody').textContent || '').slice(0, 80) }; })()`);
      await probe();
    }
    out.missing = await evaluate(`${W}.GADGET.focus("no-such-thing")`);
    out.seek = await evaluate(`${W}.GADGET.seek(1234)`);
    // every in-page link a reader can click: none may take the parent anywhere
    out.clicked = await evaluate(`(() => { const d = ${W}.document; let n = 0;
      for (const a of d.querySelectorAll('a[data-seek], a[data-sel]')) { a.click(); n++; if (n > 20) break; } return n; })()`);
    await new Promise((r) => setTimeout(r, 300));
  }
  const fig = `${origin}/${page}`;
  out.requests = [...new Set(requests)].filter((u) => u !== parent && u.split("#")[0] !== fig && !u.startsWith("data:") && !u.startsWith("blob:")
    && u !== `${origin}/favicon.ico`);   // the parent's, asked for by the browser
  out.top = await evaluate("location.href");
  out.topKept = out.top === parent;
  out.errors = errors;
  if (process.env.SHOT) {          // SHOT=<dir>: a picture of each framed figure
    const s = await send("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(path.join(process.env.SHOT, page.replace(/\.html$/, ".png")), Buffer.from(s.result.data, "base64"));
  }
  out.panes = await evaluate(`${W}.eval(${JSON.stringify(OPEN_PANES)})`);
  await new Promise((r) => setTimeout(r, 300));
  await probe();
  out.paths = Object.values(paths);
  await evaluate(`${W}.eval(${JSON.stringify(PLANT)})`);
  out.planted = (await evaluate(`${W}.eval(${JSON.stringify(PROBE)})`)).some((h) => h.path.includes("planted_probe"));
  await evaluate(`${W}.eval(${JSON.stringify(UNPLANT)})`);
  console.log(JSON.stringify(out));
}
ws.close();
chrome.kill();
server.close();
try { fs.rmSync(udd, { recursive: true, force: true }); } catch (e) { /* Chrome may still hold it */ }
process.exit(0);
