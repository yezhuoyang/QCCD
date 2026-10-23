"""Does this QASM circuit measure exactly the code's stabilizers?  A check upstream of R10.

R10 -- discharged by the proved Lean checker -- says *the compiled program implements the
QASM circuit*.  It says nothing about whether that circuit is a correct syndrome-extraction
round, and a scheduler can break that without breaking any gate: an X check and a Z check
that share two data qubits must touch both in the same relative order, or the Z ancilla
picks up one factor of Z on the X ancilla and its outcome is random.  A metric that counts
expected faults scores the broken circuit identically to the right one, so this has to be
checked separately, and it is cheap.

The test, in stim's tableau simulator:

  1. run the round once on |0>^144 -- this projects the data onto a joint eigenstate of
     whatever the circuit measures;
  2. run it again with fresh ancillas: every outcome must repeat (the circuit measures a
     commuting set, and measures the SAME set both times);
  3. for every data qubit d and both P in {X, Z}: apply P_d, run again, and require that
     exactly the checks the code says anticommute with P_d flip.

(2) and (3) together pin each measured observable's data part down to the intended check:
the flip pattern under single-qubit probes determines a Pauli string exactly.

    python Codesign/scripts/esm_check.py Compiler/examples/bb144_esm.qasm
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qccd.codes.bb import gross_code  # noqa: E402

_GATE = re.compile(r"^\s*(h|cx|measure|reset)\s+q\[(\d+)\](?:\s*,\s*q\[(\d+)\])?"
                   r"(?:\s*->\s*c\[(\d+)\])?\s*;")


def parse(path: Path) -> tuple[int, list[tuple]]:
    """(n_qubits, ops) with ops as ('h', q) | ('cx', c, t) | ('reset', q) | ('measure', q, k)."""
    n_q = None
    ops: list[tuple] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*qreg\s+q\[(\d+)\]", line)
        if m:
            n_q = int(m.group(1))
            continue
        m = _GATE.match(line)
        if not m:
            continue
        g, a, b, k = m.group(1), int(m.group(2)), m.group(3), m.group(4)
        if g == "cx":
            ops.append(("cx", a, int(b)))
        elif g == "measure":
            ops.append(("measure", a, int(k)))
        else:
            ops.append((g, a))
    if n_q is None:
        raise SystemExit(f"{path}: no qreg")
    return n_q, ops


def run_round(sim, ops: list[tuple], n_checks: int, ancillas: set[int]) -> list[bool]:
    """One ESM round with fresh ancillas; returns the outcome bit per check index."""
    for a in ancillas:
        sim.reset(a)
    out = [False] * n_checks
    for op in ops:
        if op[0] == "h":
            sim.h(op[1])
        elif op[0] == "cx":
            sim.cx(op[1], op[2])
        elif op[0] == "reset":
            sim.reset(op[1])
        else:
            out[op[2]] = sim.measure(op[1])
    return out


def check(path: Path, code=None, verbose: bool = True) -> dict:
    import stim

    code = code or gross_code()
    n_q, ops = parse(path)
    n_data = code.n
    n_checks = len(code.checks)
    ancillas = {op[1] for op in ops if op[0] == "measure"}
    measured = {op[2] for op in ops if op[0] == "measure"}
    problems: list[str] = []
    if measured != set(range(n_checks)):
        problems.append(f"measured check indices {sorted(measured)[:5]}... != 0..{n_checks - 1}")
    if any(a < n_data for a in ancillas):
        problems.append("a data qubit is measured")

    sim = stim.TableauSimulator()
    first = run_round(sim, ops, n_checks, ancillas)
    second = run_round(sim, ops, n_checks, ancillas)
    if first != second:
        bad = [k for k in range(n_checks) if first[k] != second[k]]
        problems.append(f"{len(bad)} outcomes do not repeat: checks {bad[:8]}...")

    # the syndrome map the code predicts
    x_of = {q: set() for q in range(n_data)}  # checks that flip under X_q (Z checks)
    z_of = {q: set() for q in range(n_data)}  # checks that flip under Z_q (X checks)
    for k, c in enumerate(code.checks):
        for q in c.members:
            (x_of if c.type == "Z" else z_of)[q].add(k)

    wrong = 0
    for q in range(n_data):
        for pauli, want in (("X", x_of[q]), ("Z", z_of[q])):
            if pauli == "X":
                sim.x(q)
            else:
                sim.z(q)
            got = run_round(sim, ops, n_checks, ancillas)
            flipped = {k for k in range(n_checks) if got[k] != second[k]}
            if flipped != want:
                wrong += 1
                if len(problems) < 12:
                    problems.append(
                        f"{pauli}_{q}: flips {sorted(flipped)} but the code says {sorted(want)}")
            # undo the probe so the next one is applied to the same state
            if pauli == "X":
                sim.x(q)
            else:
                sim.z(q)
            # the probe rounds also re-project; run once more to restore `second`
            second = run_round(sim, ops, n_checks, ancillas)

    ok = not problems
    if verbose:
        print(f"{path.name}: {n_q} qubits, {len(ops)} ops, {len(ancillas)} ancillas, "
              f"{sum(1 for o in ops if o[0] == 'reset')} resets")
        print(f"  repeatable: {'yes' if first == second else 'NO'}; "
              f"syndrome map: {2 * n_data - wrong}/{2 * n_data} probes correct")
        for p in problems:
            print(f"  ! {p}")
        print("  -> " + ("measures exactly the stabilizers" if ok else "NOT a valid ESM round"))
    return {"ok": ok, "repeatable": first == second, "probes_wrong": wrong,
            "n_resets": sum(1 for o in ops if o[0] == "reset"),
            "n_ancillas": len(ancillas), "problems": problems}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("qasm", nargs="+")
    a = ap.parse_args(argv)
    rc = 0
    for p in a.qasm:
        if not check(Path(p))["ok"]:
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
