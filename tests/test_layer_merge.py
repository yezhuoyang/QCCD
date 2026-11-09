"""One layer of the machine as one program: disjoint merges and patches holding, on one lattice (docs/FULLSTACK_PLAN.md,
S10's design study).  The claims: a layer measures what its merges measure and keeps every logical at full distance; its
time and its waveforms do not depend on what is in it; every rule and R10 hold at the machine's distance."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from qccd.fullstack.scale import layer as L
from qccd.fullstack.scale import programs as PR
from qccd.qec.experiments import corridor_merge, ideal_stim, layer_merge


def test_one_merge_alone_is_corridor_merge():
    letter, patches, corridor = L.facing(0)
    a, b = corridor_merge(3, letter, patches, corridor), layer_merge(3, [(letter, patches, corridor)])
    assert (a.coords, a.faces, a.detectors, a.observables, a.readout_basis) == (b.coords, b.faces, b.detectors, b.observables, b.readout_basis)
    body = lambda e: [ln for ln in e.qasm.splitlines() if not ln.startswith("//")]
    assert body(a) == body(b)


LAYERS = {
    "two merges": ([L.facing(0), L.facing(1)], []),
    "merges among patches holding": ([L.facing(0), L.facing(2)], [(1, 0), (1, 1), (3, 0)]),
    "both letters": ([L.facing(0), ("X", [((1, 0), "right"), ((2, 0), "left")], set())], [(1, 1), (2, 1)]),
    "a strip across a register": ([("Z", [((x, 1), "top") for x in range(4)], {(x, 0) for x in range(4)})], [(4, 1)]),
}


@pytest.mark.parametrize("name", sorted(LAYERS))
def test_a_layer_measures_its_products_and_keeps_every_logical(name):
    """Noiselessly every product and every patch's own logical are deterministic, and the circuit keeps distance d."""
    groups, idle = LAYERS[name]
    for d in (3, 5):
        e = layer_merge(d, groups, idle)
        c = ideal_stim(e)
        c.detector_error_model()
        assert not c.compile_detector_sampler().sample(32, append_observables=True).any()
        assert len(ideal_stim(e, 1e-3).shortest_graphlike_error(ignore_ungraphlike_errors=True)) == d


def test_regions_of_a_layer_are_disjoint():
    with pytest.raises(ValueError):
        layer_merge(3, [L.facing(0), L.facing(0, 1)])
    with pytest.raises(ValueError):
        layer_merge(3, [L.facing(0)], [(0, 0)])


def test_the_layer_is_the_same_waveforms_whatever_is_in_it():
    one = L.waveforms(L.build([L.facing(0)]))
    assert L.waveforms(L.build([L.facing(x) for x in range(4)])) == one
    assert L.waveforms(L.build([L.facing(0), L.facing(2)], [(1, 0), (1, 1), (3, 0), (3, 1)])) == one
    assert L.waveforms(L.build([("Z", [((x, 1), "top") for x in range(4)], {(x, 0) for x in range(4)})])) == one
    # nothing walks: eight motions, each a step to a junction and on to a neighbour
    moves = {m for kind, *rest in one if kind == "move" for m in rest[0]}
    assert len(moves) == 8 and all(len(m) == 2 for m in moves)


@pytest.mark.parametrize("groups,idle", [
    ([L.facing(0)], []),
    ([L.facing(0), L.facing(2)], [(1, 0), (1, 1), (3, 0), (3, 1)]),
])
def test_a_layer_at_the_machines_distance_holds_every_rule_and_r10(groups, idle, tmp_path):
    got = PR.check(L.build(groups, idle), tmp_path)
    assert got["failed"] == []
    assert got["r10_blocks"] >= 1
    assert got["us"] == 61032.0


def test_r10_a_round_at_a_time_still_catches_a_wrong_pulse_late_on(tmp_path):
    """R10 compares equal rounds once: a pulse a quarter turn off in a late round makes that round a block of its own, and it fails."""
    import dataclasses
    import math

    from test_c2lr_router import _r10_blocks
    b = L.build([L.facing(0)])
    per = L.round_ops(b)
    assert _r10_blocks(b.prog, b.cert, b.arch, b.qasm, tmp_path, per) >= 9
    rs = [k for k, i in enumerate(b.prog.instructions) if i.type == "gate" and i.gate == "R"]
    k = rs[len(rs) * 3 // 4]
    ps = [list(x) for x in b.prog.instructions[k].params]
    ps[0][0] += math.pi / 2
    b.prog.instructions[k] = dataclasses.replace(b.prog.instructions[k], params=tuple(map(tuple, ps)))
    with pytest.raises(AssertionError, match="the tableaux differ"):
        _r10_blocks(b.prog, b.cert, b.arch, b.qasm, tmp_path, per)
