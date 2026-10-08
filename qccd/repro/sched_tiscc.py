"""Our schedule for a TISCC surface-code round, on TISCC's grid, under TISCC's clock.

TISCC (LeBlond et al. 2023) compiles one error-correction round on a grid of three-zone
straight segments (M O M) joined by X-junctions, one ion per zone.  Its ZZ gate (2,000 us,
split, merge and cooling included) acts on two ions in neighbouring zones of one segment:
one in the operation zone O, the other in an M zone next to it.  Every measure ion leaves its
home O zone for each of its four ZZs and comes back, and every layer of ZZs starts together;
a junction conflict stalls every later instruction (`enforce_hw_master_validity`).

What we keep exactly: the ions and where they start, the device, TISCC's gate table, and
each ion's own sequence of native operations (preparation, rotations, ZZs with the same
partners in the same order, measurement) -- so the round is the same circuit, operation for
operation.  Single-qubit operations, preparation and measurement happen only in O zones (the
paper's zone roles; TISCC's code aborts otherwise), and every ion ends the round in the zone
it started in, so the round can be repeated.

What we change is who moves and when:

* for each ZZ, either the measure ion goes to the data ion or the data ion goes to the
  measure ion (each pair shares one junction; the traveller crosses it once each way and
  is back in its own O zone for its rotations);
* nothing is synchronised by layer: every ion runs its own program as early as its partner,
  the zones (one ion each) and the junctions (one crossing at a time) allow;
* a traveller may go ahead and wait next to its partner while the partner is still busy,
  as long as the partner will not need that zone first;
* a traveller may go straight from one partner to the next when both sit across the same
  junction ("continue"): the next partner steps into its other M zone, the traveller does its
  rotations in that partner's O zone, steps back, and the partner returns.  Where two
  meetings fall in one layer at one junction this saves a crossing.

`simulate` runs one choice of travellers and continuations as a discrete-event simulation
and returns the timed schedule; `build` searches the choices (or replays a stored plan) and
returns the shortest schedule that `timed.check` accepts under the TISCC profile, TISCC's
law and the round's ZZs, with strict durations.
"""

from __future__ import annotations

import heapq
import random
from collections import defaultdict
from typing import Mapping, Sequence

from .importers import import_tiscc
from .models import TISCC, TISCC_US, tiscc_duration
from .timed import Event, PortGraph, TimedSchedule, check

__all__ = ["programs", "simulate", "build", "round_zz", "continuable", "verify", "Deadlock"]

MOVE = TISCC_US["Move"]
JMOVE = TISCC_US["Move"] + TISCC_US["Junction"]


class Deadlock(RuntimeError):
    """The simulation stopped with ions still waiting (a plan that cannot run)."""


def programs(theirs: TimedSchedule) -> tuple[dict[str, list], list[dict], dict[str, str]]:
    """Each ion's operations in TISCC's order (moves dropped), the ZZs, and each ion's home.

    A program item is ``("op", op_name, kind)`` or ``("zz", k)`` with ``k`` an index into
    the ZZ list; a ZZ records its two ions in TISCC's order, which one waited in its O zone
    (``still``) and its layer (TISCC starts each layer's ZZs together)."""
    home = {c[0]: s for s, c in theirs.chains.items() if c}
    where = dict(home)
    prog: dict[str, list] = defaultdict(list)
    zz: list[dict] = []
    starts = sorted({e.t0 for e in theirs.events if e.kind == "gate"})
    layer = {t: i + 1 for i, t in enumerate(starts)}
    for e in sorted(theirs.events, key=lambda e: (e.t0, e.id)):
        if e.kind == "hop":
            where[e.ions[0]] = e.dst
            continue
        if e.kind == "gate":
            a, b = e.ions
            still = a if where[a] == home[a] else b
            k = len(zz)
            zz.append({"ions": (a, b), "still": still, "layer": layer[e.t0]})
            prog[a].append(("zz", k))
            prog[b].append(("zz", k))
        else:
            prog[e.ions[0]].append(("op", e.meta["op"], e.kind))
    for ion in home:
        prog.setdefault(ion, [])
    return dict(prog), zz, home


def round_zz(theirs: TimedSchedule) -> list[tuple[str, str]]:
    return [tuple(e.ions) for e in sorted(theirs.events, key=lambda e: (e.t0, e.id)) if e.kind == "gate"]


class _Geo:
    def __init__(self, dev: PortGraph):
        self.dev = dev
        self.nj: dict[str, set[str]] = defaultdict(set)      # site -> adjacent junctions
        for a, b in dev.segments.values():
            if a in dev.nodes and b in dev.sites:
                self.nj[b].add(a)
            if b in dev.nodes and a in dev.sites:
                self.nj[a].add(b)

    def meeting(self, home_a: str, home_b: str) -> tuple[str, str, str]:
        """(M zone next to a's O, junction, M zone next to b's O): the one junction the two
        segments share."""
        hits = []
        for ma in self.dev.adjacent[home_a]:
            for mb in self.dev.adjacent[home_b]:
                for j in self.nj[ma] & self.nj[mb]:
                    hits.append((ma, j, mb))
        if len(hits) != 1:
            raise ValueError(f"{home_a} and {home_b} share {len(hits)} junction routes")
        return hits[0]


def _scripts(prog, zz, home, geo: _Geo, travel: Mapping[int, str], cont=frozenset()):
    """Per ion, the steps it runs:

    ``("op", name, kind)``                       an operation where the ion stands (an O zone)
    ``("hop", dst, path, label, visit)``         a move; ``visit`` is the ZZ it brings the ion to
    ``("gate", other, label)``                   wait until ``other`` has started that step
    ``("zz", k)``

    For ZZ ``k`` the traveller goes from its O zone, across the junction the two segments
    share, to the M zone next to its partner's O, and comes back after.  When ``k`` is in
    ``cont`` the traveller comes straight from its previous ZZ instead (the same junction):
    the partner steps into its other M zone, the traveller does its rotations in the
    partner's O zone, steps back, and the partner returns."""
    sc: dict[str, list] = {}
    for ion, items in prog.items():
        zks: list[int] = []
        ops_before: dict[int, list] = {}
        cur: list = []
        for it in items:
            if it[0] == "op":
                cur.append(it)
            else:
                ops_before[it[1]] = cur
                zks.append(it[1])
                cur = []
        tail = cur
        steps: list = []
        away = None                    # (junction, own M zone) while away from home

        def go_home():
            nonlocal away
            j, m_own = away
            steps.append(("hop", m_own, (j,), None, None))
            steps.append(("hop", home[ion], (), None, None))
            away = None

        for k in zks:
            a, b = zz[k]["ions"]
            P = b if ion == a else a
            ops = ops_before[k]
            m_own, j, m_far = geo.meeting(home[ion], home[P])
            if travel[k] == ion:
                if k in cont:
                    if away is None or away[0] != j:
                        raise ValueError(f"ZZ {k}: {ion} cannot continue (not across {j})")
                    steps.append(("hop", m_far, (j,), None, k))
                    steps.append(("gate", P, f"sa{k}_out"))
                    steps.append(("hop", home[P], (), f"sa{k}_in", None))
                    steps += ops
                    steps.append(("hop", m_far, (), f"sa{k}_back", None))
                    steps.append(("zz", k))
                else:
                    if away is not None:
                        go_home()
                    steps += ops
                    steps.append(("hop", m_own, (), None, None))
                    steps.append(("hop", m_far, (j,), None, k))
                    steps.append(("zz", k))
                away = (j, m_own)
            else:
                if away is not None:
                    go_home()
                steps += ops
                if k in cont:
                    # step aside into the M zone the traveller does not use
                    m_q = next(m for m in geo.dev.adjacent[home[ion]] if m != m_own)
                    T = travel[k]
                    steps.append(("hop", m_q, (), f"sa{k}_out", None))
                    steps.append(("gate", T, f"sa{k}_back"))
                    steps.append(("hop", home[ion], (), None, None))
                steps.append(("zz", k))
        if away is not None:
            go_home()
        steps += tail
        sc[ion] = steps
    return sc


def continuable(prog, zz, home, geo: _Geo, travel: Mapping[int, str]) -> list[int]:
    """ZZs whose traveller also travelled for its previous ZZ, across the same junction."""
    out = []
    for ion, items in prog.items():
        ks = [it[1] for it in items if it[0] == "zz"]
        for k0, k1 in zip(ks, ks[1:]):
            if travel[k0] != ion or travel[k1] != ion:
                continue
            p0 = [x for x in zz[k0]["ions"] if x != ion][0]
            p1 = [x for x in zz[k1]["ions"] if x != ion][0]
            if geo.meeting(home[ion], home[p0])[1] == geo.meeting(home[ion], home[p1])[1]:
                out.append(k1)
    return out


def simulate(theirs: TimedSchedule, travel: Mapping[int, str], *, prio: Mapping[str, float] | None = None,
             cont=frozenset(), name: str = "tiscc_ours") -> TimedSchedule:
    """Run every ion's program with the given traveller per ZZ, as early as possible."""
    dev = theirs.device
    geo = _Geo(dev)
    prog, zz, home = programs(theirs)
    sc = _scripts(prog, zz, home, geo, travel, cont)
    prio = prio or {}
    zz_step: dict[tuple[str, int], int] = {}
    label_at: dict[tuple[str, str], int] = {}
    for ion, steps in sc.items():
        for i, st in enumerate(steps):
            if st[0] == "zz":
                zz_step[(ion, st[1])] = i
            elif st[0] == "hop" and st[3]:
                label_at[(ion, st[3])] = i

    def last_use(P: str, site: str, before: int) -> int:
        last = -1
        cur = home[P]
        for i, st in enumerate(sc[P][:before]):
            if st[0] == "hop":
                if site in (cur, st[1]):
                    last = i
                cur = st[1]
        return last

    pos = {ion: 0 for ion in sc}               # next step index
    t_ready = {ion: 0.0 for ion in sc}
    at = dict(home)
    occ: dict[str, str | None] = defaultdict(lambda: None)
    free_from: dict[str, float] = defaultdict(float)
    for ion, s in home.items():
        occ[s] = ion
    j_free: dict[str, float] = defaultdict(float)
    zz_wait: dict[int, tuple[str, float]] = {}
    events: list[tuple] = []
    on_free: dict[str, list[str]] = defaultdict(list)
    on_step: dict[tuple[str, int], list[str]] = defaultdict(list)
    q: list = []
    cnt = 0

    def push(ion: str, t: float) -> None:
        nonlocal cnt
        cnt += 1
        heapq.heappush(q, (t, prio.get(ion, 0.0), cnt, ion))

    def advance(ion: str, t: float) -> None:
        i = pos[ion]
        pos[ion] = i + 1
        for w in on_step.pop((ion, i), []):
            push(w, max(t, t_ready[w]))

    for ion in sc:
        push(ion, 0.0)
    while q:
        t, _, _, ion = heapq.heappop(q)
        if pos[ion] >= len(sc[ion]):
            continue
        t = max(t, t_ready[ion])
        st = sc[ion][pos[ion]]
        if st[0] == "op":
            _, op, kind = st
            if dev.sites[at[ion]].get("zone") != "O":
                raise Deadlock(f"{ion}: {op} outside an O zone ({at[ion]})")
            d = TISCC_US[op]
            events.append((kind, t, t + d, (ion,), at[ion], None, None, (), op))
            t_ready[ion] = t + d
            advance(ion, t)
            push(ion, t + d)
            continue
        if st[0] == "gate":
            _, other, label = st
            need = label_at[(other, label)]
            if pos[other] <= need:
                on_step[(other, need)].append(ion)
                continue
            advance(ion, t)
            push(ion, t)
            continue
        if st[0] == "hop":
            _, dst, path, _label, visit = st
            if visit is not None:
                # a visitor waits until its partner no longer needs the zone it goes to, and
                # until the partner's previous ZZ has begun (earlier visitors are then in
                # place or gone, so none of them can find this zone taken)
                k = visit
                a, b = zz[k]["ions"]
                P = b if ion == a else a
                before = [i for i, x in enumerate(sc[P][:zz_step[(P, k)]]) if x[0] == "zz"]
                need = max(last_use(P, dst, zz_step[(P, k)]), before[-1] if before else -1)
                if pos[P] <= need:
                    on_step[(P, need)].append(ion)
                    continue
            if occ[dst] is not None and occ[dst] != ion:
                on_free[dst].append(ion)
                continue
            t0 = max(t, free_from[dst])
            if path:
                t0 = max(t0, *(j_free[n] for n in path))
            if t0 > t:
                push(ion, t0)
                continue
            d = JMOVE if path else MOVE
            src = at[ion]
            events.append(("hop", t0, t0 + d, (ion,), None, src, dst, tuple(path), "Move"))
            for n in path:
                j_free[n] = t0 + d
            occ[dst] = ion
            occ[src] = None
            free_from[src] = t0 + d
            for w in on_free.pop(src, []):
                push(w, t0 + d)
            at[ion] = dst
            t_ready[ion] = t0 + d
            advance(ion, t0)
            push(ion, t0 + d)
            continue
        # zz
        k = st[1]
        a, b = zz[k]["ions"]
        if k in zz_wait:
            o, to = zz_wait.pop(k)
            s1, s2 = at[a], at[b]
            if not (s2 in dev.adjacent[s1] or s1 in dev.adjacent[s2]):
                raise Deadlock(f"ZZ {k}: {a}@{s1} and {b}@{s2} are not neighbours")
            t0 = max(t, to)
            d = TISCC_US["ZZ"]
            o_site = s1 if dev.sites[s1].get("zone") == "O" else s2
            events.append(("gate", t0, t0 + d, (a, b), o_site, None, None, (), "ZZ"))
            for x in (a, b):
                t_ready[x] = t0 + d
                advance(x, t0)
                push(x, t0 + d)
        else:
            zz_wait[k] = (ion, t)
    left = {i: sc[i][pos[i]] for i in sc if pos[i] < len(sc[i])}
    if left:
        raise Deadlock(f"{len(left)} ions stuck, e.g. {list(left.items())[:4]}")
    for ion, s in home.items():
        if at[ion] != s:
            raise Deadlock(f"{ion} ends in {at[ion]}, not at home {s}")
    events.sort(key=lambda r: (r[1], r[0] != "hop"))
    out = []
    for i, (kind, t0, t1, ions, site, src, dst, path, op) in enumerate(events):
        meta = {"op": op}
        if kind == "hop":
            meta["junction"] = bool(path)
            out.append(Event(i, "hop", t0, t1, ions, src=src, dst=dst, path=path, meta=meta))
        else:
            out.append(Event(i, kind, t0, t1, ions, at=site, meta=meta))
    return TimedSchedule(name=name, device=dev, chains={s: list(c) for s, c in theirs.chains.items()},
                         events=out, source={"tool": "qccd.repro.sched_tiscc"})


def verify(s: TimedSchedule, circuit: Sequence[tuple[str, str]]):
    return check(s, TISCC, duration=tiscc_duration, circuit=list(circuit), strict=True)


def _parity_plan(zz, home, measure: set[str], odd: str) -> dict[int, str]:
    """The measure ion travels in odd layers and the data ion in even ones (or the reverse)."""
    out = {}
    for k, z in enumerate(zz):
        a, b = z["ions"]
        m = a if a in measure else b
        d = b if m == a else a
        mo = (z["layer"] % 2 == 1) == (odd == "m")
        out[k] = m if mo else d
    return out


def build(spec: str, *, nrows: int, ncols: int, seconds: float = 60.0, seed: int = 0,
          plan: Mapping | None = None, circuit: Sequence[tuple[str, str]] | None = None,
          name: str = "tiscc_ours", log=None) -> TimedSchedule:
    """The shortest schedule found that the checker accepts.

    With ``plan`` (``{"travellers": {zz index: ion}, "continue": [zz index, ...]}``, as a
    schedule of ours records it in ``source["plan"]``) the plan is replayed.  Otherwise the
    search starts from the layer-parity plans and TISCC's own choice (the measure ion always
    travels), then flips travellers and continuations one or a few at a time, keeping any
    change that does not lengthen the round."""
    theirs = import_tiscc(spec, nrows=nrows, ncols=ncols)
    prog, zz, home = programs(theirs)
    geo = _Geo(theirs.device)
    circ = list(circuit) if circuit is not None else round_zz(theirs)
    measure = {i for i, p in prog.items() if any(x[0] == "op" and x[2] == "prepare" for x in p)}

    def run(travel, cont):
        try:
            s = simulate(theirs, travel, cont=frozenset(cont), name=name)
        except (Deadlock, ValueError):
            return None, None
        return s, max(e.t1 for e in s.events)

    if plan is not None:
        travel = {int(k): v for k, v in plan["travellers"].items()}
        cont = set(plan.get("continue", ()))
        s, ms = run(travel, cont)
        if s is None:
            raise Deadlock("the stored plan does not run")
    else:
        import time as _t
        rng = random.Random(seed)
        t_end = _t.time() + seconds
        theirs_plan = {k: (z["ions"][1] if z["ions"][0] == z["still"] else z["ions"][0])
                       for k, z in enumerate(zz)}
        best = None
        for travel in (_parity_plan(zz, home, measure, "m"), _parity_plan(zz, home, measure, "d"),
                       theirs_plan):
            for cont in (set(), set(continuable(prog, zz, home, geo, travel))):
                s, ms = run(travel, cont)
                if log:
                    log(f"start plan: {ms}")
                if s is not None and (best is None or ms < best[0]):
                    best = (ms, dict(travel), set(cont), s)
        if best is None:
            raise Deadlock("no starting plan runs")
        ms, travel, cont, s = best
        while _t.time() < t_end:
            tr2, co2 = dict(travel), set(cont)
            for _ in range(rng.choice([1, 1, 2, 3])):
                k = rng.randrange(len(zz))
                a, b = zz[k]["ions"]
                if rng.random() < 0.6:
                    tr2[k] = b if tr2[k] == a else a
                else:
                    co2 ^= {k}
            allowed = set(continuable(prog, zz, home, geo, tr2))
            co2 &= allowed
            if rng.random() < 0.3 and allowed - co2:
                co2.add(rng.choice(sorted(allowed - co2)))
            s2, ms2 = run(tr2, co2)
            if s2 is not None and ms2 <= ms:
                if log and ms2 < ms:
                    log(f"improved: {ms2}")
                ms, travel, cont, s = ms2, tr2, co2, s2
    rep = verify(s, circ)
    if not rep.ok:
        raise Deadlock(f"checker rejects: {rep.summary()['violations']} {rep.summary()['first'][:3]}")
    s.source["plan"] = {"travellers": {str(k): v for k, v in sorted(travel.items())},
                        "continue": sorted(cont)}
    return s
