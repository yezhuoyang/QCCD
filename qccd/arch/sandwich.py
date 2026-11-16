"""The conveyor sandwich: data traps that never move, between two loops that carry the ancillas.

A syndrome round is ancillas meeting data, one data qubit after another.  On a loop of traps
that is a traffic problem: every ion that has somewhere to go is in the way of another.
The sandwich takes the traffic away.  The data stand still, in a ring of traps.  The
ancillas ride a conveyor loop with one slot facing every data trap, and a rail from each slot
to its trap.  One rotation of the loop brings EVERY ancilla to its next partner at once, and
one hop along the rails brings them all into the data traps -- two waveforms, however many
ancillas there are.  A second loop, on the other side of the data, carries a second family
of ancillas (the Z checks, say, while the first carries the X checks).

It is built three ways, from the same parts (`layers`):

* **three layers**: a loop below, the data traps in the middle, a loop above; the rail from a
  slot to its trap is a riser.  Each layer has a broadcast of its own (docs/PLAN-3d.md), so
  the two loops rotate IN THE SAME CYCLE, each on its own waveform.
* **two layers**: one loop in the data traps' own plane, inside their ring, and the other
  above them.  The loops still turn together; the hops of the loop in the plane are a planar
  machine's.
* **one layer**, a planar machine: one loop inside the ring of data traps and one outside,
  nested racetracks.  One broadcast, so the loops take turns; and a hop from the top row
  into a trap and one from the bottom row are two directions in the lab frame, so they are
  two waveforms where the risers' hops are one.

`Codesign/scripts/q07_layers.py` compares them on the boards.  The router that drives
either is `qccd.compile.conveyor_router`.

The roles of the nodes travel in the document's `provenance["sandwich"]`:

    {"positions": P, "data": [trap id, ...],              # trap k, in ring order
     "loops": [{"id": loop id, "slots": [node id, ...]}]}  # slot k faces trap k
"""

from __future__ import annotations

from .builder import DeviceBuilder
from .device import Architecture, Device

__all__ = ["conveyor_sandwich", "sandwich_roles", "CONVEYOR", "HOP"]

#: the movement classes a sandwich declares: a loop's rotation, and the hop between a slot
#: and its data trap
CONVEYOR = "conveyor"
HOP = "shuttle"


def _racetrack(width: int) -> list[tuple[float, float]]:
    """The `2 * width` slot positions of `ring(width, 2)`, in loop order: along the top row,
    back along the bottom."""
    top = [(float(x), 0.0) for x in range(width)]
    bottom = [(float(x), 1.0) for x in reversed(range(width))]
    return top + bottom


def _layered(width: int, loops: int, zone: str) -> tuple[Device, dict]:
    """Loops and data traps one above another: every slot directly over (or under) its trap."""
    pts = _racetrack(width)
    n = len(pts)
    d = DeviceBuilder("explicit")
    # layer z=1 holds the data when there are two loops (one below at 0, one above at 2);
    # with one loop the data are at 0 and the loop at 1
    z_data = 1.0 if loops == 2 else 0.0
    z_loop = [0.0, 2.0] if loops == 2 else [1.0]
    data = []
    for k, (x, y) in enumerate(pts):
        d.site(f"D{k}", x, y, z_data, zone=zone, labels=["data"])
        data.append(f"D{k}")
    roles = []
    for g, z in enumerate(z_loop):
        lid = f"L{g}"
        slots = [f"{lid}S{k}" for k in range(n)]
        for k, (x, y) in enumerate(pts):
            d.site(slots[k], x, y, z, zone=zone, labels=["rail"])
        for k in range(n):
            d.segment(f"{lid}E{k}", slots[k], slots[(k + 1) % n], loop=lid, labels=["rail"])
        d.loop(lid, slots, closed=True, kind="ring")
        for k in range(n):
            d.segment(f"{lid}R{k}", slots[k], data[k], labels=["riser"])
        roles.append({"id": lid, "slots": slots})
    return d.build(), {"positions": n, "data": data, "loops": roles}


def _planar(width: int, loops: int, zone: str) -> tuple[Device, dict]:
    """The same graph in one plane: a loop inside the ring of data traps, and one outside."""
    n = 2 * width
    d = DeviceBuilder("explicit")
    # the inner loop's rows are y = +1 and y = -1; the data stand at +-2; the outer loop at +-3
    def at(k: int, r: float) -> tuple[float, float]:
        return (float(k), r) if k < width else (float(n - 1 - k), -r)

    data = []
    for k in range(n):
        d.site(f"D{k}", *at(k, 2.0), zone=zone, labels=["data"])
        data.append(f"D{k}")
    roles = []
    for g in range(loops):
        lid = f"L{g}"
        r = 1.0 if g == 0 else 3.0
        slots = [f"{lid}S{k}" for k in range(n)]
        for k in range(n):
            d.site(slots[k], *at(k, r), zone=zone, labels=["rail"])
        nodes = list(slots)
        if g == 0:
            for k in range(n):
                d.segment(f"{lid}E{k}", slots[k], slots[(k + 1) % n], loop=lid, labels=["rail"])
        else:
            # the outer loop's ends go round the data traps: two corners at each end, bare
            # junctions an ion is carried round (`Device.is_bend`)
            corners = [(f"{lid}C0", (float(width), r)), (f"{lid}C1", (float(width), -r)),
                       (f"{lid}C2", (-1.0, -r)), (f"{lid}C3", (-1.0, r))]
            for cid, pos in corners:
                d.junction(cid, *pos, labels=["corner"])
            nodes = slots[:width] + [corners[0][0], corners[1][0]] + slots[width:] \
                + [corners[2][0], corners[3][0]]
            for i in range(len(nodes)):
                d.segment(f"{lid}E{i}", nodes[i], nodes[(i + 1) % len(nodes)], loop=lid,
                          labels=["rail"])
        d.loop(lid, nodes, closed=True, kind="ring")
        for k in range(n):
            d.segment(f"{lid}R{k}", slots[k], data[k], labels=["spur"])
        roles.append({"id": lid, "slots": slots})
    return d.build(), {"positions": n, "data": data, "loops": roles}


def _mixed(width: int, zone: str) -> tuple[Device, dict]:
    """Two layers: the first loop in the data traps' plane, inside their ring, as the planar
    build has it; the second loop one layer up, every slot directly over its trap."""
    n = 2 * width
    d = DeviceBuilder("explicit")

    def at(k: int, r: float) -> tuple[float, float]:
        return (float(k), r) if k < width else (float(n - 1 - k), -r)

    data = []
    for k in range(n):
        d.site(f"D{k}", *at(k, 2.0), 0.0, zone=zone, labels=["data"])
        data.append(f"D{k}")
    roles = []
    for g, (r, z, label) in enumerate(((1.0, 0.0, "spur"), (2.0, 1.0, "riser"))):
        lid = f"L{g}"
        slots = [f"{lid}S{k}" for k in range(n)]
        for k in range(n):
            d.site(slots[k], *at(k, r), z, zone=zone, labels=["rail"])
        for k in range(n):
            d.segment(f"{lid}E{k}", slots[k], slots[(k + 1) % n], loop=lid, labels=["rail"])
        d.loop(lid, slots, closed=True, kind="ring")
        for k in range(n):
            d.segment(f"{lid}R{k}", slots[k], data[k], labels=[label])
        roles.append({"id": lid, "slots": slots})
    return d.build(), {"positions": n, "data": data, "loops": roles}


def conveyor_sandwich(positions: int, *, layers: int = 3, loops: int = 2,
                      template="cyclone_base", zone: str = "trap", capacity: int | None = None,
                      name: str | None = None):
    """A sandwich with `positions` data traps (rounded up to an even number) and `loops`
    conveyor loops (1 or 2), built in `layers` layers.  Returns a `qccd.Machine`.

    `layers`: 1 is the planar machine; 2 puts the second loop (or the only one) a layer above
    the data; 3 puts one loop below the data and one above.

    `template` gives the zone types, primitive curves and wiring (a machine name under
    `arch/`, or a document); `capacity`, when given, is the capacity of the `zone` every
    node is in.  A data trap must hold its own ions and one visiting ancilla.
    """
    from ..api import Machine, _template_doc

    if loops not in (1, 2):
        raise ValueError(f"a sandwich has one loop or two, not {loops}")
    if layers not in (1, 2, 3) or (layers == 3 and loops != 2):
        raise ValueError(f"a sandwich of {loops} loop(s) is built in 1 or 2 layers, or in 3 "
                         f"when it has two loops; not in {layers}")
    width = max(2, (int(positions) + 1) // 2)
    if layers == 1:
        dev, roles = _planar(width, loops, zone)
    elif layers == 2 and loops == 2:
        dev, roles = _mixed(width, zone)
    else:
        dev, roles = _layered(width, loops, zone)
    doc = dict(_template_doc(template))
    doc["name"] = name or f"sandwich{2 * width}_L{layers}"
    doc["geometry"] = dev.to_json()
    zones = {k: dict(v) for k, v in doc.get("zone_types", {}).items()}
    if capacity is not None:
        zones.setdefault(zone, {})["capacity"] = int(capacity)
    doc["zone_types"] = zones
    # the two movements it makes, declared: a loop's rotation is a shift along a rail (which
    # is what lets R23 see a conveyor), a hop between a slot and its trap is a shuttle
    control = dict(doc.get("control", {}) or {})
    classes = dict(control.get("classes", {}) or {})
    classes["extra"] = [
        {"id": CONVEYOR, "type": "shift", "orbit": "rail",
         "note": "every ancilla on a loop advances one slot: one waveform a loop"},
        {"id": HOP, "type": "shift", "orbit": "any",
         "note": "an ancilla between its slot and the data trap it faces"},
    ]
    control["classes"] = classes
    doc["control"] = control
    doc["provenance"] = {"sandwich": roles,
                         "note": "qccd.arch.sandwich.conveyor_sandwich"}
    doc["description"] = (
        f"conveyor sandwich: {roles['positions']} data traps that never move, and {loops} "
        f"conveyor loop(s) with a slot facing each, "
        + (f"in {layers} layers" if layers > 1 else "in one plane"))
    return Machine(Architecture.from_json(doc))


def sandwich_roles(arch: Architecture) -> dict:
    """The roles a sandwich's document records, or a `ValueError` saying it is not one."""
    roles = (arch.provenance or {}).get("sandwich")
    if not roles or "data" not in roles or not roles.get("loops"):
        raise ValueError(f"{arch.name} is not a conveyor sandwich (no provenance.sandwich)")
    return roles
