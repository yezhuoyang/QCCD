// Run an emitted page under the DOM shim and report what the 3-D stage WOULD draw.
//
//   node tests/stage3d.mjs <page.html>
//
// The 3-D stage is two parts (`qccd/viz/js/stage3d.js`): a pure one that turns the page's
// own data into records, and a renderer that copies the records into three.js meshes.  The
// shim has no canvas, so the renderer never starts here -- and that is the point: every
// position the stage can get wrong is computed in the pure half, and this reads it.
//
// For every frame at five phases, and for every ion, it reports the SVG mark `draw()` has
// just placed and where the 3-D stage puts the same ion.  tests/test_layers.py holds the
// two to each other: an ion's place in its layer is the mark's place in its panel, exactly.
import { loadPage } from './shim.mjs';

const file = process.argv[2];
const driver = `
;globalThis.__s3 = () => {
  const S = globalThis.STAGE3D;
  const out = { three: !!globalThis.QCCDTHREE, stage: !!S, checks: [] };
  if (!S) return out;
  out.available = S.available(); out.why = S.why();
  out.panels = L.panels || null;
  const D = S.describe();
  out.describe = { layers: D.layers.length, rails: D.rails.length, sites: D.sites.length,
                   juncs: D.juncs.length, risers: D.risers.length,
                   nodes: A.nodes.length, segments: A.segments.length,
                   flat: A.segments.filter(s => !s.riser).length,
                   railIds: Array.from(new Set(D.rails.map(r => r.id))).length };
  out.px = S.panels(); out.gap = S.layerGap(); out.lift = S.lift();
  for (let fi = 0; fi < P.frames.length; fi++) for (const ph of [0, 0.25, 0.5, 0.75, 1]) {
    frame = fi; phase = ph; draw();
    const last = S.last();
    const recs = S.ions(last.live, last.flying, last.f, last.stt);
    const pos = S.positions(last.live, last.flying, last.f, last.stt, 1);
    const flat = S.positions(last.live, last.flying, last.f, last.stt, 0);
    for (const d of recs) {
      const c = IONP[d.ion].c.attrs;
      out.checks.push({ frame: fi, phase: ph, ion: d.ion, cx: +c.cx, cy: +c.cy, panel: d.panel,
                        zf: d.zf, riser: d.riser, at: pos[d.ion], flat: flat[d.ion],
                        node: last.stt.pos[d.ion], nodeLayer: (nodeById[last.stt.pos[d.ion]] || {}).z || 0 });
    }
  }
  return out;
};`;
loadPage(file, driver);
console.log(JSON.stringify(globalThis.__s3()));
