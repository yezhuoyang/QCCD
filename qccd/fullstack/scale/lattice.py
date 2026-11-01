"""The patch-lattice scheduler (docs/FULLSTACK_PLAN.md, slice S9 step 3): a program at the PPM
level run on the stationary-tile machine.

Tiles (x, y) of the machine's lattice (`qccd.qec.experiments.corridor_merge`: tile pitch d + 1).
Long-lived blocks have homes on tiles the caller chooses -- *tile in a sea*: on even tiles, every
other tile a bus -- and never move.  Each instruction runs in program order on its blocks, once
every outcome its guard reads is decoded:

* `ppm` of one letter: a corridor merge.  A corridor through bus tiles free for the merge's whole
  duration, breadth-first from a Z̄ side (X̄ side) of each patch, joined patch by patch -- or none,
  when two patches face each other.
* `ht`: the H̄ gadget (`hadamard_bar`), which takes the bus tile right of its block.
* `prep` of a block with no home: a fresh ancilla, given a bus tile beside the partner of its
  first product, on that product's side, held until its readout.
* `prep` and `read`: the tile's reset or readout; an outcome is decoded `decode_us` later.
  `prep Y` is |Ȳ> made in place (`qccd.qec.experiments.y_memory`, Gidney's), on the tile alone.
* frames (`x`, `z`, `y`) and `bit`: classical, no time.
A guarded instruction reserves its tiles whether or not its guard holds, so the schedule is the
same for every run.  Every block not in a gadget refreshes each round on its own tile.

Each scheduled gadget is a translated copy of a program compiled once for its shape
(`Gadget.shape`, translation-free): a corridor merge, the H̄ gadget, a preparation or a readout --
so checking every distinct shape (every rule, R10) and the schedule (`check`: no two gadgets on
one tile at once, every block's instructions in program order, every guard decoded before it is
read) checks the whole run.

    schedule(stream, homes, bounds, costs)  ->  [Gadget]
    check(stream, gadgets)                   ->  [problem]
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field

__all__ = ["Costs", "Gadget", "schedule", "check", "columns", "LatticeError"]

INF = math.inf
SIDES = {"top": (0, -1), "bottom": (0, 1), "left": (-1, 0), "right": (1, 0)}
OPP = {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}
LETTER_SIDES = {"Z": ("top", "bottom"), "X": ("left", "right")}


class LatticeError(ValueError):
    pass


#: |Ȳ> made in place at the machine's distance: Gidney's preparation (`y_prep_qasm(11)`), tiled
#: on qccd-physics-best, every rule and R10
PREP_Y_US = 43_900.0


@dataclass(frozen=True)
class Costs:
    """Machine times, µs.  The defaults are the Shor machine's (`machine`: d = 11, 25 rounds a
    merge, qccd-physics-best), each measured on its compiled program: a syndrome round 5.8 ms, a
    corridor merge 138.3 ms whatever its shape, H̄ 679.8 ms, a tile's preparation 0.33 ms and
    readout 0.44 ms (the slower basis of each), |Ȳ> in place 43.9 ms; decoding is taken as one
    round -- assumed."""
    round_us: float = 5_800.0
    merge_us: float = 138_300.0
    hbar_us: float = 679_800.0
    prep_us: float = 330.0
    read_us: float = 440.0
    prep_y_us: float = PREP_Y_US
    decode_us: float = 5_800.0


@dataclass
class Gadget:
    kind: str                 # "merge" | "ht" | "prep" | "read"
    t0: float
    t1: float
    tiles: frozenset
    shape: tuple              # translation-free: the program it is a copy of
    line: int                 # its instruction in the stream
    blocks: tuple = ()
    detail: dict = field(default_factory=dict)


def _nb(t, side):
    dx, dy = SIDES[side]
    return (t[0] + dx, t[1] + dy)


class _Board:
    def __init__(self, homes: dict, bounds: tuple):
        self.home_tiles = set(homes.values())
        self.x0, self.y0, self.x1, self.y1 = bounds
        self.busy = defaultdict(list)            # tile -> [(t0, t1, tag)]

    def bus(self, t) -> bool:
        return self.x0 <= t[0] <= self.x1 and self.y0 <= t[1] <= self.y1 and t not in self.home_tiles

    def free(self, t, a, b, own=()) -> bool:
        return all(e <= a or s >= b or tag in own for s, e, tag in self.busy[t])

    def take(self, t, a, b, tag) -> None:
        self.busy[t].append((a, b, tag))

    def end(self, t, tag, b) -> None:
        self.busy[t] = [(s, b if g == tag and e == INF else e, g) for s, e, g in self.busy[t]]

    def later(self, a) -> float:
        ends = [e for lst in self.busy.values() for _, e, _ in lst if a < e < INF]
        if not ends:
            raise LatticeError("nothing will ever free the tiles this instruction needs")
        return min(ends)


def _route(board, tiles, letter, a, b, own):
    """(attachment side per patch, corridor tiles) for a product at [a, b), or None."""
    sides = LETTER_SIDES[letter]
    if len(tiles) == 2:
        for s in sides:
            if _nb(tiles[0], s) == tiles[1]:
                return [s, OPP[s]], set()

    mine = set(tiles)

    def ok(v):
        return v not in mine and board.bus(v) and board.free(v, a, b, own)

    def opts(t):
        return {_nb(t, s): s for s in sides if ok(_nb(t, s))}

    def bfs(srcs, goal):
        prev = {s: None for s in srcs}
        dq = deque(srcs)
        while dq:
            u = dq.popleft()
            if u in goal:
                path = [u]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])
                return path                       # from the goal back to a source
            for s in SIDES:
                v = _nb(u, s)
                if v not in prev and ok(v):
                    prev[v] = u
                    dq.append(v)
        return None
    o0, o1 = opts(tiles[0]), opts(tiles[1])
    p = o0 and o1 and bfs(list(o0), set(o1))
    if not p:
        return None
    tree, att = set(p), [o0[p[-1]], o1[p[0]]]
    for t in tiles[2:]:
        o = opts(t)
        hit = set(o) & tree
        if hit:
            att.append(o[min(hit)])
            continue
        q = o and bfs(list(o), tree)
        if not q:
            return None
        tree |= set(q)
        att.append(o[q[-1]])
    return att, tree


def _shape(letter, tiles, att, corridor) -> tuple:
    pts = list(tiles) + list(corridor)
    mx, my = min(x for x, _ in pts), min(y for _, y in pts)
    rel = lambda t: (t[0] - mx, t[1] - my)
    return ("merge", letter, tuple(sorted((rel(t), s) for t, s in zip(tiles, att))),
            tuple(sorted(rel(t) for t in corridor)))


def _blocks(w, a) -> list:
    return list(a[1::2]) if w == "ppm" else list(a[:1]) if w in ("prep", "read", "ht") else []


def schedule(stream, homes: dict, bounds: tuple, costs: Costs = Costs()) -> list:
    """Every gadget of `stream` -- (word, args, outcome, guard) per instruction, as
    `program._Exp` writes them -- on the lattice: `homes` block -> tile for the blocks that live
    there, `bounds` (x0, y0, x1, y1) the tiles that exist."""
    board = _Board(homes, bounds)
    where = dict(homes)
    free_at: dict = defaultdict(float)
    ready: dict = {}
    out: list = []
    uses = defaultdict(list)
    for k, (w, a, _, _) in enumerate(stream):
        for blk in _blocks(w, a):
            uses[blk].append(k)

    def guard_ready(g):
        try:
            return max((ready[x.lstrip("!")] for x in g), default=0.0)
        except KeyError as e:
            raise LatticeError(f"a guard reads {e}, which nothing before it measures") from None

    def place(blk, k, t):
        """A bus tile for a fresh ancilla, breadth-first from its first product's partners, that
        every product it takes part in until its readout can reach (a free bus tile on one of
        that letter's sides, or a partner facing it there) and, if it takes H̄, with a bus tile
        to its right."""
        life = []
        for j in uses[blk]:
            if j <= k:
                continue
            life.append(j)
            if stream[j][0] == "read" or (stream[j][0] == "prep" and stream[j][1][0] == blk):
                break
        prods = [stream[j][1] for j in life if stream[j][0] == "ppm"]
        needs_h = any(stream[j][0] == "ht" for j in life)
        first = []
        if prods:
            letters, blks = prods[0][0::2], prods[0][1::2]
            first = [_nb(where[b], s) for b in blks if b != blk and b in where
                     for s in LETTER_SIDES[letters[blks.index(blk)]]]

        def fits(c):
            if needs_h and not board.bus(_nb(c, "right")):
                return False
            for a in prods:
                L = a[0::2][a[1::2].index(blk)]
                # facing a partner joins two patches only; a product of more needs a bus side
                others = {where.get(b) for b in a[1::2] if b != blk} if len(a) == 4 else set()
                if not any(board.bus(_nb(c, s)) or _nb(c, s) in others for s in LETTER_SIDES[L]):
                    return False
            return True
        seen, dq, cands = set(first), deque(first), list(first)
        while dq and len(cands) < 400:
            u = dq.popleft()
            for s in SIDES:
                v = _nb(u, s)
                if v not in seen and board.bus(v):
                    seen.add(v)
                    cands.append(v)
                    dq.append(v)
        taken = set(where.values())
        return next((c for c in cands if board.bus(c) and c not in taken and board.free(c, t, INF)
                     and fits(c)), None)

    for k, (w, a, o, g) in enumerate(stream):
        if w in ("x", "y", "z", "bit"):
            continue
        blks = _blocks(w, a)
        t = max([free_at[b] for b in blks] + [guard_ready(g)])
        if w == "prep":
            blk = a[0]
            if blk not in homes:
                tile = place(blk, k, t)
                while tile is None:
                    t = board.later(t)
                    tile = place(blk, k, t)
                where[blk] = tile
                board.take(tile, t, INF, blk)
            dur = costs.prep_y_us if a[1] == "Y" else costs.prep_us
            out.append(Gadget("prep", t, t + dur, frozenset([where[blk]]), ("prep", a[1]), k, (blk,)))
            free_at[blk] = t + dur
        elif w == "read":
            blk = a[0]
            if blk not in where:
                raise LatticeError(f"line {k}: {blk} is read but lives nowhere")
            tile, t1 = where[blk], t + costs.read_us
            out.append(Gadget("read", t, t1, frozenset([tile]), ("read", a[1]), k, (blk,)))
            ready[o] = t1 + costs.decode_us
            free_at[blk] = t1
            if blk not in homes:
                board.end(tile, blk, t1)
                del where[blk]
        elif w == "ht":
            blk = a[0]
            tile = where[blk]
            right = _nb(tile, "right")
            if not board.bus(right):
                raise LatticeError(f"line {k}: H-bar on {blk} needs the bus tile right of {tile}")
            while not board.free(right, t, t + costs.hbar_us):
                t = board.later(t)
            board.take(right, t, t + costs.hbar_us, f"ht{k}")
            out.append(Gadget("ht", t, t + costs.hbar_us, frozenset([tile, right]), ("ht",), k, (blk,)))
            free_at[blk] = t + costs.hbar_us
        elif w == "ppm":
            letters = a[0::2]
            if len(set(letters)) != 1:
                raise LatticeError(f"line {k}: a mixed product, {' '.join(a)}: rewrite it first")
            tiles = [where[b] for b in blks]
            r = _route(board, tiles, letters[0], t, t + costs.merge_us, set(blks))
            while r is None:
                t = board.later(t)
                r = _route(board, tiles, letters[0], t, t + costs.merge_us, set(blks))
            att, cor = r
            t1 = t + costs.merge_us
            for c in cor:
                board.take(c, t, t1, f"m{k}")
            out.append(Gadget("merge", t, t1, frozenset(tiles) | frozenset(cor),
                              _shape(letters[0], tiles, att, cor), k, tuple(blks),
                              {"sides": att, "corridor": sorted(cor), "tiles": tiles}))
            for b in blks:
                free_at[b] = t1
            ready[o] = t1 + costs.decode_us
        else:
            raise LatticeError(f"line {k}: '{w}' is not an instruction of the surgery machine")
    return out


def check(stream, gadgets, costs: Costs = Costs()) -> list:
    """What is wrong with a schedule: two gadgets on one tile at once, a block's instructions out
    of program order, a guard read before its outcome is decoded."""
    bad = []
    by = defaultdict(list)
    for gd in gadgets:
        for t in gd.tiles:
            by[t].append((gd.t0, gd.t1, gd.line))
    for t, lst in by.items():
        lst.sort()
        bad += [f"tile {t}: lines {x[2]} and {y[2]} overlap" for x, y in zip(lst, lst[1:]) if y[0] < x[1] - 1e-6]
    last: dict = {}
    made: dict = {}
    for gd in sorted(gadgets, key=lambda g: g.line):
        for b in gd.blocks:
            if gd.t0 < last.get(b, 0.0) - 1e-6:
                bad.append(f"line {gd.line}: {b} starts before its previous instruction ends")
            last[b] = gd.t1
        _, _, o, g = stream[gd.line]
        for x in g:
            if gd.t0 < made.get(x.lstrip("!"), INF) - 1e-6:
                bad.append(f"line {gd.line}: its guard {x} is not decoded yet")
        if o:
            made[o] = gd.t1 + costs.decode_us
    return bad


def columns(blocks, height: int) -> tuple:
    """Tile-in-a-sea homes: block k of `blocks` on tile (2 (k // height), 2 (k % height)), and the
    bounds that keep a bus all round.  (`height` 3: the adder's bit triples, a column a bit.)"""
    homes = {b: (2 * (k // height), 2 * (k % height)) for k, b in enumerate(blocks)}
    xs = [x for x, _ in homes.values()]
    return homes, (-1, -1, max(xs) + 1, 2 * height - 1)
