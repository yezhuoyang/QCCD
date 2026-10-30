"""The baseline C2LR router: an OpenQASM circuit onto a swap-or-stay lattice (docs/c2lr.md).

A C2LR device moves its crystals only by swap-or-stay steps: one shared waveform at every
junction, one direction bit per crystal (`qccd.arch.c2lr`, `qccd.verify.c2lr`).  So routing
here is not choosing paths through junctions -- every route is the device's matching -- but
choosing, step by step, a variation and the crystals shifted toward their junction.  This is
the reference compiler of the C2LR boards, a baseline: correct first, then not wasteful.

    python -m qccd.compile.c2lr_router --circuit C --device D --expanded E --out DIR

is the `qccd.compiler@1` contract (`qccd.bench.contract`), and `compile_c2lr` the call.

THE GATES ARE NEVER REORDERED.  Every op keeps its place among the ops on its own qubits --
two commuting CNOTs on one ancilla too: their order is the circuit's hook structure.  The
ops are grouped into layers in program order (`layers`): each op joins the layer of the op
before it or a later one, never an earlier one, so an op is only ever delayed; ops in one
layer share no qubit, which is parallelism, not reordering.

One layer is played as
  1. its one-qubit gates, measurements and resets, where the crystals are;
  2. its two-qubit gates: route each pair's crystals into two legs that pair at a junction
     (parallel swap-or-stay steps, each the variation that brings the pairs closest);
     ENTER -- one crystal of each pair shifted toward the junction, the other away, so it
     joins its partner's leg (C1's two-crystal exception); the pulses, MS between them
     (the replay prices merge + MS + split); EXIT -- the same variation, the same crystal
     toward, back to its own leg.  A layer whose pairs cannot all be routed at once gates
     the ready ones first and routes the rest after: delayed, never reordered.

Only the four single diagonals are used, which arXiv:2403.00756 demonstrates (Sec. IV): the
programmes pass `c2lr-demonstrated` as well as `c2lr`.  The checkerboard two-pair steps are
the device's to offer and a better router's to use.

The certificate is the reference compiler's format (`Compiler/bridge/check_cert.py`): each
crystal a step moves is a plain move at the step's cycle, leg -> junction -> partner leg,
taken from the replay's own expansion (`C2LRTracker.expand`), so the certificate cannot
disagree with what the device does; a crystal that stays writes nothing.  The proved Lean
checker reads it unchanged.  The cycle clock `t` is the instruction's position in the
programme; a gate's witness names its op's last pulse.

Pulses are qccdc's (`Compiler/ocaml/lib/gateset.ml`): a one-qubit gate is its u3 triple as
one beam and one frame change (`QCCDC.u3_decomp`), a CX is R, MS, R, R, R (`QCCDC.cx_decomp`),
a CZ is a CX between two Hadamards on its target.  Anything else is refused by name.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from ..arch import Architecture
from ..arch.c2lr import DIAGONALS, NotC2LR, Variation, declared, geometry, is_c2lr
from ..ir.tsir import TSIR, Instruction, swap_or_stay

__all__ = ["Refused", "RouteError", "SourceOp", "parse_qasm", "compile_c2lr", "compile_with_router",
           "compile_with_stages", "write_stages", "COMPILER_NAME", "STAGE_ENGINE", "main"]

COMPILER_NAME = "c2lr-router (baseline)"
PI = math.pi

#: OpenQASM's one-qubit gates as u3(theta, phi, lambda): qccdc's table (gateset.ml `u3_of_gate`)
U3 = {
    "id": (0.0, 0.0, 0.0), "x": (PI, 0.0, PI), "y": (PI, PI / 2, PI / 2), "z": (0.0, 0.0, PI),
    "h": (PI / 2, 0.0, PI), "s": (0.0, 0.0, PI / 2), "sdg": (0.0, 0.0, -PI / 2),
    "sx": (PI / 2, -PI / 2, PI / 2), "sxdg": (PI / 2, PI / 2, -PI / 2),
}
GATES_2Q = ("cx", "cz")


class Refused(ValueError):
    """The router cannot compile this pair: the device is not a C2LR lattice, it has too few
    legs, or the circuit uses something outside the supported set.  Exit 4 under the contract."""


class RouteError(RuntimeError):
    """The router produced something the replay refuses or a rule fails: a bug, never a verdict."""


# ------------------------------------------------------------------ the circuit

@dataclass(frozen=True)
class SourceOp:
    """One statement of the source circuit, as the certificate's `circuit_ops` lists it."""

    i: int
    name: str
    qubits: tuple[int, ...]
    line: int
    #: the classical bits a measurement writes, numbered across the `creg`s in declaration order
    clbits: tuple[int, ...] = ()

    def to_json(self) -> dict:
        return {"i": self.i, "name": self.name, "qubits": list(self.qubits), "params": [],
                "line": self.line}

    def stage_json(self) -> dict:
        """The op as the native stages' evidence names it (`qccd.compile.c2lr_stages`)."""
        return {"i": self.i, "name": self.name, "qubits": list(self.qubits),
                "clbits": list(self.clbits), "params": []}


_DECL = re.compile(r"^(qreg|creg)\s+([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]$")
_MEASURE = re.compile(r"^measure\s+(.+?)\s*->\s*(.+)$")
_ARG = re.compile(r"^([A-Za-z_]\w*)\s*(?:\[\s*(\d+)\s*\])?$")


def parse_qasm(text: str) -> tuple[int, list[SourceOp]]:
    """`(qubits, ops)` of an OpenQASM 2.0 circuit in the supported set: `qreg`/`creg`, the
    gates of `U3` and `GATES_2Q`, `measure`, `reset`, `barrier`.  Qubits are numbered in
    declaration order, which is how the certificate's map is written; classical bits too.
    Raises `Refused`, naming the statement, for anything else."""
    return _parse(text)[:2]


def _parse(text: str) -> tuple[int, list[SourceOp], int]:
    """`parse_qasm`, and how many classical bits the circuit declares."""
    regs: dict[str, dict[str, tuple[int, int]]] = {"qreg": {}, "creg": {}}
    count = {"qreg": 0, "creg": 0}
    ops: list[SourceOp] = []

    def operands(kind: str, args: str, line: int) -> list[int]:
        what = "qubit" if kind == "qreg" else "classical bit"
        out = []
        for a in (x.strip() for x in args.split(",")):
            m = _ARG.match(a)
            if not m or m.group(1) not in regs[kind]:
                raise Refused(f"line {line}: operand {a!r} is not a declared {what}")
            base, size = regs[kind][m.group(1)]
            if m.group(2) is None:
                out += range(base, base + size)
            elif int(m.group(2)) < size:
                out.append(base + int(m.group(2)))
            else:
                raise Refused(f"line {line}: {a} is outside {m.group(1)}[{size}]")
        return out

    def qubits(args: str, line: int) -> list[int]:
        return operands("qreg", args, line)

    for lineno, raw in enumerate(text.splitlines(), 1):
        body = raw.split("//", 1)[0].strip()
        for stmt in (s.strip() for s in body.split(";")):
            if not stmt or stmt.startswith(("OPENQASM", "include")):
                continue
            m = _DECL.match(stmt)
            if m:
                regs[m.group(1)][m.group(2)] = (count[m.group(1)], int(m.group(3)))
                count[m.group(1)] += int(m.group(3))
                continue
            m = _MEASURE.match(stmt)
            if m:
                qs, cs = qubits(m.group(1), lineno), operands("creg", m.group(2), lineno)
                if len(qs) != len(cs):
                    raise Refused(f"line {lineno}: measure writes {len(qs)} qubit(s) into "
                                  f"{len(cs)} classical bit(s)")
                for q, c in zip(qs, cs):
                    ops.append(SourceOp(len(ops), "measure", (q,), lineno, (c,)))
                continue
            head, _, rest = stmt.partition(" ")
            if "(" in head:
                raise Refused(f"line {lineno}: the parameterised gate {head!r} is not in the "
                              f"router's set ({', '.join(list(U3) + list(GATES_2Q))})")
            qs = qubits(rest, lineno) if rest.strip() else []
            if head == "barrier":
                ops.append(SourceOp(len(ops), "barrier", tuple(qs), lineno))
            elif head in ("reset",) + tuple(U3):
                for q in qs:
                    ops.append(SourceOp(len(ops), head, (q,), lineno))
            elif head in GATES_2Q:
                if len(qs) != 2 or qs[0] == qs[1]:
                    raise Refused(f"line {lineno}: {head} takes two distinct qubits")
                ops.append(SourceOp(len(ops), head, tuple(qs), lineno))
            else:
                raise Refused(f"line {lineno}: {stmt!r} is not in the router's set (gates "
                              f"{', '.join(list(U3) + list(GATES_2Q))}, measure, reset, barrier)")
    return count["qreg"], ops, count["creg"]


def pulses(op: SourceOp, ion: Mapping[int, str]) -> list[tuple[str, tuple[str, ...], tuple]]:
    """The native pulses of one gate, in time order: `(gate, operands, params)`."""
    if op.name in U3:
        theta, phi, lam = U3[op.name]
        q = ion[op.qubits[0]]
        out: list = []
        if theta:
            out.append(("R", (q,), (theta, PI / 2 - lam)))
        if (phi + lam) % (2 * PI):
            out.append(("VZ", (q,), (phi + lam,)))
        # `id` emits no pulse; a frame change by nothing is still an instruction its witness names
        return out or [("VZ", (q,), (0.0,))]
    a, b = ion[op.qubits[0]], ion[op.qubits[1]]
    cx = [("R", (a,), (PI / 2, PI / 2)), ("MS", (a, b), (PI / 2,)), ("R", (a,), (-PI / 2, 0.0)),
          ("R", (b,), (-PI / 2, 0.0)), ("R", (a,), (-PI / 2, PI / 2))]
    if op.name == "cx":
        return cx
    h = pulses(SourceOp(-1, "h", (op.qubits[1],), 0), ion)
    return h + cx + h


def layers(ops: Sequence[SourceOp]) -> list[list[SourceOp]]:
    """The ops in layers, IN ORDER: each op joins the layer of the op before it, or a later
    one -- the first after the last op on any of its wires (its qubits, and the classical
    bit a measurement writes) -- so no op ever runs before one that precedes it in the
    circuit; an op is only delayed.  A barrier fences its qubits at the latest of them.  On
    a circuit written in time steps (a syndrome round's CNOTs, step by step) the layers are
    its steps."""
    out: list[list[SourceOp]] = []
    for op, k in zip(ops, layer_of(ops)):
        if op.name == "barrier":
            continue
        while len(out) <= k:
            out.append([])
        out[k].append(op)
    return out


def layer_of(ops: Sequence[SourceOp]) -> list[int]:
    """Each op's layer, as `layers` places it; a barrier's is the fence it sets, the latest
    layer on its qubits (-1 when none has run yet), which no later op on them may share."""
    last: dict[tuple[str, int], int] = {}
    got: list[int] = []
    k = 0
    for op in ops:
        wires = [("q", q) for q in op.qubits] + [("c", c) for c in op.clbits]
        at = max((last.get(w, -1) for w in wires), default=-1)
        if op.name == "barrier":
            for w in wires:
                last[w] = at
            got.append(at)
            continue
        k = max(k, at + 1)
        got.append(k)
        for w in wires:
            last[w] = k
    return got


# ------------------------------------------------------------------ the lattice

class Lattice:
    """The legs of a C2LR device as a graph: two legs are joined when a diagonal the device
    offers pairs them at a junction (one horizontal, one vertical: the studio's diagonal
    grid), each edge labelled with that diagonal."""

    def __init__(self, arch: Architecture):
        try:
            self.geo = geometry(arch)
        except NotC2LR as exc:
            raise Refused(str(exc)) from None
        offer = declared(arch)["diagonals"]
        self.diagonals = [d for d in DIAGONALS if d in offer]
        if not self.diagonals:
            raise Refused(f"{arch.name} offers no single diagonal; this router plays only those")
        self.legs = list(self.geo.legs)
        self.pairs = {d: self.geo.pairs(Variation("diagonal", diagonal=d)) for d in self.diagonals}
        self.edge: dict[frozenset, str] = {}
        self.nbrs: dict[str, list[str]] = {leg: [] for leg in self.legs}
        for d, pairs in self.pairs.items():
            for leg, (_, partner) in pairs.items():
                if frozenset((leg, partner)) not in self.edge:
                    self.edge[frozenset((leg, partner))] = d
                    self.nbrs[leg].append(partner)
                    self.nbrs[partner].append(leg)
        for v in self.nbrs.values():
            v.sort()
        self.dist = {leg: self._bfs(leg) for leg in self.legs}
        if any(len(d) != len(self.legs) for d in self.dist.values()):
            raise Refused(f"{arch.name}'s legs are not all connected by the diagonals it offers")
        bad = [leg for leg in self.legs if not (arch.can(leg, "gate") and arch.can(leg, "spam"))]
        if bad:
            raise Refused(f"legs {bad[:3]} cannot gate or measure; this router gates and reads "
                          f"out a crystal where it stands")

    def _bfs(self, src: str) -> dict[str, int]:
        out = {src: 0}
        todo = deque([src])
        while todo:
            x = todo.popleft()
            for y in self.nbrs[x]:
                if y not in out:
                    out[y] = out[x] + 1
                    todo.append(y)
        return out

    def path(self, src: str, dst: str) -> list[str]:
        """A shortest leg path, the first neighbour in name order at every hop."""
        out = [src]
        while out[-1] != dst:
            here = self.dist[dst][out[-1]]
            out.append(next(y for y in self.nbrs[out[-1]] if self.dist[dst][y] == here - 1))
        return out


#: what one extra enter-and-exit pair of steps costs a layer, against one leg of distance
#: between a gate's crystals (which costs at least one routing step, and usually two)
_DIAGONAL_PENALTY = 0.4


def place(lat: Lattice, n: int, plan: Sequence[Sequence[SourceOp]], *, seed: int = 0,
          restarts: int = 6, iters: int = 25000) -> dict[int, str]:
    """qubit -> leg: annealed so each two-qubit gate's crystals start in paired legs, and
    each layer's pairs pair under as few diagonals as can be (one enter step and one exit
    step for each).  The cost of a placement is its legs of distance between gate partners,
    each distinct pair once, plus `_DIAGONAL_PENALTY` for every diagonal a layer of gates
    needs beyond one.  Deterministic for a seed."""
    if n > len(lat.legs):
        raise Refused(f"{n} qubits and {len(lat.legs)} legs: one crystal is one qubit")
    pairs = sorted({tuple(sorted(o.qubits)) for layer in plan for o in layer if o.name in GATES_2Q})
    shapes = sorted({frozenset(tuple(sorted(o.qubits)) for o in layer if o.name in GATES_2Q)
                     for layer in plan}, key=sorted)
    shapes = [sorted(s) for s in shapes if len(s) > 1]
    pairs_of: dict[int, list] = {q: [p for p in pairs if q in p] for q in range(n)}
    shapes_of: dict[int, list] = {q: [k for k, s in enumerate(shapes) if any(q in p for p in s)]
                                  for q in range(n)}
    dist, edge = lat.dist, lat.edge

    def shape_cost(at, k: int) -> float:
        ds = {edge.get(frozenset((at[a], at[b]))) for a, b in shapes[k]}
        ds.discard(None)
        return _DIAGONAL_PENALTY * max(len(ds) - 1, 0)

    def local(at, qs) -> float:
        """The cost terms that involve the qubits `qs`."""
        ps = {p for q in qs for p in pairs_of[q]}
        ks = {k for q in qs for k in shapes_of[q]}
        return sum(dist[at[a]][at[b]] - 1 for a, b in ps) + sum(shape_cost(at, k) for k in ks)

    def total(at) -> float:
        return local(at, range(n))

    rng = random.Random(seed)
    # the first start follows the circuit: qubits in order along a breadth-first sweep
    sweep = list(lat.dist[lat.legs[0]])
    best = {q: sweep[q] for q in range(n)}
    best_c = total(best)
    for r in range(restarts):
        if best_c == 0:
            break
        at = dict(best) if r == 0 else dict(zip(range(n), rng.sample(lat.legs, n)))
        who = {leg: q for q, leg in at.items()}
        c = total(at)
        temp, cool = 2.0, (0.02 / 2.0) ** (1.0 / iters)
        for _ in range(iters):
            q = rng.randrange(n)
            leg = rng.choice(lat.legs)
            old, other = at[q], who.get(leg)
            if other == q:
                continue
            qs = (q,) if other is None else (q, other)
            before = local(at, qs)
            at[q] = leg
            if other is not None:
                at[other] = old
            delta = local(at, qs) - before
            if delta <= 0 or rng.random() < math.exp(-delta / temp):
                c += delta
                who.pop(old, None)
                who[leg] = q
                if other is not None:
                    who[old] = other
                if c < best_c - 1e-9:
                    best, best_c = dict(at), c
            else:
                at[q] = old
                if other is not None:
                    at[other] = leg
            temp *= cool
    return best


# ------------------------------------------------------------------ the router

#: The native stages' evidence, in the form `qccdc` writes its own (`qccd.compiler.stages.v1`,
#: Compiler/ocaml/lib/stages.ml) under an engine of its own: `qccdc check-stages` cannot read a
#: swap-or-stay step, so `qccd.compile.c2lr_stages` re-checks these (docs/c2lr.md section 6d)
STAGE_ENGINE = "c2lr-router"
STAGE_NAMES = {
    "circuit_normalization": "Circuit Normalization", "dependency_analysis": "Dependency Analysis",
    "initial_placement": "Initial Placement", "interaction_placement": "Interaction Placement",
    "transport_synthesis": "Transport Synthesis", "native_gate_lowering": "Native Gate Lowering",
    "broadcast_packing": "Broadcast Packing",
}
#: what the router claims about each boundary: nothing -- the checker's verdicts are recorded
UNCHECKED = {"status": "not_run", "method": "", "coverage": [],
             "diagnostics": ["the router claims nothing here; qccd.compile.c2lr_stages re-checks "
                             "the saved evidence"]}


@dataclass
class _Build:
    arch: Architecture
    lat: Lattice
    prog: TSIR
    pos: dict[str, str]
    moves: list[dict] = field(default_factory=list)
    gates: list[dict] = field(default_factory=list)
    #: the native stages' records, when asked for (`compile_c2lr(stages=...)`)
    stages: list[dict] | None = None

    def __post_init__(self) -> None:
        from ..verify.c2lr import C2LRTracker
        self.trk = C2LRTracker(self.arch)
        self.at = {leg: ion for ion, leg in self.pos.items()}
        self.cycle: dict[int, int] = {}          # instruction id -> its cycle (its position)
        self._steps: list[dict] = []             # the steps since the last stage record
        self._steps_from: dict[str, str] = {}

    # -- the native stages' evidence: what each boundary produced, never a verdict on it

    def record(self, stage_id: str, inp, out, witness) -> None:
        if self.stages is not None:
            self.stages.append({"id": stage_id, "name": STAGE_NAMES[stage_id], "input": inp,
                                "output": out, "witness": witness, "check": dict(UNCHECKED)})

    def flush(self) -> None:
        """The steps played since the last record, as one transport stage."""
        if self._steps:
            self.record("transport_synthesis", {"positions": self._steps_from},
                        {"steps": self._steps, "final_positions": dict(self.pos)}, {})
            self._steps, self._steps_from = [], {}

    def batch(self, ops: Sequence[SourceOp], ion: Mapping[int, str], play) -> None:
        """One batch of ops where their crystals stand: where each runs (interaction
        placement), its pulses (native lowering), and the commands `play` emits for them
        (broadcast packing)."""
        self.flush()
        start = len(self.prog.instructions)
        sited = [{"operation": op.stage_json(),
                  "assignment": {"site": self.pos[ion[op.qubits[0]]], "ions": [ion[q] for q in op.qubits]}}
                 for op in ops]
        native = [{"operation": op.stage_json(), "site": s["assignment"]["site"],
                   "ions": s["assignment"]["ions"],
                   "pulses": [{"gate": g, "ions": list(o), "params": [float(x) for x in p]}
                              for g, o, p in (pulses(op, ion) if op.name not in ("measure", "reset") else ())]}
                  for op, s in zip(ops, sited)]
        self.record("interaction_placement", {"positions": dict(self.pos),
                                              "operations": [op.stage_json() for op in ops]},
                    sited, {"sequential": False})
        play()
        self.record("native_gate_lowering", sited, native, {})
        self.record("broadcast_packing", native, [i.to_json() for i in self.prog.instructions[start:]], {})

    # -- emission: every instruction goes through here, so the certificate is the replay's

    def emit(self, **kw) -> Instruction:
        ins = Instruction(id=self.prog.next_id(), **kw)
        t = self.cycle[ins.id] = len(self.prog.instructions)
        if ins.type == "init":
            self.trk.init({}, self.pos)
        elif ins.type == "simd":
            if not self._steps:
                self._steps_from = dict(self.pos)
            self._steps.append({"class": ins.cls, "template": dict(ins.template or {})})
            step, after = self.trk.expand(ins, self.pos)
            broken = list(step.c1) + list(step.c2) + list(step.c6)
            if broken:
                raise RouteError(f"instruction {ins.id}: {broken[0]}")
            for p in step.participants:
                self.moves.append({"t": t, "ion": p.ion, "from": p.src, "to": p.dst, "via": list(p.via)})
            self.pos = after
            self.at = {}
            for ion, leg in after.items():
                self.at.setdefault(leg, ion)
        else:
            self.trk.observe(self.pos, self.pos, ins)
        self.prog.add(ins)
        return ins

    def step(self, diagonal: str, toward: Sequence[str], kind: str) -> None:
        self.emit(type="simd", cls="swap_or_stay", mode="inter",
                  template=swap_or_stay(diagonal, sorted(toward)), meta={"kind": kind})

    def witness(self, op: SourceOp, ins: Instruction, ions: Sequence[str]) -> None:
        """Op `op` ran at instruction `ins` (its last pulse), with `ions` together where the
        first of them stands: nothing moves between an op's first pulse and its last."""
        self.gates.append({"dag": op.i, "instr": ins.id, "t": self.cycle[ins.id],
                           "site": self.pos[ions[0]], "ions": list(ions)})

    # -- pulses

    def play(self, ops: Sequence[SourceOp], ion: Mapping[int, str]) -> None:
        """The gates' pulses, slot by slot: the k-th pulse of every op in one instruction per
        gate kind, so a layer's gates run together.  Each op's witness names its last pulse."""
        self.batch(ops, ion, lambda: self._play(ops, ion))

    def _play(self, ops: Sequence[SourceOp], ion: Mapping[int, str]) -> None:
        seqs = {op.i: pulses(op, ion) for op in ops}
        last: dict[int, Instruction] = {}
        for k in range(max((len(s) for s in seqs.values()), default=0)):
            by_gate: dict[str, list] = {}
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
            self.witness(op, last[op.i], [ion[q] for q in op.qubits])

    # -- transport

    def gap(self, pairs: Sequence[tuple[str, str]], pos: Mapping[str, str]) -> int:
        return sum(self.lat.dist[pos[a]][pos[b]] - 1 for a, b in pairs)

    def best_step(self, pairs: Sequence[tuple[str, str]]) -> tuple[int, str, list[str]] | None:
        """The one swap-or-stay step that brings the pairs closest: for each diagonal, the
        swaps (and moves into empty legs) taken greedily while each still helps.  Only legs
        holding a crystal of a pair are considered; every other crystal stays."""
        mine = {i for p in pairs for i in p}
        base = self.gap(pairs, self.pos)
        best = None
        for d in self.lat.diagonals:
            pos, at = dict(self.pos), dict(self.at)
            cands = sorted({frozenset((self.pos[i], self.lat.pairs[d][self.pos[i]][1]))
                            for i in mine if self.pos[i] in self.lat.pairs[d]}, key=sorted)
            chosen: list[frozenset] = []
            while True:
                now = self.gap(pairs, pos)
                pick = None
                for e in cands:
                    if e in chosen:
                        continue
                    x, y = sorted(e)
                    a, b = at.get(x), at.get(y)
                    trial = dict(pos)
                    if a is not None:
                        trial[a] = y
                    if b is not None:
                        trial[b] = x
                    gain = now - self.gap(pairs, trial)
                    if gain > 0 and (pick is None or gain > pick[0]):
                        pick = (gain, e)
                if pick is None:
                    break
                e = pick[1]
                x, y = sorted(e)
                a, b = at.pop(x, None), at.pop(y, None)
                if a is not None:
                    pos[a], at[y] = y, a
                if b is not None:
                    pos[b], at[x] = x, b
                chosen.append(e)
            gain = base - self.gap(pairs, pos)
            if chosen and gain > 0 and (best is None or gain > best[0]):
                toward = sorted(self.at[leg] for e in chosen for leg in e if leg in self.at)
                best = (gain, d, toward)
        return best

    def route(self, pairs: Sequence[tuple[str, str]]) -> None:
        """Parallel steps while one helps; each strictly lowers the total gap, so it ends."""
        while True:
            got = self.best_step(pairs)
            if got is None:
                return
            self.step(got[1], got[2], "route")

    def route_one(self, a: str, b: str) -> None:
        """One pair alone, along a shortest path: `a` swaps (or moves) one leg at a time
        until it stands beside `b`, which never moves.  Always arrives."""
        path = self.lat.path(self.pos[a], self.pos[b])
        for x, y in zip(path, path[1:-1]):
            toward = [a] + ([self.at[y]] if y in self.at else [])
            self.step(self.lat.edge[frozenset((x, y))], toward, "route")

    def gate_pairs(self, ops: Sequence[SourceOp], ion: Mapping[int, str]) -> None:
        """Enter, the pulses, exit: per diagonal one enter step (the first operand of each
        pair shifted toward the junction its two legs share) and one exit step (the same
        crystals, the same diagonal: the only exit C1 allows)."""
        groups: dict[str, list[str]] = {}
        for op in ops:
            a, b = ion[op.qubits[0]], ion[op.qubits[1]]
            groups.setdefault(self.lat.edge[frozenset((self.pos[a], self.pos[b]))], []).append(a)
        for d in sorted(groups, key=DIAGONALS.index):
            self.step(d, groups[d], "enter")
        self.play(ops, ion)
        for d in sorted(groups, key=DIAGONALS.index):
            self.step(d, groups[d], "exit")

    def two_qubit(self, ops: Sequence[SourceOp], ion: Mapping[int, str]) -> None:
        pending = list(ops)
        while pending:
            pairs = [(ion[o.qubits[0]], ion[o.qubits[1]]) for o in pending]
            self.route(pairs)
            ready = [o for o, (a, b) in zip(pending, pairs) if self.lat.dist[self.pos[a]][self.pos[b]] == 1]
            if not ready:
                self.route_one(*pairs[0])
                ready = [o for o, (a, b) in zip(pending, pairs)
                         if self.lat.dist[self.pos[a]][self.pos[b]] == 1]
            self.gate_pairs(ready, ion)
            pending = [o for o in pending if o not in ready]


def compile_c2lr(qasm_text: str, arch: Architecture, *, name: str = "circuit",
                 seed: int = 0, stages: list[dict] | None = None) -> tuple[TSIR, dict]:
    """`(programme, certificate)` for `qasm_text` on the C2LR device `arch`, uncooled: the
    certified programme (`compile_with_router` inserts the cooling).  Raises `Refused` for a
    pair it cannot compile, `RouteError` if what it built does not replay cleanly.  A list
    passed as `stages` receives the native stages' evidence (`write_stages` saves it)."""
    if not is_c2lr(arch):
        raise Refused(f"{arch.name} does not declare C2LR control; this router plays only "
                      f"swap-or-stay steps")
    lat = Lattice(arch)
    n, ops, n_clbits = _parse(qasm_text)
    plan = layers(ops)
    at = place(lat, n, plan, seed=seed)
    ion = {q: f"q{q}" for q in range(n)}
    placement = {ion[q]: at[q] for q in range(n)}
    prog = TSIR(name=name, arch_spec=f"arch/{arch.name}.arch.json",
                meta={"compiler": COMPILER_NAME, "qubit_map": {str(q): ion[q] for q in range(n)}})
    b = _Build(arch, lat, prog, dict(placement), stages=stages)
    # the router's normalization is the parse: every op it accepts is one it lowers itself
    circuit = {"n_qubits": n, "n_clbits": n_clbits, "ops": [o.stage_json() for o in ops]}
    b.record("circuit_normalization", circuit, circuit,
             {"source_op_by_normalized_op": [o.i for o in ops]})
    b.record("dependency_analysis", circuit, {"layers": layer_of(ops)}, {})
    b.record("initial_placement", circuit, {"placement": dict(placement)},
             {"logical_to_ion": [ion[q] for q in range(n)]})
    b.emit(type="init", placement=dict(placement), quanta={i: 0.0 for i in placement},
           meta={"compiler": COMPILER_NAME, "circuit": name, "arch": arch.name})
    b.emit(type="cool", broadcast=True, meta={"kind": "state_prep"})
    for layer in plan:
        one = [o for o in layer if o.name in U3]
        if one:
            b.play(one, ion)
        for kind in ("measure", "reset"):
            group = [o for o in layer if o.name == kind]
            if group:
                b.batch(group, ion, lambda kind=kind, group=group: b.emit(
                    type=kind, ions=tuple(ion[o.qubits[0]] for o in group),
                    meta={"kind": "readout" if kind == "measure" else "reset",
                          "op": [o.i for o in group]}))
        two = [o for o in layer if o.name in GATES_2Q]
        if two:
            b.two_qubit(two, ion)
    b.flush()
    cert = {
        "version": 1, "circuit": name, "arch": arch.name, "n_qubits": n,
        "circuit_ops": [o.to_json() for o in ops],
        "map": {str(q): ion[q] for q in range(n)}, "init": dict(placement),
        "moves": b.moves, "gates": b.gates, "rotations": [], "unrealised": [],
        "claims": {"cycles": len(prog.instructions),
                   "beams": sum(len(i.ions) for i in prog.instructions if i.gate == "R"),
                   "ms_gates": sum(len(i.pairs) for i in prog.instructions if i.gate == "MS"),
                   "frames": sum(len(i.ions) for i in prog.instructions if i.gate == "VZ")},
        "compiler": COMPILER_NAME,
    }
    return prog, cert


def write_stages(records: list[dict], prefix, *, circuit, arch, expanded) -> Path:
    """Save the native stages' evidence as `<prefix>.stages.json`, bound by SHA-256 to the
    circuit, the device (as written and expanded) and `<prefix>.tsir.json` it describes."""
    import hashlib

    def sha(p) -> str:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()
    out = Path(str(prefix) + ".stages.json")
    doc = {"schema": "qccd.compiler.stages.v1", "engine": STAGE_ENGINE, "accepted": True,
           "bindings": {"circuit_sha256": sha(circuit), "architecture_sha256": sha(arch),
                        "expanded_architecture_sha256": sha(expanded),
                        "output_tsir_sha256": sha(str(prefix) + ".tsir.json")},
           "stages": records}
    out.write_text(json.dumps(doc), encoding="utf-8")
    return out


def compile_with_stages(work, *, name: str = "circuit") -> int:
    """The staged pipeline's native compile of `work/circuit.qasm` onto `work/device.arch.json`
    (expanded beside it as `device.expanded.json`): writes `prog.tsir.json`, `prog.qcert.json`
    and `prog.stages.json`, as `qccdc compile -o work/prog` does, for the staged driver
    (`qccd.compile.driver`) to accept or reject.  Returns the programme's length."""
    work = Path(work)
    for stale in ("prog.tsir.json", "prog.qcert.json", "prog.stages.json", "prog.native.tsir.json",
                  "prog.cooled.tsir.json", "prog.compilation.json"):
        (work / stale).unlink(missing_ok=True)
    arch = Architecture.from_json(json.loads((work / "device.arch.json").read_text(encoding="utf-8")))
    records: list[dict] = []
    prog, cert = compile_c2lr((work / "circuit.qasm").read_text(encoding="utf-8"), arch, name=name,
                              stages=records)
    prog.save(str(work / "prog.tsir.json"), indent=None)
    (work / "prog.qcert.json").write_text(json.dumps(cert), encoding="utf-8")
    write_stages(records, work / "prog", circuit=work / "circuit.qasm", arch=work / "device.arch.json",
                 expanded=work / "device.expanded.json")
    return len(prog.instructions)


def compile_with_router(qasm_text: str, arch_path, workdir, *, table: str = "qccdsim_jones",
                        name: str = "circuit") -> tuple[Path, Path]:
    """`compile_with_qccdc`'s outputs, from this router: `prog.tsir.json` (certified),
    `prog.qcert.json`, `prog.cooled.tsir.json` (the cooling pass at `table`, the final
    programme), `device.expanded.json`, `circuit.qasm` and `compile.json` in `workdir`.
    Returns (final programme, certificate).  The final programme is checked against every
    rule before it is returned."""
    import subprocess
    import time

    from ..compile.cooling import insert_cooling
    from ..cost.models import corrected_model
    from ..verify import verify

    t0 = time.time()
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    arch_path = Path(arch_path).resolve()
    arch = Architecture.from_json(json.loads(arch_path.read_text(encoding="utf-8")))
    (work / "circuit.qasm").write_text(qasm_text, encoding="utf-8")
    bridge = Path(__file__).resolve().parents[2] / "Compiler" / "bridge"
    ex = subprocess.run([sys.executable, str(bridge / "export_arch.py"), str(arch_path), "-o",
                         str(work / "device.expanded.json")], capture_output=True, text=True)
    if ex.returncode:
        raise RouteError(f"export_arch failed: {(ex.stdout + ex.stderr)[-400:]}")
    prog, cert = compile_c2lr(qasm_text, arch, name=name)
    prog.save(str(work / "prog.tsir.json"), indent=None)
    (work / "prog.qcert.json").write_text(json.dumps(cert), encoding="utf-8")
    model = corrected_model(table)
    cooled = insert_cooling(prog, arch, model).program
    cooled.meta.update(prog.meta)
    final = work / "prog.cooled.tsir.json"
    cooled.save(str(final), indent=None)
    rep = verify(cooled, arch, model, check_metrics=False)
    failed = sorted(rep.rules.summary().get("failed") or [])
    if failed:
        v = next(v for v in rep.rules.violations if v.rule in failed)
        raise RouteError(f"the router's programme fails {failed}: {v.rule}: {v.message}")
    (work / "compile.json").write_text(json.dumps({
        "mode": "c2lr-router", "cool": True, "table": table, "compile_s": round(time.time() - t0, 3),
        "instructions": len(cooled.instructions), "steps": sum(1 for i in cooled.instructions
                                                               if i.type == "simd")}, indent=1),
        encoding="utf-8")
    return final, work / "prog.qcert.json"


# ------------------------------------------------------------------ the contract

def main(argv: list[str] | None = None) -> int:
    """`qccd.compiler@1`: write DIR/program.tsir.json (cooled, `meta.qubit_map`) and
    DIR/certificate.qcert.json; exit 4 for a pair this router does not compile."""
    ap = argparse.ArgumentParser(prog="python -m qccd.compile.c2lr_router", description=__doc__.splitlines()[0])
    ap.add_argument("--circuit", required=True)
    ap.add_argument("--device", required=True)
    ap.add_argument("--expanded", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--table", default="qccdsim_jones", help="the operating-point table cooling is priced at")
    a = ap.parse_args(argv)
    from ..bench.contract import REFUSED_EXIT
    out = Path(a.out)
    try:
        final, cert = compile_with_router(Path(a.circuit).read_text(encoding="utf-8"), a.device,
                                          out / "work", table=a.table)
    except Refused as exc:
        print(f"c2lr-router cannot compile this pair: {exc}", file=sys.stderr)
        return REFUSED_EXIT
    doc = json.loads(final.read_text(encoding="utf-8"))
    (out / "program.tsir.json").write_text(json.dumps(doc), encoding="utf-8")
    (out / "certificate.qcert.json").write_text(cert.read_text(encoding="utf-8"), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
