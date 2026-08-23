"""Odd-even transposition sort as a reconfiguration scheme.  PLAN §1 / M4 groundwork.

The scheme has to be a first-class citizen -- emitted as TSIR, replayed by the same
engine, judged by the same rules -- or the comparison against rotation is a comparison
between a measurement and a formula.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.arch import load  # noqa: E402
from qccd.compile.oddeven import (  # noqa: E402
    MERGE_CLASS,
    SPLIT_CLASS,
    cyclic_shift_target,
    odd_even_rounds,
    odd_even_sort_program,
)
from qccd.cost import corrected_model, deck_model  # noqa: E402
from qccd.verify import replay, verify  # noqa: E402


@pytest.fixture(scope="module")
def base():
    return load(ROOT / "arch" / "cyclone_base.arch.json")


@pytest.fixture(scope="module")
def ring():
    return load(ROOT / "arch" / "ring144_24v.arch.json")


def test_odd_even_rounds_sorts_in_at_most_n_rounds():
    rng = random.Random(7)
    for n in (2, 5, 16, 33):
        keys = list(range(n))
        rng.shuffle(keys)
        rounds, final = odd_even_rounds(keys)
        assert final == sorted(keys)
        assert len(rounds) <= n


def test_the_program_reaches_the_target_permutation(base):
    m = len(base.device.loops["L0"].nodes)
    ions = [f"d{i}" for i in range(m)]
    rng = random.Random(11)
    start = ions[:]
    rng.shuffle(start)
    sr = odd_even_sort_program(base, start, ions, arch_spec="arch/cyclone_base.arch.json")
    assert sr.reached_target
    res = replay(sr.program, base, deck_model(), check_rules=False)
    nodes = list(base.device.loops["L0"].nodes)
    for slot, ion in enumerate(ions):
        assert res.final_positions[ion] == nodes[slot]


def test_it_uses_exactly_two_classes_one_per_direction(base):
    m = len(base.device.loops["L0"].nodes)
    ions = [f"d{i}" for i in range(m)]
    sr = odd_even_sort_program(
        base, ions, cyclic_shift_target(ions, m // 2),
        arch_spec="arch/cyclone_base.arch.json",
    )
    templates = sr.program.templates()
    assert set(templates) == {f"class:{MERGE_CLASS}", f"class:{SPLIT_CLASS}"}
    # a merge cycle and a split cycle per active round: two classes, so WISE cannot
    # share a cycle between them
    assert sr.cycles == 2 * sr.active_rounds
    assert sr.serialization_factor == pytest.approx(2.0, abs=0.05)


def test_it_is_legal_on_a_loop_with_no_junctions(base):
    m = len(base.device.loops["L0"].nodes)
    ions = [f"d{i}" for i in range(m)]
    sr = odd_even_sort_program(
        base, ions, cyclic_shift_target(ions, m // 2),
        arch_spec="arch/cyclone_base.arch.json",
    )
    report = verify(sr.program, base, corrected_model(), check_metrics=False)
    assert report.rules.ok(), report.rules.summary()


def test_it_is_ILLEGAL_on_the_shipped_ring_because_of_the_dock_junctions(ring):
    """A transposition parks two ions in one slot; 24 of the shipped ring's slots are
    degree-3 T-junctions, where R2 allows exactly one."""
    n = len(ring.device.loops["L0"].nodes)
    ions = [f"d{i}" for i in range(n)]
    sr = odd_even_sort_program(
        ring, ions, cyclic_shift_target(ions, n // 2),
        arch_spec="arch/ring144_24v.arch.json",
    )
    report = verify(sr.program, ring, corrected_model(), check_metrics=False)
    assert "R2" in report.rules.failed()
    offenders = {
        v.message.split()[1] for v in report.rules.violations if v.rule == "R2"
    }
    assert offenders <= {n.id for n in ring.device.labelled("dock")}


def test_split_and_merge_are_charged_once_per_transposition(base):
    ions = [f"d{i}" for i in range(len(base.device.loops["L0"].nodes))]
    sr = odd_even_sort_program(
        base, ions, cyclic_shift_target(ions, 1),
        arch_spec="arch/cyclone_base.arch.json",
    )
    res = replay(sr.program, base, corrected_model(), check_rules=False)
    split = base.primitives.curve("split").pick(corrected_model().policy).quanta
    merge = base.primitives.curve("merge").pick(corrected_model().policy).quanta
    assert res.quanta_components["split_merge"] == pytest.approx(
        sr.transpositions * (split + merge)
    )


def test_rotation_reaches_only_cyclic_shifts(base):
    """A loop shift generates exactly the cyclic group -- which is why rotation is cheap
    and why it cannot stand in for a sort."""
    m = len(base.device.loops["L0"].nodes)
    ions = [f"d{i}" for i in range(m)]
    shift = base.device.shift_map("L0", 3)
    nodes = list(base.device.loops["L0"].nodes)
    reached = {nodes[i]: shift[nodes[i]] for i in range(m)}
    assert reached == {nodes[i]: nodes[(i + 3) % m] for i in range(m)}
    # a shift by k is a single cycle of length m/gcd(m,k); it can never be a transposition
    assert cyclic_shift_target(ions, 0) == ions


def test_a_shift_by_one_is_the_worst_case_for_a_linear_sort(base):
    """Rotation's best case is the sort's worst: one ion has to cross the whole array."""
    m = len(base.device.loops["L0"].nodes)
    ions = [f"d{i}" for i in range(m)]
    sr = odd_even_sort_program(
        base, ions, cyclic_shift_target(ions, 1),
        arch_spec="arch/cyclone_base.arch.json",
    )
    assert sr.transpositions == m - 1
    assert sr.active_rounds == m - 1
    assert sr.cycles == 2 * (m - 1)


def test_start_and_target_must_be_permutations_of_one_set(base):
    with pytest.raises(ValueError, match="permutations"):
        odd_even_sort_program(base, ["a", "b"], ["a", "c"])
