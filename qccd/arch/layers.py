"""Machines built in layers: planar machines one above another, joined by risers.

A layered machine is still one `Device`: a graph of nodes and rails.  What is new is a
third coordinate.  Every node of a layer has that layer's `z`, and a RISER is a rail whose
two ends are in different layers.  Nothing else is declared -- `Device.layers`,
`Device.layer_of` and `Device.is_riser` are derived, as degree and corners are.

Three decisions hold everywhere a layered machine is read (docs/PLAN-3d.md):

* **A riser is an ordinary shuttle.**  It is drawn as a rail, driven by the same kind of
  electrodes on the same kind of DACs, and takes the time and gives the heat of any other
  segment (`shuttle_segment`).  A node it ends at has one rail more, and is priced by its
  degree like any junction (R18).
* **Layers need not be alike.**  `stack` takes any planar machines: a lattice under a
  ring, a compute layer under a highway.
* **Each layer has a broadcast of its own.**  No control channel drives sites of two
  layers (`qccd.arch.control.build_control_plane`), and one cycle is one waveform a layer
  (R22).

A riser is VERTICAL: its ends share `(x, y)` and its layers are adjacent (R21).  That is
what keeps R20 meaningful -- a vertical rail is at 90 degrees to every rail of its layer
under any scale of the lattice, and a slanted one has no angle that survives the map to
the lab.  Two layers of different shape are therefore placed so that the nodes a riser
joins stand one above the other, which `stack` does from the first riser it is given.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping, Sequence

from .device import Device, ExpansionError, Loop, Node, Segment

__all__ = ["stack", "layer_names", "RISER_LABEL"]

#: The label a riser carries.  A label, not a kind: what makes a rail a riser is that its
#: ends are in two layers (`Device.is_riser`), and a hand-written document needs no label.
RISER_LABEL = "riser"

#: Coincidence tolerance for "one above the other", in lattice units (R21's `RAIL_EPS`).
_EPS = 1e-6


def layer_names(n: int) -> tuple[str, ...]:
    """`("A", "B", ...)`: what `n` layers are called, lowest first."""
    return tuple(Device.layer_name(i) for i in range(n))


def _xy(node: Node) -> tuple[float, float]:
    return (float(node.pos[0]), float(node.pos[1]) if len(node.pos) > 1 else 0.0)


def stack(layers: Sequence[Device], risers: Sequence[Sequence[str]] = (), *,
          names: Sequence[str] | None = None,
          offsets: Sequence[Sequence[float]] | None = None,
          spacing: float = 1.0, strict: bool = True) -> Device:
    """Put planar machines one above another and join them with risers.

    `layers`   the planar machines, lowest first.
    `names`    what each layer is called, `A`, `B`, ... by default.  Every id of a layer
               is prefixed with its name: node `S0` of layer B is `B.S0`, its loop `L0`
               is `B.L0`.  Two layers may therefore use the same ids.
    `risers`   pairs of node ids in the stacked machine, `("A.J0_0", "B.J0_0")`.  Each
               becomes a rail `R0`, `R1`, ... labelled `riser`.
    `offsets`  an `(dx, dy)` shift for each layer, in lattice units.  Left out, every
               layer above the first is shifted so that the first riser joining it to a
               layer below stands vertical; a layer no riser reaches is not moved.
    `spacing`  the `z` between adjacent layers, in lattice units.
    `strict`   refuse a riser that is not vertical or skips a layer.  With `strict=False`
               the machine is built as asked and R21 reports it.
    """
    layers = list(layers)
    if not layers:
        raise ExpansionError("stack() needs at least one layer")
    names = list(names) if names is not None else list(layer_names(len(layers)))
    if len(names) != len(layers) or len(set(names)) != len(names):
        raise ExpansionError(
            f"stack() needs one distinct name a layer: {len(layers)} layer(s), names {names!r}")
    for name, dev in zip(names, layers):
        if dev.dim != 2:
            raise ExpansionError(
                f"layer {name!r} is itself built in layers; stack() takes planar machines")

    def pid(k: int, old: str) -> str:
        return f"{names[k]}.{old}"

    where: dict[str, tuple[int, str]] = {}          # stacked id -> (layer, id in the layer)
    for k, dev in enumerate(layers):
        for nid in dev.nodes:
            where[pid(k, nid)] = (k, nid)

    pairs: list[tuple[str, str]] = []
    for r in risers:
        if len(r) != 2:
            raise ExpansionError(f"a riser joins two nodes, got {tuple(r)!r}")
        a, b = str(r[0]), str(r[1])
        for end in (a, b):
            if end not in where:
                raise ExpansionError(
                    f"riser ({a!r}, {b!r}): no node {end!r} in the stack; a node of layer "
                    f"{names[0]!r} is written {names[0]}.<id>")
        if where[a][0] == where[b][0]:
            raise ExpansionError(
                f"riser ({a!r}, {b!r}) joins two nodes of layer {names[where[a][0]]!r}; a "
                f"rail inside a layer belongs to that layer's machine")
        if where[a][0] > where[b][0]:
            a, b = b, a                              # written from the lower layer up
        pairs.append((a, b))

    if offsets is not None:
        shift = [(float(o[0]), float(o[1])) for o in offsets]
        if len(shift) != len(layers):
            raise ExpansionError(
                f"stack() needs one offset a layer: {len(layers)} layer(s), "
                f"{len(shift)} offset(s)")
    else:
        shift = [(0.0, 0.0)] * len(layers)
        for k in range(1, len(layers)):
            for a, b in pairs:
                (ka, na), (kb, nb) = where[a], where[b]
                if kb != k:
                    continue
                ax, ay = _xy(layers[ka].nodes[na])
                bx, by = _xy(layers[kb].nodes[nb])
                shift[k] = (ax + shift[ka][0] - bx, ay + shift[ka][1] - by)
                break

    nodes: dict[str, Node] = {}
    segments: dict[str, Segment] = {}
    loops: dict[str, Loop] = {}
    for k, dev in enumerate(layers):
        dx, dy = shift[k]
        z = float(k) * float(spacing)
        for nid, n in dev.nodes.items():
            x, y = _xy(n)
            nodes[pid(k, nid)] = replace(n, id=pid(k, nid), pos=(x + dx, y + dy, z))
        for sid, s in dev.segments.items():
            segments[pid(k, sid)] = replace(
                s, id=pid(k, sid), ends=(pid(k, s.ends[0]), pid(k, s.ends[1])),
                loop=None if s.loop is None else pid(k, s.loop))
        for lid, lp in dev.loops.items():
            loops[pid(k, lid)] = replace(
                lp, id=pid(k, lid), nodes=tuple(pid(k, n) for n in lp.nodes))

    for i, (a, b) in enumerate(pairs):
        pa, pb = nodes[a].pos, nodes[b].pos
        if strict and (abs(pa[0] - pb[0]) > _EPS or abs(pa[1] - pb[1]) > _EPS):
            raise ExpansionError(
                f"riser ({a!r}, {b!r}) is not vertical: {a} is at ({pa[0]:g}, {pa[1]:g}) "
                f"and {b} at ({pb[0]:g}, {pb[1]:g}).  A riser joins a node to the one "
                f"directly above it; give `offsets` that put them one above the other")
        if strict and where[b][0] - where[a][0] != 1:
            raise ExpansionError(
                f"riser ({a!r}, {b!r}) joins layers {names[where[a][0]]!r} and "
                f"{names[where[b][0]]!r}, which are not adjacent")
        segments[f"R{i}"] = Segment(id=f"R{i}", ends=(a, b), length=1.0, capacity=1,
                                    loop=None, labels=(RISER_LABEL,))

    return Device(nodes=nodes, segments=segments, loops=loops, generator="explicit",
                  params={})


def merge_zone_types(blocks: Sequence[Mapping[str, Mapping]],
                     names: Sequence[str]) -> tuple[dict, list[dict[str, str]]]:
    """One zone table for a stack, and for each layer the renames it needs.

    A zone two layers define alike is one zone.  One they define differently is kept
    apart: the later layer's is renamed `<layer>.<zone>`, so a `trap` of capacity 2 under
    a `trap` of capacity 4 stay what their own machines said.
    """
    out: dict[str, dict] = {}
    renames: list[dict[str, str]] = []
    for name, block in zip(names, blocks):
        mine: dict[str, str] = {}
        for zone, spec in block.items():
            spec = dict(spec)
            if zone not in out or out[zone] == spec:
                out[zone] = spec
            else:
                mine[zone] = f"{name}.{zone}"
                out[mine[zone]] = spec
        renames.append(mine)
    return out, renames
