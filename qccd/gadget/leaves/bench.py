"""The Pauli-product bench: measure any product of logical X̄ and Z̄ over up to four blocks.

A fault-tolerant program written in Pauli-product measurements (Litinski's model, and
FormalRV's `.qppm`) measures operators like Z̄_A X̄_B Z̄_C -- several blocks, mixed bases --
which a two-block Z̄Z̄ / X̄X̄ bridge cannot do.  This place does it the direct way: one
resident *probe* ion in a dock trap, prepared in |+⟩; the ring brings each support ion of each
factor past the dock, the probe takes a CX (an X̄ factor) or a CZ (a Z̄ factor) with it; the
probe is read in X.  Its outcome is the product's value.  Three rounds, so a program's
"rounds = d" reads the same here.

The probe touches only the ions of each block's logical operator (a row for Z̄, a column for
X̄: three per block at d = 3), so the circuit is a controlled-P̄ from the probe -- a projective
measurement of P̄ on the blocks.  It is NOT fault-tolerant at circuit level (one probe fault
spreads to several data ions); the user does not ask for circuit distance (docs/FULLSTACK_PLAN.md),
so no distance experiment is attached.  The specification is complete: every physical Pauli
that commutes with P̄ flows through untouched, every anticommuting one does so multiplied by
one fixed anticommuting reference, and P̄ is measured -- `18 K` independent flows on `9 K`
data ions -- plus the logical flows composition reads.

Doors: blocks enter on the top-left doors (`in_a` … `in_d`) and leave on the top-right
ones (`out_a` …), FIFO like every bay; ops are `ppm.<letters>`, one letter per block in door
order (`ppm.ZXZ`: Z̄ on the block at `in_a`, X̄ at `in_b`, Z̄ at `in_c`).
"""
from __future__ import annotations

from itertools import product

from ...api import Machine
from ...arch.builder import DeviceBuilder
from ..logic.spec import Flow, Spec
from ..model import Master, Port
from ..surface import patch
from .bay import Bay, BayProgram
from .common import (LeafBuild, add_port_stub, declare_port_classes, declare_port_zone, tidy)

__all__ = ["bench", "PATTERNS", "EXTRA_PLACES"]

D = 3
ROUNDS = 3
KEEP = ("rotate_cw", "rotate_ccw", "dock", "undock", "port_in", "port_out")
#: the most blocks one product names: a Toffoli's pi/8 rotation of Z Z Z measured against a T block is four
MAX_BLOCKS = 4
PATTERNS = tuple("".join(p) for k in range(1, MAX_BLOCKS + 1) for p in product("XZ", repeat=k))
TAGS = ("a", "b", "c", "d")
PREFIX = ("d", "e", "f", "g")


class DoorBay(Bay):
    """A bay whose doors may stand on any top-row slot, not only the corners."""

    def corner_slot(self, corner) -> int:
        if isinstance(corner, int):
            return corner
        return super().corner_slot(corner)


def door_bay(name: str, W: int, docks, doors: dict[str, int], widths: dict[str, int]) -> DoorBay:
    """`bay()` with doors at the given top-row slots (a spur up to a port trap and a stub)."""
    docks = sorted(int(s) for s in docks)
    d = DeviceBuilder("explicit", shape="bay", W=W)
    pos = {}
    for i in range(W):
        pos[i] = (float(i), 0.0)
        pos[W + i] = (float(W - 1 - i), 1.0)
    for s in range(2 * W):
        labels = ["top" if s < W else "bottom"]
        if s in docks:
            labels.append("dock")
        d.site(f"S{s}", *pos[s], zone="data", labels=labels)
    for s in range(2 * W):
        d.segment(f"E{s}", f"S{s}", f"S{(s + 1) % (2 * W)}", loop="L0", labels=["rail"])
    d.loop("L0", [f"S{s}" for s in range(2 * W)], closed=True, kind="ring",
           note="the rigid-rotation orbit")
    for s in docks:
        if s < W or s in doors.values():
            raise ValueError(f"{name}: a dock belongs on the bottom row, away from the doors")
        x, y = pos[s]
        d.site(f"A{s}", x, 2.0, zone="trap", labels=["spur_trap"])
        d.segment(f"V{s}", f"S{s}", f"A{s}", length=1.0, labels=["spur"])
    m = Machine.from_device(d.build(), name=name)
    declare_port_zone(m)
    declare_port_classes(m)
    for port, s in doors.items():
        if not 0 <= s < W:
            raise ValueError(f"{name}: door {port} must stand on the top row")
        x, y = pos[s]
        m.add_site(f"P_{port}", x, y - 1.0, zone="trap", labels=["port_trap"], to=[f"S{s}"],
                   segment_ids=[f"V_{port}"])
        add_port_stub(m, f"X_{port}", (x, y - 2.0), f"P_{port}", width=widths.get(port, 1),
                      segment=f"E_{port}")
    return DoorBay(name, W, docks, dict(doors), m, dict(widths))


def _paulis(code, ions: list[str], letter: str) -> dict:
    return {ions[q]: letter for q in code.logical_support(0, letter)}


def _commutes(a: dict, b: dict) -> bool:
    anti = 0
    for q, p in a.items():
        r = b.get(q)
        if r and r != p and "I" not in (p, r):
            anti ^= 1
    return anti == 0


def _mul(*ps: dict) -> dict:
    t = {"I": (0, 0), "X": (1, 0), "Z": (0, 1), "Y": (1, 1)}
    back = {v: k for k, v in t.items()}
    acc: dict[str, str] = {}
    for p in ps:
        for q, s in p.items():
            a, b = t[acc.get(q, "I")], t[s]
            r = back[(a[0] ^ b[0], a[1] ^ b[1])]
            if r == "I":
                acc.pop(q, None)
            else:
                acc[q] = r
    return acc


def bench(*, name: str = "ppm_d3", rounds: int = ROUNDS) -> LeafBuild:
    p = patch(D, D, name=f"surface_d{D}")
    code = p.code
    # doors four slots apart, so the junctions they hang from are spaced like a town street's
    W = 30
    doors = {**{f"in_{t}": 4 * k for k, t in enumerate(TAGS)},
             **{f"out_{t}": W - 1 - 4 * k for k, t in enumerate(TAGS)}}
    dock = W + W // 2                              # the probe's dock, bottom row middle
    b = door_bay(name, W, [dock], doors, {k: 9 for k in doors})
    probe = "p0"
    home = {probe: f"A{dock}"}
    blocks = [[f"{PREFIX[k]}{q}" for q in range(p.n)] for k in range(MAX_BLOCKS)]
    # each block on nine consecutive slots, the blocks spread round the ring
    homes = [[(W + 9 * k + q) % (2 * W) for q in range(p.n)] for k in range(MAX_BLOCKS)]
    build = LeafBuild(master=None, machine=b.machine, home=dict(home),
                      roles={probe: "ancilla", **{i: "data" for blk in blocks for i in blk}})
    for pat in PATTERNS:
        K = len(pat)
        key = f"ppm.{pat}"
        mine = blocks[:K]
        bp = BayProgram(b, f"{name}.{key}", {**home, **{i: f"X_in_{TAGS[k]}" for k in range(K)
                                                        for i in mine[k]}})
        for k in range(K):
            bp.absorb(f"in_{TAGS[k]}", mine[k], homes[k], cool_every=3, op="enter")
            bp.cool_inside(op="enter")
        bp.restore(op="enter")
        contacts0 = [(ion, pat[k]) for k in range(K) for ion in _paulis(code, mine[k], pat[k])]
        for r in range(rounds):
            bp.reset([probe], op="ppm", round=r)
            bp.gate1("H", [probe], op="ppm", round=r)
            todo = list(contacts0)
            while todo:
                # the support ion the ring brings to the dock soonest (the contacts commute:
                # every one is controlled from the same probe)
                ion, letter = min(todo, key=lambda c: abs(bp.delta_to(c[0], f"S{dock}")))
                bp.rotate(bp.delta_to(ion, f"S{dock}"), op="ppm", round=r)
                bp.move("dock", [(ion, f"A{dock}")], op="ppm", round=r)
                bp.gate("CX" if letter == "X" else "CZ", [(probe, ion)], op="ppm", round=r,
                        factor=letter)
                bp.move("undock", [(ion, f"S{dock}")], op="ppm", round=r)
                todo.remove((ion, letter))
            bp.gate1("H", [probe], op="ppm", round=r)
            bp.measure([probe], op="ppm", round=r, checks=[f"P{r}"])
            bp.restore(op="ppm")
        for k in range(K):
            bp.emit(f"out_{TAGS[k]}", mine[k], cool_every=3, op="leave")
        bp.restore(op="leave")
        from .places import _cooled
        build.programs[key] = _cooled(bp.prog, b.machine)
        build.expect_end[key] = {**home, **{i: f"X_out_{TAGS[k]}" for k in range(K) for i in mine[k]}}
        names = "".join(f"{pat[k]}̄_{'ABCD'[k]}" for k in range(K))
        build.titles[key] = f"measure {names} with a probe ion, {rounds} rounds"
        build.params[key] = {"rounds": rounds, "pattern": pat, "blocks": K, "contacts": len(contacts0)}
        build.notes[key] = [
            "a resident probe ion in |+⟩ takes a CX with each X̄-support ion and a CZ with each "
            "Z̄-support ion as the ring brings it past its dock, then is read in X: a controlled-"
            f"P̄ measurement of the product, repeated {rounds} times",
            "not fault-tolerant at circuit level (the user does not ask for circuit distance)"]
        # ---- the specification: complete, over the blocks' data ions
        data = [i for blk in mine for i in blk]
        P = {}
        for k in range(K):
            P.update(_paulis(code, mine[k], pat[k]))
        flows: list[Flow] = []
        ref = None
        for i in data:
            for s in ("X", "Z"):
                Q = {i: s}
                if _commutes(Q, P):
                    flows.append(Flow(f"{s}({i}) kept", Q, dict(Q), "exact"))
                elif ref is None:
                    ref = Q
                else:
                    R = _mul(ref, Q)
                    flows.append(Flow(f"{s}({i})·{next(iter(ref.values()))}({next(iter(ref))}) kept",
                                      R, dict(R), "exact"))
        flows.append(Flow(f"{names} measured", dict(P), {}, "measure", role="logical"))
        for k in range(K):
            L = _paulis(code, mine[k], pat[k])
            flows.append(Flow(f"{pat[k]}̄_{'ABCD'[k]} kept", L, dict(L), "exact", role="logical"))
        others = [_paulis(code, mine[k], "Z" if pat[k] == "X" else "X") for k in range(K)]
        for k in range(K - 1):
            O = _mul(others[k], others[k + 1])
            flows.append(Flow(f"{'Z' if pat[k] == 'X' else 'X'}̄_{'ABCD'[k]}"
                              f"{'Z' if pat[k + 1] == 'X' else 'X'}̄_{'ABCD'[k + 1]} kept",
                              O, dict(O), "exact", role="logical"))
        build.specs[key] = Spec(f"measure {names}", data, data, flows)
    tidy(b.machine, build.programs.values(), keep_classes=KEEP)
    build.extra = {"homes": homes, "dock": dock, "doors": doors}
    ports = []
    for k, tag in enumerate(TAGS):
        ports.append(Port(f"in_{tag}", "in", "data", 9, "W", 0.15 + 0.23 * k, node=b.trap(f"in_{tag}"),
                          stub=b.stub(f"in_{tag}"), code=code.name))
    for k, tag in enumerate(TAGS):
        ports.append(Port(f"out_{tag}", "out", "data", 9, "E", 0.15 + 0.23 * k, node=b.trap(f"out_{tag}"),
                          stub=b.stub(f"out_{tag}"), code=code.name))
    from .places import _classical_ports
    ports += _classical_ports(syndrome=rounds, outcome=1, decision=True)
    build.master = Master(
        name=name, kind="leaf", family="ppm", title="Pauli-product bench (d=3)",
        doc="one probe ion in a dock on a 60-slot ring; up to four blocks enter on the left, "
            "the probe measures the product of one logical X̄ or Z̄ from each, and they leave right",
        size=b.size(), ports=ports, inventory={"ancilla": 1}, capacity=1 + 9 * MAX_BLOCKS,
        params={"code": code.summary(), "ring_slots": b.N, "rounds": rounds,
                "patterns": list(PATTERNS)},
        device=name)
    build.master_name = name
    return build


#: places outside `leaves/places.py` (so adding one does not re-key every cached place)
EXTRA_PLACES = {"ppm_d3": bench}
