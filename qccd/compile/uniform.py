"""Uniformising transport -- one transport cycle, one waveform.  R22.

R22 says what broadcast control physically is: a transport cycle is a *waveform*, played
into every electrode of the class at once, so every ion that moves in that cycle performs
the same motion.  A site may opt out (its switch stays open); it may not do something
else.  The rule is stated and enforced in `qccd/verify/rules.py` (`r22_uniform_motion`),
and what an ion "does" is `motion_signature` there: the movement class, then hop by hop
the direction -- along a named path as `L0:+1`, on a spur as `spur:inward` /
`spur:outward`, and anywhere else as the lab-frame axis.

Routers written before R22 batch unlike hops into one time slot, because a scheduler that
only asks "do these moves conflict?" sees no conflict between an ion going north and an
ion going east.  Every such cycle is one waveform short.  This pass buys the missing ones:
it splits each offending `simd` instruction into one instruction per signature, so the
programme does exactly what it did, in as many cycles as the hardware can actually issue.

Three things make it a rewrite rather than a re-schedule.

**The signature is not recomputed.**  The pass calls `motion_signature` -- the very
function R22 calls -- on the `CycleView` the replay builds, using `replay`'s own
`_resolve_participants` and `_segments_for`.  A second opinion about what an ion is doing
would be a pass that "fixes" programmes the verifier still rejects, or that splits ones it
would have accepted.

**Vacate before arrive.**  The moves of one cycle happened at once, so one group may be
moving into sites another group is moving out of.  Serialise those the wrong way round
and a programme that was legal under R1/R2 stops being legal: two ions in a trap with
room for one.  The groups are therefore ordered by a dependency derived from the
participants' `src`/`dst` -- a group whose destinations another group is vacating runs
after it.  A dependency *cycle* (two ions exchanging places, which is R5's case) has no
serialisation at all; the pass says so in the report and falls back to sorted signature
order rather than inventing a buffer site it was not asked for.

**Idempotence.**  A programme with nothing to split comes back untouched -- the same
object, the same ids.  `Instruction.id` is the handle every join in the system keys on
(see `TSIR.next_id`), so a pass that re-labels wholesale silently re-targets all of them;
this one renumbers only when it has actually rewritten the instruction list, and then
hands back the `id_map` that translates every old handle.  Running the pass on its own
output is a no-op, which `tests/test_uniform.py` asserts rather than assumes.

What the pass does NOT do: it does not re-time, re-cost or re-cool.  Splitting one cycle
into three makes the programme longer, so every timing and cost claim it carried is now
false; claims that can no longer be made are dropped rather than left to lie, and cooling
is re-run afterwards by whoever runs this (`Compiler/bridge/make_uniform.py`, then
`Compiler/bridge/insert_cooling.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping, Sequence

from ..arch import Architecture
from ..ir.tsir import TSIR, Instruction, Participant
from ..verify.replay import ReplayError, _resolve_participants, _segments_for
from ..verify.rules import CycleView, ResolvedMove, motion_signature

__all__ = [
    "UniformReport",
    "SplitRecord",
    "make_uniform",
    "cycle_view",
    "signatures",
    "STALE_METRICS",
    "STALE_ANNOTATIONS",
]

#: Program-level claims a split invalidates: more cycles is more depth, more elapsed
#: time and more anomalous heating.  `contacts`, `batches` and `total_rotate_hops` are
#: untouched by a split, so they are kept.
STALE_METRICS = ("total_cost", "total_steps", "runtime_us", "total_quanta",
                 "peak_quanta")

#: Per-instruction claims a split invalidates.  `operating_point` is a choice of curve
#: point, not a timing claim, so it survives.
STALE_ANNOTATIONS = ("t0", "t1", "cost", "steps", "quanta_delta")


@dataclass(frozen=True)
class SplitRecord:
    """One instruction that became several, and what each of the pieces is."""

    instr_id: int                     # the id it had in the programme handed in
    signatures: tuple[str, ...]       # one per emitted instruction, in emitted order
    new_ids: tuple[int, ...]          # the ids it became, aligned with `signatures`
    #: True when no serialisation of the groups vacates before it arrives (two ions
    #: exchanging places, say), so sorted signature order was used instead.
    dependency_cycle: bool = False

    def as_dict(self) -> dict:
        return {
            "instr_id": self.instr_id,
            "signatures": list(self.signatures),
            "new_ids": list(self.new_ids),
            "dependency_cycle": self.dependency_cycle,
        }


@dataclass
class UniformReport:
    """What `make_uniform` did, in enough detail to rewrite anything that joined on ids."""

    program: TSIR
    #: old instruction id -> the new ids it became, in program order.  Every instruction
    #: of the input appears, split or not; an unchanged programme maps each id to itself.
    id_map: dict[int, list[int]] = field(default_factory=dict)
    #: `simd` transport instructions before and after.  A `loop_shift` template counts
    #: once however many hops it is issued for, exactly as `TSIR.templates` counts it.
    cycles_before: int = 0
    cycles_after: int = 0
    instructions_before: int = 0
    instructions_after: int = 0
    #: the instructions that became several, in program order
    splits: list[SplitRecord] = field(default_factory=list)
    #: (instruction id, why) for a cycle the pass could not judge -- an unresolvable
    #: route, say.  These are left exactly as they were, and R22 will still reject them.
    unsplit: list[tuple[int, str]] = field(default_factory=list)
    #: The instructions this pass could not split LEGALLY: their groups admit no
    #: vacate-before-arrive order, so they were emitted in sorted signature order and
    #: whichever edge that breaks is now a real R1/R5 exposure.  Measured on the
    #: leaderboard's lattice entries, both orders of such a pair are illegal -- the ions
    #: rotate through traps that are already at capacity, and the router's simultaneity
    #: was load-bearing.  Fixing one needs a buffer hop, which is a routing decision and
    #: not this pass's to make.
    dependency_cycles: list[int] = field(default_factory=list)
    changed: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "changed": self.changed,
            "cycles_before": self.cycles_before,
            "cycles_after": self.cycles_after,
            "instructions_before": self.instructions_before,
            "instructions_after": self.instructions_after,
            "instructions_split": len(self.splits),
            "cycles_added": self.cycles_after - self.cycles_before,
            "splits": [s.as_dict() for s in self.splits],
            "unsplit": [{"instr_id": i, "why": w} for i, w in self.unsplit],
            "dependency_cycles": list(self.dependency_cycles),
            "notes": list(self.notes),
        }

    def id_map_json(self) -> dict[str, list[int]]:
        """`id_map` with string keys, for JSON."""
        return {str(k): list(v) for k, v in sorted(self.id_map.items())}


# ------------------------------------------------------------------ signatures


def cycle_view(arch: Architecture, instr: Instruction) -> CycleView:
    """The `CycleView` the replay would build for `instr`, minus the state R22 ignores.

    `motion_signature` and `r22_uniform_motion` read exactly three things off a view --
    `arch`, `instr` (for `cls` and `type`) and `moves` -- so the positions, occupancies
    and n-bar are left empty rather than reconstructed: a half-right `pos_before` would
    be a second, wrong opinion about where the ions are, and nothing reachable from here
    would read it.

    The `moves` are built the way `replay` builds them, out of `replay`'s own
    `_resolve_participants` and `_segments_for`, so an instruction the replay refuses
    (a `via` that does not reach the declared destination) is refused here too.
    """
    if instr.template:
        raise ReplayError(
            f"instruction {instr.id}: a movement template is uniform by construction; "
            f"it has no per-participant signature to group by")
    entails = arch.entails(instr.cls) if instr.cls else ()
    moves: list[ResolvedMove] = []
    for sub in _resolve_participants(arch, instr, {}):
        for p in sub:
            node = p.src
            for seg in _segments_for(arch, p):
                nxt = seg.other(node)
                moves.append(ResolvedMove(p.ion, node, nxt, seg, tuple(entails)))
                node = nxt
            if node != p.dst:
                raise ReplayError(
                    f"instruction {instr.id}: ion {p.ion} `via` ends at {node}, not the "
                    f"declared {p.dst}")
    return CycleView(
        arch=arch,
        instr=instr,
        moves=tuple(moves),
        pos_before={},
        pos_after={},
        occ_before={},
        occ_after={},
        quanta={},
    )


def signatures(arch: Architecture, instr: Instruction) -> dict[str, str]:
    """`ion -> motion signature` for one transport instruction, exactly as R22 reads it."""
    view = cycle_view(arch, instr)
    out: dict[str, str] = {}
    for p in instr.participants:
        if p.ion not in out:
            out[p.ion] = motion_signature(view, p.ion)
    return out


# ------------------------------------------------------------------ grouping


def _group(
    arch: Architecture, instr: Instruction
) -> tuple[list[tuple[str, list[Participant]]], bool]:
    """`[(signature, participants)]` in the order they must be issued, and whether the
    vacate-before-arrive dependency had a cycle."""
    sigs = signatures(arch, instr)
    by_sig: dict[str, list[Participant]] = {}
    for p in instr.participants:
        by_sig.setdefault(sigs[p.ion], []).append(p)
    if len(by_sig) <= 1:
        return list(by_sig.items()), False
    return _order(by_sig)


def _order(
    by_sig: Mapping[str, list[Participant]]
) -> tuple[list[tuple[str, list[Participant]]], bool]:
    """Serialise the groups so that no group arrives at a site another has yet to leave.

    The edge is the physical one: if group B ends on a site group A starts from, then A
    has to vacate it before B lands, so A runs first.  Ties are broken by sorted
    signature, so the output is a function of the input and of nothing else.
    """
    srcs = {s: {p.src for p in ps} for s, ps in by_sig.items()}
    dsts = {s: {p.dst for p in ps} for s, ps in by_sig.items()}
    after: dict[str, set[str]] = {s: set() for s in by_sig}   # s -> groups that follow s
    indeg: dict[str, int] = {s: 0 for s in by_sig}
    for a in by_sig:
        for b in by_sig:
            if a == b or b in after[a]:
                continue
            if dsts[b] & srcs[a]:      # b lands where a departs: a vacates first
                after[a].add(b)
                indeg[b] += 1

    order: list[str] = []
    ready = sorted(s for s in by_sig if indeg[s] == 0)
    while ready:
        s = ready.pop(0)
        order.append(s)
        for nxt in sorted(after[s]):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)
        ready.sort()
    if len(order) != len(by_sig):
        # No serialisation exists -- the groups swap sites.  Say so, and be deterministic.
        return [(s, by_sig[s]) for s in sorted(by_sig)], True
    return [(s, by_sig[s]) for s in order], False


# ------------------------------------------------------------------ the pass


def make_uniform(prog: TSIR, arch: Architecture) -> UniformReport:
    """Rewrite `prog` so that every transport cycle is one waveform (R22).

    The programme's *meaning* is untouched: the same ions make the same hops in the same
    relative order, only in as many cycles as broadcast control can issue.  It is longer
    and slower afterwards, by construction -- that is the cost R22 names.
    """
    report = UniformReport(
        program=prog,
        instructions_before=len(prog.instructions),
        cycles_before=sum(1 for i in prog.instructions if i.type == "simd"),
    )

    # ---- pass 1: decide, without emitting -----------------------------------
    plan: list[tuple[Instruction, list[tuple[str, list[Participant]]], bool]] = []
    n_split = 0
    for instr in prog.instructions:
        if instr.type != "simd" or instr.template or not instr.participants:
            plan.append((instr, [], False))
            continue
        try:
            groups, cyclic = _group(arch, instr)
        except (ReplayError, KeyError) as exc:
            report.unsplit.append((instr.id, str(exc)))
            plan.append((instr, [], False))
            continue
        if len(groups) <= 1:
            plan.append((instr, [], False))
            continue
        n_split += 1
        if cyclic:
            report.dependency_cycles.append(instr.id)
        plan.append((instr, groups, cyclic))

    if n_split == 0:
        # Nothing to buy.  Hand back the programme itself -- ids included: `Instruction.id`
        # is an identity every join keys on, and re-labelling a programme this pass had no
        # work to do on would re-target all of them for nothing.
        report.id_map = {i.id: [i.id] for i in prog.instructions}
        report.cycles_after = report.cycles_before
        report.instructions_after = report.instructions_before
        report.notes.append(
            "every transport cycle is already one waveform; the programme is unchanged")
        if report.unsplit:
            report.notes.append(
                f"{len(report.unsplit)} instruction(s) could not be judged and were left "
                f"alone: {[i for i, _ in report.unsplit][:8]}")
        return report

    # ---- pass 2: emit -------------------------------------------------------
    out: list[Instruction] = []
    id_map: dict[int, list[int]] = {}
    splits: list[SplitRecord] = []
    for instr, groups, cyclic in plan:
        if not groups:
            out.append(instr)
            id_map[instr.id] = [len(out) - 1]
            continue
        ids: list[int] = []
        for sig, participants in groups:
            out.append(replace(
                instr,
                participants=tuple(participants),
                meta=dict(instr.meta, waveform=sig, split_of=instr.id),
            ))
            ids.append(len(out) - 1)
        id_map[instr.id] = ids
        splits.append(SplitRecord(
            instr_id=instr.id,
            signatures=tuple(s for s, _ in groups),
            new_ids=tuple(ids),
            dependency_cycle=cyclic,
        ))

    # Renumber contiguously.  The ids in `id_map` are the positions the loop above
    # recorded, so this is what makes them true rather than an accident of appending.
    out = [replace(i, id=n) for n, i in enumerate(out)]

    # ---- claims the rewrite invalidates ------------------------------------
    n_annotated = sum(
        1 for i in out if any(getattr(i, k) is not None for k in STALE_ANNOTATIONS))
    if n_annotated:
        out = [replace(i, **{k: None for k in STALE_ANNOTATIONS}) for i in out]
    metrics = {k: v for k, v in prog.metrics.items() if k not in STALE_METRICS}
    dropped_metrics = sorted(set(prog.metrics) - set(metrics))
    meta = dict(prog.meta)
    had_batch_claims = bool(meta.pop("batch_claims", None))
    meta["derived_from"] = prog.name
    meta["uniform"] = {
        "rule": "R22",
        "instructions_split": len(splits),
        "cycles_before": report.cycles_before,
        "cycles_after": sum(1 for i in out if i.type == "simd"),
        "dependency_cycles": list(report.dependency_cycles),
    }

    report.program = TSIR(
        name=f"{prog.name}+uniform",
        arch_spec=prog.arch_spec,
        instructions=_remap_meta(out, id_map),
        metrics=metrics,
        meta=meta,
        id_seq=len(out),
    )
    report.id_map = id_map
    report.splits = splits
    report.changed = True
    report.instructions_after = len(out)
    report.cycles_after = sum(1 for i in out if i.type == "simd")
    report.notes.append(
        f"{len(splits)} transport instruction(s) asked for more than one waveform; "
        f"{report.cycles_before} transport cycles became {report.cycles_after}")
    if report.dependency_cycles:
        report.notes.append(
            f"{len(report.dependency_cycles)} instruction(s) have no vacate-before-arrive "
            f"order (ions exchanging sites); sorted signature order was used, and the "
            f"programme may now fail R1/R5 there: {report.dependency_cycles[:8]}")
    if report.unsplit:
        report.notes.append(
            f"{len(report.unsplit)} instruction(s) could not be judged and were left "
            f"alone: {[i for i, _ in report.unsplit][:8]}")
    if n_annotated:
        report.notes.append(
            f"the timeline moved, so {n_annotated} instruction annotation(s) "
            f"({'/'.join(STALE_ANNOTATIONS)}) were dropped rather than left to lie")
    if dropped_metrics or had_batch_claims:
        report.notes.append(
            "dropped the programme claims the split invalidates: "
            + ", ".join(dropped_metrics + (["batch_claims"] if had_batch_claims else [])))
    return report


def _remap_meta(
    instrs: Sequence[Instruction], id_map: Mapping[int, list[int]]
) -> list[Instruction]:
    """Translate the one piece of `meta` that names another instruction.

    `qccd.compile.cooling` stamps `before_instruction` on every cool it inserts, and that
    id is a handle like any other: renumbering without translating it would leave the
    cooling report pointing at whatever instruction now happens to hold the number.  The
    cool precedes its subject, so it names the FIRST of the ids that subject became.
    """
    out: list[Instruction] = []
    for instr in instrs:
        ref = instr.meta.get("before_instruction")
        if isinstance(ref, int) and not isinstance(ref, bool) and id_map.get(ref):
            out.append(replace(
                instr, meta=dict(instr.meta, before_instruction=id_map[ref][0])))
        else:
            out.append(instr)
    return out
