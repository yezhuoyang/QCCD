// Drive the EDITOR of an emitted page, headlessly, and report what it did.
//
//   node tests/editor.mjs <page.html> [script.json]  ->  one JSON line on stdout
//
// The harness cannot synthesize a pointer event -- `Event` and `dispatchEvent` do not
// exist in the shim -- which is exactly why every pointer handler in `editor.js` is a thin
// adapter onto `EDITOR.begin/move/drop`.  Those are what this drives.  Any logic that had
// been left inside an event listener would be logic with no test at all.
//
// It runs against the EMITTED PAGE, not against the source `.js` files, so what it proves
// is the shipped editor and not a sibling copy.
import fs from 'fs';
import { loadPage } from './shim.mjs';
// ONE step vocabulary and ONE geometric scan, shared with `tests/census.mjs`.
// This file used to carry private copies of both; the census copy printed
// `worst_overlap_px: 14.684` on an edited page while a different copy was the one
// under assertion, so the number was on stdout the whole time and nothing read it.
import { applyStep, PAGE_HOOK } from './drive.mjs';
import { scan } from './census.mjs';

const file = process.argv[2];
const script = process.argv[3] ? JSON.parse(fs.readFileSync(process.argv[3], 'utf8')) : [];

// the synchronous drain: the debounced re-pricer would otherwise be invisible, because
// requestAnimationFrame and setTimeout are stubbed to never fire
globalThis.__QCCD_SYNC = true;

const HOOK = PAGE_HOOK + `
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
`;

let out = { file: file.split(/[\\/]/).pop(), steps: [] };
try {
  loadPage(file, HOOK);
} catch (e) {
  console.log(JSON.stringify({ fatal: 'eval: ' + e.message }));
  process.exit(2);
}
const ED = globalThis.EDITOR, PG = globalThis.__page;
if (!ED) { console.log(JSON.stringify({ fatal: 'the page published no EDITOR' })); process.exit(3); }

out.ready = ED.ready();
out.why = ED.why();
if (!out.ready) { console.log(JSON.stringify(out)); process.exit(0); }

// -------- a snapshot small enough to diff, complete enough to be worth diffing --------
function snap(label) {
  const pr = ED.price(), hw = ED.hardware(), L = PG.layout();
  return {
    label,
    edits: ED.edits().length,
    mode: ED.mode(),
    nodes: PG.nodes().length,
    segs: PG.segs().length,
    frames: PG.nframes(),
    authored: !!(globalThis.EDITOR.authored && globalThis.EDITOR.authored()),
    // the layout scalars an edit is most likely to move
    layout: { g: L.g, sx: L.sx, sy: L.sy, W: L.W, H: L.H, r_ion: L.r_ion,
              iso: L.iso, axis_aligned: L.axis_aligned, nbows: Object.keys(L.bows || {}).length },
    price: pr && !pr.blocked ? { cost: pr.totals.cost, steps: pr.totals.steps,
                                 us: pr.totals.us, comp: pr.comp, peak: pr.peak,
                                 transits: pr.transits, frameDrift: pr.frameDrift,
                                 frameChecked: pr.frameChecked } : null,
    blocked: pr && pr.blocked ? pr.blocked.map(b => b.kind) : null,
    hw: hw ? { dacs: hw.dacs, electrodes: hw.electrodes, switches: hw.switches,
               n_junctions: hw.n_junctions, total_capacity: hw.total_capacity,
               degree_histogram: hw.degree_histogram, over_budget: hw.over_budget } : null,
    problems: ED.problems().length,
    lints: ED.lints().map(l => l.code),
    coverage: PG.coverage(),
    n_checked: PG.coverage().filter(c => c[1] === 'checked' || c[1] === 'failed').length,
    n_unchecked: PG.coverage().filter(c => c[1] === 'unchecked' || c[1] === 'partial').length,
    side_says_count: /rules checked in this browser/.test(PG.side() || ''),
    side_says_all_pass: /all rules pass/i.test(PG.side() || ''),
  };
}

// -------- the geometric census, re-run after an edit ---------------------------------
// A drag must NOT be allowed to reintroduce ion overlap.  `2*r_ion = 0.48*g` makes overlap
// structurally impossible ONLY if `g` is recomputed, so this is the assertion that proves
// the client-side re-layout actually happened rather than redrawing at a stale scale.
//
// THE SCAN ITSELF LIVES IN `census.mjs`.  The 8-frames x 5-sub-phase grid stays here,
// because that is what `test_editor.py`'s drag assertion has always covered and baking
// census.mjs's every-frame x 9 grid in would silently change what it covers.
function census(maxFrames) {
  const r = scan(globalThis.__probe, PG.nframes(), {
    maxFrames: maxFrames || 12, sub: 4,
    stale: globalThis.__stale, ghosts: globalThis.__ghostNodes,
  });
  return { overlap_frames: r.overlap_frames, worst_overlap_px: r.worst_overlap_px,
           worst_pair: r.worst_pair && [r.worst_pair.a, r.worst_pair.b, r.worst_pair.depth],
           ions_visible_max: r.ions_visible_max,
           ions_while_invalid: r.ions_while_invalid,
           phantom_ions: r.phantom_ions, phantom_example: r.phantom_example };
}

// ===================================================================== THE DIRECT-
// MANIPULATION CENSUS
//
// THE CLAIM UNDER TEST IS NOT "the avatar looks right".  It is: the avatar of a site and
// the stage's own drawing of that same site are THE SAME MARKS.  If that holds, drift is
// impossible, because there is only one renderer.
//
// NOTHING HERE MUTATES THE DEVICE.  A census that emitted `set_site_capacity` to sweep
// the bar length would leave edits in the stack and every assertion about `out.edits` in
// `test_editor.py` would be counting this harness's own footprints.  The sweep across
// capacities is a SCRIPT, in `test_editor.py`, driven through the ordinary verbs.
// THE COMPARISON IS SCALE-NORMALISED, and it has to be: the avatar is a WINDOW onto a
// micro-device laid out at g = 72 while the ring page's own stage sits at g = 21.9155.
// Every mark in this codebase is a fixed fraction of g, so dividing by g is what turns
// "the same picture" into a number.  The paint attributes are compared VERBATIM -- a
// colour has no scale, and a zone drawn in the wrong colour is the defect this exists
// to catch.
//
// 4 decimals, because the layout scalars ship quantized to 3 dp: at g = 21.9155 that is a
// quantum of 2.3e-5 in units of g, so a tighter comparison would be measuring the
// rounding rather than the drawing.
const GEOM = ['width', 'height', 'rx', 'stroke-width'];
const PAINT = ['fill', 'fill-opacity', 'stroke', 'stroke-opacity'];
const at = (r, k) => (r.attrs ? r.attrs[k] : r.getAttribute(k));
const shapeOf = (r, g) => !r ? null
  : GEOM.map(k => k + '=' + (+(+at(r, k) / g).toFixed(4))).join(' ') + ' ' +
    PAINT.map(k => k + '=' + at(r, k)).join(' ');

// EVERY SITE ON THE PAGE, compared attribute for attribute against the avatar the element
// menu would show for it.  The capacities present on the shipped pages are 1..3, i.e.
// BELOW the point where `siteLen` saturates at `site_max = 0.88*g` -- which matters: a
// forked renderer with a different length coefficient still matches at cap 6.
function avatarParity() {
  if (!ED.elementAvatar || !ED.stageMark || !ED.state()) return null;
  const dev = ED.state().device, rows = [], caps = {};
  for (const [id] of PG.nodes()) {
    const n = dev.nodes[id];
    if (!n || n.kind !== 'site') continue;
    // deg and corner are asked for BY NAME: a dock draws in gold and a corner in the
    // corner colour, so an avatar that ignored them would "match" only the plain nodes
    // and quietly stop testing the two cases that differ.
    const meta = PG.nodes().find(r => r[0] === id);
    const av = ED.elementAvatar('site', { cap: n.cap, zone: n.zone,
                                          deg: meta ? meta[3] : 0,
                                          corner: !!(meta && meta[5]) });
    const grp = av.children[0].children[0];
    const rect = grp.children.find(x => x.tagName === 'rect');
    const ticks = grp.children.filter(x => x.tagName === 'circle').length;
    caps[n.cap] = (caps[n.cap] || 0) + 1;
    rows.push({ id, cap: n.cap,
                match: shapeOf(ED.stageMark(id), PG.layout().g) === shapeOf(rect, 72),
                bar_over_g: rect ? +(+(rect.attrs || {}).width / 72).toFixed(4) : null,
                ticks });
    if (rows.length >= 40) break;
  }
  const bad = rows.filter(r => !r.match);
  return { n: rows.length, caps: Object.keys(caps).map(Number).sort(),
           all_match: rows.length > 0 && bad.length === 0,
           unsaturated: rows.some(r => r.cap <= 3),
           first_bad: bad[0] || null, sample: rows[0] || null };
}

// every avatar scene must land on the SAME derived scale, or one tile silently renders at
// a different zoom than its neighbours and nothing complains
function avatarScales() {
  if (!ED.avatarScenes) return null;
  const S = ED.avatarScenes();
  return Object.keys(S).map(t => [t, globalThis.QCCD.computeLayout(
    S[t].nodes.map(q => ({ id: q[0], x: q[1], y: q[2] })),
    S[t].segs.map(q => ({ id: q[0], a: q[1], b: q[2] }))).g]);
}

// The MENU as it is actually emitted: one tile per palette entry, each carrying an avatar
// with real marks in it and the exact verb it will emit.  `pal-item`, `avatar` and
// `data-add` were all ZERO on the shipped page -- the palette data was computed and then
// thrown away.
function paletteCensus() {
  const host = PG.palBody ? PG.palBody() : null;
  const out = { items: 0, avatars: 0, marks: 0, empty_avatars: 0, kinds: {}, verbs: [],
                zone_chips: 0 };
  if (!host) return out;
  (function walk(n) {
    for (const c of (n.children || [])) {
      const at = c.attrs || {}, cls = at['class'] || '';
      if (cls === 'pal-item') {
        out.items++;
        out.kinds[at['data-kind']] = (out.kinds[at['data-kind']] || 0) + 1;
        out.verbs.push(at['data-el'] + ':' + (at['data-add'] || ''));
      }
      if (cls === 'zonechip') out.zone_chips++;
      if (cls === 'avatar') {
        out.avatars++;
        let m = 0;
        (function count(e) {
          for (const k of (e.children || [])) {
            if (['rect', 'circle', 'line', 'path', 'polyline'].indexOf(k.tagName) >= 0) m++;
            count(k);
          }
        })(c);
        out.marks += m;
        if (!m) out.empty_avatars++;
      }
      walk(c);
    }
  })(host);
  return out;
}

// THE REACH CENSUS -- one number per kind, collapsing every "I clicked the thing and
// nothing happened".  Sampled (24 nodes / 24 segments / 1 loop) because this harness runs
// over the whole page corpus.
function reach(cap) {
  cap = cap || 24;
  const L = PG.layout(), R = ED.hitRadii ? ED.hitRadii() : null;
  const out = { node: [0, 0], seg: [0, 0], loop: [0, 0], threw: 0, msg: null };
  if (!R) return out;
  const AX = PG.axis ? PG.axis() : {};
  const safe = f => { try { return f(); } catch (e) { out.threw++; out.msg = e.message; return null; } };
  const nodes = PG.nodes(), segs = PG.segs();
  const stepN = Math.max(1, Math.ceil(nodes.length / cap));
  for (let ix = 0; ix < nodes.length; ix += stepN) {
    const [id, x, y] = nodes[ix];
    const half = R.node[id], ax = AX[id] || { ux: 1, uy: 0 };
    const cx = L.ox + x * L.sx, cy = L.oy + y * L.sy;
    for (let i = -12; i <= 12; i++) {
      const d = (i / 12) * half;
      const h = safe(() => ED.hit(cx + ax.ux * d, cy + ax.uy * d));
      out.node[1]++; if (h && h.id === id) out.node[0]++;
    }
  }
  const stepS = Math.max(1, Math.ceil(segs.length / cap));
  for (let ix = 0; ix < segs.length; ix += stepS) {
    const [id, a, b] = segs[ix];
    const na = nodes.find(n => n[0] === a), nb = nodes.find(n => n[0] === b);
    if (!na || !nb) continue;
    const ax = L.ox + na[1] * L.sx, ay = L.oy + na[2] * L.sy;
    const bx = L.ox + nb[1] * L.sx, by = L.oy + nb[2] * L.sy;
    for (let i = 0; i <= 50; i++) {
      const t = i / 50;
      const h = safe(() => ED.hit(ax + (bx - ax) * t, ay + (by - ay) * t));
      out.seg[1]++; if (h && h.id === id) out.seg[0]++;
    }
  }
  // A LOOP IS GRABBABLE BY ITS OWN HALO, in the band the rails do not already own, so the
  // probe walks PARALLEL to the orbit -- probing along it would measure the segment.
  const loops = PG.loops(), lids = Object.keys(loops);
  if (lids.length) {
    const w = loops[lids[0]];
    for (let i = 0; i <= 50; i++) {
      const t = (i / 50) * w.length, k = Math.floor(t) % w.length, f = t - Math.floor(t);
      const q0 = nodes.find(n => n[0] === w[k]), q1 = nodes.find(n => n[0] === w[(k + 1) % w.length]);
      if (!q0 || !q1) continue;
      const X = L.ox + (q0[1] + (q1[1] - q0[1]) * f) * L.sx;
      const Y = L.oy + (q0[2] + (q1[2] - q0[2]) * f) * L.sy;
      const dx = q1[1] - q0[1], dy = q1[2] - q0[2], hyp = Math.hypot(dx, dy) || 1;
      const off = 0.19 * L.g;
      const h = safe(() => ED.hit(X - dy / hyp * off, Y + dx / hyp * off));
      out.loop[1]++; if (h && h.kind === 'loop') out.loop[0]++;
    }
  }
  return out;
}

// THE HIGHLIGHT IS DERIVED FROM WHAT WAS DRAWN, not computed a second time.  A regression
// that goes back to a circle of `0.55*L.g` fails this without needing a screenshot.
function outlineIsTheMark() {
  if (!ED.outline || !ED.nodeRec) return null;
  const S = ED.slop(), rows = [];
  // SITES, not the first sixteen nodes: `deck_unit_cell` opens with 16 junctions, and a
  // census that silently measured nothing there reported `all: false` for the right
  // reason and the wrong one at the same time.
  for (const [id] of PG.nodes()) {
    if (rows.length >= 16) break;
    const rec = ED.nodeRec(id), o = ED.outline('site', id);
    if (!rec || rec.kind !== 'site' || !o) continue;
    rows.push({ id, is_rect: o.tag === 'rect',
      width_is_bar: Math.abs(o.width - rec.len) < 1e-9,
      thicker_by_slop: Math.abs(o.height - (PG.layout().site_t + 2 * S)) < 1e-6,
      names_the_angle: String(o.transform || '').indexOf('rotate(' + rec.ang + ' ') === 0 });
  }
  return { n: rows.length,
           all: rows.length > 0 && rows.every(r => r.is_rect && r.width_is_bar &&
                                                   r.thicker_by_slop && r.names_the_angle),
           first: rows[0] || null };
}

out.steps.push(snap('base'));
out.census_base = census(8);
out.palette = ED.palette().map(e => ({ type: e.type, kind: e.kind, verb: e.verb,
  fields: e.fields.length, inert: e.fields.filter(f => f.inert).length,
  avatar_len: ED.avatarMarkup ? ED.avatarMarkup(e.type).length : 0 }));
out.palette_dom = paletteCensus();
out.docs_cover_palette = ED.elementDocs
  ? ED.palette().every(e => !!ED.elementDocs()[e.type]) : null;
out.avatar_scales = avatarScales();
out.reach_base = reach();
out.outline_is_mark = outlineIsTheMark();
// THE ARBITER: who owns a press, in edit mode and in play mode.  Pure, so asking costs
// nothing and changes nothing.
(function () {
  const L = PG.layout(), n = PG.nodes()[0], wasMode = ED.mode();
  const at = n ? [L.ox + n[1] * L.sx, L.oy + n[2] * L.sy] : [0, 0];
  if (!ED.claim) return;
  ED.setMode('edit');
  out.claim = { on_element: ED.claim(at[0], at[1], {}),
                on_empty: ED.claim(-500, -500, {}),
                with_space: ED.claim(at[0], at[1], { space: true }),
                middle: ED.claim(at[0], at[1], { button: 1 }),
                right: ED.claim(at[0], at[1], { button: 2 }) };
  ED.setMode('play');
  out.claim.play_mode = ED.claim(at[0], at[1], {});
  ED.setMode(wasMode);
})();
out.vb_base = PG.vb ? PG.vb() : null;

for (const step of script) {
  const rec = applyStep(ED, PG, step);
  rec.after = snap(step.label || step.do);
  out.steps.push(rec.after);
  out.log = (out.log || []).concat([rec]);
}

out.census_edited = census(8);
out.reach_edited = reach();
out.avatar_parity = avatarParity();
out.palette_dom_edited = paletteCensus();
// THE PAN VIEWBOX.  Dragging a node used to pan the stage out from under the gesture
// because the page's pan handler had no mode guard; this is the number that says so.
out.vb_edited = PG.vb ? PG.vb() : null;
out.cursor = PG.cursor ? PG.cursor() : null;
out.selection = ED.selection().map(x => x.kind + ':' + x.id);
out.armed = ED.armed ? ED.armed() : null;
out.inspector = PG.palInsp ? (PG.palInsp().innerHTML || '') : '';
out.program_stale = globalThis.__stale ? globalThis.__stale() : null;
out.banner = globalThis.__banner ? globalThis.__banner() : '';
out.program_breaks = ED.programBreaks ? ED.programBreaks().map(b => b.kind) : null;
// the op list itself, which is what "Copy edits" hands over and what
// `Machine.apply_edits` replays -- so a test can close the loop
out.edits = ED.edits();
out.source = ED.source();
// NOT truncated: a test that only sees the first few kilobytes of the export cannot
// tell whether the tail is valid Python, and the topology block is at the tail.
// The export can now REFUSE -- see `refuseExport` in editor.js.  A harness that let the
// throw escape would report "the page crashed" for the one case the refusal exists to
// handle, so the refusal is recorded as data and the caller asserts on it.
out.schema_errors = ED.schemaErrors ? ED.schemaErrors() : [];
try { out.python = ED.exportPython(); }
catch (e) { out.python = null; out.export_refused = String(e.message || e); }
try { out.arch_json = ED.exportJson(); }
catch (e) { out.arch_json = null; out.export_refused = String(e.message || e); }
out.json_ok = (() => { try { return JSON.parse(out.arch_json) !== null; } catch (e) { return false; } })();
console.log(JSON.stringify(out));
