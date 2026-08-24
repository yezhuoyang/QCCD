"""Derive and check the native-gate decompositions numerically, so Lean states true theorems.

The textbook CX-from-MS identity is quoted with at least three different global-phase
conventions, and a wrong phase is invisible in isolation but observable under control.
Writing a remembered constant into a Lean theorem and then fighting the proof is the slow
way to find that out.  This script is the search; Lean is the proof.  It is NOT part of
the trusted path.

Conventions, fixed here and mirrored exactly in `QCCDC/Pulse/Native.lean`:

    R θ φ  := cos(θ/2)·I - i·sin(θ/2)·(cos φ·X + sin φ·Y)      one physical pulse
    RZ λ   := diag(e^{-iλ/2}, e^{iλ/2})                        VIRTUAL: a frame change
    MS θ   := cos(θ/2)·I₄ - i·sin(θ/2)·(X⊗X)                   the entangler

Both closed forms equal exp(-i·θ/2·A) for a generator with A² = I, which is why no matrix
exponential is needed anywhere; A² = I is proved in Lean rather than assumed.

Qubit order is big-endian: in A⊗B, A acts on qubit 0, and CX has qubit 0 as control.

Two identities carry the whole single- and two-qubit story:

    u3(θ,φ,λ) = e^{i(φ+λ)/2} · RZ(φ+λ) · R(θ, π/2 − λ)
    CX        = e^{iπ/4} · Ry(−π/2)⊗I · Rx(−π/2)⊗Rx(−π/2) · MS(π/2) · Ry(π/2)⊗I

The first is the one that matters for cost: **every single-qubit gate is exactly one
physical pulse**, because the Z is a frame update with no duration.  A compiler that
emitted three pulses per single-qubit gate would be paying 3x for nothing.

    python Compiler/bridge/derive_pulses.py -o Compiler/build/pulses.json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

I2 = np.eye(2, dtype=complex)
X = np.array([[0, 1], [1, 0]], dtype=complex)
Y = np.array([[0, -1j], [1j, 0]], dtype=complex)
Z = np.array([[1, 0], [0, -1]], dtype=complex)
PI = np.pi


def R(theta, phi):
    return np.cos(theta / 2) * I2 - 1j * np.sin(theta / 2) * (
        np.cos(phi) * X + np.sin(phi) * Y)


def RZ(lam):
    return np.array([[np.exp(-1j * lam / 2), 0], [0, np.exp(1j * lam / 2)]])


def MS(theta):
    return np.cos(theta / 2) * np.eye(4) - 1j * np.sin(theta / 2) * np.kron(X, X)


def Rx(t):
    return R(t, 0.0)


def Ry(t):
    return R(t, PI / 2)


def u3(theta, phi, lam):
    return np.array([
        [np.cos(theta / 2), -np.exp(1j * lam) * np.sin(theta / 2)],
        [np.exp(1j * phi) * np.sin(theta / 2),
         np.exp(1j * (phi + lam)) * np.cos(theta / 2)],
    ])


# ------------------------------------------------------------------ the two identities


def u3_as_pulse(theta, phi, lam):
    """u3(θ,φ,λ) = e^{i(φ+λ)/2} · RZ(φ+λ) · R(θ, π/2 − λ) -- one physical pulse."""
    return np.exp(1j * (phi + lam) / 2) * (RZ(phi + lam) @ R(theta, PI / 2 - lam))


def cx_as_pulses():
    """CX = e^{-iπ/4} · [Ry(−π/2)⊗I]·[Rx(−π/2)⊗Rx(−π/2)]·MS(π/2)·[Ry(π/2)⊗I]

    Matrix order; the rightmost factor is the first pulse in time.  The phase is
    e^{-iπ/4}, not e^{+iπ/4} -- the sign is exactly the kind of thing this script
    exists to pin down rather than remember.
    """
    return np.exp(-1j * PI / 4) * (
        np.kron(Ry(-PI / 2), I2)
        @ np.kron(Rx(-PI / 2), Rx(-PI / 2))
        @ MS(PI / 2)
        @ np.kron(Ry(PI / 2), I2))


# ------------------------------------------------------------------ named gates
#
# Every one is a u3, so every one inherits the single identity above.  The table records
# the (theta, phi, lam) triple; the pulse parameters follow by formula, which is why the
# compiler needs no per-gate special cases.

NAMED_U3 = {
    "id": (0, 0, 0),
    "x": (PI, 0, PI),
    "y": (PI, PI / 2, PI / 2),
    "z": (0, 0, PI),
    "h": (PI / 2, 0, PI),
    "s": (0, 0, PI / 2),
    "sdg": (0, 0, -PI / 2),
    "t": (0, 0, PI / 4),
    "tdg": (0, 0, -PI / 4),
    "sx": (PI / 2, -PI / 2, PI / 2),
    "sxdg": (PI / 2, PI / 2, -PI / 2),
}

TARGETS = {
    "id": I2,
    "x": X,
    "y": Y,
    "z": Z,
    "h": (X + Z) / np.sqrt(2),
    "s": np.diag([1, 1j]).astype(complex),
    "sdg": np.diag([1, -1j]).astype(complex),
    "t": np.diag([1, np.exp(1j * PI / 4)]).astype(complex),
    "tdg": np.diag([1, np.exp(-1j * PI / 4)]).astype(complex),
    "sx": 0.5 * np.array([[1 + 1j, 1 - 1j], [1 - 1j, 1 + 1j]]),
    "sxdg": 0.5 * np.array([[1 - 1j, 1 + 1j], [1 + 1j, 1 - 1j]]),
}

CX = np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 0, 1], [0, 0, 1, 0]], dtype=complex)


def upto_phase(u, v, tol=1e-10):
    """Return α with u = e^{iα}·v, or None."""
    flat = np.abs(v).ravel()
    k = int(np.argmax(flat))
    i, j = divmod(k, v.shape[1])
    if abs(v[i, j]) < tol:
        return None
    ratio = u[i, j] / v[i, j]
    if abs(abs(ratio) - 1) > tol or np.max(np.abs(u - ratio * v)) > tol:
        return None
    return float(np.angle(ratio))


def as_pi_multiple(a, denom=8):
    for num in range(-2 * denom, 2 * denom + 1):
        if abs(a - num * PI / denom) < 1e-9:
            if num == 0:
                return "0"
            g = math.gcd(abs(num), denom)
            n, d = num // g, denom // g
            return f"pi" if (n, d) == (1, 1) else f"{n}*pi/{d}" if d != 1 else f"{n}*pi"
    return f"{a!r}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--trials", type=int, default=20000)
    args = ap.parse_args(argv)

    rng = np.random.default_rng(11)
    bad = 0

    # --- the general single-qubit identity, on random angles -----------------------
    worst = 0.0
    for _ in range(args.trials):
        th, ph, la = rng.uniform(-4 * PI, 4 * PI, 3)
        err = np.max(np.abs(u3_as_pulse(th, ph, la) - u3(th, ph, la)))
        worst = max(worst, err)
    ok = worst < 1e-12
    print(f"u3(θ,φ,λ) = e^{{i(φ+λ)/2}}·RZ(φ+λ)·R(θ, π/2−λ)")
    print(f"  {args.trials:,} random angle triples, max error {worst:.3e}  "
          f"{'OK' if ok else 'FAIL'}")
    bad += 0 if ok else 1

    # --- every named gate, as a u3 -------------------------------------------------
    print(f"\n{'gate':<6} {'(theta, phi, lam)':<34} {'phase':<10} status")
    print("-" * 70)
    table = {"u3_identity": {"phase": "(phi+lam)/2", "rz": "phi+lam",
                             "pulse_theta": "theta", "pulse_phi": "pi/2 - lam"},
             "gates": {}}
    for name, (th, ph, la) in NAMED_U3.items():
        built = u3(th, ph, la)
        a = upto_phase(built, TARGETS[name])
        good = a is not None
        trip = f"({as_pi_multiple(th)}, {as_pi_multiple(ph)}, {as_pi_multiple(la)})"
        print(f"{name:<6} {trip:<34} {as_pi_multiple(a) if good else '-':<10} "
              f"{'OK' if good else 'MISMATCH'}")
        if good:
            table["gates"][name] = {
                "u3": [th, ph, la],
                "u3_sym": [as_pi_multiple(th), as_pi_multiple(ph), as_pi_multiple(la)],
                "phase": as_pi_multiple(a),
                "pulses": 1,
            }
        else:
            bad += 1

    # --- CX ------------------------------------------------------------------------
    err = np.max(np.abs(cx_as_pulses() - CX))
    ok = err < 1e-12
    print(f"\nCX = e^{{-iπ/4}}·[Ry(−π/2)⊗I]·[Rx(−π/2)⊗Rx(−π/2)]·MS(π/2)·[Ry(π/2)⊗I]")
    print(f"  max error {err:.3e}  {'OK' if ok else 'FAIL'}")
    bad += 0 if ok else 1
    if ok:
        table["cx"] = {
            "phase": "-pi/4",
            "ms_gates": 1,
            "pulses": 4,
            "sequence": [
                {"gate": "R", "theta": "pi/2", "phi": "pi/2", "qubit": 0},
                {"gate": "MS", "theta": "pi/2", "qubits": [0, 1]},
                {"gate": "R", "theta": "-pi/2", "phi": "0", "qubit": 0},
                {"gate": "R", "theta": "-pi/2", "phi": "0", "qubit": 1},
                {"gate": "R", "theta": "-pi/2", "phi": "pi/2", "qubit": 0},
            ],
        }

    # --- the generators are involutions, which is what licenses the closed forms ----
    print("\ngenerator involutions (the reason no matrix exponential is needed):")
    for label, m in [("X⊗X", np.kron(X, X)), ("X", X), ("Y", Y),
                     ("cos φ·X + sin φ·Y at φ=0.7",
                      np.cos(0.7) * X + np.sin(0.7) * Y)]:
        e = np.max(np.abs(m @ m - np.eye(m.shape[0])))
        print(f"  ({label})² = I   max error {e:.3e}")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(table, indent=1) + "\n", encoding="utf-8")
        print(f"\n-> {args.out}")

    print("\nDERIVE PASS" if bad == 0 else f"\nDERIVE FAIL ({bad})")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
