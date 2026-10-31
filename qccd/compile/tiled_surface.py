"""A rotated surface code's syndrome rounds on a grid QCCD, tiled: every plaquette plays the
same moves at once.

Our general compiler (qccdc `compile`) searches, and its time grows as the cube of the ion
count: one round takes 12 s at d = 7, 52 s at d = 9, 176 s at d = 11 -- about two days at
d = 35, the distance RSA-2048 needs.  A surface patch on a grid needs no search.  On
`grid(d + 1, d + 2, spacing=2)` (`qccd.arch.generators.grid`) the data ion of code qubit
(r, c) rests in the trap of the horizontal wire `T{c}_{r+1}h`, and the measure ion of the
check on face (i, j) (`qccd.gadget.surface.patch`) in the trap of the vertical wire
`T{j+1}_{i+1}v` at the face's centre -- one junction from each of its data ions: the
junction below it for the two data ions of row i, the one above for row i + 1.

Each layer of the circuit is played as
  1. its one-qubit gates, measurements and resets, where the ions rest;
  2. its CNOTs: every measure ion bound for row i steps to its data ion through the junction
     below its face, then every one bound for row i + 1 through the junction above (no
     junction, wire or trap holds two of them in one step: a face's junctions are its own);
     the pulses, every pair at once; the same two steps back.
So the program is the same few instructions at every distance, each over every plaquette, and
writing it takes time linear in the ions.

THE GATES ARE NEVER REORDERED: the layers are `c2lr_router.layers`, program order, and the
pulses are qccdc's (`c2lr_router.pulses`).  The certificate is the reference compiler's format
(`Compiler/bridge/check_cert.py`), built from the instructions as they are emitted: each move
a step makes, at its cycle; each op's witness at its last pulse, where its ions stand together.

    compile_tiled(qasm_text, d)           ->  (program, certificate, architecture)
    compile_tiled(qasm_text, rows, cols)  -- a lattice surgery's merged patch
    compile_tiled(exp.qasm, rows, cols, coords=exp.coords)  -- patches and seams on one
                                             lattice, in phases (`surgery_cnot`)
"""
from __future__ import annotations

from typing import Mapping, Sequence

from ..ir.tsir import TSIR, Instruction, Participant
from .c2lr_router import GATES_2Q, U3, SourceOp, _parse, layers, pulses

__all__ = ["compile_tiled", "device", "TiledError", "COMPILER_NAME"]

COMPILER_NAME = "tiled-surface"


class TiledError(ValueError):
    pass


#: the physics a device may be given: its fixed blocks, and the operating-point table that
#: prices them (as `qccd.fullstack.pipeline.PHYSICS`)
PHYSICS = {"physics@2": ("qccd-physics@2.json", "quantinuum")}
FIXED_BLOCKS = ("primitives", "heating", "species", "budget")


def device(rows: int, cols: int | None = None, *, name: str | None = None,
           physics: str | None = None):
    """The grid a rows x cols patch (cols = rows: a distance-d one) is played on: cols + 1
    junction columns, rows + 2 junction rows; with `physics`, that physics' fixed blocks
    written in (as a board does)."""
    import json
    from pathlib import Path

    from ..api import Machine
    from ..arch import Architecture
    cols = cols or rows
    tag = f"d{rows}" if cols == rows else f"{rows}x{cols}"
    arch = Machine.grid(cols + 1, rows + 2, name=name or f"tiled_{tag}", spacing=2).arch
    if physics is None:
        return arch
    ref = json.loads((Path(__file__).resolve().parents[1] / "workspace" / "physics" /
                      PHYSICS[physics][0]).read_text(encoding="utf-8"))
    doc = arch.to_json()
    doc.update({b: ref[b] for b in FIXED_BLOCKS if b in ref})
    return Architecture.from_json(doc)


def _data_trap(r: int, c: int) -> str:
    return f"T{c}_{r + 1}h"


def _face_trap(i: int, j: int) -> str:
    return f"T{j + 1}_{i + 1}v"


def _route(i: int, j: int, r: int, c: int) -> tuple[str, ...]:
    """The two wires from face (i, j)'s trap to data (r, c)'s, through the face's junction."""
    R, C = i + 1, j + 1
    if r == i:          # the junction below the face, J{C}_{R}
        return (f"T{C}_{R}v.a", f"T{c}_{R}h.a" if c == C else f"T{c}_{R}h.b")
    if r == i + 1:      # the junction above it, J{C}_{R+1}
        return (f"T{C}_{R}v.b", f"T{c}_{R + 1}h.a" if c == C else f"T{c}_{R + 1}h.b")
    raise TiledError(f"data ({r}, {c}) is not on face ({i}, {j})")


class _Build:
    def __init__(self, prog: TSIR, pos: dict):
        self.prog = prog
        self.pos = pos
        self.moves: list = []
        self.gates: list = []
        self.cycle: dict = {}

    def emit(self, **kw) -> Instruction:
        ins = Instruction(id=self.prog.next_id(), **kw)
        t = self.cycle[ins.id] = len(self.prog.instructions)
        for p in ins.participants:
            if self.pos[p.ion] != p.src:
                raise TiledError(f"instruction {ins.id}: {p.ion} is at {self.pos[p.ion]}, not {p.src}")
            self.moves.append({"t": t, "ion": p.ion, "from": p.src, "to": p.dst, "via": list(p.via)})
        for p in ins.participants:
            self.pos[p.ion] = p.dst
        self.prog.add(ins)
        return ins

    def play(self, ops: Sequence[SourceOp], ion: Mapping[int, str]) -> None:
        """The ops' pulses, slot by slot, one instruction per gate kind (as the C2LR router
        plays them); each op's witness names its last pulse."""
        seqs = {op.i: pulses(op, ion) for op in ops}
        last: dict = {}
        for k in range(max((len(s) for s in seqs.values()), default=0)):
            by_gate: dict = {}
            for op in ops:
                if k < len(seqs[op.i]):
                    by_gate.setdefault(seqs[op.i][k][0], []).append((op, seqs[op.i][k]))
            for gate in ("R", "VZ", "MS"):
                group = by_gate.get(gate)
                if not group:
                    continue
                kind = {"R": "beam", "VZ": "virtual_z", "MS": "ms"}[gate]
                meta = {"kind": kind, "op": [op.i for op, _ in group]}
                params = tuple(tuple(float(x) for x in p) for _, (_, _, p) in group)
                if gate == "MS":
                    ins = self.emit(type="gate", gate="MS", mode="intra", params=params,
                                    pairs=tuple(tuple(o) for _, (_, o, _) in group), meta=meta)
                else:
                    ins = self.emit(type="gate", gate=gate, mode="intra", arity=1, params=params,
                                    ions=tuple(o[0] for _, (_, o, _) in group), meta=meta)
                for op, _ in group:
                    last[op.i] = ins
        for op in ops:
            ions = [ion[q] for q in op.qubits]
            if len({self.pos[x] for x in ions}) != 1:
                raise TiledError(f"op {op.i}: its ions are not together")
            ins = last[op.i]
            self.gates.append({"dag": op.i, "instr": ins.id, "t": self.cycle[ins.id],
                               "site": self.pos[ions[0]], "ions": ions})


def _face(touched: set, basis: str | None) -> tuple[int, int] | None:
    """The face whose corners hold every lattice point in `touched`: unique for three or more;
    for two beside each other there are two, one a Z face (i + j even) and one an X face, and
    the check's basis picks it (`qccd.gadget.surface.patch`'s rule)."""
    rs, cs = {r for r, _ in touched}, {c for _, c in touched}
    if not touched or max(rs) - min(rs) > 1 or max(cs) - min(cs) > 1:
        return None
    found = [(i, j) for i in range(max(rs) - 1, min(rs) + 1) for j in range(max(cs) - 1, min(cs) + 1)
             if basis is None or (i + j) % 2 == (basis == "X")]
    return found[0] if len(found) == 1 else None


def compile_tiled(qasm_text: str, rows: int, cols: int | None = None, *, name: str = "circuit",
                  arch=None, coords=None) -> tuple:
    """`(program, certificate, architecture)` for a circuit of surface-code checks on a lattice of
    rows x cols data points -- uncooled.  The data qubits come first: by default q0 ..
    q[rows*cols - 1] row-major, a rotated surface patch (`qccd.gadget.surface.patch`; cols =
    rows: distance d; a lattice surgery's merged patch is (2d + 1) x d or d x (2d + 1)) as
    `qccd.qec.experiments.rotated_surface` writes them; with `coords`, the (row, column) of each
    (a surgery's patches and seams, `MemoryExperiment.coords`).  Every other qubit is a check's
    measure ion and every two-qubit gate is between a measure ion and a data ion of its check.
    The checks are found from the gates: a measure ion's face is the one whose corners hold the
    data ions it ever touches, of its basis (an X check's ion is the CNOTs' control).  Any set
    of faces plays: a face's junctions are its own, whichever others are there."""
    cols = cols or rows
    arch = arch or device(rows, cols)
    n, ops, _ = _parse(qasm_text)
    at = [tuple(x) for x in coords] if coords is not None else [divmod(q, cols) for q in range(rows * cols)]
    nd = len(at)
    if len(set(at)) != nd or not all(0 <= r < rows and 0 <= c < cols for r, c in at):
        raise TiledError(f"the data coordinates are not distinct points of the {rows} x {cols} lattice")
    touch: dict = {}
    role: dict = {}
    for op in ops:
        if op.name in GATES_2Q:
            a, b = op.qubits
            if (a < nd) == (b < nd):
                raise TiledError(f"op {op.i} ({op.name} q{a}, q{b}) is not between a data ion and a measure ion")
            m, q = (a, b) if a >= nd else (b, a)
            touch.setdefault(m, set()).add(at[q])
            if op.name == "cx":
                role.setdefault(m, set()).add("X" if m == a else "Z")
        elif op.name not in U3 and op.name not in ("measure", "reset", "barrier"):
            raise TiledError(f"op {op.i}: {op.name} is not played here")
    face: dict = {}
    for m in range(nd, n):
        basis = role.get(m)
        f = _face(touch.get(m, set()), next(iter(basis)) if basis and len(basis) == 1 else None)
        if f is None:
            raise TiledError(f"q{m} touches data at {sorted(touch.get(m, ()))}: not the corners of "
                             f"one face of the {rows} x {cols} lattice")
        face[m] = f
    if len(set(face.values())) != len(face):
        raise TiledError("two measure ions on one face")
    ion = {q: f"q{q}" for q in range(n)}
    place = {ion[q]: _data_trap(*at[q]) for q in range(nd)}
    place.update({ion[m]: _face_trap(*face[m]) for m in face})
    prog = TSIR(name=name, arch_spec=f"arch/{arch.name}.arch.json",
                meta={"compiler": COMPILER_NAME, "patch": [rows, cols],
                      "qubit_map": {str(q): ion[q] for q in range(n)}})
    b = _Build(prog, dict(place))
    b.emit(type="init", placement=dict(place), quanta={i: 0.0 for i in place},
           meta={"compiler": COMPILER_NAME, "circuit": name, "arch": arch.name})
    b.emit(type="cool", broadcast=True, meta={"kind": "state_prep"})
    for layer in layers(ops):
        one = [o for o in layer if o.name in U3]
        if one:
            b.play(one, ion)
        for kind in ("measure", "reset"):
            group = [o for o in layer if o.name == kind]
            if group:
                b.emit(type=kind, ions=tuple(ion[o.qubits[0]] for o in group),
                       meta={"kind": "readout" if kind == "measure" else "reset",
                             "op": [o.i for o in group]})
        two = [o for o in layer if o.name in GATES_2Q]
        if not two:
            continue
        legs = {"north": [], "south": []}
        for o in two:
            m, q = (o.qubits[0], o.qubits[1]) if o.qubits[0] >= nd else (o.qubits[1], o.qubits[0])
            i, j = face[m]
            r, c = at[q]
            via = _route(i, j, r, c)
            legs["north" if r == i else "south"].append(
                Participant(ion[m], _face_trap(i, j), _data_trap(r, c), via))
        for leg in ("north", "south"):
            if legs[leg]:
                b.emit(type="simd", cls="shuttle", mode="inter", participants=tuple(legs[leg]),
                       meta={"kind": "route", "leg": leg, "op": [o.i for o in two]})
        b.play(two, ion)
        for leg in ("north", "south"):
            if legs[leg]:
                back = tuple(Participant(p.ion, p.dst, p.src, tuple(reversed(p.via))) for p in legs[leg])
                b.emit(type="simd", cls="shuttle", mode="inter", participants=back,
                       meta={"kind": "undock", "leg": leg, "op": [o.i for o in two]})
    cert = {
        "version": 1, "circuit": name, "arch": arch.name, "n_qubits": n,
        "circuit_ops": [o.to_json() for o in ops],
        "map": {str(q): ion[q] for q in range(n)}, "init": dict(place),
        "moves": b.moves, "gates": b.gates, "rotations": [], "unrealised": [],
        "claims": {"cycles": len(prog.instructions),
                   "beams": sum(len(i.ions) for i in prog.instructions if i.gate == "R"),
                   "ms_gates": sum(len(i.pairs) for i in prog.instructions if i.gate == "MS"),
                   "frames": sum(len(i.ions) for i in prog.instructions if i.gate == "VZ")},
        "compiler": COMPILER_NAME,
    }
    return prog, cert, arch
