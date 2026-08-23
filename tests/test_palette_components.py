"""The catalogue, made visible -- and made to draw what it will actually stamp.

Seven components have been reachable from `stampComponent` since the day they shipped and
absent from the menu, which made them a feature only someone reading the source could
find.  This wires them into the rail.  The tests are almost entirely about the avatar,
because a menu picture that lies is the worst thing in this file's neighbourhood: it looks
fine, and the disagreement only surfaces after you have placed the part.

**The avatar is laid out from the component's own records.**  `D.components[name].records`
are the builder calls the stamp replays, so the tile is drawn by running them through the
same `computeLayout` + `buildStatic` pair that draws the canvas.  The tests below count
marks and compare against the records in Python: eight site records must produce eight
site bars, twelve segments twelve rails, and a site of capacity 8 must show `_slots(8)`
ion slots -- six, because that is the clamp the STAGE applies, so the menu inherits the
stage's own rule rather than a second one.

**A pin is drawn, and drawn dashed.**  Two of the seven -- `trap_junction` and
`gate_zone` -- are a single node whose arms are pins rather than geometry, so from the
records alone they are a bare dot, and a tile for a 4-way crossing showing one dot hides
the only thing that makes it a crossing.  They are drawn.  They are also dashed, because
the stamp does NOT create them: a solid stub would be the avatar promising rails that
never arrive, which is the same lie in the other direction.

**A tile that cannot be placed says so before it is clicked.**  `stampComponent` refuses a
component whose required zone type is absent, and every device in `arch/` is missing the
`gate` zone that `gate_zone` needs.  A tile that looks placeable and then refuses is
exactly the "the menu feels broken" complaint this rail was rebuilt to answer.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.arch.library import CATALOG  # noqa: E402

NODE = shutil.which("node")
SHIM = (Path(__file__).parent / "shim.mjs").as_uri()

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

WALK = """
const walk = (n, out = []) => {
  if (!n) return out;
  out.push(n);
  for (const k of (n.children || [])) walk(k, out);
  return out;
};
const tilesOf = () => walk(document.getElementById('palBody')).filter(
  n => n.attrs && n.attrs['data-kind'] === 'component' && n.attrs['data-el']);
const marks = t => {
  const svg = walk(t).find(n => n.tagName === 'svg');
  const kids = svg ? walk(svg).filter(k => k !== svg) : [];
  const dash = kids.filter(k => k.tagName === 'line' && k.attrs['stroke-dasharray']);
  return { rects: kids.filter(k => k.tagName === 'rect').length,
           lines: kids.filter(k => k.tagName === 'line').length,
           dashed: dash.length,
           circles: kids.filter(k => k.tagName === 'circle').length,
           viewBox: svg ? svg.attrs.viewBox : null };
};
"""


def build(tmp: Path, *args) -> Path:
    page = tmp / ("studio" + ("_" + args[1] if args else "") + ".html")
    subprocess.run([sys.executable, "-m", "qccd", "studio", *args, "-o", str(page)],
                   cwd=ROOT, capture_output=True, timeout=900, check=True)
    return page


@pytest.fixture(scope="module")
def blank(tmp_path_factory) -> Path:
    return build(tmp_path_factory.mktemp("pal"))


@pytest.fixture(scope="module")
def seeded(tmp_path_factory) -> Path:
    """A shipped device: none of them declares a `gate` zone type."""
    return build(tmp_path_factory.mktemp("pals"), "--seed", "ring144_24v")


def drive(page: Path, tmp_path: Path, body: str) -> dict:
    js = tmp_path / "p.mjs"
    js.write_text(f"import {{ loadPage }} from '{SHIM}';\n"
                  "loadPage(process.argv[2], ';globalThis.__E=EDITOR;globalThis.__D=D;');\n"
                  "const E = globalThis.__E, D = globalThis.__D;\n" + WALK + body,
                  encoding="utf-8")
    r = subprocess.run([NODE, str(js), str(page)], capture_output=True, text=True,
                       timeout=600, cwd=ROOT)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr[-2500:]}"
    return json.loads(r.stdout)


NEW = "E.newCanvas({ name:'pal', template:null }); E.setMode('edit');\n"


def records_of(name: str) -> dict:
    """What the component is, counted in Python -- the other side of the comparison."""
    comp = CATALOG[name]()
    sites = [r for r in comp.records if r["method"] == "d.site"]
    junc = [r for r in comp.records if r["method"] == "d.junction"]
    segs = [r for r in comp.records if r["method"] == "d.segment"]
    caps = [int(r["kwargs"].get("capacity", 1)) for r in sites]
    return {"nodes": len(sites) + len(junc), "segs": len(segs), "pins": len(comp.pins),
            "slots": sum(min(max(c, 1), 6) for c in caps)}


def test_every_catalogued_component_has_a_tile(blank, tmp_path):
    r = drive(blank, tmp_path, NEW + """
console.log(JSON.stringify({ tiles: tilesOf().map(t => t.attrs['data-el']),
                             catalogue: Object.keys(D.components || {}) }));
""")
    assert sorted(r["catalogue"]) == sorted(CATALOG), r["catalogue"]
    assert sorted(r["tiles"]) == sorted("cmp:" + n for n in CATALOG), r["tiles"]


def test_the_avatar_draws_the_component_it_will_actually_stamp(blank, tmp_path):
    """The one that matters. Every mark in the tile is counted against the records the
    stamp replays -- so a picture cannot drift from what dropping the part produces."""
    r = drive(blank, tmp_path, NEW + """
const out = {};
for (const t of tilesOf()) out[t.attrs['data-el'].slice(4)] = marks(t);
console.log(JSON.stringify(out));
""")
    assert set(r) == set(CATALOG)
    for name, got in sorted(r.items()):
        want = records_of(name)
        assert got["rects"] == want["nodes"], (
            f"{name}: {got['rects']} bars drawn for {want['nodes']} node records")
        assert got["lines"] == want["segs"] + want["pins"], (
            f"{name}: {got['lines']} lines for {want['segs']} segments + "
            f"{want['pins']} pins")
        assert got["circles"] == want["slots"], (
            f"{name}: {got['circles']} ion slots drawn, records give {want['slots']}")
        assert got["viewBox"], f"{name} has no viewBox, so it is cropped to nothing"


def test_a_pin_is_drawn_dashed_because_the_stamp_does_not_create_it(blank, tmp_path):
    """`trap_junction` IS its pins -- one junction node and four arms that are attachment
    points, not rails. Undrawn it is a dot; drawn solid it promises geometry."""
    r = drive(blank, tmp_path, NEW + """
const out = {};
for (const t of tilesOf()) out[t.attrs['data-el'].slice(4)] = marks(t);
console.log(JSON.stringify(out));
""")
    tj = r["trap_junction"]
    assert records_of("trap_junction")["segs"] == 0, "the fixture is not what this tests"
    assert tj["lines"] == 4, "a 4-way crossing must show four arms"
    assert tj["dashed"] == 4, "every one of them is a pin, so every one must be dashed"

    loop = r["transport_loop"]
    assert loop["dashed"] == records_of("transport_loop")["pins"], (
        "a real segment must not be dashed, or the two become indistinguishable")
    assert loop["lines"] > loop["dashed"], "transport_loop is mostly real rail"


def test_a_component_whose_zone_is_missing_is_blocked_and_will_not_arm(seeded, tmp_path):
    """No device in `arch/` declares a `gate` zone, so `gate_zone` cannot be stamped on
    one. The tile has to say that before it is clicked."""
    r = drive(seeded, tmp_path, """
E.setMode('edit');
const by = {}; for (const t of tilesOf()) by[t.attrs['data-el'].slice(4)] = t;
const before = Object.keys(E.state().device.nodes).length;
const armed = E.arm('cmp:gate_zone');
const stamped = E.stampComponent('gate_zone', 0, 0, 0);
console.log(JSON.stringify({
  zones: Object.keys(E.state().zone_types).sort(),
  blocked: by.gate_zone.attrs['data-blocked'] || null,
  loop_blocked: by.transport_loop.attrs['data-blocked'] || null,
  armed: armed, armed_el: E.armed(),
  stamp_refused: stamped.ok === false,
  before: before, after: Object.keys(E.state().device.nodes).length }));
""")
    assert "gate" not in r["zones"], r["zones"]
    assert r["blocked"] == "gate", "the tile must name the zone it is missing"
    assert r["loop_blocked"] is None, "a placeable component must not be marked blocked"
    assert r["armed"] is None, "a blocked component must refuse to arm"
    assert r["armed_el"] is None
    assert r["stamp_refused"], "and stamping it directly must still be refused"
    assert r["after"] == r["before"], "a refused stamp left nodes behind"


def test_arming_a_component_and_clicking_places_exactly_its_nodes(blank, tmp_path):
    r = drive(blank, tmp_path, NEW + """
const armed = E.arm('cmp:transport_loop');
const before = Object.keys(E.state().device.nodes).length;
const placed = E.stampComponent('transport_loop', 2, 2, 0);
console.log(JSON.stringify({ armed: armed && armed.type, kind: armed && armed.kind,
  before: before, after: Object.keys(E.state().device.nodes).length,
  ok: placed.ok !== false, problems: E.problems().length }));
""")
    assert r["armed"] == "cmp:transport_loop"
    assert r["kind"] == "component"
    assert r["ok"]
    assert r["after"] - r["before"] == records_of("transport_loop")["nodes"]
    assert r["problems"] == 0


def test_arming_a_component_that_does_not_exist_is_refused(blank, tmp_path):
    """`arm` looks components up before the `palette()` lookup that would reject them;
    a typo must not fall through into that path and arm something else."""
    r = drive(blank, tmp_path, NEW + """
const bad = E.arm('cmp:no_such_part');
console.log(JSON.stringify({ bad: bad, armed: E.armed() }));
""")
    assert r["bad"] is None
    assert r["armed"] is None
