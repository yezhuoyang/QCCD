"""Our scheduler for the continuous-time QCCD model of the QCCDSim family.

Given a paper's machine (a `PortGraph` with hard capacities and an initial fill limit), its
duration law and a CX circuit, build a `TimedSchedule` that `timed.check` accepts under the
paper's own rules -- and make it short.

What "legal" means is NOT decided here: `schedule` returns only a schedule that `timed.check`
replayed clean under BOTH chain books QCCDSim's family uses -- `QCCDSIM` (a gate-swapped split
lifts the moving ion out in place) and `QCCDSIM_PHYSICAL` (the end ion's state moves to the
departing ion's old place) -- with the paper's duration law and the circuit.  A candidate that
fails is discarded, never patched.  Only ``gate``, ``split`` (carrying its gate-swap reorder),
``move`` and ``merge`` are emitted (`ALLOWED_KINDS`).

Building blocks:

* `_State` is the machine under construction with an undo log, so a plan is built for real,
  timed exactly, and taken back.  Splits and merges stay in time order per trap (that keeps
  both chain books exact); gates back-fill idle gaps; segments and junctions are reserved
  per interval, not per path.
* Both books are kept side by side.  They agree after a gate swap only when the ion was one
  from the end, or when it crossed the whole chain and the end ion it displaced leaves at
  once through the other side ("cross" + heal).  In sync mode (the default) the builder
  never lets them part: a buried ion first has the ions in its way sent out.
* `_Run` is a list scheduler.  For a gate whose ions are apart it tries every destination
  (either ion's trap, or a third), builds each plan on the undo log, and scores it by its
  finish time plus a look-ahead on the ions' next partners and their windowed target trap
  (Kernighan-Lin over the next gates, anchored to the previous window).
* Initial mapping: spectral order + Kernighan-Lin on the time-weighted interaction graph,
  balanced over the traps in use, each chain ordered so the ion that leaves first through
  a port sits at that end.
* For circuits whose qubits meet their partners in one global order (QFT, quadratic forms)
  there is a "sweep": ranks laid out along the line, the lower rank always travels, gates
  processed traveller by traveller (a trap's end is a stack, so only that keeps the chain
  order right).

`schedule` searches knob settings (`candidates`) within a time budget and keeps the best
checked schedule by makespan or by shuttle count (junction crossings, i.e. `move` events).
"""

from __future__ import annotations

import bisect
import heapq
import math
import random
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .models import QCCDSIM, QCCDSIM_PHYSICAL, QCCDSimParams, qccdsim_duration, qccdsim_gate_us
from .timed import Event, PortGraph, TimedSchedule, check

__all__ = ["schedule", "build", "Knobs", "verify", "SchedError", "ALLOWED_KINDS", "candidates"]

#: The primitives the QCCDSim model has.  The QCCDSim law makes no claim about any other
#: kind (a `hop` or a `swap` would pass the checker in zero time), so we never emit one and
#: `schedule` refuses a schedule that contains one.
ALLOWED_KINDS = ("gate", "split", "move", "merge")

INF = math.inf


class SchedError(RuntimeError):
    """No legal schedule was found with these knobs (a dead end, never a partial result)."""


def _natkey(s: str):
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", str(s))]


# --------------------------------------------------------------------------- device


@dataclass(frozen=True)
class _Leg:
    """One trap-to-trap shuttle that crosses only junctions: split, moves, merge."""

    src: str
    dst: str
    segs: tuple[str, ...]          # s0 (leaves src) .. sk (enters dst)
    nodes: tuple[str, ...]         # n1 .. nk, the junction between s(i-1) and s(i)
    move_us: tuple[float, ...]
    side_out: str
    side_in: str
    est: float                     # split + moves + merge, no reorder


class _Dev:
    def __init__(self, pg: PortGraph, p: QCCDSimParams):
        self.pg = pg
        self.p = p
        self.traps = sorted(pg.sites, key=_natkey)
        self.cap = {t: pg.capacity(t) for t in self.traps}
        self.fill = {t: int(pg.sites[t].get("fill", self.cap[t])) for t in self.traps}
        self.ports = {t: dict(pg.sites[t].get("ports", {})) for t in self.traps}
        inc: dict[str, list[str]] = defaultdict(list)
        for s, (a, b) in pg.segments.items():
            inc[a].append(s)
            inc[b].append(s)
        self.deg = {n: len(inc[n]) for n in pg.nodes}
        sm = float(p.split_merge_us)

        def mv(n: str) -> float:
            return float(p.shuttle_us) + float(p.junction_us[self.deg[n]])

        self.legs: dict[tuple[str, str], _Leg] = {}
        for a in self.traps:
            for s0, side in self.ports[a].items():
                e0 = pg.segments[s0]
                first = e0[1] if e0[0] == a else e0[0]
                heap = [(0.0, 0, s0, first, (s0,), (), ())]
                k = 1
                while heap:
                    c, _, seg, at, segs, nodes, mvs = heapq.heappop(heap)
                    if at in pg.sites:
                        if at == a:
                            continue
                        side_in = self.ports[at].get(seg)
                        if side_in is None:
                            continue
                        leg = _Leg(a, at, segs, nodes, mvs, side, side_in, 2 * sm + c)
                        old = self.legs.get((a, at))
                        if old is None or leg.est < old.est:
                            self.legs[(a, at)] = leg
                        continue
                    if at in nodes:
                        continue
                    for s1 in inc[at]:
                        if s1 == seg:
                            continue
                        x, y = pg.segments[s1]
                        nxt = y if x == at else x
                        d = mv(at)
                        heapq.heappush(heap, (c + d, k, s1, nxt, segs + (s1,), nodes + (at,),
                                              mvs + (d,)))
                        k += 1
        # trap graph: routes by fewest legs, then by time
        self.nbrs = {t: sorted({b for (a, b) in self.legs if a == t}, key=_natkey) for t in self.traps}
        self.route: dict[tuple[str, str], list[str]] = {}
        self.dist: dict[tuple[str, str], int] = {}
        for a in self.traps:
            best = {a: (0, 0.0, [a])}
            heap = [(0, 0.0, a, [a])]
            while heap:
                h, c, t, path = heapq.heappop(heap)
                if best.get(t, (INF,))[:2] < (h, c):
                    continue
                for b in self.nbrs[t]:
                    cand = (h + 1, c + self.legs[(t, b)].est, path + [b])
                    if b not in best or cand[:2] < best[b][:2]:
                        best[b] = cand
                        heapq.heappush(heap, (cand[0], cand[1], b, cand[2]))
            for b, (h, c, path) in best.items():
                self.route[(a, b)] = path
                self.dist[(a, b)] = h
        # estimated time of a route: its legs, plus a reorder in every trap it passes through
        g = min(self.gate_us(t) for t in self.traps) if self.traps else 0
        self.cost: dict[tuple[str, str], float] = {}
        for (a, b), path in self.route.items():
            c = sum(self.legs[(x, y)].est for x, y in zip(path, path[1:]))
            self.cost[(a, b)] = c + max(0, len(path) - 2) * 3 * g
        # a line order of the traps (for directional chain ordering and program-order fill)
        self.line = self._line_order()
        self.lpos = {t: i for i, t in enumerate(self.line)}
        # which traps lie behind each port: the first leg of the route leaves through it
        self.via_side: dict[tuple[str, str], str | None] = {}
        for a in self.traps:
            for b in self.traps:
                if a == b:
                    self.via_side[(a, b)] = None
                elif (a, b) in self.route:
                    nb = self.route[(a, b)][1]
                    self.via_side[(a, b)] = self.legs[(a, nb)].side_out
        self.connected = all((a, b) in self.route for a in self.traps for b in self.traps)

    def _line_order(self) -> list[str]:
        """Traps by distance from one end of the farthest pair (exact for a line)."""
        far = sorted(self.cost, key=lambda ab: (-self.dist[ab], -self.cost[ab], _natkey(ab[0]), _natkey(ab[1])))
        a = far[0][0]
        return sorted(self.traps, key=lambda t: (self.dist.get((a, t), 99), self.cost.get((a, t), INF), _natkey(t)))

    def gate_us(self, trap: str) -> int:
        return qccdsim_gate_us(self.p, self.cap[trap], None, "", "")


# ------------------------------------------------------------------------- resources


class _Busy:
    """Disjoint closed-open busy intervals, sorted."""

    __slots__ = ("s", "e")

    def __init__(self) -> None:
        self.s: list[float] = []
        self.e: list[float] = []

    def earliest(self, t: float, d: float) -> float:
        s, e = self.s, self.e
        i = bisect.bisect_right(e, t)
        n = len(s)
        while i < n and s[i] < t + d:
            if e[i] > t:
                t = e[i]
            i += 1
        return t

    def conflict_end(self, t0: float, t1: float) -> float | None:
        """None if [t0, t1) is free, else the end of the last interval overlapping it."""
        s, e = self.s, self.e
        i = bisect.bisect_right(e, t0)
        end = None
        while i < len(s) and s[i] < t1:
            end = e[i]
            i += 1
        return end

    def add(self, t0: float, t1: float) -> None:
        i = bisect.bisect_left(self.s, t0)
        self.s.insert(i, t0)
        self.e.insert(i, t1)

    def remove(self, t0: float, t1: float) -> None:
        i = bisect.bisect_left(self.s, t0)
        while self.s[i] != t0 or self.e[i] != t1:
            i += 1
        del self.s[i]
        del self.e[i]



_MISSING = object()


class _State:
    """The machine during construction, with an undo log so a plan can be tried and taken back.

    Two chain bookkeepings are kept side by side (`cp` physical, `cq` QCCDSim's); a split is
    legal only when one annotation satisfies both."""

    def __init__(self, dev: _Dev, chains: Mapping[str, list[str]]):
        self.dev = dev
        self.cp = {t: list(chains.get(t, [])) for t in dev.traps}
        self.cq = {t: list(chains.get(t, [])) for t in dev.traps}
        self.loc = {i: t for t, c in self.cp.items() for i in c}
        self.ion_t: dict[str, float] = {i: 0.0 for i in self.loc}
        self.sm_t = {t: 0.0 for t in dev.traps}
        self.busy: dict[tuple[str, str], _Busy] = {}
        self.ev: list[tuple] = []
        self.U: list[Callable[[], None]] = []
        self.g = {t: dev.gate_us(t) for t in dev.traps}
        self.sm = float(dev.p.split_merge_us)
        self.why = "gate"

    # -- undo log -------------------------------------------------------------------
    def mark(self) -> int:
        return len(self.U)

    def rollback(self, m: int) -> None:
        U = self.U
        while len(U) > m:
            U.pop()()

    def B(self, kind: str, key: str) -> _Busy:
        k = (kind, key)
        b = self.busy.get(k)
        if b is None:
            b = self.busy[k] = _Busy()
        return b

    def _reserve(self, kind: str, key: str, t0: float, t1: float) -> None:
        b = self.B(kind, key)
        b.add(t0, t1)
        self.U.append(lambda: b.remove(t0, t1))

    def _set(self, d: dict, k: Any, v: Any) -> None:
        old = d.get(k, _MISSING)
        d[k] = v
        if old is _MISSING:
            self.U.append(lambda: d.pop(k))
        else:
            self.U.append(lambda: d.__setitem__(k, old))

    def _emit(self, rec: tuple) -> None:
        self.ev.append(rec)
        self.U.append(self.ev.pop)

    def _save_chain(self, t: str) -> None:
        p, q = list(self.cp[t]), list(self.cq[t])

        def undo() -> None:
            self.cp[t] = p
            self.cq[t] = q

        self.U.append(undo)

    # -- chain order ---------------------------------------------------------------
    def split_annot(self, trap: str, ion: str, side: str):
        """None (ion at the end in both books), a gate-swap annotation, or False (the books
        need different reorders: no single split satisfies both)."""
        cp, cq = self.cp[trap], self.cq[trap]
        ep = 0 if side == "L" else len(cp) - 1
        np_, nq = cp[ep] != ion, cq[ep] != ion
        if not np_ and not nq:
            return None
        ap = (cp[ep], abs(ep - cp.index(ion))) if np_ else None
        aq = (cq[ep], abs(ep - cq.index(ion))) if nq else None
        if ap and aq and ap != aq:
            return False
        w, h = ap or aq
        return {"kind": "gate", "with": w, "hops": h}

    def _apply_split_chain(self, trap: str, ion: str, side: str) -> None:
        self._save_chain(trap)
        cp = self.cp[trap]
        ep = 0 if side == "L" else len(cp) - 1
        if cp[ep] == ion:
            cp.pop(ep)
        else:
            pos = cp.index(ion)
            cp[pos] = cp[ep]
            cp.pop(ep)
        cq = self.cq[trap]
        eq = 0 if side == "L" else len(cq) - 1
        if cq[eq] == ion:
            cq.pop(eq)
        else:
            cq.pop(cq.index(ion))

    def _apply_merge_chain(self, trap: str, ion: str, side: str) -> None:
        self._save_chain(trap)
        if side == "L":
            self.cp[trap].insert(0, ion)
            self.cq[trap].insert(0, ion)
        else:
            self.cp[trap].append(ion)
            self.cq[trap].append(ion)

    def room(self, trap: str) -> int:
        return self.dev.cap[trap] - len(self.cp[trap])

    # -- primitives ----------------------------------------------------------------
    def leg(self, ion: str, leg: _Leg, t_min: float = 0.0, jit: bool = True) -> float | None:
        """Shuttle ``ion`` along ``leg`` at the earliest legal time; return the merge's end,
        or None (nothing changed) when the split cannot be annotated for both books or the
        destination is full."""
        a, b = leg.src, leg.dst
        if self.loc[ion] != a or self.room(b) < 1:
            return None
        swap = self.split_annot(a, ion, leg.side_out)
        if swap is False:
            return None
        ds = 3 * self.g[a] + self.sm if swap else self.sm
        s0 = max(t_min, self.ion_t[ion], self.sm_t[a])
        plan = self._time_leg(leg, s0, ds)
        if plan is None:
            return None
        if jit:
            s, mv, r = plan
            slack = r - (mv[-1][1] if mv else s + ds)
            if slack > 0:
                alt = self._time_leg(leg, s + slack, ds)
                if alt is not None and alt[2] <= r:
                    plan = alt
        s, mv, r = plan
        # commit
        dev = self.dev
        self._reserve("T", a, s, s + ds)
        segs = leg.segs
        start = s
        for i, (m0, m1) in enumerate(mv):
            self._reserve("N", leg.nodes[i], m0, m1)
            self._reserve("S", segs[i], start, m1)
            start = m0
        self._reserve("S", segs[-1], start, r + self.sm)
        self._reserve("T", b, r, r + self.sm)
        self._emit(("split", s, s + ds, (ion,), a, segs[0], None, None, None, swap, self.why))
        for i, (m0, m1) in enumerate(mv):
            self._emit(("move", m0, m1, (ion,), None, None, segs[i], segs[i + 1], leg.nodes[i], None, self.why))
        self._emit(("merge", r, r + self.sm, (ion,), b, segs[-1], None, None, None, None, self.why))
        self._apply_split_chain(a, ion, leg.side_out)
        self._apply_merge_chain(b, ion, leg.side_in)
        self._set(self.loc, ion, b)
        self._set(self.ion_t, ion, r + self.sm)
        self._set(self.sm_t, a, max(self.sm_t[a], s + ds))
        self._set(self.sm_t, b, r + self.sm)
        return r + self.sm

    def _time_leg(self, leg: _Leg, s: float, ds: float):
        a, b = leg.src, leg.dst
        Ta, Tb = self.B("T", a), self.B("T", b)
        sm = self.sm
        for _ in range(200):
            s = Ta.earliest(s, ds)
            t = s + ds
            mv = []
            occ = []
            start = s
            for i, n in enumerate(leg.nodes):
                d = leg.move_us[i]
                m = self.B("N", n).earliest(t, d)
                occ.append((leg.segs[i], start, m + d))
                mv.append((m, m + d))
                start = m
                t = m + d
            r = Tb.earliest(max(t, self.sm_t[b]), sm)
            occ.append((leg.segs[-1], start, r + sm))
            shift = 0.0
            for sg, x0, x1 in occ:
                e = self.B("S", sg).conflict_end(x0, x1)
                if e is not None:
                    shift = max(shift, e - x0)
                    break
            if shift <= 0:
                return s, mv, r
            s += shift
        return None

    def gate_time(self, a: str, b: str, t_min: float = 0.0) -> float:
        trap = self.loc[a]
        t = max(t_min, self.ion_t[a], self.ion_t[b])
        return self.B("T", trap).earliest(t, self.g[trap])

    def gate(self, a: str, b: str, t_min: float = 0.0) -> float:
        trap = self.loc[a]
        assert self.loc[b] == trap
        t = self.gate_time(a, b, t_min)
        e = t + self.g[trap]
        self._reserve("T", trap, t, e)
        self._emit(("gate", t, e, (a, b), trap, None, None, None, None, None, None))
        self._set(self.ion_t, a, e)
        self._set(self.ion_t, b, e)
        return e

    def exit_kind(self, trap: str, ion: str, side: str):
        """How ``ion`` can leave ``trap`` through ``side``, and the split's annotation.

        "plain"     at the end in both books
        "hop1"      one from the end: the gate swap leaves both books equal
        "cross"     at the OTHER end of a synced chain, which has a port there: after the
                    gate swap the books differ only in where the end ion stands, and sending
                    that ion out through the other side makes them equal again
        "interior"  deeper: a gate swap here leaves the books different for good
        "skew"      the books already differ but one annotation satisfies both
        "conflict"  the books differ and no single annotation satisfies both
        """
        cp, cq = self.cp[trap], self.cq[trap]
        if cp != cq:
            ann = self.split_annot(trap, ion, side)
            if ann is False:
                return "conflict", None
            return ("plain" if ann is None else "skew"), ann
        n = len(cp)
        pos = cp.index(ion)
        ep = 0 if side == "L" else n - 1
        h = abs(ep - pos)
        if h == 0:
            return "plain", None
        ann = {"kind": "gate", "with": cp[ep], "hops": h}
        if h == 1:
            return "hop1", ann
        opp = "R" if side == "L" else "L"
        if pos == n - 1 - ep and opp in self.dev.ports[trap].values():
            return "cross", ann
        return "interior", ann

    def divergent(self) -> int:
        return sum(1 for t in self.dev.traps if self.cp[t] != self.cq[t])

    # -- output ----------------------------------------------------------------------
    def events(self) -> list[Event]:
        order = sorted(range(len(self.ev)), key=lambda i: (self.ev[i][1], i))
        out = []
        for k, i in enumerate(order):
            kind, t0, t1, ions, at, seg, src, dst, via, swap, why = self.ev[i]
            out.append(Event(k, kind, float(t0), float(t1), tuple(ions), at=at, seg=seg, src=src,
                             dst=dst, via=via, swap=swap, meta={"why": why} if why else {}))
        return out



# ---------------------------------------------------------------------------- knobs


@dataclass
class Knobs:
    """Every choice the builder makes that is not forced by the rules.  `candidates` lists
    the settings `schedule` searches; the defaults are a reasonable single try."""

    mapping: str = "part"          # "part" (spectral + KL), "po" (first use), "rank" (sweep layout)
    chain_order: str = "pull"      # in-trap order: "pull" (toward the port used first), "asc"/"desc" (first use)
    traps_used: int | None = None  # how many traps the initial mapping fills (None: fewest)
    tau: float = 0.25              # time weighting of the interaction graph (fraction of the circuit)
    balance: int | None = None     # cap partitions at the even share + this (None: the fill limit)
    kl_rounds: int = 30
    reverse_line: bool = False     # fill the line from the other end
    lookahead: int = 8             # next partners per ion in the look-ahead
    decay: float = 0.7
    alpha: float = 1.0             # weight of the look-ahead (estimated route time to next partners)
    swap_pen: float = 0.0          # extra penalty per gate-swapped split (beyond its time)
    div_pen: float = 300.0         # (sync off) penalty per trap a plan leaves with the books apart
    meet: bool = True              # consider meeting in a third trap
    jit: bool = True
    sync: bool = True              # never let the two chain books part (see _Run.go)
    fallback: bool = True          # sync: when no plan keeps the books equal, allow one that does not
    heal_return: bool = False      # sync: the ion a crossing displaced comes straight back (other end)
    policy: str = "greedy"         # "greedy", or "sweep" (monotone circuits: the lower rank travels)
    rank_sizes: str = "even"       # rank layout: "even" or "load" (fewer ions where more gates wait)
    move_w: float = 0.0            # us charged per junction crossing (shuttle-count objective)
    rank_chain: str = "exit"       # rank layout order in a trap: "exit" (next traveller at the exit end) or "entry"
    defer: int = 0                 # when the chosen gate would evict, look this far for one that need not
    park: int = 0                  # an ion with no gates left leaves a trap with fewer free slots than this
    clear_hop1: bool = False       # sync: send the end ion out rather than gate-swap past it
    collateral: float = 1.0        # look-ahead weight of ions a plan displaces
    keep: int = 0                  # keep this many slots free proactively (0: react only)
    beta: float = 0.0              # pull toward the windowed target partition (us per us of distance)
    win: int = 0                   # window length in gates (0: number of qubits)
    win_ahead: int = 3             # windows of gates each target looks at
    win_decay: float = 0.5         # weight decay per window ahead
    lam: float = 1.0               # cost of moving a qubit between consecutive targets
    target_slack: int = 2          # targets fill traps to capacity - slack
    relief_depth: int = 3          # how far a full neighbour may pass an ion on
    order: str = "est"             # gate pick: "est" (earliest start), "prog", "mix", "crit", "travel" (sweep)
    crit_w: float = 0.0            # in "est" mode: subtract crit_w * tail(gates) us
    prog_w: float = 0.0            # in "mix" mode: add prog_w * program index us
    noise: float = 0.0             # random tie-breaking in plan scores (us)
    seed: int = 0


# ------------------------------------------------------------------------- mapping


def _interaction(circ: Sequence[tuple[str, str]], tau: float) -> dict[tuple[str, str], float]:
    n = max(1, len(circ))
    W: dict[tuple[str, str], float] = defaultdict(float)
    for i, (a, b) in enumerate(circ):
        w = math.exp(-i / (tau * n)) if tau > 0 else 1.0
        k = (a, b) if a < b else (b, a)
        W[k] += w
    return W


def _ranks(circ, qubits) -> dict[str, int] | None:
    """If every qubit meets its partners in one global order (QFT, quadratic forms: each
    qubit's partner sequence is monotone in the qubit index, all in the same direction),
    return each qubit's place in that order; else None."""
    seq: dict[str, list] = defaultdict(list)
    key = lambda q: _natkey(q)
    for a, b in circ:
        seq[a].append(key(b))
        seq[b].append(key(a))
    inc = dec = True
    for s in seq.values():
        for x, y in zip(s, s[1:]):
            if x > y:
                inc = False
            if x < y:
                dec = False
    if not (inc or dec):
        return None
    order = sorted(qubits, key=key, reverse=not inc)
    return {q: i for i, q in enumerate(order)}


def _initial_mapping(circ, qubits, dev: _Dev, kn: Knobs, rng: random.Random) -> dict[str, list[str]]:
    line = list(dev.line)
    if kn.reverse_line:
        line.reverse()
    n = len(qubits)
    total_fill = sum(dev.fill[t] for t in line)
    if n > total_fill:
        raise SchedError(f"{n} qubits do not fit the fill limit ({total_fill})")
    k_min = 1
    acc = 0
    for i, t in enumerate(line):
        acc += dev.fill[t]
        if acc >= n:
            k_min = i + 1
            break
    k = max(k_min, min(len(line), kn.traps_used or k_min))
    used = line[:k]
    # balanced sizes
    sizes = {t: 0 for t in used}
    left = n
    for i, t in enumerate(used):
        share = math.ceil(left / (k - i))
        sizes[t] = min(dev.fill[t], share)
        left -= sizes[t]
    if left > 0:
        for t in used:
            add = min(left, dev.fill[t] - sizes[t])
            sizes[t] += add
            left -= add
    if kn.mapping == "rank" and kn.rank_sizes == "load":
        rk = _ranks(circ, qubits)
        if rk is not None:
            deg = defaultdict(int)          # gates each qubit waits for (partners of lower rank)
            for a, b in circ:
                deg[a if rk[a] > rk[b] else b] += 1
            byr = sorted(qubits, key=lambda q: rk[q])
            total = sum(deg.values())
            sizes = {tt: 0 for tt in used}
            idx = 0
            for j, tt in enumerate(used):
                rest = len(used) - j - 1
                goal = (total - sum(deg[q] for q in byr[:idx])) / (rest + 1)
                load = 0
                while idx < n and sizes[tt] < dev.fill[tt]:
                    if load + deg[byr[idx]] / 2 > goal and sizes[tt] > 0 and (n - idx) <= sum(
                            dev.fill[u] for u in used[j + 1:]):
                        break
                    load += deg[byr[idx]]
                    sizes[tt] += 1
                    idx += 1
            if idx < n:
                raise SchedError("rank layout does not fit the fill limit")
    # first-use order
    first: dict[str, int] = {}
    for i, (a, b) in enumerate(circ):
        first.setdefault(a, i)
        first.setdefault(b, i)
    order = sorted(qubits, key=lambda q: (first.get(q, 10 ** 9), _natkey(q)))
    rank = _ranks(circ, qubits) if kn.mapping == "rank" else None
    if kn.mapping == "rank" and rank is None:
        raise SchedError("mapping 'rank' needs a circuit whose qubits meet partners in one order")
    if rank is not None:
        order = sorted(qubits, key=lambda q: rank[q])
    assign: dict[str, str] = {}
    W = _interaction(circ, kn.tau)
    if kn.mapping == "part":
        order = _spectral_order(qubits, W, order)
    it = iter(order)
    for t in used:
        for _ in range(sizes[t]):
            assign[next(it)] = t
    if kn.mapping == "part":
        assign = _kl(assign, W, dev, _balanced_fill(dev, n, k, kn, used), kn.kl_rounds, rng)
    if rank is not None:
        out = {t: [] for t in dev.traps}
        for q, t in assign.items():
            out[t].append(q)
        for t in out:
            # the next to travel (lowest rank) at the end it leaves through
            side = dev.via_side.get((t, line[-1])) if t != line[-1] else None
            desc = (side != "L") if kn.rank_chain == "exit" else (side == "L")
            out[t].sort(key=lambda q: rank[q], reverse=desc)
        return out
    if kn.chain_order in ("asc", "desc"):
        first = {}
        for i, (a, b) in enumerate(circ):
            first.setdefault(a, i)
            first.setdefault(b, i)
        out = {t: [] for t in dev.traps}
        for q, t in assign.items():
            out[t].append(q)
        for t in out:
            out[t].sort(key=lambda q: (first.get(q, 10 ** 9), _natkey(q)), reverse=kn.chain_order == "desc")
        return out
    return _order_chains(assign, circ, dev)


def _balanced_fill(dev: _Dev, n: int, k: int, kn, used) -> dict[str, int]:
    """Per-trap limit for the partitioner: the fill limit, or -- with ``balance`` -- the
    even share of the traps in use plus ``bal_slack`` (traps not in use get nothing)."""
    if kn.balance is None:
        return {t: dev.fill[t] for t in dev.traps}
    share = math.ceil(n / k) + kn.balance
    return {t: (min(dev.fill[t], share) if t in used else 0) for t in dev.traps}


def _spectral_order(qubits, W, fallback):
    try:
        import numpy as np
    except ImportError:          # pragma: no cover
        return fallback
    idx = {q: i for i, q in enumerate(qubits)}
    n = len(qubits)
    if n < 3:
        return fallback
    L = np.zeros((n, n))
    for (a, b), w in W.items():
        i, j = idx[a], idx[b]
        L[i, j] -= w
        L[j, i] -= w
        L[i, i] += w
        L[j, j] += w
    L += 1e-9 * np.eye(n)
    vals, vecs = np.linalg.eigh(L)
    f = vecs[:, 1]
    rank = {q: (f[idx[q]], k) for k, q in enumerate(fallback)}
    out = sorted(qubits, key=lambda q: rank[q])
    # orient so the first-used qubit comes first
    if out.index(fallback[0]) > n // 2:
        out.reverse()
    return out


def _kl(assign, W, dev: _Dev, fill, rounds, rng, anchor=None, lam: float = 0.0):
    nb: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for (a, b), w in W.items():
        nb[a].append((b, w))
        nb[b].append((a, w))
    D = dev.cost
    count = defaultdict(int)
    for q, t in assign.items():
        count[t] += 1

    def gain_move(q, t):
        cur = assign[q]
        g = 0.0
        for p, w in nb[q]:
            tp = assign[p]
            g += w * (D[(cur, tp)] - D[(t, tp)])
        if anchor is not None and lam:
            h = anchor[q]
            g += lam * (D[(cur, h)] - D[(t, h)])
        return g

    qs = list(assign)
    traps = list(dev.traps)
    for _ in range(rounds):
        improved = False
        rng.shuffle(qs)
        for q in qs:
            cur = assign[q]
            best, bt = 1e-9, None
            for t in traps:
                if t == cur or count[t] >= fill[t]:
                    continue
                g = gain_move(q, t)
                if g > best:
                    best, bt = g, t
            if bt is not None:
                count[cur] -= 1
                count[bt] += 1
                assign[q] = bt
                improved = True
        # pairwise swaps
        for q in qs:
            cur = assign[q]
            tgt = {assign[p] for p, _ in nb[q]} - {cur}
            if anchor is not None and lam and anchor[q] != cur:
                tgt.add(anchor[q])
            best, bp = 1e-9, None
            for r in qs:
                tp = assign[r]
                if tp not in tgt:
                    continue
                g = gain_move(q, tp) + gain_move(r, cur)
                wqr = W.get((q, r) if q < r else (r, q), 0.0)
                g -= 2 * wqr * D[(cur, tp)]
                if g > best:
                    best, bp = g, r
            if bp is not None:
                tp = assign[bp]
                assign[q], assign[bp] = tp, cur
                improved = True
        if not improved:
            break
    return assign


def _targets(circ, chains0, dev: _Dev, kn, rng) -> tuple[int, list[dict[str, str]]]:
    """Where each qubit should live, window by window: Kernighan-Lin over the next windows'
    gates (decaying), anchored to the previous window's assignment by the cost of moving."""
    A = {q: t for t, c in chains0.items() for q in c}
    n = len(A)
    W = kn.win or max(8, n)
    fill = {t: max(1, dev.cap[t] - kn.target_slack) for t in dev.traps}
    if kn.balance is not None:
        k = max(1, min(len(dev.traps), kn.traps_used or len(dev.traps)))
        share = math.ceil(n / k) + kn.balance
        fill = {t: min(f, share) for t, f in fill.items()}
    out = []
    ng = len(circ)
    for lo in range(0, max(ng, 1), W):
        hi = min(ng, lo + kn.win_ahead * W)
        Wg: dict[tuple[str, str], float] = defaultdict(float)
        for i in range(lo, hi):
            a, b = circ[i]
            k = (a, b) if a < b else (b, a)
            Wg[k] += kn.win_decay ** ((i - lo) / W)
        prev = dict(A)
        A = _kl(dict(A), Wg, dev, fill, kn.kl_rounds, rng, anchor=prev, lam=kn.lam)
        out.append(A)
    return W, out


def _order_chains(assign: dict[str, str], circ, dev: _Dev) -> dict[str, list[str]]:
    """Ions that will leave through a port first sit at that port's end."""
    traps = {t: [] for t in dev.traps}
    for q, t in assign.items():
        traps[t].append(q)
    n = max(1, len(circ))
    pull: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    first_out: dict[str, float] = {}
    for i, (a, b) in enumerate(circ):
        ta, tb = assign.get(a), assign.get(b)
        if ta is None or tb is None or ta == tb:
            continue
        w = 1.0 / (1.0 + i / n * 20)
        for q, tq, to in ((a, ta, tb), (b, tb, ta)):
            side = dev.via_side.get((tq, to))
            if side:
                pull[q][side] += w
                first_out.setdefault(q, i)
    chains = {}
    for t, qs in traps.items():
        sides = set(dev.ports[t].values())
        if sides == {"L", "R"}:
            def key(q):
                d = pull[q]["R"] - pull[q]["L"]
                f = first_out.get(q, 10 ** 9)
                if d < 0:
                    return (-1, f)
                if d > 0:
                    return (1, -f)
                return (0, 0)
            chains[t] = sorted(qs, key=key)
        elif sides:
            side = next(iter(sides))
            # the first to leave sits at the port end
            byfirst = sorted(qs, key=lambda q: first_out.get(q, 10 ** 9))
            chains[t] = byfirst if side == "L" else byfirst[::-1]
        else:
            chains[t] = sorted(qs, key=_natkey)
    return chains


# ------------------------------------------------------------------------- the policy


class _Run:
    def __init__(self, circ, dev: _Dev, chains0, kn: Knobs):
        self.circ = [(str(a), str(b)) for a, b in circ]
        self.dev = dev
        self.kn = kn
        self.rng = random.Random(kn.seed)
        self.st = _State(dev, chains0)
        self.chains0 = {t: list(c) for t, c in chains0.items()}
        self.loc0 = {q: t for t, c in chains0.items() for q in c}
        n = len(self.circ)
        self.per_q: dict[str, list[int]] = defaultdict(list)
        for i, (a, b) in enumerate(self.circ):
            self.per_q[a].append(i)
            self.per_q[b].append(i)
        self.pos_in_q: dict[tuple[str, int], int] = {}
        for q, lst in self.per_q.items():
            for k, i in enumerate(lst):
                self.pos_in_q[(q, i)] = k
        # critical path tail (in gates)
        tail = [1] * n
        for i in range(n - 1, -1, -1):
            best = 0
            for q in self.circ[i]:
                k = self.pos_in_q[(q, i)]
                lst = self.per_q[q]
                if k + 1 < len(lst):
                    best = max(best, tail[lst[k + 1]])
            tail[i] = 1 + best
        self.tail = tail
        self.head = {q: 0 for q in self.per_q}
        self.why_ctx = "gate"
        self.sync = kn.sync
        self.fallbacks = 0
        self.rank = _ranks(self.circ, list(self.loc0)) if kn.policy == "sweep" else None
        self.loose = False
        self.W, self.tg = (_targets(self.circ, chains0, dev, kn, random.Random(kn.seed + 1))
                           if kn.beta else (1, []))

    # -- look-ahead -----------------------------------------------------------------
    def next_partners(self, q: str, after_i: int, k: int) -> list[str]:
        lst = self.per_q[q]
        j = self.pos_in_q[(q, after_i)] + 1
        out = []
        for i in lst[j:j + k]:
            a, b = self.circ[i]
            out.append(b if a == q else a)
        return out

    def future(self, q: str, at: str, i: int, override: Mapping[str, str]) -> float:
        kn = self.kn
        c, w = 0.0, 1.0
        loc = self.st.loc
        D = self.dev.cost
        for p in self.next_partners(q, i, kn.lookahead):
            tp = override.get(p) or loc[p]
            c += w * D[(at, tp)]
            w *= kn.decay
        return c

    # -- eviction ---------------------------------------------------------------------
    def _out_candidates(self, trap: str, sides: set[str] | None, avoid, no_dst=()) -> list[tuple]:
        """(cost, ion, leg) for every end ion that can leave ``trap`` consistently."""
        st = self.st
        out = []
        for side_seg, side in self.dev.ports[trap].items():
            if sides is not None and side not in sides:
                continue
            if not st.cp[trap]:
                continue
            end = 0 if side == "L" else -1
            for v in sorted({st.cp[trap][end], st.cq[trap][end]}):
                if v in avoid:
                    continue
                ann = st.split_annot(trap, v, side)
                if ann is False:
                    continue
                i_next = self._next_gate_of(v)
                for nb in self.dev.nbrs[trap]:
                    leg = self.dev.legs[(trap, nb)]
                    if leg.side_out != side or nb in no_dst:
                        continue
                    pull = 0.0 if i_next is None else (
                        self.future_from(v, trap, i_next) - self.future_from(v, nb, i_next))
                    cost = (3 * st.g[trap] if ann else 0) - pull - 20.0 * st.room(nb)
                    out.append((cost, v, leg))
        out.sort(key=lambda x: (x[0], x[1]))
        return out

    def _send_out(self, trap: str, sides, avoid, t_min: float, depth: int, no_dst=()) -> bool:
        st = self.st
        old_why = st.why
        st.why = self.why_ctx
        try:
            return self._send_out1(trap, sides, avoid, t_min, depth, no_dst)
        finally:
            st.why = old_why

    def _send_out1(self, trap: str, sides, avoid, t_min: float, depth: int, no_dst=()) -> bool:
        st = self.st
        cands = self._out_candidates(trap, sides, avoid, no_dst)
        for cost, v, leg in cands:
            if st.room(leg.dst) >= 1:
                if st.leg(v, leg, t_min, jit=self.kn.jit) is not None:
                    return True
        if depth >= self.kn.relief_depth:
            return False
        for cost, v, leg in cands:           # the neighbour is full too: relieve it first
            m = st.mark()
            if (self._send_out(leg.dst, None, set(avoid) | {v}, t_min, depth + 1,
                               tuple(no_dst) + (trap,)) and st.room(leg.dst) >= 1
                    and st.split_annot(trap, v, leg.side_out) is not False):
                if st.leg(v, leg, t_min, jit=self.kn.jit) is not None:
                    return True
            st.rollback(m)
        return False

    def evict(self, trap: str, avoid, t_min: float, no_dst=()) -> bool:
        self.why_ctx = "evict"
        return self._send_out(trap, None, avoid, t_min, 0, no_dst)

    def unblock(self, trap: str, ion: str, side: str, avoid, t_min: float) -> bool:
        """The books disagree on which ion ends ``side``: move those end ions out until
        ``ion`` can leave with one annotation that satisfies both."""
        st = self.st
        avoid = set(avoid) | {ion}
        self.why_ctx = "unblock"
        for _ in range(self.dev.cap[trap] + 1):
            if st.split_annot(trap, ion, side) is not False:
                return True
            best = None
            for cost, v, leg in self._out_candidates(trap, {side}, avoid):
                m = st.mark()
                ok = st.room(leg.dst) >= 1 or self._send_out(leg.dst, None, avoid | {v}, t_min, 1, (trap,))
                if ok and st.split_annot(trap, v, side) is not False:
                    ok = st.leg(v, leg, t_min, jit=self.kn.jit) is not None
                else:
                    ok = False
                if ok:
                    P, Q = st.cp[trap], st.cq[trap]
                    key = (st.split_annot(trap, ion, side) is False,
                           sum(1 for x, y in zip(P, Q) if x != y), cost)
                    if best is None or key < best[0]:
                        best = (key, v, leg)
                st.rollback(m)
            if best is None:
                return False
            _, v, leg = best
            if not (st.room(leg.dst) >= 1 or self._send_out(leg.dst, None, avoid | {v}, t_min, 1, (trap,))):
                return False
            if st.leg(v, leg, t_min, jit=self.kn.jit) is None:
                return False
        return st.split_annot(trap, ion, side) is not False

    def go(self, ion: str, dst: str, avoid) -> float | None:
        """Walk ``ion`` to ``dst`` leg by leg.  In sync mode the two books never part: an
        ion buried in its chain first has the ions in its way sent out, and a gate swap
        across the whole chain is followed at once by the displaced end ion leaving through
        the other side.  Otherwise divergence is allowed and repaired when it blocks."""
        st, dev, kn = self.st, self.dev, self.kn
        src = st.loc[ion]
        if src == dst:
            return st.ion_t[ion]
        path = dev.route.get((src, dst))
        if path is None:
            return None
        avoid = frozenset(avoid) | {ion}
        end = None
        for x, y in zip(path, path[1:]):
            leg = dev.legs[(x, y)]
            side = leg.side_out
            for _ in range(4):
                kind, ann = st.exit_kind(x, ion, side)
                bad = kind == "conflict" or (self.sync and kind == "interior") or (
                    self.sync and kind == "hop1" and kn.clear_hop1)
                if bad:
                    ok = self.clear(x, ion, side, avoid) if self.sync else self.unblock(x, ion, side, avoid, 0.0)
                    if not ok:
                        return None
                    continue
                if st.room(y) < 1:
                    if not self.evict(y, avoid, 0.0, (x,)):
                        return None
                    continue
                break
            else:
                return None
            kind, ann = st.exit_kind(x, ion, side)
            if kind == "conflict" or (self.sync and kind == "interior") or st.room(y) < 1:
                return None
            end = st.leg(ion, leg, 0.0, jit=kn.jit)
            if end is None:
                return None
            if kind == "cross" and self.sync:
                opp = "R" if side == "L" else "L"
                e = ann["with"]
                if not self.push_out(x, e, opp, avoid, why="heal"):
                    return None
                # the displaced ion keeps its place in this trap, now at the other end,
                # unless it has nothing left to do here
                if (kn.heal_return or kn.policy == "sweep") and self._next_gate_of(e) is not None and st.loc[e] != x:
                    back = dev.legs.get((st.loc[e], x))
                    if back is not None and st.room(x) >= 1:
                        old = st.why
                        st.why = "heal"
                        st.leg(e, back, 0.0, jit=kn.jit)
                        st.why = old
        return end

    def clear(self, trap: str, ion: str, side: str, avoid) -> bool:
        """Send out the ions between ``ion`` and the ``side`` end, end first."""
        st = self.st
        for _ in range(self.dev.cap[trap]):
            kind, ann = st.exit_kind(trap, ion, side)
            if kind in ("plain", "cross") or (kind == "hop1" and not self.kn.clear_hop1):
                return True
            if kind not in ("interior", "hop1"):
                return False
            e = st.cp[trap][0 if side == "L" else -1]
            if e in avoid or not self.push_out(trap, e, side, avoid, why="clear"):
                return False
        return False

    def push_out(self, trap: str, v: str, side: str, avoid, why: str = "push") -> bool:
        """Send ``v`` (at the ``side`` end, or consistently annotated) out through ``side``."""
        st = self.st
        old = st.why
        st.why = why
        try:
            i_next = self._next_gate_of(v)
            cands = []
            for nb in self.dev.nbrs[trap]:
                leg = self.dev.legs[(trap, nb)]
                if leg.side_out != side:
                    continue
                pull = 0.0 if i_next is None else self.future_from(v, nb, i_next)
                cands.append((pull - 20.0 * st.room(nb), nb, leg))
            cands.sort(key=lambda c: (c[0], _natkey(c[1])))
            if st.split_annot(trap, v, side) is False:
                return False
            for _, nb, leg in cands:
                if st.room(nb) >= 1 and st.leg(v, leg, 0.0, jit=self.kn.jit) is not None:
                    return True
            for _, nb, leg in cands:
                m = st.mark()
                self.why_ctx = why
                if (self._send_out(nb, None, set(avoid) | {v}, 0.0, 1, (trap,)) and st.room(nb) >= 1
                        and st.split_annot(trap, v, side) is not False
                        and st.leg(v, leg, 0.0, jit=self.kn.jit) is not None):
                    return True
                st.rollback(m)
            return False
        finally:
            st.why = old

    def blocked(self, i: int) -> bool:
        """Would gate i have to evict someone to run now?"""
        a, b = self.circ[i]
        st = self.st
        if st.loc[a] == st.loc[b]:
            return False
        if self.rank is not None and self.kn.policy == "sweep":
            hi = a if self.rank[a] > self.rank[b] else b
            lo = b if hi == a else a
            dest = st.loc[hi]
            path = self.dev.route.get((st.loc[lo], dest), [])
            return any(st.room(x) < 1 for x in path[1:])
        return st.room(st.loc[a]) < 1 and st.room(st.loc[b]) < 1

    def park(self, i: int) -> None:
        """An ion with no gates left leaves a crowded trap toward the start of the line,
        where it is out of the way and serves as the end ion a later crossing displaces."""
        st, dev = self.st, self.dev
        for q in self.circ[i]:
            if self._next_gate_of(q) is not None:
                continue
            trap = st.loc[q]
            if st.room(trap) >= self.kn.park:
                continue
            first = dev.line[0]
            if trap == first:
                continue
            side = dev.via_side.get((trap, first))
            if side is None:
                continue
            kind, _ = st.exit_kind(trap, q, side)
            if kind != "plain":
                continue
            m = st.mark()
            if not self.push_out(trap, q, side, set(), why="park"):
                st.rollback(m)

    def target(self, q: str, i: int | None) -> str | None:
        if not self.tg or i is None:
            return None
        return self.tg[min(len(self.tg) - 1, i // self.W)].get(q)

    def disagree(self, trap: str) -> int:
        P, Q = self.st.cp[trap], self.st.cq[trap]
        return sum(1 for x, y in zip(P, Q) if x != y)

    def heal(self, trap: str, avoid, t_min: float) -> bool:
        """Move displaced ions out of ``trap`` until both books order it the same."""
        st = self.st
        avoid = set(avoid)
        self.why_ctx = "heal"
        for _ in range(self.dev.cap[trap] + 1):
            if st.cp[trap] == st.cq[trap]:
                return True
            best = None
            for cost, v, leg in self._out_candidates(trap, None, avoid):
                m = st.mark()
                ok = st.room(leg.dst) >= 1 or self._send_out(leg.dst, None, avoid | {v}, t_min, 1, (trap,))
                ok = ok and st.split_annot(trap, v, leg.side_out) is not False
                ok = ok and st.leg(v, leg, t_min, jit=self.kn.jit) is not None
                if ok:
                    key = (self.disagree(trap), cost)
                    if best is None or key < best[0]:
                        best = (key, v, leg)
                st.rollback(m)
            if best is None:
                return False
            _, v, leg = best
            if not (st.room(leg.dst) >= 1 or self._send_out(leg.dst, None, avoid | {v}, t_min, 1, (trap,))):
                return False
            if st.leg(v, leg, t_min, jit=self.kn.jit) is None:
                return False
        return st.cp[trap] == st.cq[trap]

    def maintain(self) -> None:
        """Keep ``keep`` free slots in every trap, so arrivals rarely wait for an eviction."""
        k = self.kn.keep
        if k <= 0:
            return
        st = self.st
        self.why_ctx = "keep"
        for t in self.dev.traps:
            for _ in range(k):
                if st.room(t) >= k:
                    break
                m = st.mark()
                if not self._send_out(t, None, set(), 0.0, 1):
                    st.rollback(m)
                    break

    def _next_gate_of(self, q: str) -> int | None:
        lst = self.per_q.get(q, [])
        k = self.head.get(q, len(lst))
        return lst[k] if k < len(lst) else None

    def future_from(self, q: str, at: str, i_next: int) -> float:
        """Look-ahead cost of q standing at ``at`` for its gates from i_next on."""
        lst = self.per_q[q]
        j = self.pos_in_q[(q, i_next)]
        c, w = 0.0, 1.0
        D = self.dev.cost
        for i in lst[j:j + self.kn.lookahead]:
            a, b = self.circ[i]
            p = b if a == q else a
            c += w * D[(at, self.st.loc[p])]
            w *= self.kn.decay
        return c

    # -- one gate -----------------------------------------------------------------------
    def plans(self, i: int) -> list[tuple]:
        a, b = self.circ[i]
        st, dev = self.st, self.dev
        A, Bt = st.loc[a], st.loc[b]
        if self.kn.policy == "sweep" and self.rank is not None and not self.loose:
            hi = a if self.rank[a] > self.rank[b] else b
            return [(st.loc[hi],)]
        dests = [Bt, A]
        if self.kn.meet:
            for t in dev.traps:
                if t not in dests:
                    dests.append(t)
        return [(d,) for d in dests]

    def try_plan(self, i: int, dest: str, commit: bool):
        a, b = self.circ[i]
        st = self.st
        m = st.mark()
        avoid = frozenset((a, b))
        t_min = 0.0
        # the ion that is ready first moves first
        self.orig = {a: st.loc[a], b: st.loc[b]}
        movers = [q for q in (a, b) if st.loc[q] != dest]
        movers.sort(key=lambda q: st.ion_t[q])
        ev0 = len(st.ev)
        div0 = self.div0
        ok = True
        for q in movers:
            if self.go(q, dest, avoid) is None:
                ok = False
                break
        if not ok:
            st.rollback(m)
            return None
        end = st.gate(a, b)
        # score
        kn = self.kn
        swaps = 0
        moved = {}
        for r in st.ev[ev0:]:
            if r[0] == "split":
                if r[9]:
                    swaps += 1
                v = r[3][0]
                if v not in avoid and v not in moved:
                    moved[v] = r[4]
        score = end + kn.div_pen * max(0, st.divergent() - div0)
        if kn.move_w:
            score += kn.move_w * sum(1 for r in st.ev[ev0:] if r[0] == "move")
        if kn.beta:
            D = self.dev.cost
            for q in (a, b):
                tq = self.target(q, i)
                if tq is not None:
                    score += kn.beta * (D[(dest, tq)] - D[(self.orig[q], tq)])
            for v, was in moved.items():
                tq = self.target(v, self._next_gate_of(v))
                if tq is not None:
                    score += kn.beta * (D[(st.loc[v], tq)] - D[(was, tq)])
        if kn.alpha:
            ov = {a: dest, b: dest}
            score += kn.alpha * (self.future(a, dest, i, ov) + self.future(b, dest, i, ov))
            if kn.collateral:
                for v, was in moved.items():
                    i_next = self._next_gate_of(v)
                    if i_next is not None and st.loc[v] != was:
                        score += kn.alpha * kn.collateral * (
                            self.future_from(v, st.loc[v], i_next) - self.future_from(v, was, i_next))
        score += kn.swap_pen * swaps
        if kn.noise:
            score += self.rng.random() * kn.noise
        if not commit:
            st.rollback(m)
        return score, end

    def step(self, i: int) -> None:
        a, b = self.circ[i]
        st = self.st
        if st.loc[a] == st.loc[b]:
            st.gate(a, b)
            return
        best = None
        self.div0 = st.divergent()
        for (dest,) in self.plans(i):
            r = self.try_plan(i, dest, commit=False)
            if r is None:
                continue
            if best is None or r[0] < best[0]:
                best = (r[0], dest)
        if best is None and self.kn.policy == "sweep" and not self.loose:
            self.loose = True
            try:
                for (dest,) in self.plans(i):
                    r = self.try_plan(i, dest, commit=False)
                    if r is not None and (best is None or r[0] < best[0]):
                        best = (r[0], dest)
                if best is not None:
                    self.try_plan(i, best[1], commit=True)
                    return
            finally:
                self.loose = False
        if best is None and self.sync and self.kn.fallback:
            # dead end in sync mode: allow the books to part for this one gate
            self.sync = False
            try:
                for (dest,) in self.plans(i):
                    r = self.try_plan(i, dest, commit=False)
                    if r is not None and (best is None or r[0] < best[0]):
                        best = (r[0], dest)
                if best is not None:
                    self.fallbacks += 1
                    self.try_plan(i, best[1], commit=True)
                    return
            finally:
                self.sync = self.kn.sync
        if best is None:
            # dead end: make both books agree in the two traps, then try again
            for trap in sorted({st.loc[a], st.loc[b]}):
                self.heal(trap, {a, b}, 0.0)
            self.div0 = st.divergent()
            for (dest,) in self.plans(i):
                r = self.try_plan(i, dest, commit=False)
                if r is not None and (best is None or r[0] < best[0]):
                    best = (r[0], dest)
        if best is None:
            raise SchedError(f"gate {i} {self.circ[i]}: no legal plan "
                             f"({st.loc[a]} {st.cp[st.loc[a]]} / {st.loc[b]} {st.cp[st.loc[b]]})")
        self.try_plan(i, best[1], commit=True)

    def est(self, i: int) -> float:
        a, b = self.circ[i]
        st = self.st
        t = max(st.ion_t[a], st.ion_t[b])
        if st.loc[a] != st.loc[b]:
            t += 0.5 * self.dev.cost[(st.loc[a], st.loc[b])]
        else:
            t = st.gate_time(a, b)
        return t - self.kn.crit_w * self.tail[i]

    def run(self) -> _State:
        circ = self.circ
        head = self.head
        ready = set()
        for q, lst in self.per_q.items():
            i = lst[0]
            a, b = circ[i]
            if self.per_q[a][head[a]] == i and self.per_q[b][head[b]] == i:
                ready.add(i)
        done = 0
        while ready:
            if self.kn.order == "crit":
                i = min(ready, key=lambda j: (-self.tail[j], self.est(j), j))
            elif self.kn.order == "prog":
                i = min(ready)
            elif self.kn.order == "travel" and self.rank is not None:
                rk = self.rank
                i = min(ready, key=lambda j: (min(rk[q] for q in circ[j]), max(rk[q] for q in circ[j]), j))
            elif self.kn.order == "mix":
                i = min(ready, key=lambda j: (self.est(j) + self.kn.prog_w * j, j))
            else:
                i = min(ready, key=lambda j: (self.est(j), -self.tail[j], j))
            if self.kn.defer and self.blocked(i):
                alt = sorted(ready, key=lambda j: (j != i, self.est(j), j))[: self.kn.defer + 1]
                for j in alt:
                    if not self.blocked(j):
                        i = j
                        break
            ready.discard(i)
            self.cur_i = i
            self.step(i)
            st = self.st
            st.U.clear()           # committed: nothing to undo past here
            self.maintain()
            st.U.clear()
            done += 1
            for q in circ[i]:
                head[q] += 1
            if self.kn.park:
                self.park(i)
                st.U.clear()
            for q in circ[i]:
                lst = self.per_q[q]
                if head[q] < len(lst):
                    j = lst[head[q]]
                    a, b = circ[j]
                    if self.per_q[a][head[a]] == j and self.per_q[b][head[b]] == j:
                        ready.add(j)
        if done != len(circ):
            raise SchedError(f"scheduled {done} of {len(circ)} gates")
        return self.st


# ----------------------------------------------------------------------------- API


def _law_params(law) -> QCCDSimParams:
    if isinstance(law, QCCDSimParams):
        return law
    raise TypeError("law must be a QCCDSimParams (the QCCDSim family's duration law)")


def build(circuit: Sequence[tuple[str, str]], device: PortGraph, *, law: QCCDSimParams,
          knobs: Knobs | None = None, qubits: Sequence[str] | None = None,
          chains: Mapping[str, list[str]] | None = None, name: str = "sched") -> TimedSchedule:
    """One deterministic construction with fixed knobs (unchecked; see `schedule`)."""
    kn = knobs or Knobs()
    p = _law_params(law)
    if p.gate != "FM":
        raise NotImplementedError("only the FM gate law (time keyed to trap capacity) is supported")
    if p.swap != "GateSwap":
        raise NotImplementedError("only gate-swap reorders are supported")
    dev = _Dev(device, p)
    circ = [(str(a), str(b)) for a, b in circuit]
    qs = list(dict.fromkeys([str(q) for q in (qubits or [])] + [q for g in circ for q in g]))
    rng = random.Random(kn.seed)
    if chains is None:
        chains = _initial_mapping(circ, qs, dev, kn, rng)
    run = _Run(circ, dev, chains, kn)
    st = run.run()
    return TimedSchedule(name=name, device=device,
                         chains={t: list(c) for t, c in run.chains0.items()},
                         events=st.events(), qubits={q: q for q in qs},
                         source={"tool": "qccd.repro.sched", "knobs": kn.__dict__.copy()})


def verify(s: TimedSchedule, circuit, law: QCCDSimParams) -> dict:
    """Replay under both QCCDSim bookkeepings with the paper's law and the circuit."""
    out = {}
    for prof in (QCCDSIM, QCCDSIM_PHYSICAL):
        r = check(s, prof, duration=qccdsim_duration(law), circuit=list(circuit))
        out[prof.name] = r
    return out


def _metric(rep, objective: str) -> float:
    if objective == "shuttles":
        return rep.metrics["counts"].get("move", 0) * 1e9 + rep.metrics["makespan_us"]
    return rep.metrics["makespan_us"]


def schedule(circuit: list[tuple[str, str]], device: PortGraph, *, law: QCCDSimParams,
             profile=None, fill: int | None = None, qubits: list[str] | None = None,
             objective: str = "makespan", seed: int = 0, time_budget_s: float = 60,
             knobs: Sequence[Knobs] | None = None, log: Callable[[str], None] | None = None,
             **kw) -> TimedSchedule:
    """Search knob settings within the time budget; return the best schedule that the checker
    accepts under both QCCDSim bookkeepings (and ``profile`` if given)."""
    dev = device
    if fill is not None:
        dev = PortGraph(sites={k: {**v, "fill": min(int(fill), int(v["capacity"]))}
                               for k, v in device.sites.items()},
                        nodes=device.nodes, segments=device.segments, adjacent=device.adjacent)
    rng = random.Random(seed)
    cands = list(knobs) if knobs else [Knobs(**{**d, **kw}) for d in candidates(rng, 400, circuit, objective)]
    t0 = time.time()
    best = None
    tried = 0
    for kn in cands:
        if tried and time.time() - t0 > time_budget_s:
            break
        tried += 1
        try:
            s = build(circuit, dev, law=law, knobs=kn, qubits=qubits)
        except SchedError as ex:
            if log:
                log(f"knobs {kn}: {ex}")
            continue
        kinds = {e.kind for e in s.events}
        if not kinds <= set(ALLOWED_KINDS):
            raise AssertionError(f"builder emitted {kinds - set(ALLOWED_KINDS)}")
        reps = verify(s, circuit, law)
        if profile is not None and profile not in (QCCDSIM, QCCDSIM_PHYSICAL):
            reps[profile.name] = check(s, profile, duration=qccdsim_duration(law), circuit=circuit)
        if not all(r.ok for r in reps.values()):
            bad = {k: r.summary()["violations"] for k, r in reps.items() if not r.ok}
            if log:
                log(f"REJECTED (checker): {bad} knobs={kn}")
            continue
        r = reps[QCCDSIM_PHYSICAL.name]
        val = _metric(r, objective)
        if log:
            log(f"ok makespan={r.metrics['makespan_us']:.0f} moves={r.metrics['counts'].get('move', 0)} knobs={kn}")
        if best is None or val < best[0]:
            best = (val, s, kn)
    if best is None:
        raise SchedError("no legal schedule found")
    s = best[1]
    s.source["search"] = {"tried": tried, "seconds": round(time.time() - t0, 2)}
    return s


def candidates(rng: random.Random, n: int, circuit=None, objective: str = "makespan") -> list[dict]:
    """Knob settings to search, best-known first, then random draws.  For a circuit whose
    qubits meet their partners in one global order the sweep comes first; for the shuttle
    count, plans are also charged per junction crossing."""
    base = []
    if objective == "shuttles":
        base += [dict(mapping="part", tau=0, move_w=1e5, alpha=0.5, sync=False),
                 dict(mapping="part", tau=0, move_w=1e5, alpha=0.5),
                 dict(mapping="part", tau=0.25, move_w=1e5, alpha=1, beta=1),
                 dict(mapping="part", tau=0, move_w=2000, alpha=0.5, traps_used=6, balance=1)]
    if circuit is not None:
        circ = [(str(a), str(b)) for a, b in circuit]
        qs = list(dict.fromkeys(q for g in circ for q in g))
        if _ranks(circ, qs) is not None:
            base += [dict(mapping="rank", policy="sweep", order="travel", traps_used=6),
                     dict(mapping="rank", policy="sweep", order="travel", traps_used=6, park=99),
                     dict(mapping="rank", policy="sweep", order="travel", traps_used=6, rank_sizes="load", park=2)]
    base += [
        dict(mapping="part", traps_used=6, balance=0, beta=2, alpha=0.5, order="prog"),
        dict(mapping="part", traps_used=6, balance=0, beta=2, alpha=0.5, order="est"),
        dict(mapping="po", alpha=2, decay=0.9),
        dict(mapping="po", alpha=1, decay=0.9, lookahead=16),
        dict(mapping="part", traps_used=6, balance=1, beta=1, alpha=1, order="prog"),
        dict(mapping="part", alpha=1),
        dict(mapping="po", traps_used=6, balance=0, beta=2, alpha=0.5, order="prog"),
    ]
    out = [dict(d) for d in base]
    while len(out) < n:
        out.append(dict(
            mapping=rng.choice(["part", "part", "po"]),
            traps_used=rng.choice([None, 6, 6]),
            balance=rng.choice([None, 0, 0, 1, 2]),
            beta=rng.choice([0, 0.5, 1, 2, 4]),
            alpha=rng.choice([0.25, 0.5, 1, 2]),
            decay=rng.choice([0.5, 0.7, 0.9]),
            lookahead=rng.choice([4, 8, 16]),
            order=rng.choice(["est", "prog", "prog", "mix"]),
            prog_w=rng.choice([5, 20, 50]),
            tau=rng.choice([0.1, 0.25, 1.0, 0]),
            win_ahead=rng.choice([1, 2, 3, 5]),
            lam=rng.choice([0.25, 1, 4]),
            reverse_line=rng.random() < 0.2,
            keep=rng.choice([0, 0, 1]),
            jit=rng.random() < 0.8,
            move_w=(rng.choice([0, 500, 2000, 1e5]) if objective == "shuttles" else 0),
            sync=rng.random() < 0.7,
            seed=rng.randrange(10 ** 6)))
    return out
