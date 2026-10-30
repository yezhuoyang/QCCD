"""The C2LR router's native stages and their independent re-check (docs/c2lr.md section 6d).

The staged driver accepts a native programme only with seven checked stage boundaries.  qccdc's
are re-checked by `qccdc check-stages`, which cannot read a swap-or-stay step; the router's by
`qccd.compile.c2lr_stages`.  These hold that checker to what it must refuse -- each boundary's
typical forgery, and evidence that disagrees with the executable -- and the whole path, from
the router's work directory to an accepted, evidenced programme in a workspace.
"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.api import Machine  # noqa: E402
from qccd.compile import c2lr_stages as CS  # noqa: E402
from qccd.compile.c2lr_router import STAGE_ENGINE, compile_with_stages  # noqa: E402
from qccd.workspace.evaluator import Toolchain  # noqa: E402

#: every kind of op the router lowers, a measurement of a whole register and a reset
CIRCUIT = ('OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[3];\ncreg c[3];\nh q[0];\ncx q[0],q[1];\n'
           'cz q[1],q[2];\nx q[2];\nmeasure q -> c;\nreset q[0];\n')


def _toolchain() -> Toolchain:
    tc = Toolchain.discover()
    if tc.qccdc is None:
        pytest.skip("no qccdc to parse the source circuit with")
    return tc


@pytest.fixture
def work(tmp_path):
    tc = _toolchain()
    w = tmp_path / "work"
    w.mkdir()
    (w / "device.arch.json").write_text(json.dumps(Machine.c2lr_grid(3, 3).arch.to_json()), encoding="utf-8")
    (w / "circuit.qasm").write_text(CIRCUIT, encoding="utf-8")
    ex = subprocess.run([sys.executable, str(ROOT / "Compiler" / "bridge" / "export_arch.py"),
                         str(w / "device.arch.json"), "-o", str(w / "device.expanded.json")],
                        capture_output=True, text=True, timeout=60)
    assert ex.returncode == 0, ex.stderr
    compile_with_stages(w, name="probe")
    return w, tc


def _doc(w: Path) -> dict:
    return json.loads((w / "prog.stages.json").read_text(encoding="utf-8"))


def _check(w: Path, tc: Toolchain, doc: dict | None = None) -> dict:
    return CS.check_manifest(_doc(w) if doc is None else doc, circuit_path=w / "circuit.qasm",
                             arch_path=w / "device.arch.json", expanded_arch_path=w / "device.expanded.json",
                             program_path=w / "prog.tsir.json", qccdc=tc.qccdc)


def _first(doc: dict, sid: str) -> dict:
    return next(s for s in doc["stages"] if s["id"] == sid)


def _rebind(w: Path, doc: dict) -> dict:
    doc["bindings"]["output_tsir_sha256"] = hashlib.sha256((w / "prog.tsir.json").read_bytes()).hexdigest()
    return doc


def _rewrite_program(w: Path, edit) -> None:
    p = json.loads((w / "prog.tsir.json").read_text(encoding="utf-8"))
    edit(p)
    (w / "prog.tsir.json").write_text(json.dumps(p), encoding="utf-8")


# ------------------------------------------------------------------ the evidence is accepted

def test_the_router_s_stages_are_accepted_boundary_by_boundary(work):
    w, tc = work
    doc = _doc(w)
    assert doc["engine"] == STAGE_ENGINE and doc["accepted"] is True
    assert {s["id"] for s in doc["stages"]} == set(CS.NATIVE)
    assert all(s["check"]["status"] == "not_run" for s in doc["stages"])   # the router claims nothing
    verdict = _check(w, tc)
    assert verdict["accepted"] is True and verdict["checker"] == CS.METHOD
    assert len(verdict["stage_checks"]) == len(doc["stages"])
    assert all(c["status"] == "passed" and c["coverage"] for c in verdict["stage_checks"])
    assert verdict["checked"]["operations"] == 8 and verdict["checked"]["steps"] > 0   # h cx cz x, 3 measures, a reset


def test_measurements_carry_their_classical_bits_into_the_evidence(work):
    w, _ = work
    ops = _first(_doc(w), "circuit_normalization")["output"]["ops"]
    assert [(o["qubits"], o["clbits"]) for o in ops if o["name"] == "measure"] == [([0], [0]), ([1], [1]), ([2], [2])]


# ------------------------------------------------------------------ each boundary's forgery

def _forged(doc: dict, sid: str, edit) -> dict:
    doc = copy.deepcopy(doc)
    edit(_first(doc, sid))
    return doc


@pytest.mark.parametrize("sid,edit,says", [
    ("circuit_normalization", lambda s: s["input"]["ops"][0].update(name="x"), "normalization input differs"),
    ("circuit_normalization", lambda s: s["output"]["ops"][0].update(name="x"), "normalization of op 0 .h. differs"),
    ("circuit_normalization", lambda s: s["output"]["ops"][4].update(clbits=[2]), "changed by normalization"),
    ("dependency_analysis", lambda s: s["output"]["layers"].__setitem__(2, 0), "dependency"),
    ("initial_placement", lambda s: s["output"]["placement"].update(
        q1=s["output"]["placement"]["q0"]), "two crystals start in the leg"),
    ("interaction_placement", lambda s: s["output"][0]["assignment"]["ions"].reverse() if
     len(s["output"][0]["assignment"]["ions"]) > 1 else s["output"][0]["assignment"].update(ions=["q2"]),
     "wrong ordered operands"),
    ("native_gate_lowering", lambda s: s["output"][0]["pulses"][0]["params"].__setitem__(0, 1.0),
     "native pulse semantics differ"),
    ("broadcast_packing", lambda s: s["output"][0]["meta"].update(op=[99]), "provenance"),
])
def test_a_forged_boundary_is_refused(work, sid, edit, says):
    w, tc = work
    with pytest.raises(CS.Failed, match=says):
        _check(w, tc, _forged(_doc(w), sid, edit))


def test_a_step_the_evidence_misreports_is_refused(work):
    w, tc = work

    def edit(s):
        step = s["output"]["steps"][0]
        step["template"]["toward"] = [] if step["template"].get("toward") else ["q0"]
    with pytest.raises(CS.Failed, match="transport"):
        _check(w, tc, _forged(_doc(w), "transport_synthesis", edit))


def test_the_evidence_must_be_the_router_s(work):
    w, tc = work
    doc = _doc(w)
    doc["engine"] = "general"
    with pytest.raises(CS.Failed, match="not the C2LR router"):
        _check(w, tc, doc)


# ------------------------------------------------------------------ evidence against the executable

def test_an_executable_changed_after_compiling_breaks_its_binding(work):
    w, tc = work
    _rewrite_program(w, lambda p: p["meta"].update(changed=True))
    with pytest.raises(CS.Failed, match="binding mismatch: output_tsir_sha256"):
        _check(w, tc)


def test_an_executable_whose_motion_differs_from_the_checked_steps_is_refused(work):
    w, tc = work

    def edit(p):
        steps = [i for i in p["instructions"] if i["type"] == "simd"]
        steps[-1]["template"]["toward"] = []          # its last exit, now a stay of everyone
    _rewrite_program(w, edit)
    with pytest.raises(CS.Failed, match="motion|replay|addresses"):
        _check(w, tc, _rebind(w, _doc(w)))


def test_an_executable_command_the_packing_did_not_check_is_refused(work):
    w, tc = work

    def edit(p):
        g = next(i for i in p["instructions"] if i["type"] == "gate" and i.get("gate") == "R")
        g["params"][0][1] += 0.5
    _rewrite_program(w, edit)
    with pytest.raises(CS.Failed, match="packed stage outputs differ"):
        _check(w, tc, _rebind(w, _doc(w)))


# ------------------------------------------------------------------ the staged driver

def _finish(w: Path, tc: Toolchain) -> dict:
    from qccd.compile import driver
    return driver.finish_compilation(w / "prog", qasm=w / "circuit.qasm", arch_path=w / "device.arch.json",
                                     expanded_arch_path=w / "device.expanded.json",
                                     toolchain=replace(tc, qcheck=None), timeout=120)


def test_the_staged_driver_accepts_a_c2lr_programme_with_every_stage_checked(work):
    from qccd.compile.stages import verify_manifest
    w, tc = work
    report = _finish(w, tc)
    assert report["accepted"], report.get("error")
    assert report["engine"] == STAGE_ENGINE and report["native_check"]["checker"] == CS.METHOD
    assert report["semantics"]["o2_semantics"].startswith("ok")
    checked = verify_manifest(w / "prog.evidence" / "manifest.json", expected_manifest_sha256=report["manifest_sha256"])
    assert checked["reported_status"] == "passed" and checked["full_pipeline"]
    assert (w / "prog.cooled.tsir.json").exists()


def test_the_staged_driver_refuses_a_c2lr_programme_changed_after_compiling(work):
    w, tc = work
    _rewrite_program(w, lambda p: p["meta"].update(changed=True))
    report = _finish(w, tc)
    assert report["accepted"] is False and "checker" in report["error"]
    assert not (w / "prog.cooled.tsir.json").exists()


# ------------------------------------------------------------------ the workspace

def test_a_c2lr_design_compiles_runs_and_adopts_with_stage_evidence(tmp_path):
    from qccd.workspace.app import Workspace
    from qccd.workspace.compilation import has_stage_evidence
    _toolchain()
    human = {"kind": "human", "id": "test"}
    ws = Workspace.init(tmp_path / "ws", "c2lr_rep5_mem_basr@1")

    def wait(jid):
        deadline = time.monotonic() + 600
        while ws.job(jid)["status"] in ("queued", "running"):
            assert time.monotonic() < deadline, "the job did not finish"
            time.sleep(0.2)
        return ws.job(jid)
    try:
        job = wait(ws.start_job(human, "compile", {})["job_id"])
        assert job["status"] == "succeeded", job.get("result")
        fp = job["result"]["final_program"]
        assert fp["compiler"] == "c2lr-router" and "the C2LR router" in fp["label"]
        assert has_stage_evidence(ws, fp)
        ws.apply_change_set({"expected_revision": ws.head("main").revision, "request_id": "a", "mode": "apply",
                             "operations": [job["result"]["adopt_with"]]}, human)
        assert ws.head("main").state.final_program["compiler"] == "c2lr-router"
        run = wait(ws.start_job(human, "run", {"program": "ghz4"})["job_id"])
        assert run["status"] == "succeeded", run.get("result")
        assert run["result"]["run"]["compiler"] == "c2lr-router"
        assert run["result"]["run"]["compilation"]["status"] == "passed"
    finally:
        ws.close()
