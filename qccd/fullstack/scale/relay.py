"""Long products as relay chains (docs/FULLSTACK_PLAN.md, slice S9 step 5): a Pauli product of
two far patches done by a row of fresh patches between them, each merged with its neighbour, so
every merge is two patches face to face and two programs (a Z̄ pair, an X̄ pair) serve any
distance.

    ppm P a P b -> o   ==   prep r1 .. rk in Q (the other basis);
                            ppm P u P v for neighbours (u, v): every other pair, then the rest;
                            read r1 .. rk in Q;  o = the XOR of the pair outcomes;
                            a P frame on a for each relay that read -1.

`chain` writes those lines; `proved(P, k)` checks the identity by `codesign.equal_drop` (a state
vector, so small k).  A longer chain is the same identity by induction: a chain of k + 1 is a
chain of k whose last product is itself a chain of one, and equal channels stay equal in any
context; the two layers change nothing, since every product is P on different patches (they
commute) and each relay's preparation and readout touch it alone.  The tests prove k = 0 .. 6.

Where the relays go is the floorplan's.  Z̄ sides are a patch's top and bottom, X̄ its left and
right (`lattice.LETTER_SIDES`), so a Z̄ chain down a column or an X̄ chain along a row joins every
neighbour pair face to face, with no corridor tile.  `relay(stream, at)` rewrites every
two-patch product whose patches sit in one column (Z̄) or one row (X̄), apart, into a chain on
the tiles between; `crossings` puts a CNOT's ancilla where its control's column meets its
target's row, so both of its products are such lines.  The relays are fresh blocks pinned to
their tiles (`lattice.schedule(pinned=)`).  A state that has to be somewhere else is carried
there by `move`, a one-bit teleport along a column or a row (`move_proved`: a SWAP into a fresh
|0̄>, by `equal_drop`).

    crossings(stream, homes)  ->  {ancilla: tile}
    relay(stream, at)         ->  (stream, {relay: tile}, [original line of each line])
"""
from __future__ import annotations

from functools import lru_cache

__all__ = ["chain", "proved", "move", "move_proved", "line", "crossings", "relay"]

OTHER = {"Z": "X", "X": "Z"}


def chain(letter: str, a: str, b: str, relays: list, out: str, guard: tuple = ()) -> list:
    """The stream lines of `ppm letter a letter b -> out` through `relays` (block names, in order
    from a to b), every line under `guard`.  New outcomes are `out` + `.p<i>` (pair i) and
    `.r<i>` (relay i's readout)."""
    q = OTHER[letter]
    nodes = [a] + list(relays) + [b]
    pairs = list(zip(nodes, nodes[1:]))
    lines = [("prep", (r, q), None, guard) for r in relays]
    outs = []
    for layer in (0, 1):                  # no patch in two products of one layer
        for i, (u, v) in enumerate(pairs):
            if i % 2 == layer:
                o = f"{out}.p{i}"
                lines.append(("ppm", (letter, u, letter, v), o, guard))
                outs.append(o)
    reads = [f"{out}.r{i}" for i in range(len(relays))]
    lines += [("read", (r, q), m, guard) for r, m in zip(relays, reads)]
    lines.append(("bit", (" ^ ".join(sorted(outs, key=lambda o: int(o.rsplit(".p", 1)[1]))),), out, guard))
    lines += [(letter.lower(), (a,), None, tuple(sorted(set(guard) | {m}))) for m in reads]
    return lines


@lru_cache(maxsize=None)
def proved(letter: str, k: int) -> bool:
    """`chain(letter, p0, p1, k relays)` is `ppm letter p0 letter p1` once the new outcomes are
    forgotten (`codesign.equal_drop`)."""
    from .codesign import equal_drop
    from .program import Template
    lines = chain(letter, "p0", "p1", [f"l{i}" for i in range(k)], "o0")
    names = {"o0": "o0"}

    def nm(o):                            # outcomes o<i> for the template, in order of appearance
        if o not in names:
            names[o] = f"o{len(names)}"
        return names[o]
    text = []
    for w, args, o, g in lines:
        head = f"if {' & '.join(nm(x) for x in g)}: " if g else ""
        if w == "bit":
            text.append(f"bit {' ^ '.join(nm(x) for x in args[0].split(' ^ '))} -> {nm(o)}")
        else:
            text.append(head + " ".join((w,) + args) + (f" -> {nm(o)}" if o else ""))
    a = Template("chain", tuple(text), (0,) * len(text), 2, k, len(names), 0)
    b = Template("ppm", (f"ppm {letter} p0 {letter} p1 -> o0",), (0,), 2, 0, 1, 0)
    return equal_drop(a, b)


def move(letter: str, src: str, dst: str, relays: list, out: str, guard: tuple = ()) -> list:
    """The stream lines that carry `src`'s state onto the fresh block `dst` -- a one-bit teleport,
    its product down a column (Z̄) or along a row (X̄) through `relays`: `dst` made in the other
    basis, the product, `src` read in the other basis, two frames on `dst` (`move_proved`).
    After them `src` is free and its state is `dst`'s."""
    q = OTHER[letter]
    m, r = f"{out}.m", f"{out}.s"
    prod = chain(letter, src, dst, relays, m, guard) if relays else [("ppm", (letter, src, letter, dst), m, guard)]
    return ([("prep", (dst, q), None, guard)] + prod + [("read", (src, q), r, guard)]
            + [(q.lower(), (dst,), None, tuple(sorted(set(guard) | {m}))),
               (letter.lower(), (dst,), None, tuple(sorted(set(guard) | {r})))])


@lru_cache(maxsize=None)
def move_proved(letter: str) -> bool:
    """`move` (no relays: a chain is its product, `proved`) is a SWAP into a fresh |0̄>, its
    outcomes forgotten -- with `src` made |0̄> again after, as the reference leaves it."""
    from .codesign import equal_drop
    from .program import Template
    q = OTHER[letter]
    lines = (f"prep l0 {q}", f"ppm {letter} p0 {letter} l0 -> o0", f"read p0 {q} -> o1",
             f"if o0: {q.lower()} l0", f"if o1: {letter.lower()} l0", "prep p0 Z")
    ref = ("prep l0 Z", "cx p0 l0", "cx l0 p0")
    return equal_drop(Template("move", lines, (0,) * 6, 1, 1, 2, 0), Template("swap", ref, (0,) * 3, 1, 1, 0, 0))


def line(ta: tuple, tb: tuple) -> list:
    """The tiles strictly between two tiles of one row or one column, from ta's side."""
    (xa, ya), (xb, yb) = ta, tb
    if xa == xb:
        s = 1 if yb > ya else -1
        return [(xa, y) for y in range(ya + s, yb, s)]
    if ya == yb:
        s = 1 if xb > xa else -1
        return [(x, ya) for x in range(xa + s, xb, s)]
    raise ValueError(f"{ta} and {tb} share no row or column")


def _life(stream, k, blk):
    """The lines after k that use fresh block `blk`, up to its readout."""
    out = []
    for j in range(k + 1, len(stream)):
        w, a, _, _ = stream[j]
        if w == "ppm" and blk in a[1::2] or w in ("read", "ht") and a[0] == blk:
            out.append(j)
            if w == "read":
                break
    return out


def crossings(stream, homes: dict) -> dict:
    """{ancilla: tile} for each fresh block whose products until its readout are one Z̄ product
    with a block at home and one X̄ product with another: the tile in the first one's column and
    the second one's row, when no block lives there."""
    taken = set(homes.values())
    pins = {}
    for k, (w, a, _, _) in enumerate(stream):
        if w != "prep" or a[0] in homes:
            continue
        blk = a[0]
        prods = [stream[j][1] for j in _life(stream, k, blk) if stream[j][0] == "ppm"]
        if len(prods) != 2 or any(len(p) != 4 for p in prods):
            continue
        part = {}
        for p in prods:
            other = p[3] if p[1] == blk else p[1]
            if p[0] == p[2] and other in homes:
                part.setdefault(p[0], other)
        if set(part) == {"Z", "X"}:
            tile = (homes[part["Z"]][0], homes[part["X"]][1])
            if tile not in taken:
                pins[blk] = tile
    return pins


def relay(stream, at: dict):
    """(stream, {relay: tile}, [original line of each line]): every two-patch product of one
    letter whose patches (`at`: block -> tile, homes and pins) sit apart in one column (Z̄) or
    one row (X̄) rewritten as a chain on the tiles between; the relays `r<line>.<i>`."""
    new, pins, src = [], {}, []
    for k, (w, a, o, g) in enumerate(stream):
        if w == "ppm" and len(a) == 4 and a[0] == a[2] and a[1] in at and a[3] in at:
            ta, tb = at[a[1]], at[a[3]]
            axis = 0 if a[0] == "Z" else 1            # Z̄: same x (a column); X̄: same y (a row)
            if ta[axis] == tb[axis] and abs(ta[1 - axis] - tb[1 - axis]) > 1:
                tiles = line(ta, tb)
                names = [f"r{k}.{i}" for i in range(len(tiles))]
                pins.update(zip(names, tiles))
                lines = chain(a[0], a[1], a[3], names, o, g)
                new += lines
                src += [k] * len(lines)
                continue
        new.append((w, a, o, g))
        src.append(k)
    return new, pins, src
