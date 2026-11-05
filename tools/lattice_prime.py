"""One prime of Gidney 2025 (`gOnePrime`), whole, on the stationary-tile machine
(docs/FULLSTACK_PLAN.md, slice S9 step 5): every one of its steps -- gLoop1's 214 iterations,
gLoop2's and gUnloop2's 11, gLoop3's 35 and gUnloop3's 70, gLoop4's 5, and the work between them
-- on one floorplan, scheduled and checked, the steps end to end.

The floorplan (`Machine`), a widening of `lattice_loop1.py`'s:
* three farms of narrow CCZ factories in a row (`factory.Farm`), one for each register an adder
  works on -- dlog (31 factories), help (22), acc (34) -- the register's bit triples beneath
  them, bit j's three blocks under factory j (`Farm.beneath`), so every adder is laid out as the
  32-bit adder whose shapes were checked; res in a second band of triples under help's;
* under the dlog farm at its left the crossbar: a column of seats, the 64 product blocks pp[S] in
  a row below them, every other column;  below pp three lanes, addr, two lanes, the exponent
  register (m = 1280 blocks) in rows across the machine, a lane under each row;
* left of the crossbar a fourth farm (29 narrow factories) for powerProduct.

What each operation of a step becomes:
* a lookup's write (pp[S] into a register's every third block): the written blocks taken to the
  seats -- made there when they were last reset (a reset waits for the block's next use, and is
  made where that use is), else carried (`relay.carry`) -- the CNOTs' ancillas where pp[S]'s
  column meets a seat's row (`relay.crossings`), the products relay chains; the blocks carried
  home before their next use elsewhere;
* an adder: its states from its register's farm (factory i for bit i: `Farm.ports`), its CZs by
  pairs made ahead (`czpair`), its ancillas placed by the scheduler -- but one whose partners sit
  more than a corridor apart (a conditional add's control bit) brought beside each in turn
  (`relay.bring`);
* powerProduct: its states from the fourth farm, brought to pp and addr;
* everything else (the loads into addr, the releases, the swaps between help and res, the small
  lookups, readouts, frames): every ancilla brought beside its partners;
* `ppm P q -> m; if m: (flip) q` on one block -- a measurement and reset -- is `read q P -> m;
  prep q P` (`one_patch_proved`); alone, the patch kept, it is a merge with a fresh patch made
  in its basis (`one_patch_kept_proved`).
Every product is then a relay chain where its patches share a row or column (`relay.relay`).

Steps run end to end: each is scheduled from the board at rest -- every program block at home,
every outcome measured before it decoded (`schedule(known=)`) -- and the next starts when its last
gadget ends and that outcome is decoded.  So the prime's time is the sum of its steps' times,
each a schedule of checked programs (`lattice.check` on every step): concrete.  A prime begins
with the resets the prime before left waiting (`resets_left`: every prime is the same).  A step
whose lines are an earlier step's up to names (`signature`) has its schedule (the scheduler is
deterministic).  Each distinct program (`Gadget.shape`) is listed, to be checked at d = 11.

`--rest`: the same for everything the program does outside its primes -- the H̄ on the
exponent, the tail after the primes (gLoop1 once more, and its small lookups), the phase
gradient, the two QFT measurements (1,278 adds) -- from every block reset.  gGradientPrep's 32
small-angle rotations are left out (counted): the machine has no gadget for their resource
states yet.

    python tools/lattice_prime.py [--steps A:B] [--workers N] [--out DIR] [--rest] [--no-pairs]
                                  [--keep] [--cold]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pickle
import sys
import threading
import time
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

#: factories a farm: the most states one adder on that register takes (dlog's 32-bit add 31,
#: help's comparison 22, acc's 34), and powerProduct's
FARMS = {"D": 31, "H": 22, "A": 34, "P": 29}
#: a product whose partners sit further apart than this (tiles) is brought, not routed
FAR = 19
#: calls whose bodies are steps of their own; calls walked into for the steps inside them
STEPS = {"gLoop2Step", "gLoop3Step", "gLoop4Step", "gUnloop3Step", "gUnloop2Step"}
WALK = {"gLoop1", "gLoop2", "gLoop3", "gLoop4", "gUnloop3", "gUnloop2", "gUnloop3Rounds", "gUnloop3Round",
        "gUnloop3HalfA", "gUnloop3HalfB"}
#: calls taken apart into their parts (each part its own operation)
SPLIT = {"lookupPP"}
#: loops whose every iteration is a step: (the call they sit in, their variable)
STEP_LOOPS = {("gLoop1", "j"), ("tailAfterPrimes", "j"), ("gQftMeasure", "u")}
#: what the program does outside its primes, walked for `--rest`
REST_WALK = {"gApproxModexp", "tailAfterPrimes", "gQftMeasure", "gGradientPrep"}


# ---- the steps of one prime -------------------------------------------------------------------

def _expander(prog):
    from qccd.fullstack.scale import program as P

    class Exp(P._Exp):
        """`program._Exp` that keeps no record table (Gidney 2025 reads no record by address:
        `codesign.records_read`) and reads a source slot nothing has written yet as 0 (as
        `concrete.Writer` does: the one such slot a prime reads).  A small-angle rotation
        (gGradientPrep's) it leaves out, counted in `rotations`: the machine has no gadget for
        its resource state yet."""
        unwritten = 0
        rotations: list = []

        def rotation(self, fs, k, sign, guard):
            self.rotations.append(k)

        def resolve(self, ref, env):
            if isinstance(ref, P._Rec) and ref.table == "src" and ref.idx(env) not in self.sources:
                self.unwritten += 1
                return []
            return super().resolve(ref, env)

    class Discard(dict):
        def __setitem__(self, k, v):
            pass

    ex = Exp(prog, reserved=True)
    ex.records = Discard()
    return ex


def prime_steps(prog, part: str = "prime"):
    """Yield (label, [(operation, lines)]) for every step of one prime (`part` "prime"), or of
    everything the program does outside its primes ("rest": a ("@primes", None) where the
    primes run), in program order, each line (word, args, outcome, guard) as `program._Exp`
    writes it, every slot taken."""
    from qccd.fullstack.scale import program as P
    walk_into = WALK | (REST_WALK if part == "rest" else set())
    found = []

    def find(body, env, d):
        for node in body:
            if found:
                return
            if isinstance(node, P.Call):
                if node.gadget == "gOnePrime":
                    found.append((node, env))
                    return
                if d < 8:
                    find(prog.body_of(node, env), {**prog.params, **P.bind(node.skeys + node.args, env)}, d + 1)
            elif isinstance(node, P.Loop) and node.hi(env) > node.lo(env):
                find(node.body, {**env, node.var: node.lo(env)}, d)
            elif isinstance(node, P.Let):
                find(node.body, {**env, node.var: node.value(env)}, d)
    if part == "prime":
        find(prog.root, dict(prog.params), 0)
        node, env = found[0]
    ex = _expander(prog)
    cur: list = []

    def name_of(nd):
        if isinstance(nd, P.Call):
            return nd.gadget
        if isinstance(nd, P.Loop):
            return f"for {nd.var}"
        if isinstance(nd, P.When):
            return "when " + "+".join(name_of(c) for c in nd.body)
        return type(nd).__name__

    def emit(label, nd, env, guard, sid, pos):
        if isinstance(nd, P.Call) and nd.gadget in SPLIT:
            e2 = {**prog.params, **P.bind(nd.skeys + nd.args, env)}
            b = prog.body_of(nd, env)
            for i, c in enumerate(b):
                emit(f"{label}/{name_of(c)}", c, e2, guard, b.sid, i)
            return
        if isinstance(nd, P.When) and any(isinstance(c, P.Call) and c.gadget in SPLIT for c in nd.body):
            f = ex.formula(nd.cond, env)
            if len(f) != 1:
                raise ValueError(f"a guarded body (line {nd.qline}) under {len(f)} terms")
            g2 = tuple(guard) + tuple(P._guard_text(p) for p in f[0])
            for i, c in enumerate(nd.body):
                emit(f"{label}/{name_of(c)}", c, env, g2, nd.body.sid, i)
            return
        start = len(ex.out)
        if isinstance(nd, P.Seg):              # its outcomes are known by (body, position)
            ex.seg(nd, sid, pos, env, guard)
        elif isinstance(nd, P.Cat):
            ex.cat(nd, sid, pos, env, guard)
        else:
            part = P.Body([nd])
            part.sid = sid
            ex.body(part, env, guard)
        lines = [ln for ln in ex.out[start:] if ln[0] not in ("se", "decode")]
        del ex.out[start:]
        if lines:
            cur.append((label, lines))

    def walk(body, env, guard, path, sid):
        nonlocal cur
        for pos, nd in enumerate(body):
            if isinstance(nd, P.Call) and nd.gadget in STEPS:
                if cur:
                    yield path + "/between", cur
                    cur = []
                e2 = {**prog.params, **P.bind(nd.skeys + nd.args, env)}
                b = prog.body_of(nd, env)
                for i, c in enumerate(b):
                    emit(name_of(c), c, e2, guard, b.sid, i)
                yield path + "/" + nd.gadget, cur
                cur = []
            elif part == "rest" and isinstance(nd, P.Call) and nd.gadget == "gPrimes":
                if cur:
                    yield path + "/before primes", cur
                    cur = []
                yield "@primes", None
            elif isinstance(nd, P.Call) and nd.gadget in walk_into:
                e2 = {**prog.params, **P.bind(nd.skeys + nd.args, env)}
                b = prog.body_of(nd, env)
                yield from walk(b, e2, guard, path + "/" + nd.gadget, b.sid)
                if cur:
                    yield path + "/" + nd.gadget + "/end", cur
                    cur = []
            elif isinstance(nd, P.Loop) and (path.rsplit("/", 1)[-1], nd.var) in STEP_LOOPS:
                for v in range(nd.lo(env), nd.hi(env)):
                    for i, c in enumerate(nd.body):
                        emit(name_of(c), c, {**env, nd.var: v}, guard, nd.body.sid, i)
                    yield path + f"/iter{v}", cur
                    cur = []
            elif isinstance(nd, P.Loop) and any(isinstance(c, P.Loop) or isinstance(c, P.Call) and
                                                (c.gadget in STEPS or c.gadget in walk_into) for c in nd.body):
                for v in range(nd.lo(env), nd.hi(env)):
                    yield from walk(nd.body, {**env, nd.var: v}, guard, path + f"/{nd.var}{v}", nd.body.sid)
            else:
                emit(name_of(nd), nd, env, guard, sid, pos)

    if part == "rest":
        yield from walk(prog.root, dict(prog.params), (), "root", prog.root.sid)
        if cur:
            yield "root/end", cur
        prime_steps.rotations = list(ex.rotations)
        return
    b = prog.body_of(node, env)
    yield from walk(b, {**prog.params, **P.bind(node.skeys + node.args, env)}, (), "gOnePrime", b.sid)
    if cur:
        yield "gOnePrime/end", cur


# ---- a measurement and reset on one patch -----------------------------------------------------

@lru_cache(maxsize=None)
def one_patch_proved(letter: str) -> bool:
    """`ppm P p0 -> o0; if o0: (X for Z, Z for X) p0` is `read p0 P -> o0; prep p0 P`, the
    outcome kept (`codesign.equal_drop`)."""
    from qccd.fullstack.scale.codesign import equal_drop
    from qccd.fullstack.scale.program import Template
    flip = "x" if letter == "Z" else "z"
    a = Template("mz", (f"ppm {letter} p0 -> o0", f"if o0: {flip} p0"), (0, 0), 1, 0, 1, 0)
    b = Template("rp", (f"read p0 {letter} -> o0", f"prep p0 {letter}"), (0, 0), 1, 0, 1, 0)
    return equal_drop(a, b)


@lru_cache(maxsize=None)
def one_patch_kept_proved(letter: str) -> bool:
    """`ppm P p0 -> o0` alone (the patch kept) is `prep l0 P; ppm P p0 P l0 -> o0; read l0 P`,
    the ancilla's outcome forgotten (`codesign.equal_drop`)."""
    from qccd.fullstack.scale.codesign import equal_drop
    from qccd.fullstack.scale.program import Template
    a = Template("anc", (f"prep l0 {letter}", f"ppm {letter} p0 {letter} l0 -> o0", f"read l0 {letter} -> o1"),
                 (0, 0, 0), 1, 1, 2, 0)
    b = Template("m", (f"ppm {letter} p0 -> o0",), (0,), 1, 0, 1, 0)
    return equal_drop(a, b)


def one_patch(lines):
    """Every one-block product: followed (next on that block) by the flip its outcome guards, a
    readout and a reset; otherwise the same product with a fresh patch made in its basis."""
    out, skip = [], set()
    for k, (w, a, o, g) in enumerate(lines):
        if k in skip:
            continue
        if w == "ppm" and len(a) == 2:
            q, flip = a[1], "x" if a[0] == "Z" else "z"
            nxt = next((j for j in range(k + 1, len(lines)) if q in _blocks_of(lines[j])), None)
            if nxt is not None and lines[nxt][:2] == (flip, (q,)) and tuple(lines[nxt][3]) == tuple(sorted(set(g) | {o})):
                assert one_patch_proved(a[0])
                out += [("read", (q, a[0]), o, g), ("prep", (q, a[0]), None, g)]
                skip.add(nxt)
            else:
                assert one_patch_kept_proved(a[0])
                anc = f"{o}.a"
                out += [("prep", (anc, a[0]), None, ()), ("ppm", (a[0], q, a[0], anc), o, g),
                        ("read", (anc, a[0]), f"{o}.a", g)]
            continue
        out.append((w, a, o, g))
    return out


def _blocks_of(line):
    w, a = line[0], line[1]
    if w == "ppm":
        return a[1::2]
    if w in ("prep", "read", "ht", "x", "y", "z"):
        return a[:1]
    return ()


# ---- the floorplan ----------------------------------------------------------------------------

class Machine:
    """Homes, seats, lanes and farms for one prime (see the module docstring)."""

    def __init__(self, params):
        from qccd.fullstack.scale.factory import Farm
        self.params = params
        base = {k: params[f"{k}Base"] for k in ("dlog", "acc", "res", "help", "addr", "pp", "exp")}
        ends = {"exp": params["m"], "dlog": base["acc"], "acc": base["res"], "res": base["help"],
                "help": base["addr"], "addr": base["pp"], "pp": params["top"]}
        self.reg = {k: [f"q{i}" for i in range(base[k], ends[k])] for k in base}
        self.reg_of = {b: k for k, bs in self.reg.items() for b in bs}
        self.index = {b: i for bs in self.reg.values() for i, b in enumerate(bs)}
        homes = {}
        self.farms = {}
        x0 = 0
        for tag, regname in (("D", "dlog"), ("H", "help"), ("A", "acc")):
            n = FARMS[tag]
            f = Farm(n, x0=x0, y=0, serve=lambda k, n=n: k % n, strips=4, tag=tag)
            self.farms[tag] = f
            homes.update(f.beneath(self.reg[regname], 3, 1))
            if tag == "H":                      # res: a second band of triples under help's
                hb = f.beneath(self.reg["help"], 3, 1)
                y0 = min(y for _, y in hb.values())
                cols = sorted({x for x, _ in hb.values()})
                homes.update({b: (cols[j // 3], y0 + 6 + 2 * (j % 3)) for j, b in enumerate(self.reg["res"])})
            x0 = max(x for x, _ in homes.values()) + 3
        fb = self.farms["D"].bounds()
        self.x_c = x_c = fb[0] + 1
        self.y_s = y_s = max(y for _, y in homes.values()) + 2
        self.n_seats = max(len(self.reg[r]) // 3 + 1 for r in ("dlog", "help", "acc", "res"))
        self.y_pp = y_pp = y_s + self.n_seats
        y_addr, y_exp = y_pp + 5, y_pp + 9
        homes.update({b: (x_c + 2 * s, y_pp) for s, b in enumerate(self.reg["pp"])})
        homes.update({b: (x_c + 2 * i, y_addr) for i, b in enumerate(self.reg["addr"])})
        n = FARMS["P"]
        pf = Farm(n, x0=0, y=y_pp - 2, serve=lambda k: k % n, strips=4, tag="P")
        shift = x_c - 3 - pf.bounds()[2]
        self.farms["P"] = Farm(n, x0=shift, y=y_pp - 2, serve=lambda k: k % n, strips=4, tag="P")
        pb = self.farms["P"].bounds()
        x_right = max(f.bounds()[2] for f in self.farms.values())
        xs = range(pb[0] + (pb[0] - x_c) % 2, x_right + 1, 2)
        m, w1 = params["m"], params["w1"]
        rows = -(-m // len(xs))
        slots = sorted(((x, y_exp + 2 * r) for x in xs for r in range(rows)),
                       key=lambda t: (abs(t[0] - x_c - w1), t[1], t[0]))
        homes.update({b: slots[k] for k, b in enumerate(self.reg["exp"])})
        self.homes = homes
        self.lanes = [("y", y_pp + 2), ("y", y_pp + 3), ("y", y_addr + 2)] + \
                     [("y", y_exp + 2 * r + 1) for r in range(rows)]
        self.seats = [(x_c - 1, y_s + j) for j in range(self.n_seats)]
        self.farm_homes = {}
        for f in self.farms.values():
            self.farm_homes.update(f.all_homes())
        self.walls = set().union(*(f.walls() for f in self.farms.values()))
        self.blocked = self.walls | set(self.farm_homes.values()) | set(self.seats)
        pts = list(homes.values()) + self.seats + list(self.farm_homes.values())
        self.bounds = (min(min(x for x, _ in pts) - 1, pb[0]), min(f.bounds()[1] for f in self.farms.values()),
                       max(x_right, max(x for x, _ in pts) + 1), max(y for _, y in pts) + 1)
        self.tiles = (self.bounds[2] - self.bounds[0] + 1) * (self.bounds[3] - self.bounds[1] + 1)

    def reset(self):
        """Every farm idle again (a step starts on the board at rest)."""
        for f in self.farms.values():
            f.free = [0.0] * f.n
            f.last = [[()] * len(f.variants) for _ in range(f.n)]
            f.turn = [0] * f.n
            f.served = []

    def seat_of(self, b) -> tuple:
        return self.seats[self.index[b] // 3]

    def route_out(self, b) -> list:
        """Corners from block b's home to its seat: one step right, along that column to the
        seat's row, along the row to the seat."""
        (hx, hy), s = self.homes[b], self.seat_of(b)
        return [(hx, hy), (hx + 1, hy), (hx + 1, s[1]), s]


# ---- one step on the floorplan ----------------------------------------------------------------

def _prog(b):
    return isinstance(b, str) and b.startswith("q")


def _far(M, stream, k, blk, at):
    """Whether the fresh block made at line k meets, before its readout, blocks at home further
    apart than a corridor reaches."""
    from qccd.fullstack.scale.relay import _life
    tiles = set()
    for j in _life(stream, k, blk):
        w, a, _, _ = stream[j]
        if w == "ppm":
            tiles |= {at[b] for b in a[1::2] if b != blk and b in at}
    tiles = list(tiles)
    return any(abs(p[0] - q[0]) + abs(p[1] - q[1]) > FAR for i, p in enumerate(tiles) for q in tiles[i + 1:])


class Translator:
    """A prime's steps, one after another, as lines for the lattice (see the module docstring).
    It carries from step to step the resets waiting for their block's next use."""

    def __init__(self, M: Machine, pairs: bool = True, pair_lookups: bool = False):
        self.M, self.pairs, self.pair_lookups = M, pairs, pair_lookups
        self.pending: dict = {}           # block -> basis of a reset not made yet
        self.n = 0                        # names made here

    def fresh(self, p):
        self.n += 1
        return f"{p}{self.n}"

    def step(self, ops, tag):
        """(lines, pins, known outcomes, {operation kind: count}) for one step."""
        from qccd.fullstack.scale import czpair
        from qccd.fullstack.scale import relay as R
        M = self.M
        M.reset()
        out, pins = [], {}
        loc: dict = {}                    # block -> its name on a seat, while it is there
        kinds = Counter()
        starts = {t: 0 for t in M.farms}
        self.carries = 0

        def made(b, name, lines):
            """The reset waiting for b, made as `name`, and the frames after it."""
            basis, frames = self.pending.pop(b)
            lines.append(("prep", (name, basis), None, ()))
            lines += [(fw, (name,), None, fg) for fw, fg in frames]

        def to_seat(b, lines):
            if b in loc:
                return
            name = self.fresh(f"{b}@")
            pins[name] = M.seat_of(b)
            if b in self.pending:
                made(b, name, lines)
            else:
                self.carries += 1
                ls, p = R.carry(b, name, M.route_out(b), f"{tag}o{self.carries}")
                lines += ls
                pins.update(p)
            loc[b] = name

        def to_home(b, lines):
            if b in self.pending:
                made(b, b, lines)
            if b not in loc:
                return
            self.carries += 1
            ls, p = R.carry(loc.pop(b), b, M.route_out(b)[::-1], f"{tag}i{self.carries}")
            lines += ls
            pins.update(p)

        parts = []                        # (operation, its first line in `out`)
        for oi, (op, lines) in enumerate(ops):
            parts.append((op, len(out)))
            lines = one_patch([(w, tuple(a), o, tuple(g)) for w, a, o, g in lines])
            has_ccz = any(w == "autoccz_h" for w, *_ in lines)
            prod_regs = {M.reg_of[b] for w, a, *_ in lines if w == "ppm" for b in a[1::2] if _prog(b)}
            if op.split("/")[-1] == "for S" and "pp" in prod_regs:
                kind = "write"
            elif has_ccz and prod_regs & {"pp", "addr"}:
                kind = "pfarm"
            elif has_ccz:
                regs = prod_regs & {"dlog", "help", "acc"}
                if len(regs) != 1:
                    raise ValueError(f"{tag} {op}: an adder over {prod_regs}")
                kind = "adder:" + {"dlog": "D", "help": "H", "acc": "A"}[regs.pop()]
            else:
                kind = "bring"
            kinds[kind] += 1
            targets = {a[3] if not _prog(a[1]) else a[1] for w, a, *_ in lines
                       if w == "ppm" and a[0] == "X" and kind == "write" and any(_prog(b) for b in a[1::2])}
            # where each program block has to be: a write's targets on their seats (all taken
            # there before its first line: a carry's relays never meet its crossings), all else home
            body = []
            for t in sorted(targets, key=lambda b: M.index[b]):
                to_seat(t, body)
            for w, a, o, g in lines:
                if w == "prep" and _prog(a[0]):
                    if a[0] in loc:                # its old state is gone: the seat's patch read
                        body.append(("read", (loc.pop(a[0]), "Z"), self.fresh("drop"), ()))
                    self.pending[a[0]] = (a[1], [])
                    continue
                if w in ("x", "y", "z") and _prog(a[0]):   # a frame: classical, goes where its block is
                    if a[0] in self.pending:
                        self.pending[a[0]][1].append((w, g))
                    else:
                        body.append((w, (loc.get(a[0], a[0]),), o, g))
                    continue
                for b in [x for x in _blocks_of((w, a)) if _prog(x)]:
                    (to_seat if b in targets else to_home)(b, body)
                a = tuple(loc.get(x, x) if _prog(x) else x for x in a)
                body.append((w, a, o, g))
            homes_now = {**M.homes, **{loc[b]: pins[loc[b]] for b in loc}}
            if kind == "write":
                pins.update(R.crossings(body, homes_now))
                out += body
            elif kind == "pfarm":
                f = M.farms["P"]
                ported = f.ports(body, starts["P"])
                n_states = sum(1 for x in ported if x[0] == "macro")
                sites = {}
                for k in range(starts["P"], starts["P"] + n_states):
                    sites.update(f.sites_at(k))
                starts["P"] += n_states
                skip = {k: len(f.stream) for k, x in enumerate(ported) if x[0] == "macro"}
                b2, p = R.bring(ported, homes_now, sites, M.blocked, M.lanes, skip, f"{tag}b{oi}.")
                pins.update(p)
                out += b2
            elif kind.startswith("adder"):
                ftag = kind[-1]
                f = M.farms[ftag]
                st = -(-starts[ftag] // f.n) * f.n          # bit i's state from factory i
                ported = f.ports(body, st)
                starts[ftag] = st + sum(1 for x in ported if x[0] == "macro")
                if self.pairs:
                    ported, found = czpair.pairs(ported)
                    pins.update(czpair.place(found, {**homes_now, **M.farm_homes}, M.blocked))
                skip = {}
                for k, x in enumerate(ported):
                    if x[0] == "macro":
                        n = len(f.stream)
                        if [y[0] for y in ported[k + 1:k + 1 + n]] != [y[0] for y in f.stream]:
                            raise ValueError(f"{tag} {op}: a factory's lines were rewritten")
                        skip[k] = n
                at = homes_now
                b2, p = R.bring(ported, homes_now, {}, M.blocked, M.lanes, skip, f"{tag}b{oi}.",
                                only=lambda k, blk, s=ported: _far(M, s, k, blk, at))
                pins.update(p)
                out += b2
            else:
                paired = set()
                if self.pair_lookups and op.split("/")[-1].startswith("for m"):
                    # a small lookup's guarded CZs between pp blocks: by pairs made ahead
                    body, found = czpair.pairs(body)
                    pins.update(czpair.place(found, homes_now, M.blocked))
                    paired = {x for f in found for x in f[:2]}
                    kinds["paired CZ"] += len(found)
                b2, p = R.bring(body, homes_now, {}, M.blocked, M.lanes, None, f"{tag}b{oi}.",
                                only=(lambda k, blk: blk not in paired) if paired else None)
                pins.update(p)
                out += b2
        # the board at rest: every block home
        parts.append(("home again", len(out)))
        tail = []
        for b in list(loc):
            to_home(b, tail)
        out += tail
        made = {o for w, a, o, g in out if o}
        used = {x.lstrip("!") for w, a, o, g in out for x in g}
        from qccd.fullstack.scale.lattice import _names
        used |= {x for w, a, o, g in out if w == "bit" for x in _names(a[0])}
        return out, pins, sorted(used - made), dict(kinds), parts


def resets_left(prog) -> dict:
    """The resets a prime leaves waiting at its end (blocks reset and not used again in it), with
    the frames after them: every prime is the same, so each begins with them waiting -- the
    steady state the 21,640 primes run in."""
    pending: dict = {}
    for _, ops in prime_steps(prog):
        advance(pending, ops)
    return pending


def advance(pending: dict, ops) -> None:
    """`pending` (block -> (basis, frames)) after a step, as `Translator.step` leaves it."""
    for _, lines in ops:
        for w, a, o, g in one_patch([(w, tuple(a), o, tuple(g)) for w, a, o, g in lines]):
            if w == "prep" and _prog(a[0]):
                pending[a[0]] = (a[1], [])
            elif w in ("x", "y", "z") and _prog(a[0]):
                if a[0] in pending:
                    pending[a[0]][1].append((w, g))
            else:
                for b in _blocks_of((w, a)):
                    pending.pop(b, None)


# ---- scheduling one step ----------------------------------------------------------------------

def schedule_step(M: Machine, lines, pins, known):
    """(gadgets, relayed lines, check problems, its time, µs)."""
    from qccd.fullstack.scale import relay as R
    from qccd.fullstack.scale.lattice import Costs, check, schedule
    M.reset()
    relayed, rpins, src = R.relay(lines, {**M.homes, **pins})
    pins = {**pins, **rpins}
    allh = {**M.homes, **M.farm_homes}
    gadgets = schedule(relayed, allh, M.bounds, walls=M.walls,
                       macro=lambda k, args, fa, board: M.farms[args[1]].macro(k, args, fa, board),
                       pinned=pins, max_corridor=19, late=True, known=known)
    bad = check(relayed, gadgets, known=known)
    span = max(g.t1 for g in gadgets) + Costs().decode_us if gadgets else 0.0
    return gadgets, relayed, bad, span, src


_M = []


def _init(params):
    _M[:] = [Machine(params)]


def signature(lines, pins, known) -> str:
    """A step's lines with every name but a program block's renamed in order of first use, and
    where its pinned blocks sit: two steps with one signature are the same input to the
    scheduler, which is deterministic, so they have one schedule."""
    import hashlib
    from qccd.fullstack.scale.lattice import _names
    names: dict = {}

    def nm(x):
        if not isinstance(x, str) or _prog(x) or x in ("X", "Y", "Z", "T"):
            return x
        neg = x.startswith("!")
        x = x.lstrip("!")
        if x not in names:
            names[x] = len(names)
        return ("!" if neg else "") + f"#{names[x]}"
    h = hashlib.sha256()
    for k in known:
        nm(k)
    for w, a, o, g in lines:
        if w == "bit":
            a = (" ".join(nm(x) if x not in ("^", "&", "(", ")", "0", "1") else x
                          for x in a[0].replace("(", " ( ").replace(")", " ) ").split()),)
        h.update(repr((w, tuple(nm(x) for x in a), nm(o), tuple(nm(x) for x in g))).encode())
    h.update(repr(sorted((nm(b), t) for b, t in pins.items() if b in names)).encode())
    return h.hexdigest()


def _op_spans(parts, src, gadgets) -> list:
    """[operation, first start, last end (s)] for each operation of a step."""
    import bisect
    starts = [k for _, k in parts]
    lo, hi = [math.inf] * len(parts), [0.0] * len(parts)
    for g in gadgets:
        i = bisect.bisect_right(starts, src[g.line]) - 1
        lo[i], hi[i] = min(lo[i], g.t0), max(hi[i], g.t1)
    return [[op, round(a / 1e6, 3), round(b / 1e6, 3)] for (op, _), a, b in zip(parts, lo, hi) if b > 0]


#: a gadget in a frame file (`--frames`): its start (µs from the step's start, uint32), its
#: program (uint16: an index into the step's `shapes`), its tile count (uint8), then its tiles
#: (int16 x, y each); gadgets in order of start, the file gzipped
FRAME = "<IHB"


def write_frame(path, gadgets, index) -> None:
    import gzip
    import struct
    buf = bytearray()
    for g in sorted(gadgets, key=lambda g: (g.t0, g.line)):
        tiles = sorted(g.tiles)
        buf += struct.pack(FRAME, int(round(g.t0)), index[g.shape], len(tiles))
        buf += struct.pack(f"<{2 * len(tiles)}h", *[v for t in tiles for v in t])
    Path(path).write_bytes(gzip.compress(bytes(buf), 6))


def layout(M: Machine) -> dict:
    """The floorplan for a viewer: the bounds, each factory's walled inside and its sites, every
    register's homes in index order, the seats and the lanes."""
    factories = []
    for tag, f in M.farms.items():
        x0, y0, x1, _ = f.local
        row = f.homes["cl0"][1]
        for i in range(f.n):
            factories.append([tag, x0 + f.dx[i], y0 + f.dy, x1 + f.dx[i], row - 1 + f.dy])
    return {"bounds": list(M.bounds), "tiles": M.tiles, "factories": factories,
            "sites": sorted({tuple(t) for t in M.farm_homes.values()} - set(M.walls)),
            "registers": {r: [M.homes[b] for b in bs] for r, bs in M.reg.items()},
            "seats": M.seats, "lanes": [v for _, v in M.lanes]}


def worker(label, lines, pins, known, kinds, parts, out, keep=None, frame=None):
    """Schedule and check one step; its numbers and its distinct programs to `out` (JSON), with
    `keep` the schedule itself (pickled), with `frame` its gadgets packed for a viewer."""
    M = _M[0]
    t0 = time.time()
    try:
        gadgets, relayed, bad, span, src = schedule_step(M, lines, pins, known)
    except Exception as e:                       # the rest go on; this one is written down
        import traceback
        Path(out).with_suffix(".err").write_text(f"{label}\n{traceback.format_exc()}", encoding="utf-8")
        return f"{out} FAILED: {type(e).__name__}: {str(e)[:200]}"
    shapes = Counter(g.shape for g in gadgets)
    dur = {g.shape: round(g.t1 - g.t0) for g in gadgets}
    if keep:
        pickle.dump((relayed, gadgets, known), open(keep, "wb"))
    if frame:
        write_frame(frame, gadgets, {s: i for i, s in enumerate(shapes)})
    Path(out).write_text(json.dumps({
        "label": label, "kinds": kinds, "span_us": span, "instructions": len(relayed),
        "gadgets": len(gadgets), "by_kind": dict(Counter(g.kind for g in gadgets)),
        "check": bad[:20], "n_bad": len(bad), "seconds": round(time.time() - t0, 1),
        "ops": _op_spans(parts, src, gadgets),
        "shapes": [[list(s), c, dur[s]] for s, c in shapes.items()]}), encoding="utf-8")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", default="0:", help="A:B, the steps to schedule (all by default)")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--out", default=str(REPO / "out" / "lattice_prime"))
    ap.add_argument("--no-pairs", action="store_true")
    ap.add_argument("--keep", action="store_true", help="also keep each step's schedule")
    ap.add_argument("--frames", action="store_true", help="also each step's gadgets packed for a viewer")
    ap.add_argument("--cold", action="store_true", help="no resets waiting from a prime before")
    ap.add_argument("--rest", action="store_true", help="what the program does outside its primes")
    ap.add_argument("--pair-lookups", action="store_true", help="small lookups' CZs by pairs made ahead too")
    a = ap.parse_args()
    lo, hi = (a.steps.split(":") + [""])[:2]
    lo, hi = int(lo or 0), int(hi) if hi else None
    out = Path(a.out)
    (out / "steps").mkdir(parents=True, exist_ok=True)
    from lattice_lookup import rewritten
    from qccd.fullstack.scale import relay as R
    t0 = time.time()
    prog = rewritten()
    M = Machine(prog.params)
    _M.append(M)
    assert all(one_patch_proved(x) for x in "ZX") and R.move_proved("Z") and R.move_proved("X")
    print(f"machine: {M.tiles} tiles, bounds {M.bounds}; farms {FARMS}", flush=True)
    if a.frames:
        (out / "frames").mkdir(exist_ok=True)
        (out / "frames" / "layout.json").write_text(json.dumps(layout(M)), encoding="utf-8")
    T = Translator(M, pairs=not a.no_pairs, pair_lookups=a.pair_lookups)
    if a.rest:                                   # the program's start: every block reset
        T.pending = {f"q{i}": ("Z", []) for i in range(prog.n_data)}
    elif not a.cold:
        T.pending = resets_left(prog)
    print(f"{len(T.pending)} resets waiting from " + ("the program's start" if a.rest else "the prime before")
          + f" ({time.time() - t0:.0f} s)", flush=True)
    pool = None
    if a.workers > 1:
        from multiprocessing import Pool
        pool = Pool(a.workers, initializer=_init, initargs=(prog.params,))
    waiting = []
    first_of: dict = {}                          # signature -> the step scheduled for it
    same: list = []                              # (result, label, the earlier step's result)
    i = -1
    for label, ops in prime_steps(prog, "rest" if a.rest else "prime"):
        if label == "@primes":                   # the primes leave the state a prime leaves
            T.pending = resets_left(prog)
            continue
        i += 1
        if hi is not None and i >= hi:
            break
        res = out / "steps" / f"{i:03d}.json"
        if i < lo or res.exists():
            advance(T.pending, ops)
            continue
        try:
            lines, pins, known, kinds, parts = T.step(ops, f"s{i}.")
        except Exception as e:                   # written down; the steps after go on
            import traceback
            res.with_suffix(".err").write_text(f"{label}\n{traceback.format_exc()}", encoding="utf-8")
            print("FAILED to translate", res.name, label, f"{type(e).__name__}: {str(e)[:200]}", flush=True)
            continue
        sig = signature(lines, pins, known)
        if sig in first_of:                      # the same input as an earlier step: its schedule
            same.append((res, label, first_of[sig]))
            continue
        first_of[sig] = res
        job = (label, lines, pins, known, kinds, parts, str(res), str(out / "steps" / f"{i:03d}.pkl") if a.keep else None,
               str(out / "frames" / f"{i:03d}.bin.gz") if a.frames else None)
        if pool is None:
            print("done", worker(*job), label, flush=True)
            continue
        waiting.append((res.name, label, pool.apply_async(worker, job)))
        while len(waiting) >= 2 * a.workers:          # translate no further ahead than this
            for w in [w for w in waiting if w[2].ready()]:
                print("done", w[2].get(), w[1], flush=True)
                waiting.remove(w)
            time.sleep(0.5)
    for name, label, r in waiting:
        print("done", r.get(), label, flush=True)
    if pool:
        pool.close()
        pool.join()
    for res, label, orig in same:
        if orig.exists():
            r = json.loads(orig.read_text(encoding="utf-8"))
            r.update(label=label, same_as=orig.name)
            res.write_text(json.dumps(r), encoding="utf-8")
    print(f"{len(same)} steps the same input as an earlier one ({time.time() - t0:.0f} s)", flush=True)
    if a.rest:
        rot = getattr(prime_steps, "rotations", [])
        print(f"left out: {len(rot)} small-angle rotations (k = {sorted(set(rot))})", flush=True)
    summarize(out)


def summarize(out: Path):
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((out / "steps").glob("*.json"))]
    total = sum(r["span_us"] for r in rows)
    shapes = set()
    for r in rows:
        shapes |= {json.dumps(s) for s, *_ in r["shapes"]}
    bad = [r["label"] for r in rows if r["n_bad"]]
    by = defaultdict(lambda: [0, 0.0])
    import re
    for r in rows:
        k = re.sub(r"\d+", "#", r["label"])
        by[k][0] += 1
        by[k][1] += r["span_us"]
    print(f"{len(rows)} steps, {total / 1e6:.2f} s of machine time; checks failed in {len(bad)} {bad[:5]}")
    for k, (n, t) in by.items():
        print(f"  {n:4d} x {k:60s} {t / 1e6:10.2f} s  ({t / n / 1e6:.2f} s each)")
    print(f"{len(shapes)} distinct programs")
    (out / "shapes.json").write_text(json.dumps([json.loads(s) for s in sorted(shapes)]), encoding="utf-8")


if __name__ == "__main__":
    sys.setrecursionlimit(100000)
    threading.stack_size(256 * 1024 * 1024 - 4096)
    th = threading.Thread(target=main)
    th.start()
    th.join()
