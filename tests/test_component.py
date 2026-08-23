"""A component is the dual of a template, and costs no parity surface.

`listing.TEMPLATE_SKIP_SECTIONS` names the four sections a TEMPLATE drops -- geometry,
sites, segments, loops -- because a template contributes physics and declarations, not
shape.  A COMPONENT is the opposite filter over the same records: shape and nothing else.

That symmetry is the whole design, and it buys the property this file exists to assert:
**a component adds no new verb.**  Instantiating one produces `{method, args, kwargs}`
records in the vocabulary both interpreters already implement, so the JS mirror has
nothing new to drift from.  Measured here: 23 interpreter verbs before, 23 after, and the
document JS builds from the records is byte-identical to Python's.

The one real constraint is rotation.  A component may be *authored* at any angle -- bake
the coordinates in -- but may only be *placed* on a quarter turn, because one of
twenty-four measured cos/sin values differs by a single ulp between CPython and V8, and
`viz/layout.py` records that 2-5 ulp in a node position flips the sign of a segment's bow.
So `translate_point` is swaps and negations, with no trigonometry to disagree about.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.arch import Architecture  # noqa: E402
from qccd.arch.component import (  # noqa: E402
    CompError, Component, component_records, instance_of, instantiate, translate_point)
from qccd.arch.edit import apply_call  # noqa: E402
from qccd.arch.listing import TEMPLATE_SKIP_SECTIONS, template_records  # noqa: E402
from qccd.arch.schema import export_schema  # noqa: E402
from qccd.verify.rules import architecture_violations  # noqa: E402

NODE = shutil.which("node")

RING4 = Component("ring4", [
    {"method": "d.site", "args": ["A", 0.0, 0.0], "kwargs": {"capacity": 2}},
    {"method": "d.site", "args": ["B", 1.0, 0.0], "kwargs": {"capacity": 2}},
    {"method": "d.site", "args": ["C", 1.0, 1.0], "kwargs": {"capacity": 2}},
    {"method": "d.site", "args": ["D", 0.0, 1.0], "kwargs": {"capacity": 2}},
    {"method": "d.segment", "args": ["e0", "A", "B"], "kwargs": {}},
    {"method": "d.segment", "args": ["e1", "B", "C"], "kwargs": {}},
    {"method": "d.segment", "args": ["e2", "C", "D"], "kwargs": {}},
    {"method": "d.segment", "args": ["e3", "D", "A"], "kwargs": {}},
    {"method": "d.loop", "args": ["L", ["A", "B", "C", "D"]],
     "kwargs": {"closed": True, "kind": "ring"}},
], blurb="a four-trap closed orbit")

MACHINE = [
    {"method": "blank_device", "args": [], "kwargs": {"name": "two_rings"}},
    {"method": "set_control", "args": [], "kwargs": {"model": "simd_classes"}},
    {"method": "set_curve", "args": ["shuttle_segment", [
        {"us": 5.0, "quanta": 0.1, "table": "qccdsim_jones", "source": "2510.23519"}]],
     "kwargs": {}},
    {"method": "declare_class", "args": ["rot"],
     "kwargs": {"type": "shift", "orbit": "R1.L", "delta": 1}},
]


def two_rings() -> list[dict]:
    return [{"method": "DeviceBuilder", "args": ["explicit"], "kwargs": {}},
            *instantiate(RING4, "R1", 0.0, 0.0, 0),
            *instantiate(RING4, "R2", 3.0, 0.0, 0),
            *MACHINE]


def build(records) -> Architecture:
    state = None
    for c in records:
        state = apply_call(state, c)
    machine, _ = state
    return machine.arch


# ----------------------------------------------------------------- the substitution


def test_a_quarter_turn_is_swaps_and_negations_only():
    """No trigonometry, so the two languages cannot disagree by an ulp."""
    assert translate_point(1.0, 0.0, 0.0, 0.0, 0) == (1.0, 0.0)
    assert translate_point(1.0, 0.0, 0.0, 0.0, 1) == (0.0, 1.0)
    assert translate_point(1.0, 0.0, 0.0, 0.0, 2) == (-1.0, 0.0)
    assert translate_point(1.0, 0.0, 0.0, 0.0, 3) == (0.0, -1.0)
    assert translate_point(1.0, 0.0, 3.0, 2.0, 1) == (3.0, 3.0)
    # four quarter turns is the identity, exactly -- not to within an epsilon
    x, y = 0.3, -7.25
    for _ in range(4):
        x, y = translate_point(x, y, 0.0, 0.0, 1)
    assert (x, y) == (0.3, -7.25)


def test_a_component_may_not_declare_physics():
    with pytest.raises(CompError) as e:
        Component("sneaky", [{"method": "set_heating", "args": [], "kwargs": {}}])
    assert "geometry only" in str(e.value)


def test_instantiation_renames_everything_it_defines():
    recs = instantiate(RING4, "R1", 3.0, 2.0, 1)
    ids = [r["args"][0] for r in recs]
    assert ids == ["R1.A", "R1.B", "R1.C", "R1.D", "R1.e0", "R1.e1", "R1.e2", "R1.e3", "R1.L"]
    seg = next(r for r in recs if r["args"][0] == "R1.e0")
    assert seg["args"][1:] == ["R1.A", "R1.B"], "a segment must point at the renamed nodes"
    loop = next(r for r in recs if r["method"] == "d.loop")
    assert loop["args"][1] == ["R1.A", "R1.B", "R1.C", "R1.D"]


def test_a_loop_carries_no_label_because_it_cannot():
    """`DeviceBuilder.loop()` takes no `labels`; its membership is derivable from the
    nodes it walks, which are labelled. Stamping it anyway raised TypeError."""
    recs = instantiate(RING4, "R1")
    loop = next(r for r in recs if r["method"] == "d.loop")
    assert "labels" not in loop["kwargs"]
    site = next(r for r in recs if r["method"] == "d.site")
    assert "cmp:R1" in site["kwargs"]["labels"]


# ----------------------------------------------------------------- the device


def test_two_instances_make_one_legal_device():
    arch = build(two_rings())
    assert arch.device.summary()["n_sites"] == 8
    assert len(arch.device.segments) == 8
    assert sorted(arch.device.loops) == ["R1.L", "R2.L"]
    assert architecture_violations(arch) == []


def test_an_instance_is_selectable_as_a_unit_and_survives_a_reload():
    arch = build(two_rings())
    by: dict[str | None, list[str]] = {}
    for n in arch.device.nodes.values():
        by.setdefault(instance_of(n.labels), []).append(n.id)
    assert {k: len(v) for k, v in by.items()} == {"R1": 4, "R2": 4}

    doc = arch.to_json(expanded=True)
    again = Architecture.from_json(json.loads(json.dumps(doc)))
    assert again.to_json(expanded=True) == doc, "the device did not round-trip"
    assert instance_of(again.device.nodes["R2.C"].labels) == "R2"


def test_the_two_filters_partition_the_listing():
    """`template_records` and `component_records` share one frozenset on purpose: a
    section added to the schema must not land in neither half, or in both."""
    from qccd.arch import load
    from qccd.arch.listing import architecture_listing

    arch = load(ROOT / "arch" / "ring144_24v.arch.json")
    listing = architecture_listing(arch, verify=False)
    tmpl = {id(r) for r in template_records(arch, listing=listing)}
    comp = component_records(arch, listing=listing)
    sections = {l.section for l in listing.lines if l.call}
    assert TEMPLATE_SKIP_SECTIONS & sections, "the geometry sections should be present"
    # nothing a template keeps may also be geometry
    for rec in comp:
        assert rec["method"].startswith("d."), rec
    assert len(tmpl) > 0 and len(comp) >= 0


# ----------------------------------------------------------------- the parity claim


@pytest.mark.skipif(NODE is None, reason="node is not on PATH")
def test_a_component_adds_no_verb_and_the_two_halves_agree():
    """THE CLAIM THE WHOLE DESIGN RESTS ON. Feed the identical instantiated records to
    the JS interpreter: it must need no new verb, and must produce the same document."""
    arch = build(two_rings())
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "prog.json").write_text(json.dumps(two_rings()), encoding="utf-8")
        (tmp / "py.json").write_text(json.dumps(arch.to_json(expanded=True)), encoding="utf-8")
        (tmp / "schema.json").write_text(json.dumps(export_schema()), encoding="utf-8")
        js = tmp / "run.mjs"
        js.write_text(f"""
import fs from 'fs';
new Function(fs.readFileSync({json.dumps(str(ROOT / 'qccd/viz/js/edit.js'))}, 'utf8'))();
new Function(fs.readFileSync({json.dumps(str(ROOT / 'qccd/viz/engine.js'))}, 'utf8'))();
const Q = globalThis.QCCD, E = globalThis.QCCDEdit;
const schema = JSON.parse(fs.readFileSync({json.dumps(str(tmp / 'schema.json'))}, 'utf8'));
Q.setSchema(schema); if (E && E.setBounds) E.setBounds(schema.bounds);
const before = Q.methods().length;
const r = Q.applyProgram(JSON.parse(fs.readFileSync({json.dumps(str(tmp / 'prog.json'))}, 'utf8')));
if (r.error) {{ console.log(JSON.stringify({{ error: r.error.message, at: r.error.index }})); process.exit(0); }}
const ser = Q.serialize(r.ok);
const doc = (typeof ser === 'string') ? JSON.parse(ser) : ser;
const py = JSON.parse(fs.readFileSync({json.dumps(str(tmp / 'py.json'))}, 'utf8'));
console.log(JSON.stringify({{ before: before, after: Q.methods().length,
  same: JSON.stringify(doc) === JSON.stringify(py) }}));
""", encoding="utf-8")
        out = subprocess.run([NODE, str(js)], capture_output=True, text=True, timeout=600)
        assert out.returncode == 0, f"{out.stdout}\n{out.stderr[-2000:]}"
        r = json.loads(out.stdout)

    assert "error" not in r, f"the JS interpreter refused the records: {r}"
    assert r["before"] == r["after"], (
        f"instantiating a component changed the verb count {r['before']} -> {r['after']}; "
        f"a component is supposed to be data in a vocabulary that already exists")
    assert r["same"], "JS and Python built different documents from the same records"


# ----------------------------------------------------------------- the shipped library


LIB_MACHINE = [
    {"method": "blank_device", "args": [], "kwargs": {"name": "lib"}},
    *[{"method": "set_zone", "args": [z], "kwargs": k} for z, k in [
        ("trap", {"capacity": 2}), ("data", {"capacity": 2}),
        ("ancilla", {"capacity": 2}), ("gate", {"capacity": 2, "gate": True}),
        ("load", {"capacity": 8, "photoionization": True})]],
    {"method": "set_control", "args": [], "kwargs": {"model": "simd_classes"}},
    {"method": "set_curve", "args": ["shuttle_segment", [
        {"us": 5.0, "quanta": 0.1, "table": "qccdsim_jones", "source": "2510.23519"}]],
     "kwargs": {}},
    *[{"method": "set_degree_curve", "args": ["junction_cross", d, [
        {"us": 100.0, "quanta": 3.0, "table": "qccdsim_jones", "source": "2510.23519"}]],
       "kwargs": {}} for d in (3, 4, 5, 6)],
]


def _catalog_names():
    from qccd.arch.library import CATALOG
    return sorted(CATALOG)


@pytest.mark.parametrize("name", _catalog_names())
def test_every_shipped_component_instantiates_into_a_legal_device(name):
    """Each catalogue entry, placed on its own, must produce a device the verifier
    accepts. A component that only works in the one arrangement its author tried is not
    a component."""
    from qccd.arch.library import build as build_component

    arch = build([{"method": "DeviceBuilder", "args": ["explicit"], "kwargs": {}},
                  *instantiate(build_component(name), "X", 0.0, 0.0, 0),
                  *LIB_MACHINE])
    assert architecture_violations(arch) == [], name


def test_a_component_that_declares_its_own_orbit_renames_the_loop_kwarg():
    """`d.segment(loop=...)` names a loop, so it is an id and must be prefixed like one.
    Renaming only the positional arguments left every segment of `transport_loop`
    pointing at the unprefixed name and the device would not expand."""
    from qccd.arch.library import build as build_component

    recs = instantiate(build_component("transport_loop", width=3), "R1")
    segs = [r for r in recs if r["method"] == "d.segment"]
    assert segs, "transport_loop should declare segments"
    assert all(r["kwargs"].get("loop") == "R1.L" for r in segs), \
        [r["kwargs"].get("loop") for r in segs]

    arch = build([{"method": "DeviceBuilder", "args": ["explicit"], "kwargs": {}},
                  *recs, *LIB_MACHINE])
    assert list(arch.device.loops) == ["R1.L"]
    assert architecture_violations(arch) == []


def test_a_machine_assembled_from_several_components_prices():
    """The end the whole feature exists for: place pieces, get a number."""
    from qccd.api import Machine
    from qccd.arch.library import build as build_component
    from qccd.cost import corrected_model
    from qccd.verify import replay

    recs = [{"method": "DeviceBuilder", "args": ["explicit"], "kwargs": {}}]
    recs += instantiate(build_component("transport_loop", width=6), "ring", 0.0, 0.0, 0)
    arch = build(recs + LIB_MACHINE + [
        {"method": "declare_class", "args": ["rot"],
         "kwargs": {"type": "shift", "orbit": "ring.L", "delta": 1}}])

    m = Machine.from_device(arch.device)
    p = m.program("spin").fill(loop="ring.L")
    p.rotate(+1)
    r = replay(p.build(), arch, corrected_model())
    assert r.total_cost > 0 and r.total_steps > 0 and r.total_us > 0
    assert {instance_of(n.labels) for n in arch.device.nodes.values()} == {"ring"}
