"""A CZ by a pair made ahead (docs/FULLSTACK_PLAN.md, slice S9 step 5): the H̄ off the chain.

The surgery machine's CZ (`codesign._cz_turn`) is a fresh L in |+̄>, Z̄_a Z̄_L, H̄ on L, Z̄_L Z̄_b,
L read in X̄ -- a merge, the H̄ (679.8 ms, five merge-times) and a merge, all after its guard is
known.  Gidney 2025's measurement-based uncomputes are chains of such CZs, each guarded by the
readout before it.  The same CZ by a pair made before the guard is known:

    prep A Z; prep B Z; ppm X A X B -> p; ht B          the pair (I ⊗ H̄)|Φ+>, no guard
    if g: ppm Z a Z A -> m;  if g: ppm Z b Z B -> n     two merges side by side
    read A X -> s;  read B X -> t
    if g & p: z a;  if g & n: z a;  if g & s: z a;  if g & m: z b;  if g & t: z b

The pair's stabilizers are ±X̄_A Z̄_B (the sign p) and Z̄_A X̄_B; merging Z̄_a Z̄_A and Z̄_b Z̄_B and
reading A and B in X̄ maps X̄_a -> X̄_a Z̄_b and X̄_b -> X̄_b Z̄_a and leaves Z̄_a, Z̄_b alone: a CZ,
the signs its frames.  When g fails the pair is made and read and touches nothing.  Each shape
of segment met, and its pair, is proved against `ht b; cx a b; ht b` under the guard
(`codesign.equal_drop`) before it is replaced.

Where the pair goes is the floorplan's: `place` puts A facing a and B facing b, each on a Z̄
side, the two closest such tiles; the pair's own product, made before the guard, is a corridor
or a relay chain between them.

    pairs(stream)               ->  (stream, [(A, B, a, b)])
    place(found, homes, taken)  ->  {block: tile}
"""
from __future__ import annotations

from collections import Counter
from functools import lru_cache

__all__ = ["pair_lines", "proved", "pairs", "place"]

FRAMES = ("x", "y", "z")


def pair_lines(a: str, b: str, A: str, B: str, outs: tuple, guard: tuple = ()) -> list:
    """The stream lines of CZ a b under `guard` by the pair A, B; `outs` = the merges' outcomes
    and A's readout (the pair's product is `<s>.p`, B's readout `<s>.t`)."""
    m, n, s = outs
    p, t = f"{s}.p", f"{s}.t"
    g = tuple(sorted(set(guard)))
    fr = lambda o: tuple(sorted(set(guard) | {o}))
    return [("prep", (A, "Z"), None, ()), ("prep", (B, "Z"), None, ()),
            ("ppm", ("X", A, "X", B), p, ()), ("ht", (B,), None, ()),
            ("ppm", ("Z", a, "Z", A), m, g), ("ppm", ("Z", b, "Z", B), n, g),
            ("read", (A, "X"), s, ()), ("read", (B, "X"), t, ()),
            ("z", (a,), None, fr(p)), ("z", (a,), None, fr(n)), ("z", (a,), None, fr(s)),
            ("z", (b,), None, fr(m)), ("z", (b,), None, fr(t))]


def _text(lines, blocks: dict, guard: tuple) -> tuple:
    """A segment as a template's text: `blocks` renamed, its outcomes o0, o1, .. in order, its
    guard (read only whole) the outside outcome e0."""
    gs, outs = set(guard), {}

    def nm(o):
        if o.startswith("!"):
            raise ValueError(f"a negated outcome {o} past the guard")
        outs.setdefault(o, f"o{len(outs)}")
        return outs[o]
    text = []
    for w, args, o, g in lines:
        conds = (["e0"] if gs and gs <= set(g) else []) + [nm(x) for x in g if x not in gs]
        head = f"if {' & '.join(conds)}: " if conds else ""
        text.append(head + " ".join([w] + [blocks.get(x, x) for x in args]) + (f" -> {nm(o)}" if o else ""))
    return tuple(text), len(outs)


@lru_cache(maxsize=None)
def _is_cz(text: tuple, n_locals: int, n_out: int, guarded: bool) -> bool:
    from .codesign import equal_drop
    from .program import Template
    G = "if e0: " if guarded else ""
    ref = (f"{G}ht p1", f"{G}cx p0 p1", f"{G}ht p1")
    return equal_drop(Template("seg", text, (0,) * len(text), 2, n_locals, n_out, int(guarded)),
                      Template("cz", ref, (0,) * 3, 2, 0, 0, int(guarded)))


def proved(lines, a: str, b: str, ancillas, guard: tuple = ()) -> bool:
    """`lines` (on a, b and the fresh `ancillas`, under `guard`) is CZ a b once their outcomes
    are forgotten."""
    blocks = {a: "p0", b: "p1", **{x: f"l{i}" for i, x in enumerate(ancillas)}}
    text, n_out = _text(lines, blocks, guard)
    return _is_cz(text, len(ancillas), n_out, bool(guard))


def _match(stream, k, refs):
    """(end, a, b, L, (m, n, s), guard) for the surgery machine's CZ at line k -- `prep L X; ppm
    Z a Z L -> m; ht L; ppm Z L Z b -> n; read L X -> s` under one guard (the preparation under
    none), then the frames that read m, n, s, which nothing else reads -- or None."""
    if k + 5 > len(stream):
        return None
    seg = stream[k:k + 5]
    if tuple(w for w, *_ in seg) != ("prep", "ppm", "ht", "ppm", "read") or len({tuple(g) for *_, g in seg[1:]}) != 1 \
            or tuple(seg[0][3]) not in ((), tuple(seg[1][3])):   # a preparation takes no guard (`program.UNGUARDABLE`)
        return None
    (_, x0, _, _), (_, x1, m, g), (_, x2, _, _), (_, x3, n, _), (_, x4, s, _) = seg
    x0, x1, x2, x3, x4, g = map(tuple, (x0, x1, x2, x3, x4, g))     # `Farm.ports` writes lists
    L = x0[0]
    if x0[1] != "X" or x2[0] != L or x4 != (L, "X") or len(x1) != 4 or len(x3) != 4 \
            or x1[0] != "Z" or x1[2] != "Z" or x3[0] != "Z" or x3[2] != "Z" or L not in x1 or L not in x3:
        return None
    a = x1[3] if x1[1] == L else x1[1]
    b = x3[3] if x3[1] == L else x3[1]
    if a == b or L in (a, b):
        return None
    end, gs, seen = k + 5, set(g), Counter()
    while end < len(stream) and stream[end][0] in FRAMES:      # (frames: guard g and one outcome)
        rest = set(stream[end][3]) - gs
        if not gs <= set(stream[end][3]) or len(rest) != 1 or not rest <= {m, n, s}:
            break
        seen.update(rest)
        end += 1
    if any(refs[o] != seen[o] for o in (m, n, s)):
        return None
    return end, a, b, L, (m, n, s), g


def pairs(stream):
    """(stream, [(A, B, a, b)]): every surgery-machine CZ in `stream` (`_match`) by a pair made
    ahead, A = `<L>.A` and B = `<L>.B`; each segment and its replacement proved CZ (`proved`)."""
    from .lattice import _names
    refs = Counter()
    for w, a, o, g in stream:
        refs.update(x.lstrip("!") for x in g)
        if w == "bit":
            refs.update(_names(a[0]))
    out, found, k = [], [], 0
    while k < len(stream):
        hit = _match(stream, k, refs)
        if hit is None:
            out.append(stream[k])
            k += 1
            continue
        end, a, b, L, outs, g = hit
        A, B = f"{L}.A", f"{L}.B"
        new = pair_lines(a, b, A, B, outs, g)
        # when g fails the segment's L is made and never touched again: no part of the channel,
        # so it is proved with its preparation under g too
        if not proved([stream[k][:3] + (g,)] + stream[k + 1:end], a, b, [L], g):
            raise AssertionError(f"line {k}: the segment is not CZ {a} {b}")
        if not proved(new, a, b, [A, B], g):
            raise AssertionError(f"line {k}: the pair is not CZ {a} {b}")
        out += new
        found.append((A, B, a, b))
        k = end
    return out, found


def place(found, homes: dict, taken=()) -> dict:
    """{block: tile}: for each pair (A, B, a, b) with a and b at home, A on a tile facing a on
    a Z̄ side and B on one facing b, two different tiles that are no home and not `taken`,
    B's right tile (its H̄'s) neither -- the closest two such.  (The pair's own product is then
    a short corridor, a seam, or -- in one row -- a relay chain.)"""
    from .relay import beside
    busy = set(homes.values()) | set(taken)
    pins = {}
    for A, B, a, b in found:
        if a not in homes or b not in homes:
            continue
        best = None
        for ta in (beside(homes[a], "Z"), beside(homes[a], "Z", True)):
            for tb in (beside(homes[b], "Z"), beside(homes[b], "Z", True)):
                right = (tb[0] + 1, tb[1])
                if ta == tb or right == ta or {ta, tb, right} & busy:
                    continue
                d = abs(ta[0] - tb[0]) + abs(ta[1] - tb[1])
                if best is None or d < best[0]:
                    best = (d, ta, tb)
        if best:
            pins[A], pins[B] = best[1], best[2]
    return pins
