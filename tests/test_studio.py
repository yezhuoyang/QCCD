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
  * an empty `<div>` where the 27 rule verdicts go, which reads as "nothing wrong".
  * `exportJson()` handing over an `.arch.json` Python refuses with 24 structural errors,
    while the same page reported 576 DACs for a machine whose total ion capacity was 0.

Every test here carries a MUTATION GUARD, because a test that cannot fail is a golden
vector wearing a disguise.
"""

from __future__ import annotations

import json
import re
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
    assert b["n_checked"] == 0 and b["n_unchecked"] == 27, (
        "every rule is vacuously satisfied over zero cycles; reporting them as CHECKED "
        "would be 21 green badges for a machine nothing has ever been run on")

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

#: A device built entirely by clicking: four sites, a junction, a spur trap, six segments
#: and one closed loop.  This is the path V8 coverage showed was never executed -- the
#: eight builder verbs -- driven the way a user would drive it.
_SCRATCH = [
    {"do": "canvas", "opts": {"name": "mini", "template": "ring144_24v"}},
    {"do": "node", "x": 0.0, "y": 0.0, "opts": {"id": "T0", "zone": "data"}},
    {"do": "node", "x": 1.0, "y": 0.0, "opts": {"id": "T1", "zone": "data"}},
    {"do": "node", "x": 1.0, "y": 1.0, "opts": {"id": "T2", "zone": "data"}},
    {"do": "node", "x": 0.0, "y": 1.0, "opts": {"id": "T3", "zone": "data"}},
    {"do": "node", "x": 2.0, "y": 0.5, "opts": {"id": "J0", "kind": "junction"}},
    {"do": "node", "x": 3.0, "y": 0.5, "opts": {"id": "A0", "zone": "trap"}},
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

    The programme is deliberately WRONG in three ways a designer would not notice: the
    rotation puts a second ion on a degree-3 node (R2), the same rotation asks two ions to
    travel in two different lab-frame directions in one broadcast cycle (R22 -- the drawn
    ring turns a corner and its rails carry no loop name, so `hop_label` falls through to
    the axis), and the gate happens at n-bar 3.411 against a 1.0 budget (R7).  A tool that
    reported "all rules pass" here would be worse than one that reported nothing.
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

    assert after["rules"]["by_rule"] == {"R2": 1, "R7": 1, "R22": 1}, after["rules"]
    assert after["rules"]["messages"] == [
        "2 ions move in 2 different ways in one cycle (rotate_cw +x and rotate_cw +y); "
        "one cycle is one waveform, so unlike motions need 2 cycles",
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
    assert sorted(rep.rules.failed()) == ["R2", "R22", "R7"]
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
    assert after["n_unchecked"] == 27


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
    # ANCHORED ON THE CALL, with whatever `restore` does in between.  This used to be a
    # three-line literal ending in `rebuild();`, which is not the same thing: inserting
    # `refitNext();` between the WHY_NOT reset and the rebuild turned the guard red for an
    # edit that changed nothing it tests, which is exactly what the comment promised it
    # would not do.  A regex that allows intervening lines pins the claim -- restore clears
    # WHY_NOT and then rebuilds -- rather than the shape of the block on one particular day.
    block = re.compile(r"  var was = WHY_NOT;\n  WHY_NOT = null;\n"
                       r"((?:  [^\n]*\n){0,4}?)  rebuild\(\);")
    found = block.findall(html)
    assert len(found) == 1, f"restore's rebuild moved: {len(found)} match(es)"
    broken = tmp_path / "shortcut.html"
    # remove the CALL and keep everything else the block does, so the mutation is exactly
    # "assign the record lists without rebuilding" and not "delete three lines"
    broken.write_text(block.sub(lambda m: "  var was = WHY_NOT;\n  WHY_NOT = null;\n"
                                          + m.group(1), html, count=1), encoding="utf-8")
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


#: The pages `python -m qccd regen` writes under `out/` -- the same set
#: `test_engine_parity.py::_regen_pages` sweeps, named the same way.
REGEN_GLOBS = ("*__*__*.html", "studio.html", "verify/*.html")


def _pages_this_build_made() -> list[Path]:
    """The emitted pages this build OWNS, by name rather than by freshness.

    `out/` is not a directory of this build's outputs; it is where everything lands.  A
    plain glob held `out/studio_micro.html` -- 9 September, written by a manual run, nothing
    in the tree writes it -- to today's rule counts, and it failed for the honest reason
    that the browser checked 17 rules the day it was made rather than 21.

    Scoped by GLOB and not by `render.page_stamp()`, which was the first attempt and is
    subtly wrong: the stamp moves whenever anyone edits one of the five inlined files, so in
    a shared tree with another session mid-edit the filter matches nothing and the sweep
    covers zero pages while every test in it reports green.  A page in this set that is
    STALE should fail on the rule counts and say so -- that is a real finding -- but a page
    outside it was never this build's to judge.
    """
    out: list[Path] = []
    for pat in REGEN_GLOBS:
        out += [q for q in OUT.glob(pat) if q.name != "index.html"]
    return sorted(set(out), key=lambda q: q.name)


def test_the_sweep_below_actually_found_pages():
    """Anti-vacuity.  A scan that matches nothing passes every assertion in its caller and
    reports a green that means "there was nothing to look at" -- the failure mode this file
    is full of guards against, turned inward."""
    pages = _pages_this_build_made()
    assert len(pages) >= 15, (
        f"only {len(pages)} page(s) matched {REGEN_GLOBS}; run `python -m qccd regen` "
        f"-- an empty sweep is not a pass")


@requires_node
@pytest.mark.parametrize("page", _pages_this_build_made(), ids=lambda p: p.stem)
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
    assert oracle["n"] == 21, oracle
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
def test_qccd_open_replays_a_browser_design_and_reports_all_27_rules(tmp_path):
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
    assert browser == {"R2": 1, "R7": 1, "R22": 1}, browser

    cli = subprocess.run([sys.executable, "-m", "qccd", "open", str(snap)],
                         capture_output=True, text=True, timeout=900, cwd=str(ROOT))
    text = cli.stdout
    assert "nodes 6  segments 6  loops 1" in text, text
    assert "total_cost     7" in text and "total_steps    7" in text, text
    assert "rules failed   R2 R22 R7" in text, text
    # and the ones the browser could not check are named by Python, with verdicts
    assert "rules skipped  R10 R7b" in text, text
    assert "[R2] instruction 2: junction T1 (degree 3) holds 2 ions" in text, text
    # the return leg is a FAILING run, so the exit code says so
    assert cli.returncode == 1


@requires_node
@requires_studio
def test_the_programme_pane_describes_the_authored_programme_not_the_shipped_one(tmp_path):
    """After Evaluate of a two-statement programme the Program pane must describe THAT
    programme: `#pCount` counts its instructions (it read "13 / 13" -- the shipped walk --
    under a two-frame shuttle), `#pNow` never joins an authored frame to Python's listing
    (`x4 . 105 us` was the shipped instruction's width and duration, printed under a
    frame it had nothing to do with; `#undefined` was the join over no frame at all),
    and the head's denominators are the engine's totals for it.  A refused statement is a
    refusal, said with how much of the programme ran -- the button used to toast "ok".
    """
    page = OUT / "studio_grid.html"
    if not page.exists():
        pytest.skip("run `python -m qccd studio --seed arch/grid9x9.arch.json --program walk -o out/studio_grid.html`")
    good = 'p.init({"d0": "T0_0h"})\np.shuttle("d0", ["T0_0h", "J0_0", "T0_0v"])'
    cut = 'p.init({"d0": "T0_0h"})\np.shuttle("d0", ["T0_0h", "J0_0", "T4_4h"])'
    r = drive(page, [
        {"do": "evaluate", "src": good},
        {"do": "evaluate", "src": cut},
        {"do": "evaluate", "src": 'p.init({"d0":"T0_0h")'},
        {"do": "evaluate", "src": ""},
    ], tmp_path)
    base = r["base"]
    # 19, not the 13 this pinned before `qccd/compile/programs.py::walk` changed: the
    # number is the SHIPPED programme's instruction count, read off the page that was
    # regenerated from the current builder, and the claim under test is that an authored
    # programme replaces it -- not what it happens to be.
    assert base["p_count"] == "19 / 19 instructions" and base["authored"] is False

    ok = r["steps"][0]
    assert ok["result"] == {"ok": True, "parsed": True, "ran": 2, "statements": 2,
                            "frames": 2, "problems": []}, ok["result"]
    after = ok["after"]
    assert after["p_count"] == "2 / 2 instructions", after["p_count"]
    assert "#undefined" not in after["p_now"] and "105 us" not in after["p_now"], after["p_now"]
    assert after["counters"]["stepsOf"] == "/" + str(after["price"]["steps"]), after["counters"]
    assert after["counters"]["costOf"] == "/" + str(after["price"]["cost"]), after["counters"]
    # the head's chips are the Report's rows: the same price, not the shipped 140
    assert after["metrics"][:2] == ["cost=%d" % after["price"]["cost"],
                                    "steps=%d" % after["price"]["steps"]], after["metrics"]
    # (the separators are middle dots, which the console decoding here does not keep)
    words = after["lede"].split()
    assert words[0:2] == ["144", "sites"] and words[3:5] == ["288", "segments"] \
        and words[6:8] == ["2", "instructions"], after["lede"]
    assert after["toasts"][-1] == {"kind": "ok", "message": "2 statements, 2 frames"}

    # a statement the device refuses is a REFUSAL, with how much of the programme ran
    cut_step = r["steps"][1]
    assert cut_step["result"]["ok"] is False and cut_step["result"]["ran"] == 1
    t = cut_step["after"]["toasts"][-1]
    assert t["kind"] == "bad", t
    assert t["message"].startswith("statement 2: "), t
    assert t["message"].endswith("1 of 2 statements run"), t
    assert cut_step["after"]["p_count"] == "1 / 1 instructions"

    # a parse error is AT its line, in the pane and not only in a toast that fades
    parse = r["steps"][2]
    assert parse["result"]["parsed"] is False
    assert "line 1 col 21" in parse["after"]["pw_err"], parse["after"]["pw_err"]
    assert parse["after"]["frames"] == 1, "the records stand until a text parses"

    # an empty text clears the programme; the shipped one and the shipped head come back
    cleared = r["steps"][3]["after"]
    assert cleared["toasts"][-1] == {"kind": "ok", "message": "programme cleared"}
    assert cleared["authored"] is False and cleared["p_count"] == "19 / 19 instructions"
    assert cleared["counters"] == base["counters"] and cleared["lede"] == base["lede"]

    # THE BLANK PAGE'S HEADLINE is true of nothing once a site is on the stage: two stamps
    # by hand, and the lede counts them instead of saying "an empty canvas"
    blank = OUT / "studio.html"
    if blank.exists():
        rb = drive(blank, [{"do": "stamp", "type": "site", "at": [0, 0]},
                           {"do": "stamp", "type": "site", "at": [1, 0]}], tmp_path)
        assert "empty canvas" in rb["base"]["lede"], rb["base"]["lede"]
        built = rb["steps"][1]["after"]
        assert built["lede"].split()[0:2] == ["2", "sites"], built["lede"]
        assert built["doc_title"].endswith(" - design"), built["doc_title"]

    # THE CIRCUIT LANE belongs to the shipped programme: an authored frame must not be
    # joined to the compiled walk's statement map (it highlighted `h q[0]` under a
    # hand-written shuttle), and the Program pane's inline "circuit ->" note goes with it
    micro = OUT / "studio_micro.html"
    if micro.exists():
        rm = drive(micro, [{"do": "evaluate", "src": good}], tmp_path)
        assert "circuit" in rm["base"]["q_now"] or "executing" in rm["base"]["q_now"] \
            or "shuttling" in rm["base"]["q_now"], rm["base"]["q_now"]
        authored = rm["steps"][0]["after"]
        assert authored["authored"] is True and authored["frames"] == 2
        assert "circuit &rarr;" not in authored["p_now"], authored["p_now"]
        assert "written here" in authored["q_now"], authored["q_now"]
        assert "executing" not in authored["q_now"], authored["q_now"]


# ======================================================================================
# H. THE PICTURE IS MEASURABLE: MICROMETRES, DEGREES, AND A SCALE THAT DOES NOT LIE
# ======================================================================================
#
# A node position is a LATTICE UNIT, which is not a length.  For as long as that was all a
# page carried, no distance on it had a value and no angle on it was the angle on the die:
# `_fit` may stretch one axis by up to `K_ANISO` to fill the viewport, and on the shipped
# ring it does -- 21.9 px per unit across against 144 down, so a right angle is drawn at
# 8.1 degrees.  Both of those are now answerable, and both are answered with NUMBERS the
# harness can assert rather than a picture someone has to look at.


@requires_node
@requires_studio
def test_every_page_carries_the_scale_it_is_drawn_in(tmp_path):
    """The technology reaches the page, and the page is fitted to it.

    `surface_default` is the fallback for every entry point that names none, so a board
    page and the studio both measure -- the studio never loaded a technology at all before
    this, and `qccd phys --html` was the only page on which a length meant anything.
    """
    for page in [STUDIO] + [p for p in (OUT / "ring144_24v__deck__corrected.html",
                                        OUT / "grid9x9__walk__corrected.html")
                            if p.exists()]:
        r = drive(page, [], tmp_path)
        sc = r["base"]["scale"]
        assert sc is not None, f"{page.name} carries no technology"
        assert sc["preset"] == "surface_default"
        assert sc["nm_x"] == 464000 and sc["nm_y"] == 464000
        assert sc["n_dc_pairs"] == 3 and sc["dc_pitch"] == 58000
        # TRUE SCALE IS THE DEFAULT, and the layout the page is actually drawn at says so.
        # `tests/studio.mjs` deliberately does not stub localStorage, so this is the
        # first-visit state and not a remembered one.
        assert sc["true_scale"] is True and sc["layout_true"] is True
        # the bar is a ROUND physical length, and it names the process it is measured in
        assert sc["bar_um"] in (0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500,
                                1000, 2000, 5000, 10000, 20000, 50000, 100000,
                                200000, 500000), sc
        assert sc["preset"] in sc["bar_label"]
        # ... and it does NOT claim a single scale on a drawing that has two
        assert "x only" not in sc["bar_label"], (
            "the page is at true scale, so one length means the same in both directions")


@requires_node
@requires_studio
def test_the_ruler_measures_in_micrometres_and_degrees(tmp_path):
    """Two clicks are a distance, three are an angle, two rails are the angle between them.

    The numbers are the point.  `ring144_24v` is a lattice of unit steps and the default
    technology is 464 um per unit, so the distance between two adjacent sites is 464 um
    exactly -- and its rails meet at 90 degrees, which the DRAWING does not show, because
    the fit is free to stretch one axis.  The ruler computes from node positions through
    the technology rather than off the screen for exactly that reason.
    """
    page = OUT / "ring144_24v__deck__corrected.html"
    if not page.exists():
        pytest.skip("run `python -m qccd demo`")
    r = drive(page, [], tmp_path)
    ruler = r["ruler"]
    assert ruler is not None and ruler["on"] is True
    # while the tool is on it owns every left press: a click is a measurement
    assert ruler["claim"] == "measure" and ruler["claim_after"] == "pan"
    two = ruler["two"]
    assert two["kinds"] == ["node", "node"], "a click must snap to the site it is near"
    assert two["lattice"] == 1
    assert two["distance_um"] == 464, two
    assert (two["dx_um"], two["dy_um"]) == (464, 0)
    rails = ruler["rails"]
    assert rails is not None and rails["kinds"] == ["rail", "rail"]
    assert abs(rails["rail_angle_deg"] - 90.0) < 1e-9, rails
    assert ruler["off"] is False, "pressing the button again leaves the tool"


@requires_node
def test_a_right_click_offers_every_verb_that_applies_to_what_is_under_it(tmp_path):
    """The element menu, on the page `qccd studio` ships.

    It is the only per-element surface left -- the Selection popover, which opened itself
    on every click, is gone -- so what a right-click offers has to be data rather than a
    screenshot.  Three properties, each one a thing that would otherwise fail silently:

    * the claim table's new row, both halves: a right press on a PART is its menu, a right
      press on EMPTY STAGE still pans;
    * every item that cannot run says WHY, on the screen, rather than being hidden or
      offered and then refused;
    * the Modify panel's rows name which applier owns each -- `mutate` works on every
      device, `builder` needs a device with builder statements -- and a device that has
      none is told so and offered the one thing that would fix it.
    """
    page = tmp_path / "menu_probe.html"
    subprocess.run([sys.executable, "-m", "qccd", "studio", "-o", str(page)],
                   cwd=str(ROOT), capture_output=True, timeout=900, check=True)
    r = drive(page, [], tmp_path)["menu"]
    assert r is not None, "the page carries no element menu at all"
    assert r["claim_on_empty"] == "pan", "right-drag on empty stage still pans"
    assert r["drew"] is True, "the probe could not put a device on the scratch canvas"
    assert r["claim_on_element"] == "menu", "a right press on a part is its menu"
    assert r["open"] is True and r["subject"] is not None, r
    ids = [i[0] for i in r["items"]]
    assert ids[0] == "head", "the menu names what it is about first"
    assert "modify" in ids and "delete" in ids, ids
    assert ids[-1] == "close-loop", "the loop gesture is always the last offer"
    assert r["disabled_say_why"] is True, (
        "an item that cannot run must carry its reason; hiding it leaves the user "
        "hunting for a menu item that was never going to be there")
    m = r["modify"]
    assert m["ok"] is True, m
    layers = {f[0]: f[1] for f in m["fields"]}
    assert layers.get("capacity") == "mutate", m["fields"]
    assert set(layers.values()) <= {"mutate", "builder"}, m["fields"]
    # THE PANEL IS HONEST ABOUT WHAT THIS DEVICE CAN TAKE: a builder-backed row is either
    # writable or disabled with the explode offer, never both and never silently dead.
    builder_off = [f[0] for f in m["fields"] if f[1] == "builder" and not f[2]]
    assert (not builder_off) or m["explode_offered"], m
    assert r["escape"] == "menu", "Escape closes the menu before it closes anything else"
    assert r["closed"] is True
    assert r["restored"] is True, "the probe left the page as it found it"


@requires_node
def test_the_page_can_draw_a_shape_and_not_only_measure_one(tmp_path):
    """SKETCH MODE, end to end on the page `qccd studio` ships.

    The ruler probe above proves this page can MEASURE; this one proves it can DRAW.  A
    6 x 4 rectangle has a perimeter of 20 lattice units, so at the one-unit spacing every
    generator uses it holds 20 trapping sites and 20 rails and declares one closed orbit
    over them -- and `Q.lint`, the pass that judges R19, R20 and R21 on the committed
    document, must find nothing the shape put there.  The whole thing comes back in one
    `undoGroup`, because a sketch is ONE gesture.

    The page is built fresh: the probe is on the harness, so a page emitted before it
    existed reports `null` and could not assert anything.
    """
    page = tmp_path / "sketch_probe.html"
    subprocess.run([sys.executable, "-m", "qccd", "studio", "-o", str(page)],
                   cwd=str(ROOT), capture_output=True, timeout=900, check=True)
    sk = drive(page, [], tmp_path)["sketch"]
    assert sk is not None, "the page carries no sketch verbs at all"
    assert sk["mode"] == "sketch", "a canvas with nothing on it opens on Sketch"
    assert sk["tools"] == ["rect", "ellipse", "line", "poly"]
    # an armed shape owns every left press, exactly as the ruler does, and gives it back
    assert sk["armed"] == "rect" and sk["claim"] == "sketch"
    assert sk["claim_after"] == "pan"
    assert sk["ok"] is True, sk["problems"]
    assert sk["nodes"] == 20 and sk["segments"] == 20, sk
    assert sk["loop"] == {"id": sk["loop"]["id"], "n": 20, "closed": True, "kind": "ring"}
    assert sk["geometry_lints"] == [], sk["geometry_lints"]
    assert sk["instance_members"] == 40, (
        "the finished sketch is one unit: every site and rail carries its instance label")
    assert sk["after_undo"] == 0, "one undo takes the whole shape back"
    # and a corner tighter than the device's minimum angle is refused BY NAME
    assert sk["tight"] == {"ok": False, "rule": "R20"}
    assert sk["restored"] is True, "the probe left the page as it found it"


@requires_node
@requires_studio
def test_true_scale_is_a_toggle_and_the_fit_is_the_other_setting(tmp_path):
    """Off, `sx` and `sy` may differ by up to K_ANISO; on, their ratio is the technology's.

    On `ring144_24v` the two are 21.9 and 144 under the fit -- a 6.6:1 stretch, which is
    what makes the device legible and what makes every angle on it wrong.
    """
    page = OUT / "ring144_24v__deck__corrected.html"
    if not page.exists():
        pytest.skip("run `python -m qccd demo`")
    r = drive(page, [], tmp_path)
    t = r["scale_toggle"]
    assert t is not None and t["was"] is True and t["off"] is False
    assert t["fit"]["true_scale"] is False and t["on"]["true_scale"] is True
    assert t["fit"]["sx"] != t["fit"]["sy"], "this device is the anisotropic case"
    # one pixel, the same number of nanometres on both axes.  Not bit-exact: `sx` and `sy`
    # are quantized to four decimals, so a true-scale pair agrees to about 1e-15 relative.
    kx = t["on"]["sx"] / 464000.0
    ky = t["on"]["sy"] / 464000.0
    assert abs(kx - ky) <= 1e-9 * kx, t


@requires_node
@requires_studio
def test_the_scale_guard_can_fail(tmp_path):
    """MUTATION GUARD.  Take the technology away and the readouts must stop claiming one.

    Without this the block above would pass on a page whose `tech` came from the literal
    fallback in the page script rather than from Python, which is a page that measures in
    whatever the last person to edit that literal believed.
    """
    html = STUDIO.read_text(encoding="utf-8")
    anchor = '"nm_per_unit_x":464000'
    assert anchor in html, "the technology is not in the shipped view model"
    broken = tmp_path / "unscaled.html"
    broken.write_text(html.replace(anchor, '"nm_per_unit_x":1', 1), encoding="utf-8")
    r = drive(broken, [], tmp_path)
    assert r["base"]["scale"]["nm_x"] == 1, (
        "the mutation did not reach the page, so the assertion above is reading a literal")


@requires_studio
def test_the_scale_bar_is_never_drawn_over_the_chip():
    """DEFECT, reported 2026-09-16: the scale bar was drawn INSIDE the SVG at the view's
    bottom-left corner, on an opaque plate, so a fit, a pan or a zoom that brought the device
    into that corner hid part of the chip.  It lives in the toolbar under the picture now,
    where nothing on the canvas can be under it: the bar is in the toolbar, not in the canvas
    box, and nothing draws a scale group into the SVG any more."""
    html = STUDIO.read_text(encoding="utf-8")
    canvas = html[html.index('<div class="canvas" id="canvas">'):html.index('<div class="stage-empty"')]
    toolbar = html[html.index('<div class="stagebar" id="stagebar">'):html.index('<div class="track" id="track"')]
    assert 'id="scaleBar"' in toolbar and 'id="scaleLine"' in toolbar and 'id="scaleTxt"' in toolbar
    assert 'id="scaleBar"' not in canvas
    assert "gScale" not in html and "SBAR.plate" not in html
