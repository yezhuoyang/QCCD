"""Exact unitary comparison: do the emitted pulses compute the input circuit?

The stabilizer route (`check_cert.py`) settles this for Clifford circuits and says nothing
outside that fragment -- so `qft8` and `adder3`, whose whole content is `T` gates and
arbitrary-angle phases, came back `partial`. This closes that gap for circuits small
enough to hold a full operator: build the circuit's unitary and the *pulse sequence's*
unitary, and compare them entry by entry, up to one global phase.

Two things make it honest:

* The program's unitary is built from the **emitted TSIR** -- `R`, `VZ` and `MS`
  instructions with their parameters -- through the certificate's qubit→ion map. Nothing
  the compiler asserts about its own output is consulted.
* Both sides are built here, in one convention (qubit 0 is the most significant bit,
  matching `QCCDC.kron` in Lean). Comparing against qiskit's `Operator` instead would
  invite an endianness mismatch to masquerade as a compiler bug, or vice versa.

The cost is `2^n × 2^n`, so this is capped at 10 qubits by default: 16 MB and a couple of
seconds. Beyond that the stabilizer route is the only one that scales, which is exactly
why decision D1 made Clifford the verified core.
"""

from __future__ import annotations

import math

import numpy as np

PI = math.pi
I2 = np.eye(2, dtype=complex)
X = np.array([[0, 1], [1, 0]], dtype=complex)
Y = np.array([[0, -1j], [1j, 0]], dtype=complex)
Z = np.array([[1, 0], [0, -1]], dtype=complex)


def u3(theta: float, phi: float, lam: float) -> np.ndarray:
    c, s = np.cos(theta / 2), np.sin(theta / 2)
    return np.array([[c, -np.exp(1j * lam) * s],
                     [np.exp(1j * phi) * s, np.exp(1j * (phi + lam)) * c]])


def ctrl(u: np.ndarray) -> np.ndarray:
    m = np.eye(4, dtype=complex)
    m[2:, 2:] = u
    return m


def r_pulse(theta: float, phi: float) -> np.ndarray:
    return np.cos(theta / 2) * I2 - 1j * np.sin(theta / 2) * (
        np.cos(phi) * X + np.sin(phi) * Y)


def vz(lam: float) -> np.ndarray:
    return np.diag([1, np.exp(1j * lam)]).astype(complex)


def ms(theta: float) -> np.ndarray:
    return np.cos(theta / 2) * np.eye(4) - 1j * np.sin(theta / 2) * np.kron(X, X)


SWAP = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], dtype=complex)


def gate_matrix(name: str, params: list[float]) -> tuple[np.ndarray, int] | None:
    """The defining unitary of a circuit gate, and how many qubits it acts on."""
    p = list(params)
    one = {
        "id": (0, 0, 0), "x": (PI, 0, PI), "y": (PI, PI / 2, PI / 2), "z": (0, 0, PI),
        "h": (PI / 2, 0, PI), "s": (0, 0, PI / 2), "sdg": (0, 0, -PI / 2),
        "t": (0, 0, PI / 4), "tdg": (0, 0, -PI / 4),
        "sx": (PI / 2, -PI / 2, PI / 2), "sxdg": (PI / 2, PI / 2, -PI / 2),
    }
    if name in one:
        return u3(*one[name]), 1
    if name in ("rx",) and p:
        return u3(p[0], -PI / 2, PI / 2), 1
    if name in ("ry",) and p:
        return u3(p[0], 0, 0), 1
    if name in ("rz", "u1", "p") and p:
        return u3(0, 0, p[0]), 1
    if name == "u2" and len(p) >= 2:
        return u3(PI / 2, p[0], p[1]), 1
    if name in ("u3", "u") and len(p) >= 3:
        return u3(p[0], p[1], p[2]), 1
    if name == "cx":
        return ctrl(u3(PI, 0, PI)), 2
    if name == "cy":
        return ctrl(u3(PI, PI / 2, PI / 2)), 2
    if name == "cz":
        return ctrl(u3(0, 0, PI)), 2
    if name == "ch":
        return ctrl(u3(PI / 2, 0, PI)), 2
    if name == "swap":
        return SWAP, 2
    if name == "crz" and p:
        m = np.eye(4, dtype=complex)
        m[2, 2] = np.exp(-1j * p[0] / 2)
        m[3, 3] = np.exp(1j * p[0] / 2)
        return m, 2
    if name in ("cu1", "cp") and p:
        m = np.eye(4, dtype=complex)
        m[3, 3] = np.exp(1j * p[0])
        return m, 2
    if name == "rzz" and p:
        d = [np.exp(-1j * p[0] / 2), np.exp(1j * p[0] / 2),
             np.exp(1j * p[0] / 2), np.exp(-1j * p[0] / 2)]
        return np.diag(d).astype(complex), 2
    if name == "rxx" and p:
        return ms(p[0]), 2
    if name == "ccx":
        m = np.eye(8, dtype=complex)
        m[[6, 7]] = m[[7, 6]]
        return m, 3
    if name == "cswap":
        m = np.eye(8, dtype=complex)
        m[[5, 6]] = m[[6, 5]]
        return m, 3
    return None


# ------------------------------------------------------------------ application


def apply_gate(U: np.ndarray, g: np.ndarray, qubits: list[int], n: int) -> np.ndarray:
    """Left-multiply `U` by `g` acting on `qubits`.

    Done by reshaping the row index into `n` qubit axes and contracting, which costs
    O(2^n · 2^n) per gate instead of the O(8^n) a dense matrix product would.
    Qubit 0 is the most significant bit.
    """
    k = len(qubits)
    dim = 1 << n
    T = U.reshape((2,) * n + (dim,))
    g = g.reshape((2,) * k + (2,) * k)
    axes = list(qubits)
    T = np.tensordot(g, T, axes=(list(range(k, 2 * k)), axes))
    # tensordot puts the k new axes first; move them back to where they came from
    T = np.moveaxis(T, list(range(k)), axes)
    return T.reshape(dim, dim)


def circuit_unitary(ops: list[dict], n: int) -> np.ndarray:
    """The input circuit's unitary.  `measure`/`reset`/`barrier` are skipped."""
    U = np.eye(1 << n, dtype=complex)
    for o in ops:
        if o["name"] in ("measure", "reset", "barrier"):
            continue
        got = gate_matrix(o["name"], o.get("params", []))
        if got is None:
            raise NotImplementedError(f"no defining unitary for {o['name']}")
        g, k = got
        if len(o["qubits"]) != k:
            raise NotImplementedError(
                f"{o['name']} on {len(o['qubits'])} qubits, expected {k}")
        U = apply_gate(U, g, o["qubits"], n)
    return U


def program_unitary(instrs, qubit_of: dict[str, int], n: int) -> np.ndarray:
    """The unitary the EMITTED pulses actually realise."""
    U = np.eye(1 << n, dtype=complex)
    for instr in instrs:
        if instr.type != "gate":
            continue
        params = list(instr.params)
        if instr.gate == "R":
            for k, ion in enumerate(instr.ions):
                th, ph = params[k] if k < len(params) else (0.0, 0.0)
                U = apply_gate(U, r_pulse(th, ph), [qubit_of[ion]], n)
        elif instr.gate == "VZ":
            for k, ion in enumerate(instr.ions):
                (lam,) = params[k] if k < len(params) else (0.0,)
                U = apply_gate(U, vz(lam), [qubit_of[ion]], n)
        elif instr.gate == "MS":
            for k, (a, b) in enumerate(instr.pairs):
                (th,) = params[k] if k < len(params) else (0.0,)
                U = apply_gate(U, ms(th), [qubit_of[a], qubit_of[b]], n)
    return U


def same_up_to_phase(a: np.ndarray, b: np.ndarray, tol: float = 1e-8
                     ) -> tuple[bool, float, float]:
    """Is `a = e^{iα}·b`?  Returns (verdict, α, the worst entry difference)."""
    flat = np.abs(b).ravel()
    idx = int(np.argmax(flat))
    i, j = divmod(idx, b.shape[1])
    if abs(b[i, j]) < tol:
        return False, 0.0, float("inf")
    ratio = a[i, j] / b[i, j]
    if abs(abs(ratio) - 1) > tol:
        return False, 0.0, float("inf")
    worst = float(np.max(np.abs(a - ratio * b)))
    return worst <= tol, float(np.angle(ratio)), worst
