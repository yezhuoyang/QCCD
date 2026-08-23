"""Opening an architecture document, and the fixed point that says it worked.

Dropping a `.arch.json` on the studio used to DESTROY it.  `snapshotOf` built a seed
`from_device` with a `document=` kwarg that `from_device` does not implement -- it builds
from `st.builder`, which nothing had filled -- so `restore` reported success with an empty
problem list and left a canvas of zero nodes.  Export your design, open it again, and it
was gone, quietly.

`Q.documentStatements(doc)` turns the document back into the CALL RECORDS the interpreter
already implements.  That is the same thing `splitListing` replays for the architecture the
page ships, so it introduces no verb and no state shape -- but it is deliberately NOT a
mirror of `listing.py`, which is 942 lines because it also writes prose, picks a baseline
device and elides default parameters to stay readable.  None of that is needed to rebuild
an architecture.

**The specification is a fixed point, not an agreement.**  If what comes back out is what
went in, the import is correct -- whatever route it took, and without `documentStatements`
having to agree with `listing.py` about anything.

"What went in" needs one honest qualification, and it is not a loophole.  A generator
document stores `generator` plus its non-default params and computes the nodes on load;
`serialize` always writes the device out in full.  So reopening `ring144_24v` and saving it
gives a document with 168 explicit nodes rather than four lines of parameters.  That is not
this import's doing -- `qccd studio --seed ring144_24v` and export has always produced the
expanded form -- and nothing is lost, but it means the byte-for-byte fixed point holds only
for documents that were already explicit.  So the specification is checked three ways:

    Architecture.from_json(round) describes the same machine as the original   (all 9)
    round-tripping the result again changes nothing                            (all 9)
    the bytes are identical                                                    (explicit)

The third is the one the user actually meets, because it is what the studio exports.

The zone ordering is the part that looks like a detail and is not.  The seal carries zone
NAMES, because a generator cannot place a site in a zone that does not exist at expansion
time; the per-zone properties are set afterwards, which is legal for a zone that already
exists.  Emitting the full zone block at the seal instead puts a `zone_after_seal` refusal
in the way, which is the bug this project already hit once from the other direction.
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

from qccd.arch import Architecture, load  # noqa: E402
from qccd.verify.rules import architecture_violations  # noqa: E402

NODE = shutil.which("node")
SHIM = (Path(__file__).parent / "shim.mjs").as_uri()
ARCH = ROOT / "arch"
STEMS = sorted(p.stem.replace(".arch", "") for p in ARCH.glob("*.arch.json"))

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

DRIVER = """
import fs from 'fs';
import { createRequire } from 'module';
import path from 'path';
const require = createRequire(import.meta.url);
// edit.js FIRST: the engine delegates degree / corner / corner_endpoints to it rather
// than keeping a second copy of code that already has a parity test.
require(path.join(path.dirname(process.argv[2]), 'js', 'edit.js'));
const Q = require(process.argv[2]);
// `serialize` stamps `schema_version` from the schema, so the engine needs the one the
// page ships -- exported from `qccd/arch/schema.py`, not restated here.
const SCHEMA = JSON.parse(fs.readFileSync(process.argv[5], 'utf8'));
Q.setSchema(SCHEMA);
// the explicit seal validates ids and capacities against the schema's own bounds
globalThis.QCCDEdit.setBounds(SCHEMA.bounds);
const doc = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
let out = {};
try {
  const stmts = Q.documentStatements(doc);
  out.methods = stmts.map(s => s.method);
  const r = Q.applyProgram(stmts);
  if (r.error) { out.errors = [r.error.method + ' #' + r.error.index + ': ' + r.error.message]; }
  else { out.errors = []; out.round = Q.serialize(r.ok); }
} catch (err) {
  out.threw = String((err && err.message) || err);
}
fs.writeFileSync(process.argv[4], JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("docimp") / "drive.mjs"
    p.write_text(DRIVER, encoding="utf-8")
    return p


def roundtrip(driver: Path, tmp_path: Path, doc: dict) -> dict:
    from qccd.arch.schema import export_schema

    src = tmp_path / "in.json"
    dst = tmp_path / "out.json"
    sch = tmp_path / "schema.json"
    src.write_text(json.dumps(doc), encoding="utf-8")
    sch.write_text(json.dumps(export_schema()), encoding="utf-8")
    r = subprocess.run([NODE, str(driver), str(ROOT / "qccd" / "viz" / "engine.js"),
                        str(src), str(dst), str(sch)],
                       capture_output=True, text=True, timeout=600, cwd=ROOT,
                       encoding="utf-8")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr[-3000:]}"
    return json.loads(dst.read_text(encoding="utf-8"))


def canonical(d) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"))


def _where(a, b, path="") -> str:
    """The first leaf two documents disagree on -- a bare False is not a bug report."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if canonical(a.get(k)) != canonical(b.get(k)):
                return _where(a.get(k), b.get(k), f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path}: length {len(a)} -> {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            if canonical(x) != canonical(y):
                return _where(x, y, f"{path}[{i}]")
    return f"{path}: {str(a)[:160]!r} -> {str(b)[:160]!r}"


def numeric(obj):
    """Every number as a float.

    JSON has ONE number type and Python has two, so a `1.0` written by Python comes back
    from JavaScript as `1`.  That is not this import's doing -- `serialize` has always
    collapsed it, and `Architecture.from_json` reads either as a float where the schema
    says `number` -- so the free-form physics blocks are compared on VALUE.  The places
    where the distinction is load-bearing (`capacity`, `degree`) are compared through
    `Architecture`, which types them, and every round-tripped document is separately
    asserted to still load and pass the rules.
    """
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float)):
        return float(obj)
    if isinstance(obj, list):
        return [numeric(x) for x in obj]
    if isinstance(obj, dict):
        return {k: numeric(v) for k, v in obj.items()}
    return obj


def _machine(doc: dict) -> dict:
    """Everything about the document that describes the MACHINE, in a form two documents
    written by different routes can be compared on."""
    a = Architecture.from_json(doc)
    d = a.device
    return {
        "nodes": {n: [list(x.pos), x.kind, x.capacity, x.zone_type, sorted(x.labels)]
                  for n, x in sorted(d.nodes.items())},
        "segments": {s: [sorted(x.ends), x.length, x.capacity, x.loop,
                         sorted(x.labels)]
                     for s, x in sorted(d.segments.items())},
        "loops": {k: [list(v.nodes), v.closed, v.kind]
                  for k, v in sorted(d.loops.items())},
        "zone_types": {k: dict(v) for k, v in sorted(a.zone_types.items())},
        "control": numeric(json.loads(json.dumps(a.control, default=str))),
        "primitives": numeric(doc.get("primitives")),
        "heating": numeric(doc.get("heating")), "species": numeric(doc.get("species")),
        "budget": numeric(doc.get("budget")), "name": doc.get("name"),
        "description": doc.get("description"),
    }


@pytest.mark.parametrize("stem", STEMS)
def test_the_reopened_document_describes_the_same_machine(stem, driver, tmp_path):
    """The specification. Not "the same bytes" for a generator document -- `serialize`
    writes the device out in full and always has -- but the same machine, node for node,
    segment for segment, and every physics section intact."""
    doc = json.loads((ARCH / f"{stem}.arch.json").read_text(encoding="utf-8"))
    got = roundtrip(driver, tmp_path, doc)
    assert "threw" not in got, got.get("threw")
    assert not got["errors"], got["errors"][:3]

    want, back = _machine(doc), _machine(got["round"])
    for key in sorted(set(want) | set(back)):
        assert canonical({key: want.get(key)}) == canonical({key: back.get(key)}), (
            f"{stem}: {key!r} did not survive the round trip")


@pytest.mark.parametrize("stem", STEMS)
def test_reopening_the_result_again_changes_nothing(stem, driver, tmp_path):
    """Idempotence. The first pass may expand a generator into explicit geometry; a second
    pass must be a no-op, or the format is drifting a little further every time it is
    opened."""
    doc = json.loads((ARCH / f"{stem}.arch.json").read_text(encoding="utf-8"))
    once = roundtrip(driver, tmp_path, doc)["round"]
    twice = roundtrip(driver, tmp_path, once)["round"]
    assert canonical(once) == canonical(twice), f"{stem}: opening it twice is not stable"
    assert json.dumps(once) == json.dumps(twice), (
        f"{stem}: every value survived but the key order moved, so the file diffs "
        f"against itself every time it is opened")


@pytest.mark.parametrize("stem", STEMS)
def test_an_explicit_document_round_trips_byte_for_byte(stem, driver, tmp_path):
    """The case the user actually meets: the studio exports explicit documents, so this is
    what "export, then open it again" does. Here the fixed point IS exact."""
    doc = json.loads((ARCH / f"{stem}.arch.json").read_text(encoding="utf-8"))
    explicit = roundtrip(driver, tmp_path, doc)["round"]      # now fully written out
    assert explicit["geometry"].get("nodes"), "the fixture is not explicit"
    again = roundtrip(driver, tmp_path, explicit)["round"]
    assert canonical(explicit) == canonical(again)


@pytest.mark.parametrize("stem", STEMS)
def test_the_reopened_architecture_still_loads_in_python_and_passes_the_rules(
        stem, driver, tmp_path):
    """A document that round-trips as JSON but no longer loads would be a worse bug than
    the one this replaces."""
    doc = json.loads((ARCH / f"{stem}.arch.json").read_text(encoding="utf-8"))
    got = roundtrip(driver, tmp_path, doc)
    arch = Architecture.from_json(got["round"])
    assert architecture_violations(arch) == []
    original = load(ARCH / f"{stem}.arch.json")
    assert len(arch.device.nodes) == len(original.device.nodes)
    assert len(arch.device.segments) == len(original.device.segments)


def test_both_document_kinds_are_covered_and_it_says_which_route_covers_each():
    """Anti-vacuity, stated accurately. The generator branch and the explicit branch are
    different code, and EVERY shipped document is generator-based -- so the explicit branch
    is not reached by opening one of these files directly. It is reached by the second
    pass, because the first pass writes the device out in full, and that is what
    `test_an_explicit_document_round_trips_byte_for_byte` and the idempotence sweep
    exercise. Recording it here so nobody reads the corpus and assumes both are covered
    the same way."""
    kinds = {}
    for stem in STEMS:
        g = json.loads((ARCH / f"{stem}.arch.json").read_text(encoding="utf-8"))["geometry"]
        kinds.setdefault("explicit" if g.get("nodes") else "generator", []).append(stem)
    assert sorted(kinds) == ["generator"], (
        f"a shipped document is now explicit ({kinds.get('explicit')}); the explicit "
        f"branch is reached directly and this test's note is out of date")
    assert len(STEMS) >= 5, STEMS


def test_the_seal_carries_zone_names_and_the_properties_come_after(driver, tmp_path):
    """A generator cannot place a site in a zone that does not exist at expansion time,
    and a zone declared after the seal cannot be used by a site placed later. The split is
    what satisfies both, and getting it backwards fails loudly rather than subtly."""
    doc = json.loads((ARCH / "ring144_24v.arch.json").read_text(encoding="utf-8"))
    got = roundtrip(driver, tmp_path, doc)
    m = got["methods"]
    seal = next(i for i, x in enumerate(m) if x in ("blank", "blank_device"))
    zones = [i for i, x in enumerate(m) if x == "set_zone"]
    assert zones, "no zone was retuned at all"
    assert all(i > seal for i in zones), (seal, zones)
    assert m.count("blank") + m.count("blank_device") == 1, m


# -- the studio path, end to end --------------------------------------------------------

@pytest.fixture(scope="module")
def studio(tmp_path_factory) -> Path:
    page = tmp_path_factory.mktemp("docstudio") / "studio.html"
    subprocess.run([sys.executable, "-m", "qccd", "studio", "-o", str(page)],
                   cwd=ROOT, capture_output=True, timeout=900, check=True)
    return page


def drive(page: Path, tmp_path: Path, body: str, out: Path | None = None) -> dict:
    js = tmp_path / "d.mjs"
    js.write_text("import fs from 'fs';\n"
                  f"import {{ loadPage }} from '{SHIM}';\n"
                  "loadPage(process.argv[2], ';globalThis.__E=EDITOR;');\n"
                  "const E = globalThis.__E;\n"
                  "const N = () => Object.keys(E.state().device.nodes).length;\n"
                  + body, encoding="utf-8")
    args = [NODE, str(js), str(page)] + ([str(out)] if out else [])
    r = subprocess.run(args, capture_output=True, text=True, timeout=600, cwd=ROOT,
                       encoding="utf-8")
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr[-2500:]}"
    return json.loads(r.stdout)


def test_a_design_survives_export_and_reopening(studio, tmp_path):
    """The gesture that used to destroy the work: export, open it again."""
    r = drive(studio, tmp_path, """
E.newCanvas({ name:'mine', template:null }); E.setMode('edit');
E.setComponentParam('linear_register', 'n', 7);
E.stampComponent('linear_register', 0, 0, 0);
E.stampComponent('ancilla_dock', 2, -2, 0);
const doc = E.exportJson();
const before = { n: N(), doc: JSON.parse(doc) };
E.newCanvas({ name:'blank', template:null }); E.setMode('edit');
const emptied = N();
const rr = E.importText(doc);
console.log(JSON.stringify({ before_n: before.n, emptied,
  ok: rr.ok !== false, problems: (rr.problems||[]).map(p=>p.message||p.code).slice(0,2),
  after_n: N(), out: JSON.parse(E.exportJson()), orig: before.doc }));
""")
    assert r["emptied"] == 0
    assert r["ok"], r["problems"]
    assert r["after_n"] == r["before_n"] == 9, (r["after_n"], r["before_n"])
    assert canonical(r["out"]) == canonical(r["orig"]), _where(r["orig"], r["out"])


@pytest.mark.parametrize("stem", STEMS)
def test_reopening_a_shipped_device_gives_back_the_same_machine(stem, studio, tmp_path):
    """The other direction: a file Python wrote, opened in the browser.

    Byte equality is the wrong bar for a GENERATOR document -- `grid9x9` stores
    `{"generator": "grid", "params": {"a": 9, "b": 9}}` and reopening writes its 305 nodes
    out in full, which `serialize` has always done and which loses nothing. The machine has
    to be identical; the encoding does not.
    """
    src = ARCH / f"{stem}.arch.json"
    r = drive(studio, tmp_path, """
const doc = fs.readFileSync(process.argv[3], 'utf8');
const rr = E.importText(doc);
console.log(JSON.stringify({ ok: rr.ok !== false, n: N(),
  problems: (rr.problems||[]).map(p=>p.message||p.code).slice(0,2),
  back: JSON.parse(E.exportJson()) }));
""", out=src)
    assert r["ok"], r["problems"]
    original = load(src)
    assert r["n"] == len(original.device.nodes)

    want = _machine(json.loads(src.read_text(encoding="utf-8")))
    back = _machine(r["back"])
    for key in sorted(set(want) | set(back)):
        assert canonical({key: want.get(key)}) == canonical({key: back.get(key)}), (
            f"{stem}: {key!r} changed by being opened -- "
            f"{_where(want.get(key), back.get(key))}")


def test_an_explicit_file_opens_back_byte_for_byte(studio, tmp_path):
    """Where the exact fixed point does hold: a document that was already explicit, which
    is every document the studio itself writes."""
    doc = json.loads((ARCH / "grid9x9.arch.json").read_text(encoding="utf-8"))
    r = drive(studio, tmp_path, """
const doc = fs.readFileSync(process.argv[3], 'utf8');
E.importText(doc);
const once = E.exportJson();
E.newCanvas({ name:'blank', template:null }); E.setMode('edit');
const rr = E.importText(once);
console.log(JSON.stringify({ ok: rr.ok !== false, once: JSON.parse(once),
  twice: JSON.parse(E.exportJson()) }));
""", out=ARCH / "grid9x9.arch.json")
    assert r["ok"]
    assert r["once"]["geometry"].get("nodes"), "the first pass did not make it explicit"
    assert canonical(r["once"]) == canonical(r["twice"]), _where(r["once"], r["twice"])
    # AND IN THE SAME ORDER. `canonical` sorts keys, so it cannot see a rebuild that
    # keeps every value and shuffles `zone_types` -- which is exactly what sorting the
    # zone names in the converter did: the file stopped matching itself on every reopen.
    assert json.dumps(r["once"]) == json.dumps(r["twice"]), "the key order changed"


def test_an_import_that_fails_leaves_the_design_alone(studio, tmp_path):
    """Import must refuse what it cannot rebuild, without taking the open design with it
    -- the failure mode this whole file exists to close."""
    r = drive(studio, tmp_path, """
E.newCanvas({ name:'keep', template:null }); E.setMode('edit');
E.addNodeAt(0,0,{kind:'site',zone:'data'});
E.addNodeAt(1,0,{kind:'site',zone:'data'});
const before = N();
const bad = E.importText(JSON.stringify(
  { name:'broken', schema_version: 1, geometry: { generator:'explicit', params:{},
    nodes: [{ id:'a', pos:[0,0], kind:'site', capacity:2, zone_type:'nosuchzone' }] },
    zone_types: {}, primitives: {} }));
console.log(JSON.stringify({ refused: bad.ok === false, before, after: N(),
  why: (bad.problems||[]).map(p=>p.message).slice(0,1) }));
""")
    assert r["refused"], "a document naming an undeclared zone must be refused"
    assert r["after"] == r["before"] == 2, "a refused import took the design with it"
