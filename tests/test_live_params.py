"""The palette's parameters move real geometry, and the pin bug that exposed.

Until now a component shipped at its defaults and the tile advertised a parameter count
nobody could act on.  These drive the actual form: set `n`, and the avatar, the record
count, the pins and the stamped geometry all follow -- because they are the same resolved
object, not three things kept in step.

**The bug this feature uncovered was already shipping.**  `pinNode` recovered which
catalogue entry a placed instance came from by probing for `inst + '.' + pins[0].node` and
taking the first match in `CMP`.  `ancilla_dock` and `trap_junction` both call that node
`'j'`, and `CMP` is in sorted order -- so every pin of a placed `trap_junction` resolved
against `ancilla_dock` and came back `no_pin`.  The component whose whole purpose is
"charged by degree, so attach all 4" could not be attached at all, and no test noticed
because nothing had ever tried.

**And it was about to get worse quietly.**  `linear_register`'s east pin is `s{n-1}`.  The
moment `n` is live, the old probe would weld a rail to `s7` on a 13-trap register -- a node
that EXISTS, so the existence check passes, the join succeeds, and the R18 junction charge
lands on the wrong trap with nothing refused.  That is the failure mode this file is really
guarding: not a crash, a plausible wrong answer.

The fix is that a stamp writes its component and variant into the labels, so nothing is
guessed.  The label carries integers only -- pin ids depend on the enumerated dimensions
and on nothing else -- which keeps float formatting out of the round trip entirely.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.arch import Architecture  # noqa: E402
from qccd.arch.library import CATALOG, build  # noqa: E402
from qccd.verify.rules import architecture_violations  # noqa: E402

NODE = shutil.which("node")
SHIM = (Path(__file__).parent / "shim.mjs").as_uri()
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


@pytest.fixture(scope="module")
def studio(tmp_path_factory) -> Path:
    page = tmp_path_factory.mktemp("live") / "studio.html"
    subprocess.run([sys.executable, "-m", "qccd", "studio", "-o", str(page)],
                   cwd=ROOT, capture_output=True, timeout=900, check=True)
    return page


def drive(page: Path, tmp_path: Path, body: str, out: Path | None = None) -> dict:
    js = tmp_path / "d.mjs"
    js.write_text("import fs from 'fs';\n"
                  f"import {{ loadPage }} from '{SHIM}';\n"
                  "loadPage(process.argv[2], ';globalThis.__E=EDITOR;globalThis.__D=D;');\n"
                  "const E = globalThis.__E, D = globalThis.__D;\n"
                  "const N = () => Object.keys(E.state().device.nodes).length;\n"
                  "const walk = (n, o = []) => { if (!n) return o; o.push(n);\n"
                  "  for (const k of (n.children||[])) walk(k, o); return o; };\n"
                  "E.newCanvas({ name:'live', template:null }); E.setMode('edit');\n"
                  + body, encoding="utf-8")
    args = [NODE, str(js), str(page)] + ([str(out)] if out else [])
    r = subprocess.run(args, capture_output=True, text=True, timeout=600, cwd=ROOT)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr[-2500:]}"
    return json.loads(r.stdout)


# -- the pin bug ----------------------------------------------------------------------

def test_a_trap_junction_can_be_attached_at_all(studio, tmp_path):
    """It could not, on every version of this page that has ever shipped: `pinNode`
    resolved it against `ancilla_dock`, which sorts first and shares the node name 'j'."""
    assert (build("ancilla_dock").pins[0]["node"]
            == build("trap_junction").pins[0]["node"] == "j"), (
        "the collision this test is about no longer exists; rewrite the test")

    r = drive(studio, tmp_path, """
const lr = E.stampComponent('linear_register', 0, 0, 0);
const tj = E.stampComponent('trap_junction', 0, 3, 0);
const j = E.joinPin(tj.instance, 'p0', lr.instance + '.s0', {});
console.log(JSON.stringify({
  variant: E.variantOf(tj.instance), pin: E.pinNode(tj.instance, 'p0'),
  ok: j.ok !== false, code: (j.problems||[]).map(p=>p.code)[0] || null,
  problems: E.problems().length }));
""")
    assert r["variant"]["name"] == "trap_junction", r["variant"]
    assert r["pin"] == "c2.j", r["pin"]
    assert r["ok"], f"the join was refused: {r['code']}"
    assert r["problems"] == 0


def test_a_placed_instance_resolves_the_pin_of_its_own_variant(studio, tmp_path):
    """The silent one. `linear_register`'s east pin is `s{n-1}`; probing the default
    catalogue entry gives `s7`, which EXISTS on a 13-trap register -- so the join would
    succeed and put the junction charge on the wrong trap."""
    r = drive(studio, tmp_path, """
E.setComponentParam('linear_register', 'n', 13);
const a = E.stampComponent('linear_register', 0, 0, 0);
E.setComponentParam('linear_register', 'n', 4);
const b = E.stampComponent('linear_register', 0, 5, 0);
console.log(JSON.stringify({
  a_e: E.pinNode(a.instance, 'e'), a_var: E.variantOf(a.instance),
  b_e: E.pinNode(b.instance, 'e'), b_var: E.variantOf(b.instance) }));
""")
    assert r["a_e"] == "c1.s12", r["a_e"]
    assert r["b_e"] == "c2.s3", r["b_e"]
    assert r["a_var"]["sel"] == {"n": 13}
    assert r["b_var"]["sel"] == {"n": 4}
    # and those are the pins the factory declares
    assert build("linear_register", n=13).pins[1]["node"] == "s12"
    assert build("linear_register", n=4).pins[1]["node"] == "s3"


def test_two_instances_of_different_variants_keep_their_own_pins(studio, tmp_path):
    """One shared `CMP` entry, two live variants: the labels are what keep them apart."""
    r = drive(studio, tmp_path, """
E.setComponentParam('grid_tile', 'a', 3); E.setComponentParam('grid_tile', 'b', 2);
const g = E.stampComponent('grid_tile', 0, 0, 0);
E.setComponentParam('grid_tile', 'a', 1); E.setComponentParam('grid_tile', 'b', 1);
const h = E.stampComponent('grid_tile', 9, 0, 0);
console.log(JSON.stringify({ g: E.pinNode(g.instance, 'ne'),
                             h: E.pinNode(h.instance, 'ne') }));
""")
    assert r["g"] == "c1.j3_2", r["g"]
    assert r["h"] == "c2.j1_1", r["h"]


# -- the parameters actually move geometry --------------------------------------------

def test_a_dimension_changes_the_geometry_that_gets_stamped(studio, tmp_path):
    r = drive(studio, tmp_path, """
const before = E.componentSpec('linear_register').records.length;
const n0 = N();
E.setComponentParam('linear_register', 'n', 13);
const after = E.componentSpec('linear_register').records.length;
E.stampComponent('linear_register', 0, 0, 0);
console.log(JSON.stringify({ before, after, placed: N() - n0,
  ids: E.componentSpec('linear_register').records
        .filter(r => r.method === 'd.site').map(r => r.args[0]),
  problems: E.problems().length }));
""")
    want = build("linear_register", n=13)
    assert r["after"] == len(want.records)
    assert r["before"] == len(build("linear_register").records)
    assert r["placed"] == 13
    assert r["ids"] == [x["args"][0] for x in want.records if x["method"] == "d.site"]
    assert r["problems"] == 0


def test_a_scalar_parameter_multiplies_the_coordinates(studio, tmp_path):
    r = drive(studio, tmp_path, """
E.setComponentParam('linear_register', 'n', 4);
const at1 = E.componentSpec('linear_register').records
  .filter(r => r.method === 'd.site').map(r => r.args[1]);
E.setComponentParam('linear_register', 'pitch', 2.5);
const at25 = E.componentSpec('linear_register').records
  .filter(r => r.method === 'd.site').map(r => r.args[1]);
console.log(JSON.stringify({ at1, at25 }));
""")
    assert r["at1"] == [0, 1, 2, 3]
    assert r["at25"] == [0, 2.5, 5, 7.5]
    want = [x["args"][1] for x in build("linear_register", n=4, pitch=2.5).records
            if x["method"] == "d.site"]
    assert r["at25"] == want


def test_the_avatar_redraws_when_a_parameter_changes(studio, tmp_path):
    """The cache key has to include the selection, or the number changes and the picture
    does not -- which is the most confusing way for this to be broken."""
    r = drive(studio, tmp_path, """
const marks = () => {
  const t = walk(document.getElementById('palBody')).find(
    n => n.attrs && n.attrs['data-el'] === 'cmp:linear_register');
  const svg = walk(t).find(n => n.tagName === 'svg');
  const kids = svg ? walk(svg).filter(k => k !== svg) : [];
  return { rects: kids.filter(k => k.tagName === 'rect').length,
           lines: kids.filter(k => k.tagName === 'line').length };
};
const at8 = marks();
E.setComponentParam('linear_register', 'n', 13);
const at13 = marks();
E.setComponentParam('linear_register', 'n', 2);
console.log(JSON.stringify({ at8, at13, at2: marks() }));
""")

    def want(n):
        c = build("linear_register", n=n)
        sites = sum(1 for x in c.records if x["method"] == "d.site")
        segs = sum(1 for x in c.records if x["method"] == "d.segment")
        return {"rects": sites, "lines": segs + len(c.pins)}

    assert r["at8"] == want(8), (r["at8"], want(8))
    assert r["at13"] == want(13), (r["at13"], want(13))
    assert r["at2"] == want(2), (r["at2"], want(2))
    assert r["at8"] != r["at13"], "the avatar did not redraw"


def test_an_inert_parameter_is_shown_as_inert_and_refuses_to_move(studio, tmp_path):
    """`trap_junction.arm` reaches `params` and no record, pin or blurb. A control that
    moves nothing is worse than no control -- the user turns it and believes something
    happened."""
    r = drive(studio, tmp_path, """
const rr = E.setComponentParam('trap_junction', 'arm', 5.0);
const rows = walk(document.getElementById('palBody'))
  .filter(n => n.attrs && n.attrs['data-param'] === 'arm');
console.log(JSON.stringify({ ok: rr.ok,
  code: (rr.problems||[]).map(p=>p.code)[0] || null,
  kinds: rows.map(n => n.attrs['data-kind']) }));
""")
    assert r["ok"] is False
    assert r["code"] == "inert_param"
    assert r["kinds"] == ["inert"], r["kinds"]


def test_the_form_offers_exactly_the_parameters_the_factories_declare(studio, tmp_path):
    """No control that cannot move anything, and no parameter left off the form."""
    r = drive(studio, tmp_path, """
const rows = walk(document.getElementById('palBody'))
  .filter(n => n.attrs && n.attrs['data-param']);
const by = {};
for (const n of rows) by[n.attrs['data-kind']] = (by[n.attrs['data-kind']]||0)+1;
console.log(JSON.stringify({ n: rows.length, by }));
""")
    import inspect

    total = sum(len([p for p, q in inspect.signature(f).parameters.items()
                     if q.default is not inspect.Parameter.empty])
                for f in CATALOG.values())
    assert r["n"] == total, (r["n"], total)
    assert r["by"] == {"dim": 5, "slot": 19, "inert": 1}, r["by"]


# -- refusals -------------------------------------------------------------------------

def test_a_value_the_layout_cannot_measure_is_refused_and_rolled_back(studio, tmp_path):
    """`computeLayout` throws past COORD_MAX and `renderPalette` is outside `paint()`'s
    try/catch, so an accepted bad value aborts the whole bar -- and keeps doing so."""
    r = drive(studio, tmp_path, """
E.setComponentParam('linear_register', 'pitch', 2.5);
const bad = E.setComponentParam('linear_register', 'pitch', 1e9);
console.log(JSON.stringify({ ok: bad.ok,
  code: (bad.problems||[]).map(p=>p.code)[0] || null,
  message: (bad.problems||[]).map(p=>p.message)[0] || null,
  still: E.componentParams('linear_register').slot.pitch,
  drawn: E.componentSpec('linear_register').records.length }));
""")
    assert r["ok"] is False
    assert r["code"] == "out_of_range"
    assert "pitch" in r["message"] and "1000000" in r["message"]
    assert r["still"] == 2.5, "the refused value was kept"
    assert r["drawn"] > 0, "the palette stopped being able to draw"


def test_a_refused_stamp_leaves_nothing_behind(studio, tmp_path):
    """`transaction` trials with `applyProgram`, which has no range check, and calls
    `rebuild()` after committing -- so the refusal has to come first."""
    r = drive(studio, tmp_path, """
const n0 = N();
E.componentParams('linear_register').slot.pitch = 1e9;   // past the guard, by hand
const rr = E.stampComponent('linear_register', 0, 0, 0);
console.log(JSON.stringify({ refused: rr.ok === false, n0, n1: N(),
  code: (rr.problems||[]).map(p=>p.code)[0] || null }));
""")
    assert r["refused"], "a value the layout cannot measure was stamped"
    assert r["n1"] == r["n0"]
    assert r["code"] == "out_of_range"


def test_a_non_numeric_entry_never_reaches_the_table(studio, tmp_path):
    """`Number('1_0')` is 10 in JS and `int('1_0')` is 10 in Python, but `Number('0x10')`
    is 16 where `int()` raises. The form parses, so the table only ever sees numbers."""
    r = drive(studio, tmp_path, """
console.log(JSON.stringify({
  hex: E.cmpCoerce('0x10', 'integer'), under: E.cmpCoerce('1_0', 'integer'),
  spaces: E.cmpCoerce('  7 ', 'integer'), plain: E.cmpCoerce('12', 'integer'),
  f: E.cmpCoerce('2.5', 'number'), fbad: E.cmpCoerce('2.5.1', 'number'),
  exp: E.cmpCoerce('1e3', 'number'), inf: E.cmpCoerce('Infinity', 'number') }));
""")
    assert r["hex"] is None and r["under"] is None
    assert r["spaces"] == 7 and r["plain"] == 12
    assert r["f"] == 2.5 and r["fbad"] is None
    assert r["exp"] == 1000.0
    assert r["inf"] is None, "Infinity must not reach the table"


# -- it still exports -------------------------------------------------------------------

def test_a_reparameterised_design_loads_in_python_and_passes_the_rules(studio, tmp_path):
    out = tmp_path / "live.arch.json"
    drive(studio, tmp_path, """
E.setComponentParam('linear_register', 'n', 11);
E.setComponentParam('linear_register', 'pitch', 2.0);
const a = E.stampComponent('linear_register', 0, 0, 0);
E.setComponentParam('transport_loop', 'width', 5);
E.stampComponent('transport_loop', 0, 6, 0);
fs.writeFileSync(process.argv[3], E.exportJson());
console.log(JSON.stringify({ nodes: N(), problems: E.problems().length }));
""", out=out)
    arch = Architecture.from_json(json.loads(out.read_text(encoding="utf-8")))
    assert architecture_violations(arch) == []
    assert len(arch.device.nodes) == 11 + 2 * 5
    xs = sorted({round(n.pos[0], 6) for n in arch.device.nodes.values()})
    assert 2.0 in xs and 20.0 in xs, xs        # pitch 2 over 11 traps

    labels = {lab for n in arch.device.nodes.values() for lab in n.labels}
    assert "cmpvar:linear_register:n=11" in labels, sorted(labels)
    assert "cmpvar:transport_loop:width=5" in labels, sorted(labels)


def test_the_variant_survives_saving_the_project_and_opening_it_again(studio, tmp_path):
    """A session-only map would lose this and the pins would silently go wrong on reopen.
    The label rides in the document, so a fresh canvas that imports the snapshot resolves
    the same pin the original did."""
    r = drive(studio, tmp_path, """
E.setComponentParam('linear_register', 'n', 9);
E.stampComponent('linear_register', 0, 0, 0);
const snap = JSON.stringify(E.snapshot());
const before = { n: N(), pin: E.pinNode('c1', 'e'), v: E.variantOf('c1') };
E.newCanvas({ name:'fresh', template:null }); E.setMode('edit');
const emptied = N();
const rr = E.importText(snap);
console.log(JSON.stringify({ before, emptied, ok: rr.ok !== false,
  after: N(), pin: E.pinNode('c1', 'e'), v: E.variantOf('c1') }));
""")
    assert r["emptied"] == 0, "the fresh canvas was not actually empty"
    assert r["ok"], "the studio could not re-open its own snapshot"
    assert r["after"] == r["before"]["n"] == 9
    assert r["v"]["sel"] == {"n": 9}, r["v"]
    assert r["pin"] == "c1.s8" == r["before"]["pin"], r["pin"]


def test_a_component_design_survives_export_and_reopening(studio, tmp_path):
    """Exporting a design and opening it again used to destroy it -- the import succeeded
    with an empty problem list and a canvas of zero nodes. It now rebuilds, and the
    variant labels ride along, so the reopened parts still resolve their own pins."""
    r = drive(studio, tmp_path, """
E.setComponentParam('linear_register', 'n', 6);
E.stampComponent('linear_register', 0, 0, 0);
E.stampComponent('trap_junction', 0, 4, 0);
const doc = E.exportJson();
const before = { n: N(), e: E.pinNode('c1', 'e'), j: E.pinNode('c2', 'p0') };
E.newCanvas({ name:'blank', template:null }); E.setMode('edit');
const emptied = N();
const rr = E.importText(doc);
console.log(JSON.stringify({ before, emptied, ok: rr.ok !== false,
  after: N(), e: E.pinNode('c1', 'e'), j: E.pinNode('c2', 'p0'),
  v: E.variantOf('c1'),
  same: JSON.stringify(JSON.parse(E.exportJson())) === JSON.stringify(JSON.parse(doc)) }));
""")
    assert r["emptied"] == 0
    assert r["ok"], "the design could not be reopened"
    assert r["after"] == r["before"]["n"] == 7
    assert r["same"], "the reopened design is not the design that was exported"
    assert r["e"] == r["before"]["e"] == "c1.s5", (r["e"], r["before"]["e"])
    assert r["j"] == r["before"]["j"] == "c2.j", (r["j"], r["before"]["j"])
    assert r["v"]["sel"] == {"n": 6}, r["v"]
