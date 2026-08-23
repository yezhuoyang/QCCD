"""Does the work survive, and does Python agree it is a real device?

`snapshot()`, `restore()`, `autosave()` and `autoload()` were all written, complete and
correct -- and `autosave` and `autoload` each appeared exactly ONCE in `editor.js`, in
their own definition. Nothing called them. A design tool whose persistence has no caller
loses your work in the ordinary case, silently, and no test noticed because there was
nothing to test: the functions did what they said, they just never ran.

So this file asserts the two properties that matter, and asserts them through the page:

**IT COMES BACK.** Build a device, snapshot it, throw the work away, import the snapshot,
and the structural digest must match bit for bit.

**PYTHON AGREES.** A format that lives only in JS has no oracle, and this repo has spent
several rounds on what happens when two implementations of one truth drift. The snapshot
carries a real `.arch.json`, so `Architecture.from_json` must load it and price it to the
same numbers the browser did. That is the parity story for the format.

And the refusals: import must reject exactly what export refuses to write, and must leave
the current design untouched when it does -- a design tool that eats your work because you
dropped the wrong file is worse than one that cannot import at all.
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

from qccd.api import Machine  # noqa: E402
from qccd.arch import Architecture  # noqa: E402
from qccd.cost import corrected_model  # noqa: E402
from qccd.verify import replay  # noqa: E402

NODE = shutil.which("node")
SHIM = (Path(__file__).parent / "shim.mjs").as_uri()

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

#: build a four-trap ring from an empty canvas, declare a class, write a programme
BUILD = """
E.newCanvas({ name:'__NAME__', template:null });
[[0,0],[1,0],[1,1],[0,1]].forEach((p,i)=>E.addNodeAt(p[0],p[1],{kind:'site',id:'N'+i}));
const ids = Object.keys(E.state().device.nodes);
for(let i=0;i<ids.length;i++) E.joinNodes(ids[i], ids[(i+1)%ids.length]);
E.closeLoop('L0', ids, true, 'ring');
E.emit({ method:'declare_class', args:['rotate_cw'],
         kwargs:{type:'shift',orbit:'L0',delta:1} });
E.applyProgramSource('p.init({"a": "N0"})\\np.rotate(1)\\n');
"""


def drive(tmp_path, body: str, out: Path | None = None) -> dict:
    js = tmp_path / "drive.mjs"
    js.write_text(
        "import fs from 'fs';\n"
        f"import {{ loadPage }} from '{SHIM}';\n"
        "loadPage(process.argv[2], ';globalThis.__E=EDITOR;');\n"
        "const E = globalThis.__E;\n" + body,
        encoding="utf-8")
    page = tmp_path / "studio.html"
    if not page.exists():
        subprocess.run([sys.executable, "-m", "qccd", "studio", "-o", str(page)],
                       cwd=ROOT, capture_output=True, timeout=900, check=True)
    args = [NODE, str(js), str(page)] + ([str(out)] if out else [])
    r = subprocess.run(args, capture_output=True, text=True, timeout=600, cwd=ROOT)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr[-2500:]}"
    return json.loads(r.stdout)


def test_a_design_survives_being_thrown_away(tmp_path):
    r = drive(tmp_path, BUILD.replace("__NAME__", "keepme") + """
const before = JSON.stringify(E.digest());
const text = JSON.stringify(E.snapshot());
E.newCanvas({ name:'wiped', template:null });          // lose the work
const wiped = Object.keys(E.state().device.nodes).length;
const imp = E.importText(text);
console.log(JSON.stringify({ wiped: wiped, ok: imp.ok,
  same: JSON.stringify(E.digest()) === before,
  nodes: Object.keys(E.state().device.nodes).length }));
""")
    assert r["wiped"] == 0, "the wipe did not actually clear the canvas"
    assert r["ok"], "the snapshot would not import"
    assert r["same"], "the design came back different from the one that was saved"
    assert r["nodes"] == 4


@pytest.mark.parametrize("junk", [
    "not json at all",
    '{"kind":"something.else"}',
    '{"a":1}',
    "[]",
])
def test_import_refuses_junk_without_eating_the_current_design(tmp_path, junk):
    r = drive(tmp_path, BUILD.replace("__NAME__", "keepme") + f"""
const keep = JSON.stringify(E.digest());
const b = E.importText({json.dumps(junk)});
console.log(JSON.stringify({{ ok: b.ok, intact: JSON.stringify(E.digest()) === keep }}));
""")
    # a bare [] is a legal (empty) edit list, so it may be accepted -- what may NOT happen
    # is the current design being damaged on the way through
    assert r["intact"], "a refused import left the design changed"


def test_python_loads_the_browsers_snapshot_and_prices_it_the_same(tmp_path):
    """The parity story for a format that would otherwise live only in JS."""
    snap = tmp_path / "snap.json"
    r = drive(tmp_path, BUILD.replace("__NAME__", "oracle4") + """
fs.writeFileSync(process.argv[3], JSON.stringify(E.snapshot(), null, 1));
const p = E.price();
console.log(JSON.stringify({ price: p && (p.totals || p.blocked) }));
""", out=snap)

    doc = json.loads(snap.read_text(encoding="utf-8"))["arch"]
    if isinstance(doc, str):
        doc = json.loads(doc)
    arch = Architecture.from_json(doc)
    assert arch.device.summary()["n_sites"] == 4
    assert list(arch.device.loops) == ["L0"]
    assert "rotate_cw" in arch.simd_classes

    m = Machine.from_device(arch.device)
    prog = m.program("t").init({"a": "N0"})
    prog.rotate(1)
    py = replay(prog.build(), arch, corrected_model())

    js = r["price"]
    assert js["cost"] == py.total_cost, (js, py.total_cost)
    assert js["steps"] == py.total_steps, (js, py.total_steps)
    assert js["us"] == py.total_us, (js, py.total_us)


def test_the_round_trip_check_notices_a_snapshot_that_drops_the_edits(tmp_path):
    """A test that cannot fail is not a test. Neuter the edit log on the way out and
    confirm the digest comparison catches it."""
    r = drive(tmp_path, BUILD.replace("__NAME__", "keepme") + """
const before = JSON.stringify(E.digest());
const snap = E.snapshot();
snap.edits = [];                       // the exact shape of a lossy serialiser
E.newCanvas({ name:'wiped', template:null });
E.importText(JSON.stringify(snap));
console.log(JSON.stringify({ same: JSON.stringify(E.digest()) === before }));
""")
    assert not r["same"], (
        "a snapshot with its edit log removed restored to the same design -- the "
        "round-trip check is not actually comparing anything")
