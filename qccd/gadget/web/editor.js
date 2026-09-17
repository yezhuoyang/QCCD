// qccd.gadget page: the composition editor.  GADGETS.md section 10, "Edit".
//
// It edits a COPY of the library -- the running schedule belongs to the design it was
// synthesized from, so nothing here changes what the stage replays.  What it does:
//   place     an instance of any master (the palette on the left)
//   move      drag an instance; every channel on it re-routes and re-measures
//   connect   click a port, then another: the channel is made only if G1 accepts the pair,
//             otherwise the reason is shown and nothing is made
//   group     a selection becomes a new composite master; every channel that crossed the
//             selection's boundary now ends on a port the new master exports
//   ungroup   the reverse, one level
//   export    the whole library as qccd.gadget/0.1 JSON, which
//             `python -m qccd.gadget build PROGRAM --design FILE` synthesizes
// Undo and redo keep whole snapshots of the edited masters; a library of dozens of masters
// is a few kilobytes, so there is nothing to be clever about.

(function () {
  "use strict";
  let G = null;                 // the page's handle (window.GADGETS), given by attach()
  const E = {
    active: false, master: null, lib: null, sel: new Set(), chan: null, port: null,
    drag: null, undo: [], redo: [], message: "", hover: null, placing: null,
  };

  const clone = (x) => JSON.parse(JSON.stringify(x));
  const $ = (id) => document.getElementById(id);

  // ------------------------------------------------------------------ the library copy

  function masterOf(name) { return E.lib.masters[name]; }

  function portPoint(mname, port) {
    const m = masterOf(mname);
    const p = (m.ports || []).find((q) => q.name === port);
    if (!p) return null;
    if (m.kind === "leaf") return G.portXY(mname, port);
    if (!p.bind) return null;
    const [iname, cport] = p.bind.split(".");
    const inst = m.instances.find((i) => i.name === iname);
    const c = inst && portPoint(inst.master, cport);
    return c ? [c[0] + inst.x, c[1] + inst.y] : null;
  }

  function refPoint(m, ref) {
    const [iname, port] = ref.split(".");
    const inst = m.instances.find((i) => i.name === iname);
    if (!inst) return null;
    const p = portPoint(inst.master, port);
    return p ? [p[0] + inst.x, p[1] + inst.y] : null;
  }

  function refPort(m, ref) {
    const [iname, port] = ref.split(".");
    const inst = m.instances.find((i) => i.name === iname);
    if (!inst) return null;
    return (masterOf(inst.master).ports || []).find((p) => p.name === port) || null;
  }

  // G1, exactly as qccd.gadget.model.Port.accepts says it
  function accepts(a, b) {
    const why = [];
    if (a.role !== "any" && b.role !== "any" && a.role !== b.role) why.push(`roles differ: ${a.role} vs ${b.role}`);
    if (a.role === "data" && b.role === "data") {
      if (a.width !== b.width) why.push(`bundle widths differ: ${a.width} vs ${b.width}`);
      if (a.code && b.code && a.code !== b.code) why.push(`codes differ: ${a.code} vs ${b.code}`);
    }
    if (a.dir === b.dir && a.dir !== "inout") why.push(`both ports are '${a.dir}'`);
    return why;
  }

  // the same Manhattan rule as qccd.gadget.designs.Designer.composite
  function route(m, ch) {
    const pa = refPoint(m, ch.a), pb = refPoint(m, ch.b);
    if (!pa || !pb) return;
    // a channel whose ends have not moved keeps the route it was given (via points and
    // all); one whose end moved is re-routed as an L
    const old = ch.points || [];
    const same = (p, q) => p && q && Math.abs(p[0] - q[0]) < 1e-9 && Math.abs(p[1] - q[1]) < 1e-9;
    const keep = old.length >= 2 && same(old[0], pa) && same(old[old.length - 1], pb);
    const pts = keep ? old.map((p) => p.slice()) : [pa];
    if (!keep) {
      if (pa[0] !== pb[0] && pa[1] !== pb[1]) pts.push([pb[0], pa[1]]);
      pts.push(pb);
    }
    let len = 0;
    for (let i = 1; i < pts.length; i++) len += Math.abs(pts[i][0] - pts[i - 1][0]) + Math.abs(pts[i][1] - pts[i - 1][1]);
    ch.points = pts;
    ch.length = Math.max(1, Math.round(len) - 1);
  }

  function refresh(m) {
    for (const ch of m.channels) route(m, ch);
    const box = [Infinity, Infinity, -Infinity, -Infinity];
    const grow = (x, y) => { box[0] = Math.min(box[0], x); box[1] = Math.min(box[1], y); box[2] = Math.max(box[2], x); box[3] = Math.max(box[3], y); };
    const inv = {};
    let cap = 0;
    for (const inst of m.instances) {
      const c = masterOf(inst.master);
      const b = c.bbox && c.bbox.length ? c.bbox : [0, 0, 4, 4];
      grow(inst.x + b[0], inst.y + b[1]); grow(inst.x + b[2], inst.y + b[3]);
      for (const k in c.inventory || {}) inv[k] = (inv[k] || 0) + c.inventory[k];
      cap += c.capacity || 0;
    }
    for (const ch of m.channels) for (const p of ch.points || []) grow(p[0], p[1]);
    if (!isFinite(box[0])) box.splice(0, 4, 0, 0, 10, 10);
    m.bbox = box;
    m.size = [box[2] - box[0], box[3] - box[1]];
    m.inventory = inv;
    m.capacity = cap;
  }

  function problems(m) {
    const out = [];
    const used = {};
    for (const ch of m.channels) {
      const a = refPort(m, ch.a), b = refPort(m, ch.b);
      if (!a || !b) { out.push(`${ch.name}: ${!a ? ch.a : ch.b} is not a port here`); continue; }
      for (const w of accepts(a, b)) out.push(`${ch.name} (${ch.a} → ${ch.b}): ${w}`);
      for (const end of [ch.a, ch.b]) {
        const p = refPort(m, end);
        if (used[end] && p && p.role !== "any") out.push(`port ${end} is on two channels (${used[end]}, ${ch.name})`);
        used[end] = ch.name;
      }
    }
    for (const p of m.ports || []) {
      if (!p.bind || !refPort(m, p.bind)) out.push(`port ${p.name} binds to no child port`);
    }
    const names = m.instances.map((i) => i.name);
    for (const n of new Set(names)) if (names.filter((x) => x === n).length > 1) out.push(`instance name ${n} is used twice`);
    // a master may not contain itself, at any depth
    const seen = new Set();
    (function walk(name) {
      const mm = masterOf(name);
      if (!mm || mm.kind !== "composite") return;
      for (const i of mm.instances) {
        if (i.master === m.name) out.push(`${m.name} contains itself through ${name}`);
        if (!seen.has(i.master)) { seen.add(i.master); walk(i.master); }
      }
    })(m.name);
    return out;
  }

  // ------------------------------------------------------------------ history

  function snapshot() { return clone(E.lib.masters); }
  function commit(message) {
    E.undo.push({ masters: snapshot(), master: E.master, message });
    if (E.undo.length > 200) E.undo.shift();
    E.redo.length = 0;
  }
  function undo() {
    const s = E.undo.pop();
    if (!s) return;
    E.redo.push({ masters: snapshot(), master: E.master });
    E.lib.masters = s.masters;
    E.master = s.master;
    E.sel.clear(); E.chan = null; E.port = null;
    say(`undid: ${s.message}`);
    update();
  }
  function redoIt() {
    const s = E.redo.pop();
    if (!s) return;
    E.undo.push({ masters: snapshot(), master: E.master, message: "redo" });
    E.lib.masters = s.masters;
    E.master = s.master;
    update();
  }

  // ------------------------------------------------------------------ edits

  function unique(m, base) {
    const names = new Set(m.instances.map((i) => i.name));
    if (!names.has(base)) return base;
    for (let k = 1; ; k++) if (!names.has(base + k)) return base + k;
  }
  function uniqueMaster(base) {
    if (!E.lib.masters[base]) return base;
    for (let k = 2; ; k++) if (!E.lib.masters[base + "_" + k]) return base + "_" + k;
  }
  function uniqueChannel(m, base) {
    const names = new Set(m.channels.map((c) => c.name));
    if (!names.has(base)) return base;
    for (let k = 1; ; k++) if (!names.has(base + k)) return base + k;
  }

  function place(masterName, x, y) {
    const m = masterOf(E.master);
    if (masterName === m.name) { say("a gadget cannot contain itself"); return; }
    commit(`place ${masterName}`);
    const short = masterName.replace(/_.*$/, "").replace(/\d+$/, "") || "i";
    const inst = { name: unique(m, short), master: masterName, x: Math.round(x), y: Math.round(y), rot: 0 };
    const c = masterOf(masterName);
    if (c.bbox && c.bbox.length) { inst.x = Math.round(x - (c.bbox[0] + c.bbox[2]) / 2); inst.y = Math.round(y - (c.bbox[1] + c.bbox[3]) / 2); }
    m.instances.push(inst);
    E.sel = new Set([inst.name]);
    refresh(m);
    say(`placed ${inst.name} (${masterName})`);
    update();
  }

  function connect(a, b) {
    const m = masterOf(E.master);
    if (a === b) return;
    const pa = refPort(m, a), pb = refPort(m, b);
    const why = accepts(pa, pb);
    if (why.length) { say(`not connected — ${why.join("; ")}`, true); return; }
    for (const end of [a, b]) {
      const p = refPort(m, end);
      if (p.role !== "any" && m.channels.some((c) => c.a === end || c.b === end)) {
        say(`not connected — ${end} already has a channel`, true);
        return;
      }
    }
    commit(`connect ${a} → ${b}`);
    const ch = { name: uniqueChannel(m, "c"), a, b, length: 1, junctions: 0, points: [], role: "any" };
    m.channels.push(ch);
    refresh(m);
    E.chan = ch.name;
    say(`channel ${ch.name}: ${a} → ${b}, ${ch.length} sites`);
    update();
  }

  function removeSelection() {
    const m = masterOf(E.master);
    if (E.chan) {
      commit(`delete channel ${E.chan}`);
      m.channels = m.channels.filter((c) => c.name !== E.chan);
      E.chan = null;
    } else if (E.sel.size) {
      commit(`delete ${[...E.sel].join(", ")}`);
      m.instances = m.instances.filter((i) => !E.sel.has(i.name));
      m.channels = m.channels.filter((c) => !E.sel.has(c.a.split(".")[0]) && !E.sel.has(c.b.split(".")[0]));
      m.ports = (m.ports || []).filter((p) => !p.bind || !E.sel.has(p.bind.split(".")[0]));
      E.sel.clear();
    } else return;
    refresh(m);
    update();
  }

  function exportPort(ref, given) {
    const m = masterOf(E.master);
    const p = refPort(m, ref);
    if (!p) return;
    if ((m.ports || []).some((q) => q.bind === ref)) { say(`${ref} is already exported`); return; }
    const name = (given || window.prompt(`Export ${ref} as a port of ${m.name} named:`, ref.replace(".", "_")) || "").trim();
    if (!name) return;
    if ((m.ports || []).some((q) => q.name === name)) { say(`${m.name} already has a port ${name}`, true); return; }
    commit(`export ${ref} as ${name}`);
    m.ports = m.ports || [];
    m.ports.push({ ...clone(p), name, bind: ref, node: null, stub: null });
    say(`${m.name}.${name} exports ${ref}`);
    update();
  }

  function group(given) {
    const m = masterOf(E.master);
    const chosen = m.instances.filter((i) => E.sel.has(i.name));
    if (!chosen.length) { say("select instances to group (shift-click adds)"); return; }
    const name = (typeof given === "string" && given ? given :
      window.prompt(`Name the new composite gadget made of ${chosen.map((i) => i.name).join(", ")}:`, uniqueMaster(`${E.master}_group`)) || "").trim();
    if (!name) return;
    if (E.lib.masters[name]) { say(`a master named ${name} exists`, true); return; }
    commit(`group ${chosen.map((i) => i.name).join(", ")} into ${name}`);
    const inside = new Set(chosen.map((i) => i.name));
    const ox = Math.floor(Math.min(...chosen.map((i) => i.x + (masterOf(i.master).bbox || [0])[0])));
    const oy = Math.floor(Math.min(...chosen.map((i) => i.y + (masterOf(i.master).bbox || [0, 0])[1])));
    const nm = { name, kind: "composite", family: "custom", title: name, doc: `grouped from ${m.name}`,
                 size: [0, 0], bbox: [], ports: [], inventory: {}, capacity: 0, ops: {}, params: {},
                 device: null, instances: chosen.map((i) => ({ ...i, x: i.x - ox, y: i.y - oy })), channels: [] };
    const instName = unique({ instances: m.instances.filter((i) => !inside.has(i.name)) }, name.replace(/_group.*$/, "g"));
    const keep = [];
    for (const ch of m.channels) {
      const ia = inside.has(ch.a.split(".")[0]), ib = inside.has(ch.b.split(".")[0]);
      if (ia && ib) nm.channels.push({ ...ch, points: [] });
      else if (ia || ib) {
        const innerRef = ia ? ch.a : ch.b;
        const p = refPort(m, innerRef);
        let pname = innerRef.replace(".", "_");
        while (nm.ports.some((q) => q.name === pname)) pname += "_";
        nm.ports.push({ ...clone(p), name: pname, bind: innerRef, node: null, stub: null });
        keep.push({ ...ch, a: ia ? `${instName}.${pname}` : ch.a, b: ib ? `${instName}.${pname}` : ch.b });
      } else keep.push(ch);
    }
    // the ports of the grouped master the old master exported
    for (const p of m.ports || []) {
      if (p.bind && inside.has(p.bind.split(".")[0])) {
        let pname = p.bind.replace(".", "_");
        while (nm.ports.some((q) => q.name === pname)) pname += "_";
        nm.ports.push({ ...clone(refPort(m, p.bind)), name: pname, bind: p.bind, node: null, stub: null });
        p.bind = `${instName}.${pname}`;
      }
    }
    E.lib.masters[name] = nm;
    refresh(nm);
    m.instances = m.instances.filter((i) => !inside.has(i.name));
    m.instances.push({ name: instName, master: name, x: ox, y: oy, rot: 0 });
    m.channels = keep;
    refresh(m);
    E.sel = new Set([instName]);
    say(`grouped ${chosen.length} instances into ${name} (${nm.ports.length} ports exported)`);
    update();
  }

  function ungroup() {
    const m = masterOf(E.master);
    const target = m.instances.find((i) => E.sel.has(i.name) && masterOf(i.master).kind === "composite");
    if (!target) { say("select one composite instance to ungroup"); return; }
    commit(`ungroup ${target.name}`);
    const c = masterOf(target.master);
    const rename = {};
    for (const child of c.instances) {
      const nn = unique(m, `${target.name}_${child.name}`);
      rename[child.name] = nn;
      m.instances.push({ ...child, name: nn, x: child.x + target.x, y: child.y + target.y });
    }
    const re = (ref) => { const [i, p] = ref.split("."); return `${rename[i]}.${p}`; };
    for (const ch of c.channels) m.channels.push({ ...ch, name: uniqueChannel(m, `${target.name}_${ch.name}`), a: re(ch.a), b: re(ch.b), points: [] });
    const bound = {};
    for (const p of c.ports || []) bound[p.name] = re(p.bind);
    for (const ch of m.channels) {
      for (const end of ["a", "b"]) {
        const [i, p] = ch[end].split(".");
        if (i === target.name) ch[end] = bound[p];
      }
    }
    for (const p of m.ports || []) {
      if (p.bind && p.bind.split(".")[0] === target.name) p.bind = bound[p.bind.split(".")[1]];
    }
    m.instances = m.instances.filter((i) => i.name !== target.name);
    refresh(m);
    E.sel = new Set(Object.values(rename));
    say(`ungrouped ${target.name}`);
    update();
  }

  function exportDesign() {
    const doc = { schema: "qccd.gadget/0.1", top: E.master, masters: E.lib.masters };
    for (const m of Object.values(doc.masters)) if (m.kind === "composite") refresh(m);
    const blob = new Blob([JSON.stringify(doc, null, 1)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${E.master}.gadget.json`;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    say(`exported ${E.master}.gadget.json — python -m qccd.gadget build PROGRAM.lq --design ${E.master}.gadget.json`);
  }

  // ------------------------------------------------------------------ drawing

  function draw() {
    const [w, h] = G.sizeCanvas(G.stage, G.sctx);
    const ctx = G.sctx;
    const { X, Y, COLOR, FAMILY, ROLE } = G;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#fbfcfd";
    ctx.fillRect(0, 0, w, h);
    const m = masterOf(E.master);
    const s = G.S.cam.s;
    // grid
    if (s > 6) {
      ctx.fillStyle = "#dfe3ea";
      const x0 = Math.floor(-G.S.cam.x / s), x1 = Math.ceil((w - G.S.cam.x) / s);
      const y0 = Math.floor(-G.S.cam.y / s), y1 = Math.ceil((h - G.S.cam.y) / s);
      const step = s > 20 ? 1 : s > 10 ? 2 : 5;
      for (let gx = Math.floor(x0 / step) * step; gx <= x1; gx += step)
        for (let gy = Math.floor(y0 / step) * step; gy <= y1; gy += step) ctx.fillRect(X(gx) - 0.5, Y(gy) - 0.5, 1, 1);
    }
    E.hits = [];
    const bad = new Set();
    for (const p of problems(m)) { const n = p.split(" ")[0].replace(":", ""); bad.add(n); }
    // channels
    for (const ch of m.channels) {
      if (!ch.points || ch.points.length < 2) route(m, ch);
      if (!ch.points || ch.points.length < 2) continue;
      const selected = E.chan === ch.name;
      ctx.strokeStyle = bad.has(ch.name) ? "#b42318" : selected ? COLOR.accent : COLOR.rail;
      ctx.lineWidth = selected ? 4 : Math.max(2, Math.min(5, s * 0.35));
      ctx.beginPath();
      ch.points.forEach((p, i) => (i ? ctx.lineTo(X(p[0]), Y(p[1])) : ctx.moveTo(X(p[0]), Y(p[1]))));
      ctx.stroke();
      const mid = ch.points[Math.floor(ch.points.length / 2)];
      ctx.font = "10.5px ui-monospace, monospace";
      G.haloText(ctx, `${ch.name} · ${ch.length}`, X(mid[0]) + 4, Y(mid[1]) - 4, COLOR.muted);
      for (let i = 1; i < ch.points.length; i++) {
        const a = ch.points[i - 1], b = ch.points[i];
        E.hits.push({ kind: "chan", name: ch.name, seg: [X(a[0]), Y(a[1]), X(b[0]), Y(b[1])] });
      }
    }
    // instances
    for (const inst of m.instances) {
      const c = masterOf(inst.master);
      const b = c.bbox && c.bbox.length ? c.bbox : [0, 0, 4, 4];
      const pad = c.kind === "leaf" ? 0.6 : 1.4;
      const r = [X(inst.x + b[0] - pad), Y(inst.y + b[1] - pad), X(inst.x + b[2] + pad), Y(inst.y + b[3] + pad)];
      const fam = FAMILY[c.family] || FAMILY.tile;
      const selected = E.sel.has(inst.name);
      ctx.fillStyle = fam[0];
      ctx.strokeStyle = selected ? COLOR.accent : fam[1];
      ctx.lineWidth = selected ? 2.5 : 1.3;
      if (c.kind === "composite") ctx.setLineDash([5, 3]);
      G.roundRect(ctx, r[0], r[1], r[2] - r[0], r[3] - r[1], 7);
      ctx.fill(); ctx.stroke(); ctx.setLineDash([]);
      // a leaf shows its rails, a composite its children's outlines
      if (c.kind === "leaf" && G.M.leafData[c.name]) {
        const d = G.M.leafData[c.name];
        const pos = {};
        for (const n of d.device.nodes) pos[n.id] = n.pos;
        ctx.strokeStyle = COLOR.rail;
        ctx.lineWidth = 1;
        ctx.beginPath();
        for (const sg of d.device.segments) {
          const a = pos[sg.ends[0]], e = pos[sg.ends[1]];
          ctx.moveTo(X(inst.x + a[0]), Y(inst.y + a[1])); ctx.lineTo(X(inst.x + e[0]), Y(inst.y + e[1]));
        }
        ctx.stroke();
      } else if (c.kind === "composite") {
        ctx.strokeStyle = "#c8ced8";
        ctx.lineWidth = 1;
        for (const ci of c.instances) {
          const cb = masterOf(ci.master).bbox;
          if (!cb || !cb.length) continue;
          ctx.strokeRect(X(inst.x + ci.x + cb[0]), Y(inst.y + ci.y + cb[1]), (cb[2] - cb[0]) * s, (cb[3] - cb[1]) * s);
        }
      }
      ctx.font = "600 12px ui-sans-serif, system-ui, sans-serif";
      G.haloText(ctx, G.clip(ctx, `${inst.name} · ${c.name}`, Math.max(40, r[2] - r[0] - 10)), r[0] + 6, r[1] + 15, COLOR.navy);
      E.hits.push({ kind: "inst", name: inst.name, rect: r });
      // ports
      for (const p of c.ports || []) {
        const pp = portPoint(c.name, p.name);
        if (!pp) continue;
        const ref = `${inst.name}.${p.name}`;
        const px = X(inst.x + pp[0]), py = Y(inst.y + pp[1]);
        const rad = Math.max(4, Math.min(8, s * 0.8));
        let ring = ROLE[p.role] || COLOR.teal;
        let fill = COLOR.panel;
        if (E.port) {
          const from = refPort(m, E.port);
          if (ref === E.port) fill = COLOR.accent;
          else if (from) fill = accepts(from, p).length ? "#fee4e2" : "#dcfae6";
        }
        const linked = m.channels.some((ch) => ch.a === ref || ch.b === ref);
        const exported = (m.ports || []).find((q) => q.bind === ref);
        ctx.fillStyle = fill;
        ctx.strokeStyle = ring;
        ctx.lineWidth = linked ? 2.5 : 1.5;
        ctx.beginPath(); ctx.arc(px, py, rad, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
        if (exported) {
          ctx.strokeStyle = COLOR.navy; ctx.lineWidth = 1.5;
          ctx.beginPath(); ctx.arc(px, py, rad + 4, 0, 2 * Math.PI); ctx.stroke();
        }
        if (s > 16 || selected || E.port) { ctx.font = "10px ui-monospace, monospace"; G.haloText(ctx, p.name + (exported ? ` ⇢ ${exported.name}` : ""), px + rad + 3, py - rad, COLOR.ink); }
        E.hits.push({ kind: "port", ref, rect: [px - rad - 3, py - rad - 3, px + rad + 3, py + rad + 3] });
      }
    }
    if (E.placing) {
      ctx.font = "13px ui-sans-serif, system-ui, sans-serif";
      G.haloText(ctx, `click on the canvas to place ${E.placing}  (Esc cancels)`, 14, h - 16, COLOR.accent);
    }
    renderBar();
  }

  function hitAt(x, y) {
    let best = null;
    for (const h of E.hits || []) {
      if (h.kind === "port" && x >= h.rect[0] && x <= h.rect[2] && y >= h.rect[1] && y <= h.rect[3]) return h;
    }
    for (const h of E.hits || []) {
      if (h.kind === "chan") {
        const [x0, y0, x1, y1] = h.seg;
        const L2 = (x1 - x0) ** 2 + (y1 - y0) ** 2;
        const t = L2 ? Math.max(0, Math.min(1, ((x - x0) * (x1 - x0) + (y - y0) * (y1 - y0)) / L2)) : 0;
        const d = Math.hypot(x - (x0 + t * (x1 - x0)), y - (y0 + t * (y1 - y0)));
        if (d < 6) best = best || h;
      }
    }
    if (best) return best;
    for (let i = (E.hits || []).length - 1; i >= 0; i--) {
      const h = E.hits[i];
      if (h.kind === "inst" && x >= h.rect[0] && x <= h.rect[2] && y >= h.rect[1] && y <= h.rect[3]) return h;
    }
    return null;
  }

  // ------------------------------------------------------------------ panels

  function renderBar() {
    let bar = $("editBar");
    if (!bar) return;
    const m = masterOf(E.master);
    const probs = problems(m);
    $("editStatus").innerHTML = probs.length
      ? `<span style="color:var(--bad)">${probs.length} problem${probs.length > 1 ? "s" : ""}</span>`
      : `<span style="color:var(--ok)">G1 ✓</span>`;
    $("editStatus").title = probs.join("\n");
    $("editMsg").textContent = E.message;
    $("editMsg").style.color = E.messageBad ? "var(--bad)" : "var(--muted)";
    $("eUndo").disabled = !E.undo.length;
    $("eRedo").disabled = !E.redo.length;
  }

  function renderPalette() {
    const el = $("tree");
    el.textContent = "";
    const head = document.createElement("div");
    head.className = "note";
    head.style.padding = "4px 10px";
    head.textContent = "Click a master, then click the canvas to place it.";
    el.appendChild(head);
    const groups = {};
    for (const mm of Object.values(E.lib.masters)) (groups[mm.kind === "leaf" ? "leaf gadgets" : "composite gadgets"] ||= []).push(mm);
    for (const g of ["leaf gadgets", "composite gadgets"]) {
      const h = document.createElement("div");
      h.className = "note";
      h.style.cssText = "padding:6px 10px 2px;text-transform:uppercase;font-size:10.5px;letter-spacing:.06em";
      h.textContent = g;
      el.appendChild(h);
      for (const mm of (groups[g] || []).sort((a, b) => a.name.localeCompare(b.name))) {
        const div = document.createElement("div");
        div.className = "tn" + (E.placing === mm.name ? " sel" : "");
        div.style.paddingLeft = "10px";
        const fam = (G.FAMILY[mm.family] || G.FAMILY.tile);
        div.innerHTML = `<span class="dot" style="background:${fam[1]}"></span><span class="nm"></span><span class="sub"></span>`;
        div.children[1].textContent = mm.name;
        div.children[2].textContent = mm.family + (mm.name === E.master ? " · editing" : "");
        div.onclick = () => { E.placing = mm.name === E.master ? null : mm.name; renderPalette(); G.redraw(); };
        div.ondblclick = () => { if (mm.kind === "composite") openMaster(mm.name); };
        div.title = `${mm.title}\n${(mm.ports || []).map((p) => `${p.name}: ${p.dir} ${p.role} x${p.width}`).join("\n")}` +
          (mm.kind === "composite" ? "\ndouble-click to edit it" : "");
        el.appendChild(div);
      }
    }
    $("treeCount").textContent = `${Object.keys(E.lib.masters).length} masters`;
  }

  function renderInspector() {
    const m = masterOf(E.master);
    const el = $("inspBody");
    const esc = G.escapeHtml;
    let h = "";
    if (E.chan) {
      const ch = m.channels.find((c) => c.name === E.chan);
      const a = refPort(m, ch.a), b = refPort(m, ch.b);
      const why = a && b ? accepts(a, b) : ["an end is missing"];
      h += `<h4>channel ${esc(ch.name)}</h4><div class="muted">${esc(ch.a)} → ${esc(ch.b)}</div>`;
      h += `<div class="note">${ch.length} sites along a Manhattan route; a carry is priced by replaying a conveyor of that length</div>`;
      h += why.length ? `<div class="note" style="color:var(--bad)">G1: ${esc(why.join("; "))}</div>` : `<div class="note" style="color:var(--ok)">G1 ✓ ${a.role}/${b.role}</div>`;
      h += `<div class="btns"><button data-e="delete">Delete channel</button></div>`;
    } else if (E.sel.size === 1) {
      const inst = m.instances.find((i) => E.sel.has(i.name));
      const c = masterOf(inst.master);
      h += `<h4><input id="eName" value="${esc(inst.name)}" style="font:inherit;font-weight:600;width:60%"></h4>`;
      h += `<div class="muted">${esc(c.name)} · ${c.kind} ${c.family} · at (${inst.x}, ${inst.y})</div>`;
      h += `<table><tr><th>port</th><th>dir</th><th>role</th><th class="num">w</th><th></th></tr>`;
      for (const p of c.ports || []) {
        const ref = `${inst.name}.${p.name}`;
        const ch = m.channels.find((x) => x.a === ref || x.b === ref);
        const ex = (m.ports || []).find((q) => q.bind === ref);
        h += `<tr><td>${esc(p.name)}</td><td>${p.dir}</td><td>${p.role}</td><td class="num">${p.width}</td><td>` +
          (ch ? `<span class="note">${esc(ch.name)}</span>` : "") +
          (ex ? ` <span class="note">⇢ ${esc(ex.name)}</span>` : ` <button data-export="${esc(ref)}" title="make this a port of ${esc(m.name)}">export</button>`) + `</td></tr>`;
      }
      h += `</table>`;
      h += `<div class="btns"><button data-e="delete">Delete</button>` +
        (c.kind === "composite" ? `<button data-e="ungroup">Ungroup</button><button data-open="${esc(c.name)}">Edit ${esc(c.name)} ↘</button>` : "") + `</div>`;
    } else {
      const inv = Object.entries(m.inventory || {}).map(([k, v]) => `${v} ${k}`).join(", ") || "no ions";
      h += `<h4>${esc(m.name)}</h4><div class="muted">composite · ${m.instances.length} instances · ${m.channels.length} channels · ${inv}</div>`;
      if (E.sel.size > 1) h += `<div class="note">${E.sel.size} selected — <button data-e="group">Group into a new gadget</button></div>`;
      h += `<table><tr><th>port</th><th>dir</th><th>role</th><th>binds</th></tr>`;
      for (const p of m.ports || []) h += `<tr><td>${esc(p.name)}</td><td>${p.dir}</td><td>${p.role}</td><td>${esc(p.bind || "")}</td></tr>`;
      h += `</table>`;
      const probs = problems(m);
      h += probs.length ? probs.slice(0, 12).map((x) => `<div class="note" style="color:var(--bad)">• ${esc(x)}</div>`).join("")
        : `<div class="note" style="color:var(--ok)">every channel passes G1</div>`;
      h += `<div class="note">Shortcuts: click a port then another to connect · shift-click to multi-select · Del deletes · G groups · Ctrl+Z / Ctrl+Y · Esc cancels</div>`;
    }
    el.innerHTML = h;
    for (const b of el.querySelectorAll("[data-e]")) b.onclick = () => ({ delete: removeSelection, group: () => group(), ungroup })[b.dataset.e]();
    for (const b of el.querySelectorAll("[data-export]")) b.onclick = () => exportPort(b.dataset.export);
    for (const b of el.querySelectorAll("[data-open]")) b.onclick = () => openMaster(b.dataset.open);
    const nameInput = $("eName");
    if (nameInput) nameInput.onchange = () => renameInstance([...E.sel][0], nameInput.value.trim());
  }

  function renameInstance(old, name) {
    const m = masterOf(E.master);
    if (!name || name === old) return;
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) { say("an instance name is a plain identifier", true); return; }
    if (m.instances.some((i) => i.name === name)) { say(`${name} is taken`, true); return; }
    commit(`rename ${old} to ${name}`);
    for (const i of m.instances) if (i.name === old) i.name = name;
    const re = (ref) => ref && ref.split(".")[0] === old ? `${name}.${ref.split(".")[1]}` : ref;
    for (const ch of m.channels) { ch.a = re(ch.a); ch.b = re(ch.b); }
    for (const p of m.ports || []) p.bind = re(p.bind);
    E.sel = new Set([name]);
    update();
  }

  function say(text, bad) { E.message = text; E.messageBad = !!bad; if ($("editMsg")) renderBar(); }

  function update() {
    const m = masterOf(E.master);
    refresh(m);
    renderInspector();
    renderPalette();
    const crumbs = $("crumbs");
    crumbs.innerHTML = `<a class="here">editing ${G.escapeHtml(E.master)}</a>`;
    G.redraw();
  }

  // ------------------------------------------------------------------ open / close

  function ensureBar() {
    if ($("editBar")) return;
    const bar = document.createElement("div");
    bar.id = "editBar";
    bar.style.cssText = "position:absolute;left:10px;top:8px;right:10px;display:flex;gap:6px;align-items:center;z-index:5;flex-wrap:wrap";
    bar.innerHTML = `<b style="color:var(--navy);margin-right:4px">Edit</b>
      <button id="eUndo" title="Ctrl+Z">Undo</button><button id="eRedo" title="Ctrl+Y">Redo</button>
      <button id="eGroup" title="G">Group</button><button id="eUngroup">Ungroup</button><button id="eDelete" title="Del">Delete</button>
      <button id="eFit">Fit</button><span id="editStatus" style="margin-left:6px"></span>
      <span id="editMsg" style="flex:1;min-width:120px;color:var(--muted);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis"></span>
      <button id="eExport" class="on" title="download the library as qccd.gadget/0.1 JSON">Export design</button><button id="eClose">Close</button>`;
    $("stageWrap").appendChild(bar);
    $("eUndo").onclick = undo; $("eRedo").onclick = redoIt; $("eGroup").onclick = () => group();
    $("eUngroup").onclick = ungroup; $("eDelete").onclick = removeSelection; $("eExport").onclick = exportDesign;
    $("eClose").onclick = close; $("eFit").onclick = fitMaster;
  }

  function fitMaster() {
    const m = masterOf(E.master);
    refresh(m);
    const [w, h] = G.sizeCanvas(G.stage, G.sctx);
    const [x0, y0, x1, y1] = m.bbox;
    const pad = 60;
    const s = Math.min((w - 2 * pad) / Math.max(10, x1 - x0), (h - 2 * pad) / Math.max(10, y1 - y0));
    G.S.cam.s = Math.max(0.2, Math.min(40, s));
    G.S.cam.x = (w - (x1 - x0) * G.S.cam.s) / 2 - x0 * G.S.cam.s;
    G.S.cam.y = (h - (y1 - y0) * G.S.cam.s) / 2 - y0 * G.S.cam.s + 16;
    G.redraw();
  }

  function openMaster(name) {
    if (!E.lib) E.lib = clone(G.M.lib);
    if (!E.lib.masters[name] || E.lib.masters[name].kind !== "composite") return;
    E.master = name;
    E.sel.clear(); E.chan = null; E.port = null; E.placing = null;
    fitMaster();
    update();
  }

  function open(name) {
    if (!E.lib) E.lib = clone(G.M.lib);
    E.active = true;
    document.body.dataset.editing = "1";
    for (const id of ["hud", "legend", "stageTools", "checks"]) { const el = $(id); if (el) el.style.display = "none"; }
    $("prog").style.display = "none";
    $("tlWrap").style.opacity = "0.35";
    ensureBar();
    $("editBar").style.display = "flex";
    openMaster(name || G.M.master(G.S.path).name);
    say("editing a copy of the library; the running schedule is unchanged until you export and rebuild");
  }

  function create() {
    if (!E.lib) E.lib = clone(G.M.lib);
    const name = (window.prompt("Name the new composite gadget:", uniqueMaster("my_gadget")) || "").trim();
    if (!name) return;
    if (E.lib.masters[name]) { window.alert(`a master named ${name} exists`); return; }
    E.lib.masters[name] = { name, kind: "composite", family: "custom", title: name, doc: "made in the editor",
      size: [10, 10], bbox: [0, 0, 10, 10], ports: [], inventory: {}, capacity: 0, ops: {}, params: {},
      device: null, instances: [], channels: [] };
    open(name);
    say("an empty gadget: pick a master in the palette and click the canvas to place it");
  }

  function close() {
    E.active = false;
    delete document.body.dataset.editing;
    for (const id of ["hud", "legend", "stageTools", "checks"]) { const el = $(id); if (el) el.style.display = ""; }
    $("prog").style.display = "";
    $("tlWrap").style.opacity = "";
    if ($("editBar")) $("editBar").style.display = "none";
    G.renderTree();
    G.renderInspector();
    G.crumbs();
    G.fit();
  }

  // ------------------------------------------------------------------ input

  function attach(g) {
    G = g;
    const stage = G.stage;
    const btn = document.getElementById("bEdit");
    if (btn) btn.onclick = () => {
      const node = G.M.node(G.S.path);
      open(node.leaf ? G.M.master(node.parent || "").name : G.M.master(G.S.path).name);
    };
    const toModel = (ev) => {
      const r = stage.getBoundingClientRect();
      const x = ev.clientX - r.left, y = ev.clientY - r.top;
      return { x, y, mx: (x - G.S.cam.x) / G.S.cam.s, my: (y - G.S.cam.y) / G.S.cam.s };
    };
    stage.addEventListener("pointerdown", (ev) => {
      if (!E.active) return;
      stage.setPointerCapture(ev.pointerId);
      const p = toModel(ev);
      const h = hitAt(p.x, p.y);
      const m = masterOf(E.master);
      if (E.placing && !h) { place(E.placing, p.mx, p.my); if (!ev.shiftKey) { E.placing = null; renderPalette(); } return; }
      if (h && h.kind === "port") {
        if (E.port && E.port !== h.ref) { const a = E.port; E.port = null; connect(a, h.ref); }
        else { E.port = E.port === h.ref ? null : h.ref; E.chan = null; say(E.port ? `from ${E.port}: click a green port to connect (red ones fail G1)` : ""); renderInspector(); G.redraw(); }
        return;
      }
      E.port = null;
      if (h && h.kind === "chan") { E.chan = h.name; E.sel.clear(); renderInspector(); G.redraw(); return; }
      E.chan = null;
      if (h && h.kind === "inst") {
        if (ev.shiftKey) { if (E.sel.has(h.name)) E.sel.delete(h.name); else E.sel.add(h.name); }
        else if (!E.sel.has(h.name)) E.sel = new Set([h.name]);
        const moving = m.instances.filter((i) => E.sel.has(i.name)).map((i) => ({ i, x: i.x, y: i.y }));
        E.drag = { kind: "move", mx: p.mx, my: p.my, moving, committed: false };
      } else {
        if (!ev.shiftKey) E.sel.clear();
        E.drag = { kind: "pan", x: p.x, y: p.y, cx: G.S.cam.x, cy: G.S.cam.y };
      }
      renderInspector();
      G.redraw();
    });
    stage.addEventListener("pointermove", (ev) => {
      if (!E.active || !E.drag) return;
      const p = toModel(ev);
      if (E.drag.kind === "pan") { G.S.cam.x = E.drag.cx + p.x - E.drag.x; G.S.cam.y = E.drag.cy + p.y - E.drag.y; G.redraw(); return; }
      const dx = Math.round(p.mx - E.drag.mx), dy = Math.round(p.my - E.drag.my);
      if ((dx || dy) && !E.drag.committed) { commit(`move ${[...E.sel].join(", ")}`); E.drag.committed = true; }
      for (const mv of E.drag.moving) { mv.i.x = mv.x + dx; mv.i.y = mv.y + dy; }
      refresh(masterOf(E.master));
      G.redraw();
    });
    stage.addEventListener("pointerup", () => { if (!E.active) return; if (E.drag && E.drag.committed) update(); E.drag = null; });
    window.addEventListener("keydown", (ev) => {
      if (!E.active || ev.target.tagName === "INPUT") return;
      if (ev.key === "Delete" || ev.key === "Backspace") { ev.preventDefault(); removeSelection(); }
      else if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "z") { ev.preventDefault(); undo(); }
      else if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "y") { ev.preventDefault(); redoIt(); }
      else if (ev.key === "g" || ev.key === "G") group();
      else if (ev.key === "Escape") { if (E.placing || E.port) { E.placing = null; E.port = null; renderPalette(); G.redraw(); } else close(); }
      else if (ev.key === "f" || ev.key === "F") fitMaster();
    });
  }

  window.GADGET_EDITOR = {
    get active() { return E.active; }, attach, open, create, close, draw, E,
    // the operations a headless test drives
    place, connect, group, ungroup, removeSelection, exportPort, undo, redo: redoIt, problems: () => problems(masterOf(E.master)),
    select: (names) => { E.sel = new Set(names); E.chan = null; update(); },
    lib: () => E.lib, master: () => masterOf(E.master),
    json: () => JSON.stringify({ schema: "qccd.gadget/0.1", top: E.master, masters: E.lib.masters }),
  };
})();
