// THE DESIGN-TOOL HARNESS.
//
//   node tests/studio.mjs <page.html> [script.json] -> one JSON object on stdout
//
// It drives an emitted page through `tests/drive.mjs`'s ONE step vocabulary and reports
// what the page would tell a user: whether editing is available, what it refuses, what it
// checked, and -- the load-bearing part -- what it says about checks that did NOT run.
//
// WHAT THIS EXISTS TO CATCH, measured on this tree before the fix:
//
//   * an empty page printing "self-check ... agrees with the Python verifier to 0.0e+0
//     quanta per ion" -- a green tick for a loop that never executed, in the one panel
//     that asserts the page is trustworthy;
//   * `Step 1 / 0 - undefined` as the first sentence a new user reads;
//   * an empty `<div>` where the rule verdicts go, which reads as "nothing wrong";
//   * `exportJson()` handing over a document Python refuses with 24 structural errors
//     while the page reported 576 DACs for a machine whose ion capacity was zero.
//
// It does NOT stub `localStorage`, and `tests/shim.mjs` must not either: the design tool's
// most important guarantee is that your work survives, and asserting that against a fake
// would prove nothing.  `snapshot()`/`restore()` are pure functions and the round trip is
// asserted through `EDITOR.digest()`, which exercises the real applier.
import fs from 'fs';
import { loadPage } from './shim.mjs';
import { PAGE_HOOK, applyStep } from './drive.mjs';

globalThis.__QCCD_SYNC = true;

const page = process.argv[2];
const script = process.argv[3] ? JSON.parse(fs.readFileSync(process.argv[3], 'utf8')) : [];

const out = { page: page.replace(/\\/g, '/').split('/').pop() };
let byId;
try {
  ({ byId } = loadPage(page, PAGE_HOOK));
} catch (err) {
  console.log(JSON.stringify({ ...out, fatal: String(err && err.message) }));
  process.exit(0);
}
const ED = globalThis.EDITOR, PG = globalThis.__page;

function evidence(label) {
  const side = PG.side() || '';
  const cov = PG.coverage();
  const pr = ED.price();
  let exportErr = null, exported = null;
  try { exported = ED.exportJson(); } catch (err) { exportErr = String(err.message); }
  return {
    label,
    ready: ED.ready(), why: ED.why(),
    nodes: PG.nodes().length, segs: PG.segs().length,
    loops: Object.keys(PG.loops() || {}).length,
    frames: PG.nframes(),
    // the empty state over the canvas and the start fold, read off their attributes
    stage_empty: PG.stageEmpty ? PG.stageEmpty() : null,
    start_open: PG.startOpen ? PG.startOpen() : null,
    canvas_undo: ED.canvasHistory ? ED.canvasHistory().undo : null,
    name: ED.state() ? ED.state().name : null,
    // the playhead and the help overlay, so a key step's effect is in the evidence
    frame: PG.frame ? PG.frame() : null,
    help_on: PG.helpOn ? PG.helpOn() : null,
    lesson: ED.lessonState ? ED.lessonState() : null,
    lessons: ED.lessonList ? ED.lessonList().length : 0,
    // the live feedback: the drag HUD (class + text) and the toasts still showing -- a
    // refusal must be visible BEFORE the release, and one refusal must not become three
    hud: PG.hud ? PG.hud() : null,
    toasts: PG.toasts ? PG.toasts() : null,
    length_note: (ED.lengthNote ? ED.lengthNote() : null),
    layout: (({ W, H, sx, sy, g, n, wide }) => ({ W, H, sx, sy, g, n, wide }))(PG.layout()),
    // THE SCALE, which is what makes the picture measurable at all: the technology the
    // page carries, whether the fit is true to it, and the scale bar's own numbers.  A
    // page that lost its technology would draw identically and mean nothing, so the
    // evidence has to name it rather than show a picture of it.
    scale: (() => {
      const V = globalThis.window && globalThis.window.VIEW;
      if (!V || !V.tech) return null;
      const t = V.tech(), bar = V.bar ? V.bar() : null;
      return { preset: t.preset, nm_x: t.nm_per_unit_x, nm_y: t.nm_per_unit_y,
               n_dc_pairs: t.n_dc_pairs, dc_pitch: t.dc_pitch,
               true_scale: V.trueScale ? V.trueScale() : null,
               layout_true: !!PG.layout().true_scale,
               bar_um: bar ? bar.um : null, bar_label: bar ? bar.label : null };
    })(),
    // THE RULER, as numbers.  `measure()` is pure and callable without an Event, like
    // every other gesture here, so "the distance between these two sites is 464 um" is
    // an assertion rather than a screenshot.
    measure: ED.measure ? ED.measure() : null,
    data_layout: (byId['row'].attrs || {})['data-layout'] || null,
    // the app frame: the two folds and the Circuit pane's off switch, as attributes
    folds: PG.folds ? PG.folds() : null,
    dragging: ED.dragging ? ED.dragging() : null,
    pane_q_off: PG.paneOff ? PG.paneOff('Q') : null,
    prog: PG.progState ? PG.progState() : null,
    // THE THREE HONESTY SENTENCES, matched on the page's own words
    says_agrees_with_python: /agrees with the (Python verifier|checker's replay)/.test(side),
    // 651f39c reworded both sentences ("the checker"); this matcher kept the old words
    says_nothing_to_check: /nothing to (check this one against|compare this page's replay with)/.test(side),
    says_no_programme_replayed: /no programme has been replayed/.test(side),
    says_all_pass: /all rules pass/i.test(side),
    says_rule_count: /rules checked in this browser/.test(side),
    status: byId['status'].innerHTML || '',
    // disabled iff the programme no longer fits the device -- one owner, no mode
    transport_disabled: ['play', 'step', 'glide', 'phase', 'reset', 'slider', 'speed']
      .map(id => !!globalThis.document.getElementById(id).disabled),
    // `Step 1 / 0` (no frames), `Step 0 / N` (`frame` -1 after a tick over no frames)
    // and `undefined` (a frame that does not exist) are all the same invented step
    status_invents_a_step: /<b>Step (1<\/b> \/ 0|0<\/b>)|undefined/.test(byId['status'].innerHTML || ''),
    // the verdict coverage, three states per rule
    coverage: cov,
    n_checked: cov.filter(c => c[1] === 'checked' || c[1] === 'failed').length,
    n_unchecked: cov.filter(c => c[1] === 'unchecked' || c[1] === 'partial').length,
    // what leaves the page
    schema_errors: ED.schemaErrors(),
    export_error: exportErr,
    export_bytes: exported ? exported.length : 0,
    price: pr && !pr.blocked
      ? { cost: pr.totals.cost, steps: pr.totals.steps, us: pr.totals.us,
          frameChecked: pr.frameChecked, frameDrift: pr.frameDrift }
      : null,
    blocked: pr && pr.blocked ? pr.blocked.map(b => b.kind) : null,
    hardware: ED.hardware() ? { dacs: ED.hardware().dacs,
                                total_capacity: ED.hardware().total_capacity } : null,
    program_statements: ED.program().length,
    program_errors: ED.programErrors().map(e => e.message),
    authored: ED.authored(),
    // the programme pane's own words for an authored programme: the row count, the NOW
    // strip, the Write pane's error strip, the head's counters and chips, lede and title
    p_count: PG.pCount ? PG.pCount() : null,
    p_now: PG.pNow ? PG.pNow() : null,
    q_now: PG.qNow ? PG.qNow() : null,
    pw_err: PG.pwErr ? PG.pwErr() : null,
    counters: PG.counters ? PG.counters() : null,
    metrics: PG.metrics ? PG.metrics() : null,
    lede: PG.lede ? PG.lede() : null,
    doc_title: PG.docTitle ? PG.docTitle() : null,
    program_stale: globalThis.__stale ? globalThis.__stale() : null,
    banner: globalThis.__banner ? globalThis.__banner() : '',
    rules: ED.rules() ? { by_rule: ED.rules().by_rule,
                          messages: (ED.rules().messages || []).map(m => m.message),
                          oracle: ED.rules().oracle } : null,
    digest: ED.digest(),
  };
}

out.base = evidence('base');
// ONE STEP, THEN ITS EVIDENCE.  `applyScript(...).map(evidence)` ran every step first and
// then read the page once per record, so all N `after` snapshots described the FINAL
// state and a per-step assertion could never fail for the right reason.
out.steps = script.map((step, i) => ({ ...applyStep(ED, PG, step), after: evidence('s' + i) }));

// -------- the layout regime, at three widths and both aspect ratios -------------------
// `applyLayout` is called rather than inspected: the point of the change from a CSS class
// to an attribute is that the regime became READABLE, and `classList` is a no-op here.
out.layout_regimes = {};
for (const w of [800, 1000, 1400]) {
  globalThis.window.innerWidth = w;
  out.layout_regimes[String(w)] = PG.applyLayout();
}
globalThis.window.innerWidth = 1600;

// -------- the ruler, and the scale it reads in ----------------------------------------
// A DISTANCE IS A NUMBER OR IT IS NOTHING.  The whole point of the technology block is
// that a reader can put a figure in micrometres on any two points of this picture, and a
// screenshot cannot be asserted.  So the harness takes one real measurement -- the first
// two nodes, through the page's own snapping and its own arithmetic -- and reports it.
//
// It runs BEFORE the persistence probe deliberately: that probe adds a node and restores,
// and a measurement taken across an edit would be measuring the harness.
out.ruler = (() => {
  if (!ED.measureToggle) return null;
  const ns = PG.nodes(), L = PG.layout();
  const r = { on: ED.measureToggle(true), claim: ED.claim(0, 0, {}), n: ns.length };
  if (ns.length >= 2) {
    const at = n => [L.ox + n[1] * L.sx, L.oy + n[2] * L.sy];
    ED.measureClick(...at(ns[0]));
    const two = ED.measureClick(...at(ns[1]));
    r.two = { ids: two.points.map(q => q.id), kinds: two.points.map(q => q.kind),
              distance_um: two.distance_um, lattice: two.lattice,
              dx_um: two.dx_um, dy_um: two.dy_um };
    if (ns.length >= 3) {
      const three = ED.measureClick(...at(ns[2]));
      r.angle_deg = three.angle_deg;
    }
  }
  // the angle between two rails, from NODE positions in physical units -- correct even
  // when the fit has stretched one axis, which is the reason it is computed that way
  const segs = PG.segs(), by = Object.fromEntries(PG.nodes().map(q => [q[0], q]));
  let found = null;
  for (let i = 0; i < segs.length && !found; i++) {
    for (let j = i + 1; j < segs.length; j++) {
      const a = by[segs[i][1]], b = by[segs[i][2]], c = by[segs[j][1]], d = by[segs[j][2]];
      if (!a || !b || !c || !d) continue;
      const v1 = [b[1] - a[1], b[2] - a[2]], v2 = [d[1] - c[1], d[2] - c[2]];
      if (Math.abs(v1[0] * v2[1] - v1[1] * v2[0]) < 1e-9) continue;
      const mid = (x, y) => [(L.ox + (x[1] + y[1]) / 2 * L.sx), (L.oy + (x[2] + y[2]) / 2 * L.sy)];
      ED.measureClear();
      ED.measureClick(...mid(a, b));
      const rr = ED.measureClick(...mid(c, d));
      found = { rails: [segs[i][0], segs[j][0]], kinds: rr.points.map(q => q.kind),
                rail_angle_deg: rr.rail_angle_deg };
      break;
    }
  }
  r.rails = found;
  ED.measureToggle(false);
  r.off = ED.measureOn();
  r.claim_after = ED.claim(0, 0, {});
  return r;
})();
// -------- the sketch, and the two modes -----------------------------------------------
// A SHAPE IS A DEVICE OR IT IS NOTHING.  The ruler probe above proves this page can
// MEASURE; this one proves it can DRAW: the mode the page opens on, the four shape tools,
// and one rectangle laid down on a canvas of its own -- the sites it made, the orbit it
// declared, and the R19 / R20 / R21 findings it left behind (which must be none).
//
// It runs on a SCRATCH canvas and takes it back with one `undoGroup`, so the page the rest
// of this harness measures is the page Python shipped.
out.sketch = (() => {
  if (!ED.sketchDraw) return null;
  const r = { mode: ED.designMode(), tools: ED.sketchTools(),
              armed: ED.sketchTool('rect'), claim: ED.claim(0, 0, {}) };
  ED.sketchTool(null);
  r.claim_after = ED.claim(0, 0, {});
  const before = JSON.stringify(ED.digest());
  ED.newCanvas({ name: 'sketchprobe', template: null });
  const drew = ED.sketchDraw('rect', [0, 0], [6, 4], {});
  r.ok = !!drew.ok;
  r.problems = (drew.problems || []).map(p => p.code || p.message);
  if (drew.ok) {
    const st = ED.state(), dev = st.device;
    r.nodes = Object.keys(dev.nodes).length;
    r.segments = Object.keys(dev.segments).length;
    const lid = Object.keys(dev.loops)[0] || null;
    r.loop = lid ? { id: lid, n: dev.loops[lid].nodes.length,
                     closed: !!dev.loops[lid].closed, kind: dev.loops[lid].kind } : null;
    r.geometry_lints = globalThis.QCCD.lint(st)
      .filter(l => l.code === 'R19' || l.code === 'R20' || l.code === 'R21')
      .map(l => l.code);
    r.instance_members = ED.instanceMembers(drew.instance).length;
    ED.undoGroup();
    r.after_undo = Object.keys(ED.state().device.nodes).length;
  }
  // a refusal, named: a 30-degree corner is R20, and nothing is committed
  const tight = ED.sketchPlan('poly', { pts: [[0, 0], [6, 0], [0.3, 1]], closed: false }, null, {});
  r.tight = { ok: tight.ok, rule: tight.rule };
  ED.undo();                                   // the canvas record the probe pushed
  r.restored = JSON.stringify(ED.digest()) === before;
  ED.sketchTool(null);
  ED.setDesignMode(r.mode);                    // leave the page in the mode it opened on
  return r;
})();

// -------- the element menu ------------------------------------------------------------
// WHAT A RIGHT-CLICK OFFERS, as data.  Since the Selection popover went, this menu is the
// only per-element surface on the page, so "what can I do to this thing?" has to be
// assertable rather than a screenshot.
//
// It runs on a SCRATCH canvas, exactly as the sketch probe above does and for the same
// reason: `qccd studio` emits a page with NO device on it, and a probe that reported
// `null` there would assert nothing about the page anyone actually opens.  It INVOKES
// nothing -- it reads the items and the Modify panel's fields and closes with Escape --
// and the scratch device is taken back the same way, so the page the rest of this
// harness measures is the one Python shipped.
out.menu = (() => {
  if (!ED.menuOpen) return null;
  const was = ED.selection().map(s => ({ kind: s.kind, id: s.id }));
  const before = JSON.stringify(ED.digest());
  const r = { claim_on_empty: ED.claim(-5000, -5000, { button: 2 }) };
  ED.newCanvas({ name: 'menuprobe', template: null });
  const drew = ED.sketchDraw('rect', [0, 0], [6, 4], {});
  r.drew = !!drew.ok;
  if (!drew.ok) { ED.undo(); return r; }
  const L = PG.layout(), U = v => Number(globalThis.QCCD.unbox(v));
  const dev = ED.state().device;
  const nid = Object.keys(dev.nodes)[0];
  const n = dev.nodes[nid];
  const p = [L.ox + U(n.pos[0]) * L.sx, L.oy + U(n.pos[1]) * L.sy];
  r.claim_on_element = ED.claim(p[0], p[1], { button: 2 });
  const st = ED.menuOpen(p[0], p[1]);
  r.open = !!(st && st.open);
  r.subject = st ? st.subject : null;
  r.items = st ? st.items.map(i => [i.id, i.kind, i.enabled]) : [];
  // A DISABLED ITEM MUST SAY WHY.  An action that cannot run is never hidden and never
  // offered only to be refused one click later, so this is the property that matters.
  r.disabled_say_why = st ? st.items.every(i => i.kind === 'head' || i.enabled || !!i.why) : null;
  // the Modify panel: its rows, which applier owns each, and which of them this device
  // cannot write (a generator device has no builder statements to rewrite)
  const dlg = ED.modifyOpen();
  const ds = ED.menuState();
  r.modify = { ok: !!(dlg && dlg.ok),
               fields: ds.fields.map(f => [f.name, f.layer, f.enabled]),
               explode_offered: !!ds.explode };
  r.escape = ED.cancelGesture();
  const after = ED.menuState();
  r.closed = !after.open && !after.dialog;
  ED.undoGroup();                              // the shape
  ED.undo();                                   // the canvas record the probe pushed
  ED.select(was);
  r.restored = JSON.stringify(ED.digest()) === before;
  return r;
})();

// the true-scale toggle, both ways, back where it started
out.scale_toggle = (() => {
  if (!ED.setTrueScale) return null;
  const was = ED.trueScale();
  const off = ED.setTrueScale(false), lo = PG.layout();
  const a = { sx: lo.sx, sy: lo.sy, true_scale: !!lo.true_scale };
  ED.setTrueScale(true);
  const lt = PG.layout();
  const b = { sx: lt.sx, sy: lt.sy, true_scale: !!lt.true_scale };
  ED.setTrueScale(was);
  return { was, off, fit: a, on: b };
})();

// -------- persistence: a pure round trip through the one applier ----------------------
const d0 = JSON.stringify(ED.digest());
const p0 = JSON.stringify((ED.price() || {}).totals || null);
const snap = ED.snapshot();
out.snapshot_bytes = JSON.stringify(snap).length;
out.snapshot_kind = snap.kind;
out.autosave_ok = ED.autosave();
out.autoload_matches = JSON.stringify(ED.autoload()) === JSON.stringify(snap);
// PERTURB FIRST.  Restoring the state you just snapshotted is a no-op whatever `restore`
// does, so the round trip has to cross a real difference or it proves nothing -- which is
// exactly what the mutation guard for this test checks.
ED.setMode('edit');
const perturb = ED.addNodeAt(97.0, 97.0, { id: 'ZZ_probe', zone: 'data', capacity: 2 });
out.perturb_ok = perturb.ok;
out.perturbed_digest_differs = JSON.stringify(ED.digest()) !== d0;
const back = ED.restore(snap);
out.restore_ok = back.ok;
out.restore_same_digest = JSON.stringify(ED.digest()) === d0;
out.restore_same_price = JSON.stringify((ED.price() || {}).totals || null) === p0;

// -------- the palette, generated ------------------------------------------------------
const pal = ED.palette();
out.palette = pal.map(e => ({ type: e.type, kind: e.kind, verb: e.verb,
                              fields: e.fields.map(f => f.name),
                              inert: e.fields.filter(f => f.inert).map(f => f.name) }));

console.log(JSON.stringify(out));
