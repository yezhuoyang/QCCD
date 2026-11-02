"""The magic-state factory of the stationary-tile machine (docs/FULLSTACK_PLAN.md, slice S9 step
4), as lattice instruction streams (`lattice.schedule` runs them).

**15-to-1** (`distill_15to1`): five patches in |+>; for each of the 15 nonzero v in F_2^4, a π/8
rotation of Z on the patches of supp(v) and the output patch; then the first four read in X,
and the output kept when all four read +1.  With no faults it accepts every time and leaves
T†|+>; every single and double fault in a magic state is caught, 35 sets of three are not --
the output fails with 35 p³ (`fault_census`, by state vector).

Each rotation is a teleported T: a fresh patch injected with |T> (`prep m T`:
`qccd.qec.experiments.injection`), the product of the rotation's Z with Z on it, and its X
readout.  A product of -1 turned the rotation the wrong way, so exp(-iπ/4 P) follows, by a
|Ȳ> (`prep y Y`: Gidney's in place, `y_memory`), the product with it and its X readout --
which is exact up to a Pauli.  What is left is Pauli frames, Z on the rotation's patches.
`run` executes a stream on a state vector, outcomes sampled, frames applied.

**8T -> |AutoCCZ>** (`autoccz`): the state FormalRV's Toffoli consumes (`autoccz.circ`: CCZ|+++>
on x y z and three routing pairs) from eight T states.  The [[8,3,2]] cube code's T on even
vertices, T† on odd, is logical CCZ; written with the code's rows chosen as the all-ones word (a
check patch) and the three faces (x, y, z), vertex v is a π/8 rotation of Z on the check and on
the outputs of supp(v) -- T for even |v|, T† for odd, the same teleported gadget with the
correction's guard flipped -- and both values of the check leave CCZ|+++>, so the check reads +1
in X (any one T fault turns it: 28 p² out).  The routing pairs are, by commuting their CNOTs
through, CZ edges: CZ_xy CZ_xz CZ_yz on x y z -- Pauli frames, since CCZ CZ_xy CZ_xz CZ_yz|+++> =
Z⊗3 X⊗3 CCZ|+++> -- and each site attached by CZ to its partner and one of x y z.  A site
attached to qubits all in one frame is made by one merge on a fresh patch, in the opposite frame:
H-turned and measured Z with them (a parity copy), or plain and measured X with turned ones.
FormalRV's consumption needs x, y plain and z turned (its X̄_t Z̄_z becomes X̄X̄), so z takes an
H̄; the pair on x and y closes an odd cycle and takes a second H̄ on one site.  The output is
held with `TURNED` sites H-turned; `held` swaps the letters of the consumer's instructions on
them, which is exact (a read of H|ψ> in B is a read of |ψ> in HBH).
"""
from __future__ import annotations

import itertools
import random
import re

import numpy as np

__all__ = ["distill_15to1", "run", "fault_census", "autoccz", "held", "ccz_census", "ccz_factory",
           "TURNED"]


def distill_15to1(tag: str = "f") -> tuple:
    """(stream, blocks): the 15-to-1 as lattice instructions on blocks `{tag}q0..q4` (q4 the
    output); fresh `{tag}m<j>` (|T>) and `{tag}y<j>` (|Ȳ>) per rotation; outcomes `{tag}r0..r3`
    are the four checks."""
    q = [f"{tag}q{i}" for i in range(5)]
    lines = [("prep", [b, "X"], None, []) for b in q]
    vs = [v for v in itertools.product((0, 1), repeat=4) if any(v)]
    for j, v in enumerate(vs):
        blocks = [q[i] for i in range(4) if v[i]] + [q[4]]
        m, y = f"{tag}m{j}", f"{tag}y{j}"
        a, b, c, e = (f"{tag}{x}{j}" for x in "abce")
        prod = [x for blk in blocks for x in ("Z", blk)]
        lines += [("prep", [m, "T"], None, []), ("ppm", prod + ["Z", m], a, []), ("read", [m, "X"], b, []),
                  ("prep", [y, "Y"], None, []), ("ppm", prod + ["Z", y], c, [a]), ("read", [y, "X"], e, [a])]
        for blk in blocks:
            lines += [("z", [blk], None, [b]), ("z", [blk], None, [a, c]), ("z", [blk], None, [a, e])]
    lines += [("read", [q[i], "X"], f"{tag}r{i}", []) for i in range(4)]
    return lines, q


#: the |AutoCCZ> sites held H-turned (site index in FormalRV's order x y z s3 .. s8)
TURNED = (2, 4, 6, 8)


def autoccz(tag: str = "c", ts=None) -> tuple:
    """(stream, blocks): |AutoCCZ> as lattice instructions on blocks `{tag}k` (the check) and
    `{tag}l0..l8` (FormalRV's sites x y z s3 .. s8, `TURNED` ones H-turned); outcome `{tag}r` is
    the check.  `ts`: eight blocks holding T†|+> (the 15-to-1's output) for the eight rotations,
    else fresh `{tag}m<j>` injected with |T>."""
    k, l = f"{tag}k", [f"{tag}l{i}" for i in range(9)]
    lines = [("prep", [b, "X"], None, []) for b in [k] + l[:3]]
    for j, v in enumerate(itertools.product((0, 1), repeat=3)):
        blocks = [k] + [l[i] for i in range(3) if v[i]]
        prod = [x for blk in blocks for x in ("Z", blk)]
        m, y = (ts[j] if ts else f"{tag}m{j}"), f"{tag}y{j}"
        a, b, c, e = (f"{tag}{x}{j}" for x in "abce")
        g = a if sum(v) % 2 == 0 else "!" + a          # T wants a = 0's turn, T† a = 1's
        lines.append(("x", [m], None, []) if ts else ("prep", [m, "T"], None, []))
        lines += [("ppm", prod + ["Z", m], a, []), ("read", [m, "X"], b, []),
                  ("prep", [y, "Y"], None, []), ("ppm", prod + ["Z", y], c, [g]), ("read", [y, "X"], e, [g])]
        for blk in blocks:
            lines += [("z", [blk], None, [b]), ("z", [blk], None, [g, c]), ("z", [blk], None, [g, e])]
            if g != a:                                # exp(+iπ/4 P) is exp(-iπ/4 P) then P
                lines.append(("z", [blk], None, [g]))
    lines.append(("read", [k, "X"], f"{tag}r", []))
    lines += [(p, [b], None, []) for p in "xz" for b in l[:3]]        # CZ_xy CZ_xz CZ_yz
    lines.append(("ht", [l[2]], None, []))

    def attach(s, partners, turned, o):
        if turned:                  # H|parity of the partners>: CZ to each of them
            return [("prep", [l[s], "X"], None, []), ("ppm", ["Z", l[s]] + [x for p in partners for x in ("Z", l[p])],
                                                      f"{tag}{o}", []), ("x", [l[s]], None, [f"{tag}{o}"])]
        return [("prep", [l[s], "Z"], None, []), ("ppm", ["X", l[s]] + [x for p in partners for x in ("X", l[p])],
                                                  f"{tag}{o}", []), ("z", [l[s]], None, [f"{tag}{o}"])]
    lines += attach(4, [1], True, "p4") + attach(3, [4, 2], False, "p3")          # (s3, s4) on y, z
    lines += attach(6, [0], True, "p6") + attach(5, [6, 2], False, "p5")          # (s5, s6) on x, z
    lines += attach(7, [1], True, "p7") + [("ht", [l[7]], None, [])]              # (s7, s8) on x, y
    lines += attach(8, [7, 0], True, "p8")
    return lines, [k] + l


def held(stream, blocks) -> list:
    """`stream` with X and Z swapped on `blocks` in products, readouts and frames: what it does to
    states held H-turned on those blocks."""
    sw, turn = {"X": "Z", "Z": "X", "x": "z", "z": "x"}, set(blocks)
    out = []
    for w, a, o, g in stream:
        if w == "ppm":
            a = [x for i in range(0, len(a), 2) for x in ((sw[a[i]] if a[i + 1] in turn else a[i]), a[i + 1])]
        elif w == "read" and a[0] in turn:
            a = [a[0], sw[a[1]]]
        elif w in ("x", "z") and a[0] in turn:
            w = sw[w]
        out.append((w, a, o, g))
    return out


def ccz_factory(tag: str = "c") -> tuple:
    """(stream, blocks): eight 15-to-1s (`{tag}t0..t7`) and the |AutoCCZ> stage on their outputs;
    `blocks` the long-lived ones, the |AutoCCZ> stage's last."""
    stream, blocks = [], []
    for j in range(8):
        s, q = distill_15to1(f"{tag}t{j}")
        stream += s
        blocks += q
    s, b = autoccz(tag, [f"{tag}t{j}q4" for j in range(8)])
    return stream + s, blocks + b


#: the narrow factory's |AutoCCZ> patches, two rows below its four strips: (x, y)
NARROW_ROWS = {"cl3": (0, 11), "cl4": (2, 11), "ck": (4, 11), "cl5": (6, 11), "cl7": (8, 11), "cl6": (10, 11),
               "cl8": (0, 13), "cl0": (2, 13), "cl1": (4, 13), "cl2": (6, 13)}


def factory_schedule(tag: str = "c", costs=None, strips: int = 8, rows=None, walls=()) -> tuple:
    """(stream, gadgets, homes, bounds): `ccz_factory` on the lattice.  One 15-to-1 is scheduled
    on a strip of its own (its five patches a column, a bus column each side) and the eight are
    translated copies of it, strips side by side -- the same gadgets, so the same shapes; the
    |AutoCCZ> stage's ten patches sit in a row below, and it is scheduled around the copies, each
    T output free once its 15-to-1 is done, and its patches made only then (so the row is free
    while the 15-to-1s run: a farm's next state can start before the last is consumed).

    `strips` 4: the narrow factory, 12 tiles wide.  Four strips make two T each in turn -- the
    second 15-to-1 on a strip a copy started once the stage has read the strip's first T and no
    stage gadget is on the strip -- and the stage's patches sit in two rows (`rows`, by default
    `NARROW_ROWS`), scheduled in two parts, around the first copies and then around the second."""
    from qccd.fullstack.scale.lattice import Costs, holds, schedule, shifted
    costs = costs or Costs()
    stream, blocks = ccz_factory(tag)
    unit, q = distill_15to1(f"{tag}t0")
    gs = schedule(unit, {b: (0, 2 * i) for i, b in enumerate(q)}, (-1, -1, 1, 9), costs)
    if strips == 4:
        return _narrow(tag, costs, stream, blocks, unit, q, gs, rows or NARROW_ROWS, walls)
    homes, copies = {}, []
    for j in range(8):
        name = lambda b, j=j: f"{tag}t{j}" + b[len(f"{tag}t0"):]
        copies += [shifted(g, 3 * j, 0, j * len(unit), name) for g in gs]
        homes.update({name(b): (3 * j, 2 * i) for i, b in enumerate(q)})
    homes.update({b: (2 + 2 * i, 12) for i, b in enumerate(blocks[40:])})
    bounds = (-1, -1, 22, 13)
    reserved = [(t, g.t0, g.t1) for g in copies for t in g.tiles] + [(t, a, z) for t, a, z, _ in holds(copies)]
    done = max(g.t1 for g in copies)
    stage = schedule(stream[8 * len(unit):], homes, bounds, costs, reserved=reserved,
                     start={b: done for b in [f"{tag}t{j}q4" for j in range(8)] + blocks[40:]})
    return stream, copies + [shifted(g, 0, 0, 8 * len(unit)) for g in stage], homes, bounds


def _narrow(tag, costs, stream, blocks, unit, q, gs, rows, walls=()) -> tuple:
    from qccd.fullstack.scale.lattice import holds, schedule, shifted
    n, done = len(unit), max(g.t1 for g in gs)
    name = lambda j: (lambda b: f"{tag}t{j}" + b[len(f"{tag}t0"):])
    homes = {name(j)(b): (3 * (j % 4), 2 * i) for j in range(8) for i, b in enumerate(q)}
    homes.update({b.replace("c", tag, 1): xy for b, xy in rows.items()})
    bounds = (-1, -1, 10, max(y for _, y in rows.values()) + 1)

    def taken(gg):
        return [(t, g.t0, g.t1) for g in gg for t in g.tiles] + [(t, a, z) for t, a, z, _ in holds(gg)]
    first = [g2 for j in range(4) for g2 in (shifted(g, 3 * j, 0, j * n, name(j)) for g in gs)]
    rest = stream[8 * n:]
    cut = next(i for i, x in enumerate(rest) if x[0] == "x" and x[1] == [f"{tag}t4q4"])
    part1 = [shifted(g, 0, 0, 8 * n) for g in schedule(
        rest[:cut], homes, bounds, costs, walls=walls, reserved=taken(first),
        start={b: done for b in [f"{tag}t{j}q4" for j in range(4)] + blocks[40:]})]
    busy: dict = {}
    for t, a, z in taken(first + part1):
        busy.setdefault(t, []).append((a, z))
    second = []
    for j in range(4):
        dt = max(g.t1 for g in part1 if f"{tag}t{j}q4" in g.blocks)
        while True:
            cp = [shifted(g, 3 * j, 0, (4 + j) * n, name(4 + j), dt) for g in gs]
            clash = [e for t, a, z in taken(cp) for s, e in busy.get(t, []) if s < z - 1e-6 and e > a + 1e-6]
            if not clash:
                break
            dt = max(dt, min(clash))
        second += cp
        for t, a, z in taken(cp):
            busy.setdefault(t, []).append((a, z))
    start = {b: max((g.t1 for g in part1 if b in g.blocks), default=done) for b in blocks[40:]}
    start.update({f"{tag}t{j}q4": max(g.t1 for g in second if f"{tag}t{j}q4" in g.blocks) for j in range(4, 8)})
    part2 = [shifted(g, 0, 0, 8 * n + cut) for g in schedule(
        rest[cut:], homes, bounds, costs, walls=walls, reserved=taken(first + part1 + second), start=start)]
    return stream, first + second + part1 + part2, homes, bounds


class Farm:
    """`n` CCZ factories (`factory_schedule`, scheduled once) side by side, their |AutoCCZ> rows
    along y = `y`, the first factory's left edge at x = `x0`; a program's `autoccz_h` lines
    become `macro` lines (`ports`) and each is served by a translated, time-shifted copy of the
    factory: the factory free soonest, once its 15-to-1 strips are done with its last state and
    the sites it will make the next state on are all read -- or, with `serve`, the factory
    `serve(k)` for the k-th state (a fixed assignment: with the program's blocks laid out to
    match, every consumer reaches its factory alike, so the same shapes repeat).

    `buffers` 2 (with `strips` 4): two sets of sites, the second two rows below the first, a
    factory's states taking them in turn -- its next state is made while the last one waits to
    be consumed.  A factory's inside is walled to everything else (`walls`): only its rows of
    sites and the bus rows between and below them are open.  `tag` prefixes every name it gives
    (two farms on one lattice)."""

    def __init__(self, n: int, x0: int, y: int, costs=None, serve=None, strips: int = 8, buffers: int = 1,
                 tag: str = ""):
        from qccd.fullstack.scale.lattice import holds
        if buffers > 1 and strips != 4:
            raise ValueError("a second set of sites is laid out for the narrow factory (strips 4)")
        sites = [f"cl{i}" for i in range(9)]
        self.variants = []
        sets = [{b: (x, yy + 4 * v) for b, (x, yy) in NARROW_ROWS.items()} for v in range(buffers)]
        for v in range(buffers):
            rows = sets[v] if strips == 4 else None
            # the other sets' sites hold states while this one works: never a corridor through them
            others = {t for u, r in enumerate(sets) if u != v for t in r.values()} if buffers > 1 else ()
            stream, gadgets, homes, b = factory_schedule("c", costs, strips, rows, others)
            units = [g for g in gadgets if g.blocks and all(x.startswith("ct") for x in g.blocks)]
            self.variants.append({
                "gadgets": gadgets, "homes": homes, "bounds": b, "inner": holds(gadgets),
                "row_from": min(g.t0 for g in gadgets if set(g.blocks) & {"ck", *sites}),
                "made": {s: max(g.t1 for g in gadgets if s in g.blocks) for s in sites},
                "strips_done": max(g.t1 for g in units)})
            self.stream = stream
        b0 = self.variants[0]["bounds"]
        self.local = (b0[0], b0[1], b0[2], max(v["bounds"][3] for v in self.variants))
        self.n, self.w = n, self.local[2] - self.local[0] + 1
        self.dx = [x0 - self.local[0] + f * self.w for f in range(n)]
        self.homes = self.variants[0]["homes"]
        self.dy = y - self.homes["cl0"][1]
        self.span = max(g.t1 for g in self.variants[0]["gadgets"])
        self.serve = serve
        self.free = [0.0] * n                 # when each factory's strips may start again
        self.last = [[()] * buffers for _ in range(n)]     # per set of sites, the state it holds
        self.turn = [0] * n
        self.served: list = []                # (line, factory, dt)
        self.tag = tag

    def bounds(self) -> tuple:
        x0, y0, x1, y1 = self.local
        return (x0 + self.dx[0], y0 + self.dy, x1 + self.dx[-1], y1 + self.dy)

    def all_homes(self) -> dict:
        return {f"{self.tag}F{f}.v{k}.{b}": (x + self.dx[f], y + self.dy) for f in range(self.n)
                for k, v in enumerate(self.variants) for b, (x, y) in v["homes"].items()}

    def sites_at(self, k: int) -> dict:
        """The tiles the k-th state's nine sites are made on (`serve` fixed: its factory, and
        which set of sites, the states before it on that factory say)."""
        f = self.serve(k)
        v = self.variants[sum(1 for i in range(k) if self.serve(i) == f) % len(self.variants)]
        return {f"{self.tag}t{k}s{i}": (v["homes"][f"cl{i}"][0] + self.dx[f], v["homes"][f"cl{i}"][1] + self.dy)
                for i in range(9)}

    def beneath(self, blocks, per: int, columns: int = 1) -> dict:
        """Homes for a program's blocks, `per` a column, `columns` columns under each factory
        (the first below its x site, the rest evenly after it), the rows between them a bus:
        with `serve(k) = k // columns` every consumer reaches its factory alike."""
        x = self.homes["cl0"][0] - self.local[0]
        y = self.dy + self.local[3] + 2
        step = self.w // columns
        return {b: (self.dx[0] + (j // per) * step + self.local[0] + x, y + 2 * (j % per))
                for j, b in enumerate(blocks)}

    def walls(self) -> set:
        x0, y0, x1, y1 = self.local
        row = self.homes["cl0"][1]
        return {(x + self.dx[f], y + self.dy) for f in range(self.n) for x in range(x0, x1 + 1)
                for y in range(y0, row)}

    def ports(self, stream) -> list:
        """`stream` with each `autoccz_h s0 .. s8` a `macro` line followed by its factory's lines,
        renamed: the nine sites its own (`t<k>s<i>`, the program's names after it too) and the
        factory's blocks and outcomes `F<k>.`-prefixed."""
        out, ren, k, tag = [], {}, 0, self.tag
        for w, a, o, g in stream:
            if w == "autoccz_h":
                ren = {s: f"{tag}t{k}s{i}" for i, s in enumerate(a)}
                name = lambda x, k=k: ren[a[int(x[2:])]] if re.fullmatch(r"cl\d", x) else f"{tag}F{k}.{x}"
                out.append(("macro", [k, tag], None, []))
                out += [(fw, [name(x) if re.fullmatch(r"c\w+", x) else x for x in fa], fo and name(fo),
                         [("!" if x[0] == "!" else "") + name(x.lstrip("!")) for x in fg])
                        for fw, fa, fo, fg in self.stream]
                k += 1
                continue
            out.append((w, [ren.get(x, x) for x in a], o, g))
        return out

    def _start(self, f, free_at) -> float:
        v = self.variants[self.turn[f]]
        held = max((free_at[s] for s in self.last[f][self.turn[f]]), default=0.0)
        return max(self.free[f], held - v["row_from"], 0.0)

    def macro(self, line, args, free_at, board):
        """The factory copy for the k-th state, at `line` of the ported stream (see `schedule`)."""
        from qccd.fullstack.scale.lattice import shifted
        k = args[0]
        f = self.serve(k) if self.serve else min(range(self.n), key=lambda i: self._start(i, free_at))
        dt, turn = self._start(f, free_at), self.turn[f]
        v = self.variants[turn]
        sites = {f"cl{i}": f"{self.tag}t{k}s{i}" for i in range(9)}
        name = lambda x: sites.get(x, f"{self.tag}F{k}.{x}")
        while True:
            gs = [shifted(g, self.dx[f], self.dy, line + 1, name, dt) for g in v["gadgets"]]
            busy = [(t, g.t0, g.t1) for g in gs for t in g.tiles] + \
                   [((x + self.dx[f], y + self.dy), a + dt, z + dt) for (x, y), a, z, _ in v["inner"]]
            clash = [e for t, a, z in busy for s, e, _ in board.busy[t] if s < z - 1e-6 and e > a + 1e-6]
            if not clash:
                break
            if min(clash) == float("inf"):
                raise ValueError(f"line {line}: a block lives on factory {f}'s tiles for good")
            dt = max(dt, min(clash))
        self.free[f] = dt + v["strips_done"]
        self.last[f][turn] = tuple(sites.values())
        self.turn[f] = (turn + 1) % len(self.variants)
        self.served.append((line, f, dt))
        at = {s: (v["homes"][c][0] + self.dx[f], v["homes"][c][1] + self.dy) for c, s in sites.items()}
        return gs, len(self.stream), at, {s: dt + v["made"][c] for c, s in sites.items()}, {}


# ---- a state vector over named qubits ----------------------------------------------------------

_H = np.array([[1, 1], [1, -1]]) / np.sqrt(2)
_Z = np.diag([1, -1])
_X = np.array([[0, 1], [1, 0]])
_STATES = {"X": [1, 1], "Z": [1, 0], "Y": [1, 1j], "T": [1, np.exp(1j * np.pi / 4)]}


class _SV:
    def __init__(self):
        self.names: list = []
        self.psi = np.ones(1, dtype=complex)

    def add(self, name, vec):
        self.names.append(name)
        self.psi = np.kron(self.psi, np.asarray(vec, dtype=complex) / np.linalg.norm(vec))

    def gate(self, name, U):
        n, ax = len(self.names), self.names.index(name)
        t = np.moveaxis(np.tensordot(U, self.psi.reshape([2] * n), axes=([1], [ax])), 0, ax)
        self.psi = t.reshape(-1)

    def measure_z(self, names, rng) -> int:
        n, idx = len(self.names), np.arange(2 ** len(self.names))
        par = np.zeros_like(idx)
        for nm in names:
            par ^= (idx >> (n - 1 - self.names.index(nm))) & 1
        out = int(rng.random() < float(np.sum(np.abs(self.psi[par == 1]) ** 2)))
        self.psi = np.where(par == out, self.psi, 0)
        self.psi /= np.linalg.norm(self.psi)
        return out

    def drop(self, name):
        """Remove a qubit in a product state with the rest (measured, or never touched)."""
        n, ax = len(self.names), self.names.index(name)
        t = np.moveaxis(self.psi.reshape([2] * n), ax, 0)
        v = t[0] if np.linalg.norm(t[0]) > 1e-9 else t[1]
        self.names.pop(ax)
        self.psi = v.reshape(-1) / np.linalg.norm(v)


def run(stream, rng, faults=(), sv=None) -> tuple:
    """(state vector, outcomes): the stream executed, outcomes sampled, Pauli frames applied as
    gates, `ht` as H; a guarded instruction runs only when its guard holds (a block whose
    readout is skipped and that nothing touched is an unused ancilla, and is discarded).
    `faults`: indices of `prep ... T` lines whose magic state is wrong by Z.  `sv`: a state to
    start from (it is changed)."""
    sv, out = sv or _SV(), {}
    touched = set(sv.names)
    for k, (w, a, o, g) in enumerate(stream):
        # a guard is a flattened nest of conditions: an outcome never made (its readout's own
        # guard failed) means an outer condition failed, so the whole guard does
        if g and not all(x.lstrip("!") in out and (out[x[1:]] == 0 if x[0] == "!" else out[x]) for x in g):
            if w == "read" and a[0] not in touched and a[0] in sv.names:
                sv.drop(a[0])
            if w != "prep":
                continue
        if w == "ppm":
            touched.update(a[1::2])
        if w == "prep":
            sv.add(a[0], _STATES[a[1]])
            if k in faults:
                sv.gate(a[0], _Z)
        elif w == "ppm":
            xs = [a[i + 1] for i in range(0, len(a), 2) if a[i] == "X"]
            for b in xs:
                sv.gate(b, _H)
            out[o] = sv.measure_z(a[1::2], rng)
            for b in xs:
                sv.gate(b, _H)
        elif w == "read":
            if a[1] == "X":
                sv.gate(a[0], _H)
            out[o] = sv.measure_z([a[0]], rng)
            sv.drop(a[0])
        elif w in ("x", "z", "ht"):
            sv.gate(a[0], {"x": _X, "z": _Z, "ht": _H}[w])
        else:
            raise ValueError(f"line {k}: {w} is not run here")
    return sv, out


def fault_census(trials: int = 200, seed: int = 1) -> dict:
    """The 15-to-1 by state vector: {"accepted": of `trials` faultless runs, "fidelity": the worst
    accepted output's with T†|+>, "caught": single magic-state faults always rejected (of 15)}."""
    stream, q = distill_15to1()
    rng = random.Random(seed)
    want = np.array([1, np.exp(-1j * np.pi / 4)]) / np.sqrt(2)
    checks = [f"fr{i}" for i in range(4)]
    acc, worst = 0, 1.0
    for _ in range(trials):
        sv, out = run(stream, rng)
        if not any(out[c] for c in checks):
            acc += 1
            worst = min(worst, abs(np.vdot(want, sv.psi)) ** 2)
    mags = [k for k, (w, a, _, _) in enumerate(stream) if w == "prep" and a[1] == "T"]
    caught = sum(all(any(run(stream, rng, (k,))[1][c] for c in checks) for _ in range(10)) for k in mags)
    return {"accepted": acc, "fidelity": worst, "caught": caught}


def _sites(sv: _SV, sites) -> np.ndarray:
    """The state on `sites` (all that is left), in their order, the `TURNED` ones turned back."""
    if sorted(sv.names) != sorted(sites):
        raise ValueError(f"left on the state: {sv.names}")
    for i in TURNED:
        sv.gate(sites[i], _H)
    t = sv.psi.reshape([2] * len(sites))
    return np.transpose(t, [sv.names.index(b) for b in sites]).reshape(-1)


def ccz_census(trials: int = 50, seed: int = 1, full: bool = False) -> dict:
    """The |AutoCCZ> stage by state vector -- on injected T, or (`full`) the whole factory with its
    eight 15-to-1s: {"accepted": of `trials` faultless runs, "fidelity": the worst accepted
    output's with FormalRV's |AutoCCZ> (`qccd.gadget.leaves.ccz.autoccz_vector`), "caught": T
    faults always rejected, of "mags" (on injected T only)}."""
    from qccd.gadget.leaves.ccz import autoccz_vector
    stream, blocks = ccz_factory() if full else autoccz()
    checks = ["cr"] + ([f"ct{j}r{i}" for j in range(8) for i in range(4)] if full else [])
    want = autoccz_vector()
    rng = random.Random(seed)
    acc, worst = 0, 1.0
    for _ in range(trials):
        sv, out = run(stream, rng)
        if not any(out[c] for c in checks):
            acc += 1
            worst = min(worst, abs(np.vdot(want, _sites(sv, blocks[-9:]))) ** 2)
    mags = [] if full else [k for k, (w, a, _, _) in enumerate(stream) if w == "prep" and a[1] == "T"]
    caught = sum(all(run(stream, rng, (k,))[1]["cr"] for _ in range(10)) for k in mags)
    return {"accepted": acc, "fidelity": worst, "caught": caught, "mags": len(mags)}
