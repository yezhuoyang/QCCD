// tests/roundtrip_census.mjs -- census.mjs, extended into a ROUND-TRIP harness.
//
//   node tests/roundtrip_census.mjs <page.html> <edit-name> <out.json> [censusFrames]
//
// census.mjs answers "does the emitted page draw a sane picture".  This answers the
// harder question: after an edit, does the architecture the page EXPORTS still describe
// the architecture the page BELIEVES it is showing -- when the export is taken all the
// way through Python's own parser and back.
//
// It is a COPY of census.mjs (which is left untouched) with three things bolted on:
//   * a belief snapshot, taken off EDITOR.state() -- the JS editor's own model;
//   * an edit lane, one named edit per process so a loss can be attributed to ONE edit
//     rather than to a cumulative script;
//   * the exports (arch json, python listing, edit record) written out for Python.
// The overlap/snap census still runs, before and after, because an edit that silently
// breaks the picture is also a round-trip failure.
import fs from 'fs';
import { loadPage } from './shim.mjs';

const file = process.argv[2];
const editName = process.argv[3] || 'none';
const outPath = process.argv[4];
const CENSUS_FRAMES = +(process.argv[5] || 6);

globalThis.__QCCD_SYNC = true;   // drain the debounced re-pricer synchronously

// ---------------------------------------------------------------- the page hook
const driver = `
;globalThis.__probe = (fi, ph) => {
  frame = fi; phase = ph; draw();
  const out = [];
  for (const ion in IONP) {
    const c = IONP[ion].c;
    if (!c || c.attrs.display === 'none') continue;
    out.push([ion, +c.attrs.cx, +c.attrs.cy, +c.attrs.r]);
  }
  return out;
};
globalThis.__nframes = () => P.frames.length;
globalThis.__Q = globalThis.QCCD;

// THE BELIEF SNAPSHOT.  Everything is read off EDITOR.state(), which is the model the
// stage draws from and the model the exporter serializes from -- so if these two ever
// disagree the page is lying to itself, not just to Python.
globalThis.__belief = () => {
  const ED = globalThis.EDITOR, Q = globalThis.QCCD;
  const st = ED.state();
  if (!st) return null;
  const dev = st.device;

  const nodeOrder = Object.keys(dev.nodes);
  const nodes = {}, caps = {}, explicit = [], zoneOf = {};
  let nsite = 0, njunc_kind = 0;
  for (const id of nodeOrder) {
    const n = dev.nodes[id];
    const z = (n.zone === undefined) ? null : n.zone;
    nodes[id] = { pos: [Number(n.pos[0]), Number(n.pos[1])], kind: n.kind, zone: z,
                  cap: Math.trunc(Number(n.cap || 0)),
                  explicit: !!n.capacity_explicit,
                  labels: (n.labels || []).slice() };
    zoneOf[id] = z;
    if (n.kind === 'site') { nsite++; caps[id] = Math.trunc(Number(n.cap || 0)); }
    else njunc_kind++;
    if (n.capacity_explicit) explicit.push(id);
  }

  const segOrder = Object.keys(dev.segments);
  const segs = {};
  for (const id of segOrder) {
    const s = dev.segments[id];
    segs[id] = { ends: [s.a, s.b], length: Number(s.length), cap: Math.trunc(Number(s.cap)),
                 loop: (s.loop === undefined) ? null : s.loop,
                 labels: (s.labels || []).slice() };
  }

  // ORDER MATTERS -- an array, not a map, and each loop's node list kept as written.
  const loops = Object.keys(dev.loops).map(function (lid) {
    const l = dev.loops[lid];
    return { id: lid, kind: l.kind, closed: !!l.closed,
             note: (l.note === undefined) ? null : l.note, nodes: l.nodes.slice() };
  });

  const deg = Q.degrees(dev);
  const degHist = {};
  deg.forEach(function (d) { degHist[d] = (degHist[d] || 0) + 1; });
  const degSorted = {};
  Object.keys(degHist).map(Number).sort(function (a, b) { return a - b; })
    .forEach(function (d) { degSorted[d] = degHist[d]; });

  const classes = (st.control && st.control.classes) || {};
  const declared = (classes.extra || []).map(function (c) { return JSON.parse(JSON.stringify(c)); });

  const hw = Q.hardwareReport(dev, st.control, st.budget, st.name);

  return {
    name: st.name,
    description: st.description === undefined ? null : st.description,
    generator: dev.generator,
    params: JSON.parse(JSON.stringify(dev.params || {})),
    n_nodes: nodeOrder.length,
    n_sites: nsite,
    n_junction_kind: njunc_kind,
    n_junction_degree: Q.junctionNodes(dev).length,
    n_segments: segOrder.length,
    node_order: nodeOrder,
    seg_order: segOrder,
    nodes: nodes, segments: segs,
    loops: loops,
    loop_order: loops.map(function (l) { return l.id; }),
    capacities: caps,
    capacity_explicit: explicit.slice().sort(),
    total_capacity: Q.totalCapacity(dev),
    degree_histogram: degSorted,
    zone_types: JSON.parse(JSON.stringify(st.zone_types || {})),
    zone_of_site: zoneOf,
    zones_in_use: Array.from(new Set(Object.values(zoneOf).filter(function (z) { return z !== null; }))).sort(),
    classes_block: JSON.parse(JSON.stringify(classes)),
    declared_classes: declared,
    declared_class_ids: declared.map(function (c) { return c.id; }),
    control: JSON.parse(JSON.stringify(st.control || {})),
    channels_spec: JSON.parse(JSON.stringify((st.control || {}).channels || null)),
    control_plane: { n_shared: hw.dacs_broadcast, n_compensation: hw.dacs_compensation,
                     n_channels: hw.dacs, electrodes: hw.electrodes, switches: hw.switches },
    curves: JSON.parse(JSON.stringify(st.primitives || {})),
    budget: JSON.parse(JSON.stringify(st.budget || {})),
    heating: JSON.parse(JSON.stringify(st.heating || {})),
    species: JSON.parse(JSON.stringify(st.species || {})),
    provenance: JSON.parse(JSON.stringify(st.provenance || {})),
    over_budget: hw.over_budget.slice()
  };
};
`;

let out = { file: file.split(/[\\/]/).pop(), edit: editName };
try { loadPage(file, driver); }
catch (e) { out.fatal = 'eval: ' + e.message; dump(); process.exit(2); }

const ED = globalThis.EDITOR, Q = globalThis.__Q;
if (!ED) { out.fatal = 'the page published no EDITOR'; dump(); process.exit(3); }
out.ready = ED.ready(); out.why = ED.why();
if (!out.ready) { dump(); process.exit(4); }

// ---------------------------------------------------------------- the census (verbatim)
function census(maxFrames) {
  const N = Math.min(globalThis.__nframes(), maxFrames);
  const SUB = 4;
  let probed = 0, overlapFrames = 0, worstOverlap = 0, worstPair = null;
  let worstSnap = 0, snapAt = null, probeErr = null, endOfPrev = null;
  for (let f = 0; f < N; f++) {
    for (let k = 0; k <= SUB; k++) {
      const ph = k / SUB;
      let pts;
      try { pts = globalThis.__probe(f, ph); }
      catch (e) { probeErr ||= e.message; continue; }
      probed++;
      let bad = false;
      for (let i = 0; i < pts.length; i++) for (let j = i + 1; j < pts.length; j++) {
        const a = pts[i], b = pts[j];
        if (!isFinite(a[3]) || !isFinite(b[3])) continue;
        const d = Math.hypot(a[1] - b[1], a[2] - b[2]);
        const depth = (a[3] + b[3]) - d;
        if (depth > 0.5) { bad = true;
          if (depth > worstOverlap) { worstOverlap = depth;
            worstPair = { frame: f, phase: ph, a: a[0], b: b[0], depth: +depth.toFixed(3) }; } }
      }
      if (bad) overlapFrames++;
      if (k === 0 && endOfPrev) {
        const cur = new Map(pts.map(function (p) { return [p[0], p]; }));
        for (const [ion, p] of endOfPrev) {
          const q = cur.get(ion); if (!q) continue;
          const jump = Math.hypot(p[1] - q[1], p[2] - q[2]);
          if (jump > worstSnap && jump > 1e-6) { worstSnap = jump; snapAt = { frame: f, ion: ion }; }
        }
      }
      if (k === SUB) endOfPrev = new Map(pts.map(function (p) { return [p[0], p]; }));
    }
  }
  return { frames: N, probed: probed, overlap_frames: overlapFrames,
           worst_overlap_px: +worstOverlap.toFixed(3), worst_pair: worstPair,
           worst_boundary_snap_px: +worstSnap.toFixed(3), snap_at: snapAt,
           probe_error: probeErr };
}

// ---------------------------------------------------------------- the edits
const B = globalThis.__belief();
const siteIds = B.node_order.filter(function (i) { return B.nodes[i].kind === 'site'; });
const segIds = B.seg_order;
function pick(arr, frac) { return arr[Math.min(arr.length - 1, Math.floor(arr.length * frac))]; }

const EDITS = {
  none: function () { return { note: 'baseline: no edit at all' }; },

  // (1) MOVE A SITE -- the whole pointer gesture, through begin/move/drop, in model coords
  move_site: function () {
    const id = pick(siteIds, 0.25);
    const L = ED.layout(), n = B.nodes[id];
    ED.begin('site', id, L.ox + n.pos[0] * L.sx, L.oy + n.pos[1] * L.sy);
    const target = [n.pos[0] + 0.37, n.pos[1] - 0.21];
    const r = ED.move(L.ox + target[0] * L.sx, L.oy + target[1] * L.sy, { free: true });
    const d = ED.drop();
    return { target_id: id, from: n.pos, asked: target, landed: r ? [r.x, r.y] : null,
             problems: d ? d.problems.map(function (p) { return p.code; }) : null };
  },

  // (2) CHANGE A ZONE CAPACITY -- every inheriting site in that zone must follow
  zone_capacity: function () {
    const zone = B.zones_in_use[0];
    const was = (B.zone_types[zone] || {}).capacity;
    const r = ED.emit({ method: 'set_zone', args: [zone], kwargs: { capacity: 9 },
                        meta: { group: 'gz', src: 'text' } });
    return { zone: zone, was: was, now: 9, ok: r.ok,
             problems: (r.problems || []).map(function (p) { return p.code; }) };
  },

  // (3) ADD A SITE -- a topology edit, through the JS mirror of qccd/arch/edit.py
  add_site: function () {
    const near = pick(siteIds, 0.5), n = B.nodes[near];
    const r = ED.addSite(n.pos[0] + 0.5, n.pos[1] + 1.5, near);
    return { near: near, ok: r.ok, problems: r.problems.map(function (p) { return p.message; }) };
  },

  // (4) REMOVE A SEGMENT -- opens whatever loop it was on
  remove_segment: function () {
    const id = pick(segIds, 0.5);
    ED.select([{ kind: 'segment', id: id }]);
    const r = ED.removeSelected();
    return { target_id: id, ok: r.ok, problems: r.problems.map(function (p) { return p.message; }) };
  },

  // (5) DECLARE A CLASS -- ordered, and re-declaration replaces wholesale
  declare_class: function () {
    const r = ED.emit({ method: 'declare_class', args: ['dock_left'],
                        kwargs: { type: 'dock', orbit: 'any', delta: 0,
                                  entails: ['split', 'merge'], note: 'round-trip probe' },
                        meta: { group: 'gc', src: 'text' } });
    return { id: 'dock_left', ok: r.ok,
             problems: (r.problems || []).map(function (p) { return p.code; }) };
  },

  // (5b) RE-DECLARE AN EXISTING CLASS with fewer fields.  engine.js documents that this
  // MOVES the class to the end of the list and replaces it WHOLESALE -- both of which are
  // order-and-content facts Python has to agree with exactly.
  redeclare_class: function () {
    const first = B.declared_class_ids[0];
    const r = ED.emit({ method: 'declare_class', args: [first],
                        kwargs: { type: 'shift' },
                        meta: { group: 'gr', src: 'text' } });
    return { id: first, ok: r.ok,
             problems: (r.problems || []).map(function (p) { return p.code; }) };
  },

  // (2b) DECLARE A ZONE THAT DID NOT EXIST -- set_zone creates unknown zones silently
  new_zone: function () {
    const r = ED.emit({ method: 'set_zone', args: ['probe_zone'],
                        kwargs: { capacity: 3, gate: true, spam: false, cool: true,
                                  note: 'declared by the round-trip harness' },
                        meta: { group: 'gn', src: 'text' } });
    return { ok: r.ok, problems: (r.problems || []).map(function (p) { return p.code; }) };
  },

  // (6) AN EXPLICIT PER-SITE CAPACITY OVERRIDE -- the field resolve_capacities must honour
  site_capacity: function () {
    const id = pick(siteIds, 0.75);
    const r = ED.emit({ method: 'set_site_capacity', args: [id, 7], kwargs: {},
                        meta: { group: 'gs', src: 'text' } });
    return { target_id: id, ok: r.ok,
             problems: (r.problems || []).map(function (p) { return p.code; }) };
  },

  // (7) A CURVE -- primitives are the priced part of the document
  set_curve: function () {
    const r = ED.emit({ method: 'set_curve',
                        args: ['shuttle', [{ us: 33.5, quanta: 0.75 },
                                           { us: 12.25, quanta: 3.5 }]],
                        kwargs: { table: 'local', source: 'harness' },
                        meta: { group: 'gv', src: 'text' } });
    return { ok: r.ok, problems: (r.problems || []).map(function (p) { return p.code; }) };
  },

  // (7b) THE SAME CURVE with a table name the JSON SCHEMA does not allow.  The browser
  // has no schema, so it accepts this happily; the export is then refused outright by
  // `Architecture.from_json`.  That is a round-trip failure of a different shape --
  // nothing is silently lost, everything is.
  set_curve_bad_table: function () {
    const r = ED.emit({ method: 'set_curve',
                        args: ['shuttle', [{ us: 33.5, quanta: 0.75 }]],
                        kwargs: { table: 'roundtrip_probe', source: 'harness' },
                        meta: { group: 'gv', src: 'text' } });
    return { ok: r.ok, problems: (r.problems || []).map(function (p) { return p.code; }) };
  },

  // (7c) A DEGREE-KEYED CURVE.  JS object keys are STRINGS, Python dict keys are INTS,
  // and the schema says the curve_by_degree keys are integer-ish strings -- so this is
  // where a key-type slip would show up.
  set_degree_curve: function () {
    const r = ED.emit({ method: 'set_degree_curve',
                        args: ['junction_cross', 5, [{ us: 101.5, quanta: 2.25 }]],
                        kwargs: { table: 'local', source: 'harness' },
                        meta: { group: 'gd', src: 'text' } });
    return { ok: r.ok, problems: (r.problems || []).map(function (p) { return p.code; }) };
  },

  // (8) THE BUDGET, which the hardware report checks against
  set_budget: function () {
    const r = ED.emit({ method: 'set_budget', args: [],
                        kwargs: { max_dacs: 12, max_junctions: 3, max_area_mm2: 42.5 },
                        meta: { group: 'gb', src: 'text' } });
    return { ok: r.ok, problems: (r.problems || []).map(function (p) { return p.code; }) };
  },

  // (9) ADD A SITE AND WIRE IT IN -- the orphan gesture followed by two chords, which is
  // the only way the shipped editor can produce a *connected* new site
  add_site_wired: function () {
    const near = pick(siteIds, 0.5), n = B.nodes[near];
    const r = ED.addSite(n.pos[0] + 0.5, n.pos[1] + 1.5, near);
    if (!r.ok) return { ok: false, problems: r.problems.map(function (p) { return p.message; }) };
    const fresh = ED.state().device.nodes;
    let added = null;
    for (const k in fresh) if (!B.nodes[k]) added = k;
    const r2 = ED.addSegment(added, near);
    const other = siteIds[(siteIds.indexOf(near) + 1) % siteIds.length];
    const r3 = ED.addSegment(added, other);
    return { added: added, near: near, other: other, ok: r2.ok && r3.ok,
             problems: r2.problems.concat(r3.problems).map(function (p) { return p.message; }) };
  },

  // (10) REMOVE A SITE -- mend:'splice', so the loop's NODE ORDER changes
  remove_site: function () {
    const id = pick(siteIds, 0.33);
    ED.select([{ kind: 'site', id: id }]);
    const r = ED.removeSelected();
    return { target_id: id, ok: r.ok, problems: r.problems.map(function (p) { return p.message; }) };
  },

  // (11) RETUNE THE CONTROL PLANE ITSELF -- the channel count is derived, so this is the
  // field most likely to be recomputed on one side and copied on the other
  channels: function () {
    const r = ED.emit({ method: 'set_control', args: [],
                        kwargs: { channels: { grouping: 'row_column',
                                              roles: { linear_h: 2, linear_v: 3 },
                                              differential: 1, switch_per_site: false } },
                        meta: { group: 'gh', src: 'text' } });
    return { ok: r.ok, problems: (r.problems || []).map(function (p) { return p.code; }) };
  },

  // (12) THE WIRING AGGREGATES, which feed the compensation-channel count
  set_wiring: function () {
    const r = ED.emit({ method: 'set_wiring', args: [],
                        kwargs: { shim_per_dac: 7, electrodes_per_trap: 30,
                                  compensation_electrodes_per_trap: 5 },
                        meta: { group: 'gw', src: 'text' } });
    return { ok: r.ok, problems: (r.problems || []).map(function (p) { return p.code; }) };
  },

  // (13) MOVE A SITE OFF-AXIS far enough to bend the rail -- this changes `corner`,
  // `corner_endpoints` and therefore what R18 charges, all of which Python RECOMPUTES and
  // then checks the exported document against
  move_site_bend: function () {
    const id = pick(siteIds, 0.6);
    const L = ED.layout(), n = B.nodes[id];
    ED.begin('site', id, L.ox + n.pos[0] * L.sx, L.oy + n.pos[1] * L.sy);
    const target = [n.pos[0] + 0.5, n.pos[1] + 2.0];
    ED.move(L.ox + target[0] * L.sx, L.oy + target[1] * L.sy, { free: true });
    const d = ED.drop();
    return { target_id: id, from: n.pos, asked: target,
             problems: d ? d.problems.map(function (p) { return p.code; }) : null };
  },

  // (14) ALL OF THEM, cumulatively -- an edit that survives alone but not in company is
  // still a lost edit
  all: function () {
    const log = {};
    const seq = ['move_site', 'zone_capacity', 'add_site', 'remove_segment',
                 'declare_class', 'site_capacity', 'set_curve', 'set_budget'];
    for (const k of seq) log[k] = EDITS[k]();
    return log;
  }
};

out.census_base = census(CENSUS_FRAMES);
out.before = B;
if (!EDITS[editName]) { out.fatal = 'unknown edit ' + editName; dump(); process.exit(5); }
try { out.edit_result = EDITS[editName](); }
catch (e) { out.edit_error = e.name + ': ' + e.message; }

out.after = globalThis.__belief();
out.census_after = census(CENSUS_FRAMES);
out.n_edits = ED.edits().length;
out.problems = ED.problems().map(function (p) { return p.code || p.message; });
out.lints = ED.lints().map(function (l) { return l.code; });
const pr = ED.price();
out.price_blocked = pr && pr.blocked ? pr.blocked.map(function (b) { return b.kind; }) : null;

// the three exports, whole -- a harness that truncates cannot tell a valid tail from a
// missing one
out.arch_json = ED.exportJson();
out.python = ED.exportPython();
out.edits_record = ED.exportEdits();
out.source = ED.source();
dump();

function dump() {
  const text = JSON.stringify(out, null, 1);
  if (outPath) fs.writeFileSync(outPath, text);
  else console.log(text);
}
