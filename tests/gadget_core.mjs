// The gadget page's model, headless: node tests/gadget_core.mjs <build dir>
//
// Loads qccd/gadget/web/core.js the way the page does (it attaches GadgetCore to the global
// object), then the build's gir.json, library.json and leaf/*.js, and prints one JSON line:
//   final   master -> op -> { ion: node id } after the JS replay of every leaf program,
//           which tests/test_gadget_page.py compares with the Python verifier's replay;
//   samples a few model queries (running event, local time, carried ions, inventory).
import { readFileSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const dir = process.argv[2];
// `transit.js` first, exactly as `qccd/gadget/page.py` orders the two script tags: it is
// the occupancy law `LeafSim` places ions with, and it is shared with the studio stage.
new Function(readFileSync(join(here, "..", "qccd", "viz", "js", "transit.js"), "utf8"))();
new Function(readFileSync(join(here, "..", "qccd", "gadget", "web", "core.js"), "utf8"))();
const { Model, LeafSim } = globalThis.GadgetCore;

const gir = JSON.parse(readFileSync(join(dir, "gir.json"), "utf8"));
const library = JSON.parse(readFileSync(join(dir, "library.json"), "utf8"));
const M = new Model({ library, gir, checks: {} });

const final = {};
for (const f of readdirSync(join(dir, "leaf"))) {
  const text = readFileSync(join(dir, "leaf", f), "utf8");
  const json = text.slice(text.indexOf("(") + 1, text.lastIndexOf(")"));
  const data = JSON.parse(json);
  M.leafData[data.master] = data;
  const sim = new LeafSim(data);
  final[data.master] = {};
  for (const op in data.programs) {
    const P = sim.prepare(op);
    const out = {};
    P.ions.forEach((ion, i) => { out[ion] = data.device.nodes[P.final[i]].id; });
    final[data.master][op] = out;
    // the state at the end of the op must agree with the final positions
    const last = P.steps[P.steps.length - 1];
    const st = sim.state(op, last.t1);
    if (st.k !== P.steps.length - 1) throw new Error(`${data.master}.${op}: state at the end is step ${st.k}`);
  }
}

const samples = [];
const leaves = Object.keys(gir.leaves);
for (const leaf of leaves.slice(0, 12)) {
  for (const e of M.eventsOf(leaf)) {
    if (e[2] <= e[1]) continue;
    const mid = (e[1] + e[2]) / 2;
    const got = M.eventAt(leaf, mid);
    const lt = M.localTime(e, mid);
    samples.push({ leaf, id: e[0], got: got ? got[0] : null, local: lt.local, duration: lt.duration });
  }
}
let maxInChannel = 0, overfull = 0;
for (const net in M.byNet) {
  for (const c of M.byNet[net]) {
    for (const t of [c[4], (c[4] + c[11]) / 2, c[11] - 1e-3]) {
      const n = M.carryIons(c, t, gir.nets[net][4]).length;
      maxInChannel = Math.max(maxInChannel, n);
    }
  }
}
const home = {};
let inventoryAtEnd = 0, homeTotal = 0;
for (const leaf of leaves) {
  inventoryAtEnd += M.inventory(leaf, M.makespan + 1);
  homeTotal += gir.ion_order[gir.leaves[leaf][0]].length;
}
console.log(JSON.stringify({ final, samples, maxInChannel, inventoryAtEnd, homeTotal,
  activeAtHalf: M.activeInstructions(M.makespan / 2).length }));
