"""Reusable parametric pieces of a device -- the idea qiskit-metal is built on.

A user of `qiskit-metal` does not place polygons; they place a `TransmonPocket` with
options and it draws its own geometry.  Our studio made you place bare `d.site` and
`d.segment` calls one at a time, which is why adding anything felt laborious.

**A component is the exact dual of a template.**  `listing.TEMPLATE_SKIP_SECTIONS` names
the four sections a template DROPS -- geometry, sites, segments, loops -- because a
template contributes physics and declarations, not shape.  A component is the opposite
filter over the same records: shape and nothing else.  Both halves therefore replay
through interpreters that already exist on both sides, and a component adds **no new
parity surface at all** -- no new verb, no new document format, nothing for the JS mirror
to drift from.  That is the whole reason this file is short.

Instantiation is pure substitution
----------------------------------
`instantiate()` renames, translates and quarter-turns.  It does not solve, fit or lay out.

* **Rename** every local id to ``f"{inst}.{local}"``.  Legal today: `schema.ID_PATTERN`
  already admits ``.`` and ``:``.
* **Translate** by IEEE-754 addition, which is correctly rounded and identically specified
  in CPython and V8, so the JS mirror lands on the same bits.
* **Quarter-turn only.**  Not arbitrary rotation, and this is forced rather than chosen:
  one of twenty-four measured ``cos``/``sin`` values differs by a single ulp between
  CPython and V8, and `viz/layout.py`'s own header records that 2-5 ulp in a node position
  flips the sign of a segment's bow.  A component may therefore be *authored* at any
  angle -- bake the coordinates in -- but *placed* only on quarter turns.

Instances are identified by a label, not by a new field.  `Node.labels` and
`Segment.labels` round-trip byte-identically through `to_json`/`from_json`, are re-emitted
verbatim by the explicit listing, and are read by nothing in `verify/rules.py`,
`compile/` or `cost/` -- so selecting, highlighting and deleting a component as a unit
costs exactly zero physics risk.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping, Sequence

__all__ = [
    "Component", "CompError", "GEOMETRY_METHODS", "LABEL_PREFIX",
    "instantiate", "component_records", "label_of", "instance_of", "translate_point",
]

#: The verbs a component may contain.  Anything else belongs to the machine, not to a
#: piece of it: a component that quietly redefined `set_heating` would change the physics
#: of every other component on the device.
GEOMETRY_METHODS = frozenset({"d.site", "d.junction", "d.segment", "d.loop"})

#: `cmp:<instance>` on every node and segment a component emitted.
LABEL_PREFIX = "cmp:"

_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]*$")


class CompError(ValueError):
    """A component or an instantiation that cannot be expressed."""


def label_of(instance: str) -> str:
    return LABEL_PREFIX + instance


def instance_of(labels: Iterable[str]) -> str | None:
    """The instance a node or segment belongs to, or None if it is hand-placed."""
    for lab in labels or ():
        if str(lab).startswith(LABEL_PREFIX):
            return str(lab)[len(LABEL_PREFIX):]
    return None


def translate_point(x: float, y: float, dx: float, dy: float, quarter: int
                    ) -> tuple[float, float]:
    """Quarter-turn about the origin, then translate.

    Written as swaps and negations rather than a rotation matrix so that both languages
    produce the same bits: there is no trigonometry here to disagree about.
    """
    q = int(quarter) % 4
    if q == 1:
        x, y = -y, x
    elif q == 2:
        x, y = -x, -y
    elif q == 3:
        x, y = y, -x
    return (x + dx, y + dy)


class Component:
    """A named, parameterised bundle of geometry records."""

    def __init__(self, name: str, records: Sequence[Mapping], *,
                 params: Mapping | None = None, pins: Sequence[Mapping] = (),
                 requires: Mapping | None = None, version: int = 1,
                 blurb: str = ""):
        self.name = str(name)
        self.version = int(version)
        self.params = dict(params or {})
        self.pins = [dict(p) for p in pins]
        self.requires = dict(requires or {})
        self.blurb = str(blurb)
        self.records = [dict(r) for r in records]
        for r in self.records:
            m = str(r.get("method", ""))
            if m not in GEOMETRY_METHODS:
                raise CompError(
                    f"component {self.name!r} contains {m!r}; a component is geometry "
                    f"only ({', '.join(sorted(GEOMETRY_METHODS))}). A record that "
                    f"declares physics belongs to the machine, not to a piece of it")

    # ------------------------------------------------------------------ ids
    def local_ids(self) -> tuple[str, ...]:
        """Every id this component defines, in declaration order."""
        out: list[str] = []
        for r in self.records:
            args = list(r.get("args", ()))
            if args:
                out.append(str(args[0]))
        return tuple(out)

    def to_json(self) -> dict:
        return {"name": self.name, "version": self.version, "params": dict(self.params),
                "pins": [dict(p) for p in self.pins], "requires": dict(self.requires),
                "blurb": self.blurb, "records": [dict(r) for r in self.records]}

    @classmethod
    def from_json(cls, d: Mapping) -> "Component":
        return cls(d["name"], d.get("records", ()), params=d.get("params"),
                   pins=d.get("pins", ()), requires=d.get("requires"),
                   version=int(d.get("version", 1)), blurb=d.get("blurb", ""))


def instantiate(comp: Component, instance: str, dx: float = 0.0, dy: float = 0.0,
                quarter: int = 0, *, extra_labels: Sequence[str] = ()) -> list[dict]:
    """`comp` placed at `(dx, dy)`, as records ready for `apply_program`.

    Returns plain `{method, args, kwargs}` dicts -- the same shape the architecture
    listing already emits and both interpreters already replay.
    """
    if not _ID.match(str(instance)):
        raise CompError(
            f"{instance!r} is not a usable instance name; it becomes the prefix of every "
            f"id this component defines, so it has to satisfy the schema's own id rule")
    local = set(comp.local_ids())
    prefix = str(instance) + "."

    def rename(v):
        return prefix + str(v) if str(v) in local else v

    out: list[dict] = []
    for r in comp.records:
        method = str(r["method"])
        args = list(r.get("args", ()))
        kwargs = dict(r.get("kwargs", {}))

        if method in ("d.site", "d.junction"):
            # (id, x, y)
            args = [rename(args[0]),
                    *translate_point(float(args[1]), float(args[2]), dx, dy, quarter)]
        elif method == "d.segment":
            # (id, a, b) -- and `loop=` NAMES A LOOP, so it is an id too. Renaming only
            # the positional arguments left every segment of a component that declares
            # its own orbit pointing at the unprefixed name: "unknown loop ''".
            args = [rename(args[0]), rename(args[1]), rename(args[2])]
            if kwargs.get("loop"):
                kwargs["loop"] = rename(kwargs["loop"])
        elif method == "d.loop":
            # (id, [nodes...])
            args = [rename(args[0]), [rename(n) for n in (args[1] or ())]]

        # NODES AND SEGMENTS CARRY THE INSTANCE LABEL; A LOOP DOES NOT.
        # `DeviceBuilder.loop()` takes no `labels` -- a loop is an ordered walk over nodes
        # that are themselves already labelled, so its membership is derivable and does
        # not need restating. Stamping it anyway raised `unexpected keyword argument`.
        if method != "d.loop":
            labels = list(kwargs.get("labels", ()) or ())
            labels.append(label_of(instance))
            labels.extend(str(x) for x in extra_labels)
            kwargs["labels"] = labels
        out.append({"method": method, "args": args, "kwargs": kwargs})
    return out


def component_records(arch, *, listing=None) -> tuple[dict, ...]:
    """The geometry half of an architecture's listing -- a template's exact complement.

    `template_records` keeps everything OUTSIDE `TEMPLATE_SKIP_SECTIONS`; this keeps
    everything inside it.  The two share that one frozenset deliberately, so a section
    added to the schema cannot end up in neither half or in both.
    """
    from .listing import TEMPLATE_SKIP_SECTIONS, architecture_listing

    if listing is None:
        listing = architecture_listing(arch, verify=False)
    return tuple(
        dict(line.call) for line in listing.lines
        if line.call and line.section in TEMPLATE_SKIP_SECTIONS
        and str(line.call.get("method", "")) in GEOMETRY_METHODS
    )
