"""`bit`: a bit the archive computes from outcomes it holds (qccd/gadget/algorithm.py).

Gidney-Ekera's spacelike adder picks a measurement basis by an XOR of conjunctions of earlier
outcomes (FormalRV's `parity c[..] <- ⊕ ... (c[a] ∧ c[b])` chains, then `when c[..]: measure
Z` / `when ¬c[..]: measure X`).  An `.alg` guard is a conjunction of parities, so such a bit is
computed first, by the archive, and the guard reads it like a measured outcome:

    bit (a & !b) ^ c -> f
    if f: read q Z -> m

What is held here:

* it parses (an XOR of conjunctions, `!` a negation, `1` the empty conjunction) and refuses a
  bit that reads an outcome nothing measured;
* its value in every branch is the formula of that branch's outcomes, and a guard on it acts
  exactly then (exact branch enumeration, `logic.contract`);
* an affine bit is affine in the symbolic simulator; a product is refused there, by name;
* the town schedules it as an archive job after the outcomes it reads are decoded, every
  place and machine rule holding;
* the flat sign-off, which folds outcomes as parities, says it cannot follow a product.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "out" / "gadgets" / "cache"

AND = """prep q0 Z
prep q1 Z
prep q2 Z
read q0 X -> a
read q1 X -> b
decode q0 q1
bit (a & !b) ^ a -> f
if f: x q2
read q2 Z -> r
decode q2
"""


def test_a_bit_parses_and_refuses_an_unmeasured_outcome():
    from qccd.gadget.algorithm import AlgorithmError, parse_algorithm, parse_bit
    assert parse_bit("(a & !b) ^ c ^ 1") == (((False, "a"), (True, "b")), ((False, "c"),), ())
    ins = [i for i in parse_algorithm(AND).instructions if i.kind == "bit"]
    assert len(ins) == 1 and ins[0].cvar == "f" and ins[0].blocks == []
    with pytest.raises(AlgorithmError):
        parse_algorithm("prep q0 Z\nread q0 X -> a\nbit a & z -> f\n")
    with pytest.raises(AlgorithmError):
        parse_algorithm("prep q0 Z\nread q0 X -> a\nbit a\n")              # names no outcome


def test_its_value_is_the_formula_in_every_branch():
    from qccd.gadget.algorithm import parse_algorithm
    from qccd.gadget.logic.contract import program_outcomes
    d = program_outcomes(parse_algorithm(AND))
    assert d["outcomes"] == ["a", "b", "f", "r"] and len(d["distribution"]) == 4
    for (a, b, f, r), p in d["distribution"].items():
        assert f == (a & b) and r == f and abs(p - 0.25) < 1e-9


def test_an_affine_bit_is_affine_and_a_product_is_refused_symbolically():
    from qccd.gadget.algorithm import parse_algorithm
    from qccd.gadget.logic.symbolic import NotClifford, expr_text, run
    out = run(parse_algorithm(AND.replace("bit (a & !b) ^ a -> f", "bit a ^ !b -> f")))
    assert expr_text(out["f"]) == expr_text(out["a"] ^ out["b"] ^ frozenset({"1"}))
    with pytest.raises(NotClifford):
        run(parse_algorithm(AND))


def test_the_town_schedules_it_in_the_archive_and_the_flat_sign_off_says_why_not():
    from qccd.gadget.algorithm import parse_algorithm
    from qccd.gadget.city import schedule
    from qccd.gadget.floorplan import load_file
    from qccd.gadget.library import LeafLibrary
    from qccd.gadget.logic.flat import sign_off
    from qccd.gadget.place_checks import check_places
    leaves = LeafLibrary(str(CACHE), log=None)
    lib, _ = load_file(REPO / "examples/gadgets/floorplans/fullstack.floor", leaves)
    alg = parse_algorithm(AND)
    gir = schedule(alg, lib, leaves, log=None, parallel=True)
    assert gir["refused"] == []
    i_bit = next(i.index for i in alg.instructions if i.kind == "bit")
    i_x = next(i.index for i in alg.instructions if i.kind == "x")
    rec = [e for e in gir["events"] if e[6] == i_bit]
    assert [e[4] for e in rec] == ["record"]                     # one archive job, no ion
    # after both outcomes it reads are recorded and decoded (a post office records its outcome
    # before its op ends: it still sends the ions out)
    read = {o["cvar"]: o for o in gir["outcomes"] if o["cvar"] in ("a", "b")}
    wins = [w for w in gir["windows"] if w["instruction"] in {o["instruction"] for o in read.values()}]
    assert len(read) == 2 and wins and all(w.get("written_us") is not None for w in wins)
    assert rec[0][1] >= max([o["recorded_us"] for o in read.values()] + [w["written_us"] for w in wins])
    assert any(f["instruction"] == i_x for f in gir["frames"])
    ck = check_places(gir, lib, leaves)
    assert not [r for r, v in ck.items() if isinstance(v, dict) and v.get("passed") is False]
    so = sign_off(alg, gir, lib, leaves)
    assert so["passed"] is None and "product of outcomes" in so["skipped"]
