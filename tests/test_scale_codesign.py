"""Codesign rewrites of compiled-once templates (qccd/fullstack/scale/codesign.py).

The user, 2026-10-29: "this is a codesign system ... optimize the bottleneck first", and "keep
25 [rounds] to stay paper faithful".  Every rewrite here replaces a merge by operations that
need none, and is a theorem, proved by the exact simulator.  What is held here:

* a Pauli product of any letters over one to three blocks is the ancilla measurement that
  replaces it (one transversal CNOT a factor; `ht` on the ancilla for a mixed one);
* FormalRV's lattice-surgery CNOT, to one target or two, is `cx`;
* FormalRV's H̄, three |Ȳ⟩ merges S̄ √X̄ S̄, is `ht`;
* on Gidney 2025 at RSA-2048 the rewrites find those idioms in the carry chain, and the
  uncompute template -- no magic state, so the simulator runs it whole -- is proved equal to
  its rewrite, every branch;
* a rewrite never drops an outcome a call site hands on;
* fusing the lookups' rows keeps the number of CNOTs exactly.
"""
from __future__ import annotations

import pytest

from qccd.fullstack.scale import codesign as C
from qccd.fullstack.scale.program import Template


def _t(lines, n_params, n_locals=0, n_out=0):
    return Template("t", tuple(lines), (0,) * len(lines), n_params, n_locals, n_out, 0)


def test_every_product_of_up_to_three_blocks_is_its_ancilla_measurement():
    assert C.products_proved(sizes=(1, 2, 3))


@pytest.mark.parametrize("targets", [1, 2])
def test_formalrvs_lattice_surgery_cnot_is_a_transversal_cnot(targets):
    ts = [f"p{k + 1}" for k in range(targets)]
    lines = ["prep l0 X", "ppm Z p0 Z l0 -> o0", "se p0 3", "se l0 3", "decode p0 l0",
             "ppm X l0 " + " ".join(f"X {t}" for t in ts) + " -> o1", "decode l0 " + " ".join(ts),
             "read l0 Z -> o2", "decode l0", "if o1: z p0"] + \
            [f"if o0: x {t}" for t in ts] + [f"if o2: x {t}" for t in ts]
    t = _t(lines, 1 + targets, 1, 3)
    new, n = C.rewrite(t)
    assert n == {"cx": 1}
    assert new.lines == tuple(f"cx p0 {x}" for x in ts)
    assert C.equal_drop(t, new)


def test_formalrvs_hadamard_is_one_bay_visit():
    def merge(L, letter, o):
        a = letter.lower()
        return [f"prep {L} Y", f"ppm {letter} p0 Z {L} -> o{o}", "se p0 3", f"decode p0 {L}",
                f"read {L} X -> o{o + 1}", f"decode {L}", f"if o{o}: {a} p0", f"if o{o + 1}: {a} p0"]
    t = _t(merge("l0", "Z", 0) + merge("l1", "X", 2) + merge("l2", "Z", 4), 1, 3, 6)
    new, n = C.rewrite(t)
    assert n == {"ht": 1} and new.lines == ("ht p0",)
    assert C.equal_drop(t, new)


def test_an_outcome_a_call_site_hands_on_is_never_dropped():
    lines = ["prep l0 X", "ppm Z p0 Z l0 -> o0", "ppm X l0 X p1 -> o1", "read l0 Z -> o2",
             "if o1: z p0", "if o0: x p1", "if o2: x p1"]
    t = _t(lines, 2, 1, 3)
    assert C.rewrite(t)[1] == {"cx": 1}
    new, n = C.rewrite(t, exported={"o1"})
    assert "cx" not in n                      # its merges are measured one by one instead
    assert C.equal_drop(t, new)


def _gidney2025():
    """Gidney 2025 at RSA-2048 on the paper machine, lowered (in a thread with a deep stack)."""
    import sys
    import threading
    from collections import Counter
    from qccd.fullstack import formalrv, layers
    from qccd.fullstack.scale import program as P
    from qccd.fullstack.scale.inputs import read_inputs
    try:
        formalrv.check_pin()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"the pinned FormalRV clone is needed: {e}")
    aud = formalrv.root() / "FormalRV" / "Audit" / "Gidney2025"
    params = {}
    for f in ("Instance/rsa2048.inputs", "PaperMachine_q1M/execution.inputs"):
        params.update(read_inputs(aud / "Resource" / "Inputs" / f))
    spec = (aud / "Resource" / "Inputs" / "PaperMachine_q1M" / "spec.args").read_text(encoding="utf-8").split()
    for v in spec[1::2]:
        name, val = v.split("=")
        params[name] = int(val)
    st = layers.read(aud / "Gidney2025.qec", "qec")
    out = {}

    def go():
        prog = P.Builder(st).build({k: v for k, v in params.items() if isinstance(v, (int, list, tuple))})
        out["v"] = prog, P.total(prog, lambda tid: Counter({tid: 1}), lambda *a: Counter())
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(100_000)
    threading.stack_size(256 * 1024 * 1024 - 4096)
    try:
        th = threading.Thread(target=go)
        th.start()
        th.join()
    finally:
        threading.stack_size(0)
        sys.setrecursionlimit(old)
    return out["v"]


@pytest.fixture(scope="module")
def gidney():
    return _gidney2025()


def _seg_of(prog, gadget):
    segs = [s for key, body in prog.bodies.items() if key[0] == gadget for s in C._segs(body)]
    return max(segs, key=lambda s: len(prog.templates[s.tid].lines))


def test_the_carry_chain_is_rewritten_and_the_uncompute_proved_whole(gidney):
    prog, inst = gidney
    exp = C.exports(prog)
    interior = prog.templates[_seg_of(prog, "gidneyInterior").tid]
    new, n = C.rewrite(interior, exp.get(interior.tid, set()))
    assert n["cx"] == 2 and n["product"] >= 3, n
    uncompute = prog.templates[_seg_of(prog, "gidneyMeasInteriorRev").tid]
    new, n = C.rewrite(uncompute, exp.get(uncompute.tid, set()))
    assert n["cx"] == 2 and n["ht"] == 1, n
    assert C.equal_drop(uncompute, new)


def test_fusing_the_lookups_keeps_every_cnot():
    from collections import Counter
    from qccd.fullstack.scale import program as P
    prog, before = _gidney2025()                     # its own: fusing rewrites the program
    lookups = [s for key, body in prog.bodies.items() if key[0] == "gLoop1" for s in C._segs(body)]
    cx = next(s.tid for s in lookups if prog.templates[s.tid].n_params == 2 and
              C.rewrite(prog.templates[s.tid])[1] == {"cx": 1})
    info = C.fuse_rows(prog, cx, 16)
    after = P.total(prog, lambda tid: Counter({tid: 1}), lambda *a: Counter())
    assert info["rows"] >= 2 and 16 in info["fans"]
    assert before[cx] == after[cx] + sum(k * after[t] for k, t in info["fans"].items())
