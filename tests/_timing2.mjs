import { loadPage } from './shim.mjs';
import { PAGE_HOOK } from './drive.mjs';
globalThis.__QCCD_SYNC = true;
loadPage(process.argv[2], PAGE_HOOK + ';globalThis.__ctx = () => ({PH, D, A, P});');
const { PH: PHY, D: DD, A: AA, P: PP } = globalThis.__ctx();
const ED = globalThis.EDITOR, Q = globalThis.QCCD;
const st = ED.state(), dev = st.device;

const classes = {};
for (const e of ((st.control.classes || {}).extra || [])) classes[e.id] = e;
const model = Q.makeModel(st.primitives, Q.degrees(dev), Q.cornerEndpoints(dev), dev.segments, {
  kind: (DD.model && DD.model.name) === 'deck' ? 'deck' : 'corrected',
  corner_hops: DD.model ? DD.model.corner_hops : 1,
  junction_min_degree: PHY.junction_min_degree || 3,
  length_scaling: !!(DD.model && DD.model.length_scaling),
  pitch: (DD.model && DD.model.pitch) || 1.0,
  include_anomalous: !(DD.model && DD.model.include_anomalous === false),
  anomalous_per_ms: PHY.anomalous_per_ms || 0,
  policy: PHY.policy });
model._pair = Q.pairIndex(dev);
function time(label, fn, n = 7) {
  let best = Infinity;
  for (let i = 0; i < n; i++) { const t = Date.now(); fn(); best = Math.min(best, Date.now() - t); }
  console.log(label.padEnd(26), best, 'ms');
}
time('priceFrames (no rules)', () => Q.priceFrames(PP.frames, AA.loops, model, classes));
time('checkFrames (rules)', () => Q.checkFrames(dev, PP.frames, AA.loops, model, classes, {
  zone_types: st.zone_types, max_simd: PP.max_simd_classes, gate_threshold: PHY.gate_threshold,
  models_heating: true, chain_limit: 15, state: st }));
console.log('rules:', JSON.stringify(Q.checkFrames(dev, PP.frames, AA.loops, model, classes, {zone_types: st.zone_types, max_simd: PP.max_simd_classes, gate_threshold: PHY.gate_threshold, models_heating: true, chain_limit: 15, state: st}).by_rule));
time('validateProgram', () => Q.validateProgram(dev, PP.frames, classes));

const base = { zone_types: st.zone_types, max_simd: PP.max_simd_classes,
               gate_threshold: PHY.gate_threshold, models_heating: true,
               chain_limit: 15, state: st };
time('rules: none', () => Q.checkFrames(dev, PP.frames, AA.loops, model, classes,
                                        Object.assign({}, base, { rules: [] })));
for (const r of Q.MIRRORED_RULES) {
  time('rules: ' + r, () => Q.checkFrames(dev, PP.frames, AA.loops, model, classes,
                                          Object.assign({}, base, { rules: [r] })));
}
