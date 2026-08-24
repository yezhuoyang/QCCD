// Execute an emitted page and report what the SOURCE CIRCUIT pane renders.
//
// `panels.mjs` measures the hardware listing; this measures the second listing a compiled
// page carries -- the user's QASM -- and the one property that makes it worth having: on
// every frame, the statement highlighted is the statement the executing instruction is
// discharging. A pane that merely displayed the circuit would be a text dump; the join is
// the whole feature, and the join is what silently rots.
//
// Same shim as every other page harness, deliberately. Note two of its traps bite here:
// `classList` is a no-op, so the row state must be readable from `className` (it is --
// `makeList` assigns the whole string), and `remove()` does nothing, so rows are pooled.
import { loadPage } from './shim.mjs';

const file = process.argv[2];
const driver = `
;globalThis.__src = (fi) => {
  frame = fi; phase = 1; draw();
  const rows = [];
  for (const r of document.getElementById('qWin').children) {
    if (r.style.display === 'none' || r._i < 0) continue;
    rows.push({ row: r._i, cls: r.className || '', n: r._k[0].textContent,
                text: r._k[1].textContent });
  }
  return {
    lines: QLIST ? QLIST.rows() : 0,
    cursor: QLIST ? QLIST.cursor() : -2,
    now: document.getElementById('qNow').innerHTML || '',
    inline: document.getElementById('pNow').innerHTML || '',
    marks: QMARK || {},
    rows,
  };
};
globalThis.__srcMeta = () => ({
  present: !!SRC,
  tab: document.getElementById('tabQ').className,
  ops: SRC ? SRC.ops.length : 0,
  realises: SRC ? Object.keys(SRC.realises).length : 0,
  toward: SRC ? Object.keys(SRC.toward).length : 0,
  after: SRC ? Object.keys(SRC.after || {}).length : 0,
  frameIds: P.frames.map(f => f.id),
  // the inverse index the click-through uses
  jump: (line) => { pickSrc(line - 1); return frame; },
});
`;

try { loadPage(file, driver); }
catch (e) {
  console.log(JSON.stringify({ error: 'eval', message: e.message, stack: e.stack }));
  process.exit(2);
}

const meta = globalThis.__srcMeta();
const probes = [];
const n = meta.frameIds.length;
for (let i = 0; i < n; i++) probes.push({ frame: i, ...globalThis.__src(i) });

// the click-through, on every line that carries a statement
const jumps = [];
if (meta.present) {
  const seen = new Set();
  for (const p of probes) {
    for (const r of p.rows) {
      const line = Number(r.n);
      if (seen.has(line) || !/qh|qw/.test(r.cls)) continue;
      seen.add(line);
      jumps.push({ line, frame: meta.jump(line) });
    }
  }
}
console.log(JSON.stringify({
  present: meta.present, tab: meta.tab, ops: meta.ops,
  realises: meta.realises, toward: meta.toward, after: meta.after,
  frameIds: meta.frameIds, probes, jumps,
}));
