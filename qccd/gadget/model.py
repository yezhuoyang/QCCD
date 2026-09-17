"""The gadget data model: masters, ports, ops, instances, channels.  GADGETS.md §2-3.

A **master** is a gadget definition and a **library** is a set of them.  A leaf master owns
a testbench device and one TSIR per op; a composite master owns instances of other masters
and the channels between their ports.  Instances never copy a master's programs: they add
a position and, at synthesis time, the global ids of their ions.  That sharing is the whole
scaling argument, so the model makes it structural -- an `Instance` has no field that could
hold a program.

Everything serialises to plain JSON (`qccd.gadget/0.1`), because the browser tool reads the
same documents Python writes.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

__all__ = [
    "SCHEMA", "ROLES", "DIRS", "SIDES", "STATUSES",
    "Port", "Transfer", "Op", "Instance", "Channel", "Master", "Library",
]

SCHEMA = "qccd.gadget/0.1"

#: What an ion crossing a port is FOR.  `data` bundles carry a code block's data qubits;
#: `messenger` ions carry a parity between blocks; `magic` ions carry a resource state;
#: `fresh` ions come from a loader.  `any` accepts all of them (a bus, a junction).
ROLES = ("data", "messenger", "magic", "fresh", "any")
DIRS = ("in", "out", "inout")
SIDES = ("N", "E", "S", "W")

#: What crosses a port: ions, or classical bits on a wire (GADGETS.md §10).
KINDS = ("ion", "classical")

#: What a classical port carries.  `syndrome` are raw check outcomes as a gadget measured
#: them, `outcome` a decoded logical measurement result, `frame` an update to a block's
#: Pauli (or Clifford) frame, `decision` a guard's value -- the bit a conditional op waits
#: for.  `any` accepts all of them.
SIGNALS = ("syndrome", "outcome", "frame", "decision", "any")

#: The reporting contract of docs/rules.md, one level up (GADGETS.md §2).
STATUSES = ("certified", "verified", "modeled", "failed")


@dataclass
class Port:
    name: str
    dir: str
    role: str
    width: int = 1
    side: str = "N"
    at: float = 0.5
    order: str = "fifo"
    max_quanta: float | None = None
    #: leaf only: the device node the port sits on, and the testbench stub beyond it
    node: str | None = None
    stub: str | None = None
    #: composite only: the child port this port exports, as "instance.port"
    bind: str | None = None
    #: data bundles: the code whose data ions they are (bundles of different codes don't pair)
    code: str | None = None
    #: `ion` or `classical`: a wire's port carries bits, not ions
    kind: str = "ion"
    #: classical only: what the bits mean (`SIGNALS`) and how many of them cross at once
    signal: str | None = None
    bits: int = 0

    def accepts(self, other: "Port") -> list[str]:
        """Why a channel from this port to `other` would be illegal (G1); empty if legal."""
        why = []
        if self.kind != other.kind:
            why.append(f"one port carries {self.kind}s, the other {other.kind}s")
        if self.kind == "classical" and other.kind == "classical":
            a, b = self.signal or "any", other.signal or "any"
            if "any" not in (a, b) and a != b:
                why.append(f"signals differ: {a} vs {b}")
            if self.dir == other.dir and self.dir != "inout":
                why.append(f"both ports are '{self.dir}'")
            return why
        if "any" not in (self.role, other.role) and self.role != other.role:
            why.append(f"roles differ: {self.role} vs {other.role}")
        if self.role == "data" and other.role == "data":
            if self.width != other.width:
                why.append(f"bundle widths differ: {self.width} vs {other.width}")
            if self.code and other.code and self.code != other.code:
                why.append(f"codes differ: {self.code} vs {other.code}")
        if self.dir == other.dir and self.dir != "inout":
            why.append(f"both ports are '{self.dir}'")
        return why


@dataclass
class Transfer:
    """One bundle crossing one port during one op, as the op's replay measured it."""

    port: str
    dir: str
    count: int
    t_first_us: float
    t_last_us: float
    quanta: float = 0.0
    #: local ion names in crossing order -- the bundle's declared order, made concrete
    ions: list[str] = field(default_factory=list)
    #: when each of those ions crossed, from op start (the per-ion timing arcs)
    times_us: list[float] = field(default_factory=list)


@dataclass
class Op:
    name: str
    title: str
    status: str
    duration_us: float = 0.0
    transfers: list[Transfer] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    rules: dict = field(default_factory=dict)
    #: what the op does NOT establish, in words (GADGETS.md §6.2's third column)
    notes: list[str] = field(default_factory=list)
    params: dict = field(default_factory=dict)
    #: leaf ops: the key of the op's TSIR inside the master's leaf data
    program: str | None = None
    #: composite ops: `[{"t": us, "inst": name, "op": op} | {"t": us, "channel": name}]`
    schedule: list[dict] | None = None
    #: what the op's circuit was checked to compute (`qccd.gadget.logic`): the spec, the
    #: flow report, the fault-distance report, and `status`
    logic: dict = field(default_factory=dict)

    def transfer(self, port: str, dir: str) -> Transfer | None:
        for t in self.transfers:
            if t.port == port and t.dir == dir:
                return t
        return None


@dataclass
class Instance:
    name: str
    master: str
    x: float = 0.0
    y: float = 0.0
    rot: int = 0


@dataclass
class Channel:
    """A transport link between two ports: `a` and `b` are "instance.port" or ".port".

    `kind` is `road` for a conveyor channel ions travel on, or `wire` for a classical
    link: a wire carries no ions and its cost is one number, `latency_us` -- the delay
    from the bit being available at `a` to it being usable at `b`."""

    name: str
    a: str
    b: str
    length: int = 1
    junctions: int = 0
    points: list[list[float]] = field(default_factory=list)
    role: str = "any"
    kind: str = "road"
    latency_us: float = 0.0


@dataclass
class Master:
    name: str
    kind: str
    family: str
    title: str = ""
    doc: str = ""
    size: list[float] = field(default_factory=lambda: [4.0, 4.0])
    #: the footprint in the master's own frame, `[x0, y0, x1, y1]` (model units)
    bbox: list[float] = field(default_factory=list)
    ports: list[Port] = field(default_factory=list)
    inventory: dict[str, int] = field(default_factory=dict)
    capacity: int = 0
    ops: dict[str, Op] = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    #: leaf: the key of the testbench device inside the leaf data
    device: str | None = None
    instances: list[Instance] = field(default_factory=list)
    channels: list[Channel] = field(default_factory=list)

    def port(self, name: str) -> Port:
        for p in self.ports:
            if p.name == name:
                return p
        raise KeyError(f"{self.name} has no port {name!r} "
                       f"(have: {', '.join(p.name for p in self.ports)})")

    def instance(self, name: str) -> Instance:
        for i in self.instances:
            if i.name == name:
                return i
        raise KeyError(f"{self.name} has no instance {name!r}")

    @property
    def ions_at_rest(self) -> int:
        return sum(self.inventory.values())

    # -- json ----------------------------------------------------------------

    def to_json(self) -> dict:
        d = asdict(self)
        if self.kind == "leaf":
            d.pop("instances")
            d.pop("channels")
        return d

    @staticmethod
    def from_json(d: dict) -> "Master":
        ops = {}
        for name, o in (d.get("ops") or {}).items():
            o = dict(o)
            o["transfers"] = [Transfer(**t) for t in o.get("transfers", [])]
            ops[name] = Op(**o)
        return Master(
            name=d["name"], kind=d["kind"], family=d["family"], title=d.get("title", ""),
            doc=d.get("doc", ""), size=list(d.get("size", [4.0, 4.0])),
            bbox=list(d.get("bbox", [])),
            ports=[Port(**p) for p in d.get("ports", [])],
            inventory=dict(d.get("inventory", {})), capacity=int(d.get("capacity", 0)),
            ops=ops, params=dict(d.get("params", {})), device=d.get("device"),
            instances=[Instance(**i) for i in d.get("instances", [])],
            channels=[Channel(**c) for c in d.get("channels", [])],
        )


@dataclass
class Library:
    masters: dict[str, Master] = field(default_factory=dict)
    top: str | None = None

    def add(self, master: Master) -> Master:
        self.masters[master.name] = master
        return master

    def __getitem__(self, name: str) -> Master:
        try:
            return self.masters[name]
        except KeyError:
            raise KeyError(f"no master {name!r} in the library "
                           f"(have: {', '.join(sorted(self.masters))})") from None

    def __contains__(self, name: str) -> bool:
        return name in self.masters

    # -- hierarchy -----------------------------------------------------------

    def walk(self, root: str | None = None, prefix: str = "") -> Iterable[tuple[str, Master]]:
        """Every instance path under `root`, depth first, with its master."""
        master = self[root or self.top]
        for inst in master.instances:
            path = f"{prefix}{inst.name}"
            child = self[inst.master]
            yield path, child
            if child.kind == "composite":
                yield from self.walk(child.name, prefix=path + ".")

    def resolve(self, path: str, root: str | None = None) -> Master:
        master = self[root or self.top]
        for part in path.split("."):
            master = self[master.instance(part).master]
        return master

    def leaf_masters(self, root: str | None = None) -> list[str]:
        seen: list[str] = []
        for _, m in self.walk(root):
            if m.kind == "leaf" and m.name not in seen:
                seen.append(m.name)
        return seen

    def ions_at_rest(self, root: str | None = None) -> int:
        top = self[root or self.top]
        if top.kind == "leaf":
            return top.ions_at_rest
        return sum(m.ions_at_rest for _, m in self.walk(root) if m.kind == "leaf")

    def port_of(self, composite: Master, ref: str) -> Port:
        """`"inst.port"` inside `composite`, or `".port"` for the composite's own port."""
        inst, _, port = ref.partition(".")
        if not inst:
            return composite.port(port)
        return self[composite.instance(inst).master].port(port)

    # -- checks --------------------------------------------------------------

    def structure_violations(self) -> list[str]:
        """G1 on the static design: every channel joins two real, compatible ports."""
        out = []
        for m in self.masters.values():
            if m.kind != "composite":
                continue
            names = [i.name for i in m.instances]
            for dup in {n for n in names if names.count(n) > 1}:
                out.append(f"{m.name}: instance name {dup!r} is used twice")
            used: dict[str, str] = {}
            for ch in m.channels:
                try:
                    pa, pb = self.port_of(m, ch.a), self.port_of(m, ch.b)
                except KeyError as exc:
                    out.append(f"{m.name}.{ch.name}: {exc.args[0]}")
                    continue
                # a composite's own port is seen from inside, so its direction flips
                if ch.a.startswith("."):
                    pa = _flipped(pa)
                if ch.b.startswith("."):
                    pb = _flipped(pb)
                for why in pa.accepts(pb):
                    out.append(f"{m.name}.{ch.name} ({ch.a} -> {ch.b}): {why}")
                for end in (ch.a, ch.b):
                    if end in used and self.port_of(m, end).role != "any":
                        out.append(f"{m.name}: port {end} is on two channels "
                                   f"({used[end]} and {ch.name})")
                    used[end] = ch.name
            for p in m.ports:
                if not p.bind:
                    out.append(f"{m.name}: port {p.name!r} binds to no child port")
                    continue
                try:
                    self.port_of(m, p.bind)
                except KeyError as exc:
                    out.append(f"{m.name}: port {p.name!r}: {exc.args[0]}")
        return out

    # -- json ----------------------------------------------------------------

    def to_json(self) -> dict:
        return {"schema": SCHEMA, "top": self.top,
                "masters": {k: m.to_json() for k, m in self.masters.items()}}

    @staticmethod
    def from_json(d: dict) -> "Library":
        if d.get("schema") != SCHEMA:
            raise ValueError(f"expected schema {SCHEMA!r}, got {d.get('schema')!r}")
        return Library(masters={k: Master.from_json(m) for k, m in d["masters"].items()},
                       top=d.get("top"))

    def save(self, path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_json(), indent=1), encoding="utf-8")
        return p

    @staticmethod
    def load(path) -> "Library":
        return Library.from_json(json.loads(Path(path).read_text(encoding="utf-8")))


def _flipped(p: Port) -> Port:
    flip = {"in": "out", "out": "in", "inout": "inout"}
    return Port(**{**asdict(p), "dir": flip[p.dir]})
