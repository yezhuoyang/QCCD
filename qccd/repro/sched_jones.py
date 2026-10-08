"""Our schedule for the rotated surface code on Jones et al.'s grid, under their model.

Jones & Murali (arXiv 2510.23519) compile one round of `surface_code:rotated_memory_z` onto
a grid of capacity-2 traps (one qubit per trap, a slot left for a visitor), with junctions
between them, and time it with their Table 1 (split/merge 80 us, shuttle 5, junction entry
and exit 50 each, MS 40, rotation 5, measurement 400, reset 50).  Their router sends each
ancilla to one data qubit and back for every CNOT and inserts a barrier after every routing
pass; it keeps each ancilla's CNOTs in circuit order but not each data qubit's.

Ours keeps the device, the qubit-to-trap mapping, the native decomposition (H -> RY RX;
CX(c,t) -> RY(c) RX(c) RX(t) MS RY(c); MR -> M then R) and every qubit's sequence of
operations; the only reordering is the one the circuit allows (a qubit's CNOTs in which it
plays the same role, between two of the other role).  Then:

* every ancilla tours its data instead of going home after each gate: it visits the data
  around one end of its own trap, passes through its (empty) home trap, then visits those
  around the other end, and comes home to be measured.  It always leaves a data trap through
  the end it came in by, so no gate swap is needed anywhere;
* the order of the tour follows what each data qubit needs: an ancilla is the first gate of
  its (+1,+1) data, the last of its (-1,-1) data and a middle one of the other two, and every
  gate gets a slot (1, 2 around one end of the trap; 4, 5 around the other) that both the
  ancilla's tour and the data's own order follow, so every qubit sees its gates in an order
  the circuit allows and no ancilla waits on a chain of others;
* nothing waits for a barrier: each operation starts as soon as its ions, its trap and every
  component the model says it holds are free (split and merge hold the junction at the far
  end of their segment, as the artifact declares; an entry or exit holds the junction and the
  segment).  Two rules keep the list scheduler out of its own way: a data qubit is measured
  only once no visitor is left in its trap (a measurement holds the trap 400 us), and a
  junction serves the ancilla whose slots 1-2 lie behind it before the one whose slots 4-5 do.

Every event carries the components it holds (``meta["locks"]``, in the artifact's naming), and
`build` returns a schedule only after `timed.check` accepted it under the JONES profile with
strict durations and the circuit.
"""

from __future__ import annotations

import heapq
import re
from collections import defaultdict
from pathlib import Path
from typing import Mapping, Sequence

from .models import JONES, JONES_US, jones_duration
from .sched import _Busy
from .timed import Event, PortGraph, TimedSchedule, check

__all__ = ["stim_program", "build", "Deadlock"]

_OPNAME = {"reset": "QubitReset", "measure": "Measurement", "RX": "XRotation", "RY": "YRotation",
           "gate": "TwoQubitMSGate", "split": "Split", "merge": "Merge", "transit": "Move",
           "j_enter": "JunctionCrossing", "j_exit": "JunctionCrossing"}


class Deadlock(RuntimeError):
    pass


_STIM_LINE = re.compile(r"^([A-Z_]+)(\(([^)]*)\))?\s*(.*)$")


def stim_program(path: str | Path) -> tuple[list[tuple[str, str]], dict[str, list], dict[str, tuple]]:
    """The circuit's CX list, each qubit's operations in circuit order (``("op", name)`` or
    ``("cx", k)``), and qubit coordinates."""
    cx: list[tuple[str, str]] = []
    prog: dict[str, list] = defaultdict(list)
    coords: dict[str, tuple] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        ln = line.split("#")[0].strip()
        if not ln:
            continue
        m = _STIM_LINE.match(ln)
        if m is None:
            raise ValueError(f"cannot read stim line {ln!r}")
        op, par, rest = m.group(1), m.group(3), m.group(4)
        args = rest.split()
        if op == "QUBIT_COORDS":
            coords[args[0]] = tuple(float(x) for x in par.split(","))
        elif op == "R":
            for q in args:
                prog[q].append(("op", "reset"))
        elif op == "H":
            for q in args:
                prog[q] += [("op", "RY"), ("op", "RX")]
        elif op in ("CX", "CNOT"):
            for i in range(0, len(args), 2):
                c, t = args[i], args[i + 1]
                k = len(cx)
                cx.append((c, t))
                prog[c].append(("cx", k))
                prog[t].append(("cx", k))
        elif op == "M":
            for q in args:
                prog[q].append(("op", "measure"))
        elif op == "MR":
            for q in args:
                prog[q] += [("op", "measure"), ("op", "reset")]
        elif op in ("TICK", "DETECTOR", "OBSERVABLE_INCLUDE", "SHIFT_COORDS"):
            continue
        elif op == "REPEAT" or op == "}":
            raise NotImplementedError("one round, no REPEAT blocks")
        else:
            raise ValueError(f"unhandled stim op {op!r}")
    return cx, dict(prog), coords


def _runs(q: str, items: list, cx: list) -> list[list[int]]:
    """The qubit's CNOTs grouped into maximal runs of one role (the reorderable blocks)."""
    out, role = [], None
    for it in items:
        if it[0] != "cx":
            continue
        k = it[1]
        r = "c" if cx[k][0] == q else "t"
        if r != role:
            out.append([])
            role = r
        out[-1].append(k)
    return out


def _num(x: str) -> str:
    return x[1:] if x[:1] in "TCJ" else x


class _Sim:
    def __init__(self, dev: PortGraph, chains: Mapping[str, list[str]]):
        self.dev = dev
        self.busy: dict[str, _Busy] = defaultdict(_Busy)
        self.chain = {t: list(chains.get(t, [])) for t in dev.sites}
        self.loc = {i: t for t, c in self.chain.items() for i in c}
        self.init = {t: len(c) for t, c in self.chain.items()}
        self.sm_t: dict[str, float] = defaultdict(float)
        self.occ_ev: dict[str, list[tuple[float, int]]] = defaultdict(list)
        self.events: list[tuple] = []

    def _fit(self, t: float, blocks: Sequence[tuple[str, float, float]]) -> float:
        s = t
        for _ in range(10000):
            moved = False
            for comp, a, b in blocks:
                e = self.busy[comp].conflict_end(s + a, s + b)
                if e is not None:
                    s = max(s, e - a)
                    moved = True
            if not moved:
                return s
        raise Deadlock("no slot found")

    def _take(self, s: float, blocks) -> None:
        for comp, a, b in blocks:
            self.busy[comp].add(s + a, s + b)

    def occupancy_ok(self, trap: str, a: float) -> bool:
        """One more ion arriving at ``a`` fits in ``trap`` at every later instant, given the
        arrivals and departures scheduled so far (a resident whose departure is not yet
        scheduled stays for good).  At equal times a departure goes first, as in the checker."""
        cap = self.dev.capacity(trap)
        evs = sorted(self.occ_ev[trap])
        level = self.init[trap] + sum(d for x, d in evs if x <= a)
        if level + 1 > cap:
            return False
        for x, d in evs:
            if x > a:
                level += d
                if level + 1 > cap:
                    return False
        return True


def build(dev: PortGraph, chains: Mapping[str, list[str]], qubits: Mapping[str, str],
          stim: str | Path, *, name: str = "jones_ours", verify: bool = True,
          slots: Mapping[tuple[int, int], int] | None = None,
          junction_order: bool = True) -> TimedSchedule:
    cx, prog, coords = stim_program(stim)
    ion = {q: str(qubits[q]) for q in prog}
    # an ancilla is measured and reset (MR); a data qubit is only measured
    roles = {q: ("ancilla" if ("op", "measure") in items and
                 ("op", "reset") in items[items.index(("op", "measure")):]
                 else "data") for q, items in prog.items()}
    sim = _Sim(dev, chains)
    home = {i: sim.loc[i] for i in ion.values()}
    ends: dict[str, dict[str, str]] = defaultdict(dict)       # trap -> junction -> segment
    for s, (a, b) in dev.segments.items():
        if a in dev.sites and b in dev.nodes:
            ends[a][b] = s
        elif b in dev.sites and a in dev.nodes:
            ends[b][a] = s

    # -- each data qubit's CNOT order: its runs in circuit order (a run may be reordered)
    run_of: dict[tuple[str, int], int] = {}
    for q, items in prog.items():
        for r, ks in enumerate(_runs(q, items, cx)):
            for k in ks:
                run_of[(q, k)] = r

    # -- each ancilla's tour.  Its data sit at code offsets (+-1, +-1); the ancilla is the
    # first gate of its (+1,+1) data and the last of its (-1,-1) data, and a middle one of
    # the other two.  Each pair of data shares one end junction with the ancilla's trap.  A
    # gate's slot: 1 for (+1,+1), 2 for the middle that shares its junction, 4 for the other
    # middle (after a pass through home), 5 for (-1,-1).  Tours run in slot order and every
    # data qubit takes its middle visitors in slot order, so all agree on who comes first.
    def offset(a: str, d: str) -> tuple[int, int]:
        return (int(round(coords[d][0] - coords[a][0])), int(round(coords[d][1] - coords[a][1])))

    junc: dict[int, str] = {}
    pairs: dict[str, list[tuple[int, str]]] = {}
    for a, items in prog.items():
        if roles[a] != "ancilla":
            continue
        ta = home[ion[a]]
        pairs[a] = []
        for it in items:
            if it[0] != "cx":
                continue
            k = it[1]
            d = cx[k][1] if cx[k][0] == a else cx[k][0]
            shared = set(ends[ta]) & set(ends[home[ion[d]]])
            if len(shared) != 1:
                raise Deadlock(f"ancilla {a} and data {d} share {len(shared)} junctions")
            junc[k] = next(iter(shared))
            pairs[a].append((k, d))
    near = None                   # the middle offset that shares a junction with (+1, +1)
    for a, ps in pairs.items():
        by_off = {offset(a, d): k for k, d in ps}
        if (1, 1) in by_off:
            for m in ((1, -1), (-1, 1)):
                if m in by_off and junc[by_off[m]] == junc[by_off[(1, 1)]]:
                    near = m
    if near is None:
        raise Deadlock("cannot tell which middle data shares a junction with the first")
    slot_of_off = {(1, 1): 1, near: 2, (-near[0], -near[1]): 4, (-1, -1): 5}
    if slots:
        slot_of_off.update(slots)
    tours: dict[str, list[tuple[str, int]]] = {}
    stop_of: dict[int, float] = {}
    for a, ps in pairs.items():
        tour = []
        for k, d in sorted(ps, key=lambda kd: (slot_of_off[offset(a, kd[1])], kd[0])):
            stop_of[k] = slot_of_off[offset(a, d)]
            tour.append((junc[k], k))
        tours[a] = tour

    # -- per-qubit operation lists in our order
    def expand(q: str, k: int) -> tuple[list, list]:
        c, t = cx[k]
        if q == c:
            return [("RY",), ("RX",)], [("RY",)]
        return [("RX",)], []

    scripts: dict[str, list] = {}
    for q, items in prog.items():
        i = ion[q]
        cxs = [it[1] for it in items if it[0] == "cx"]
        if roles[q] == "ancilla":
            order = [k for _, k in tours[q]]
        else:
            order = sorted(cxs, key=lambda k: (run_of[(q, k)], stop_of.get(k, 0), k))
        it_cx = iter(order)
        steps: list = []
        here = home[i]
        tour = {k: j for j, k in tours.get(q, [])}
        for it in items:
            if it[0] == "op":
                steps.append(("q", it[1]))
                continue
            k = next(it_cx)
            pre, post = expand(q, k)
            steps += [("q", p[0]) for p in pre]
            if roles[q] == "ancilla":
                d = cx[k][1] if cx[k][0] == q else cx[k][0]
                td = home[ion[d]]
                j = tour[k]
                if here != home[i] and j not in ends[here]:
                    # come home first, then out through the other end
                    jj = next(x for x in ends[here] if x in ends[home[i]])
                    steps.append(("hop", here, jj, home[i], None))
                    here = home[i]
                steps.append(("hop", here, j, td, k))
                here = td
            steps.append(("ms", k))
            steps += [("q", p[0]) for p in post]
            if roles[q] == "ancilla" and (q not in tours or k == tours[q][-1][1]):
                jj = next(x for x in ends[here] if x in ends[home[i]])
                steps.append(("hop", here, jj, home[i], None))
                here = home[i]
        scripts[i] = steps

    # -- the simulation
    ms_step: dict[tuple[str, int], int] = {}
    for i, st in scripts.items():
        for n, s in enumerate(st):
            if s[0] == "ms":
                ms_step[(i, s[1])] = n
    pos = {i: 0 for i in scripts}
    ready = {i: 0.0 for i in scripts}
    ms_wait: dict[int, tuple[str, float]] = {}
    ms_done: set[int] = set()
    on_ms: dict[int, list[str]] = defaultdict(list)
    on_depart: dict[str, list[str]] = defaultdict(list)
    q: list = []
    cnt = [0]

    def push(i: str, t: float) -> None:
        cnt[0] += 1
        heapq.heappush(q, (t, cnt[0], i))

    def emit(kind, t0, t1, ions, locks, **kw):
        sim.events.append((kind, t0, t1, tuple(ions), sorted(set(locks)), kw))

    def prev_ms(i: str, k: int) -> int | None:
        n = ms_step[(i, k)]
        ks = [s[1] for s in scripts[i][:n] if s[0] == "ms"]
        return ks[-1] if ks else None

    # each junction serves the ancilla that visits slots 1-2 through it before the one that
    # visits slots 4-5 through it (a boundary ancilla with nothing in slots 1-2 would
    # otherwise take the junction while its neighbour still needs it)
    first_user: dict[str, tuple[str, int]] = {}
    if junction_order:
        for a, tour in tours.items():
            js = {j for j, k in tour if stop_of[k] <= 2}
            for j in js:
                i_a = ion[a]
                last = max(n for n, x in enumerate(scripts[i_a]) if x[0] == "hop" and x[2] == j)
                first_user[j] = (i_a, last)
    on_pass: dict[tuple[str, int], list[str]] = defaultdict(list)

    def advance(x: str) -> None:
        n = pos[x]
        pos[x] = n + 1
        for w in on_pass.pop((x, n), []):
            push(w, ready[w])

    for i in scripts:
        push(i, 0.0)
    while q:
        t, _, i = heapq.heappop(q)
        if pos[i] >= len(scripts[i]):
            continue
        t = max(t, ready[i])
        st = scripts[i][pos[i]]
        trap = sim.loc[i]
        if st[0] == "q":
            kind = {"reset": "reset", "measure": "measure", "RX": "gate1", "RY": "gate1"}[st[1]]
            if kind == "measure" and any(x != i for x in sim.chain[trap]):
                # a 400 us measurement holds the trap: let the visitor leave first
                on_depart[trap].append(i)
                continue
            d = JONES_US[kind]
            locks = [f"ion:{i}", f"trap:{_num(trap)}"]
            s = sim._fit(t, [(c, 0, d) for c in locks])
            sim._take(s, [(c, 0, d) for c in locks])
            emit(kind, s, s + d, (i,), locks, at=trap, op=_OPNAME[st[1]])
            ready[i] = s + d
            advance(i)
            push(i, s + d)
            continue
        if st[0] == "ms":
            k = st[1]
            if k in ms_wait:
                o, _ = ms_wait.pop(k)
                if sim.loc[o] != trap:
                    raise Deadlock(f"MS {k}: {o} in {sim.loc[o]}, {i} in {trap}")
                d = JONES_US["gate"]
                a_, b_ = ion[cx[k][0]], ion[cx[k][1]]
                locks = [f"ion:{a_}", f"ion:{b_}", f"trap:{_num(trap)}"]
                s = sim._fit(max(t, ready[o]), [(c, 0, d) for c in locks])
                sim._take(s, [(c, 0, d) for c in locks])
                emit("gate", s, s + d, (a_, b_), locks, at=trap, op=_OPNAME["gate"])
                ms_done.add(k)
                for x in (i, o):
                    ready[x] = s + d
                    advance(x)
                    push(x, s + d)
                for w in on_ms.pop(k, []):
                    push(w, ready[w])
            else:
                ms_wait[k] = (i, t)
            continue
        # hop
        _, src, j, dst, k = st
        if src != trap:
            raise Deadlock(f"{i} is in {trap}, script says {src}")
        if k is not None:
            # a visitor goes only once its data has begun the gate before this one
            d_ion = ion[cx[k][1]] if ion[cx[k][0]] == i else ion[cx[k][0]]
            pk = prev_ms(d_ion, k)
            if pk is not None and pk not in ms_done:
                on_ms[pk].append(i)
                continue
        fu = first_user.get(j)
        if fu is not None and fu[0] != i and pos[fu[0]] <= fu[1]:
            on_pass[fu].append(i)
            continue
        s1, s2 = ends[src][j], ends[dst][j]
        side_out = dev.port(src, s1)
        ch = sim.chain[src]
        if not ch or ch[0 if side_out == "L" else -1] != i:
            raise Deadlock(f"{i} is not at the {side_out} end of {src}: {ch}")
        S, M = JONES_US["split"], JONES_US["merge"]
        T5, JE, JX = JONES_US["transit"], JONES_US["j_enter"], JONES_US["j_exit"]
        o1 = S
        o2 = o1 + T5
        o3 = o2 + JE
        o4 = o3 + JX
        o5 = o4 + T5
        o6 = o5 + M
        I, Ts, Td = f"ion:{i}", f"trap:{_num(src)}", f"trap:{_num(dst)}"
        G1, G2, Jn = f"segment:{_num(s1)}", f"segment:{_num(s2)}", f"junction:{_num(j)}"
        blocks = [(I, 0, o6), (Ts, 0, o1), (G1, 0, o3), (Jn, 0, o1), (Jn, o2, o4),
                  (G2, o3, o6), (Jn, o5, o6), (Td, o5, o6)]
        s = max(t, sim.sm_t[src], sim.sm_t[dst] - o5)
        for _ in range(1000):
            s = sim._fit(s, blocks)
            if sim.occupancy_ok(dst, s + o5):
                break
            # the destination is full: try from the next departure already scheduled
            later = sorted(x for x, dd in sim.occ_ev[dst] if dd < 0 and x > s + o5)
            if not later:
                s = None
                break
            s = later[0] - o5
        else:
            s = None
        if s is None:
            on_depart[dst].append(i)
            continue
        sim._take(s, blocks)
        emit("split", s, s + o1, (i,), [Ts, G1, Jn], at=src, seg=s1, op="Split")
        emit("transit", s + o1, s + o2, (i,), [G1], seg=s1, op="Move")
        emit("j_enter", s + o2, s + o3, (i,), [Jn, G1], src=s1, via=j, op="JunctionCrossing")
        emit("j_exit", s + o3, s + o4, (i,), [Jn, G2], via=j, dst=s2, op="JunctionCrossing")
        emit("transit", s + o4, s + o5, (i,), [G2], seg=s2, op="Move")
        emit("merge", s + o5, s + o6, (i,), [Td, G2, Jn], at=dst, seg=s2, op="Merge")
        ch.remove(i)
        side_in = dev.port(dst, s2)
        if side_in == "L":
            sim.chain[dst].insert(0, i)
        else:
            sim.chain[dst].append(i)
        sim.loc[i] = dst
        sim.sm_t[src] = s + o1
        sim.sm_t[dst] = s + o6
        sim.occ_ev[src].append((s + o1, -1))
        sim.occ_ev[dst].append((s + o5, +1))
        ready[i] = s + o6
        advance(i)
        push(i, s + o6)
        for w in on_depart.pop(src, []):
            push(w, ready[w])
    stuck = {i: scripts[i][pos[i]] for i in scripts if pos[i] < len(scripts[i])}
    if stuck:
        raise Deadlock(f"{len(stuck)} ions stuck, e.g. {list(stuck.items())[:4]}")
    for i, t in home.items():
        if sim.loc[i] != t:
            raise Deadlock(f"{i} ends in {sim.loc[i]}, not at home {t}")
    rows = sorted(sim.events, key=lambda r: (r[1], r[0] not in ("split", "merge")))
    events = []
    for n, (kind, t0, t1, ions, locks, kw) in enumerate(rows):
        op = kw.pop("op")
        events.append(Event(n, kind, t0, t1, ions, meta={"op": op, "locks": locks}, **kw))
    s = TimedSchedule(name=name, device=dev, chains={t: list(c) for t, c in chains.items()},
                      events=events, qubits=dict(ion),
                      source={"tool": "qccd.repro.sched_jones",
                              "plan": {"tours": {a: [k for _, k in tr] for a, tr in tours.items()}}})
    if verify:
        rep = check(s, JONES, duration=jones_duration, circuit=cx, strict=True)
        if not rep.ok:
            raise Deadlock(f"checker rejects: {rep.summary()['violations']} {rep.summary()['first'][:3]}")
    return s
