"""A stim memory circuit read into a `MemoryExperiment` is the same experiment.

* stim's own rotated surface-code memories, both bases, import with the same fault mechanisms
  under two-qubit noise, the data as q[0..n-1] and their readout appended;
* the import is deterministic and keeps the circuit distance;
* an ancilla whose last measurement comes after the last gate (an X half that ends a round, as
  in a colour code with one auxiliary per plaquette) is not mistaken for data;
* a stim instruction with no QASM translation is refused by name.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
stim = pytest.importorskip("stim")

from qccd.qec.experiments import ideal_stim  # noqa: E402
from qccd.qec.qasm import parse_qasm  # noqa: E402
from qccd.qec.stim_import import StimImportError, check_equivalent, from_stim  # noqa: E402


@pytest.mark.parametrize("basis", ["z", "x"])
def test_stim_surface_memory_imports_unchanged(basis):
    src = stim.Circuit.generated(f"surface_code:rotated_memory_{basis}", distance=3, rounds=3)
    exp = from_stim(src, name=f"s3{basis}", code="surface", distance=3, rounds=3, basis=basis.upper())
    assert check_equivalent(src, exp) == []
    assert exp.n_data == 9 and exp.n_ancilla == 8
    assert exp.readout == "appended" and [q for q, _ in exp.readout_bits] == list(range(9))
    prog = parse_qasm(exp.qasm)
    assert prog.n_qubits == 17 and prog.n_bits == 3 * 8 + 9
    # every detector deterministic and the distance kept
    noisy = ideal_stim(exp, 1e-3)
    assert len(noisy.shortest_graphlike_error()) == 3


def test_an_ancilla_read_after_the_last_gate_is_not_data():
    # two data qubits and one shared ancilla, two rounds of a Z half then an X half (the colour
    # code's round with one auxiliary): the ancilla's last measurement follows the last gate
    src = stim.Circuit("""
        R 0 1 2
        CX 0 2 1 2
        M 2
        DETECTOR rec[-1]
        RX 2
        CX 2 0 2 1
        MX 2
        R 2
        CX 0 2 1 2
        M 2
        DETECTOR rec[-1] rec[-3]
        RX 2
        CX 2 0 2 1
        MX 2
        DETECTOR rec[-1] rec[-3]
        M 0 1
        DETECTOR rec[-1] rec[-2] rec[-4]
        OBSERVABLE_INCLUDE(0) rec[-1] rec[-2]
    """)
    exp = from_stim(src, name="tiny", code="rep2", distance=1, rounds=2, basis="Z")
    assert exp.n_data == 2 and exp.n_ancilla == 1
    assert check_equivalent(src, exp) == []


def test_untranslatable_instructions_are_refused():
    src = stim.Circuit("R 0 1\nCY 0 1\nM 0 1\nOBSERVABLE_INCLUDE(0) rec[-1]")
    with pytest.raises(StimImportError, match="CY"):
        from_stim(src, name="bad", code="x", distance=1, rounds=1)
