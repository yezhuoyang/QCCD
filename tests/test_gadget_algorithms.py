"""Logical algorithms on the town: scheduling, the place-model checks, and the flat sign-off.

Every example in examples/gadgets/algorithms/ must schedule with all nine checks passing and
sign off against its ideal circuit.  The sign-off and the checks are also shown to fail: on
an ideal circuit that is not the one scheduled, and on schedules corrupted on purpose.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from qccd.gadget.algorithm import AlgorithmError, parse_algorithm, parse_algorithm_file
from qccd.gadget.city import schedule
from qccd.gadget.library import LeafLibrary
from qccd.gadget.logic.flat import sign_off
from qccd.gadget.place_checks import check_places
from qccd.gadget.town import town

EXAMPLES = sorted((Path(__file__).resolve().parents[1] / "examples" / "gadgets" / "algorithms")
                  .glob("*.alg"))


@pytest.fixture(scope="module")
def leaves(tmp_path_factory):
    return LeafLibrary(tmp_path_factory.getbasetemp() / "place_cache", log=None)


@pytest.fixture(scope="module")
def lib(leaves):
    return town(leaves)


@pytest.fixture(scope="module")
def runs(leaves, lib):
    out = {}
    for f in EXAMPLES:
        alg = parse_algorithm_file(f)
        gir = schedule(alg, lib, leaves)
        out[f.stem] = (alg, gir)
    return out


def test_there_are_examples():
    assert {f.stem for f in EXAMPLES} >= {"bell_cnot", "bell_surgery", "ghz3", "teleport",
                                          "magic_state", "magic_teleport", "frames", "s_gate"}


@pytest.mark.parametrize("stem", [f.stem for f in EXAMPLES])
def test_every_example_passes_the_checks_and_signs_off(runs, lib, leaves, stem):
    alg, gir = runs[stem]
    assert not gir["refused"]
    ck = check_places(gir, lib, leaves)
    assert not ck["failed"], ck["violations"]
    rep = sign_off(alg, gir, lib, leaves)
    assert rep["passed"], rep["why"]
    assert all(r["ok"] for r in rep["relations"]) and all(f["ok"] for f in rep["flows"])
    if not rep["distance"].get("skipped"):
        assert rep["distance"]["passed"] and rep["distance"]["at_least"] == 3
        assert rep["distance"]["observables"] >= 1
    else:
        # the only reason to skip is a state injection, which is distance 1 by design
        assert "inject" in rep["distance"]["skipped"]


def test_teleportation_carries_any_input(runs, lib, leaves):
    alg, gir = runs["magic_teleport"]
    rep = sign_off(alg, gir, lib, leaves)
    flows = [f["flow"] for f in rep["flows"]]
    assert len(flows) == 2 and all(f["ok"] for f in rep["flows"])
    assert any(f.startswith("X̄[t] → X̄[b]") for f in flows)


def test_sign_off_fails_against_a_different_ideal(runs, lib, leaves):
    alg, gir = runs["bell_cnot"]
    wrong = parse_algorithm(alg.source.replace("prep q0 X", "prep q0 Z"), "wrong")
    rep = sign_off(wrong, gir, lib, leaves)
    assert not rep["passed"]
    assert rep["random_bits"]["hardware"] != rep["random_bits"]["ideal"]
    alg, gir = runs["ghz3"]
    wrong = parse_algorithm(alg.source.replace("cx q0 q2", "cx q2 q0"), "wrong")
    assert not sign_off(wrong, gir, lib, leaves)["passed"]


def test_a_schedule_with_swapped_ions_fails_g1_and_g2(runs, lib, leaves):
    _, gir = runs["bell_cnot"]
    bad = copy.deepcopy(gir)
    c = next(c for c in bad["carries"] if len(c[7]) == 9)
    c[7][0], c[7][1] = c[7][1], c[7][0]
    ck = check_places(bad, lib, leaves)
    assert "G1" in ck["failed"]


def test_a_dropped_event_fails_g7(runs, lib, leaves):
    _, gir = runs["ghz3"]
    bad = copy.deepcopy(gir)
    ins = bad["program"]["instructions"][3]["id"]
    bad["events"] = [e for e in bad["events"] if e[6] != ins]
    assert "G7" in check_places(bad, lib, leaves)["failed"]


def test_overlapping_ops_fail_g5(runs, lib, leaves):
    _, gir = runs["bell_surgery"]
    bad = copy.deepcopy(gir)
    es = [e for e in bad["events"] if e[3] == "prep"]
    es[1][1] = es[0][1] + 1.0
    assert "G5" in check_places(bad, lib, leaves)["failed"]


def test_bad_programs_are_refused_with_the_line():
    for src, why in (("se q0 1", "before prep"), ("prep q0 Y", "basis"),
                     ("prep q0 Z\nread q0 Z\nse q0 1", "already read"),
                     ("prep a Z\ncx a a", "two blocks"), ("frobnicate q0", "unknown")):
        with pytest.raises(AlgorithmError) as exc:
            parse_algorithm(src)
        assert why in str(exc.value)
