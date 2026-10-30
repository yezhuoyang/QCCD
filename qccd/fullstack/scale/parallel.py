"""Shor at scale, layer four: the run's time when its independent work runs at once.

`program.duration_ns` runs every gadget instance after the one before it, on one district (only
FormalRV's `__par` loops overlap).  Here the run is a dataflow: every template instance starts
as soon as the blocks it touches are free and the outcomes it needs are recorded, each on a
district of its own -- as many districts as the run can use at once.

What an instance needs and gives is its template's PORTS, read off its compiled schedule
(`compile.compile_template`, relative to its start): when it first and last touches each block
it is passed, when it records each outcome, when it first needs an outcome measured before it.
An instance's schedule is rigid -- the compiled one, shifted -- so every time here is one a
real schedule meets.  A register-wide product (`program.Cat`) holds every block it names for
`cat_us(blocks, rounds)`; a rotation chain (`program.Rot`) holds its blocks for `rot_us`.

Blocks are FormalRV's: a data block by its index; a logical ancilla is a NEW block every time
it is prepared (FormalRV reuses ancilla indices; a fresh block from a preparation place is not
the old one), and local to the gadget call that names it.  Records are FormalRV's addresses
(`rv_recbase` + index), source slots their own.

A loop is run iteration by iteration until what each iteration writes repeats: each block and
record it writes moved by its own fixed stride, each time later by its own fixed slope (a
pipeline: an adder's carry, a lookup's register).  Then the rest is written in closed form --
and checked: an iteration in the middle and the one before last are run for real, and must be
what the form predicted, and the last is run for real (it may differ: a loop's end is often
special; it is the real one that is kept).  A form the middle iteration refutes (a register a tree filled, whose entries are ready
at no affine time) is undone, and the rest of the loop run for real.  Times are whole
nanoseconds, so a pattern is exact or it is not one.

What this does not model yet: moving a block between districts (transport between the
districts that use it), and how many districts the machine has -- the time is the one a
machine with a district for every instance that runs at once would take; `peak` bounds how
many that is.
"""
from __future__ import annotations

from .program import Call, Cat, Let, Loop, Pred, Program, Rot, Seg, When, _MXor, _Rec, bind

__all__ = ["parallel_us", "ParallelError"]


class ParallelError(ValueError):
    pass


#: iterations run for real before a loop's pattern is looked for; how many more to look in; and
#: the most a loop with no pattern is run for
CAP = 8
PATIENCE = 64
LIMIT = 50_000


class _Run:
    def __init__(self, prog: Program, ports: dict, rot_us, cat_us):
        # (every time in whole nanoseconds: the ports are given to the nanosecond)
        def ns(x):
            return None if x is None else round(x * 1000)
        self.ports = {tid: {"first": [ns(x) for x in q["first"]], "last": [ns(x) for x in q["last"]],
                            "out": [ns(x) for x in q["out"]], "span": ns(q["span"]),
                            "need": {int(j): ns(x) for j, x in q["need"].items()}}
                      for tid, q in ports.items()}
        self.prog = prog
        self.rot_us = lambda *a: round(rot_us(*a) * 1000)
        self.cat_us = lambda *a: round(cat_us(*a) * 1000)
        self.ready: dict = {}          # block key -> when it is free
        self.rec: dict = {}            # record address -> when recorded
        self.src: dict = {}            # source slot -> when written
        self.outs: dict = {}           # (body sid, node index) -> [when each outcome is recorded]
        self.gen: dict = {}            # scope -> {ancilla index: its generation}
        self.held: dict = {}           # scope -> the ancilla keys written in it
        self.scope = 0
        self.end = 0
        self.work = 0                  # district-nanoseconds: every instance's span, summed
        self.journal: list | None = None
        self.floor = 0                 # nothing inside a guarded body starts before its guard is known
        self.undo: list = []           # per closed form being checked: (table, key, old value)
        self.stats = {"segs": 0, "extrapolated": 0, "checked": 0}
        # records, and source slots, matter only if some template reads one by its address
        # (each table on its own: Gidney 2025 reads source slots and never a record)
        reads = [r for b in [prog.root, *prog.bodies.values()] for x in _segs(b) for r in x.ext]
        self.kept = {"r": any(isinstance(r, _Rec) and r.table == "rec" for r in reads),
                     "s": any(isinstance(r, _MXor) or (isinstance(r, _Rec) and r.table == "src") for r in reads)}
        # a closed form's records and source slots are not written one by one (Gidney 2025's
        # top loop would write 178 million): each strided key is ONE run, (first, count, time,
        # slope), read by arithmetic; a direct write and a run each carry when they were made,
        # and the later one is what a slot holds
        self.runs = {"r": {}, "s": {}}  # table -> stride -> residue -> [[first, count, t, slope, seq]]
        self.seqs = {"r": {}, "s": {}}  # table -> slot -> when it was written directly
        self.seq = 0

    # ---- state, journaled for loops -----------------------------------------------------------
    def put(self, table: str, key, value: float) -> None:
        if table in ("r", "s") and not self.kept[table]:
            return
        d = getattr(self, {"b": "ready", "r": "rec", "s": "src", "o": "outs"}[table])
        if table == "b" and key[0] == "a" and key not in d:
            self.held.setdefault(key[1], []).append(key)
        if self.undo:
            self.undo[-1].append((d, key, d.get(key, _MISSING)))
            if table in ("r", "s"):
                self.undo[-1].append((self.seqs[table], key, self.seqs[table].get(key, _MISSING)))
        d[key] = value
        if table in ("r", "s"):
            self.seqs[table][key] = self.seq
            self.seq += 1
        if self.journal is not None:
            self.journal.append((table, key, value))

    def key(self, fam, idx, env, scope, new=False):
        v = idx(env)
        if fam == "LogicData":
            return ("d", v)
        g = self.gen.setdefault(scope, {})
        if new:
            g[v] = g.get(v, -1) + 1
        return ("a", scope, v, g.get(v, 0))

    def slot(self, table: str, key) -> int:
        """When a record or source slot was written (0: before the run, or never)."""
        d = self.rec if table == "r" else self.src
        best, seq = d.get(key, 0), self.seqs[table].get(key, -1)
        for stride, by in self.runs[table].items():
            for first, n, t, slope, s in reversed(by.get(key % stride, ())):
                j = (key - first) // stride
                if 0 <= j < n and s > seq:
                    best, seq = t + slope * j, s
                    break
        return best

    def run(self, table: str, first, stride: int, n: int, t, slope) -> None:
        """Slots first, first + stride, ... (n of them) written at t, t + slope, ..."""
        if stride < 0:
            first, t, slope, stride = first + stride * (n - 1), t + slope * (n - 1), -slope, -stride
        lst = self.runs[table].setdefault(stride, {}).setdefault(first % stride, [])
        item = [first, n, t, slope, self.seq]
        self.seq += 1
        lst.append(item)
        if self.undo:
            self.undo[-1].append((_RUN, lst, item))

    def ext(self, ref, env) -> float:
        if isinstance(ref, _Rec):
            if ref.table == "src":
                return self.slot("s", ref.idx(env))
            return self.slot("r", env.get("rv_recbase", 0) + ref.idx(env))
        if isinstance(ref, _MXor):
            t = 0
            for v in range(ref.n(env)):
                t = max(t, self.slot("s", ref.idx({**env, ref.var: v})))
            return t
        bsid, bpos, k = ref
        return self.outs.get((bsid, bpos), [0] * (k + 1))[k]

    # ---- the tree ---------------------------------------------------------------------------
    def body(self, body, env: dict, scope: int) -> None:
        for i, node in enumerate(body):
            if isinstance(node, Seg):
                self.seg(node, body.sid, i, env, scope)
            elif isinstance(node, Loop):
                self.loop(node, env, scope)
            elif isinstance(node, Let):
                self.body(node.body, {**env, node.var: node.value(env)}, scope)
            elif isinstance(node, Pred):
                self.body(node.body, env, scope)           # a slot: reserved either way
            elif isinstance(node, When):
                # reserved either way, and not begun before the bit that decides it is recorded
                saved = self.floor
                self.floor = max(saved, max([self.ext(r, env) for t in node.cond.terms
                                             for _, rs in t for r in rs] + [0]))
                self.body(node.body, env, scope)
                self.floor = saved
            elif isinstance(node, Call):
                self.scope += 1
                sc = self.scope
                self.body(self.prog.body_of(node, env), {**self.prog.params, **bind(node.skeys + node.args, env)},
                          sc)
                # its ancillas are its own (FormalRV numbers them per gadget): nothing outside
                # can name them, so they are forgotten when it returns
                for k in self.held.pop(sc, ()):
                    self.ready.pop(k, None)
                self.gen.pop(sc, None)
            elif isinstance(node, Cat):
                self.cat(node, body.sid, i, env, scope)
            elif isinstance(node, Rot):
                keys = [self.key(fam, idx, env, scope) for _, fam, idx in node.factors]
                dur = self.rot_us(node.k(env), node.sign, len(node.factors))
                s = max([self.ready.get(k, 0) for k in keys] + [self.floor])
                for k in keys:
                    self.put("b", k, s + dur)
                self.end = max(self.end, s + dur)
                self.work += dur

    def seg(self, node: Seg, sid, pos, env, scope) -> None:
        t = self.prog.templates[node.tid]
        ext = [self.ext(r, env) for r in node.ext]
        base = env.get("rv_recbase", 0)
        if not t.lines:                                     # records the archive writes: no ion
            self.writes(node, env, ext, [], 0, base)
            return
        P = self.ports[node.tid]
        pk = [self.key(fam, idx, env, scope) for fam, idx in node.params]
        lk = [self.key(fam, idx, env, scope, new=True) for fam, idx in node.locals]
        s = max([self.ready.get(k, 0) - P["first"][i] for i, k in enumerate(pk) if P["first"][i] is not None]
                + [ext[int(j)] - need for j, need in P["need"].items()] + [self.floor])
        for i, k in enumerate(pk):
            if P["first"][i] is not None:
                self.put("b", k, s + P["last"][i])
        for k in lk:
            self.put("b", k, s + P["span"])
        outs = [s + o for o in P["out"]]
        self.put("o", (sid, pos), outs)
        for k, band in enumerate(node.bands):
            if band is not None:
                for a in range(base + band[0](env), base + band[1](env)):
                    self.put("r", a, outs[k])
        self.writes(node, env, ext, outs, s, base)
        self.end = max(self.end, s + P["span"])
        self.work += P["span"]
        self.stats["segs"] += 1

    def writes(self, node, env, ext, outs, s, base) -> None:
        import re
        for table, lo, hi, text in node.writes:
            ts = [outs[int(k)] for k in re.findall(r"\bo(\d+)\b", text)]
            ts += [ext[int(k)] for k in re.findall(r"\be(\d+)\b", text)]
            t = max(ts + [s])
            off = base if table == "rec" else 0
            for a in range(off + lo(env), off + hi(env)):
                self.put("r" if table == "rec" else "s", a, t)

    def cat(self, node: Cat, sid, pos, env, scope) -> None:
        keys = [self.key(fam, idx, env, scope) for _, fam, idx in node.factors]
        keys += [self.key(node.fam, node.idx, {**env, node.var: v}, scope)
                 for v in range(node.lo(env), node.hi(env))]          # every slot taken
        dur = self.cat_us(len(keys), node.rounds)
        s = max([self.ready.get(k, 0) for k in keys] + [self.floor])
        for k in keys:
            self.put("b", k, s + dur)
        self.put("o", (sid, pos), [s + dur])
        if node.band is not None:
            base = env.get("rv_recbase", 0)
            for a in range(base + node.band[0](env), base + node.band[1](env)):
                self.put("r", a, s + dur)
        self.end = max(self.end, s + dur)
        self.work += dur * len(keys)                       # a bench and a clinic per block

    # ---- loops: run until the pattern shows, then the closed form, checked ---------------------
    def iteration(self, node: Loop, env, scope, v) -> tuple:
        saved, self.journal = self.journal, []
        w0 = self.work
        self.body(node.body, {**env, node.var: v}, scope)
        got, self.journal = self.journal, saved
        if saved is not None:
            saved.extend(got)
        delta = {}
        for table, key, value in got:
            if table == "b" and key[0] == "a":
                continue                # a fresh ancilla of the iteration: nothing outside reads it
            delta[(table, key)] = value
        return delta, self.work - w0

    def loop(self, node: Loop, env, scope) -> None:
        lo, hi = node.lo(env), node.hi(env)
        n = hi - lo
        if n <= 0:
            return
        if n <= CAP + 4:
            for v in range(lo, hi):
                self.iteration(node, env, scope, v)
            return
        ds, ws = [], []
        form, v = None, lo
        while v < hi - 2 and v < lo + CAP + PATIENCE:
            d, w = self.iteration(node, env, scope, v)
            ds, ws = (ds + [d])[-3:], ws + [w]
            v += 1
            if v - lo >= CAP and (form := _pattern(ds)) is not None:
                break
        if form is None:
            if n > LIMIT:
                raise ParallelError(f"a loop of {n} iterations (line {node.qline}) whose iterations "
                                    f"do not repeat a pattern")
            for u in range(v, hi):
                self.iteration(node, env, scope, u)
            return
        done = v - 1                                        # the iteration `form` is anchored at
        if hi - 1 - done < 3:
            for u in range(v, hi):
                self.iteration(node, env, scope, u)
            return
        mid = done + 1 + (hi - 1 - done) // 2
        # checked in the middle and at the one before last (a pattern that breaks -- two regimes
        # crossing -- has broken by then), and the last run for real, which may differ
        for target in sorted({mid, hi - 2, hi - 1}):
            mark = (self.end, self.work, dict(self.stats),
                    len(self.journal) if self.journal is not None else None)
            self.undo.append([])
            # the closed form up to the iteration before `target` ...
            self.apply(form, target - done - 1)
            self.stats["extrapolated"] += max(0, target - done - 1)
            self.work += ws[-1] * max(0, target - done - 1)
            # ... and `target` for real, which must be what the form says (the last may differ:
            # a loop's end is often special, and it is the real one that stands)
            d, w = self.iteration(node, env, scope, target)
            self.stats["checked"] += 1
            log = self.undo.pop()
            if target != hi - 1 and not _same(d, _predict(form, target - done)):
                # refuted: everything since the anchor is undone, and the rest run for real
                for table, key, old in reversed(log):
                    if table is _RUN:
                        key.remove(old)                     # (a run: its list, itself)
                    elif old is _MISSING:
                        table.pop(key, None)
                    else:
                        table[key] = old
                self.end, self.work, self.stats, jl = mark
                if jl is not None:
                    del self.journal[jl:]
                self.stats["refuted"] = self.stats.get("refuted", 0) + 1
                if hi - done - 1 > LIMIT:
                    raise ParallelError(f"the loop at line {node.qline}: iteration {target} is not "
                                        f"what its pattern says, and {hi - done - 1} are too many to run")
                for u in range(done + 1, hi):
                    self.iteration(node, env, scope, u)
                return
            if self.undo:
                self.undo[-1].extend(log)          # an enclosing check may still undo it
            form, done = _advance(form, target - done), target

    def apply(self, form: list, last: int) -> None:
        """Iterations 1 .. `last` after the anchor, in closed form.  A key every iteration writes
        is written once, its last value (no read can fall between: these iterations are not run)."""
        if last < 1:
            return
        for (table, key), stride, val, slope in form:
            if table in ("r", "s") and stride and self.journal is None:
                if self.kept[table]:
                    self.run(table, key + stride, stride, last, val + slope, slope)
                self.end = max(self.end, _step(val, slope, 1), _step(val, slope, last))
                continue
            for j in (range(1, last + 1) if stride else (last,)):
                t = _step(val, slope, j)
                self.put(table, _shift((table, key), stride, j)[1], t)
            if table != "o":
                self.end = max(self.end, _step(val, slope, 1), _step(val, slope, last))


_MISSING = object()
_RUN = object()


# A loop's pattern: per key written by an iteration, (key, stride, value, slope) -- iteration
# j after the anchor writes the key moved by j strides, its time j slopes later.

def _segs(body):
    for n in body:
        if isinstance(n, Seg):
            yield n
        elif hasattr(n, "body"):
            yield from _segs(n.body)


def _name(tk) -> str:
    return "d" if tk[0] == "b" else tk[0]


def _index(tk):
    return tk[1][1] if tk[0] == "b" else (tk[1] if tk[0] in ("r", "s") else None)


def _shift(tk, stride: int, j: int):
    table, key = tk
    if table == "b":                                       # ("d", index)
        return (table, (key[0], key[1] + stride * j))
    if table in ("r", "s"):
        return (table, key + stride * j)
    return tk


def _step(val, slope, j: int):
    """A time (or an instance's outcome times) `j` slopes later."""
    if isinstance(val, list):
        return [x + s * j for x, s in zip(val, slope)]
    return val + slope * j


def _predict(form: list, j: int) -> dict:
    return {_shift(tk, st, j): _step(v, sl, j) for tk, st, v, sl in form}


def _advance(form: list, j: int) -> list:
    return [(_shift(tk, st, j), st, _step(v, sl, j), sl) for tk, st, v, sl in form]


def _same(a: dict, b: dict) -> bool:
    if a.keys() != b.keys():
        return False
    for k, v in a.items():
        w = b[k]
        if v != w:
            return False
    return True


def _pattern(ds: list):
    """The pattern of three consecutive iterations, anchored at the last, or None: every key
    the first writes, paired in order with one the second and one the third write, moved by
    the same stride both times, its time later by the same slope both times."""
    groups = []
    for d in ds:
        g: dict = {}
        for tk in d:
            g.setdefault(_name(tk), []).append(tk)
        for name, ks in g.items():
            ks.sort(key=(lambda t: _index(t)) if name != "o" else repr)
        groups.append(g)
    if not (groups[0].keys() == groups[1].keys() == groups[2].keys()):
        return None
    form = []
    for name in groups[0]:
        ka, kb, kc = (g[name] for g in groups)
        if not (len(ka) == len(kb) == len(kc)):
            return None
        for x, y, z in zip(ka, kb, kc):
            if name == "o":
                if not (x == y == z):
                    return None
                st = 0
            else:
                st = _index(y) - _index(x)
                if _index(z) - _index(y) != st:
                    return None
            va, vb, vc = ds[0][x], ds[1][y], ds[2][z]
            if isinstance(va, list):
                if not (len(va) == len(vb) == len(vc)):
                    return None
                sl = [q - p for p, q in zip(va, vb)]
                if any((r - q) != d for q, r, d in zip(vb, vc, sl)):
                    return None
            else:
                sl = vb - va
                if vc - vb != sl:
                    return None
            form.append((z, st, vc, sl))
    return form


def parallel_us(prog: Program, ports: dict, rot_us=None, cat_us=None) -> dict:
    """The run's time with every instance as early as its blocks and outcomes allow, each on a
    district of its own: `us` (microseconds), `work` (district-microseconds), and their ratio,
    the districts busy on average.  `ports[tid]`: `compile_template(...)["ports"]`."""
    def no_rot(*a):
        raise ParallelError("a rotation chain and no time for one")

    def no_cat(*a):
        raise ParallelError("a product over a register and no time for one")
    r = _Run(prog, ports, rot_us or no_rot, cat_us or no_cat)
    r.body(prog.root, dict(prog.params), 0)
    return {"ns": r.end, "us": r.end / 1000, "work_us": r.work / 1000,
            "busy": r.work / r.end if r.end else 0.0, **r.stats}
