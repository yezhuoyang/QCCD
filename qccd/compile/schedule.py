"""Pass 7 -- event scheduling.  PLAN §4, §7.

List-order scheduling runs every instruction after the previous one finishes. That is
depth-oriented, and PLAN §4 records what it forfeits: operation latencies here span 5 to
500 us, and time-sliced synchronization that tracks actual completion times took a worked
example from 823 us to 545 us (arXiv:2504.17886).

This pass assigns each instruction the earliest start at which every resource it needs is
free, preserving program order per resource. What can then overlap is decided by the
resources, not by a special case:

``transport``   the DC control pathway. R4b makes intra- and inter-trap transport mutually
                exclusive, so they contend for this one resource and never overlap.
``optics``      gate, measure, reset and cooling beams. A separate pathway, so an ancilla
                measurement CAN proceed while the rail rotates -- different hardware.
sites, ions     an instruction waits for the ions and sites it touches.

Whether cooling may run *during* transport is a physics claim, not a scheduling one, so it
is read from the architecture (`primitives.cool.concurrent_with_transport`) and defaults to
false. Hiding 119 ms of cooling under the rotation would be the single largest win
available here, which is exactly why the platform should not grant it to itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping

from ..arch import Architecture
from ..ir.tsir import TSIR, Instruction, iter_pairs
from ..verify.replay import ReplayResult

__all__ = ["ScheduleResult", "schedule_events", "resources_of"]

TRANSPORT = "@transport"
OPTICS = "@optics"


@dataclass
class ScheduleResult:
    program: TSIR
    serial_us: float = 0.0
    makespan_us: float = 0.0
    overlapped_us: float = 0.0
    n_overlapped: int = 0
    critical_path_us: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def speedup(self) -> float:
        return self.serial_us / self.makespan_us if self.makespan_us else 1.0


def resources_of(
    arch: Architecture, instr: Instruction, cool_concurrent: bool
) -> tuple[set[str], set[str]]:
    """`(resources, ions)` the instruction holds for its duration."""
    res: set[str] = set()
    ions: set[str] = set()
    if instr.type == "simd":
        res.add(TRANSPORT)
        res.update(instr.holds)
        for p in instr.participants:
            ions.add(p.ion)
            res.update({p.src, p.dst})
            res.update(p.via)
        if instr.template and instr.template.get("kind") == "loop_shift":
            loop = str(instr.template["loop"])
            res.add(loop)
            # a rigid shift moves every ion on the loop, so it holds all of them
            ions.update({"*loop:" + loop})
    elif instr.type == "gate":
        res.add(OPTICS)
        res.update(instr.sites)
        for a, b in iter_pairs(instr):
            ions.update({a, b})
    elif instr.type in ("measure", "reset"):
        res.add(OPTICS)
        ions.update(instr.ions)
    elif instr.type == "cool":
        res.add(OPTICS)
        if not cool_concurrent:
            # a global cool that cannot run during transport blocks the rail too
            res.add(TRANSPORT)
        if instr.ions:
            ions.update(instr.ions)
        else:
            ions.add("*all")
    elif instr.type == "barrier":
        res.update({TRANSPORT, OPTICS})
        ions.add("*all")
    return res, ions


def schedule_events(
    prog: TSIR,
    arch: Architecture,
    res: ReplayResult,
    *,
    respect_order: bool = True,
) -> ScheduleResult:
    """Annotate `t0`/`t1` with the earliest resource-feasible start for each instruction.

    Program order is preserved *per resource*, never globally, which is the whole point:
    two instructions that share nothing are free to run at once.
    """
    cool_spec = {}
    try:
        cool_spec = dict(arch.primitives.scalar("cool"))
    except KeyError:
        pass
    cool_concurrent = bool(cool_spec.get("concurrent_with_transport", False))

    duration: dict[int, float] = {}
    for c in res.cycles:
        duration[c.instr_id] = duration.get(c.instr_id, 0.0) + (c.t1 - c.t0)

    # a resource with multiplicity k has k independent slots; an instruction takes the
    # one that frees first.  `@transport` has as many slots as the machine has movement
    # classes it can drive at once, which is what makes a C2LR-style limit of 4 usable
    # rather than merely declared.
    slots = {TRANSPORT: max(1, arch.max_simd_classes()), OPTICS: 1}
    free_slots: dict[str, list[float]] = {k: [0.0] * n for k, n in slots.items()}
    free_res: dict[str, float] = {}
    free_ion: dict[str, float] = {}
    everything = 0.0   # when the last instruction that touched EVERY ion finished
    out: list[Instruction] = []
    serial = 0.0
    makespan = 0.0
    overlapped = 0

    for instr in prog.instructions:
        d = duration.get(instr.id, 0.0)
        rs, ions = resources_of(arch, instr, cool_concurrent)
        # a rigid shift or a broadcast cool touches every ion, so it both waits for all
        # of them and blocks all of them; naming that explicitly is cheaper and clearer
        # than expanding 144 ion entries per instruction
        touches_all = any(i.startswith("*") for i in ions)
        named = {i for i in ions if not i.startswith("*")}

        multi = [r for r in rs if r in free_slots]
        single = [r for r in rs if r not in free_slots]
        start = max((free_res.get(r, 0.0) for r in single), default=0.0)
        for r in multi:
            start = max(start, min(free_slots[r]))
        start = max(start, everything)
        if touches_all:
            start = max(start, max(free_ion.values(), default=0.0))
        else:
            start = max([start] + [free_ion.get(i, 0.0) for i in named])

        end = start + d
        if d > 0 and start < serial:
            overlapped += 1
        for r in single:
            free_res[r] = end
        for r in multi:
            free_slots[r][free_slots[r].index(min(free_slots[r]))] = end
        for i in named:
            free_ion[i] = end
        if touches_all:
            everything = end
            free_ion.clear()
        serial += d
        makespan = max(makespan, end)
        out.append(replace(instr, t0=round(start, 6), t1=round(end, 6)))

    scheduled = TSIR(name=prog.name, arch_spec=prog.arch_spec, instructions=out,
                     metrics=dict(prog.metrics), meta=dict(prog.meta),
                     id_seq=prog.id_seq)
    saved = serial - makespan
    notes = [
        f"serial {serial / 1000:.2f} ms -> scheduled {makespan / 1000:.2f} ms "
        f"({saved / 1000:.2f} ms hidden, {overlapped} instructions overlapped)",
    ]
    if slots[TRANSPORT] > 1:
        notes.append(
            f"the machine drives {slots[TRANSPORT]} movement classes at once, so that "
            f"many transport instructions may overlap (R4)")
    if not cool_concurrent:
        notes.append(
            "cooling holds the transport pathway too: the architecture does not declare "
            "`cool.concurrent_with_transport`, and granting it would be a physics claim")
    return ScheduleResult(
        program=scheduled, serial_us=serial, makespan_us=makespan,
        overlapped_us=saved, n_overlapped=overlapped, critical_path_us=makespan,
        notes=notes,
    )
