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

from qccd.fullstack.scale.lattice import Costs, Gadget, LatticeError, check, columns, schedule

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


def test_a_y_preparation_is_the_in_place_gadget():
    homes, bounds = columns(["q0"], 3)
    stream = [("prep", ["L0", "Y"], None, []), ("ppm", ["Z", "q0", "Z", "L0"], "m0", []),
              ("read", ["L0", "X"], "m1", [])]
    gadgets = schedule(stream, homes, bounds)
    assert check(stream, gadgets) == []
    prep = next(g for g in gadgets if g.kind == "prep")
    assert prep.shape == ("prep", "Y") and prep.t1 - prep.t0 == Costs().prep_y_us


def test_the_check_sees_a_gadget_on_a_tile_where_an_ancilla_lives():
    stream = [("prep", ["a", "Y"], None, []), ("ppm", ["Z", "q0", "Z", "q1"], "m0", []),
              ("read", ["a", "X"], "m1", [])]
    tile = frozenset([(1, 0)])
    gadgets = [Gadget("prep", 0.0, 10.0, tile, ("prep", "Y"), 0, ("a",)),
               Gadget("merge", 20.0, 30.0, tile | {(0, 0), (2, 0)}, (), 1, ("q0", "q1")),
               Gadget("read", 40.0, 41.0, tile, ("read", "X"), 2, ("a",))]
    assert check(stream, gadgets) == ["tile (1, 0): line 1 while a lives there"]


def test_reserved_tiles_and_start_times_hold_a_schedule_back():
    homes, bounds = columns(["q0", "q1"], 1)
    stream = [("ppm", ["Z", "q0", "Z", "q1"], "m0", [])]
    bus = [(x, y) for x in range(bounds[0], bounds[2] + 1) for y in range(bounds[1], bounds[3] + 1)
           if (x, y) not in homes.values()]
    assert schedule(stream, homes, bounds)[0].t0 == 0
    assert schedule(stream, homes, bounds, reserved=[(t, 0.0, 1e6) for t in bus])[0].t0 == 1e6
    assert schedule(stream, homes, bounds, start={"q1": 5e5})[0].t0 == 5e5
