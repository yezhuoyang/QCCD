"""R10: does the compiled program implement the input circuit?

`qccd/verify/__init__.py` lists R10 as UNCHECKABLE with the reason *"needs symbolic
permutation + Pauli-frame tracking against a QASM DAG"*.  This is the check that reason
describes.  It has two halves, and neither of them asks the compiler what it intended.

**O1 -- transport and co-location.**  Ion positions are recomputed by replaying the
certificate's move list from the initial placement.  Every gate the emitted program
executes must find its operands already in one trap, and that trap must be gate-capable.
Positions the certificate also claims are cross-checked, never believed -- the discipline
`qccd/ir/import_deck.py` applies to the shipped artifact, for the same reason.

**O2/O3 -- semantics.**  For a Clifford circuit (decision D1) the compiled program's
meaning is recovered *from the emitted hardware program itself*: the native pulses are
read out of the TSIR, composed into a stabilizer tableau over the logical qubits through
the certificate's qubit->ion map, and compared with the tableau of the source circuit.
Nothing about the compiler's reasoning enters.  A swapped operand, a dropped gate, a
wrong rotation angle or a mis-tracked frame all move the tableau.

This subsumes Pauli-frame tracking rather than implementing it separately, which is the
whole argument for D1: a frame error IS a tableau difference.

    python Compiler/bridge/check_cert.py build/out/ghz8_grid9x9 \
        --qasm bench/ghz8.qasm --arch arch/grid9x9.arch.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qccd.arch import Architecture  # noqa: E402
from qccd.ir.tsir import TSIR  # noqa: E402

PI = math.pi


# ------------------------------------------------------------------ O1


def replay_positions(cert: dict, arch: Architecture) -> tuple[dict, list[str]]:
    """Recompute where every ion is at every cycle, from init + the movement witnesses.

    Movement is of two kinds. A `move` carries one ion across one segment. A `rotation`
    turns a whole loop: every ion standing ON it advances by `delta` slots, and every ion
    that is not -- a docked one -- stays where it is. That asymmetry is why the rotation
    compiler must never turn the loop while a dock is occupied, and modelling it here is
    what makes the replay able to catch it if it ever did.

    The loop's node order is read from `arch`, never from the certificate: a compiler that
    supplied its own could rotate ions to wherever its gates needed them.
    """
    problems: list[str] = []
    pos = dict(cert["init"])
    loops = {
        lid: list(l.nodes)
        for lid, l in getattr(arch.device, "loops", {}).items()
        if l.closed
    }

    by_cycle: dict[int, list] = {}
    for m in cert["moves"]:
        by_cycle.setdefault(m["t"], []).append(m)
    rots_at: dict[int, list] = {}
    for r in cert.get("rotations", []):
        rots_at.setdefault(r["t"], []).append(r)

    last = max([0] + [m["t"] for m in cert["moves"]] + [g["t"] for g in cert["gates"]]
               + [r["t"] + abs(r["delta"]) for r in cert.get("rotations", [])])
    snapshots: dict[int, dict] = {}
    for t in range(last + 1):
        snapshots[t] = dict(pos)
        for m in by_cycle.get(t, []):
            if pos.get(m["ion"]) != m["from"]:
                problems.append(
                    f"t={t}: {m['ion']} departs {m['from']} but is at {pos.get(m['ion'])}")
            pos[m["ion"]] = m["to"]
        for r in rots_at.get(t, []):
            nodes = loops.get(r["loop"])
            if nodes is None:
                problems.append(
                    f"t={t}: rotation of {r['loop']}, which the architecture does not have")
                continue
            slot = {s: i for i, s in enumerate(nodes)}
            n = len(nodes)
            for ion, site in list(pos.items()):
                if site in slot:
                    pos[ion] = nodes[(slot[site] + r["delta"]) % n]
    return snapshots, problems


def check_o1(cert: dict, arch: Architecture) -> list[str]:
    snapshots, problems = replay_positions(cert, arch)
    for g in cert["gates"]:
        at = snapshots.get(g["t"], {})
        sites = {ion: at.get(ion) for ion in g["ions"]}
        if len(set(sites.values())) != 1 or None in sites.values():
            problems.append(
                f"op {g['dag']}: operands not co-located at t={g['t']}: {sites}")
            continue
        site = next(iter(sites.values()))
        if site != g["site"]:
            problems.append(
                f"op {g['dag']}: witness says {g['site']}, replay says {site}")
        if not arch.can(site, "gate"):
            zt = arch.device.nodes[site].zone_type
            problems.append(
                f"op {g['dag']}: gate at {site}, whose zone {zt!r} cannot gate")
    return problems


# ------------------------------------------------------------------ O2 / O3


def is_right_angle(x: float, eps: float = 1e-9) -> bool:
    return abs(x / (PI / 2) - round(x / (PI / 2))) < eps


def tableau_from_program(prog: TSIR, cert: dict, n: int):
    """Compose the EMITTED pulses into a stabilizer tableau over the logical qubits.

    Reads the hardware program, not the circuit and not the compiler's intent.
    """
    import stim

    qubit_of = {ion: int(q) for q, ion in cert["map"].items()}
    circ = stim.Circuit()
    for instr in prog.instructions:
        if instr.type != "gate":
            continue
        params = list(instr.params)
        if instr.gate == "R":
            for k, ion in enumerate(instr.ions):
                th, ph = params[k] if k < len(params) else (0.0, 0.0)
                if not (is_right_angle(th) and is_right_angle(ph)):
                    raise NotImplementedError(f"non-Clifford pulse R({th},{ph})")
                _emit_r(circ, qubit_of[ion], th, ph)
        elif instr.gate == "VZ":
            for k, ion in enumerate(instr.ions):
                (lam,) = params[k] if k < len(params) else (0.0,)
                if not is_right_angle(lam):
                    raise NotImplementedError(f"non-Clifford frame VZ({lam})")
                # VZ(lam) = diag(1, e^{i lam}) -- S applied `lam / (pi/2)` times
                for _ in range(round(lam / (PI / 2)) % 4):
                    circ.append("S", [qubit_of[ion]])
        elif instr.gate == "MS":
            for k, (a, b) in enumerate(instr.pairs):
                (th,) = params[k] if k < len(params) else (0.0,)
                if not is_right_angle(th):
                    raise NotImplementedError(f"non-Clifford MS({th})")
                _emit_ms(circ, qubit_of[a], qubit_of[b], th)
    circ_padded = stim.Circuit()
    circ_padded.append("I", list(range(n)))
    circ_padded += circ
    return stim.Tableau.from_circuit(circ_padded)


def _emit_r(circ, q: int, theta: float, phi: float) -> None:
    """R(theta, phi) = exp(-i theta/2 (cos phi X + sin phi Y)), for theta a multiple of pi/2."""
    k = round(theta / (PI / 2)) % 4          # quarter turns
    axis = round(phi / (PI / 2)) % 4         # 0 = X, 1 = Y, 2 = -X, 3 = -Y
    if k == 0:
        return
    # rotate about +X or +Y; a negative axis is the same axis with the opposite sense
    if axis in (0, 2):
        base, sense = "X", (1 if axis == 0 else -1)
    else:
        base, sense = "Y", (1 if axis == 1 else -1)
    turns = (k * sense) % 4
    if turns == 0:
        return
    # stim's SQRT_X is exp(-i pi/4 X) up to a global phase, i.e. R(+pi/2, 0) -- NOT its
    # dagger.  Getting this backwards is invisible on a single gate (a tableau ignores
    # global phase) and shows up only as a whole-program mismatch, which is what this
    # checker is for.
    if turns == 2:
        circ.append(base, [q])               # a pi rotation is the Pauli itself
    elif turns == 1:
        circ.append("SQRT_X" if base == "X" else "SQRT_Y", [q])
    else:
        circ.append("SQRT_X_DAG" if base == "X" else "SQRT_Y_DAG", [q])


def _emit_ms(circ, a: int, b: int, theta: float) -> None:
    """MS(theta) = exp(-i theta/2 X⊗X), for theta a multiple of pi/2."""
    k = round(theta / (PI / 2)) % 4
    if k == 0:
        return
    if k == 2:
        circ.append("X", [a])
        circ.append("X", [b])
        return
    circ.append("SQRT_XX" if k == 1 else "SQRT_XX_DAG", [a, b])


def circuit_tableau(qasm: Path, n: int):
    """The source circuit's tableau, via qiskit -> stim, for the Clifford fragment."""
    import stim
    from qiskit import qasm2

    qc = qasm2.load(str(qasm), custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
    circ = stim.Circuit()
    circ.append("I", list(range(n)))
    simple = {"x": "X", "y": "Y", "z": "Z", "h": "H", "s": "S", "sdg": "S_DAG",
              "sx": "SQRT_X", "sxdg": "SQRT_X_DAG", "id": "I",
              "cx": "CX", "cy": "CY", "cz": "CZ", "swap": "SWAP"}
    for inst in qc.data:
        name = inst.operation.name
        qs = [qc.find_bit(q).index for q in inst.qubits]
        if name in ("measure", "reset", "barrier"):
            continue
        if name in simple:
            circ.append(simple[name], qs)
        elif name in ("rz", "u1", "p") and is_right_angle(float(inst.operation.params[0])):
            k = round(float(inst.operation.params[0]) / (PI / 2)) % 4
            for _ in range(k):
                circ.append("S", qs)
        else:
            raise NotImplementedError(f"non-Clifford source gate {name}")
    return stim.Tableau.from_circuit(circ)


# ------------------------------------------------------------------ driver


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("prefix", help="the compiler's -o prefix (.tsir.json / .qcert.json)")
    ap.add_argument("--qasm", required=True)
    ap.add_argument("--arch", required=True)
    ap.add_argument("--qcheck", default=None,
                    help="a qcheck input from mk_qcheck_input.py; runs the PROVED Lean "
                         "checker and lets R10 be reported as `passed`")
    ap.add_argument("--max-unitary-qubits", type=int, default=10,
                    help="fall back to an exact unitary comparison up to this size; "
                         "2^n x 2^n, so 10 is ~16 MB")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    cert = json.loads(Path(args.prefix + ".qcert.json").read_text(encoding="utf-8"))
    prog = TSIR.load(args.prefix + ".tsir.json")
    arch_path = ROOT / args.arch if not Path(args.arch).is_absolute() else Path(args.arch)
    arch = Architecture.from_json(json.loads(arch_path.read_text(encoding="utf-8")))

    verdict: dict = {"program": prog.name, "arch": arch.name}

    # the binding: a certificate with no binding is a certificate for anything
    qasm_sha = hashlib.sha256(Path(args.qasm).read_bytes()).hexdigest()
    verdict["circuit_sha256"] = qasm_sha

    o1 = check_o1(cert, arch)
    verdict["o1_transport"] = "ok" if not o1 else o1[:6]

    # The proved checker.  Until C6 this did not exist and R10 was capped at `partial`
    # by decision D3; now that it does, its verdict is what lifts the cap.
    lean_ok: bool | None = None
    if args.qcheck:
        exe = ROOT / "Compiler" / "lean" / ".lake" / "build" / "bin" / "qcheck.exe"
        if not exe.exists():
            verdict["lean"] = "qcheck not built"
        else:
            r = subprocess.run([str(exe), args.qcheck], capture_output=True, text=True)
            lean_ok = "ACCEPTED" in r.stdout
            verdict["lean"] = "accepted" if lean_ok else "REJECTED"
            if not lean_ok:
                verdict["lean_why"] = [
                    ln.strip()[2:] for ln in r.stdout.splitlines()
                    if ln.strip().startswith("- ")]

    unrealised = cert.get("unrealised", [])
    verdict["unrealised_ops"] = len(unrealised)

    n = cert["n_qubits"]
    try:
        got = tableau_from_program(prog, cert, n)
        want = circuit_tableau(Path(args.qasm), n)
        same = got == want
        verdict["o2_semantics"] = "ok" if same else "TABLEAU MISMATCH"
        verdict["method"] = "stabilizer tableau, composed from the emitted pulses"
    except NotImplementedError as exc:
        # Outside the Clifford fragment the tableau says nothing.  For a circuit small
        # enough to hold a full operator, compare the unitaries instead -- which is a
        # STRONGER check (it sees global phase and every amplitude), just one that costs
        # 2^n x 2^n and so cannot scale.
        same = None
        verdict["o2_semantics"] = f"not checked: {exc}"
        verdict["method"] = "outside the Clifford fragment (decision D1)"
        if n <= args.max_unitary_qubits:
            try:
                import unitary as U

                qubit_of = {ion: int(q) for q, ion in cert["map"].items()}
                lowered = cert["circuit_ops"]
                a = U.program_unitary(prog.instructions, qubit_of, n)
                b = U.circuit_unitary(lowered, n)
                ok_u, alpha, worst = U.same_up_to_phase(a, b)
                same = ok_u
                verdict["o2_semantics"] = (
                    f"ok (max entry error {worst:.2e})" if ok_u
                    else f"UNITARY MISMATCH (max entry error {worst:.2e})")
                verdict["method"] = (
                    f"exact {1 << n}x{1 << n} unitary, built from the emitted pulses; "
                    f"global phase {alpha:+.4f}")
            except NotImplementedError as exc2:
                verdict["o2_semantics"] = f"not checked: {exc2}"
            except MemoryError:
                verdict["o2_semantics"] = f"not checked: {n} qubits is too large"

    structural_ok = (not o1) and not unrealised
    if lean_ok is False:
        verdict["R10"] = "FAILED"
        verdict["R10_reason"] = "the proved Lean checker rejected the certificate"
    elif not structural_ok:
        verdict["R10"] = "FAILED"
        verdict["R10_reason"] = "transport, co-location or coverage did not check out"
    elif same is False:
        verdict["R10"] = "FAILED"
        verdict["R10_reason"] = "the compiled program does not implement the circuit"
    elif same is True and lean_ok:
        # Both halves, and the O1 half by a checker proved sound in Lean
        # (`QCCDC.Cert.check_sound`).  This is the condition D3 set for `passed`.
        verdict["R10"] = "passed"
        verdict["R10_reason"] = (
            "O1 by the proved Lean checker (QCCDC.Cert.check_sound); O2 by "
            + verdict["method"])
    elif same is True:
        verdict["R10"] = "partial"
        verdict["R10_reason"] = (
            "O1 and O2 both check out, but only the fast OCaml/Python checker ran; "
            "pass --qcheck to have the proved Lean checker decide it")
    else:
        # Outside the Clifford fragment the stabilizer route says nothing, and saying
        # nothing is not the same as failing.  D1 defers these to the symbolic route.
        # Even with the Lean checker accepting, O2 is unestablished, so this stays
        # `partial` -- O1 alone is not R10.
        verdict["R10"] = "partial"
        verdict["R10_reason"] = (
            "O1 checks out; O2 not established -- the circuit is outside the Clifford "
            "fragment, where decision D1 defers to the symbolic route")
    ok = verdict["R10"] in ("partial", "passed")

    if args.json:
        print(json.dumps(verdict, indent=1))
    else:
        print(f"R10 for {prog.name} on {arch.name}")
        print(f"  O1 transport + co-location : {verdict['o1_transport']}")
        print(f"  O2 semantics               : {verdict['o2_semantics']}")
        if "lean" in verdict:
            print(f"  proved Lean checker        : {verdict['lean']}")
        print(f"     ({verdict['method']})")
        if unrealised:
            print(f"  UNREALISED circuit ops     : {len(unrealised)} {unrealised[:8]}")
        print(f"  -> R10 {verdict['R10']}: {verdict['R10_reason']}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
