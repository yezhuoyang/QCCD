"""`catbench_d3`: lattice surgery across many districts -- a Pauli product measured by cat states.

Gidney and Ekera's Shor measures Pauli products over a whole register in one merge
(`merge X[a] * PROD i from 0 to n if cst/2^i%2 . X[data[base + i]]`: up to 2,049 blocks at
RSA-2048).  The Pauli-product bench (`ppm_d3`) takes four blocks.  The user, 2026-10-29,
chose how the machine does the rest: a CAT CHAIN -- each block's district has a bench whose
probe ions are joined into cat states (|0...0> + |1...1>) with the other benches'; each probe
takes a CX (X factor) or CZ (Z factor) with its own block's logical support and is read in X;
the product's value is the parity of the probes' readings (Shor's cat-state measurement).

The cats are made IN PARALLEL, so a merge over the whole register takes the time of one over
two blocks: every probe starts in |+> (a cat of one), and each pair of neighbouring benches
is fused by a parity check Z_p Z_p' -- a bundle of fresh links takes a copy of the left
probes' Z (`cat.lend`) and is read against the right ones (`cat.check`).  All checks run at
once.  A check that reads 1 leaves the cats fused up to an X on every probe to its right; the
couplings carry that X onto the blocks as a P̄, so the archive keeps it as a frame and no one
waits for it.

Three cats at once, like the bench's three rounds: three probes, each in a dock trap of its
own, and bundles of three links.  The three products agree unless a fault struck, so a single
fault shows -- the promise the bench's three rounds make.

The device is the Pauli-product bench's ring (60 slots, docks on the bottom row), with a block
door in and out and a three-ion link door in and out.  Five ops:

    cat.open          each probe in |+>
    cat.lend          three fresh links come in, each takes a copy of a probe's Z (a CX probe ->
                      link), and they leave for the next bench
    cat.check         the links come in, each takes this bench's probe's Z too and is read: the
                      parity that fuses the two cats; the links leave spent
    cat.couple.<P>    a block comes in; each probe takes a controlled-P̄ with its logical support
                      (P = X or Z: a CX or CZ with each support ion), is read in X, and the
                      block leaves

Every op is specified completely by stabilizer flows (`logic.spec`), and verified on the circuit
read back off its compiled TSIR, like every place.  Not fault-tolerant at circuit level (no cat
verification; a probe fault can spread to its block): the user does not ask for circuit distance.
"""
from __future__ import annotations

from ..logic.spec import Flow, Spec
from ..model import Master, Port
from ..surface import patch
from .bay import BayProgram
from .bench import D, KEEP, _commutes, _paulis, door_bay
from .common import LeafBuild, tidy

__all__ = ["catbench", "depot_x", "ROUNDS", "check_flow", "probe_flow", "EXTRA_PLACES"]

#: cats made at once (the bench's rounds)
ROUNDS = 3


def check_flow(r: int) -> str:
    """The measure flow whose record is round `r`'s parity bit at a `cat.check`."""
    return f"Z_l{r} Z_p{r} measured"


def probe_flow(r: int, letter: str) -> str:
    """The measure flow whose record is probe `r`'s bit at a `cat.couple.<letter>`."""
    return f"X_p{r} {letter}̄ measured"


def catbench(*, name: str = "catbench_d3") -> LeafBuild:
    p = patch(D, D, name=f"surface_d{D}")
    code = p.code
    W = 30
    doors = {"in_a": 0, "in_l": 4, "out_l": W - 5, "out_a": W - 1}
    docks = [W + W // 2 - 4, W + W // 2, W + W // 2 + 4]
    b = door_bay(name, W, docks, doors, {"in_a": 9, "out_a": 9, "in_l": ROUNDS, "out_l": ROUNDS})
    R = range(ROUNDS)
    probes, links = [f"p{r}" for r in R], [f"l{r}" for r in R]
    home = {probes[r]: f"A{docks[r]}" for r in R}
    blk = [f"d{q}" for q in range(p.n)]
    blk_home = [(W + 9 + q) % (2 * W) for q in range(p.n)]
    link_home = [(W + 2 + r) % (2 * W) for r in R]
    build = LeafBuild(master=None, machine=b.machine, home=dict(home),
                      roles={**{x: "ancilla" for x in probes}, **{x: "messenger" for x in links},
                             **{i: "data" for i in blk}})

    def links_in(bp):
        bp.absorb("in_l", links, link_home, op="enter")
        bp.restore(op="enter")

    def dock(bp, ion, r):
        bp.rotate(bp.delta_to(ion, f"S{docks[r]}"), op="cat", round=r)
        bp.move("dock", [(ion, f"A{docks[r]}")], op="cat", round=r)

    def links_out(bp):
        bp.emit("out_l", links, op="leave")
        bp.restore(op="leave")

    # ---- cat.open: each probe in |+>: a cat of one ---------------------------------------
    key = "cat.open"
    bp = BayProgram(b, f"{name}.{key}", dict(home))
    for r in R:
        bp.reset([probes[r]], op="cat", round=r)
        bp.gate1("H", [probes[r]], op="cat", round=r)
    _finish(build, b, key, bp, dict(home), f"each of the {ROUNDS} probes in |+>: a cat of one",
            Spec(f"open {ROUNDS} cats", [], probes,
                 [Flow(f"X_p{r} = +1", {}, {probes[r]: "X"}, "exact") for r in R]))

    # ---- cat.lend: fresh links take a copy of each probe's Z, and leave for the next bench ----
    key = "cat.lend"
    bp = BayProgram(b, f"{name}.{key}", {**home, **{x: "X_in_l" for x in links}})
    links_in(bp)
    for r in R:
        dock(bp, links[r], r)
        bp.reset([links[r]], op="cat", round=r)       # (in the dock trap: a ring slot cannot reset)
        bp.gate("CX", [(probes[r], links[r])], op="cat", round=r)
        bp.move("undock", [(links[r], f"S{docks[r]}")], op="cat", round=r)
    links_out(bp)
    flows = []
    for r in R:
        flows += [Flow(f"Z_p{r} Z_l{r} = +1", {}, {probes[r]: "Z", links[r]: "Z"}, "exact"),
                  Flow(f"Z_p{r} kept", {probes[r]: "Z"}, {probes[r]: "Z"}, "exact"),
                  Flow(f"X_p{r} -> X_p{r} X_l{r}", {probes[r]: "X"},
                       {probes[r]: "X", links[r]: "X"}, "exact")]
    _finish(build, b, key, bp, {**home, **{x: "X_out_l" for x in links}},
            f"{ROUNDS} fresh links each take a copy of a probe's Z, and leave for the next bench",
            Spec(f"lend {ROUNDS} links", probes, probes + links, flows))

    # ---- cat.check: the arriving links read Z_l Z_p -- the parity that fuses two cats ------
    key = "cat.check"
    bp = BayProgram(b, f"{name}.{key}", {**home, **{x: "X_in_l" for x in links}})
    links_in(bp)
    for r in R:
        dock(bp, links[r], r)
        bp.gate("CX", [(probes[r], links[r])], op="cat", round=r)
        bp.measure([links[r]], op="cat", round=r, checks=[f"z{r}"])
        bp.move("undock", [(links[r], f"S{docks[r]}")], op="cat", round=r)
    links_out(bp)
    flows = []
    for r in R:
        flows += [Flow(check_flow(r), {links[r]: "Z", probes[r]: "Z"}, {}, "measure"),
                  Flow(f"Z_p{r} kept", {probes[r]: "Z"}, {probes[r]: "Z"}, "exact"),
                  Flow(f"X_l{r} X_p{r} -> X_p{r}", {links[r]: "X", probes[r]: "X"},
                       {probes[r]: "X"}, "exact")]
    _finish(build, b, key, bp, {**home, **{x: "X_out_l" for x in links}},
            "the arriving links read the parity of their probes' Z and this bench's: two cats "
            "become one, up to a flip the archive keeps; the links leave spent",
            Spec(f"fuse {ROUNDS} pairs of cats", probes + links, probes, flows))

    # ---- cat.couple.<P>: each probe takes a controlled-P̄ with a block, and is read in X ------
    for letter in ("X", "Z"):
        key = f"cat.couple.{letter}"
        bp = BayProgram(b, f"{name}.{key}", {**home, **{i: "X_in_a" for i in blk}})
        bp.absorb("in_a", blk, blk_home, cool_every=3, op="enter")
        bp.cool_inside(op="enter")
        bp.restore(op="enter")
        todo = [(ion, r) for ion in _paulis(code, blk, letter) for r in R]
        while todo:
            # the (support ion, probe) pair the ring brings together soonest: the contacts
            # commute (each is controlled from its own probe)
            ion, r = min(todo, key=lambda c: (abs(bp.delta_to(c[0], f"S{docks[c[1]]}")), c[1]))
            bp.rotate(bp.delta_to(ion, f"S{docks[r]}"), op="couple", round=r)
            bp.move("dock", [(ion, f"A{docks[r]}")], op="couple", round=r)
            bp.gate("CX" if letter == "X" else "CZ", [(probes[r], ion)], op="couple", round=r,
                    factor=letter)
            bp.move("undock", [(ion, f"S{docks[r]}")], op="couple", round=r)
            todo.remove((ion, r))
        for r in R:
            bp.gate1("H", [probes[r]], op="couple", round=r)
            bp.measure([probes[r]], op="couple", round=r, checks=[f"b{r}"])
        bp.restore(op="couple")
        bp.emit("out_a", blk, cool_every=3, op="leave")
        bp.restore(op="leave")
        P = _paulis(code, blk, letter)
        flows = [Flow(probe_flow(r, letter), {probes[r]: "X", **P}, {}, "measure", role="logical")
                 for r in R]
        every = {x: "Z" for x in probes}
        for i in blk:
            for s in ("X", "Z"):
                Q = {i: s}
                if _commutes(Q, P):
                    flows.append(Flow(f"{s}({i}) kept", Q, dict(Q), "exact"))
                else:
                    flows.append(Flow(f"Z_p {s}({i}) -> {s}({i})", {**every, **Q}, dict(Q), "exact"))
        _finish(build, b, key, bp, {**home, **{i: "X_out_a" for i in blk}},
                f"the probes, each part of a cat, measure {letter}̄ of a block with it",
                Spec(f"couple {ROUNDS} cats to {letter}̄", probes + blk, blk, flows))

    tidy(b.machine, build.programs.values(), keep_classes=KEEP)
    from .places import _classical_ports
    ports = [Port("in_a", "in", "data", 9, "W", 0.15, node=b.trap("in_a"), stub=b.stub("in_a"), code=code.name),
             Port("out_a", "out", "data", 9, "E", 0.15, node=b.trap("out_a"), stub=b.stub("out_a"), code=code.name),
             Port("in_l", "in", "any", ROUNDS, "W", 0.4, node=b.trap("in_l"), stub=b.stub("in_l")),
             Port("out_l", "out", "any", ROUNDS, "E", 0.4, node=b.trap("out_l"), stub=b.stub("out_l"))]
    ports += _classical_ports(outcome=ROUNDS, decision=True)
    build.master = Master(
        name=name, kind="leaf", family="catbench", title="Cat bench (d=3)",
        doc=f"{ROUNDS} probe ions, each in a dock on a 60-slot ring; links passing through join "
            "them to cat states with the other benches' probes, and a block passing through is "
            "measured against them: lattice surgery over a whole register, one district at a time",
        size=b.size(), ports=ports, inventory={"ancilla": ROUNDS}, capacity=ROUNDS + ROUNDS + 9,
        params={"code": code.summary(), "ring_slots": b.N, "rounds": ROUNDS}, device=name)
    build.master_name = name
    return build


def _finish(build, b, key, bp, end, title, spec):
    from .places import _cooled
    build.programs[key] = _cooled(bp.prog, b.machine)
    build.expect_end[key] = dict(end)
    build.titles[key] = title
    build.specs[key] = spec
    build.notes[key] = [f"{ROUNDS} cats at once, like the bench's {ROUNDS} rounds: a single fault "
                        "makes them disagree",
                        "not fault-tolerant at circuit level (no cat verification; a probe fault "
                        "can spread to its block): the user does not ask for circuit distance"]


def depot_x(*, name: str = "depot9x") -> LeafBuild:
    """The ion depot (`depot9`) that also sends out and takes back a cat chain's links: three
    fresh ones out (`load.3`), and the three spent ones, read at the tail bench, in
    (`dump.3`) -- they leave the computation here."""
    from .common import TrackedProgram
    from .places import depot
    build = depot(name=name)
    m = build.machine
    rail = [f"R{i}" for i in range(9)]
    ions = [f"i{j}" for j in range(ROUNDS)]

    def shift(tp, step, **meta):
        moves = [(x, rail[rail.index(w) + step]) for x, w in tp.pos.items()
                 if w in rail and 0 <= rail.index(w) + step < len(rail)]
        tp.move("conveyor", moves, **meta)

    key = f"load.{ROUNDS}"
    tp = TrackedProgram(m, f"{name}.{key}", {ion: rail[j] for j, ion in enumerate(ions)})
    tp.cool(op="load")
    for j, ion in enumerate(ions):
        tp.move("port_out", [(ion, "X_out")], op="load")
        if j + 1 < ROUNDS:
            shift(tp, -1, op="close_up")
    build.programs[key] = tp.prog
    build.titles[key] = f"{ROUNDS} freshly loaded ions out"
    build.expect_end[key] = {ion: "X_out" for ion in ions}
    key = f"dump.{ROUNDS}"
    tp = TrackedProgram(m, f"{name}.{key}", {ion: "X_in" for ion in ions})
    for j, ion in enumerate(ions):
        if j:
            shift(tp, -1, op="make_room")
        tp.move("port_in", [(ion, rail[-1])], op="dump")
    build.programs[key] = tp.prog
    build.titles[key] = f"{ROUNDS} used ions in, out of the computation"
    build.expect_end[key] = {ion: rail[9 - ROUNDS + j] for j, ion in enumerate(ions)}
    for key in (f"load.{ROUNDS}", f"dump.{ROUNDS}"):
        build.notes[key] = list(build.notes["dump.9"])
    build.master.doc = (f"a loading rail: fresh ions out (1, {ROUNDS}, 8 or 9 at a time), used "
                        f"ions in (9, or {ROUNDS}: a cat chain's spent links)")
    return build


EXTRA_PLACES = {"catbench_d3": catbench, "depot9x": depot_x}
