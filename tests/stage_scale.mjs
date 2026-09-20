// THE STAGE AT SEVERAL SIZES: node tests/stage_scale.mjs <page.html> [frames] [samples]
//
// The drawing is re-fitted whenever the window changes, which moves every mark and every
// electrode.  Two things must survive that and neither is obvious from reading the code:
// the ions must still not overlap (the fit changes `L.g`, and every mark is a fraction of
// it, so a rule that only happens to hold at one size is not a rule), and the picture must
// stay the SAME PICTURE -- a similarity transform of itself, not a re-arrangement.
//
// The second is what catches an occupancy rule that is secretly reading pixels: if which
// slot an ion holds depended on the drawn distance rather than on the device, an ion would
// change places with its trap-mate when you made the window narrower.
//
// Prints one JSON object with a record per size.
import fs from "fs";
import { loadPage } from "./shim.mjs";
import { PAGE_HOOK } from "./drive.mjs";

globalThis.__QCCD_SYNC = true;
const page = process.argv[2];
const FRAMES = +(process.argv[3] || 24);
const NS = +(process.argv[4] || 4);
// 800 is below the page's own narrow threshold (900), so every device reaches at least
// two panel regimes whatever its aspect ratio: a square lattice is never 'wide'.
const SIZES = [[1600, 1000], [1180, 820], [800, 700], [2400, 1400]];

loadPage(page, PAGE_HOOK + "\n;globalThis.__scale={L:L,PAD_BY_SITE:PAD_BY_SITE,PAD_BY_SEG:PAD_BY_SEG,SLOTS:SLOTS," +
  "vb:()=>({x:VB.x,y:VB.y,w:VB.w,h:VB.h}),zoom:(k)=>{VB.w*=k;VB.h*=k;applyVB();return {x:VB.x,y:VB.y,w:VB.w,h:VB.h};}," +
  "trueScale:(on)=>{if(globalThis.EDITOR&&globalThis.EDITOR.setTrueScale){globalThis.EDITOR.setTrueScale(on);return true;}" +
  "if(typeof setTrueScale==='function'){setTrueScale(on);return true;}return false;}};\n");
const PG = globalThis.__page, X = globalThis.__scale;

function census() {
  const nf = Math.min(PG.nframes(), FRAMES);
  let overlaps = 0, samples = 0, worst = 0, pair = null;
  const first = [];
  for (let f = 0; f < nf; f++) {
    for (let s = 0; s <= NS; s++) {
      PG.drawAt(f, s / NS);
      const m = PG.ionMarks();
      samples++;
      if (!first.length) for (const a of m) first.push([a[0], a[1], a[2]]);
      let maxR = 0;
      for (const a of m) maxR = Math.max(maxR, a[3]);
      const cell = Math.max(1e-9, 2 * maxR), H = new Map();
      for (const a of m) {
        const k = Math.floor(a[1] / cell) + ":" + Math.floor(a[2] / cell);
        (H.get(k) || H.set(k, []).get(k)).push(a);
      }
      let hit = 0;
      for (const a of m) {
        const i = Math.floor(a[1] / cell), j = Math.floor(a[2] / cell);
        for (let di = -1; di <= 1; di++) for (let dj = -1; dj <= 1; dj++) {
          for (const b of H.get((i + di) + ":" + (j + dj)) || []) {
            if (b[0] <= a[0]) continue;
            const d = Math.hypot(a[1] - b[1], a[2] - b[2]), lim = Math.max(a[3], b[3]);
            if (d < lim) {
              hit++;
              const pen = (lim - d) / lim;
              if (pen > worst) { worst = pen; pair = [a[0], b[0], +d.toFixed(3), +lim.toFixed(3)]; }
            }
          }
        }
      }
      if (hit) overlaps++;
    }
  }
  // the slot each ion holds, at the end of every frame walked: this must not depend on
  // the drawn size at all
  const slots = [];
  for (let f = 0; f < nf; f++) {
    const row = {};
    for (const site in (X.SLOTS[f] || {})) row[site] = X.SLOTS[f][site].join(",");
    slots.push(row);
  }
  let pads = 0, north = 0, south = 0, lone = 0;
  for (const tbl of [X.PAD_BY_SITE, X.PAD_BY_SEG]) {
    for (const k in tbl) for (const p of tbl[k]) {
      pads += p.pads.length;
      if (p.pads.length < 2) lone++;
      for (const q of p.pads) { if (q.sign > 0) north++; else south++; }
    }
  }
  return { samples, overlap_samples: overlaps, worst_overlap: +worst.toFixed(4), worst_pair: pair,
           g: X.L.g, sx: X.L.sx, sy: X.L.sy, W: X.L.W, H: X.L.H,
           marks: first, slots, pads, pads_north: north, pads_south: south, pairs_with_one_pad: lone };
}

const out = { page: page.replace(/\\/g, "/").split("/").pop(), sizes: [] };
for (const [w, h] of SIZES) {
  globalThis.window.innerWidth = w;
  globalThis.window.innerHeight = h;
  if (PG.relayout) PG.relayout();
  const r = census();
  r.data_layout = PG.dataLayout ? PG.dataLayout() : null;
  out.sizes.push({ w, h, label: `${w}x${h}`, ...r });
}
// ZOOM is a viewBox change, which is why it cannot move a mark relative to another; and
// TRUE SCALE is the one control that re-fits the drawing itself (sx:sy becomes the
// technology's nm-per-unit ratio), which moves every mark and every electrode.  Both are
// censused the same way, because both are ways the reader changes the picture.
globalThis.window.innerWidth = 1600;
globalThis.window.innerHeight = 1000;
if (PG.relayout) PG.relayout();
for (const k of [0.25, 4]) {
  X.zoom(k);
  out.sizes.push({ w: 1600, h: 1000, label: `zoom x${k}`, vb: X.vb(), ...census() });
  X.zoom(1 / k);
}
out.true_scale_available = X.trueScale(true);
if (out.true_scale_available) {
  out.sizes.push({ w: 1600, h: 1000, label: "true scale", ...census() });
  X.trueScale(false);
  out.sizes.push({ w: 1600, h: 1000, label: "true scale off", ...census() });
}

// Is every size the same picture? Compare each against the first by the ratio of pairwise
// distances between the ion marks of frame 0: a similarity transform keeps them constant.
const base = out.sizes[0].marks;
for (const rec of out.sizes) {
  const m = rec.marks;
  let lo = Infinity, hi = -Infinity, n = 0;
  const step = Math.max(1, Math.floor(base.length / 40));
  for (let i = 0; i < base.length; i += step) {
    for (let j = i + step; j < base.length; j += step) {
      if (base[i][0] !== m[i][0] || base[j][0] !== m[j][0]) { rec.ion_order_changed = true; continue; }
      const d0 = Math.hypot(base[i][1] - base[j][1], base[i][2] - base[j][2]);
      const d1 = Math.hypot(m[i][1] - m[j][1], m[i][2] - m[j][2]);
      if (d0 < 1e-6) continue;
      const k = d1 / d0;
      lo = Math.min(lo, k); hi = Math.max(hi, k); n++;
    }
  }
  rec.similarity = { n, lo: +lo.toFixed(6), hi: +hi.toFixed(6),
                     spread: n ? +((hi - lo) / Math.max(1e-9, hi)).toFixed(6) : null };
  delete rec.marks;
}
// the slot assignment must be byte-identical across sizes
const s0 = JSON.stringify(out.sizes[0].slots);
out.slots_identical_across_sizes = out.sizes.every((r) => JSON.stringify(r.slots) === s0);
for (const r of out.sizes) delete r.slots;
console.log(JSON.stringify(out, null, 1));
