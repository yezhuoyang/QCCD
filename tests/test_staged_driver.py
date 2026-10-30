"""Acceptance-path faults must never publish a supposedly verified executable.

Most tests stub only external process results: physical transforms, serialized
rechecks, hardware replay, and evidence recording remain real. Separate smoke
tests exercise an available staged native compiler and the semantic bridge.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qccd.compile import driver
from qccd.compile.physical_stages import ANNOTATIONS, finish_program
from qccd.compile.stages import STAGE_IDS, verify_manifest
from qccd.cost import corrected_model
from qccd.ir import TSIR, Instruction
from qccd.workspace.procs import RunResult
from test_physical_stages import architecture, gate, program


def good_semantics(**changes):
    return {"binding": "ok", "transport": "ok", "o1_transport": "ok", "order": "ok",
            "o2_semantics": "ok", "unrealised_ops": 0, "readout": "ok: no readout",
            "R10": "partial", **changes}


@pytest.mark.parametrize("field,value", [
    ("binding", "FAILED"), ("transport", "failed"), ("o1_transport", "missing"),
    ("order", "FAILED"), ("o2_semantics", "not checked: too many qubits"),
    ("unrealised_ops", 1), ("readout", ["wrong measurement"]), ("R10", "FAILED"),
])
def test_semantic_errors_requires_every_established_obligation(field, value):
    assert driver.semantic_errors(good_semantics(**{field: value}), require_lean=False)


def test_semantics_allows_python_proof_scope_only_when_lean_is_not_required():
    assert driver.semantic_errors(good_semantics(), require_lean=False) == []
    assert driver.semantic_errors(good_semantics(), require_lean=True)
    assert driver.semantic_errors(good_semantics(lean="accepted", R10="passed"), require_lean=True) == []
    assert driver.semantic_errors(good_semantics(o2_semantics="ok (max entry error 1.0e-15)"),
                                  require_lean=False) == []


@pytest.mark.parametrize("malformed", [None, [], 1, {"o2_semantics": None},
                                        good_semantics(o2_semantics=False)])
def test_semantic_errors_handles_malformed_json_as_rejection(malformed):
    assert driver.semantic_errors(malformed, require_lean=False)


def test_semantic_errors_does_not_accept_missing_obligations():
    assert driver.semantic_errors({}, require_lean=False)
    for field in ("binding", "transport", "o1_transport", "order", "o2_semantics", "unrealised_ops", "R10"):
        verdict = good_semantics()
        del verdict[field]
        assert driver.semantic_errors(verdict, require_lean=False)


def test_certified_baseline_only_removes_fresh_cooling_and_keeps_annotations():
    native = program(gate(), quanta=5)
    result = finish_program(native, architecture(), corrected_model())
    assert result.ok
    baseline = driver.certified_baseline(native, result.program)
    assert [i.id for i in baseline] == [i.id for i in native]
    assert baseline.instructions[-1].t0 == 300
    assert baseline.instructions[-1].operating_point == result.program.instructions[-1].operating_point
    assert native.instructions[-1].t0 is None


@pytest.mark.parametrize("fault", ["change_body", "drop_gate", "reverse_order", "insert_reset",
                                    "duplicate_fresh_id", "bad_allocator", "change_metadata"])
def test_certified_baseline_refuses_changes_outside_physical_annotations(fault):
    native = program(gate())
    final = finish_program(native, architecture(), corrected_model()).program
    if fault == "change_body":
        final.instructions[-1] = replace(final.instructions[-1], gate="SWAP")
    elif fault == "drop_gate":
        final.instructions.pop()
    elif fault == "reverse_order":
        final.instructions.reverse()
    elif fault == "insert_reset":
        final.instructions.append(Instruction(type="reset", id=final.next_id(), ions=("q0",)))
    elif fault == "duplicate_fresh_id":
        final.instructions.insert(1, copy.deepcopy(final.of_type("cool")[0]))
    elif fault == "bad_allocator":
        final.id_seq = 0
    elif fault == "change_metadata":
        final.meta["source"] = "a different source"
    with pytest.raises(driver.CompilationRejected):
        driver.certified_baseline(native, final)


@pytest.fixture
def job(tmp_path, monkeypatch):
    prefix = tmp_path / "prog"
    file = lambda suffix: Path(str(prefix) + suffix)
    qasm = tmp_path / "circuit.qasm"
    qasm.write_text('OPENQASM 2.0; include "qelib1.inc"; qreg q[2]; cx q[0],q[1];', encoding="utf-8")
    arch_path = tmp_path / "device.arch.json"
    expanded = tmp_path / "device.expanded.json"
    document = architecture().to_json(expanded=True)
    arch_path.write_text(json.dumps(document), encoding="utf-8")
    expanded.write_text(json.dumps(document), encoding="utf-8")
    native = program(gate())
    native.save(file(".tsir.json"))
    file(".qcert.json").write_text(json.dumps({"unrealised": []}), encoding="utf-8")
    stages = {
        "schema": "qccd.compiler.stages.v1", "accepted": True, "engine": "test-process-boundary",
        "stages": [{"id": stage, "name": stage, "input": {"fixture": index},
                    "output": {"fixture": index + 1}, "witness": {"fixture": "external checker stub"},
                    "check": {"status": "passed", "method": "test.external_native_checker",
                              "coverage": ["external native stage verdict"]}}
                   for index, stage in enumerate(STAGE_IDS[:7])],
    }
    file(".stages.json").write_text(json.dumps(stages), encoding="utf-8")
    toolchain = SimpleNamespace(qccdc="test-native-compiler", qcheck=None,
                               python=sys.executable, bridge=tmp_path,
                               identity=lambda: {"test": "mock external processes only"})
    state = {"semantic": good_semantics(), "semantic_status": "ok",
             "native_verdict": {"accepted": True}, "native_status": "ok", "calls": []}

    def run(args, **kwargs):
        state["calls"].append([str(arg) for arg in args])
        native_check = "check-stages" in [str(arg) for arg in args]
        verdict = state["native_verdict" if native_check else "semantic"]
        status = state["native_status" if native_check else "semantic_status"]
        text = verdict if isinstance(verdict, str) else json.dumps(verdict)
        return RunResult(status, 0 if status == "ok" else 1, text, "checker rejected fixture", 0)

    monkeypatch.setattr(driver, "run_limited", run)

    def finish():
        return driver.finish_compilation(prefix, qasm=qasm, arch_path=arch_path,
                                         expanded_arch_path=expanded, toolchain=toolchain)

    return SimpleNamespace(prefix=prefix, file=file, qasm=qasm, arch_path=arch_path,
                           expanded=expanded, native=native, stages=stages,
                           state=state, finish=finish)


def test_acceptance_publishes_rechecked_outputs_and_invalidates_old_thermal_claims(job):
    report = job.finish()
    assert report["accepted"], report
    assert job.file(".cooled.tsir.json").exists()
    assert TSIR.load(job.file(".native.tsir.json")).to_json() == job.native.to_json()
    manifest = verify_manifest(job.file(".evidence") / "manifest.json",
                               expected_manifest_sha256=report["manifest_sha256"])
    assert manifest["status"] == "passed"
    raw = json.loads((job.file(".evidence") / "manifest.json").read_text(encoding="utf-8"))
    records = [json.loads((job.file(".evidence") / "objects" / (digest + ".json")).read_text(encoding="utf-8"))
               for digest in raw["records"]]
    stale_thermal = next(record for record in records
                         if record["stage_id"] == "thermal_analysis" and record["check"]["status"] == "failed")
    active_thermal_ref = raw["active"]["thermal_analysis"]
    assert stale_thermal != json.loads((job.file(".evidence") / "objects" /
                                       (active_thermal_ref + ".json")).read_text(encoding="utf-8"))
    assert any(record["invalidated"] for record in records if record["stage_id"] == "operating_point_binding")


def test_missing_native_stage_evidence_rejects_and_removes_stale_executable(job):
    job.file(".stages.json").unlink()
    job.file(".cooled.tsir.json").write_text("stale accepted output", encoding="utf-8")
    report = job.finish()
    assert report["accepted"] is False
    assert not job.file(".cooled.tsir.json").exists()
    assert not job.state["calls"]


@pytest.mark.parametrize("native_status,native_verdict", [
    ("nonzero", {"accepted": True}), ("ok", {"accepted": False}),
])
def test_mismatched_native_evidence_or_failed_checker_cannot_be_promoted(job, native_status, native_verdict):
    job.state.update(native_status=native_status, native_verdict=native_verdict)
    report = job.finish()
    assert report["accepted"] is False
    assert not job.file(".cooled.tsir.json").exists()
    assert len(job.state["calls"]) == 1


@pytest.mark.parametrize("status,verdict", [
    ("nonzero", good_semantics()),
    ("ok", good_semantics(o2_semantics="not checked: outside supported fragment")),
    ("ok", good_semantics(unrealised_ops=1)),
    ("ok", good_semantics(order="FAILED")),
    ("ok", "null"), ("ok", "[]"), ("ok", '{"unrelated": NaN}'),
])
def test_semantic_exit_status_and_partial_or_malformed_results_fail_closed(job, status, verdict):
    job.state.update(semantic_status=status, semantic=verdict)
    report = job.finish()
    assert report["accepted"] is False
    assert not job.file(".cooled.tsir.json").exists()
    saved = json.loads(job.file(".compilation.json").read_text(encoding="utf-8"))
    assert saved["accepted"] is False


def test_read_back_must_be_exactly_the_accepted_stage_output(job, monkeypatch):
    save = TSIR.save

    def corrupt_serialization(self, path, **kwargs):
        result = save(self, path, **kwargs)
        if str(path).endswith(".validation.tsir.json"):
            document = json.loads(Path(path).read_text(encoding="utf-8"))
            # The commands and all physical annotations still satisfy replay;
            # the serialized result nevertheless differs from accepted output.
            document["meta"]["serializer_injected"] = "not accepted by previous stages"
            Path(path).write_text(json.dumps(document), encoding="utf-8")
        return result

    monkeypatch.setattr(TSIR, "save", corrupt_serialization)
    report = job.finish()
    assert report["accepted"] is False
    assert not job.file(".cooled.tsir.json").exists()


def test_missing_context_input_replaces_a_stale_success_report(job):
    job.file(".compilation.json").write_text('{"accepted": true}', encoding="utf-8")
    job.qasm.unlink()
    report = job.finish()
    assert report["accepted"] is False
    assert json.loads(job.file(".compilation.json").read_text(encoding="utf-8"))["accepted"] is False


def _real_compilation(tmp_path):
    from qccd.api import Machine
    from qccd.workspace.evaluator import Toolchain

    toolchain = Toolchain.discover()
    if toolchain.qccdc is None:
        pytest.skip("native compiler is not available")
    help_result = subprocess.run([str(toolchain.qccdc)], capture_output=True, text=True, timeout=20)
    if "check-stages" not in help_result.stdout + help_result.stderr:
        pytest.skip("available native compiler predates checked stage evidence")
    qasm = tmp_path / "source.qasm"
    qasm.write_text('OPENQASM 2.0; include "qelib1.inc"; qreg q[2]; creg c[2]; '
                    'h q[0]; cx q[0],q[1]; measure q -> c;', encoding="utf-8")
    arch_path = tmp_path / "device.arch.json"
    arch_path.write_text(json.dumps(Machine.chain(4).arch.to_json()), encoding="utf-8")
    expanded = tmp_path / "device.expanded.json"
    export = subprocess.run([toolchain.python, str(toolchain.bridge / "export_arch.py"),
                             str(arch_path), "-o", str(expanded)],
                            capture_output=True, text=True, timeout=30)
    assert export.returncode == 0, export.stderr
    prefix = tmp_path / "prog"
    compile_result = subprocess.run([str(toolchain.qccdc), "compile", str(qasm), "--arch",
                                     str(expanded), "-o", str(prefix)],
                                    capture_output=True, text=True, timeout=60)
    assert compile_result.returncode == 0, compile_result.stdout + compile_result.stderr
    # The driver's requirement is explicit: this smoke test checks Python source
    # semantics. Availability of an optional Lean binary does not change it.
    toolchain = replace(toolchain, qcheck=None)
    return prefix, dict(qasm=qasm, arch_path=arch_path, expanded_arch_path=expanded,
                        toolchain=toolchain, timeout=60)


def test_real_compiler_and_semantic_bridge_accept_a_bell_circuit(tmp_path):
    prefix, kwargs = _real_compilation(tmp_path)
    report = driver.finish_compilation(prefix, **kwargs)
    assert report["accepted"], report
    assert report["semantics"]["o2_semantics"] == "ok"
    assert Path(str(prefix) + ".cooled.tsir.json").exists()


def test_real_saved_stage_checker_rejects_program_evidence_mismatch(tmp_path):
    prefix, kwargs = _real_compilation(tmp_path)
    path = Path(str(prefix) + ".tsir.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    document["meta"]["changed_after_native_compilation"] = True
    path.write_text(json.dumps(document), encoding="utf-8")
    report = driver.finish_compilation(prefix, **kwargs)
    assert report["accepted"] is False
    assert "checker" in report["error"] or "native" in report["error"]
    assert not Path(str(prefix) + ".cooled.tsir.json").exists()


def _wait_for_job(workspace, job_id):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        job = workspace.job(job_id)
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.05)
    pytest.fail("Studio run did not finish within 120 seconds")


def _studio_workspace(tmp_path):
    from qccd.workspace.app import Workspace
    from qccd.workspace.evaluator import Toolchain
    if Toolchain.discover().qccdc is None:
        pytest.skip("native compiler is not available")
    return Workspace.init(tmp_path / "studio", "ghz4@1")


def test_studio_run_stores_checked_evidence_and_pins_canonical_artifacts(tmp_path, monkeypatch):
    from qccd.workspace import procs
    workspace = _studio_workspace(tmp_path)
    real_run = procs.run_limited
    finishing_calls = []

    def track_run(args, **kwargs):
        if any(str(arg).endswith("finish_staged.py") for arg in args):
            finishing_calls.append(([str(arg) for arg in args], kwargs))
        return real_run(args, **kwargs)

    monkeypatch.setattr(procs, "run_limited", track_run)
    try:
        started = workspace.start_job({"kind": "human", "id": "test"}, "run", {"program": "ghz4"})
        job = _wait_for_job(workspace, started["job_id"])
        assert job["status"] == "succeeded", job
        run = job["result"]["run"]
        compilation = run["compilation"]
        assert compilation == job["result"]["compilation"]
        assert compilation["status"] == "passed"
        for role in ("program", "certified", "certificate"):
            assert compilation[role] == run["artifacts"][role]
            assert workspace.get_artifact(compilation[role])
        report = json.loads(workspace.get_artifact(compilation["report"]))
        manifest = json.loads(workspace.get_artifact(compilation["manifest"]))
        assert report["accepted"] and manifest["status"] == "passed"
        assert compilation["manifest_sha256"] == manifest["manifest_sha256"]
        assert compilation["output_sha256"] == report["output_sha256"]
        assert compilation["objects"]
        assert all(workspace.get_artifact(ref) for ref in compilation["objects"].values())
        args, limits = finishing_calls[0]
        semantic_timeout = float(args[args.index("--semantic-timeout") + 1])
        semantic_memory = int(args[args.index("--semantic-mem-mb") + 1])
        assert semantic_timeout >= workspace.release.manifest["limits"].get("lean_timeout_s", 1800)
        assert limits["timeout"] > semantic_timeout
        assert limits["mem_mb"] >= semantic_memory
    finally:
        workspace.close()


def test_studio_run_rejects_executable_changed_after_successful_worker(tmp_path, monkeypatch):
    from qccd.workspace import procs
    workspace = _studio_workspace(tmp_path)
    real_run = procs.run_limited

    def mutate_output(args, **kwargs):
        result = real_run(args, **kwargs)
        if result.ok and any(str(arg).endswith("finish_staged.py") for arg in args):
            target = Path(kwargs["cwd"]) / "prog.cooled.tsir.json"
            content = json.loads(target.read_text(encoding="utf-8"))
            content["meta"]["changed_after_worker"] = True
            target.write_text(json.dumps(content), encoding="utf-8")
        return result

    monkeypatch.setattr(procs, "run_limited", mutate_output)
    try:
        started = workspace.start_job({"kind": "human", "id": "test"}, "run", {"program": "ghz4"})
        job = _wait_for_job(workspace, started["job_id"])
        assert job["status"] == "failed", job
        assert "accepted artifact changed" in job["result"]["summary"]
        assert "run" not in job["result"]
        assert workspace.head().state.final_program is None
    finally:
        workspace.close()


def test_studio_run_stores_the_checked_bytes_when_files_change_during_ingestion(tmp_path, monkeypatch):
    workspace = _studio_workspace(tmp_path)
    original_put = workspace.put_artifact
    changed = []

    def change_after_device(data, media_type, label=""):
        if label.startswith("run ") and label.endswith(": device"):
            target = next((workspace.root / ".qccd" / "runs").glob("*/prog.cooled.tsir.json"))
            document = json.loads(target.read_text(encoding="utf-8"))
            document["meta"]["changed_during_ingestion"] = True
            target.write_text(json.dumps(document), encoding="utf-8")
            changed.append(target)
        return original_put(data, media_type, label)

    monkeypatch.setattr(workspace, "put_artifact", change_after_device)
    try:
        started = workspace.start_job({"kind": "human", "id": "test"}, "run", {"program": "ghz4"})
        job = _wait_for_job(workspace, started["job_id"])
        assert job["status"] == "succeeded", job
        assert changed
        run = job["result"]["run"]
        stored = json.loads(workspace.get_artifact(run["artifacts"]["program"]))
        assert "changed_during_ingestion" not in stored["meta"]
        from qccd.workspace.compilation import has_stage_evidence
        from qccd.workspace.jsonsafe import canonical_bytes
        reference = workspace.put_artifact(canonical_bytes({**run["compilation"],
            "schema": "qccd.compilation.reference.v1"}), "application/json")
        assert has_stage_evidence(workspace, {"artifact": run["artifacts"]["program"],
            "certified": run["artifacts"]["certified"], "certificate": run["artifacts"]["certificate"],
            "compilation": reference})
    finally:
        workspace.close()
