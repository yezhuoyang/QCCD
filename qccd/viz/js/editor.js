// qccd/viz/js/editor.js -- DIRECT MANIPULATION AND THE SIDE EDITOR.
//
// The user asked to edit the architecture in the browser, in real time, two ways: by
// clicking and dragging on the stage, and by programming in a side editor -- with the page
// re-rendering and re-pricing client-side.  This is that layer.  `qccd/viz/engine.js` does
// the arithmetic (and is bit-identical to Python, proved by `tests/test_engine_parity.py`);
// `qccd/viz/js/edit.js` does topology (proved by `tests/test_edit_parity.py`).  Nothing
// here computes a number that either of those could compute.
//
// THE KEYSTONE: EVERY GESTURE REDUCES TO ONE SERIALIZABLE OP.
// -----------------------------------------------------------
// Nothing in the mouse layer mutates geometry.  A gesture's only output is one record in
// the SAME shape `ArchLine.call` already uses:
//
//     {method: "move_site", args: ["S12", 4.0, 1.0], kwargs: {}, meta: {group, src}}
//
// State is BASE (the shipped `A.listing` call records, immutable) + EDITS[] + UNDONE[].
// `rebuild()` replays BASE and then every op, from scratch.  No inverse ops, no
// incremental undo state.  That is what makes drag, the text editor, undo, the Python
// round trip and the parity test ONE mechanism instead of five -- and it is why the text
// lane and the mouse lane can never disagree: there is only one applier.
//
// WHAT IT REFUSES TO DO
// ---------------------
// It does not re-route, re-compile or re-verify.  `qccd/compile/*` and the R1-R18 rule
// engine stay in Python.  After a geometry edit the animation is showing a program that
// was compiled against the PREVIOUS device, so the page says so and strikes the rule
// badges out rather than leaving a green tick for a check that did not run.  A rule badge
// that still says "pass" after the design changed is worse than no badge.
//
// HARNESS CONSTRAINTS THAT SHAPE THE CODE (tests/census.mjs, tests/panels.mjs)
// ---------------------------------------------------------------------------
//  * `classList` is a no-op in the shim, so NO editor behaviour may depend on a CSS class;
//    every piece of editor state lives in a JS variable and classes are presentation only.
//  * `Event` / `dispatchEvent` do not exist, so a headless test cannot synthesize a drag.
//    Every pointer handler is therefore a thin adapter that converts client coordinates to
//    model coordinates and calls `EDITOR.begin/move/drop`; any logic left inside an event
//    handler would be logic with no test.
//  * `requestAnimationFrame` / `setTimeout` are stubbed to never fire, so a debounced
//    re-pricer would be invisible.  `globalThis.__QCCD_SYNC` makes it run synchronously.
//  * `element.remove()` is a no-op that leaves the child in `parent.children`, so a
//    rebuild clears through `replaceChildren()` (real DOM) or `children.length = 0` (the
//    shim's plain array) -- both correct in their own environment.
//  * `navigator` is entirely undefined, so the clipboard is feature-detected INSIDE the
//    click handler, and the visible textarea is the always-works path.
//
// And nothing here may name a network primitive: `render.py`'s FORBIDDEN scan rejects
// those substrings anywhere in the emitted file, comments included.

'use strict';

var EDITOR = (function () {
'use strict';

var Q = globalThis.QCCD, E = globalThis.QCCDEdit;
var SYNC = (typeof globalThis !== 'undefined' && globalThis.__QCCD_SYNC === true);

// ---------------------------------------------------------------------- state
var MODE = 'play';                 // 'play' | 'edit'
var EDITS = [], UNDONE = [];
var STATE = null;                  // the ArchState the last rebuild produced
var PROBLEMS = [], LINTS = [];
// clean | edited | stale | exact | blocked | unoracled
//
// `unoracled` is the fifth, and it exists because the per-frame self-check -- the thing
// that lets this page display a cost at all -- compares each re-priced frame against the
// cost PYTHON shipped for it.  An AUTHORED programme has no such frames, so there is
// nothing to compare and `frameDrift` over `frameChecked === 0` would report a confident
// zero for a check that never ran.  The badge says so instead.
var PRICE = null, PRICE_STATUS = 'clean';
var GROUP = 0;
var SNAP = true, SELSET = [];
var DOWN = null, ARMED = null, GHOST = null;
var HW = null, HW0 = null;
var READY = false, WHY_NOT = null;

function ok() { return READY; }

// The BASE program: the shipped architecture as its own command list.  Without a listing
// the page cannot rebuild the architecture from first principles, so editing is refused
// outright rather than offered and then failing at the first click.
//
// SPLIT INTO THREE ORDERED LISTS, because building a device from nothing is a different
// SHAPE of edit from retuning one.  A builder statement has to run BEFORE the seal that
// turns the builder into a machine, and a retune has to run after it; an append-only log
// cannot express both.  So:
//
//   GEOM  -- the BUILD records (`DeviceBuilder`, `d.site`, `d.junction`, `d.segment`,
//            `d.loop`), in declaration order.  On a blank canvas this IS the geometry.
//   SEED  -- exactly one seal (`blank`, `blank_device`, `from_template`, `from_device`).
//   POST  -- the shipped listing's retunes, which follow the seal.
//
// An edit the user makes is still one record in EDITS; a record tagged `{build: ...}` is
// hoisted into GEOM's position at replay time so it lands before the seal, which is what
// lets ONE undo stack cover both kinds of gesture.
var GEOM = [], SEED = null, POST = [], LAST_FRAMES = null;

function splitListing() {
  var lines = (D.arch.listing && D.arch.listing.lines) || [];
  GEOM = []; SEED = null; POST = [];
  for (var i = 0; i < lines.length; i++) {
    if (lines[i].kind !== 'call' || !lines[i].call) continue;
    var c = lines[i].call, kind = kindOf(c.method);
    if (SEED === null && kind === 'build') GEOM.push(c);
    else if (SEED === null && kind === 'seed') SEED = c;
    else POST.push(c);
  }
}

// The engine advertises its own vocabulary; this file must not keep a second copy of it.
function kindOf(method) {
  if (Q.BUILD_METHODS.indexOf(method) >= 0) return 'build';
  if (Q.SEED_METHODS.indexOf(method) >= 0) return 'seed';
  return 'mutate';
}

function baseCalls() { return baseCallsFrom(GEOM, SEED, POST, EDITS); }

function baseCallsFrom(geom, seed, post, edits) {
  var out = geom.slice();
  for (var i = 0; i < edits.length; i++) if (edits[i].build) out.push(edits[i].build);
  if (seed) out.push(seed);
  return out.concat(post);
}
// the same statements as the TEXT Python emitted for them, which is the only faithful
// rendering of a record that crossed a JSON boundary -- see `sourceText`
function baseTexts() {
  var lines = (D.arch.listing && D.arch.listing.lines) || [];
  var out = [], by = {};
  for (var i = 0; i < lines.length; i++) {
    if (lines[i].kind === 'call' && lines[i].call) by[JSON.stringify(lines[i].call)] = lines[i].text;
  }
  // in the order `baseCalls()` produces, so `applySource`'s index-by-index diff still
  // lines up once the listing has been split into three lists
  var calls = baseCalls();
  for (var j = 0; j < calls.length; j++) {
    var k = JSON.stringify(calls[j]);
    out.push(has(by, k) ? by[k] : Q.render(calls[j]));
  }
  return out;
}

// ------------------------------------------------------------------- geometry helpers
function nodesOf(st) {
  var out = [];
  for (var nid in st.device.nodes) if (has(st.device.nodes, nid)) {
    var n = st.device.nodes[nid];
    out.push({ id: nid, x: +Q.unbox(n.pos[0]), y: +Q.unbox(n.pos[1]) });
  }
  return out;
}
function segsOf(st) {
  var out = [];
  for (var sid in st.device.segments) if (has(st.device.segments, sid)) {
    out.push({ id: sid, a: st.device.segments[sid].a, b: st.device.segments[sid].b });
  }
  return out;
}
function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }

// ------------------------------------------------------------------- the applier
//
// ONE function, three callers: the drag, the text editor and undo/redo.  Replay from
// scratch every time.  Ops are ~80 bytes and the expensive part (layout + redraw) happens
// once per rebuild regardless, so there is nothing to buy by being clever -- and an
// incremental path would be a THIRD implementation with no oracle to check it against.
// Validate the document the current state would EXPORT, against the schema Python
// ships.  Two speeds, and the fast one is the only one that normally runs.
//
// FAST PATH: one walk of the finished document per rebuild.  If it is clean -- the
// overwhelmingly common case -- nothing else happens.
//
// SLOW PATH: only when it is dirty.  A schema error has a JSON path, not a statement
// index, and "your document is invalid somewhere" is not something a design tool may say.
// So the replay is re-run one statement at a time and each error is attributed to the
// FIRST statement whose document contained it.  That costs one walk per statement, but it
// only ever runs when the user has actually broken something, and what it buys is the
// existing rollback in `emit()`: a statement whose problem carries `i` is spliced back
// out of EDITS, exactly the way an interpreter refusal already is.  No new UX, no new
// channel, no invalid statement left sitting in the exported Python.
function schemaProblems(st, calls, edits) {
  var errs;
  try { errs = Q.validateDocument(Q.serialize(st)); }
  catch (err) { return [{ i: null, code: 'schema', message: err.message, path: null }]; }
  if (!errs.length) return [];

  // `Object.create(null)`, not `{}`: the KEYS here are error messages, i.e. text derived
  // from a document the user controls, and a plain object would answer `blame['toString']`
  // with a function rather than undefined.  Every message the schema produces starts with
  // `$.`, so this is defence rather than a live bug -- but a lookup table keyed by
  // user-shaped strings should never inherit a prototype.
  var blame = Object.create(null), i, j;
  for (i = 0; i < errs.length; i++) blame[errs[i]] = null;
  var seen = Object.create(null), walk = null;
  try {
    var r0 = Q.applyProgram(calls);
    walk = r0.error ? null : r0.ok;
  } catch (err) { walk = null; }
  if (walk) {
    // BASE first: an error already present in the shipped architecture is nobody's edit.
    try {
      var base = Q.validateDocument(Q.serialize(walk));
      for (i = 0; i < base.length; i++) seen[base[i]] = true;
    } catch (err) { /* fall through: everything gets blamed on an edit */ }
    for (i = 0; i < edits.length && walk; i++) {
      if (edits[i].build) continue;
      if (edits[i].topology) {
        try {
          var pair = E.applyEdit(walk.device === null ? null : walk.device, edits[i].topology);
          walk.device = pair[0];
        } catch (err) { continue; }
      } else {
        try { walk = Q.apply(walk, edits[i]); } catch (err) { continue; }
      }
      var now;
      try { now = Q.validateDocument(Q.serialize(walk)); } catch (err) { continue; }
      for (j = 0; j < now.length; j++) {
        if (!seen[now[j]]) { seen[now[j]] = true; if (blame[now[j]] === null) blame[now[j]] = i; }
      }
    }
  }
  var out = [];
  for (i = 0; i < errs.length; i++) {
    out.push({ i: blame[errs[i]], code: 'schema',
               message: 'this edit makes a document the toolchain will not load: ' + errs[i],
               path: errs[i].split(':')[0] });
  }
  return out;
}

function replay() {
  var calls = baseCalls();
  var r = Q.applyProgram(calls);
  if (r.error) {
    return { error: 'the shipped listing does not replay in the browser: ' +
                    r.error.message + ' (statement ' + r.error.index + ')' };
  }
  var st = r.ok, problems = [];
  for (var i = 0; i < EDITS.length; i++) {
    var op = EDITS[i];
    // a `{build: ...}` record already ran, hoisted into GEOM's position by `baseCalls`
    if (op.build) continue;
    try {
      if (op.topology) {
        var pair = E.applyEdit(st.device === null ? null : wireOf(st), op.topology);
        st.device = pair[0];
        op._report = pair[1];
      } else {
        st = Q.apply(st, op);
      }
    } catch (err) {
      problems.push({ i: i, op: op, message: err.message, code: err.code || 'refused' });
    }
  }
  // A statement can be accepted by the interpreter and STILL produce a document Python's
  // loader refuses -- `set_curve(..., table="mytable")` was accepted by both sides and
  // exported an unloadable file, because the browser knew nothing about the schema's
  // enums.  It does now, and the refusal is reported through the same channel.
  var sp = schemaProblems(st, calls, EDITS);
  for (var k = 0; k < sp.length; k++) {
    if (sp[k].i !== null && sp[k].i >= 0 && sp[k].i < EDITS.length) sp[k].op = EDITS[sp[k].i];
    problems.push(sp[k]);
  }
  return { ok: st, problems: problems };
}
function wireOf(st) { return st.device; }

// ------------------------------------------------------------------- rebuild the picture
//
// A full recompute, deliberately.  There is no decomposition that would let an edit
// recompute "only what it changed": `g` is a global minimum over all pairs, `pad` is a
// five-pass fixed point over `g`, and all thirty mark scalars are fractions of `g`, so
// moving ONE node can change every number on the stage.  Full recompute of the shipped
// ring is well under a millisecond; an incremental path would be a third implementation
// with different failure modes and nothing to check it against.
function rebuild(opts) {
  opts = opts || {};
  var r = replay();
  if (r.error) { WHY_NOT = r.error; READY = false; return; }
  STATE = r.ok;
  PROBLEMS = r.problems;
  LINTS = Q.lint(STATE);
  // A topology edit can leave the device in disconnected pieces. `edit.js` DETECTS that
  // and returns a warning, `replay()` stored it on `op._report` -- and nothing ever read
  // it. Removing two segments on grid9x9 split the device into 2 components with J0_0
  // orphaned and the page reported a perfectly ordinary price line. Surface it through
  // the channel the page already shows.
  for (var ri = 0; ri < EDITS.length; ri++) {
    var rep = EDITS[ri]._report;
    if (!rep || !rep.warnings) continue;
    for (var wi = 0; wi < rep.warnings.length; wi++) {
      LINTS.push({ rule: 'structure', severity: 'warn',
                   message: rep.warnings[wi],
                   components: rep.components_after || null });
    }
  }

  // 0. THE SPATIAL INDEX IS DERIVED STATE, NOT CACHED STATE.  `buildIndex()` ran once,
  //    lazily, and nothing invalidated it -- the comment on `GRID` claimed `rebuild()`
  //    did and `rebuild()` never touched it.  Measured consequences, all three of them
  //    the user's complaint verbatim: on a device built in the studio the index had been
  //    built over an EMPTY canvas, so 0 of 14 nodes were clickable, for ever; after one
  //    drag on a shipped page reach fell 168/168 -> 96/168 and a node answered at its OLD
  //    drawn centre; and after a delete the index still named the removed node, so the
  //    next pointermove dereferenced nodeById[<gone>] and threw -- killing hover,
  //    selection and dragging for the rest of the session.
  GRID = null;

  // 1. re-lay-out
  var lay = Q.computeLayout(nodesOf(STATE), segsOf(STATE));
  for (var k in lay) if (has(lay, k)) L[k] = lay[k];

  // 2. rewrite the page's own view of the device, IN PLACE: `A` is captured by every
  //    closure in the main script, so it is mutated rather than replaced.
  syncArch();

  // 3. LOWER, then redraw, then re-price.  Lowering runs on every ARCHITECTURE edit as
  //    well, which is what keeps an authored programme's `entails` from going stale: it
  //    is read from the live class table rather than baked at emit time.
  lowerNow();
  // ONLY WHEN THE FRAMES THEMSELVES CHANGED.  `states`/`before`/`SLOTS`/`cum` describe a
  // PROGRAMME, not a device: re-deriving them against an edited device would walk the
  // shipped programme over nodes that no longer exist -- which is the state the page
  // FREEZES on, and freezing is the honest answer, not silently re-deriving a picture of
  // a programme that cannot run.
  if (LAST_FRAMES !== P.frames) {
    LAST_FRAMES = P.frames;
    if (typeof deriveStage === 'function') deriveStage(P.frames);
    if (typeof slider !== 'undefined' && slider) {
      slider.max = String(Math.max(0, P.frames.length - 1));
      if (typeof frame === 'number' && frame > P.frames.length - 1) frame = 0;
    }
  }
  rebuildStatic();
  repriceNow();
  // THE VERDICTS ARE RE-DERIVED, not struck through.  `RULES_STALE` used to invalidate
  // all 25 at once because none of them could be re-run here; 17 of them now can, off the
  // walk `repriceNow` just did, and only the other 6 go grey.  Keeping both mechanisms
  // would leave the page with two answers about the same rule.
  if (typeof renderSide === 'function') renderSide();
  if (typeof renderReport === 'function') renderReport();
  if (typeof sizeStage === 'function') sizeStage();
  if (typeof draw === 'function') draw();
  paint();
  // KEEP THE WORK. Debounced, because this runs on every keystroke in the source pane.
  // `autosave` was written and had no caller at all.
  if (typeof autosaveSoon === 'function' && READY) autosaveSoon();
}

// Push the edited architecture back into the drawing shape the main script reads.  The
// derived quantities (`deg`, `corner`, `corner_endpoints`) come from the edit mirror,
// which has its own differential test against Python -- they are NOT recomputed here.
function syncArch() {
  var dev = STATE.device;
  var deriv = E.derived(dev);
  var nodes = [], segments = [], loops = {}, nid, sid, lid;
  for (nid in dev.nodes) if (has(dev.nodes, nid)) {
    var n = dev.nodes[nid];
    nodes.push({ id: nid, x: +Q.unbox(n.pos[0]), y: +Q.unbox(n.pos[1]), kind: n.kind,
                 zone: n.zone, cap: n.cap, deg: deriv.degree[nid],
                 corner: !!deriv.corner[nid], labels: (n.labels || []).slice(),
                 cap_explicit: !!n.capacity_explicit });
  }
  for (sid in dev.segments) if (has(dev.segments, sid)) {
    var s = dev.segments[sid];
    segments.push({ id: sid, a: s.a, b: s.b, loop: s.loop,
                    labels: (s.labels || []).slice(), cap: s.cap,
                    len: +Q.unbox(s.length),
                    corner_endpoints: deriv.corner_endpoints[sid] });
  }
  for (lid in dev.loops) if (has(dev.loops, lid)) loops[lid] = dev.loops[lid].nodes.slice();
  A.nodes = nodes; A.segments = segments; A.loops = loops;
  A.zone_types = STATE.zone_types;
  for (var kk in nodeById) if (has(nodeById, kk)) delete nodeById[kk];
  for (var k2 in segById) if (has(segById, k2)) delete segById[k2];
  for (var i = 0; i < nodes.length; i++) nodeById[nodes[i].id] = nodes[i];
  for (var j = 0; j < segments.length; j++) segById[segments[j].id] = segments[j];
}

// Clear a group in a way that is correct in BOTH environments: `replaceChildren()` in a
// real browser, and truncating the plain array the DOM shim uses for `children`.  The
// shim's `remove()` is a no-op that leaves the child in place, which is why nothing here
// removes children one at a time.
function clearGroup(g) {
  if (g && typeof g.replaceChildren === 'function') { g.replaceChildren(); return; }
  if (g && g.children && typeof g.children.length === 'number') g.children.length = 0;
}

function rebuildStatic() {
  clearGroup(gLoop); clearGroup(gSeg); clearGroup(gElec); clearGroup(gNode);
  var maps = [SEGEL, SEGINFO, PAD_BY_SEG, SEG_BY_PAIR, NODEEL, CAPTXT];
  for (var m = 0; m < maps.length; m++) {
    for (var k in maps[m]) if (has(maps[m], k)) delete maps[m][k];
  }
  for (var a in AXIS) if (has(AXIS, a)) delete AXIS[a];
  // `lastHot`, `lastSegHot` and `lastMark` hold references into the element set just
  // destroyed above.  Benign while the only reader was `draw()`'s own inline cooldown
  // passes -- they overwrite orphans harmlessly -- but `clearTransients()` is now shared
  // with the freeze path, so the first frozen draw after a rebuild would write to pad
  // objects nothing renders.
  lastHot = []; lastSegHot = []; lastMark = [];
  rebuildAxis();
  // THE STAGE IS ONE SCENE among the ones buildStatic can draw; the palette avatars are
  // the others.  Same function, same layout constants, same palette -- so the picture in
  // the menu cannot drift from the picture on the canvas by construction.
  buildStatic(STAGE);
  refitVB();
  svg.setAttribute('viewBox', VB.x + ' ' + VB.y + ' ' + VB.w + ' ' + VB.h);
}

// The layout is recomputed on every geometry edit -- `g`, `W` and `H` all move -- but the
// viewBox was written back UNCHANGED, so an edit that reshapes the device pushed nodes
// out of frame with no indication.  Measured on h2_racetrack: drag S1 by 0.88 of a step
// and g goes 72 -> 9.89, H changes, and four nodes sit outside the viewBox.
//
// If the frame was showing the whole device, keep showing the whole device.  If the user
// had zoomed in, keep their zoom -- but never let the content escape the frame entirely:
// grow the box just enough to contain it.
var LASTFIT = { w: L.W, h: L.H };
function refitVB() {
  var fitted = Math.abs(VB.x) < 0.5 && Math.abs(VB.y) < 0.5 &&
               Math.abs(VB.w - LASTFIT.w) < 0.5 && Math.abs(VB.h - LASTFIT.h) < 0.5;
  if (fitted) {
    VB.x = 0; VB.y = 0; VB.w = L.W; VB.h = L.H;
  } else {
    var x1 = VB.x + VB.w, y1 = VB.y + VB.h;
    if (VB.x > 0) VB.x = 0;
    if (VB.y > 0) VB.y = 0;
    if (x1 < L.W) x1 = L.W;
    if (y1 < L.H) y1 = L.H;
    VB.w = x1 - VB.x; VB.h = y1 - VB.y;
  }
  LASTFIT.w = L.W; LASTFIT.h = L.H;
}

// the trap axis at each node -- the same derivation the main script does at load, re-run
// because a drag changes every incident direction
function rebuildAxis() {
  var got = axisOf(A.nodes, A.segments, px, py, nodeById), k;
  for (k in AXIS) if (has(AXIS, k)) delete AXIS[k];
  for (k in got) if (has(got, k)) AXIS[k] = got[k];
}

// The SAME derivation, taking its scene explicitly.  A palette avatar lays out its own
// micro-device and needs the trap axis for it; deriving that a second time is how the
// menu picture starts disagreeing with the canvas picture about which way a bar points.
function axisOf(nodes, segments, px, py, byId) {
  var AXIS = {}, arms = {}, i;
  for (i = 0; i < segments.length; i++) {
    var sg = segments[i], a = byId[sg.a], b = byId[sg.b];
    if (!a || !b) continue;
    var dx = px(b) - px(a), dy = py(b) - py(a), h = Math.sqrt(dx * dx + dy * dy);
    if (h < 1e-9) continue;
    var ux = dx / h, uy = dy / h;
    if (ux < -1e-12 || (Math.abs(ux) < 1e-12 && uy < 0)) { ux = -ux; uy = -uy; }
    (arms[sg.a] || (arms[sg.a] = [])).push([ux, uy]);
    (arms[sg.b] || (arms[sg.b] = [])).push([ux, uy]);
  }
  for (i = 0; i < nodes.length; i++) {
    var n = nodes[i], v = arms[n.id];
    if (!v || !v.length) { AXIS[n.id] = { ux: 1, uy: 0 }; continue; }
    var best = v[0], bs = -1;
    for (var c = 0; c < v.length; c++) {
      var sscore = 0;
      for (var o = 0; o < v.length; o++) sscore += Math.abs(v[c][0] * v[o][0] + v[c][1] * v[o][1]);
      if (sscore > bs) { bs = sscore; best = v[c]; }
    }
    AXIS[n.id] = { ux: best[0], uy: best[1] };
  }
  return AXIS;
}

// ------------------------------------------------------------------- re-pricing
//
// This EXTENDS the page's own self-check rather than adding a second mechanism.  The page
// already replays client-side and already reports out loud when its n-bar disagrees with
// the exported `D.checksum`; the engine now re-derives the constants from the EDITED
// architecture instead of reading baked ones, and accumulates cost / steps / us alongside
// the quanta.
//
// The per-frame diff is free and needs no extra payload: Python already ships `f.cost` and
// `f.steps` on every frame, so a disagreement localises to a frame index rather than to a
// total.  Totals-only agreement is explicitly not enough -- a compensating error inside the
// programme cancels, which is the bug shape that hid last time.
function repriceNow() {
  PRICE = null;
  // THE VERDICTS ARE CLEARED FIRST, not left standing.  `repriceNow` returns early on a
  // structural break, so anything not reset here would keep last rebuild's answer on
  // screen for a device it no longer describes -- a green tick for a check that did not
  // run, which is the one thing this surface must never show.
  RULES = null;
  if (!STATE) return;
  var dev = STATE.device;
  var classes = {};
  var extra = ((STATE.control.classes || {}).extra) || [];
  for (var i = 0; i < extra.length; i++) classes[extra[i].id] = extra[i];

  // hardware ALWAYS reprices: it is pure counting over the graph and the wiring block, it
  // costs a couple of milliseconds, and it is the number the WISE argument lives in.
  try {
    HW = Q.hardwareReport(dev, STATE.control, STATE.budget, STATE.name);
  } catch (err) { HW = null; }

  // structural checks BEFORE pricing.  The last one matters most: `Architecture.entails`
  // swallows a KeyError and returns (), so dropping a movement class reprices the whole
  // programme -- half the docking heat vanishes -- with NO error anywhere in the stack.
  // Python is silent, so parity holds and a differential test could never catch it.  Only
  // a structural check can.
  var bad = Q.validateProgram(dev, P.frames, classes);
  var lens = Q.loopLengths(dev), baseLens = BASE_LOOPS;
  for (var lid in baseLens) if (has(baseLens, lid)) {
    if (has(lens, lid) && lens[lid] !== baseLens[lid]) {
      bad.push({ kind: 'loop_resized', loop: lid, was: baseLens[lid], now: lens[lid],
                 count: 1 });
    }
  }
  // ONE derivation, TWO surfaces.  The price refuses and the stage stops off the same
  // array, so there is no state in which the page says "price unavailable" while the ions
  // keep moving -- which is exactly what shipped.
  //
  // The predicate is the STRUCTURAL `bad` array only, never `PRICE_STATUS === 'blocked'`:
  // `no_curve` and `price_error` below are cost-model failures, and a programme that still
  // fits the device is still a true picture.  Freezing on those would over-freeze.
  if (typeof PROGRAM_STALE !== 'undefined') {
    PROGRAM_STALE = bad.length
      ? { breaks: bad, why: breakMessage(bad[0]), n: bad.length } : null;
    if (typeof onProgramValidity === 'function') onProgramValidity();
  }
  PRICE_STATUS = bad.length ? 'blocked' : (EDITS.length ? 'edited' : 'clean');
  if (bad.length) { PRICE = { blocked: bad }; return; }

  var model;
  try {
    model = Q.makeModel(STATE.primitives, Q.degrees(dev), Q.cornerEndpoints(dev),
                        dev.segments, {
      kind: (D.model && D.model.name) === 'deck' ? 'deck' : 'corrected',
      corner_hops: D.model ? D.model.corner_hops : 1,
      junction_min_degree: PH.junction_min_degree || 3,
      length_scaling: !!(D.model && D.model.length_scaling),
      pitch: (D.model && D.model.pitch) || 1.0,
      include_anomalous: !(D.model && D.model.include_anomalous === false),
      anomalous_per_ms: PH.anomalous_per_ms || 0,
      policy: PH.policy || { table: 'qccdsim_jones', objective: 'fastest' }
    });
  } catch (err) {
    PRICE = { blocked: [{ kind: 'no_curve', message: err.message, count: 1 }] };
    PRICE_STATUS = 'blocked';
    return;
  }
  model._pair = Q.pairIndex(dev);
  try {
    PRICE = Q.priceFrames(P.frames, A.loops, model, classes);
  } catch (err) {
    PRICE = { blocked: [{ kind: 'price_error', message: err.message, count: 1 }] };
    PRICE_STATUS = 'blocked';
    return;
  }
  // the per-frame self-check, against the numbers Python already shipped
  var drift = 0, driftAt = null, n = 0;
  for (var f = 0; f < P.frames.length; f++) {
    var want = P.frames[f];
    if (want.cost === undefined) continue;
    n++;
    var d = Math.abs(PRICE.perFrame[f][0] - want.cost) + Math.abs(PRICE.perFrame[f][1] - (want.steps || 0));
    if (d > drift) { drift = d; driftAt = f; }
  }
  PRICE.frameDrift = drift;
  PRICE.frameDriftAt = driftAt;
  // THE VERDICTS, off the same model and the same frames the price just used.  Not a
  // second walk: `checkFrames` drives `priceFrames` with an `onCycle` hook, exactly as
  // `replay(on_cycle=...)` does in Python, so the panel and the verifier cannot disagree.
  evaluateNow(model);
  PRICE.frameChecked = n;
  if (!n && P.frames.length) PRICE_STATUS = 'unoracled';
}
var BASE_LOOPS = null;

// The debounced, chunked wrapper.  `requestAnimationFrame` is stubbed to never fire under
// the harness, so without the synchronous drain the re-pricer would be invisible to every
// headless test -- the same trap `syncCursor()` already documents in the main script.
var repriceTimer = null;
function reprice() {
  if (SYNC) { repriceNow(); return; }
  if (repriceTimer) clearTimeout(repriceTimer);
  repriceTimer = setTimeout(function () { repriceTimer = null; repriceNow(); paint(); }, 180);
}

// ------------------------------------------------------------------- hit testing
//
// GEOMETRIC, on the model, never on DOM targets.  `e.target._nid` gets away with it in the
// existing click path only because nothing overlaps a node; a pointerdown six pixels from
// a node centre lands on the rail `<line>`.  And `elementFromPoint` / `getBBox` /
// `getScreenCTM` are not stubbed by the harness, so anything built on them would be
// untestable as well as wrong.
// THE LETTERBOX. `preserveAspectRatio="xMidYMid meet"` fits the viewBox INSIDE the
// element and centres it, so whenever the two aspects differ there are margins. This used
// to divide by the element's full width and height, which was right only because
// `height:auto` made the element take the viewBox's own aspect exactly. The moment the
// canvas became a constant size those margins appeared, and every pointer coordinate was
// off by them -- clicks landed to one side of the cursor and drags fought the mouse.
//
// `getScreenCTM().inverse()` is the usual way to do this, but `tests/shim.mjs`
// deliberately does not stub it, so the geometry is computed here where a harness can
// check it.
function fitBox() {
  var r = svg.getBoundingClientRect();
  var w = Math.max(1, r.width), h = Math.max(1, r.height);
  var k = Math.min(w / Math.max(1e-9, VB.w), h / Math.max(1e-9, VB.h));  // 'meet'
  return { r: r, k: k, ox: (w - VB.w * k) / 2, oy: (h - VB.h * k) / 2 };
}
function toModel(clientX, clientY) {
  var f = fitBox();
  return { x: VB.x + (clientX - f.r.left - f.ox) / f.k,
           y: VB.y + (clientY - f.r.top - f.oy) / f.k };
}
function userPerPx() {
  // one screen pixel in user units, along the axis the fit is driven by
  return 1 / Math.max(1e-9, fitBox().k);
}

// A uniform grid over the nodes, cell = g.  Rebuilt inside `rebuild()`.  168 nodes is
// trivial; chain(288) and a future 2,000-node device are not, and a linear scan on every
// pointermove is what makes an editor feel broken on exactly the device that matters.
var GRID = null;
function buildIndex() {
  var cell = Math.max(L.g, 1), map = {};
  for (var i = 0; i < A.nodes.length; i++) {
    var n = A.nodes[i];
    var key = Math.floor(px(n) / cell) + ',' + Math.floor(py(n) / cell);
    (map[key] || (map[key] = [])).push(n.id);
  }
  GRID = { cell: cell, map: map };
}

// THE HIT TARGET IS THE DRAWN GEOMETRY.  It used to be two discs -- min(0.45g, 11px) for
// a node and min(0.35g, 8px) for a segment -- and a disc is the wrong shape in both
// directions at once.  Measured on the shipped pages: only 48% of a cap-4 site's own
// drawn BAR selected it (the bar sticks 8.18 px past the disc at each end, which is
// literally "I clicked the thing and nothing happened"), while the same disc covered so
// much of a segment that a segment was grabbable on 10% of its own length.
//
// So the target is read back out of the registries buildStatic populated -- NODEEL's
// len/ang/ax and SEGINFO's curve -- rather than derived a second time.  It cannot drift
// from the picture, because it IS the picture.
//
// ONE forgiveness, in units of g, with a hard ceiling -- and it is PERPENDICULAR ONLY on
// a site bar.  Measured why: a cap-4 bar is 0.88g long, so at one lattice step two of them
// already meet end to end; adding the slop ALONG the axis as well left a cyclone segment
// grabbable on 3% of its own length, WORSE than the discs it replaced.  Forgiveness
// belongs where nothing is competing for the pixel -- across the bar, where the nearest
// other target is a rail 0.5g away -- and nowhere near where a rail and a bar meet.
function slop() {
  var u = userPerPx();
  return Math.min(0.06 * L.g, Math.max(0.03 * L.g, 6 * u));
}

function siteHalfAxis(n) {
  var E = NODEEL[n.id];
  var ax = (E && E.ax) || AXIS[n.id] || { ux: 1, uy: 0 };
  var len = (E && E.len !== undefined) ? E.len : _siteLen(n.cap, L);
  return { ax: ax, len: len, half: Math.max(0, (len - L.site_t) / 2) };
}
function isJunctionNode(n) {
  var E = NODEEL[n.id];
  return E ? E.kind === 'junction' : (n.kind === 'junction' || (n.cap || 0) === 0);
}

// the radii the hit test actually uses, in USER units -- published so the spec is
// assertable in units of g rather than eyeballed off a screenshot
function hitRadii() {
  var S = slop(), node = {}, i;
  for (i = 0; i < A.nodes.length; i++) {
    var n = A.nodes[i];
    // ALONG-AXIS half-extent: exactly the drawn body, no slop.  0.44g max, so at one
    // lattice step it can never reach a neighbour's own 0.44g and steal its click.
    node[n.id] = isJunctionNode(n) ? L.r_junc : (siteHalfAxis(n).len / 2);
  }
  return { slop: S, node: node, seg: segBand(S), loop: 0.5 * L.sw_loop,
           node_across: L.site_t / 2 + S };
}

// A RAIL IS AT LEAST AS EASY TO HIT AS THE BAR SITTING ON IT.  `sw_rail/2` alone is
// 0.042g -- two pixels on the deck page -- which is why 67-72% of the stage answered
// nothing.  Derived from `site_t`, not chosen: the two things you click on a rail should
// not need different aim.
function segBand(S) { return Math.max(L.sw_rail / 2, L.site_t / 2) + S; }

function loopPointList(lid) {
  var seq = (A.loops || {})[lid] || [], out = [];
  for (var i = 0; i < seq.length; i++) {
    var n = nodeById[seq[i]];
    if (n) out.push([px(n), py(n)]);
  }
  return out;
}

function hit(mx, my, coarse) {
  if (!GRID) buildIndex();
  var S = slop(), SS = coarse ? 2 * S : S;
  var bestJ = null, bestS = null;
  var cx = Math.floor(mx / GRID.cell), cy = Math.floor(my / GRID.cell);
  for (var i = -1; i <= 1; i++) for (var j = -1; j <= 1; j++) {
    var ids = GRID.map[(cx + i) + ',' + (cy + j)];
    if (!ids) continue;
    for (var k = 0; k < ids.length; k++) {
      // A STALE INDEX CAN NO LONGER THROW.  rebuild() clears GRID now, but the guard
      // stays: this function used to be the thing that broke the whole session after a
      // delete, and a hit test is not the right place to find that out.
      var n = nodeById[ids[k]];
      if (!n) continue;
      var X = px(n), Y = py(n);
      if (isJunctionNode(n)) {
        // the drawn square, exactly.  It is 0.6g across already -- inflating it would only
        // take the pixel away from the three or four rails that meet inside it.
        var h = L.r_junc;
        var dj = Math.max(Math.abs(mx - X), Math.abs(my - Y));
        if (dj <= h && (bestJ === null || dj < bestJ.dist)) {
          bestJ = { kind: 'junction', id: n.id, dist: dj };
        }
        continue;
      }
      // THE DRAWN BAR, in its own frame: along the axis it is exactly as long as it is
      // drawn, across the axis it gets the slop.
      var G = siteHalfAxis(n);
      var ex = mx - X, ey = my - Y;
      var u = ex * G.ax.ux + ey * G.ax.uy, v = -ex * G.ax.uy + ey * G.ax.ux;
      // INCLUSIVE at the tip.  A bar's own end is part of the bar, and a strict
      // comparison loses it to floating point on about 7% of the probes -- the
      // difference between "the whole drawn body is clickable" and "nearly all".
      if (Math.abs(u) <= G.len / 2 + 1e-9 && Math.abs(v) <= L.site_t / 2 + S) {
        // distance to the bar's own centre line, for ordering WITHIN the kind only
        var d = Math.sqrt(Math.max(0, Math.abs(u) - G.half) * Math.max(0, Math.abs(u) - G.half) +
                          v * v);
        if (bestS === null || d < bestS.dist) bestS = { kind: 'site', id: n.id, dist: d };
      }
    }
  }
  // STRICT ORDER, first inside wins -- never a cross-kind distance comparison.  A site's
  // bar and the rail it sits on are both under the cursor at the site's own centre; the
  // one you are pointing at is the one whose DRAWN BODY contains the point.
  if (bestJ) return bestJ;
  if (bestS) return bestS;
  var best = null;
  for (var sI = 0; sI < A.segments.length; sI++) {
    var sg = A.segments[sI], a = nodeById[sg.a], b = nodeById[sg.b];
    if (!a || !b) continue;
    var I = SEGINFO[sg.id];
    var dd;
    if (I && I.cp) {
      // A bowed segment is a quadratic; sample it and take the polyline minimum, on the
      // same curve everything else that rides the segment is evaluated on.
      //
      // THE SAMPLE COUNT IS DERIVED, NOT CHOSEN. A fixed 16 leaves gaps of `len/16`
      // between samples, so the worst-case error against the true curve is half of that
      // -- and on the shipped ring's end cap that error is the same size as the grab
      // band, which cost a third of the rail: measured, E71 answered on 67% of its own
      // drawn length while every straight rail answered on 100%. Space the samples at
      // about a third of the band instead, so the approximation error is always well
      // inside it, and cap the count so a very long bow cannot make the hit test slow.
      dd = Infinity;
      var band = segBand(SS);
      var approx = Math.abs(I.len || 0) || (0.5 * (Math.abs(px(b) - px(a)) + Math.abs(py(b) - py(a))));
      var nS = Math.max(16, Math.min(96, Math.ceil(approx / Math.max(1e-6, 0.34 * band))));
      for (var t = 0; t <= nS; t++) {
        var q = bezPoint(I, t / nS);
        var e = Math.sqrt((q.x - mx) * (q.x - mx) + (q.y - my) * (q.y - my));
        if (e < dd) dd = e;
      }
    } else {
      dd = Q.pointSegment([mx, my], [px(a), py(a)], [px(b), py(b)])[0];
    }
    if (dd <= segBand(SS) && (best === null || dd < best.dist)) {
      best = { kind: 'segment', id: sg.id, dist: dd };
    }
  }
  if (best) return best;
  // LOOPS LAST, by their own coloured halo.  hit() returned kind:'loop' zero times in
  // 25,000 probes because it had no loop pass at all -- a loop was the one thing on the
  // stage that was drawn and could not be touched.  The band it is grabbable in is the
  // one the rails do not already own.
  var bl = null;
  for (var lid in (A.loops || {})) if (has(A.loops, lid)) {
    var pts = loopPointList(lid);
    if (pts.length < 3) continue;
    var dl = Infinity;
    for (var q2 = 0; q2 < pts.length; q2++) {
      var r2 = pts[(q2 + 1) % pts.length];
      var dq = Q.pointSegment([mx, my], pts[q2], r2)[0];
      if (dq < dl) dl = dq;
    }
    if (dl <= 0.5 * L.sw_loop && (bl === null || dl < bl.dist)) {
      bl = { kind: 'loop', id: lid, dist: dl };
    }
  }
  return bl;
}

// THE ONE ARBITER of who owns a press.  The page's pan handler had no mode guard at all,
// so both it and the editor ran on every pointerdown and the stage panned out from under
// a node drag (measured: VB.x 0 -> -8.96 on a replayed drag).  render.py asks this and
// restates nothing; play mode is byte-for-byte as it was.
//
// Pure, and callable without an Event, so a harness can assert the RULE rather than the
// handler that happens to obey it.
function claim(mx, my, mod) {
  mod = mod || {};
  if (MODE !== 'edit') return 'pan';
  if (mod.space || mod.button === 1 || mod.button === 2) return 'pan';
  return hit(mx, my) ? 'element' : 'marquee';
}
function claimEvent(e) {
  if (MODE !== 'edit') return 'pan';
  var m = toModel(e.clientX, e.clientY);
  return claim(m.x, m.y, { button: e.button, space: SPACE, alt: e.altKey,
                           shift: e.shiftKey, ctrl: e.ctrlKey });
}

// THE SHAPE THE HIGHLIGHT DRAWS, derived from what was DRAWN and never computed a second
// time.  The hover ring, the selection ring and the marquee preview all call this, so a
// regression back to a 0.55*g circle fails a test rather than a screenshot.
function outlineOf(kind, id) {
  var S = slop();
  if (kind === 'segment') {
    var I = SEGINFO[id];
    if (!I) return null;
    return I.cp
      ? { tag: 'path', d: 'M ' + I.ax + ' ' + I.ay + ' Q ' + I.cp.x + ' ' + I.cp.y +
                          ' ' + (I.ax + I.dx) + ' ' + (I.ay + I.dy) }
      : { tag: 'line', x1: I.ax, y1: I.ay, x2: I.ax + I.dx, y2: I.ay + I.dy };
  }
  if (kind === 'loop') {
    var pts = loopPointList(id);
    if (pts.length < 3) return null;
    return { tag: 'polyline',
             points: pts.map(function (q) { return q[0] + ',' + q[1]; }).join(' ') +
                     ' ' + pts[0][0] + ',' + pts[0][1] };
  }
  var n = nodeById[id];
  if (!n) return null;
  if (isJunctionNode(n)) {
    var h = L.r_junc;
    return { tag: 'rect', x: px(n) - h, y: py(n) - h, width: 2 * h, height: 2 * h,
             rx: 0.12 * L.g };
  }
  // the SAME rule the hit test uses: drawn length, slop across.  A highlight wider than
  // the target would promise a click the stage will not honour.
  var E = NODEEL[id], G = siteHalfAxis(n);
  var w = G.len, t = L.site_t + 2 * S;
  var ang = (E && E.ang !== undefined) ? E.ang
          : Math.atan2(G.ax.uy, G.ax.ux) * 180 / Math.PI;
  return { tag: 'rect', x: px(n) - w / 2, y: py(n) - t / 2, width: w, height: t, rx: t / 2,
           transform: 'rotate(' + ang + ' ' + px(n) + ' ' + py(n) + ')' };
}

// ------------------------------------------------------------------- snapping
function snapTo(x, y, free, fine) {
  if (!SNAP || free) return { x: x, y: y, guides: [] };
  var stepx = (L.ux || 1) / (fine ? 4 : 1), stepy = (L.uy || L.ux || 1) / (fine ? 4 : 1);
  var rx = Math.round(x / stepx) * stepx, ry = Math.round(y / stepy) * stepy;
  var out = { x: x, y: y, guides: [] };
  if (Math.abs(rx - x) * L.sx < 0.30 * stepx * L.sx) out.x = rx;
  if (Math.abs(ry - y) * L.sy < 0.30 * stepy * L.sy) out.y = ry;
  // alignment snap.  Not cosmetic: `compute_layout` tests `axis_aligned` and falls back
  // from anisotropic to isotropic the instant ONE diagonal segment exists, which visibly
  // rescales the whole picture.  Keeping a drag on-axis is what keeps that from happening
  // by accident.
  for (var i = 0; i < A.nodes.length; i++) {
    var n = A.nodes[i];
    // every member of the drag, not only the one pressed: a group drag that snapped to
    // its own passengers would pin itself in place
    if (GHOST && (GHOST.ids ? GHOST.ids.indexOf(n.id) >= 0 : n.id === GHOST.id)) continue;
    if (Math.abs((n.x - out.x) * L.sx) < 0.15 * L.g) { out.x = n.x; out.guides.push(['x', n.x]); }
    if (Math.abs((n.y - out.y) * L.sy) < 0.15 * L.g) { out.y = n.y; out.guides.push(['y', n.y]); }
  }
  return out;
}

// ------------------------------------------------------------------- validation
//
// Three tiers, immediate, never modal.  Tier 2 is the interesting one: refused at commit,
// naming the object.
function validate(op) {
  var problems = [];
  if (!STATE) return problems;
  if (op.method === 'move_site') {
    var nid = op.args[0], x = +Q.unbox(op.args[1]), y = +Q.unbox(op.args[2]);
    if (!isFinite(x) || !isFinite(y) || Math.abs(x) > Q.COORD_MAX || Math.abs(y) > Q.COORD_MAX) {
      problems.push({ code: 'coord_range', targets: [nid],
                      message: nid + ' would sit outside the range the layout can measure' });
    }
    // COINCIDENCE IS A HARD REFUSAL, not a warning.  `min_nearest_neighbour` SKIPS
    // coincident points, so two nodes at one position make `g` get measured off the NEXT
    // pair and every mark on the stage silently becomes the wrong size -- `2*r_ion < g`
    // stops meaning what it says.  Completely invisible, which is why it cannot be a
    // warning.
    for (var i = 0; i < A.nodes.length; i++) {
      var n = A.nodes[i];
      if (n.id === nid) continue;
      var d = Math.sqrt((n.x - x) * (n.x - x) + (n.y - y) * (n.y - y));
      if (d < 0.05 * (L.gd || 1)) {
        problems.push({ code: 'coincident', targets: [nid, n.id],
                        message: nid + ' would sit on top of ' + n.id +
                          '. The layout measures the nearest-neighbour gap, and two nodes ' +
                          'at one point make every mark the wrong size.' });
      }
    }
  }
  if (op.method === 'set_site_capacity') {
    var site = typeof op.args[0] === 'string' ? op.args[0] : op.args[0][0];
    var cap = Math.trunc(+Q.unbox(op.args[1]));
    if (cap < 1) {
      problems.push({ code: 'cap_lt_1', targets: [site],
                      message: 'a site must be able to hold at least one ion' });
    } else if (MAXOCC[site] !== undefined && cap < MAXOCC[site]) {
      problems.push({ code: 'cap_below_occupancy', targets: [site],
                      message: site + ' holds ' + MAXOCC[site] + ' ions at some point in ' +
                               'this programme; capacity cannot go below that' });
    }
  }
  return problems;
}
// max occupancy per site over the whole programme -- ONE integer per site, precomputed
// once.  A per-frame occupancy table would be 1,975 x 168 on the deck page.
var MAXOCC = {};
function buildMaxOcc() {
  if (typeof states === 'undefined' || !states) return;
  for (var i = 0; i < states.length; i++) {
    var occ = {}, pos = states[i].pos;
    for (var ion in pos) if (has(pos, ion)) occ[pos[ion]] = (occ[pos[ion]] || 0) + 1;
    for (var s in occ) if (has(occ, s)) {
      if (!(MAXOCC[s] >= occ[s])) MAXOCC[s] = occ[s];
    }
  }
}

// ------------------------------------------------------------------- the gesture API
//
// Every pointer handler is a THIN ADAPTER onto these.  The headless harness cannot
// synthesize a pointer event -- `Event` and `dispatchEvent` do not exist in the shim -- so
// any logic left inside a handler would be logic with no test.  These five functions are
// the whole drag, and `tests/editor.mjs` drives them directly.
// WHAT A PRESS IS ACTUALLY DRAGGING.  A segment is not a node, so `begin('segment', ...)`
// used to return null and a segment could be selected but never moved; a loop could be
// neither.  One place expands a press into the nodes that will actually move, and a press
// INSIDE a multi-selection moves the whole selection rather than dropping it.
function subjectOf(kind, id) {
  var out = [], i, q;
  function push(nid) { if (nodeById[nid] && out.indexOf(nid) < 0) out.push(nid); }
  if (kind === 'segment') { var sg = segById[id]; if (sg) { push(sg.a); push(sg.b); } return out; }
  if (kind === 'loop') {
    var w = (A.loops || {})[id] || [];
    for (i = 0; i < w.length; i++) push(w[i]);
    return out;
  }
  for (i = 0; i < SELSET.length; i++) {
    var sl = SELSET[i];
    if (sl.kind === 'segment') { var s2 = segById[sl.id]; if (s2) { push(s2.a); push(s2.b); } }
    else if (sl.kind === 'loop') {
      var w2 = (A.loops || {})[sl.id] || [];
      for (q = 0; q < w2.length; q++) push(w2[q]);
    } else push(sl.id);
  }
  if (out.indexOf(id) >= 0) return out;
  return nodeById[id] ? [id] : [];
}

function begin(kind, id, mx, my) {
  var ids = subjectOf(kind, id);
  if (!ids.length) return null;
  var anchor = nodeById[id] ? id : ids[0];
  var n = nodeById[anchor];
  GROUP++;
  var p0 = [], i;
  for (i = 0; i < ids.length; i++) p0.push({ id: ids[i], x: nodeById[ids[i]].x,
                                             y: nodeById[ids[i]].y });
  GHOST = { kind: kind, id: id, anchor: anchor, ids: ids, p0: p0,
            x0: n.x, y0: n.y, mx0: mx, my0: my,
            px0: px(n), py0: py(n), group: 'g' + GROUP };
  return GHOST;
}

function move(mx, my, opts) {
  if (!GHOST) return null;
  opts = opts || {};
  var dx = (mx - GHOST.mx0) / (L.sx || 1), dy = (my - GHOST.my0) / (L.sy || 1);
  var s = snapTo(GHOST.x0 + dx, GHOST.y0 + dy, opts.free, opts.fine);
  GHOST.x = s.x; GHOST.y = s.y; GHOST.guides = s.guides;
  // THE SNAP IS COMPUTED ON THE PRESSED MEMBER ONLY and applied to the rest as one
  // translation, so a rigid group keeps the shape it started with instead of every
  // passenger collapsing onto the nearest lattice point independently.
  var ox = s.x - GHOST.x0, oy = s.y - GHOST.y0;
  // LAYOUT IS FROZEN FOR THE WHOLE DRAG.  Recomputing it per pointermove changes sx, sy,
  // ox, oy and g and therefore EVERY mark, and rebuilding the static picture creates
  // thousands of SVG elements.  So the dragged node's own marks move and its incident
  // segments get new endpoints; everything else waits for the drop.
  var warnings = [];
  if (breaksAxisAlignment(GHOST.id, s.x, s.y) && L.axis_aligned) {
    warnings.push('this makes a segment diagonal: the fit switches from anisotropic (' +
                  L.sx + ' x ' + L.sy + ' px/unit) to isotropic and the whole picture ' +
                  'rescales');
  }
  var decl = declaredMismatch(GHOST.id, s.x, s.y);
  if (decl) warnings.push(decl);
  GHOST.warnings = warnings;
  for (var mi = 0; mi < GHOST.p0.length; mi++) {
    var r0 = GHOST.p0[mi];
    liveMove(r0.id, r0.x + ox, r0.y + oy);
  }
  return { x: s.x, y: s.y, snapped: (s.x !== GHOST.x0 + dx) || (s.y !== GHOST.y0 + dy),
           guides: s.guides, warnings: warnings, ids: GHOST.ids.slice() };
}

function drop() {
  if (!GHOST) return null;
  var g = GHOST, i;
  var ox = (g.x === undefined ? 0 : g.x - g.x0), oy = (g.y === undefined ? 0 : g.y - g.y0);
  // ONE move_site PER MEMBER, all stamped with the SAME meta.group, so a group drag is
  // one entry in the undo stack rather than N.
  var ops = [], problems = [], op = null;
  for (i = 0; i < g.p0.length; i++) {
    var r = g.p0[i];
    var o = { method: 'move_site',
              args: [r.id, Q.pyFloat(r.x + ox), Q.pyFloat(r.y + oy)],
              kwargs: {}, meta: { group: g.group, src: 'stage' } };
    ops.push(o);
    if (r.id === g.anchor) op = o;
    problems = problems.concat(validate(o));
  }
  if (!op) op = ops[0];
  GHOST = null;
  if (problems.length) { rebuild(); return { op: op, ops: ops, problems: problems }; }
  for (i = 0; i < ops.length; i++) EDITS.push(ops[i]);
  UNDONE.length = 0;
  rebuild();
  return { op: op, ops: ops, problems: [] };
}

// ------------------------------------------------------------------- marquee select
//
// Mirrors begin/move/drop exactly, so the pointer adapter and the harness verb have the
// same shape.  No index: it is a rectangle test.
var MARQ = null;
function marqueeBegin(mx, my) { MARQ = { x0: mx, y0: my, x1: mx, y1: my }; return MARQ; }
function marqueeMove(mx, my) {
  if (!MARQ) return null;
  MARQ.x1 = mx; MARQ.y1 = my; paintOverlay();
  return { x0: MARQ.x0, y0: MARQ.y0, x1: MARQ.x1, y1: MARQ.y1 };
}
function marqueeDrop(opts) {
  opts = opts || {};
  if (!MARQ) return SELSET;
  var x0 = Math.min(MARQ.x0, MARQ.x1), x1 = Math.max(MARQ.x0, MARQ.x1);
  var y0 = Math.min(MARQ.y0, MARQ.y1), y1 = Math.max(MARQ.y0, MARQ.y1);
  MARQ = null;
  var inside = {}, picked = [], i;
  for (i = 0; i < A.nodes.length; i++) {
    var n = A.nodes[i], X = px(n), Y = py(n);
    if (X < x0 || X > x1 || Y < y0 || Y > y1) continue;
    inside[n.id] = 1;
    picked.push({ kind: isJunctionNode(n) ? 'junction' : 'site', id: n.id });
  }
  // a segment is in when BOTH its endpoints are, a loop when ALL of its nodes are: the
  // rule that makes "drag a box round it and move it" mean what it looks like it means
  for (i = 0; i < A.segments.length; i++) {
    var sg = A.segments[i];
    if (inside[sg.a] && inside[sg.b]) picked.push({ kind: 'segment', id: sg.id });
  }
  for (var lid in (A.loops || {})) if (has(A.loops, lid)) {
    var w = A.loops[lid], all = w.length > 0;
    for (i = 0; i < w.length; i++) if (!inside[w[i]]) { all = false; break; }
    if (all) picked.push({ kind: 'loop', id: lid });
  }
  setSelection(opts.additive ? SELSET.concat(picked) : picked);
  return SELSET;
}

// ONE selection vocabulary -- 'site' | 'junction' | 'segment' | 'loop', exactly what
// `hit()` returns.  `removeSelected` used to test `kind === 'segment'` while
// `renderInspector` tested `kind === 'node'` and `hit()` returned neither for a node, so
// the Selection panel was blank for every node on the stage.  Normalising here means
// there is one vocabulary and the older 'node' spelling still works.
function normKind(k, id) {
  if (k === 'node') {
    var n = nodeById[id];
    return (n && isJunctionNode(n)) ? 'junction' : 'site';
  }
  return k;
}
function setSelection(list) {
  var out = [], seen = {}, i;
  for (i = 0; i < (list || []).length; i++) {
    var s0 = list[i], k = normKind(s0.kind, s0.id), key = k + ':' + s0.id;
    if (seen[key]) continue;
    seen[key] = 1; out.push({ kind: k, id: s0.id });
  }
  SELSET = out;
  paint();
  return SELSET;
}

function cancel() { GHOST = null; rebuild(); }

// WHAT ESCAPE MEANS, in one place.  It used to be bound only in the page's own handler,
// which cleared the programme filter and never told the editor -- so the two selection
// models disagreed and `drop()` after Escape still committed the move.  The key handler
// and the headless harness both call this, so there is one order and one answer.
function escapeGesture() {
  if (GHOST) { cancel(); setCursor(HOVERED); return 'drag'; }
  if (typeof PGHOST !== 'undefined' && PGHOST) { ghostCancel(); return 'placement'; }
  if (MARQ) { MARQ = null; paintOverlay(); return 'marquee'; }
  if (SELSET.length) {
    setSelection([]);
    if (typeof selectRef === 'function') selectRef(null, null);
    return 'selection';
  }
  if (ARMED_EL) { arm(null); return 'armed'; }
  if (FORM) { FORM = null; paint(); return 'form'; }
  return null;
}

// THE CURSOR IS STATE, and it must be readable.  CSS said `svg.editing{cursor:default}`,
// so in edit mode nothing on the stage looked draggable -- and `classList` is a no-op in
// the harness, so no test could have caught that.  Written to `style`, published here.
var CURSOR = '';
function cursor() { return CURSOR; }
function setCursor(h) {
  var c = MODE !== 'edit' ? '' :
          GHOST ? 'grabbing' :
          SPACE ? 'grab' :
          h ? 'move' : 'crosshair';
  CURSOR = c;
  if (typeof svg !== 'undefined' && svg && svg.style) svg.style.cursor = c;
  return c;
}

function commit(op) {
  EDITS.push(op);
  UNDONE.length = 0;
  rebuild();
  return { ok: true, problems: PROBLEMS };
}

// `emit` is the ONE entry point the text lane, the inspector and the mouse all write
// through.  There is only one applier, so the lanes cannot disagree.
function emit(op) {
  var problems = validate(op);
  if (problems.length) return { ok: false, problems: problems };
  // COMMIT, THEN CHECK THE APPLIER.  `validate` only knows about `move_site` and
  // `set_site_capacity`; every other method is refused (if at all) by the interpreter
  // during `replay()`, which records the refusal in PROBLEMS rather than throwing here.
  // Leaving a refused statement in EDITS would put it in the side editor, in the exported
  // Python and in the "Copy edits" record -- an export the toolchain will not run -- while
  // this function returned `ok: true`.  So take it back out, and report only the problems
  // that belong to THIS op rather than the whole replay's list.
  var at = EDITS.length;
  commit(op);
  var mine = PROBLEMS.filter(function (p) { return p.i === at; });
  if (mine.length) {
    EDITS.splice(at, 1);
    UNDONE.length = 0;
    rebuild();
    return { ok: false, problems: mine };
  }
  return { ok: true, problems: PROBLEMS };
}

function undo() { if (EDITS.length) { UNDONE.push(EDITS.pop()); rebuild(); } }
function redo() { if (UNDONE.length) { EDITS.push(UNDONE.pop()); rebuild(); } }

// During a drag: write the dragged node's marks and its incident segment endpoints
// directly, without recomputing the layout.  Incident DC pads are hidden -- retiling them
// is the expensive part and a rail without pads reads perfectly well for the ~300 ms of a
// drag.
function liveMove(nid, x, y) {
  var n = nodeById[nid];
  if (!n) return;
  n.x = x; n.y = y;
  var nx = px(n), ny = py(n), rec = NODEEL[nid];
  if (rec && rec.kind === 'junction') {
    rec.el.setAttribute('x', nx - L.r_junc); rec.el.setAttribute('y', ny - L.r_junc);
  } else if (rec) {
    rec.grp.setAttribute('transform', 'rotate(' + rec.ang + ' ' + nx + ' ' + ny + ')');
    rec.el.setAttribute('x', nx - rec.len / 2); rec.el.setAttribute('y', ny - L.site_t / 2);
  }
  for (var i = 0; i < A.segments.length; i++) {
    var sg = A.segments[i];
    if (sg.a !== nid && sg.b !== nid) continue;
    var a = nodeById[sg.a], b = nodeById[sg.b], ln = SEGEL[sg.id];
    if (!a || !b || !ln) continue;
    var ax = px(a), ay = py(a), bx = px(b), by = py(b);
    if (ln.tagName === 'path') ln.setAttribute('d', 'M ' + ax + ' ' + ay + ' L ' + bx + ' ' + by);
    else { ln.setAttribute('x1', ax); ln.setAttribute('y1', ay);
           ln.setAttribute('x2', bx); ln.setAttribute('y2', by); }
    var pads = PAD_BY_SEG[sg.id];
    if (pads) for (var k = 0; k < pads.length; k++) pads[k].el.style.display = 'none';
  }
}

function breaksAxisAlignment(nid, x, y) {
  for (var i = 0; i < A.segments.length; i++) {
    var sg = A.segments[i];
    if (sg.a !== nid && sg.b !== nid) continue;
    var other = nodeById[sg.a === nid ? sg.b : sg.a];
    if (!other) continue;
    if (Math.abs(other.x - x) > 1e-9 && Math.abs(other.y - y) > 1e-9) return true;
  }
  return false;
}

// A drag does NOT implicitly call `set_segment_length`: `move_site` deliberately leaves
// declared lengths alone, because length is an independently declared property that only
// `length_scaling` models read.  So the page SAYS the geometry and the declaration now
// disagree, and offers to reconcile, instead of quietly changing a number the user did not
// ask to change.
function declaredMismatch(nid, x, y) {
  var worst = 0, which = null, count = 0;
  for (var i = 0; i < A.segments.length; i++) {
    var sg = A.segments[i];
    if (sg.a !== nid && sg.b !== nid) continue;
    var other = nodeById[sg.a === nid ? sg.b : sg.a];
    if (!other) continue;
    var drawn = Math.sqrt((other.x - x) * (other.x - x) + (other.y - y) * (other.y - y));
    var decl = sg.len === undefined ? 1.0 : sg.len;
    if (decl <= 0) continue;
    var ratio = drawn / decl;
    if (Math.abs(ratio - 1) > 0.2) { count++; if (Math.abs(ratio - 1) > worst) { worst = Math.abs(ratio - 1); which = [sg.id, ratio, decl]; } }
  }
  if (!which) return null;
  var scaling = !!(D.model && D.model.length_scaling);
  return count + ' incident segment(s) are now up to ' + which[1].toFixed(2) +
         'x their declared length ' + which[2] + '. ' +
         (scaling ? 'This model DOES scale cost by length, so the price is now wrong until '
                  + 'you reconcile them.'
                  : 'This model ignores segment length, so this changes the drawing, not '
                  + 'the cost.');
}

// Reconcile: append `set_segment_length` ops with the SAME meta.group as the move, so the
// whole thing is one undo.
function reconcileLengths(nid) {
  var group = 'g' + GROUP, n = nodeById[nid];
  if (!n) return 0;
  var made = 0;
  for (var i = 0; i < A.segments.length; i++) {
    var sg = A.segments[i];
    if (sg.a !== nid && sg.b !== nid) continue;
    var other = nodeById[sg.a === nid ? sg.b : sg.a];
    if (!other) continue;
    var drawn = Math.sqrt((other.x - n.x) * (other.x - n.x) + (other.y - n.y) * (other.y - n.y));
    if (!(drawn > 0)) continue;
    EDITS.push({ method: 'set_segment_length',
                 args: [sg.id, Q.pyFloat(Math.round(drawn * 100) / 100)],
                 kwargs: {}, meta: { group: group, src: 'reconcile' } });
    made++;
  }
  if (made) { UNDONE.length = 0; rebuild(); }
  return made;
}

// ------------------------------------------------------------------- topology gestures
//
// These are the ones that were BLOCKED until `Machine.add_site` / `add_junction` /
// `add_segment` / `remove_node` / `remove_segment` existed in Python.  A mouse gesture
// that emitted an op Python could not replay would break the round-trip guarantee the
// whole listing rests on, so they route through `qccd/viz/js/edit.js`, whose Python twin
// is `qccd/arch/edit.py` and whose parity is proved edit by edit.
function freshId(prefix) {
  for (var k = 0; ; k++) {
    var id = prefix + k;
    if (!has(STATE.device.nodes, id) && !has(STATE.device.segments, id)) return id;
  }
}

function addSite(x, y, near) {
  var proto = near ? nodeById[near] : null;
  var op = { topology: { op: 'add_site', args: {
    id: freshId('N'), pos: [x, y], zone: proto ? proto.zone : null,
    capacity: proto ? proto.cap : 1, labels: ['added'],
    zone_types: STATE.zone_types } },
    meta: { group: 'g' + (++GROUP), src: 'stage' } };
  return tryTopology(op);
}

function addSegment(a, b) {
  if (a === b) return { ok: false, problems: [{ code: 'self_loop', targets: [a],
    message: 'a segment must join two different nodes' }] };
  for (var sid in STATE.device.segments) if (has(STATE.device.segments, sid)) {
    var s = STATE.device.segments[sid];
    if ((s.a === a && s.b === b) || (s.a === b && s.b === a)) {
      return { ok: false, problems: [{ code: 'duplicate_segment', targets: [a, b],
        message: a + '-' + b + ' are already joined by segment ' + sid }] };
    }
  }
  var na = nodeById[a], nb = nodeById[b];
  // Check BEFORE dereferencing. Reading `.x` off an absent node threw an uncaught
  // TypeError out of a function whose whole contract is to return {ok:false, problems},
  // and the mirror already has the right message for it.
  var missing = [];
  if (!na) missing.push(a);
  if (!nb) missing.push(b);
  if (missing.length) {
    return { ok: false, problems: missing.map(function (id) {
      return { code: 'no_such_node', targets: [id], message: 'no such node ' + id };
    }) };
  }
  var len = Math.sqrt((na.x - nb.x) * (na.x - nb.x) + (na.y - nb.y) * (na.y - nb.y));
  // The one place geometry SHOULD set length, because there is no prior declaration to
  // contradict.  A chord stays `loop: null` -- `corner_endpoints` scores a segment by the
  // corners of the loop it lies on, so a chord joining two corners would be charged a whole
  // turn it does not contain.
  var op = { topology: { op: 'add_segment', args: {
    id: freshId('X'), a: a, b: b, labels: ['chord'],
    length: Math.round(len * 100) / 100 || 1.0, capacity: 1 } },
    meta: { group: 'g' + (++GROUP), src: 'stage' } };
  return tryTopology(op);
}

function removeSelected() {
  if (!SELSET.length) return { ok: false, problems: [] };
  var out = null;
  for (var i = 0; i < SELSET.length; i++) {
    var sel = SELSET[i];
    // A LOOP IS SELECTABLE NOW, so Delete on one is reachable for the first time -- and
    // `edit.js` has no `remove_loop` op, so it would have issued `remove_node` with a
    // loop id and leaked the mirror's bare `topology` refusal.  Name it instead.
    if (sel.kind === 'loop') {
      return { ok: false, problems: [{ code: 'no_remove_loop', targets: [sel.id],
        message: sel.id + ' is a transport loop; there is no delete verb for one yet -- ' +
                 'delete one of its segments to open it, or edit the loop in the text lane.' }] };
    }
    var op = sel.kind === 'segment'
      ? { topology: { op: 'remove_segment', args: { id: sel.id, on_loop: 'open' } },
          meta: { group: 'g' + (++GROUP), src: 'stage' } }
      : { topology: { op: 'remove_node', args: { id: sel.id, mend: 'splice', cascade: true } },
          meta: { group: 'g' + (++GROUP), src: 'stage' } };
    out = tryTopology(op);
    if (!out.ok) return out;
  }
  SELSET = [];
  return out || { ok: false, problems: [] };
}

// Try it on a COPY first, so a refusal leaves the last good architecture on the stage
// rather than a half-applied one, and so the refusal message is the mirror's own -- which
// the parity test has already diffed character for character against Python's.
function tryTopology(op) {
  try {
    E.applyEdit(STATE.device, op.topology);
  } catch (err) {
    return { ok: false, problems: [{ code: 'topology', targets: [], message: err.message }] };
  }
  EDITS.push(op);
  UNDONE.length = 0;
  rebuild();
  return { ok: true, problems: PROBLEMS };
}

// ------------------------------------------------------------------- the text lane
//
// The side editor's language is a strict subset of the Python `architecture_listing`
// already emits, so what the user types is exactly what `m.source()` prints and exactly
// what `rebuild()` execs.  Copy the panel, paste into a .py, run it: it works.  Neither a
// form nor a JSON editor can say that.
//
// It writes into the SAME `EDITS` array through the same applier, so the mouse lane and
// the text lane can never disagree -- there is only one applier.
// The SHIPPED text for every statement the user has not touched, and rendered text for
// the ones they have.
//
// This is not laziness -- it is the only correct answer.  JSON has ONE number type, so a
// `call` record that reached the browser as JSON cannot tell `us=5.0` (a float, which is
// what `CurvePoint.us` is) from `us=5` (an int, which is what `capacity` is).  Re-rendering
// from the record therefore emits `dict(us=5, ...)` where Python emits `dict(us=5.0, ...)`,
// and the round trip reports a phantom edit on a line nobody touched.
//
// `ArchLine.text` is the exact Python Python itself emitted, and it ships already, so an
// untouched statement is quoted rather than reconstructed.  Statements the user typed keep
// their float-ness for free: the parser boxes any literal written with a `.` or an
// exponent as a `PyFloat`, which is why `render(parse(x)) === x` holds byte for byte on
// all nine shipped listings (`tests/test_engine_parity.py`, the `sources` corpus).
function sourceText() {
  // `baseTexts()` follows `baseCalls()`, which already carries the hoisted `{build: ...}`
  // edits in their statement POSITION -- so a node the user placed on a blank canvas
  // appears as `d.site("N0", 0.0, 0.0, zone="data")` between the `DeviceBuilder` and the
  // seal, exactly where Python's own explicit listing would put it.
  var rows = baseTexts();
  var edits = EDITS.filter(function (o) { return !o.topology && !o.build; });
  for (var j = 0; j < edits.length; j++) rows.push(Q.render(edits[j]));
  return rows.join('\n') + '\n';
}

// Apply typed source: parse, diff against BASE, and keep only the statements the user
// actually changed or added as EDITS.  Replacing BASE wholesale would lose the ability to
// say what was edited, and an append-only log would grow without bound.
function applySource(src) {
  var p = Q.parse(src);
  if (p.errors.length) return { ok: false, errors: p.errors };
  // A PROGRAMME STATEMENT IN THE ARCHITECTURE PANE is refused by message, never applied
  // and never dropped.  `applyProgram` would refuse it anyway -- `'init' is not an
  // editable method` -- but that sentence blames the verb rather than the pane, and the
  // user's next question would be "why not?".
  if (p.prog.length || p.progSeed) {
    var first = p.prog[0] || p.progSeed;
    return { ok: false, errors: [{ line: first.line, col: 1, text: first.text,
      message: 'this is a programme statement; it belongs in the Write pane. The ' +
               'architecture pane takes `m.` and `d.` verbs.' }] };
  }
  // Compare against the TEXT the editor showed, not against a re-rendering of the
  // record: `sourceText` quotes Python's own text for untouched statements, so anything
  // else here would make every line with an integral float read as edited.
  var base = baseTexts();
  var next = [];
  for (var i = 0; i < p.stmts.length; i++) {
    var s = p.stmts[i];
    if (i < base.length && Q.render(s) === base[i]) continue;             // unchanged
    if (i < base.length && s.text !== undefined && s.text === base[i]) continue;
    next.push({ method: s.method, args: s.args, kwargs: s.kwargs,
                meta: { group: 'text', src: 'text' } });
  }
  // A typed program is authoritative for the whole command list, so it REPLACES the
  // command edits; topology edits are geometry and survive.
  var topo = EDITS.filter(function (o) { return !!o.topology; });
  EDITS = topo.concat(next);
  UNDONE.length = 0;
  rebuild();
  // `ok` reports whether the SOURCE WAS APPLIED, and a statement `rebuild()` refused was
  // not applied. Returning ok:true beside a non-empty `problems` said "your text went in"
  // about text that had been dropped -- which is the same class of lie as pricing a
  // programme that does not fit. The caller still gets `problems` either way.
  return { ok: PROBLEMS.length === 0, errors: [], problems: PROBLEMS };
}

// ------------------------------------------------------------------- export
//
// Two artifacts, both produced from the same statement list, both accepted by the real
// toolchain.  Neither is sent anywhere: the network primitives are on `render.py`'s
// FORBIDDEN list and the self-containment test asserts on exactly that, so the temptation
// to POST an edit to a local helper is already a build failure -- which is right, because
// the user rejected a local server.  The text is handed over through the clipboard and
// through a visible textarea, both of which work from a file:// page.
function exportPython() {
  var bad = refuseExport('this listing');
  if (bad) throw bad;
  // `build` edits are hoisted into `baseTexts()` at their statement position already
  var ops = EDITS.filter(function (o) { return !o.build; });
  var anyTopo = ops.some(function (o) { return !!o.topology; });
  var head = '# edited in the browser, from ' + A.name + '\n' +
             '# ' + EDITS.length + ' edit(s) on top of the shipped architecture\n' +
             'from qccd import Machine\n' + (anyTopo ? 'import json\n' : '') + '\n';
  var rows = baseTexts();

  // IN EDIT ORDER.  Partitioning -- every command edit, then every topology edit --
  // wrote them in an order the user never chose, so a statement naming an object that a
  // topology edit had CREATED ran before that object existed and the exported file
  // raised.  Two clicks reached it: delete a node, then drag a neighbour and press L,
  // which emits `set_segment_length` on the bridge `remove_node(mend='splice')` mints
  // -> `ValueError: no such segment 'E4.E5'`, with the browser reporting no problem.
  // `replay()` and `Machine.apply_edits` both honour EDITS order; now so does this.
  //
  // A topology edit is NOT a listing statement.  It routes through
  // `qccd.arch.edit.apply_edit`, the method whitelist whose JS mirror the parity test
  // diffs edit by edit.  Emitting these as text would need an emitter the browser has no
  // oracle for, so it emits the DATA and lets Python's own whitelist execute it -- and
  // consecutive ones batch into a single call.  Nothing here is exec'd.
  var i = 0;
  while (i < ops.length) {
    if (ops[i].topology) {
      var run = [];
      while (i < ops.length && ops[i].topology) run.push(ops[i++]);
      rows.push('# topology edit(s), replayed through the method whitelist (never exec)');
      rows.push("m.apply_edits(json.loads(r'''" + JSON.stringify(run) + "'''))");
    } else {
      rows.push(Q.render(ops[i++]));
    }
  }
  return head + rows.join('\n') + '\n';
}

// The list of reasons the CURRENT state could not be written to an `.arch.json`, in the
// schema's own words.  Empty for every clean state, which is all of them until the user
// types something the file format cannot hold.
function schemaErrors() {
  if (!STATE) return [];
  // THE SCHEMA IS NOT THE WHOLE LOADER.  `Architecture.from_json` runs `check(doc)` AND
  // `Device.check_structure`, and the second is where "a site needs capacity >= 1" lives.
  // Measured: `Machine.blank("ring", width=12)` exported an `.arch.json` with
  // `schemaErrors() === []` that Python refused with 24 structural errors -- while the
  // page reported a confident 576 DACs for a machine whose total ion capacity was ZERO.
  // The mirror already existed (`QCCDEdit.checkStructure`, with its own parity test) and
  // was called from `applyEdit` and nowhere else.
  var out;
  try { out = Q.validateDocument(Q.serialize(STATE)); }
  catch (err) { return [err.message]; }
  try { out = out.concat(E.checkStructure(STATE.device)); }
  catch (err) { out = out.concat([err.message]); }
  // and the one thing NEITHER of them checks: a site whose zone type is not declared.
  // `Architecture.can` RAISES there, so R6 cannot run at all -- "0 violations" for a
  // check that never executed is exactly the claim this tool must not make.
  try {
    var used = Q.zonesInUse(STATE.device), have = STATE.zone_types || {};
    for (var i = 0; i < used.length; i++) {
      if (!has(have, used[i])) {
        out.push("node zone type '" + used[i] + "' is used by a site but not declared " +
                 '(have: ' + (Object.keys(have).sort().join(', ') || 'none') + ')');
      }
    }
  } catch (err) { /* a device-less state has no zones to check */ }
  return out;
}

// THE EXPORT BOUNDARY REFUSES.  Not the edit -- the export.
//
// The two lanes need different answers to the same bad statement, and both answers are
// right.  `emit()` is a single committed gesture, so it rolls the statement back and the
// user never leaves a good state.  The TEXT lane is a program the user is in the middle
// of writing: refusing to load sixty lines because line forty-three names a table that
// does not exist is what a compiler does, not what an editor does, so the bad line stays,
// carrying its problem, exactly the way a squiggle stays under a typo.
//
// What must NOT differ between the lanes is what LEAVES the page.  A design tool may let
// you hold a broken state; it may not hand you a broken artifact and call it an export.
// So both exports refuse, with the schema's own message and the statement to fix -- which
// is strictly more useful than a file that fails hours later inside somebody else's
// loader, which is what this page used to produce.
function refuseExport(what) {
  var errs = schemaErrors();
  if (!errs.length) return null;
  var mine = PROBLEMS.filter(function (p) { return p.code === 'schema' && p.i !== null; });
  var where = mine.length ? '\n  statement ' + (mine[0].i + 1) + ' of the edits is the one to fix.' : '';
  return new Error(
    'refusing to export ' + what + ': the toolchain would not load it.\n  ' +
    errs.slice(0, 6).join('\n  ') +
    (errs.length > 6 ? '\n  ... and ' + (errs.length - 6) + ' more' : '') + where);
}

function exportJson() {
  if (!STATE) return '{}';
  var bad = refuseExport('this architecture');
  if (bad) throw bad;
  return JSON.stringify(Q.serialize(STATE), null, 2);
}

function exportEdits() { return JSON.stringify(EDITS, null, 1); }

// ------------------------------------------------------------------- chrome
var EL = {};
function $(id) { return document.getElementById(id); }

function paint() {
  if (!EL.bar) return;
  EL.count.textContent = EDITS.length + (EDITS.length === 1 ? ' edit' : ' edits');
  var nprob = PROBLEMS.length + LINTS.length;
  EL.prob.textContent = nprob + (nprob === 1 ? ' problem' : ' problems');
  EL.undo.disabled = !EDITS.length;
  EL.redo.disabled = !UNDONE.length;
  EL.bar.className = 'ebar' + (MODE === 'edit' ? ' edit' : '');
  EL.mPlay.className = MODE === 'play' ? 'on' : '';
  EL.mEdit.className = MODE === 'edit' ? 'on' : '';
  EL.snap.setAttribute('aria-pressed', SNAP ? 'true' : 'false');
  EL.snap.className = 'tgl' + (SNAP ? ' on' : '');
  EL.price.textContent = priceLine();
  if (EL.src && document.activeElement !== EL.src) EL.src.value = sourceText();
  if (EL.out) {
    // A refused export is shown IN the box, not swallowed: the box is where the user
    // looks for the file, so it is where the reason there is no file belongs.  Letting
    // the throw escape would abort the rest of `paint()` and leave the whole bar stale.
    try {
      EL.out.value = EL.outWhich === 'json' ? exportJson()
                   : EL.outWhich === 'edits' ? exportEdits()
                   : EL.outWhich === 'tsir' ? JSON.stringify(framesAsTsir(STATE.name), null, 1)
                   : exportPython();
    } catch (err) {
      EL.out.value = String(err && err.message ? err.message : err);
    }
  }
  renderStart();
  renderPalette();
  renderInspector();
  renderWrite();
  renderReport();
  paintOverlay();
}

// ONE sentence per break, said by BOTH surfaces.  The price line and the stage banner
// used to be able to describe the same break differently -- the stage said nothing at all
// -- so this is extracted rather than copied.
function breakMessage(b) {
  if (!b) return 'the programme does not fit this device';
  return b.kind === 'unknown_node' ? 'this edit removed ' + b.node + ', which the programme places an ion on'
       : b.kind === 'no_segment' ? 'the programme routes ' + b.src + ' to ' + b.dst + ', which no segment now joins'
       : b.kind === 'missing_loop' ? 'the programme shifts loop ' + b.loop + ', which this edit removed'
       : b.kind === 'loop_broken' ? 'loop ' + b.loop + ' is no longer a closed ring -- nothing joins '
                                    + b.src + ' to ' + b.dst + ', and the programme shuttles ions across it'
       : b.kind === 'loop_resized' ? 'loop ' + b.loop + ' now has ' + b.now + ' nodes, not ' + b.was +
                                     '; a rigid rotation of a different-length loop is a different programme'
       : b.kind === 'unknown_class' ? 'the programme uses movement class ' + b.cls + ', which this edit removed'
       : b.kind === 'declared_elsewhere' ? 'ion ' + b.ion + ' is declared to move from ' + b.src +
                                           ' but is at ' + b.dst + '; the replay stops there, so ' +
                                           'no number after this statement would be computed'
       : (b.message || b.kind);
}

// The price line is the honest one.  It never shows a number it cannot stand behind: a
// geometry edit invalidates the compiled programme, and the page says so instead of
// animating a programme whose node ids may no longer exist.
function priceLine() {
  if (!READY) return WHY_NOT || 'editing unavailable';
  if (!PRICE) return '';
  if (PRICE.blocked) {
    return 'price unavailable · ' + breakMessage(PRICE.blocked[0]) +
           ' · recompile in Python';
  }
  var t = PRICE.totals;
  var same = !EDITS.length && !PROG.length;
  var head = 'cost ' + fmt(t.cost) + ' · steps ' + fmt(t.steps) +
             ' · runtime ' + fmt(t.us / 1000, 2) + ' ms · n̄ ' +
             fmt(PRICE.comp.shuttle + PRICE.comp.junction + PRICE.comp.split_merge, 1);
  if (HW) head += ' · ' + fmt(HW.dacs) + ' DACs';
  if (HW0 && HW && HW.dacs !== HW0.dacs) head += ' (' + (HW.dacs > HW0.dacs ? '+' : '') + fmt(HW.dacs - HW0.dacs) + ')';
  if (same) return head + ' · unedited';
  // THE FIFTH STATE.  The per-frame self-check compares each re-priced frame against the
  // cost PYTHON shipped for it; an AUTHORED programme has no such frames, so there is
  // nothing to compare and reporting `frameDrift === 0` over `frameChecked === 0` would be
  // a confident zero for a check that never ran.  The arithmetic is parity-tested; THIS
  // PROGRAMME is not, and the difference is the whole point of saying so.
  if (PRICE_STATUS === 'unoracled' || !PRICE.frameChecked) {
    return head + ' · no per-frame oracle: these frames were never priced by Python. ' +
           'Download the pair and run: python -m qccd run ' + A.name +
           '.arch.json --tsir ' + A.name + '.tsir.json';
  }
  if (PRICE.frameDrift === 0) {
    return head + ' · re-priced client-side; every one of ' + fmt(PRICE.frameChecked) +
           ' frames still agrees with the Python verifier';
  }
  if (!EDITS.some(function (o) { return priceAffecting(o); })) {
    return head + ' · price unchanged: this model ignores the geometry you changed';
  }
  return head + ' · re-priced client-side · re-verify in Python: ' +
         'python -m qccd run ' + A.name + ' --program ' + P.name;
}
function priceAffecting(op) {
  if (op.topology) return true;
  // `move_site` belongs here. Geometry decides `corner_endpoints`, and a corner segment
  // costs three hops under the deck model where a straight one costs one -- so dragging a
  // site into a bend moved the shipped ring's cost by 16,032 while the page printed
  // "price unchanged: this model ignores the geometry you changed" on the same line.
  // `set_site_capacity` belongs here too: it changes total capacity and can push a site
  // below its own occupancy.
  return ['set_curve', 'set_degree_curve', 'set_primitive', 'set_heating',
          'set_segment_length', 'move_site', 'set_site_capacity',
          'set_zone'].indexOf(op.method) >= 0;
}

// ------------------------------------------------------------------- the overlay layer
//
// A dedicated group appended LAST, above everything structural.  It never contains ions,
// so `census.mjs`'s ion probe is untouched by anything drawn here.  Everything is POOLED
// and hidden, never removed -- the shim's `remove()` is a no-op that leaves the child in
// `parent.children`.
var gEdit = null, EHOVER = null, EGHOST = null, EGUIDE = [], EBAND = null, ESEL = [];
function initOverlay() {
  gEdit = el('g', {});
  svg.append(gEdit);
  // kept only so the ghost/band/guide trio still has a sibling to hide alongside; the
  // hover highlight itself is an OUTLINE of the drawn shape, drawn from the pool
  EHOVER = el('circle', { r: 1, fill: 'none', stroke: C.accent, 'stroke-width': 1.5,
                          opacity: 0.9, 'pointer-events': 'none' });
  EHOVER.style.display = 'none'; gEdit.append(EHOVER);
  EGHOST = el('circle', { r: 1, fill: 'none', stroke: C.muted, 'stroke-width': 1.2,
                          'stroke-dasharray': '4 3', opacity: 0.5, 'pointer-events': 'none' });
  EGHOST.style.display = 'none'; gEdit.append(EGHOST);
  EBAND = el('line', { stroke: C.accent, 'stroke-width': 1.6, 'stroke-dasharray': '5 4',
                       opacity: 0.85, 'pointer-events': 'none' });
  EBAND.style.display = 'none'; gEdit.append(EBAND);
  for (var i = 0; i < 2; i++) {
    var g = el('line', { stroke: C.accent, 'stroke-width': 1, opacity: 0.45,
                         'pointer-events': 'none' });
    g.style.display = 'none'; gEdit.append(g); EGUIDE.push(g);
  }
}
// A POOL PER TAG, hidden rather than removed: `remove()` is a no-op in the harness and
// leaves the child in `parent.children`, so anything that removed-and-recreated would
// grow the overlay without bound and be invisible to a test at the same time.
var OPOOL = {};
function poolTake(tag) {
  var arr = OPOOL[tag] || (OPOOL[tag] = []);
  for (var i = 0; i < arr.length; i++) if (arr[i]._free) { arr[i]._free = false; return arr[i]; }
  var e = el(tag, { fill: 'none', 'pointer-events': 'none' });
  e._free = false; arr.push(e); gEdit.append(e);
  return e;
}
function poolReset() {
  for (var t in OPOOL) if (has(OPOOL, t)) {
    for (var i = 0; i < OPOOL[t].length; i++) {
      OPOOL[t][i]._free = true; OPOOL[t][i].style.display = 'none';
    }
  }
}
// stale geometry from a previous use must not survive: a `rect` reused as a `rect` for a
// junction would otherwise keep the capsule's rotate() and sit crooked
var OATTRS = ['x', 'y', 'width', 'height', 'rx', 'transform', 'points', 'd',
              'x1', 'y1', 'x2', 'y2', 'cx', 'cy', 'r'];
function drawOutline(o, style) {
  if (!o || !gEdit) return null;
  var e = poolTake(o.tag), k, i;
  for (i = 0; i < OATTRS.length; i++) e.removeAttribute(OATTRS[i]);
  for (k in o) if (has(o, k) && k !== 'tag') e.setAttribute(k, o[k]);
  for (k in (style || {})) if (has(style, k)) e.setAttribute(k, style[k]);
  e.setAttribute('fill', 'none');
  e.setAttribute('pointer-events', 'none');
  e.style.display = '';
  return e;
}

// EVERY HIGHLIGHT IS THE OUTLINE OF WHAT WAS DRAWN.  This used to paint a circle of
// 0.55*g at a node's centre whatever shape the node was, and to paint NOTHING AT ALL for
// a segment (`nodeById[s.id]` is never a segment id) -- so you could select a segment and
// have no way to see that you had.  A loop had neither a hit target nor a highlight.
function paintOverlay() {
  poolReset();
  if (!gEdit) return;
  if (MODE !== 'edit') {
    EHOVER.style.display = 'none'; EGHOST.style.display = 'none';
    EBAND.style.display = 'none';
    EGUIDE[0].style.display = 'none'; EGUIDE[1].style.display = 'none';
    return;
  }
  var i, sw = Math.max(1.5, 0.07 * L.g);
  for (i = 0; i < SELSET.length; i++) {
    drawOutline(outlineOf(SELSET[i].kind, SELSET[i].id),
      { stroke: C.accent, 'stroke-opacity': 0.95, 'stroke-width': sw,
        'stroke-linejoin': 'round', 'stroke-linecap': 'round' });
  }
  if (HOVERED && !(SELSET.length === 1 && SELSET[0].id === HOVERED.id &&
                   SELSET[0].kind === HOVERED.kind)) {
    drawOutline(outlineOf(HOVERED.kind, HOVERED.id),
      { stroke: C.accent, 'stroke-opacity': 0.45, 'stroke-width': Math.max(1.2, 0.035 * L.g),
        'stroke-linejoin': 'round', 'stroke-linecap': 'round' });
  }
  if (MARQ) {
    drawOutline({ tag: 'rect', x: Math.min(MARQ.x0, MARQ.x1), y: Math.min(MARQ.y0, MARQ.y1),
                  width: Math.abs(MARQ.x1 - MARQ.x0), height: Math.abs(MARQ.y1 - MARQ.y0) },
      { stroke: C.accent, 'stroke-opacity': 0.8, 'stroke-width': Math.max(1, 0.02 * L.g),
        'stroke-dasharray': (0.09 * L.g) + ' ' + (0.07 * L.g) });
  }
}

// ------------------------------------------------------------------- mode
function setMode(m) {
  if (m === MODE) return;
  if (m === 'edit') {
    if (!READY) return;
    if (typeof stop === 'function') stop();
  }
  MODE = m;
  HOVERED = null;
  setCursor(null);
  if (typeof svg !== 'undefined' && svg && svg.setAttribute) svg.setAttribute('data-mode', m);
  // In edit mode the transport controls are disabled but the SLIDER stays live: scrubbing
  // is read-only and it is how you understand what you are about to edit.  And the FRAME
  // INDEX is kept -- re-running from 0 throws away the user's position, which is the single
  // most annoying thing an editing animator can do.
  var ids = ['play', 'step', 'glide', 'phase'];
  for (var i = 0; i < ids.length; i++) {
    var b = $(ids[i]);
    if (b) b.disabled = (MODE === 'edit');
  }
  // the mode lives on `data-mode` (written in setMode) and the cursor on `style`;
  // `classList` is a no-op in the harness, so anything routed through it is state no
  // test can read -- the same trade `data-layout` already made.
  if (svg.classList) svg.classList.toggle('editing', MODE === 'edit');
  paint();
}
function fmt(x, d) {
  return (x == null) ? '-' : Number(x).toLocaleString(undefined, { maximumFractionDigits: d || 0 });
}

// ------------------------------------------------------------------- toasts
//
// `textContent` only, never `innerHTML`: an id typed into the side editor is untrusted
// text and it ends up in these messages.
function toast(kind, message) {
  var host = $('toasts');
  if (!host) return;
  var t = document.createElement('div');
  t.className = 'toast' + (kind ? ' ' + kind : '');
  t.textContent = message;
  host.append(t);
  if (!SYNC) setTimeout(function () { t.style.display = 'none'; }, 4500);
}

// ------------------------------------------------------------------- wiring
//
// Every handler here is a thin adapter: client coordinates in, `EDITOR.*` out.  Nothing
// decides anything inside an event listener, because the harness cannot fire one.
function wire() {
  EL.bar = $('ebar');
  if (!EL.bar) return;
  EL.mPlay = $('mPlay'); EL.mEdit = $('mEdit'); EL.snap = $('tSnap');
  EL.undo = $('eUndo'); EL.redo = $('eRedo'); EL.count = $('eCount');
  EL.prob = $('eProb'); EL.price = $('ePrice'); EL.src = $('eSrc');
  EL.out = $('eOut'); EL.outWhich = 'py';

  EL.mPlay.onclick = function () { setMode('play'); };
  EL.mEdit.onclick = function () { setMode('edit'); };
  EL.snap.onclick = function () { SNAP = !SNAP; paint(); };
  EL.undo.onclick = function () { undo(); };
  EL.redo.onclick = function () { redo(); };
  EL.prob.onclick = function () {
    var rows = PROBLEMS.map(function (p) { return 'statement ' + p.i + ': ' + p.message; })
      .concat(LINTS.map(function (l) { return l.code + ': ' + l.message; }));
    toast(rows.length ? 'warn' : 'ok', rows.length ? rows.join('  ·  ') : 'no problems');
  };
  var pw = $('pwText');
  if (pw) {
    pw.oninput = function () { pw._touched = true; };
    pw.onchange = function () { pw._touched = true; applyProgramSource(pw.value); };
  }
  var run = $('pwRun');
  if (run) run.onclick = function () {
    var ta = $('pwText');
    var r = applyProgramSource(ta ? ta.value : '');
    if (!r.ok) toast('bad', (r.errors[0] || {}).message || 'the programme did not parse');
    else toast('ok', PROG.length + ' statements, ' + P.frames.length + ' frames');
  };
  var seg = $('eWhich');
  if (seg) seg.onchange = function () { EL.outWhich = seg.value; paint(); };
  var copy = $('eCopy');
  if (copy) copy.onclick = function () {
    // `navigator` is entirely undefined under the test shim, so the clipboard is
    // feature-detected INSIDE the handler.  The visible textarea is the always-works
    // path and it is not a fallback -- it is the primary affordance, because a sandboxed
    // viewer blocks a download a page starts itself.
    var text = EL.out ? EL.out.value : '';
    var nav = (typeof navigator !== 'undefined') ? navigator : null;
    if (nav && nav.clipboard && nav.clipboard.writeText) {
      nav.clipboard.writeText(text);
      toast('ok', 'copied ' + text.length + ' characters');
    } else if (EL.out && EL.out.select) {
      EL.out.select();
      toast('ok', 'selected: press ctrl+C to copy');
    }
  };
  if (EL.src) {
    EL.src.oninput = function () {
      if (SRCT) clearTimeout(SRCT);
      var run = function () {
        SRCT = null;
        var r = applySource(EL.src.value);
        var strip = $('eSrcErr');
        if (strip) {
          strip.textContent = r.ok ? ''
            : ('line ' + r.errors[0].line + ' col ' + r.errors[0].col + ': ' + r.errors[0].message);
        }
      };
      if (SYNC) run(); else SRCT = setTimeout(run, 220);
    };
  }

  // -- the stage ---------------------------------------------------------------------
  // Pan behaviour in PLAY mode is byte-for-byte unchanged, so no existing test can
  // regress: this handler returns immediately unless the editor is on.
  svg.addEventListener('pointerdown', function (e) {
    if (MODE !== 'edit') return;
    var m = toModel(e.clientX, e.clientY);
    // THE SAME ARBITER the page's pan handler asked, so the two can never disagree about
    // who owns this press.
    var who = claim(m.x, m.y, { button: e.button, space: SPACE, alt: e.altKey,
                                shift: e.shiftKey, ctrl: e.ctrlKey });
    if (who === 'pan') { DOWN = null; ARMED = null; return; }
    DOWN = { cx: e.clientX, cy: e.clientY, mx: m.x, my: m.y, hit: hit(m.x, m.y),
             shift: e.shiftKey, claim: who, id: e.pointerId };
    ARMED = null;
    // A drag that leaves the stage must keep arriving.  The page's pan handler used to
    // capture on every press and the editor rode along on that capture; now that pan
    // yields, the editor has to take it itself or a drag stops halfway to wherever it
    // was going.
    if (svg.setPointerCapture) { try { svg.setPointerCapture(e.pointerId); } catch (err) {} }
  });
  svg.addEventListener('pointermove', function (e) {
    if (MODE !== 'edit') return;
    var m = toModel(e.clientX, e.clientY);
    if (!DOWN) {
      hover(m.x, m.y);
      // the ghost IS the element, drawn by the stage's own code at stage scale
      if (ARMED_EL === 'site' || ARMED_EL === 'junction') {
        if (!PGHOST) ghostBegin(ARMED_EL, m.x, m.y); else ghostMove(m.x, m.y, {});
      } else if (PGHOST) ghostCancel();
      return;
    }
    // 4 px, not 3: the existing click threshold is 3, so one pixel of hysteresis means a
    // click can never become a drag.
    if (!ARMED && Math.sqrt((e.clientX - DOWN.cx) * (e.clientX - DOWN.cx) +
                            (e.clientY - DOWN.cy) * (e.clientY - DOWN.cy)) < 4) return;
    if (!ARMED) {
      // LEFT-DRAG ON AN ELEMENT MOVES IT; left-drag on empty stage marquee-selects.  Pan
      // is space+drag, middle-drag or right-drag -- the Figma/Illustrator convention, and
      // it needs no teaching.  Every one of the four kinds is draggable now, because
      // `begin` expands a press into the nodes it actually moves.
      ARMED = (DOWN.claim === 'marquee') ? 'marquee'
            : ((DOWN.shift || ARMED_EL === 'segment') && DOWN.hit.kind !== 'segment' &&
               DOWN.hit.kind !== 'loop') ? 'band'
            : 'node';
      if (ARMED === 'node') begin(DOWN.hit.kind, DOWN.hit.id, DOWN.mx, DOWN.my);
      if (ARMED === 'band') { BAND = DOWN.hit.id; }
      if (ARMED === 'marquee') marqueeBegin(DOWN.mx, DOWN.my);
      setCursor(DOWN.hit);
    }
    if (ARMED === 'node') {
      var r = move(m.x, m.y, { free: e.altKey, fine: e.shiftKey });
      showHud(e.clientX, e.clientY, r);
    } else if (ARMED === 'marquee') {
      marqueeMove(m.x, m.y);
    } else if (ARMED === 'band') {
      var n = nodeById[BAND];
      EBAND.style.display = '';
      EBAND.setAttribute('x1', px(n)); EBAND.setAttribute('y1', py(n));
      EBAND.setAttribute('x2', m.x); EBAND.setAttribute('y2', m.y);
    }
  });
  var end = function (e) {
    if (MODE !== 'edit') { DOWN = null; ARMED = null; return; }
    var m = toModel(e.clientX, e.clientY);
    var wasArmed = ARMED, down = DOWN;
    DOWN = null; ARMED = null;
    if (down && svg.releasePointerCapture) {
      try { svg.releasePointerCapture(e.pointerId); } catch (err) {}
    }
    hideHud();
    if (wasArmed === 'node') {
      var r = drop();
      if (r && r.problems.length) {
        toast('bad', r.problems[0].message);
      } else if (r) {
        var d = declaredMismatch(r.op.args[0], +Q.unbox(r.op.args[1]), +Q.unbox(r.op.args[2]));
        if (d) toast('warn', d + '  (press L to set the lengths to match)');
        LASTMOVED = r.op.args[0];
      }
      return;
    }
    if (wasArmed === 'marquee') {
      marqueeDrop({ additive: e.shiftKey });
      setCursor(hit(m.x, m.y));
      return;
    }
    if (wasArmed === 'band') {
      EBAND.style.display = 'none';
      var target = hit(m.x, m.y);
      if (target && target.kind !== 'segment' && target.kind !== 'loop' && target.id !== BAND) {
        var res = joinNodes(BAND, target.id);
        if (!res.ok && (res.problems[0] || {}).code === 'no_builder') res = addSegment(BAND, target.id);
        if (!res.ok) toast('bad', (res.problems[0] || {}).message || 'refused');
      } else if (!target) {
        toast('warn', 'a segment joins two nodes -- drop it on a second one');
      }
      BAND = null;
      return;
    }
    if (!wasArmed && down &&
        Math.sqrt((e.clientX - down.cx) * (e.clientX - down.cx) +
                  (e.clientY - down.cy) * (e.clientY - down.cy)) < 4) {
      var h = down.hit;
      // AN ARMED STAMP PLACES ON A PLAIN CLICK.  Double-click still works and is still in
      // the help table, but requiring it was most of "I cannot flexibly add anything":
      // you arm an element, click where you want it, and it is there.
      if (!h && (ARMED_EL === 'site' || ARMED_EL === 'junction')) {
        var sp = snapTo((down.mx - L.ox) / (L.sx || 1), (down.my - L.oy) / (L.sy || 1),
                        e.altKey, e.shiftKey);
        var pr = placeStamp(ARMED_EL, sp.x, sp.y);
        if (!pr.ok) toast('bad', (pr.problems[0] || {}).message || 'refused');
        return;
      }
      if (!h) setSelection([]);
      else if (e.shiftKey) {
        // shift-click TOGGLES, which is what every other editor does and what makes a
        // marquee correctable without starting over
        var was = false, keep = [];
        for (var si = 0; si < SELSET.length; si++) {
          if (SELSET[si].id === h.id && SELSET[si].kind === h.kind) was = true;
          else keep.push(SELSET[si]);
        }
        setSelection(was ? keep : SELSET.concat([{ kind: h.kind, id: h.id }]));
      } else setSelection([{ kind: h.kind, id: h.id }]);
      // extend the EXISTING selection bus rather than building a second one
      if (h && typeof selectRef === 'function') {
        selectRef(h.kind === 'segment' ? 'segment' : 'site', h.id);
      }
    }
  };
  svg.addEventListener('pointerup', end);
  svg.addEventListener('pointercancel', end);
  svg.addEventListener('dblclick', function (e) {
    if (MODE !== 'edit') return;
    var m = toModel(e.clientX, e.clientY);
    if (hit(m.x, m.y)) return;
    var x = (m.x - L.ox) / (L.sx || 1), y = (m.y - L.oy) / (L.sy || 1);
    var s = snapTo(x, y, false, false);
    // WITH A PALETTE ELEMENT ARMED this places THAT element through the builder verbs, so
    // the same double-click is the from-scratch gesture on a blank canvas and the
    // add-a-node-to-an-existing-device gesture otherwise.  Without one it falls back to
    // `add_site`, which copies zone and capacity from the nearest node -- the right
    // default when there IS a nearest node and meaningless when there is not.
    var res;
    // THE TWO STAMPS THAT ARE NOT PLACED BY A POINT say so instead of quietly placing a
    // site.  Arming `segment` or `loop` and double-clicking used to fall through to
    // `addSite`, so the menu looked like it worked and produced the wrong element.
    if (ARMED_EL === 'segment') {
      toast('warn', 'a segment joins two nodes: drag from one node to another');
      return;
    }
    if (ARMED_EL === 'loop') {
      toast('warn', 'a loop is a walk over nodes that already exist: select them in ' +
                    'orbit order, then press Close loop');
      return;
    }
    if (String(ARMED_EL).slice(0, 4) === 'cmp:') {
      res = stampComponent(String(ARMED_EL).slice(4), s.x, s.y, 0);
      if (res.ok !== false) {
        toast('ok', 'placed ' + String(ARMED_EL).slice(4));
        arm(null);
      }
    } else if (ARMED_EL === 'site' || ARMED_EL === 'junction') {
      res = addNodeAt(s.x, s.y, { kind: ARMED_EL, zone: ARMED_EL === 'site'
        ? (nearestZone() || Object.keys(STATE.zone_types).sort()[0]) : undefined });
    } else if (!nodesOf(STATE).length) {
      // an EMPTY canvas has no nearest node to copy from, so `add_site` cannot work at
      // all; the builder verb is the only gesture that can start a device
      res = addNodeAt(s.x, s.y, { kind: 'site',
                                  zone: Object.keys(STATE.zone_types).sort()[0] });
    } else {
      res = addSite(s.x, s.y, nearestId(m.x, m.y));
    }
    if (!res.ok) toast('bad', (res.problems[0] || {}).message || 'refused');
  });

  // -- keys ---------------------------------------------------------------------------
  // The existing handler early-returns on ctrl/meta/alt, and that guard is what keeps
  // browser shortcuts working, so undo/redo are checked BEFORE it rather than by relaxing
  // it.  This listener runs first because it is registered later on the same target only
  // for the combos it owns.
  document.addEventListener('keydown', function (e) {
    var tag = e.target && e.target.tagName;
    if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
    var k = e.key;
    if ((e.ctrlKey || e.metaKey) && (k === 'z' || k === 'Z')) {
      e.preventDefault();
      if (e.shiftKey) redo(); else undo();
      return;
    }
    if ((e.ctrlKey || e.metaKey) && (k === 'y' || k === 'Y')) { e.preventDefault(); redo(); return; }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (k === ' ') { SPACE = true; setCursor(HOVERED); return; }
    // ESCAPE CANCELS THE LIVE DRAG FIRST.  It was bound only in the page's own handler,
    // which cleared the programme filter and left the editor's selection untouched -- so
    // the two selection models disagreed and `drop()` after Escape still committed the
    // move.  One order, said once.
    if (k === 'Escape') { escapeGesture(); return; }
    if (k === 'e' || k === 'E') { setMode(MODE === 'edit' ? 'play' : 'edit'); return; }
    if (MODE !== 'edit') return;
    if (k === 'Delete' || k === 'Backspace') {
      e.preventDefault();
      var r = removeSelected();
      if (r && !r.ok && r.problems.length) toast('bad', r.problems[0].message);
      return;
    }
    if (k === 'L' || k === 'l') {
      if (LASTMOVED) toast('ok', reconcileLengths(LASTMOVED) + ' segment length(s) set to match the drawing');
      return;
    }
    if (k === 'ArrowLeft' || k === 'ArrowRight' || k === 'ArrowUp' || k === 'ArrowDown') {
      if (!SELSET.length) return;
      e.preventDefault();
      nudge(k, e.shiftKey ? 4 : 1);
      return;
    }
  });
  document.addEventListener('keyup', function (e) {
    if (e.key === ' ') { SPACE = false; setCursor(HOVERED); }
  });
}
var SPACE = false, BAND = null, SRCT = null, LASTMOVED = null;

function nearestId(mx, my) {
  var best = null, bd = Infinity;
  for (var i = 0; i < A.nodes.length; i++) {
    var n = A.nodes[i];
    var d = (px(n) - mx) * (px(n) - mx) + (py(n) - my) * (py(n) - my);
    if (d < bd) { bd = d; best = n.id; }
  }
  return best;
}

// Arrow-key nudge: coalesced into ONE undo entry by an explicit meta.group stamped at the
// start of the repeat, never by guessing from timestamps at undo time.  Time-based
// coalescing would be exactly the kind of second implementation this codebase has been
// burned by.
function nudge(key, mult) {
  var dx = key === 'ArrowLeft' ? -1 : key === 'ArrowRight' ? 1 : 0;
  var dy = key === 'ArrowUp' ? -1 : key === 'ArrowDown' ? 1 : 0;
  var stepx = (L.ux || 1) * mult, stepy = (L.uy || L.ux || 1) * mult;
  for (var i = 0; i < SELSET.length; i++) {
    var n = nodeById[SELSET[i].id];
    if (!n) continue;
    emit({ method: 'move_site',
           args: [n.id, Q.pyFloat(n.x + dx * stepx), Q.pyFloat(n.y + dy * stepy)],
           kwargs: {}, meta: { group: 'nudge', src: 'keys' } });
  }
}

// EVERY KIND HOVERS.  This used to bail out for a segment (`if (!h || h.kind ===
// 'segment')`) and had no idea a loop existed, so two of the four things on the stage
// gave no feedback whatever.  Returns the hit, so the pointer handler is a one-line
// adapter and the harness can assert what the cursor is about to say.
var HOVERED = null;
function hover(mx, my) {
  var h = hit(mx, my);
  HOVERED = h;
  setCursor(h);
  paintOverlay();
  return h;
}

function showHud(cx, cy, r) {
  var hud = $('hud');
  if (!hud || !r || !GHOST) return;
  hud.className = r.warnings.length ? 'hud warn' : 'hud';
  hud.style.left = cx + 'px'; hud.style.top = cy + 'px';
  var d = Math.sqrt((r.x - GHOST.x0) * (r.x - GHOST.x0) + (r.y - GHOST.y0) * (r.y - GHOST.y0));
  hud.textContent = GHOST.id + ' -> (' + r.x.toFixed(2) + ', ' + r.y.toFixed(2) + ')  d=' +
                    d.toFixed(2) + (r.warnings.length ? '  ·  ' + r.warnings[0] : '');
  EGHOST.setAttribute('cx', GHOST.px0); EGHOST.setAttribute('cy', GHOST.py0);
  EGHOST.setAttribute('r', 0.45 * L.g); EGHOST.style.display = '';
  for (var i = 0; i < 2; i++) EGUIDE[i].style.display = 'none';
  for (i = 0; i < Math.min(2, (r.guides || []).length); i++) {
    var g = r.guides[i], ln = EGUIDE[i];
    if (g[0] === 'x') {
      var X = L.ox + g[1] * L.sx;
      ln.setAttribute('x1', X); ln.setAttribute('x2', X);
      ln.setAttribute('y1', 0); ln.setAttribute('y2', L.H);
    } else {
      var Y = L.oy + g[1] * L.sy;
      ln.setAttribute('y1', Y); ln.setAttribute('y2', Y);
      ln.setAttribute('x1', 0); ln.setAttribute('x2', L.W);
    }
    ln.style.display = '';
  }
}
function hideHud() {
  var hud = $('hud');
  if (hud) hud.className = 'hud off';
  if (EGHOST) EGHOST.style.display = 'none';
  for (var i = 0; i < EGUIDE.length; i++) EGUIDE[i].style.display = 'none';
}

// ------------------------------------------------------------------- boot
//
// The page runs its OWN interpreter over `A.listing.lines` from scratch, before the user
// touches anything, and compares the result against the `fingerprint` Python shipped.  On
// disagreement editing is refused and the page says why.  That extends the existing
// checksum self-check to the architecture rather than adding a second status mechanism --
// a drift that escapes CI is then visible at the user's desk instead of quietly producing
// a wrong price.
function boot() {
  if (!Q || !E) { WHY_NOT = 'the client-side engine did not load'; return; }
  // The schema comes from `qccd/arch/schema.py::export_schema()` through the data blob.
  // The engine keeps no copy, so this must happen before the first `serialize()`.
  if (!D.schema) {
    WHY_NOT = 'this page was emitted without the schema, so the browser cannot tell ' +
              'which documents the Python loader will accept';
    return;
  }
  try { Q.setSchema(D.schema); E.setBounds(D.schema.bounds); }
  catch (err) { WHY_NOT = err.message; return; }
  // The template registry, same contract as the schema: `Machine.ring(..., template=...)`
  // reads `arch/<stem>.arch.json` off disk in Python, and a browser has no filesystem, so
  // the page ships each template as the RECORDS THAT DECLARE IT.  Without this a listing
  // Python replays is refused here with `unknown_template`.
  try { Q.setTemplates(D.templates || {}, D.template_default); }
  catch (err) { WHY_NOT = err.message; return; }
  if (!D.arch.listing || !D.arch.listing.lines) {
    WHY_NOT = 'this page was emitted without the architecture listing, so it cannot ' +
              'rebuild the machine from first principles';
    return;
  }
  splitListing();
  // The page script already ran `deriveStage(P.frames)` at load, against the device this
  // page was emitted for.  Recording the array identity here is what stops the first
  // rebuild re-deriving it: those four tables describe a PROGRAMME on the device it was
  // compiled against, and re-deriving them after a geometry edit would quietly redraw a
  // programme that cannot run instead of freezing on it.
  LAST_FRAMES = P.frames;
  BASE_LOOPS = {};
  for (var lid in A.loops) if (has(A.loops, lid)) BASE_LOOPS[lid] = A.loops[lid].length;
  var r = replay();
  if (r.error) { WHY_NOT = r.error; return; }
  if (r.problems.length) { WHY_NOT = r.problems[0].message; return; }
  // the constants must match the ones Python laid this page out with
  var consts = D.layout_consts || null;
  if (consts) {
    for (var k in consts) if (has(consts, k)) {
      if (Q.LAYOUT_CONSTS[k] !== consts[k]) {
        WHY_NOT = 'the engine and this page disagree about the layout constant ' + k +
                  ' (' + Q.LAYOUT_CONSTS[k] + ' vs ' + consts[k] + '), so the picture it ' +
                  'would draw is not the picture Python measured';
        return;
      }
    }
  }
  STATE = r.ok;
  // The shipped architecture gets linted BEFORE the user touches anything.  Without
  // this the badge reads 0 at load and jumps the moment the first unrelated edit calls
  // `rebuild()`, so a drag that moved one node appears to have created three problems --
  // and undoing the drag does not take them away, because they were never its fault.
  LINTS = Q.lint(STATE);
  buildMaxOcc();
  buildIndex();
  initOverlay();
  try { HW0 = Q.hardwareReport(STATE.device, STATE.control, STATE.budget, STATE.name); }
  catch (err) { HW0 = null; }
  READY = true;
  repriceNow();
  // A page whose SHIPPED programme does not fit its own device freezes at load.  `boot()`
  // does not otherwise redraw, and `repriceNow` above is where the flag is written.
  if (PROGRAM_STALE && typeof draw === 'function') draw();
  wire();
  paint();
  // The page script renders the Machine pane BEFORE this file has run, so the first
  // render falls back to the verdicts Python shipped.  Re-render now that the browser's
  // own rule pass exists: the heading has to COUNT what was actually checked, and a page
  // that showed 21 green badges from a run it did not do would be claiming coverage.
  if (typeof renderSide === 'function') renderSide();
  renderReport();
}


// =====================================================================================
// THE DESIGN TOOL: transactions, from-scratch geometry, the programme lane, the report
// =====================================================================================

// ------------------------------------------------------------------- transactions
//
// `emit()` commits ONE op and rebuilds.  Every gallery pick and the blank-canvas seed are
// MULTI-STATEMENT AND ATOMIC: `d.site` alone after a `blank_device` fails with `no_builder`,
// and a seed verb DISCARDS every statement before it, so a gallery pick must re-emit the
// zone and control defaults after the seed in the same breath.  Without this a refused
// half-applied pick would leave the design in a state the user did not ask for and cannot
// see, and undo would restore only half of it.
//
// Same rollback semantics as `emit`, one undo group, one applier.
function transaction(ops, label) {
  var i, at = EDITS.length;
  var g0 = GEOM.slice(), s0 = SEED, p0 = POST.slice(), e0 = EDITS.slice();
  var geom = GEOM.slice(), seed = SEED, post = POST.slice(), edits = EDITS.slice();
  var group = GROUP + 1;
  for (i = 0; i < ops.length; i++) {
    var op = ops[i];
    if (op.canvas) {
      // a hard reset: a new device replaces the geometry, the seal and the retunes
      geom = op.canvas.geom.slice();
      seed = op.canvas.seed;
      post = (op.canvas.post || []).slice();
      edits = [];
      at = 0;
      continue;
    }
    if (op.build) { edits.push({ build: op.build, meta: { group: group, label: label } }); continue; }
    if (op.topology) { edits.push({ topology: op.topology, meta: { group: group, label: label } }); continue; }
    edits.push({ method: op.method, args: op.args || [], kwargs: op.kwargs || {},
                 meta: { group: group, label: label } });
  }
  // TRY THE WHOLE THING FIRST, on a copy.  A builder statement that breaks the SEAL --
  // a segment to a node that does not exist, say -- makes `applyProgram` refuse the base
  // program outright, and committing that would leave the page with no architecture at
  // all rather than with a refused edit.  Same shape as `tryTopology`: a refusal leaves
  // the last good architecture on the stage.
  //
  // `buildProblems` is what blames the right STATEMENT: every builder refusal otherwise
  // surfaces at the seed's index with a schema path (`$.geometry.nodes[0].id`), which a
  // design tool cannot highlight a gesture from.
  var trial = Q.applyProgram(baseCallsFrom(geom, seed, post, edits));
  if (trial.error) {
    var blame = Q.buildProblems(baseCallsFrom(geom, seed, post, edits));
    return { ok: false, label: label,
             problems: blame.map(function (b) {
               return { i: null, code: b.code, method: b.method, message: b.message }; }) };
  }
  GROUP = group;
  GEOM = geom; SEED = seed; POST = post; EDITS = edits;
  UNDONE.length = 0;
  rebuild();
  var mine = PROBLEMS.filter(function (p) { return p.i !== null && p.i >= at; });
  if (mine.length) {
    GEOM = g0; SEED = s0; POST = p0; EDITS = e0;
    rebuild();
    return { ok: false, problems: mine, label: label };
  }
  return { ok: true, problems: PROBLEMS, label: label };
}

// undo/redo span the whole group, so one Ctrl+Z takes back one gesture however many
// records it emitted
function undoGroup() {
  if (!EDITS.length) { undo(); return; }
  var g = (EDITS[EDITS.length - 1].meta || {}).group;
  if (g === undefined) { undo(); return; }
  while (EDITS.length && (EDITS[EDITS.length - 1].meta || {}).group === g) {
    UNDONE.push(EDITS.pop());
  }
  rebuild();
}
function redoGroup() {
  if (!UNDONE.length) { redo(); return; }
  var g = (UNDONE[UNDONE.length - 1].meta || {}).group;
  if (g === undefined) { redo(); return; }
  while (UNDONE.length && (UNDONE[UNDONE.length - 1].meta || {}).group === g) {
    EDITS.push(UNDONE.pop());
  }
  rebuild();
}

// ------------------------------------------------------------------- from scratch
//
// Every gesture is a PURE FUNCTION returning a call record, so the undo stack, the text
// lane, the exported Python and the parity harness stay one thing.

function idPattern() {
  var p = (D.schema && D.schema.bounds && D.schema.bounds.id_pattern) || null;
  if (!p) return null;
  return new RegExp(p);
}
function checkId(kind, id) {
  var re = idPattern();
  if (typeof id !== 'string' || !id) return "a " + kind + " needs an id";
  if (re && !re.test(id)) {
    return "'" + id + "' is not a usable " + kind + " id: it must start with a letter or " +
           'underscore and contain only letters, digits, and _ . : -';
  }
  return null;
}

// THE DEFAULT SEED IS `from_device`, NEVER `blank_device`.
//
// `Machine.blank_device` declares `primitives: {}`, and a device with no `shuttle_segment`
// curve cannot be priced AT ALL -- the first run dies with
// `KeyError: architecture declares no 'shuttle_segment' curve`.  A "new device" button that
// seeded with `blank_device` would make capability 4 (write a test programme and evaluate
// it) unreachable from capability 1 (build from scratch), and the honest refusal that
// followed would read as a broken tool.
function newCanvas(opts) {
  opts = opts || {};
  var name = opts.name === undefined ? 'design' : String(opts.name);
  var bad = checkId('device name', name);
  if (bad) return { ok: false, problems: [{ code: 'bad_id', message: bad }] };
  var tmpl = opts.template === undefined ? (Q.templateDefault() || null) : opts.template;
  var geom = [{ method: 'DeviceBuilder', args: [opts.generator || 'explicit'], kwargs: {} }];
  // THE ZONES THE COMPONENT LIBRARY NEEDS, DECLARED BEFORE THE SEAL. A blank canvas
  // declares none, so every component that places a zoned site was refused -- correctly
  // and uselessly, since placing something is the first thing anyone does. Declaring them
  // in `post` did not work either: `post` runs AFTER `blank_device`, and a zone declared
  // after the seal cannot be used by a site placed later (`zone_after_seal`). They belong
  // in the seed, which is what `blank_device(zones=...)` is for.
  var ZONES = {
    data:    { capacity: 2 },
    trap:    { capacity: 2, gate: true, spam: true, cool: true },
    ancilla: { capacity: 2, gate: true, spam: true, cool: true },
    gate:    { capacity: 2, gate: true },
    load:    { capacity: 8, spam: true, cool: true, photoionization: true }
  };
  var seed = tmpl
    ? { method: 'from_device', args: [], kwargs: { name: name, template: tmpl } }
    : { method: 'blank_device', args: [], kwargs: { name: name, zones: ZONES } };
  // Seed the one key that makes a blank canvas RUNNABLE. `control.model` is required by
  // the schema, and without it `declare_class` is refused ("$.control: missing required
  // key 'model'") -- so a from-scratch device could be built and exported but never
  // priced, and every one of the 25 rules stayed `unchecked` forever. `simd_classes` is
  // the model every shipped architecture uses; `set_control` changes it.
  // Plus the smallest primitive set that makes a device PRICEABLE. Without these,
  // `declare_class` succeeds and pricing then dies on "architecture declares no
  // `shuttle_segment` curve", so the tool could build a device it could never evaluate.
  //
  // These are REAL published operating points, not invented defaults -- the same
  // `qccdsim_jones` values every shipped architecture uses, carrying their `source` so the
  // panel shows where each number came from. A design tool must not price a device against
  // constants it made up; `set_curve` / `set_degree_curve` replace them.
  var post = [
    { method: 'set_control', args: [], kwargs: { model: 'simd_classes' } },
    { method: 'set_curve', args: ['shuttle_segment',
        [{ us: 5.0, quanta: 0.1, table: 'qccdsim_jones', source: '2510.23519',
           label: 't7 ion shuttling, one segment' }]], kwargs: {} },
    { method: 'set_degree_curve', args: ['junction_cross', 3,
        [{ us: 100.0, quanta: 3.0, table: 'qccdsim_jones', source: '2510.23519',
           label: 'three-way junction crossing' }]], kwargs: {} },
    // a grid tile makes degree-4 nodes, and an unpriceable junction is an R11 violation
    { method: 'set_degree_curve', args: ['junction_cross', 4,
        [{ us: 100.0, quanta: 3.0, table: 'qccdsim_jones', source: '2510.23519',
           label: 'four-way junction crossing' }]], kwargs: {} },
  ];
  var r = transaction([{ canvas: { geom: geom, seed: seed, post: post } }], 'new canvas');
  if (r.ok) { setProgram([]); }
  return r;
}

// Start from a GENERATOR, borrowing a template's physics.  `Machine.blank(<gen>)` is
// deliberately NOT the default: it declares no zone types, so every site comes out with
// capacity 0 and the document Python refuses -- which is now caught at the export
// boundary, but is still a worse place to find out than not offering it.
function newFromGenerator(gen, params, opts) {
  opts = opts || {};
  var name = opts.name === undefined ? 'design' : String(opts.name);
  var bad = checkId('device name', name);
  if (bad) return { ok: false, problems: [{ code: 'bad_id', message: bad }] };
  var kw = {};
  for (var k in params) if (has(params, k)) kw[k] = params[k];
  kw.name = name;
  kw.template = opts.template === undefined ? (Q.templateDefault() || null) : opts.template;
  var seed = { method: 'from_template', args: [String(gen)], kwargs: kw };
  var r = transaction([{ canvas: { geom: [], seed: seed, post: [] } }], 'new device');
  if (r.ok) { setProgram([]); }
  return r;
}

function nodeIds() {
  var out = [];
  if (!STATE || !STATE.device) return out;
  for (var nid in STATE.device.nodes) if (has(STATE.device.nodes, nid)) out.push(nid);
  return out;
}
function builderNodeIds() {
  // the ids the BUILDER holds, which is what a new id must not collide with -- the
  // builder survives a seal, so it can hold nodes the sealed device does not
  var out = {}, calls = baseCalls(), i;
  for (i = 0; i < calls.length; i++) {
    if (calls[i].method === 'd.site' || calls[i].method === 'd.junction') {
      out[String(calls[i].args[0])] = true;
    }
  }
  for (i = 0; i < nodeIds().length; i++) out[nodeIds()[i]] = true;
  return out;
}
function builderSegIds() {
  var out = {}, calls = baseCalls(), i;
  for (i = 0; i < calls.length; i++) {
    if (calls[i].method === 'd.segment') out[String(calls[i].args[0])] = true;
  }
  if (STATE && STATE.device) {
    for (var sid in STATE.device.segments) if (has(STATE.device.segments, sid)) out[sid] = true;
  }
  return out;
}

// `d.site` / `d.junction`.  THE BUILDER OVERWRITES A DUPLICATE ID SILENTLY -- measured on
// both sides: `d.site("S0",0,0)` then `d.site("S0",5,5)` leaves one node at (5,5) with no
// warning anywhere, and nothing downstream will ever catch it.  So the UI refuses.
function addNodeAt(x, y, opts) {
  opts = opts || {};
  var kind = opts.kind === 'junction' ? 'junction' : 'site';
  var taken = builderNodeIds();
  var id = opts.id === undefined ? freshFrom(taken, kind === 'junction' ? 'J' : 'N') : String(opts.id);
  var bad = checkId('node', id);
  if (bad) return { ok: false, problems: [{ code: 'bad_id', message: bad }] };
  if (has(taken, id)) {
    return { ok: false, problems: [{ code: 'duplicate_id',
      message: "a node called '" + id + "' already exists; the builder would overwrite it " +
               'silently and nothing downstream would notice' }] };
  }
  // coincident placement: `min_nearest_neighbour` SKIPS coincident points, so two nodes on
  // one spot silently resize every mark on the stage -- the same reason `validate()`
  // already refuses a coincident `move_site`.
  var ns = nodesOf(STATE);
  for (var i = 0; i < ns.length; i++) {
    if (Math.abs(ns[i].x - x) < 1e-9 && Math.abs(ns[i].y - y) < 1e-9) {
      return { ok: false, problems: [{ code: 'coincident',
        message: 'a node already sits at (' + x + ', ' + y + '); two nodes on one point ' +
                 'make the drawn scale meaningless' }] };
    }
  }
  var kw = {};
  if (kind === 'site') {
    var hasZone = opts.zone !== undefined && opts.zone !== null && opts.zone !== '';
    var hasCap = opts.capacity !== undefined && opts.capacity !== null && opts.capacity !== '';
    if (hasZone) kw.zone = String(opts.zone);
    if (hasCap) {
      kw.capacity = Math.trunc(Number(opts.capacity));
    } else if (!hasZone) {
      // A trap must hold at least one ion, and on a BLANK canvas there is no zone type to
      // inherit that from -- so the first node a user drops was refused with "a site needs
      // capacity >= 1". Correct physics, useless as a default: it fires on the most obvious
      // first action in the whole tool. One ion is the smallest thing a trap can be, so it
      // is the honest default; naming a zone or passing a capacity still overrides it.
      kw.capacity = 1;
    }
  } else if (opts.zone || opts.capacity) {
    return { ok: false, problems: [{ code: 'TypeError',
      message: "DeviceBuilder.junction() got an unexpected keyword argument '" +
               (opts.zone ? 'zone' : 'capacity') + "'" }] };
  }
  if (opts.labels && opts.labels.length) kw.labels = opts.labels.slice();
  var rec = { method: kind === 'site' ? 'd.site' : 'd.junction',
              args: [id, Q.pyFloat(x), Q.pyFloat(y)], kwargs: kw };
  var r = transaction([{ build: rec }], 'add ' + kind);
  r.id = id;
  return r;
}

function freshFrom(taken, prefix) {
  for (var i = 0; ; i++) if (!has(taken, prefix + i)) return prefix + i;
}

// `d.segment`.  `loop=` only when a and b are ALREADY consecutive in that loop: a chord
// declared as a loop edge is priced as a turn the loop does not contain.
function joinNodes(a, b, opts) {
  opts = opts || {};
  var probs = [];
  if (a === b) probs.push({ code: 'self_loop', message: 'a segment must join two different nodes' });
  var dev = STATE ? STATE.device : null;
  if (!dev || !has(dev.nodes, a)) probs.push({ code: 'unknown_node', message: "no node '" + a + "'" });
  if (!dev || !has(dev.nodes, b)) probs.push({ code: 'unknown_node', message: "no node '" + b + "'" });
  if (dev) {
    for (var sid in dev.segments) if (has(dev.segments, sid)) {
      var sg = dev.segments[sid];
      if ((sg.a === a && sg.b === b) || (sg.a === b && sg.b === a)) {
        probs.push({ code: 'parallel', message: "segment '" + sid + "' already joins these " +
                     'two nodes -- parallel segments are not modelled' });
      }
    }
  }
  var len = opts.length === undefined ? null : Number(opts.length);
  if (len !== null && !(len > 0)) {
    probs.push({ code: 'bad_length', message: 'a segment must have positive length' });
  }
  var capv = opts.capacity === undefined ? null : Math.trunc(Number(opts.capacity));
  if (capv !== null && capv < 1) {
    probs.push({ code: 'bad_capacity', message: 'a segment must carry at least one ion' });
  }
  var taken = builderSegIds();
  var id = opts.id === undefined ? freshFrom(taken, 'X') : String(opts.id);
  var bad = checkId('segment', id);
  if (bad) probs.push({ code: 'bad_id', message: bad });
  if (has(taken, id)) {
    probs.push({ code: 'duplicate_id',
                 message: "a segment called '" + id + "' already exists; the builder would " +
                          'overwrite it silently' });
  }
  if (opts.loop) {
    var lp = dev && dev.loops[opts.loop];
    if (!lp) probs.push({ code: 'unknown_loop', message: "no loop '" + opts.loop + "'" });
    else if (!consecutiveIn(lp, a, b)) {
      probs.push({ code: 'chord_on_loop',
        message: "'" + a + "' and '" + b + "' are not consecutive in loop '" + opts.loop +
                 "'; a chord declared as a loop edge is charged a turn the loop does not contain" });
    }
  }
  if (probs.length) return { ok: false, problems: probs };
  var kw = {};
  if (opts.loop) kw.loop = String(opts.loop);
  if (len !== null) kw.length = Q.pyFloat(len);
  if (capv !== null) kw.capacity = capv;
  if (opts.labels && opts.labels.length) kw.labels = opts.labels.slice();
  var r = transaction([{ build: { method: 'd.segment', args: [id, a, b], kwargs: kw } }],
                      'join');
  r.id = id;
  return r;
}

function consecutiveIn(lp, a, b) {
  var ns = lp.nodes, k = ns.length;
  for (var i = 0; i + 1 < k; i++) {
    if ((ns[i] === a && ns[i + 1] === b) || (ns[i] === b && ns[i + 1] === a)) return true;
  }
  if (lp.closed && k > 1) {
    if ((ns[k - 1] === a && ns[0] === b) || (ns[k - 1] === b && ns[0] === a)) return true;
  }
  return false;
}

// `d.loop`.  Every consecutive pair -- plus the wrap when closed -- must have a real
// segment, or `Device.loop_segments` raises and the whole document is refused at load.
function closeLoop(id, walk, closed, kind) {
  var probs = [], dev = STATE ? STATE.device : null;
  var minN = (D.schema && D.schema.bounds && D.schema.bounds.min_loop_nodes) || 2;
  id = String(id);
  var bad = checkId('loop', id);
  if (bad) probs.push({ code: 'bad_id', message: bad });
  walk = (walk || []).map(String);
  if (walk.length < minN) {
    probs.push({ code: 'short_loop',
      message: 'a transport loop needs at least ' + minN + ' nodes, because a shorter one ' +
               'has no segment to walk and no .arch.json can hold it' });
  }
  var seen = {};
  for (var i = 0; i < walk.length; i++) {
    if (has(seen, walk[i])) probs.push({ code: 'repeat', message: "loop '" + id + "': repeats a node" });
    seen[walk[i]] = true;
    if (dev && !has(dev.nodes, walk[i])) {
      probs.push({ code: 'unknown_node', message: "loop '" + id + "': unknown node '" + walk[i] + "'" });
    }
  }
  if (kind !== 'ring' && kind !== 'path') {
    probs.push({ code: 'bad_kind', message: "a loop kind is 'ring' or 'path', not " +
                 JSON.stringify(kind) });
  }
  if (dev) {
    var pairs = [];
    for (i = 0; i + 1 < walk.length; i++) pairs.push([walk[i], walk[i + 1]]);
    if (closed && walk.length > 1) pairs.push([walk[walk.length - 1], walk[0]]);
    for (i = 0; i < pairs.length; i++) {
      if (!segmentBetween(dev, pairs[i][0], pairs[i][1])) {
        probs.push({ code: 'no_segment',
          message: "loop '" + id + "': no segment between '" + pairs[i][0] + "' and '" +
                   pairs[i][1] + "'" });
        break;
      }
    }
  }
  if (probs.length) return { ok: false, problems: probs };
  return transaction([{ build: { method: 'd.loop', args: [id, walk],
                                kwargs: { closed: !!closed, kind: String(kind) } } }],
                     'close loop');
}
function segmentBetween(dev, a, b) {
  for (var sid in dev.segments) if (has(dev.segments, sid)) {
    var sg = dev.segments[sid];
    if ((sg.a === a && sg.b === b) || (sg.a === b && sg.b === a)) return sid;
  }
  return null;
}

// `set_zone`, emitted AFTER the seed.  `from_device` has no `zones=` parameter, so a novel
// zone name is only reachable by putting `capacity=` on every site in it -- and the export
// boundary refuses while any site's zone is undeclared, because `Architecture.can` raises
// there and R6 cannot run at all.
// THE RECORD A `set_zone` WOULD WRITE for this name: the defaults, then whatever the
// zone type ALREADY says, then the caller's fields.  The middle layer is the fix for a
// silent data loss: `set_zone` replaces the record wholesale, so starting from the
// defaults alone meant editing `data` (cool: true) through a form that sent only
// `capacity` dropped `cool` -- a save that loses a field every time it is used.
//
// ONE merge, read by `nameZone` and by the form that prefills it, so the box you look at
// and the record that gets written cannot disagree.
function zoneFields(zone, fields) {
  var kw = {}, k;
  var dflt = (D.defaults && D.defaults.new_zone_type) || { capacity: 1 };
  var cur = ((A.zone_types || {})[String(zone)]) || {};
  for (k in dflt) if (has(dflt, k)) kw[k] = dflt[k];
  for (k in cur) if (has(cur, k)) kw[k] = cur[k];
  for (k in (fields || {})) if (has(fields, k)) kw[k] = fields[k];
  return kw;
}
function nameZone(zone, fields) {
  var bad = checkId('zone type', String(zone));
  if (bad) return { ok: false, problems: [{ code: 'bad_id', message: bad }] };
  return transaction([{ method: 'set_zone', args: [String(zone)],
                        kwargs: zoneFields(zone, fields) }], 'zone');
}

// EXPLODE TO EXPLICIT.  The eleven rebuild-only geometry fields -- a site's zone and
// labels, a segment's capacity, loop and labels, and all five loop fields -- become
// editable in place by rewriting ONE statement, with zero new verbs.
//
// IRREVERSIBLE, and the caller must say so before the first click:
// `Device.reproducible_from_generator()` goes False forever and `to_json(expanded=False)`
// writes the expanded form from then on.
function explodeToExplicit() {
  if (!STATE || !STATE.device) return { ok: false, problems: [{ message: 'no device' }] };
  var tmpl = (SEED && SEED.kwargs && SEED.kwargs.template) || Q.templateDefault() || null;
  var stmts = Q.explicitStatements(STATE.device, { name: STATE.name, template: tmpl });
  var geom = [], seed = null;
  for (var i = 0; i < stmts.length; i++) {
    if (kindOf(stmts[i].method) === 'seed') seed = stmts[i]; else geom.push(stmts[i]);
  }
  var post = POST.slice();
  for (var j = 0; j < EDITS.length; j++) {
    if (!EDITS[j].build && !EDITS[j].topology) {
      post.push({ method: EDITS[j].method, args: EDITS[j].args, kwargs: EDITS[j].kwargs });
    }
  }
  var r = transaction([{ canvas: { geom: geom, seed: seed, post: post } }], 'explode');
  r.statements = stmts.length;
  r.warning = 'this device no longer reproduces from its generator: saving now writes ' +
              geom.length + ' explicit statements rather than the generator and its parameters.';
  return r;
}

// ------------------------------------------------------------------- the programme lane
//
// A SECOND RECORD LIST beside EDITS, replayed from scratch by the same one-applier
// discipline.  `P.frames` stays the single source the stage, the price and the rules all
// read, so an authored programme costs the rest of the page nothing.
var PROG = [], PROG_SRC = null, LOWER = null, AUTHORED = false;
var SHIPPED_FRAMES = null, SHIPPED_PROV = null;

function setProgram(records) {
  PROG = (records || []).map(function (r) {
    return { method: r.method, args: (r.args || []).slice(), kwargs: r.kwargs || {},
             text: r.text, line: r.line };
  });
  PROG_SRC = null;
  rebuild();
  return { ok: true, errors: lowerErrors() };
}

function lowerErrors() { return LOWER ? LOWER.errors : []; }

// Re-lowered on EVERY architecture edit as well as every programme edit, which is why an
// authored programme's `entails` can never go stale: it is read from the live class table
// at lowering time rather than baked at emit time.
function lowerNow() {
  if (SHIPPED_FRAMES === null) {
    SHIPPED_FRAMES = P.frames;
    SHIPPED_PROV = (typeof PROV !== 'undefined') ? PROV : null;
  }
  if (!PROG.length) {
    AUTHORED = false;
    LOWER = null;
    P.frames = SHIPPED_FRAMES;
    if (typeof PROV !== 'undefined') PROV = SHIPPED_PROV;
    return;
  }
  AUTHORED = true;
  // the page's own programme name describes the SHIPPED programme; an authored one is a
  // different programme and says so, in the export filename as well as on screen
  P.name = 'authored';
  var classes = classTable();
  try {
    LOWER = Q.lowerProgram(PROG, STATE.device, A.loops,
                           { name: STATE.name, classes: classes });
  } catch (err) {
    LOWER = { frames: [], prov: null, errors: [{ i: null, code: 'error', message: err.message }] };
  }
  P.frames = LOWER.frames;
  P.n_instructions = LOWER.frames.length;
  if (typeof PROV !== 'undefined') PROV = LOWER.prov;
}

function classTable() {
  var classes = {};
  if (!STATE) return classes;
  var extra = ((STATE.control.classes || {}).extra) || [];
  for (var i = 0; i < extra.length; i++) classes[extra[i].id] = extra[i];
  return classes;
}

function programSource() {
  if (PROG_SRC !== null) return PROG_SRC;
  if (!PROG.length) return '';
  return Q.renderProgramSource(PROG);
}

function applyProgramSource(src) {
  PROG_SRC = src;
  var p = Q.parse(src);
  if (p.errors.length) return { ok: false, errors: p.errors };
  var wrong = p.arch.length ? p.arch[0] : null;
  if (wrong) {
    return { ok: false, errors: [{ line: wrong.line, col: 1,
      message: JSON.stringify(wrong.method) + ' is an architecture statement; the ' +
               'programme pane takes `p.` verbs only (have: ' +
               Q.PROGRAM_METHODS.join(', ') + ')', text: wrong.text }] };
  }
  PROG = p.prog.map(function (r) {
    return { method: r.method, args: r.args, kwargs: r.kwargs, text: r.text, line: r.line };
  });
  PROG_SRC = src;
  rebuild();
  return { ok: true, errors: [], problems: lowerErrors() };
}

// One record, appended.  The palette's programme buttons and a future drag-to-author both
// write through this, so the text lane and the button lane cannot disagree.
function emitProgram(rec) {
  var before = PROG.slice();
  PROG.push({ method: rec.method, args: (rec.args || []).slice(), kwargs: rec.kwargs || {} });
  PROG_SRC = null;
  rebuild();
  var mine = lowerErrors().filter(function (e) { return e.i === before.length; });
  if (mine.length) {
    PROG = before;
    rebuild();
    return { ok: false, problems: mine };
  }
  return { ok: true, problems: lowerErrors() };
}

function programToTsir() {
  if (!STATE) return null;
  var t = Q.programToTsir(PROG, STATE.device, A.loops,
                          { name: (P.name || 'authored'), archSpec: STATE.name + '.arch.json',
                            classes: classTable() });
  return t.doc;
}

// EXPORT IS A PAIR, never a single file.  `TSIR.arch_spec` names an `.arch.json` the user
// may not have; shipping only the programme produces a file the toolchain cannot load and
// the failure surfaces in Python minutes later rather than at the download.
function exportPair() {
  var bad = refuseExport('this design');
  if (bad) throw bad;
  var stem = STATE.name;
  return {
    arch: { name: stem + '.arch.json', text: JSON.stringify(Q.serialize(STATE), null, 2) },
    tsir: { name: stem + '.tsir.json',
            text: JSON.stringify(framesAsTsir(stem), null, 1) },
    command: 'python -m qccd run ' + stem + '.arch.json --tsir ' + stem + '.tsir.json' +
             ' --model ' + ((D.model && D.model.name) || 'corrected') + ' --json report.json'
  };
}

function framesAsTsir(stem) {
  if (AUTHORED) return programToTsir();
  return Q.framesToTsir(P.frames, STATE.device, classTable(), P.name || 'programme',
                        stem + '.arch.json');
}

// ------------------------------------------------------------------- the verdicts
//
// THREE STATES PER RULE, and the header counts rather than saying "all".  1 of the 25 is
// state-free; the browser re-derives 17 of them off the pricing walk; the other 6 need
// Python, and each is named WITH ITS REASON rather than being absent.
var RULES = null;
function ruleReport() { return RULES; }

function evaluateNow(model) {
  RULES = null;
  if (!STATE || !model) return;
  if (PRICE && PRICE.blocked) return;    // a broken programme has no verdicts to report
  // NO PROGRAMME, NO VERDICTS.  Every rule is vacuously satisfied over zero cycles, so a
  // report built from an empty replay would show 17 green badges for a machine nothing has
  // ever been run on -- the same shape of lie as a self-check whose loop body never
  // executed.  An empty canvas is the FIRST thing a user of this tool sees.
  if (!P.frames.length) return;
  try {
    RULES = Q.checkFrames(STATE.device, P.frames, A.loops, model, classTable(), {
      zone_types: STATE.zone_types,
      max_simd: P.max_simd_classes,
      gate_threshold: PH.gate_threshold,
      models_heating: !(D.model && D.model.models_heating === false),
      chain_limit: 15,
      state: STATE
    });
  } catch (err) {
    RULES = { fatal: err.message, by_rule: {}, messages: [], checked: [], passed: [],
              failed: [], skipped: {}, partial: {}, vacuous: {}, scope: 'browser',
              violations: 0 };
  }
  // THE RULE HALF OF THE SELF-CHECK.  Before any edit, the page's own counts must match
  // the integers Python shipped.  COUNTS, not verdicts: `architectureViolations` reported
  // 2 where Python reported 77 and the verdict agreed both times.  On a disagreement the
  // page WITHDRAWS the surface rather than degrading it -- the same thing `price().blocked`
  // and `PROGRAM_STALE` already do.
  RULES.oracle = null;
  if (RULES && !RULES.fatal && D.rule_checksum && !EDITS.length && !PROG.length) {
    var drift = [];
    for (var r in D.rule_checksum) if (has(D.rule_checksum, r)) {
      var want = D.rule_checksum[r], got = (RULES.by_rule || {})[r] || 0;
      if (want !== got) drift.push(r + ': python ' + want + ', here ' + got);
    }
    RULES.oracle = drift.length ? { ok: false, drift: drift }
                                : { ok: true, n: Object.keys(D.rule_checksum).length };
    if (drift.length) {
      RULES.checked = [];
      RULES.passed = [];
      RULES.withdrawn = "this page's own rule counts disagree with the verifier that " +
                        'produced it; trust Python, not this page';
    }
  }
}

// 25 entries, one per rule, each with its state and its reason.  The header text is
// DERIVED from this array and never written down.
function ruleCoverage() {
  var all = (D.evidence && D.evidence.rules_all) || [];
  var stmts = D.rule_statements || {};
  var rep = RULES || {};
  // no replay, no verdicts.  A programme that does not fit the device is not a programme
  // that "passes"; it is one nothing could be run on.
  var blocked = (PRICE && PRICE.blocked) ? breakMessage(PRICE.blocked[0]) : null;
  if (!RULES) {
    rep = { checked: [], by_rule: {}, skipped: {}, partial: {},
            withdrawn: blocked ? 'no rule could run: ' + blocked
                               : (P.frames.length ? 'the programme has not been replayed here'
                                                  : 'there is no programme to replay') };
  }
  var out = [];
  for (var i = 0; i < all.length; i++) {
    var r = all[i], state, why = '';
    if (rep.withdrawn) { state = 'unchecked'; why = rep.withdrawn; }
    else if ((rep.by_rule || {})[r]) { state = 'failed'; why = (rep.by_rule || {})[r] + ' violation(s)'; }
    else if ((rep.checked || []).indexOf(r) >= 0) { state = 'checked'; }
    else if (has(rep.partial || {}, r)) { state = 'partial'; why = rep.partial[r]; }
    else if (has(rep.skipped || {}, r)) { state = 'unchecked'; why = rep.skipped[r]; }
    else { state = 'unchecked'; why = 'not checked in the browser'; }
    out.push({ rule: r, state: state, why: why,
               statement: (stmts[r] || {}).statement || '',
               sources: (stmts[r] || {}).sources || '',
               count: (rep.by_rule || {})[r] || 0 });
  }
  return out;
}

// ------------------------------------------------------------------- the palette
//
// GENERATED from the shipped schema and the shipped consumer table, never drawn by hand.
// `D.schema` describes every CLOSED object -- node, segment, loop, zone type, curve point
// -- field for field, with its enums, bounds and id pattern.  `D.consumers` covers the
// OPEN maps the schema cannot describe (`primitives.*`, `heating`, `species`, `budget`,
// `control.*`, `provenance`) and carries, for each field, WHO READS IT.  27 of those 65
// fields carry `reader: null`: declared, printed, round-tripped and computed with by
// NOTHING.  The palette says so at the control, because rendering an inert field like a
// live one implies a causation it does not have.
function palette() {
  var out = [], root = (D.schema && D.schema.root) || null;
  function fieldsOf(spec, skip) {
    var fs = [], k;
    if (!spec || !spec.props) return fs;
    for (k in spec.props) if (has(spec.props, k)) {
      if (skip && skip.indexOf(k) >= 0) continue;
      var p = spec.props[k];
      fs.push({ name: k, type: p.type, enum: p.enum || null,
                min: p.min === undefined ? null : p.min,
                max: p.max === undefined ? null : p.max,
                pattern: p.pattern || null,
                required: (spec.required || []).indexOf(k) >= 0 });
    }
    return fs;
  }
  function at(path) {
    var cur = root, parts = path.split('.');
    for (var i = 0; i < parts.length && cur; i++) {
      if (parts[i] === '[]') cur = cur.items;
      else if (parts[i] === '*') cur = cur.values;
      else cur = (cur.props || {})[parts[i]];
    }
    return cur;
  }
  var DERIVED = ['degree', 'corner', 'corner_endpoints', 'schema_version'];
  out.push({ type: 'site', kind: 'stamp', verb: 'd.site',
             fields: fieldsOf(at('geometry.nodes.[]'), DERIVED.concat(['kind'])),
             defaults: (D.defaults || {}).node || {} });
  out.push({ type: 'junction', kind: 'stamp', verb: 'd.junction',
             fields: fieldsOf(at('geometry.nodes.[]'),
                              DERIVED.concat(['kind', 'zone_type', 'capacity',
                                              'capacity_explicit'])),
             defaults: (D.defaults || {}).node || {} });
  out.push({ type: 'segment', kind: 'stamp', verb: 'd.segment',
             fields: fieldsOf(at('geometry.segments.[]'), DERIVED),
             defaults: (D.defaults || {}).segment || {} });
  out.push({ type: 'loop', kind: 'stamp', verb: 'd.loop',
             fields: fieldsOf(at('geometry.loops.[]'), DERIVED),
             defaults: (D.defaults || {}).loop || {} });
  out.push({ type: 'zone_type', kind: 'named', verb: 'set_zone',
             fields: fieldsOf(at('zone_types.*'), DERIVED),
             defaults: (D.defaults || {}).new_zone_type || {} });
  out.push({ type: 'curve_point', kind: 'row', verb: 'set_curve',
             fields: fieldsOf(at('primitives.*.curve.[]'), DERIVED),
             defaults: (D.defaults || {}).curve_point || {} });
  // the OPEN maps, from the consumer table
  var groups = {};
  var cons = (D.consumers && D.consumers.fields) || [];
  for (var i = 0; i < cons.length; i++) {
    var f = cons[i], head = f.path.split('.')[0];
    if (head === 'zone_types') continue;             // closed; already covered above
    (groups[head] || (groups[head] = [])).push(f);
  }
  var VERB = { primitives: 'set_primitive', control: 'set_control', heating: 'set_heating',
               species: 'set_species', budget: 'set_budget' };
  for (var g in groups) if (has(groups, g)) {
    out.push({ type: g, kind: 'block', verb: VERB[g] || null,
               fields: groups[g].map(function (x) {
                 return { name: x.path, type: x.type, default: x.default,
                          reader: x.reader,
                          inert: x.reader === null };
               }), defaults: {} });
  }
  return out;
}

// ------------------------------------------------------------------- persistence
//
// THE FILE IS AUTHORITATIVE.  `localStorage` is an offer, never an action: auto-restoring
// would make the page's content depend on invisible state, and "is this my design or the
// shipped one?" would be unanswerable.
//
// `tests/shim.mjs` deliberately does NOT stub `localStorage`, and adding a stub would let
// the design tool's most important guarantee -- your work survives -- be asserted against
// a fake.  So the SERIALIZATION is a pure function and the storage is a three-line
// adapter, exactly as `exportPython`/`exportJson` already are: the harness asserts the
// round-trip identity through `digest()` and never touches storage at all.
var STORE = (function () {
  try { if (globalThis.localStorage) return globalThis.localStorage; } catch (e) { /* none */ }
  var m = Object.create(null);
  return { getItem: function (k) { return k in m ? m[k] : null; },
           setItem: function (k, v) { m[k] = String(v); },
           removeItem: function (k) { delete m[k]; },
           get length() { return Object.keys(m).length; },
           key: function (i) { return Object.keys(m)[i]; } };
})();
var STORE_KEY = 'qccd.studio.autosave';

function snapshot() {
  return {
    kind: 'qccd.studio', version: 1,
    arch: STATE ? Q.serialize(STATE) : null,
    program: { calls: PROG.map(function (r) {
      return { method: r.method, args: r.args, kwargs: r.kwargs }; }) },
    geom: GEOM.slice(), seed: SEED, post: POST.slice(),
    edits: EDITS.slice(),
    saved_at_frame: (typeof frame === 'number') ? frame : 0
  };
}

function restore(snap) {
  if (!snap || snap.kind !== 'qccd.studio') {
    return { ok: false, problems: [{ message: 'not a qccd.studio snapshot' }] };
  }
  var g0 = GEOM.slice(), s0 = SEED, p0 = POST.slice(), e0 = EDITS.slice(), pr0 = PROG.slice();
  GEOM = (snap.geom || []).slice();
  SEED = snap.seed || null;
  POST = (snap.post || []).slice();
  EDITS = (snap.edits || []).slice();
  PROG = ((snap.program || {}).calls || []).map(function (r) {
    return { method: r.method, args: (r.args || []).slice(), kwargs: r.kwargs || {} }; });
  PROG_SRC = null;
  var was = WHY_NOT;
  WHY_NOT = null;
  rebuild();
  // A REPLAY THAT DIED LEAVES NO PROBLEM TO FIND. `rebuild()` returns early on
  // `replay().error`, so `STATE` and `PROBLEMS` still describe the PREVIOUS build -- and
  // reading them here concluded that a document which cannot be built had imported
  // cleanly. That is the same shape as the bug this function was fixed for once already:
  // the operation failed and said it succeeded. `WHY_NOT` is the only witness, so it is
  // what gets checked.
  var died = WHY_NOT;
  var bad = died ? [{ code: (died && died.code) || 'replay_failed', i: null,
                      message: 'this document cannot be rebuilt: ' +
                               ((died && died.message) || String(died)) }]
                 : PROBLEMS.filter(function (p) { return p.i !== null; });
  if (bad.length) {
    GEOM = g0; SEED = s0; POST = p0; EDITS = e0; PROG = pr0;
    WHY_NOT = was;
    rebuild();
    return { ok: false, problems: bad };
  }
  return { ok: true, problems: PROBLEMS };
}

function autosave() {
  try { STORE.setItem(STORE_KEY, JSON.stringify(snapshot())); return true; }
  catch (err) { return false; }
}
function autoload() {
  var raw = null;
  try { raw = STORE.getItem(STORE_KEY); } catch (err) { return null; }
  if (!raw) return null;
  try { return JSON.parse(raw); } catch (err) { return null; }
}


// ------------------------------------------------------------------- keeping the work
//
// Everything above this line was written and never called: `autosave` and `autoload` each
// appeared exactly once in this file, in their own definition. The serialisation was
// finished and nothing reached it.

var SAVE_T = null, FILE_HANDLE = null, LAST_SAVED = null;

// DEBOUNCED, because `rebuild()` runs on every keystroke in the source pane and
// `JSON.stringify(snapshot())` on a 168-node device is not free.
function autosaveSoon() {
  if (SAVE_T) { clearTimeout(SAVE_T); }
  SAVE_T = setTimeout(function () { SAVE_T = null; autosave(); }, 700);
}

// `visibilitychange`, NOT `onbeforeunload`: the return-string form of beforeunload is
// ignored by every current browser, and the event itself is unreliable on mobile. This
// one fires when the tab is hidden, which is the moment that actually precedes losing it.
if (typeof document !== 'undefined' && document.addEventListener) {
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') { autosave(); }
  });
}

// ---- file in ----------------------------------------------------------------------
//
// Three shapes are accepted, sniffed rather than declared, because a user drops the file
// they have: our own snapshot, a bare `.arch.json`, or a bare list of edit ops.
function snapshotOf(text) {
  var doc;
  try { doc = JSON.parse(text); }
  catch (err) { return { ok: false, why: 'not JSON: ' + err.message }; }
  if (doc && doc.kind === 'qccd.studio') return { ok: true, snap: doc };
  if (doc && doc.geometry && doc.schema_version) {
    // AN ARCHITECTURE DOCUMENT, as the records that declare it.
    //
    // This branch used to build a seed `from_device` with a `document=` kwarg that
    // `from_device` does not implement -- it builds from `st.builder`, which nothing had
    // filled -- so `restore` succeeded with a device of zero nodes and reported no
    // problem at all. Exporting a design and opening it again destroyed it, quietly.
    //
    // `Q.documentStatements` turns the document into the same KIND of call list
    // `splitListing` replays for the architecture the page ships, so the import goes
    // through the interpreter that is already there rather than a second deserializer.
    // The edit log is dropped because a document carries no history -- the geometry
    // becomes the seed, and edits start from there.
    var stmts;
    try { stmts = Q.documentStatements(doc); }
    catch (err) {
      return { ok: false, why: 'that architecture document cannot be rebuilt: ' +
                               (err && err.message ? err.message : String(err)) };
    }
    var dgeom = [], dseed = null, dpost = [];
    for (var di = 0; di < stmts.length; di++) {
      var dk = kindOf(stmts[di].method);
      if (dseed === null && dk === 'build') dgeom.push(stmts[di]);
      else if (dseed === null && dk === 'seed') dseed = stmts[di];
      else dpost.push(stmts[di]);
    }
    return { ok: true, snap: { kind: 'qccd.studio', version: 1, arch: null,
      program: { calls: [] }, geom: dgeom, seed: dseed, post: dpost, edits: [] } };
  }
  if (Array.isArray(doc)) {
    // A BARE LIST REPLACES THE EDIT LOG, so an EMPTY one would silently delete every edit
    // in the current design -- which is what dropping an empty or truncated file looks
    // like. Refuse it by name: "nothing to import" is a better answer than an empty
    // canvas the user did not ask for.
    if (!doc.length) {
      return { ok: false, why: 'that file contains no edits; importing it would delete ' +
                              'the ' + EDITS.length + ' edit(s) in this design' };
    }
    return { ok: true, snap: { kind: 'qccd.studio', version: 1, arch: null,
      program: { calls: [] }, geom: GEOM.slice(), seed: SEED, post: POST.slice(),
      edits: doc } };
  }
  return { ok: false, why: 'not a studio snapshot, an .arch.json, or a list of edit ops' };
}

// VALIDATE, THEN TAKE A BACKUP, THEN APPLY -- and put the backup back if anything throws.
// `restore()` rolls itself back internally; this outer net covers `restore()` being handed
// something it cannot even parse. Import must refuse what export would refuse to write.
function importText(text) {
  var got = snapshotOf(text);
  if (!got.ok) return { ok: false, problems: [{ message: got.why }] };
  var backup = snapshot();
  var r;
  try { r = restore(got.snap); }
  catch (err) {
    try { restore(backup); } catch (e2) { /* nothing left to try */ }
    return { ok: false, problems: [{ message: 'import failed: ' + err.message }] };
  }
  if (!r.ok) { try { restore(backup); } catch (e3) {} return r; }
  autosave();
  return r;
}

function readDropped(file, done) {
  if (typeof FileReader === 'undefined') { done({ ok: false,
    problems: [{ message: 'this browser cannot read a dropped file' }] }); return; }
  var fr = new FileReader();
  fr.onload = function () { done(importText(String(fr.result))); };
  fr.onerror = function () { done({ ok: false,
    problems: [{ message: 'could not read ' + file.name }] }); };
  fr.readAsText(file);
}

// ---- file out ---------------------------------------------------------------------
//
// `showSaveFilePicker` gives a RETAINED HANDLE, so the second save writes back to the same
// file instead of dropping another copy in Downloads -- the affordance CircuitVerse does
// not have. Feature-detected the way `navigator.clipboard` already is, with a plain
// download as the fallback and the clipboard as the path that works in a sandbox.
function saveText(text, suggested) {
  if (typeof showSaveFilePicker === 'function') {
    return showSaveFilePicker({ suggestedName: suggested,
        types: [{ description: 'QCCD studio', accept: { 'application/json': ['.json'] } }] })
      .then(function (h) { FILE_HANDLE = h; return writeHandle(text); });
  }
  downloadText(text, suggested);
  return Promise.resolve({ ok: true, how: 'download' });
}
function writeHandle(text) {
  if (!FILE_HANDLE) return Promise.resolve({ ok: false, how: 'no handle' });
  return FILE_HANDLE.createWritable().then(function (w) {
    return w.write(text).then(function () { return w.close(); });
  }).then(function () { LAST_SAVED = text; return { ok: true, how: 'handle' }; });
}
function downloadText(text, name) {
  if (typeof Blob === 'undefined' || typeof URL === 'undefined' ||
      !URL.createObjectURL || !document.createElement) return false;
  var a = document.createElement('a');
  var url = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
  a.href = url; a.download = name; a.style.display = 'none';
  if (document.body && document.body.append) document.body.append(a);
  if (a.click) a.click();
  setTimeout(function () { try { URL.revokeObjectURL(url); } catch (e) {} }, 4000);
  return true;
}
// Ctrl+S writes back to the file you opened, if there is one, and otherwise asks once.
function saveProject() {
  var text = JSON.stringify(snapshot(), null, 1);
  var name = ((STATE && STATE.name) || 'design') + '.studio.json';
  return FILE_HANDLE ? writeHandle(text) : saveText(text, name);
}


// ------------------------------------------------------------------- the studio chrome
//
// Every panel below is RENDERED FROM STATE, never mutated in place, so a rebuild and a
// first paint take the same path -- the same discipline `renderSide` already follows.

function esc2(t) {
  return String(t === undefined || t === null ? '' : t)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// THE START GALLERY.  Step 1 is the PHYSICS PACKAGE, not the shape: `Machine.blank(<gen>)`
// declares no zone types, no curves and no control block, so it produces a machine that
// cannot trap an ion, cannot be priced, and (until the export boundary started consulting
// `check_structure`) exported a file Python refused while reporting 576 DACs.  A template
// seed produces a real machine, so that is what the gallery offers first.
function renderStart() {
  var host = $('palStartBody');
  if (!host) return;
  var stems = Q.templates(), i, h = '';
  h += '<div class="cards">';
  for (i = 0; i < stems.length; i++) {
    h += '<button class="card2" data-tpl="' + esc2(stems[i]) + '">' + esc2(stems[i]) + '</button>';
  }
  h += '</div>';
  h += '<div class="fieldrow"><label>name</label>' +
       '<input id="palName" value="' + esc2(STATE ? STATE.name : 'design') + '"></div>';
  h += '<div class="cards" style="margin-top:5px">' +
       '<button class="card2" id="palBlank">Blank canvas</button>';
  var gens = Q.generators();
  for (i = 0; i < gens.length; i++) {
    h += '<button class="card2" data-gen="' + esc2(gens[i]) + '">' + esc2(gens[i]) + '</button>';
  }
  h += '</div>';
  h += '<div class="mut" style="margin-top:5px;font-size:11px">a blank canvas borrows the ' +
       'selected package’s curves, zone types and control block, because a device with ' +
       'no <code>shuttle_segment</code> curve cannot be priced at all.</div>';
  host.innerHTML = h;
  var kids = host.querySelectorAll ? host.querySelectorAll('button') : [];
  for (i = 0; i < (kids.length || 0); i++) wireStartButton(kids[i]);
}

var PICKED_TEMPLATE = null;
function wireStartButton(b) {
  if (!b || !b.addEventListener) return;
  b.addEventListener('click', function () {
    var tpl = b.getAttribute('data-tpl'), gen = b.getAttribute('data-gen');
    var nm = ($('palName') && $('palName').value) || 'design';
    if (tpl) { PICKED_TEMPLATE = tpl; toast('ok', 'physics package: ' + tpl); renderStart(); return; }
    var r;
    if (gen) {
      var sig = (D.generator_signatures || {})[gen] || { required: [], defaults: {} };
      var params = {};
      for (var k in sig.defaults) if (has(sig.defaults, k)) params[k] = sig.defaults[k];
      // `generator_defaults` drops every REQUIRED positional, so a gallery built from it
      // alone constructs an illegal call for four of the six generators.  The shipped
      // `generator_signatures` names them and the palette supplies a starting value.
      for (var j = 0; j < sig.required.length; j++) params[sig.required[j]] = 4;
      r = newFromGenerator(gen, params, { name: nm, template: PICKED_TEMPLATE });
    } else {
      r = newCanvas({ name: nm, template: PICKED_TEMPLATE });
    }
    if (!r.ok) toast('bad', (r.problems[0] || {}).message || 'refused');
    else toast('ok', gen ? ('new ' + gen) : 'blank canvas');
  });
}

// ===================================================================== ELEMENT AVATARS
//
// AN AVATAR IS NOT AN ICON.  It is a 64x40 WINDOW ONTO A REAL STAGE: a micro-device laid
// out by `QCCD.computeLayout` -- the same mirror of `layout.py` the page itself uses --
// and drawn by `buildStatic`, the very function that draws the device you are editing.
// There is no second renderer anywhere in this file, so an avatar CANNOT drift from what
// dropping the element actually produces.
//
// This codebase has been bitten expensively by the alternative: a JS operand renderer
// beside a Python one made the program panel's search text differ from what the user
// could see, on 3,830 of 3,830 rows.  A hand-drawn menu picture is that mistake again,
// one level up -- and the failure mode is worse, because a picture that lies looks fine.
//
// Every micro-scene puts its nodes ONE LATTICE STEP APART, which makes the layout's
// PITCH_CAP bind and `g` come out at exactly 72 for all of them: one scale for every
// avatar, derived rather than chosen.  `tests/editor.mjs` asserts that.
var AVW = 64, AVH = 40;

var AV_SCENE = {
  // `nodes`/`segs` lay the scene out; `show` is what is DRAWN (context lays out but stays
  // off the picture), `span` is the crop width in units of g, `layers` picks which of
  // buildStatic's four groups get mounted.
  site: { nodes: [['a', 0, 0]], segs: [], loops: {}, show: ['a'], on: 'a',
          span: 1.55, layers: ['node'] },
  junction: { nodes: [['j', 0, 0], ['w', -1, 0], ['e', 1, 0], ['s', 0, 1]],
              segs: [['s0', 'w', 'j'], ['s1', 'j', 'e'], ['s2', 'j', 's']], loops: {},
              show: ['j'], on: 'j', span: 1.55, layers: ['seg', 'node'] },
  segment: { nodes: [['a', 0, 0], ['b', 1, 0], ['c', 2, 0]],
             segs: [['s0', 'a', 'b'], ['s1', 'b', 'c']], loops: {}, show: [],
             onSeg: 's0', onSegT: 1.0, span: 1.55, layers: ['seg', 'elec'] },
  loop: { nodes: [['a', 0, 0], ['b', 1, 0], ['c', 1, 1], ['d', 0, 1]],
          segs: [['s0', 'a', 'b'], ['s1', 'b', 'c'], ['s2', 'c', 'd'], ['s3', 'd', 'a']],
          loops: { '': ['a', 'b', 'c', 'd'] }, show: ['a', 'b', 'c', 'd'], onBox: true,
          span: 2.60, layers: ['loop', 'seg', 'node'] }
};

// A COMPONENT'S AVATAR IS ITS OWN RECORDS.  The catalogue already travels as the
// builder calls that construct each part (`D.components[name].records`), so the menu
// picture can be laid out from the very statements the stamp will replay -- no second
// description of the shape, and therefore nothing that can drift from what dropping it
// produces.  `linear_register(n=8)` is eight sites long in the menu because it is eight
// sites long, not because a picture was drawn with eight dots.
function componentScene(name) {
  var spec = componentSpec(name);
  if (!spec || !spec.records) return null;
  var nodes = [], segs = [], loops = {}, show = [], i;
  for (i = 0; i < spec.records.length; i++) {
    var r = spec.records[i], m = String(r.method);
    var a = r.args || [], kw = r.kwargs || {};
    if (m === 'd.site' || m === 'd.junction') {
      nodes.push([String(a[0]), Number(Q.unbox(a[1])), Number(Q.unbox(a[2])),
                  m === 'd.junction' ? 'junction' : 'site',
                  kw.zone === undefined ? null : String(Q.unbox(kw.zone)),
                  kw.capacity === undefined ? 1 : Number(Q.unbox(kw.capacity))]);
      show.push(String(a[0]));
    } else if (m === 'd.segment') {
      segs.push([String(a[0]), String(a[1]), String(a[2])]);
    } else if (m === 'd.loop') {
      var walk = [];
      for (var j = 0; j < (a[1] || []).length; j++) walk.push(String(Q.unbox(a[1][j])));
      loops[String(a[0])] = walk;
    }
  }
  if (!nodes.length) return null;
  // THE PINS ARE THE POINT, for two of the seven. `trap_junction` is a SINGLE junction
  // node -- its four arms are pins, not geometry -- and `gate_zone` is a single site with
  // a west and an east pin. Drawn from the records alone they are a bare dot, which hides
  // the only thing that makes a 4-way crossing a 4-way crossing. So the pins are drawn,
  // DASHED and short: a pin is an attachment point the stamp does not create, and a solid
  // stub would be the avatar promising geometry that never arrives.
  var pins = [];
  for (i = 0; i < (spec.pins || []).length; i++) {
    var pn = spec.pins[i], dv = pn.dir || [0, 0];
    pins.push({ node: String(pn.node), dx: Number(dv[0]) || 0, dy: Number(dv[1]) || 0,
                name: String(pn.name || '') });
  }
  // `fit` crops to what was actually drawn rather than to a span someone guessed, so a
  // 12-site loop and a 2-site dock are both whole and both at their true relative size.
  return { nodes: nodes, segs: segs, loops: loops, show: show, fit: true, pins: pins,
           layers: ['loop', 'seg', 'pin', 'node'] };
}

function elementAvatar(type, opt) {
  opt = opt || {};
  var D0 = opt.scene || AV_SCENE[type];
  if (!D0) return null;
  var cap = opt.cap === undefined ? 2 : opt.cap;
  var zone = opt.zone === undefined ? 'data' : opt.zone;
  var role = opt.role || 'rail';
  var i, k;

  // 1. THE SAME LAYOUT ENGINE the stage uses.  Measured: g === 72 for every scene here.
  var lnodes = [], lsegs = [];
  for (i = 0; i < D0.nodes.length; i++) {
    lnodes.push({ id: D0.nodes[i][0], x: D0.nodes[i][1], y: D0.nodes[i][2] });
  }
  for (i = 0; i < D0.segs.length; i++) {
    lsegs.push({ id: D0.segs[i][0], a: D0.segs[i][1], b: D0.segs[i][2] });
  }
  var LA = Q.computeLayout(lnodes, lsegs);
  var pxA = function (q) { return LA.ox + q.x * LA.sx; };
  var pyA = function (q) { return LA.oy + q.y * LA.sy; };

  // 2. the DRAWING SHAPE `A.nodes` already is -- the same keys `syncArch` writes
  var byId = {}, all = [], nodes = [];
  for (i = 0; i < D0.nodes.length; i++) {
    var pnt = D0.nodes[i];
    // A scene may name each node's own kind (a component has junctions AND sites); the
    // single-element scenes say nothing and keep the old rule.
    var isJ = pnt[3] ? (pnt[3] === 'junction') : (type === 'junction' && pnt[0] === 'j');
    var deg = 0;
    for (k = 0; k < D0.segs.length; k++) {
      if (D0.segs[k][1] === pnt[0] || D0.segs[k][2] === pnt[0]) deg++;
    }
    // `deg` and `corner` are DRAWN DIFFERENCES, not decoration: a site of degree 3 or
    // more is a dock and gets the gold stroke, a corner gets the corner colour, and both
    // get 1.7x the stroke width.  The menu shows a plain site because that is what
    // dropping one gives you -- but the parameters exist so the harness can ask for the
    // avatar of a node that IS a dock and diff it against that node's own stage mark.
    var isOn = (D0.show.indexOf(pnt[0]) >= 0);
    var n = { id: pnt[0], x: pnt[1], y: pnt[2], kind: isJ ? 'junction' : 'site',
              zone: isJ ? null : (pnt[4] === undefined || pnt[4] === null ? zone : pnt[4]),
              cap: isJ ? 0 : (pnt[5] === undefined ? cap : pnt[5]),
              deg: (isOn && opt.deg !== undefined) ? opt.deg : deg,
              corner: !!(isOn && opt.corner),
              labels: (opt.labels || []).slice(), cap_explicit: true };
    byId[pnt[0]] = n; all.push(n);
    if (D0.show.indexOf(pnt[0]) >= 0) nodes.push(n);
  }
  var segments = [], ROLE = {};
  for (i = 0; i < D0.segs.length; i++) {
    var sp = D0.segs[i];
    segments.push({ id: sp[0], a: sp[1], b: sp[2], loop: null,
                    labels: (opt.labels || []).slice(), cap: 1, len: 1,
                    corner_endpoints: 0 });
    // A MAP, exactly the shape `ROLE` is on the stage.  A function here draws every
    // segment as a rail with the wrong stroke width and looks perfectly plausible,
    // because `buildStatic` does `ROLE[sg.id] || 'rail'`.
    ROLE[sp[0]] = role;
  }

  // 3. the trap axis, by the SAME derivation `rebuildAxis()` uses
  var AX = axisOf(all, segments, pxA, pyA, byId);

  // 4. throwaway groups and registries -- nothing on the stage is touched
  var into = { loop: el('g', {}), seg: el('g', {}), elec: el('g', {}), node: el('g', {}),
               pin: el('g', {}) };
  var reg = { SEGEL: {}, SEGINFO: {}, PAD_BY_SEG: {}, SEG_BY_PAIR: {},
              NODEEL: {}, CAPTXT: {} };

  // >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>> THE ONE CALL <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
  buildStatic({ A: { nodes: nodes, segments: segments, loops: D0.loops },
                L: LA, AXIS: AX, role: ROLE, px: pxA, py: pyA, byId: byId,
                into: into, reg: reg });
  // >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>><<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<

  // one pad energized, so "control plane" shows what it physically means: the electrodes
  // and the DACs that ramp them.  The stage's own `lastHot` mark, not a second one.
  if (opt.hot) {
    var pads = reg.PAD_BY_SEG[D0.onSeg || 's0'] || [];
    for (i = 0; i < pads.length; i++) {
      if (Math.abs(pads[i].t - 0.5) < 0.2) {
        pads[i].el.setAttribute('fill', C.dc_hot);
        pads[i].el.setAttribute('opacity', 0.95);
      }
    }
  }

  // 4b. the pin stubs. Half a lattice step, dashed, in the muted line colour -- legible
  // as "something attaches here", and not confusable with the solid rail `buildStatic`
  // draws one group below.
  if (D0.pins && D0.pins.length) {
    for (i = 0; i < D0.pins.length; i++) {
      var pin = D0.pins[i], host = byId[pin.node];
      if (!host) continue;
      var L = Math.sqrt(pin.dx * pin.dx + pin.dy * pin.dy) || 1;
      var sx0 = pxA(host), sy0 = pyA(host);
      var ux = pin.dx / L, uy = pin.dy / L;
      var r0 = LA.g * 0.30, r1 = LA.g * 0.78;
      into.pin.append(el('line', {
        x1: +(sx0 + ux * r0).toFixed(2), y1: +(sy0 + uy * r0).toFixed(2),
        x2: +(sx0 + ux * r1).toFixed(2), y2: +(sy0 + uy * r1).toFixed(2),
        stroke: C.line, 'stroke-width': Math.max(2, LA.g * 0.055),
        'stroke-dasharray': (LA.g * 0.10).toFixed(1) + ' ' + (LA.g * 0.085).toFixed(1),
        'stroke-linecap': 'round', opacity: 0.85 }));
    }
  }

  // 5. the crop: a window in units of g, so every avatar is at one scale
  var w = D0.span * LA.g, h = w * AVH / AVW, cx, cy;
  if (D0.fit) {
    // THE CROP IS MEASURED, NOT CHOSEN. Take the drawn extent, pad by half a lattice
    // step so a site's own radius is not clipped, then letterbox to the tile's aspect --
    // the same `xMidYMid meet` discipline the stage uses, and for the same reason: any
    // other fit silently cuts one axis.
    var fx = [], fy = [];
    for (i = 0; i < all.length; i++) { fx.push(pxA(all[i])); fy.push(pyA(all[i])); }
    // the stubs are part of the picture, so they are part of the extent
    for (i = 0; i < ((D0.pins && D0.pins.length) ? D0.pins.length : 0); i++) {
      var q = D0.pins[i], qh = byId[q.node];
      if (!qh) continue;
      var qL = Math.sqrt(q.dx * q.dx + q.dy * q.dy) || 1;
      fx.push(pxA(qh) + (q.dx / qL) * LA.g * 0.78);
      fy.push(pyA(qh) + (q.dy / qL) * LA.g * 0.78);
    }
    var x0 = Math.min.apply(null, fx), x1 = Math.max.apply(null, fx);
    var y0 = Math.min.apply(null, fy), y1 = Math.max.apply(null, fy);
    var pad = LA.g * 0.62;
    w = (x1 - x0) + 2 * pad;
    h = (y1 - y0) + 2 * pad;
    if (w / h < AVW / AVH) w = h * AVW / AVH; else h = w * AVH / AVW;
    cx = (x0 + x1) / 2; cy = (y0 + y1) / 2;
  } else if (D0.onBox) {
    var xs = [], ys = [];
    for (i = 0; i < all.length; i++) { xs.push(pxA(all[i])); ys.push(pyA(all[i])); }
    cx = (Math.min.apply(null, xs) + Math.max.apply(null, xs)) / 2;
    cy = (Math.min.apply(null, ys) + Math.max.apply(null, ys)) / 2;
  } else if (D0.onSeg) {
    var I = reg.SEGINFO[D0.onSeg];
    cx = I.ax + I.dx * (D0.onSegT === undefined ? 0.5 : D0.onSegT);
    cy = I.ay + I.dy * (D0.onSegT === undefined ? 0.5 : D0.onSegT);
  } else {
    cx = pxA(byId[D0.on]); cy = pyA(byId[D0.on]);
  }
  var vb = [cx - w / 2, cy - h / 2, w, h];
  for (i = 0; i < 4; i++) vb[i] = +vb[i].toFixed(2);
  var svgEl = el('svg', { 'class': 'av', width: AVW, height: AVH, 'aria-hidden': 'true',
                          preserveAspectRatio: 'xMidYMid meet', viewBox: vb.join(' ') });
  for (i = 0; i < D0.layers.length; i++) svgEl.append(into[D0.layers[i]]);
  return svgEl;
}

// ---- the three elements with NO stage depiction get no fake stage depiction ----------
//
// A curve, a heating rate and a budget never appear on the canvas.  Inventing a device
// mark for them would be the same lie in the other direction, so these are charts over
// SHIPPED DATA, they draw no device geometry, and they sit inside a dashed tile so
// nothing suggests they can be dropped anywhere.
function avSvg() {
  return el('svg', { 'class': 'av', width: AVW, height: AVH, 'aria-hidden': 'true',
                     viewBox: '0 0 64 40', preserveAspectRatio: 'xMidYMid meet' });
}
function curveGlyph(name, opt) {
  opt = opt || {};
  var pts = ((D.physics && D.physics.curves) || {})[name] || [];
  var sv = avSvg(), i;
  if (!pts.length) return sv;
  var xs = [], ys = [];
  for (i = 0; i < pts.length; i++) { xs.push(+pts[i].us); ys.push(+pts[i].quanta); }
  var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
  var y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
  var X = function (v) { return 8 + (x1 > x0 ? (v - x0) / (x1 - x0) : 0.5) * 48; };
  var Y = function (v) { return 32 - (y1 > y0 ? (v - y0) / (y1 - y0) : 0.5) * 22; };
  sv.append(el('line', { x1: 7, y1: 33, x2: 60, y2: 33, stroke: C.line, 'stroke-width': 1 }));
  sv.append(el('line', { x1: 7, y1: 33, x2: 7, y2: 7, stroke: C.line, 'stroke-width': 1 }));
  var d = [];
  for (i = 0; i < pts.length; i++) d.push((i ? 'L' : 'M') + ' ' + X(xs[i]) + ' ' + Y(ys[i]));
  sv.append(el('path', { d: d.join(' '), fill: 'none', stroke: C.accent, 'stroke-width': 1.6,
                         'stroke-linejoin': 'round' }));
  for (i = 0; i < pts.length; i++) {
    var fill = opt.dot ? (i === 2 ? C.accent : C.panel) : C.panel;
    sv.append(el('circle', { cx: X(xs[i]), cy: Y(ys[i]), r: 2.1, fill: fill,
                             stroke: C.accent, 'stroke-width': 1.2 }));
  }
  return sv;
}
function heatRamp() {
  // THE PAGE'S OWN `heat(q)`, the identical function the "colour: heating" stage mode
  // uses -- so this swatch cannot mean something different from the stage.
  var sv = avSvg(), thr = (PH && PH.gate_threshold) || 1, i;
  var qs = [0, 0.5, 2, 8, 32, 64];
  for (i = 0; i < qs.length; i++) {
    sv.append(el('rect', { x: 5 + i * 9, y: 11, width: 8.4, height: 18, rx: 1.5,
      fill: (typeof heat === 'function') ? heat(qs[i] * thr) : C.cold }));
  }
  sv.append(el('line', { x1: 5 + 2 * 9, y1: 8, x2: 5 + 2 * 9, y2: 32, stroke: C.ink,
                         'stroke-width': 1, opacity: 0.55 }));
  return sv;
}
function meterGlyph() {
  var sv = avSvg();
  var hw = HW || (A.hardware || {}), b = (STATE && STATE.budget) || {};
  var cap = +b.max_dacs || 0, used = +hw.dacs || 0;
  var f = cap > 0 ? Math.max(0, Math.min(1, used / cap)) : 0;
  sv.append(el('rect', { x: 6, y: 15, width: 52, height: 10, rx: 5, fill: 'none',
                         stroke: C.line, 'stroke-width': 1.4 }));
  if (f > 0) sv.append(el('rect', { x: 6, y: 15, width: 52 * f, height: 10, rx: 5,
                                    fill: C.navy, opacity: 0.85 }));
  sv.append(el('line', { x1: 58, y1: 12, x2: 58, y2: 28, stroke: C.z, 'stroke-width': 1.6 }));
  return sv;
}

// EVERY palette type, dispatched.  Eight of the eleven are drawn by the stage's own
// `buildStatic`; the other three are charts over shipped data and say so by being dashed.
function kindAvatar(e, opt) {
  opt = opt || {};
  var zt = (A.zone_types || {}), df = (D.defaults || {});
  switch (e.type) {
    case 'site':      return elementAvatar('site',
                        { cap: opt.cap !== undefined ? opt.cap
                               : ((zt.data && zt.data.capacity) || 2),
                          zone: opt.zone || 'data' });
    case 'junction':  return elementAvatar('junction', {});
    case 'segment':   return elementAvatar('segment', { role: opt.role || 'rail' });
    case 'loop':      return elementAvatar('loop', {});
    case 'zone_type': return elementAvatar('site',
                        { cap: opt.cap !== undefined ? opt.cap
                               : ((df.new_zone_type || {}).capacity || 1),
                          zone: opt.zone || 'trap' });
    case 'control':   return elementAvatar('segment', { role: 'rail', hot: 1 });
    case 'species':   return elementAvatar('site', { cap: 2, zone: 'data' });
    case 'heating':   return heatRamp();
    case 'curve_point': return curveGlyph(opt.curve || 'shuttle_segment', { dot: 1 });
    case 'primitives':  return curveGlyph('shuttle_segment', { family: 1 });
    case 'budget':      return meterGlyph();
  }
  if (String(e.type).slice(0, 4) === 'cmp:') {
    var sc = componentScene(String(e.type).slice(4));
    return sc ? elementAvatar(null, { scene: sc }) : avSvg();
  }
  return avSvg();
}

// An avatar is ~0.5-3.4 KB of SVG and `renderPalette()` runs on every `paint()`, which
// runs on every edit -- so a drag would re-mint eleven SVG trees per pointermove.  Keyed
// by everything that can change the picture, and by nothing that cannot.
var AVCACHE = {};
function cachedAvatar(e, opt) {
  // THE SELECTION IS PART OF THE PICTURE. Without it in the key the tile keeps serving
  // the default drawing forever and the form looks broken in the most confusing way:
  // the number changes and the picture does not.
  var cn = (String(e.type).slice(0, 4) === 'cmp:') ? String(e.type).slice(4) : null;
  var key = e.type + '|' + (cn ? selKey(cn) : '') + '|' + (opt ? JSON.stringify(opt) : '');
  if (!AVCACHE[key]) AVCACHE[key] = kindAvatar(e, opt || {});
  return AVCACHE[key];
}

// The avatar as MARKUP, so a headless harness can assert that it is the stage's own mark
// rather than a look-alike.  Works against the shim's plain objects and a real DOM alike.
function markup(n) {
  if (!n || !n.tagName) return '';
  var a = [], k, i;
  if (n.attrs) { for (k in n.attrs) if (has(n.attrs, k)) a.push(k + '=' + n.attrs[k]); }
  else if (n.attributes) {
    for (i = 0; i < n.attributes.length; i++) a.push(n.attributes[i].name + '=' + n.attributes[i].value);
  }
  a.sort();
  var kids = [], ch = n.children || [];
  for (i = 0; i < ch.length; i++) kids.push(markup(ch[i]));
  return '<' + n.tagName + (a.length ? ' ' + a.join(' ') : '') + '>' + kids.join('') +
         '</' + n.tagName + '>';
}
// the stage's own mark for one element, so the two can be diffed attribute for attribute
function stageMark(id) {
  var rec = NODEEL[id];
  if (rec) return rec.el;
  return SEGEL[id] || null;
}

// ===================================================================== COMPONENTS
//
// A component is a bundle of geometry records shipped with the page (`D.components`, from
// `render.py::_component_registry`, itself from `arch/library.py`). Placing one is PURE
// SUBSTITUTION -- rename every id it defines, translate, quarter-turn -- which is why this
// is twenty lines and not a subsystem: the records it produces are in the vocabulary the
// interpreter already implements, so there is no new verb and nothing for the mirror to
// drift from.
//
// `translate_point` here must agree with `arch/component.py::translate_point` exactly, and
// does so by construction: swaps and negations, no trigonometry. Arbitrary rotation is NOT
// offered, because one of twenty-four measured cos/sin values differs by an ulp between
// CPython and V8 and `layout.py` records that 2-5 ulp flips a segment's bow sign.

var CMP = (D.components || {});
var CMP_N = 0;

// ---- LIVE PARAMETERS -------------------------------------------------------------
//
// `CMPSEL[name]` is what the palette form is currently showing: `dim` for the enumerated
// parameters (a precomputed variant is selected by them) and `slot` for the rest (applied
// by `Q.resolveVariant` with one multiply or one substitution). `componentSpec` is the
// ONE place a component's current geometry comes from, so the avatar in the menu and the
// records a click stamps are literally the same object -- they cannot disagree.
var CMPSEL = {};

function cmpSel(name) {
  if (!CMPSEL[name]) {
    var vb = CMP[name] && CMP[name]['var'], dim = {}, slot = {}, p;
    if (vb && vb.params) {
      for (p in vb.params) {
        if (!has(vb.params, p)) continue;
        var m = vb.params[p];
        if (m.kind === 'dim') dim[p] = m['default'];
        else if (m.kind === 'slot') slot[p] = m['default'];
      }
    }
    CMPSEL[name] = { dim: dim, slot: slot };
  }
  return CMPSEL[name];
}

function selKey(name) {
  var c = CMP[name];
  if (!c || !c['var']) return '';
  return JSON.stringify(cmpSel(name));
}

// A component whose table is absent (a future one whose classification failed) degrades
// to its shipped default records rather than disappearing from the menu.
function componentSpec(name) {
  var c = CMP[name];
  if (!c) return null;
  if (!c['var']) return c;
  var sel = cmpSel(name), r;
  try {
    r = Q.resolveVariant(c['var'], sel.dim, sel.slot);
  } catch (err) {
    return c;
  }
  r.name = name;
  r.params = c.params;
  return r;
}

function setComponentParam(name, param, value) {
  var c = CMP[name];
  if (!c || !c['var'] || !c['var'].params || !has(c['var'].params, param)) {
    return { ok: false, problems: [{ code: 'no_param',
      message: 'component ' + Q.pyRepr(name) + ' has no parameter ' + Q.pyRepr(param) }] };
  }
  var meta = c['var'].params[param];
  if (meta.kind === 'inert') {
    return { ok: false, problems: [{ code: 'inert_param', message: meta.why }] };
  }
  var sel = cmpSel(name);
  var where = (meta.kind === 'dim') ? 'dim' : 'slot';
  var was = sel[where][param];
  sel[where][param] = value;
  // REFUSE AT THE KEYSTROKE, not at the draw. `computeLayout` throws past COORD_MAX and
  // `renderPalette` is outside `paint()`'s try/catch, so an accepted bad value does not
  // merely look wrong -- it aborts the rest of the bar and keeps doing so, because the
  // value persists. The bound is shipped data, never a limit retyped here.
  var bad = null;
  try {
    bad = Q.variantGuard(c['var'], sel.dim, sel.slot);
  } catch (err) {
    bad = { code: 'out_of_range', message: String(err && err.message ? err.message : err) };
  }
  if (bad) {
    sel[where][param] = was;
    return { ok: false, problems: [bad] };
  }
  AVCACHE = {};
  paint();
  return { ok: true, value: value, spec: componentSpec(name) };
}

function cmpTranslate(x, y, dx, dy, q) {
  q = ((q | 0) % 4 + 4) % 4;
  var t;
  if (q === 1) { t = x; x = -y; y = t; }
  else if (q === 2) { x = -x; y = -y; }
  else if (q === 3) { t = x; x = y; y = -t; }
  return [x + dx, y + dy];
}

function cmpInstantiate(spec, inst, dx, dy, quarter, extraLabels) {
  var local = {}, i, r;
  for (i = 0; i < spec.records.length; i++) {
    var a0 = (spec.records[i].args || [])[0];
    if (a0 !== undefined) local[String(a0)] = 1;
  }
  var pre = inst + '.';
  var ren = function (v) { return has(local, String(v)) ? pre + String(v) : v; };

  var out = [];
  for (i = 0; i < spec.records.length; i++) {
    r = spec.records[i];
    var m = String(r.method), args = (r.args || []).slice(), kw = {}, k;
    for (k in (r.kwargs || {})) if (has(r.kwargs, k)) kw[k] = r.kwargs[k];

    if (m === 'd.site' || m === 'd.junction') {
      var p2 = cmpTranslate(Number(Q.unbox(args[1])), Number(Q.unbox(args[2])), dx, dy, quarter);
      args = [ren(args[0]), Q.pyFloat(p2[0]), Q.pyFloat(p2[1])];
    } else if (m === 'd.segment') {
      args = [ren(args[0]), ren(args[1]), ren(args[2])];
      if (kw.loop) kw.loop = ren(kw.loop);          // `loop=` names a loop: it is an id
    } else if (m === 'd.loop') {
      var walk = (args[1] || []).map(ren);
      args = [ren(args[0]), walk];
    }
    if (m !== 'd.loop') {                            // a loop takes no labels
      var labs = (kw.labels || []).slice();
      labs.push('cmp:' + inst);
      // AND WHICH COMPONENT, AT WHICH VARIANT. Without this, a placed instance's identity
      // has to be guessed from its node ids -- which is what `pinNode` used to do, and it
      // cannot tell `ancilla_dock` from `trap_junction` because both call their first pin
      // node 'j'. The label carries integers only, so no float formatting is involved.
      for (var li = 0; li < (extraLabels || []).length; li++) labs.push(extraLabels[li]);
      kw.labels = labs;
    }
    out.push({ method: m, args: args, kwargs: kw, meta: { group: 'cmp', src: 'palette' } });
  }
  return out;
}

// Place `name` with its top-left at (x, y) in DEVICE units.  One transaction, so a
// component that cannot be placed leaves nothing behind and costs one undo.
function stampComponent(name, x, y, quarter) {
  var spec = componentSpec(name);
  if (!spec) {
    return { ok: false, problems: [{ code: 'no_component',
      message: 'no component ' + Q.pyRepr(name) + '; have: ' + Object.keys(CMP).sort().join(', ') }] };
  }
  var need = (spec.requires && spec.requires.zones) || [];
  var missing = [];
  for (var i = 0; i < need.length; i++) {
    if (!STATE || !STATE.zone_types || !has(STATE.zone_types, need[i])) missing.push(need[i]);
  }
  if (missing.length) {
    // NAME WHAT IS MISSING AND WHAT TO DO. A component that needs a zone the machine has
    // not declared is a real refusal, not a mystery: the site would resolve to capacity 0.
    return { ok: false, problems: [{ code: 'missing_zone', targets: missing,
      message: name + ' places sites in zone ' + Q.pyRepr(missing[0]) +
               ', which this machine does not declare -- add it from Elements first' }] };
  }
  var vb = CMP[name] && CMP[name]['var'];
  if (vb) {
    var bad = null;
    try {
      bad = Q.variantGuard(vb, cmpSel(name).dim, cmpSel(name).slot);
    } catch (err) {
      bad = { code: 'out_of_range', message: String(err && err.message ? err.message : err) };
    }
    // REFUSE BEFORE THE TRANSACTION. `transaction` trials with `applyProgram`, which has
    // no range check, and calls `rebuild()` AFTER committing -- so a value the layout
    // cannot measure would be in the document before anything threw.
    if (bad) return { ok: false, problems: [bad] };
  }
  // THE FIRST LOCAL ID, not the literal 'a'. Exactly one of the seven components defines
  // a node called 'a', so the old probe was checking a name six of them never use.
  var probe = String(((spec.records[0] || {}).args || [])[0] || 'a');
  var inst = 'c' + (++CMP_N);
  while (STATE && STATE.device && has(STATE.device.nodes, inst + '.' + probe)) {
    inst = 'c' + (++CMP_N);
  }
  var labels = vb ? [Q.variantLabel(name, vb, cmpSel(name).dim)] : [];
  var ops = cmpInstantiate(spec, inst, x, y, quarter || 0, labels);
  var r = transaction(ops.map(function (o) { return { build: o }; }), 'place ' + name);
  r.instance = inst;
  return r;
}

// ---- PINS: what turns placed parts into an assembled machine -------------------
//
// A component declares PINS -- named nodes it expects to be joined to something else. An
// `ancilla_dock` sitting next to a rail is two disconnected pieces; joined at its `rail`
// pin it is a dock, and the rail node becomes degree 3, which is what makes the cost model
// charge a junction on every rigid hop through it (R18). The difference between placing
// parts and assembling a machine is exactly this call.
//
// A pin is joined with an ORDINARY SEGMENT, not a merge: two nodes that coincide would
// make `min_nearest_neighbour` measure the gap off the next pair and silently resize every
// mark on the stage, which `addNodeAt` already refuses for the same reason.
// WHICH COMPONENT, AND AT WHICH VARIANT. This used to recover the answer by probing for
// `inst + '.' + CMP[k].pins[0].node` and taking the first catalogue entry that matched --
// which is wrong twice over. `ancilla_dock` and `trap_junction` BOTH call that node 'j'
// and `CMP` is in sorted order, so every pin of a placed `trap_junction` resolved against
// `ancilla_dock` and came back `no_pin`: the component whose whole purpose is "attach all
// four arms" could not be attached at all. And once `n` is live, `linear_register`'s east
// pin is `s{n-1}` rather than `s7`, so the probe would have welded a rail to a node that
// EXISTS -- passing every existence check and putting the R18 junction charge silently in
// the wrong place.
//
// The label a stamp writes says both, so nothing is guessed.
function variantOf(inst) {
  var mem = instanceMembers(inst), i, j;
  for (i = 0; i < mem.length; i++) {
    var d = (mem[i].kind === 'site') ? STATE.device.nodes : STATE.device.segments;
    var labs = (d[mem[i].id] || {}).labels || [];
    for (j = 0; j < labs.length; j++) {
      var parsed = Q.parseVariantLabel(labs[j]);
      if (parsed && CMP[parsed.name]) return parsed;
    }
  }
  return null;
}

function pinNode(inst, pin) {
  var spec = null, k;
  var v = variantOf(inst);
  if (v) {
    var vb = CMP[v.name]['var'];
    if (vb) {
      var row = Q.variantRow(vb, v.sel);
      if (row) spec = { pins: vb.pins_pool[row[1]] };
    }
    if (!spec) spec = CMP[v.name];
  }
  if (!spec) {
    // A document saved before components carried their variant. Best effort, and it is
    // the ambiguous path described above -- kept so old files still open.
    for (k in CMP) if (has(CMP, k)) {
      if (STATE && STATE.device &&
          has(STATE.device.nodes, inst + '.' + (CMP[k].pins[0] || {}).node)) {
        spec = CMP[k]; break;
      }
    }
  }
  if (!spec) return null;
  for (var i = 0; i < spec.pins.length; i++) {
    if (spec.pins[i].name === pin) return inst + '.' + spec.pins[i].node;
  }
  return null;
}

// Join one component's pin to any node. Returns the same shape every edit does.
function joinPin(inst, pin, target, opts) {
  opts = opts || {};
  var from = pinNode(inst, pin);
  if (!from) {
    return { ok: false, problems: [{ code: 'no_pin',
      message: 'instance ' + Q.pyRepr(inst) + ' has no pin ' + Q.pyRepr(pin) }] };
  }
  if (!STATE || !STATE.device || !has(STATE.device.nodes, target)) {
    return { ok: false, problems: [{ code: 'no_such_node',
      message: 'no node ' + Q.pyRepr(target) + ' to join ' + Q.pyRepr(pin) + ' to' }] };
  }
  if (from === target) {
    return { ok: false, problems: [{ code: 'self_join',
      message: 'a pin cannot be joined to itself' }] };
  }
  return joinNodes(from, target, opts);
}

// Place a component AND wire it in, as one undoable act -- because a half-attached dock
// is not a state a user ever wants to be left in.
function attachComponent(name, x, y, quarter, pin, target, opts) {
  var placed = stampComponent(name, x, y, quarter);
  if (!placed.ok) return placed;
  var j = joinPin(placed.instance, pin, target, opts);
  if (!j.ok) {
    // THE PLACEMENT AND THE JOIN ARE ONE ACT, so the rollback must remove the whole
    // placement -- `undo()` pops a SINGLE edit and a component is as many edits as it has
    // records, so it left two thirds of a dock behind. `transaction` already stamps every
    // record of one placement with a group id, for exactly this.
    if (typeof undoGroup === 'function') undoGroup();
    else if (typeof undo === 'function') undo();
    j.problems = (j.problems || []).concat([{ code: 'rolled_back',
      message: 'the component was removed again, because attaching it failed' }]);
    return j;
  }
  j.instance = placed.instance;
  return j;
}

// every node and segment an instance owns, for select- and delete-as-a-unit
function instanceMembers(inst) {
  var lab = 'cmp:' + inst, out = [], k;
  if (!STATE || !STATE.device) return out;
  for (k in STATE.device.nodes) if (has(STATE.device.nodes, k)) {
    if (((STATE.device.nodes[k].labels) || []).indexOf(lab) >= 0) out.push({ kind: 'site', id: k });
  }
  for (k in STATE.device.segments) if (has(STATE.device.segments, k)) {
    if (((STATE.device.segments[k].labels) || []).indexOf(lab) >= 0) out.push({ kind: 'segment', id: k });
  }
  return out;
}
function instanceAt(id) {
  var n = (STATE && STATE.device) ? (STATE.device.nodes[id] || STATE.device.segments[id]) : null;
  var labs = (n && n.labels) || [];
  for (var i = 0; i < labs.length; i++) {
    if (String(labs[i]).indexOf('cmp:') === 0) return String(labs[i]).slice(4);
  }
  return null;
}

// ===================================================================== THE ELEMENT MENU
//
// GENERATED, never a literal list: `palette()` stays the only source of the eleven.  But
// the four `kind`s are not four styles of one control, they are three different ACTIONS,
// and the menu has to say which -- half the user's complaint was arming an element and
// having nothing happen.
var GESTURE = {
  site: 'click an empty spot on the stage',
  junction: 'click an empty spot on the stage',
  segment: 'drag from one node to another',
  loop: 'select nodes, then press Close loop',
  zone_type: 'assign it to sites as you place them',
  curve_point: 'appends a row to a named curve',
  primitives: 'one record; edit it in place',
  control: 'one record; edit it in place',
  heating: 'one record; edit it in place',
  species: 'one record; edit it in place',
  budget: 'one record; edit it in place'
};
var SECTION = { stamp: 'Place on the canvas', named: 'Define, then assign',
                row: 'Append a row', block: 'Machine settings',
                component: 'Components — whole parts' };

function docOf(type) {
  var d = (D.element_docs || {})[type];
  return d || { name: String(type).replace(/_/g, ' '), blurb: '' };
}
function elh(tag, cls) {
  var e = document.createElement(tag);
  if (cls) e.setAttribute('class', cls);
  return e;
}
function paletteEntry(type) {
  var pal = palette();
  for (var i = 0; i < pal.length; i++) if (pal[i].type === type) return pal[i];
  return null;
}

function renderPalette() {
  var host = $('palBody');
  if (!host) return;
  var pal = palette(), by = { stamp: [], named: [], row: [], block: [] }, i;
  for (i = 0; i < pal.length; i++) (by[pal[i].kind] || (by[pal[i].kind] = [])).push(pal[i]);
  host.replaceChildren();
  var order = ['stamp', 'named', 'row', 'block'];
  for (i = 0; i < order.length; i++) {
    if (!by[order[i]] || !by[order[i]].length) continue;
    host.append(paletteSection(order[i], by[order[i]]));
  }
  // A THROW HERE USED TO ABORT THE WHOLE BAR. `paint()` guards only the export box, so
  // an exception from a tile skipped `renderInspector`, `renderWrite` and `renderReport`
  // -- and kept skipping them, because whatever caused it persisted.
  try {
    var cmp = componentSection();
    if (cmp) host.append(cmp);
  } catch (err) {
    var oops = elh('div', 'palgrp');
    var oh = elh('h5');
    oh.textContent = SECTION.component;
    var msg = elh('i', 'pal-why');
    msg.textContent = 'the component menu could not be drawn: ' +
                      String(err && err.message ? err.message : err);
    oops.append(oh, msg);
    host.append(oops);
  }
  var ex = elh('button', 'tool');
  ex.setAttribute('id', 'palExplode');
  ex.textContent = 'explode to explicit…';
  wirePaletteButton(ex);
  host.append(ex);
}

// ---- COMPONENTS: whole parts, not single elements ------------------------------------
//
// The catalogue has been reachable from `stampComponent` since it shipped and invisible
// in the menu, which made it a feature only someone reading the source could find.  Each
// tile is the part's own geometry (see `componentScene`), its own blurb, and -- the part
// that matters -- whether the zones it REQUIRES exist on this device, because
// `stampComponent` refuses without them and a tile that looks placeable but is not is
// exactly the "menu feels broken" failure this rail was rebuilt to end.
function componentBlocked(spec) {
  var need = (spec.requires && spec.requires.zones) || [], zt = A.zone_types || {}, out = [];
  for (var i = 0; i < need.length; i++) if (!zt[need[i]]) out.push(need[i]);
  return out;
}

function componentSection() {
  var names = Object.keys(CMP).sort(), i;
  if (!names.length) return null;
  var sec = elh('div', 'palgrp');
  sec.setAttribute('data-kind', 'component');
  var h = elh('h5');
  h.textContent = SECTION.component;
  sec.append(h);
  var grid = elh('div', 'palgrid');
  for (i = 0; i < names.length; i++) grid.append(componentItem(names[i]));
  sec.append(grid);
  return sec;
}

function componentItem(name) {
  var spec = componentSpec(name), blocked = componentBlocked(spec);
  var b = elh('button', 'pal-item');
  b.setAttribute('data-el', 'cmp:' + name);
  b.setAttribute('data-kind', 'component');
  b.setAttribute('data-add', 'component');
  b.setAttribute('aria-pressed', ARMED_EL === ('cmp:' + name) ? 'true' : 'false');
  if (blocked.length) {
    b.setAttribute('data-blocked', blocked.join(','));
    b.setAttribute('title', name + ' needs the zone type ' + blocked.join(', ') +
                            ', which this device does not have');
  }
  var av = elh('span', 'avatar');
  var pic = cachedAvatar({ type: 'cmp:' + name }, {});
  if (pic) av.append(pic);
  var tx = elh('span', 'pal-text');
  var nm = elh('b'); nm.textContent = name.replace(/_/g, ' '); tx.append(nm);
  var why = elh('i', 'pal-why'); why.textContent = spec.blurb || ''; tx.append(why);
  var how = elh('i', 'pal-how');
  how.textContent = blocked.length ? ('needs zone: ' + blocked.join(', '))
                                   : 'click the canvas to place the whole part';
  tx.append(how);
  var meta = elh('i', 'pal-meta');
  var np = Object.keys(spec.params || {}).length;
  meta.textContent = spec.records.length + ' records · ' + np + ' params · ' +
                     ((spec.pins || []).length) + ' pin' +
                     ((spec.pins || []).length === 1 ? '' : 's');
  tx.append(meta);
  b.append(av, tx);
  wirePaletteButton(b);
  var wrap = elh('div', 'cmp-tile');
  wrap.append(b);
  var form = componentParamForm(name);
  if (form) wrap.append(form);
  return wrap;
}

// ---- THE FORM ----------------------------------------------------------------------
//
// Built from `var.params`, which Python derived from the factory -- so a parameter cannot
// appear here unless the table can actually move it, and cannot be missing if it can.
// A `dim` is a select over the values that were precomputed; a `slot` is a free input,
// because a multiply and a substitution work at any value the guard allows.
function componentParamForm(name) {
  var vb = CMP[name] && CMP[name]['var'];
  if (!vb || !vb.params) return null;
  var names = Object.keys(vb.params).sort();
  if (!names.length) return null;
  var form = elh('div', 'cmp-form'), i;
  for (i = 0; i < names.length; i++) form.append(componentParamRow(name, names[i]));
  return form;
}

function componentParamRow(name, param) {
  var vb = CMP[name]['var'], meta = vb.params[param], sel = cmpSel(name);
  var row = elh('label', 'cmp-row');
  row.setAttribute('data-param', param);
  row.setAttribute('data-kind', meta.kind);
  var lab = elh('span', 'cmp-k');
  lab.textContent = param;
  row.append(lab);

  if (meta.kind === 'inert') {
    var dead = elh('input', 'cmp-v');
    dead.setAttribute('type', 'text');
    dead.setAttribute('disabled', 'disabled');
    dead.setAttribute('value', String(meta['default']));
    dead.setAttribute('title', meta.why);
    row.append(dead);
    var why = elh('i', 'cmp-why');
    why.textContent = 'changes nothing';
    why.setAttribute('title', meta.why);
    row.append(why);
    return row;
  }

  var input;
  if (meta.kind === 'dim') {
    input = elh('select', 'cmp-v');
    for (var j = 0; j < meta.values.length; j++) {
      var o = elh('option');
      o.setAttribute('value', String(meta.values[j]));
      if (meta.values[j] === sel.dim[param]) o.setAttribute('selected', 'selected');
      o.textContent = String(meta.values[j]);
      input.append(o);
    }
  } else {
    input = elh('input', 'cmp-v');
    input.setAttribute('type', 'text');
    input.setAttribute('value', String(sel.slot[param]));
    input.setAttribute('data-type', meta.type);
  }
  wireComponentInput(input, name, param, meta);
  row.append(input);
  return row;
}

// `integer` must not go through `Number`: `Number('1_0')` is NaN where Python reads 10,
// and `Number('0x10')` is 16 where `int()` raises. The table never sees a raw string.
function cmpCoerce(raw, type) {
  var s = String(raw).trim();
  if (type === 'string') return s;
  if (type === 'integer') {
    if (!/^-?\d+$/.test(s)) return null;
    return parseInt(s, 10);
  }
  if (!/^-?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(s)) return null;
  return parseFloat(s);
}

function wireComponentInput(input, name, param, meta) {
  if (!input || !input.addEventListener) return;
  var handler = function () {
    var raw = input.value;
    var v;
    if (meta.kind === 'dim') {
      v = cmpCoerce(raw, 'integer');
    } else if (meta.type === 'string') {
      v = String(raw);
    } else {
      v = cmpCoerce(raw, meta.type);
    }
    if (v === null || v === undefined || (typeof v === 'number' && v !== v)) {
      toast('bad', param + ': ' + Q.pyRepr(String(raw)) + ' is not a ' +
                   (meta.kind === 'dim' ? 'whole number' : meta.type));
      return;
    }
    var r = setComponentParam(name, param, v);
    if (!r.ok) toast('bad', (r.problems[0] || {}).message || 'refused');
  };
  input.addEventListener('change', handler);
  input.addEventListener('input', handler);
}

function paletteSection(kind, items) {
  var sec = elh('div', 'palgrp'), i;
  sec.setAttribute('data-kind', kind);
  var h = elh('h5');
  h.textContent = SECTION[kind] || kind;
  sec.append(h);
  var grid = elh('div', 'palgrid');
  // ONE COLUMN.  A tile is a 64px avatar plus a name, a sentence and the gesture that
  // places it; two of those side by side in a 252px rail leaves 38px for the words.
  for (i = 0; i < items.length; i++) grid.append(paletteItem(items[i]));
  sec.append(grid);
  // THE ZONE TYPES THAT ACTUALLY EXIST, each with its OWN site-bar avatar in its own
  // colour at its own capacity -- because that is the only thing a zone type ever draws.
  // `load` (cap 8) is visibly a longer bar than `data` (cap 2), which is the fact.
  if (kind === 'named') sec.append(zoneStrip());
  return sec;
}

function paletteItem(e) {
  var inert = 0, j;
  for (j = 0; j < e.fields.length; j++) if (e.fields[j].inert) inert++;
  var doc = docOf(e.type);
  var b = elh('button', 'pal-item');
  b.setAttribute('data-el', e.type);
  b.setAttribute('data-kind', e.kind);
  b.setAttribute('data-add', e.verb || '');          // the exact call it will emit
  b.setAttribute('aria-pressed', ARMED_EL === e.type ? 'true' : 'false');
  var av = elh('span', 'avatar');
  var pic = cachedAvatar(e, avatarOptsFor(e));
  if (pic) av.append(pic);
  var tx = elh('span', 'pal-text');
  var nm = elh('b'); nm.textContent = doc.name; tx.append(nm);
  var why = elh('i', 'pal-why'); why.textContent = doc.blurb; tx.append(why);
  var how = elh('i', 'pal-how'); how.textContent = GESTURE[e.type] || ''; tx.append(how);
  var meta = elh('i', 'pal-meta');
  meta.textContent = (e.verb || '—') + ' · ' + e.fields.length + ' fields' +
                     (inert ? ' · ' + inert + ' inert' : '');
  tx.append(meta);
  b.append(av, tx);
  wirePaletteButton(b);
  return b;
}

// The avatar is a FUNCTION OF THE ITEM'S OWN LIVE FIELDS, which is what makes it a
// preview rather than a picture: raise a zone type's capacity and the bar in the menu
// grows and grows ticks, because the stage's own `siteLen` is what drew it.
function avatarOptsFor(e) {
  var zt = A.zone_types || {};
  if (e.type === 'site') {
    var z = NEW_ZONE || Object.keys(zt).sort()[0] || 'data';
    return { zone: z, cap: (zt[z] && zt[z].capacity) || 1 };
  }
  return {};
}

function zoneStrip() {
  var zt = A.zone_types || {}, names = Object.keys(zt).sort(), i;
  var after = postSeedZones();
  var strip = elh('div', 'zonestrip');
  for (i = 0; i < names.length; i++) {
    var z = names[i];
    var b = elh('button', 'zonechip');
    b.setAttribute('data-zone', z);
    b.setAttribute('aria-pressed', NEW_ZONE === z ? 'true' : 'false');
    if (after[z]) {
      // declared after the seal: real, exportable, and unusable by a new site
      b.setAttribute('data-after-seal', '1');
      b.setAttribute('title', z + ' was declared after the machine was sealed, so a site ' +
                              'placed now cannot use it');
    }
    var av = elh('span', 'avatar');
    av.append(cachedAvatar({ type: 'zone_type' }, { zone: z, cap: zt[z].capacity || 1 }));
    b.append(av);
    var lab = elh('span');
    lab.textContent = z + ' · ' + (zt[z].capacity || 1);
    b.append(lab);
    wireZoneChip(b);
    strip.append(b);
  }
  var add = elh('button', 'zonechip');
  add.setAttribute('data-zone-new', '1');
  add.textContent = '+ new zone type';
  wireZoneChip(add);
  strip.append(add);
  return strip;
}

// ---- arming ---------------------------------------------------------------------------
var ARMED_EL = null;
var NEW_ZONE = null;              // the zone the next placed site inherits
function armed() { return ARMED_EL; }
function arm(type) {
  // A COMPONENT ARMS LIKE A STAMP. It has no `palette()` entry -- that list is generated
  // from the schema and a component is not a schema element -- so it is matched by name
  // before the lookup that would reject it.
  if (type && String(type).slice(0, 4) === 'cmp:') {
    var cn = String(type).slice(4);
    if (!CMP[cn]) return null;
    var miss = componentBlocked(componentSpec(cn));
    if (miss.length) {
      toast('warn', cn + ' needs the zone type ' + miss.join(', ') + '; add it in ' +
                    'Elements > zone type first, or this placement would be refused');
      return null;
    }
    ARMED_EL = (ARMED_EL === type) ? null : type;
    FORM = null;
    if (ARMED_EL) setMode('edit');
    paint();
    return ARMED_EL ? { type: ARMED_EL, kind: 'component', verb: 'component' } : null;
  }
  var e = type ? paletteEntry(type) : null;
  if (type && !e) return null;
  // A NAMED / ROW / BLOCK ELEMENT IS NEVER PLACED, so clicking it opens its form rather
  // than arming a stage gesture it does not have.  Arming all eleven and honouring two of
  // them is what made the old menu feel broken: arm `loop`, double-click, get a SITE.
  if (e && e.kind !== 'stamp') {
    ARMED_EL = null;
    FORM = (FORM && FORM.type === type) ? null : { type: type };
    paint();
    return FORM ? { type: type, kind: e.kind, verb: e.verb, defaults: e.defaults } : null;
  }
  ARMED_EL = (ARMED_EL === type || !type) ? null : type;
  FORM = null;
  if (ARMED_EL) setMode('edit');
  paint();
  return ARMED_EL ? { type: ARMED_EL, kind: e.kind, verb: e.verb, defaults: e.defaults }
                  : null;
}

// ===================================================================== PLACEMENT GHOST
//
// `begin()` cannot preview a placement: it needs an existing `nodeById[id]`, and the
// thing being placed does not exist yet.  So this is its twin for a stamp -- and the
// preview is drawn by `buildStatic` at STAGE scale, which makes the menu tile, the ghost
// under the cursor and the element that lands three calls to one function.
var PGHOST = null, gGhost = null;
function ghostGroup() {
  if (!gGhost && gEdit) { gGhost = el('g', { opacity: 0.55, 'pointer-events': 'none' });
                          gEdit.append(gGhost); }
  return gGhost;
}
function ghostMarks(type, x, y) {
  var g = ghostGroup();
  if (!g) return;
  clearGroup(g);
  if (!type || (type !== 'site' && type !== 'junction')) return;
  var z = nearestZone() || Object.keys((A.zone_types || {})).sort()[0] || null;
  var cap = (A.zone_types && A.zone_types[z] && A.zone_types[z].capacity) || 1;
  var n = { id: '__ghost', x: x, y: y, kind: type,
            zone: type === 'site' ? z : null, cap: type === 'site' ? cap : 0,
            deg: 0, corner: false, labels: [], cap_explicit: true };
  var byId = { __ghost: n };
  var into = { loop: el('g', {}), seg: el('g', {}), elec: el('g', {}), node: g };
  buildStatic({ A: { nodes: [n], segments: [], loops: {} }, L: L,
                AXIS: { __ghost: { ux: 1, uy: 0 } }, role: {}, px: px, py: py, byId: byId,
                into: into,
                reg: { SEGEL: {}, SEGINFO: {}, PAD_BY_SEG: {}, SEG_BY_PAIR: {},
                       NODEEL: {}, CAPTXT: {} } });
}
function ghostBegin(type, mx, my) {
  var e = paletteEntry(type);
  if (!e || e.kind !== 'stamp') return null;
  ARMED_EL = type;
  PGHOST = { type: type, mx: mx, my: my,
             x: (mx - L.ox) / (L.sx || 1), y: (my - L.oy) / (L.sy || 1),
             valid: true, why: null };
  ghostMarks(type, PGHOST.x, PGHOST.y);
  return PGHOST;
}
function ghostMove(mx, my, opts) {
  if (!PGHOST) return null;
  opts = opts || {};
  var raw = { x: (mx - L.ox) / (L.sx || 1), y: (my - L.oy) / (L.sy || 1) };
  var sn = snapTo(raw.x, raw.y, opts.free, opts.fine);
  var h = hit(mx, my);
  PGHOST.x = sn.x; PGHOST.y = sn.y; PGHOST.mx = mx; PGHOST.my = my;
  PGHOST.guides = sn.guides;
  PGHOST.valid = !h;
  PGHOST.why = h ? (h.id + ' is already here') : null;
  ghostMarks(PGHOST.type, sn.x, sn.y);
  return { x: sn.x, y: sn.y, snapped: (sn.x !== raw.x) || (sn.y !== raw.y),
           guides: sn.guides, valid: PGHOST.valid, why: PGHOST.why };
}
function ghostDrop() {
  if (!PGHOST) return { ok: false, problems: [{ code: 'no_ghost',
    message: 'nothing is being placed' }] };
  var t = PGHOST.type, x = PGHOST.x, y = PGHOST.y;
  ghostCancel();
  return placeStamp(t, x, y);
}
function ghostCancel() { PGHOST = null; clearGroup(ghostGroup()); }

// THE ONE PLACEMENT CALL, so the double-click, the armed click and the harness verb all
// land on the same defaults.  `palette().defaults.node.capacity` is 0 and a capacity of 0
// is refused -- so a form built from the defaults alone fails on every drop; `addNodeAt`
// supplies 1 when neither a zone nor a capacity is given, and a named zone supplies its
// own.
function placeStamp(type, x, y) {
  if (type === 'junction') return addNodeAt(x, y, { kind: 'junction' });
  var z = nearestZone() || Object.keys((STATE && STATE.zone_types) || {}).sort()[0];
  if (z && postSeedZones()[z]) {
    return { ok: false, problems: [{ code: 'zone_after_seal', targets: [z],
      message: "'" + z + "' was declared with set_zone AFTER the machine was sealed, and " +
        "a site is a BUILDER statement, which is hoisted above the seal -- so Python " +
        "would refuse it with \"no zone type '" + z + "' is declared\".  Place the site " +
        'in a zone the physics package declares, or start the canvas from a package that ' +
        'has this one.' }] };
  }
  return addNodeAt(x, y, { kind: 'site', zone: z || undefined });
}

// WHICH ZONE TYPES A NEW SITE CANNOT USE.  `set_zone` is a post-seal MUTATE and `d.site`
// is a pre-seal BUILD, and `baseCallsFrom` hoists every build above the seed -- so a zone
// type you name in the studio exists in the browser's state and is refused by the
// expansion the moment you put a site in it.  That is a real constraint of the file
// format, not of this menu, so the menu says so instead of offering the trap.
//
// Read off the LISTING, which is the only thing that knows the order: any `set_zone`
// after the seed, whether it shipped with the page or you just typed it.
function postSeedZones() {
  var out = {}, i, recs = POST.concat(EDITS);
  for (i = 0; i < recs.length; i++) {
    var r = recs[i];
    if (r && r.method === 'set_zone' && r.args && r.args.length) out[String(r.args[0])] = true;
  }
  return out;
}

// A LOOP IS THE ONE STAMP WITH NO SINGLE-GESTURE PLACEMENT: it is a named walk over nodes
// that already exist.  So the menu says "select nodes, then press Close loop", and this is
// that button.  It uses the SELECTION ORDER, because a loop is an orbit and the order is
// the orbit.
function closeLoopFromSelection(opts) {
  opts = opts || {};
  var walk = [], i;
  for (i = 0; i < SELSET.length; i++) {
    if (SELSET[i].kind === 'segment' || SELSET[i].kind === 'loop') continue;
    if (walk.indexOf(SELSET[i].id) < 0) walk.push(SELSET[i].id);
  }
  if (walk.length < 3) {
    return { ok: false, problems: [{ code: 'short_walk',
      message: 'a loop needs at least three nodes; select them in orbit order first' }] };
  }
  var id = opts.id || freshFrom((A.loops || {}), 'L');
  var closed = opts.closed === undefined
    ? !!segmentBetween(STATE.device, walk[walk.length - 1], walk[0]) : !!opts.closed;
  return closeLoop(id, walk, closed, closed ? 'ring' : 'path');
}

function nearestZone() {
  if (NEW_ZONE) return NEW_ZONE;
  var ns = nodesOf(STATE);
  if (!ns.length) return null;
  var n = STATE.device.nodes[ns[0].id];
  return n && n.zone ? n.zone : null;
}

function wirePaletteButton(b) {
  if (!b || !b.addEventListener) return;
  b.addEventListener('click', function () {
    if (b.getAttribute('id') === 'palExplode') {
      var r = explodeToExplicit();
      toast(r.ok ? 'warn' : 'bad', r.ok ? r.warning : ((r.problems[0] || {}).message || 'refused'));
      return;
    }
    arm(b.getAttribute('data-el'));
  });
}
function wireZoneChip(b) {
  if (!b || !b.addEventListener) return;
  b.addEventListener('click', function () {
    if (b.getAttribute('data-zone-new')) { FORM = { type: 'zone_type', fresh: true }; paint(); return; }
    var z = b.getAttribute('data-zone');
    if (b.getAttribute('data-after-seal')) {
      toast('warn', z + ' was declared after the machine was sealed; a site placed now ' +
                    'cannot use it, because builder statements are hoisted above the seal');
      return;
    }
    NEW_ZONE = (NEW_ZONE === z) ? null : z;
    AVCACHE = {};                       // the site tile previews the chosen zone
    toast('ok', NEW_ZONE ? ('new sites will be ' + NEW_ZONE) : 'new sites take the nearest zone');
    paint();
  });
}

// ===================================================================== THE FORMS
//
// `named`, `row` and `block` are never placed on a canvas.  They are reachable here, in
// the Selection panel, from the same menu -- which is the whole of the fifth thing the
// user could not do.
var FORM = null;
function openForm(type, opts) { FORM = type ? { type: type } : null;
                                if (opts) for (var k in opts) FORM[k] = opts[k];
                                paint(); return FORM; }
function formState() { return FORM; }

function fieldRow(label, ctrl, note) {
  var r = elh('div', 'fieldrow');
  var l = elh('label'); l.textContent = label; r.append(l);
  r.append(ctrl);
  if (note) { var n = elh('span', 'inert'); n.textContent = note; r.append(n); }
  return r;
}
function textInput(id, value) {
  var i = document.createElement('input');
  i.setAttribute('id', id);
  i.value = (value === undefined || value === null) ? '' : String(value);
  return i;
}
function btn(id, label, primary) {
  var b = document.createElement('button');
  b.setAttribute('id', id);
  if (primary) b.setAttribute('class', 'p');
  b.textContent = label;
  return b;
}
function val(id) { var e = $(id); return e ? e.value : ''; }
function coerce(raw, f) {
  var t = f && f.type;
  if (raw === '' || raw === undefined || raw === null) return null;
  if (t === 'integer') return Math.trunc(Number(raw));
  if (t === 'number') return Q.pyFloat(Number(raw));
  if (t === 'boolean') return raw === 'true' || raw === true || raw === '1';
  return String(raw);
}
function pathGet(obj, parts) {
  var cur = obj;
  for (var i = 0; i < parts.length && cur !== undefined && cur !== null; i++) cur = cur[parts[i]];
  return cur;
}

function renderForm(host) {
  var t = FORM.type, e = paletteEntry(t), doc = docOf(t);
  var head = elh('div');
  var b = elh('b'); b.textContent = doc.name; head.append(b);
  var sub = elh('span', 'sub'); sub.textContent = ' ' + (e ? (e.verb || '') : ''); head.append(sub);
  host.append(head);
  var why = elh('div', 'mut'); why.textContent = doc.blurb; host.append(why);
  if (t === 'zone_type') return zoneForm(host, e);
  if (t === 'curve_point') return curveForm(host, e);
  return blockForm(host, e);
}

function zoneForm(host, e) {
  // PREFILLED FROM THE EXISTING RECORD, merged over the defaults.  `nameZone` starts from
  // `defaults.new_zone_type` alone, so editing `data` (cool: true) through a form that
  // sent only `capacity` silently dropped `cool` -- a save that loses data every time.
  var zt = A.zone_types || {};
  var name = FORM.fresh ? '' : (FORM.zone || NEW_ZONE || Object.keys(zt).sort()[0] || '');
  var merged = zoneFields(name, null);
  host.append(fieldRow('name', textInput('zfName', name)));
  var fields = (e && e.fields) || [];
  for (var i = 0; i < fields.length; i++) {
    var f = fields[i];
    host.append(fieldRow(f.name, textInput('zf_' + f.name, merged[f.name]),
                         f.required ? 'required' : ''));
  }
  var bar = elh('div', 'formbtns');
  var save = btn('zfSave', 'set zone type', true);
  save.addEventListener('click', function () {
    var nm = val('zfName'), out = {};
    for (var j = 0; j < fields.length; j++) {
      var v = coerce(val('zf_' + fields[j].name), fields[j]);
      if (v !== null) out[fields[j].name] = v;
    }
    var r = nameZone(nm, out);
    toast(r.ok ? 'ok' : 'bad', r.ok ? ('zone type ' + nm)
                                    : ((r.problems[0] || {}).message || 'refused'));
    if (r.ok) { NEW_ZONE = nm; AVCACHE = {}; FORM = null; paint(); }
  });
  bar.append(save);
  var close = btn('zfClose', 'close');
  close.addEventListener('click', function () { FORM = null; paint(); });
  bar.append(close);
  host.append(bar);
}

function curveForm(host, e) {
  var names = Object.keys((D.physics && D.physics.curves) || {}).sort();
  var live = (STATE && STATE.primitives && STATE.primitives.curves) || {};
  for (var q = 0; q < Object.keys(live).length; q++) {
    var kk = Object.keys(live)[q];
    if (names.indexOf(kk) < 0) names.push(kk);
  }
  var which = FORM.curve || names[0] || 'shuttle_segment';
  var sel = document.createElement('select');
  sel.setAttribute('id', 'cfName');
  for (var i = 0; i < names.length; i++) {
    var o = document.createElement('option');
    o.setAttribute('value', names[i]);
    if (names[i] === which) o.setAttribute('selected', 'selected');
    o.textContent = names[i];
    sel.append(o);
  }
  sel.value = which;
  sel.addEventListener('change', function () { FORM.curve = sel.value; paint(); });
  host.append(fieldRow('curve', sel));
  var rows = curveRows(which);
  var list = elh('div', 'rowlist');
  for (i = 0; i < rows.length; i++) {
    var r = rows[i];
    var line = elh('div', 'fieldrow');
    var lab = elh('label'); lab.textContent = 'us / quanta'; line.append(lab);
    line.append(textInput('cf_us_' + i, r.us));
    line.append(textInput('cf_q_' + i, r.quanta));
    var del = btn('cfDel' + i, '−');
    if (which === 'shuttle_segment' && rows.length <= 1) {
      del.setAttribute('disabled', 'disabled');
      del.setAttribute('title', 'pricing needs at least one shuttle_segment point');
    } else {
      (function (ix) {
        del.addEventListener('click', function () { curveRemovePoint(which, ix); });
      })(i);
    }
    line.append(del);
    list.append(line);
  }
  host.append(list);
  var addLine = elh('div', 'fieldrow');
  var al = elh('label'); al.textContent = '+ point'; addLine.append(al);
  var last = rows.length ? rows[rows.length - 1] : { us: 5.0, quanta: 0.1 };
  addLine.append(textInput('cfNewUs', last.us));
  addLine.append(textInput('cfNewQ', last.quanta));
  var add = btn('cfAdd', 'add', true);
  add.addEventListener('click', function () {
    curveAddPoint(which, { us: Number(val('cfNewUs')), quanta: Number(val('cfNewQ')),
                           table: last.table || 'qccdsim_jones' });
  });
  addLine.append(add);
  host.append(addLine);
  var note = elh('div', 'mut');
  note.textContent = 'set_curve replaces the whole curve, so every untouched row is ' +
    'carried through verbatim -- source and label included, never reconstructed.';
  host.append(note);
}
function curveRows(name) {
  var live = (STATE && STATE.primitives && STATE.primitives.curves) || {};
  var src = live[name] || ((D.physics && D.physics.curves) || {})[name] || [];
  var out = [];
  for (var i = 0; i < src.length; i++) {
    var r = src[i], c = {};
    for (var k in r) if (has(r, k)) c[k] = r[k];
    out.push(c);
  }
  return out;
}
// THE ROW VERBS, as functions.  `addEventListener` is a no-op in the harness, so logic
// left inside a button's click handler is logic with no test -- the same reason every
// pointer handler on the stage is a thin adapter.  The buttons call these.
function curveAddPoint(name, pt) {
  var rows = curveRows(name), last = rows.length ? rows[rows.length - 1] : {};
  var p = { us: Q.pyFloat(+((pt || {}).us !== undefined ? pt.us : (last.us || 5.0))),
            quanta: Q.pyFloat(+((pt || {}).quanta !== undefined ? pt.quanta : (last.quanta || 0.1))),
            table: (pt || {}).table || last.table || 'qccdsim_jones' };
  // `source` and `label` are PROVENANCE and are never invented: a made-up citation is
  // worse than none.
  if ((pt || {}).source) p.source = pt.source;
  if ((pt || {}).label) p.label = pt.label;
  return emitCurve(name, rows, p, -1);
}
function curveRemovePoint(name, ix) {
  var rows = curveRows(name);
  if (name === 'shuttle_segment' && rows.length <= 1) {
    return { ok: false, problems: [{ code: 'last_point',
      message: 'pricing needs at least one shuttle_segment point' }] };
  }
  return emitCurve(name, rows, null, ix);
}

// THE BLOCK VERB, as a function.  `changes` is a flat map of the field paths BELOW the
// block -- 'anomalous_rate_quanta_per_ms', or 'wiring.scheme' -- and it is folded one
// level deep, which is what `set_control(wiring={...})` accepts.
function applyBlock(type, changes) {
  var e = paletteEntry(type);
  if (!e || e.kind !== 'block') {
    return { ok: false, problems: [{ code: 'not_a_block',
      message: String(type) + ' is not a machine settings block' }] };
  }
  var kw = {}, k;
  for (k in (changes || {})) if (has(changes, k)) {
    var parts = k.split('.');
    if (parts.length === 1) kw[parts[0]] = changes[k];
    else {
      var g = kw[parts[0]] || (kw[parts[0]] = {});
      g[parts.slice(1).join('.')] = changes[k];
    }
  }
  var args = (type === 'primitives' && changes && changes.__name__) ? [changes.__name__] : [];
  if (args.length) delete kw.__name__;
  return emit({ method: e.verb, args: args, kwargs: kw });
}

function emitCurve(name, rows, add, dropIx) {
  var out = [], i;
  for (i = 0; i < rows.length; i++) {
    if (i === dropIx) continue;
    // THE ROW IS CARRIED THROUGH, not rebuilt: `source` and `label` are provenance, and a
    // form that reconstructed them would silently rewrite a citation on every save.
    var r = rows[i], c = {};
    for (var k in r) if (has(r, k)) c[k] = (k === 'us' || k === 'quanta') ? Q.pyFloat(+r[k]) : r[k];
    out.push(c);
  }
  if (add) out.push(add);
  if (!out.length) {
    toast('bad', 'a curve with no points is refused by the schema; edit a value instead');
    return;
  }
  var res = emit({ method: 'set_curve', args: [name, out], kwargs: {} });
  toast(res.ok ? 'ok' : 'bad', res.ok ? (name + ': ' + out.length + ' point(s)')
                                      : ((res.problems[0] || {}).message || 'refused'));
  paint();
}

function blockForm(host, e) {
  var head = (e.fields[0] || {}).name || '';
  head = head.split('.')[0];
  var live = (STATE && STATE[head]) || {}, i;
  var editable = [];
  for (i = 0; i < e.fields.length; i++) {
    var f = e.fields[i], parts = f.name.split('.');
    // a path with a `<placeholder>` names ONE INSTANCE of a repeated record, not a field
    // of this block; offering it as a field would emit a call with a made-up key
    if (f.name.indexOf('<') >= 0 || f.name.indexOf('[]') >= 0) continue;
    editable.push({ f: f, parts: parts.slice(1) });
  }
  for (i = 0; i < editable.length; i++) {
    var ent = editable[i], v = pathGet(live, ent.parts);
    var ctrl = textInput('bf_' + i, v === undefined || v === null ? '' : v);
    if (ent.f.inert) ctrl.setAttribute('disabled', 'disabled');
    if (ent.f.default !== undefined && ent.f.default !== null) {
      ctrl.setAttribute('placeholder', String(ent.f.default));
    }
    host.append(fieldRow(ent.parts.join('.'), ctrl,
                         ent.f.inert ? 'nothing reads this' : ''));
  }
  var skipped = e.fields.length - editable.length;
  var bar = elh('div', 'formbtns');
  var save = btn('bfSave', 'apply', true);
  save.addEventListener('click', function () {
    var flat = {}, j, changed = 0;
    for (j = 0; j < editable.length; j++) {
      var en = editable[j];
      if (en.f.inert) continue;
      var raw = val('bf_' + j), was = pathGet(live, en.parts);
      if (raw === '' || String(was === undefined || was === null ? '' : was) === raw) continue;
      // ONLY THE CHANGED FIELDS: writing a default back is indistinguishable from the
      // user asserting it, and `set_*` is a merge with no way to unset.
      flat[en.parts.join('.')] = coerce(raw, en.f);
      changed++;
    }
    if (!changed) { toast('warn', 'nothing changed'); return; }
    var r = applyBlock(e.type, flat);
    toast(r.ok ? 'ok' : 'bad', r.ok ? (e.verb + ': ' + changed + ' field(s)')
                                    : ((r.problems[0] || {}).message || 'refused'));
    paint();
  });
  bar.append(save);
  var close = btn('bfClose', 'close');
  close.addEventListener('click', function () { FORM = null; paint(); });
  bar.append(close);
  host.append(bar);
  var note = elh('div', 'mut');
  note.textContent = 'set, never unset' + (skipped ? (' · ' + skipped +
    ' per-instance field(s) live on the record they belong to') : '') +
    ' · export and edit the file to remove a field.';
  host.append(note);
}

// The selection inspector: every field the schema admits for the selected object, with the
// ones nothing reads marked as such.  It also hosts the forms for the elements that are
// never placed on a canvas.
function renderInspector() {
  var host = $('palInsp');
  if (!host) return;
  host.replaceChildren();
  host.innerHTML = '';
  if (FORM) return renderForm(host);
  if (!SELSET.length) {
    var m = elh('div', 'mut');
    m.textContent = ARMED_EL
      ? (String(ARMED_EL).slice(0, 4) === 'cmp:'
          ? ('armed: ' + String(ARMED_EL).slice(4).replace(/_/g, ' ') +
             ' — click the canvas to place the whole part')
          : ('armed: ' + docOf(ARMED_EL).name + ' — ' + (GESTURE[ARMED_EL] || '')))
      : 'nothing selected. Pick an element above, then use the gesture it names.';
    host.append(m);
    return;
  }
  var sel = SELSET[0], h = '';
  if ((sel.kind === 'site' || sel.kind === 'junction' || sel.kind === 'node') &&
      STATE.device.nodes[sel.id]) {
    var n = STATE.device.nodes[sel.id];
    h += '<b>' + esc2(sel.id) + '</b> <span class="sub">' + esc2(n.kind) + '</span>';
    h += '<div class="fieldrow"><label>zone</label><span>' + esc2(n.zone || '—') + '</span></div>';
    h += '<div class="fieldrow"><label>capacity</label><span>' + n.cap +
         (n.capacity_explicit ? ' (explicit)' : ' (from zone)') + '</span></div>';
    h += '<div class="fieldrow"><label>pos</label><span>' + Q.unbox(n.pos[0]) + ', ' +
         Q.unbox(n.pos[1]) + '</span></div>';
  } else if (sel.kind === 'segment' && STATE.device.segments[sel.id]) {
    var sg = STATE.device.segments[sel.id];
    h += '<b>' + esc2(sel.id) + '</b> <span class="sub">' + esc2(sg.a) + ' – ' + esc2(sg.b) + '</span>';
    h += '<div class="fieldrow"><label>length</label><span>' + Q.unbox(sg.length) + '</span></div>';
    h += '<div class="fieldrow"><label>capacity</label><span>' + sg.cap + '</span></div>';
    h += '<div class="fieldrow"><label>loop</label><span>' + esc2(sg.loop || '—') + '</span></div>';
    h += '<div class="fieldrow"><label>role</label><span>' + esc2(roleOf(segById[sel.id])) + '</span></div>';
  } else if (sel.kind === 'loop') {
    var w = (A.loops || {})[sel.id] || [];
    h += '<b>' + esc2(sel.id) + '</b> <span class="sub">transport loop</span>';
    h += '<div class="fieldrow"><label>nodes</label><span>' + w.length + '</span></div>';
    h += '<div class="fieldrow"><label>walk</label><span>' + esc2(w.join(' → ')) + '</span></div>';
  }
  if (SELSET.length > 1) {
    h += '<div class="mut">' + SELSET.length + ' selected · drag any one of them to ' +
         'move them all · del removes them</div>';
  }
  // innerHTML FIRST, then the live controls: setting it afterwards would wipe them in a
  // real browser, and the two orders are indistinguishable in the shim.
  host.innerHTML = h;
  var nodeSel = 0;
  for (var si = 0; si < SELSET.length; si++) {
    if (SELSET[si].kind !== 'segment' && SELSET[si].kind !== 'loop') nodeSel++;
  }
  var bar = elh('div', 'formbtns');
  if (nodeSel >= 3) {
    // THE LOOP'S PLACEMENT GESTURE, made visible.  A loop is a named walk over nodes that
    // already exist, so there is nothing to double-click; this is the button the menu's
    // "select nodes, then press Close loop" points at.
    var cl = btn('selCloseLoop', 'Close loop', ARMED_EL === 'loop');
    cl.addEventListener('click', function () {
      var r = closeLoopFromSelection({});
      toast(r.ok ? 'ok' : 'bad', r.ok ? 'loop closed'
                                      : ((r.problems[0] || {}).message || 'refused'));
    });
    bar.append(cl);
  }
  var del = btn('selDelete', 'Delete');
  del.addEventListener('click', function () {
    var r = removeSelected();
    if (r && !r.ok && r.problems.length) toast('bad', r.problems[0].message);
  });
  bar.append(del);
  host.append(bar);
}

// THE WRITE PANE.
function renderWrite() {
  var ta = $('pwText');
  if (!ta) return;
  if (ta.value === undefined || ta.value === '' || !ta._touched) ta.value = programSource();
  var errs = lowerErrors();
  var e = $('pwErr');
  if (e) {
    e.innerHTML = errs.length
      ? errs.map(function (x) {
          return '<div>statement ' + ((x.i === null || x.i === undefined) ? '?' : x.i + 1) +
                 ': ' + esc2(x.message) + '</div>'; }).join('')
      : '';
  }
  var c = $('pwCount');
  if (c) {
    c.textContent = PROG.length ? (PROG.length + ' statements → ' + P.frames.length + ' frames')
                                : (P.frames.length + ' shipped frames');
  }
  var f = $('pwFoot');
  if (f) {
    f.innerHTML = AUTHORED
      ? 'this programme was written here. <b>No Python replay exists to check it against</b>, ' +
        'so the per-frame oracle does not apply: <code>frameChecked</code> is ' +
        ((PRICE && PRICE.frameChecked) || 0) + '. Download the pair and run ' +
        '<code>python -m qccd run &lt;name&gt;.arch.json --tsir &lt;name&gt;.tsir.json</code>.'
      : 'the shipped programme. Type <code>p.init({...})</code> and the stage re-renders ' +
        'from what you wrote; <b>Evaluate</b> re-prices and re-checks.';
  }
}

// THE REPORT.  Three registers and never a fourth.
function renderReport() {
  var host = $('report');
  if (!host) return;
  var cov = ruleCoverage();
  var checked = cov.filter(function (c) { return c.state === 'checked'; });
  var failed = cov.filter(function (c) { return c.state === 'failed'; });
  var grey = cov.filter(function (c) { return c.state === 'unchecked' || c.state === 'partial'; });
  var h = '';

  // -- BACKED ------------------------------------------------------------------
  h += '<h3>Backed</h3>';
  if (PRICE && !PRICE.blocked) {
    h += '<table>' +
      row('cost', fmt(PRICE.totals.cost)) +
      row('steps', fmt(PRICE.totals.steps)) +
      row('runtime', fmt(PRICE.totals.us / 1000, 2) + ' ms') +
      row('peak n-bar', fmt(PRICE.peak, 3) + (PRICE.peakIon ? ' (' + esc2(PRICE.peakIon) + ')' : '')) +
      row('junction transits', fmt(PRICE.transits)) +
      (HW ? row('DACs', fmt(HW.dacs)) + row('electrodes', fmt(HW.electrodes)) +
            row('switches', fmt(HW.switches)) + row('ion capacity', fmt(HW.total_capacity)) : '') +
      '</table>';
    h += '<div class="mut">' + (
      PRICE.frameChecked
        ? 'per-frame self-check: ' + PRICE.frameChecked + ' frames compared against the ' +
          'numbers Python shipped, worst drift ' + fmt(PRICE.frameDrift, 6)
        : '<b>no per-frame oracle</b>: these frames were not priced by Python, so there is ' +
          'nothing to compare them against. The arithmetic is parity-tested; <i>this ' +
          'programme</i> is not.') + '</div>';
  } else {
    h += '<div class="mut">no price: see below.</div>';
  }

  // -- REFUSED -----------------------------------------------------------------
  var refused = [];
  if (PRICE && PRICE.blocked) {
    for (var i = 0; i < PRICE.blocked.length; i++) refused.push(breakMessage(PRICE.blocked[i]));
  }
  var se = schemaErrors();
  for (var j = 0; j < Math.min(se.length, 4); j++) refused.push(se[j]);
  if (refused.length) {
    h += '<h3 style="margin-top:14px">Refused</h3><div class="mut">' +
         refused.map(function (t) { return '<div>' + esc2(t) + '</div>'; }).join('') + '</div>';
  }

  // -- THE VERDICTS ------------------------------------------------------------
  h += '<h3 style="margin-top:14px">Rules &mdash; ' + (checked.length + failed.length) +
       ' of ' + cov.length + ' checked in the browser</h3>';
  if (RULES && RULES.oracle) {
    h += '<div class="mut">' + (RULES.oracle.ok
      ? '<span class="badge ok">self-check</span> all ' + RULES.oracle.n +
        ' browser-set rule counts match the ones Python shipped with this page.'
      : '<span class="badge bad">self-check FAILED</span> ' +
        esc2(RULES.oracle.drift.join('; ')) +
        ' &mdash; the verdicts are withdrawn; trust Python, not this page.') + '</div>';
  }
  h += '<div>' + cov.map(function (c) {
    var cls = { checked: 'ok', failed: 'bad', partial: 'warn', unchecked: 'unchecked' }[c.state];
    return '<span class="badge ' + cls + '" title="' + esc2(c.statement) + '">' + c.rule +
           (c.count ? ' ' + c.count : '') + '</span>';
  }).join('') + '</div>';
  if (failed.length) {
    h += '<div class="mut" style="margin-top:6px">' + failed.map(function (c) {
      var first = ((RULES && RULES.messages) || []).filter(function (v) { return v.rule === c.rule; })[0];
      return '<div><b>' + c.rule + '</b> &middot; ' + c.count + ' &middot; ' +
             esc2(first ? first.message : c.statement) + '</div>';
    }).join('') + '</div>';
  }

  // -- NOT CHECKED HERE --------------------------------------------------------
  h += '<h3 style="margin-top:14px">Not checked here</h3><div class="mut">';
  for (var g = 0; g < grey.length; g++) {
    h += '<div><b>' + grey[g].rule + '</b> &nbsp;' + esc2(grey[g].why || grey[g].statement) + '</div>';
  }
  h += '</div>';
  var stem = STATE ? STATE.name : 'design';
  h += '<div class="mut" style="margin-top:8px">download both files and run:<br><code>' +
       'python -m qccd run ' + esc2(stem) + '.arch.json --tsir ' + esc2(stem) +
       '.tsir.json --json report.json</code></div>';
  host.innerHTML = h;
  var sc = $('rScope');
  if (sc) sc.textContent = 'scope: browser';
}

function row(k, v) { return '<tr><td>' + k + '</td><td>' + v + '</td></tr>'; }

var API = {
  // state
  mode: function () { return MODE; }, setMode: setMode,
  edits: function () { return EDITS; }, problems: function () { return PROBLEMS; },
  lints: function () { return LINTS; }, state: function () { return STATE; },
  ready: ok, why: function () { return WHY_NOT; },
  // gestures -- the whole drag, callable without an Event
  hit: hit, begin: begin, move: move, drop: drop, cancel: cancel,
  // the arbiter, the geometry and the feedback -- all callable without an Event, which is
  // the property that keeps every gesture on this page drivable headlessly
  claim: claim, claimEvent: claimEvent, hover: hover, cursor: cursor,
  outline: outlineOf, hitRadii: hitRadii, slop: slop,
  marqueeBegin: marqueeBegin, marqueeMove: marqueeMove, marqueeDrop: marqueeDrop,
  cancelGesture: escapeGesture,
  subjectOf: subjectOf,
  emit: emit, validate: validate, undo: undo, redo: redo,
  addSite: addSite, addSegment: addSegment, removeSelected: removeSelected,
  reconcileLengths: reconcileLengths,
  select: setSelection,
  selection: function () { return SELSET; },
  // components: place one, and treat its parts as a unit
  stampComponent: stampComponent, components: function () { return CMP; },
  instanceMembers: instanceMembers, instanceAt: instanceAt,
  joinPin: joinPin, pinNode: pinNode, attachComponent: attachComponent,
  // live parameters: what the menu is showing, and how to move it
  componentSpec: componentSpec, componentParams: cmpSel, variantOf: variantOf,
  setComponentParam: setComponentParam, cmpCoerce: cmpCoerce,
  // the element menu: arming, the forms, and the avatars
  arm: arm, armed: armed, palettes: renderPalette,
  avatar: function (type, opt) { return kindAvatar({ type: type }, opt || {}); },
  avatarMarkup: function (type, opt) {
    return markup(kindAvatar({ type: type }, opt || {}));
  },
  elementAvatar: elementAvatar, avatarScenes: function () { return AV_SCENE; },
  stageMark: stageMark, stageMarkup: function (id) { return markup(stageMark(id)); },
  // WHAT buildStatic RECORDED about one node -- the bar's drawn length, its slot count and
  // its angle.  The hit target and the highlight are both read out of this, so a test can
  // assert they are DERIVED from the picture rather than computed a second time.
  nodeRec: function (id) {
    var r = NODEEL[id];
    return r ? { kind: r.kind, len: r.len, m: r.m, ang: r.ang } : null;
  },
  markup: markup,
  openForm: openForm, form: formState,
  ghostBegin: ghostBegin, ghostMove: ghostMove, ghostDrop: ghostDrop,
  ghostCancel: ghostCancel, placeStamp: placeStamp,
  zone: function (z) { if (z !== undefined) { NEW_ZONE = z; AVCACHE = {}; paint(); }
                       return NEW_ZONE; },
  closeLoopFromSelection: closeLoopFromSelection,
  elementDocs: function () { return D.element_docs || {}; },
  toModel: toModel, snapTo: snapTo, rebuild: rebuild,
  // numbers
  price: function () { return PRICE; }, hardware: function () { return HW; },
  // THE PREDICATE THAT FREEZES THE STAGE: the structural break list, `[]` when the
  // programme fits.  `price().blocked` is this list PLUS the cost-model failures
  // (`no_curve`, `price_error`), which do not freeze because the picture is still true.
  programBreaks: function () {
    return (typeof PROGRAM_STALE !== 'undefined' && PROGRAM_STALE)
      ? PROGRAM_STALE.breaks.slice() : [];
  },
  hardware0: function () { return HW0; }, repriceNow: repriceNow,
  layout: function () { return L; },
  digest: function () {
    // the structural diff key: sorted, so it is order-independent on purpose -- this one
    // is for "did the graph change", not for "does the file byte-match"
    var nodes = nodesOf(STATE).slice().sort(function (a, b) { return a.id < b.id ? -1 : 1; });
    var segs = segsOf(STATE).slice().sort(function (a, b) { return a.id < b.id ? -1 : 1; });
    return { nodes: nodes, segments: segs };
  },
  // text
  source: sourceText, applySource: applySource, exportPython: exportPython,
  schemaErrors: schemaErrors,
  exportJson: exportJson, exportEdits: exportEdits,
  // ---- the design tool ----------------------------------------------------------
  transaction: transaction, undoGroup: undoGroup, redoGroup: redoGroup,
  newCanvas: newCanvas, newFromGenerator: newFromGenerator,
  addNodeAt: addNodeAt, joinNodes: joinNodes, closeLoop: closeLoop, nameZone: nameZone,
  zoneFields: zoneFields, postSeedZones: postSeedZones,
  curveAddPoint: curveAddPoint, curveRemovePoint: curveRemovePoint,
  applyBlock: applyBlock, curveRows: curveRows,
  explodeToExplicit: explodeToExplicit,
  geom: function () { return GEOM.slice(); },
  seed: function () { return SEED; },
  post: function () { return POST.slice(); },
  // the programme lane
  program: function () { return PROG.slice(); },
  setProgram: setProgram, emitProgram: emitProgram,
  programSource: programSource, applyProgramSource: applyProgramSource,
  programErrors: lowerErrors, authored: function () { return AUTHORED; },
  programToTsir: programToTsir, exportPair: exportPair,
  // the verdicts
  rules: ruleReport, ruleCoverage: ruleCoverage,
  // the palette, GENERATED from the shipped schema + consumer table
  palette: palette,
  // persistence -- pure functions; storage is a three-line adapter over them
  snapshot: snapshot, restore: restore, autosave: autosave, autoload: autoload,
  autosaveSoon: autosaveSoon, importText: importText, snapshotOf: snapshotOf,
  saveProject: saveProject, hasFileHandle: function () { return !!FILE_HANDLE; },
  boot: boot
};
if (typeof globalThis !== 'undefined') globalThis.EDITOR = API;
boot();
return API;
})();
