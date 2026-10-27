"""The rotation source: one ion in a small rotation state, for rotations finer than T.

FormalRV leaves a Pauli-product rotation `ppmrot(±π/2^k) P` finer than π/8 as a *synthesis
stub* (`tSynth[k]` magic states, no circuit).  QCCD does it the way a T gate is done, one level
down (docs/FULLSTACK_PLAN.md, S5d): measure P̄ ⊗ Z̄ against a block in

    |m⟩ = RZn|+⟩ = (|0⟩ + e^{iπ/2^n}|1⟩)/√2,    n = k − 1,

read that block in X, and the blocks have turned by exp(∓iπ/2^k P̄) -- by the wrong sign half
the time, which the doubled angle, one level up, puts right (repeat until success, ending at a
T state, a |Ȳ⟩ or a Pauli).  This place makes the physical half of |m⟩: a fresh ion comes in,
is reset, put in |+⟩ and rotated, at a dock, and leaves; the pharmacy's `inject` -- verified by
its flows, X(m) → X̄ and Z(m) → Z̄ -- encodes it into a block, as it does the factory's T state.

    rz<n>, rz<n>dg     RZn|+⟩ or RZn†|+⟩ on the ion that leaves, n = 3 … 10

The op is verified like the factory's distillation: replayed as hardware, every rule checked,
and its circuit simulated as a state vector -- the ion that leaves holds exactly RZn|+⟩.  It is
an injection: one fault on the ion changes the state undetectably (distance 1, as for |Ȳ⟩);
the user does not ask for circuit distance here.
"""
from __future__ import annotations

import cmath
import math

from ..model import Port
from .bay import BayProgram, bay
from .common import LeafBuild, tidy

__all__ = ["rotation_source", "EXTRA_PLACES", "ANGLES"]

#: the rotations a source makes: RZn|+⟩ for these n (a Pauli-product rotation by π/2^(n+1))
ANGLES = tuple(range(3, 11))
KEEP = ("rotate_cw", "rotate_ccw", "dock", "undock", "port_in", "port_out")


def rotation_source(*, name: str = "rot1") -> LeafBuild:
    from .places import _cooled, _master
    W = 4
    b = bay(name, W, [1], {"in": "BL", "out": "TR"}, {"in": 1, "out": 1})
    build = LeafBuild(master=None, machine=b.machine, home={}, roles={"m0": "magic"})
    for n in ANGLES:
        for dg in (False, True):
            key = f"rz{n}" + ("dg" if dg else "")
            gate = f"RZ{n}" + ("_DAG" if dg else "")
            bp = BayProgram(b, f"{name}.{key}", {"m0": "X_in"})
            bp.absorb("in", ["m0"], [1], op="enter")

            def init(batch, gate=gate, bp=bp):
                bp.reset(batch, op="init")
                bp.gate1("H", batch, op="init")
                bp.gate1(gate, batch, op="init")

            bp.at_docks(["m0"], init, op="init")
            bp.emit("out", ["m0"], op="leave")
            bp.restore(op="leave")
            build.programs[key] = _cooled(bp.prog, b.machine)
            build.titles[key] = f"one ion in {gate.replace('_DAG', '†')}|+⟩"
            build.expect_end[key] = {"m0": "X_out"}
            build.notes[key] = [
                f"reset, H and {gate} on one ion at the dock: it leaves holding "
                f"(|0⟩ + e^{{{'-' if dg else ''}iπ/{2 ** n}}}|1⟩)/√2, the resource of a "
                f"Pauli-product rotation by {'-' if dg else ''}π/{2 ** (n + 1)}",
                "checked as a state vector; not fault-tolerant (an injection, distance 1)"]
            build.logic_checks[key] = _check(n, dg)
    tidy(b.machine, build.programs.values(), keep_classes=KEEP)
    return _master(build, name=name, category="rotation", title="Rotation source",
                   doc="a fresh ion comes in, is put in a small rotation state RZn|+⟩ at the "
                       "dock, and leaves for a pharmacy to encode",
                   ports=[Port("in", "in", "fresh", 1, "W", 0.8, node=b.trap("in"),
                               stub=b.stub("in")),
                          Port("out", "out", "magic", 1, "E", 0.2, node=b.trap("out"),
                               stub=b.stub("out"))],
                   inventory={}, capacity=1,
                   params={"ring_slots": b.N, "angles": [f"π/{2 ** n}" for n in ANGLES]},
                   size=b.size())


def _check(n: int, dg: bool):
    """The state the ion leaves with, as a state vector, against RZn|+⟩ (or its inverse)."""
    def check(circ):
        from ..logic.statevec import qubit_state, run_postselected
        st, p = run_postselected(circ)
        amp = qubit_state(st, circ.index["m0"])
        ph = cmath.exp((-1j if dg else 1j) * math.pi / 2 ** n)
        want = (1 / math.sqrt(2), ph / math.sqrt(2))
        fid = abs(want[0].conjugate() * amp[0] + want[1].conjugate() * amp[1]) ** 2 if amp else 0.0
        label = f"RZ{n}{'†' if dg else ''}|+⟩"
        return {"kind": "statevector", "output": label, "fidelity": round(fid, 12),
                "accept": round(p, 12), "passed": bool(amp and fid > 1 - 1e-9 and abs(p - 1) < 1e-9),
                "claim": f"the ion that leaves holds {label} exactly"}
    return check


EXTRA_PLACES = {"rot1": rotation_source}
