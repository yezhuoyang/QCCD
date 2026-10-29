"""Shor at scale, layer one: the symbolic program (qccd/fullstack/scale).

The user, 2026-10-29: make Shor scale "eventually Shor2048 ... How to represent program in a
symbolic compact way so it actually fit in the browser while still is correct ... make
everything localized: there must be many many repeated structure, that you don't need to
recompile, and don't need to store twice".

FormalRV's `.qec` for Shor is the same size at every key size; QCCD's lowering
(`qec2alg.lower`) unrolls it, so its output grows with the run.  `scale.program` lowers the
same `.qec` without unrolling: loops stay loops, data decisions are slots, straight runs are
templates stored once.  What is held here:

* the arithmetic evaluates FormalRV's expressions as `sysprog.evaluate` does, and a power
  under a remainder (Shor's `a^(2^(m-1-j)) % N`) modulo, at 2048 bits;
* expanded, the symbolic program IS qec2alg's lowering -- every instruction, guard and name
  (up to renaming) -- from FormalRV's GENERIC Shor.qec given the witness, against the `.qec`
  FormalRV specialized itself (the toolchain is needed for that one);
* its instruction count, taken without expanding, is the expansion's;
* its size is the program's, not the run's: the same at 3 bits and at RSA-2048, where the
  run executes 3.6 x 10^14 instructions.
"""
from __future__ import annotations

import random
import time
from collections import Counter
from pathlib import Path

import pytest

from qccd.fullstack import layers
from qccd.fullstack.scale import program as P
from qccd.fullstack.scale.expr import ev
from qccd.fullstack.sysprog import evaluate

REPO = Path(__file__).resolve().parents[1]

WITNESS = dict(m=4, bits=3, N=3, a=2, ainv=2, rec=19)
RSA2048 = int(
    "25195908475657893494027183240048398571429282126204032027777137836043662020707595556264018525880784406918290641249515082189298559149176184502808489120072844992687392807287776735971418347270261896375014971824691165077613379859095700097330459748808428401797429100642458691817195118746121515172654632282216869987549182422433637259085141865462043576798423387184774447920739934236584823824281198163815010674810451660377306056201619676256133844143603833904414952634432190114657544454178424020924616515723350778707749817125772467962926386356373289912154831438167899885040445364023527381951378636564391212010397122822120720357")


def _generic():
    from qccd.fullstack import formalrv
    try:
        formalrv.check_pin()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"the pinned FormalRV clone is needed: {e}")
    return layers.read(formalrv.root() / "FormalRV" / "Examples" / "Shor" / "Shor.qec", "qec")


def _rsa2048() -> dict:
    N = RSA2048
    bits = N.bit_length() + 1
    m = next(k for k in range(2 * bits + 2) if N * N < 2 ** k <= 2 * N * N)
    return dict(m=m, bits=bits, N=N, a=2, ainv=pow(2, -1, N), rec=m + 4 * bits + 3)


def test_the_arithmetic_is_formalrvs_and_a_power_under_a_remainder_is_taken_modulo():
    cases = ["7^(2^(7 - j))%15", "1 - (1 - (1 - cst))", "cst/2^k%2", "n - (rv_d + 1)",
             "(1 - (1 - (a - b + (b - a)))) + 2", "j*4988198 + 2346", "min(a, b) + max(3, j)", "a - b - 3"]
    rnd = random.Random(7)
    for c in cases:
        for _ in range(300):
            env = {v: rnd.randint(0, 40) for v in "j cst k n rv_d a b".split()}
            env["j"] = rnd.randint(0, 7)
            assert ev(c, env) == evaluate(c, env), (c, env)
    w = _rsa2048()
    t = time.time()
    for j in (0, 1, 4095):
        v = ev("a^(2^(m - (j + 1)))%N", {**w, "j": j})
        assert v == pow(2, 2 ** (w["m"] - j - 1), w["N"])
    assert time.time() - t < 5


def test_the_symbolic_program_is_qec2algs_lowering_instruction_for_instruction(tmp_path):
    """FormalRV's generic Shor.qec, evaluated by QCCD at the witness, expanded, against
    qec2alg's lowering of the .qec FormalRV specialized itself (758,494 instructions)."""
    from test_fullstack import _toolchains_built
    if not _toolchains_built():
        pytest.skip("needs the pinned FormalRV toolchain")
    from qccd.fullstack import formalrv, qec2alg
    from qccd.gadget.algorithm import parse_algorithm
    st = _generic()
    src = formalrv.root() / "FormalRV" / "Examples" / "Shor" / "Shor.prog"
    formalrv.prog_to_qppm(src, tmp_path / "Shor.qppm")
    formalrv.qppm_to_qec(tmp_path / "Shor.qppm", tmp_path / "Shor.qec", "surface", 3,
                         {k: str(v) for k, v in WITNESS.items()})
    ones = [WITNESS["m"] + WITNESS["bits"] - 1]
    low = qec2alg.lower(layers.read(tmp_path / "Shor.qec", "qec"), ones=ones)
    alg = parse_algorithm(low.text, name="Shor")
    prog = P.build(st, params=WITNESS)
    r = P.compare(prog, alg, data_ones=ones)
    assert r["same"], r
    assert r["mine"] == 758_494          # with every merge's extraction rounds (lattice surgery in full)
    # and the same from the .qec FormalRV specialized: one program, two inputs
    r2 = P.compare(P.build(layers.read(tmp_path / "Shor.qec", "qec")), alg, data_ones=ones)
    assert r2["same"], r2


def test_counting_without_expanding_is_the_expansion():
    st = _generic()
    for w in (WITNESS, dict(m=6, bits=4, N=7, a=2, ainv=4, rec=25)):
        prog = P.build(st, params=w)
        c = P.count(prog)
        e = Counter(word for word, _, _, _ in P.expand(prog, reserved=True))
        e["prep"] -= prog.n_data                     # the inputs, not the program's
        assert +c == +e, {k: (c[k], e[k]) for k in set(c) | set(e) if c[k] != e[k]}


def test_the_program_is_as_large_as_its_gadgets_not_its_run():
    st = _generic()
    small = P.build(st, params=WITNESS)
    t = time.time()
    big = P.build(st, params=_rsa2048())
    c = P.count(big)
    assert time.time() - t < 30
    assert small.size() == big.size()
    assert big.size()["templates"] < 20 and big.size()["nodes"] < 200
    assert big.n_data == 16_391                          # RSA-2048: 2049-bit registers, m = 4096
    # every instruction the run executes, every predicate taken (the schedule's view)
    assert sum(c.values()) == 361_651_164_297_216
    assert c["ppm"] == 32_916_974_086_144 and c["inject"] == 11_075_549_313_024
