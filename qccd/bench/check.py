"""Grading one compiler output for one pair: is it a correct program for this device, and how good.

The checks, in order; the first that fails decides the pair's status:

  program      the file is a well-formed TSIR within the suite's limits
  native       every gate is one the hardware runs (R, VZ, MS): an abstract CX would escape the
               cost of its decomposition, and the semantic checks below read native pulses
  qubit_map    `meta.qubit_map` names one distinct ion per circuit qubit, all placed by `init`
  rules        the replay under the suite's physics passes every rule it can check (the same
               allowed skips as a board's grade: R7b, R9, and R10 -- decided here instead)
  semantics    the program implements the circuit, WITHOUT trusting a certificate:
                 memory    the extracted stim circuit's detectors and observables are
                           deterministic AND have the source circuit's parity (qccd.qec.check)
                 clifford  the stabilizer tableau of the emitted pulses, through the qubit map,
                           equals the circuit's; each qubit is measured as often as in the circuit
                 general   the exact unitary, up to global phase, for circuits of <= 10 qubits
  metrics      the board numbers (T_jones and the rest), under the suite's physics
  ler          memory pairs: the logical error rate under the suite's noise model and budget

A pair is `valid` when every check passed, `wrong` when the program is malformed, breaks a rule
or computes something else.  (The compiler's own refusals, crashes and timeouts are decided by
the harness before this runs.)
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping

__all__ = ["grade_pair", "NATIVE_GATES", "MAX_UNITARY_QUBITS"]

NATIVE_GATES = {"R", "VZ", "MS"}
MAX_UNITARY_QUBITS = 10
_BRIDGE = Path(__file__).resolve().parents[2] / "Compiler" / "bridge"


def _bridge(name: str):
    """A module of Compiler/bridge (check_cert, unitary): scripts, not a package."""
    if str(_BRIDGE) not in sys.path:
        sys.path.insert(0, str(_BRIDGE))
    spec = importlib.util.spec_from_file_location(f"qccd_bridge_{name}", _BRIDGE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _wrong(checks: dict, stage: str, reason: str) -> dict:
    checks[stage] = {"ok": False, "reason": reason[:1500]}
    return {"status": "wrong", "reason": f"{stage}: {reason}"[:600], "checks": checks}


def grade_pair(pair, program_path: Path, *, physics: Mapping, ler: Mapping | None,
               limits: Mapping | None = None, workdir: Path | None = None) -> dict:
    """Grade one compiled program.  Never raises for a bad program: a bad program is a verdict."""
    from ..arch.device import Architecture
    from ..ir.tsir import TSIR, validate_program
    checks: dict[str, Any] = {}
    limits = dict(limits or {})
    # ---- program
    try:
        doc = json.loads(Path(program_path).read_text(encoding="utf-8"))
        prog = TSIR.from_json(doc)
        errs = list(validate_program(prog))
    except (ValueError, KeyError, TypeError) as exc:
        return _wrong(checks, "program", f"the program does not parse: {exc}")
    if errs:
        return _wrong(checks, "program", "; ".join(str(e) for e in errs[:5]))
    if len(prog) > int(limits.get("max_instructions", 500000)):
        return _wrong(checks, "program", f"{len(prog)} instructions exceeds {limits.get('max_instructions')}")
    checks["program"] = {"ok": True, "instructions": len(prog)}
    # ---- native gate set
    foreign = sorted({i.gate for i in prog.instructions if i.type == "gate" and i.gate not in NATIVE_GATES})
    if foreign:
        return _wrong(checks, "native", f"gates {foreign} are not native (R, VZ, MS)")
    checks["native"] = {"ok": True}
    # ---- qubit map
    qm = (prog.meta or {}).get("qubit_map") if isinstance(prog.meta, Mapping) else None
    if not isinstance(qm, Mapping):
        return _wrong(checks, "qubit_map", "meta.qubit_map is missing: say which ion holds each circuit qubit")
    try:
        qmap = {int(k): str(v) for k, v in qm.items()}
    except (TypeError, ValueError):
        return _wrong(checks, "qubit_map", "meta.qubit_map keys must be qubit numbers")
    placed = set()
    for i in prog.instructions:
        if i.type == "init":
            placed |= set(i.placement)
    if sorted(qmap) != list(range(pair.n_qubits)):
        return _wrong(checks, "qubit_map", f"meta.qubit_map must name qubits 0..{pair.n_qubits - 1}")
    if len(set(qmap.values())) != len(qmap) or not set(qmap.values()) <= placed:
        return _wrong(checks, "qubit_map", "each qubit needs its own ion, placed by the program's init")
    checks["qubit_map"] = {"ok": True}
    # ---- rules (under the suite's physics)
    from ..cost.models import corrected_model
    from ..verify import verify
    from ..workspace.evaluator import ALLOWED_PARTIAL, ALLOWED_SKIPS
    arch = Architecture.from_json(pair.device_doc)
    table = str(physics.get("rank_table", "qccdsim_jones")) if isinstance(physics, Mapping) else "qccdsim_jones"
    try:
        rep = verify(prog, arch, corrected_model(table), check_metrics=False)
    except Exception as exc:        # a program the replay cannot execute is not a valid program
        return _wrong(checks, "rules", f"the replay could not execute it: {type(exc).__name__}: {exc}")
    s = rep.rules.summary()
    failed = sorted(s.get("failed") or [])
    bad_skip = sorted(set(s.get("skipped") or {}) - ALLOWED_SKIPS)
    bad_partial = sorted(set(s.get("partial") or {}) - ALLOWED_PARTIAL)
    if failed or bad_skip or bad_partial:
        first = [f"{v.rule}: {v.message}" for v in rep.rules.violations[:3]]
        return _wrong(checks, "rules", f"failed {failed}" + (f", unchecked {bad_skip + bad_partial}" if
                      (bad_skip or bad_partial) else "") + (" -- " + " | ".join(first) if first else ""))
    checks["rules"] = {"ok": True, "passed": len(s.get("passed") or [])}
    # ---- semantics
    mem_report = None
    try:
        if pair.kind == "memory":
            from ..qec import MemoryExperiment, evaluate_memory
            exp = MemoryExperiment.from_spec(pair.detectors, pair.qasm)
            mem_report = evaluate_memory(prog, arch, exp, noise_id=(ler or {}).get("noise", "qccd-noise@1"),
                                         qubit_map=qmap, ler_budget=_budget(ler), seed=(ler or {}).get("seed"))
            chk = mem_report["check"] or {}
            if not chk.get("ok"):
                return _wrong(checks, "semantics", f"the detectors do not come out as the circuit's: "
                              f"{chk.get('reason') or chk}")
            checks["semantics"] = {"ok": True, "method": "detectors deterministic with the circuit's parity (stim)"}
        elif pair.kind == "clifford":
            _clifford_check(prog, qmap, pair)
            checks["semantics"] = {"ok": True, "method": "stabilizer tableau of the emitted pulses"}
        else:
            if pair.n_qubits > MAX_UNITARY_QUBITS:
                return _wrong(checks, "semantics", f"a non-Clifford circuit of {pair.n_qubits} qubits cannot be "
                              f"checked (the suite keeps these to <= {MAX_UNITARY_QUBITS})")
            _unitary_check(prog, qmap, pair, workdir)
            checks["semantics"] = {"ok": True, "method": "exact unitary up to global phase"}
    except _Mismatch as exc:
        return _wrong(checks, "semantics", str(exc))
    # ---- metrics
    from ..workspace.metrics import compute_metrics
    values, _ = compute_metrics(arch, prog, {"tables": ["qccdsim_jones", "transport_excitation"],
                                             "rank_table": table, "model": "corrected"})
    out = {"status": "valid", "reason": "", "checks": checks,
           "metrics": {k: v for k, v in values.items() if isinstance(v, (int, float))}}
    if mem_report is not None:
        out["ler"] = {"ler": (mem_report.get("ler") or {}).get("ler"),
                      "per_round": (mem_report.get("ler") or {}).get("per_round"),
                      "errors": (mem_report.get("ler") or {}).get("errors"),
                      "shots": (mem_report.get("ler") or {}).get("shots"),
                      "lo": (mem_report.get("ler") or {}).get("lo"), "hi": (mem_report.get("ler") or {}).get("hi"),
                      "budget": mem_report.get("budget"), "noise": (mem_report.get("noise") or {}).get("id")}
    return out


def _budget(ler: Mapping | None) -> dict:
    return {k: v for k, v in (ler or {}).items() if k in ("decoder", "max_shots", "max_errors", "batch", "time_limit_s")}


class _Mismatch(Exception):
    pass


def _measure_counts(prog, qmap) -> dict[int, int]:
    ion_q = {ion: q for q, ion in qmap.items()}
    got: dict[int, int] = {}
    for i in prog.instructions:
        if i.type == "measure":
            for ion in i.ions:
                if ion in ion_q:
                    got[ion_q[ion]] = got.get(ion_q[ion], 0) + 1
    return got


def _clifford_check(prog, qmap, pair) -> None:
    cc = _bridge("check_cert")
    n = pair.n_qubits
    try:
        got = cc.tableau_from_program(prog, {"map": {str(q): ion for q, ion in qmap.items()}}, n)
    except NotImplementedError as exc:
        raise _Mismatch(f"the program is not Clifford: {exc}") from None
    want = _source_tableau(pair.qasm, n, cc)
    if got != want:
        raise _Mismatch("the program's stabilizer tableau is not the circuit's")
    if _measure_counts(prog, qmap) != _source_measures(pair.qasm):
        raise _Mismatch("the program does not measure each qubit as often as the circuit does")


def _source_tableau(qasm: str, n: int, cc):
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "c.qasm"
        p.write_text(qasm, encoding="utf-8")
        return cc.circuit_tableau(p, n)


def _source_measures(qasm: str) -> dict[int, int]:
    """How often the circuit measures each qubit, read by qiskit (it expands `measure q -> c`)."""
    from qiskit import qasm2
    qc = qasm2.loads(qasm, custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
    out: dict[int, int] = {}
    for inst in qc.data:
        if inst.operation.name == "measure":
            q = qc.find_bit(inst.qubits[0]).index
            out[q] = out.get(q, 0) + 1
    return out


def _unitary_check(prog, qmap, pair, workdir) -> None:
    import tempfile
    from ..workspace.evaluator import Toolchain
    from ..workspace.procs import run_limited
    U = _bridge("unitary")
    tc = Toolchain.discover()
    if tc.qccdc is None:
        raise _Mismatch("the trusted parser (qccdc_cli parse) is not installed, so the circuit cannot be lowered")
    with tempfile.TemporaryDirectory(dir=workdir) as td:
        cp, op = Path(td) / "c.qasm", Path(td) / "c.parsed.json"
        cp.write_text(pair.qasm, encoding="utf-8")
        r = run_limited([tc.qccdc, "parse", cp, "-o", op], cwd=td, timeout=120, mem_mb=2048)
        if not r.ok:
            raise _Mismatch(f"the trusted parser failed on the circuit: {(r.stderr or r.stdout)[-300:]}")
        ops = json.loads(op.read_text(encoding="utf-8"))["ops"]
    qubit_of = {ion: q for q, ion in qmap.items()}
    try:
        a = U.program_unitary(prog.instructions, qubit_of, pair.n_qubits)
        b = U.circuit_unitary(ops, pair.n_qubits)
    except NotImplementedError as exc:
        raise _Mismatch(f"the unitary cannot be built: {exc}") from None
    ok, _alpha, worst = U.same_up_to_phase(a, b)
    if not ok:
        raise _Mismatch(f"the program's unitary is not the circuit's (max entry error {worst:.2e})")


def geomean(xs) -> float | None:
    xs = [x for x in xs if x is not None and x > 0 and math.isfinite(x)]
    if not xs:
        return None
    return math.exp(sum(math.log(x) for x in xs) / len(xs))
