"""Compiling under a rule set: the rule set goes in, and every rule of it is checked on what
comes out.

    from qccd.ruleset.compile import compile_under
    out = compile_under("bell.qasm", "arch/chain.arch.json", "standard", "out/bell")
    out["report"].summary()

The compiler is given the rule set itself (`qccdc ... --rules`), resolved into one file, and
plans with what it knows how to satisfy (`Compiler/ocaml/lib/rules.ml`).  The programme is
then cooled, replayed and judged by every rule of the same file -- the ones the compiler
planned for, and any it has never heard of.  A rule that fails is reported with the
instructions that break it; the compiler is not trusted to have obeyed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import RuleSet, check, load
from .model import CircuitOpData

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "Compiler" / "bridge"

__all__ = ["compile_under", "circuit_of_certificate"]


def circuit_of_certificate(cert: dict) -> list[CircuitOpData]:
    """The circuit the certificate says it compiled, with each qubit named by its ion."""
    ion = {int(k): str(v) for k, v in (cert.get("map") or {}).items()}
    out = []
    for op in cert.get("circuit_ops", ()):
        qs = tuple(ion.get(int(q), f"q{q}") for q in op.get("qubits", ()))
        roles = ("control", "target") if op.get("name") in ("cx", "CX") and len(qs) == 2 else ()
        args = ",".join(f"q[{q}]" for q in op.get("qubits", ()))
        out.append(CircuitOpData(int(op["i"]), str(op["name"]), qs, roles,
                                 text=f"{op['name']} {args}"))
    out.sort(key=lambda c: c.index)
    return out


def _qccdc() -> Path:
    """`QCCD_QCCDC` when set, else this checkout's own build.  A compiler released before
    rule sets ignores `--rules`, and the check afterwards is what reports the difference."""
    import os
    env = os.environ.get("QCCD_QCCDC")
    if env:
        return Path(env)
    return ROOT / "Compiler" / "ocaml" / "_build" / "default" / "bin" / "qccdc_cli.exe"


def compile_under(qasm: str | Path, arch_path: str | Path, ruleset: RuleSet | str | Path,
                  out_dir: str | Path, *, qccdc: str | Path | None = None,
                  command: str = "compile", semantics: bool = True,
                  timeout: float = 1800) -> dict[str, Any]:
    """Compile `qasm` onto the device under `ruleset`; cool; judge by every rule of it."""
    from ..arch import load as load_arch
    from ..compile.cooling import CoolingPolicy, insert_cooling
    from ..cost.models import corrected_model
    from ..ir.tsir import TSIR
    from .programme import programme_execution

    rs = ruleset if isinstance(ruleset, RuleSet) else load(ruleset)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rules_file = out / "ruleset.rules"
    rules_file.write_text(rs.text(), encoding="utf-8", newline="\n")
    arch_path = Path(arch_path)
    expanded = out / "device.expanded.json"
    r = subprocess.run([sys.executable, str(BRIDGE / "export_arch.py"), str(arch_path), "-o",
                        str(expanded)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"the device could not be exported: {r.stderr[-400:]}")
    exe_path = Path(qccdc) if qccdc else _qccdc()
    prefix = out / "prog"
    r = subprocess.run([str(exe_path), command, str(qasm), "--arch", str(expanded), "-o",
                        str(prefix), "--rules", str(rules_file)],
                       capture_output=True, text=True, timeout=timeout)
    log = (r.stdout or "") + (r.stderr or "")
    (out / "compile.log").write_text(log, encoding="utf-8")
    tsir = out / "prog.tsir.json"
    if not tsir.exists():
        return {"ok": False, "compiled": False, "log": log, "ruleset": rs.name}
    arch = load_arch(arch_path)
    model = corrected_model()
    cooled = insert_cooling(TSIR.load(tsir), arch, model, policy=CoolingPolicy()).program
    cooled_path = out / "prog.cooled.tsir.json"
    cooled_path.write_text(json.dumps(cooled.to_json(), indent=1), encoding="utf-8")
    cert = json.loads((out / "prog.qcert.json").read_text(encoding="utf-8")) \
        if (out / "prog.qcert.json").exists() else {}
    computes, why = None, "what the programme computes was not checked"
    verdict: dict = {}
    if semantics and cert:
        # the proved checker decides the half it can (where every gate happens, with whom);
        # the pulses are composed back into the circuit by the other half
        qin = out / "prog.qcheck.json"
        q = subprocess.run([sys.executable, str(BRIDGE / "mk_qcheck_input.py"), str(prefix),
                            "--arch", str(expanded), "-o", str(qin)], capture_output=True, text=True)
        args = [sys.executable, str(BRIDGE / "check_cert.py"), str(prefix), "--qasm", str(qasm),
                "--arch", str(arch_path), "--json"]
        if q.returncode == 0 and qin.exists():
            args += ["--qcheck", str(qin)]
        v = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        try:
            verdict = json.loads(v.stdout[v.stdout.index("{"):])
        except ValueError:
            verdict = {"error": (v.stderr or v.stdout)[-400:]}
        r10 = verdict.get("R10")
        if r10 == "passed":
            computes, why = True, ""
        elif r10 == "FAILED":
            computes, why = False, verdict.get("R10_reason", "")
        else:
            why = verdict.get("R10_reason") or verdict.get("error") or f"the check said {r10}"
    exe = programme_execution(cooled, arch, model,
                              circuit=circuit_of_certificate(cert) if cert else None,
                              computes_circuit=computes, check_metrics=False)
    exe.facts["computes_reason"] = why
    report = check(rs, exe)
    (out / "rules.json").write_text(json.dumps(report.to_json(), indent=1, default=str),
                                    encoding="utf-8")
    return {"ok": report.ok and r.returncode == 0, "compiled": True, "exit": r.returncode,
            "log": log, "ruleset": rs.name, "report": report, "semantics": verdict,
            "programme": str(cooled_path)}
