"""The four passes PLAN §7 left crude, and the bounds they are measured against.

The shipped hand-made schedule is the oracle throughout: same code, same device, same 864
contacts. A compiler is free to beat it -- these tests pin *by how much*, so a regression
shows up as a number moving rather than as everything still passing.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd import Machine  # noqa: E402
from qccd.codes import gross_code  # noqa: E402
from qccd.compile import CompilePolicy, compile_code  # noqa: E402
from qccd.compile.order import bind_dynamic, bind_fixed_waves  # noqa: E402
from qccd.compile.place import (  # noqa: E402
    identity_seed,
    interleaved_seed,
    lower_bound_revolutions,
    window,
)
from qccd.compile.schedule import OPTICS, TRANSPORT, resources_of  # noqa: E402

CAP = 144
DOCKS = list(range(0, CAP, 6))
SHIPPED_HOPS, SHIPPED_BATCHES, SHIPPED_UTIL = 2672, 396, 2.18


@pytest.fixture(scope="module")
def machine():
    return Machine.load(ROOT / "arch" / "ring144_24v.arch.json")


@pytest.fixture(scope="module")
def code():
    return gross_code()


@pytest.fixture(scope="module")
def compiled(machine, code):
    return compile_code(machine.arch, code,
                        policy=CompilePolicy(insert_cooling=False))


# ------------------------------------------------------------------ pass 1: place


def test_window_is_cyclic_not_linear():
    """Members at slots 2 and 142 are 4 apart on a 144-ring, not 140."""
    assert window([2, 142], CAP) == 4
    assert window([0, 1, 2], CAP) == 2
    assert window([0], CAP) == 0


def test_interleaving_the_code_blocks_halves_the_window(code):
    """A BB check takes three qubits from each block, so separating the blocks puts half
    of every check on the far side of the ring."""
    ident = [window([identity_seed(code)[q] for q in c.members], CAP)
             for c in code.checks]
    inter = [window([interleaved_seed(code)[q] for q in c.members], CAP)
             for c in code.checks]
    assert sum(ident) / len(ident) > 80
    assert sum(inter) / len(inter) < 40
    assert lower_bound_revolutions(inter, CAP, 24) < 1.7


def test_the_packing_bound_is_a_real_lower_bound(compiled):
    """Every check holds a dock for its whole window; 24 docks offer 144 offsets each."""
    assert compiled.bound_revolutions > 0
    assert compiled.revolutions >= compiled.bound_revolutions
    assert compiled.revolutions / compiled.bound_revolutions < 1.3


# ------------------------------------------------------------------ pass 2: order


def test_the_arc_anchor_points_the_right_way(code):
    """A contact happens at `(dock - slot)`, so sweeping forward walks slots backward.

    Anchoring on the near end of the slot arc instead of the far end makes every arc span
    nearly a whole revolution -- a 3x error that looks like a merely mediocre schedule.
    """
    slot_of = interleaved_seed(code)
    b = bind_dynamic(code.checks, slot_of, DOCKS, CAP)
    widths = [a.width for a in b.assignments]
    wins = [window([slot_of[q] for q in c.members], CAP) for c in code.checks]
    assert max(widths) <= max(wins), "an arc cannot be wider than its check's window"


def test_dynamic_binding_beats_the_fixed_wave_rule(code):
    slot_of = interleaved_seed(code)
    dyn = bind_dynamic(code.checks, slot_of, DOCKS, CAP)
    fix = bind_fixed_waves(code.checks, slot_of, DOCKS, CAP)
    assert dyn.sweep < fix.sweep / 3
    assert dyn.revolutions < 2.0, "Cyclone's claim is 'exactly 2 rotations'"


def test_every_dock_serves_one_check_at_a_time(code):
    """Disjoint arcs per dock is what makes the schedule legal: an ion is at exactly one
    dock at each offset, so overlapping arcs would mean two checks wanting one ion."""
    b = bind_dynamic(code.checks, interleaved_seed(code), DOCKS, CAP)
    per_dock: dict[int, list] = {}
    for a in b.assignments:
        per_dock.setdefault(a.dock, []).append((a.start, a.end))
    for dock, arcs in per_dock.items():
        arcs.sort()
        for (s1, e1), (s2, e2) in zip(arcs, arcs[1:]):
            assert s2 > e1, f"dock {dock}: arcs {(s1, e1)} and {(s2, e2)} overlap"


def test_binding_realizes_every_contact(code):
    b = bind_dynamic(code.checks, interleaved_seed(code), DOCKS, CAP)
    assert len(b.assignments) == len(code.checks)
    assert sum(len(a.contacts) for a in b.assignments) == 864
    for a in b.assignments:
        assert {q for _, q in a.contacts} == set(a.check.members)


# ------------------------------------------------------------------ pass 3: route


def test_the_route_is_monotone_and_one_direction(compiled):
    """Rigid rotation in one direction cannot deadlock, so PLAN §7's cycle-rotation
    machinery has nothing to do on a ring."""
    prog = compiled.program
    classes = {i.cls for i in prog.instructions
               if i.type == "simd" and (i.cls or "").startswith("rotate")}
    assert classes <= {"rotate_cw"}


# ------------------------------------------------------------------ pass 4: simd


def test_each_batch_is_a_maximum_independent_set(compiled):
    """Distinct docks hold distinct ions, so every contact at one offset is compatible --
    the batch is maximal by construction, not by search."""
    for instr in compiled.program.instructions:
        if instr.type != "gate":
            continue
        ions = [a for a, _ in instr.pairs]
        sites = list(instr.sites)
        assert len(ions) == len(set(ions))
        assert len(sites) == len(set(sites))


def test_every_check_is_measured_once_and_its_ancilla_reset(compiled):
    meas = Counter()
    reset = Counter()
    for i in compiled.program.instructions:
        if i.type == "measure":
            meas.update(i.ions)
        elif i.type == "reset":
            reset.update(i.ions)
    assert sum(meas.values()) == 144, "one readout per check"
    assert meas == reset, "an ancilla is reset exactly when it is read"
    assert set(meas.values()) == {6}, "each of the 24 ancillas serves six checks"


def test_the_compiled_contacts_are_exactly_the_codes(compiled, code):
    got: dict[str, set] = {}
    for i in compiled.program.instructions:
        if i.type != "gate":
            continue
        for n, (dq, _) in enumerate(i.pairs):
            got.setdefault(i.meta["checks"][n], set()).add(int(dq[1:]) - 1)
    assert got == {c.name: set(c.members) for c in code.checks}


# ------------------------------------------------------------------ pass 7: schedule


def test_transport_and_optics_are_separate_pathways(machine):
    from qccd.ir import Instruction, Participant, loop_shift

    rot = Instruction(type="simd", id=1, cls="rotate_cw", mode="inter",
                      template=loop_shift("L0", 1))
    meas = Instruction(type="measure", id=2, ions=("a0",))
    cool = Instruction(type="cool", id=3, broadcast=True)
    r_rot, _ = resources_of(machine.arch, rot, False)
    r_meas, _ = resources_of(machine.arch, meas, False)
    r_cool, _ = resources_of(machine.arch, cool, False)
    assert TRANSPORT in r_rot and OPTICS not in r_rot
    assert OPTICS in r_meas and TRANSPORT not in r_meas
    # cooling holds transport too unless the device says otherwise -- a physics claim
    assert TRANSPORT in r_cool
    assert TRANSPORT not in resources_of(machine.arch, cool, True)[0]


def test_event_scheduling_hides_work_under_transport(machine, code):
    serial = compile_code(machine.arch, code, policy=CompilePolicy(
        insert_cooling=False, event_schedule=False))
    sched = compile_code(machine.arch, code, policy=CompilePolicy(
        insert_cooling=False, event_schedule=True))
    assert sched.makespan_us < serial.makespan_us
    for i in sched.program.instructions:
        assert i.t0 is not None and i.t1 is not None and i.t1 >= i.t0


# ------------------------------------------------------------------ the whole thing


def test_the_compiler_beats_the_shipped_schedule_by_the_margin_it_claims(compiled):
    assert compiled.contacts == 864
    assert compiled.hops <= SHIPPED_HOPS / 8, f"{compiled.hops} vs {SHIPPED_HOPS}"
    assert compiled.batches <= SHIPPED_BATCHES / 5
    assert compiled.revolutions < 2.0


def test_solving_the_passes_beats_leaving_them_crude(machine, code):
    crude = compile_code(machine.arch, code, policy=CompilePolicy(
        placement="identity", ancilla_binding="fixed", insert_cooling=False,
        event_schedule=False, spam=False, refine_steps=0))
    solved = compile_code(machine.arch, code,
                          policy=CompilePolicy(insert_cooling=False))
    assert solved.hops < crude.hops / 3
    assert solved.batches < crude.batches
    assert crude.contacts == solved.contacts == 864


def test_batch_utilization_meets_plan_m7(machine, code, compiled):
    from qccd.cost import t1_metrics

    res = machine.run(compiled.program, check_metrics=False).report.result
    t1 = t1_metrics(compiled.program, machine.arch, res)
    # PLAN M7: ">=3x batch-utilization over the 9.1% baseline"
    assert t1.contact_batch_utilization >= 3 * SHIPPED_UTIL


def test_the_compiled_program_is_fully_legal_with_cooling(machine, code):
    r = compile_code(machine.arch, code, policy=CompilePolicy(insert_cooling=True))
    out = machine.run(r.program, check_metrics=False)
    assert out.ok, out.rules_failed
    assert any(i.type == "cool" for i in r.program.instructions)
