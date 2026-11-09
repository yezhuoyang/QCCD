"""What limits FormalRV's Gidney 2025 listing on the ion machine (docs/FULLSTACK_PLAN.md, S10's design study): the numbers
behind the study, each rerun from the checked programs `tools/gidney_concrete.py` left in OUT.

    gidney_diagnose.py inside OUT [SHAPE...]    a program's time by kind of instruction
    gidney_diagnose.py path OUT [noseam|merge]  one prime's critical path on a flat schedule: its items, what links them
    gidney_diagnose.py bounds OUT               one prime with one constraint or program time changed at a time
    gidney_diagnose.py gaps                     one unchecked gap's logical failure at d = 11, sampled (stim, pymatching)
    gidney_diagnose.py failures OUT             the whole program's expected failures from its products' gaps

`bounds` builds nothing: each row removes a constraint or assumes a time, so it says what a design could give at most.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import sys
import time
from array import array
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.setrecursionlimit(100000)

PRIMES = 21640
#: a 61.03 ms layer of 11 rounds (`qccd.fullstack.scale.layer`, every rule and R10), by round
MERGE_ROUND_US = 61032.0 / 11
#: `gaps`, d = 11: (p of Z on every data ion, failures per gap); below the first, its (d + 1) / 2 = 6th power
GAP_CURVE = [(0.02, 4.7e-5), (0.03, 4.62e-4), (0.05, 6.95e-3), (0.1, 1.283e-1), (0.2, 4.642e-1), (0.5, 0.5)]


def _programs(out: Path):
    import gidney_concrete as GC
    from qccd.fullstack.scale import gidney_plan as P
    rows = json.loads((out / "shapes.json").read_text(encoding="utf-8"))
    times, cells = P.Times(), {}
    for text in rows:
        f = out / "programs" / (hashlib.sha1(text.encode()).hexdigest()[:12] + ".json")
        row = json.loads(f.read_text(encoding="utf-8"))
        shape = GC._shape(text)
        times[shape] = row["us"]
        cells[shape] = [tuple(c) for c in row["cells"]]
    return rows, times, cells


def inside(out: Path, shapes) -> None:
    import gidney_concrete as GC
    from qccd.fullstack.scale import catprog as K
    from qccd.verify.replay import replay
    rows = json.loads((out / "shapes.json").read_text(encoding="utf-8"))
    for text in shapes or sorted(rows, key=lambda k: -rows[k])[:8]:
        row = json.loads((out / "programs" / (hashlib.sha1(text.encode()).hexdigest()[:12] + ".json")).read_text(encoding="utf-8"))
        shape = GC._shape(text)
        b = (K.build_wide(shape[1], (shape[1] // 2, -1), mask=shape[2]) if shape[0] == "wide"
             else K.build_rep(shape[1], homes=row["homes"]))
        res = replay(K.cooled(b), b.arch, K._model(), check_rules=False)
        moves = [i for i in b.walker.prog.instructions if i.type == "simd" and i.cls == "shuttle"]
        print(f"{text}: {res.total_us:,.0f} us; {len(moves)} transport cycles, "
              f"{sum(len(i.participants) for i in moves) / max(1, len(moves)):.2f} ions a cycle")
        for k, v in sorted(res.us_by_class.items(), key=lambda kv: -kv[1]):
            print(f"   {k:10} {v:>10,.0f} us  {100 * v / res.total_us:5.1f} %")


class _Flat:
    """One instance inlined, every item as soon as its resources are free, each item's predecessor kept."""

    LINK = {"wire": "same wire or patch", "patch": "same wire or patch", "cold": "same wire or patch", "tile": "tile",
            "h": "seam along a row", "v": "seam down a column", "floor": "classical guard"}

    def __init__(self, plan, times, cells, merge=False, seam=True):
        self.plan, self.times, self.cells, self.merge, self.seam = plan, times, cells, merge, seam
        self.resolved: dict = {}
        self.kinds: dict = {}
        self.dur, self.kin, self.pre = array("d"), array("i"), array("q")
        self.link: list = []
        self.ready: dict = {}
        self.holder: dict = {}
        self.instances = 0

    def items(self, key):
        got = self.resolved.get(key)
        if got is None:
            got = self.resolved[key] = list(self.plan._resolve(key))
        return got

    def walk(self, key, floor0=0.0, floor_item=-1):
        from qccd.fullstack.scale import gidney_plan as P
        ready, holder = self.ready, self.holder
        known = latest = floor = floor0
        known_i = latest_i = floor_i = floor_item
        end, end_i = floor0, floor_item
        depth = 0
        # a request's patch is named within its instance: two instances' same names are two patches
        self.instances += 1
        me = self.instances

        def own(r):
            return r + (me,) if r[0] == "patch" else r

        def run(d, busy, kind):
            nonlocal end, end_i
            busy = [own(r) for r in busy]
            t, why, by = (floor, "floor", floor_i) if depth else (floor0, "floor", floor_item)
            for r in busy:
                v = ready.get(r, 0.0)
                if v > t:
                    t, why, by = v, r[0], holder.get(r, -1)
            if kind in ("merge", "rounds"):
                t = self.plan._on_clock(t)
            i = len(self.dur)
            self.dur.append(d)
            self.kin.append(self.kinds.setdefault(kind, len(self.kinds)))
            self.pre.append(by)
            self.link.append(why)
            e = t + d
            for r in busy:
                ready[r] = e
                holder[r] = i
            if e > end:
                end, end_i = e, i
            return e, i

        for it in self.items(key):
            k = it[0]
            if k == "guard":
                if depth == 0:
                    floor, floor_i = (floor0, floor_item) if it[1] and not self.plan.source_waits else (known, known_i)
                depth += 1
            elif k == "endguard":
                depth -= 1
            elif k == "decode":
                if latest + P.DECODE_US > known:
                    known, known_i = latest + P.DECODE_US, latest_i
            elif k == "work":
                e, i = run(0.0 if self.merge and it[4] == "rounds" else it[1], it[2], it[4])
                if it[3]:
                    latest, latest_i = e, i
            elif k == "rep":
                _, reps, shape, busy, (x0, y0) = it
                d = (self.times.wide(shape[1], shape[2], shape[3]) if shape[0] == "wide" else self.times.rep(shape)) * reps
                b = list(busy)
                if self.merge:
                    d = MERGE_ROUND_US * reps
                elif self.seam:
                    b += [(c[0], c[1] + x0, c[2] + y0) for c in self.cells[P.program_of(shape)]]
                kind = "wide" if shape[0] == "wide" else "".join(sorted(t.letter for t in shape[1]))
                latest, latest_i = run(d, b, "product " + kind)
            elif k == "free":
                _, tile, patch = it
                patch = own(patch)
                v = ready.get(patch, 0.0)
                if v > ready.get(tile, 0.0):
                    ready[tile] = v
                    holder[tile] = holder.get(patch, -1)
            elif k == "call":
                e, e_i = self.walk(it[1], floor if depth else floor0, floor_i if depth else floor_item)
                if e > end:
                    end, end_i = e, e_i
                if e > known:
                    latest, latest_i, known, known_i = e, e_i, e, e_i
        return end, end_i


def path(out: Path, options) -> None:
    import gidney_concrete as GC
    from qccd.fullstack.scale.gidney_plan import Plan
    _, times, cells = _programs(out)
    lst, R = GC._reader(out)
    f = _Flat(Plan(R), times, cells, merge="merge" in options, seam="noseam" not in options)
    end, i = f.walk(GC._keys(lst, R))
    names = {v: k for k, v in f.kinds.items()}
    what, why = Counter(), Counter()
    while i >= 0:
        what[names[f.kin[i]]] += f.dur[i]
        why[_Flat.LINK.get(f.link[i], f.link[i])] += 1
        i = f.pre[i]
    total = sum(what.values())
    print(f"one prime, flat: {end / 3.6e9:.2f} h; on its critical path, {sum(why.values()):,} items:")
    for k, v in what.most_common():
        print(f"   {v / 1e6:>10,.0f} s  {100 * v / total:5.1f} %  {k}")
    for k, v in why.most_common():
        print(f"   {v:>10,} links  {k}")


def _box(s) -> list:
    """What a merge holds at most: the tiles of its patches' bounding box."""
    if s[0] == "wide":
        return [("box", i, 0) for i in range(s[1])]
    a = s[1][0]
    xs, ys = [q.x for q in s[1]], [q.y for q in s[1]]
    return [("box", i - a.x, j - a.y) for i in range(min(xs), max(xs) + 1) for j in range(min(ys), max(ys) + 1)]


def bounds(out: Path) -> None:
    import gidney_concrete as GC
    from qccd.fullstack.scale import gidney_plan as P
    _, times, cells = _programs(out)
    lst, R = GC._reader(out)
    key = GC._keys(lst, R)
    base = None

    def row(name, t, c, rounds=1.0, magic=1.0):
        nonlocal base
        P.ROUND_US, P.MAGIC_CCZ_US, P.MAGIC_T_US, P.PREP_Y_US = 5822.0 * rounds, 150 * 5822.0 * magic, 23 * 5822.0 * magic, 43862.0 * magic
        plan = P.Plan(R)
        got = plan.schedule(key, t, c)[0]
        work = sum(plan.serial(key, t).values())
        base = base or got
        print(f"{name:52} {got / 3.6e9:7.2f} h a prime = {got * PRIMES / 1e6 / (365.25 * 86400):6.1f} y  "
              f"{got / base:5.3f} x  {work / got:4.2f} at once", flush=True)

    def x_assumed(s, t):    # assumed, not built: a two-patch X̄ product at ZZ's 25.6 ms, three patches at 66 ms
        if s[0] != "rep" or all(q.letter != "X" for q in s[1]) or len(s[1]) == 1:
            return t
        return min(t, {2: 25620.0, 3: 66171.6}.get(len(s[1]), t))

    none = {k: [] for k in cells}
    row("the run", times, cells)
    row("no seam blocks", times, none)
    row("cat programs x0.75", P.Times({s: 0.75 * t for s, t in times.items()}), cells)
    row("X̄ products at ZZ's time (assumed)", P.Times({s: x_assumed(s, t) for s, t in times.items()}), cells)
    row("wide cats as merges", P.Times({s: MERGE_ROUND_US if s[0] == "wide" else t for s, t in times.items()}),
        {s: [] if s[0] == "wide" else c for s, c in cells.items()})
    merge = P.Times({s: MERGE_ROUND_US for s in times})
    held = {s: _box(s) for s in times}
    row("every product a merge, corridors free", merge, none, rounds=0.0)
    row("every product a merge, corridors held", merge, held, rounds=0.0)
    row("  and magic at half the provisional time", merge, held, rounds=0.0, magic=0.5)
    row("  and magic made ahead", merge, held, rounds=0.0, magic=0.0)
    row("  with 25 rounds a merge, magic made ahead", P.Times({s: MERGE_ROUND_US * 25 / 11 for s in times}), held, rounds=0.0, magic=0.0)


def _p_of(t: float, coherent: bool = True) -> float:
    """Z on an ion idle for t seconds: the machine's noise, its linear and coherent memory terms."""
    from qccd.fullstack.scale.machine import noise
    n = noise()
    return n.memory_linear({}, t * 1e6) + (n.memory_coherent({}, t * 1e6) if coherent else 0.0)


def _gap_fail(p: float) -> float:
    if p <= GAP_CURVE[0][0]:
        return GAP_CURVE[0][1] * (p / GAP_CURVE[0][0]) ** 6
    if p >= GAP_CURVE[-1][0]:
        return 0.5
    i = bisect.bisect_left([c[0] for c in GAP_CURVE], p)
    (a, fa), (b, fb) = GAP_CURVE[i - 1], GAP_CURVE[i]
    return math.exp(math.log(fa) + math.log(p / a) / math.log(b / a) * math.log(fb / fa))


def gaps() -> None:
    """A rotated memory with one layer of Z_ERROR(p) on every data qubit between rounds, its other noise the machine's."""
    import numpy as np
    import pymatching
    import stim

    def circuit(d, p):
        c = stim.Circuit.generated("surface_code:rotated_memory_x", distance=d, rounds=6, after_clifford_depolarization=4.2e-5,
                                   before_measure_flip_probability=3.5e-6, after_reset_flip_probability=3.5e-6)
        data = [q for q, xy in c.get_final_qubit_coordinates().items() if xy[0] % 2 == 1 and xy[1] % 2 == 1]
        out, rounds = stim.Circuit(), 0
        for ins in c.flattened():
            out.append(ins)
            if ins.name == "MR":
                rounds += 1
                if rounds == 2:
                    out.append("Z_ERROR", data, p)
        return out

    for d in (5, 7, 9, 11):
        for p in (0.2, 0.1, 0.05, 0.03, 0.02, 0.01):
            c = circuit(d, p)
            m = pymatching.Matching.from_detector_error_model(c.detector_error_model(decompose_errors=True, approximate_disjoint_errors=True))
            sampler = c.compile_detector_sampler(seed=1)
            fails = n = 0
            while n < 2_000_000 and fails < 200:
                det, obs = sampler.sample(100_000, separate_observables=True)
                fails += int(np.sum(np.any(m.decode_batch(det) != obs, axis=1)))
                n += 100_000
            print(f"d {d:2}  p {p:5.3f}  {fails:>6}/{n:<8} {fails / n:.3e}", flush=True)


def failures(out: Path) -> None:
    rows, _, _ = _programs(out)
    us = {t: json.loads((out / "programs" / (hashlib.sha1(t.encode()).hexdigest()[:12] + ".json")).read_text(encoding="utf-8"))["us"] / 1e6 for t in rows}
    patches = lambda t: int(t.split()[1]) + 1 if t.startswith("wide") else len(t.split()) - 1
    for name, reps, coherent in (("11 repetitions, then 11 rounds: the listing", 11, True), ("  the same, a perfect echo", 11, False),
                                 ("repetition, round, repetition, round ...", 1, True), ("  the same, a perfect echo", 1, False)):
        by = sorted(((n * _gap_fail(_p_of(us[t] * reps, coherent)) * patches(t) * 11 / reps, t) for t, n in rows.items()), reverse=True)
        print(f"{name:46} longest gap {max(us.values()) * reps:6.2f} s   E[failures a run] {sum(b[0] for b in by):.3g}   most from {by[0][1]}")
    t = MERGE_ROUND_US / 1e6
    print(f"a merge: one round's stretch, {t * 1e3:.1f} ms, p = {_p_of(t):.2e}: the patch the distance study measured")


def main(argv) -> None:
    if not argv:
        sys.exit(__doc__)
    what = argv[0]
    if what == "gaps":
        gaps()
    elif what == "inside":
        inside(Path(argv[1]), argv[2:])
    elif what == "path":
        path(Path(argv[1]), argv[2:])
    elif what == "bounds":
        bounds(Path(argv[1]))
    elif what == "failures":
        failures(Path(argv[1]))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
