"""Shor at scale, layer four: the run's time when independent work runs at once
(qccd/fullstack/scale/parallel.py).

The user, 2026-10-29: "Can we add parallelism???"  The composed time ran every gadget after the
one before, on one district.  Here every template instance starts as soon as the blocks it
touches are free and the outcomes it needs are recorded, each on a district of its own, its
compiled schedule shifted whole (its PORTS: when it first and last touches each block, when it
records each outcome).  What is held here:

* the dataflow is exactly the one its definition says, on programs small enough to work out
  by hand: a chain whose steps share a block is a pipeline (each starts when the one before
  lets go of the block), steps on disjoint blocks all start at once, a register every step
  writes serializes them;
* a long loop is run until each block and record it writes moves by its own stride and each
  time by its own slope, then written in closed form -- and the closed form is EXACTLY what
  running every iteration gives (whole nanoseconds), checked in the middle and at the end;
* a closed form the middle check refutes is undone and the loop run for real: a chain whose
  blocks become ready at quadratic times looks affine for its first iterations, and the result
  is still exactly full iteration's;
* on FormalRV's textbook Shor, compiled once with its ports, the parallel time never exceeds
  the sequential one, and with extrapolation off it is the same to the nanosecond.
"""
from __future__ import annotations

import pytest

from qccd.fullstack.scale import parallel as PAR
from qccd.fullstack.scale import program as P
from qccd.fullstack.scale.expr import Expr


def _program(n: int, idx0: str, idx1: str) -> P.Program:
    t = P.Template("tstep", ("ppm Z p0 Z p1 -> o0",), (0,), 2, 0, 1, 0)
    seg = P.Seg("tstep", (("LogicData", Expr(idx0)), ("LogicData", Expr(idx1))), (), ())
    loop = P.Loop("k", Expr("0"), Expr(str(n)), P.Body([seg]))
    return P.Program(templates={"tstep": t}, root=P.Body([loop]))


#: block 0 touched from 0 to 10 us, block 1 from 5 to 30 us; the step takes 40 us
PORTS = {"tstep": {"first": [0.0, 5.0], "last": [10.0, 30.0], "out": [20.0], "need": {}, "span": 40.0}}


@pytest.mark.parametrize("n", [5, 200])
def test_a_chain_sharing_a_block_is_a_pipeline(n):
    # step k touches blocks k and k+1: it starts when step k-1 lets go of block k (its block 1,
    # free 30 us after that step's start), so step k starts at 30k
    got = PAR.parallel_us(_program(n, "k", "k + 1"), PORTS)
    assert got["us"] == 30 * (n - 1) + 40
    assert got["busy"] == pytest.approx(40 * n / (30 * (n - 1) + 40))


@pytest.mark.parametrize("n", [5, 200])
def test_steps_on_disjoint_blocks_run_at_once(n):
    got = PAR.parallel_us(_program(n, "2*k", "2*k + 1"), PORTS)
    assert got["us"] == 40 and got["busy"] == n


def test_a_block_every_step_writes_serializes_them():
    # block 0 in every step (as its block 1): each waits until the last lets go of it at 30 us
    got = PAR.parallel_us(_program(200, "k + 1", "0"), PORTS)
    assert got["us"] == 25 * 199 + 40          # step k starts when block 0 frees, minus its first touch (5)


@pytest.mark.parametrize("idx", [("k", "k + 1"), ("2*k", "2*k + 1"), ("k + 1", "0")])
def test_the_closed_form_is_exactly_full_iteration(idx, monkeypatch):
    got = PAR.parallel_us(_program(500, *idx), PORTS)
    assert got["extrapolated"] > 400 and got["checked"] == 3
    monkeypatch.setattr(PAR, "CAP", 10 ** 9)
    full = PAR.parallel_us(_program(500, *idx), PORTS)
    assert full["extrapolated"] == 0 and full["ns"] == got["ns"]


def test_a_refuted_closed_form_is_undone_and_the_loop_run_for_real(monkeypatch):
    # loop A: for k, for i < k, a step on blocks 50 (shared: serializes them all) and 100 + k,
    # so block 100 + k is ready at 10 * k(k+1)/2 us -- quadratic.  Loop C: a chain on block 300
    # (which is also loop A's block 100 + 200, ready at 201 ms), 1000 us a step, each also
    # reading block 100 + k: 201 ms + 1000k, affine past the middle of the loop, until the
    # quadratic readiness takes over near k = 300 -- found only by the check before last
    ta = P.Template("ta", ("ppm Z p0 Z p1 -> o0",), (0,), 2, 0, 1, 0)
    tc = P.Template("tc", ("ppm Z p0 Z p1 -> o0",), (0,), 2, 0, 1, 0)
    inner = P.Loop("i", Expr("0"), Expr("k"), P.Body([P.Seg("ta", (("LogicData", Expr("50")),
                                                                    ("LogicData", Expr("100 + k"))), (), ())]))
    loop_a = P.Loop("k", Expr("0"), Expr("400"), P.Body([inner]))
    loop_c = P.Loop("k", Expr("0"), Expr("400"), P.Body([P.Seg("tc", (("LogicData", Expr("300")),
                                                                       ("LogicData", Expr("100 + k"))), (), ())]))
    ports = {"ta": {"first": [0.0, 0.0], "last": [10.0, 10.0], "out": [10.0], "need": {}, "span": 10.0},
             "tc": {"first": [0.0, 0.0], "last": [1000.0, 1000.0], "out": [10.0], "need": {}, "span": 1000.0}}

    def prog():
        return P.Program(templates={"ta": ta, "tc": tc}, root=P.Body([loop_a, loop_c]))
    got = PAR.parallel_us(prog(), ports)
    assert got.get("refuted", 0) >= 1
    monkeypatch.setattr(PAR, "CAP", 10 ** 9)
    assert PAR.parallel_us(prog(), ports)["ns"] == got["ns"]


def _head(kclass: str, sign: int, nf: int) -> P.Template:
    """One level of the repeat-until-success rotation (program._Exp.rotation)."""
    res = {"Y": ["prep l0 Y"] + (["z l0"] if sign < 0 else []),
           "T": ["inject l0"] + (["x l0"] if sign > 0 else []),
           "R": ["prep l0 R5" + ("" if sign > 0 else "dg")]}[kclass]
    ps = [f"p{i}" for i in range(nf)]
    lines = res + [f"ppm {' '.join('Z ' + q for q in ps)} Z l0 -> o0", f"decode {' '.join(ps)} l0",
                   "read l0 X -> o1", "decode l0"] + [f"if o1: z {q}" for q in ps]
    return P.Template(f"rot_{kclass}_{'p' if sign > 0 else 'm'}{nf}", tuple(lines), tuple([0] * len(lines)),
                      nf, 1, 2, 0)


def test_textbook_shor_never_slower_in_parallel_and_exact(tmp_path):
    """FormalRV's textbook Shor at 3 bits, its templates compiled once with their ports."""
    from pathlib import Path

    from qccd.fullstack import formalrv, layers
    from qccd.fullstack.scale.compile import compile_template
    from qccd.gadget.library import LeafLibrary
    try:
        formalrv.check_pin()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"the pinned FormalRV clone is needed: {e}")
    repo = Path(__file__).resolve().parents[1]
    st = layers.read(formalrv.root() / "FormalRV" / "Examples" / "Shor" / "Shor.qec", "qec")
    prog = P.build(st, params=dict(m=4, bits=3, N=3, a=2, ainv=2, rec=19))
    leaves = LeafLibrary(str(repo / "out" / "gadgets" / "cache"), log=None)
    fp = repo / "examples" / "gadgets" / "floorplans" / "fullstack_wide.floor"
    ports = {}
    for t in prog.templates.values():
        if t.lines:
            c = compile_template(t, fp, leaves, check=False)
            ports[t.tid] = c["ports"]
    heads = {}
    for kc in "YTR":
        for sign in (1, -1):
            for nf in (1, 2):
                h = _head(kc, sign, nf)
                heads[h.tid] = compile_template(h, fp, leaves, check=False)["span_us"]

    def rot_us(k, sign, nf):
        sg = "p" if sign > 0 else "m"
        h = lambda kc: heads[f"rot_{kc}_{sg}{nf}"]  # noqa: E731
        return 0.0 if k <= 1 else h("Y") + (h("T") if k >= 3 else 0) + max(0, k - 3) * h("R")
    seq = P.duration_ns(prog, {t: round(p["span"] * 1000) for t, p in ports.items()},
                        lambda *a: round(rot_us(*a) * 1000))
    par = PAR.parallel_us(prog, ports, rot_us)
    assert par["ns"] <= seq
    PAR_CAP = PAR.CAP
    try:
        PAR.CAP = 10 ** 9
        assert PAR.parallel_us(P.build(st, params=dict(m=4, bits=3, N=3, a=2, ainv=2, rec=19)),
                               ports, rot_us)["ns"] == par["ns"]
    finally:
        PAR.CAP = PAR_CAP


def test_a_gadget_calls_ancillas_are_forgotten_when_it_returns():
    """A logical ancilla is local to the gadget call that names it (FormalRV numbers ancillas per
    gadget), so the run keeps nothing of it once the call returns: a loop of calls holds no
    more ancillas than one call does (Gidney 2025 makes ~10^10 of them)."""
    t = P.Template("tanc", ("prep l0 Z", "ppm Z p0 Z l0 -> o0", "read l0 Z -> o1"), (0, 0, 0), 1, 1, 2, 0)
    seg = P.Seg("tanc", (("LogicData", Expr("k")),), (("LogicalAncilla", Expr("0")),), ())
    gadget = P.Body([seg])
    call = P.Call("g", (), (("k", Expr("k")),), ())
    prog = P.Program(templates={"tanc": t}, root=P.Body([P.Loop("k", Expr("0"), Expr("500"), P.Body([call]))]),
                     bodies={("g", (), ()): gadget})
    ports = {"tanc": {"first": [0.0], "last": [30.0], "out": [10.0, 20.0], "need": {}, "span": 40.0}}
    r = PAR._Run(prog, ports, lambda *a: 0, lambda *a: 0)
    r.body(prog.root, {}, 0)
    assert not [k for k in r.ready if k[0] == "a"] and not r.gen and not r.held
    assert r.end == 40_000                      # 500 calls on 500 blocks, all at once


def test_source_slots_a_closed_form_writes_are_read_back_exactly(monkeypatch):
    """A closed form's source slots are stored as one strided run each (Gidney 2025's top loop
    writes ~8,000 a step, 21,640 steps), and a later read gets exactly what full iteration
    gives: here loop A writes slot 1000 + k at 10k + 5 us, and loop B, each step on a block of
    its own, waits for its slot -- so the run ends at the last slot's time plus B's 20 us."""
    tw = P.Template("tw", ("ppm Z p0 Z p1 -> o0",), (0,), 2, 0, 1, 0)
    tr = P.Template("tr", ("if e0: x p0",), (0,), 1, 0, 0, 1)
    write = P.Seg("tw", (("LogicData", Expr("k")), ("LogicData", Expr("4000"))), (), (), (),
                  (("src", Expr("1000 + k"), Expr("1001 + k"), "o0"),))
    read = P.Seg("tr", (("LogicData", Expr("5000 + k")),), (), (P._Rec("src", Expr("1000 + k")),))
    ports = {"tw": {"first": [0.0, 0.0], "last": [10.0, 10.0], "out": [5.0], "need": {}, "span": 10.0},
             "tr": {"first": [0.0], "last": [20.0], "out": [], "need": {"0": 0.0}, "span": 20.0}}

    def prog():
        return P.Program(templates={"tw": tw, "tr": tr},
                         root=P.Body([P.Loop("k", Expr("0"), Expr("3000"), P.Body([write])),
                                      P.Loop("k", Expr("0"), Expr("3000"), P.Body([read]))]))
    got = PAR.parallel_us(prog(), ports)
    assert got["extrapolated"] > 5000
    assert got["us"] == 10 * 2999 + 5 + 20
    monkeypatch.setattr(PAR, "CAP", 10 ** 9)
    assert PAR.parallel_us(prog(), ports)["ns"] == got["ns"]
