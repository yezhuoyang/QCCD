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
//: A NOTE IS NOT A PROBLEM.  Two of `lint()`'s findings are observations about what has
//: NOT been drawn yet rather than faults in what has: a zone type no site uses, and a
//: movement class whose orbit matches no loop.  On a blank canvas from the shipped
//: template EVERY finding is one of those, so the page greeted an empty stage with
//: "10 problems" -- ten statements about a device the user had not started drawing.
//: Nothing is silenced: they keep their own chip and their own heading in the list, and
//: `lints()` still hands back all of them.  The problems chip counts what is wrong with
//: what was actually drawn: a geometry-rule finding, a structural warning, a refusal.
var NOTE_CODES = { zone_unused: 1, class_no_participants: 1 };
function lintNotes() {
  return LINTS.filter(function (l) { return !!NOTE_CODES[l.code]; });
}
function lintProblems() {
  return LINTS.filter(function (l) { return !NOTE_CODES[l.code]; });
}
// clean | edited | stale | exact | blocked | unoracled
//
// `unoracled` is the fifth, and it exists because the per-frame self-check -- the thing
// that lets this page display a cost at all -- compares each re-priced frame against the
// cost PYTHON shipped for it.  An AUTHORED programme has no such frames, so there is
// nothing to compare and `frameDrift` over `frameChecked === 0` would report a confident
// zero for a check that never ran.  The badge says so instead.
var PRICE = null, PRICE_STATUS = 'clean';
var GROUP = 0;
// FREE BY DEFAULT.  A coordinate is any real number; the lattice is an aid you switch on
// (the Snap button, remembered), or hold for one drop (shift).  Alt frees a snapped drag.
var SNAP = false, SELSET = [];
var SNAP_KEY = 'qccd.studio.snap';
// THE BOUNDARY'S UNITS: the size of every mark in MODEL units, taken from the layout of
// the device as it was first drawn and kept through its edits.  Marks on screen are sized
// to the smallest gap, so if the boundary were read off the screen two parts pushed
// together would shrink every mark and then fit closer still -- a feedback loop with no
// floor.  Frozen per device (refreshed whenever a device has no edits), the boundary is
// a property of the part, not of the zoom.
var BOUND = null;
function boundaryFrom(lay) {
  // A COPY, never the live layout: `L` is mutated in place on every rebuild, and a
  // boundary that read it would shrink with the marks -- the loop this exists to break.
  // IN THE PIXELS OF THAT LAYOUT, not in model units: a long thin ring is drawn with
  // 22 px per unit across and 144 up, and a bar's rotation only means what it means in
  // the space it is drawn in.  Positions are scaled by the frozen sx/sy on the way in.
  var snap = {}, k;
  for (k in lay) if (has(lay, k)) snap[k] = lay[k];
  return { sx: snap.sx || 1, sy: snap.sy || snap.sx || 1,
           len: function (cap) { return _siteLen(cap, snap); },
           t: snap.site_t, rj: snap.r_junc };
}
// THE SWEEP: a move is tested along its whole path, not only at its end, or a quick
// pointer steps clean over a mark narrower than the step.  Samples every half-thickness.
function firstContact(p0, from, to, member) {
  var dist = Math.sqrt((to.ox - from.ox) * (to.ox - from.ox) + (to.oy - from.oy) * (to.oy - from.oy));
  // the sample step is half a bar's thickness, in UNITS (the box is in pixels)
  if (!BOUND) BOUND = boundaryFrom(L);
  var step = Math.max(0.002, (BOUND.t / 2) / Math.max(BOUND.sx, BOUND.sy)), n = Math.max(1, Math.ceil(dist / step));
  for (var i = 1; i <= n; i++) {
    var t = i / n;
    var c = contactAt(p0, from.ox + (to.ox - from.ox) * t, from.oy + (to.oy - from.oy) * t, member);
    if (c) return { t: t, t0: (i - 1) / n, hit: c };
  }
  return null;
}
function snapRound(v) { return Math.round(v * 1000) / 1000 || 0; }
var DOWN = null, ARMED = null, GHOST = null, LASTPT = null;
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

  // 1. re-lay-out, under whatever scale mode the stage is in -- `layoutOpts()` is the
  //    page's own "true scale" state, so an edit cannot silently change the scale rule
  var lay = Q.computeLayout(nodesOf(STATE), segsOf(STATE), holdOpts());
  for (var k in lay) if (has(lay, k)) L[k] = lay[k];
  // a device with no edits is a fresh device: its first layout is the boundary's unit
  if (!EDITS.length) BOUND = boundaryFrom(lay);

  // 2. rewrite the page's own view of the device, IN PLACE: `A` is captured by every
  //    closure in the main script, so it is mutated rather than replaced.
  syncArch();
  // THE DECLARED-LENGTH NOTE IS A PROPERTY OF THE DEVICE, read off it here on every
  // rebuild -- never a fact remembered from the last drop.  Stored, it outlived the
  // geometry it described: ctrl+Z (undoGroup) popped the edit and the strip went on
  // saying "2 segment(s) drawn up to 0.50x their declared length" about a device that
  // had none, and a fresh Start-card ring inherited the grid's note.
  LENGTH_NOTE = mismatchShort(deviceMismatch());

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
    var nWas = LAST_FRAMES ? LAST_FRAMES.length : 0;
    LAST_FRAMES = P.frames;
    if (typeof deriveStage === 'function') deriveStage(P.frames);
    if (typeof slider !== 'undefined' && slider) {
      slider.max = String(Math.max(0, P.frames.length - 1));
      // `< 0` as well: a play tick that ran while the programme was empty left `frame`
      // at -1, and the next programme then opened on "Step 0 / 2 - undefined"
      if (typeof frame === 'number' && (frame < 0 || frame > P.frames.length - 1)) frame = 0;
    }
    // A PROGRAMME OF ANOTHER LENGTH IS ANOTHER PROGRAMME: the tick queued over the old
    // one is stopped so it cannot write its last frame into the new one.  A drag while
    // an authored programme plays re-lowers to the same length and keeps playing.
    if (typeof stop === 'function' && P.frames.length !== nWas) stop();
    // THE LISTING FOLLOWS THE FRAMES.  The Program pane's row count, its filter view
    // and its NOW strip were built once at load over the shipped frames, so an authored
    // programme of 2 statements sat under a header reading "13 / 13 instructions" with
    // 11 ghost rows below it.  `rebuildView` is the page's own filter path; it is
    // guarded because the editor's first rebuild can run before the page defines it.
    if (typeof rebuildView === 'function') {
      rebuildView(($('pFilter') && $('pFilter').value) || '', null);
    }
  }
  rebuildStatic();
  repriceNow();
  // THE VERDICTS ARE RE-DERIVED, not struck through.  `RULES_STALE` used to invalidate
  // all 27 at once because none of them could be re-run here; 21 of them now can, off the
  // walk `repriceNow` just did, and only the other 6 go grey.  Keeping both mechanisms
  // would leave the page with two answers about the same rule.
  if (typeof renderSide === 'function') renderSide();
  if (typeof renderReport === 'function') renderReport();
  // the regime is a property of the device's shape (`L.wide`), so it is re-read here
  if (typeof applyLayout === 'function') applyLayout();
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
  // THE NAME TOO: a device from a gallery card is named after what made it, and the
  // price line, the export header and the recompile hint all read `A.name` -- which
  // otherwise kept naming the page's shipped device for ever
  if (STATE.name !== undefined && STATE.name !== null) A.name = STATE.name;
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
  var maps = [SEGEL, SEGINFO, PAD_BY_SEG, PAD_BY_SITE, SITE_SPAN, SEG_BY_PAIR,
              NODEEL, CAPTXT];
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
  // THE FRAME MOVES ONLY WHEN THE DRAWING WAS RE-FITTED.  A held edit keeps the same
  // scale, origin and viewBox, so there is nothing to re-frame and re-framing would be
  // the shift the hold exists to prevent; `LASTFIT` still follows `L` so the next real
  // fit can tell whether the frame was showing the whole device.
  REFRAME = REFIT_NEXT;
  rebuildAxis();
  // THE STAGE IS ONE SCENE among the ones buildStatic can draw; the palette avatars are
  // the others.  Same function, same layout constants, same palette -- so the picture in
  // the menu cannot drift from the picture on the canvas by construction.
  buildStatic(STAGE);
  refitVB();
  svg.setAttribute('viewBox', VB.x + ' ' + VB.y + ' ' + VB.w + ' ' + VB.h);
  // the bar is in user units and the viewBox just moved; and a "no rail between A and B"
  // badge is a claim about the device that was, so a new device clears it
  if (typeof placeScaleBar === 'function') placeScaleBar();
  if (typeof clearNoRail === 'function') clearNoRail();
  // A MEASUREMENT IS ABOUT A GEOMETRY.  Every path here is a geometry change -- an edit,
  // an undo, the true-scale toggle -- so the two picked points no longer mean what they
  // meant.  Keeping them would leave a ruler pinned to pixels nothing is at any more.
  // (A pan or a zoom does NOT come through here, and a measurement survives both.)
  measureClear();
  // A HALF-DRAWN SHAPE IS ABOUT A GEOMETRY TOO: its points are model coordinates the new
  // layout no longer puts in the same place, and a preview left pinned to the old ones
  // would commit a shape nobody drew.
  sketchClear();
  // THE FLAG IS CLEARED BY THE REDRAW THAT HONOURED IT, not by its caller: `rebuild` and
  // `rescale` both end here, and one of them forgetting would leave every later edit
  // re-fitting -- which is the defect, silently back.
  REFIT_NEXT = false;
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
var REFRAME = true;
function refitVB() {
  if (!REFRAME) { LASTFIT.w = L.W; LASTFIT.h = L.H; return; }
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
  // The other per-node table the stage derives from the device: which PLACE each node is,
  // so two sites dropped on one coordinate share a slot stack instead of drawing two ions
  // at the same point.  It goes stale for exactly the reasons the axis does.
  if (typeof rebuildSites === 'function') rebuildSites();
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
  // `shift: [loop, delta]` frames were compiled for a loop of ONE length, so the shipped
  // programme goes stale when that loop is resized.  An authored programme is re-lowered
  // against the live loop on every rebuild, and a device with no programme has nothing
  // to go stale -- so the guard reads the compiled lengths only for the compiled frames.
  var lens = Q.loopLengths(dev), baseLens = programmeIsShipped() ? BASE_LOOPS : {};
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

// A MINIMUM PICK RADIUS OF 8 CLIENT PX, taken only when nothing was hit exactly.  The
// ring page draws its junction squares and site bars 1.8-2.7 px across at fit, and the
// perpendicular slop is 0.06g = 1.3 px there: a target you can see but cannot aim at.
// `widen` re-runs the NODE pass -- the same lines, the same drawn bodies -- with the slop
// raised to 8 px across the bar and around the junction, and the LOOP pass with its halo
// widened to the same 8 px; nothing along the bar's axis changes and no segment is
// widened, so every exact answer stays exactly what it was and the widened one is
// offered only where the exact pass answered nothing.  (The loop halo is what the rails
// do not own -- 1.25 px a side on the ring page at fit, a target no pointer can land on.)
function hit(mx, my, coarse, widen) {
  if (!GRID) buildIndex();
  var S = slop(), SS = coarse ? 2 * S : S, SJ = 0;
  if (widen) { S = Math.max(S, 8 * userPerPx()); SJ = S; }
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
        if (dj <= h + SJ && (bestJ === null || dj < bestJ.dist)) {
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
  for (var sI = 0; sI < (widen ? 0 : A.segments.length); sI++) {
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
    if (dl <= Math.max(0.5 * L.sw_loop, SJ) && (bl === null || dl < bl.dist)) {
      bl = { kind: 'loop', id: lid, dist: dl };
    }
  }
  if (bl) return bl;
  return widen ? null : hit(mx, my, coarse, true);
}

// THE ONE ARBITER of who owns a press.  The page's pan handler had no mode guard at all,
// so both it and the editor ran on every pointerdown and the stage panned out from under
// a node drag (measured: VB.x 0 -> -8.96 on a replayed drag).  render.py asks this and
// restates nothing.
//
// THERE ARE NO MODES.  A press on an element is the editor's (drag = move, click =
// select); shift on empty stage is a marquee; everything else -- empty stage, middle or
// right button -- pans.  The page used to open in a "play" mode in which this answered
// 'pan' for every press, so the first drag any user made slid the stage instead of the
// node under the pointer, and nothing on screen said why.  (`mod.space` is still honoured
// for a caller that says it; the page itself no longer holds space to pan, because space
// is play/pause and empty-stage drag already pans.)
//
// Pure, and callable without an Event, so a harness can assert the RULE rather than the
// handler that happens to obey it.
// VIEW-ONLY: the page shows a programme running and nothing may be edited -- the
// embedded examples on the website.  Every press pans, every editing key is dead, and
// the transport (play, step, seek, fit) is all that answers.
var VIEW_ONLY = false;
function setViewOnly(on) {
  VIEW_ONLY = !!on;
  if (VIEW_ONLY) {
    if (GHOST) cancel();
    if (ARMED_EL) arm(null);
    if (MEASURE) measureToggle(false);
    if (SKETCH) { SKETCH = null; sketchClear(); }
    menuClose();
    document.body.setAttribute('data-viewonly', '1');
  } else {
    document.body.removeAttribute('data-viewonly');
  }
  paint();
}
var VIEW_ONLY_VERBS = { 'toggle-play': 1, 'glide': 1, 'escape': 1, 'seek-first': 1, 'seek-last': 1,
                        'seek-back': 1, 'seek-ahead': 1, 'seek-next': 1, 'seek-prev': 1, 'fit': 1,
                        'follow': 1, 'help': 1, 'explain': 1 };
function claim(mx, my, mod) {
  mod = mod || {};
  if (VIEW_ONLY) return 'pan';
  // THE RULER TAKES EVERY LEFT PRESS while it is on: a click is a measurement, and a
  // measurement you have to aim around a drag would be no measurement at all.  A middle
  // or right press still pans, so the view can be moved without leaving the tool.
  if (MEASURE && !(mod.button === 1 || mod.button === 2)) return 'measure';
  // AND SO DOES AN ARMED SHAPE TOOL, for the same reason: a drag you had to aim around
  // the parts already on the canvas would be no drawing tool at all.  Middle and right
  // still pan, so the view can be moved without leaving the tool.
  if (SKETCH && !(mod.button === 1 || mod.button === 2)) return 'sketch';
  // A RIGHT PRESS ON SOMETHING IS THAT THING'S MENU.  A right press on EMPTY STAGE still
  // pans, because right-drag panning is a documented gesture and `render.py` suppresses
  // the browser's own context menu over the stage for exactly that reason -- so the only
  // place the browser menu is given up is where this page has a better one to offer.
  if (mod.button === 2 && hit(mx, my)) return 'menu';
  if (mod.space || mod.button === 1 || mod.button === 2) return 'pan';
  return hit(mx, my) ? 'element' : (mod.shift ? 'marquee' : 'pan');
}
function claimEvent(e) {
  var m = toModel(e.clientX, e.clientY);
  return claim(m.x, m.y, { button: e.button, alt: e.altKey,
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
// `hard` rounds to the nearest lattice step UNCONDITIONALLY -- no window, no `free`.  A
// stamp has no drag to preserve, and a placement that landed 0.31 of a step off the
// lattice (measured: `L.sx` is 72 on the blank page, so the window is 22 px wide and a
// click misses it as often as not) shrank every mark on the stage.  One lattice rule,
// here, with one extra switch -- not a second rounding somewhere else.
function snapTo(x, y, free, fine, hard) {
  // free: where the pointer is, to a thousandth of a lattice unit so the source stays
  // readable (`d.site("S3", 2.417, 0.0)`), never `2.41700000001`
  if (!hard && !fine && (!SNAP || free)) return { x: snapRound(x), y: snapRound(y), guides: [] };
  var stepx = (L.ux || 1) / (fine ? 4 : 1), stepy = (L.uy || L.ux || 1) / (fine ? 4 : 1);
  // `Math.round(-0.2)` is -0, which the Python export prints as `-0.0`: a node at the
  // origin that reads as if it were somewhere else.  `|| 0` folds it to +0.
  //
  // THE NEAREST LATTICE POINT, UNCONDITIONALLY.  A 0.30-step window used to decide
  // whether to round at all, so 41% of drops -- every release in the 40% of a step the
  // window did not cover -- landed off-lattice, and ONE off-lattice node makes the
  // nearest-neighbour gap `g` shrink and every mark on the stage with it (measured: g
  // 50.9 -> 22.7 px after a single 120 px drag).  Snap on means on; `alt` (free) is the
  // one way off it and `shift` (fine) quarters the step.
  var rx = Math.round(x / stepx) * stepx || 0, ry = Math.round(y / stepy) * stepy || 0;
  var out = { x: rx, y: ry, guides: [] };
  // alignment snap.  Not cosmetic: `compute_layout` tests `axis_aligned` and falls back
  // from anisotropic to isotropic the instant ONE diagonal segment exists, which visibly
  // rescales the whole picture.  Keeping a drag on-axis is what keeps that from happening
  // by accident.  AT MOST ONE GUIDE PER AXIS -- the nearest -- so the HUD's two guide
  // lines are the two that decided the point rather than the first two of many.
  var gx = null, gy = null;
  for (var i = 0; i < A.nodes.length; i++) {
    var n = A.nodes[i];
    // every member of the drag, not only the one pressed: a group drag that snapped to
    // its own passengers would pin itself in place
    if (GHOST && (GHOST.ids ? GHOST.ids.indexOf(n.id) >= 0 : n.id === GHOST.id)) continue;
    var ex = Math.abs((n.x - out.x) * L.sx), ey = Math.abs((n.y - out.y) * L.sy);
    if (ex < 0.15 * L.g && (gx === null || ex < gx.d)) gx = { v: n.x, d: ex };
    if (ey < 0.15 * L.g && (gy === null || ey < gy.d)) gy = { v: n.y, d: ey };
  }
  if (gx) { out.x = gx.v; out.guides.push(['x', gx.v]); }
  if (gy) { out.y = gy.v; out.guides.push(['y', gy.v]); }
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
    problems = problems.concat(moveProblems(op.args[0], +Q.unbox(op.args[1]),
                                            +Q.unbox(op.args[2]), null));
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
// THE ONE RULE FOR A NODE AT A POINT, asked three times: by `validate` for a single
// `move_site`, by `move()` for every member of a live drag (so the HUD turns red BEFORE
// the release, not a toast after it) and by `drop()` for the same members.  `skip` names
// the other members of a rigid group -- a member landing where another member WAS is
// not a coincidence, and the other member's own move says where it is going.
function moveProblems(nid, x, y, skip) {
  var problems = [];
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
    if (n.id === nid || (skip && skip[n.id])) continue;
    var d = Math.sqrt((n.x - x) * (n.x - x) + (n.y - y) * (n.y - y));
    if (d < 0.05 * (L.gd || 1)) {
      problems.push({ code: 'coincident', targets: [nid, n.id],
                      message: nid + ' would sit on top of ' + n.id +
                        '. The layout measures the nearest-neighbour gap, and two nodes ' +
                        'at one point make every mark the wrong size.' });
    }
  }
  return problems;
}
// EVERY MEMBER OF A DRAG, checked against everything that is NOT moving with it.  The
// warnings used to be asked of `GHOST.id`, which for a segment or loop drag is a segment
// or loop id that no node has, so those drags warned about nothing; and the refusal was
// asked only at the drop.  One scan, at `p0 + (ox, oy)`, feeding the HUD and the drop.
// ---- THE BOUNDARY ------------------------------------------------------------------
// Two marks never overlap.  Each node is an oriented box in stage pixels -- a site is its
// drawn bar (length from its capacity, thickness `site_t`, at its own angle), a junction
// its square -- and a drag is stopped against the first box in its way.  The same box the
// stage draws and the hit test uses, so the boundary is where the eye says it is.
// THE BAR'S AXIS AT A POSITION: the same rule the stage draws by (`axisOf`) -- the arm
// direction most of its arms agree with -- evaluated with the moving nodes where they
// are going, so the box a drag is tested with is the box the drop will draw.
function axisAt(id, pos) {
  if (!BOUND) BOUND = boundaryFrom(L);
  var arms = [], i, sx = BOUND.sx, sy = BOUND.sy;
  var p = pos[id] || nodeById[id];
  for (i = 0; i < A.segments.length; i++) {
    var sg = A.segments[i];
    if (sg.a !== id && sg.b !== id) continue;
    var oid = sg.a === id ? sg.b : sg.a, o = pos[oid] || nodeById[oid];
    if (!o || !p) continue;
    var dx = (o.x - p.x) * sx, dy = (o.y - p.y) * sy, h = Math.sqrt(dx * dx + dy * dy);
    if (h < 1e-9) continue;
    var ux = dx / h, uy = dy / h;
    if (ux < -1e-12 || (Math.abs(ux) < 1e-12 && uy < 0)) { ux = -ux; uy = -uy; }
    arms.push([ux, uy]);
  }
  if (!arms.length) return null;
  var best = arms[0], bs = -1;
  for (i = 0; i < arms.length; i++) {
    var sc = 0;
    for (var o2 = 0; o2 < arms.length; o2++) sc += Math.abs(arms[i][0] * arms[o2][0] + arms[i][1] * arms[o2][1]);
    if (sc > bs) { bs = sc; best = arms[i]; }
  }
  return { ux: best[0], uy: best[1] };
}
function nodeBoxAt(n, x, y, axis) {
  // the shipped layout is the unit until the first edit re-lays the device out
  if (!BOUND) BOUND = boundaryFrom(L);
  var B = BOUND, cx = x * B.sx, cy = y * B.sy;
  if (isJunctionNode(n)) return { cx: cx, cy: cy, hl: B.rj, ht: B.rj, ux: 1, uy: 0 };
  var G = siteHalfAxis(n), E = NODEEL[n.id], ux, uy;
  if (axis) { ux = axis.ux; uy = axis.uy; }
  else {
    var ang = (E && E.ang !== undefined) ? E.ang * Math.PI / 180 : Math.atan2(G.ax.uy, G.ax.ux);
    ux = Math.cos(ang); uy = Math.sin(ang);
  }
  return { cx: cx, cy: cy, hl: B.len(n.cap || 0) / 2, ht: B.t / 2, ux: ux, uy: uy };
}
// separating-axis test over the four axes of two oriented rectangles
function boxesOverlap(a, b, margin) {
  var axes = [[a.ux, a.uy], [-a.uy, a.ux], [b.ux, b.uy], [-b.uy, b.ux]];
  var dx = b.cx - a.cx, dy = b.cy - a.cy;
  for (var i = 0; i < 4; i++) {
    var ax = axes[i][0], ay = axes[i][1];
    var ra = Math.abs(a.ux * ax + a.uy * ay) * a.hl + Math.abs(-a.uy * ax + a.ux * ay) * a.ht;
    var rb = Math.abs(b.ux * ax + b.uy * ay) * b.hl + Math.abs(-b.uy * ax + b.ux * ay) * b.ht;
    if (Math.abs(dx * ax + dy * ay) >= ra + rb + margin) return false;
  }
  return true;
}
// ONE TOLERANCE, EVERYWHERE.  Marks may touch; an overlap smaller than this (a
// thousandth of a unit is what a coordinate is kept to) is touching.  A cosmetic gap on
// top of it would let rounding leave a part a hair inside the gap, where a drag toward
// the obstacle is stuck and a drag away starts 'already overlapping' and is exempt.
function boundaryMargin() { return 0; }
// the first fixed node any member of a drag would overlap at offset (ox, oy), or null
function contactAt(p0, ox, oy, member, mg) {
  var margin = mg === undefined ? boundaryMargin() : mg, pos = {}, k;
  for (k = 0; k < p0.length; k++) pos[p0[k].id] = { x: p0[k].x + ox, y: p0[k].y + oy };
  for (var i = 0; i < p0.length; i++) {
    var r = p0[i], n = nodeById[r.id];
    if (!n) continue;
    var a = nodeBoxAt(n, r.x + ox, r.y + oy, isJunctionNode(n) ? null : axisAt(r.id, pos));
    for (var j = 0; j < A.nodes.length; j++) {
      var m = A.nodes[j];
      if (member[m.id]) continue;
      if (boxesOverlap(a, nodeBoxAt(m, m.x, m.y), margin)) return { id: r.id, against: m.id };
    }
  }
  return null;
}
// the node a stamp (a pseudo-node: kind, capacity, position) would overlap, or null
function stampContact(pseudo, x, y) {
  var a = nodeBoxAt(pseudo, x, y), margin = boundaryMargin();
  for (var j = 0; j < A.nodes.length; j++) {
    var m = A.nodes[j];
    if (boxesOverlap(a, nodeBoxAt(m, m.x, m.y), margin)) return m.id;
  }
  return null;
}
function groupCheck(p0, ox, oy) {
  var member = {}, i, warnings = [], brief = [], problems = [];
  for (i = 0; i < p0.length; i++) member[p0[i].id] = 1;
  var diag = false, agg = { count: 0, worst: null };
  for (i = 0; i < p0.length; i++) {
    var r = p0[i], x = r.x + ox, y = r.y + oy;
    problems = problems.concat(moveProblems(r.id, x, y, member));
    if (!diag && breaksAxisAlignment(r.id, x, y, member)) diag = true;
    mismatchScan(r.id, x, y, member, agg);
  }
  if (diag && L.axis_aligned) {
    warnings.push('this makes a segment diagonal: the fit switches from anisotropic (' +
                  L.sx + ' x ' + L.sy + ' px/unit) to isotropic and the whole picture ' +
                  'rescales');
    brief.push('makes a segment diagonal: the whole picture rescales');
  }
  var decl = mismatchText(agg);
  if (decl) { warnings.push(decl); brief.push(mismatchHud(agg)); }
  // `brief[i]` is `warnings[i]` sized for the HUD's second line: a cut of the long
  // sentence at 72 characters landed mid-number ('anisotropic (101.7...')
  return { warnings: warnings, brief: brief, problems: problems, mismatch: agg.worst ? agg : null };
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
//
// A PLACED COMPONENT IS ONE THING.  A press on any part of a stamped component -- a node
// or one of its segments -- that is not already inside the selection moves every member
// of that instance (`instanceMembers`, the same list select- and delete-as-a-unit use);
// `opts.alt` takes the one part under the pointer instead.  Measured before this: a drag
// on `c1.s0` of a freshly stamped grid tile moved 1 of its 8 nodes.
function subjectOf(kind, id, opts) {
  var out = [], i, q;
  opts = opts || {};
  function push(nid) { if (nodeById[nid] && out.indexOf(nid) < 0) out.push(nid); }
  function inSelection() {
    for (var k = 0; k < SELSET.length; k++) if (SELSET[k].kind === kind && SELSET[k].id === id) return true;
    return false;
  }
  function members() {
    var inst = (!opts.alt && typeof instanceAt === 'function') ? instanceAt(id) : null;
    if (!inst) return false;
    var mem = instanceMembers(inst);
    for (var m = 0; m < mem.length; m++) {
      if (mem[m].kind === 'segment') { var s3 = segById[mem[m].id]; if (s3) { push(s3.a); push(s3.b); } }
      else push(mem[m].id);
    }
    return out.length > 0;
  }
  if (kind === 'segment') {
    if (inSelection()) return selectionNodes();
    if (members()) return out;
    var sg = segById[id]; if (sg) { push(sg.a); push(sg.b); } return out;
  }
  if (kind === 'loop') {
    if (inSelection()) return selectionNodes();
    var w = (A.loops || {})[id] || [];
    for (i = 0; i < w.length; i++) push(w[i]);
    return out;
  }
  out = selectionNodes();
  if (out.indexOf(id) >= 0) return out;
  out = [];
  if (members()) return out;
  return nodeById[id] ? [id] : [];
}
// THE NODES THE SELECTION MOVES -- a segment brings both ends, a loop its whole walk.
// One expansion for a drag from inside the selection and for an arrow-key nudge, so the
// two gestures cannot disagree about what "move the selection" means.
function selectionNodes() {
  var out = [], i, q;
  function push(nid) { if (nodeById[nid] && out.indexOf(nid) < 0) out.push(nid); }
  for (i = 0; i < SELSET.length; i++) {
    var sl = SELSET[i];
    if (sl.kind === 'segment') { var s2 = segById[sl.id]; if (s2) { push(s2.a); push(s2.b); } }
    else if (sl.kind === 'loop') {
      var w2 = (A.loops || {})[sl.id] || [];
      for (q = 0; q < w2.length; q++) push(w2[q]);
    } else push(sl.id);
  }
  return out;
}

function begin(kind, id, mx, my, opts) {
  var ids = subjectOf(kind, id, opts);
  if (!ids.length) return null;
  var anchor = nodeById[id] ? id : ids[0];
  var n = nodeById[anchor];
  GROUP++;
  var p0 = [], i;
  for (i = 0; i < ids.length; i++) p0.push({ id: ids[i], x: nodeById[ids[i]].x,
                                             y: nodeById[ids[i]].y });
  // the device's own extent BEFORE the drag, for `stageBox`: every node, members at the
  // positions they are leaving
  var bb = null;
  for (i = 0; i < A.nodes.length; i++) {
    var q = A.nodes[i];
    if (!bb) bb = { x0: q.x, x1: q.x, y0: q.y, y1: q.y };
    else { bb.x0 = Math.min(bb.x0, q.x); bb.x1 = Math.max(bb.x1, q.x);
           bb.y0 = Math.min(bb.y0, q.y); bb.y1 = Math.max(bb.y1, q.y); }
  }
  GHOST = { kind: kind, id: id, anchor: anchor, ids: ids, p0: p0,
            x0: n.x, y0: n.y, mx0: mx, my0: my,
            px0: px(n), py0: py(n), group: 'g' + GROUP,
            warnings: [], problems: [], bbox: bb };
  return GHOST;
}
// WHERE A SNAPPED DRAG MAY LAND, in lattice units: the visible stage, rounded inward to
// the step, widened to the device's own box plus one step on every side.  A release
// outside the stage then lands the node on the nearest lattice point that is still on
// the canvas -- a pointer that leaves the window keeps arriving (the svg holds the
// capture), so the user can come back; what they cannot do any more is lose a node
// off-screen.  The device box is the floor because the visible padding is not always a
// whole step: on the ring page `sy` is 144 px/unit and the stage's margin is a fifth of
// a step, so the stage alone would have pinned every site to the row it started on.
// `null` when nothing legal is inside either box.
function stageBox(stepx, stepy, bb) {
  var b = null;
  if (typeof VB !== 'undefined' && VB && VB.w > 0) {
    // the WHOLE svg box, letterbox included: `meet` shows more than the viewBox on the
    // axis it does not fill, and every pixel of the box is canvas the user can see
    var f = fitBox();
    var mx0 = VB.x - f.ox / f.k, mx1 = VB.x + (Math.max(1, f.r.width) - f.ox) / f.k;
    var my0 = VB.y - f.oy / f.k, my1 = VB.y + (Math.max(1, f.r.height) - f.oy) / f.k;
    var lx0 = (mx0 - L.ox) / (L.sx || 1), lx1 = (mx1 - L.ox) / (L.sx || 1);
    var ly0 = (my0 - L.oy) / (L.sy || 1), ly1 = (my1 - L.oy) / (L.sy || 1);
    b = { x0: Math.ceil(Math.min(lx0, lx1) / stepx) * stepx, x1: Math.floor(Math.max(lx0, lx1) / stepx) * stepx,
          y0: Math.ceil(Math.min(ly0, ly1) / stepy) * stepy, y1: Math.floor(Math.max(ly0, ly1) / stepy) * stepy };
  }
  if (bb) {
    var d = { x0: Math.floor(bb.x0 / stepx) * stepx - stepx, x1: Math.ceil(bb.x1 / stepx) * stepx + stepx,
              y0: Math.floor(bb.y0 / stepy) * stepy - stepy, y1: Math.ceil(bb.y1 / stepy) * stepy + stepy };
    b = b ? { x0: Math.min(b.x0, d.x0), x1: Math.max(b.x1, d.x1),
              y0: Math.min(b.y0, d.y0), y1: Math.max(b.y1, d.y1) } : d;
  }
  return (b && b.x0 <= b.x1 && b.y0 <= b.y1) ? b : null;
}

function move(mx, my, opts) {
  if (!GHOST) return null;
  opts = opts || {};
  // remembered in LATTICE units: the drop re-lays the device out, so a model point kept
  // from before it would name the wrong place once `L.sx`/`L.ox` have moved
  LASTPT = { lx: (mx - L.ox) / (L.sx || 1), ly: (my - L.oy) / (L.sy || 1) };
  var dx = (mx - GHOST.mx0) / (L.sx || 1), dy = (my - GHOST.my0) / (L.sy || 1);
  var s = snapTo(GHOST.x0 + dx, GHOST.y0 + dy, opts.free, opts.fine);
  // NEVER OFF-CANVAS: a snapped drag stays inside the visible stage (`stageBox`), so a
  // release past its edge -- or outside the window -- lands on the nearest lattice point
  // that is still on screen instead of somewhere no one can grab it back from.  A free
  // (alt) drag is the user's own business; Snap off is not -- it only stops the rounding.
  if (!opts.free) {
    var box = stageBox((L.ux || 1) / (opts.fine ? 4 : 1), (L.uy || L.ux || 1) / (opts.fine ? 4 : 1), GHOST.bbox);
    if (box) {
      s.x = Math.max(box.x0, Math.min(box.x1, s.x));
      s.y = Math.max(box.y0, Math.min(box.y1, s.y));
    }
  }
  // THE BOUNDARY.  The wanted offset is tested against every mark that is not moving;
  // if it overlaps one, the drag goes as far along its path as it can (a bisection from
  // the last accepted offset) and then slides along the obstacle -- the pointer's x with
  // the accepted y, or the accepted x with the pointer's y -- so a part moves along the
  // side of the one it is pressed against instead of sticking to it.  A snapped drag
  // simply keeps its last lattice point.  A drag that STARTS overlapping (a device drawn
  // that way) is exempt, or it could never be pulled apart.
  var member = {}, mk;
  for (mk = 0; mk < GHOST.p0.length; mk++) member[GHOST.p0[mk].id] = 1;
  if (GHOST.acc === undefined) {
    GHOST.acc = { ox: 0, oy: 0 };
    // exempt only a REAL overlap (a device drawn that way), never a hairline one
    GHOST.noBoundary = !!contactAt(GHOST.p0, 0, 0, member, -0.5);
  }
  GHOST.contact = null;
  if (!GHOST.noBoundary) {
    var want = { ox: s.x - GHOST.x0, oy: s.y - GHOST.y0 }, acc = GHOST.acc;
    var fc = firstContact(GHOST.p0, acc, want, member);
    if (fc) {
      var best = acc;
      if (!(SNAP && !opts.free)) {
        // the farthest free point between the last free sample and the first blocked one
        var lo = fc.t0, hi = fc.t, t, it;
        for (it = 0; it < 8; it++) {
          t = (lo + hi) / 2;
          if (contactAt(GHOST.p0, acc.ox + (want.ox - acc.ox) * t, acc.oy + (want.oy - acc.oy) * t, member)) hi = t; else lo = t;
        }
        best = { ox: acc.ox + (want.ox - acc.ox) * lo, oy: acc.oy + (want.oy - acc.oy) * lo };
        // then slide along the obstacle, each slide swept too
        var cA = { ox: want.ox, oy: best.oy }, cB = { ox: best.ox, oy: want.oy };
        if (!firstContact(GHOST.p0, best, cA, member)) best = cA;
        else if (!firstContact(GHOST.p0, best, cB, member)) best = cB;
      }
      // ROUND AWAY FROM THE OBSTACLE.  A landing kept to a thousandth can round a hair
      // INTO the mark it stopped against, and the next drag would then creep deeper by
      // that hair every step; so a rounded landing that touches is backed off along its
      // own path until it is clear, and the last accepted offset is the floor.
      var rx = snapRound(GHOST.x0 + best.ox), ry = snapRound(GHOST.y0 + best.oy);
      var bl = Math.sqrt((best.ox - acc.ox) * (best.ox - acc.ox) + (best.oy - acc.oy) * (best.oy - acc.oy)) || 1;
      for (var bk = 1; bk <= 4 && contactAt(GHOST.p0, rx - GHOST.x0, ry - GHOST.y0, member); bk++) {
        rx = snapRound(GHOST.x0 + best.ox - (best.ox - acc.ox) / bl * 0.001 * bk);
        ry = snapRound(GHOST.y0 + best.oy - (best.oy - acc.oy) / bl * 0.001 * bk);
      }
      if (contactAt(GHOST.p0, rx - GHOST.x0, ry - GHOST.y0, member)) { rx = GHOST.x0 + acc.ox; ry = GHOST.y0 + acc.oy; }
      s.x = rx; s.y = ry; s.guides = [];
      GHOST.contact = fc.hit.against;
    }
    GHOST.acc = { ox: s.x - GHOST.x0, oy: s.y - GHOST.y0 };
  }
  GHOST.x = s.x; GHOST.y = s.y; GHOST.guides = s.guides;
  // THE SNAP IS COMPUTED ON THE PRESSED MEMBER ONLY and applied to the rest as one
  // translation, so a rigid group keeps the shape it started with instead of every
  // passenger collapsing onto the nearest lattice point independently.
  var ox = s.x - GHOST.x0, oy = s.y - GHOST.y0;
  // LAYOUT IS FROZEN FOR THE WHOLE DRAG.  Recomputing it per pointermove changes sx, sy,
  // ox, oy and g and therefore EVERY mark, and rebuilding the static picture creates
  // thousands of SVG elements.  So the dragged node's own marks move and its incident
  // segments get new endpoints; everything else waits for the drop.
  //
  // THE VERDICT IS LIVE.  Every member is checked at its landing point against everything
  // that is not moving with it, so the HUD says "this drop will be refused" while the
  // pointer is still down, and a slide of one more step is all it takes.
  var chk = groupCheck(GHOST.p0, ox, oy);
  GHOST.warnings = chk.warnings; GHOST.brief = chk.brief; GHOST.problems = chk.problems;
  for (var mi = 0; mi < GHOST.p0.length; mi++) {
    var r0 = GHOST.p0[mi];
    liveMove(r0.id, r0.x + ox, r0.y + oy);
  }
  // the selection outline follows what it outlines: it is drawn from the same
  // `nodeById` positions `liveMove` just wrote
  paintOverlay();
  return { x: s.x, y: s.y, snapped: (s.x !== GHOST.x0 + dx) || (s.y !== GHOST.y0 + dy),
           guides: s.guides, warnings: chk.warnings, brief: chk.brief, problems: chk.problems,
           ids: GHOST.ids.slice(), contact: GHOST.contact || null };
}

function drop() {
  if (!GHOST) return null;
  var g = GHOST, i;
  var ox = (g.x === undefined ? 0 : g.x - g.x0), oy = (g.y === undefined ? 0 : g.y - g.y0);
  // ONE move_site PER MEMBER, all stamped with the SAME meta.group, so a group drag is
  // one entry in the undo stack rather than N.
  var ops = [], op = null;
  // THE SAME CHECK THE HUD SHOWED, at the same points: a drop cannot be refused for a
  // reason the drag did not already say
  var chk = groupCheck(g.p0, ox, oy), problems = chk.problems;
  if (!g.noBoundary && (ox || oy)) {
    var memb = {}; for (i = 0; i < g.p0.length; i++) memb[g.p0[i].id] = 1;
    // a hair looser than the drag's own test, so rounding the landing to a thousandth
    // can never refuse a drop the drag accepted
    var touch = contactAt(g.p0, ox, oy, memb, boundaryMargin() - 0.15);
    if (touch) problems = problems.concat([{ code: 'overlap', targets: [touch.id, touch.against],
      message: touch.id + ' would overlap ' + touch.against + ' \u2014 marks do not overlap' }]);
  }
  for (i = 0; i < g.p0.length; i++) {
    var r = g.p0[i];
    var o = { method: 'move_site',
              args: [r.id, Q.pyFloat(r.x + ox), Q.pyFloat(r.y + oy)],
              kwargs: {}, meta: { group: g.group, src: 'stage' } };
    ops.push(o);
    if (r.id === g.anchor) op = o;
  }
  if (!op) op = ops[0];
  GHOST = null;
  hideHud();
  // THE CURSOR RESETS HERE, not only in the pointer adapter: the harness's drag step never
  // runs `end`, so a cursor left at 'grabbing' by the drop was a cursor no test could see.
  var pt = LASTPT;
  if (problems.length) {
    rebuild();
    if (pt) hover(L.ox + pt.lx * (L.sx || 1), L.oy + pt.ly * (L.sy || 1));
    return { op: op, ops: ops, problems: problems };
  }
  for (i = 0; i < ops.length; i++) EDITS.push(ops[i]);
  UNDONE.length = 0;
  // `L` reconciles the node this drop moved -- remembered here, not in the pointer
  // adapter, so a harness drop can press L too
  LASTMOVED = op.args[0];
  // THE DECLARED-LENGTH NOTE GOES TO THE PRICE STRIP, not to a toast per drop: `rebuild`
  // reads it off the device it just produced, and a toast that fired on every one of a
  // dozen drags was the noise that hid the one refusal that mattered.
  rebuild();
  if (pt) hover(L.ox + pt.lx * (L.sx || 1), L.oy + pt.ly * (L.sy || 1));
  return { op: op, ops: ops, problems: [], warnings: chk.warnings };
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

// ESCAPE MID-DRAG ENDS THE WHOLE PRESS.  The HUD and the ghost go with the drag, and the
// press that started it is forgotten too, so the pointer that is still down does nothing
// more -- it used to keep feeding `move()` and the "cancelled" drag carried on under a
// HUD that never went away.
function cancel() {
  GHOST = null;
  hideHud();
  DOWN = null; ARMED = null;
  rebuild();
}

// WHAT ESCAPE MEANS, in one place.  It used to be bound only in the page's own handler,
// which cleared the programme filter and never told the editor -- so the two selection
// models disagreed and `drop()` after Escape still committed the move.  The key handler
// and the headless harness both call this, so there is one order and one answer.
function escapeGesture() {
  // THE MENU IS THE THING NEAREST THE HAND: a panel at the pointer, opened by the press
  // just made, so it goes FIRST -- before the ruler and before the sketch.  Its submenu
  // is one more thing on the screen, and therefore one more press.
  if (MDLG) { modifyClose(); return 'menu'; }
  if (MENU && MENU.sub) { MENU.sub = null; paintMenu(); return 'menu'; }
  if (MENU) { menuClose(); return 'menu'; }
  // ONE THING PER PRESS, and a half-taken measurement is the thing nearest the hand: the
  // first Escape drops the points, the second leaves the tool.
  if (MEASURE && MPTS.length) { measureClear(); return 'measure'; }
  if (MEASURE) { measureToggle(false); return 'measure'; }
  // THE SAME TWO-PRESS RULE FOR A SHAPE: the first Escape drops the points of the shape
  // being drawn, the second leaves the tool.
  if (SKETCH && (SKPTS.length || SKDRAG)) { sketchClear(); return 'sketch'; }
  if (SKETCH) { sketchTool(null); return 'sketch'; }
  if (GHOST) { cancel(); setCursor(HOVERED); return 'drag'; }
  // A LIVE BAND IS A LIVE DRAG: Escape takes the dashed line away and the release makes
  // nothing.  It used to fall through to the selection branch, the line stayed drawn and
  // the release still created the segment.  The rest of the press is inert, as after
  // `cancel()`.
  if (BAND) { bandCancel(); DOWN = null; ARMED = null; return 'drag'; }
  // AN ARMED STAMP DISARMS IN ONE PRESS, ghost and all.  It used to take two: the first
  // Escape removed the ghost and left the tile armed, so the next pointer move drew the
  // ghost straight back and the second press was the one that actually disarmed.
  // (a ghost without an armed tile cannot exist: `ghostBegin` arms, `setArmed` cancels)
  if (ARMED_EL) { ghostCancel(); arm(null); return 'armed'; }
  if (MARQ) { MARQ = null; DOWN = null; ARMED = null; paintOverlay(); return 'marquee'; }
  if (SELSET.length) {
    setSelection([]);
    if (typeof selectRef === 'function') selectRef(null, null);
    return 'selection';
  }
  if (FORM) { FORM = null; paint(); return 'form'; }
  if (HELPON) { helpToggle(false); return 'help'; }
  return null;
}

// THE CURSOR IS STATE, and it must be readable.  CSS said `svg.editing{cursor:default}`,
// so in edit mode nothing on the stage looked draggable -- and `classList` is a no-op in
// the harness, so no test could have caught that.  Written to `style`, published here.
var CURSOR = '', PANNING = false;
function cursor() { return CURSOR; }
function setCursor(h) {
  // MEASURE MODE OWNS THE CURSOR.  It owns every press too (`claim` below), so a stage
  // that still said "grab" would be promising a drag that cannot happen.
  var c = (MEASURE || SKETCH) ? 'crosshair' :
          (GHOST || PANNING) ? 'grabbing' :
          h ? 'move' : 'grab';
  CURSOR = c;
  if (typeof svg !== 'undefined' && svg && svg.style) svg.style.cursor = c;
  return c;
}
// THE PAN IS THE PAGE'S GESTURE BUT THE CURSOR IS THE EDITOR'S STATE.  render.py's pan
// handler used to write `svg.classList.add('drag')`, which the harness cannot read back
// and which fought the inline cursor written here.  One writer, one channel.
function panning(on) {
  PANNING = !!on;
  return setCursor(PANNING ? null : HOVERED);
}

// A NEW EDIT FORGETS WHAT WAS UNDONE -- on both stacks.  `UNDONE` was always cleared,
// but the canvas redo stack survived a new edit: undo a gallery pick, drag a node on the
// device that came back, press Redo, and the other device replaced the one just edited.
// Linear history, one rule, one place to apply it.
function forgetRedo() {
  UNDONE.length = 0;
  CANVAS_REDO.length = 0;
}
function commit(op) {
  EDITS.push(op);
  forgetRedo();
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

// A NEW DEVICE IS A GESTURE, SO IT IS UNDOABLE.  A gallery pick or a blank canvas used to
// set `EDITS = []` and throw the previous device away with its whole edit history, so the
// one click that could lose an hour of work was the one click with no undo.  The previous
// device is kept here as the same snapshot `saveProject` writes, and `undo()` reaches for
// it only once the edit stack is empty -- so ctrl+Z takes back the drags made on the new
// device first, then the device itself, in the order the user made them.  Restoring goes
// through `restore()`, the one applier a snapshot has; nothing here replays anything.
var CANVAS_UNDO = [], CANVAS_REDO = [];
function canvasRecord() {
  // the document without the architecture: `restore` rebuilds it from geom/seed/post/
  // edits/program, so serialising a 168-node device here would be work thrown away
  var s = documentRecord(null);
  // the redo stack belongs to the device it was popped from: restoring a device and then
  // redoing the OTHER device's last edit onto it is what the record exists to prevent.
  // `transaction` blanks this on the record it keeps -- a new device is a new gesture.
  s.undone = UNDONE.slice();
  return s;
}
function canvasHistory() { return { undo: CANVAS_UNDO.length, redo: CANVAS_REDO.length }; }
function sameCanvas(a, b) {
  var key = function (s) { return JSON.stringify([s.geom, s.seed, s.post, s.edits, s.program]); };
  return key(a) === key(b);
}
function restoreCanvas(rec) {
  var r = restore(rec);
  if (r.ok) {
    UNDONE.length = 0;
    for (var i = 0; i < (rec.undone || []).length; i++) UNDONE.push(rec.undone[i]);
    paint();
  }
  return r;
}
// a refused restore is said out loud: a ctrl+Z that does nothing, with Undo still lit,
// is the one silent failure a history can have
// The stacks move BEFORE the restore: `restoreCanvas` paints, and the paint reads both
// stacks for the Undo/Redo buttons -- pushed afterwards, the button for the step just
// made stayed disabled until some unrelated paint.  A refused restore puts both back.
function canvasStep(from, to) {
  var rec = from.pop(), now = canvasRecord();
  to.push(now);
  var r = restoreCanvas(rec);
  if (r.ok) return;
  to.pop();
  from.push(rec);
  toast('bad', ((r.problems || [])[0] || {}).message || 'the other device could not be restored');
}
function undo() {
  if (EDITS.length) { UNDONE.push(EDITS.pop()); rebuild(); return; }
  if (CANVAS_UNDO.length) canvasStep(CANVAS_UNDO, CANVAS_REDO);
}
function redo() {
  if (UNDONE.length) { EDITS.push(UNDONE.pop()); rebuild(); return; }
  if (CANVAS_REDO.length) canvasStep(CANVAS_REDO, CANVAS_UNDO);
}

// During a drag: write the dragged node's marks and its incident segment endpoints
// directly, without recomputing the layout.
//
// A BOWED SEGMENT STAYS BOWED, and it keeps its electrodes.  This used to rewrite every
// incident segment to a straight `M ... L ...` and hide its DC pads for the duration of
// the drag.  Both were lies about the hardware, and both were visible: a bow exists to
// route a rail AROUND a node it does not touch, so straightening it mid-drag drew the
// rail straight THROUGH that node -- and an ion riding the same segment is drawn on the
// curve (`bezPoint`), so the ion left the rail the moment you touched it.  The control
// point is the one thing that has to be recomputed: `L.bows` is a per-segment offset in
// pixels and the layout is frozen for the whole drag, so the bow amount is carried and
// only the endpoints move.  The pads then ride the new curve -- they were already
// evaluated on it, they were just being hidden.
function rebowSegment(sg, I, ax, ay, bx, by) {
  var bw = (L.bows || {})[sg.id];
  var dx = bx - ax, dy = by - ay, len = Math.sqrt(dx * dx + dy * dy);
  I.ax = ax; I.ay = ay; I.dx = dx; I.dy = dy; I.len = len;
  I.cp = (bw && len > 1e-6)
    ? { x: (ax + bx) / 2 - (dy / len) * 2 * bw, y: (ay + by) / 2 + (dx / len) * 2 * bw }
    : null;
  I.alen = (typeof bezLen === 'function') ? bezLen(I) : len;
  return I;
}
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
  // the moved node's OWN electrodes travel with it, rigidly: they are its well, and a
  // well that stayed behind while its site moved would be the clearest possible way to
  // say the wrong thing about what holds an ion
  movePadRun(PAD_BY_SITE[nid], nx, ny, (rec && rec.ax) || AXIS[nid] || { ux: 1, uy: 0 });
  for (var i = 0; i < A.segments.length; i++) {
    var sg = A.segments[i];
    if (sg.a !== nid && sg.b !== nid) continue;
    var a = nodeById[sg.a], b = nodeById[sg.b], ln = SEGEL[sg.id];
    if (!a || !b || !ln) continue;
    var ax = px(a), ay = py(a), bx = px(b), by = py(b);
    var I = SEGINFO[sg.id];
    if (I) rebowSegment(sg, I, ax, ay, bx, by);
    if (ln.tagName === 'path') {
      ln.setAttribute('d', (I && I.cp)
        ? 'M ' + ax + ' ' + ay + ' Q ' + I.cp.x + ' ' + I.cp.y + ' ' + bx + ' ' + by
        : 'M ' + ax + ' ' + ay + ' L ' + bx + ' ' + by);
    } else { ln.setAttribute('x1', ax); ln.setAttribute('y1', ay);
             ln.setAttribute('x2', bx); ln.setAttribute('y2', by); }
    if (I) movePadsOnSegment(PAD_BY_SEG[sg.id], I);
  }
}
// Re-place one pair of electrodes at a point, facing along (tx, ty).  The same arithmetic
// `buildStatic`'s `padPair` uses, and the only copy of it that is allowed to exist: it
// writes the SAME record fields, so `clearTransients` keeps working on a pad that moved.
function placePair(pair, x, y, tx, ty, w) {
  var ang = Math.atan2(ty, tx) * 180 / Math.PI, nx = -ty, ny = tx, j;
  pair.x = x; pair.y = y; pair.tx = tx; pair.ty = ty; pair.w = w; pair.ang = ang;
  for (j = 0; j < pair.pads.length; j++) {
    var q = pair.pads[j];
    q.cx = x + nx * L.pad_off * q.sign; q.cy = y + ny * L.pad_off * q.sign;
    q.el.setAttribute('x', q.cx - w / 2); q.el.setAttribute('y', q.cy - L.pad_t / 2);
    q.el.setAttribute('width', w);
    q.el.setAttribute('transform', 'rotate(' + ang + ' ' + q.cx + ' ' + q.cy + ')');
  }
}
// a site's own pairs: `pair.t` is the index of the pair in the stack, so the offsets are
// rebuilt from the pitch the tiling was drawn at rather than remembered per pad
function movePadRun(list, x, y, ax) {
  if (!list || !list.length) return;
  var pit = list.pitch || 0, m = list.length, i;
  for (i = 0; i < m; i++) {
    var o = (i - (m - 1) / 2) * pit;
    placePair(list[i], x + ax.ux * o, y + ax.uy * o, ax.ux, ax.uy, list[i].w);
  }
}
// a rail's pairs: each keeps its parameter along the segment and is re-evaluated on the
// curve the drag just produced
function movePadsOnSegment(list, I) {
  if (!list || !list.length || typeof bezPoint !== 'function') return;
  for (var i = 0; i < list.length; i++) {
    var q = bezPoint(I, list[i].t);
    placePair(list[i], q.x, q.y, q.tx, q.ty, list[i].w);
  }
}

// `skip`: the other members of a rigid group -- a segment whose far end moves with this
// node keeps its direction and its length, so it is not the one to warn about
function breaksAxisAlignment(nid, x, y, skip) {
  for (var i = 0; i < A.segments.length; i++) {
    var sg = A.segments[i];
    if (sg.a !== nid && sg.b !== nid) continue;
    var oid = sg.a === nid ? sg.b : sg.a;
    if (skip && skip[oid]) continue;
    var other = nodeById[oid];
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
//
// THE ONE TEST FOR A SEGMENT: its drawn length against its declared one, 20% tolerance.
// `agg.count` segments are off, `agg.worst` is [id, ratio, declared, |ratio-1|] of the
// worst of them.  Asked of a live drag (`mismatchScan`, members at their landing points)
// and of the whole device (`deviceMismatch`, once per rebuild, for the price strip).
function segMismatch(sg, drawn, agg) {
  var decl = sg.len === undefined ? 1.0 : sg.len;
  if (decl <= 0) return agg;
  var ratio = drawn / decl;
  if (Math.abs(ratio - 1) > 0.2) {
    agg.count++;
    if (!agg.worst || Math.abs(ratio - 1) > agg.worst[3]) agg.worst = [sg.id, ratio, decl, Math.abs(ratio - 1)];
  }
  return agg;
}
// ONE SCAN, accumulated over a group: a member at (x, y) against the segments it is on.
// A segment whose far end is another member (`skip`) keeps its drawn length and is not
// counted.
function mismatchScan(nid, x, y, skip, agg) {
  for (var i = 0; i < A.segments.length; i++) {
    var sg = A.segments[i];
    if (sg.a !== nid && sg.b !== nid) continue;
    var oid = sg.a === nid ? sg.b : sg.a;
    if (skip && skip[oid]) continue;
    var other = nodeById[oid];
    if (!other) continue;
    segMismatch(sg, Math.sqrt((other.x - x) * (other.x - x) + (other.y - y) * (other.y - y)), agg);
  }
  return agg;
}
// every segment of the device as it stands, each counted once; null when none is off
function deviceMismatch() {
  var agg = { count: 0, worst: null };
  for (var i = 0; i < A.segments.length; i++) {
    var sg = A.segments[i], a = nodeById[sg.a], b = nodeById[sg.b];
    if (!a || !b) continue;
    segMismatch(sg, Math.sqrt((a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y)), agg);
  }
  return agg.worst ? agg : null;
}
function mismatchText(agg) {
  var which = agg.worst, count = agg.count;
  if (!which) return null;
  var scaling = !!(D.model && D.model.length_scaling);
  return count + ' incident segment(s) are now up to ' + which[1].toFixed(2) +
         'x their declared length ' + which[2] + '. ' +
         (scaling ? 'This model DOES scale cost by length, so the price is now wrong until '
                  + 'you reconcile them.'
                  : 'This model ignores segment length, so this changes the drawing, not '
                  + 'the cost.');
}
// the same fact, sized for the price strip -- written by `rebuild()` only, so it can
// never describe a device other than the one on the stage
var LENGTH_NOTE = null;
function mismatchShort(agg) {
  if (!agg || !agg.worst) return null;
  var scaling = !!(D.model && D.model.length_scaling);
  return agg.count + ' segment(s) drawn up to ' + agg.worst[1].toFixed(2) + 'x their declared length' +
         (scaling ? ' (this model prices length: press L to reconcile)' : ' (press L to reconcile)');
}
// and sized for the HUD's second line
function mismatchHud(agg) {
  if (!agg || !agg.worst) return null;
  return agg.count + ' segment(s) up to ' + agg.worst[1].toFixed(2) + 'x declared length';
}
function lengthNote() { return LENGTH_NOTE; }

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
  if (made) { forgetRedo(); rebuild(); }
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

// A TOPOLOGY ADD: applied to the sealed device, exported through `m.apply_edits`.  With
// `near` it copies zone and capacity from that node (the double-click on an existing
// device); with `opts.kind`/`opts.zone` it places what the palette asked for, inheriting
// the capacity from the zone as `d.site(zone=...)` would.
function addSite(x, y, near, opts) {
  opts = opts || {};
  var proto = near ? nodeById[near] : null;
  var kind = opts.kind === 'junction' ? 'junction' : 'site';
  // `add_junction` is its own whitelist entry on both sides: Python's `add_site` lambda
  // does not forward `kind`, so a junction sent as `add_site` would come back a SITE
  // with no zone and be refused by the toolchain the export is for.
  // THE SAME REFUSAL AS `addNodeAt`: the double-click and the armed click on a generator
  // device come through here after a hard lattice snap, so the point they land on can be
  // one a node already holds while the pointer is outside that node's hit halo.  The
  // engine accepts the second node -- `min_nearest_neighbour` skips the coincident pair,
  // so nothing downstream would ever say so.
  var coin = coincidentAt(x, y);
  if (coin) return { ok: false, problems: [coin] };
  var args = { id: freshId(kind === 'junction' ? 'J' : 'N'), pos: [x, y], labels: ['added'] };
  if (kind === 'site') {
    // a junction has no zone to copy, so the nearest node being one used to refuse the
    // double-click outright ("a site needs capacity >= 1"); a declared zone is the
    // honest default there, exactly as the armed stamp would use
    var fromProto = !!(proto && proto.zone);
    var zone = opts.zone !== undefined ? (opts.zone || null)
             : fromProto ? proto.zone
             : defaultZone();
    args.zone = zone;
    args.capacity = opts.capacity !== undefined ? opts.capacity
                  : fromProto ? proto.cap : (zone ? 0 : 1);
    args.zone_types = STATE.zone_types;
  }
  var op = { topology: { op: kind === 'junction' ? 'add_junction' : 'add_site', args: args },
             meta: { group: 'g' + (++GROUP), src: 'stage' } };
  var r = tryTopology(op);
  // an id only for a node that exists: a refusal used to carry the id it did not place
  if (r.ok) r.id = args.id;
  return r;
}
// does the device still have a builder, i.e. can a `d.site` be hoisted above its seal?  A
// generator device (`blank`, `from_template`) has none: its nodes are topology edits.
function hasBuilder() { return !!(STATE && STATE.builder); }

function addSegment(a, b, opts) {
  opts = opts || {};
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
  var hasLen = opts.length !== undefined && opts.length !== null;
  var hasCap = opts.capacity !== undefined && opts.capacity !== null;
  var args = { id: freshId('X'), a: a, b: b,
               labels: (opts.labels && opts.labels.length) ? opts.labels.slice() : ['chord'],
               length: hasLen ? Number(opts.length) : (Math.round(len * 100) / 100 || 1.0),
               capacity: hasCap ? Math.trunc(Number(opts.capacity)) : 1 };
  var op = { topology: { op: 'add_segment', args: args },
             meta: { group: 'g' + (++GROUP), src: 'stage' } };
  var r = tryTopology(op);
  if (r.ok) r.id = args.id;
  return r;
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
      // ONE SENTENCE, ONE PLACE: the menu shows this as a DISABLED item carrying the
      // same words, so the two can never drift into different explanations of one gap.
      return { ok: false, problems: [{ code: 'no_remove_loop', targets: [sel.id],
        message: noRemoveLoopWhy(sel.id) }] };
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
  forgetRedo();
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
  function same(s, text) {
    return text !== undefined && (Q.render(s) === text || (s.text !== undefined && s.text === text));
  }
  // THE CANVAS'S OWN BUILDER STATEMENTS ARE IN THE TEXT TOO.  `baseTexts()` lists the
  // shipped builder lines (GEOM), then the `{build}` edits the canvas made, then the seal
  // and the retunes.  A line matching the base was skipped as unchanged, and the
  // `EDITS = topo.concat(next)` that followed kept no `{build}` edit -- so on a device
  // built by hand, applying the Source text emptied it.  Measured 2026-09-16 on lesson
  // A2's canvas: the text applied UNCHANGED took the device from 6 nodes and 4 segments
  // to none, and the lesson's step, which checks only the zone, still passed.
  //
  // So the leading builder statements are matched to the build edits that wrote them (a
  // match keeps the edit, its label and group), a new or retyped one becomes a build
  // edit, and one deleted from the text is dropped.  A shipped builder line (GEOM) that
  // was changed goes through as a command edit, as before, and is refused there.
  var builds = EDITS.filter(function (o) { return !!o.build; });
  var nb = GEOM.length + builds.length;          // builder lines at the head of `base`
  var head = 0;
  while (head < p.stmts.length && kindOf(p.stmts[head].method) === 'build') head++;
  var keep = [], next = [], typedBuild = null, bi = 0, i, s;
  for (i = 0; i < head; i++) {
    s = p.stmts[i];
    if (i < GEOM.length) {
      if (same(s, base[i])) continue;                                    // shipped, unchanged
      next.push({ method: s.method, args: s.args, kwargs: s.kwargs, meta: { group: 'text', src: 'text' } });
      continue;
    }
    var hit = -1;
    for (var k = bi; k < builds.length && hit < 0; k++) if (same(s, base[GEOM.length + k])) hit = k;
    if (hit >= 0) { keep.push(builds[hit]); bi = hit + 1; continue; }
    if (!typedBuild) typedBuild = s;
    keep.push({ build: { method: s.method, args: s.args, kwargs: s.kwargs }, meta: { group: 'text', src: 'text' } });
  }
  // From the seal on, index by index against the base from ITS seal on: a builder line
  // added or removed above must not shift every later line into an "edit".
  for (i = head; i < p.stmts.length; i++) {
    s = p.stmts[i];
    if (same(s, base[nb + (i - head)])) continue;                        // unchanged
    next.push({ method: s.method, args: s.args, kwargs: s.kwargs,
                meta: { group: 'text', src: 'text' } });
  }
  // A typed program is authoritative for the whole command list, so it REPLACES the
  // command edits; topology edits are geometry and survive.
  var topo = EDITS.filter(function (o) { return !!o.topology; });
  var edits = keep.concat(topo, next);
  // TRY IT FIRST, as `transaction` does: a builder statement that breaks the seal (a
  // segment to a node the text no longer has) makes the whole base program refuse, and
  // committing that would leave the page with no architecture rather than a refused line.
  var trial = Q.applyProgram(baseCallsFrom(GEOM, SEED, POST, edits));
  if (trial.error) {
    var blame = Q.buildProblems(baseCallsFrom(GEOM, SEED, POST, edits));
    var why = blame.length ? blame[0].message : trial.error.message;
    return { ok: false,
             errors: [{ line: (typedBuild && typedBuild.line) || 1, col: 1, message: why }],
             problems: blame.map(function (b) {
               return { i: null, code: b.code, method: b.method, message: b.message }; }) };
  }
  EDITS = edits;
  forgetRedo();
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
  // `DeviceBuilder` is not exported from the package root, and every explicit listing
  // opens with `d = DeviceBuilder(...)` -- so the blank page's own export died on its
  // first line with a NameError.  Imported whenever a builder statement will be printed.
  var anyBuild = GEOM.length > 0 || EDITS.some(function (o) { return !!o.build; });
  var head = '# edited in the browser, from ' + A.name + '\n' +
             '# ' + EDITS.length + ' edit(s) on top of the shipped architecture\n' +
             (anyBuild ? 'from qccd.api import Machine, DeviceBuilder\n'
                       : 'from qccd import Machine\n') +
             (anyTopo ? 'import json\n' : '') + '\n';
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
  var nprob = PROBLEMS.length + lintProblems().length, nnote = lintNotes().length;
  EL.prob.textContent = nprob + (nprob === 1 ? ' problem' : ' problems');
  if (EL.notes) {
    EL.notes.textContent = nnote + (nnote === 1 ? ' note' : ' notes');
    // `style.display`, never a class: `classList` is a no-op in tests/shim.mjs
    if (EL.notes.style) EL.notes.style.display = nnote ? '' : 'none';
  }
  EL.undo.disabled = !EDITS.length && !CANVAS_UNDO.length;
  EL.redo.disabled = !UNDONE.length && !CANVAS_REDO.length;
  // THE EMPTY STATE lives on the stage, not below the fold: with no node to look at, the
  // canvas itself carries the start cards.  `style.display`, never a class, and never
  // removed -- the harness reads it back.
  var empty = $('stageEmpty');
  if (empty && empty.style) empty.style.display = (STATE && nodesOf(STATE).length) ? 'none' : 'block';
  EL.snap.setAttribute('aria-pressed', SNAP ? 'true' : 'false');
  EL.snap.className = 'tgl' + (SNAP ? ' on' : '');
  if (EL.trueS) {
    EL.trueS.setAttribute('aria-pressed', TRUE_SCALE ? 'true' : 'false');
    EL.trueS.className = 'tgl' + (TRUE_SCALE ? ' on' : '');
  }
  if (EL.meas) {
    EL.meas.setAttribute('aria-pressed', MEASURE ? 'true' : 'false');
    EL.meas.className = 'tgl' + (MEASURE ? ' on' : '');
  }
  var dm = designMode();
  if (EL.mode) {
    for (var mk in EL.mode) if (has(EL.mode, mk) && EL.mode[mk]) {
      EL.mode[mk].setAttribute('aria-pressed', dm === mk ? 'true' : 'false');
      EL.mode[mk].className = 'tgl' + (dm === mk ? ' on' : '');
    }
  }
  // THE SHAPE TOOLS ARE THE RAIL'S TILES AND THE r/e/n/p KEYS, and nothing else.  The
  // stage toolbar carried a third copy of the same four buttons wired to the same
  // `sketchTool` verb; `renderPalette` already paints each rail tile's `aria-pressed`
  // from `SKETCH` on every paint, so there is nothing left here to keep in step -- and
  // `#tShapes`, the span that had to be hidden in Parts mode, is gone with them.
  // numbers only on the strip; the sentence about what they are worth is the Report's
  EL.price.textContent = priceHead();
  if (EL.src && document.activeElement !== EL.src) EL.src.value = sourceText();
  // ONLY WHEN THE BOX IS ON SCREEN.  Serialising the whole document to Python or JSON is
  // 6-7 ms of every paint on the shipped grid and ring (measured: exportPython 2.7 ms +
  // exportJson 4.6 ms), and the box lives in the Architecture pane's Source view, which
  // is closed almost always -- so a drag paid for a string nobody could read.  Opening
  // that view calls `setArchView('src')`, which makes the wrapper visible and then calls
  // `setMode('edit')`, and `setMode` always paints: by then `offsetParent` is set and the
  // box fills.  `!== null` rather than a truth test on purpose -- `tests/shim.mjs`
  // elements have no `offsetParent` at all, so the harness keeps computing it and every
  // export assertion still runs.
  if (EL.out && EL.out.offsetParent !== null) {
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
  renderHead();
  paintOverlay();
}

// THE HEAD SAYS WHAT IS ON THE STAGE.  Python wrote the name, the one-line lede and the
// window title for the device and programme it shipped, and they stayed put through a
// generator card, a blank canvas and an authored programme -- "an empty canvas: ..."
// over an 8-site ring.  The moment the state is not the shipped one those three lines
// are re-derived from it; the moment it is the shipped one again they are put back
// exactly, so a page nobody has changed is never rewritten.  The counters and the metric
// chips are the page's own (`paintHead`), told the same thing.
var HEAD0 = null;
function renderHead() {
  var title = $('title'), lede = $('lede');
  var mine = !!(STATE && nodesOf(STATE).length &&
                (!shippedSeed() || AUTHORED || SHIPPED_EMPTY));
  if (!mine) {
    if (HEAD0) {
      if (title) title.textContent = HEAD0.title;
      if (lede) lede.textContent = HEAD0.lede;
      document.title = HEAD0.doc;
      HEAD0 = null;
    }
  } else {
    if (!HEAD0) {
      HEAD0 = { title: title ? title.textContent : '', lede: lede ? lede.textContent : '',
                doc: String(document.title || '') };
    }
    var sites = 0, nid;
    for (nid in STATE.device.nodes) if (has(STATE.device.nodes, nid)) {
      if (STATE.device.nodes[nid].kind !== 'junction') sites++;
    }
    var k = P.n_instructions === undefined ? P.frames.length : P.n_instructions;
    var name = STATE.name + ' - ' + (PROG.length ? 'programme' : 'design');
    if (title) title.textContent = name;
    if (lede) {
      lede.textContent = sites + ' site' + (sites === 1 ? '' : 's') + ' · ' +
        segsOf(STATE).length + ' segment' + (segsOf(STATE).length === 1 ? '' : 's') +
        ' · ' + k + ' instruction' + (k === 1 ? '' : 's');
    }
    document.title = name;
  }
  if (typeof paintHead === 'function') paintHead();
}

// THE NUMBERS THE HEAD AND THE REPORT SHARE: one list, read by both, so the chips over
// the stage and the Backed table cannot print different figures for the same price.
// Empty while the price is refused -- a chip for a number that was not computed is the
// one thing this surface must never show.
function metricRows() {
  if (!PRICE || PRICE.blocked) return [];
  var rows = [
    ['cost', fmt(PRICE.totals.cost)],
    ['steps', fmt(PRICE.totals.steps)],
    ['runtime', fmt(PRICE.totals.us / 1000, 2) + ' ms'],
    ['peak n-bar', fmt(PRICE.peak, 3) + (PRICE.peakIon ? ' (' + PRICE.peakIon + ')' : '')],
    ['junction transits', fmt(PRICE.transits)]];
  if (HW) {
    rows.push(['DACs', fmt(HW.dacs)], ['electrodes', fmt(HW.electrodes)],
              ['switches', fmt(HW.switches)], ['ion capacity', fmt(HW.total_capacity)]);
  }
  return rows;
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
// animating a programme whose node ids may no longer exist.  Two halves: `priceHead` is
// the numbers, for the toolbar strip; `priceNote` is the sentence about what they are
// worth -- the oracle, the CLI to re-verify with -- which lives in the Report pane, where
// there is room for a sentence.
function priceHead() {
  if (!READY) return WHY_NOT || 'editing unavailable';
  if (!PRICE) return '';
  if (PRICE.blocked) return 'price unavailable · ' + breakMessage(PRICE.blocked[0]);
  var t = PRICE.totals;
  var same = !EDITS.length && !PROG.length;
  var head = 'cost ' + fmt(t.cost) + ' · steps ' + fmt(t.steps) +
             ' · runtime ' + fmt(t.us / 1000, 2) + ' ms · n̄ ' +
             fmt(PRICE.comp.shuttle + PRICE.comp.junction + PRICE.comp.split_merge, 1);
  if (HW) head += ' · ' + fmt(HW.dacs) + ' DACs';
  if (HW0 && HW && HW.dacs !== HW0.dacs) head += ' (' + (HW.dacs > HW0.dacs ? '+' : '') + fmt(HW.dacs - HW0.dacs) + ')';
  if (LENGTH_NOTE) head += ' · ' + LENGTH_NOTE;
  // a device from a gallery card or a blank canvas is NEW, not "unedited": nothing about
  // it shipped with the page, and the word would claim a baseline that does not exist
  if (same) return head + (shippedSeed() ? ' · unedited' : ' · new device');
  return head;
}
function priceNote() {
  if (!READY || !PRICE) return '';
  if (PRICE.blocked) return 'recompile in Python';
  if (!EDITS.length && !PROG.length) return '';
  // THE FIFTH STATE.  The per-frame self-check compares each re-priced frame against the
  // cost PYTHON shipped for it; an AUTHORED programme has no such frames, so there is
  // nothing to compare and reporting `frameDrift === 0` over `frameChecked === 0` would be
  // a confident zero for a check that never ran.  The arithmetic is parity-tested; THIS
  // PROGRAMME is not, and the difference is the whole point of saying so.
  // NO PROGRAMME AT ALL comes first: with zero frames there is no pair to download and
  // no `--program` to name, and both sentences below would have named one anyway
  if (!P.frames.length) {
    return 'no programme yet: press Test drive, or write one in the Write pane';
  }
  if (PRICE_STATUS === 'unoracled' || !PRICE.frameChecked) {
    // a programme written HERE has no tsir pair to download: its return leg to Python is
    // the saved snapshot, which `qccd open` replays and prices from first principles
    if (AUTHORED) {
      return 'no per-frame oracle: these frames were never priced by Python. ' +
             'Save (ctrl+S) and run: python -m qccd open ' +
             ((STATE && STATE.name) || 'design') + '.studio.json';
    }
    return 'no per-frame oracle: these frames were never priced by Python. ' +
           'Download the pair and run: python -m qccd run ' + A.name +
           '.arch.json --tsir ' + A.name + '.tsir.json';
  }
  if (PRICE.frameDrift === 0) {
    return 're-priced client-side; every one of ' + fmt(PRICE.frameChecked) +
           ' frames still agrees with the Python verifier';
  }
  if (!EDITS.some(function (o) { return priceAffecting(o); })) {
    return 'price unchanged: this model ignores the geometry you changed';
  }
  return 're-priced client-side · re-verify in Python: ' +
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
// A COMPATIBILITY SHIM.  There are no modes: every gesture works at all times, while the
// animation runs or not, and `.disabled` on the transport has exactly one writer --
// render.py's `onProgramValidity` (disabled iff PROGRAM_STALE).  This used to stop the
// animation and disable four buttons that `onProgramValidity` then re-enabled after the
// first drop, so two owners disagreed about whether Play was available; and pressed
// mid-gesture it left a live drag orphaned, which `drop()` then committed.  It stays
// because the harness's `{do:'mode'}` step and about forty tests call it; what it still
// does is finish any gesture that was in flight, so no press can outlive the call.
function setMode(m) {
  if (GHOST) cancel();
  if (ARMED_EL) arm(null);
  MODE = m;
  paint();
}
function fmt(x, d) {
  return (x == null) ? '-' : Number(x).toLocaleString(undefined, { maximumFractionDigits: d || 0 });
}

// ------------------------------------------------------------------- toasts
//
// `textContent` only, never `innerHTML`: an id typed into the side editor is untrusted
// text and it ends up in these messages.
//
// KEYED BY KIND + MESSAGE, so the same refusal twice is shown once (re-shown, moved to
// the end, its timer restarted) rather than stacked; CAPPED at the last three, so a run
// of refusals cannot climb over the toolbar; and a click dismisses one.  Hidden toasts
// are dropped from the host when the next one arrives -- `remove()` is a no-op in the
// harness, so the host is rebuilt with `replaceChildren` from the ones still showing.
var TOASTS = {}, TOAST_MAX = 3;
function toast(kind, message) {
  var host = $('toasts');
  if (!host) return null;
  var key = (kind || '') + ':' + message, t = TOASTS[key];
  if (!t) {
    t = document.createElement('div');
    t.className = 'toast' + (kind ? ' ' + kind : '');
    t.textContent = message;
    t.onclick = function () { t.style.display = 'none'; };
    TOASTS[key] = t;
  }
  t.style.display = '';
  var keep = [], i;
  for (i = 0; i < host.children.length; i++) {
    var c = host.children[i];
    if (c !== t && c.style && c.style.display !== 'none') keep.push(c);
  }
  keep.push(t);
  while (keep.length > TOAST_MAX) keep.shift();
  host.replaceChildren.apply(host, keep);
  if (!SYNC) {
    if (t._timer) clearTimeout(t._timer);
    t._timer = setTimeout(function () { t.style.display = 'none'; t._timer = null; }, 4500);
  }
  return t;
}
// what is showing, oldest first -- the harness's view of the strip
function toasts() {
  var host = $('toasts'), out = [];
  if (!host) return out;
  for (var i = 0; i < host.children.length; i++) {
    var c = host.children[i];
    if (c.style && c.style.display !== 'none') out.push({ kind: (c.className || '').replace(/^toast ?/, ''), message: c.textContent });
  }
  return out;
}

// ------------------------------------------------------------------- wiring
//
// Every handler here is a thin adapter: client coordinates in, `EDITOR.*` out.  Nothing
// decides anything inside an event listener, because the harness cannot fire one.
function wire() {
  EL.bar = $('ebar');
  if (!EL.bar) return;
  EL.snap = $('tSnap');
  EL.undo = $('eUndo'); EL.redo = $('eRedo'); EL.count = $('eCount');
  EL.prob = $('eProb'); EL.notes = $('eNotes');
  EL.price = $('ePrice'); EL.src = $('eSrc');
  EL.out = $('eOut'); EL.outWhich = 'py';

  EL.snap.onclick = function () { setSnap(!SNAP); unfocus(this); };
  try { var sv = STORE.getItem(SNAP_KEY); if (sv !== null) SNAP = sv === '1'; } catch (err) { /* no store */ }
  // The two stage tools the ruler work added.  `TRUE_SCALE` was already read from the
  // store by the page script -- it has to be, because `L` must be right before the first
  // mark is drawn -- so this only binds the button to the setter.
  EL.trueS = $('tTrue'); EL.meas = $('tMeasure');
  if (EL.trueS) EL.trueS.onclick = function () { setTrueScale(!TRUE_SCALE); unfocus(this); };
  if (EL.meas) EL.meas.onclick = function () { measureToggle(); unfocus(this); };
  // THE TWO MODES, and the shape tools that only mean anything in one of them.  The mode
  // is remembered exactly as Snap and True scale are; with nothing stored, a canvas with
  // no device on it opens on Sketch.
  try { var mv = STORE.getItem(MODE_KEY); if (mv === 'sketch' || mv === 'parts') DMODE = mv; }
  catch (err) { /* no store */ }
  EL.mode = { sketch: $('tModeSketch'), parts: $('tModeParts') };
  if (EL.mode.sketch) EL.mode.sketch.onclick = function () { setDesignMode('sketch'); unfocus(this); };
  if (EL.mode.parts) EL.mode.parts.onclick = function () { setDesignMode('parts'); unfocus(this); };
  // (no shape buttons to wire here any more: the rail's tiles and r/e/n/p own the tools)
  // ONE GESTURE PER PRESS: a group drag or a nudge comes back in one step, not N.  The
  // single-edit `undo`/`redo` stay on the API for scripts that want the finer grain.
  EL.undo.onclick = function () { undoGroup(); unfocus(this); };
  EL.redo.onclick = function () { redoGroup(); unfocus(this); };
  // BOTH REGISTERS, EACH UNDER ITS OWN HEADING.  The chip counts problems; the notes are
  // one click away here as well as on their own chip, so the split changes which number
  // is shouted and nothing about what can be read.
  EL.prob.onclick = function () {
    var probs = PROBLEMS.map(function (p) { return 'statement ' + p.i + ': ' + p.message; })
      .concat(lintProblems().map(function (l) { return l.code + ': ' + l.message; }));
    var notes = lintNotes().map(function (l) { return l.code + ': ' + l.message; });
    var rows = [];
    rows.push(probs.length ? ('Problems \u2014 ' + probs.join('  \u00b7  '))
                           : 'No problems: nothing you have drawn breaks a rule.');
    if (notes.length) {
      rows.push('Notes, about what is not there yet \u2014 ' + notes.join('  \u00b7  '));
    }
    toast(probs.length ? 'warn' : 'ok', rows.join('\n'));
    unfocus(this);
  };
  if (EL.notes) EL.notes.onclick = function () {
    var notes = lintNotes().map(function (l) { return l.code + ': ' + l.message; });
    toast('ok', notes.length
      ? ('Nothing is wrong \u2014 these are about what is not there yet:\n' +
         notes.join('  \u00b7  '))
      : 'no notes');
    unfocus(this);
  };
  var helpBtn = $('eHelp');
  if (helpBtn) helpBtn.onclick = function () { helpToggle(); unfocus(this); };
  var helpHost = $('help');
  if (helpHost && helpHost.addEventListener) {
    // the backdrop closes it; a click inside the card does not
    helpHost.addEventListener('click', function (e) { if (e.target === helpHost) helpToggle(false); });
  }
  renderHelp();
  var pw = $('pwText');
  if (pw) {
    // ONE WAY TO RUN A PROGRAMME: the Evaluate button.  The pane used to apply itself on
    // blur as well, silently and without the button's feedback -- so Escape, which is
    // documented as "only leaves the field", ran whatever was half-typed.  Typing only
    // marks the text as the user's, so a repaint does not overwrite it.
    pw.oninput = function () { pw._touched = true; };
  }
  var run = $('pwRun');
  if (run) run.onclick = function () { evaluateWrite(); };
  var drive = $('pwDrive');
  if (drive) drive.onclick = function () { pressTestDrive(); };
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
    unfocus(copy);
  };
  if (EL.src) {
    EL.src.oninput = function () {
      if (SRCT) clearTimeout(SRCT);
      var run = function () {
        SRCT = null;
        var r = applySource(EL.src.value);
        var strip = $('eSrcErr');
        if (strip) {
          // a statement the applier REFUSED arrives with `problems` and no parse error:
          // `r.errors[0].line` threw there, and the strip said nothing
          var e0 = r.errors && r.errors[0], p0 = r.problems && r.problems[0];
          strip.textContent = r.ok ? ''
            : e0 ? ('line ' + e0.line + ' col ' + e0.col + ': ' + e0.message)
            : (p0 ? p0.message : 'not applied');
        }
      };
      if (SYNC) run(); else SRCT = setTimeout(run, 220);
    };
  }

  // -- the explain layer: one-line adapters ------------------------------------------
  var ex = $('eExplain');
  if (ex) ex.onclick = function () { explainToggle(); unfocus(ex); };
  if (document.addEventListener) {
    document.addEventListener('mouseover', function (e) { hintFrom(e.target, e.clientX, e.clientY); });
    document.addEventListener('focusin', function (e) { hintFrom(e.target); });
    document.addEventListener('focusout', function () { hintHide(); });
  }
  renderCaptions();

  // -- the stage ---------------------------------------------------------------------
  // No mode guard anywhere below: `claim` arbitrates every press, at all times.
  svg.addEventListener('pointerdown', function (e) {
    var m = toModel(e.clientX, e.clientY);
    // THE SAME ARBITER the page's pan handler asked, so the two can never disagree about
    // who owns this press.
    var who = claim(m.x, m.y, { button: e.button, alt: e.altKey,
                                shift: e.shiftKey, ctrl: e.ctrlKey });
    // A PAN-CLAIMED PRESS IS STILL REMEMBERED, because a press that never moves is a
    // click and not a pan: releasing on empty stage clears the selection or places the
    // armed stamp.  `pointermove` leaves it alone (the page pans) and `end` reads it back
    // only when the pointer travelled under the click threshold with the left button.
    // ANY OTHER PRESS PUTS THE MENU AWAY.  There is one panel and it belongs to the
    // press that opened it; a press inside the panel never reaches this handler.
    if ((MENU || MDLG) && who !== 'menu') menuClose();
    // THE RIGHT-CLICK MENU.  The press SELECTS what is under it (unless that is already
    // part of the selection, in which case the menu acts on all of it) and opens every
    // verb that applies.  All the logic is in `menuOpen`; this is the adapter.
    if (who === 'menu') {
      DOWN = null; ARMED = null;
      menuOpen(m.x, m.y, { cx: e.clientX, cy: e.clientY });
      return;
    }
    if (who === 'measure') { DOWN = null; ARMED = null; measureClick(m.x, m.y); return; }
    // THE SHAPE TOOL, function for function as the ruler: the press begins the drag (or,
    // for the polyline, drops one point), `pointermove` drives the preview and the release
    // commits.  A drag that leaves the stage must keep arriving, so it captures too.
    if (who === 'sketch') {
      DOWN = null; ARMED = null;
      sketchDown(m.x, m.y, { shift: e.shiftKey });
      if (svg.setPointerCapture) { try { svg.setPointerCapture(e.pointerId); } catch (err) {} }
      return;
    }
    DOWN = { cx: e.clientX, cy: e.clientY, mx: m.x, my: m.y,
             hit: who === 'pan' ? null : hit(m.x, m.y),
             shift: e.shiftKey, alt: e.altKey, claim: who, id: e.pointerId, button: e.button };
    ARMED = null;
    if (who === 'pan') return;
    // A drag that leaves the stage must keep arriving.  The page's pan handler used to
    // capture on every press and the editor rode along on that capture; now that pan
    // yields, the editor has to take it itself or a drag stops halfway to wherever it
    // was going.
    if (svg.setPointerCapture) { try { svg.setPointerCapture(e.pointerId); } catch (err) {} }
  });
  svg.addEventListener('pointermove', function (e) {
    var m = toModel(e.clientX, e.clientY);
    if (SKETCH) { sketchMove(m.x, m.y, { shift: e.shiftKey }); return; }
    if (!DOWN) {
      hover(m.x, m.y, e.clientX, e.clientY);
      // the ghost IS the element, drawn by the stage's own code at stage scale -- a
      // whole component as much as a single site
      if (isStampType(ARMED_EL)) {
        if (!PGHOST) ghostBegin(ARMED_EL, m.x, m.y); else ghostMove(m.x, m.y);
      } else if (PGHOST) ghostCancel();
      return;
    }
    if (DOWN.claim === 'pan') return;   // the page's pan handler owns this drag
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
      if (ARMED === 'node') begin(DOWN.hit.kind, DOWN.hit.id, DOWN.mx, DOWN.my, { alt: DOWN.alt });
      if (ARMED === 'band') bandBegin(DOWN.hit.id);
      if (ARMED === 'marquee') marqueeBegin(DOWN.mx, DOWN.my);
      setCursor(DOWN.hit);
    }
    if (ARMED === 'node') {
      var r = move(m.x, m.y, { free: e.altKey, fine: e.shiftKey });
      showHud(e.clientX, e.clientY, r);
    } else if (ARMED === 'marquee') {
      marqueeMove(m.x, m.y);
    } else if (ARMED === 'band') {
      bandMove(m.x, m.y);
    }
  });
  var end = function (e) {
    var m = toModel(e.clientX, e.clientY);
    if (SKETCH) {
      if (svg.releasePointerCapture) { try { svg.releasePointerCapture(e.pointerId); } catch (err) {} }
      sketchUp(m.x, m.y, { shift: e.shiftKey });
      return;
    }
    var wasArmed = ARMED, down = DOWN;
    DOWN = null; ARMED = null;
    if (down && svg.releasePointerCapture) {
      try { svg.releasePointerCapture(e.pointerId); } catch (err) {}
    }
    hideHud();
    if (wasArmed === 'node') {
      // `drop()` resets the cursor itself, through the layout it just produced (the
      // harness needs that); a second hover here was a second hit test per release
      var r = drop();
      // the declared-length note is on the price strip (`rebuild` derives it); a toast
      // per drop was the noise that hid the refusals
      if (r && r.problems.length) toast('bad', r.problems[0].message);
      return;
    }
    if (wasArmed === 'marquee') {
      marqueeDrop({ additive: e.shiftKey });
      setCursor(hit(m.x, m.y));
      return;
    }
    if (wasArmed === 'band') { bandDrop(m.x, m.y); return; }
    if (!wasArmed && down && !down.button &&
        Math.sqrt((e.clientX - down.cx) * (e.clientX - down.cx) +
                  (e.clientY - down.cy) * (e.clientY - down.cy)) < 4) {
      clickStage(down.mx, down.my, { shift: e.shiftKey });
    }
  };
  svg.addEventListener('pointerup', end);
  svg.addEventListener('pointercancel', end);
  svg.addEventListener('dblclick', function (e) {
    var m = toModel(e.clientX, e.clientY);
    // a double-click FINISHES A POLYLINE, open; it never places a site while a shape tool
    // is armed, because the two single clicks before it were points of the shape
    if (SKETCH === 'poly') { sketchFinish(false); return; }
    if (SKETCH) return;
    dblclickStage(m.x, m.y);
  });
  // the ghost follows the pointer, so it goes when the pointer leaves the stage; the tile
  // stays armed and the next `pointermove` begins a fresh one (the rule `setArmed` uses)
  svg.addEventListener('pointerleave', function () { leaveStage(); });

  // -- keys: NONE HERE.  The page owns the one `keydown` listener and dispatches on
  // `keyGesture` below; a second listener on the same target is how the arrows came to
  // nudge AND scrub at once.
}
var BAND = null, SRCT = null, LASTMOVED = null;
// THE BAND -- shift-drag from a node to a second one makes a segment -- as verbs, like
// begin/move/drop: the pointer adapter calls these and holds no state of its own, so
// the harness can hold a band open and ask what Escape does to it.
function bandBegin(id) {
  if (!nodeById[id]) return null;
  BAND = id;
  return { from: BAND };
}
function bandMove(mx, my) {
  var n = BAND ? nodeById[BAND] : null;
  if (!n) return null;
  EBAND.style.display = '';
  EBAND.setAttribute('x1', px(n)); EBAND.setAttribute('y1', py(n));
  EBAND.setAttribute('x2', mx); EBAND.setAttribute('y2', my);
  return { from: BAND, x: mx, y: my };
}
function bandDrop(mx, my) {
  if (!BAND) return null;
  EBAND.style.display = 'none';
  var from = BAND, target = hit(mx, my), res = null;
  BAND = null;
  if (target && target.kind !== 'segment' && target.kind !== 'loop' && target.id !== from) {
    // `joinNodes` toasts its own refusal
    res = joinNodes(from, target.id);
  } else if (!target) {
    toast('warn', 'a segment joins two nodes -- drop it on a second one');
  }
  return { from: from, to: target ? target.id : null,
           ok: !!(res && res.ok), problems: res ? res.problems : [] };
}
function bandCancel() {
  if (!BAND) return false;
  if (EBAND) EBAND.style.display = 'none';
  BAND = null;
  return true;
}
// a toolbar button keeps focus after a click, and then the space bar re-fires it instead
// of playing; `blur` is feature-detected because the harness's elements have none
function unfocus(el) { if (el && el.blur) el.blur(); }

// THE POINTER LEFT THE STAGE, callable without an Event.  A press in flight keeps its
// ghost (pointer capture brings the pointer back); otherwise the placement preview goes
// and the tile stays armed.  Returns whether the ghost was dropped, so the harness can
// drive the decision the adapter used to make on its own.
function leaveStage() {
  if (DOWN) return false;
  ghostCancel();
  hintHide();
  return true;
}

// Arrow-key nudge: ONE gesture.  Every member's `move_site` is built from its pre-move
// position, validated as a set (a member may land where another member was), stamped
// with one fresh group id -- the constant `'nudge'` used to glue every nudge ever made
// into a single undo step -- and committed with ONE rebuild rather than one per node.
// A refusal is returned AND toasted: `emit()`'s verdict used to be dropped on the floor,
// so nudging two adjacent sites into each other did nothing and said nothing.
function nudge(key, mult) {
  var dx = key === 'ArrowLeft' ? -1 : key === 'ArrowRight' ? 1 : 0;
  var dy = key === 'ArrowUp' ? -1 : key === 'ArrowDown' ? 1 : 0;
  var stepx = (L.ux || 1) * (mult || 1), stepy = (L.uy || L.ux || 1) * (mult || 1);
  var ids = selectionNodes(), ops = [], problems = [], i, j;
  if (!ids.length) return { ok: false, ops: [], problems: [{ code: 'no_selection', message: 'nothing is selected to nudge' }] };
  var group = 'g' + (++GROUP), member = {};
  for (i = 0; i < ids.length; i++) member[ids[i]] = 1;
  for (i = 0; i < ids.length; i++) {
    var n = nodeById[ids[i]];
    ops.push({ method: 'move_site',
               args: [n.id, Q.pyFloat(n.x + dx * stepx), Q.pyFloat(n.y + dy * stepy)],
               kwargs: {}, meta: { group: group, src: 'keys' } });
  }
  for (i = 0; i < ops.length; i++) {
    var ps = validate(ops[i]);
    for (j = 0; j < ps.length; j++) {
      // the set moves together: a member sitting where another member WAS is not a
      // coincidence, and the other member's own op says where it is going
      if (ps[j].code === 'coincident' && member[ps[j].targets[1]]) continue;
      problems.push(ps[j]);
    }
  }
  if (problems.length) { toast('bad', problems[0].message); return { ok: false, ops: ops, problems: problems }; }
  // COMMIT AS ONE, THEN CHECK THE APPLIER -- the same take-it-back-out rule `emit` uses,
  // over the whole set, so a refused nudge leaves no half of itself in the stack.
  var at = EDITS.length;
  for (i = 0; i < ops.length; i++) EDITS.push(ops[i]);
  forgetRedo();
  rebuild();
  var mine = PROBLEMS.filter(function (p) { return p.i >= at; });
  if (mine.length) {
    EDITS.length = at;
    rebuild();
    toast('bad', mine[0].message);
    return { ok: false, ops: ops, problems: mine };
  }
  LASTMOVED = ids[0];
  return { ok: true, ops: ops, problems: [] };
}
// `L`: the last moved node's segment lengths are set to what the drawing shows
function reconcileLast() {
  if (!LASTMOVED) return { ok: false, n: 0 };
  var n = reconcileLengths(LASTMOVED);
  toast('ok', n + ' segment length(s) set to match the drawing');
  return { ok: true, n: n };
}

// ------------------------------------------------------- the ruler, and the true scale
//
// WHAT THIS EXISTS FOR.  A reviewer asked that anyone be able to measure the distance
// between any two points on this picture, in micrometres, and the angle between any two
// rails.  Neither was possible before: node coordinates are LATTICE UNITS, which are not
// a length, and the fit is free to stretch one axis by up to `K_ANISO`, which makes every
// angle on the screen a different angle from the one on the die.
//
// So there are two things here, and they are separate on purpose.
//
//   * TRUE SCALE fixes the drawing: `sx:sy` is forced to the technology's nm-per-unit
//     ratio, so one pixel is the same number of nanometres on both axes.  On by default,
//     remembered like Snap.  With it off the ruler still reads correctly -- every number
//     below is computed from LATTICE positions through the technology, never off the
//     screen -- but the picture no longer agrees with the numbers, so the scale bar says
//     which axis it is for and the ruler still tells the truth.
//   * THE RULER measures.  Click two points for a distance, a third for the angle at the
//     middle one, or two points on two different rails for the angle between those rails.
//
// EVERY NUMBER IS PHYSICAL, AND NONE IS READ OFF THE SCREEN.  `physVec` maps a pixel
// displacement back through `sx`/`sy` into lattice units and then through the technology
// into nanometres, so a measurement taken on a stretched drawing is the same measurement
// as one taken on a true-scale drawing.  That is the whole reason the ruler is worth
// having: a protractor held against a distorted picture measures the distortion.
var MEASURE = false, MPTS = [], gMeas = null;
var MDOT = [], MLINE = [], MTXT = [], MARC = null;

// A pixel displacement as a physical one, in NANOMETRES, per axis.
function physVec(dxpx, dypx) {
  return { x: dxpx / (L.sx || 1) * NM_X, y: dypx / (L.sy || 1) * NM_Y };
}
// The direction of one rail, from its two NODES in physical units -- not from the two
// points the user happened to click on it, and not from the pixels it is drawn in.  This
// is what makes "the angle between two rails" correct on an anisotropic drawing.
function railDirection(sid) {
  var sg = segById[sid];
  if (!sg) return null;
  var a = nodeById[sg.a], b = nodeById[sg.b];
  if (!a || !b) return null;
  return { x: (b.x - a.x) * NM_X, y: (b.y - a.y) * NM_Y };
}
// Degrees between two vectors.  `smaller` folds 170 degrees to 10: two RAILS cross at one
// angle and its supplement, and the angle a reader means is the acute one.  A three-point
// angle is not folded -- there the order of the clicks says which of the two is meant.
function angleBetween(u, v, smaller) {
  if (!u || !v) return null;
  var lu = Math.sqrt(u.x * u.x + u.y * u.y), lv = Math.sqrt(v.x * v.x + v.y * v.y);
  if (lu < 1e-12 || lv < 1e-12) return null;
  var c = (u.x * v.x + u.y * v.y) / (lu * lv);
  if (c > 1) c = 1; else if (c < -1) c = -1;
  var deg = Math.acos(c) * 180 / Math.PI;
  return smaller ? Math.min(deg, 180 - deg) : deg;
}
// The nearest point ON a drawn segment, curve included: a bowed rail is not its chord,
// and snapping to the chord would put the ruler's end in the middle of nothing.
function nearestOnSegment(I, mx, my) {
  if (!I.cp) {
    var l2 = I.dx * I.dx + I.dy * I.dy;
    var t = l2 > 1e-12 ? ((mx - I.ax) * I.dx + (my - I.ay) * I.dy) / l2 : 0;
    t = Math.max(0, Math.min(1, t));
    var x = I.ax + I.dx * t, y = I.ay + I.dy * t;
    return { x: x, y: y, t: t, d: Math.sqrt((x - mx) * (x - mx) + (y - my) * (y - my)) };
  }
  var best = null;
  for (var i = 0; i <= 24; i++) {
    var q = bezPoint(I, i / 24);
    var d = Math.sqrt((q.x - mx) * (q.x - mx) + (q.y - my) * (q.y - my));
    if (!best || d < best.d) best = { x: q.x, y: q.y, t: i / 24, d: d };
  }
  return best;
}
// SNAP TO SOMETHING REAL FIRST.  A ruler whose ends land wherever the pointer was is a
// ruler that measures the hand: a node centre wins, then the nearest rail, and only past
// both does the click stand where it fell (and says so, so the reading is not mistaken
// for a measurement between two parts).
function measureSnap(mx, my) {
  var R = Math.max(2 * slop(), 8 * userPerPx()), best = null, i;
  for (i = 0; i < A.nodes.length; i++) {
    var n = A.nodes[i], dx = px(n) - mx, dy = py(n) - my;
    var d = Math.sqrt(dx * dx + dy * dy);
    if (d <= R && (!best || d < best.d)) best = { x: px(n), y: py(n), d: d, kind: 'node', id: n.id };
  }
  if (best) return best;
  for (i = 0; i < A.segments.length; i++) {
    var sg = A.segments[i], I = SEGINFO[sg.id];
    if (!I || I.len < 1e-6) continue;
    var q = nearestOnSegment(I, mx, my);
    if (q.d <= R && (!best || q.d < best.d)) best = { x: q.x, y: q.y, d: q.d, kind: 'rail', id: sg.id };
  }
  return best || { x: mx, y: my, d: 0, kind: 'free', id: null };
}

// THE OVERLAY IS ITS OWN GROUP, and nothing else writes into it.  `clearTransients()` --
// the page's single undo point for the DC ramp, the over-capacity restroke and the
// in-play site marks -- runs on every frame; a ruler drawn into any of those groups would
// be wiped by the next animation tick, which is exactly the failure that makes people
// stop trusting a measurement tool.  Pooled and hidden, never removed: `remove()` is a
// no-op in tests/shim.mjs.
function ensureMeasure() {
  if (gMeas) return;
  gMeas = el('g', { 'pointer-events': 'none' });
  svg.append(gMeas);
  var i;
  for (i = 0; i < 3; i++) {
    var d = el('circle', { r: 3.4, fill: 'none', stroke: C.accent, 'stroke-width': 1.6 });
    d.style.display = 'none'; gMeas.append(d); MDOT.push(d);
  }
  for (i = 0; i < 2; i++) {
    var ln = el('line', { stroke: C.accent, 'stroke-width': 1.6, 'stroke-dasharray': '6 3' });
    ln.style.display = 'none'; gMeas.append(ln); MLINE.push(ln);
  }
  MARC = el('path', { fill: 'none', stroke: C.gold, 'stroke-width': 1.8 });
  MARC.style.display = 'none'; gMeas.append(MARC);
  for (i = 0; i < 2; i++) {
    var t = el('text', { 'font-size': 11, 'font-weight': 650, fill: C.ink });
    t.style.display = 'none'; gMeas.append(t); MTXT.push(t);
  }
}

// THE NUMBERS, as numbers.  Published so a harness can assert what the page claims a
// distance is, rather than scraping a label -- and so the labels below have exactly one
// source.
function measureReadout() {
  var out = { on: MEASURE, n: MPTS.length, points: [], distance_um: null, lattice: null,
              dx_um: null, dy_um: null, angle_deg: null, rail_angle_deg: null,
              rails: [], preset: TECH.preset, true_scale: !!TRUE_SCALE };
  var i;
  for (i = 0; i < MPTS.length; i++) {
    out.points.push({ x: MPTS[i].x, y: MPTS[i].y, kind: MPTS[i].kind, id: MPTS[i].id });
    if (MPTS[i].kind === 'rail') out.rails.push(MPTS[i].id);
  }
  if (MPTS.length >= 2) {
    var v = physVec(MPTS[1].x - MPTS[0].x, MPTS[1].y - MPTS[0].y);
    out.dx_um = v.x / 1000; out.dy_um = v.y / 1000;
    out.distance_um = Math.sqrt(v.x * v.x + v.y * v.y) / 1000;
    var lx = (MPTS[1].x - MPTS[0].x) / (L.sx || 1), ly = (MPTS[1].y - MPTS[0].y) / (L.sy || 1);
    out.lattice = Math.sqrt(lx * lx + ly * ly);
    if (MPTS[0].kind === 'rail' && MPTS[1].kind === 'rail' && MPTS[0].id !== MPTS[1].id) {
      out.rail_angle_deg = angleBetween(railDirection(MPTS[0].id), railDirection(MPTS[1].id), true);
    }
  }
  if (MPTS.length >= 3) {
    out.angle_deg = angleBetween(physVec(MPTS[0].x - MPTS[1].x, MPTS[0].y - MPTS[1].y),
                                 physVec(MPTS[2].x - MPTS[1].x, MPTS[2].y - MPTS[1].y), false);
  }
  return out;
}

function measureRedraw() {
  if (!gMeas) { if (!MEASURE) return; ensureMeasure(); }
  var r = measureReadout(), i;
  // THE RULER IS FURNITURE, SIZED IN SCREEN PIXELS.  Everything below is written in the
  // pixels a reader sees and converted once: a mark sized in user units grows with the
  // zoom, and a ruler whose tick marks swell as you look closer is a ruler that argues
  // with the thing it is measuring.
  var u = userPerPx(), sw = 1.6 * u;
  for (i = 0; i < MDOT.length; i++) {
    if (i < MPTS.length) {
      MDOT[i].setAttribute('cx', MPTS[i].x); MDOT[i].setAttribute('cy', MPTS[i].y);
      MDOT[i].setAttribute('r', 4 * u);
      MDOT[i].setAttribute('stroke-width', sw);
      // a point that snapped to nothing is drawn hollow-red, so a reading taken in empty
      // space cannot be mistaken for one taken between two parts
      MDOT[i].setAttribute('stroke', MPTS[i].kind === 'free' ? C.z : C.accent);
      MDOT[i].style.display = '';
    } else MDOT[i].style.display = 'none';
  }
  for (i = 0; i < MLINE.length; i++) {
    if (i + 1 < MPTS.length) {
      MLINE[i].setAttribute('x1', MPTS[i].x); MLINE[i].setAttribute('y1', MPTS[i].y);
      MLINE[i].setAttribute('x2', MPTS[i + 1].x); MLINE[i].setAttribute('y2', MPTS[i + 1].y);
      MLINE[i].setAttribute('stroke-width', sw);
      MLINE[i].setAttribute('stroke-dasharray', (6 * u) + ' ' + (3 * u));
      MLINE[i].style.display = '';
    } else MLINE[i].style.display = 'none';
  }
  for (i = 0; i < MTXT.length; i++) MTXT[i].style.display = 'none';
  MARC.style.display = 'none';
  if (MPTS.length >= 2 && r.distance_um !== null) {
    var t0 = MTXT[0], mx = (MPTS[0].x + MPTS[1].x) / 2, my = (MPTS[0].y + MPTS[1].y) / 2;
    t0.setAttribute('x', mx + 6 * u); t0.setAttribute('y', my - 6 * u);
    t0.setAttribute('font-size', 12 * u);
    t0.textContent = fmtUm(r.distance_um) + '  ·  ' + (+r.lattice.toFixed(3)) + ' u  ·  d(' +
                     um1(r.dx_um) + ', ' + um1(r.dy_um) + ') um' +
                     (r.rail_angle_deg !== null
                        ? '  ·  rails ' + r.rail_angle_deg.toFixed(2) + '\u00b0' : '');
    t0.style.display = '';
  }
  if (MPTS.length >= 3 && r.angle_deg !== null) {
    var b = MPTS[1], a0 = Math.atan2(MPTS[0].y - b.y, MPTS[0].x - b.x);
    var a2 = Math.atan2(MPTS[2].y - b.y, MPTS[2].x - b.x);
    var dA = a2 - a0;
    while (dA > Math.PI) dA -= 2 * Math.PI;
    while (dA < -Math.PI) dA += 2 * Math.PI;
    var rr = Math.min(0.5 * L.g, 40 * u), pts = [];
    for (i = 0; i <= 16; i++) {
      var aa = a0 + dA * (i / 16);
      pts.push((i ? 'L ' : 'M ') + (b.x + rr * Math.cos(aa)) + ' ' + (b.y + rr * Math.sin(aa)));
    }
    MARC.setAttribute('d', pts.join(' '));
    MARC.setAttribute('stroke-width', 1.8 * u);
    MARC.style.display = '';
    var t1 = MTXT[1], am = a0 + dA / 2;
    t1.setAttribute('x', b.x + (rr + 6 * u) * Math.cos(am));
    t1.setAttribute('y', b.y + (rr + 6 * u) * Math.sin(am));
    t1.setAttribute('font-size', 12 * u);
    t1.setAttribute('fill', C.gold);
    t1.textContent = r.angle_deg.toFixed(2) + '\u00b0';
    t1.style.display = '';
  }
  return r;
}

// ONE CLICK, ONE POINT.  Two points are a distance, three are an angle at the middle one,
// and a fourth starts over -- so the tool never accumulates a reading nobody asked for.
function measureClick(mx, my) {
  if (!MEASURE) return null;
  ensureMeasure();
  if (MPTS.length >= 3) MPTS = [];
  MPTS.push(measureSnap(mx, my));
  measureRedraw();
  return measureReadout();
}
function measureClear() { MPTS = []; measureRedraw(); return measureReadout(); }
function measureToggle(on) {
  MEASURE = (on === undefined) ? !MEASURE : !!on;
  MPTS = [];
  ensureMeasure();
  measureRedraw();
  setCursor(null);
  paint();
  return MEASURE;
}

// ------------------------------------------------------------------- the true scale
// The toggle, and the one place the page re-lays-out without an edit behind it.  `L` is
// MUTATED, never replaced: every closure in the page script and in this file captured
// this object.  With no editable state (a page whose listing does not replay) there is
// nothing to recompute from, so the pair Python shipped is used instead -- which is why
// `build_view_model` emits both layouts rather than one.
//
// NOT `relayout`: render.py owns that name for the window-resize path, and these scripts
// share one scope -- a second `function relayout()` here would silently replace it.
// THE VIEW IS HELD ACROSS AN EDIT.  Re-fitting on every edit moved the picture under
// the pointer: place four sites along one row and they come out as a staircase, because
// each placement re-scaled the drawing and the next click meant another model point
// (reported 2026-09-17).  So an edit passes the scale and origin it was drawn at to
// `computeLayout` and nothing on the screen moves; a REPLACEMENT -- Fit, a new device
// from a card or a file, the true-scale toggle -- calls `refitNext()` first and the
// drawing is fitted afresh.  One flag, cleared by the rebuild that honours it.
var REFIT_NEXT = false;
function refitNext() { REFIT_NEXT = true; }
function holdOpts() {
  return layoutOpts(REFIT_NEXT ? null : [L.sx, L.sy, L.ox, L.oy]);
}
function refit() { refitNext(); rescale(); }
function rescale() {
  var lay = STATE ? Q.computeLayout(nodesOf(STATE), segsOf(STATE), holdOpts())
                  : ((TRUE_SCALE && D.layout_true) ? D.layout_true : D.layout);
  for (var k in lay) if (has(lay, k)) L[k] = lay[k];
  if (!EDITS.length) BOUND = boundaryFrom(L);
  GRID = null;
  rebuildStatic();
  REFIT_NEXT = false;
  if (typeof sizeStage === 'function') sizeStage();
  if (typeof draw === 'function') draw();
}
function setTrueScale(on) {
  TRUE_SCALE = !!on;
  try { STORE.setItem(TS_KEY, TRUE_SCALE ? '1' : '0'); } catch (err) { /* no store */ }
  refitNext();                       // another scale rule is another drawing: fit it
  rescale();
  paint();
  return TRUE_SCALE;
}


// =====================================================================================
// THE SKETCH: the shape first, the parts after
// =====================================================================================
//
// WHY THIS EXISTS.  Every device in `qccd/arch/generators.py` is a SHAPE first -- a ring,
// a grid, a chain -- and the studio made you build one site at a time.  Asked for
// (2026-09-17): "it would be much more direct if the design can start from a Sketch of
// the shuttling shape, before the details are filled in".  So there are two modes.
// SKETCH is the default on a canvas with no device on it: arm a shape, drag it, and the
// release lays trapping sites along what was drawn, ONE LATTICE UNIT apart -- the spacing
// every generator uses -- declares the orbit if the shape closed, and puts a JUNCTION
// wherever the new rail meets or crosses one that is already there.  PARTS is the tool as
// it was: every gesture it had still works, untouched, and the course and the detail work
// live there.
//
// WHAT A CORNER IS.  A corner of a drawn shape is a degree-2 node, and R18 prices a
// degree-2 node as ordinary transport: a BEND, not a junction.  The shipped ring has four
// of them and pays nothing for any of them.  So a corner gets a SITE and the readout says
// so -- declaring a degree-2 `junction` would contradict R18 and the verifier would price
// the whole device wrong.  A junction is minted only where three or four rails actually
// meet, which is the "a Junction should be automatically added" the request asked for.
//
// WHERE IT COMMITS.  One `transaction`, so one `meta.group` and one Ctrl+Z, and the
// records go through `cmpInstantiate` -- the component palette's own instantiator -- so
// every id is namespaced and every part carries the `cmp:<inst>` label that makes
// `instanceMembers` / `instanceAt` treat the finished sketch as ONE thing to select, drag
// or delete.
//
// A SKETCH IS AN EDIT, so it HOLDS THE VIEW: nothing here calls `refitNext()`, and the
// shape stays exactly where it was drawn.

var MODE_KEY = 'qccd.studio.mode';
// `null` means "not chosen": Sketch on a canvas with nothing on it, Parts once there is a
// device to work on.  A stored choice, or a press of the segmented control, pins it.
var DMODE = null;
var SK_TOOLS = { rect: 'Rectangle', ellipse: 'Ellipse', line: 'Line', poly: 'Polyline' };
var SK_ORDER = ['rect', 'ellipse', 'line', 'poly'];
function designMode() {
  if (DMODE === 'sketch' || DMODE === 'parts') return DMODE;
  return (STATE && nodesOf(STATE).length) ? 'parts' : 'sketch';
}
function setDesignMode(m) {
  m = (String(m) === 'parts') ? 'parts' : 'sketch';
  DMODE = m;
  try { STORE.setItem(MODE_KEY, m); } catch (err) { /* no store */ }
  if (m === 'parts') { SKETCH = null; sketchClear(); } else arm(null);
  setCursor(HOVERED);
  paint();
  return m;
}

// ---- the armed tool -------------------------------------------------------------------
var SKETCH = null;            // 'rect' | 'ellipse' | 'line' | 'poly' | null
var SKPTS = [];               // the polyline's committed vertices, in DEVICE units
var SKDRAG = null;            // { a, b, shift } while a drag is live
var SKHOVER = null;           // where the pointer is, in DEVICE units (the poly's band)
var SK_N = 0;
function sketchOn() { return SKETCH; }
function sketchTool(t) {
  t = (t && has(SK_TOOLS, String(t))) ? String(t) : null;
  if (VIEW_ONLY) t = null;
  var next = (SKETCH === t) ? null : t;
  SKETCH = next;
  SKPTS = []; SKDRAG = null; SKHOVER = null;
  if (next) {
    if (designMode() !== 'sketch') setDesignMode('sketch');
    arm(null);                                  // one stage tool at a time
    if (MEASURE) measureToggle(false);
  }
  ensureSketch();
  sketchRedraw();
  setCursor(null);
  paint();
  return SKETCH;
}

// ---- units ----------------------------------------------------------------------------
// ONE LATTICE UNIT is what every generator spaces its sites by, and `L.ux` / `L.uy` is what
// the layout measured it to be.  On an empty canvas there is nothing to measure, so it is
// 0 and the fallback is 1 -- exactly `snapTo`'s rule, not a second one.
//
// A PITCH SMALLER THAN THE TWO CLOSEST SITES IS NOT A PITCH.  `ux` / `uy` are the smallest
// gap between distinct coordinates, which is the pitch on an axis-aligned lattice and
// rounding noise on a slanted one: a triangle's two sloped sides put sites at heights a
// thousandth apart, and every shape drawn after it laid its sites 0.001 units apart and was
// refused (2026-09-24, a dock spur off a triangle).  No two sites are closer than `L.gd`,
// so neither is the unit.
function skUnit() {
  var ux = L.ux || 1, uy = L.uy || L.ux || 1, gd = L.gd || 0;
  if (gd > 0) { if (ux < gd) ux = gd; if (uy < gd) uy = gd; }
  return { x: ux, y: uy };
}
function skToU(mx, my) { return { x: (mx - L.ox) / (L.sx || 1), y: (my - L.oy) / (L.sy || 1) }; }
function skToPx(x, y) { return { x: L.ox + x * (L.sx || 1), y: L.oy + y * (L.sy || 1) }; }
function sk3(v) { var x = Math.round(v * 1000) / 1000; return x === 0 ? 0 : x; }
function skHyp(x, y) { return Math.sqrt(x * x + y * y); }

// ---- the shape's own vertices ---------------------------------------------------------
function skVerts(tool, a, b, mod) {
  mod = mod || {};
  var lim = Q.geometryLimits(STATE), i;
  if (tool === 'line') return { verts: [[sk3(a.x), sk3(a.y)], [sk3(b.x), sk3(b.y)]], closed: false };
  if (tool === 'rect') {
    return { verts: [[sk3(a.x), sk3(a.y)], [sk3(b.x), sk3(a.y)],
                     [sk3(b.x), sk3(b.y)], [sk3(a.x), sk3(b.y)]], closed: true };
  }
  if (tool === 'ellipse') {
    var dx = b.x - a.x, dy = b.y - a.y;
    // shift constrains to a CIRCLE: the longer of the two drags drives both axes and the
    // corner the drag started from stays put, which is what a drawing tool does.
    if (mod.shift) {
      var mm = Math.max(Math.abs(dx), Math.abs(dy));
      dx = (dx < 0 ? -mm : mm); dy = (dy < 0 ? -mm : mm);
    }
    var rx = Math.abs(dx) / 2, ry = Math.abs(dy) / 2;
    var cx = a.x + dx / 2, cy = a.y + dy / 2;
    if (rx < 1e-9 || ry < 1e-9) return { verts: [], closed: true };
    var U = skUnit(), step = (U.x + U.y) / 2;
    // Ramanujan's circumference, then ONE SIDE PER LATTICE UNIT -- the same spacing the
    // straight edges get, so a circle and a rectangle of one perimeter hold one number of
    // ions.
    var C2 = Math.PI * (3 * (rx + ry) - Math.sqrt((3 * rx + ry) * (rx + 3 * ry)));
    var n = Math.max(3, Math.round(C2 / Math.max(1e-9, step)));
    // R20 BY CONSTRUCTION: a regular n-gon's two rails meet at 180 - 360/n degrees, so a
    // circle too small to carry enough sides cannot satisfy this device's minimum, and is
    // refused with the angle it would have made rather than drawn and refused later.
    var ang = 180 - 360 / n;
    if (ang < lim.minAng - 1e-9) {
      return { verts: [], closed: true, rule: 'R20',
               why: 'a circle this small polygonises to ' + n + ' sides, whose rails meet ' +
                    'at ' + ang.toFixed(1) + ' degrees -- less than the ' + lim.minAng +
                    ' this device requires (budget.min_rail_angle_deg): drag a bigger one' };
    }
    var ph = Math.atan2((a.y - cy) / ry, (a.x - cx) / rx);
    var verts = [];
    for (i = 0; i < n; i++) {
      var th = ph + 2 * Math.PI * i / n;
      verts.push([sk3(cx + rx * Math.cos(th)), sk3(cy + ry * Math.sin(th))]);
    }
    return { verts: verts, closed: true };
  }
  if (tool === 'poly') {
    var pts = (a && a.pts) ? a.pts : [];
    return { verts: pts.map(function (p) { return [sk3(p[0]), sk3(p[1])]; }),
             closed: !!(a && a.closed) };
  }
  return { verts: [], closed: false, why: 'no shape tool is armed' };
}

// ---- the sites the shape lays down ----------------------------------------------------
//
// ONE LATTICE UNIT APART, the first site at the shape's start, every CORNER a site.  The
// last spacing on an edge may be short: what the user drew is the authority, so the shape
// is never silently resized to make the arithmetic come out even.
function skSites(verts, closed) {
  var U = skUnit(), out = [], i, j;
  var m = verts.length;
  if (m < 2) return out;
  var last = closed ? m : m - 1;
  var push = function (x, y) {
    var p = [sk3(x), sk3(y)], k = out.length - 1;
    if (k >= 0 && skHyp(out[k][0] - p[0], out[k][1] - p[1]) < 1e-9) return;
    out.push(p);
  };
  for (i = 0; i < last; i++) {
    var p0 = verts[i], p1 = verts[(i + 1) % m];
    var dx = p1[0] - p0[0], dy = p1[1] - p0[1], len = skHyp(dx, dy);
    if (len < 1e-9) continue;
    var ux = dx / len, uy = dy / len;
    var step = skHyp(ux * U.x, uy * U.y);
    if (!(step > 1e-9)) step = 1;
    for (j = 0; ; j++) {
      var t = j * step;
      if (t > len - 0.5 * step + 1e-9) break;
      push(p0[0] + ux * t, p0[1] + uy * t);
    }
  }
  if (!closed) push(verts[m - 1][0], verts[m - 1][1]);
  if (closed && out.length > 1 &&
      skHyp(out[0][0] - out[out.length - 1][0], out[0][1] - out[out.length - 1][1]) < 1e-9) {
    out.pop();
  }
  return out;
}

// ---- the graph the shape meets --------------------------------------------------------
//
// The engine's own crossing predicates, in the one place a sketch needs them BEFORE the
// commit.  `geometryViolations` (engine.js, not edited here) is what JUDGES the committed
// document against R19 / R20 / R21; these are the same tests run while the shape is still
// under the pointer, so the refusal arrives in the readout rather than as a lint
// afterwards.
var SK_EPS = 1e-6;
function skOrient(p, q, r) {
  return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
}
function skCross(a, b, c, d) {
  var o1 = skOrient(a, b, c), o2 = skOrient(a, b, d);
  var o3 = skOrient(c, d, a), o4 = skOrient(c, d, b);
  return ((o1 > SK_EPS && o2 < -SK_EPS) || (o1 < -SK_EPS && o2 > SK_EPS)) &&
         ((o3 > SK_EPS && o4 < -SK_EPS) || (o3 < -SK_EPS && o4 > SK_EPS));
}
function skCrossPoint(a, b, c, d) {
  var r = [b[0] - a[0], b[1] - a[1]], s = [d[0] - c[0], d[1] - c[1]];
  var den = r[0] * s[1] - r[1] * s[0];
  if (Math.abs(den) < 1e-12) return null;
  var t = ((c[0] - a[0]) * s[1] - (c[1] - a[1]) * s[0]) / den;
  return { t: t, p: [sk3(a[0] + t * r[0]), sk3(a[1] + t * r[1])] };
}
function skOnSegment(p, a, b) {
  var abx = b[0] - a[0], aby = b[1] - a[1];
  var apx = p[0] - a[0], apy = p[1] - a[1];
  var l2 = abx * abx + aby * aby;
  if (l2 === 0) return null;
  var cr = abx * apy - aby * apx;
  if (Math.abs(cr) > SK_EPS * Math.sqrt(l2)) return null;
  var t = (apx * abx + apy * aby) / l2;
  return (SK_EPS < t && t < 1 - SK_EPS) ? t : null;
}
function skDevSegs() {
  var out = [], sid, dev = STATE ? STATE.device : null;
  if (!dev) return out;
  for (sid in dev.segments) if (has(dev.segments, sid)) {
    var s = dev.segments[sid], a = dev.nodes[s.a], b = dev.nodes[s.b];
    if (!a || !b) continue;
    out.push({ id: sid, a: s.a, b: s.b, loop: s.loop === undefined ? null : s.loop,
               cap: s.cap || 1,
               pa: [+Q.unbox(a.pos[0]), +Q.unbox(a.pos[1])],
               pb: [+Q.unbox(b.pos[0]), +Q.unbox(b.pos[1])] });
  }
  out.sort(function (x, y) { return x.id < y.id ? -1 : x.id > y.id ? 1 : 0; });
  return out;
}
function skDegree(nid) {
  var dev = STATE ? STATE.device : null, d = 0, sid;
  if (!dev) return 0;
  for (sid in dev.segments) if (has(dev.segments, sid)) {
    var s = dev.segments[sid];
    if (s.a === nid) d++;
    if (s.b === nid) d++;
  }
  return d;
}
// Is this rail an edge of some declared orbit?  `remove_segment` refuses to take a loop
// edge away, so a rail that carries an orbit has to be split the other way -- see the
// note in `sketchPlan`.
function skOnLoop(sg) {
  var dev = STATE ? STATE.device : null, lid;
  if (!dev) return null;
  if (sg.loop) return sg.loop;
  for (lid in dev.loops) if (has(dev.loops, lid)) {
    var ns = dev.loops[lid].nodes, k = ns.length, i;
    for (i = 0; i + 1 < k; i++) {
      if ((ns[i] === sg.a && ns[i + 1] === sg.b) ||
          (ns[i] === sg.b && ns[i + 1] === sg.a)) return lid;
    }
    if (dev.loops[lid].closed && k > 1 &&
        ((ns[k - 1] === sg.a && ns[0] === sg.b) ||
         (ns[k - 1] === sg.b && ns[0] === sg.a))) return lid;
  }
  return null;
}

// WEAVING THE NEW RAIL INTO THE OLD ONE.  Three things can happen where a drawn shape
// meets a device that is already there, and R21 says all three must end with a SHARED
// NODE:
//
//   * a generated site lands on an existing node  ->  that node is REUSED.  Its zone and
//     capacity are kept: R18 already makes a degree-3 node a junction by DEGREE, and
//     re-declaring it `kind: junction` would throw away a trap the user built.
//   * a generated site, or an edge of the shape, lands on an existing RAIL  ->  a JUNCTION
//     is minted there and the crossed rail is split in two.
//   * an existing node lies on an edge of the shape  ->  the edge is split at it.
//
// The answer is the path, in order, each point either new, reused, or a split.
function skWeave(sites, closed) {
  var dev = STATE ? STATE.device : null;
  var pts = [], i, j, k;
  if (!dev) {
    for (i = 0; i < sites.length; i++) {
      pts.push({ x: sites[i][0], y: sites[i][1], node: null, split: null });
    }
    return { pts: pts };
  }
  var segs = skDevSegs();
  for (i = 0; i < sites.length; i++) {
    var x = sites[i][0], y = sites[i][1], p = { x: x, y: y, node: null, split: null };
    var coin = coincidentAt(x, y);
    if (coin) p.node = coin.targets[0];
    else {
      for (j = 0; j < segs.length; j++) {
        if (skOnSegment([x, y], segs[j].pa, segs[j].pb) !== null) { p.split = segs[j]; break; }
      }
    }
    pts.push(p);
  }
  var out = [], m = pts.length, last = closed ? m : m - 1;
  var nodes = nodesOf(STATE);
  for (i = 0; i < last; i++) {
    var A0 = pts[i], B0 = pts[(i + 1) % m];
    var a = [A0.x, A0.y], b = [B0.x, B0.y], ev = [];
    for (k = 0; k < nodes.length; k++) {
      if (nodes[k].id === A0.node || nodes[k].id === B0.node) continue;
      var tn = skOnSegment([nodes[k].x, nodes[k].y], a, b);
      if (tn !== null) ev.push({ t: tn, x: nodes[k].x, y: nodes[k].y, node: nodes[k].id, split: null });
    }
    for (j = 0; j < segs.length; j++) {
      var sg = segs[j];
      if (sg.a === A0.node || sg.b === A0.node || sg.a === B0.node || sg.b === B0.node) continue;
      if (A0.split && A0.split.id === sg.id) continue;
      if (B0.split && B0.split.id === sg.id) continue;
      if (!skCross(a, b, sg.pa, sg.pb)) continue;
      var xp = skCrossPoint(a, b, sg.pa, sg.pb);
      if (!xp) continue;
      ev.push({ t: xp.t, x: xp.p[0], y: xp.p[1], node: null, split: sg });
    }
    ev.sort(function (p1, p2) { return p1.t - p2.t; });
    out.push(A0);
    for (k = 0; k < ev.length; k++) {
      out.push({ x: ev[k].x, y: ev[k].y, node: ev[k].node, split: ev[k].split });
    }
  }
  if (!closed) out.push(pts[m - 1]);
  // A JUNCTION MAY BE MINTED ONLY ONCE PER CROSSED RAIL: two events on one rail would be
  // two nodes on one segment, and the second `on=` would name a segment that is gone.
  var seen = {}, clean = [];
  for (i = 0; i < out.length; i++) {
    var q = out[i];
    if (q.split) {
      if (has(seen, q.split.id)) q = { x: q.x, y: q.y, node: null, split: null };
      else seen[q.split.id] = 1;
    }
    clean.push(q);
  }
  return { pts: clean };
}

// ---- the whole plan, judged before anything is committed ------------------------------
function skAngleAt(prev, at, next) {
  var ax = prev[0] - at[0], ay = prev[1] - at[1];
  var bx = next[0] - at[0], by = next[1] - at[1];
  var la = skHyp(ax, ay), lb = skHyp(bx, by);
  if (la < 1e-12 || lb < 1e-12) return 180;
  var c = (ax * bx + ay * by) / (la * lb);
  c = c > 1 ? 1 : c < -1 ? -1 : c;
  return Math.acos(c) * 180 / Math.PI;
}

function sketchPlan(tool, a, b, mod) {
  tool = tool || SKETCH;
  mod = mod || {};
  var lim = Q.geometryLimits(STATE), i;
  var bad = function (why, rule) {
    return { ok: false, why: why, rule: rule || null, tool: tool, verts: [], sites: [],
             pts: [], closed: false, n_sites: 0, n_junctions: 0, n_shared: 0, bends: 0,
             w_um: 0, h_um: 0, loop: false };
  };
  if (!tool || !has(SK_TOOLS, tool)) return bad('no shape tool is armed');
  if (!STATE) return bad('there is no device to draw on');
  var g = skVerts(tool, a, b, mod);
  if (g.why) return bad(g.why, g.rule);
  var verts = g.verts, closed = !!g.closed;
  if (verts.length < 2) return bad('drag further: a shape needs two ends');
  var sites = skSites(verts, closed);
  if (sites.length < 2) {
    return bad('that is shorter than one lattice unit: a rail needs at least two trapping sites');
  }
  // R20 ON THE SHAPE'S OWN CORNERS, before the device is asked anything.  A corner tighter
  // than the minimum is refused with the angle it would have made.
  var n = sites.length, first = closed ? 0 : 1, lastI = closed ? n : n - 1;
  var worst = null, bends = 0;
  for (i = first; i < lastI; i++) {
    var ang = skAngleAt(sites[(i - 1 + n) % n], sites[i], sites[(i + 1) % n]);
    if (worst === null || ang < worst) worst = ang;
    if (Math.abs(ang - 180) > 1e-6) bends++;
    if (ang < lim.minAng - 1e-9) {
      return bad('a corner of ' + ang.toFixed(1) + ' degrees: two rails meeting at one ' +
                 'node must subtend at least ' + lim.minAng + ' degrees on this device ' +
                 '(budget.min_rail_angle_deg)', 'R20');
    }
  }
  // A SHAPE WHOSE SITES WOULD SIT ON TOP OF EACH OTHER IS NOT A DEVICE.  Along one edge
  // the spacing is a lattice unit by construction, but a polygonised circle's SIDE can be
  // shorter than one -- and two traps half a unit apart make `min_nearest_neighbour`
  // shrink every mark on the stage, which is the same reason `coincidentAt` exists.
  var U0 = skUnit(), floorStep = 0.5 * (U0.x + U0.y) / 2, gap = null;
  for (i = 0; i < (closed ? n : n - 1); i++) {
    var q0 = sites[i], q1 = sites[(i + 1) % n];
    var dd = skHyp(q1[0] - q0[0], q1[1] - q0[1]);
    if (gap === null || dd < gap) gap = dd;
  }
  if (gap !== null && gap < floorStep) {
    return bad('this shape would put trapping sites ' + gap.toFixed(2) + ' lattice units ' +
               'apart, closer than the half unit two marks can be drawn at: drag a bigger one');
  }
  var pts = skWeave(sites, closed).pts, m = pts.length;
  if (m < 2) return bad('drag further: a shape needs two ends');
  // R19 where the new rail meets the old one
  var deg = {}, meets = [], nj = 0, nshared = 0, nnew = 0;
  for (i = 0; i < m; i++) {
    var p = pts[i];
    var arms = (closed || (i > 0 && i < m - 1)) ? 2 : 1;
    if (p.node) {
      nshared++;
      deg[p.node] = (deg[p.node] === undefined ? skDegree(p.node) : deg[p.node]) + arms;
      meets.push({ kind: 'node', id: p.node, x: p.x, y: p.y, degree: deg[p.node] });
    } else if (p.split) {
      nj++;
      meets.push({ kind: 'junction', on: p.split.id, x: p.x, y: p.y, degree: 2 + arms });
    } else nnew++;
  }
  for (i = 0; i < meets.length; i++) {
    if (meets[i].degree > lim.maxDeg) {
      return bad('this would make ' + (meets[i].id || 'the new junction on rail ' + meets[i].on) +
                 ' degree ' + meets[i].degree + ', and a node on this device may join at ' +
                 'most ' + lim.maxDeg + ' rails (budget.max_junction_degree)', 'R19');
    }
  }
  // A CLOSED ORBIT CANNOT BE DECLARED ACROSS A POST-SEAL JUNCTION.  `d.loop` is a BUILDER
  // statement and every builder statement is hoisted above the seal, so a node minted by
  // the `add_junction` topology edit does not exist when the walk is written -- and there
  // is no `add_loop` after the seal (`qccd/viz/js/edit.js`'s OPS table is the whole
  // vocabulary).  The rail that has to be split that way is exactly one that already
  // carries an orbit, because `remove_segment` refuses to take a loop edge away and the
  // builder split is therefore unavailable for it.  One junction, one `on=`: at most one
  // of the two orbits can be re-walked, so a ring crossing a ring is refused and said so.
  var topoSplit = false;
  for (i = 0; i < m; i++) if (pts[i].split && skOnLoop(pts[i].split)) topoSplit = true;
  if (closed && topoSplit) {
    return bad('this ring crosses a rail that already carries an orbit, and the studio ' +
               'cannot declare a second orbit across a junction added after the device was ' +
               'sealed -- draw the shape to meet that loop at one of its own sites instead',
               'R21');
  }
  var xs = verts.map(function (v) { return v[0]; }), ys = verts.map(function (v) { return v[1]; });
  var um = toUm(Math.max.apply(null, xs) - Math.min.apply(null, xs),
                Math.max.apply(null, ys) - Math.min.apply(null, ys));
  return { ok: true, why: null, rule: null, tool: tool, verts: verts, sites: sites,
           pts: pts, closed: closed, edges: closed ? m : m - 1, meets: meets,
           n_sites: nnew, n_junctions: nj, n_shared: nshared, bends: bends,
           min_angle: worst, w_um: um.x, h_um: um.y, loop: !topoSplit };
}

// ---- committing -----------------------------------------------------------------------
//
// ONE TRANSACTION.  The shape's own sites, rails and orbit are BUILDER records put through
// `cmpInstantiate`, so every local id is namespaced, `loop=` and the `d.loop` walk are
// rewritten with them, and every part carries `cmp:<inst>`.  A junction on a rail that
// carries an orbit is the one piece that cannot be a builder record (see `sketchPlan`) and
// is emitted as the `add_junction(on=...)` topology edit, whose Python twin
// (`qccd/arch/edit.py`) already splices the split node into every loop that contained the
// rail.
function sketchCommit(plan) {
  if (!plan || !plan.ok) {
    return { ok: false, problems: [{ code: (plan && plan.rule) || 'sketch',
                                     message: (plan && plan.why) || 'nothing to commit' }] };
  }
  // A GENERATOR DEVICE HAS NO BUILDER, so `d.site` cannot be hoisted into it and there is
  // no post-seal verb that declares a loop.  Rather than emit half a shape, say what to
  // press: `explode to explicit…` turns the generator into the builder statements a sketch
  // can join.
  if (!hasBuilder()) {
    return { ok: false, problems: [{ code: 'no_builder',
      message: 'this device came from a generator, so a sketch cannot be added to it as ' +
               'builder statements -- press "explode to explicit…" at the bottom of ' +
               'Elements first' }] };
  }
  var zone = defaultZone();
  if (zone && postSeedZones()[zone]) {
    return { ok: false, problems: [{ code: 'zone_after_seal', targets: [zone],
      message: "zone '" + zone + "' was added after this device was sealed, so a new site " +
               'cannot use it — pick another zone chip.' }] };
  }
  var missing = componentBlocked({ requires: { zones: zone ? [zone] : [] } });
  if (missing.length) {
    return { ok: false, problems: [{ code: 'missing_zone', targets: missing,
      message: 'a sketch places sites in zone ' + Q.pyRepr(missing[0]) + ', which this ' +
               'machine does not declare -- add it from Elements first' }] };
  }
  var pts = plan.pts, m = pts.length, closed = plan.closed, i;
  var inst = 'k' + (++SK_N);
  while (STATE && STATE.device && has(STATE.device.nodes, inst + '.s0')) inst = 'k' + (++SK_N);

  var recs = [], topo = [], ids = [], si = 0, ji = 0, ei = 0, xi = 0;
  for (i = 0; i < m; i++) {
    var p = pts[i];
    if (p.node) { ids.push(p.node); continue; }
    if (p.split && skOnLoop(p.split)) {                    // a junction made after the seal
      var jid = inst + '.j' + (ji++);
      ids.push(jid);
      topo.push({ op: 'add_junction',
                  args: { id: jid, pos: [sk3(p.x), sk3(p.y)], on: p.split.id,
                          segment_ids: [jid + 'a', jid + 'b'],
                          labels: ['cmp:' + inst, 'sketch', 'junction'] } });
      continue;
    }
    if (p.split) {                                          // a junction the builder can hold
      var bj = 'j' + (ji++), S = p.split;
      ids.push(bj);
      recs.push({ method: 'd.junction', args: [bj, Q.pyFloat(p.x), Q.pyFloat(p.y)],
                  kwargs: { labels: ['junction'] } });
      // the crossed rail, re-declared as its two halves; the original goes after the seal
      var la = sk3(skHyp(p.x - S.pa[0], p.y - S.pa[1])) || 1;
      var lb = sk3(skHyp(S.pb[0] - p.x, S.pb[1] - p.y)) || 1;
      recs.push({ method: 'd.segment', args: ['x' + xi + 'a', S.a, bj],
                  kwargs: { length: Q.pyFloat(la), capacity: S.cap, labels: ['rail'] } });
      recs.push({ method: 'd.segment', args: ['x' + xi + 'b', bj, S.b],
                  kwargs: { length: Q.pyFloat(lb), capacity: S.cap, labels: ['rail'] } });
      xi++;
      topo.push({ op: 'remove_segment', args: { id: S.id, on_loop: 'refuse' } });
      continue;
    }
    var sid = 's' + (si++);
    ids.push(sid);
    var kw = {};
    if (zone) kw.zone = zone; else kw.capacity = 1;
    recs.push({ method: 'd.site', args: [sid, Q.pyFloat(p.x), Q.pyFloat(p.y)], kwargs: kw });
  }
  // the ids as they will read AFTER `cmpInstantiate` renames the local ones
  var localIds = {};
  for (i = 0; i < recs.length; i++) localIds[String(recs[i].args[0])] = 1;
  var full = function (id) { return has(localIds, id) ? inst + '.' + id : id; };

  var edges = closed ? m : m - 1, topoEdge = {}, loopId = plan.loop ? (closed ? 'L' : 'P') : null;
  for (i = 0; i < edges; i++) {
    var pa0 = pts[i], pb0 = pts[(i + 1) % m];
    if ((pa0.split && skOnLoop(pa0.split)) || (pb0.split && skOnLoop(pb0.split))) {
      topoEdge[i] = 1; loopId = null;
    }
  }
  for (i = 0; i < edges; i++) {
    var ia = ids[i], ib = ids[(i + 1) % m];
    var pa = pts[i], pb = pts[(i + 1) % m];
    var len = sk3(skHyp(pb.x - pa.x, pb.y - pa.y)) || 1;
    if (topoEdge[i]) {
      topo.push({ op: 'add_segment',
                  args: { id: inst + '.e' + (ei++), a: full(ia), b: full(ib),
                          length: len, capacity: 1,
                          labels: ['rail', 'cmp:' + inst, 'sketch'] } });
    } else {
      var kw2 = { length: Q.pyFloat(len), capacity: 1, labels: ['rail'] };
      if (loopId) kw2.loop = loopId;
      recs.push({ method: 'd.segment', args: ['e' + (ei++), ia, ib], kwargs: kw2 });
    }
  }
  // A CLOSED SHAPE DECLARES A CLOSED LOOP over its sites in orbit order, `kind: 'ring'`;
  // an OPEN one declares the path loop `chain()` gives a linear register, `kind: 'path'`.
  if (loopId) {
    var walk = [];
    for (i = 0; i < m; i++) walk.push(ids[i]);
    recs.push({ method: 'd.loop', args: [loopId, walk],
                kwargs: { closed: !!closed, kind: closed ? 'ring' : 'path' } });
  }
  // the refusals a stamp already makes, made here before anything is written
  for (i = 0; i < recs.length; i++) {
    if (recs[i].method !== 'd.site' && recs[i].method !== 'd.junction') continue;
    var cx0 = Number(Q.unbox(recs[i].args[1])), cy0 = Number(Q.unbox(recs[i].args[2]));
    var coin2 = coincidentAt(cx0, cy0);
    if (coin2) return { ok: false, problems: [coin2] };
    var over = stampContact({ id: '__sk', kind: recs[i].method === 'd.junction' ? 'junction' : 'site',
                              cap: 2 }, cx0, cy0);
    if (over) {
      return { ok: false, problems: [{ code: 'overlap', targets: [over],
        message: 'a site of this shape would overlap the mark ' + over +
                 ' already on the canvas; draw it clear of that part' }] };
    }
  }
  var spec = { name: 'sketch', records: recs, requires: { zones: zone ? [zone] : [] }, pins: [] };
  var ops = cmpInstantiate(spec, inst, 0, 0, 0, ['sketch:' + plan.tool])
              .map(function (o) { return { build: o }; });
  for (i = 0; i < topo.length; i++) ops.push({ topology: topo[i] });

  var was = skGeomLints();
  var r = transaction(ops, 'sketch ' + plan.tool);
  if (!r.ok) return r;
  // THE GUARANTEE, CHECKED RATHER THAN ARGUED.  `Q.lint` is what judges R19 / R20 / R21 on
  // the committed document; a sketch that added one of those findings is taken back through
  // the same single undo group it was committed as, and the rule is named.
  var now = skGeomLints(), fresh = [];
  for (i = 0; i < now.list.length; i++) {
    if (!has(was.seen, now.list[i].message)) fresh.push(now.list[i]);
  }
  if (fresh.length) {
    undoGroup();
    return { ok: false, problems: [{ code: fresh[0].code, message: fresh[0].message }] };
  }
  r.instance = inst;
  r.nodes = ids.map(full);
  r.loop = loopId ? inst + '.' + loopId : null;
  return r;
}
function skGeomLints() {
  var out = { list: [], seen: {} }, i;
  for (i = 0; i < LINTS.length; i++) {
    var c = LINTS[i].code;
    if (c !== 'R19' && c !== 'R20' && c !== 'R21') continue;
    out.list.push(LINTS[i]);
    out.seen[LINTS[i].message] = 1;
  }
  return out;
}

// ---- the preview ----------------------------------------------------------------------
//
// ITS OWN GROUP, and nothing else writes into it -- the ruler's rule, for the ruler's
// reason: `clearTransients()` wipes the animation groups on every frame, so a preview
// drawn into one of those would blink out from under the drag.  Pooled and hidden, never
// removed (`remove()` is a no-op in tests/shim.mjs).
var gSketch = null, SKPATH = null, SKDOT = [], SKTXT = null, SKDOT_MAX = 600;
function ensureSketch() {
  if (gSketch || typeof svg === 'undefined' || !svg) return;
  gSketch = el('g', { 'pointer-events': 'none' });
  svg.append(gSketch);
  SKPATH = el('path', { fill: 'none', stroke: C.navy, 'stroke-width': 1.8,
                        'stroke-dasharray': '7 4' });
  SKPATH.style.display = 'none'; gSketch.append(SKPATH);
  SKTXT = el('text', { 'font-size': 11, 'font-weight': 650, fill: C.ink });
  SKTXT.style.display = 'none'; gSketch.append(SKTXT);
}
function skDot(i) {
  if (i >= SKDOT_MAX) return null;
  while (SKDOT.length <= i) {
    var d = el('circle', { r: 3, fill: 'none', stroke: C.navy, 'stroke-width': 1.4 });
    d.style.display = 'none';
    gSketch.append(d);
    SKDOT.push(d);
  }
  return SKDOT[i];
}
// WHAT THE PREVIEW CLAIMS, as numbers: the size on both axes in micrometres, how many
// trapping sites the release will make, what the corners are, and -- in red -- the reason
// the shape would be refused.  Published so a harness can assert the claim rather than
// scrape a label.
function sketchReadout() {
  var out = { on: SKETCH, tool: SKETCH, mode: designMode(), dragging: !!SKDRAG,
              points: SKPTS.length, ok: false, why: null, rule: null, n_sites: 0,
              n_junctions: 0, n_shared: 0, bends: 0, closed: false,
              w_um: null, h_um: null, text: '' };
  var plan = sketchLive();
  if (!plan) return out;
  out.ok = !!plan.ok; out.why = plan.why; out.rule = plan.rule;
  out.n_sites = plan.n_sites || 0; out.n_junctions = plan.n_junctions || 0;
  out.n_shared = plan.n_shared || 0;
  out.bends = plan.bends || 0; out.closed = !!plan.closed;
  out.w_um = plan.w_um === undefined ? null : plan.w_um;
  out.h_um = plan.h_um === undefined ? null : plan.h_um;
  out.text = plan.ok
    ? (fmtUm(plan.w_um) + ' × ' + fmtUm(plan.h_um) + '  ·  ' + plan.n_sites +
       ' trapping site' + (plan.n_sites === 1 ? '' : 's') +
       (plan.n_junctions ? '  ·  ' + plan.n_junctions + ' junction' +
                           (plan.n_junctions === 1 ? '' : 's') : '') +
       (plan.closed ? '  ·  a closed orbit' : '') +
       (plan.bends ? '  ·  corner: a bend (R18), not a junction' : ''))
    : ((plan.rule ? plan.rule + ': ' : '') + plan.why);
  return out;
}
// the plan for whatever is being drawn right now, or null when nothing is
function sketchLive() {
  if (!SKETCH) return null;
  if (SKETCH === 'poly') {
    var pts = SKPTS.slice();
    if (SKHOVER && !skPolyWouldClose()) pts.push([SKHOVER.x, SKHOVER.y]);
    if (pts.length < 2) return null;
    return sketchPlan('poly', { pts: pts, closed: skPolyWouldClose() }, null, {});
  }
  if (!SKDRAG) return null;
  return sketchPlan(SKETCH, SKDRAG.a, SKDRAG.b, { shift: SKDRAG.shift });
}
function skPolyWouldClose() {
  if (SKPTS.length < 3 || !SKHOVER) return false;
  var U = skUnit();
  return skHyp((SKHOVER.x - SKPTS[0][0]) / (U.x || 1),
               (SKHOVER.y - SKPTS[0][1]) / (U.y || 1)) < 0.45;
}
function sketchRedraw() {
  if (!gSketch) { if (!SKETCH) return; ensureSketch(); }
  if (!gSketch) return;
  var plan = sketchLive(), i, u = userPerPx();
  for (i = 0; i < SKDOT.length; i++) SKDOT[i].style.display = 'none';
  SKPATH.style.display = 'none';
  SKTXT.style.display = 'none';
  if (!plan) return;
  var verts = plan.verts || [], sites = plan.sites || [];
  if (verts.length >= 2) {
    var d = '';
    for (i = 0; i < verts.length; i++) {
      var q = skToPx(verts[i][0], verts[i][1]);
      d += (i ? ' L ' : 'M ') + q.x + ' ' + q.y;
    }
    if (plan.closed) d += ' Z';
    SKPATH.setAttribute('d', d);
    SKPATH.setAttribute('stroke', plan.ok ? C.navy : C.z);
    SKPATH.setAttribute('stroke-width', 1.8 * u);
    SKPATH.setAttribute('stroke-dasharray', (7 * u) + ' ' + (4 * u));
    SKPATH.style.display = '';
  }
  for (i = 0; i < sites.length; i++) {
    var dot = skDot(i);
    if (!dot) break;
    var s = skToPx(sites[i][0], sites[i][1]);
    dot.setAttribute('cx', s.x); dot.setAttribute('cy', s.y);
    dot.setAttribute('r', 3.4 * u);
    dot.setAttribute('stroke-width', 1.4 * u);
    dot.setAttribute('stroke', plan.ok ? C.navy : C.z);
    dot.style.display = '';
  }
  var r = sketchReadout();
  if (r.text && verts.length) {
    var x0 = Math.min.apply(null, verts.map(function (v) { return v[0]; }));
    var y0 = Math.min.apply(null, verts.map(function (v) { return v[1]; }));
    var at = skToPx(x0, y0);
    SKTXT.setAttribute('x', at.x); SKTXT.setAttribute('y', at.y - 8 * u);
    SKTXT.setAttribute('font-size', 12 * u);
    SKTXT.setAttribute('fill', r.ok ? C.ink : C.z);
    SKTXT.textContent = r.text;
    SKTXT.style.display = '';
  }
}
function sketchClear() {
  SKPTS = []; SKDRAG = null; SKHOVER = null;
  sketchRedraw();
  return sketchReadout();
}

// ---- the pointer, as five adapters ----------------------------------------------------
//
// A POINT THE SHAPE STARTS OR ENDS AT SNAPS TO SOMETHING REAL FIRST -- the ruler's own
// `measureSnap`, reused rather than re-derived: an end within a few pixels of a site or a
// rail belongs to that site or that rail, and a rail end becomes the junction.
function skPoint(mx, my, endpoint) {
  if (endpoint) {
    var s = measureSnap(mx, my);
    if (s && s.kind !== 'free') return skToU(s.x, s.y);
  }
  var u = skToU(mx, my);
  var sn = snapTo(u.x, u.y, false, false, SNAP);
  return { x: sn.x, y: sn.y };
}
function sketchDown(mx, my, mod) {
  if (!SKETCH) return null;
  mod = mod || {};
  ensureSketch();
  if (SKETCH === 'poly') return sketchClick(mx, my, mod);
  var a = skPoint(mx, my, true);
  SKDRAG = { a: a, b: { x: a.x, y: a.y }, shift: !!mod.shift };
  sketchRedraw();
  return sketchReadout();
}
function sketchMove(mx, my, mod) {
  if (!SKETCH) return null;
  mod = mod || {};
  if (SKETCH === 'poly') SKHOVER = skPoint(mx, my, true);
  else if (SKDRAG) { SKDRAG.b = skPoint(mx, my, true); SKDRAG.shift = !!mod.shift; }
  else return sketchReadout();
  sketchRedraw();
  return sketchReadout();
}
function sketchUp(mx, my, mod) {
  if (!SKETCH || SKETCH === 'poly' || !SKDRAG) return null;
  mod = mod || {};
  SKDRAG.b = skPoint(mx, my, true);
  SKDRAG.shift = !!mod.shift;
  // A PRESS THAT NEVER MOVED IS A CLICK, NOT A SHAPE.  It used to release into "that is
  // shorter than one lattice unit", which is a refusal for a gesture nobody made -- the
  // same noise `clickStage` avoids when nothing is armed.  Below the 4 px the drag
  // threshold already uses, the tool stays armed and says nothing.
  var moved = skHyp((SKDRAG.b.x - SKDRAG.a.x) * (L.sx || 1),
                    (SKDRAG.b.y - SKDRAG.a.y) * (L.sy || 1));
  if (moved < 4 * userPerPx()) { SKDRAG = null; sketchRedraw(); return null; }
  var plan = sketchPlan(SKETCH, SKDRAG.a, SKDRAG.b, { shift: SKDRAG.shift });
  SKDRAG = null;
  sketchRedraw();
  // A REFUSED SHAPE DOES NOT COMMIT.  The reason was already in the readout, in red; the
  // toast names the rule so it survives the release.
  if (!plan.ok) {
    toast('bad', (plan.rule ? plan.rule + ': ' : '') + plan.why);
    return { ok: false, problems: [{ code: plan.rule || 'sketch', message: plan.why }] };
  }
  return skFinish(plan);
}
function sketchClick(mx, my, mod) {
  if (SKETCH !== 'poly') return null;
  ensureSketch();
  var p = skPoint(mx, my, true);
  SKHOVER = p;
  if (skPolyWouldClose()) return sketchFinish(true);
  SKPTS.push([p.x, p.y]);
  sketchRedraw();
  return sketchReadout();
}
// double-click, or Enter: the polyline ends OPEN.  Clicking the first point again closes it.
function sketchFinish(closed) {
  if (SKETCH !== 'poly') return null;
  var pts = SKPTS.slice();
  if (pts.length < 2) {
    sketchClear();
    return { ok: false, problems: [{ code: 'sketch',
      message: 'a polyline needs at least two points' }] };
  }
  var plan = sketchPlan('poly', { pts: pts, closed: !!closed }, null, {});
  SKPTS = []; SKHOVER = null;
  sketchRedraw();
  if (!plan.ok) {
    toast('bad', (plan.rule ? plan.rule + ': ' : '') + plan.why);
    return { ok: false, problems: [{ code: plan.rule || 'sketch', message: plan.why }] };
  }
  return skFinish(plan);
}
function skFinish(plan) {
  var r = sketchCommit(plan);
  if (!r.ok) {
    toast('bad', ((r.problems || [])[0] || {}).message || 'refused');
    return r;
  }
  toast('ok', SK_TOOLS[plan.tool].toLowerCase() + ': ' + plan.n_sites + ' trapping site' +
              (plan.n_sites === 1 ? '' : 's') +
              (plan.n_junctions ? ', ' + plan.n_junctions + ' junction' +
                                  (plan.n_junctions === 1 ? '' : 's') : '') +
              (plan.closed ? ', one closed orbit' : '') +
              (plan.bends ? ' · a corner is a bend (R18), not a junction' : ''));
  // ONE SHAPE PER ARMING, exactly as a tile disarms the moment its element lands.
  SKETCH = null;
  if (!DMODE) DMODE = 'sketch';       // a shape was drawn: the rail stays on the shapes
  sketchRedraw();
  setCursor(null);
  paint();
  return r;
}
// THE HEADLESS GESTURE -- what the pointer does, in MODEL (lattice) units and callable
// without an Event, so the harness drives the rule rather than the handler.
function sketchDraw(tool, from, to, mod) {
  mod = mod || {};
  if (tool) { SKETCH = null; sketchTool(tool); }
  if (!SKETCH) {
    return { ok: false, problems: [{ code: 'sketch', message: 'no shape tool is armed' }] };
  }
  if (SKETCH === 'poly') {
    SKPTS = (from || []).map(function (p) { return [p[0], p[1]]; });
    return sketchFinish(!!mod.closed);
  }
  var a = { x: from[0], y: from[1] }, b = { x: to[0], y: to[1] };
  var plan = sketchPlan(SKETCH, a, b, { shift: !!mod.shift });
  if (!plan.ok) {
    toast('bad', (plan.rule ? plan.rule + ': ' : '') + plan.why);
    return { ok: false, problems: [{ code: plan.rule || 'sketch', message: plan.why }] };
  }
  return skFinish(plan);
}

// ------------------------------------------------------------------- the keymap
//
// ONE TABLE.  Two `keydown` listeners used to compete for the same keys: this file's
// nudged the selection with the arrows while the page's scrubbed the programme, so a
// nudge also moved the playhead; space was held-to-pan here and play/pause there, and
// `e` flipped a mode the pointer no longer has.  `keyGesture` is the only reading of a
// key now -- pure, no Event -- and the page's one listener dispatches on the verb it
// returns.  The `?` overlay is rendered from the same rows, so a binding cannot exist
// without its line of help, or the other way round.
var KEYMAP = [
  { keys: [' '], verb: 'toggle-play', label: 'space', doc: 'play / pause' },
  { keys: ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'], verb: 'nudge-or-seek',
    label: '\u2190 \u2191 \u2192 \u2193',
    doc: 'nudge the selection one lattice step (shift: four) \u00b7 with nothing selected, previous / next instruction' },
  { keys: ['Enter'], verb: 'glide', label: 'enter',
    doc: 'glide the current instruction \u00b7 in the Modify panel, apply it' },
  { keys: ['Delete', 'Backspace'], verb: 'remove', label: 'del',
    doc: 'remove the selection (a site takes its segments with it)' },
  { keys: ['Escape'], verb: 'escape', label: 'esc',
    doc: 'one thing per press: close the element menu, cancel the drag, disarm the stamp, clear the selection, close this help \u00b7 in a text field it only leaves the field, and in the Modify panel it cancels it' },
  { keys: ['Home'], verb: 'seek-first', label: 'home', doc: 'first instruction' },
  { keys: ['End'], verb: 'seek-last', label: 'end', doc: 'last instruction' },
  { keys: ['PageUp'], verb: 'seek-back', label: 'pgup', doc: '25 instructions back' },
  { keys: ['PageDown'], verb: 'seek-ahead', label: 'pgdn', doc: '25 instructions ahead' },
  { keys: ['j', '.'], verb: 'seek-next', label: 'j  .', doc: 'next instruction' },
  { keys: ['k', ','], verb: 'seek-prev', label: 'k  ,', doc: 'previous instruction' },
  { keys: ['f', '0'], verb: 'fit', label: 'f  0', doc: 'fit the stage' },
  { keys: ['F'], verb: 'follow', label: 'F', doc: 'toggle Follow in the programme listing' },
  { keys: ['1'], verb: 'pane-P', label: '1', doc: 'Program pane' },
  { keys: ['2'], verb: 'pane-A', label: '2', doc: 'Device pane' },
  { keys: ['3'], verb: 'pane-M', label: '3', doc: 'Machine pane' },
  { keys: ['['], verb: 'fold-rail', label: '[', doc: 'fold the element rail' },
  { keys: [']'], verb: 'fold-dock', label: ']', doc: 'fold the side panels' },
  { keys: ['\\'], verb: 'fold-both', label: '\\', doc: 'fold both' },
  { keys: ['/'], verb: 'filter', label: '/', doc: 'filter the programme listing' },
  { keys: ['L', 'l'], verb: 'reconcile', label: 'L',
    doc: 'set the last moved node\u2019s segment lengths to match the drawing' },
  { keys: ['m', 'M'], verb: 'measure', label: 'm',
    doc: 'Measure: click two points for the distance in micrometres, a third for the angle at the middle one, or two rails for the angle between them \u00b7 esc clears' },
  { keys: ['d', 'D'], verb: 'design-mode', label: 'd',
    doc: 'switch between Sketch (draw the shape the ions travel on) and Parts (place sites, junctions and rails one at a time)' },
  { keys: ['r', 'R'], verb: 'shape-rect', label: 'r',
    doc: 'Sketch: Rectangle \u00b7 drag corner to corner for a closed rectangular rail' },
  { keys: ['e', 'E'], verb: 'shape-ellipse', label: 'e',
    doc: 'Sketch: Ellipse \u00b7 drag a box for a closed rounded rail; shift makes it a circle' },
  { keys: ['n', 'N'], verb: 'shape-line', label: 'n',
    doc: 'Sketch: Line \u00b7 drag end to end for an open register' },
  { keys: ['p', 'P'], verb: 'shape-poly', label: 'p',
    doc: 'Sketch: Polyline \u00b7 click point after point; double-click or enter ends it open, clicking the first point again closes it' },
  { keys: ['?'], verb: 'help', label: '?', doc: 'toggle this guide' },
  { keys: ['h', 'H'], verb: 'explain', label: 'h', doc: 'Explain: label the parts of the screen' },
  { keys: ['z', 'Z'], ctrl: true, verb: 'undo', label: 'ctrl+Z',
    doc: 'undo one whole gesture (a group drag or a nudge is one step) \u00b7 ctrl+shift+Z redo' },
  { keys: ['y', 'Y'], ctrl: true, verb: 'redo', label: 'ctrl+Y', doc: 'redo one gesture' },
  { keys: ['s', 'S'], ctrl: true, verb: 'save', label: 'ctrl+S', doc: 'save the design' }
];
var POINTER_HELP = [
  ['drag an element', 'move it anywhere \u00b7 Snap (button) lands it on the lattice, shift on quarter steps, alt frees a snapped drag \u00b7 a member of a selection, a segment or a loop moves the whole thing as one \u00b7 a red HUD before release means this drop will be refused: slide a little further'],
  ['drag a placed component', 'the whole component moves as one step \u00b7 alt+drag takes just the part under the pointer'],
  ['drag empty stage', 'pan \u00b7 so does a middle-drag, and a right-drag that STARTS on empty stage'],
  ['shift+drag empty stage', 'marquee-select everything inside the rectangle'],
  ['shift+drag element to element', 'a new segment between them'],
  ['click', 'select (shift adds) \u00b7 click empty stage to clear'],
  ['right-click a part', 'its menu: everything the page can do to the thing under the pointer \u00b7 Modify\u2026 opens its own fields (a site\u2019s capacity and position, a rail\u2019s length, a loop\u2019s kind), and beside it Set zone, place another, delete \u00b7 on a rail, drop a trapping site into it \u00b7 on a placed component, select or delete the whole part \u00b7 right-clicking one member of a selection keeps the selection and acts on all of it \u00b7 an action that cannot run here is shown greyed with the reason, never hidden'],
  ['double-click empty stage', 'place a site, or the armed element'],
  ['armed tile + click', 'place the element where you click, once: the tile disarms as it lands \u00b7 shift-click places and stays armed for a run of them'],
  ['wheel', 'zoom at the pointer'],
  ['Measure on + click', 'a ruler end: it snaps to the nearest site or rail \u00b7 two clicks give a distance, three give an angle'],
  ['Sketch: a shape armed + drag', 'draw the shape the ions travel on \u00b7 the readout gives its size in micrometres, how many trapping sites the release will make, and in red the rule that would refuse it \u00b7 the release lays sites one lattice unit apart, declares the orbit if the shape closed, and puts a junction where the new rail meets or crosses one that is already there'],
  ['Sketch: Polyline armed + click', 'one point per click \u00b7 double-click or enter finishes it open, clicking the first point again closes it']
];
// THE VERB A KEY MEANS, from the table above and nothing else.  `mods` is
// `{ctrl, meta, shift, alt}`; `ctx.field` names the text field that has focus (its id,
// or its tag), because a field keeps its own keys: there Escape only leaves the field --
// clearing the two listing filters -- and everything else is the browser's.  `null`
// means "not ours": the listener must not preventDefault, so browser accelerators keep
// working.  Reads `SELSET` for the arrows (nudge with a selection, scrub without); no
// side effects.
function keyGesture(key, mods, ctx) {
  mods = mods || {}; ctx = ctx || {};
  var accel = !!(mods.ctrl || mods.meta);
  // THE MODIFY PANEL'S OWN TWO KEYS.  Its rows are text inputs, so the generic field
  // branch below would answer 'blur' for Escape and nothing at all for Enter -- and a
  // form that cannot be submitted or abandoned from the keyboard is a form nobody
  // finishes.  No new KEYMAP row: these are the meanings enter and esc already have,
  // in one more place, and the two rows say so.
  if (ctx.field && String(ctx.field).slice(0, 5) === 'mdlg_') {
    if (key === 'Enter' && !accel) return 'modify-apply';
    if (key === 'Escape') return 'modify-cancel';
  }
  if (ctx.field) {
    if (key === 'Escape') return (ctx.field === 'pFilter' || ctx.field === 'aFilter') ? 'clear-filter' : 'blur';
    if (accel && !mods.alt && (key === 's' || key === 'S')) return 'save';
    return null;
  }
  if (mods.alt) return null;
  // ENTER ENDS THE POLYLINE while one is being drawn.  Its other meaning -- glide the
  // instruction -- is what it keeps everywhere else, and the KEYMAP row still documents it.
  if (key === 'Enter' && !accel && SKETCH === 'poly' && SKPTS.length && !VIEW_ONLY) {
    return 'sketch-finish';
  }
  for (var i = 0; i < KEYMAP.length; i++) {
    var row = KEYMAP[i];
    if (!!row.ctrl !== accel || row.keys.indexOf(key) < 0) continue;
    if (row.verb === 'undo') return mods.shift ? 'redo' : 'undo';
    // a fold moves the stage under a live drag, and the drop would land a lattice step
    // from the pointer: the fold keys wait.  (The arrows do not: a nudge or a seek moves
    // nothing under the pointer.)
    if ((GHOST || BAND) && row.verb.indexOf('fold-') === 0) return null;
    if (row.verb === 'nudge-or-seek') {
      if (SELSET.length && !VIEW_ONLY) return 'nudge';
      return (key === 'ArrowRight' || key === 'ArrowDown') ? 'seek-next' : 'seek-prev';
    }
    if (VIEW_ONLY && !VIEW_ONLY_VERBS[row.verb]) return null;
    return row.verb;
  }
  return null;
}
function keyHelp() {
  return { keys: KEYMAP.map(function (r) { return [r.label, r.doc]; }), pointer: POINTER_HELP.slice() };
}
// THE GUIDE.  It opens on what you are looking at -- each mark drawn by the stage's own
// avatar code beside its plain sentence -- then how to use the page in three steps, then
// the gesture and key tables.  A newcomer reads the top; the tables are for later.
var GLOSSARY = [
  ['el:site', { type: 'site' }, { zone: 'trap', cap: 2 }],
  ['el:junction', { type: 'junction' }, {}],
  ['el:segment', { type: 'segment' }, {}],
  ['el:loop', { type: 'loop' }, {}],
  ['el:zone_type', { type: 'zone_type' }, { zone: 'load', cap: 8 }],
  ['el:component', { type: 'cmp:spur_dock' }, {}]
];
var STEPS = [
  ['Build a device', 'Pick a card on the empty canvas for a ready-made one, or press Trapping site and click the canvas a few times, then shift-drag one site onto another to lay a rail between them.'],
  ['Run a programme on it', 'Press Test drive for a small valid programme, or write your own in the Write panel and press Evaluate. The ions move on the canvas; the counters in the head keep score.'],
  ['Read the verdict', 'The status line names the instruction, the price line says what it costs, and the Report panel lists every hardware rule the design passes or fails, with the reason.']
];
function renderHelp() {
  var host = $('helpBody'), guide = $('guideBody');
  if (!host) return;
  var h = keyHelp(), i;
  var tr = function (r) { return '<tr><td><code>' + esc2(r[0]) + '</code></td><td>' + esc2(r[1]) + '</td></tr>'; };
  // the tables, as markup on their own host -- what the harness census reads
  host.innerHTML = '<h3>Pointer</h3><table>' + h.pointer.map(tr).join('') + '</table>' +
                   '<h3>Keys</h3><table>' + h.keys.map(tr).join('') + '</table>' +
                   '<p class="mut">Hover anything on the page for what it is; press <b>Explain</b> in the head to label the regions. Esc, or a click outside this card, closes it.</p>';
  if (!guide) return;
  guide.replaceChildren();
  var h3 = function (t) { var e = elh('h3'); e.textContent = t; return e; };
  guide.append(h3('What you are looking at'));
  var gl = elh('div', 'gloss');
  for (i = 0; i < GLOSSARY.length; i++) {
    var g = GLOSSARY[i], e = hintFor(g[0]);
    if (!e) continue;
    var av = elh('span', 'avatar');
    try { av.append(kindAvatar(g[1], g[2])); } catch (err) { /* a component the page does not ship */ }
    var tx = elh('span');
    var b = elh('b'); b.textContent = e.t; tx.append(b);
    var d = elh('span'); d.textContent = ' ' + e.d; tx.append(d);
    gl.append(av, tx);
  }
  guide.append(gl);
  guide.append(h3('How to use it'));
  var ol = elh('ol', 'steps');
  for (i = 0; i < STEPS.length; i++) {
    var li = elh('li'), sb = elh('b');
    sb.textContent = STEPS[i][0] + '. ';
    var st = elh('span'); st.textContent = STEPS[i][1];
    li.append(sb, st);
    ol.append(li);
  }
  guide.append(ol);
}
// the overlay's state, readable: the class is what the stylesheet keys on, the inline
// display is what a harness can read back
var HELPON = false;
function helpToggle(on) {
  HELPON = (on === undefined) ? !HELPON : !!on;
  var h = $('help');
  if (h) {
    h.className = HELPON ? 'help' : 'help off';
    if (h.style) h.style.display = HELPON ? '' : 'none';
  }
  return HELPON;
}

// EVERY KIND HOVERS.  This used to bail out for a segment (`if (!h || h.kind ===
// 'segment')`) and had no idea a loop existed, so two of the four things on the stage
// gave no feedback whatever.  Returns the hit, so the pointer handler is a one-line
// adapter and the harness can assert what the cursor is about to say.
var HOVERED = null;
function hover(mx, my, cx, cy) {
  var h = hit(mx, my);
  HOVERED = h;
  setCursor(h);
  paintOverlay();
  // the on-canvas hint follows the pointer; the adapter passes client coordinates and the
  // harness, which has none, passes nothing and gets no card
  if (cx !== undefined) stageHint(h, cx, cy);
  return h;
}

// THE HUD SITS AT THE POINTER.  It is `position:fixed`, so the client coordinates the
// pointer event carries are exactly its coordinates -- they used to be written into a
// stage-relative box and the HUD drew 287 px right and 196 px below the pointer.  Two
// lines: where the drop lands and how far it moved, then the one thing worth saying
// about it, cut short (the full sentence is in `move()`'s return).  Red means "this
// release will be refused"; it flips to the left of the pointer near the stage's right
// edge so it never runs off the window.
function hudBrief(s) {
  s = String(s || '');
  var dot = s.indexOf('. ');
  if (dot > 0) s = s.slice(0, dot);
  return s.length > 72 ? s.slice(0, 71) + '\u2026' : s;
}
function showHud(cx, cy, r) {
  var hud = $('hud');
  if (!hud || !r || !GHOST) return;
  var bad = (r.problems || []).length > 0, warn = (r.warnings || []).length > 0;
  hud.className = bad ? 'hud bad' : warn ? 'hud warn' : 'hud';
  var f = fitBox(), right = (f.r.left || 0) + (f.r.width || 0);
  var flip = cx > right - 320;
  // A FLIPPED HUD IS ANCHORED BY ITS RIGHT EDGE, not translated: a fixed box at
  // `left: 1536px` in a 1600 px window is laid out in the 64 px that remain and wraps
  // every two words, and the transform moves the squeezed box, it does not widen it.
  var iw = (typeof innerWidth === 'number' && innerWidth > 0) ? innerWidth : right;
  hud.style.top = cy + 'px';
  if (flip) { hud.style.left = 'auto'; hud.style.right = (iw - cx + 12) + 'px'; }
  else { hud.style.right = 'auto'; hud.style.left = (cx + 12) + 'px'; }
  hud.style.transform = 'translate(0, -26px)';
  var d = Math.sqrt((r.x - GHOST.x0) * (r.x - GHOST.x0) + (r.y - GHOST.y0) * (r.y - GHOST.y0));
  var note = bad ? 'refused: ' + r.problems[0].message
           : warn ? ((r.brief || [])[0] || r.warnings[0])
           : (r.contact ? 'against ' + r.contact + ' \u2014 marks do not overlap' : '');
  // LATTICE AND MICROMETRES, both.  A drag is where a designer decides a distance, and
  // until now the only number it offered was in lattice units -- which are not a length.
  var um = toUm(r.x, r.y);
  hud.textContent = GHOST.id + ' -> (' + r.x.toFixed(2) + ', ' + r.y.toFixed(2) + ') = (' +
                    um1(um.x) + ', ' + um1(um.y) + ') um  d=' + d.toFixed(2) + ' u = ' +
                    fmtUm(distUm(r.x - GHOST.x0, r.y - GHOST.y0)) +
                    (note ? '\n' + hudBrief(note) : '');
  EGHOST.setAttribute('cx', GHOST.px0); EGHOST.setAttribute('cy', GHOST.py0);
  EGHOST.setAttribute('r', 0.45 * L.g); EGHOST.style.display = '';
  EGHOST.setAttribute('stroke', bad ? C.z : C.muted);
  EGHOST.setAttribute('opacity', bad ? 0.9 : 0.5);
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

// ------------------------------------------------------------------- the explain layer
//
// ONE TABLE SAYS WHAT EVERYTHING IS.  Someone new to trapped-ion hardware -- or only new
// to this picture of it -- should be able to point at any control, tile, chip, tab, rule
// or mark and be told in plain words what it is and what it does here.  Every such
// sentence lives in HINTS, keyed by the `data-hint` an element carries, and four surfaces
// read it and nothing else: the hover card, the region captions the Explain toggle
// shows, the guide behind `?`, and the on-canvas line that names what is under the
// pointer.  The physics stays where it was -- the schema's own blurb (`docOf`) is shown
// under the plain sentence, never rewritten here -- so the tool has one voice for "what
// is this" and one source for "what does it cost".
var HINTS = {
  // the three regions of the screen
  'region:rail':  { t: '1 \u00b7 Elements', d: 'The standard parts: press a tile, then click the canvas to place it there; pick a zone type first for a site. Everything else about the device is in the tools bar above.' },
  'region:tools': { t: '0 \u00b7 Tools', d: 'One button per thing that used to crowd the rail: Start a device, append a row, the machine settings, whole components. Each opens a panel under its button; Escape closes it. What you can do to one PART of the device is on the part itself — right-click it. The search box finds any feature on the page (Ctrl+K).' },
  'tools:start': { t: 'Start', d: 'A new device: a blank canvas, a generator, or one of the shipped devices opened as itself.' },
  'tools:rows': { t: 'Append a row', d: 'Add a row to the device as code: a curve point, a zone, a class, a wiring or budget setting.' },
  'tools:machine': { t: 'Machine settings', d: 'The physics and the control plane: primitives, heating, species, budgets, wiring.' },
  'tools:components': { t: 'Components', d: 'Whole parts placed as one: a trap with its junction, a register, a dock spur. Press one, then click the canvas.' },
  // (no 'tools:selection': the button it described is gone.  The rail's own Selection
  //  section is inside `region:rail`, and per-element actions are `ctxmenu` below.)
  'search': { t: 'Search', d: 'Type any words: every control on the page, its hint, and every lesson is indexed. Choose a hit to open where it lives, scroll to it and flash it. Ctrl+K focuses this box.' },
  'region:stage': { t: '2 \u00b7 Canvas', d: 'The device, drawn to scale. Drag a part to move it; drag empty space to pan; wheel to zoom; shift-drag one site onto another to join them with a rail.' },
  'region:bar':   { t: 'Transport', d: 'Plays the hardware programme on this device: every step is one instruction that moves, gates or measures ions.' },
  'region:dock':  { t: '4 \u00b7 Panel', d: 'One panel at a time, opened from the menu in the head: the course, the device as code, the machine settings, a place to write your own test programme, or the report. Press its menu item again to close it.' },
  'region:prog':  { t: '3 \u00b7 Program', d: 'The hardware programme one instruction per row, and the circuit it was compiled from, beside the animation: the executing instruction is marked and the next ones are in view. Hardware, Gates or Both.' },
  'progview': { t: 'Hardware \u00b7 Gates \u00b7 Both', d: 'What the Program pane shows: the hardware instructions, the circuit statements they realise, or both stacked. Only a compiled page has a circuit.' },
  'progpin': { t: 'Beside the animation', d: 'Keep the programme in its own column next to the canvas, the whole height, or put it back among the panels to give the canvas the width.' },
  // the picture, element by element
  'el:site':       { t: 'Trapping site', d: 'A pocket in the electric field where ions sit still. Each site holds a few ions (its capacity), and its zone says what may happen there: a gate, a measurement, cooling.', k: 'press the tile, then click the canvas' },
  'el:junction':   { t: 'Junction', d: 'A crossing where rails meet. Ions pass through it on the way somewhere else and never rest in it, and every crossing heats the ion a little.', k: 'press the tile, then click the canvas' },
  'el:segment':    { t: 'Segment', d: 'One stretch of rail between two sites or junctions: the road an ion travels along, paved with the electrodes that push it.', k: 'shift-drag from one site to another' },
  'el:loop':       { t: 'Loop', d: 'A closed ring of sites. The machine can rotate every ion on a loop by one step in a single instruction \u2014 the cheapest way to move many ions at once.', k: 'select the sites in order, then Close loop' },
  'el:zone_type':  { t: 'Zone type', d: 'A label a site carries: how many ions fit, and whether a gate, a measurement or cooling can happen there. Pick the chip you want before placing sites.', k: 'click a chip to choose it for the next site' },
  'zone:new':      { t: 'New zone type', d: 'Declare another kind of site \u2014 a different capacity, or different things allowed in it.' },
  'el:curve_point': { t: 'Curve point', d: 'One measured data point of how fast an operation runs against how much it heats the ion. The price of every move is read off these curves.', k: 'adds a row to a named curve' },
  'el:primitives': { t: 'Primitives', d: 'The physics of the machine: how long a shuttle, a split, a merge, a crossing or a gate takes, and how much each heats the ion. Every price on this page comes from here.', k: 'edit in place' },
  'el:control':    { t: 'Control plane', d: 'The wiring: how many voltage sources (DACs) drive how many electrodes, and how many different motions can happen in one step. This is what the DAC count measures.', k: 'edit in place' },
  'el:heating':    { t: 'Heating', d: 'How fast an ion warms up just by waiting, in quanta per millisecond. A hot ion makes a bad gate, so time itself has a price.', k: 'edit in place' },
  'el:species':    { t: 'Species', d: 'Which ion carries the qubit, which one is used for cooling, and how long the qubit stays coherent.', k: 'edit in place' },
  'el:budget':     { t: 'Budget', d: 'Hard ceilings for the design \u2014 at most this many DACs, junctions or square millimetres. The report says when a design goes over.', k: 'edit in place' },
  'explode': { t: 'Convert parts to plain sites', d: 'Turns every placed component into ordinary sites and rails so they can be edited one by one. The device stays the same; only its description changes.' },
  'el:component':  { t: 'Component', d: 'A whole part made of several sites and rails \u2014 a dock, a tile, a register \u2014 placed as one piece and moved as one piece.', k: 'press the tile, then click the canvas' },
  // the toolbar
  'play':   { t: 'Play / Pause', d: 'Run the programme as an animation: ions move along the rails one instruction at a time.', k: 'space' },
  'step':   { t: 'Step', d: 'Jump to the end of the next instruction.', k: '\u2192' },
  'glide':  { t: 'Glide', d: 'Animate just the next instruction, then stop.', k: 'enter' },
  'phase':  { t: 'Phase', d: 'Jump to the next batch of instructions that run together.' },
  'reset':  { t: 'Reset', d: 'Back to the first instruction.', k: 'home' },
  'fit':    { t: 'Fit', d: 'Zoom so the whole device fills the canvas.', k: 'f' },
  'slider': { t: 'Position', d: 'Where you are in the programme. Drag to scrub.' },
  'speed':  { t: 'Speed', d: 'How fast the animation plays, relative to real time.' },
  'colour': { t: 'Colour by', d: '\u201crole\u201d colours an ion by what it is doing; \u201cheating\u201d colours it by how hot it has become \u2014 a hot ion gates badly.' },
  'snap':   { t: 'Snap', d: 'Off (the default): a part lands exactly where you drop it, at any coordinate. On: it lands on the lattice; hold alt to place it freely anyway, shift for quarter steps. Marks never overlap: a drag stops against the next mark and slides along it.' },
  'truescale': { t: 'True scale', d: 'On (the default): one screen pixel is the same distance across and down, so an angle you measure on the screen is the angle on the chip. Off: a long thin device is stretched to fill the canvas \u2014 easier to see, and wrong about every angle. The bar at the bottom left of the canvas always says what a length on screen is worth.' },
  'scalebar': { t: 'Scale bar', d: 'The bracket is as long, on the chip, as the distance written beside it, at the zoom you are looking at, and it re-measures as you zoom. It sits under the picture so it never covers the device.' },
  'mode': { t: 'Sketch \u00b7 Parts', d: 'Two ways to design. Sketch: draw the shape the ions travel on \u00b7 a rectangle, a circle, a line, a polyline \u00b7 and the release fills it with trapping sites one lattice unit apart, declares the orbit if the shape closed, and adds a junction where the new rail meets an existing one. Parts: place a site, a junction or a rail one at a time, which is what the course teaches and what detail work needs. Remembered between visits; a canvas with nothing on it opens on Sketch.', k: 'd' },
  'mode:sketch': { t: 'Sketch', d: 'Draw the shape first and fill in the details after: arm a shape, drag it on the canvas, and the release lays the trapping sites along what you drew. The default on a canvas with no device on it.', k: 'd' },
  'mode:parts': { t: 'Parts', d: 'The element-at-a-time tool: press a tile, click the canvas, shift-drag one site onto another for a rail. Everything it could ever do, unchanged.', k: 'd' },
  'shape:rect': { t: 'Rectangle', d: 'Drag corner to corner. The release makes a closed rectangular rail with a trapping site every lattice unit and a site at each corner \u00b7 a corner is a BEND, which R18 prices as ordinary transport, not a junction.', k: 'r' },
  'shape:ellipse': { t: 'Ellipse / circle', d: 'Drag a box; hold shift for a circle. The ring is polygonised at one side per lattice unit, and a circle too small for its rails to subtend the device\u2019s minimum angle is refused by R20 while you are still dragging.', k: 'e' },
  'shape:line': { t: 'Line', d: 'Drag end to end for an open register: trapping sites one lattice unit apart and no orbit to rotate \u00b7 the shape the chain generator makes.', k: 'n' },
  'shape:poly': { t: 'Polyline', d: 'Click point after point. Double-click or press enter to finish it open; click the first point again to close it into an orbit. A corner tighter than the device\u2019s minimum angle is refused by R20 with the angle it would have made.', k: 'p' },
  // The QEC-cycle panel is another session's (qccd/site/qec_cycle.py); it rides into the
  // page through the site build and adds its `data-hint` only once this entry resolves.
  'qec:cycle': { t: 'QEC cycle', d: 'One syndrome-extraction round and the classical loop around it: the outcomes cross a wire to a decoder, and its answer either stays in the classical memory as a Pauli-frame update or comes back to gate an operation. The panel reads this device’s own round time and says whether the decoder keeps up with the rounds and what waiting for its answer costs.' },
  'measure': { t: 'Measure', d: 'A ruler. Click two points and it gives the distance in micrometres, in lattice units, and as a pair of offsets; click a third and it gives the angle at the middle one; click two different rails and it also gives the angle between those rails. Each click snaps to the nearest site or rail. Escape clears it, and pressing the button again leaves the tool.', k: 'm' },
  'undo':   { t: 'Undo', d: 'Take back the last gesture: a drag, a placement, a nudge.', k: 'ctrl+Z' },
  'redo':   { t: 'Redo', d: 'Put back what you just undid.', k: 'ctrl+Y' },
  'help':   { t: 'Guide', d: 'What everything on this screen is, how to use it in three steps, and every gesture and key.', k: '?' },
  'explain': { t: 'Explain', d: 'Label the parts of the screen. Hovering anything already tells you what it is.', k: 'h' },
  'status': { t: 'Now', d: 'Which instruction the animation is on, and what that instruction does.' },
  'price':  { t: 'Price', d: 'What the programme costs on this device: heating (cost), instructions (steps), wall-clock time, and the voltage sources the wiring needs (DACs).' },
  'edits':  { t: 'Edits', d: 'How many changes you have made to the device. Undo takes them back one gesture at a time.' },
  'problems': { t: 'Problems', d: 'Things wrong with the device you have drawn: a geometry rule broken, a site with no zone, a segment with a missing end, a statement the applier refused. Click to list them. Observations about what has NOT been drawn yet are counted separately, as notes.' },
  'notes': { t: 'Notes', d: 'Observations about what is not there yet \u2014 a zone type no site uses, a movement class whose orbit matches no loop. Nothing is wrong; they are here so an empty canvas does not open on a pile of red. Click to read them.' },
  'ctxmenu': { t: 'Element menu', d: 'Everything the page can do to the part you right-clicked: change its capacity, position and zone, place another one, delete it, drop a trapping site into a rail, or take a whole placed component. An action that cannot run here is greyed with the reason rather than hidden.', k: 'right-click a part' },
  'menu:item': { t: 'Menu action', d: 'One thing the page can do to the part under the pointer. Greyed means it cannot run on this device, and the line under it says why.' },
  'menu:modify': { t: 'Modify', d: 'Opens a small panel of this part\u2019s own fields \u2014 a site\u2019s capacity and position, a rail\u2019s length, a loop\u2019s kind \u2014 built from the file format itself, so it can never offer a field the format cannot hold. Enter applies, Escape cancels, and a refusal is shown in the panel beside the field.' },
  'menu:zone': { t: 'Set zone', d: 'Give this trapping site one of the zone types the device declares: how many ions it holds, and whether a gate, a measurement or cooling may happen in it.' },
  'menu:place': { t: 'Place another one of these', d: 'Arms the same kind of element, so the next click on the canvas puts one where you click.' },
  'menu:delete': { t: 'Delete', d: 'Removes the selection. A site takes its rails with it, and one Undo brings the whole thing back.', k: 'del' },
  'menu:insert': { t: 'Insert a trapping site here', d: 'Splits this rail where you clicked and drops a trap into the gap. The two halves inherit the rail\u2019s loop, labels and capacity, its declared length is split at that point, and the new site is spliced into the orbit in the right place \u2014 the only add that can safely put a node on a transport loop.' },
  'menu:component': { t: 'The whole component', d: 'This part was placed as one piece \u2014 a dock, a tile, a register. Select or delete every site and rail it owns, in one step.' },
  'menu:close-loop': { t: 'Close loop', d: 'Declares the selected sites, in the order you selected them, as an orbit the machine can rotate by one step in a single instruction. It needs at least three.' },
  'testdrive': { t: 'Test drive', d: 'Writes a small valid programme for this device \u2014 a rotation if it has a loop, otherwise a shuttle out and back \u2014 and plays it.' },
  'gripRail': { t: 'Elements', d: 'Hide or show the elements rail.', k: '[' },
  'gripDock': { t: 'Panels', d: 'Hide or show the side panels.', k: ']' },
  // the head
  'c:steps': { t: 'Steps', d: 'Instructions executed so far, out of the total. A step is one hardware instruction: a move, a gate, a measurement.' },
  'c:cost':  { t: 'Cost', d: 'Heating spent so far, in motional quanta summed over every ion, out of the programme\u2019s total. Lower is better: a hot ion gates badly.' },
  'm:cost':  { t: 'Cost', d: 'The heating the whole programme puts into the ions, in motional quanta. The number a design is judged by.' },
  'm:steps': { t: 'Steps', d: 'How many hardware instructions the programme takes.' },
  'm:runtime': { t: 'Runtime', d: 'Wall-clock time to run the programme once.' },
  'm:quanta': { t: 'Quanta', d: 'Heating summed over every ion: moves, crossings and waiting.' },
  'm:peak n\u0304': { t: 'Peak n\u0304', d: 'The hottest any single ion gets, in average motional quanta. A gate on an ion above the budget fails rule R7.' },
  'm:peak n-bar': { t: 'Peak n\u0304', d: 'The hottest any single ion gets, in average motional quanta. A gate on an ion above the budget fails rule R7.' },
  'm:contacts': { t: 'Contacts', d: 'How many two-ion gates the programme performs.' },
  'm:cooling': { t: 'Cooling', d: 'Time spent cooling ions back down.' },
  'm:DACs':   { t: 'DACs', d: 'Independent voltage sources the wiring needs. The headline hardware cost: a broadcast scheme keeps it flat as the device grows, a direct scheme pays one per electrode.' },
  'm:junctions': { t: 'Junctions', d: 'Crossings in the device. Every ion that passes one is heated.' },
  'm:junction transits': { t: 'Junction transits', d: 'How many times an ion crosses a junction in the programme.' },
  'm:electrodes': { t: 'Electrodes', d: 'The metal pads that shape the field along every rail.' },
  'm:switches': { t: 'Switches', d: 'Per-site switches that let one voltage source serve many electrodes.' },
  'm:ion capacity': { t: 'Ion capacity', d: 'How many ions the whole device can hold at once.' },
  // the panels
  'tab:P': { t: 'Program', d: 'The hardware programme, one instruction per row. Click a row to jump the animation there.' },
  'tab:Q': { t: 'Circuit', d: 'The quantum circuit this programme was compiled from, stepping in lockstep with the animation.' },
  'tab:A': { t: 'Device', d: 'The device as code: every site, rail and setting as the statements that rebuild it. Edit the source and the picture follows.' },
  'tab:M': { t: 'Machine', d: 'Hardware totals, and which of the rules this page could check.' },
  'tab:W': { t: 'Write', d: 'Write your own test programme \u2014 place ions, shuttle them, rotate a loop \u2014 then press Evaluate to price it and play it.' },
  'menu': { t: 'Panels', d: 'One item per panel. Click to open it beside (or under) the canvas; click the lit item to close it and give the canvas the room back.' },
  'tab:L': { t: 'Learn', d: 'The course: what the machine is, how to build and program it by hand, every rule it must obey, and how to choose an architecture. Small lessons, each with an exercise the page checks.' },
  'learn:pick': { t: 'Lessons', d: 'Jump to any lesson. Stars show what you have passed.' },
  'learn:load': { t: 'Load', d: 'Put this lesson\u2019s device and programme on the canvas. Undo takes you back to what was there.' },
  'learn:check': { t: 'Check', d: 'Test your answer against what the page measures: the frames, the verdicts, the price.' },
  'learn:hint': { t: 'Hint', d: 'Three hints, each deeper. Taking one costs nothing.' },
  'learn:solution': { t: 'Solution', d: 'Show one way to do it, applied to the canvas so you can read it back.' },
  'learn:next': { t: 'Next', d: 'On to the next lesson.' },
  'learn:verdict': { t: 'Python\u2019s verdict', d: 'Five rules are not checked in the browser. For these lessons the page carries the verdict Python computed on this exact programme when the page was built \u2014 the rule, its state, and the verifier\u2019s own sentences.' },
  'p:init': { t: 'p.init', d: 'Puts named ions on named sites: p.init({"d0": "S0"}). The first line of every programme; a device starts empty.' },
  'p:shuttle': { t: 'p.shuttle', d: 'Pushes one ion along a path of site ids, one rail per step: p.shuttle("d0", ["S0", "S1", "S2"]). A third argument names the movement class (default: shuttle).' },
  'p:move': { t: 'p.move', d: 'One ion, one hop: p.move("d0", "S0", "S1"). The same thing p.shuttle does for one rail.' },
  'p:simd': { t: 'p.simd', d: 'Several ions in one step: p.simd("shuttle", [["d0", "S0", "S1"], ["d1", "S2", "S3"]]). The class first, then a list of [ion, from, to]. One instruction, one step, judged as a whole by the rules.' },
  'p:gate': { t: 'p.gate', d: 'A gate. Two-qubit: p.gate("CX", [["d0", "d1"]]) -- both ions in one site, control first. Single-qubit: p.gate("H", [], ["S0"]) names the site.' },
  'p:cool': { t: 'p.cool', d: 'Cools the ions back toward the ground state: p.cool() for all of them, p.cool(["d0"]) for some. Transport heats; a gate on a hot ion is refused (R7, R7c).' },
  'p:measure': { t: 'p.measure', d: 'Reads ions out: p.measure(["d0", "d1"]). Needs a site whose zone has spam (state preparation and measurement).' },
  'p:reset': { t: 'p.reset', d: 'Puts ions back to |0>: p.reset(["d0"]). Same zone requirement as measuring.' },
  'p:fill': { t: 'p.fill', d: 'Puts one ion on every site of a loop, d0 on its first site and so on: p.fill() for the only loop, p.fill("L0") to name it.' },
  'p:rotate': { t: 'p.rotate', d: 'Turns a closed loop: p.rotate(2) moves every ion on it two sites forward, p.rotate(-1) one site back. One instruction, every ion.' },
  'p:barrier': { t: 'p.barrier', d: 'A step in which nothing happens; a marker between phases of a programme.' },
  'p:decode': { t: 'p.decode', d: 'Calls the decoder: p.decode() sends the outcomes of every ion measured since the last decode down the wires to the decoder, and its correction on to the classical memory. p.decode(["a0"]) names them. It moves no ion and takes no machine time -- the decoder works alongside the ions -- and the wires light while it runs.' },
  'tab:R': { t: 'Report', d: 'The verdict: every cost figure with its provenance, and each of the 27 hardware rules \u2014 passed, failed, or not checkable here.' },
  'follow': { t: 'Follow', d: 'Keep the executing instruction scrolled into view.', k: 'F' },
  'filter': { t: 'Filter', d: 'Show only the rows containing this text.', k: '/' },
  'evaluate': { t: 'Evaluate', d: 'Price the programme, check it against the rules, and play it.' },
  'archview': { t: 'View', d: 'Program: the statements that build the device \u00b7 Device: every node and rail \u00b7 Source: edit it as text.' },
  // the legend
  'leg:segment': { t: 'Rail', d: 'A segment an ion can travel along.' },
  'leg:site':    { t: 'Trapping site', d: 'Where ions sit; the ticks are its capacity.' },
  'leg:junction': { t: 'Junction', d: 'A crossing. Ions pass through and never rest here.' },
  'leg:slot':    { t: 'Free slot', d: 'Room for one more ion in a site.' },
  'leg:ion':     { t: 'Ion', d: 'One ion. When it moves, the pads under it light up.' },
  'leg:gate':    { t: 'Ion in a gate', d: 'Two ions in the same site being gated together.' },
  'leg:pad':     { t: 'Energized electrode', d: 'A pad carrying the potential well that is pushing an ion along.' },
  'leg:well':    { t: 'Moving well', d: 'The potential well in flight along a rail.' },
  'leg:loop':    { t: 'Transport loop', d: 'A ring the machine can rotate as one.' },
  'leg:zone':    { t: 'Site colour', d: 'Sites are coloured by their zone type.' }
};
// THE 27 RULES IN PLAIN WORDS.  The formal statement each badge carries as its title is
// the contract (`docs/rules.md`); this is what it means to someone who has not read it.
var RULE_HINTS = {
  R1: 'No site ever holds more ions than its capacity.',
  R2: 'A junction holds at most one ion, and at most one ion crosses it per step.',
  R3: 'No more ions travel one rail in a step than it is rated for.',
  R4: 'Only declared kinds of motion, and no more of them per step than the control plane can drive at once.',
  R4b: 'A step is either transport or gates, never both.',
  R4d: 'Everything sharing one control channel moves the same way in a step.',
  R5: 'Two ions never pass each other on one rail in the same step.',
  R6: 'A gate, a measurement or cooling only happens in a zone that allows it.',
  R6b: 'Both ions of a gate are in the same site.',
  R7: 'An ion is cool enough (n\u0304 under budget) when it enters a gate.',
  R7b: 'Per-zone duty-cycle budgets \u2014 no device declares one yet.',
  R7c: 'A programme with gates, under a heating model, schedules some cooling.',
  R8: 'Ions are neither created nor lost, and none moves twice in one step.',
  R9: 'The totals the programme claims match what the replay measures.',
  R10: 'The compiled programme implements the circuit it came from.',
  R11: 'A loop rotates one way per step, and every junction has a price.',
  R12: 'At most one gate per site per step.',
  R13: 'At most 15 ions in a site when a gate fires.',
  R14: 'Splitting a chain longer than two is accounted as a swap.',
  R15: 'Heating adds up (the replay counts an upper bound).',
  R16: 'Gate error is read off the ion\u2019s actual heating, not a constant.',
  R17: 'Ions heat up just by waiting.',
  R18: 'A junction is charged by how many rails meet there.',
  R19: 'No more rails meet at one node than the device says a junction can join.',
  R20: 'Two rails leaving the same node are far enough apart in angle to be built.',
  R21: 'The drawing is flat: a rail touches only the nodes it ends at, and two rails cross only where they share a node.',
  R22: 'Every ion that moves in one step moves the same way; a site may sit the step out, but it may not do something else.'
};

// The entry a key names, resolved: `el:cmp:*` is a component, `rule:*` reads the rule
// table, and a key with no entry is no hint (never a blank card).
function hintFor(key) {
  key = String(key || '');
  if (!key) return null;
  if (key.slice(0, 7) === 'el:cmp:') {
    var cn = key.slice(7), spec = componentSpec(cn), base = HINTS['el:component'];
    return { t: cn.replace(/_/g, ' '), d: (spec && spec.blurb ? spec.blurb + ' ' : '') + base.d, k: base.k };
  }
  if (key.slice(0, 5) === 'rule:') {
    var r = key.slice(5);
    return RULE_HINTS[r] ? { t: 'Rule ' + r, d: RULE_HINTS[r] } : null;
  }
  if (key.slice(0, 3) === 'el:' && HINTS[key]) {
    var e = HINTS[key], doc = docOf(key.slice(3));
    // the plain sentence first; the schema's own words under it, for the physics
    return { t: e.t, d: e.d, k: e.k, more: doc && doc.blurb && doc.blurb !== e.d ? doc.blurb : '' };
  }
  return HINTS[key] || null;
}
// The element a hover or a focus belongs to: the nearest ancestor carrying `data-hint`,
// or the stage itself, which draws its own hint from the hit and must not be hidden by
// the pointer crossing into one of its thousands of marks.  No `closest`: the shim has
// none, and six levels is every control on this page.
function hintOwner(el) {
  for (var i = 0; el && i < 8; i++) {
    if (el.getAttribute && el.getAttribute('data-hint')) return el;
    if (el.getAttribute && el.getAttribute('id') === 'svg') return el;
    el = el.parentNode;
  }
  return null;
}
function hintFrom(el, cx, cy) {
  var owner = hintOwner(el);
  if (!owner) { hintHide(); return null; }
  if (owner.getAttribute('id') === 'svg') return null;
  var key = owner.getAttribute('data-hint');
  var entry = hintFor(key);
  if (!entry) { hintHide(); return null; }
  var x = cx, y = cy;
  if (x === undefined && owner.getBoundingClientRect) {
    var r = owner.getBoundingClientRect();
    x = r.left; y = r.bottom;
  }
  hintShow(entry, x, y);
  return key;
}
// The card at (x, y), flipped away from whichever window edge it would otherwise leave.
// Text only, never markup: a component's blurb and an id typed by the user end up here.
function hintShow(entry, x, y) {
  var card = $('hint');
  if (!card) return;
  var T = $('hintT'), Dd = $('hintD'), K = $('hintK');
  if (T) T.textContent = entry.t || '';
  if (Dd) Dd.textContent = (entry.d || '') + (entry.more ? '  \u2014  ' + entry.more : '');
  if (K) K.textContent = entry.k || '';
  var iw = (typeof innerWidth === 'number' && innerWidth > 0) ? innerWidth : 1600;
  var ih = (typeof innerHeight === 'number' && innerHeight > 0) ? innerHeight : 1000;
  x = (x === undefined ? 0 : x); y = (y === undefined ? 0 : y);
  var flipX = x > iw - 340, flipY = y > ih - 140;
  if (card.style) {
    card.style.display = '';
    card.style.left = flipX ? 'auto' : (x + 14) + 'px';
    card.style.right = flipX ? (iw - x + 10) + 'px' : 'auto';
    card.style.top = flipY ? 'auto' : (y + 16) + 'px';
    card.style.bottom = flipY ? (ih - y + 10) + 'px' : 'auto';
  }
  card.setAttribute('data-on', '1');
}
function hintHide() {
  var card = $('hint');
  if (!card) return;
  if (card.style) card.style.display = 'none';
  card.setAttribute('data-on', '0');
}
// WHAT IS UNDER THE POINTER, in words: the kind, the id, and the facts a newcomer wants --
// what zone it is, how many ions fit, how many are there right now (read off the replay
// at the current frame).  Nothing while a drag or a placement is in progress: the HUD
// speaks then.
function stageHint(h, cx, cy) {
  if (!h || DOWN || GHOST || PGHOST || BAND) { hintHide(); return null; }
  var entry = null, dev = STATE && STATE.device;
  if (h.kind === 'site' || h.kind === 'junction') {
    // the page's own node record: the capacity and the degree the picture was drawn with
    var n = nodeById[h.id], base = HINTS[n && n.kind === 'junction' ? 'el:junction' : 'el:site'];
    if (!n) { hintHide(); return null; }
    var here = 0;
    if (typeof states !== 'undefined' && typeof frame !== 'undefined' && states[frame] && states[frame].pos) {
      var pos = states[frame].pos, ion;
      for (ion in pos) if (has(pos, ion) && pos[ion] === h.id) here++;
    }
    var deg = n.deg || 0, cap = n.cap || 0;
    entry = n.kind === 'junction'
      ? { t: base.t + ' ' + h.id, d: deg + ' rail' + (deg === 1 ? '' : 's') + ' meet here. Ions pass through and never rest in it.' }
      : { t: base.t + ' ' + h.id, d: 'zone ' + (n.zone || '—') + ' · holds up to ' + cap +
             ' ion' + (cap === 1 ? '' : 's') + ' · ' + here + ' here now · ' + deg + ' rail' + (deg === 1 ? '' : 's') + ' attached',
          k: 'drag to move · shift-drag onto another site to join' };
  } else if (h.kind === 'segment') {
    var sg = dev && dev.segments[h.id];
    if (!sg) { hintHide(); return null; }
    entry = { t: 'Segment ' + h.id, d: sg.a + ' ↔ ' + sg.b + ' · length ' + fmt(sg.length, 2) +
             (sg.loop ? ' · part of loop ' + sg.loop : ''), k: 'drag to move both ends' };
  } else if (h.kind === 'loop') {
    var lp = dev && dev.loops[h.id];
    if (!lp) { hintHide(); return null; }
    entry = { t: 'Loop ' + h.id, d: lp.nodes.length + ' sites in a ring. One instruction rotates every ion on it by one step.', k: 'drag to move the whole ring' };
  } else { hintHide(); return null; }
  hintShow(entry, cx, cy);
  return entry;
}

// EXPLAIN MODE labels the regions.  The state is an attribute on <body>, which the
// stylesheet keys on and a harness can read; the captions are rendered once from HINTS.
var EXPLAIN = false;
function explainToggle(on) {
  EXPLAIN = (on === undefined) ? !EXPLAIN : !!on;
  if (document.body && document.body.setAttribute) document.body.setAttribute('data-explain', EXPLAIN ? '1' : '0');
  var b = $('eExplain');
  if (b) b.setAttribute('aria-pressed', EXPLAIN ? 'true' : 'false');
  // the legend is part of the explanation: open it with the captions, leave it as found
  var lf = $('legendFold');
  if (lf && EXPLAIN && lf.setAttribute) lf.setAttribute('open', 'open');
  return EXPLAIN;
}
function renderCaptions() {
  var ids = { capTools: 'region:tools', capRail: 'region:rail', capStage: 'region:stage', capBar: 'region:bar', capProg: 'region:prog', capDock: 'region:dock' };
  for (var id in ids) if (has(ids, id)) {
    var el = $(id), e = HINTS[ids[id]];
    if (!el || !e) continue;
    el.replaceChildren();
    var b = elh('b'); b.textContent = e.t; el.append(b);
    var t = elh('span'); t.textContent = e.d; el.append(t);
  }
}

// THE RAIL FOLDS ITS EXPERT SECTIONS AWAY.  Four tiles and the zone chips are what a
// first device needs; the physics settings and the whole-part components are there, one
// click down, with a count on the fold so nothing looks missing.  The fold state is the
// user's for the life of the page -- `renderPalette` runs on every paint and would
// otherwise snap an opened fold shut on the next drag.
var PAL_OPEN = { row: false, block: false, component: false };
function foldSection(kind, node, count) {
  var det = elh('details', 'palfold');
  det.setAttribute('data-fold', kind);
  if (PAL_OPEN[kind]) det.setAttribute('open', 'open');
  var sm = elh('summary');
  sm.textContent = (SECTION[kind] || kind) + (count ? ' \u00b7 ' + count : '');
  det.append(sm, node);
  if (det.addEventListener) det.addEventListener('toggle', function () { setFold(kind, !!det.open); });
  return det;
}
function setFold(kind, open) { PAL_OPEN[kind] = !!open; return PAL_OPEN[kind]; }

// ------------------------------------------------------------------- the course
//
// LESSONS ARE DATA; THE ENGINE IS SMALL.  `tutorial.js` registers a table of lessons after
// this file has booted.  A lesson's SETUP is a list of editor verbs applied in order -- the
// same verbs the pointer adapters call -- so loading a lesson is indistinguishable from a
// user building the device by hand and can be undone the same way.  Its CHECK is an
// expression over the page's own measurements (`[name, ...args]`, evaluated by `CHECKS`),
// never a second reading of any rule or price.  What a pass says is generated from the
// facts the check gathered, so praise is always true and always specific; what a failure
// says is a nudge keyed to the predicate that failed, never a mark.
var COURSE = null;
var COURSE_KEY = 'qccd.studio.tutorial';
var LSTATE = { id: null, stage: 0, attempts: 0, hints: 0, recorded: {}, feedback: null,
               answer: null, passed: false, shown: false };

function courseProgress() {
  var raw = null;
  try { raw = STORE.getItem(COURSE_KEY); } catch (err) { raw = null; }
  var p = null;
  try { p = raw ? JSON.parse(raw) : null; } catch (err) { p = null; }
  if (!p || typeof p !== 'object') p = {};
  if (!p.stars) p.stars = {};
  if (!p.best) p.best = {};
  return p;
}
function courseSave(p) { try { STORE.setItem(COURSE_KEY, JSON.stringify(p)); } catch (err) { /* no store */ } }

function lessonsReady(t) {
  if (!t || !t.lessons || !t.lessons.length) return 0;
  COURSE = t;
  var prog = courseProgress();
  if (prog.current && lessonById(prog.current)) LSTATE.id = prog.current;
  renderLearn();
  renderLessonStrip();
  return t.lessons.length;
}
function lessonById(id) {
  if (!COURSE) return null;
  for (var i = 0; i < COURSE.lessons.length; i++) if (COURSE.lessons[i].id === id) return COURSE.lessons[i];
  return null;
}
function lessonIndex(id) {
  if (!COURSE) return -1;
  for (var i = 0; i < COURSE.lessons.length; i++) if (COURSE.lessons[i].id === id) return i;
  return -1;
}
function partById(id) {
  if (!COURSE) return null;
  for (var i = 0; i < COURSE.parts.length; i++) if (COURSE.parts[i].id === id) return COURSE.parts[i];
  return null;
}
function lessonList() {
  if (!COURSE) return [];
  var prog = courseProgress();
  return COURSE.lessons.map(function (L) {
    return { id: L.id, part: L.part, title: L.title, stars: prog.stars[L.id] || 0,
             breaks: L.breaks || null, page: L.page || null,
             stageBreaks: L.stages ? L.stages.map(function (s) { return s.breaks || null; }) : null,
             verdict: !!(L.verdict || (L.stages || []).some(function (s) { return !!s.verdict; })) };
  });
}
function lessonState() {
  return { id: LSTATE.id, stage: LSTATE.stage, attempts: LSTATE.attempts, hints: LSTATE.hints,
           passed: LSTATE.passed, shown: LSTATE.shown, feedback: LSTATE.feedback,
           stars: LSTATE.id ? (courseProgress().stars[LSTATE.id] || 0) : 0 };
}

// A setup or a solution is a list of `[verb, ...args]`; each verb is a function on the
// API, so there is no second vocabulary to keep in step with the pointer adapters.
function runSteps(steps) {
  steps = steps || [];
  for (var i = 0; i < steps.length; i++) {
    var st = steps[i], verb = st[0], f = API[verb];
    if (typeof f !== 'function') {
      return { ok: false, problems: [{ code: 'no_verb', message: 'the lesson names a verb this page does not have: ' + verb }] };
    }
    var r;
    try { r = f.apply(null, st.slice(1)); }
    catch (err) { return { ok: false, problems: [{ code: 'threw', message: verb + ' threw: ' + (err && err.message ? err.message : err) }] }; }
    if (r && r.ok === false) {
      return { ok: false, problems: [{ code: 'refused', message: verb + ': ' + (((r.problems || [])[0] || {}).message || 'refused') }] };
    }
  }
  return { ok: true };
}
// the facts the lesson may refer back to ("cost fell from {cost0}")
function courseFacts() {
  var f = { sites: 0, junctions: 0, segments: 0, loops: 0, frames: P.frames.length, edits: EDITS.length };
  if (STATE && STATE.device) {
    var id;
    for (id in STATE.device.nodes) if (has(STATE.device.nodes, id)) {
      if (STATE.device.nodes[id].kind === 'junction') f.junctions++; else f.sites++;
    }
    f.segments = Object.keys(STATE.device.segments || {}).length;
    f.loops = Object.keys(STATE.device.loops || {}).length;
  }
  if (PRICE && !PRICE.blocked) {
    f.cost = PRICE.totals.cost; f.steps = PRICE.totals.steps; f.us = PRICE.totals.us;
    f.transits = PRICE.transits; f.peak = PRICE.peak;
  }
  if (HW) { f.dacs = HW.dacs; f.scheme = HW.scheme; f.electrodes = HW.electrodes; f.switches = HW.switches; }
  if (typeof frame === 'number') f.frame = frame;
  return f;
}

function lessonLoad(id) {
  var L = lessonById(id);
  if (!L) return { ok: false, problems: [{ code: 'no_lesson', message: 'no lesson ' + id }] };
  if (GHOST) cancel();
  if (ARMED_EL) arm(null);
  if (FORM) openForm(null);
  LSTATE = { id: id, stage: 0, attempts: 0, hints: 0, recorded: {}, feedback: null,
             answer: null, passed: false, shown: false };
  var r = runSteps(L.setup);
  if (r.ok) r = enterStage(L, 0);
  if (!r.ok) {
    LSTATE.feedback = { kind: 'bad', text: 'this lesson could not be set up: ' + r.problems[0].message };
    renderLearn();
    return r;
  }
  LSTATE.recorded = courseFacts();
  var prog = courseProgress();
  prog.current = id;
  courseSave(prog);
  setSelection([]);
  renderLearn();
  renderLessonStrip();
  return { ok: true, id: id, stages: L.stages ? L.stages.length : 1 };
}
function loadCase(id, which) {
  var c = (D.tutorial_cases || {})[id];
  if (!c) return { ok: false, problems: [{ code: 'no_case', message: 'this page carries no prepared case ' + id }] };
  var r = newFromGenerator(c.device.generator, c.device.params, { name: c.device.name });
  if (!r.ok) return r;
  setProgram((which === 'fixed' ? c.fixed : c.program) || []);
  return { ok: true, id: id, which: which === 'fixed' ? 'fixed' : 'program' };
}
function shippedVerdict(id, which) {
  var v = (D.tutorial_verdicts || {})[id];
  return v ? (v[which || 'loaded'] || null) : null;
}
function verdictState(summary, rule) {
  summary = summary || {};
  if ((summary.failed || []).indexOf(rule) >= 0) return 'failed';
  if (has(summary.partial || {}, rule)) return 'partial';
  if (has(summary.skipped || {}, rule)) return 'skipped';
  if ((summary.passed || []).indexOf(rule) >= 0) return 'passed';
  return 'unknown';
}
// a stage may load its own device and programme; entering it runs that setup
function enterStage(L, k) {
  LSTATE.stage = k;
  var st = L && L.stages ? L.stages[k] : null;
  if (st && st.setup) {
    var r = runSteps(st.setup);
    if (!r.ok) return r;
    setSelection([]);
  }
  return { ok: true };
}
function lessonTask(L) {
  if (!L) return null;
  if (L.stages) return L.stages[Math.min(LSTATE.stage, L.stages.length - 1)];
  return { exercise: L.exercise, check: L.check };
}
function lessonTotalStages(L) { return L && L.stages ? L.stages.length : 1; }

// ---- the check vocabulary -----------------------------------------------------------
// Each predicate returns {ok, why, facts}; `why` names what is missing in the user's
// terms and `facts` is what a pass may quote.  `all`/`any`/`not` compose them.
function lastPos() {
  if (typeof states === 'undefined' || !states.length) return null;
  return states[states.length - 1].pos || null;
}
function rec(key) { return LSTATE.recorded ? LSTATE.recorded[key] : undefined; }
function num(v, key) { return v === 'setup' ? rec(key) : Number(v); }
var CHECKS = {
  all: function () {
    var facts = {}, i;
    for (i = 0; i < arguments.length; i++) {
      var r = evalCheck(arguments[i]);
      var k; for (k in r.facts) if (has(r.facts, k)) facts[k] = r.facts[k];
      if (!r.ok) { r.facts = facts; return r; }
    }
    return { ok: true, facts: facts };
  },
  any: function () {
    var last = null;
    for (var i = 0; i < arguments.length; i++) { var r = evalCheck(arguments[i]); if (r.ok) return r; last = r; }
    return last || { ok: false, why: 'nothing to check', facts: {} };
  },
  not: function (spec) { var r = evalCheck(spec); return { ok: !r.ok, why: r.ok ? 'that is still true' : '', pred: r.pred, facts: r.facts }; },
  sites: function (n) { var f = courseFacts(); return { ok: f.sites === n, why: 'sites', have: f.sites, facts: { sites: f.sites } }; },
  junctions: function (n) { var f = courseFacts(); return { ok: f.junctions === n, why: 'junctions', have: f.junctions, facts: { junctions: f.junctions } }; },
  segments: function (n) { var f = courseFacts(); return { ok: f.segments === n, why: 'rails', have: f.segments, facts: { segments: f.segments } }; },
  loops: function (n, closed) {
    var lp = (STATE && STATE.device && STATE.device.loops) || {}, k = 0, id;
    for (id in lp) if (has(lp, id) && (closed === undefined || !!lp[id].closed === !!closed)) k++;
    return { ok: k === n, why: 'loops', have: k, facts: { loops: k } };
  },
  zoneCapacity: function (z, n) {
    var zt = (STATE && STATE.zone_types) || {}, have = zt[z] ? zt[z].capacity : null;
    return { ok: have === n, why: 'zone capacity', have: have, facts: { cap: have, zone: z } };
  },
  anyNodeZone: function (z) {
    var ns = (STATE && STATE.device && STATE.device.nodes) || {}, id;
    for (id in ns) if (has(ns, id) && ns[id].zone === z) return { ok: true, facts: { zone: z } };
    return { ok: false, why: 'no site in zone ' + z, facts: {} };
  },
  wiring: function (scheme) { return { ok: !!HW && HW.scheme === scheme, why: 'wiring', have: HW ? HW.scheme : null, facts: { scheme: HW ? HW.scheme : null } }; },
  dacsAbove: function (v) { var d = HW ? HW.dacs : null, ref = num(v, 'dacs'); return { ok: d !== null && d > ref, why: 'DACs', have: d, facts: { dacs: d, dacs0: ref } }; },
  dacsBelow: function (v) { var d = HW ? HW.dacs : null, ref = num(v, 'dacs'); return { ok: d !== null && d < ref, why: 'DACs', have: d, facts: { dacs: d, dacs0: ref } }; },
  dacsSame: function (v) { var d = HW ? HW.dacs : null, ref = num(v, 'dacs'); return { ok: d !== null && d === ref, why: 'DACs moved', have: d, facts: { dacs: d, dacs0: ref } }; },
  electrodesAbove: function (v) { var e = HW ? HW.electrodes : null, ref = num(v, 'electrodes'); return { ok: e !== null && e > ref, why: 'electrodes', have: e, facts: { electrodes: e, electrodes0: ref } }; },
  selected: function (want) {
    var s = SELSET.length ? SELSET[0] : null;
    if (!s) return { ok: false, why: 'nothing selected', have: null, facts: {} };
    var n = nodeById[s.id];
    if (want.id !== undefined) return { ok: s.id === want.id, why: 'wrong element', have: s.id, facts: { selected: s.id } };
    if (want.kind === 'junction') return { ok: !!n && n.kind === 'junction', why: 'not a junction', have: s.id, facts: { selected: s.id } };
    if (want.kind === 'site') return { ok: !!n && n.kind !== 'junction', why: 'not a site', have: s.id, facts: { selected: s.id } };
    return { ok: s.kind === want.kind, why: 'wrong kind', have: s.kind, facts: { selected: s.id } };
  },
  frames: function (min, max) { var n = P.frames.length; return { ok: n >= min && (max === undefined || n <= max), why: 'instructions', have: n, facts: { frames: n } }; },
  ionAt: function (ion, site) { var pos = lastPos(); var at = pos ? pos[ion] : null; return { ok: at === site, why: 'where ' + ion + ' ends', have: at, facts: { ion: ion, at: at } }; },
  ionsPresent: function (ions) {
    var pos = lastPos() || {}, missing = [];
    for (var i = 0; i < ions.length; i++) if (!has(pos, ions[i])) missing.push(ions[i]);
    return { ok: !missing.length, why: 'missing ions', have: missing.join(', '), facts: {} };
  },
  occupancy: function (site, n) {
    var pos = lastPos() || {}, k = 0, ion;
    for (ion in pos) if (has(pos, ion) && pos[ion] === site) k++;
    return { ok: k === n, why: 'ions in ' + site, have: k, facts: { occupancy: k } };
  },
  gateBetween: function (a, b, name) {
    for (var i = 0; i < P.frames.length; i++) {
      var f = P.frames[i];
      if (f.type !== 'gate' || (name && f.gate !== name)) continue;
      var pp = f.pairs || [];
      for (var j = 0; j < pp.length; j++) {
        if ((pp[j][0] === a && pp[j][1] === b) || (pp[j][0] === b && pp[j][1] === a)) return { ok: true, facts: { gate: f.gate, pair: a + ',' + b, gateFrame: i } };
      }
    }
    return { ok: false, why: 'no ' + (name || 'gate') + ' between ' + a + ' and ' + b, facts: {} };
  },
  measured: function (ions) {
    var seen = {}, i, j;
    for (i = 0; i < P.frames.length; i++) if (P.frames[i].type === 'measure') for (j = 0; j < (P.frames[i].ions || []).length; j++) seen[P.frames[i].ions[j]] = 1;
    var missing = ions.filter(function (x) { return !seen[x]; });
    return { ok: !missing.length, why: 'not measured', have: missing.join(', '), facts: {} };
  },
  cooled: function () { for (var i = 0; i < P.frames.length; i++) if (P.frames[i].type === 'cool') return { ok: true, facts: { coolFrame: i } }; return { ok: false, why: 'no cooling', facts: {} }; },
  lowered: function () {
    var errs = lowerErrors();
    if (PARSE_ERRS.length) return { ok: false, why: 'the programme does not parse', have: PARSE_ERRS[0].message, facts: {} };
    if (errs.length) return { ok: false, why: 'the programme was refused', have: errs[0].message, facts: {} };
    if (!P.frames.length) return { ok: false, why: 'no instructions', have: 0, facts: {} };
    return { ok: true, facts: { frames: P.frames.length } };
  },
  rulesPass: function () {
    var low = CHECKS.lowered(); if (!low.ok) return low;
    if (PRICE && PRICE.blocked) return { ok: false, why: 'the price is blocked', have: (PRICE.blocked[0] || {}).message, facts: {} };
    var cov = ruleCoverage(), bad = cov.filter(function (c) { return c.state === 'failed'; });
    if (bad.length) {
      var m = ((RULES && RULES.messages) || []).filter(function (v) { return v.rule === bad[0].rule; })[0];
      return { ok: false, why: bad[0].rule + ' fails', have: m ? m.message : bad[0].statement, rule: bad[0].rule, facts: {} };
    }
    var f = courseFacts();
    return { ok: true, facts: { cost: f.cost, steps: f.steps, frames: f.frames, rulesChecked: cov.filter(function (c) { return c.state === 'checked'; }).length } };
  },
  ruleFails: function (r) {
    var cov = ruleCoverage().filter(function (c) { return c.rule === r; })[0];
    return { ok: !!cov && cov.state === 'failed', why: r + ' does not fail', have: cov ? cov.state : 'unknown', facts: { rule: r } };
  },
  ruleState: function (r, state) {
    var cov = ruleCoverage().filter(function (c) { return c.rule === r; })[0];
    return { ok: !!cov && cov.state === state, why: r + ' is ' + (cov ? cov.state : 'unknown'), have: cov ? cov.state : null, facts: { rule: r } };
  },
  instructionsAtMost: function (n) { return { ok: P.frames.length <= n, why: 'too many instructions', have: P.frames.length, facts: { frames: P.frames.length } }; },
  stepsAtMost: function (n) { var f = courseFacts(); return { ok: f.steps !== undefined && f.steps <= n, why: 'too many steps', have: f.steps, facts: { steps: f.steps } }; },
  costAtMost: function (x) { var f = courseFacts(); return { ok: f.cost !== undefined && f.cost <= x, why: 'too costly', have: f.cost, facts: { cost: f.cost } }; },
  costBelow: function (v) { var f = courseFacts(), ref = num(v, 'cost'); return { ok: f.cost !== undefined && ref !== undefined && f.cost < ref, why: 'cost', have: f.cost, facts: { cost: f.cost, cost0: ref } }; },
  transitsAtMost: function (n) { var f = courseFacts(); return { ok: f.transits !== undefined && f.transits <= n, why: 'junction transits', have: f.transits, facts: { transits: f.transits } }; },
  runtimeAtMost: function (us) { var f = courseFacts(); return { ok: f.us !== undefined && f.us <= us, why: 'runtime', have: f.us, facts: { us: f.us } }; },
  runtimeBelow: function (v) { var f = courseFacts(), ref = num(v, 'us'); return { ok: f.us !== undefined && ref !== undefined && f.us < ref, why: 'runtime', have: f.us, facts: { us: f.us, us0: ref } }; },
  peakAbove: function (v) { var f = courseFacts(), ref = num(v, 'peak'); return { ok: f.peak !== undefined && ref !== undefined && f.peak > ref, why: 'peak heating', have: f.peak, facts: { peak: f.peak, peak0: ref } }; },
  editsAtMost: function (n) { return { ok: EDITS.length <= n, why: 'gestures', have: EDITS.length, facts: { edits: EDITS.length } }; },
  frameIs: function (which) {
    var want = frameFor(which);
    if (want < 0 && typeof which === 'string' && which.slice(0, 9) === 'realises:') {
      return { ok: false, why: 'this lesson runs on the compiled page', have: D.source ? 'no instruction realises that statement' : 'not a compiled page', facts: {} };
    }
    var now = (typeof frame === 'number') ? frame : -1;
    var cost = (PRICE && PRICE.perFrame && PRICE.perFrame[now]) ? PRICE.perFrame[now][0] : undefined;
    return { ok: now === want, why: 'instruction', have: now, facts: { frame: now, cost: cost } };
  },
  answer: function (x) { return { ok: LSTATE.answer === x, why: 'answer', have: LSTATE.answer, facts: { answer: LSTATE.answer } }; },
  // every ion that started on `loop` ends `delta` sites along it (mod the loop's length)
  rotated: function (loop, delta) {
    var lp = (STATE && STATE.device && STATE.device.loops) ? STATE.device.loops[loop] : null;
    var f0 = P.frames[0], pos = lastPos();
    if (!lp || !f0 || f0.type !== 'init' || !pos) return { ok: false, why: 'the loop has not turned', have: 'no ions on it', facts: {} };
    var seq = lp.nodes, k = seq.length, idx = {}, i, n = 0, bad = null, ion;
    for (i = 0; i < k; i++) idx[seq[i]] = i;
    var want = ((delta % k) + k) % k;
    for (ion in f0.place) if (has(f0.place, ion) && idx[f0.place[ion]] !== undefined) {
      n++;
      var to = pos[ion];
      if (idx[to] === undefined || (((idx[to] - idx[f0.place[ion]]) % k) + k) % k !== want) { if (!bad) bad = ion + ' at ' + to; }
    }
    return { ok: n > 0 && !bad, why: 'the loop has not turned by ' + delta, have: bad || 'no ions on it', facts: { loop: loop, delta: delta, ions: n } };
  },
  // a gate named `name` fired at `site`: declared on the instruction, or where its ions sat
  gateAt: function (name, site) {
    for (var i = 0; i < P.frames.length; i++) {
      var f = P.frames[i];
      if (f.type !== 'gate' || f.gate !== name) continue;
      if ((f.sites || []).indexOf(site) >= 0) return { ok: true, facts: { gate: name, site: site } };
      var pos = (typeof states !== 'undefined' && states[i]) ? (states[i].pos || {}) : {}, pp = f.pairs || [];
      for (var j = 0; j < pp.length; j++) if (pos[pp[j][0]] === site || pos[pp[j][1]] === site) return { ok: true, facts: { gate: name, site: site } };
    }
    return { ok: false, why: 'no ' + name + ' at ' + site, facts: {} };
  },
  gateCount: function (name, min) {
    var n = 0;
    for (var i = 0; i < P.frames.length; i++) if (P.frames[i].type === 'gate' && P.frames[i].gate === name) n++;
    return { ok: n >= min, why: name + ' gates', have: n, facts: { gates: n } };
  },
  hasFrame: function (type) {
    for (var i = 0; i < P.frames.length; i++) if (P.frames[i].type === type) return { ok: true, facts: {} };
    return { ok: false, why: 'no ' + type + ' instruction', facts: {} };
  },
  classUsed: function (cls) {
    for (var i = 0; i < P.frames.length; i++) if (P.frames[i].type === 'simd' && P.frames[i].cls === cls) return { ok: true, facts: { cls: cls } };
    return { ok: false, why: 'no move of class ' + cls, facts: {} };
  },
  // Python's verdict, shipped with the page for a prepared case
  shipped: function (id, which, rule, state) {
    var v = shippedVerdict(id, which);
    if (!v) return { ok: false, why: 'no shipped verdict for ' + id, have: null, facts: {} };
    var s = (v.focus && v.focus.rule === rule) ? v.focus.state : verdictState(v.rules, rule);
    return { ok: s === state, why: rule + ' is ' + s + ' in Python\u2019s verdict', have: s,
             facts: { rule: rule, state: s, gateError: v.metrics ? v.metrics.gate_error_sum : undefined } };
  },
  // the price is withheld: the programme asks for something the device cannot price
  priceBlocked: function () {
    var b = (PRICE && PRICE.blocked && PRICE.blocked.length) ? PRICE.blocked[0] : null;
    return { ok: !!b, why: 'the price is not blocked', have: b ? (b.message || breakMessage(b)) : null,
             facts: { blocked: b ? (b.kind || b.code || '') : '' } };
  },
  // the language refused a statement, with a message naming `sub`
  refused: function (sub) {
    var msgs = PARSE_ERRS.concat(lowerErrors()).map(function (e) { return String((e && e.message) || ''); });
    var hit = msgs.filter(function (m) { return m.indexOf(sub) >= 0; });
    return { ok: hit.length > 0, why: 'nothing refused naming ' + sub, have: msgs[0] || 'nothing refused',
             facts: { refusal: hit[0] || '' } };
  },
  // the programme claims `key` = `value` (R9's subject)
  claimIs: function (key, value) {
    var have;
    for (var i = 0; i < PROG.length; i++) if (PROG[i].method === 'claim' && PROG[i].kwargs && has(PROG[i].kwargs, key)) have = PROG[i].kwargs[key];
    return { ok: have !== undefined && Number(have) === Number(value), why: 'the claim for ' + key,
             have: have === undefined ? 'no claim' : have, facts: { claimKey: key, claim: have } };
  },
  // Part D: the answer names the device the measured table says is smaller on `metric`
  measuredLess: function (kind, metric, a, b) {
    var ra = measuredRow(kind, a), rb = measuredRow(kind, b);
    if (!ra || !rb) return { ok: false, why: 'no measured result for ' + (ra ? b : a), have: null, facts: {} };
    var win = Number(ra[metric]) < Number(rb[metric]) ? a : Number(rb[metric]) < Number(ra[metric]) ? b : null;
    return { ok: !!win && LSTATE.answer === win, why: 'answer', have: LSTATE.answer,
             facts: { winner: win, a: a, b: b, va: ra[metric], vb: rb[metric] } };
  },
  measuredSame: function (kind, metric, a, b) {
    var ra = measuredRow(kind, a), rb = measuredRow(kind, b);
    if (!ra || !rb) return { ok: false, why: 'no measured result for ' + (ra ? b : a), have: null, facts: {} };
    return { ok: Number(ra[metric]) === Number(rb[metric]), why: metric + ' differs', have: ra[metric] + ' vs ' + rb[metric],
             facts: { va: ra[metric], vb: rb[metric] } };
  },
  dacsAtMost: function (n) { var d = HW ? HW.dacs : null; return { ok: d !== null && d <= n, why: 'DACs', have: d, facts: { dacs: d } }; },
  broadcastCool: function () { for (var i = 0; i < P.frames.length; i++) if (P.frames[i].type === 'cool' && P.frames[i].broadcast) return { ok: true, why: 'a cooling pulse is broadcast to every ion', have: 'broadcast', facts: {} }; return { ok: false, why: 'no broadcast cooling', have: 'targeted', facts: {} }; },
  // the replay put `ion` on `site` at some point
  visited: function (ion, site) {
    if (typeof states === 'undefined') return { ok: false, why: 'no replay', facts: {} };
    for (var i = 0; i < states.length; i++) if ((states[i].pos || {})[ion] === site) return { ok: true, facts: { ion: ion, site: site, visitFrame: i } };
    return { ok: false, why: ion + ' never reached ' + site, facts: {} };
  }
};
function evalCheck(spec) {
  if (!spec || !spec.length) return { ok: false, why: 'no check', facts: {} };
  var name = spec[0], f = CHECKS[name];
  if (typeof f !== 'function') return { ok: false, why: 'unknown check ' + name, facts: {} };
  var r = f.apply(null, spec.slice(1));
  if (!r.pred) r.pred = name;
  if (!r.facts) r.facts = {};
  return r;
}

// ---- the feedback ---------------------------------------------------------------------
function checkUses(spec, name) {
  if (!spec || typeof spec !== 'object') return false;
  if (spec[0] === name) return true;
  for (var i = 1; i < spec.length; i++) if (checkUses(spec[i], name)) return true;
  return false;
}
function fillFacts(tpl, facts) {
  return String(tpl || '').replace(/\{(\w+)\}/g, function (m, k) {
    var v = facts[k];
    if (v === undefined || v === null) return m;
    return typeof v === 'number' ? fmt(v, 2) : String(v);
  });
}
function pickNudge(L, r) {
  var pool = (L.nudges && (L.nudges[r.pred] || L.nudges['default'])) || ['Not there yet.'];
  var i = (LSTATE.attempts - 1) % pool.length;
  var facts = {}, fk;
  for (fk in (r.facts || {})) if (has(r.facts, fk)) facts[fk] = r.facts[fk];
  facts.have = r.have === undefined || r.have === null ? '?' : r.have;
  var text = fillFacts(pool[i], facts);
  if (r.have !== undefined && r.have !== null && r.why && text.indexOf(String(r.have)) < 0 && r.pred !== 'selected' && r.pred !== 'answer' && r.pred !== 'measuredLess') {
    text += ' (' + r.why + ': ' + r.have + ')';
  }
  return text;
}
// THE CELEBRATION: the praise, made of the facts that earned it; the stage plays the
// user's own programme back; a green sweep runs over the canvas (an attribute the
// stylesheet animates -- nothing behavioural rides on it).
function cheer() {
  var st = $('canvas');
  if (!st || !st.setAttribute) return;
  st.setAttribute('data-cheer', '1');
  var off = function () { st.setAttribute('data-cheer', '0'); };
  if (SYNC) off(); else setTimeout(off, 1400);
  if (P.frames.length && typeof seek === 'function' && !SYNC) seek(0, { play: true });
}
function awardStar(id, n) {
  var prog = courseProgress();
  if ((prog.stars[id] || 0) < n) prog.stars[id] = n;
  courseSave(prog);
  return prog.stars[id];
}
function partDone(part) {
  if (!COURSE) return false;
  var prog = courseProgress();
  for (var i = 0; i < COURSE.lessons.length; i++) {
    if (COURSE.lessons[i].part === part && !(prog.stars[COURSE.lessons[i].id] > 0)) return false;
  }
  return true;
}

function lessonCheck() {
  var L = lessonById(LSTATE.id);
  if (!L) return { ok: false, why: 'no lesson loaded' };
  var task = lessonTask(L), r;
  if (!LSTATE.passed) {
    r = evalCheck(task.check);
    var facts = r.facts || {}, k, base = courseFacts();
    for (k in base) if (has(base, k) && facts[k] === undefined) facts[k] = base[k];
    var k0;
    for (k0 in LSTATE.recorded) if (has(LSTATE.recorded, k0) && facts[k0 + '0'] === undefined) facts[k0 + '0'] = LSTATE.recorded[k0];
    if (r.ok) {
      LSTATE.attempts = 0;
      if (L.stages && LSTATE.stage < L.stages.length - 1) {
        var en = enterStage(L, LSTATE.stage + 1), fresh = !!(L.stages[LSTATE.stage] && L.stages[LSTATE.stage].setup);
        LSTATE.recorded = courseFacts();
        LSTATE.feedback = en.ok
          ? { kind: 'ok', text: 'Yes. ' + (L.stages.length - LSTATE.stage) + ' to go' + (fresh ? ' -- a new programme is loaded: ' : ': ') + plain(lessonTask(L).exercise) }
          : { kind: 'bad', text: 'the next step could not be set up: ' + en.problems[0].message };
      } else {
        LSTATE.passed = true;
        var stars = LSTATE.shown ? 0 : awardStar(L.id, 1);
        var text = fillFacts(L.praise || 'Done.', facts);
        if (!LSTATE.shown && partDone(L.part)) {
          var pt = partById(L.part);
          if (pt && pt.milestone) text += '\n\n' + pt.milestone;
        }
        LSTATE.feedback = { kind: 'ok', text: text, stars: stars };
        cheer();
      }
    } else {
      LSTATE.attempts++;
      var nudge = pickNudge(L, r);
      // the third miss opens the next hint on its own: nobody should have to ask
      if (LSTATE.attempts >= 3 && L.hints && LSTATE.hints < L.hints.length) LSTATE.hints++;
      LSTATE.feedback = { kind: 'bad', text: nudge };
    }
  } else {
    // the lesson is passed: the boundary and the challenge are the second and third stars,
    // earned in order, and never after the solution was shown -- a star says "you did it"
    var have = courseProgress().stars[L.id] || 0;
    var extra = LSTATE.shown || have < 1 ? null
              : (L.boundary && have < 2) ? L.boundary
              : (L.challenge && have < (L.boundary ? 3 : 2)) ? L.challenge : null;
    if (!extra) {
      LSTATE.feedback = { kind: 'ok', text: LSTATE.shown
        ? 'You have seen one way. The next lesson is yours to do from scratch.'
        : 'This one is done. Next!' };
      r = { ok: true, facts: {} };
    } else {
      r = evalCheck(extra.check);
      if (r.ok) {
        var n = extra === L.boundary ? 2 : (L.boundary ? 3 : 2);
        awardStar(L.id, n);
        LSTATE.feedback = { kind: 'ok', text: fillFacts(extra.praise || (extra === L.boundary ? 'Boundary found.' : 'Sharp.'), r.facts || {}), stars: n };
        cheer();
      } else {
        LSTATE.attempts++;
        LSTATE.feedback = { kind: 'bad', text: pickNudge(L, r) };
      }
    }
  }
  renderLearn();
  renderLessonStrip();
  return { ok: !!r.ok, why: r.why, pred: r.pred, have: r.have, stage: LSTATE.stage, passed: LSTATE.passed,
           feedback: LSTATE.feedback ? LSTATE.feedback.text : '' };
}
function lessonHint() {
  var L = lessonById(LSTATE.id);
  if (!L || !L.hints) return null;
  if (LSTATE.hints < L.hints.length) LSTATE.hints++;
  renderLearn();
  return L.hints[LSTATE.hints - 1];
}
function lessonSolution(opts) {
  var L = lessonById(LSTATE.id);
  if (!L || !L.solution) return { ok: false, problems: [{ message: 'no solution for this lesson' }] };
  var sol = L.solution, r = { ok: true }, one = !!(opts && opts.one);
  LSTATE.shown = true;
  if (sol.program !== undefined) r = applyProgramSource(sol.program);
  else if (sol.stages) {
    // `one`: solve only the current stage and let the check advance to the next (which
    // then runs that stage's own setup); otherwise every remaining stage, in order
    var start = LSTATE.stage;
    for (var i = start; i < sol.stages.length && r.ok; i++) {
      if (i > start) r = enterStage(L, i);
      if (r.ok) r = runSteps(sol.stages[i]);
      if (one) break;
    }
  } else if (sol.steps) r = runSteps(sol.steps);
  else if (sol.answer !== undefined) LSTATE.answer = sol.answer;
  if (!r.ok) {
    LSTATE.feedback = { kind: 'bad', text: 'the solution could not be applied here: ' + (((r.problems || r.errors || [])[0] || {}).message || 'refused') };
    renderLearn();
    return r;
  }
  var c = lessonCheck();
  if (c.ok) LSTATE.feedback = { kind: 'ok', text: 'Here is one way -- read it back off the canvas, then try the next one yourself.' };
  renderLearn();
  return { ok: c.ok, shown: true };
}
function lessonNext() {
  if (!COURSE) return null;
  var i = lessonIndex(LSTATE.id);
  var nxt = COURSE.lessons[Math.min(i + 1, COURSE.lessons.length - 1)];
  if (!nxt || nxt.id === LSTATE.id) return null;
  lessonLoad(nxt.id);
  return nxt.id;
}
function lessonAnswer(choice) { LSTATE.answer = choice; return lessonCheck(); }
function setAnswer(choice) { LSTATE.answer = choice; return { ok: true, answer: choice }; }

// ---- rendering --------------------------------------------------------------------------
// Three marks: **bold**, `code`, [[term|hint-key]].  Everything is escaped first.
function inline(t) {
  var h = esc2(t);
  h = h.replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, function (m, term, key) {
    return '<span class="term" data-hint="' + esc2(key) + '">' + term + '</span>';
  });
  h = h.replace(/\{\{([^}|]+)\|([^}]+)\}\}/g, function (m, label, href) {
    return '<a href="' + esc2(href) + '" target="_blank" rel="noopener">' + label + '</a>';
  });
  h = h.replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
  h = h.replace(/`([^`]+)`/g, '<code>$1</code>');
  return h;
}
// the frame a lesson names: an index, the dearest instruction, or the one that realises
// a circuit statement on a compiled page (`realises:<op>`, from the certificate's join)
function frameFor(which) {
  if (typeof which === 'string' && which.slice(0, 9) === 'realises:') {
    var S = D.source, op = Number(which.slice(9));
    if (!S || !S.realises) return -1;
    for (var i = 0; i < P.frames.length; i++) {
      var ops = S.realises[P.frames[i].id] || [];
      if (ops.indexOf(op) >= 0) return i;
    }
    return -1;
  }
  if (which === 'maxcost') {
    var best = -1, bi = -1;
    for (var k = 0; k < P.frames.length; k++) {
      var c = (PRICE && PRICE.perFrame && PRICE.perFrame[k]) ? Number(PRICE.perFrame[k][0]) : Number(P.frames[k].cost || 0);
      if (c > best) { best = c; bi = k; }
    }
    return bi;
  }
  return Number(which);
}
// Part D's measured table, rendered from what the page shipped
function measuredRow(kind, arch) {
  var M = D.tutorial_measured || {};
  if (kind === 'planes') return (M.planes || {})[arch] || null;
  var mi = (M.micro || {})[arch];
  return mi ? (mi.cooled || mi.raw || null) : null;
}
function renderMeasured(kind) {
  var M = D.tutorial_measured;
  if (!M) return '<div class="learn-note">this page carries no measured results</div>';
  var arches = Object.keys(kind === 'planes' ? (M.planes || {}) : (M.micro || {})), i, h;
  if (!arches.length) return '<div class="learn-note">no measured results shipped for ' + esc2(kind) + '</div>';
  if (kind === 'planes') {
    h = '<table class="learn-table"><tr><th>device</th><th>wiring</th><th>electrodes</th><th>DACs</th><th>junctions</th></tr>';
    for (i = 0; i < arches.length; i++) {
      var p = M.planes[arches[i]];
      h += '<tr><td>' + esc2(arches[i]) + '</td><td>' + esc2(p.scheme) + '</td><td>' + p.electrodes + '</td><td>' + p.dacs + '</td><td>' + p.junctions + '</td></tr>';
    }
    return h + '</table><div class="mut">hardware_report(arch) at page-build time</div>';
  }
  h = '<table class="learn-table"><tr><th>device</th><th>instructions</th><th>cost</th><th>steps</th><th>runtime</th><th>DACs</th></tr>';
  for (i = 0; i < arches.length; i++) {
    var r = measuredRow('micro', arches[i]), pl = (M.planes || {})[arches[i]] || {};
    if (!r) continue;
    h += '<tr><td>' + esc2(arches[i]) + '</td><td>' + r.instructions + '</td><td>' + fmt(r.total_cost, 1) + '</td><td>' + r.total_steps + '</td><td>' + fmt(r.runtime_us / 1000, 2) + ' ms</td><td>' + (pl.dacs === undefined ? '' : pl.dacs) + '</td></tr>';
  }
  return h + '</table><div class="mut">' + esc2(M.circuit) + ' compiled for each device, replayed under the ' + esc2(M.model) + ' model at page-build time (cooled artifacts)</div>';
}
function plain(t) { return String(t || '').replace(/\[\[([^\]|]+)\|[^\]]+\]\]/g, '$1').replace(/\*\*/g, '').replace(/`/g, ''); }
function starsMax(L) { return 1 + (L && L.boundary ? 1 : 0) + (L && L.challenge ? 1 : 0); }
function starsOf(n, max) {
  var out = '', k = max === undefined ? 3 : max;
  for (var i = 1; i <= k; i++) out += (n >= i ? '\u2605' : '\u2606');
  return out;
}

function renderLearn() {
  var host = $('learnBody');
  if (!host) return;
  if (!COURSE) { host.innerHTML = '<div class="mut">the course did not load with this page</div>'; return; }
  var prog = courseProgress(), h = '', i;
  var L = lessonById(LSTATE.id);
  // the picker: every lesson, grouped by part, with its stars
  h += '<div class="learn-nav"><button id="lnPrev" title="previous lesson">\u25c0</button>' +
       '<select id="lnPick" data-hint="learn:pick">';
  for (i = 0; i < COURSE.parts.length; i++) {
    var pt = COURSE.parts[i];
    h += '<optgroup label="' + esc2(pt.id + ' \u00b7 ' + pt.title) + '">';
    for (var j = 0; j < COURSE.lessons.length; j++) {
      var M = COURSE.lessons[j];
      if (M.part !== pt.id) continue;
      h += '<option value="' + esc2(M.id) + '"' + (L && M.id === L.id ? ' selected' : '') + '>' +
           esc2(M.id + ' \u00b7 ' + M.title) + '  ' + starsOf(prog.stars[M.id] || 0, starsMax(M)) + '</option>';
    }
    h += '</optgroup>';
  }
  h += '</select><button id="lnNext" title="next lesson" data-hint="learn:next">\u25b6</button></div>';
  if (!L) {
    var first = COURSE.lessons[0];
    h += '<div class="learn-story">A course for people who have never seen one of these machines: ' +
         COURSE.lessons.length + ' lessons in ' + COURSE.parts.length + ' parts, each a few minutes, each with an exercise this page checks.</div>' +
         '<div class="learn-btns"><button class="p" id="lnLoad" data-hint="learn:load">Start with ' + esc2(first.id) + ' \u00b7 ' + esc2(first.title) + '</button></div>';
    host.innerHTML = h;
    wireLearn(host);
    return;
  }
  var task = lessonTask(L), nst = lessonTotalStages(L), stars = prog.stars[L.id] || 0;
  h += '<div class="learn-head"><b>' + esc2(L.id) + ' \u00b7 ' + esc2(L.title) + '</b><span class="learn-stars" title="' +
       (L.boundary && L.challenge ? 'done \u00b7 boundary \u00b7 sharp' : L.boundary ? 'done \u00b7 boundary' : L.challenge ? 'done \u00b7 sharp' : 'done') +
       '">' + starsOf(stars, starsMax(L)) + '</span></div>';
  if (L.story) h += '<div class="learn-story">' + inline(L.story) + '</div>';
  for (i = 0; i < (L.text || []).length; i++) h += '<p class="learn-p">' + inline(L.text[i]) + '</p>';
  h += '<div class="learn-ex"><div class="learn-exh">Exercise' + (nst > 1 ? ' \u00b7 step ' + (LSTATE.stage + 1) + ' of ' + nst : '') + '</div>' +
       '<div>' + inline(task.exercise) + '</div>';
  if (L.choices && (checkUses(task.check, 'answer') || checkUses(task.check, 'measuredLess'))) {
    h += '<div class="learn-choices">';
    for (i = 0; i < L.choices.length; i++) {
      h += '<label><input type="radio" name="lnChoice" value="' + esc2(L.choices[i].id) + '"' + (LSTATE.answer === L.choices[i].id ? ' checked' : '') + '> ' + inline(L.choices[i].text) + '</label>';
    }
    h += '</div>';
  }
  h += '<div class="learn-btns">' +
       '<button id="lnLoad" data-hint="learn:load">Load</button>' +
       '<button class="p" id="lnCheck" data-hint="learn:check">Check</button>' +
       '<button id="lnHint" data-hint="learn:hint"' + (L.hints && LSTATE.hints < L.hints.length ? '' : ' disabled') + '>Hint</button>' +
       '<button id="lnSol" data-hint="learn:solution"' + (L.solution ? '' : ' disabled') + '>Solution</button>' +
       '<button id="lnGo" data-hint="learn:next"' + (LSTATE.passed ? ' class="p"' : '') + '>Next \u25b6</button></div></div>';
  if (L.page && !D.source) {
    h += '<div class="learn-note">This lesson runs on the <b>compiled</b> page, which this one is not. ' +
         inline('Build both with `python -m qccd tutorial -o out/tutorial` and open {{' + L.page + '.html|' + L.page + '.html}} beside this page.') + '</div>';
  }
  if (L.table) h += renderMeasured(L.table);
  var vd = task.verdict || L.verdict;
  if (vd) h += renderVerdict(vd);
  if (LSTATE.feedback) {
    h += '<div class="learn-fb ' + (LSTATE.feedback.kind === 'ok' ? 'ok' : 'bad') + '" id="learnFeedback">' +
         (LSTATE.feedback.stars ? '<span class="learn-fbstars">' + starsOf(LSTATE.feedback.stars, starsMax(L)) + '</span>' : '') +
         esc2(LSTATE.feedback.text).replace(/\n/g, '<br>') + '</div>';
  }
  if (LSTATE.hints > 0) {
    h += '<div class="learn-hints">';
    for (i = 0; i < LSTATE.hints; i++) h += '<div class="learn-hint"><span>' + (i + 1) + '</span>' + inline(L.hints[i]) + '</div>';
    h += '</div>';
  }
  if (LSTATE.passed && (L.boundary || L.challenge)) {
    var extra = (L.boundary && stars < 2) ? L.boundary : (L.challenge && stars < 3) ? L.challenge : null;
    if (extra) h += '<div class="learn-ex extra"><div class="learn-exh">' + (extra === L.boundary ? 'Second star \u00b7 the boundary' : 'Third star \u00b7 sharp') + '</div><div>' + inline(extra.exercise) + '</div></div>';
  }
  host.innerHTML = h;
  wireLearn(host);
}
function renderVerdict(vd) {
  var V = shippedVerdict(vd['case'], vd.which);
  if (!V) return '<div class="learn-verdict" data-hint="learn:verdict"><div class="learn-exh">Python\u2019s verdict</div><div class="mut">this page carries no verdict for case ' + esc2(vd['case']) + '</div></div>';
  var h = '<div class="learn-verdict" data-hint="learn:verdict"><div class="learn-exh">Python\u2019s verdict on the ' +
          (vd.which === 'fixed' ? 'repaired' : 'loaded') + ' programme \u00b7 computed when this page was built \u00b7 ' + esc2(V.model || '') + ' model</div>';
  var f = V.focus, i;
  if (f) {
    h += '<div><b data-hint="rule:' + esc2(f.rule) + '">' + esc2(f.rule) + '</b><span class="vstate ' + esc2(f.state) + '">' + esc2(f.state) + '</span>' +
         (f.why ? ' <span class="mut">' + esc2(f.why) + '</span>' : '') + '</div>';
    if (f.messages && f.messages.length) {
      h += '<ul>';
      for (i = 0; i < Math.min(2, f.messages.length); i++) h += '<li>' + esc2(f.messages[i].message) + '</li>';
      if (f.messages.length > 2) h += '<li class="mut">and ' + (f.messages.length - 2) + ' more like it</li>';
      h += '</ul>';
    }
  }
  var R = V.rules || {}, sk = R.skipped || {}, pa = R.partial || {}, k;
  var others = [];
  for (i = 0; i < (R.failed || []).length; i++) if (!f || R.failed[i] !== f.rule) others.push(R.failed[i] + ' failed');
  for (k in sk) if (has(sk, k) && (!f || k !== f.rule)) others.push(k + ' skipped: ' + sk[k]);
  for (k in pa) if (has(pa, k) && (!f || k !== f.rule)) others.push(k + ' partial: ' + pa[k]);
  h += '<div class="mut" style="margin-top:4px">' + (R.passed || []).length + ' rules passed' + (others.length ? '; ' + esc2(others.join('; ')) : '') + '.</div>';
  var M = V.metrics || {};
  if (M.total_cost !== undefined && M.total_cost !== null) {
    h += '<div class="mut">cost ' + fmt(M.total_cost, 2) + ' \u00b7 ' + M.total_steps + ' steps \u00b7 ' + fmt(M.runtime_us, 1) + ' us \u00b7 peak n-bar ' + fmt(M.peak_quanta, 3) +
         (M.gate_error_sum !== undefined && M.gate_error_sum !== null ? ' \u00b7 gate error (R16) ' + Number(M.gate_error_sum).toExponential(2) : '') + '</div>';
  }
  return h + '</div>';
}
function wireLearn(host) {
  var on = function (id, f) { var e = $(id); if (e) e.onclick = function () { f(); unfocus(e); }; };
  on('lnLoad', function () { lessonLoad(LSTATE.id || (COURSE.lessons[0] && COURSE.lessons[0].id)); });
  on('lnCheck', lessonCheck);
  on('lnHint', lessonHint);
  on('lnSol', lessonSolution);
  on('lnGo', lessonNext);
  on('lnNext', lessonNext);
  on('lnPrev', function () { var i = lessonIndex(LSTATE.id); if (i > 0) lessonLoad(COURSE.lessons[i - 1].id); });
  var pick = $('lnPick');
  if (pick) pick.onchange = function () { lessonLoad(pick.value); };
  var radios = host.querySelectorAll ? host.querySelectorAll('input[name="lnChoice"]') : [];
  for (var i = 0; i < radios.length; i++) {
    radios[i].onchange = (function (r) { return function () { LSTATE.answer = r.value; }; })(radios[i]);
  }
}
// THE STRIP over the Write pane: the exercise in one line and the two buttons that matter
// there, so a programming exercise never needs a tab switch.
function renderLessonStrip() {
  var el = $('pwLesson');
  if (!el) return;
  var L = lessonById(LSTATE.id);
  if (!L) { if (el.style) el.style.display = 'none'; return; }
  var task = lessonTask(L);
  el.innerHTML = '<b>' + esc2(L.id) + '</b> <span>' + inline(task.exercise) + '</span>' +
                 '<button class="p" id="lsCheck" data-hint="learn:check">Check</button>' +
                 '<button id="lsHint" data-hint="learn:hint">Hint</button>';
  if (el.style) el.style.display = '';
  var c = $('lsCheck'), hh = $('lsHint');
  if (c) c.onclick = function () { lessonCheck(); unfocus(c); setPaneL(); };
  if (hh) hh.onclick = function () { lessonHint(); unfocus(hh); setPaneL(); };
}
function setPaneL() { if (typeof setPane === 'function') setPane('L'); }

// verbs the lessons use that had no API entry of their own
function seekTo(which) {
  var i = frameFor(which);
  if (i < 0 || !P.frames.length) return null;
  if (typeof seek === 'function') seek(i, {});
  return (typeof frame === 'number') ? frame : i;
}
function fitStage() { if (typeof fit === 'function') fit(); return true; }

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
  // The seal the shipped frames were compiled against is read NOW: a generator card as
  // the very first gesture would otherwise be the first caller of `lowerNow`, and it
  // would record the new device's seal as the shipped one.
  captureShipped();
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
function isCanvasOps(ops) {
  for (var i = 0; ops && i < ops.length; i++) if (ops[i] && ops[i].canvas) return true;
  return false;
}
function transaction(ops, label) {
  var i, at = EDITS.length, prev = null;
  var g0 = GEOM.slice(), s0 = SEED, p0 = POST.slice(), e0 = EDITS.slice();
  var geom = GEOM.slice(), seed = SEED, post = POST.slice(), edits = EDITS.slice();
  var group = GROUP + 1;
  for (i = 0; i < ops.length; i++) {
    var op = ops[i];
    if (op.canvas) {
      // a hard reset: a new device replaces the geometry, the seal and the retunes --
      // and the device it replaces is kept for `undo()`, edits and programme included
      if (!prev && STATE) prev = canvasRecord();
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
  // a new edit forgets both redo stacks; a new DEVICE keeps the canvas stack for the
  // record it pushes below (and `prev === null` there means nothing changed)
  if (prev) UNDONE.length = 0; else forgetRedo();
  // A CANVAS OP IS A NEW DEVICE, and the one transaction that re-fits: every other holds
  // the view, because an edit that re-fitted moved the drawing under the pointer.
  if (isCanvasOps(ops)) refitNext();
  rebuild();
  var mine = PROBLEMS.filter(function (p) { return p.i !== null && p.i >= at; });
  if (mine.length) {
    GEOM = g0; SEED = s0; POST = p0; EDITS = e0;
    if (isCanvasOps(ops)) refitNext();
    rebuild();
    return { ok: false, problems: mine, label: label };
  }
  // A NEW DEVICE IS A NEW GESTURE: the redo stack of the device it replaces is discarded
  // exactly as `UNDONE.length = 0` above discards it for an edit, so undoing the pick
  // brings the old device back and the NEXT redo re-picks the new one -- not an edit the
  // old device had undone before the pick.  Linear history, one rule.
  // A PICK THAT CHANGES NOTHING IS NOT A GESTURE: the same card pressed twice used to
  // push an identical record, so the first ctrl+Z restored the device already on the
  // stage and looked dead.  Same geometry, seal, retunes and (no) edits: no record.
  if (prev && sameCanvas(prev, canvasRecord())) prev = null;
  if (prev) { prev.undone = []; CANVAS_UNDO.push(prev); CANVAS_REDO.length = 0; }
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
  //
  // WHAT A ZONE MAY BE NAMED AFTER: the physics of the site, never the role a code would
  // give the ion sitting in it.  This is a hardware design tool -- a zone says whether a
  // gate can be driven there, whether it can be measured, cooled or loaded -- and
  // `ancilla` says none of those things; it says what an error-correcting code would use
  // the site for, which is a layer this tool does not model.  So a canvas starts with the
  // four HARDWARE zones and nothing else.  A device that wants a code's vocabulary can
  // still declare it (Elements > zone type, or a template that ships one), and every
  // shipped `arch/*.arch.json` keeps whatever it already declares -- this is the studio's
  // own default, not a change to the format.
  var ZONES = {
    data:    { capacity: 2 },
    trap:    { capacity: 2, gate: true, spam: true, cool: true },
    gate:    { capacity: 2, gate: true },
    load:    { capacity: 8, spam: true, cool: true, photoionization: true }
  };
  var seed = tmpl
    ? { method: 'from_device', args: [], kwargs: { name: name, template: tmpl } }
    : { method: 'blank_device', args: [], kwargs: { name: name, zones: ZONES } };
  // Seed the one key that makes a blank canvas RUNNABLE. `control.model` is required by
  // the schema, and without it `declare_class` is refused ("$.control: missing required
  // key 'model'") -- so a from-scratch device could be built and exported but never
  // priced, and every one of the 27 rules stayed `unchecked` forever. `simd_classes` is
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
// the ids the BUILDER itself holds: every `d.site` / `d.junction` above the seal.  A
// node the sealed device has but the builder does not (a post-seal `add_site`) is one a
// builder `d.segment` cannot reach -- it is hoisted above the seal, where that node does
// not exist yet -- which is what `joinNodes` reads this for.
function builderHeldIds() {
  var out = {}, calls = baseCalls(), i;
  for (i = 0; i < calls.length; i++) {
    if (calls[i].method === 'd.site' || calls[i].method === 'd.junction') {
      out[String(calls[i].args[0])] = true;
    }
  }
  return out;
}
function builderNodeIds() {
  // the ids a NEW id must not collide with: what the builder holds -- the builder
  // survives a seal, so it can hold nodes the sealed device does not -- plus the device's
  var out = builderHeldIds(), i;
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

// COINCIDENT PLACEMENT: `min_nearest_neighbour` SKIPS coincident points, so two nodes on
// one spot silently resize every mark on the stage -- the same reason `validate()`
// already refuses a coincident `move_site`.  The one check for a single node and for
// every node of a component about to land.
function coincidentAt(x, y) {
  var ns = nodesOf(STATE);
  for (var i = 0; i < ns.length; i++) {
    if (Math.abs(ns[i].x - x) < 1e-9 && Math.abs(ns[i].y - y) < 1e-9) {
      return { code: 'coincident', targets: [ns[i].id],
        message: 'a node already sits at (' + x + ', ' + y + '); two nodes on one point ' +
                 'make the drawn scale meaningless' };
    }
  }
  return null;
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
  var coin = coincidentAt(x, y);
  if (coin) return { ok: false, problems: [coin] };
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
  // THE APPLIER THE ENDPOINTS HAVE.  A builder `d.segment` is hoisted above the seal, so
  // it can only join nodes the builder holds; a generator device holds none, and a node
  // added after the seal (`add_site`) is one the builder never saw.  Either way the
  // segment is a post-seal topology edit -- the same `add_segment` the shift-drag's
  // `joinNodes` lands on, decided here (not retried after a refusal) so the harness's
  // `join`, the inspector and the pointer cannot disagree.  A loop edge stays a builder
  // statement:
  // `add_segment` has no `loop=`.
  var held = builderHeldIds();
  if (!opts.loop && (!hasBuilder() || !has(held, a) || !has(held, b))) {
    return addSegment(a, b, { length: len, capacity: capv, labels: opts.labels });
  }
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
// THE PARSE ERRORS, kept beside `PROG` so the pane can show them AT THE LINE: a text
// that does not parse leaves the records as they were, and until this existed the only
// trace of the failure was a toast that had already faded.
var PARSE_ERRS = [];
// WHAT THE PAGE SHIPPED WITH, recorded once so `lowerNow` can hand it back: the compiled
// frames and their provenance, the instruction count and the programme name -- and the
// SEAL they were compiled against.  `SEED0` is the witness that decides whether the
// shipped programme is a programme for the device on the stage at all.
var SHIPPED_FRAMES = null, SHIPPED_PROV = null, SHIPPED_N = 0, SHIPPED_NAME = null;
var SEED0 = null, SEED0_KEY = null, NO_FRAMES = [];
// WHETHER THE PAGE SHIPPED EMPTY: the blank page's headline ("an empty canvas: build a
// device ...") is true of nothing once a site is on the stage, where a seeded page's
// headline stays true of the device it describes.  Read once, from the arch the page was
// emitted with, before any rebuild can touch it.
var SHIPPED_EMPTY = false;

function captureShipped() {
  if (SHIPPED_FRAMES !== null) return;
  SHIPPED_EMPTY = !!(typeof A !== 'undefined' && A && A.nodes && A.nodes.length === 0);
  SHIPPED_FRAMES = P.frames;
  SHIPPED_PROV = (typeof PROV !== 'undefined') ? PROV : null;
  SHIPPED_N = P.n_instructions === undefined ? P.frames.length : P.n_instructions;
  SHIPPED_NAME = P.name;
  SEED0 = SEED;
  SEED0_KEY = JSON.stringify(SEED0);
}
// The shipped programme fits exactly one device: the one whose seal it was compiled
// against.  BY VALUE, not identity: a snapshot that went through JSON (autoload, a file
// import) comes back with a fresh seed object for the very same device, and the 13
// shipped frames are still its programme.
function shippedSeed() {
  return SEED === SEED0 || JSON.stringify(SEED) === SEED0_KEY;
}
function programmeIsShipped() { return P.frames === SHIPPED_FRAMES; }

function setProgram(records) {
  PROG = (records || []).map(function (r) {
    return { method: r.method, args: (r.args || []).slice(), kwargs: r.kwargs || {},
             text: r.text, line: r.line };
  });
  PROG_SRC = null;
  PARSE_ERRS = [];
  // the pane repaints from the records now, not from the previous device's text
  var ta = $('pwText');
  if (ta) ta._touched = false;
  rebuild();
  return { ok: true, errors: lowerErrors() };
}

function lowerErrors() { return LOWER ? LOWER.errors : []; }

// Re-lowered on EVERY architecture edit as well as every programme edit, which is why an
// authored programme's `entails` can never go stale: it is read from the live class table
// at lowering time rather than baked at emit time.
//
// DECIDED ONCE, HERE: with no authored programme the stage shows the SHIPPED frames only
// while the seal is the one they were compiled against.  A device from a generator card,
// a blank canvas, a restored foreign snapshot or an imported file is a different device,
// and it used to inherit the previous programme's frames -- so every new device opened
// under a red "programme invalid" banner blaming an edit nobody made (measured: 31
// `unknown_node` breaks on a fresh 8-site ring).  A new device has NO programme, and the
// honest sentence for that is "no programme yet", never a verdict.
function lowerNow() {
  captureShipped();
  if (!PROG.length) {
    AUTHORED = false;
    LOWER = null;
    var shipped = shippedSeed();
    P.frames = shipped ? SHIPPED_FRAMES : NO_FRAMES;
    P.n_instructions = shipped ? SHIPPED_N : 0;
    P.name = shipped ? SHIPPED_NAME : 'none';
    if (typeof PROV !== 'undefined') PROV = shipped ? SHIPPED_PROV : null;
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
  var wrong = (!p.errors.length && p.arch.length) ? p.arch[0] : null;
  if (p.errors.length || wrong) {
    // the records stand; the pane shows the failure at its line until the next parse
    PARSE_ERRS = p.errors.length ? p.errors : [{ line: wrong.line, col: 1,
      message: JSON.stringify(wrong.method) + ' is an architecture statement; the ' +
               'programme pane takes `p.` verbs only (have: ' +
               Q.PROGRAM_METHODS.join(', ') + ')', text: wrong.text }];
    renderWrite();
    return { ok: false, errors: PARSE_ERRS.slice() };
  }
  PARSE_ERRS = [];
  PROG = p.prog.map(function (r) {
    return { method: r.method, args: r.args, kwargs: r.kwargs, text: r.text, line: r.line };
  });
  PROG_SRC = src;
  rebuild();
  return { ok: true, errors: [], problems: lowerErrors() };
}

// THE ENGINE'S WORDS, said for THIS pane.  The parser is shared with the architecture
// lane, so its "must start with `m = Machine.`..." names four prefixes of which none
// is the one the programme pane takes; and a KeyError is Python's exact `repr(x)`,
// which says what was not found but not where.  Reworded here, never in engine.js.
function humanMessage(e) {
  var m = String((e && e.message) || '');
  if (/must start with/.test(m)) {
    return 'a programme statement must start with `p.` (have: ' +
           Q.PROGRAM_METHODS.map(function (v) { return 'p.' + v; }).join(', ') + ')';
  }
  // a bare `'T9'` is Python's KeyError for a lookup that found nothing; a KeyError that
  // already carries a sentence ("no segment between ...") says where by itself
  if (e && e.code === 'KeyError' && /^'[^']*'$/.test(m)) {
    return m + ' — no such node, segment or loop on ' + ((STATE && STATE.name) || 'this device');
  }
  return m;
}
// one line per problem, for the strip and the toast alike: a parse error is AT a line,
// a lowering error is AT a statement
function problemLine(e) {
  if (e && e.line !== undefined && e.i === undefined) {
    return 'line ' + e.line + ' col ' + (e.col || 1) + ': ' + humanMessage(e);
  }
  return 'statement ' + ((!e || e.i === null || e.i === undefined) ? '?' : e.i + 1) +
         ': ' + humanMessage(e);
}

// WHAT THE EVALUATE BUTTON DOES, as one verb.  The button used to say "ok: 2 statements,
// 1 frames" about a programme whose second statement had been refused -- a truncated
// programme reported as a success.  Every problem the two checkers already produce (the
// parse, then `Q.lowerProgram` through `rebuild`) is a refusal here, said with how much
// of the programme did run; an empty text clears the programme and says so.
// HOW MANY STATEMENTS RAN: the records minus every statement a lowering error names.
function statementsRun(probs) {
  var ran = PROG.length, seen = {};
  for (var i = 0; i < probs.length; i++) {
    var k = probs[i].i === null || probs[i].i === undefined ? '?' : probs[i].i;
    if (!seen[k]) { seen[k] = true; ran--; }
  }
  return ran;
}
// THE STANDING REFUSAL, one sentence: what the Evaluate toast says, and what the stage's
// reason strip says for as long as it holds -- an ARCHITECTURE edit can refuse an
// authored statement too (delete the junction it routes over), and until this line stood
// under the picture the only trace was the Write pane, off-screen behind another tab.
// '' while nothing is refused.
function refusedLine() {
  var probs = lowerErrors();
  if (!AUTHORED || !probs.length) return '';
  return problemLine(probs[0]) + ' — ' + statementsRun(probs) + ' of ' + PROG.length +
         ' statements run';
}
function evaluateWrite(text) {
  if (text === undefined) { var ta = $('pwText'); text = ta ? (ta.value || '') : ''; }
  var r = applyProgramSource(text);
  var probs = r.ok ? (r.problems || []) : r.errors;
  var ran = r.ok ? statementsRun(probs) : 0;
  if (r.ok && !text.trim()) toast('ok', 'programme cleared');
  else if (probs.length) toast('bad', r.ok ? refusedLine() : problemLine(probs[0]));
  else toast('ok', PROG.length + ' statements, ' + P.frames.length + ' frames');
  return { ok: r.ok && !probs.length, parsed: r.ok, problems: probs,
           ran: ran, statements: PROG.length, frames: P.frames.length };
}

// THE PREDICATE THAT FREEZES THE STAGE: the structural break list, `[]` when the
// programme fits.  `price().blocked` is this list PLUS the cost-model failures
// (`no_curve`, `price_error`), which do not freeze because the picture is still true.
function programBreaks() {
  return (typeof PROGRAM_STALE !== 'undefined' && PROGRAM_STALE)
    ? PROGRAM_STALE.breaks.slice() : [];
}

// ------------------------------------------------------------------- test drive
//
// ONE CLICK FROM A DEVICE TO AN ANIMATION.  Getting there by hand took eight steps
// including two off-screen scrolls and hand-typed node ids, and a new generator device
// opened with no programme at all.  `testDrivePlan` reads ONLY `STATE.device` and writes
// the smallest programme that exercises it: rotate a closed loop if there is one, else
// shuttle one ion out over real segments and back.  It is a plan, not a verdict --
// `testDrive` runs it through exactly the lowering and validation `rebuild` already does
// and rolls back if the device refuses it.  There is no second checker here.
function testDrivePlan(opts) {
  opts = opts || {};
  var dev = STATE && STATE.device;
  var refuse = function (code, message) {
    return { ok: false, problems: [{ code: code, message: message }] }; };
  if (!dev) return refuse('no_device', 'no device to drive');
  var lid;
  // `opts.noLoop`: the shuttle route even when a closed loop exists -- what `testDrive`
  // asks for after the device refused the rotation (a physics package with no rotate
  // class, say), because a loop the device cannot turn still has segments to drive
  if (!opts.noLoop) for (lid in dev.loops) if (has(dev.loops, lid)) {
    var lp = dev.loops[lid];
    if (lp.closed && lp.nodes.length >= 2) {
      return { ok: true, kind: 'rotate', loop: lid, from: lp.nodes[0], statements: [
        { method: 'fill', args: [lid], kwargs: {} },
        { method: 'rotate', args: [1, lid], kwargs: {} },
        { method: 'rotate', args: [1, lid], kwargs: {} },
        { method: 'rotate', args: [-2, lid], kwargs: {} }] };
    }
  }
  // no closed loop: breadth-first over the segments from the first site that has one, to
  // the farthest site within 8 hops of it -- a node path `p.shuttle` can drive, because
  // every consecutive pair is a real segment
  var adj = {}, sid, nid;
  for (sid in dev.segments) if (has(dev.segments, sid)) {
    var sg = dev.segments[sid];
    (adj[sg.a] = adj[sg.a] || []).push(sg.b);
    (adj[sg.b] = adj[sg.b] || []).push(sg.a);
  }
  var start = null;
  for (nid in dev.nodes) if (has(dev.nodes, nid)) {
    if (dev.nodes[nid].kind === 'site' && adj[nid] && adj[nid].length) { start = nid; break; }
  }
  if (start === null) return refuse('no_route', 'place two sites and a segment first');
  var dist = {}, prev = {}, queue = [start], far = null, qi = 0;
  dist[start] = 0;
  while (qi < queue.length) {
    var u = queue[qi++];
    if (dist[u] >= 8) continue;
    var nb = adj[u] || [];
    for (var i = 0; i < nb.length; i++) {
      var v = nb[i];
      if (has(dist, v)) continue;
      dist[v] = dist[u] + 1; prev[v] = u; queue.push(v);
      if (dev.nodes[v] && dev.nodes[v].kind === 'site' &&
          (far === null || dist[v] > dist[far])) far = v;
    }
  }
  // one site joined only to junctions: an ion could leave but has nowhere to stop
  if (far === null) return refuse('no_route', 'place two sites and a segment first');
  var path = [far];
  while (path[path.length - 1] !== start) path.push(prev[path[path.length - 1]]);
  path.reverse();
  var back = path.slice().reverse();
  return { ok: true, kind: 'shuttle', from: start, to: far, statements: [
    { method: 'init', args: [{ d0: start }], kwargs: {} },
    { method: 'shuttle', args: ['d0', path], kwargs: {} },
    { method: 'shuttle', args: ['d0', back], kwargs: {} }] };
}

// Writes the plan as the authored programme, through `setProgram` -- the same lane the
// Write pane uses, so the pane shows its source -- and keeps it only if the lowering and
// the structural validation `rebuild` just ran accept it.  Synchronous: `P.frames` and
// the return value are correct before this returns; the autoplay is the one deferred
// thing and it is skipped under `SYNC`, where no animation frame ever fires.
function testDrive() {
  var plan = testDrivePlan();
  if (!plan.ok) return plan;
  var ta = $('pwText');
  var prog0 = PROG, src0 = PROG_SRC, touched0 = ta ? ta._touched : false;
  var tryPlan = function (pl) {
    setProgram(pl.statements);
    var bad = lowerErrors().map(function (e) {
      return { code: e.code || 'lower_error', message: e.message }; });
    bad = bad.concat(programBreaks().map(function (b) {
      return { code: b.kind, message: breakMessage(b) }; }));
    if (!bad.length && PRICE && PRICE.blocked && PRICE.blocked.length) {
      bad = PRICE.blocked.map(function (b) {
        return { code: b.kind, message: b.message || breakMessage(b) }; });
    }
    return bad;
  };
  var bad = tryPlan(plan);
  // A LOOP THE DEVICE CANNOT TURN still has segments to shuttle over: an 8-node ring on
  // a package with no rotate class refused the rotation, and the button said "refused"
  // about a device a two-statement shuttle drives perfectly well.  The second plan is
  // the same planner told to skip the loop, judged by the same lowering.
  if (bad.length && plan.kind === 'rotate') {
    var alt = testDrivePlan({ noLoop: true });
    if (alt.ok) {
      var bad2 = tryPlan(alt);
      if (!bad2.length) { plan = alt; bad = bad2; }
    }
  }
  if (bad.length) {
    PROG = prog0; PROG_SRC = src0;
    if (ta) ta._touched = touched0;
    rebuild();
    return { ok: false, kind: plan.kind, problems: bad };
  }
  if (typeof seek === 'function' && !SYNC) seek(0, { play: true });
  return { ok: true, kind: plan.kind, statements: PROG.slice(), frames: P.frames.length,
           problems: [] };
}

// The two Test drive buttons share this: the verb, then the one sentence about it.
function pressTestDrive() {
  var r = testDrive();
  if (!r.ok) toast('bad', 'test drive refused: ' + ((r.problems[0] || {}).message || 'no route'));
  else toast('ok', 'test drive: ' + r.statements.length + ' statements, ' + r.frames + ' frames');
  return r;
}

// One record, appended.  The palette's programme buttons and a future drag-to-author both
// write through this, so the text lane and the button lane cannot disagree.
function emitProgram(rec) {
  var before = PROG.slice();
  PROG.push({ method: rec.method, args: (rec.args || []).slice(), kwargs: rec.kwargs || {} });
  PROG_SRC = null;
  PARSE_ERRS = [];
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
// THREE STATES PER RULE, and the header counts rather than saying "all".  1 of the 27 is
// state-free; the browser re-derives 21 of them off the pricing walk; the other 6 need
// Python, and each is named WITH ITS REASON rather than being absent.
var RULES = null;
function ruleReport() { return RULES; }

function evaluateNow(model) {
  RULES = null;
  if (!STATE || !model) return;
  if (PRICE && PRICE.blocked) return;    // a broken programme has no verdicts to report
  // NO PROGRAMME, NO VERDICTS.  Every rule is vacuously satisfied over zero cycles, so a
  // report built from an empty replay would show 21 green badges for a machine nothing has
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

// 27 entries, one per rule, each with its state and its reason.  The header text is
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

function snapshot() { return documentRecord(STATE ? Q.serialize(STATE) : null); }
// the document part of a snapshot, with the architecture supplied by the caller: the
// saved file carries `Q.serialize(STATE)`, the canvas history carries none
function documentRecord(arch) {
  return {
    kind: 'qccd.studio', version: 1,
    arch: arch,
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
  // a parse error stands only while the text that produced it is the pane's text
  PARSE_ERRS = [];
  var ta = $('pwText');
  if (ta) ta._touched = false;
  var was = WHY_NOT;
  WHY_NOT = null;
  refitNext();                       // an imported device is a new drawing
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
    refitNext();
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
//
// THE STAGE IS THE START SURFACE.  While the canvas has no node it carries the build
// cards (a blank canvas, or a generator with the parameters it will use written on the
// card), the physics package as one <select> (the template whose zones, curves and
// control block the device borrows), the shipped devices as one <select> and an "open"
// button, and two buttons that arm a site or a junction stamp -- so the sentence "press
// Trapping site, then click here" points at something on screen.  The rail's Start fold
// is the same controls, compact, and it stays closed unless the user opens it: an open
// fold that listed ten packages with a card each pushed the first element tile 1,200 px
// below the top of the page, off every laptop screen, with the empty state pointing at
// it.
//
// THE PARAMETERS A GENERATOR CARD USES, in one place: a starting value for every
// REQUIRED positional of `generator_signatures` (reflected off Python) and NOTHING else.
// The defaults are not forwarded: a default is what the generator does when the keyword
// is absent, so sending it changes nothing -- except when Python has grown a keyword the
// engine's twin has not (`ring(dock_offset=)` did exactly that, and the card refused with
// "unexpected keyword argument" on every page).  The card label, its tooltip and the
// click read the same table, and the call the tooltip shows is the call that is made.
function generatorParams(gen) {
  var sig = (D.generator_signatures || {})[gen] || { required: [], defaults: {} };
  var params = {}, j;
  for (j = 0; j < sig.required.length; j++) params[sig.required[j]] = 4;
  return params;
}
function generatorLabel(gen) {
  var sig = (D.generator_signatures || {})[gen] || { required: [], defaults: {} };
  var p = generatorParams(gen), parts = [], i;
  if (sig.required.length === 2) return String(p[sig.required[0]]) + '×' + String(p[sig.required[1]]);
  for (i = 0; i < sig.required.length; i++) parts.push(sig.required[i] + ' ' + p[sig.required[i]]);
  return parts.join(' · ');
}
function paramsLabel(params) {
  var out = [], k;
  for (k in params) if (has(params, k)) {
    out.push(k + '=' + (Array.isArray(params[k]) ? '[' + params[k].length + ']' : Q.pyRepr(params[k])));
  }
  return out.join(', ');
}
// the cards themselves, one builder for the rail and the stage
function startCards(where) {
  var gens = Q.generators(), i, h = '<div class="cards" data-start="' + where + '">';
  h += '<button class="card2" data-blank="1"' + (where === 'rail' ? ' id="palBlank"' : '') +
       ' title="an empty canvas on the chosen physics package"><b>Blank canvas</b>' +
       '<span class="sub">place sites by hand</span></button>';
  for (i = 0; i < gens.length; i++) {
    h += '<button class="card2" data-gen="' + esc2(gens[i]) + '" title="Machine.' +
         esc2(gens[i]) + '(' + esc2(paramsLabel(generatorParams(gens[i]))) + ')"><b>' +
         esc2(gens[i]) + '</b><span class="sub">' + esc2(generatorLabel(gens[i])) + '</span></button>';
  }
  return h + '</div>';
}
var NAME_EDIT = null;              // what was typed into #palName, or null for the default
// A CLEARED BOX IS AN UNTOUCHED BOX: the generator or template stem names the device,
// and a typed name replaces it.  The box itself is empty until something is typed, so
// the placeholder can say the rule instead of a value the user has to delete first.
function typedName() { var t = NAME_EDIT === null ? '' : String(NAME_EDIT).trim(); return t || null; }
// what typing into #palName does, callable without an input event
function setName(text) {
  NAME_EDIT = (text === null || text === undefined) ? null : String(text);
  var b = $('palName');
  if (b) b.value = NAME_EDIT || '';
  return typedName();
}
function defaultName(gen) { return typedName() || gen || 'design'; }
// the physics package, as one <select>: `data-start="tpl"`, the picked stem selected
function packageSelect(id) {
  var stems = Q.templates(), td = D.template_devices || {}, i;
  var on = PICKED_TEMPLATE || Q.templateDefault();
  var h = '<select id="' + id + '" data-start="tpl" title="the zones, curves and control ' +
          'block a new device borrows">';
  for (i = 0; i < stems.length; i++) {
    h += '<option value="' + esc2(stems[i]) + '"' + (on === stems[i] ? ' selected' : '') + '>' +
         esc2(stems[i]) +
         (td[stems[i]] ? ' · ' + esc2(td[stems[i]].generator) + ' ' +
                         esc2(paramsLabel(td[stems[i]].params)) : '') +
         '</option>';
  }
  return h + '</select>';
}
// the shipped devices, as one <select> and the button that opens the chosen one; `short`
// lists the stems alone, for the 224 px rail
function openPicker(id, short) {
  var stems = Q.templates(), td = D.template_devices || {}, i;
  var h = '<select id="' + id + '" data-start="open" title="a device this page ships, ' +
          'opened as itself on its own physics">';
  for (i = 0; i < stems.length; i++) {
    if (!td[stems[i]]) continue;
    h += '<option value="' + esc2(stems[i]) + '">' + esc2(stems[i]) +
         (short ? '' : ' · ' + esc2(td[stems[i]].generator) + ' ' +
                       esc2(paramsLabel(td[stems[i]].params))) +
         '</option>';
  }
  return h + '</select><button data-open-pick="' + id + '" title="open the chosen device">' +
         'open</button>';
}
// THE SHAPE BUTTONS ON THE EMPTY CANVAS.  Same verb as the tools bar and the rail; the
// empty state is where a first-time reader is looking, so the shapes are named there in
// the words the request used.
function shapeButtons() {
  var h = '', i;
  for (i = 0; i < SK_ORDER.length; i++) {
    var t = SK_ORDER[i];
    h += '<button data-shape="' + t + '" data-hint="shape:' + t + '" aria-pressed="' +
         (SKETCH === t ? 'true' : 'false') + '" title="' + esc2(SHAPE_DOC[t][1]) + '">' +
         esc2(SHAPE_DOC[t][0]) + '</button>';
  }
  return h;
}
// the two stamps a hand-built device starts with, armed from the stage itself
function armButtons() {
  var kinds = [['site', 'Trapping site'], ['junction', 'Junction']], h = '', i;
  for (i = 0; i < kinds.length; i++) {
    h += '<button data-arm="' + kinds[i][0] + '" aria-pressed="' +
         (ARMED_EL === kinds[i][0] ? 'true' : 'false') + '" title="arm the ' + kinds[i][1] +
         ' stamp, then click the canvas to place one">' + kinds[i][1] + '</button>';
  }
  return h;
}
function wireStartControls(host) {
  if (!host || !host.querySelectorAll) return;
  var kids = host.querySelectorAll('button'), i;
  for (i = 0; i < (kids.length || 0); i++) {
    var b = kids[i];
    if (b.getAttribute('data-open-pick')) wireStartOpen(b);
    else if (b.getAttribute('data-arm')) wireArmButton(b);
    else if (b.getAttribute('data-shape')) wireShapeButton(b);
    else wireStartButton(b);
  }
  var sels = host.querySelectorAll('select');
  for (i = 0; i < (sels.length || 0); i++) {
    if (sels[i].getAttribute('data-start') === 'tpl') wireStartSelect(sels[i]);
  }
}
function wireShapeButton(b) {
  if (!b || !b.addEventListener) return;
  b.addEventListener('click', function () {
    sketchTool(b.getAttribute('data-shape'));
    unfocus(b);
  });
}
function renderStart() {
  var host = $('palStartBody'), empty = $('stageEmpty');
  var n = STATE ? nodesOf(STATE).length : 0;
  if (host) {
    var h = '';
    h += '<h5>Draw the shape your ions travel on</h5>' +
         '<div class="startrow">' + shapeButtons() + '</div>' +
         '<div class="mut" style="margin:5px 0 9px;font-size:11px">drag one on the canvas: ' +
         'the release lays trapping sites along it one lattice unit apart, declares the ' +
         'orbit if the shape closed, and adds a junction where the new rail meets an ' +
         'existing one. A corner is a <b>bend</b> (R18), not a junction.</div>';
    h += '<div class="fieldrow"><label>physics</label>' + packageSelect('palTpl') + '</div>';
    h += '<div class="fieldrow"><label>name</label>' +
         '<input id="palName" value="' + esc2(typedName() || '') +
         '" placeholder="named after the card you pick, unless you type one"></div>';
    h += '<h5>Build a device</h5>' + startCards('rail');
    h += '<div class="fieldrow"><label>open</label>' + openPicker('palOpen', true) + '</div>';
    h += '<div class="mut" style="margin-top:5px;font-size:11px">a blank canvas borrows the ' +
         'selected package’s curves, zone types and control block, because a device with ' +
         'no <code>shuttle_segment</code> curve cannot be priced at all.</div>';
    host.innerHTML = h;
    wireStartControls(host);
    var nameBox = $('palName');
    if (nameBox && nameBox.addEventListener) {
      nameBox.addEventListener('input', function () { NAME_EDIT = nameBox.value; });
    }
  }
  // THE FOLD IS THE USER'S: `open` is whatever they last set it to.  It used to open
  // itself on an empty stage, and being 900 px tall it pushed the element tiles the
  // empty state pointed at off every laptop screen.  The stage carries the start now.
  if (empty) {
    empty.innerHTML = n ? '' :
      '<h3>Draw the shape your ions travel on</h3>' +
      '<div class="startrow"><span class="mut">press a shape, then drag on the canvas:</span>' +
      shapeButtons() + '</div>' +
      '<p class="mut">the release lays trapping sites along what you drew, one lattice ' +
      'unit apart \u00b7 a closed shape also declares the orbit the machine can rotate \u00b7 ' +
      'a corner is a <b>bend</b>, which R18 prices as ordinary transport, not a junction ' +
      '\u00b7 a junction is added by itself where the new rail meets or crosses one that is ' +
      'already there.</p>' +
      '<h3>or start from a ready-made device</h3>' + startCards('stage') +
      '<div class="startrow"><label>physics package ' + packageSelect('stageTpl') + '</label>' +
      '<label>shipped device ' + openPicker('stageOpen') + '</label></div>' +
      '<div class="startrow"><span class="mut">or build one by hand (Parts): press</span>' + armButtons() +
      '<span class="mut">then click the canvas — a double-click on empty canvas also ' +
      'places a site; shift-drag one site onto another joins them.</span></div>' +
      '<p class="mut">a generator device comes with a <b>Test drive</b> programme and ' +
      'animates at once.</p>' +
      '<p class="mut">New here? Hover anything for what it is, press <b>Explain</b> (top right) to label ' +
      'the parts of the screen, or <b>?</b> for the guide.</p>';
    wireStartControls(empty);
  }
}

// OPEN A SHIPPED DEVICE AS ITSELF.  `newCanvas({template})` yields an EMPTY canvas on
// that package, which is not what "grid9x9" on a card promises; the device is
// `from_template` with the generator and parameters the page ships for that stem.
function newFromTemplate(stem, opts) {
  opts = opts || {};
  var td = (D.template_devices || {})[String(stem)];
  if (!td) {
    return { ok: false, problems: [{ code: 'no_template_device',
      message: 'this page carries no device for template ' + Q.pyRepr(String(stem)) +
               '; have: ' + Object.keys(D.template_devices || {}).sort().join(', ') }] };
  }
  var params = {};
  for (var k in td.params) if (has(td.params, k)) params[k] = td.params[k];
  return newFromGenerator(td.generator, params,
                          { name: opts.name === undefined ? String(stem) : opts.name,
                            template: String(stem) });
}

var PICKED_TEMPLATE = null;
// A START CARD, callable without an Event: `{tpl}` picks the physics package; `{open}`,
// `{gen}` or `{blank}` makes the device, named after what made it unless a name was
// typed, and drives it at once.  A blank canvas has nothing to drive and that refusal is
// silent: the status strip already says "no programme yet".
function pressStartCard(card) {
  card = card || {};
  var tpl = card.tpl, gen = card.gen, open = card.open;
  if (tpl) {
    PICKED_TEMPLATE = String(tpl);
    toast('ok', 'physics package: ' + tpl);
    renderStart();
    return { ok: true, tpl: PICKED_TEMPLATE, r: null, drive: null, name: null };
  }
  var nm = defaultName(gen || open || null);
  var r;
  if (open) r = newFromTemplate(open, { name: nm });
  else if (gen) r = newFromGenerator(gen, generatorParams(gen), { name: nm, template: PICKED_TEMPLATE });
  else r = newCanvas({ name: nm, template: PICKED_TEMPLATE });
  if (!r.ok) {
    toast('bad', (r.problems[0] || {}).message || 'refused');
    return { ok: false, r: r, drive: null, name: nm };
  }
  var d = testDrive();
  toast('ok', (open ? ('opened ' + open) : gen ? ('new ' + gen) : 'blank canvas') +
              (d.ok ? (' · test drive: ' + d.frames + ' frames') : ''));
  return { ok: true, r: r, drive: d, name: nm };
}
function wireStartButton(b) {
  if (!b || !b.addEventListener) return;
  b.addEventListener('click', function () {
    pressStartCard({ gen: b.getAttribute('data-gen'), open: b.getAttribute('data-open') });
  });
}
// the <select> for the physics package: choosing is the press
function wireStartSelect(sel) {
  if (!sel || !sel.addEventListener) return;
  sel.addEventListener('change', function () { pressStartCard({ tpl: sel.value }); });
}
// the "open" button beside a shipped-device <select>, named by `data-open-pick`
function wireStartOpen(b) {
  if (!b || !b.addEventListener) return;
  b.addEventListener('click', function () {
    var sel = $(b.getAttribute('data-open-pick'));
    if (sel && sel.value) pressStartCard({ open: sel.value });
  });
}
// the stage's own "Trapping site" / "Junction": the palette tile's verb, from the stage
function wireArmButton(b) {
  if (!b || !b.addEventListener) return;
  b.addEventListener('click', function () { arm(b.getAttribute('data-arm')); });
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
  var reg = { SEGEL: {}, SEGINFO: {}, PAD_BY_SEG: {}, PAD_BY_SITE: {}, SITE_SPAN: {},
              SEG_BY_PAIR: {}, NODEEL: {}, CAPTXT: {} };

  // >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>> THE ONE CALL <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
  buildStatic({ A: { nodes: nodes, segments: segments, loops: D0.loops },
                L: LA, AXIS: AX, role: ROLE, px: pxA, py: pyA, byId: byId,
                into: into, reg: reg });
  // >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>><<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<

  // one pad energized, so "control plane" shows what it physically means: the electrodes
  // and the DACs that ramp them.  The stage's own `lastHot` mark, not a second one.
  if (opt.hot) {
    // A PAIR, as the stage lights a pair: both electrodes across the RF null at one
    // position.  The registry holds pair records now, so the avatar reads pair.pads.
    var pads = reg.PAD_BY_SEG[D0.onSeg || 's0'] || [];
    for (i = 0; i < pads.length; i++) {
      if (Math.abs(pads[i].t - 0.5) >= 0.2) continue;
      for (var hp = 0; hp < pads[i].pads.length; hp++) {
        pads[i].pads[hp].el.setAttribute('fill', C.dc_hot);
        pads[i].pads[hp].el.setAttribute('opacity', 0.95);
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
      // cannot tell `spur_dock` from `trap_junction` because both call their first pin
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
  // the same refusal a single site gets: a part dropped twice on one point would put
  // eight pairs of nodes on eight points, and nothing downstream would say so
  for (var oi = 0; oi < ops.length; oi++) {
    if (ops[oi].method !== 'd.site' && ops[oi].method !== 'd.junction') continue;
    var coin = coincidentAt(Number(Q.unbox(ops[oi].args[1])), Number(Q.unbox(ops[oi].args[2])));
    if (coin) return { ok: false, problems: [coin] };
  }
  var r = transaction(ops.map(function (o) { return { build: o }; }), 'place ' + name);
  r.instance = inst;
  return r;
}

// ---- PINS: what turns placed parts into an assembled machine -------------------
//
// A component declares PINS -- named nodes it expects to be joined to something else. An
// `spur_dock` sitting next to a rail is two disconnected pieces; joined at its `rail`
// pin it is a dock, and the rail node becomes degree 3, which is what makes the cost model
// charge a junction on every rigid hop through it (R18). The difference between placing
// parts and assembling a machine is exactly this call.
//
// A pin is joined with an ORDINARY SEGMENT, not a merge: two nodes that coincide would
// make `min_nearest_neighbour` measure the gap off the next pair and silently resize every
// mark on the stage, which `addNodeAt` already refuses for the same reason.
// WHICH COMPONENT, AND AT WHICH VARIANT. This used to recover the answer by probing for
// `inst + '.' + CMP[k].pins[0].node` and taking the first catalogue entry that matched --
// which is wrong twice over. `spur_dock` and `trap_junction` BOTH call that node 'j'
// and `CMP` is in sorted order, so every pin of a placed `trap_junction` resolved against
// `spur_dock` and came back `no_pin`: the component whose whole purpose is "attach all
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

// THE SHAPE GROUP: what the rail leads with in Sketch mode.  The four tools, the sentence
// that says what each one draws, and the one gesture that draws it -- the same buttons as
// the ones in the tools bar, wired to the same verb, so neither can be armed while the
// other says it is not.
var SHAPE_DOC = {
  rect: ['Rectangle', 'drag corner to corner', 'a closed rectangular rail: sites one lattice unit apart, a site at each corner'],
  ellipse: ['Ellipse / circle', 'drag a box \u00b7 shift for a circle', 'a closed rounded rail, polygonised at one side per lattice unit'],
  line: ['Line', 'drag end to end', 'an open register: no orbit to rotate'],
  poly: ['Polyline', 'click, click, click', 'double-click or enter ends it open \u00b7 the first point again closes it']
};
function shapeSection() {
  var sec = elh('div', 'palgrp'), i;
  sec.setAttribute('data-kind', 'shape');
  var h = elh('h5');
  h.textContent = 'Draw the shape your ions travel on';
  sec.append(h);
  var grid = elh('div', 'palgrid');
  for (i = 0; i < SK_ORDER.length; i++) {
    (function (t) {
      var doc = SHAPE_DOC[t], b = elh('button', 'pal-item');
      b.setAttribute('data-el', 'shape:' + t);
      b.setAttribute('data-kind', 'shape');
      b.setAttribute('data-hint', 'shape:' + t);
      b.setAttribute('aria-pressed', SKETCH === t ? 'true' : 'false');
      var txt = elh('span', 'pal-text');
      var nb = elh('b');
      nb.textContent = doc[0];
      var why = elh('i', 'pal-why');
      why.textContent = doc[2];
      var how = elh('i', 'pal-how');
      how.textContent = doc[1];
      txt.append(nb, why, how);
      b.append(txt);
      if (b.addEventListener) b.addEventListener('click', function () { sketchTool(t); unfocus(b); });
      grid.append(b);
    }(SK_ORDER[i]));
  }
  sec.append(grid);
  return sec;
}

function renderPalette() {
  var host = $('palBody');
  if (!host) return;
  var pal = palette(), by = { stamp: [], named: [], row: [], block: [] }, i;
  for (i = 0; i < pal.length; i++) (by[pal[i].kind] || (by[pal[i].kind] = [])).push(pal[i]);
  host.replaceChildren();
  // SKETCH MODE LEADS WITH THE SHAPES and puts the element tiles under "fill in the
  // details" -- reachable, and second.  PARTS MODE IS EXACTLY WHAT IT WAS: the same
  // sections, in the same order, with no extra heading.
  if (designMode() === 'sketch') {
    host.append(shapeSection());
    var det = elh('h5', 'palnext');
    det.textContent = 'or fill in the details by hand';
    host.append(det);
  }
  var order = ['stamp', 'named', 'row', 'block'];
  for (i = 0; i < order.length; i++) {
    if (!by[order[i]] || !by[order[i]].length) continue;
    var sec = paletteSection(order[i], by[order[i]]);
    host.append(has(PAL_OPEN, order[i]) ? foldSection(order[i], sec, by[order[i]].length) : sec);
  }
  // A THROW HERE USED TO ABORT THE WHOLE BAR. `paint()` guards only the export box, so
  // an exception from a tile skipped `renderInspector`, `renderWrite` and `renderReport`
  // -- and kept skipping them, because whatever caused it persisted.
  // "explode" acts on placed components, so it lives in their fold, last
  var ex = elh('button', 'tool');
  ex.setAttribute('id', 'palExplode');
  ex.setAttribute('data-hint', 'explode');
  ex.textContent = 'explode to explicit…';
  wirePaletteButton(ex);
  try {
    var cmp = componentSection();
    if (cmp) {
      var ncmp = cmp.querySelectorAll ? cmp.querySelectorAll('.pal-item').length : 0;
      cmp.append(ex);
      host.append(foldSection('component', cmp, ncmp));
    } else host.append(ex);
  } catch (err) {
    var oops = elh('div', 'palgrp');
    var oh = elh('h5');
    oh.textContent = SECTION.component;
    var msg = elh('i', 'pal-why');
    msg.textContent = 'the component menu could not be drawn: ' +
                      String(err && err.message ? err.message : err);
    oops.append(oh, msg);
    host.append(oops, ex);
  }
  // the page's tools bar shows the folds as popovers and re-applies the open one here
  if (typeof window !== 'undefined' && typeof window.onPalette === 'function') { try { window.onPalette(); } catch (err) { /* page hook */ } }
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
  b.setAttribute('data-hint', 'el:' + e.type);
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
    b.setAttribute('data-hint', 'el:zone_type');
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
  add.setAttribute('data-hint', 'zone:new');
  add.textContent = '+ new zone type';
  wireZoneChip(add);
  strip.append(add);
  return strip;
}

// ---- arming ---------------------------------------------------------------------------
var ARMED_EL = null;
var NEW_ZONE = null;              // the zone the next placed site inherits
function armed() { return ARMED_EL; }
// THE GHOST BELONGS TO THE ARMED TILE: whenever the armed element changes -- to another
// tile, or to nothing -- the preview drawn for the old one goes with it, and the next
// pointer move begins a ghost of the new one.  Without this the stage kept drawing a SITE
// after the user had clicked Junction, and drew a ghost for a tile no longer armed.
function setArmed(type) {
  if (ARMED_EL === type) return;
  ARMED_EL = type;
  ghostCancel();
}
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
    setArmed((ARMED_EL === type) ? null : type);
    FORM = null;
    paint();
    return ARMED_EL ? { type: ARMED_EL, kind: 'component', verb: 'component' } : null;
  }
  var e = type ? paletteEntry(type) : null;
  if (type && !e) return null;
  // A NAMED / ROW / BLOCK ELEMENT IS NEVER PLACED, so clicking it opens its form rather
  // than arming a stage gesture it does not have.  Arming all eleven and honouring two of
  // them is what made the old menu feel broken: arm `loop`, double-click, get a SITE.
  if (e && e.kind !== 'stamp') {
    setArmed(null);
    FORM = (FORM && FORM.type === type) ? null : { type: type };
    paint();
    return FORM ? { type: type, kind: e.kind, verb: e.verb, defaults: e.defaults } : null;
  }
  setArmed((ARMED_EL === type || !type) ? null : type);
  FORM = null;
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
// The three things a POINT places: a site, a junction, a whole component.  A segment and
// a loop are stamps too but have no single-point gesture, so they are not ghosted.
function isStampType(t) {
  return t === 'site' || t === 'junction' || String(t).slice(0, 4) === 'cmp:';
}

// The ghost's scene in DEVICE units at (x, y): one node for a site or a junction, and for
// a component the very records `stampComponent` will replay, translated the way
// `cmpInstantiate` translates them (quarter 0).  `componentScene` is what the menu tile
// is drawn from, so the tile, the ghost and the placed part are one description.
function ghostScene(type, x, y) {
  var nodes = [], segs = [], loops = {}, i;
  if (String(type).slice(0, 4) === 'cmp:') {
    var sc = componentScene(String(type).slice(4));
    if (!sc) return null;
    for (i = 0; i < sc.nodes.length; i++) {
      var q = sc.nodes[i], p2 = cmpTranslate(q[1], q[2], x, y, 0);
      nodes.push([q[0], p2[0], p2[1], q[3], q[4], q[5]]);
    }
    segs = sc.segs; loops = sc.loops;
  } else {
    var z = defaultZone();
    var cap = (A.zone_types && A.zone_types[z] && A.zone_types[z].capacity) || 1;
    nodes.push(['__ghost', x, y, type, type === 'site' ? z : null, type === 'site' ? cap : 0]);
  }
  return { nodes: nodes, segs: segs, loops: loops };
}

function ghostMarks(type, x, y, scene) {
  var g = ghostGroup();
  if (!g) return;
  clearGroup(g);
  var sc = scene || (isStampType(type) ? ghostScene(type, x, y) : null);
  if (!sc) return;
  // the same drawing shape `syncArch` writes and `elementAvatar` builds, at STAGE scale
  var byId = {}, nodes = [], segments = [], i, k;
  for (i = 0; i < sc.nodes.length; i++) {
    var q = sc.nodes[i], isJ = q[3] === 'junction', deg = 0;
    for (k = 0; k < sc.segs.length; k++) if (sc.segs[k][1] === q[0] || sc.segs[k][2] === q[0]) deg++;
    var n = { id: q[0], x: q[1], y: q[2], kind: isJ ? 'junction' : 'site',
              zone: isJ ? null : (q[4] === undefined || q[4] === null ? defaultZone() : q[4]),
              cap: isJ ? 0 : (q[5] === undefined ? 1 : q[5]),
              deg: deg, corner: false, labels: [], cap_explicit: true };
    byId[q[0]] = n; nodes.push(n);
  }
  for (i = 0; i < sc.segs.length; i++) {
    segments.push({ id: sc.segs[i][0], a: sc.segs[i][1], b: sc.segs[i][2], loop: null,
                    labels: [], cap: 1, len: 1, corner_endpoints: 0 });
  }
  var into = { loop: el('g', {}), seg: g, elec: el('g', {}), node: g };
  buildStatic({ A: { nodes: nodes, segments: segments, loops: sc.loops }, L: L,
                AXIS: axisOf(nodes, segments, px, py, byId), role: {}, px: px, py: py,
                byId: byId, into: into,
                reg: { SEGEL: {}, SEGINFO: {}, PAD_BY_SEG: {}, SEG_BY_PAIR: {},
                       NODEEL: {}, CAPTXT: {} } });
}
// A STAMP LANDS ON THE LATTICE, always: `snapTo(..., hard)`.  There is no drag whose
// intent a window could be preserving, and a stamp 0.3 of a step off the grid is the
// one placement that quietly shrinks every mark on the stage.
function ghostPoint(mx, my) {
  // on the lattice only while Snap is on: a stamp is free by the same rule a drag is
  return snapTo((mx - L.ox) / (L.sx || 1), (my - L.oy) / (L.sy || 1), false, false, SNAP);
}
function setSnap(on) {
  SNAP = !!on;
  try { STORE.setItem(SNAP_KEY, SNAP ? '1' : '0'); } catch (err) { /* no store */ }
  paint();
  return SNAP;
}
function ghostBegin(type, mx, my) {
  if (String(type).slice(0, 4) === 'cmp:') {
    if (!CMP[String(type).slice(4)]) return null;
  } else {
    var e = paletteEntry(type);
    if (!e || e.kind !== 'stamp') return null;
  }
  ARMED_EL = type;
  var sn = ghostPoint(mx, my);
  PGHOST = { type: type, mx: mx, my: my, x: sn.x, y: sn.y, guides: sn.guides,
             valid: true, why: null };
  ghostMarks(type, PGHOST.x, PGHOST.y);
  return PGHOST;
}
function ghostMove(mx, my) {
  if (!PGHOST) return null;
  var raw = { x: (mx - L.ox) / (L.sx || 1), y: (my - L.oy) / (L.sy || 1) };
  var sn = ghostPoint(mx, my);
  // THE SNAPPED POINT IS WHAT LANDS, so the ghost's verdict is read there and not under
  // the raw pointer: a hard snap can put the stamp on a node the pointer is 0.35 of a
  // step away from, outside its hit halo.  `coincidentAt` over the ghost's own scene is
  // the check the drop makes (`addNodeAt`, `addSite`, `stampComponent`), for every node
  // of a component as much as for one site, so the preview and the refusal agree.
  var sc = ghostScene(PGHOST.type, sn.x, sn.y), coin = null, h = null, i;
  for (i = 0; sc && i < sc.nodes.length && !coin; i++) coin = coincidentAt(sc.nodes[i][1], sc.nodes[i][2]);
  if (!coin && (PGHOST.type === 'site' || PGHOST.type === 'junction')) h = hit(mx, my);
  // the boundary: the stamp's own box against every mark on the stage
  var over = null;
  for (i = 0; !coin && !h && sc && i < sc.nodes.length && !over; i++) {
    var q = sc.nodes[i];
    over = stampContact({ id: q[0], kind: q[3], cap: q[5] || 0 }, q[1], q[2]);
  }
  PGHOST.x = sn.x; PGHOST.y = sn.y; PGHOST.mx = mx; PGHOST.my = my;
  PGHOST.guides = sn.guides;
  PGHOST.valid = !coin && !h && !over;
  PGHOST.why = coin ? coin.message : h ? (h.id + ' is already here')
            : over ? ('would overlap ' + over + ' \u2014 marks do not overlap') : null;
  PGHOST.on = coin ? coin.targets[0] : h ? h.id : over;
  ghostMarks(PGHOST.type, sn.x, sn.y, sc);
  return { x: sn.x, y: sn.y, snapped: (sn.x !== raw.x) || (sn.y !== raw.y),
           guides: sn.guides, valid: PGHOST.valid, why: PGHOST.why, on: PGHOST.on };
}
// THE ONE DROP for everything a point places: the armed click, the double-click and the
// harness's `stamp` step all come through here, so a site and a whole component are
// placed by one path and cannot be refused for different reasons.
function ghostDrop() {
  if (!PGHOST) return { ok: false, problems: [{ code: 'no_ghost',
    message: 'nothing is being placed' }] };
  var t = PGHOST.type, x = PGHOST.x, y = PGHOST.y, why = PGHOST.valid ? null : PGHOST.why;
  ghostCancel();
  // what the ghost said could not land does not land: on a mark, or overlapping one
  if (why) {
    return { ok: false, problems: [{ code: /overlap/.test(why) ? 'overlap' : 'blocked', message: why }] };
  }
  if (String(t).slice(0, 4) === 'cmp:') {
    var r = stampComponent(String(t).slice(4), x, y, 0);
    if (r.ok === false) return r;
    r.ok = true;
    return r;
  }
  return placeStamp(t, x, y);
}
function ghostCancel() { PGHOST = null; clearGroup(ghostGroup()); }

// A CLICK ON THE STAGE, callable without an Event.  With a stamp armed and nothing under
// the pointer it PLACES -- the ghost is put where the press was and dropped, so the click
// and the hover preview cannot disagree about where the element lands.  `segment` and
// `loop` say what their gesture is on the first click instead of silently doing nothing.
// Otherwise it selects: click = this one, shift-click = toggle, click empty = clear.
function clickStage(mx, my, mods) {
  mods = mods || {};
  var h = hit(mx, my);
  if (!h && ARMED_EL) {
    // `mods.quiet`: the double-click's own two clicks have already said this
    if (ARMED_EL === 'segment') {
      if (!mods.quiet) toast('warn', 'a segment joins two nodes: drag from one node to another');
      return { ok: false, placed: null, problems: [{ code: 'gesture', message: GESTURE.segment }] };
    }
    if (ARMED_EL === 'loop') {
      if (!mods.quiet) toast('warn', 'a loop is a walk over nodes that already exist: select them in ' +
                                     'orbit order, then press Close loop');
      return { ok: false, placed: null, problems: [{ code: 'gesture', message: GESTURE.loop }] };
    }
    if (isStampType(ARMED_EL)) {
      var t = ARMED_EL;
      if (!PGHOST || PGHOST.type !== t) { ghostCancel(); ghostBegin(t, mx, my); }
      ghostMove(mx, my);
      // THE LATTICE POINT, NOT THE MARK RADIUS, IS WHAT A STAMP CLICK MEANS: the second
      // click of a double-click lands on the point the first one filled, 28 px from a
      // 9 px mark, so `hit()` sees nothing and the placement would be refused out loud
      // twice.  A single node aimed at a point that holds one is a click on that node.
      if (PGHOST && PGHOST.on && (t === 'site' || t === 'junction')) {
        var on = PGHOST.on;
        setSelection([{ kind: 'node', id: on }]);
        if (typeof selectRef === 'function') selectRef('site', on);
        return { ok: true, placed: null, selected: SELSET.slice() };
      }
      var pr = ghostDrop();
      if (!pr.ok) {
        var p0 = pr.problems[0] || {};
        // a component is builder statements, and a generator device has no builder: say
        // what to do rather than leak the interpreter's `no_builder`
        toast('bad', p0.code === 'no_builder'
          ? 'this device came from a generator, so a part cannot be added to it as builder ' +
            'statements -- press "explode to explicit…" at the bottom of Elements first'
          : (p0.message || 'refused'));
      } else {
        // ONE PLACEMENT PER ARMING.  The tile disarms the moment its element lands, so the
        // pointer goes back to selecting and dragging and a stray click cannot drop a
        // second site nobody asked for; press the tile again to place another, or hold
        // shift to stay armed for a run of them (asked for 2026-09-17).
        if (String(t).slice(0, 4) === 'cmp:') toast('ok', 'placed ' + String(t).slice(4));
        if (!mods.shift) arm(null);
      }
      pr.placed = t;
      return pr;
    }
  }
  if (!h) { setSelection([]); return { ok: true, placed: null, selected: [] }; }
  if (mods.shift) {
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
  if (typeof selectRef === 'function') selectRef(h.kind === 'segment' ? 'segment' : 'site', h.id);
  return { ok: true, placed: null, selected: SELSET.slice() };
}

// A DOUBLE-CLICK ON EMPTY STAGE PLACES A SITE.  With a stamp armed the two single clicks
// that precede it have already placed through `clickStage` (the first placed, the second
// selected what it placed), so the double-click itself does nothing more; a segment or
// a loop armed gets its guidance, quietly, since the two clicks said it.  Otherwise this
// is the un-armed gesture, and it is THE SAME APPLIER AS THE ARMED CLICK: `placeStamp`,
// which hoists a `d.site` into the builder when the device has one and falls back to a
// post-seal `add_site` when it does not.  It used to choose by node count -- the first
// double-click through the builder, every later one through `add_site` -- so N0 lived
// above the seal and N1 below it, and the segment joining them was refused with
// "unknown endpoint 'N1'" on the very page the empty state points at.  One device, one
// applier, one rule.
function dblclickStage(mx, my) {
  if (VIEW_ONLY) return;
  if (hit(mx, my)) return null;
  if (isStampType(ARMED_EL)) return null;
  if (ARMED_EL) return clickStage(mx, my, { quiet: true });
  var s = ghostPoint(mx, my);
  // THE CLICK THAT PLACED HAS ALREADY RUN.  A stamp disarms as soon as it lands, so the
  // second click of a double-click arrives with nothing armed and the point under it
  // holds the node the first click placed: placing again there is a coincident refusal
  // the user never asked for.  Selecting what is there is what a click on it would do.
  var coin = coincidentAt(s.x, s.y);
  if (coin && coin.targets && coin.targets.length) {
    var onId = coin.targets[0];
    setSelection([{ kind: 'node', id: onId }]);
    if (typeof selectRef === 'function') selectRef('site', onId);
    return { ok: true, placed: null, selected: SELSET.slice() };
  }
  var res = placeStamp('site', s.x, s.y);
  if (!res.ok) toast('bad', (res.problems[0] || {}).message || 'refused');
  return res;
}

// THE ONE PLACEMENT CALL, so the double-click, the armed click and the harness verb all
// land on the same defaults.  `palette().defaults.node.capacity` is 0 and a capacity of 0
// is refused -- so a form built from the defaults alone fails on every drop; `addNodeAt`
// supplies 1 when neither a zone nor a capacity is given, and a named zone supplies its
// own.
function placeStamp(type, x, y) {
  var z = defaultZone();
  // A GENERATOR DEVICE HAS NO BUILDER, so there is nothing to hoist a `d.site` into and
  // the builder verb is refused with `no_builder` -- on every shipped page.  The node is
  // a topology edit instead, applied AFTER the seal (so any declared zone is usable) and
  // exported through the method whitelist: the same fallback `joinNodes` already makes
  // to `addSegment`.  One gesture, the applier the device actually has.
  if (!hasBuilder()) {
    return addSite(x, y, null, { kind: type, zone: type === 'site' ? (z || null) : undefined });
  }
  if (type === 'junction') return addNodeAt(x, y, { kind: 'junction' });
  if (z && postSeedZones()[z]) {
    return { ok: false, problems: [{ code: 'zone_after_seal', targets: [z],
      message: "zone '" + z + "' was added after this device was sealed, so a new site " +
               'cannot use it — pick another zone chip.' }] };
  }
  return addNodeAt(x, y, { kind: 'site', zone: z || undefined });
}

// WHICH ZONE TYPES THE SEAL DECLARES, read off the seed record itself.  `blank` and
// `blank_device` carry them as `zones=` (names or a block); a `blank_device` with none
// infers them from the builder's own sites, as Python's `_zonesOfDevice` does; a template
// seed (`from_template`, `from_device`) borrows every zone the template's records declare.
// Nothing is replayed to find out: the answer is in the record.
function sealZones() {
  var out = {}, i, k;
  if (!SEED) return out;
  var kw = SEED.kwargs || {};
  if (SEED.method === 'blank' || SEED.method === 'blank_device') {
    var zs = kw.zones;
    if (Array.isArray(zs)) {
      for (i = 0; i < zs.length; i++) out[String(zs[i])] = true;
    } else if (zs && typeof zs === 'object') {
      for (k in zs) if (has(zs, k)) out[k] = true;
    } else if (SEED.method === 'blank_device') {
      var calls = baseCallsFrom(GEOM, null, [], EDITS);
      for (i = 0; i < calls.length; i++) {
        var c = calls[i], cz = c.kwargs && c.kwargs.zone;
        if (c.method === 'd.site' && cz !== undefined && cz !== null) out[String(Q.unbox(cz))] = true;
      }
    }
    return out;
  }
  var key = (kw.template === undefined || kw.template === null || String(kw.template) === '')
    ? Q.templateDefault() : String(kw.template);
  var recs = ((D.templates || {})[key]) || [];
  for (i = 0; i < recs.length; i++) {
    var r = recs[i];
    if (r && r.method === 'set_zone' && r.args && r.args.length) out[String(r.args[0])] = true;
  }
  return out;
}

// WHICH ZONE TYPES A NEW SITE CANNOT USE.  `set_zone` is a post-seal MUTATE and `d.site`
// is a pre-seal BUILD, and `baseCallsFrom` hoists every build above the seed -- so a zone
// type you name in the studio exists in the browser's state and is refused by the
// expansion the moment you put a site in it.  That is a real constraint of the file
// format, not of this menu, so the menu says so instead of offering the trap.
//
// Read off the LISTING, which is the only thing that knows the order: any `set_zone`
// after the seed that the seed itself did not declare.  Every shipped listing retunes
// each zone with `set_zone` after the seal -- which is legal for a zone the seal already
// named -- and counting those as "added after the seal" is what refused the first click
// on "Trapping site" on every shipped page (measured: `zone_after_seal` on all four).
function postSeedZones() {
  var out = {}, i, recs = POST.concat(EDITS), pre = sealZones();
  for (i = 0; i < recs.length; i++) {
    var r = recs[i];
    if (r && r.method === 'set_zone' && r.args && r.args.length && !pre[String(r.args[0])]) {
      out[String(r.args[0])] = true;
    }
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

// THE ZONE A NEW SITE GETS when nothing says otherwise: the chip the user picked, else
// the zone of the first node on the stage, else the first declared zone type by name.
// One rule, read by the double-click, the ghost and the armed stamp alike.
// WHAT ZONE A HAND-PLACED SITE LANDS IN.  The fallback used to be
// `Object.keys(zone_types).sort()[0]`, which is alphabetical order, which on every shipped
// package is `ancilla` -- so the first site anyone placed on an empty canvas came out as a
// code's ancilla rather than as a trap.  Alphabetical order is not a preference, it is an
// accident of spelling.  Prefer the zones that describe HARDWARE, most general first: a
// plain trap, then storage, then a gate-only zone, then the loading zone.  Anything else
// (a code's own vocabulary, or a name this device invented) is used only when the package
// declares nothing better, and `nearestZone()` still wins when there is a device to copy.
var ZONE_PREFERENCE = ['trap', 'data', 'gate', 'load'];
function defaultZone() {
  var near = nearestZone();
  if (near) return near;
  var have = (STATE && STATE.zone_types) || {};
  for (var i = 0; i < ZONE_PREFERENCE.length; i++) {
    if (has(have, ZONE_PREFERENCE[i])) return ZONE_PREFERENCE[i];
  }
  return Object.keys(have).sort()[0] || null;
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

// ============================================================ THE RIGHT-CLICK MENU
//
// WHAT IS UNDER THE POINTER, AND EVERY VERB THAT APPLIES TO IT.  Until this existed the
// only way to reach a site's capacity was the Selection popover, which opened itself on
// every click and was the busiest thing on the page; the only way to change a zone was to
// retype the statement in the text lane; and a rail could not be subdivided at all
// without knowing that `add_site(on=...)` existed.
//
// IT ADDS NO VERB AND NO PERSISTENCE.  Every item below is an adapter onto a function
// that already shipped -- `removeSelected`, `arm`, `emit`, `transaction`, `add_site`,
// `closeLoopFromSelection`, `explodeToExplicit` -- so the menu cannot do anything the
// rest of the page could not already do, and cannot disagree with it about what is
// refused.
//
// EVENT-FREE, like every other gesture on this page: `menuOpen` / `menuItems` /
// `menuInvoke` / `menuClose` / `menuState` are the whole of it, and the pointer handler
// is a one-line adapter.  `tests/shim.mjs` has no events, no `classList`, no timers and
// a `remove()` that does nothing, so anything that lived inside a listener would be
// logic with no test.
var MENU = null;          // { mx, my, cx, cy, subject, sub }
var MDLG = null;          // the Modify panel, anchored where the menu was

// the inverse of `toModel`: where a stage point sits on the screen, so the panel opens
// under the pointer without the caller having to carry client coordinates
function toClient(x, y) {
  var f = fitBox();
  return { x: (f.r.left || 0) + f.ox + (x - VB.x) * f.k,
           y: (f.r.top || 0) + f.oy + (y - VB.y) * f.k };
}
// stage pixels -> LATTICE units, the coordinates every record is written in
function latticeAt(mx, my) {
  return { x: (mx - L.ox) / (L.sx || 1), y: (my - L.oy) / (L.sy || L.sx || 1) };
}
function subjectWord(kind) {
  return kind === 'site' ? 'trapping site' : kind === 'junction' ? 'junction'
       : kind === 'segment' ? 'rail' : kind === 'loop' ? 'transport loop' : String(kind);
}
function subjectAlive(s) {
  if (!s || !STATE || !STATE.device) return false;
  if (s.kind === 'loop') return has(STATE.device.loops || {}, s.id);
  if (s.kind === 'segment') return has(STATE.device.segments, s.id);
  return has(STATE.device.nodes, s.id);
}
// THE ONE SENTENCE FOR "a loop has no delete verb", so `removeSelected` and the menu's
// disabled item cannot drift into two different explanations of the same gap.
function noRemoveLoopWhy(id) {
  return id + ' is a transport loop; there is no delete verb for one yet -- delete one ' +
         'of its segments to open it, or edit the loop in the text lane.';
}

function menuOpen(mx, my, opts) {
  opts = opts || {};
  // THE WEBSITE'S EMBEDS ARE READ-ONLY and `claim` already answers 'pan' for every press
  // there, so this is unreachable from the pointer -- but a harness can call it, and a
  // menu of edits on a page where nothing may be edited would be a lie.
  if (VIEW_ONLY) return null;
  var h = hit(mx, my);
  if (!h) { menuClose(); return null; }
  var sub = { kind: normKind(h.kind, h.id), id: h.id };
  // THE PRESS SELECTS WHAT IS UNDER IT -- unless that is already part of the selection,
  // in which case the selection stands and the menu acts on all of it.  Right-clicking
  // one member of a marquee to delete the other nine would be the worst kind of surprise.
  var inSel = false, i;
  for (i = 0; i < SELSET.length; i++) {
    if (SELSET[i].id === sub.id && SELSET[i].kind === sub.kind) inSel = true;
  }
  if (!inSel) {
    setSelection([{ kind: sub.kind, id: sub.id }]);
    if (typeof selectRef === 'function') {
      selectRef(sub.kind === 'segment' ? 'segment' : 'site', sub.id);
    }
  }
  var pt = (opts.cx === undefined) ? toClient(mx, my) : { x: opts.cx, y: opts.cy };
  MDLG = null;
  MENU = { mx: mx, my: my, cx: pt.x, cy: pt.y, subject: sub, sub: null };
  paintMenu();
  return menuState();
}
function menuClose() {
  var was = !!(MENU || MDLG);
  MENU = null; MDLG = null;
  paintMenu();
  return was;
}

function menuItem(id, label, enabled, why, hint) {
  return { kind: 'item', id: id, label: label, enabled: !!enabled, why: why || '',
           hint: hint || 'menu:item' };
}

// WHY a builder-backed field cannot be written, in the device's own terms -- or '' when
// it can.  A generator device has no builder at all; a node added after the seal as a
// topology edit has a builder but no statement of its own to rewrite.
function builderWhy(method, id) {
  if (!hasBuilder()) {
    return 'this device came from a generator, so it has no builder statements to ' +
           'rewrite -- explode it to explicit geometry first.';
  }
  if (!builderRecordFor(method, id)) {
    return id + ' was added after the seal as a topology edit, so there is no ' + method +
           ' statement to rewrite. Explode to explicit geometry to get one.';
  }
  return '';
}
// The LAST builder statement that names this object, which is the one in force: a
// rewrite is another statement of the same kind, and `d.site` overwrites by id.
function builderRecordFor(method, id) {
  var calls = baseCalls(), found = null;
  for (var i = 0; i < calls.length; i++) {
    if (calls[i].method === method && String((calls[i].args || [])[0]) === String(id)) {
      found = calls[i];
    }
  }
  if (!found) return null;
  var kw = {}, k;
  for (k in (found.kwargs || {})) if (has(found.kwargs, k)) kw[k] = found.kwargs[k];
  return { method: found.method, args: (found.args || []).slice(), kwargs: kw };
}
// ONE BUILDER RECORD, REWRITTEN.  `d.site`/`d.segment`/`d.loop` all key on the id, so a
// second statement with the same id replaces the first -- this is the same path
// `addNodeAt` and `joinNodes` take, with the args carried through verbatim so a rewrite
// of the zone cannot silently move the node.
function rewriteBuilder(method, id, changes, label) {
  var why = builderWhy(method, id);
  if (why) return { ok: false, problems: [{ code: 'no_builder_record', message: why }] };
  var rec = builderRecordFor(method, id), k;
  for (k in (changes || {})) if (has(changes, k)) {
    if (changes[k] === null || changes[k] === undefined) delete rec.kwargs[k];
    else rec.kwargs[k] = changes[k];
  }
  return transaction([{ build: rec }], label || ('modify ' + id));
}

// THE ZONE SUBMENU: the zone types this device declares, each one applied to the site
// under the pointer.  A zone declared AFTER the seal is offered but disabled with the
// sentence the zone chips already give, because a builder statement is hoisted above the
// seal and could not name it.
function zoneMenuItem(sub) {
  var zt = (STATE && STATE.zone_types) || {};
  var names = Object.keys(zt).sort();
  var why = builderWhy('d.site', sub.id);
  if (!why && !names.length) why = 'this device declares no zone types';
  var post = postSeedZones();
  var kids = names.map(function (z) {
    var w = why || (post[z]
      ? (z + ' was declared after the machine was sealed; a site cannot use it, because ' +
         'builder statements are hoisted above the seal')
      : '');
    var cap = zt[z] && zt[z].capacity !== undefined ? Q.unbox(zt[z].capacity) : '?';
    return menuItem('zone:' + z, z + ' · holds ' + cap, !w, w, 'menu:zone');
  });
  return { kind: 'submenu', id: 'zone', label: 'Set zone', enabled: !why, why: why,
           hint: 'menu:zone', items: kids };
}

// Would `closeLoopFromSelection` work?  Asked without calling it, because asking must not
// commit -- the same walk it builds, and the same sentence it refuses with.
function closeLoopReady() {
  var walk = [], i;
  for (i = 0; i < SELSET.length; i++) {
    if (SELSET[i].kind === 'segment' || SELSET[i].kind === 'loop') continue;
    if (walk.indexOf(SELSET[i].id) < 0) walk.push(SELSET[i].id);
  }
  if (walk.length < 3) {
    return { ok: false, why: 'a loop needs at least three nodes; select them in orbit ' +
                             'order first (' + walk.length + ' selected)' };
  }
  return { ok: true, why: '' };
}

function menuItems() {
  if (!MENU || !subjectAlive(MENU.subject)) return [];
  var sub = MENU.subject, out = [], i;
  var sel = SELSET.slice(), kinds = {}, kindList;
  for (i = 0; i < sel.length; i++) kinds[sel[i].kind] = 1;
  kindList = Object.keys(kinds).sort();
  var multi = sel.length > 1;
  var inst = instanceAt(sub.id);

  out.push({ kind: 'head', id: 'head', enabled: false, why: '',
             label: multi ? (sel.length + ' selected · ' + kindList.join(' + '))
                          : (sub.id + ' · ' + subjectWord(sub.kind)) });

  if (multi) {
    var same = kindList.length === 1;
    out.push(menuItem('modify', 'Modify…', same,
      same ? '' : ('the selection mixes ' + kindList.join(' and ') +
                   '; one form cannot describe them all'), 'menu:modify'));
    var loopIn = null;
    for (i = 0; i < sel.length; i++) if (sel[i].kind === 'loop') loopIn = sel[i].id;
    out.push(menuItem('delete', 'Delete (' + sel.length + ')', !loopIn,
                      loopIn ? noRemoveLoopWhy(loopIn) : '', 'menu:delete'));
  } else if (sub.kind === 'site') {
    out.push(menuItem('modify', 'Modify…', true, '', 'menu:modify'));
    out.push(zoneMenuItem(sub));
    out.push(menuItem('place', 'Place another one of these', true, '', 'menu:place'));
    out.push(menuItem('delete', 'Delete', true, '', 'menu:delete'));
  } else if (sub.kind === 'junction') {
    // A JUNCTION HAS NO ZONE AND NO CAPACITY -- `DeviceBuilder.junction` raises TypeError
    // on either -- so there is nothing to set and nothing to copy: two items, and both
    // of them do something.
    out.push(menuItem('modify', 'Modify…', true, '', 'menu:modify'));
    out.push(menuItem('delete', 'Delete', true, '', 'menu:delete'));
  } else if (sub.kind === 'segment') {
    out.push(menuItem('modify', 'Modify…', true, '', 'menu:modify'));
    var iw = insertWhy(sub.id);
    out.push(menuItem('insert-site', 'Insert a trapping site here', !iw, iw, 'menu:insert'));
    out.push(menuItem('delete', 'Delete', true, '', 'menu:delete'));
  } else if (sub.kind === 'loop') {
    out.push(menuItem('modify', 'Modify…', true, '', 'menu:modify'));
    // NOT A DEAD BUTTON AND NOT A HIDDEN ONE: the verb does not exist, so the item says
    // so and says what to do instead.  Offering it and refusing on the click would be
    // the same information one gesture later.
    out.push(menuItem('delete', 'Delete', false, noRemoveLoopWhy(sub.id), 'menu:delete'));
  }

  if (inst) {
    out.push({ kind: 'head', id: 'cmp', enabled: false, why: '',
               label: 'part of ' + String(inst).replace(/_/g, ' ') });
    out.push(menuItem('select-component', 'Select the whole component', true, '', 'menu:component'));
    out.push(menuItem('delete-component', 'Delete the whole component', true, '', 'menu:component'));
  }

  var cl = closeLoopReady();
  out.push(menuItem('close-loop', 'Close loop', cl.ok, cl.why, 'menu:close-loop'));
  return out;
}
function menuFlat() {
  var items = menuItems(), out = [], i, j;
  for (i = 0; i < items.length; i++) {
    out.push(items[i]);
    for (j = 0; j < (items[i].items || []).length; j++) out.push(items[i].items[j]);
  }
  return out;
}

// Why a rail cannot take a site dropped into it, or '' when it can.
function insertWhy(segId) {
  var sg = (STATE && STATE.device) ? STATE.device.segments[segId] : null;
  if (!sg) return 'no such rail';
  return '';
}
// THE SUBDIVIDE GESTURE: `add_site(on="<segment>")`, which is the only add that can put a
// node onto a transport loop -- it is the only one with an unambiguous splice index (see
// qccd/arch/edit.py).  The new site lands at the point of the rail nearest the pointer.
function insertSiteOn(segId, mx, my) {
  var dev = STATE && STATE.device;
  var sg = dev ? dev.segments[segId] : null;
  if (!sg) return { ok: false, problems: [{ code: 'no_segment', message: 'no rail ' + segId }] };
  var na = dev.nodes[sg.a], nb = dev.nodes[sg.b];
  if (!na || !nb) return { ok: false, problems: [{ code: 'no_endpoint',
    message: segId + ' has an endpoint this device does not have' }] };
  var ax = +Q.unbox(na.pos[0]), ay = +Q.unbox(na.pos[1]);
  var bx = +Q.unbox(nb.pos[0]), by = +Q.unbox(nb.pos[1]);
  var p = latticeAt(mx, my);
  var dx = bx - ax, dy = by - ay, d2 = dx * dx + dy * dy;
  var t = d2 > 1e-12 ? ((p.x - ax) * dx + (p.y - ay) * dy) / d2 : 0.5;
  // never on top of an endpoint: two nodes at one point make `min_nearest_neighbour`
  // skip the pair and every mark on the stage silently becomes the wrong size
  t = Math.max(0.2, Math.min(0.8, t));
  var x = Q.pyFloat(Math.round((ax + t * dx) * 1000) / 1000);
  var y = Q.pyFloat(Math.round((ay + t * dy) * 1000) / 1000);
  // the zone an endpoint already carries, else whatever a hand-placed site would get
  var z = (na.kind === 'site' && na.zone) ? na.zone
        : (nb.kind === 'site' && nb.zone) ? nb.zone : defaultZone();
  var args = { id: freshId('N'), pos: [x, y], labels: ['added'], on: segId,
               zone: z || null, capacity: z ? 0 : 1, zone_types: STATE.zone_types };
  var op = { topology: { op: 'add_site', args: args },
             meta: { group: 'g' + (++GROUP), src: 'menu' } };
  var r = tryTopology(op);
  if (r.ok) { r.id = args.id; setSelection([{ kind: 'site', id: args.id }]); }
  return r;
}

function menuInvoke(id) {
  if (!MENU) {
    return { ok: false, problems: [{ code: 'no_menu', message: 'no menu is open' }] };
  }
  var flat = menuFlat(), it = null, i;
  for (i = 0; i < flat.length; i++) if (flat[i].id === id) it = flat[i];
  if (!it) {
    return { ok: false, problems: [{ code: 'no_item', message: 'no menu item ' + id }] };
  }
  if (it.kind === 'head') {
    return { ok: false, problems: [{ code: 'not_an_action',
      message: it.label + ' names what the menu is about; it is not an action' }] };
  }
  // A DISABLED ITEM STATES ITS REASON and does nothing.  It is never hidden: a verb that
  // does not exist here is a fact about the device, and hiding it leaves the user
  // hunting for a menu item that was never going to be there.
  if (!it.enabled) {
    toast('warn', it.why);
    return { ok: false, problems: [{ code: 'disabled', message: it.why }] };
  }
  var sub = MENU.subject, r;
  if (it.kind === 'submenu') {
    MENU.sub = (MENU.sub === it.id) ? null : it.id;
    paintMenu();
    return { ok: true, submenu: MENU.sub };
  }
  if (id === 'modify') return modifyOpen();
  if (String(id).slice(0, 5) === 'zone:') {
    var z = String(id).slice(5);
    r = rewriteBuilder('d.site', sub.id, { zone: z }, 'set zone');
    toast(r.ok ? 'ok' : 'bad', r.ok ? (sub.id + ' is now ' + z)
                                    : ((r.problems[0] || {}).message || 'refused'));
    if (r.ok) menuClose();
    return r;
  }
  if (id === 'place') {
    arm(sub.kind === 'junction' ? 'junction' : 'site');
    menuClose();
    return { ok: true, armed: armed() };
  }
  if (id === 'delete') {
    r = removeSelected();
    if (r && !r.ok && r.problems.length) toast('bad', r.problems[0].message);
    menuClose();
    return r;
  }
  if (id === 'insert-site') {
    r = insertSiteOn(sub.id, MENU.mx, MENU.my);
    toast(r.ok ? 'ok' : 'bad', r.ok ? (r.id + ' dropped into ' + sub.id)
                                    : ((r.problems[0] || {}).message || 'refused'));
    menuClose();
    return r;
  }
  if (id === 'select-component') {
    var mem = instanceMembers(instanceAt(sub.id));
    setSelection(mem);
    menuClose();
    return { ok: true, selected: mem.length };
  }
  if (id === 'delete-component') {
    setSelection(instanceMembers(instanceAt(sub.id)));
    r = removeSelected();
    if (r && !r.ok && r.problems.length) toast('bad', r.problems[0].message);
    menuClose();
    return r;
  }
  if (id === 'close-loop') {
    r = closeLoopFromSelection({});
    toast(r.ok ? 'ok' : 'bad', r.ok ? 'loop closed'
                                    : ((r.problems[0] || {}).message || 'refused'));
    menuClose();
    return r;
  }
  return { ok: false, problems: [{ code: 'no_item', message: 'no menu item ' + id }] };
}

function menuState() {
  var strip = function (it) {
    return { id: it.id, kind: it.kind, label: it.label, enabled: !!it.enabled,
             why: it.why || '',
             items: (it.items || []).map(function (k) {
               return { id: k.id, label: k.label, enabled: !!k.enabled, why: k.why || '' };
             }) };
  };
  return {
    open: !!MENU, dialog: !!MDLG,
    at: MENU ? [MENU.mx, MENU.my] : (MDLG ? [MDLG.mx, MDLG.my] : null),
    subject: MENU ? { kind: MENU.subject.kind, id: MENU.subject.id }
           : (MDLG ? { kind: MDLG.kind, id: MDLG.ids[0] } : null),
    submenu: MENU ? MENU.sub : null,
    selection: SELSET.map(function (s) { return s.kind + ':' + s.id; }),
    items: (MENU ? menuItems() : []).map(strip),
    fields: MDLG ? modifyFields().map(function (f) {
      return { name: f.name, value: f.value === null ? '' : String(f.value),
               layer: f.layer, enabled: !!f.enabled, why: f.why || '',
               mixed: !!f.mixed }; }) : [],
    why: MDLG ? MDLG.why : '',
    explode: MDLG ? !!MDLG.explode : false,
    confirm: MDLG ? !!MDLG.confirm : false
  };
}

// ------------------------------------------------------------------- the Modify panel
//
// THE FIELDS ARE NOT A LITERAL LIST.  Each row names a field of the SCHEMA (through
// `paletteEntry(kind).fields`, which is generated from the shipped schema), so a field
// the file format stops carrying disappears from this panel rather than becoming a form
// that writes something no loader accepts.  `layer` says which applier owns it:
//
//   mutate  -- `emit({method, args, kwargs})`: validated, committed, rolled back on a
//              refusal.  Works on EVERY device, generator or hand-built, because these
//              are post-seal retunes.
//   builder -- ONE `d.site` / `d.segment` / `d.loop` record rewritten through
//              `transaction`, the same path `addNodeAt` / `joinNodes` / `closeLoop` take.
//              A generator device has no builder, so these rows are DISABLED with the
//              reason and the panel offers the one thing that would fix it.
var MOD_SPEC = {
  site: [
    { name: 'capacity', schema: 'capacity', layer: 'mutate', type: 'integer',
      note: 'ions this trap holds · set_site_capacity' },
    { name: 'x', schema: 'pos', layer: 'mutate', type: 'number', one: true, note: 'move_site' },
    { name: 'y', schema: 'pos', layer: 'mutate', type: 'number', one: true, note: 'move_site' },
    { name: 'zone', schema: 'zone_type', layer: 'builder', type: 'string',
      note: 'what may happen here · d.site(zone=)' },
    { name: 'labels', schema: 'labels', layer: 'builder', type: 'list',
      note: 'space separated · d.site(labels=)' }
  ],
  junction: [
    { name: 'x', schema: 'pos', layer: 'mutate', type: 'number', one: true, note: 'move_site' },
    { name: 'y', schema: 'pos', layer: 'mutate', type: 'number', one: true, note: 'move_site' }
  ],
  segment: [
    { name: 'length', schema: 'length', layer: 'mutate', type: 'number',
      note: 'lattice units · set_segment_length' },
    { name: 'capacity', schema: 'capacity', layer: 'builder', type: 'integer',
      note: 'ions in flight · d.segment(capacity=)' },
    { name: 'loop', schema: 'loop', layer: 'builder', type: 'string',
      note: 'the orbit this rail lies on · "none" clears it' },
    { name: 'labels', schema: 'labels', layer: 'builder', type: 'list',
      note: 'space separated · d.segment(labels=)' }
  ],
  loop: [
    { name: 'closed', schema: 'closed', layer: 'builder', type: 'boolean',
      note: 'true is a ring the machine can rotate' },
    { name: 'kind', schema: 'kind', layer: 'builder', type: 'string', note: 'ring or path' },
    { name: 'note', schema: 'note', layer: 'builder', type: 'string', note: 'free text' }
  ]
};
var MOD_BUILD = { site: 'd.site', junction: 'd.junction', segment: 'd.segment', loop: 'd.loop' };

function num3(v) {
  var n = Number(Q.unbox(v));
  if (!isFinite(n)) return '';
  return String(Math.round(n * 1000) / 1000);
}
function modifyValue(kind, id, name) {
  var dev = STATE && STATE.device;
  if (!dev) return null;
  if (kind === 'site' || kind === 'junction') {
    var n = dev.nodes[id];
    if (!n) return null;
    if (name === 'capacity') return n.cap;
    if (name === 'x') return num3(n.pos[0]);
    if (name === 'y') return num3(n.pos[1]);
    if (name === 'zone') return n.zone || '';
    if (name === 'labels') return (n.labels || []).join(' ');
  } else if (kind === 'segment') {
    var s = dev.segments[id];
    if (!s) return null;
    if (name === 'length') return num3(s.length);
    if (name === 'capacity') return s.cap;
    if (name === 'loop') return s.loop || '';
    if (name === 'labels') return (s.labels || []).join(' ');
  } else if (kind === 'loop') {
    var lp = (dev.loops || {})[id];
    if (!lp) return null;
    if (name === 'closed') return lp.closed ? 'true' : 'false';
    if (name === 'kind') return lp.kind || 'ring';
    if (name === 'note') return lp.note === null || lp.note === undefined ? '' : String(lp.note);
  }
  return null;
}
// the rows the SCHEMA still admits for this kind, in the order above
function modifySpec(kind) {
  var spec = MOD_SPEC[kind] || [], e = paletteEntry(kind), known = {}, out = [], i;
  var fs = (e && e.fields) || [];
  for (i = 0; i < fs.length; i++) known[fs[i].name] = fs[i];
  for (i = 0; i < spec.length; i++) if (has(known, spec[i].schema)) out.push(spec[i]);
  return out;
}
function modifyFields() {
  if (!MDLG) return [];
  var spec = modifySpec(MDLG.kind), out = [], i, j;
  var ids = MDLG.ids, multi = ids.length > 1, noBuilder = false;
  for (i = 0; i < spec.length; i++) {
    var f = spec[i], why = '';
    var vals = [];
    for (j = 0; j < ids.length; j++) vals.push(modifyValue(MDLG.kind, ids[j], f.name));
    var mixed = false;
    for (j = 1; j < vals.length; j++) if (String(vals[j]) !== String(vals[0])) mixed = true;
    if (f.one && multi) {
      why = 'a position belongs to one ' + subjectWord(MDLG.kind) +
            '; ' + ids.length + ' of them cannot share it. Drag them instead.';
    } else if (f.layer === 'builder') {
      for (j = 0; j < ids.length && !why; j++) why = builderWhy(MOD_BUILD[MDLG.kind], ids[j]);
      if (why && !hasBuilder()) noBuilder = true;
    }
    out.push({ name: f.name, value: vals[0], mixed: mixed, layer: f.layer, type: f.type,
               note: f.note || '', enabled: !why, why: why });
  }
  MDLG.explode = noBuilder;
  return out;
}

function modifyOpen(opts) {
  opts = opts || {};
  var sel = SELSET.slice(), kinds = {}, i;
  if (!sel.length) return { ok: false, problems: [{ code: 'no_selection',
    message: 'nothing is selected' }] };
  for (i = 0; i < sel.length; i++) kinds[sel[i].kind] = 1;
  var kindList = Object.keys(kinds);
  if (kindList.length !== 1) {
    return { ok: false, problems: [{ code: 'mixed_selection',
      message: 'the selection mixes ' + kindList.sort().join(' and ') +
               '; one form cannot describe them all' }] };
  }
  var anchor = MENU || MDLG || { mx: 0, my: 0, cx: 40, cy: 80 };
  MDLG = { kind: kindList[0], ids: sel.map(function (s) { return s.id; }),
           mx: anchor.mx, my: anchor.my, cx: anchor.cx, cy: anchor.cy,
           why: '', explode: false, confirm: false };
  MENU = null;
  paintMenu();
  return { ok: true, dialog: menuState() };
}
function modifyClose() {
  var was = !!MDLG;
  MDLG = null;
  paintMenu();
  return was;
}
// THE SHARED `coerce` DOES THE WORK, so this panel cannot disagree with the zone form and
// the block forms about what "3" or "true" means.  Two things it has no type for: a list
// of labels, and the one convention that CLEARS a kwarg rather than setting it (there is
// no "unset" in a merge, so a field needs a word for empty).
function modifyCoerce(raw, f) {
  var s = String(raw);
  if (f.type === 'list') {
    return s.split(/[\s,]+/).filter(function (x) { return !!x; });
  }
  if (f.type === 'string' && (s === 'none' || s === 'null')) return null;
  return coerce(s, { type: f.type });
}
function modifyFail(r) {
  MDLG.why = ((r.problems || [])[0] || {}).message || 'refused';
  paintMenu();
  return { ok: false, problems: r.problems || [] };
}
// APPLY.  `vals` is a map of field name -> raw text; with none given the live inputs are
// read, which is what the Apply button does.  A field left blank, or unchanged, is not
// written: `set_*` is a merge with no way to unset, so writing a value back is
// indistinguishable from the user asserting it.
function modifyApply(vals) {
  if (!MDLG) {
    return { ok: false, problems: [{ code: 'no_dialog',
      message: 'no Modify panel is open' }] };
  }
  var fields = modifyFields(), changes = {}, i, f, raw;
  for (i = 0; i < fields.length; i++) {
    f = fields[i];
    raw = (vals && has(vals, f.name)) ? vals[f.name] : val('mdlg_' + f.name);
    if (raw === undefined || raw === null) continue;
    raw = String(raw);
    if (raw === '') continue;
    if (!f.mixed && raw === String(f.value === null ? '' : f.value)) continue;
    if (!f.enabled) return modifyFail({ problems: [{ code: 'disabled', message: f.why }] });
    changes[f.name] = raw;
  }
  var kind = MDLG.kind, ids = MDLG.ids.slice(), spec = modifySpec(kind);
  var build = {}, anyBuild = false, n = 0, r;
  for (i = 0; i < spec.length; i++) {
    f = spec[i];
    if (!has(changes, f.name)) continue;
    if (f.layer === 'builder') { build[f.name] = modifyCoerce(changes[f.name], f); anyBuild = true; continue; }
    if (f.name === 'x' || f.name === 'y') continue;          // both at once, below
    if (kind === 'site' && f.name === 'capacity') {
      r = emit({ method: 'set_site_capacity', args: [ids.slice(), modifyCoerce(changes[f.name], f)], kwargs: {} });
    } else if (kind === 'segment' && f.name === 'length') {
      r = emit({ method: 'set_segment_length', args: [ids.slice(), modifyCoerce(changes[f.name], f)], kwargs: {} });
    } else continue;
    n++;
    if (!r.ok) return modifyFail(r);
  }
  // X AND Y TRAVEL TOGETHER: `move_site` takes a whole position, and sending the old y
  // with a new x is still a move -- one that `moveProblems` must judge as a whole.
  if (has(changes, 'x') || has(changes, 'y')) {
    var nd = STATE.device.nodes[ids[0]];
    if (!nd) return modifyFail({ problems: [{ code: 'gone', message: ids[0] + ' is no longer on the stage' }] });
    var nx = Q.pyFloat(has(changes, 'x') ? Number(changes.x) : +Q.unbox(nd.pos[0]));
    var ny = Q.pyFloat(has(changes, 'y') ? Number(changes.y) : +Q.unbox(nd.pos[1]));
    r = emit({ method: 'move_site', args: [ids[0], nx, ny], kwargs: {} });
    n++;
    if (!r.ok) return modifyFail(r);
  }
  if (anyBuild) {
    for (i = 0; i < ids.length; i++) {
      r = rewriteBuilder(MOD_BUILD[kind], ids[i], build, 'modify ' + ids[i]);
      n++;
      if (!r.ok) return modifyFail(r);
    }
  }
  if (!n) {
    MDLG.why = 'nothing changed';
    paintMenu();
    return { ok: false, problems: [{ code: 'no_change', message: 'nothing changed' }] };
  }
  toast('ok', ids.join(', ') + ': ' + n + ' change(s)');
  modifyClose();
  return { ok: true, problems: [], n: n };
}
// THE ONE IRREVERSIBLE OFFER, behind a confirmation.  `clickStage` already tells the user
// in words that a generator device has no builder and that "explode to explicit" is the
// way out; this closes that loop where the refusal is.
function modifyExplode() {
  if (!MDLG) {
    return { ok: false, problems: [{ code: 'no_dialog', message: 'no Modify panel is open' }] };
  }
  if (!MDLG.confirm) {
    MDLG.confirm = true;
    MDLG.why = 'this device will stop reproducing from its generator: it would be saved ' +
               'as explicit statements instead. Press again to do it.';
    paintMenu();
    return { ok: false, confirm: true,
             problems: [{ code: 'confirm', message: MDLG.why }] };
  }
  var r = explodeToExplicit();
  MDLG.confirm = false;
  MDLG.why = r.ok ? '' : (((r.problems || [])[0] || {}).message || 'refused');
  if (r.ok) toast('warn', r.warning);
  paintMenu();
  return r;
}

// ---- the panel, drawn ----------------------------------------------------------------
function menuButton(it) {
  var b = elh('button', 'cmitem' + (it.kind === 'submenu' ? ' cmmore' : ''));
  b.setAttribute('data-item', it.id);
  b.setAttribute('data-hint', it.hint || 'menu:item');
  b.textContent = it.label + (it.kind === 'submenu' ? ' ▸' : '');
  if (!it.enabled) {
    b.setAttribute('disabled', 'disabled');
    b.setAttribute('title', it.why);
  }
  if (b.addEventListener) b.addEventListener('click', function () { menuInvoke(it.id); });
  return b;
}
function whyLine(text) {
  var w = elh('div', 'cmwhy');
  w.textContent = text;
  return w;
}
function renderMenuInto(host) {
  var items = menuItems(), i, j;
  for (i = 0; i < items.length; i++) {
    var it = items[i];
    if (it.kind === 'head') {
      var h = elh('div', 'cmhead');
      h.textContent = it.label;
      host.append(h);
      continue;
    }
    host.append(menuButton(it));
    // THE REASON IS ON THE SCREEN, not only in a tooltip: a greyed item with no
    // explanation is exactly the "why can't I?" this menu exists to answer.
    if (!it.enabled && it.why) host.append(whyLine(it.why));
    if (it.kind === 'submenu' && MENU.sub === it.id) {
      var box = elh('div', 'cmsub');
      for (j = 0; j < (it.items || []).length; j++) {
        box.append(menuButton(it.items[j]));
        if (!it.items[j].enabled && it.items[j].why) box.append(whyLine(it.items[j].why));
      }
      host.append(box);
    }
  }
  var foot = elh('div', 'cmfoot');
  foot.textContent = 'esc closes · right-click any part of the canvas';
  host.append(foot);
}
function renderModifyInto(host) {
  var head = elh('div', 'cmhead');
  head.textContent = 'Modify ' + (MDLG.ids.length > 1
    ? (MDLG.ids.length + ' × ' + subjectWord(MDLG.kind))
    : (MDLG.ids[0] + ' · ' + subjectWord(MDLG.kind)));
  host.append(head);
  var fields = modifyFields(), i;
  for (i = 0; i < fields.length; i++) {
    var f = fields[i];
    var inp = textInput('mdlg_' + f.name, f.mixed ? '' : (f.value === null ? '' : f.value));
    if (!f.enabled) {
      inp.setAttribute('disabled', 'disabled');
      inp.setAttribute('title', f.why);
    }
    if (f.mixed && inp.setAttribute) inp.setAttribute('placeholder', '(mixed)');
    host.append(fieldRow(f.name, inp, f.enabled ? f.note : ''));
    if (!f.enabled && f.why) host.append(whyLine(f.why));
  }
  // THE REFUSAL IS INLINE, not a toast: it belongs to the field the user just typed in
  // and it must still be on screen while they fix it.
  var err = elh('div', 'cmerr');
  err.setAttribute('id', 'mdlgErr');
  err.textContent = MDLG.why || '';
  host.append(err);
  var bar = elh('div', 'formbtns');
  var ap = btn('mdlgApply', 'Apply', true);
  if (ap.addEventListener) ap.addEventListener('click', function () { modifyApply(); });
  bar.append(ap);
  var cl = btn('mdlgClose', 'Cancel');
  if (cl.addEventListener) cl.addEventListener('click', function () { modifyClose(); });
  bar.append(cl);
  host.append(bar);
  if (MDLG.explode) {
    var ex = btn('mdlgExplode', MDLG.confirm
      ? 'Yes — write it out as explicit statements'
      : 'Make this editable (explode to explicit…)');
    ex.setAttribute('data-hint', 'explode');
    if (ex.addEventListener) ex.addEventListener('click', function () { modifyExplode(); });
    host.append(ex);
  }
  var note = elh('div', 'cmfoot');
  note.textContent = 'enter applies · esc cancels';
  host.append(note);
}
function paintMenu() {
  var host = $('ctxmenu');
  if (!host) return;
  if (host.replaceChildren) host.replaceChildren();
  host.innerHTML = '';
  if ((MENU && !subjectAlive(MENU.subject)) ) { MENU = null; }
  if (!MENU && !MDLG) {
    host.setAttribute('data-open', '0');
    if (host.style) host.style.display = 'none';
    return;
  }
  host.setAttribute('data-open', '1');
  var cx = MENU ? MENU.cx : MDLG.cx, cy = MENU ? MENU.cy : MDLG.cy;
  var W = (typeof innerWidth === 'number' && innerWidth > 0) ? innerWidth : 1600;
  var H = (typeof innerHeight === 'number' && innerHeight > 0) ? innerHeight : 900;
  if (host.style) {
    host.style.display = '';
    host.style.left = Math.max(4, Math.min(cx, W - 268)) + 'px';
    host.style.top = Math.max(4, Math.min(cy, H - (MDLG ? 320 : 250))) + 'px';
  }
  if (MDLG) renderModifyInto(host); else renderMenuInto(host);
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
                           table: policyTable() });
  });
  addLine.append(add);
  host.append(addLine);
  var note = elh('div', 'mut');
  note.textContent = 'a new point goes into ' + policyTable() + ', the table the model ' +
    'prices: it takes the fastest point there. set_curve replaces the whole curve, so ' +
    'every untouched row is carried through verbatim -- source and label included.';
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
// THE TABLE THE MODEL PRICES.  The operating-point policy restricts a curve to one
// table and takes the fastest point in it, so a point in any other table is never priced
// at all.  A new point therefore defaults to the policy's table -- it used to inherit the
// last row's, which on the shipped physics is not the priced one, so a point added from
// the form changed nothing on the page and there was no field to say why.
function policyTable() {
  return (typeof PH !== 'undefined' && PH && PH.policy && PH.policy.table) || 'qccdsim_jones';
}
function curveAddPoint(name, pt) {
  var rows = curveRows(name), last = rows.length ? rows[rows.length - 1] : {};
  var p = { us: Q.pyFloat(+((pt || {}).us !== undefined ? pt.us : (last.us || 5.0))),
            quanta: Q.pyFloat(+((pt || {}).quanta !== undefined ? pt.quanta : (last.quanta || 0.1))),
            table: (pt || {}).table || policyTable() };
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
    return { ok: false, problems: [{ code: 'empty_curve', message: 'a curve with no points is refused by the schema' }] };
  }
  var res = emit({ method: 'set_curve', args: [name, out], kwargs: {} });
  toast(res.ok ? 'ok' : 'bad', res.ok ? (name + ': ' + out.length + ' point(s)')
                                      : ((res.problems[0] || {}).message || 'refused'));
  paint();
  return res;
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
  if (typeof window !== 'undefined' && typeof window.onInspector === 'function') { try { window.onInspector(SELSET.length); } catch (err) { /* page hook */ } }
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
      : 'Nothing selected. Click a part on the canvas to see its details here, or press a tile above to add one.';
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
    h += '<div class="fieldrow"><label>pos</label><span>' + fmt(Q.unbox(n.pos[0]), 3) + ', ' +
         fmt(Q.unbox(n.pos[1]), 3) + '</span></div>';
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
  // the parse errors first (at their line), then the lowering errors (at their
  // statement) -- the same lines the Evaluate toast says, so the strip and the toast
  // cannot disagree about what was refused
  var errs = PARSE_ERRS.concat(lowerErrors());
  var e = $('pwErr');
  if (e) {
    // one line per problem; joined with a newline so `textContent` reads as lines too
    e.innerHTML = errs.length
      ? errs.map(function (x) { return '<div>' + esc2(problemLine(x)) + '</div>'; }).join('\n')
      : '';
  }
  var c = $('pwCount');
  if (c) {
    c.textContent = PROG.length ? (PROG.length + ' statements → ' + P.frames.length + ' frames')
                  : P.frames.length ? (P.frames.length + ' shipped frames')
                  : 'no programme';
  }
  // THE PLACEHOLDER IS WHAT TEST DRIVE WOULD WRITE, rendered by the same function that
  // renders the pane's text -- so the grey hint names this device's own sites and is a
  // programme that runs, not an example from some other device.
  // -- and only when there is no programme: a shipped or authored programme fills the
  // pane, and planning a test drive on every paint of a 168-node device would be work
  // nobody sees
  if (ta.setAttribute) {
    if (PROG.length || P.frames.length) ta.setAttribute('placeholder', '');
    else {
      var plan = testDrivePlan();
      var first = plan.ok ? plan.from : firstSiteId();
      ta.setAttribute('placeholder',
        (plan.ok ? Q.renderProgramSource(plan.statements) + '\n' : '') +
        '# no programme yet \u2014 press Test drive or type p.init({"d0": "' + first + '"})');
    }
  }
  var f = $('pwFoot');
  if (f) {
    f.innerHTML = AUTHORED
      ? 'this programme was written here. <b>No Python replay exists to check it against</b>, ' +
        'so the per-frame oracle does not apply: <code>frameChecked</code> is ' +
        ((PRICE && PRICE.frameChecked) || 0) + '. Download the pair and run ' +
        '<code>python -m qccd run &lt;name&gt;.arch.json --tsir &lt;name&gt;.tsir.json</code>.'
      : P.frames.length
      ? 'the shipped programme. Type <code>p.init({...})</code> and the stage re-renders ' +
        'from what you wrote; <b>Evaluate</b> re-prices and re-checks.'
      : 'no programme yet \u2014 press <b>Test drive</b> or type <code>p.init({"d0": "' +
        esc2(first) + '"})</code> and press <b>Evaluate</b>.';
  }
}
function firstSiteId() {
  var dev = STATE && STATE.device;
  if (dev) {
    for (var nid in dev.nodes) if (has(dev.nodes, nid) && dev.nodes[nid].kind === 'site') return nid;
  }
  return '<first site>';
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
    // the same rows the head's chips show for an authored programme
    h += '<table>' +
      metricRows().map(function (r) { return row(r[0], esc2(r[1])); }).join('') +
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
  // the sentence the price strip used to carry: what these numbers are worth, and the
  // command that would verify them in Python
  var note = priceNote();
  if (note) h += '<div class="mut" id="rPrice">' + esc2(note) + '</div>';

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
    return '<span class="badge ' + cls + '" data-hint="rule:' + esc2(c.rule) + '" title="' +
           esc2(c.statement) + '">' + c.rule + (c.count ? ' ' + c.count : '') + '</span>';
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
  claim: claim, claimEvent: claimEvent, hover: hover, cursor: cursor, panning: panning,
  outline: outlineOf, hitRadii: hitRadii, slop: slop,
  marqueeBegin: marqueeBegin, marqueeMove: marqueeMove, marqueeDrop: marqueeDrop,
  bandBegin: bandBegin, bandMove: bandMove, bandDrop: bandDrop, bandCancel: bandCancel,
  cancelGesture: escapeGesture,
  // THE RULER AND THE SCALE, callable without an Event like every other gesture here, so
  // a harness can assert a distance in micrometres rather than a rectangle in pixels
  measureToggle: measureToggle, measureClick: measureClick, measureClear: measureClear,
  measure: measureReadout, measureOn: function () { return MEASURE; },
  setTrueScale: setTrueScale, trueScale: function () { return TRUE_SCALE; },
  rescale: rescale, refit: refit, refitNext: refitNext,
  toUm: toUm, physVec: physVec, railAngle: angleBetween,
  // Redraw the static scene from the CURRENT layout, without recomputing it.  `rescale`
  // recomputes and would overwrite anything written into `L` by hand -- so this is the
  // only way to see the effect of a change made to the layout in place, which is how a
  // harness reaches the bowed-segment branch that no shipped device is in.
  redraw: rebuildStatic,
  dragging: function () { return !!(GHOST || BAND); },
  // the live feedback, callable without an Event: the HUD the pointer adapter shows and
  // the toast strip as the page holds it
  showHud: showHud, hideHud: hideHud, toasts: toasts, lengthNote: lengthNote,
  groupCheck: groupCheck,
  // THE KEYMAP: the verb a key means, the rows the help shows, and the overlay's state
  keyGesture: keyGesture, keyHelp: keyHelp, helpToggle: helpToggle,
  helpOn: function () { return HELPON; },
  nudge: nudge, reconcileLast: reconcileLast, toast: toast,
  subjectOf: subjectOf, selectionNodes: selectionNodes,
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
  // THE RIGHT-CLICK MENU AND ITS MODIFY PANEL, Event-free like every other gesture here:
  // the model, the items, invoking one, and the panel's fields and verdict
  menuOpen: menuOpen, menuItems: menuItems, menuInvoke: menuInvoke, menuClose: menuClose,
  menuState: menuState,
  modifyOpen: modifyOpen, modifyFields: modifyFields, modifyApply: modifyApply,
  modifyClose: modifyClose, modifyExplode: modifyExplode,
  insertSiteOn: insertSiteOn, rewriteBuilder: rewriteBuilder,
  // the two registers of `lints()`: what is wrong, and what is merely not there yet
  notes: lintNotes, lintProblems: lintProblems,
  ghostBegin: ghostBegin, ghostMove: ghostMove, ghostDrop: ghostDrop,
  ghostCancel: ghostCancel, placeStamp: placeStamp,
  clickStage: clickStage, dblclickStage: dblclickStage, isStampType: isStampType,
  setViewOnly: setViewOnly, viewOnly: function () { return VIEW_ONLY; },
  leaveStage: leaveStage,
  sealZones: sealZones, canvasHistory: canvasHistory, pressStartCard: pressStartCard,
  setName: setName,
  zone: function (z) { if (z !== undefined) { NEW_ZONE = z; AVCACHE = {}; paint(); }
                       return NEW_ZONE; },
  closeLoopFromSelection: closeLoopFromSelection,
  elementDocs: function () { return D.element_docs || {}; },
  toModel: toModel, snapTo: snapTo, rebuild: rebuild,
  setSnap: setSnap, snap: function () { return SNAP; },
  // ---- the sketch: the shape first -----------------------------------------------
  // The mode, the armed tool, the live preview's own numbers, the plan a release would
  // commit, and every gesture -- all callable without an Event, so a harness drives the
  // rule rather than the handler that happens to obey it.
  designMode: designMode, setDesignMode: setDesignMode,
  sketchTool: sketchTool, sketchOn: sketchOn,
  sketchTools: function () { return SK_ORDER.slice(); },
  sketchPlan: sketchPlan, sketchReadout: sketchReadout, sketchCommit: sketchCommit,
  sketchDown: sketchDown, sketchMove: sketchMove, sketchUp: sketchUp,
  sketchClick: sketchClick, sketchFinish: sketchFinish, sketchClear: sketchClear,
  sketchDraw: sketchDraw,
  sketchPoints: function () { return SKPTS.slice(); },
  // the boundary, readable: the frozen mark sizes in model units, a node's box, and
  // the first mark a set of members would overlap at an offset
  boundary: function () { return BOUND ? { len2: BOUND.len(2), len0: BOUND.len(0), t: BOUND.t, rj: BOUND.rj, sx: BOUND.sx, sy: BOUND.sy } : null; },
  nodeBox: function (id, x, y) { var n = nodeById[id]; return n ? nodeBoxAt(n, x === undefined ? n.x : x, y === undefined ? n.y : y) : null; },
  dragState: function () { return GHOST ? { x: GHOST.x, y: GHOST.y, x0: GHOST.x0, y0: GHOST.y0, acc: GHOST.acc, contact: GHOST.contact, noBoundary: GHOST.noBoundary, mx0: GHOST.mx0, my0: GHOST.my0 } : null; },
  contactAt: function (p0, ox, oy) { var m = {}; for (var i = 0; i < p0.length; i++) m[p0[i].id] = 1; return contactAt(p0, ox || 0, oy || 0, m); },
  // numbers
  price: function () { return PRICE; }, hardware: function () { return HW; },
  // THE PREDICATE THAT FREEZES THE STAGE: the structural break list, `[]` when the
  // programme fits.  `price().blocked` is this list PLUS the cost-model failures
  // (`no_curve`, `price_error`), which do not freeze because the picture is still true.
  programBreaks: programBreaks,
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
  newCanvas: newCanvas, newFromGenerator: newFromGenerator, newFromTemplate: newFromTemplate,
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
  parseErrors: function () { return PARSE_ERRS.slice(); },
  evaluateWrite: evaluateWrite, problemLine: problemLine, metricRows: metricRows,
  refusedLine: refusedLine,
  testDrivePlan: testDrivePlan, testDrive: testDrive, pressTestDrive: pressTestDrive,
  programToTsir: programToTsir, exportPair: exportPair,
  // the verdicts
  rules: ruleReport, ruleCoverage: ruleCoverage,
  // the palette, GENERATED from the shipped schema + consumer table
  palette: palette,
  // the explain layer
  hintFor: hintFor, hintFrom: hintFrom, hintHide: hintHide, stageHint: stageHint,
  explainToggle: explainToggle, explain: function () { return EXPLAIN; },
  setFold: setFold, folds: function () { return { row: PAL_OPEN.row, block: PAL_OPEN.block, component: PAL_OPEN.component }; },
  // the course
  lessonsReady: lessonsReady, lessonList: lessonList, lessonState: lessonState,
  lessonLoad: lessonLoad, lessonCheck: lessonCheck, lessonHint: lessonHint,
  lessonSolution: lessonSolution, lessonNext: lessonNext, lessonAnswer: lessonAnswer, setAnswer: setAnswer,
  courseProgress: courseProgress, evalCheck: evalCheck, loadCase: loadCase, shippedVerdict: shippedVerdict,
  courseBlob: function () {
    return { model: D.model || null, table: (PH && PH.policy) ? PH.policy.table : null,
             cases: D.tutorial_cases || null, verdicts: D.tutorial_verdicts || null };
  },
  // verbs the lessons drive that the pointer adapters reach by other names
  setSelection: setSelection, seekTo: seekTo, fit: fitStage,
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
