"""The compiled programme depends on the device, not on the order its file is written in.

Issue #20.  The compiler used to break its ties -- which trap of equal score to place a qubit
in, where two ions meet, which of two equally short routes a hop takes -- by the order the
`.arch.json` lists its nodes and segments.  So one device, written in another order, compiled
to another programme with another time, and the hillclimb agents of PRs #10-#14 searched over
file order to win on bb144 (134.6 -> 110.6 ms for one round by listing the data traps first).
`qccdc` now puts the device in a canonical order as it reads it (`Arch.of_json`).

Each case compiles a device and shuffled copies of it -- nodes shuffled, segments shuffled,
each segment's two ends swapped at random, the named loops and the SIMD classes listed the
other way round -- and asks for byte-identical programmes and certificates.  The copies are
shuffled at the compiler's input, the expanded document `export_arch.py` writes; the hillclimb
design is also shuffled one step earlier, as a contributor does, in the design file itself.
Every case here fails with compiler f88754a2fa71: a different programme for a shuffled copy,
or, for the ring beside a bare loop, no rotation at all once the bare loop is listed first.
"""

from __future__ import annotations

import copy
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

from qccd.api import Machine
from qccd.workspace.evaluator import Toolchain

REPO = Path(__file__).resolve().parents[1]
BRIDGE = REPO / "Compiler" / "bridge"
EXAMPLES = REPO / "Compiler" / "examples"
SUITE = REPO / "qccd" / "bench" / "suites" / "suite@1" / "devices"
HILLCLIMB = Path(__file__).resolve().parent / "fixtures" / "hillclimb_bb144_01-a03-c2b22c83.arch.json"
QCCDC = Toolchain.discover().qccdc
pytestmark = pytest.mark.skipif(QCCDC is None, reason="qccdc_cli is not built")

SEEDS = (1, 2)


def _shuffled(doc: dict, seed: int) -> dict:
    """The same device, written in another order."""
    d = copy.deepcopy(doc)
    rng = random.Random(seed)
    g = d["geometry"]
    rng.shuffle(g["nodes"])
    rng.shuffle(g["segments"])
    for s in g["segments"]:
        if rng.random() < 0.5:
            s["ends"] = list(reversed(s["ends"]))
    g["loops"] = list(reversed(g.get("loops") or []))  # each loop's own node order is kept
    if isinstance(d.get("simd_classes"), dict):
        d["simd_classes"] = dict(reversed(list(d["simd_classes"].items())))
    return d


def _export(design: dict, where: Path) -> dict:
    where.mkdir(parents=True, exist_ok=True)
    (where / "device.arch.json").write_text(json.dumps(design), encoding="utf-8")
    subprocess.run([sys.executable, str(BRIDGE / "export_arch.py"), str(where / "device.arch.json"), "-o",
                    str(where / "device.expanded.json")], check=True, capture_output=True)
    return json.loads((where / "device.expanded.json").read_text(encoding="utf-8"))


def _compile(expanded: dict, qasm: Path, mode: str, where: Path) -> tuple[bytes, bytes]:
    """The programme and the certificate, as bytes.  Run inside `where`, so the certificate's
    architecture fingerprint (of `arch/<name>.arch.json`, which is not there) is the same for
    every copy."""
    where.mkdir(parents=True, exist_ok=True)
    (where / "device.expanded.json").write_text(json.dumps(expanded), encoding="utf-8")
    cp = subprocess.run([str(QCCDC), mode, str(qasm), "--arch", str(where / "device.expanded.json"), "-o",
                         str(where / "prog")], capture_output=True, text=True, timeout=600, cwd=where)
    assert cp.returncode == 0, cp.stdout[-800:] + cp.stderr[-800:]
    assert not json.loads((where / "prog.qcert.json").read_text(encoding="utf-8")).get("unrealised")
    return (where / "prog.tsir.json").read_bytes(), (where / "prog.qcert.json").read_bytes()


def ring_and_bare_loop() -> dict:
    """The docked ring, plus a second closed loop `A` with no docks.  Rotation drives the
    docked one however the two are listed: it used to take the first closed loop in the file
    and give up when that one had no docks."""
    doc = Machine.ring(12, 2, 6, dock_offset=1, name="two_loops").arch.to_json()
    g = doc["geometry"]
    g["generator"] = "explicit"
    g.pop("params", None)
    for n in g["nodes"]:
        n.pop("corner", None)
        n.pop("degree", None)
    for s in g["segments"]:
        s.pop("corner_endpoints", None)
    ring = [f"X{i}" for i in range(4)]
    for i, (x, y) in enumerate([(0.0, 10.0), (1.0, 10.0), (1.0, 11.0), (0.0, 11.0)]):
        g["nodes"].append({"id": ring[i], "pos": [x, y], "kind": "site", "capacity": 2, "zone_type": "data",
                           "labels": []})
        g["segments"].append({"id": f"F{i}", "ends": [ring[i], ring[(i + 1) % 4]], "length": 1.0, "capacity": 1,
                              "loop": "A", "labels": ["rail"]})
    g["loops"].append({"id": "A", "kind": "ring", "nodes": ring, "closed": True})
    return doc


CASES = {
    # rotation: the docks and the rail the rotation holds used to come out in segment order
    "ring with docks, rotate": (lambda: Machine.ring(12, 2, 6, dock_offset=1, name="docks").arch.to_json(),
                                "steane_esm", "rotate"),
    # ... and the loop it rotates was the first closed one in the file
    "ring and a bare loop, rotate": (ring_and_bare_loop, "steane_esm", "rotate"),
    # the router: placement and meeting points break ties in trap order
    "grid, compile": (lambda: Machine.grid(4, 4, name="grid").arch.to_json(), "qft8", "compile"),
    # two closed loops, listed D then A
    "two loops, compile": (lambda: json.loads((SUITE / "cyclone_dual_loop.arch.json").read_text(encoding="utf-8")),
                           "ghz16", "compile"),
    # a design from the hillclimb, on a smaller circuit than its board's to keep this quick:
    # the order it depended on is the device's, and a Steane round shows it as well
    "bb144 hillclimb design, compile": (lambda: json.loads(HILLCLIMB.read_text(encoding="utf-8")),
                                        "steane_esm", "compile"),
}


@pytest.mark.parametrize("case", list(CASES))
def test_shuffled_device_compiles_to_the_same_programme(case, tmp_path):
    make, circuit, mode = CASES[case]
    expanded = _export(make(), tmp_path / "export")
    qasm = EXAMPLES / f"{circuit}.qasm"
    want = _compile(expanded, qasm, mode, tmp_path / "file")
    for seed in SEEDS:
        got = _compile(_shuffled(expanded, seed), qasm, mode, tmp_path / f"shuffled{seed}")
        assert got[0] == want[0], f"{case}: shuffle {seed} changed the programme"
        assert got[1] == want[1], f"{case}: shuffle {seed} changed the certificate"


SURFACE17 = ("06-a09-b7294b0b", "07-a05-314e4fe0", "08-a08-514dae97")


@pytest.mark.parametrize("key", SURFACE17)
def test_surface17_hillclimb_design_compiles_whatever_its_order(key, tmp_path):
    """Three surface17 hillclimb designs -- five traps in a ring, seventeen ions -- compiled
    with the published compiler only in the file order a search had found for them (it
    compiled 6, 11 and 15 of 30 random orders), and the canonical order was refused as
    unroutable at any horizon.  The compiler now tries the trap order rotated before it
    refuses (`Compile.run`): each compiles, passes every rule, and a shuffled copy gives the
    same programme."""
    from qccd.arch.device import Architecture
    from qccd.cost.models import corrected_model
    from qccd.ir.tsir import TSIR
    from qccd.verify.replay import replay

    design = json.loads((HILLCLIMB.parent / f"hillclimb_surface17_{key}.arch.json").read_text(encoding="utf-8"))
    qasm = REPO / "qccd" / "workspace" / "releases" / "surface17@1" / "circuit.qasm"
    expanded = _export(design, tmp_path / "export")
    want = _compile(expanded, qasm, "compile", tmp_path / "file")
    subprocess.run([sys.executable, str(BRIDGE / "insert_cooling.py"), str(tmp_path / "file" / "prog.tsir.json"),
                    "--arch", str(tmp_path / "export" / "device.arch.json"), "-o",
                    str(tmp_path / "file" / "prog.cooled.tsir.json")], check=True, capture_output=True)
    cooled = json.loads((tmp_path / "file" / "prog.cooled.tsir.json").read_text(encoding="utf-8"))
    res = replay(TSIR.from_json(cooled), Architecture.from_json(design), corrected_model("qccdsim_jones"),
                 check_rules=True)
    assert not res.rules.violations, f"{key}: {res.rules.violations[:3]}"
    got = _compile(_shuffled(expanded, 1), qasm, "compile", tmp_path / "shuffled")
    assert got == want, f"{key}: a shuffled copy compiled to another programme"


def test_design_file_order_does_not_change_the_programme(tmp_path):
    """The hillclimb's own move: the same explicit design, its lists reordered in the DESIGN
    file, through the exporter and the compiler."""
    design = json.loads(HILLCLIMB.read_text(encoding="utf-8"))
    qasm = EXAMPLES / "steane_esm.qasm"
    want = _compile(_export(design, tmp_path / "e0"), qasm, "compile", tmp_path / "c0")
    reordered = _shuffled(design, 3)
    assert [n["id"] for n in reordered["geometry"]["nodes"]] != [n["id"] for n in design["geometry"]["nodes"]]
    got = _compile(_export(reordered, tmp_path / "e1"), qasm, "compile", tmp_path / "c1")
    assert got == want
