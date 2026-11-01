"""The factory of the stationary-tile machine (qccd/fullstack/scale/factory.py, S9 step 4).

* the 15-to-1 as lattice instructions -- teleported T gates, exp(-i pi/4 P) by |Y-bar> when a
  product came out -1, Pauli frames -- accepts every faultless run and leaves exactly T-dagger|+>,
  and rejects a Z fault on any one of its 15 magic states (state vector, outcomes sampled);
* its stream schedules on the lattice and passes the schedule's check;
* the |AutoCCZ> stage leaves exactly FormalRV's |AutoCCZ> (its turned sites turned back) and
  rejects any one T fault, on injected T and fed by eight 15-to-1s;
* FormalRV's Toffoli -- its own instructions from the adder, letters swapped on the turned sites
  -- consumes the factory's state as CCX on random data;
* the whole factory schedules as eight translated copies of one 15-to-1 and the stage, clean.
"""
from __future__ import annotations

import random
from collections import Counter

import numpy as np

from qccd.fullstack.scale import factory as F
from qccd.fullstack.scale.lattice import check, columns, schedule

#: FormalRV's Toffoli through one |AutoCCZ> (ppmc.ml `auto_ccx`), as Gidney 2025's first adder
#: call writes it on data a, b (controls) and t (target): the products, the sites' readouts by
#: guard, the Pauli frames
TOFFOLI = [
    ('ppm', ['Z', 'a', 'Z', 'cl0'], 'm1', []), ('ppm', ['Z', 'b', 'Z', 'cl1'], 'm2', []),
    ('ppm', ['X', 't', 'Z', 'cl2'], 'm3', []), ('read', ['cl0', 'X'], 'm4', []),
    ('read', ['cl1', 'X'], 'm5', []), ('read', ['cl2', 'X'], 'm6', []), ('read', ['cl3', 'Z'], 'm7', ['m1']),
    ('read', ['cl3', 'X'], 'm8', ['!m1']), ('read', ['cl4', 'Z'], 'm9', ['m1']),
    ('read', ['cl4', 'X'], 'm10', ['!m1']), ('z', ['b'], None, ['m1', 'm9']),
    ('z', ['b'], None, ['m1', 'm3']), ('x', ['t'], None, ['m1', 'm7']), ('x', ['t'], None, ['m1', 'm2']),
    ('z', ['b'], None, ['!m1', 'm8']), ('x', ['t'], None, ['!m1', 'm10']),
    ('read', ['cl5', 'Z'], 'm11', ['m2']), ('read', ['cl5', 'X'], 'm12', ['!m2']),
    ('read', ['cl6', 'Z'], 'm13', ['m2']), ('read', ['cl6', 'X'], 'm14', ['!m2']),
    ('z', ['a'], None, ['m13', 'm2']), ('z', ['a'], None, ['m2', 'm3']), ('x', ['t'], None, ['m11', 'm2']),
    ('x', ['t'], None, ['m1', 'm2']), ('z', ['a'], None, ['!m2', 'm12']), ('x', ['t'], None, ['!m2', 'm14']),
    ('read', ['cl7', 'Z'], 'm15', ['m3']), ('read', ['cl7', 'X'], 'm16', ['!m3']),
    ('read', ['cl8', 'Z'], 'm17', ['m3']), ('read', ['cl8', 'X'], 'm18', ['!m3']),
    ('z', ['a'], None, ['m17', 'm3']), ('z', ['a'], None, ['m2', 'm3']), ('z', ['b'], None, ['m15', 'm3']),
    ('z', ['b'], None, ['m1', 'm3']), ('z', ['a'], None, ['!m3', 'm16']), ('z', ['b'], None, ['!m3', 'm18']),
    ('x', ['t'], None, ['m1', 'm2']), ('z', ['b'], None, ['m1', 'm3']), ('z', ['a'], None, ['m2', 'm3']),
    ('z', ['a'], None, ['m4']), ('z', ['b'], None, ['m5']), ('x', ['t'], None, ['m6']),
]


def test_the_15_to_1_distills_and_catches_every_single_fault():
    got = F.fault_census(trials=60)
    assert got["accepted"] == 60 and abs(got["fidelity"] - 1) < 1e-9 and got["caught"] == 15


def test_the_15_to_1_schedules_on_the_lattice():
    stream, q = F.distill_15to1()
    homes, bounds = columns(q, 5)
    gadgets = schedule(stream, homes, bounds)
    assert check(stream, gadgets) == []
    assert sum(g.kind == "merge" for g in gadgets) == 30


def test_the_autoccz_stage_makes_formalrvs_state_and_catches_every_single_fault():
    got = F.ccz_census(trials=40)
    assert got["accepted"] == 40 and abs(got["fidelity"] - 1) < 1e-9
    assert got["caught"] == got["mags"] == 8


def test_the_whole_factory_makes_autoccz_from_distilled_t():
    got = F.ccz_census(trials=4, full=True)
    assert got["accepted"] == 4 and abs(got["fidelity"] - 1) < 1e-9


def test_formalrvs_toffoli_consumes_the_factorys_state_as_ccx():
    stream, blocks = F.autoccz()
    consume = F.held(TOFFOLI, [blocks[1 + i] for i in F.TURNED])
    rng = random.Random(5)
    for _ in range(30):
        v = np.array([complex(rng.gauss(0, 1), rng.gauss(0, 1)) for _ in range(8)])
        v /= np.linalg.norm(v)
        sv = F._SV()
        sv.names, sv.psi = ["a", "b", "t"], v.copy()
        sv, out = F.run(stream + consume, rng, sv=sv)
        want = v.copy()
        want[[6, 7]] = want[[7, 6]]
        assert out["cr"] == 0 and sv.names == ["a", "b", "t"]
        assert abs(np.vdot(want, sv.psi)) ** 2 > 1 - 1e-9


def test_the_ccz_factory_schedules_as_copies_of_one_15_to_1():
    stream, gadgets, homes, bounds = F.factory_schedule()
    assert check(stream, gadgets) == []
    n = len(F.distill_15to1()[0])
    units = [Counter(g.shape for g in gadgets if j * n <= g.line < (j + 1) * n) for j in range(8)]
    assert all(u == units[0] for u in units)
    assert max(g.t1 for g in gadgets) < 8e6 and sum(g.kind == "ht" for g in gadgets) == 2
