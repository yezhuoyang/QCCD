"""DOWNLOAD AND REPRODUCE (the user, 2026-11-23): every data point of every leaderboard is one zip.

"For all leaderboard, a user should be able to download the qasm (input QEC program), hardware
program, Stim code with noise injected, and finally, the python script that run the experiment.
Everyone should be able to reproduce every single data point on all leaderboards."

`qccd/qec/reproduce.py` builds the zip for every board.  These tests hold what a reader relies on:

* the zip's layout, and that a rebuild gives the same bytes;
* `run.py`, which uses no QCCD code, counts exactly what `qccd.qec.decode.estimate_ler` counts
  for the same seed (PyMatching and BP-OSD);
* `regenerate.py` replays the program to the published time and rebuilds `circuit.stim`
  character for character;
* a board ranked by time has no stim circuit and says why;
* a folder that holds another compile of the point is refused;
* nothing in a zip says whose disk it was built on;
* the decoder of a logical error rate is named with its parameters;
* the button is the template's own (`render_html(reproduce=)`), and a board's index offers it
  on every compiled row.
"""

from __future__ import annotations

import ast
import io
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.qec import reproduce as R  # noqa: E402

LER_FILES = ["README.md", "circuit.qasm", "machine.arch.json", "hardware_program.tsir.json",
             "hardware_program.txt", "certificate.qcert.json", "detectors.json", "circuit.stim",
             "experiment.json", "run.py", "regenerate.py", "requirements.txt"]
TIME_FILES = [f for f in LER_FILES if f not in ("detectors.json", "circuit.stim", "run.py")]


def _task(board: str) -> dict:
    return json.loads((ROOT / "tasks" / board / "task.json").read_text(encoding="utf-8"))


def _row(board: str, machine: str) -> dict:
    t = _task(board)
    rows = json.loads((ROOT / t["seed"] / "manifest.json").read_text(encoding="utf-8"))
    return next(r for r in rows if r.get("short") == machine and r.get("status") == "ok")


def _point(board: str, machine: str) -> R.Point:
    p = R.board_point(_task(board), _row(board, machine))
    assert p is not None
    return p


def _unzip(data: bytes, where: Path) -> Path:
    where.mkdir(parents=True, exist_ok=True)
    zipfile.ZipFile(io.BytesIO(data)).extractall(where)
    return where


def _run(script: Path, *args: str, qccd: bool = False) -> subprocess.CompletedProcess:
    """A zip's script, run as a reader runs it: in its own folder, in isolated mode, and with
    the QCCD package on the path only for the script that says it needs it."""
    env = {k: v for k, v in __import__("os").environ.items() if k != "PYTHONPATH"}
    if qccd:
        env["PYTHONPATH"] = str(ROOT)
    return subprocess.run([sys.executable, str(script), *args], cwd=str(script.parent), env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)


@pytest.fixture(scope="module")
def memory_zip(tmp_path_factory):
    pytest.importorskip("stim")
    pytest.importorskip("pymatching")
    point = _point("surface3_mem", "ring24_8_j")
    data, info = R.build(point)
    return point, data, info, _unzip(data, tmp_path_factory.mktemp("memory_zip"))


@pytest.fixture(scope="module")
def time_zip(tmp_path_factory):
    point = _point("steane", "grid5x5")
    data, info = R.build(point)
    return point, data, info, _unzip(data, tmp_path_factory.mktemp("time_zip"))


# ------------------------------------------------------------------ the zip itself

def test_a_memory_point_s_zip_has_the_whole_experiment(memory_zip):
    point, data, info, folder = memory_zip
    z = zipfile.ZipFile(io.BytesIO(data))
    assert z.namelist() == LER_FILES
    assert info["name"] == "surface3_mem-01_ring24_8_j-reproduce.zip" == point.name
    spec = json.loads((folder / "experiment.json").read_text(encoding="utf-8"))
    assert spec["reproduces"] == "ler"
    assert spec["board"]["release"] == "surface3_mem@1" and spec["circuit"]["matches_release"] is True
    assert spec["noise"]["id"] == "qccd-noise@1" and spec["noise"]["digest"].startswith("sha256:")
    assert spec["decoder"]["name"] == "pymatching"
    assert spec["decoder"]["label"] == "PyMatching 2 (minimum-weight perfect matching)"
    # the budget with every default written out: a script must not need one
    assert spec["sampling"] == {**spec["sampling"], "seed": 20260928, "max_shots": 200000, "max_errors": 200,
                                "batch": 10000, "time_limit_s": None}
    pub = spec["published"]
    assert pub["ler"] == point.ler and pub["time"]["total_ms"] == point.time_ms
    assert pub["time"]["per_round_ms"] == pytest.approx(point.time_ms / 3)
    assert pub["tools"]["stim"] and spec["built"]["repository"] == "https://github.com/yezhuoyang/QCCD"
    assert re.fullmatch(r"[0-9a-f]{40}", spec["built"]["commit"] or ""), "the commit the zip was built from"
    # every file's digest is the file's
    import hashlib
    for name, rec in spec["files"].items():
        assert rec["sha256"] == "sha256:" + hashlib.sha256(z.read(name)).hexdigest(), name
    # the stim circuit is a circuit, with the experiment's detectors and observable
    import stim
    c = stim.Circuit((folder / "circuit.stim").read_text(encoding="utf-8"))
    assert (c.num_detectors, c.num_observables) == (24, 1)
    assert "DEPOLARIZE2" in str(c) and "X_ERROR" in str(c), "noise is injected"
    # the program as text: one line per instruction
    assert len((folder / "hardware_program.txt").read_text(encoding="utf-8").splitlines()) == spec["program"]["instructions"]
    readme = (folder / "README.md").read_text(encoding="utf-8")
    for needle in ("python run.py", "python regenerate.py", "pip install -r requirements.txt",
                   "PyMatching 2 (minimum-weight perfect matching)", "20260928", "204 logical errors in 170,000 shots",
                   "lies inside the 95% interval of your run, or the two intervals overlap", "**Exactly:**"):
        assert needle in readme, needle
    assert "stim==" in (folder / "requirements.txt").read_text(encoding="utf-8")


def test_a_rebuild_gives_the_same_bytes(memory_zip):
    point, data, _, _ = memory_zip
    again, _ = R.build(point)
    assert again == data
    z = zipfile.ZipFile(io.BytesIO(data))
    assert {i.date_time for i in z.infolist()} == {(2026, 1, 1, 0, 0, 0)}
    assert {i.create_system for i in z.infolist()} == {3}


def test_nothing_in_a_zip_says_whose_disk_it_was_built_on(memory_zip, time_zip):
    """The committed programs carry the folder of the machine that compiled them in their
    provenance (`meta.prov.root`); the zip's copy does not, and nothing else is changed."""
    for point, data, _, folder in (memory_zip, time_zip):
        z = zipfile.ZipFile(io.BytesIO(data))
        for name in z.namelist():
            text = z.read(name).decode("utf-8")
            assert not re.search(r"[A-Za-z]:\\\\?Users|/home/\w|/Users/\w", text), name
            assert not re.search(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", text), name
            assert "\r\n" not in text, name
        had = json.loads(Path(point.program).read_text(encoding="utf-8"))
        got = json.loads((folder / "hardware_program.tsir.json").read_text(encoding="utf-8"))
        assert got["instructions"] == had["instructions"]
        if ((had.get("meta") or {}).get("prov") or {}).get("root"):
            assert got["meta"]["prov"]["root"] == ""
            assert json.loads((folder / "experiment.json").read_text(encoding="utf-8"))["program"]["provenance_paths_removed"]


def test_the_sampling_script_uses_no_qccd_code():
    """`run.py` is self-contained: the standard library, stim, numpy and the decoder's package."""
    tree = ast.parse((ROOT / "qccd" / "qec" / "reproduce_run.py").read_text(encoding="utf-8"))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative import"
            mods.add((node.module or "").split(".")[0])
    assert mods <= {"__future__", "argparse", "json", "math", "sys", "time", "pathlib", "importlib",
                    "numpy", "stim", "pymatching", "ldpc", "scipy"}, mods


# ------------------------------------------------------------------ run.py against estimate_ler

def test_run_py_counts_what_estimate_ler_counts(memory_zip, tmp_path):
    """The same circuit, seed and budget on the same machine: identical errors and shots."""
    import stim
    from qccd.qec.decode import estimate_ler
    point, _, _, folder = memory_zip
    spec = json.loads((folder / "experiment.json").read_text(encoding="utf-8"))
    s = spec["sampling"]
    est = estimate_ler(stim.Circuit((folder / "circuit.stim").read_text(encoding="utf-8")), decoder="pymatching",
                       max_shots=s["max_shots"], max_errors=s["max_errors"], batch=s["batch"], seed=s["seed"],
                       rounds=spec["experiment"]["rounds"])
    out = tmp_path / "got.json"
    r = _run(folder / "run.py", "--quiet", "--json", str(out))
    assert r.returncode == 0, r.stdout + r.stderr
    got = json.loads(out.read_text(encoding="utf-8"))
    assert (got["errors"], got["shots"], got["stopped"]) == (est.errors, est.shots, est.stopped)
    for k in ("ler", "lo", "hi", "per_round", "per_round_lo", "per_round_hi"):
        assert got[k] == getattr(est, k), k
    assert "PyMatching 2 (minimum-weight perfect matching)" in r.stdout
    assert "REPRODUCED" in r.stdout and "NOT REPRODUCED" not in r.stdout
    # this point's published counts come out exactly on the machine class they were made on;
    # anywhere, they are inside the interval
    assert got["verdict"] in ("identical", "consistent")
    # a quick run is compared by its interval, never called identical
    quick = _run(folder / "run.py", "--quiet", "--shots", "20000", "--json", str(out))
    assert quick.returncode == 0, quick.stdout + quick.stderr
    q = json.loads(out.read_text(encoding="utf-8"))
    assert q["shots"] == 20000 and q["verdict"] == "consistent"


def test_run_py_decodes_with_bp_osd_as_estimate_ler_does(tmp_path):
    """BP-OSD with the release's settings (product-sum BP capped at `bp_max_iter`, OSD-CS of
    order 7, the syndrome cache): the script's count is `estimate_ler`'s, shot for shot."""
    pytest.importorskip("ldpc")
    import stim
    from dataclasses import replace
    from qccd.qec.decode import estimate_ler
    base = _point("surface3_mem", "ring24_8_j")
    budget = {"decoder": "bposd", "bp_max_iter": 30, "max_shots": 6000, "max_errors": 8, "batch": 1000,
              "seed": 20261029}
    # the circuit first, to publish what estimate_ler measures on it
    data, _ = R.build(replace(base, budget=budget, ler={**base.ler, "decoder": "bposd"}))
    circuit = stim.Circuit(zipfile.ZipFile(io.BytesIO(data)).read("circuit.stim").decode("utf-8"))
    est = estimate_ler(circuit, decoder="bposd", bp_max_iter=30, max_shots=6000, max_errors=8, batch=1000,
                       seed=20261029, rounds=3)
    assert est.decoder == "bposd" and est.errors >= 8, "the budget stops on errors, after whole batches"
    data, info = R.build(replace(base, budget=budget, ler=est.to_json()))
    folder = _unzip(data, tmp_path / "z")
    spec = json.loads((folder / "experiment.json").read_text(encoding="utf-8"))
    assert spec["decoder"]["label"] == "BP-OSD (ldpc: product-sum BP, 30 iterations; OSD-CS, order 7)"
    assert spec["decoder"]["parameters"] == {**spec["decoder"]["parameters"], "bp_method": "product_sum",
                                             "osd_method": "osd_cs", "osd_order": 7, "bp_max_iter": 30}
    assert "ldpc==" in (folder / "requirements.txt").read_text(encoding="utf-8")
    out = tmp_path / "got.json"
    r = _run(folder / "run.py", "--quiet", "--json", str(out))
    assert r.returncode == 0, r.stdout + r.stderr
    got = json.loads(out.read_text(encoding="utf-8"))
    assert (got["errors"], got["shots"], got["stopped"]) == (est.errors, est.shots, est.stopped)
    assert got["bp_max_iter_used"] == 30 and got["verdict"] == "identical"
    assert "REPRODUCED EXACTLY" in r.stdout


def test_bp_osd_without_a_cap_iterates_as_estimate_ler_does(tmp_path):
    """No `bp_max_iter` in the budget: as many iterations as detectors, at least 100."""
    pytest.importorskip("ldpc")
    import stim
    from dataclasses import replace
    from qccd.qec.decode import estimate_ler
    base = _point("surface3_mem", "ring24_8_j")
    budget = {"decoder": "bposd", "max_shots": 2000, "max_errors": 1000, "batch": 500, "seed": 7}
    data, _ = R.build(replace(base, budget=budget, ler={**base.ler, "decoder": "bposd"}))
    circuit = stim.Circuit(zipfile.ZipFile(io.BytesIO(data)).read("circuit.stim").decode("utf-8"))
    est = estimate_ler(circuit, decoder="bposd", max_shots=2000, max_errors=1000, batch=500, seed=7, rounds=3)
    data, _ = R.build(replace(base, budget=budget, ler=est.to_json()))
    folder = _unzip(data, tmp_path / "z")
    out = tmp_path / "got.json"
    r = _run(folder / "run.py", "--quiet", "--json", str(out))
    assert r.returncode == 0, r.stdout + r.stderr
    got = json.loads(out.read_text(encoding="utf-8"))
    assert (got["errors"], got["shots"]) == (est.errors, est.shots) and got["verdict"] == "identical"
    assert got["bp_max_iter_used"] == max(circuit.num_detectors, 100) == 100


def test_a_rate_outside_the_interval_is_not_reproduced(memory_zip, tmp_path):
    """The verdict bites: against a published rate ten times lower, the run says so and fails."""
    _, data, _, _ = memory_zip
    folder = _unzip(data, tmp_path / "z")
    spec = json.loads((folder / "experiment.json").read_text(encoding="utf-8"))
    ler = spec["published"]["ler"]
    ler.update(errors=20, ler=20 / ler["shots"], lo=0.5 * 20 / ler["shots"], hi=1.6 * 20 / ler["shots"])
    (folder / "experiment.json").write_text(json.dumps(spec), encoding="utf-8")
    r = _run(folder / "run.py", "--quiet")
    assert r.returncode == 1 and "NOT REPRODUCED" in r.stdout, r.stdout + r.stderr


# ------------------------------------------------------------------ regenerate.py

def test_regenerate_py_replays_the_program_and_rebuilds_the_stim_circuit(memory_zip):
    point, _, _, folder = memory_zip
    r = _run(folder / "regenerate.py", qccd=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"(published: {point.time_ms} ms)   identical" in r.stdout
    assert "every rule holds" in r.stdout and "identical to circuit.stim" in r.stdout
    assert "REPRODUCED: everything computed is identical." in r.stdout


def test_regenerate_py_catches_a_changed_program(memory_zip, tmp_path):
    """One cooling step fewer: another time, and the comparison says so."""
    _, data, _, _ = memory_zip
    folder = _unzip(data, tmp_path / "z")
    prog = json.loads((folder / "hardware_program.tsir.json").read_text(encoding="utf-8"))
    cut = next(i for i, ins in enumerate(prog["instructions"]) if ins.get("type") == "cool")
    del prog["instructions"][cut]
    (folder / "hardware_program.tsir.json").write_text(json.dumps(prog), encoding="utf-8")
    r = _run(folder / "regenerate.py", qccd=True)
    assert r.returncode == 1 and "DIFFERENT" in r.stdout and "NOT REPRODUCED" in r.stdout, r.stdout + r.stderr


def test_a_board_ranked_by_time_has_no_stim_circuit_and_says_why(time_zip):
    point, data, info, folder = time_zip
    assert zipfile.ZipFile(io.BytesIO(data)).namelist() == TIME_FILES
    spec = json.loads((folder / "experiment.json").read_text(encoding="utf-8"))
    assert spec["reproduces"] == "time" and spec["experiment"] is None and "decoder" not in spec
    assert "no noise model" in spec["why_no_noise_experiment"]
    assert spec["published"]["time"]["total_ms"] == point.time_ms == _row("steane", "grid5x5")["numbers"]["T_jones"]
    readme = (folder / "README.md").read_text(encoding="utf-8")
    assert "there is no stim circuit and no sampling script" in readme and "python regenerate.py" in readme
    assert "run.py" not in readme
    r = _run(folder / "regenerate.py", qccd=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"(published: {point.time_ms} ms)   identical" in r.stdout
    assert "the replay above is the whole reproduction" in r.stdout


def test_another_compile_of_the_point_is_refused():
    """A folder whose program does not replay to the published time is not that point."""
    from dataclasses import replace
    point = _point("steane", "grid5x5")
    with pytest.raises(R.ReproduceError, match="replays to .* the published row says"):
        R.build(replace(point, time_ms=point.time_ms + 1.0))
    with pytest.raises(R.ReproduceError, match="is missing"):
        R.build(replace(point, program=point.program.with_name("no_such.tsir.json")))


# ------------------------------------------------------------------ the decoder, in words

def test_the_decoder_is_named_with_its_parameters():
    assert R.decoder_label({"decoder": "pymatching"}) == "PyMatching 2 (minimum-weight perfect matching)"
    assert (R.decoder_label({"decoder": "bposd"}, {"bp_max_iter": 30})
            == "BP-OSD (ldpc: product-sum BP, 30 iterations; OSD-CS, order 7)")
    assert (R.decoder_label({"decoder": "bposd"}, {"decoder": "bposd"})
            == "BP-OSD (ldpc: product-sum BP, as many iterations as detectors, at least 100; OSD-CS, order 7)")
    # no result at hand: the release's budget says which decoder it asks for
    assert R.decoder_label(None, {"decoder": "pymatching"}).startswith("PyMatching 2")
    assert R.decoder_label(None, {"decoder": "bposd", "bp_max_iter": 30}).startswith("BP-OSD (ldpc: product-sum BP, 30")
    # a result that says its own decoder in words is taken at its word
    said = {"decoder": "bposd", "decoder_config": {"label": "BP-OSD, as the report says it"}}
    assert R.decoder_label(said, {"bp_max_iter": 30}) == "BP-OSD, as the report says it"
    assert R.decoder_config(said, {"bp_max_iter": 30})["parameters"]["bp_max_iter"] == 30
    # ... and when the result kept only that configuration, its decoder is read from it
    only = {"decoder_config": {"decoder": "bposd", "label": "BP-OSD (ldpc 2.4.1: product-sum BP, 30 iterations; OSD-CS order 7)"}}
    cfg = R.decoder_config(only, {"bp_max_iter": 30})
    assert cfg["name"] == "bposd" and cfg["label"] == only["decoder_config"]["label"] and cfg["package"] == "ldpc"
    assert set(R.decoder_labels({"bp_max_iter": 30})) == {"pymatching", "bposd"}
    # the settings a label states are the ones the decoder is built with
    src = (ROOT / "qccd" / "qec" / "decode.py").read_text(encoding="utf-8")
    for setting in ('bp_method="product_sum"', 'osd_method="osd_cs"', "osd_order: int = 7", "max_iter or max(nd, 100)"):
        assert setting in src, f"qccd/qec/decode.py no longer says {setting}: the labels and run.py must follow"


def test_every_release_with_a_logical_error_rate_names_a_decoder():
    from qccd.workspace.tasks import list_releases
    seen = 0
    for rel in list_releases():
        q = rel.manifest.get("qec")
        if not q:
            continue
        seen += 1
        label = R.decoder_label(None, q.get("ler"))
        assert label.startswith(("PyMatching 2 (", "BP-OSD (ldpc:")), (rel.id, label)
    assert seen >= 20


# ------------------------------------------------------------------ a board's zips, and the buttons

def test_a_board_s_zips_are_written_beside_its_pages(tmp_path):
    t = _task("surface3_mem")
    rows = [_row("surface3_mem", "ring24_8_j"), _row("surface3_mem", "grid9x9")]
    gone = {**rows[1], "key": "99_not_here", "prefix": "MemoryBoard/surface3_mem/work/no_such/prog"}
    refused = {"key": "98_refused", "status": "ok"}                 # no program: no zip, and not missing
    done = R.write_board(t, rows + [gone, refused], tmp_path)
    assert (done["built"], done["copied"], done["missing"], done["failed"]) == (2, 0, ["99_not_here"], {})
    for r in rows:
        z = tmp_path / "reproduce" / f"{r['key']}.zip"
        assert zipfile.ZipFile(z).namelist() == LER_FILES
        assert R.link("surface3_mem", r["key"]) == {"href": f"reproduce/{r['key']}.zip",
                                                    "name": f"surface3_mem-{r['key']}-reproduce.zip"}
    # a board whose programs are not in the repository: the zip its generator left is copied
    pre = tmp_path / "pre"
    pre.mkdir()
    (pre / "99_not_here.zip").write_bytes(b"PK-prebuilt")
    done = R.write_board(t, [gone], tmp_path / "b", prebuilt=pre)
    assert (done["built"], done["copied"], done["missing"]) == (0, 1, [])
    assert (tmp_path / "b" / "reproduce" / "99_not_here.zip").read_bytes() == b"PK-prebuilt"


def test_the_site_build_writes_the_zips_of_every_board():
    """The build's board loop calls the one builder, for the public board and for the area a
    restricted entry is served under, and reports an entry left without a zip."""
    src = (ROOT / "qccd" / "site" / "build.py").read_text(encoding="utf-8")
    assert src.count("repro.write_board(") == 2
    assert 't["rows"] + t["disqualified"], dst, prebuilt=src / repro.FOLDER' in src
    assert "NO ZIP for" in src


@pytest.fixture(scope="module")
def pages(tmp_path_factory):
    from qccd.arch import load
    from qccd.cost import corrected_model
    from qccd.ir.tsir import TSIR
    from qccd.verify import verify
    from qccd.viz.render import render_html
    arch = load(str(ROOT / "arch" / "chain.arch.json"))
    prog = TSIR(name="empty", arch_spec=arch.name)
    model = corrected_model()
    d = tmp_path_factory.mktemp("button")
    res = verify(prog, arch, model).result
    render_html(arch, prog, res, model, d / "with.html", source=None, reproduce=R.link("steane", "03_grid5x5"))
    render_html(arch, prog, res, model, d / "without.html", source=None)
    return (d / "with.html").read_text(encoding="utf-8"), (d / "without.html").read_text(encoding="utf-8")


def test_the_design_page_s_button_is_the_template_s_own(pages):
    """`render_html(reproduce=)`: one download link in the tools bar, labelled word for word,
    hidden on a page that has no zip; the page that has one carries where it is."""
    with_, without = pages
    link = '<a class="tb dl" id="tbReproduce" data-hint="reproduce" style="display:none" download>Download and reproduce</a>'
    assert with_.count(link) == 1 and without.count(link) == 1
    assert '"reproduce":{"href":"reproduce/03_grid5x5.zip","name":"steane-03_grid5x5-reproduce.zip"}' in with_
    assert '"reproduce":{' not in without
    assert "dl.setAttribute('href', D.reproduce.href)" in with_
    # a page without a zip does not offer the hidden link in its search either
    assert "if(el.id === 'tbReproduce' && !el.getAttribute('href')) continue;" in without
    # the hover card: what the zip holds, in words (a page names no source file)
    from qccd.viz.render import EDITOR_JS  # noqa: F401
    hints = (ROOT / "qccd" / "viz" / "js" / "editor.js").read_text(encoding="utf-8")
    m = re.search(r"'reproduce': \{ t: 'Download and reproduce', d: '([^']+)' \}", hints)
    assert m and not re.search(r"\w\.(py|json|stim|qasm)\b", m.group(1)), "the card names no file"
    assert R.LABEL == "Download and reproduce"


def test_every_generator_passes_the_button_through_the_template():
    """No generator draws the button itself: the board entries' and Goal 3's pages hand
    `render_html` the link, and nothing writes the label into a page after it is rendered."""
    bb = (ROOT / "Codesign" / "scripts" / "bb_studio.py").read_text(encoding="utf-8")
    assert "reproduce=zip_link)" in bb and "reproduce_zip.link(board, entry[\"key\"])" in bb
    for f in ("Codesign/scripts/memory_boards.py", "Codesign/scripts/small_codes.py", "Codesign/scripts/c2lr_boards.py"):
        text = (ROOT / f).read_text(encoding="utf-8")
        assert "tbReproduce" not in text, f


def test_a_board_s_index_offers_the_zip_on_every_compiled_row(tmp_path):
    """The seed index (`bb_studio.write_index`): the button in a row's detail panel and a link
    in the table's last column, and on a memory board the decoder of the rate."""
    sys.path.insert(0, str(ROOT / "Codesign" / "scripts"))
    import bb_studio as B
    seed = json.loads((ROOT / "MemoryBoard" / "surface3_mem" / "manifest.json").read_text(encoding="utf-8"))
    rows = [dict(r) for r in seed if r.get("status") == "ok" and r.get("prefix")
            and not r.get("rules_failed") and not r.get("over_budget")][:2]      # two ranked rows
    for r in rows:
        r["reproduce"] = R.link("surface3_mem", r["key"])
    B._RELEASE_OF.clear()
    B.release_of(ROOT / "MemoryBoard" / "surface3_mem")
    B._RELEASE_OF[str(tmp_path.resolve())] = "surface3_mem@1"
    page = B.write_index(rows, tmp_path).read_text(encoding="utf-8")
    data = json.loads(re.search(r"var DATA = (\[.*?\]);\n", page, re.S).group(1))
    assert len(data) == 2
    for d, r in zip(data, rows):
        assert d["zip"] == f"reproduce/{r['key']}.zip" and d["zipname"] == f"surface3_mem-{r['key']}-reproduce.zip"
        assert d["decoder"] == "PyMatching 2 (minimum-weight perfect matching)"
    assert "z.textContent='Download and reproduce'" in page and "z.setAttribute('download', d.zipname||'')" in page
    assert "if (d.zip) btns.appendChild(zipLink(d, 'btn dl'));" in page
    assert "tb.appendChild(row('Logical error rate: decoder', d.decoder))" in page


def test_the_bb_board_writes_its_zips_beside_its_seed_pages(tmp_path, monkeypatch):
    """The BB board's programs are not in the repository (Compiler/build/), so its generator
    writes the zips where the programs are, beside the seed pages, and the site build copies
    them.  Shown here on a board whose programs are in the tree."""
    sys.path.insert(0, str(ROOT / "Codesign" / "scripts"))
    import bb_studio as B
    assert B.task_of(ROOT / "BBResults" / "studio")["id"] == "bb144"
    assert B.task_of(tmp_path) is None and B.write_reproduce_zips([], tmp_path) is None
    monkeypatch.setattr(B, "task_of", lambda out_dir: _task("steane"))
    rows = [_row("steane", "grid5x5")]
    done = B.write_reproduce_zips(rows, tmp_path)
    assert done["built"] == 1 and not done["missing"] and not done["failed"]
    assert zipfile.ZipFile(tmp_path / "reproduce" / f"{rows[0]['key']}.zip").namelist() == TIME_FILES
    src = (ROOT / "Codesign" / "scripts" / "bb_studio.py").read_text(encoding="utf-8")
    assert '"--zips-only"' in src and '"--no-zips"' in src


def _scripts_parse(page: str, tmp_path: Path) -> int:
    """Every inline script of a page is JavaScript node accepts; how many were checked."""
    n = 0
    for i, js in enumerate(re.findall(r"<script>(.*?)</script>", page, re.S)):
        f = tmp_path / f"s{i}.js"
        f.write_text(js, encoding="utf-8")
        r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[-1500:]
        n += 1
    return n


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_pages_scripts_parse(tmp_path):
    """The index's chart script with the zip link, the official layer over it, and Goal 3's."""
    from qccd.site import build, goal3, leaderboard
    seed = (ROOT / "MemoryBoard" / "surface3_mem" / "index.html").read_text(encoding="utf-8")
    sys.path.insert(0, str(ROOT / "Codesign" / "scripts"))
    import bb_studio as B
    fresh = B._INDEX_TEMPLATE.replace("__DATA__", "[]").replace("__FAMHEX_MORE__", "")
    assert _scripts_parse(fresh, tmp_path) == 1
    layered = leaderboard.with_submissions(seed, "surface3_mem", "Surface code memory, distance 3")
    assert _scripts_parse(layered, tmp_path) >= 3
    assert _scripts_parse(goal3.page(build.PAGE, build.STYLE), tmp_path) >= 2


# ------------------------------------------------------------------ the decoder, wherever a rate is shown

def test_the_leaderboard_cards_and_the_noise_page_name_each_board_s_decoder():
    from qccd.site import boards as lb
    from qccd.site import build
    ts = build.tasks()
    cards = lb.memory_section(ts) + lb.c2lr_section(ts)
    n_cards = cards.count('<div class="lb-card"')
    assert n_cards >= 6
    assert cards.count('decoded by <a href="noise/#decoders">PyMatching 2 (minimum-weight perfect matching)</a>') == n_cards
    assert "each with Download and reproduce" in cards
    page = lb.noise_page(build.PAGE, build.STYLE)
    assert '<h2 id="decoders">How each rate is decoded</h2>' in page
    for label in ("PyMatching 2 (minimum-weight perfect matching)",
                  "BP-OSD (ldpc: product-sum BP, 30 iterations; OSD-CS, order 7)",
                  # a release that sets no cap (the Steane memory's)
                  "BP-OSD (ldpc: product-sum BP, as many iterations as detectors, at least 100; OSD-CS, order 7)"):
        assert f"<td>{label}</td>" in page, label
    assert "<b>Download and reproduce</b>" in page
    # one row per board with a rate
    assert page.count("<td>PyMatching 2") + page.count("<td>BP-OSD") == len(lb.memory_boards())


def test_an_official_entry_s_panel_names_its_decoder():
    """Official rows carry their decoder's name; the page reads it with the release's budget."""
    from qccd.site import leaderboard
    d = leaderboard.decoders()
    assert d["surface5xz_mem@1"]["pymatching"] == "PyMatching 2 (minimum-weight perfect matching)"
    assert d["bb18xz_mem@1"]["bposd"] == "BP-OSD (ldpc: product-sum BP, 30 iterations; OSD-CS, order 7)"
    assert d["steanexz_mem@1"]["bposd"].startswith("BP-OSD (ldpc: product-sum BP, as many iterations as detectors")
    assert set(d["*"]) == {"pymatching", "bposd"}
    seed = (ROOT / "MemoryBoard" / "surface3_mem" / "index.html").read_text(encoding="utf-8")
    page = leaderboard.with_submissions(seed, "surface3_mem", "Surface code memory, distance 3")
    assert "DECODERS = {" in page and '"surface3_mem@1": {"pymatching": "PyMatching 2 (minimum-weight' in page
    assert "tb.appendChild(row('decoder', decoderOf(d)))" in page
    assert "if (cfg && cfg.label) return cfg.label;" in page, "a result that says its decoder in words is taken at its word"
    assert "__DECODERS__" not in page


# ------------------------------------------------------------------ Goal 3

GOAL3 = ROOT / "goals" / "goal3"


def test_goal_3_links_every_built_point_and_names_each_code_s_decoder():
    from qccd.site import build, goal3
    index = json.loads((GOAL3 / "bundles.json").read_text(encoding="utf-8"))
    assert len(index.get("surface3", {})) == 21, "every size of the distance-3 surface code"
    d = goal3.data()
    by = {c["task"]: c for c in d["codes"]}
    linked = 0
    for code, pts in index.items():
        c = by[f"goal3_{code}_mem"]
        drawn = {p["n"]: p for p in c["points"] + c["failed"]}
        for name, z in pts.items():
            # an indexed point is a compiled point of the results, and the page's data links its zip
            assert z["zip"] == f"{code}/{name}.zip" and z["name"] == R.zip_name(f"goal3-{code}", name)
            assert drawn[name]["z"] == f"z/{code}/{name}.zip" and drawn[name]["zn"] == z["name"]
            assert z["reproduces"] in ("ler", "time")
            linked += 1
    assert linked >= 21
    assert by["goal3_surface3_mem"]["decoder"] == "PyMatching 2 (minimum-weight perfect matching)"
    for code in ("color3", "bb72", "hgp58"):
        assert by[f"goal3_{code}_mem"]["decoder"] == "BP-OSD (ldpc: product-sum BP, 30 iterations; OSD-CS, order 7)"
    page = goal3.page(build.PAGE, build.STYLE)
    assert '<details id="g3-reproduce"><summary>Every point: download and reproduce</summary>' in page
    assert 'id="g3-zips"' in page and "H('a', null, 'Download and reproduce')" in page
    assert "'Decoded by ' + c.decoder" in page, "the tooltip of a point"
    assert "'Every rate of this code is decoded by ' + c.decoder" in page, "under each family's best"
    # About this goal: which codes by which decoder
    assert "by PyMatching 2 (minimum-weight perfect matching); " in page
    assert "by BP-OSD (ldpc: product-sum BP, 30 iterations; OSD-CS, order 7)." in page


def test_a_goal_3_point_s_page_gets_its_button_once_its_zip_is_built():
    sys.path.insert(0, str(GOAL3))
    import pages
    assert pages.reproduce_link({"code": "surface3", "point": "c2lr4x4"}) == {
        "href": "../../z/surface3/c2lr4x4.zip", "name": "goal3-surface3-c2lr4x4-reproduce.zip"}
    assert pages.reproduce_link({"code": "surface3", "point": "no_such_size"}) is None
    assert "reproduce=reproduce_link(row))" in (GOAL3 / "pages.py").read_text(encoding="utf-8")


def test_a_goal_3_zip_is_the_row_s_own_sample_from_the_folder_that_holds_it(tmp_path):
    """bundles.py: the row's own budget (its batch size too), the sweep's noise model and clock,
    the family's rule set; the folder whose report is the row's sample first; and a folder
    without the row's program is refused, never zipped."""
    sys.path.insert(0, str(GOAL3))
    import bundles
    row = next(r for r in bundles.rows("surface3") if r["point"] == "helios98")
    assert row["ler_budget"]["batch"] == 500, "this size was sampled in smaller batches than the release's"
    pt = bundles.point_of(row, tmp_path)
    assert pt.budget == row["ler_budget"] and pt.ler == row["ler"]
    assert (pt.noise, pt.table, pt.release) == ("qccd-noise@3", "helios", "goal3_surface3_mem@1")
    assert pt.rule_set == row["rule_set"] and pt.replay_rules is False
    assert pt.time_ms == row["T_ms"] and pt.time_tol_ms <= 1e-3
    assert pt.name == "goal3-surface3-helios98-reproduce.zip"
    a, b = tmp_path / "a", tmp_path / "b"
    for root, errors in ((a, row["ler"]["errors"] + 1), (b, row["ler"]["errors"])):
        w = root / "surface3" / "helios98"
        w.mkdir(parents=True)
        (w / "prog.cooled.tsir.json").write_text("{}", encoding="utf-8")
        (w / "ler_report.json").write_text(json.dumps({"ler": {**row["ler"], "errors": errors}}), encoding="utf-8")
    wa, wb = a / "surface3" / "helios98", b / "surface3" / "helios98"
    assert bundles.candidates("surface3", "helios98", [a, b, tmp_path / "c"]) == [wa, wb]
    assert bundles.same_sample(row, wa) is False and bundles.same_sample(row, wb) is True
    assert bundles.same_sample(row, tmp_path) is None
    with pytest.raises(R.ReproduceError, match="no work folder holds this row's program"):
        bundles.build(row, [str(wa), str(wb)], str(tmp_path / "out"))
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").rglob("*.zip"))
