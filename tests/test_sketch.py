"""SKETCH MODE: the shape first, the parts after.

Asked for on 2026-09-17: *"it would be much more direct and useful if the design can start
from a Sketch of the shuttling shape, before the details are filled in ... moreover, some
component, such as a Junction, should be automatically added if the shape has a corner"*.

Two of those three sentences become code directly.  The third does not, and the difference
is the most important thing in this file:

**A CORNER IS NOT A JUNCTION.**  A corner of a drawn rail is a degree-2 node, and R18
prices a degree-2 node as ordinary transport -- a BEND.  The shipped ring has four of them
and pays nothing for any of them.  Minting a degree-2 `junction` there would contradict
R18, and the verifier would then price every rigid rotation of that loop as though it
crossed four junctions it does not contain.  So a corner gets a SITE and the readout says
which it is.  A junction IS added by itself, but where a junction actually is: at a real
meeting of three or four rails, which is what happens when the new shape crosses one that
is already on the canvas.

Everything below is asserted through the editor's own verbs, over the page `qccd studio`
actually ships, and every expectation is recomputed in Python rather than echoed from the
implementation: a 6 x 4 rectangle has a perimeter of 20 lattice units, so it holds 20
trapping sites and 20 rails, and that arithmetic is done here.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

NODE = shutil.which("node")
SHIM = (Path(__file__).parent / "shim.mjs").as_uri()
EDITOR_JS = ROOT / "qccd" / "viz" / "js" / "editor.js"
RENDER_PY = ROOT / "qccd" / "viz" / "render.py"

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


def build(tmp: Path) -> Path:
    page = tmp / "studio.html"
    subprocess.run([sys.executable, "-m", "qccd", "studio", "-o", str(page)],
                   cwd=ROOT, capture_output=True, timeout=900, check=True)
    return page


def build_seeded(tmp: Path) -> Path:
    page = tmp / "seeded.html"
    subprocess.run([sys.executable, "-m", "qccd", "studio", "--seed", "ring144_24v",
                    "-o", str(page)],
                   cwd=ROOT, capture_output=True, timeout=900, check=True)
    return page


@pytest.fixture(scope="module")
def blank(tmp_path_factory) -> Path:
    return build(tmp_path_factory.mktemp("sketch"))


@pytest.fixture(scope="module")
def seeded(tmp_path_factory) -> Path:
    return build_seeded(tmp_path_factory.mktemp("sketch_seeded"))


# A MEMORY `localStorage`, installed BEFORE the page is evaluated, because that is when
# the editor's `STORE` binds to it.  `tests/shim.mjs` deliberately ships none -- the
# harness must not fake persistence for the tests that assert a round trip survives -- so a
# test that IS about persistence brings its own, and says so.
STORE_STUB = """
globalThis.__STORE = Object.create(null);
globalThis.localStorage = {
  getItem: k => (k in globalThis.__STORE ? globalThis.__STORE[k] : null),
  setItem: (k, v) => { globalThis.__STORE[k] = String(v); },
  removeItem: k => { delete globalThis.__STORE[k]; },
};
"""

HELPERS = """
const dev = () => E.state().device;
const nodeIds = () => Object.keys(dev().nodes).sort();
const segIds = () => Object.keys(dev().segments).sort();
const degrees = () => { const d = {};
  for (const s of Object.values(dev().segments)) { d[s.a] = (d[s.a]||0)+1; d[s.b] = (d[s.b]||0)+1; }
  return d; };
const geomLints = () => globalThis.QCCD.lint(E.state())
  .filter(l => l.code === 'R19' || l.code === 'R20' || l.code === 'R21')
  .map(l => l.code + ': ' + l.message);
const loops = () => Object.entries(dev().loops).map(
  ([k, v]) => ({ id: k, n: v.nodes.length, closed: !!v.closed, kind: v.kind, nodes: v.nodes }));
"""


def drive(page: Path, tmp_path: Path, body: str, store: bool = False) -> dict:
    """Run `body` in the page's own scope, exactly as tests/test_palette_components.py's
    shim-based helper does -- the editor is `E`, the engine is `QCCD`."""
    js = tmp_path / "p.mjs"
    js.write_text((STORE_STUB if store else "") +
                  f"import {{ loadPage }} from '{SHIM}';\n"
                  "loadPage(process.argv[2], ';globalThis.__E=EDITOR;globalThis.__L=L;');\n"
                  "const E = globalThis.__E, L = globalThis.__L;\n" + HELPERS + body,
                  encoding="utf-8")
    r = subprocess.run([NODE, str(js), str(page)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600, cwd=ROOT)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr[-3000:]}"
    return json.loads(r.stdout.strip().splitlines()[-1])


NEW = "E.newCanvas({ name:'sk', template:null });\n"


# ======================================================================================
# A. THE MODE
# ======================================================================================


def test_sketch_is_the_default_on_a_canvas_with_nothing_on_it(blank, tmp_path):
    """The request asked for it in those words: *by default the user should start with a
    Shape Sketch*.  A canvas that already carries a device opens on Parts, because there
    the work is detail work."""
    r = drive(blank, tmp_path, """
const empty = E.designMode();
E.sketchDraw('line', [0, 0], [4, 0], {});
console.log(JSON.stringify({ empty: empty, tools: E.sketchTools(),
                             nodes: nodeIds().length }));
""")
    assert r["empty"] == "sketch", "an empty canvas must open on Sketch"
    assert r["tools"] == ["rect", "ellipse", "line", "poly"]
    assert r["nodes"] == 5


def test_a_page_that_ships_a_device_opens_on_parts(seeded, tmp_path):
    r = drive(seeded, tmp_path, """
console.log(JSON.stringify({ mode: E.designMode(),
                             nodes: nodeIds().length }));
""")
    assert r["nodes"] > 20, "the seeded page carries a device"
    assert r["mode"] == "parts", (
        "with a device on the canvas the tool opens where the detail work is")


def test_the_mode_is_remembered_exactly_as_snap_and_true_scale_are(blank, tmp_path):
    """`qccd.studio.mode`, beside `qccd.studio.snap` and `qccd.studio.truescale`."""
    r = drive(blank, tmp_path, """
const out = { before: E.designMode() };
E.setDesignMode('parts');
out.after = E.designMode();
out.stored = globalThis.localStorage.getItem('qccd.studio.mode');
out.keys = Object.keys(globalThis.__STORE);
E.setDesignMode('sketch');
out.stored2 = globalThis.localStorage.getItem('qccd.studio.mode');
console.log(JSON.stringify(out));
""", store=True)
    assert r["before"] == "sketch"
    assert r["after"] == "parts"
    assert r["stored"] == "parts", f"nothing was written to localStorage: {r['keys']}"
    assert r["stored2"] == "sketch"


def test_a_stored_mode_wins_over_the_default(blank, tmp_path):
    """Seed the store BEFORE the page boots -- Parts, on a canvas that would otherwise have
    opened on Sketch.  `import` is hoisted, so the seed is written between the import and
    the call that evaluates the page, which is the order node actually runs them in."""
    js = tmp_path / "seedmode.mjs"
    js.write_text("\n".join([
        f"import {{ loadPage }} from '{SHIM}';",
        STORE_STUB,
        "globalThis.__STORE['qccd.studio.mode'] = 'parts';",
        "loadPage(process.argv[2], ';globalThis.__E=EDITOR;');",
        "const E = globalThis.__E;",
        "console.log(JSON.stringify({ mode: E.designMode(),",
        "  nodes: Object.keys(E.state().device.nodes).length }));",
    ]), encoding="utf-8")
    out = subprocess.run([NODE, str(js), str(blank)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=600, cwd=ROOT)
    assert out.returncode == 0, out.stderr[-3000:]
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["nodes"] == 0, "the canvas is empty: the default alone would say Sketch"
    assert got["mode"] == "parts", "a stored choice must beat the empty-canvas default"


def test_parts_mode_is_today_s_tool_untouched(blank, tmp_path):
    """The request was explicit: *the current mode must be preserved*.  In Parts the shape
    tools are disarmed, a press on empty stage is a pan, and the armed-tile placement that
    tests/test_placement.py asserts still works."""
    r = drive(blank, tmp_path, NEW + """
E.sketchTool('rect');
E.setDesignMode('parts');
const out = { tool: E.sketchOn(), claim: E.claim(0, 0, {}) };
const p = [L.ox, L.oy];
E.arm('site');
E.ghostBegin('site', p[0], p[1]);
E.ghostMove(p[0], p[1]);
out.placed = E.clickStage(p[0], p[1], {}).ok;
out.nodes = nodeIds().length;
console.log(JSON.stringify(out));
""")
    assert r["tool"] is None, "leaving Sketch disarms whatever shape was armed"
    assert r["claim"] == "pan", "in Parts an empty-stage press pans, as it always did"
    assert r["placed"] is True and r["nodes"] == 1


# ======================================================================================
# B. WHAT A RELEASED SHAPE GENERATES
# ======================================================================================


def test_a_rectangle_lays_one_site_per_lattice_unit_around_its_perimeter(blank, tmp_path):
    """6 x 4 lattice units.  The perimeter is 2*(6+4) = 20 units, the spacing is one unit,
    and the first site is at the shape's start -- so 20 sites, 20 rails, and one closed
    orbit of 20 nodes.  That is the shipped ring's own arithmetic: `ring(w, h)` gives
    `2W + 2H - 4` slots, which for the 7 x 5 lattice of corners this rectangle spans is
    14 + 10 - 4 = 20.
    """
    w, h = 6, 4
    want_sites = 2 * (w + h)                       # computed here, not read back
    r = drive(blank, tmp_path, NEW + """
const drew = E.sketchDraw('rect', [0, 0], [6, 4], {});
const d = dev();
const xs = Object.values(d.nodes).map(n => [Number(n.pos[0]), Number(n.pos[1])]);
console.log(JSON.stringify({
  ok: drew.ok, problems: (drew.problems||[]).map(p => p.message),
  instance: drew.instance, loopId: drew.loop,
  nodes: nodeIds(), segs: segIds().length, loops: loops(),
  kinds: Object.values(d.nodes).map(n => n.kind),
  zones: Array.from(new Set(Object.values(d.nodes).map(n => n.zone))),
  pos: xs, lints: geomLints(),
  degrees: Array.from(new Set(Object.values(degrees()))),
  members: E.instanceMembers(drew.instance).length,
}));
""")
    assert r["ok"] is True, r["problems"]
    assert len(r["nodes"]) == want_sites, f"{want_sites} sites expected, got {len(r['nodes'])}"
    assert r["segs"] == want_sites, "a closed rail has as many segments as sites"
    assert set(r["kinds"]) == {"site"}, (
        "every node of a plain rectangle is a trapping site: its four corners are BENDS, "
        "which R18 prices as ordinary transport, not junctions")
    assert set(r["degrees"]) == {2}, "a closed rail is degree 2 everywhere"
    assert len(r["loops"]) == 1
    lp = r["loops"][0]
    assert lp["closed"] is True and lp["kind"] == "ring" and lp["n"] == want_sites
    assert lp["id"] == r["loopId"]
    assert r["lints"] == [], f"the generated device must be geometrically clean: {r['lints']}"
    assert r["members"] == 2 * want_sites, (
        "the whole sketch is one instance: every site and every rail carries its label")

    # the positions are exactly the perimeter of the rectangle that was drawn: nothing was
    # silently resized to make the spacing come out even
    pos = {(round(x, 6), round(y, 6)) for x, y in r["pos"]}
    want = set()
    for i in range(w):
        want.add((float(i), 0.0))
        want.add((float(w - i), float(h)))
    for j in range(h):
        want.add((float(w), float(j)))
        want.add((0.0, float(h - j)))
    assert pos == want, sorted(pos ^ want)


def test_one_undo_takes_the_whole_sketch_back(blank, tmp_path):
    """One gesture, one `meta.group`, one Ctrl+Z -- which is why the commit is a single
    `transaction` and not twenty `addSite` calls."""
    r = drive(blank, tmp_path, NEW + """
const before = nodeIds().length;
E.sketchDraw('rect', [0, 0], [6, 4], {});
const mid = { nodes: nodeIds().length, segs: segIds().length, edits: E.geom().length };
E.undoGroup();
const after = { nodes: nodeIds().length, segs: segIds().length };
E.redoGroup();
const again = { nodes: nodeIds().length, segs: segIds().length };
console.log(JSON.stringify({ before: before, mid: mid, after: after, again: again }));
""")
    assert r["before"] == 0
    assert r["mid"]["nodes"] == 20 and r["mid"]["segs"] == 20
    assert r["after"] == {"nodes": 0, "segs": 0}, (
        "one undo must take back the whole shape, not one site of it")
    assert r["again"] == {"nodes": 20, "segs": 20}, "and one redo must put it back"


def test_a_closed_shape_can_be_rotated_as_a_loop(blank, tmp_path):
    """The point of declaring the orbit: `p.rotate` is the one instruction that moves every
    ion on a loop at once, and it needs a loop with `closed: true`."""
    r = drive(blank, tmp_path, NEW + """
const drew = E.sketchDraw('rect', [0, 0], [6, 4], {});
const lid = drew.loop;
const prog = E.applyProgramSource('p.fill("' + lid + '")\\np.rotate(1)\\n');
console.log(JSON.stringify({ lid: lid, ok: prog.ok,
                             errors: (prog.errors||[]).map(e => e.message),
                             problems: (prog.problems||[]).map(e => e.message),
                             statements: E.program().length }));
""")
    assert r["ok"] is True, (r["errors"], r["problems"])
    assert r["statements"] == 2, "fill and rotate both lowered onto the drawn ring"


def test_a_line_is_an_open_register(blank, tmp_path):
    """`chain(n)`'s own shape: n sites in a row, no closed orbit, two degree-1 ends, and
    the `kind: 'path'` loop the generator declares for them."""
    r = drive(blank, tmp_path, NEW + """
const drew = E.sketchDraw('line', [0, 0], [6, 0], {});
const d = dev();
console.log(JSON.stringify({ ok: drew.ok, nodes: nodeIds().length, segs: segIds().length,
                             loops: loops(), lints: geomLints(),
                             degrees: Object.values(degrees()).sort(),
                             ys: Array.from(new Set(Object.values(d.nodes).map(n => Number(n.pos[1])))) }));
""")
    assert r["ok"] is True
    assert r["nodes"] == 7, "0 .. 6 inclusive: the drawn extent is the authority"
    assert r["segs"] == 6
    assert r["degrees"] == [1, 1, 2, 2, 2, 2, 2], "two ends, five interior"
    assert len(r["loops"]) == 1
    assert r["loops"][0]["closed"] is False and r["loops"][0]["kind"] == "path"
    assert r["lints"] == []
    assert r["ys"] == [0.0]


def test_an_ellipse_is_polygonised_at_one_side_per_lattice_unit(blank, tmp_path):
    """The number of sides comes from the DRAWN circumference, so a bigger circle holds
    more ions -- and every corner of the polygon still leaves the two rails at least the
    device's minimum angle apart (R20), which a regular n-gon does by construction for
    n >= 3 at 60 degrees."""
    import math
    rx = ry = 4.0
    circ = math.pi * (3 * (rx + ry) - math.sqrt((3 * rx + ry) * (rx + 3 * ry)))
    want = max(3, round(circ))                     # one side per lattice unit
    r = drive(blank, tmp_path, NEW + """
const drew = E.sketchDraw('ellipse', [0, 0], [8, 8], {});
console.log(JSON.stringify({ ok: drew.ok, problems: (drew.problems||[]).map(p=>p.message),
                             nodes: nodeIds().length, segs: segIds().length,
                             loops: loops(), lints: geomLints() }));
""")
    assert r["ok"] is True, r["problems"]
    assert r["nodes"] == want, f"a circle of circumference {circ:.2f} wants {want} sides"
    assert r["segs"] == want
    assert r["loops"][0]["closed"] is True and r["loops"][0]["kind"] == "ring"
    assert r["lints"] == []


def test_shift_makes_the_ellipse_a_circle(blank, tmp_path):
    r = drive(blank, tmp_path, NEW + """
const drew = E.sketchDraw('ellipse', [0, 0], [8, 3], { shift: true });
const pts = Object.values(dev().nodes).map(n => [Number(n.pos[0]), Number(n.pos[1])]);
const rad = pts.map(p => Math.hypot(p[0]-4, p[1]-4));   // shift squared the 8 x 3 drag
console.log(JSON.stringify({ ok: drew.ok, spread: Math.max(...rad) - Math.min(...rad),
                             r: Math.max(...rad) }));
""")
    assert r["ok"] is True
    assert r["spread"] < 2e-3, "with shift every site is the same distance from the centre"
    assert abs(r["r"] - 4.0) < 2e-3, "the longer drag drives both axes"


def test_a_polyline_closes_on_its_first_point_and_ends_open_otherwise(blank, tmp_path):
    r = drive(blank, tmp_path, NEW + """
const open_ = E.sketchDraw('poly', [[0,0],[4,0],[4,4]], null, { closed: false });
const a = { ok: open_.ok, loops: loops(), nodes: nodeIds().length };
E.undoGroup();
const shut = E.sketchDraw('poly', [[0,0],[4,0],[4,4],[0,4]], null, { closed: true });
const b = { ok: shut.ok, loops: loops(), nodes: nodeIds().length, lints: geomLints() };
console.log(JSON.stringify({ open: a, closed: b }));
""")
    assert r["open"]["ok"] is True
    assert r["open"]["loops"][0]["closed"] is False
    assert r["open"]["nodes"] == 9, "0..4 along x then 1..4 up y"
    assert r["closed"]["ok"] is True
    assert r["closed"]["loops"][0]["closed"] is True
    assert r["closed"]["loops"][0]["kind"] == "ring"
    assert r["closed"]["nodes"] == 16, "a 4 x 4 square: perimeter 16 lattice units"
    assert r["closed"]["lints"] == []


# ======================================================================================
# C. JUNCTIONS, AUTOMATICALLY -- WHERE A JUNCTION ACTUALLY IS
# ======================================================================================


def test_a_crossing_mints_a_junction_and_splits_the_rail_it_crossed(blank, tmp_path):
    """Rule R21 forbids two rails crossing without a shared node.  So where the new shape
    crosses an existing rail the studio inserts a junction AT the crossing and splits the
    crossed segment in two -- which is the "a Junction should be automatically added" the
    request asked for, at the place a junction is: a real meeting of four rails.
    """
    r = drive(blank, tmp_path, NEW + """
E.sketchDraw('line', [0, 0], [6, 0], {});
const first = { nodes: nodeIds().length, segs: segIds().length, ids: segIds() };
const drew = E.sketchDraw('line', [3.5, -2], [3.5, 2], {});
const d = dev();
const junc = Object.entries(d.nodes).filter(([k, n]) => n.kind === 'junction')
  .map(([k, n]) => ({ id: k, pos: [Number(n.pos[0]), Number(n.pos[1])], deg: degrees()[k] }));
console.log(JSON.stringify({
  ok: drew.ok, problems: (drew.problems||[]).map(p => p.message),
  first: first, nodes: nodeIds().length, segs: segIds().length, segIds: segIds(),
  junctions: junc, lints: geomLints(),
  gone: first.ids.filter(s => !segIds().includes(s)),
}));
""")
    assert r["ok"] is True, r["problems"]
    # the first line: 7 sites, 6 rails.  The second: 4 sites (-2, -1, 1, 2) plus the
    # junction at (3.5, 0) -- so 11 nodes, and the crossed rail became two.
    assert r["first"] == {"nodes": 7, "segs": 6, "ids": r["first"]["ids"]}
    assert len(r["junctions"]) == 1, r["junctions"]
    j = r["junctions"][0]
    assert j["pos"] == [3.5, 0.0], "the junction sits exactly where the rails crossed"
    assert j["deg"] == 4, (
        "four rails meet there: the two halves of the rail it split, and the two of the "
        "shape that crossed it")
    assert len(r["gone"]) == 1, (
        f"exactly one existing rail was replaced by its two halves: {r['gone']}")
    assert r["nodes"] == 12 and r["segs"] == 11, (
        "7 + 4 sites + 1 junction; 6 - 1 + 2 halves + 4 new rails")
    assert r["lints"] == [], f"a crossing must leave no R21 behind: {r['lints']}"


def test_a_shape_that_ends_on_an_existing_node_shares_it(blank, tmp_path):
    """The other way two rails may meet: at a node they both end at.  The existing site is
    REUSED -- not re-declared as a junction, which would throw away the trap the user
    built.  R18 already makes it a junction by degree."""
    r = drive(blank, tmp_path, NEW + """
E.sketchDraw('line', [0, 0], [4, 0], {});
const drew = E.sketchDraw('line', [2, -3], [2, 0], {});
const d = dev();
console.log(JSON.stringify({ ok: drew.ok, nodes: nodeIds().length,
                             junctions: Object.values(d.nodes).filter(n => n.kind === 'junction').length,
                             deg: degrees(), lints: geomLints() }));
""")
    assert r["ok"] is True
    # 5 sites on the first line, 3 more on the spur (y = -3, -2, -1); the y = 0 end is the
    # existing site
    assert r["nodes"] == 8
    assert r["junctions"] == 0, (
        "no junction node is minted at a site the shape merely ends on: R18 prices it as "
        "one by degree, and the zone and capacity are kept")
    assert max(r["deg"].values()) == 3
    assert r["lints"] == []


def test_a_crossing_past_the_degree_ceiling_is_refused_and_names_R19(blank, tmp_path):
    r = drive(blank, tmp_path, NEW + """
E.sketchDraw('line', [0, 0], [4, 0], {});
E.sketchDraw('line', [2, -2], [2, 2], {});
const at2 = degrees()['k1.s2'];
const plan = E.sketchPlan('line', { x: 0, y: -2 }, { x: 4, y: 2 }, {});
const before = nodeIds().length;
const drew = E.sketchDraw('line', [0, -2], [4, 2], {});
console.log(JSON.stringify({ at2: at2, plan: { ok: plan.ok, rule: plan.rule, why: plan.why },
                             ok: drew.ok,
                             problems: (drew.problems||[]).map(p => p.code + ': ' + p.message),
                             before: before, after: nodeIds().length,
                             max_deg: Math.max(...Object.values(degrees())),
                             lints: geomLints() }));
""")
    assert r["at2"] == 4, "the crossed node is already at the ceiling"
    assert r["plan"]["ok"] is False
    assert r["plan"]["rule"] == "R19", r["plan"]
    assert "degree 6" in r["plan"]["why"] and "at most 4" in r["plan"]["why"], r["plan"]["why"]
    assert r["ok"] is False
    assert any(p.startswith("R19") for p in r["problems"]), r["problems"]
    assert r["after"] == r["before"], "a refused shape must leave nothing behind"
    assert r["max_deg"] == 4 and r["lints"] == []


def test_a_corner_tighter_than_the_minimum_angle_is_refused_and_names_R20(blank, tmp_path):
    """R20: two rails at one node must subtend at least `budget.min_rail_angle_deg`
    (60 by default).  The readout says the angle while the shape is still being drawn, and
    the release does not commit it."""
    r = drive(blank, tmp_path, NEW + """
// a 30-DEGREE CORNER at (6, 0): the two rails leave it 30 degrees apart, so the second
// point is 6 units away in the direction 180 - 30 from the first rail
const th = Math.PI * (180 - 30) / 180;
const pts = [[0, 0], [6, 0], [6 + 6 * Math.cos(th), 6 * Math.sin(th)]];
const plan = E.sketchPlan('poly', { pts: pts, closed: false }, null, {});
const before = nodeIds().length;
const drew = E.sketchDraw('poly', pts, null, { closed: false });
console.log(JSON.stringify({ plan: { ok: plan.ok, rule: plan.rule, why: plan.why },
                             ok: drew.ok,
                             problems: (drew.problems||[]).map(p => p.code + ': ' + p.message),
                             before: before, after: nodeIds().length,
                             toasts: E.toasts().map(t => t.kind + ': ' + t.message) }));
""")
    assert r["plan"]["ok"] is False
    assert r["plan"]["rule"] == "R20", r["plan"]
    assert re.search(r"corner of 30\.0 degrees", r["plan"]["why"]), r["plan"]["why"]
    assert "min_rail_angle_deg" in r["plan"]["why"]
    assert r["ok"] is False
    assert any(p.startswith("R20") for p in r["problems"]), r["problems"]
    assert r["after"] == r["before"] == 0, "a refused shape must not commit"
    assert any("R20" in t for t in r["toasts"]), (
        f"the refusal must name the rule out loud: {r['toasts']}")


def test_every_shape_this_tool_can_draw_leaves_no_geometry_finding(blank, tmp_path):
    """The guarantee, over all four tools on one canvas: `Q.lint` -- the pass that judges
    R19, R20 and R21 on the committed document -- must find nothing the sketch put there."""
    r = drive(blank, tmp_path, NEW + """
const out = { steps: [] };
const go = (tool, a, b, mod) => {
  const r = (tool === 'poly') ? E.sketchDraw('poly', a, null, mod || {})
                              : E.sketchDraw(tool, a, b, mod || {});
  out.steps.push({ tool: tool, ok: !!r.ok,
                   problems: (r.problems||[]).map(p => p.message),
                   lints: geomLints() });
};
go('rect', [0, 0], [6, 4]);
go('line', [-4, 2], [10, 2]);
go('ellipse', [0, 10], [8, 18]);
go('poly', [[-6, -6], [-6, 8], [-2, 12]], null, { closed: false });
out.final_lints = geomLints();
out.nodes = nodeIds().length;
console.log(JSON.stringify(out));
""")
    for step in r["steps"]:
        assert step["ok"] is True, (step["tool"], step["problems"])
        assert step["lints"] == [], (step["tool"], step["lints"])
    assert r["final_lints"] == []
    assert r["nodes"] > 40


def test_an_endpoint_dragged_near_a_site_lands_on_it(blank, tmp_path):
    """The ruler's `measureSnap` is reused rather than re-derived: an end within a few
    pixels of a site or a rail belongs to that site or that rail, so a second shape can be
    attached to the first without hitting the exact coordinate."""
    r = drive(blank, tmp_path, NEW + """
E.sketchDraw('line', [0, 0], [4, 0], {});
const first = nodeIds().length;
E.setSnap(false);
E.sketchTool('line');
// start three screen pixels off the site at (2, 0): near enough to be that site
const p = (x, y, dx) => [L.ox + x * L.sx + (dx || 0), L.oy + y * L.sy];
E.sketchDown(...p(2, 0, 3), {});
E.sketchMove(...p(2, -3), {});
const res = E.sketchUp(...p(2, -3), {});
console.log(JSON.stringify({ ok: !!(res && res.ok), first: first,
                             nodes: nodeIds().length,
                             deg: degrees(), lints: geomLints(),
                             junctions: Object.values(dev().nodes).filter(n => n.kind === 'junction').length }));
""")
    assert r["ok"] is True
    assert r["first"] == 5
    assert r["nodes"] == 8, "the spur reused the site it started on rather than doubling it"
    assert max(r["deg"].values()) == 3, r["deg"]
    assert r["junctions"] == 0
    assert r["lints"] == []


def test_a_sketch_holds_the_view_the_way_every_other_edit_does(blank, tmp_path):
    """A sketch is an EDIT, so the drawing must not re-fit under it: the shape has to stay
    where it was drawn.  Same guarantee tests/test_placement.py asserts for a placement."""
    r = drive(blank, tmp_path, NEW + """
const view = () => [L.sx, L.sy, L.ox, L.oy];
E.sketchDraw('line', [0, 0], [4, 0], {});
const a = view();
E.sketchDraw('rect', [0, 4], [9, 12], {});
const b = view();
E.refit();
const c = view();
console.log(JSON.stringify({ a: a, b: b, c: c }));
""")
    assert r["a"] == r["b"], (
        f"the second shape re-fitted the drawing: {r['a']} -> {r['b']}")
    assert r["c"] != r["b"], "Fit must still re-fit a device that grew past its frame"


# ======================================================================================
# D. THE PREVIEW, AND THE WORDS IT USES
# ======================================================================================


def test_the_readout_gives_the_size_the_sites_and_what_a_corner_is(blank, tmp_path):
    """A live preview that only draws a dashed box is a preview that says nothing.  It has
    to give the size in micrometres on both axes, how many trapping sites the release will
    make, and -- the sentence this whole file is about -- that a corner is a bend."""
    r = drive(blank, tmp_path, NEW + """
E.sketchTool('rect');
const p = (x, y) => [L.ox + x * L.sx, L.oy + y * L.sy];
E.sketchDown(...p(0, 0), {});
const mid = E.sketchMove(...p(6, 4), {});
const before = nodeIds().length;
E.sketchUp(...p(6, 4), {});
console.log(JSON.stringify({ mid: mid, before: before, after: nodeIds().length,
                             armed_after: E.sketchOn(),
                             toasts: E.toasts().map(t => t.kind + ': ' + t.message) }));
""")
    mid = r["mid"]
    assert mid["ok"] is True and mid["dragging"] is True
    assert mid["n_sites"] == 20 and mid["closed"] is True and mid["bends"] == 4
    assert mid["w_um"] and mid["h_um"] and mid["w_um"] > mid["h_um"] > 0
    assert "20 trapping sites" in mid["text"]
    assert "corner: a bend (R18), not a junction" in mid["text"], mid["text"]
    # 6 lattice units at 464 um each: 2.784 mm, which is what `fmtUm` prints at that size
    assert abs(mid["w_um"] - 6 * 464) < 1e-6 and abs(mid["h_um"] - 4 * 464) < 1e-6
    assert re.search(r"(nm|um|µm|mm)", mid["text"]), mid["text"]
    assert r["before"] == 0 and r["after"] == 20, "the release committed the shape"
    assert r["armed_after"] is None, "one shape per arming, as a tile disarms when it lands"
    assert any("bend (R18)" in t for t in r["toasts"]), r["toasts"]


def test_the_readout_says_the_reason_a_shape_would_be_refused(blank, tmp_path):
    r = drive(blank, tmp_path, NEW + """
E.sketchTool('rect');
const p = (x, y) => [L.ox + x * L.sx, L.oy + y * L.sy];
E.sketchDown(...p(0, 0), {});
const tiny = E.sketchMove(...p(0.2, 0.2), {});
const res = E.sketchUp(...p(0.2, 0.2), {});
console.log(JSON.stringify({ tiny: tiny, committed: !!(res && res.ok),
                             nodes: nodeIds().length }));
""")
    assert r["tiny"]["ok"] is False
    assert r["tiny"]["why"], "the readout must say WHY"
    assert r["tiny"]["text"] == r["tiny"]["why"] or r["tiny"]["text"].endswith(r["tiny"]["why"])
    assert r["committed"] is False and r["nodes"] == 0


def test_escape_drops_the_shape_then_leaves_the_tool(blank, tmp_path):
    """One thing per press -- the ruler's rule, for the same reason."""
    r = drive(blank, tmp_path, NEW + """
E.sketchTool('poly');
const p = (x, y) => [L.ox + x * L.sx, L.oy + y * L.sy];
E.sketchClick(...p(0, 0), {});
E.sketchClick(...p(4, 0), {});
const out = { pts: E.sketchPoints().length };
out.first = E.cancelGesture();
out.pts_after = E.sketchPoints().length;
out.tool = E.sketchOn();
out.second = E.cancelGesture();
out.tool_after = E.sketchOn();
console.log(JSON.stringify(out));
""")
    assert r["pts"] == 2
    assert r["first"] == "sketch" and r["pts_after"] == 0
    assert r["tool"] == "poly", "the first Escape drops the points and keeps the tool"
    assert r["second"] == "sketch" and r["tool_after"] is None


def test_a_generator_device_says_what_to_press_rather_than_half_committing(seeded, tmp_path):
    """A generator device has no builder, so `d.site` cannot be hoisted into it and there
    is no post-seal verb that declares a loop.  Rather than emit half a shape, the refusal
    names the button that makes the device explicit."""
    r = drive(seeded, tmp_path, """
E.setDesignMode('sketch');
const before = nodeIds().length;
const drew = E.sketchDraw('line', [0, -6], [4, -6], {});
console.log(JSON.stringify({ ok: drew.ok,
                             problems: (drew.problems||[]).map(p => p.code + ': ' + p.message),
                             before: before, after: nodeIds().length }));
""")
    assert r["ok"] is False
    assert any("explode to explicit" in p for p in r["problems"]), r["problems"]
    assert r["after"] == r["before"]


# ======================================================================================
# E. DISCOVERABILITY -- every control the sketch adds is findable
# ======================================================================================


#: The Sketch/Parts switch is markup in `render.py`.  The four shape tools are NOT any
#: more: the stage toolbar's four buttons were pure duplicates of the rail's tiles, wired
#: to the same `sketchTool` verb, and they were removed on 2026-09-17 when the right-click
#: menu took over the per-element work and the bar was thinned.  The tiles that remain are
#: built at run time by `shapeSection()` in editor.js, which is where their `data-hint`
#: now comes from -- and `test_the_rail_leads_with_the_shapes_in_sketch_mode...` below
#: asserts they are on the page.
MARKUP_HINTS = ["mode", "mode:sketch", "mode:parts"]
NEW_HINTS = MARKUP_HINTS + ["shape:rect", "shape:ellipse", "shape:line", "shape:poly"]


def test_every_new_control_carries_a_hint_and_that_hint_exists():
    """A control without a `data-hint` AND a HINTS row is invisible to the hover card, the
    Explain captions, the `?` guide and the Ctrl+K search all at once."""
    markup = RENDER_PY.read_text(encoding="utf-8")
    for key in MARKUP_HINTS:
        assert f'data-hint="{key}"' in markup, f"no control carries data-hint={key!r}"
    src = EDITOR_JS.read_text(encoding="utf-8")
    assert "b.setAttribute('data-hint', 'shape:' + t);" in src, (
        "the rail's shape tiles are the only shape controls left; they must still carry "
        "a data-hint, or four surfaces go silent at once")
    for key in NEW_HINTS:
        assert f"'{key}':" in src, f"HINTS has no row for {key!r}"


def test_the_hints_are_reachable_through_the_page_s_own_lookup(blank, tmp_path):
    r = drive(blank, tmp_path, """
const keys = %s;
console.log(JSON.stringify(Object.fromEntries(
  keys.map(k => [k, E.hintFor(k) ? { t: E.hintFor(k).t, k: E.hintFor(k).k || null,
                                     n: (E.hintFor(k).d || '').length } : null]))));
""" % json.dumps(NEW_HINTS))
    for key in NEW_HINTS:
        got = r[key]
        assert got is not None, f"hintFor({key!r}) is silent"
        assert got["t"], key
        assert got["n"] > 60, f"{key}: the sentence is too short to explain anything"
        assert got["k"], f"{key}: no key is named, but every one of these has a binding"


def test_the_keymap_documents_every_key_the_sketch_binds(blank, tmp_path):
    """The `?` overlay is rendered from KEYMAP, so a binding without its line of help is
    impossible by construction -- this asserts the bindings are actually THERE."""
    r = drive(blank, tmp_path, """
const rows = E.keyHelp();
const verbs = {};
for (const k of ['d', 'r', 'e', 'n', 'p']) verbs[k] = E.keyGesture(k, {}, {});
console.log(JSON.stringify({ verbs: verbs,
                             keys: rows.keys.map(r => r[0]),
                             docs: Object.fromEntries(rows.keys),
                             pointer: rows.pointer.map(p => p[0]) }));
""")
    assert r["verbs"] == {"d": "design-mode", "r": "shape-rect", "e": "shape-ellipse",
                          "n": "shape-line", "p": "shape-poly"}
    for label in ["d", "r", "e", "n", "p"]:
        assert label in r["keys"], f"the guide has no row for {label!r}"
        assert len(r["docs"][label]) > 25, f"{label}: the help line says nothing"
    assert any("Sketch" in row for row in r["pointer"]), (
        "POINTER_HELP must describe the drag that draws a shape")


def test_the_rail_leads_with_the_shapes_in_sketch_mode_and_is_untouched_in_parts(blank, tmp_path):
    r = drive(blank, tmp_path, """
const walk = (n, out = []) => { if (!n) return out; out.push(n);
  for (const k of (n.children || [])) walk(k, out); return out; };
const tiles = () => walk(document.getElementById('palBody'))
  .filter(n => n.attrs && n.attrs['data-el']).map(n => n.attrs['data-el']);
E.setDesignMode('sketch');
const sketch = tiles();
E.setDesignMode('parts');
const parts = tiles();
console.log(JSON.stringify({ sketch: sketch, parts: parts }));
""")
    shapes = ["shape:rect", "shape:ellipse", "shape:line", "shape:poly"]
    assert r["sketch"][:4] == shapes, f"Sketch must lead with the shapes: {r['sketch'][:6]}"
    for t in ["site", "junction", "segment", "loop"]:
        assert t in r["sketch"], f"{t} must stay reachable in Sketch mode"
    assert not any(x.startswith("shape:") for x in r["parts"]), (
        "Parts is exactly the rail it was")
    assert r["parts"][:4] == ["site", "junction", "segment", "loop"]


def test_the_empty_canvas_leads_with_the_shape_and_keeps_every_start_card(blank, tmp_path):
    r = drive(blank, tmp_path, """
console.log(JSON.stringify({ html: document.getElementById('stageEmpty').innerHTML }));
""")
    h = r["html"]
    assert h.index("Draw the shape your ions travel on") < h.index("ready-made device"), (
        "the empty canvas must lead with the shape and keep the cards below it")
    for t in ["rect", "ellipse", "line", "poly"]:
        assert f'data-shape="{t}"' in h, f"no {t} button on the empty canvas"
    assert 'data-blank="1"' in h, "the Blank canvas card must still be there"
    assert h.count("data-gen=") >= 3, "every generator card must still be there"
    assert 'data-arm="site"' in h and 'data-arm="junction"' in h
    assert "data-open-pick=" in h, "the shipped-device picker must still be there"
    assert "bend" in h and "R18" in h, (
        "the empty state has to say what a corner becomes, because that is the one thing "
        "the request got wrong")


def test_a_view_only_page_cannot_be_drawn_on(blank, tmp_path):
    """The website embeds the studio with `setViewOnly(true)`; the shape tools live inside
    `#etools`, which those pages hide, and the verbs are dead besides."""
    r = drive(blank, tmp_path, NEW + """
E.sketchTool('rect');
E.setViewOnly(true);
const out = { armed: E.sketchOn(), claim: E.claim(0, 0, {}),
              rearm: E.sketchTool('rect'),
              key: E.keyGesture('r', {}, {}), mode_key: E.keyGesture('d', {}, {}) };
const drew = E.sketchDraw('rect', [0, 0], [6, 4], {});
out.drew = !!drew.ok;
E.setViewOnly(false);
console.log(JSON.stringify(out));
""")
    assert r["armed"] is None and r["rearm"] is None
    assert r["claim"] == "pan"
    assert r["key"] is None and r["mode_key"] is None
    assert r["drew"] is False
