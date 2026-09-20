"""The design tool's page (docs/GADGETS.md §10): what it is built from, and what it replays.

The page draws every leaf instance by replaying that leaf's TSIR in JavaScript.  That
replay is a second implementation of transport, so it is held to the first: for every op
of every leaf master, the ion positions the page ends on must be exactly the ones the
Python verifier's replay ends on (`tests/gadget_core.mjs` does the JS side).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from qccd.cost.models import corrected_model
from qccd.gadget.build import build
from qccd.gadget.logicq import parse
from qccd.gadget.programs import showcase
from qccd.ir.tsir import TSIR
from qccd.verify.replay import replay

node = shutil.which("node")
requires_node = pytest.mark.skipif(node is None, reason="node is not on PATH")
HARNESS = Path(__file__).parent / "gadget_core.mjs"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("gadget_page")
    res = build(parse(showcase(4), name="showcase4"), out_dir=out, rows=1, pairs=2,
                cache_dir=tmp_path_factory.getbasetemp() / "gadget_cache", log=None)
    return out, res


def test_the_build_writes_a_self_contained_page(built):
    out, res = built
    html = (out / "index.html").read_text(encoding="utf-8")
    assert res["checks"]["failed"] == []
    for marker in ("__DATA__", "__APP__", "__CORE__", "__TRANSIT__", "__EDITOR__"):
        assert marker not in html, f"{marker} was not substituted"
    # the occupancy law is the studio's own file, inlined here rather than copied into
    # `gadget/web/` -- if this ever stops being true the two canvases have forked
    assert "QCCDTransit" in html
    for forbidden in ("http://", "https://", "fetch(", "XMLHttpRequest"):
        assert forbidden not in html.split('<script id="gdata"')[0]
    data = html.split('<script id="gdata" type="application/json">', 1)[1].split("</script>", 1)[0]
    blob = json.loads(data)
    assert set(blob) >= {"library", "gir", "checks", "rules", "leafFiles"}
    for rel in blob["leafFiles"].values():
        assert (out / rel).exists()


@requires_node
def test_the_page_replays_every_leaf_program_exactly_as_the_verifier(built):
    out, _ = built
    run = subprocess.run([node, str(HARNESS), str(out)], capture_output=True, text=True,
                         encoding="utf-8", timeout=300)
    assert run.returncode == 0, run.stderr
    got = json.loads(run.stdout.strip().splitlines()[-1])
    model = corrected_model("qccdsim_jones")
    from qccd.gadget.library import LeafLibrary
    checked = 0
    for leaf_file in (out / "leaf").iterdir():
        text = leaf_file.read_text(encoding="utf-8")
        data = json.loads(text[text.index("(") + 1:text.rindex(")")])
        arch = _arch_of(data, out)
        for op, prog_json in data["programs"].items():
            prog = TSIR.from_json(prog_json)
            want = replay(prog, arch, model, check_rules=False, keep_cycles=False).final_positions
            assert got["final"][data["master"]][op] == want, f"{data['master']}.{op}"
            checked += 1
    assert checked >= 10


@requires_node
def test_the_page_model_agrees_with_the_schedule(built):
    out, _ = built
    run = subprocess.run([node, str(HARNESS), str(out)], capture_output=True, text=True,
                         encoding="utf-8", timeout=300)
    got = json.loads(run.stdout.strip().splitlines()[-1])
    assert got["samples"], "no events sampled"
    for s in got["samples"]:
        assert s["got"] == s["id"], s                 # the running event is found by time
        assert 0 <= s["local"] <= s["duration"] + 1e-6
    gir = json.loads((out / "gir.json").read_text(encoding="utf-8"))
    longest = max(n[4] for n in gir["nets"].values())
    assert got["maxInChannel"] <= max(longest, 1)
    assert got["inventoryAtEnd"] == got["homeTotal"] == gir["n_ions"]


def _arch_of(data: dict, out: Path):
    """The testbench device of a leaf, rebuilt from the characterized library's builders."""
    from qccd.gadget.codes import TUTORIAL_BB, bivariate_bicycle, parse_poly
    from qccd.gadget.leaves import memory, transport
    name = data["master"]
    if name.startswith("mem_"):
        l, m, A, B, _ = TUTORIAL_BB["bb72"]
        code = bivariate_bicycle(None, l, m, parse_poly(A, l, m), parse_poly(B, l, m))
        return _cached_machine("mem", lambda: memory.memory(code, visits=[])).arch
    if name.startswith("tcx"):
        return _cached_machine(name, lambda: transport.station(72, code="bb72")).arch
    if name.startswith("res"):
        return _cached_machine(name, lambda: transport.reservoir(4)).arch
    if name.startswith("fac"):
        from qccd.gadget.leaves import factory
        return _cached_machine(name, lambda: factory.factory_t15()).arch
    return _cached_machine(name, lambda: transport.junction()).arch


_MACHINES: dict = {}


def _cached_machine(key, fn):
    if key not in _MACHINES:
        _MACHINES[key] = fn().machine
    return _MACHINES[key]
