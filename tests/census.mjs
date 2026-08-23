// Execute an emitted QCCD page under a minimal DOM shim and census what it actually
// draws: ion-ion circle overlaps, and position jumps at frame boundaries.
//
//   node tests/census.mjs <page.html> [maxFrames] [--edit <script.json>]
//                                                 [--program <source.py>]
//
// With `--program` the page is driven through the PROGRAMME lane first -- the text is
// parsed, lowered and made the stage's frame list -- and then censused.  That is the
// only thing that would see `deriveStage` breaking `SLOTS`: the four stage tables were
// page-scope constants computed once at load, and turning them into a function that
// reassigns module-level bindings is the exact shape of change that produced 18 ions up
// to 48.4 px from their compiled site with a 14.68 px overlap.
//
// With `--edit` the page is driven through `tests/drive.mjs`'s step vocabulary FIRST and
// then censused, which is the lane the stale-programme defect lived in: `census.mjs` only
// ever ran on unedited pages, and the one copy of this scan that did run after an edit
// (`editor.mjs`'s private one) printed `worst_overlap_px: 14.684` to stdout for months
// while the delete test asserted on something else entirely.  There is now ONE scan,
// exported from here and imported there.
import fs from 'fs';
import { loadPage } from './shim.mjs';
import { applyScript, PAGE_HOOK } from './drive.mjs';

// --------------------------------------------------------------------- the scan
//
// `live` is a const inside draw(), so read positions and radii off the ion marks
// themselves -- IONP is module level. display:none means the ion is not on the stage.
//
// `frames` x `sub` are PARAMETERS, not constants: census.mjs walks every frame x 9
// sub-phases while editor.mjs's drag assertion walks 8 frames x 5.  Baking one grid in
// would silently change what `test_editor.py`'s existing assertion covers.
export function scan(probe, nframes, { maxFrames = Infinity, sub = 8,
                                       stale = null, ghosts = null } = {}) {
  const N = Math.min(nframes, maxFrames);
  let probed = 0, overlapFrames = 0, worstOverlap = 0, worstPair = null;
  let worstSnap = 0, snapAt = null, probeErr = null;
  let endOfPrev = null;
  // the three counters the stale-programme freeze is asserted on
  let ionsWhileInvalid = 0, ionsVisibleMax = 0, phantomIons = 0, phantomExample = null;

  for (let f = 0; f < N; f++) {
    for (let k = 0; k <= sub; k++) {
      const ph = k / sub;
      let pts;
      try { pts = probe(f, ph); }
      catch (e) { probeErr ||= e.message; continue; }
      probed++;

      if (pts.length > ionsVisibleMax) ionsVisibleMax = pts.length;
      // THE PRIMARY ASSERTION.  It is the only counter that covers the 18 mis-placed
      // ions of the measured defect, every one of which sat on a node that still exists
      // -- so no per-ion existence check could ever have found them.
      if (stale && stale() && pts.length > ionsWhileInvalid) ionsWhileInvalid = pts.length;
      if (ghosts && pts.length) {
        const g = ghosts(f);
        for (const p of pts) {
          if (g[p[0]] !== undefined) {
            phantomIons++;
            phantomExample ||= { frame: f, phase: ph, ion: p[0], node: g[p[0]] };
          }
        }
      }

      let bad = false;
      for (let i = 0; i < pts.length; i++) {
        for (let j = i + 1; j < pts.length; j++) {
          const a = pts[i], b = pts[j];
          if (!isFinite(a[3]) || !isFinite(b[3])) continue;
          const d = Math.hypot(a[1] - b[1], a[2] - b[2]);
          const depth = (a[3] + b[3]) - d;
          if (depth > 0.5) {
            bad = true;
            if (depth > worstOverlap) {
              worstOverlap = depth;
              worstPair = { frame: f, phase: ph, a: a[0], b: b[0],
                            centres_apart: +d.toFixed(2), ra: a[3], rb: b[3],
                            depth: +depth.toFixed(3) };
            }
          }
        }
      }
      if (bad) overlapFrames++;

      // the seam: end of frame f-1 (phase 1) against start of frame f (phase 0)
      if (k === 0 && endOfPrev) {
        const cur = new Map(pts.map(p => [p[0], p]));
        for (const [ion, p] of endOfPrev) {
          const q = cur.get(ion);
          if (!q) continue;
          const jump = Math.hypot(p[1] - q[1], p[2] - q[2]);
          if (jump > worstSnap && jump > 1e-6) {   // below this is float epsilon, not a snap
            worstSnap = jump; snapAt = { frame: f, ion };
          }
        }
      }
      if (k === sub) endOfPrev = new Map(pts.map(p => [p[0], p]));
    }
  }

  return {
    frames: nframes, probed,
    overlap_frames: overlapFrames,
    overlap_pct: probed ? +(100 * overlapFrames / probed).toFixed(1) : null,
    worst_overlap_px: +worstOverlap.toFixed(3),
    worst_pair: worstPair,
    worst_boundary_snap_px: +worstSnap.toFixed(3),
    snap_at: snapAt,
    ions_visible_max: ionsVisibleMax,
    ions_while_invalid: ionsWhileInvalid,
    phantom_ions: phantomIons,
    phantom_example: phantomExample,
    probe_error: probeErr,
  };
}

// --------------------------------------------------------------------- the CLI
// Importing this file must not run the CLI (editor.mjs imports `scan`), and
// `import.meta.url === 'file://' + process.argv[1]` does not hold on Windows -- the drive
// letter, the separators and the URL escaping all differ.  Comparing basenames is the
// check that is actually true on both platforms.
if ((process.argv[1] || '').replace(/\\/g, '/').split('/').pop() === 'census.mjs') {
  main();
}

function main() {
  // EXPLICIT parsing: `--edit` is a flag, never a third positional, so the existing
  // `[page, frames]` call sites keep meaning what they meant.
  const argv = process.argv.slice(2);
  let file = null, maxArg = null, editFile = null, progFile = null;
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--edit') { editFile = argv[++i]; continue; }
    if (argv[i] === '--program') { progFile = argv[++i]; continue; }
    if (file === null) { file = argv[i]; continue; }
    if (maxArg === null) { maxArg = argv[i]; continue; }
  }
  const script = editFile ? JSON.parse(fs.readFileSync(editFile, 'utf8')) : null;
  const progSrc = progFile ? fs.readFileSync(progFile, 'utf8') : null;

  // BEFORE loadPage, and it must be: the debounced re-pricer never fires under the shim,
  // so without the synchronous drain an edited page is censused against a STALE flag and
  // reports a clean freeze that never happened.  This is the single easiest way to write
  // a new test that passes for the wrong reason.
  if (script || progSrc) globalThis.__QCCD_SYNC = true;

  const driver = PAGE_HOOK + `
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
`;

  try { loadPage(file, driver); }
  catch (e) { console.log(JSON.stringify({ error: 'eval', message: e.message })); process.exit(2); }

  let log = null;
  if (script) {
    const ED = globalThis.EDITOR;
    if (!ED) { console.log(JSON.stringify({ error: 'no EDITOR on the page' })); process.exit(3); }
    if (!ED.ready()) {
      console.log(JSON.stringify({ error: 'editing unavailable', why: ED.why() }));
      process.exit(4);
    }
    log = applyScript(ED, globalThis.__page, script);
  }
  let prog = null;
  if (progSrc) {
    const ED = globalThis.EDITOR;
    if (!ED) { console.log(JSON.stringify({ error: 'no EDITOR on the page' })); process.exit(3); }
    if (!ED.ready()) {
      console.log(JSON.stringify({ error: 'editing unavailable', why: ED.why() }));
      process.exit(4);
    }
    const r0 = ED.applyProgramSource(progSrc);
    prog = { ok: r0.ok,
             // BOTH error channels: `applyProgramSource` reports PARSE errors, and the
             // lowering reports statements the device refuses.  A census that read only
             // the first would report a clean stage for a programme half of which was
             // silently rolled back.
             parse_errors: (r0.errors || []).map(e => e.message),
             lower_errors: ED.programErrors().map(e => e.message),
             statements: ED.program().length, frames: globalThis.__nframes(),
             blocked: ((ED.price() && ED.price().blocked) || []).map(b => b.kind) };
  }

  // EVERY frame by default. Defaulting to 300 meant a plain `node census.mjs <page>` run
  // censused 15% of the 1975-frame deck page and still printed a clean result -- a harness
  // that silently checks a sixth of the thing is worse than no harness.
  const r = scan(globalThis.__probe, globalThis.__nframes(), {
    maxFrames: +(maxArg || Infinity), sub: 8,
    stale: globalThis.__stale, ghosts: globalThis.__ghostNodes,
  });

  console.log(JSON.stringify(Object.assign(
    { file: file.split(/[\\/]/).pop() }, r,
    { program_stale: globalThis.__stale ? globalThis.__stale() : null,
      banner: globalThis.__banner ? globalThis.__banner() : '',
      edit_log: log, program: prog }), null, 1));
}
