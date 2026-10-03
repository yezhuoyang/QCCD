"""A program the compiler gets wrong is the compiler's fault, not the person's design's.

A Mac compiled the BB round onto a new 11x11 grid with a withdrawn compiler (73864a5c493d,
from before the R22 router fix), and the workspace animated the program -- ions moving in
different directions in one cycle -- then adopted it and graded it "not eligible: rules
failed", as if the design had broken the rule.  Now:

  * a withdrawn compiler release is refused before anything is compiled, with what to do;
  * a run or a compile whose program breaks a rule fails as a COMPILER fault: the program is
    not shown, not adopted and not graded.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

import pytest

from qccd.api import Machine
from qccd.arch.device import Architecture
from qccd.cost.models import corrected_model
from qccd.ir.tsir import TSIR
from qccd.verify.replay import replay
from qccd.workspace import results, toolchain
from qccd.workspace.app import Workspace, WorkspaceError
from qccd.workspace.evaluator import Toolchain

REPO = Path(__file__).resolve().parents[1]
HUMAN = {"kind": "human", "id": "cli"}
needs_compiler = pytest.mark.skipif(Toolchain.discover().qccdc is None, reason="qccdc_cli is not built")


def _two_way_cycle():
    """One cycle on a 3x3 grid in which one ion moves +x and another +y: two waveforms."""
    arch = Architecture.from_json(Machine.grid(3, 3, name="g").arch.to_json())
    prog = TSIR.from_json({
        "name": "two-ways", "arch_spec": "g", "metrics": {}, "meta": {}, "id_seq": 2,
        "instructions": [
            {"type": "init", "id": 0, "placement": {"a": "T0_0h", "b": "T2_0v"}, "quanta": {"a": 0.0, "b": 0.0}},
            {"type": "simd", "id": 1, "class": "shuttle", "mode": "inter", "participants": [
                {"ion": "a", "from": "T0_0h", "to": "T1_0h", "via": ["T0_0h.b", "T1_0h.a"]},
                {"ion": "b", "from": "T2_0v", "to": "T2_1v", "via": ["T2_0v.b", "T2_1v.a"]}]},
        ]})
    return arch, prog


def test_a_program_that_breaks_a_rule_is_the_compilers_fault():
    arch, prog = _two_way_cycle()
    res = replay(prog, arch, corrected_model("qccdsim_jones"), check_rules=True)
    tc = Toolchain(qccdc=Path("qccdc_cli"), qcheck=None, python="python", bridge=REPO)
    fault = results._compiler_fault(res, tc, design="my grid", program="BB [[144,12,12]]")
    assert fault["_status"] == "failed" and fault["compiler_fault"]["rules"] == ["R22"]
    s = fault["summary"]
    assert "fault in the compiler, not in my grid" in s and "R22" in s and "not shown, adopted or graded" in s
    # the same two ions moving one after the other is legal, and is not a fault
    ok = TSIR.from_json({**json.loads(json.dumps(prog.to_json())), "instructions": [
        prog.to_json()["instructions"][0],
        {"type": "simd", "id": 1, "class": "shuttle", "mode": "inter", "participants": [
            {"ion": "a", "from": "T0_0h", "to": "T1_0h", "via": ["T0_0h.b", "T1_0h.a"]}]},
        {"type": "simd", "id": 2, "class": "shuttle", "mode": "inter", "participants": [
            {"ion": "b", "from": "T2_0v", "to": "T2_1v", "via": ["T2_0v.b", "T2_1v.a"]}]}]})
    assert results._compiler_fault(replay(ok, arch, corrected_model("qccdsim_jones"), check_rules=True), tc,
                                   design="my grid", program="x") is None


def test_the_withdrawn_list_is_the_73864a5c493d_release_as_it_was_published():
    # the pin that published it, from history: a shallow clone does not have that commit
    old = subprocess.run(["git", "show", "3b1546c:qccd/workspace/toolchain.json"], cwd=REPO,
                         capture_output=True, text=True)
    if old.returncode:
        pytest.skip("needs the commit that pinned 73864a5c493d (3b1546c); this clone has no such history")
    pinned = json.loads(old.stdout)["qccdc_cli"]
    assert pinned["version"] == "73864a5c493d"
    digests = {a.get("unpacked_sha256") or a["sha256"] for a in pinned["assets"].values()}
    assert digests == {d for d, v in toolchain.RETIRED_QCCDC.items() if v == "73864a5c493d"}
    assert toolchain.manifest()["qccdc_cli"]["version"] not in toolchain.RETIRED_QCCDC.values()


def test_a_withdrawn_compiler_is_refused_before_compiling(tmp_path, monkeypatch):
    exe = tmp_path / "qccdc_cli.exe"
    exe.write_bytes(b"an old compiler")
    monkeypatch.setitem(toolchain.RETIRED_QCCDC, hashlib.sha256(exe.read_bytes()).hexdigest(), "73864a5c493d")
    tc = Toolchain(qccdc=exe, qcheck=None, python="python", bridge=REPO)
    with pytest.raises(WorkspaceError) as e:
        results._refuse_withdrawn_compiler(tc)
    assert e.value.code == "toolchain_withdrawn"
    assert "73864a5c493d" in str(e.value) and "R22" in str(e.value) and "Nothing was compiled" in str(e.value)
    # and `qccd toolchain status` says so
    monkeypatch.setenv("QCCD_QCCDC", str(exe))
    assert "73864a5c493d" in toolchain.status()["in_use"]["withdrawn"]
    # a compiler that is not withdrawn passes
    other = tmp_path / "new.exe"
    other.write_bytes(b"a current compiler")
    results._refuse_withdrawn_compiler(Toolchain(qccdc=other, qcheck=None, python="python", bridge=REPO))


@pytest.fixture
def ws(tmp_path):
    w = Workspace.init(tmp_path / "ws", "ghz4@1")
    yield w
    w.close()


def _wait(ws, jid, timeout=300):
    t0 = time.time()
    while ws.job(jid)["status"] not in ("succeeded", "failed", "internal_error", "cancelled", "timeout"):
        assert time.time() - t0 < timeout, "the job did not finish"
        time.sleep(0.3)
    return ws.job(jid)


def _always_a_fault(res, tc, *, design, program):
    return {"_status": "failed", "summary": f"the compiler made a program for {program} on {design} that breaks "
                                            "rule R22 ... That is a fault in the compiler",
            "compiler_fault": {"rules": ["R22"]}}


@needs_compiler
def test_a_run_whose_program_breaks_a_rule_fails_and_is_never_adopted(ws, monkeypatch):
    monkeypatch.setattr(results, "_compiler_fault", _always_a_fault)
    j = _wait(ws, ws.start_job(HUMAN, "run", {"program": "ghz4"})["job_id"])
    assert j["status"] == "failed" and "fault in the compiler" in j["result"]["summary"]
    assert j["result"]["compiler_fault"]["rules"] == ["R22"]
    with pytest.raises(WorkspaceError):
        ws.run(j["id"])                       # nothing to animate
    rel = ws.board(None)
    head = ws.head("main")
    assert ws._run_to_adopt("main", rel, ws.replayed("main", head.revision).arch_digest(), head.revision) is None


@needs_compiler
def test_a_submission_whose_program_breaks_a_rule_is_not_graded(ws, monkeypatch):
    monkeypatch.setattr(results, "_compiler_fault", _always_a_fault)
    out = ws.submit_design(HUMAN, design="main")
    j = _wait(ws, out["job_id"])
    assert j["status"] == "failed" and "fault in the compiler" in j["result"]["summary"]
    assert ws.store.one("SELECT COUNT(*) AS n FROM submissions")["n"] == 0
