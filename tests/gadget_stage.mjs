// THE DESIGN CANVAS'S STAGE, measured: node tests/gadget_stage.mjs <build dir> [samples]
//
// `tests/test_viz_js.py` asserts two properties of the studio stage -- no two ions ever
// overlap, and no ion jumps at a frame boundary -- by running the emitted page.  The
// gadget Design canvas had no equivalent, and it drew 41% of its sampled instants with two
// ions through each other, the worst pair at a separation of exactly zero, plus 11,163
// sideways flicks as `app.js::spread`'s three-decimal coincidence test switched on and off
// between adjacent animation frames.
//
// This harness measures the same two properties on the same model the page draws from:
// `core.js::LeafSim.state` for the positions (which is `qccd/viz/js/transit.js` underneath)
// and `core.js::ionRadius` for the mark, so nothing here is a second opinion about what
// the canvas does.  Everything is in DEVICE UNITS, which is what `LeafSim` works in; the
// camera scales positions and radii together and cannot turn an overlap into a gap.
//
// Prints one JSON object:
//   overlaps   samples in which two ion discs are drawn through each other
//   seams      ions whose position at the end of one instruction is not where the next
//              instruction starts them
//   conflicts  how many times a mover's route met an ion standing in it -- so a run that
//              reports zero overlaps because nothing ever got close says so
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
new Function(readFileSync(join(here, "..", "qccd", "viz", "js", "transit.js"), "utf8"))();
new Function(readFileSync(join(here, "..", "qccd", "gadget", "web", "core.js"), "utf8"))();
const { LeafSim, ionRadius } = globalThis.GadgetCore;

const dir = process.argv[2];
const NS = +(process.argv[3] || 6);
const ONLY = (process.argv.find((a) => a.startsWith("--leaf=")) || "").slice(7);
const CAP = +((process.argv.find((a) => a.startsWith("--steps=")) || "--steps=1e9").split("=")[1]);

// qccd/viz/theme.py GEOMETRY, as app.js reads it
const K_ION = 0.175 + 0.065, K_REST = 0.13;

const out = { dir, samples: NS, leaves: {} };
let nSamples = 0, nOverlap = 0, nSeam = 0, nConflict = 0;
let worstOverlap = null, worstSeam = null;

for (const file of readdirSync(join(dir, "leaf"))) {
  if (ONLY && !file.startsWith(ONLY)) continue;
  const text = readFileSync(join(dir, "leaf", file), "utf8");
  const data = JSON.parse(text.slice(text.indexOf("(") + 1, text.lastIndexOf(")")));
  const sim = new LeafSim(data);
  const g = sim.geom.gd, rIon = K_ION * g, rRest = K_REST * g;
  const rec = { gd: g, ops: 0, steps: 0, samples: 0, overlaps: 0, seams: 0, conflicts: 0 };

  for (const op in data.programs) {
    const P = sim.prepare(op);
    if (!P) continue;
    rec.ops++;
    let prevEnd = null;
    const n = Math.min(P.steps.length, CAP);
    for (let k = 0; k < n; k++) {
      const st = P.steps[k];
      rec.steps++;
      for (let s = 0; s <= NS; s++) {
        const local = st.t0 + (st.t1 - st.t0) * (s / NS);
        const S = sim.state(op, local);
        if (!S) continue;
        rec.samples++; nSamples++;
        if (s === 0 && S.passes) { rec.conflicts += S.passes.pairs.length; nConflict += S.passes.pairs.length; }
        const pts = S.ions.map((name, i) => ({
          name, x: S.xy[i][0], y: S.xy[i][1],
          r: ionRadius(S.live ? S.live[name] : null, rIon, rRest, 1, S.active.includes(i)),
        }));
        // overlap, by spatial hash: two discs whose centres are closer than the larger
        // radius are drawn one inside the other
        let maxR = 0;
        for (const q of pts) maxR = Math.max(maxR, q.r);
        const cell = Math.max(1e-9, 2 * maxR), H = new Map();
        for (const q of pts) {
          const key = Math.floor(q.x / cell) + ":" + Math.floor(q.y / cell);
          (H.get(key) || H.set(key, []).get(key)).push(q);
        }
        let hit = 0, worst = 0, pair = null;
        for (const q of pts) {
          const i = Math.floor(q.x / cell), j = Math.floor(q.y / cell);
          for (let di = -1; di <= 1; di++) for (let dj = -1; dj <= 1; dj++) {
            for (const o of H.get((i + di) + ":" + (j + dj)) || []) {
              if (o.name <= q.name) continue;
              const d = Math.hypot(q.x - o.x, q.y - o.y), lim = Math.max(q.r, o.r);
              if (d < lim) {
                hit++;
                const pen = (lim - d) / lim;
                if (pen > worst) { worst = pen; pair = [q.name, o.name, +d.toFixed(5), +lim.toFixed(5)]; }
              }
            }
          }
        }
        if (hit) {
          rec.overlaps++; nOverlap++;
          if (!worstOverlap || worst > worstOverlap.pen)
            worstOverlap = { pen: +worst.toFixed(4), leaf: data.master, op, step: k,
                             t: s / NS, pair, hits: hit };
        }
        const m = new Map(pts.map((q) => [q.name, q]));
        if (s === 0 && prevEnd) {
          for (const [name, q] of m) {
            const b = prevEnd.get(name);
            if (!b) continue;
            const d = Math.hypot(q.x - b.x, q.y - b.y);
            if (d > 1e-9) {
              rec.seams++; nSeam++;
              if (!worstSeam || d > worstSeam.d)
                worstSeam = { d: +d.toFixed(6), leaf: data.master, op, between: [k - 1, k], ion: name };
            }
          }
        }
        if (s === NS) prevEnd = m;
      }
    }
  }
  out.leaves[data.master] = rec;
}
out.total = { samples: nSamples, overlaps: nOverlap, seams: nSeam, conflicts: nConflict,
              pct_overlap: +(100 * nOverlap / Math.max(1, nSamples)).toFixed(3) };
out.worst_overlap = worstOverlap;
out.worst_seam = worstSeam;
console.log(JSON.stringify(out));
