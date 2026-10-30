"""`mem_d3f`: the logical memory with its ions resting where the doors are short too.

Codesign, 2026-10-29 (the user: "optimize the gadget geometry ... even reconfigure gadget
layout").  Inside Gidney 2025's carry chain a block goes back to its memory between nearby uses,
and a visit costs more at the doors than in the round: `mem_d3` takes a block in ion by ion in
code order, turning each ion's home to the door, then leaves the same way.  A turn costs 100 us
a slot crossed, a contact batch of the round (dock, CX, undock) about 0.48 ms, and `place()`
chose the homes for the shortest round only.  In slots (a batch priced at 4.8):

    mem_d3   round 30 + 13 batches = 92,  entry 86, exit 81   = 259 a visit
    mem_d3f  round 48 +  9 batches = 91,  entry 28, exit 30   = 149 a visit

Same ring, same docks, same ancillas, same programs: only where each data ion rests and which
dock each check's ancilla lives in.  `door_cost` scores a placement; `search` finds one.
"""
from __future__ import annotations

import random

from .bay import dry_round

__all__ = ["DATA", "DOCKS", "door_cost", "search", "memory_by_doors", "EXTRA_PLACES"]

#: the memory's ring: 24 slots, docks every third slot, doors at TL (slot 0) in, TR (11) out
N, IN, OUT = 24, 0, 11
RING_DOCKS = [1 + 3 * i for i in range(8)]
#: a contact batch of the round, in slots of ring turn
BATCH = 4.8
#: where `search` (seed 11) rests each data ion, and docks each check
DATA = {0: 2, 1: 23, 2: 21, 3: 20, 4: 17, 5: 14, 6: 11, 7: 8, 8: 5}
DOCKS = {"X0": 22, "X1": 13, "X2": 7, "X3": 19, "Z0": 16, "Z1": 1, "Z2": 10, "Z3": 4}


def _short(d: int) -> int:
    d %= N
    return d if d <= N // 2 else N - d


def door_cost(patch, ds: dict, ad: dict) -> tuple[float, int, int] | None:
    """(round, entry slots, exit slots) of a placement -- the round its turns with their restore
    plus its contact batches at `BATCH` each, the ions coming in and going out in code order the
    way `BayProgram.absorb`/`emit` move them; None if the round never finishes."""
    steps, batches = dry_round(patch, ds, ad, N)
    if steps >= 10 ** 9:
        return None
    rnd = steps + min(steps % N, N - steps % N) + BATCH * batches
    off, ent = 0, 0
    for q in range(patch.n):
        want = (IN - ds[q]) % N
        ent += _short(want - off)
        off = want
    ent += _short(-off)
    off, ext = 0, 0
    for q in range(patch.n):
        want = (OUT - ds[q]) % N
        ext += _short(want - off)
        off = want
    return rnd, ent, ext


def search(patch, *, iterations: int = 30000, seed: int = 11, w_round: float = 1.0):
    """Data slots and docks with the fewest slots a visit (round weighted `w_round`): random
    restarts, then single moves kept when they do not cost more."""
    rng = random.Random(seed)
    names = [c.name for c in patch.checks]

    def total(ds, ad):
        s = door_cost(patch, ds, ad)
        return 1e9 if s is None else w_round * s[0] + s[1] + s[2]
    best = None
    for _ in range(12):
        ds = dict(zip(range(patch.n), rng.sample(range(N), patch.n)))
        ad = dict(zip(names, rng.sample(RING_DOCKS, len(names))))
        c = total(ds, ad)
        for _ in range(iterations // 12):
            if rng.random() < 0.4:
                a, b = rng.sample(names, 2)
                ad[a], ad[b] = ad[b], ad[a]
                c2 = total(ds, ad)
                if c2 <= c:
                    c = c2
                else:
                    ad[a], ad[b] = ad[b], ad[a]
            else:
                q, s = rng.randrange(patch.n), rng.randrange(N)
                other = next((x for x in ds if ds[x] == s), None)
                old = ds[q]
                ds[q] = s
                if other is not None:
                    ds[other] = old
                c2 = total(ds, ad)
                if c2 <= c:
                    c = c2
                else:
                    ds[q] = old
                    if other is not None:
                        ds[other] = s
        if best is None or c < best[0]:
            best = (c, dict(ds), dict(ad))
    return best[1], best[2]


def memory_by_doors(*, name: str = "mem_d3f"):
    """`places.memory`, its ions resting at DATA/DOCKS instead of where `place()` puts them.
    (`places.py` is one of the sources every cached place is keyed by, so this place borrows
    its builder rather than changing it.)"""
    from . import places

    def fixed(patch, n_slots, docks, **kw):
        return dict(DATA), dict(DOCKS), dry_round(patch, DATA, DOCKS, n_slots)[0]
    saved = places.place
    places.place = fixed
    try:
        return places.memory(name=name)
    finally:
        places.place = saved


EXTRA_PLACES = {"mem_d3f": memory_by_doors}
