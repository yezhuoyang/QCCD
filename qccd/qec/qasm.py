"""The OpenQASM 2.0 subset a memory experiment is written in, read without qiskit.

A memory experiment's detectors are declared on the circuit's CLASSICAL BITS, so two
facts have to be read off the source exactly: which bit each `measure` writes, and the
order in which each qubit is measured.  The second is what pins a compiled program's
measurements to those bits without a certificate -- a qubit is a wire, a compiler may
reorder operations on different wires but never two measurements of the same one, so the
k-th measurement of qubit q in the hardware program IS the k-th `measure q[..]` here.

The subset is what `qccd.qec.experiments` emits and qccdc accepts: `qreg`/`creg`
declarations, the Clifford gates `id x y z h s sdg cx cz swap`, `measure a[i] -> c[j]`,
`reset a[i]` and `barrier`.  Anything else is refused by name rather than guessed at,
because a parser that skips a gate it does not know produces a circuit that is quietly a
different circuit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["QasmError", "QasmOp", "QasmProgram", "parse_qasm", "GATES_1Q", "GATES_2Q"]

#: QASM gate name -> stim gate name.
GATES_1Q = {"id": "I", "x": "X", "y": "Y", "z": "Z", "h": "H", "s": "S", "sdg": "S_DAG"}
GATES_2Q = {"cx": "CX", "cz": "CZ", "swap": "SWAP"}

_DECL = re.compile(r"^(qreg|creg)\s+([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]$")
_MEASURE = re.compile(r"^measure\s+([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]\s*->\s*"
                      r"([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]$")
_ARG = re.compile(r"^([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]$")


class QasmError(ValueError):
    """The text is not in the supported subset."""


@dataclass(frozen=True)
class QasmOp:
    """One statement: `name` is a stim gate name, `"M"` or `"R"`; `bit` is set for `"M"`."""

    name: str
    qubits: tuple[int, ...]
    bit: int | None = None
    line: int = 0


@dataclass
class QasmProgram:
    n_qubits: int
    n_bits: int
    ops: list[QasmOp] = field(default_factory=list)

    def bits_by_qubit(self) -> dict[int, list[int]]:
        """For each qubit, the classical bits its measurements write, in program order."""
        out: dict[int, list[int]] = {}
        for op in self.ops:
            if op.name == "M":
                out.setdefault(op.qubits[0], []).append(op.bit)  # type: ignore[arg-type]
        return out

    def measured_bits(self) -> list[int]:
        return [op.bit for op in self.ops if op.name == "M"]  # type: ignore[misc]


def parse_qasm(text: str) -> QasmProgram:
    qregs: dict[str, tuple[int, int]] = {}
    cregs: dict[str, tuple[int, int]] = {}
    nq = nc = 0
    ops: list[QasmOp] = []

    def index(regs: dict, name: str, i: int, line: int, what: str) -> int:
        if name not in regs:
            raise QasmError(f"line {line}: unknown {what} register {name!r}")
        base, size = regs[name]
        if not 0 <= i < size:
            raise QasmError(f"line {line}: {name}[{i}] is outside {name}[{size}]")
        return base + i

    for lineno, raw in enumerate(text.splitlines(), 1):
        body = raw.split("//", 1)[0].strip()
        if not body:
            continue
        for stmt in body.split(";"):
            stmt = stmt.strip()
            if not stmt:
                continue
            if stmt.startswith("OPENQASM") or stmt.startswith("include"):
                continue
            m = _DECL.match(stmt)
            if m:
                kind, name, size = m.group(1), m.group(2), int(m.group(3))
                if kind == "qreg":
                    qregs[name] = (nq, size)
                    nq += size
                else:
                    cregs[name] = (nc, size)
                    nc += size
                continue
            m = _MEASURE.match(stmt)
            if m:
                q = index(qregs, m.group(1), int(m.group(2)), lineno, "quantum")
                b = index(cregs, m.group(3), int(m.group(4)), lineno, "classical")
                ops.append(QasmOp("M", (q,), b, lineno))
                continue
            head, _, rest = stmt.partition(" ")
            head = head.strip()
            if head == "barrier":
                continue
            if "(" in head:
                raise QasmError(f"line {lineno}: parameterised gate {head!r} is outside the "
                                f"supported subset")
            args = [a.strip() for a in rest.split(",")] if rest.strip() else []
            qs = []
            for a in args:
                am = _ARG.match(a)
                if not am:
                    raise QasmError(f"line {lineno}: operand {a!r} is not reg[index]")
                qs.append(index(qregs, am.group(1), int(am.group(2)), lineno, "quantum"))
            if head == "reset":
                if len(qs) != 1:
                    raise QasmError(f"line {lineno}: reset takes one qubit")
                ops.append(QasmOp("R", tuple(qs), None, lineno))
            elif head in GATES_1Q:
                if len(qs) != 1:
                    raise QasmError(f"line {lineno}: {head} takes one qubit")
                ops.append(QasmOp(GATES_1Q[head], tuple(qs), None, lineno))
            elif head in GATES_2Q:
                if len(qs) != 2 or qs[0] == qs[1]:
                    raise QasmError(f"line {lineno}: {head} takes two distinct qubits")
                ops.append(QasmOp(GATES_2Q[head], tuple(qs), None, lineno))
            else:
                raise QasmError(f"line {lineno}: {stmt!r} is outside the supported subset "
                                f"(gates: {', '.join(list(GATES_1Q) + list(GATES_2Q))}, "
                                f"measure, reset, barrier)")
    return QasmProgram(nq, nc, ops)
