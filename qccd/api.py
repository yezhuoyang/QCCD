"""The Python surface: set up a machine and program it, in Python.

This is the front door. Python *is* the hardware description and the hardware program
language -- there is no separate syntax to learn, and everything below is the same objects
the compiler and verifier use, so a hand-written program and a compiled one are
indistinguishable to the rest of the stack.

Set up a machine::

    from qccd import Machine

    m = Machine.ring(width=72, height=2, verticals=24, name="my_ring")
    m = Machine.grid(9, 9)
    m = Machine.ladder(72, rungs=6, highways=2)
    m = Machine.load("arch/ring144_24v.arch.json")

    m.set_zone("data", capacity=4)                       # retune the device
    m.set_curve("shuttle_segment", [(5, 0.10), (12, 1.0)])
    m.declare_class("rotate_cw", type="shift", orbit="L0", delta=1)
    m.save("arch/my_ring.arch.json")

Program it::

    p = m.program("demo")
    p.init({"d0": "S0", "d1": "S1", "a0": "A0"})
    p.rotate(+13)                                        # one template, every ion
    with p.cycle("dock") as c:                           # one class, many participants
        c.move("d0", "S0", "A0", via=["V0"])
    p.gate("CX", [("d0", "a0")], sites=["A0"])
    p.cool()

Run it::

    r = m.run(p)                       # replay + every rule + the objective
    r.cost, r.steps, r.runtime_ms, r.quanta_per_ion, r.rules_failed
    m.render(p, "out/demo.html")       # one self-contained page

**Space and time are explicit.** Space is the node and segment ids of the device; time is
the sequence of cycles, and a `cycle` is one machine step in which everything happens
together. `p.cycle(cls)` takes exactly one SIMD class because the machine takes exactly one
per step (R4) -- so a program that the hardware could not run is one the API will not build,
rather than one the verifier has to reject afterwards.
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .arch import (
    Architecture,
    Curve,
    CurvePoint,
    DegreeCurve,
    Device,
    DeviceBuilder,
    OperatingPointPolicy,
    SCHEMA_VERSION,
    chain,
    dual_loop,
    grid,
    ladder,
    racetrack,
    ring,
)
from .arch.device import ExpansionError, resolve_capacities
from .arch import edit as _edit
from .arch import load as _load_arch
from .arch import save as _save_arch
from .cost.hardware import HardwareReport, hardware_report
from .cost.models import CostModel, corrected_model, deck_model
from .cost.combinatorial import t1_metrics
from .cost.physical import t2_metrics
from .ir.provenance import CALL_KEY, PROV_KEY, CallLog, capture_site
from .ir.tsir import TSIR, Instruction, Participant, loop_shift
from .verify import VerificationReport, verify

__all__ = ["Machine", "Program", "Cycle", "RunResult", "DeviceBuilder",
           "DEFAULT_TEMPLATE"]

#: The template `Machine.ring(...)` borrows zone types, primitive curves and a
#: control block from when none is named.  ONE definition: `render.py` ships it into
#: the page's data blob and `engine.js` reads it from there, so the browser resolves
#: an un-named template to the same architecture Python does without a second copy.
DEFAULT_TEMPLATE = "ring144_24v"


# --------------------------------------------------------------------------- program


def _traced(fn):
    """Record the Python call that ran `fn`, unless an outer traced call is open.

    The guard is what makes `p.shuttle(...)` one record instead of one per hop: the
    `p.move` calls it makes inside see an open call and inherit it, so every instruction
    the shuttle emits points at the shuttle line the user actually wrote.
    """
    names = fn.__code__.co_varnames[1:fn.__code__.co_argcount]
    op = fn.__name__

    @functools.wraps(fn)
    def wrapper(self, *a, **kw):
        log = self._prov
        if log is None or log.current is not None:
            return fn(self, *a, **kw)
        # sys._getframe, never traceback/inspect: see qccd.ir.provenance for the
        # 350x / 15000x measurements that make that not a style preference
        log.open(op, capture_site(1), names, a, kw)
        try:
            return fn(self, *a, **kw)
        finally:
            log.close()

    wrapper.__wrapped__ = fn
    return wrapper


class Cycle:
    """One machine step: one SIMD class, many participants (R4).

    Handed to you by `Program.cycle`; collect moves on it and they are issued together.
    """

    def __init__(self, cls: str, mode: str = "inter", broadcast: bool | str = False):
        self.cls = cls
        self.mode = mode
        #: The R4c claim: `"one"` | `"per_direction"` | `"per_site"` | False.  It is an
        #: INTENT, not a device fact -- it names no channel and states no count.
        self.broadcast = broadcast
        self._moves: list[Participant] = []
        self.meta: dict = {}

    def move(
        self,
        ion: str,
        src: str,
        dst: str,
        via: Sequence[str] | None = None,
    ) -> "Cycle":
        """One ion from `src` to `dst`.

        `via` lists the segments crossed, in order. Give it whenever the route passes
        through anything -- a junction between two traps, for instance -- because the
        cost model charges what the path crosses, not what it ends on.
        """
        self._moves.append(Participant(ion, src, dst, tuple(via or ())))
        return self

    def moves(self, items: Iterable[Sequence]) -> "Cycle":
        for it in items:
            self.move(*it)
        return self

    def __len__(self) -> int:
        return len(self._moves)


class Program:
    """A TSIR program under construction.  Every method returns `self` so it chains."""

    def __init__(self, machine: "Machine", name: str, *,
                 provenance: str = "calls", root: str | None = None):
        self._m = machine
        self._prog = TSIR(name=name, arch_spec=machine.spec_path)
        self._prov = None if provenance == "off" else CallLog(
            provenance, root if root is not None else os.getcwd())
        if self._prov is not None:
            # the live lists, so the log in `meta` is always current without a stamp step
            self._prog.meta[PROV_KEY] = self._prov.to_json()

    def _meta(self, meta: Mapping, **extra) -> dict:
        """The meta of an instruction about to be emitted, tagged with its call."""
        d = dict(meta, **extra) if extra else dict(meta)
        if self._prov is not None and self._prov.current is not None:
            d[CALL_KEY] = self._prov.current
        return d

    # -- state ---------------------------------------------------------------

    @_traced
    def init(
        self,
        placement: Mapping[str, str],
        quanta: Mapping[str, float] | None = None,
        **meta,
    ) -> "Program":
        """Place every ion.  A program must open with this."""
        unknown = [n for n in placement.values() if n not in self._m.arch.device.nodes]
        if unknown:
            raise ValueError(f"no such node(s) on {self._m.name}: {unknown[:5]}")
        self._prog.add(Instruction(
            type="init", id=self._prog.next_id(), placement=dict(placement),
            quanta={k: float((quanta or {}).get(k, 0.0)) for k in placement},
            meta=self._meta(meta)))
        return self

    @_traced
    def fill(self, loop: str | None = None, prefix: str = "d", **meta) -> "Program":
        """Place one ion on every slot of a loop -- the packed ring, in one call."""
        loop = loop or self._m.default_loop
        nodes = list(self._m.arch.device.loops[loop].nodes)
        return self.init({f"{prefix}{i}": n for i, n in enumerate(nodes)}, **meta)

    # -- transport -----------------------------------------------------------

    @_traced
    def rotate(self, delta: int, loop: str | None = None, cls: str | None = None,
               broadcast: bool | str = False, **meta) -> "Program":
        """Rigid lockstep rotation: one template, every ion on the loop.

        `delta` is in slots; sign is the direction. This is one instruction however many
        ions move and however far, because that is what the hardware issues.
        """
        loop = loop or self._m.default_loop
        if loop not in self._m.arch.device.loops:
            raise ValueError(f"no loop {loop!r} on {self._m.name}")
        if not self._m.arch.device.loops[loop].closed and delta:
            raise ValueError(f"loop {loop!r} is open; a rigid rotation is undefined")
        cls = cls or ("rotate_cw" if delta >= 0 else "rotate_ccw")
        self._prog.add(Instruction(
            type="simd", id=self._prog.next_id(), cls=cls, mode="inter",
            template=loop_shift(loop, int(delta)), broadcast=broadcast,
            meta=self._meta(meta, kind="rotate", hops=abs(int(delta)))))
        return self

    def cycle(self, cls: str, mode: str = "inter", broadcast: bool | str = False,
              **meta) -> "_CycleCtx":
        """Open one machine step of class `cls`.

        Use as a context manager; the moves collected inside are issued together::

            with p.cycle("dock") as c:
                c.move("d0", "S0", "A0", via=["V0"])
        """
        if mode not in ("intra", "inter"):
            raise ValueError(f"mode must be 'intra' or 'inter', not {mode!r} (R4b)")
        # `cycle` cannot use @_traced: it RETURNS before the context manager emits the
        # instruction, so the guard would have closed by then.  Capture here, hand the
        # index to the context, and the reported line is the `with p.cycle(...)` the
        # user wrote rather than `_CycleCtx.__exit__`.
        call = None
        if self._prov is not None:
            call = (self._prov.current if self._prov.current is not None
                    else self._prov.record("cycle", capture_site(1),
                                           ("cls", "mode"), (cls, mode), meta))
        return _CycleCtx(self, Cycle(cls, mode, broadcast), meta, call)

    def _route(self, ion: str, src: str, dst: str, via) -> None:
        """Refuse a route whose `via` does not connect `src` to `dst`.

        `Cycle.move` stored whatever it was handed and the error surfaced much later, out
        of the replay or out of `render.py::_node_path`, as a bare `KeyError` on a segment
        id.  A design tool has to say which STATEMENT is wrong while the user is looking at
        it -- and the browser mirror cannot even build a frame without walking the route,
        so this is the check that keeps the two halves refusing the same programmes.
        """
        dev = self._m.arch.device
        node = str(src)
        for sid in via or ():
            if str(sid) not in dev.segments:
                raise KeyError(str(sid))
            node = dev.segments[str(sid)].other(node)
        if via and node != str(dst):
            raise ValueError(
                f"the route of {ion!r} ends at {node!r} but was declared to end at {dst!r}")

    @_traced
    def simd(self, cls: str, moves: Sequence[Sequence], mode: str = "inter",
             broadcast: bool | str = False, **meta) -> "Program":
        """One machine step, FLAT: `with p.cycle(cls, mode) as c: c.moves(moves)`.

        This exists because the browser's Python-subset grammar joins physical lines on
        BRACKET DEPTH ALONE -- Python's own rule, and exactly what admits the multi-line
        `set_curve(name, [\n dict(...),\n])` the emitter produces.  Teaching it `with`
        and indented suites would put a second, competing line-joining rule in the same
        function and the byte-for-byte `render(parse(x)) == x` assertion would be the
        first casualty.

        So the browser writes `p.simd(...)`, and this is what makes that text mean the
        same thing when it is pasted into a `.py`.  Text that PARSES and does something
        different in Python is strictly worse than text that does not parse -- and
        `p.cycle("dock", moves=[...])` would be accepted today (it swallows `**meta`),
        return an un-entered context manager and emit NOTHING: a silently empty program
        that still prices.

        Each move is `(ion, src, dst)` or `(ion, src, dst, via)`.
        """
        for mv in moves:
            self._route(str(mv[0]), str(mv[1]), str(mv[2]),
                        mv[3] if len(mv) > 3 else ())
        with self.cycle(cls, mode, broadcast=broadcast, **meta) as c:
            c.moves(moves)
        return self

    @_traced
    def move(self, ion: str, src: str, dst: str, via: Sequence[str] | None = None,
             cls: str = "shuttle", broadcast: bool | str = False, **meta) -> "Program":
        """One ion, one step -- shorthand for a cycle with a single participant."""
        self._route(ion, src, dst, via)
        with self.cycle(cls, broadcast=broadcast, **meta) as c:
            c.move(ion, src, dst, via)
        return self

    @_traced
    def shuttle(self, ion: str, path: Sequence[str], cls: str = "shuttle",
                **meta) -> "Program":
        """Walk one ion along a node path, one trap-to-trap step per cycle.

        Intermediate junctions are transited, never rested on: a hop from trap to trap
        crossing a junction is one cycle with the segments in `via`.
        """
        dev = self._m.arch.device
        stops = [n for n in path if dev.nodes[n].kind == "site"]
        idx = {n: i for i, n in enumerate(path)}
        for a, b in zip(stops, stops[1:]):
            hop = list(path[idx[a]:idx[b] + 1])
            via = [dev.segment_between(u, v).id for u, v in zip(hop, hop[1:])]
            self.move(ion, a, b, via=via, cls=cls, **meta)
        return self

    # -- gates, SPAM, cooling ------------------------------------------------

    @_traced
    def gate(self, name: str, pairs: Sequence[Sequence[str]],
             sites: Sequence[str] | None = None, broadcast: bool | str = False,
             **meta) -> "Program":
        """One or more co-located pairs, driven together.

        `broadcast="one"` is the OPTICAL claim: one beam, or one splitter tree, lights
        every named zone.  R4c checks it against `control.optical`.
        """
        pp = tuple((a, b) for a, b in pairs)
        self._prog.add(Instruction(
            type="gate", id=self._prog.next_id(), gate=name, mode="intra",
            pairs=pp, sites=tuple(sites or ()), broadcast=broadcast,
            meta=self._meta(meta)))
        return self

    @_traced
    def cool(self, ions: Sequence[str] | None = None, broadcast: bool | str | None = None,
             **meta) -> "Program":
        """Cooling.  With no ions it is a global broadcast -- one op, every ion (R7c).

        `broadcast` defaults to `not ions`, which is the spelling every shipped program
        writes and serializes as `true`; pass `"one"` to say it in the R4c vocabulary.
        """
        self._prog.add(Instruction(
            type="cool", id=self._prog.next_id(), ions=tuple(ions or ()),
            broadcast=(not ions) if broadcast is None else broadcast,
            meta=self._meta(meta)))
        return self

    @_traced
    def measure(self, ions: Sequence[str], broadcast: bool | str = False,
                **meta) -> "Program":
        self._prog.add(Instruction(type="measure", id=self._prog.next_id(),
                                   ions=tuple(ions), broadcast=broadcast,
                                   meta=self._meta(meta)))
        return self

    @_traced
    def reset(self, ions: Sequence[str], broadcast: bool | str = False,
              **meta) -> "Program":
        self._prog.add(Instruction(type="reset", id=self._prog.next_id(),
                                   ions=tuple(ions), broadcast=broadcast,
                                   meta=self._meta(meta)))
        return self

    @_traced
    def barrier(self, **meta) -> "Program":
        self._prog.add(Instruction(type="barrier", id=self._prog.next_id(),
                                   meta=self._meta(meta)))
        return self

    # -- claims and output ---------------------------------------------------

    def claim(self, **metrics) -> "Program":
        """Assert what this program should cost.  R9 checks it against the replay."""
        self._prog.metrics.update(metrics)
        return self

    def apply_calls(self, records: "Sequence[Mapping]") -> "Program":
        """Replay a list of `{method, args, kwargs}` program records.

        THE ROUND-TRIP CLOSER, mirroring `Machine.apply_edits`: a program authored in the
        browser can be re-executed by the real toolchain and re-verified.  A WHITELIST
        DISPATCHER, never `getattr` on arbitrary text and never `exec` -- `PROGRAM_METHODS`
        is the vocabulary and nothing outside it can be named.
        """
        for i, rec in enumerate(records):
            method = str(rec.get("method", ""))
            if method not in PROGRAM_METHODS:
                raise ValueError(
                    f"statement {i}: {method!r} is not a program method "
                    f"(have: {', '.join(PROGRAM_METHODS)})")
            fn = getattr(self, method)
            try:
                fn(*list(rec.get("args", ())), **dict(rec.get("kwargs", {})))
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError(f"statement {i} ({method}): {exc}") from None
        return self

    def build(self) -> TSIR:
        return self._prog

    def listing(self, **kw):
        """Disassemble this program.  See `qccd.ir.listing.disassemble`."""
        from .ir.listing import disassemble

        return disassemble(self._prog, self._m.arch, **kw)

    @property
    def tsir(self) -> TSIR:
        return self._prog

    def __len__(self) -> int:
        return len(self._prog)

    def run(self, **kw) -> "RunResult":
        return self._m.run(self, **kw)

    def render(self, path, **kw) -> Path:
        return self._m.render(self, path, **kw)

    def save(self, path) -> Path:
        return self._prog.save(path, indent=1)

    def __repr__(self) -> str:
        return f"<Program {self._prog.name!r} on {self._m.name}: {len(self._prog)} instr>"


class _CycleCtx:
    def __init__(self, prog: Program, cycle: Cycle, meta: dict,
                 call: int | None = None):
        self._p, self._c, self._meta = prog, cycle, meta
        self._call = call

    def __enter__(self) -> Cycle:
        return self._c

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            return False
        if not self._c._moves:
            raise ValueError(f"cycle {self._c.cls!r} moves nothing")
        self._p._prog.add(Instruction(
            type="simd", id=self._p._prog.next_id(), cls=self._c.cls,
            mode=self._c.mode, participants=tuple(self._c._moves),
            broadcast=self._c.broadcast,
            meta=self._tag(dict(self._meta, **self._c.meta))))
        return False

    def _tag(self, meta: dict) -> dict:
        if self._call is not None:
            meta[CALL_KEY] = self._call
        return meta


# ------------------------------------------------------------------------- results


@dataclass
class RunResult:
    """What a run produced.  The verifier's own numbers, not a summary of them."""

    report: VerificationReport
    machine: "Machine"
    program: TSIR
    model: CostModel

    @property
    def cost(self) -> float:
        return self.report.result.total_cost

    @property
    def steps(self) -> int:
        return self.report.result.total_steps

    @property
    def runtime_ms(self) -> float:
        return self.report.result.total_us / 1000.0

    @property
    def peak_quanta(self) -> float:
        return self.report.result.peak_quanta

    @property
    def rules_passed(self) -> list[str]:
        return self.report.rules.passed()

    @property
    def rules_failed(self) -> list[str]:
        return sorted(self.report.rules.failed())

    @property
    def violations(self) -> list:
        return self.report.rules.violations

    @property
    def ok(self) -> bool:
        return self.report.ok()

    def quanta_per_ion(self, prefix: str = "d") -> dict[str, float]:
        return t2_metrics(self.machine.arch, self.report.result,
                          self.model, data_ion_prefix=prefix).quanta_per_data_ion

    def t1(self):
        return t1_metrics(self.program, self.machine.arch, self.report.result)

    def t2(self):
        return t2_metrics(self.machine.arch, self.report.result, self.model)

    def summary(self) -> dict:
        return self.report.summary()

    def __repr__(self) -> str:
        bad = f" FAILED {self.rules_failed}" if self.rules_failed else " all rules pass"
        t = f", {self.runtime_ms:.2f} ms" if self.model.models_time else ""
        return (f"<RunResult cost={self.cost:,.0f} steps={self.steps:,}{t};{bad}>")


# ------------------------------------------------------------------------- machine


@dataclass
class Machine:
    """A device you can retune and program, in Python."""

    arch: Architecture
    spec_path: str = ""

    # -- construction --------------------------------------------------------

    @classmethod
    def load(cls, path) -> "Machine":
        return cls(_load_arch(path), spec_path=str(path))

    @classmethod
    def from_device(cls, device: Device, *, name: str = "custom",
                    template: str | Mapping | None = None, **kw) -> "Machine":
        """Wrap an expanded `Device` in a full architecture, borrowing a template's
        zone types, primitive curves and control block so you only describe geometry."""
        doc = _template_doc(template)
        doc = dict(doc)
        doc["name"] = name
        doc["geometry"] = _geometry_of(device, name)
        doc.update(kw)
        return cls(Architecture.from_json(doc))

    @classmethod
    def _gen(cls, fn, name, template, kwargs) -> "Machine":
        doc = dict(_template_doc(template))
        doc["name"] = name
        doc["geometry"] = {"generator": fn, "params": kwargs}
        return cls(Architecture.from_json(doc))

    @classmethod
    def blank(cls, generator: str, *, name: str = "custom",
              zones: "Sequence[str] | Mapping[str, Mapping]" = (),
              **params) -> "Machine":
        """A machine with NOTHING declared but the geometry.

        No zone capabilities, no primitive curves, no control block -- unlike
        `Machine.ring(...)`, which borrows all of that from a template.  `zones` names
        (or defines) the zone types the geometry references, because a generator cannot
        place a site in a zone that does not exist *at expansion time*; everything else
        is declared by the calls that follow.

        This is the constructor a self-contained architecture listing emits, and the
        reason it exists: a chained setter cannot declare a zone before the geometry
        call that puts sites in it has already run.
        """
        return cls(Architecture.from_json({
            "name": name, "schema_version": SCHEMA_VERSION,
            "zone_types": _zone_block(zones), "primitives": {},
            "geometry": {"generator": generator, "params": dict(params)}}))

    @classmethod
    def blank_device(cls, device: Device, *, name: str = "custom",
                     zones: "Sequence[str] | Mapping[str, Mapping]" = ()) -> "Machine":
        """`Machine.blank`, wrapping an explicit `Device` instead of a generator."""
        return cls(Architecture.from_json({
            "name": name, "schema_version": SCHEMA_VERSION,
            "zone_types": _zone_block(zones), "primitives": {},
            "geometry": _geometry_of(device, name)}))

    @classmethod
    def ring(cls, width, height=2, verticals=0, *, name="ring",
             template=None, **kw) -> "Machine":
        return cls._gen("ring", name, template,
                        dict(width=width, height=height, verticals=verticals, **kw))

    @classmethod
    def grid(cls, a, b, *, name="grid", template=None, **kw) -> "Machine":
        return cls._gen("grid", name, template, dict(a=a, b=b, **kw))

    @classmethod
    def chain(cls, n, *, name="chain", template=None, **kw) -> "Machine":
        return cls._gen("chain", name, template, dict(n=n, **kw))

    @classmethod
    def ladder(cls, width, rungs=None, highways=0, *, name="ladder",
               template=None, **kw) -> "Machine":
        return cls._gen("ladder", name, template,
                        dict(width=width, rungs=rungs, highways=highways, **kw))

    @classmethod
    def racetrack(cls, straight, *, name="racetrack", template=None, **kw) -> "Machine":
        return cls._gen("racetrack", name, template, dict(straight=straight, **kw))

    @classmethod
    def dual_loop(cls, width, couplings=None, *, name="dual_loop",
                  template=None, **kw) -> "Machine":
        return cls._gen("dual_loop", name, template,
                        dict(width=width, couplings=couplings, **kw))

    # -- retuning ------------------------------------------------------------

    def set_zone(self, zone: str, **fields) -> "Machine":
        """Change a zone type -- capacity, or what may happen there (R1, R6)."""
        zones = {k: dict(v) for k, v in self.arch.zone_types.items()}
        zones.setdefault(zone, {"capacity": 1})
        zones[zone].update(fields)
        # sites that inherited their capacity from this zone follow it
        self._rebuild(zone_types=zones,
                      device=resolve_capacities(self.arch.device, zones))
        return self

    def set_site_capacity(self, sites, capacity: int) -> "Machine":
        """Set how many ions ONE site (or a named group) may hold.

        This overrides the zone type for those sites only, and survives later
        `set_zone` calls -- a trap you deliberately made bigger should not silently
        shrink when its zone is retuned. R1 then checks the per-site number, and a full
        site blocks transport through it.

            m.set_site_capacity("S0", 8)                    # one site
            m.set_site_capacity(["S0", "S6"], 4)            # several
            m.set_site_capacity(m.sites("dock"), 4)         # every dock
        """
        if capacity < 1:
            raise ValueError("a site must be able to hold at least one ion")
        # `int(...)`: `set_site_capacity("S0", 4.0)` used to store a float, pass this
        # guard, and then fail on save with `sites[0].capacity: expected integer, got
        # number`.  A schema failure hours away from the click that caused it.
        capacity = int(capacity)
        names = [sites] if isinstance(sites, str) else list(sites)
        nodes = dict(self.arch.device.nodes)
        for nid in names:
            if nid not in nodes:
                raise ValueError(f"no such site {nid!r} on {self.name}")
            if nodes[nid].kind != "site":
                raise ValueError(f"{nid!r} is a junction; it holds no ions")
            nodes[nid] = replace(nodes[nid], capacity=capacity, capacity_explicit=True)
        self._rebuild(device=Device(
            nodes=nodes, segments=self.arch.device.segments,
            loops=self.arch.device.loops, generator=self.arch.device.generator,
            params=self.arch.device.params))
        return self

    def set_segment_length(self, segments, length: float) -> "Machine":
        """Set the physical length of a segment, in trap-pitch units.

        Only the cost models that opt into `length_scaling=True` read it, so changing a
        length is inert until you ask for it -- which is deliberate: the shipped oracles
        were calibrated on a unit-pitch device and must keep reproducing exactly.

            m.set_segment_length("S0-S1", 4.0)             # one segment
            m.set_segment_length(long_spurs, 2.5)          # several
            m.run(prog, model=corrected_model(length_scaling=True))
        """
        if length <= 0:
            raise ValueError("a segment must have positive length")
        names = [segments] if isinstance(segments, str) else list(segments)
        segs = dict(self.arch.device.segments)
        for sid in names:
            if sid not in segs:
                raise ValueError(f"no such segment {sid!r} on {self.name}")
            segs[sid] = replace(segs[sid], length=float(length))
        self._rebuild(device=Device(
            nodes=self.arch.device.nodes, segments=segs,
            loops=self.arch.device.loops, generator=self.arch.device.generator,
            params=self.arch.device.params))
        return self

    def length_of(self, segment: str) -> float:
        return self.arch.device.segments[segment].length

    def capacity_of(self, site: str) -> int:
        return self.arch.device.nodes[site].capacity

    def set_curve(self, primitive: str, points: Sequence[Sequence[float] | Mapping],
                  table: str = "local", source: str | None = None) -> "Machine":
        """Replace a primitive's `(duration, quanta)` curve.

        Points are `(us, quanta)` pairs or dicts. Tag them with the `table` they were
        measured in -- the whole stack can re-run against a named table, which is how
        ranking stability under parameter uncertainty gets measured (PLAN §3.2).
        """
        pts = []
        for p in points:
            if isinstance(p, Mapping):
                # `float(...)` on BOTH branches.  The tuple branch already coerced and the
                # mapping branch did not, so `set_curve("split", [{"us": 5, ...}])` stored
                # an int and `json.dumps` wrote `5` where the tuple form wrote `5.0`.  JS
                # has one number type and cannot reproduce that difference, so the exported
                # `.arch.json` would differ byte for byte between the two implementations.
                # Fixed here rather than papered over in the comparator.
                d = {"table": table, "source": source, **dict(p)}
                d["us"] = float(d["us"])
                d["quanta"] = float(d["quanta"])
                pts.append(CurvePoint(**d))
            else:
                us, q = p
                pts.append(CurvePoint(us=float(us), quanta=float(q), table=table,
                                      source=source))
        curves = dict(self.arch.primitives.curves)
        curves[primitive] = Curve(tuple(pts))
        self._rebuild(primitives=replace(self.arch.primitives, curves=curves))
        return self

    def set_primitive(self, primitive: str, **fields) -> "Machine":
        """Set the scalar fields of a primitive (`ms_gate.max_quanta`, `cool.us`, ...)."""
        scalars = {k: dict(v) for k, v in self.arch.primitives.scalars.items()}
        scalars.setdefault(primitive, {}).update(fields)
        self._rebuild(primitives=replace(self.arch.primitives, scalars=scalars))
        return self

    def declare_class(self, class_id: str, **fields) -> "Machine":
        """Declare a SIMD movement class.  R4 requires a cycle's class to exist."""
        control = {k: v for k, v in self.arch.control.items()}
        classes = dict(control.get("classes", {}) or {})
        extra = [dict(e) for e in classes.get("extra", []) or []
                 if e.get("id") != class_id]
        extra.append({"id": class_id, **fields})
        classes["extra"] = extra
        control["classes"] = classes
        self._rebuild(control=control)
        return self

    def set_wiring(self, **fields) -> "Machine":
        """Change the control wiring -- the scheme, DAC counts, electrodes per trap."""
        control = {k: v for k, v in self.arch.control.items()}
        control["wiring"] = dict(control.get("wiring", {}) or {}, **fields)
        self._rebuild(control=control)
        return self

    def set_control(self, **fields) -> "Machine":
        """Set a top-level `control` block: model, channels, optical, classes metadata,
        max_simd_classes_per_cycle, intra_inter_exclusive.

        A Mapping value merges one level deep, so `set_control(classes={"count": 18})`
        leaves the `extra` list `declare_class` maintains untouched.  A full replacement
        would destroy every declared class, which is why the merge is not optional.
        """
        control = {k: v for k, v in self.arch.control.items()}
        for k, v in fields.items():
            if isinstance(v, Mapping) and isinstance(control.get(k), Mapping):
                control[k] = dict(control[k], **dict(v))
            else:
                control[k] = v
        self._rebuild(control=control)
        return self

    def set_degree_curve(self, primitive: str, degree: int,
                         points: Sequence[Sequence[float] | Mapping],
                         table: str = "local",
                         source: str | None = None) -> "Machine":
        """Replace the curve charged at nodes of one degree (R18's junction term)."""
        pts = []
        for p in points:
            if isinstance(p, Mapping):
                # `float(...)` on BOTH branches.  The tuple branch already coerced and the
                # mapping branch did not, so `set_curve("split", [{"us": 5, ...}])` stored
                # an int and `json.dumps` wrote `5` where the tuple form wrote `5.0`.  JS
                # has one number type and cannot reproduce that difference, so the exported
                # `.arch.json` would differ byte for byte between the two implementations.
                # Fixed here rather than papered over in the comparator.
                d = {"table": table, "source": source, **dict(p)}
                d["us"] = float(d["us"])
                d["quanta"] = float(d["quanta"])
                pts.append(CurvePoint(**d))
            else:
                us, q = p
                pts.append(CurvePoint(us=float(us), quanta=float(q), table=table,
                                      source=source))
        dcs = dict(self.arch.primitives.degree_curves)
        by = dict(dcs[primitive].by_degree) if primitive in dcs else {}
        by[int(degree)] = Curve(tuple(pts))
        dcs[primitive] = DegreeCurve(by)
        self._rebuild(primitives=replace(self.arch.primitives, degree_curves=dcs))
        return self

    def set_budget(self, **fields) -> "Machine":
        """Set the resource budget the utilization report is checked against."""
        self._rebuild(budget=dict(self.arch.budget, **fields))
        return self

    def set_species(self, **fields) -> "Machine":
        """Set the ion species block (qubit, coolant, coherence time)."""
        self._rebuild(species=dict(self.arch.species, **fields))
        return self

    def describe(self, description: str = "", **provenance) -> "Machine":
        """Set the prose description and merge into the provenance block."""
        self._rebuild(description=description or None,
                      provenance=dict(self.arch.provenance, **provenance))
        return self

    def move_site(self, site: str, *pos: float) -> "Machine":
        """Move one node.  THE DRAG OPERATION.

        Note it does not touch the lengths of the incident segments: length is an
        independently declared property (only `length_scaling` cost models read it), so
        a design tool that wants drag to change cost has to call `set_segment_length`
        itself.  After this the device is no longer reproducible from its generator, and
        the architecture listing switches from `generator` to `patch` mode.
        """
        if site not in self.arch.device.nodes:
            raise ValueError(f"no such node {site!r} on {self.name}")
        nodes = dict(self.arch.device.nodes)
        nodes[site] = replace(nodes[site], pos=tuple(float(c) for c in pos))
        d = self.arch.device
        self._rebuild(device=Device(nodes=nodes, segments=d.segments, loops=d.loops,
                                    generator=d.generator, params=d.params))
        return self

    # -- topology ------------------------------------------------------------
    #
    # `move_site` drags a node that exists; these change WHICH nodes exist.  Every one
    # goes through `qccd.arch.edit`, which is mirrored in `qccd/viz/js/edit.js` for the
    # in-browser editor and held to that mirror by `tests/test_edit_parity.py`.  The
    # Machine layer adds exactly three things the pure device functions cannot do:
    # resolve a new site's capacity against THIS architecture's zone types, prune the
    # control block's explicit channel map, and refuse a disconnection unless asked.
    #
    # After any of these the device is no longer reproducible from its generator AND no
    # longer patchable, so `architecture_listing` switches to `explicit` mode and emits
    # every node, segment and loop.  That is the honest serialization: the generator's
    # parameters no longer describe the graph.

    def _apply_edit(self, op: str, args: Mapping, *,
                    allow_disconnect: bool = False):
        dev, rep = _edit.apply_edit(self.arch.device, {"op": op, "args": dict(args)})
        if rep.disconnects and not allow_disconnect:
            sizes = ", ".join(str(len(c)) for c in rep.components_after)
            raise _edit.TopologyError(
                f"{op} would split {self.name} into {len(rep.components_after)} "
                f"disconnected pieces ({sizes} nodes); no ion could move between them. "
                f"Pass allow_disconnect=True if that is what you meant")
        control = self._prune_control(rep)
        self._rebuild(device=resolve_capacities(dev, self.arch.zone_types),
                      **({"control": control} if control is not None else {}))
        return replace(rep, warnings=rep.warnings + tuple(self._orbit_warnings(rep)))

    def _orbit_warnings(self, rep) -> list[str]:
        """Declared movement classes a topology edit just killed or emptied.

        `declare_class("rotate_cw", type="shift", orbit="L0")` names a loop that a
        topology edit can delete or open, and nothing downstream complains: the class
        stays declared, `class_participants` quietly returns the empty tuple, and the
        listing prints "0 site(s) may participate".  A program that then asks for
        `p.rotate(+1)` fails at `p.fill("L0")` with a bare KeyError, hundreds of lines
        from the click that caused it.  So the edit says it at the moment it happens.
        """
        from .arch.listing import class_participants

        out: list[str] = []
        gone = set(rep.loops_removed)
        opened = set(rep.loops_opened)
        for cid, spec in self.arch.simd_classes.items():
            orbit = str(spec.get("orbit", "any"))
            if orbit in gone:
                out.append(
                    f"movement class {cid!r} has orbit {orbit!r}, which this edit "
                    f"deleted; it can no longer run")
            elif orbit in opened and str(spec.get("type", "")) == "shift":
                out.append(
                    f"movement class {cid!r} is a rigid shift on {orbit!r}, which this "
                    f"edit left OPEN; shift_map refuses an open loop, so the class can "
                    f"no longer run")
            elif not class_participants(self.arch, cid):
                out.append(
                    f"movement class {cid!r} (orbit {orbit!r}) now has no participants")
        return out

    def _prune_control(self, rep) -> dict | None:
        """Drop removed sites from an EXPLICIT channel map.

        Every other grouping (`direct`, `broadcast`, `row`, `column`) derives its channels
        from the device, so it follows a topology edit for free.  `explicit` lists site ids
        by hand, and a dangling id there would silently give a channel a member that no
        longer exists -- which `ControlPlane` counts, so the DAC budget would drift.
        """
        if not rep.nodes_removed:
            return None
        spec = dict(self.arch.control.get("channels", {}) or {})
        if str(spec.get("grouping", "direct")) != "explicit" or not spec.get("explicit"):
            return None
        gone = set(rep.nodes_removed)
        entries = []
        for e in spec.get("explicit", ()):
            drives = [d for d in e.get("drives", ()) if d not in gone]
            if drives:
                entries.append(dict(e, drives=drives))
        spec["explicit"] = entries
        return dict(self.arch.control, channels=spec)

    def add_site(self, site: str, *pos: float, zone: str | None = None,
                 capacity: int = 0, labels: Sequence[str] = (),
                 on: str | None = None, to: Sequence[str] = (),
                 segment_ids: Sequence[str] | None = None,
                 allow_disconnect: bool = False) -> "Machine":
        """Add one trap site.  THE PLACE OPERATION.

        Three gestures::

            m.add_site("S72b", 3.5, 0.0, zone="data", on="E3")      # split a rail in two
            m.add_site("A99", 5.0, 1.0, zone="ancilla", to=["S5"])  # a dock on a spur
            m.add_site("F0", 9.0, 9.0, zone="data", capacity=4)     # free-standing

        `on=` is the only gesture that can put a node ON a transport loop, because it is
        the only one with an unambiguous place in the loop's ORDERED node list: the new
        node is spliced between the subdivided segment's endpoints, in every loop that
        walked that pair.  Get that wrong and `rotate(+1)` prices an orbit that skips the
        new trap -- a wrong number with no error attached.  The subdivided segment's
        declared length is split at the projection of `pos` onto it, so length is
        conserved.

        `to=` wires the new node to existing ones with plain spur segments off every loop.
        Each raises the far node's degree, and a rail node going from 2 to 3 becomes an
        R18 junction that the architecture must be able to price.

        Neither leaves an isolated node, which is legal and is what a two-step "place,
        then wire" gesture looks like between clicks; `topology_report()` says so.

        `capacity=0` inherits from `zone`, which must already be declared -- see
        `set_zone`.
        """
        self._apply_edit("add_site", {
            "id": site, "pos": [float(c) for c in pos], "zone": zone,
            "capacity": capacity, "labels": list(labels), "on": on, "to": list(to),
            "segment_ids": list(segment_ids) if segment_ids else None,
            "zone_types": {k: dict(v) for k, v in self.arch.zone_types.items()},
        }, allow_disconnect=allow_disconnect)
        return self

    def add_junction(self, node: str, *pos: float, labels: Sequence[str] = (),
                     on: str | None = None, to: Sequence[str] = (),
                     segment_ids: Sequence[str] | None = None,
                     allow_disconnect: bool = False) -> "Machine":
        """Add a bare junction -- no zone, no capacity, holds no ions.

        Being *called* a junction is not what makes R18 charge one; degree is.  A junction
        wired to fewer than three neighbours is priced as plain transport, and the edit
        report says so rather than leaving you to discover it in the cost.
        """
        self._apply_edit("add_junction", {
            "id": node, "pos": [float(c) for c in pos], "labels": list(labels),
            "on": on, "to": list(to),
            "segment_ids": list(segment_ids) if segment_ids else None,
        }, allow_disconnect=allow_disconnect)
        return self

    def add_segment(self, segment: str, a: str, b: str, *, loop: str | None = None,
                    labels: Sequence[str] = (), length: float = 1.0,
                    capacity: int = 1, allow_disconnect: bool = False) -> "Machine":
        """Join two existing nodes.  THE WIRE OPERATION.

        `loop=` is accepted only when `a` and `b` are already consecutive in that loop's
        node order.  A CHORD -- both ends on a loop but not adjacent -- is legal and stays
        off every loop, because `corner_endpoints` scores a segment by the corners of the
        loop it lies on, and a chord between two corners would be charged as though it
        contained a whole turn.  It does not; it is a shortcut.
        """
        self._apply_edit("add_segment", {
            "id": segment, "a": a, "b": b, "loop": loop, "labels": list(labels),
            "length": length, "capacity": capacity,
        }, allow_disconnect=allow_disconnect)
        return self

    def remove_node(self, node: str, *, mend: str = "splice", cascade: bool = False,
                    allow_disconnect: bool = False) -> "Machine":
        """Delete one node and every segment on it.  THE DELETE OPERATION.

        `mend="splice"` (default) closes the transport loop over the gap: the node's two
        loop edges become ONE segment joining its neighbours, carrying the SUM of their
        lengths, and the node leaves the ordered list.  The orbit shrinks by one and every
        remaining consecutive pair is still a real segment, so a rigid rotation still
        prices.  `mend="open"` cuts the loop open there instead; `mend="delete"` drops the
        loop.  Spurs and couplings are simply deleted, and `cascade=True` also removes any
        node they left with no segment at all.
        """
        self._apply_edit("remove_node", {"id": node, "mend": mend, "cascade": cascade},
                         allow_disconnect=allow_disconnect)
        return self

    def remove_segment(self, segment: str, *, on_loop: str = "refuse",
                       allow_disconnect: bool = False) -> "Machine":
        """Delete one segment.  THE CUT OPERATION.

        A segment whose endpoints are consecutive in a loop's node order is an edge of
        that loop's walk, and deleting it makes `loop_segments` raise on the next
        rotation.  So it is refused by default; `on_loop="open"` cuts the loop open there
        (rotating the node list so the seam falls exactly where the segment was, and
        killing the rigid shift, which `shift_map` refuses on an open loop), and
        `on_loop="delete"` drops the loop and clears `Segment.loop` on everything that
        pointed at it.
        """
        self._apply_edit("remove_segment", {"id": segment, "on_loop": on_loop},
                         allow_disconnect=allow_disconnect)
        return self

    def topology_report(self) -> dict:
        """Connectivity, isolated nodes and the degree histogram, as one dict.

        The questions an editor asks after every edit that the rule set does not:
        `architecture_violations` checks that every junction the graph made is priceable
        (R11/R18/R19), but nothing in R1-R19 notices that a device fell into two islands or
        that a trap has no segment on it.  Both are legal, and both are almost always a
        misplaced click.
        """
        dev = self.arch.device
        comps = _edit.components(dev)
        return {
            "n_components": len(comps),
            "components": [list(c) for c in comps] if len(comps) > 1 else [],
            "isolated": sorted(n for n in dev.nodes if dev.degree(n) == 0),
            "degree_histogram": dev.summary()["degree_histogram"],
            "open_loops": sorted(lid for lid, lp in dev.loops.items() if not lp.closed),
            "reproducible_from_generator": dev.reproducible_from_generator(),
        }

    def violations(self) -> list:
        """The architecture-level rule checks (R11/R18/R19), as `Violation` records.

        A topology edit is the only thing that can turn these from empty to non-empty
        without touching the program, so an editor calls this after every edit.
        """
        from .verify.rules import architecture_violations
        return architecture_violations(self.arch)

    def only_zones(self, *names: str) -> "Machine":
        """Drop every zone type not named -- a template's leftovers."""
        keep = set(names)
        zones = {k: dict(v) for k, v in self.arch.zone_types.items() if k in keep}
        self._rebuild(zone_types=zones,
                      device=resolve_capacities(self.arch.device, zones))
        return self

    def only_classes(self, *ids: str) -> "Machine":
        """Drop every declared movement class not named."""
        keep = list(ids)
        control = {k: v for k, v in self.arch.control.items()}
        classes = dict(control.get("classes", {}) or {})
        extra = [dict(e) for e in classes.get("extra", []) or [] if e.get("id") in keep]
        extra.sort(key=lambda e: keep.index(e["id"]))
        classes["extra"] = extra
        control["classes"] = classes
        self._rebuild(control=control)
        return self

    def only_primitives(self, *names: str) -> "Machine":
        """Drop every primitive curve, degree curve and scalar not named."""
        keep = set(names)
        p = self.arch.primitives
        self._rebuild(primitives=replace(
            p,
            curves={k: v for k, v in p.curves.items() if k in keep},
            degree_curves={k: v for k, v in p.degree_curves.items() if k in keep},
            scalars={k: v for k, v in p.scalars.items() if k in keep}))
        return self

    def set_heating(self, **fields) -> "Machine":
        """Change the R17 background rate."""
        self._rebuild(heating=dict(self.arch.heating, **fields))
        return self

    def apply_edits(self, ops: "Sequence[Mapping]") -> "Machine":
        """Replay a browser edit list.  THE ROUND-TRIP CLOSER.

        The page's editor represents every gesture -- a drag, a typed statement, an added
        segment -- as one record in the same shape `ArchLine.call` already uses, and its
        "Copy edits" button hands them over as JSON.  This is the one-line Python entry
        point that replays them, so an edit made in the browser can be re-executed by the
        real toolchain and then re-verified:

            m = Machine.load("arch/ring144_24v.arch.json")
            m.apply_edits(json.loads(edits_from_the_browser))
            m.run(prog)

        Two record shapes, because there are two kinds of edit and they route through two
        different whitelists.  A COMMAND edit carries `method` and goes through the same
        setter the listing emits.  A TOPOLOGY edit carries `topology` and goes through
        `qccd.arch.edit.apply_edit`, whose JS mirror the parity test diffs edit by edit.
        Neither path can name a Python attribute, and neither is `exec` -- `rebuild()`'s
        own docstring is explicit that a design tool must use a method whitelist instead
        of executing user-edited text.
        """
        from .arch.edit import MUTATE, EditError, apply_edit

        for i, op in enumerate(ops):
            if "topology" in op:
                device, _report = apply_edit(self.arch.device, op["topology"])
                self._rebuild(device=device)
                continue
            method = str(op.get("method", ""))
            if method not in MUTATE:
                raise EditError(
                    "unknown_method", method,
                    f"{method!r} is not an editable method; the browser may only emit "
                    f"{', '.join(sorted(MUTATE))}", i)
            try:
                getattr(self, method)(*op.get("args", ()), **op.get("kwargs", {}))
            except (ValueError, KeyError, TypeError) as exc:
                raise EditError(type(exc).__name__, method, str(exc), i) from None
        return self

    def _rebuild(self, **fields) -> None:
        self.arch = replace(self.arch, **fields)

    # -- inspection ----------------------------------------------------------

    @property
    def name(self) -> str:
        return self.arch.name

    @property
    def device(self) -> Device:
        return self.arch.device

    @property
    def default_loop(self) -> str:
        closed = [lid for lid, lp in self.arch.device.loops.items() if lp.closed]
        if closed:
            return closed[0]
        if self.arch.device.loops:
            return next(iter(self.arch.device.loops))
        raise ValueError(f"{self.name} has no transport loop")

    def summary(self) -> dict:
        return self.arch.device.summary()

    def resources(self) -> HardwareReport:
        """The utilization report: electrodes, switches, DACs, and the budget check."""
        return hardware_report(self.arch)

    def sites(self, label: str | None = None) -> list[str]:
        nodes = self.arch.device.nodes.values()
        if label:
            return [n.id for n in nodes if n.has(label)]
        return [n.id for n in nodes if n.kind == "site"]

    def junctions(self) -> tuple[str, ...]:
        return self.arch.device.junction_nodes

    def listing(self, **kw):
        """This architecture as a structured, round-trippable program listing."""
        from .arch.listing import architecture_listing

        return architecture_listing(self.arch, **kw)

    def source(self, **kw) -> str:
        """The Python that rebuilds this architecture."""
        from .arch.listing import architecture_listing

        return architecture_listing(self.arch, mode=kw.pop("mode", "full"),
                                    **kw).python()

    def save(self, path, *, expanded: bool = False) -> Path:
        p = _save_arch(self.arch, path, expanded=expanded)
        self.spec_path = str(p)
        return p

    # -- programming ---------------------------------------------------------

    def program(self, name: str = "program", *,
                provenance: str = "calls") -> Program:
        """A new program.

        Provenance is on by default and costs ~2 us per instruction -- 1 ms on a
        2000-instruction build against 7.9 s to verify it, four orders of magnitude
        below the noise floor.  `provenance="off"` opts out; nothing else changes.
        """
        return Program(self, name, provenance=provenance)

    def run(self, program: "Program | TSIR", *, model: "str | CostModel" = "corrected",
            table: str = "qccdsim_jones", cool: bool = False,
            check_metrics: bool = True) -> RunResult:
        """Replay, rule-check and score.  This is the verifier, not a shortcut past it."""
        prog = program.build() if isinstance(program, Program) else program
        m = _resolve_model(model, table)
        if cool:
            from .compile import CoolingPolicy, insert_cooling

            if not m.models_heating:
                raise ValueError(
                    f"the {m.name} model does not model heating, so there is nothing "
                    f"for a cooling pass to schedule")
            prog = insert_cooling(prog, self.arch, m, policy=CoolingPolicy()).program
        return RunResult(verify(prog, self.arch, m, check_metrics=check_metrics),
                         self, prog, m)

    def render(self, program: "Program | TSIR", path, *,
               model: "str | CostModel" = "corrected", table: str = "qccdsim_jones",
               **kw) -> Path:
        from .viz import render_html

        prog = program.build() if isinstance(program, Program) else program
        m = _resolve_model(model, table)
        res = self.run(prog, model=m, check_metrics=False).report.result
        return render_html(self.arch, prog, res, m, path, **kw)

    def compile(self, code, *, policy=None, model: "str | CostModel" = "corrected"):
        """Run the PLAN §7 pipeline: a code in, a verified program out."""
        from .compile.pipeline import compile_code

        return compile_code(self.arch, code, policy=policy)

    def __repr__(self) -> str:
        s = self.summary()
        return (f"<Machine {self.name!r} {s['generator']}: {s['n_nodes']} nodes, "
                f"{s['n_junction_nodes']} junctions, {self.resources().dacs} DACs>")


def _geometry_of(device: Device, name: str) -> dict:
    """`device.to_json()`, but refusing a dangling id in words rather than a `KeyError`.

    `Device.to_json` computes `all_corners`, which walks every loop and reads
    `self.nodes[nxt].pos`.  A loop naming a node that does not exist therefore died as a
    bare `KeyError: 'ZZ'` from `Device.corners` -- BEFORE `Architecture.from_json` got to
    `check_structure`, which has held the right sentence all along.  A design tool cannot
    show a user `KeyError: 'ZZ'` and call it a diagnosis, and `qccd.arch.edit.apply_call`
    is a dispatcher: it cannot be more careful than the constructor it dispatches into.
    So the constructor is careful here, in the same words and the same shape
    `Architecture.from_json` uses for every other structural refusal.
    """
    bad = device.dangling_references()
    if bad:
        raise ExpansionError(
            f"{len(bad)} structural error(s) in {name!r}:\n  " + "\n  ".join(bad))
    return device.to_json()


#: The authoring vocabulary, DERIVED from `Program` rather than listed beside it.
#:
#: `qccd/viz/engine.js` mirrors this as `PCALLS` and `tests/test_engine_parity.py` asserts
#: the two sets are equal, so the browser's parser cannot advertise a verb its lowerer
#: lacks -- a drift that no differential bucket could ever see, because Python would never
#: emit the verb in the first place.
PROGRAM_METHODS: tuple[str, ...] = tuple(sorted(
    name for name in vars(Program)
    if not name.startswith("_")
    and callable(vars(Program)[name])
    and name not in ("build", "listing", "run", "render", "save", "cycle", "apply_calls")
))


def _zone_block(zones) -> dict:
    """`zones=["data", "ancilla"]` or a full mapping -> a zone_types block.

    A bare name becomes a placeholder `{"capacity": 1}` that a later `set_zone` fills
    in; it exists only so the geometry call may reference the zone.
    """
    if isinstance(zones, Mapping):
        return {str(k): dict(v) for k, v in zones.items()}
    return {str(z): {"capacity": 1} for z in zones}


def _resolve_model(model, table: str) -> CostModel:
    if isinstance(model, CostModel):
        return model
    if model == "deck":
        return deck_model()
    if model == "corrected":
        return corrected_model(table)
    raise ValueError(f"unknown model {model!r}; use 'deck', 'corrected', or a CostModel")


_TEMPLATE_CACHE: dict[str, dict] = {}


def _template_doc(template) -> dict:
    """Zone types, primitive curves and control block to build a new machine on.

    Defaults to `arch/ring144_24v.arch.json`, so `Machine.ring(...)` gives you a device
    with the corpus's sourced primitive tables rather than an empty one you would have to
    populate before any number meant anything.
    """
    if isinstance(template, Mapping):
        return dict(template)
    key = str(template or DEFAULT_TEMPLATE)
    if key not in _TEMPLATE_CACHE:
        import json

        root = Path(__file__).resolve().parents[1]
        path = Path(key) if Path(key).exists() else root / "arch" / f"{key}.arch.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        for drop in ("geometry", "name", "description", "provenance"):
            doc.pop(drop, None)
        _TEMPLATE_CACHE[key] = doc
    return dict(_TEMPLATE_CACHE[key])
