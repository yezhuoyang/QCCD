"""The conveyor sandwich's compiler: an OpenQASM circuit onto data traps between two loops.

The machine (`qccd.arch.sandwich`) keeps its data qubits still, two to a trap or one, and
carries its ancillas on conveyor loops with one slot facing every data trap.  So compiling is
deciding where each data qubit stands and which slot each ancilla starts in.  After that a
two-qubit gate is three movements, each ONE waveform however many ancillas take part:

    rotate   the ancilla's loop turns until the ancilla faces its partner's trap;
    descend  every ancilla with a gate to play hops from its slot into the trap it faces;
    ascend   and hops back.

The gates between the two hops are qccdc's native pulses (`c2lr_router.pulses`), and the
programme is written with its certificate (`qccd.compile.layered`) like any other compiler's.

THE GATES ARE NEVER REORDERED.  An op runs only when it is the next op of every qubit it
touches; an ancilla whose partner has an earlier gate to play waits.

ONE WAVEFORM, OR ONE A LAYER.  On a sandwich built in layers each layer has a broadcast of
its own (docs/PLAN-3d.md), so the two loops turn in the same cycle and a hop on one side of
the data runs beside a rotation on the other.  On the same machine drawn in one plane there
is one broadcast: the loops take turns, and a hop from the top row is a different motion in
the lab frame from a hop from the bottom row.  `per_layer` says which, and is read off the
machine when not given; the cycles this router emits are grouped by the verifier's own
motion labels (`qccd.verify.rules.hop_label`), so what it packs into a cycle is what R22
accepts.

What it refuses, by name: a two-qubit gate between two data qubits or two ancillas (they
never stand together), more ancillas on a loop than it has slots, and a data trap too small
for its data and one visitor.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from pathlib import Path
from typing import Mapping, Sequence

from ..arch import Architecture
from ..arch.sandwich import CONVEYOR, HOP, sandwich_roles
from ..ir.tsir import TSIR, Participant
from ..verify.replay import bend_vias
from ..verify.rules import hop_label, loop_node_set
from .c2lr_router import GATES_2Q, U3, Refused, RouteError, SourceOp, _parse
from .layered import Emitter, certificate

__all__ = ["compile_conveyor", "compile_with_router", "COMPILER_NAME", "Plan", "place",
           "lattice_rows"]

COMPILER_NAME = "conveyor-router"

#: what a rotation step and a whole visit (descend, cool, gate, ascend) cost, in the units a
#: target is chosen by: a visit is worth about six steps under the qccdsim_jones table
_STEP, _VISIT = 100.0, 610.0


class Plan:
    """Where everything stands: data qubit -> trap position; ancilla -> (loop, start slot)."""

    def __init__(self, positions: int, data: dict[int, int], loop: dict[int, int],
                 slot: dict[int, int]):
        self.positions, self.data, self.loop, self.slot = positions, data, loop, slot

    def offset(self, anc: int, dat: int) -> int:
        """How far the ancilla's loop must be turned for it to face the data qubit's trap."""
        return (self.data[dat] - self.slot[anc]) % self.positions


def _signed(delta: int, n: int) -> int:
    """`delta mod n`, as the shortest way round: in (-n/2, n/2]."""
    d = delta % n
    return d - n if d > n // 2 else d


def _windows(ops: Sequence[SourceOp], n_data: int) -> tuple[int, dict[int, list[tuple[int, int, int]]]]:
    """The first round's two-qubit gates, ancilla by ancilla in the circuit's order:
    `(layers, {ancilla: [(earliest, latest, data qubit), ...]})`.

    A round ends where an ancilla that has played a gate is read out or reset.  A gate's
    earliest layer is as soon as both its qubits are free, its latest as late as the gates
    after it allow: a check of full weight has no choice, a check on the boundary has."""
    gates: list[tuple[int, int]] = []
    seen: set[int] = set()
    for op in ops:
        if op.name in GATES_2Q:
            x, y = op.qubits
            anc, dat = (x, y) if x >= n_data else (y, x)
            gates.append((anc, dat))
            seen.add(anc)
        elif op.name in ("measure", "reset") and op.qubits[0] in seen:
            break
    depth: dict[int, int] = {}
    early = []
    for anc, dat in gates:
        t = max(depth.get(anc, 0), depth.get(dat, 0))
        depth[anc] = depth[dat] = t + 1
        early.append(t)
    layers = max(early, default=-1) + 1
    room: dict[int, int] = {}
    late = [0] * len(gates)
    for i in range(len(gates) - 1, -1, -1):
        anc, dat = gates[i]
        t = min(room.get(anc, layers - 1), room.get(dat, layers - 1))
        room[anc] = room[dat] = t - 1
        late[i] = t
    out: dict[int, list[tuple[int, int, int]]] = {}
    for (anc, dat), lo, hi in zip(gates, early, late):
        out.setdefault(anc, []).append((lo, hi, dat))
    return layers, out


def _families(ops: Sequence[SourceOp], n: int, n_data: int, n_loops: int,
              positions: int) -> list[list[int]]:
    """Which ancillas ride which loop.  Ancillas that play the same part in their gates (the
    control of every CX: an X check; the target: a Z check) move alike, so they share a
    loop; a circuit whose ancillas do not fall into two such kinds is split down the middle."""
    ancillas = list(range(n_data, n))
    if n_loops == 1 or not ancillas:
        return [ancillas]
    part: dict[int, tuple] = {}
    for op in ops:
        if op.name in GATES_2Q:
            anc = op.qubits[0] if op.qubits[0] >= n_data else op.qubits[1]
            part.setdefault(anc, (op.name, op.qubits.index(anc)))
    kinds = sorted({part.get(q, ()) for q in ancillas})
    if len(kinds) == 2:
        groups = [[q for q in ancillas if part.get(q, ()) == k] for k in kinds]
        if all(0 < len(g) <= positions for g in groups):
            return sorted(groups, key=lambda g: g[0])
    half = math.ceil(len(ancillas) / 2)
    return [ancillas[:half], ancillas[half:]]


def place(ops: Sequence[SourceOp], n: int, n_data: int, positions: int, n_loops: int, *,
          align: str = "index", data_at: Mapping[int, int] | None = None) -> Plan:
    """Where the data stand and which slot each ancilla starts in.

    Data qubit q stands in trap `q mod positions`: the placement a code's own numbering asks
    for.  (With a trap for every data qubit and room to spare, the data are spread at the
    stride that puts the k-th ancilla's slot over the k-th data qubit of each block.)
    `data_at` places them instead: data qubit -> trap position, for a code whose lattice the
    caller knows (`lattice_rows`).

    `align` chooses the ancillas' slots:

    * `"index"`: the k-th ancilla of a loop in the k-th slot.  For a code whose checks are
      translations of one another -- a bivariate bicycle code, numbered `6 i + j` on a
      12 x 6 torus -- one loop offset then serves a whole layer of gates, or two where the
      translation wraps.
    * `"vote"`: the heaviest check sets the loop's offset in every layer of gates it takes
      part in, and each other ancilla takes the free slot from which the most of its gates
      meet their data at a layer's offset, in a layer the circuit's order allows that gate
      (`_windows`).  This finds the same structure when the circuit's numbering of the
      ancillas hides it, and seats a check on a code's boundary beside the checks of the bulk.
    """
    fams = _families(ops, n, n_data, n_loops, positions)
    biggest = max((len(f) for f in fams), default=0)
    if biggest > positions:
        raise Refused(f"{n - n_data} ancillas on {n_loops} loop(s) of {positions} slots: a "
                      f"slot carries one ancilla")
    stride = 1
    if data_at is not None:
        data = {q: int(data_at[q]) % positions for q in range(n_data)}
    elif biggest and n_data <= positions and n_data % biggest == 0 \
            and 1 < n_data // biggest <= positions // biggest:
        stride = positions // biggest
        data = {q: (q % biggest) * stride + q // biggest for q in range(n_data)}
    else:
        data = {q: q % positions for q in range(n_data)}
    loop = {q: g for g, fam in enumerate(fams) for q in fam}
    slot: dict[int, int] = {}
    if align == "index":
        for fam in fams:
            for i, q in enumerate(fam):
                slot[q] = (i * stride) % positions
    elif align == "vote":
        _, windows = _windows(ops, n_data)

        def fit(mine, at: int, offset: Mapping[int, int]) -> list[int | None]:
            """The layer each of an ancilla's gates plays in from slot `at`: the earliest in
            its window, after the gate before, whose offset is the one it needs."""
            out, after = [], -1
            for lo, hi, dq in mine:
                need = (data[dq] - at) % positions
                t = next((t for t in range(max(lo, after + 1), hi + 1) if offset.get(t) == need), None)
                out.append(t)
                after = t if t is not None else after
            return out

        for fam in fams:
            offset: dict[int, int] = {}                      # layer -> the loop's offset in it
            free = set(range(positions))
            order = sorted(fam, key=lambda q: (-len(windows.get(q, ())),
                                               sum(hi - lo for lo, hi, _ in windows.get(q, ())), q))
            for q in order:
                mine = windows.get(q, [])
                cands = {(data[dq] - offset[t]) % positions
                         for lo, hi, dq in mine for t in range(lo, hi + 1) if t in offset} & free
                score = lambda at: sum(t is not None for t in fit(mine, at, offset))
                pick = max(cands, key=lambda at: (score(at), -at), default=None)
                if pick is None:                              # the first, or all its slots taken
                    want = data[mine[0][2]] if mine else 0
                    pick = min(free, key=lambda at: (abs(_signed(at - want, positions)), at))
                slot[q] = pick
                free.discard(pick)
                # a gate no layer serves yet names its own: the earliest free one in its window
                played = fit(mine, pick, offset)
                after = -1
                for (lo, hi, dq), t in zip(mine, played):
                    if t is None:
                        t = next((t for t in range(max(lo, after + 1), hi + 1) if t not in offset), None)
                        if t is not None:
                            offset[t] = (data[dq] - pick) % positions
                    after = t if t is not None else after
    else:
        raise ValueError(f"align is 'index' or 'vote', not {align!r}")
    return Plan(positions, data, loop, slot)


def lattice_rows(rows: int, cols: int, *, spare: int = 1) -> tuple[int, dict[int, int]]:
    """A `rows x cols` lattice of data qubits, numbered row by row, laid along the ring with
    `spare` empty traps after every row: `(positions, data_at)`.

    A check on a plaquette then meets its four corners at the same four offsets wherever the
    plaquette is, and a check on the boundary finds a slot of its own in the spare column:
    a surface code's round is one loop offset a layer of gates, whatever its distance."""
    stride = cols + spare
    positions = rows * stride
    return positions + positions % 2, {r * cols + c: r * stride + c for r in range(rows) for c in range(cols)}


class _Run:
    """One compilation: the schedule's state, and the cycles it has decided to emit."""

    def __init__(self, arch: Architecture, ops: Sequence[SourceOp], n: int, n_data: int,
                 plan: Plan, em: Emitter, ion: Mapping[int, str], per_layer: bool,
                 wait: float = 0.0):
        self.arch, self.dev = arch, arch.device
        self.roles = sandwich_roles(arch)
        self.ops, self.n, self.n_data, self.plan = list(ops), n, n_data, plan
        self.em, self.ion, self.per_layer, self.wait = em, ion, per_layer, wait
        self.loops = self.roles["loops"]
        self.traps = self.roles["data"]
        self.P = self.roles["positions"]
        self.on_loop = loop_node_set(self.dev)
        self.ahead = {(g, s): bend_vias(self.dev, lp["id"], s)
                      for g, lp in enumerate(self.loops) for s in (1, -1)}
        # every qubit's ops, in the circuit's order
        self.queue: dict[int, list[int]] = {q: [] for q in range(n)}
        for k, op in enumerate(self.ops):
            for q in op.qubits:
                self.queue[q].append(k)
        self.head = {q: 0 for q in range(n)}
        self.left = sum(1 for op in self.ops if op.name != "barrier")
        self.done = [op.name == "barrier" for op in self.ops]
        for q in range(n):
            self._skip(q)
        g_n = len(self.loops)
        self.rot = [0] * g_n
        self.phase = ["idle"] * g_n
        self.target = [0] * g_n
        self.visit: list[list[int]] = [[] for _ in range(g_n)]   # the ops a loop's ancillas are down for
        self.visitor: dict[int, set[int]] = {}                   # trap position -> the loops visiting it
        # how many visitors a trap has room for: its capacity less the data that live in it
        held: dict[int, int] = {}
        for q in range(n_data):
            held[plan.data[q]] = held.get(plan.data[q], 0) + 1
        self.room = {k: self.dev.nodes[t].capacity - held.get(k, 0) for k, t in enumerate(self.roles["data"])}
        self.riders = [[a for a in range(n_data, n) if plan.loop[a] == g] for g in range(g_n)]

    # ---------------------------------------------------------------- the circuit's order
    def _skip(self, q: int) -> None:
        while self.head[q] < len(self.queue[q]) and self.done[self.queue[q][self.head[q]]]:
            self.head[q] += 1

    def _ready(self, k: int) -> bool:
        return all(self.head[q] < len(self.queue[q]) and self.queue[q][self.head[q]] == k
                   for q in self.ops[k].qubits)

    def _finish(self, ks: Sequence[int]) -> None:
        for k in ks:
            self.done[k] = True
            self.left -= 1
        for k in ks:
            for q in self.ops[k].qubits:
                self._skip(q)

    def _frontier(self) -> list[int]:
        out = {self.queue[q][self.head[q]] for q in range(self.n) if self.head[q] < len(self.queue[q])}
        return sorted(k for k in out if self._ready(k))

    def _pair(self, k: int) -> tuple[int, int]:
        a, b = self.ops[k].qubits
        return (a, b) if a >= self.n_data else (b, a)

    # ---------------------------------------------------------------- where things are
    def _slot_node(self, anc: int, turn: int = 0) -> str:
        g = self.plan.loop[anc]
        return self.loops[g]["slots"][(self.plan.slot[anc] + self.rot[g] + turn) % self.P]

    def _down(self, anc: int) -> bool:
        return self.em.pos[self.ion[anc]] in self._trap_set

    # ---------------------------------------------------------------- one tick of transport
    def _choose(self, g: int, front: Sequence[int]) -> None:
        """The loop's next offset: the one that plays the most ready gates for the turning
        it takes, a visit counted as `_VISIT / _STEP` steps."""
        by: dict[int, int] = {}
        for k in front:
            if self.ops[k].name in GATES_2Q:
                anc, dat = self._pair(k)
                if self.plan.loop[anc] == g:
                    o = self.plan.offset(anc, dat)
                    by[o] = by.get(o, 0) + 1
        if not by:
            return
        cost = lambda o: (abs(_signed(o - self.rot[g], self.P)) * _STEP + _VISIT) / by[o]
        self.target[g] = min(by, key=lambda o: (cost(o), abs(_signed(o - self.rot[g], self.P)), o))
        self.phase[g] = "rot"

    def _units(self, front: Sequence[int]) -> list[tuple[str, int, list[Participant]]]:
        """What each loop does this tick: `(kind, loop, moves)`, kinds in the order they are
        packed -- ascents first (they free traps), then rotations, then descents."""
        ups, rots, downs = [], [], []
        freed: dict[int, int] = {}
        for g in range(len(self.loops)):
            if self.phase[g] == "up":
                moves = []
                for k in self.visit[g]:
                    anc, dat = self._pair(k)
                    moves.append(Participant(self.ion[anc], self.traps[self.plan.data[dat]],
                                             self._slot_node(anc)))
                    freed[self.plan.data[dat]] = freed.get(self.plan.data[dat], 0) + 1
                ups.append(("up", g, moves))
        claimed: dict[int, int] = {}
        for g in range(len(self.loops)):
            if self.phase[g] != "rot":
                continue
            step = _signed(self.target[g] - self.rot[g], self.P)
            if step:
                s = 1 if step > 0 else -1
                ahead = self.ahead[(g, s)]
                moves = []
                for a in self.riders[g]:
                    here = self._slot_node(a)
                    moves.append(Participant(self.ion[a], here, self._slot_node(a, s),
                                             via=tuple(ahead.get(here, ()))))
                rots.append(("rot", g, moves, s))
                continue
            picked, moves, mine = [], [], set()
            for k in front:
                if self.ops[k].name not in GATES_2Q:
                    continue
                anc, dat = self._pair(k)
                pos = self.plan.data[dat]
                if self.plan.loop[anc] != g or self.plan.offset(anc, dat) != self.rot[g]:
                    continue
                # a trap takes the visitors it has room for, and one from a loop at a time
                here = len(self.visitor.get(pos, ())) - freed.get(pos, 0) + claimed.get(pos, 0)
                if here >= self.room[pos] or pos in mine:
                    continue
                mine.add(pos)
                picked.append(k)
                moves.append(Participant(self.ion[anc], self._slot_node(anc), self.traps[pos]))
            if picked and self._wait_for_another(g):
                continue                      # one cooling will serve both loops' gates
            if picked:
                for pos in mine:
                    claimed[pos] = claimed.get(pos, 0) + 1
                downs.append(("down", g, moves, picked))
            elif not any(self.plan.loop[self._pair(k)[0]] == g and
                         self.plan.offset(*self._pair(k)) == self.rot[g]
                         for k in front if self.ops[k].name in GATES_2Q):
                self.phase[g] = "idle"        # nothing left at this offset: choose again
        return ups + rots + downs

    def _wait_for_another(self, g: int) -> bool:
        """Whether loop `g`, ready to descend, holds for a loop still turning towards its own
        visit, when that loop is at most `wait` steps away.  Gates played after one descent
        share one cooling; the price is that the two loops' hops then fall in step, and no
        longer overlap each other's turning."""
        for h in range(len(self.loops)):
            if h != g and self.phase[h] == "rot":
                away = abs(_signed(self.target[h] - self.rot[h], self.P))
                if 0 < away <= self.wait:
                    return True
        return False

    def _label(self, p: Participant) -> str:
        seg = self.dev.segment_between(p.src, p.dst) if not p.via else self.dev.segments[p.via[-1]]
        return hop_label(self.dev, self.on_loop, p.src, p.dst, seg)

    def _pack(self, units) -> list[tuple[bool, list[Participant]]]:
        """The tick's moves in as few cycles as one waveform a layer (or one waveform)
        allows.  A unit is split by the verifier's own label for each hop, and a group joins
        the first cycle in which every layer it touches is free or already plays its label."""
        groups: list[tuple[str, frozenset, list[Participant], bool]] = []
        for unit in units:
            kind, g, moves = unit[0], unit[1], unit[2]
            if kind == "rot":
                label = f"{self.loops[g]['id']}:{unit[3]:+d}"
                layers = frozenset({self.dev.layer_of(moves[0].src)}) if moves else frozenset()
                groups.append((label, layers, list(moves), True))
                continue
            by: dict[str, list[Participant]] = {}
            for p in moves:
                by.setdefault(self._label(p), []).append(p)
            for label in sorted(by):
                layers = frozenset(k for p in by[label]
                                   for k in (self.dev.layer_of(p.src), self.dev.layer_of(p.dst)))
                groups.append((label, layers, by[label], False))
        # [what each layer plays, the moves, whether every move is a loop's rotation]
        cycles: list[list] = []
        for label, layers, moves, turning in groups:
            if not moves:
                continue
            keys = layers if self.per_layer else frozenset({0})
            for cyc in cycles:
                if all(cyc[0].get(k, label) == label for k in keys):
                    for k in keys:
                        cyc[0][k] = label
                    cyc[1].extend(moves)
                    cyc[2] = cyc[2] and turning
                    break
            else:
                cycles.append([{k: label for k in keys}, list(moves), turning])
        return [(cyc[2], cyc[1]) for cyc in cycles]

    def _tick(self) -> bool:
        """One round of decisions: every loop does its next thing.  False when nothing moved
        and nothing was played."""
        acted = False
        # everything that needs no meeting: one-qubit gates, readout, reset
        while True:
            front = self._frontier()
            solo = [k for k in front if self.ops[k].name not in GATES_2Q
                    and not any(q >= self.n_data and self._down(q) for q in self.ops[k].qubits)]
            if not solo:
                break
            gates = [self.ops[k] for k in solo if self.ops[k].name in U3]
            if gates:
                self.em.play(gates, self.ion)
            for kind in ("measure", "reset"):
                group = [self.ops[k] for k in solo if self.ops[k].name == kind]
                if group:
                    self.em.emit(type=kind, ions=tuple(self.ion[o.qubits[0]] for o in group),
                                 meta={"kind": "readout" if kind == "measure" else "reset",
                                       "op": [o.i for o in group]})
            other = [k for k in solo if self.ops[k].name not in U3
                     and self.ops[k].name not in ("measure", "reset")]
            if other:
                raise Refused(f"op {self.ops[other[0]].i}: {self.ops[other[0]].name} is outside "
                              f"what the conveyor router plays")
            self._finish(solo)
            acted = True
        front = self._frontier()
        for g in range(len(self.loops)):
            if self.phase[g] == "idle":
                self._choose(g, front)
        units = self._units(front)
        for turning, parts in self._pack(units):
            # a cycle of rotations only is the conveyor's own class; one that carries a hop
            # between a slot and a trap is a shuttle, whatever turns beside it
            self.em.emit(type="simd", cls=CONVEYOR if turning else HOP, mode="inter",
                         participants=tuple(parts),
                         meta={"kind": "rotate" if turning else "hop"})
            acted = True
        gate_ops: list[int] = []
        for unit in units:
            kind, g = unit[0], unit[1]
            if kind == "rot":
                self.rot[g] = (self.rot[g] + unit[3]) % self.P
            elif kind == "up":
                for k in self.visit[g]:
                    pos = self.plan.data[self._pair(k)[1]]
                    self.visitor.get(pos, set()).discard(g)
                    if not self.visitor.get(pos):
                        self.visitor.pop(pos, None)
                self.visit[g] = []
                self.phase[g] = "idle"
            else:
                self.visit[g] = list(unit[3])
                for k in unit[3]:
                    self.visitor.setdefault(self.plan.data[self._pair(k)[1]], set()).add(g)
                gate_ops += unit[3]
                self.phase[g] = "up"
        # each trap plays one gate at a time (R12), the traps play together: a trap with two
        # visitors plays its second gate in a second batch
        batch: list[int] = []
        while gate_ops:
            busy: set[int] = set()
            batch, rest = [], []
            for k in sorted(gate_ops):
                pos = self.plan.data[self._pair(k)[1]]
                (rest if pos in busy else batch).append(k)
                busy.add(pos)
            self.em.play([self.ops[k] for k in batch], self.ion)
            self._finish(batch)
            gate_ops = rest
        return acted

    def run(self) -> None:
        self._trap_set = set(self.traps)
        guard = 0
        while self.left or any(p != "idle" for p in self.phase):
            if not self._tick():
                guard += 1
                if guard > 3:
                    stuck = [self.ops[k].i for k in self._frontier()][:5]
                    raise RouteError(f"the conveyor router made no progress; ready ops {stuck}")
            else:
                guard = 0


def _proxy(prog: TSIR, cooling: float) -> float:
    """What a way of compiling is chosen by, before any cost model has seen the programme:
    its transport cycles, in rotation steps (a hop is nearer a step and a half), and
    `cooling` steps for every batch of gates that follows a descent."""
    cost, after_hop = 0.0, False
    for ins in prog.instructions:
        if ins.type == "simd":
            hop = (ins.meta or {}).get("kind") == "hop"
            cost += 1.4 if hop else 1.0
            after_hop = after_hop or hop
        elif ins.type == "gate" and after_hop:
            cost += cooling
            after_hop = False
    return cost


def _cooling_in_steps(arch: Architecture) -> float:
    """One cooling's time over one rotation step's (a crossing of a three-rail node), on the
    machine's own prices; 0 where cooling takes no time."""
    doc = arch.primitives.to_json()
    try:
        cool = float(doc["cool"]["us"])
        step = float(doc["junction_cross"]["curve_by_degree"]["3"][0]["us"])
    except (KeyError, IndexError, TypeError, ValueError):
        return _VISIT / _STEP / 2
    return cool / step if step > 0 else 0.0


def compile_conveyor(qasm_text: str, arch: Architecture, n_data: int, *, name: str = "circuit",
                     per_layer: bool | None = None, align: str = "best",
                     wait: float | None = None,
                     data_at: Mapping[int, int] | None = None) -> tuple[TSIR, dict]:
    """`(programme, certificate)` for `qasm_text` on the sandwich `arch`, uncooled.

    `per_layer`: one waveform a layer (a sandwich built in layers) or one waveform (the same
    machine in a plane).  Read off the machine when left out.

    `align`: how the ancillas' slots are chosen (`place`), and `data_at` where the data stand
    when the caller knows better than the circuit's numbering.

    `wait`: how many steps of turning a loop that is ready to descend holds for another loop
    (`_Run._wait_for_another`): gates played after one descent share one cooling.

    Left out, `align` and `wait` are tried each way -- two placements; not waiting, and
    waiting a cooling's worth of steps -- and the programme that is cheapest on the
    machine's own prices is kept (`_proxy`).  `meta["placement"]` and `meta["wait_steps"]`
    say which it was."""
    dev = arch.device
    roles = sandwich_roles(arch)
    if per_layer is None:
        per_layer = dev.dim == 3 and len(dev.layers) > 1
    cooling = _cooling_in_steps(arch)
    n, ops, _ = _parse(qasm_text)
    if not 0 < n_data <= n:
        raise Refused(f"{n_data} data qubits of {n}")
    for op in ops:
        if op.name in GATES_2Q:
            a, b = op.qubits
            if (a < n_data) == (b < n_data):
                raise Refused(f"op {op.i}: {op.name} between two "
                              f"{'data qubits' if a < n_data else 'ancillas'} ({a}, {b}); on a "
                              f"sandwich only an ancilla and a data qubit ever stand together")
    ion = {q: f"q{q}" for q in range(n)}

    def build(how: str, hold: float):
        plan = place(ops, n, n_data, roles["positions"], len(roles["loops"]), align=how,
                     data_at=data_at)
        per_trap: dict[int, int] = {}
        for q, k in plan.data.items():
            per_trap[k] = per_trap.get(k, 0) + 1
        for k, c in per_trap.items():
            cap = dev.nodes[roles["data"][k]].capacity
            if c + 1 > cap:
                raise Refused(f"data trap {roles['data'][k]} holds {cap} ions: too few for its "
                              f"{c} data qubit(s) and a visiting ancilla")
        placement = {ion[q]: roles["data"][plan.data[q]] for q in range(n_data)}
        for q in range(n_data, n):
            placement[ion[q]] = roles["loops"][plan.loop[q]]["slots"][plan.slot[q]]
        prog = TSIR(name=name, arch_spec=f"arch/{arch.name}.arch.json",
                    meta={"compiler": COMPILER_NAME, "placement": how,
                          "wait_steps": None if math.isinf(hold) else hold,
                          "qubit_map": {str(q): ion[q] for q in range(n)},
                          "data_ions": [ion[q] for q in range(n_data)],
                          "waveforms": "one a layer" if per_layer else "one"})
        em = Emitter(prog, dict(placement))
        em.emit(type="init", placement=dict(placement), quanta={i: 0.0 for i in placement},
                meta={"compiler": COMPILER_NAME, "circuit": name, "arch": arch.name})
        em.emit(type="cool", broadcast=True, meta={"kind": "state_prep"})
        _Run(arch, ops, n, n_data, plan, em, ion, per_layer, hold).run()
        return prog, em, placement

    hows = ("index", "vote") if align == "best" else (align,)
    holds = (0.0, math.inf) if wait is None else (float(wait),)
    best = None
    for how in hows:
        for hold in holds:
            built = build(how, hold)
            cost = _proxy(built[0], cooling)
            if best is None or cost < best[0]:
                best = (cost, built)
    prog, em, placement = best[1]
    return prog, certificate(prog, ops, name, arch.name, n, ion, placement, em, COMPILER_NAME)


def compile_with_router(qasm_text: str, arch_path, workdir, *, n_data: int,
                        table: str = "qccdsim_jones", name: str = "circuit",
                        per_layer: bool | None = None, align: str = "best",
                        wait: float | None = None,
                        data_at: Mapping[int, int] | None = None) -> tuple[Path, Path]:
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
    prog, cert = compile_conveyor(qasm_text, arch, n_data, name=name, per_layer=per_layer,
                                  align=align, wait=wait, data_at=data_at)
    prog.save(str(work / "prog.tsir.json"), indent=None)
    (work / "prog.qcert.json").write_text(json.dumps(cert), encoding="utf-8")
    cooled = insert_cooling(prog, arch, corrected_model(table)).program
    cooled.meta.update(prog.meta)
    final = work / "prog.cooled.tsir.json"
    cooled.save(str(final), indent=None)
    (work / "compile.json").write_text(json.dumps(
        {"mode": "conveyor-router", "compiler": COMPILER_NAME,
         "seconds": round(time.time() - t0, 3)}), encoding="utf-8")
    return final, work / "prog.qcert.json"
