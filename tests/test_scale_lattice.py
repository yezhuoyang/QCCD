"""The patch-lattice scheduler (qccd/fullstack/scale/lattice.py, FULLSTACK_PLAN S9 step 3).

Held here, on a small stream in the adder's own idioms (a lattice-surgery CNOT, a fan-out, an X̄
readout with a fresh |0̄>, a guarded CZ whose ancilla takes H̄):
* the schedule passes its check -- no two gadgets on one tile at once, every block's
  instructions in program order, every guard decoded before it is read;
* every merge it places is a corridor merge that measures its product at distance d;
* independent work runs at once, a guarded instruction waits for its outcome, and a mixed
  product is refused, saying why.
"""
from __future__ import annotations

import pytest

from qccd.fullstack.scale.lattice import Costs, LatticeError, check, columns, schedule

STREAM = [
    ("prep", ["L0", "X"], None, []),
    ("ppm", ["Z", "q0", "Z", "L0"], "m0", []),
    ("ppm", ["X", "L0", "X", "q1"], "m1", []),
    ("read", ["L0", "Z"], "m2", []),
    ("x", ["q1"], None, ["m0"]),
    ("prep", ["L1", "X"], None, []),
    ("ppm", ["Z", "q3", "Z", "L1"], "m3", []),
    ("ppm", ["X", "L1", "X", "q4", "X", "q5"], "m4", []),
    ("read", ["L1", "Z"], "m5", []),
    ("read", ["q1", "X"], "m6", []),
    ("prep", ["q1", "Z"], None, []),
    ("prep", ["L2", "X"], None, []),
    ("ppm", ["Z", "q2", "Z", "L2"], "m7", ["m6"]),
    ("ht", ["L2"], None, ["m6"]),
    ("ppm", ["Z", "L2", "Z", "q0"], "m8", ["m6"]),
    ("read", ["L2", "X"], "m9", ["m6"]),
]


def _run():
    homes, bounds = columns([f"q{k}" for k in range(6)], 3)
    return schedule(STREAM, homes, bounds), homes


def test_the_schedule_passes_its_check():
    gadgets, _ = _run()
    assert check(STREAM, gadgets) == []
    assert sorted({g.kind for g in gadgets}) == ["ht", "merge", "prep", "read"]


def test_every_merge_placed_is_a_corridor_merge_at_full_distance():
    from qccd.qec.experiments import corridor_merge, ideal_stim
    gadgets, _ = _run()
    for shape in {g.shape for g in gadgets if g.kind == "merge"}:
        _, letter, patches, corridor = shape
        exp = corridor_merge(3, letter, list(patches), set(corridor))
        c = ideal_stim(exp)
        c.detector_error_model()
        assert not c.compile_detector_sampler().sample(16, append_observables=True).any()
        assert len(ideal_stim(exp, 1e-3).shortest_graphlike_error(ignore_ungraphlike_errors=True)) == 3


def test_independent_work_runs_at_once_and_a_guard_waits():
    gadgets, _ = _run()
    at = {g.line: g for g in gadgets}
    first, second = at[1], at[6]          # the two CNOTs' first merges share no block
    assert first.t0 < second.t1 and second.t0 < first.t1
    assert at[12].t0 >= at[9].t1 + Costs().decode_us     # the CZ waits for m6, decoded
    assert at[13].t1 - at[13].t0 == Costs().hbar_us


def test_a_mixed_product_is_refused():
    homes, bounds = columns(["q0", "q1"], 3)
    with pytest.raises(LatticeError, match="mixed product"):
        schedule([("ppm", ["X", "q0", "Z", "q1"], "m0", [])], homes, bounds)
