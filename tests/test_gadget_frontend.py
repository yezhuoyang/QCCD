"""The LogicQ front end of the gadget layer (docs/GADGETS.md §6.1).

Two promises are tested: a program means here what it means in LogicQ -- the same `k`, the
same logical basis, the same transversal verdicts -- and a statement LogicQ would refuse is
refused here too, with a reason a person can act on.
"""

from __future__ import annotations

import pytest

from qccd.gadget import gf2
from qccd.gadget.codes import TUTORIAL_BB, bivariate_bicycle, code_name, parse_poly
from qccd.gadget.logicq import ParseError, parse


def _bb(name):
    l, m, A, B, _ = TUTORIAL_BB[name]
    return bivariate_bicycle(None, l, m, parse_poly(A, l, m), parse_poly(B, l, m))


BB72 = ("code q as BivariateBicycle { l = 6; m = 6; A = x^3 + y + y^2; "
        "B = y^3 + x + x^2; params = (72, 12, 6); }\n"
        "code r as BivariateBicycle { l = 6; m = 6; A = x^3 + y + y^2; "
        "B = y^3 + x + x^2; params = (72, 12, 6); }\n")


# ------------------------------------------------------------------------ codes


@pytest.mark.parametrize("name", sorted(TUTORIAL_BB))
def test_k_matches_logicq_tutorial_table(name):
    n, k, _ = TUTORIAL_BB[name][4]
    code = _bb(name)
    assert (code.n, code.k) == (n, k)


@pytest.mark.parametrize("name", ["bb18", "bb72", "bb144"])
def test_derived_logical_basis_is_a_valid_symplectic_basis(name):
    code = _bb(name)
    lx, lz = code.logical_basis
    assert len(lx) == len(lz) == code.k
    for i in range(code.k):
        for j in range(code.k):
            assert gf2.dot(lx[i], lz[j]) == (i == j)
    assert all(gf2.dot(x, z) == 0 for x in lx for z in code.hz)
    assert all(gf2.dot(z, x) == 0 for z in lz for x in code.hx)
    assert not any(gf2.in_span(code.hx, x) for x in lx)
    assert not any(gf2.in_span(code.hz, z) for z in lz)


def test_transversal_verdicts_match_the_tutorial():
    # Ex02: blockTransversal H/S is accepted on bb18; Ex08: plain H on bb72 is refused
    assert _bb("bb18").transversal_preserves("H")
    assert _bb("bb18").transversal_preserves("S")
    assert not _bb("bb72").transversal_preserves("H")


def test_polynomials_follow_chainq_grammar():
    assert parse_poly("x^2*y + x^2*y^2", 3, 3) == ((2, 1), (2, 2))
    assert parse_poly("(x + 1)^2", 3, 3) == ((0, 0), (2, 0))      # over F2
    assert parse_poly("2 + x + x", 3, 3) == ()                        # even literal, cancelled
    assert parse_poly("x^3", 3, 3) == ((0, 0),)                       # x^l = 1


def test_equal_matrices_share_one_code_identity():
    l, m, A, B, _ = TUTORIAL_BB["bb72"]
    assert code_name(l, m, parse_poly(A, l, m), parse_poly(B, l, m)) == "bb72"
    other = code_name(3, 3, parse_poly("x^2*y + x^2*y^2", 3, 3), parse_poly("1 + x*y^2", 3, 3))
    assert other.startswith("bb18_")


# ------------------------------------------------------------------------ programs


def test_every_accepted_form_parses_to_its_kind():
    src = BB72 + """
code b as Bare; Logical X b[0]
Logical H b[0]
Logical X q[0]; Logical Z q[1]
Logical measure q[0]↦Z -> c0
ppm c1 := M q[0]↦Z, r[1]↦Z
c2 := M q[2]|->X
pauli Y r[3]
magic T q[4]
Logical T r[5]
transversal 2 S
blockTransversal q H
transversalCNOTBatch q r
Logical transversalBatch r -> q
Logical CNOT q[0] r[0]
Logical H q[7]
ppm frame X(q[0])
ppm discard r[11]
ppm skip
"""
    prog = parse(src)
    kinds = [i.kind for i in prog.instructions]
    assert kinds == ["pauli", "transversal", "pauli", "pauli", "ppm", "ppm", "ppm", "pauli",
                     "magic", "magic", "transversal", "transversal", "tcnot_batch",
                     "tcnot_batch", "gate2", "gate1", "pauli", "discard"]
    assert prog.instructions[5].target == (("q", 0, "Z"), ("r", 1, "Z"))
    assert prog.instructions[10].blocks == ("b",)          # `transversal 2` = the third block
    assert prog.instructions[13].blocks == ("r", "q")      # control first
    assert prog.code("q") is not prog.code("r") and prog.code("q").name == prog.code("r").name


def test_a_declaration_may_span_lines_and_share_a_line():
    prog = parse("code q as BivariateBicycle {\n l = 6; m = 6;\n A = x^3 + y + y^2;\n"
                 " B = y^3 + x + x^2;\n}; Logical X q[0]")
    assert prog.code("q").k == 12
    assert [i.kind for i in prog.instructions] == ["pauli"]


@pytest.mark.parametrize("stmt, reason", [
    ("Logical CNOT q[0], r[0]", "spaces, not a comma"),
    ("Logical measure q[0]->Z -> c0", "q[i]↦P"),
    ("Logical H q[12]", "there is no logical 12"),
    ("transversal 9 H", "names no declaration"),
    ("code z as LiftedProduct { ell = 3 }", "not realised by the gadget layer"),
    ("automorphism 0 [[1]]", "no text form"),
    ("foo bar", "not a LogicQ statement"),
    ("ppm c9 := M q[0]↦Z, q[0]↦X", "appears twice"),
    ("transversalCNOT q[0] r[0] [[1]]", "72 x 72"),
    ("code s as BivariateBicycle { l = 6; m = 6; A = x^3 + y + y^2; B = y^3 + x + x^2; "
     "params = (72, 10, 6); }", "the matrices give n = 72, k = 12"),
])
def test_refusals_name_the_reason(stmt, reason):
    with pytest.raises(ParseError) as exc:
        parse(BB72 + stmt)
    assert reason in exc.value.reason
    assert exc.value.line == 3
