"""The factory of the stationary-tile machine (qccd/fullstack/scale/factory.py, S9 step 4).

* the 15-to-1 as lattice instructions -- teleported T gates, exp(-i pi/4 P) by |Y-bar> when a
  product came out -1, Pauli frames -- accepts every faultless run and leaves exactly T-dagger|+>,
  and rejects a Z fault on any one of its 15 magic states (state vector, outcomes sampled);
* its stream schedules on the lattice and passes the schedule's check.
"""
from __future__ import annotations

from qccd.fullstack.scale import factory as F
from qccd.fullstack.scale.lattice import check, columns, schedule


def test_the_15_to_1_distills_and_catches_every_single_fault():
    got = F.fault_census(trials=60)
    assert got["accepted"] == 60 and abs(got["fidelity"] - 1) < 1e-9 and got["caught"] == 15


def test_the_15_to_1_schedules_on_the_lattice():
    stream, q = F.distill_15to1()
    homes, bounds = columns(q, 5)
    gadgets = schedule(stream, homes, bounds)
    assert check(stream, gadgets) == []
    assert sum(g.kind == "merge" for g in gadgets) == 30
