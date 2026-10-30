"""Shor at scale, the most advanced audit: FormalRV's Gidney 2025 at RSA-2048.

The user, 2026-10-29: "I don't understand why you choose textbook Shor, there are more advanced
Shor."  FormalRV audits Gidney's *How to factor 2048 bit RSA integers with less than a million
noisy qubits* (arXiv:2505.15917) -- residue arithmetic, measurement-based cleanup, yoked cold
storage -- as FormalRV/Audit/Gidney2025: a generic `Gidney2025.qec` (surface code, d = 25)
whose parameters the paper machine fixes (Resource/Inputs/PaperMachine_q1M: execution.inputs,
the instance's rsa2048.inputs, and spec.args).  What is held here:

* it lowers completely, nothing left in survey mode: 20 templates, about a hundred gadget instances,
  a few hundred nodes, in about a second;
* its gadgets read the program's parameters without being passed them (FormalRV's are global);
* its lookups are calls and loops under a guard on an outcome: `When` bodies, reserved either
  way, every instruction of them carrying the guard;
* counted with every data slot taken, it asks for about 1.1 x 10^9 |AutoCCZ> states -- a
  quarter of Gidney-Ekera 2021's (tests/test_scale_ge2021.py);
* a decision on a loop variable (`if i ≥ 1: interior(i) else: first`) is structure, taken where
  it holds and lowered there -- not a data slot (fixed 2026-10-29: the adder's first step had
  been charged on every step, and the interior's previous carry merged with its own).
"""
from __future__ import annotations

import sys
import threading

import pytest

from qccd.fullstack import layers
from qccd.fullstack.scale import program as P
from qccd.fullstack.scale.inputs import read_inputs


def _gidney2025():
    from qccd.fullstack import formalrv
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
    return layers.read(aud / "Gidney2025.qec", "qec"), params


def _in_thread(fn):
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


def test_gidney2025_rsa2048_lowers_completely_and_is_counted():
    st, params = _gidney2025()
    assert params["top"] == 1695 and params["modN"].bit_length() == 2048

    def go():
        b = P.Builder(st)
        b.survey = {}
        prog = b.build({k: v for k, v in params.items() if isinstance(v, (int, list, tuple))})
        return b.survey, prog, P.count(prog)
    survey, prog, c = _in_thread(go)
    assert survey == {}, sorted(survey)
    size = prog.size()
    assert size["templates"] <= 25 and size["gadget_instances"] < 200 and size["nodes"] < 1000, size
    whens = [n for b in prog.bodies.values() for n in b if isinstance(n, P.When)]
    assert whens and all(len(w.cond.terms) == 1 for w in whens)
    assert 5e8 < c["autoccz"] < 2e9, c["autoccz"]
    assert c["ppm"] > 1e10 and c["se"] > 1e11


def test_a_decision_on_a_loop_variable_is_structure_not_data():
    """`for 0 <= i < nSeg: if i ≥ 1: gidneyInterior(i) else: gidneyFirst` (gidneyForwardProp,
    and gidneyMeasReverse the same way back).  The guard reads only the loop variable, so it is
    taken where it holds: the first step once an adder, not once a step.  And the interior is
    lowered where `i ≥ 1` holds, as its own `requires 1 ≤ i` says -- lowered at i = 0, the
    previous carry `(i - 1)*3 + 2` and `i*3 + 2` were one block (both 2, FormalRV's minus
    saturating), and its first CNOT went from a block into itself."""
    from collections import Counter
    st, params = _gidney2025()

    def go():
        prog = P.Builder(st).build({k: v for k, v in params.items() if isinstance(v, (int, list, tuple))})
        return prog, P.total(prog, lambda tid: Counter({tid: 1}), lambda *a: Counter())
    prog, inst = _in_thread(go)

    def segs(gadget):
        out = []

        def walk(body):
            for n in body:
                if isinstance(n, P.Seg):
                    out.append(n)
                elif isinstance(n, (P.Pred, P.When, P.Let, P.Loop)):
                    walk(n.body)
        for key, body in prog.bodies.items():
            if key[0] == gadget:
                walk(body)
        return out
    for interior, first in (("gidneyInterior", "gidneyFirst"), ("gidneyMeasInteriorRev", "gidneyMeasFirstRev")):
        big = max(segs(interior), key=lambda s: len(s.params))
        texts = {e.text for _, e in big.params}
        assert {"rv_database + (i - 1)*3 + 2", "rv_database + i*3 + 2"} <= texts, (interior, texts)
        one = max(segs(first), key=lambda s: len(s.params))
        assert 10 * inst[one.tid] < inst[big.tid], (interior, inst[one.tid], inst[big.tid])
    structural = [p for b in prog.bodies.values() for n in b if isinstance(n, P.Loop)
                  for p in n.body if isinstance(p, P.Pred) and p.structural]
    assert structural and all(not P._reads_table(p.cond.node) for p in structural)


def test_a_source_slot_written_and_read_in_one_run_reads_the_bit_just_written():
    """Gidney's measured uncompute: `source c[k] <- c[523,525)` and then `when source c[k]:` --
    the fix-up branches on the outcome this step just measured.  Read as the slot's value
    before the step (fixed 2026-10-30), the branch read another step's bit, or none: the
    expansion had no writer for it, and each template took it as decided."""
    from collections import Counter
    st, params = _gidney2025()

    def go():
        prog = P.Builder(st).build({k: v for k, v in params.items() if isinstance(v, (int, list, tuple))})
        P.total(prog, lambda tid: Counter({tid: 1}), lambda *a: Counter())
        return prog
    prog = _in_thread(go)
    for gadget in ("gidneyMeasInteriorRev", "gidneyMeasFirstRev"):
        segs = [n for key, b in prog.bodies.items() if key[0] == gadget for n in b if isinstance(n, P.Seg)]
        assert segs, gadget
        for s in segs:
            assert not s.ext, (gadget, s.ext)
            (_, _, _, written), = [w for w in s.writes if w[0] == "src"]
            t = prog.templates[s.tid]
            guarded = [ln for ln in t.lines if ln.startswith(f"if {written}: ")]
            assert any(" read " in f" {ln} " for ln in guarded), (gadget, written)
