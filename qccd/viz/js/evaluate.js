// The evaluation language, in the browser.
//
// Every number the studio reports -- time, heat, hardware, the noise each channel puts in --
// is a term of the evaluation language (qccd/evalset/library/*.eval, printed on the website's
// Evaluation page).  The page carries the texts' parse trees, made by the Python parser
// (`qccd.evalset.ast_json`); this file reads them over the engine's own replay, with the
// semantics of `qccd.evalset.interp`, so the browser and the grader compute the same numbers
// from the same text.  `tests/test_eval_parity.py` holds the two interpreters to each other.
//
// The run is built exactly as `qccd.evalset.model.build_run` builds it from the Python
// replay's views: one step per frame (instruction), the state at its start from the
// engine's `onFrame` hook, the moves of each cycle from its `onCycle` window, each move's
// priced splits and merges, and each frame's duration and depth from the pricing walk.
(function () {
'use strict';
var Q = (typeof globalThis !== 'undefined' && globalThis.QCCD) ||
        (typeof module !== 'undefined' && module.exports && require('../engine.js'));
function own(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }

var OPERATED = { gate1: 1, gate2: 1, measure: 1, reset: 1 };
var ABSTRACT_2Q = { CX: 1, CZ: 1, SWAP: 1, CNOT: 1 };

function Absent(msg) { this.absent = true; this.message = msg; }
function EvalErr(msg) { this.evalError = true; this.message = msg; }

// ------------------------------------------------------------------ the run

var _uid = 0;
// A step's state is typed arrays over the run's ions (`pos0`: the node index each ion stands
// on, -1 for none; `nbar0`/`nbar1`: each ion's n-bar at its start and end), and a state equal
// to the step before's is the same array -- a 20,000-step programme keeps one array per
// change of state, not one object per step.  The occupancy of a node is counted from the
// positions when it is asked for (`occOf`): the engine writes its own occupancy only where it
// moves an ion (`_occSet`), so at every frame boundary the two are the same table.
function Step(index, frame, kind) {
  this._id = ++_uid; this.index = index; this.frame = frame; this.kind = kind;
  this.cls = frame && frame.cls !== undefined ? frame.cls : null;
  this.t0 = 0; this.t1 = 0; this.dt = 0; this.ops = [];
  this.pos0 = null; this.nbar0 = null; this.nbar1 = null;
  this.moves = []; this.depth = 0; this.cost = 0; this.cycles = 0; this.inProgramme = true;
  this._at0 = null; this._at1 = null;
}
// the instant a step starts or ends is ONE object, so a define called with `start(g)` twice
// is called with the same argument twice (its memo is keyed by identity)
Step.prototype.at = function (edge) {
  if (edge === 'start') return this._at0 || (this._at0 = new Instant(this, 'start'));
  return this._at1 || (this._at1 = new Instant(this, 'end'));
};
Step.prototype.label = function () {
  if (this.kind === 'prepare') return 'the preparation';
  var id = this.frame && this.frame.id !== undefined ? this.frame.id : this.index;
  return '#' + id + ' ' + this.kind + (this.cls ? ' ' + this.cls : '');
};
function Op(index, step, kind, ions, site, gate) {
  this._id = ++_uid; this.index = index; this.step = step; this.kind = kind; this.ions = ions;
  this.site = site === undefined ? null : site; this.gate = gate === undefined ? null : gate;
  this.basis = 'Z'; this.path = []; this.entails = [];
}
Op.prototype.label = function () {
  return (this.gate || this.kind) + ' ' + this.ions.join(',') + ' (' + this.step.label() + ')';
};
function Instant(step, edge) { this.step = step; this.edge = edge; }
Instant.prototype.label = function () { return 'the ' + this.edge + ' of ' + this.step.label(); };
function Hop(index, op, src, dst) { this._id = ++_uid; this.index = index; this.op = op; this.src = src; this.dst = dst; }

function sameArr(a, b) {
  if (!a || !b || a.length !== b.length) return false;
  for (var i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

// A gate frame's operations, read as the stim translation reads them: MS and the two-ion
// abstract gates by pair, R, VZ and the one-ion abstract gates by ion.
function gateOps(f) {
  var g = String(f.gate || 'MS');
  var two = g === 'MS' || own(ABSTRACT_2Q, g.toUpperCase());
  if (!two && g !== 'R' && g !== 'VZ') {
    var known1 = { I: 1, X: 1, Y: 1, Z: 1, H: 1, S: 1, S_DAG: 1, SDG: 1, SDAG: 1, ID: 1, IDLE: 1 };
    if (!own(known1, g.toUpperCase())) two = (f.pairs || []).length > 0;
  }
  var out = [];
  if (two) {
    var pairs = f.pairs && f.pairs.length ? f.pairs : (f.acts || []).filter(function (a) { return a.length === 2; });
    for (var i = 0; i < pairs.length; i++) out.push(['gate2', [pairs[i][0], pairs[i][1]]]);
  } else {
    var acts = f.acts || [];
    for (var j = 0; j < acts.length; j++) for (var k = 0; k < acts[j].length; k++) out.push(['gate1', [acts[j][k]]]);
  }
  return out;
}

// `build_run`: the run the texts are read over.  `model` is the engine's pricing model (the
// page's clock), `dev` the engine's device, `state` the editor's {control, budget, primitives}.
function buildRun(frames, loops, model, classes, dev, state, clock) {
  var c = collector(frames, dev);
  return c.finish(Q.priceFrames(frames, loops, model, classes || {}, c.hooks), model, loops, state, clock);
}

// The same run, in two halves: `hooks` for a pricing walk of `frames` that someone makes
// anyway (the editor's price walk: the window, `onFrame` and `onCycle` only watch it), and
// `finish(priced, ...)` on that walk's result -- so the page's evaluation costs no walk of
// its own.
function collector(frames, dev) {
  var degMap = Q.degrees(dev), degs = {};      // a Map in the engine; a plain object here
  degMap.forEach(function (v, k) { degs[k] = v; });
  var w = { dev: dev, deg: function (n) { return degs[n] || 0; }, moves: [] };
  // the ions in the order the programme places them (Python's `run.ions`), the nodes in the
  // device's order: the axes of every state array
  var ions = [], ionIdx = new Map(), nodes = Object.keys(dev.nodes), nodeIdx = new Map();
  for (var f0 = 0; f0 < frames.length; f0++) {
    var pl = frames[f0].type === 'init' ? frames[f0].place : null;
    if (pl) for (var k0 in pl) if (own(pl, k0) && !ionIdx.has(k0)) { ionIdx.set(k0, ions.length); ions.push(k0); }
  }
  nodes.forEach(function (n, i) { nodeIdx.set(n, i); });
  var nI = ions.length;
  function nodeOf(n) {
    var i = nodeIdx.get(n);
    if (i === undefined) { i = nodes.length; nodes.push(n); nodeIdx.set(n, i); }
    return i;
  }
  function snapPos(pos) {
    var P = new Int32Array(nI);
    for (var a = 0; a < nI; a++) { var x = pos[ions[a]]; P[a] = x === undefined || x === null ? -1 : nodeOf(x); }
    return P;
  }
  function snapQ(q) {
    var M = new Float64Array(nI);
    for (var a = 0; a < nI; a++) { var v = q[ions[a]]; M[a] = v === undefined ? 0 : Number(v); }
    return M;
  }
  // only an init or a transport moves an ion: after any other frame the positions and the
  // occupancy are the frame before's, and are not even read
  var steps = [], lastP = null, lastQ = null, moved = true;
  var hooks = {
    window: w,
    onFrame: function (fi, f, pos, occ, q) {
      var st = new Step(fi, f, f.type);
      if (moved || !lastP) {
        var P = snapPos(pos);
        if (!sameArr(P, lastP)) lastP = P;
      }
      var M = snapQ(q);
      if (!sameArr(M, lastQ)) lastQ = M;
      st.pos0 = lastP; st.nbar0 = lastQ;
      moved = f.type === 'init' || f.type === 'simd';
      steps.push(st);
    },
    onCycle: function () {
      var st = steps[steps.length - 1];
      st.cycles++;
      var firstOf = {}, i, m;
      for (i = 0; i < w.nmv; i++) {
        m = w.moves[i];
        var pr = own(firstOf, m.ion) ? null : (m.priced || []);
        if (pr) firstOf[m.ion] = pr;
        st.moves.push({ ion: m.ion, src: m.src, dst: m.dst, seg: m.seg, priced: pr });
      }
      // THE CYCLE'S DEPTH, as `qccd.evalset.model._cycle_depth` reads it off the moves: its
      // longest route, one deep for each rail and for each split or merge the ion is
      // charged -- not the pricing model's step count, which a deck model counts by hops
      if (st.kind === 'simd') {
        var routes = [], last = {};
        for (i = 0; i < w.nmv; i++) {
          m = w.moves[i];
          var prev = own(last, m.ion) ? last[m.ion] : null;
          if (prev && prev[1] === m.src) { routes[prev[0]]++; prev[1] = m.dst; }
          else { last[m.ion] = [routes.length, m.dst]; routes.push(1 + firstOf[m.ion].length); }
        }
        var d = 0;
        for (i = 0; i < routes.length; i++) if (routes[i] > d) d = routes[i];
        st.depth += d;
      }
    }
  };
  function finish(priced, model, loops, state, clock) {
  var rate = model._anomalousPerUs || 0;
  var finalP = snapPos(priced.pos || {}), finalQ = snapQ(priced.q || {});
  if (sameArr(finalP, lastP)) finalP = lastP;
  if (sameArr(finalQ, lastQ)) finalQ = lastQ;
  function nodeAt(P, ion) { var a = ionIdx.get(ion); if (a === undefined) return null; var v = P[a]; return v < 0 ? null : nodes[v]; }
  var t = 0, ops = [];
  for (var s = 0; s < steps.length; s++) {
    var st = steps[s], f = st.frame, pf = priced.perFrame[s];
    st.cost = pf[0]; st.dt = pf[2];
    st.t0 = t; t = t + pf[2]; st.t1 = t;
    // a transport step's depth is its cycles' (above); any other step is one deep per cycle
    if (f.type !== 'simd') st.depth = st.cycles;
    if (f.type === 'simd') {
      var byIon = {}, order = [];
      for (var mi = 0; mi < st.moves.length; mi++) {
        var mv = st.moves[mi];
        if (!own(byIon, mv.ion)) { byIon[mv.ion] = []; order.push(mv.ion); }
        byIon[mv.ion].push(mv);
      }
      for (var oi = 0; oi < order.length; oi++) {
        var ms = byIon[order[oi]];
        var op = new Op(ops.length, st, 'move', [order[oi]], null, st.cls);
        op.path = [ms[0].src];
        for (var q = 0; q < ms.length; q++) {
          op.path.push(ms[q].dst);
          if (ms[q].priced) op.entails = op.entails.concat(ms[q].priced);
        }
        ops.push(op); st.ops.push(op);
      }
    } else if (f.type === 'gate') {
      var go = gateOps(f);
      for (var gi = 0; gi < go.length; gi++) {
        var o = new Op(ops.length, st, go[gi][0], go[gi][1], nodeAt(st.pos0, go[gi][1][0]), String(f.gate || 'MS'));
        ops.push(o); st.ops.push(o);
      }
    } else if (f.type === 'measure' || f.type === 'reset') {
      var fi2 = f.ions || [];
      for (var mi2 = 0; mi2 < fi2.length; mi2++) {
        var o2 = new Op(ops.length, st, f.type, [fi2[mi2]], nodeAt(st.pos0, fi2[mi2]));
        ops.push(o2); st.ops.push(o2);
      }
    } else if (f.type === 'cool') {
      var cooled = (f.ions && f.ions.length) ? f.ions.slice()
                 : (f.broadcast ? ions.filter(function (ion, a) { return st.pos0[a] >= 0; }) : []);
      var o3 = new Op(ops.length, st, 'cool', cooled);
      ops.push(o3); st.ops.push(o3);
    }
  }
  // n-bar at the end of a step: the next one's start, except across a cooling, whose end is
  // the heat the step itself added (an ion with no place has none)
  for (var e = 0; e < steps.length; e++) {
    var se = steps[e];
    if (se.kind === 'cool') {
      se.nbar1 = new Float64Array(nI);
      for (var a = 0; a < nI; a++) if (se.pos0[a] >= 0) se.nbar1[a] = se.nbar0[a] + rate * se.dt;
    } else {
      se.nbar1 = e + 1 < steps.length ? steps[e + 1].nbar0 : finalQ;
    }
  }
  var prep = new Step(-1, null, 'prepare');
  prep.inProgramme = false;
  var init0 = steps.length && steps[0].kind === 'init';
  prep.pos0 = init0 ? (steps.length > 1 ? steps[1].pos0 : finalP) : new Int32Array(nI).fill(-1);
  prep.nbar0 = prep.nbar1 = new Float64Array(nI);
  var pop = new Op(ops.length, prep, 'prepare', ions.slice());
  prep.ops.push(pop); ops.push(pop);
  return { steps: steps, prepare: prep, readout: null, ops: ops, ions: ions, priced: priced,
           ionIdx: ionIdx, nodes: nodes, nodeIdx: nodeIdx, finalP: finalP,
           dev: dev, degs: degs, loops: loops, state: state || {}, model: model, clock: clock,
           anomalousPerUs: rate, frames: frames, experiment: null };
  }
  return { hooks: hooks, finish: finish };
}

// ------------------------------------------------------------------ the words

function step_(x, fn) {
  if (x instanceof Step) return x;
  if (x instanceof Op) return x.step;
  throw new EvalErr(fn + ' needs a step');
}
function op_(x, fn) { if (x instanceof Op) return x; throw new EvalErr(fn + ' needs an operation'); }
function inst_(x, fn) { if (x instanceof Instant) return x; throw new EvalErr(fn + ' needs an instant'); }

function scalarOf(state, p) {
  var prim = (state.primitives || {});
  var s = prim.scalars ? prim.scalars[p] : prim[p];
  if (!s) throw new EvalErr('the device declares no primitive ' + JSON.stringify(p));
  return s;
}

// what one site of a declared channel map is driven by: one channel per electrode role and
// bank, per axis for a row-and-column plane, or the explicit entries that name it
function _driven(run) {
  if (run._driven) return run._driven;
  var ctl = run.state.control || {}, spec = ctl.channels || {}, out = {};
  var declared = false; for (var k in spec) if (own(spec, k)) { declared = true; break; }
  run._channelMap = declared;
  if (!declared) { run._driven = out; return out; }
  var grouping = String(spec.grouping === undefined ? 'direct' : spec.grouping);
  var raw = spec.roles === undefined ? ['linear'] : spec.roles, counts = 0;
  if (Array.isArray(raw)) counts = raw.length * Math.trunc(Number(spec.channels_per_role === undefined ? 1 : spec.channels_per_role));
  else for (var r in raw) if (own(raw, r)) counts += Math.trunc(Number(raw[r]));
  var pair = Math.trunc(Number(spec.differential === undefined ? 1 : spec.differential));
  var sites = run.dev.nodes, nid;
  if (grouping === 'explicit') {
    var ex = spec.explicit || [];
    for (nid in sites) if (own(sites, nid) && sites[nid].kind === 'site') out[nid] = 0;
    for (var i = 0; i < ex.length; i++) {
      var dr = ex[i].drives || [];
      for (var j = 0; j < dr.length; j++) if (own(out, dr[j])) out[dr[j]]++;
    }
  } else {
    var axes = grouping === 'row_column' ? 2 : 1;
    for (nid in sites) if (own(sites, nid) && sites[nid].kind === 'site') out[nid] = counts * pair * axes;
  }
  run._driven = out;
  return out;
}

var W = {};
function word(name, fn, domain) { W[name] = { fn: fn, domain: !!domain }; }

word('sites', function (c) { var o = []; for (var n in c.run.dev.nodes) if (own(c.run.dev.nodes, n) && c.run.dev.nodes[n].kind === 'site') o.push(n); return o; }, true);
word('junctions', function (c) { var o = []; for (var n in c.run.dev.nodes) if (own(c.run.dev.nodes, n) && (c.run.degs[n] || 0) >= 3) o.push(n); return o; }, true);
word('nodes', function (c) { return Object.keys(c.run.dev.nodes); }, true);
word('rails', function (c) { return Object.keys(c.run.dev.segments); }, true);
word('capacity', function (c, x) {
  var d = c.run.dev;
  if (own(d.nodes, x)) return Math.trunc(Number(d.nodes[x].cap));
  if (own(d.segments, x)) return Math.trunc(Number(d.segments[x].cap));
  throw new EvalErr('capacity: ' + x + ' is neither a node nor a rail');
});
word('degree', function (c, n) { return c.run.degs[n] || 0; });
word('wiring', function (c, key, d) {
  var w = ((c.run.state.control || {}).wiring) || {};
  var v = own(w, key) ? w[key] : null;
  return v === null || v === undefined ? (d === undefined ? null : d) : v;
});
word('budget', function (c, key, d) {
  var b = c.run.state.budget || {};
  var v = own(b, key) ? b[key] : null;
  return v === null || v === undefined ? (d === undefined ? null : d) : v;
});
word('channel_map', function (c) { _driven(c.run); return c.run._channelMap; });
word('shared_channels', function (c) { return c.run.plane().groups; });
word('compensation_channels', function (c) {
  var w = ((c.run.state.control || {}).wiring) || {};
  var comp = Math.trunc(Number(w.compensation_electrodes_per_trap === undefined ? 0 : w.compensation_electrodes_per_trap));
  var demux = Math.trunc(Number(w.shim_per_dac === undefined ? 1 : w.shim_per_dac)) || 1;
  var total = c.run.plane().n_sites * comp;
  return demux ? Math.ceil(total / demux) : total;
});
word('map_electrodes', function (c) {
  var w = ((c.run.state.control || {}).wiring) || {};
  var p = Math.trunc(Number(w.electrodes_per_trap === undefined ? 0 : w.electrodes_per_trap));
  var comp = Math.trunc(Number(w.compensation_electrodes_per_trap === undefined ? 0 : w.compensation_electrodes_per_trap));
  var q = Math.trunc(Number(w.electrodes_per_junction === undefined ? 0 : w.electrodes_per_junction));
  var kj = 0; for (var n in c.run.dev.nodes) if (own(c.run.dev.nodes, n) && c.run.dev.nodes[n].kind === 'junction') kj++;
  return c.run.plane().n_sites * (p + comp) + kj * q;
});
word('map_switches', function (c) {
  var spec = ((c.run.state.control || {}).channels) || {};
  var sw = spec.switch_per_site === undefined ? true : !!spec.switch_per_site;
  return sw ? W.map_electrodes.fn(c) * 2 : 0;
});
word('driven', function (c, s) { var d = _driven(c.run); return own(d, s) ? d[s] : 0; });

word('steps', function (c) { return c.run.steps; }, true);
word('operations', function (c) { return c.run.ops.filter(function (o) { return o.step.inProgramme; }); }, true);
function kinds(list) { return function (c) { return c.run.ops.filter(function (o) { return list.indexOf(o.kind) >= 0; }); }; }
word('two_qubit_gates', kinds(['gate2']), true);
word('single_qubit_gates', kinds(['gate1']), true);
word('measurements', kinds(['measure']), true);
word('resets', kinds(['reset']), true);
word('coolings', kinds(['cool']), true);
word('moves', kinds(['move']), true);
word('preparations', kinds(['prepare']), true);
word('qubit_operations', kinds(['gate1', 'gate2', 'measure', 'reset']), true);
word('hops', function (c) {
  if (c.run._hops) return c.run._hops;
  var out = [];
  for (var s = 0; s < c.run.steps.length; s++) {
    var st = c.run.steps[s], by = {};
    for (var i = 0; i < st.ops.length; i++) if (st.ops[i].kind === 'move') by[st.ops[i].ions[0]] = st.ops[i];
    for (var m = 0; m < st.moves.length; m++) out.push(new Hop(out.length, by[st.moves[m].ion], st.moves[m].src, st.moves[m].dst));
  }
  c.run._hops = out;
  return out;
}, true);
word('readout', function (c) { return []; }, true);
word('ions', function (c) { return c.run.ions; }, true);
word('kind', function (c, x) { if (x instanceof Op || x instanceof Step) return x.kind; throw new EvalErr('kind needs an operation or a step'); });
word('ions_of', function (c, x) {
  if (x instanceof Op) return x.ions;
  if (x instanceof Step) { var o = [], s = {}; x.ops.forEach(function (p) { p.ions.forEach(function (i) { if (!own(s, i)) { s[i] = 1; o.push(i); } }); }); return o; }
  throw new EvalErr('ions needs an operation or a step');
});
word('operated', function (c, k) {
  var st = step_(k, 'operated');
  if (!st._operated) { st._operated = new Set(); st.ops.forEach(function (o) { if (own(OPERATED, o.kind)) o.ions.forEach(function (i) { st._operated.add(i); }); }); }
  return st._operated;
});
word('gate', function (c, o) { return op_(o, 'gate').gate; });
word('basis', function (c, m) { return op_(m, 'basis').basis; });
word('site', function (c, o) { return op_(o, 'site').site; });
word('step', function (c, o) { return op_(o, 'step').step; });
word('cls', function (c, k) { return step_(k, 'cls').cls; });
word('to', function (c, h) { if (h instanceof Hop) return h.dst; var o = op_(h, 'to'); return o.path.length ? o.path[o.path.length - 1] : null; });
word('from', function (c, h) { if (h instanceof Hop) return h.src; var o = op_(h, 'from'); return o.path.length ? o.path[0] : null; });
word('move_of', function (c, h) { if (h instanceof Hop) return h.op; throw new EvalErr('move needs a hop'); });
word('entails', function (c, o) { return op_(o, 'entails').entails; });
word('path', function (c, o) { return op_(o, 'path').path; });
word('crossed', function (c, o) { o = op_(o, 'crossed'); return o.path.slice(1).filter(function (n) { return (c.run.degs[n] || 0) >= 3; }); });
word('cycles', function (c, k) { return step_(k, 'cycles').cycles; });
word('depth', function (c, k) { return step_(k, 'depth').depth; });
word('movers', function (c, k) { var st = step_(k, 'movers'), s = new Set(); st.moves.forEach(function (m) { s.add(m.ion); }); return s.size; });
word('rotation', function (c, k) { var st = step_(k, 'rotation'); return !!(st.kind === 'simd' && st.cls && String(st.cls).indexOf('rotate') === 0); });
word('loop_riders', function (c, k) {
  var st = step_(k, 'loop_riders'), f = st.frame;
  if (!f || f.type !== 'simd') return 0;
  if (st.cls && String(st.cls).indexOf('rotate') === 0) {
    if (c.run._riders === undefined) {
      var onLoop = {}, loops = c.run.closedLoops || {};
      for (var l in loops) if (own(loops, l)) loops[l].forEach(function (n) { onLoop[n] = 1; });
      var init = null;
      for (var i = 0; i < c.run.frames.length; i++) if (c.run.frames[i].type === 'init') { init = c.run.frames[i]; break; }
      var n = 0;
      if (init) for (var ion in init.place) if (own(init.place, ion) && own(onLoop, init.place[ion])) n++;
      c.run._riders = n;
    }
    return c.run._riders;
  }
  return (f.moves || []).length;
});

word('instants', function (c) {
  if (c.run._instants) return c.run._instants;
  var out = [], all = [c.run.prepare].concat(c.run.steps);
  all.forEach(function (s) { out.push(s.at('start')); out.push(s.at('end')); });
  return (c.run._instants = out);
}, true);
word('start', function (c, x) { return step_(x, 'start').at('start'); });
word('end', function (c, x) { return step_(x, 'end').at('end'); });
word('final', function (c) { var s = c.run.steps; return (s.length ? s[s.length - 1] : c.run.prepare).at('end'); });
word('time', function (c, t) { t = inst_(t, 'time'); return t.edge === 'start' ? t.step.t0 : t.step.t1; });
word('duration', function (c, k) { return step_(k, 'duration').dt; });
// a step's end is the next one's start (the state between two frames does not change); the
// last step's is the replay's final state, and the preparation's is its start
function endPos(run, st) {
  if (st.index < 0) return st.pos0;
  return st.index + 1 < run.steps.length ? run.steps[st.index + 1].pos0 : run.finalP;
}
function occOf(run, P) {
  if (!P._occ) {
    var O = new Int32Array(run.nodes.length);
    for (var a = 0; a < P.length; a++) if (P[a] >= 0) O[P[a]]++;
    P._occ = O;
  }
  return P._occ;
}
word('position', function (c, i, t) {
  t = inst_(t, 'position');
  var a = c.run.ionIdx.get(i);
  if (a === undefined) return null;
  var v = (t.edge === 'start' ? t.step.pos0 : endPos(c.run, t.step))[a];
  return v < 0 ? null : c.run.nodes[v];
});
word('occupancy', function (c, x, t) {
  t = inst_(t, 'occupancy');
  var O = occOf(c.run, t.edge === 'start' ? t.step.pos0 : endPos(c.run, t.step)), i = c.run.nodeIdx.get(x);
  return i === undefined || i >= O.length ? 0 : O[i];
});
word('nbar', function (c, i, t) {
  t = inst_(t, 'nbar');
  var a = c.run.ionIdx.get(i);
  return a === undefined ? 0.0 : (t.edge === 'start' ? t.step.nbar0 : t.step.nbar1)[a];
});
word('waited', function (c, i, o) {
  o = op_(o, 'waited');
  if (!c.run._waited) {
    var acc = {}, out = {};
    c.run.ions.forEach(function (ion) { acc[ion] = 0.0; });
    var all = [c.run.prepare].concat(c.run.steps);
    for (var s = 0; s < all.length; s++) {
      var st = all[s], ops = {};
      for (var k = 0; k < st.ops.length; k++) {
        var x = st.ops[k];
        if (own(OPERATED, x.kind)) for (var j = 0; j < x.ions.length; j++) {
          out[x.ions[j] + '|' + st.index + '|' + st.kind] = own(acc, x.ions[j]) ? acc[x.ions[j]] : 0.0;
          ops[x.ions[j]] = 1;
        }
      }
      for (var ion2 in acc) if (own(acc, ion2)) {
        if (own(ops, ion2)) acc[ion2] = 0.0;
        else if (st.inProgramme && st.dt > 0) acc[ion2] += st.dt;
      }
    }
    c.run._waited = out;
  }
  var key = i + '|' + o.step.index + '|' + o.step.kind;
  return own(c.run._waited, key) ? c.run._waited[key] : 0.0;
});
word('deposited', function (c, comp) { return Number((c.run.priced.comp || {})[comp] || 0); });

word('primitive', function (c, p, f) {
  var s = scalarOf(c.run.state, p), v = s[f];
  if (v === undefined || v === null) throw new EvalErr('the primitive ' + p + ' declares no ' + f);
  return typeof v === 'number' ? v : v;
});
word('slope', function (c, p) {
  var s = String(scalarOf(c.run.state, p).error_vs_quanta || 'linear:0');
  return s.indexOf('linear:') === 0 ? Number(s.slice(7)) : 0.0;
});
word('chain_reference', function (c, p) {
  var s = scalarOf(c.run.state, p).error_vs_chain;
  if (!s || String(s).indexOf('murali:') !== 0) return null;
  return Number(String(s).slice(7));
});
word('coherence_time', function (c) { var sp = c.run.state.species || {}; var v = sp.T_coh_s; return v ? Number(v) : null; });
word('heating_rate', function (c) { return c.run.anomalousPerUs; });

['rounds', 'logical_qubits', 'observables', 'distance'].forEach(function (n) {
  word(n, function () { throw new Absent('no experiment was given with the programme'); });
});
['shots', 'failures'].forEach(function (n) {
  word(n, function () { throw new Absent('no sample was drawn: the logical error rate is estimated by the sampler'); });
});
word('noiseless_ok', function () { throw new Absent('the noiseless check was not run'); });
word('rules_hold', function (c) { if (c.checks && c.checks.rules !== undefined) return !!c.checks.rules; throw new Absent('the rules were not checked'); });

function num1(fn) { return function (c, x) { if (x === null || x === undefined) throw new EvalErr('of none'); return fn(x); }; }
word('ln', num1(Math.log)); word('exp', num1(Math.exp)); word('sqrt', num1(Math.sqrt));
word('sin', num1(Math.sin)); word('abs', num1(Math.abs));
word('ceil', num1(function (x) { return Math.ceil(x); }));
word('pow', function (c, x, y) { return Math.pow(x, y); });
word('min', function (c) { var a = Array.prototype.slice.call(arguments, 1); return a.reduce(function (p, v) { return v < p ? v : p; }); });
word('max', function (c) { var a = Array.prototype.slice.call(arguments, 1); return a.reduce(function (p, v) { return v > p ? v : p; }); });
word('pi', function () { return Math.PI; });
word('size', function (c, a) { return a instanceof Set ? a.size : a.length; });

var CALL_ALIAS = { ions: 'ions_of', move: 'move_of' };

// ------------------------------------------------------------------ the interpreter

function freeNames(n, bound, out) {
  if (!n) return out;
  switch (n[0]) {
    case 'v': if (!bound.has(n[1])) out.add(n[1]); break;
    case 'f': n[2].forEach(function (a) { freeNames(a, bound, out); }); break;
    case 'b': freeNames(n[2], bound, out); freeNames(n[3], bound, out); break;
    case '!': freeNames(n[1], bound, out); break;
    case '{': n[1].forEach(function (a) { freeNames(a, bound, out); }); break;
    case '?': freeNames(n[1], bound, out); freeNames(n[2], bound, out); freeNames(n[3], bound, out); break;
    case 'g': case 'q':
      var binders = n[0] === 'g' ? n[3] : n[2], where = n[0] === 'g' ? n[4] : n[3];
      var inner = new Set(bound);
      binders.forEach(function (b) { freeNames(b[1], inner, out); b[0].forEach(function (x) { inner.add(x); }); });
      freeNames(where, inner, out);
      freeNames(n[0] === 'g' ? n[2] : n[4], inner, out);
      break;
  }
  return out;
}

function keyOf(v) {
  if (v === null || v === undefined) return 'none';
  if (typeof v === 'object') return v._id !== undefined ? '#' + v._id : (v instanceof Instant ? '@' + v.step._id + v.edge : JSON.stringify(v));
  return typeof v + ':' + String(v);
}

function Evaluator(ev, run, opts) {
  opts = opts || {};
  this.ev = ev; this.run = run;
  this.ctx = { run: run, checks: opts.checks || {} };
  this.params = ev.params || {};
  this.defines = {}; (ev.defines || []).forEach(function (d) { this.defines[d.name] = d; }, this);
  this.metrics = {}; (ev.metrics || []).forEach(function (m) { this.metrics[m.name] = m; }, this);
  this.vals = {}; this.busy = {}; this.memoT = new Map(); this.freeC = new WeakMap(); this.doms = new WeakMap();
  this.wordMemo = new Map(); this.defMemo = new Map();
  this.under = opts.under || null;
}
Evaluator.prototype.free = function (n) {
  var f = this.freeC.get(n);
  if (!f) { f = freeNames(n, new Set(), new Set()); this.freeC.set(n, f); }
  return f;
};
Evaluator.prototype.known = function (name) {
  return own(this.params, name) || own(this.metrics, name) || (own(this.defines, name) && !this.defines[name].args.length);
};
Evaluator.prototype.value = function (name) {
  if (own(this.vals, name)) return this.vals[name];
  if (this.busy[name]) throw new EvalErr(name + ' is defined in terms of itself');
  this.busy[name] = 1;
  var v;
  try {
    if (own(this.params, name)) v = this.term(this.params[name], {});
    else if (own(this.defines, name) && !this.defines[name].args.length) v = this.term(this.defines[name].body, {});
    else if (own(this.metrics, name)) {
      var m = this.metrics[name];
      if (m.clock && m.clock !== this.run.clock) {
        if (!this.under) throw new Absent('on the ' + m.clock + ' clock, which this page does not price');
        v = this.under(m.clock).value(name);
      } else v = this.term(m.body, {});
    } else throw new EvalErr(name + ' is not defined');
  } finally { delete this.busy[name]; }
  this.vals[name] = v;
  return v;
};
Evaluator.prototype.lookup = function (name, env) {
  if (own(env, name)) return env[name];
  if (this.known(name)) return this.value(name);
  var w = W[name];
  if (w) { var k = 'w:' + name; if (!this.wordMemo.has(k)) this.wordMemo.set(k, w.fn(this.ctx)); return this.wordMemo.get(k); }
  throw new EvalErr(JSON.stringify(name) + ' is neither a variable, a parameter, a define, a metric nor a word');
};
Evaluator.prototype.truth = function (n, env) { return !!this.term(n, env); };
Evaluator.prototype.term = function (n, env) {
  switch (n[0]) {
    case 'n': case 's': case 'c': return n[1];
    case 'v': return this.lookup(n[1], env);
    case 'f': return this.call(n, env);
    case 'b':
      var op = n[1];
      if (op === 'and') return this.truth(n[2], env) && this.truth(n[3], env);
      if (op === 'or') return this.truth(n[2], env) || this.truth(n[3], env);
      if (op === 'implies') return !this.truth(n[2], env) || this.truth(n[3], env);
      return this.binop(op, this.term(n[2], env), this.term(n[3], env));
    case '!': return !this.truth(n[1], env);
    case '{': var s = new Set(); n[1].forEach(function (a) { s.add(this.term(a, env)); }, this); return s;
    case '?': return this.term(this.truth(n[1], env) ? n[2] : n[3], env);
    case 'g': case 'q': return this.memo(n, env);
  }
  throw new EvalErr('cannot evaluate ' + JSON.stringify(n).slice(0, 60));
};
Evaluator.prototype.memo = function (n, env) {
  var fr = this.free(n), key = '', ok = true;
  var names = Array.from(fr).sort();
  for (var i = 0; i < names.length; i++) if (own(env, names[i])) key += names[i] + '=' + keyOf(env[names[i]]) + ';';
  var bucket = this.memoT.get(n);
  if (!bucket) { bucket = new Map(); this.memoT.set(n, bucket); }
  if (bucket.has(key)) return bucket.get(key);
  var v = n[0] === 'g' ? this.aggregate(n, env) : this.quant(n, env);
  bucket.set(key, v);
  return v;
};
Evaluator.prototype.call = function (n, env) {
  var fn = n[1], args = n[2], self = this, vals, key, v;
  var d = this.defines[fn];
  if (d && own(this.defines, fn)) {
    if (d.args.length !== args.length) throw new EvalErr(fn + ' takes ' + d.args.length + ' argument(s)');
    vals = args.map(function (a) { return self.term(a, env); });
    // a define is a pure function of its arguments: kept per argument tuple, in one Map per
    // argument position keyed by the value itself (instants are canonical, see Step.at)
    var m = this.defMemo.get(fn);
    if (!m) { m = new Map(); this.defMemo.set(fn, m); }
    for (var j = 0; j < vals.length - 1; j++) {
      var nx = m.get(vals[j]);
      if (!nx) { nx = new Map(); m.set(vals[j], nx); }
      m = nx;
    }
    key = vals.length ? vals[vals.length - 1] : NOARG;
    if (m.has(key)) return m.get(key);
    if (this.busy[fn]) throw new EvalErr(fn + ' is defined in terms of itself');
    this.busy[fn] = 1;
    var e = {}; d.args.forEach(function (a, i) { e[a] = vals[i]; });
    try { v = this.term(d.body, e); } finally { delete this.busy[fn]; }
    m.set(key, v);
    return v;
  }
  if (fn === 'count' && args.length === 1) { var c = this.term(args[0], env); return c instanceof Set ? c.size : c.length; }
  var w = W[own(CALL_ALIAS, fn) ? CALL_ALIAS[fn] : fn];
  if (!w) throw new EvalErr('the vocabulary has no word ' + JSON.stringify(fn));
  // a word is NOT kept per call (as in Python): it is a lookup, and keeping every call cost
  // one entry per (ion, instant)
  switch (args.length) {
    case 0: return w.fn(this.ctx);
    case 1: return w.fn(this.ctx, this.term(args[0], env));
    case 2: return w.fn(this.ctx, this.term(args[0], env), this.term(args[1], env));
  }
  vals = args.map(function (a) { return self.term(a, env); });
  return w.fn.apply(null, [this.ctx].concat(vals));
};
var NOARG = {};
function same(a, b) {
  if (a instanceof Instant && b instanceof Instant) return a.step === b.step && a.edge === b.edge;
  if (a === undefined) a = null; if (b === undefined) b = null;
  return a === b;
}
function hasIn(b, a) {
  if (b === null || b === undefined) return false;
  if (b instanceof Set) return b.has(a);
  if (Array.isArray(b)) return b.indexOf(a) >= 0;
  return false;
}
function ikey(t) { return t.step.index * 2 + (t.edge === 'end' ? 1 : 0); }
Evaluator.prototype.binop = function (op, a, b) {
  if (op === '=') return same(a, b);
  if (op === '!=') return !same(a, b);
  if (op === 'in') return hasIn(b, a);
  if (op === 'notin') return !hasIn(b, a);
  if (a === null || a === undefined || b === null || b === undefined) throw new EvalErr('a value is none');
  if (op === '+' && Array.isArray(a) && Array.isArray(b)) return a.concat(b.filter(function (x) { return a.indexOf(x) < 0; }));
  if (a instanceof Instant && b instanceof Instant) { a = ikey(a); b = ikey(b); }
  if (typeof a === 'boolean') a = a ? 1 : 0;
  if (typeof b === 'boolean') b = b ? 1 : 0;
  switch (op) {
    case '<': return a < b; case '<=': return a <= b; case '>': return a > b; case '>=': return a >= b;
    case '+': return a + b; case '-': return a - b; case '*': return a * b;
    case '/': return b ? a / b : Infinity;
  }
  throw new EvalErr('unknown operator ' + op);
};
Evaluator.prototype.domain = function (node, env, bound) {
  var fr = this.free(node), dep = false;
  fr.forEach(function (x) { if (bound.has(x) || own(env, x)) dep = true; });
  if (!dep) {
    var c = this.doms.get(node);
    if (!c) { c = this.term(node, {}); c = c === null || c === undefined ? [] : Array.from(c); this.doms.set(node, c); }
    return c;
  }
  var v = this.term(node, env);
  return v === null || v === undefined ? [] : Array.from(v);
};
function conjuncts(n) { return n && n[0] === 'b' && n[1] === 'and' ? conjuncts(n[2]).concat(conjuncts(n[3])) : [n]; }
// Every binding of the binders that satisfies `where`, in the walk's order, handed to `cb`
// (which returns true to stop).  ONE environment, rebound in place: every caller reads what
// it needs from a binding before the next one is made, as Python's generator is read.
Evaluator.prototype.eachBinding = function (binders, where, env, cb) {
  var vars = [], self = this;
  binders.forEach(function (b) { b[0].forEach(function (nm) { vars.push([nm, b[1]]); }); });
  var e = Object.assign({}, env);
  if (vars.length === 1 && where) {
    var nm = vars[0][0], fast = [], rest = [];
    conjuncts(where).forEach(function (c) {
      if (c[0] === 'b' && (c[1] === 'in' || c[1] === 'notin') && c[2][0] === 'v' && c[2][1] === nm && !self.free(c[3]).has(nm)) fast.push([c[1] === 'in', c[3]]);
      else rest.push(c);
    });
    if (fast.length) {
      var sets = fast.map(function (f) { var v = self.term(f[1], env); return [f[0], v instanceof Set ? v : new Set(v || [])]; });
      var dom = this.domain(vars[0][1], env, new Set([nm]));
      outer: for (var i = 0; i < dom.length; i++) {
        var x = dom[i];
        for (var s = 0; s < sets.length; s++) if (sets[s][1].has(x) !== sets[s][0]) continue outer;
        e[nm] = x;
        for (var r = 0; r < rest.length; r++) if (!self.truth(rest[r], e)) continue outer;
        if (cb(e)) return;
      }
      return;
    }
  }
  var bound = new Set(vars.map(function (v) { return v[0]; }));
  (function walk(i) {
    if (i === vars.length) return (!where || self.truth(where, e)) ? !!cb(e) : false;
    var name = vars[i][0], had = own(e, name), was = e[name];
    var dom = self.domain(vars[i][1], e, bound), stop = false;
    for (var k = 0; k < dom.length && !stop; k++) { e[name] = dom[k]; stop = walk(i + 1); }
    if (had) e[name] = was; else delete e[name];   // what a later binder's domain must see
    return stop;
  })(0);
};
Evaluator.prototype.bindings = function (binders, where, env) {
  var out = [];
  this.eachBinding(binders, where, env, function (e) { out.push(Object.assign({}, e)); });
  return out;
};
// `expr for x in D`, one variable and no `where` (Python's `_values_over`): the values in the
// walk's order; a word whose only arguments that read x ARE x has its other arguments
// evaluated once (the domain is not empty, so the walk would have evaluated them too)
Evaluator.prototype.valuesOver = function (n, env) {
  var nm = n[3][0][0][0], dom = this.domain(n[3][0][1], env, new Set([nm])), expr = n[2], out = [];
  if (!dom.length) return out;
  if (expr[0] === 'f' && !own(this.defines, expr[1]) && expr[1] !== 'count') {
    var w = W[own(CALL_ALIAS, expr[1]) ? CALL_ALIAS[expr[1]] : expr[1]], self = this;
    var free = expr[2].map(function (a) { return self.free(a).has(nm); });
    if (w && expr[2].every(function (a, i) { return !free[i] || (a[0] === 'v' && a[1] === nm); })) {
      var args = [this.ctx].concat(expr[2].map(function (a, i) { return free[i] ? null : self.term(a, env); }));
      var at = []; free.forEach(function (f, i) { if (f) at.push(i + 1); });
      for (var k = 0; k < dom.length; k++) {
        for (var j = 0; j < at.length; j++) args[at[j]] = dom[k];
        out.push(w.fn.apply(null, args));
      }
      return out;
    }
  }
  var e = Object.assign({}, env);
  for (var q = 0; q < dom.length; q++) { e[nm] = dom[q]; out.push(this.term(expr, e)); }
  return out;
};
Evaluator.prototype.aggregate = function (n, env) {
  var kind = n[1], self = this, vals;
  if (kind === 'count') { var c = 0; this.eachBinding(n[3], n[4], env, function () { c++; }); return c; }
  if (n[3].length === 1 && n[3][0][0].length === 1 && !n[4]) vals = this.valuesOver(n, env);
  else { vals = []; this.eachBinding(n[3], n[4], env, function (e) { vals.push(self.term(n[2], e)); }); }
  if (kind === 'set') return new Set(vals);
  if (kind === 'sum') { var t = 0; for (var i = 0; i < vals.length; i++) t = t + vals[i]; return t; }
  if (kind === 'max' || kind === 'min') {
    if (!vals.length) throw new EvalErr(kind + ' of nothing');
    return vals.reduce(function (p, v) { return kind === 'max' ? (v > p ? v : p) : (v < p ? v : p); });
  }
  if (kind === 'all_different') return new Set(vals).size === vals.length;
  throw new EvalErr('unknown aggregate ' + kind);
};
Evaluator.prototype.quant = function (n, env) {
  var self = this, all = n[1] === 'all', res = all;
  this.eachBinding(n[2], n[3], env, function (e) {
    var ok = self.truth(n[4], e);
    if (all && !ok) { res = false; return true; }
    if (!all && ok) { res = true; return true; }
    return false;
  });
  return res;
};

// ------------------------------------------------------------------ what a page shows

function noiseBudget(e, channels) {
  var out = {}, order = [];
  channels.forEach(function (ch) {
    var keys = ch.counted.length ? ch.counted.map(function (c) { return c[0]; }) : [ch.name];
    keys.forEach(function (k) { if (!own(out, k)) { out[k] = { faults: 0, places: 0, channels: [] }; order.push(k); } if (out[k].channels.indexOf(ch.name) < 0) out[k].channels.push(ch.name); });
  });
  channels.forEach(function (ch) {
    e.eachBinding(ch.binders, ch.where, {}, function (b) {
      var p = e.term(ch.prob, b);
      if (p === null || p === undefined || !(p > 0)) return false;
      var tg = e.term(ch.targets, b);
      var n = typeof tg === 'string' ? 1 : (tg instanceof Set ? tg.size : (tg ? tg.length : 0));
      if (!n) return false;
      var amount = ch.op === 'DEPOLARIZE2' ? p : p * n, line;
      if (!ch.counted.length) { line = out[ch.name]; line.faults += amount; line.places += 1; return false; }
      if (ch.counted.length === 1 && !ch.counted[0][1]) { line = out[ch.counted[0][0]]; line.faults += amount; line.places += 1; return false; }
      var terms = ch.counted.map(function (c) { return Number(e.term(c[1], b)); });
      var whole = terms.reduce(function (a, x) { return a + x; }, 0);
      ch.counted.forEach(function (c, j) { line = out[c[0]]; line.faults += whole ? amount * terms[j] / whole : 0; line.places += 1; });
      return false;
    });
  });
  return { lines: out, order: order };
}

// THE RUN OF A DEVICE WITH NO PROGRAMME: no step, no operation, no ion.  What the hardware
// metrics read (the sites, the rails, the wiring, the budget) is all there; every execution
// domain is empty.  The page prices its device with it on every edit, programme or not.
function deviceRun(dev, state, clock) {
  var degMap = Q.degrees(dev), degs = {};
  degMap.forEach(function (v, k) { degs[k] = v; });
  var prep = new Step(-1, null, 'prepare');
  prep.inProgramme = false;
  prep.pos0 = new Int32Array(0); prep.nbar0 = prep.nbar1 = new Float64Array(0);
  var pop = new Op(0, prep, 'prepare', []);
  prep.ops.push(pop);
  var nodes = Object.keys(dev.nodes), nodeIdx = new Map();
  nodes.forEach(function (n, i) { nodeIdx.set(n, i); });
  return { steps: [], prepare: prep, readout: null, ops: [pop], ions: [], ionIdx: new Map(),
           nodes: nodes, nodeIdx: nodeIdx, finalP: prep.pos0,
           priced: { perFrame: [], comp: {}, q: {}, pos: {} }, dev: dev, degs: degs, loops: {},
           state: state || {}, model: null, clock: clock, anomalousPerUs: 0, frames: [],
           experiment: null };
}

function isMissing(err) { return !!(err && (err.absent || err.evalError)); }

// ONE EVALUATION OF ONE PAGE STATE, computed as it is asked for.  `value(name)` reads one
// metric -- the replay is made the first time a metric needs it, and a metric with a clock
// of its own replays under that clock the first time it is read -- so the head's chips cost
// one replay and the Report's full table, the other clocks and the noise budget are paid for
// only when the Report is open.  `frames === null` is the device alone (`deviceRun`).
// `mk(table)` builds the engine's pricing model under another table.
function session(payload, frames, loops, model, classes, dev, state, opts) {
  opts = opts || {};
  var plane = null, runs = {}, main = null, page = null;
  function planeOf() {
    if (!plane) {
      var n = 0;
      for (var nid in dev.nodes) if (own(dev.nodes, nid) && dev.nodes[nid].kind === 'site') n++;
      // the plane's own count: the engine's `_channelGroups`, through the hardware report
      plane = { groups: Q.hardwareReport(dev, (state || {}).control || {}, {}, '').dacs_broadcast, n_sites: n };
    }
    return plane;
  }
  function attach(r) { r.plane = planeOf; r.closedLoops = opts.closedLoops || {}; return r; }
  function under(clock) {
    if (!opts.mk || frames === null) return null;
    if (!runs[clock]) {
      runs[clock] = new Evaluator(payload, attach(buildRun(frames, loops, opts.mk(clock), classes, dev, state, clock)),
                                  { checks: opts.checks, under: under });
    }
    return runs[clock];
  }
  function ev() {
    if (!main) {
      // `opts.run`: the run made from a walk the page made anyway (`collector`)
      var r = opts.run ? opts.run
            : frames === null ? deviceRun(dev, state, payload.clock)
            : buildRun(frames, loops, model, classes, dev, state, payload.clock);
      main = new Evaluator(payload, attach(r), { checks: opts.checks, under: opts.mk ? under : null });
    }
    return main;
  }
  var cache = {};
  function value(name) {
    if (own(cache, name)) return cache[name];
    var out;
    try { out = { value: ev().value(name) }; }
    catch (err) { if (isMissing(err)) out = { missing: err.message }; else throw err; }
    return (cache[name] = out);
  }
  function requirements() {
    var e = ev();
    return payload.requirements.map(function (r) {
      try { return { id: r.id, title: r.title, text: r.text, verdict: e.truth(r.formula, {}) ? 'holds' : 'fails' }; }
      catch (err) { if (isMissing(err)) return { id: r.id, title: r.title, text: r.text, verdict: 'could not be checked', reason: err.message }; throw err; }
    });
  }
  // `part`: the metrics on the page's own clock and the requirements only -- a metric with a
  // clock of its own replays the whole programme again, and the noise budget walks every
  // step and ion, so a page with a long programme leaves those until it is asked
  function all(part) {
    // a full page answers either request; a part answers only a part
    if (page && (!page.part || part)) return page;
    var metrics = payload.metrics.map(function (m) {
      var row = { name: m.name, title: m.title, unit: m.unit, better: m.better, group: m.group, clock: m.clock };
      if (part && m.clock && m.clock !== payload.clock) { row.deferred = true; return row; }
      var v = value(m.name);
      if (own(v, 'value')) row.value = v.value; else row.missing = v.missing;
      return row;
    });
    var budget = null;
    if (part) budget = { deferred: true };
    else {
      try { budget = noiseBudget(ev(), payload.channels || []); }
      catch (err) { if (isMissing(err)) budget = { error: err.message }; else throw err; }
    }
    page = { name: payload.name, digest: payload.digest, clock: payload.clock, metrics: metrics,
             requirements: requirements(), budget: budget, run: ev().run, part: !!part };
    return page;
  }
  return { value: value, requirements: requirements, page: all, payload: payload,
           run: function () { return ev().run; } };
}

// Everything the studio reports, from one evaluation payload: every metric (or why it cannot
// be computed here), every requirement's verdict, and the noise model's budget.
function evaluatePage(payload, frames, loops, model, classes, dev, state, opts) {
  return session(payload, frames, loops, model, classes, dev, state, opts).page();
}

var API = { buildRun: buildRun, collector: collector, deviceRun: deviceRun, Evaluator: Evaluator, session: session,
            evaluatePage: evaluatePage, noiseBudget: noiseBudget, WORDS: W };
if (Q) Q.evaluation = API;
if (typeof module !== 'undefined' && module.exports) module.exports = API;
})();
