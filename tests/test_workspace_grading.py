"""Compilation, immutable local submissions and the evaluator's integrity.

Uses the REAL toolchain (qccdc_cli, insert_cooling, the Lean qcheck, check_cert); skipped
when it is not built.  One workspace is compiled and graded once per module; each tamper
test copies the frozen bundle, changes one thing, re-seals it with fresh digests (so the
bundle stage passes and the check under test is the one that must catch it), and grades.
"""

from __future__ import annotations

import copy
import io
import json
import os
import shutil
import stat
import time
import zipfile
from pathlib import Path

import pytest

from qccd.workspace.app import Workspace, WorkspaceError
from qccd.workspace.bundle import BundleError, archive_bundle, extract_archive, read_bundle, write_bundle
from qccd.workspace.evaluator import Toolchain, grade
from qccd.workspace.tasks import find_release

TC = Toolchain.discover()
needs_toolchain = pytest.mark.skipif(TC.qccdc is None or TC.qcheck is None,
                                     reason="the OCaml compiler and the Lean checker are not built")
HUMAN = {"kind": "human", "id": "cli"}
AGENT = {"kind": "agent", "id": "agent:t"}


def _wait(ws, jid, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = ws.job(jid)
        if j["status"] in ("succeeded", "failed", "cancelled", "timeout", "internal_error"):
            return j
        time.sleep(0.3)
    raise AssertionError(f"job {jid} did not finish")


@pytest.fixture(scope="module")
def graded(tmp_path_factory):
    if TC.qccdc is None or TC.qcheck is None:
        pytest.skip("toolchain not built")
    ws = Workspace.init(tmp_path_factory.mktemp("g") / "ws", "ghz4@1")
    j = _wait(ws, ws.start_job(AGENT, "compile", {})["job_id"])
    assert j["status"] == "succeeded", j
    ws.apply_change_set({"expected_revision": ws.head().revision, "request_id": "adopt", "mode": "apply",
                         "operations": [j["result"]["adopt_with"]]}, AGENT)
    sub = ws.submit_local(HUMAN, profile="reference", request_id="s1")
    _wait(ws, sub["job_id"])
    yield ws, sub
    ws.close()


@needs_toolchain
def test_the_starter_design_is_eligible_under_the_reference_profile(graded):
    ws, sub = graded
    s = ws.submission(sub["submission_id"])
    rep = s["report"]
    assert rep["eligibility"]["eligible"], rep["eligibility"]
    assert {st["id"]: st["status"] for st in rep["stages"]} == {k: "passed" for k in (
        "bundle", "device", "physics_lock", "program", "rules", "correspondence", "certificate_binding",
        "lean_certificate", "semantics", "metrics")}
    assert rep["metrics"]["T_jones"]["unit"] == "ms" and rep["metrics"]["T_jones"]["ranked"]
    assert rep["task"]["digest"] == ws.release.digest and rep["bundle"]["digest"] == sub["bundle_digest"]
    assert rep["evaluator"]["toolchain"]["qcheck"].startswith("sha256:")
    assert s["label"] == "Local result - not published"
    # a retried submit is the same submission
    assert ws.submit_local(HUMAN, profile="reference", request_id="s1")["duplicate"]


@needs_toolchain
def test_a_local_result_is_immutable_and_goes_stale(graded):
    ws, sub = graded
    s0 = ws.submission(sub["submission_id"])
    bdir = ws.root / s0["snapshot"]["dir"] / "bundle"
    before = {p.name: p.read_bytes() for p in bdir.iterdir()}
    assert not os.access(bdir / "device.arch.json", os.W_OK)          # frozen read-only
    ws.apply_change_set({"expected_revision": ws.head().revision, "request_id": "later", "mode": "apply",
                         "operations": [{"type": "move_site", "id": "C0", "pos": [0, 1]}]}, HUMAN)
    s1 = ws.submission(sub["submission_id"])
    assert s1["stale"] and s1["report"] == s0["report"]
    assert {p.name: p.read_bytes() for p in bdir.iterdir()} == before
    lb = ws.leaderboard()
    assert lb["label"] == "Local results - not published" and lb["rows"][0]["stale"]
    ws.undo_change_set(ws.history()[0]["change_set_id"], HUMAN)


def _reseal(src: Path, dest: Path, mutate) -> Path:
    b = read_bundle(src)
    docs = copy.deepcopy(b.docs)
    man = b.manifest
    mutate(docs, man)
    write_bundle(dest, task_id=man["task"]["id"], task_digest=man["task"]["digest"], device=docs["device"],
                 program=docs["program"], certified_program=docs.get("certified_program"),
                 certificate=docs.get("certificate"), presentation=docs.get("presentation"),
                 provenance=man.get("provenance"))
    return dest


def _stages(rep):
    return {st["id"]: st["status"] for st in rep["stages"]}


@pytest.fixture
def frozen(graded):
    ws, sub = graded
    return ws.root / ws.submission(sub["submission_id"])["snapshot"]["dir"] / "bundle"


@needs_toolchain
def test_changed_physics_is_exploratory_never_eligible(frozen, tmp_path):
    def faster(docs, man):
        for pt in docs["device"]["primitives"]["shuttle_segment"]["curve"]:
            pt["us"] = 0.001
    b = _reseal(frozen, tmp_path / "b", faster)
    rep = grade(find_release("ghz4@1"), b, "reference", workdir=tmp_path / "w")
    assert _stages(rep)["physics_lock"] == "failed" and not rep["eligibility"]["eligible"]
    assert any(d["id"] == "TASK.PHYSICS_MODIFIED" for d in rep["diagnostics"])


@needs_toolchain
def test_a_certificate_for_another_circuit_is_not_bound(frozen, tmp_path):
    def other(docs, man):
        docs["certificate"]["circuit_ops"][1]["qubits"] = [0, 2]
    rep = grade(find_release("ghz4@1"), _reseal(frozen, tmp_path / "b", other), "reference", workdir=tmp_path / "w")
    st = _stages(rep)
    assert st["certificate_binding"] == "failed" and st["lean_certificate"] == "skipped"
    assert not rep["eligibility"]["eligible"]


@needs_toolchain
def test_an_altered_final_program_breaks_correspondence(frozen, tmp_path):
    def swap(docs, man):
        gates = [i for i in docs["program"]["instructions"] if i.get("type") == "gate"]
        gates[0].setdefault("meta", {})["op"] = 99
    rep = grade(find_release("ghz4@1"), _reseal(frozen, tmp_path / "b", swap), "reference", workdir=tmp_path / "w")
    assert _stages(rep)["correspondence"] == "failed" and not rep["eligibility"]["eligible"]


@needs_toolchain
def test_a_foreign_instruction_type_is_not_an_allowed_insertion(frozen, tmp_path):
    def add(docs, man):
        ins = docs["program"]["instructions"]
        extra = copy.deepcopy(next(i for i in ins if i["type"] == "gate"))
        extra["id"] = 10 ** 6
        docs["program"]["id_seq"] = 10 ** 6 + 1        # a well-formed program, so the check under test runs
        ins.insert(3, extra)
    rep = grade(find_release("ghz4@1"), _reseal(frozen, tmp_path / "b", add), "reference", workdir=tmp_path / "w")
    assert _stages(rep)["correspondence"] == "failed"


@needs_toolchain
def test_digests_and_task_binding_are_checked(frozen, tmp_path):
    b = tmp_path / "b"
    shutil.copytree(frozen, b)
    for p in b.iterdir():
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
    doc = json.loads((b / "device.arch.json").read_text())
    doc["name"] = "tampered"
    (b / "device.arch.json").write_text(json.dumps(doc))
    rep = grade(find_release("ghz4@1"), b, "reference", workdir=tmp_path / "w")
    assert _stages(rep)["bundle"] == "failed" and any(d["id"] == "BUNDLE.DIGEST_MISMATCH" for d in rep["diagnostics"])

    def wrong_task(docs, man):
        man["task"] = {"id": "ghz4@1", "digest": "sha256:" + "0" * 64}
    rep = grade(find_release("ghz4@1"), _reseal(frozen, tmp_path / "c", wrong_task), "reference", workdir=tmp_path / "w2")
    assert _stages(rep)["bundle"] == "failed"


@needs_toolchain
def test_skipped_or_unsupported_required_checks_are_never_eligible(frozen, tmp_path):
    rep = grade(find_release("ghz4@1"), frozen, "draft", workdir=tmp_path / "d")
    assert not rep["eligibility"]["eligible"] and _stages(rep)["lean_certificate"] == "skipped"
    no_lean = Toolchain(qccdc=TC.qccdc, qcheck=None, python=TC.python, bridge=TC.bridge)
    rep = grade(find_release("ghz4@1"), frozen, "reference", workdir=tmp_path / "n", toolchain=no_lean)
    assert _stages(rep)["lean_certificate"] == "unsupported" and not rep["eligibility"]["eligible"]


@needs_toolchain
def test_publication_needs_a_person_and_binds_the_digest_and_parameters(graded, tmp_path):
    ws, sub = graded
    rv = ws.prepare_publish(AGENT, sub["submission_id"], visibility="public")
    assert rv["bundle_digest"] == sub["bundle_digest"] and "credentials" in rv["excluded"]
    with pytest.raises(WorkspaceError):
        ws.approve_publish(AGENT, sub["submission_id"], rv["bundle_digest"], {"visibility": "public"})
    with pytest.raises(WorkspaceError):
        ws.approve_publish(HUMAN, sub["submission_id"], "sha256:" + "1" * 64, {"visibility": "public"})
    ap = ws.approve_publish(HUMAN, sub["submission_id"], rv["bundle_digest"], {"visibility": "unlisted"})
    sent = []
    out = ws.publish(ap["approval_id"], lambda data, meta: sent.append((data, meta)) or {"submission_id": "os_x"})
    assert sent[0][1]["visibility"] == "unlisted" and sent[0][1]["bundle_digest"] == rv["bundle_digest"]
    b = extract_archive(sent[0][0], tmp_path / "x")
    assert b.digest == rv["bundle_digest"]                       # exactly the reviewed snapshot
    with pytest.raises(WorkspaceError) as e:                     # an approval is used once
        ws.publish(ap["approval_id"], lambda d, m: {})
    assert e.value.code == "approval_used"


def _zip(entries) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data, mode in entries:
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            if mode:
                info.external_attr = mode << 16
            z.writestr(info, data)
    return buf.getvalue()


@pytest.mark.parametrize("entries,code", [
    ([("../evil.json", b"{}", 0)], "unsafe_path"),
    ([("/abs.json", b"{}", 0)], "unsafe_path"),
    ([("sub/manifest.json", b"{}", 0)], "unsafe_path"),
    ([("manifest.json", b"{}", 0o120777)], "unsafe_path"),       # a symlink
    ([("notes.txt", b"hi", 0)], "unsafe_path"),
    ([("manifest.json", b"{}", 0), ("manifest.json", b"{}", 0)], "bad_archive"),
    ([("manifest.json", b"0" * 5_000_000, 0)], "bad_archive"),    # compression bomb ratio
])
def test_unsafe_archives_are_refused_before_extraction(tmp_path, entries, code):
    with pytest.raises(BundleError) as e:
        extract_archive(_zip(entries), tmp_path / "x")
    assert e.value.code == code
    assert not (tmp_path / "evil.json").exists()


@needs_toolchain
def test_archives_are_deterministic(frozen):
    b = read_bundle(frozen)
    assert archive_bundle(b) == archive_bundle(b)
