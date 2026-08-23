// ONE definition of what an edit script means.
//
// `tests/editor.mjs` owned this vocabulary privately, and `tests/census.mjs` now needs the
// same one to census an EDITED page.  A second interpreter would be two harnesses that
// disagree about what `{"do":"remove"}` does -- the same shape of problem `tests/shim.mjs`
// was created to end (three copies of one shim is a two-implementations bug in miniature).
//
// A step is `{do: <verb>, ...}` and the verbs are exactly the EDITOR gestures that are
// callable without an Event, which is why `editor.js`'s pointer handlers are thin adapters
// onto `EDITOR.begin/move/drop` in the first place:
//
//   {"do":"mode","mode":"edit"}                       enter/leave edit mode
//   {"do":"drag","id":"S5","to":[x,y],free?,fine?}    the whole gesture, in MODEL coords
//   {"do":"emit","op":{method,args,kwargs}}           one structured call
//   {"do":"source","src":"..."}                       the text lane
//   {"do":"addSegment","a":"S0","b":"S9"}
//   {"do":"addSite","x":..,"y":..,"near":"S0"}
//   {"do":"remove","sel":[{"kind":"node","id":"S5"}]} select-then-removeSelected
//   {"do":"undo"} / {"do":"redo"}
//   {"do":"hit","mx":..,"my":..}
//
// and the direct-manipulation verbs -- still the SAME vocabulary, extended.  Every one of
// them is an EDITOR gesture callable without an Event, which is why the pointer handlers
// are one-line adapters onto them:
//   {"do":"claim","mx":..,"my":..,"mod":{space?,button?}}   who owns this press
//   {"do":"hover","mx":..,"my":..}                          the hit + the cursor it sets
//   {"do":"arm","type":"site"}                              arm a palette element
//   {"do":"stamp","type":"site","at":[x,y],"via":[[x,y]..]} place one, in MODEL coords
//   {"do":"dragKind","kind":"segment","id":"E0","to":[x,y]} drag a segment or a loop
//   {"do":"marquee","from":[x,y],"to":[x,y],"additive":..}  rubber-band select
//   {"do":"key","k":"Escape"}                               a key, through the editor
//   {"do":"closeLoop"}                                      close a loop over the selection
//
// and the design-tool verbs, which are the same shape:
//   {"do":"canvas","opts":{name,template}}            a blank canvas
//   {"do":"generator","gen":"ring","params":{...}}    a device from a generator
//   {"do":"node","x":..,"y":..,"opts":{...}}          place a site or a junction
//   {"do":"join","a":"N0","b":"N1","opts":{...}}      a segment
//   {"do":"loop","id":"L0","walk":[...],"closed":..}  a transport loop
//   {"do":"zone","zone":"data","fields":{...}}
//   {"do":"transaction","ops":[...]}                  N ops, atomically
//   {"do":"explode"}                                  to explicit geometry
//   {"do":"prog","src":"p.init({...}) ..."}             the programme, as text
//   {"do":"pemit","op":{method,args,kwargs}}          one programme statement
//   {"do":"snapshot"} / {"do":"restore","snap":...}
//
// `applyScript` returns one record per step (never throws for a step that failed: a
// refusal is DATA, and a harness that let it escape would report "the page crashed" for
// the one case the refusal exists to handle).

export function applyStep(ED, PG, step) {
  const rec = { do: step.do };
  try {
    if (step.do === 'mode') { ED.setMode(step.mode); }
    else if (step.do === 'drag') {
      // the whole gesture, without an Event: begin at the node, move to the target,
      // drop.  Model coordinates, exactly what the pointer adapter would compute.
      const L = PG.layout();
      const n = PG.nodes().find(r => r[0] === step.id);
      ED.begin('site', step.id, L.ox + n[1] * L.sx, L.oy + n[2] * L.sy);
      const r = ED.move(L.ox + step.to[0] * L.sx, L.oy + step.to[1] * L.sy,
                        { free: !!step.free, fine: !!step.fine });
      rec.warnings = r ? r.warnings : null;
      rec.result = ED.drop();
      if (rec.result) rec.result = { problems: rec.result.problems.map(p => p.code) };
    }
    else if (step.do === 'emit') {
      rec.result = ED.emit(step.op);
      if (rec.result) rec.result = { ok: rec.result.ok,
                                     problems: (rec.result.problems || []).map(p => p.code || p.message) };
    }
    else if (step.do === 'source') { rec.result = ED.applySource(step.src); }
    else if (step.do === 'addSegment') { const r = ED.addSegment(step.a, step.b); rec.result = { ok: r.ok, problems: r.problems.map(p => p.message) }; }
    else if (step.do === 'addSite') { const r = ED.addSite(step.x, step.y, step.near); rec.result = { ok: r.ok, problems: r.problems.map(p => p.message) }; }
    else if (step.do === 'remove') { ED.select(step.sel); const r = ED.removeSelected(); rec.result = { ok: r.ok, problems: r.problems.map(p => p.message) }; }
    else if (step.do === 'undo') { ED.undo(); }
    else if (step.do === 'redo') { ED.redo(); }
    else if (step.do === 'hit') { rec.result = ED.hit(step.mx, step.my); }
    // `hitModel` takes LATTICE coordinates and converts, so a script can say "the point
    // where N0 is" without knowing the current scale
    else if (step.do === 'hitModel') {
      const L = PG.layout();
      rec.result = ED.hit(L.ox + step.x * L.sx, L.oy + step.y * L.sy);
    }
    else if (step.do === 'claim') {
      rec.result = ED.claim(step.mx, step.my, step.mod || {});
    }
    else if (step.do === 'hover') {
      rec.result = ED.hover(step.mx, step.my);
      rec.cursor = ED.cursor();
    }
    else if (step.do === 'arm') { rec.result = ED.arm(step.type); rec.armed = ED.armed(); }
    else if (step.do === 'stamp') {
      const L = PG.layout();
      ED.arm(step.type);
      ED.ghostBegin(step.type, L.ox + step.at[0] * L.sx, L.oy + step.at[1] * L.sy);
      for (const q of (step.via || [])) ED.ghostMove(L.ox + q[0] * L.sx, L.oy + q[1] * L.sy,
                                                     step.opts || {});
      const r = ED.ghostDrop();
      rec.result = { ok: r.ok, id: r.id,
                     problems: (r.problems || []).map(p => p.code || p.message) };
    }
    else if (step.do === 'dragKind') {
      const L = PG.layout();
      const g = ED.begin(step.kind, step.id, L.ox + step.from[0] * L.sx,
                         L.oy + step.from[1] * L.sy);
      rec.ids = g ? g.ids : null;
      if (!g) { rec.result = null; }
      else {
        ED.move(L.ox + step.to[0] * L.sx, L.oy + step.to[1] * L.sy,
                { free: !!step.free, fine: !!step.fine });
        const r = ED.drop();
        rec.result = r ? { n: r.ops.length, problems: r.problems.map(p => p.code) } : null;
      }
    }
    else if (step.do === 'marquee') {
      const L = PG.layout();
      ED.marqueeBegin(L.ox + step.from[0] * L.sx, L.oy + step.from[1] * L.sy);
      ED.marqueeMove(L.ox + step.to[0] * L.sx, L.oy + step.to[1] * L.sy);
      rec.result = ED.marqueeDrop({ additive: !!step.additive })
        .map(x => x.kind + ':' + x.id);
    }
    else if (step.do === 'select') { rec.result = ED.select(step.sel || []).map(x => x.kind + ':' + x.id); }
    else if (step.do === 'key') {
      // the editor's OWN key semantics, not a synthesized KeyboardEvent: the shim has no
      // Event constructor, and a key that only worked through one would be a key with no
      // test.  `Escape` is the interesting one -- it must cancel a live drag.
      if (step.k === 'Escape') {
        if (ED.cancelGesture) rec.result = ED.cancelGesture();
      } else rec.error = 'no headless binding for key ' + step.k;
    }
    else if (step.do === 'closeLoop') {
      const r = ED.closeLoopFromSelection(step.opts || {});
      rec.result = { ok: r.ok, problems: (r.problems || []).map(p => p.code || p.message) };
    }
    else if (step.do === 'form') { rec.result = ED.openForm(step.type, step.opts || null); }
    // ---- the design-tool verbs.  ONE step vocabulary, extended, never forked ---------
    else if (step.do === 'canvas') {
      const r = ED.newCanvas(step.opts || {});
      rec.result = { ok: r.ok, problems: (r.problems || []).map(p => p.code || p.message) };
    }
    else if (step.do === 'generator') {
      const r = ED.newFromGenerator(step.gen, step.params || {}, step.opts || {});
      rec.result = { ok: r.ok, problems: (r.problems || []).map(p => p.code || p.message) };
    }
    else if (step.do === 'node') {
      const r = ED.addNodeAt(step.x, step.y, step.opts || {});
      rec.result = { ok: r.ok, id: r.id,
                     problems: (r.problems || []).map(p => p.code || p.message) };
    }
    else if (step.do === 'join') {
      const r = ED.joinNodes(step.a, step.b, step.opts || {});
      rec.result = { ok: r.ok, id: r.id,
                     problems: (r.problems || []).map(p => p.code || p.message) };
    }
    else if (step.do === 'loop') {
      const r = ED.closeLoop(step.id, step.walk, step.closed !== false, step.kind || 'ring');
      rec.result = { ok: r.ok, problems: (r.problems || []).map(p => p.code || p.message) };
    }
    else if (step.do === 'zone') {
      const r = ED.nameZone(step.zone, step.fields || {});
      rec.result = { ok: r.ok, problems: (r.problems || []).map(p => p.code || p.message) };
    }
    else if (step.do === 'transaction') {
      const r = ED.transaction(step.ops || [], step.label || 'txn');
      rec.result = { ok: r.ok, problems: (r.problems || []).map(p => p.code || p.message) };
    }
    else if (step.do === 'explode') {
      const r = ED.explodeToExplicit();
      rec.result = { ok: r.ok, statements: r.statements, warning: r.warning };
    }
    else if (step.do === 'prog') {
      const r = ED.applyProgramSource(step.src);
      rec.result = { ok: r.ok, errors: (r.errors || []).map(e => e.message) };
    }
    else if (step.do === 'pemit') {
      const r = ED.emitProgram(step.op);
      rec.result = { ok: r.ok, problems: (r.problems || []).map(p => p.message) };
    }
    else if (step.do === 'snapshot') { rec.snap = ED.snapshot(); }
    else if (step.do === 'restore') {
      const r = ED.restore(step.snap);
      rec.result = { ok: r.ok, problems: (r.problems || []).map(p => p.message) };
    }
    else rec.error = 'unknown step ' + step.do;
  } catch (err) {
    rec.error = err.name + ': ' + err.message;
  }
  return rec;
}

export function applyScript(ED, PG, script) {
  return (script || []).map(step => applyStep(ED, PG, step));
}

// The probe hook every driver installs. `PG.drawAt` is what lets a census walk the stage
// without synthesizing a pointer event or an animation frame.
export const PAGE_HOOK = `
;globalThis.__page = {
  nodes: () => A.nodes.map(n => [n.id, n.x, n.y, n.deg, n.cap, !!n.corner]),
  segs:  () => A.segments.map(s => [s.id, s.a, s.b, s.len, s.corner_endpoints]),
  loops: () => A.loops,
  layout: () => L,
  ionMarks: () => { const o=[]; for(const i in IONP){ const c=IONP[i].c;
      if(!c || c.attrs.display==='none') continue; o.push([i,+c.attrs.cx,+c.attrs.cy,+c.attrs.r]); } return o; },
  drawAt: (f, ph) => { frame=f; phase=ph; draw(); },
  nframes: () => P.frames.length,
  // the Machine pane, so a test can check that the R1-R18 verdicts are honest after an
  // edit rather than left standing as a green tick for a check that did not run
  side: () => document.getElementById('side').innerHTML,
  report: () => document.getElementById('report').innerHTML,
  // THE VERDICT COVERAGE, three states per rule.  RULES_STALE -- a single boolean that
  // struck all 23 through at once -- is gone: 17 of them are re-derived client-side off
  // the pricing walk, so only the other 6 go grey, and a test that asked "are they stale?"
  // was asking a question with no answer any more.
  // (No backticks anywhere in this hook: it is itself a template literal.)
  coverage: () => (globalThis.EDITOR && globalThis.EDITOR.ruleCoverage)
    ? globalThis.EDITOR.ruleCoverage().map(c => [c.rule, c.state, c.count]) : [],
  // THE LAYOUT REGIME, readable for the first time.  It used to be a CSS class written
  // once at load, and classList is a no-op in the shim -- so the one piece of page state
  // no harness could see was the one that decides the whole layout.
  applyLayout: () => applyLayout(),
  dataLayout: () => document.getElementById('row').getAttribute('data-layout'),
  // THE PAN VIEWBOX, so "dragging a node must not pan the stage" is assertable.  It was
  // the one piece of stage state a harness could not see, and it was moving on every drag.
  vb: () => ({ x: VB.x, y: VB.y, w: VB.w, h: VB.h }),
  frame: () => frame,
  // the element menu as DOM, walked rather than regex'd out of a string
  palBody: () => document.getElementById('palBody'),
  palInsp: () => document.getElementById('palInsp'),
  cursor: () => (svg.style || {}).cursor || '',
  // the trap axis, which is the direction a site BAR runs in -- a reach probe that walked
  // the wrong axis would be measuring a chord of the capsule and reporting it as the body
  axis: () => AXIS
};
// Is the COMPILED programme still a programme for the device on screen?  Read off the
// page's own flag; a page emitted before the flag existed reports null rather than
// pretending everything is fine.
globalThis.__stale = () => (typeof PROGRAM_STALE === 'undefined' || !PROGRAM_STALE) ? null
  : { n: PROGRAM_STALE.n, kinds: PROGRAM_STALE.breaks.map(b => b.kind), why: PROGRAM_STALE.why };
globalThis.__banner = () => { const e = document.getElementById('invalid');
                              return e ? (e.textContent || '') : ''; };
// The nodes the COMPILED programme names at this frame that the DEVICE no longer has,
// derived from the page's own replay tables rather than from anything published for the
// test's benefit.  BOTH \`before[fi]\` and \`states[fi]\` are needed: an ion whose SOURCE
// node was deleted still has a stale DESTINATION that exists, so a check on end-position
// alone misses it at frame 1 and only catches it at frame 0.
globalThis.__ghostNodes = (fi) => {
  const out = {}, pos = (states[fi] || {pos:{}}).pos, was = before[fi] || {};
  for (const ion in pos) if (!nodeById[pos[ion]]) out[ion] = pos[ion];
  for (const ion in was) if (!nodeById[was[ion]]) out[ion] = was[ion];
  const ps = pathsOf(P.frames[fi] || {}, was);
  for (const ion in ps) for (const n of ps[ion]) if (!nodeById[n]) out[ion] = n;
  return out;
};
`;
