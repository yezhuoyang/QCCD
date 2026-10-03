"""The compiler on designs that are on no leaderboard: every program it emits passes every rule.

The boards' devices are generated (rings, grids, ladders), and every board entry passes the
rules, so R22 (one waveform per cycle) looked solved.  A design a person draws is not
generated.  Two shapes the Studio's sketch produces made the published compiler emit cycles
in which ions moved in different directions:

  * an OPEN path with a corner -- the router labelled a step by the path alone (`P0:+1`), so
    a hop before the corner (+x) and one after it (+y) shared a cycle;
  * a dock drawn as a line -- each spur is then its own open path, labelled by its lab-frame
    axis, and the rotate pipeline docked the riders above the rail and below it together.

Each case here failed R22 with compiler f52d3ba63b9a.  The program is compiled exactly as the
workspace does it (export, qccdc_cli rotate on a closed loop else compile, cooling) and
replayed with every rule on.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from qccd.api import Machine
from qccd.arch.device import Architecture
from qccd.compile.programs import closed_loops
from qccd.cost.models import corrected_model
from qccd.ir.tsir import TSIR
from qccd.verify.replay import replay
from qccd.workspace.evaluator import Toolchain

REPO = Path(__file__).resolve().parents[1]
BRIDGE = REPO / "Compiler" / "bridge"
QCCDC = Toolchain.discover().qccdc
pytestmark = pytest.mark.skipif(QCCDC is None, reason="qccdc_cli is not built")


def _fresh(doc):
    """Drop the fields the loader derives from geometry, after the geometry was edited."""
    for n in doc["geometry"]["nodes"]:
        n.pop("corner", None)
        n.pop("degree", None)
    for s in doc["geometry"]["segments"]:
        s.pop("corner_endpoints", None)
    return doc


def bent_chain(n, turns):
    """A chain of `n` traps (one open path) that turns: `turns` maps the index after which it
    turns to the new unit step."""
    doc = Machine.chain(n, name="bent").arch.to_json()
    x, y, step = 0.0, 0.0, (1.0, 0.0)
    for i, node in enumerate(doc["geometry"]["nodes"]):
        if i:
            step = turns.get(i - 1, step)
            x, y = x + step[0], y + step[1]
        node["pos"] = [x, y]
    return _fresh(doc)


def line_docks(width, verticals):
    """A docked ring whose spurs are each declared as an open path, as a line sketch does."""
    doc = Machine.ring(width, 2, verticals, dock_offset=1, name="docks").arch.to_json()
    g = doc["geometry"]
    for k, seg in enumerate(s for s in g["segments"] if s.get("loop") is None):
        seg["loop"] = f"P{k}"
        g["loops"].append({"id": f"P{k}", "kind": "path", "nodes": list(seg["ends"]), "closed": False})
    return doc


CASES = {
    "L-shaped path, adder": (lambda: bent_chain(20, {9: (0.0, 1.0)}), "adder3"),
    "U-shaped path, QFT": (lambda: bent_chain(16, {5: (0.0, 1.0), 10: (-1.0, 0.0)}), "qft8"),
    "zig-zag path, adder": (lambda: bent_chain(14, {3: (0.0, 1.0), 6: (1.0, 0.0), 9: (0.0, -1.0)}), "adder3"),
    "ring with line docks, Steane": (lambda: line_docks(12, 6), "steane_esm"),
    "ring with line docks, repetition": (lambda: line_docks(16, 8), "rep9_esm"),
    "periodic grid, QFT": (lambda: Machine.grid(4, 4, periodic=True, name="torus").arch.to_json(), "qft8"),
}


@pytest.mark.parametrize("case", list(CASES))
def test_every_program_the_compiler_emits_passes_every_rule(case, tmp_path):
    make, circuit = CASES[case]
    doc = make()
    (tmp_path / "device.arch.json").write_text(json.dumps(doc), encoding="utf-8")
    subprocess.run([sys.executable, str(BRIDGE / "export_arch.py"), str(tmp_path / "device.arch.json"), "-o",
                    str(tmp_path / "device.expanded.json")], check=True, capture_output=True)
    arch = Architecture.from_json(doc)
    mode = "rotate" if closed_loops(arch) else "compile"
    cp = subprocess.run([str(QCCDC), mode, str(REPO / "Compiler" / "examples" / f"{circuit}.qasm"), "--arch",
                         str(tmp_path / "device.expanded.json"), "-o", str(tmp_path / "prog")],
                        capture_output=True, text=True, timeout=600)
    assert cp.returncode == 0, cp.stdout[-800:] + cp.stderr[-800:]
    assert not json.loads((tmp_path / "prog.qcert.json").read_text(encoding="utf-8")).get("unrealised")
    subprocess.run([sys.executable, str(BRIDGE / "insert_cooling.py"), str(tmp_path / "prog.tsir.json"), "--arch",
                    str(tmp_path / "device.arch.json"), "-o", str(tmp_path / "prog.cooled.tsir.json")],
                   check=True, capture_output=True)
    prog = TSIR.from_json(json.loads((tmp_path / "prog.cooled.tsir.json").read_text(encoding="utf-8")))
    res = replay(prog, arch, corrected_model("qccdsim_jones"), check_rules=True)
    bad = res.rules.violations
    assert not bad, f"{case}: {dict(Counter(v.rule for v in bad))}; first: {bad[0]}"
