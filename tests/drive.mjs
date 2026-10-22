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
//   {"do":"drag","id":"S5","to":[x,y],free?,fine?,alt?} the whole gesture, in MODEL coords
//                                                     (`alt`: one part of a placed
//                                                     component rather than all of it)
//   {"do":"grab","kind":"site","id":"S5","to":[x,y],from?,free?,fine?,alt?}
//                                                     begin + move + the HUD, and NO drop:
//                                                     what the pointer is showing mid-drag
//   {"do":"drop"}                                     release the grabbed drag
//   {"do":"emit","op":{method,args,kwargs}}           one structured call
//   {"do":"source","src":"..."}                       the text lane
//   {"do":"addSegment","a":"S0","b":"S9"}
//   {"do":"addSite","x":..,"y":..,"near":"S0"}
//   {"do":"remove","sel":[{"kind":"node","id":"S5"}]} select-then-removeSelected
//   {"do":"undo"} / {"do":"redo"}                     one edit record
//   {"do":"undoGroup"} / {"do":"redoGroup"}           one whole gesture (what ctrl+Z does)
//   {"do":"hit","mx":..,"my":..}
//
// and the direct-manipulation verbs -- still the SAME vocabulary, extended.  Every one of
// them is an EDITOR gesture callable without an Event, which is why the pointer handlers
// are one-line adapters onto them:
//   {"do":"claim","mx":..,"my":..,"mod":{space?,button?}}   who owns this press
//   {"do":"hover","mx":..,"my":..}                          the hit + the cursor it sets
//   {"do":"panning","on":true}                              the page's pan began/ended
//   {"do":"lesson","id":"A1"} / lessonCheck / lessonHint / lessonSolution / lessonNext / lessons   the course
//   {"do":"leave"}                                          the pointer left the stage
//   {"do":"arm","type":"site"}                              arm a palette element
//   {"do":"stamp","type":"site","at":[x,y],"via":[[x,y]..]} place one, in MODEL coords
//   {"do":"dragKind","kind":"segment","id":"E0","to":[x,y],alt?} drag a segment or a loop
//   {"do":"toast","kind":"bad","message":"..."}             one toast, as the page shows it
//   {"do":"marquee","from":[x,y],"to":[x,y],"additive":..}  rubber-band select
//   {"do":"band","a":"S0","to":[x,y]}                       shift-drag from a node, HELD:
//                                                           the dashed segment-to-be
//   {"do":"bandDrop","to":[x,y]}                            release it (on a node = join)
//   {"do":"key","k":"ArrowRight","mods":{shift?,ctrl?},"field":"pwText"?}
//                                                           a key, through the page's ONE
//                                                           dispatcher (`pressKey`): the verb
//                                                           `EDITOR.keyGesture` gives it and
//                                                           what the page did with it
//   {"do":"closeLoop"}                                      close a loop over the selection
//   {"do":"menu","at":[x,y],"button":2}                     right-click a part: who claims
//                                                           the press, and the menu it opens
//                                                           (LATTICE coordinates, as `stamp`)
//   {"do":"menuInvoke","id":"delete"}                       press one item (a disabled one
//                                                           answers with its reason)
//   {"do":"menuState"}                                      the menu or its Modify panel
//   {"do":"menuClose"}
//   {"do":"modify","values":{"capacity":"3"}}               the Modify panel's Apply, with
//                                                           the text the fields would hold
//   {"do":"click","at":[x,y],"shift":..}                    a click on the stage (LATTICE
//                                                           coords): places the armed
//                                                           stamp, else selects
//   {"do":"dblclick","at":[x,y]}                            double-click: a site on empty stage
//   {"do":"mode2","mode":"sketch"|"parts"}                   the design mode (Sketch / Parts)
//   {"do":"sketchTool","tool":"rect"|"ellipse"|"line"|"poly"|null}   arm a shape tool
//   {"do":"sketch","tool":"rect","from":[x,y],"to":[x,y],"shift":..}
//                                                           the whole drag, in MODEL
//                                                           (lattice) coordinates: the
//                                                           plan the release would commit
//                                                           and what it committed
//   {"do":"sketch","tool":"poly","pts":[[x,y],..],"closed":..}       a polyline
//   {"do":"sketchPlan","tool":"rect","from":[x,y],"to":[x,y]}        the plan, uncommitted
//   {"do":"sketchReadout"}                                  what the live preview claims
//
// and the design-tool verbs, which are the same shape:
//   {"do":"canvas","opts":{name,template}}            a blank canvas
//   {"do":"generator","gen":"ring","params":{...}}    a device from a generator
//   {"do":"openTemplate","tpl":"grid9x9","opts":{}}   a shipped device, as itself
//   {"do":"node","x":..,"y":..,"opts":{...}}          place a site or a junction
//   {"do":"join","a":"N0","b":"N1","opts":{...}}      a segment
//   {"do":"loop","id":"L0","walk":[...],"closed":..}  a transport loop
//   {"do":"zone","zone":"data","fields":{...}}
//   {"do":"transaction","ops":[...]}                  N ops, atomically
//   {"do":"explode"}                                  to explicit geometry
//   {"do":"prog","src":"p.init({...}) ..."}             the programme, as text
//   {"do":"evaluate","src":"..."}                     what the Evaluate button does: the
//                                                     text through the same parse and
//                                                     lowering, judged (a refused statement
//                                                     is a refusal, not "ok") and toasted
//   {"do":"pemit","op":{method,args,kwargs}}          one programme statement
//   {"do":"testDrive","press":..}                     write + run a programme for this device
//                                                     (`press`: the button, verb + toast)
//   {"do":"testDrivePlan"}                            the route Test drive would take
//   {"do":"sealZones"}                                the zone types the seal declares
//   {"do":"startCard","gen":"grid"|"open":..|"tpl":..|"blank":true}
//                                                     a Start card: device, name, test drive
//   {"do":"setName","name":"..."}                     what typing into #palName does
//   {"do":"snapshot"} / {"do":"restore","snap":...}
//
// and the app frame -- the two panel folds and the layout regime, as attributes:
//   {"do":"fold","el":"rail"|"dock","on":true?}        what the head's handle (and [ ]) does
//   {"do":"resize","width":..,"height":..}             the window changed: the page's own
//                                                     resize path (`relayout`), which
//                                                     re-reads the regime and folds the
//                                                     rail on the way into `narrow` --
//                                                     and cancels a live drag
//   {"do":"dragging"}                                  is a drag or a band live?
//
// `grab`, `drop`, `key`, `fold` and `resize` records also carry `dragging`: whether a
// gesture is still live after the step (a fold key mid-drag leaves it live; a resize
// ends it).
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
      ED.begin('site', step.id, L.ox + n[1] * L.sx, L.oy + n[2] * L.sy, { alt: !!step.alt });
      const r = ED.move(L.ox + step.to[0] * L.sx, L.oy + step.to[1] * L.sy,
                        { free: !!step.free, fine: !!step.fine });
      rec.warnings = r ? r.warnings : null;
      // the live verdict: what the HUD turned red for before the release
      rec.problems = r ? (r.problems || []).map(p => p.code) : null;
      rec.ids = r ? r.ids : null;
      rec.contact = r ? (r.contact || null) : null;
      rec.landed = r ? [r.x, r.y] : null;
      rec.result = ED.drop();
      if (rec.result) rec.result = { problems: rec.result.problems.map(p => p.code), contact: rec.contact, landed: rec.landed };
    }
    // the drag HELD: begin at the node (or at `from`, in lattice coords), move, and show
    // the HUD where the pointer adapter would -- so what the page says mid-drag, and what
    // Escape does to it, are assertable
    else if (step.do === 'grab') {
      const L = PG.layout();
      let from = step.from;
      if (!from) { const n = PG.nodes().find(r => r[0] === step.id); from = n ? [n[1], n[2]] : [0, 0]; }
      const g = ED.begin(step.kind || 'site', step.id, L.ox + from[0] * L.sx, L.oy + from[1] * L.sy,
                         { alt: !!step.alt });
      rec.ids = g ? g.ids : null;
      if (g) {
        const mx = L.ox + step.to[0] * L.sx, my = L.oy + step.to[1] * L.sy;
        const r = ED.move(mx, my, { free: !!step.free, fine: !!step.fine });
        if (ED.showHud) ED.showHud(mx, my, r);
        rec.warnings = r ? r.warnings : null;
        rec.problems = r ? (r.problems || []).map(p => p.code) : null;
        rec.result = r ? { x: r.x, y: r.y, snapped: r.snapped, contact: r.contact || null } : null;
      } else rec.result = null;
    }
    else if (step.do === 'drop') {
      const r = ED.drop();
      rec.result = r ? { n: r.ops.length, problems: r.problems.map(p => p.code) } : null;
    }
    else if (step.do === 'toast') { const t = ED.toast(step.kind || '', step.message || ''); rec.result = !!t; }
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
    else if (step.do === 'undoGroup') { ED.undoGroup(); }
    else if (step.do === 'redoGroup') { ED.redoGroup(); }
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
    // what render.py's pan handler calls on pointerdown/up, so the cursor it leaves
    // behind is assertable without a pointer event
    else if (step.do === 'panning') { rec.result = ED.panning(!!step.on); rec.cursor = ED.cursor(); }
    // the pointer left the stage: what the `pointerleave` adapter calls
    else if (step.do === 'leave') { rec.result = ED.leaveStage(); rec.armed = ED.armed(); }
    else if (step.do === 'arm') { rec.result = ED.arm(step.type); rec.armed = ED.armed(); }
    // ---- the sketch --------------------------------------------------------------
    else if (step.do === 'mode2') { rec.result = ED.setDesignMode(step.mode); rec.mode = ED.designMode(); }
    else if (step.do === 'sketchTool') {
      ED.sketchTool(null);
      rec.result = step.tool ? ED.sketchTool(step.tool) : null;
      rec.tool = ED.sketchOn();
      rec.mode = ED.designMode();
    }
    else if (step.do === 'sketchPlan') {
      const pl = ED.sketchPlan(step.tool, step.pts ? { pts: step.pts, closed: !!step.closed }
                                                   : { x: step.from[0], y: step.from[1] },
                               step.to ? { x: step.to[0], y: step.to[1] } : null,
                               { shift: !!step.shift });
      rec.result = { ok: pl.ok, rule: pl.rule, why: pl.why, closed: pl.closed,
                     n_sites: pl.n_sites, n_junctions: pl.n_junctions,
                     n_shared: pl.n_shared, bends: pl.bends,
                     w_um: pl.w_um, h_um: pl.h_um };
    }
    else if (step.do === 'sketch') {
      const r = (step.tool === 'poly')
        ? ED.sketchDraw('poly', step.pts || [], null, { closed: !!step.closed })
        : ED.sketchDraw(step.tool, step.from, step.to, { shift: !!step.shift });
      rec.result = { ok: !!r.ok, instance: r.instance || null, loop: r.loop || null,
                     nodes: r.nodes || null,
                     problems: (r.problems || []).map(p => p.code || p.message) };
      rec.tool = ED.sketchOn();
    }
    else if (step.do === 'sketchReadout') { rec.result = ED.sketchReadout(); }
    else if (step.do === 'stamp') {
      const L = PG.layout();
      ED.arm(step.type);
      ED.ghostBegin(step.type, L.ox + step.at[0] * L.sx, L.oy + step.at[1] * L.sy);
      for (const q of (step.via || [])) ED.ghostMove(L.ox + q[0] * L.sx, L.oy + q[1] * L.sy);
      const r = ED.ghostDrop();
      rec.result = { ok: r.ok, id: r.id,
                     problems: (r.problems || []).map(p => p.code || p.message) };
    }
    else if (step.do === 'dragKind') {
      const L = PG.layout();
      const g = ED.begin(step.kind, step.id, L.ox + step.from[0] * L.sx,
                         L.oy + step.from[1] * L.sy, { alt: !!step.alt });
      rec.ids = g ? g.ids : null;
      if (!g) { rec.result = null; }
      else {
        const m = ED.move(L.ox + step.to[0] * L.sx, L.oy + step.to[1] * L.sy,
                          { free: !!step.free, fine: !!step.fine });
        rec.warnings = m ? m.warnings : null;
        rec.problems = m ? (m.problems || []).map(p => p.code) : null;
        const r = ED.drop();
        rec.result = r ? { n: r.ops.length, problems: r.problems.map(p => p.code) } : null;
      }
    }
    else if (step.do === 'band') {
      const L = PG.layout();
      const b = ED.bandBegin(step.a);
      rec.result = b ? ED.bandMove(L.ox + step.to[0] * L.sx, L.oy + step.to[1] * L.sy) : null;
    }
    else if (step.do === 'bandDrop') {
      const L = PG.layout();
      const r = ED.bandDrop(L.ox + step.to[0] * L.sx, L.oy + step.to[1] * L.sy);
      rec.result = r ? { from: r.from, to: r.to, ok: r.ok,
                         problems: (r.problems || []).map(p => p.code || p.message) } : null;
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
      // THE PAGE'S OWN DISPATCHER, not a synthesized KeyboardEvent: the shim has no Event
      // constructor, and a key that only worked through one would be a key with no test.
      // `pressKey` is what the page's single `keydown` listener calls, so every binding
      // in `EDITOR.keyGesture`'s table is reachable here with the same code path.  A page
      // emitted before the dispatcher existed still answers Escape through the editor.
      const mods = step.mods || {};
      if (PG.press) {
        const r = PG.press(step.k, mods, { field: step.field || null });
        rec.verb = r ? r.verb : null;
        rec.result = r ? r.result : null;
        // a nudge's verdict is an op list; keep it diffable
        if (rec.verb === 'nudge' && rec.result && rec.result.ops) {
          rec.result = { ok: rec.result.ok, n: rec.result.ops.length,
                         problems: (rec.result.problems || []).map(p => p.code || p.message) };
        }
        if (rec.verb === 'remove' && rec.result && rec.result.problems) {
          rec.result = { ok: rec.result.ok, problems: rec.result.problems.map(p => p.code || p.message) };
        }
        if (rec.verb === null) rec.error = 'no binding for key ' + step.k;
      } else if (step.k === 'Escape') {
        if (ED.cancelGesture) rec.result = ED.cancelGesture();
      } else rec.error = 'no headless binding for key ' + step.k;
    }
    else if (step.do === 'closeLoop') {
      const r = ED.closeLoopFromSelection(step.opts || {});
      rec.result = { ok: r.ok, problems: (r.problems || []).map(p => p.code || p.message) };
    }
    // the pointer adapters' click and double-click, without an Event: what `end` and the
    // `dblclick` listener call, in LATTICE coordinates
    else if (step.do === 'click' || step.do === 'dblclick') {
      const L = PG.layout();
      const mx = L.ox + step.at[0] * L.sx, my = L.oy + step.at[1] * L.sy;
      const r = step.do === 'click' ? ED.clickStage(mx, my, { shift: !!step.shift })
                                    : ED.dblclickStage(mx, my);
      rec.result = r === null ? null
        : { ok: r.ok, id: r.id, placed: r.placed === undefined ? null : r.placed,
            selected: r.selected ? r.selected.map(x => x.kind + ':' + x.id) : undefined,
            problems: (r.problems || []).map(p => p.code || p.message) };
      rec.armed = ED.armed();
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
    else if (step.do === 'openTemplate') {
      const r = ED.newFromTemplate(step.tpl, step.opts || {});
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
    else if (step.do === 'evaluate') {
      const r = ED.evaluateWrite(step.src);
      rec.result = { ok: r.ok, parsed: r.parsed, ran: r.ran, statements: r.statements,
                     frames: r.frames,
                     problems: (r.problems || []).map(p => ED.problemLine ? ED.problemLine(p) : p.message) };
    }
    else if (step.do === 'pemit') {
      const r = ED.emitProgram(step.op);
      rec.result = { ok: r.ok, problems: (r.problems || []).map(p => p.message) };
    }
    // the one-click programme: `{ok, kind, statements, frames}` is correct synchronously
    // and the autoplay it would start is skipped under SYNC, so a script can assert on
    // P.frames right after this step
    else if (step.do === 'testDrive') {
      const r = step.press ? ED.pressTestDrive() : ED.testDrive();
      rec.result = { ok: r.ok, kind: r.kind || null,
                     statements: (r.statements || []).map(s => s.method),
                     frames: r.frames === undefined ? null : r.frames,
                     problems: (r.problems || []).map(p => p.code || p.message) };
    }
    else if (step.do === 'testDrivePlan') {
      const r = ED.testDrivePlan();
      rec.result = { ok: r.ok, kind: r.kind || null, from: r.from === undefined ? null : r.from,
                     to: r.to === undefined ? null : r.to, loop: r.loop === undefined ? null : r.loop,
                     statements: (r.statements || []).map(s => s.method),
                     problems: (r.problems || []).map(p => p.code || p.message) };
    }
    else if (step.do === 'sealZones') { rec.result = Object.keys(ED.sealZones()).sort(); }
    else if (step.do === 'setName') { rec.result = ED.setName(step.name); }
    // the Start card's whole gesture -- device, its name, the automatic test drive --
    // exactly what the card's click listener calls
    else if (step.do === 'startCard') {
      const r = ED.pressStartCard({ gen: step.gen, open: step.open, tpl: step.tpl });
      rec.result = { ok: r.ok, name: r.name === undefined ? null : r.name,
                     tpl: r.tpl === undefined ? null : r.tpl,
                     problems: (r.r && r.r.problems || []).map(p => p.code || p.message),
                     drive: r.drive ? { ok: r.drive.ok, kind: r.drive.kind || null,
                                        frames: r.drive.frames === undefined ? null : r.drive.frames }
                                    : null };
      rec.name = ED.state() ? ED.state().name : null;
      rec.history = ED.canvasHistory();
    }
    else if (step.do === 'fold') { rec.result = PG.fold(step.el, step.on); rec.folds = PG.folds(); }
    else if (step.do === 'resize') {
      if (step.width) globalThis.window.innerWidth = step.width;
      if (step.height) globalThis.window.innerHeight = step.height;
      rec.result = PG.relayout ? PG.relayout() : null;
      rec.folds = PG.folds(); rec.data_layout = PG.dataLayout();
    }
    else if (step.do === 'dragging') { rec.result = ED.dragging ? ED.dragging() : null; }
    // the course: load a lesson, check it, take a hint, apply its solution, move on
    else if (step.do === 'lesson') { rec.result = ED.lessonLoad(step.id); rec.lesson = ED.lessonState(); }
    else if (step.do === 'lessonCheck') { rec.result = ED.lessonCheck(); rec.lesson = ED.lessonState(); }
    else if (step.do === 'lessonHint') { rec.result = ED.lessonHint(); rec.lesson = ED.lessonState(); }
    else if (step.do === 'lessonSolution') { rec.result = ED.lessonSolution(step.one ? { one: true } : undefined); rec.lesson = ED.lessonState(); }
    else if (step.do === 'lessonNext') { rec.result = ED.lessonNext(); rec.lesson = ED.lessonState(); }
    else if (step.do === 'lessonAnswer') { rec.result = ED.lessonAnswer(step.choice); rec.lesson = ED.lessonState(); }
    else if (step.do === 'lessons') { rec.result = ED.lessonList(); }
    else if (step.do === 'check') { rec.result = ED.evalCheck(step.spec); }
    else if (step.do === 'snap') { rec.result = ED.setSnap ? ED.setSnap(step.on !== false) : null; }
    else if (step.do === 'progView') { rec.result = PG.setProgView ? PG.setProgView(step.view) : null; rec.prog = PG.progState ? PG.progState() : null; }
    else if (step.do === 'progPin') { rec.result = PG.progPin ? PG.progPin(step.on) : null; rec.prog = PG.progState ? PG.progState() : null; }
    else if (step.do === 'pane') { rec.result = PG.setPane ? PG.setPane(step.pane) : null; rec.prog = PG.progState ? PG.progState() : null; }
    else if (step.do === 'tools') { rec.result = window.TOOLSBAR ? window.TOOLSBAR.open(step.pop) : null; rec.tools = PG.tools ? PG.tools() : null; }
    else if (step.do === 'search') { rec.result = window.TOOLSBAR ? window.TOOLSBAR.search(step.q) : null; }
    else if (step.do === 'reveal') { rec.result = window.TOOLSBAR ? window.TOOLSBAR.reveal(step.i || 0) : null; rec.tools = PG.tools ? PG.tools() : null; }
    // TWO THINGS ARE CALLED A MENU on this page and both are driven from here: the
    // head's PANEL menu (`pane`), which had the verb first, and the stage's ELEMENT menu
    // (`at`), which a right-click opens.  Told apart by which key the step carries, so
    // neither has to be renamed and no script already written changes meaning.
    else if (step.do === 'menu' && step.at) {
      const L = PG.layout();
      const mx = L.ox + step.at[0] * L.sx, my = L.oy + step.at[1] * L.sy;
      rec.claim = ED.claim(mx, my, { button: step.button === undefined ? 2 : step.button });
      rec.result = ED.menuOpen(mx, my);
      rec.menu = ED.menuState();
    }
    else if (step.do === 'menuInvoke') {
      const r = ED.menuInvoke(step.id);
      rec.result = r ? { ok: !!r.ok, id: r.id === undefined ? null : r.id,
                         problems: (r.problems || []).map(p => p.code || p.message) } : null;
      rec.menu = ED.menuState();
    }
    else if (step.do === 'menuState') { rec.result = ED.menuState(); rec.menu = rec.result; }
    else if (step.do === 'menuClose') { rec.result = ED.menuClose(); rec.menu = ED.menuState(); }
    else if (step.do === 'modify') {
      const r = ED.modifyApply(step.values || {});
      rec.result = { ok: !!r.ok, problems: (r.problems || []).map(p => p.code || p.message) };
      rec.menu = ED.menuState();
    }
    else if (step.do === 'menu') { rec.result = PG.menuPick ? PG.menuPick(step.pane) : null; rec.prog = PG.progState ? PG.progState() : null; rec.menu = PG.menu(); rec.folds = PG.folds(); }
    // what the page shipped for the course: the cost model, the cases, Python's verdicts
    else if (step.do === 'blob') { rec.result = ED.courseBlob(); }
    else if (step.do === 'snapshot') { rec.snap = ED.snapshot(); }
    else if (step.do === 'restore') {
      const r = ED.restore(step.snap);
      rec.result = { ok: r.ok, problems: (r.problems || []).map(p => p.message) };
    }
    else rec.error = 'unknown step ' + step.do;
  } catch (err) {
    rec.error = err.name + ': ' + err.message;
  }
  if (['grab', 'drop', 'key', 'fold', 'resize'].indexOf(step.do) >= 0)
    rec.dragging = ED.dragging ? ED.dragging() : null;
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
  // THE APP FRAME, as the attributes that drive it: the two folds (and the wide regime's
  // copy of the dock's on #row), the page's own resize path, the Circuit pane's off
  // switch and the one toolbar.  Absent on a page emitted before the frame existed.
  folds: () => ({ rail: document.getElementById('rail').getAttribute('data-collapsed') === '1',
                  dock: document.getElementById('dock').getAttribute('data-collapsed') === '1',
                  row_dock: document.getElementById('row').getAttribute('data-dock') }),
  fold: (el, on) => { foldPanel(document.getElementById(el === 'dock' ? 'dock' : 'rail'), on);
                      return document.getElementById(el === 'dock' ? 'dock' : 'rail').getAttribute('data-collapsed'); },
  relayout: (typeof relayout === 'function') ? (() => relayout()) : null,
  paneOff: (k) => { const p = document.getElementById('pane' + (k || 'Q'));
                    return p ? p.getAttribute('data-off') === '1' : null; },
  // THE PROGRAMME COLUMN: where the Program pane lives, what it shows, and the pin
  progState: (typeof placeProgram === 'function') ? (() => ({
    col: PROG_COL, view: PROG_VIEW, pin: PROG_PIN, pane: PANE,
    progcol: document.getElementById('progcol').getAttribute('data-collapsed'),
    dock_prog: document.getElementById('dock').getAttribute('data-prog') })) : null,
  setProgView: (typeof setProgView === 'function') ? ((v) => setProgView(v)) : null,
  progPin: (typeof progPinToggle === 'function') ? ((on) => progPinToggle(on)) : null,
  setPane: (typeof setPane === 'function') ? ((k) => { setPane(k); return PANE; }) : null,
  menuPick: (typeof menuPick === 'function') ? ((k) => menuPick(k)) : null,
  // FOUR POPOVERS, not five: "Selection" is gone -- its panel was the same palInspect
  // node the rail already carries, and it popped itself open on every click.
  // (no backticks anywhere in this hook: it is itself a template literal)
  tools: (typeof openPop === 'function') ? (() => { const o = {}; for (const k of ['start','row','block','component']) { const el = popEl(k); o[k] = el ? (((el.className || '') + '').indexOf('pop-open') >= 0) : null; } o.current = POP; return o; }) : null,
  menu: () => { const o = {}; for (const k of ['L','P','Q','A','M','W','R']) { const t = document.getElementById('tab' + k); o[k] = t ? t.className : null; } return o; },
  // THE PAN VIEWBOX, so "dragging a node must not pan the stage" is assertable.  It was
  // the one piece of stage state a harness could not see, and it was moving on every drag.
  vb: () => ({ x: VB.x, y: VB.y, w: VB.w, h: VB.h }),
  frame: () => frame,
  // THE KEY DISPATCHER the page's one keydown listener calls -- absent on a page emitted
  // before it existed, so a driver falls back rather than throws
  press: (typeof pressKey === 'function') ? ((k, mods, ctx) => pressKey(k, mods, ctx)) : null,
  // the help overlay, as the attributes that drive it
  helpOn: () => (globalThis.EDITOR && globalThis.EDITOR.helpOn) ? globalThis.EDITOR.helpOn() : null,
  helpBody: () => document.getElementById('helpBody').innerHTML || '',
  // the element menu as DOM, walked rather than regex'd out of a string
  palBody: () => document.getElementById('palBody'),
  palInsp: () => document.getElementById('palInsp'),
  // the element menu's host, as the attributes that drive it
  ctxmenu: () => { const e = document.getElementById('ctxmenu');
                   return e ? { open: e.getAttribute('data-open'), html: e.innerHTML || '' } : null; },
  // THE TWO REGISTERS ON THE STRIP: what is wrong with what was drawn, and what is merely
  // not there yet.  An empty canvas must read "0 problems".
  chips: () => ({ problems: document.getElementById('eProb').textContent || '',
                  notes: document.getElementById('eNotes').textContent || '',
                  notes_shown: (document.getElementById('eNotes').style || {}).display !== 'none' }),
  // THE EMPTY STATE and the start fold, as the attributes that drive them: style.display
  // on #stageEmpty and the open attribute on #palStart.  Both used to be invisible here.
  // The fold no longer opens itself on an empty stage (the stage carries the start), so
  // startOpen reads only what the user toggled: false on every fresh page.
  stageEmpty: () => { const e = document.getElementById('stageEmpty');
                      return e ? ((e.style || {}).display !== 'none') : null; },
  startOpen: () => { const d = document.getElementById('palStart');
                     return d ? (d.getAttribute('open') !== null && d.getAttribute('open') !== undefined) : null; },
  cursor: () => (svg.style || {}).cursor || '',
  // THE LIVE FEEDBACK, as the page holds it: the drag HUD's class and text (the class is
  // what the stylesheet keys on -- 'hud bad' is the red one -- and the text is what it
  // says), and the toasts still showing, oldest first
  hud: () => { const h = document.getElementById('hud');
               return h ? { cls: h.className || '', text: h.textContent || '' } : null; },
  toasts: () => (globalThis.EDITOR && globalThis.EDITOR.toasts) ? globalThis.EDITOR.toasts() : null,
  // THE PROGRAMME PANE'S OWN WORDS: the row count over the filter, the NOW strip, the
  // Write pane's error strip, the head's two counters and its metric chips, the lede and
  // the window title -- every one of them used to describe the SHIPPED programme while
  // an authored one was on the stage
  pCount: () => document.getElementById('pCount').textContent || '',
  pNow: () => document.getElementById('pNow').innerHTML || '',
  // the Circuit lane's NOW strip: joined to the shipped walk only, never to an authored frame
  qNow: () => document.getElementById('qNow').innerHTML || '',
  pwErr: () => (document.getElementById('pwErr').innerHTML || '')
    .replace(/<\\/div>/g, '\\n').replace(/<[^>]*>/g, '').trim(),
  counters: () => ({ steps: document.getElementById('cSteps').textContent || '',
                     stepsOf: document.getElementById('cStepsOf').textContent || '',
                     cost: document.getElementById('cCost').textContent || '',
                     costOf: document.getElementById('cCostOf').textContent || '' }),
  // off innerHTML, which the shim holds as a string: one 'label=value' per chip
  metrics: () => (document.getElementById('metrics').innerHTML || '')
    .replace(/<\\/span>/g, '=').replace(/<\\/div>/g, '\\n').replace(/<[^>]*>/g, '')
    .split('\\n').map(s => s.trim()).filter(Boolean),
  lede: () => document.getElementById('lede').textContent || '',
  docTitle: () => String(document.title || ''),
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
