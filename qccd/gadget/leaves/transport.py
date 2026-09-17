"""The gadgets that move ions between blocks.  GADGETS.md §5.1, §5.3.

    tcx<n>     a transversal-CNOT station: two bundles in, n CX pairs, both bundles out
    res<k>     a messenger reservoir: k spare ions, released and accepted one at a time
    xjunc      an X-junction with four arms: one ion from any arm to any other
    chan<L>    a conveyor channel of L sites: the reference a channel is priced from

Every device here is built from the repository's own primitives (sites, junctions,
segments, named open paths), borrows the template architecture's zone types, primitive
curves and control block, and adds only the port zone and handoff classes of
`leaves.common`.  Conveyor moves along a named open path use one declared class,
`conveyor`, which entails no split or merge: a chain of singly occupied traps moving
together is the H2 race track's conveyor belt (arXiv:2305.03828).
"""

from __future__ import annotations

from ...api import Machine
from ...arch.builder import DeviceBuilder
from ...compile.cooling import CoolingPolicy, insert_cooling
from ...cost.models import corrected_model
from ..model import Master, Port
from .common import (LeafBuild, TrackedProgram, add_port_stub, declare_port_classes,
                     declare_port_zone, tidy)

__all__ = ["station", "reservoir", "junction", "channel"]


def _machine(device, name: str) -> Machine:
    m = Machine.from_device(device, name=name)
    declare_port_zone(m)
    declare_port_classes(m)
    m.declare_class("conveyor", type="shift", orbit="rail",
                    note="every ion on a named open path advances one site together")
    return m


def _cooled(prog, m: Machine):
    return insert_cooling(prog, m.arch, corrected_model("qccdsim_jones"),
                          policy=CoolingPolicy()).program


def _size(m: Machine) -> list[float]:
    xs = [nd.pos[0] for nd in m.arch.device.nodes.values()]
    ys = [nd.pos[1] for nd in m.arch.device.nodes.values()]
    return [max(xs) - min(xs), max(ys) - min(ys)]


def _shift(tp: TrackedProgram, rail: list[str], step: int, **meta) -> None:
    """One conveyor cycle: every ion on `rail` moves `step` (+1 / -1) along it."""
    index = {n: i for i, n in enumerate(rail)}
    moves = []
    for ion, where in tp.pos.items():
        if where in index:
            j = index[where] + step
            if 0 <= j < len(rail):
                moves.append((ion, rail[j]))
    tp.move("conveyor", moves, **meta)


# --------------------------------------------------------------------------- station


def station(n: int, *, code: str, name: str | None = None) -> LeafBuild:
    """`tcx<n>`: a comb.  Rail A over n gate traps over rail B; the two bundles fill the
    rails from their stubs, drop into the traps pairwise in one cycle, take one batched CX,
    rise again and leave the way they came (so each bundle returns reversed: GADGETS.md §3)."""
    name = name or f"tcx{n}"
    d = DeviceBuilder("explicit", shape="comb", n=n)
    rail_a = [f"A{i}" for i in range(n)]
    rail_b = [f"B{i}" for i in range(n)]
    for i in range(n):
        d.site(f"A{i}", float(i), 0.0, zone="data", labels=["rail"])
        d.site(f"G{i}", float(i), 1.0, zone="trap", labels=["gate_trap"])
        d.site(f"B{i}", float(i), 2.0, zone="data", labels=["rail"])
        d.segment(f"RA{i}", f"A{i}", f"G{i}", labels=["rung"])
        d.segment(f"RB{i}", f"G{i}", f"B{i}", labels=["rung"])
    for i in range(n - 1):
        d.segment(f"EA{i}", f"A{i}", f"A{i + 1}", loop="LA", labels=["rail"])
        d.segment(f"EB{i}", f"B{i}", f"B{i + 1}", loop="LB", labels=["rail"])
    d.loop("LA", rail_a, closed=False, kind="path")
    d.loop("LB", rail_b, closed=False, kind="path")
    m = _machine(d.build(), name)
    add_port_stub(m, "XA", (-1.0, 0.0), "A0", width=n, segment="SA")
    add_port_stub(m, "XB", (-1.0, 2.0), "B0", width=n, segment="SB")

    ctrl = [f"c{j}" for j in range(n)]
    targ = [f"t{j}" for j in range(n)]
    start = {**{c: "XA" for c in ctrl}, **{t: "XB" for t in targ}}
    b = LeafBuild(master=None, machine=m,
                  roles={**{c: "data" for c in ctrl}, **{t: "data" for t in targ}})
    for key, a_controls in (("cx", True), ("xc", False)):
        tp = TrackedProgram(m, f"{name}.{key}", start)
        for j in range(n):
            if j:
                _shift(tp, rail_a, +1, op="fill")
                _shift(tp, rail_b, +1, op="fill")
            tp.move("port_in", [(ctrl[j], "A0"), (targ[j], "B0")], op="fill")
        tp.move("dock", [(ion, f"G{tp.pos[ion][1:]}") for ion in ctrl + targ], op="pair")
        tp.gate("CX", [(ctrl[j], targ[j]) if a_controls else (targ[j], ctrl[j])
                       for j in range(n)], op=key)
        tp.move("undock", [(ion, f"{'A' if ion[0] == 'c' else 'B'}{tp.pos[ion][1:]}")
                           for ion in ctrl + targ], op="unpair")
        tp.cool(op="before_exit")
        for j in range(n):
            tp.move("port_out", [(tp.at("A0")[0], "XA"), (tp.at("B0")[0], "XB")], op="exit")
            if j + 1 < n:
                _shift(tp, rail_a, -1, op="exit")
                _shift(tp, rail_b, -1, op="exit")
        b.programs[key] = _cooled(tp.prog, m)
        who = "a" if a_controls else "b"
        b.titles[key] = f"transversal CNOT, {n} pairs, the bundle on {who} controls"
        b.notes[key] = ["pairs the i-th ion of each bundle: the bundles must arrive in the "
                        "same order (G1)", "each bundle leaves reversed through its own port"]
        b.expect_end[key] = dict(start)
    tidy(m, b.programs.values())
    b.master = Master(
        name=name, kind="leaf", family="station", title=f"transversal-CNOT station ({n} pairs)",
        doc="a comb: two conveyor rails over one row of gate traps", size=_size(m),
        ports=[Port("a", "inout", "data", width=n, side="W", at=0.0, order="lifo",
                    node="A0", stub="XA", code=code),
               Port("b", "inout", "data", width=n, side="W", at=1.0, order="lifo",
                    node="B0", stub="XB", code=code)],
        inventory={}, capacity=2 * n, params={"pairs": n, "code": code}, device=name)
    return b


# --------------------------------------------------------------------------- reservoir


def reservoir(k: int = 4, *, name: str | None = None) -> LeafBuild:
    """`res<k>`: k traps in a row, the port at the first.  A messenger is prepared here
    (reset, and H for an X parity) before it leaves, and read here (H, measure) when it
    returns; the row closes up behind a departure and opens for an arrival."""
    name = name or f"res{k}"
    d = DeviceBuilder("explicit", shape="reservoir", n=k)
    rail = [f"R{i}" for i in range(k)]
    for i in range(k):
        d.site(f"R{i}", float(i), 0.0, zone="trap", labels=["rail", "reservoir"])
    for i in range(k - 1):
        d.segment(f"ER{i}", f"R{i}", f"R{i + 1}", loop="LR", labels=["rail"])
    d.loop("LR", rail, closed=False, kind="path")
    m = _machine(d.build(), name)
    add_port_stub(m, "X", (-1.0, 0.0), "R0", width=1, segment="SX")

    ions = [f"m{i}" for i in range(k)]
    full = {ion: rail[i] for i, ion in enumerate(ions)}
    b = LeafBuild(master=None, machine=m, home=full, roles={i: "messenger" for i in ions})

    part = {ions[i]: rail[i - 1] for i in range(1, k)}
    for basis in ("Z", "X"):
        tp = TrackedProgram(m, f"{name}.release.{basis}", full)
        tp.reset([ions[0]], op="release")
        if basis == "X":
            tp.gate1("H", [ions[0]], op="release")
        tp.cool(op="release")
        tp.move("port_out", [(ions[0], "X")], op="release")
        _shift(tp, rail, -1, op="close_up")
        key = f"release.{basis}"
        b.programs[key] = tp.prog
        b.titles[key] = f"one messenger out, prepared in |{'0' if basis == 'Z' else '+'}>, cold"
        b.expect_end[key] = {ions[0]: "X", **part}

        tp = TrackedProgram(m, f"{name}.accept.{basis}", {**part, ions[0]: "X"})
        _shift(tp, rail, +1, op="make_room")
        tp.move("port_in", [(ions[0], "R0")], op="accept")
        tp.cool(op="accept")
        if basis == "X":
            tp.gate1("H", [ions[0]], op="accept")
        tp.measure([ions[0]], op="accept")
        key = f"accept.{basis}"
        b.programs[key] = _cooled(tp.prog, m)
        b.titles[key] = f"one messenger back in, measured in the {basis} basis"
        b.expect_end[key] = dict(full)
    for op in list(b.programs):
        b.notes[op] = [f"characterized with {k - 1} of {k} messengers on the moving side; the "
                       f"row shift is one waveform at any occupancy"]

    tidy(m, b.programs.values())
    b.master = Master(
        name=name, kind="leaf", family="reservoir", title=f"messenger reservoir ({k})",
        doc="spare ions for messengers, reset and cooled on the way out", size=_size(m),
        ports=[Port("port", "inout", "messenger", width=1, side="W", at=0.5,
                    node="R0", stub="X")],
        inventory={"messenger": k}, capacity=k, params={"slots": k}, device=name)
    return b


# --------------------------------------------------------------------------- junction


def junction(*, arm: int = 1, name: str | None = None) -> LeafBuild:
    """`xjunc`: a degree-4 junction with one trap on each arm.  `pass` carries one ion from
    the west stub to the east stub; every other pair of arms crosses the same junction."""
    name = name or "xjunc"
    d = DeviceBuilder("explicit", shape="xjunction")
    d.junction("J", 0.0, 0.0)
    arms = {"N": (0.0, -1.0), "E": (1.0, 0.0), "S": (0.0, 1.0), "W": (-1.0, 0.0)}
    for side, (x, y) in arms.items():
        d.site(side, x * arm, y * arm, zone="data", labels=["arm"])
        d.segment(f"J{side}", "J", side, length=float(arm), labels=["arm"])
    m = _machine(d.build(), name)
    for side, (x, y) in arms.items():
        add_port_stub(m, f"X{side}", (x * (arm + 1), y * (arm + 1)), side, width=1,
                      segment=f"S{side}")

    tp = TrackedProgram(m, f"{name}.pass", {"i0": "XW"})
    tp.move("port_in", [("i0", "W")], op="pass")
    tp.move("shuttle", [("i0", "E")], via={"i0": ["JW", "JE"]}, op="pass")
    tp.move("port_out", [("i0", "XE")], op="pass")
    b = LeafBuild(master=None, machine=m, roles={"i0": "any"})
    b.programs["pass"] = tp.prog
    b.titles["pass"] = "one ion across the junction"
    b.expect_end["pass"] = {"i0": "XE"}
    b.notes["pass"] = ["west to east; the junction is degree 4 for every pair of arms, so "
                       "turning costs what crossing does"]
    tidy(m, b.programs.values())
    b.master = Master(
        name=name, kind="leaf", family="junction", title="X-junction",
        doc="four arms meeting at one degree-4 junction", size=_size(m),
        ports=[Port(p, "inout", "any", width=1, side=s, at=0.5, node=s, stub=f"X{s}")
               for p, s in (("n", "N"), ("e", "E"), ("s", "S"), ("w", "W"))],
        inventory={}, capacity=4, params={"arm": arm}, device=name)
    return b


# --------------------------------------------------------------------------- channel


def channel(length: int, count: int = 1, *, name: str | None = None) -> LeafBuild:
    """`chan<L>`: L data sites in a row, a stub at each end; `carry` conveys `count` ions
    from the west stub to the east stub, the whole row advancing one site per cycle."""
    name = name or f"chan{length}"
    d = DeviceBuilder("explicit", shape="channel", n=length)
    rail = [f"C{i}" for i in range(length)]
    for i in range(length):
        d.site(f"C{i}", float(i), 0.0, zone="data", labels=["rail", "channel"])
    for i in range(length - 1):
        d.segment(f"EC{i}", f"C{i}", f"C{i + 1}", loop="LC", labels=["rail"])
    if length > 1:            # a path needs two nodes; a one-site channel is only a handoff
        d.loop("LC", rail, closed=False, kind="path")
    m = _machine(d.build(), name)
    add_port_stub(m, "XW", (-1.0, 0.0), "C0", width=count, segment="SW")
    add_port_stub(m, "XE", (float(length), 0.0), f"C{length - 1}", width=count, segment="SE")

    ions = [f"i{j}" for j in range(count)]
    tp = TrackedProgram(m, f"{name}.carry", {ion: "XW" for ion in ions})
    queue = list(ions)
    delivered = 0
    while delivered < count:
        last = tp.at(rail[-1])
        if last:
            tp.move("port_out", [(last[0], "XE")], op="deliver")
            delivered += 1
        if any(tp.pos[i] in rail for i in ions):
            _shift(tp, rail, +1, op="convey")
        if queue and not tp.at(rail[0]):
            tp.move("port_in", [(queue.pop(0), "C0")], op="load")
    b = LeafBuild(master=None, machine=m, roles={i: "any" for i in ions})
    b.programs["carry"] = tp.prog
    b.titles["carry"] = f"{count} ion(s) along {length} sites"
    b.expect_end["carry"] = {ion: "XE" for ion in ions}
    b.params["carry"] = {"length": length, "count": count}
    tidy(m, b.programs.values())
    b.master = Master(
        name=name, kind="leaf", family="channel", title=f"channel, {length} sites",
        doc="a conveyor: the reference every channel in a design is priced from",
        size=_size(m),
        ports=[Port("w", "inout", "any", width=count, side="W", at=0.5, node="C0", stub="XW"),
               Port("e", "inout", "any", width=count, side="E", at=0.5,
                    node=f"C{length - 1}", stub="XE")],
        inventory={}, capacity=length, params={"length": length}, device=name)
    return b
