"""FormalRV's Gidney 2025 by lattice surgery, on a floorplan of tiles (docs/FULLSTACK_PLAN.md, S10; the user's call of 2026-11-06).

FormalRV's `.qec` writes every product `merge P -> c`, `extract rounds=11`, `decode`, `split`; its backend lowers that to 11 cat
repetitions, then 11 rounds on each patch, with no check on the patches in between (`tools/gidney_diagnose.py failures`).  Here each
such product, in the listing's order, is one merge: 11 rounds of the merged patches, the machine's own rounds and the product's at
once.  The listing's other rounds stay.  A product read by `gidney.Reader` becomes:

    a product of patches one way round     a corridor merge (`experiments.corridor_merge`): facing, or through free tiles
    `M(P)` on a request's patch at its end its destructive readout
    `M(P)` on a patch that lives on        a merge with one fresh tile
    the same patch the other way round     H̄ first: FormalRV's own, S̄, √X̄, S̄, three |Ȳ> merges

**Which way round.**  A merge joins strings parallel to its seam, so what matters is whether a patch's operator is a row or a column.
A patch may hold its state H̄-turned (its label 1): its listing Z̄ is then the column string and its X̄ the row string.  Every operation
on it is the listing's conjugated by H̄, so nothing else changes; a turned patch only reads and prepares in the other basis.  A request's
patch chooses at each product; a wire chooses once (`labels`).  So a product is one letter when label + letter agree over its patches.
A wire pair asked for both a CNOT and a CZ is an odd cycle no labelling removes: there the request's patch takes H̄ in between.

**Where.**  `Plan`'s rows, spread `pitch` tiles a bit so that every wire has a free tile beside it as well as above or below it: a row
string reaches the rows, a column string the gaps, and all free tiles connect.  A patch goes on a tile only if every product due in its
life can still route around it.  A factory works in the band beside the bit it serves, provisional until step 4 gives it its real size
and time; neighbours' factories overlap, so they take turns.  With `spread`, each band is instead a road over fixed slots that never
overlap, and consecutive bits draw from `spread` neighbouring slots: measured, no faster (42.2 years against 42.1), since a slot is busy
until its nine patches are read and its corridors are longer.

    labels(reader)                         {wire: 0 | 1} and what it leaves
    SurgeryPlan(reader, labels)            the floorplan and the walk: `schedule`, `serial` as `gidney_plan.Plan`'s
    plan.merges(key)                       {shape: uses}: every program a schedule copies, `lattice`'s translation-free form
    plan.placed(key)                       where they run, and every tile anything holds
    hot_ions(plan, placed)                 the ions the machine must hold: every trap any program uses
"""
from __future__ import annotations

import bisect
import random
from collections import Counter, deque

from .gidney import Anc, Reader
from .gidney_plan import DECODE_US, MAGIC_CCZ_US, MAGIC_T_US, PREP_US, READ_US, ROUND_US, H_US, PREP_Y_US, Plan, strips
from .lattice import LETTER_SIDES, SIDES, _nb, _route, _shape

#: a factory slot's tiles no other corridor may cross (`SurgeryPlan._factory_unit`): the outputs and the check, the rail, the T row.  The gaps
#: stay bus, for a consumer that meets an output side on; each is a dead end under the road
_SLOT_PATCH_TILES = [(0, 0), (2, 0), (4, 0), (6, 0), (8, 0), (9, 0)] + [(a, b) for a in range(10) for b in (1, 2)]

__all__ = ["labels", "SurgeryPlan", "hot_ions", "MERGE_US", "ROUND_IN_MERGE_US", "LabelError"]

#: a merge's 11 rounds, its fresh ions reset and made |+> or |0>, every rule and R10 (`layer`), whatever its shape
MERGE_US = 61_032.0
ROUND_IN_MERGE_US = MERGE_US / 11
#: a cultivated |T> on its tile (`cultivation`): Gidney, Shutty and Jones's d1 = 3 injection and cultivation, one growing round and one
#: round of the grafted code at d2 = 11, the drop to a plain patch and two plain rounds, compiled (246 ions, every rule and R10), and its gap
#: decided a decode later; an attempt the gap discards is made again on the same tile while the factory's other rotations run
T_US = 117_369.0
T_DECIDED_US = T_US + 5_822.0
#: Ȳ read in place: Gidney's preparation run backwards (`experiments.y_read_qasm`), compiled (251 ions, every rule and R10)
READ_Y_US = 48_415.0
#: the cultivated factory's slot (`SurgeryPlan._factory_unit`): ten tiles wide, three rows under its band's road
SLOT_WIDTH, SLOT_ROWS = 10, 3
_BIT = {"Z": 0, "X": 1}


class LabelError(ValueError):
    pass


def _is_wire(p) -> bool:
    return isinstance(p, int) and p >= 0


class _Parity:
    """Union-find with parity: s_a + s_b."""

    def __init__(self):
        self.up: dict = {}
        self.par: dict = {}

    def find(self, a) -> tuple:
        if a not in self.up:
            self.up[a], self.par[a] = a, 0
        trail = []
        while self.up[a] != a:
            trail.append(a)
            a = self.up[a]
        acc = 0
        for n in reversed(trail):
            acc ^= self.par[n]
            self.par[n], self.up[n] = acc, a
        return a

    def parity(self, a) -> int:
        self.find(a)
        return self.par[a] if self.up[a] != a else 0

    def join(self, a, b, p) -> bool:
        """s_a + s_b = p; False if that contradicts what is known."""
        ra, rb = self.find(a), self.find(b)
        pa, pb = self.parity(a), self.parity(b)
        if ra == rb:
            return pa ^ pb == p
        self.up[ra], self.par[ra] = rb, pa ^ pb ^ p
        return True


def labels(reader: Reader, seed: int = 1, tries: int = 40) -> tuple:
    """({wire: its label}, {"hard": [wires of one product a labelling cannot make one way round], "edges": ..., "broken": ...}): the
    wires of one product must agree; wires joined through a request's patch would like to (else that patch takes H̄).  Weighted by uses."""
    memo: dict = {}

    def under(key):
        if key in memo:
            return memo[key]
        hard, soft = Counter(), Counter()
        items = reader.flat(key) if key is not None else reader.program()
        through: dict = {}
        for it in items:
            if it[0] == "call":
                a, b = under(it[1])
                hard.update(a)
                soft.update(b)
            elif it[0] == "rep":
                tg = [(p, l) for p, l in it[2] if l != "Y"]
                wires = [(p, l) for p, l in tg if _is_wire(p)]
                for (a, la), (b, lb) in zip(wires, wires[1:]):
                    hard[(min(a, b), max(a, b), _BIT[la] ^ _BIT[lb])] += 1
                for p, l in tg:
                    if not _is_wire(p) and wires:
                        # what this patch's label must be, in terms of the first wire's
                        through.setdefault(p, []).append((wires[0][0], _BIT[wires[0][1]] ^ _BIT[l]))
        for seq in through.values():
            for (a, pa), (b, pb) in zip(seq, seq[1:]):
                if a != b:
                    soft[(min(a, b), max(a, b), pa ^ pb)] += 1
        memo[key] = (hard, soft)
        return memo[key]

    hard, soft = under(None)
    uf = _Parity()
    bad = []
    for (a, b, p), n in sorted(hard.items(), key=lambda kv: -kv[1]):
        if not uf.join(a, b, p):
            bad.append((a, b, p, n))
    wires = sorted({w for e in list(hard) + list(soft) for w in e[:2]})
    # the groups hard edges made, and the soft edges between them
    edges: Counter = Counter()
    for (a, b, p), n in soft.items():
        ra, rb = uf.find(a), uf.find(b)
        if ra != rb:
            q = p ^ uf.parity(a) ^ uf.parity(b)
            edges[(min(ra, rb), max(ra, rb), q)] += n
    groups = sorted({uf.find(w) for w in wires})
    adj: dict = {g: [] for g in groups}
    for (a, b, p), n in edges.items():
        adj[a].append((b, p, n))
        adj[b].append((a, p, n))
    rng = random.Random(seed)
    best = None
    for _ in range(tries):
        s = {g: rng.randrange(2) for g in groups}
        changed = True
        while changed:
            changed = False
            order = groups[:]
            rng.shuffle(order)
            for g in order:
                if sum((n if s[g] ^ s[b] != p else -n) for b, p, n in adj[g]) > 0:
                    s[g] ^= 1
                    changed = True
        c = sum(n for (a, b, p), n in edges.items() if s[a] ^ s[b] != p)
        if best is None or c < best[0]:
            best = (c, dict(s))
    out = {w: best[1][uf.find(w)] ^ uf.parity(w) for w in wires}
    return out, {"hard": bad, "edges": sum(soft.values()), "broken": sum(n for (a, b, p), n in soft.items()
                                                                       if out[a] ^ out[b] != p)}


def _corner(tiles, cor) -> tuple:
    """Where a shape's (0, 0) sits on the floorplan: `lattice._shape` counts from the least x and y."""
    pts = list(tiles) + list(cor)
    return min(x for x, _ in pts), min(y for _, y in pts)


class _Static:
    """`lattice._route`'s board, a frame's view: a free tile is one no wire and none of this frame's live patches holds."""

    def __init__(self, plan, live, own=()):
        self.plan, self.live, self.own = plan, live, set(own)

    def bus(self, t) -> bool:
        if t in self.own:
            return True
        return self.plan.in_bounds(t) and t not in self.plan.wire_tiles and t not in self.plan.slot_tiles and t not in self.live

    def free(self, t, a, b, own=()) -> bool:
        return True


class SurgeryPlan(Plan):
    def __init__(self, reader: Reader, wire_label: dict, pitch: int = 3, band: int = 2, inner: int = 1, gap: int = 0,
                 spread: int = 0, period: int = 12):
        """`pitch` 2: a free column right of every bit; 3: one between two bits.  `band`: rows a register has for its road and its
        factories; `inner`: free rows between w0 and w2; `gap`: between registers.  `spread`: slots consecutive bits draw from (0: a
        factory where it is needed, overlapping its neighbours'); `period`: tiles from slot to slot along the band."""
        self.reader, self.label, self.pitch, self.band_rows = reader, wire_label, pitch, band
        self.spread, self.period = spread, period
        self.tile, self.band = {}, {}
        self.bands: list = []
        y = 1
        self.width = 0
        for base, bits, stride in strips():
            if stride == 3:         # w1, the band, w0, free rows, w2
                rows = {1: y, 0: y + band + 1, 2: y + band + 2 + inner}
                the_band, y2 = (y + 1, y + band), y + band + 3 + inner
            elif stride == 2:       # w1, the band, w0, free rows
                rows = {1: y, 0: y + band + 1}
                the_band, y2 = (y + 1, y + band), y + band + 2 + inner
            else:                   # the band, its wires
                rows = {0: y + band}
                the_band, y2 = (y, y + band - 1), y + band + 1
            y2 += gap - 1
            for k, row in rows.items():
                for i in range(bits):
                    w = base + stride * i + k
                    self.tile[w], self.band[w] = (self._column(i), row), the_band
            self.width = max(self.width, self._column(bits - 1) + 1)
            if stride > 1:
                self.bands.append(the_band)
            y = y2 + 1                  # a free row between registers
        self.port_row = y
        self.height = y + 1
        self.width += 1
        missing = [w for w in range(415) if w not in self.tile]
        if missing:
            raise ValueError(f"wires with no tile: {missing}")
        self.wire_tiles = set(self.tile.values())
        self.slots: dict = {}           # band -> [its slots' tiles]
        self.slot_tiles: set = set()
        self._units: dict = {}
        if spread:
            if band < SLOT_ROWS + 1 or period < SLOT_WIDTH:
                raise ValueError(f"a factory slot is {SLOT_WIDTH} tiles wide and {SLOT_ROWS} rows under the road: band >= {SLOT_ROWS + 1}, "
                                 f"period >= {SLOT_WIDTH}")
            for top, bottom in self.bands:
                row = []
                for k in range((self.width - 1) // period):
                    origin = (period * k + 1, top + 1)
                    row.append(origin)
                    self.slot_tiles.update((origin[0] + a, origin[1] + b) for a, b in _SLOT_PATCH_TILES)
                self.slots[(top, bottom)] = row
        self.clashes = Counter()
        self.clock = ROUND_IN_MERGE_US
        self.source_waits = True        # `source c[..] <- c[..]` is usually the outcome of the product just above it
        self.flat_from = {"gOnePrime"}
        self.hbars = Counter()
        self.y_seq = 0
        self._memo: dict = {}
        self._walked: dict = {}

    def _column(self, i: int) -> int:
        """pitch 2: every bit a free column to its right; pitch 3: two bits share one between them."""
        return 2 * i + 1 if self.pitch == 2 else 3 * (i // 2) + (i % 2) + 1

    def in_bounds(self, t) -> bool:
        return 0 <= t[0] < self.width and 0 <= t[1] < self.height

    # ---------------------------------------------------------------- what a product asks of its patches
    def _wire_way(self, w, letter) -> int:
        return _BIT[letter] ^ self.label.get(w, 0)

    def _plan_frame(self, items) -> tuple:
        """Per request's patch in this frame: its products in order, each (item, letter, the way round it needs: 0 row, 1 column, None
        if alone, the one wire it could face, every wire's tile).  And the last item that touches each patch."""
        life: dict = {}
        last: dict = {}
        for i, it in enumerate(items):
            if it[0] == "rep":
                ways = [self._wire_way(p, l) for p, l in it[2] if _is_wire(p) and l != "Y"]
                wires = [p for p, _ in it[2] if _is_wire(p)]
                face = self.tile[wires[0]] if len(it[2]) == 2 and len(wires) == 1 else None
                at = tuple(self.tile[q] for q in wires)
                for p, l in it[2]:
                    if not _is_wire(p):
                        life.setdefault(p, []).append((i, l, ways[0] if ways else None, face, at))
                        last[p] = i
            elif it[0] == "cold":
                for p, l in it[5]:
                    if not _is_wire(p):
                        life.setdefault(p, []).append((i, l, None, None, ()))
                        last[p] = i
            elif it[0] in ("read", "h"):
                last[it[1]] = i
        return life, last

    def _spot(self, p, seq, near, near_way, spot, live, routes=lambda t: True) -> tuple:
        """A free tile for a request's patch, breadth-first from `near`, the wire of its largest product, on that product's side, from
        which every product of its life can reach out: `seq`, each (.., .., the way round it needs, or None, the wire it could face)."""
        if p in spot:
            return spot[p]
        needs = {(w, face, at) for _i, _l, w, face, at in seq if w is not None}
        if near is not None:
            start = [_nb(near, side) for side in LETTER_SIDES["Z" if near_way == 0 else "X"]] + [_nb(near, side) for side in SIDES]
        else:
            start = [(x, self.port_row) for x in range(self.width)]
        board = _Static(self, live)

        # it will take H̄ if its products ask for it both ways round; H̄'s |Ȳ>s need a free side each way
        turns = len({e[2] ^ _BIT[e[1]] for e in seq if e[2] is not None}) > 1

        def fits(t):
            if not board.bus(t):
                return False
            if turns and not all(any(board.bus(_nb(t, side)) for side in LETTER_SIDES[letter]) for letter in "ZX"):
                return False
            for w, face, at in needs:
                sides = LETTER_SIDES["Z" if w == 0 else "X"]
                facing = face is not None and any(_nb(t, side) == face for side in sides)
                if not facing and not any(board.bus(_nb(t, side)) for side in sides):
                    return False
                # it leaves every other wire of the product a side of its own
                if not facing and not all(any(board.bus(_nb(q, side)) and _nb(q, side) != t for side in sides) for q in at):
                    return False
            return True
        # `routes`: every product due while it lives still has a corridor with it here
        for test in (lambda t: fits(t) and routes(t), fits):
            seen, dq = set(start), deque(start)
            while dq:
                t = dq.popleft()
                if test(t):
                    spot[p] = t
                    live[t] = p.base
                    return t
                for side in SIDES:
                    v = _nb(t, side)
                    if v not in seen and self.in_bounds(v):
                        seen.add(v)
                        dq.append(v)
        raise LabelError(f"no free tile for {p}: near {near}, its products {[(e[1], e[2], e[3], e[4][:4]) for e in seq]}")

    def _resolve(self, key):
        got = self._walked.get(key)
        if got is None:
            got = self._walked[key] = list(self._walk(key))
        return got

    def _walk(self, key):
        items = self.reader.flat(key) if key is not None else self.reader.program()
        life, last = self._plan_frame(items)
        products = [j for j, it in enumerate(items) if it[0] == "rep"]
        from_factory = {it[2] for it in items if it[0] == "magic" and it[1] == "|AutoCCZ>"}
        requests: dict = {}
        for it in items:
            if it[0] == "prep":
                requests[it[1]] = it[2]
            elif it[0] == "magic":
                requests[it[2]] = -abs(requests.get(it[2], 1))
        spot: dict = {}
        live: dict = {}
        way: dict = {}          # a request's patch: how it holds its state now
        near_of: dict = {}
        for p, seq in life.items():
            # beside its largest product: a register-wide product then has one shape, whichever partner this patch also meets
            ranked = sorted((e for e in seq if items[e[0]][0] == "rep" and e[2] is not None), key=lambda e: -len(items[e[0]][2]))
            if ranked:
                wires = [q for q, _ in items[ranked[0][0]][2] if _is_wire(q)]
                near_of[p] = (self.tile[wires[0]], ranked[0][2])

        def res(p):
            return ("wire", p) if _is_wire(p) else (("cold", p) if isinstance(p, int) else ("patch", p.base, p.index))

        def place(p):
            seq = life.get(p, ())
            want = next((e[2] ^ _BIT[e[1]] for e in seq if e[2] is not None), 0)
            # its Z̄ first: `want` is the way round of its first product with a wire
            way.setdefault(p, want)
            near, near_way = near_of.get(p, (None, 0))
            if near is None:
                for j in range(abs(requests.get(p.base, 1))):
                    q = Anc(p.base, j)
                    if q in spot:
                        near = spot[q]
                        break
                    if q in near_of:
                        near, near_way = near_of[q]
                        break

            def routes(t):
                for j in products[bisect.bisect_left(products, i):bisect.bisect_right(products, last.get(p, i))]:
                    tg = items[j][2]
                    wires = [(q, "X" if l == "Y" else l) for q, l in tg if _is_wire(q)]
                    if not wires:
                        continue
                    tiles = [self.tile[q] if _is_wire(q) else (t if q == p else spot.get(q)) for q, _ in tg]
                    tiles = [c for c in tiles if c is not None]
                    letter = "Z" if self._wire_way(*wires[0]) == 0 else "X"
                    board = _Static(self, {**live, t: p.base}, set(tiles))
                    if len(tiles) == 1:
                        if not any(board.bus(_nb(tiles[0], side)) and _nb(tiles[0], side) not in tiles for side in LETTER_SIDES[letter]):
                            return False
                    elif _route(board, tiles, letter, 0.0, 1.0, set(tiles)) is None:
                        return False
                return True
            return self._spot(p, seq, near, near_way, spot, live, routes)

        i, n = 0, len(items)
        factories = 0
        while i < n:
            it = items[i]
            k = it[0]
            if k in ("guard", "endguard", "decode"):
                yield it
            elif k == "call":
                yield ("call", it[1], dict(live))
            elif k == "prep":
                if self.spread and it[1] in from_factory:
                    i += 1
                    continue        # a factory slot's outputs: the factory makes them, there
                for j in range(it[2]):
                    p = Anc(it[1], j)
                    t = place(p)
                    yield ("work", PREP_US, [("tile", t), res(p)], False, "prep", ("prep", it[3]))
            elif k == "free":
                for j in range(abs(requests.get(it[1], 1))):
                    p = Anc(it[1], j)
                    t = spot.pop(p, None)
                    if t is not None:
                        live.pop(t, None)
                        yield ("free", ("tile", t), res(p))
            elif k == "magic":
                cnt = abs(requests.get(it[2], 1))
                ps = [Anc(it[2], j) for j in range(cnt)]
                if it[1] == "|AutoCCZ>" and self.spread:
                    near = next((near_of[q][0] for q in ps if q in near_of), (0, self.port_row))
                    yield from self._slot_factory(near, ps, life, spot, live, way, res, factories, len(from_factory) > 1)
                    factories += 1
                    i += 1
                    continue
                tiles = [place(p) for p in ps]
                busy = [res(p) for p in ps] + [("tile", t) for t in tiles]
                if it[1] == "|AutoCCZ>":
                    # the factory: provisional until step 4 builds it, 18 tiles of the band round its first output
                    x, _y = tiles[0]
                    w = next((q for q, t in self.tile.items() if t == near_of.get(ps[0], (None,))[0]), None)
                    top, bottom = self.band[w] if w is not None else (self.port_row, self.port_row)
                    cols = max(1, 18 // (bottom - top + 1))
                    x0 = max(0, min(x - cols // 2, self.width - cols))
                    busy += [("tile", (x0 + a, row)) for a in range(cols) for row in range(top, bottom + 1)
                             if (x0 + a, row) not in self.wire_tiles and (x0 + a, row) not in tiles]
                    yield ("work", MAGIC_CCZ_US, busy, False, "|AutoCCZ>")
                else:
                    t_us = (T_DECIDED_US if self.spread else MAGIC_T_US)
                    yield ("work", t_us if it[1] == "|T>" else PREP_Y_US, busy, False, it[1])
            elif k == "rep":
                yield from self._product(it, spot, live, way, life, last, i, place, res)
                # the merge's own rounds are its rounds
                inside = {p for p, _ in it[2]}
                j = i + 1
                while j < n and items[j][0] == "rounds" and items[j][2] in inside:
                    j += 1
                i = j - 1
            elif k == "cold":
                # a stored qubit's merge with its partner: the cold round counted as a hot one, composed; the block held
                busy = [res(p) for p, _ in it[5]] + [("cold", it[2])]
                yield ("work", it[1] * ROUND_IN_MERGE_US + READ_US, busy, True, "cold")
                inside = {p for p, _ in it[5]}
                j = i + 1
                while j < n and items[j][0] == "rounds" and items[j][2] in inside:
                    j += 1
                i = j - 1
            elif k == "rounds":
                p = it[2]
                if isinstance(p, Anc):
                    place(p)
                yield ("work", ROUND_US * it[1], [res(p)], True, "rounds")
            elif k in ("read", "h"):
                p = it[1]
                while i + 1 < n and items[i + 1][0] == k and items[i + 1][1] == p:
                    i += 1
                # the program it runs: a read's basis is the listing's (a hand-made stream may leave it out); an H is one pulse, no program
                prog = ("read", it[2]) if k == "read" and len(it) > 2 else None
                yield ("work", READ_US if k == "read" else H_US, [res(p)], False, k, prog)
            i += 1

    def _slot_factory(self, near, ps, life, spot, live, way, res, count=0, many=False):
        """The |AutoCCZ> of a bit at `near`, made by the cultivated factory (`_factory_unit`) in a slot of the nearest band: its items, each a copy
        of the unit's translated to the slot.  Its nine sites are left on the slot's tiles, each held the way round its one product asks (the unit
        turns those that must turn with FormalRV's H̄, inside the factory); a last item holds them all until the factory is done."""
        band = min(self.slots, key=lambda b: (abs(b[0] - near[1]), b[0]))
        row = self.slots[band]
        k0 = min((near[0] - 1) // self.period, len(row) - 1)
        # a window of `spread` slots round the bit, inside the band.  Consecutive factories of one frame take them in turn; a frame with one factory, a gadget
        # called bit by bit, by its column (a column of the machine's pitch is 1.5 tiles a bit)
        first = max(0, min(k0 - self.spread // 2, len(row) - self.spread))
        x0, y0 = row[first + (count if many else near[0] * 2 // 3) % min(self.spread, len(row))]
        # what each of x, y, z must be when the consumer meets it: its label, from its one product
        want = []
        for p in ps[:3]:
            seq = life.get(p, ())
            w = next((e[2] ^ _BIT[e[1]] for e in seq if e[2] is not None), None)
            want.append(w)
        unit = self._factory_unit(tuple(want))
        mv = lambda t: (t[0] + x0, t[1] + y0)
        for kind, dur, tiles, extra in unit["items"]:
            busy = [("tile", mv(t)) for t in tiles]
            if kind == "merge":
                shape, corner = extra
                yield ("work", dur, busy, True, "merge", shape, mv(corner))
            else:
                yield ("work", dur, busy, False, kind, extra)
        for p, (t, label) in zip(ps, unit["sites"]):
            spot[p], live[mv(t)] = mv(t), p.base
            way[p] = label
        yield ("work", 0.0, [res(p) for p in ps] + [("tile", mv(t)) for t, _ in unit["sites"]], False, "|AutoCCZ>")

    def _factory_unit(self, want: tuple) -> dict:
        """The cultivated factory (`factory.cultivated_autoccz`) on a slot's tiles, scheduled once by the lattice scheduler (`lattice.schedule`,
        checked by `lattice.check`), for one set of labels its x, y, z must leave with (None: any).  {"items": [(kind, µs, tiles, (shape, corner) for a
        merge)], "sites": [(tile, label)] for FormalRV's nine sites, "us": its span}.

            road  (the band's top row, shared)
            x  .  y  .  z  .  k  .  T  T      outputs and the check; a gap beside each output for its H̄ and for a consumer that meets it side on
            -  -  -  -  -  -  -  -  -  -      the rail: every rotation's corridor
            s4 T  s3 T  s5 T  s6 T  s7 s8     six tiles making the eight cultivated T in turn; the sites on the tiles between, each beside a T tile

        x and y leave plain and z turned, as `autoccz` makes them; an output its consumer wants the other way round takes H̄ before it leaves."""
        got = self._units.get(want)
        if got is not None:
            return got
        from . import factory as F
        from .lattice import Costs, check, schedule
        stream, blocks = F.cultivated_autoccz("c")
        labels = {0: 0, 1: 0, 2: 1}
        for idx in range(3):
            if want[idx] is not None and want[idx] != labels[idx]:
                stream += F.hbar(f"cl{idx}", f"cf{idx}")
                labels[idx] ^= 1
        homes = {"ck": (6, 0), "cl0": (0, 0), "cl1": (2, 0), "cl2": (4, 0)}
        # six T tiles making T in turn for rotations due every 61.5 ms: the odd tiles of the T row and two beside the check.  The sites go on the T row's
        # even tiles, so a slot's next factory never waits for the last one's consumer to read its sites.  With an attempt discarded 4.6 % of the
        # time and each rotation taking the first T ready (they commute), 0.44 % of factories end late, by 0.4 ms on average (four tiles: 18.6 %,
        # 13 ms; simulated, docs/FULLSTACK_PLAN.md S10 step 4)
        t_tiles = [(1, 2), (3, 2), (5, 2), (7, 2), (8, 0), (9, 0)]
        pinned = {f"cm{j}": t_tiles[j % len(t_tiles)] for j in range(8)}
        pinned.update({"cl4": (0, 2), "cl3": (2, 2), "cl5": (4, 2), "cl6": (6, 2), "cl7": (8, 2), "cl8": (9, 2)})
        # an output's H̄: its |Ȳ>s on the rail below it (S̄) and the gap beside it (√X̄)
        for idx, (a, side) in enumerate(((0, 1), (2, 3), (4, 5))):
            pinned.update({f"cf{idx}y0": (a, 1), f"cf{idx}y1": (side, 0), f"cf{idx}y2": (a, 1)})
        costs = Costs(merge_us=11 * ROUND_IN_MERGE_US + READ_US, prep_us=PREP_US, read_us=READ_US, prep_y_us=PREP_Y_US, decode_us=DECODE_US,
                      prep_t_us=T_DECIDED_US, read_y_us=READ_Y_US + DECODE_US)
        gadgets = schedule(stream, homes, (0, 0, SLOT_WIDTH - 1, SLOT_ROWS - 1), costs, pinned=pinned)
        bad = check(stream, gadgets, costs)
        if bad:
            raise LabelError(f"the factory's schedule is not sound: {bad[:3]}")
        items = []
        for g in sorted(gadgets, key=lambda g: (g.t0, g.line)):
            dur = g.t1 - g.t0
            if g.kind == "merge":
                cor = g.detail.get("corridor", [])
                items.append(("merge", dur, sorted(g.tiles), (g.shape, _corner(g.detail["tiles"], cor))))
            elif g.kind == "prep":
                items.append(({"T": "|T>", "Y": "|Y+>"}.get(g.shape[1], "prep"), dur, sorted(g.tiles), g.shape))
            else:
                items.append(("read", dur, sorted(g.tiles), g.shape))
        names = [f"cl{i}" for i in range(9)]
        where = {**homes, **pinned}
        sites = [(where[b], labels.get(i, 1 if i in F.TURNED else 0)) for i, b in enumerate(names)]
        got = self._units[want] = {"items": items, "sites": sites, "us": max(g.t1 for g in gadgets)}
        return got

    def _with_y(self, patch, tile, way, live) -> list:
        """A fresh |Ȳ> beside `patch` (on `tile`), merged with it along the side of that way round, then read: S̄ or √X̄ of the patch,
        as FormalRV writes them, a Pauli frame apart.  The |Ȳ> is made as soon as its tile is free, so ahead when it can be."""
        letter = "Z" if way == 0 else "X"
        board = _Static(self, live, {tile})
        side = next((s for s in LETTER_SIDES[letter] if board.bus(_nb(tile, s))), None)
        self.y_seq += 1
        y = ("patch", "y", self.y_seq)
        if side is not None:
            spot = _nb(tile, side)
            tiles, att, cor = [tile, spot], [side, {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}[side]], set()
        else:
            # no free side: the nearest free tile with a side that way round, through a corridor
            seen, dq = {tile}, deque([tile])
            found = None
            while dq and found is None:
                t = dq.popleft()
                for s in SIDES:
                    v = _nb(t, s)
                    if v in seen or not self.in_bounds(v):
                        continue
                    seen.add(v)
                    dq.append(v)
                    if board.bus(v) and v not in live:
                        found = v
                        break
            got = found and _route(_Static(self, live, {tile, found}), [tile, found], letter, 0.0, 1.0, {tile, found})
            if not got:
                raise LabelError(f"no tile for a |Ȳ> beside {tile}")
            spot, (att, cor), tiles = found, got, [tile, found]
        return [("work", PREP_Y_US, [("tile", spot), y], False, "|Y+>"),
                ("work", 11 * ROUND_IN_MERGE_US + READ_US, [patch, y] + [("tile", c) for c in cor], True, "merge",
                 _shape(letter, tiles, att, cor), _corner(tiles, cor)),
                ("work", READ_US, [y], False, "read", ("read", "X" if letter == "Z" else "Z")),
                ("free", ("tile", spot), y)]

    def _product(self, it, spot, live, way, life, last, i, place, res):
        tg = list(it[2])
        reps = it[1]
        conj = [p for p, l in tg if l == "Y"]
        if conj:
            # M(Ȳ Z̄) = S̄ M(X̄ Z̄) S̄†, each S̄ a |Ȳ>, a merge and its readout
            tg = [(p, "X" if l == "Y" else l) for p, l in tg]
            for q in conj:
                yield from self._with_y(res(q), self.tile[q] if _is_wire(q) else spot[q], self._wire_way(q, "Z"), live)
        if len(tg) == 1 and not _is_wire(tg[0][0]) and last.get(tg[0][0]) == i:
            # the patch is released after this: its readout, in its product's letter as the patch holds it
            p, letter = tg[0]
            place(p)
            yield ("work", READ_US, [res(p)], True, "read", ("read", letter if not way.get(p) else "XZ"["ZX".index(letter)]))
            return
        wires = [(p, l) for p, l in tg if _is_wire(p)]
        target = self._wire_way(*wires[0]) if wires else None
        for p, l in tg:
            if _is_wire(p):
                continue
            place(p)
            if target is None:
                target = way[p] ^ _BIT[l]
            if way[p] ^ _BIT[l] != target:
                # the odd cycle: this patch must turn.  FormalRV's H̄: S̄, √X̄, S̄
                self.hbars[len(tg)] += 1
                for w in (way[p], way[p] ^ 1, way[p]):
                    yield from self._with_y(res(p), spot[p], w, live)
                way[p] ^= 1
        ways = {(self._wire_way(p, l) if _is_wire(p) else way[p] ^ _BIT[l]) for p, l in tg}
        if len(ways) != 1:
            raise LabelError(f"a product no labelling makes one way round: {tg}")
        letter = "Z" if target == 0 else "X"
        tiles = [self.tile[p] if _is_wire(p) else spot[p] for p, _ in tg]
        own = set(tiles)
        board = _Static(self, live, own)
        if len(tiles) == 1:
            side = next((s for s in LETTER_SIDES[letter] if board.bus(_nb(tiles[0], s))), None)
            got = ([side], {_nb(tiles[0], side)}) if side else None
        else:
            got = _route(board, tiles, letter, 0.0, 1.0, own)
        if got is None:
            raise LabelError(f"no corridor for {tg} on {tiles}")
        att, cor = got
        busy = [res(p) for p, _ in tg] + [("tile", c) for c in cor]
        dur = reps * ROUND_IN_MERGE_US + READ_US
        yield ("work", dur, busy, True, "merge", _shape(letter, tiles, att, cor), _corner(tiles, cor))
        for q in conj:
            yield from self._with_y(res(q), self.tile[q] if _is_wire(q) else spot[q], self._wire_way(q, "Z"), live)

    # ---------------------------------------------------------------- what it needs
    def merges(self, key, memo: dict | None = None) -> Counter:
        """{shape: uses} under one instance (None: the whole program)."""
        memo = {} if memo is None else memo
        if key in memo:
            return memo[key]
        out: Counter = Counter()
        for it in self._resolve(key):
            if it[0] == "call":
                out.update(self.merges(it[1], memo))
            elif it[0] == "work" and it[4] == "merge":
                out[it[5]] += 1
        memo[key] = out
        return out

    def placed(self, key, memo: dict | None = None) -> tuple:
        """({(shape, the tile its program's corner sits on)}, {every tile a patch, a corridor or a factory holds}) under one instance."""
        memo = {} if memo is None else memo
        if key in memo:
            return memo[key]
        out, tiles = set(), set()
        for it in self._resolve(key):
            if it[0] == "call":
                a, b = self.placed(it[1], memo)
                out |= a
                tiles |= b
            elif it[0] == "work":
                here = {r[1] for r in it[2] if r[0] == "tile"} | {self.tile[r[1]] for r in it[2] if r[0] == "wire"}
                tiles |= here
                if it[4] == "merge":
                    out.add((it[5], it[6]))
            elif it[0] == "free":
                tiles.add(it[1][1])
        memo[key] = (out, tiles)
        return memo[key]


def hot_ions(plan: SurgeryPlan, placed, tiles, d: int = 11) -> dict:
    """The ions the machine must hold: every data and measure trap of every tile a patch or a factory ever holds, and of every merge's
    own program where it runs.  Nothing travels, so an ion is needed exactly where some program names it."""
    from ...qec.experiments import _merge_checks, _merge_region, _region
    p = d + 1
    edges = {"top": lambda k: "X", "bottom": lambda k: "X", "left": lambda k: "Z", "right": lambda k: "Z"}
    one = {(i, j): 0 for i in range(d) for j in range(d)}
    tile_faces = [c[0] for c in _region((0, 0), d, d, edges, one)]
    data: set = set()
    faces: set = set()
    for x, y in tiles:
        data.update((y * p + i, x * p + j) for i in range(d) for j in range(d))
        faces.update((y * p + i, x * p + j) for i, j in tile_faces)
    for shape, at in placed:
        _, letter, pats, cor = shape
        x0, y0 = at
        group = [((t[0] + x0, t[1] + y0), s) for t, s in pats]
        points, _inside, far = _merge_region(d, letter, group, {(c[0] + x0, c[1] + y0) for c in cor})
        index = {q: k for k, q in enumerate(points)}
        data |= set(points)
        faces |= {c[0] for c in _merge_checks(letter, points, far, index)}
    return {"tiles": len(tiles), "data": len(data), "faces": len(faces), "ions": len(data) + len(faces)}
