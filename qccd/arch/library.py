"""The shipped component library -- the pieces a QCCD is actually made of.

qiskit-metal's `qlibrary/` carries 49 classes because a designer places a
`TransmonPocket`, not a polygon.  This is the trapped-ion equivalent, and it is
deliberately small: each entry here is a shape a QCCD paper would name, expressed in the
four builder verbs (`d.site`, `d.junction`, `d.segment`, `d.loop`) and nothing else.

Every component is a FUNCTION of its parameters returning a `Component`, because that is
where the "parametric" in parametric component lives: `linear_register(n=8)` and
`linear_register(n=32)` are different geometry from one definition.  What it is NOT is a
solver -- the coordinates are arithmetic on a lattice, so both language halves land on the
same bits and the parity harness has nothing new to check.

Placement is on quarter turns only; see `component.translate_point` for why.
"""

from __future__ import annotations

from typing import Callable, Mapping

from .component import Component

__all__ = ["CATALOG", "build", "catalog_json"]


def _site(i, x, y, *, zone=None, cap=None, labels=()):
    kw: dict = {}
    if zone:
        kw["zone"] = str(zone)
    if cap is not None:
        kw["capacity"] = int(cap)
    if labels:
        kw["labels"] = list(labels)
    return {"method": "d.site", "args": [i, float(x), float(y)], "kwargs": kw}


def _junction(i, x, y, *, labels=()):
    kw = {"labels": list(labels)} if labels else {}
    return {"method": "d.junction", "args": [i, float(x), float(y)], "kwargs": kw}


def _seg(i, a, b, *, loop=None, length=None, cap=None, labels=()):
    kw: dict = {}
    if loop:
        kw["loop"] = str(loop)
    if length is not None:
        kw["length"] = float(length)
    if cap is not None:
        kw["capacity"] = int(cap)
    if labels:
        kw["labels"] = list(labels)
    return {"method": "d.segment", "args": [i, a, b], "kwargs": kw}


def _loop(i, nodes, *, closed=True, kind="ring"):
    return {"method": "d.loop", "args": [i, list(nodes)],
            "kwargs": {"closed": bool(closed), "kind": str(kind)}}


# --------------------------------------------------------------------------- pieces


def linear_register(n: int = 8, pitch: float = 1.0, zone: str = "trap",
                    capacity: int = 2, segment_capacity: int = 1) -> Component:
    """`n` traps in a row, joined end to end.  The lowest common denominator of a QCCD."""
    n = max(2, int(n))
    recs = [_site(f"s{i}", i * pitch, 0.0, zone=zone, cap=capacity) for i in range(n)]
    recs += [_seg(f"e{i}", f"s{i}", f"s{i+1}", length=pitch, cap=segment_capacity)
             for i in range(n - 1)]
    return Component(
        "linear_register", recs,
        params={"n": n, "pitch": pitch, "zone": zone, "capacity": capacity,
                "segment_capacity": segment_capacity},
        pins=[{"name": "w", "node": "s0", "dir": [-1, 0], "kind": "rail"},
              {"name": "e", "node": f"s{n-1}", "dir": [1, 0], "kind": "rail"}],
        requires={"zones": (zone,)},
        blurb=f"{n} traps in a line -- a storage register, or a stretch of rail")


def transport_loop(width: int = 6, height: float = 1.0, zone: str = "data",
                   capacity: int = 2) -> Component:
    """A closed orbit: the thing a rigid rotation rotates."""
    width = max(2, int(width))
    top = [_site(f"t{i}", i, 0.0, zone=zone, cap=capacity) for i in range(width)]
    bot = [_site(f"b{i}", i, height, zone=zone, cap=capacity) for i in range(width)]
    walk = [f"t{i}" for i in range(width)] + [f"b{i}" for i in range(width - 1, -1, -1)]
    recs = top + bot
    recs += [_seg(f"et{i}", f"t{i}", f"t{i+1}", loop="L") for i in range(width - 1)]
    recs += [_seg(f"eb{i}", f"b{i}", f"b{i+1}", loop="L") for i in range(width - 1)]
    recs += [_seg("ecapR", f"t{width-1}", f"b{width-1}", loop="L"),
             _seg("ecapL", "b0", "t0", loop="L")]
    recs += [_loop("L", walk, closed=True, kind="ring")]
    return Component(
        "transport_loop", recs,
        params={"width": width, "height": height, "zone": zone, "capacity": capacity},
        pins=[{"name": "nw", "node": "t0", "dir": [0, -1], "kind": "rail"}],
        requires={"zones": (zone,)},
        blurb=f"a closed {2*width}-slot orbit -- what a rigid rotation rotates")


def ancilla_dock(depth: float = 1.0, zone: str = "ancilla", capacity: int = 2,
                 spur_capacity: int = 1) -> Component:
    """One ancilla trap on a spur.  Joining its pin to a rail makes that rail degree 3 --
    which is what puts a junction charge on every rigid hop through it (R18)."""
    return Component(
        "ancilla_dock",
        [_site("a", 0.0, -float(depth), zone=zone, cap=capacity),
         _site("j", 0.0, 0.0, zone=zone, cap=capacity),
         _seg("v", "j", "a", length=depth, cap=spur_capacity, labels=("spur",))],
        params={"depth": depth, "zone": zone, "capacity": capacity,
                "spur_capacity": spur_capacity},
        pins=[{"name": "rail", "node": "j", "dir": [0, 1], "kind": "rail"}],
        requires={"zones": (zone,)},
        blurb="an ancilla on a spur -- a dock, and a degree-3 node on the rail")


def gate_zone(zone: str = "gate", capacity: int = 2) -> Component:
    """A trap where a two-qubit gate may happen.  The zone is what makes it one."""
    return Component(
        "gate_zone", [_site("g", 0.0, 0.0, zone=zone, cap=capacity)],
        params={"zone": zone, "capacity": capacity},
        pins=[{"name": "w", "node": "g", "dir": [-1, 0], "kind": "rail"},
              {"name": "e", "node": "g", "dir": [1, 0], "kind": "rail"}],
        requires={"zones": (zone,)},
        blurb="a trap where a gate may be executed")


def loading_zone(zone: str = "load", capacity: int = 8, spur: float = 1.0) -> Component:
    """Where ions enter the machine.  Deep, because loading is slow and batched."""
    return Component(
        "loading_zone",
        [_site("l", 0.0, -float(spur), zone=zone, cap=capacity),
         _site("m", 0.0, 0.0, zone=zone, cap=2),
         _seg("v", "m", "l", length=spur, labels=("spur",))],
        params={"zone": zone, "capacity": capacity, "spur": spur},
        pins=[{"name": "rail", "node": "m", "dir": [0, 1], "kind": "rail"}],
        requires={"zones": (zone,)},
        blurb=f"a photoionization loading trap holding {capacity} ions")


def trap_junction(arms: int = 4, arm: float = 1.0) -> Component:
    """A crossing.  The arms are PINS, not geometry: what you attach decides the degree,
    and the degree is what the cost model charges (`junction_cross` by degree)."""
    arms = int(arms)
    if arms not in (3, 4, 6):
        raise ValueError("a trap junction has 3, 4 or 6 arms")
    dirs = {3: [(1, 0), (-1, 0), (0, 1)],
            4: [(1, 0), (-1, 0), (0, 1), (0, -1)],
            6: [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1)]}[arms]
    return Component(
        "trap_junction", [_junction("j", 0.0, 0.0)],
        params={"arms": arms, "arm": arm},
        pins=[{"name": f"p{i}", "node": "j", "dir": list(d), "kind": "rail"}
              for i, d in enumerate(dirs)],
        blurb=f"a {arms}-way crossing -- charged by degree, so attach all {arms}")


def grid_tile(a: int = 1, b: int = 1, pitch: float = 1.0, zone: str = "trap",
              capacity: int = 2) -> Component:
    """One cell of a junction lattice: junctions at the corners, a trap on each wire."""
    a, b = max(1, int(a)), max(1, int(b))
    recs, segs = [], []
    for i in range(a + 1):
        for j in range(b + 1):
            recs.append(_junction(f"j{i}_{j}", i * pitch, j * pitch))
    for i in range(a + 1):
        for j in range(b):
            recs.append(_site(f"h{i}_{j}", i * pitch, (j + 0.5) * pitch,
                              zone=zone, cap=capacity))
            segs += [_seg(f"hs{i}_{j}", f"j{i}_{j}", f"h{i}_{j}"),
                     _seg(f"hn{i}_{j}", f"h{i}_{j}", f"j{i}_{j+1}")]
    for i in range(a):
        for j in range(b + 1):
            recs.append(_site(f"w{i}_{j}", (i + 0.5) * pitch, j * pitch,
                              zone=zone, cap=capacity))
            segs += [_seg(f"ws{i}_{j}", f"j{i}_{j}", f"w{i}_{j}"),
                     _seg(f"wn{i}_{j}", f"w{i}_{j}", f"j{i+1}_{j}")]
    return Component(
        "grid_tile", recs + segs,
        params={"a": a, "b": b, "pitch": pitch, "zone": zone, "capacity": capacity},
        pins=[{"name": "sw", "node": "j0_0", "dir": [-1, -1], "kind": "rail"},
              {"name": "ne", "node": f"j{a}_{b}", "dir": [1, 1], "kind": "rail"}],
        requires={"zones": (zone,)},
        blurb=f"a {a}x{b} junction lattice with a trap on every wire")


#: The parameters that change how many records a component HAS, and the values the design
#: tool offers for them.  Everything else about a component -- capacities, zones, pitch,
#: depth -- is a substitution or a multiply and needs no enumeration; these four change the
#: shape itself, so the browser cannot compute them and `variants.py` precomputes each one
#: instead.  A range here is a page-byte price and nothing else: widening it costs bytes
#: and zero new code.  See `qccd/arch/variants.py` for why the split falls exactly here.
VARIANT_DOMAINS: Mapping[str, Mapping[str, list]] = {
    "linear_register": {"n": list(range(2, 33))},
    "transport_loop": {"width": list(range(2, 25))},
    "grid_tile": {"a": [1, 2, 3, 4], "b": [1, 2, 3, 4]},
    "trap_junction": {"arms": [3, 4, 6]},
    "ancilla_dock": {},
    "gate_zone": {},
    "loading_zone": {},
}


#: name -> factory.  Parameters are the factory's own keyword arguments, so the palette
#: can generate a form by reflection rather than carrying a second description of them.
CATALOG: Mapping[str, Callable[..., Component]] = {
    "linear_register": linear_register,
    "transport_loop": transport_loop,
    "ancilla_dock": ancilla_dock,
    "gate_zone": gate_zone,
    "loading_zone": loading_zone,
    "trap_junction": trap_junction,
    "grid_tile": grid_tile,
}


def build(name: str, **params) -> Component:
    """One component by name, with its parameters."""
    if name not in CATALOG:
        raise KeyError(f"no component {name!r}; have: {', '.join(sorted(CATALOG))}")
    return CATALOG[name](**params)


def catalog_json() -> list[dict]:
    """The catalogue as data, for the palette -- defaults read off the factories."""
    import inspect

    out = []
    for name, fn in sorted(CATALOG.items()):
        sig = inspect.signature(fn)
        comp = fn()
        out.append({
            "name": name,
            "blurb": comp.blurb,
            "params": {k: (None if p.default is inspect.Parameter.empty else p.default)
                       for k, p in sig.parameters.items()},
            "pins": [dict(p) for p in comp.pins],
            "requires": dict(comp.requires),
            "n_records": len(comp.records),
        })
    return out
