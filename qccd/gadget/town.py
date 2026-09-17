"""A town: every verified place, in two rows between three streets.  GADGETS.md §9.

    street 0  ─E─J──J──J─────J──J─────────J──J──J────────E
               │ │  │  │     │  │         │  │  │        │
               │ [depot][prep ][clinic ][workshop][post][zone1]
               │    │           │  │       │  │         │
    street 1  ─E─J──J───────J───J──J─────J─J──J────J────E
               │ │  │       │   │  │     │ │  │    │    │
               │ [bridge        ][zone2][zone3][factory][pharmacy]
               │    │                    │   │            │
    street 2  ─E─J──J────────────────────J───J────────────E

Below the last street is the **control floor**: the decoder and the classical memory, wired
(dashed, not roads) to every place that measures and to every place whose operation can be
made conditional.  Syndrome bits go place → decoder, the decoder's frame update goes
decoder → archive, logical outcomes go place → archive, and guards go archive → place.  No
ion ever travels on a wire, so the control floor has no doors and no junctions.

A door on a place's top edge takes a vertical channel up to a junction on the street above
its row; a door on its bottom edge takes one down to the street below.  At both ends of
every street an end junction joins it to the streets above and below, so there is a road
from any door to any door.  A door channel holds at least nine sites, so a whole bundle can
queue in front of a busy place; street channels between junctions can be short, because the
town's schedule moves one bundle at a time on them.

Everything is derived from the places' own devices: a door sits at its port node, and a
channel's length is its Manhattan route less one, as for every composite.
"""

from __future__ import annotations

from .designs import Designer
from .library import LeafLibrary
from .model import Instance, Library

__all__ = ["town", "TOWN_ROWS"]

#: (instance, master), row by row, left to right
TOWN_ROWS = [
    [("depot", "depot9"), ("prep", "prep_d3"), ("clinic", "se_d3"), ("workshop", "tcx_d3"),
     ("post", "read_d3"), ("zone1", "zone_d3")],
    [("bridge", "ls_d3"), ("zone2", "zone_d3"), ("zone3", "zone_d3"), ("factory", "msf15"),
     ("pharmacy", "inj_d3")],
]

#: the control floor: (instance, master), left to right
CONTROL = [("decoder", "dec_d3"), ("archive", "arch1")]

STREET = 26.0         # street spacing; a row of places sits between two streets
ROW_Y = 12.0          # a row's offset below the street above it
GAP = 6.0             # between places in a row
MIN_SPACING = 4.0     # between junctions on a street
BUS_Y = 10.0          # the classical bus runs this far below the last street
CTRL_Y = 20.0         # and the control floor this far below it


def town(leaves: LeafLibrary, *, name: str = "town_d3", rows=TOWN_ROWS,
         control=CONTROL) -> Library:
    d = Designer(leaves)
    for row in rows:
        for _, mname in row:
            d.leaf(leaves.place(mname))
    d.leaf(leaves.place("junc9"))
    for _, mname in control:
        d.leaf(leaves.place(mname))

    def layout(shifts):
        instances, doors = [], {}          # street -> [(x, arm, "inst.port")]
        for r, row in enumerate(rows):
            x = shifts[r]
            for inst, mname in row:
                m = d.lib[mname]
                x0, _, x1, _ = m.bbox
                ox = x - x0
                instances.append(Instance(inst, mname, ox, r * STREET + ROW_Y))
                for p in m.ports:
                    if p.kind != "ion":
                        continue                # a wire port is no door: see the control floor
                    px, py = d.port_point(m, p.name)
                    if py <= 0.0:
                        doors.setdefault(r, []).append((ox + px, "s", f"{inst}.{p.name}"))
                    else:
                        doors.setdefault(r + 1, []).append((ox + px, "n", f"{inst}.{p.name}"))
                x = ox + x1 + GAP
        return instances, doors

    def spacing_ok(doors):
        for street in doors.values():
            xs = sorted({x for x, _, _ in street})
            if any(b - a < MIN_SPACING for a, b in zip(xs, xs[1:])):
                return False
            seen = {}
            for x, arm, _ in street:
                if (x, arm) in seen:
                    return False
                seen[(x, arm)] = True
        return True

    shifts = [0.0] * len(rows)
    for extra in range(0, 40):
        shifts = [0.0] + [float(extra)] * (len(rows) - 1)
        instances, doors = layout(shifts)
        if spacing_ok(doors):
            break
    else:
        raise ValueError("no row offset keeps the junctions on the shared street apart")

    links: list[tuple] = []
    xs = [x for street in doors.values() for x, _, _ in street]
    west, east = min(xs) - 6.0, max(xs) + 6.0
    ends = {}
    for k in range(len(rows) + 1):
        y = k * STREET
        street = sorted(doors.get(k, []))
        js = []
        for x in sorted({x for x, _, _ in street}):
            j = f"j{k}_{len(js)}"
            instances.append(Instance(j, "junc9", x, y))
            js.append((x, j))
            for dx, arm, ref in street:
                if dx == x:
                    links.append((f"door_{ref.replace('.', '_')}", f"{j}.{arm}", ref))
        we, ee = f"w{k}", f"e{k}"
        instances.append(Instance(we, "junc9", west, y))
        instances.append(Instance(ee, "junc9", east, y))
        chain = [(west, we)] + js + [(east, ee)]
        for (xa, ja), (xb, jb) in zip(chain, chain[1:]):
            links.append((f"street{k}_{ja}_{jb}", f"{ja}.e", f"{jb}.w"))
        ends[k] = (we, ee)
    for k in range(len(rows)):
        links.append((f"west_{k}", f"{ends[k][0]}.s", f"{ends[k + 1][0]}.n"))
        links.append((f"east_{k}", f"{ends[k][1]}.s", f"{ends[k + 1][1]}.n"))

    # -- the control floor: the decoder, the archive, and the wires between them and the
    # places.  A wire has one cost, its latency, and carries no ions (GADGETS.md §10).
    from .logic.decode import LATENCY
    bus_y = len(rows) * STREET + BUS_Y
    ctrl_y = len(rows) * STREET + CTRL_Y
    x = west + 8.0
    for inst, mname in control:
        x0, _, x1, _ = d.lib[mname].bbox
        instances.append(Instance(inst, mname, x - x0, ctrl_y))
        x += (x1 - x0) + 16.0
    where = {i.name: (i.master, i.x, i.y) for i in instances}

    def at(ref: str) -> tuple[float, float]:
        """Where "inst.port" sits in the town's own frame."""
        inst, _, port = ref.partition(".")
        mname, ox, oy = where[inst]
        px, py = d.port_point(d.lib[mname], port)
        return ox + px, oy + py

    #: which classical signal goes where, and what the wire costs
    TO = {"syndrome": ("decoder.syn", LATENCY["link_us"]),
          "outcome": ("archive.in", LATENCY["link_us"]),
          "decision": ("archive.ctl", LATENCY["decision_us"])}
    for row in rows:
        for inst, mname in row:
            for p in d.lib[mname].ports:
                if p.kind != "classical" or p.signal not in TO:
                    continue
                other, lat = TO[p.signal]
                a, b = (f"{inst}.{p.name}", other) if p.dir == "out" else \
                       (other, f"{inst}.{p.name}")
                # down to the bus, along it, and up to the control floor
                links.append({"name": f"wire_{inst}_{p.name}", "a": a, "b": b, "kind": "wire",
                              "latency_us": lat,
                              "via": [[at(a)[0], bus_y], [at(b)[0], bus_y]]})
    links.append({"name": "wire_decoder_frame", "a": "decoder.frame", "b": "archive.frame",
                  "kind": "wire", "latency_us": 1.0})
    d.composite(name, "town", "A town of verified places (surface code, d = 3)",
                "every verified place in two rows between three streets: blocks travel from "
                "door to door through junctions",
                instances=instances, links=links, ports=[],
                params={"places": [p for row in rows for p, _ in row],
                        "control": [p for p, _ in control], "code": "surface_d3"})
    d.lib.top = name
    return d.lib
