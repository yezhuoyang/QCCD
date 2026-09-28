"""The pulse table `qccd.qec.native` copies from `Compiler/bridge/check_cert.py`.

The copy exists so a library imported by a server never edits `sys.path`; this test is
what keeps it a copy.  Two independent statements are checked:

* the copy emits exactly what the bridge emits, for all 16 `(quarter turns, axis)` cases
  of `R` and all 4 of `MS` -- so a sign fixed in one place and not the other fails here;
* each emitted Clifford IS the rotation the TSIR names, compared with the unitary written
  from its definition -- so the two cannot agree on something wrong.
"""

from __future__ import annotations

import cmath
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

stim = pytest.importorskip("stim")
np = pytest.importorskip("numpy")

from qccd.qec import native  # noqa: E402

PI = math.pi


@pytest.fixture(scope="module")
def check_cert():
    sys.path.insert(0, str(ROOT / "Compiler" / "bridge"))
    try:
        import check_cert as cc
    finally:
        sys.path.remove(str(ROOT / "Compiler" / "bridge"))
    return cc


def _circ(fn, *args) -> str:
    c = stim.Circuit()
    fn(c, *args)
    return str(c)


def _tableau(fn, n, *args):
    c = stim.Circuit()
    c.append("I", list(range(n)))
    fn(c, *args)
    return stim.Tableau.from_circuit(c)


X = np.array([[0, 1], [1, 0]], dtype=complex)
Y = np.array([[0, -1j], [1j, 0]], dtype=complex)
I2 = np.eye(2, dtype=complex)


def _expm_pauli(theta: float, P) -> np.ndarray:
    """exp(-i θ/2 P) for a Pauli (P² = 1)."""
    return math.cos(theta / 2) * np.eye(P.shape[0]) - 1j * math.sin(theta / 2) * P


@pytest.mark.parametrize("k", range(4))
@pytest.mark.parametrize("axis", range(4))
def test_r_agrees_with_check_cert(check_cert, k, axis):
    th, ph = k * PI / 2, axis * PI / 2
    assert _circ(native.emit_r, 0, th, ph) == _circ(check_cert._emit_r, 0, th, ph)
    # and with a negative spelling of the same angles
    assert _circ(native.emit_r, 0, th - 2 * PI, ph - 2 * PI) == \
        _circ(check_cert._emit_r, 0, th - 2 * PI, ph - 2 * PI)


@pytest.mark.parametrize("k", range(4))
@pytest.mark.parametrize("axis", range(4))
def test_r_is_the_rotation_it_names(k, axis):
    th, ph = k * PI / 2, axis * PI / 2
    U = _expm_pauli(th, math.cos(ph) * X + math.sin(ph) * Y)
    want = stim.Tableau.from_unitary_matrix(U, endian="little")
    assert _tableau(native.emit_r, 1, 0, th, ph) == want


@pytest.mark.parametrize("k", range(4))
def test_ms_agrees_with_check_cert_and_is_the_rotation(check_cert, k):
    th = k * PI / 2
    assert _circ(native.emit_ms, 0, 1, th) == _circ(check_cert._emit_ms, 0, 1, th)
    U = _expm_pauli(th, np.kron(X, X))
    assert _tableau(native.emit_ms, 2, 0, 1, th) == \
        stim.Tableau.from_unitary_matrix(U, endian="little")


@pytest.mark.parametrize("k", range(4))
def test_vz_is_the_phase_it_names(k):
    lam = k * PI / 2
    U = np.diag([1, cmath.exp(1j * lam)])
    assert _tableau(native.emit_vz, 1, 0, lam) == \
        stim.Tableau.from_unitary_matrix(U, endian="little")


def test_right_angle_predicate_is_the_bridges(check_cert):
    for x in (0.0, PI / 2, -PI, 3 * PI / 2 + 1e-12, 0.3, PI / 4):
        assert native.is_right_angle(x) == check_cert.is_right_angle(x)


def test_non_clifford_is_refused_not_rounded():
    c = stim.Circuit()
    with pytest.raises(native.NotClifford):
        native.emit_r(c, 0, PI / 4, 0.0)
    with pytest.raises(native.NotClifford):
        native.emit_ms(c, 0, 1, 0.3)
    with pytest.raises(native.NotClifford):
        native.canonical_abstract("T")


def test_abstract_names_and_aliases():
    c = stim.Circuit()
    assert native.emit_abstract(c, "cnot", [0, 1]) == 2
    assert native.emit_abstract(c, "sdg", [0]) == 1
    assert native.emit_abstract(c, "I", [1]) == 1          # an explicit idle is no gate
    assert str(c) == "CX 0 1\nS_DAG 0"
