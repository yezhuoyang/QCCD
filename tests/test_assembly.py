"""Placing parts is not assembling a machine.

Two components dropped at neighbouring coordinates are two disconnected devices that
happen to be drawn near each other.  What makes an `ancilla_dock` a dock is that its pin
is JOINED to a rail -- and the moment it is, that rail node becomes degree 3, which is
what makes the cost model charge a junction on every rigid hop through it (R18).  The
degree histogram is therefore the honest test of whether an assembly happened: parts
placed side by side leave every node at degree <= 2.

The rollback matters as much as the join.  `attachComponent` places and wires in one act,
so a failure has to leave nothing behind -- and the first version did not: `undo()` pops a
SINGLE edit while a component is as many edits as it has records, so a failed attach left
two thirds of a dock on the canvas.  `transaction` stamps every record of one placement
with a group id for exactly this reason, and `undoGroup()` is what reads it.
"""

from __future__ import annotations

import collections
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.arch import Architecture  # noqa: E402
from qccd.arch.component import instance_of  # noqa: E402
from qccd.verify.rules import architecture_violations  # noqa: E402

NODE = shutil.which("node")
SHIM = (Path(__file__).parent / "shim.mjs").as_uri()

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


@pytest.fixture(scope="module")
def studio(tmp_path_factory) -> Path:
    page = tmp_path_factory.mktemp("studio") / "studio.html"
    subprocess.run([sys.executable, "-m", "qccd", "studio", "-o", str(page)],
                   cwd=ROOT, capture_output=True, timeout=900, check=True)
    return page


def drive(page: Path, tmp_path: Path, body: str, out: Path | None = None) -> dict:
    js = tmp_path / "d.mjs"
    js.write_text("import fs from 'fs';\n"
                  f"import {{ loadPage }} from '{SHIM}';\n"
                  "loadPage(process.argv[2], ';globalThis.__E=EDITOR;');\n"
                  "const E = globalThis.__E;\n"
                  "const N = () => Object.keys(E.state().device.nodes).length;\n"
                  "const DEG = id => { const d = E.state().device; let n = 0;\n"
                  "  for (const k in d.segments) if (d.segments[k].a===id||d.segments[k].b===id) n++;\n"
                  "  return n; };\n" + body,
                  encoding="utf-8")
    args = [NODE, str(js), str(page)] + ([str(out)] if out else [])
    r = subprocess.run(args, capture_output=True, text=True, timeout=600, cwd=ROOT)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr[-2500:]}"
    return json.loads(r.stdout)


SETUP = """
E.newCanvas({ name:'asm', template:null }); E.setMode('edit');
const loop = E.stampComponent('transport_loop', 0, 0, 0);
"""


def test_attaching_a_dock_raises_the_rails_degree(studio, tmp_path):
    """The whole point. Degree 2 is a rail; degree 3 is a dock, and the cost model
    charges a junction crossing for it."""
    r = drive(studio, tmp_path, SETUP + """
const before = DEG(loop.instance + '.t1');
const a = E.attachComponent('ancilla_dock', 1, -1.5, 2, 'rail', loop.instance + '.t1');
console.log(JSON.stringify({ ok: a.ok !== false, before: before,
  after: DEG(loop.instance + '.t1'), problems: E.problems().length }));
""")
    assert r["ok"], "the dock would not attach"
    assert r["before"] == 2, "a plain rail node should be degree 2"
    assert r["after"] == 3, "attaching a dock must make the rail node degree 3"
    assert r["problems"] == 0


def test_a_failed_attach_leaves_nothing_behind(studio, tmp_path):
    """`undo()` pops one edit; a component is as many edits as it has records. The first
    version of this rollback left two thirds of a dock on the canvas."""
    r = drive(studio, tmp_path, SETUP + """
const before = N();
const bad = E.attachComponent('ancilla_dock', 5, 5, 0, 'rail', 'NO_SUCH_NODE');
console.log(JSON.stringify({ refused: bad.ok === false, before: before, after: N(),
  codes: (bad.problems||[]).map(p => p.code) }));
""")
    assert r["refused"], "attaching to a node that does not exist must be refused"
    assert r["after"] == r["before"], (
        f"a failed attach left {r['after'] - r['before']} node(s) behind")
    assert "rolled_back" in r["codes"]


def test_an_assembled_machine_loads_in_python_with_the_join_intact(studio, tmp_path):
    """The join has to survive export, or the browser and the toolchain disagree about
    what was built."""
    out = tmp_path / "asm.arch.json"
    drive(studio, tmp_path, SETUP + """
E.attachComponent('ancilla_dock', 1, -1.5, 2, 'rail', loop.instance + '.t1');
fs.writeFileSync(process.argv[3], E.exportJson());
console.log(JSON.stringify({ nodes: N() }));
""", out=out)

    arch = Architecture.from_json(json.loads(out.read_text(encoding="utf-8")))
    dev = arch.device
    degrees = collections.Counter(dev.degree(n) for n in dev.nodes)
    assert degrees[3] == 1, f"expected exactly one dock, got degrees {dict(degrees)}"
    assert architecture_violations(arch) == []

    by = collections.Counter(instance_of(n.labels) for n in dev.nodes.values())
    assert set(by) == {"c1", "c2"}, by
    assert by["c1"] == 12 and by["c2"] == 2


def test_parts_placed_side_by_side_are_not_an_assembly(studio, tmp_path):
    """The negative case, so the degree check above is measuring something. Two
    components placed near each other and NOT joined leave every node at degree <= 2."""
    out = tmp_path / "loose.arch.json"
    drive(studio, tmp_path, SETUP + """
E.stampComponent('ancilla_dock', 4, -1.5, 2);      // placed, never joined
fs.writeFileSync(process.argv[3], E.exportJson());
console.log(JSON.stringify({ nodes: N() }));
""", out=out)

    dev = Architecture.from_json(json.loads(out.read_text(encoding="utf-8"))).device
    degrees = collections.Counter(dev.degree(n) for n in dev.nodes)
    assert degrees[3] == 0, (
        f"nothing was joined, so nothing should be degree 3: {dict(degrees)}")
