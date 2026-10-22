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

Two more shapes failed it once R22's hop label stopped being coarser than the motion (2026-10,
issues #18 and #19), with the compiler of a9ead69, which labelled hops as coarsely as the
verifier then did:

  * rails at 30 and 60 degrees -- both read `+x+y` under the sign-only label, and the router
    played hops on both in one cycle;
  * a torus declared as one loop through its junctions -- a trap between two loop junctions
    read as a spur, so two ions leaving two such traps in opposite directions shared a cycle.
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


def slanted_chain(n):
    """A chain (one open path) whose rails alternate 60 and 30 degrees: every hop reads
    `+x+y` under the sign-only label R22 had until issue #19, and the router put hops on
    both kinds of rail into one cycle."""
    s30 = (math.cos(math.radians(30)), math.sin(math.radians(30)))
    s60 = (math.cos(math.radians(60)), math.sin(math.radians(60)))
    return bent_chain(n, {i: (s30 if i % 2 else s60) for i in range(n)})


#: Two edge-disjoint Hamiltonian cycles of the 3 x 3 torus `Machine.grid(3, 3, periodic=True)`,
#: junction, trap, junction, ...: between them they hold all 18 rails.
TORUS_CYCLES = {
    "L0": ["J0_0", "T0_0h", "J1_0", "T1_0h", "J2_0", "T2_0v", "J2_1", "T2_1h", "J0_1",
           "T0_1h", "J1_1", "T1_1v", "J1_2", "T1_2h", "J2_2", "T2_2h", "J0_2", "T0_2v"],
    "L1": ["J0_0", "T0_0v", "J0_1", "T0_1v", "J0_2", "T0_2h", "J1_2", "T1_2v", "J1_0",
           "T1_0v", "J1_1", "T1_1h", "J2_1", "T2_1v", "J2_2", "T2_2v", "J2_0", "T2_0h"],
}


def torus_loops(loops):
    """The 3 x 3 torus with `loops` of its two Hamiltonian cycles declared as closed loops.
    With one, the other cycle's nine traps are chords between two loop junctions: the bb144
    design of issue #18, which R22 read as spurs, so opposite moves shared a waveform."""
    doc = Machine.grid(3, 3, periodic=True, name="torus").arch.to_json(expanded=True)
    g = doc["geometry"]
    g["generator"], g["params"] = "explicit", {}
    on = {}
    for lid in list(TORUS_CYCLES)[:loops]:
        seq = TORUS_CYCLES[lid]
        on.update((frozenset(e), lid) for e in zip(seq, seq[1:] + seq[:1]))
        g.setdefault("loops", []).append({"id": lid, "kind": "ring", "nodes": seq, "closed": True})
    for s in g["segments"]:
        s["loop"] = on.get(frozenset(s["ends"]))
    return _fresh(doc)


#: The torus cases name the general router: the bb144 design compiled with it, because
#: rotation declines there (24 qubits need a dock, the torus has 18).
CASES = {
    "chain at 30 and 60 degrees, adder": (lambda: slanted_chain(14), "adder3"),
    "chain at 30 and 60 degrees, QFT": (lambda: slanted_chain(20), "qft8"),
    "torus, one loop through its junctions, QFT": (lambda: torus_loops(1), "qft8", "compile"),
    "torus, one loop through its junctions, Steane": (lambda: torus_loops(1), "steane_esm", "compile"),
    "L-shaped path, adder": (lambda: bent_chain(20, {9: (0.0, 1.0)}), "adder3"),
    "U-shaped path, QFT": (lambda: bent_chain(16, {5: (0.0, 1.0), 10: (-1.0, 0.0)}), "qft8"),
    "zig-zag path, adder": (lambda: bent_chain(14, {3: (0.0, 1.0), 6: (1.0, 0.0), 9: (0.0, -1.0)}), "adder3"),
    "ring with line docks, Steane": (lambda: line_docks(12, 6), "steane_esm"),
    "ring with line docks, repetition": (lambda: line_docks(16, 8), "rep9_esm"),
    "periodic grid, QFT": (lambda: Machine.grid(4, 4, periodic=True, name="torus").arch.to_json(), "qft8"),
}


def _compile(doc, circuit, tmp_path, mode=None, qasm=None):
    """Compile `circuit` (or the file `qasm`) on `doc` exactly as the workspace does; the cooled
    program (as JSON), the architecture, and what the replay found."""
    (tmp_path / "device.arch.json").write_text(json.dumps(doc), encoding="utf-8")
    subprocess.run([sys.executable, str(BRIDGE / "export_arch.py"), str(tmp_path / "device.arch.json"), "-o",
                    str(tmp_path / "device.expanded.json")], check=True, capture_output=True)
    arch = Architecture.from_json(doc)
    mode = mode or ("rotate" if closed_loops(arch) else "compile")
    qasm = qasm or REPO / "Compiler" / "examples" / f"{circuit}.qasm"
    cp = subprocess.run([str(QCCDC), mode, str(qasm), "--arch",
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
    make, circuit, *mode = CASES[case]
    _, _, bad = _compile(make(), circuit, tmp_path, *mode)
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
#   M4  a conveyor ion was moved out alone while another on the same signals stayed (R4);
#   M5  (fails on 02bceb9117e7) a gate between two qubits that both ride the loop, which has
#       no docks: the router jammed and rotation declined.  Compiled without --no-rotate,
#       because its subject is the race-track fallback (`Racetrack`): rigid shifts of the
#       whole loop, swaps in the capacity-2 zones, the pair merged into one gate zone.

sys.path.insert(0, str(REPO / "Reproduce" / "moses2023"))
from run_h2 import ARCH as H2_ARCH, MINIMAL as H2_MINIMAL  # noqa: E402


def _h2_minimal(case, tmp_path):
    from qccd.qec import load_arch, load_program
    from qccd.verify import verify

    exp = tmp_path / "h2.expanded.json"
    subprocess.run([sys.executable, str(BRIDGE / "export_arch.py"), str(H2_ARCH), "-o", str(exp)],
                   check=True, capture_output=True)
    (tmp_path / "c.qasm").write_text(case["qasm"], encoding="utf-8")
    cmd = [str(QCCDC), "compile", str(tmp_path / "c.qasm"), "--arch", str(exp), "-o", str(tmp_path / "prog")]
    if not case.get("rotate"):
        cmd += ["--no-rotate"]
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
    elif case["id"].startswith("M5"):
        # the whole loop shifted rigidly (the only way a conveyor ion moves), every other
        # move one ion one site, and the gate ran in one round in a DG zone
        shifts = [i for i in ins if i["type"] == "simd" and (i.get("template") or {}).get("kind") == "loop_shift"]
        assert shifts and all(i["class"].startswith("batch_shift") for i in shifts)
        steps = [i for i in ins if i["type"] == "simd" and not i.get("template")]
        assert steps and all(len(i["participants"]) <= 4 for i in steps)
        assert len(ms) == 1 and ms[0]["sites"][0].startswith("DG")
        assert {"R1", "R3", "R4", "R11"} <= set(summary["passed"])


# ------------------------------------------------------------------ a barrier is a fence
#
# Reported 2026-10-17 on BB[[144,12,12]] with a `barrier a,b` after each of its 864 CNOTs:
# compiler c7c535380bfb's conveyor (`qccdc rotate`) read a two-qubit barrier by its SHAPE and
# compiled it as a CX -- 1728 contacts for 864 CNOTs, a certificate its own self-check passed,
# and a programme whose tableau is not the circuit's (R10).  The same reading made a barrier
# between two riders a reason to refuse (the interaction graph "was not bipartite"), compiled a
# CZ contact as a CX (the contact never asked which gate it was), and made the general router's
# placer pull two ions together because a barrier named them.  A barrier of any width -- one
# qubit, two, many -- orders the ops on its wires and does nothing else, in every pass.
#
# Every case below fails on c7c535380bfb (`QCCD_QCCDC=<exe>` reruns them on another compiler)
# except the general router's and the race track's compiles, which always read the name before
# the arity and are pinned here so that they keep doing so.

QASM_HEAD = 'OPENQASM 2.0;\ninclude "qelib1.inc";\n'

# an ESM-shaped round: ancillas q4 and q5 dock, q0..q3 ride; fences of width 1, 2 and 3
FENCED = QASM_HEAD + """qreg q[6];
creg c[2];
reset q[4];
reset q[5];
h q[4];
cx q[4],q[0];
barrier q[4],q[0];
cx q[4],q[1];
barrier q[4],q[1];
cx q[4],q[2];
barrier q[4];
h q[4];
cx q[0],q[5];
barrier q[0],q[5];
cx q[1],q[5];
barrier q[1],q[5];
cx q[3],q[5];
barrier q[3],q[5],q[4];
measure q[4] -> c[0];
measure q[5] -> c[1];
"""
# the same round fenced between two riders as well: they never meet, so nothing is refused
FENCED_RIDERS = FENCED.replace("barrier q[4];\n", "barrier q[4];\nbarrier q[0],q[1];\n")
# two CZ contacts and a CX, which are different gates (no barrier: this one is the contact's)
CZ = QASM_HEAD + """qreg q[4];
creg c[1];
h q[3];
cz q[3],q[0];
cx q[1],q[3];
cz q[2],q[3];
measure q[3] -> c[0];
"""

FENCE_CASES = {
    "ring with docks, rotate: barriers between a docked and a riding qubit": ("ring", FENCED, "rotate"),
    "ring with docks, rotate: a barrier between two riders": ("ring", FENCED_RIDERS, "rotate"),
    "chain, general router": ("chain", FENCED_RIDERS, "compile"),
    "grid, general router": ("grid", FENCED, "compile"),
}
FENCE_DEVICES = {
    "ring": lambda: Machine.ring(12, 2, 6, name="docks").arch.to_json(),
    "chain": lambda: Machine.chain(8, name="line").arch.to_json(),
    "grid": lambda: Machine.grid(3, 3, name="g").arch.to_json(),
}


def _two_qubit_gates(qasm):
    return sum(1 for ln in qasm.splitlines() if ln.split(" ")[0] in ("cx", "cz"))


def _r10(prefix, qasm, arch, expanded):
    """R10 on a compiled programme: the stabilizer tableau composed from the emitted pulses
    against the circuit's (`check_cert.py`), and the proved Lean checker when it is built."""
    pytest.importorskip("stim")
    pytest.importorskip("qiskit")
    sys.path.insert(0, str(BRIDGE))
    from check_cert import find_qcheck

    cmd = [sys.executable, str(BRIDGE / "check_cert.py"), str(prefix), "--qasm", str(qasm), "--arch", str(arch),
           "--json"]
    if find_qcheck(REPO / "Compiler" / "lean") is not None:
        subprocess.run([sys.executable, str(BRIDGE / "mk_qcheck_input.py"), str(prefix), "--arch", str(expanded),
                        "-o", str(prefix) + ".qin.json"], check=True, capture_output=True)
        cmd += ["--qcheck", str(prefix) + ".qin.json"]
    cp = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    return json.loads(cp.stdout)


def _fenced_right(raw, cert, qasm, verdict):
    """The programme has exactly the circuit's two-qubit gates, a witness for every gate and
    none for a barrier, and R10 accepts it."""
    ms = sum(len(i.get("pairs") or []) for i in raw["instructions"] if i["type"] == "gate" and i.get("gate") == "MS")
    assert ms == _two_qubit_gates(qasm), f"{ms} entangling pulses for {_two_qubit_gates(qasm)} two-qubit gates"
    name = {o["i"]: o["name"] for o in cert["circuit_ops"]}
    seen = Counter(g["dag"] for g in cert["gates"])
    assert not [d for d in seen if name[d] == "barrier"], "a barrier has a gate witness"
    gates = [i for i, n in name.items() if n not in ("barrier", "measure", "reset")]
    assert all(seen[i] == 1 for i in gates) and len(seen) == len(gates)
    assert verdict["o2_semantics"] == "ok", verdict
    assert verdict["R10"] in ("partial", "passed"), verdict
    assert verdict.get("lean", "accepted") == "accepted", verdict


def _compiled_right(device, qasm, mode, tmp_path):
    (tmp_path / "c.qasm").write_text(qasm, encoding="utf-8")
    raw, _, bad = _compile(FENCE_DEVICES[device](), None, tmp_path, mode=mode, qasm=tmp_path / "c.qasm")
    assert not bad, f"{dict(Counter(v.rule for v in bad))}; first: {bad[0]}"
    cert = json.loads((tmp_path / "prog.qcert.json").read_text(encoding="utf-8"))
    if mode == "rotate":
        assert dict(cert["claims"])["contacts"] == _two_qubit_gates(qasm)
    verdict = _r10(tmp_path / "prog", tmp_path / "c.qasm", tmp_path / "device.arch.json",
                   tmp_path / "device.expanded.json")
    _fenced_right(raw, cert, qasm, verdict)


@pytest.mark.parametrize("case", list(FENCE_CASES))
def test_a_barrier_of_any_width_is_a_fence_never_a_gate(case, tmp_path):
    _compiled_right(*FENCE_CASES[case], tmp_path)


def test_a_contact_runs_its_own_gate_not_a_cx(tmp_path):
    """The conveyor's contact emitted the CX decomposition for every two-qubit op; a CZ is
    another gate, and the programme passed every rule while computing something else."""
    _compiled_right("ring", CZ, "rotate", tmp_path)


@pytest.mark.parametrize("device", ["chain", "grid"])
def test_a_barrier_does_not_pull_two_ions_together(device, tmp_path):
    """The placer weighs a pair by how often its qubits interact.  c7c535380bfb counted
    `barrier a,b` as an interaction, so a fence at the very END of a circuit, where it orders
    nothing, still moved where the ions start (on the chain: 10 transport cycles became 16).
    The programme with it is the programme without it."""
    body = "qreg q[6];\nh q[0];\n" + "".join(f"cx q[{i}],q[{i + 1}];\n" for i in range(5))
    progs = {}
    for tag, tail in (("plain", ""), ("fenced", "barrier q[0],q[5];\n")):
        (tmp_path / tag).mkdir()
        (tmp_path / tag / "c.qasm").write_text(QASM_HEAD + body + tail, encoding="utf-8")
        raw, _, bad = _compile(FENCE_DEVICES[device](), None, tmp_path / tag, mode="compile",
                               qasm=tmp_path / tag / "c.qasm")
        assert not bad, f"{tag}: {dict(Counter(v.rule for v in bad))}; first: {bad[0]}"
        progs[tag] = [{k: v for k, v in i.items() if k != "meta"} for i in raw["instructions"]
                      if i["type"] != "barrier"]
    assert progs["fenced"][0] == progs["plain"][0], "the barrier moved where the ions start"
    assert progs["fenced"] == progs["plain"]


def test_a_barrier_on_the_race_track_is_a_fence(tmp_path):
    """The race track (H2: one loop, no docks) with a two-qubit barrier either side of its one
    CX: one entangling pulse, no witness for either barrier, and R10 accepts it."""
    m5 = next(c for c in H2_MINIMAL if c["id"].startswith("M5"))
    qasm = (QASM_HEAD + "qreg q[32];\nh q[0];\nbarrier q[0],q[31];\ncx q[0],q[31];\nbarrier q[31],q[0];\n"
            "h q[31];\n")
    raw, _ = _h2_minimal({**m5, "id": "pair barriers", "qasm": qasm}, tmp_path)
    assert any((i.get("template") or {}).get("kind") == "loop_shift" for i in raw["instructions"]
               if i["type"] == "simd"), "not the race track: the subject is its pass"
    exp = tmp_path / "h2.expanded.json"
    cert = json.loads((tmp_path / "prog.qcert.json").read_text(encoding="utf-8"))
    _fenced_right(raw, cert, qasm, _r10(tmp_path / "prog", tmp_path / "c.qasm", H2_ARCH, exp))


# ------------------------------------------------------------------ a crowded layer is split
#
# Two surface-5 patches side by side (98 ions; the X and the Z memory of `qccd.qec`) put about
# 50 movers into each CNOT layer of grid9x9.  Prioritised planning parks the first ions it
# routes, and they wall in the last: every placement and every trap order was refused, on every
# shipped device (2026-10-21).  The split rung runs such a round with half its ops and defers
# the rest, which is what a round already does for an op that finds no free trap.

def test_a_crowded_layer_is_split_not_refused(tmp_path):
    from qccd.qec.experiments import rotated_surface_xz

    exp = rotated_surface_xz(5)
    (tmp_path / "c.qasm").write_text(exp.qasm, encoding="utf-8")
    doc = json.loads((REPO / "arch" / "grid9x9.arch.json").read_text(encoding="utf-8"))
    _, _, bad = _compile(doc, None, tmp_path, mode="compile", qasm=tmp_path / "c.qasm")
    assert not bad, f"{dict(Counter(v.rule for v in bad))}; first: {bad[0]}"
    cp = subprocess.run([sys.executable, str(BRIDGE / "check_cert.py"), str(tmp_path / "prog"),
                         "--qasm", str(tmp_path / "c.qasm"), "--arch", str(tmp_path / "device.arch.json"),
                         "--json"], capture_output=True, text=True, timeout=600)
    verdict = json.loads(cp.stdout)
    assert verdict["order"] == "ok" and verdict["o2_semantics"] == "ok" and verdict["o1_transport"] == "ok"


def _dedicated(n: int) -> str:
    """One ancilla per check: n data, n ancillas, each check reset-H-CX-CX-H-measure."""
    body = "".join(f"reset q[{n + a}];\nh q[{n + a}];\ncx q[{n + a}],q[{a}];\ncx q[{n + a}],q[{(a + 1) % n}];\n"
                   f"h q[{n + a}];\nmeasure q[{n + a}] -> c[{a}];\n" for a in range(n))
    return f'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[{2 * n}];\ncreg c[{n}];\n' + body


@pytest.mark.parametrize("switches", [True, False])
def test_dedicated_ancillas_dock_without_a_placement(switches, tmp_path):
    """A round with one ancilla per check splits its qubits evenly, and rotate parked the
    data -- the tie went to the side holding q0 -- then refused every ancilla's reset:
    "reset acts on q4, which rides the loop" (BB[[144,12,12]] with 144 dedicated ancillas on
    a 144-dock ring, 2026-10-21).  It now docks the side that acts alone.  On a ring whose
    spurs share one waveform (no per-site switch) it also docks every rider in every batch,
    unasked: anything else breaks R4."""
    doc = Machine.ring(8, 2, verticals=4, name="dedicated").arch.to_json()
    doc["control"]["channels"]["switch_per_site"] = switches
    (tmp_path / "c.qasm").write_text(_dedicated(4), encoding="utf-8")
    raw, arch, bad = _compile(doc, None, tmp_path, mode="rotate", qasm=tmp_path / "c.qasm")
    assert not bad, f"{dict(Counter(v.rule for v in bad))}; first: {bad[0]}"
    cert = json.loads((tmp_path / "prog.qcert.json").read_text(encoding="utf-8"))
    on_loop = {n for lp in arch.device.loops.values() if lp.closed for n in lp.nodes}
    ancilla_sites = {cert["init"][cert["map"][str(4 + a)]] for a in range(4)}
    data_sites = {cert["init"][cert["map"][str(d)]] for d in range(4)}
    assert not ancilla_sites & on_loop and data_sites <= on_loop  # the ancillas are the docked side
    moves = Counter(m["ion"] for m in cert["moves"])
    if not switches:                                              # dock-all: idle riders dock too
        assert len(moves) == 4 and sum(moves.values()) > 2 * 8
