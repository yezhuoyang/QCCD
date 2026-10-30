"""Running a program on a design: compile it, replay it, and say where the time goes.

A RUN is an experiment, not a submission.  It takes any program -- one from the catalogue
(`Compiler/examples/*.qasm`: the BB [[144,12,12]] round, surface-17, steane, GHZ, QFT, ...)
or QASM the agent writes -- and a design (the working design `main`, or any draft), and:

    1. exports the design for the compiler, and compiles the program onto it with the real
       compiler (`qccdc_cli rotate` when the design has closed loops, else `compile`;
       `auto` falls back from rotate to compile);
    2. checks the compilation stages, including physical binding, scheduling,
       cooling and the serialized program's source semantics;
    3. replays it under the release's cost model and under the transport table, with the
       rules checked, and builds the performance report (`perf.py`);
    4. keeps the device, the program and each step's start time as artifacts, so the Studio
       can animate the run and play two runs side by side on one clock.

When the configured toolchain includes Lean, its certificate checker runs too.
A run is still an experiment; the local submission path (`submit_local`) grades
eligibility for a particular board. Runs are jobs of kind `run`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping

from .jsonsafe import canonical_bytes, strict_loads
from .bundle import _FILE_LIMITS as PROGRAM_LIMITS
from .tasks import board_device

__all__ = ["RunsMixin", "program_catalog", "resolve_program"]

_QASM_MAX = 2 * 1024 * 1024
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _examples_dir() -> Path:
    from .evaluator import BRIDGE
    return Path(BRIDGE).parent / "examples"


def _qubits(qasm: str) -> int:
    return sum(int(n) for n in re.findall(r"^\s*qreg\s+\w+\s*\[\s*(\d+)\s*\]\s*;", qasm, re.M))


def _describe(qasm: str) -> str:
    lines = [ln.strip()[2:].strip() for ln in qasm.splitlines()[:12] if ln.strip().startswith("//")]
    return " ".join(lines)[:240]


def program_catalog() -> list:
    """The programs a run can name: every tracked `Compiler/examples/*.qasm`."""
    out = []
    d = _examples_dir()
    for p in sorted(d.glob("*.qasm")) if d.is_dir() else []:
        text = p.read_text(encoding="utf-8", errors="replace")
        out.append({"name": p.stem, "qubits": _qubits(text), "description": _describe(text),
                    "source": f"Compiler/examples/{p.name}"})
    return out


#: what people call the catalogue's programs
_ALIASES = {"bb": "bb144_esm", "bb144": "bb144_esm", "bbcode": "bb144_esm", "bb code": "bb144_esm",
            "gross": "bb144_esm", "gross code": "bb144_esm", "bivariate bicycle": "bb144_esm",
            "surface17": "surface17_esm", "surface code": "surface17_esm", "steane": "steane_esm",
            "rep9": "rep9_esm", "repetition": "rep9_esm"}


def resolve_program(spec) -> tuple[str, str, str]:
    """`spec` is a catalogue name (or a common alias: "bb", "bb code", "gross"), or
    `{"name": ..., "qasm": "OPENQASM 2.0; ..."}`.  Returns (name, qasm, source)."""
    from .core import WorkspaceError
    if isinstance(spec, Mapping):
        qasm = spec.get("qasm")
        if not isinstance(qasm, str) or not qasm.strip():
            raise WorkspaceError("bad_request", "program.qasm must be OpenQASM 2.0 text", status=422)
        if len(qasm.encode("utf-8")) > _QASM_MAX:
            raise WorkspaceError("too_large", "program.qasm is larger than 2 MB", status=413)
        name = str(spec.get("name") or "custom")
        if not _NAME.match(name):
            raise WorkspaceError("bad_request", "program.name: letters, digits, _ . - (at most 64)", status=422)
        return name, qasm, "written by the agent"
    if not isinstance(spec, str) or not spec.strip():
        raise WorkspaceError("bad_request", "program must be a catalogue name or {name, qasm}", status=422)
    key = spec.strip().lower()
    key = _ALIASES.get(key, key)
    cat = {p["name"].lower(): p for p in program_catalog()}
    hit = cat.get(key) or cat.get(key + "_esm")
    if hit is None:
        starts = [p for n, p in cat.items() if n.startswith(key)]
        hit = starts[0] if len(starts) == 1 else None
    if hit is None:
        # a leaderboard by its title: that board's own circuit, byte for byte (what a submission compiles)
        from .tasks import ReleaseError, find_board
        try:
            board = find_board(spec)
            return board.manifest["task"], board.circuit_text(), f"the board {board.title}"
        except ReleaseError:
            pass
        raise WorkspaceError("unknown_program", f"no program {spec!r}; the catalogue has: "
                             + ", ".join(sorted(cat)) + "; or name a board by its title", status=404)
    path = _examples_dir() / Path(hit["source"]).name
    return hit["name"], path.read_text(encoding="utf-8"), hit["source"]


def _capacity(arch) -> tuple[int, int]:
    nodes = arch.device.nodes
    return sum(int(getattr(n, "capacity", 0) or 0) for n in nodes.values()), len(nodes)


def _step_times(prog, res) -> list:
    """Each instruction's start time (us), in program order -- the page's frame i is
    instruction i, so this maps a shared clock onto both animations."""
    first: dict = {}
    for c in res.cycles:
        if c.type != "init" and c.instr_id not in first:
            first[c.instr_id] = round(float(c.t0), 3)
    out, last = [], 0.0
    for ins in prog.instructions:
        last = first.get(ins.id, last)
        out.append(last)
    return out


class RunsMixin:
    # ------------------------------------------------------------------ catalogue

    def programs(self) -> list:
        return program_catalog()

    def run_program_source(self, run_id: str) -> dict:
        """What a run compiles, as the person's page shows it while the run is going: the
        program's name, its OpenQASM and the design it goes onto."""
        from .core import WorkspaceError
        j = self.job(run_id)
        if j["kind"] != "run":
            raise WorkspaceError("unknown_run", f"{run_id} is not a run", status=404)
        res = j.get("result") or {}
        params, run = res.get("params") or {}, res.get("run") or {}
        spec = params.get("program") if params else (run.get("program") or {}).get("name")
        circuit = (run.get("artifacts") or {}).get("circuit")
        try:
            name, qasm, source = resolve_program(spec)
        except WorkspaceError:
            name, qasm, source = str((run.get("program") or {}).get("name") or spec or "program"), "", ""
        if circuit:                                   # a finished run: exactly what was compiled
            qasm = self.get_artifact(circuit).decode("utf-8", errors="replace")
        draft = params.get("branch") or (run.get("design") or {}).get("draft") or "main"
        return {"run_id": run_id, "name": name, "qasm": qasm[:200_000], "source": source,
                "animate": (params or {}).get("animate", run.get("animate", True)) is not False,
                "draft": str(draft).removeprefix("cand/"), "design": self._design_title_of(str(draft)),
                "status": j["status"],
                "progress": (j.get("progress") or {}).get("message"), "agent": (j.get("actor") or {}).get("label")}

    # ------------------------------------------------------------------ the job

    def _check_run_params(self, params: dict) -> None:
        from .core import WorkspaceError
        resolve_program(params.get("program"))          # refuse an unknown program at once
        mode = params.get("compiler", "auto")
        if mode not in ("auto", "rotate", "compile"):
            raise WorkspaceError("bad_request", "compiler must be auto, rotate or compile", status=422)
        params["compiler"] = mode
        # whether the person's tab plays the run or opens it at its result (run_eval.py)
        params["animate"] = params.get("animate", True) is not False

    def _job_run(self, jid, params, actor, origin_prompt_id, cancel) -> dict:
        from ..arch.device import Architecture
        from ..compile.programs import closed_loops
        from ..compile.driver import validate_compilation_outputs
        from ..cost.models import corrected_model
        from ..ir.tsir import TSIR
        from ..verify.replay import replay
        from .core import WorkspaceError
        from .evaluator import Toolchain
        from .results import _compiler_fault, _compiler_reason, _refuse_withdrawn_compiler
        from .metrics import replay_time
        from .perf import performance
        from .procs import run_limited

        tc = Toolchain.discover()
        if tc.qccdc is None:
            raise WorkspaceError("toolchain_missing", "the compiler (qccdc_cli) is not installed: run "
                                 "`qccd toolchain install` in a terminal (it fetches the prebuilt one), "
                                 "or build Compiler/ocaml", status=424)
        _refuse_withdrawn_compiler(tc)
        branch, rev = params["branch"], params["revision"]
        name, qasm, source = resolve_program(params["program"])
        r = self.replayed(branch, rev)
        if not r.ok:
            raise WorkspaceError("design_invalid", f"the design on {branch} at r{rev} does not build", status=422)
        q = _qubits(qasm)
        cap, n_sites = _capacity(r.arch)
        # what the person calls them: the board's title and the design's name, never an internal id
        shown = source[len("the board "):] if source.startswith("the board ") else name
        dname = self._design_title_of(branch)
        design = {"draft": branch, "revision": rev, "name": r.arch.name, "sites": n_sites,
                  "ion_capacity": cap, "digest": r.arch_digest()}
        if q > cap:
            return {"_status": "failed", "design": design, "program": {"name": name, "qubits": q},
                    "summary": f"{shown} uses {q} qubits, but {dname} holds at most {cap} ions in "
                               f"{n_sites} sites: it cannot run here"}
        work = self.root / ".qccd" / "runs" / jid
        work.mkdir(parents=True, exist_ok=True)
        device = board_device(r.arch_doc, self.release)
        (work / "device.arch.json").write_text(json.dumps(device), encoding="utf-8")
        (work / "circuit.qasm").write_text(qasm, encoding="utf-8")
        self._progress(jid, "export", "expanding the device for the compiler")
        ex = run_limited([tc.python, tc.bridge / "export_arch.py", work / "device.arch.json", "-o",
                          work / "device.expanded.json"], cwd=work, timeout=300, cancel=cancel)
        if not ex.ok:
            return {"_status": "cancelled" if ex.status == "cancelled" else "failed",
                    "summary": "exporting the design for the compiler failed", "log": (ex.stderr or ex.stdout)[-2000:]}
        mode = params["compiler"]
        modes = ([mode] if mode != "auto" else (["rotate", "compile"] if closed_loops(r.arch) else ["compile"]))
        limits = self.release.manifest.get("limits") or {}
        compile_budget = float(params.get("timeout_s") or limits.get("compile_timeout_s", 900))
        semantic_budget = max(compile_budget, float(limits.get("lean_timeout_s", 1800)),
                              float(limits.get("semantics_timeout_s", 1800)))
        semantic_memory = max(int(limits.get("lean_mem_mb", 12288)),
                              int(limits.get("semantics_mem_mb", 12288)))
        # The finishing worker contains a saved native-stage check, optional Lean
        # preparation, and source semantics. Its outer limit must not cut short
        # a checker that is still within the release's own timeout/memory budget.
        verification_budget = 2 * compile_budget + semantic_budget + 600
        cp, log, used, compilation = None, "", None, None
        for m in modes:
            self._progress(jid, "compile", f"compiling {name} ({q} qubits) with qccdc_cli {m}")
            compilation = None
            for stale in ("prog.tsir.json", "prog.qcert.json", "prog.stages.json",
                          "prog.native.tsir.json", "prog.cooled.tsir.json", "prog.compilation.json"):
                (work / stale).unlink(missing_ok=True)
            cp = run_limited([tc.qccdc, m, work / "circuit.qasm", "--arch", work / "device.expanded.json",
                              "-o", work / "prog"], cwd=work, timeout=compile_budget,
                             mem_mb=8192, cancel=cancel)
            log += f"--- qccdc_cli {m}\n" + (cp.stdout + "\n" + cp.stderr)[-4000:]
            if cp.status in ("cancelled", "timeout"):
                return {"_status": cp.status, "summary": f"compiling {cp.status}", "log": log[-6000:]}
            if cp.ok and (work / "prog.tsir.json").exists():
                self._progress(jid, "stages", "checking stage artifacts, timing, cooling and source semantics")
                args = [tc.python, tc.bridge / "finish_staged.py", work / "prog",
                        "--qasm", work / "circuit.qasm", "--arch", work / "device.arch.json",
                        "--expanded-arch", work / "device.expanded.json", "--qccdc", tc.qccdc,
                        "--table", self.release.manifest["physics"].get("rank_table", "qccdsim_jones"),
                        "--timeout", str(compile_budget), "--semantic-timeout", str(semantic_budget),
                        "--semantic-mem-mb", str(semantic_memory)]
                if tc.qcheck is not None:
                    args += ["--qcheck", tc.qcheck]
                finished = run_limited(args, cwd=work, timeout=verification_budget,
                                       mem_mb=max(8192, semantic_memory), cancel=cancel)
                if finished.status in ("cancelled", "timeout"):
                    return {"_status": finished.status, "summary": f"stage verification {finished.status}",
                            "log": log[-6000:]}
                try:
                    if (work / "prog.compilation.json").exists():
                        compilation = strict_loads((work / "prog.compilation.json").read_bytes(), PROGRAM_LIMITS)
                    if finished.ok and isinstance(compilation, dict):
                        validate_compilation_outputs(work / "prog", compilation)
                        used = m
                        break
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    log += f"\nstage artifact validation rejected {m}: {exc}\n"
                    compilation = {"accepted": False, "error": str(exc)}
                reason = ((compilation or {}).get("error") if isinstance(compilation, dict) else None)
                log += f"\nstage verification rejected {m}: {reason or (finished.stderr or finished.stdout)[-1600:]}\n"
        if used is None:
            reason = ((compilation or {}).get("error") if isinstance(compilation, dict) else None)
            return {"_status": "failed", "design": design, "program": {"name": name, "qubits": q},
                    "summary": f"the compiler could not place {shown} on {dname}: "
                               + (reason or _compiler_reason(log)), "log": log[-6000:], "compilation": compilation}
        self._progress(jid, "replay", "replaying the program under the cost model")
        arch = Architecture.from_json(json.loads((work / "device.arch.json").read_text(encoding="utf-8")))
        prog = TSIR.from_json(json.loads((work / "prog.cooled.tsir.json").read_text(encoding="utf-8")))
        table = self.release.manifest["physics"].get("rank_table", "qccdsim_jones")
        res = replay(prog, arch, corrected_model(table), check_rules=True, keep_cycles=True)
        # an illegal program is the compiler's fault and is not shown as this design's run
        fault = _compiler_fault(res, tc, design=dname, program=shown)
        if fault:
            return {**fault, "design": design, "program": {"name": name, "qubits": q}, "log": log[-2000:]}
        try:
            t_tr, _ = replay_time(arch, prog, "transport_excitation")
        except Exception:
            t_tr = None
        perf = performance(arch, prog, res, table=table, t_transport_ms=t_tr)
        # Recheck the manifest and exact output hashes at the artifact boundary,
        # after the analysis work, instead of trusting a previously read flag.
        try:
            raw_bytes = (work / "prog.tsir.json").read_bytes()
            program_bytes = (work / "prog.cooled.tsir.json").read_bytes()
            cert_bytes = (work / "prog.qcert.json").read_bytes()
            validate_compilation_outputs(work / "prog", compilation)
            import hashlib
            if any(hashlib.sha256(data).hexdigest() != compilation[key] for data, key in
                   ((raw_bytes, "certified_sha256"), (program_bytes, "output_sha256"),
                    (cert_bytes, "certificate_sha256"))):
                raise ValueError("program changed during artifact ingestion")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return {"_status": "failed", "summary": f"compiled artifacts changed before storage: {exc}",
                    "design": design, "program": {"name": name, "qubits": q}, "log": log[-2000:]}
        d_dev = self.put_artifact(canonical_bytes(device), "application/json", f"run {jid}: device")
        d_prog = self.put_artifact(canonical_bytes(strict_loads(program_bytes, PROGRAM_LIMITS)),
                                   "application/json", f"run {jid}: program (cooled)")
        d_times = self.put_artifact(canonical_bytes({"times_us": _step_times(prog, res)}), "application/json",
                                    f"run {jid}: step start times")
        # the circuit and the compiler's certificate: the run's page draws the circuit beside the
        # compiled program from them (qccd.ir.source_map), as the site's compiled pages do
        d_cert = self.put_artifact(canonical_bytes(strict_loads(cert_bytes, PROGRAM_LIMITS)),
                                   "application/json", f"run {jid}: compiler certificate")
        d_qasm = self.put_artifact(qasm.encode("utf-8"), "text/plain", f"run {jid}: circuit")
        # the certified (uncooled) program too: with it a submission of the same circuit on the same
        # device adopts this compile instead of compiling again (the grade checks it all the same)
        d_raw = self.put_artifact(canonical_bytes(strict_loads(raw_bytes, PROGRAM_LIMITS)),
                                  "application/json", f"run {jid}: certified program")
        evidence_artifacts = {
            obj.stem: self.put_artifact(obj.read_bytes(), "application/json", f"run {jid}: compiler stage evidence")
            for obj in sorted((work / "prog.evidence" / "objects").glob("*.json"))
        }
        d_manifest = self.put_artifact((work / "prog.evidence" / "manifest.json").read_bytes(),
                                       "application/json", f"run {jid}: compiler stage manifest")
        d_report = self.put_artifact((work / "prog.compilation.json").read_bytes(),
                                     "application/json", f"run {jid}: compiler verification report")
        compilation_ref = {
            "report": d_report, "manifest": d_manifest, "objects": evidence_artifacts,
            **{key: compilation[key] for key in ("status", "manifest_sha256", "output_sha256",
                                                 "certified_sha256", "certificate_sha256", "physical_iterations")},
            "artifacts": {"program": d_prog, "certified": d_raw, "certificate": d_cert},
            "program": d_prog, "certified": d_raw, "certificate": d_cert,
        }
        headline = perf["bottleneck"][0] if perf["bottleneck"] else ""
        return {"summary": f"{shown} on {dname} (r{rev}): {perf['total']['ms']:g} ms per round. "
                           + headline,
                "run": {"run_id": jid, "program": {"name": name, "qubits": q, "source": source},
                        "design": design, "compiler": used, "performance": perf,
                        "compilation": compilation_ref,
                        "animate": params.get("animate", True) is not False,
                        "artifacts": {"device": d_dev, "program": d_prog, "times": d_times,
                                      "certificate": d_cert, "circuit": d_qasm, "certified": d_raw,
                                      "compilation_report": d_report, "compilation_manifest": d_manifest},
                        "view": f"/runview/{jid}",
                        "note": "a performance run with checked compilation stages and source semantics; "
                                "board eligibility is checked when submitted"},
                "compilation": compilation_ref, "log": log[-2000:]}

    # ------------------------------------------------------------------ reading runs

    def runs(self, limit: int = 30) -> list:
        out = []
        for row in self.store.all("SELECT id FROM jobs WHERE kind='run' ORDER BY created_at DESC LIMIT ?", (int(limit),)):
            j = self.job(row["id"])
            res = j.get("result") or {}
            run = res.get("run") or {}
            params = (res.get("params") or {}) if not run else {}
            out.append({"run_id": j["id"], "status": j["status"], "summary": res.get("summary"),
                        "program": ((run.get("program") or res.get("program") or {}).get("name")
                                    or str(params.get("program", ""))),
                        "draft": (run.get("design") or {}).get("draft") or params.get("branch"),
                        "revision": (run.get("design") or {}).get("revision"),
                        "total_ms": ((run.get("performance") or {}).get("total") or {}).get("ms"),
                        "created_at": j["created_at"]})
        return out

    def run(self, run_id: str) -> dict:
        from .core import WorkspaceError
        j = self.job(run_id)
        if j["kind"] != "run":
            raise WorkspaceError("not_found", f"{run_id} is not a run", status=404)
        res = j.get("result") or {}
        if j["status"] != "succeeded" or "run" not in res:
            raise WorkspaceError("not_ready", f"run {run_id} is {j['status']}: {res.get('summary') or j.get('error') or ''}",
                                 status=409)
        return res["run"]

    def compare_runs(self, run_ids) -> dict:
        from .core import WorkspaceError
        from .perf import compare
        if not isinstance(run_ids, (list, tuple)) or len(run_ids) != 2:
            raise WorkspaceError("bad_request", "compare exactly two runs", status=422)
        a, b = (self.run(x) for x in run_ids)
        la = f"{self._design_title_of(a['design']['draft'])} r{a['design']['revision']}"
        lb = f"{self._design_title_of(b['design']['draft'])} r{b['design']['revision']}"
        if la == lb:
            la, lb = f"{la} ({a['program']['name']})", f"{lb} ({b['program']['name']})"
        out = compare(a, b, (la, lb))
        out["runs"] = [{"run_id": x["run_id"], "label": lab, "program": x["program"]["name"],
                        "design": x["design"]["name"], "total_ms": x["performance"]["total"]["ms"]}
                       for x, lab in ((a, la), (b, lb))]
        out["view"] = f"/compare?runs={a['run_id']},{b['run_id']}"
        if a["program"]["name"] != b["program"]["name"]:
            out["warning"] = "the two runs ran DIFFERENT programs; their times are not comparable"
        return out
