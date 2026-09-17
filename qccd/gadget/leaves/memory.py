"""`mem_<code>`: one code block on a rotating ring, with two ion ports.  GADGETS.md §5.1.

The device is the repository's own ring: `ring(n/2, 2, V, dock_offset=1)`, whose `n` rail
slots hold the data ions and whose `V` dock traps hold the ancillas.  `dock_offset=1`
keeps every dock off the four corners, which frees two corners for ports: a spur from the
top-left slot `S0` to the port trap `P_bus` (the data bundle leaves and returns here), and
one from the bottom-right slot `S{n/2}` to `P_net` (a messenger enters, takes a parity,
and leaves).  Both spurs run outward, so the device stays planar (R21) with right angles
at the corner (R20).

Ops, each one TSIR from the home placement back to it (the placement contract, §2):

    cycle    one syndrome-extraction round: `qccd.compile.compile_code`, then the rotation
             that undoes the round's net rigid shift, then a cool
    layer1q  one single-qubit gate on every data ion, dock by dock
    emit     every data ion out through `bus`, in ring order (S0's ion first)
    absorb   the reverse: the bundle comes back in reverse ring order and lands home
    visit    a messenger from `net` takes the parity of one logical operator's support

Ion names are the compiler's: data `d1 … dn` (qubit q is `d{q+1}`), ancillas `a{slot}`, and
the messenger `m0`.
"""

from __future__ import annotations

from dataclasses import replace

from ...api import Machine
from ...codes.bb import BBCode, Check
from ...compile.cooling import CoolingPolicy, insert_cooling
from ...compile.pipeline import CompilePolicy, compile_code
from ...cost.models import corrected_model
from ...ir.tsir import TSIR, Instruction, loop_shift
from ...verify.replay import replay
from .. import gf2
from ..codes import CSSCode
from ..model import Master, Port
from .common import (LeafBuild, TrackedProgram, add_port_stub, declare_port_classes,
                     declare_port_zone, tidy)

__all__ = ["memory", "add_visit", "RingProgram"]


class RingProgram(TrackedProgram):
    """`TrackedProgram` on a ring: adds the rigid rotation and slot arithmetic."""

    def __init__(self, machine: Machine, name: str, placement: dict[str, str]):
        super().__init__(machine, name, placement)
        self.loop = list(self.dev.loops["L0"].nodes)
        self.index = {n: i for i, n in enumerate(self.loop)}

    def ion_at(self, node: str) -> str | None:
        here = self.at(node)
        return here[0] if here else None

    def slot(self, ion: str) -> int:
        return self.index[self.pos[ion]]

    def delta_to(self, ion: str, node: str) -> int:
        """The shortest signed rotation that brings `ion` (on the loop) to `node`."""
        k = len(self.loop)
        d = (self.index[node] - self.slot(ion)) % k
        return d if d <= k // 2 else d - k

    def rotate(self, delta: int, **meta) -> None:
        if not delta:
            return
        k = len(self.loop)
        self.prog.add(Instruction(
            type="simd", id=self.prog.next_id(),
            cls="rotate_cw" if delta > 0 else "rotate_ccw", mode="inter",
            template=loop_shift("L0", int(delta)),
            meta=dict(meta, kind="rotate", hops=abs(int(delta)))))
        for ion, where in list(self.pos.items()):
            if where in self.index:
                self.pos[ion] = self.loop[(self.index[where] + delta) % k]


def _bbcode_for(code: CSSCode) -> BBCode:
    """The compiler's `BBCode`, with checks taken from LogicQ's matrices.

    `compile_code` reads `checks`, `n`, `l`, `m` and `name` only.  Building the checks
    from the parsed `hx`/`hz` rows (rather than from `(var, power)` terms) is what admits
    every polynomial LogicQ admits, mixed monomials such as `x^2*y` included; the X/Z row
    order and the left/right qubit halves are the same in both.
    """
    bb = BBCode(l=code.params["l"], m=code.params["m"], a_terms=(), b_terms=(),
                name=code.name)
    bb.checks = ([Check(f"X_{v}", "X", v, tuple(gf2.support(r)))
                  for v, r in enumerate(code.hx)]
                 + [Check(f"Z_{v}", "Z", v, tuple(gf2.support(r)))
                    for v, r in enumerate(code.hz)])
    return bb


def _cooled(prog: TSIR, machine: Machine) -> TSIR:
    """R7-legal: the repository's cooling pass, never a hand-placed cool."""
    return insert_cooling(prog, machine.arch, corrected_model("qccdsim_jones"),
                          policy=CoolingPolicy()).program


def add_visit(build: LeafBuild, logical: int, basis: str) -> str:
    """Compile `visit.<basis><logical>` into an existing memory build; returns the op key.

    The messenger arrives already prepared (a reservoir's `release.Z` / `release.X`) and
    leaves unmeasured, so one messenger can take a parity across several blocks before a
    reservoir's `accept` reads it.  Z parity is CX(data -> messenger) per support ion; X
    parity is CX(messenger -> data).  Both families of gates commute among themselves, so
    the visiting order is free, and the ring is taken the shortest way to each ion.
    """
    if basis not in ("X", "Z"):
        raise ValueError(f"a messenger visit collects an X or a Z parity, not {basis!r}")
    key = f"visit.{basis}{logical}"
    if key in build.programs:
        return key
    m, home, code = build.machine, build.home, build.extra["code"]
    net_slot, order = build.extra["net_slot"], build.extra["order"]
    support = [f"d{q + 1}" for q in code.logical_support(logical, basis)]
    rp = RingProgram(m, f"{build.master_name}.{key}", {**home, "m0": "X_net"})
    rp.move("port_in", [("m0", "P_net")], op="visit")
    pending = list(support)
    while pending:
        ion = min(pending, key=lambda i: abs(rp.delta_to(i, net_slot)))
        pending.remove(ion)
        rp.rotate(rp.delta_to(ion, net_slot), op="visit")
        rp.move("dock", [(ion, "P_net")], op="visit")
        rp.gate("CX", [(ion, "m0") if basis == "Z" else ("m0", ion)], op="visit",
                logical=logical, basis=basis)
        rp.move("undock", [(ion, net_slot)], op="visit")
    rp.rotate(rp.delta_to(order[0], "S0"), restore=True)
    rp.cool(restore=True)                     # while the messenger is still in P_net
    rp.move("port_out", [("m0", "X_net")], op="visit")
    build.programs[key] = _cooled(rp.prog, m)
    build.titles[key] = f"collect the {basis} parity of logical {logical} ({len(support)} ions)"
    build.notes[key] = ["one bare ancilla collects the parity once: not fault-tolerant "
                        "against hook errors (an obligation, GADGETS.md §6.2)"]
    build.params[key] = {"logical": logical, "basis": basis, "weight": len(support)}
    build.expect_end[key] = {**home, "m0": "X_net"}
    build.roles["m0"] = "messenger"
    return key


def memory(code: CSSCode, *, name: str | None = None, verticals: int | None = None,
           policy: CompilePolicy | None = None, visits: list[tuple[int, str]] | None = None
           ) -> LeafBuild:
    """Build `mem_<code>`: device, ports, and the five op programs (not yet characterized).

    `visits` lists the `(logical index, "X"|"Z")` operators to compile a `visit` for; the
    default is logical 0 in both bases, which characterizes the op.  Synthesis asks for
    the one it needs by name (`visit.Z3`).
    """
    if code.family != "bivariate_bicycle":
        raise ValueError(f"mem_<code> is built for bivariate-bicycle codes, not {code.family}")
    n = code.n
    half = n // 2
    V = verticals or next(v for v in (24, 12, 18, 36, 6, 3) if n % v == 0 and n // v >= 3)
    name = name or f"mem_{code.name}"

    m = Machine.ring(half, 2, V, name=name, dock_offset=1)
    declare_port_zone(m)
    declare_port_classes(m)
    net_slot = f"S{half}"
    m.add_site("P_bus", 0.0, -1.0, zone="trap", labels=["port_trap"], to=["S0"],
               segment_ids=["V_bus"])
    add_port_stub(m, "X_bus", (0.0, -2.0), "P_bus", width=n, segment="E_bus")
    m.add_site("P_net", float(half - 1), 2.0, zone="trap", labels=["port_trap"],
               to=[net_slot], segment_ids=["V_net"])
    add_port_stub(m, "X_net", (float(half - 1), 3.0), "P_net", width=1, segment="E_net")

    compiled = compile_code(m.arch, _bbcode_for(code),
                            policy=policy or CompilePolicy(anneal_iterations=20000,
                                                           refine_steps=100))
    home = dict(compiled.program.instructions[0].placement)
    data = sorted((i for i in home if i.startswith("d")), key=lambda s: int(s[1:]))
    ancillas = sorted((i for i in home if i.startswith("a")), key=lambda s: int(s[1:]))
    roles = {**{d: "data" for d in data}, **{a: "ancilla" for a in ancillas}}

    build = LeafBuild(master=None, machine=m, home=home, roles=roles)
    build.master_name = name
    progs, titles, notes, params, ends = (build.programs, build.titles, build.notes,
                                          build.params, build.expect_end)

    # -- cycle ---------------------------------------------------------------------
    rp = RingProgram(m, f"{name}.cycle", home)
    for ins in compiled.program.instructions[1:]:
        rp.prog.add(replace(ins, id=rp.prog.next_id(), t0=None, t1=None, cost=None,
                            steps=None, quanta_delta=None, operating_point=None))
    after = replay(rp.prog, m.arch, corrected_model("qccdsim_jones"), check_rules=False)
    shifts = {(rp.index[after.final_positions[d]] - rp.index[home[d]]) % n for d in data}
    if len(shifts) != 1 or any(after.final_positions[a] != home[a] for a in ancillas):
        raise RuntimeError(f"{name}: the compiled round does not end in a rigid rotation of "
                           f"home ({sorted(shifts)[:4]}); the placement contract needs one")
    back = (n - shifts.pop()) % n
    rp.pos = dict(after.final_positions)
    rp.rotate(back if back <= half else back - n, restore=True)
    rp.cool(restore=True)
    progs["cycle"] = rp.prog
    titles["cycle"] = "one syndrome-extraction round"
    notes["cycle"] = [
        "contact schedule from qccd.compile: every check contact is a CX between the data "
        "ion and the check's ancilla; the extraction circuit's H layers and CX orientation "
        "are not emitted (R10 cannot be decided on this program)",
        f"then {abs(back if back <= half else back - n)} restoring rotation hops and one "
        f"cool, so the round ends where it began",
    ]
    ends["cycle"] = dict(home)

    # -- layer1q ---------------------------------------------------------------------
    rp = RingProgram(m, f"{name}.layer1q", home)
    dock_slots = sorted(int(s.id[1:]) for s in m.arch.device.nodes.values()
                        if s.kind == "site" and "dock" in s.labels)
    spacing = n // V
    for p in range(spacing):
        batch = [(rp.ion_at(f"S{s}"), f"A{s}") for s in dock_slots if rp.ion_at(f"S{s}") in roles
                 and roles[rp.ion_at(f"S{s}")] == "data"]
        if batch:
            rp.move("dock", batch, op="layer1q", pass_=p)
            rp.gate1("G", [ion for ion, _ in batch], op="layer1q", pass_=p)
            rp.move("undock", [(ion, f"S{int(dock[1:])}") for ion, dock in batch],
                    op="layer1q", pass_=p)
        if p + 1 < spacing:
            rp.rotate(-1, op="layer1q")
    rp.rotate(spacing - 1, restore=True)
    rp.cool(restore=True)
    progs["layer1q"] = _cooled(rp.prog, m)
    titles["layer1q"] = "a single-qubit gate on every data ion"
    notes["layer1q"] = ["the gate is written `G`: which gate, and whether the layer is a "
                        "logical operation on this code, is the instruction's to say "
                        "(LogicQ's checkTransversal)"]
    ends["layer1q"] = dict(home)

    # -- emit / absorb ------------------------------------------------------------------
    rp = RingProgram(m, f"{name}.emit", home)
    order = [rp.ion_at(f"S{s}") for s in range(n)]
    for j, ion in enumerate(order):
        if j:
            rp.rotate(-1, op="emit")
        rp.move("dock", [(ion, "P_bus")], op="emit")
        rp.move("port_out", [(ion, "X_bus")], op="emit")
    # no restoring rotation: the rail is empty, and `absorb` lands every ion on its own
    # home slot whatever phase the empty ring was left at.  No cool either: the bundle is
    # on the stub, which a cooling beam of THIS gadget does not reach (R6), and it leaves
    # as warm as the rotations made it -- the receiver's problem, stated in the transfer
    progs["emit"] = rp.prog
    titles["emit"] = "the data bundle out through bus, in ring order"
    notes["emit"] = ["the bundle leaves warm (every ion rotated past the port corner and the "
                     "dock junctions); the transfer's n̄ says how warm"]
    ends["emit"] = {**{a: home[a] for a in ancillas}, **{d: "X_bus" for d in data}}

    start = {**{a: home[a] for a in ancillas}, **{d: "X_bus" for d in data}}
    rp = RingProgram(m, f"{name}.absorb", start)
    for j, ion in enumerate(reversed(order)):
        if j:
            rp.rotate(+1, op="absorb")
        rp.move("port_in", [(ion, "P_bus")], op="absorb")
        rp.move("undock", [(ion, "S0")], op="absorb")
    rp.cool(restore=True)
    progs["absorb"] = rp.prog
    titles["absorb"] = "the data bundle back through bus, in reverse ring order"
    ends["absorb"] = dict(home)
    params["emit"] = params["absorb"] = {"order": order}

    build.extra = {"order": order, "code": code, "net_slot": net_slot, "data": data}
    for logical, basis in (visits or [(0, "Z"), (0, "X")]):
        add_visit(build, logical, basis)

    # a visit compiled later rotates both ways, docks and crosses the net port
    tidy(m, progs.values(), keep_classes=("rotate_cw", "rotate_ccw", "dock", "undock",
                                          "port_in", "port_out"))

    # -- the master's abstract ---------------------------------------------------------------
    xs = [p[0] for p in (nd.pos for nd in m.arch.device.nodes.values())]
    ys = [p[1] for p in (nd.pos for nd in m.arch.device.nodes.values())]
    build.master = Master(
        name=name, kind="leaf", family="memory",
        title=f"{code.name} memory [[{n},{code.k}]]",
        doc=f"{n} data ions on a {n}-slot ring, {V} ancillas in dock traps; the data bundle "
            f"crosses `bus`, messengers cross `net`",
        size=[max(xs) - min(xs), max(ys) - min(ys)],
        ports=[
            Port("bus", "inout", "data", width=n, side="N", at=0.0, order="ring",
                 node="P_bus", stub="X_bus", code=code.name),
            Port("net", "inout", "messenger", width=1, side="S", at=1.0,
                 node="P_net", stub="X_net"),
        ],
        inventory={"data": n, "ancilla": len(ancillas)},
        capacity=n + len(ancillas) + 2,
        params={"code": code.summary(), "ring": [half, 2, V], "dock_offset": 1,
                "compile": {"batches": compiled.batches, "hops": compiled.hops,
                            "contacts": compiled.contacts}},
        device=name,
    )
    return build
