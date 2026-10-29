"""Shor at scale, the advanced algorithm: FormalRV's Gidney-Ekera 2021 audit at RSA-2048.

The user, 2026-10-29: "We definitely need to support advanced Shor's algorithm and
parallelism."  FormalRV writes Gidney and Ekera's windowed, coset-encoded, carry-runway Shor
for RSA-2048 (FormalRV/Audit/GidneyEkera2021, GE2021.qec at distance 27, inputs in
Resource/Inputs/RuntimeGap/execution.inputs).  The symbolic program lowers it as it lowers the
textbook Shor -- loops stay loops, a data decision is a slot, a recursive gadget (the unary
lookup) is lowered once per depth -- in SURVEY mode: what QCCD's machine cannot do yet is
recorded by kind and stands as a placeholder.  What is held here:

* it builds, the size of its gadgets (tens of templates, tens of thousands of nodes), and its
  run is counted in closed form: the CCZ states it requests are the order of the paper's
  Toffoli count (2.7 x 10^9; ours counts every data slot as taken, the schedule's view);
* the constructs still to lower are exactly these, so a new one, or one lowered, shows:
  a Pauli product over a register (lattice surgery across many districts), |AutoCCZ> states,
  classical parities and guards on them, a record copied into a source slot (`source c[k] <-`)
  and guards on one, and records another call wrote.
"""
from __future__ import annotations

import sys
import threading

import pytest

from qccd.fullstack import layers
from qccd.fullstack.scale import program as P
from qccd.fullstack.scale.inputs import read_inputs


def _ge2021():
    from qccd.fullstack import formalrv
    try:
        formalrv.check_pin()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"the pinned FormalRV clone is needed: {e}")
    aud = formalrv.root() / "FormalRV" / "Audit" / "GidneyEkera2021"
    return (layers.read(aud / "GE2021.qec", "qec"),
            read_inputs(aud / "Resource" / "Inputs" / "RuntimeGap" / "execution.inputs"))


def _in_thread(fn):
    """(the lowering recurses through the unary lookup: a thread with a deep stack)"""
    out = {}

    def run():
        try:
            out["v"] = fn()
        except BaseException as e:  # noqa: BLE001
            out["e"] = e
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(100_000)
    threading.stack_size(256 * 1024 * 1024 - 4096)
    try:
        t = threading.Thread(target=run)
        t.start()
        t.join()
    finally:
        threading.stack_size(0)
        sys.setrecursionlimit(old)
    if "e" in out:
        raise out["e"]
    return out["v"]


def test_gidney_ekera_rsa2048_is_lowered_symbolically_and_counted():
    st, params = _ge2021()
    assert params["bits"] == 2048 and params["N"].bit_length() == 2048

    def go():
        b = P.Builder(st)
        b.survey = {}
        prog = b.build({k: v for k, v in params.items() if isinstance(v, (int, list, tuple))})
        return b.survey, prog, P.count(prog)
    survey, prog, c = _in_thread(go)
    size = prog.size()
    assert size["templates"] < 40 and size["nodes"] < 50_000, size
    assert 1e9 < c["inject"] < 1e10, c["inject"]          # CCZ requests: the paper's 2.7e9 Toffolis
    assert c["ppm"] > 1e10 and c["se"] > 1e11
    assert set(survey) == {"merge * PROD", "magic |AutoCCZ>", "parity", "when ¬", "source",
                           "guard on a source record", "record of another call"}, sorted(survey)
