"""The verified place library for the rotated surface code (d = 3).  GADGETS.md §9.

Every place is a leaf gadget: a device, resident ions, doors, and op programs.  Unlike the
beta leaves, every op here carries a *logic specification* next to its program, and
characterization checks the program's own circuit against it (`qccd.gadget.logic`):

    place          category     ops                  specification
    prep_d3        prep         prep.Z  prep.X       1 -> Z̄ (or X̄) exactly; every stabilizer holds
    se_d3          se           se.1  se.3           every stabilizer measured and holds; X̄, Z̄ kept
    zone_d3        zone         check_in check_out   identity on every ion
    tcx_d3         operation    cx                   CNOT ion by ion (36 exact flows)
    ls_d3          surgery      zz  xx               Z̄Z̄ (X̄X̄) measured; Z̄_A, Z̄_B, X̄X̄ (…) kept
    read_d3        readout      read.Z  read.X       every data ion measured in the basis
    inj_d3         injection    inject               X -> X̄, Z -> Z̄ from the magic ion; stabilizers hold
    msf15          factory      produce              T|+> on the output when all 14 checks pass
    depot9         depot        load.k  dump.9       none: fresh ions carry no promise
    junc9          road         pass.<a>.<b>         identity on every ion

A spec is complete (its flows pin the channel down; `logic.spec`), and every error-
correcting op also gets a fault-distance experiment (`logic.experiment`).

Ion names are local and fixed per op: a block's data ions are `d0 … d8` in code-qubit
order (row-major on the 3×3 patch), a second block's `e0 … e8`; residents are `a<check>`
(ancillas), `s0 …` (seam or staff).  A block always leaves in the order it arrived.
"""

from __future__ import annotations

from ...api import Machine
from ...arch.builder import DeviceBuilder
from ...compile.cooling import CoolingPolicy, insert_cooling
from ...cost.models import corrected_model
from ..logic.experiment import Block, LogicalFlow, logical_pauli
from ..logic.spec import Flow, Spec
from ..model import Master, Port
from ..surface import merged_patch, patch
from .bay import BayProgram, bay, place
from .common import (LeafBuild, TrackedProgram, add_port_stub, declare_port_classes,
                     declare_port_zone, tidy)
from .factory import ring_circuit, rm15_circuit

__all__ = ["prep", "clinic", "zone", "workshop", "bridge", "post_office", "pharmacy",
           "factory", "depot", "junction9", "PLACES", "D"]

D = 3
KEEP = ("rotate_cw", "rotate_ccw", "dock", "undock", "port_in", "port_out")


def _cooled(prog, m: Machine):
    return insert_cooling(prog, m.arch, corrected_model("qccdsim_jones"),
                          policy=CoolingPolicy()).program


def _surface():
    p = patch(D, D, name=f"surface_d{D}")
    return p, p.code


def _stab_flows(p, ions, *, measured: bool, holds: bool, prefix: str = "") -> list[Flow]:
    out = []
    for ch in p.checks:
        P = {ions[q]: ch.basis for q in ch.support}
        if measured:
            out.append(Flow(f"{prefix}{ch.name} measured", P, {}, "measure", role="stabilizer"))
        if holds:
            out.append(Flow(f"{prefix}{ch.name} holds", {}, dict(P), "frame", role="stabilizer"))
    return out


def _L(code, ions, letter):
    return logical_pauli(Block(code, ions), 0, letter)


def _mul(*paulis):
    acc: dict[str, str] = {}
    t = {"I": (0, 0), "X": (1, 0), "Z": (0, 1), "Y": (1, 1)}
    back = {v: k for k, v in t.items()}
    for p in paulis:
        for q, s in p.items():
            a, b = t[acc.get(q, "I")], t[s]
            r = back[(a[0] ^ b[0], a[1] ^ b[1])]
            if r == "I":
                acc.pop(q, None)
            else:
                acc[q] = r
    return acc


def _classical_ports(*, syndrome: int = 0, outcome: int = 0, decision: bool = False,
                     side: str = "S") -> list[Port]:
    """The wire ports of a place: what it tells the classical half, and what it waits for.

    A classical port carries bits, not ions: it has no device node and no stub, because
    nothing is shuttled through it -- it is where a wire is soldered on (GADGETS.md §10).
    `syndrome` bits are the raw check outcomes of a round, `outcome` bits a decoded logical
    measurement, and `decision` is the guard a conditional op waits for."""
    out = []
    if syndrome:
        out.append(Port("syn", "out", "any", 0, side, 0.35, kind="classical",
                        signal="syndrome", bits=syndrome))
    if outcome:
        out.append(Port("res", "out", "any", 0, side, 0.6, kind="classical",
                        signal="outcome", bits=outcome))
    if decision:
        out.append(Port("ctl", "in", "any", 0, side, 0.85, kind="classical",
                        signal="decision", bits=1))
    return out


def _master(build, *, name, category, title, doc, ports, inventory, capacity, params, size):
    build.master = Master(name=name, kind="leaf", family=category, title=title, doc=doc,
                          size=size, ports=ports, inventory=inventory, capacity=capacity,
                          params=params, device=name)
    build.master_name = name
    return build


def _anc_names(p):
    return {c.name: f"a{c.name}" for c in p.checks}


# --------------------------------------------------------------------------- clinic


def clinic(*, name: str = "se_d3", rounds=(1, 3)) -> LeafBuild:
    """Syndrome extraction: a block comes in, takes `r` rounds, and leaves."""
    p, code = _surface()
    W = 12
    docks = [1 + 3 * i for i in range(8)]
    b = bay(name, W, docks, {"in": "TL", "out": "TR"}, {"in": 9, "out": 9})
    ds, ad, steps = place(p, b.N, docks)
    data = [f"d{q}" for q in range(p.n)]
    anc = _anc_names(p)
    home = {anc[c]: f"A{ad[c]}" for c in anc}
    build = LeafBuild(master=None, machine=b.machine, home=home,
                      roles={**{a: "ancilla" for a in home}, **{d: "data" for d in data}})
    blk = Block(code, data, "A")
    for r in rounds:
        key = f"se.{r}"
        bp = BayProgram(b, f"{name}.{key}", {**home, **{d: "X_in" for d in data}})
        bp.absorb("in", data, [ds[q] for q in range(p.n)], op="enter")
        bp.restore(op="enter")
        for k in range(r):
            bp.conveyor_round(p, dict(enumerate(data)), anc, ad, round_index=k)
            bp.restore(op="round")
        bp.emit("out", data, cool_every=5, op="leave")
        build.programs[key] = _cooled(bp.prog, b.machine)
        build.titles[key] = f"{r} syndrome-extraction round{'s' if r > 1 else ''} on a visiting block"
        build.expect_end[key] = {**home, **{d: "X_out" for d in data}}
        build.params[key] = {"rounds": r, "conveyor_steps": steps}
        build.notes[key] = [
            "ancillas live in the dock traps; the ring turns and each data ion docks at its "
            "check's ancilla when the schedule allows (Tomita-Svore order, as a partial order)"]
        build.specs[key] = Spec(
            f"{r} round(s) of syndrome extraction", data, data,
            _stab_flows(p, data, measured=True, holds=True)
            + [Flow("X̄ kept", _L(code, data, "X"), _L(code, data, "X"), "exact", role="logical"),
               Flow("Z̄ kept", _L(code, data, "Z"), _L(code, data, "Z"), "exact", role="logical")])
        build.experiments[key] = dict(
            blocks_in=[blk], blocks_out=[blk], expect="ft",
            flows=[LogicalFlow("X̄", [("A", 0, "X")], [("A", 0, "X")]),
                   LogicalFlow("Z̄", [("A", 0, "Z")], [("A", 0, "Z")])])
    tidy(b.machine, build.programs.values(), keep_classes=KEEP)
    build.extra = {"patch": p, "data_slots": ds, "anc_docks": ad}
    return _master(build, name=name, category="se", title="Syndrome extraction (d=3)",
                   doc="8 resident ancillas in dock traps on a 24-slot ring; a visiting block "
                       "rides the ring past them",
                   ports=[Port("in", "in", "data", 9, "W", 0.3, node=b.trap("in"),
                               stub=b.stub("in"), code=code.name),
                          Port("out", "out", "data", 9, "E", 0.3, node=b.trap("out"),
                               stub=b.stub("out"), code=code.name)]
                         + _classical_ports(syndrome=8 * max(rounds)),
                   inventory={"ancilla": 8}, capacity=8 + 9,
                   params={"code": code.summary(), "ring_slots": b.N, "docks": docks},
                   size=b.size())


# --------------------------------------------------------------------------- prep


def prep(*, name: str = "prep_d3") -> LeafBuild:
    """Preparation: 9 fresh ions in, a |0̄> or |+̄> block out."""
    p, code = _surface()
    W = 12
    docks = [1 + 3 * i for i in range(8)]
    b = bay(name, W, docks, {"in": "TL", "out": "TR"}, {"in": 9, "out": 9})
    ds, ad, steps = place(p, b.N, docks, seed=11)
    data = [f"d{q}" for q in range(p.n)]
    anc = _anc_names(p)
    home = {anc[c]: f"A{ad[c]}" for c in anc}
    build = LeafBuild(master=None, machine=b.machine, home=home,
                      roles={**{a: "ancilla" for a in home}, **{d: "fresh" for d in data}})
    for basis in ("Z", "X"):
        key = f"prep.{basis}"
        bp = BayProgram(b, f"{name}.{key}", {**home, **{d: "X_in" for d in data}})
        bp.absorb("in", data, [ds[q] for q in range(p.n)], op="enter")
        bp.restore(op="enter")

        def init(batch, basis=basis):
            bp.reset(batch, op="init")
            if basis == "X":
                bp.gate1("H", batch, op="init")

        bp.at_docks(data, init, op="init")
        bp.restore(op="init")
        bp.conveyor_round(p, dict(enumerate(data)), anc, ad, round_index=0)
        bp.restore(op="round")
        bp.emit("out", data, cool_every=5, op="leave")
        build.programs[key] = _cooled(bp.prog, b.machine)
        ket = "|0̄⟩" if basis == "Z" else "|+̄⟩"
        build.titles[key] = f"a fresh block in {ket}"
        build.expect_end[key] = {**home, **{d: "X_out" for d in data}}
        build.notes[key] = [f"data reset{' and H' if basis == 'X' else ''} at the docks, then one "
                            f"syndrome round: the {'Z' if basis == 'Z' else 'X'} checks come out "
                            f"deterministic, the others random (their values are the frame)"]
        L = _L(code, data, basis)
        build.specs[key] = Spec(f"prepare {ket}", [], data,
                                [Flow(f"{basis}̄ = +1", {}, L, "exact", role="logical")]
                                + _stab_flows(p, data, measured=False, holds=True))
        build.experiments[key] = dict(
            blocks_out=[Block(code, data, "A")], expect="ft",
            flows=[LogicalFlow(f"{basis}̄", [], [("A", 0, basis)])])
    tidy(b.machine, build.programs.values(), keep_classes=KEEP)
    return _master(build, name=name, category="prep", title="Logical preparation (d=3)",
                   doc="fresh ions are reset at the docks, then one round of the resident "
                       "ancillas makes them a code block",
                   ports=[Port("in", "in", "fresh", 9, "W", 0.3, node=b.trap("in"),
                               stub=b.stub("in")),
                          Port("out", "out", "data", 9, "E", 0.3, node=b.trap("out"),
                               stub=b.stub("out"), code=code.name)]
                         + _classical_ports(syndrome=8),
                   inventory={"ancilla": 8}, capacity=17,
                   params={"code": code.summary(), "ring_slots": b.N, "docks": docks},
                   size=b.size())


# --------------------------------------------------------------------------- readout


def post_office(*, name: str = "read_d3") -> LeafBuild:
    """Readout: a block in, every data ion measured, the ions out to a depot."""
    p, code = _surface()
    W = 6
    docks = [1, 4, 7, 10]
    b = bay(name, W, docks, {"in": "TL", "out": "TR"}, {"in": 9, "out": 9})
    data = [f"d{q}" for q in range(p.n)]
    homes = [s for s in range(b.N)][: p.n]
    build = LeafBuild(master=None, machine=b.machine, home={},
                      roles={d: "data" for d in data})
    for basis in ("Z", "X"):
        key = f"read.{basis}"
        bp = BayProgram(b, f"{name}.{key}", {d: "X_in" for d in data})
        bp.absorb("in", data, homes, op="enter")
        bp.restore(op="enter")

        def read(batch, basis=basis):
            if basis == "X":
                bp.gate1("H", batch, op="read")
            bp.measure(batch, op="read", data=[int(i[1:]) for i in batch])

        bp.at_docks(data, read, op="read")
        bp.restore(op="read")
        bp.emit("out", data, op="leave")
        build.programs[key] = _cooled(bp.prog, b.machine)
        build.titles[key] = f"measure every data ion in {basis}; Z̄ is the parity of a row" \
            if basis == "Z" else "measure every data ion in X; X̄ is the parity of a column"
        build.expect_end[key] = {d: "X_out" for d in data}
        L = _L(code, data, basis)
        build.specs[key] = Spec(
            f"read out in {basis}", data, [],
            [Flow(f"{basis}({d}) measured", {d: basis}, {}, "measure") for d in data]
            + [Flow(f"{basis}̄ measured", L, {}, "measure", role="logical")])
        build.experiments[key] = dict(
            blocks_in=[Block(code, data, "A")], expect="ft",
            flows=[LogicalFlow(f"{basis}̄", [("A", 0, basis)], [])])
    tidy(b.machine, build.programs.values(), keep_classes=KEEP)
    return _master(build, name=name, category="readout", title="Logical readout (d=3)",
                   doc="four measurement docks on a 12-slot ring; a block's ions are read in "
                       "parallel batches and leave for a depot",
                   ports=[Port("in", "in", "data", 9, "W", 0.3, node=b.trap("in"),
                               stub=b.stub("in"), code=code.name),
                          Port("out", "out", "fresh", 9, "E", 0.3, node=b.trap("out"),
                               stub=b.stub("out"))]
                         + _classical_ports(syndrome=9, outcome=1, decision=True),
                   inventory={}, capacity=9, params={"code": code.summary(), "ring_slots": b.N},
                   size=b.size())


# --------------------------------------------------------------------------- zone


def zone(*, name: str = "zone_d3") -> LeafBuild:
    """A logical zone: one block rests here between operations."""
    p, code = _surface()
    b = bay(name, 6, [], {"in": "TL", "out": "TR"}, {"in": 9, "out": 9})
    data = [f"d{q}" for q in range(p.n)]
    homes = list(range(p.n))
    rest = {d: f"S{h}" for d, h in zip(data, homes)}
    build = LeafBuild(master=None, machine=b.machine, home={}, roles={d: "data" for d in data})
    ident = [Flow(f"{s}({d})", {d: s}, {d: s}, "exact") for d in data for s in ("X", "Z")]
    bp = BayProgram(b, f"{name}.check_in", {d: "X_in" for d in data})
    bp.absorb("in", data, homes, op="enter")
    bp.restore(op="enter")
    build.programs["check_in"] = _cooled(bp.prog, b.machine)
    build.titles["check_in"] = "a block checks in and rests on the ring"
    build.expect_end["check_in"] = dict(rest)
    build.specs["check_in"] = Spec("store", data, data, ident)
    bp = BayProgram(b, f"{name}.check_out", dict(rest))
    bp.emit("out", data, op="leave")
    build.programs["check_out"] = bp.prog
    build.titles["check_out"] = "the block checks out, in the order it arrived"
    build.expect_end["check_out"] = {d: "X_out" for d in data}
    build.specs["check_out"] = Spec("release", data, data, ident)
    build.extra = {"rest": rest}
    tidy(b.machine, build.programs.values(), keep_classes=KEEP)
    return _master(build, name=name, category="zone", title="Logical zone (1 block)",
                   doc="a 12-slot storage ring with no lasers: one block rests here",
                   ports=[Port("in", "in", "data", 9, "W", 0.3, node=b.trap("in"),
                               stub=b.stub("in"), code=code.name),
                          Port("out", "out", "data", 9, "E", 0.3, node=b.trap("out"),
                               stub=b.stub("out"), code=code.name)],
                   inventory={}, capacity=9, params={"code": code.summary(), "ring_slots": b.N,
                                                      "rest": rest},
                   size=b.size())


# --------------------------------------------------------------------------- depot


def depot(*, name: str = "depot9", slots: int = 9) -> LeafBuild:
    """An ion depot: a loading rail that sends out fresh ions and takes used ones back.

    The rail's sites are in the template's `load` zone (photoionization loading).  A `load`
    op starts with the ions already loaded on the rail -- loading itself is not a TSIR
    instruction -- and sends k of them out through the door, closing the row up behind each
    one; `dump` takes a bundle in and keeps it.  So a depot is a source and a sink: the
    ions it sends out are new, and the ions it takes in leave the computation."""
    d = DeviceBuilder("explicit", shape="depot", n=slots)
    rail = [f"R{i}" for i in range(slots)]
    for i in range(slots):
        d.site(f"R{i}", float(i), 0.0, zone="load", labels=["rail", "load"])
    for i in range(slots - 1):
        d.segment(f"ER{i}", f"R{i}", f"R{i + 1}", loop="LR", labels=["rail"])
    d.loop("LR", rail, closed=False, kind="path")
    m = Machine.from_device(d.build(), name=name)
    m.set_zone("load", capacity=1)
    declare_port_zone(m)
    declare_port_classes(m)
    m.declare_class("conveyor", type="shift", orbit="rail",
                    note="every ion on a named open path advances one site together")
    add_port_stub(m, "X_out", (0.0, -1.0), "R0", width=9, segment="S_out")
    add_port_stub(m, "X_in", (float(slots - 1), -1.0), f"R{slots - 1}", width=9, segment="S_in")
    build = LeafBuild(master=None, machine=m, home={}, roles={})

    def shift(tp, step, **meta):
        index = {nd: i for i, nd in enumerate(rail)}
        moves = [(ion, rail[index[w] + step]) for ion, w in tp.pos.items()
                 if w in index and 0 <= index[w] + step < len(rail)]
        tp.move("conveyor", moves, **meta)

    for k in (1, 8, 9):
        ions = [f"i{j}" for j in range(k)]
        tp = TrackedProgram(m, f"{name}.load.{k}", {ion: rail[j] for j, ion in enumerate(ions)})
        tp.cool(op="load")
        for j, ion in enumerate(ions):
            tp.move("port_out", [(ion, "X_out")], op="load")
            if j + 1 < k:
                shift(tp, -1, op="close_up")
        key = f"load.{k}"
        build.programs[key] = tp.prog
        build.titles[key] = f"{k} freshly loaded ion{'s' if k > 1 else ''} out"
        build.expect_end[key] = {ion: "X_out" for ion in ions}
        build.roles.update({ion: "fresh" for ion in ions})
    ions = [f"i{j}" for j in range(9)]
    tp = TrackedProgram(m, f"{name}.dump.9", {ion: "X_in" for ion in ions})
    for j, ion in enumerate(ions):
        if j:
            shift(tp, -1, op="make_room")
        tp.move("port_in", [(ion, rail[-1])], op="dump")
    build.programs["dump.9"] = tp.prog
    build.titles["dump.9"] = "nine used ions in, out of the computation"
    build.expect_end["dump.9"] = {ion: rail[slots - 9 + j] for j, ion in enumerate(ions)}
    for key in build.programs:
        build.notes[key] = ["a depot's ions carry no quantum promise: every place that takes "
                            "fresh ions resets them before use (their logic checks start from "
                            "an arbitrary input state)"]
    tidy(m, build.programs.values())
    xs = [nd.pos[0] for nd in m.arch.device.nodes.values()]
    ys = [nd.pos[1] for nd in m.arch.device.nodes.values()]
    return _master(build, name=name, category="depot", title="Ion depot",
                   doc="a loading rail: fresh ions out (1, 8 or 9 at a time), used ions in",
                   ports=[Port("out", "out", "fresh", 9, "N", 0.0, node="R0", stub="X_out"),
                          Port("in", "in", "fresh", 9, "N", 1.0, node=f"R{slots - 1}",
                               stub="X_in")],
                   inventory={}, capacity=slots, params={"slots": slots, "source": True},
                   size=[max(xs) - min(xs), max(ys) - min(ys)])


# --------------------------------------------------------------------------- junction


def junction9(*, name: str = "junc9") -> LeafBuild:
    """A road junction for bundles: four arms of one site around a degree-4 junction; a
    `pass.<from>.<to>` op carries a bundle of nine through it one ion at a time."""
    d = DeviceBuilder("explicit", shape="junction9")
    d.junction("J", 0.0, 0.0)
    arms = {"N": (0.0, -1.0), "E": (1.0, 0.0), "S": (0.0, 1.0), "W": (-1.0, 0.0)}
    for side, (x, y) in arms.items():
        d.site(side, x, y, zone="data", labels=["arm"])
        d.segment(f"J{side}", "J", side, length=1.0, labels=["arm"])
    m = Machine.from_device(d.build(), name=name)
    declare_port_zone(m)
    declare_port_classes(m)
    for side, (x, y) in arms.items():
        add_port_stub(m, f"X{side}", (2 * x, 2 * y), side, width=9, segment=f"S{side}")
    build = LeafBuild(master=None, machine=m, home={}, roles={f"i{j}": "any" for j in range(9)})
    # a block is nine ions; a factory's input is one and an injection's fresh ions are eight
    for k in (9, 8, 1):
        ions = [f"i{j}" for j in range(k)]
        for a in arms:
            for b in arms:
                if a == b:
                    continue
                key = f"pass.{a}.{b}" + ("" if k == 9 else f".{k}")
                tp = TrackedProgram(m, f"{name}.{key}", {i: f"X{a}" for i in ions})
                for ion in ions:
                    tp.move("port_in", [(ion, a)], op="pass")
                    tp.move("shuttle", [(ion, b)], via={ion: [f"J{a}", f"J{b}"]}, op="pass")
                    tp.move("port_out", [(ion, f"X{b}")], op="pass")
                build.programs[key] = tp.prog
                build.titles[key] = f"a bundle of {k} from {a} to {b}"
                build.expect_end[key] = {i: f"X{b}" for i in ions}
                build.specs[key] = Spec("pass", ions, ions,
                                        [Flow(f"{s}({i})", {i: s}, {i: s}, "exact")
                                         for i in ions for s in ("X", "Z")])
    tidy(m, build.programs.values())
    return _master(build, name=name, category="road", title="Junction (bundles of 9)",
                   doc="a degree-4 junction with one site on each arm; bundles cross ion by ion",
                   ports=[Port(p.lower(), "inout", "any", 9, s, 0.5, node=s, stub=f"X{s}")
                          for p, s in (("n", "N"), ("e", "E"), ("s", "S"), ("w", "W"))],
                   inventory={}, capacity=4, params={}, size=[2.0, 2.0])


# --------------------------------------------------------------------------- workshop


def workshop(*, name: str = "tcx_d3", n: int = 9) -> LeafBuild:
    """Transversal CNOT: two blocks flow through a comb, pair by pair."""
    p, code = _surface()
    d = DeviceBuilder("explicit", shape="flow_comb", n=n)
    ra = [f"A{i}" for i in range(n)]
    rb = [f"B{i}" for i in range(n)]
    for i in range(n):
        d.site(f"A{i}", float(i), 0.0, zone="data", labels=["rail"])
        d.site(f"G{i}", float(i), 1.0, zone="trap", labels=["gate_trap"])
        d.site(f"B{i}", float(i), 2.0, zone="data", labels=["rail"])
        d.segment(f"RA{i}", f"A{i}", f"G{i}", labels=["rung"])
        d.segment(f"RB{i}", f"G{i}", f"B{i}", labels=["rung"])
    for i in range(n - 1):
        d.segment(f"EA{i}", f"A{i}", f"A{i + 1}", loop="LA", labels=["rail"])
        d.segment(f"EB{i}", f"B{i}", f"B{i + 1}", loop="LB", labels=["rail"])
    d.loop("LA", ra, closed=False, kind="path")
    d.loop("LB", rb, closed=False, kind="path")
    m = Machine.from_device(d.build(), name=name)
    declare_port_zone(m)
    declare_port_classes(m)
    m.declare_class("conveyor", type="shift", orbit="rail",
                    note="every ion on a named open path advances one site together")
    # doors face the streets: rail A's above it, rail B's below it
    add_port_stub(m, "XA_in", (0.0, -1.0), "A0", width=n, segment="SA_in")
    add_port_stub(m, "XB_in", (0.0, 3.0), "B0", width=n, segment="SB_in")
    add_port_stub(m, "XA_out", (float(n - 1), -1.0), f"A{n - 1}", width=n, segment="SA_out")
    add_port_stub(m, "XB_out", (float(n - 1), 3.0), f"B{n - 1}", width=n, segment="SB_out")

    ctrl = [f"d{j}" for j in range(n)]
    targ = [f"e{j}" for j in range(n)]
    build = LeafBuild(master=None, machine=m, home={},
                      roles={**{c: "data" for c in ctrl}, **{t: "data" for t in targ}})

    def shift(tp, rail, **meta):
        index = {nd: i for i, nd in enumerate(rail)}
        moves = [(ion, rail[index[w] + 1]) for ion, w in tp.pos.items()
                 if w in index and index[w] + 1 < len(rail)]
        tp.move("conveyor", moves, **meta)

    tp = TrackedProgram(m, f"{name}.cx", {**{c: "XA_in" for c in ctrl},
                                          **{t: "XB_in" for t in targ}})
    for j in range(n):
        if j:
            shift(tp, ra, op="fill")
            shift(tp, rb, op="fill")
        tp.move("port_in", [(ctrl[j], "A0"), (targ[j], "B0")], op="fill")
    tp.move("dock", [(ion, f"G{tp.pos[ion][1:]}") for ion in ctrl + targ], op="pair")
    tp.gate("CX", [(ctrl[j], targ[j]) for j in range(n)], op="cx")
    tp.move("undock", [(ion, f"{'A' if ion[0] == 'd' else 'B'}{tp.pos[ion][1:]}")
                       for ion in ctrl + targ], op="unpair")
    tp.cool(op="before_exit")
    for j in range(n):
        tp.move("port_out", [(tp.at(f"A{n - 1}")[0], "XA_out"),
                             (tp.at(f"B{n - 1}")[0], "XB_out")], op="exit")
        if j + 1 < n:
            shift(tp, ra, op="exit")
            shift(tp, rb, op="exit")
    build.programs["cx"] = _cooled(tp.prog, m)
    build.titles["cx"] = "transversal CNOT: block on rail A controls block on rail B"
    build.expect_end["cx"] = {**{c: "XA_out" for c in ctrl}, **{t: "XB_out" for t in targ}}
    build.notes["cx"] = ["the i-th ion of one block meets only the i-th ion of the other: a "
                         "fault spreads to at most one ion per block"]
    flows = []
    for c, t in zip(ctrl, targ):
        flows += [Flow(f"X({c})", {c: "X"}, {c: "X", t: "X"}, "exact"),
                  Flow(f"Z({c})", {c: "Z"}, {c: "Z"}, "exact"),
                  Flow(f"X({t})", {t: "X"}, {t: "X"}, "exact"),
                  Flow(f"Z({t})", {t: "Z"}, {c: "Z", t: "Z"}, "exact")]
    LA = {s: _L(code, ctrl, s) for s in ("X", "Z")}
    LB = {s: _L(code, targ, s) for s in ("X", "Z")}
    flows += [Flow("X̄_A → X̄_A X̄_B", LA["X"], _mul(LA["X"], LB["X"]), "exact", role="logical"),
              Flow("Z̄_A kept", LA["Z"], LA["Z"], "exact", role="logical"),
              Flow("X̄_B kept", LB["X"], LB["X"], "exact", role="logical"),
              Flow("Z̄_B → Z̄_A Z̄_B", LB["Z"], _mul(LA["Z"], LB["Z"]), "exact", role="logical")]
    build.specs["cx"] = Spec("transversal CNOT", ctrl + targ, ctrl + targ, flows)
    A, B = Block(code, ctrl, "A"), Block(code, targ, "B")
    build.experiments["cx"] = dict(
        blocks_in=[A, B], blocks_out=[A, B], expect="ft",
        flows=[LogicalFlow("X̄_A", [("A", 0, "X")], [("A", 0, "X"), ("B", 0, "X")]),
               LogicalFlow("Z̄_A", [("A", 0, "Z")], [("A", 0, "Z")]),
               LogicalFlow("X̄_B", [("B", 0, "X")], [("B", 0, "X")]),
               LogicalFlow("Z̄_B", [("B", 0, "Z")], [("A", 0, "Z"), ("B", 0, "Z")])])
    tidy(m, build.programs.values())
    xs = [nd.pos[0] for nd in m.arch.device.nodes.values()]
    ys = [nd.pos[1] for nd in m.arch.device.nodes.values()]
    return _master(build, name=name, category="operation", title="Transversal CNOT (d=3)",
                   doc="two conveyor rails over nine gate traps; both blocks flow through "
                       "west to east and meet ion by ion",
                   ports=[Port("a_in", "in", "data", n, "N", 0.0, node="A0", stub="XA_in",
                               code=code.name),
                          Port("b_in", "in", "data", n, "S", 0.0, node="B0", stub="XB_in",
                               code=code.name),
                          Port("a_out", "out", "data", n, "N", 1.0, node=f"A{n - 1}",
                               stub="XA_out", code=code.name),
                          Port("b_out", "out", "data", n, "S", 1.0, node=f"B{n - 1}",
                               stub="XB_out", code=code.name)]
                         + _classical_ports(decision=True),
                   inventory={}, capacity=2 * n, params={"code": code.summary(), "pairs": n},
                   size=[max(xs) - min(xs), max(ys) - min(ys)])


# --------------------------------------------------------------------------- bridge


def bridge(*, name: str = "ls_d3", rounds: int = D) -> LeafBuild:
    """Lattice surgery: two blocks merged through a seam of 3 resident ions for `rounds`
    rounds, then split.  One device, two ops (Z̄Z̄ and X̄X̄)."""
    p, code = _surface()
    W = 30
    docks = [1 + 3 * i for i in range(20)]
    b = bay(name, W, docks, {"in_a": "TL", "out_a": "TR", "in_b": "BL", "out_b": "BR"},
            {"in_a": 9, "out_a": 9, "in_b": 9, "out_b": 9})
    A = [f"d{q}" for q in range(p.n)]
    B = [f"e{q}" for q in range(p.n)]
    seam = [f"s{i}" for i in range(D)]
    anc_ions = [f"a{i}" for i in range(len(docks))]
    home = {a: f"A{s}" for a, s in zip(anc_ions, docks)}
    seam_slots = None
    build = LeafBuild(master=None, machine=b.machine, home=dict(home),
                      roles={**{a: "ancilla" for a in anc_ions}, **{s: "ancilla" for s in seam},
                             **{x: "data" for x in A + B}})
    layouts = {}
    for kind in ("ZZ", "XX"):
        mp, where = merged_patch(D, kind)
        fixed = None
        if seam_slots is not None:
            fixed = {q: s for q, s in zip(where["seam"], seam_slots)}
        ds, ad, steps = place(mp, b.N, docks, fixed=fixed, seed=5, iterations=2400)
        if seam_slots is None:
            seam_slots = [ds[q] for q in where["seam"]]
            home.update({s: f"S{slot}" for s, slot in zip(seam, seam_slots)})
            build.home = dict(home)
        ion_of = {}
        for i, q in enumerate(where["A"]):
            ion_of[q] = A[i]
        for i, q in enumerate(where["B"]):
            ion_of[q] = B[i]
        for i, q in enumerate(where["seam"]):
            ion_of[q] = seam[i]
        anc = {c.name: anc_ions[docks.index(ad[c.name])] for c in mp.checks}
        layouts[kind] = (mp, where, ds, ad, steps)
        key = kind.lower()
        bp = BayProgram(b, f"{name}.{key}", {**home, **{x: "X_in_a" for x in A},
                                             **{x: "X_in_b" for x in B}})
        bp.absorb("in_a", A, [ds[q] for q in where["A"]], cool_every=3, op="enter")
        bp.cool_inside(op="enter")
        bp.absorb("in_b", B, [ds[q] for q in where["B"]], cool_every=3, op="enter")
        bp.restore(op="enter")
        seam_basis = where["seam_basis"]

        def seam_init(batch, seam_basis=seam_basis):
            bp.reset(batch, op="merge")
            if seam_basis == "X":
                bp.gate1("H", batch, op="merge")

        def seam_read(batch, seam_basis=seam_basis):
            if seam_basis == "X":
                bp.gate1("H", batch, op="split")
            bp.measure(batch, op="split", seam=True)

        bp.at_docks(seam, seam_init, op="merge")
        bp.restore(op="merge")
        for r in range(rounds):
            bp.conveyor_round(mp, ion_of, anc, ad, round_index=r, tag="merged")
            bp.restore(op="merged")
        bp.at_docks(seam, seam_read, op="split")
        bp.restore(op="split")
        bp.emit("out_a", A, cool_every=3, op="leave")
        bp.emit("out_b", B, cool_every=3, op="leave")
        bp.restore(op="leave")                  # the seam rides the ring: bring it home
        build.programs[key] = _cooled(bp.prog, b.machine)
        build.expect_end[key] = {**home, **{x: "X_out_a" for x in A},
                                 **{x: "X_out_b" for x in B}}
        build.params[key] = {"rounds": rounds, "conveyor_steps": steps,
                             "measured_checks": where["measured"]}
        m_letter = kind[0]
        other = "X" if m_letter == "Z" else "Z"
        build.titles[key] = f"measure {m_letter}̄_A {m_letter}̄_B by merging for {rounds} rounds"
        build.notes[key] = [
            f"the seam ({D} resident ions) starts in |{'+' if seam_basis == 'X' else '0'}⟩ and is "
            f"measured in {seam_basis} at the split; the merged {mp.rows}×{mp.cols} patch has "
            f"{len(mp.checks)} checks",
            f"the outcome is the product of the {len(where['measured'])} seam {m_letter} checks "
            f"of any merged round"]
        LA = {s: _L(code, A, s) for s in ("X", "Z")}
        LB = {s: _L(code, B, s) for s in ("X", "Z")}
        flows = (_stab_flows(p, A, measured=True, holds=True, prefix="A.")
                 + _stab_flows(p, B, measured=True, holds=True, prefix="B.")
                 + [Flow(f"{m_letter}̄_A kept", LA[m_letter], LA[m_letter], "frame", role="logical"),
                    Flow(f"{m_letter}̄_B kept", LB[m_letter], LB[m_letter], "frame", role="logical"),
                    Flow(f"{other}̄_A{other}̄_B kept", _mul(LA[other], LB[other]),
                         _mul(LA[other], LB[other]), "frame", role="logical"),
                    Flow(f"{m_letter}̄_A{m_letter}̄_B measured", _mul(LA[m_letter], LB[m_letter]),
                         {}, "measure", role="logical")])
        build.specs[key] = Spec(f"lattice surgery {kind}", A + B, A + B, flows)
        bA, bB = Block(code, A, "A"), Block(code, B, "B")
        build.experiments[key] = dict(
            blocks_in=[bA, bB], blocks_out=[bA, bB], expect="ft",
            flows=[LogicalFlow(f"{m_letter}̄_A", [("A", 0, m_letter)], [("A", 0, m_letter)]),
                   LogicalFlow(f"{m_letter}̄_B", [("B", 0, m_letter)], [("B", 0, m_letter)]),
                   LogicalFlow(f"{other}̄_A{other}̄_B", [("A", 0, other), ("B", 0, other)],
                               [("A", 0, other), ("B", 0, other)]),
                   LogicalFlow(f"{m_letter}̄_A{m_letter}̄_B",
                               [("A", 0, m_letter), ("B", 0, m_letter)], [])])
    tidy(b.machine, build.programs.values(), keep_classes=KEEP)
    build.extra = {"layouts": {k: {"data_slots": v[2], "anc_docks": v[3]} for k, v in layouts.items()}}
    return _master(build, name=name, category="surgery", title="Lattice surgery (d=3)",
                   doc="20 resident ancillas and a 3-ion seam on a 60-slot ring; two blocks "
                       "enter from the left, merge for d rounds, split, and leave right",
                   ports=[Port("in_a", "in", "data", 9, "W", 0.2, node=b.trap("in_a"),
                               stub=b.stub("in_a"), code=code.name),
                          Port("in_b", "in", "data", 9, "W", 0.8, node=b.trap("in_b"),
                               stub=b.stub("in_b"), code=code.name),
                          Port("out_a", "out", "data", 9, "E", 0.2, node=b.trap("out_a"),
                               stub=b.stub("out_a"), code=code.name),
                          Port("out_b", "out", "data", 9, "E", 0.8, node=b.trap("out_b"),
                               stub=b.stub("out_b"), code=code.name)]
                         + _classical_ports(syndrome=24 * rounds, outcome=1, decision=True),
                   inventory={"ancilla": 23}, capacity=23 + 18,
                   params={"code": code.summary(), "ring_slots": b.N, "rounds": rounds},
                   size=b.size())


# --------------------------------------------------------------------------- injection


def pharmacy(*, name: str = "inj_d3") -> LeafBuild:
    """Magic-state injection: a physical magic ion and 8 fresh ions in, a logical block out."""
    p, code = _surface()
    W = 12
    docks = [1 + 3 * i for i in range(8)]
    b = bay(name, W, docks, {"magic": "BL", "in": "TL", "out": "TR"},
            {"magic": 1, "in": 8, "out": 9})
    ds, ad, steps = place(p, b.N, docks, seed=3)
    data = ["m0"] + [f"d{q}" for q in range(1, p.n)]
    fresh = data[1:]
    anc = _anc_names(p)
    home = {anc[c]: f"A{ad[c]}" for c in anc}
    build = LeafBuild(master=None, machine=b.machine, home=home,
                      roles={**{a: "ancilla" for a in home}, "m0": "magic",
                             **{d: "fresh" for d in fresh}})
    # the magic ion is code qubit 0, the corner shared by Z̄ (row 0) and X̄ (column 0): the
    # rest of row 0 starts in |0>, the rest of column 0 in |+>, so Z̄ = Z_m and X̄ = X_m
    zsup = set(code.logical_support(0, "Z"))
    xsup = set(code.logical_support(0, "X"))
    plus = [data[q] for q in sorted(xsup - {0})]
    assert zsup & xsup == {0}
    for key in ("inject", "inject.Y"):
        # `inject` encodes a state the ion arrived with; `inject.Y` prepares |Y⟩ = S|+⟩ on
        # the corner ion here, so the block comes out in |Ȳ⟩ -- the resource an S̄ gadget
        # consumes (Litinski 2019 §4: a π/4 rotation is a Z̄Z̄ measurement against |Y⟩)
        own = key.endswith(".Y")
        bp = BayProgram(b, f"{name}.{key}", {**home, "m0": "X_magic",
                                             **{d: "X_in" for d in fresh}})
        bp.absorb("magic", ["m0"], [ds[0]], op="enter")
        bp.absorb("in", fresh, [ds[q] for q in range(1, p.n)], op="enter")
        bp.restore(op="enter")

        def init(batch, own=own):
            bp.reset(batch, op="init")
            hs = [i for i in batch if i in plus or (own and i == "m0")]
            if hs:
                bp.gate1("H", hs, op="init")
            if own and "m0" in batch:
                bp.gate1("S", ["m0"], op="init")       # |+⟩ -> |Y⟩

        bp.at_docks(data if own else fresh, init, op="init")
        bp.restore(op="init")
        bp.conveyor_round(p, dict(enumerate(data)), anc, ad, round_index=0)
        bp.restore(op="round")
        bp.emit("out", data, cool_every=5, op="leave")
        build.programs[key] = _cooled(bp.prog, b.machine)
        build.titles[key] = ("prepare |Ȳ⟩ by injecting S|+⟩ on the corner ion" if own else
                             "encode the magic ion's state into a fresh block")
        build.expect_end[key] = {**home, **{d: "X_out" for d in data}}
        build.notes[key] = [
            f"{'the corner ion is reset, H and S here' if own else 'the magic ion'} becomes "
            f"code qubit 0; {', '.join(plus)} start in |+⟩ and the rest of the fresh ions in "
            f"|0⟩ (qubit 0 is on both Z̄ = row 0 and X̄ = column 0)",
            "not fault-tolerant: one fault on the magic ion before the round changes the "
            "encoded state undetectably, which is why injected states are distilled"]
        if own:
            build.notes[key].append(
                "this is the resource of the logical S̄ gadget: measure Z̄Z̄ between the data "
                "block and this one by lattice surgery, read this one out in X, and the "
                "correction is Z̄ when the two outcomes disagree -- a Pauli frame, kept in "
                "the classical memory")
            LY = _mul(_L(code, data, "X"), _L(code, data, "Z"))
            build.specs[key] = Spec(
                "prepare |Ȳ⟩", [], data,
                [Flow("Ȳ = +1", {}, LY, "frame", role="logical")]
                + _stab_flows(p, data, measured=False, holds=True))
            build.experiments[key] = dict(
                blocks_out=[Block(code, data, "A")],
                expect="a state injection, fault-tolerant to distance 1 by design: the |Ȳ⟩ "
                       "it prepares carries an O(p) logical error, as every injection does",
                flows=[LogicalFlow("Ȳ", [], [("A", 0, "Y")])])
        else:
            build.specs[key] = Spec(
                "inject a physical state", ["m0"], data,
                [Flow("X(m) → X̄", {"m0": "X"}, _L(code, data, "X"), "frame", role="logical"),
                 Flow("Z(m) → Z̄", {"m0": "Z"}, _L(code, data, "Z"), "frame", role="logical")]
                + _stab_flows(p, data, measured=False, holds=True))
            build.experiments[key] = dict(
                qubits_in=["m0"], blocks_out=[Block(code, data, "A")],
                expect="injection is not fault-tolerant: its logical error rate is O(p), "
                       "removed by distillation downstream",
                flows=[LogicalFlow("X(m) → X̄", [("m0", None, "X")], [("A", 0, "X")]),
                       LogicalFlow("Z(m) → Z̄", [("m0", None, "Z")], [("A", 0, "Z")])])
    tidy(b.machine, build.programs.values(), keep_classes=KEEP)
    return _master(build, name=name, category="injection", title="Magic-state injection (d=3)",
                   doc="the magic ion and 8 fresh ions ride past 8 resident ancillas for one "
                       "round and leave as a code block",
                   ports=[Port("magic", "in", "any", 1, "W", 0.8, node=b.trap("magic"),
                               stub=b.stub("magic")),
                          Port("in", "in", "fresh", 8, "W", 0.2, node=b.trap("in"),
                               stub=b.stub("in")),
                          Port("out", "out", "data", 9, "E", 0.2, node=b.trap("out"),
                               stub=b.stub("out"), code=code.name)]
                         + _classical_ports(syndrome=8, decision=True),
                   inventory={"ancilla": 8}, capacity=17,
                   params={"code": code.summary(), "ring_slots": b.N, "docks": docks},
                   size=b.size())


# --------------------------------------------------------------------------- factory


def factory(*, name: str = "msf15") -> LeafBuild:
    """15-to-1 distillation: 14 resident ions and one visitor, who leaves holding the state."""
    from ..logic.faults import propagate
    from ..logic.statevec import qubit_state, run_postselected

    W = 10
    b = bay(name, W, [1], {"in": "BL", "out": "TR"}, {"in": 1, "out": 1})
    gates, out = rm15_circuit()
    names = [f"s{i}" if i != out else "m0" for i in range(15)]
    staff = [nm for nm in names if nm != "m0"]
    slots = [s for s in range(2, 17)]
    home = {nm: f"S{s}" for nm, s in zip(names, slots) if nm != "m0"}
    m0_home = slots[out]
    build = LeafBuild(master=None, machine=b.machine, home=dict(home),
                      roles={**{s: "magic" for s in staff}, "m0": "magic"})
    key = "produce"
    bp = BayProgram(b, f"{name}.{key}", {**home, "m0": "X_in"})
    bp.absorb("in", ["m0"], [m0_home], op="enter")
    bp.restore(op="enter")
    ring_circuit(bp, names, gates, slot="S1", dock="A1", op="produce")
    bp.restore(op="produce")
    bp.emit("out", ["m0"], op="leave")
    bp.restore(op="leave")                      # the residents ride the ring: bring them home
    build.programs[key] = _cooled(bp.prog, b.machine)
    build.titles[key] = "15-to-1 distillation; the visitor leaves holding the output state"
    build.expect_end[key] = {**home, "m0": "X_out"}
    n_cx = sum(1 for g in gates if g[0] == "CX")
    build.notes[key] = [
        f"{n_cx} CX, 15 T, 14 syndrome measurements at one dock trap, gate by gate",
        "checked: with ideal gates the 14 outcomes are all 0 and the visitor holds T†|+⟩; a "
        "Z error after any 1 or 2 of the 15 T gates always shows in the outcomes, and exactly "
        "35 of the 455 triples do not (output error 35p³)",
        "not checked: errors on the Clifford gates, which this protocol does not protect "
        "against (distillation assumes Cliffords much better than T)"]

    def check(circ):
        rep = {"kind": "statevector"}
        st, p_acc = run_postselected(circ)
        q = circ.index["m0"]
        amp = qubit_state(st, q)
        import cmath
        import math
        target = {"T|+⟩": (1 / math.sqrt(2), cmath.exp(1j * math.pi / 4) / math.sqrt(2)),
                  "T†|+⟩": (1 / math.sqrt(2), cmath.exp(-1j * math.pi / 4) / math.sqrt(2))}
        fid = {}
        if amp is not None:
            for label, (a0, a1) in target.items():
                fid[label] = abs(a0.conjugate() * amp[0] + a1.conjugate() * amp[1]) ** 2
        best = max(fid, key=fid.get) if fid else None
        rep.update(accept=round(p_acc, 12), output=best,
                   fidelity=round(fid.get(best, 0.0), 12) if best else 0.0)
        # Z faults after each T gate, pushed through the Clifford decoder
        tq = [(k, t) for k, op in enumerate(circ.ops) if op.name in ("T", "T_DAG") for t in op.targets]

        def custom(k, op):
            return [((t,), ("Z",)) for kk, t in tq if kk == k]

        prop = propagate(circ, custom=custom)
        syn = []
        for f in range(len(prop.faults)):
            s = 0
            for r, flips in enumerate(prop.flips):
                if (flips >> f) & 1:
                    s |= 1 << r
            bad = ((prop.final_x[q] >> f) & 1) | ((prop.final_z[q] >> f) & 1)
            syn.append((s, bad))
        n = len(syn)
        w1 = sum(1 for s, _ in syn if s)
        w2 = sum(1 for i in range(n) for j in range(i + 1, n) if syn[i][0] ^ syn[j][0])
        und3 = bad3 = 0
        for i in range(n):
            for j in range(i + 1, n):
                sij = syn[i][0] ^ syn[j][0]
                bij = syn[i][1] ^ syn[j][1]
                for k in range(j + 1, n):
                    if not (sij ^ syn[k][0]):
                        und3 += 1
                        bad3 += 1 if bij ^ syn[k][1] else 0
        rep.update(t_gates=n, detected_1=w1, singles=n, detected_2=w2, pairs=n * (n - 1) // 2,
                   undetected_3=und3, logical_3=bad3, triples=n * (n - 1) * (n - 2) // 6)
        rep["passed"] = bool(abs(p_acc - 1) < 1e-9 and best and fid[best] > 1 - 1e-9
                             and w1 == n and w2 == n * (n - 1) // 2 and bad3 == und3 == 35)
        rep["claim"] = (f"accepts with probability {rep['accept']:.6g} and outputs {best} "
                        f"exactly; every 1 or 2 T-gate Z errors detected; {und3} undetected "
                        f"triples, all logical: output error {bad3}p³")
        return rep

    build.logic_checks[key] = check
    tidy(b.machine, build.programs.values(), keep_classes=KEEP)
    return _master(build, name=name, category="factory", title="15-to-1 magic-state factory",
                   doc="14 resident ions on a 20-slot ring with one gate dock; a visiting ion "
                       "becomes the output of each run",
                   ports=[Port("in", "in", "fresh", 1, "W", 0.8, node=b.trap("in"),
                               stub=b.stub("in")),
                          Port("out", "out", "magic", 1, "E", 0.2, node=b.trap("out"),
                               stub=b.stub("out"))],
                   inventory={"magic": 14}, capacity=15,
                   params={"ring_slots": b.N, "protocol": "15-to-1 Reed-Muller (Bravyi-Kitaev 2005)"},
                   size=b.size())


PLACES = {"prep_d3": prep, "se_d3": clinic, "zone_d3": zone, "tcx_d3": workshop,
          "ls_d3": bridge, "read_d3": post_office, "inj_d3": pharmacy, "msf15": factory,
          "depot9": depot, "junc9": junction9}
