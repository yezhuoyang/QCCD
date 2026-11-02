"""The debugger: qccd/debug, the website's Debug page (qccd/site/debugpage.py + qccd/site/debug/) and
the agent's qccd_debug (qccd/workspace/debugging.py).

What it must get right, in order of how badly a reader is misled when it does not:

* a verdict is the grader's verdict: the same rule set judges the same programme the same way;
* a failure is placed where it happens: the step the Studio shows ("Step 7 / 11" is step 7) and
  the sites, junctions, rails and ions it names, for as long as it lasts;
* what a person types (assertions, breakpoints, expressions) means what it says, quoted names
  included;
* the page runs the same Python in the browser (Pyodide), and drives the Studio page unchanged.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from qccd.debug import DebugError, assertion_rules, open_documents, open_files
from qccd.debug import web

REPO = Path(__file__).resolve().parents[1]
C = REPO / "qccd" / "site" / "compiled"
ARCH, PROG, CERT = C / "chain4.arch.json", C / "bell" / "prog.cooled.tsir.json", C / "bell" / "prog.qcert.json"
PYODIDE = REPO / "out" / "debug-cache"

from chrome_path import CHROME  # noqa: E402


@pytest.fixture(scope="module")
def bell():
    return open_files(ARCH, PROG)


# ------------------------------------------------------------------ steps and states

def test_steps_count_like_the_studio(bell):
    # the init is step 1 (the page's frame 0), and the grader's replay steps are renumbered to it
    assert bell.n == 11
    assert bell.step(1)["type"] == "init" and bell.step(-1)["type"] == "measure"
    assert bell.labels[0] == "the start" and "during step 5" in bell.labels and "after step 11" in bell.labels
    assert bell.renumber("MS q0,q1 at C0, step 5") == "MS q0,q1 at C0, step 7"   # replay step 5 is instruction 7
    with pytest.raises(DebugError, match="steps 1 to 11"):
        bell.step(0)
    with pytest.raises(DebugError, match="steps 1 to 11"):
        bell.state(12)


def test_state_trace_and_diff(bell):
    st = bell.state(5, watch=["q0", "C0", "C1"])
    assert st["step_info"]["type"] == "simd" and st["moved"] == ["q0"]
    assert st["positions"]["q0"] == "C0" and st["occupied"]["C0"]["ions"] == ["q0", "q1"]
    assert st["watch"]["C1"]["ions"] == [] and st["watch"]["q0"]["at"] == "C0"
    assert st["watch"]["C0"]["during_step"] == 2
    tr = bell.trace("q0")
    assert [e["step"] for e in tr["events"]][:2] == [1, 2] and any(e["at"] == "C0" for e in tr["events"])
    assert bell.diff(1, 11)["moved"] == {"q0": ["C1", "C0"]}
    with pytest.raises(DebugError, match="not an ion, site, junction or rail"):
        bell.trace("nowhere")


# ------------------------------------------------------------------ rules

def test_a_rule_set_is_judged_as_the_grader_judges_it(bell):
    from qccd.cost.models import corrected_model
    from qccd.ruleset import check_programme, load
    rep = check_programme(load("standard"), bell.prog, bell.arch, corrected_model())
    mine = bell.check("standard")
    assert {r["id"]: r["verdict"] for r in mine["rules"]} == {r.id: r.verdict for r in rep.results}
    assert {r["id"]: r.get("count", 0) for r in mine["rules"]} == {r.id: r.count for r in rep.results}
    assert mine["failing"] == rep.failed() and mine["ok"] == rep.ok


def test_a_failure_is_placed_where_and_when_it_happens(bell):
    r7 = next(r for r in bell.check("standard", only=["R7"])["rules"] if r["id"] == "R7")
    assert r7["verdict"] == "fails" and r7["count"] == 1
    f = r7["failures"][0]
    assert (f["step"], f["until"], f["phase"]) == (7, 7, "at")              # the MS gate is step 7
    assert {"kind": "site", "id": "C0"} in f["where"]
    assert {w["id"] for w in f["where"] if w["kind"] == "ion"} == {"q0", "q1"}
    assert all(w.get("at") == "C0" for w in f["where"] if w["kind"] == "ion")
    assert "step 7" in f["message"] and "step 6" not in f["message"]       # renumbered to the page's steps


def test_a_state_that_stays_bad_stays_crossed_out(bell):
    # the rule language reports a capacity breach once, where it starts; the page shows it until it ends
    r = bell.check(None, "for all s in sites, t in instants: occupancy(s, t) <= 1")
    a1 = r["rules"][0]
    assert a1["id"] == "A1" and a1["origin"] == "yours" and a1["verdict"] == "fails"
    f = a1["failures"][0]
    assert (f["step"], f["until"], f["phase"], f["where"]) == (5, 11, "during", [{"kind": "site", "id": "C0"}])
    assert [x["id"] for x in r["rules"]] == ["A1"]                        # none: only the assertions


def test_quoted_names_mean_what_they_say(bell):
    # the rule language looks a quoted argument up as a string (occupancy("C0", t) reads 0); here it is the site
    assert bell.value('occupancy("C0", t)', 5, phase="during")["value"] == 2
    assert bell.value('position("q0", t)', 6)["value"] == "C0"
    r = bell.check(None, 'assert "C0 holds one":\nfor all t in instants: occupancy("C0", t) <= 1')
    a = r["rules"][0]
    assert a["title"] == "C0 holds one" and a["verdict"] == "fails"
    assert a["failures"][0]["where"] == [{"kind": "site", "id": "C0"}] and a["failures"][0]["step"] == 5
    assert 'occupancy("C0", t)' in a["failures"][0]["message"]


def test_breakpoints_and_expressions(bell):
    hit = bell.find('occupancy("C0", t) >= 2')
    assert (hit["found"], hit["step"], hit["phase"]) == (True, 5, "during")
    back = bell.find('position("q0", t) = "C1"', 11, backward=True)
    assert back["found"] and back["step"] == 4
    assert bell.find('occupancy("C3", t) > 0')["found"] is False
    with pytest.raises(DebugError, match="neither the instant t"):
        bell.find("occupancy(s, t) > 0")
    with pytest.raises(DebugError, match="does not parse"):
        bell.value("occupancy(", 2)


def test_assertion_text_forms():
    assert assertion_rules("") == ""
    two = assertion_rules('for all t in instants: true\n\nassert "named":\nfor all s in sites: capacity(s) >= 1')
    assert 'rule A1 "assertion 1"' in two and 'rule A2 "named"' in two
    own = 'rule MINE "kept as written"\n  for all t in instants: true\n'
    assert assertion_rules(own) == own
    from qccd.ruleset.syntax import parse
    assert [r.id for r in parse(two).rules] == ["A1", "A2"]


def test_bad_input_is_said_in_words(bell):
    with pytest.raises(DebugError, match="do not parse"):
        bell.check(None, "for all s in sites: occupancy(s, t) <=")
    with pytest.raises(DebugError, match="crowding.*not a word of the vocabulary"):
        bell.check(None, "for all s in sites, t in instants: crowding(s, t) <= 1")
    with pytest.raises(DebugError, match="neither a variable it binds"):
        bell.check(None, "for all s in sites: occupancy(s, moment) <= 1")
    with pytest.raises(DebugError, match="not a word of the vocabulary"):
        bell.find("crowding(t) > 1")
    with pytest.raises(DebugError, match="cannot be read"):
        bell.check("no-such-set")
    with pytest.raises(DebugError, match="not JSON"):
        open_documents("{", PROG.read_text(encoding="utf-8"))


def test_the_certificate_brings_the_circuit():
    plain = open_files(ARCH, PROG).check("standard", only=["R24", "R25"])
    withc = open_files(ARCH, PROG, certificate=CERT).check("standard", only=["R24", "R25"])
    assert {r["verdict"] for r in plain["rules"]} == {"does not apply"}
    assert {r["verdict"] for r in withc["rules"]} == {"holds"}


# ------------------------------------------------------------------ the glue the page and the agent call

def test_the_web_glue_speaks_json():
    a, p = ARCH.read_text(encoding="utf-8"), PROG.read_text(encoding="utf-8")
    o = json.loads(web.open_session(a, p, "null", "bell"))
    assert o["ok"] and o["summary"]["steps"] == 11 and o["instants"][0] == {"step": 1, "phase": "start", "label": "the start"}
    c = json.loads(web.call(o["key"], "check", json.dumps({"rules": "standard"})))
    assert c["ok"] is True and c["all_hold"] is False and c["failing"] == ["R7"]   # the call worked; a rule failed
    v = json.loads(web.call(o["key"], "value", json.dumps({"exprs": ['occupancy("C0", t)'], "step": 6})))
    assert v["values"][0]["value"] == 2
    bad = json.loads(web.call(o["key"], "state", json.dumps({"k": 99})))
    assert bad["ok"] is False and bad["error"]
    assert json.loads(web.call("nope", "state", "{}"))["error"].startswith("that programme is no longer open")
    assert json.loads(web.call(o["key"], "rm", "{}"))["ok"] is False
    rs = json.loads(web.rulesets())
    assert "standard" in [r["name"] for r in rs["rulesets"]]


def test_the_command_line(capsys):
    from qccd.debug.__main__ import main
    rc = main([str(PROG), "--arch", str(ARCH), "check", "--rules", "standard", "--only", "R7"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["failing"] == ["R7"]
    assert main([str(PROG), "--arch", str(ARCH), "find", 'occupancy("C0", t) >= 2']) == 0
    assert json.loads(capsys.readouterr().out)["step"] == 5
    assert main([str(PROG), "--arch", str(ARCH), "state", "40"]) == 2
    assert "no step 40" in json.loads(capsys.readouterr().out)["error"]


def test_the_agents_tool_reads_runs_and_files(tmp_path):
    from qccd.workspace.core import WorkspaceError
    from qccd.workspace.debugging import debug_call

    class WS:
        root = tmp_path.resolve()
        arts = {"sha:dev": ARCH.read_bytes(), "sha:prog": PROG.read_bytes(), "sha:cert": CERT.read_bytes()}

        def run(self, rid):
            if rid != "r1":
                raise WorkspaceError("not_found", "no run", status=404)
            return {"artifacts": {"device": "sha:dev", "program": "sha:prog", "certificate": "sha:cert"},
                    "program": {"name": "bell"}, "design": {"name": "chain4"}}

        def get_artifact(self, d):
            return self.arts[d]

    ws = WS()
    out = debug_call(ws, {"action": "check", "run_id": "r1", "only": ["R7", "R24"]})
    assert out["failing"] == ["R7"] and out["all_hold"] is False and "run r1" in out["program"]
    assert {r["id"]: r["verdict"] for r in out["rules"]}["R24"] == "holds"        # the run's certificate came along
    shutil.copy(ARCH, tmp_path / "d.arch.json")
    shutil.copy(PROG, tmp_path / "p.tsir.json")
    st = debug_call(ws, {"action": "state", "arch": "d.arch.json", "program": "p.tsir.json", "step": 5})
    assert st["moved"] == ["q0"] and st["step"] == 5
    with pytest.raises(WorkspaceError, match="inside the workspace"):
        debug_call(ws, {"action": "summary", "arch": "../x.arch.json", "program": "p.tsir.json"})
    with pytest.raises(WorkspaceError, match="name the program"):
        debug_call(ws, {"action": "summary"})
    with pytest.raises(WorkspaceError, match="steps 1 to 11"):
        debug_call(ws, {"action": "step", "run_id": "r1", "step": 99})


# ------------------------------------------------------------------ the website's page

def test_the_page_section(tmp_path):
    from qccd.site import debugpage as dp
    z1, z2 = dp.python_zip(), dp.python_zip()
    assert z1 == z2, "the Python the page runs is the same bytes every build"
    names = set(zipfile.ZipFile(io.BytesIO(z1)).namelist())
    for need in ("qccd/debug/web.py", "qccd/ruleset/library/catalogue.rules", "qccd/viz/render.py",
                 "qccd/viz/engine.js", "qccd/evalset/library", "arch/ring144_24v.arch.json"):
        assert any(n.startswith(need) for n in names), need
    cat = dp.catalogue([("studio.html", "x"), ("board/rep5_mem/01_ring.html", "ring"), ("compilation/ex/bell.html", "bell"),
                        ("reproduce/jones/x.html", "j"), ("debug/index.html", "me")],
                       [{"u": "board/rep5_mem/01_ring.html", "t": "A ring"}])
    assert [(r["g"], r["t"]) for r in cat] == [("Leaderboard", "A ring"), ("Reproduce", "j"), ("Compilation", "bell"),
                                              ("Design and tutorial", "The Design page (a blank canvas, or your own design)")]
    cfg = dp.config(cat)
    api = json.loads((dp.SRC / "debug_api.json").read_text(encoding="utf-8"))
    assert cfg["verbs"] == sorted(api["verbs"]) and cfg["rulesets"][0]["name"] == "standard"
    s = open_files(ARCH, PROG)
    for title, text in dp.EXAMPLES:                       # every example the page offers runs
        r = s.check(None, text)
        assert r["rules"] and r["rules"][0]["verdict"] in ("holds", "fails"), title
    html = dp.page("<html><head><style>{style}{extra_css}</style></head><body>{body}__FOOTER__</body></html>"
                   .replace("{title}", ""), "", cfg)
    assert "window.QCCD_DEBUG_CONFIG=" in html and "__FOOTER__" not in html and "</script>" in html


def test_the_mirror_lets_the_rule_language_compile():
    from qccd.workspace.mirror import mirror_csp
    csp = mirror_csp("http://127.0.0.1:47101")
    script = next(d for d in csp.split(";") if d.strip().startswith("script-src"))
    assert "'wasm-unsafe-eval'" in script and "'unsafe-eval'" not in script.replace("'wasm-unsafe-eval'", "")


def _node():
    return shutil.which("node")


def _pyodide_dir():
    """The runtime exactly as the page serves it (renamed and patched, debugpage.served), made from
    the cached release; None when there is no cached release (a site build fetches it)."""
    from qccd.site import debugpage as dp
    tar = PYODIDE / f"pyodide-core-{dp.PYODIDE_VERSION}.tar.bz2"
    if not tar.exists():
        return None
    d = PYODIDE / f"served-{dp.PYODIDE_VERSION}"
    d.mkdir(parents=True, exist_ok=True)
    for name, data in dp.pyodide_files().items():
        if not (d / name).exists() or (d / name).read_bytes() != data:
            (d / name).write_bytes(data)
    return d


@pytest.mark.skipif(not _node(), reason="needs node")
def test_the_rule_language_runs_in_the_browser_runtime(tmp_path):
    """The zip the page ships, under the pinned Pyodide (in Node): the same verdicts as here."""
    d = _pyodide_dir()
    if d is None:
        pytest.skip("no cached Pyodide release (out/debug-cache): a site build fetches it")
    from qccd.site import debugpage as dp
    (tmp_path / "qccd-py.zip").write_bytes(dp.python_zip())
    script = tmp_path / "run.mjs"
    script.write_text(
        "import { loadPyodide } from " + json.dumps((d / "pyodide.esm.js").as_uri()) + ";\n"
        "import { readFileSync } from 'node:fs';\n"
        "const py = await loadPyodide({ indexURL: " + json.dumps(str(d).replace("\\", "/") + "/") + " });\n"
        "py.unpackArchive(new Uint8Array(readFileSync(" + json.dumps(str(tmp_path / "qccd-py.zip")) + ")), 'zip', "
        "{ extractDir: '/home/pyodide/qccd-py' });\n"
        "py.runPython(\"import sys\\nsys.path.insert(0, '/home/pyodide/qccd-py')\");\n"
        "const W = py.pyimport('qccd.debug.web');\n"
        "const o = JSON.parse(W.open_session(readFileSync(" + json.dumps(str(ARCH)) + ", 'utf8'), readFileSync("
        + json.dumps(str(PROG)) + ", 'utf8'), 'null', 'bell'));\n"
        "const c = JSON.parse(W.call(o.key, 'check', JSON.stringify({rules: 'standard', assertions: "
        "'for all s in sites, t in instants: occupancy(s, t) <= 1'})));\n"
        "console.log(JSON.stringify({failing: c.failing, a1: c.rules.find(r => r.id === 'A1').failures[0]}));\n",
        encoding="utf-8")
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=240)
    assert r.returncode == 0, r.stderr[-2000:]
    got = json.loads(r.stdout.strip().splitlines()[-1])
    native = open_files(ARCH, PROG).check("standard", "for all s in sites, t in instants: occupancy(s, t) <= 1")
    assert got["failing"] == native["failing"] == ["R7", "A1"]
    assert got["a1"] == next(x for x in native["rules"] if x["id"] == "A1")["failures"][0]


@pytest.mark.skipif(not (_node() and CHROME and Path(CHROME).exists()), reason="needs node and Chrome")
def test_the_page_in_a_browser(tmp_path):
    """The Debug page hosting a Studio page: open, step, watch, check, crosses, report -- and the
    same under the workspace mirror's security policy."""
    if _pyodide_dir() is None:
        pytest.skip("no cached Pyodide release (out/debug-cache): a site build fetches it")
    from qccd.arch import load as load_arch
    from qccd.cost.models import corrected_model
    from qccd.ir.tsir import TSIR
    from qccd.site import debugpage as dp
    from qccd.verify.replay import replay
    from qccd.viz.render import render_html
    site = tmp_path / "site"
    (site / "compilation" / "ex").mkdir(parents=True)
    arch, prog, model = load_arch(ARCH), TSIR.load(PROG), corrected_model()
    render_html(arch, prog, replay(prog, arch, model, check_rules=True, keep_cycles=True), model,
                site / "compilation" / "ex" / "bell.html")
    assert dp.main(["-o", str(site), "--pages", str(site)]) == 0
    from qccd.workspace.mirror import mirror_csp
    for csp in (None, mirror_csp("http://127.0.0.1:47101")):
        args = ["node", str(REPO / "tests" / "debug_page.mjs"), str(site), "compilation/ex/bell.html"]
        if csp:
            args += ["--csp", csp]
        r = subprocess.run(args, capture_output=True, text=True, timeout=300)
        out = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {"fails": [r.stderr[-800:]]}
        where = "under the mirror's policy" if csp else "with no policy"
        assert r.returncode == 0 and not out["fails"], f"{where}: {json.dumps(out)}"
        assert out["steps"] == 11 and "A1" in out["failing"] and out["crosses"] > 0
