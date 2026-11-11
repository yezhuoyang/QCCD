"""The conveyor sandwich (`qccd.arch.sandwich`) and its router (`qccd.compile.conveyor_router`).

What these tests hold:

* the sandwich is one graph built three ways -- one layer (a planar machine), two, three --
  and each build is clean under the geometry rules;
* its programmes pass the site's standard rule set on every build, with the gates in the
  circuit's order (the certificate's replay);
* loops in different layers turn in the same cycle and loops in one plane take turns: that,
  and the hops, are the whole difference between the builds, so a round on three layers
  takes fewer cycles than on two, and on two fewer than on one;
* a surface code laid row by row plays one loop offset a layer of gates, whatever its
  distance;
* what the router cannot do it refuses by name.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qccd.arch.sandwich import CONVEYOR, HOP, conveyor_sandwich, sandwich_roles
from qccd.compile.c2lr_router import Refused, _parse
from qccd.compile.conveyor_router import (_families, _windows, compile_conveyor,
                                          lattice_rows, place)
from qccd.compile.cooling import insert_cooling
from qccd.cost.models import corrected_model
from qccd.ruleset import FAILS, check, legacy_config
from qccd.ruleset import load as load_rules
from qccd.ruleset.compile import circuit_of_certificate
from qccd.ruleset.programme import programme_execution

REPO = Path(__file__).resolve().parents[1]
BB144 = REPO / "tasks" / "bb144" / "circuit.qasm"
SURFACE3 = REPO / "goals" / "goal3" / "codes" / "surface3_z" / "circuit.qasm"
SURFACE5 = REPO / "goals" / "goal3" / "codes" / "surface5_z" / "circuit.qasm"

#: a check on two data qubits by each of two ancillas: an X check (the ancilla controls) and
#: a Z check (the ancilla is the target), the smallest circuit with both kinds
TWO_CHECKS = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[6];
creg c[2];
h q[4];
cx q[4],q[0];
cx q[0],q[5];
cx q[4],q[1];
cx q[1],q[5];
cx q[4],q[2];
cx q[3],q[5];
h q[4];
measure q[4] -> c[0];
measure q[5] -> c[1];
"""


def simd(prog, kind=None):
    return [i for i in prog.instructions if i.type == "simd"
            and (kind is None or (i.meta or {}).get("kind") == kind)]


def rules_failed(machine, prog, cert, table="qccdsim_jones") -> list[str]:
    """The standard rule set on the cooled programme, judged as the grader judges an entry."""
    arch = machine.arch
    cooled = insert_cooling(prog, arch, corrected_model(table)).program
    cooled.meta.update(prog.meta)
    rs = load_rules("standard")
    exe = programme_execution(cooled, arch, corrected_model(table), circuit=circuit_of_certificate(cert),
                              rule_config=legacy_config(rs) or None, check_metrics=False)
    return sorted(x.id for x in check(rs, exe).results if x.verdict == FAILS)


# ------------------------------------------------------------------------------ the machine

@pytest.mark.parametrize("layers, n_layers, risers, nodes", [(1, 1, 0, 28), (2, 2, 8, 24), (3, 3, 16, 24)])
def test_one_graph_built_three_ways(layers, n_layers, risers, nodes):
    m = conveyor_sandwich(8, layers=layers)
    d = m.arch.device
    assert (len(d.layers), len(d.risers), len(d.nodes)) == (n_layers, risers, nodes)
    assert m.violations() == [], "every build is clean under R18-R21"
    roles = sandwich_roles(m.arch)
    assert roles["positions"] == 8 and len(roles["data"]) == 8
    assert [len(lp["slots"]) for lp in roles["loops"]] == [8, 8]
    for lp in roles["loops"]:
        for k, slot in enumerate(lp["slots"]):
            assert d.segment_between(slot, roles["data"][k]) is not None, "slot k faces trap k"
    assert {c["id"] for c in m.arch.to_json()["control"]["classes"]["extra"]} >= {CONVEYOR, HOP}


def test_three_layers_put_a_loop_below_the_data_and_one_above():
    d = conveyor_sandwich(8, layers=3).arch.device
    roles = sandwich_roles(conveyor_sandwich(8, layers=3).arch)
    z = lambda n: d.nodes[n].pos[2]
    assert {z(n) for n in roles["data"]} == {1.0}
    assert [{z(n) for n in lp["slots"]} for lp in roles["loops"]] == [{0.0}, {2.0}]
    # every rail from a slot to its trap is a riser, and stands straight up
    for lp in roles["loops"]:
        for k, slot in enumerate(lp["slots"]):
            assert d.is_riser(d.segment_between(slot, roles["data"][k]).id)


def test_each_layer_of_a_sandwich_has_its_own_broadcast():
    dacs = [conveyor_sandwich(72, layers=k).resources().dacs for k in (1, 2, 3)]
    assert dacs[0] < dacs[1] < dacs[2], dacs
    assert dacs[2] <= 128, "three layers of 72 traps fit the boards' DAC budget"


def test_a_sandwich_is_one_two_or_three_layers():
    with pytest.raises(ValueError, match="1 or 2 layers, or in 3"):
        conveyor_sandwich(8, layers=4)
    with pytest.raises(ValueError, match="1 or 2 layers, or in 3"):
        conveyor_sandwich(8, layers=3, loops=1)
    with pytest.raises(ValueError, match="one loop or two"):
        conveyor_sandwich(8, loops=3)
    assert len(conveyor_sandwich(8, layers=2, loops=1).arch.device.layers) == 2


def test_a_machine_that_is_not_a_sandwich_says_so():
    from qccd import Machine
    with pytest.raises(ValueError, match="not a conveyor sandwich"):
        sandwich_roles(Machine.grid(3, 3).arch)


# ------------------------------------------------------------------------------ the router

@pytest.mark.parametrize("layers", [1, 2, 3])
def test_a_programme_passes_the_standard_rules_on_every_build(layers):
    m = conveyor_sandwich(4, layers=layers)
    prog, cert = compile_conveyor(TWO_CHECKS, m.arch, 4, name="two_checks")
    assert rules_failed(m, prog, cert) == []
    assert prog.meta["compiler"] == "conveyor-router"
    assert prog.meta["waveforms"] == ("one a layer" if layers > 1 else "one")


@pytest.mark.parametrize("layers", [1, 3])
def test_the_surface_code_passes_the_standard_rules(layers):
    positions, data_at = lattice_rows(3, 3)
    m = conveyor_sandwich(positions, layers=layers)
    prog, cert = compile_conveyor(SURFACE3.read_text(encoding="utf-8"), m.arch, 9, name="surface3",
                                  data_at=data_at)
    assert rules_failed(m, prog, cert) == []


def test_the_data_never_move():
    m = conveyor_sandwich(4, layers=3)
    prog, _ = compile_conveyor(TWO_CHECKS, m.arch, 4)
    movers = {p.ion for i in simd(prog) for p in i.participants}
    assert movers == {"q4", "q5"}, "only the ancillas ride"


def test_x_checks_ride_one_loop_and_z_checks_the_other():
    n, ops, _ = _parse(TWO_CHECKS)
    assert _families(ops, n, 4, 2, 4) == [[4], [5]]
    n, ops, _ = _parse(BB144.read_text(encoding="utf-8"))
    fams = _families(ops, n, 144, 2, 72)
    assert [len(f) for f in fams] == [72, 72]


def test_loops_in_different_layers_turn_in_the_same_cycle():
    """The one thing layers add to this machine: a waveform a layer.  On three layers some
    cycle moves ancillas of both loops; in a plane none does, except a hop whose lab-frame
    direction the two loops happen to share."""
    qasm = BB144.read_text(encoding="utf-8")
    both = {}
    for layers in (1, 3):
        m = conveyor_sandwich(72, layers=layers, capacity=3)
        prog, _ = compile_conveyor(qasm, m.arch, 144, name="bb144")
        loop_of = {f"q{q}": (0 if q < 216 else 1) for q in range(144, 288)}
        both[layers] = sum(len({loop_of[p.ion] for p in i.participants}) == 2 for i in simd(prog, "rotate"))
    assert both[1] == 0 and both[3] > 0, both


def test_every_layer_added_takes_cycles_off_a_round_of_the_bicycle_code():
    qasm = BB144.read_text(encoding="utf-8")
    cycles = {}
    for layers in (1, 2, 3):
        m = conveyor_sandwich(72, layers=layers, capacity=3)
        prog, _ = compile_conveyor(qasm, m.arch, 144, name="bb144")
        cycles[layers] = len(simd(prog))
    assert cycles[3] < cycles[2] < cycles[1], cycles
    assert cycles[3] <= 120, "a round of [[144,12,12]] in about a hundred cycles"


def test_a_translation_of_the_bicycle_code_is_one_offset_or_two():
    """Numbered 6 i + j on its 12 x 6 torus, a layer of gates is one loop offset, or two
    where the translation wraps a row: 9 visits a loop a round, 18 in all."""
    m = conveyor_sandwich(72, layers=3, capacity=3)
    prog, _ = compile_conveyor(BB144.read_text(encoding="utf-8"), m.arch, 144, name="bb144", wait=0)
    assert prog.meta["placement"] == "index"
    assert len(simd(prog, "hop")) == 36, "18 visits, a descent and an ascent each"


@pytest.mark.parametrize("path, d", [(SURFACE3, 3), (SURFACE5, 5)])
def test_a_surface_code_round_is_four_gate_layers_whatever_its_distance(path, d):
    """Laid row by row with a spare trap a row, every check meets its corners at the bulk's
    four offsets, the boundary checks included: with the loops in step a round plays four
    batches of two-qubit gates, so it needs four coolings."""
    qasm = path.read_text(encoding="utf-8")
    positions, data_at = lattice_rows(d, d)
    assert positions == d * (d + 1) + (d * (d + 1)) % 2
    m = conveyor_sandwich(positions, layers=3)
    prog, _ = compile_conveyor(qasm, m.arch, d * d, name=f"surface{d}", data_at=data_at,
                               align="vote", wait=float("inf"))
    ms = [i for i in prog.instructions if i.type == "gate" and (i.meta or {}).get("kind") == "ms"]
    rounds = d                                   # the Z memory: d rounds
    assert len(ms) == 4 * rounds, (len(ms), rounds)


def test_a_boundary_check_has_a_window_and_a_bulk_check_has_none():
    n, ops, _ = _parse(SURFACE3.read_text(encoding="utf-8"))
    layers, win = _windows(ops, 9)
    assert layers == 4
    weights = sorted(len(v) for v in win.values())
    assert weights == [2, 2, 2, 2, 4, 4, 4, 4]
    free = 0
    for gates in win.values():
        if len(gates) == 4:
            assert [(lo, hi) for lo, hi, _ in gates] == [(0, 0), (1, 1), (2, 2), (3, 3)]
        else:
            free += any(hi > lo for lo, hi, _ in gates)
    assert free, "some weight-2 check may play early or late: its window is the placement's to use"


def test_two_visitors_share_a_trap_only_where_it_has_room():
    m2 = conveyor_sandwich(72, layers=1, capacity=3)
    m3 = conveyor_sandwich(72, layers=1, capacity=4)
    qasm = BB144.read_text(encoding="utf-8")
    for m, most in ((m2, 3), (m3, 4)):
        prog, cert = compile_conveyor(qasm, m.arch, 144, name="bb144")
        occ: dict[str, int] = {}
        init = next(i for i in prog.instructions if i.type == "init")
        for node in init.placement.values():
            occ[node] = occ.get(node, 0) + 1
        peak = max(occ.values())
        for i in simd(prog):
            for p in i.participants:
                occ[p.src] -= 1
                occ[p.dst] = occ.get(p.dst, 0) + 1
            peak = max(peak, max(occ.values()))
        assert peak == most, (peak, most)


# ------------------------------------------------------------------------------ what it refuses

def test_a_gate_between_two_data_qubits_is_refused():
    bad = TWO_CHECKS.replace("cx q[4],q[0];", "cx q[1],q[0];", 1)
    with pytest.raises(Refused, match="between two data qubits"):
        compile_conveyor(bad, conveyor_sandwich(4).arch, 4)


def test_more_ancillas_than_slots_is_refused():
    with pytest.raises(Refused, match="a slot carries one ancilla"):
        n, ops, _ = _parse(BB144.read_text(encoding="utf-8"))
        place(ops, n, 144, 36, 2)


def test_a_trap_too_small_for_its_data_and_a_visitor_is_refused():
    with pytest.raises(Refused, match="too few for its"):
        compile_conveyor(BB144.read_text(encoding="utf-8"), conveyor_sandwich(72, capacity=2).arch, 144)


def test_the_study_table_is_the_table_the_script_writes():
    """`Codesign/data/q07.json` is what `q07_layers.py` wrote: every row names its circuit and
    design, and no design that broke a rule carries a time it could be ranked by."""
    rows = json.loads((REPO / "Codesign" / "data" / "q07.json").read_text(encoding="utf-8"))
    assert rows and all({"circuit", "design", "status"} <= set(r) for r in rows)
    for r in rows:
        if r["status"] != "ok":
            assert r.get("ranked") is not True


def test_the_bb_board_ranks_the_sandwich_by_what_the_router_writes_today():
    """The BB board's three sandwich rows (`Codesign/scripts/bb_studio.py`) are made from the
    release by the router, with no file from a build folder: so the row's time is the time
    of the programme this checkout's router writes, and each layer added is faster."""
    from qccd.arch.device import Architecture
    from qccd.workspace.tasks import board_device, find_release
    rows = {r["key"]: r for r in json.loads((REPO / "BBResults" / "studio" / "manifest.json").read_text(encoding="utf-8"))}
    rel = find_release("bb144@2")
    model = corrected_model(rel.manifest.get("rank_table", "qccdsim_jones"))
    times = {}
    for layers in (1, 2, 3):
        row = rows[f"sandwich72_L{layers}"]
        assert row["status"] == "ok" and row["rules_failed"] == [] and not row["over_budget"]
        assert row["family"] == "conveyor sandwich" and row["numbers"]["engine"] == "conveyor"
        assert row["numbers"]["order_violations"] == 0
        arch = Architecture.from_json(board_device(conveyor_sandwich(72, layers=layers, capacity=3)
                                                   .arch.to_json(expanded=True), rel))
        prog, _ = compile_conveyor(rel.circuit_text(), arch, 144, name="bb144")
        cooled = insert_cooling(prog, arch, model).program
        from qccd import Machine
        times[layers] = Machine(arch).run(cooled, model=model).runtime_ms
        assert row["numbers"]["T_jones"] == pytest.approx(times[layers], abs=1e-6)
    assert times[3] < times[2] < times[1]
    qccdc_best = min(r["numbers"]["T_jones"] for k, r in rows.items()
                     if not k.startswith("sandwich") and (r.get("numbers") or {}).get("T_jones"))
    assert times[1] < qccdc_best, "the planar sandwich is already ahead of every design qccdc serves"
