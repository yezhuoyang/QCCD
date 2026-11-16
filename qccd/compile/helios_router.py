"""A functional Helios router for the published ring/cache/logic floorplan.

This is intentionally a baseline, not Quantinuum's proprietary real-time compiler.  It works
the way the paper describes Helios working (arXiv:2511.05465, Fig. 2; rule HL4): qubits rest
in the rotatable storage ring, and the ring rotates to bring the crystal an operation needs to
the X junction, from where it is shuttled through the upper cache into an operation zone.

    homes      qubit q rests on ring-storage site R<q>.  The X junction sits on the ring between
               R81 and R0; nothing ever rests on it, so an ion crossing it passes nobody.
    the zone   every operation -- gate, preparation, measurement -- runs in the operation zone
               next to the cache (UO0); ring storage, cache and leg storage have no logic (HL3).
    one op     in the input's order: the ions in the zone the op does not use go back to their
               homes, last in first out (they stand in a line); then each operand not yet there
               is fetched: the ring rotates its home to R0, and it moves R0 -> X0 -> UC -> UO0.
    rotation   the ring turns one storage site at a time, the shorter way round, every ion on it
               moving to the next site (the declared helios_rotate_cw / helios_rotate_ccw
               classes).  A cycle is one waveform, so the ion a step carries across the junction
               (R81 <-> R0, through X0) goes alone in a cycle of its own (`_Helios._step`).

An ion only ever leaves a zone the way it came in, and only crosses places nobody stands on,
so no two ions pass each other.  Gates run in the input's order.  Every emitted operation is
replayed and rule-checked.
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
from .c2lr_router import GATES_2Q, U3, Refused, RouteError, SourceOp, _parse
from .cooling import insert_cooling
from .layered import Emitter, certificate

COMPILER_NAME = "helios-functional-router (baseline)"
#: the storage ring's loop, and the operation zone every operation runs in
RING, ZONE = "storage_ring", "UO0"
ROTATE = {1: "helios_rotate_cw", -1: "helios_rotate_ccw"}


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
    """The programme under construction: where every ion is, and each qubit's home slot."""

    def __init__(self, arch: Architecture, em: Emitter, ion: dict[int, str], home: dict[int, int]):
        self.arch, self.em, self.ion = arch, em, ion
        self.slots = _storage(arch)
        self.home = dict(home)            # qubit -> its storage-site index (it turns with the ring)
        self.in_zone: list[int] = []      # the qubits in the zone, in the order they came in

    def _move(self, q: int, dst: str, ops: Sequence[int]) -> None:
        """One ion from where it is to `dst`, a stop at each site on the way (a bare junction is
        crossed inside a step).  Refuses to pass anyone: every place between here and `dst` must
        be empty (`dst` itself may hold a partner, up to its capacity, which the replay checks)."""
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
        """The rails from storage site a to the next one, b: one, or two across the junction."""
        return tuple(self.arch.device.segment_between(x, y).id
                     for x, y in zip(*(lambda r: (r, r[1:]))(_path(self.arch, a, b))))

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
        """The ring turns one storage site.  Every cycle is one waveform: its ions all move one site
        the same way.  An ion that would cross the junction (R0 going back, R81 going on) goes alone,
        through X0, in a cycle of its own; so the ions are moved in up to three cycles --
            1. every ion but that one and the run of ions right behind it,
            2. that one, across the junction to the far storage site (the first cycle emptied it),
            3. the run behind it, into the place it left.
        A ring that is not full (at most 81 qubits) always has a hole, so the run ends."""
        k = len(self.slots)
        on = {self.home[p]: p for p in self.ion if p not in self.in_zone}      # site index -> qubit
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

    def fetch(self, q: int, ops: Sequence[int]) -> None:
        self._rotate_to_r0(q, ops)
        self._move(q, ZONE, ops)
        self.in_zone.append(q)

    def put_back(self, ops: Sequence[int]) -> None:
        """The ion nearest the cache goes home: it is the last one in."""
        q = self.in_zone[-1]
        self._rotate_to_r0(q, ops)        # it stays in the zone while the ring turns its home round
        self.in_zone.pop()
        self._move(q, self.slots[0], ops)

    def gather(self, qubits: Sequence[int], ops: Sequence[int]) -> None:
        """The zone holds exactly `qubits` (it takes two)."""
        while any(p not in qubits for p in self.in_zone):
            self.put_back(ops)
        for q in qubits:
            if q not in self.in_zone:
                self.fetch(q, ops)


def compile_helios(qasm_text: str, arch: Architecture, *, name: str = "circuit") -> tuple[TSIR, dict]:
    """Compile OpenQASM onto Helios: qubits rest in the storage ring, which turns to bring each
    operation's ions to the cache, and every operation runs in one operation zone."""
    if (arch.device.params or {}).get("family") != "helios":
        raise Refused(f"{arch.name} is not a Helios functional floorplan")
    if RING not in arch.device.loops or ZONE not in arch.device.nodes:
        raise Refused(f"{arch.name} has no {RING} or no operation zone {ZONE}")
    n, ops, _ = _parse(qasm_text)
    slots = _storage(arch)
    if n >= len(slots):
        raise Refused(f"this baseline keeps every qubit on the storage ring, with a hole to turn it: {n} qubits, "
                      f"{len(slots)} ring sites")
    for op in ops:
        if op.name not in U3 and op.name not in GATES_2Q and op.name not in ("measure", "reset", "barrier"):
            raise Refused(f"line {op.line}: {op.name} is not a gate this baseline plays")
    ion = {q: f"q{q}" for q in range(n)}
    home = {q: q for q in range(n)}
    placement = {ion[q]: slots[home[q]] for q in range(n)}
    prog = TSIR(name=name, arch_spec=f"arch/{arch.name}.arch.json",
                meta={"compiler": COMPILER_NAME,
                      "qubit_map": {str(q): ion[q] for q in range(n)}})
    em = Emitter(prog, dict(placement))
    em.emit(type="init", placement=dict(placement), quanta={i: 0.0 for i in placement},
            meta={"compiler": COMPILER_NAME, "circuit": name, "arch": arch.name})
    em.emit(type="cool", broadcast=True, meta={"kind": "state_prep"})
    h = _Helios(arch, em, ion, home)

    for op in ops:                        # the input's order, one statement at a time
        if op.name == "barrier":
            continue
        h.gather(op.qubits, [op.i])
        if op.name in ("measure", "reset"):
            em.emit(type=op.name, ions=(ion[op.qubits[0]],),
                    meta={"kind": "readout" if op.name == "measure" else "reset", "op": [op.i]})
        else:
            em.play([op], ion)

    return prog, certificate(prog, ops, name, arch.name, n, ion, placement, em, COMPILER_NAME)


def compile_with_router(qasm_text: str, arch_path, workdir, *, table: str = "helios",
                        name: str = "circuit") -> tuple[Path, Path]:
    """Write, cool, replay, and check a certified Helios compilation."""
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
    cooled = insert_cooling(prog, arch, model).program
    cooled.meta.update(prog.meta)
    final = work / "prog.cooled.tsir.json"
    cooled.save(str(final), indent=None)
    rep = verify(cooled, arch, model, check_metrics=False)
    failed = sorted(rep.rules.summary().get("failed") or [])
    if failed:
        violation = next(v for v in rep.rules.violations if v.rule in failed)
        raise RouteError(f"the Helios router fails {failed}: {violation.rule}: {violation.message}")
    (work / "compile.json").write_text(json.dumps({
        "mode": "helios-functional-router", "table": table,
        "compile_s": round(time.time() - t0, 3), "instructions": len(cooled.instructions),
    }, indent=1), encoding="utf-8")
    return final, work / "prog.qcert.json"
