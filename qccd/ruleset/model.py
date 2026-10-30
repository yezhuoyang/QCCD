"""What a rule talks about: the hardware, and one execution on it.

Two kinds of execution are judged by the same rules:

* a **programme** -- the instruction list this website's compiler and studio produce, run
  in lockstep: one instruction (a *step*) at a time, every operation of a step together;
* a **schedule** -- a list of timed operations, each with a start and an end in
  microseconds, the form most papers compute: operations overlap whenever their resources
  allow.

Both are reduced to the same objects -- ions, operations with a kind, a place, a start and
an end, and the state (where every ion is) at every *instant* -- so one formula means the
same thing on both.  Where the two differ, the difference lives in how a term is defined
for each kind (`vocabulary.py`), never in the rule.

Everything here is plain data built once per execution; a rule only reads it.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "Entity", "Site", "Rail", "Ion", "Op", "Hop", "Instant", "Step", "CircuitOp", "Loop",
    "Hardware", "Execution", "Timeline",
]


class Entity:
    """A thing a variable can stand for.  Equal when kind and id are."""

    __slots__ = ("id",)
    kind = "thing"

    def __init__(self, id: Any):
        self.id = id

    def __eq__(self, other: object) -> bool:
        return type(other) is type(self) and other.id == self.id  # type: ignore[attr-defined]

    def __hash__(self) -> int:
        return hash((self.kind, self.id))

    def __lt__(self, other: "Entity") -> bool:
        return (self.kind, str(self.id)) < (other.kind, str(other.id))

    def __repr__(self) -> str:
        return f"{self.kind} {self.id}"

    def label(self) -> str:
        return f"{self.kind} {self.id}"


class Site(Entity):
    """A node of the device: a place an ion can be (a trap, a dock, a loop slot) or a
    junction.  `sites` and `junctions` are both sets of these."""
    __slots__ = ()
    kind = "site"

    def label(self) -> str:
        return str(self.id)


class Rail(Entity):
    __slots__ = ()
    kind = "rail"


class Ion(Entity):
    __slots__ = ()
    kind = "ion"

    def label(self) -> str:
        return f"ion {self.id}"


class Loop(Entity):
    __slots__ = ()
    kind = "loop"


class Instant(Entity):
    """A moment of the execution, by index into `Execution.instants`."""
    __slots__ = ()
    kind = "instant"


class Step(Entity):
    """One instruction of a programme (one cycle of the machine), by index."""
    __slots__ = ()
    kind = "step"


class CircuitOp(Entity):
    """One operation of the input circuit, by its index in the circuit."""
    __slots__ = ()
    kind = "circuit operation"


@dataclass(eq=False)
class Op:
    """One operation of the execution.

    ``kind``    gate2 (a two-qubit gate), gate1, measure, reset, cool, swap (a reorder inside
                a chain), or a transport kind: move (one ion's motion in one programme step),
                split, merge, shuttle, junction_enter, junction_exit, hop
    ``site``    where an operation in place happens (None for transport and global ones)
    ``path``    for transport: the nodes the ion passes, first to last
    ``rails``   for transport: the rails it uses
    ``start``/``end``  instants (indices); ``t0``/``t1`` the same in microseconds
    ``step``    the programme step, or None in a schedule
    """

    index: int
    kind: str
    ions: tuple[Ion, ...]
    start: int
    end: int
    t0: float
    t1: float
    site: Site | None = None
    path: tuple[Site, ...] = ()
    rails: tuple[Rail, ...] = ()
    step: int | None = None
    serves: tuple[int, ...] = ()           # circuit operations this op belongs to
    facts: dict[str, Any] = field(default_factory=dict)
    text: str = ""

    def __hash__(self) -> int:
        return self.index

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Op) and other.index == self.index

    def __lt__(self, other: "Op") -> bool:
        return self.index < other.index

    def __repr__(self) -> str:
        return self.label()

    def label(self) -> str:
        where = f" at {self.site.label()}" if self.site is not None else ""
        when = f", step {self.step}" if self.step is not None else f", {self.t0:g}-{self.t1:g} us"
        return f"{self.text or self.kind}{where}{when}"


@dataclass(eq=False)
class Hop:
    """One rail crossed by one transport operation: the unit a motion is made of."""

    index: int
    op: Op
    ion: Ion
    src: Site
    dst: Site
    rail: Rail
    loop: str | None = None
    shift: int | None = None               # steps along a closed loop, signed
    label_: str = ""

    def __hash__(self) -> int:
        return hash(("hop", self.index))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Hop) and other.index == self.index

    def __lt__(self, other: "Hop") -> bool:
        return self.index < other.index

    def label(self) -> str:
        return f"{self.ion.label()} {self.src.label()}→{self.dst.label()} ({self.op.label()})"


@dataclass(eq=False)
class CircuitOpData:
    index: int
    name: str
    qubits: tuple[str, ...]
    roles: tuple[str, ...] = ()            # per qubit, for a CX: "control" / "target"
    text: str = ""


@dataclass
class Hardware:
    """The device as the rules see it."""

    name: str
    sites: list[Site]                      # every place an ion may stand
    junctions: list[Site]                  # nodes where three or more rails meet
    nodes: list[Site]                      # every node
    rails: list[Rail]
    capacity: dict[Any, int]               # Site or Rail -> places
    degree: dict[Site, int]
    zone: dict[Site, str]
    abilities: dict[Site, frozenset[str]]  # gate / spam / cool / measure / reset ...
    ends: dict[Rail, tuple[Site, Site]]
    rails_at: dict[Site, tuple[Rail, ...]]
    neighbours: dict[Site, frozenset[Site]] = field(default_factory=dict)
    loops: dict[str, tuple[Site, ...]] = field(default_factory=dict)
    position: dict[Site, tuple[float, float]] = field(default_factory=dict)
    numbers: dict[str, Any] = field(default_factory=dict)   # declared limits, control model
    arch: Any = None                       # the architecture document, for the geometry terms


class Timeline:
    """Values that change over the execution, stored as change lists per key.

    `at(key, i)` is the value at instant i (the last change at or before it); `changed(i)`
    is the set of keys whose value changes at instant i, which is what lets a rule over
    every instant skip the keys that did not move.
    """

    __slots__ = ("times", "values", "default", "dirty", "initial")

    def __init__(self, default: Any = 0):
        self.times: dict[Any, list[int]] = {}
        self.values: dict[Any, list[Any]] = {}
        self.default = default
        self.dirty: dict[int, set] = {}
        self.initial: set = set()

    def set(self, key: Any, i: int, value: Any) -> None:
        ts = self.times.get(key)
        if ts is None:
            self.times[key] = [i]
            self.values[key] = [value]
            if i == 0:
                self.initial.add(key)
            else:
                self.dirty.setdefault(i, set()).add(key)
            return
        vs = self.values[key]
        if vs[-1] == value:
            return
        if ts[-1] == i:
            vs[-1] = value
        else:
            ts.append(i)
            vs.append(value)
        self.dirty.setdefault(i, set()).add(key)

    def at(self, key: Any, i: int) -> Any:
        ts = self.times.get(key)
        if not ts:
            return self.default
        k = bisect_right(ts, i) - 1
        return self.values[key][k] if k >= 0 else self.default

    def changed(self, i: int) -> set:
        if i == 0:
            return set(self.initial)
        return self.dirty.get(i, set())

    def keys(self) -> Iterable[Any]:
        return self.times.keys()


@dataclass
class Execution:
    """One execution, ready to be judged.

    ``kind``      "programme" or "schedule"
    ``instants``  descriptions, one per instant, in time order (index = Instant.id)
    ``occupancy`` Timeline: Site or Rail -> ions there (for a programme, an instant *during*
                  a step counts every ion that rests there before or after the step, the
                  larger, plus every ion that passes through it)
    ``position``  Timeline: Ion -> Site where it stands, or None while it is in motion
    ``heat``      Ion -> {instant: n-bar}, recorded at the start of every gate
    ``circuit``   the input circuit's operations, when the execution is judged against one
    """

    kind: str
    hardware: Hardware
    ions: list[Ion]
    ops: list[Op]
    instants: list[str]
    instant_time: list[float]
    occupancy: Timeline
    position: Timeline
    hops: list[Hop] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    heat: dict[Ion, dict[int, float]] = field(default_factory=dict)
    circuit: list[CircuitOpData] | None = None
    facts: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    name: str = ""
    #: further per-key timelines a word reads, by word: `entered` (C1's exception, Site ->
    #: bool) on a C2LR programme.  The instants pruning reads them like `occupancy`.
    timelines: dict[str, "Timeline"] = field(default_factory=dict)

    # derived indexes, built on first use
    _by_kind: dict[str, list[Op]] | None = None
    _by_step: dict[int, list[Op]] | None = None
    _hops_by_step: dict[int, list[Hop]] | None = None

    def ops_of(self, kinds: Sequence[str] | None = None, step: int | None = None) -> list[Op]:
        if step is not None:
            if self._by_step is None:
                by: dict[int, list[Op]] = {}
                for o in self.ops:
                    if o.step is not None:
                        by.setdefault(o.step, []).append(o)
                self._by_step = by
            src = self._by_step.get(step, [])
            return src if kinds is None else [o for o in src if o.kind in kinds]
        if kinds is None:
            return self.ops
        if self._by_kind is None:
            byk: dict[str, list[Op]] = {}
            for o in self.ops:
                byk.setdefault(o.kind, []).append(o)
            self._by_kind = byk
        if len(kinds) == 1:
            return self._by_kind.get(kinds[0], [])
        out = [o for k in kinds for o in self._by_kind.get(k, [])]
        out.sort(key=lambda o: o.index)
        return out

    def hops_of(self, step: int | None = None) -> list[Hop]:
        if step is None:
            return self.hops
        if self._hops_by_step is None:
            by: dict[int, list[Hop]] = {}
            for h in self.hops:
                if h.op.step is not None:
                    by.setdefault(h.op.step, []).append(h)
            self._hops_by_step = by
        return self._hops_by_step.get(step, [])


#: Operation kinds by family.  A paper's schedule and a programme name their transport
#: differently; the families are what the rules quantify over.
TRANSPORT_KINDS = ("move", "split", "merge", "shuttle", "junction_enter", "junction_exit", "hop")
GATE_KINDS = ("gate2", "gate1")
PLACE_KINDS = ("gate2", "gate1", "measure", "reset", "cool", "swap")
