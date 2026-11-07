"""WISE's compiler: an OpenQASM circuit onto a grid of zones, sorted by parallel switched swaps.

The device is `qccd.arch.wise`'s.  Gate layers and reconfiguration layers alternate (deck S10).
Each layer of the circuit plays as

  1. its one-qubit gates, readouts and resets where the qubits stand, one kind to a layer;
  2. a reconfiguration: its shim charge, then swap steps until each two-qubit gate's qubits stand
     in two neighbouring gate zones;
  3. the gates' first pulses; the merge of each pair into one well, one parity at a time; the
     MS pulse; the split; the gates' last pulses.

THE GATES ARE NEVER REORDERED.  The ops go into layers in program order, so an op is only ever
delayed and ops in one layer share no qubit; only a run of readouts reads out together
(`c2lr_router.layers_reading_together`).

A swap step is one kind of pair and one parity across the whole chip (S4, S5), and the kinds are
`qccd.arch.wise.ctl_classes`'.  The paper's odd-even sort reaches any arrangement in at most
2m + kn steps (S7).  This router takes a real schedule from the same steps rather than that worst
case: at each step, the kind whose swaps bring the layer's qubits closest per unit of time.  Only
the qubits of the layer's gates have somewhere to be, and any other qubit may be displaced.  When
no swap helps, one qubit goes one zone further along its shortest path, displacing what stands
there, which is what ends every deadlock.
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from ..arch import Architecture
from ..arch.wise import Edge, NotWise, Zones
from ..ir.tsir import TSIR, Participant
from .c2lr_router import GATES_2Q, U3, Refused, RouteError, SourceOp, _parse, layers_reading_together, pulses
from .layered import Emitter, certificate

__all__ = ["compile_wise", "compile_with_router", "COMPILER_NAME"]

COMPILER_NAME = "wise-router (baseline)"


def place(z: Zones, n: int, plan: Sequence[Sequence[SourceOp]], *, seed: int = 0, iters: int = 60000) -> dict[int, str]:
    """qubit -> gate zone: annealed so two-qubit partners stand close, each distinct pair once.
    Deterministic for a seed."""
    if n > len(z.sites):
        raise Refused(f"{n} qubits and {len(z.sites)} gate zones: while sorting a zone holds one qubit")
    partners: dict[int, list[int]] = {q: [] for q in range(n)}
    seen: set = set()
    for layer in plan:
        for op in layer:
            if op.name in GATES_2Q:
                pair = frozenset(op.qubits)
                if pair not in seen:
                    seen.add(pair)
                    a, b = op.qubits
                    partners[a].append(b)
                    partners[b].append(a)
    sites = list(z.sites)
    at = {q: sites[q] for q in range(n)}
    held = {s: q for q, s in at.items()}
    rng = random.Random(seed)

    def cost(q: int, s: str) -> int:
        return sum(z.dist[s][at[p]] for p in partners[q])

    temp = 1.5
    for i in range(iters):
        q = rng.randrange(n)
        s = rng.choice(sites)
        here = at[q]
        if s == here:
            continue
        other = held.get(s)
        before = cost(q, here) + (cost(other, s) if other is not None else 0)
        at[q] = s
        if other is not None:
            at[other] = here
        after = cost(q, s) + (cost(other, here) if other is not None else 0)
        t = temp * (1 - i / iters) + 1e-3
        if after <= before or rng.random() < pow(2.718281828, (before - after) / t):
            held[s] = q
            if other is not None:
                held[here] = other
            else:
                del held[here]
        else:
            at[q] = here
            if other is not None:
                at[other] = s
    return at


@dataclass
class _Build:
    z: Zones
    em: Emitter
    ion: Mapping[int, str]
    limit: int = 0
    at: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.at = {s: i for i, s in self.em.pos.items()}
        self.limit = 60 * len(self.z.sites) + 2000

    # -- a swap step: its merge half, then its split half

    def step(self, kind: str, edges: Sequence[Edge]) -> None:
        a, b = [], []
        for e in edges:
            x, y = self.at.get(e.u), self.at.get(e.v)
            if y is not None:
                a.append(Participant(y, e.v, e.u, via=e.in_))
            if x is not None:
                b.append(Participant(x, e.u, e.v, via=e.out))
        # an ion joins its partner, then the partner leaves: between steps a zone holds one
        for half, parts in (("a", a), ("b", b)):
            if parts:
                self.em.emit(type="simd", cls=f"wise_{kind}_{half}", mode="inter", participants=tuple(parts),
                             meta={"kind": "swap_step", "pair_kind": kind, "half": half})
        self.at = {s: i for i, s in self.em.pos.items()}

    # -- a reconfiguration

    def route(self, want: Mapping[str, str]) -> None:
        """Swap steps until each ion of `want` stands in its zone."""
        z = self.z
        pos = self.em.pos
        settled = all(pos[i] == s for i, s in want.items())
        if settled:
            return
        self.settle()
        steps = 0
        while not all(pos[i] == s for i, s in want.items()):
            steps += 1
            if steps > self.limit:
                raise Refused(f"unroutable: {steps} swap steps and {sum(pos[i] != s for i, s in want.items())} "
                              f"qubit(s) still not home")
            best = None
            for kind, edges in sorted(z.edges.items()):
                active, gain = [], 0
                for e in edges:
                    x, y = self.at.get(e.u), self.at.get(e.v)
                    if x is None and y is None:
                        continue
                    g = 0
                    if x in want:
                        g += z.dist[e.u][want[x]] - z.dist[e.v][want[x]]
                    if y in want:
                        g += z.dist[e.v][want[y]] - z.dist[e.u][want[y]]
                    if g > 0:
                        active.append(e)
                        gain += g
                rate = gain / z.cost(kind)
                if active and (best is None or rate > best[0]):
                    best = (rate, kind, active)
            if best is None:
                self._finish(want)
                return
            self.step(best[1], best[2])

    def _finish(self, want: Mapping[str, str]) -> None:
        """What greedy steps leave: one qubit at a time goes all the way home, keeping its turn
        until it arrives, so two qubits cannot push each other back and forth.  Its path avoids
        qubits already home where it can; one it cannot avoid is displaced, and goes home on its
        own turn."""
        z, pos = self.z, self.em.pos
        steps = 0
        while True:
            todo = sorted(i for i in want if pos[i] != want[i])
            if not todo:
                return
            mover = todo[0]
            while pos[mover] != want[mover]:
                steps += 1
                if steps > self.limit:
                    raise Refused(f"unroutable: {steps} swap steps and "
                                  f"{sum(pos[i] != s for i, s in want.items())} qubit(s) still not home")
                home = {want[i] for i in want if i != mover and pos[i] == want[i]}
                nxt = self._next_hop(pos[mover], want[mover], home)
                self.step(*self._edge(pos[mover], nxt))

    def _next_hop(self, here: str, goal: str, home: set[str]) -> str:
        """The first hop of a shortest path that avoids `home`, or of a shortest path if none does."""
        back = {goal: None}
        todo = deque([goal])
        while todo:
            x = todo.popleft()
            for y in self.z.nbrs[x]:
                if y not in back and (y == here or y not in home):
                    back[y] = x
                    todo.append(y)
        if here in back:
            return back[here]
        d = self.z.dist[goal]
        return next(y for y in self.z.nbrs[here] if d[y] == d[here] - 1)

    def _edge(self, a: str, b: str) -> tuple[str, list[Edge]]:
        for kind, edges in sorted(self.z.edges.items()):
            for e in edges:
                if {e.u, e.v} == {a, b}:
                    return kind, [e]
        raise RouteError(f"no step joins {a} and {b}")

    def settle(self) -> None:
        """A reconfiguration opens with its shim charge (S11): a layer with no pulse, whose price
        is the identity's.  It acts on no qubit, and no noise channel charges an identity."""
        first = next(iter(sorted(self.em.pos)))
        self.em.emit(type="gate", gate="I", mode="intra", arity=1, params=((0.0,),), ions=(first,),
                     meta={"kind": "shim_charge"})

    # -- a layer

    def one_qubit(self, ops: Sequence[SourceOp]) -> None:
        if ops:
            self.em.play(ops, self.ion)

    def assign(self, ops: Sequence[SourceOp]) -> tuple[list[tuple[SourceOp, Edge]], list[SourceOp]]:
        """Each gate its pair of neighbouring zones, the nearest first; those that find none wait."""
        z, pos, ion = self.z, self.em.pos, self.ion
        used: set[str] = set()
        got, later = [], []

        def price(op: SourceOp, e: Edge) -> tuple[int, int]:
            a, b = pos[ion[op.qubits[0]]], pos[ion[op.qubits[1]]]
            return min((z.dist[e.u][a] + z.dist[e.v][b], 0), (z.dist[e.v][a] + z.dist[e.u][b], 1))

        order = sorted(ops, key=lambda o: (z.dist[pos[ion[o.qubits[0]]]][pos[ion[o.qubits[1]]]], o.i))
        for op in order:
            best = None
            for e in z.slots:
                if e.u in used or e.v in used:
                    continue
                c = price(op, e)
                if best is None or c < best[0]:
                    best = (c, e)
            if best is None:
                later.append(op)
                continue
            used |= {best[1].u, best[1].v}
            got.append((op, best[1]))
        return got, sorted(later, key=lambda o: o.i)

    def two_qubit(self, ops: Sequence[SourceOp]) -> None:
        pending = list(ops)
        while pending:
            got, later = self.assign(pending)
            if not got:
                raise Refused("no pair of neighbouring gate zones is free for a layer's gate")
            pos, ion = self.em.pos, self.ion
            want: dict[str, str] = {}
            placed: dict[int, tuple[Edge, str, str]] = {}
            for op, e in got:
                a, b = ion[op.qubits[0]], ion[op.qubits[1]]
                d = self.z.dist
                direct = d[e.u][pos[a]] + d[e.v][pos[b]] <= d[e.v][pos[a]] + d[e.u][pos[b]]
                # the ion in `v` moves: it merges into `u`, the zone of the pair's well
                stay, mover = (a, b) if direct else (b, a)
                want[stay], want[mover] = e.u, e.v
                placed[op.i] = (e, stay, mover)
            self.route(want)
            mine = [op for op, _ in got]
            seqs = {op.i: pulses(op, ion) for op in mine}
            first = {i: next(k for k, p in enumerate(s) if p[0] == "MS") for i, s in seqs.items()}
            self.em.play(mine, ion, {i: s[:first[i]] for i, s in seqs.items()}, witness=False)
            for kind in ("h0", "h1"):
                parts = [Participant(m, e.v, e.u) for op in mine for e, _, m in [placed[op.i]] if e.kind == kind]
                if parts:
                    self.em.emit(type="simd", cls=f"wise_{kind}_a", mode="inter", participants=tuple(parts),
                                 meta={"kind": "merge_for_gate", "pair_kind": kind, "half": "a"})
            ms = self.em.pulse_group("MS", [(seqs[op.i][first[op.i]][1], seqs[op.i][first[op.i]][2]) for op in mine],
                                     [op.i for op in mine])
            for op in mine:
                self.em.witness(op, ms, [ion[q] for q in op.qubits])
            for kind in ("h0", "h1"):
                parts = [Participant(m, e.u, e.v) for op in mine for e, _, m in [placed[op.i]] if e.kind == kind]
                if parts:
                    self.em.emit(type="simd", cls=f"wise_{kind}_b", mode="inter", participants=tuple(parts),
                                 meta={"kind": "split_after_gate", "pair_kind": kind, "half": "b"})
            self.at = {s: i for i, s in self.em.pos.items()}
            self.em.play(mine, ion, {i: s[first[i] + 1:] for i, s in seqs.items()}, witness=False)
            pending = later


def compile_wise(qasm_text: str, arch: Architecture, *, name: str = "circuit", seed: int = 0) -> tuple[TSIR, dict]:
    """`(programme, certificate)` for `qasm_text` on the chip `arch`, uncooled."""
    try:
        z = Zones(arch)
    except NotWise as exc:
        raise Refused(str(exc)) from None
    n, ops, _ = _parse(qasm_text)
    plan = layers_reading_together(ops)
    at = place(z, n, plan, seed=seed)
    ion = {q: f"q{q}" for q in range(n)}
    placement = {ion[q]: at[q] for q in range(n)}
    prog = TSIR(name=name, arch_spec=f"arch/{arch.name}.arch.json",
                meta={"compiler": COMPILER_NAME, "qubit_map": {str(q): ion[q] for q in range(n)}})
    em = Emitter(prog, dict(placement), one_kind=True)
    em.emit(type="init", placement=dict(placement), quanta={i: 0.0 for i in placement},
            meta={"compiler": COMPILER_NAME, "circuit": name, "arch": arch.name})
    em.emit(type="cool", broadcast=True, meta={"kind": "state_prep"})
    b = _Build(z, em, ion)
    for layer in plan:
        b.one_qubit([o for o in layer if o.name in U3])
        for kind in ("measure", "reset"):
            group = [o for o in layer if o.name == kind]
            if group:
                em.emit(type=kind, ions=tuple(ion[o.qubits[0]] for o in group),
                        meta={"kind": "readout" if kind == "measure" else "reset", "op": [o.i for o in group]})
        b.two_qubit([o for o in layer if o.name in GATES_2Q])
    return prog, certificate(prog, ops, name, arch.name, n, ion, placement, em, COMPILER_NAME)


def compile_with_router(qasm_text: str, arch_path, workdir, *, table: str = "helios",
                        name: str = "circuit") -> tuple[Path, Path]:
    """`compile_with_qccdc`'s outputs from this router; returns (final programme, certificate)."""
    from ..compile.cooling import insert_cooling
    from ..cost.models import corrected_model
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
    prog, cert = compile_wise(qasm_text, arch, name=name)
    prog.save(str(work / "prog.tsir.json"), indent=None)
    (work / "prog.qcert.json").write_text(json.dumps(cert), encoding="utf-8")
    cooled = insert_cooling(prog, arch, corrected_model(table)).program
    cooled.meta.update(prog.meta)
    final = work / "prog.cooled.tsir.json"
    cooled.save(str(final), indent=None)
    (work / "compile.json").write_text(json.dumps({"mode": "wise-router", "compiler": COMPILER_NAME,
                                                   "seconds": round(time.time() - t0, 3)}), encoding="utf-8")
    return final, work / "prog.qcert.json"
