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
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from qccd.debug import DebugError, assertion_rules, open_documents, open_files
from qccd.debug import web

REPO = Path(__file__).resolve().parents[1]
C = REPO / "qccd" / "site" / "compiled"
# the Bell pair as the compiler emits it, BEFORE the cooling pass: its MS gate starts hot (R7 fails at
# step 7), which is exactly what the pass is for -- a program with a known failure, kept in the repository
ARCH, PROG, CERT = C / "chain4.arch.json", C / "bell" / "prog.tsir.json", C / "bell" / "prog.qcert.json"
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


# ------------------------------------------------------------------ a rule at work, step by step

def test_every_rule_at_a_step_says_what_it_judges_there(bell):
    at7 = {r["id"]: r for r in bell.rules_at(7)["rules"]}
    assert (at7["R7"]["here"], at7["R7"]["cases"], at7["R7"]["failing"]) == ("fails", 2, 1)   # MS q0,q1: two ions
    at6 = {r["id"]: r for r in bell.rules_at(6)["rules"]}
    assert at6["R7"]["here"] == "nothing to judge"                      # no two-ion gate at step 6
    assert any(r.get("every_step") for r in at6.values())              # a rule about the device alone
    failing = {(k, r["id"]) for k in range(1, bell.n + 1) for r in bell.rules_at(k)["rules"] if r["here"] == "fails"}
    assert failing == {(7, "R7")}, "the steps where a rule fails are the ones check locates"


def test_a_rule_at_one_step_shows_every_case_with_its_values(bell):
    ins = bell.inspect("R7", 7)
    assert (ins["cases"], ins["failing"], ins["here"]) == (2, 1, "fails")
    bad, ok = ins["shown"]                                              # failing first
    assert not bad["holds"] and ok["holds"] and bad["step"] == 7
    assert bad["values"]["i"] == "ion q0" and {"kind": "ion", "id": "q0", "at": "C0"} in bad["where"]
    why = bad["why"]                                                    # both sides of the comparison
    assert why["op"] == "<=" and why["value"] is False and why["left"]["number"] > why["right"]["number"] == 1
    assert bad["gauge"]["holds"] is False and bad["gauge"]["room"] < 0 and bad["gauge"]["limit"] == 1
    assert ok["gauge"]["holds"] is True and ok["gauge"]["value"] <= 1
    # the grader's sentence, without the variables the case lists beside it
    assert bell.check("standard", only=["R7"])["rules"][0]["failures"][0]["message"].endswith(": " + bad["message"])
    assert bell.inspect("R7", 6)["note"] == "this rule judges nothing at this step"
    with pytest.raises(DebugError, match="not a rule"):
        bell.inspect("R99", 7)


def test_the_closest_case_of_a_rule_that_holds(bell):
    c = bell.closest("R13")                                             # a gate's chain under max_chain
    assert c["found"] and c["complete"] and c["room"] == 13 and c["case"]["gauge"]["limit"] == 15
    assert c["case"]["step"] is not None and c["case"]["holds"]
    r7 = bell.closest("R7")
    assert r7["failing"] == 1 and r7["found"] and r7["room"] >= 0      # a failure is not a near miss


def test_a_failure_carries_its_gauge(bell):
    f = bell.check("standard", only=["R7"])["rules"][0]["failures"][0]
    assert f["gauge"]["holds"] is False and f["gauge"]["value"] > f["gauge"]["limit"]
    assert f["gauge"]["left"] == "heat(i, start(g))" and f["gauge"]["right"] == "heat_budget()"


def test_a_device_rule_fails_at_every_step():
    """A device with an 18 degree corner (from the review skill's evaluations): R20 is about the
    device alone, so its failure names no step and is drawn at every one."""
    arch = (REPO / "tests" / "fixtures" / "sharp_corner.arch.json").read_text(encoding="utf-8")
    prog = json.dumps({"name": "nothing", "arch_spec": "sharp_corner", "instructions": []})
    r20 = open_documents(arch, prog).check("standard", only=["R20"])["rules"][0]
    assert r20["verdict"] == "fails" and r20["failures"][0]["step"] is None
    assert r20["failures"][0]["gauge"]["holds"] is False                # the angle against its minimum


def test_your_own_assertion_at_a_step(bell):
    one = "for all s in sites, t in instants: occupancy(s, t) <= 1"
    ins = bell.inspect("A1", 5, None, one)                              # step 5 brings q0 into C0, beside q1
    assert ins["here"] == "fails" and any(not c["holds"] and {"kind": "site", "id": "C0"} in c["where"] for c in ins["shown"])
    assert bell.inspect("A1", 3, None, one)["here"] == "holds"


def test_the_review_guide_says_what_every_rule_of_every_set_means():
    """qccd/debug/guide.md is hand-written: a rule added to any shipped set fails here until it has a
    row (what its failure usually means, and the fix).  The page shows a person those words, so they
    name no file of the repository."""
    from qccd.debug import guide
    from qccd.ruleset import catalogue, load, names
    ids = [r.id for r in catalogue().rules] + [i for n in names("device") + names("machine") for i in load(n).ids]
    missing = sorted(set(ids) - set(guide()))
    assert not missing, ("qccd/debug/guide.md has no row for " + ", ".join(missing)
                         + ": add one to its section 2 (rule | usually means | fix)")
    assert "budget" in guide()
    for rid, row in guide().items():
        for text in row.values():
            words = re.sub(r"python -m qccd[\w.]*", "", text)          # a command to type is not a file
            assert not re.search(r"qccd/|qccd\.[a-z]|\.py\b|references/|review\.py|snippet", words), (rid, text)


def test_the_skill_and_the_page_carry_the_same_guide():
    from qccd.debug.guide import GUIDE, guide
    from qccd.site import debugpage as dp
    from qccd.workspace.installers import _skill_files
    assert _skill_files()["references/review-guide.md"] == GUIDE.read_bytes()
    assert dp.config([])["guide"] == guide()


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
    at = json.loads(web.call(o["key"], "rules_at", json.dumps({"step": 7})))
    assert at["ok"] and {r["id"]: r["here"] for r in at["rules"]}["R7"] == "fails"
    ins = json.loads(web.call(o["key"], "inspect", json.dumps({"rule": "R7", "step": 7})))
    assert ins["ok"] and ins["failing"] == 1
    cl = json.loads(web.call(o["key"], "closest", json.dumps({"rule": "R13"})))
    assert cl["ok"] and cl["room"] == 13


def test_the_workspace_pages_carry_the_debugger():
    """The person's own Studio and every run's page get the panel, its rule language the service's."""
    from qccd.site import debugpage as dp
    html = dp.workspace_inject("/api/debug/web")
    cfg = json.loads(re.search(r"window\.QCCD_DEBUG_CONFIG=(\{.*?\});</script>", html, re.S).group(1))
    assert cfg["host"] == "inpage" and cfg["python"] == {"backend": "service", "endpoint": "/api/debug/web"}
    assert cfg["guide"]["R7"]["fix"] and cfg["catalogue"] == []
    assert 'id="qdbg-workspace"' in html and "window.QCCD_DEBUG = API" in html
    body = html.split("window.QCCD_DEBUG_CONFIG=", 1)[1]
    assert body.count("</script>") == 3, "the panel's code cannot close its own script element"


def test_the_command_line(capsys):
    from qccd.debug.__main__ import main
    rc = main([str(PROG), "--arch", str(ARCH), "check", "--rules", "standard", "--only", "R7"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["failing"] == ["R7"]
    assert main([str(PROG), "--arch", str(ARCH), "find", 'occupancy("C0", t) >= 2']) == 0
    assert json.loads(capsys.readouterr().out)["step"] == 5
    assert main([str(PROG), "--arch", str(ARCH), "state", "40"]) == 2
    assert "no step 40" in json.loads(capsys.readouterr().out)["error"]
    assert main([str(PROG), "--arch", str(ARCH), "inspect", "R7", "7"]) == 0
    assert json.loads(capsys.readouterr().out)["failing"] == 1
    assert main([str(PROG), "--arch", str(ARCH), "closest", "R13"]) == 0
    assert json.loads(capsys.readouterr().out)["room"] == 13
    assert main([str(PROG), "--arch", str(ARCH), "guide", "R7"]) == 0
    assert "re-cool" in json.loads(capsys.readouterr().out)["fix"]
    assert main([str(PROG), "--arch", str(ARCH), "guide", "R99"]) == 2


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
def test_the_workspace_studio_debugs_and_hands_a_review_to_the_agent(tmp_path, monkeypatch):
    """The person's own Studio: the Debug button opens the panel beside the design, its rule
    language is the workspace's (POST /api/debug/web, nothing downloaded), and "Review with my
    agent" writes the request into the chat for the person to send -- it never sends it."""
    import sys
    from qccd.workspace.app import Workspace
    from qccd.workspace.runtime import ensure_service, service_request
    monkeypatch.setenv("QCCD_RUNTIME_DIR", str(tmp_path / "runtime"))
    for k in ("QCCD_CODEX", "QCCD_CLAUDE", "QCCD_CURSOR"):
        monkeypatch.setenv(k, "none")
    Workspace.init(tmp_path / "ws", "ghz4@1").close()
    info = ensure_service(tmp_path / "ws", python=sys.executable)
    try:
        # the route the panel calls, with the same answers the page's worker gives
        a, p = ARCH.read_text(encoding="utf-8"), PROG.read_text(encoding="utf-8")
        o = json.loads(service_request(info, "POST", "/api/debug/web", {"fn": "open_session", "args": [a, p, "null", "bell"]},
                                       token="owner")["text"])
        c = json.loads(service_request(info, "POST", "/api/debug/web",
                                       {"fn": "call", "args": [o["key"], "inspect", json.dumps({"rule": "R7", "step": 7})]},
                                       token="owner")["text"])
        assert o["ok"] and c["ok"] and c["failing"] == 1
        code = service_request(info, "POST", "/api/pair-code", {}, token="owner")["code"]
        spec = tmp_path / "spec.json"
        spec.write_text(json.dumps({"pages": {"a": f"http://127.0.0.1:{info['port']}/studio#pair={code}"}, "steps": [
            {"wait": "!!window.EDITOR && !!window.QCCD_DEBUG_INPAGE && !!document.getElementById('qdbgBtn')"
                     " && !!document.getElementById('qcl-text')", "timeout": 40000, "stopOnFail": True},
            {"eval": "document.getElementById('qdbgBtn').click(), true"},
            {"wait": "!!document.getElementById('dbg') && QCCD_DEBUG_INPAGE.active", "timeout": 20000},
            {"eval": "QCCD_DEBUG.check({rules: 'standard'}).then(function (r) { return JSON.stringify({n: r.rules.length,"
                     " failing: r.failing, py: QCCD_DEBUG.status().python}); })"},
            {"eval": "document.querySelector('#dTabs [data-tab=rules]').click(), document.getElementById('rAgent').click(),"
                     " JSON.stringify({chat: document.getElementById('qcl-text').value,"
                     " help: !!document.querySelector('.q-agenthelp')})"},
            # the chat and the panel share the right edge: the chat stands left of the panel while it is open
            {"eval": "Math.round(document.getElementById('qcl-dock').getBoundingClientRect().right"
                     " - document.getElementById('dbg').getBoundingClientRect().left)"},
            {"eval": "document.getElementById('qdbgBtn').click(), true"},
            {"wait": "!document.getElementById('dbg')", "timeout": 10000},
            {"eval": "Math.round(innerWidth - document.getElementById('qcl-dock').getBoundingClientRect().right)"}]}), encoding="utf-8")
        r = subprocess.run(["node", str(REPO / "tests" / "workspace_browser.mjs"), str(spec)], capture_output=True,
                           timeout=300, cwd=REPO)
        out = json.loads(r.stdout.decode("utf-8") or "{}")
        st = out.get("steps", [])
        assert len(st) == 9 and all(s.get("ok", True) for s in st[:3]), json.dumps(out)[:3000]
        chk = json.loads(st[3]["value"])
        assert chk["n"] > 20 and chk["py"] == "ready", chk                 # judged by the workspace's Python
        got = json.loads(st[4]["value"])
        assert got["chat"].startswith("Review this program with me") and not got["help"], got
        assert st[5]["value"] <= -8, f"the chat overlaps the Debug panel by {st[5]['value']} px"
        assert st[8]["value"] <= 20, f"the chat did not go back to its corner: {st[8]['value']} px from the edge"
        assert not out.get("errors"), out.get("errors")
    finally:
        try:
            service_request(info, "POST", "/api/shutdown", {}, token="owner")
        except Exception:
            pass


def _bell_site(site: Path) -> Path:
    """A site of one Studio page (the Bell example, with the site bar) and the Debug section."""
    from qccd.arch import load as load_arch
    from qccd.cost.models import corrected_model
    from qccd.ir.tsir import TSIR
    from qccd.site import build as B
    from qccd.site import debugpage as dp
    from qccd.verify.replay import replay
    from qccd.viz.render import render_html
    (site / "compilation" / "ex").mkdir(parents=True)
    arch, prog, model = load_arch(ARCH), TSIR.load(PROG), corrected_model()
    bell = site / "compilation" / "ex" / "bell.html"
    render_html(arch, prog, replay(prog, arch, model, check_rules=True, keep_cycles=True), model, bell)
    # the page as the site serves it: with the site bar (the loader finds the site's root from it)
    bell.write_text(B.with_nav(bell.read_text(encoding="utf-8"), 2, "compilation", "[]", app=True),
                    encoding="utf-8", newline="")
    assert dp.main(["-o", str(site), "--pages", str(site)]) == 0
    return site


@pytest.mark.skipif(not (_node() and CHROME and Path(CHROME).exists()), reason="needs node and Chrome")
def test_a_website_page_opened_with_my_agent_is_reviewed_in_its_panel(tmp_path, monkeypatch):
    """A Studio page of the website, opened through the person's workspace (`qccd web`): "Review
    with my agent" writes the request into the chat frame (another origin, by message) and does not
    send it; the agent drives the same panel with its page tool (inspect, note), and reads it."""
    if _pyodide_dir() is None:
        pytest.skip("no cached Pyodide release (out/debug-cache): a site build fetches it")
    import http.server
    import sys
    import threading
    from functools import partial
    from qccd.workspace.app import Workspace
    from qccd.workspace.runtime import ensure_service, service_request
    site = _bell_site(tmp_path / "site")
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(http.server.SimpleHTTPRequestHandler, directory=str(site)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setenv("QCCD_SITE_URL", f"http://127.0.0.1:{httpd.server_address[1]}/")
    monkeypatch.setenv("QCCD_RUNTIME_DIR", str(tmp_path / "runtime"))
    for k in ("QCCD_CODEX", "QCCD_CLAUDE", "QCCD_CURSOR"):
        monkeypatch.setenv(k, "none")
    Workspace.init(tmp_path / "ws", "ghz4@1").close()
    info = ensure_service(tmp_path / "ws", python=sys.executable)
    try:
        own = lambda m, p, b=None: service_request(info, m, p, b, token="owner", timeout=60)
        base, web = f"http://127.0.0.1:{info['port']}", f"http://127.0.0.1:{info['web_port']}"
        hdr = {"Authorization": f"Bearer {info['owner_token']}", "Content-Type": "application/json"}

        def act(action, **args):
            return {"http": {"method": "POST", "url": base + "/api/page-actions", "headers": hdr,
                             "body": {"action": action, "args": args}}}
        steps = [
            {"wait": f"location.origin === '{web}' && !!document.getElementById('qccd-chat') && !!document.getElementById('qdbgBtn')"
                     " && typeof window.QCCD_AGENT_DRAFT === 'function'", "timeout": 40000, "stopOnFail": True},        # 0
            {"frame": "/chatframe", "wait": "window.QCCD_LIVE && QCCD_LIVE.state().paired && QCCD_LIVE.state().connected"
                                            " && !!QCCD_LIVE.state().view", "timeout": 30000, "stopOnFail": True},      # 1
            {"eval": "QCCD_DEBUG.check({rules: 'standard'}).then(function (r) { return r.failing.join(','); })"},     # 2
            {"eval": "document.querySelector('#dTabs [data-tab=rules]').click(), document.getElementById('rAgent').click(), true"},  # 3
            {"frame": "/chatframe", "eval": "document.getElementById('qcl-text').value"},                               # 4
            act("debug", verb="inspect", args=[{"rule": "R7", "step": 7}]),                                             # 5
            # the chat frame stands left of the open panel, not over it
            {"eval": "Math.round(document.getElementById('qccd-chat').getBoundingClientRect().right"
                     " - document.getElementById('dbg').getBoundingClientRect().left)"},                                # 6
            act("debug", verb="note", args=[{"step": 7, "rule": "R7", "text": "cool q0 before the gate"}]),             # 7
            {"eval": "JSON.stringify(QCCD_DEBUG.notes().notes)"},                                                       # 8
            act("read", controls=False),                                                                                # 9
        ]
        code = own("POST", "/api/pair-code", {})["code"]
        spec = tmp_path / "spec.json"
        spec.write_text(json.dumps({"pages": {"a": f"{base}/open-web#pair={code}&to=/web/compilation/ex/bell.html"},
                                    "steps": steps}), encoding="utf-8")
        r = subprocess.run(["node", str(REPO / "tests" / "workspace_browser.mjs"), str(spec)], capture_output=True,
                           timeout=300, cwd=REPO)
        out = json.loads(r.stdout.decode("utf-8") or "{}")
        st = out.get("steps", [])
        diag = json.dumps({"steps": st, "logs": out.get("logs"), "fatal": out.get("fatal")})[:6000]
        assert len(st) == len(steps) and st[0]["ok"] and st[1]["ok"], diag
        assert st[2]["value"] == "R7", diag                                 # the page's own Python, through the mirror
        assert st[4]["value"].startswith("Review this program with me"), diag
        ins = json.loads(st[5]["body"])
        assert ins["ok"] and ins["result"]["result"]["failing"] == 1, diag
        assert st[6]["value"] <= -8, f"the chat overlaps the Debug panel by {st[6]['value']} px"
        notes = json.loads(st[8]["value"])
        assert notes and notes[0]["step"] == 7 and notes[0]["by"], diag   # signed with the agent's name
        rd = json.loads(st[9]["body"])
        assert "# Debug panel" in rd["result"]["text"] and "R7" in rd["result"]["text"], diag
    finally:
        httpd.shutdown()
        try:
            service_request(info, "POST", "/api/shutdown", {}, token="owner")
        except Exception:
            pass


@pytest.mark.skipif(not (_node() and CHROME and Path(CHROME).exists()), reason="needs node and Chrome")
def test_the_page_in_a_browser(tmp_path):
    """The Debug page hosting a Studio page: open, step, watch, check, crosses, report -- and the
    same under the workspace mirror's security policy."""
    if _pyodide_dir() is None:
        pytest.skip("no cached Pyodide release (out/debug-cache): a site build fetches it")
    site = _bell_site(tmp_path / "site")
    from qccd.workspace.mirror import mirror_csp
    assert 'id="qdbg-loader"' in (site / "compilation" / "ex" / "bell.html").read_text(encoding="utf-8")
    for inpage in (False, True):                  # the Debug page, then the Studio page's own Debug button
        for csp in (None, mirror_csp("http://127.0.0.1:47101")):
            args = ["node", str(REPO / "tests" / "debug_page.mjs"), str(site), "compilation/ex/bell.html"]
            if csp:
                args += ["--csp", csp]
            if inpage:
                args += ["--inpage"]
            r = subprocess.run(args, capture_output=True, text=True, timeout=300)
            out = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {"fails": [r.stderr[-800:]]}
            where = ("in the Studio page" if inpage else "on the Debug page") + (", under the mirror's policy" if csp else "")
            assert r.returncode == 0 and not out["fails"], f"{where}: {json.dumps(out)}"
            assert "A1" in out["failing"] and out["crosses"] > 0, where
            assert inpage or out["steps"] == 11
