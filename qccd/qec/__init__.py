"""The logical error rate of a compiled QEC program.  docs/PLAN-boards.md, Phase 1.

    experiments  memory experiments: QASM + detectors on classical bits (compiler-independent)
    noise        named, versioned noise models built from the device's own physics
    extract      TSIR + architecture + experiment -> noisy stim circuit + error budget
    check        the noiseless determinism-and-parity check of an extracted circuit
    decode       adaptive sampling, pymatching / BP-OSD, Wilson interval, LER per round

`evaluate_memory` runs the whole chain and returns a `qccd.ler_report`; a program that
cannot be read, or reads as the wrong circuit, is a report with `check.ok = false` and no
LER, never an exception.  `compile_with_qccdc` is the reference compile (qccdc + cooling)
the tests and the benchmark suite measure against.

stim, pymatching and ldpc are imported inside the functions that use them, so importing
this package costs nothing and the rest of `qccd` stays standard-library only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping

from .check import CheckResult, noiseless_check
from .decode import LerEstimate, estimate_ler, per_round, wilson
from .experiments import (EXPERIMENTS, MemoryExperiment, bb, css_memory, ideal_stim,
                          repetition, rotated_surface, steane, steane_code)
from .experiments import get as get_experiment
from .extract import Extraction, ExtractionError, extract, load_arch, load_program
from .noise import CHANNELS, MODELS, NoiseModel, describe as describe_noise
from .noise import get as get_noise

__all__ = [
    "REPORT_KIND",
    "evaluate_memory",
    "compile_with_qccdc",
    "CompileFailed",
    "tool_versions",
    # experiments
    "MemoryExperiment", "EXPERIMENTS", "get_experiment", "repetition", "rotated_surface",
    "steane", "steane_code", "css_memory", "bb", "ideal_stim",
    # noise
    "NoiseModel", "MODELS", "CHANNELS", "get_noise", "describe_noise",
    # extract / check / decode
    "extract", "Extraction", "ExtractionError", "load_program", "load_arch",
    "noiseless_check", "CheckResult",
    "estimate_ler", "LerEstimate", "wilson", "per_round",
]

REPORT_KIND = "qccd.ler_report"

_REPO = Path(__file__).resolve().parents[2]
_LER_BUDGET_KEYS = ("decoder", "max_shots", "max_errors", "batch", "time_limit_s")


def tool_versions() -> dict[str, str | None]:
    """The versions an LER depends on; recorded in every report."""
    from importlib import metadata

    out: dict[str, str | None] = {}
    for dist in ("stim", "pymatching", "ldpc"):
        try:
            out[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            out[dist] = None
    return out


def evaluate_memory(prog, arch, exp: MemoryExperiment, *, noise_id: str = "qccd-noise@1",
                    qubit_map=None, cert: Mapping | None = None,
                    ler_budget: Mapping | None = None, seed: int | None = None,
                    noise: NoiseModel | None = None) -> dict[str, Any]:
    """Extract, check, and (if the check passes) estimate the LER.  JSON-safe report.

    `ler_budget` passes sampling limits through to `estimate_ler` (`decoder`,
    `max_shots`, `max_errors`, `batch`, `time_limit_s`).  `noise` evaluates under an
    unpublished model (a sweep); a report then names that model's id with its settings.
    """
    budget_kw = dict(ler_budget or {})
    unknown = sorted(set(budget_kw) - set(_LER_BUDGET_KEYS))
    if unknown:
        raise ValueError(f"unknown ler_budget key(s) {unknown}; known: {list(_LER_BUDGET_KEYS)}")
    nm = noise or get_noise(noise_id)
    report: dict[str, Any] = {
        "kind": REPORT_KIND,
        "version": 1,
        "experiment": exp.summary(),
        "noise": {"id": nm.id, "settings": nm.to_json(), "params": None},
        "check": None,
        "ler": None,
        "budget": {},
        "stats": {},
        "tools": tool_versions(),
    }
    try:
        arch_obj = load_arch(arch)
        report["noise"]["params"] = nm.resolve(arch_obj)
        ex = extract(prog, arch_obj, exp, noise=nm, qubit_map=qubit_map, cert=cert)
    except (ExtractionError, ValueError, KeyError, TypeError) as exc:
        report["check"] = {"ok": False, "stage": "extract",
                           "reason": f"{type(exc).__name__}: {exc}",
                           "nondeterministic": False, "wrong_detectors": [],
                           "wrong_observables": []}
        return report
    report["noise"]["params"] = ex.params
    report["budget"] = {k: float(v) for k, v in ex.budget.items()}
    report["budget"]["total"] = float(sum(ex.budget.values()))
    report["stats"] = ex.stats
    chk = noiseless_check(ex.circuit)
    report["check"] = {"stage": "noiseless", **chk.to_json()}
    if chk.ok:
        # "auto" judges only the circuit's error model; the experiment knows whether its
        # code is matchable at all (`MemoryExperiment.matchable`), so it decides first
        if budget_kw.get("decoder", "auto") == "auto" and not exp.matchable:
            budget_kw["decoder"] = "bposd"
        est = estimate_ler(ex.circuit, rounds=exp.rounds, seed=seed, **budget_kw)
        report["ler"] = est.to_json()
    return report


# ------------------------------------------------------------------ the reference compile


class CompileFailed(RuntimeError):
    """qccdc (or the cooling pass) did not produce a program.

    `kind` is "refused" (the compiler declined: the device cannot hold or route the
    circuit), "timeout", "crash" (a non-zero exit that was not a refusal, or a missing
    output) or "toolchain_missing".  `log` is the tail of what the tools printed.
    """

    def __init__(self, kind: str, message: str, log: str = ""):
        super().__init__(message)
        self.kind = kind
        self.log = log


def _toolchain() -> tuple[Path | None, Path, str]:
    """(qccdc, bridge dir, python): the workspace's discovery, else the repo's own build."""
    try:
        from ..workspace.evaluator import Toolchain

        tc = Toolchain.discover()
        return tc.qccdc, Path(tc.bridge), tc.python
    except Exception:  # noqa: BLE001 -- the workspace is optional; fall back to the build tree
        env = os.environ.get("QCCD_QCCDC")
        ob = _REPO / "Compiler" / "ocaml" / "_build" / "default" / "bin"
        cands = [Path(env)] if env else [ob / "qccdc_cli.exe", ob / "qccdc_cli"]
        qccdc = next((p for p in cands if p.is_file()), None)
        return qccdc, _REPO / "Compiler" / "bridge", sys.executable


def _reason(log: str) -> str:
    """qccdc's own words: the first line of each attempt, without the circuit path it
    prefixes, plus the verdict on the rotation fallback when it tried one."""
    said: list[str] = []
    for block in log.split("--- qccdc_cli ")[1:]:
        lines = [ln.strip() for ln in block.splitlines()[1:] if ln.strip()]
        if not lines:
            continue
        first = lines[0].split(".qasm: ", 1)[-1]
        last = lines[-1].split(".qasm: ", 1)[-1]
        for s in (first, last) if last != first else (first,):
            if s not in said:
                said.append(s)
    return (" / ".join(said) or "no output")[:400]


def compile_with_qccdc(qasm_text: str, arch, workdir, *, cool: bool = True,
                       timeout: float = 600.0, mode: str = "auto") -> tuple[Path, Path]:
    """Compile `qasm_text` for `arch` with qccdc, then insert cooling: (program, certificate).

    `arch` is a path to an `.arch.json`, its parsed document, or an `Architecture`.
    `mode` is qccdc's verb:
    "compile" (place, route; falls back to rigid rotation by itself), "rotate", or "auto",
    which is what the workspace's compile job does -- rotate first on a device with a
    closed loop, else compile.  The program returned is the cooled one when `cool`.
    A `compile.json` beside the outputs records the mode used, the timings and the log.
    """
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    qccdc, bridge, python = _toolchain()
    if qccdc is None:
        raise CompileFailed("toolchain_missing", "the compiler (qccdc_cli) is not installed: "
                            "run `qccd toolchain install`, or build Compiler/ocaml")
    if isinstance(arch, (str, Path)):
        arch_path = Path(arch).resolve()
        arch_doc = json.loads(arch_path.read_text(encoding="utf-8"))
    else:
        # an Architecture (e.g. `Machine.ring(8, 2, 8).arch`) goes in its expanded form,
        # which is what the workspace hands the same tools
        arch_doc = arch.to_json(expanded=True) if hasattr(arch, "to_json") else dict(arch)
        arch_path = work / "device.arch.json"
        arch_path.write_text(json.dumps(arch_doc), encoding="utf-8")
    qasm_path = work / "circuit.qasm"
    qasm_path.write_text(qasm_text, encoding="utf-8")
    expanded = work / "device.expanded.json"
    t0 = time.time()
    deadline = t0 + timeout

    def run(cmd: list, what: str) -> subprocess.CompletedProcess:
        left = deadline - time.time()
        if left <= 0:
            raise CompileFailed("timeout", f"{what}: out of time ({timeout:g} s)")
        try:
            return subprocess.run([str(x) for x in cmd], capture_output=True, text=True,
                                  timeout=left, cwd=work)
        except subprocess.TimeoutExpired as exc:
            out = (exc.stdout or b"")
            out = out.decode(errors="replace") if isinstance(out, bytes) else out
            raise CompileFailed("timeout", f"{what} did not finish in {timeout:g} s",
                                out[-4000:]) from None

    ex = run([python, bridge / "export_arch.py", arch_path, "-o", expanded], "export_arch")
    if ex.returncode or not expanded.exists():
        raise CompileFailed("crash", "export_arch failed", (ex.stdout + ex.stderr)[-4000:])

    if mode == "auto":
        from ..compile.programs import closed_loops

        modes = ["rotate", "compile"] if closed_loops(load_arch(arch_doc)) else ["compile"]
    elif mode in ("compile", "rotate"):
        modes = [mode]
    else:
        raise ValueError(f"mode must be auto, compile or rotate, not {mode!r}")

    prefix = work / "prog"
    tsir, cert = Path(f"{prefix}.tsir.json"), Path(f"{prefix}.qcert.json")
    log, used = "", None
    for m in modes:
        for stale in (tsir, cert):
            stale.unlink(missing_ok=True)
        cp = run([qccdc, m, qasm_path, "--arch", expanded, "-o", prefix], f"qccdc {m}")
        log += f"--- qccdc_cli {m} (exit {cp.returncode})\n" + (cp.stdout + "\n" + cp.stderr)[-4000:]
        if cp.returncode == 0 and tsir.exists() and cert.exists():
            unrealised = json.loads(cert.read_text(encoding="utf-8")).get("unrealised")
            if not unrealised:
                used = m
                break
            log += f"\n{len(unrealised)} circuit op(s) left unrealised\n"
    t_compile = time.time() - t0
    if used is None:
        raise CompileFailed("refused", _reason(log), log[-6000:])
    prog = tsir
    if cool:
        cooled = work / "prog.cooled.tsir.json"
        cooled.unlink(missing_ok=True)
        co = run([python, bridge / "insert_cooling.py", tsir, "--arch", arch_path, "-o", cooled],
                 "insert_cooling")
        if co.returncode or not cooled.exists():
            raise CompileFailed("crash", "insert_cooling failed", (co.stdout + co.stderr)[-4000:])
        prog = cooled
    (work / "compile.json").write_text(json.dumps({
        "mode": used, "cool": cool, "compile_s": round(t_compile, 3),
        "total_s": round(time.time() - t0, 3), "log": log[-6000:]}, indent=1), encoding="utf-8")
    return prog, cert
