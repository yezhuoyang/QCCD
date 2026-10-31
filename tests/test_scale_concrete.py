"""Gidney 2025's gadget calls written out concrete, as a folded `.alg` (qccd/fullstack/scale/concrete.py).

The user, 2026-10-30: "everything should be concrete hardware instruction, there is no virtual
qubit, virtual system calls".  What is held here:

* a call is written as gates over its blocks, and the folded parser reads it;
* expanded in full, the program does exactly the physical work the symbolic program counts for
  the same call -- every read, CNOT, CCZ state, preparation, merge and round, template by
  template: nothing dropped, nothing added;
* a gate's text names its classical inputs and outputs, and a prime of Gidney 2025 hands one
  bit to the next.
"""
from __future__ import annotations

import sys
import threading
from collections import Counter

import pytest


def _gidney2025():
    from qccd.fullstack import formalrv, layers
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


PHYSICAL = ("read", "cx", "autoccz", "prep", "inject", "ppm", "se", "ht", "zz", "xx", "decode")


@pytest.fixture(scope="module")
def prog():
    from qccd.fullstack.scale import program as P
    st, params = _gidney2025()

    def go():
        p = P.Builder(st).build({k: v for k, v in params.items() if isinstance(v, (int, list, tuple))})
        P.total(p, lambda tid: Counter({tid: 1}), lambda *a: Counter())
        return p
    return _in_thread(go)


def _expected(prog, gadget, keyvals):
    """The physical words the symbolic program counts for one call of `gadget`."""
    from qccd.fullstack.scale import program as P
    key = next(k for k in prog.bodies if k[0] == gadget and k[1] == keyvals)
    call = P.Call(gadget, tuple((f, P.Expr(str(v))) for f, v in keyvals),
                  tuple((f, P.Expr("0")) for f in prog.formals[gadget] if f not in dict(keyvals)), key[2])
    mini = P.Program(templates=prog.templates, bodies=prog.bodies, formals=prog.formals,
                     root=P.Body([call]), n_data=prog.n_data, params={**prog.params, "i": 0},
                     builder=prog.builder)
    inst = _in_thread(lambda: P.total(mini, lambda tid: Counter({tid: 1}), lambda *a: Counter()))
    out = Counter()
    for tid, n in inst.items():
        for ln in prog.templates[tid].lines:
            w = (ln.split(": ", 1)[1] if ln.startswith("if ") else ln).split()[0]
            if w in PHYSICAL:
                out[w] += n
    return out


@pytest.mark.parametrize("fold", [0, 8])
@pytest.mark.parametrize("gadget,keyvals", [("gidneyAddNoCarry", (("n", 32),)),
                                            ("gLoop4", (("w4", 5), ("W4", 5), ("f", 33)))])
def test_written_concrete_it_does_exactly_the_work_the_program_counts(prog, gadget, keyvals, fold):
    """Folded or not (a call-free loop written as a gate of its own), the same work."""
    from qccd.fullstack.scale import concrete as C
    from qccd.gadget.algorithm import parse_algorithm
    from qccd.gadget.fold import parse_folded
    text, info = _in_thread(lambda: C.write(prog, gadget, keyvals, {"i": 0}, {}, floorplan="town",
                                            fold=fold))
    parse_folded(text, name=gadget)
    flat = parse_algorithm(text)
    got = Counter(i.kind for i in flat.instructions if i.kind in PHYSICAL)
    got["prep"] -= info["blocks"]                    # (the program prepares the blocks it passes)
    assert +got == +_expected(prog, gadget, keyvals), (got, info)
    if fold and gadget == "gLoop4":
        assert info["stats"].get("folded loops"), info["stats"]


def _prime_gate(prog, i):
    """Prime i's gate as gPrimes calls it, the tables the controller's: (text hash, blocks, I/O)."""
    import hashlib
    from qccd.fullstack.scale import concrete as C
    from qccd.fullstack.scale import program as P
    gp = next(b for (g, _, _), b in prog.bodies.items() if g == "gPrimes")
    loop = next(n for n in gp if isinstance(n, P.Loop))
    call = next(n for n in loop.body if isinstance(n, P.Call))
    env = {**prog.params, **{f: 0 for f in prog.formals["gPrimes"]}, loop.var: i}

    def go():
        C._SUBGATES.clear()
        w = C.Writer(prog, {}, controller=True)
        name, blocks, cin, cout = w.gate(call, env, frozenset())
        head, body = w.gates[name]
        text = "\n".join([" ".join(head)] + body + sorted(f"{k} {v}" for k, v in C._SUBGATES.items()))
        return hashlib.sha1(text.encode()).hexdigest(), len(blocks), cin, cout, w.gates
    return _in_thread(go)


def test_every_prime_is_the_same_hardware_and_reads_no_bit_from_outside(prog):
    """Gidney 2025's 21,640 primes differ in their tables and in their slots' addresses: with the
    tables the classical controller's, a prime is one gate, the same text for every prime.  The
    one slot a prime reads that it did not write is a slot nothing writes: it holds 0."""
    h0, n0, cin0, cout0, gates = _prime_gate(prog, 0)
    h1, n1, cin1, cout1, _ = _prime_gate(prog, 1)
    assert h0 == h1 and n0 == n1 > 1600
    assert cin0 == cin1 == [] and cout0 == cout1 == []
    heads = [" ".join(h) for h, _ in gates.values() if "<-" in h or "->" in h]
    assert heads, "some gate inside a prime takes or gives a classical bit"
