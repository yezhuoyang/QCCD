"""Placing something must not move anything else.

Two defects, both reported on 2026-09-17 against the design studio:

  * a tile stayed armed after its element landed, so the next click anywhere dropped
    another site the user had not asked for;
  * every placement RE-FITTED the drawing.  The device had grown, so the fit changed,
    and the model point under the pointer changed with it: four sites meant for one row
    came out as a staircase, each one further from where it was clicked.

The fix is one option on the layout engine -- `hold`, the scale and origin an edit keeps
-- plus one line in the placement path.  Both are asserted here through the editor's own
verbs, over the page the studio actually ships.
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

NODE = shutil.which("node")
SHIM = (Path(__file__).parent / "shim.mjs").as_uri()

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


def build(tmp: Path) -> Path:
    page = tmp / "studio.html"
    subprocess.run([sys.executable, "-m", "qccd", "studio", "-o", str(page)],
                   cwd=ROOT, capture_output=True, timeout=900, check=True)
    return page


@pytest.fixture(scope="module")
def blank(tmp_path_factory) -> Path:
    return build(tmp_path_factory.mktemp("place"))


def drive(page: Path, tmp_path: Path, body: str) -> dict:
    """Run `body` in the page's own scope.  `L` is exposed as well as `EDITOR`: the whole
    question here is what the layout does, and the layout IS `L`."""
    js = tmp_path / "p.mjs"
    js.write_text(f"import {{ loadPage }} from '{SHIM}';\n"
                  "loadPage(process.argv[2], "
                  "';globalThis.__E=EDITOR;globalThis.__L=L;globalThis.__VB=()=>VB;');\n"
                  "const E = globalThis.__E, L = globalThis.__L, VB = globalThis.__VB;\n"
                  "const view = () => [L.sx, L.sy, L.ox, L.oy];\n"
                  "const screenOf = (x, y) => [L.ox + x * L.sx, L.oy + y * L.sy];\n"
                  "const place = (type, x, y, mods) => {\n"
                  "  const p = screenOf(x, y);\n"
                  "  E.arm(type);\n"
                  "  E.ghostBegin(type, p[0], p[1]);\n"
                  "  E.ghostMove(p[0], p[1]);\n"
                  "  return E.clickStage(p[0], p[1], mods || {});\n"
                  "};\n" + body,
                  encoding="utf-8")
    r = subprocess.run([NODE, str(js), str(page)], capture_output=True, text=True,
                       timeout=600, cwd=ROOT)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr[-2500:]}"
    return json.loads(r.stdout)


NEW = "E.newCanvas({ name:'place', template:null }); E.setMode('edit');\n"


# ---------------------------------------------------------------- one per arming

def test_a_tile_disarms_as_soon_as_its_element_lands(blank, tmp_path):
    r = drive(blank, tmp_path, NEW + """
E.arm('site');
const before = E.armed();
const put = place('site', 0, 0);
console.log(JSON.stringify({ before: before, ok: put.ok,
                             after: E.armed(), nodes: Object.keys(E.state().device.nodes).length }));
""")
    assert r["before"] == "site", "arming the tile is what the click needs"
    assert r["ok"] and r["nodes"] == 1
    assert r["after"] is None, "the tile must disarm the moment its element lands"


def test_shift_keeps_the_tile_armed_for_a_run_of_them(blank, tmp_path):
    r = drive(blank, tmp_path, NEW + """
place('site', 0, 0, { shift: true });
const armed = E.armed();
place('site', 2, 0, { shift: true });
place('site', 4, 0);
console.log(JSON.stringify({ armed: armed, after: E.armed(),
                             nodes: Object.keys(E.state().device.nodes).length }));
""")
    assert r["armed"] == "site", "shift-click places and stays armed"
    assert r["nodes"] == 3
    assert r["after"] is None, "the click without shift disarms as any other does"


def test_a_double_click_after_a_placement_does_not_place_a_second_site(blank, tmp_path):
    """The two clicks of a double-click have already run: the first placed and disarmed,
    so the double-click itself lands on the node it just made."""
    r = drive(blank, tmp_path, NEW + """
place('site', 0, 0);
const n1 = Object.keys(E.state().device.nodes).length;
const p = screenOf(0, 0);
const dbl = E.dblclickStage(p[0], p[1]);
console.log(JSON.stringify({ n1: n1, n2: Object.keys(E.state().device.nodes).length,
                             quiet: dbl === null || dbl === undefined || !!dbl.ok }));
""")
    assert r["n1"] == 1
    assert r["n2"] == 1, "the double-click must not drop a second site on the first"
    assert r["quiet"], "and it refuses nothing out loud: there was nothing to refuse"


# ---------------------------------------------------------------- the held view

def test_placing_something_moves_nothing_that_is_already_drawn(blank, tmp_path):
    """The defect verbatim: four sites clicked along one row must come out on one row."""
    r = drive(blank, tmp_path, NEW + """
const out = { views: [], screens: [] };
for (const x of [0, 1, 2, 3]) {
  place('site', x, 0);
  out.views.push(view());
  out.screens.push(screenOf(0, 0));        // where the FIRST site is drawn, every time
}
const st = E.state().device.nodes;
out.ys = Object.keys(st).map(k => Number(E.unbox ? E.unbox(st[k].pos[1]) : st[k].pos[1]));
out.n = Object.keys(st).length;
console.log(JSON.stringify(out));
""")
    assert r["n"] == 4
    assert r["views"][0] == r["views"][-1], (
        f"the fit changed while placing: {r['views'][0]} -> {r['views'][-1]}")
    assert r["screens"][0] == r["screens"][-1], (
        f"the first site moved on screen while the others were placed: {r['screens']}")
    assert r["ys"] == [0.0, 0.0, 0.0, 0.0], (
        f"every click was on one row; the sites landed on {r['ys']}")


def test_the_element_lands_exactly_where_the_click_was(blank, tmp_path):
    r = drive(blank, tmp_path, NEW + """
E.setSnap(false);
const want = [[0, 0], [2.5, 1.25], [-3.75, 4.5]];
const got = [];
for (const p of want) { place('site', p[0], p[1]); }
const st = E.state().device.nodes;
for (const k of Object.keys(st)) {
  const n = st[k];
  got.push([Number(E.unbox ? E.unbox(n.pos[0]) : n.pos[0]),
            Number(E.unbox ? E.unbox(n.pos[1]) : n.pos[1])]);
}
console.log(JSON.stringify({ want: want, got: got }));
""")
    assert r["got"] == r["want"], "a free placement lands under the pointer, exactly"


def test_fit_is_where_the_drawing_re_fits(blank, tmp_path):
    """The hold is not "never re-fit": `Fit` asks for a fresh one, and gets it."""
    r = drive(blank, tmp_path, NEW + """
place('site', 0, 0);
const held0 = view();
place('site', 12, 9);
const held1 = view();
E.refit();
const fitted = view();
console.log(JSON.stringify({ held0: held0, held1: held1, fitted: fitted }));
""")
    assert r["held0"] == r["held1"], "the second placement held the view"
    assert r["fitted"] != r["held1"], "Fit must re-fit a device that grew past its frame"


def test_a_new_device_re_fits_rather_than_inheriting_the_last_one(blank, tmp_path):
    """A card or a file is a new drawing, not an edit to the one on the stage."""
    r = drive(blank, tmp_path, NEW + """
place('site', 0, 0);
place('site', 1, 0);
const drawn = view();
E.newFromGenerator('ring', { width: 12, height: 2, verticals: 4 }, { name: 'ring' });
const fresh = view();
console.log(JSON.stringify({ drawn: drawn, fresh: fresh,
                             nodes: Object.keys(E.state().device.nodes).length }));
""")
    assert r["nodes"] > 20, f"the generated device is on the stage: {r}"
    assert r["drawn"] != r["fresh"], "a new device is fitted, not drawn at the old scale"
