// The JS mirror of `qccd/arch/edit.py` and of the three derived quantities in
// `qccd/arch/device.py` that a topology edit moves.
//
// THIS FILE IS THE ONLY COPY OF THIS CODE IN THE PROJECT.  `qccd/viz/render.py` inlines
// it verbatim into the emitted page (there is no second, hand-maintained copy in the
// render template) and `tests/edit_parity.mjs` imports this same file, so the JS the
// parity test proves equal to Python is byte-for-byte the JS that ships.
//
// It exists because the page must re-render and re-price after an edit without a server.
// It is a second implementation of one truth, which this codebase has been burned by
// before, so it comes with `tests/test_edit_parity.py`: that test drives BOTH
// implementations through the same randomised edit scripts on all nine shipped
// architectures and diffs the resulting devices, derived quantities and reports field by
// field.  Golden vectors would go stale silently; a differential run cannot.
//
// Conventions that keep the two sides comparable:
//   * a device here is {nodes:{id->node}, segments:{id->seg}, loops:{id->loop}} with
//     INSERTION ORDER significant, matching Python dicts;
//   * every float that Python rounds is rounded here with the same ROUND=9;
//   * an edit that Python raises TopologyError for throws EditError with the same
//     message, and the parity test diffs the messages too -- a mirror that refuses
//     different things is not a mirror.

'use strict';

// Wrapped in an IIFE so that inlining it into the page leaks exactly ONE name.  The page
// already defines `A`, `D`, `L`, `P`, `C`, `st`, `derived`-adjacent helpers and a hundred
// other short module-level identifiers, and a mirror that collided with any of them would
// fail in a way that looks like a rendering bug rather than a name clash.  It also has to
// parse as a plain <script>: top-level `export` is a syntax error outside a module, which
// is why this file is CommonJS-plus-global rather than ESM -- Node loads it as CJS (there
// is no package.json declaring "type":"module") and `tests/edit_parity.mjs` reads it back
// through `(await import(url)).default`.
const QCCD_EDIT = (function () {
'use strict';

const ROUND = 9;
const r9 = (x) => {
  // Python's round() is banker's rounding on ties; at 9 decimals over coordinates and
  // lengths the tie case does not arise for any value either side produces, but the
  // scaling is done identically so a tie would break BOTH the same way.
  const f = Math.pow(10, ROUND);
  const y = x * f;
  const fl = Math.floor(y), d = y - fl;
  let n;
  if (d > 0.5) n = fl + 1;
  else if (d < 0.5) n = fl;
  else n = (fl % 2 === 0) ? fl : fl + 1;
  return n / f + 0;
};

// The id grammar `qccd/arch/schema.py` enforces on every saved document.  SHIPPED, not
// copied: `export_schema().bounds.id_pattern` travels beside `min_loop_nodes` and this
// file keeps no pattern of its own.  It used to carry the literal, and a design palette
// mints an id on EVERY add -- the most-exercised path in the whole feature -- so a
// hand-copied grammar here is the second-source-of-truth shape this codebase keeps
// deleting.  JS `$` without the `m` flag matches only at end-of-string, and Python uses
// `re.fullmatch` rather than `$` on the other side, so the two agree on a trailing
// newline too.

class EditError extends Error {
  // `name` is not set by subclassing, and the parity harness maps EditError ->
  // TopologyError by name, so an unnamed subclass would make every refusal look like a
  // crash in the runner rather than the deliberate refusal it is.
  constructor(msg) { super(msg); this.name = 'EditError'; }
}

// ---------------------------------------------------------------- device primitives

function nodeIds(dev) { return Object.keys(dev.nodes); }
function segIds(dev) { return Object.keys(dev.segments); }

function incidence(dev) {
  const inc = {};
  for (const n of nodeIds(dev)) inc[n] = [];
  for (const sid of segIds(dev)) {
    const s = dev.segments[sid];
    for (const e of [s.a, s.b]) {
      if (!(e in inc)) throw new EditError(`segment '${sid}' references unknown node '${e}'`);
      inc[e].push(sid);
    }
  }
  return inc;
}

function degrees(dev) {
  const inc = incidence(dev), out = {};
  for (const n in inc) out[n] = inc[n].length;
  return out;
}

function other(seg, nid) { return seg.a === nid ? seg.b : seg.a; }

function segmentBetween(dev, a, b) {
  for (const sid of segIds(dev)) {
    const s = dev.segments[sid];
    if ((s.a === a && s.b === b) || (s.a === b && s.b === a)) return sid;
  }
  return null;
}

// Mirror of device._unit: the canonical unit direction, scaled by the largest |component|
// so float noise cannot make two collinear steps compare unequal.
function unit(pa, pb) {
  const n = Math.min(pa.length, pb.length);
  const d = [];
  let scale = 0;
  for (let i = 0; i < n; i++) { const c = pb[i] - pa[i]; d.push(c); scale = Math.max(scale, Math.abs(c)); }
  if (scale === 0) return d.map(() => 0);
  return d.map((c) => r9(c / scale) + 0);
}
const sameDir = (u, v) => u.length === v.length && u.every((c, i) => c === v[i]);

function loopCorners(dev, lid) {
  const lp = dev.loops[lid], seq = lp.nodes, k = seq.length, out = new Set();
  const lo = lp.closed ? 0 : 1, hi = lp.closed ? k : k - 1;
  for (let i = lo; i < hi; i++) {
    const prev = seq[(i - 1 + k) % k], nxt = seq[(i + 1) % k], here = seq[i];
    const P = dev.nodes[prev].pos, H = dev.nodes[here].pos, N = dev.nodes[nxt].pos;
    if (!sameDir(unit(P, H), unit(H, N))) out.add(here);
  }
  return out;
}

function geometricBends(dev) {
  const inc = incidence(dev), out = new Set();
  for (const nid in inc) {
    if (inc[nid].length !== 2) continue;
    const a = other(dev.segments[inc[nid][0]], nid);
    const b = other(dev.segments[inc[nid][1]], nid);
    const d1 = unit(dev.nodes[nid].pos, dev.nodes[a].pos);
    const d2 = unit(dev.nodes[nid].pos, dev.nodes[b].pos);
    if (!sameDir(d1.map((c) => -c + 0), d2)) out.add(nid);
  }
  return out;
}

function allCorners(dev) {
  const out = geometricBends(dev);
  for (const lid in dev.loops) for (const n of loopCorners(dev, lid)) out.add(n);
  return out;
}

function cornerEndpoints(dev) {
  const corners = allCorners(dev), out = {};
  for (const sid of segIds(dev)) {
    const s = dev.segments[sid];
    if (s.loop === null || s.loop === undefined || !(s.loop in dev.loops)) { out[sid] = 0; continue; }
    out[sid] = (corners.has(s.a) ? 1 : 0) + (corners.has(s.b) ? 1 : 0);
  }
  return out;
}

// The exact set the page ships precomputed from Python, recomputed here.  `derive()` is
// what the page's load-time self-check runs against the shipped values.
function derived(dev) {
  const deg = degrees(dev), corners = allCorners(dev), ce = cornerEndpoints(dev);
  const degree = {}, corner = {}, cornerEnd = {};
  for (const n of nodeIds(dev).slice().sort()) { degree[n] = deg[n]; corner[n] = corners.has(n); }
  for (const s of segIds(dev).slice().sort()) cornerEnd[s] = ce[s];
  return { degree, corner, corner_endpoints: cornerEnd };
}

function derivedDelta(before, after) {
  const diff = (a, b) => {
    const out = {};
    for (const k of Object.keys(a)) if (k in b && a[k] !== b[k]) out[k] = [a[k], b[k]];
    return out;
  };
  return [diff(before.degree, after.degree), diff(before.corner, after.corner),
          diff(before.corner_endpoints, after.corner_endpoints)];
}

function components(dev) {
  const adj = {};
  for (const n of nodeIds(dev)) adj[n] = [];
  for (const sid of segIds(dev)) {
    const s = dev.segments[sid];
    if (s.a in adj && s.b in adj) { adj[s.a].push(s.b); adj[s.b].push(s.a); }
  }
  const seen = new Set(), out = [];
  for (const start of nodeIds(dev).slice().sort()) {
    if (seen.has(start)) continue;
    const stack = [start], comp = [];
    seen.add(start);
    while (stack.length) {
      const cur = stack.pop();
      comp.push(cur);
      for (const nx of adj[cur]) if (!seen.has(nx)) { seen.add(nx); stack.push(nx); }
    }
    out.push(comp.sort());
  }
  out.sort((x, y) => (x[0] < y[0] ? -1 : x[0] > y[0] ? 1 : 0));
  return out;
}

// ------------------------------------------------------------------- schema bounds
//
// The one schema constant this file needs -- `$.geometry.loops[].nodes` min -- comes from
// `qccd/arch/schema.py::export_schema()['bounds']`, handed over by `editor.js` from the
// page's data blob (and by `tests/edit_parity.mjs` from the Python side of the corpus).
// It is NOT written down here.  A literal `2` would be a hand-copied schema bound, which
// is the same second-source-of-truth mistake as a hand-copied enum: guarded by a parity
// test, maybe, but still a copy that a schema change has to remember to update.  There is
// nothing to remember when there is nothing to update.
var BOUNDS = null;
function setBounds(b) {
  if (!b || typeof b.min_loop_nodes !== 'number' || typeof b.id_pattern !== 'string') {
    throw new EditError(
      'setBounds needs the bounds block from qccd/arch/schema.py export_schema(); ' +
      'this file keeps no copy of any schema constant and cannot check structure without it');
  }
  BOUNDS = b;
  return BOUNDS;
}
function minLoopNodes() {
  if (!BOUNDS) {
    throw new EditError('no schema bounds loaded: call QCCDEdit.setBounds(D.schema.bounds) first');
  }
  return BOUNDS.min_loop_nodes;
}

// ------------------------------------------------------------------------- structure

// Mirror of Device.check_structure (the graph half; the `declared` cross-check has no
// client-side counterpart because the page IS the declaration).
function checkStructure(dev) {
  const errors = [];
  for (const sid of segIds(dev)) {
    const s = dev.segments[sid];
    for (const e of [s.a, s.b]) if (!(e in dev.nodes)) errors.push(`segment '${sid}': unknown endpoint '${e}'`);
    if (s.a === s.b) errors.push(`segment '${sid}': self-loop is not a transport segment`);
    if (s.loop !== null && s.loop !== undefined && !(s.loop in dev.loops)) errors.push(`segment '${sid}': unknown loop '${s.loop}'`);
  }
  const seen = {};
  for (const sid of segIds(dev)) {
    const s = dev.segments[sid];
    const key = [s.a, s.b].slice().sort().join('\u0000');
    if (s.a !== s.b && key in seen) {
      errors.push(`segment '${sid}' duplicates '${seen[key]}' between ` +
                  `['${[s.a, s.b].slice().sort().join("', '")}'] -- parallel segments are not modelled`);
    }
    seen[key] = sid;
  }
  const minNodes = minLoopNodes();
  for (const lid in dev.loops) {
    const lp = dev.loops[lid];
    // THE INVARIANT THE SCHEMA ALWAYS HAD AND THIS CHECK DID NOT -- see the long comment
    // on the Python side in `Device.check_structure`.  A loop with fewer than two nodes
    // has no consecutive pair, walks no segment and cannot be written to an .arch.json.
    if (lp.nodes.length < minNodes) {
      errors.push(`loop '${lid}': has ${lp.nodes.length} node(s); a transport loop needs ` +
                  `at least ${minNodes}, because a shorter one has no segment to walk and ` +
                  `no .arch.json can hold it`);
    }
    if (new Set(lp.nodes).size !== lp.nodes.length) errors.push(`loop '${lid}': repeats a node`);
    for (const n of lp.nodes) if (!(n in dev.nodes)) errors.push(`loop '${lid}': unknown node '${n}'`);
    if (errors.length) continue;
    const seq = lp.nodes, k = seq.length, pairs = [];
    for (let i = 0; i < k - 1; i++) pairs.push([seq[i], seq[i + 1]]);
    if (lp.closed && k > 1) pairs.push([seq[k - 1], seq[0]]);
    // ONE per loop, not one per gap: Python walks the loop with `loop_segments`, which
    // RAISES on the first missing pair, so `check_structure` appends exactly one message
    // however many pairs are broken.  Reporting every gap agreed on the verdict and
    // disagreed on the count -- which is the shape of drift this whole file exists to
    // make impossible.
    for (const [a, b] of pairs) {
      if (segmentBetween(dev, a, b) === null) {
        errors.push(`loop '${lid}': "no segment between '${a}' and '${b}'"`);
        break;
      }
    }
  }
  for (const nid of nodeIds(dev)) {
    const n = dev.nodes[nid];
    if (n.kind === 'site' && n.cap < 1) errors.push(`node '${nid}': a site needs capacity >= 1`);
    if (n.kind === 'junction' && n.cap !== 0) errors.push(`node '${nid}': a bare junction holds no ions, so capacity must be 0`);
  }
  return errors;
}

// --------------------------------------------------------------- degenerate loops

// Mirror of `qccd/arch/edit.py::_prune_degenerate_loops`.  Read that docstring for the
// design argument: a one-node loop is REPAIRED away with a warning, never a reason to
// refuse the user's delete.
function pruneDegenerateLoops(loops, segments, warnings) {
  const minNodes = minLoopNodes(), dropped = [];
  for (const lid of Object.keys(loops)) {
    if (loops[lid].nodes.length >= minNodes) continue;
    const n = loops[lid].nodes.length;
    delete loops[lid];
    dropped.push(lid);
    warnings.push(`loop '${lid}' is down to ${n} node(s), fewer than the ${minNodes} a ` +
                  `transport loop needs, so it is DROPPED: it has no segment left to walk, ` +
                  `prices no corner, and no .arch.json can hold it`);
  }
  if (dropped.length) {
    let n = 0;
    for (const sid of Object.keys(segments)) {
      if (dropped.includes(segments[sid].loop)) { segments[sid].loop = null; n++; }
    }
    if (n) {
      warnings.push(`${n} segment(s) pointed at ${dropped.slice().sort().join(', ')} and ` +
                    `are now off every loop; they price no corner`);
    }
  }
  return dropped;
}

// ----------------------------------------------------------------------- loop helpers

function pairPositions(lp, a, b) {
  const seq = lp.nodes, k = seq.length, out = [];
  const hi = lp.closed ? k : k - 1;
  for (let i = 0; i < hi; i++) {
    const x = seq[i], y = seq[(i + 1) % k];
    if ((x === a && y === b) || (x === b && y === a)) out.push(i);
  }
  return out;
}

function adjacentOn(dev, a, b) {
  return Object.keys(dev.loops).filter((lid) => pairPositions(dev.loops[lid], a, b).length);
}

function idPattern() {
  if (!BOUNDS || typeof BOUNDS.id_pattern !== 'string') {
    throw new EditError('no schema bounds loaded: call QCCDEdit.setBounds(D.schema.bounds) first');
  }
  return new RegExp(BOUNDS.id_pattern);
}

function checkId(kind, value) {
  if (typeof value !== 'string' || !idPattern().test(value)) {
    throw new EditError(
      `'${value}' is not a usable ${kind} id: it must start with a letter or underscore ` +
      `and contain only letters, digits, and _ . : -`);
  }
  return value;
}

function fresh(prefix, taken) {
  const t = new Set(taken);
  if (!t.has(prefix)) return prefix;
  let i = 1;
  while (t.has(prefix + i)) i++;
  return prefix + i;
}

function splitLengths(dev, seg, pos) {
  const pa = dev.nodes[seg.a].pos, pb = dev.nodes[seg.b].pos;
  const dim = Math.min(pa.length, pb.length, pos.length);
  let denom = 0, num = 0;
  for (let i = 0; i < dim; i++) { const v = pb[i] - pa[i], w = pos[i] - pa[i]; denom += v * v; num += v * w; }
  let t = denom <= 0 ? 0.5 : num / denom;
  t = Math.min(Math.max(t, 0), 1);
  const total = seg.length, first = r9(total * t);
  return [first, r9(total - first)];
}

const clone = (d) => ({
  nodes: Object.fromEntries(Object.entries(d.nodes).map(([k, v]) => [k, Object.assign({}, v)])),
  segments: Object.fromEntries(Object.entries(d.segments).map(([k, v]) => [k, Object.assign({}, v)])),
  loops: Object.fromEntries(Object.entries(d.loops).map(([k, v]) => [k, Object.assign({}, v, { nodes: v.nodes.slice() })])),
  generator: d.generator, params: d.params,
});

// Rebuild `obj` with `key` reinserted at its original ordinal position.  Python's
// `dict[k] = v` on an existing key keeps its slot, and `del` then re-add moves it to the
// end; JS objects behave the same for string keys, so a subdivision that deletes E7 and
// adds E7a/E7b lands in the same relative order on both sides -- which matters because
// the emitted listing and the page both iterate segments in insertion order.
function replaceKeys(obj, key, entries) {
  const out = {};
  for (const [k, v] of Object.entries(obj)) {
    if (k === key) { for (const [nk, nv] of entries) out[nk] = nv; }
    else out[k] = v;
  }
  return out;
}

function finish(dev, report, before) {
  const errors = checkStructure(dev);
  if (errors.length) {
    throw new EditError(`${report.op} would leave ${errors.length} structural error(s):\n  ` +
                        errors.slice(0, 8).join('\n  '));
  }
  const after = derived(dev);
  const [dd, dc, dce] = derivedDelta(before, after);
  const comps = components(dev);
  const warnings = report.warnings.slice();
  if (comps.length > 1) {
    warnings.push(`the device is in ${comps.length} disconnected pieces ` +
                  `(${comps.map((c) => c.length).join(', ')} nodes); no ion can move between them`);
  }
  // Key ORDER matters: the parity harness compares dict order because the listing and
  // the page both iterate in insertion order, so the report is rebuilt here in exactly
  // the order `EditReport.to_json` emits rather than patched onto a partial object.
  return [dev, {
    op: report.op,
    nodes_added: report.nodes_added,
    nodes_removed: report.nodes_removed,
    segments_added: report.segments_added,
    segments_removed: report.segments_removed,
    loops_changed: report.loops_changed,
    loops_opened: report.loops_opened,
    loops_removed: report.loops_removed,
    degree_changed: dd,
    corner_changed: dc,
    corner_endpoints_changed: dce,
    disconnects: comps.length > 1,
    components_after: comps,
    orphaned: report.orphaned,
    warnings,
  }];
}

const blankReport = (op) => ({
  op, nodes_added: [], nodes_removed: [], segments_added: [], segments_removed: [],
  loops_changed: [], loops_opened: [], loops_removed: [], orphaned: [], warnings: [],
});

// ---------------------------------------------------------------------------- add

function addSite(dev, nodeId, pos, opts) {
  opts = opts || {};
  const kind = opts.kind || 'site';
  const zone = opts.zone === undefined ? null : opts.zone;
  const capacity = opts.capacity || 0;
  const labels = opts.labels || [];
  const on = opts.on === undefined ? null : opts.on;
  const to = opts.to || [];
  const segmentIds = opts.segment_ids || null;

  if (nodeId in dev.nodes) throw new EditError(`node id '${nodeId}' is already taken`);
  if (!nodeId) throw new EditError('a node needs an id');
  checkId('node', nodeId);
  if (on && to.length) throw new EditError(
    'give `on=` (subdivide a segment) or `to=` (wire to existing nodes), not both -- ' +
    'subdividing already wires the node to two neighbours');
  if (kind !== 'site' && kind !== 'junction') throw new EditError(`node kind must be 'site' or 'junction', not '${kind}'`);
  if (kind === 'junction' && (capacity || zone)) throw new EditError('a bare junction holds no ions: it has no zone and no capacity');
  if (kind === 'site' && capacity < 0) throw new EditError('capacity must be >= 0 (0 means inherit from the zone)');
  const explicit = kind === 'site' && !!capacity;
  let cap = capacity;
  if (kind === 'site' && !capacity) {
    const zt = opts.zone_types || {};
    if (zone === null || zone === undefined) throw new EditError(
      'a site needs capacity >= 1: pass capacity=, or zone= with the zone types so it can inherit one');
    const declared = Object.keys(zt);
    if (declared.length && !(zone in zt)) throw new EditError(
      `no zone type '${zone}' is declared (have: ${declared.sort().join(', ') || 'none'})`);
    cap = Number((zt[zone] || {}).capacity || 0);
    if (cap < 1) throw new EditError(
      `zone type '${zone}' declares capacity ${cap}; a site needs >= 1, so pass capacity= explicitly`);
  }
  if (!pos || !pos.length) throw new EditError('a node needs a position');

  const before = derived(dev);
  const d = clone(dev);
  d.nodes[nodeId] = {
    id: nodeId, pos: pos.map(Number), kind,
    cap: kind === 'site' ? cap : 0,
    zone: kind === 'site' ? zone : null,
    labels: labels.slice(),
    capacity_explicit: explicit,
  };
  const addedSegs = [], removedSegs = [], touched = [], warnings = [];

  if (on !== null) {
    if (!(on in dev.segments)) throw new EditError(`no such segment '${on}' to subdivide`);
    const seg = dev.segments[on], a = seg.a, b = seg.b;
    const ids = segmentIds ? segmentIds.slice() : [on + 'a', on + 'b'];
    if (ids.length !== 2) throw new EditError('subdividing a segment needs exactly two new segment ids');
    for (const sid of ids) {
      checkId('segment', sid);
      if (sid in dev.segments && sid !== on) throw new EditError(`segment id '${sid}' is already taken`);
    }
    const [la, lb] = splitLengths(dev, seg, pos.map(Number));
    d.segments = replaceKeys(d.segments, on, [
      [ids[0], Object.assign({}, seg, { id: ids[0], a, b: nodeId, length: la })],
      [ids[1], Object.assign({}, seg, { id: ids[1], a: nodeId, b, length: lb })],
    ]);
    removedSegs.push(on);
    addedSegs.push(ids[0], ids[1]);
    for (const lid of Object.keys(dev.loops)) {
      const hits = pairPositions(dev.loops[lid], a, b);
      if (!hits.length) continue;
      const seq = d.loops[lid].nodes;
      for (const i of hits.slice().sort((x, y) => y - x)) seq.splice(i + 1, 0, nodeId);
      touched.push(lid);
    }
  } else if (to.length) {
    const seen = new Set();
    for (let j = 0; j < to.length; j++) {
      const o = to[j];
      if (o === nodeId) throw new EditError('a segment from a node to itself is not transport');
      if (!(o in dev.nodes)) throw new EditError(`no such node '${o}' to wire '${nodeId}' to`);
      if (seen.has(o)) throw new EditError(`'${o}' given twice in \`to=\``);
      seen.add(o);
      const sid = (segmentIds && j < segmentIds.length) ? segmentIds[j]
        : fresh(`${nodeId}-${o}`, Object.keys(d.segments));
      checkId('segment', sid);
      if (sid in d.segments) throw new EditError(`segment id '${sid}' is already taken`);
      d.segments[sid] = { id: sid, a: o, b: nodeId, length: 1.0, cap: 1, loop: null, labels: ['spur'] };
      addedSegs.push(sid);
    }
  } else {
    warnings.push(`${nodeId} is wired to nothing; no ion can reach it until it gets a segment`);
  }

  const rep = Object.assign(blankReport(`add_${kind}`), {
    nodes_added: [nodeId], segments_added: addedSegs, segments_removed: removedSegs,
    loops_changed: Array.from(new Set(touched)).sort(),
    orphaned: (on !== null || to.length) ? [] : [nodeId],
    warnings,
  });
  return finish(d, rep, before);
}

function addJunction(dev, nodeId, pos, opts) {
  const [d, rep] = addSite(dev, nodeId, pos, Object.assign({}, opts || {}, { kind: 'junction' }));
  const deg = degrees(d)[nodeId];
  if (deg < 3) {
    rep.warnings.push(`${nodeId} is declared a junction but has degree ${deg}; R18 prices a ` +
                      `junction only at degree >= 3, so this node is charged as plain transport`);
  }
  return [d, rep];
}

function addSegment(dev, segId, a, b, opts) {
  opts = opts || {};
  const loop = opts.loop === undefined ? null : opts.loop;
  const labels = opts.labels || [];
  const length = opts.length === undefined ? 1.0 : opts.length;
  const capacity = opts.capacity === undefined ? 1 : opts.capacity;

  if (segId in dev.segments) throw new EditError(`segment id '${segId}' is already taken`);
  checkId('segment', segId);
  if (a === b) throw new EditError('a self-loop is not a transport segment');
  for (const e of [a, b]) if (!(e in dev.nodes)) throw new EditError(`no such node '${e}'`);
  const dup = segmentBetween(dev, a, b);
  if (dup !== null) throw new EditError(
    `${a} and ${b} are already joined by segment '${dup}'; parallel segments are not ` +
    `modelled (set_segment_length or set capacity on '${dup}' instead)`);
  if (length <= 0) throw new EditError('a segment must have positive length');
  if (capacity < 1) throw new EditError('a segment must be able to carry at least one ion');

  const before = derived(dev);
  const warnings = [];
  const onLoops = adjacentOn(dev, a, b);
  if (loop !== null) {
    if (!(loop in dev.loops)) throw new EditError(
      `no such loop '${loop}' (have: ${Object.keys(dev.loops).sort().join(', ') || 'none'})`);
    if (!onLoops.includes(loop)) throw new EditError(
      `${a} and ${b} are not consecutive in loop '${loop}', so a segment between them is ` +
      `a CHORD, not a loop edge.  A chord on a loop would be priced by corner_endpoints ` +
      `as though it contained a turn.  Add it with loop=None, or reorder the loop first`);
  } else {
    const shared = Object.keys(dev.loops).filter(
      (lid) => dev.loops[lid].nodes.includes(a) && dev.loops[lid].nodes.includes(b));
    if (shared.length && !onLoops.length) {
      warnings.push(`${segId} is a chord across loop(s) ${shared.slice().sort().join(', ')}: it ` +
                    `shortcuts the declared orbit, so a rigid shift still walks the long way ` +
                    `round while a routed move may not`);
    }
    if (onLoops.length) {
      warnings.push(`${a} and ${b} are consecutive in loop(s) ${onLoops.slice().sort().join(', ')} ` +
                    `but ${segId} is off every loop; pass loop=... to price it as a loop edge`);
    }
  }

  const d = clone(dev);
  d.segments[segId] = { id: segId, a, b, length: Number(length), cap: capacity, loop, labels: labels.slice() };
  const dBefore = degrees(dev), dAfter = degrees(d);
  for (const e of [a, b]) {
    if (dBefore[e] === 2 && dAfter[e] === 3) {
      warnings.push(`${e} is now degree 3, so R18 makes it a junction; the architecture must ` +
                    `price junction_cross at degree 3 or R11 will refuse it`);
    }
  }
  return finish(d, Object.assign(blankReport('add_segment'), { segments_added: [segId], warnings }), before);
}

// -------------------------------------------------------------------------- remove

function removeSegment(dev, segId, opts) {
  opts = opts || {};
  const onLoop = opts.on_loop || 'refuse';
  if (!(segId in dev.segments)) throw new EditError(`no such segment '${segId}'`);
  if (!['refuse', 'open', 'delete'].includes(onLoop)) throw new EditError("on_loop must be 'refuse', 'open' or 'delete'");

  const before = derived(dev);
  const seg = dev.segments[segId], a = seg.a, b = seg.b;
  const affected = adjacentOn(dev, a, b);
  const d = clone(dev);
  delete d.segments[segId];
  const opened = [], dropped = [], warnings = [];

  if (affected.length) {
    if (onLoop === 'refuse') {
      throw new EditError(
        `${segId} is an edge of loop(s) ${affected.slice().sort().join(', ')}; deleting it ` +
        `breaks the walk that prices a rigid rotation.  Pass on_loop='open' to cut the loop ` +
        `open there, or on_loop='delete' to drop the loop`);
    }
    for (const lid of affected) {
      const lp = dev.loops[lid], hits = pairPositions(lp, a, b);
      if (onLoop === 'delete') { delete d.loops[lid]; dropped.push(lid); continue; }
      if (hits.length > 1) throw new EditError(
        `loop '${lid}' walks ${a}-${b} ${hits.length} times; cutting it open there is ambiguous`);
      const i = hits[0], seq = lp.nodes.slice(), k = seq.length;
      if (lp.closed) {
        const j = (i + 1) % k;
        d.loops[lid].nodes = seq.slice(j).concat(seq.slice(0, j));
        d.loops[lid].closed = false;
      } else {
        const head = seq.slice(0, i + 1), tail = seq.slice(i + 1);
        const keep = head.length >= tail.length ? head : tail;
        d.loops[lid].nodes = keep;
        if (keep.length < minLoopNodes()) {
          // Neither piece is long enough to be a transport loop.
          // `pruneDegenerateLoops` drops it below and says so; saying "kept the 1-node
          // piece" and "is now OPEN" here as well would be two warnings about a loop
          // that no longer exists -- which is what the page printed while the export was
          // quietly unloadable.
          continue;
        }
        warnings.push(`loop '${lid}' was already open and is cut in two; keeping the ` +
                      `${keep.length}-node piece and dropping ${seq.length - keep.length} node(s) from the path`);
      }
      opened.push(lid);
      warnings.push(`loop '${lid}' is now OPEN: shift_map raises on it, so every declared ` +
                    `movement class with orbit '${lid}' can no longer run a rigid rotation`);
    }
  }
  if (dropped.length) {
    let n = 0;
    for (const sid of Object.keys(d.segments)) if (dropped.includes(d.segments[sid].loop)) d.segments[sid].loop = null;
    for (const sid of Object.keys(dev.segments)) if (dropped.includes(dev.segments[sid].loop)) n++;
    warnings.push(`loop(s) ${dropped.slice().sort().join(', ')} deleted; ${n} segment(s) are ` +
                  `now off every loop and price no corner`);
  }

  const degenerate = pruneDegenerateLoops(d.loops, d.segments, warnings);
  for (const lid of degenerate) if (!dropped.includes(lid)) dropped.push(lid);
  const openedKept = opened.filter((lid) => !degenerate.includes(lid));

  const degAfter = degrees(d);
  const orphans = [a, b].filter((n) => degAfter[n] === 0).sort();
  if (orphans.length) warnings.push(`${orphans.join(', ')} now has no segment at all; nothing can reach it`);

  const rep = Object.assign(blankReport('remove_segment'), {
    segments_removed: [segId],
    loops_changed: Array.from(new Set(openedKept.concat(dropped))).sort(),
    loops_opened: Array.from(new Set(openedKept)).sort(),
    loops_removed: Array.from(new Set(dropped)).sort(),
    orphaned: orphans, warnings,
  });
  return finish(d, rep, before);
}

function removeNode(dev, nodeId, opts) {
  opts = opts || {};
  const mend = opts.mend || 'splice';
  const cascade = !!opts.cascade;
  if (!(nodeId in dev.nodes)) throw new EditError(`no such node '${nodeId}'`);
  if (!['splice', 'open', 'delete'].includes(mend)) throw new EditError("mend must be 'splice', 'open' or 'delete'");

  const before = derived(dev);
  const d = clone(dev);
  const removedSegs = [], addedSegs = [], touched = [], opened = [], dropped = [], warnings = [];
  const onLoops = Object.keys(dev.loops).filter((lid) => dev.loops[lid].nodes.includes(nodeId));

  for (const lid of onLoops) {
    const lp = dev.loops[lid], seq = lp.nodes.slice(), i = seq.indexOf(nodeId), k = seq.length;
    if (mend === 'delete') { delete d.loops[lid]; dropped.push(lid); continue; }
    if (mend === 'open') {
      if (lp.closed) { d.loops[lid].nodes = seq.slice(i + 1).concat(seq.slice(0, i)); d.loops[lid].closed = false; }
      else {
        const head = seq.slice(0, i), tail = seq.slice(i + 1);
        d.loops[lid].nodes = head.length >= tail.length ? head : tail;
        if (d.loops[lid].nodes.length < minLoopNodes()) {
          // too short to be a transport loop at all; `pruneDegenerateLoops` drops it
          // below.  Do not also claim it is "now open".
          continue;
        }
      }
      opened.push(lid); touched.push(lid);
      warnings.push(`loop '${lid}' is now OPEN: rigid rotation on it is undefined`);
      continue;
    }
    if (lp.closed && k <= 3) throw new EditError(
      `loop '${lid}' has ${k} nodes; splicing ${nodeId} out would leave a closed loop of ` +
      `${k - 1}, which cannot be walked without a parallel edge`);
    const prevId = seq[(i - 1 + k) % k], nextId = seq[(i + 1) % k];
    if (!lp.closed && (i === 0 || i === k - 1)) {
      d.loops[lid].nodes = seq.filter((n) => n !== nodeId);
      touched.push(lid);
      continue;
    }
    const clash = segmentBetween(dev, prevId, nextId);
    if (clash !== null) throw new EditError(
      `splicing ${nodeId} out of loop '${lid}' needs a segment between ${prevId} and ${nextId}, ` +
      `but '${clash}' already joins them; that would be a parallel edge.  Use mend='open' or ` +
      `delete '${clash}' first`);
    const sPrev = dev.segments[segmentBetween(dev, prevId, nodeId)];
    const sNext = dev.segments[segmentBetween(dev, nodeId, nextId)];
    const bridge = fresh(`${sPrev.id}.${sNext.id}`, Object.keys(d.segments));
    d.segments = replaceKeys(d.segments, sPrev.id, [[bridge, Object.assign({}, sPrev, {
      id: bridge, a: prevId, b: nextId, length: r9(sPrev.length + sNext.length),
    })]]);
    addedSegs.push(bridge);
    for (const sid of [sPrev.id, sNext.id]) { if (sid in d.segments) delete d.segments[sid]; removedSegs.push(sid); }
    d.loops[lid].nodes = seq.filter((n) => n !== nodeId);
    touched.push(lid);
  }

  for (const sid of Object.keys(d.segments)) {
    const s = d.segments[sid];
    if (s.a === nodeId || s.b === nodeId) { delete d.segments[sid]; removedSegs.push(sid); }
  }
  if (dropped.length) for (const sid of Object.keys(d.segments)) if (dropped.includes(d.segments[sid].loop)) d.segments[sid].loop = null;
  delete d.nodes[nodeId];
  for (const lid of Object.keys(d.loops)) {
    if (d.loops[lid].nodes.includes(nodeId)) {
      d.loops[lid].nodes = d.loops[lid].nodes.filter((n) => n !== nodeId);
      touched.push(lid);
    }
  }

  const degenerate = pruneDegenerateLoops(d.loops, d.segments, warnings);
  for (const lid of degenerate) if (!dropped.includes(lid)) dropped.push(lid);
  const openedKept = opened.filter((lid) => !degenerate.includes(lid));
  const touchedKept = touched.filter((lid) => !degenerate.includes(lid));

  const degBefore = degrees(dev);
  let degAfter = degrees(d);
  let orphans = Object.keys(d.nodes).filter((n) => degAfter[n] === 0 && degBefore[n] > 0).sort();
  let nodesRemoved = [nodeId];
  if (orphans.length && cascade) {
    for (const nid of orphans) delete d.nodes[nid];
    warnings.push(`cascade removed ${orphans.length} node(s) left with no segment: ${orphans.join(', ')}`);
    nodesRemoved = nodesRemoved.concat(orphans);
    orphans = [];
  } else if (orphans.length) {
    warnings.push(`${orphans.length} node(s) are now wired to nothing: ${orphans.slice(0, 6).join(', ')}`);
  }

  const goneZone = dev.nodes[nodeId].zone;
  if (goneZone !== null && goneZone !== undefined) {
    const still = new Set(Object.keys(d.nodes).map((n) => d.nodes[n].zone));
    if (!still.has(goneZone)) {
      warnings.push(`zone type(s) ${goneZone} now have no sites; they stay declared but unused`);
    }
  }

  const rep = Object.assign(blankReport('remove_node'), {
    nodes_removed: nodesRemoved,
    segments_added: addedSegs,
    segments_removed: Array.from(new Set(removedSegs)).sort(),
    loops_changed: Array.from(new Set(touchedKept.concat(dropped))).sort(),
    loops_opened: Array.from(new Set(openedKept)).sort(),
    loops_removed: Array.from(new Set(dropped)).sort(),
    orphaned: orphans, warnings,
  });
  return finish(d, rep, before);
}

// ------------------------------------------------------------------------ dispatch

// One name per Python function, so the parity corpus is a list of
// {op, args} records executed identically on both sides.
const OPS = {
  add_site: (dev, a) => addSite(dev, a.id, a.pos, a),
  add_junction: (dev, a) => addJunction(dev, a.id, a.pos, a),
  add_segment: (dev, a) => addSegment(dev, a.id, a.a, a.b, a),
  remove_node: (dev, a) => removeNode(dev, a.id, a),
  remove_segment: (dev, a) => removeSegment(dev, a.id, a),
};

function applyEdit(dev, edit) {
  const fn = OPS[edit.op];
  if (!fn) throw new EditError(`unknown edit op '${edit.op}'`);
  return fn(dev, edit.args || {});
}

const API = {
  setBounds, bounds: () => BOUNDS, minLoopNodes, idPattern, pruneDegenerateLoops,
  EditError, checkId, derived, derivedDelta, components, checkStructure, degrees, allCorners,
  cornerEndpoints, addSite, addJunction, addSegment, removeNode, removeSegment,
  applyEdit, OPS,
};

// Usable three ways without a second copy: as an ES module (the parity test), as a
// CommonJS module, and as a plain <script> in the emitted page, where it hangs off
// `globalThis.QCCDEdit`.
if (typeof module !== 'undefined' && module.exports) module.exports = API;
if (typeof globalThis !== 'undefined') globalThis.QCCDEdit = API;

return API;
})();
