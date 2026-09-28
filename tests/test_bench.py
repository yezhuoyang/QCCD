"""The Compiler board's benchmark (qccd/bench): the contract, the archive, the harness's verdicts,
the hidden split, the sandbox, and a compiler graded end to end by the official service.

A suite of four small pairs is built in a temporary directory; the reference compiler (qccdc)
compiles them, and deliberately broken compilers must be caught for what they are: a flipped MS
angle is `wrong`, exit 4 is `refused`, a crash is `crash`, a sleeper is `timeout`, an abstract
CX is `wrong` (not native).  Needs the compiler (qccdc) and stim; skipped without them.
"""

from __future__ import annotations

import io
import json
import sys
import threading
import zipfile
from pathlib import Path

import pytest

from qccd.bench.contract import ContractError, load_compiler, pack, starter, unpack
from qccd.bench.suite import Suite, build_suite, hidden_pairs

REPO = Path(__file__).resolve().parents[1]


def _qccdc_available() -> bool:
    from qccd.workspace.evaluator import Toolchain
    return Toolchain.discover().qccdc is not None


needs_toolchain = pytest.mark.skipif(not _qccdc_available(), reason="no compiler: `qccd toolchain install`")


@pytest.fixture(scope="module")
def suite(tmp_path_factory) -> Suite:
    pytest.importorskip("stim")
    pytest.importorskip("qiskit")
    from qccd.api import Machine
    from qccd.workspace.tasks import find_release
    d = tmp_path_factory.mktemp("suites")
    circuits = [{"name": "ghz8", "qasm": (REPO / "Compiler/bench/ghz8.qasm").read_text()},
                {"name": "bv6", "qasm": (REPO / "Compiler/examples/bv6.qasm").read_text()}]
    devices = [{"name": "h2_racetrack", "doc": json.loads((REPO / "arch/h2_racetrack.arch.json").read_text())},
               {"name": "ring16", "doc": Machine.ring(8, 2, 8, name="ring16").arch.to_json()}]
    root = build_suite("t", "1", out_dir=d, circuits=circuits, devices=devices,
                       physics=find_release("ghz4@1").physics(), title="test suite", description="",
                       limits={"pair_timeout_s": 60, "pair_mem_mb": 4096, "build_timeout_s": 60},
                       ler={"noise": "qccd-noise@1", "max_shots": 5000, "max_errors": 20, "seed": 1}, hidden_count=2)
    return Suite.load(root)


def _compiler(d: Path, body: str, name: str = "test") -> Path:
    """A contract compiler whose compile.py runs the reference and then `body` on the result."""
    d.mkdir(parents=True, exist_ok=True)
    (d / "qccd-compiler.json").write_text(json.dumps({"kind": "qccd.compiler", "version": 1, "name": name,
                                                      "runtime": "python3", "entry": ["python3", "compile.py"],
                                                      "build": None}))
    (d / "compile.py").write_text(
        "import json, sys, time\nfrom pathlib import Path\nfrom qccd.bench.baseline import main as ref\n"
        "argv = sys.argv[1:]\nout = Path(argv[argv.index('--out') + 1])\n" + body)
    return d


# ---------------------------------------------------------------------- the contract and the archive

def test_the_contract_refuses_what_it_does_not_allow(tmp_path):
    with pytest.raises(ContractError, match="qccd-compiler.json"):
        load_compiler(tmp_path)
    (tmp_path / "qccd-compiler.json").write_text(json.dumps({"kind": "qccd.compiler", "version": 1, "name": "x",
                                                             "runtime": "docker", "entry": ["x"]}))
    with pytest.raises(ContractError, match="runtime"):
        load_compiler(tmp_path)
    (tmp_path / "qccd-compiler.json").write_text(json.dumps({"kind": "qccd.compiler", "version": 1, "name": "x",
                                                             "runtime": "python3", "entry": "python3 x.py"}))
    with pytest.raises(ContractError, match="argv"):
        load_compiler(tmp_path)
    s = load_compiler(starter(tmp_path / "start"))
    assert s.name == "my-compiler" and s.runtime == "python3" and s.argv(s.entry)[0] == sys.executable


def test_a_compiler_archive_round_trips_and_unsafe_ones_are_refused(tmp_path, suite):
    spec = load_compiler(starter(tmp_path / "c"))
    archive, binding = pack(spec, suite)
    got, b = unpack(archive, tmp_path / "out")
    assert b["suite"] == {"id": suite.id, "digest": suite.digest} and got.digest() == spec.digest()

    def z(entries: dict) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zz:
            for k, v in entries.items():
                zz.writestr(k, v)
        return buf.getvalue()
    ok = {"qccd-compiler.json": (spec.root / "qccd-compiler.json").read_text(), "qccd-submission.json": json.dumps(binding)}
    for bad, why in (({**ok, "../evil.py": "x"}, "unsafe"), ({**ok, "/abs.py": "x"}, "unsafe"),
                     ({"compile.py": "x"}, "must hold")):
        with pytest.raises(ContractError, match=why):
            unpack(z(bad), tmp_path / f"bad{len(bad)}")
    link = io.BytesIO()
    with zipfile.ZipFile(link, "w") as zz:
        for k, v in ok.items():
            zz.writestr(k, v)
        info = zipfile.ZipInfo("link")
        info.external_attr = (0o120777 << 16)
        zz.writestr(info, "/etc/passwd")
    with pytest.raises(ContractError, match="link"):
        unpack(link.getvalue(), tmp_path / "link")


def test_the_hidden_split_is_fixed_by_its_seed_and_unknown_without_it(suite):
    phys = suite.physics()
    a = [p.id for p in hidden_pairs("s1", phys, count=4)]
    assert a == [p.id for p in hidden_pairs("s1", phys, count=4)]
    assert a != [p.id for p in hidden_pairs("s2", phys, count=4)]
    assert suite.pairs("hidden") == [] and len(suite.pairs("all", hidden_seed="s1")) == 4 + 2


# ---------------------------------------------------------------------- the harness's verdicts

@needs_toolchain
def test_the_reference_compiler_against_itself(tmp_path, suite):
    from qccd.bench.harness import reference_compiler, run_suite
    base = run_suite(suite, reference_compiler(), workdir=tmp_path / "ref", reference={})
    by = {r["id"]: r for r in base["pairs"]}
    assert by["ghz8@h2_racetrack"]["status"] == "valid", by["ghz8@h2_racetrack"]
    assert by["ghz8@h2_racetrack"]["metrics"]["T_jones"] > 0
    ref = {r["id"]: r for r in base["pairs"]}
    again = run_suite(suite, reference_compiler(), workdir=tmp_path / "again", reference=ref)
    s = again["summary"]
    assert s["speedup"] == pytest.approx(1.0) and s["compared"] == s["counts"]["valid"] >= 1
    assert again["eligibility"]["eligible"] is False            # a local report is never rankable
    assert any("profile" in x for x in again["eligibility"]["reasons"])


@needs_toolchain
@pytest.mark.parametrize("body,status", [
    # a Pauli-frame slip: one MS angle negated.  The tableau differs; the rules do not notice.
    ("rc = ref(argv)\np = out / 'program.tsir.json'\nd = json.loads(p.read_text())\n"
     "g = next(i for i in d['instructions'] if i.get('gate') == 'MS')\ng['params'][0][0] *= -1\n"
     "p.write_text(json.dumps(d))\nsys.exit(rc)\n", "wrong"),
    # an abstract gate in place of native pulses
    ("rc = ref(argv)\np = out / 'program.tsir.json'\nd = json.loads(p.read_text())\n"
     "g = next(i for i in d['instructions'] if i.get('gate') == 'MS')\ng['gate'] = 'CX'\n"
     "p.write_text(json.dumps(d))\nsys.exit(rc)\n", "wrong"),
    ("sys.exit(4)\n", "refused"),
    ("raise RuntimeError('boom')\n", "crash"),
    ("sys.exit(0)\n", "no_output"),
    ("time.sleep(30)\n", "timeout"),
])
def test_a_broken_compiler_is_caught_for_what_it_is(tmp_path, suite, body, status):
    from qccd.bench.harness import run_suite
    spec = load_compiler(_compiler(tmp_path / "c", body))
    if status == "timeout":
        suite = Suite(suite.root, {**suite.manifest, "limits": {**suite.manifest["limits"], "pair_timeout_s": 2}})
    rep = run_suite(suite, spec, workdir=tmp_path / "w", only=["ghz8@h2_racetrack"], reference={})
    (r,) = rep["pairs"]
    assert r["status"] == status, r
    if status == "wrong":
        assert r["reason"].startswith(("semantics", "native")), r["reason"]
        assert any("wrong" in x for x in rep["eligibility"]["reasons"])


@needs_toolchain
def test_the_sandbox_spool_gives_the_same_verdicts_as_a_local_run(tmp_path, suite):
    from qccd.bench import sandbox
    from qccd.bench.harness import reference_compiler, run_suite
    spool = tmp_path / "spool"
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            sandbox.serve(spool, once=True)
            stop.wait(0.1)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    try:
        spec = load_compiler(starter(tmp_path / "c"))
        runner = sandbox.SpoolRunner(spool, "job1")
        via = run_suite(suite, spec, workdir=tmp_path / "a", only=["h2_racetrack"], runner=runner, reference={})
        runner.close()
        local = run_suite(suite, reference_compiler(), workdir=tmp_path / "b", only=["h2_racetrack"], reference={})
    finally:
        stop.set()
        t.join(5)
    assert [(r["id"], r["status"]) for r in via["pairs"]] == [(r["id"], r["status"]) for r in local["pairs"]]
    assert not (spool / "jobs" / "job1").exists()


# ---------------------------------------------------------------------- the official service

@needs_toolchain
def test_a_compiler_is_graded_by_the_official_service_and_ranked(tmp_path, suite, monkeypatch):
    from qccd.bench.harness import reference_compiler, run_suite
    from qccd.official.service import OfficialService
    from qccd.official.worker import process_one
    # pin the suite's baseline (a published suite carries it): the reference on the public pairs
    base = run_suite(suite, reference_compiler(), workdir=tmp_path / "ref", reference={})
    sdir = tmp_path / "suites"
    phys = suite.physics()
    circuits = [{"name": c["name"], "qasm": (suite.root / c["file"]).read_text()} for c in suite.manifest["circuits"]]
    devices = [{"name": d["name"], "doc": json.loads((suite.root / d["file"]).read_text())}
               for d in suite.manifest["devices"]]
    root = build_suite("t", "1", out_dir=sdir, circuits=circuits, devices=devices, physics=phys, title="test suite",
                       description="", limits=suite.manifest["limits"], ler=suite.manifest["ler"], hidden_count=1,
                       baseline={"pairs": {r["id"]: {k: r.get(k) for k in ("status", "metrics", "ler")}
                                           for r in base["pairs"]}})
    s2 = Suite.load(root)
    monkeypatch.setenv("QCCD_HIDDEN_SEED", "test-seed")
    monkeypatch.setenv("QCCD_OFFICIAL_SUITES", str(sdir))     # the grader resolves suites in ITS directory
    monkeypatch.delenv("QCCD_SANDBOX_SPOOL", raising=False)
    (tmp_path / "no-releases").mkdir()
    svc = OfficialService(f"sqlite:///{tmp_path}/o.db", tmp_path / "no-releases", tmp_path / "artifacts",
                          suites_dir=sdir)
    assert any(t["id"] == s2.id and t["track"] == "compiler" for t in svc.tasks())
    tok = svc.create_uploader("tester")
    up = svc.authenticate(tok, "127.0.0.1")
    archive, _ = pack(load_compiler(starter(tmp_path / "c")), s2)
    sub = svc.submit(up, archive, task=s2.id, display_name="starter")
    assert sub["status"] == "queued"
    assert svc.submit(up, archive, task=s2.id)["duplicate"] is True
    wrong_suite = {**s2.manifest, "release": "9"}
    bad, _ = pack(load_compiler(tmp_path / "c"), Suite(s2.root, wrong_suite))
    with pytest.raises(Exception, match="bound to"):
        svc.submit(up, bad, task=s2.id)
    assert process_one(svc, tmp_path / "spool", inline=True, releases=tmp_path / "no-releases")
    job = svc.db.one("SELECT status, error FROM jobs WHERE submission=?", (sub["submission_id"],))
    assert job["status"] == "succeeded", job
    rep = svc.report(sub["submission_id"], up)
    assert rep["kind"] == "qccd.compiler_report" and rep["profile"] == "official"
    assert rep["summary"]["hidden_pairs"] == 1
    assert rep["run"]["isolation"].startswith("child process")
    counts = rep["summary"]["counts"]
    assert rep["eligibility"]["eligible"] is (counts["wrong"] == 0), rep["eligibility"]
    lb = svc.leaderboard(s2.id)
    assert lb["track"] == "compiler" and lb["better"] == "high"
    if rep["eligibility"]["eligible"]:
        assert lb["rows"][0]["rank_value"] == pytest.approx(1.0)      # the reference is 1x itself
    else:
        assert lb["rows"] == []


@needs_toolchain
def test_the_documents_match_their_published_schemas(tmp_path, suite):
    jsonschema = pytest.importorskip("jsonschema")
    from qccd.bench.harness import reference_compiler, run_suite
    sch = lambda n: json.loads((REPO / "qccd/workspace/schemas" / f"{n}.schema.json").read_text())
    rep = run_suite(suite, reference_compiler(), workdir=tmp_path / "w", only=["ghz8"], reference={})
    jsonschema.validate(json.loads(json.dumps(rep)), sch("compiler_report"))
    jsonschema.validate(json.loads((starter(tmp_path / "c") / "qccd-compiler.json").read_text()), sch("compiler_manifest"))
    jsonschema.validate(suite.manifest, sch("compiler_suite"))
