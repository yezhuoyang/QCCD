"""Pass 2 -- interaction order: which ancilla serves which check, and when.

The shipped schedule binds one dock to each check for a whole wave and runs six waves.
Cyclone assigns dynamically instead, and PLAN §7 records that this is "a large part of why
it needs only 2 rotations". This pass is that difference, made switchable.

The scheduling problem, exactly
------------------------------
Given a placement, check `c` at dock `d` has its six contacts at offsets
`(d - slot(q)) mod capacity`, an arc of width `window(c)` whose *position* moves with `d`
in steps of the dock spacing. The ancilla at `d` holds `c`'s syndrome for that whole arc.

So: pack 144 arcs onto 24 docks, arcs on one dock disjoint, minimizing the total offset
sweep. That is list scheduling on 24 machines, and the greedy "put it where it finishes
earliest" rule is what `dynamic` does.

Two conflicts that look like they need handling do not:

* **two checks wanting the same ion at once** -- an ion sits at exactly one dock at each
  offset, so if two checks need it simultaneously they need the same dock, and disjoint
  arcs already forbid that;
* **two contacts at one offset** -- distinct docks hold distinct ions, so a whole offset's
  worth of contacts is always simultaneously legal. That is why pass 4 gets a full batch
  for free once this pass has done its job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from ..codes.bb import Check
from .place import window

__all__ = ["Assignment", "Binding", "bind_dynamic", "bind_fixed_waves"]


@dataclass(frozen=True)
class Assignment:
    """One check, its dock, and the offset arc its ancilla is busy for."""

    check: Check
    dock: int
    start: int  # absolute offset in the sweep (not reduced mod capacity)
    end: int
    contacts: tuple[tuple[int, int], ...]  # (absolute offset, data qubit)

    @property
    def width(self) -> int:
        return self.end - self.start


@dataclass
class Binding:
    assignments: list[Assignment] = field(default_factory=list)
    sweep: int = 0  # total offsets traversed
    capacity: int = 144
    n_docks: int = 24
    strategy: str = "dynamic"
    notes: list[str] = field(default_factory=list)

    @property
    def revolutions(self) -> float:
        return self.sweep / self.capacity if self.capacity else 0.0

    def contacts_by_offset(self) -> dict[int, list[tuple[Assignment, int]]]:
        out: dict[int, list[tuple[Assignment, int]]] = {}
        for a in self.assignments:
            for off, q in a.contacts:
                out.setdefault(off, []).append((a, q))
        return out


def _arc(slots: Sequence[int], capacity: int) -> tuple[int, list[int]]:
    """The anchor slot and the relative contact offsets of a check.

    A contact happens at offset `(dock - slot) mod capacity`, so moving *forward* through
    the sweep moves *backward* through slots. The member whose contact comes first is
    therefore the one at the END of the narrowest slot arc, not the beginning -- and
    anchoring on the beginning instead makes every arc span nearly a full revolution,
    which is exactly as expensive as it sounds.
    """
    s = sorted(set(x % capacity for x in slots))
    best_gap, best_i = -1, 0
    for i in range(len(s)):
        gap = (s[(i + 1) % len(s)] - s[i]) % capacity
        if gap > best_gap:
            best_gap, best_i = gap, i
    anchor = s[best_i]  # the slot just BEFORE the biggest gap: the arc's far end
    return anchor, [(anchor - x) % capacity for x in s]


def bind_dynamic(
    checks: Sequence[Check],
    slot_of: Mapping[int, int],
    docks: Sequence[int],
    capacity: int,
    *,
    order: str = "widest_first",
    max_active: int | None = None,
) -> Binding:
    """Event-driven sweep: advance the rotation, start whatever can start.

    ``max_active`` caps how many docks may hold a check at once -- the artifact's
    `active_contact_limit`, which is a real hardware bound on simultaneous contacts and
    not a scheduling preference. Setting it to 1 serializes the whole round, which is
    what makes it a usable knob for showing that a benchmark can tell good from bad.

    Earliest-finish list scheduling is the wrong rule here, and measurably so -- it does
    worse than the fixed-wave baseline it is supposed to beat. The reason is the
    congruence: check `c` can only begin at a dock `d` when the sweep offset satisfies
    `t = d - anchor(c) (mod capacity)`, so "put it where it finishes earliest" makes a
    check wait up to a whole revolution for its moment, and greedily burns the docks that
    were free early.

    Sweeping instead inverts the lookup. At offset `t` a free dock `d` can start exactly
    those checks whose anchor is `(d - t) mod capacity`, which is an O(1) index. Every
    dock is then offered a check at every offset it is free, and the packing follows the
    bound instead of fighting it.
    """
    anchors: dict[int, tuple[int, int]] = {}   # check index -> (anchor slot, width)
    by_anchor: dict[int, list[int]] = {}
    for i, c in enumerate(checks):
        anchor, rel = _arc([slot_of[q] for q in c.members], capacity)
        anchors[i] = (anchor, max(rel))
        by_anchor.setdefault(anchor, []).append(i)
    if order == "widest_first":
        for a in by_anchor:
            by_anchor[a].sort(key=lambda i: -anchors[i][1])

    unassigned = set(range(len(checks)))
    busy_until = {d: -1 for d in docks}
    limit = max_active if max_active else len(docks)
    out: list[Assignment] = []
    t = 0
    guard = capacity * (len(checks) + 2)
    while unassigned and t < guard:
        for d in docks:
            if busy_until[d] >= t or not unassigned:
                continue
            if sum(1 for x in docks if busy_until[x] >= t) >= limit:
                break  # the machine cannot hold another contact open
            want = (d - t) % capacity
            pool = by_anchor.get(want)
            if not pool:
                continue
            pick = next((i for i in pool if i in unassigned), None)
            if pick is None:
                continue
            c = checks[pick]
            contacts = []
            for q in c.members:
                base = (d - slot_of[q]) % capacity
                off = t + (base - t) % capacity
                contacts.append((off, q))
            contacts.sort()
            end = contacts[-1][0]
            out.append(Assignment(c, d, t, end, tuple(contacts)))
            busy_until[d] = end
            unassigned.discard(pick)
        t += 1

    if unassigned:
        raise RuntimeError(
            f"{len(unassigned)} checks unplaced after {guard} offsets; the dock set "
            f"cannot serve this placement")

    sweep = max((a.end for a in out), default=0) + 1
    widths = [a.width for a in out]
    per_dock: dict[int, int] = {}
    for a in out:
        per_dock[a.dock] = per_dock.get(a.dock, 0) + 1
    return Binding(
        assignments=out, sweep=sweep, capacity=capacity, n_docks=len(docks),
        strategy="dynamic",
        notes=[
            f"event-driven sweep over {len(docks)} docks, {order}",
            f"arc widths mean {sum(widths) / max(1, len(widths)):.1f}, "
            f"max {max(widths, default=0)}",
            f"checks per dock {min(per_dock.values())}-{max(per_dock.values())}",
            f"at most {limit} contacts open at once",
            f"sweep {sweep} offsets = {sweep / capacity:.2f} revolutions",
        ],
    )


def bind_fixed_waves(
    checks: Sequence[Check],
    slot_of: Mapping[int, int],
    docks: Sequence[int],
    capacity: int,
    *,
    waves: int = 6,
) -> Binding:
    """The shipped rule: one dock per check for a whole wave, waves run in turn.

    Kept as the baseline the dynamic binding is measured against -- and because PLAN §7.1
    is explicit that a mismatched policy makes any architecture look bad, so the
    comparison has to be able to run both.
    """
    groups: list[list[Check]] = [[] for _ in range(waves)]
    for i, c in enumerate(checks):
        groups[i % waves].append(c)
    out: list[Assignment] = []
    cursor = 0
    for group in groups:
        wave_end = cursor
        for j, c in enumerate(group):
            dock = docks[j % len(docks)]
            contacts = []
            for q in c.members:
                base = (dock - slot_of[q]) % capacity
                off = base + ((cursor - base + capacity - 1) // capacity) * capacity \
                    if cursor > base else base
                contacts.append((off, q))
            contacts.sort()
            out.append(Assignment(c, dock, contacts[0][0], contacts[-1][0],
                                  tuple(contacts)))
            wave_end = max(wave_end, contacts[-1][0])
        cursor = wave_end + 1
    sweep = max((a.end for a in out), default=0) + 1
    return Binding(
        assignments=out, sweep=sweep, capacity=capacity, n_docks=len(docks),
        strategy="fixed",
        notes=[f"{waves} waves run to completion in turn (ancilla-reuse rule)",
               f"sweep {sweep} offsets = {sweep / capacity:.2f} revolutions"],
    )
