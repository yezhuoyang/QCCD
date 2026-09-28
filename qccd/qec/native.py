"""Hardware gates as stabilizer operations: what one TSIR gate does to a Pauli frame.

Two vocabularies reach this module, and both have to mean exactly one Clifford:

* **qccdc's native pulses** -- `R(θ, φ)` per ion, the frame change `VZ(λ)`, and `MS(θ)` per
  pair.  The table below MIRRORS `Compiler/bridge/check_cert.py` (`_emit_r`, `_emit_ms`,
  `is_right_angle`) line for line, and `tests/test_qec_native.py` asserts the two agree on
  all 16 `(quarter turns, axis)` cases of `R` and all 4 of `MS`.  It is a copy rather than
  an import because the bridge is a script directory, not a package: reaching into it
  means editing `sys.path`, which a library imported by a server must not do.  The test is
  what keeps the copy honest -- a sign fixed in one place and not the other fails it.
* **the gadget layer's abstract Clifford names** (`I X Y Z H S S_DAG CX CZ SWAP`, see
  `qccd.gadget.logic.circuit`), so a program built from gadgets extracts as well as one
  compiled by qccdc.

Angles must be multiples of π/2.  Anything else is outside the stabilizer formalism, and
`NotClifford` says so rather than rounding it into a different circuit.
"""

from __future__ import annotations

import math

__all__ = [
    "PI",
    "NotClifford",
    "is_right_angle",
    "quarter_turns",
    "emit_r",
    "emit_vz",
    "emit_ms",
    "ABSTRACT_1Q",
    "ABSTRACT_2Q",
    "canonical_abstract",
    "emit_abstract",
]

PI = math.pi


class NotClifford(ValueError):
    """A gate or angle the stabilizer simulation cannot represent exactly."""


def is_right_angle(x: float, eps: float = 1e-9) -> bool:
    """Is `x` a multiple of π/2?  (`check_cert.is_right_angle`.)"""
    return abs(x / (PI / 2) - round(x / (PI / 2))) < eps


def quarter_turns(x: float, what: str = "angle") -> int:
    """`x / (π/2)` mod 4, refusing an angle that is not a multiple of π/2."""
    if not is_right_angle(x):
        raise NotClifford(f"non-Clifford {what} {x!r}: not a multiple of pi/2")
    return round(x / (PI / 2)) % 4


def emit_r(circ, q: int, theta: float, phi: float) -> None:
    """R(θ, φ) = exp(-i θ/2 (cos φ X + sin φ Y)), θ and φ multiples of π/2.

    Mirrors `check_cert._emit_r`.  stim's `SQRT_X` is exp(-i π/4 X) up to a global phase,
    i.e. R(+π/2, 0) -- not its dagger.  The mistake is invisible on a single gate and shows
    up only as a whole-program mismatch, which is what the noiseless check exists to catch.
    """
    k = quarter_turns(theta, "pulse angle")
    axis = quarter_turns(phi, "pulse phase")   # 0 = X, 1 = Y, 2 = -X, 3 = -Y
    if k == 0:
        return
    if axis in (0, 2):
        base, sense = "X", (1 if axis == 0 else -1)
    else:
        base, sense = "Y", (1 if axis == 1 else -1)
    turns = (k * sense) % 4
    if turns == 0:
        return
    if turns == 2:
        circ.append(base, [q])                 # a π rotation is the Pauli itself
    elif turns == 1:
        circ.append("SQRT_X" if base == "X" else "SQRT_Y", [q])
    else:
        circ.append("SQRT_X_DAG" if base == "X" else "SQRT_Y_DAG", [q])


def emit_vz(circ, q: int, lam: float) -> None:
    """VZ(λ) = diag(1, e^{iλ}): S applied λ/(π/2) times (`check_cert.tableau_from_program`)."""
    for _ in range(quarter_turns(lam, "frame change")):
        circ.append("S", [q])


def emit_ms(circ, a: int, b: int, theta: float) -> None:
    """MS(θ) = exp(-i θ/2 X⊗X), θ a multiple of π/2.  Mirrors `check_cert._emit_ms`."""
    k = quarter_turns(theta, "MS angle")
    if k == 0:
        return
    if k == 2:
        circ.append("X", [a])
        circ.append("X", [b])
        return
    circ.append("SQRT_XX" if k == 1 else "SQRT_XX_DAG", [a, b])


# ------------------------------------------------------------------ abstract gates

#: One-qubit Clifford names the gadget layer emits, as stim gates.  `I` is kept so a
#: program that spells an idle explicitly still extracts; it is not a gate and gets no
#: gate noise.
ABSTRACT_1Q = {"I": "I", "X": "X", "Y": "Y", "Z": "Z", "H": "H", "S": "S", "S_DAG": "S_DAG"}
#: Two-qubit names.  Each stands for ONE entangling gate on the hardware, so each is
#: charged one MS gate's error; operand order is (control, target) for CX.
ABSTRACT_2Q = {"CX": "CX", "CZ": "CZ", "SWAP": "SWAP"}

_ALIASES = {"CNOT": "CX", "SDG": "S_DAG", "SDAG": "S_DAG", "ID": "I", "IDLE": "I"}


def canonical_abstract(name: str) -> str:
    """The canonical spelling (`qccd.gadget.logic.circuit.canonical`'s aliases), or
    `NotClifford` for a name with no stabilizer meaning (T, a custom gate)."""
    key = str(name).strip().upper()
    key = _ALIASES.get(key, key)
    if key in ABSTRACT_1Q or key in ABSTRACT_2Q:
        return key
    raise NotClifford(f"gate {name!r} has no Clifford meaning here (known: native R, VZ, MS; "
                      f"abstract {', '.join(list(ABSTRACT_1Q) + list(ABSTRACT_2Q))})")


def emit_abstract(circ, name: str, qubits: list[int]) -> int:
    """Append one abstract gate; returns its arity (1 or 2)."""
    key = canonical_abstract(name)
    if key in ABSTRACT_2Q:
        if len(qubits) != 2:
            raise NotClifford(f"{key} needs two operands, got {len(qubits)}")
        circ.append(ABSTRACT_2Q[key], qubits)
        return 2
    if len(qubits) != 1:
        raise NotClifford(f"{key} needs one operand, got {len(qubits)}")
    if key != "I":
        circ.append(ABSTRACT_1Q[key], qubits)
    return 1
