"""Pass 1 -- placement.  PLAN §7.

On a rotating ring the only movement is a rigid shift, so a contact between check `c` and
data qubit `q` can happen at exactly one rotation offset::

    R(c, q) = (dock(c) - slot(q))  mod  capacity

A check's six contacts therefore land at six offsets, and the ancilla holding that check's
syndrome is busy from the first to the last. The *width of that interval is the spread of
the check's members' slots* -- which placement, and only placement, decides.

That makes the objective sharp. With 24 docks and 144 checks, six checks share a dock, so

    revolutions  >=  (sum of check windows) / (n_docks * capacity)

and the hop count can never beat it. Identity placement gives a mean window of 83.7 and
so needs at least 3.7 revolutions; interleaving the code's two blocks gives 38.8 and needs
1.9 -- which is where Cyclone's "exactly 2 rotations" comes from. Annealing starts from
the interleaved seed and improves on it.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from ..codes.bb import BBCode, Check

__all__ = ["Placement", "anneal", "refine", "interleaved_seed", "identity_seed",
           "window", "lower_bound_revolutions"]


def window(slots: Sequence[int], capacity: int) -> int:
    """Width of the narrowest arc of the `capacity`-cycle containing every slot.

    Cyclic, not linear: members at slots 2 and 142 are 4 apart on a 144-ring, not 140.
    """
    s = sorted(set(x % capacity for x in slots))
    if len(s) <= 1:
        return 0
    biggest_gap = max(
        (s[(i + 1) % len(s)] - s[i]) % capacity for i in range(len(s))
    )
    return capacity - biggest_gap


@dataclass
class Placement:
    slot_of: dict[int, int]
    capacity: int
    cost: float = 0.0
    max_window: int = 0
    mean_window: float = 0.0
    iterations: int = 0
    seed_cost: float = 0.0
    notes: list[str] = field(default_factory=list)

    def windows(self, checks: Sequence[Check]) -> list[int]:
        return [window([self.slot_of[q] for q in c.members], self.capacity)
                for c in checks]

    def bound_revolutions(self, checks: Sequence[Check], n_docks: int) -> float:
        return lower_bound_revolutions(self.windows(checks), self.capacity, n_docks)


def lower_bound_revolutions(windows: Sequence[int], capacity: int, n_docks: int) -> float:
    """No schedule can beat this.

    Every check occupies one dock for its whole window, there are `n_docks` docks, and one
    revolution offers `capacity` offsets of dock time each. It is a packing bound, so it
    ignores alignment -- real schedules land above it.
    """
    if not windows or not n_docks:
        return 0.0
    return sum(windows) / (n_docks * capacity)


def identity_seed(code: BBCode) -> dict[int, int]:
    return {q: q for q in range(code.n)}


def interleaved_seed(code: BBCode) -> dict[int, int]:
    """Left qubit `v` and right qubit `half + v` become ring neighbours.

    A BB check takes three qubits from each block, so separating the blocks puts half of
    every check on the far side of the ring. Interleaving them halves the mean window
    before any search runs, which is why it is the seed rather than the answer.
    """
    half = code.n // 2
    out: dict[int, int] = {}
    for v in range(half):
        out[v] = 2 * v
        out[half + v] = 2 * v + 1
    return out


def anneal(
    code: BBCode,
    capacity: int,
    *,
    n_docks: int = 24,
    seed: Mapping[int, int] | None = None,
    iterations: int = 120_000,
    t0: float = 6.0,
    t1: float = 0.02,
    rng_seed: int = 20260820,
    objective: str = "sum",
) -> Placement:
    """Simulated annealing on the check-window objective.

    A move swaps the slots of two data qubits, which changes only the windows of the (at
    most twelve) checks containing them -- so the cost delta is local and the search can
    afford six figures of iterations in pure Python.

    ``objective`` ``"sum"`` minimizes total window, which is the packing bound above;
    ``"minmax"`` minimizes the largest window, which bounds the *latency* of the slowest
    check instead.
    """
    slot_of = dict(seed) if seed is not None else interleaved_seed(code)
    if sorted(slot_of.values()) != sorted(slot_of.values()):
        raise ValueError("seed placement is not a permutation")
    checks = code.checks
    checks_of: dict[int, list[int]] = {}
    for i, c in enumerate(checks):
        for q in c.members:
            checks_of.setdefault(q, []).append(i)

    win = [window([slot_of[q] for q in c.members], capacity) for c in checks]

    def total(ws: Sequence[int]) -> float:
        return float(sum(ws)) if objective == "sum" else float(max(ws))

    cost = total(win)
    seed_cost = cost
    best_slot, best_cost = dict(slot_of), cost
    rng = random.Random(rng_seed)
    qubits = list(slot_of)

    for it in range(iterations):
        temp = t0 * (t1 / t0) ** (it / max(1, iterations - 1))
        a, b = rng.sample(qubits, 2)
        slot_of[a], slot_of[b] = slot_of[b], slot_of[a]
        touched = set(checks_of.get(a, ())) | set(checks_of.get(b, ()))
        new = {i: window([slot_of[q] for q in checks[i].members], capacity)
               for i in touched}
        if objective == "sum":
            delta = sum(new.values()) - sum(win[i] for i in touched)
        else:
            trial = list(win)
            for i, v in new.items():
                trial[i] = v
            delta = max(trial) - cost
        if delta <= 0 or rng.random() < math.exp(-delta / max(temp, 1e-9)):
            for i, v in new.items():
                win[i] = v
            cost += delta if objective == "sum" else 0.0
            if objective != "sum":
                cost = total(win)
            if cost < best_cost:
                best_cost, best_slot = cost, dict(slot_of)
        else:
            slot_of[a], slot_of[b] = slot_of[b], slot_of[a]

    final = [window([best_slot[q] for q in c.members], capacity) for c in checks]
    return Placement(
        slot_of=best_slot, capacity=capacity, cost=best_cost,
        max_window=max(final), mean_window=sum(final) / len(final),
        iterations=iterations, seed_cost=seed_cost,
        notes=[
            f"objective {objective}: {seed_cost:.0f} -> {best_cost:.0f} in "
            f"{iterations:,} swaps",
            f"mean window {sum(final) / len(final):.1f}, max {max(final)}; packing bound "
            f"{lower_bound_revolutions(final, capacity, n_docks):.2f} revolutions",
        ],
    )


def refine(
    slot_of: Mapping[int, int],
    evaluate,
    *,
    steps: int = 400,
    rng_seed: int = 20260820,
    checks: Sequence[Check] | None = None,
) -> tuple[dict[int, int], float, list[str]]:
    """Hill-climb on the TRUE objective, whatever `evaluate` measures.

    Annealing above minimizes a surrogate -- the sum of check windows -- because it has a
    cheap local delta. The surrogate is not the answer: on the gross code it finds a
    placement with a *better* window sum that schedules *worse*, because packing also
    depends on how the arcs align, which no window count can see.

    The real binder costs under a millisecond, so a few hundred true evaluations are
    affordable and settle the question. Accept only strict improvements: this runs after
    annealing has already done the exploring.
    """
    rng = random.Random(rng_seed)
    best = dict(slot_of)
    best_cost = evaluate(best)
    start_cost = best_cost
    qubits = list(best)
    accepted = 0
    for _ in range(steps):
        a, b = rng.sample(qubits, 2)
        trial = dict(best)
        trial[a], trial[b] = trial[b], trial[a]
        cost = evaluate(trial)
        if cost < best_cost:
            best, best_cost = trial, cost
            accepted += 1
    return best, best_cost, [
        f"true-objective refinement: {start_cost:.0f} -> {best_cost:.0f} "
        f"in {steps} evaluations ({accepted} accepted)"
    ]
