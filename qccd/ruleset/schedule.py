"""A paper's timed schedule, as the rules see it.

The schedule is replayed in start-time order exactly as the timed checker replays it
(`qccd.repro.timed.check`): where every ion is, which chain it stands in and in what order,
and when it holds a trap, a rail or a junction.  What the replay finds is recorded on the
operations (did it find its ions where it said? did a split take the end ion?) and in the
two timelines; the rules then judge.

Instants are the start and every time at which an operation begins or ends.  An ion counts
in a trap from the moment it starts to arrive until the moment it has finished leaving --
a split holds its ion in the trap until it ends, a merge from the moment it begins -- which
is the timed checker's convention, so the two can never disagree about capacity.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Callable, Mapping, Sequence

from .model import CircuitOpData, Execution, Hardware, Ion, Op, Rail, Site, Timeline

__all__ = ["schedule_hardware", "schedule_execution"]

INF = float("inf")

#: the timed checker's event kinds -> the rules' operation kinds
KIND = {"gate": "gate2", "gate1": "gate1", "measure": "measure", "reset": "reset",
        "prepare": "reset", "swap": "swap", "cool": "cool", "split": "split",
        "merge": "merge", "move": "shuttle", "transit": "shuttle",
        "j_enter": "junction_enter", "j_exit": "junction_exit", "hop": "hop"}


def schedule_hardware(dev) -> Hardware:
    """A `PortGraph` in the rules' terms.  Junctions are the graph's nodes; a site where
    three or more segments meet is one too."""
    sites = [Site(s) for s in dev.sites]
    nodes_j = [Site(n) for n in dev.nodes]
    rails = [Rail(s) for s in dev.segments]
    deg: Counter = Counter()
    for a, b in dev.segments.values():
        deg[a] += 1
        deg[b] += 1
    junctions = list(nodes_j)
    cap: dict[Any, int] = {Site(s): int(d.get("capacity", 0)) for s, d in dev.sites.items()}
    cap.update({Site(n): int(d.get("capacity", 1)) for n, d in dev.nodes.items()})
    cap.update({Rail(s): 1 for s in dev.segments})
    abilities: dict[Site, frozenset[str]] = {}
    for s, d in dev.sites.items():
        if "abilities" in d:
            abilities[Site(s)] = frozenset(d["abilities"])
    rails_at: dict[Site, list[Rail]] = defaultdict(list)
    for sid, (a, b) in dev.segments.items():
        rails_at[Site(a)].append(Rail(sid))
        rails_at[Site(b)].append(Rail(sid))
    return Hardware(
        name="", sites=sites, junctions=junctions, nodes=sites + nodes_j, rails=rails,
        capacity=cap, degree={Site(n): deg[n] for n in list(dev.sites) + list(dev.nodes)},
        zone={Site(s): d.get("zone") for s, d in dev.sites.items()},
        abilities=abilities,
        ends={Rail(s): (Site(a), Site(b)) for s, (a, b) in dev.segments.items()},
        rails_at={k: tuple(v) for k, v in rails_at.items()},
        neighbours={Site(s): frozenset(Site(x) for x in v) for s, v in dev.adjacent.items()},
    )


def schedule_execution(sched, *, chain_order: str | None = None,
                       duration: Callable | None = None, strict_clock: bool = False,
                       circuit: Sequence[tuple[str, str]] | None = None,
                       tol: float = 1e-6, name: str = "") -> Execution:
    """Replay a `TimedSchedule`.

    ``chain_order``  how the chain is tracked through a reorder: "qccdsim" (the moving ion
                     is lifted out where it stood), "physical" (a gate swap exchanges the two
                     ions' states), or None (the order inside a trap is not tracked)
    ``duration``     the paper's duration law, `(event, context) -> microseconds | None`
    ``strict_clock`` an event the law gives no duration is not an operation of the model
                     (for schedules we write ourselves), rather than one it does not time
    ``circuit``      the circuit's two-qubit gates as (control, target), in program order
    """
    from ..repro.timed import Context, _split_order

    dev = sched.device
    hw = schedule_hardware(dev)
    hw.name = getattr(sched, "name", "")
    seg_between: dict[frozenset, str] = {}
    for sid, ends in dev.segments.items():
        seg_between.setdefault(frozenset(ends), sid)

    events = sorted(sched.events, key=lambda e: (e.t0, e.id))
    times = sorted({e.t0 for e in events} | {e.t1 for e in events})
    index = {t: i + 1 for i, t in enumerate(times)}       # instant 0 is the start
    instants = ["the start"] + [f"t = {t:g} µs" for t in times]

    ions: dict[str, Ion] = {}
    loc: dict[str, tuple[str, str]] = {}
    chains: dict[str, list[str]] = {s: [] for s in dev.sites}
    arrival: dict[str, float] = {}
    resid: dict[Any, list[tuple[float, float]]] = defaultdict(list)
    pos_changes: list[tuple[float, int, str, Any]] = []
    malformed_start: list[str] = []
    for site, chain in sched.chains.items():
        if site not in dev.sites:
            malformed_start.append(f"initial chain on unknown site {site}")
            continue
        for i in chain:
            if i in loc:
                malformed_start.append(f"ion {i} placed twice initially")
            ions.setdefault(i, Ion(i))
            loc[i] = ("site", site)
            chains[site].append(i)
            arrival[i] = -INF

    def key(kind: str, where: str):
        return Site(where) if kind in ("site", "node") else Rail(where)

    def leave(i: str, t: float) -> None:
        kind, where = loc[i]
        resid[key(kind, where)].append((arrival[i], t))

    def arrive(i: str, kind: str, where: str, t: float, order: int) -> None:
        loc[i] = (kind, where)
        arrival[i] = t
        pos_changes.append((t, order, i, Site(where) if kind == "site" else None))

    ops: list[Op] = []
    for n, e in enumerate(events):
        kind = "gate1" if e.kind == "gate" and len(e.ions) == 1 else KIND.get(e.kind, e.kind)
        o = Op(index=n, kind=kind, ions=tuple(ions.setdefault(i, Ion(i)) for i in e.ions),
               start=index[e.t0], end=index[e.t1], t0=e.t0, t1=e.t1,
               site=Site(e.at) if e.at is not None and e.at in dev.sites else None,
               facts={"event": e.id, "locks": tuple(str(c) for c in e.meta.get("locks", ()))},
               text=f"{e.kind} {','.join(e.ions)} (event {e.id})")
        if e.meta.get("class") is not None:
            o.facts["motion"] = str(e.meta.get("class"))
        ops.append(o)

        def misplaced(msg: str, o=o) -> None:
            o.facts["misplaced"] = msg

        def malformed(msg: str, o=o) -> None:
            o.facts["malformed"] = msg

        if e.t1 < e.t0 - tol:
            malformed(f"ends before it starts")
        unknown = [i for i in e.ions if i not in loc]
        if unknown:
            malformed(f"names unknown ion {unknown[0]}")
            continue
        chain = chains.get(e.at) if e.at in chains else None
        if duration is not None:
            want = duration(e, Context(dev, list(chain) if chain is not None else None,
                                       len(chain) if chain is not None else None))
            if want is not None or strict_clock:
                o.facts["clock"] = want

        if e.kind in ("gate", "gate1", "measure", "reset", "prepare", "swap", "cool"):
            where = [loc[i] for i in e.ions]
            if e.kind == "gate" and len(e.ions) == 2:
                (k1, s1), (k2, s2) = where
                if k1 != "site" or k2 != "site":
                    misplaced(f"gate on ions not in traps")
                elif e.at is not None and e.at not in (s1, s2):
                    misplaced(f"says trap {e.at}, its ions are in {s1} and {s2}")
                if k1 == "site" and o.site is None:
                    o.site = Site(s1)
                o.facts["partner_sites"] = (s1, s2)
            else:
                for i, (k, s) in zip(e.ions, where):
                    if k != "site":
                        misplaced(f"{i} is on {k} {s}, not in a trap")
                    elif e.at is not None and s != e.at:
                        misplaced(f"says {e.at}, {i} is in {s}")
                    elif o.site is None:
                        o.site = Site(s)
            if e.kind == "swap" and len(e.ions) == 2 and chain is not None and chain_order:
                a, b = e.ions
                if a in chain and b in chain:
                    x, y = chain.index(a), chain.index(b)
                    chain[x], chain[y] = chain[y], chain[x]
            continue

        if len(e.ions) != 1:
            malformed(f"carries {len(e.ions)} ions; one expected")
            continue
        i = e.ions[0]
        kind, where = loc[i]
        if e.kind == "split":
            o.path = (Site(e.at),) if e.at else ()
            o.rails = (Rail(e.seg),) if e.seg else ()
            if kind != "site" or where != e.at:
                misplaced(f"{i} is on {kind} {where}, not in {e.at}")
                continue
            if e.seg not in dev.segments or e.at not in dev.segments[e.seg]:
                malformed(f"segment {e.seg} does not touch {e.at}")
                continue
            if chain_order:
                verdict: list[str] = []
                _split_order(chains[e.at], i, dev.port(e.at, e.seg), e, chain_order,
                             lambda _c, msg, *_ids: verdict.append(msg), Counter())
                o.facts["at_chain_end"] = not e.swap and not verdict
                o.facts["swap_paid"] = bool(e.swap) and not verdict
                if verdict:
                    o.facts["chain_problem"] = verdict[0]
            else:
                chains[e.at].remove(i)
                o.facts["at_chain_end"] = True
            leave(i, e.t1)
            arrive(i, "seg", e.seg, e.t0, n)
        elif e.kind == "merge":
            o.path = (Site(e.at),) if e.at else ()
            o.rails = (Rail(e.seg),) if e.seg else ()
            if kind != "seg" or where != e.seg:
                misplaced(f"{i} is on {kind} {where}, not on {e.seg}")
                continue
            if e.at not in dev.sites or e.at not in dev.segments.get(e.seg, ()):
                malformed(f"segment {e.seg} does not touch {e.at}")
                continue
            leave(i, e.t1)
            side = dev.port(e.at, e.seg)
            if chain_order and side == "L":
                chains[e.at].insert(0, i)
            else:
                chains[e.at].append(i)
            arrive(i, "site", e.at, e.t0, n)
        elif e.kind == "move":
            if kind != "seg" or where != e.src:
                misplaced(f"{i} is on {kind} {where}, not on {e.src}")
                continue
            ends = set(dev.segments.get(e.src, ())) & set(dev.segments.get(e.dst, ()))
            via = next(iter(ends)) if e.via is None and len(ends) == 1 else e.via
            if via not in ends:
                malformed(f"{e.src} and {e.dst} do not meet at {via}")
                continue
            o.rails = (Rail(e.src), Rail(e.dst))
            o.facts["crossed"] = (Site(via),)
            resid[Site(via)].append((e.t0, e.t1))
            leave(i, e.t1)
            arrive(i, "seg", e.dst, e.t0, n)
        elif e.kind == "transit":
            o.rails = (Rail(e.seg),) if e.seg else ()
            if kind != "seg" or where != e.seg:
                misplaced(f"{i} is on {kind} {where}, not on {e.seg}")
        elif e.kind == "j_enter":
            if kind != "seg" or where != e.src:
                misplaced(f"{i} is on {kind} {where}, not on {e.src}")
                continue
            if e.via not in dev.segments.get(e.src, ()):
                malformed(f"{e.src} does not touch {e.via}")
                continue
            o.facts["crossed"] = (Site(e.via),)
            leave(i, e.t1)
            arrive(i, "node", e.via, e.t0, n)
            pos_changes[-1] = (e.t0, n, i, None)
        elif e.kind == "j_exit":
            if kind != "node" or where != e.via:
                misplaced(f"{i} is on {kind} {where}, not in junction {e.via}")
                continue
            if e.via not in dev.segments.get(e.dst, ()):
                malformed(f"{e.dst} does not touch {e.via}")
                continue
            leave(i, e.t1)
            arrive(i, "seg", e.dst, e.t0, n)
        elif e.kind == "hop":
            if kind != "site" or where != e.src:
                misplaced(f"{i} is on {kind} {where}, not in {e.src}")
                continue
            if e.dst not in dev.sites:
                malformed(f"destination {e.dst} is not a trap")
                continue
            walk = [e.src, *e.path, e.dst]
            if any(x not in dev.nodes for x in e.path):
                malformed(f"its path passes something that is not a junction")
                continue
            used = [seg_between.get(frozenset((x, y))) for x, y in zip(walk, walk[1:])]
            if None in used:
                malformed(f"no segment joins two of its nodes")
                continue
            o.path = tuple(Site(x) for x in walk)
            o.rails = tuple(Rail(s) for s in used)
            o.facts["crossed"] = tuple(Site(x) for x in e.path)
            for sg in used:
                resid[Rail(sg)].append((e.t0, e.t1))
            for x in e.path:
                resid[Site(x)].append((e.t0, e.t1))
            chains[e.src].remove(i)
            leave(i, e.t1)
            chains[e.dst].append(i)
            arrive(i, "site", e.dst, e.t0, n)
        else:
            malformed(f"unknown kind {e.kind!r}")

    ends_on: list[str] = []
    for i, (kind, where) in loc.items():
        resid[key(kind, where)].append((arrival[i], INF))
        if kind != "site":
            ends_on.append(f"{i} ends the schedule on {'a rail' if kind == 'seg' else 'junction'} {where}")

    # -- the occupancy timeline: closed-open intervals, swept at every boundary
    occ = Timeline(0)
    for k, ivs in resid.items():
        marks: list[tuple[float, int]] = []
        for a, b in ivs:
            marks.append((a, 1))
            if b != INF:
                marks.append((b, -1))
        marks.sort(key=lambda m: (m[0], m[1]))
        n_ = 0
        for t, d in marks:
            n_ += d
            occ.set(k, 0 if t == -INF else index.get(t, 0), n_)
    pos = Timeline(None)
    for site, chain in sched.chains.items():
        for i in chain:
            pos.set(ions[i], 0, Site(site))
    pos_changes.sort(key=lambda x: (x[0], x[1]))
    for t, _, i, where in pos_changes:
        pos.set(ions[i], index[t], where)

    exe = Execution(kind="schedule", hardware=hw, ions=sorted(ions.values()), ops=ops,
                    instants=instants, instant_time=[-INF] + times, occupancy=occ, position=pos,
                    name=name or getattr(sched, "name", ""))
    exe.settings["tol"] = tol
    exe.facts["has_clock"] = duration is not None
    exe.facts["malformed_start"] = malformed_start
    exe.facts["ends_off_site"] = ends_on

    if circuit is not None:
        ion_of = dict(sched.qubits or {})
        to_ion = lambda q: ion_of.get(str(q), str(q))
        circ: list[CircuitOpData] = []
        want_by_pair: dict[frozenset, list[int]] = defaultdict(list)
        for k, (a, b) in enumerate(circuit):
            ia, ib = to_ion(a), to_ion(b)
            circ.append(CircuitOpData(k, "cx", (ia, ib), ("control", "target"),
                                      text=f"cx {a},{b}"))
            want_by_pair[frozenset((ia, ib))].append(k)
        got_by_pair: dict[frozenset, list[Op]] = defaultdict(list)
        for o in sorted((o for o in ops if o.kind == "gate2"), key=lambda o: (o.t0, o.index)):
            got_by_pair[frozenset(x.id for x in o.ions)].append(o)
        for pair, gs in got_by_pair.items():
            for o, c in zip(gs, want_by_pair.get(pair, ())):
                o.serves = (c,)
        exe.circuit = circ
    return exe
