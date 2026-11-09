"""FormalRV's Gidney 2025 listing on a floorplan of tiles, timed by its programs (docs/FULLSTACK_PLAN.md, S10 step 3).

The listing fixes the order of the work; this module fixes where it happens and how long it takes.

Where.  Hot wires never move; a register of bits is a strip of tiles, three wires a bit in a column, round a band
three tiles high: from the top, wire 3i + 1 turned (its Z̄ on its lower edge), the band, wire 3i (Z̄ on its upper
edge), a free row, wire 3i + 2.  So a factory's outputs on the band's edges face the two wires an adder reads with Z̄.
A patch a request makes lands beside the first wire it meets, its Z̄ facing the wire's, or on a free neighbour if a
live patch holds that tile; one that meets only cold blocks (the QFT's) lands on the port row below the registers.
A request holds its tile from `prep` to `free`.

When.  As soon as the listing's order allows: an item starts when its wires, patches, tiles and the stretches of
seam its program walks (`catprog.cells`) are free, and a gadget instance is placed whole, its internal schedule
kept, at the earliest moment each of its resources is free at the offset it first uses it.  Guarded quantum work
waits for the local outcomes before it, a decode later than the work that made them; a guard on a `source` record
reads an outcome of long ago.  Frames take no time here: the listing's 40 ns each are 0.2 s a prime.

    Plan(reader)                        the floorplan and the walk
    plan.shapes(key)                    {shape: uses}: every program a gadget instance needs
    plan.schedule(key, times)           (end, first use, last use) of an instance, by the program times
    plan.flat(key, times)               the same, with no instance kept whole below `key`: each item as soon as it can run
    plan.serial(key, times)             {kind: µs} of its work, as if nothing ran at once: where the time goes
    plan.intervals(key, times)          every resource's busy intervals, flat, for checking a bounded instance
"""
from __future__ import annotations

import math
from collections import Counter

from ...compile.tiled_cat import Target
from .gidney import Anc, Reader

__all__ = ["Plan", "program_of", "strips", "Times"]

ROUND_US, PREP_US, READ_US, H_US, DECODE_US = 5822.0, 345.0, 440.0, 5.0, 5822.0
PREP_Y_US = 43862.0
#: provisional until step 4 builds them: the paper's 150 and 23 rounds, at our round
MAGIC_CCZ_US, MAGIC_T_US = 150 * ROUND_US, 23 * ROUND_US
#: a walk along a seam, per tile: a row of 12 traps, a hop each; down a column, two hops a row
ROW_TILE_US, COLUMN_TILE_US = 12 * 188.0, 12 * 376.0
#: a yoked access's repetition, cat ladder across the block's 102-trap side, half of it on average
COLD_REP_US = 30000.0 + 2 * ROW_TILE_US * 8


def strips() -> list:
    """(first wire, bits, wires a bit) of each register, in the order they stack."""
    return [(0, 32, 3), (351, 32, 2), (276, 22, 3), (96, 6, 1), (342, 9, 1), (102, 34, 3), (204, 4, 1), (208, 22, 3),
            (274, 2, 1)]


def program_of(shape: tuple) -> tuple:
    """The program a shape runs: a wide cat's control is reckoned apart, so its program is (width, mask)."""
    return ("wide", shape[1], shape[3]) if shape[0] == "wide" else shape


class Times(dict):
    """{program: µs per repetition}, from the checked programs.  A wide cat's program has its control above the row's
    middle; one further off adds cat 0's walk there and back, composed (`tests/test_gidney_concrete.py` checks the
    rule on built programs)."""

    def rep(self, shape: tuple) -> float:
        return self[shape]

    def wide(self, width: int, control: tuple, mask: tuple | None = None) -> float:
        return self[("wide", width, mask)] + 2 * (abs(control[0]) * ROW_TILE_US + max(0, abs(control[1]) - 1) * COLUMN_TILE_US)


class Plan:
    def __init__(self, reader: Reader):
        self.reader = reader
        self.tile: dict = {}
        self.turned: dict = {}
        self.band: dict = {}
        y = 0
        self.width = 0
        for base, bits, stride in strips():
            if stride == 3:         # w1 turned, the band, w0, a free row, w2
                rows = {1: (y, True), 0: (y + 4, False), 2: (y + 6, False)}
                band, y = (y + 1, y + 3), y + 7
            elif stride == 2:       # w1 turned, the band, w0
                rows = {1: (y, True), 0: (y + 4, False)}
                band, y = (y + 1, y + 3), y + 5
            else:                   # the band, its wires
                rows = {0: (y + 3, False)}
                band, y = (y, y + 2), y + 4
            for k, (row, turned) in rows.items():
                for i in range(bits):
                    w = base + stride * i + k
                    self.tile[w], self.turned[w], self.band[w] = (i, row), turned, band
            self.width = max(self.width, bits)
        self.port_row = y
        self.height = y + 1
        missing = [w for w in range(415) if w not in self.tile]
        if missing:
            raise ValueError(f"wires with no tile: {missing}")
        self.wire_tiles = set(self.tile.values())
        self.clashes = Counter()
        self.clock = 0.0
        self.flat_from: set = set()     # gadgets `schedule` writes out whole, rather than placing their instances rigidly
        self.source_waits = False       # whether a guard on a `source` record waits for the decode before it, as a guard on `c[..]` does
        self._memo: dict = {}

    def _on_clock(self, t: float) -> float:
        """The first round the machine's clock (`clock` µs a round; 0: no clock) starts at or after t."""
        return math.ceil(t / self.clock - 1e-9) * self.clock if self.clock else t

    @property
    def tiles(self) -> int:
        return self.width * self.height

    # ---------------------------------------------------------------- a frame's places
    def _frame(self, items) -> tuple:
        first, requests = {}, {}
        for it in items:
            if it[0] == "prep":
                requests[it[1]] = it[2]
            elif it[0] == "magic":
                requests[it[2]] = -abs(requests.get(it[2], 1))
            elif it[0] in ("rep", "cold"):
                tg = it[2] if it[0] == "rep" else it[5]
                wires = [p for p, _ in tg if isinstance(p, int) and p >= 0]
                for p, _ in tg:
                    if isinstance(p, Anc) and p not in first and wires:
                        first[p] = wires[0]
        return first, requests

    def _place(self, p: Anc, first, requests, spot, live) -> tuple:
        """A patch's tile, chosen when it is requested, among those no live patch holds; (x, y, turned)."""
        t = spot.get(p)
        if t is not None:
            return t
        w = first.get(p)
        if w is None:
            t = (next(x for x in range(self.width) if (x, self.port_row) not in live), self.port_row, False)
        else:
            x, y = self.tile[w]
            turned = not self.turned[w]     # its Z̄ faces the wire's
            row = y + 1 if self.turned[w] else y - 1
            if requests.get(p.base, 1) < 0:
                top, bottom = self.band[w]
                row = top if self.turned[w] else bottom
            t = next(((x + a, row, turned) for a in (0, 1, -1, 2, -2, 3, -3)
                      if (x + a, row) not in live and (x + a, row) not in self.wire_tiles and 0 <= x + a < self.width),
                     (x, row, turned))
        spot[p] = t
        live[t[:2]] = p.base
        return t

    def _target(self, p, letter, first, requests, spot, live) -> Target:
        if isinstance(p, int):
            x, y = self.tile[p]
            return Target(x, y, self.turned[p], letter)
        return Target(*self._place(p, first, requests, spot, live), letter)

    def _resolve(self, key):
        """A frame's items, with places and programs: (kind, ...) in stream order."""
        items = self.reader.flat(key) if key is not None else self.reader.program()
        first, requests = self._frame(items)
        spot, live = {}, {}
        i, n = 0, len(items)

        def res(p):
            return ("wire", p) if isinstance(p, int) and p >= 0 else (("cold", p) if isinstance(p, int)
                                                                     else ("patch", p.base, p.index))
        while i < n:
            it = items[i]
            k = it[0]
            if k in ("guard", "endguard", "decode"):
                yield it
            elif k == "call":
                yield ("call", it[1], live)
            elif k == "prep":
                for j in range(it[2]):
                    p = Anc(it[1], j)
                    t = self._place(p, first, requests, spot, live)
                    yield ("work", PREP_US, [("tile", t[:2]), res(p)], False, "prep")
            elif k == "free":
                for j in range(abs(requests.get(it[1], 1))):
                    p = Anc(it[1], j)
                    t = spot.pop(p, None)
                    if t is not None:
                        live.pop(t[:2], None)
                        yield ("free", ("tile", t[:2]), res(p))
            elif k == "magic":
                cnt = abs(requests.get(it[2], 1))
                ps = [Anc(it[2], j) for j in range(cnt)]
                tiles = [self._place(p, first, requests, spot, live) for p in ps]
                busy = [res(p) for p in ps]
                if it[1] == "|AutoCCZ>":
                    # the factory: six columns of the band round its first output, its 18 tiles
                    x = tiles[0][0]
                    w = first.get(ps[0], 0)
                    top, bottom = self.band[w]
                    x0 = max(0, min(x - 1, self.width - 6))
                    busy += [("tile", (x0 + a, row)) for a in range(6) for row in range(top, bottom + 1)]
                    yield ("work", MAGIC_CCZ_US, busy, False, "|AutoCCZ>")
                else:
                    yield ("work", MAGIC_T_US if it[1] == "|T>" else PREP_Y_US,
                           busy + [("tile", t[:2]) for t in tiles], False, it[1])
            elif k == "rep":
                tg = it[2]
                targets = [self._target(p, l, first, requests, spot, live) for p, l in tg]
                busy = [res(p) for p, _ in tg]
                if len(tg) > 4 or len(it) > 4:
                    control, row = targets[0], targets[1:]
                    # the row's tiles, by cat ion: cat i + 1 acts on the row's tile i
                    places = list(range(len(row))) if len(it) <= 4 else [o - 1 for o in it[4][1:]]
                    width = len(row) if len(it) <= 4 else it[3] - 3
                    mask = None if len(it) <= 4 else tuple(places)
                    x0, y0 = row[0].x - places[0], row[0].y
                    if not all(t.letter == "X" and not t.turned and (t.x, t.y) == (x0 + i, y0)
                               for t, i in zip(row, places)) or control.letter != "X":
                        raise ValueError(f"a register-wide cat that is not a row of X̄: {targets}")
                    shape = ("wide", width, (control.x - x0 - width // 2, control.y - y0), mask)
                    # cat 0's walk to the control, beyond its program: along the row's lane, then its column
                    middle = x0 + width // 2
                    busy += [("h", x, y0) for x in range(min(control.x, middle), max(control.x, middle) + 1)]
                    busy += [("v", control.x, v) for v in range(min(control.y, y0), max(control.y, y0) + 1)]
                else:
                    shape = ("rep", _normal(targets))
                    x0, y0 = targets[0].x, targets[0].y
                yield ("rep", it[1], shape, busy, (x0, y0))
            elif k == "cold":
                busy = [res(p) for p, _ in it[5]] + [("cold", it[2])]
                yield ("work", COLD_REP_US * it[1], busy, True, "cold")
            elif k == "rounds":
                p = it[2]
                if isinstance(p, Anc):
                    self._place(p, first, requests, spot, live)
                yield ("work", ROUND_US * it[1], [res(p)], True, "rounds")
            elif k in ("read", "h"):
                p = it[1]
                while i + 1 < n and items[i + 1][0] == k and items[i + 1][1] == p:
                    i += 1
                yield ("work", READ_US if k == "read" else H_US, [res(p)], False, k)
            i += 1

    # ---------------------------------------------------------------- what it needs
    def shapes(self, key, memo: dict | None = None) -> Counter:
        """{shape: uses} under one instance (None: the whole program)."""
        memo = {} if memo is None else memo
        if key in memo:
            return memo[key]
        out: Counter = Counter()
        for it in self._resolve(key):
            if it[0] == "call":
                out.update(self.shapes(it[1], memo))
            elif it[0] == "rep":
                out[it[2]] += 1
        memo[key] = out
        return out

    def serial(self, key, times: Times, memo: dict | None = None) -> Counter:
        """{kind: µs of work} under one instance, as if nothing ran at once; a repetition's kind is its letters."""
        memo = {} if memo is None else memo
        if key in memo:
            return memo[key]
        out: Counter = Counter()
        for it in self._resolve(key):
            if it[0] == "call":
                out.update(self.serial(it[1], times, memo))
            elif it[0] == "work":
                out[it[4]] += it[1]
            elif it[0] == "rep":
                shape = it[2]
                if shape[0] == "wide":
                    out["wide cat"] += it[1] * times.wide(shape[1], shape[2], shape[3])
                else:
                    out["cat " + "".join(sorted(t.letter for t in shape[1]))] += it[1] * times.rep(shape)
        memo[key] = out
        return out

    # ---------------------------------------------------------------- when
    def schedule(self, key, times: Times, cells: dict) -> tuple:
        """(end, {resource: first use}, {resource: last use}) of an instance, µs from its start."""
        got = self._memo.get(key)
        if got is not None:
            return got
        if key is not None and key[0] in self.flat_from:
            got = self._memo[key] = self.flat(key, times, cells)
            return got
        ready: dict = {}
        fst: dict = {}
        end = known = latest = floor = 0.0
        depth = 0

        def run(dur, busy, on_clock=False):
            nonlocal end
            t = floor if depth else 0.0
            for r in busy:
                v = ready.get(r, 0.0)
                if v > t:
                    t = v
            if on_clock:
                t = self._on_clock(t)
            e = t + dur
            for r in busy:
                ready[r] = e
                if r not in fst:
                    fst[r] = t
            if e > end:
                end = e
            return e

        for it in self._resolve(key):
            k = it[0]
            if k == "guard":
                if depth == 0:
                    floor = 0.0 if it[1] and not self.source_waits else known
                depth += 1
            elif k == "endguard":
                depth -= 1
            elif k == "decode":
                known = max(known, latest + DECODE_US)
            elif k == "work":
                e = run(it[1], it[2], it[4] in ("merge", "rounds"))
                if it[3]:
                    latest = e
            elif k == "rep":
                _, reps, shape, busy, (x0, y0) = it
                dur = times.wide(shape[1], shape[2], shape[3]) if shape[0] == "wide" else times.rep(shape)
                row = cells[program_of(shape)]
                latest = run(dur * reps, list(busy) + [(c[0], c[1] + x0, c[2] + y0) for c in row])
            elif k == "free":
                _, tile, patch = it
                v = ready.get(patch, 0.0)
                if v > ready.get(tile, 0.0):
                    ready[tile] = v
                    fst.setdefault(tile, v)
            elif k == "call":
                s_end, s_first, s_last = self.schedule(it[1], times, cells)
                t = floor if depth else 0.0
                for r, v in s_first.items():
                    d = ready.get(r, 0.0) - v
                    if d > t:
                        t = d
                t = self._on_clock(t)
                live = it[2]
                for r, v in s_last.items():
                    ready[r] = t + v
                    if r not in fst:
                        fst[r] = t + s_first[r]
                    if r[0] == "tile" and r[1] in live:
                        self.clashes[it[1][0]] += 1
                if t + s_end > end:
                    end = t + s_end
                latest = known = max(known, t + s_end)
        got = self._memo[key] = (end, {r: v for r, v in fst.items() if r[0] != "patch"},
                                 {r: v for r, v in ready.items() if r[0] != "patch"})
        return got

    def flat(self, key, times: Times, cells: dict, out: dict | None = None, trace=None) -> tuple:
        """`schedule`'s (end, first use, last use), with every gadget instance below `key` written out: an item starts when its wires, patches,
        tiles and the outcomes it waits for are ready, in the listing's order on each resource.  Sound as `schedule` is: nothing shares a resource at
        once and nothing passes what it waits for; what it gives up is only that all instances of a gadget keep one internal timing, which the machine does
        not need, since a program is compiled once and only its start differs.  A request's patch is named within its instance.  `out`, for a
        bounded instance: {resource: [(start, end)]}; `trace(path, item, start, end)`, for every work item as it is placed, `path` the keys of
        the instances it is inside, outermost first (a page draws the schedule from it)."""
        ready: dict = {}
        fst: dict = {}
        count = [0]
        path: list = []
        began = [0.0]

        def walk(key, floor0):
            count[0] += 1
            me = count[0]
            end = known = latest = floor = floor0
            depth = 0
            path.append(key)

            def own(r):
                return r + (me,) if r[0] == "patch" else r

            def run(dur, busy, on_clock=False):
                nonlocal end
                busy = [own(r) for r in busy]
                t = floor if depth else floor0
                for r in busy:
                    v = ready.get(r, 0.0)
                    if v > t:
                        t = v
                if on_clock:
                    t = self._on_clock(t)
                began[0] = t
                e = t + dur
                for r in busy:
                    ready[r] = e
                    if r not in fst:
                        fst[r] = t
                    if out is not None:
                        out.setdefault(r, []).append((t, e))
                if e > end:
                    end = e
                return e

            for it in self._resolve(key):
                k = it[0]
                if k == "guard":
                    if depth == 0:
                        floor = floor0 if it[1] and not self.source_waits else known
                    depth += 1
                elif k == "endguard":
                    depth -= 1
                elif k == "decode":
                    known = max(known, latest + DECODE_US)
                elif k == "work":
                    e = run(it[1], it[2], it[4] in ("merge", "rounds"))
                    if trace is not None:
                        trace(path, it, began[0], e)
                    if it[3]:
                        latest = e
                elif k == "rep":
                    _, reps, shape, busy, (x0, y0) = it
                    dur = times.wide(shape[1], shape[2], shape[3]) if shape[0] == "wide" else times.rep(shape)
                    row = cells[program_of(shape)]
                    latest = run(dur * reps, list(busy) + [(c[0], c[1] + x0, c[2] + y0) for c in row])
                elif k == "free":
                    _, tile, patch = it
                    v = ready.get(own(patch), 0.0)
                    if v > ready.get(tile, 0.0):
                        ready[tile] = v
                        fst.setdefault(tile, v)
                elif k == "call":
                    e = walk(it[1], floor if depth else floor0)
                    if e > end:
                        end = e
                    if e > known:
                        latest = known = e
            path.pop()
            return end

        end = walk(key, 0.0)
        return end, {r: v for r, v in fst.items() if r[0] != "patch"}, {r: v for r, v in ready.items() if r[0] != "patch"}

    def intervals(self, key, times: Times, cells: dict, at: float = 0.0, out: dict | None = None) -> dict:
        """{resource: [(start, end)]} of an instance, flat: the same placing as `schedule`, written out so that
        a test can see no resource is used twice at once.  Only for a bounded instance."""
        out = {} if out is None else out
        ready: dict = {}
        end = known = latest = floor = 0.0
        depth = 0

        def run(dur, busy, on_clock=False):
            nonlocal end
            t = floor if depth else 0.0
            for r in busy:
                v = ready.get(r, 0.0)
                if v > t:
                    t = v
            if on_clock:
                t = self._on_clock(t)
            e = t + dur
            for r in busy:
                ready[r] = e
                out.setdefault(r, []).append((at + t, at + e))
            if e > end:
                end = e
            return e

        for it in self._resolve(key):
            k = it[0]
            if k == "guard":
                if depth == 0:
                    floor = 0.0 if it[1] and not self.source_waits else known
                depth += 1
            elif k == "endguard":
                depth -= 1
            elif k == "decode":
                known = max(known, latest + DECODE_US)
            elif k == "work":
                e = run(it[1], it[2], it[4] in ("merge", "rounds"))
                if it[3]:
                    latest = e
            elif k == "rep":
                _, reps, shape, busy, (x0, y0) = it
                dur = times.wide(shape[1], shape[2], shape[3]) if shape[0] == "wide" else times.rep(shape)
                row = cells[program_of(shape)]
                latest = run(dur * reps, list(busy) + [(c[0], c[1] + x0, c[2] + y0) for c in row])
            elif k == "free":
                _, tile, patch = it
                v = ready.get(patch, 0.0)
                if v > ready.get(tile, 0.0):
                    ready[tile] = v
            elif k == "call":
                s_end, s_first, s_last = self.schedule(it[1], times, cells)
                t = floor if depth else 0.0
                for r, v in s_first.items():
                    d = ready.get(r, 0.0) - v
                    if d > t:
                        t = d
                t = self._on_clock(t)
                self.intervals(it[1], times, cells, at + t, out)
                for r, v in s_last.items():
                    ready[r] = t + v
                if t + s_end > end:
                    end = t + s_end
                latest = known = max(known, t + s_end)
        return out


def _normal(targets) -> tuple:
    x0, y0 = min(t.x for t in targets), min(t.y for t in targets)
    return tuple(Target(t.x - x0, t.y - y0, t.turned, t.letter) for t in targets)
