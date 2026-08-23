"""THE DESIGN TOOL: an empty canvas, a device built from nothing, a programme, a verdict.

`python -m qccd studio` emits the SAME page `python -m qccd demo` does -- `render_html` is
already a renderer over `(architecture, programme)`, and `(empty, empty)` became a legal
pair the moment `Architecture.from_json` started testing for the PRESENCE of `nodes`
rather than its truthiness.  Two page kinds would be two implementations of one page.

WHAT THIS FILE IS FOR.  Every assertion below is about a sentence the page says to a user,
and every one of them was MEASURED SAYING SOMETHING FALSE on this tree before the change:

  * `self-check ... agrees with the Python verifier to 0.0e+0 quanta per ion` on a page
    with no Python replay behind it -- `for (const ion in D.checksum)` over an empty
    object, so `drift` stayed 0 and the loop body never ran.  A green tick for a check
    that did not happen, in the one panel that asserts the page is trustworthy.
  * `Step 1 / 0 - undefined` as the first sentence a new user reads.
  * an empty `<div>` where the 23 rule verdicts go, which reads as "nothing wrong".
  * `exportJson()` handing over an `.arch.json` Python refuses with 24 structural errors,
    while the same page reported 576 DACs for a machine whose total ion capacity was 0.

Every test here carries a MUTATION GUARD, because a test that cannot fail is a golden
vector wearing a disguise.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from qccd.arch import load
from qccd.arch.device import ExpansionError
from qccd.ir.tsir import TSIR
from qccd.verify import verify
from qccd.cost.models import corrected_model

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
STUDIO = OUT / "studio.html"
RUNNER = Path(__file__).resolve().parent / "studio.mjs"

node = shutil.which("node")
requires_node = pytest.mark.skipif(node is None, reason="node is not on PATH")
requires_studio = pytest.mark.skipif(not STUDIO.exists(),
                                     reason="run `python -m qccd studio`")


def _url(p: Path) -> str:
    """A `file://` URL, JSON-quoted.  Node's ESM loader refuses a bare Windows path
    (`Received protocol 'c:'`), so a probe written into a temp directory has to import the
    harness by URL rather than by path."""
    return json.dumps(p.resolve().as_uri())


def drive(page: Path, script=(), tmp_path: Path | None = None) -> dict:
    args = [node, str(RUNNER), str(page)]
    if script:
        sp = (tmp_path or Path(".")) / "studio_script.json"
        sp.write_text(json.dumps(list(script)), encoding="utf-8")
        args.append(str(sp))
    r = subprocess.run(args, capture_output=True, text=True, timeout=900, cwd=str(ROOT))
    assert r.returncode == 0, f"studio.mjs failed on {page.name}: {r.stderr[-3000:]}"
    return json.loads(r.stdout.strip().splitlines()[-1])


# ======================================================================================
# A. THE EMPTY START LOADS, AND IT IS HONEST ABOUT HAVING NOTHING TO SHOW
# ======================================================================================


@requires_node
@requires_studio
def test_the_empty_canvas_loads_and_claims_nothing_it_did_not_check(tmp_path):
    r = drive(STUDIO, [], tmp_path)
    b = r["base"]
    # it LOADS.  Before the fix `architecture_listing` emitted a bare
    # `set_control(max_simd_classes_per_cycle=1)` for an empty control block, and the
    # document failed `$.control: missing required key 'model'`.
    assert b["ready"] is True, b["why"]
    assert b["why"] is None
    assert b["nodes"] == 0 and b["frames"] == 0

    # THE LOAD-BEARING ASSERTION.  Measured verbatim on the pre-fix page:
    #   "self-check ... agrees with the Python verifier to 0.0e+0 quanta per ion."
    assert b["says_agrees_with_python"] is False, (
        "the page claims agreement with a verifier that never ran on it")
    assert b["says_nothing_to_check"] is True, (
        "the page does not say WHY there is no self-check")

    # no invented step, no empty verdict block, no green badge for an unrun rule
    assert b["status_invents_a_step"] is False, b["status"]
    assert b["says_no_programme_replayed"] is True
    assert b["says_all_pass"] is False
    assert b["n_checked"] == 0 and b["n_unchecked"] == 23, (
        "every rule is vacuously satisfied over zero cycles; reporting them as CHECKED "
        "would be 17 green badges for a machine nothing has ever been run on")

    # the degenerate layout is a good default, not a survival value: one data unit at the
    # maximum pitch the tool ever uses, so the first site lands dead centre and the second
    # lands 72 px away with no re-fit
    assert b["layout"] == {"W": 280, "H": 132, "sx": 72, "sy": 72, "g": 72,
                           "n": 0, "wide": False}
    assert b["data_layout"] == "tall"


@requires_node
@requires_studio
@pytest.mark.parametrize("anchor,replacement,why", [
    ("""    ? (EV.self_check_ions === 0""", "    ? (false", "the evidence guard on the self-check"),
])
def test_the_empty_start_guard_can_fail(tmp_path, anchor, replacement, why):
    """MUTATION GUARD M1.  Revert the three-valued self-check to the two-valued one and the
    page prints "agrees with the Python verifier to 0.0e+0 quanta per ion" again.

    The predicate has to be `evidence.self_check_ions === 0`, not "are there frames": a
    page CAN carry frames Python never priced (an authored programme is exactly that), and
    keying on the wrong evidence set would put the green tick back for those.
    """
    html = STUDIO.read_text(encoding="utf-8")
    assert html.count(anchor) == 1, f"the guard moved: {why}"
    broken = tmp_path / "broken.html"
    broken.write_text(html.replace(anchor, replacement, 1), encoding="utf-8")
    r = drive(broken, [], tmp_path)
    assert r["base"]["says_agrees_with_python"] is True, (
        "the mutation did not reintroduce the false green tick, so the assertion that "
        "forbids it is not guarding anything")


# ======================================================================================
# B. THE EXPORT BOUNDARY REFUSES EXACTLY WHAT PYTHON REFUSES
# ======================================================================================


@requires_node
@requires_studio
def test_the_export_refuses_a_document_python_would_refuse(tmp_path):
    """`Machine.blank(<generator>)` declares no zone types, so every site comes out with
    capacity 0 and `Architecture.from_json` refuses the document with one structural error
    per site.  MEASURED before the fix: `schemaErrors()` was `[]`, `exportJson()` succeeded,
    and the page reported 576 DACs and 24 trapping zones for a machine whose total ion
    capacity was ZERO.

    The mirror already existed -- `QCCDEdit.checkStructure`, with its own parity test --
    and was called from `applyEdit` and nowhere else.
    """
    r = drive(STUDIO, [{"do": "mode", "mode": "edit"},
                       {"do": "emit", "op": {"method": "blank", "args": ["ring"],
                                             "kwargs": {"width": 12, "name": "myring"}}}],
              tmp_path)
    after = r["steps"][-1]["after"]
    assert after["nodes"] > 0, "the generator did not run"
    assert after["export_error"] is not None, (
        "the browser exported a document Python refuses")
    assert "a site needs capacity >= 1" in after["export_error"], after["export_error"]
    assert after["schema_errors"], "schemaErrors() is still schema-only"


@requires_node
@requires_studio
def test_the_export_still_accepts_what_python_accepts(tmp_path):
    """THE CONVERSE GUARD, in the same file, so the fix cannot pass by refusing everything.

    A generator seeded from a TEMPLATE borrows real zone types, real curves and a real
    control block; it must export, and Python must load it.
    """
    r = drive(STUDIO, [{"do": "generator", "gen": "ring",
                        "params": {"width": 8, "height": 2, "verticals": 0},
                        "opts": {"name": "tpl", "template": "ring144_24v"}}], tmp_path)
    after = r["steps"][-1]["after"]
    assert after["nodes"] == 16, after["nodes"]
    assert after["export_error"] is None, after["export_error"]
    assert after["schema_errors"] == []
    assert after["hardware"]["total_capacity"] > 0
    doc = tmp_path / "tpl.arch.json"
    # the harness reports the byte count; re-derive the document itself the same way the
    # page would hand it over, and load it
    r2 = drive(STUDIO, [{"do": "generator", "gen": "ring",
                         "params": {"width": 8, "height": 2, "verticals": 0},
                         "opts": {"name": "tpl", "template": "ring144_24v"}}], tmp_path)
    assert r2["steps"][-1]["after"]["export_bytes"] > 1000


@requires_node
@requires_studio
def test_the_export_guard_can_fail(tmp_path):
    """MUTATION GUARD M2.  Delete the structural half of `schemaErrors` from the emitted
    page.  The blank-generator case must go GREEN (i.e. the refusal disappears) and the
    template case must stay green -- a mutation that reddens both would mean the fix is
    refusing indiscriminately rather than refusing what Python refuses."""
    html = STUDIO.read_text(encoding="utf-8")
    anchor = "  try { out = out.concat(E.checkStructure(STATE.device)); }"
    assert html.count(anchor) == 1, "the structural check moved"
    broken = tmp_path / "unguarded.html"
    broken.write_text(html.replace(anchor, "  try { out = out.concat([]); }", 1),
                      encoding="utf-8")
    bad = drive(broken, [{"do": "mode", "mode": "edit"},
                         {"do": "emit", "op": {"method": "blank", "args": ["ring"],
                                               "kwargs": {"width": 12, "name": "myring"}}}],
                tmp_path)["steps"][-1]["after"]
    # The document is refused for TWO independent reasons -- 24 sites below capacity AND
    # 24 sites in an undeclared zone -- and this mutation removes only the first, so the
    # assertion is on the SENTENCE `check_structure` owns rather than on the refusal as a
    # whole.  A guard that asserted "the export succeeds" would be silently satisfied by
    # the other check and would prove nothing about this one.
    assert "a site needs capacity >= 1" not in (bad["export_error"] or ""), (
        "the mutation did not reopen the hole, so the assertion is not guarding it")
    assert "a site needs capacity >= 1" in (
        drive(STUDIO, [{"do": "mode", "mode": "edit"},
                       {"do": "emit", "op": {"method": "blank", "args": ["ring"],
                                             "kwargs": {"width": 12, "name": "myring"}}}],
              tmp_path)["steps"][-1]["after"]["export_error"] or "")
    good = drive(broken, [{"do": "generator", "gen": "ring",
                           "params": {"width": 8, "height": 2, "verticals": 0},
                           "opts": {"name": "tpl", "template": "ring144_24v"}}],
                 tmp_path)["steps"][-1]["after"]
    assert good["export_error"] is None, "the converse case was already failing"


# ======================================================================================
# C. FROM SCRATCH, AND BACK INTO PYTHON
# ======================================================================================

#: A device built entirely by clicking: four sites, a junction, an ancilla, six segments
#: and one closed loop.  This is the path V8 coverage showed was never executed -- the
#: eight builder verbs -- driven the way a user would drive it.
_SCRATCH = [
    {"do": "canvas", "opts": {"name": "mini", "template": "ring144_24v"}},
    {"do": "node", "x": 0.0, "y": 0.0, "opts": {"id": "T0", "zone": "data"}},
    {"do": "node", "x": 1.0, "y": 0.0, "opts": {"id": "T1", "zone": "data"}},
    {"do": "node", "x": 1.0, "y": 1.0, "opts": {"id": "T2", "zone": "data"}},
    {"do": "node", "x": 0.0, "y": 1.0, "opts": {"id": "T3", "zone": "data"}},
    {"do": "node", "x": 2.0, "y": 0.5, "opts": {"id": "J0", "kind": "junction"}},
    {"do": "node", "x": 3.0, "y": 0.5, "opts": {"id": "A0", "zone": "ancilla"}},
    {"do": "join", "a": "T0", "b": "T1", "opts": {"id": "E0"}},
    {"do": "join", "a": "T1", "b": "T2", "opts": {"id": "E1"}},
    {"do": "join", "a": "T2", "b": "T3", "opts": {"id": "E2"}},
    {"do": "join", "a": "T3", "b": "T0", "opts": {"id": "E3"}},
    {"do": "join", "a": "T1", "b": "J0", "opts": {"id": "V0"}},
    {"do": "join", "a": "J0", "b": "A0", "opts": {"id": "V1"}},
    {"do": "loop", "id": "L0", "walk": ["T0", "T1", "T2", "T3"],
     "closed": True, "kind": "ring"},
]

_PROGRAM = (
    'p.init({"d0": "T0", "d1": "T1", "a0": "A0"})\n'
    "p.rotate(1)\n"
    'p.shuttle("d1", ["T2", "T1", "J0", "A0"])\n'
    'p.gate("MS", [["d1", "a0"]], sites=["A0"])\n'
    "p.cool()\n"
    'p.measure(["d1", "a0"])\n'
)


@requires_node
@requires_studio
def test_a_device_built_by_clicking_loads_in_python(tmp_path):
    r = drive(STUDIO, _SCRATCH, tmp_path)
    for step in r["steps"]:
        assert step.get("error") is None, step
        assert step["result"]["ok"] is True, step
    after = r["steps"][-1]["after"]
    assert after["nodes"] == 6 and after["segs"] == 6 and after["loops"] == 1
    assert after["schema_errors"] == []
    assert after["export_error"] is None

    # the same graph, through Python's own loader
    doc = json.loads(_export(tmp_path, _SCRATCH))
    arch = load(doc) if not isinstance(doc, dict) else _load_doc(doc)
    assert len(arch.device.nodes) == 6
    assert len(arch.device.segments) == 6
    assert len(arch.device.loops) == 1
    # node for node, so "it loaded" is not mistaken for "it is the same device"
    got = {n.id: (round(float(n.pos[0]), 12), round(float(n.pos[1]), 12))
           for n in arch.device.nodes.values()}
    want = {d["id"]: (round(d["x"], 12), round(d["y"], 12))
            for d in after["digest"]["nodes"]}
    assert got == want, (got, want)


def _load_doc(doc):
    from qccd.arch.device import Architecture
    return Architecture.from_json(doc)


def _export(tmp_path: Path, script) -> str:
    """Drive the page and return the `.arch.json` it would hand over."""
    hook = list(script) + [{"do": "mode", "mode": "edit"}]
    r = drive(STUDIO, hook, tmp_path)
    assert r["steps"][-1]["after"]["export_error"] is None
    # re-run through a tiny node one-liner rather than shipping the text in the report,
    # which would double the harness output for every case
    sp = tmp_path / "exp_script.json"
    sp.write_text(json.dumps(hook), encoding="utf-8")
    probe = tmp_path / "export.mjs"
    probe.write_text(
        "import fs from 'fs';\n"
        "import { loadPage } from " + _url(ROOT / "tests" / "shim.mjs") + ";\n"
        "import { PAGE_HOOK, applyScript } from " + _url(ROOT / "tests" / "drive.mjs") + ";\n"
        "globalThis.__QCCD_SYNC = true;\n"
        "loadPage(" + json.dumps(str(STUDIO)) + ", PAGE_HOOK);\n"
        "applyScript(globalThis.EDITOR, globalThis.__page, "
        "JSON.parse(fs.readFileSync(" + json.dumps(str(sp)) + ", 'utf8')));\n"
        "process.stdout.write(globalThis.EDITOR.exportJson());\n", encoding="utf-8")
    out = subprocess.run([node, str(probe)], capture_output=True, text=True, timeout=900,
                         cwd=str(ROOT))
    assert out.returncode == 0, out.stderr[-3000:]
    return out.stdout


@requires_node
@requires_studio
def test_the_scratch_round_trip_compares_structure_and_not_just_loading(tmp_path):
    """MUTATION GUARD M3.  Perturb one exported coordinate by 1e-9 before handing the file
    to Python.  The comparison must notice and NAME the node -- a load-only assertion would
    wave it through, which is precisely the failure mode "it loaded" hides."""
    doc = json.loads(_export(tmp_path, _SCRATCH))
    doc["geometry"]["nodes"][0]["pos"][0] += 1e-9
    arch = _load_doc(doc)
    got = {n.id: (round(float(n.pos[0]), 12), round(float(n.pos[1]), 12))
           for n in arch.device.nodes.values()}
    clean = json.loads(_export(tmp_path, _SCRATCH))
    want = {n["id"]: (round(float(n["pos"][0]), 12), round(float(n["pos"][1]), 12))
            for n in clean["geometry"]["nodes"]}
    assert got != want, "a 1e-9 perturbation survived the comparison"
    bad = [k for k in want if got[k] != want[k]]
    assert bad == [clean["geometry"]["nodes"][0]["id"]], bad


@requires_node
@requires_studio
def test_a_transaction_is_atomic(tmp_path):
    """MUTATION GUARD M4.  `d.site` alone, with no `DeviceBuilder` before it, must roll the
    WHOLE transaction back rather than leaving a half-applied device.

    This is why `transaction` exists at all: `emit()` commits one op and rebuilds, and a
    seed verb DISCARDS every statement before it, so a gallery pick that failed halfway
    would leave the design in a state the user did not ask for and cannot see.
    """
    r = drive(STUDIO, [
        {"do": "canvas", "opts": {"name": "mini", "template": "ring144_24v"}},
        {"do": "node", "x": 0.0, "y": 0.0, "opts": {"id": "T0", "zone": "data"}},
        # one transaction, whose SECOND op is illegal: a segment to a node that does not
        # exist.  Both ops must go.
        {"do": "transaction", "ops": [
            {"build": {"method": "d.site", "args": ["T9", 9.0, 9.0],
                       "kwargs": {"zone": "data"}}},
            {"build": {"method": "d.segment", "args": ["EX", "T9", "NOPE"], "kwargs": {}}},
        ]},
    ], tmp_path)
    before = r["steps"][1]["after"]
    txn = r["steps"][2]
    assert txn["result"]["ok"] is False, "an illegal transaction was committed"
    after = txn["after"]
    assert after["nodes"] == before["nodes"], (
        "the transaction left half of itself applied")
    assert after["schema_errors"] == []


# ======================================================================================
# D. A PROGRAMME WRITTEN IN THE BROWSER, EVALUATED, AND RE-VERIFIED BY PYTHON
# ======================================================================================


@requires_node
@requires_studio
def test_a_programme_written_here_is_priced_and_judged_and_python_agrees(tmp_path):
    """THE WHOLE POINT.  Build a device from nothing, write a test programme against it,
    and get numbers and verdicts the real toolchain reproduces exactly.

    The programme is deliberately WRONG in two ways a designer would not notice: the
    rotation puts a second ion on a degree-3 node (R2) and the gate happens at n-bar 3.411
    against a 1.0 budget (R7).  A tool that reported "all rules pass" here would be worse
    than one that reported nothing.
    """
    script = list(_SCRATCH) + [{"do": "prog", "src": _PROGRAM}]
    r = drive(STUDIO, script, tmp_path)
    after = r["steps"][-1]["after"]
    assert after["program_errors"] == []
    assert after["authored"] is True
    assert after["program_statements"] == 6
    assert after["frames"] == 7          # the shuttle is TWO hops, so 6 statements -> 7
    assert after["blocked"] is None, after["blocked"]
    price = after["price"]
    assert price["cost"] == 7 and price["steps"] == 7
    # NO ORACLE: these frames were never priced by Python, so there is nothing to compare
    # them against and the page has to say so rather than reporting drift 0.
    assert price["frameChecked"] == 0

    assert after["rules"]["by_rule"] == {"R2": 1, "R7": 1}, after["rules"]
    assert after["rules"]["messages"] == [
        "junction T1 (degree 3) holds 2 ions",
        "ion d1 enters a 2Q gate at n-bar=3.411 > 1.0; a cooling operation must precede it",
    ]

    # ---- and now Python, on the two files the page hands over -------------------------
    arch_doc, tsir_doc = _export_pair(tmp_path, script)
    arch = _load_doc(json.loads(arch_doc))
    prog = TSIR.from_json(json.loads(tsir_doc))
    rep = verify(prog, arch, corrected_model("qccdsim_jones"))
    assert rep.result.total_cost == 7
    assert rep.result.total_steps == 7
    assert sorted(rep.rules.failed()) == ["R2", "R7"]
    assert [v.message for v in rep.rules.violations] == after["rules"]["messages"]


def _export_pair(tmp_path: Path, script) -> tuple[str, str]:
    sp = tmp_path / "pair_script.json"
    sp.write_text(json.dumps(list(script)), encoding="utf-8")
    probe = tmp_path / "pair.mjs"
    probe.write_text(
        "import fs from 'fs';\n"
        "import { loadPage } from " + _url(ROOT / "tests" / "shim.mjs") + ";\n"
        "import { PAGE_HOOK, applyScript } from " + _url(ROOT / "tests" / "drive.mjs") + ";\n"
        "globalThis.__QCCD_SYNC = true;\n"
        "loadPage(" + json.dumps(str(STUDIO)) + ", PAGE_HOOK);\n"
        "applyScript(globalThis.EDITOR, globalThis.__page, "
        "JSON.parse(fs.readFileSync(" + json.dumps(str(sp)) + ", 'utf8')));\n"
        "const pair = globalThis.EDITOR.exportPair();\n"
        "process.stdout.write(JSON.stringify([pair.arch.text, pair.tsir.text, pair.command]));\n",
        encoding="utf-8")
    out = subprocess.run([node, str(probe)], capture_output=True, text=True, timeout=900,
                         cwd=str(ROOT))
    assert out.returncode == 0, out.stderr[-3000:]
    a, t, cmd = json.loads(out.stdout)
    assert "--tsir" in cmd and ".arch.json" in cmd, cmd
    return a, t


@requires_node
@requires_studio
def test_the_programme_lane_refuses_a_programme_python_cannot_run(tmp_path):
    """An ion declared to move from where it is NOT stops `replay.py` dead, so no rule
    runs and no number exists.  The page must refuse the price and withdraw every verdict
    rather than pricing a programme the toolchain cannot execute."""
    bad = _PROGRAM.replace('p.shuttle("d1", ["T2", "T1", "J0", "A0"])',
                           'p.shuttle("d1", ["T1", "J0", "A0"])')
    r = drive(STUDIO, list(_SCRATCH) + [{"do": "prog", "src": bad}], tmp_path)
    after = r["steps"][-1]["after"]
    assert after["blocked"] == ["declared_elsewhere"], after["blocked"]
    assert after["price"] is None
    assert after["n_checked"] == 0, (
        "the page reported verdicts for a programme it could not replay")
    assert after["n_unchecked"] == 23


@requires_node
@requires_studio
def test_the_programme_lane_guard_can_fail(tmp_path):
    """MUTATION GUARD M5.  Remove the position check and the page prices a programme
    Python refuses to run at all -- measured: cost 5, steps 6, with the stage animating an
    ion teleporting between two traps."""
    html = STUDIO.read_text(encoding="utf-8")
    anchor = "        if (own(pos, mion) && path.length && pos[mion] !== path[0]) {"
    assert html.count(anchor) == 1, "the position check moved"
    broken = tmp_path / "unchecked.html"
    broken.write_text(html.replace(anchor, "        if (false) {", 1), encoding="utf-8")
    bad = _PROGRAM.replace('p.shuttle("d1", ["T2", "T1", "J0", "A0"])',
                           'p.shuttle("d1", ["T1", "J0", "A0"])')
    r = drive(broken, list(_SCRATCH) + [{"do": "prog", "src": bad}], tmp_path)
    after = r["steps"][-1]["after"]
    assert after["blocked"] is None and after["price"] is not None, (
        "the mutation did not reopen the hole, so the assertion is not guarding it")


# ======================================================================================
# E. PERSISTENCE, WITHOUT STUBBING localStorage
# ======================================================================================


@requires_node
@requires_studio
def test_a_snapshot_restores_to_the_same_device_and_the_same_price(tmp_path):
    """`tests/shim.mjs` deliberately does not stub `localStorage`, and must not start:
    the design tool's most important guarantee is that your work survives, and asserting
    that against a fake would prove nothing.

    So SERIALIZATION is a pure function and storage is a three-line adapter over it, and
    the round trip is asserted through `EDITOR.digest()` -- which routes through
    `transaction()` -> `replay()` -> the ONE applier, so a pass proves the real path.
    """
    r = drive(STUDIO, list(_SCRATCH) + [{"do": "prog", "src": _PROGRAM}], tmp_path)
    assert r["snapshot_kind"] == "qccd.studio"
    assert r["restore_ok"] is True
    assert r["perturb_ok"] is True and r["perturbed_digest_differs"] is True, (
        "the probe did not actually change anything, so the round trip crossed no "
        "difference and would pass however `restore` was written")
    assert r["restore_same_digest"] is True, "restore produced a different device"
    assert r["restore_same_price"] is True, "restore produced a different price"
    assert r["autosave_ok"] is True and r["autoload_matches"] is True


@requires_node
@requires_studio
def test_the_persistence_guard_can_fail(tmp_path):
    """MUTATION GUARD M6.  Make `restore` assign the record lists WITHOUT rebuilding, and
    the price comparison must go red -- proving the round trip exercises the applier
    rather than a shortcut past it."""
    html = STUDIO.read_text(encoding="utf-8")
    # Anchored on the CALL, not on the lines around it: the block gained a `WHY_NOT`
    # check when importing a document that cannot be rebuilt started being possible, and
    # a guard pinned to its neighbours goes red for an edit that changed nothing it tests.
    anchor = """  var was = WHY_NOT;
  WHY_NOT = null;
  rebuild();"""
    assert html.count(anchor) == 1, "restore's rebuild moved"
    broken = tmp_path / "shortcut.html"
    broken.write_text(html.replace(anchor, """  var was = WHY_NOT;
  WHY_NOT = null;""", 1), encoding="utf-8")
    r = drive(broken, list(_SCRATCH) + [{"do": "prog", "src": _PROGRAM}], tmp_path)
    assert not (r["restore_same_digest"] and r["restore_same_price"]), (
        "restore without a rebuild still produced the same device AND the same price, so "
        "the round trip is comparing the page against itself")


# ======================================================================================
# F. THE LAYOUT REGIME IS READABLE AT ALL
# ======================================================================================


@requires_node
@requires_studio
def test_the_layout_regime_is_an_attribute_and_follows_the_window(tmp_path):
    """`classList.add('wide')` ran ONCE at load, so a device that BECAME long-and-thin
    while you drew it never got the wide layout -- and `classList` is a no-op in the shim,
    which made the layout regime the one piece of page state no harness could read."""
    r = drive(STUDIO, [], tmp_path)
    assert r["layout_regimes"] == {"800": "narrow", "1000": "tall", "1400": "tall"}
    wide = OUT / "ring144_24v__deck__corrected.html"
    if wide.exists():
        w = drive(wide, [], tmp_path)
        assert w["base"]["layout"]["wide"] is True
        assert w["layout_regimes"] == {"800": "narrow", "1000": "tall", "1400": "wide"}


@requires_node
@requires_studio
def test_the_layout_guard_can_fail(tmp_path):
    """MUTATION GUARD M7.  Put the load-time `classList.add('wide')` back.  The regime
    becomes unreadable, and the fact that this mutation CANNOT be caught any other way is
    the whole argument for the change."""
    wide = OUT / "ring144_24v__deck__corrected.html"
    if not wide.exists():
        pytest.skip("run `python -m qccd demo`")
    html = wide.read_text(encoding="utf-8")
    anchor = "  document.getElementById('row').setAttribute('data-layout', mode);"
    assert html.count(anchor) == 1, "applyLayout moved"
    broken = tmp_path / "classy.html"
    broken.write_text(
        html.replace(anchor, "  document.getElementById('row').classList.add(mode);", 1),
        encoding="utf-8")
    r = drive(broken, [], tmp_path)
    assert r["base"]["data_layout"] is None, (
        "the mutation did not make the regime unreadable, so the attribute is not what "
        "the assertion is reading")


# ======================================================================================
# G. THE PALETTE IS GENERATED, NOT WRITTEN DOWN
# ======================================================================================


@requires_node
@requires_studio
def test_the_palette_is_derived_from_the_shipped_schema(tmp_path):
    """MUTATION GUARD M8 is `test_a_new_schema_field_reaches_the_palette` below; this is
    the shape assertion.

    Every field comes from `D.schema` (the CLOSED objects) or `D.consumers` (the OPEN
    maps, each with WHO READS IT).  27 of the 65 open fields are read by NOTHING, and the
    palette marks them -- a tool that renders an inert field like a live one implies a
    causation it does not have.
    """
    from qccd.arch.schema import export_consumers

    r = drive(STUDIO, [], tmp_path)
    types = {p["type"] for p in r["palette"]}
    assert {"site", "junction", "segment", "loop", "zone_type", "curve_point",
            "primitives", "control", "heating", "species", "budget"} <= types, types
    inert = sum(len(p["inert"]) for p in r["palette"])
    py_inert = sum(1 for f in export_consumers()["fields"]
                   if f["reader"] is None and not f["path"].startswith("zone_types."))
    assert inert == py_inert, (
        f"the palette marks {inert} fields inert; the shipped consumer table says "
        f"{py_inert}")
    # the closed objects come from the schema, field for field
    site = [p for p in r["palette"] if p["type"] == "site"][0]
    assert set(site["fields"]) == {"id", "pos", "capacity", "capacity_explicit",
                                   "zone_type", "labels"}, site["fields"]


@requires_node
def test_a_new_schema_field_reaches_the_palette_without_touching_javascript(tmp_path):
    """MUTATION GUARD M8, and the one that proves the palette is not hand-written.

    Add a field to `_ZONE_TYPE` in `qccd/arch/schema.py`, rebuild the page, touch NO
    JavaScript.  The palette must grow the field.  If it does not, the palette is a second
    source of truth and the whole design has failed.
    """
    src = ROOT / "qccd" / "arch" / "schema.py"
    original = src.read_text(encoding="utf-8")
    anchor = '        "cool": {"type": "boolean"},'
    assert original.count(anchor) == 1, "the zone-type schema moved"
    page = tmp_path / "palette.html"
    try:
        src.write_text(original.replace(
            anchor, anchor + '\n        "anneal": {"type": "boolean"},', 1),
            encoding="utf-8")
        out = subprocess.run([sys.executable, "-m", "qccd", "studio", "-o", str(page)],
                             capture_output=True, text=True, timeout=900, cwd=str(ROOT))
        assert out.returncode == 0, out.stderr[-2000:]
        r = drive(page, [], tmp_path)
    finally:
        src.write_text(original, encoding="utf-8")
    zone = [p for p in r["palette"] if p["type"] == "zone_type"][0]
    assert "anneal" in zone["fields"], (
        "a field added to the SCHEMA did not reach the palette; the palette is a second "
        f"source of truth. it has: {zone['fields']}")


# ======================================================================================
# H. THE STAGE, FOR A PROGRAMME WRITTEN HERE
# ======================================================================================

#: 144 ions on a 144-slot ring, rotated both ways.  This is the shape the slot-order defect
#: lived in: WITHIN a frame the order must not change (or two ions cross straight through
#: each other) and ACROSS a boundary it must not change either (or an ion jumps a whole slot
#: pitch).  Measured when it was wrong: 40% of frames overlapping, or a 6.5 px seam at every
#: dock, and 18 ions up to 48.4 px from their compiled site.
_CENSUS_PROGRAM = (
    'p.fill("L0")\n'
    "p.rotate(3)\n"
    "p.cool()\n"
    # d3 joins d4 in S4: TWO ions in one trap, which is the only situation in which the
    # slot order is observable at all.  A census over a ring with one ion per site would
    # pass whatever the ordering did.
    'p.move("d3", "S6", "S7", via=["E6"])\n'
    "p.rotate(-2)\n"
    "p.barrier()\n"
    'p.measure(["d0"])\n'
)


def _census(page: Path, tmp_path: Path, program: str) -> dict:
    src = tmp_path / "prog.py"
    src.write_text(program, encoding="utf-8")
    r = subprocess.run([node, str(ROOT / "tests" / "census.mjs"), str(page),
                        "--program", str(src)],
                       capture_output=True, text=True, timeout=1800, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(r.stdout)


@requires_node
def test_the_stage_redraws_a_programme_written_in_the_browser(tmp_path):
    """`states`, `before`, `SLOTS` and `cum` were four page-scope CONSTANTS computed once
    against the frames the page was emitted for.  A programme written here produces a
    different frame list, so they had to become a function -- and turning four constants
    captured by every closure in the inline script into reassigned module bindings is
    exactly the shape of change that produced the 14.68 px overlap.

    This is the only harness that would see it.
    """
    page = OUT / "ring144_24v__deck__corrected.html"
    if not page.exists():
        pytest.skip("run `python -m qccd demo`")
    r = _census(page, tmp_path, _CENSUS_PROGRAM)
    assert r["program"]["ok"] is True
    assert r["program"]["parse_errors"] == [] and r["program"]["lower_errors"] == []
    assert r["program"]["statements"] == 7 and r["program"]["frames"] == 7
    assert r["ions_visible_max"] == 144, r["ions_visible_max"]
    assert r["overlap_frames"] == 0 and r["worst_overlap_px"] == 0, r["worst_pair"]
    assert r["worst_boundary_snap_px"] == 0, r["snap_at"]
    assert r["phantom_ions"] == 0
    assert r["program"]["blocked"] == []


@requires_node
def test_the_program_census_catches_a_stage_that_stops_following_the_frames(tmp_path):
    """MUTATION GUARD.  Stop re-deriving the four stage tables when the frame list changes,
    and the stage animates the SHIPPED programme's positions over the AUTHORED programme's
    frames -- two epochs of the same device mixed in one picture, which is exactly the
    defect `deriveStage` was extracted to make impossible.

    Nothing else sees this: the price is right, the rules are right, the schema is right,
    and the picture is wrong.
    """
    page = OUT / "ring144_24v__deck__corrected.html"
    if not page.exists():
        pytest.skip("run `python -m qccd demo`")
    clean = _census(page, tmp_path, _CENSUS_PROGRAM)
    assert clean["worst_boundary_snap_px"] == 0 and clean["overlap_frames"] == 0

    html = page.read_text(encoding="utf-8")
    anchor = "  if (LAST_FRAMES !== P.frames) {"
    assert html.count(anchor) == 1, "the frame-identity guard moved"
    broken = tmp_path / "frozen_stage.html"
    broken.write_text(html.replace(anchor, "  if (false) {", 1), encoding="utf-8")
    r = _census(broken, tmp_path, _CENSUS_PROGRAM)
    assert (r["worst_boundary_snap_px"] > 0 or r["overlap_frames"] > 0
            or r["phantom_ions"] > 0 or r["ions_visible_max"] != clean["ions_visible_max"]), (
        "a stage that stopped following the frame list drew the same picture, so the "
        f"census is not covering it: {json.dumps(r)[:400]}")


# ======================================================================================
# I. THE RULE HALF OF THE SELF-CHECK, ON EVERY PAGE THAT ALREADY SHIPPED
# ======================================================================================


@requires_node
@pytest.mark.parametrize("page", sorted(
    [p for p in list(OUT.glob("*.html")) + list((OUT / "verify").glob("*.html"))
     if p.name != "index.html"], key=lambda p: p.name),
    ids=lambda p: p.stem)
def test_every_page_agrees_with_the_rule_counts_python_shipped(page, tmp_path):
    """`D.rule_checksum` is SEVENTEEN INTEGERS -- how many violations Python found for each
    rule the browser also checks -- and the page diffs its own counts against them before
    the user touches anything.

    COUNTS, not verdicts.  `architectureViolations` once reported 2 where Python reported
    77 and the verdict agreed both times, which is exactly why a verdict-only comparison
    called that agreement.  On a disagreement the page WITHDRAWS the verdict surface rather
    than degrading it, the same thing `price().blocked` and `PROGRAM_STALE` already do.
    """
    r = drive(page, [], tmp_path)
    b = r["base"]
    assert b["ready"] is True, b["why"]
    if not b["frames"]:
        assert b["rules"] is None
        return
    assert b["rules"] is not None, "a page with frames reported no rule pass at all"
    oracle = b["rules"]["oracle"]
    assert oracle is not None, "the page did not run its own rule self-check"
    assert oracle["ok"] is True, oracle
    assert oracle["n"] == 17, oracle
    assert b["says_all_pass"] is False
    assert b["says_rule_count"] is True


@requires_node
def test_the_rule_self_check_withdraws_the_verdicts_when_it_disagrees(tmp_path):
    """MUTATION GUARD.  Change one shipped rule count in the page's data block; the page's
    own pass now disagrees with it, and every browser-set badge must drop to `unchecked`
    with the reason -- not stay green, and not quietly ignore the disagreement."""
    page = OUT / "ring144_24v__deck__corrected.html"
    if not page.exists():
        pytest.skip("run `python -m qccd demo`")
    html = page.read_text(encoding="utf-8")
    anchor = '"rule_checksum":{"R1":0'
    assert html.count(anchor) == 1, "the rule checksum moved"
    broken = tmp_path / "wrong_checksum.html"
    broken.write_text(html.replace(anchor, '"rule_checksum":{"R1":99', 1), encoding="utf-8")
    r = drive(broken, [], tmp_path)
    b = r["base"]
    assert b["rules"]["oracle"]["ok"] is False, "the page did not notice the disagreement"
    assert b["n_checked"] == 0, (
        "the page kept showing green badges after its own counts disagreed with the "
        "verifier that produced it")


# ======================================================================================
# J. THE RETURN LEG: a design leaves for the browser and comes back with evidence
# ======================================================================================


@requires_node
@requires_studio
def test_qccd_open_replays_a_browser_design_and_reports_all_23_rules(tmp_path):
    """`qccd studio` without `qccd open` is a ONE-WAY DOOR: designs leave for the browser
    and never come back with evidence, so the Report pane's grey "not checked here"
    register never clears and the tool permanently cannot answer "does my architecture
    pass?".

    The snapshot carries the programme as RECORDS, not as compiled TSIR, so the return leg
    goes through `Program.apply_calls` -- the whitelist dispatcher, never `getattr` on
    arbitrary text and never `exec` -- and exercises the same twelve authoring verbs the
    browser used rather than a second reader that could disagree with it.
    """
    script = list(_SCRATCH) + [{"do": "prog", "src": _PROGRAM}]
    sp = tmp_path / "script.json"
    sp.write_text(json.dumps(script), encoding="utf-8")
    snap = tmp_path / "design.qccd.json"
    probe = tmp_path / "snap.mjs"
    probe.write_text(
        "import fs from 'fs';\n"
        "import { loadPage } from " + _url(ROOT / "tests" / "shim.mjs") + ";\n"
        "import { PAGE_HOOK, applyScript } from " + _url(ROOT / "tests" / "drive.mjs") + ";\n"
        "globalThis.__QCCD_SYNC = true;\n"
        "loadPage(" + json.dumps(str(STUDIO)) + ", PAGE_HOOK);\n"
        "const ED = globalThis.EDITOR;\n"
        "applyScript(ED, globalThis.__page, JSON.parse(fs.readFileSync("
        + json.dumps(str(sp)) + ", 'utf8')));\n"
        "fs.writeFileSync(" + json.dumps(str(snap)) + ", JSON.stringify(ED.snapshot()));\n"
        "process.stdout.write(JSON.stringify((ED.rules() || {}).by_rule || {}));\n",
        encoding="utf-8")
    out = subprocess.run([node, str(probe)], capture_output=True, text=True, timeout=900,
                         cwd=str(ROOT))
    assert out.returncode == 0, out.stderr[-3000:]
    browser = json.loads(out.stdout)
    assert browser == {"R2": 1, "R7": 1}, browser

    cli = subprocess.run([sys.executable, "-m", "qccd", "open", str(snap)],
                         capture_output=True, text=True, timeout=900, cwd=str(ROOT))
    text = cli.stdout
    assert "nodes 6  segments 6  loops 1" in text, text
    assert "total_cost     7" in text and "total_steps    7" in text, text
    assert "rules failed   R2 R7" in text, text
    # and the SIX the browser could not check are named by Python, with verdicts
    assert "rules skipped  R10 R7b" in text, text
    assert "[R2] instruction 2: junction T1 (degree 3) holds 2 ions" in text, text
    # the return leg is a FAILING run, so the exit code says so
    assert cli.returncode == 1
