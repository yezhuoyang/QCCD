"""Odd-even transposition sort as a reconfiguration scheme.  PLAN §1, milestone M4.

The thesis is that rigid lockstep rotation makes WISE's serialization penalty nearly
free, because rotation needs one movement template where an odd-even sort needs many.
Testing that needs the *other* scheme to be a first-class citizen of the platform --
emitted as TSIR, replayed by the same engine, judged by the same rules -- rather than a
formula in a slide.  This module is that scheme.

How a transposition is realized
-------------------------------
The ring is fully packed: `n` ions on `n` capacity-2 slots, no free space.  So a
transposition cannot be done by sliding two ions past each other; it is

    merge   the ion at slot i+1 shuttles left into slot i, joining the chain there
    split   one of the two ions splits back out to slot i+1

and *which* one splits out is the choice that realizes the transposition.  At capacity 2
both ions are at a trap edge, so R14's hidden 3-CX swap costs nothing and no explicit
intra-trap reordering is needed.

Two consequences, and they are the whole comparison:

**Direction.**  The merge phase moves ions left; the split phase moves them right.  R4
says a class fixes the operation type *and the global direction*, so those are two
classes and under WISE (one class per cycle) they cannot share a cycle.  H2's compiler
already does exactly this -- a parallel bubble sort "that lets qubits move in both
directions around the device" (arXiv:2305.03828) -- which is what makes it the
experimental reference point rather than a strawman.

**Primitive mix.**  Every transposition pays one merge and one split, at n-bar < 6 quanta
each, against n-bar < 0.1 for the segment shuttle a rotation hop costs (arXiv:2510.23519).
That is a 60x heating ratio per movement, and heating is what buys cooling time.  The
platform charges it automatically: the classes declare `entails`, and the cost model
reads it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from ..arch import Architecture
from ..ir.tsir import TSIR, Instruction, Participant

__all__ = [
    "SortResult",
    "odd_even_sort_program",
    "odd_even_rounds",
    "MERGE_CLASS",
    "SPLIT_CLASS",
]

#: The two movement classes a packed-ring transposition needs.  Declared by the
#: architecture (see `tools/make_arch.py`) so R4 can check them.
MERGE_CLASS = "sort_merge"
SPLIT_CLASS = "sort_split"


@dataclass
class SortResult:
    program: TSIR
    rounds: int = 0
    active_rounds: int = 0
    transpositions: int = 0
    cycles: int = 0
    #: cycles the same schedule would need with no limit on classes per cycle
    cycles_unconstrained: int = 0
    max_transpositions_in_a_round: int = 0
    reached_target: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def serialization_factor(self) -> float:
        """Cycles under WISE (one class per cycle) over cycles with free wiring."""
        if not self.cycles_unconstrained:
            return float("nan")
        return self.cycles / self.cycles_unconstrained

    def as_dict(self) -> dict:
        return {
            "rounds": self.rounds,
            "active_rounds": self.active_rounds,
            "transpositions": self.transpositions,
            "cycles": self.cycles,
            "cycles_unconstrained": self.cycles_unconstrained,
            "serialization_factor": self.serialization_factor,
            "max_transpositions_in_a_round": self.max_transpositions_in_a_round,
            "reached_target": self.reached_target,
            "notes": list(self.notes),
        }


def odd_even_rounds(keys: Sequence[int]) -> tuple[list[list[int]], list[int]]:
    """Run odd-even transposition sort over `keys`, returning the swaps it performs.

    Returns `(rounds, final_order)` where `rounds[r]` is the list of left indices `i`
    swapped in round `r`.  Odd-even transposition sort sorts any sequence of `n` elements
    in at most `n` rounds, which is the bound the comparison uses.
    """
    a = list(keys)
    n = len(a)
    rounds: list[list[int]] = []
    for r in range(n):
        parity = r % 2
        swaps: list[int] = []
        for i in range(parity, n - 1, 2):
            if a[i] > a[i + 1]:
                a[i], a[i + 1] = a[i + 1], a[i]
                swaps.append(i)
        rounds.append(swaps)
        if a == sorted(a):
            break
    return rounds, a


def odd_even_sort_program(
    arch: Architecture,
    start: Sequence[str],
    target: Sequence[str],
    *,
    loop_id: str | None = None,
    name: str = "odd_even_reconfiguration",
    arch_spec: str = "arch/ring144_24v.arch.json",
    extra_placement: Mapping[str, str] | None = None,
) -> SortResult:
    """Emit a TSIR program that permutes `start` into `target` by odd-even sort.

    `start` and `target` are ion ids indexed by slot along the loop.  Both must be
    permutations of the same ion set.
    """
    if sorted(start) != sorted(target):
        raise ValueError("start and target must be permutations of the same ion set")
    n = len(start)
    if n < 2:
        raise ValueError("odd-even sort needs at least two slots")

    loop_id = loop_id or next(iter(arch.device.loops))
    loop = arch.device.loops[loop_id]
    if len(loop.nodes) < n:
        raise ValueError(
            f"loop {loop_id!r} has {len(loop.nodes)} slots but the placement names {n}"
        )
    nodes = list(loop.nodes[:n])

    # each ion's key is where it has to end up; sorting by key realizes the permutation
    want = {ion: i for i, ion in enumerate(target)}
    rounds, final = odd_even_rounds([want[ion] for ion in start])

    prog = TSIR(name=name, arch_spec=arch_spec)
    placement = {ion: nodes[i] for i, ion in enumerate(start)}
    if extra_placement:
        placement.update(extra_placement)
    prog.add(
        Instruction(
            type="init",
            id=prog.next_id(),
            placement=placement,
            quanta={ion: 0.0 for ion in placement},
            meta={"scheme": "odd_even_transposition_sort"},
        )
    )

    order = list(start)
    cycles = 0
    transpositions = 0
    active_rounds = 0
    widest = 0
    for r, swaps in enumerate(rounds):
        if not swaps:
            continue
        active_rounds += 1
        widest = max(widest, len(swaps))
        transpositions += len(swaps)

        # phase 1 -- every participating pair merges leftward, one class, variadic
        prog.add(
            Instruction(
                type="simd",
                id=prog.next_id(),
                cls=MERGE_CLASS,
                mode="inter",
                participants=tuple(
                    Participant(order[i + 1], nodes[i + 1], nodes[i]) for i in swaps
                ),
                holds=tuple(f"{nodes[i]}" for i in swaps),
                meta={"round": r, "phase": "merge", "pairs": len(swaps)},
            )
        )
        # phase 2 -- the other ion of each pair splits back out rightward.  A different
        # global direction, so R4 makes it a different class and WISE a different cycle.
        prog.add(
            Instruction(
                type="simd",
                id=prog.next_id(),
                cls=SPLIT_CLASS,
                mode="inter",
                participants=tuple(
                    Participant(order[i], nodes[i], nodes[i + 1]) for i in swaps
                ),
                holds=tuple(f"{nodes[i + 1]}" for i in swaps),
                meta={"round": r, "phase": "split", "pairs": len(swaps)},
            )
        )
        cycles += 2
        for i in swaps:
            order[i], order[i + 1] = order[i + 1], order[i]

    reached = order == list(target)
    notes = [
        f"odd-even transposition sort: {active_rounds} active rounds of at most {n}, "
        f"{transpositions} transpositions, {widest} at most in one round",
        "each transposition is one merge (leftward) plus one split (rightward): two "
        "classes, so WISE cannot put them in one cycle",
    ]
    if not reached:
        notes.append("the emitted program does NOT reach the target permutation")

    # With unconstrained wiring the merge phase of a round and the split phase of the
    # previous round act on disjoint pairs (odd rounds and even rounds interleave), so
    # they pipeline: the makespan is one cycle per active round plus one to drain.
    unconstrained = active_rounds + 1 if active_rounds else 0

    return SortResult(
        program=prog,
        rounds=len(rounds),
        active_rounds=active_rounds,
        transpositions=transpositions,
        cycles=cycles,
        cycles_unconstrained=unconstrained,
        max_transpositions_in_a_round=widest,
        reached_target=reached,
        notes=notes,
    )


def cyclic_shift_target(start: Sequence[str], k: int) -> list[str]:
    """The placement a rigid rotation by `k` produces -- the only permutations rotation
    can reach at all, since a loop shift generates exactly the cyclic group."""
    n = len(start)
    out = [""] * n
    for i, ion in enumerate(start):
        out[(i + k) % n] = ion
    return out
