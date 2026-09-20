"""Where every ion is while the machine is moving it -- the Python twin of `js/transit.js`.

The law is stated once, in JavaScript, because that is where two canvases run it: the
studio stage (the inline script in `render.py`) and the gadget Design canvas
(`qccd/gadget/web/core.js`).  This module is its twin for the renderers that have no
JavaScript runtime -- today `tools/make_gif.py`, which turns a compiled programme into an
animated GIF and had its own third implementation of "where is this ion right now",
hop-uniform and lexicographically sorted, which is to say wrong in two ways the page had
already been fixed for.

**Twins are compared, not trusted.**  `tests/test_transit_parity.py` runs this module and
`js/transit.js` over the same synthetic programmes and requires agreement to 1e-9, in the
same spirit as `tests/test_engine_parity.py` does for `engine.js`.  A change to one that
is not made to the other fails that test rather than quietly producing two pictures of one
machine.

The geometry is the caller's, exactly as in the JS: a `Geometry` carries the six functions
that differ between canvases (position, axis, slot offsets, hop length, hop point, and the
place a node belongs to) and nothing about frames, pixels or drawing enters here.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

__all__ = ["Geometry", "Placed", "Step", "Transit", "natural_key"]

_NUM = re.compile(r"(\d+)")


def natural_key(name: str) -> tuple:
    """'d10' after 'd2', not before it.

    Plain lexicographic order changes which slot an ion holds as the population around it
    changes, and an ion that changes slot between two frames jumps a whole pitch.
    """
    return tuple(int(p) if p.isdigit() else p.lower() for p in _NUM.split(str(name)))


def _cmp(a: str, b: str) -> int:
    ka, kb = natural_key(a), natural_key(b)
    return -1 if ka < kb else (1 if ka > kb else 0)


@dataclass
class Geometry:
    """The adapter: everything about the drawing that the occupancy law must not know.

    `pos` returns None for a node the device does not have, which is how a programme that
    outlived its device fails safe rather than drawing at NaN.
    """

    pos: Callable[[str], tuple[float, float] | None]
    axis: Callable[[str], tuple[float, float]] = lambda _id: (1.0, 0.0)
    #: `(node, k) -> (offsets, pitch)` -- where `k` ions in one place sit along its axis
    slot_offsets: Callable[[str, int], tuple[Sequence[float], float]] | None = None
    #: drawn length of one hop; the straight-line distance unless the canvas bows its rails
    edge_len: Callable[[str, str], float] | None = None
    #: a point `u` of the way along one hop
    edge_point: Callable[[str, str, float], tuple[float, float] | None] | None = None
    #: which PLACE a node is.  Two nodes at one coordinate are one place to the eye.
    site: Callable[[str], str] = lambda i: i
    #: the detour amplitude an ion takes round one it has to get past
    bow: float = 0.0


@dataclass
class Step:
    """One instruction, as the law sees it."""

    before: Mapping[str, str]           # ion -> node at the start
    pos: Mapping[str, str]              # ion -> node at the end
    paths: Mapping[str, Sequence[str]]  # ion -> node walk, movers only


@dataclass
class Placed:
    """Where one ion is drawn, and what the drawing needs to size the mark."""

    x: float
    y: float
    fly: bool
    node: str
    t: float = 1.0
    pitch: float = 0.0
    pitch_a: float = 0.0
    pitch_b: float = 0.0
    swap: bool = False
    tight: bool = False
    hop: tuple[str, str, float] | None = None   # which hop it is on, and how far along


@dataclass
class Transit:
    geom: Geometry
    _plen: dict = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------ geometry adapter

    def _pos(self, node: str):
        p = self.geom.pos(node)
        if p is None:
            return None
        return (float(p[0]), float(p[1]))

    def _axis(self, node: str) -> tuple[float, float]:
        a = self.geom.axis(node)
        return (float(a[0]), float(a[1])) if a and (a[0] or a[1]) else (1.0, 0.0)

    def _site(self, node: str) -> str:
        return self.geom.site(node) or node

    def _slots(self, node: str, k: int) -> tuple[list[float], float]:
        if self.geom.slot_offsets is None:
            return [0.0] * k, 0.0
        off, pitch = self.geom.slot_offsets(node, k)
        return list(off), float(pitch)

    def _edge_len(self, a: str, b: str) -> float:
        if self.geom.edge_len is not None:
            return float(self.geom.edge_len(a, b))
        p, q = self._pos(a), self._pos(b)
        return math.hypot(q[0] - p[0], q[1] - p[1]) if (p and q) else 0.0

    def _edge_point(self, a: str, b: str, u: float):
        if self.geom.edge_point is not None:
            return self.geom.edge_point(a, b, u)
        p, q = self._pos(a), self._pos(b)
        if p is None or q is None:
            return None
        return (p[0] + (q[0] - p[0]) * u, p[1] + (q[1] - p[1]) * u)

    # -------------------------------------------------------- a point along a path

    def _hops(self, path: Sequence[str]):
        key = tuple(path)
        rec = self._plen.get(key)
        if rec is None:
            segs = [self._edge_len(path[i], path[i + 1]) for i in range(len(path) - 1)]
            rec = (segs, math.fsum(segs))
            self._plen[key] = rec
        return rec

    def point_on_path(self, path: Sequence[str], t: float):
        """BY ARC LENGTH, not by hop count.

        Spreading `t` uniformly over the hops makes an ion crossing a 3-unit segment and
        then a 1-unit one spend half the step on each -- one shuttle drawn at two speeds,
        visibly lurching at the join.  `make_gif.py` did exactly that.
        """
        if not path:
            return None
        if len(path) == 1:
            p = self._pos(path[0])
            return None if p is None else (p[0], p[1], None, None, 0.0)
        u = min(max(t, 0.0), 1.0)
        segs, total = self._hops(path)
        if total <= 1e-9:
            span = (len(path) - 1) * u
            i = min(int(span), len(path) - 2)
            local = span - i
        else:
            d, i = total * u, 0
            while i < len(segs) - 1 and d > segs[i]:
                d -= segs[i]
                i += 1
            local = min(1.0, d / segs[i]) if segs[i] > 1e-9 else 0.0
        q = self._edge_point(path[i], path[i + 1], local)
        if q is None:
            p = self._pos(path[i]) or self._pos(path[i + 1])
            return None if p is None else (p[0], p[1], None, None, 0.0)
        return (q[0], q[1], path[i], path[i + 1], local)

    # ------------------------------------------------------------------ slot order

    def slot_order(self, steps: Sequence[Step]) -> list[dict[str, list[str]]]:
        """The order ions occupy each place at the end of every step, carried FORWARD.

        Re-deriving it inside a step cannot satisfy both invariants at once: within a step
        it must not change (or two ions cross through each other) and across a boundary it
        must not change either (or an ion jumps a whole slot pitch).
        """
        out: list[dict[str, list[str]]] = []
        cur: dict[str, list[str]] = {}
        for st in steps:
            nxt: dict[str, list[str]] = {}
            for place, ions in cur.items():
                keep = [i for i in ions if i in st.pos and self._site(st.pos[i]) == place]
                if keep:
                    nxt[place] = keep
            for ion, at_node in st.pos.items():
                at = self._site(at_node)
                n = self._pos(at)
                if n is None:
                    continue
                lst = nxt.setdefault(at, [])
                if ion in lst:
                    continue
                walk = st.paths.get(ion)
                frm = self._site(walk[0] if walk else st.before.get(ion, at_node))
                o, ax = self._pos(frm), self._axis(at)
                d = ((o[0] - n[0]) * ax[0] + (o[1] - n[1]) * ax[1]) if o else 0.0
                # join at the end you arrive from
                lst.insert(0, ion) if d < 0 else lst.append(ion)
            out.append(nxt)
            cur = nxt
        return out

    # ------------------------------------------------- who has to get past whom

    def passes(self, step: Step, ord_start, ord_end) -> dict:
        """Every unordered pair of ions that cannot both hold a straight line, and the
        side of the axis each one goes round on.  Four kinds: `exchange`, `leave`,
        `arrive`, `over` -- see the JS twin's header for what each means."""
        pairs: list[tuple[str, str, str]] = []
        seen: dict[tuple[str, str], str] = {}

        def add(a: str, b: str, kind: str) -> None:
            key = (a, b) if _cmp(a, b) <= 0 else (b, a)
            if key in seen:
                return
            seen[key] = kind
            pairs.append((key[0], key[1], kind))

        def side(here: str, there: str) -> float:
            n, o, ax = self._pos(here), self._pos(there), self._axis(here)
            return ((o[0] - n[0]) * ax[0] + (o[1] - n[1]) * ax[1]) if (n and o) else 0.0

        def blocked_by(lst, ion, direction, resident):
            if ion not in lst or len(lst) < 2 or not direction:
                return []
            i = lst.index(ion)
            return [lst[k] for k in range(len(lst))
                    if k != i and (k > i if direction > 0 else k < i) and lst[k] in resident]

        at_start: dict[str, list[str]] = {}
        for ion, node in step.before.items():
            at_start.setdefault(self._site(node), []).append(ion)
        ends_at: dict[str, list[str]] = {}
        for ion, walk in step.paths.items():
            if walk and len(walk) > 1:
                ends_at.setdefault(self._site(walk[-1]), []).append(ion)

        for ion, walk in step.paths.items():
            if not walk or len(walk) < 2:
                continue
            frm, to = self._site(walk[0]), self._site(walk[-1])
            if frm == to:
                continue
            for other in ends_at.get(frm, ()):
                if other == ion:
                    continue
                op = step.paths.get(other)
                if op and self._site(op[0]) == to:
                    add(ion, other, "exchange")
            for who in blocked_by(ord_start.get(frm, []), ion, side(frm, to),
                                  ord_end.get(frm, [])):
                add(ion, who, "leave")
            for who in blocked_by(ord_end.get(to, []), ion, side(to, frm),
                                  ord_start.get(to, [])):
                add(ion, who, "arrive")
            for h in range(1, len(walk) - 1):
                at = self._site(walk[h])
                for occ in at_start.get(at, ()):
                    if occ != ion and self._site(step.pos.get(occ, "")) == at:
                        add(ion, occ, "over")

        # A resting ion never gets a detour, so a mover passing one keeps the default
        # side; two ions that BOTH move and must pass each other take opposite sides, or
        # they swing the same way and meet in the middle anyway.
        sides = {a: 1 for p in pairs for a in p[:2]}

        def moves(i: str) -> bool:
            w = step.paths.get(i)
            return bool(w and len(w) > 1 and self._site(w[0]) != self._site(w[-1]))

        adj: dict[str, list[str]] = {}
        for a, b, _ in pairs:
            if moves(a) and moves(b):
                adj.setdefault(a, []).append(b)
                adj.setdefault(b, []).append(a)
        done: set[str] = set()
        for start in sorted(adj, key=natural_key):
            if start in done:
                continue
            comp, queue = [], [start]
            done.add(start)
            while queue:
                cur = queue.pop(0)
                comp.append(cur)
                for nb in adj.get(cur, ()):
                    if nb not in done:
                        done.add(nb)
                        queue.append(nb)
            for c, ion in enumerate(sorted(comp, key=natural_key)):
                sides[ion] = 1 if c % 2 == 0 else -1
        return {"pairs": pairs, "side": sides, "kinds": seen}

    # ------------------------------------------------------------------ placement

    def place(self, step: Step, t: float, ord_start, ord_end,
              rest: bool = False) -> dict[str, Placed]:
        """Where every ion is drawn at `t` in [0, 1] of one step."""
        t = min(1.0, max(0.0, float(t)))
        src, dst, occ_s, occ_e = {}, {}, {}, {}
        for ion, node in step.pos.items():
            walk = step.paths.get(ion)
            a = self._site(walk[0] if walk else node)
            b = self._site(walk[-1] if walk else node)
            if self._pos(a) is None or self._pos(b) is None:
                continue
            src[ion], dst[ion] = a, b
            occ_s.setdefault(a, []).append(ion)
            occ_e.setdefault(b, []).append(ion)

        def order_key(ord_tbl, place):
            lst = ord_tbl.get(place, [])
            return lambda ion: (lst.index(ion) if ion in lst else 10 ** 9, natural_key(ion))

        for place, ions in occ_s.items():
            ions.sort(key=order_key(ord_start, place))
        for place, ions in occ_e.items():
            ions.sort(key=order_key(ord_end, place))

        def slot_at(place, lst, ion):
            k = len(lst)
            j = lst.index(ion) if ion in lst else 0
            off, pitch = self._slots(place, k)
            ax = self._axis(place)
            o = off[j] if j < len(off) else 0.0
            return (ax[0] * o, ax[1] * o, pitch if k > 1 else 0.0, place)

        pas = self.passes(step, ord_start, ord_end)
        bow0 = float(self.geom.bow or 0.0)
        out: dict[str, Placed] = {}
        for ion in src:
            walk = step.paths.get(ion)
            ax_, ay_, pa, a_node = slot_at(src[ion], occ_s[src[ion]], ion)
            bx_, by_, pb, b_node = slot_at(dst[ion], occ_e[dst[ion]], ion)
            if walk and not rest:
                q = self.point_on_path(walk, t)
                if q is None or not math.isfinite(q[0]):
                    continue
                sd = pas["side"].get(ion, 0)
                bow = sd * bow0 * 4 * t * (1 - t) if (sd and bow0) else 0.0
                dx = dy = 0.0
                if bow:
                    axn = self._axis(a_node)
                    dx, dy = -axn[1] * bow, axn[0] * bow
                tight = len(ord_start.get(a_node, [])) > 1 or len(ord_end.get(b_node, [])) > 1
                out[ion] = Placed(
                    x=q[0] + ax_ + (bx_ - ax_) * t + dx,
                    y=q[1] + ay_ + (by_ - ay_) * t + dy,
                    fly=True, node=b_node, t=t, pitch_a=pa, pitch_b=pb,
                    swap=bool(bow), tight=tight,
                    hop=(q[2], q[3], q[4]) if q[2] and q[3] else None)
            else:
                p0, p1 = self._pos(a_node), self._pos(b_node)
                if p0 is None or p1 is None:
                    continue
                u = 1.0 if rest else t
                x0, y0 = p0[0] + ax_, p0[1] + ay_
                x1, y1 = p1[0] + bx_, p1[1] + by_
                out[ion] = Placed(
                    x=x0 + (x1 - x0) * u, y=y0 + (y1 - y0) * u, fly=False, node=b_node,
                    t=u, pitch=(pa + (pb - pa) * u) if (pa and pb) else (pb or pa))
        return out
