// THE 3D STAGE: the same machine and the same programme as the SVG stage, drawn as a stack
// of layers you can turn.  docs/PLAN-3d.md.
//
// ONE POSITION LAW.  This file computes no ion position.  `draw()` asks `TRANSIT.place`
// where every ion is, draws the SVG marks, and then hands the same answer here
// (`STAGE3D.frame`).  What this file adds is where a LAYER is: on the SVG a machine built
// in layers is one panel a layer, side by side (`L.panels`); here each panel is slid back
// over the others and lifted to its own height.  So an ion's place in its layer is the SVG
// mark's place in its panel, exactly, and the two stages cannot disagree about where an ion
// is -- only about how the layers are laid out.  `fold` is that one number: 0 is the SVG
// picture seen from above (panels side by side, everything flat), 1 is the stack.
//
// TWO PARTS.  `describe()` and `ions()` are pure: page data in, plain records out.  They
// run under the Node shim with no canvas, and the tests read them.  Everything below
// "the renderer" owns the three.js objects and copies those records into instanced meshes;
// it knows nothing about frames, rules or ions beyond the records.
//
// A RISER IS A RAIL.  It is drawn with a rail's width and a rail's colour, standing up.
//
// COST.  One instanced mesh for each kind of part, so a frame is about a dozen draw calls
// whatever the machine's size.  Nothing is drawn while nothing changes: the loop runs only
// while the programme plays, the camera moves or an animation is in flight.  The WebGL
// context is made when 3D is first shown and released when it is hidden, because a browser
// allows a page only a handful.
(function () {
'use strict';

var T = globalThis.QCCDTHREE || null;       // the vendored three.js build; null without it

// ----------------------------------------------------------------------------- state
var S = {
  on: false,          // is the 3D stage the one being shown
  fold: 1,            // 0: panels side by side and flat (the SVG picture); 1: the stack
  spacing: 1,         // the reader's multiplier on the distance between layers
  only: -1,           // the one layer shown at full strength, or -1 for all
  why: ''             // why 3D cannot be shown, when it cannot
};
var R = null;         // the renderer's objects, while a WebGL context is held
var LAST = null;      // the last frame `draw()` handed over, so a rebuild can redraw it

// ----------------------------------------------------------------------- pure geometry
// Stage units are the SVG's user units, with y turned over (the SVG's y runs down the
// page, and here z is up and the eye is on the -y side).

function panels() {
  var P = (typeof L !== 'undefined' && L && L.panels) ? L.panels : null;
  if (!P || !P.length) return [{ x: 0, y: 0 }];
  return P.map(function (p) { return { x: p[0] * L.sx, y: p[1] * L.sy }; });
}
function layerOf(id) { var n = nodeById[id]; return (n && n.z) ? n.z : 0; }
function layerName(k) { return (k >= 0 && k < 26) ? String.fromCharCode(65 + k) : 'L' + k; }

// The distance between two layers, before the reader's multiplier.  Not to scale and not
// meant to be: on the die it is tens of micrometres under millimetres of trap, and a riser
// drawn that short would not be seen.  A fraction of the machine's own extent, held
// between two and ten lattice steps.
function layerGap() {
  var b = bounds(true), ext = Math.max(b.x1 - b.x0, b.y1 - b.y0), g = L.g || 1;
  return Math.min(10 * g, Math.max(2 * g, 0.16 * ext)) * S.spacing;
}
// An ion is drawn a little larger here than on the flat stage: seen at an angle and from
// further off, the same bead would be lost.  Size is a mark's, not a measurement.
var ION_SCALE = 1.3;
// How far above its layer's surface an ion is drawn.  A real ion floats above the
// electrodes; here the height is one ion radius, so a resting ion clears its rail.
function lift() { return 1.15 * ION_SCALE * (L.r_ion || 1); }

// where a point of panel `panel`, drawn at (x, y) on the SVG, stands at layer height `zf`
// (a layer index, fractional on a riser)
function place3(x, y, panel, zf, up, fold) {
  var P = panels(), p = P[panel] || P[0];
  return [x - p.x * fold, -(y - p.y * fold), zf * layerGap() * fold + (up || 0)];
}

// the drawn extent of every layer: in its own panel (`stacked` false) or slid back over
// the others (`stacked` true)
function bounds(stacked) {
  var P = panels(), x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (var i = 0; i < A.nodes.length; i++) {
    var n = A.nodes[i], p = P[n.z || 0] || P[0];
    var x = px(n) - (stacked ? p.x : 0), y = py(n) - (stacked ? p.y : 0);
    if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
  }
  if (!(x0 <= x1)) { x0 = y0 = 0; x1 = y1 = 1; }
  return { x0: x0, y0: y0, x1: x1, y1: y1 };
}

// THE STATIC SCENE, as records.  Everything is in SVG coordinates of the part's own panel;
// `k` is its layer.  The renderer puts each layer's parts in a group and moves the group.
function describe() {
  var out = { layers: [], rails: [], sites: [], juncs: [], risers: [] };
  var P = panels(), k, i;
  for (k = 0; k < P.length; k++) {
    out.layers.push({ k: k, name: layerName(k), x0: Infinity, y0: Infinity,
                      x1: -Infinity, y1: -Infinity, nodes: 0 });
  }
  for (i = 0; i < A.nodes.length; i++) {
    var n = A.nodes[i], x = px(n), y = py(n), lay = out.layers[n.z || 0] || out.layers[0];
    lay.nodes++;
    if (x < lay.x0) lay.x0 = x; if (x > lay.x1) lay.x1 = x;
    if (y < lay.y0) lay.y0 = y; if (y > lay.y1) lay.y1 = y;
    if (n.kind === 'junction' || (n.cap || 0) === 0) {
      out.juncs.push({ id: n.id, k: lay.k, x: x, y: y, h: L.r_junc,
                       colour: n.corner ? C.corner : C.junction, deg: n.deg });
    } else {
      var ax = AXIS[n.id] || { ux: 1, uy: 0 };
      out.sites.push({ id: n.id, k: lay.k, x: x, y: y, len: siteLen(n.cap), t: L.site_t,
                       ang: Math.atan2(ax.uy, ax.ux), colour: zoneColour(n.zone), cap: n.cap });
    }
  }
  for (i = 0; i < A.segments.length; i++) {
    var sg = A.segments[i], I = SEGINFO[sg.id];
    var a = nodeById[sg.a], b = nodeById[sg.b];
    if (!a || !b) continue;
    if (sg.riser) { out.risers.push({ id: sg.id, a: sg.a, b: sg.b, w: L.sw_rail, colour: C.rail }); continue; }
    if (!I || I.len < 1e-6) continue;
    var role = ROLE[sg.id] || 'rail', thin = role === 'compute';
    var w = thin ? L.sw_thin : L.sw_rail, colour = C[role] || C.rail;
    // a bowed rail is a curve; ten straight pieces of it are indistinguishable at any zoom
    // the stage reaches, and they are evaluated on the SAME curve the ion rides
    var pieces = I.cp ? 10 : 1, q0 = bezPoint(I, 0);
    for (var j = 1; j <= pieces; j++) {
      var q1 = bezPoint(I, j / pieces);
      out.rails.push({ id: sg.id, k: a.z || 0, x: (q0.x + q1.x) / 2, y: (q0.y + q1.y) / 2,
                       len: Math.hypot(q1.x - q0.x, q1.y - q0.y),
                       ang: Math.atan2(q1.y - q0.y, q1.x - q0.x), w: w, colour: colour });
      q0 = q1;
    }
  }
  return out;
}

// THE IONS OF ONE FRAME, as records.  `live` and `flying` are `TRANSIT.place`'s own answer,
// as `draw()` got it.  `panel` is the panel the SVG mark is drawn in and `zf` the layer
// height: the same number at rest and inside a layer, and part way between two on a riser.
function ions(live, flying, f, stt) {
  var out = [];
  for (var ion in live) {
    var pt = live[ion], q = flying ? flying[ion] : null, p = IONP[ion] || {};
    var panel, zf, riser = false;
    if (q && q.a && q.b && layerOf(q.a) !== layerOf(q.b)) {
      var la = layerOf(q.a), lb = layerOf(q.b);
      riser = true;
      zf = la + (lb - la) * q.u;
      panel = q.u < 0.5 ? la : lb;                 // the panel the SVG mark is drawn in
    } else {
      panel = (q && q.a) ? layerOf(q.a)
        : layerOf(pt.node !== undefined && pt.node !== null ? pt.node : stt.pos[ion]);
      zf = panel;
    }
    out.push({ ion: ion, x: pt.x, y: pt.y, panel: panel, zf: zf, riser: riser,
               r: ION_SCALE * (p.r3 || L.r_rest), fly: !!pt.fly, beam: p.b3 || null,
               colour: ionColour(ion, f, stt) });
  }
  return out;
}

// where each ion of a frame stands in the stack: `{ion: [X, Y, Z]}` at `fold` (1 unless
// given).  The tests compare this with the SVG marks; nothing else needs it.
function positions(live, flying, f, stt, fold) {
  var out = {}, list = ions(live, flying, f, stt), up = lift();
  fold = fold === undefined ? 1 : fold;
  for (var i = 0; i < list.length; i++) {
    var d = list[i];
    out[d.ion] = place3(d.x, d.y, d.panel, d.zf, up, fold);
  }
  return out;
}

// ------------------------------------------------------------------------ the renderer
function available() {
  if (!T) { S.why = 'this page was built without the 3D stage'; return false; }
  if (typeof document === 'undefined' || !document.createElement) { S.why = 'no document'; return false; }
  var c = document.createElement('canvas');
  if (!c || typeof c.getContext !== 'function') { S.why = 'this browser has no canvas'; return false; }
  return true;
}

var M4 = T ? new T.Matrix4() : null, V3 = T ? new T.Vector3() : null,
    SC = T ? new T.Vector3() : null, Q4 = T ? new T.Quaternion() : null,
    ZAX = T ? new T.Vector3(0, 0, 1) : null, YAX = T ? new T.Vector3(0, 1, 0) : null,
    COL = T ? new T.Color() : null;

function mat(colour, extra) {
  var o = { color: colour === undefined ? 0xffffff : colour, roughness: 0.78, metalness: 0.0 };
  for (var k in (extra || {})) o[k] = extra[k];
  return new T.MeshStandardMaterial(o);
}
function instanced(geo, material, n, parent) {
  var m = new T.InstancedMesh(geo, material, Math.max(1, n));
  m.count = n; m.frustumCulled = false; parent.add(m);
  return m;
}
function setColour(mesh, i, css) { mesh.setColorAt(i, COL.set(css || '#888888')); }

function start() {
  var host = document.getElementById('stage3d');
  if (!host) { S.why = 'this page has no 3D stage'; return false; }
  var renderer;
  try {
    renderer = new T.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
  } catch (e) { S.why = 'this browser could not start WebGL'; return false; }
  renderer.setPixelRatio(Math.min(globalThis.devicePixelRatio || 1, 2));
  renderer.setClearColor(0x000000, 0);
  renderer.domElement.className = 's3canvas';
  host.insertBefore(renderer.domElement, host.firstChild);
  var scene = new T.Scene();
  scene.add(new T.HemisphereLight(0xffffff, 0xc9d0dc, 1.25));
  var sun = new T.DirectionalLight(0xffffff, 1.35); sun.position.set(-0.5, -0.8, 2.0); scene.add(sun);
  var camera = new T.PerspectiveCamera(30, 1, 1, 100000); camera.up.set(0, 0, 1);
  var controls = new T.OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.dampingFactor = 0.14;
  controls.zoomToCursor = true;
  R = { host: host, renderer: renderer, scene: scene, camera: camera, controls: controls,
        root: new T.Group(), groups: [], mats: [], labels: [], raf: 0, dirty: true,
        anim: null, ionCap: 0, geo: {} };
  scene.add(R.root);
  controls.addEventListener('change', kick);
  R.onResize = function () { resize(); kick(); };
  if (typeof ResizeObserver === 'function') { R.ro = new ResizeObserver(R.onResize); R.ro.observe(host); }
  else globalThis.addEventListener('resize', R.onResize);
  renderer.domElement.addEventListener('pointermove', hover);
  renderer.domElement.addEventListener('pointerleave', function () { tip(null); });
  renderer.domElement.addEventListener('dblclick', function () { view('corner', true); });
  renderer.domElement.addEventListener('webglcontextlost', function (e) { e.preventDefault(); off(true); });
  R.geo.box = new T.BoxGeometry(1, 1, 1);
  R.geo.sphere = new T.SphereGeometry(1, 20, 14);
  R.geo.disc = new T.CircleGeometry(1, 24);
  R.geo.rod = new T.CylinderGeometry(1, 1, 1, 10, 1, true);     // along y; stretched and turned
  buildStatic3();
  resize();
  return true;
}

function stop() {
  if (!R) return;
  if (R.raf) cancelAnimationFrame(R.raf);
  if (R.ro) R.ro.disconnect(); else globalThis.removeEventListener('resize', R.onResize);
  R.controls.dispose();
  R.scene.traverse(function (o) {
    if (o.geometry && o.geometry.dispose) o.geometry.dispose();
    if (o.material && o.material.dispose) o.material.dispose();
  });
  for (var k in R.geo) R.geo[k].dispose();
  R.renderer.dispose();
  // give the context back NOW: a page of several embedded stages must not hold one each
  if (R.renderer.forceContextLoss) R.renderer.forceContextLoss();
  if (R.renderer.domElement.parentNode) R.renderer.domElement.parentNode.removeChild(R.renderer.domElement);
  for (var i = 0; i < R.labels.length; i++) if (R.labels[i].parentNode) R.labels[i].parentNode.removeChild(R.labels[i]);
  R = null;
}

function clearRoot() {
  var kids = R.root.children.slice();
  for (var i = 0; i < kids.length; i++) {
    R.root.remove(kids[i]);
    kids[i].traverse(function (o) { if (o.material && o.material.dispose) o.material.dispose(); });
  }
  for (var j = 0; j < R.labels.length; j++) if (R.labels[j].parentNode) R.labels[j].parentNode.removeChild(R.labels[j]);
  R.groups = []; R.mats = []; R.labels = []; R.ionCap = 0;
}

// the machine itself: built when the stage starts and again whenever the page rebuilds its
// own static picture (an edit, a relayout, the true-scale switch)
function buildStatic3() {
  if (!R) return;
  clearRoot();
  var D = describe(), k, i, d;
  R.desc = D;
  var per = D.layers.map(function () { return { rails: [], sites: [], juncs: [] }; });
  for (i = 0; i < D.rails.length; i++) (per[D.rails[i].k] || per[0]).rails.push(D.rails[i]);
  for (i = 0; i < D.sites.length; i++) (per[D.sites[i].k] || per[0]).sites.push(D.sites[i]);
  for (i = 0; i < D.juncs.length; i++) (per[D.juncs[i].k] || per[0]).juncs.push(D.juncs[i]);
  var thick = Math.max(0.6, 0.45 * (L.sw_rail || 1));
  for (k = 0; k < D.layers.length; k++) {
    var g = new T.Group(), lay = D.layers[k], mats = [];
    // the layer itself: a faint plane under its parts, a little larger than they are
    if (lay.nodes) {
      var pad = 1.1 * (L.g || 1);
      var pm = new T.MeshBasicMaterial({ color: COL.set(C.navy || '#1e2761').getHex(), transparent: true,
                                         opacity: 0.05, depthWrite: false, side: T.DoubleSide });
      var plane = new T.Mesh(new T.PlaneGeometry((lay.x1 - lay.x0) + 2 * pad, (lay.y1 - lay.y0) + 2 * pad), pm);
      plane.position.set((lay.x0 + lay.x1) / 2, -(lay.y0 + lay.y1) / 2, -thick);
      pm.userData.base = 0.05; g.add(plane); mats.push(pm);
    }
    var mr = mat(0xffffff), ms = mat(0xffffff, { transparent: true, opacity: 0.62, depthWrite: false }),
        mj = mat(0xffffff);
    mr.userData.base = 1; ms.userData.base = 0.62; mj.userData.base = 1;
    mats.push(mr, ms, mj);
    var rails = instanced(R.geo.box, mr, per[k].rails.length, g);
    for (i = 0; i < per[k].rails.length; i++) {
      d = per[k].rails[i];
      Q4.setFromAxisAngle(ZAX, -d.ang);
      M4.compose(V3.set(d.x, -d.y, 0), Q4, SC.set(d.len, d.w, thick));
      rails.setMatrixAt(i, M4); setColour(rails, i, d.colour);
    }
    var sites = instanced(R.geo.box, ms, per[k].sites.length, g);
    for (i = 0; i < per[k].sites.length; i++) {
      d = per[k].sites[i];
      Q4.setFromAxisAngle(ZAX, -d.ang);
      M4.compose(V3.set(d.x, -d.y, thick * 0.6), Q4, SC.set(d.len, d.t, thick * 2.4));
      sites.setMatrixAt(i, M4); setColour(sites, i, d.colour);
    }
    sites.userData.kind = 'site'; sites.userData.items = per[k].sites;
    var juncs = instanced(R.geo.box, mj, per[k].juncs.length, g);
    for (i = 0; i < per[k].juncs.length; i++) {
      d = per[k].juncs[i];
      M4.compose(V3.set(d.x, -d.y, thick * 0.3), Q4.identity(), SC.set(2 * d.h, 2 * d.h, thick * 1.6));
      juncs.setMatrixAt(i, M4); setColour(juncs, i, d.colour);
    }
    juncs.userData.kind = 'junction'; juncs.userData.items = per[k].juncs;
    [rails, sites, juncs].forEach(function (m) {
      m.instanceMatrix.needsUpdate = true; if (m.instanceColor) m.instanceColor.needsUpdate = true;
    });
    R.root.add(g); R.groups.push(g); R.mats.push(mats);
    if (D.layers.length > 1 && lay.nodes) {
      var tag = document.createElement('div');
      tag.className = 's3label'; tag.textContent = 'layer ' + lay.name;
      R.host.appendChild(tag); R.labels.push(tag); tag._k = k;
    }
  }
  // risers: rails that stand up.  Their ends move with the layers, so they are placed in
  // `layoutLayers`, not here.
  R.mRiser = instanced(R.geo.box, mat(0xffffff), D.risers.length, R.root);
  for (i = 0; i < D.risers.length; i++) setColour(R.mRiser, i, D.risers[i].colour);
  if (R.mRiser.instanceColor) R.mRiser.instanceColor.needsUpdate = true;
  // what moves: the ions, a shadow under each on its own layer, and what a laser is doing
  R.mIon = null; R.mShadow = null; R.mSpot = null; R.mBeam = null; R.mLink = null; R.mHalo = null;
  growIons(64);
  layoutLayers();
  if (LAST) frame(LAST.live, LAST.flying, LAST.f, LAST.stt);
}

function growIons(n) {
  if (n <= R.ionCap) return;
  var cap = Math.max(64, 1 << Math.ceil(Math.log(n) / Math.LN2));
  ['mIon', 'mShadow', 'mSpot', 'mBeam', 'mLink', 'mHalo'].forEach(function (key) {
    if (R[key]) { R.root.remove(R[key]); R[key].material.dispose(); }
  });
  R.mIon = instanced(R.geo.sphere, mat(0xffffff, { roughness: 0.38 }), cap, R.root);
  R.mShadow = instanced(R.geo.disc, new T.MeshBasicMaterial({ color: 0x101828, transparent: true, opacity: 0.16, depthWrite: false }), cap, R.root);
  R.mSpot = instanced(R.geo.disc, new T.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.30, depthWrite: false }), cap, R.root);
  R.mBeam = instanced(R.geo.rod, new T.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.72, depthWrite: false }), cap, R.root);
  R.mLink = instanced(R.geo.rod, new T.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.55, depthWrite: false }), cap, R.root);
  R.mHalo = instanced(R.geo.disc, new T.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.5, depthWrite: false }), cap, R.root);
  ['mIon', 'mShadow', 'mSpot', 'mBeam', 'mLink', 'mHalo'].forEach(function (key) {
    R[key].instanceMatrix.setUsage(T.DynamicDrawUsage); R[key].count = 0;
    R[key].instanceColor = new T.InstancedBufferAttribute(new Float32Array(cap * 3), 3);
    R[key].instanceColor.setUsage(T.DynamicDrawUsage);
  });
  R.ionCap = cap;
}

// a rod (a cylinder along y, radius 1, length 1) from point a to point b
function rodBetween(mesh, i, a, b, radius) {
  var dx = b[0] - a[0], dy = b[1] - a[1], dz = b[2] - a[2], len = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1e-6;
  Q4.setFromUnitVectors(YAX, V3.set(dx / len, dy / len, dz / len));
  M4.compose(V3.set((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2), Q4, SC.set(radius, len, radius));
  mesh.setMatrixAt(i, M4);
}

// where the layers are: each layer's group is slid and lifted, the risers are stretched
// between their two ends, and a layer the reader has not picked is drawn faint
function layoutLayers() {
  if (!R) return;
  var P = panels(), gap = layerGap(), D = R.desc, k, i;
  for (k = 0; k < R.groups.length; k++) {
    var p = P[k] || P[0];
    R.groups[k].position.set(-p.x * S.fold, p.y * S.fold, k * gap * S.fold);
    var dim = (S.only >= 0 && S.only !== k) ? 0.16 : 1;
    for (i = 0; i < R.mats[k].length; i++) {
      var m = R.mats[k][i], base = m.userData.base === undefined ? 1 : m.userData.base;
      m.opacity = base * dim; m.transparent = (base * dim) < 1; m.depthWrite = (base * dim) >= 1;
      m.needsUpdate = true;
    }
  }
  var thick = Math.max(0.6, 0.45 * (L.sw_rail || 1));
  for (i = 0; i < D.risers.length; i++) {
    var r = D.risers[i], a = nodeById[r.a], b = nodeById[r.b];
    var pa = place3(px(a), py(a), a.z || 0, a.z || 0, 0, S.fold);
    var pb = place3(px(b), py(b), b.z || 0, b.z || 0, 0, S.fold);
    var dx = pb[0] - pa[0], dy = pb[1] - pa[1], dz = pb[2] - pa[2];
    var len = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1e-6;
    // a box whose LONG side is x, turned to run from one end to the other: a rail, upright
    Q4.setFromUnitVectors(V3.set(1, 0, 0), SC.set(dx / len, dy / len, dz / len));
    M4.compose(V3.set((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2, (pa[2] + pb[2]) / 2), Q4, SC.set(len, r.w, thick));
    R.mRiser.setMatrixAt(i, M4);
  }
  R.mRiser.instanceMatrix.needsUpdate = true;
  R.mRiser.material.opacity = S.fold; R.mRiser.material.transparent = S.fold < 1;
  kick();
}

// ONE FRAME.  Called by `draw()` with what it has just drawn on the SVG.
function frame(live, flying, f, stt) {
  LAST = { live: live, flying: flying, f: f || {}, stt: stt || { pos: {}, q: {} } };
  if (!R || !S.on) return;
  var list = ions(live, flying, LAST.f, LAST.stt), n = list.length, i, d, at = {}, up = lift();
  growIons(n);
  var nSpot = 0, nBeam = 0, gap = layerGap();
  for (i = 0; i < n; i++) {
    d = list[i];
    var p = place3(d.x, d.y, d.panel, d.zf, up, S.fold);
    at[d.ion] = p;
    var dim = (S.only >= 0 && Math.round(d.zf) !== S.only) ? 0.3 : 1;
    M4.compose(V3.set(p[0], p[1], p[2]), Q4.identity(), SC.set(d.r, d.r, d.r));
    R.mIon.setMatrixAt(i, M4);
    COL.set(d.colour || '#475467'); if (dim < 1) COL.lerp(new T.Color(0xf7f8fb), 0.7);
    R.mIon.setColorAt(i, COL);
    // its shadow, on the surface of the layer it is over; none while it is between two
    var sr = d.riser ? 0 : d.r * 0.92;
    M4.compose(V3.set(p[0], p[1], d.zf * gap * S.fold + 0.9), Q4.identity(), SC.set(sr, sr, 1));
    R.mShadow.setMatrixAt(i, M4); R.mShadow.setColorAt(i, COL.set('#101828'));
    if (d.beam) {
      // the spot under the ion and the beam onto it: "a laser is pointed here", not a ray
      // trace -- the SVG draws the same two marks, from up and left
      var rs = d.r * 2.05;
      M4.compose(V3.set(p[0], p[1], p[2] - d.r * 0.2), Q4.identity(), SC.set(rs, rs, 1));
      R.mSpot.setMatrixAt(nSpot, M4); R.mSpot.setColorAt(nSpot, COL.set(d.beam)); nSpot++;
      var far = [p[0] - rs * 2.0, p[1] + rs * 2.0, p[2] + rs * 4.2];
      rodBetween(R.mBeam, nBeam, far, p, Math.max(0.5, d.r * 0.2));
      R.mBeam.setColorAt(nBeam, COL.set(d.beam)); nBeam++;
    }
  }
  // one laser across two ions: tie the pair together, as the SVG does
  var nLink = 0, ops = (LAST.f.acts && LAST.f.acts.length) ? LAST.f.acts : (LAST.f.pairs || []);
  if (LAST.f.type === 'gate') {
    for (i = 0; i < ops.length; i++) {
      if (ops[i].length > 1 && at[ops[i][0]] && at[ops[i][1]] && nLink < R.ionCap) {
        rodBetween(R.mLink, nLink, at[ops[i][0]], at[ops[i][1]], Math.max(0.6, (L.r_ion || 1) * 0.3));
        R.mLink.setColorAt(nLink, COL.set(C.beam_two || '#c2308a')); nLink++;
      }
    }
  }
  // the sites in play
  var nHalo = 0, sites = LAST.f.sites || [];
  for (i = 0; i < sites.length && nHalo < R.ionCap; i++) {
    var sn = nodeById[sites[i]]; if (!sn) continue;
    var hp = place3(px(sn), py(sn), sn.z || 0, sn.z || 0, 1.2, S.fold);
    M4.compose(V3.set(hp[0], hp[1], hp[2]), Q4.identity(), SC.set(L.r_active || 1, L.r_active || 1, 1));
    R.mHalo.setMatrixAt(nHalo, M4); R.mHalo.setColorAt(nHalo, COL.set(C.active || '#f6c34a')); nHalo++;
  }
  R.mIon.count = n; R.mShadow.count = n; R.mSpot.count = nSpot; R.mBeam.count = nBeam;
  R.mLink.count = nLink; R.mHalo.count = nHalo;
  [R.mIon, R.mShadow, R.mSpot, R.mBeam, R.mLink, R.mHalo].forEach(function (m) {
    m.instanceMatrix.needsUpdate = true; m.instanceColor.needsUpdate = true;
  });
  R.ionList = list; R.ionAt = at;
  kick();
}

// --------------------------------------------------------------------- camera and loop
function resize() {
  if (!R) return;
  var w = R.host.clientWidth || 800, h = R.host.clientHeight || 500;
  R.renderer.setSize(w, h, false);
  R.camera.aspect = w / h; R.camera.updateProjectionMatrix();
  R.dirty = true;
}
// the stack's centre and the radius of a sphere that holds it
function extent() {
  var b = bounds(true), n = panels().length, top = (n - 1) * layerGap();
  var cx = (b.x0 + b.x1) / 2, cy = -(b.y0 + b.y1) / 2, cz = top / 2;
  var rx = (b.x1 - b.x0) / 2, ry = (b.y1 - b.y0) / 2, rz = top / 2 + 2 * (L.g || 1);
  return { c: [cx, cy, cz], r: Math.sqrt(rx * rx + ry * ry + rz * rz) + 1.5 * (L.g || 1) };
}
function fitDistance(radius) {
  var half = R.camera.fov * Math.PI / 360;
  var v = radius / Math.sin(half), h = radius / Math.sin(Math.atan(Math.tan(half) * R.camera.aspect));
  return Math.max(v, h) * 0.86;          // the sphere is a loose fit for a flat machine
}
var DIRS = { corner: [-0.42, -0.72, 0.55], top: [0, -0.0015, 1], front: [0, -1, 0.16] };
function pose(kind) {
  var e = extent(), d = DIRS[kind] || DIRS.corner, dist = fitDistance(e.r);
  var len = Math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2]);
  return { t: e.c, p: [e.c[0] + d[0] / len * dist, e.c[1] + d[1] / len * dist, e.c[2] + d[2] / len * dist] };
}
// the pose from which the flat scene looks exactly as the SVG does: straight down on the
// part of the picture the viewBox shows
function svgPose() {
  var vb = (globalThis.VIEW && VIEW.vb) ? VIEW.vb() : { x: 0, y: 0, w: L.W, h: L.H };
  var w = R.host.clientWidth || 800, h = R.host.clientHeight || 500;
  var scale = Math.min(w / vb.w, h / vb.h), seen = h / scale;          // `meet`: letterboxed
  var half = R.camera.fov * Math.PI / 360, dist = (seen / 2) / Math.tan(half);
  var t = [vb.x + vb.w / 2, -(vb.y + vb.h / 2), 0];
  return { t: t, p: [t[0], t[1] - dist * 0.0015, t[2] + dist] };
}
function setPose(po) {
  R.camera.position.set(po.p[0], po.p[1], po.p[2]);
  R.controls.target.set(po.t[0], po.t[1], po.t[2]);
  R.camera.near = Math.max(0.5, extent().r / 400); R.camera.far = extent().r * 60;
  R.camera.updateProjectionMatrix();
  R.controls.update(); R.dirty = true;
}
function mix(a, b, e) { return [a[0] + (b[0] - a[0]) * e, a[1] + (b[1] - a[1]) * e, a[2] + (b[2] - a[2]) * e]; }
function still() {
  try { return !!(globalThis.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches); }
  catch (e) { return false; }
}
// move the camera from where it is to a pose, and the layers from one fold to another
function animate(to, fold, ms, done) {
  var from = { p: [R.camera.position.x, R.camera.position.y, R.camera.position.z],
               t: [R.controls.target.x, R.controls.target.y, R.controls.target.z] };
  var f0 = S.fold;
  if (still() || !(ms > 0)) { S.fold = fold; layoutLayers(); refresh(); setPose(to); if (done) done(); kick(); return; }
  R.anim = { t0: performance.now(), ms: ms, step: function (x) {
    var e = x * x * (3 - 2 * x);
    S.fold = f0 + (fold - f0) * e; layoutLayers(); refresh();
    setPose({ p: mix(from.p, to.p, e), t: mix(from.t, to.t, e) });
  }, done: done };
  kick();
}
function refresh() { if (LAST) frame(LAST.live, LAST.flying, LAST.f, LAST.stt); }
function view(kind, smooth) {
  if (!R) return;
  if (smooth) animate(pose(kind), 1, 450); else { setPose(pose(kind)); kick(); }
}

function kick() {
  if (!R || R.raf) return;
  R.raf = requestAnimationFrame(tick);
}
function tick(now) {
  if (!R) return;
  R.raf = 0;
  var busy = false;
  if (R.anim) {
    var x = Math.min(1, (now - R.anim.t0) / R.anim.ms);
    R.anim.step(x); busy = true;
    if (x >= 1) { var d = R.anim.done; R.anim = null; if (d) d(); }
  }
  if (R && R.controls.update()) busy = true;            // true while damping still moves it
  if (!R) return;
  R.renderer.render(R.scene, R.camera);
  R.frames = (R.frames || 0) + 1;
  placeLabels();
  R.dirty = false;
  if (busy) kick();                                     // otherwise the loop stops here
}

function project(p) {
  V3.set(p[0], p[1], p[2]).project(R.camera);
  var w = R.host.clientWidth || 1, h = R.host.clientHeight || 1;
  return [(V3.x + 1) / 2 * w, (1 - V3.y) / 2 * h, V3.z];
}
function placeLabels() {
  var D = R.desc;
  for (var i = 0; i < R.labels.length; i++) {
    var tag = R.labels[i], lay = D.layers[tag._k];
    var g = R.groups[tag._k].position;
    var s = project([lay.x0 + g.x - 0.9 * (L.g || 1), -(lay.y1) + g.y - 0.9 * (L.g || 1), g.z]);
    tag.style.left = s[0] + 'px'; tag.style.top = s[1] + 'px';
    tag.style.opacity = String(Math.min(1, Math.max(0, S.fold * 1.4 - 0.2)) * ((S.only >= 0 && S.only !== tag._k) ? 0.35 : 1));
  }
}

// which ion is under the pointer
var RAY = T ? new T.Raycaster() : null, PT = T ? new T.Vector2() : null;
function tip(text, x, y) {
  var el = document.getElementById('s3tip'); if (!el) return;
  if (!text) { el.style.display = 'none'; return; }
  el.textContent = text; el.style.display = 'block'; el.style.left = (x + 14) + 'px'; el.style.top = (y + 12) + 'px';
}
function hover(e) {
  if (!R || !R.mIon || R.anim || R.mIon.count > 20000) return;
  var b = R.renderer.domElement.getBoundingClientRect();
  PT.set((e.clientX - b.left) / b.width * 2 - 1, -(e.clientY - b.top) / b.height * 2 + 1);
  RAY.setFromCamera(PT, R.camera);
  var hit = RAY.intersectObject(R.mIon, false)[0];
  if (hit && R.ionList && R.ionList[hit.instanceId]) {
    var d = R.ionList[hit.instanceId], where = LAST.stt.pos[d.ion];
    tip(d.ion + (d.riser ? ' · on a riser' : (d.fly ? ' · moving' : '')) + (where ? ' · ' + where : '') +
        (panels().length > 1 ? ' · layer ' + layerName(Math.round(d.zf)) : ''), e.clientX - b.left, e.clientY - b.top);
  } else tip(null);
}

// ------------------------------------------------------------------- the switch itself
function mark() {
  var c = document.getElementById('canvas'), b = document.getElementById('t3d'), h = document.getElementById('stage3d');
  if (c) { if (S.on) c.setAttribute('data-3d', '1'); else c.removeAttribute('data-3d'); }
  if (h) { if (S.on) h.setAttribute('data-on', '1'); else h.removeAttribute('data-on'); }
  if (b) { b.setAttribute('aria-pressed', S.on ? 'true' : 'false'); b.className = S.on ? 'tgl on' : 'tgl'; }
}
function on() {
  if (S.on) return true;
  if (!available() || !start()) { mark(); return false; }
  S.on = true; S.fold = 0; mark();
  resize(); layoutLayers();
  // the page drew its first frame before this script existed: ask it for one now, so the
  // ions are on the stage the moment it opens
  if (LAST) refresh(); else if (typeof draw === 'function') draw();
  // open on the SVG's own picture, then turn it into the stack: the reader sees which
  // panel became which layer
  setPose(svgPose());
  animate(pose('corner'), 1, 850);
  return true;
}
function off(now) {
  if (!S.on) return;
  var finish = function () { S.on = false; stop(); mark(); };
  if (now || !R) { finish(); return; }
  animate(svgPose(), 0, 420, finish);
}
function toggle() { if (S.on) off(false); else on(); return S.on; }

function wire() {
  if (typeof document === 'undefined' || !document.getElementById) return;
  var b = document.getElementById('t3d'), host = document.getElementById('stage3d');
  if (!b || !host) return;
  if (!available()) { b.disabled = true; b.title = 'the 3D stage cannot be shown: ' + S.why; return; }
  b.onclick = function () { if (!toggle() && !S.on && S.why) { b.disabled = true; b.title = 'the 3D stage cannot be shown: ' + S.why; } };
  var q = function (id) { return document.getElementById(id); };
  if (q('s3top')) q('s3top').onclick = function () { view('top', true); };
  if (q('s3corner')) q('s3corner').onclick = function () { view('corner', true); };
  if (q('s3front')) q('s3front').onclick = function () { view('front', true); };
  if (q('s3gap')) q('s3gap').oninput = function () { S.spacing = Number(this.value) || 1; layoutLayers(); refresh(); };
  var sel = q('s3only');
  if (sel) {
    var n = panels().length;
    if (n < 2) {
      // one layer: nothing to space and nothing to pick
      if (q('s3layers')) q('s3layers').style.display = 'none';
      if (q('s3gap') && q('s3gap').parentNode) q('s3gap').parentNode.style.display = 'none';
    } else {
      if (q('s3note')) q('s3note').textContent = 'Layer spacing is not to scale. ' + q('s3note').textContent;
      for (var k = 0; k < n; k++) { var o = document.createElement('option'); o.value = String(k); o.textContent = 'layer ' + layerName(k); sel.appendChild(o); }
      sel.onchange = function () { S.only = Number(this.value); layoutLayers(); refresh(); };
    }
  }
  if (q('s3png')) q('s3png').onclick = function () {
    if (!R) return;
    R.renderer.render(R.scene, R.camera);                 // the buffer is only valid this tick
    var a = document.createElement('a');
    a.download = ((typeof A !== 'undefined' && A.name) || 'machine') + '-3d.png';
    a.href = R.renderer.domElement.toDataURL('image/png'); a.click();
  };
}

globalThis.STAGE3D = {
  // the switch
  available: available, on: on, off: off, toggle: toggle, isOn: function () { return S.on; },
  why: function () { return S.why; },
  // what the page calls
  frame: frame, rebuild: function () { if (R) { buildStatic3(); kick(); } },
  view: view, wire: wire,
  // the pure half, for the tests and for anything that wants the scene as data
  describe: describe, ions: ions, positions: positions, place3: place3, panels: panels,
  // the frame `draw()` last handed over: {live, flying, f, stt}, or null before the first
  last: function () { return LAST; },
  layerGap: layerGap, lift: lift,
  state: function () { return { on: S.on, fold: S.fold, spacing: S.spacing, only: S.only,
                                context: !!R, frames: R ? (R.frames || 0) : 0,
                                calls: R ? R.renderer.info.render.calls : 0,
                                animating: !!(R && R.anim) }; },
  setFold: function (f) { S.fold = f; if (R) { layoutLayers(); refresh(); } },
  setSpacing: function (v) { S.spacing = v; if (R) { layoutLayers(); refresh(); } },
  setOnly: function (k) { S.only = k; if (R) { layoutLayers(); refresh(); } }
};
})();
