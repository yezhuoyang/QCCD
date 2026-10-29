"""The CCZ factory (`msccz`, qccd/gadget/leaves/ccz.py): |AutoCCZ> states for Gidney-Ekera's Shor.

The user, 2026-10-29, choosing how GE2021's 4.9 x 10^9 |AutoCCZ> states are made: "New CCZ
factory" -- a verified place that distills CCZ (8T -> CCZ), compiled line by line like the
15-to-1 factory.  What is held here:

* the place builds and its op is verified: the circuit read back off its compiled TSIR,
  post-selected on the five detectors, leaves the nine visitors in exactly the |AutoCCZ> state
  FormalRV's Toffoli consumes; every single T-gate Z error shows in the detectors; the TSIR
  carries out protocols/autoccz.circ operation for operation;
* the state is the right one: FormalRV's own Toffoli lowering (ppmc.ml `auto_ccx`) through it
  is CCX(a, b, t) in all 4,096 branches -- and with the routing pairs' links crossed, in none;
* an `autoccz` instruction runs on a machine with a CCZ factory: nine fresh ions through the
  factory, each output encoded by a pharmacy into its own block, every place and machine
  rule holding.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "out" / "gadgets" / "cache"


@pytest.fixture(scope="module")
def leaves():
    from qccd.gadget.library import LeafLibrary
    return LeafLibrary(str(CACHE), log=None)


def test_the_ccz_factory_is_verified_and_makes_the_state_formalrvs_toffoli_consumes(leaves):
    op = leaves.place("msccz").ops["produce"]
    assert op.status == "verified"
    ck = op.logic["check"]
    assert ck["passed"], ck.get("claim")
    assert ck["accept"] == 1.0 and ck["fidelity"] == 1.0 and ck["output"] == "|AutoCCZ>"
    assert ck["t_gates"] == 8 and ck["detected_1"] == 8          # distance 2: every single fault shows
    assert ck["source_order"]["passed"] and ck["source_order"]["operations"] == 74
    assert ck["consumption"]["passed"] and ck["consumption"]["branches"] == 4096


def test_with_the_routing_links_crossed_formalrvs_toffoli_fails():
    """The check can fail: the same consumption through a state whose pairs are linked the
    other way round is not CCX in any branch."""
    from qccd.gadget.leaves import ccz
    orig = ccz._prepare_autoccz

    def crossed(st, sites):
        x, y, z = sites[:3]
        for q in (x, y, z):
            st.g1(ccz._H, q)
        st.cz(x, y, z)
        for (k1, k2), (su, sv) in (((sites[3], sites[4]), (y, z)), ((sites[5], sites[6]), (x, z)),
                                   ((sites[7], sites[8]), (x, y))):
            st.g1(ccz._H, k1)
            st.g1(ccz._H, k2)
            st.cz(k1, k2)
            st.cx(su, k2)
            st.cx(sv, k1)
    ccz._prepare_autoccz = crossed
    try:
        r = ccz.auto_ccx_check()
    finally:
        ccz._prepare_autoccz = orig
    assert not r["passed"] and r["worst_fidelity"] < 0.5


def test_an_autoccz_instruction_runs_on_a_machine_with_a_ccz_factory(leaves, tmp_path):
    from qccd.fullstack.machine import write_floorplan
    from qccd.fullstack.scale.compile import compile_template
    from qccd.fullstack.scale.program import Template
    fp = tmp_path / "district_ccz.floor"
    write_floorplan(leaves, fp, wide={"memories": 16, "benches": 2, "factories": 2, "ccz": 1, "rotations": 2})
    lines = (["autoccz " + " ".join(f"l{i}" for i in range(9))] + [f"read l{i} X -> o{i}" for i in range(9)]
             + ["decode " + " ".join(f"l{i}" for i in range(9)), "if o0: z p0"])
    t = Template("autoccz_smoke", tuple(lines), tuple([0] * len(lines)), 1, 9, 9, 0)
    c = compile_template(t, fp, leaves)
    assert c["checks"]["failed"] == [], c["checks"]
    assert {"G1", "G5", "G9", "G11", "H1", "H2"} <= set(c["checks"]["passed"])
    g = c["gir"]
    ops = {e[4] for e in g["events"]} | {e[4] for m in g["macros"].values() for e in m["events"]}
    assert "produce" in ops and "inject" in ops
    assert c["span_us"] > 0


def test_the_autoccz_state_is_nine_sites_ccz_and_three_linked_pairs():
    from qccd.gadget.leaves.ccz import autoccz_vector, read_outputs, PROTOCOLS, SOURCE
    v = autoccz_vector()
    assert v.shape == (512,) and abs(np.linalg.norm(v) - 1) < 1e-12
    assert read_outputs(PROTOCOLS / SOURCE) == [1, 2, 4, 8, 9, 10, 11, 12, 13]
