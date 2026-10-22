"""Our schedule for the rotated surface code on Jones et al.'s grid, under their model.

Jones & Murali (arXiv 2510.23519) compile one round of `surface_code:rotated_memory_z` onto
a grid of capacity-2 traps (one qubit per trap, a slot left for a visitor), with junctions
between them, and time it with their Table 1 (split/merge 80 us, shuttle 5, junction entry
and exit 50 each, MS 40, rotation 5, measurement 400, reset 50).  Their router sends each
ancilla to one data qubit and back for every CNOT and inserts a barrier after every routing
pass; it keeps each ancilla's CNOTs in circuit order but not each data qubit's.

Ours keeps the device, the qubit-to-trap mapping, the native decomposition (H -> RY RX;
CX(c,t) -> RY(c) RX(c) RX(t) MS RY(c); MR -> M then R) and every qubit's sequence of
operations exactly as the circuit writes it: each qubit meets its CNOT partners in the
circuit's order, even where two of its CNOTs commute.  That order is the circuit author's
decision, and a compiler that changes it has compiled a different circuit; what a schedule
may change is only when operations on different qubits happen relative to each other.  Then:

* every ancilla tours its data in its own circuit order instead of going home after each
  gate.  An ancilla's trap and each of its data qubits' traps share one junction, with up to
  two data qubits around each end of the ancilla's trap.  The ancilla goes straight from one data
  qubit to the next when both lie around the same end, and through its own (empty) trap
  when the next one lies around the other end; it comes home to be measured.  It always
  leaves a data trap through the end it came in by, so no gate swap is needed anywhere.  In
  stim's CX order one kind of ancilla (X or Z, depending on the distance's orientation)
  meets both data qubits of one end and then both of the other, 6 hops; the other kind
  alternates between the ends and needs 8, and its tours are what the round's length
  mostly comes from;
* nothing waits for a barrier: each operation starts as soon as its ions, its trap and every
  component the model says it holds are free (split and merge hold the junction at the far
  end of their segment, as the artifact declares; an entry or exit holds the junction and the
  segment).  Three rules keep the list scheduler out of its own way: a visitor sets off only
  once its data qubit has begun the gate before this one (with one slot per data trap, that
  is what makes every data qubit see its visitors in circuit order); a data qubit is measured
  only once no visitor is left in its trap (a measurement holds the trap 400 us); and each
  junction passes ions in the order of the circuit's layers -- a hop toward a CNOT of layer n
  before any toward a later one, a hop home after a layer-n CNOT counting as n + 1/2, and of
  two hops home of one rank, the one whose ion has more trips ahead goes first.

Every event carries the components it holds (``meta["locks"]``, in the artifact's naming), and
`build` returns a schedule only after `timed.check` accepted it under the JONES profile with
strict durations and the circuit, with each qubit's CNOTs in exactly the circuit's order
(``circuit_order="strict"``).
"""

from __future__ import annotations

import heapq
import re
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Mapping, Sequence

from .models import JONES, JONES_US, jones_duration
from .sched import _Busy
from .timed import Event, PortGraph, TimedSchedule, check

__all__ = ["stim_program", "layers", "build", "publish", "Deadlock"]

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


def layers(cx: Sequence[tuple[str, str]]) -> list[int]:
    """Each CNOT's depth in the circuit: one more than the deepest earlier CNOT on either of
    its qubits (for stim's surface-code rounds, the CX layer it was written in)."""
    depth: dict[str, int] = defaultdict(int)
    out = []
    for c, t in cx:
        n = max(depth[c], depth[t]) + 1
        depth[c] = depth[t] = n
        out.append(n)
    return out


def build(dev: PortGraph, chains: Mapping[str, list[str]], qubits: Mapping[str, str],
          stim: str | Path, *, name: str = "jones_ours", verify: bool = True,
          junction_order: bool = True) -> TimedSchedule:
    cx, prog, _ = stim_program(stim)
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
    layer = layers(cx)

    # -- each ancilla's tour: its CNOTs in the circuit's order, each through the one junction
    # its trap shares with that data qubit's trap
    junc: dict[int, str] = {}
    tours: dict[str, list[tuple[str, int]]] = {}
    for a, items in prog.items():
        if roles[a] != "ancilla":
            continue
        ta = home[ion[a]]
        tours[a] = []
        for it in items:
            if it[0] != "cx":
                continue
            k = it[1]
            d = cx[k][1] if cx[k][0] == a else cx[k][0]
            shared = set(ends[ta]) & set(ends[home[ion[d]]])
            if len(shared) != 1:
                raise Deadlock(f"ancilla {a} and data {d} share {len(shared)} junctions")
            junc[k] = next(iter(shared))
            tours[a].append((junc[k], k))

    # -- per-qubit operation lists, in the circuit's order (nothing is reordered)
    def expand(q: str, k: int) -> tuple[list, list]:
        c, t = cx[k]
        if q == c:
            return [("RY",), ("RX",)], [("RY",)]
        return [("RX",)], []

    scripts: dict[str, list] = {}
    for q, items in prog.items():
        i = ion[q]
        steps: list = []
        here = home[i]
        tour = {k: j for j, k in tours.get(q, [])}
        last = tours[q][-1][1] if tours.get(q) else None
        for it in items:
            if it[0] == "op":
                steps.append(("q", it[1]))
                continue
            k = it[1]
            pre, post = expand(q, k)
            steps += [("q", p[0]) for p in pre]
            if roles[q] == "ancilla":
                d = cx[k][1] if cx[k][0] == q else cx[k][0]
                td = home[ion[d]]
                j = tour[k]
                if here != home[i] and j not in ends[here]:
                    # the next data qubit lies at the other end: through home and out again
                    jj = next(x for x in ends[here] if x in ends[home[i]])
                    steps.append(("hop", here, jj, home[i], None))
                    here = home[i]
                steps.append(("hop", here, j, td, k))
                here = td
            steps.append(("ms", k))
            steps += [("q", p[0]) for p in post]
            if roles[q] == "ancilla" and k == last:
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

    # each junction passes ions in the order of the circuit's layers.  A hop toward a CNOT of
    # layer n has rank n, a hop home after one n + 1/2; a hop goes only once every other ion's
    # hop through that junction of a lower rank has been scheduled.  Between two hops home of
    # one rank, the ion with more trips still ahead goes first (it has less slack).
    rank: dict[tuple[str, int], tuple[float, int]] = {}
    by_junction: dict[str, list[tuple[tuple[float, int], str, int]]] = defaultdict(list)
    if junction_order:
        for i, st in scripts.items():
            done = 0.0
            hops = [n for n, x in enumerate(st) if x[0] == "hop"]
            for n, x in enumerate(st):
                if x[0] == "ms":
                    done = layer[x[1]] + 0.5
                elif x[0] == "hop":
                    r = ((float(layer[x[4]]), 0) if x[4] is not None
                         else (done, -sum(1 for m in hops if m > n)))
                    rank[(i, n)] = r
                    by_junction[x[2]].append((r, i, n))
        for v in by_junction.values():
            v.sort()
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
        r = rank.get((i, pos[i]))
        if r is not None:
            ahead = None
            for rr, x, n in by_junction[j]:
                if rr >= r:
                    break
                if x != i and pos[x] <= n:
                    ahead = (x, n)
                    break
            if ahead is not None:
                on_pass[ahead].append(i)
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
        for prof in (JONES, replace(JONES, circuit_order="strict")):
            rep = check(s, prof, duration=jones_duration, circuit=cx, strict=True)
            if not rep.ok:
                raise Deadlock(f"checker rejects ({prof.circuit_order} circuit order): "
                               f"{rep.summary()['violations']} {rep.summary()['first'][:3]}")
    return s


def publish(out: str | Path | None = None, distances: Sequence[int] = (2, 3, 6, 12)) -> list[Path]:
    """Write our schedule for each grid run on the reproduction page, as it is published:
    built on the artifact's own device, placement and qubit-to-ion map, saved as
    ``grid_rot_d<d>_c2_ours.schedule.json.gz`` in ``Reproduce/jones2025/ours`` (or ``out``),
    gzip'd with no timestamp so that a rebuild is identical byte for byte."""
    import gzip
    import json

    from . import importers
    from .__main__ import ROOT, _open

    base = ROOT / "jones2025"
    out = Path(out) if out is not None else base / "ours"
    written = []
    for d in distances:
        ref = importers.import_jones(_open(base / "artifact" / f"grid_rot_d{d}_c2.schedule.json.gz"))
        s = build(ref.device, ref.chains, ref.qubits,
                  base / "circuits" / f"rotated_memory_z_d{d}_r1.stim", name=f"grid_rot_d{d}_c2_ours")
        p = out / f"grid_rot_d{d}_c2_ours.schedule.json.gz"
        p.write_bytes(gzip.compress(json.dumps(s.to_json(), separators=(",", ":")).encode("utf-8"),
                                    compresslevel=9, mtime=0))
        written.append(p)
    return written


if __name__ == "__main__":
    for p in publish():
        print(p)
