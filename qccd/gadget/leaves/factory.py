"""`fac_t15`: a magic-state factory.  GADGETS.md §5.1, §6.2.

The device is a small ring -- `ring(10, 2, 1, dock_offset=1)`, 20 slots, one dock trap `A1`
-- holding 15 ions, with a port trap `P_out` on the corner slot `S0` and a stub beyond it.

    produce   15-to-1 distillation, leaving the distilled state on ion f0 in P_out
    consume   a messenger comes in through the port, takes Z_T into its parity with one CX,
              leaves; then f0 is measured in X, reset, and returns to the ring

**The circuit.**  The [[15,1,3]] quantum Reed-Muller code's |+̄⟩ is the uniform
superposition over the classical [15,5] code spanned by the all-ones word and the four
bit rows (column j holds the binary digits of j).  Reduced to row echelon form that code
has pivots {0, 1, 2, 3, 7}, so: prepare the pivots in |+⟩ and the rest in |0⟩, fan out 32
CNOTs (the encoder); apply T to all 15 ions (transversal T is logical T†); apply the same
32 CNOTs again (they commute, so the encoder is its own inverse); fold the phase onto f0
with CX(f1→f0), CX(f2→f0) -- in this basis the T† phase sits on the parity a0⊕a1⊕a2 of the
first three pivots -- then measure the other four pivots in X and the ten non-pivots in Z.
All fourteen outcomes trivial is the postselection; f0 then holds T†|+⟩.

**What is verified and what is not.**  The hardware program is replayed and every rule
checked, as for any leaf.  That the circuit distills -- the output error 35p³ at input
error p, the acceptance probability -- is the textbook protocol's claim (Bravyi-Kitaev
2005), recorded on the op as a note; nothing here simulates the noisy states.

The gate sequencing is a plain **ring circuit compiler** (`ring_circuit`): every gate
happens in the dock trap; a one-qubit gate docks its ion and returns it to the slot it
left; a CX docks the control, rotates the target under the same dock, docks it, gates,
returns the target, and rotates back so the control's own (empty) slot is under the dock
again.  Every gate therefore leaves the ring a rigid rotation of what it was, which is what
lets an op end in its home placement with one rotation.  It is slow in the obvious way (one
gate site); it is also short, and its output is verified like everything else.
"""

from __future__ import annotations

from ...api import Machine
from ...compile.cooling import CoolingPolicy, insert_cooling
from ...cost.models import corrected_model
from .. import gf2
from ..model import Master, Port
from .common import LeafBuild, add_port_stub, declare_port_classes, declare_port_zone, tidy
from .memory import RingProgram

__all__ = ["factory_t15", "ring_circuit", "rm15_circuit"]


def rm15_circuit() -> tuple[list[tuple], int]:
    """The 15-to-1 gate list on qubits 0..14, and the index of the output qubit."""
    cols = range(1, 16)
    rows = [sum(1 << c for c in range(15))] + \
        [sum(1 << c for c, j in enumerate(cols) if (j >> b) & 1) for b in range(4)]
    basis = gf2.row_reduce(rows)
    pivots = [gf2.first_one(r) for r in basis]
    fan = [(p, t) for r, p in zip(basis, pivots) for t in gf2.support(r) if t != p]
    odd = [p for r, p in zip(basis, pivots) if gf2.weight(r) % 2]
    out = odd[0]
    gates: list[tuple] = [("reset", q) for q in range(15)]
    gates += [("H", p) for p in pivots]
    gates += [("CX", c, t) for c, t in fan]
    gates += [("T", q) for q in range(15)]
    gates += [("CX", c, t) for c, t in fan]
    gates += [("CX", o, out) for o in odd[1:]]
    syndrome_x = [p for p in pivots if p != out]
    gates += [("H", p) for p in syndrome_x]
    gates += [("measure", q) for q in range(15) if q != out]
    return gates, out


def ring_circuit(rp: RingProgram, names: list[str], gates: list[tuple], *, slot: str,
                 dock: str, **meta) -> None:
    """Emit `gates` (on qubit indices into `names`) at one dock trap of a ring."""

    def bring(ion):
        rp.rotate(rp.delta_to(ion, slot), **meta)

    i = 0
    while i < len(gates):
        g = gates[i]
        if g[0] != "CX":
            q = names[g[1]]
            run = [g]
            while i + 1 < len(gates) and gates[i + 1][0] != "CX" and gates[i + 1][1] == g[1]:
                i += 1
                run.append(gates[i])
            bring(q)
            rp.move("dock", [(q, dock)], **meta)
            for op in run:
                if op[0] == "reset":
                    rp.reset([q], **meta)
                elif op[0] == "measure":
                    rp.measure([q], **meta)
                else:
                    rp.gate1(op[0], [q], **meta)
            rp.move("undock", [(q, slot)], **meta)
        else:
            c, t = names[g[1]], names[g[2]]
            bring(c)
            rp.move("dock", [(c, dock)], **meta)
            delta = rp.delta_to(t, slot)
            rp.rotate(delta, **meta)
            rp.move("dock", [(t, dock)], **meta)
            rp.gate("CX", [(c, t)], **meta)
            rp.move("undock", [(t, slot)], **meta)
            # undo the target's rotation: the control's own slot comes back under the dock,
            # so every gate leaves the ring a rigid rotation of what it was
            rp.rotate(-delta, **meta)
            rp.move("undock", [(c, slot)], **meta)
        i += 1


def factory_t15(*, name: str = "fac_t15") -> LeafBuild:
    m = Machine.ring(10, 2, 1, name=name, dock_offset=1)
    declare_port_zone(m)
    declare_port_classes(m)
    m.add_site("P_out", 0.0, -1.0, zone="trap", labels=["port_trap"], to=["S0"],
               segment_ids=["V_out"])
    add_port_stub(m, "X_out", (0.0, -2.0), "P_out", width=1, segment="E_out")

    names = [f"f{i}" for i in range(15)]
    home = {f: f"S{i + 2}" for i, f in enumerate(names)}          # S0, S1 and S17-S19 empty
    b = LeafBuild(master=None, machine=m, home=home, roles={f: "magic" for f in names})
    model = corrected_model("qccdsim_jones")

    gates, out = rm15_circuit()
    rp = RingProgram(m, f"{name}.produce", home)
    ring_circuit(rp, names, gates, slot="S1", dock="A1", op="produce")
    q = names[out]
    rp.rotate(rp.delta_to(q, "S0"), op="produce")
    rp.move("dock", [(q, "P_out")], op="produce")
    rp.rotate(rp.delta_to(names[1], home[names[1]]), restore=True)
    rp.cool(restore=True)
    b.programs["produce"] = insert_cooling(rp.prog, m.arch, model, policy=CoolingPolicy()).program
    b.titles["produce"] = "15-to-1 distillation; the output waits in P_out"
    b.notes["produce"] = [
        f"{sum(1 for g in gates if g[0] == 'CX')} CX, 15 T, 14 syndrome measurements on one "
        f"dock trap; the output ion f{out} is parked in the port trap",
        "distillation statistics (35p^3 output error, acceptance ~1-15p) are the protocol's "
        "claim, not simulated here: the op is verified as hardware only",
    ]
    after = {**home, q: "P_out"}
    b.expect_end["produce"] = dict(after)

    rp = RingProgram(m, f"{name}.consume", {**after, "m0": "X_out"})
    rp.move("port_in", [("m0", "P_out")], op="consume")
    rp.gate("CX", [(q, "m0")], op="consume")                      # Z_T into the parity
    # return the T ion to its own home slot: rotate that slot under the port corner first
    k = len(rp.loop)
    shift = (rp.index["S0"] - rp.index[home[q]]) % k
    shift = shift if shift <= k // 2 else shift - k
    rp.rotate(shift, op="consume")
    rp.move("undock", [(q, "S0")], op="consume")
    # measure it in X in the dock trap, away from the messenger still waiting in P_out
    rp.rotate(rp.delta_to(q, "S1"), op="consume")
    rp.move("dock", [(q, "A1")], op="consume")
    rp.gate1("H", [q], op="consume")
    rp.measure([q], op="consume")
    rp.reset([q], op="consume")
    rp.move("undock", [(q, "S1")], op="consume")
    rp.rotate(rp.delta_to(names[1], home[names[1]]), restore=True)
    rp.cool(restore=True)                        # the messenger is still in P_out
    rp.move("port_out", [("m0", "X_out")], op="consume")
    b.programs["consume"] = insert_cooling(rp.prog, m.arch, model, policy=CoolingPolicy()).program
    b.titles["consume"] = "a messenger takes Z_T; the T ion is measured in X and returns"
    b.notes["consume"] = [
        "with the messenger's Z̄ᵢ parity this completes the injection's Z̄ᵢ⊗Z_T measurement; "
        "the X outcome and the S̄ correction it implies are tracked classically (Pauli-based "
        "computation commutes Cliffords forward), not applied",
        "the T ion is measured in the dock trap while the messenger waits in the port trap; "
        "crosstalk from fluorescence between neighbouring traps is not modelled",
    ]
    b.expect_end["consume"] = {**home, "m0": "X_out"}
    b.roles["m0"] = "messenger"

    tidy(m, b.programs.values())
    xs = [nd.pos[0] for nd in m.arch.device.nodes.values()]
    ys = [nd.pos[1] for nd in m.arch.device.nodes.values()]
    b.master = Master(
        name=name, kind="leaf", family="factory", title="15-to-1 T factory",
        doc="15 ions on a 20-slot ring with one gate dock; distills one T state per run and "
            "hands its Z parity to a visiting messenger",
        size=[max(xs) - min(xs), max(ys) - min(ys)],
        ports=[Port("port", "inout", "messenger", width=1, side="N", at=0.0,
                    node="P_out", stub="X_out")],
        inventory={"magic": 15}, capacity=17, params={"ring": [10, 2, 1], "code": "rm15"},
        device=name)
    return b
