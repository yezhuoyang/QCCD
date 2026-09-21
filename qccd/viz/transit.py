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


def _slot_weights(q, t: float, ramp_a: float, ramp_b: float) -> tuple[float, float]:
    """How much of each end's slot offset applies here -- see the JS twin.

    Measured in ARC over a ramp that is as long as the bar keeps the rail beside it,
    which is `Transit._ramp_at`.
    """
    if q is None or len(q) < 9:
        return 1.0 - t, t
    # a ramp of zero is "no bar to slide along", not "no offset" -- see the JS twin
    wa = 1.0 - min(1.0, q[7] / ramp_a) if ramp_a > 1e-9 else 1.0 - t
    wb = 1.0 - min(1.0, q[8] / ramp_b) if ramp_b > 1e-9 else t
    if t <= 0.0:
        return 1.0, 0.0
    if t >= 1.0:
        return 0.0, 1.0
    if wa + wb > 1.0:
        wb = 1.0 - wa
    return wa, wb


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
    #: half the drawn length of a node's site bar -- the scale the detour tapers over.
    #: Not the slot pitch: a capacity-4 trap is 0.88 g long against a lattice step of g.
    span: Callable[[str], float] = lambda _i: 0.0
    #: half its THICKNESS -- how far across its own trap an ion may be drawn.  An ion
    #: outside its own capsule is drawn where no well exists.
    across: Callable[[str], float] = lambda _i: 0.0
    #: half a RAIL's width -- the room there is out between the traps, where ions run
    #: single file and never need to step around one another.
    rail: float = 0.0
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
    #: an upper bound on the mark's radius: half the gap to the nearest ion
    #: it has to pass, so a mark never covers the neighbour it is getting by
    room: float = 0.0
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

    def _span(self, node: str) -> float:
        try:
            return float(self.geom.span(node) or 0.0)
        except Exception:
            return 0.0

    def _across(self, node: str) -> float:
        try:
            return float(self.geom.across(node) or 0.0)
        except Exception:
            return 0.0

    def _ramp_at(self, node: str, toward: str) -> float:
        """Over how much arc this node's slot offset is taken up -- see the JS twin.

        A slot offset lies along the bar, so it never changes how far the ion is from the
        bar's centre line: that distance is the rail's own.  A rail leaving ALONG the bar
        never leaves the line; one leaving ACROSS it departs as fast as the ion travels.
        """
        a = self._across(node)
        if a <= 0:
            return 0.0
        sp, p, o = self._span(node), self._pos(node), self._pos(toward)
        if p is None or o is None:
            return a
        dx, dy = o[0] - p[0], o[1] - p[1]
        d = math.hypot(dx, dy)
        if d <= 1e-9:
            return a
        ax = self._axis(node)
        sin = abs((dx * ax[1] - dy * ax[0]) / d)
        r = a / sin if sin > 1e-6 else (sp or a)
        return min(max(r, a), sp) if sp > 0 else max(r, a)

    def _room_across(self, x: float, y: float, ends) -> float:
        """How much room across the metal there is where this ion stands.

        Half a bar's thickness inside its own trap, half a rail's out between them,
        ramped at slope one between the two -- see the JS twin.
        """
        best = float(self.geom.rail or 0.0)
        for node in ends:
            if not node:
                continue
            a = self._across(node)
            if a <= best:
                continue
            p = self._pos(node)
            if p is None:
                continue
            ax = self._axis(node)
            along = abs((x - p[0]) * ax[0] + (y - p[1]) * ax[1])
            out = along - self._span(node)
            room = a if out <= 0 else (a - out if out < a - best else best)
            best = max(best, room)
        return best

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
            return None if p is None else (p[0], p[1], None, None, 0.0, 0, 0, 0.0, 0.0)
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
            return None if p is None else (p[0], p[1], None, None, 0.0, 0, 0, 0.0, 0.0)
        # the ARC travelled and the arc remaining, which is what the slot offsets are
        # shed and taken up over -- see `_slot_weights`
        done = sum(segs[:i]) + (segs[i] if i < len(segs) else 0.0) * local
        return (q[0], q[1], path[i], path[i + 1], local, i, len(path) - 1,
                done, total - done)

    def point_at_arc(self, path: Sequence[str], arc: float):
        """The point at ARC `arc` along the path, continued along the rail's own tangent
        beyond either end -- see the JS twin."""
        segs, total = self._hops(path)
        if len(path) < 2 or total <= 1e-9:
            return self.point_on_path(path, 0.0)
        if 0.0 <= arc <= total:
            return self.point_on_path(path, arc / total)
        end = arc > total
        q = self.point_on_path(path, 1.0 if end else 0.0)
        if q is None:
            return q
        tg = self.tangent_at(path, end)
        over = arc - total if end else arc
        return (q[0] + tg[0] * over, q[1] + tg[1] * over, q[2], q[3], q[4], q[5], q[6],
                total if end else 0.0, 0.0 if end else total)

    def tangent_at(self, path: Sequence[str], at_end: bool) -> tuple[float, float]:
        """The unit direction of travel at the start or the end of a path."""
        a, b = (path[-2], path[-1]) if at_end else (path[0], path[1])
        p0 = self._edge_point(a, b, 0.98 if at_end else 0.0)
        p1 = self._edge_point(a, b, 1.0 if at_end else 0.02)
        if p0 is None or p1 is None:
            P0, P1 = self._pos(a), self._pos(b)
            if P0 is None or P1 is None:
                return (1.0, 0.0)
            dx, dy = P1[0] - P0[0], P1[1] - P0[1]
        else:
            dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        n = math.hypot(dx, dy)
        return (dx / n, dy / n) if n > 1e-12 else (1.0, 0.0)

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
            # a chain arrives in the order it travelled -- see the JS twin: arrivals from
            # one place into one place go leader first, every other arrival in `pos` order
            arrivals = []
            first: dict[tuple, int] = {}
            for k, (ion, at_node) in enumerate(st.pos.items()):
                at = self._site(at_node)
                if self._pos(at) is None or ion in nxt.get(at, ()):
                    continue
                walk = st.paths.get(ion)
                s0 = walk[0] if walk else st.before.get(ion)
                sp = self._site(s0) if s0 is not None else None
                g = first.setdefault((at, sp), len(arrivals))
                lead = 0.0
                ls = cur.get(sp) if sp is not None else None
                if ls and len(ls) > 1 and ion in ls:
                    po, pf, axs = self._pos(at), self._pos(sp), self._axis(s0)
                    if po is not None and pf is not None:
                        lead = (ls.index(ion) - (len(ls) - 1) / 2) * (
                            (po[0] - pf[0]) * axs[0] + (po[1] - pf[1]) * axs[1])
                arrivals.append((g, -lead, k, ion, at_node))
            arrivals.sort(key=lambda r: (r[0], r[1], r[2]))
            for _g, _l, _k, ion, at_node in arrivals:
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
        # which ions are trading ends of one run: the one conflict with no on-rail
        # drawing, and a step R5 refuses, so this is empty on a verified programme
        exch = {i for p in pairs if p[2] == "exchange" for i in p[:2]}
        return {"pairs": pairs, "side": sides, "kinds": seen, "exchange": exch}

    # ------------------------------------------------------------------ placement

    def place(self, step: Step, t: float, ord_start, ord_end,
              rest: bool = False) -> dict[str, Placed]:
        """Where every ion is drawn at `t` in [0, 1] of one step."""
        t = min(1.0, max(0.0, float(t)))
        src, dst, occ_s, occ_e = {}, {}, {}, {}
        src_node, dst_node = {}, {}
        for ion, node in step.pos.items():
            walk = step.paths.get(ion)
            a = self._site(walk[0] if walk else node)
            b = self._site(walk[-1] if walk else node)
            if self._pos(a) is None or self._pos(b) is None:
                continue
            src[ion], dst[ion] = a, b
            src_node[ion] = walk[0] if walk else node
            dst_node[ion] = walk[-1] if walk else node
            occ_s.setdefault(a, []).append(ion)
            occ_e.setdefault(b, []).append(ion)

        def order_key(ord_tbl, place):
            lst = ord_tbl.get(place, [])
            return lambda ion: (lst.index(ion) if ion in lst else 10 ** 9, natural_key(ion))

        for place, ions in occ_s.items():
            ions.sort(key=order_key(ord_start, place))
        for place, ions in occ_e.items():
            ions.sort(key=order_key(ord_end, place))

        # The stack belongs to the PLACE, the direction to the NODE -- see the JS twin.
        # Taking the axis from the place's representative points the slot offset along
        # whatever rail that node happens to sit on, which on a device that piles several
        # nodes at one coordinate is not the rail this ion is riding.
        def slot_at(place, node, lst, ion):
            k = len(lst)
            j = lst.index(ion) if ion in lst else 0
            off, pitch = self._slots(place, k)
            ax = self._axis(node)
            o = off[j] if j < len(off) else 0.0
            return (ax[0] * o, ax[1] * o, pitch if k > 1 else 0.0, place, node)

        pas = self.passes(step, ord_start, ord_end)
        bow0 = float(self.geom.bow or 0.0)

        # ---- pass one: where everyone is before anybody gets out of anybody's way
        base: dict[str, dict] = {}
        for ion in src:
            walk = step.paths.get(ion)
            ax_, ay_, pa, a_node, a_axis = slot_at(
                src[ion], src_node[ion], occ_s[src[ion]], ion)
            bx_, by_, pb, b_node, b_axis = slot_at(
                dst[ion], dst_node[ion], occ_e[dst[ion]], ion)
            if walk and not rest:
                q0 = self.point_on_path(walk, t)
                if q0 is None or not math.isfinite(q0[0]):
                    continue
                # the slot offset belongs to the trap, not to the walk; its part ALONG the
                # rail is carried as a shift in arc, so two ions in one well keep their
                # spacing, and only the rest is ramped within the bars -- see the JS
                tA, tB = self.tangent_at(walk, False), self.tangent_at(walk, True)
                aA = ax_ * tA[0] + ay_ * tA[1]
                aB = bx_ * tB[0] + by_ * tB[1]
                tot = self._hops(walk)[1]
                q = self.point_at_arc(walk, tot * t + aA + (aB - aA) * t) or q0
                e0, e1 = self.point_at_arc(walk, aA), self.point_at_arc(walk, tot + aB)
                qs, qe = self.point_on_path(walk, 0.0), self.point_on_path(walk, 1.0)
                rAx = qs[0] + ax_ - e0[0] if (qs and e0) else ax_
                rAy = qs[1] + ay_ - e0[1] if (qs and e0) else ay_
                rBx = qe[0] + bx_ - e1[0] if (qe and e1) else bx_
                rBy = qe[1] + by_ - e1[1] if (qe and e1) else by_
                wa, wb = _slot_weights(q, t, self._ramp_at(a_axis, walk[1]),
                                       self._ramp_at(b_axis, walk[-2]))
                # the ends in exactly the arithmetic they always had -- see the JS twin
                if t <= 0:
                    x, y, q = q0[0] + ax_, q0[1] + ay_, q0
                elif t >= 1:
                    x, y, q = q0[0] + bx_, q0[1] + by_, q0
                else:
                    x, y = q[0] + rAx * wa + rBx * wb, q[1] + rAy * wa + rBy * wb
                base[ion] = dict(x=x, y=y, fly=True, q=q, u=t,
                                 pa=pa, pb=pb, a=a_node, b=b_node,
                                 ax=a_axis, bx=b_axis)
                if qs and qe:
                    base[ion]["ends"] = ((qs[0] + ax_, qs[1] + ay_), (qe[0] + bx_, qe[1] + by_))
            else:
                p0, p1 = self._pos(a_node), self._pos(b_node)
                if p0 is None or p1 is None:
                    continue
                u = 1.0 if rest else t
                x0, y0 = p0[0] + ax_, p0[1] + ay_
                x1, y1 = p1[0] + bx_, p1[1] + by_
                base[ion] = dict(x=x0 + (x1 - x0) * u, y=y0 + (y1 - y0) * u, fly=False,
                                 q=None, u=u, pa=pa, pb=pb, a=a_node, b=b_node,
                                 ax=a_axis, bx=b_axis, ends=((x0, y0), (x1, y1)))

        partners: dict[str, list[str]] = {}
        for a, b, _ in pas["pairs"]:
            partners.setdefault(a, []).append(b)
            partners.setdefault(b, []).append(a)

        # THE DETOUR IS EXACTLY AS BIG AS IT HAS TO BE.  See the JS twin for why: an ion
        # already `clear` of everything it must pass needs no detour and is drawn on the
        # rail, and one that is closer is lifted by the perpendicular leg that restores
        # `clear`.  It vanishes on its own at both ends of a walk, so it needs no ramp,
        # and nothing rests at a junction, so a crossing is drawn on the metal.
        # less than one slot pitch, or the detour never switches off and an ion is
        # lifted where it stands -- see the JS twin
        def clear_of(pitch):
            return 0.95 * pitch if pitch > 0 else 0.62 * bow0
        # A PASS IS TWO IONS GOING ROUND EACH OTHER, AND BOTH OF THEM MOVE -- see the JS
        # twin.  Lifting only the one in flight took the whole of the room out of one side
        # of the bar and drew the swap as two specks sliding through each other; each of
        # the pair takes HALF the perpendicular leg that restores `clear`, on opposite
        # sides.  Per pair, on the wider of the two traps, and never more than the pair is
        # apart at either end of the step, so the detour is zero at both frame boundaries.
        need: dict[str, float] = {}
        cause: dict[str, str] = {}
        for a, b, _ in pas["pairs"]:
            ma, mb = base.get(a), base.get(b)
            if ma is None or mb is None or not (ma["fly"] or mb["fly"]):
                continue
            clear = clear_of(max(ma["pa"], ma["pb"], mb["pa"], mb["pb"]))
            if "ends" in ma and "ends" in mb:
                (a0, a1), (b0, b1) = ma["ends"], mb["ends"]
                clear = min(clear, math.dist(a0, b0), math.dist(a1, b1))
            if clear <= 0:
                continue
            d = math.dist((ma["x"], ma["y"]), (mb["x"], mb["y"]))
            if d >= clear:
                continue
            h = 0.5 * math.sqrt(max(0.0, clear * clear - d * d))
            if a not in need or need[a] < h:
                need[a], cause[a] = h, b
            if b not in need or need[b] < h:
                need[b], cause[b] = h, a

        # And never off the metal, taking HALF the room so the mark fits in the other
        # half.  The ion in flight goes to the side `passes` gave it; the one it is
        # getting past goes to the other side OF IT, or straight away from it where the
        # two bars are square to each other -- see the JS twin.
        lift: dict[str, tuple] = {}
        order = [i for i in need if base[i]["fly"]] + [i for i in need if not base[i]["fly"]]
        for ion in order:
            me, h = base[ion], need[ion]
            q = me.get("q") or (me["x"], me["y"])
            lim = self._room_across(q[0], q[1], [me.get("ax") or me["a"],
                                                 me.get("bx") or me["b"]])
            if lim > 0 and h > 0.5 * lim:
                h = 0.5 * lim
            if not h > 0:
                continue
            axn = self._axis(me.get("ax") or me["a"])
            nx, ny = -axn[1], axn[0]
            if me["fly"]:
                sd = pas["side"].get(ion, 1) or 1
            else:
                sd = 1
                c = cause[ion]
                v, o = lift.get(c), base.get(c)
                dot = (nx * v[0] + ny * v[1]) / v[2] if v else 0.0
                if abs(dot) > 0.5:
                    sd = -1 if dot > 0 else 1
                elif o is not None and nx * (me["x"] - o["x"]) + ny * (me["y"] - o["y"]) < 0:
                    sd = -1
            lift[ion] = (nx * sd * h, ny * sd * h, h, lim)

        out: dict[str, Placed] = {}
        for ion, me in base.items():
            mates = partners.get(ion) or []
            up = lift.get(ion)
            dx, dy = (up[0], up[1]) if up else (0.0, 0.0)
            room = 0.0
            # A mark is never wider than half the space it is in, measured against every
            # ion around it and not only the ones `passes` calls partners, to where each
            # of them is DRAWN, and for the ion standing still as much as the one moving
            # -- see the JS twin.
            nbrs = set(mates)
            nbrs.update(occ_s.get(me["a"]) or ())
            nbrs.update(occ_e.get(me["b"]) or ())
            nbrs.discard(ion)
            gap = math.inf
            for mate in nbrs:
                o2 = base.get(mate)
                if o2 is not None:
                    u2 = lift.get(mate)
                    gap = min(gap, math.dist((me["x"] + dx, me["y"] + dy),
                                             (o2["x"] + (u2[0] if u2 else 0.0),
                                              o2["y"] + (u2[1] if u2 else 0.0))))
            if math.isfinite(gap):
                room = 0.45 * gap
            if up and up[3] > 0:
                room = min(room or up[3], up[3] - up[2])
            if me["fly"]:
                tight = (len(ord_start.get(me["a"], [])) > 1
                         or len(ord_end.get(me["b"], [])) > 1)
                q = me["q"]
                out[ion] = Placed(
                    x=me["x"] + dx, y=me["y"] + dy, fly=True, node=me["b"], t=t,
                    pitch_a=me["pa"], pitch_b=me["pb"], swap=bool(mates), tight=tight,
                    room=room,
                    hop=(q[2], q[3], q[4]) if (q and q[2] and q[3]) else None)
            else:
                pa, pb, u = me["pa"], me["pb"], me["u"]
                out[ion] = Placed(
                    x=me["x"] + dx, y=me["y"] + dy, fly=False, node=me["b"], t=u,
                    room=room,
                    pitch=(pa + (pb - pa) * u) if (pa and pb) else (pb or pa))
        return out
