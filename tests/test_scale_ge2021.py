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
* its |AutoCCZ> states are the CCZ factory's (`autoccz`, nine new blocks: qccd/gadget/leaves/
  ccz.py) and its guards on an outcome being 0 are `.alg` guards (`if !m:`);
* its Pauli products over a register (`merge X[a] * PROD i ... . X[data[base + i]]`: the
  masked preparation over n = 2,086 blocks, the unary lookup's over W = 2,048) are `Cat`
  nodes -- lattice surgery by cat states across districts (qccd/gadget/leaves/catbench.py,
  tests/test_gadget_cat.py), one node however many blocks, its factors' bits data -- and are
  counted with every slot taken;
* the constructs still to lower are exactly these, so a new one, or one lowered, shows:
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
    assert 1e9 < c["autoccz"] < 1e10, c["autoccz"]        # |AutoCCZ> states: the paper's 2.7e9 Toffolis
    assert c["ppm"] > 1e10 and c["se"] > 1e11
    cats = {key[0]: n for key, body in prog.bodies.items() for n in body if isinstance(n, P.Cat)}
    assert set(cats) == {"cPrepMasked", "unaryQROMSegCoh"}, sorted(cats)
    assert all(n.rounds == 27 and n.letter == "X" for n in cats.values())
    assert c["cat"] > 1e8 and c["cat_blocks"] > 2000 * c["cat"]   # register-wide, every slot taken
    assert set(survey) == {"parity", "source", "guard on a source record",
                           "record of another call"}, sorted(survey)


def test_the_masked_preparation_through_cat_states_prepares_the_constant(tmp_path):
    """GE2021's own `cPrepMasked` (a scratch copy of FormalRV's gadget, n = 4), its register
    product a `Cat` node and its ancilla a prepared block, expanded and run in the ideal
    stabilizer simulator: for every constant and both control values, the register reads the
    constant when the control is 1 and zeros when it is 0."""
    import shutil
    from qccd.fullstack import formalrv
    from qccd.gadget.algorithm import parse_algorithm
    from qccd.gadget.logic.symbolic import expr_text, run
    _ge2021()
    src = formalrv.root() / "FormalRV" / "Audit" / "GidneyEkera2021" / "GE2021.qec"
    d = tmp_path / "masked.qec"
    d.mkdir()
    for f in ("GE2021.qec", "cPrepMasked.qec"):
        shutil.copy(src / f, d / f)
    (d / "main.qec").write_text(
        "program[LogicData: n + 1](Surface[n + 1] LogicData, Surface[64] LogicalAncilla):\n"
        "  cPrepMasked(0, 1, cst, n, 0, 0, 0, 0, 0, LogicData, LogicalAncilla)\n", encoding="utf-8")
    (d / "sources").write_text("GE2021.qec\ncPrepMasked.qec\nmain.qec\n", encoding="utf-8")
    st = layers.read(d, "qec")
    n = 4
    for cst in range(2 ** n):
        prog = P.Builder(st).build({"n": n, "cst": cst})
        assert any(isinstance(x, P.Cat) for b in prog.bodies.values() for x in b)
        for ctrl in (0, 1):
            lines = [(f"if {' & '.join(g)}: " if g else "") + f"{w} {' '.join(a)}" + (f" -> {o}" if o else "")
                     for w, a, o, g in P.expand(prog, data_ones=(0,) if ctrl else ())]
            lines += [f"read q{i} Z -> r{i}" for i in range(n + 1)]
            out = run(parse_algorithm("\n".join(lines) + "\n"))
            got = {k: expr_text(v) for k, v in out.items() if k.startswith("r")}
            want = {"r0": str(ctrl), **{f"r{i + 1}": str((cst >> i) & 1 if ctrl else 0) for i in range(n)}}
            assert got == want, (cst, ctrl, got)
