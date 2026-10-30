"""Acceptance driver for the staged Studio compiler.

Native optimization, physical transformations, and acceptance are deliberately
separate. A successful process exit is never a substitute for a checker verdict.
Evidence describes executable checks within the declared model, not new formal
proofs of the compiler or experimental calibration.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from ..arch import Architecture
from ..cost.models import corrected_model
from ..ir.tsir import TSIR, validate_program
from ..verify import verify
from ..workspace.procs import run_limited
from .physical_stages import ANNOTATIONS, finish_program, analyze_thermal, check_binding, check_schedule
from .stages import STAGE_IDS, StageCheck, StageRecorder, verify_manifest


#: the C2LR router's stage engine (`qccd.compile.c2lr_router.STAGE_ENGINE`)
C2LR_ENGINE = "c2lr-router"


class CompilationRejected(ValueError):
    """The candidate has no complete, accepted chain of stage contracts."""


def _loads(text):
    def unique(items):
        out = {}
        for key, value in items:
            if key in out:
                raise CompilationRejected(f"duplicate JSON key: {key}")
            out[key] = value
        return out
    return json.loads(text, object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(
                          CompilationRejected(f"nonfinite JSON value: {value}")))


def _load(path):
    return _loads(Path(path).read_text(encoding="utf-8"))


def _write(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False), encoding="utf-8")


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _check(value):
    messages = value.get("diagnostics", value.get("messages", []))
    return StageCheck(value.get("status", "unsupported"), value.get("method", ""),
                      tuple(value.get("coverage", [])),
                      tuple(m if isinstance(m, str) else json.dumps(m, sort_keys=True) for m in messages),
                      tuple(value.get("limitations", [])))


def validate_compilation_outputs(prefix, report: dict) -> dict:
    """Check the worker's accepted artifacts again at the ingestion boundary."""
    prefix = Path(prefix)
    if (report.get("schema") != "qccd.compilation.report.v1" or report.get("accepted") is not True
            or report.get("status") != "passed"):
        raise CompilationRejected("compilation report is not accepted")
    for suffix, key in ((".cooled.tsir.json", "output_sha256"),
                        (".tsir.json", "certified_sha256"), (".qcert.json", "certificate_sha256")):
        if _sha(Path(str(prefix) + suffix)) != report.get(key):
            raise CompilationRejected(f"accepted artifact changed: {suffix}")
    checked = verify_manifest(Path(str(prefix) + ".evidence") / "manifest.json",
                              expected_context=report["context"],
                              expected_manifest_sha256=report["manifest_sha256"])
    if checked["reported_status"] != "passed":
        raise CompilationRejected("accepted manifest does not report a complete passed chain")
    return checked


def semantic_errors(verdict: dict, *, require_lean: bool) -> list[str]:
    """Interpret the bridge's detailed verdict, including its exit-zero partials."""
    if not isinstance(verdict, dict):
        return ["semantic checker returned a non-object verdict"]
    errors = []
    for key in ("binding", "transport", "o1_transport", "order"):
        if verdict.get(key) != "ok":
            errors.append(f"{key}: {verdict.get(key, 'missing')}")
    o2 = verdict.get("o2_semantics", "")
    if not (isinstance(o2, str) and (o2 == "ok" or o2.startswith("ok ("))):
        errors.append(f"source semantics not established: {o2 or 'missing'}")
    if verdict.get("unrealised_ops") != 0:
        errors.append("unrealised circuit operations")
    if "readout" in verdict and not str(verdict["readout"]).startswith("ok"):
        errors.append(f"readout: {verdict['readout']}")
    if verdict.get("R10") not in ("passed", "partial"):
        errors.append(f"R10: {verdict.get('R10', 'missing')}")
    if require_lean and verdict.get("lean") != "accepted":
        errors.append("configured Lean checker did not accept")
    return errors


def certified_baseline(native: TSIR, final: TSIR) -> TSIR:
    """Keep final annotations on exactly the certified operation sequence.

    The native file is retained separately. Only checked physical annotations
    may differ from it; only fresh cool operations may be added. This preserves
    the evaluator's strict final/certified correspondence contract unchanged.
    """
    before = native.to_json()
    after = final.to_json()
    errors = validate_program(final)
    if errors:
        raise CompilationRejected("invalid final program: " + "; ".join(errors))
    if (native.name, native.arch_spec, native.meta) != (final.name, final.arch_spec, final.meta):
        raise CompilationRejected("physical stages changed program identity or metadata")
    ids = [i.id for i in native]
    if len(ids) != len(set(ids)):
        raise CompilationRejected("duplicate native instruction IDs")
    old = set(ids)
    kept = [i for i in after["instructions"] if i["id"] in old]
    if [i["id"] for i in kept] != ids:
        raise CompilationRejected("physical stages changed certified instruction order or coverage")

    def body(i):
        return {k: v for k, v in i.items() if k not in ANNOTATIONS}
    if [body(i) for i in before["instructions"]] != [body(i) for i in kept]:
        raise CompilationRejected("physical stages changed a certified operation")
    inserted = [i for i in after["instructions"] if i["id"] not in old]
    if any(i["type"] != "cool" or i["id"] < native.id_seq for i in inserted):
        raise CompilationRejected("physical stages inserted something other than fresh cooling")
    out = copy.deepcopy(after)
    out["instructions"] = kept
    out["metrics"] = {}  # this baseline is a correspondence artifact, not a runnable thermal schedule
    return TSIR.from_json(out)


def finish_compilation(prefix, *, qasm, arch_path, expanded_arch_path, toolchain,
                       table="qccdsim_jones", objective="fastest", timeout=600,
                       semantic_timeout=None, semantic_mem_mb=12288,
                       cancel=None, max_cooling_iterations=4) -> dict:
    """Finish and independently check native stages, then publish accepted files.

    Writes ``.compilation.json`` on rejection too. Successful outputs are
    ``.native.tsir.json`` (original), ``.tsir.json`` (annotated certified baseline),
    ``.cooled.tsir.json`` (executable) and ``.evidence/manifest.json``.
    """
    prefix = Path(prefix).resolve()
    qasm, arch_path, expanded_arch_path = map(lambda p: Path(p).resolve(),
                                            (qasm, arch_path, expanded_arch_path))
    work = prefix.parent
    file = lambda suffix: Path(str(prefix) + suffix)
    report_path = file(".compilation.json")
    # A retry may not leave an earlier executable looking accepted.
    file(".cooled.tsir.json").unlink(missing_ok=True)
    _write(report_path, {"schema": "qccd.compilation.report.v1", "accepted": False, "status": "started"})
    try:
        model = corrected_model(table, objective=objective)
        context = {"source_sha256": _sha(qasm), "architecture_sha256": _sha(arch_path),
                   "expanded_architecture_sha256": _sha(expanded_arch_path),
                   "toolchain": toolchain.identity(), "physical_model": model.describe(),
                   "policy": {"schedule": "serial between instructions, SIMD within instructions",
                              "max_cooling_iterations": max_cooling_iterations},
                   "stage_implementation": {name: _sha(Path(__file__).with_name(name))
                                            for name in ("driver.py", "physical_stages.py", "stages.py",
                                                         "c2lr_stages.py")}}
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        report = {"schema": "qccd.compilation.report.v1", "accepted": False,
                  "status": "rejected", "error": str(exc)}
        _write(report_path, report)
        return report
    recorder = StageRecorder(file(".evidence"), context)
    report = {"schema": "qccd.compilation.report.v1", "accepted": False,
              "evidence": str(file(".evidence") / "manifest.json"), "context": context,
              "verification_scope": "Executable stage contracts under the declared hardware and heating model",
              "not_established": ["new formal soundness proofs for the stage transformations",
                                  "experimental calibration", "R7b duty-cycle budgets", "R15 interference model"]}
    conditional = {}

    def run(args):
        if cancel is not None and cancel.is_set():
            raise CompilationRejected("compilation cancelled")
        result = run_limited(args, cwd=work, timeout=timeout, mem_mb=8192, cancel=cancel)
        if not result.ok:
            raise CompilationRejected(f"checker {result.status}: " + (result.stderr or result.stdout)[-1600:])
        return result

    try:
        native_doc = _load(file(".tsir.json"))
        native = TSIR.from_json(native_doc)
        cert = _load(file(".qcert.json"))
        if validate_program(native) or cert.get("unrealised"):
            raise CompilationRejected("native program is malformed or has unrealised operations")
        # Preserve original bytes: the native stage replay binds this exact file.
        file(".native.tsir.json").write_bytes(file(".tsir.json").read_bytes())
        if not file(".stages.json").is_file():
            raise CompilationRejected("compiler does not emit stage evidence; rebuild Compiler/ocaml before compiling")
        stages = _load(file(".stages.json"))
        if stages.get("schema") != "qccd.compiler.stages.v1" or stages.get("accepted") is not True:
            raise CompilationRejected("compiler did not supply accepted native stage evidence; rebuild the compiler")
        # A C2LR router's swap-or-stay steps name no routes, so `qccdc check-stages` cannot replay
        # them: its saved stages have their own re-check, and its verdicts are what is recorded
        # (the router claims nothing about a boundary).  Every other engine is qccdc's.
        c2lr = stages.get("engine") == C2LR_ENGINE
        if c2lr:
            checked = run([toolchain.python, toolchain.bridge / "check_c2lr_stages.py", file(".stages.json"),
                           "--qasm", qasm, "--arch", arch_path, "--expanded-arch", expanded_arch_path,
                           "--program", file(".native.tsir.json"), "--qccdc", toolchain.qccdc])
        else:
            checked = run([toolchain.qccdc, "check-stages", file(".stages.json"),
                           "--qasm", qasm, "--arch", expanded_arch_path,
                           "--program", file(".native.tsir.json")])
        native_verdict = _loads(checked.stdout)
        if not isinstance(native_verdict, dict) or native_verdict.get("accepted") is not True:
            raise CompilationRejected("saved native stage checker did not accept")
        report["native_check"] = native_verdict
        report["engine"] = stages.get("engine")
        claims = native_verdict.get("stage_checks") if c2lr else None
        if c2lr and (not isinstance(claims, list) or len(claims) != len(stages.get("stages", []))):
            raise CompilationRejected("the C2LR stage checker returned no verdict for some stage")
        seen = set()
        for index, stage in enumerate(stages.get("stages", [])):
            sid = stage["id"]
            if sid not in STAGE_IDS[:7]:
                raise CompilationRejected(f"unexpected native stage {sid}")
            iteration = stage.get("iteration")
            if iteration is None and sid in STAGE_IDS[3:7]:
                iteration = {"native_record": index}
            record = recorder.record(sid, stage["input"], stage["output"], stage["witness"],
                                     _check(claims[index] if c2lr else stage["check"]), iteration=iteration)
            if record["check"]["status"] not in ("passed", "not_applicable"):
                raise CompilationRejected(f"{stage['name']} was not fully checked")
            seen.add(sid)
        if seen != set(STAGE_IDS[:7]):
            raise CompilationRejected("native evidence omits mandatory stages: " + ", ".join(sorted(set(STAGE_IDS[:7]) - seen)))

        arch = Architecture.from_json(_load(arch_path))
        physical = finish_program(native, arch, model, max_iterations=max_cooling_iterations,
                                  cancelled=cancel.is_set if cancel is not None else None)
        for stage in physical.stages:
            # Replacing stage8 invalidates its previous downstream timing, thermal
            # and cooling claims. History remains immutable and independently readable.
            recorder.record(STAGE_IDS[stage["id"] - 1], stage["input"], stage["output"],
                            stage["witness"], _check(stage["check"]))
        report["physical_iterations"] = physical.iterations
        if not physical.ok:
            raise CompilationRejected(physical.error or "physical compilation failed")
        conditional["cooling_insertion"] = "No further cooling required after the final thermal analysis; earlier repairs remain in history."

        final_path = file(".validation.tsir.json")
        physical.program.save(final_path, indent=None)
        final = TSIR.from_json(_load(final_path))
        if final.to_json() != physical.program.to_json():
            raise CompilationRejected("serialized executable differs from accepted physical stage output")
        # Recompute from the reloaded serialized output, not optimizer objects.
        binding = check_binding(native if physical.iterations == 1 else final, final, arch, model)
        scheduling = check_schedule(final, final, arch, model)
        thermal = analyze_thermal(final, arch, model)
        hardware = verify(final, arch, model)
        baseline = certified_baseline(native, final)
        diagnostics = []
        for label, check in (("binding", binding), ("scheduling", scheduling), ("thermal", thermal["check"])):
            if not check["ok"]:
                diagnostics.append(f"serialized {label}: {check['diagnostics']}")
        if not hardware.ok():
            diagnostics.extend(str(v) for v in hardware.rules.violations if v.severity == "error")
        report["hardware"] = hardware.rules.summary()

        # The semantic checker consumes the final file, including the inserted cools.
        validation = file(".validation")
        _write(file(".validation.qcert.json"), cert)
        args = [toolchain.python, toolchain.bridge / "check_cert.py", validation,
                "--qasm", qasm, "--arch", arch_path, "--json"]
        if toolchain.qcheck is not None:
            run([toolchain.python, toolchain.bridge / "mk_qcheck_input.py", validation,
                 "--arch", expanded_arch_path, "-o", file(".validation.qcheck.json")])
            args += ["--qcheck", file(".validation.qcheck.json")]
        # Respect the exact checker selected by Toolchain, including installed ones.
        import os
        env = dict(os.environ)
        if toolchain.qcheck is not None:
            env["QCCD_QCHECK"] = str(toolchain.qcheck)
        semantic_run = run_limited(args, cwd=work, timeout=semantic_timeout or timeout,
                                   mem_mb=semantic_mem_mb, cancel=cancel, env=env)
        try:
            semantic = _loads(semantic_run.stdout)
        except (ValueError, TypeError):
            semantic = {"error": (semantic_run.stderr or semantic_run.stdout)[-1600:]}
        report["semantics"] = semantic
        if not semantic_run.ok:
            diagnostics.append(f"semantic checker {semantic_run.status}: {semantic}")
        diagnostics.extend(semantic_errors(semantic, require_lean=toolchain.qcheck is not None))
        recorder.record("artifact_validation", {"native": native_doc, "certificate": cert},
                        final.to_json(), {"semantics": semantic, "hardware": report["hardware"],
                                          "serialized_sha256": _sha(final_path),
                                          "certified_baseline": baseline.to_json()},
                        StageCheck("failed" if diagnostics else "passed", "driver.serialized_artifact_recheck",
                                   ("source circuit semantics and readout", "certificate binding and instruction order",
                                    "enforceable hardware rules", "reloaded operating points, schedule and thermal budgets",
                                    "exact preservation of certified bodies except checked physical annotations"),
                                   tuple(diagnostics)))
        if diagnostics:
            raise CompilationRejected("artifact validation: " + "; ".join(diagnostics[:5]))
        manifest = recorder.finish(conditional_stages=conditional)
        if manifest["status"] != "passed":
            raise CompilationRejected(f"stage chain is {manifest['status']}")
        verify_manifest(file(".evidence") / "manifest.json", expected_context=context,
                        expected_manifest_sha256=manifest["manifest_sha256"])
        baseline.save(file(".tsir.json"), indent=None)
        file(".cooled.tsir.json").write_bytes(final_path.read_bytes())
        report.update(accepted=True, status="passed", manifest_sha256=manifest["manifest_sha256"],
                      instructions=len(final), duration_us=thermal["makespan_us"],
                      output_sha256=_sha(file(".cooled.tsir.json")),
                      certified_sha256=_sha(file(".tsir.json")), certificate_sha256=_sha(file(".qcert.json")))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError) as exc:
        report.update(status="rejected", error=str(exc))
        manifest = recorder.finish(conditional_stages=conditional)
        report["manifest_sha256"] = manifest["manifest_sha256"]
        report["stage_status"] = manifest["status"]
    _write(report_path, report)
    return report
