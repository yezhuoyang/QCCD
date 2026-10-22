"""Refresh: syndrome rounds wherever a block would otherwise wait past its coherence budget.

A surface-code block resting in a logical zone is not being corrected: no ancilla touches
it.  G8 bounds how long that may last (`coherence_budget`, one second here).  A program that
keeps a block waiting longer -- Shor's work register waits for iteration 3 -- needs syndrome
rounds in between, and where they go is a scheduling question, not the program's: the
program says what to compute, and the compiler keeps the blocks alive while it does.

`schedule_with_refresh` schedules, finds every gap longer than `margin` of the budget,
inserts `se BLOCK 1` instructions (marked `refresh`, each with the earliest time it may run)
at the point of the issue order where the schedule reached that time, and schedules again,
until no gap is left or `rounds` runs out.  A refresh changes no logical state (a syndrome
round is the identity on the code space, and the flat sign-off and the contract checker
both say so), so the program means what it meant.
"""

from __future__ import annotations

import copy

from .algorithm import Algorithm, Instr
from .city import schedule
from .place_checks import COHERENCE_BUDGET_US, SE_OPS

__all__ = ["schedule_with_refresh", "gaps", "with_refreshes"]


def gaps(gir: dict) -> dict[str, list[tuple[float, float]]]:
    """Per block, every stretch without syndrome extraction: (from, to), as G8 measures it."""
    out: dict[str, list[tuple[float, float]]] = {}
    events = gir["events"]
    for b, info in gir["blocks"].items():
        ions = set(info["ions"])
        times = sorted((e[1], e[2]) for e in events if e[4].startswith(SE_OPS)
                       and any(isinstance(g, int) and g in ions for _, g in e[8]))
        end = info.get("read", gir["makespan_us"])
        t = times[0][1] if times else info["born"]
        rows = []
        for a, z in times[1:] + [(end, end)]:
            rows.append((t, a))
            t = max(t, z)
        out[b] = rows
    return out


def with_refreshes(alg: Algorithm, adds: list[tuple[int, str, float]]) -> Algorithm:
    """A copy of `alg` with `se BLOCK 1` inserted before instruction `index` for each
    `(index, block, not_before)`."""
    new = copy.copy(alg)
    by_pos: dict[int, list[tuple[str, float]]] = {}
    for idx, b, t in adds:
        by_pos.setdefault(idx, []).append((b, t))
    out: list[Instr] = []
    remap: dict[int, int] = {}
    for ins in alg.instructions:
        for b, t in sorted(by_pos.get(ins.index, []), key=lambda x: x[1]):
            out.append(Instr(index=len(out), line=ins.line, text=f"se {b} 1    # refresh",
                             kind="se", blocks=[b], rounds=1, not_before=round(t, 3),
                             refresh=True))
        c = copy.copy(ins)
        remap[ins.index] = len(out)
        c.index = len(out)
        out.append(c)
    new.instructions = out
    new.calls = [dict(c, first=remap.get(c["first"], c["first"]),
                      last=remap.get(c["last"], c["last"])) for c in alg.calls]
    return new


def schedule_with_refresh(alg: Algorithm, lib, leaves, *, budget_us: float = COHERENCE_BUDGET_US,
                          margin: float = 0.8, rounds: int = 8, parallel: bool = True,
                          log=None) -> tuple[Algorithm, dict]:
    """(the program with its refreshes, its schedule)."""
    gir = schedule(alg, lib, leaves, log=log, parallel=parallel)
    for _ in range(rounds):
        limit = margin * budget_us
        start = {}
        for e in gir["events"]:
            start.setdefault(e[6], e[1])
            start[e[6]] = min(start[e[6]], e[1])
        uses: dict[str, list[int]] = {}
        for ins in alg.instructions:
            for b in ins.blocks:
                uses.setdefault(b, []).append(ins.index)
        adds: list[tuple[int, str, float]] = []
        for b, rows in gaps(gir).items():
            if b not in uses:
                continue                      # a resource block the gadgets made, not the program's
            order = sorted(uses[b])
            for t_from, t_to in rows:
                if t_to - t_from <= budget_us:
                    continue
                # refresh every `limit` of the gap.  Each round is issued just before the
                # block's first use that starts after it -- the instruction that would take the
                # block out of where it rests -- so it runs while the block is resting there,
                # not after (a gap ends at the next op that extracts syndromes, which may come
                # several uses later)
                t = t_from + limit
                while t < t_to - 1e-6:
                    u = next((i for i in order if start.get(i, float("inf")) >= t - 1e-6), None)
                    if u is None:
                        break
                    adds.append((u, b, t))
                    t += limit
        if not adds:
            break
        alg = with_refreshes(alg, adds)
        gir = schedule(alg, lib, leaves, log=log, parallel=parallel)
    gir["refreshes"] = sum(1 for i in alg.instructions if i.refresh)
    return alg, gir
