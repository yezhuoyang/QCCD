"""`qccd.fullstack.scale.programs`: every program the lattice schedule copies, and the schedule's
times (`lattice.Costs`) held to those programs' own replayed times, to the microsecond -- a merge
that is 40 µs longer than its slot would let the next gadget on its tiles start before it ends."""
from __future__ import annotations

import pytest

from qccd.fullstack.scale import programs as P
from qccd.fullstack.scale.lattice import Costs

C = Costs()


@pytest.mark.parametrize("shape,slot", [
    (("idle",), C.round_us),
    (("prep", "Y"), C.prep_y_us),
    (("merge", "Z", (((0, 0), "bottom"), ((0, 1), "top")), ()), C.merge_us),
    (("ht",), C.hbar_us),
])
def test_a_slot_is_its_programs_time(shape, slot):
    assert P.machine_us(P.build(shape)) == slot


def test_a_preparation_or_readout_fits_its_slot_and_the_slowest_fills_it():
    prep = {b: P.machine_us(P.build(("prep", b))) for b in "ZXT"}
    read = {b: P.machine_us(P.build(("read", b))) for b in "ZX"}
    assert max(prep.values()) == C.prep_us and max(read.values()) == C.read_us
    assert prep == {"Z": 310.0, "X": 330.0, "T": 345.0} and read == {"Z": 420.0, "X": 440.0}


def test_decoding_is_taken_as_one_round():
    assert C.decode_us == C.round_us


def test_the_idle_round_resets_its_measure_ions_and_never_touches_the_data():
    b = P.build(("idle",))
    data = b.ions - 120                    # d = 11: 121 data ions, 120 checks
    lines = b.qasm.splitlines()
    resets = [ln for ln in lines if ln.startswith("reset")]
    assert len(resets) == 120 and all(int(ln[8:-2]) >= data for ln in resets)
    assert not any(ln.startswith("measure") and int(ln.split("[")[1].split("]")[0]) < data for ln in lines)


def test_the_t_preparation_is_the_injections_with_the_corner_as_its_clifford_twin():
    q = P.build(("prep", "T")).qasm
    assert "h q[0];\ns q[0];" in q and q.count("reset") == 121
    assert q.count("\nh ") == 1 + 55       # the corner, and the lower-left triangle r > c (11 * 10 / 2)


def test_a_shape_with_no_program_is_refused():
    with pytest.raises(ValueError, match="no program"):
        P.build(("prep", "Q"))
