"""A Helios router for the published ring/cache/logic floorplan: batches in the eight zones.

This is a baseline, not Quantinuum's proprietary real-time compiler.  It works the way the paper
describes Helios working (arXiv:2511.05465, Fig. 2; rule HL4): qubits rest in the rotatable storage
ring, the ring turns to bring the crystal wanted to the X junction, and a batch of up to sixteen
qubits is processed in the eight two-ion operation zones at once.

    homes      qubit q rests on ring-storage site R<q>.  The X junction sits on the ring between R81
               and R0; nothing ever rests on it, so an ion crossing it passes nobody.
    a batch    up to eight loads, one a zone, from the front of the circuit: the two qubits of a
               two-qubit gate whose turn it is for both, or one or two qubits whose next operation
               is on one qubit (a pulse, a readout, a reset).  Earliest operation first.
    loading    a leg (X0 - cache - four operation zones) fills from its far zone inwards, so a qubit
               on its way passes only empty zones; the ring turns (the shorter way) to bring each
               qubit to R0, and it moves R0 -> X0 -> cache -> its zone.  The next qubit fetched is
               the one nearest R0 among those whose zone is next on either leg.
    running    every zone plays, in order, each operation all of whose qubits are in it and whose
               turn it is; the zones play side by side (`Emitter.play_queues`): a laser step
               addresses one chain position (R34), one operation at a time in a zone (R12).
    unloading  the near zones first, and in a zone the ion at the end toward X0 first, so an ion
               leaves a zone the way it came in, and every qubit goes back to its home on the ring.
    rotation   one storage site a step, every ion on the ring moving to the next site (the declared
               helios_rotate_cw / helios_rotate_ccw classes).  A step is one waveform, so the ion a
               step carries across the junction (R81 <-> R0, through X0) goes alone in a step of its
               own (`_Helios._step`).

Gates run in each qubit's input order (R25); no ion passes another; every emitted operation is
replayed and rule-checked.  At most 81 qubits: the ring keeps a free site to turn.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Sequence

from ..arch import Architecture
from ..cost.models import corrected_model
from ..ir.tsir import Participant, TSIR
from ..verify import verify
from ..verify.chains import Chains
from .c2lr_router import GATES_2Q, U3, Refused, RouteError, SourceOp, _parse
from .layered import Emitter, certificate

COMPILER_NAME = "helios-batch-router (baseline)"
RING = "storage_ring"
ROTATE = {1: "helios_rotate_cw", -1: "helios_rotate_ccw"}
#: each leg: its cache, then its operation zones from X0 outwards
LEGS = {"U": ("UC", ("UO0", "UO1", "UO2", "UO3")), "L": ("LC", ("LO0", "LO1", "LO2", "LO3"))}
ONE_QUBIT = set(U3) | {"measure", "reset"}


def _path(arch: Architecture, src: str, dst: str) -> list[str]:
    back: dict[str, str | None] = {src: None}
    todo = deque([src])
    while todo:
        here = todo.popleft()
        if here == dst:
            break
        for sid in arch.device.incidence[here]:
            nxt = arch.device.segments[sid].other(here)
            if nxt not in back:
                back[nxt] = here
                todo.append(nxt)
    if dst not in back:
        raise RouteError(f"no Helios transport path from {src} to {dst}")
    out = [dst]
    while back[out[-1]] is not None:
        out.append(back[out[-1]])
    return list(reversed(out))


def _storage(arch: Architecture) -> tuple[str, ...]:
    """The ring's storage sites in rotation order, R0 first: its slots less the junction."""
    dev = arch.device
    slots = dev.loop_slots(RING)
    i = slots.index("X0") if "X0" in slots else -1
    if i < 0 or dev.nodes["X0"].kind != "junction":
        raise RouteError("the Helios ring must pass through its X junction X0")
    order = slots[i + 1:] + slots[:i]
    if order[0] != "R0" or any(dev.nodes[n].kind != "site" for n in order):
        raise RouteError("the Helios ring must run X0, R0, ... with storage sites only")
    return order


class _Helios:
    """The programme under construction: where every ion is, each qubit's home on the ring, and what
    each operation zone holds (in the order its ions came in)."""

    def __init__(self, arch: Architecture, em: Emitter, ion: dict[int, str], home: dict[int, int]):
        self.arch, self.em, self.ion = arch, em, ion
        self.slots = _storage(arch)
        self.home = dict(home)
        self.zone_of: dict[int, str] = {}                     # qubit -> its zone, while it is in one
        self.held: dict[str, list[int]] = {z: [] for _, zs in LEGS.values() for z in zs}

    # ---------------------------------------------------------------- moving

    def _move(self, q: int, dst: str, ops: Sequence[int]) -> None:
        """One ion from where it is to `dst`, a stop at each site on the way (a bare junction is
        crossed inside a step).  Refuses to pass anyone: every place between must be empty."""
        ion = self.ion[q]
        full = _path(self.arch, self.em.pos[ion], dst)
        taken = {n for i, n in self.em.pos.items() if i != ion}
        if taken & set(full[1:-1]):
            raise RouteError(f"{ion} would pass {sorted(taken & set(full[1:-1]))} on its way to {dst}")
        dev = self.arch.device
        stops = [n for n in full if dev.nodes[n].kind == "site"]
        at = {n: i for i, n in enumerate(full)}
        for a, b in zip(stops, stops[1:]):
            route = full[at[a]:at[b] + 1]
            via = tuple(dev.segment_between(x, y).id for x, y in zip(route, route[1:]))
            self.em.emit(type="simd", cls="helios_shuttle", mode="inter",
                         participants=(Participant(ion, a, b, via=via),),
                         meta={"kind": "helios_route", "op": list(ops)})

    def _hop(self, a: str, b: str) -> tuple[str, ...]:
        route = _path(self.arch, a, b)
        return tuple(self.arch.device.segment_between(x, y).id for x, y in zip(route, route[1:]))

    def _distance(self, q: int) -> int:
        k = len(self.slots)
        d = (-self.home[q]) % k
        return min(d, k - d)

    def _rotate_to_r0(self, q: int, ops: Sequence[int]) -> None:
        """Turn the ring until qubit q's home is R0, next to the junction, the shorter way round."""
        k = len(self.slots)
        delta = (-self.home[q]) % k
        if delta > k // 2:
            delta -= k
        step = 1 if delta > 0 else -1
        for _ in range(abs(delta)):
            self._step(step, ops)

    def _step(self, step: int, ops: Sequence[int]) -> None:
        """The ring turns one storage site.  Every step is one waveform: its ions all move one site
        the same way.  An ion that would cross the junction (R0 going back, R81 going on) goes alone,
        through X0, in a step of its own; so the ions move in up to three steps --
            1. every ion but that one and the run of ions right behind it,
            2. that one, across the junction to the far storage site (step 1 emptied it),
            3. the run behind it, into the place it left.
        A ring that is not full always has a hole, so the run ends."""
        k = len(self.slots)
        on = {self.home[p]: p for p in self.ion if p not in self.zone_of}
        edge = 0 if step < 0 else k - 1
        cross = on.get(edge)
        behind: list[int] = []
        if cross is not None:
            j = (edge - step) % k
            while j in on and len(behind) < k:
                behind.append(on[j])
                j = (j - step) % k
        rest = [p for p in on.values() if p != cross and p not in behind]

        def cycle(qs, cls, kind):
            parts = tuple(Participant(self.ion[p], self.slots[self.home[p]], self.slots[(self.home[p] + step) % k],
                                      via=self._hop(self.slots[self.home[p]], self.slots[(self.home[p] + step) % k]))
                          for p in sorted(qs))
            if parts:
                self.em.emit(type="simd", cls=cls, mode="inter", participants=parts,
                             meta={"kind": kind, "op": list(ops)})
        cycle(rest, ROTATE[step], "helios_rotate")
        if cross is not None:
            cycle([cross], "helios_shuttle", "helios_rotate_junction")
            cycle(behind, ROTATE[step], "helios_rotate")
        for p in self.home:
            self.home[p] = (self.home[p] + step) % k

    # ---------------------------------------------------------------- a batch

    def load(self, units: list[tuple[int, ...]], ops: Sequence[int]) -> None:
        """Bring each unit (one or two qubits) to a zone of its own: each leg fills from its far zone
        inwards; the next qubit fetched is the one nearest R0 among those that may go next."""
        free = {leg: [z for z in reversed(zs) if not self.held[z]] for leg, (_, zs) in LEGS.items()}
        todo = list(units)
        while todo:
            legs = [leg for leg in free if free[leg]]
            if not legs:
                raise RouteError("more loads than free operation zones")
            unit = min(todo, key=lambda u: min(self._distance(q) for q in u))
            # the leg whose next zone is nearer X0 last: fill the leg with more room
            leg = max(legs, key=lambda g: len(free[g]))
            zone = free[leg].pop(0)
            todo.remove(unit)
            rest = list(unit)
            while rest:
                q = min(rest, key=self._distance)
                rest.remove(q)
                self._rotate_to_r0(q, ops)
                self._move(q, zone, ops)
                self.held[zone].append(q)
                self.zone_of[q] = zone

    def _free_r0(self, ops: Sequence[int]) -> None:
        """Turn the ring, the shorter way, until R0 is free: the nearest free site comes to it."""
        k = len(self.slots)
        on = {self.home[p] for p in self.ion if p not in self.zone_of}
        if 0 not in on:
            return
        ahead = next(d for d in range(1, k) if (k - d) % k not in on)      # a free site d steps before R0
        behind = next(d for d in range(1, k) if d % k not in on)           # a free site d steps after R0
        step, n = (1, ahead) if ahead <= behind else (-1, behind)
        for _ in range(n):
            self._step(step, ops)

    def unload(self, ops: Sequence[int]) -> None:
        """Every qubit in a zone goes back to the ring: the near zones first, in a zone the last one in
        first.  A qubit takes the free site nearest R0 (the ring turns at most a site or two), and that
        is its home from then on: qubits used together stay near each other, and near the junction."""
        for _, zs in LEGS.values():
            for z in zs:
                while self.held[z]:
                    q = self.held[z][-1]
                    self._free_r0(ops)                         # it stays in the zone while the ring turns
                    self.held[z].pop()
                    del self.zone_of[q]
                    self.home[q] = 0
                    self._move(q, self.slots[0], ops)


def _batch(queues: dict[int, deque], zones: int) -> list[tuple[int, ...]]:
    """The next batch's loads: earliest operation first, a two-qubit gate whose turn it is for both
    its qubits as a pair, a qubit whose next operation is on it alone paired with the next such."""
    heads = []
    for q, qq in queues.items():
        if qq:
            heads.append((qq[0].i, q))
    heads.sort()
    units: list[tuple[int, ...]] = []
    used: set[int] = set()
    single: int | None = None
    for _, q in heads:
        if q in used or len(units) >= zones:
            continue
        op = queues[q][0]
        if op.name in GATES_2Q:
            a, b = op.qubits
            other = b if q == a else a
            if other in used or not queues[other] or queues[other][0].i != op.i:
                continue
            units.append((a, b))
            used |= {a, b}
        else:
            if single is None:
                single = q
                units.append((q,))
            else:
                units[units.index((single,))] = (single, q)
                single = None
            used.add(q)
    return units


def compile_helios(qasm_text: str, arch: Architecture, *, name: str = "circuit") -> tuple[TSIR, dict]:
    """Compile OpenQASM onto Helios: qubits rest in the storage ring, which turns to bring each batch
    to the X junction, and up to sixteen at a time run in the eight operation zones."""
    if (arch.device.params or {}).get("family") != "helios":
        raise Refused(f"{arch.name} is not a Helios functional floorplan")
    if RING not in arch.device.loops or any(z not in arch.device.nodes for _, zs in LEGS.values() for z in zs):
        raise Refused(f"{arch.name} has no {RING} or not its eight operation zones")
    n, ops, _ = _parse(qasm_text)
    slots = _storage(arch)
    if n >= len(slots):
        raise Refused(f"this baseline keeps every qubit on the storage ring, with a free site to turn it: {n} "
                      f"qubits, {len(slots)} ring sites")
    for op in ops:
        if op.name not in ONE_QUBIT and op.name not in GATES_2Q and op.name != "barrier":
            raise Refused(f"line {op.line}: {op.name} is not a gate this baseline plays")
    ion = {q: f"q{q}" for q in range(n)}
    home = {q: q for q in range(n)}
    placement = {ion[q]: slots[home[q]] for q in range(n)}
    prog = TSIR(name=name, arch_spec=f"arch/{arch.name}.arch.json",
                meta={"compiler": COMPILER_NAME, "qubit_map": {str(q): ion[q] for q in range(n)}})
    em = Emitter(prog, dict(placement), chains=Chains(arch.device))
    em.emit(type="init", placement=dict(placement), quanta={i: 0.0 for i in placement},
            meta={"compiler": COMPILER_NAME, "circuit": name, "arch": arch.name})
    em.emit(type="cool", broadcast=True, meta={"kind": "state_prep"})
    h = _Helios(arch, em, ion, home)
    queues: dict[int, deque] = {q: deque() for q in range(n)}
    for op in ops:
        if op.name != "barrier":
            for q in op.qubits:
                queues[q].append(op)
    zones = sum(len(zs) for _, zs in LEGS.values())
    while any(queues.values()):
        units = _batch(queues, zones)
        if not units:
            raise RouteError("no operation can run: the circuit's order is stuck")
        h.load(units, [queues[u[0]][0].i for u in units])
        plays: list[list[SourceOp]] = []
        for z, qs in h.held.items():
            if not qs:
                continue
            here, play = set(qs), []
            while True:                                      # this zone's operations, in order
                nxt = None
                for q in qs:
                    if queues[q]:
                        op = queues[q][0]
                        if set(op.qubits) <= here and all(queues[p] and queues[p][0].i == op.i for p in op.qubits):
                            if nxt is None or op.i < nxt.i:
                                nxt = op
                if nxt is None:
                    break
                play.append(nxt)
                for p in nxt.qubits:
                    queues[p].popleft()
            if play:
                plays.append(play)
        em.play_queues(plays, ion)
        h.unload([p[-1].i for p in plays if p])
    return prog, certificate(prog, ops, name, arch.name, n, ion, placement, em, COMPILER_NAME)


def compile_with_router(qasm_text: str, arch_path, workdir, *, table: str = "helios",
                        name: str = "circuit") -> tuple[Path, Path]:
    """Write, cool, replay, and check a certified Helios compilation."""
    from .cooling import CoolingPolicy, insert_cooling
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
    prog, cert = compile_helios(qasm_text, arch, name=name)
    prog.save(str(work / "prog.tsir.json"), indent=None)
    (work / "prog.qcert.json").write_text(json.dumps(cert), encoding="utf-8")
    model = corrected_model(table)
    cooled = insert_cooling(prog, arch, model, policy=CoolingPolicy(lazy=True)).program
    cooled.meta.update(prog.meta)
    final = work / "prog.cooled.tsir.json"
    cooled.save(str(final), indent=None)
    rep = verify(cooled, arch, model, check_metrics=False)
    failed = sorted(rep.rules.summary().get("failed") or [])
    if failed:
        violation = next(v for v in rep.rules.violations if v.rule in failed)
        raise RouteError(f"the Helios router fails {failed}: {violation.rule}: {violation.message}")
    (work / "compile.json").write_text(json.dumps({
        "mode": "helios-batch-router", "table": table,
        "compile_s": round(time.time() - t0, 3), "instructions": len(cooled.instructions),
    }, indent=1), encoding="utf-8")
    return final, work / "prog.qcert.json"
