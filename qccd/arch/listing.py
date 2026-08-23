"""The architecture listing -- "the program for the Architecture".

`qccd.ir.listing` disassembles the hardware *program*.  This is its twin for the
*machine*: the Python that would rebuild this architecture, emitted as a list of
structured records rather than as a blob of text.

Three properties make it a foundation for an editor rather than a pretty-printer.

**One record is one statement, and it carries the id of what it declares.**
`ArchLine.target` is a namespaced stable id (`site:S0`, `class:dock`, `curve:split`,
`channel:linear_h.all.0`), `ArchLine.refs` are the ids the statement is *about*, and
`ArchLine.call` is the structured `{method, args, kwargs}` form.  A click on the stage
resolves through `ArchListing.index`; a click on a line resolves through `call`.  Nothing
round-trips through text.

**It round-trips.**  `round_trip_check()` emits the Python, `exec`s it, and structurally
diffs the result against the original -- device, params, zone types, primitives, control,
heating, species, budget, description and provenance.  All nine shipped architectures
come back identical.  A listing that could not rebuild its own subject would be a
description, not a program.

**It degrades honestly.**  A device that is still reproducible from its generator emits
one `Machine.blank("ring", width=72, ...)` line.  One that has been dragged emits the
generator call *plus* `move_site` patches (`patch` mode).  One that has gained a node
emits every node, segment and loop through a `DeviceBuilder` (`explicit` mode).  The
mode is chosen from `Device.reproducible_from_generator()`, never guessed.

Statement order matters in exactly three places, and an editor MUST re-emit from the
object model rather than splice text because of them:

1. zone types referenced by `geometry.params` must exist *before* the constructor runs
   -- which is what `Machine.blank(..., zones=[...])` is for.  A chained setter cannot
   express it: `stationary_chain` puts its sites in a `register` zone at expansion time.
2. `set_control(classes={...})` must precede `declare_class(...)`; the one-level merge
   preserves the `extra` list, a wholesale replacement would not.
3. `set_zone` before `set_site_capacity` is not *required* (`capacity_explicit` survives
   `resolve_capacities`) but the emitted order keeps it anyway.

Everything else commutes.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .curves import OperatingPointPolicy
from .device import Architecture, Device
from .generators import GENERATORS

__all__ = [
    "ArchLine", "ArchListing", "architecture_listing", "emit_python", "rebuild",
    "architecture_diff", "round_trip_check", "class_participants",
    "template_records", "TEMPLATE_SKIP_SECTIONS",
]

#: The listing sections that describe the GEOMETRY -- i.e. exactly the keys
#: `api._template_doc` pops (`geometry`, `name`, `description`, `provenance`).  Everything
#: outside them is what a template contributes: zone types, movement classes, the control
#: block, primitive curves, background physics and the budget.
TEMPLATE_SKIP_SECTIONS = frozenset({"geometry", "sites", "segments", "loops"})


def template_records(arch, *, listing=None) -> tuple[dict, ...]:
    """The call records that declare everything `api._template_doc` keeps.

    `Machine.ring(..., template="ring144_24v")` reads `arch/ring144_24v.arch.json` off
    disk and drops its geometry.  A browser has no filesystem, so the emitted page ships
    each template it might need as THESE RECORDS and `engine.js::_applyTemplate` replays
    them onto a fresh state.  Expressing a template as the program that declares it --
    rather than as a second document format the browser would have to deserialize -- keeps
    it in the one language both halves already agree on, and lets the parity harness
    replay the identical records through `apply_program` on the Python side.
    """
    if listing is None:
        listing = architecture_listing(arch, verify=False)
    return tuple(
        dict(line.call) for line in listing.lines
        if line.call and line.section not in TEMPLATE_SKIP_SECTIONS
    )


# --------------------------------------------------------------------------- records


@dataclass(frozen=True, slots=True)
class ArchLine:
    """One statement of the architecture program.

    `kind == "call"` records are the Python; everything else is annotation that
    `python()` drops.  A record may render over several physical lines (a curve with
    four operating points is one statement), which is why `line_map()` exists.
    """

    n: int                       # record ordinal, 1-based, stable within one listing
    kind: str                    # "call" | "note" | "comment" | "header" | "blank"
    section: str                 # header|geometry|sites|segments|loops|zones|classes|
                                 # control|primitives|policy|physics|budget
    text: str                    # rendered line(s); may contain "\n"
    target: str | None = None    # the namespaced id this record DECLARES
    call: Mapping | None = None  # {"method": str, "args": list, "kwargs": dict}
    refs: tuple[str, ...] = ()   # ids this record is ABOUT (the highlight set)
    note: str | None = None      # derived annotation; NEVER part of the Python
    editable: bool = False       # may a future editor rewrite this into a call?

    def to_json(self) -> dict:
        d: dict = {"n": self.n, "kind": self.kind, "section": self.section,
                   "text": self.text}
        if self.target:
            d["target"] = self.target
        if self.call:
            d["call"] = dict(self.call)
        if self.refs:
            d["refs"] = list(self.refs)
        if self.note:
            d["note"] = self.note
        if self.editable:
            d["editable"] = True
        return d


@dataclass(frozen=True)
class ArchListing:
    """The whole listing: records, plus the inverted id -> records index."""

    name: str
    mode: str                              # "generator" | "patch" | "explicit"
    template: str | None                   # base doc, or None if self-contained
    lines: tuple[ArchLine, ...]
    index: Mapping[str, tuple[int, ...]]   # object id -> record ordinals
    round_trip: bool | None = None
    round_trip_diff: tuple[str, ...] = ()

    def python(self) -> str:
        """Just the executable statements."""
        return "\n".join(l.text for l in self.lines if l.kind == "call") + "\n"

    def text(self, *, notes: bool = True, numbers: bool = False,
             width: int = 100) -> str:
        """The listing as a reader sees it: statements with their derived annotations."""
        rows: list[str] = []
        phys = 0
        for ln in self.lines:
            body = ln.text
            if notes and ln.note:
                if "\n" in body:
                    body = f"{body}\n{' ' * 4}# {ln.note}"
                else:
                    body = f"{body}{' ' * max(1, width - len(body))}# {ln.note}"
            for physline in body.split("\n"):
                phys += 1
                rows.append(f"{phys:4d} | {physline}" if numbers else physline)
        return "\n".join(rows) + "\n"

    def line_map(self, *, notes: bool = False) -> tuple[int, ...]:
        """physical line index (0-based) -> record ordinal.

        `notes` must match the `text(notes=...)` this is used against: a note on a
        multi-line record renders as one extra physical line, so a map built one way and
        a rendering built the other drift apart by a line per curve.  The page splits
        `ArchLine.text` alone, which is `notes=False`.
        """
        out: list[int] = []
        for ln in self.lines:
            k = len(ln.text.split("\n"))
            if notes and ln.note and "\n" in ln.text:
                k += 1
            out.extend([ln.n] * k)
        return tuple(out)

    def lines_for(self, obj_id: str) -> tuple[int, ...]:
        return self.index.get(obj_id, ())

    def record_at(self, physical_line: int, *, notes: bool = False) -> ArchLine | None:
        lm = self.line_map(notes=notes)
        if not 0 <= physical_line < len(lm):
            return None
        return self.lines[lm[physical_line] - 1]

    def by_n(self, n: int) -> ArchLine | None:
        return self.lines[n - 1] if 1 <= n <= len(self.lines) else None

    def to_json(self, *, refs: bool = True) -> dict:
        """`refs=False` drops the per-record highlight sets.

        They are the same information as `index`, inverted, and dropping them takes the
        ring's payload from 47 KB to 38 KB -- worth it inside a self-contained page,
        never worth it for a tool that wants to walk one record.
        """
        lines = []
        for l in self.lines:
            d = l.to_json()
            if not refs:
                d.pop("refs", None)
            lines.append(d)
        return {"name": self.name, "mode": self.mode, "template": self.template,
                "lines": lines,
                "index": {k: list(v) for k, v in self.index.items()},
                "round_trip": self.round_trip,
                "round_trip_diff": list(self.round_trip_diff)}


# --------------------------------------------------------------------------- helpers


def _lit(v: Any) -> str:
    """A Python literal for `v`.

    `ensure_ascii=False` matters: the corpus's prose carries section signs and en
    dashes, and ASCII-escaping them turns readable source into `\\u00a7`.  Listings are
    written as UTF-8.
    """
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        return repr(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_lit(x) for x in v) + "]"
    if isinstance(v, Mapping):
        return "{" + ", ".join(f"{_lit(k)}: {_lit(x)}" for k, x in v.items()) + "}"
    return repr(v)


def _kwd(d: Mapping) -> str:
    """Keyword arguments.  A key that is not an identifier forces the `**{...}` form."""
    if any(not str(k).isidentifier() for k in d):
        return "**" + _lit({str(k): v for k, v in d.items()})
    return ", ".join(f"{k}={_lit(v)}" for k, v in d.items())


def _wrap(text: str, width: int) -> list[str]:
    rows, cur = [], ""
    for w in text.split():
        if len(cur) + len(w) + 1 > width:
            rows.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        rows.append(cur)
    return rows


def class_participants(arch: Architecture, cls_id: str) -> tuple[str, ...]:
    """Which sites may take part in a movement class.

    Resolves `simd_class(cls)["orbit"]`: a loop id -> that loop's nodes; `"any"` ->
    every site; else a segment label (exact, then singular-by-stripping-`s`) -> the
    endpoints of those segments; else a node label -> those nodes; else `()`.

    This is what turns `orbit: "spurs"` into "48 sites may participate" -- the number a
    reader needs to judge whether a declared class is broad or narrow.
    """
    dev = arch.device
    try:
        spec = arch.simd_class(cls_id)
    except KeyError:
        return ()
    orbit = str(spec.get("orbit", "any"))
    if orbit in dev.loops:
        return tuple(dev.loops[orbit].nodes)
    if orbit == "any":
        return tuple(sorted(n.id for n in dev.nodes.values() if n.is_site))
    for label in (orbit, orbit[:-1] if orbit.endswith("s") else orbit):
        segs = [s for s in dev.segments.values() if label in s.labels]
        if segs:
            out: set[str] = set()
            for s in segs:
                out |= set(s.ends)
            return tuple(sorted(out))
        nodes = [n.id for n in dev.nodes.values() if label in n.labels]
        if nodes:
            return tuple(sorted(nodes))
    return ()


def _gen_defaults(gen: str) -> dict:
    fn = GENERATORS.get(gen)
    if fn is None:
        return {}
    return {k: p.default for k, p in inspect.signature(fn).parameters.items()
            if p.default is not inspect.Parameter.empty}


def _nondefault_params(dev: Device) -> dict:
    """The generator parameters worth printing.

    Generators record their *effective* parameters, not the caller's, so `ring(...)`
    reports site_zone/ancilla_zone/segment_capacity/loop_id as well as width/verticals.
    Comparing against `inspect.signature`'s defaults is what lets the emitted call stay
    `Machine.blank("ring", width=72, verticals=24)` instead of a seven-kwarg wall --
    and it is why generators must keep recording the full effective set.
    """
    defaults = _gen_defaults(dev.generator)
    return {k: v for k, v in dev.params.items()
            if k not in defaults or defaults[k] != v}


def _baseline(dev: Device) -> Device | None:
    """What the generator alone would produce, so only real edits are emitted."""
    fn = GENERATORS.get(dev.generator)
    if fn is None:
        return None
    try:
        return fn(**dict(dev.params))
    except Exception:
        return None


def _patchable(dev: Device) -> bool:
    """True when the device differs from its generator only in node positions,
    per-site capacities and segment lengths -- things a patch call can restate."""
    ref = _baseline(dev)
    if ref is None:
        return False
    if set(ref.nodes) != set(dev.nodes) or set(ref.segments) != set(dev.segments):
        return False
    if {k: v.nodes for k, v in ref.loops.items()} != {
            k: v.nodes for k, v in dev.loops.items()}:
        return False
    for nid, n in dev.nodes.items():
        r = ref.nodes[nid]
        if (n.kind, n.zone_type, n.labels) != (r.kind, r.zone_type, r.labels):
            return False
    for sid, sg in dev.segments.items():
        r = ref.segments[sid]
        if (sg.ends, sg.loop, sg.labels, sg.capacity) != (
                r.ends, r.loop, r.labels, r.capacity):
            return False
    return True


# --------------------------------------------------------------------------- builder


class _B:
    """Accumulates records and the inverted index as the emitter walks the arch."""

    def __init__(self, dev: Device | None = None) -> None:
        self.lines: list[ArchLine] = []
        self.index: dict[str, list[int]] = {}
        self._dev = dev

    def _qualify(self, r: str) -> str:
        """Every id in the listing is namespaced, so the id space is unambiguous."""
        if ":" in r or self._dev is None:
            return r
        if r in self._dev.nodes:
            return f"site:{r}"
        if r in self._dev.segments:
            return f"segment:{r}"
        if r in self._dev.loops:
            return f"loop:{r}"
        return f"channel:{r}"

    def add(self, kind, section, text, *, target=None, call=None, refs=(), note=None,
            editable=False) -> None:
        n = len(self.lines) + 1
        refs = tuple(self._qualify(str(r)) for r in refs)
        self.lines.append(ArchLine(n, kind, section, text, target, call, refs, note,
                                   editable))
        for key in ((target,) if target else ()) + refs:
            self.index.setdefault(key, []).append(n)

    def call(self, section, method, sig, *, target, args=(), kwargs=None, refs=(),
             note=None) -> None:
        self.add("call", section, f"m.{method}({sig})", target=target,
                 call={"method": method, "args": list(args),
                       "kwargs": dict(kwargs or {})},
                 refs=refs, note=note, editable=True)

    def note(self, section, text, *, target=None, refs=()) -> None:
        self.add("note", section, f"# {text}", target=target, refs=refs)

    def blank(self, section="") -> None:
        self.add("blank", section, "")

    def rule(self, section, title) -> None:
        self.add("comment", section, f"# -- {title} " + "-" * max(3, 76 - len(title)))


# ------------------------------------------------------------------------------ emit


def architecture_listing(
    arch: Architecture,
    *,
    mode: str = "auto",
    template: str | None = None,
    policy: OperatingPointPolicy | None = None,
    verify: bool = False,
    max_channel_lines: int = 24,
) -> ArchListing:
    """Render `arch` as the program that rebuilds it.

    `mode`: "auto" picks generator / patch / explicit from the device and emits against
    `template` (a base `.arch.json` whose zones, curves and control block are inherited);
    "full" is the self-contained form -- no template, every declaration stated -- and is
    the one that is actually a program you can hand to someone.

    `policy` (a cost model's `OperatingPointPolicy`) resolves each curve to the point
    the compiler would pick, which is the difference between "here are four operating
    points" and "here is the one this run used".
    """
    dev = arch.device
    repro = dev.reproducible_from_generator()
    resolved = ("generator" if repro
                else ("patch" if _patchable(dev) else "explicit"))
    if mode in ("auto", "full"):
        emit_mode = resolved
        # never resolve a template from `arch.name`: a template is a FILE STEM and the
        # name is not (arch/chain.arch.json declares "chain72"), so guessing one from
        # the other raises FileNotFoundError on the rebuild.  No template given means
        # self-contained, which is the form that is actually a program.
        tmpl = None if mode == "full" else template
    else:
        emit_mode = mode
        tmpl = template
    b = _B(dev)
    s = dev.summary()

    # ---- header ----------------------------------------------------------
    b.add("header", "header", f"# architecture {arch.name!r}", target="arch")
    for chunk in _wrap(arch.description or "", 92):
        b.add("header", "header", f"#   {chunk}", target="arch")
    b.add("header", "header",
          f"#   {s['n_nodes']} nodes ({s['n_sites']} sites, capacity "
          f"{s['total_capacity']}), {s['n_segments']} segments, {s['n_loops']} loop(s), "
          f"{s['n_junction_nodes']} junctions (deg>=3), {s['n_corners']} corners",
          target="arch")
    for k, v in (arch.provenance or {}).items():
        b.add("header", "header",
              f"#   {k}: {v if isinstance(v, str) else _lit(v)}", target="arch")
    b.blank()

    _emit_geometry(b, arch, dev, emit_mode, tmpl, s)
    _emit_zones(b, arch, dev, emit_mode)
    _emit_loops(b, arch, dev, s)
    _emit_classes(b, arch)
    _emit_control(b, arch, dev, max_channel_lines)
    _emit_primitives(b, arch, dev)
    _emit_policy(b, arch, policy)
    _emit_physics(b, arch)
    _emit_budget(b, arch)

    # In generator/patch mode no record DECLARES an individual node or segment, so
    # every id nothing else claimed resolves to the generator call.  Clicking any
    # object on the stage always lands somewhere in the listing -- and the editor's job
    # on such a click is to promote the listing (move_site / set_site_capacity) so the
    # object gets a record of its own.
    gen_rec = next((l.n for l in b.lines
                    if l.section == "geometry" and l.kind == "call"), None)
    if gen_rec is not None:
        for nid in dev.nodes:
            b.index.setdefault(f"site:{nid}", []).append(gen_rec)
        for sid in dev.segments:
            b.index.setdefault(f"segment:{sid}", []).append(gen_rec)

    listing = ArchListing(name=arch.name, mode=emit_mode, template=tmpl,
                          lines=tuple(b.lines),
                          index={k: tuple(v) for k, v in b.index.items()})
    if verify:
        ok, diff = round_trip_check(arch, listing=listing)
        listing = ArchListing(listing.name, listing.mode, listing.template,
                              listing.lines, listing.index, ok, tuple(diff))
    return listing


def _emit_geometry(b, arch, dev, mode, tmpl, s) -> None:
    b.rule("geometry", "geometry")
    if mode in ("generator", "patch"):
        params = _nondefault_params(dev)
        sig = _kwd(params)
        zone_refs = sorted({v for k, v in dev.params.items()
                            if k.endswith("_zone") and isinstance(v, str)})
        if tmpl:
            # `sig` is EMPTY when every generator parameter is at its default, and
            # `f"({sig}, name=...)"` would then emit `Machine.ring(, name=...)`, which
            # neither Python nor the browser's grammar can parse.  All nine shipped
            # architectures set at least one non-default parameter, so it has never
            # fired -- which is exactly why it needs a guard rather than a comment.
            head = (f"m = Machine.{dev.generator}(" + (f"{sig}, " if sig else "")
                    + f"name={_lit(arch.name)}, template={_lit(tmpl)})")
            kwargs = {**params, "name": arch.name, "template": tmpl}
            meth = "from_template"
        else:
            head = (f"m = Machine.blank({_lit(dev.generator)}"
                    + (f", {sig}" if sig else "")
                    + f", name={_lit(arch.name)}, zones={_lit(zone_refs)})")
            kwargs = {**params, "name": arch.name, "zones": zone_refs}
            meth = "blank"
        # `args=[dev.generator]`, NOT `args=[]`.  The rendered text says
        # `Machine.blank("ring", ...)` but the STRUCTURED form used to carry an empty arg
        # list, so the generator name survived only in `target="gen:ring"` -- and replaying
        # the record an editor is built on raised `TypeError: Machine.blank() missing 1
        # required positional argument` on all nine shipped architectures.  Two
        # representations of one truth, in the exact record the editor reads.
        # `tests/test_engine_parity.py` replays from `call` ALONE, never reading `target`,
        # which is what keeps this honest.
        b.add("call", "geometry", head, target=f"gen:{dev.generator}",
              call={"method": meth, "args": [dev.generator], "kwargs": kwargs},
              note=f"{s['n_sites']} sites, {s['n_segments']} segments, "
                   f"{s['n_junction_nodes']} of degree>=3",
              editable=True)
        dropped = {k: v for k, v in dev.params.items() if k not in params}
        if dropped:
            b.note("geometry", f"generator defaults left implicit: {_kwd(dropped)}",
                   target=f"gen:{dev.generator}")
        if mode == "patch":
            ref = _baseline(dev)
            moved = [nid for nid, n in dev.nodes.items()
                     if ref and nid in ref.nodes and n.pos != ref.nodes[nid].pos]
            b.note("geometry",
                   f"{len(moved)} node(s) moved off the generator's grid; the geometry "
                   f"is the generator plus these edits",
                   target=f"gen:{dev.generator}")
            for nid in moved:
                pos = [float(c) for c in dev.nodes[nid].pos]
                b.call("sites", "move_site",
                       f"{_lit(nid)}, " + ", ".join(_lit(c) for c in pos),
                       target=f"site:{nid}", args=[nid, *pos], refs=(nid,),
                       note=f"was {tuple(ref.nodes[nid].pos)}; "
                            f"degree {dev.degree(nid)}; incident segment lengths "
                            f"are unchanged")
    else:
        gsig = _lit(dev.generator) + ((", " + _kwd(dev.params)) if dev.params else "")
        b.add("call", "geometry", f"d = DeviceBuilder({gsig})", target="device",
              call={"method": "DeviceBuilder", "args": [dev.generator],
                    "kwargs": dict(dev.params)})
        b.note("geometry",
               "explicit geometry: the generator and its parameters are kept for "
               "provenance but no longer reproduce this graph, so every node, segment "
               "and loop is stated", target="device")
        corners = dev.all_corners
        for nid, node in dev.nodes.items():
            kw: dict = {}
            if node.zone_type:
                kw["zone"] = node.zone_type
            if node.labels:
                kw["labels"] = list(node.labels)
            if node.capacity_explicit:
                kw["capacity"] = node.capacity
            pos = [float(c) for c in node.pos]
            meth = "site" if node.is_site else "junction"
            b.add("call", "sites",
                  f"d.{meth}({_lit(nid)}, " + ", ".join(_lit(c) for c in pos)
                  + (", " + _kwd(kw) if kw else "") + ")",
                  target=f"site:{nid}",
                  call={"method": f"d.{meth}", "args": [nid, *pos], "kwargs": kw},
                  note=f"degree {dev.degree(nid)}"
                       + (", corner" if nid in corners else "")
                       + (f", cap {node.capacity}" if node.is_site else ""),
                  editable=True)
        for sid, seg in dev.segments.items():
            kw = {}
            if seg.loop:
                kw["loop"] = seg.loop
            if seg.labels:
                kw["labels"] = list(seg.labels)
            if seg.length != 1.0:
                kw["length"] = seg.length
            if seg.capacity != 1:
                kw["capacity"] = seg.capacity
            b.add("call", "segments",
                  f"d.segment({_lit(sid)}, {_lit(seg.ends[0])}, {_lit(seg.ends[1])}"
                  + (", " + _kwd(kw) if kw else "") + ")",
                  target=f"segment:{sid}",
                  call={"method": "d.segment", "args": [sid, *seg.ends], "kwargs": kw},
                  refs=seg.ends,
                  note=f"corner_endpoints {dev.corner_endpoints[sid]}", editable=True)
        for lid, loop in dev.loops.items():
            b.add("call", "loops",
                  f"d.loop({_lit(lid)}, {_lit(list(loop.nodes))}, "
                  f"closed={_lit(loop.closed)}, kind={_lit(loop.kind)}"
                  + (f", note={_lit(loop.note)}" if loop.note else "") + ")",
                  target=f"loop:{lid}",
                  call={"method": "d.loop", "args": [lid, list(loop.nodes)],
                        "kwargs": {"closed": loop.closed, "kind": loop.kind,
                                   "note": loop.note}},
                  refs=loop.nodes, editable=True)
        zone_refs = sorted({n.zone_type for n in dev.nodes.values() if n.zone_type})
        b.add("call", "geometry",
              (f"m = Machine.from_device(d.build(), name={_lit(arch.name)}, "
               f"template={_lit(tmpl)})") if tmpl else
              (f"m = Machine.blank_device(d.build(), name={_lit(arch.name)}, "
               f"zones={_lit(zone_refs)})"),
              target="device",
              call={"method": "blank_device" if not tmpl else "from_device",
                    "args": [], "kwargs": {"name": arch.name, "template": tmpl}})
    if arch.description or arch.provenance:
        prov = _kwd(arch.provenance) if arch.provenance else ""
        b.call("geometry", "describe",
               _lit(arch.description or "") + ((", " + prov) if prov else ""),
               target="arch", args=[arch.description or ""],
               kwargs=dict(arch.provenance),
               note="prose and provenance; carried through save()")
    b.blank()


def _emit_zones(b, arch, dev, mode) -> None:
    b.rule("zones", "zones: capacity (R1) and what may happen there (R6)")
    members_by_zone: dict[str, list[str]] = {}
    for nid, node in dev.nodes.items():
        if node.zone_type:
            members_by_zone.setdefault(node.zone_type, []).append(nid)
    for zname, zspec in arch.zone_types.items():
        members = members_by_zone.get(zname, [])
        caps = [k for k in ("gate", "spam", "cool", "photoionization") if zspec.get(k)]
        b.call("zones", "set_zone", f"{_lit(zname)}, {_kwd(zspec)}",
               target=f"zone:{zname}", args=[zname], kwargs=dict(zspec), refs=members,
               note=(f"{len(members)} site(s)" if members else "declared, unused")
                    + (f"; may {', '.join(caps)}" if caps else "; transport only"))
    overrides: dict[int, list[str]] = {}
    for nid, node in dev.nodes.items():
        if node.capacity_explicit:
            overrides.setdefault(node.capacity, []).append(nid)
    if mode != "explicit":
        for cap, ids in sorted(overrides.items()):
            b.call("zones", "set_site_capacity", f"{_lit(ids)}, {cap}",
                   target=f"capacity:{cap}", args=[ids, cap], refs=ids,
                   note=f"{len(ids)} site(s) overriding their zone; survives set_zone")
    if not overrides:
        b.note("zones", "no per-site capacity overrides: every site inherits its zone")
    # only real edits: without the baseline comparison the ring emitted a spurious
    # 24-id set_segment_length for the spurs the generator already makes 0.5 long
    base = _baseline(dev) if mode in ("generator", "patch") else None
    lengths: dict[float, list[str]] = {}
    for sid, seg in dev.segments.items():
        ref = base.segments[sid].length if (base and sid in base.segments) else 1.0
        if seg.length != ref:
            lengths.setdefault(seg.length, []).append(sid)
    if mode != "explicit":
        for length, ids in sorted(lengths.items()):
            b.call("zones", "set_segment_length", f"{_lit(ids)}, {_lit(length)}",
                   target=f"length:{length}", args=[ids, length], refs=ids,
                   note=f"{len(ids)} segment(s); read only by length_scaling models")
    b.blank()


def _emit_loops(b, arch, dev, s) -> None:
    b.rule("loops", "transport loops (derived from the geometry; not settable)")
    corners = dev.all_corners
    for lid, loop in dev.loops.items():
        lc = sorted(set(loop.nodes) & corners)
        b.add("note", "loops",
              f"#   {lid:<8} {loop.kind:<5} {'closed' if loop.closed else 'open':<6} "
              f"{len(loop.nodes):>4} nodes   corners: {', '.join(lc) if lc else '-'}",
              target=f"loop:{lid}", refs=loop.nodes)
    roles: dict[tuple, int] = {}
    for seg in dev.segments.values():
        roles[seg.labels] = roles.get(seg.labels, 0) + 1
    b.note("loops", "segment roles: " + ", ".join(
        f"{'+'.join(k) or '(none)'} x{v}"
        for k, v in sorted(roles.items(), key=lambda kv: -kv[1])))
    b.note("loops", f"degree histogram {s['degree_histogram']}; R18 junctions are the "
                    f"{s['n_junction_nodes']} node(s) of degree>=3")
    b.blank()


def _emit_classes(b, arch) -> None:
    b.rule("classes", "movement classes: one per machine step (R4)")
    meta = {k: v for k, v in (arch.control.get("classes") or {}).items()
            if k != "extra"}
    if meta:
        # must precede declare_class: set_control merges one level deep, so `extra`
        # survives -- a wholesale `classes=` replacement afterwards would not
        b.call("classes", "set_control", f"classes={_lit(meta)}",
               target="classes.meta", kwargs={"classes": meta},
               note="the class family this device is drawn from; `extra` is declared "
                    "below, and this line must come first")
    for cid, spec in arch.simd_classes.items():
        fields = {k: v for k, v in spec.items() if k != "id"}
        part = class_participants(arch, cid)
        b.call("classes", "declare_class", f"{_lit(cid)}, {_kwd(fields)}",
               target=f"class:{cid}", args=[cid], kwargs=fields, refs=part,
               note=f"{len(part)} site(s) may participate"
                    + (f"; entails {'+'.join(spec['entails'])}"
                       if spec.get("entails") else ""))
    # NOT unconditional.  `$.control` is optional at the top level but `model` is REQUIRED
    # inside it (schema.py `_CONTROL.required`), so emitting this line for an architecture
    # whose control block is empty CREATES a control object without a model and the
    # listing no longer replays: `$.control: missing required key 'model'`.  That is only
    # reachable from `Machine.blank(...)` and from an empty from-scratch device -- i.e.
    # from exactly the two starting points a design tool offers -- and it made
    # `EDITOR.ready()` false on a blank canvas.  An architecture with no control block
    # gets its `max_simd_classes()` from the same default on reload, so saying nothing
    # here is faithful as well as necessary.
    if arch.control:
        b.call("classes", "set_control",
               f"max_simd_classes_per_cycle={arch.max_simd_classes()}",
               target="control.max_simd",
               kwargs={"max_simd_classes_per_cycle": arch.max_simd_classes()},
               note="R4: a cycle carries this many classes, so participation is variadic "
                    "but the operation is not")
    if "intra_inter_exclusive" in arch.control:
        b.call("classes", "set_control",
               f"intra_inter_exclusive={_lit(arch.control['intra_inter_exclusive'])}",
               target="control.intra_inter",
               kwargs={"intra_inter_exclusive": arch.control["intra_inter_exclusive"]})
    b.blank()


def _emit_control(b, arch, dev, max_channel_lines) -> None:
    b.rule("control",
           "control plane: what shares a channel, and therefore moves together")
    if arch.control.get("model"):
        b.call("control", "set_control", f"model={_lit(arch.control['model'])}",
               target="control.model", kwargs={"model": arch.control["model"]})
    wiring = dict(arch.control.get("wiring") or {})
    if wiring:
        b.call("control", "set_wiring", _kwd(wiring), target="control.wiring",
               kwargs=wiring,
               note="electrodes per cell and the DAC scheme (deck p.19-20)")
    channels = dict(arch.control.get("channels") or {})
    if channels:
        b.call("control", "set_control", f"channels={_lit(channels)}",
               target="control.channels", kwargs={"channels": channels},
               note="how those electrodes are grouped onto analog channels")
    if arch.control.get("optical"):
        b.call("control", "set_control", f"optical={_lit(arch.control['optical'])}",
               target="control.optical", kwargs={"optical": arch.control["optical"]})
    plane = arch.control_plane
    if not plane.declared:
        b.note("control", "no channel map declared: counts come from the aggregate "
                          "wiring fields and drivability is not judged")
        b.blank()
        return
    # one line per distinct (role, membership).  A role with more distinct memberships
    # than the cap is summarized instead: grid9x9's `direct` wiring expands to 4608
    # channel groups, and "one channel per site per role" is one fact about the scheme,
    # not 4608 facts about 4608 channels.
    buckets: dict[tuple, list[str]] = {}
    by_role: dict[str, list[tuple]] = {}
    for g in plane.groups:
        key = (g.role, g.sites)
        buckets.setdefault(key, []).append(g.id)
        seen = by_role.setdefault(g.role, [])
        if key not in seen:
            seen.append(key)
    for role, keys in by_role.items():
        ids_here = [i for k in keys for i in buckets[k]]
        if len(keys) > max_channel_lines:
            sizes = sorted({len(k[1]) for k in keys})
            b.add("note", "control",
                  f"#   role {role:<10} {len(ids_here):>5} channel(s) over {len(keys)} "
                  f"group(s), each driving "
                  f"{sizes[0] if len(sizes) == 1 else sizes} site(s)",
                  target=f"role:{role}", refs=tuple(ids_here[:1]))
            continue
        for k in keys:
            ids = buckets[k]
            head = ids[0] if len(ids) == 1 else f"{ids[0]} .. {ids[-1]}"
            b.add("note", "control",
                  f"#   {head:<34} role {role:<10} drives {len(k[1]):>4} site(s)",
                  target=f"channel:{ids[0]}", refs=tuple(ids) + tuple(sorted(k[1])))
    for nt in plane.notes:
        b.note("control", nt)
    sm = plane.summary()
    b.note("control", f"{sm['shared_channels']} shared + "
                      f"{sm['compensation_channels']} compensation = {sm['channels']} "
                      f"channels; {sm['electrodes']} electrodes, {sm['switches']} "
                      f"switches")
    example = next((n.id for n in dev.nodes.values() if n.is_site), None)
    if example:
        # the single most legible statement of what broadcast wiring costs: on the ring
        # this reads "forced to follow 167 other site(s)", on a direct-wired grid "0"
        share = plane.sites_sharing_with(example)
        b.note("control", f"e.g. {example} is on {len(plane.channels_of(example))} "
                          f"channel(s) and is forced to follow {len(share)} other "
                          f"site(s)", refs=(example,))
    b.blank()


def _emit_primitives(b, arch, dev) -> None:
    b.rule("primitives",
           "primitive curves: every transport cost is a (us, quanta) curve")
    for pname, curve in arch.primitives.curves.items():
        pts = ",\n".join(f"    dict({_kwd(p.to_json())})" for p in curve.points)
        b.add("call", "primitives",
              f"m.set_curve({_lit(pname)}, [\n{pts}\n])",
              target=f"curve:{pname}",
              call={"method": "set_curve",
                    "args": [pname, [p.to_json() for p in curve.points]], "kwargs": {}},
              note=f"{len(curve.points)} operating point(s) from tables "
                   f"{', '.join(curve.tables())}", editable=True)
    for pname, dc in arch.primitives.degree_curves.items():
        for deg in dc.degrees():
            c = dc.get(deg)
            pts = ",\n".join(f"    dict({_kwd(p.to_json())})" for p in c.points)
            here = sum(1 for n in dev.nodes if dev.degree(n) == deg)
            b.add("call", "primitives",
                  f"m.set_degree_curve({_lit(pname)}, {deg}, [\n{pts}\n])",
                  target=f"curve:{pname}#{deg}",
                  call={"method": "set_degree_curve",
                        "args": [pname, deg, [p.to_json() for p in c.points]],
                        "kwargs": {}},
                  note=f"charged at nodes of degree {deg} ({here} here)", editable=True)
    for pname, sc in arch.primitives.scalars.items():
        b.call("primitives", "set_primitive", f"{_lit(pname)}, {_kwd(sc)}",
               target=f"primitive:{pname}", args=[pname], kwargs=dict(sc))
    b.blank()


def _emit_policy(b, arch, policy) -> None:
    b.rule("policy", "operating point: which point on each curve the compiler picks")
    if policy is None:
        b.note("policy", "no policy given; pass the cost model's OperatingPointPolicy "
                         "to resolve each curve to a point", target="policy")
        b.blank()
        return
    b.note("policy", f"policy table={policy.table!r} objective={policy.objective!r} "
                     f"(set by the cost model, not by the device)", target="policy")
    cover = arch.primitives.table_coverage(policy.table) if policy.table else {}
    for pname, curve in arch.primitives.curves.items():
        try:
            p = curve.pick(policy)
            b.note("policy", f"  {pname:<18} -> {p.us:>6} us, {p.quanta:>5} quanta   "
                             f"[{p.table}]", target=f"curve:{pname}")
        except Exception as exc:                       # an unresolvable curve is data
            b.note("policy", f"  {pname:<18} -> unresolved ({exc})",
                   target=f"curve:{pname}")
    missing = sorted(k for k, v in cover.items() if not v)
    if missing:
        b.note("policy", f"WARNING: table {policy.table!r} supplies no point for "
                         f"{', '.join(missing)}; the union of tables is used instead",
               target="policy")
    b.blank()


def _emit_physics(b, arch) -> None:
    b.rule("physics", "background physics")
    if arch.heating:
        b.call("physics", "set_heating", _kwd(arch.heating), target="heating",
               kwargs=dict(arch.heating),
               note="R17: quanta accrue with elapsed time whether or not an ion moves")
    if arch.species:
        b.call("physics", "set_species", _kwd(arch.species), target="species",
               kwargs=dict(arch.species))
    b.blank()


def _emit_budget(b, arch) -> None:
    from ..cost.hardware import hardware_report

    b.rule("budget", "budget, and what it is measured against")
    hw = hardware_report(arch)
    if arch.budget:
        b.call("budget", "set_budget", _kwd(arch.budget), target="budget",
               kwargs=dict(arch.budget))
    measured = {
        "max_dacs": (hw.dacs, f"{hw.dacs_broadcast} broadcast + "
                              f"{hw.dacs_compensation} compensation, counted from the "
                              f"channel map"),
        "max_junctions": (hw.n_junctions,
                          "nodes of degree>=3 in the expanded graph (R18)"),
        "max_area_mm2": (hw.area_mm2 or None,
                         "no area model yet: hardware_report leaves area_mm2 at 0.0"),
    }
    for key, val in (arch.budget or {}).items():
        got, how = measured.get(key, (None, "not measured by hardware_report"))
        if got is None:
            b.note("budget", f"  {key:<16} {val!s:<8} -- {how}", target=f"budget:{key}")
        else:
            ok = "OK" if got <= float(val) else "OVER"
            b.note("budget", f"  {key:<16} {val!s:<8} vs {got!s:<8} {ok:<5} {how}",
                   target=f"budget:{key}")
    for o in hw.over_budget:
        b.note("budget", f"  OVER BUDGET: {o}", target="budget")
    b.note("budget", f"  electrodes {hw.electrodes}, switches {hw.switches}, "
                     f"trapping zones {hw.trapping_zones}, total capacity "
                     f"{hw.total_capacity}")


# ------------------------------------------------------------------------ round trip


def emit_python(arch: Architecture, **kw) -> str:
    kw.setdefault("mode", "full")
    return architecture_listing(arch, **kw).python()


def rebuild(python: str) -> Architecture:
    """Execute an emitted listing and return the architecture it builds.

    `exec` on generated text is fine; `exec` on *user-edited* text is arbitrary code
    execution.  A design tool must apply `ArchLine.call` records through a method
    whitelist instead of routing edits back through here.
    """
    from ..api import Machine
    from . import Device, DeviceBuilder, Loop, Node, Segment

    ns: dict = {"Machine": Machine, "Node": Node, "Segment": Segment, "Loop": Loop,
                "Device": Device, "DeviceBuilder": DeviceBuilder}
    exec(compile(python, "<arch-listing>", "exec"), ns)
    return ns["m"].arch


def architecture_diff(a: Architecture, b: Architecture) -> list[str]:
    """Structural difference between two architectures.  Empty means identical."""
    from . import devices_equal

    out: list[str] = []
    if a.name != b.name:
        out.append(f"name {a.name!r} != {b.name!r}")
    out += [f"device: {d}" for d in devices_equal(a.device, b.device)]
    if dict(a.device.params) != dict(b.device.params):
        out.append(f"device.params {dict(a.device.params)} != {dict(b.device.params)}")
    for fld in ("zone_types", "control", "heating", "species", "budget"):
        x, y = dict(getattr(a, fld)), dict(getattr(b, fld))
        if x != y:
            keys = sorted(k for k in set(x) | set(y) if x.get(k) != y.get(k))
            out.append(f"{fld}: keys differ {keys}")
    if a.primitives != b.primitives:
        pa, pb = a.primitives, b.primitives
        out.append("primitives: curves %s, degree_curves %s, scalars %s" % (
            sorted(k for k in set(pa.curves) | set(pb.curves)
                   if pa.curves.get(k) != pb.curves.get(k)),
            sorted(k for k in set(pa.degree_curves) | set(pb.degree_curves)
                   if pa.degree_curves.get(k) != pb.degree_curves.get(k)),
            sorted(k for k in set(pa.scalars) | set(pb.scalars)
                   if pa.scalars.get(k) != pb.scalars.get(k))))
    if (a.description or "") != (b.description or ""):
        out.append("description differs")
    if dict(a.provenance) != dict(b.provenance):
        out.append("provenance differs")
    return out


def round_trip_check(arch: Architecture, *, listing: ArchListing | None = None,
                     **kw) -> tuple[bool, list[str]]:
    """Emit, `exec`, diff.  `(True, [])` means the listing rebuilds its own subject."""
    listing = listing or architecture_listing(arch, mode=kw.pop("mode", "full"), **kw)
    try:
        rebuilt = rebuild(listing.python())
    except Exception as exc:
        return False, [f"{type(exc).__name__}: {exc}"]
    diff = architecture_diff(arch, rebuilt)
    return (not diff), diff
