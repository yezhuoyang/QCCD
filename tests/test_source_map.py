"""`qccd.ir.source_map`: the join, and its refusal to draw an unchecked one.

The circuit pane reads a dense map -- every instruction's `meta.op`, stamped by the
compiler as it emitted -- because the certificate's gate witnesses are far too sparse to
drive it (on a real compilation they cover about a fifth of the gate instructions). But a
stamp is the compiler's own account of what it did, and a page that showed it unqualified
would be presenting an unverified correspondence as though it were a checked one.

So the sparse map checks the dense one, and these tests are about the check: it must pass
on an honest program, and it must fire on each of the two ways the two can disagree.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.ir.source_map import Mismatch, build  # noqa: E402
from qccd.ir.tsir import TSIR, Instruction  # noqa: E402

QASM = """OPENQASM 2.0;
qreg q[2];
h q[0];
cx q[0],q[1];
"""


@pytest.fixture
def qasm(tmp_path):
    p = tmp_path / "c.qasm"
    p.write_text(QASM, encoding="utf-8")
    return p


def _prog(stamps):
    """`stamps` maps instruction id -> the ops it claims, or None for no claim."""
    instrs = [Instruction(type="init", id=0, placement={"q0": "S0", "q1": "S1"},
                          quanta={"q0": 0.0, "q1": 0.0})]
    for iid, ops in stamps.items():
        instrs.append(Instruction(
            type="gate", id=iid, gate="R", arity=1, mode="intra", ions=("q0",),
            meta=({"op": ops} if ops is not None else {})))
    return TSIR(name="c", arch_spec="x", instructions=instrs, id_seq=99)


CERT = {
    "circuit_ops": [
        {"i": 0, "name": "h", "qubits": [0], "params": [], "line": 3},
        {"i": 1, "name": "cx", "qubits": [0, 1], "params": [], "line": 4},
    ],
    "gates": [{"dag": 0, "instr": 1}, {"dag": 1, "instr": 2}],
}


def test_an_honest_program_joins(qasm):
    src = build(_prog({1: [0], 2: [1]}), CERT, qasm)
    assert src["realises"] == {"1": [0], "2": [1]}
    assert src["ops"][1]["line"] == 4
    assert src["lines"][2] == "h q[0];"


def test_transport_is_towards_rather_than_executing(qasm):
    """A shuttle serves a gate; it does not perform one, and the pane must not say so."""
    prog = _prog({1: [0], 2: [1]})
    prog.instructions.append(
        Instruction(type="simd", id=7, cls="shuttle", mode="inter", meta={"op": [1]}))
    src = build(prog, CERT, qasm)
    assert src["toward"] == {"7": [1]}
    assert "7" not in src["realises"]


def test_a_stamp_that_contradicts_a_witness_is_refused(qasm):
    """Instruction 2 claims op 0, but the certificate says it realises op 1."""
    with pytest.raises(Mismatch, match="claims ops"):
        build(_prog({1: [0], 2: [0]}), CERT, qasm)


def test_a_witness_naming_an_unstamped_instruction_is_refused(qasm):
    with pytest.raises(Mismatch, match="no circuit operation at all"):
        build(_prog({1: [0], 2: None}), CERT, qasm)


def test_a_stamp_for_an_op_the_circuit_lacks_is_dropped(qasm):
    """Not an error -- the pane simply has nothing to point at, and says nothing."""
    src = build(_prog({1: [0], 2: [1, 44]}), CERT, qasm)
    assert src["realises"]["2"] == [1]


def test_the_check_can_be_waived_but_not_by_accident(qasm):
    """`check=False` exists for a certificate that predates the stamps; it is explicit."""
    src = build(_prog({1: [0], 2: [0]}), CERT, qasm, check=False)
    assert src["realises"] == {"1": [0], "2": [0]}


def test_a_certificate_without_source_lines_degrades_quietly(qasm):
    """Line 0 means "nowhere": the pane never highlights rather than highlighting line 1."""
    cert = {"circuit_ops": [{"i": 0, "name": "h", "qubits": [0], "params": []}],
            "gates": []}
    src = build(_prog({1: [0]}), cert, qasm)
    assert src["ops"][0]["line"] == 0
