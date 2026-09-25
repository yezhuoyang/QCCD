"""The sketch's lattice unit, after a slanted shape: a dock spur off a triangle.

Found on 2026-09-24, drawing what a person asked for ("a large triangle and a smaller
triangle"): a triangle's sloped sides put sites at heights a thousandth apart, the Studio read
the lattice unit as the smallest gap between two heights, and every shape drawn after the
triangle laid its sites a fraction of a unit apart.  A one-unit dock spur was refused outright
("trapping sites 0.00 lattice units apart").  `skUnit` now never goes below the distance
between the two closest sites.

The triangle has side 20 with its corners cut by 2, so every corner is 120 degrees (R20): the
smallest of the sizes tried (8 to 50) on which the old unit broke.  Asserted through the
editor's own verbs over the page `qccd studio` ships, with the expectations computed here.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
SHIM = (Path(__file__).parent / "shim.mjs").as_uri()

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

SIDE, CUT = 20.0, 2.0
HEIGHT = SIDE * 3 ** 0.5 / 2


def _along(p, q, t):
    dx, dy = q[0] - p[0], q[1] - p[1]
    n = (dx * dx + dy * dy) ** 0.5
    return [p[0] + dx / n * t, p[1] + dy / n * t]


_A, _B, _C = (0.0, HEIGHT), (SIDE, HEIGHT), (SIDE / 2, 0.0)     # apex up: y grows downward
TRIANGLE = [_along(_A, _B, CUT), _along(_B, _A, CUT), _along(_B, _C, CUT),
            _along(_C, _B, CUT), _along(_C, _A, CUT), _along(_A, _C, CUT)]
SITES = round(3 * SIDE - 3 * CUT)            # one site per lattice unit of perimeter: 54


@pytest.fixture(scope="module")
def page(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("sketch_unit") / "studio.html"
    subprocess.run([sys.executable, "-m", "qccd", "studio", "-o", str(out)],
                   cwd=ROOT, capture_output=True, timeout=900, check=True)
    return out


def drive(page: Path, tmp_path: Path, body: str) -> dict:
    js = tmp_path / "p.mjs"
    js.write_text(f"import {{ loadPage }} from '{SHIM}';\n"
                  "loadPage(process.argv[2], ';globalThis.__E=EDITOR;');\n"
                  "const E = globalThis.__E;\n"
                  "const dev = () => E.state().device;\n"
                  "const ids = () => Object.keys(dev().nodes).sort();\n"
                  "E.newCanvas({ name: 'sk', template: null });\n"
                  f"const tri = E.sketchDraw('poly', {json.dumps(TRIANGLE)}, null, {{ closed: true }});\n"
                  "const before = ids();\n"
                  # the site in the middle of the bottom side, and the point one unit inside from it
                  f"let at = null, bd = 1e9;\n"
                  f"for (const id of before) {{ const p = dev().nodes[id].pos.map(Number);\n"
                  f"  const d = Math.hypot(p[0] - {SIDE / 2}, p[1] - {HEIGHT}); if (d < bd) {{ bd = d; at = id; }} }}\n"
                  "const p0 = dev().nodes[at].pos.map(Number), p1 = [p0[0], p0[1] - 1];\n"
                  "const degree = id => Object.values(dev().segments).filter(s => s.a === id || s.b === id).length;\n"
                  + body, encoding="utf-8")
    r = subprocess.run([NODE, str(js), str(page)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600, cwd=ROOT)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr[-3000:]}"
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_a_spur_drawn_after_a_triangle_is_one_unit_long(page, tmp_path):
    r = drive(page, tmp_path, """
const spur = E.sketchDraw('line', p0, p1, {});
const added = ids().filter(id => !before.includes(id));
console.log(JSON.stringify({ tri: tri.ok, sites: before.length, ok: spur.ok,
  problems: (spur.problems || []).map(p => p.message),
  far: added.map(id => dev().nodes[id].pos.map(Number)), p1: p1, degree: degree(at) }));
""")
    assert r["tri"] is True and r["sites"] == SITES, r
    assert r["ok"] is True, r["problems"]
    assert len(r["far"]) == 1, f"exactly one new site: {r['far']}"
    assert all(abs(a - b) < 2e-3 for a, b in zip(r["far"][0], r["p1"])), "one unit in"
    assert r["degree"] == 3, "the loop site the spur leaves from joins three rails"


def test_a_dock_made_of_parts_is_a_plain_rail_off_the_loop(page, tmp_path):
    """What the design guide tells an agent to do for a dock: a site one unit in, joined to
    its loop site by one rail that belongs to no path (a sketched line would declare one, and
    R22 would then read docking from different sides as different motions)."""
    r = drive(page, tmp_path, """
const made = E.addNodeAt(p1[0], p1[1], { kind: 'site', zone: 'trap' });
const dock = ids().filter(id => !before.includes(id))[0];
const joined = E.joinNodes(at, dock);
const rail = Object.values(dev().segments).find(s => (s.a === at && s.b === dock) || (s.a === dock && s.b === at));
const onPath = Object.values(dev().loops).some(l => l.nodes.includes(dock));
console.log(JSON.stringify({ made: !!made && made.ok !== false, joined: !!joined && joined.ok !== false,
  rail: !!rail, loop: rail ? (rail.loop === undefined ? null : rail.loop) : 'none', onPath: onPath,
  degree: degree(at) }));
""")
    assert r["made"] and r["joined"] and r["rail"], r
    assert r["loop"] is None and r["onPath"] is False, r
    assert r["degree"] == 3
