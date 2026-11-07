"""Cyclone's compiler: an OpenQASM circuit onto a ring of traps, in the circuit's own order.

The ring (`qccd.arch.cyclone`) keeps its data qubits still and rotates its ancillas in lockstep,
one trap per step, one way round (deck S1-S4).  So compiling is deciding where the data stands and
where each ancilla starts; after that the rotation says when a gate can run: ancilla a, starting
in trap s, stands in trap (s + t) mod x at step t, and its gate with a data qubit in trap p runs
at a step with t = p - s mod x.

THE GATES ARE NEVER REORDERED.  Each op runs at the first step that is no earlier than the last
op on any of its qubits and at which its qubits stand together; an ancilla that passes a data
qubit it may not touch yet comes round again.  Within a step a trap runs its ops one at a time, in
circuit order (S5), and different traps run together.  Cyclone's paper assigns stabilizers on the
fly, every X check in one rotation and every Z check in the next (S6); that reorders the circuit,
so here a round takes as many rotations as the circuit's order needs.

A step is the trap's gates, then its rotation: every ancilla splits, moves and merges into the next
trap (S4), the ancillas of one trap in turn.  The ring keeps rotating through a step with nothing
to do (S3).

The ions of a program with `n_data` data qubits are the circuit's first `n_data` qubits, as Goal
3's circuits number them.  An operation between two qubits that never stand together, two
ancillas in different traps say, is refused by name.
"""

from __future__ import annotations

import json
import math
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import Mapping, Sequence

from ..arch import Architecture
from ..ir.tsir import TSIR, Participant
from ..verify.replay import bend_vias
from .c2lr_router import GATES_2Q, U3, Refused, RouteError, SourceOp, _parse
from .layered import Emitter, certificate

__all__ = ["Plan", "plan_times", "place", "compile_cyclone", "compile_with_router", "COMPILER_NAME"]

COMPILER_NAME = "cyclone-router (baseline)"
CLASS = "cyclone_shift"


class Plan:
    """Where everything stands: data qubit -> trap, ancilla qubit -> the trap it starts in."""

    def __init__(self, x: int, data: dict[int, int], start: dict[int, int]):
        self.x, self.data, self.start = x, data, start

    def trap(self, q: int, t: int) -> int:
        return self.data[q] if q in self.data else (self.start[q] + t) % self.x


def plan_times(ops: Sequence[SourceOp], plan: Plan) -> list[int]:
    """The step each op runs at, gate by gate in the circuit's order, each at the first step no
    earlier than its qubits' last op at which they stand together.  A barrier's qubits leave it
    together.  Refuses two ancillas that never meet, or two data qubits in different traps."""
    x = plan.x
    last: dict[int, int] = {}
    data, start = plan.data, plan.start
    out = []
    for op in ops:
        t = max((last.get(q, 0) for q in op.qubits), default=0)
        if op.name in GATES_2Q:
            a, b = op.qubits
            if a in data and b in data:
                if data[a] != data[b]:
                    raise Refused(f"op {op.i}: data qubits {a} and {b} never stand together: a data "
                                  f"qubit never moves")
            elif a in data or b in data:
                d, anc = (a, b) if a in data else (b, a)
                t += (data[d] - start[anc] - t) % x
            elif start[a] != start[b]:
                raise Refused(f"op {op.i}: ancillas {a} and {b} rotate rigidly and start in "
                              f"different traps, so they never stand together")
        for q in op.qubits:
            last[q] = t
        out.append(t)
    return out


def _first_round(ops: Sequence[SourceOp]) -> list[SourceOp]:
    """The ops up to the first readout: one syndrome round, what placement is tuned on."""
    for k, op in enumerate(ops):
        if op.name == "measure":
            return list(ops[:k])
    return list(ops)


def place(ops: Sequence[SourceOp], n: int, n_data: int, x: int, capacity: int, *, seed: int = 0,
          iters: int = 4000) -> Plan:
    """Data traps and ancilla starts, annealed to the fewest steps over the first round.  Starts
    from the data in circuit order and the ancillas evenly round the ring.  Capacity holds at
    every rotation: a trap's data plus the most ancillas any trap ever carries."""
    ancillas = list(range(n_data, n))
    per_anc = math.ceil(len(ancillas) / x) if ancillas else 0
    room = capacity - per_anc
    if room < 0 or room * x < n_data:
        raise Refused(f"{x} traps of {capacity} ions hold {n_data} data qubits and "
                      f"{len(ancillas)} ancillas only with {per_anc} ancilla(s) to a trap and "
                      f"{math.ceil(n_data / x)} data qubits: {capacity} is too few")
    data = {q: min(q * x // max(n_data, 1), x - 1) for q in range(n_data)}
    start = {a: i * x // max(len(ancillas), 1) for i, a in enumerate(ancillas)}
    load = [0] * x
    for q, t in data.items():
        load[t] += 1
    # an even split can exceed `room` only by rounding: move the excess on
    for t in range(x):
        while load[t] > room:
            spare = next(u for u in range(x) if load[u] < room)
            q = next(q for q, v in data.items() if v == t)
            data[q], load[t], load[spare] = spare, load[t] - 1, load[spare] + 1
    # ancillas that gate each other must start together
    for op in ops:
        if op.name in GATES_2Q and all(q >= n_data for q in op.qubits):
            start[op.qubits[1]] = start[op.qubits[0]]
    tied = {q for op in ops if op.name in GATES_2Q and all(q >= n_data for q in op.qubits) for q in op.qubits}
    round_ops = _first_round(ops)

    def cost(pl: Plan) -> int:
        return plan_times(round_ops, pl)[-1] if round_ops else 0

    best = Plan(x, dict(data), dict(start))
    if not round_ops or x < 2:
        return best
    best_c = cost(best)
    rng = random.Random(seed)
    cur, cur_c = Plan(x, dict(data), dict(start)), best_c
    free = [a for a in ancillas if a not in tied]
    for i in range(iters):
        temp = max(0.01, 2.0 * (1 - i / iters))
        if free and (rng.random() < 0.5 or n_data < 2):
            a, b = rng.choice(free), rng.choice(free)
            if a == b:
                continue
            cur.start[a], cur.start[b] = cur.start[b], cur.start[a]
            swap = ("s", a, b)
        else:
            a, b = rng.choice(range(n_data)), rng.choice(range(n_data))
            if a == b or cur.data[a] == cur.data[b]:
                continue
            cur.data[a], cur.data[b] = cur.data[b], cur.data[a]
            swap = ("d", a, b)
        c = cost(cur)
        if c <= cur_c or rng.random() < math.exp((cur_c - c) / temp):
            cur_c = c
            if c < best_c:
                best, best_c = Plan(x, dict(cur.data), dict(cur.start)), c
        else:
            table = cur.start if swap[0] == "s" else cur.data
            table[swap[1]], table[swap[2]] = table[swap[2]], table[swap[1]]
    return best


def compile_cyclone(qasm_text: str, arch: Architecture, n_data: int, *, name: str = "circuit",
                    seed: int = 0) -> tuple[TSIR, dict]:
    """`(programme, certificate)` for `qasm_text` on the ring `arch`, uncooled."""
    dev = arch.device
    if not dev.loops:
        raise Refused(f"{arch.name} has no loop; Cyclone rotates its ancillas round one")
    loop = next(iter(dev.loops))
    traps = list(dev.loop_slots(loop))
    x = len(traps)
    capacity = min(dev.nodes[t].capacity for t in traps)
    n, ops, _ = _parse(qasm_text)
    if not 0 < n_data <= n:
        raise Refused(f"{n_data} data qubits of {n}")
    pl = place(ops, n, n_data, x, capacity, seed=seed)
    times = plan_times(ops, pl)
    ion = {q: f"q{q}" for q in range(n)}
    placement = {ion[q]: traps[pl.trap(q, 0)] for q in range(n)}
    prog = TSIR(name=name, arch_spec=f"arch/{arch.name}.arch.json",
                meta={"compiler": COMPILER_NAME, "qubit_map": {str(q): ion[q] for q in range(n)},
                      "data_ions": [ion[q] for q in range(n_data)]})
    em = Emitter(prog, dict(placement))
    em.emit(type="init", placement=dict(placement), quanta={i: 0.0 for i in placement},
            meta={"compiler": COMPILER_NAME, "circuit": name, "arch": arch.name})
    em.emit(type="cool", broadcast=True, meta={"kind": "state_prep"})
    ahead = bend_vias(dev, loop, 1)
    by_step: dict[int, list[SourceOp]] = {}
    for op, t in zip(ops, times):
        if op.name != "barrier":
            by_step.setdefault(t, []).append(op)
    last_step = max(by_step, default=0)
    ancillas = [ion[q] for q in range(n_data, n)]
    for t in range(last_step + 1):
        _run_step(em, by_step.get(t, ()), pl, t, ion)
        if t < last_step:
            # a trap does one thing at a time and a rail holds one ion (S5, HW3), so the ancillas
            # of one trap leave in turn: the j-th of every trap goes together
            by_trap: dict[str, list[str]] = {}
            for a in ancillas:
                by_trap.setdefault(em.pos[a], []).append(a)
            for j in range(max((len(v) for v in by_trap.values()), default=0)):
                parts = []
                for here, group in by_trap.items():
                    if j < len(group):
                        nxt = traps[(traps.index(here) + 1) % x]
                        parts.append(Participant(group[j], here, nxt, via=tuple(ahead.get(here, ()))))
                em.emit(type="simd", cls=CLASS, mode="inter", participants=tuple(parts),
                        meta={"kind": "rotate", "step": t, "gate_swaps": True})
    return prog, certificate(prog, ops, name, arch.name, n, ion, placement, em, COMPILER_NAME)


def _run_step(em: Emitter, ops: Sequence[SourceOp], pl: Plan, t: int, ion: Mapping[int, str]) -> None:
    """One step's ops: a trap runs one at a time, in circuit order; traps run together."""
    slots: list[list[SourceOp]] = []
    used: dict[int, int] = {}
    for op in ops:
        trap = pl.trap(op.qubits[0], t)
        k = used.get(trap, 0)
        used[trap] = k + 1
        while len(slots) <= k:
            slots.append([])
        slots[k].append(op)
    for slot in slots:
        gates = [o for o in slot if o.name in U3 or o.name in GATES_2Q]
        if gates:
            em.play(gates, ion)
        for kind in ("measure", "reset"):
            group = [o for o in slot if o.name == kind]
            if group:
                em.emit(type=kind, ions=tuple(ion[o.qubits[0]] for o in group),
                        meta={"kind": "readout" if kind == "measure" else "reset", "op": [o.i for o in group]})


def compile_with_router(qasm_text: str, arch_path, workdir, *, n_data: int, table: str = "helios",
                        name: str = "circuit") -> tuple[Path, Path]:
    """`compile_with_qccdc`'s outputs from this router; returns (final programme, certificate).
    The cooling pass runs as for any device: nothing heats here, so it adds nothing."""
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
    prog, cert = compile_cyclone(qasm_text, arch, n_data, name=name)
    prog.save(str(work / "prog.tsir.json"), indent=None)
    (work / "prog.qcert.json").write_text(json.dumps(cert), encoding="utf-8")
    cooled = insert_cooling(prog, arch, corrected_model(table)).program
    cooled.meta.update(prog.meta)
    final = work / "prog.cooled.tsir.json"
    cooled.save(str(final), indent=None)
    (work / "compile.json").write_text(json.dumps({"mode": "cyclone-router", "compiler": COMPILER_NAME,
                                                   "seconds": round(time.time() - t0, 3)}), encoding="utf-8")
    return final, work / "prog.qcert.json"
