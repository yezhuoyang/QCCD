"""The `.arch.json` schema and its validator.  PLAN §3.

Hand-rolled rather than `jsonschema`-based: the platform has to run with nothing but the
standard library plus the scientific stack that is already installed (`jsonschema` is
not), and the errors a hand-rolled walker produces carry a JSON path and a sentence of
*why*, which a generic draft-2020-12 message does not.

The schema is a plain nested dict in a tiny type language:

    {"type": "object", "props": {...}, "required": [...], "additional": bool}
    {"type": "map",    "values": T, "keys": <regex or None>}
    {"type": "array",  "items": T, "min": int, "max": int}
    {"type": "string", "enum": [...] , "pattern": <regex>}
    {"type": "number", "min": x, "max": x}
    {"type": "integer", "min": i}
    {"type": "boolean"}
    {"type": "union",  "options": [T, ...]}
    {"type": "any"}

`validate_document` checks shape only.  Cross-field and derived-quantity checks -- that
declared node degrees match the segment incidence, that declared corners match the loop
geometry, that every referenced node id exists -- live in `qccd.arch.device` because
they need the expanded graph.  Both are run by `qccd.arch.load`.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from .curves import TABLES

__all__ = [
    "SCHEMA_VERSION",
    "ARCH_SCHEMA",
    "MIN_LOOP_NODES",
    "ValidationError",
    "validate_document",
    "check",
    "export_schema",
    "ELEMENT_DOC",
    "export_element_docs",
]

#: 0.3 replaced the expanded geometry's `sites`/`junctions` split with one ordered
#: `nodes` array.  No shipped `arch/*.arch.json` changes -- all nine are generator+params
#: -- but the expanded form's shape did, and silently reusing a version number is how a
#: reader ends up guessing.
SCHEMA_VERSION = "0.3"

ID_PATTERN = r"^[A-Za-z_][A-Za-z0-9_.:-]*$"

_ZONE_TYPE = {
    "type": "object",
    "required": ["capacity"],
    "props": {
        "capacity": {"type": "integer", "min": 0},
        "gate": {"type": "boolean"},
        "spam": {"type": "boolean"},
        "cool": {"type": "boolean"},
        "photoionization": {"type": "boolean"},
        "note": {"type": "string"},
    },
}

_CURVE_POINT = {
    "type": "object",
    "required": ["us", "quanta"],
    "props": {
        "us": {"type": "number", "min": 0.0},
        "quanta": {"type": "number", "min": 0.0},
        "table": {"type": "string", "enum": list(TABLES)},
        "source": {"type": "string"},
        "label": {"type": "string"},
    },
}

_CURVE = {"type": "array", "items": _CURVE_POINT, "min": 1}

_PRIMITIVE = {
    "type": "object",
    "required": [],
    "additional": True,  # gates carry free-form scalar fields (us, fidelity, error, ...)
    "props": {
        "curve": _CURVE,
        "curve_by_degree": {"type": "map", "keys": r"^[0-9]+$", "values": _CURVE},
    },
}

_NODE = {
    "type": "object",
    "required": ["id", "pos", "kind"],
    "props": {
        "id": {"type": "string", "pattern": ID_PATTERN},
        "pos": {"type": "array", "items": {"type": "number"}, "min": 1, "max": 3},
        "kind": {"type": "string", "enum": ["site", "junction"]},
        "capacity": {"type": "integer", "min": 0},
        "capacity_explicit": {"type": "boolean"},
        "zone_type": {"type": "string"},
        "labels": {"type": "array", "items": {"type": "string"}},
        # derived-and-cached; re-checked against the expanded graph on load
        "degree": {"type": "integer", "min": 0},
        "corner": {"type": "boolean"},
    },
}

_SEGMENT = {
    "type": "object",
    "required": ["id", "ends"],
    "props": {
        "id": {"type": "string", "pattern": ID_PATTERN},
        "ends": {"type": "array", "items": {"type": "string"}, "min": 2, "max": 2},
        "length": {"type": "number", "min": 0.0},
        "capacity": {"type": "integer", "min": 1},
        "loop": {"type": "union", "options": [{"type": "string"}, {"type": "null"}]},
        "labels": {"type": "array", "items": {"type": "string"}},
        # derived-and-cached
        "corner_endpoints": {"type": "integer", "min": 0, "max": 2},
    },
}

_LOOP = {
    "type": "object",
    "required": ["id", "nodes", "closed"],
    "props": {
        "id": {"type": "string", "pattern": ID_PATTERN},
        "kind": {"type": "string", "enum": ["ring", "path"]},
        "nodes": {"type": "array", "items": {"type": "string"}, "min": 2},
        "closed": {"type": "boolean"},
        "note": {"type": "string"},
    },
}

#: The schema's own floor on a transport loop's node list, READ OUT OF `_LOOP` rather
#: than restated.  `Device.check_structure` enforces it, so an in-memory device can never
#: be one that an `.arch.json` could not hold -- which is exactly what an ordinary
#: "delete this segment" produced before: a one-node loop that every structural check
#: accepted and `Architecture.from_json` refused.
MIN_LOOP_NODES: int = _LOOP["props"]["nodes"]["min"]

_GEOMETRY = {
    "type": "object",
    "required": ["generator"],
    "props": {
        "generator": {
            "type": "string",
            "enum": [
                "ring", "grid", "chain", "ladder", "racetrack", "dual_loop",
                "explicit",
            ],
        },
        "params": {"type": "map", "values": {"type": "any"}},
        # ONE ordered array is what `Device.to_json` writes: the sites/junctions split
        # could not record an order, and node order is what `explicit` listings and
        # `layout._bows`' centroid both ride on.  The two legacy keys stay readable --
        # `_GEOMETRY` does not set `additional: True`, so an unlisted key is refused.
        "nodes": {"type": "array", "items": _NODE},
        "sites": {"type": "array", "items": _NODE},
        "junctions": {"type": "array", "items": _NODE},
        "segments": {"type": "array", "items": _SEGMENT},
        "loops": {"type": "array", "items": _LOOP},
    },
}

_CONTROL = {
    "type": "object",
    "required": ["model"],
    "props": {
        "model": {"type": "string", "enum": ["simd_classes", "direct"]},
        "classes": {"type": "any"},
        "channels": {"type": "map", "values": {"type": "any"}},
        "max_simd_classes_per_cycle": {"type": "integer", "min": 1},
        "intra_inter_exclusive": {"type": "boolean"},
        "wiring": {"type": "map", "values": {"type": "any"}},
        "optical": {"type": "map", "values": {"type": "any"}},
    },
}

ARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name", "schema_version", "geometry", "zone_types", "primitives"],
    "props": {
        "name": {"type": "string", "pattern": ID_PATTERN},
        "schema_version": {"type": "string"},
        "description": {"type": "string"},
        "provenance": {"type": "map", "values": {"type": "any"}},
        "geometry": _GEOMETRY,
        "zone_types": {"type": "map", "values": _ZONE_TYPE},
        "control": _CONTROL,
        "primitives": {"type": "map", "values": _PRIMITIVE},
        "heating": {
            "type": "object",
            "required": [],
            "additional": True,
            "props": {
                "anomalous_rate_quanta_per_ms": {"type": "number", "min": 0.0},
                "note": {"type": "string"},
            },
        },
        "species": {"type": "map", "values": {"type": "any"}},
        "budget": {"type": "map", "values": {"type": "any"}},
    },
}


class ValidationError(ValueError):
    """Raised by `check`; carries the full list of messages."""

    def __init__(self, errors: Sequence[str]):
        self.errors = list(errors)
        n = len(self.errors)
        head = "\n  ".join(self.errors[:20])
        more = f"\n  ... and {n - 20} more" if n > 20 else ""
        super().__init__(f"{n} schema error(s):\n  {head}{more}")


def _typename(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


def _numtext(x: Any) -> str:
    """Render a number the way BOTH walkers must render it.

    JSON has one number type, so the JS mirror cannot recover Python's int/float
    distinction from a shipped schema: `{"min": 0.0}` parses to `0` in V8 and the message
    read `... the minimum 0` on one side and `... the minimum 0.0` on the other.  Encoding
    the float-ness in the blob would be metadata whose only purpose is to reproduce a
    difference that carries no information for the reader, so the difference is DELETED
    here instead -- integral values print without a fraction on both sides, and
    `engine.js::_schemaNum` is this function, character for character.
    """
    if isinstance(x, bool):
        return str(x)
    f = float(x)
    return str(int(f)) if f == int(f) and abs(f) < 1e16 else repr(f)


def _walk(value: Any, spec: Mapping, path: str, errors: list[str]) -> None:
    kind = spec["type"]

    if kind == "any":
        return

    if kind == "null":
        if value is not None:
            errors.append(f"{path}: expected null, got {_typename(value)}")
        return

    if kind == "union":
        for option in spec["options"]:
            trial: list[str] = []
            _walk(value, option, path, trial)
            if not trial:
                return
        opts = ", ".join(o["type"] for o in spec["options"])
        errors.append(f"{path}: matched none of the allowed forms ({opts}); got {_typename(value)}")
        return

    if kind == "boolean":
        if not isinstance(value, bool):
            errors.append(f"{path}: expected boolean, got {_typename(value)}")
        return

    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"{path}: expected integer, got {_typename(value)}")
            return
        if "min" in spec and value < spec["min"]:
            errors.append(f"{path}: {_numtext(value)} is below the minimum {_numtext(spec['min'])}")
        if "max" in spec and value > spec["max"]:
            errors.append(f"{path}: {_numtext(value)} is above the maximum {_numtext(spec['max'])}")
        return

    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{path}: expected number, got {_typename(value)}")
            return
        if "min" in spec and value < spec["min"]:
            errors.append(f"{path}: {_numtext(value)} is below the minimum {_numtext(spec['min'])}")
        if "max" in spec and value > spec["max"]:
            errors.append(f"{path}: {_numtext(value)} is above the maximum {_numtext(spec['max'])}")
        return

    if kind == "string":
        if not isinstance(value, str):
            errors.append(f"{path}: expected string, got {_typename(value)}")
            return
        if "enum" in spec and value not in spec["enum"]:
            allowed = ", ".join(repr(e) for e in spec["enum"])
            errors.append(f"{path}: {value!r} is not one of {allowed}")
        if "pattern" in spec and not re.match(spec["pattern"], value):
            errors.append(f"{path}: {value!r} does not match {spec['pattern']}")
        return

    if kind == "array":
        if not isinstance(value, list):
            errors.append(f"{path}: expected array, got {_typename(value)}")
            return
        if "min" in spec and len(value) < spec["min"]:
            errors.append(f"{path}: needs at least {spec['min']} item(s), has {len(value)}")
        if "max" in spec and len(value) > spec["max"]:
            errors.append(f"{path}: allows at most {spec['max']} item(s), has {len(value)}")
        item_spec = spec.get("items")
        if item_spec is not None:
            for i, item in enumerate(value):
                _walk(item, item_spec, f"{path}[{i}]", errors)
        return

    if kind == "map":
        if not isinstance(value, dict):
            errors.append(f"{path}: expected object, got {_typename(value)}")
            return
        key_pat = spec.get("keys")
        for k, v in value.items():
            if key_pat is not None and not re.match(key_pat, str(k)):
                errors.append(f"{path}: key {k!r} does not match {key_pat}")
            _walk(v, spec["values"], f"{path}.{k}", errors)
        return

    if kind == "object":
        if not isinstance(value, dict):
            errors.append(f"{path}: expected object, got {_typename(value)}")
            return
        props: Mapping = spec.get("props", {})
        for req in spec.get("required", ()):
            if req not in value:
                errors.append(f"{path}: missing required key {req!r}")
        allow_extra = bool(spec.get("additional", False))
        for k, v in value.items():
            if k in props:
                _walk(v, props[k], f"{path}.{k}", errors)
            elif not allow_extra:
                near = ", ".join(sorted(props)[:8])
                errors.append(f"{path}: unknown key {k!r} (known keys: {near})")
        return

    raise AssertionError(f"unhandled schema node type {kind!r} at {path}")


def validate_document(doc: Any, schema: Mapping | None = None) -> list[str]:
    """Shape-check `doc`.  Returns a list of human-readable errors (empty if valid)."""
    errors: list[str] = []
    _walk(doc, dict(schema or ARCH_SCHEMA), "$", errors)
    if isinstance(doc, dict):
        version = doc.get("schema_version")
        if isinstance(version, str) and version != SCHEMA_VERSION:
            errors.append(
                f"$.schema_version: {version!r} but this build speaks {SCHEMA_VERSION!r}"
            )
    return errors


def export_schema() -> dict:
    """The schema, as data, for a client that has no Python.

    `qccd/viz/render.py` puts this in the page's data blob and `engine.js` walks it with a
    line-for-line mirror of `_walk`.  The browser therefore learns the enums, the array
    bounds and the id patterns FROM THIS FILE at build time; nothing about the schema is
    written down in JavaScript.  That matters because a hand-copied enum is a second
    source of truth, and this codebase has been bitten by that repeatedly -- see the
    hand-copied `var SCHEMA_VERSION = '0.2'` this replaces.

    `ARCH_SCHEMA` is already JSON-safe (strings, numbers, booleans, lists, dicts), so this
    is a shallow hand-off rather than a translation: there is no encoder that could put a
    different schema in the page than the one `validate_document` walks.  4,004 bytes
    minified -- 1.2% of the smallest emitted page, 0.4% of the median, 0.1% of the largest.
    """
    return {
        "version": SCHEMA_VERSION,
        "root": ARCH_SCHEMA,
        # The handful of bounds a STRUCTURAL check needs, looked up here by name.  The
        # browser could reach `min_loop_nodes` by navigating `root` -- but a path literal
        # in JS is a second source of truth wearing a disguise, so the lookup happens on
        # this side, where it sits next to the definition.
        # `id_pattern` travels for the same reason `min_loop_nodes` does, and more
        # urgently: `qccd/viz/js/edit.js` carried it as a HAND-COPIED regex literal, and
        # the design palette mints an id on every single add, which makes it the
        # most-exercised path in the whole feature.
        "bounds": {"min_loop_nodes": MIN_LOOP_NODES, "id_pattern": ID_PATTERN},
    }


def export_consumers() -> dict:
    """Every field the schema CANNOT describe: its type, its default, and WHO READS IT.

    `primitives.*` is an OPEN object (`additional: True`) and `heating`, `species`,
    `budget`, `control.classes`, `control.channels`, `control.wiring`, `control.optical`
    and `provenance` are all `{"type": "map", "values": {"type": "any"}}`.  A palette
    generated from the schema alone therefore knows nothing about them, and the only
    alternative -- writing the field list down in JavaScript -- is the second source of
    truth this file exists to prevent.

    `reader` is a dotted name or None.  **None means: this field is declared, printed,
    round-tripped and saved -- and nothing in the cost model, the rules or the hardware
    report computes with it.**  The palette greys it and says so, which is the same
    discipline `_emit_budget` already applies to `max_area_mm2` ("no area model yet").
    A tool that renders an inert field like a live one implies a causation it does not
    have, which is the honest-numbers rule applied to the INPUT side.
    """
    F = _consumer
    return {"fields": [
        # -- zone types (closed in the schema, but the READERS are not in the schema) ---
        F("zone_types.*.capacity", "integer", 1, "qccd.verify.rules.r1_capacity"),
        F("zone_types.*.gate", "boolean", False, "qccd.verify.rules.r6_capability"),
        F("zone_types.*.spam", "boolean", False, "qccd.verify.rules.r6_capability"),
        F("zone_types.*.cool", "boolean", False, "qccd.verify.rules.r6_capability"),
        F("zone_types.*.photoionization", "boolean", False, None),
        F("zone_types.*.note", "string", None, None),
        # -- scalar primitives (OPEN) --------------------------------------------------
        F("primitives.<name>.us", "number", None, "qccd.cost.models.CostModel.gate"),
        F("primitives.<name>.fidelity", "number", None,
          "qccd.verify.replay (SPAM error)"),
        F("primitives.<name>.fidelity_at_n0", "number", None,
          "qccd.verify.rules (R16)"),
        F("primitives.<name>.error_vs_quanta", "string", None,
          "qccd.verify.rules (R16)"),
        F("primitives.<name>.max_quanta", "number", None,
          "qccd.verify.rules.r7_thermal"),
        F("primitives.<name>.error", "number", None, "qccd.verify.replay (reset)"),
        F("primitives.<name>.gates", "integer", None,
          "qccd.cost.models (gate_swap -> gates x ms_gate.us)"),
        F("primitives.<name>.concurrent_with_transport", "boolean", False,
          "qccd.compile (scheduler)"),
        F("primitives.<name>.removes_quanta", "boolean", False, None),
        F("primitives.<name>.broadcastable", "boolean", False, None),
        F("primitives.<name>.scope", "string", None, None),
        F("primitives.<name>.source", "string", None, None),
        F("primitives.<name>.note", "string", None, None),
        # -- control (OPEN maps) -------------------------------------------------------
        F("control.model", "string", "simd_classes", "qccd.arch.control"),
        F("control.max_simd_classes_per_cycle", "integer", 1,
          "qccd.verify.rules.r4_simd_classes"),
        F("control.intra_inter_exclusive", "boolean", False, None),
        F("control.wiring.scheme", "string", "direct",
          "qccd.cost.hardware.hardware_report"),
        F("control.wiring.dacs_dynamic", "integer", 0,
          "qccd.cost.hardware.hardware_report"),
        F("control.wiring.electrodes_per_trap", "integer", 0,
          "qccd.cost.hardware.hardware_report"),
        F("control.wiring.electrodes_per_junction", "integer", 0,
          "qccd.cost.hardware.hardware_report"),
        F("control.wiring.compensation_electrodes_per_trap", "integer", 0,
          "qccd.cost.hardware.hardware_report"),
        F("control.wiring.shim_per_dac", "integer", 1,
          "qccd.cost.hardware.hardware_report"),
        F("control.wiring.note", "string", None, None),
        F("control.channels.grouping", "string", "direct",
          "qccd.arch.control.build_control_plane"),
        F("control.channels.roles", "any", None,
          "qccd.arch.control.build_control_plane"),
        F("control.channels.channels_per_role", "integer", 1,
          "qccd.arch.control.build_control_plane"),
        F("control.channels.differential", "boolean", False,
          "qccd.arch.control.build_control_plane"),
        F("control.channels.switch_per_site", "boolean", False,
          "qccd.verify.rules.r4_drivable"),
        F("control.channels.explicit", "any", None,
          "qccd.arch.control.build_control_plane"),
        F("control.optical.addressing", "string", None, None),
        F("control.optical.per_zone_switch", "boolean", False, None),
        F("control.classes.count", "integer", None, None),
        F("control.classes.generator", "string", None, None),
        F("control.classes.note", "string", None, None),
        F("control.classes.extra.<id>.id", "string", None,
          "qccd.arch.device.Architecture.simd_classes"),
        F("control.classes.extra.<id>.type", "string", "shift",
          "qccd.api.Machine._orbit_warnings"),
        F("control.classes.extra.<id>.orbit", "string", "any",
          "qccd.arch.listing.class_participants"),
        F("control.classes.extra.<id>.entails", "any", None,
          "qccd.cost.models (split/merge charge)"),
        F("control.classes.extra.<id>.delta", "integer", None, None),
        F("control.classes.extra.<id>.direction", "string", None, None),
        F("control.classes.extra.<id>.note", "string", None, None),
        # -- physics (OPEN maps) -------------------------------------------------------
        F("heating.anomalous_rate_quanta_per_ms", "number", 0.0,
          "qccd.verify.rules (R17)"),
        F("heating.note", "string", None, None),
        F("heating.source", "string", None, None),
        F("species.T_coh_s", "number", 0.0, "qccd.cost.physical.t2_metrics"),
        F("species.qubit", "string", None, None),
        F("species.coolant", "string", None, None),
        F("species.sympathetic", "boolean", False, None),
        F("species.coolant_fraction", "number", None, None),
        F("species.source", "string", None, None),
        F("budget.max_dacs", "integer", None, "qccd.cost.hardware.hardware_report"),
        F("budget.max_junctions", "integer", None,
          "qccd.cost.hardware.hardware_report"),
        F("budget.max_area_mm2", "number", None,
          "qccd.cost.hardware.hardware_report (area_mm2 is always 0.0: no area model yet)"),
        F("budget.junction_electrode_multiplier", "number", None, None),
        # -- curve points (closed, but the readers are not in the schema) --------------
        F("primitives.<curve>.points[].us", "number", None, "qccd.cost.models"),
        F("primitives.<curve>.points[].quanta", "number", None, "qccd.cost.models"),
        F("primitives.<curve>.points[].table", "string", "qccdsim_jones",
          "qccd.arch.curves.Curve.pick"),
        F("primitives.<curve>.points[].source", "string", None, None),
        F("primitives.<curve>.points[].label", "string", None, None),
    ]}


def _consumer(path: str, type_: str, default, reader) -> dict:
    return {"path": path, "type": type_, "default": default, "reader": reader}


def export_defaults() -> dict:
    """The dataclass defaults a palette needs, BY REFLECTION.

    Exactly the trick `qccd/viz/render.py::_generator_defaults` already uses for the six
    generators.  A default changed in Python reaches the browser by rebuilding the page,
    with nothing left to forget -- and nothing about a default is written down in JS.
    """
    import dataclasses

    from .curves import CurvePoint, OperatingPointPolicy
    from .device import Loop, Node, Segment

    def defaults(cls, skip=()) -> dict:
        out = {}
        for f in dataclasses.fields(cls):
            if f.name in skip:
                continue
            if f.default is not dataclasses.MISSING:
                v = f.default
            elif f.default_factory is not dataclasses.MISSING:   # type: ignore[misc]
                v = f.default_factory()                          # type: ignore[misc]
            else:
                continue
            if isinstance(v, tuple):
                v = list(v)
            out[f.name] = v
        return out

    return {
        "node": defaults(Node, skip=("id", "pos")),
        "segment": defaults(Segment, skip=("id", "ends")),
        "loop": defaults(Loop, skip=("id", "nodes")),
        "curve_point": defaults(CurvePoint),
        "policy": defaults(OperatingPointPolicy),
        # POLICY, not reflection, and the palette must say why.  A new zone type gets
        # capacity 1 rather than 0 because `add_site` refuses a zone whose capacity is
        # below 1 and because `resolve_capacities` silently SKIPS a zero; a new movement
        # class gets orbit "any" because that is the only orbit that always has
        # participants, so the class cannot land in `lint`'s `class_no_participants` on
        # the very click that created it.
        "new_zone_type": {"capacity": 1, "gate": False, "spam": False, "cool": False},
        "new_class": {"type": "shift", "orbit": "any"},
    }



#: One name and one sentence per palette element.  Lives HERE, beside the schema that
#: GENERATES the palette, because a description written in JavaScript is a description
#: that outlives the field it describes.  `export_schema()` emits only
#: type/enum/min/max/pattern/required -- there are no field docs to draw on -- so this is
#: the only honest home for the prose the element menu shows.
#: `tests/test_arch.py::test_element_docs_cover_palette` asserts these keys are exactly
#: the eleven `EDITOR.palette()` produces, so a twelfth element fails a test rather than
#: rendering a blank line.
ELEMENT_DOC = {
    "site": (
        "Trapping site",
        "A potential well that holds ions. Its capacity is how many fit; its zone type "
        "says whether you can gate, measure or cool there.",
    ),
    "junction": (
        "Junction",
        "A cross where rails meet. Ions pass through and never rest in it, and every "
        "transit is charged motional quanta by degree.",
    ),
    "segment": (
        "Segment",
        "One length of RF rail between two nodes: the road an ion shuttles along, tiled "
        "with the DC electrodes whose ramp carries the well.",
    ),
    "loop": (
        "Loop",
        "A named closed orbit of nodes, so the compiler can rotate everything on it by "
        "one step as a single SIMD instruction.",
    ),
    "zone_type": (
        "Zone type",
        "A named capability class -- capacity, gate, SPAM, cool -- that every site "
        "carrying it inherits.",
    ),
    "curve_point": (
        "Curve point",
        "One measured operating point (microseconds, quanta) on a primitive's "
        "speed-against-heating curve, with the table and paper it came from.",
    ),
    "primitives": (
        "Primitives",
        "The physics package: what a shuttle, split, merge, junction cross or gate costs "
        "in time and in motional quanta.",
    ),
    "control": (
        "Control plane",
        "Wiring scheme, DAC and electrode counts, channel grouping, optical addressing, "
        "and how many SIMD classes may fire in one cycle.",
    ),
    "heating": (
        "Heating",
        "The anomalous background rate in quanta per millisecond -- the elapsed-time "
        "term, which charges an ion for merely existing.",
    ),
    "species": (
        "Species",
        "The qubit ion, the coolant ion, and whether sympathetic cooling is available; "
        "T_coh sets the coherence budget.",
    ),
    "budget": (
        "Budget",
        "Hard ceilings -- DACs, junctions, area -- that the hardware report checks the "
        "design against and reports as over-budget.",
    ),
}


def export_element_docs() -> dict:
    """`{type: {"name": ..., "blurb": ...}}`, shipped to the page as `D.element_docs`."""
    return {k: {"name": n, "blurb": b} for k, (n, b) in ELEMENT_DOC.items()}


def check(doc: Any, schema: Mapping | None = None) -> None:
    """`validate_document`, raising `ValidationError` instead of returning."""
    errors = validate_document(doc, schema)
    if errors:
        raise ValidationError(errors)
