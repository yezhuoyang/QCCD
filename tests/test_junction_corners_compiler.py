"""The compiler on loops with junction corners: a bend is passed, never a stop.

Reader report T30 (Ke Sun, on lesson A4): a square trap has its trapping sites on the sides
only, and its corners are junctions.  `ring(corners="junction")` and `dual_loop(...)` build
that: each corner is a bare junction where only the loop's two rails meet -- a BEND
(`Device.is_bend`) -- and a loop's SLOTS are its nodes less its bends.  The compiler used to
read every loop node as a slot, so a rotation would have parked an ion on a corner.

Every pass is held to the one rule here, on small devices so the file runs in seconds:

* rigid rotation (`rotate`) turns the loop by slots, an ion beside a corner riding round it
  in one cycle (`via` = the rails on both sides), in both directions;
* the general router (`compile`) crosses a corner in one move, labelled as one step along
  the loop like a straight hop (R4d's `L0:+1`, R22's signature);
* the race track (`racetrack`) steps and swaps round corners;
* the proved checker's input (`mk_qcheck_input.py`) gives the loop's slots, so qcheck
  replays a rotation onto traps and accepts.

Each programme is replayed with every rule on, and no ion may stand on a bend after any
cycle.  Designs without bends -- every device written before junction corners -- compile
byte-identically; that gate is run against the origin compiler, not here.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from qccd.arch.device import Architecture
from qccd.cost.models import corrected_model
from qccd.ir.tsir import TSIR
from qccd.verify.replay import replay
from qccd.workspace.evaluator import Toolchain

REPO = Path(__file__).resolve().parents[1]
BRIDGE = REPO / "Compiler" / "bridge"
EXAMPLES = REPO / "Compiler" / "examples"
TOOLS = Toolchain.discover()
QCCDC = TOOLS.qccdc
pytestmark = pytest.mark.skipif(QCCDC is None, reason="qccdc_cli is not built")

#: (shipped document whose zones, physics and control are kept, generator params)
DEVICES = {
    # ten rail slots of a data zone, two docks: the rotation case
    "ring": ("ring144_24v", {"width": 6, "height": 3, "verticals": 2, "corners": "junction"}),
    # the same ring with trap slots, so the general router can gate on the rail
    "ring_traps": ("ring144_24v", {"width": 6, "height": 3, "verticals": 2, "site_zone": "trap",
                                   "corners": "junction"}),
    # a dockless loop of trap slots: the race track's case
    "cyclone": ("cyclone_base", {"width": 8, "height": 3, "verticals": 0, "site_zone": "trap",
                                 "corners": "junction"}),
    # two concentric loops, eight slots each
    "dual": ("cyclone_dual_loop", {"width": 6, "corners": "junction"}),
}

#: two ancillas at the docks, six data ions riding the loop from slot 0 -- beside the
#: top-left corner -- so the rotations that serve the eight contacts turn ions round corners
TINY = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[8];
creg c[2];
reset q[0];
reset q[1];
h q[0];
cx q[0],q[2];
cx q[0],q[5];
cx q[0],q[7];
cx q[0],q[3];
h q[0];
cx q[4],q[1];
cx q[6],q[1];
cx q[2],q[1];
cx q[7],q[1];
measure q[0] -> c[0];
measure q[1] -> c[1];
"""


def _device(name: str, tmp_path: Path) -> tuple[dict, Path, Path]:
    src, params = DEVICES[name]
    doc = copy.deepcopy(json.loads((REPO / "arch" / f"{src}.arch.json").read_text(encoding="utf-8")))
    doc["name"] = f"junction_{name}"
    doc["geometry"] = {"generator": doc["geometry"]["generator"], "params": params}
    arch_path = tmp_path / "device.arch.json"
    arch_path.write_text(json.dumps(doc), encoding="utf-8")
    exp = tmp_path / "device.expanded.json"
    subprocess.run([sys.executable, str(BRIDGE / "export_arch.py"), str(arch_path), "-o", str(exp)],
                   check=True, capture_output=True)
    return doc, arch_path, exp


def _qasm(circuit: str, tmp_path: Path) -> Path:
    if circuit == "tiny":
        p = tmp_path / "tiny.qasm"
        p.write_text(TINY, encoding="utf-8")
        return p
    return EXAMPLES / f"{circuit}.qasm"


def _compile(name: str, circuit: str, mode: str, tmp_path: Path):
    """Compile, check the stage evidence, cool, and replay with every rule on.  Returns the
    architecture, the raw programme, the rule violations, and what the replay saw at bends."""
    doc, arch_path, exp = _device(name, tmp_path)
    qasm = _qasm(circuit, tmp_path)
    prefix = tmp_path / "prog"
    cp = subprocess.run([str(QCCDC), mode, str(qasm), "--arch", str(exp), "-o", str(prefix)],
                        capture_output=True, text=True, timeout=600)
    assert cp.returncode == 0, cp.stdout[-800:] + cp.stderr[-800:]
    assert not json.loads((tmp_path / "prog.qcert.json").read_text(encoding="utf-8")).get("unrealised")
    cs = subprocess.run([str(QCCDC), "check-stages", str(tmp_path / "prog.stages.json"), "--qasm", str(qasm),
                         "--arch", str(exp), "--program", str(tmp_path / "prog.tsir.json")],
                        capture_output=True, text=True, timeout=600)
    assert '"accepted":true' in cs.stdout, cs.stdout[-400:] + cs.stderr[-400:]
    subprocess.run([sys.executable, str(BRIDGE / "insert_cooling.py"), str(tmp_path / "prog.tsir.json"),
                    "--arch", str(arch_path), "-o", str(tmp_path / "prog.cooled.tsir.json")],
                   check=True, capture_output=True)
    arch = Architecture.from_json(doc)
    dev = arch.device
    bends = {n for n in dev.nodes if dev.is_bend(n)}
    assert len(bends) >= 4                      # the device has corners for the check to see
    seen: Counter = Counter()

    def on_cycle(v):
        rotating = (v.instr.template or {}).get("kind") == "loop_shift"
        for m in v.moves:
            if m.dst in bends:
                seen["rotation passes" if rotating else "move passes"] += 1
        seen["rests"] += sum(1 for node in v.pos_after.values() if node in bends)

    raw = json.loads((tmp_path / "prog.cooled.tsir.json").read_text(encoding="utf-8"))
    res = replay(TSIR.from_json(raw), arch, corrected_model("qccdsim_jones"), check_rules=True,
                 on_cycle=on_cycle)
    return arch, raw, exp, res.rules.violations, seen


def _accepted_by_qcheck(exp: Path, tmp_path: Path) -> None:
    if TOOLS.qcheck is None:
        pytest.skip("the Lean checker is not built")
    subprocess.run([sys.executable, str(BRIDGE / "mk_qcheck_input.py"), str(tmp_path / "prog"), "--arch",
                    str(exp), "-o", str(tmp_path / "qin.json")], check=True, capture_output=True)
    qc = subprocess.run([str(TOOLS.qcheck), str(tmp_path / "qin.json")], capture_output=True, text=True,
                        timeout=900)
    assert "ACCEPTED" in qc.stdout, (qc.stdout + qc.stderr)[-600:]


def test_rotation_turns_ions_round_the_corners_both_ways(tmp_path):
    arch, raw, exp, bad, seen = _compile("ring", "tiny", "rotate", tmp_path)
    assert not bad, f"{dict(Counter(v.rule for v in bad))}; first: {bad[0]}"
    assert seen["rests"] == 0
    assert seen["rotation passes"] > 0
    deltas = [i["template"]["delta"] for i in raw["instructions"] if (i.get("template") or {}).get("kind") == "loop_shift"]
    assert any(d > 0 for d in deltas) and any(d < 0 for d in deltas), deltas
    # the riders start on slots, and the loop has 2W + 2H - 8 of them
    slots = arch.device.loop_slots("L0")
    assert len(slots) == 10 and not any(arch.device.is_bend(n) for n in slots)
    init = next(i for i in raw["instructions"] if i["type"] == "init")["placement"]
    assert all(not arch.device.is_bend(n) for n in init.values())
    _accepted_by_qcheck(exp, tmp_path)
    # the negative control: the same certificate, replayed by the checker over the loop's
    # NODES (what its input said before junction corners), turns ions onto corners and is
    # refused -- so the slots in `mk_qcheck_input.py` are what makes it accept
    qin = json.loads((tmp_path / "qin.json").read_text(encoding="utf-8"))
    qin["loops"] = {lid: list(arch.device.loops[lid].nodes) for lid in qin["loops"]}
    (tmp_path / "qin_nodes.json").write_text(json.dumps(qin), encoding="utf-8")
    qc = subprocess.run([str(TOOLS.qcheck), str(tmp_path / "qin_nodes.json")], capture_output=True,
                        text=True, timeout=900)
    assert "ACCEPTED" not in qc.stdout, qc.stdout[-600:]


def test_a_programme_that_parks_an_ion_on_a_corner_is_refused(tmp_path):
    """The negative control for the rule check above: the compiled rotation, with one more
    cycle that stops a rider on the corner beside it, fails R0 -- a corner is no place to
    stand -- where the programme as compiled holds every rule."""
    from qccd.ir.tsir import Instruction, Participant
    from qccd.ruleset import check_programme

    arch, raw, _, bad, _ = _compile("ring", "tiny", "rotate", tmp_path)
    assert not bad
    dev = arch.device
    prog = TSIR.from_json(raw)
    assert check_programme("standard", prog, arch, corrected_model("qccdsim_jones")).ok
    pos: dict = {}
    replay(prog, arch, corrected_model("qccdsim_jones"), check_rules=False,
           on_cycle=lambda v: pos.update(v.pos_after))
    ion, slot, corner = next((i, s, n) for i, s in sorted(pos.items()) if s in dev.loops["L0"].nodes
                             for n in dev.neighbours(s) if dev.is_bend(n))
    prog.add(Instruction(type="simd", id=prog.next_id(), cls="rotate_cw", mode="inter",
                         participants=(Participant(ion, slot, corner, (dev.segment_between(slot, corner).id,)),)))
    rep = check_programme("standard", prog, arch, corrected_model("qccdsim_jones"))
    assert "R0" in rep.failed(), [(r.id, r.verdict) for r in rep.results]


@pytest.mark.parametrize("name,circuit", [("ring_traps", "qft8"), ("dual", "bv6")])
def test_the_general_router_crosses_a_corner_in_one_move(name, circuit, tmp_path):
    arch, raw, exp, bad, seen = _compile(name, circuit, "compile", tmp_path)
    assert not bad, f"{dict(Counter(v.rule for v in bad))}; first: {bad[0]}"
    assert seen["rests"] == 0
    assert seen["move passes"] > 0
    _accepted_by_qcheck(exp, tmp_path)


def test_the_race_track_steps_round_corners(tmp_path):
    arch, raw, exp, bad, seen = _compile("cyclone", "qft8", "racetrack", tmp_path)
    assert not bad, f"{dict(Counter(v.rule for v in bad))}; first: {bad[0]}"
    assert seen["rests"] == 0
    assert seen["move passes"] > 0
    rot = [i for i in raw["instructions"] if (i.get("template") or {}).get("kind") == "loop_shift"]
    # a shift holds every rail of the loop, the corners' too
    rails = {s for s, seg in arch.device.segments.items() if seg.loop == "L0"}
    assert all(rails <= set(i["holds"]) for i in rot)
    _accepted_by_qcheck(exp, tmp_path)


def test_a_corner_hop_is_one_slot_along_the_loop(tmp_path):
    """R4d's action, as the router locks it: the hop round a corner is `L0:+1` or `L0:-1`,
    exactly like a straight hop between two neighbouring slots."""
    _, _, exp = _device("cyclone", tmp_path)
    out = tmp_path / "inst.json"
    cp = subprocess.run([str(QCCDC), "route-instances", str(EXAMPLES / "ghz4.qasm"), "--arch", str(exp),
                         "-o", str(out)], capture_output=True, text=True, timeout=300)
    assert cp.returncode == 0, cp.stderr[-600:]
    exp_doc = json.loads(exp.read_text(encoding="utf-8"))
    kind = {n["id"]: n.get("kind", "site") for n in exp_doc["geometry"]["nodes"]}
    hops = json.loads(out.read_text(encoding="utf-8"))["instances"][0]["hops"]
    corner = [h for h in hops if len(h["via"]) == 2]
    straight = [h for h in hops if len(h["via"]) == 1]
    assert corner and straight
    assert all(kind[h["to"]] == "site" for h in hops)           # no hop ends on a corner
    assert {tuple(h["action"]) for h in corner} == {("L0", 1), ("L0", -1)}
    assert {tuple(h["action"]) for h in straight} == {("L0", 1), ("L0", -1)}


def test_the_checker_input_gives_the_slots():
    """The Lean checker replays a rotation in the slot order `mk_qcheck_input.py` reads off
    the device: the nodes less the bends -- and, on a loop without bends, the nodes exactly."""
    sys.path.insert(0, str(BRIDGE))
    from export_arch import expand
    from mk_qcheck_input import loop_slots

    for name, n_slots in (("ring", 10), ("cyclone", 14)):
        src, params = DEVICES[name]
        doc = json.loads((REPO / "arch" / f"{src}.arch.json").read_text(encoding="utf-8"))
        doc["geometry"] = {"generator": doc["geometry"]["generator"], "params": params}
        arch = Architecture.from_json(doc)
        exp = expand(arch)
        (lp,) = exp["geometry"]["loops"]
        assert loop_slots(exp, lp) == list(arch.device.loop_slots(lp["id"]))
        assert len(loop_slots(exp, lp)) == n_slots < len(lp["nodes"])
    shipped = Architecture.from_json(json.loads((REPO / "arch" / "ring144_24v.arch.json").read_text(encoding="utf-8")))
    exp = expand(shipped)
    (lp,) = exp["geometry"]["loops"]
    assert loop_slots(exp, lp) == lp["nodes"]
