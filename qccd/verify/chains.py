"""Chain order: where in its trap each ion stands, step by step.

The replay knows which site an ion is in, not where in the chain (#22).  Two things need the
order: a broadcast laser addresses one chain position at a time (R34, the team's feedback F2), and
a gate swap acts on two particular ions, the one leaving and the one at the end it leaves by.  This
module keeps the order by four rules, the same for a compiler laying a programme out and for the
rules judging it:

* **a site's frame.**  Position 1 is the end toward the previous node of the loop the site is a
  slot of, so every trap of a ring is the same trap; on any other site it is the end with the
  smaller coordinate along the axis its rails mostly run.
* **the start.**  An `init` puts a site's ions in the order it lists them.
* **leaving.**  An ion leaves by the end facing the rail it leaves on.  Ions leaving one site by
  one rail in one step go as a train, the one nearest that end first.  In a step that pays for gate
  swaps (`meta.gate_swaps`, the Cyclone deck's S7), an ion with others between it and that end
  first swaps places with the ion at the end, three two-ion gates between them: `swaps` records
  each (ion, partner).  Without one it leaves anyway, and `from_inside` records it.
* **arriving.**  An ion arrives at the end facing the rail it arrives on.  A train keeps its
  order, so its leader ends up innermost.

Nothing here changes what the replay does or what any older rule judges.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

__all__ = ["Chains", "frames", "pattern_text"]

_EPS = 1e-9


def frames(dev) -> dict[str, dict[str, int]]:
    """site -> {rail id: 0 or 1}: which end of the site each of its rails meets, 0 being
    position 1's end."""
    slot_of: dict[str, tuple] = {}
    for lp in dev.loops.values():
        k = len(lp.nodes)
        for i, n in enumerate(lp.nodes):
            if lp.closed or 0 < i < k - 1:
                slot_of.setdefault(n, (lp.nodes[(i - 1) % k], lp.nodes[(i + 1) % k]))
    out: dict[str, dict[str, int]] = {}
    for n, node in dev.nodes.items():
        if node.kind != "site":
            continue
        here = node.pos
        vec = {}
        for sid in dev.incidence[n]:
            there = dev.nodes[dev.segments[sid].other(n)].pos
            vec[sid] = (there[0] - here[0], there[1] - here[1])
        if n in slot_of:
            prev, nxt = slot_of[n]
            a, b = dev.nodes[prev].pos, dev.nodes[nxt].pos
            axis = (b[0] - a[0], b[1] - a[1])
            if abs(axis[0]) <= _EPS and abs(axis[1]) <= _EPS:
                axis = (1.0, 0.0)
            ends = {}
            for sid, (dx, dy) in vec.items():
                other = dev.segments[sid].other(n)
                if other == prev:
                    ends[sid] = 0
                elif other == nxt:
                    ends[sid] = 1
                else:
                    ends[sid] = 0 if dx * axis[0] + dy * axis[1] < 0 else 1
            out[n] = ends
            continue
        along_x = sum(abs(dx) for dx, _ in vec.values()) >= sum(abs(dy) for _, dy in vec.values())
        ends = {}
        for sid, (dx, dy) in vec.items():
            main, side = (dx, dy) if along_x else (dy, dx)
            ends[sid] = (0 if main < 0 else 1) if abs(main) > _EPS else (0 if side < 0 else 1)
        out[n] = ends
    return out


def pattern_text(pat: tuple[int, tuple[int, ...]] | None) -> str:
    """`(3, (1, 2))` as "1,2 of 3"."""
    if pat is None:
        return "none"
    n, pos = pat
    return f"{','.join(str(p) for p in pos)} of {n}"


class Chains:
    """The order of every site's chain, kept through a programme's moves."""

    def __init__(self, dev, frame: Mapping[str, Mapping[str, int]] | None = None):
        self.dev = dev
        self.frame = dict(frame) if frame is not None else frames(dev)
        self.order: dict[str, list[str]] = {}
        self.at: dict[str, str] = {}

    # ------------------------------------------------------------------ state

    def start(self, placement: Mapping[str, str]) -> None:
        """An `init`: every ion it names where it says, in the order it lists them, after any
        already there; an ion it does not name stays where it was."""
        for ion, site in placement.items():
            old = self.at.get(ion)
            if old is not None:
                self.order[old].remove(ion)
                if not self.order[old]:
                    del self.order[old]
            self.order.setdefault(site, []).append(ion)
            self.at[ion] = site

    def chain(self, site: str) -> list[str]:
        return list(self.order.get(site, ()))

    def position(self, ion: str) -> int | None:
        """1-based, from the site's position-1 end."""
        site = self.at.get(ion)
        if site is None:
            return None
        return self.order[site].index(ion) + 1

    def pattern(self, ions: Iterable[str]) -> tuple[int, tuple[int, ...]] | None:
        """What a laser must address to reach `ions`: their chain's length and their positions
        in it.  None if they are not all in one site's chain."""
        ions = list(ions)
        sites = {self.at.get(i) for i in ions}
        if len(sites) != 1 or None in sites:
            return None
        site = sites.pop()
        chain = self.order[site]
        return len(chain), tuple(sorted(chain.index(i) + 1 for i in ions))

    # ------------------------------------------------------------------ moves

    def _end(self, site: str, rail: str | None) -> int:
        ends = self.frame.get(site, {})
        if rail in ends:
            return ends[rail]
        return 1

    def move(self, moves: Sequence[tuple[str, str, str, str | None, str | None]], *,
             gate_swaps: bool = False) -> dict:
        """One step's moves, each `(ion, src, dst, first rail, last rail)`.  Returns
        `{"swaps": [(ion, partner), ...], "from_inside": [ion, ...]}`."""
        swaps: list[tuple[str, str]] = []
        inside: list[str] = []
        trains: dict[tuple[str, str | None], list[tuple]] = {}
        listed: set[str] = set()
        for m in moves:
            ion, src, dst, first, last = m
            # an ion listed twice in one step (R8 refuses it) moves by its first listing here
            if src == dst or ion in listed:
                continue
            listed.add(ion)
            trains.setdefault((src, first), []).append(m)
        arriving: list[tuple[int, tuple]] = []
        for (src, first), group in trains.items():
            chain = self.order.get(src, [])
            end = self._end(src, first)
            leaving = {m[0] for m in group}
            # nearest the exit first
            def dist(i: str) -> int:
                k = chain.index(i)
                return k if end == 0 else len(chain) - 1 - k
            order = sorted((m for m in group if m[0] in chain), key=lambda m: dist(m[0]))
            for rank, m in enumerate(order):
                ion = m[0]
                k = chain.index(ion)
                edge = rank if end == 0 else len(chain) - 1 - rank
                if k != edge:
                    blockers = chain[min(k, edge):max(k, edge) + 1]
                    if any(b not in leaving for b in blockers):
                        if gate_swaps:
                            partner = chain[edge]
                            chain[k], chain[edge] = chain[edge], chain[k]
                            swaps.append((ion, partner))
                        else:
                            inside.append(ion)
            for rank, m in enumerate(order):
                arriving.append((rank, m))
            # an ion the order does not hold here (a placement it missed) still arrives, after the train
            for rank, m in enumerate((m for m in group if m[0] not in chain), start=len(order)):
                arriving.append((rank, m))
            for m in order:
                if m[0] in chain:
                    chain.remove(m[0])
                self.at.pop(m[0], None)
            if not chain:
                self.order.pop(src, None)
        # arrivals: the train's leader (rank 0) goes in first, so it ends innermost
        for rank, m in sorted(arriving, key=lambda x: x[0]):
            ion, src, dst, first, last = m
            stale = self.at.pop(ion, None)
            if stale is not None and ion in self.order.get(stale, ()):
                self.order[stale].remove(ion)      # held elsewhere by a placement the order missed
                if not self.order[stale]:
                    del self.order[stale]
            chain = self.order.setdefault(dst, [])
            if self._end(dst, last) == 0:
                chain.insert(0, ion)
            else:
                chain.append(ion)
            self.at[ion] = dst
        return {"swaps": swaps, "from_inside": inside}

    def route_rails(self, src: str, dst: str, via: Sequence[str]) -> tuple[str | None, str | None]:
        """The rail a participant leaves `src` on and the one it reaches `dst` by."""
        if via:
            return via[0], via[-1]
        try:
            sid = self.dev.segment_between(src, dst).id
        except KeyError:
            return None, None
        return sid, sid
