"""TSIR modules (qccd/gadget/modules.py): a run on an architecture as text, every hardware
instruction in a gadget, read back into the very schedule the checks read."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from qccd.fullstack import layers

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "out" / "gadgets" / "cache"
SHOR = REPO / "tests" / "fixtures" / "fullstack" / "shor15"


@pytest.fixture(scope="module")
def shor15():
    from qccd.fullstack import qec2alg
    from qccd.gadget.floorplan import FLOORPLANS_DIR, load_file
    from qccd.gadget.fold import parse_folded, schedule_folded
    from qccd.gadget.library import LeafLibrary
    leaves = LeafLibrary(CACHE, log=None)
    alg = parse_folded(qec2alg.lower(layers.read(SHOR / "shor15.qec", "qec")).text, name="shor15")
    lib, _ = load_file(FLOORPLANS_DIR / "fullstack.floor", leaves)
    return schedule_folded(alg, lib, leaves), lib, leaves


def test_a_folded_run_and_its_modules_are_the_same_program(shor15, tmp_path):
    from qccd.gadget.fold import check_folded
    from qccd.gadget.modules import modules_text, parse_modules, read_modules, write_modules
    g, lib, leaves = shor15
    want = json.loads(json.dumps(g))
    w = write_modules(g, tmp_path / "hw", leaves=leaves)
    assert {"program.tsir", "leaf/msf15.tsir", "source/rm15.circ"} <= set(w["files"])
    assert len([f for f in w["files"] if f.startswith("gadget/")]) == len(g["macros"])
    m = read_modules(tmp_path / "hw")
    assert m.gir == want
    assert parse_modules(modules_text(g, leaves)).gir == want
    # the checks read what the reader reads
    assert check_folded(m.gir, lib, leaves)["failed"] == []
    # every row's gadget is a module: a leaf's TSIR, or (decoder, archive) a classical one
    for body in [m.gir] + list(m.gir["macros"].values()):
        for e in body["events"]:
            key = (m.gir["leaves"][e[3]][0], e[4])
            assert key in m.leaves, key
            assert m.leaves[key] is not None or e[3] in ("decoder", "archive"), key
        for h in body["holds"]:
            assert m.leaves[(m.gir["leaves"][h["place"]][0], "refresh")] is not None
    # a road is its conveyor's own gadget
    roads = {(f"chan{v[4]}", "carry") for v in m.gir["nets"].values()}
    assert any(k in m.leaves for k in roads)
    # the factory's leaf gadget carries its protocol, line by line
    produce = m.leaves[("msf15", "produce")]
    assert m.sources[("msf15", "produce")] == "rm15.circ"
    tagged = [i for i in produce.instructions if i.type in ("gate", "measure", "reset")]
    assert tagged and all(str(i.meta.get("src", "")).startswith("rm15.circ:") for i in tagged)
    text = (tmp_path / "hw" / "source" / "rm15.circ").read_text(encoding="utf-8")
    assert text == (REPO / "qccd" / "gadget" / "leaves" / "protocols" / "rm15.circ").read_text(encoding="utf-8")
    # nothing of the build machine is in the text
    for f in w["files"]:
        assert "Users" not in (tmp_path / "hw" / f).read_text(encoding="utf-8"), f


def test_a_row_names_its_gadget_and_its_ions_by_what_they_are(shor15, tmp_path):
    from qccd.gadget.modules import read_modules, write_modules
    g, lib, leaves = shor15
    w = write_modules(g, tmp_path / "hw", leaves=leaves)
    mid = next(iter(g["macros"]))
    text = (tmp_path / "hw" / "gadget" / f"{mid}.tsir").read_text(encoding="utf-8")
    # a gadget's parameter blocks are its caller's: their ions are symbols, `arg0.3`
    assert re.search(r"^    run \d+ \S+ \S+ \w+\.\S+ @\w+ .*:arg0\.\d", text, re.M)
    # a carry names the road's gadget, chan<L>.carry, and the road is that long
    m = read_modules(tmp_path / "hw")
    for line in text.splitlines():
        if line.strip().startswith("carry "):
            _, cid, t0, gadget, net, *_ = line.split()
            assert gadget == f"chan{m.gir['nets'][net][4]}.carry"
    # the writer and the reader agree on where every row is
    assert w["lines"] == {k: v for k, v in m.lines.items() if k in w["lines"]}
    file, n = w["lines"][(mid, "run", 0)]
    assert (tmp_path / "hw" / file).read_text(encoding="utf-8").splitlines()[n - 1].strip().startswith("run 0 ")


def test_the_reader_refuses_what_the_architecture_does_not_allow(shor15):
    from qccd.gadget.modules import ModuleError, modules_text, parse_modules
    g, lib, leaves = shor15
    text = modules_text(g)
    # a place runs only its own master's ops
    row = re.search(r"^    run \d+ \S+ \S+ (ppm_d3\.\S+) @(\w+)", text, re.M)
    bad = text.replace(row.group(0), row.group(0).replace(row.group(1), "mem_d3.store"), 1)
    with pytest.raises(ModuleError, match="is a ppm_d3; it cannot run mem_d3.store"):
        parse_modules(bad)
    # a carry's gadget is the road's own conveyor
    row = re.search(r"^    carry \d+ \S+ (chan\d+)\.carry", text, re.M)
    bad = text.replace(row.group(0), row.group(0).replace(row.group(1), "chan999"), 1)
    with pytest.raises(ModuleError, match="a carry on it is"):
        parse_modules(bad)
    # a call names a gadget some module defines
    mid = next(iter(g["macros"]))
    bad = re.sub(rf"^gadget {mid} .*$", "gadget zz_none x", text, count=1, flags=re.M)
    with pytest.raises(ModuleError, match="no module defines"):
        parse_modules(bad)
