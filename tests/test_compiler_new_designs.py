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
import math
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from qccd.api import Machine
from qccd.arch.builder import DeviceBuilder
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


def _compile(doc, circuit, tmp_path, mode=None):
    """Compile `circuit` on `doc` exactly as the workspace does; the cooled program (as JSON),
    the architecture, and what the replay found."""
    (tmp_path / "device.arch.json").write_text(json.dumps(doc), encoding="utf-8")
    subprocess.run([sys.executable, str(BRIDGE / "export_arch.py"), str(tmp_path / "device.arch.json"), "-o",
                    str(tmp_path / "device.expanded.json")], check=True, capture_output=True)
    arch = Architecture.from_json(doc)
    mode = mode or ("rotate" if closed_loops(arch) else "compile")
    cp = subprocess.run([str(QCCDC), mode, str(REPO / "Compiler" / "examples" / f"{circuit}.qasm"), "--arch",
                         str(tmp_path / "device.expanded.json"), "-o", str(tmp_path / "prog")],
                        capture_output=True, text=True, timeout=600)
    assert cp.returncode == 0, cp.stdout[-800:] + cp.stderr[-800:]
    assert not json.loads((tmp_path / "prog.qcert.json").read_text(encoding="utf-8")).get("unrealised")
    subprocess.run([sys.executable, str(BRIDGE / "insert_cooling.py"), str(tmp_path / "prog.tsir.json"), "--arch",
                    str(tmp_path / "device.arch.json"), "-o", str(tmp_path / "prog.cooled.tsir.json")],
                   check=True, capture_output=True)
    raw = json.loads((tmp_path / "prog.cooled.tsir.json").read_text(encoding="utf-8"))
    res = replay(TSIR.from_json(raw), arch, corrected_model("qccdsim_jones"), check_rules=True)
    return raw, arch, res.rules.violations


@pytest.mark.parametrize("case", list(CASES))
def test_every_program_the_compiler_emits_passes_every_rule(case, tmp_path):
    make, circuit = CASES[case]
    _, _, bad = _compile(make(), circuit, tmp_path)
    assert not bad, f"{case}: {dict(Counter(v.rule for v in bad))}; first: {bad[0]}"


# ------------------------------------------------------------------ junctions are crossed
#
# Reported 2026-09-29 on a hexagon lattice a reader's agent designed: ions sat on the
# corners, where three rails meet, with empty traps on every side.  The corners were
# declared as SITES, and a site where three rails meet is a junction (R18) that R2 lets one
# ion stand on, so the router used it as a trap: a Steane round stopped there 77 times, once
# for 17 instructions.  With room for every ion elsewhere, the compiler now crosses such a
# site in one move, as it always crossed a bare junction, and never rests on it.  R23 is the
# rule; `_compile` replays every rule, so the programs below are judged against it.

def hexagon_lattice(corner_sites):
    """Seven flat-top hexagons: a corner where rails meet, two traps on every side, a
    two-trap spur off every outer corner only two sides reach.  `corner_sites` declares
    the corners as sites (what the reported design did) instead of bare junctions."""
    a, b = 3.0, DeviceBuilder("explicit")
    centres = [(0.0, 0.0)] + [(math.sqrt(3) * a * math.cos(math.radians(30 + 60 * k)),
                               math.sqrt(3) * a * math.sin(math.radians(30 + 60 * k))) for k in range(6)]
    vid, sides, n_seg = {}, {}, 0
    for cx, cy in centres:
        ring = []
        for k in range(6):
            p = (round(cx + a * math.cos(math.radians(60 * k)), 6), round(cy + a * math.sin(math.radians(60 * k)), 6))
            if p not in vid:
                vid[p] = f"J{len(vid)}"
                b.site(vid[p], *p, zone="data") if corner_sites else b.junction(vid[p], *p)
            ring.append(p)
        for k in range(6):
            (u, pu), (w, pw) = sorted((vid[q], q) for q in (ring[k], ring[(k + 1) % 6]))
            if (u, w) in sides:
                continue
            sides[(u, w)] = chain = [u]
            for t in (1 / 3, 2 / 3):
                chain.append(f"S{2 * len(sides) + len(chain) - 3}")
                b.site(chain[-1], pu[0] + t * (pw[0] - pu[0]), pu[1] + t * (pw[1] - pu[1]),
                       zone="trap" if abs(pu[1] - pw[1]) < 1e-6 else "data")
            chain.append(w)
            for x, y in zip(chain, chain[1:]):
                b.segment(f"E{n_seg}", x, y)
                n_seg += 1
    pos = {v: p for p, v in vid.items()}
    for v, d in sorted(Counter(v for side in sides for v in side).items()):
        if d == 2:
            x, y = pos[v]
            r, prev = math.hypot(x, y), v
            for i in (1, 2):
                b.site(f"D{v[1:]}_{i}", x + i * x / r, y + i * y / r, zone="trap")
                b.segment(f"E{n_seg}", prev, f"D{v[1:]}_{i}")
                n_seg, prev = n_seg + 1, f"D{v[1:]}_{i}"
    return Machine.from_device(b.build(), name="hexagon").arch.to_json()


def junction_rests(raw, arch):
    """How many times an instruction ends with an ion on a node where three or more rails meet."""
    dev = arch.device
    junctions = {i for i in dev.nodes if dev.degree(i) >= 3}
    pos, rests = {}, 0
    for ins in raw["instructions"]:
        if ins["type"] == "init":
            pos = dict(ins["placement"])
        elif ins["type"] == "simd":
            pos.update((p["ion"], p["to"]) for p in ins["participants"])
        rests += sum(1 for s in pos.values() if s in junctions)
    return rests


@pytest.mark.parametrize("corner_sites", [True, False], ids=["corners are sites", "corners are junctions"])
@pytest.mark.parametrize("circuit", ["steane_esm", "qft8"])
def test_no_ion_rests_on_a_junction_while_the_device_has_room_elsewhere(corner_sites, circuit, tmp_path):
    raw, arch, bad = _compile(hexagon_lattice(corner_sites), circuit, tmp_path, mode="compile")
    assert not bad, f"{dict(Counter(v.rule for v in bad))}; first: {bad[0]}"
    assert sum(1 for i in arch.device.nodes if arch.device.degree(i) >= 3) == 24   # the check has something to see
    assert junction_rests(raw, arch) == 0


def test_the_proved_checker_admits_a_move_that_crosses_a_junction_site(tmp_path):
    """The Lean checker judges every move against hops read off the device by code the
    compiler never runs (`mk_qcheck_input.py`).  That reader has to know that a site where
    three rails meet may be crossed, or it rejects exactly the programs this fix produces."""
    qcheck = REPO / "Compiler" / "lean" / ".lake" / "build" / "bin" / ("qcheck.exe" if sys.platform == "win32" else "qcheck")
    if not qcheck.exists():
        pytest.skip("the Lean checker is not built")
    raw, arch, _ = _compile(hexagon_lattice(True), "steane_esm", tmp_path, mode="compile")
    crossing = [p for ins in raw["instructions"] if ins["type"] == "simd" for p in ins["participants"] if len(p["via"]) > 1]
    assert crossing, "no move crosses a junction site: the check has nothing to see"
    subprocess.run([sys.executable, str(BRIDGE / "mk_qcheck_input.py"), str(tmp_path / "prog"), "--arch",
                    str(tmp_path / "device.expanded.json"), "-o", str(tmp_path / "qin.json")], check=True, capture_output=True)
    cp = subprocess.run([str(qcheck), str(tmp_path / "qin.json")], capture_output=True, text=True, timeout=900)
    assert "ACCEPTED" in cp.stdout, (cp.stdout + cp.stderr)[-600:]


# ------------------------------------------------------------------ Quantinuum H2
#
# The rebuilt H2 (Reproduce/moses2023/h2.arch.json: one closed loop, four gate zones, and
# two conveyors whose forty wells three tied signals drive with no per-site switch) made
# compiler 35bf68427d9c refuse or break all 248 of the paper's circuits.  Each cause is
# pinned by a minimal example in `run_h2.MINIMAL`, and each must now compile with nothing
# unrealised, pass every rule, and be ACCEPTED by the proved checker:
#
#   M1  a barrier on three qubits crashed the compiler before placement;
#   M2  five independent gates on four gate zones left the fifth unrealised;
#   M3  a gate with an idle ion in every gate zone was left unrealised;
#   M4  a conveyor ion was moved out alone while another on the same signals stayed (R4).

sys.path.insert(0, str(REPO / "Reproduce" / "moses2023"))
from run_h2 import ARCH as H2_ARCH, MINIMAL as H2_MINIMAL  # noqa: E402


def _h2_minimal(case, tmp_path):
    from qccd.qec import load_arch, load_program
    from qccd.verify import verify

    exp = tmp_path / "h2.expanded.json"
    subprocess.run([sys.executable, str(BRIDGE / "export_arch.py"), str(H2_ARCH), "-o", str(exp)],
                   check=True, capture_output=True)
    (tmp_path / "c.qasm").write_text(case["qasm"], encoding="utf-8")
    cmd = [str(QCCDC), "compile", str(tmp_path / "c.qasm"), "--arch", str(exp), "-o", str(tmp_path / "prog"),
           "--no-rotate"]
    if case["init"]:
        (tmp_path / "init.json").write_text(json.dumps(case["init"]), encoding="utf-8")
        cmd += ["--init-placement", str(tmp_path / "init.json")]
    cp = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    assert cp.returncode == 0, cp.stdout[-800:] + cp.stderr[-800:]
    cert = json.loads((tmp_path / "prog.qcert.json").read_text(encoding="utf-8"))
    assert not cert.get("unrealised"), cp.stdout[-800:]
    subprocess.run([sys.executable, str(BRIDGE / "insert_cooling.py"), str(tmp_path / "prog.tsir.json"), "--arch",
                    str(H2_ARCH), "-o", str(tmp_path / "prog.cooled.tsir.json")], check=True, capture_output=True)
    arch = load_arch(H2_ARCH)
    reports = {}
    for which in ("prog.tsir.json", "prog.cooled.tsir.json"):
        rep = verify(load_program(tmp_path / which), arch, corrected_model(table="local"))
        bad = rep.rules.violations
        assert not bad, f"{which}: {dict(Counter(v.rule for v in bad))}; first: {bad[0]}"
        reports[which] = rep.rules.summary()
    qcheck = REPO / "Compiler" / "lean" / ".lake" / "build" / "bin" / ("qcheck.exe" if sys.platform == "win32" else "qcheck")
    if qcheck.exists():
        subprocess.run([sys.executable, str(BRIDGE / "mk_qcheck_input.py"), str(tmp_path / "prog"), "--arch", str(exp),
                        "-o", str(tmp_path / "qin.json")], check=True, capture_output=True)
        qc = subprocess.run([str(qcheck), str(tmp_path / "qin.json")], capture_output=True, text=True, timeout=900)
        assert "ACCEPTED" in qc.stdout, (qc.stdout + qc.stderr)[-600:]
    raw = json.loads((tmp_path / "prog.tsir.json").read_text(encoding="utf-8"))
    return raw, reports["prog.tsir.json"]


@pytest.mark.parametrize("case", H2_MINIMAL, ids=[c["id"] for c in H2_MINIMAL])
def test_h2_minimal_examples_compile_pass_every_rule_and_are_certified(case, tmp_path):
    raw, summary = _h2_minimal(case, tmp_path)
    ins = raw["instructions"]
    ms = [i for i in ins if i["type"] == "gate" and i.get("pairs")]
    if case["id"].startswith("M1"):
        # the barrier is a fence: the program still prepares, gates and reads out
        assert any(i["type"] == "measure" for i in ins)
    elif case["id"].startswith("M2"):
        # four gates in one round, the fifth in a second
        assert sorted(len(i["pairs"]) for i in ms) == [1, 4]
    elif case["id"].startswith("M3"):
        # an idle ion left a gate zone so that the gate could take it
        moved = {p["ion"] for i in ins if i["type"] == "simd" for p in i["participants"]}
        assert moved & {"q2", "q3", "q4", "q5"} and len(ms) == 1
    elif case["id"].startswith("M4"):
        # R4 judged the conveyor and passed: both stored ions left it, together
        assert "R4" in summary["passed"]
        wells = {f"C{s}{k:02d}" for s in "LR" for k in range(1, 21)}
        first = next(i for i in ins if i["type"] == "simd")
        assert {p["ion"] for p in first["participants"] if p["from"] in wells} == {"q0", "q2"}
