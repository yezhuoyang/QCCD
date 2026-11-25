"""The native gate set as a declared property of a leaderboard entry (`qccd.ir.gate_sets`).

A submission bundle declares the gate set its programs are in (`manifest.gate_set`; the default,
ms, by naming none, so an ms bundle is the bundle it always was).  The grader grades an rzz
program end to end -- the replay, the rules, R10 against the source QASM, the stim circuit and its
logical error rate -- names the set in the report when it is not the default, and refuses a
program whose pulses, meta or certificate say another set than the bundle, or a set the board
does not take.  The official API's entry rows, the website's leaderboards and the "Download and
reproduce" zip say which set an entry is in.

The entry is the C2LR repetition-code memory board's experiment, compiled by the baseline C2LR
router (which lowers to either set with no binary) on the board's starter lattice.
"""

from __future__ import annotations

import dataclasses
import io
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.ir import gate_sets  # noqa: E402
from qccd.workspace.bundle import BundleError, read_bundle, write_bundle  # noqa: E402
from qccd.workspace.evaluator import Toolchain, grade  # noqa: E402
from qccd.workspace.tasks import RELEASES_DIR, TaskRelease, board_device  # noqa: E402

BOARD = "c2lr_rep5_mem_basr@1"
TC = Toolchain.discover()


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """The board's experiment compiled in each set: {set: (device doc, program, certified, cert, work)}."""
    pytest.importorskip("stim")
    pytest.importorskip("pymatching")
    from qccd.api import Machine
    from qccd.compile.c2lr_router import compile_with_router
    rel = TaskRelease.load(RELEASES_DIR / BOARD)
    doc = board_device(Machine.c2lr_grid(5, 2, species="Ba-Sr").arch.to_json(), rel)
    out = {}
    base = tmp_path_factory.mktemp("gs")
    for gs in gate_sets.GATE_SETS:
        w = base / gs
        w.mkdir()
        (w / "device.arch.json").write_text(json.dumps(doc), encoding="utf-8")
        final, cert = compile_with_router(rel.circuit_text(), w / "device.arch.json", w / "work", gate_set=gs)
        rd = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))  # noqa: E731
        out[gs] = (doc, rd(final), rd(w / "work" / "prog.tsir.json"), rd(cert), w)
    return rel, out


def _bundle(rel, parts, where: Path, **kw):
    doc, prog, certified, cert, _ = parts
    return write_bundle(where, task_id=rel.id, task_digest=rel.digest, device=doc, program=prog,
                        certified_program=certified, certificate=cert, **kw)


@pytest.fixture(scope="module")
def graded(built, tmp_path_factory):
    """Each set's bundle graded under the reference profile: {set: (bundle, report)}."""
    if TC.qcheck is None:
        pytest.skip("the Lean checker (qcheck) is not installed")
    rel, out = built
    base = tmp_path_factory.mktemp("graded")
    got = {}
    for gs, parts in out.items():
        b = _bundle(rel, parts, base / f"b_{gs}")
        got[gs] = (b, grade(rel, b.root, "reference", workdir=base / f"w_{gs}"))
    return got


def _stage(rep, sid):
    return next(s for s in rep["stages"] if s["id"] == sid)


# ------------------------------------------------------------------ the declaration


def test_a_bundle_declares_the_set_its_program_is_in_and_the_default_by_naming_none(built, tmp_path):
    rel, out = built
    ms = _bundle(rel, out["ms"], tmp_path / "ms")
    rzz = _bundle(rel, out["rzz"], tmp_path / "rzz")
    assert "gate_set" not in ms.manifest                    # an ms bundle is the bundle it always was
    assert rzz.manifest["gate_set"] == "rzz"                 # derived from the program and certificate
    # the same ms bundle with the default named or left out: one digest
    assert _bundle(rel, out["ms"], tmp_path / "ms2", gate_set="ms").digest == ms.digest
    # a stored bundle written again as it was keeps its (missing) declaration
    assert "gate_set" not in _bundle(rel, out["rzz"], tmp_path / "old", derive_gate_set=False).manifest


def test_the_manifest_takes_the_sets_there_are_only(built, tmp_path):
    rel, out = built
    b = _bundle(rel, out["rzz"], tmp_path / "b")
    man = dict(b.manifest, gate_set="xx")
    (b.root / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    with pytest.raises(BundleError, match="gate_set"):
        read_bundle(b.root)


def test_derive_reads_the_pulses_the_meta_and_the_claims():
    def prog(*gates, meta=None):
        return {"meta": meta or {}, "instructions": [{"type": "gate", "gate": g} for g in gates]}
    assert gate_sets.derive(prog("R", "MS")) == "ms"
    assert gate_sets.derive(prog("R", "RZZ")) == "rzz"
    assert gate_sets.derive(prog("R")) == "ms"                                   # no entangling pulse
    assert gate_sets.derive(prog("R"), {"claims": {"gate_set": "rzz"}}) == "rzz"
    assert gate_sets.derive(prog("MS", "RZZ")) is None                           # mixed
    assert gate_sets.derive(prog("MS", meta={"gate_set": "rzz"})) is None        # says one, is the other


# ------------------------------------------------------------------ the grade


def test_an_rzz_bundle_grades_eligible_with_its_gate_set_recorded(graded):
    b, rep = graded["rzz"]
    assert rep["eligibility"]["eligible"], rep["eligibility"]
    assert rep["gate_set"] == "rzz"
    prog = _stage(rep, "program")
    assert prog["detail"]["gate_set"] == "rzz" and "rzz (R, VZ, RZZ)" in prog["coverage"]
    # R10's two halves and the noisy circuit read the RZZ pulses
    assert _stage(rep, "semantics")["detail"]["o2_semantics"] == "ok"
    assert _stage(rep, "lean_certificate")["status"] == "passed"
    stats = _stage(rep, "ler")["detail"]["stats"]
    assert stats["n_ms"] == 40 and stats["n_vz"] > 0          # every entangling pulse noised as the 2-qubit gate
    assert not [d for d in rep["diagnostics"] if d["id"].startswith(("PROGRAM.", "BUNDLE."))]


def test_an_ms_report_names_no_gate_set(graded):
    """The default set's report is the one it always was: no key, no word, no detail added."""
    _, rep = graded["ms"]
    assert rep["eligibility"]["eligible"]
    assert "gate_set" not in json.dumps(rep)
    prog = _stage(rep, "program")
    assert prog["coverage"] == "TSIR shape and instruction types" and set(prog["detail"]) == {"instructions"}
    # the two sets' entries differ in their pulses only: the same stages pass, the same experiment runs
    _, rzz = graded["rzz"]
    assert {s["id"]: s["status"] for s in rep["stages"]} == {s["id"]: s["status"] for s in rzz["stages"]}
    assert _stage(rep, "ler")["detail"]["stats"]["detectors"] == _stage(rzz, "ler")["detail"]["stats"]["detectors"]


def test_a_bundle_from_before_the_declaration_is_graded_in_its_program_s_set(built, tmp_path):
    if TC.qcheck is None:
        pytest.skip("the Lean checker (qcheck) is not installed")
    rel, out = built
    b = _bundle(rel, out["rzz"], tmp_path / "b", derive_gate_set=False)
    rep = grade(rel, b.root, "reference", workdir=tmp_path / "w")
    assert rep["eligibility"]["eligible"] and rep["gate_set"] == "rzz"
    assert [d["severity"] for d in rep["diagnostics"] if d["id"] == "BUNDLE.GATE_SET_UNDECLARED"] == ["warning"]


def test_a_program_in_another_set_than_its_bundle_declares_is_refused(built, tmp_path):
    rel, out = built
    # write_bundle writes the default as no gate_set at all (and a bundle that names none is read by
    # its program); a bundle may still name ms outright, and then its pulses must be MS
    b = _bundle(rel, out["rzz"], tmp_path / "b")
    (b.root / "manifest.json").write_text(json.dumps(dict(b.manifest, gate_set="ms")), encoding="utf-8")
    rep = grade(rel, b.root, "draft", workdir=tmp_path / "w")
    assert _stage(rep, "program")["status"] == "failed" and not rep["eligibility"]["eligible"]
    assert {d["id"] for d in rep["diagnostics"]} >= {"PROGRAM.GATE_SET"}
    # and one that mixes the two entangling pulses
    doc, prog, certified, cert, w = out["rzz"]
    mixed = json.loads(json.dumps(prog))
    next(i for i in mixed["instructions"] if i.get("gate") == "RZZ")["gate"] = "MS"
    rep = grade(rel, _bundle(rel, (doc, mixed, certified, cert, w), tmp_path / "m").root, "draft",
                workdir=tmp_path / "w2")
    assert [d["id"] for d in rep["diagnostics"] if d["id"].startswith("PROGRAM.")] == ["PROGRAM.GATE_SET_MIXED"]


def test_a_board_that_pins_a_set_refuses_a_program_in_another(built, tmp_path):
    rel, out = built
    d = tmp_path / "pinned"
    shutil.copytree(rel.root, d)
    man = json.loads((d / "release.json").read_text(encoding="utf-8"))
    man["gate_sets"] = ["ms"]
    (d / "release.json").write_text(json.dumps(man, indent=1, sort_keys=True), encoding="utf-8")
    pinned = TaskRelease.load(d)
    assert pinned.gate_sets == ("ms",) and rel.gate_sets == ("ms", "rzz")
    assert pinned.summary()["gate_sets"] == ["ms"] and "gate_sets" not in rel.summary()
    rep = grade(pinned, _bundle(pinned, out["rzz"], tmp_path / "b").root, "draft", workdir=tmp_path / "w")
    assert [d["id"] for d in rep["diagnostics"] if d["id"].startswith("PROGRAM.")] == ["PROGRAM.GATE_SET_NOT_ALLOWED"]
    rep = grade(pinned, _bundle(pinned, out["ms"], tmp_path / "b2").root, "draft", workdir=tmp_path / "w2")
    assert _stage(rep, "program")["status"] == "passed"


# ------------------------------------------------------------------ the official API


def test_the_official_entry_carries_its_gate_set(built, graded, tmp_path, monkeypatch):
    from qccd.official.service import OfficialError, OfficialService
    from qccd.official.worker import process_one
    from qccd.workspace.bundle import archive_bundle
    svc = OfficialService(f"sqlite:///{tmp_path / 'o.db'}", RELEASES_DIR, tmp_path / "artifacts")
    up = svc.authenticate(svc.create_uploader("ada"), "10.0.0.1")
    ids = {}
    for gs in gate_sets.GATE_SETS:
        b, _ = graded[gs]
        ids[gs] = svc.submit(up, archive_bundle(b), task=BOARD, display_name=f"c2lr {gs}")["submission_id"]
        assert process_one(svc, tmp_path / "spool", inline=True, releases=RELEASES_DIR)
    rows = {r["id"]: r for r in svc.leaderboard(BOARD)["rows"]}
    assert rows[ids["rzz"]]["gate_set"] == "rzz" and rows[ids["ms"]]["gate_set"] == "ms"
    assert svc.leaderboard(BOARD)["gate_sets"] == ["ms", "rzz"]
    assert svc.submission(ids["rzz"], None)["report"]["gate_set"] == "rzz"
    assert svc.report(ids["rzz"], None)["gate_set"] == "rzz" and "gate_set" not in svc.report(ids["ms"], None)
    track = svc.track_board("c2lr")
    got = {r["id"]: r["gate_set"] for bd in track["boards"] for r in bd["rows"]}
    assert got == {ids["ms"]: "ms", ids["rzz"]: "rzz"}
    # a board that pins a set refuses a bundle declaring another at upload (as if its release named
    # only ms: a release's manifest is pinned by its digest, which the bundle names)
    monkeypatch.setattr(TaskRelease, "gate_sets", property(lambda self: ("ms",)))
    b, _ = graded["rzz"]
    with pytest.raises(OfficialError) as e:
        svc.submit(up, archive_bundle(b), task=BOARD, display_name="again")
    assert e.value.code == "gate_set_not_allowed" and e.value.status == 422


# ------------------------------------------------------------------ the website


_DOM = r"""
class N { constructor(t){ this.tag = t; this.children = []; this.attrs = {}; this.className = ''; this.title = '';
            this._t = ''; this.style = {}; this.options = []; this.value = ''; }
  get textContent(){ return this._t + this.children.map(function(c){ return c.textContent; }).join(''); }
  set textContent(v){ this._t = String(v); this.children = []; }
  appendChild(c){ this.children.push(c); if (this.tag === 'select') this.options.push(c); return c; }
  setAttribute(k, v){ this.attrs[k] = String(v); } getAttribute(k){ return this.attrs[k] == null ? null : this.attrs[k]; }
  remove(){} getBoundingClientRect(){ return {left: 0, bottom: 0}; } }
var box = new N('div'); box.setAttribute('data-board', 'b');
global.window = {}; global.location = {search: '', pathname: '/'};
global.document = {createElement: function(t){ return new N(t); }, body: new N('body'), addEventListener: function(){},
  querySelectorAll: function(s){ return s.indexOf('lbx-live') >= 0 ? [box] : []; }};
var LB = __LB__;
global.fetch = function(url){ return Promise.resolve({ok: true, json: function(){
  return Promise.resolve(/\/tasks$/.test(url) ? {tasks: [{id: 'b@1'}]} : LB); }}); };
function walk(n, out){ if (n.className === 'lbx-gs') out.push([n.textContent, n.title]); n.children.forEach(function(c){ walk(c, out); }); return out; }
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_board_s_list_tags_an_rzz_entry_and_not_an_ms_one(tmp_path):
    from qccd.site import leaderboard
    row = lambda i, gs: {"id": i, "display_name": i, "by": "ada", "created_at": 0, "rank_value": 1.0,  # noqa: E731
                         "metrics": {"ler_per_round": 1e-3}, "conformance": {"board": True}, "gate_set": gs}
    lb = {"rows": [row("x_rzz", "rzz"), row("x_ms", "ms"), {**row("x_old", None)}], "metrics": [
        {"name": "ler_per_round", "better": "low", "unit": "per round"}], "rank_by": "ler_per_round",
        "better": "low", "counts": {"board": 3, "all": 3}, "rules": "board"}
    js = (_DOM.replace("__LB__", json.dumps(lb))
          + leaderboard.SCRIPT.replace("__BOARDS__", json.dumps({"b": {"id": "b@1", "title": "B"}}))
          .replace("__LOCAL__", '""').replace("__CHARTS__", "[]")
          + "\nsetTimeout(function(){ console.log(JSON.stringify(walk(box, []))); }, 50);\n")
    f = tmp_path / "t.js"
    f.write_text(js, encoding="utf-8")
    r = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    tags = json.loads(r.stdout.strip().splitlines()[-1])
    assert len(tags) == 1 and tags[0][0] == "gate set RZZ" and "R, VZ, RZZ" in tags[0][1], tags


def test_the_ranking_charts_and_goal_3_show_an_entry_s_gate_set(monkeypatch, tmp_path):
    from qccd.site import goal3, leaderboard
    # a ranking chart's entry: its detail and its table name the set
    assert "gs: r.gate_set || 'ms'" in leaderboard.CHART_SCRIPT
    assert "row('gate set'" in leaderboard.CHART_SCRIPT and "gsNote(d.gs)" in leaderboard.CHART_SCRIPT
    # Goal 1's table, an official entry on Goal 3, and a reference row compiled to rzz
    assert "b.gate_set" in leaderboard.GOAL_TABLE_SCRIPT
    assert "gs: e.gate_set" in goal3.SCRIPT and "'Gate set ' + p.gs.toUpperCase()" in goal3.SCRIPT
    g = tmp_path / "goal3"
    (g / "results").mkdir(parents=True)
    shutil.copy(goal3.GOAL / "families.py", g / "families.py")
    real = (goal3.GOAL / "results" / "surface3.jsonl").read_text(encoding="utf-8").splitlines()[0]
    row = json.loads(real)
    (g / "results" / "surface3.jsonl").write_text(
        json.dumps(row) + "\n" + json.dumps({**row, "point": row["point"] + "_rzz", "gate_set": "rzz"}) + "\n",
        encoding="utf-8")
    monkeypatch.setattr(goal3, "GOAL", g)
    d = goal3.data()
    pts = {p["n"]: p for c in d["codes"] for p in c["points"]}
    assert pts[row["point"] + "_rzz"]["gs"] == "rzz" and "gs" not in pts[row["point"]]


# ------------------------------------------------------------------ the zip


def test_the_zip_records_the_gate_set_and_regenerate_rebuilds_the_rzz_point(built, graded, tmp_path):
    from qccd.qec import reproduce as R
    rel, out = built
    _, rep = graded["rzz"]
    doc, prog, certified, cert, w = out["rzz"]
    (w / "final.tsir.json").write_text(json.dumps(prog), encoding="utf-8")
    (w / "cert.json").write_text(json.dumps(cert), encoding="utf-8")
    point = R.Point(board="c2lr_rep5_mem_basr", board_title=rel.title, entry="c2lr5x2_rzz",
                    title="C2LR 5x2, rzz", qasm=rel.circuit_path, machine=w / "device.arch.json",
                    program=w / "final.tsir.json", certificate=w / "cert.json", release=rel.id,
                    ler=_stage(rep, "ler")["detail"]["ler"], rule_set="c2lr", replay_rules=False,
                    family="C2LR", compiler="the baseline C2LR router", gate_set="rzz")
    data, info = R.build(point)
    assert info["gate_set"] == {"name": "rzz", "pulses": ["R", "VZ", "RZZ"], "note": info["gate_set"]["note"]}
    folder = tmp_path / "z"
    zipfile.ZipFile(io.BytesIO(data)).extractall(folder)
    assert json.loads((folder / "experiment.json").read_text(encoding="utf-8"))["gate_set"]["name"] == "rzz"
    assert "| native gate set | `rzz`: R, VZ, RZZ" in (folder / "README.md").read_text(encoding="utf-8")
    import os
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    r = subprocess.run([sys.executable, str(folder / "regenerate.py")], cwd=str(folder), env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "gate set  rzz (R, VZ, RZZ)   identical" in r.stdout and "identical to circuit.stim" in r.stdout
    assert "REPRODUCED: everything computed is identical." in r.stdout
    # a row that says ms, with an rzz program in its folder, is refused
    with pytest.raises(R.ReproduceError, match="gate set"):
        R.build(dataclasses.replace(point, gate_set="ms"))
