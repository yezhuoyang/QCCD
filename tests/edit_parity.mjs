// The JS half of the topology-edit parity harness.
//
// Reads a corpus of `{name, device, script}` cases on stdin (or from a file argument),
// runs `qccd/viz/js/edit.js` -- the SAME file the emitted page inlines -- over every
// script, and writes one trace record per edit to stdout.  `tests/test_edit_parity.py`
// produces the identical trace from `qccd/arch/edit.py` and diffs the two field by
// field.  Neither side ever reads the other's output, so a drift in either shows up as a
// diff rather than as a quietly-refreshed golden file.
//
//   node tests/edit_parity.mjs corpus.json > js_trace.json
//
// The trace deliberately carries the FULL device after every step, not just the final
// one: diffing per step is what turns "the ring came out with 168 nodes instead of 169"
// into "edit 7, remove_node S4, loop L0 node order differs at index 4".
import fs from 'fs';
import path from 'path';
import { fileURLToPath, pathToFileURL } from 'url';

const here = path.dirname(fileURLToPath(import.meta.url));
const modPath = process.env.QCCD_EDIT_JS ||
  path.join(here, '..', 'qccd', 'viz', 'js', 'edit.js');
const E = (await import(pathToFileURL(modPath).href)).default;

const src = process.argv[2]
  ? fs.readFileSync(process.argv[2], 'utf8')
  : fs.readFileSync(0, 'utf8');
const corpus = JSON.parse(src);
// `edit.js` holds no schema constant; the bounds come from `qccd/arch/schema.py` through
// the corpus, the same way they reach the page through `D.schema.bounds`.  No default and
// no fallback: a corpus without them must fail loudly rather than let the mirror guess.
E.setBounds(corpus.bounds);

// Deep-sort nothing and normalise nothing: the whole point is that the two sides agree
// on ORDER as well as on content, because the listing and the page both iterate
// segments and loop nodes in insertion order.
function traceOne(device, script) {
  const out = [];
  let cur = device;
  for (let i = 0; i < script.length; i++) {
    const edit = script[i];
    const rec = { i, op: edit.op, args: edit.args || {} };
    try {
      const [next, rep] = E.applyEdit(cur, edit);
      cur = next;
      rec.ok = true;
      rec.report = rep;
      rec.device = cur;
      rec.derived = E.derived(cur);
    } catch (err) {
      rec.ok = false;
      // Python raises TopologyError; the mirror raises EditError.  The harness compares
      // `error` after stripping the class name on both sides, so the MESSAGE is what has
      // to match -- a mirror that refuses the same edits for different stated reasons is
      // a mirror the user cannot trust to explain itself.
      rec.error = (err && err.name === 'EditError' ? 'TopologyError: ' : `${err.name}: `) +
                  (err && err.message);
    }
    out.push(rec);
  }
  return out;
}

const results = {};
for (const cse of corpus.cases) {
  results[cse.name] = traceOne(cse.device, cse.script);
}
process.stdout.write(JSON.stringify({ cases: results }, null, 0));
