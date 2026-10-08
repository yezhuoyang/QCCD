"""Check that circuits.py's rewrite is exact.

    python Reproduce/moses2023/check_rewrites.py <artifact clone> [--qv N] [--rcs N] [--mb N]

1. Identities: every rewrite rule against the gate's definition, up to global phase, on
   random angles (2x2 / 4x4 unitaries).
2. Against the artifact's own ideal outputs, which test the DEFINITIONS too (an assumed
   convention that differs from Quantinuum's would fail here, not in step 1):
   qv   the ideal heavy-output probability `heavy_ideal[i]` (16-qubit statevector)
   rcs  the ideal probability of each sampled bitstring, `bitstring_probs`, at N = 16 (the
        N = 32 circuits use the same gates and the same rewrite; 2^32 amplitudes do not fit)
   mb   the mirror circuits' expected output `surv_state` (Clifford: stim tableau)
Prints one line per check and exits non-zero on any failure.
"""

from __future__ import annotations

import ast
import json
import math
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from circuits import PI, STMT, ev, rewrite  # noqa: E402

I2 = np.eye(2, dtype=complex)
X = np.array([[0, 1], [1, 0]], dtype=complex)
Y = np.array([[0, -1j], [1j, 0]], dtype=complex)
Z = np.diag([1, -1]).astype(complex)
H = np.array([[1, 1], [1, -1]], dtype=complex) / math.sqrt(2)
S = np.diag([1, 1j])


def expm_herm(h: np.ndarray, t: float) -> np.ndarray:
    """exp(-i t/2 h) for a Hermitian h with h @ h = I (a Pauli or Pauli product)."""
    return math.cos(t / 2) * np.eye(h.shape[0]) - 1j * math.sin(t / 2) * h


def u3(t, p, l):
    return np.array([[math.cos(t / 2), -np.exp(1j * l) * math.sin(t / 2)],
                     [np.exp(1j * p) * math.sin(t / 2), np.exp(1j * (p + l)) * math.cos(t / 2)]])


ONE_Q = {
    "h": lambda: H, "x": lambda: X, "y": lambda: Y, "z": lambda: Z, "s": lambda: S,
    "sdg": lambda: S.conj().T, "t": lambda: np.diag([1, np.exp(1j * PI / 4)]),
    "tdg": lambda: np.diag([1, np.exp(-1j * PI / 4)]), "id": lambda: I2,
    "rx": lambda t: expm_herm(X, t), "ry": lambda t: expm_herm(Y, t),
    "rz": lambda t: expm_herm(Z, t), "u1": lambda l: np.diag([1, np.exp(1j * l)]),
    "u3": u3, "u": u3,
}
XX, YY, ZZ = np.kron(X, X), np.kron(Y, Y), np.kron(Z, Z)


def two_q(name, ps):
    if name == "rxx":
        return expm_herm(XX, ps[0])
    if name == "cx":
        return np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 0, 1], [0, 0, 1, 0]], dtype=complex)
    raise KeyError(name)


def same_up_to_phase(a, b, tol=1e-9) -> float:
    k = np.vdot(a.flatten(), b.flatten())
    ph = k / abs(k) if abs(k) > 1e-12 else 1.0
    return float(np.max(np.abs(a * ph - b)))


# -------------------------------------------------------------------- a tiny simulator

def parse(text):
    """[(name, params, [qubit index])] over the single quantum register, plus its size
    and the measurement map {qubit: clbit}."""
    nq, ops, meas = None, [], {}
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("qreg"):
            nq = int(re.search(r"\[(\d+)\]", s).group(1))
            continue
        if not s or s.startswith(("OPENQASM", "include", "creg", "barrier")):
            continue
        if s.startswith("measure"):
            q, c = re.findall(r"\[(\d+)\]", s)
            meas[int(q)] = int(c)
            continue
        m = STMT.match(s)
        name, params, args = m.group(1), m.group(2), m.group(3)
        ps = [ev(p) for p in params.split(",")] if params else []
        qs = [int(re.search(r"\[(\d+)\]", a).group(1)) for a in args.split(",")]
        ops.append((name, ps, qs))
    return nq, ops, meas


def simulate(text) -> tuple[np.ndarray, dict]:
    nq, ops, meas = parse(text)
    psi = np.zeros((2,) * nq, dtype=complex)
    psi[(0,) * nq] = 1.0
    for name, ps, qs in ops:
        if len(qs) == 1:
            u = ONE_Q[name](*ps)
            psi = np.moveaxis(np.tensordot(u, psi, axes=([1], [qs[0]])), 0, qs[0])
        else:
            u = two_q(name, ps).reshape(2, 2, 2, 2)
            psi = np.moveaxis(np.tensordot(u, psi, axes=([2, 3], qs)), [0, 1], qs)
    # axis k is qubit k; flatten so that index bit (nq-1-k) is qubit k
    return np.abs(psi.reshape(-1)) ** 2, {"nq": nq, "meas": meas}


def heavy_output_probability(p: np.ndarray) -> float:
    med = np.median(p)
    return float(p[p > med].sum())


# -------------------------------------------------------------------- the checks

def check_identities(rng) -> list[tuple[str, float]]:
    out = []
    for _ in range(50):
        t, f = rng.uniform(-4 * PI, 4 * PI, 2)
        want = expm_herm(math.cos(f) * X + math.sin(f) * Y, t)
        got = u3(t, f - PI / 2, PI / 2 - f)
        out.append(("U1q(t,f) == u3(t, f-pi/2, pi/2-f)", same_up_to_phase(got, want)))
    for _ in range(50):
        a, b, c = rng.uniform(-4 * PI, 4 * PI, 3)
        want = expm_herm(XX, a) @ expm_herm(YY, b) @ expm_herm(ZZ, c)
        for name, text in (("RZZ", f"RZZ({c}) q[0],q[1];"),
                           ("Rxxyyzz", f"Rxxyyzz({a},{b},{c}) q[0],q[1];")):
            src = f'OPENQASM 2.0;\ninclude "hqslib1.inc";\nqreg q[2];\n{text}\n'
            rw, _ = rewrite(src)
            u = np.eye(4, dtype=complex)
            for nm, ps, qs in parse(rw)[1]:
                g = ONE_Q[nm](*ps) if len(qs) == 1 else two_q(nm, ps)
                if len(qs) == 1:
                    g = np.kron(g, I2) if qs[0] == 0 else np.kron(I2, g)
                u = g @ u
            ref = expm_herm(ZZ, c) if name == "RZZ" else want
            out.append((f"{name} rewrite == its definition", same_up_to_phase(u, ref)))
    src = 'OPENQASM 2.0;\ninclude "hqslib1.inc";\nqreg q[2];\nZZ q[0],q[1];\n'
    u = np.eye(4, dtype=complex)
    for nm, ps, qs in parse(rewrite(src)[0])[1]:
        g = ONE_Q[nm](*ps) if len(qs) == 1 else two_q(nm, ps)
        if len(qs) == 1:
            g = np.kron(g, I2) if qs[0] == 0 else np.kron(I2, g)
        u = g @ u
    out.append(("ZZ rewrite == U_ZZ(pi/2) = exp(-i pi/4 ZZ)",
                same_up_to_phase(u, expm_herm(ZZ, PI / 2))))
    return out


def _aer_probs(text: str) -> tuple[np.ndarray, dict]:
    """Probabilities as an array with axis q = qubit q (Aer statevector; 16 qubits)."""
    from qiskit import QuantumCircuit
    from qiskit_aer import AerSimulator

    nq, _, meas = parse(text)
    qc = QuantumCircuit.from_qasm_str(text)
    qc.remove_final_measurements()
    qc.save_statevector()
    sv = np.asarray(AerSimulator(method="statevector").run(qc).result().get_statevector())
    p = (np.abs(sv) ** 2).reshape((2,) * nq)           # little-endian: axis 0 = qubit nq-1
    return np.transpose(p, list(range(nq - 1, -1, -1))), {"nq": nq, "meas": meas}


def _scale_rxxyyzz(text: str, sx: float, sy: float, sz: float) -> str:
    """The same circuit read under a DIFFERENT angle convention, to show the check can fail."""
    def sub(m):
        a, b, c = (ev(v) for v in m.group(1).split(","))
        return f"Rxxyyzz({a * sx},{b * sy},{c * sz})"
    return re.sub(r"Rxxyyzz\(([^)]*)\)", sub, text)


def check_qv(art: Path, n: int) -> list[tuple[str, float]]:
    """The executed QV circuits (`qv_circs`, TK2 written as Rxxyyzz) are APPROXIMATE
    compilations of the ideal ones (`qv_circs_nomeas`, u3 + cx): pytket zeroed ~63 small
    components per circuit, so no exact reference exists for them.  What can be checked:
    the classical fidelity between the rewritten executed circuit and the ideal circuit is
    high under the convention the rewrite assumes, and low under the alternatives.  The
    heavy-output probability of each ideal circuit is compared with `heavy_ideal`."""
    d = json.loads((art / "quantum_volume/data/n16_H2-1_raw_results.json").read_text())
    out = []
    hop = []
    for i in range(n):
        # u3 + cx only; rewrite() just drops the hqslib1_dev include.  qubit k -> clbit k
        ideal, _ = _aer_probs(rewrite(d["qv_circs_nomeas"][i])[0])
        hop.append(heavy_output_probability(ideal.reshape(-1)))

        def fid(src: str) -> float:
            p, info = _aer_probs(rewrite(src)[0])
            perm = [None] * info["nq"]
            for q, c in info["meas"].items():
                perm[c] = q
            pc = np.transpose(p, perm)                         # axis c = clbit c
            return float(np.sqrt(pc * ideal).sum() ** 2)

        ours = fid(d["qv_circs"][i])
        alts = {"angles x2": fid(_scale_rxxyyzz(d["qv_circs"][i], 2, 2, 2)),
                "angles x1/2": fid(_scale_rxxyyzz(d["qv_circs"][i], .5, .5, .5)),
                "YY sign flipped": fid(_scale_rxxyyzz(d["qv_circs"][i], 1, -1, 1))}
        print(f"    qv_n16_{i:03d}: classical fidelity to the ideal circuit {ours:.4f}; "
              + ", ".join(f"{k} {v:.4f}" for k, v in alts.items()))
        margin = ours - max(alts.values())
        out.append(("qv executed circuit (rewritten) is the ideal one up to pytket's "
                    "approximation: fidelity > 0.9 and > every alternative convention + 0.2",
                    0.0 if ours > 0.9 and margin > 0.2 else 1.0))
    for i in range(n):
        j = min(range(n), key=lambda k: abs(hop[k] - d["heavy_ideal"][i]))
        print(f"    heavy_ideal[{i}] = {d['heavy_ideal'][i]:.9f} is the ideal heavy-output "
              f"probability of qv_circs_nomeas[{j}] ({hop[j]:.9f})")
        out.append(("qv heavy_ideal[i] equals the heavy-output probability of some ideal "
                    "circuit among the ones simulated", abs(hop[j] - d["heavy_ideal"][i])))
    return out


def check_rcs(art: Path, n: int) -> list[tuple[str, float]]:
    d = json.loads((art / "random_circuit_sampling/data/RCS_N16_Experiment_10Circuits.json")
                   .read_text())
    out = []
    for k in [str(i) for i in range(n)]:
        p, info = simulate(rewrite(d["qasm_compiled"][k])[0])
        nq, meas = info["nq"], info["meas"]
        q_of_c = {c: q for q, c in meas.items()}
        best = None
        for order in ("c0_first", "c0_last"):
            err = 0.0
            for bits, want in d["bitstring_probs"][k].items():
                cb = bits if order == "c0_first" else bits[::-1]     # cb[j] = clbit j
                # sum over unmeasured qubits (none here, but keep it honest)
                fixed = {q_of_c[j]: int(cb[j]) for j in range(len(cb)) if j in q_of_c}
                idx = [slice(None)] * nq
                for q, v in fixed.items():
                    idx[q] = v
                got = float(p.reshape((2,) * nq)[tuple(idx)].sum())
                err = max(err, abs(got - want) / max(want, 1e-30))
            best = (order, err) if best is None or err < best[1] else best
        out.append((f"rcs_n16_{k} sampled-bitstring ideal probabilities "
                    f"(relative, bit order {best[0]})", best[1]))
    return out


def check_mb(art: Path, n: int) -> list[tuple[str, float]]:
    import stim

    d = json.loads((art / "mirror_benchmarking/data/MB_N32.json").read_text())
    keys = [k for k in sorted(d["qasm"], key=lambda k: ast.literal_eval(k))
            if ast.literal_eval(k)[0] == 10][:n]
    out = []
    quarter = {"rx": ("I", "SQRT_X", "X", "SQRT_X_DAG"), "ry": ("I", "SQRT_Y", "Y", "SQRT_Y_DAG")}
    names = {"h": "H", "x": "X", "y": "Y", "z": "Z", "s": "S", "sdg": "S_DAG"}
    for k in keys:
        nq, ops, meas = parse(rewrite(d["qasm"][k])[0])
        c = stim.Circuit()
        for nm, ps, qs in ops:
            if nm in names:
                c.append(names[nm], qs)
            elif nm in quarter:
                r = ps[0] / (PI / 2)
                assert abs(r - round(r)) < 1e-9, (nm, ps)
                g = quarter[nm][round(r) % 4]
                if g != "I":
                    c.append(g, qs)
            elif nm == "rxx":
                r = ps[0] / (PI / 2)
                assert abs(r - 1) < 1e-9, ps
                c.append("SQRT_XX", qs)
            else:
                raise ValueError(nm)
        order = sorted(meas, key=lambda q: meas[q])               # clbit 0 first
        c.append("M", order)
        shots = c.compile_sampler().sample(8)
        assert (shots == shots[0]).all(), "not deterministic"
        got = "".join("1" if b else "0" for b in shots[0])         # clbit 0 first
        want = d["surv_state"][k]
        ok = got == want or got[::-1] == want
        out.append((f"mb_n32 {k} deterministic output == surv_state "
                    f"({'c0 first' if got == want else 'c0 last' if ok else 'MISMATCH'})",
                    0.0 if ok else 1.0))
    return out


def main(argv: list[str]) -> int:
    art = Path(argv[0])
    opt = {a.lstrip("-"): int(b) for a, b in zip(argv[1::2], argv[2::2])}
    rng = np.random.default_rng(20230516)
    results = check_identities(rng)
    results += check_mb(art, opt.get("mb", 10))
    results += check_rcs(art, opt.get("rcs", 3))
    results += check_qv(art, opt.get("qv", 10))
    bad = 0
    seen = {}
    for name, err in results:
        key = re.sub(r"_\d{3}|\(\d+, \d+\)|n16_\d+", "#", name)
        seen.setdefault(key, []).append(err)
    for key, errs in seen.items():
        worst = max(errs)
        flag = "ok " if worst < 1e-6 else "BAD"
        bad += worst >= 1e-6
        print(f"{flag} {len(errs):3d} x  worst {worst:.2e}  {key}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
