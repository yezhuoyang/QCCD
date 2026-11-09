"""A logical product measured by a cat state on the stationary-tile machine: the listing's `route` made concrete
(docs/FULLSTACK_PLAN.md, slice S10 step 3).

FormalRV's Gidney 2025 listing measures a logical product by repetitions of this: `cat` ions in a GHZ state (a
binary tree of CNOTs from the first), each coupled to its share of the operator's data ions in turn (CZ on a Z̄'s,
CNOT on an X̄'s), the first coupled to a flag; a verifier made |+>, coupled to the flag and read; every cat ion
read in X.

The machine is a grid of tiles, pitch d + 1: tile (x, y)'s data point (r, c) is at lattice point (12 y + 1 + r,
12 x + 1 + c) at d = 11, in `tiled_surface`'s trap, its measure ions parked at their faces.  A patch may be
turned by 180 degrees (`Target.turned`); the ions then fill the same traps, so its Z̄ (row 0) lies along its lower
edge and its X̄ (column 0) along its right edge.  Between tiles run the seams.  Every tile's data and measure traps
count as taken whether or not the program names their ions (`taken`): what a program moves is the cat, the flag's
visitors and the verifier, and it moves them through the seams and into the traps of its own operators:

  * a data ion's trap is entered from the junction at one of its ends and left by the same (the two ions are a chain
    whose order never changes: R14);
  * one transport cycle is one waveform (R22): a cycle is one hop from a trap to the next, and every ion whose next
    hop is that motion rides it, so a transport takes as long as its longest walk each way, not the sum of them;
    ions in one cycle share no junction or segment (R2, R3 count them per cycle).

The gates are never reordered: every qubit's ops run in the source's order, and each gate is `pulses`'.  The
certificate is the reference compiler's format, as `tiled_surface`'s.

    Target(x, y, turned, letter)           one patch of a product: its tile, its operator
    rep_circuit(targets, d)                the listing's repetition as QASM, on all those patches' ions
    play_rep(targets, d, homes, ...)       its program
    anneal(targets, d, ...)                the free ions' homes, searched by the writer's own time
    play_wide(width, d, ...)               the listing's register-wide cat (`prepMaskedP`, `lookupPP`, `gLoop1`)
"""
from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass
from typing import Mapping, Sequence

from ..ir.tsir import TSIR, Participant
from .c2lr_router import _parse
from .tiled_surface import COMPILER_NAME, TiledError, _Build, _data_trap, _face_trap

__all__ = ["Target", "Grid", "Walker", "rep_circuit", "play_rep", "anneal", "play_wide", "wide_homes", "certificate",
           "HALF_US", "SPLIT_US", "MERGE_US"]

#: one half segment, a split, a merge, µs: `qccd-physics-best`
HALF_US, SPLIT_US, MERGE_US = 94.0, 128.0, 128.0


@dataclass(frozen=True)
class Target:
    """One patch of a product: tile (x, y), turned or not, and the letter of its operator there."""
    x: int
    y: int
    turned: bool
    letter: str


class Grid:
    """The tiles a program's device spans, and what they hold."""

    def __init__(self, d: int, width: int, height: int):
        from ..gadget.surface import patch as surface_patch
        self.d, self.width, self.height = d, width, height
        self.pitch = d + 1
        self.rows = self.pitch * height + 1
        self.cols = self.pitch * width + 1
        self.checks = surface_patch(d, d).checks

    def point(self, t: Target, r: int, c: int) -> tuple:
        row, col = self.pitch * t.y + 1, self.pitch * t.x + 1
        return (row + self.d - 1 - r, col + self.d - 1 - c) if t.turned else (row + r, col + c)

    def face(self, t: Target, i: int, j: int) -> tuple:
        row, col = self.pitch * t.y + 1, self.pitch * t.x + 1
        return (row + self.d - 2 - i, col + self.d - 2 - j) if t.turned else (row + i, col + j)

    def taken(self) -> set:
        """Every tile's data and measure traps: the same set for a turned patch and for a plain one."""
        out = set()
        for y in range(self.height):
            for x in range(self.width):
                t = Target(x, y, False, "Z")
                out.update(_data_trap(*self.point(t, r, c)) for r in range(self.d) for c in range(self.d))
                out.update(_face_trap(*self.face(t, c.i, c.j)) for c in self.checks)
        return out

    def seam_cell(self, node: str) -> tuple | None:
        """The stretch of seam a node of a walk belongs to: ("h", x, y) along the top of tile (x, y), its side traps
        included, ("v", x, y) down its left side; None inside a tile.  What a schedule reserves for a program's
        whole time."""
        p = self.pitch
        body = node[1:]
        kind = body[-1] if node[0] == "T" else "j"
        c, r = (int(v) for v in (body[:-1] if node[0] == "T" else body).split("_"))
        if (kind in ("h", "j") and r % p == 1) or (kind == "v" and r % p in (0, 1)):
            return ("h", max(c - 1, 0) // p, r // p)
        if (kind in ("v", "j") and c % p in (0, 1)) or (kind == "h" and c % p == 0):
            return ("v", c // p, max(r - 1, 0) // p)
        return None


_adjacency: tuple = (None, {})   # the last device's, an anneal plays one device thousands of times


def _adjacent(dev) -> dict:
    global _adjacency
    if _adjacency[0] is not dev:
        adj: dict = {}
        for sid, s in dev.segments.items():
            a, b = s.ends
            adj.setdefault(a, []).append((b, sid))
            adj.setdefault(b, []).append((a, sid))
        _adjacency = (dev, adj)
    return _adjacency[1]


class Walker(_Build):
    """The writer: free ions walk the seams, one hop a cycle, one waveform per motion."""

    def __init__(self, prog: TSIR, pos: dict, arch, taken: set = frozenset()):
        super().__init__(prog, pos)
        self.dev = arch.device
        self.taken = taken
        self.adj = _adjacent(self.dev)
        self.entered: dict = {}     # an ion sharing a trap -> the half segment it came in by
        self.us = 0.0
        self.walked: set = set()    # every node a free ion passed
        self.at: dict = {}          # trap -> its ions, kept by emit
        for i, t in pos.items():
            self.at.setdefault(t, []).append(i)

    def emit(self, **kw) -> Instruction:
        ins = super().emit(**kw)
        for m in ins.participants:
            self.at[m.src].remove(m.ion)
            self.at.setdefault(m.dst, []).append(m.ion)
        return ins

    def occupancy(self) -> dict:
        return self.at

    def motion(self, src: str, via: Sequence[str]) -> tuple:
        out, node = [], src
        for sid in via:
            a, b = self.dev.segments[sid].ends
            nxt = b if a == node else a
            pa, pb = self.dev.nodes[node].pos, self.dev.nodes[nxt].pos
            out.append((round(pb[0] - pa[0], 3), round(pb[1] - pa[1], 3)))
            node = nxt
        return tuple(out)

    def nodes_of(self, src: str, via: Sequence[str]) -> set:
        out, node = {src}, src
        for sid in via:
            a, b = self.dev.segments[sid].ends
            node = b if a == node else a
            out.add(node)
        return out

    def go(self, moves: Sequence[tuple], meta: Mapping | None = None) -> None:
        """Bring ions to traps: moves [(ion, dst)]; any ions whose next hop is the same motion go together."""
        left = {ion: dst for ion, dst in moves if self.pos[ion] != dst}
        ways: dict = {}
        stuck = 0
        while left:
            for ion, dst in left.items():
                if ion not in ways:
                    ways[ion] = self._hops(ion, dst, set(left))
            occ = self.occupancy()
            by_motion: dict = {}
            for ion, way in list(ways.items()):
                nxt, via = way[0]
                others = [o for o in occ.get(nxt, ()) if o != ion]
                if others and len(way) > 1 and all(o not in left for o in others):
                    del ways[ion]           # a parked ion stands in the way: walk round it
                    continue
                by_motion.setdefault(self.motion(self.pos[ion], via), []).append(ion)
            if not by_motion:
                stuck += 1
                if stuck > 8:
                    raise TiledError(f"transport stuck: {left}")
                continue
            motion = max(by_motion, key=lambda m: (len(by_motion[m]), m))
            used: set = set()
            now = []
            going = set(by_motion[motion])
            for ion in sorted(by_motion[motion], key=lambda i: self._front(self.pos[i], motion)):
                nxt, via = ways[ion][0]
                ns = self.nodes_of(self.pos[ion], via) - {self.pos[ion]}
                here = [o for o in occ.get(nxt, ()) if o != ion]
                full = len(here) >= 2 or any(o in left and o not in going for o in here) or (
                    here and len(ways[ion]) > 1)
                if ns & used or full:
                    continue
                used |= ns
                now.append((ion, nxt, via))
            if not now:
                for m in by_motion:
                    if m == motion:
                        continue
                    ion = by_motion[m][0]
                    nxt, via = ways[ion][0]
                    if not [o for o in occ.get(nxt, ()) if o != ion] or len(ways[ion]) == 1:
                        now = [(ion, nxt, via)]
                        break
                else:
                    stuck += 1
                    if stuck > 8:
                        raise TiledError(f"transport stuck: {left}")
                    ways.clear()
                    continue
            stuck = 0
            self.ride(now, meta)
            for ion, *_ in now:
                ways[ion] = ways[ion][1:]
                if not ways[ion]:
                    del ways[ion]
                    del left[ion]

    def _front(self, trap: str, motion: tuple) -> float:
        x, y = self.dev.nodes[trap].pos
        dx, dy = sum(m[0] for m in motion), sum(m[1] for m in motion)
        return -(x * dx + y * dy)

    def _hops(self, ion: str, dst: str, moving: set) -> list:
        """The way to dst as [(next trap, its half segments)], through junctions and traps nothing holds or will
        hold (`taken`, or a parked ion; an ion also on the move will have gone)."""
        src = self.pos[ion]
        occ = self.occupancy()
        first = self.entered.get(ion)
        q = deque([(src, ())])
        seen = {src}
        found = None
        while q and found is None:
            node, via = q.popleft()
            for nb, sid in self.adj[node]:
                if node == src and first and sid != first:
                    continue
                if nb == dst:
                    found = via + (sid,)
                    break
                if nb in seen or nb in self.taken:
                    continue
                if self.dev.nodes[nb].kind == "site" and any(o not in moving for o in occ.get(nb, ())):
                    continue
                seen.add(nb)
                q.append((nb, via + (sid,)))
        if found is None:
            raise TiledError(f"no way for {ion} from {src} to {dst}")
        hops, cur, node = [], [], src
        for sid in found:
            a, b = self.dev.segments[sid].ends
            node = b if a == node else a
            cur.append(sid)
            if self.dev.nodes[node].kind == "site":
                hops.append((node, tuple(cur)))
                cur = []
        return hops

    def ride(self, now: Sequence[tuple], meta: Mapping | None = None) -> None:
        """One waveform: [(ion, its next trap, its half segments)]."""
        occ = self.occupancy()
        srcs = [self.pos[m[0]] for m in now]
        split = any(len(occ.get(s, ())) > 1 for s in srcs)
        merge = any(occ.get(m[1]) for m in now)
        self.us += HALF_US * max(len(m[2]) for m in now) + (SPLIT_US if split else 0) + (MERGE_US if merge else 0)
        for (ion, dst, via), src in zip(now, srcs):
            self.walked |= self.nodes_of(src, via)
        self.emit(type="simd", cls="shuttle", mode="inter",
                  participants=tuple(Participant(i, self.pos[i], d, tuple(v)) for i, d, v in now),
                  meta={"kind": "route", **(meta or {})})
        occ = self.occupancy()
        for (ion, dst, via), src in zip(now, srcs):
            self.entered.pop(ion, None)
            if len(occ.get(dst, ())) > 1:
                self.entered[ion] = via[-1]
            if len(occ.get(src, ())) == 1:
                self.entered.pop(occ[src][0], None)


def certificate(name: str, arch, n: int, ops, ion: Mapping, place: Mapping, b: _Build) -> dict:
    prog = b.prog
    return {
        "version": 1, "circuit": name, "arch": arch.name, "n_qubits": n,
        "circuit_ops": [o.to_json() for o in ops],
        "map": {str(q): ion[q] for q in range(n)}, "init": dict(place),
        "moves": b.moves, "gates": b.gates, "rotations": [], "unrealised": [],
        "claims": {"cycles": len(prog.instructions),
                   "beams": sum(len(i.ions) for i in prog.instructions if i.gate == "R"),
                   "ms_gates": sum(len(i.pairs) for i in prog.instructions if i.gate == "MS"),
                   "frames": sum(len(i.ions) for i in prog.instructions if i.gate == "VZ")},
        "compiler": COMPILER_NAME,
    }


def _start(name: str, arch, n: int, place: Mapping, taken: set) -> tuple:
    ion = {q: f"q{q}" for q in range(n)}
    prog = TSIR(name=name, arch_spec=f"arch/{arch.name}.arch.json",
                meta={"compiler": COMPILER_NAME, "qubit_map": {str(q): ion[q] for q in range(n)}})
    b = Walker(prog, dict(place), arch, taken)
    b.emit(type="init", placement=dict(place), quanta={i: 0.0 for i in place},
           meta={"compiler": COMPILER_NAME, "circuit": name, "arch": arch.name})
    b.emit(type="cool", broadcast=True, meta={"kind": "state_prep"})
    return b, ion


def _home(grid: Grid, targets: Sequence[Target]) -> tuple:
    """(data points, faces, placement): all ions of the targets, each in its trap."""
    d = grid.d
    at, faces = [], []
    for t in targets:
        at += [grid.point(t, r, c) for r in range(d) for c in range(d)]
    for t in targets:
        faces += [grid.face(t, c.i, c.j) for c in grid.checks]
    nd = len(at)
    place = {f"q{q}": _data_trap(*at[q]) for q in range(nd)}
    place.update({f"q{nd + i}": _face_trap(*f) for i, f in enumerate(faces)})
    return at, faces, place


def _tree(cat: Sequence[int]) -> list:
    """The listing's cat: layer L couples i to i + 2^L for each i < 2^L."""
    out, size = [], 1
    while size < len(cat):
        out.append([(cat[i], cat[i + size]) for i in range(min(size, len(cat) - size))])
        size *= 2
    return out


def operator_gates(letter: str, d: int) -> list:
    """The operator's gates in the listing's order, [(data offset, gate)]: Z̄ a row by CZ, X̄ a column by CNOT, Ȳ their
    crossing by CY, then the rest of the column, then the rest of the row."""
    row = [(c, "cz") for c in range(d)]
    column = [(r * d, "cx") for r in range(d)]
    if letter == "Z":
        return row
    if letter == "X":
        return column
    if letter == "Y":
        return [(0, "cy")] + column[1:] + row[1:]
    raise TiledError(f"no operator {letter!r}")


def rep_circuit(targets: Sequence[Target], d: int) -> tuple:
    """One repetition as the listing writes it: the operators' data ions in order, four to a cat ion."""
    n_data = len(targets) * d * d
    n_faces = len(targets) * (d * d - 1)
    pieces = []
    for i, t in enumerate(targets):
        base = i * d * d
        pieces += [(base + off, g) for off, g in operator_gates(t.letter, d)]
    k = (len(pieces) + 3) // 4
    first = n_data + n_faces
    cat = list(range(first, first + k))
    flag, ver = first + k, first + k + 1
    n = first + k + 2
    assign = [pieces[4 * i: 4 * i + 4] for i in range(k)]
    out = ['OPENQASM 2.0;', 'include "qelib1.inc";', f"qreg q[{n}];", f"creg c[{k + 1}];"]
    out += [f"reset q[{c}];" for c in cat] + [f"reset q[{ver}];"]
    out.append(f"h q[{cat[0]}];")
    tree = _tree(cat)
    for lay in tree:
        out += [f"cx q[{a}], q[{b}];" for a, b in lay]
    for j in range(4):
        for i in range(k):
            if j < len(assign[i]):
                q, g = assign[i][j]
                if g == "cy":       # qelib1's own definition, in the gates the machine plays
                    out += [f"sdg q[{q}];", f"cx q[{cat[i]}], q[{q}];", f"s q[{q}];"]
                else:
                    out.append(f"{g} q[{cat[i]}], q[{q}];")
    out.append(f"cx q[{cat[0]}], q[{flag}];")
    out += [f"h q[{ver}];", f"cx q[{ver}], q[{flag}];", f"h q[{ver}];", f"measure q[{ver}] -> c[{k}];"]
    out += [f"h q[{c}];" for c in cat]
    out += [f"measure q[{c}] -> c[{i}];" for i, c in enumerate(cat)]
    return "\n".join(out) + "\n", cat, flag, ver, assign, tree, n


class _Play:
    """The ops of a source, played by name."""

    def __init__(self, b: Walker, ion: Mapping, ops):
        self.b, self.ion, self.ops = b, ion, ops
        self.done: set = set()
        self.by: dict = {}
        for o in ops:
            self.by.setdefault((o.name, o.qubits), []).append(o)

    def next(self, name: str, *qubits) -> object:
        return self.by[(name, qubits)][0]

    def gates(self, group: Sequence) -> None:
        if group:
            self.b.play(group, self.ion)
            for o in group:
                self.done.add(o.i)
                self.by[(o.name, o.qubits)].remove(o)

    def record(self, kind: str, group: Sequence) -> None:
        if group:
            self.b.emit(type=kind, ions=tuple(self.ion[o.qubits[0]] for o in group),
                        meta={"kind": "readout" if kind == "measure" else "reset", "op": [o.i for o in group]})
            for o in group:
                self.done.add(o.i)
                self.by[(o.name, o.qubits)].remove(o)

    def rest(self, name: str) -> list:
        return [o for o in self.ops if o.name == name and o.i not in self.done]


def play_rep(targets: Sequence[Target], grid: Grid, arch, homes: Sequence[str], taken: set, name: str = "cat_rep") -> tuple:
    """The repetition's program: homes are the cat ions', then the flag's, then the verifier's.
    Returns (walker, certificate, qasm)."""
    d = grid.d
    at, _faces, place = _home(grid, targets)
    qasm, cat, flag, ver, assign, tree, n = rep_circuit(targets, d)
    _, ops, _ = _parse(qasm)
    k = len(cat)
    for i, c in enumerate(cat):
        place[f"q{c}"] = homes[i]
    place[f"q{flag}"], place[f"q{ver}"] = homes[k], homes[k + 1]
    b, ion = _start(name, arch, n, place, taken)
    p = _Play(b, ion, ops)
    hm = {c: b.pos[ion[c]] for c in cat}
    p.record("reset", p.rest("reset"))
    p.gates([p.next("h", cat[0])])
    for lay in tree:
        b.go([(ion[y], hm[x]) for x, y in lay])
        p.gates([p.next("cx", x, y) for x, y in lay])
        b.go([(ion[y], hm[y]) for x, y in lay])
    for j in range(4):
        grp, mv, frames = [], [], []
        for i, c in enumerate(cat):
            if j < len(assign[i]):
                q, g = assign[i][j]
                mv.append((ion[c], _data_trap(*at[q])))
                if g == "cy":
                    frames.append(q)
                    g = "cx"
                grp.append(p.next(g, c, q))
        b.go(mv)
        p.gates([p.next("sdg", q) for q in frames])
        p.gates(grp)
        p.gates([p.next("s", q) for q in frames])
    fl, vh = b.pos[ion[flag]], b.pos[ion[ver]]
    b.go([(ion[cat[0]], fl)])
    p.gates([p.next("cx", cat[0], flag)])
    b.go([(ion[c], hm[c]) for c in cat])
    b.go([(ion[ver], fl)])
    p.gates([p.next("h", ver)])
    p.gates([p.next("cx", ver, flag)])
    b.go([(ion[ver], vh)])
    p.gates(p.rest("h"))
    p.record("measure", p.rest("measure"))
    if any(o.i not in p.done for o in ops):
        raise TiledError("ops left unplayed")
    return b, certificate(name, arch, n, ops, ion, place, b), qasm


def anneal(targets: Sequence[Target], grid: Grid, arch, *, tries: int = 400, seed: int = 1, start=None) -> tuple:
    """Homes for the free ions by simulated annealing over the free traps near the operators, on the
    writer's own transport time.  Returns (µs, homes)."""
    rng = random.Random(seed)
    taken = grid.taken()
    at, _faces, _place = _home(grid, targets)
    d = grid.d
    _q, cat, _f, _v, assign, _t, _n = rep_circuit(targets, d)
    k = len(cat)
    dev = arch.device
    pts = [at[q] for a in assign for q, _ in a]

    def near(t):
        x, y = dev.nodes[t].pos
        return min(abs(x - (2 * c + 1)) + abs(y - (2 * r + 2)) for r, c in pts) <= 3
    pool = [t for t, nd in dev.nodes.items() if nd.kind == "site" and t not in taken and near(t)]
    if len(pool) < k + 2:
        raise TiledError(f"{len(pool)} free traps for {k + 2} ions")

    def cost(s):
        try:
            return play_rep(targets, grid, arch, s, taken)[0].us
        except TiledError:
            return None
    def dist(t, q):
        x, y = dev.nodes[t].pos
        r, c = at[q]
        return abs(x - (2 * c + 1)) + abs(y - (2 * r + 2))
    if start is None:           # a first guess: each cat ion by its first data ion, the flag by the first's last
        start = []
        for a in assign:
            start.append(min((t for t in pool if t not in start), key=lambda t: dist(t, a[0][0])))
        for _ in range(2):
            start.append(min((t for t in pool if t not in start), key=lambda t: dist(t, assign[0][-1][0])))
    cur = None
    c = cost(list(start))
    if c is not None:
        cur = (c, list(start))
    # Ions cross a long way together on one waveform, so all the free ions at one operator is the other kind of start:
    # the tree stays local and one walk takes the far operator's cat ions over.
    d2 = d * d
    operators: dict = {}
    for a in assign:
        for q, _g in a:
            operators.setdefault(q // d2, []).append(q)
    if len(operators) > 1:
        for qs in operators.values():
            close = [t for t in pool if min(dist(t, q) for q in qs) <= 3]
            if len(close) < k + 2:
                continue
            for _ in range(tries // 5):
                s = rng.sample(close, k + 2)
                c = cost(s)
                if c is not None and (cur is None or c < cur[0]):
                    cur = (c, s)
    for _ in range(2000):
        if cur is not None:
            break
        s = rng.sample(pool, k + 2)
        c = cost(s)
        if c is not None:
            cur = (c, s)
    if cur is None:
        raise TiledError("no homes found")
    best = cur
    for t in range(tries):
        temp = 300.0 * (1 - t / tries) + 1
        s = list(cur[1])
        i = rng.randrange(k + 2)
        if rng.random() < 0.35:
            j = rng.randrange(k + 2)
            s[i], s[j] = s[j], s[i]
        else:
            alt = rng.choice(pool)
            if alt in s:
                continue
            s[i] = alt
        c = cost(s)
        if c is None:
            continue
        if c < cur[0] or rng.random() < math.exp((cur[0] - c) / temp):
            cur = (c, s)
            if c < best[0]:
                best = cur
    return best


# ----------------------------------------------------------------------------- the register-wide cat

def _lane_walk(b: Walker, lane_row: int, group: Sequence[str], sign: int, home: Mapping) -> None:
    """Ions in side traps at a lane walk it to `home[ion]`, a side trap above or below it, all one way, everyone on
    the lane riding one waveform.  `group`: the order they may step on."""
    S = lane_row

    def col(t):
        return int(t[1:].split("_")[0])
    waiting = list(group)
    lane: dict = {}
    while waiting or lane:
        stop = [i for i in lane if lane[i] == (col(home[i]) - 1 if sign > 0 else col(home[i]))]
        if stop:
            kinds: dict = {}
            for i in stop:
                kinds.setdefault(home[i].endswith(f"_{S}v"), []).append(i)
            for lower, ions in kinds.items():
                now = []
                for i in ions:
                    c, t = lane[i], home[i]
                    last = f"{t}.a" if lower else f"{t}.b"
                    now.append((i, t, (f"T{c}_{S}h.b", last) if sign > 0 else (f"T{c}_{S}h.a", last)))
                b.ride(now)
            for i in stop:
                del lane[i]
            continue
        occupied = set(lane.values())
        went = False
        for ion in waiting:
            t = b.pos[ion]
            c = col(t)
            cell = c if sign > 0 else c - 1
            if {cell - 1, cell, cell + 1} & occupied:
                continue
            first = f"{t}.a" if t.endswith(f"_{S}v") else f"{t}.b"
            second = f"T{c}_{S}h.a" if sign > 0 else f"T{c - 1}_{S}h.b"
            b.ride([(ion, f"T{cell}_{S}h", (first, second))])
            lane[ion] = cell
            waiting.remove(ion)
            went = True
            break
        if went:
            continue
        now = []
        for i in sorted(lane, key=lambda i: -sign * lane[i]):
            c, nxt = lane[i], lane[i] + sign
            now.append((i, f"T{nxt}_{S}h", (f"T{c}_{S}h.b", f"T{nxt}_{S}h.a") if sign > 0
                        else (f"T{c}_{S}h.a", f"T{nxt}_{S}h.b")))
        b.ride(now)
        for i, *_ in now:
            lane[i] += sign


def wide_homes(grid: Grid, targets: Sequence[Target], taken: set, lane_row: int) -> tuple:
    """Side traps above and below the lane nearest its middle, in bit-reversed order of the cat: a tree layer's
    partners are then one distance apart and their walks do not overlap.  Keeps out of the targets' columns."""
    d = grid.d
    columns = {grid.point(t, 0, 0)[1] for t in targets[1:]}
    mid = sorted(columns)[len(columns) // 2]
    side = []
    for c in range(grid.cols + 1):
        for r, kind in ((lane_row, "v"), (lane_row - 1, "v")):
            t = f"T{c}_{r}{kind}"
            if t not in taken and not (r == lane_row and c in columns):
                side.append((abs(c - mid), c, t))
    side.sort()
    k = len(targets)
    picks = sorted(side[: k + 2], key=lambda x: x[1])
    flag_at, ver_at = picks[(k + 2) // 2][2], picks[(k + 2) // 2 + 1][2]
    picks = [p for p in picks if p[2] not in (flag_at, ver_at)]
    bits = max(1, math.ceil(math.log2(k)))
    order = sorted(range(k), key=lambda i: int(format(i, f"0{bits}b")[::-1], 2))
    homes = [""] * k
    for pos, i in enumerate(order):
        homes[i] = picks[pos][2]
    return homes, flag_at, ver_at


def play_wide(targets: Sequence[Target], grid: Grid, arch, name: str = "wide_rep", mask=None) -> tuple:
    """One repetition of the register-wide cat over X̄ of `targets`: targets[0] the control (by cat 0), the
    others in a row of tiles, each by its own cat ion, all walking their columns at once.  `mask` names the row's
    tiles a masked cat acts on (the listing's `prepMaskedP`): every ion is in the tree, the others stay home.
    Returns (walker, certificate, qasm, parts): `parts` splits the writer's time into tree, out, columns, back, flag."""
    d = grid.d
    taken = grid.taken()
    at, _faces, place = _home(grid, targets)
    nd = len(at) + len(targets) * (d * d - 1)
    k = len(targets)
    cat = list(range(nd, nd + k))
    flag, ver = nd + k, nd + k + 1
    n = nd + k + 2
    lines = ['OPENQASM 2.0;', 'include "qelib1.inc";', f"qreg q[{n}];", f"creg c[{k + 1}];"]
    lines += [f"reset q[{c}];" for c in cat] + [f"reset q[{ver}];", f"h q[{cat[0]}];"]
    tree = _tree(cat)
    for lay in tree:
        lines += [f"cx q[{a}], q[{b}];" for a, b in lay]
    acting = [i for i in range(1, k) if mask is None or i - 1 in mask]
    for p_ in [0] + acting:
        lines += [f"cx q[{cat[p_]}], q[{p_ * d * d + r * d}];" for r in range(d)]
    lines.append(f"cx q[{cat[0]}], q[{flag}];")
    lines += [f"h q[{ver}];", f"cx q[{ver}], q[{flag}];", f"h q[{ver}];", f"measure q[{ver}] -> c[{k}];"]
    lines += [f"h q[{c}];" for c in cat]
    lines += [f"measure q[{c}] -> c[{i}];" for i, c in enumerate(cat)]
    qasm = "\n".join(lines) + "\n"
    _, ops, _ = _parse(qasm)
    y = targets[1].y
    lane_row = grid.pitch * y + 1
    homes, flag_at, ver_at = wide_homes(grid, targets, taken, lane_row)
    for i, c in enumerate(cat):
        place[f"q{c}"] = homes[i]
    place[f"q{flag}"], place[f"q{ver}"] = flag_at, ver_at
    b, ion = _start(name, arch, n, place, taken)
    p = _Play(b, ion, ops)
    hm = {c: b.pos[ion[c]] for c in cat}
    p.record("reset", p.rest("reset"))
    p.gates([p.next("h", cat[0])])
    t0 = b.us
    for lay in tree:
        b.go([(ion[v], hm[u]) for u, v in lay])
        p.gates([p.next("cx", u, v) for u, v in lay])
        b.go([(ion[v], hm[v]) for u, v in lay])
    t1 = b.us

    def col(t):
        return int(t[1:].split("_")[0])
    # out along the lane, each to the side trap above its column
    above = {ion[cat[i]]: f"T{grid.point(targets[i], 0, 0)[1]}_{lane_row}v" for i in acting}
    for sign in (1, -1):
        go = [i for i in above if (col(above[i]) - col(b.pos[i])) * sign > 0]
        go.sort(key=lambda i: -abs(col(above[i]) - col(b.pos[i])))
        _lane_walk(b, lane_row, go, sign, above)
    b.go([(i, above[i]) for i in above if b.pos[i] != above[i]])
    b.go([(ion[cat[0]], _data_trap(*at[0]))])
    p.gates([p.next("cx", cat[0], 0)])
    t2 = b.us
    # down the columns, all together: the control's walks with them once it has its first
    for r in range(d):
        mv, grp = [], []
        for i in acting:
            q = i * d * d + r * d
            mv.append((ion[cat[i]], _data_trap(*at[q])))
            grp.append(p.next("cx", cat[i], q))
        if r:
            mv.append((ion[cat[0]], _data_trap(*at[r * d])))
            grp.append(p.next("cx", cat[0], r * d))
        b.go(mv)
        p.gates(grp)
    t3 = b.us
    fl, vh = b.pos[ion[flag]], b.pos[ion[ver]]
    b.go([(ion[cat[0]], fl)])
    p.gates([p.next("cx", cat[0], flag)])
    b.go([(ion[cat[0]], hm[cat[0]])])
    b.go([(i, above[i]) for i in above])
    back = {ion[cat[i]]: hm[cat[i]] for i in acting}
    for sign in (-1, 1):
        go = [i for i in back if (col(back[i]) - col(b.pos[i])) * sign > 0]
        go.sort(key=lambda i: -sign * col(b.pos[i]))
        _lane_walk(b, lane_row, go, sign, back)
    b.go([(i, back[i]) for i in back if b.pos[i] != back[i]])
    t4 = b.us
    b.go([(ion[ver], fl)])
    p.gates([p.next("h", ver)])
    p.gates([p.next("cx", ver, flag)])
    b.go([(ion[ver], vh)])
    p.gates(p.rest("h"))
    p.record("measure", p.rest("measure"))
    if any(o.i not in p.done for o in ops):
        raise TiledError("ops left unplayed")
    parts = tuple(round(x) for x in (t1 - t0, t2 - t1, t3 - t2, t4 - t3, b.us - t4))
    return b, certificate(name, arch, n, ops, ion, place, b), qasm, parts
