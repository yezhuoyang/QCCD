"""`hbay_d3`: a logical Hadamard H̄ on a d = 3 surface-code block, done transversally.

Codesign, 2026-10-29 (the user: "you don't necessarily need to follow how we design gadget ...
Always optimize the bottleneck first").  Gidney 2025's critical path is its adder's carry
chain, and its steps measure mixed products X̄_a Z̄_b by lattice surgery, 25 rounds a merge.  With
a logical Hadamard, X̄_a Z̄_b = H̄_a (Z̄_a Z̄_b) H̄_a: a one-letter product, which transversal CNOTs
measure with no merge at all.

On this code transversal H is a logical H̄ up to a relabelling of the ions.  H on every data ion
turns the X checks into Z checks and back, and the patch turned a quarter turn -- input position
q leaving at position ROTATE[q] -- is the original code again, with X̄ and Z̄ exchanged (up to
stabilizers).  On a QCCD machine the relabelling costs nothing but ring turns: the bay sends the
ions out in the turned order.

One op, `h`: the block comes in, every ion takes an H at a dock (a batch at a time), and the
block leaves in the turned order.  Specified completely by its 18 single-ion flows; its
experiment checks the logical flows X̄ -> Z̄ and Z̄ -> X̄ on the block as it leaves.  Transversal,
so a fault stays on its own ion.
"""
from __future__ import annotations

from ..logic.experiment import Block, LogicalFlow
from ..logic.spec import Flow, Spec
from ..model import Port
from ..surface import patch
from .bay import BayProgram, bay, place
from .common import LeafBuild, tidy

__all__ = ["hbay", "ROTATE", "EXTRA_PLACES"]

#: the quarter turn: the ion at input position q leaves at position ROTATE[q]
ROTATE = (2, 5, 8, 1, 4, 7, 0, 3, 6)


def hbay(*, name: str = "hbay_d3", data_slots: dict | None = None) -> LeafBuild:
    """`data_slots` -- the ring slot each data ion rests at -- instead of `place()`'s (which
    are chosen for a syndrome round this bay never runs): `hbay_d3f` (leaves/doors.py)."""
    from .places import KEEP, _classical_ports, _cooled, _master
    p = patch(3, 3, name="surface_d3")
    code = p.code
    W = 12
    docks = [1 + 3 * i for i in range(8)]
    b = bay(name, W, docks, {"in": "TL", "out": "TR"}, {"in": 9, "out": 9})
    ds = dict(data_slots) if data_slots is not None else place(p, b.N, docks)[0]
    data = [f"d{q}" for q in range(p.n)]
    out = [None] * p.n
    for q in range(p.n):
        out[ROTATE[q]] = data[q]
    build = LeafBuild(master=None, machine=b.machine, home={}, roles={d: "data" for d in data})
    bp = BayProgram(b, f"{name}.h", {d: "X_in" for d in data})
    bp.absorb("in", data, [ds[q] for q in range(p.n)], cool_every=3, op="enter")
    bp.restore(op="enter")
    bp.at_docks(data, lambda batch: bp.gate1("H", batch, op="h"), op="h")
    bp.restore(op="h")
    bp.emit("out", out, cool_every=5, op="leave")
    build.programs["h"] = _cooled(bp.prog, b.machine)
    build.titles["h"] = "a logical Hadamard: H on every ion, the block sent out a quarter turn round"
    build.expect_end["h"] = {d: "X_out" for d in data}
    build.notes["h"] = ["transversal: each ion takes its own H, so a fault stays on its ion",
                        "the quarter turn is the order the ions leave in (ROTATE): no gate"]
    build.specs["h"] = Spec("transversal H", data, data,
                            [f for d in data for f in (Flow(f"X({d}) -> Z({d})", {d: "X"}, {d: "Z"}, "exact"),
                                                       Flow(f"Z({d}) -> X({d})", {d: "Z"}, {d: "X"}, "exact"))])
    build.experiments["h"] = dict(
        blocks_in=[Block(code, data, "A")], blocks_out=[Block(code, out, "A")], expect="ft",
        flows=[LogicalFlow("X̄ -> Z̄", [("A", 0, "X")], [("A", 0, "Z")]),
               LogicalFlow("Z̄ -> X̄", [("A", 0, "Z")], [("A", 0, "X")])])
    tidy(b.machine, build.programs.values(), keep_classes=KEEP)
    return _master(build, name=name, category="hadamard", title="Hadamard bay (d=3)",
                   doc="a 24-slot ring with 8 empty docks: a visiting block takes H on every ion "
                       "and leaves a quarter turn round -- a logical Hadamard",
                   ports=[Port("in", "in", "data", 9, "W", 0.3, node=b.trap("in"), stub=b.stub("in"),
                               code=code.name),
                          Port("out", "out", "data", 9, "E", 0.3, node=b.trap("out"), stub=b.stub("out"),
                               code=code.name)] + _classical_ports(decision=True),
                   inventory={}, capacity=9, params={"code": code.summary(), "ring_slots": b.N,
                                                     "docks": docks, "rotate": list(ROTATE)},
                   size=b.size())


EXTRA_PLACES = {"hbay_d3": hbay}
