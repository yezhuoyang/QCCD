"""The verified place library (qccd/gadget/leaves/places.py), checked as hardware and logic.

The places are built and characterized once for the module (about thirty seconds) into a
cache under pytest's base temp, which `test_gadget_algorithms.py` reads back.  Besides
"everything passes", every kind of logic check is shown to fail on a program corrupted in
the way it exists to catch.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from qccd.gadget.characterize import logic_report
from qccd.gadget.leaves.bay import BayProgram
from qccd.gadget.leaves.places import PLACES
from qccd.gadget.library import LeafLibrary
from qccd.gadget.logic.circuit import from_tsir
from qccd.gadget.surface import Check, patch

try:
    import stim
except ImportError:
    stim = None


@pytest.fixture(scope="module")
def leaves(tmp_path_factory):
    return LeafLibrary(tmp_path_factory.getbasetemp() / "place_cache", log=None)


@pytest.fixture(scope="module")
def places(leaves):
    return leaves.places()


def test_every_place_op_passes_hardware_and_logic(places):
    from qccd.gadget.leaves.classical import CLASSICAL
    from qccd.gadget.place_checks import CLASSICAL_FAMILIES
    assert set(places) == set(PLACES) | set(CLASSICAL)
    bad = []
    for name, m in places.items():
        classical = m.family in CLASSICAL_FAMILIES
        for op in m.ops.values():
            status = (op.logic or {}).get("status")
            # a classical place has no TSIR to replay: its latency is modeled, and its
            # function is checked where there is one to check (the decoder's table)
            want = ("verified",) + (("modeled",) if classical else ())
            if op.status not in want or status == "failed":
                bad.append((name, op.name, op.rules["failed"], (op.logic or {}).get("why")))
            if m.family not in ("depot",) + CLASSICAL_FAMILIES:
                assert status == "verified", (name, op.name)
    assert not bad


def test_every_spec_is_complete(places):
    for name, m in places.items():
        for op in m.ops.values():
            fl = (op.logic or {}).get("flows")
            if fl:
                assert fl["complete"] and fl["rank"] == fl["needed"], (name, op.name)


def test_fault_distance_is_three_except_where_it_cannot_be(places):
    seen = {}
    for name, m in places.items():
        for op in m.ops.values():
            d = (op.logic or {}).get("distance")
            if d is None:
                continue
            seen[(name, op.name)] = d
            if d["expect"] == "ft":
                assert d["passed"] and d["at_least"] == 3, (name, op.name, d.get("witness"))
    assert seen[("inj_d3", "inject")]["distance"] == 1          # injection, by design
    assert {k[0] for k in seen} >= {"se_d3", "prep_d3", "read_d3", "tcx_d3", "ls_d3", "inj_d3"}


def test_lattice_surgery_measures_the_logical_product(places):
    for kind, letter in (("zz", "Z"), ("xx", "X")):
        flows = {f["name"]: f for f in places["ls_d3"].ops[kind].logic["flows"]["flows"]}
        measured = flows[f"{letter}̄_A{letter}̄_B measured"]
        assert measured["ok"] and measured["records"]
        other = "X" if letter == "Z" else "Z"
        assert flows[f"{other}̄_A{other}̄_B kept"]["ok"]


def test_the_factory_distills(places):
    ck = places["msf15"].ops["produce"].logic["check"]
    assert ck["passed"] and ck["accept"] == 1 and ck["output"] == "T†|+⟩"
    assert ck["detected_1"] == ck["singles"] == 15 and ck["detected_2"] == ck["pairs"] == 105
    assert ck["undetected_3"] == ck["logical_3"] == 35


def test_rings_bring_their_residents_home_and_ions_stay_cool(places):
    from qccd.gadget.checks import ION_LOSS_QUANTA
    from qccd.gadget.place_checks import CLASSICAL_FAMILIES
    for name, m in places.items():
        if m.family in CLASSICAL_FAMILIES:
            continue                      # no ions there: nothing to heat
        for op in m.ops.values():
            assert op.metrics["peak_quanta"] < ION_LOSS_QUANTA * 0.5, (name, op.name)


# ----------------------------------------------------------------------------- mutations


def _mutate(prog, fn):
    return replace(prog, instructions=[fn(ins) for ins in prog.instructions])


def test_a_cnot_turned_around_fails_the_logic_check(leaves):
    leaves.place("se_d3")
    build = leaves.builds.get("se_d3")
    if build is None:
        build = PLACES["se_d3"]()
    prog = build.programs["se.1"]
    done = []

    def flip(ins):
        if ins.type == "gate" and ins.gate == "CX" and not done:
            done.append(ins.id)
            pairs = (tuple(reversed(ins.pairs[0])),) + tuple(ins.pairs[1:])
            return replace(ins, pairs=pairs)
        return ins

    rep = logic_report(build, "se.1", _mutate(prog, flip))
    assert rep["status"] == "failed" and not rep["flows"]["passed"]


def test_a_dropped_hadamard_fails_the_logic_check(leaves):
    leaves.place("se_d3")
    build = leaves.builds.get("se_d3") or PLACES["se_d3"]()
    prog = build.programs["se.1"]
    first = next(i for i in prog.instructions if i.type == "gate" and i.gate == "H")
    rep = logic_report(build, "se.1", replace(prog, instructions=[i for i in prog.instructions
                                                                 if i is not first]))
    assert rep["status"] == "failed"


def test_a_hook_unsafe_schedule_computes_right_but_loses_distance():
    build = PLACES["se_d3"]()
    p = build.extra["patch"]
    bad = patch(3, 3, name=p.name)
    checks = []
    for ch in bad.checks:
        if ch.basis == "X" and ch.weight == 4:
            cm = dict(ch.corners)
            checks.append(Check(ch.name, ch.basis, ch.i, ch.j,
                                tuple((k, cm[k]) for k in ("NW", "SW", "NE", "SE")), (0, 1, 2, 3)))
        else:
            checks.append(ch)
    bad.checks = checks
    from qccd.gadget.leaves.bay import bay
    b = bay("se_d3", 12, [1 + 3 * i for i in range(8)], {"in": "TL", "out": "TR"},
            {"in": 9, "out": 9})
    data = [f"d{q}" for q in range(9)]
    anc = {c.name: f"a{c.name}" for c in p.checks}
    ad, ds = build.extra["anc_docks"], build.extra["data_slots"]
    home = {anc[c]: f"A{ad[c]}" for c in anc}
    bp = BayProgram(b, "se_d3.bad", {**home, **{d: "X_in" for d in data}})
    bp.absorb("in", data, [ds[q] for q in range(9)])
    bp.restore()
    bp.conveyor_round(bad, dict(enumerate(data)), anc, ad)
    bp.restore()
    bp.emit("out", data)
    rep = logic_report(build, "se.1", bp.prog)
    assert rep["flows"]["passed"]                        # the right stabilizers ...
    assert not rep["distance"]["passed"] and rep["distance"]["distance"] == 2   # ... not FT
    assert rep["status"] == "failed"


@pytest.mark.skipif(stim is None, reason="stim not installed")
def test_place_distances_agree_with_stim(leaves):
    from qccd.gadget.logic.experiment import build_experiment, to_stim_experiment
    for name in ("se_d3", "tcx_d3", "inj_d3", "ls_d3"):
        leaves.place(name)
        build = leaves.builds.get(name) or PLACES[name]()
        for key, exp in build.experiments.items():
            kw = {k: v for k, v in exp.items() if k != "expect"}
            e = build_experiment(from_tsir(build.programs[key]), **kw)
            found = stim.Circuit(to_stim_experiment(e)).search_for_undetectable_logical_errors(
                dont_explore_detection_event_sets_with_size_above=6,
                dont_explore_edges_with_degree_above=6,
                dont_explore_edges_increasing_symptom_degree=False,
                canonicalize_circuit_errors=True)
            want = 1 if name == "inj_d3" else 3
            assert len(found) == want, (name, key, len(found))
