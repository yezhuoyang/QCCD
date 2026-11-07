"""What the Cyclone and WISE compilers share: writing a programme and its certificate.

Both compilers decide transport their own way and hand this module the instructions.  It keeps
the ions' positions, writes the certificate's moves from the participants, and turns a batch of
source gates into native pulses (`c2lr_router.pulses`, which are qccdc's).  Nothing here changes
the order of a qubit's gates: `play` runs each op's pulses in the op's order, and a batch holds
ops on disjoint qubits.

The certificate is the reference compiler's format (`Compiler/bridge/check_cert.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from ..ir.tsir import TSIR, Instruction
from .c2lr_router import SourceOp, pulses

__all__ = ["Emitter", "certificate"]

#: the native kinds, as a batch's instructions are tagged (`c2lr_router._Build._play`'s words)
_KIND = {"R": "beam", "VZ": "virtual_z", "MS": "ms"}


@dataclass
class Emitter:
    """A programme under construction, and the state its certificate is read from."""

    prog: TSIR
    pos: dict[str, str]
    #: put only identical pulses in one instruction: WISE's one gate kind per layer (rule W10).
    #: Otherwise a batch's pulses run together, each at its own angle.
    one_kind: bool = False
    moves: list[dict] = field(default_factory=list)
    gates: list[dict] = field(default_factory=list)
    cycle: dict[int, int] = field(default_factory=dict)

    def emit(self, **kw) -> Instruction:
        ins = Instruction(id=self.prog.next_id(), **kw)
        t = self.cycle[ins.id] = len(self.prog.instructions)
        for p in ins.participants:
            self.moves.append({"t": t, "ion": p.ion, "from": p.src, "to": p.dst, "via": list(p.via)})
            self.pos[p.ion] = p.dst
        self.prog.add(ins)
        return ins

    def witness(self, op: SourceOp, ins: Instruction, ions: Sequence[str]) -> None:
        """Op `op` ran at instruction `ins`, with `ions` standing together where the first is."""
        self.gates.append({"dag": op.i, "instr": ins.id, "t": self.cycle[ins.id],
                           "site": self.pos[ions[0]], "ions": list(ions)})

    def pulse_group(self, gate: str, group: Sequence[tuple], ops: Iterable[int]) -> Instruction:
        """One native instruction: `group` holds each operand's `(operands, params)`."""
        meta = {"kind": _KIND[gate], "op": sorted(set(ops))}
        params = tuple(tuple(float(x) for x in p) for _, p in group)
        if gate == "MS":
            return self.emit(type="gate", gate="MS", mode="intra", params=params,
                             pairs=tuple(tuple(o) for o, _ in group), meta=meta)
        return self.emit(type="gate", gate=gate, mode="intra", arity=1, params=params,
                         ions=tuple(o[0] for o, _ in group), meta=meta)

    def play(self, ops: Sequence[SourceOp], ion: Mapping[int, str],
             seqs: Mapping[int, Sequence] | None = None, *, witness: bool = True) -> dict[int, Instruction]:
        """The ops' pulses, each op's in its own order, packed into as few instructions as the
        layer's rule allows.  A frame change takes no time, so a ready one goes first.  Returns
        each op's last pulse; `witness` names it as the op's witness."""
        seqs = {op.i: list((seqs or {}).get(op.i, pulses(op, ion))) for op in ops}
        at = {op.i: 0 for op in ops}
        last: dict[int, Instruction] = {}
        while True:
            ready: dict[tuple, list] = {}
            for op in ops:
                k = at[op.i]
                if k < len(seqs[op.i]):
                    gate, operands, params = seqs[op.i][k]
                    key = (gate, tuple(params)) if self.one_kind and gate != "VZ" else (gate, ())
                    ready.setdefault(key, []).append((op, operands, params))
            if not ready:
                break
            # the frame changes first, as they cost nothing; then the largest group, and among
            # equals the first in the circuit, so the same input always gives the same programme
            key = min(ready, key=lambda k: (k[0] != "VZ", -len(ready[k]), ready[k][0][0].i))
            group = ready[key]
            ins = self.pulse_group(key[0], [(o, p) for _, o, p in group], [op.i for op, _, _ in group])
            for op, _, _ in group:
                at[op.i] += 1
                last[op.i] = ins
        if witness:
            for op in ops:
                self.witness(op, last[op.i], [ion[q] for q in op.qubits])
        return last


def certificate(prog: TSIR, ops: Sequence[SourceOp], name: str, arch_name: str, n: int,
                ion: Mapping[int, str], init: Mapping[str, str], em: Emitter, compiler: str) -> dict:
    """The programme's certificate: what `compile_with_qccdc` writes beside its programme."""
    return {
        "version": 1, "circuit": name, "arch": arch_name, "n_qubits": n,
        "circuit_ops": [o.to_json() for o in ops],
        "map": {str(q): ion[q] for q in range(n)}, "init": dict(init),
        "moves": em.moves, "gates": em.gates, "rotations": [], "unrealised": [],
        "claims": {"cycles": len(prog.instructions),
                   "beams": sum(len(i.ions) for i in prog.instructions if i.gate == "R"),
                   "ms_gates": sum(len(i.pairs) for i in prog.instructions if i.gate == "MS"),
                   "frames": sum(len(i.ions) for i in prog.instructions if i.gate == "VZ")},
        "compiler": compiler,
    }
