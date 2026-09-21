// THE OCCUPANCY LAW, run from the command line: node tests/transit.mjs <scenario.json>
//
// `qccd/viz/js/transit.js` is what two canvases run and `qccd/viz/transit.py` is its twin
// for the renderers with no JavaScript in them.  Twins are compared, not trusted, so this
// harness evaluates the JS side of a scenario and prints what it placed;
// `tests/test_transit_parity.py` computes the same scenario in Python and requires
// agreement to 1e-9.
//
// A scenario is deliberately plain -- nodes, an axis and a capacity each, one slot pitch,
// and a list of steps -- because the point is to pin the LAW, not a device.  The slot rule
// is stated here and in the Python test in the same words for the same reason.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
new Function(readFileSync(join(here, "..", "qccd", "viz", "js", "transit.js"), "utf8"))();

const S = JSON.parse(readFileSync(process.argv[2], "utf8"));
const NS = S.samples === undefined ? 4 : S.samples;
const pitch = S.pitch === undefined ? 0.3 : S.pitch;

const T = new globalThis.QCCDTransit.Transit({
  pos: (id) => S.nodes[id] || null,
  axis: (id) => (S.axis && S.axis[id]) || [1, 0],
  site: (id) => (S.site && S.site[id]) || id,
  slotOffsets: (id, k) => {
    const off = [];
    for (let j = 0; j < k; j++) off.push((j - (k - 1) / 2) * pitch);
    return { off, pitch };
  },
  bow: S.bow === undefined ? 0.5 : S.bow,
  // the metal: half a bar's length and thickness, half a rail's width (0 = none given)
  span: S.span === undefined ? undefined : () => S.span,
  across: S.across === undefined ? undefined : () => S.across,
  rail: S.rail || 0,
});

const ord = T.slotOrder(S.steps);
const out = { order: ord, frames: [] };
for (let k = 0; k < S.steps.length; k++) {
  const st = S.steps[k];
  for (let s = 0; s <= NS; s++) {
    const PL = T.place({
      before: st.before, pos: st.pos, paths: st.paths || {},
      t: s / NS, rest: false,
      ordStart: k > 0 ? ord[k - 1] : {}, ordEnd: ord[k],
    });
    const row = {};
    for (const ion in PL.live) {
      const p = PL.live[ion];
      row[ion] = [round(p.x), round(p.y), p.fly ? 1 : 0, p.swap ? 1 : 0, round(p.pitch || 0),
                  round(p.pitchA || 0), round(p.pitchB || 0), round(p.room || 0)];
    }
    out.frames.push({ step: k, t: s / NS, at: row,
                      passes: PL.passes.pairs, side: PL.passes.side });
  }
}
function round(v) { return Math.round(v * 1e9) / 1e9; }
console.log(JSON.stringify(out));
