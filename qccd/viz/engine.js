// qccd/viz/engine.js -- THE CLIENT-SIDE ENGINE.  One file, no build step, no bundler.
//
// WHAT THIS IS
// ------------
// A mirror, in JavaScript, of the parts of the Python toolchain the page must be able to
// re-run *after the user edits the architecture in the browser*: the six geometry
// generators, `compute_layout`, the architecture command interpreter, the hardware
// recount and the re-pricer.  The user asked to edit the machine by dragging on the stage
// and by typing in a side editor, with the picture and the price both updating live and
// client-side.  There is no server and no Pyodide, so the engine has to be here.
//
// WHY THAT IS DANGEROUS, AND WHAT IS DONE ABOUT IT
// ------------------------------------------------
// Layout was deliberately moved OUT of JS into Python (`qccd/viz/layout.py`) so it could
// be tested.  Putting a copy back reintroduces two-implementations-of-one-truth, and this
// codebase has already been bitten hard by exactly that: a JS operand renderer and a
// Python one disagreed, the program filter searched text the user could not see, and
// 3,830 of 3,830 rows came out wrong.
//
// So this file does not ship without `tests/test_engine_parity.py`, which runs BOTH
// implementations over the same multi-thousand-device corpus and diffs every scalar at
// TOLERANCE ZERO -- not 1e-9, zero.  That bar is reachable because `layout.py` was first
// rewritten to use only operations IEEE-754 specifies identically in CPython and V8
// (`+ - * /`, `sqrt`, `min`, `max`, `floor`, `ceil`, comparisons, a total-order sort); see
// the "portable arithmetic" block there and `_hyp` / `_fsum` / `_q` below, which are its
// exact counterparts.  Any epsilon in that test would hide a defect rather than absorb
// noise; needing one is itself the alarm that a non-portable idiom crept back.
//
// THE SAME BYTES SHIP AND ARE TESTED
// ----------------------------------
// `qccd/viz/render.py` inlines this file verbatim at `__ENGINE__`, and `tests/parity.mjs`
// loads it with `new Function(src)()` -- the idiom `tests/census.mjs` already uses on the
// whole page.  `test_engine_parity.py::test_the_page_inlines_this_exact_engine` asserts
// the file's bytes appear byte-for-byte in every emitted page, so "two copies" is
// structurally impossible rather than conventionally avoided.
//
// COST, HONESTLY
// --------------
// It adds tens of KB to a ~760 KB page and replaces a 539-byte `layout` blob.  This is not
// a byte saving.  It buys interactivity: a full re-generate plus re-layout of the shipped
// ring is well under a millisecond in JS against ~21 ms in CPython, so the same algorithm
// on the far side of an IPC would blow a 60 fps frame budget on five of the nine shipped
// devices before the first byte moved.
//
// It must also keep passing `render.py`'s FORBIDDEN scan, which rejects anything that
// could make the page reach the network -- network calls, external script or stylesheet
// references, remote images, CSS imports -- ANYWHERE in the emitted file, including inside
// a comment.  That is deliberate: a scanner that tried to tell a mention from a use would
// be a parser.  So this file names none of those tokens, and page emission fails at build
// time if one appears.  See `FORBIDDEN` in render.py for the list.

'use strict';

var QCCD = (function () {
'use strict';

// =====================================================================================
// 0. PORTABLE ARITHMETIC -- the exact counterparts of layout.py's `_hyp`/`_fsum`/`_q`
// =====================================================================================
//
// Each of these replaces an idiom whose natural JS translation is WRONG.  All three were
// measured against CPython, not reasoned about.

var _P10 = [];
for (var _i10 = 0; _i10 < 13; _i10++) _P10.push(Math.pow(10, _i10));

// `sqrt(dx*dx+dy*dy)`, never `Math.hypot`.  CPython's `math.hypot` and V8's `Math.hypot`
// are both under 1 ulp but miss in OPPOSITE directions, so `Math.hypot` is a strictly
// worse mirror of `math.hypot` than this is.  `*`, `+` and `sqrt` are correctly rounded
// and identically specified by IEEE-754 in both runtimes, so this form is reproducible bit
// for bit.
function _hyp(dx, dy) { return Math.sqrt(dx * dx + dy * dy); }

// Left-to-right accumulation.  CPython >= 3.12 gives the builtin `sum()` Neumaier
// compensation, which NO JS `for` loop reproduces: on a 160-node ring the `_bows` centroid
// came out 128000.0 in Python against 127999.99999999999 here, the collinear tie-break saw
// -0.0 on one side and -1.14e-13 on the other, and one spur bowed INTO the loop it exists
// to route around.  A sign flip, not an ulp.  layout.py uses the same explicit loop.
function _fsum(vals) {
  var a = 0.0;
  for (var i = 0; i < vals.length; i++) a += vals[i];
  return a;
}

// `floor(v*10^nd + 0.5)/10^nd`.  Python's `round()` is half-to-EVEN on the exact binary
// value; `Number(v.toFixed(nd))` is half-away-from-zero and `Math.round` is half-up and
// also disagrees on negatives (`round(-1.5) == -2`, `Math.round(-1.5) == -1`).  Ties are
// reachable and occurred unprompted: `round(24.28125, 4)` is 24.2812 against a toFixed of
// "24.2813", on `sx`, `sy` and `g`.  layout.py quantizes with this same form.
function _q(v, nd) {
  if (nd === undefined || nd === null) return Math.floor(v + 0.5);
  var s = _P10[nd];
  return Math.floor(v * s + 0.5) / s;
}

// Coordinates outside this are refused on ingest, in BOTH languages.  `_hyp` overflows
// above ~1e154 where `hypot` would not; real device coordinates are O(1..1e3), but a drag
// editor accepts whatever a text field contains, and refusing at a boundary nine orders
// clear of the failure is better than returning Infinity from a layout.
var COORD_MAX = 1e6;

function EngineError(msg) {
  var e = Error.call(this, msg);
  this.message = msg; this.name = 'EngineError';
  if (Error.captureStackTrace) Error.captureStackTrace(this, EngineError);
  else this.stack = e.stack;
}
EngineError.prototype = Object.create(Error.prototype);
EngineError.prototype.constructor = EngineError;

function ExpansionError(msg) { EngineError.call(this, msg); this.name = 'ExpansionError'; }
ExpansionError.prototype = Object.create(EngineError.prototype);
ExpansionError.prototype.constructor = ExpansionError;

function EditError(msg, code, method, index) {
  EngineError.call(this, msg);
  this.name = 'EditError'; this.code = code || 'error';
  this.method = method || null; this.index = index === undefined ? null : index;
}
EditError.prototype = Object.create(EngineError.prototype);
EditError.prototype.constructor = EditError;

function _coord(v, what) {
  var f = Number(v);
  if (!isFinite(f)) throw new EngineError(what + ' is not a finite number: ' + v);
  if (Math.abs(f) > COORD_MAX) {
    throw new EngineError(
      what + ' = ' + f + ' is outside +/-1e+06; the layout measures distances as ' +
      'sqrt(dx*dx+dy*dy) so it can be reproduced exactly in the browser, and that form ' +
      'is only exact well inside this range');
  }
  return f;
}

// A total order over [x, y] pairs matching Python's tuple comparison.  `Array.sort()` with
// no comparator is LEXICOGRAPHIC ON STRINGS -- `[0,10,5].sort()` is `[0,10,5]` -- which is
// the single most common way a numeric port goes silently wrong.  Both sorts are stable
// (ES2019+ and CPython), so equal pairs keep declaration order identically.
function _cmpPt(p, q) {
  return p[0] < q[0] ? -1 : p[0] > q[0] ? 1 : p[1] < q[1] ? -1 : p[1] > q[1] ? 1 : 0;
}
function _cmpNum(a, b) { return a < b ? -1 : a > b ? 1 : 0; }
function _cmpStr(a, b) { return a < b ? -1 : a > b ? 1 : 0; }

// =====================================================================================
// 1. pyRepr -- Python's repr() for a number, in JS
// =====================================================================================
//
// The side editor round-trips SOURCE TEXT, and `String(v)` disagrees with `repr(v)` on
// about a tenth of all doubles -- never in the digits, always in the framing.  If this is
// skipped or half-applied, the listing's own emit -> exec -> structural-diff round trip
// reports phantom edits and the user cannot tell a formatting artefact from a real change.
//
//   1.0    -> "1.0"     an integral FLOAT keeps its .0
//   -0.0   -> "-0.0"    String(-0) is "0"
//   1e-7   -> "1e-07"   signed, at least two exponent digits
//   1e-5   -> "1e-05"   Python switches to exponent at decpt <= -4 or > 16;
//   1e16   -> "1e+16"   JS switches at 1e-6 / 1e21
//
// Built on `toExponential()`, which the spec guarantees is the shortest round-tripping
// form -- the same digits CPython's repr picks.
//
// JS has ONE number type and Python has two, and the schema cares (`capacity`, `degree`
// and the degree-curve keys are `{"type": "integer"}`).  So a value that must print as a
// float carries a tag: `PyFloat` boxes it, `pyRepr` unboxes it.  Everything that came out
// of a `.arch.json` float field is boxed at ingest, which is what keeps `length=1.0` from
// re-emitting as `length=1`.
function PyFloat(v) { this.v = Number(v); }
PyFloat.prototype.valueOf = function () { return this.v; };
PyFloat.prototype.toJSON = function () { return this.v; };
function isPyFloat(v) { return v instanceof PyFloat; }
function unbox(v) { return v instanceof PyFloat ? v.v : v; }

function pyRepr(v) {
  if (v instanceof PyFloat) return _pyFloatRepr(v.v);
  if (v === null || v === undefined) return 'None';
  if (typeof v === 'boolean') return v ? 'True' : 'False';
  if (typeof v === 'string') return _pyStrRepr(v);
  if (typeof v === 'number') {
    if (v !== v) return 'nan';
    if (!isFinite(v)) return v > 0 ? 'inf' : '-inf';
    if (Number.isInteger(v) && !Object.is(v, -0)) return String(v);
    return _pyFloatRepr(v);
  }
  if (Array.isArray(v)) return '[' + v.map(pyRepr).join(', ') + ']';
  if (v instanceof Map) {
    var ps = [];
    v.forEach(function (val, k) { ps.push(pyRepr(k) + ': ' + pyRepr(val)); });
    return '{' + ps.join(', ') + '}';
  }
  if (typeof v === 'object') {
    var qs = [];
    for (var k in v) if (Object.prototype.hasOwnProperty.call(v, k)) qs.push(pyRepr(k) + ': ' + pyRepr(v[k]));
    return '{' + qs.join(', ') + '}';
  }
  return String(v);
}

// Python's `repr()` for a string: SINGLE quotes by default, double when the string
// contains a single quote and no double.  This is the form that appears inside Python
// ERROR MESSAGES, and the parity harness diffs error text character for character -- a
// mirror that refuses the same things for differently-worded reasons is a mirror the user
// cannot trust to explain itself.  It is deliberately NOT the same function as `lit`,
// which is the listing emitter's `_lit` and json-dumps a string in DOUBLE quotes.
function _pyStrRepr(s) {
  var q = (s.indexOf("'") >= 0 && s.indexOf('"') < 0) ? '"' : "'";
  var out = q;
  for (var i = 0; i < s.length; i++) {
    var c = s[i], code = s.charCodeAt(i);
    if (c === q || c === '\\') out += '\\' + c;
    else if (c === '\n') out += '\\n';
    else if (c === '\r') out += '\\r';
    else if (c === '\t') out += '\\t';
    else if (code < 0x20 || code === 0x7f) out += '\\x' + ('0' + code.toString(16)).slice(-2);
    else out += c;
  }
  return out + q;
}

// `json.dumps(s, ensure_ascii=False)` -- the listing emitter's `_lit` for strings.  Always
// double quotes on output even though the parser accepts single, which is what lets the
// render(parse(x)) === x byte comparison hold.  The corpus really contains section signs
// and en dashes, so non-ASCII passes through unescaped.
function _pyStr(s) {
  var out = '"';
  for (var i = 0; i < s.length; i++) {
    var c = s[i], code = s.charCodeAt(i);
    if (c === '"') out += '\\"';
    else if (c === '\\') out += '\\\\';
    else if (c === '\n') out += '\\n';
    else if (c === '\r') out += '\\r';
    else if (c === '\t') out += '\\t';
    // `json.dumps` has short forms for these two and the `code < 0x20` fallback below
    // does not, so without them the writer emitted `"A<U+0008>B"` where Python writes
    // `"A\bB"` -- a mismatch on every string carrying a backspace or a form feed.
    else if (c === '\b') out += '\\b';
    else if (c === '\f') out += '\\f';
    else if (code < 0x20) out += '\\u' + ('000' + code.toString(16)).slice(-4);
    else out += c;
  }
  return out + '"';
}

function _pyFloatRepr(x) {
  if (x !== x) return 'nan';
  if (!isFinite(x)) return x > 0 ? 'inf' : '-inf';
  if (Object.is(x, -0)) return '-0.0';
  if (x === 0) return '0.0';
  var neg = x < 0, a = Math.abs(x);
  var es = a.toExponential();
  var m = /^(\d)(?:\.(\d+))?e([+-]\d+)$/.exec(es);
  var digits = m[1] + (m[2] || '');
  var decpt = parseInt(m[3], 10) + 1;   // CPython's decimal point position
  var out;
  if (decpt <= -4 || decpt > 16) {
    // repr(1e-7) is '1e-07', NOT '1.0e-07': a single significant digit carries no
    // fractional part at all in the exponential branch, unlike the fixed one.
    var frac = digits.length > 1 ? '.' + digits.slice(1) : '';
    var e = decpt - 1, sign = e < 0 ? '-' : '+', mag = String(Math.abs(e));
    out = digits[0] + frac + 'e' + sign + (mag.length < 2 ? '0' + mag : mag);
  } else if (decpt <= 0) {
    out = '0.' + _rep('0', -decpt) + digits;
  } else if (decpt >= digits.length) {
    out = digits + _rep('0', decpt - digits.length) + '.0';
  } else {
    out = digits.slice(0, decpt) + '.' + digits.slice(decpt);
  }
  return neg ? '-' + out : out;
}
function _rep(s, n) { var o = ''; for (var i = 0; i < n; i++) o += s; return o; }

// =====================================================================================
// 2. THE LAYOUT MIRROR -- qccd/viz/layout.py, function for function
// =====================================================================================

var W_MAX = 1600.0, W_MIN = 280.0, H_MAX = 900.0, H_MIN = 132.0;
var PAD_A = 2.2, PAD_B = 10.0;
var PITCH_CAP = 72.0, K_ANISO = 12.0, ISO_ASPECT = 2.0;
// theme.GEOMETRY, the one home for the deck's measured shape ratios.  These are ALSO
// shipped in the page payload as `D.layout_consts`, and the parity test asserts the two
// agree, so a constant changed in Python and forgotten here fails BY NAME rather than
// looking like an algorithm difference at the far end of a fixed point.
var GEOM = { ION_D_FRAC: 0.13, ION_D_FRAC_ACTIVE: 0.175,
             RAIL_W_FRAC: 0.083, RUNG_W_FRAC: 0.065 };
var K_ION = GEOM.ION_D_FRAC_ACTIVE + 0.065;
var K_REST = GEOM.ION_D_FRAC;
var R_ION_MAX = 26.0, R_ION_MIN = 3.0;
var _EPS = 1e-9;

// Exact minimum nearest-neighbour distance: x-sweep with the standard early break.
// Coincident points are SKIPPED rather than returning 0 -- two nodes at one position is a
// device-authoring question, and returning 0 would collapse every derived size.  Which is
// also why the editor refuses a drop onto another node outright: `g` would silently get
// measured off the NEXT pair and `2*r_ion < g` would stop meaning what it says.
function minNearestNeighbour(pts) {
  var n = pts.length;
  if (n < 2) return 0.0;
  var order = pts.slice().sort(_cmpPt);
  var best = Infinity;
  for (var a = 0; a < n; a++) {
    var xi = order[a][0], yi = order[a][1];
    for (var b = a + 1; b < n; b++) {
      var dx = order[b][0] - xi;
      if (dx >= best) break;
      var d = _hyp(dx, order[b][1] - yi);
      if (_EPS < d && d < best) best = d;
    }
  }
  return isFinite(best) ? best : 0.0;
}

function _latticeStep(vals) {
  var set = new Set();
  for (var i = 0; i < vals.length; i++) set.add(_q(vals[i], 9));
  var uniq = Array.from(set).sort(_cmpNum);
  if (uniq.length < 2) return 0.0;
  var m = Infinity;
  for (var j = 0; j + 1 < uniq.length; j++) {
    var d = uniq[j + 1] - uniq[j];
    if (d < m) m = d;
  }
  return m;
}

// Above the cap `g` is an APPROXIMATION, and it is only reproducible because the sample is
// a strided slice in NODE DECLARATION ORDER on both sides.  Verified bit-exact at 6,001 /
// 9,000 / 20,000 nodes; keep a >6,000-node case in the parity corpus permanently, because
// any future change to how `render.py` orders `nodes` breaks it silently for large devices
// only.
function _sample(pts, cap) {
  cap = cap === undefined ? 6000 : cap;
  if (pts.length <= cap) return pts;
  var stride = pts.length / cap, out = [];
  for (var i = 0; i < cap; i++) out.push(pts[Math.trunc(i * stride)]);
  return out;
}

function _fit(dx, dy, ux, uy, pad, iso) {
  var availW = Math.max(W_MAX - 2 * pad, 80.0);
  var availH = Math.max(H_MAX - 2 * pad, 80.0);
  var sx = dx > _EPS ? availW / dx : Infinity;
  var sy = dy > _EPS ? availH / dy : Infinity;
  if (ux > _EPS) sx = Math.min(sx, PITCH_CAP / ux);
  if (uy > _EPS) sy = Math.min(sy, PITCH_CAP / uy);
  if (!isFinite(sx) && !isFinite(sy)) { sx = PITCH_CAP; sy = PITCH_CAP; }
  else if (!isFinite(sx)) { sx = sy; }
  else if (!isFinite(sy)) { sy = sx; }
  if (iso) { var mn = Math.min(sx, sy); sx = mn; sy = mn; }
  else {
    // These two lines are order-INDEPENDENT, which is worth stating because it looks like
    // they would not be: once one clamp binds, the clamped value is K_ANISO times the
    // other, so K_ANISO times IT is K_ANISO^2 times the other and the second clamp can
    // never bind.  Checked over 200,000 random (sx, sy) pairs: zero disagreements.  The
    // parity harness's mutation set therefore does NOT include swapping them -- a mutation
    // that provably changes nothing would be a mutation guard that proves nothing.
    sy = Math.min(sy, K_ANISO * sx);
    sx = Math.min(sx, K_ANISO * sy);
  }
  return [sx, sy];
}

// `int(cap or 0)` in Python -> Math.trunc, which truncates toward zero.  Math.floor would
// differ on a negative capacity, which a side editor can produce.  The PAGE used to
// compute this inline with no truncation at all, and `pad_tiling` was never mirrored at
// all -- the page tiled pads at 0.34*g while layout.pad_tiling derived k from 0.50*g.
// Both are in here now precisely so the parity diff covers the parts that had drifted
// rather than certifying the parts that were already fine.
function siteLength(cap, g) {
  var m = Math.max(1, Math.min(Math.trunc(cap || 0), 6));
  return Math.min(0.88 * g, (0.30 + 0.15 * m) * g);
}

function padTiling(length, g) {
  if (length <= _EPS || g <= _EPS) return [0, 0.0, 0.0];
  var k = Math.max(1, Math.min(12, Math.floor(length / (0.50 * g) + 0.5)));
  var pitch = length / k;
  return [k, pitch, 0.72 * pitch];
}

function _pointSegment(p, a, b) {
  var dx = b[0] - a[0], dy = b[1] - a[1];
  var l2 = dx * dx + dy * dy;
  if (l2 < 1e-18) return [_hyp(p[0] - a[0], p[1] - a[1]), 0.0];
  var t = Math.max(0.0, Math.min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2));
  var qx = a[0] + t * dx, qy = a[1] + t * dy;
  var h = Math.sqrt(l2);
  var nx = -dy / h, ny = dx / h;
  return [_hyp(p[0] - qx, p[1] - qy), (p[0] - a[0]) * nx + (p[1] - a[1]) * ny];
}

// Segments that would otherwise be drawn straight THROUGH a node they do not touch.  This
// is the least-exercised surface in the whole layout -- it returns {} for seven of the
// nine shipped devices -- and every divergence worse than 2 ulp the parity harness found
// was in here.  The fuzz and drag buckets of the corpus are what cover it; do not let
// anyone trim the corpus to "the devices we ship".
function _bows(nodes, segments, pos, posOrder, g, pad, clearance, need) {
  if (g <= _EPS || posOrder.length === 0) return {};
  var xs = [], ys = [], i;
  for (i = 0; i < posOrder.length; i++) { xs.push(posOrder[i][0]); ys.push(posOrder[i][1]); }
  var cx = _fsum(xs) / posOrder.length;
  var cy = _fsum(ys) / posOrder.length;
  var amount = Math.min(0.9 * g, Math.max(need, pad - clearance));
  var out = {};
  for (var si = 0; si < segments.length; si++) {
    var s = segments[si], sid = s.id;
    var a = pos.get(s.a), b = pos.get(s.b);
    if (sid === undefined || sid === null || a === undefined || b === undefined) continue;
    if (_hyp(b[0] - a[0], b[1] - a[1]) <= _EPS) continue;
    var worst = null;
    for (var ni = 0; ni < nodes.length; ni++) {
      var n = nodes[ni];
      if (n.id === s.a || n.id === s.b) continue;
      var r = _pointSegment(pos.get(n.id), a, b);
      // strict `<`: the winner is the FIRST strict minimum in node DECLARATION order,
      // exactly as Python's `worst is None or d < worst[0]`.  Comparing sets, or sorting
      // the candidates, would pass a device that then lays out differently.
      if (r[0] < 0.55 * g && (worst === null || r[0] < worst[0])) worst = r;
    }
    if (worst === null) continue;
    var sign;
    if (Math.abs(worst[1]) > 1e-6) {
      sign = worst[1] > 0 ? -1.0 : 1.0;
    } else {
      var h = _hyp(b[0] - a[0], b[1] - a[1]);
      var nx = -(b[1] - a[1]) / h, ny = (b[0] - a[0]) / h;
      var mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
      sign = ((mx - cx) * nx + (my - cy) * ny) >= 0 ? 1.0 : -1.0;
    }
    out[String(sid)] = _q(sign * amount, 3);
  }
  return out;
}

// `compute_layout(nodes, segments)` -- 39 scalars plus `bows`.  `{raw: true}` skips the
// output quantizer; the parity test diffs BOTH modes, because quantization masked 3,539 of
// 3,569 real divergences in the measured run and a round-only harness would have shrugged
// at 99% of the drift.
function computeLayout(nodes, segments, opts) {
  segments = segments || [];
  var raw = !!(opts && opts.raw);
  var R = raw ? function (v) { return v; } : _q;
  var pts = [], i;
  for (i = 0; i < nodes.length; i++) {
    var nd = nodes[i];
    var lbl = nd.id === undefined ? '?' : nd.id;
    pts.push([_coord(nd.x, 'node ' + lbl + ' x'), _coord(nd.y, 'node ' + lbl + ' y')]);
  }
  if (pts.length === 0) pts = [[0.0, 0.0]];
  var x0 = pts[0][0], x1 = pts[0][0], y0 = pts[0][1], y1 = pts[0][1];
  for (i = 1; i < pts.length; i++) {
    if (pts[i][0] < x0) x0 = pts[i][0];
    if (pts[i][0] > x1) x1 = pts[i][0];
    if (pts[i][1] < y0) y0 = pts[i][1];
    if (pts[i][1] > y1) y1 = pts[i][1];
  }
  var dx = x1 - x0, dy = y1 - y0;
  var xsOnly = [], ysOnly = [];
  for (i = 0; i < pts.length; i++) { xsOnly.push(pts[i][0]); ysOnly.push(pts[i][1]); }
  var ux = _latticeStep(xsOnly), uy = _latticeStep(ysOnly);
  var sample = _sample(pts);
  var gd = minNearestNeighbour(sample);
  if (gd <= _EPS) gd = Math.max(dx, dy) || 1.0;

  var axisAligned = true;
  var byId = new Map();
  for (i = 0; i < nodes.length; i++) byId.set(nodes[i].id, [Number(nodes[i].x), Number(nodes[i].y)]);
  for (i = 0; i < segments.length; i++) {
    var pa = byId.get(segments[i].a), pb = byId.get(segments[i].b);
    if (pa === undefined || pb === undefined) continue;
    if (Math.abs(pa[0] - pb[0]) > _EPS && Math.abs(pa[1] - pb[1]) > _EPS) {
      axisAligned = false; break;
    }
  }
  // `1/ISO_ASPECT <= dx/dy <= ISO_ASPECT` written out.  A chained comparison in JS parses
  // as `(0.5 <= r) <= 2`, a boolean coerced to 0/1, which is ALWAYS TRUE.  Silent.
  // Python short-circuits before evaluating `dx/dy`, so the guards come first here too.
  var ratio = dx / dy;
  var iso = (!axisAligned) || dy <= _EPS || dx <= _EPS ||
            ((1.0 / ISO_ASPECT) <= ratio && ratio <= ISO_ASPECT);

  var pad = 24.0, sx = 0.0, sy = 0.0, g = 0.0, f;
  // FIVE passes then one final refit.  Do NOT add a convergence early-exit: it is five
  // passes plus a refit, and any early break changes `pad`.
  for (var it = 0; it < 5; it++) {
    f = _fit(dx, dy, ux, uy, pad, iso); sx = f[0]; sy = f[1];
    g = minNearestNeighbour(_scale(sample, sx, sy));
    if (g <= _EPS) g = gd * Math.max(sx, sy);
    var r0 = Math.min(R_ION_MAX, Math.max(Math.min(R_ION_MIN, 0.45 * g), K_ION * g));
    var r1 = Math.min(r0, Math.max(Math.min(1.6, 0.30 * g), K_REST * g));
    var bowNeed = r0 + r1 + 3.0;
    var padNeed = Math.max(0.30 * g + 0.10 * g, r0) + bowNeed;
    pad = Math.ceil(Math.max(PAD_A * r0 + PAD_B, padNeed));
  }
  f = _fit(dx, dy, ux, uy, pad, iso); sx = f[0]; sy = f[1];
  g = minNearestNeighbour(_scale(sample, sx, sy));
  if (g <= _EPS) g = gd * Math.max(sx, sy);

  var W = Math.min(W_MAX, Math.max(W_MIN, _q(dx * sx + 2 * pad)));
  var H = Math.min(H_MAX, Math.max(H_MIN, _q(dy * sy + 2 * pad)));
  var ox = (W - dx * sx) / 2.0 - x0 * sx;
  var oy = (H - dy * sy) / 2.0 - y0 * sy;

  var rIon = Math.min(R_ION_MAX, Math.max(Math.min(R_ION_MIN, 0.45 * g), K_ION * g));
  var rRest = Math.min(rIon, Math.max(Math.min(1.6, 0.30 * g), K_REST * g));
  var siteT = GEOM.RAIL_W_FRAC * 2.4 * g;
  // `pos` is a Map, never a plain object: JS object keys that look like integers reorder
  // to ascending numeric, and `pos.values()` order is the centroid's summation order and
  // the `_bows` tie-break.
  var pos = new Map(), posOrder = [], seen = [];
  for (i = 0; i < nodes.length; i++) {
    var nn = nodes[i];
    var p = [ox + Number(nn.x) * sx, oy + Number(nn.y) * sy];
    if (pos.has(nn.id)) { posOrder[seen.indexOf(nn.id)] = p; }
    else { seen.push(nn.id); posOrder.push(p); }
    pos.set(nn.id, p);
  }

  return {
    sx: R(sx, 4), sy: R(sy, 4),
    ox: R(ox, 4), oy: R(oy, 4),
    x0: x0, y0: y0, x1: x1, y1: y1,
    W: Math.trunc(W), H: Math.trunc(H), pad: Math.trunc(pad),
    wide: !!(H > 0 && W / H >= 3.0),
    g: R(g, 4), gd: R(gd, 6),
    ux: R(ux, 6), uy: R(uy, 6),
    iso: !!iso, axis_aligned: !!axisAligned,
    n: nodes.length,
    r_ion: R(rIon, 3),
    r_rest: R(rRest, 3),
    swap_bow: R(Math.min(0.62 * g, 1.9 * (rIon + rRest)), 3),
    r_junc: R(0.30 * g, 3),
    r_active: R(0.46 * g, 3),
    site_t: R(siteT, 3),
    site_max: R(0.88 * g, 3),
    slot_r: R(0.085 * g, 3),
    sw_rail: R(Math.max(1.2, GEOM.RAIL_W_FRAC * g), 3),
    sw_thin: R(Math.max(1.0, GEOM.RUNG_W_FRAC * g), 3),
    sw_halo: R(Math.max(0.7, 0.055 * g), 3),
    sw_node: R(Math.max(1.0, 0.05 * g), 3),
    sw_loop: R(Math.max(3.0, 0.44 * g), 3),
    well_rx: R(0.44 * g, 3),
    well_ry: R(0.30 * g, 3),
    pad_pitch: R(0.34 * g, 3),
    pad_t: R(0.20 * g, 3),
    pad_off: R(0.30 * g, 3),
    labels: !!(rIon >= 11.0),
    bows: _bows(nodes, segments, pos, posOrder, g, pad,
                Math.max(rIon, 0.30 * g + 0.10 * g) + 2.0,
                rIon + rRest + 3.0)
  };
}

function _scale(pts, sx, sy) {
  var out = [];
  for (var i = 0; i < pts.length; i++) out.push([pts[i][0] * sx, pts[i][1] * sy]);
  return out;
}

// The drawing-shape adapter: a wire device (id-keyed maps) -> the [{id,x,y}] /
// [{id,a,b}] arrays `compute_layout` takes.  Order is preserved because order is what
// `_bows` and `_sample` depend on.
function layoutOf(dev, opts) {
  var nodes = [], segments = [];
  for (var nid in dev.nodes) if (own(dev.nodes, nid)) {
    var n = dev.nodes[nid];
    nodes.push({ id: nid, x: Number(unbox(n.pos[0])), y: Number(unbox(n.pos[1])) });
  }
  for (var sid in dev.segments) if (own(dev.segments, sid)) {
    var s = dev.segments[sid];
    segments.push({ id: sid, a: s.a, b: s.b });
  }
  return computeLayout(nodes, segments, opts);
}
function own(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }

// =====================================================================================
// 3. THE GENERATORS -- qccd/arch/generators.py
// =====================================================================================
//
// Six functions, ~200 lines of pure integer arithmetic, producing the SAME wire shape
// `qccd/arch/edit.py::device_to_wire` produces -- one wire format in the whole project,
// so nothing needs an adapter.
//
// ORDER IS PART OF THE ANSWER, not an implementation detail.  `Device.to_json` emits
// dict-insertion order, the exported `.arch.json` is byte-compared, and `_bows`'s centroid
// is an order-dependent float sum whose winner is "first strict minimum in declaration
// order".  Comparing sets would pass a device that then lays out differently, so the
// parity test diffs the ORDERED node / segment / loop lists field by field.
//
// Errors carry Python's exact message text, so the editor's error strip says what
// `python -m qccd` would say.

function _node(id, x, y, kind, zone, cap, labels) {
  // plain numbers, not PyFloat boxes: `pos` is `tuple(float)` in Python and `length` is a
  // float, so there is no ambiguity to carry, and a box here would travel into edit.js's
  // reports and serialize as {"v": ...}.  The tag lives only where the ambiguity is real.
  return { id: id, pos: [Number(x), Number(y)], kind: kind || 'site',
           cap: cap || 0, zone: zone === undefined ? null : zone,
           labels: labels || [], capacity_explicit: false };
}
function _seg(id, a, b, length, cap, loop, labels) {
  return { id: id, a: a, b: b, length: Number(length === undefined ? 1.0 : length),
           cap: cap === undefined ? 1 : cap, loop: loop === undefined ? null : loop,
           labels: labels || [] };
}
function _loop(id, nodes, closed, kind, note) {
  return { id: id, nodes: nodes, closed: !!closed, kind: kind,
           note: note === undefined ? null : note };
}
function _dev(nodes, segments, loops, generator, params) {
  return { nodes: nodes, segments: segments, loops: loops,
           generator: generator, params: params };
}

// `isinstance(True, int)` is True in Python but `typeof true === 'boolean'` in JS, so
// `ladder(w, rungs=True)` takes the INT branch in Python and would take the array branch
// here.  Obscure, but reachable from a side editor where the user typed `True`.
function _isPyInt(v) {
  if (typeof v === 'boolean') return true;
  return typeof v === 'number' && Number.isInteger(v);
}
function _asInt(v) { return typeof v === 'boolean' ? (v ? 1 : 0) : Math.trunc(Number(v)); }

function _ringSlots(width, height) {
  if (width < 2 || height < 2) throw new ExpansionError('ring needs width >= 2 and height >= 2');
  var slots = [], x, y;
  for (x = 0; x < width; x++) slots.push(['top', x, 0.0]);
  for (y = 1; y < height - 1; y++) slots.push(['right', width - 1, y]);
  for (x = width - 1; x >= 0; x--) slots.push(['bottom', x, height - 1]);
  for (y = height - 2; y > 0; y--) slots.push(['left', 0.0, y]);
  return slots;
}

function ring(width, height, verticals, kw) {
  height = height === undefined || height === null ? 2 : height;
  verticals = verticals === undefined || verticals === null ? 0 : verticals;
  kw = kw || {};
  var siteZone = kw.site_zone === undefined ? 'data' : kw.site_zone;
  var ancillaZone = kw.ancilla_zone === undefined ? 'ancilla' : kw.ancilla_zone;
  var segCap = kw.segment_capacity === undefined ? 1 : kw.segment_capacity;
  var loopId = kw.loop_id === undefined ? 'L0' : kw.loop_id;

  var slots = _ringSlots(width, height);
  var capacity = slots.length;
  if (verticals < 0) throw new ExpansionError('verticals must be >= 0');
  if (verticals && capacity % verticals) {
    throw new ExpansionError(
      verticals + ' verticals do not divide ' + capacity + ' slots evenly; ' +
      'the deck spaces docks uniformly around the loop');
  }
  var spacing = verticals ? Math.floor(capacity / verticals) : 0;
  var dockSlots = new Set();
  for (var i = 0; i < verticals; i++) dockSlots.add(i * spacing);

  var midX = (width - 1) / 2.0, midY = (height - 1) / 2.0;
  var nodes = {}, segments = {}, s;
  for (s = 0; s < slots.length; s++) {
    var labels = [slots[s][0]];
    if (dockSlots.has(s)) labels.push('dock');
    nodes['S' + s] = _node('S' + s, slots[s][1], slots[s][2], 'site', siteZone, 0, labels);
  }
  for (s = 0; s < capacity; s++) {
    segments['E' + s] = _seg('E' + s, 'S' + s, 'S' + ((s + 1) % capacity), 1.0, segCap,
                             loopId, ['rail']);
  }
  var docks = Array.from(dockSlots).sort(_cmpNum);
  for (var di = 0; di < docks.length; di++) {
    s = docks[di];
    var side = slots[s][0], x = slots[s][1], y = slots[s][2];
    var ax = (side === 'top' || side === 'bottom') ? x : midX;
    var ay = (side === 'top' || side === 'bottom') ? midY : y;
    nodes['A' + s] = _node('A' + s, ax, ay, 'site', ancillaZone, 0, ['ancilla']);
    segments['V' + s] = _seg('V' + s, 'S' + s, 'A' + s,
                             Math.abs(ax - x) + Math.abs(ay - y), segCap, null, ['spur']);
  }
  var seq = [];
  for (s = 0; s < capacity; s++) seq.push('S' + s);
  var loops = {};
  loops[loopId] = _loop(loopId, seq, true, 'ring',
    'the rigid-rotation orbit; one movement template shifts every ion on it');
  return _dev(nodes, segments, loops, 'ring', {
    width: width, height: height, verticals: verticals, site_zone: siteZone,
    ancilla_zone: ancillaZone, segment_capacity: segCap, loop_id: loopId });
}

function grid(a, b, kw) {
  kw = kw || {};
  var siteZone = kw.site_zone === undefined ? 'trap' : kw.site_zone;
  var segCap = kw.segment_capacity === undefined ? 1 : kw.segment_capacity;
  if (a < 2 || b < 2) throw new ExpansionError('grid needs a >= 2 and b >= 2');
  var nodes = {}, segments = {}, i, j;
  for (i = 0; i < a; i++) for (j = 0; j < b; j++) {
    nodes['J' + i + '_' + j] = _node('J' + i + '_' + j, i, j, 'junction', undefined, 0,
                                     ['lattice']);
  }
  function addTrap(tid, x, y, u, v) {
    nodes[tid] = _node(tid, x, y, 'site', siteZone, 0, ['trap']);
    segments[tid + '.a'] = _seg(tid + '.a', u, tid, 0.5, segCap, null, []);
    segments[tid + '.b'] = _seg(tid + '.b', tid, v, 0.5, segCap, null, []);
  }
  for (i = 0; i < a - 1; i++) for (j = 0; j < b; j++) {
    addTrap('T' + i + '_' + j + 'h', i + 0.5, j, 'J' + i + '_' + j, 'J' + (i + 1) + '_' + j);
  }
  for (i = 0; i < a; i++) for (j = 0; j < b - 1; j++) {
    addTrap('T' + i + '_' + j + 'v', i, j + 0.5, 'J' + i + '_' + j, 'J' + i + '_' + (j + 1));
  }
  return _dev(nodes, segments, {}, 'grid',
              { a: a, b: b, site_zone: siteZone, segment_capacity: segCap });
}

function chain(n, kw) {
  kw = kw || {};
  var siteZone = kw.site_zone === undefined ? 'trap' : kw.site_zone;
  var segCap = kw.segment_capacity === undefined ? 1 : kw.segment_capacity;
  var pathId = kw.path_id === undefined ? 'P0' : kw.path_id;
  if (n < 1) throw new ExpansionError('chain needs n >= 1');
  var nodes = {}, segments = {}, seq = [], i;
  for (i = 0; i < n; i++) {
    nodes['C' + i] = _node('C' + i, i, 0.0, 'site', siteZone, 0, ['trap']);
    seq.push('C' + i);
  }
  for (i = 0; i < n - 1; i++) {
    segments['E' + i] = _seg('E' + i, 'C' + i, 'C' + (i + 1), 1.0, segCap,
                             n > 1 ? pathId : null, ['rail']);
  }
  var loops = {};
  if (n > 1) {
    loops[pathId] = _loop(pathId, seq, false, 'path',
      'open register: no rigid rotation, so no single movement template');
  }
  return _dev(nodes, segments, loops, 'chain',
              { n: n, site_zone: siteZone, segment_capacity: segCap, path_id: pathId });
}

function ladder(width, rungs, highways, kw) {
  rungs = rungs === undefined ? null : rungs;
  highways = highways === undefined || highways === null ? 0 : highways;
  kw = kw || {};
  var siteZone = kw.site_zone === undefined ? 'data' : kw.site_zone;
  var highwayZone = kw.highway_zone === undefined ? 'trap' : kw.highway_zone;
  var segCap = kw.segment_capacity === undefined ? 1 : kw.segment_capacity;
  if (width < 2) throw new ExpansionError('ladder needs width >= 2');
  if (highways !== 0 && highways !== 1 && highways !== 2) {
    throw new ExpansionError('ladder supports 0, 1 or 2 highways');
  }
  var rungX = [], x;
  if (rungs === null) { for (x = 0; x < width; x++) rungX.push(x); }
  else if (_isPyInt(rungs)) {
    var step = _asInt(rungs);
    if (step < 1) throw new ExpansionError('rung spacing must be >= 1');
    for (x = 0; x < width; x += step) rungX.push(x);
  } else {
    var st = new Set();
    for (var ri = 0; ri < rungs.length; ri++) st.add(Math.trunc(Number(rungs[ri])));
    rungX = Array.from(st).sort(_cmpNum);
  }
  for (var k = 0; k < rungX.length; k++) {
    if (!(0 <= rungX[k] && rungX[k] < width)) {
      throw new ExpansionError('rung position ' + rungX[k] + ' is outside the ladder');
    }
  }
  var nodes = {}, segments = {}, loops = {}, i;
  // nodes INTERLEAVED T{x}, B{x} per x; segments interleaved ET{x}, EB{x} per x
  for (x = 0; x < width; x++) {
    nodes['T' + x] = _node('T' + x, x, 1.0, 'site', siteZone, 0, ['top', 'rail']);
    nodes['B' + x] = _node('B' + x, x, 0.0, 'site', siteZone, 0, ['bottom', 'rail']);
  }
  for (x = 0; x < width - 1; x++) {
    segments['ET' + x] = _seg('ET' + x, 'T' + x, 'T' + (x + 1), 1.0, segCap, 'TOP', ['rail']);
    segments['EB' + x] = _seg('EB' + x, 'B' + x, 'B' + (x + 1), 1.0, segCap, 'BOTTOM', ['rail']);
  }
  for (i = 0; i < rungX.length; i++) {
    x = rungX[i];
    segments['R' + x] = _seg('R' + x, 'B' + x, 'T' + x, 1.0, segCap, null, ['rung', 'compute']);
  }
  var tSeq = [], bSeq = [];
  for (x = 0; x < width; x++) { tSeq.push('T' + x); bSeq.push('B' + x); }
  loops.TOP = _loop('TOP', tSeq, false, 'path', 'upper rail');
  loops.BOTTOM = _loop('BOTTOM', bSeq, false, 'path', 'lower rail');

  var lanes = [];
  if (highways >= 1) lanes.push(['HT', 2.0, 'T', 'top']);
  if (highways >= 2) lanes.push(['HB', -1.0, 'B', 'bottom']);
  for (var li = 0; li < lanes.length; li++) {
    var prefix = lanes[li][0], y = lanes[li][1], rail = lanes[li][2], side = lanes[li][3];
    var hSeq = [];
    for (x = 0; x < width; x++) {
      nodes[prefix + x] = _node(prefix + x, x, y, 'site', highwayZone, 0, ['highway', side]);
      hSeq.push(prefix + x);
    }
    for (x = 0; x < width - 1; x++) {
      // `loop: prefix` -- the field a first port dropped, which the ordered structural
      // diff caught on 150 of 1500 fuzz cases.  A set comparison would have passed it.
      segments['E' + prefix + x] = _seg('E' + prefix + x, prefix + x, prefix + (x + 1),
                                        1.0, segCap, prefix, ['highway']);
    }
    for (i = 0; i < rungX.length; i++) {
      x = rungX[i];
      segments['L' + prefix + x] = _seg('L' + prefix + x, rail + x, prefix + x, 1.0,
                                        segCap, null, ['onramp']);
    }
    loops[prefix] = _loop(prefix, hSeq, false, 'path', side + ' shuttling highway');
  }
  return _dev(nodes, segments, loops, 'ladder',
              { width: width, rungs: rungX, highways: highways, site_zone: siteZone,
                highway_zone: highwayZone, segment_capacity: segCap });
}

function racetrack(straight, kw) {
  kw = kw || {};
  var siteZone = kw.site_zone === undefined ? 'trap' : kw.site_zone;
  var segCap = kw.segment_capacity === undefined ? 1 : kw.segment_capacity;
  var loopId = kw.loop_id === undefined ? 'L0' : kw.loop_id;
  var dev = ring(straight, 2, 0,
                 { site_zone: siteZone, segment_capacity: segCap, loop_id: loopId });
  return _dev(dev.nodes, dev.segments, dev.loops, 'racetrack',
              { straight: straight, site_zone: siteZone, segment_capacity: segCap,
                loop_id: loopId });
}

function dualLoop(width, couplings, kw) {
  couplings = couplings === undefined ? null : couplings;
  kw = kw || {};
  var dataZone = kw.data_zone === undefined ? 'data' : kw.data_zone;
  var ancillaZone = kw.ancilla_zone === undefined ? 'ancilla' : kw.ancilla_zone;
  var segCap = kw.segment_capacity === undefined ? 1 : kw.segment_capacity;
  if (width < 2) throw new ExpansionError('dual_loop needs width >= 2');
  var couplingX = [], x;
  if (couplings === null) couplingX = [];
  else if (_isPyInt(couplings)) {
    var step = _asInt(couplings);
    if (step) { for (x = 0; x < width; x += step) couplingX.push(x); }
  } else {
    var st = new Set();
    for (var ci = 0; ci < couplings.length; ci++) st.add(Math.trunc(Number(couplings[ci])));
    couplingX = Array.from(st).sort(_cmpNum);
  }
  var nodes = {}, segments = {}, loops = {};
  var plan = [['D', dataZone, 1.0, 2.0, 'data', 'inner'],
              ['A', ancillaZone, 0.0, 3.0, 'ancilla', 'outer']];
  for (var pi = 0; pi < plan.length; pi++) {
    var tag = plan[pi][0], zone = plan[pi][1], yTop = plan[pi][2], yBot = plan[pi][3];
    var label = plan[pi][4], side = plan[pi][5];
    var order = [];
    for (x = 0; x < width; x++) {
      nodes[tag + 'T' + x] = _node(tag + 'T' + x, x, yTop, 'site', zone, 0,
                                   [label, side, 'top']);
      order.push(tag + 'T' + x);
    }
    for (x = width - 1; x >= 0; x--) {
      nodes[tag + 'B' + x] = _node(tag + 'B' + x, x, yBot, 'site', zone, 0,
                                   [label, side, 'bottom']);
      order.push(tag + 'B' + x);
    }
    for (var i = 0; i < order.length; i++) {
      var j = (i + 1) % order.length;
      segments['E' + tag + i] = _seg('E' + tag + i, order[i], order[j], 1.0, segCap,
                                     tag, ['rail']);
    }
    loops[tag] = _loop(tag, order, true, 'ring', label + ' loop');
  }
  for (var k = 0; k < couplingX.length; k++) {
    x = couplingX[k];
    segments['C' + x] = _seg('C' + x, 'DT' + x, 'AT' + x, 1.0, segCap, null, ['coupling']);
  }
  return _dev(nodes, segments, loops, 'dual_loop',
              { width: width, couplings: couplingX, data_zone: dataZone,
                ancilla_zone: ancillaZone, segment_capacity: segCap });
}

// `_gen_defaults` in Python uses `inspect.signature` -- pure reflection.  Re-declaring it
// in JS would be a mirror with nothing to check it against, so the DEFAULTS are shipped as
// data in the page payload and only the positional shape lives here.
var GENERATORS = {
  ring: function (p) { return ring(p.width, p.height, p.verticals, p); },
  grid: function (p) { return grid(p.a, p.b, p); },
  chain: function (p) { return chain(p.n, p); },
  ladder: function (p) { return ladder(p.width, p.rungs, p.highways, p); },
  racetrack: function (p) { return racetrack(p.straight, p); },
  dual_loop: function (p) { return dualLoop(p.width, p.couplings, p); }
};

var _GEN_REQUIRED = { ring: ['width'], grid: ['a', 'b'], chain: ['n'],
                      ladder: ['width'], racetrack: ['straight'], dual_loop: ['width'] };
var _GEN_KNOWN = {
  ring: ['width', 'height', 'verticals', 'site_zone', 'ancilla_zone', 'segment_capacity', 'loop_id'],
  grid: ['a', 'b', 'site_zone', 'segment_capacity'],
  chain: ['n', 'site_zone', 'segment_capacity', 'path_id'],
  ladder: ['width', 'rungs', 'highways', 'site_zone', 'highway_zone', 'segment_capacity'],
  racetrack: ['straight', 'site_zone', 'segment_capacity', 'loop_id'],
  dual_loop: ['width', 'couplings', 'data_zone', 'ancilla_zone', 'segment_capacity']
};

function generatorNames() { return Object.keys(GENERATORS).sort(_cmpStr); }

// `expand_generator(name, params, zone_types)`.  Refuses an unknown generator by name and
// an undeclared zone, with Python's exact wording -- a browser that silently accepted what
// the toolchain rejects would price an architecture that cannot be built, which is the
// worst failure available here.
function expandGenerator(name, params, zoneTypes) {
  if (name === 'explicit') {
    throw new ExpansionError(
      "geometry.generator is 'explicit' but the document carries no `nodes`");
  }
  var fn = GENERATORS[name];
  if (!fn) {
    throw new ExpansionError('unknown generator ' + pyRepr(name) +
                             ' (have: ' + generatorNames().join(', ') + ')');
  }
  var kwargs = {};
  for (var k in params) if (own(params, k)) kwargs[k] = unbox(params[k]);
  if (zoneTypes) {
    var zk = ['site_zone', 'ancilla_zone'];
    for (var i = 0; i < zk.length; i++) {
      if (own(kwargs, zk[i]) && !own(zoneTypes, kwargs[zk[i]])) {
        throw new ExpansionError('geometry.params.' + zk[i] + ' = ' +
                                 pyRepr(kwargs[zk[i]]) + ' is not a declared zone type');
      }
    }
  }
  // Python raises `TypeError` inside the generator and `expand_generator` re-wraps it as
  // `generator 'ring': ...`; mirror the two reachable cases -- an unknown keyword and a
  // missing required argument.
  var known = _GEN_KNOWN[name], req = _GEN_REQUIRED[name], kk;
  for (kk in kwargs) if (own(kwargs, kk) && known.indexOf(kk) < 0) {
    throw new ExpansionError('generator ' + pyRepr(name) + ': ' + name +
                             '() got an unexpected keyword argument ' + pyRepr(kk));
  }
  for (var r = 0; r < req.length; r++) {
    if (!own(kwargs, req[r])) {
      throw new ExpansionError('generator ' + pyRepr(name) + ': ' + name +
                               "() missing 1 required positional argument: " +
                               pyRepr(req[r]));
    }
  }
  return fn(kwargs);
}

// =====================================================================================
// 4. GRAPH ARITHMETIC AND THE HARDWARE RECOUNT
// =====================================================================================
//
// Integer arithmetic throughout, so parity here is exact equality with no float question
// at all.  This is the part that runs on every pointermove during a drag: 3 ms on the
// shipped ring, which is what makes the DAC / junction / degree-histogram contrast live
// rather than a thing you get after letting go.

function degrees(dev) {
  var deg = new Map(), nid;
  for (nid in dev.nodes) if (own(dev.nodes, nid)) deg.set(nid, 0);
  for (var sid in dev.segments) if (own(dev.segments, sid)) {
    var s = dev.segments[sid];
    if (!deg.has(s.a)) throw new ExpansionError('segment ' + pyRepr(sid) + ' names unknown node ' + pyRepr(s.a));
    if (!deg.has(s.b)) throw new ExpansionError('segment ' + pyRepr(sid) + ' names unknown node ' + pyRepr(s.b));
    deg.set(s.a, deg.get(s.a) + 1);
    deg.set(s.b, deg.get(s.b) + 1);
  }
  return deg;
}

function junctionNodes(dev) {
  var deg = degrees(dev), out = [];
  deg.forEach(function (d, nid) { if (d >= 3) out.push(nid); });
  return out.sort(_cmpStr);
}

function degreeHistogram(dev) {
  var deg = degrees(dev), hist = {};
  deg.forEach(function (d) { hist[d] = (hist[d] || 0) + 1; });
  return hist;
}

function totalCapacity(dev) {
  var t = 0;
  for (var nid in dev.nodes) if (own(dev.nodes, nid)) t += Math.trunc(dev.nodes[nid].cap || 0);
  return t;
}

// `build_control_plane` materialises one ChannelGroup per channel -- a direct plane on 168
// sites is 5,376 objects.  The page must not build them, so this counts the groups the
// same construction WOULD produce, arithmetic term for arithmetic term, including the
// `add()` guard that skips an empty member set.
function _channelGroups(dev, control) {
  var spec = (control && control.channels) || {};
  if (!_nonEmpty(spec)) return null;
  var grouping = String(spec.grouping === undefined ? 'direct' : spec.grouping);
  var sites = [];
  for (var nid in dev.nodes) if (own(dev.nodes, nid) && dev.nodes[nid].kind === 'site') sites.push(nid);
  var rawRoles = spec.roles === undefined ? ['linear'] : spec.roles;
  var roleCounts = {};
  if (Array.isArray(rawRoles)) {
    var per = Math.trunc(Number(spec.channels_per_role === undefined ? 1 : spec.channels_per_role));
    for (var i = 0; i < rawRoles.length; i++) roleCounts[String(rawRoles[i])] = per;
  } else {
    for (var rk in rawRoles) if (own(rawRoles, rk)) roleCounts[String(rk)] = Math.trunc(Number(rawRoles[rk]));
  }
  var pair = Math.trunc(Number(spec.differential === undefined ? 1 : spec.differential));
  var roles = Object.keys(roleCounts);
  var groups = 0, r;

  function add(role, nMembers) {
    if (!nMembers) return;              // `if not members: return` -- an empty set adds none
    groups += (roleCounts[role] === undefined ? 1 : roleCounts[role]) * pair;
  }
  if (grouping === 'broadcast') {
    for (r = 0; r < roles.length; r++) add(roles[r], sites.length);
  } else if (grouping === 'direct') {
    for (r = 0; r < roles.length; r++) for (var s = 0; s < sites.length; s++) add(roles[r], 1);
  } else if (grouping === 'row' || grouping === 'column' || grouping === 'row_column') {
    var axes = grouping === 'row' ? [1] : grouping === 'column' ? [0] : [0, 1];
    for (var ai = 0; ai < axes.length; ai++) {
      var buckets = new Map();
      for (var si = 0; si < sites.length; si++) {
        // `round(pos[axis], 6)` in Python is half-to-even; use the portable quantizer, and
        // note the two agree on every coordinate a device actually carries.
        var key = _q(Number(unbox(dev.nodes[sites[si]].pos[axes[ai]])), 6);
        buckets.set(key, (buckets.get(key) || 0) + 1);
      }
      for (r = 0; r < roles.length; r++) {
        buckets.forEach(function (count) { add(roles[r], count); });
      }
    }
  } else if (grouping === 'explicit') {
    var ex = spec.explicit || [];
    groups = ex.length;
  } else {
    throw new EngineError('unknown channel grouping ' + pyRepr(grouping) +
                          '; have: broadcast, direct, row, column, row_column, explicit');
  }
  return { grouping: grouping, groups: groups, n_sites: sites.length };
}

function _nonEmpty(o) {
  if (!o) return false;
  for (var k in o) if (own(o, k)) return true;
  return false;
}

// `hardware_report(arch)`.  Counts only -- the prose `notes` stay Python-side, because a
// mirrored sentence is a mirror with no oracle.
//
// NOTE THE TWO DEFINITIONS OF "JUNCTION" INSIDE ONE PYTHON FUNCTION, faithfully mirrored:
// the reported `n_junctions` and the `max_junctions` budget check use `junction_nodes`
// (degree >= 3, which is 24 on the shipped ring), while the electrode count comes from
// `ControlPlane.n_junctions`, which is `kind == "junction"` (0 on that same ring).  A
// mirror that picks one is wrong on the other, and a human reviewer nods straight past it;
// the numeric diff is what caught it.
function hardwareReport(dev, control, budget, name) {
  control = control || {}; budget = budget || {};
  var wiring = control.wiring || {};
  var scheme = String(wiring.scheme === undefined ? 'direct' : wiring.scheme);
  var perTrap = _int(wiring.electrodes_per_trap, 24);
  var perJunction = _int(wiring.electrodes_per_junction, 48);
  var shimPerDac = _int(wiring.shim_per_dac, 1) || 1;
  var compPerCell = _int(wiring.compensation_electrodes_per_trap, 8);

  var traps = [], kindJunctions = [], nid;
  for (nid in dev.nodes) if (own(dev.nodes, nid)) {
    if (dev.nodes[nid].kind === 'site') traps.push(nid); else kindJunctions.push(nid);
  }
  var junctionIds = junctionNodes(dev);                  // degree >= 3
  var eTrap = traps.length * perTrap;
  var eJunction = junctionIds.length * perJunction;
  var electrodes = eTrap + eJunction;
  var switches = electrodes * 2;

  var plane = _channelGroups(dev, control);
  var broadcast, compDacs, dacs;
  if (plane) {
    // the DECLARED plane: electrodes and switches come from the plane's own fields, where
    // `n_junctions` means kind == "junction"
    var planeWiring = {
      electrodes_per_site: _int(wiring.electrodes_per_trap, 0),
      electrodes_per_junction: _int(wiring.electrodes_per_junction, 0),
      compensation_per_site: _int(wiring.compensation_electrodes_per_trap, 0),
      demux: _int(wiring.shim_per_dac, 1) || 1,
      switch_per_site: (control.channels && control.channels.switch_per_site !== undefined)
        ? !!control.channels.switch_per_site : true
    };
    broadcast = plane.groups;
    var totalComp = plane.n_sites * planeWiring.compensation_per_site;
    compDacs = planeWiring.demux ? Math.ceil(totalComp / planeWiring.demux) : totalComp;
    dacs = broadcast + compDacs;
    electrodes = plane.n_sites * (planeWiring.electrodes_per_site + planeWiring.compensation_per_site)
               + kindJunctions.length * planeWiring.electrodes_per_junction;
    switches = planeWiring.switch_per_site ? electrodes * 2 : 0;
  } else if (scheme === 'wise' || scheme === 'broadcast_groups') {
    broadcast = _int(wiring.dacs_dynamic, 0);
    var compensation = traps.length * compPerCell;
    compDacs = shimPerDac ? Math.ceil(compensation / shimPerDac) : compensation;
    dacs = broadcast + compDacs;
  } else {
    broadcast = 0;
    compDacs = electrodes;
    dacs = electrodes;
  }

  var over = [];
  if (own(budget, 'max_dacs') && dacs > Math.trunc(Number(budget.max_dacs))) {
    over.push('dacs ' + dacs + ' > max_dacs ' + _pyNum(budget.max_dacs));
  }
  if (own(budget, 'max_junctions') && junctionIds.length > Math.trunc(Number(budget.max_junctions))) {
    over.push('junctions ' + junctionIds.length + ' > max_junctions ' + _pyNum(budget.max_junctions));
  }
  var hist = degreeHistogram(dev), sortedHist = {};
  Object.keys(hist).map(Number).sort(_cmpNum).forEach(function (d) { sortedHist[d] = hist[d]; });
  return {
    name: name === undefined ? '' : name,
    scheme: scheme,
    n_traps: traps.length,
    n_junctions: junctionIds.length,
    n_segments: Object.keys(dev.segments).length,
    trapping_zones: traps.length,
    total_capacity: totalCapacity(dev),
    degree_histogram: sortedHist,
    electrodes: electrodes,
    switches: switches,
    dacs: dacs,
    dacs_broadcast: broadcast,
    dacs_compensation: compDacs,
    dacs_per_trap: traps.length ? dacs / traps.length : 0.0,
    over_budget: over
  };
}

function _int(v, dflt) {
  if (v === undefined || v === null) return dflt;
  return Math.trunc(Number(unbox(v)));
}
// budget values print in the message exactly as Python's f-string would
function _pyNum(v) { var u = unbox(v); return Number.isInteger(u) ? String(u) : String(u); }

// =====================================================================================
// 5. THE COMMAND INTERPRETER -- the twelve methods `architecture_listing` emits
// =====================================================================================
//
// The architecture IS ALREADY a structured command list.  `ArchLine.call` carries
// `{method, args, kwargs}` and a stable `target`, and every shipped architecture is
// exactly twelve distinct methods: blank, describe, set_zone, set_control, declare_class,
// set_wiring, set_curve, set_degree_curve, set_primitive, set_heating, set_species,
// set_budget.  Three more arrive the moment anything is edited: move_site,
// set_site_capacity, set_segment_length.  Plus the explicit-geometry builder verbs.
//
// So the interpreter is a WHITELIST DISPATCHER, not an evaluator.  `apply` takes a record
// and mutates a state; there is no `eval`, and nothing in the grammar can name a property.
// The Python side (`qccd/arch/edit.py::apply_call`) is not a re-implementation of
// `Machine` -- it dispatches INTO `Machine` -- so only this half can drift, and the parity
// test replays every shipped listing through both and diffs the resulting documents.
//
// THE VOCABULARY IS COMPUTED, NOT LISTED.  `test_engine_parity.py` builds the expected set
// from `{line.call.method for arch in ALL_ARCHS for line in listing(arch).lines}` and
// compares it to `methods()`.  Add a thirteenth method to `listing.py` and that test goes
// red NAMING the method, before any numeric diff runs.

// THE VOCABULARY IS THE DISPATCHER.  It used to be three hand-written arrays beside a
// fourth hand-written object (`CALLS`), and they disagreed: `SEED_METHODS` advertised
// `from_template`, `CALLS` implemented neither it nor `from_device`, and `renderStmt`
// carried a THIRD list that omitted `from_template` and so rendered it as a mutate.
// `methods()` feeds the "(have: ...)" text of BOTH refusal messages, so the engine
// refused a verb using a list that named it.  Deriving the advertised set from the
// dispatched set makes "advertised implies dispatchable" true by construction rather
// than by a test -- and re-hard-coding the list is invisible to every differential
// bucket, so construction is the only place it can be enforced.
var KIND = {
  blank: 'seed', blank_device: 'seed', from_device: 'seed', from_template: 'seed',
  DeviceBuilder: 'build', 'd.site': 'build', 'd.junction': 'build',
  'd.segment': 'build', 'd.loop': 'build'
};
function _kindOf(method) { return own(KIND, method) ? KIND[method] : 'mutate'; }
function _methodsOfKind(kind) {
  var out = [];
  for (var m in CALLS) if (own(CALLS, m) && _kindOf(m) === kind) out.push(m);
  return out.sort(_cmpStr);
}
function methods() { return Object.keys(CALLS).sort(_cmpStr); }

// The template registry: `{stem: [call records]}`, handed over by the page (`D.templates`)
// or the parity harness.  Same contract as `setSchema`: the engine keeps no copy of its
// own, so a template can never drift from the `.arch.json` it was read out of.
var TEMPLATES = null;
// What an un-named `template=` resolves to.  NOT a literal: `qccd/api.py` owns the value
// (`DEFAULT_TEMPLATE`, used by `_template_doc`) and ships it in the page's data blob, the
// same way the schema and the generator defaults travel.  A stem written down here would
// be one more hand-copied constant, which is the shape of bug this file keeps deleting.
var TEMPLATE_DEFAULT = null;

function setTemplates(map, dflt) {
  if (map === null || map === undefined) { TEMPLATES = null; TEMPLATE_DEFAULT = null; return; }
  if (typeof map !== 'object') {
    throw new EngineError('setTemplates needs {stem: [call records]} as produced by ' +
                          'qccd/arch/listing.py template_records()');
  }
  TEMPLATES = map;
  TEMPLATE_DEFAULT = dflt === undefined || dflt === null ? null : String(dflt);
}
function templateNames() { return TEMPLATES ? Object.keys(TEMPLATES).sort(_cmpStr) : []; }
function templateDefault() { return TEMPLATE_DEFAULT; }

// `str(template or DEFAULT_TEMPLATE)`, with the default supplied rather than remembered.
function _templateKey(v) {
  if (v !== undefined && v !== null && String(v) !== '') return String(v);
  if (TEMPLATE_DEFAULT === null) {
    throw new EditError(
      'this page was emitted without a default template, so an un-named `template=` ' +
      'cannot be resolved; name one of: ' + (templateNames().join(', ') || '(none)'),
      'unknown_template', 'from_template');
  }
  return TEMPLATE_DEFAULT;
}

// Replay a template's declaring records onto a fresh state.  A template the page does not
// carry is refused as a fact about the DATA -- naming what IS registered -- never as a
// fact about the verb, which is what `'from_template' is not an editable method` used to
// claim while `methods()` listed it.
function _applyTemplate(key, name) {
  if (!TEMPLATES || !own(TEMPLATES, key)) {
    throw new EditError(
      'unknown template ' + pyRepr(key) + ' (this page carries: ' +
      (templateNames().join(', ') || '(none)') + ')', 'unknown_template', 'from_template');
  }
  var st = _blankState(name); st.seeded = true;
  var recs = TEMPLATES[key] || [];
  for (var i = 0; i < recs.length; i++) st = applyCall(st, recs[i]);
  st.name = name;
  st.description = null;
  st.provenance = {};
  return st;
}

function _blankState(name) {
  return { seeded: false,
           name: name === undefined ? 'custom' : name, description: null, provenance: {},
           device: null, zone_types: {},
           primitives: { curves: {}, degree_curves: {}, scalars: {} },
           control: {}, heating: {}, species: {}, budget: {},
           builder: null };
}

function _zoneBlock(zones) {
  var out = {};
  if (!zones) return out;
  if (Array.isArray(zones)) {
    for (var i = 0; i < zones.length; i++) out[String(zones[i])] = { capacity: 1 };
  } else {
    for (var k in zones) if (own(zones, k)) out[String(k)] = _shallow(zones[k]);
  }
  return out;
}
function _shallow(o) {
  var d = {};
  for (var k in o) if (own(o, k)) d[k] = o[k];
  return d;
}
function _isPlainObject(v) {
  // must match Python's `isinstance(v, Mapping)`: TRUE for object literals, FALSE for
  // arrays.  An array assigned over an array REPLACES, it does not concatenate.
  return v !== null && typeof v === 'object' && !Array.isArray(v) && !(v instanceof PyFloat);
}

// `resolve_capacities` (device.py).  Note `if cap and cap != node.capacity`: a zone
// capacity of ZERO is silently skipped, so `set_zone(z, capacity=0)` leaves every site on
// its old capacity and the document still saves clean.  That is faithfully mirrored, and
// separately LINTED, because nothing in R1-R18 notices it.
function resolveCapacities(st) {
  if (!st.device) return st;
  for (var nid in st.device.nodes) if (own(st.device.nodes, nid)) {
    var n = st.device.nodes[nid];
    if (n.kind !== 'site' || n.zone === null || n.zone === undefined) continue;
    if (n.capacity_explicit) continue;
    var z = st.zone_types[n.zone];
    var cap = Math.trunc(Number((z && z.capacity) || 0));
    if (cap && cap !== n.cap) n.cap = cap;
  }
  return st;
}

function _requireDevice(st, method) {
  if (!st.device) {
    throw new EditError('no geometry yet: ' + method + ' needs a Machine.blank(...) first',
                        'no_device', method);
  }
}

function _curvePoints(points, table, source) {
  var pts = [];
  for (var i = 0; i < points.length; i++) {
    var p = points[i], rec;
    if (Array.isArray(p)) {
      rec = { us: new PyFloat(_finite(p[0], 'a curve point us', 'set_curve')),
              quanta: new PyFloat(_finite(p[1], 'a curve point quanta', 'set_curve')),
              table: table, source: source };
    } else {
      // `{"table": table, "source": source, **dict(p)}` -- the point's OWN table/source
      // override the call-level defaults.  Python coerces us/quanta to float in the tuple
      // branch only; `api.py` was fixed to coerce in both, because JS has no int/float
      // distinction and could not otherwise reproduce `5` vs `5.0` in the exported JSON.
      rec = { table: table, source: source };
      for (var k in p) if (own(p, k)) rec[k] = p[k];
      rec.us = new PyFloat(_finite(rec.us, 'a curve point us', 'set_curve'));
      rec.quanta = new PyFloat(_finite(rec.quanta, 'a curve point quanta', 'set_curve'));
    }
    pts.push(rec);
  }
  return pts;
}


// THE SEAL GUARD.  `Machine.blank_device` / `Machine.from_device` hand the device to
// `Architecture.from_json`, which runs, IN THIS ORDER: the dangling-reference guard the
// constructor applies before it serializes; check(doc) against ARCH_SCHEMA;
// Device.from_json; resolve_capacities; check_structure; the undeclared-zone check.
// Nothing in this file ran ANY of that, so every device Python refuses -- 403 of 403 in
// the build corpus -- was built here without complaint, priced, drawn and exported into a
// file Python would not load.  Both mirrors already shipped (`QCCD.validateDocument`,
// `QCCDEdit.checkStructure`); this is the call site they never had.
//
// The ORDER is the contract, not a detail: a device with both a bad id and a dangling
// endpoint must be refused by the same one of the two on both sides, or the error index
// and the message disagree while the verdict agrees -- which is exactly the shape of
// agreement a verdict-only comparison waves through.
function _sealDevice(st, name) {
  var dev = st.device, nid, sid, lid, i;
  if (!dev || !dev.nodes) {
    throw new EditError("geometry.generator is 'explicit' but the document carries no `nodes`",
                        'ExpansionError', 'blank_device');
  }
  // 1. dangling references -- `Device.to_json` computes `all_corners`, which walks every
  //    loop and reads `nodes[nxt].pos`, so this is what `_geometry_of` refuses first.
  var dangling = [];
  for (sid in dev.segments) if (own(dev.segments, sid)) {
    var sg = dev.segments[sid];
    if (!(sg.a in dev.nodes)) dangling.push("segment '" + sid + "': unknown endpoint '" + sg.a + "'");
    if (!(sg.b in dev.nodes)) dangling.push("segment '" + sid + "': unknown endpoint '" + sg.b + "'");
  }
  for (lid in dev.loops) if (own(dev.loops, lid)) {
    var lp = dev.loops[lid];
    for (i = 0; i < lp.nodes.length; i++) {
      if (!(lp.nodes[i] in dev.nodes)) {
        dangling.push("loop '" + lid + "': unknown node '" + lp.nodes[i] + "'");
      }
    }
  }
  if (dangling.length) {
    throw new EditError(dangling.length + " structural error(s) in '" + name + "':\n  " +
                        dangling.join('\n  '), 'ExpansionError', 'blank_device');
  }
  // 2. the schema, which is where an empty node list, a bad id and an out-of-range
  //    capacity are all caught -- `check(doc)` is the first thing `from_json` does.
  var doc = serialize(st);
  var v = validateDocument(doc, SCHEMA);
  if (v.length) {
    throw new EditError(v.length + ' schema error(s):\n  ' + v.join('\n  '),
                        'ValidationError', 'blank_device');
  }
  // 3. `check_structure` plus the undeclared-zone check `Architecture.from_json` runs
  //    beside it.  One list, one count, one message -- exactly as Python assembles it.
  var errs = globalThis.QCCDEdit.checkStructure(dev);
  var zs = [], have = [];
  for (var z in st.zone_types) if (own(st.zone_types, z)) have.push(z);
  have.sort(_cmpStr);
  for (nid in dev.nodes) if (own(dev.nodes, nid)) {
    var nz = dev.nodes[nid].zone;
    if (nz !== null && nz !== undefined && !own(st.zone_types, nz)) {
      zs.push("node '" + nid + "': no zone type '" + nz + "' is declared (have: " +
              (have.join(', ') || 'none') + ")");
    }
  }
  errs = errs.concat(zs);
  if (errs.length) {
    throw new EditError(errs.length + " structural error(s) in '" + name + "':\n  " +
                        errs.join('\n  '), 'ExpansionError', 'blank_device');
  }
  return st;
}

var CALLS = {

  // -- seeds ------------------------------------------------------------------------
  blank: function (st, args, kw) {
    var generator = args[0];
    if (generator === undefined) {
      // The defect this interpreter was built on: `listing.py` emitted the geometry
      // statement with `args: []`, so the generator name survived only in `target`, and
      // replaying the STRUCTURED form raised TypeError on 9 of 9 shipped architectures.
      // The listing now puts it in `args`; refusing loudly here is what keeps it there.
      throw new EditError(
        "Machine.blank() needs the generator name as its first positional argument; " +
        "the listing record must carry it in `args`, not only in `target`",
        'missing_generator', 'blank');
    }
    var params = {}, name = 'custom', zones = [];
    for (var k in kw) if (own(kw, k)) {
      if (k === 'name') name = String(kw[k]);
      else if (k === 'zones') zones = kw[k];
      else params[k] = kw[k];
    }
    var fresh = _blankState(name);
    fresh.zone_types = _zoneBlock(zones);
    fresh.device = expandGenerator(String(generator), params, fresh.zone_types);
    return resolveCapacities(fresh);
  },

  blank_device: function (st, args, kw) {
    if (!st.builder) {
      throw new EditError('blank_device needs a DeviceBuilder to have been built first',
                          'no_builder', 'blank_device');
    }
    var fresh = _blankState(kw.name === undefined ? 'custom' : String(kw.name));
    fresh.zone_types = _zoneBlock(kw.zones || _zonesOfDevice(st.builder));
    // DEEP COPY, then keep the builder: `Machine.blank_device` calls `builder.build()`,
    // which copies, and `apply_call` returns `(machine, builder)` -- so in Python the
    // builder survives a seed AND does not see `resolve_capacities`.  Aliasing the two
    // here made a second seed price the first seed's resolved capacities.
    fresh.device = _deep(st.builder);
    fresh.builder = st.builder;
    return _sealDevice(resolveCapacities(fresh),
                       kw.name === undefined ? 'custom' : String(kw.name));
  },

  // `Machine.<generator>(..., template=...)`, i.e. `api.py::Machine._gen`: take a
  // template's zone types, primitive curves and control block, then expand the geometry
  // ON TOP of them -- the order matters, because a generator cannot place a site in a
  // zone that does not exist at expansion time.
  //
  // Python reads `arch/<stem>.arch.json` off disk.  A browser has no filesystem, so the
  // page hands the engine each template AS THE RECORDS THAT DECLARE IT and this replays
  // them onto a fresh state.  That is deliberately not a second document deserializer:
  // the template is expressed in the one language both halves already agree on, and the
  // parity bucket replays the same records through `apply_program` on the Python side.
  from_template: function (st, args, kw) {
    var gen = args[0] === undefined ? kw.generator : args[0];
    if (gen === undefined || gen === null) {
      throw new EditError('a template seed needs its generator name in `args`',
                          'missing_generator', 'from_template');
    }
    var fresh = _applyTemplate(_templateKey(kw.template),
                               kw.name === undefined ? 'custom' : String(kw.name));
    var params = {};
    for (var k in kw) if (own(kw, k) && k !== 'name' && k !== 'template' && k !== 'generator') {
      params[k] = kw[k];
    }
    fresh.device = expandGenerator(String(gen), params, fresh.zone_types);
    return resolveCapacities(fresh);
  },

  from_device: function (st, args, kw) {
    if (!st.builder) {
      throw new EditError('from_device needs a DeviceBuilder to have been built first',
                          'no_builder', 'from_device');
    }
    var fresh = _applyTemplate(_templateKey(kw.template),
                               kw.name === undefined ? 'custom' : String(kw.name));
    fresh.device = _deep(st.builder);
    fresh.builder = st.builder;
    return _sealDevice(resolveCapacities(fresh),
                       kw.name === undefined ? 'custom' : String(kw.name));
  },

  DeviceBuilder: function (st, args, kw) {
    st.builder = _dev({}, {}, {}, args[0] === undefined ? 'explicit' : String(args[0]),
                      _shallow(kw));
    return st;
  },
  'd.site': function (st, args, kw) { return _dBuild(st, args, kw, 'site'); },
  'd.junction': function (st, args, kw) {
    // `DeviceBuilder.junction(id, *pos, labels=())` -- there is no `zone` and no
    // `capacity`, and Python raises TypeError on either.  Refuse with the same words.
    for (var _k in kw) if (own(kw, _k) && _k !== 'labels') {
      throw new EditError("DeviceBuilder.junction() got an unexpected keyword argument '" +
                          _k + "'", 'TypeError', 'd.junction');
    }
    return _dBuild(st, args, kw, 'junction');
  },
  'd.segment': function (st, args, kw) {
    _builder(st, 'd.segment');
    st.builder.segments[String(args[0])] = _seg(
      String(args[0]), String(args[1]), String(args[2]),
      kw.length === undefined ? 1.0 : _finite(kw.length, 'a segment length', 'd.segment'),
      kw.capacity === undefined ? 1
        : Math.trunc(_finite(kw.capacity, 'a segment capacity', 'd.segment')),
      kw.loop === undefined ? null : kw.loop,
      kw.labels ? kw.labels.slice() : []);
    return st;
  },
  'd.loop': function (st, args, kw) {
    _builder(st, 'd.loop');
    st.builder.loops[String(args[0])] = _loop(
      String(args[0]), (args[1] || []).slice(),
      kw.closed === undefined ? true : !!kw.closed,
      kw.kind === undefined ? 'ring' : String(kw.kind),
      kw.note === undefined ? null : kw.note);
    return st;
  },

  // -- the twelve retuning methods ---------------------------------------------------

  describe: function (st, args, kw) {
    var d = args[0] === undefined ? '' : String(args[0]);
    st.description = d ? d : null;      // an EMPTY STRING becomes null, as in Python
    for (var k in kw) if (own(kw, k)) st.provenance[k] = kw[k];
    return st;
  },

  set_zone: function (st, args, kw) {
    var zone = String(args[0]);
    // an UNKNOWN zone is created with {capacity: 1} and then updated -- no error
    if (!own(st.zone_types, zone)) st.zone_types[zone] = { capacity: 1 };
    for (var k in kw) if (own(kw, k)) st.zone_types[zone][k] = kw[k];
    return resolveCapacities(st);
  },

  set_site_capacity: function (st, args, kw) {
    _requireDevice(st, 'set_site_capacity');
    var capacity = Math.trunc(_finite(args[1], 'a site capacity', 'set_site_capacity'));
    if (capacity < 1) {
      throw new EditError('a site must be able to hold at least one ion',
                          'cap_lt_1', 'set_site_capacity');
    }
    var names = typeof args[0] === 'string' ? [args[0]] : args[0];
    for (var i = 0; i < names.length; i++) {
      var nid = String(names[i]);
      if (!own(st.device.nodes, nid)) {
        throw new EditError('no such site ' + pyRepr(nid) + ' on ' + st.name,
                            'no_such_site', 'set_site_capacity');
      }
      if (st.device.nodes[nid].kind !== 'site') {
        throw new EditError(pyRepr(nid) + ' is a junction; it holds no ions',
                            'not_a_site', 'set_site_capacity');
      }
      st.device.nodes[nid].cap = capacity;
      st.device.nodes[nid].capacity_explicit = true;
    }
    return st;
  },

  set_segment_length: function (st, args, kw) {
    _requireDevice(st, 'set_segment_length');
    var length = _finite(args[1], 'a segment length', 'set_segment_length');
    if (!(length > 0)) {
      throw new EditError('a segment must have positive length',
                          'length_lte_0', 'set_segment_length');
    }
    var names = typeof args[0] === 'string' ? [args[0]] : args[0];
    for (var i = 0; i < names.length; i++) {
      var sid = String(names[i]);
      if (!own(st.device.segments, sid)) {
        throw new EditError('no such segment ' + pyRepr(sid) + ' on ' + st.name,
                            'no_such_segment', 'set_segment_length');
      }
      st.device.segments[sid].length = new PyFloat(length);
    }
    return st;
  },

  move_site: function (st, args, kw) {
    _requireDevice(st, 'move_site');
    var nid = String(args[0]);
    if (!own(st.device.nodes, nid)) {
      throw new EditError('no such node ' + pyRepr(nid) + ' on ' + st.name,
                          'no_such_node', 'move_site');
    }
    var pos = [];
    for (var i = 1; i < args.length; i++) pos.push(_coord(unbox(args[i]), 'position'));
    st.device.nodes[nid].pos = pos;
    return st;
  },

  set_curve: function (st, args, kw) {
    var name = String(args[0]);
    var table = kw.table === undefined ? 'local' : String(kw.table);
    var source = kw.source === undefined ? null : kw.source;
    st.primitives.curves[name] = _curvePoints(args[1] || [], table, source);
    return st;
  },

  set_degree_curve: function (st, args, kw) {
    var name = String(args[0]);
    var degree = Math.trunc(Number(unbox(args[1])));
    var table = kw.table === undefined ? 'local' : String(kw.table);
    var source = kw.source === undefined ? null : kw.source;
    // MERGES into the existing degree map; it does not replace the whole thing
    if (!own(st.primitives.degree_curves, name)) st.primitives.degree_curves[name] = {};
    st.primitives.degree_curves[name][degree] = _curvePoints(args[2] || [], table, source);
    return st;
  },

  set_primitive: function (st, args, kw) {
    var name = String(args[0]);
    if (!own(st.primitives.scalars, name)) st.primitives.scalars[name] = {};
    for (var k in kw) if (own(kw, k)) st.primitives.scalars[name][k] = kw[k];
    return st;
  },

  declare_class: function (st, args, kw) {
    var cid = String(args[0]);
    var classes = st.control.classes ? _shallow(st.control.classes) : {};
    var extra = [], old = classes.extra || [];
    for (var i = 0; i < old.length; i++) if (old[i].id !== cid) extra.push(old[i]);
    // RE-DECLARATION MOVES THE CLASS TO THE END AND REPLACES IT WHOLESALE -- no field
    // merge, so a re-declared class loses any field the new call does not name.  That
    // ordering is user-visible in the emitted Python and is diffed byte for byte.
    var rec = { id: cid };
    for (var k in kw) if (own(kw, k)) rec[k] = kw[k];
    extra.push(rec);
    classes.extra = extra;
    st.control.classes = classes;
    return st;
  },

  set_control: function (st, args, kw) {
    for (var k in kw) if (own(kw, k)) {
      // ONE LEVEL DEEP.  A wholesale replacement would destroy the `extra` list
      // `declare_class` maintains, which is why `listing.py` emits set_control BEFORE the
      // declare_class block.
      if (_isPlainObject(kw[k]) && _isPlainObject(st.control[k])) {
        var merged = _shallow(st.control[k]);
        for (var j in kw[k]) if (own(kw[k], j)) merged[j] = kw[k][j];
        st.control[k] = merged;
      } else {
        st.control[k] = kw[k];
      }
    }
    return st;
  },

  set_wiring: function (st, args, kw) {
    var w = st.control.wiring ? _shallow(st.control.wiring) : {};
    for (var k in kw) if (own(kw, k)) w[k] = kw[k];
    st.control.wiring = w;
    return st;
  },

  set_heating: function (st, args, kw) { return _mergeBlock(st, 'heating', kw); },
  set_species: function (st, args, kw) { return _mergeBlock(st, 'species', kw); },
  set_budget: function (st, args, kw) { return _mergeBlock(st, 'budget', kw); }
};

function _mergeBlock(st, block, kw) {
  for (var k in kw) if (own(kw, k)) st[block][k] = kw[k];
  return st;
}
function _builder(st, method) {
  if (!st.builder) throw new EditError(method + ' needs a DeviceBuilder first', 'no_builder', method);
}
function _dBuild(st, args, kw, kind) {
  _builder(st, 'd.' + kind);
  var nid = String(args[0]);
  // EXACTLY TWO.  The schema admits one to three coordinates and nothing in the codebase
  // reads a third, so a 3-D device would draw as a silent projection -- and this mirror
  // reads `args[1]` and `args[2]` only, so a 1-D one becomes NaN.  `DeviceBuilder.site`
  // refuses both; refuse them here, at the same statement and in the same words.
  if (args.length - 1 !== 2) {
    throw new EditError('DeviceBuilder.' + kind + '(' + pyRepr(nid) + ', ...) needs ' +
                        'exactly two coordinates, got ' + (args.length - 1) +
                        '; the layout is two-dimensional and nothing reads a third',
                        'ExpansionError', 'd.' + kind);
  }
  var n = _node(nid, _finite(args[1], 'an x coordinate', 'd.' + kind),
                _finite(args[2], 'a y coordinate', 'd.' + kind), kind,
                kw.zone === undefined ? null : kw.zone,
                kw.capacity === undefined ? 0
                  : Math.trunc(_finite(kw.capacity, 'a site capacity', 'd.' + kind)),
                kw.labels ? kw.labels.slice() : []);
  // `capacity_explicit=bool(capacity)` in `DeviceBuilder.site` -- a capacity of ZERO
  // means INHERIT, not "explicitly zero", and a NEGATIVE one is explicit (bool(-2) is
  // True), which is what lets the schema refuse it rather than the zone quietly
  // overwriting it.  Keying on the kwarg being PRESENT made `d.site(..., capacity=0)`
  // keep capacity 0 through `resolve_capacities` and then fail `check_structure`, on a
  // statement Python accepts; keying on `> 0` let a negative capacity be resolved away.
  if (n.cap !== 0) n.capacity_explicit = true;
  st.builder.nodes[nid] = n;
  return st;
}
function _zonesOfDevice(dev) {
  var set = new Set();
  for (var nid in dev.nodes) if (own(dev.nodes, nid)) {
    var z = dev.nodes[nid].zone;
    if (z !== null && z !== undefined) set.add(z);
  }
  return Array.from(set).sort(_cmpStr);
}

// `apply(state, {method, args, kwargs})` -- pure in the sense that it never touches the
// caller's state; the state is deep-copied on entry so a refused statement leaves the
// last good architecture on the stage rather than a half-applied one.
function applyCall(state, call) {
  var method = call && call.method;
  // A PROGRAMME STATEMENT IS REFUSED BY MESSAGE, never skipped.  Silence is the enemy this
  // whole page is built against: a `p.rotate(-3)` quietly dropped from an architecture
  // replay would leave the user with a programme they wrote and a page that does not run
  // it, and nothing anywhere would say so.
  if (call && (call.lane === 'prog' || call.lane === 'progseed')) {
    throw new EditError(
      pyRepr(method) + ' is a programme statement, not an architecture edit; it belongs ' +
      'in the programme lane (`p.` verbs: ' + programMethods().join(', ') + ')',
      'wrong_lane', method);
  }
  var fn = own(CALLS, method) ? CALLS[method] : null;
  if (!fn) {
    throw new EditError(pyRepr(method) + ' is not an editable method (have: ' +
                        methods().join(', ') + ')', 'unknown_method', method);
  }
  var st = state ? cloneState(state) : _blankState();
  // `apply_call` refuses a MUTATE verb when `machine is None`; nothing here did, so
  // `set_zone` before any seed built a zero-node architecture in the browser and raised
  // `no_machine` in Python.  NOT keyed on `st.device`: `_applyTemplate` legitimately
  // replays a template's own `set_zone` records onto a device-less state, because in
  // Python a template is a DOCUMENT off disk and never crosses `apply_call` at all.
  if (_kindOf(method) === 'mutate' && !st.seeded) {
    throw new EditError('no geometry yet: ' + method + ' needs a Machine.blank(...) first',
                        'no_machine', method);
  }
  var out = fn(st, (call.args || []).slice(), call.kwargs || {});
  if (_kindOf(method) === 'seed') out.seeded = true;
  return out;
}

function applyProgram(calls) {
  var st = null;
  for (var i = 0; i < calls.length; i++) {
    try {
      st = applyCall(st, calls[i]);
    } catch (err) {
      return { error: { code: err.code || 'error', method: (calls[i] || {}).method,
                        index: i, message: err.message } };
    }
  }
  return { ok: st };
}

function cloneState(st) {
  return {
    seeded: !!st.seeded,
    name: st.name, description: st.description, provenance: _deep(st.provenance),
    device: st.device ? _deep(st.device) : null,
    zone_types: _deep(st.zone_types), primitives: _deep(st.primitives),
    control: _deep(st.control), heating: _deep(st.heating),
    species: _deep(st.species), budget: _deep(st.budget),
    builder: st.builder ? _deep(st.builder) : null
  };
}
function _deep(v) {
  if (v === null || v === undefined) return v;
  if (v instanceof PyFloat) return new PyFloat(v.v);
  if (Array.isArray(v)) { var a = []; for (var i = 0; i < v.length; i++) a.push(_deep(v[i])); return a; }
  if (typeof v === 'object') {
    var o = {};
    for (var k in v) if (own(v, k)) o[k] = _deep(v[k]);
    return o;
  }
  return v;
}

// =====================================================================================
// 6. SERIALIZE -- Architecture.to_json(expanded=True), key for key
// =====================================================================================

// THE SCHEMA IS DATA, NOT CODE.  `qccd/arch/schema.py::export_schema()` puts the whole
// `ARCH_SCHEMA` dict -- every enum, every array bound, every id pattern, and the version
// string -- into the page's data blob, and `setSchema` hands it to this file at startup.
// Nothing about the schema is written down in JavaScript.
//
// This replaces `var SCHEMA_VERSION = '0.2'`, which was a hand-copied constant: exactly
// the second-source-of-truth this codebase keeps getting bitten by.  A version bump in
// schema.py now reaches the browser by rebuilding the page and cannot be forgotten,
// because there is nothing left to forget.
var SCHEMA = null;
function setSchema(s) {
  if (!s || !s.root || typeof s.version !== 'string') {
    throw new EngineError(
      'setSchema needs {version, root} as produced by qccd/arch/schema.py export_schema(); ' +
      'the engine keeps no copy of the schema and cannot validate or serialize without it');
  }
  SCHEMA = s;
  return SCHEMA;
}
function schema() { return SCHEMA; }
function schemaVersion() {
  if (!SCHEMA) throw new EngineError('no schema loaded: call QCCD.setSchema(D.schema) first');
  return SCHEMA.version;
}

// ---------------------------------------------------------------- the validator
//
// A line-for-line mirror of `qccd/arch/schema.py::_walk`, MESSAGE TEXT INCLUDED --
// `tests/test_engine_parity.py`'s `schema` bucket walks thousands of documents (the nine
// shipped architectures, their expanded forms, and ten families of one-token mutation)
// through BOTH walkers and diffs the error lists at tolerance zero.  A mirror that
// refuses different things, or refuses the same things with different words, is not a
// mirror.

function _schemaTypename(v) {
  if (v === null || v === undefined) return 'null';
  if (typeof v === 'boolean') return 'boolean';
  // `Number.isFinite`, not `typeof`: NaN and +-Infinity are `typeof 'number'` and would
  // pass the walker in memory, then become the `null` the walker correctly refuses only
  // after `JSON.stringify`.  A document that validates and then serializes into one that
  // does not is the worst of both.
  if (typeof v === 'number') {
    if (!Number.isFinite(v)) return 'non-finite number';
    // Above 1e21 `JSON.stringify` switches to exponential notation and `json.loads` reads
    // the result back as a Python FLOAT, so an integral JS number stops being an integer
    // the moment it crosses the blob.  Reporting it as 'integer' here let
    // `set_site_capacity("S0", 1e308)` pass every browser check and then be refused by
    // Python with "expected integer, got number".  `schema.py::_numtext` already carries
    // this divergence in its MESSAGE text; it was missing from the TYPE test.
    if (Number.isInteger(v) && Math.abs(v) < 1e21) return 'integer';
    return 'number';
  }
  if (typeof v === 'string') return 'string';
  if (Array.isArray(v)) return 'array';
  if (typeof v === 'object') return 'object';
  return typeof v;
}

// `qccd/arch/schema.py::_numtext`, character for character.  See its docstring: JSON has
// one number type, so Python's int/float distinction cannot survive the blob, and the
// message is written so that it does not need to.
function _schemaNum(x) {
  if (typeof x === 'boolean') return String(x);
  // `abs < 1e16` is Python's guard, not decoration: above it `str(int(f))` and `repr(f)`
  // diverge (`-1.2345678901234568e+20` against `-123456789012345680000`) and JS's
  // `String` follows the WRONG one of the two.  The schema-bucket corpus plants numbers
  // on both sides of that line for exactly this reason.
  if (Number.isInteger(x) && Math.abs(x) < 1e16) return String(x);
  return _pyFloatRepr(x);
}

// Python's `re.match` anchors at the START and not at the end.  Every pattern in the
// schema carries its own anchors, but one that did not must behave identically on both
// sides, so the start anchor is applied here rather than assumed.
function _schemaMatch(pat, str) { return new RegExp('^(?:' + pat + ')').test(str); }

function _schemaWalk(value, spec, path, errors) {
  var kind = spec.type, i, k;
  if (kind === 'any') return;
  if (kind === 'null') {
    if (value !== null && value !== undefined) {
      errors.push(path + ': expected null, got ' + _schemaTypename(value));
    }
    return;
  }
  if (kind === 'union') {
    for (i = 0; i < spec.options.length; i++) {
      var trial = [];
      _schemaWalk(value, spec.options[i], path, trial);
      if (!trial.length) return;
    }
    var opts = [];
    for (i = 0; i < spec.options.length; i++) opts.push(spec.options[i].type);
    errors.push(path + ': matched none of the allowed forms (' + opts.join(', ') +
                '); got ' + _schemaTypename(value));
    return;
  }
  if (kind === 'boolean') {
    if (typeof value !== 'boolean') {
      errors.push(path + ': expected boolean, got ' + _schemaTypename(value));
    }
    return;
  }
  if (kind === 'integer') {
    // `_schemaTypename` is the authority on what counts as an integer HERE, because it
    // knows what survives the JSON round trip: `Number.isInteger(1e308)` is true, but
    // `JSON.stringify` writes `1e+308` and Python reads that back as a float, so the
    // browser passed a document Python then refused. Testing `Number.isInteger` directly
    // reintroduced exactly the divergence the typename function exists to model.
    if (_schemaTypename(value) !== 'integer') {
      errors.push(path + ': expected integer, got ' + _schemaTypename(value));
      return;
    }
    if (own(spec, 'min') && value < spec.min) {
      errors.push(path + ': ' + _schemaNum(value) + ' is below the minimum ' + _schemaNum(spec.min));
    }
    if (own(spec, 'max') && value > spec.max) {
      errors.push(path + ': ' + _schemaNum(value) + ' is above the maximum ' + _schemaNum(spec.max));
    }
    return;
  }
  if (kind === 'number') {
    if (typeof value !== 'number') {
      errors.push(path + ': expected number, got ' + _schemaTypename(value));
      return;
    }
    if (own(spec, 'min') && value < spec.min) {
      errors.push(path + ': ' + _schemaNum(value) + ' is below the minimum ' + _schemaNum(spec.min));
    }
    if (own(spec, 'max') && value > spec.max) {
      errors.push(path + ': ' + _schemaNum(value) + ' is above the maximum ' + _schemaNum(spec.max));
    }
    return;
  }
  if (kind === 'string') {
    if (typeof value !== 'string') {
      errors.push(path + ': expected string, got ' + _schemaTypename(value));
      return;
    }
    if (spec.enum && spec.enum.indexOf(value) < 0) {
      var allowed = [];
      for (i = 0; i < spec.enum.length; i++) allowed.push(pyRepr(spec.enum[i]));
      errors.push(path + ': ' + pyRepr(value) + ' is not one of ' + allowed.join(', '));
    }
    if (spec.pattern && !_schemaMatch(spec.pattern, value)) {
      errors.push(path + ': ' + pyRepr(value) + ' does not match ' + spec.pattern);
    }
    return;
  }
  if (kind === 'array') {
    if (!Array.isArray(value)) {
      errors.push(path + ': expected array, got ' + _schemaTypename(value));
      return;
    }
    if (own(spec, 'min') && value.length < spec.min) {
      errors.push(path + ': needs at least ' + spec.min + ' item(s), has ' + value.length);
    }
    if (own(spec, 'max') && value.length > spec.max) {
      errors.push(path + ': allows at most ' + spec.max + ' item(s), has ' + value.length);
    }
    if (spec.items) {
      for (i = 0; i < value.length; i++) {
        _schemaWalk(value[i], spec.items, path + '[' + i + ']', errors);
      }
    }
    return;
  }
  if (kind === 'map') {
    if (value === null || typeof value !== 'object' || Array.isArray(value)) {
      errors.push(path + ': expected object, got ' + _schemaTypename(value));
      return;
    }
    for (k in value) if (own(value, k)) {
      if (spec.keys && !_schemaMatch(spec.keys, String(k))) {
        errors.push(path + ': key ' + pyRepr(String(k)) + ' does not match ' + spec.keys);
      }
      _schemaWalk(value[k], spec.values, path + '.' + k, errors);
    }
    return;
  }
  if (kind === 'object') {
    if (value === null || typeof value !== 'object' || Array.isArray(value)) {
      errors.push(path + ': expected object, got ' + _schemaTypename(value));
      return;
    }
    var props = spec.props || {}, req = spec.required || [];
    for (i = 0; i < req.length; i++) {
      if (!own(value, req[i])) errors.push(path + ': missing required key ' + pyRepr(req[i]));
    }
    var allowExtra = !!spec.additional;
    for (k in value) if (own(value, k)) {
      if (own(props, k)) _schemaWalk(value[k], props[k], path + '.' + k, errors);
      else if (!allowExtra) {
        var near = Object.keys(props).sort(_cmpStr).slice(0, 8).join(', ');
        errors.push(path + ': unknown key ' + pyRepr(k) + ' (known keys: ' + near + ')');
      }
    }
    return;
  }
  throw new EngineError('unhandled schema node type ' + pyRepr(kind) + ' at ' + path);
}

// `qccd/arch/schema.py::validate_document`.  Returns the error list; it never throws for
// a bad DOCUMENT -- only for a missing SCHEMA, which is a wiring fault, not a user error.
function validateDocument(doc, sch) {
  sch = sch || SCHEMA;
  if (!sch || !sch.root) {
    throw new EngineError(
      'validateDocument needs the schema shipped from qccd/arch/schema.py; the page ' +
      'carries it at D.schema and this file keeps no copy of its own');
  }
  var errors = [];
  _schemaWalk(doc, sch.root, '$', errors);
  if (doc !== null && typeof doc === 'object' && !Array.isArray(doc)) {
    var v = doc.schema_version;
    if (typeof v === 'string' && v !== sch.version) {
      errors.push('$.schema_version: ' + pyRepr(v) + ' but this build speaks ' + pyRepr(sch.version));
    }
  }
  return errors;
}

function serialize(st, opts) {
  opts = opts || {};
  var doc = { name: st.name, schema_version: schemaVersion() };
  if (st.description) doc.description = st.description;
  if (_nonEmpty(st.provenance)) doc.provenance = _json(st.provenance);
  doc.geometry = _json(deviceToJson(st.device));
  doc.zone_types = _json(st.zone_types);
  if (_nonEmpty(st.control)) doc.control = _json(st.control);
  doc.primitives = _json(primitivesToJson(st.primitives));
  if (_nonEmpty(st.heating)) doc.heating = _json(st.heating);
  if (_nonEmpty(st.species)) doc.species = _json(st.species);
  if (_nonEmpty(st.budget)) doc.budget = _json(st.budget);
  return doc;
}

// A JSON document has ONE number type, so the PyFloat boxes are stripped here.  They exist
// only so the TEXT surface can tell `length=1.0` from `capacity=1` -- the schema does care
// (`capacity`, `degree` and the degree-curve keys are `{"type": "integer"}`) and `_lit`
// re-emits floats with their `.0`.  Keeping the tag out of the serialized document is what
// lets the two sides be compared with a plain JSON canonicaliser.
function _json(v) {
  if (v instanceof PyFloat) return v.v;
  if (v === null || v === undefined) return v;
  if (Array.isArray(v)) { var a = []; for (var i = 0; i < v.length; i++) a.push(_json(v[i])); return a; }
  if (typeof v === 'object') {
    var o = {};
    for (var k in v) if (own(v, k)) o[k] = _json(v[k]);
    return o;
  }
  return v;
}

function deviceToJson(dev) {
  if (!dev) return { generator: 'explicit', params: {}, nodes: [] };
  var corners = allCorners(dev), ce = cornerEndpoints(dev), deg = degrees(dev);
  // ONE ordered array, mirroring `Device.to_json`.  The sites/junctions split it
  // replaces could not record an order, and `grid` interleaves -- so a round trip
  // reordered the device, which moves `layout._bows`' centroid and can flip a bow sign.
  var nodes = [], nid;
  for (nid in dev.nodes) if (own(dev.nodes, nid)) {
    var n = dev.nodes[nid];
    var d = { id: nid, pos: [n.pos[0], n.pos[1]], kind: n.kind };
    if (n.kind === 'site' || n.cap) d.capacity = Math.trunc(n.cap || 0);
    if (n.capacity_explicit) d.capacity_explicit = true;
    if (n.zone !== null && n.zone !== undefined) d.zone_type = n.zone;
    if (n.labels && n.labels.length) d.labels = n.labels.slice();
    d.degree = deg.get(nid);
    d.corner = corners.has(nid);
    nodes.push(d);
  }
  var out = { generator: dev.generator, params: _deep(dev.params), nodes: nodes };
  out.segments = [];
  for (var sid in dev.segments) if (own(dev.segments, sid)) {
    var s = dev.segments[sid];
    var sj = { id: sid, ends: [s.a, s.b], length: s.length,
               capacity: Math.trunc(s.cap), loop: s.loop === undefined ? null : s.loop };
    if (s.labels && s.labels.length) sj.labels = s.labels.slice();
    sj.corner_endpoints = ce.get(sid) || 0;
    out.segments.push(sj);
  }
  var loops = [];
  for (var lid in dev.loops) if (own(dev.loops, lid)) {
    var l = dev.loops[lid];
    var lj = { id: lid, kind: l.kind, nodes: l.nodes.slice(), closed: !!l.closed };
    if (l.note) lj.note = l.note;
    loops.push(lj);
  }
  if (loops.length) out.loops = loops;
  return out;
}

function primitivesToJson(prims) {
  var out = {}, name, k;
  for (name in prims.curves) if (own(prims.curves, name)) {
    out[name] = { curve: prims.curves[name].map(_pointToJson) };
  }
  for (name in prims.degree_curves) if (own(prims.degree_curves, name)) {
    if (!own(out, name)) out[name] = {};
    var by = {}, degs = Object.keys(prims.degree_curves[name]).map(Number).sort(_cmpNum);
    for (var i = 0; i < degs.length; i++) {
      by[String(degs[i])] = prims.degree_curves[name][degs[i]].map(_pointToJson);
    }
    out[name].curve_by_degree = by;
  }
  for (name in prims.scalars) if (own(prims.scalars, name)) {
    if (!own(out, name)) out[name] = {};
    for (k in prims.scalars[name]) if (own(prims.scalars[name], k)) {
      out[name][k] = prims.scalars[name][k];
    }
  }
  return out;
}
function _pointToJson(p) {
  var d = { us: p.us, quanta: p.quanta, table: p.table === undefined ? 'qccdsim_jones' : p.table };
  if (p.source !== undefined && p.source !== null) d.source = p.source;
  if (p.label !== undefined && p.label !== null) d.label = p.label;
  return d;
}

// =====================================================================================
// 7. RE-PRICING -- extending the page's existing accumulator, not replacing it
// =====================================================================================
//
// The page ALREADY replays client-side: `applyFrame` accumulates per-ion n-bar from the
// architecture's own curve constants and reports out loud when it disagrees with the
// exported `checksum`.  That is the precedent for client-side pricing, so this extends it
// rather than inventing a second mechanism.
//
// What is added: the constants are now RE-DERIVED from the (possibly edited) architecture
// instead of baked, and cost / depth / us are accumulated alongside the quanta.  That buys
// a parity check for FREE and with zero extra payload: Python already ships `f.cost` and
// `f.steps` on every frame, so the engine recomputes them and diffs frame by frame -- on
// the deck page that is 1,579 independent comparison points, and totals-only agreement is
// explicitly not enough because a compensating error inside the programme cancels.
//
// WHAT IS DELIBERATELY NOT MIRRORED: routing and compilation (`qccd/compile/*`), the rule
// engine R2..R18 (~750 lines over a CycleView with occupancy history and drivability), and
// the control-plane drivability map.  A green rule badge for a check that did not run is
// worse than no badge, so after an edit every rule except the structural one is shown
// struck through and labelled stale, with the exact re-verify command.

// `min(pts, key=...)` is FIRST-WINS on a tie, so the comparison must be strict `<`.
// `<=` would silently pick the LAST tied point, and two points that tie on `us` but not on
// `quanta` are exactly what a two-table curve contains.
function pickPoint(points, policy) {
  policy = policy || {};
  var table = policy.table === undefined ? 'qccdsim_jones' : policy.table;
  var objective = policy.objective || 'fastest';
  var pts = points;
  if (table !== null && table !== undefined) {
    var restricted = [];
    for (var i = 0; i < points.length; i++) if (points[i].table === table) restricted.push(points[i]);
    if (restricted.length) pts = restricted;
  }
  if (!pts.length) throw new EngineError('operating-point policy selected no curve point');
  var key;
  if (objective === 'fastest') key = function (p) { return [num(p.us), num(p.quanta)]; };
  else if (objective === 'coolest') key = function (p) { return [num(p.quanta), num(p.us)]; };
  else if (objective === 'balanced') {
    var usRef = Infinity, qRef = Infinity, j;
    for (j = 0; j < pts.length; j++) usRef = Math.min(usRef, num(pts[j].us));
    for (j = 0; j < pts.length; j++) if (num(pts[j].quanta) > 0) qRef = Math.min(qRef, num(pts[j].quanta));
    if (!usRef) usRef = 1.0;
    if (!isFinite(qRef) || !qRef) qRef = 1.0;
    key = function (p) { return [num(p.us) / usRef + num(p.quanta) / qRef, num(p.us)]; };
  } else throw new EngineError('unknown operating-point objective ' + pyRepr(objective));
  var best = pts[0], bk = key(best);
  for (var k = 1; k < pts.length; k++) {
    var kk = key(pts[k]);
    if (kk[0] < bk[0] || (kk[0] === bk[0] && kk[1] < bk[1])) { best = pts[k]; bk = kk; }
  }
  return best;
}
function num(v) { return Number(unbox(v)); }

// STRICT coercion, mirroring Python's `float()` / `int()`, which RAISE on a value that is
// not a number.  `Number("abc")` is NaN and `typeof NaN === 'number'`, so a typed value
// passed every downstream guard -- `NaN < 1` is false, so even an explicit `if (x < 1)
// throw` did not fire -- and `JSON.stringify` turned it into `null`.  The page then
// reported zero problems and handed over a document Python refuses.  Refuse it here, at
// the same point Python does.
function _finite(v, what, method) {
  var n = Number(unbox(v));
  if (!Number.isFinite(n)) {
    throw new EditError(
      'could not convert ' + pyRepr(unbox(v)) + ' to a number for ' + what +
      '; a non-finite value serializes as null and the toolchain refuses the file',
      'not_a_number', method);
  }
  return n;
}


function _charge(cost, depth, us, quanta) {
  return { cost: cost, depth: depth, us: us, q: quanta || {} };
}
// `then`: cost/depth/us/quanta ALL ADD.
function _then(a, b) {
  var q = {}, k;
  for (k in a.q) if (own(a.q, k)) q[k] = a.q[k];
  for (k in b.q) if (own(b.q, k)) q[k] = (q[k] || 0) + b.q[k];
  return _charge(a.cost + b.cost, a.depth + b.depth, a.us + b.us, q);
}
// `overlapping`: cost ADDS, depth and us take the MAX, quanta ADD.  Swapping this with
// `then` changes runtime by about 20% and nothing else, so no counter looks obviously
// wrong -- which is why the per-frame diff exists.
function _overlap(a, b) {
  var q = {}, k;
  for (k in a.q) if (own(a.q, k)) q[k] = a.q[k];
  for (k in b.q) if (own(b.q, k)) q[k] = (q[k] || 0) + b.q[k];
  return _charge(a.cost + b.cost, Math.max(a.depth, b.depth), Math.max(a.us, b.us), q);
}

// `DeckModel.move` / `CorrectedModel.move`.  `opts` mirrors `res.model`, which the page
// already ships: {kind, corner_hops, junction_min_degree, length_scaling, pitch}.
function makeModel(prims, deg, ce, segs, opts) {
  opts = opts || {};
  var kind = opts.kind || 'corrected';
  var cornerHops = opts.corner_hops === undefined ? (kind === 'deck' ? 3 : 1) : opts.corner_hops;
  var minDeg = opts.junction_min_degree === undefined ? 3 : opts.junction_min_degree;
  var lengthScaling = !!opts.length_scaling;
  var pitch = opts.pitch === undefined ? 1.0 : opts.pitch;
  var policy = opts.policy || { table: 'qccdsim_jones', objective: 'fastest' };
  var cache = {};
  function pt(name) {
    if (own(cache, name)) return cache[name];
    var c = prims.curves[name];
    if (!c) throw new EngineError('architecture declares no `' + name + '` curve');
    return (cache[name] = pickPoint(c, policy));
  }
  var jcache = {};
  function jpoint(d) {
    if (d < minDeg) return null;
    if (own(jcache, d)) return jcache[d];
    var dc = prims.degree_curves.junction_cross;
    var c = dc ? dc[d] : null;
    if (!c) {
      throw new EngineError('a degree-' + d + ' node is on a transport path but the ' +
                            'architecture prices no junction_cross at degree ' + d);
    }
    return (jcache[d] = pickPoint(c, policy));
  }
  // `CostModel.gate/cool/measure/reset`.  The BASE class returns an empty Charge and
  // `DeckModel` inherits it -- the deck's unit is routing operations, not time -- so the
  // deck model prices these at zero while the corrected model prices them from the
  // primitive scalars.  Leaving them out entirely (as a first cut did) lost one step on
  // every gate and every cool: the transport numbers were perfect and the total was 7%
  // short, which is precisely why the per-frame diff exists.
  function scalar(name) {
    var v = prims.scalars[name];
    if (!v) throw new EngineError('architecture declares no `' + name + '` primitive');
    return v;
  }
  function nonTransport(type, gateName) {
    if (kind === 'deck') return _charge(0, 0, 0, {});
    if (type === 'gate') {
      if (gateName === 'SWAP') {
        var sw = scalar('gate_swap'), ms = scalar('ms_gate');
        return _charge(0, 1, num(sw.gates) * num(ms.us), {});
      }
      return _charge(0, 1, num(scalar('ms_gate').us), {});
    }
    return _charge(0, 1, num(scalar(type).us), {});
  }

  return {
    kind: kind,
    _deg: deg,
    _anomalousPerUs: (opts.include_anomalous === false || kind === 'deck')
      ? 0 : (opts.anomalous_per_ms || 0) / 1000.0,
    nonTransport: nonTransport,
    move: function (segId, src, dst, entails) {
      var corner = (ce.get(segId) || 0) === 2;
      if (kind === 'deck') {
        var hops = corner ? cornerHops : 1;
        return _charge(hops, hops, 0, {});
      }
      var shuttle = pt('shuttle_segment');
      var h;
      if (corner) h = cornerHops;
      // `max(1, round(length/pitch))` -- Python's round is HALF TO EVEN.  Reachable from a
      // numeric field or a drag that lands on a half pitch: at length 2.5 the half-up form
      // gives 3 rather than 2, which is a 0.58% cost error and completely invisible.
      else if (lengthScaling && pitch > 0) h = Math.max(1, _pyRound(num(segs[segId].length) / pitch));
      else h = 1;
      var ch = _charge(h, 1, num(shuttle.us) * h, { shuttle: num(shuttle.quanta) * h });
      var jp = jpoint(deg.get(dst) || 0);
      if (jp) ch = _overlap(ch, _charge(1, 1, num(jp.us), { junction: num(jp.quanta) }));
      for (var i = 0; i < (entails || []).length; i++) {
        var w = entails[i];
        if (w !== 'split' && w !== 'merge') {
          throw new EngineError('movement class entails unknown primitive ' + pyRepr(w));
        }
        var p = pt(w);
        ch = _then(ch, _charge(1, 1, num(p.us), { split_merge: num(p.quanta) }));
      }
      return ch;
    }
  };
}

// Python's `round()` -- HALF TO EVEN.  `Math.round` is half-up and also disagrees on
// negatives.  This is the same trap `_q` covers for the layout, in the pricing path.
function _pyRound(x) {
  var f = Math.floor(x), d = x - f;
  if (d > 0.5) return f + 1;
  if (d < 0.5) return f;
  return (f % 2 === 0) ? f : f + 1;
}

// Re-price the shipped frames against the CURRENT architecture.  This is the extension of
// `applyFrame`: same walk, same order, now also accumulating cost / depth / us and
// re-deriving the constants instead of reading baked ones.
//
// Accumulation ORDER is load-bearing: frames in list order, and within a frame the moves
// in the order the frame lists them.  Deviating turns exact agreement into 1e-12 noise and
// hides the next real bug under a tolerance.
function priceFrames(frames, loops, model, classes, opts) {
  opts = opts || {};
  // `onCycle(win)` is called once per MACHINE CYCLE with the SAME window the pricing walk
  // is using -- exactly what `replay(on_cycle=...)` does in Python, and for exactly the
  // same reason: a panel and a verifier that derive occupancy separately will eventually
  // disagree, and the one that is wrong is the one nobody is testing.  `win` is REUSED
  // across cycles; retaining it is a bug.
  var onCycle = opts.onCycle || null;
  var w = opts.window || null;
  var pos = {}, q = {}, life = {}, occ = {};
  var over = {}, overJ = {};          // incremental over-capacity sets -- see _occSet
  var cap = {}, nid;
  if (onCycle && w) {
    for (nid in w.dev.nodes) if (own(w.dev.nodes, nid)) cap[nid] = w.dev.nodes[nid].cap;
  }
  // `gate` too: Python's ReplayResult carries five components and a mirror that
  // omits one reports `undefined` where the reference reports 0.
  var comp = { shuttle: 0, junction: 0, split_merge: 0, gate: 0, anomalous: 0 };
  var totals = { cost: 0, steps: 0, us: 0 };
  var perFrame = [], transits = 0;
  var peak = { v: 0.0, ion: null };
  var warnings = [];
  var work = 0, workCap = opts.workCap || 0;
  var EMPTY = {};      // shared, never written: the transit map of a cycle with none

  // Overwrite pool slot `i`, growing the pool only when a cycle is wider than any before.
  function _pool(win, i, ion, src, dst, seg, ent) {
    var m = win.moves[i];
    if (m === undefined) { m = { ion: 0, src: 0, dst: 0, seg: 0, entails: null };
                           win.moves[i] = m; }
    m.ion = ion; m.src = src; m.dst = dst; m.seg = seg; m.entails = ent;
  }

  // ONE occupancy write, and the two over-sets follow it.  Python re-reports an
  // over-capacity node EVERY cycle it stays over, so R1 has to see the whole set and not
  // only the nodes this cycle touched -- an incremental set keeps that O(1) per change
  // instead of O(nodes) per cycle.
  var nOver = 0, nOverJ = 0;
  function _occSet(n, d) {
    var v = (occ[n] || 0) + d;
    if (v <= 0) delete occ[n]; else occ[n] = v;
    if (!onCycle) return;
    var c = cap[n] === undefined ? 0 : cap[n];
    var was = own(over, n);
    if (v > c) { if (!was) { over[n] = true; nOver++; } }
    else if (was) { delete over[n]; nOver--; }
    var wasJ = own(overJ, n);
    if (v > 1 && w.deg(n) >= 3) { if (!wasJ) { overJ[n] = true; nOverJ++; } }
    else if (wasJ) { delete overJ[n]; nOverJ--; }
  }

  // an ion INTRODUCED by this cycle is R8's "new" half; nothing removes an ion, so the
  // "lost" half is empty by construction and the mirror says so rather than guessing.
  // Only a `moves` frame can introduce one -- a rotation moves the ions that are already
  // placed -- so the scan is done there rather than over every ion of every cycle.
  var NONE = [];
  function _fire(f, fid, type, cls, mode, nmoves, participants, posBefore, occBefore,
                 quanta, tr, added) {
    if (!onCycle) return;
    w.f = f; w.id = fid; w.type = type; w.cls = cls; w.mode = mode;
    w.nmv = nmoves; w.participants = participants;
    w.posBefore = posBefore; w.pos = pos;
    w.added = added;
    w.occBefore = occBefore; w.occ = occ;
    w.quanta = quanta; w.transits = tr;
    // `Object.keys` on an empty object still allocates; over-capacity is the exception
    // rather than the rule, so the empty case reuses one array.
    w.over = nOver ? Object.keys(over) : NONE;
    w.overJ = nOverJ ? Object.keys(overJ) : NONE;
    onCycle();
  }

  for (var fi = 0; fi < frames.length; fi++) {
    var f = frames[fi];
    var fid = f.id === undefined ? fi : f.id;
    var fc = 0, fd = 0, fus = 0;
    if (f.type === 'init') {
      for (var k in f.place) if (own(f.place, k)) {
        // a later `init` that re-places an ion must VACATE the node it was on, or the
        // occupancy counters leak a phantom ion no later cycle can clear and R1/R2/R13
        // start reading corrupted occupancies
        if (own(pos, k)) _occSet(pos[k], -1);
        pos[k] = f.place[k]; q[k] = 0; life[k] = 0;
        _occSet(f.place[k], +1);
      }
      // NO CycleView: `replay.py` records an `init` cycle and `continue`s before the rule
      // pass, so a mirror that judged it would report violations Python never can.
    } else if (f.type === 'gate' || f.type === 'measure' || f.type === 'reset' ||
               f.type === 'cool') {
      // THE QUANTA PHASE.  `quanta_at_start` is snapshotted BEFORE this cycle's own charge
      // and before its anomalous term, because R7 asks what the ions are at GATE TIME.
      // Reading `q` after `_anom` disagrees by one cycle's background heating -- invisible
      // in every total, and R7's budget is 1.0 against arrivals at 22.5.
      var qStart = null;
      if (onCycle && f.type === 'gate') {
        qStart = {};
        var gp = f.pairs || [];
        for (var gi = 0; gi < gp.length; gi++) {
          qStart[gp[gi][0]] = q[gp[gi][0]] || 0;
          qStart[gp[gi][1]] = q[gp[gi][1]] || 0;
        }
      }
      var ch0;
      try { ch0 = model.nonTransport(f.type, f.gate); }
      catch (err) { ch0 = _charge(0, 1, 0, {}); warnings.push(err.message); }
      fc += ch0.cost; fd += ch0.depth; fus += ch0.us;
      _anom(model, pos, q, life, comp, fus, peak);
      if (f.type === 'cool') {
        // a cool zeroes the RUNNING n-bar, which is what drives `peak`, but never the
        // lifetime deposit, which is what the checksum sums.  Two accumulators, easy to
        // conflate, and only a per-ion diff catches the conflation.
        var ions = (f.ions && f.ions.length) ? f.ions : Object.keys(pos);
        for (var ci = 0; ci < ions.length; ci++) q[ions[ci]] = 0;
      }
      // NOTHING MOVED, so pos_before IS pos and occ_before IS occ.
      _fire(f, fid, f.type, f.cls === undefined ? null : f.cls,
            f.mode === undefined ? null : f.mode, 0, NONE, pos, occ,
            qStart === null ? q : qStart, EMPTY, NONE);
    } else if (f.type === 'simd') {
      // `entails` is read from the CLASS TABLE, not from the frame: after
      // `declare_class("dock", entails=[])` the baked list on the frame is a lie.
      var ent = f.entails || [];
      if (f.cls && classes && own(classes, f.cls) && classes[f.cls].entails) {
        ent = classes[f.cls].entails;
      }
      if (f.shift) {
        var loop = f.shift[0], delta = f.shift[1], seq = loops[loop];
        if (!seq) { warnings.push('frame ' + fi + ' shifts unknown loop ' + loop); }
        else if (Number(delta) === 0) {
          // a zero-hop rotation is a real (if degenerate) cycle: Python returns one empty
          // sub-cycle so its claims are still exposed to the rules rather than waved
          // through by an instruction that produced no trace at all
          _fire(f, fid, 'simd', f.cls === undefined ? null : f.cls,
                f.mode === undefined ? null : f.mode, 0, NONE, pos, occ, q, EMPTY, NONE);
        } else {
          var kk = seq.length, step = delta >= 0 ? 1 : -1, idx = {};
          for (var si = 0; si < seq.length; si++) idx[seq[si]] = si;
          // |delta| = n is n SEPARATE CYCLES, each with its own max depth and duration.
          for (var h = 0; h < Math.abs(delta); h++) {
            var subC = 0, subD = 0, subUs = 0;
            var ionIds = Object.keys(pos).sort(_cmpStr);   // participants sorted by ion id
            var moved = {}, wParts = [], pb = {}, ob = {}, nmv = 0;
            for (var ii = 0; ii < ionIds.length; ii++) {
              var ion = ionIds[ii], i0 = idx[pos[ion]];
              if (i0 === undefined) continue;
              var dstNode = seq[((i0 + step) % kk + kk) % kk];
              moved[ion] = dstNode;
              var segId = _between(model, pos[ion], dstNode);
              var ch = model.move(segId, pos[ion], dstNode, ent);
              subC += ch.cost; subD = Math.max(subD, ch.depth); subUs = Math.max(subUs, ch.us);
              _deposit(q, life, comp, ion, ch.q, peak);
              if (deg(model, dstNode) >= 3) transits++;
              if (onCycle) {
                _pool(w, nmv++, ion, pos[ion], dstNode, segId, ent);
                wParts.push(ion);
                pb[ion] = pos[ion];
                if (ob[pos[ion]] === undefined) ob[pos[ion]] = occ[pos[ion]] || 0;
                if (ob[dstNode] === undefined) ob[dstNode] = occ[dstNode] || 0;
              }
            }
            for (var m in moved) if (own(moved, m)) {
              _occSet(pos[m], -1);
              pos[m] = moved[m];
            }
            for (var m2 in moved) if (own(moved, m2)) _occSet(moved[m2], +1);
            _anom(model, pos, q, life, comp, subUs, peak);
            fc += subC; fd += subD; fus += subUs;
            work += nmv + 1;
            if (workCap && work > workCap) {
              throw new EngineError('this programme is too large to rule-check in the ' +
                'browser (' + work + ' resolved moves past a cap of ' + workCap +
                '); run `python -m qccd run` instead of reporting a number that was not computed');
            }
            // TRANSPORT reads POST-cycle quanta, unlike a gate.
            _fire(f, fid, 'simd', f.cls === undefined ? null : f.cls,
                  f.mode === undefined ? null : f.mode, nmv, wParts, pb, ob, q, EMPTY,
                  NONE);
          }
        }
      } else if (f.moves) {
        var wParts2 = [], pb2 = {}, ob2 = {}, tr2 = {}, nmv2 = 0;
        var origPos = {}, finalPos = {}, mv;
        // OCCUPANCY IS SNAPSHOTTED BEFORE ANYTHING MOVES and applied after everything
        // has: `replay.py` computes every charge, then decrements every source, then
        // increments every destination.  Recording `occ_before` lazily as each move was
        // priced would let an earlier move in the same cycle change what a later one
        // reads, and R1's roadblock test compares exactly those two numbers.
        if (onCycle) {
          for (var oi = 0; oi < f.moves.length; oi++) {
            var op = f.moves[oi][1];
            for (var oj = 0; oj < op.length; oj++) {
              if (ob2[op[oj]] === undefined) ob2[op[oj]] = occ[op[oj]] || 0;
            }
          }
        }
        for (var mi = 0; mi < f.moves.length; mi++) {
          var ionb = f.moves[mi][0], path = f.moves[mi][1];
          // TWO LEVELS OF COMPOSITION, and getting them the wrong way round is invisible
          // in the cost and wrong in the steps.  ALONG one ion's route the charges compose
          // SEQUENTIALLY (`then`: cost, depth and us all add -- the ion really does
          // traverse the second segment after the first).  ACROSS the ions of one cycle
          // they compose in PARALLEL: cost sums, but depth and duration take the MAX,
          // because a SIMD cycle is one batched machine operation.  Flattening both into a
          // single max over every segment of every ion gave the RIGHT cost and the wrong
          // steps -- a correct-looking total hiding an incorrect one, which is exactly the
          // shape a totals-only check waves through.
          var charge = _charge(0, 0, 0, {});
          for (var pi = 1; pi < path.length; pi++) {
            var sid = _between(model, path[pi - 1], path[pi]);
            // entails once per MOVE, on the FIRST segment of a multi-segment route: one
            // dock is one split plus one merge however long the spur is.
            charge = _then(charge, model.move(sid, path[pi - 1], path[pi],
                                              pi === 1 ? ent : []));
            // a junction TRANSIT is ENTERING a node of degree >= 3, not traversing a
            // segment; the two differ by a factor of three on the shipped ring.
            if (deg(model, path[pi]) >= 3) transits++;
            if (onCycle) {
              _pool(w, nmv2++, ionb, path[pi - 1], path[pi], sid, ent);
              // every node the path ENTERED except the last is passed THROUGH, and a site
              // with no room to spare there is a roadblock (R1)
              if (pi < path.length - 1) tr2[path[pi]] = (tr2[path[pi]] || 0) + 1;
            }
          }
          fc += charge.cost;
          fd = Math.max(fd, charge.depth);
          fus = Math.max(fus, charge.us);
          _deposit(q, life, comp, ionb, charge.q, peak);
          if (onCycle) {
            wParts2.push(ionb);
            if (!own(pb2, ionb)) pb2[ionb] = own(pos, ionb) ? pos[ionb] : undefined;
          }
          if (!own(origPos, ionb)) origPos[ionb] = own(pos, ionb) ? pos[ionb] : null;
          finalPos[ionb] = path[path.length - 1];
          pos[ionb] = path[path.length - 1];
        }
        // keyed by ION: a programme that lists one ion twice in a cycle is illegal (R8
        // reports it), but it must not be able to corrupt the occupancy counters on the
        // way to being reported.
        for (mv in origPos) if (own(origPos, mv)) {
          if (origPos[mv] !== null) _occSet(origPos[mv], -1);
        }
        for (mv in finalPos) if (own(finalPos, mv)) _occSet(finalPos[mv], +1);
        _anom(model, pos, q, life, comp, fus, peak);
        work += nmv2 + 1;
        if (workCap && work > workCap) {
          throw new EngineError('this programme is too large to rule-check in the ' +
            'browser (' + work + ' resolved moves past a cap of ' + workCap +
            '); run `python -m qccd run` instead of reporting a number that was not computed');
        }
        var added2 = [];
        for (var a2 in pb2) if (own(pb2, a2) && pb2[a2] === undefined) added2.push(a2);
        _fire(f, fid, 'simd', f.cls === undefined ? null : f.cls,
              f.mode === undefined ? null : f.mode, nmv2, wParts2, pb2, ob2, q, tr2,
              added2);
      } else {
        // a simd cycle with no template and no participants is still a cycle
        _fire(f, fid, 'simd', f.cls === undefined ? null : f.cls,
              f.mode === undefined ? null : f.mode, 0, NONE, pos, occ, q, EMPTY, NONE);
      }
    }
    totals.cost += fc; totals.steps += fd; totals.us += fus;
    perFrame.push([fc, fd, fus]);
  }
  return { totals: totals, comp: comp, perFrame: perFrame, life: life, q: q,
           transits: transits, peak: peak.v, peakIon: peak.ion, warnings: warnings,
           occ: occ, pos: pos };
}
// `peak` is the peak of the RUNNING n-bar -- the accumulator a cool ZEROES -- and NOT of
// the lifetime deposit, which is what the checksum sums.  Two accumulators, and conflating
// them is invisible in every total: on the shipped deck programme the lifetime maximum is
// 1,805 quanta and the real peak is 55.17.  Python tracks it inside its own `_bump`, so
// this does too rather than snapshotting once per frame.
function _deposit(q, life, comp, ion, quanta, peak) {
  var t = 0;
  for (var k in quanta) if (own(quanta, k)) {
    t += quanta[k];
    comp[k] = (comp[k] || 0) + quanta[k];
  }
  q[ion] = (q[ion] || 0) + t;
  life[ion] = (life[ion] || 0) + t;
  if (q[ion] > peak.v) { peak.v = q[ion]; peak.ion = ion; }
}

function deg(model, nid) {
  var d = model._deg ? model._deg.get(nid) : 0;
  return d === undefined ? 0 : d;
}

// R17: the background term is deposited on EVERY PLACED ION every cycle with a non-zero
// duration -- including ions that never moved, and including gate, cool, measure and reset
// cycles.  It therefore depends on getting the cycle DURATION exactly right, which means a
// `us` bug shows up twice.
function _anom(model, pos, q, life, comp, us, peak) {
  var rate = model._anomalousPerUs || 0;
  if (!rate || !us) return;
  var d = rate * us;
  for (var ion in pos) if (own(pos, ion)) {
    q[ion] = (q[ion] || 0) + d;
    life[ion] = (life[ion] || 0) + d;
    comp.anomalous = (comp.anomalous || 0) + d;
    if (q[ion] > peak.v) { peak.v = q[ion]; peak.ion = ion; }
  }
}

// The segment joining two nodes, by id.  Attached to the model so the lookup table is
// built once per re-price rather than once per move.
function _between(model, a, b) {
  var s = model._pair[a + '>' + b];
  if (s === undefined) {
    throw new EngineError('no segment between ' + pyRepr(a) + ' and ' + pyRepr(b));
  }
  return s;
}

function pairIndex(dev) {
  var out = {};
  for (var sid in dev.segments) if (own(dev.segments, sid)) {
    var s = dev.segments[sid];
    out[s.a + '>' + s.b] = sid;
    out[s.b + '>' + s.a] = sid;
  }
  return out;
}

// Structural checks that must run BEFORE pricing, because Python is silent on some of
// them and parity therefore cannot catch them.  The worst is the last: `Architecture.
// entails` swallows a KeyError and returns (), so dropping a movement class reprices the
// whole programme -- half the docking heat vanishes, runtime falls 14% -- with NO error
// anywhere in the stack.  A page that only diffed against Python would agree, confidently,
// with a wrong number.
function validateProgram(dev, frames, classes) {
  var out = [], seen = {}, loopOk = {}, fi, i;
  var pair = pairIndex(dev);
  // WHERE EVERY ION IS, walked alongside the structural checks.  `replay.py` REFUSES a
  // participant that is not where it says it is -- "instruction 2: ion d1 declared at T1
  // but is at T2" -- and stops, so no rule ever runs and no number is produced.  Without
  // this the browser priced such a programme happily (cost 5, steps 6) for something
  // Python cannot execute at all, and the stage animated an ion teleporting.  It is a
  // STRUCTURAL break, so the price refuses and the stage freezes off the same array.
  var pos = {};
  for (fi = 0; fi < frames.length; fi++) {
    var f = frames[fi];
    if (f.type === 'init') {
      for (var ion in f.place) if (own(f.place, ion)) {
        if (!own(dev.nodes, f.place[ion])) {
          _bump(out, seen, { kind: 'unknown_node', instr: f.id === undefined ? fi : f.id,
                             ion: ion, node: f.place[ion] });
        }
        pos[ion] = f.place[ion];
      }
    }
    if (f.type !== 'simd') continue;
    if (f.cls && classes && !own(classes, f.cls)) {
      _bump(out, seen, { kind: 'unknown_class', instr: f.id === undefined ? fi : f.id,
                         cls: f.cls });
    }
    if (f.shift) {
      var lid = f.shift[0];
      // the positions have to follow the rotation, or the NEXT frame's declared source
      // is compared against where the ion was three cycles ago
      if (own(dev.loops, lid)) {
        var sq = dev.loops[lid].nodes, kk = sq.length;
        var dl = Math.trunc(Number(f.shift[1]));
        if (kk && dl) {
          var ix = {};
          for (var qi = 0; qi < kk; qi++) ix[sq[qi]] = qi;
          var next = {};
          for (var ion2 in pos) if (own(pos, ion2)) {
            var at = ix[pos[ion2]];
            if (at !== undefined) next[ion2] = sq[((at + dl) % kk + kk) % kk];
          }
          for (var ion3 in next) if (own(next, ion3)) pos[ion3] = next[ion3];
        }
      }
      if (!own(dev.loops, lid)) {
        _bump(out, seen, { kind: 'missing_loop', instr: f.id === undefined ? fi : f.id, loop: lid });
      } else if (Number(f.shift[1]) !== 0 && !own(loopOk, lid)) {
        // A rigid rotation shuttles across EVERY segment of the ring, so a shift needs
        // the ring to still BE a ring: `Device.shift_map` raises "loop is open; a rigid
        // shift is undefined" on an open one, and walks the wrap-around pair on a closed
        // one.  Nothing in the browser said so.  The `no_segment` check above ran only on
        // `moves` frames and `loopLengths` compares node COUNTS, so cutting a rail left
        // the count identical and the structural check completely silent -- measured on
        // cyclone_base, every one of E0..E7 escaped here and surfaced only as a
        // `price_error` thrown from inside the cost model, while the stage flew all 72
        // ions across a rail that was gone.
        //
        // A delta of 0 moves nothing and is legal over an open path, so it is not a break.
        // Memoised per loop id: `repriceNow` runs on every keystroke in the side editor
        // and a 1,975-frame deck programme over a 144-node ring would otherwise do ~284k
        // pair lookups each time.
        loopOk[lid] = true;
        var seq = dev.loops[lid].nodes;
        // `remove_segment` OPENS a loop by rotating the sequence so the cut lands at the
        // wrap, so an open loop's missing join is exactly `seq[last] -> seq[0]`.  Naming
        // that pair makes the message say what the cost model would have thrown.
        if (dev.loops[lid].closed === false) {
          _bump(out, seen, { kind: 'loop_broken', instr: f.id === undefined ? fi : f.id,
                             loop: lid, src: seq[seq.length - 1], dst: seq[0] });
        }
        for (i = 0; i < seq.length; i++) {
          var u = seq[i], v = seq[(i + 1) % seq.length];
          if (pair[u + '>' + v] === undefined) {
            _bump(out, seen, { kind: 'loop_broken', instr: f.id === undefined ? fi : f.id,
                               loop: lid, src: u, dst: v });
          }
        }
      }
    } else if (f.moves) {
      for (i = 0; i < f.moves.length; i++) {
        var mion = f.moves[i][0], path = f.moves[i][1];
        for (var p = 0; p < path.length; p++) {
          if (!own(dev.nodes, path[p])) {
            _bump(out, seen, { kind: 'unknown_node', instr: f.id === undefined ? fi : f.id,
                               ion: mion, node: path[p] });
          } else if (p > 0 && pair[path[p - 1] + '>' + path[p]] === undefined) {
            _bump(out, seen, { kind: 'no_segment', instr: f.id === undefined ? fi : f.id,
                               ion: mion, src: path[p - 1], dst: path[p] });
          }
        }
        if (own(pos, mion) && path.length && pos[mion] !== path[0]) {
          _bump(out, seen, { kind: 'declared_elsewhere',
                             instr: f.id === undefined ? fi : f.id,
                             ion: mion, src: path[0], dst: pos[mion] });
        }
        if (path.length) pos[mion] = path[path.length - 1];
      }
    }
  }
  return out;
}
function _bump(out, seen, rec) {
  var key = rec.kind + '|' + (rec.node || '') + '|' + (rec.cls || '') + '|' +
            (rec.loop || '') + '|' + (rec.src || '') + '|' + (rec.dst || '');
  if (own(seen, key)) { seen[key].count++; return; }
  rec.count = 1;
  seen[key] = rec;
  out.push(rec);
}

// A loop whose LENGTH changed still shifts, and produces different numbers with no error
// at all.  Recording the length at load and refusing to price a shift over a resized loop
// is the only thing that catches it.
function loopLengths(dev) {
  var out = {};
  for (var lid in dev.loops) if (own(dev.loops, lid)) out[lid] = dev.loops[lid].nodes.length;
  return out;
}

// =====================================================================================
// 8. THE TEXT SURFACE -- a strict subset of the emitted Python, parsed and re-rendered
// =====================================================================================
//
// The user asked to PROGRAM the architecture, so the side editor's language is the same
// Python `architecture_listing` already emits.  What you type is exactly what `m.source()`
// prints and exactly what `rebuild()` execs: copy the panel, paste into a .py, run it.
// Neither a form nor a JSON editor can say that.
//
// It is a GRAMMAR WITH NO EXPRESSION PRODUCTION, not `eval` with a blocklist.  There is no
// attribute access, no operator, no arithmetic, no comprehension, no control flow; `dict(
// ... )` is the only callable in value position and method names are checked against the
// whitelist AT PARSE TIME, so an unknown method is a red mark at a column rather than a
// runtime surprise.
//
//   program   := (statement | COMMENT | BLANK)*
//   statement := 'm' '=' 'Machine' '.' NAME '(' args ')'      -- seed
//              | 'm' '.' NAME '(' args ')'                    -- edit
//              | 'd' '=' 'DeviceBuilder' '(' args ')'         -- build
//              | 'd' '.' NAME '(' args ')'
//   args      := [ arg (',' arg)* [','] ]
//   arg       := NAME '=' value | '**' object | value
//   value     := STRING | NUMBER | 'True' | 'False' | 'None' | array | object | dictcall
//
// A logical line continues while bracket depth > 0 -- Python's own rule, and what admits
// the multi-line `set_curve(name, [\n dict(...),\n ...\n])` the emitter produces.
// STRING accepts ' and " on input (be generous to a human typing) but render() ALWAYS
// emits ", matching json.dumps.  Superset in, strict subset out: that is what lets the
// byte-for-byte `render(parse(x)) === x` assertion hold.

// ONE escape table, read by `tokenize` and written by `_pyStr`.  It used to be two open
// ternary chains ending in `: e` and `else if (code < 0x20)`, so the two halves named
// DIFFERENT sets and neither said so: `"A\bB"` was read as `AbB` and written as
// `"A<U+0008>B"` where `json.dumps` writes `"A\bB"`, `"A\x41B"` was read as `Ax41B`, and
// `"A\uZZZZB"` became a NUL because `String.fromCharCode(NaN)` is NUL.  All silent.
// Prose pasted out of a PDF carries form feeds and every `note=` / `source=` / `describe`
// field is free text, so this is reachable, and `render(parse(x)) === x` provably cannot
// hold while the two disagree.
var _ESC = { 'n': '\n', 't': '\t', 'r': '\r', 'b': '\b', 'f': '\f',
             '\\': '\\', '"': '"', "'": "'" };

function tokenize(src, line0) {
  var toks = [], i = 0, line = line0, col = 1;
  while (i < src.length) {
    var c = src[i];
    if (c === ' ' || c === '\t' || c === '\r') { i++; col++; continue; }
    if (c === '\n') { i++; line++; col = 1; continue; }
    if (c === '#') { while (i < src.length && src[i] !== '\n') i++; continue; }
    var start = { line: line, col: col };
    if (c === '"' || c === "'") {
      var quote = c, out = ''; i++; col++;
      while (i < src.length && src[i] !== quote) {
        if (src[i] === '\\') {
          var e = src[i + 1];
          if (e === 'u') {
            var hex = src.substr(i + 2, 4);
            if (!/^[0-9a-fA-F]{4}$/.test(hex)) {
              throw _perr(start, 'bad \\u escape: ' + JSON.stringify('\\u' + hex));
            }
            out += String.fromCharCode(parseInt(hex, 16));
            i += 6; col += 6;
          } else if (own(_ESC, e)) {
            out += _ESC[e]; i += 2; col += 2;
          } else {
            // REFUSING is safe precisely because `render()` provably emits only the
            // closed json.dumps set, so no emitted listing can contain one.  Falling
            // through to `: e` swallowed the backslash and mistranslated the character,
            // which is a whole class of silent corruption rather than two members of it.
            throw _perr(start, 'unknown escape ' + JSON.stringify('\\' + (e === undefined ? '' : e)) +
                        '; the emitter produces only \\\\ \\" \\b \\f \\n \\r \\t and \\uXXXX');
          }
        } else { out += src[i]; i++; col++; }
      }
      if (i >= src.length) throw _perr(start, 'unterminated string');
      i++; col++;
      toks.push({ t: 'str', v: out, at: start });
      continue;
    }
    if (/[0-9]/.test(c) || (c === '-' && /[0-9.]/.test(src[i + 1] || ''))) {
      var m = /^-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/.exec(src.slice(i));
      var text = m[0];
      i += text.length; col += text.length;
      var isFloat = /[.eE]/.test(text);
      var val = Number(text);
      if (!isFinite(val)) throw _perr(start, 'number is not finite: ' + text);
      toks.push({ t: 'num', v: isFloat ? new PyFloat(val) : val, at: start });
      continue;
    }
    if (/[A-Za-z_]/.test(c)) {
      var w = /^[A-Za-z_][A-Za-z0-9_]*/.exec(src.slice(i))[0];
      i += w.length; col += w.length;
      toks.push({ t: 'name', v: w, at: start });
      continue;
    }
    if ('()[]{},=:*.'.indexOf(c) >= 0) {
      if (c === '*' && src[i + 1] === '*') { toks.push({ t: 'op', v: '**', at: start }); i += 2; col += 2; continue; }
      toks.push({ t: 'op', v: c, at: start }); i++; col++;
      continue;
    }
    // `inf` / `nan` are rejected by the NUMBER rule because they are not JSON and the
    // emitter must never produce them either; anything else here is a real syntax error.
    throw _perr(start, 'unexpected character ' + JSON.stringify(c));
  }
  return toks;
}
function _perr(at, msg) {
  var e = new EditError(msg, 'syntax', null);
  e.line = at.line; e.col = at.col;
  return e;
}

// Split source into LOGICAL lines: a physical line continues while bracket depth > 0, and
// a `#` starts a comment that runs to end of line (never inside a string).
function logicalLines(src) {
  var lines = src.split('\n'), out = [], buf = null, depth = 0, startLine = 0;
  for (var i = 0; i < lines.length; i++) {
    var text = lines[i];
    if (buf === null) { buf = text; startLine = i + 1; } else { buf += '\n' + text; }
    depth += _depthDelta(text);
    if (depth <= 0) { out.push({ line: startLine, text: buf }); buf = null; depth = 0; }
  }
  if (buf !== null) out.push({ line: startLine, text: buf });
  return out;
}
function _depthDelta(text) {
  var d = 0, q = null;
  for (var i = 0; i < text.length; i++) {
    var c = text[i];
    if (q) { if (c === '\\') i++; else if (c === q) q = null; continue; }
    if (c === '"' || c === "'") { q = c; continue; }
    if (c === '#') break;
    if (c === '(' || c === '[' || c === '{') d++;
    if (c === ')' || c === ']' || c === '}') d--;
  }
  return d;
}

function parse(src) {
  var stmts = [], errors = [], arch = [], prog = [], seed = null;
  var rows = logicalLines(src);
  for (var r = 0; r < rows.length; r++) {
    var row = rows[r];
    if (!row.text.trim() || /^\s*#/.test(row.text)) continue;
    try {
      var st = _parseStatement(row.text, row.line);
      stmts.push(st);
      if (st.lane === 'prog') prog.push(st);
      else if (st.lane === 'progseed') seed = st;
      else arch.push(st);
    } catch (err) {
      errors.push({ line: err.line || row.line, col: err.col || 1,
                    message: err.message, text: row.text });
    }
  }
  // ONE text pane, ONE parse, TWO whitelists.  `stmts` keeps every statement in source
  // order so `renderProgram(parse(x).stmts) === x` still holds byte for byte on a mixed
  // document; `arch` and `prog` are the two views the appliers take.
  return { stmts: stmts, errors: errors, arch: arch, prog: prog, progSeed: seed };
}

function _parseStatement(text, line) {
  var toks = tokenize(text, line), p = { toks: toks, i: 0 };
  var method = null, seed = false;
  // TWO NEW PRODUCTIONS AND NO NEW VALUE FORM.  Every literal shape a programme statement
  // needs -- nested lists, string-keyed objects, negative ints -- already parses; the only
  // thing missing was the receiver.  Deliberately NO `with` and no indented suites:
  // `logicalLines` joins physical lines on BRACKET DEPTH ALONE, which is Python's own rule
  // and exactly what admits the multi-line `set_curve(name, [\n dict(...),\n])` the
  // emitter produces.  Adding indentation tracking would put a second, competing
  // line-joining rule in the same function, and `render(parse(x)) === x` is its first
  // casualty.  `p.simd(cls, moves)` is the flat form of `with p.cycle(...)`, and it exists
  // in `qccd/api.py` so the text means the same thing when it is pasted into a `.py`.
  if (_isName(p, 'p') && _peekOp(p, 1) === '=') {
    _take(p); _take(p);
    var powner = _expectName(p);
    _expectOp(p, '.');
    var pmeth = _expectName(p);
    if (powner !== 'm' || pmeth !== 'program') {
      throw _perr(toks[0].at, 'only `p = m.program(...)` opens a programme');
    }
    var pcall = _parseArgs(p, 'program');
    if (p.i < toks.length) throw _perr(toks[p.i].at, 'trailing input after the statement');
    return { method: 'program', args: pcall.args, kwargs: pcall.kwargs,
             lane: 'progseed', text: text, line: line };
  }
  if (_isName(p, 'p') && _peekOp(p, 1) === '.') {
    _take(p); _take(p);
    var pv = _expectName(p);
    if (!own(PCALLS, pv)) {
      throw _perr(toks[p.i - 1].at, pyRepr(pv) + ' is not a program method (have: ' +
                  programMethods().join(', ') + ')');
    }
    var pargs = _parseArgs(p, pv);
    if (p.i < toks.length) throw _perr(toks[p.i].at, 'trailing input after the statement');
    return { method: pv, args: pargs.args, kwargs: pargs.kwargs,
             lane: 'prog', text: text, line: line };
  }
  if (_isName(p, 'm') && _peekOp(p, 1) === '=') {
    _take(p); _take(p);
    var owner = _expectName(p);
    _expectOp(p, '.');
    method = _expectName(p);
    seed = true;
    if (owner !== 'Machine') throw _perr(toks[0].at, 'only Machine.* seeds a program, not ' + owner);
  } else if (_isName(p, 'd') && _peekOp(p, 1) === '=') {
    _take(p); _take(p);
    method = _expectName(p);
    if (method !== 'DeviceBuilder') throw _perr(toks[0].at, 'only DeviceBuilder builds a device');
  } else if (_isName(p, 'm') && _peekOp(p, 1) === '.') {
    _take(p); _take(p);
    method = _expectName(p);
  } else if (_isName(p, 'd') && _peekOp(p, 1) === '.') {
    _take(p); _take(p);
    method = 'd.' + _expectName(p);
  } else {
    throw _perr(toks.length ? toks[0].at : { line: line, col: 1 },
                'a statement must start with `m = Machine.`, `m.`, `d = DeviceBuilder(` or `d.`');
  }
  // `m = Machine.ring(width=72, ..., template="ring144_24v")` is the TEXT `listing.py`
  // emits for a template seed, while the record it emits beside it says
  // `{"method":"from_template","args":["ring"]}`.  The grammar never knew the text form,
  // so `render(parse(x)) === x` was untestable on 3 of the 4 listing shapes and parsing a
  // template listing died at `'ring' is not an editable method`.  One rewrite, no new
  // production: a generator name in seed position IS a template seed.
  if (seed && !own(CALLS, method) && own(GENERATORS, method)) {
    var tcall = _parseArgs(p, 'from_template');
    if (p.i < toks.length) throw _perr(toks[p.i].at, 'trailing input after the statement');
    return { method: 'from_template', args: [method].concat(tcall.args),
             kwargs: tcall.kwargs, lane: 'arch', text: text, line: line };
  }
  if (!own(CALLS, method)) {
    throw _perr(toks[p.i - 1].at, pyRepr(method) + ' is not an editable method (have: ' +
                methods().join(', ') + ')');
  }
  var call = _parseArgs(p, method);
  if (p.i < toks.length) throw _perr(toks[p.i].at, 'trailing input after the statement');
  return { method: method, args: call.args, kwargs: call.kwargs,
           lane: _kindOf(method) === 'build' ? 'build' : 'arch', text: text, line: line };
}

// exactly the five tokens `d` `.` `build` `(` `)`, and nothing that merely starts that way
function _isDBuild(p) {
  var t = p.toks;
  return !!(t[p.i] && t[p.i].t === 'name' && t[p.i].v === 'd' &&
            t[p.i + 1] && t[p.i + 1].t === 'op' && t[p.i + 1].v === '.' &&
            t[p.i + 2] && t[p.i + 2].t === 'name' && t[p.i + 2].v === 'build' &&
            t[p.i + 3] && t[p.i + 3].t === 'op' && t[p.i + 3].v === '(' &&
            t[p.i + 4] && t[p.i + 4].t === 'op' && t[p.i + 4].v === ')');
}

function _parseArgs(p, method) {
  _expectOp(p, '(');
  var args = [], kwargs = {};
  while (!_isOp(p, ')')) {
    if (_isOp(p, '**')) {
      _take(p);
      var obj = _parseValue(p);
      for (var k in obj) if (own(obj, k)) kwargs[k] = obj[k];
    } else if (p.toks[p.i] && p.toks[p.i].t === 'name' && _peekOp(p, 1) === '=') {
      var key = _take(p).v; _take(p);
      kwargs[key] = _parseValue(p);
    } else if (_isDBuild(p) && (method === 'blank_device' || method === 'from_device')) {
      // `m = Machine.blank_device(d.build(), name=...)` is the text every explicit-mode
      // listing seals with, while the record beside it carries `args: []` -- the builder
      // is threaded through the interpreter's own state, not through the argument list.
      // Without this the parser died at `'d' is not a value` and NO explicit listing
      // parsed at all.  It is five literal tokens, not an expression production, and it
      // is restricted to the two verbs that take a builder so `m.describe(d.build())`
      // stays the syntax error it is.
      p.i += 5;
    } else {
      if (_nonEmpty(kwargs)) {
        throw _perr(p.toks[p.i].at, 'positional argument after keyword argument');
      }
      args.push(_parseValue(p));
    }
    if (_isOp(p, ',')) { _take(p); continue; }
    break;
  }
  _expectOp(p, ')');
  return { args: args, kwargs: kwargs };
}

function _parseValue(p) {
  var t = p.toks[p.i];
  if (!t) throw _perr({ line: 0, col: 0 }, 'expected a value');
  if (t.t === 'str' || t.t === 'num') { _take(p); return t.v; }
  if (t.t === 'name') {
    if (t.v === 'True') { _take(p); return true; }
    if (t.v === 'False') { _take(p); return false; }
    if (t.v === 'None') { _take(p); return null; }
    if (t.v === 'dict') {
      _take(p); _expectOp(p, '(');
      var o = {};
      while (!_isOp(p, ')')) {
        var k = _expectName(p); _expectOp(p, '=');
        o[k] = _parseValue(p);
        if (_isOp(p, ',')) { _take(p); continue; }
        break;
      }
      _expectOp(p, ')');
      return o;
    }
    throw _perr(t.at, pyRepr(t.v) + ' is not a value; the grammar has no names in value ' +
                      'position except True, False, None and dict(...)');
  }
  if (_isOp(p, '[')) {
    _take(p);
    var arr = [];
    while (!_isOp(p, ']')) {
      arr.push(_parseValue(p));
      if (_isOp(p, ',')) { _take(p); continue; }
      break;
    }
    _expectOp(p, ']');
    return arr;
  }
  if (_isOp(p, '{')) {
    _take(p);
    var d = {};
    while (!_isOp(p, '}')) {
      var kt = p.toks[p.i];
      if (!kt || kt.t !== 'str') throw _perr(kt ? kt.at : t.at, 'object keys must be strings');
      _take(p); _expectOp(p, ':');
      d[kt.v] = _parseValue(p);
      if (_isOp(p, ',')) { _take(p); continue; }
      break;
    }
    _expectOp(p, '}');
    return d;
  }
  throw _perr(t.at, 'expected a value, got ' + JSON.stringify(String(t.v)));
}

function _take(p) { return p.toks[p.i++]; }
function _isName(p, v) { var t = p.toks[p.i]; return !!t && t.t === 'name' && t.v === v; }
function _isOp(p, v) { var t = p.toks[p.i]; return !!t && t.t === 'op' && t.v === v; }
function _peekOp(p, k) { var t = p.toks[p.i + k]; return t && t.t === 'op' ? t.v : null; }
function _expectName(p) {
  var t = p.toks[p.i];
  if (!t || t.t !== 'name') throw _perr(t ? t.at : { line: 0, col: 0 }, 'expected a name');
  p.i++; return t.v;
}
function _expectOp(p, v) {
  var t = p.toks[p.i];
  if (!t || t.t !== 'op' || t.v !== v) {
    throw _perr(t ? t.at : { line: 0, col: 0 }, "expected '" + v + "'");
  }
  p.i++;
}

// `_lit` / `_kwd` from listing.py, mirrored.  A key that is not an identifier forces the
// `**{...}` form, which is what the corpus's non-identifier zone names need.
// `_lit` in listing.py: json.dumps for strings (DOUBLE quotes), repr for everything else.
// Deliberately NOT the same function as `pyRepr`, which is Python's repr and single-quotes
// a string -- the two differ on exactly the surface the byte-comparison covers.
function lit(v) {
  if (typeof v === 'string') return _pyStr(v);
  if (Array.isArray(v)) return '[' + v.map(lit).join(', ') + ']';
  if (v !== null && typeof v === 'object' && !(v instanceof PyFloat)) {
    var ps = [];
    for (var k in v) if (own(v, k)) ps.push(lit(k) + ': ' + lit(v[k]));
    return '{' + ps.join(', ') + '}';
  }
  return pyRepr(v);
}
function kwd(d) {
  var keys = [], k;
  for (k in d) if (own(d, k)) keys.push(k);
  var nonIdent = false;
  for (var i = 0; i < keys.length; i++) if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(keys[i])) nonIdent = true;
  if (nonIdent) return '**' + lit(d);
  var parts = [];
  for (i = 0; i < keys.length; i++) parts.push(keys[i] + '=' + lit(d[keys[i]]));
  return parts.join(', ');
}

// Render one statement back to source.  `set_curve` / `set_degree_curve` get the emitter's
// multi-line `dict(...)` layout, because that is what `listing.py` produces and the byte
// comparison is against that.
function renderStmt(s) {
  var method = s.method, args = s.args || [], kw = s.kwargs || {};
  // The programme lane renders through the SAME `lit`/`kwd`, so the round-trip assertion
  // is one assertion over a mixed document rather than two over two.
  if (s.lane === 'prog') return renderProgramStmt(s);
  if (s.lane === 'progseed') {
    var pp = [];
    for (var pi = 0; pi < args.length; pi++) pp.push(lit(args[pi]));
    var ptail = kwd(kw);
    if (ptail) pp.push(ptail);
    return 'p = m.program(' + pp.join(', ') + ')';
  }
  if (method === 'set_curve' || method === 'set_degree_curve') {
    var head = method === 'set_curve'
      ? 'm.set_curve(' + lit(args[0]) + ', ['
      : 'm.set_degree_curve(' + lit(args[0]) + ', ' + lit(args[1]) + ', [';
    var pts = args[method === 'set_curve' ? 1 : 2] || [];
    var rows = [];
    for (var i = 0; i < pts.length; i++) rows.push('    dict(' + kwd(pts[i]) + ')');
    return head + '\n' + rows.join(',\n') + '\n])';
  }
  // `from_template` renders as the GENERATOR's name, which is what `listing.py:455`
  // emits: `m = Machine.ring(width=72, ..., template="ring144_24v")`.  The old chain
  // omitted it entirely and fell through to the mutate form `m.from_template(...)` --
  // a second hand-written list of seeds, disagreeing with the first.
  if (method === 'from_template') {
    var targs = [];
    for (var t = 1; t < args.length; t++) targs.push(lit(args[t]));
    var ttail = kwd(kw);
    if (ttail) targs.push(ttail);
    return 'm = Machine.' + String(args[0]) + '(' + targs.join(', ') + ')';
  }
  var parts = [];
  for (var a = 0; a < args.length; a++) parts.push(lit(args[a]));
  var tail = kwd(kw);
  if (tail) parts.push(tail);
  var sig = parts.join(', ');
  // the two device seals take the builder through the interpreter's state, so their
  // records carry `args: []` while their text carries `d.build()`
  if (method === 'blank_device' || method === 'from_device') {
    return 'm = Machine.' + method + '(d.build()' + (sig ? ', ' + sig : '') + ')';
  }
  if (_kindOf(method) === 'seed') return 'm = Machine.' + method + '(' + sig + ')';
  if (method === 'DeviceBuilder') return 'd = DeviceBuilder(' + sig + ')';
  if (method.indexOf('d.') === 0) return method + '(' + sig + ')';
  return 'm.' + method + '(' + sig + ')';
}

function renderProgram(stmts) {
  var rows = [];
  for (var i = 0; i < stmts.length; i++) rows.push(renderStmt(stmts[i]));
  return rows.join('\n') + '\n';
}

// =====================================================================================
// 9. LINTS -- the things everything accepts and nothing complains about
// =====================================================================================
//
// Measured, not guessed.  Each of these is legal to every setter, passes `check()`, and is
// almost always a mistake:
//
//  * `set_zone(z, capacity=0)` is ACCEPTED, saves clean, and is then silently SKIPPED by
//    `resolve_capacities` (`if cap and cap != node.capacity`), so the zone says 0 and its
//    sites keep their old number, with nothing complaining anywhere in the stack.
//  * `declare_class("x", orbit="NOSUCHLOOP")` is accepted, `architecture_violations`
//    returns [], and the class simply has no participants.
//  * a declared-but-unused zone type is harmless and worth saying out loud.
//
// And the ONE state-free rule check that does exist -- R11/R18/R19: every node the graph made
// a junction must be priceable at its degree.  It is pure, cheap and fully client-side,
// and it is exactly the check a geometry edit needs.  Everything else in `rules.py` takes
// a CycleView built from a PROGRAM, so it cannot run on an architecture edit at all.
function architectureViolations(st) {
  if (!st.device) return [];
  var out = [], deg = degrees(st.device);
  var dc = (st.primitives.degree_curves || {}).junction_cross || {};
  // ONE PER JUNCTION NODE, not one per distinct degree.  `rules.py:architecture_violations`
  // walks `dev.junction_nodes` and emits a Violation for each; deduping by degree here
  // reported 2 where Python reported 77 on grid9x9 and deck_unit_cell.  The verdict agreed
  // -- both said "illegal" -- which is exactly why a verdict-only comparison called it
  // agreement, and why this bucket compares the whole enumeration.
  var ids = junctionNodes(st.device);
  for (var i = 0; i < ids.length; i++) {
    var nid = ids[i], d = deg.get(nid);
    if (!own(dc, d)) {
      out.push({ rule: 'R11', node: nid, degree: d,
                 message: 'node ' + nid + ' has degree ' + d + ' but the architecture ' +
                          'prices no junction_cross at degree ' + d });
    }
  }
  return out;
}

// `str(x)` for the values an orbit can hold.  Python does `str(spec.get("orbit", "any"))`
// unconditionally, so `orbit: null` becomes the string `"None"` -- which matches nothing,
// and must keep matching nothing -- while a MISSING key becomes `"any"`.
function _pyStrOf(v) {
  if (v === null || v === undefined) return 'None';
  if (v === true) return 'True';
  if (v === false) return 'False';
  return String(unbox(v));
}

// `class_participants` (listing.py), mirrored.
//
// This used to be a re-derivation that flattened Python's control flow, and the flattening
// changed the ANSWER, not just its order.  Python makes FOUR ordered probes -- exact
// label over segments, exact over nodes, singular over segments, singular over nodes --
// and sorts every result.  The old mirror made two (segments taking both spellings at
// once, then nodes taking only the exact one, unsorted), so:
//   * `orbit: "docks"` on ring144_24v gave 0 participants where Python gives 24, because
//     the segment pass consumed the singular spelling and could never fall through to the
//     node probe.  A cardinality difference, not an ordering one: `lint()` reads
//     `.length` and so raised `class_no_participants` where Python's `_orbit_warnings`
//     does not;
//   * a class declared with NO orbit key gave 0 where Python gives every site (168 on
//     ring144_24v) -- one click away in the editor's declare_class form;
//   * `"any"` and the node-label branch returned `for..in` insertion order, which differs
//     from Python's `sorted()` on 8 of the 9 shipped architectures.
// So mirror the control flow, not the outcome.
function classParticipants(st, clsId) {
  if (!st.device) return [];
  var cls = null, extra = ((st.control.classes || {}).extra) || [], i;
  for (i = 0; i < extra.length; i++) if (extra[i].id === clsId) cls = extra[i];
  if (!cls) return [];
  var orbit = own(cls, 'orbit') ? _pyStrOf(cls.orbit) : 'any';
  if (own(st.device.loops, orbit)) return st.device.loops[orbit].nodes.slice();
  var nid, sid, out = [];
  if (orbit === 'any') {
    for (nid in st.device.nodes) {
      if (own(st.device.nodes, nid) && st.device.nodes[nid].kind === 'site') out.push(nid);
    }
    return out.sort(_cmpStr);
  }
  // Python's 2-tuple `(orbit, orbit[:-1] if orbit.endswith("s") else orbit)`, duplicate
  // and all -- a second identical probe is a no-op, so the duplicate is harmless and
  // reproducing it is cheaper than reasoning about when it matters.
  var labels = [orbit, /s$/.test(orbit) ? orbit.slice(0, -1) : orbit];
  for (var li = 0; li < labels.length; li++) {
    var label = labels[li], ends = [];
    for (sid in st.device.segments) if (own(st.device.segments, sid)) {
      if ((st.device.segments[sid].labels || []).indexOf(label) >= 0) {
        ends.push(st.device.segments[sid].a); ends.push(st.device.segments[sid].b);
      }
    }
    if (ends.length) return Array.from(new Set(ends)).sort(_cmpStr);
    var ns = [];
    for (nid in st.device.nodes) if (own(st.device.nodes, nid)) {
      if ((st.device.nodes[nid].labels || []).indexOf(label) >= 0) ns.push(nid);
    }
    if (ns.length) return ns.sort(_cmpStr);
  }
  return [];
}

function lint(st) {
  var out = [], z, nid;
  if (!st.device) return out;
  for (z in st.zone_types) if (own(st.zone_types, z)) {
    var cap = Number(unbox(st.zone_types[z].capacity));
    if (cap === 0) {
      out.push({ code: 'zone_capacity_zero', target: 'zone:' + z,
                 message: 'zone ' + pyRepr(z) + ' declares capacity 0, which ' +
                          'resolve_capacities skips: its sites keep their old capacity ' +
                          'and nothing in R1-R18 notices the disagreement' });
    } else if (cap < 0) {
      out.push({ code: 'zone_capacity_negative', target: 'zone:' + z,
                 message: 'zone ' + pyRepr(z) + ' declares capacity ' + cap +
                          '; the schema requires >= 1 and this will be refused on save' });
    }
  }
  var used = {};
  for (nid in st.device.nodes) if (own(st.device.nodes, nid)) {
    var zt = st.device.nodes[nid].zone;
    if (zt) used[zt] = 1;
  }
  for (z in st.zone_types) if (own(st.zone_types, z) && !own(used, z)) {
    out.push({ code: 'zone_unused', target: 'zone:' + z,
               message: 'zone type ' + pyRepr(z) + ' is declared but no site uses it' });
  }
  var extra = ((st.control.classes || {}).extra) || [];
  for (var i = 0; i < extra.length; i++) {
    if (!classParticipants(st, extra[i].id).length) {
      out.push({ code: 'class_no_participants', target: 'class:' + extra[i].id,
                 message: 'movement class ' + pyRepr(extra[i].id) + ' has no participants: ' +
                          'its orbit ' + pyRepr(extra[i].orbit === undefined ? null : extra[i].orbit) +
                          ' matches no loop, label or site' });
    }
  }
  var v = architectureViolations(st);
  for (i = 0; i < v.length; i++) {
    out.push({ code: 'R11', target: 'site:' + v[i].node, message: '[R11] ' + v[i].message });
  }
  return out;
}

// =====================================================================================
// 10. EXPORTS
// =====================================================================================
//
// `allCorners` / `cornerEndpoints` are NOT reimplemented here.  `qccd/viz/js/edit.js`
// already mirrors them and already has a differential test against Python; a second copy
// in this file would be a THIRD implementation of the same truth, which is the exact
// mistake this engine exists to avoid.  edit.js is inlined before engine.js in the page
// and loaded before it in the harness.
function _edit() {
  var E = (typeof globalThis !== 'undefined') ? globalThis.QCCDEdit : null;
  if (!E) {
    throw new EngineError(
      'qccd/viz/js/edit.js must be loaded before qccd/viz/engine.js: the engine ' +
      'delegates degree / corner / corner_endpoints to it rather than keeping a second ' +
      'copy of code that already has a parity test');
  }
  return E;
}
function allCorners(dev) { return _edit().allCorners(dev); }
function cornerEndpoints(dev) {
  var raw = _edit().cornerEndpoints(dev), m = new Map();
  for (var k in raw) if (own(raw, k)) m.set(k, raw[k]);
  return m;
}

// =====================================================================================
// 11. THE PROGRAM LANE -- authoring, lowering, and the two projections of a record list
// =====================================================================================
//
// A program is a list of `{method, args, kwargs}` records, EXACTLY like the architecture.
// That is not an analogy, it is the same mechanism: one applier, one whitelist, one text
// surface, and `render(parse(x)) === x` byte for byte on both.
//
//     ARCH:  [{method:"move_site", args:["S5",3.5,0.0], kwargs:{}}, ...]
//     PROG:  [{method:"init",      args:[{d0:"S3"}],     kwargs:{}}, ...]
//
// FRAMES AND TSIR ARE TWO PROJECTIONS OF THE RECORDS, never of each other.  A frame
// carries a NODE PATH; a TSIR participant carries `via` SEGMENT IDS; and the map from a
// path back to segments is ambiguous on a multigraph.  Converting frames to TSIR would be
// a lossy inverse dressed as a conversion, so both projections read the records.
//
// `frames` is byte-for-byte the shape `qccd/viz/render.py` builds from a replayed TSIR --
// same keys, same order -- which is what makes this lane free: `priceFrames`,
// `validateProgram`, `checkFrames`, `pathsOf` and the stage need ZERO changes to consume
// an authored program.

//: The authoring vocabulary.  Mirrors `qccd.api.PROGRAM_METHODS`, which is itself derived
//: from `Program`'s public methods -- and `tests/test_engine_parity.py` asserts the two
//: sets are equal, so the parser cannot advertise a verb the lowerer lacks.  That drift is
//: invisible to every differential bucket, because Python would never emit the verb.
var PCALLS = {
  init: _pInit, fill: _pFill, rotate: _pRotate, simd: _pSimd, move: _pMove,
  shuttle: _pShuttle, gate: _pGate, cool: _pCool, measure: _pMeasure, reset: _pReset,
  barrier: _pBarrier, claim: _pClaim
};
function programMethods() { return Object.keys(PCALLS).sort(_cmpStr); }

// `Program.<verb>` raises ValueError/KeyError BEFORE any replay for these; the browser
// reproduces the message text because the page's standard is that its error strip says
// what `python -m qccd` would say.
function ProgError(msg, code) {
  var e = new EngineError(msg);
  e.name = 'ProgramError';
  e.code = code || 'ValueError';
  return e;
}

function _pctx(dev, loops, opts) {
  opts = opts || {};
  return {
    dev: dev, loops: loops || {}, name: opts.name || 'device',
    classes: opts.classes || {}, defaultLoop: opts.defaultLoop || null,
    pair: pairIndex(dev), frames: [], claims: {}, ids: [],
    idSeq: opts.idSeq === undefined ? 0 : Math.trunc(Number(opts.idSeq)), call: 0
  };
}

// `Machine.default_loop`: the first CLOSED loop, else the first loop, else a refusal.
function _defaultLoop(ctx) {
  if (ctx.defaultLoop && own(ctx.loops, ctx.defaultLoop)) return ctx.defaultLoop;
  var lid, first = null;
  for (lid in ctx.loops) if (own(ctx.loops, lid)) {
    if (first === null) first = lid;
    if (ctx.dev.loops[lid] && ctx.dev.loops[lid].closed) return lid;
  }
  if (first !== null) return first;
  throw ProgError(ctx.name + ' has no transport loop');
}

function _nextId(ctx) { var i = ctx.idSeq; ctx.idSeq = i + 1; return i; }

function _emitFrame(ctx, f) {
  f.id = _nextId(ctx);
  f.call = ctx.call;
  ctx.frames.push(f);
  ctx.ids.push(f.id);
  return f;
}

// `arch.entails(cls)` read from the LIVE class table, never baked.  `render.py` bakes it
// at emit time, and after `declare_class("dock", entails=[])` the baked list is a lie --
// the engine already reads the table when pricing, and lowering re-runs on every
// architecture edit, so an authored programme's `entails` can never go stale.
function _entailsOf(ctx, cls) {
  if (!cls || !own(ctx.classes, cls)) return [];
  var e = ctx.classes[cls].entails;
  return e ? e.slice() : [];
}

function _pInit(ctx, args, kw) {
  var place = args[0] || {}, unknown = [], ion;
  for (ion in place) if (own(place, ion)) {
    if (!own(ctx.dev.nodes, place[ion])) unknown.push(place[ion]);
  }
  if (unknown.length) {
    throw ProgError('no such node(s) on ' + ctx.name + ': ' +
                    pyRepr(unknown.slice(0, 5)));
  }
  var p = {};
  for (ion in place) if (own(place, ion)) p[ion] = String(place[ion]);
  _emitFrame(ctx, { type: 'init', cls: null, mode: null, place: p });
}

function _pFill(ctx, args, kw) {
  var loop = args[0] === undefined ? (kw.loop === undefined ? null : kw.loop) : args[0];
  loop = (loop === null || loop === undefined) ? _defaultLoop(ctx) : String(loop);
  if (!own(ctx.loops, loop)) throw ProgError(String(loop), 'KeyError');
  var prefix = args[1] === undefined ? (kw.prefix === undefined ? 'd' : String(kw.prefix))
                                     : String(args[1]);
  var nodes = ctx.loops[loop], place = {};
  for (var i = 0; i < nodes.length; i++) place[prefix + i] = nodes[i];
  _pInit(ctx, [place], {});
}

function _pRotate(ctx, args, kw) {
  var delta = Math.trunc(Number(unbox(args[0] === undefined ? kw.delta : args[0])));
  var loop = args[1] === undefined ? (kw.loop === undefined ? null : kw.loop) : args[1];
  loop = (loop === null || loop === undefined) ? _defaultLoop(ctx) : String(loop);
  if (!own(ctx.dev.loops, loop)) {
    throw ProgError('no loop ' + pyRepr(loop) + ' on ' + ctx.name);
  }
  if (!ctx.dev.loops[loop].closed && delta) {
    throw ProgError('loop ' + pyRepr(loop) + ' is open; a rigid rotation is undefined');
  }
  var cls = args[2] === undefined ? (kw.cls === undefined ? null : kw.cls) : args[2];
  // THE DIRECTION IS IN THE CLASS NAME AND NOWHERE ELSE.  Both `rotate_cw` and
  // `rotate_ccw` declare `entails: ()`, so every total, every per-ion quantum and
  // `validateProgram` are blind to getting this wrong -- measured: identical cost, steps,
  // us and per-ion n-bar under a planted `cls: "rotate_cw"`.  Python is not blind
  // (`MNEMONIC_BY_CLASS`, `prog.templates()` and `Instruction.cls` all differ), which is
  // why the `prog` parity bucket diffs FRAMES field by field rather than totals.
  cls = (cls === null || cls === undefined) ? (delta >= 0 ? 'rotate_cw' : 'rotate_ccw')
                                            : String(cls);
  _emitFrame(ctx, { type: 'simd', cls: cls, mode: 'inter', shift: [loop, delta],
                    kind: 'rotate', hops: Math.abs(delta) });
}

// `via` (segment ids) -> the node path a frame carries.  `render.py::_node_path` walks
// `segments[sid].other(node)`; the refusal when a segment is not incident is an AUTHORING
// error, not a reason to fall back to `[src, dst]` -- a silent fallback would price a
// route the machine cannot drive.
function _nodePath(ctx, ion, src, dst, via) {
  if (!via || !via.length) return [String(src), String(dst)];
  var path = [String(src)], node = String(src);
  for (var i = 0; i < via.length; i++) {
    var sid = String(via[i]), s = ctx.dev.segments[sid];
    if (!s) throw ProgError(sid, 'KeyError');
    if (s.a === node) node = s.b;
    else if (s.b === node) node = s.a;
    else {
      throw ProgError('segment ' + pyRepr(sid) + ' is not incident on ' + pyRepr(node) +
                      ', so the route of ' + pyRepr(ion) + ' does not connect');
    }
    path.push(node);
  }
  if (node !== String(dst)) {
    throw ProgError('the route of ' + pyRepr(ion) + ' ends at ' + pyRepr(node) +
                    ' but was declared to end at ' + pyRepr(String(dst)));
  }
  return path;
}

function _cycleFrame(ctx, cls, mode, items) {
  if (mode !== 'intra' && mode !== 'inter') {
    throw ProgError("mode must be 'intra' or 'inter', not " + pyRepr(mode) + ' (R4b)');
  }
  if (!items || !items.length) {
    throw ProgError('cycle ' + pyRepr(cls) + ' moves nothing');
  }
  var moves = [], declared = [];
  for (var i = 0; i < items.length; i++) {
    var it = items[i];
    moves.push([String(it[0]), _nodePath(ctx, it[0], it[1], it[2], it[3])]);
    // WHAT THE STATEMENT DECLARED, kept beside what it lowered to.  A move with no `via`
    // is `via=()` in Python and the TSIR omits the key; re-deriving it from the node path
    // would write a `via` the author never wrote, and the exported file would differ from
    // a Python-built one on a programme that is otherwise identical.
    declared.push(it[3] === undefined || it[3] === null ? null : it[3].map(String));
  }
  _emitFrame(ctx, { type: 'simd', cls: String(cls), mode: mode, moves: moves,
                    entails: _entailsOf(ctx, String(cls)), _via: declared });
}

function _pSimd(ctx, args, kw) {
  var cls = args[0] === undefined ? kw.cls : args[0];
  var moves = args[1] === undefined ? (kw.moves || []) : args[1];
  var mode = args[2] === undefined ? (kw.mode === undefined ? 'inter' : kw.mode) : args[2];
  _cycleFrame(ctx, cls, String(mode), moves);
}

function _pMove(ctx, args, kw) {
  var ion = args[0] === undefined ? kw.ion : args[0];
  var src = args[1] === undefined ? kw.src : args[1];
  var dst = args[2] === undefined ? kw.dst : args[2];
  var via = args[3] === undefined ? (kw.via === undefined ? null : kw.via) : args[3];
  var cls = args[4] === undefined ? (kw.cls === undefined ? 'shuttle' : kw.cls) : args[4];
  _cycleFrame(ctx, cls, 'inter', [[ion, src, dst, via]]);
}

function _pShuttle(ctx, args, kw) {
  var ion = String(args[0] === undefined ? kw.ion : args[0]);
  var path = args[1] === undefined ? (kw.path || []) : args[1];
  var cls = args[2] === undefined ? (kw.cls === undefined ? 'shuttle' : kw.cls) : args[2];
  var stops = [], idx = {}, i;
  for (i = 0; i < path.length; i++) {
    var nid = String(path[i]);
    if (!own(ctx.dev.nodes, nid)) throw ProgError(nid, 'KeyError');
    idx[nid] = i;
    if (ctx.dev.nodes[nid].kind === 'site') stops.push(nid);
  }
  for (i = 0; i + 1 < stops.length; i++) {
    var a = stops[i], b = stops[i + 1];
    var hop = path.slice(idx[a], idx[b] + 1).map(String), via = [];
    for (var h = 0; h + 1 < hop.length; h++) {
      var sid = ctx.pair[hop[h] + '>' + hop[h + 1]];
      if (sid === undefined) {
        throw ProgError('no segment between ' + pyRepr(hop[h]) + ' and ' +
                        pyRepr(hop[h + 1]), 'KeyError');
      }
      via.push(sid);
    }
    _cycleFrame(ctx, cls, 'inter', [[ion, a, b, via]]);
  }
}

function _pGate(ctx, args, kw) {
  var name = String(args[0] === undefined ? kw.name : args[0]);
  var pairs = args[1] === undefined ? (kw.pairs || []) : args[1];
  var sites = args[2] === undefined ? (kw.sites === undefined ? [] : kw.sites) : args[2];
  var pp = [];
  for (var i = 0; i < pairs.length; i++) pp.push([String(pairs[i][0]), String(pairs[i][1])]);
  _emitFrame(ctx, { type: 'gate', cls: null, mode: 'intra', pairs: pp,
                    sites: (sites || []).map(String), gate: name });
}

function _pCool(ctx, args, kw) {
  var ions = args[0] === undefined ? (kw.ions === undefined ? null : kw.ions) : args[0];
  var list = (ions || []).map(String);
  _emitFrame(ctx, { type: 'cool', cls: null, mode: null,
                    broadcast: !list.length, ions: list });
}

function _pMeasure(ctx, args, kw) {
  var ions = (args[0] === undefined ? (kw.ions || []) : args[0]).map(String);
  _emitFrame(ctx, { type: 'measure', cls: null, mode: null, ions: ions });
}

function _pReset(ctx, args, kw) {
  var ions = (args[0] === undefined ? (kw.ions || []) : args[0]).map(String);
  _emitFrame(ctx, { type: 'reset', cls: null, mode: null, ions: ions });
}

function _pBarrier(ctx, args, kw) {
  _emitFrame(ctx, { type: 'barrier', cls: null, mode: null });
}

// `claim` emits NO frame -- it is R9's subject, an assertion about what the replay should
// produce.  The browser records it and the report says whether it was checked.
function _pClaim(ctx, args, kw) {
  for (var k in kw) if (own(kw, k)) ctx.claims[k] = unbox(kw[k]);
}

// ---------------------------------------------------------------- provenance mirror
//
// `qccd/ir/provenance.py::_jsonable` summarizes any mapping or sequence over
// `_MAX_ITEMS = 32` rather than copying it, and truncates any other repr at
// `_MAX_REPR = 120`.  MEASURED: `p.fill("L0")` on ring144_24v logs
// `args: {"placement": "<144 entries>"}`, not 144 pairs.  A JS log that wrote the full
// mapping would produce an exported `.tsir.json` that differs from a Python-built one for
// precisely the programmes anyone runs.
var PROV_MAX_ITEMS = 32, PROV_MAX_REPR = 120;

function _provArg(v, depth) {
  depth = depth || 0;
  if (v === null || v === true || v === false) return v;
  if (typeof v === 'number' || typeof v === 'string') return v;
  if (v instanceof PyFloat) return v.v;
  if (Array.isArray(v)) {
    if (depth >= 2 || v.length > PROV_MAX_ITEMS) return '<' + v.length + ' items>';
    var a = [];
    for (var i = 0; i < v.length; i++) a.push(_provArg(v[i], depth + 1));
    return a;
  }
  if (typeof v === 'object') {
    var keys = Object.keys(v);
    if (depth >= 2 || keys.length > PROV_MAX_ITEMS) return '<' + keys.length + ' entries>';
    var o = {};
    for (var j = 0; j < keys.length; j++) o[keys[j]] = _provArg(v[keys[j]], depth + 1);
    return o;
  }
  return String(v).slice(0, PROV_MAX_REPR);
}

//: The positional parameter names of each verb, so a call record logs `{"placement": ...}`
//: rather than `{"0": ...}` -- the same names `@_traced` reads off the Python signature.
var PARG_NAMES = {
  init: ['placement', 'quanta'], fill: ['loop', 'prefix'],
  rotate: ['delta', 'loop', 'cls'], simd: ['cls', 'moves', 'mode'],
  move: ['ion', 'src', 'dst', 'via', 'cls'], shuttle: ['ion', 'path', 'cls'],
  gate: ['name', 'pairs', 'sites'], cool: ['ions'], measure: ['ions'], reset: ['ions'],
  barrier: [], claim: []
};

function lowerProgram(stmts, dev, loops, opts) {
  opts = opts || {};
  var ctx = _pctx(dev, loops, opts);
  var prov = { version: schemaVersion(), level: 'calls', root: '',
               sites: [], calls: [] };
  var errors = [];
  for (var i = 0; i < stmts.length; i++) {
    var s = stmts[i] || {};
    var method = String(s.method || '');
    if (s.lane && s.lane !== 'prog') {
      errors.push({ i: i, code: 'wrong_lane', method: method,
                    message: pyRepr(method) + ' is an architecture statement; ' +
                             'a programme is written with `p.` verbs (have: ' +
                             programMethods().join(', ') + ')' });
      continue;
    }
    if (!own(PCALLS, method)) {
      errors.push({ i: i, code: 'unknown_method', method: method,
                    message: pyRepr(method) + ' is not a program method (have: ' +
                             programMethods().join(', ') + ')' });
      continue;
    }
    var args = (s.args || []).slice(), kw = s.kwargs || {};
    // the call log entry FIRST, so a frame emitted by this statement points at it
    ctx.call = prov.calls.length;
    var names = PARG_NAMES[method] || [], logged = {}, k;
    for (k = 0; k < args.length; k++) {
      if (names[k]) logged[names[k]] = _provArg(args[k]);
    }
    for (k in kw) if (own(kw, k)) logged[k] = _provArg(kw[k]);
    var site = prov.sites.length;
    prov.sites.push({ file: '<authored>', line: s.line === undefined ? i + 1 : s.line,
                      text: s.text === undefined ? renderProgramStmt(s) : s.text,
                      func: 'program' });
    prov.calls.push({ op: method, site: site, args: logged });
    var before = ctx.frames.length;
    try {
      PCALLS[method](ctx, args, kw);
    } catch (err) {
      // roll the statement's partial frames back: a refused statement must leave the
      // programme as it was, exactly as a refused architecture edit does
      ctx.frames.length = before;
      ctx.ids.length = before;
      // `str(KeyError(x))` is `repr(x)`, not `x` -- Python's own message carries the
      // quotes, and an error strip that dropped them would print a sentence the toolchain
      // never says.
      errors.push({ i: i, code: err.code || 'ValueError', method: method,
                    message: err.code === 'KeyError' ? pyRepr(err.message) : err.message });
    }
  }
  return { frames: ctx.frames, prov: prov, errors: errors, idSeq: ctx.idSeq,
           claims: ctx.claims, ids: ctx.ids };
}

// ---------------------------------------------------------------- TSIR projection
//
// `TSIR.to_json()`'s exact shape, so the file the browser downloads is the file
// `TSIR.load` reads.  Built from the RECORDS, not from the frames: a frame carries a node
// path and a participant carries `via` segment ids, and the path -> segments direction is
// ambiguous on a multigraph.
function programToTsir(stmts, dev, loops, opts) {
  opts = opts || {};
  var low = lowerProgram(stmts, dev, loops, opts);
  var instructions = framesToInstructions(low.frames, dev, opts.classes || {});
  var doc = {
    version: schemaVersion(),
    name: opts.name === undefined ? 'authored' : String(opts.name),
    arch_spec: opts.archSpec === undefined ? '' : String(opts.archSpec),
    instructions: instructions,
    metrics: _deep(low.claims),
    meta: { prov: low.prov },
    id_seq: low.idSeq
  };
  return { doc: doc, errors: low.errors, frames: low.frames, prov: low.prov };
}

//: The frame -> Instruction inversion.  `moves:[[ion,path]]` becomes
//: `participants:[{ion,src,dst,via}]` with `via` resolved through `pairIndex`, and
//: `shift:[loop,delta]` becomes `template:{kind:"loop_shift",loop,delta}`.  Every other
//: field is a rename.  Used by BOTH `programToTsir` (authored) and `framesToTsir` (the
//: escape hatch for a page whose programme came from Python).
function framesToInstructions(frames, dev, classes) {
  var pair = pairIndex(dev), out = [];
  for (var i = 0; i < frames.length; i++) {
    var f = frames[i], d = { type: f.type, id: f.id === undefined ? i : f.id };
    if (f.cls !== null && f.cls !== undefined) d['class'] = f.cls;
    if (f.mode !== null && f.mode !== undefined) d.mode = f.mode;
    if (f.shift) {
      d.template = { kind: 'loop_shift', loop: f.shift[0], delta: Math.trunc(Number(f.shift[1])) };
    }
    if (f.moves && f.moves.length) {
      var parts = [];
      for (var m = 0; m < f.moves.length; m++) {
        var ion = f.moves[m][0], path = f.moves[m][1], via = [];
        // an AUTHORED frame remembers what its statement declared; a frame that arrived
        // from Python does not, so its route is re-derived from the node path
        var decl = f._via ? f._via[m] : undefined;
        if (decl !== undefined) via = decl === null ? [] : decl.slice();
        else for (var h = 0; h + 1 < path.length; h++) {
          var sid = pair[path[h] + '>' + path[h + 1]];
          if (sid === undefined) {
            throw new EngineError('no segment between ' + pyRepr(path[h]) + ' and ' +
                                  pyRepr(path[h + 1]) + ', so this frame cannot be ' +
                                  'written back as a TSIR participant');
          }
          via.push(sid);
        }
        // `Participant.to_json` writes `from` / `to`, not `src` / `dst`; the dataclass
        // field names and the wire names differ and `TSIR.from_json` reads the WIRE ones.
        var p = { ion: ion, from: path[0], to: path[path.length - 1] };
        if (via.length) p.via = via;
        parts.push(p);
      }
      d.participants = parts;
    }
    if (f.gate) d.gate = f.gate;
    if (f.pairs && f.pairs.length) d.pairs = f.pairs.map(function (x) { return x.slice(); });
    if (f.ions && f.ions.length) d.ions = f.ions.slice();
    if (f.sites && f.sites.length) d.sites = f.sites.slice();
    if (f.broadcast) d.broadcast = true;
    if (f.place) d.placement = _shallow(f.place);
    if (f.place) {
      var q = {};
      for (var k in f.place) if (own(f.place, k)) q[k] = 0.0;
      d.quanta = q;
    }
    var meta = {};
    if (f.kind !== undefined) meta.kind = f.kind;
    if (f.hops !== undefined) meta.hops = f.hops;
    if (f.call !== undefined) meta.call = f.call;
    if (_nonEmpty(meta)) d.meta = meta;
    out.push(d);
  }
  return out;
}

function framesToTsir(frames, dev, classes, name, archSpec) {
  return { version: schemaVersion(), name: String(name || 'exported'),
           arch_spec: String(archSpec || ''),
           instructions: framesToInstructions(frames, dev, classes || {}),
           metrics: {}, meta: {},
           id_seq: frames.length ? Math.max.apply(null,
             frames.map(function (f, i) { return f.id === undefined ? i : f.id; })) + 1 : 0 };
}

// ---------------------------------------------------------------- the text surface
//
// `p.NAME(args)`, built on the SAME `lit()` / `kwd()` the architecture lane uses, so
// `renderProgramSource(parse(x).prog) === x` byte for byte holds by construction rather
// than by a second emitter that happens to agree.
function renderProgramStmt(s) {
  var parts = [], i;
  for (i = 0; i < (s.args || []).length; i++) parts.push(lit(s.args[i]));
  var kw = kwd(s.kwargs || {});
  if (kw) parts.push(kw);
  return 'p.' + s.method + '(' + parts.join(', ') + ')';
}

function renderProgramSource(stmts) {
  var out = [];
  for (var i = 0; i < stmts.length; i++) out.push(renderProgramStmt(stmts[i]));
  return out.join('\n') + (out.length ? '\n' : '');
}

// =====================================================================================
// 12. THE BROWSER RULE SET -- one walk, two surfaces
// =====================================================================================
//
// `qccd/verify/rules.py` has 25 rules.  Exactly one of them -- `architecture_violations`,
// R11's structural half -- is state-free; the other 22 need a `CycleView` built from a
// replay.  `priceFrames` ALREADY IS that replay: it carries `pos` (every ion's site), `q`
// (running n-bar), `life` (lifetime deposit) and per-frame cost/steps/us, and it is
// parity-checked against Python at 349,424 comparisons and zero mismatches.
//
// So this is NOT a second replay.  `priceFrames` gained an `onCycle` hook and hands the
// SAME window object to the rule pass that it uses for pricing -- which is exactly what
// `replay(on_cycle=...)` does on the Python side, and for exactly the same reason: a panel
// and a verifier that derive occupancy separately WILL eventually disagree, and the one
// that is wrong is the one nobody is testing.
//
// THREE THINGS THAT ARE LOAD-BEARING AND WERE MEASURED WRONG IN A PROTOTYPE:
//
//  1. R1/R2/R8 RUN ON GATE, COOL, MEASURE AND RESET CYCLES TOO.  Python builds a
//     `CycleView` for every instruction, not only transport ones.  A transport-only mirror
//     reported 864 where Python reported 1,728 -- with the VERDICT identical both times --
//     and would report "R1 passed" on a programme made entirely of gates, which is
//     precisely the programme a user writing a first test programme produces.
//
//  2. R1 ENUMERATES EVERY OVER-CAPACITY NODE, not only ones that gained an ion this
//     cycle.  Python re-reports a node every cycle it stays over capacity.  Same 864 vs
//     1,728, different cause.  Maintained here as an incremental over-set so it stays
//     O(moves) rather than O(nodes) per cycle.
//
//  3. THE QUANTA PHASE.  `replay.py` snapshots `quanta_at_start` BEFORE a non-transport
//     cycle's own charge and anomalous term, but uses POST-cycle quanta for transport.  A
//     mirror that reads `q` after `_anom` on the gate frame disagrees by one cycle's
//     background heating -- invisible in every total, and R7's budget is 1.0 quanta
//     against arrivals at 22.5, so it flips verdicts.
//
// THE PARITY BUCKET COMPARES COUNTS AND MESSAGE MULTISETS, NEVER VERDICTS.  `engine.js`
// already carries the reason in `architectureViolations`: deduping by degree reported 2
// where Python reported 77, "the verdict agreed -- both said illegal -- which is exactly
// why a verdict-only comparison called it agreement."

//: The rules this file dispatches.  DERIVED FROM THE DISPATCHER, never a hand list: a rule
//: that is advertised-but-undispatchable, or dispatchable-but-unadvertised, is impossible
//: rather than merely tested against.  `render.py::BROWSER_SET` is asserted equal to this.
var RULE_FNS = {
  R1: _r1, R2: _r2, R3: _r3, R4: _r4, R4b: _r4b, R5: _r5, R6: _r6, R6b: _r6b,
  R7: _r7, R8: _r8, R11: _r11, R12: _r12, R13: _r13, R14: _r14
};
//: Checked BY CONSTRUCTION rather than by a per-cycle function, exactly as
//: `qccd/verify/__init__.py` marks them: R17's background term is deposited by `_anom` on
//: every cycle with a duration (and is bit-compared in the pricing bucket), R18 is enforced
//: by `makeModel.jpoint`, which THROWS for a degree the architecture cannot price, and R7c
//: is a program-level check over the whole frame list.
var RULE_CONSTRUCTED = ['R7c', 'R17', 'R18'];
function mirroredRules() {
  return Object.keys(RULE_FNS).concat(RULE_CONSTRUCTED).sort(_cmpStr);
}

function _v(rule, id, message) { return { rule: rule, instr_id: id, message: message }; }

// ------------------------------------------------------------------------ the rules

function _r1(w) {
  var out = [], i;
  // EVERY over-capacity site, not only the ones this cycle touched.
  for (i = 0; i < w.over.length; i++) {
    var nid = w.over[i];
    out.push(_v('R1', w.id, 'site ' + nid + ' holds ' + w.occ[nid] + ' ions but capacity is ' +
                w.cap[nid] + '; a rebalance must be scheduled explicitly'));
  }
  for (var tn in w.transits) if (own(w.transits, tn)) {
    var node = w.dev.nodes[tn];
    if (!node || node.kind !== 'site') continue;      // a junction holds no ions; R2 governs it
    var ob = w.occBefore[tn] === undefined ? (w.occ[tn] || 0) : w.occBefore[tn];
    var resident = Math.max(ob, w.occ[tn] || 0);
    if (resident + w.transits[tn] > node.cap) {
      out.push(_v('R1', w.id, 'ROADBLOCK: ' + w.transits[tn] + ' ion(s) transit site ' + tn +
                  ', which already holds ' + resident + ' of its ' + node.cap +
                  '; there is no room to pass through and the route must go around or wait'));
    }
  }
  return out;
}

function _r2(w) {
  var out = [], i;
  for (i = 0; i < w.overJ.length; i++) {
    var nid = w.overJ[i];
    out.push(_v('R2', w.id, 'junction ' + nid + ' (degree ' + w.deg(nid) + ') holds ' +
                w.occ[nid] + ' ions'));
  }
  var entering = {};
  for (i = 0; i < w.nmv; i++) {
    var m = w.moves[i];
    if (w.deg(m.dst) >= 3) entering[m.dst] = (entering[m.dst] || 0) + 1;
  }
  for (var d in entering) if (own(entering, d)) {
    if (entering[d] > 1) {
      out.push(_v('R2', w.id, entering[d] + ' ions cross junction ' + d + ' in one cycle'));
    }
  }
  return out;
}

function _r3(w) {
  var use = {}, out = [], i;
  for (i = 0; i < w.nmv; i++) {
    var sid = w.moves[i].seg;
    use[sid] = (use[sid] || 0) + 1;
  }
  for (var s in use) if (own(use, s)) {
    var cap = w.dev.segments[s].cap;
    if (use[s] > cap) {
      out.push(_v('R3', w.id, 'segment ' + s + ' carries ' + use[s] +
                  ' ions but capacity is ' + cap));
    }
  }
  return out;
}

function _r4(w) {
  if (w.type !== 'simd') return [];
  var out = [], declared = false, k;
  for (k in w.classes) if (own(w.classes, k)) { declared = true; break; }
  if (w.cls !== null && w.cls !== undefined && declared && !own(w.classes, w.cls)) {
    out.push(_v('R4', w.id, 'movement class ' + pyRepr(w.cls) +
                ' is not declared by the architecture'));
  }
  if (1 > w.maxSimd) {
    out.push(_v('R4', w.id, 'cycle uses 1 class but the limit is ' + w.maxSimd));
  }
  return out;
}

function _r4b(w) {
  if (w.type !== 'simd') return [];
  var out = [];
  if (w.mode !== 'intra' && w.mode !== 'inter') {
    out.push(_v('R4b', w.id, 'transport cycle has mode ' + pyRepr(w.mode === undefined ? null : w.mode)));
  }
  if ((w.f.pairs && w.f.pairs.length) || w.f.gate) {
    out.push(_v('R4b', w.id, 'a cycle carries both transport and gates: intra- and ' +
                'inter-trap control pathways are distinct and cannot overlap'));
  }
  return out;
}

function _r5(w) {
  // A TWO-BIT DIRECTION MASK PER SEGMENT.  Bit 0 is a->b, bit 1 is b->a, and an exchange
  // is both bits set -- the same answer a set of (src, dst) string keys gives, at a
  // fraction of the cost.  This runs once per move of every cycle: 648,000 evaluations on
  // the shipped deck programme, where the difference between building a string key and
  // OR-ing an integer is most of the rule pass.
  var dir = w._dir, out = [], i, m, sid;
  for (sid in dir) if (own(dir, sid)) delete dir[sid];
  for (i = 0; i < w.nmv; i++) {
    m = w.moves[i];
    var bit = w.dev.segments[m.seg].a === m.src ? 1 : 2;
    dir[m.seg] = (dir[m.seg] || 0) | bit;
  }
  for (sid in dir) if (own(dir, sid)) {
    if (dir[sid] === 3) {
      out.push(_v('R5', w.id, 'ions exchange positions across segment ' + sid +
                  ' in one step'));
    }
  }
  return out;
}

// `Architecture.can(node, capability)`: False when the node has no zone type, else the
// zone's flag.  Python RAISES when the zone type is not declared -- which is why the
// loader now refuses such a document outright, so this can never be reached with one.
function _can(w, site, capability) {
  var n = w.dev.nodes[site];
  if (!n || n.zone === null || n.zone === undefined) return false;
  var z = w.zoneTypes[n.zone];
  if (!z) return false;
  return !!z[capability];
}

// `CycleView.gate_sites()`: the REPLAYED positions of the operands, then the declared
// `sites`.  Derived from where the ions actually are, not from what the programme claims
// about itself -- a check driven by a claim can be switched off by omitting the claim.
function _gateSites(w) {
  var out = [], i, j, pr = w.f.pairs || [];
  for (i = 0; i < pr.length; i++) {
    for (j = 0; j < 2; j++) {
      var site = w.posBefore[pr[i][j]];
      if (site !== undefined && out.indexOf(site) < 0) out.push(site);
    }
  }
  var sites = w.f.sites || [];
  for (i = 0; i < sites.length; i++) if (out.indexOf(sites[i]) < 0) out.push(sites[i]);
  return out;
}

function _r6(w) {
  var out = [], i, site;
  if (w.type === 'gate') {
    var gs = _gateSites(w);
    for (i = 0; i < gs.length; i++) {
      if (!_can(w, gs[i], 'gate')) {
        var n = w.dev.nodes[gs[i]];
        out.push(_v('R6', w.id, 'gate at ' + gs[i] + ' whose zone type ' +
                    pyRepr(n && n.zone !== undefined ? n.zone : null) + ' has gate=false'));
      }
    }
  } else if (w.type === 'measure' || w.type === 'reset') {
    var ions = w.f.ions || [];
    for (i = 0; i < ions.length; i++) {
      site = w.posBefore[ions[i]];
      if (site === undefined) out.push(_v('R6', w.id, w.type + ' on unplaced ion ' + ions[i]));
      else if (!_can(w, site, 'spam')) {
        out.push(_v('R6', w.id, w.type + ' at ' + site + ': no spam capability'));
      }
    }
  } else if (w.type === 'cool') {
    var cs = (w.f.ions && w.f.ions.length) ? w.f.ions : Object.keys(w.posBefore);
    for (i = 0; i < cs.length; i++) {
      site = w.posBefore[cs[i]];
      if (site === undefined) out.push(_v('R6', w.id, 'cool on unplaced ion ' + cs[i]));
      else if (!_can(w, site, 'cool')) {
        out.push(_v('R6', w.id, 'cool at ' + site + ': no cool capability'));
      }
    }
  }
  return out;
}

function _r6b(w) {
  if (w.type !== 'gate') return [];
  var out = [], declared = (w.f.sites || []).slice(), pr = w.f.pairs || [];
  for (var i = 0; i < pr.length; i++) {
    var a = pr[i][0], b = pr[i][1];
    var sa = w.posBefore[a], sb = w.posBefore[b];
    if (sa === undefined || sb === undefined) {
      out.push(_v('R6b', w.id, 'gate on unplaced ion(s) ' + a + ', ' + b));
      continue;
    }
    if (sa !== sb) {
      out.push(_v('R6b', w.id, '2Q gate on ' + a + '@' + sa + ' and ' + b + '@' + sb +
                  ': not co-located'));
    } else if (declared.length && declared.indexOf(sa) < 0) {
      var uniq = [];
      for (var d = 0; d < declared.length; d++) if (uniq.indexOf(declared[d]) < 0) uniq.push(declared[d]);
      out.push(_v('R6b', w.id, 'gate on ' + a + ',' + b + ' happens at ' + sa +
                  ' but the instruction declares ' + pyRepr(uniq.slice().sort(_cmpStr))));
    }
  }
  return out;
}

function _r7(w) {
  if (w.type !== 'gate') return [];
  var out = [], budget = w.gateBudget, pr = w.f.pairs || [];
  for (var i = 0; i < pr.length; i++) {
    for (var j = 0; j < 2; j++) {
      var ion = pr[i][j], n = w.quanta[ion] || 0.0;
      if (n > budget) {
        out.push(_v('R7', w.id, 'ion ' + ion + ' enters a 2Q gate at n-bar=' +
                    _q(n, 3).toFixed(3) + ' > ' + _pyFloatRepr(budget) +
                    '; a cooling operation must precede it'));
      }
    }
  }
  return out;
}

function _r8(w) {
  var out = [], ion, i;
  // THE ION SET may not change outside load/unload.  `w.posBefore` is SPARSE on a
  // transport cycle -- it holds only the ions that moved, because copying every ion's
  // position once per cycle is 648k property copies on the shipped deck programme and
  // buys nothing: no cycle can REMOVE an ion, so "lost" is empty by construction and
  // "new" is exactly the set `priceFrames` saw a move introduce.
  if (w.added && w.added.length) {
    out.push(_v('R8', w.id, 'ion set changed outside load/unload (lost ' + pyRepr([]) +
                ', new ' + pyRepr(w.added.slice().sort(_cmpStr).slice(0, 5)) + ')'));
  }
  var once = {};
  for (i = 0; i < w.participants.length; i++) {
    var p = w.participants[i];
    once[p] = (once[p] || 0) + 1;
  }
  for (ion in once) if (own(once, ion)) {
    if (once[ion] > 1) {
      out.push(_v('R8', w.id, 'ion ' + ion + ' is a participant ' + once[ion] +
                  ' times in one cycle; the ion->site map would not be a function'));
    }
  }
  // the declared origin is the FIRST resolved hop's source, not the last: a participant
  // whose route crosses several segments produces several moves and the intermediate
  // nodes are not where the ion started
  var origin = {};
  for (i = 0; i < w.nmv; i++) {
    if (!own(origin, w.moves[i].ion)) origin[w.moves[i].ion] = w.moves[i].src;
  }
  for (ion in w.posBefore) if (own(w.posBefore, ion)) {
    var was = w.posBefore[ion], now = w.pos[ion];
    if (was === undefined) continue;      // introduced this cycle; not in `pos_before`
    if (now === was) continue;
    if (!own(origin, ion)) {
      out.push(_v('R8', w.id, 'ion ' + ion + ' moved ' + was + ' -> ' + now +
                  ' with no participant'));
    } else if (origin[ion] !== was) {
      out.push(_v('R8', w.id, 'ion ' + ion + ' declared from ' + origin[ion] +
                  ' but was at ' + was));
    }
  }
  return out;
}

function _r11(w) {
  var perLoop = {}, out = [], i;
  for (i = 0; i < w.nmv; i++) {
    var m = w.moves[i], loop = w.dev.segments[m.seg].loop;
    if (loop === null || loop === undefined) continue;
    var seq = w.dev.loops[loop].nodes, k = seq.length;
    var idx = w.loopIdx[loop] || (w.loopIdx[loop] = _indexOfSeq(seq));
    var delta = ((idx[m.dst] - idx[m.src]) % k + k) % k;
    (perLoop[loop] || (perLoop[loop] = {}))[delta] = true;
  }
  for (var lid in perLoop) if (own(perLoop, lid)) {
    var deltas = Object.keys(perLoop[lid]).map(Number).sort(_cmpNum);
    if (deltas.length > 1) {
      out.push(_v('R11', w.id, 'ions move along loop ' + lid + ' by ' + pyRepr(deltas) +
                  ' in one cycle; shuttling is unidirectional per path'));
    }
  }
  return out;
}
function _indexOfSeq(seq) {
  var idx = {};
  for (var i = 0; i < seq.length; i++) idx[seq[i]] = i;
  return idx;
}

function _r12(w) {
  if (w.type !== 'gate') return [];
  var per = {}, out = [], pr = w.f.pairs || [];
  for (var i = 0; i < pr.length; i++) {
    var site = w.posBefore[pr[i][0]];
    if (site !== undefined) per[site] = (per[site] || 0) + 1;
  }
  for (var s in per) if (own(per, s)) {
    if (per[s] > 1) {
      out.push(_v('R12', w.id, per[s] + ' gates in trap ' + s +
                  ' in one cycle; intra-trap parallelism is 1'));
    }
  }
  return out;
}

function _r13(w) {
  if (w.type !== 'gate') return [];
  var out = [], gs = _gateSites(w), seen = {};
  for (var i = 0; i < gs.length; i++) {
    if (own(seen, gs[i])) continue;
    seen[gs[i]] = true;
    var occ = w.occBefore[gs[i]] === undefined ? (w.occ[gs[i]] || 0) : w.occBefore[gs[i]];
    if (occ > w.chainLimit) {
      out.push(_v('R13', w.id, '2Q gate in a chain of ' + occ + ' ions at ' + gs[i] +
                  '; gate time degrades sharply above ~' + w.chainLimit));
    }
  }
  return out;
}

function _r14(w) {
  var out = [];
  for (var i = 0; i < w.nmv; i++) {
    var m = w.moves[i];
    if (m.entails.indexOf('split') < 0) continue;
    var occ = w.occBefore[m.src] === undefined ? (w.occ[m.src] || 0) : w.occBefore[m.src];
    if (occ > 2 && !w.f.gate_swaps) {
      out.push(_v('R14', w.id, 'ion ' + m.ion + ' splits from a chain of ' + occ + ' at ' +
                  m.src + ' with no gate_swap accounted; R14 charges 3 CX to reach the edge'));
    }
  }
  return out;
}

// ------------------------------------------------------------------ the cycle window
//
// ONE object, REUSED across cycles.  Retaining it is a bug -- say so here rather than in a
// comment nobody reads at the call site.  Materialising 3,860 real snapshots is what makes
// Python's own rule pass cost 3.4 s; this pass is measured at ~22 ms on the same programme,
// which is 8-14% of the pricing walk that already runs inside the existing 180 ms debounce.
function _makeChecker(dev, loops, classes, ctx) {
  ctx = ctx || {};
  var cap = {}, nid;
  for (nid in dev.nodes) if (own(dev.nodes, nid)) cap[nid] = dev.nodes[nid].cap;
  var w = {
    dev: dev, loops: loops || {}, classes: classes || {}, cap: cap,
    zoneTypes: ctx.zone_types || {},
    maxSimd: ctx.max_simd === undefined ? 1 : Math.trunc(Number(ctx.max_simd)),
    gateBudget: ctx.gate_threshold === undefined || ctx.gate_threshold === null ||
                !(Number(ctx.gate_threshold) > 0) ? Infinity : Number(ctx.gate_threshold),
    chainLimit: ctx.chain_limit === undefined ? 15 : Math.trunc(Number(ctx.chain_limit)),
    loopIdx: {}, id: 0, type: null, cls: null, mode: null, f: null,
    // `moves` is a POOL: `nmv` says how much of it this cycle uses, and the entries are
    // overwritten rather than reallocated.  The shipped deck programme resolves 648,000
    // moves, and one object literal each was measured as the single largest cost in the
    // whole rule pass -- larger than any rule.  Every rule reads `w.nmv`, never
    // `w.moves.length`, and a rule that read the length would silently judge the previous
    // cycle's tail as well.
    moves: [], nmv: 0,
    participants: [], posBefore: {}, pos: {}, occBefore: {}, occ: {},
    quanta: {}, transits: {}, over: [], overJ: [], deg: null, added: [],
    // scratch, REUSED across cycles.  One object per rule per cycle is what a
    // 3,861-cycle programme cannot afford; retaining any of it is a bug.
    _dir: {}
  };
  var violations = [], byRule = {}, only = ctx.rules || null;
  var names = Object.keys(RULE_FNS);
  if (only) names = names.filter(function (r) { return only.indexOf(r) >= 0; });
  return {
    window: w,
    violations: violations,
    byRule: byRule,
    judge: function () {
      for (var i = 0; i < names.length; i++) {
        var vs = RULE_FNS[names[i]](w);
        for (var j = 0; j < vs.length; j++) {
          violations.push(vs[j]);
          byRule[vs[j].rule] = (byRule[vs[j].rule] || 0) + 1;
        }
      }
    }
  };
}

//: A guard, not a degradation.  Every per-cycle structure above is O(moves) with hoisted
//: indexes, but a from-scratch device is the point of this tool and nothing bounds its
//: size.  Above this cycle x move product the pass REFUSES -- in the same voice as
//: "price unavailable" -- rather than quietly running for a minute.
var RULE_WORK_CAP = 40000000;

function evaluate(dev, frames, loops, model, classes, ctx) {
  ctx = ctx || {};
  var chk = _makeChecker(dev, loops, classes, ctx);
  var w = chk.window;
  w.deg = function (n) { return deg(model, n); };
  var priced;
  try {
    priced = priceFrames(frames, loops, model, classes, { onCycle: chk.judge, window: w,
                                                          workCap: RULE_WORK_CAP });
  } catch (err) {
    return { price: null, rules: _report(chk, frames, null, ctx, err.message) };
  }
  return { price: priced, rules: _report(chk, frames, priced, ctx, null) };
}

function checkFrames(dev, frames, loops, model, classes, ctx) {
  return evaluate(dev, frames, loops, model, classes, ctx).rules;
}

// R7c is PROGRAM-LEVEL, not per-cycle: gates anywhere and no cool anywhere.  The rule that
// catches "the designer deleted the cooling".
function _r7c(frames, priced, modelsHeating) {
  if (!modelsHeating) return [];
  var pairs = 0, cools = 0;
  for (var i = 0; i < frames.length; i++) {
    if (frames[i].type === 'gate') pairs += (frames[i].pairs || []).length;
    if (frames[i].type === 'cool') cools++;
  }
  if (!pairs || cools) return [];
  return [_v('R7c', -1, pairs + ' two-qubit gates and no cooling operation anywhere; ' +
             'peak n-bar reaches ' + _q(priced ? priced.peak : 0, 1).toFixed(1) + ' quanta')];
}

function _report(chk, frames, priced, ctx, fatal) {
  var byRule = {}, k;
  for (k in chk.byRule) if (own(chk.byRule, k)) byRule[k] = chk.byRule[k];
  var violations = chk.violations.slice();
  var checked = mirroredRules(), skipped = {}, partial = {}, vacuous = {};

  // R7c, then R11's structural half -- which `RuleReport.by_rule()` counts under R11
  // alongside the per-cycle half, so the mirror must add it in the same bucket or the
  // count is short by exactly the number of unpriceable junctions.
  if (ctx.state) {
    var av = architectureViolations(ctx.state);
    for (var a = 0; a < av.length; a++) {
      violations.push(_v('R11', -1, av[a].message));
      byRule.R11 = (byRule.R11 || 0) + 1;
    }
  }
  var r7c = _r7c(frames, priced, ctx.models_heating !== false);
  for (var c = 0; c < r7c.length; c++) {
    violations.push(r7c[c]);
    byRule.R7c = (byRule.R7c || 0) + 1;
  }
  if (ctx.models_heating === false) {
    skipped.R7c = 'cooling legality is only meaningful once heating is modelled';
    skipped.R17 = 'this cost model does not model elapsed time';
    checked = checked.filter(function (r) { return r !== 'R7c' && r !== 'R17'; });
  }

  // WHAT THIS PAGE CANNOT CHECK, enumerated with its reason.  Silence is not acceptable
  // and neither is a green tick: an un-run rule is a THIRD state.
  skipped.R4d = 'the channel-membership map has to be rebuilt from the edited wiring; ' +
                'ControlPlane.drivable is deliberately not mirrored, so R4d needs Python';
  skipped.R7b = 'no architecture declares a per-zone duty-cycle budget, so there is ' +
                'nothing to check anywhere -- Python lists R7b as unimplemented too';
  skipped.R9 = "the programme's cost and step claims describe the device this page was " +
               'built for, not this one; after an edit the only honest verdict is "not ' +
               'applicable"';
  skipped.R10 = 'needs the source circuit and a Pauli-frame tracker; the page carries no ' +
                'circuit, only compiled output';
  skipped.R16 = 'gate error against n-bar is not mirrored client-side, and adding it ' +
                'without a parity bucket would introduce a number with no oracle';
  partial.R15 = 'quanta are composed additively here, which R15 says is an upper bound; ' +
                'the interference term needs a secular-phase model the corpus does not supply';
  vacuous['R4-concurrency'] = 'no instruction carries t0/t1, so nothing overlaps in time ' +
                              'and the concurrency half of R4 judged nothing';

  var failed = {}, i;
  for (i = 0; i < violations.length; i++) failed[violations[i].rule] = true;
  var failedList = Object.keys(failed).sort(_cmpStr);
  var passed = checked.filter(function (r) { return !own(failed, r) && !own(skipped, r); });
  return {
    passed: passed, failed: failedList, partial: partial, skipped: skipped,
    violations: violations.length, by_rule: byRule, messages: violations,
    checked: checked.slice(), vacuous: vacuous, scope: 'browser',
    warnings: priced ? (priced.warnings || []) : [],
    fatal: fatal
  };
}

// The zone types the sites actually carry.  The UI must refuse to price while this is not
// a subset of the declared `zone_types`: `Architecture.can` RAISES there rather than
// returning False, so R6 cannot run at all -- and "0 violations" for a check that never
// executed is exactly the claim this project forbids.
function zonesInUse(dev) {
  var set = {}, out = [];
  if (!dev) return out;
  for (var nid in dev.nodes) if (own(dev.nodes, nid)) {
    var z = dev.nodes[nid].zone;
    if (z !== null && z !== undefined && !own(set, z)) { set[z] = true; out.push(z); }
  }
  return out.sort(_cmpStr);
}

// =====================================================================================
// 13. FROM-SCRATCH GEOMETRY -- blaming the right statement, and exploding to explicit
// =====================================================================================

// WHICH STATEMENT DID THE USER GET WRONG?
//
// Every builder refusal lands at the SEED's index: `d.site("0S", ...)` succeeds silently
// and the `ValidationError` arrives at `blank_device` with a schema path
// (`$.geometry.nodes[0].id`).  A design tool cannot highlight the offending gesture from
// that, and no better MESSAGE fixes it -- the information is simply not there.
//
// Re-running `applyProgram(GEOM.slice(0, k+1) ++ [SEED])` for increasing `k` and reporting
// the first `k` at which the seal starts failing gives the statement that actually caused
// it, at the cost of one replay per statement on a graph a human drew.  It runs only when
// something is already broken.
function buildProblems(calls) {
  var full = applyProgram(calls);
  if (!full.error) return [];
  var seedAt = -1, i;
  for (i = 0; i < calls.length; i++) {
    if (_kindOf((calls[i] || {}).method) === 'seed') seedAt = i;
  }
  var err = full.error;
  var blamed = err.index;
  // a refusal that already lands on a BUILD statement is its own blame
  if (seedAt >= 0 && err.index === seedAt) {
    var seed = calls[seedAt];
    for (i = 0; i < seedAt; i++) {
      if (_kindOf((calls[i] || {}).method) !== 'build') continue;
      var prefix = calls.slice(0, i + 1).concat([seed]);
      var r = applyProgram(prefix);
      if (r.error) { blamed = i; break; }
    }
  }
  return [{ i: blamed, code: err.code, method: (calls[blamed] || {}).method || err.method,
            message: err.message, at_seed: err.index }];
}

// `qccd/arch/listing.py::_emit_geometry`'s EXPLICIT branch, as call records.
//
// This is what makes the eleven rebuild-only geometry fields -- a site's zone and labels,
// a segment's capacity, loop and labels, and all five loop fields -- editable in place
// with ZERO new verbs: the device becomes `DeviceBuilder` + every `d.site`/`d.junction` +
// every `d.segment` + every `d.loop` + a seal, and each of those fields is then one
// statement to rewrite.  Four new verbs would have been four methods x two
// implementations x a listing emitter x a parity bucket each, and would have weakened the
// invariant that makes "advertised implies dispatchable" true by construction.
//
// IRREVERSIBLE, and the caller must say so: after this the device no longer reproduces
// from its generator (`Device.reproducible_from_generator()` is False forever) and
// `to_json(expanded=False)` writes the expanded form from then on.
function explicitStatements(dev, opts) {
  opts = opts || {};
  var out = [], nid, sid, lid;
  out.push({ method: 'DeviceBuilder', args: [dev.generator], kwargs: _deep(dev.params) });
  for (nid in dev.nodes) if (own(dev.nodes, nid)) {
    var n = dev.nodes[nid], kw = {};
    if (n.zone !== null && n.zone !== undefined) kw.zone = n.zone;
    if (n.labels && n.labels.length) kw.labels = n.labels.slice();
    if (n.capacity_explicit) kw.capacity = Math.trunc(n.cap);
    out.push({ method: n.kind === 'site' ? 'd.site' : 'd.junction',
               args: [nid, new PyFloat(unbox(n.pos[0])), new PyFloat(unbox(n.pos[1]))],
               kwargs: kw });
  }
  for (sid in dev.segments) if (own(dev.segments, sid)) {
    var sg = dev.segments[sid], skw = {};
    if (sg.loop) skw.loop = sg.loop;
    if (sg.labels && sg.labels.length) skw.labels = sg.labels.slice();
    if (Number(unbox(sg.length)) !== 1.0) skw.length = new PyFloat(unbox(sg.length));
    if (Math.trunc(sg.cap) !== 1) skw.capacity = Math.trunc(sg.cap);
    out.push({ method: 'd.segment', args: [sid, sg.a, sg.b], kwargs: skw });
  }
  for (lid in dev.loops) if (own(dev.loops, lid)) {
    var lp = dev.loops[lid];
    out.push({ method: 'd.loop', args: [lid, lp.nodes.slice()],
               kwargs: { closed: !!lp.closed, kind: lp.kind,
                         note: lp.note === undefined ? null : lp.note } });
  }
  out.push({ method: opts.template ? 'from_device' : 'blank_device', args: [],
             kwargs: { name: opts.name === undefined ? 'custom' : String(opts.name),
                       template: opts.template === undefined ? null : opts.template } });
  return out;
}

// ======================================================== COMPONENT VARIANTS
//
// The browser half of `qccd/arch/variants.py`.  It is deliberately the dumbest code in
// this file: it indexes an array, deep-copies, and performs exactly three operations --
//
//     mul    x = coefficient * value        one IEEE multiply
//     set    x = value                      substitution, no arithmetic
//     text   s = prefix + String(v) + suffix
//
// -- because everything that decides the SHAPE of a component (which records exist, what
// their local ids are, what a loop's walk is) was computed by Python across an enumerated
// grid and interned into `pool`.  Nothing here re-implements a factory, and there is no
// arithmetic for a mirror to get wrong: `*` is a single correctly-rounded IEEE operation
// specified identically in CPython and V8, which is exactly the property `cos`/`sin` did
// not have when a 1-ulp difference flipped a segment's bow.
//
// It lives in engine.js rather than editor.js on purpose: `tests/parity.mjs` imports this
// file directly, and `test_the_page_inlines_this_exact_engine` proves these bytes are the
// page's bytes -- so the differential harness runs against the shipped resolver with no
// DOM anywhere.
function _vPathGet(o, path) {
  for (var i = 0; i < path.length; i++) o = o[path[i]];
  return o;
}
function _vPathSet(o, path, v) {
  for (var i = 0; i < path.length - 1; i++) o = o[path[i]];
  o[path[path.length - 1]] = v;
}
function _vClone(x) { return JSON.parse(JSON.stringify(x)); }

// Mixed radix over `dims`, in the order Python declared them -- the same order
// `variants._row` walks, which is what keeps the two indexings from transposing.
function variantRow(vb, sel) {
  var ix = 0, i, j;
  if (!vb || !vb.grid) return null;
  for (i = 0; i < vb.dims.length; i++) {
    var d = vb.dims[i], vals = d.values, at = -1;
    var v = (sel && sel[d.param] !== undefined && sel[d.param] !== null)
      ? sel[d.param] : vals[0];
    for (j = 0; j < vals.length; j++) if (vals[j] === v) { at = j; break; }
    if (at < 0) return null;
    ix = ix * vals.length + at;
  }
  return vb.grid[ix] || null;
}

function _vApply(obj, slots, values) {
  if (!slots) return;
  for (var i = 0; i < slots.length; i++) {
    var s = slots[i], p = s[0], path = s[1], op = s[2];
    if (!values || values[p] === undefined || values[p] === null) continue;
    var v = values[p];
    if (op === 'mul') _vPathSet(obj, path, _vPathGet(obj, path) * v);
    else if (op === 'set') _vPathSet(obj, path, v);
    else if (op === 'text') _vPathSet(obj, path, s[3][0] + String(v) + s[3][1]);
    else throw new EngineError('unknown component slot op ' + JSON.stringify(op));
  }
}

// `{records, pins, requires, blurb}` -- the same four keys `variants.resolve` returns.
function resolveVariant(vb, sel, slots) {
  var row = variantRow(vb, sel);
  if (!row) throw new EngineError('no such component variant: ' + JSON.stringify(sel));
  var plan = row[0], i;
  var spec = { records: [],
               pins: _vClone(vb.pins_pool[row[1]]),
               requires: _vClone(vb.req_pool[row[2]]),
               blurb: vb.blurbs[row[3]] };
  for (i = 0; i < plan.length; i++) {
    // A DEEP COPY PER RECORD. The pool is shared across every variant and every
    // instance; mutating it in place would make one component's pitch change another's.
    var rec = _vClone(vb.pool[plan[i]]);
    _vApply(rec, vb.slotsets[vb.poolslots[plan[i]]], slots);
    spec.records.push(rec);
  }
  _vApply(spec, vb.topslots, slots);
  return spec;
}

// WHAT THE PAGE MAY DRAW, decided from shipped numbers rather than a bound retyped here.
// `computeLayout` throws above COORD_MAX, and `renderPalette` is not inside `paint()`'s
// try/catch -- so an unrefused value does not merely draw badly, it aborts the rest of
// the bar and keeps doing so, because the offending value persists.
var VARIANT_MIN_GAP = 1e-6;
function variantGuard(vb, sel, slots) {
  var row = variantRow(vb, sel);
  if (!row) {
    return { code: 'no_variant',
             message: 'no such variant: ' + JSON.stringify(sel || {}) };
  }
  var bounds = row[4] || {}, constmax = row[5] || 0, p;
  if (Math.abs(constmax) > COORD_MAX) {
    return { code: 'out_of_range',
             message: 'this variant is already ' + constmax + ' across, past the '
                    + COORD_MAX + ' the layout can measure' };
  }
  for (p in bounds) {
    if (!Object.prototype.hasOwnProperty.call(bounds, p)) continue;
    var v = (slots && slots[p] !== undefined && slots[p] !== null) ? Number(slots[p]) : 1;
    if (!isFinite(v)) {
      return { code: 'out_of_range', message: p + ' must be a finite number' };
    }
    var b = bounds[p];
    if (b.cmax * Math.abs(v) > COORD_MAX) {
      return { code: 'out_of_range',
               message: p + ' = ' + v + ' puts a node at ' + (b.cmax * Math.abs(v))
                      + ', past the ' + COORD_MAX + ' the layout can measure' };
    }
    if (v === 0 && b.cmax > 0) {
      return { code: 'out_of_range',
               message: p + ' = 0 would put every node of this part at one point' };
    }
    if (b.cmin > 0 && b.cmin * Math.abs(v) < VARIANT_MIN_GAP) {
      return { code: 'out_of_range',
               message: p + ' = ' + v + ' collapses the part below the '
                      + VARIANT_MIN_GAP + ' the lattice can resolve' };
    }
  }
  return null;
}

// INTEGERS ONLY, and that is the point: pin node ids depend on the enumerated dims and
// on nothing else, so a placed instance can record its variant without ever writing a
// float -- and float formatting can therefore never drift across the two languages.
function variantLabel(name, vb, sel) {
  var parts = [], i;
  if (vb && vb.dims) {
    for (i = 0; i < vb.dims.length; i++) {
      var d = vb.dims[i];
      var v = (sel && sel[d.param] !== undefined && sel[d.param] !== null)
        ? sel[d.param] : d.values[0];
      parts.push(d.param + '=' + String(Math.round(Number(v))));
    }
  }
  return 'cmpvar:' + name + (parts.length ? ':' + parts.join(',') : '');
}

// The inverse: `{name, sel}` from a label a stamp wrote, or null.
function parseVariantLabel(label) {
  var s = String(label || '');
  if (s.slice(0, 7) !== 'cmpvar:') return null;
  var rest = s.slice(7), c = rest.indexOf(':');
  var name = c < 0 ? rest : rest.slice(0, c);
  var sel = {};
  if (c >= 0) {
    var parts = rest.slice(c + 1).split(',');
    for (var i = 0; i < parts.length; i++) {
      var kv = parts[i].split('=');
      if (kv.length === 2) sel[kv[0]] = parseInt(kv[1], 10);
    }
  }
  return { name: name, sel: sel };
}

// ================================================== AN ARCHITECTURE DOCUMENT, AS RECORDS
//
// Opening a `.arch.json` used to DESTROY it: `snapshotOf` built a seed `from_device` with
// a `document=` kwarg that `from_device` does not implement -- it builds from
// `st.builder`, which nothing had filled -- so the import reported success with an empty
// problem list and a canvas of zero nodes.
//
// The fix is not a second document parser.  `splitListing` already shows what the page
// does with an architecture: it replays the CALL RECORDS Python's `listing.py` emits.
// This produces the same KIND of thing straight from the document, in the vocabulary the
// interpreter already implements -- `blank`/`blank_device`, `d.site`, `set_zone`,
// `set_control`, `set_curve`, `set_primitive` -- so there is no new verb, no new state
// shape, and nothing for a mirror to drift from.
//
// It is emphatically NOT a mirror of `listing.py`.  That module is 942 lines because it
// also writes prose, picks a baseline device and elides default parameters to make a
// READABLE listing.  None of that is needed to rebuild an architecture: these records
// only have to REPLAY to the same document, and `test_import_export.py` asserts exactly
// that -- `serialize(replay(documentStatements(doc))) === doc` for every shipped
// architecture.  That fixed point is the specification, and it does not require this
// function to agree with `listing.py` about anything.
//
// Two orderings are load-bearing:
//   * the seal carries `zones` as NAMES, because a generator cannot place a site in a
//     zone that does not exist at expansion time; `set_zone` then retunes each one after
//     the seal, which is legal for a zone that already exists and is why `listing.py`
//     splits it the same way.
//   * `declare_class` and `set_wiring` come from inside the control block but are their
//     own verbs, so they are emitted before the plain `set_control` merges.
function documentStatements(doc) {
  if (!doc || typeof doc !== 'object' || !doc.geometry) {
    throw new EditError('not an architecture document: no `geometry` section',
                        'not_a_document', 'documentStatements');
  }
  var out = [], g = doc.geometry || {}, i, k;
  var zones = doc.zone_types || {};
  // IN DECLARATION ORDER, NOT SORTED. `serialize` writes `zone_types` in the order the
  // zones were declared, so sorting the names here rebuilds the same five zones under a
  // different key order and the file no longer matches itself after a reopen.
  var zoneNames = [];
  for (k in zones) if (own(zones, k)) zoneNames.push(k);
  var name = doc.name === undefined || doc.name === null ? 'imported' : String(doc.name);

  var explicit = g.nodes && g.nodes.length;
  if (explicit) {
    out.push({ method: 'DeviceBuilder', args: [g.generator || 'explicit'],
               kwargs: _deep(g.params || {}) });
    for (i = 0; i < g.nodes.length; i++) {
      var n = g.nodes[i], nkw = {};
      if (n.zone_type !== undefined && n.zone_type !== null) nkw.zone = n.zone_type;
      if (n.labels && n.labels.length) nkw.labels = n.labels.slice();
      // ONLY WHEN EXPLICIT. `resolve_capacities` fills a site's capacity from its zone,
      // and re-stating a derived capacity would make it explicit on the way back out --
      // which is a different document, and one `capacity_explicit` exists to distinguish.
      if (n.capacity_explicit) nkw.capacity = Math.trunc(n.capacity);
      out.push({ method: n.kind === 'junction' ? 'd.junction' : 'd.site',
                 args: [n.id, new PyFloat(n.pos[0]), new PyFloat(n.pos[1])],
                 kwargs: nkw });
    }
    for (i = 0; i < (g.segments || []).length; i++) {
      var s = g.segments[i], skw = {};
      if (s.loop) skw.loop = s.loop;
      if (s.labels && s.labels.length) skw.labels = s.labels.slice();
      if (Number(s.length) !== 1.0) skw.length = new PyFloat(s.length);
      if (Math.trunc(s.capacity) !== 1) skw.capacity = Math.trunc(s.capacity);
      out.push({ method: 'd.segment', args: [s.id, s.ends[0], s.ends[1]], kwargs: skw });
    }
    for (i = 0; i < (g.loops || []).length; i++) {
      var l = g.loops[i];
      out.push({ method: 'd.loop', args: [l.id, l.nodes.slice()],
                 kwargs: { closed: !!l.closed, kind: l.kind,
                           note: l.note === undefined ? null : l.note } });
    }
    out.push({ method: 'blank_device', args: [],
               kwargs: { name: name, zones: zoneNames.slice() } });
  } else {
    var bkw = {};
    for (k in (g.params || {})) if (own(g.params, k)) bkw[k] = _deep(g.params[k]);
    bkw.name = name;
    bkw.zones = zoneNames.slice();
    out.push({ method: 'blank', args: [g.generator], kwargs: bkw });
  }

  if (doc.description) {
    out.push({ method: 'describe', args: [String(doc.description)], kwargs: {} });
  }
  for (i = 0; i < zoneNames.length; i++) {
    out.push({ method: 'set_zone', args: [zoneNames[i]],
               kwargs: _deep(zones[zoneNames[i]]) });
  }

  // IN THE DOCUMENT'S OWN KEY ORDER. `serialize` writes `control` in the order the keys
  // were added, so emitting `set_wiring` first -- or sorting -- rebuilds the same block
  // with its keys shuffled. Every value survives that, and the file still diffs against
  // itself on every reopen, which is its own kind of broken.
  var ctrl = doc.control || {};
  for (k in ctrl) if (own(ctrl, k)) {
    if (k === 'wiring') {
      out.push({ method: 'set_wiring', args: [], kwargs: _deep(ctrl.wiring) });
      continue;
    }
    // `classes` goes through `set_control` like every other key, INCLUDING the declared
    // ones -- they live under `classes.extra`, and `set_control` stores the block whole.
    // Synthesising `declare_class` calls instead would mean reproducing that merge
    // exactly, on a path no shipped document exercises, to arrive at the same value.
    var one = {};
    one[k] = _deep(ctrl[k]);
    out.push({ method: 'set_control', args: [], kwargs: one });
  }

  var prims = doc.primitives || {};
  for (k in prims) if (own(prims, k)) {
    var p = prims[k], scal = {}, any = false, pk;
    for (pk in p) if (own(p, pk)) {
      if (pk === 'curve') {
        out.push({ method: 'set_curve', args: [k, _deep(p.curve)], kwargs: {} });
      } else if (pk === 'curve_by_degree') {
        var degs = [];
        for (var dk in p.curve_by_degree) if (own(p.curve_by_degree, dk)) degs.push(dk);
        degs.sort(function (a, b) { return Number(a) - Number(b); });
        for (i = 0; i < degs.length; i++) {
          out.push({ method: 'set_degree_curve',
                     args: [k, Number(degs[i]), _deep(p.curve_by_degree[degs[i]])],
                     kwargs: {} });
        }
      } else { scal[pk] = _deep(p[pk]); any = true; }
    }
    if (any) out.push({ method: 'set_primitive', args: [k], kwargs: scal });
  }

  if (_nonEmpty(doc.heating)) {
    out.push({ method: 'set_heating', args: [], kwargs: _deep(doc.heating) });
  }
  if (_nonEmpty(doc.species)) {
    out.push({ method: 'set_species', args: [], kwargs: _deep(doc.species) });
  }
  if (_nonEmpty(doc.budget)) {
    out.push({ method: 'set_budget', args: [], kwargs: _deep(doc.budget) });
  }
  return out;
}

var API = {
  ENGINE: 'qccd-engine/1',
  // portable arithmetic, exported so a test can prove they are the ones in use
  _q: _q, _hyp: _hyp, _fsum: _fsum, _pyRound: _pyRound, COORD_MAX: COORD_MAX,
  PyFloat: PyFloat, pyFloat: function (v) { return new PyFloat(v); }, unbox: unbox,
  pyRepr: pyRepr, lit: lit, kwd: kwd,
  // layout
  computeLayout: computeLayout, layoutOf: layoutOf,
  minNearestNeighbour: minNearestNeighbour, siteLength: siteLength, padTiling: padTiling,
  pointSegment: _pointSegment,
  LAYOUT_CONSTS: { W_MAX: W_MAX, W_MIN: W_MIN, H_MAX: H_MAX, H_MIN: H_MIN,
                   PAD_A: PAD_A, PAD_B: PAD_B, PITCH_CAP: PITCH_CAP, K_ANISO: K_ANISO,
                   ISO_ASPECT: ISO_ASPECT, K_ION: K_ION, K_REST: K_REST,
                   R_ION_MAX: R_ION_MAX, R_ION_MIN: R_ION_MIN,
                   ION_D_FRAC: GEOM.ION_D_FRAC, ION_D_FRAC_ACTIVE: GEOM.ION_D_FRAC_ACTIVE,
                   RAIL_W_FRAC: GEOM.RAIL_W_FRAC, RUNG_W_FRAC: GEOM.RUNG_W_FRAC },
  // generators
  GENERATORS: GENERATORS, expandGenerator: expandGenerator, generators: generatorNames,
  ring: ring, grid: grid, chain: chain, ladder: ladder, racetrack: racetrack,
  dual_loop: dualLoop,
  // graph + hardware
  degrees: degrees, junctionNodes: junctionNodes, degreeHistogram: degreeHistogram,
  totalCapacity: totalCapacity, hardwareReport: hardwareReport,
  allCorners: allCorners, cornerEndpoints: cornerEndpoints,
  // schema -- SHIPPED as data by qccd/arch/schema.py::export_schema(); no copy here
  setSchema: setSchema, schema: schema, schemaVersion: schemaVersion,
  validateDocument: validateDocument,
  // interpreter
  methods: methods, apply: applyCall, applyProgram: applyProgram, cloneState: cloneState,
  serialize: serialize, deviceToJson: deviceToJson, resolveCapacities: resolveCapacities,
  blankState: _blankState,
  // derived over CALLS, so they cannot name a verb the dispatcher does not have
  get SEED_METHODS() { return _methodsOfKind('seed'); },
  get MUTATE_METHODS() { return _methodsOfKind('mutate'); },
  get BUILD_METHODS() { return _methodsOfKind('build'); },
  setTemplates: setTemplates, templates: templateNames,
  templateDefault: templateDefault,
  // text surface
  parse: parse, render: renderStmt, renderProgram: renderProgram, tokenize: tokenize,
  // the programme lane -- authoring, lowering, and the two projections of a record list
  get PROGRAM_METHODS() { return programMethods(); },
  lowerProgram: lowerProgram, programToTsir: programToTsir, framesToTsir: framesToTsir,
  framesToInstructions: framesToInstructions,
  renderProgramStmt: renderProgramStmt, renderProgramSource: renderProgramSource,
  // pricing
  pickPoint: pickPoint, makeModel: makeModel, priceFrames: priceFrames,
  pairIndex: pairIndex, validateProgram: validateProgram, loopLengths: loopLengths,
  // lint
  lint: lint, architectureViolations: architectureViolations,
  classParticipants: classParticipants, zonesInUse: zonesInUse,
  // the browser rule set -- 17 of the 23, derived from the dispatcher and never listed
  get MIRRORED_RULES() { return mirroredRules(); },
  checkFrames: checkFrames, evaluate: evaluate, RULE_WORK_CAP: RULE_WORK_CAP,
  // component variants -- the browser half of arch/variants.py
  resolveVariant: resolveVariant, variantRow: variantRow, variantGuard: variantGuard,
  variantLabel: variantLabel, parseVariantLabel: parseVariantLabel,
  VARIANT_MIN_GAP: VARIANT_MIN_GAP,
  // from-scratch geometry
  sealDevice: _sealDevice, buildProblems: buildProblems,
  explicitStatements: explicitStatements,
  documentStatements: documentStatements,
  // errors
  EngineError: EngineError, ExpansionError: ExpansionError, EditError: EditError
};

if (typeof module !== 'undefined' && module.exports) module.exports = API;
if (typeof globalThis !== 'undefined') globalThis.QCCD = API;

return API;
})();
