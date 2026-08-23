// Execute an emitted QCCD page under the same minimal DOM shim `census.mjs` uses, and
// report what the ARCHITECTURE and HARDWARE PROGRAM panels actually render.
//
// census.mjs measures the stage (ion overlap, frame-boundary jumps).  This measures the
// dock: that every animated instruction has a listing row, that the row cursor follows
// `frame`, that the architecture statement which authorised the instruction is lit, and
// that the control-plane strip says something.  Same shim, deliberately: a page that
// needs a DOM API the shim does not stub is a page that has stopped being testable.
//
// THIS HARNESS HAS NO EDIT LANE, deliberately.  Its central assertion is `cursor === f` on
// every probe, and a page whose compiled programme no longer fits its device stops the
// animation -- `drawInvalid()` returns before `syncCursor()`, so the listing cursor stays
// where it was.  That is correct (nothing is executing), but it means this harness must
// never be pointed at an edited page without accounting for it.  Use `census.mjs --edit`,
// which measures the property that survives an edit.
import { loadPage } from './shim.mjs';

const file = process.argv[2];
const driver = `
;globalThis.__panel = (fi) => {
  frame = fi; phase = 1; draw();
  const live = [];
  for (const r of document.getElementById('pWin').children) {
    if (r.style.display === 'none' || r._i < 0) continue;
    live.push({ row: r._i, id: r._ref ? r._ref.id : null,
                op: r._k[1].textContent, args: r._k[3].innerHTML || '' });
  }
  const arch = [];
  for (const r of document.getElementById('aWin').children) {
    if (r.style.display === 'none' || r._i < 0) continue;
    arch.push({ row: r._i, text: (r._k[1].innerHTML || '').slice(0, 200) });
  }
  return {
    rows: PLIST.rows(), archRows: ALIST.rows(),
    cursor: PLIST.cursor(), archCursor: ALIST.cursor(),
    pool: document.getElementById('pWin').children.length,
    archPool: document.getElementById('aWin').children.length,
    now: document.getElementById('pNow').innerHTML,
    foot: document.getElementById('pFoot').innerHTML,
    live, arch,
  };
};
globalThis.__meta = () => ({
  frames: P.frames.length,
  listingRows: D.listing ? D.listing.ids.length : 0,
  ids: D.listing ? D.listing.ids : [],
  frameIds: P.frames.map(f => f.id),
  ctlRecords: D.control ? D.control.records.length : 0,
  ctlIndex: D.control ? D.control.index.length : 0,
  archLines: A.listing ? A.listing.lines.length : 0,
  archIndexKeys: A.listing ? Object.keys(A.listing.index).length : 0,
  archRoundTrip: A.listing ? A.listing.round_trip : null,
  provSites: D.prov ? D.prov.sites.length : 0,
});
`;

try { loadPage(file, driver); }
catch (e) { console.log(JSON.stringify({ error: 'eval', message: e.message, stack: e.stack })); process.exit(2); }

const meta = globalThis.__meta();
const N = meta.frames;
const MAXF = Math.min(N, +(process.argv[3] || 40));

let cursorWrong = 0, missingRow = 0, archLit = 0, ctlLines = 0, probeErr = null;
const samples = [];
for (let f = 0; f < MAXF; f++) {
  let r;
  try { r = globalThis.__panel(f); }
  catch (e) { probeErr ||= (e.message + ' @frame ' + f); break; }
  if (r.cursor !== f) cursorWrong++;
  const shown = r.live.find(x => x.row === f);
  if (!shown || shown.id !== meta.frameIds[f]) missingRow++;
  if (r.archCursor >= 0) archLit++;
  if (/control:/.test(r.now)) ctlLines++;
  if (f < 4) samples.push({ frame: f, cursor: r.cursor, archCursor: r.archCursor,
                            now: r.now.slice(0, 260), foot: r.foot.slice(0, 160) });
}

const last = globalThis.__panel(Math.min(N - 1, MAXF - 1));
console.log(JSON.stringify({
  file: file.split(/[\\/]/).pop(),
  ...meta,
  probed: MAXF,
  cursor_wrong: cursorWrong,
  rows_missing: missingRow,
  arch_lit: archLit,
  control_lines: ctlLines,
  pool: last.pool, arch_pool: last.archPool,
  samples,
  probe_error: probeErr,
}, null, 1));
