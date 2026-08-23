"""`DeviceBuilder` -- an explicit `Device`, one object at a time.

Every shipped architecture is produced by a *generator*: `ring(width=72, verticals=24)`
expands to 168 nodes and 168 segments, and the compact `.arch.json` stores the two
parameters rather than the graph.  That is the right default and it is what keeps the
files readable, but it has a hard edge -- a generator can only produce the family of
graphs it was written for.  The moment a device gains one node the generator does not
know about, the parameters stop describing it (`Device.reproducible_from_generator()`
goes False) and the only faithful serialization is the expanded graph.

This is the constructor for that case, and the one a drag-and-drop editor writes into:

    d = DeviceBuilder("racetrack", straight=20)
    d.site("S0", 0.0, 0.0, zone="data", labels=["rail"])
    d.junction("J0", 1.0, 0.0)
    d.segment("S0-J0", "S0", "J0", loop="L0", length=1.0)
    d.loop("L0", ["S0", "J0", ...], closed=True, kind="ring")
    m = Machine.blank_device(d.build(), name="mine", zones=["data"])

`generator` and `params` are carried through as provenance -- they say what the device
*started* as, which is exactly what a listing wants to print even once it can no longer
rebuild the graph from them.  Nothing downstream reads them for anything else; every
consumer takes the expanded graph.

`capacity > 0` on a site sets `capacity_explicit`, so `resolve_capacities` will leave it
alone when its zone is later retuned: a trap you deliberately made bigger should not
silently shrink.
"""

from __future__ import annotations

from typing import Sequence

from .device import Device, ExpansionError, Loop, Node, Segment

__all__ = ["DeviceBuilder"]


def _pos2(who: str, id: str, pos: Sequence[float]) -> tuple[float, ...]:
    """Exactly two coordinates.  The schema admits one to three and nothing reads a third.

    `compute_layout` is two-dimensional, so a 3-D device would draw as a silent
    projection and a 1-D one is unrepresentable in the browser mirror at all (it reads
    `args[1]` and `args[2]` and produces `NaN`).  A design tool must not accept a
    coordinate it will then quietly discard.
    """
    if len(pos) != 2:
        raise ExpansionError(
            f"DeviceBuilder.{who}({id!r}, ...) needs exactly two coordinates, got "
            f"{len(pos)}; the layout is two-dimensional and nothing reads a third")
    return tuple(float(c) for c in pos)


class DeviceBuilder:
    """Accumulate nodes, segments and loops, then `build()` the immutable `Device`."""

    def __init__(self, generator: str = "explicit", **params) -> None:
        self._nodes: dict[str, Node] = {}
        self._segments: dict[str, Segment] = {}
        self._loops: dict[str, Loop] = {}
        self.generator = str(generator)
        self.params = dict(params)

    # -- nodes ---------------------------------------------------------------

    def site(self, id: str, *pos: float, zone: str | None = None,
             labels: Sequence[str] = (), capacity: int = 0) -> "DeviceBuilder":
        """A trapping site.  `capacity=0` means "inherit from the zone type"."""
        self._nodes[id] = Node(
            id=id, pos=_pos2("site", id, pos), kind="site",
            capacity=int(capacity), zone_type=zone, labels=tuple(labels),
            capacity_explicit=bool(capacity))
        return self

    def junction(self, id: str, *pos: float,
                 labels: Sequence[str] = ()) -> "DeviceBuilder":
        """A bare junction: it holds no ions, so it has no zone and no capacity.

        Note that being *called* a junction is not what makes R18 charge one -- degree
        does.  A site with three trap axes meeting on it is a junction whatever it is
        named here.
        """
        self._nodes[id] = Node(id=id, pos=_pos2("junction", id, pos),
                               kind="junction", labels=tuple(labels))
        return self

    # -- edges ---------------------------------------------------------------

    def segment(self, id: str, a: str, b: str, *, loop: str | None = None,
                labels: Sequence[str] = (), length: float = 1.0,
                capacity: int = 1) -> "DeviceBuilder":
        self._segments[id] = Segment(id=id, ends=(a, b), length=float(length),
                                     capacity=int(capacity), loop=loop,
                                     labels=tuple(labels))
        return self

    def loop(self, id: str, nodes: Sequence[str], *, closed: bool = True,
             kind: str = "ring", note: str | None = None) -> "DeviceBuilder":
        """A named transport path -- the domain of one rigid-shift movement template."""
        self._loops[id] = Loop(id=id, nodes=tuple(nodes), closed=bool(closed),
                               kind=str(kind), note=note)
        return self

    # -- output --------------------------------------------------------------

    def build(self) -> Device:
        return Device(nodes=dict(self._nodes), segments=dict(self._segments),
                      loops=dict(self._loops), generator=self.generator,
                      params=dict(self.params))

    def __len__(self) -> int:
        return len(self._nodes)

    def __repr__(self) -> str:
        return (f"<DeviceBuilder {self.generator!r}: {len(self._nodes)} nodes, "
                f"{len(self._segments)} segments, {len(self._loops)} loop(s)>")
