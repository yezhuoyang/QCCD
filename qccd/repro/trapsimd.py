"""TrapSIMD / FluxTrap (Ruan et al., arXiv 2504.17886) in the timed checker.

The paper has no artifact, so its reproduction has two parts:

* its two fully specified worked examples (Fig. 6, Fig. 7), written event for event and
  replayed EXACTLY -- 982 / 732 us and 823 / 545 us;
* model level: its device (a D x D grid of cells, one linear trap of L positions on every
  internal lattice edge, X-junctions), its timing table (Table 1) and its broadcast rule (one
  JT-SIMD class per inter-trap cycle), with circuits rebuilt from its description and
  scheduled by OUR compiler (`schedule_ours`).  The circuits are not the authors' circuits.

Our compiler lays a line of ions along a trail of traps chosen to need as few JT-SIMD classes
as possible (`trap_trail`) and runs line rounds on it (`LineExecutor`): the odd-even
transposition network for the all-pairs circuits (`all_pairs_rounds`: QAOA on the complete
graph in n rounds, VQE's full entanglement in 2n - 3, its dependency depth), and a
first-come-first-served line router for the rest (`lnn_rounds`: BV, the ripple-carry adder).

How the model is written down (MODEL.md in ``Reproduce/ruan2025`` lists every primitive):

    device     every trap POSITION is a site of capacity 1 ({"zone": "gate" | "aux"}); the
               positions of a trap are chained by segments; the edge position of a trap is
               joined to the junction node it faces.  Gate zones inside a trap are not placed
               by the paper: `gate_zones` spaces them evenly, which reproduces both worked
               examples' layouts (L=3, one zone: the middle; L=7, three zones: 1, 3, 5).
    intra      an intra-trap shift (58 us) of one ion to the neighbouring position is a
               ``split`` onto the segment between the two positions (29 us) and a ``merge``
               into the next one (29 us); an intra-trap swap (200 us) is the same for two
               ions crossing on one segment (100 + 100 us).  See "Why half-steps" below.
    inter      an inter-trap shift is a ``hop`` from the edge position of one trap, through
               the junction node, to the empty edge position of another, 250 us, with
               ``meta["class"]`` its JT-SIMD class: the direction the ion moves into the
               junction and the direction it leaves in, e.g. "D->R" (the paper's DR-SH).
               The core checker's ``broadcast`` rule makes every hop of a cycle carry one
               class and start and end together.
    gates      a two-qubit gate acts on two ions in adjacent positions of one trap, one of
               them in a gate zone (Fig. 2(a)), and lasts the EFFECTIVE 141 us = 58 + 25 + 58
               (shift in, gate, shift out) that both worked examples need; Table 1's bare
               25 us is kept as a parameter.  A one-qubit gate (5 us) acts in a gate zone.

Why half-steps.  The core checker holds a ``hop``'s source until the hop ends and its
destination from the hop's start, which is right for one ion entering an empty position but
makes the paper's lockstep S3 group shift (Q4 and Q5 in Fig. 7: Q5 moves into the position
Q4 is leaving, at the same instant) a capacity violation, and it has no event for two ions
exchanging two positions (``swap`` reorders ions inside ONE site).  A split releases its
position when it ends and a merge takes the next one when it starts, so the split/merge pair
expresses both, with the two half-steps' sum equal to the paper's latency.  This module's
own `simd_rules` checks what that representation leaves open: a split is followed at once by
the merge into the neighbouring position of the same trap, and two ions share a segment only
as the two halves of one swap.

TrapSIMD rules the core checker does not have, checked here (`simd_rules`):

    intra    half-steps pair up as above; positions are neighbours in one trap
    inter    a hop goes edge -> junction -> edge of another arm of that junction, and its
             class is the one those two arms define
    modes    inter-trap cycles never overlap one another (one waveform at a time), and no
             other event overlaps a cycle: the paper waits for every in-flight intra-trap
             operation, gates included (Figs. 6(b2) and 6(c2) start a cycle only after the
             gates end), before it issues inter-trap transport
    zones    two- and one-qubit gates name a gate-zone position; a swap has one of its ions
             in a gate zone (`Rules.swap_gz`: not stated, but every swap the paper draws is
             one); optionally at most one gate at a time per trap (`Rules.one_gate_per_trap`)
    program  with the circuit: every operation executed once, on its qubits, in an order the
             circuit allows (runs of CX gates in which a qubit keeps its role commute; a
             one-qubit gate is a barrier for its qubit; ZZ gates commute)
"""

from __future__ import annotations

import dataclasses
import json
import math
import random
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .timed import Context, Event, PortGraph, Profile, Report, TimedSchedule, Violation, check

__all__ = [
    "ARMS", "IN_DIR", "OUT_DIR", "SHIFT_CLASSES",
    "Trap", "Device", "gate_zones", "grid_device", "w1_device", "w2_device",
    "TRAPSIMD", "TRAPSIMD_US", "trapsimd_duration", "Rules", "simd_rules", "check_trapsimd",
    "summarize", "fidelity",
    "Recorder",
    "w1_eager", "w1_batched", "w2_depth", "w2_sliced",
    "Op", "Program", "vqe", "qaoa_complete", "qaoa_regular", "bv", "rca", "w1_program",
    "w2_program",
    "trap_trail", "all_pairs_rounds", "LineExecutor", "schedule_line", "lnn_rounds",
    "schedule_lnn", "line_order", "schedule_ours",
    "TABLE3", "TABLE2_D", "PAPER_2Q", "PAPER_META", "reproduce",
]

# --------------------------------------------------------------------------- device

#: A junction's arms.  Rows grow downward (south), columns rightward (east).
ARMS = ("N", "E", "S", "W")
#: The direction an ion moves when it enters a junction from that arm ...
IN_DIR = {"N": "D", "S": "U", "E": "L", "W": "R"}
#: ... and when it leaves the junction into that arm.
OUT_DIR = {"N": "U", "S": "D", "E": "R", "W": "L"}
_OPP = {"U": "D", "D": "U", "L": "R", "R": "L"}
#: The 12 JT-SIMD shift classes (Sec. 3.1): every (in, out) pair but the U-turns.  The
#: paper's "DR-SH" (Fig. 6) is "D->R": down into the junction, right out of it.
SHIFT_CLASSES = tuple(f"{a}->{b}" for a in "UDLR" for b in "UDLR" if b != _OPP[a])


@dataclass(frozen=True)
class Trap:
    """A linear trap: ``L`` positions in a row, gate zones at ``gz``.

    ``ends`` maps a junction to (the trap's edge position facing it, the junction's arm the
    trap lies on).  Positions are numbered west->east for a horizontal trap and
    north->south for a vertical one.
    """

    id: str
    L: int
    gz: tuple[int, ...]
    ends: Mapping[str, tuple[int, str]]


@dataclass
class Device:
    name: str
    traps: dict[str, Trap]
    #: junction -> arm -> trap
    arms: dict[str, dict[str, str]]
    params: dict = field(default_factory=dict)

    @staticmethod
    def site(trap: str, i: int) -> str:
        return f"{trap}.{i}"

    def edge(self, trap: str, junction: str) -> int:
        return self.traps[trap].ends[junction][0]

    def arm(self, trap: str, junction: str) -> str:
        return self.traps[trap].ends[junction][1]

    def klass(self, src: str, junction: str, dst: str) -> str:
        """The JT-SIMD class of moving an ion from trap ``src`` to ``dst`` through
        ``junction``."""
        return f"{IN_DIR[self.arm(src, junction)]}->{OUT_DIR[self.arm(dst, junction)]}"

    @property
    def n_positions(self) -> int:
        return sum(t.L for t in self.traps.values())

    def portgraph(self) -> PortGraph:
        sites: dict[str, dict] = {}
        segments: dict[str, tuple[str, str]] = {}
        adjacent: dict[str, list[str]] = {}
        for t in self.traps.values():
            for i in range(t.L):
                sites[self.site(t.id, i)] = {"capacity": 1, "zone": "gate" if i in t.gz else "aux",
                                             "trap": t.id, "index": i, "ports": {}}
            for i in range(t.L - 1):
                seg = f"{t.id}.{i}|{i + 1}"
                segments[seg] = (self.site(t.id, i), self.site(t.id, i + 1))
                sites[self.site(t.id, i)]["ports"][seg] = "R"
                sites[self.site(t.id, i + 1)]["ports"][seg] = "L"
            for j, (cell, arm) in t.ends.items():
                seg = f"{t.id}~{j}"
                segments[seg] = (self.site(t.id, cell), j)
                s = sites[self.site(t.id, cell)]
                s["ports"][seg] = "L" if cell == 0 and t.L > 1 else "R"
                s.setdefault("junctions", {})[j] = arm
            for g in t.gz:
                adjacent[self.site(t.id, g)] = [self.site(t.id, k) for k in (g - 1, g + 1)
                                                if 0 <= k < t.L]
        nodes = {j: {"arms": dict(a)} for j, a in self.arms.items()}
        return PortGraph(sites=sites, nodes=nodes, segments=segments, adjacent=adjacent)


def gate_zones(L: int, n: int) -> tuple[int, ...]:
    """Where ``n`` gate zones sit in a trap of ``L`` positions.  The paper does not say;
    this spaces them evenly, ``round(k (L+1) / (n+1)) - 1`` for k = 1..n, which gives the
    worked examples' traps (L=3, n=1 -> (1,); L=7, n=3 -> (1, 3, 5)) and L=8, n=2 -> (2, 5),
    L=14, n=2 -> (4, 9)."""
    if n <= 0:
        return ()
    out = tuple(int(math.floor(k * (L + 1) / (n + 1) + 0.5)) - 1 for k in range(1, n + 1))
    if len(set(out)) != n or min(out) < 0 or max(out) >= L:
        raise ValueError(f"cannot place {n} gate zones in {L} positions")
    return out


def grid_device(D: int, L: int, n_gz: int = 2, gz: Sequence[int] | None = None) -> Device:
    """The paper's D x D grid (Table 2, Fig. 3(c,d), ``fig/DxD_layout.pdf``): (D+1)^2
    X-junctions ``J{r}{c}``; a horizontal trap ``H{r}{c}`` between J(r,c) and J(r,c+1) and a
    vertical trap ``V{r}{c}`` between J(r,c) and J(r+1,c) on every internal lattice edge,
    2D(D+1) traps.  The boundary junctions' outer arms have no trap."""
    zones = tuple(gz) if gz is not None else gate_zones(L, n_gz)
    traps: dict[str, Trap] = {}
    arms: dict[str, dict[str, str]] = {f"J{r}{c}": {} for r in range(D + 1) for c in range(D + 1)}
    for r in range(D + 1):
        for c in range(D):
            tid = f"H{r}{c}"
            west, east = f"J{r}{c}", f"J{r}{c + 1}"
            traps[tid] = Trap(tid, L, zones, {west: (0, "E"), east: (L - 1, "W")})
            arms[west]["E"] = tid
            arms[east]["W"] = tid
    for r in range(D):
        for c in range(D + 1):
            tid = f"V{r}{c}"
            north, south = f"J{r}{c}", f"J{r + 1}{c}"
            traps[tid] = Trap(tid, L, zones, {north: (0, "S"), south: (L - 1, "N")})
            arms[north]["S"] = tid
            arms[south]["N"] = tid
    centers = {f"H{r}{c}": (float(r), c + 0.5) for r in range(D + 1) for c in range(D)}
    centers.update({f"V{r}{c}": (r + 0.5, float(c)) for r in range(D) for c in range(D + 1)})
    return Device(f"grid{D}x{D}_L{L}_gz{len(zones)}", traps, arms,
                  {"D": D, "L": L, "gate_zones": list(zones), "centers": centers})


def w1_device() -> Device:
    """Fig. 6(a2): two X-junctions; traps of three positions with the gate zone in the
    middle, and the left junction's west arm a single auxiliary position."""
    t = {
        "N1": Trap("N1", 3, (1,), {"J1": (2, "N")}),
        "S1": Trap("S1", 3, (1,), {"J1": (0, "S")}),
        "W1": Trap("W1", 1, (), {"J1": (0, "W")}),
        "M": Trap("M", 3, (1,), {"J1": (0, "E"), "J2": (2, "W")}),
        "N2": Trap("N2", 3, (1,), {"J2": (2, "N")}),
        "S2": Trap("S2", 3, (1,), {"J2": (0, "S")}),
        "E2": Trap("E2", 3, (1,), {"J2": (0, "E")}),
    }
    arms = {"J1": {"N": "N1", "S": "S1", "W": "W1", "E": "M"},
            "J2": {"N": "N2", "S": "S2", "W": "M", "E": "E2"}}
    return Device("fig6", t, arms, {"source": "Fig. 6(a2)"})


def w2_device() -> Device:
    """Fig. 7(a2): one vertical trap of seven positions, gate zones at 1, 3, 5."""
    return Device("fig7", {"T": Trap("T", 7, (1, 3, 5), {})}, {}, {"source": "Fig. 7(a2)"})


# ----------------------------------------------------------------------------- model

#: Table 1 latencies (us), plus the effective two-qubit latency the worked examples need.
TRAPSIMD_US = {
    "gate1": 5.0,
    "gate2_table": 25.0,          # Table 1, the bare MS gate
    "gate2_effective": 141.0,     # 58 + 25 + 58: shift in, gate, shift out (Sec. 2.1, Fig. 2(a))
    "measure": 120.0,
    "intra_shift": 58.0,
    "intra_swap": 200.0,
    "inter_shift": 250.0,
    "inter_swap": 500.0,
}

#: Table 1 fidelities; T_coh = 600 s (Sec. 6.1).
TRAPSIMD_F = {"gate1": 0.999975, "gate2": 0.9982, "measure": 0.9984, "intra_shift": 0.99978,
              "intra_swap": 0.99978, "inter_shift": 0.99956, "inter_swap": 0.99912,
              "t_coh_us": 600e6}

#: The rules the paper states.  Inside a trap nothing is serial (single-site addressing:
#: Figs. 6-7 run gates, shifts and swaps of one trap at once); a junction passes one ion at
#: a time; the inter-trap shift is broadcast.  Circuit order: CX runs commute (Fig. 7's
#: time-sliced schedule runs CX(Q3,Q4) before CX(Q3,Q1), "actually commutable").
TRAPSIMD = Profile(
    "trapsimd",
    trap_serial=(),
    segment_mutex=False,          # a swap puts two ions on one segment; `simd_rules` checks the rest
    junction_mutex=True,
    chain_order=None,             # one ion per position: the position IS the order
    gate_locality="adjacent",     # PortGraph.adjacent: a gate zone -> its two neighbours
    circuit_order="commuting",
    broadcast=("hop",),
)

_HALF = {"shift": TRAPSIMD_US["intra_shift"] / 2, "swap": TRAPSIMD_US["intra_swap"] / 2}


def trapsimd_duration(tg: float = TRAPSIMD_US["gate2_effective"]) -> Callable[[Event, Context], float | None]:
    """Table 1 as a duration law.  ``tg`` is the two-qubit latency: 141 (effective, what the
    worked examples need) or 25 (Table 1's bare gate)."""

    def law(e: Event, ctx: Context) -> float | None:
        if e.kind == "gate":
            return tg
        if e.kind == "gate1":
            return TRAPSIMD_US["gate1"]
        if e.kind == "measure":
            return TRAPSIMD_US["measure"]
        if e.kind in ("split", "merge"):
            return _HALF.get(str(e.meta.get("op")))
        if e.kind == "hop":
            return TRAPSIMD_US["inter_shift"]
        return None

    return law


@dataclass(frozen=True)
class Rules:
    """The TrapSIMD rules `simd_rules` enforces beside the core checker.

    ``inter_exclusive``  "all": no event overlaps an inter-trap cycle (the paper's LRT wait
                         covers gates too, Fig. 6); "transport": only intra-trap transport
    ``gz_1q``            a one-qubit gate needs its ion in a gate zone (Sec. 2.1)
    ``swap_gz``          an intra-trap swap has one of its two ions in a gate zone.  NOT
                         stated; every swap the paper draws (Figs. 2(a), 6, 7) is one, so it
                         is on by default
    ``one_gate_per_trap`` at most one gate at a time in a trap.  NOT stated (the paper's
                         traps have two gate zones); off by default, a sensitivity
    """

    inter_exclusive: str = "all"
    gz_1q: bool = True
    swap_gz: bool = True
    one_gate_per_trap: bool = False


# -------------------------------------------------------------------- event builders


class Recorder:
    """Builds TrapSIMD events in the representation described in the module docstring."""

    def __init__(self, dev: Device):
        self.dev = dev
        self.events: list[Event] = []
        self._id = 0
        self._group = 0

    def _add(self, kind: str, t0: float, t1: float, ions: tuple[str, ...], **kw) -> Event:
        e = Event(self._id, kind, float(t0), float(t1), ions, **kw)
        self._id += 1
        self.events.append(e)
        return e

    def shift(self, trap: str, moves: Sequence[tuple[str, int, int]], t: float) -> None:
        """One S3 instruction: every (ion, from, to) moves one position at once, 58 us."""
        g = self._group
        self._group += 1
        h = _HALF["shift"]
        for ion, i, j in moves:
            if abs(i - j) != 1:
                raise ValueError(f"shift of {ion} from {i} to {j}: not neighbours")
            seg = f"{trap}.{min(i, j)}|{max(i, j)}"
            meta = {"op": "shift", "group": g, "trap": trap}
            self._add("split", t, t + h, (ion,), at=self.dev.site(trap, i), seg=seg, meta=meta)
            self._add("merge", t + h, t + 2 * h, (ion,), at=self.dev.site(trap, j), seg=seg,
                      meta=meta)

    def swap(self, trap: str, a: str, i: int, b: str, j: int, t: float) -> None:
        """An intra-trap swap of the ions in neighbouring positions i and j, 200 us."""
        if abs(i - j) != 1:
            raise ValueError(f"swap of positions {i} and {j}: not neighbours")
        g = self._group
        self._group += 1
        h = _HALF["swap"]
        seg = f"{trap}.{min(i, j)}|{max(i, j)}"
        for ion, src, dst in ((a, i, j), (b, j, i)):
            meta = {"op": "swap", "group": g, "trap": trap}
            self._add("split", t, t + h, (ion,), at=self.dev.site(trap, src), seg=seg, meta=meta)
            self._add("merge", t + h, t + 2 * h, (ion,), at=self.dev.site(trap, dst), seg=seg,
                      meta=meta)

    def cycle(self, moves: Sequence[tuple[str, str, str, str]], t: float) -> str:
        """One JT-SIMD shift instruction: every (ion, src trap, junction, dst trap) crosses
        its junction at once, 250 us.  Returns the class (all moves must share it)."""
        classes = {self.dev.klass(a, j, b) for _, a, j, b in moves}
        if len(classes) != 1:
            raise ValueError(f"one JT-SIMD cycle, several classes: {sorted(classes)}")
        cls = classes.pop()
        g = self._group
        self._group += 1
        d = TRAPSIMD_US["inter_shift"]
        for ion, a, j, b in moves:
            self._add("hop", t, t + d, (ion,), src=self.dev.site(a, self.dev.edge(a, j)),
                      dst=self.dev.site(b, self.dev.edge(b, j)), path=(j,),
                      meta={"class": cls, "op": "jt_shift", "group": g})
        return cls

    def gate(self, a: str, b: str, trap: str, gz: int, t: float, dur: float, op: int | None = None,
             name: str = "cx") -> None:
        meta: dict[str, Any] = {"name": name}
        if op is not None:
            meta["op"] = op
        self._add("gate", t, t + dur, (a, b), at=self.dev.site(trap, gz), meta=meta)

    def gate1(self, q: str, trap: str, i: int, t: float, op: int | None = None,
              name: str = "u") -> None:
        meta: dict[str, Any] = {"name": name}
        if op is not None:
            meta["op"] = op
        self._add("gate1", t, t + TRAPSIMD_US["gate1"], (q,), at=self.dev.site(trap, i), meta=meta)


# ----------------------------------------------------------------- the TrapSIMD rules


def simd_rules(sched: TimedSchedule, rules: Rules = Rules(),
               program: "Program | None" = None) -> tuple[dict[str, str], list[Violation]]:
    """The rules the core checker does not express (module docstring).  Reads only the
    schedule: its PortGraph carries the trap, zone and junction-arm of every position."""
    dev = sched.device
    out: list[Violation] = []

    def v(name: str, msg: str, *ids: int) -> None:
        out.append(Violation(name, msg, tuple(ids)))

    info = dev.sites
    arms_of: dict[str, dict[str, str]] = {j: {t: a for a, t in n.get("arms", {}).items()}
                                         for j, n in dev.nodes.items()}
    evs = sorted(sched.events, key=lambda e: (e.t0, e.id))
    tol = 1e-6

    # -- intra: half-steps --------------------------------------------------------------
    nxt: dict[int, Event] = {}
    by_ion: dict[str, list[Event]] = defaultdict(list)
    for e in evs:
        for ion in e.ions:
            by_ion[ion].append(e)
    for ion, es in by_ion.items():
        for a, b in zip(es, es[1:]):
            if a.kind == "split":
                nxt[a.id] = b
        if es and es[-1].kind == "split":
            nxt[es[-1].id] = None  # type: ignore[assignment]
    seg_use: dict[str, list[tuple[float, float, Event, Event]]] = defaultdict(list)
    for e in evs:
        if e.kind == "merge" and not any(s.kind == "split" and nxt.get(s.id) is e
                                         for s in by_ion[e.ions[0]]):
            v("intra", f"merge {e.id} of {e.ions[0]} follows no split", e.id)
        if e.kind != "split":
            continue
        op = e.meta.get("op")
        seg = dev.segments.get(e.seg or "")
        m = nxt.get(e.id)
        if op not in ("shift", "swap"):
            v("intra", f"split {e.id}: op {op!r} is neither shift nor swap", e.id)
            continue
        if seg is None or any(s not in info for s in seg):
            v("intra", f"split {e.id}: {e.seg} is not a segment between two positions", e.id)
            continue
        a, b = seg
        if info[a]["trap"] != info[b]["trap"] or abs(info[a]["index"] - info[b]["index"]) != 1:
            v("intra", f"split {e.id}: {e.seg} does not join neighbouring positions of one trap", e.id)
            continue
        other = b if e.at == a else a
        if m is None or m.kind != "merge" or m.seg != e.seg or m.at != other:
            v("intra", f"split {e.id} of {e.ions[0]} is not followed by its merge into {other}", e.id)
            continue
        if abs(m.t0 - e.t1) > tol or m.meta.get("op") != op:
            v("intra", f"split {e.id} and merge {m.id} of one {op} do not join up", e.id, m.id)
            continue
        seg_use[e.seg].append((e.t0, m.t1, e, m))
    for seg, uses in seg_use.items():
        uses.sort(key=lambda u: (u[0], u[2].id))
        for x, (t0, t1, e, m) in enumerate(uses):
            for t0b, t1b, eb, mb in uses[x + 1:]:
                if t0b >= t1 - tol:
                    break
                pair = (e.meta.get("op") == "swap" and eb.meta.get("op") == "swap"
                        and abs(t0 - t0b) <= tol and abs(t1 - t1b) <= tol and e.at != eb.at)
                if not pair:
                    v("intra", f"{e.ions[0]} and {eb.ions[0]} share segment {seg} at "
                      f"[{t0b:g}, {min(t1, t1b):g}) us without being one swap", e.id, eb.id)

    # -- inter: hops ------------------------------------------------------------------------
    cycles: dict[float, list[Event]] = defaultdict(list)
    for e in evs:
        if e.kind != "hop":
            continue
        cycles[e.t0].append(e)
        if len(e.path) != 1 or e.path[0] not in dev.nodes:
            v("inter", f"hop {e.id} crosses {list(e.path)}, not one junction", e.id)
            continue
        j = e.path[0]
        s, d = info.get(e.src or ""), info.get(e.dst or "")
        if s is None or d is None:
            v("inter", f"hop {e.id}: {e.src} -> {e.dst} are not positions", e.id)
            continue
        if j not in s.get("junctions", {}) or j not in d.get("junctions", {}):
            v("inter", f"hop {e.id}: {e.src} -> {e.dst} are not both edge positions facing {j}", e.id)
            continue
        if s["trap"] == d["trap"]:
            v("inter", f"hop {e.id} returns to its own trap {s['trap']}", e.id)
            continue
        want = f"{IN_DIR[arms_of[j][s['trap']]]}->{OUT_DIR[arms_of[j][d['trap']]]}"
        if e.meta.get("class") != want:
            v("inter", f"hop {e.id} through {j} is class {want}, labelled {e.meta.get('class')!r}", e.id)

    # -- modes: one waveform at a time, nothing during it ----------------------------------------
    spans = sorted((t0, max(x.t1 for x in es), es) for t0, es in cycles.items())
    for (a0, a1, ea), (b0, b1, eb) in zip(spans, spans[1:]):
        if b0 < a1 - tol:
            v("modes", f"inter-trap cycles at {a0:g} and {b0:g} us overlap", ea[0].id, eb[0].id)
    blocked = (("split", "merge", "gate", "gate1", "measure") if rules.inter_exclusive == "all"
               else ("split", "merge"))
    starts = [s[0] for s in spans]
    import bisect
    for e in evs:
        if e.kind not in blocked:
            continue
        k = bisect.bisect_right(starts, e.t1 - tol) - 1
        while k >= 0 and spans[k][1] > e.t0 + tol:
            c0, c1, ce = spans[k]
            if c0 < e.t1 - tol and e.t0 < c1 - tol:
                v("modes", f"{e.kind} {e.id} [{e.t0:g}, {e.t1:g}) overlaps the inter-trap cycle at "
                  f"{c0:g} us", e.id, ce[0].id)
                break
            k -= 1

    # -- zones ----------------------------------------------------------------------------------
    gate_iv: dict[str, list[tuple[float, float, int]]] = defaultdict(list)
    for e in evs:
        if e.kind == "gate":
            z = info.get(e.at or "", {})
            if z.get("zone") != "gate":
                v("zones", f"gate {e.id} at {e.at}: not a gate zone", e.id)
            if z:
                gate_iv[z["trap"]].append((e.t0, e.t1, e.id))
        elif e.kind == "gate1" and rules.gz_1q:
            if info.get(e.at or "", {}).get("zone") != "gate":
                v("zones", f"one-qubit gate {e.id} at {e.at}: not a gate zone", e.id)
    if rules.swap_gz:
        for e in evs:
            if e.kind == "split" and e.meta.get("op") == "swap":
                seg = dev.segments.get(e.seg or "", ())
                if seg and not any(info.get(x, {}).get("zone") == "gate" for x in seg):
                    v("zones", f"swap {e.id} of {e.ions[0]} on {e.seg}: neither position is a gate zone", e.id)
    if rules.one_gate_per_trap:
        for trap, ivs in gate_iv.items():
            ivs.sort()
            for (a0, a1, ia), (b0, b1, ib) in zip(ivs, ivs[1:]):
                if b0 < a1 - tol:
                    v("zones", f"two gates at once in trap {trap}", ia, ib)

    # -- program --------------------------------------------------------------------------------
    if program is not None:
        _program_rule(sched, program, evs, v)

    names = ["intra", "inter", "modes", "zones"] + (["program"] if program is not None else [])
    by = Counter(x.check for x in out)
    checks = {n: ("failed" if by.get(n) else "passed") for n in names}
    if program is None:
        checks["program"] = "skipped: no program given"
    return checks, out


def _program_rule(sched: TimedSchedule, prog: "Program", evs: list[Event], v) -> None:
    ion_of = sched.qubits or {}
    to_ion = lambda q: ion_of.get(prog.qname(q), prog.qname(q))
    seen: dict[int, Event] = {}
    per_q: dict[int, list[tuple[float, int, int]]] = defaultdict(list)
    for e in evs:
        if e.kind not in ("gate", "gate1"):
            continue
        k = e.meta.get("op")
        if k is None or not (0 <= int(k) < len(prog.ops)):
            v("program", f"{e.kind} {e.id} names no operation of the program", e.id)
            continue
        k = int(k)
        op = prog.ops[k]
        if k in seen:
            v("program", f"operation {k} ({op.name}) executed twice", seen[k].id, e.id)
            continue
        seen[k] = e
        want = {to_ion(q) for q in op.qubits}
        if set(e.ions) != want or len(e.ions) != len(op.qubits):
            v("program", f"{e.kind} {e.id} acts on {list(e.ions)}, operation {k} on {sorted(want)}", e.id)
        for q in op.qubits:
            per_q[q].append((e.t0, e.id, k))
    missing = [k for k in range(len(prog.ops)) if k not in seen]
    if missing:
        v("program", f"{len(missing)} operations never executed (e.g. "
          f"{[(k, prog.ops[k].name, prog.ops[k].qubits) for k in missing[:4]]})")
    bidx = prog.block_index()
    for q, xs in per_q.items():
        xs.sort()
        last = -1
        for t0, eid, k in xs:
            b = bidx[k][q]
            if b < last:
                v("program", f"qubit {q}: operation {k} ({prog.ops[k].name}) runs after a later "
                  f"block of that qubit", eid)
                break
            last = b


def check_trapsimd(sched: TimedSchedule, *, tg: float = TRAPSIMD_US["gate2_effective"],
                   rules: Rules = Rules(), program: "Program | None" = None,
                   profile: Profile | None = None) -> Report:
    """The core checker under `TRAPSIMD` (circuit order from the program) plus `simd_rules`,
    in one report; ``report.metrics["trapsimd"]`` is `summarize`."""
    prof = profile or TRAPSIMD
    if program is not None and profile is None:
        prof = dataclasses.replace(TRAPSIMD, circuit_order=program.order)
    circ = program.pairs() if program is not None else None
    rep = check(sched, prof, duration=trapsimd_duration(tg), circuit=circ)
    checks, extra = simd_rules(sched, rules, program)
    rep.violations.extend(extra)
    rep.checks.update(checks)
    rep.metrics["trapsimd"] = summarize(sched, tg=tg)
    return rep


def summarize(sched: TimedSchedule, *, tg: float = TRAPSIMD_US["gate2_effective"],
              n_qubits: int | None = None, f2q: float = TRAPSIMD_F["gate2"]) -> dict:
    """T_exe, T_inter (summed cycle time), T_intra = T_exe - T_inter, operation counts and
    the paper's fidelity product (Sec. 6.1), counting transport per ion moved and a swap
    once."""
    cyc: dict[float, float] = {}
    shifts = swaps = hops = g2 = g1 = meas = 0
    for e in sched.events:
        if e.kind == "hop":
            hops += 1
            cyc[e.t0] = max(cyc.get(e.t0, 0.0), e.t1)
        elif e.kind == "merge":
            if e.meta.get("op") == "shift":
                shifts += 1
            elif e.meta.get("op") == "swap":
                swaps += 1
        elif e.kind == "gate":
            g2 += 1
        elif e.kind == "gate1":
            g1 += 1
        elif e.kind == "measure":
            meas += 1
    swaps //= 2
    t_exe = max((e.t1 for e in sched.events), default=0.0)
    t_inter = sum(t1 - t0 for t0, t1 in cyc.items())
    n = n_qubits if n_qubits is not None else sum(len(c) for c in sched.chains.values())
    counts = {"gate1": g1, "gate2": g2, "measure": meas, "intra_shift": shifts, "intra_swap": swaps,
              "inter_shift": hops, "inter_cycles": len(cyc)}
    return {"T_exe_us": t_exe, "T_inter_us": t_inter, "T_intra_us": t_exe - t_inter,
            "counts": counts, "fidelity": fidelity(counts, t_exe, n, f2q=f2q)}


def fidelity(counts: Mapping[str, int], t_exe_us: float, n_qubits: int, *,
             f2q: float = TRAPSIMD_F["gate2"]) -> dict:
    """Sec. 6.1: F = F_1Q F_2Q F_transport F_decoh, F_decoh = exp(-n T / T_coh)."""
    F = TRAPSIMD_F
    f1 = F["gate1"] ** counts.get("gate1", 0)
    f2 = f2q ** counts.get("gate2", 0)
    ft = (F["intra_shift"] ** counts.get("intra_shift", 0) * F["intra_swap"] ** counts.get("intra_swap", 0)
          * F["inter_shift"] ** counts.get("inter_shift", 0) * F["inter_swap"] ** counts.get("inter_swap", 0))
    fd = math.exp(-n_qubits * t_exe_us / F["t_coh_us"])
    return {"F": f1 * f2 * ft * fd, "F_1Q": f1, "F_2Q": f2, "F_transport": ft, "F_decoh": fd,
            "f_2q": f2q, "transport_counted": "per ion moved; a swap once"}


# ------------------------------------------------------------------- worked examples

def _w1_start() -> tuple[Device, Recorder, dict[str, list[str]]]:
    dev = w1_device()
    # Fig. 6(a2): Q1 at the top of the upper-left trap; Q2 in the middle trap's gate zone and
    # Q3 beside it, toward the right junction; Q4 at the bottom of the upper-right trap; Q5 in
    # the right trap's gate zone.  Chains are per position: one ion or none.
    occ = {"N1.0": "Q1", "M.1": "Q2", "M.2": "Q3", "N2.2": "Q4", "E2.1": "Q5"}
    chains = {s: ([occ[s]] if s in occ else []) for s in dev.portgraph().sites}
    return dev, Recorder(dev), chains


def _sched(name: str, dev: Device, rec: Recorder, chains, claims: dict, source: dict) -> TimedSchedule:
    s = TimedSchedule(name, dev.portgraph(), chains, rec.events, claims=claims, source=source)
    return s


def w1_eager() -> TimedSchedule:
    """Fig. 6(b1)/(b2), eager JT-SIMD: 141 + 250 + max(141, 58, 200) + 250 + 141 = 982 us."""
    dev, r, chains = _w1_start()
    TG = TRAPSIMD_US["gate2_effective"]
    # step 1: TG(Q2,Q3) in M's gate zone; Q1 shifts down one position
    r.gate("Q2", "Q3", "M", 1, 0, TG, op=0)
    r.shift("N1", [("Q1", 0, 1)], 0)
    # the size-1 DR cycle waits for every in-flight operation (the gate ends at 141)
    r.cycle([("Q4", "N2", "J2", "E2")], 141)
    # step 2: TG(Q4,Q5); Q1 shifts down again; Q2 and Q3 swap
    r.gate("Q4", "Q5", "E2", 1, 391, TG, op=2)
    r.shift("N1", [("Q1", 1, 2)], 391)
    r.swap("M", "Q2", 1, "Q3", 2, 391)
    # step 3: the second size-1 DR cycle after the swap (591), then TG(Q1,Q3)
    r.cycle([("Q1", "N1", "J1", "M")], 591)
    r.gate("Q1", "Q3", "M", 1, 841, TG, op=1)
    return _sched("trapsimd-fig6-eager", dev, r, chains, {"T_exe_us": 982, "where": "Fig. 6(b2)"},
                  {"paper": "arXiv 2504.17886 v2", "figure": "Fig. 6(b1), (b2)", "transcribed": "by hand"})


def w1_batched() -> TimedSchedule:
    """Fig. 6(c1)/(c2), batched JT-SIMD (Eq. 2): max(141, 2*58) + 200 + 250 + 141 = 732 us."""
    dev, r, chains = _w1_start()
    TG = TRAPSIMD_US["gate2_effective"]
    r.gate("Q2", "Q3", "M", 1, 0, TG, op=0)
    r.shift("N1", [("Q1", 0, 1)], 0)
    r.shift("N1", [("Q1", 1, 2)], 58)
    r.swap("M", "Q2", 1, "Q3", 2, 141)
    # one size-2 DR cycle carries Q1 (through J1) and Q4 (through J2)
    r.cycle([("Q1", "N1", "J1", "M"), ("Q4", "N2", "J2", "E2")], 341)
    r.gate("Q1", "Q3", "M", 1, 591, TG, op=1)
    r.gate("Q4", "Q5", "E2", 1, 591, TG, op=2)
    return _sched("trapsimd-fig6-batched", dev, r, chains, {"T_exe_us": 732, "where": "Fig. 6(c2)"},
                  {"paper": "arXiv 2504.17886 v2", "figure": "Fig. 6(c1), (c2)", "transcribed": "by hand"})


def _w2_start() -> tuple[Device, Recorder, dict[str, list[str]]]:
    dev = w2_device()
    occ = {"T.1": "Q1", "T.2": "Q2", "T.3": "Q3", "T.5": "Q4", "T.6": "Q5"}
    chains = {s: ([occ[s]] if s in occ else []) for s in dev.portgraph().sites}
    return dev, Recorder(dev), chains


def w2_depth() -> TimedSchedule:
    """Fig. 7(b1)/(b2), depth-oriented: 200 + 141 + 141 + 200 + 141 = 823 us."""
    dev, r, chains = _w2_start()
    TG = TRAPSIMD_US["gate2_effective"]
    r.swap("T", "Q1", 1, "Q2", 2, 0)
    r.gate1("Q4", "T", 5, 0, op=1, name="h")
    r.shift("T", [("Q4", 5, 4), ("Q5", 6, 5)], 5)       # one S3 group of two ions
    r.gate("Q3", "Q1", "T", 3, 200, TG, op=0)
    r.gate("Q3", "Q4", "T", 3, 341, TG, op=2)
    r.swap("T", "Q4", 4, "Q5", 5, 482)
    r.gate("Q3", "Q5", "T", 3, 682, TG, op=3)
    return _sched("trapsimd-fig7-depth", dev, r, chains, {"T_exe_us": 823, "where": "Fig. 7(b2)"},
                  {"paper": "arXiv 2504.17886 v2", "figure": "Fig. 7(b1), (b2)", "transcribed": "by hand"})


def w2_sliced() -> TimedSchedule:
    """Fig. 7(c1), time-sliced: stamps 0, 5, 63, 204, 404, 545 us."""
    dev, r, chains = _w2_start()
    TG = TRAPSIMD_US["gate2_effective"]
    r.swap("T", "Q1", 1, "Q2", 2, 0)
    r.gate1("Q4", "T", 5, 0, op=1, name="h")
    r.shift("T", [("Q4", 5, 4), ("Q5", 6, 5)], 5)
    r.gate("Q3", "Q4", "T", 3, 63, TG, op=2)            # runs while the Q1-Q2 swap is in flight
    r.gate("Q3", "Q1", "T", 3, 204, TG, op=0)
    r.swap("T", "Q4", 4, "Q5", 5, 204)
    r.gate("Q3", "Q5", "T", 3, 404, TG, op=3)
    return _sched("trapsimd-fig7-sliced", dev, r, chains, {"T_exe_us": 545, "where": "Fig. 7(c1)"},
                  {"paper": "arXiv 2504.17886 v2", "figure": "Fig. 7(c1)", "transcribed": "by hand"})


# --------------------------------------------------------------------------- programs


@dataclass(frozen=True)
class Op:
    name: str
    qubits: tuple[int, ...]


#: Two-qubit gates that are diagonal (they commute with one another on every qubit).
_DIAGONAL_2Q = frozenset({"zz", "cz", "rzz"})


@dataclass
class Program:
    """A circuit as the scheduler and `simd_rules` see it.

    ``order`` is the core checker's circuit order for its two-qubit pairs; `block_index`
    is the exact rule: per qubit, a maximal run of CX gates in which the qubit keeps its
    role (or of diagonal gates) is one block whose gates commute; a one-qubit gate is a
    block of its own.
    """

    name: str
    n: int
    ops: list[Op]
    order: str = "commuting"
    names: list[str] | None = None
    meta: dict = field(default_factory=dict)

    def qname(self, q: int) -> str:
        return self.names[q] if self.names else f"q{q}"

    def pairs(self) -> list[tuple[str, str]]:
        return [(self.qname(o.qubits[0]), self.qname(o.qubits[1])) for o in self.ops
                if len(o.qubits) == 2]

    @property
    def n2q(self) -> int:
        return sum(1 for o in self.ops if len(o.qubits) == 2)

    @property
    def n1q(self) -> int:
        return sum(1 for o in self.ops if len(o.qubits) == 1)

    @staticmethod
    def _role(op: Op, q: int) -> str | None:
        if len(op.qubits) == 1:
            return None
        if op.name in _DIAGONAL_2Q:
            return "z"
        if op.name == "cx":
            return "c" if q == op.qubits[0] else "t"
        return None

    def block_index(self) -> list[dict[int, int]]:
        cached = self.meta.get("_bidx")
        if cached is not None and len(cached) == len(self.ops):
            return cached
        cur: dict[int, int] = {}
        last: dict[int, str | None] = {}
        out: list[dict[int, int]] = []
        for op in self.ops:
            d = {}
            for q in op.qubits:
                r = self._role(op, q)
                if q not in cur:
                    cur[q] = 0
                elif r is None or last[q] is None or r != last[q]:
                    cur[q] += 1
                last[q] = r
                d[q] = cur[q]
            out.append(d)
        self.meta["_bidx"] = out
        return out

    def describe(self) -> dict:
        return {"name": self.name, "qubits": self.n, "ops": len(self.ops), "two_qubit": self.n2q,
                "one_qubit": self.n1q, "order": self.order,
                **{k: v for k, v in self.meta.items() if not k.startswith("_")}}


def w1_program() -> Program:
    """Fig. 6(a1): CX(Q2,Q3), CX(Q1,Q3), CX(Q4,Q5)."""
    return Program("fig6", 5, [Op("cx", (1, 2)), Op("cx", (0, 2)), Op("cx", (3, 4))],
                   "commuting", names=["Q1", "Q2", "Q3", "Q4", "Q5"])


def w2_program() -> Program:
    """Fig. 7(a1): CX(Q3,Q1), H(Q4), CX(Q3,Q4), CX(Q3,Q5)."""
    return Program("fig7", 5, [Op("cx", (2, 0)), Op("h", (3,)), Op("cx", (2, 3)), Op("cx", (2, 4))],
                   "commuting", names=["Q1", "Q2", "Q3", "Q4", "Q5"])


def vqe(n: int) -> Program:
    """"Standard full-entanglement ansatz" (Sec. 6.1): one RY layer, CX(i, j) for every
    i < j in qiskit's 'full' order, one RY layer.  n(n-1)/2 CX, which is the count the
    paper's Fig. 9 F_2Q curve implies.  Rotation gates and reps are not stated: one RY per
    qubit per layer, reps = 1."""
    ops = [Op("ry", (q,)) for q in range(n)]
    ops += [Op("cx", (i, j)) for i in range(n) for j in range(i + 1, n)]
    ops += [Op("ry", (q,)) for q in range(n)]
    return Program(f"vqe{n}", n, ops, "commuting",
                   meta={"source": "rebuilt: RY, full CX entanglement, RY (reps=1)"})


def qaoa_complete(n: int) -> Program:
    """QAOA p=1 with a ZZ on EVERY pair: n(n-1)/2 two-qubit gates, the count the paper's
    Fig. 9 F_2Q points imply (1770 at n=60) although its text says 3-regular graphs."""
    ops = [Op("h", (q,)) for q in range(n)]
    ops += [Op("zz", (i, j)) for i in range(n) for j in range(i + 1, n)]
    ops += [Op("rx", (q,)) for q in range(n)]
    return Program(f"qaoa{n}_complete", n, ops, "multiset",
                   meta={"source": "rebuilt: H, ZZ on all pairs (one native 2Q gate each), RX; p=1"})


def qaoa_regular(n: int, seed: int = 0, degree: int = 3) -> Program:
    """QAOA p=1 on a random 3-regular graph, as the text says ("ZZ gates are applied only
    between graph-connected qubits"); the seed is ours.  3n/2 ZZ gates."""
    import networkx as nx

    g = nx.random_regular_graph(degree, n, seed=seed)
    edges = sorted(tuple(sorted(e)) for e in g.edges())
    ops = [Op("h", (q,)) for q in range(n)]
    ops += [Op("zz", e) for e in edges]
    ops += [Op("rx", (q,)) for q in range(n)]
    return Program(f"qaoa{n}_3reg_s{seed}", n, ops, "multiset",
                   meta={"source": f"rebuilt: H, ZZ on a random {degree}-regular graph "
                         f"(networkx seed {seed}), RX; p=1"})


def bv(n: int, seed: int = 0) -> Program:
    """Bernstein-Vazirani on n qubits: n-1 data qubits and one ancilla (the last), a secret
    with n/2 - 1 ones (the count the paper's Fig. 9 F_2Q implies), seeded.  X and H on the
    ancilla, H on every data qubit, CX(data, ancilla) per one, H on the data.  Measurement
    is left out: the paper's T_exe counts gates and transport only (Sec. 6.1)."""
    anc = n - 1
    ones = sorted(random.Random(seed).sample(range(n - 1), n // 2 - 1))
    ops = [Op("x", (anc,)), Op("h", (anc,))] + [Op("h", (q,)) for q in range(n - 1)]
    ops += [Op("cx", (q, anc)) for q in ones]
    ops += [Op("h", (q,)) for q in range(n - 1)]
    return Program(f"bv{n}", n, ops, "commuting",
                   meta={"source": f"rebuilt: balanced secret, {len(ones)} ones, seed {seed}",
                         "secret_ones": ones})


def _ccx(x: int, y: int, z: int) -> list[Op]:
    """The standard 6-CX Toffoli (Nielsen & Chuang Fig. 4.9)."""
    return [Op("h", (z,)), Op("cx", (y, z)), Op("tdg", (z,)), Op("cx", (x, z)), Op("t", (z,)),
            Op("cx", (y, z)), Op("tdg", (z,)), Op("cx", (x, z)), Op("t", (y,)), Op("t", (z,)),
            Op("h", (z,)), Op("cx", (x, y)), Op("t", (x,)), Op("tdg", (y,)), Op("cx", (x, y))]


def rca(n: int) -> Program:
    """Cuccaro ripple-carry adder (quant-ph/0410184) on n = 2m + 2 qubits: carry-in 0,
    a_i = 1 + i, b_i = 1 + m + i, carry-out 2m + 1; MAJ / UMA with 6-CX Toffolis.  16m + 1
    CX (465 at n = 60).  The paper's F_2Q implies 15n - 43 (857 at n = 60): its Toffoli
    decomposition or width differs and is not stated, so this RCA has fewer two-qubit gates
    than the paper's."""
    if n % 2 or n < 4:
        raise ValueError("the Cuccaro adder needs an even n >= 4")
    m = (n - 2) // 2
    cin, cout = 0, 2 * m + 1
    a = [1 + i for i in range(m)]
    b = [1 + m + i for i in range(m)]
    ops: list[Op] = []

    def maj(c: int, bb: int, aa: int) -> None:
        ops.extend([Op("cx", (aa, bb)), Op("cx", (aa, c))])
        ops.extend(_ccx(c, bb, aa))

    def uma(c: int, bb: int, aa: int) -> None:
        ops.extend(_ccx(c, bb, aa))
        ops.extend([Op("cx", (aa, c)), Op("cx", (c, bb))])

    maj(cin, b[0], a[0])
    for i in range(1, m):
        maj(a[i - 1], b[i], a[i])
    ops.append(Op("cx", (a[m - 1], cout)))
    for i in range(m - 1, 0, -1):
        uma(a[i - 1], b[i], a[i])
    uma(cin, b[0], a[0])
    return Program(f"rca{n}", n, ops, "commuting",
                   meta={"source": f"rebuilt: Cuccaro MAJ/UMA, m = {m} bits, 6-CX Toffoli"})


# ---------------------------------------------------------------------- line networks
#
# The all-pairs circuits (VQE's full entanglement, QAOA on the complete graph) are odd-even
# transposition networks on a line: in round r every pair of neighbours (p, p+1) with
# p = r mod 2 meets -- its gate, then the two ions exchange places.  QAOA (all ZZ commute)
# needs n rounds; VQE meets a pair only when its lower qubit has received every CX it is the
# target of, and needs 2n - 3 rounds, its dependency depth.  `LineExecutor` lays that line
# along a trail of traps and runs the rounds on the TrapSIMD machine.

_STEP = {"R": (0, 1), "L": (0, -1), "D": (1, 0), "U": (-1, 0)}


def trap_trail(dev: Device, m: int, budget: int = 2_000_000) -> list[tuple[str, str]]:
    """A trail of ``m`` traps of a `grid_device` -- consecutive traps meet at a junction,
    each trap is entered at one end and left at the other -- whose junction turns use as
    few JT-SIMD classes as possible (a crossing from one trap to the next has the class
    (direction in, direction out)).  Returns [(trap, direction of travel)]."""
    D = int(dev.params["D"])
    best: list = [None, 99]
    nodes = [0]

    def trap_of(r: int, c: int, d: str) -> tuple[str, tuple[int, int]]:
        dr, dc = _STEP[d]
        r2, c2 = r + dr, c + dc
        tid = {"R": f"H{r}{c}", "L": f"H{r}{c2}", "D": f"V{r}{c}", "U": f"V{r2}{c}"}[d]
        return tid, (r2, c2)

    def dfs(r: int, c: int, last: str | None, used: set, classes: frozenset, path: list) -> None:
        nodes[0] += 1
        if nodes[0] > budget:
            return
        if len(path) == m:
            if len(classes) < best[1]:
                best[0], best[1] = list(path), len(classes)
            return
        opts = []
        for d in "RDLU":
            t, (r2, c2) = trap_of(r, c, d)
            if not (0 <= r2 <= D and 0 <= c2 <= D) or t in used:
                continue
            new = classes if last is None else classes | {f"{last}->{d}"}
            if len(new) >= best[1]:
                continue
            opts.append((len(new) - len(classes), d, t, r2, c2, new))
        opts.sort(key=lambda o: o[0])
        for _, d, t, r2, c2, new in opts:
            used.add(t)
            path.append((t, d))
            dfs(r2, c2, d, used, new, path)
            path.pop()
            used.discard(t)

    for r in range(D + 1):
        for c in range(D + 1):
            dfs(r, c, None, set(), frozenset(), [])
    if best[0] is None:
        raise ValueError(f"no trail of {m} traps in {dev.name}")
    return best[0]


def all_pairs_rounds(prog: Program, *, partial: bool = False) -> list[list[tuple]]:
    """The odd-even rounds of an all-pairs program on the line q0, q1, ..., q(n-1): a list
    of rounds, each a list of ("2", line position p, operation, True) -- the operation's
    two ions are at p and p+1 and exchange after it.  "multiset" programs (all two-qubit
    gates commute) meet every pair of the round's parity; otherwise a pair meets only when
    its left ion has the lower index and the operation is ready (VQE's activation order).

    ``partial``: a "multiset" program on SOME of the pairs (QAOA on a sparse graph) runs the
    same network, the pairs it has no gate for only exchanging (("x", p)), until every
    gate it has has run."""
    n = prog.n
    pair_op: dict[tuple[int, int], int] = {}
    for k, o in enumerate(prog.ops):
        if len(o.qubits) == 2:
            key = tuple(sorted(o.qubits))
            if key in pair_op:
                raise ValueError("not an all-pairs program: a pair repeats")
            pair_op[key] = k
    if partial:
        if prog.order != "multiset":
            raise ValueError("a partial line network needs commuting two-qubit gates")
    elif len(pair_op) != n * (n - 1) // 2:
        raise ValueError("not an all-pairs program")
    bidx = prog.block_index()
    blocks: dict[int, list[list[int]]] = defaultdict(list)
    for k, d in enumerate(bidx):
        for q, b in d.items():
            while len(blocks[q]) <= b:
                blocks[q].append([])
            blocks[q][b].append(k)
    ptr = {q: 0 for q in range(n)}
    left = {q: len(blocks[q][0]) for q in range(n)}
    done = [False] * len(prog.ops)

    def finish(k: int) -> None:
        done[k] = True
        for q in prog.ops[k].qubits:
            left[q] -= 1
            while left[q] == 0 and ptr[q] + 1 < len(blocks[q]):
                ptr[q] += 1
                left[q] = len(blocks[q][ptr[q]])

    def ready(k: int) -> bool:
        return not done[k] and all(ptr[q] == bidx[k][q] for q in prog.ops[k].qubits)

    # the one-qubit gates before the entangling part
    for k, o in enumerate(prog.ops):
        if len(o.qubits) == 1 and bidx[k][o.qubits[0]] < min(
                (bidx[j][o.qubits[0]] for j in pair_op.values() if o.qubits[0] in prog.ops[j].qubits),
                default=math.inf):
            finish(k)
    free = prog.order == "multiset"
    line = list(range(n))
    rounds: list[list[tuple[int, int]]] = []
    met = 0
    r = 0
    while met < len(pair_op):
        if r > 4 * n + 10:
            raise RuntimeError("the line network does not cover every pair")
        cur = []
        for p in range(r % 2, n - 1, 2):
            u, v = line[p], line[p + 1]
            k = pair_op.get((min(u, v), max(u, v)))
            if k is None:
                cur.append(("x", p))
                continue
            if done[k] or not ready(k) or (not free and u > v):
                if partial:
                    cur.append(("x", p))
                continue
            cur.append(("2", p, k, True))
        for a in cur:
            p = a[1]
            if a[0] == "2":
                finish(a[2])
                met += 1
            line[p], line[p + 1] = line[p + 1], line[p]
        rounds.append(cur)
        r += 1
    while rounds and not rounds[-1]:
        rounds.pop()
    return rounds


class LineExecutor:
    """Runs line rounds on a trail of traps.

    Every trap of the trail holds a stretch of the line (``per_trap`` ions to start), in
    order along the direction of travel.  A pair inside a trap meets around a gate zone: its
    two ions shift (order kept, all ions of the trap moving at once) until one is in the
    zone and the other beside it, the gate runs, and a swap exchanges them (a plain
    exchange stands around a zone too, `Rules.swap_gz`).  A pair across a junction (the last
    ion of one trap, the first of the next) first brings one of its ions over: it walks to
    the trap edge, ONE JT-SIMD cycle per class carries every such ion through its junction
    at once, and the pair then meets inside one trap.  Traps run their rounds on their own
    clocks; all of them stop for a cycle (nothing overlaps an inter-trap cycle).
    """

    def __init__(self, dev: Device, prog: Program, trail: Sequence[tuple[str, str]],
                 order: Sequence[int] | None = None, *, per_trap: int = 4,
                 tg: float = TRAPSIMD_US["gate2_effective"], rules: Rules = Rules()):
        self.dev, self.prog, self.tg = dev, prog, float(tg)
        self.rules = rules
        self.trail = [t for t, _ in trail]
        self.fwd = [1 if d in "RD" else -1 for _, d in trail]
        self.L = [dev.traps[t].L for t in self.trail]
        self.junc = []
        for a, b in zip(self.trail, self.trail[1:]):
            common = set(dev.traps[a].ends) & set(dev.traps[b].ends)
            if len(common) != 1:
                raise ValueError(f"traps {a} and {b} do not meet at one junction")
            self.junc.append(common.pop())
        for k, j in enumerate(self.junc):     # the trail must leave each trap at its far end
            if self.local(k, dev.edge(self.trail[k], j)) != self.L[k] - 1 or \
                    self.local(k + 1, dev.edge(self.trail[k + 1], j)) != 0:
                raise ValueError(f"the trail does not run through {self.trail[k + 1]} end to end")
        self.rec = Recorder(dev)
        order = list(order) if order is not None else list(range(prog.n))
        self.lists: list[list[str]] = []
        self.u: dict[str, int] = {}          # ion -> local position in its trap
        self.k_of: dict[str, int] = {}
        it = iter(order)
        for k in range(len(self.trail)):
            lst = [prog.qname(q) for q in [next(it, None) for _ in range(per_trap)] if q is not None]
            self.lists.append(lst)
            for x, u in zip(lst, self._home(k, len(lst))):
                self.u[x] = u
                self.k_of[x] = k
        if any(True for _ in it):
            raise ValueError("the trail is too short for the program")
        self.clock = [0.0] * len(self.trail)
        self.crossings = 0
        self.chains = {}
        for T, tr in dev.traps.items():
            for i in range(tr.L):
                self.chains[dev.site(T, i)] = []
        for x, u in self.u.items():
            k = self.k_of[x]
            self.chains[dev.site(self.trail[k], self.cell(k, u))] = [x]

    # ---- geometry
    def local(self, k: int, cell: int) -> int:
        return cell if self.fwd[k] > 0 else self.L[k] - 1 - cell

    def cell(self, k: int, u: int) -> int:
        return u if self.fwd[k] > 0 else self.L[k] - 1 - u

    def gz(self, k: int) -> list[int]:
        return sorted(self.local(k, g) for g in self.dev.traps[self.trail[k]].gz)

    def _home(self, k: int, n: int) -> list[int]:
        """Where a stretch of n ions starts: pairs around the gate zones, e.g. (1, 2, 4, 5)
        for L = 8 with zones at 2 and 5."""
        want = []
        for g in self.gz(k):
            want += [g - 1, g]
        want = [u for u in want if 0 <= u < self.L[k]]
        return self._arrange(k, [want[i] if i < len(want) else self.L[k] - 1 for i in range(n)], {}, set())

    def _arrange(self, k: int, desire: list[int], pins: dict[int, int], forbid: set[int]) -> list[int]:
        """Order-preserving cells for the ions of trap k: ion i at pins[i] if pinned, else
        as close to desire[i] as possible (squared distance), never in a forbidden cell."""
        L, n = self.L[k], len(desire)
        INF = math.inf
        F = [[INF] * L for _ in range(n)]
        B = [[-1] * L for _ in range(n)]
        for i in range(n):
            for c in range(L):
                if (i in pins and pins[i] != c) or (i not in pins and c in forbid):
                    continue
                cost = (c - desire[i]) ** 2
                if i == 0:
                    F[i][c] = cost
                    continue
                best, arg = INF, -1
                for c2 in range(c):
                    if F[i - 1][c2] < best:
                        best, arg = F[i - 1][c2], c2
                if best < INF:
                    F[i][c] = best + cost
                    B[i][c] = arg
        if n == 0:
            return []
        c = min(range(L), key=lambda c: F[n - 1][c])
        if F[n - 1][c] == INF:
            raise RuntimeError(f"no arrangement of {n} ions in trap {self.trail[k]}")
        out = [0] * n
        for i in range(n - 1, -1, -1):
            out[i] = c
            c = B[i][c]
        return out

    # ---- primitive steps on one trap's clock
    def _move_to(self, k: int, targets: list[int]) -> None:
        lst = self.lists[k]
        T = self.trail[k]
        while True:
            moves = []
            for x, tgt in zip(lst, targets):
                u = self.u[x]
                if u != tgt:
                    d = 1 if tgt > u else -1
                    moves.append((x, self.cell(k, u), self.cell(k, u + d)))
            if not moves:
                return
            self.rec.shift(T, moves, self.clock[k])
            for x, a, b in moves:
                self.u[x] = self.local(k, b)
            self.clock[k] += TRAPSIMD_US["intra_shift"]

    def _plan_slot(self, k: int, units: list[tuple]) -> tuple | None:
        """The cheapest placement of ``units`` of trap k, or None if they cannot stand at
        once.  ("g", i, op, exch): ions i, i+1 around a gate zone; ("1", i, ops): ion i in
        a gate zone; ("x", i): ions i, i+1 side by side anywhere (an exchange needs no
        zone).  Gate units take distinct zones in line order.  Returns ((max shift, total
        shift), targets, zones of the gate units)."""
        zones = self.gz(k)
        L = self.L[k]
        cur = [self.u[x] for x in self.lists[k]]
        zoned = (lambda u: u[0] != "x" or self.rules.swap_gz)
        gated = [u for u in units if zoned(u)]
        xs = [u for u in units if not zoned(u)]
        xpins = {}
        for _, i in xs:                          # an exchange pair closes up where it stands
            lo = min(max(cur[i], 0), L - 2)
            if cur[i + 1] - cur[i] > 1:
                lo = min(cur[i], L - 2)
            xpins[i], xpins[i + 1] = lo, lo + 1
        best = None
        for zs in _ordered_choices(zones, len(gated)):
            for orient in range(1 << len(gated)):
                pins = dict(xpins)
                ok = True
                for b, (u, g) in enumerate(zip(gated, zs)):
                    i = u[1]
                    if u[0] == "1":
                        if i in pins:
                            ok = False
                            break
                        pins[i] = g
                        continue
                    lo = g - 1 if (orient >> b) & 1 else g
                    if lo < 0 or lo + 1 >= L or i in pins or i + 1 in pins:
                        ok = False
                        break
                    pins[i], pins[i + 1] = lo, lo + 1
                if not ok:
                    continue
                try:
                    tg = self._arrange(k, cur, pins, set())
                except RuntimeError:
                    continue
                cost = (max(abs(a - b) for a, b in zip(tg, cur)), sum(abs(a - b) for a, b in zip(tg, cur)))
                if best is None or cost < best[0]:
                    best = (cost, tg, zs)
        return best

    def _exec_slot(self, k: int, units: list[tuple], tg: list[int], zs: tuple[int, ...]) -> None:
        """Shift into place; run the gates (one per zone, at once) and the plain exchanges;
        exchange the gated pairs that exchange."""
        self._move_to(k, tg)
        lst = self.lists[k]
        T = self.trail[k]
        t0 = self.clock[k]
        end = t0
        swaps: list[tuple[int, float]] = []
        gated = [u for u in units if u[0] != "x" or self.rules.swap_gz]
        for u, g in zip(gated, zs):
            if u[0] == "x":
                continue
            if u[0] == "g":
                _, i, op, exch = u
                o = self.prog.ops[op]
                a, b = self.prog.qname(o.qubits[0]), self.prog.qname(o.qubits[1])
                self.rec.gate(a, b, T, self.cell(k, g), t0, self.tg, op=op, name=o.name)
                end = max(end, t0 + self.tg)
                if exch:
                    swaps.append((i, t0 + self.tg))
            else:
                _, i, ops = u
                t = t0
                for j in ops:
                    self.rec.gate1(lst[i], T, self.cell(k, g), t, op=j, name=self.prog.ops[j].name)
                    t += TRAPSIMD_US["gate1"]
                end = max(end, t)
        for u in units:
            if u[0] == "x":
                swaps.append((u[1], t0))
        for i, t in swaps:
            x, y = lst[i], lst[i + 1]
            self.rec.swap(T, x, self.cell(k, self.u[x]), y, self.cell(k, self.u[y]), t)
            self.u[x], self.u[y] = self.u[y], self.u[x]
            lst[i], lst[i + 1] = y, x
            end = max(end, t + TRAPSIMD_US["intra_swap"])
        self.clock[k] = end

    def _run_units(self, k: int, units: list[tuple]) -> None:
        """Every unit of trap k, in slots no larger than the gate zones allow.  Two packings
        are tried -- the largest set that can stand at once, and the set that does the
        most per microsecond -- and the one that ends earlier is kept."""
        if not units:
            return
        best = None
        for how in ("most", "rate"):
            snap = self._snapshot(k)
            try:
                self._pack(k, list(units), how)
                end = self.clock[k]
            except RuntimeError:
                end = math.inf
            self._restore(k, snap)
            if best is None or end < best[0] - 1e-9:
                best = (end, how)
        if best[0] == math.inf:
            raise RuntimeError(f"cannot place {units} in trap {self.trail[k]}")
        self._pack(k, list(units), best[1])

    def _snapshot(self, k: int) -> tuple:
        return (list(self.lists[k]), {x: self.u[x] for x in self.lists[k]}, self.clock[k],
                len(self.rec.events), self.rec._id, self.rec._group)

    def _restore(self, k: int, snap: tuple) -> None:
        lst, us, clock, n, i, g = snap
        self.lists[k] = lst
        self.u.update(us)
        self.clock[k] = clock
        del self.rec.events[n:]
        self.rec._id, self.rec._group = i, g

    def _slot_time(self, units: list[tuple], plan: tuple) -> float:
        t = TRAPSIMD_US["intra_shift"] * plan[0][0]
        if any(u[0] == "g" for u in units):
            t += self.tg
        elif any(u[0] == "1" for u in units):
            t += TRAPSIMD_US["gate1"] * max(len(u[2]) for u in units if u[0] == "1")
        if any(u[0] == "x" or (u[0] == "g" and u[3]) for u in units):
            t += TRAPSIMD_US["intra_swap"]
        return t

    def _pack(self, k: int, units: list[tuple], how: str) -> None:
        from itertools import combinations
        units = sorted(units, key=lambda u: (u[1], u[0]))
        n = len(self.gz(k))
        zoned = (lambda u: u[0] != "x" or self.rules.swap_gz)
        while units:
            pick = None
            gate_units = [u for u in units if zoned(u)]
            for size in range(min(n, len(gate_units)), -1, -1):
                for sub in combinations(gate_units, size):
                    if self.rules.one_gate_per_trap and sum(1 for u in sub if u[0] != "x") > 1:
                        continue
                    trial = list(sub) + [u for u in units if not zoned(u) and
                                         not any(v[1] in (u[1] - 1, u[1], u[1] + 1) for v in sub)]
                    if not trial:
                        continue
                    plan = self._plan_slot(k, trial)
                    if plan is None:
                        continue
                    if how == "most":
                        key = (-len(trial), plan[0])
                    else:
                        key = (-len(trial) / self._slot_time(trial, plan), plan[0])
                    if pick is None or key < pick[0]:
                        pick = (key, trial, plan)
                if pick is not None and how == "most":
                    break
            if pick is None:
                raise RuntimeError(f"cannot place {units} in trap {self.trail[k]}")
            _, sub, (_, tg, zs) = pick
            self._exec_slot(k, sub, tg, zs)
            done = {id(u) for u in sub}
            units = [u for u in units if id(u) not in done]

    # ---- rounds
    def one_qubit_phase(self, ops_of: Mapping[str, list[int]]) -> None:
        for k, lst in enumerate(self.lists):
            units = [("1", i, ops_of[x]) for i, x in enumerate(lst) if ops_of.get(x)]
            if units:
                self._run_units(k, units)

    def round(self, acts: Sequence[tuple]) -> None:
        """One round: ("2", p, op, exchange) -- the operation on the ions at line positions
        p, p+1, then (if asked) they exchange places; ("x", p) -- they only exchange;
        ("1", p, ops) -- one-qubit operations on the ion at p.  A pair across a junction
        first brings one ion over: crossings alternate direction from one crossing round
        to the next (the last ion of a trap forward, then the first ion of a trap back),
        so no trap's share of the line drifts to one end."""
        loc = [(k, i) for k, lst in enumerate(self.lists) for i in range(len(lst))]
        inner: list[tuple] = []          # units keyed by ion name, resolved after crossings
        cross: list[tuple[int, tuple]] = []
        for a in acts:
            p = a[1]
            if a[0] == "1":
                k, i = loc[p]
                inner.append(("1", self.lists[k][i], a[2]))
                continue
            (k1, i1), (k2, _) = loc[p], loc[p + 1]
            unit = ("g", None, a[2], a[3]) if a[0] == "2" else ("x", None)
            if k1 == k2:
                inner.append((unit[0], self.lists[k1][i1]) + unit[2:])
            else:
                cross.append((k1, unit))
        if cross:
            # each crossing carries an ion from the fuller trap to the emptier; on a tie
            # the direction alternates from one crossing round to the next, so a dense
            # round (all traps equal) uses one direction -- one set of classes
            parity = self.crossings % 2 == 0
            self.crossings += 1
            fwd = {}
            for k, _ in cross:
                a_, b_ = len(self.lists[k]), len(self.lists[k + 1])
                fwd[k] = a_ > b_ if a_ != b_ else parity
            for k in range(len(self.trail)):
                lst = self.lists[k]
                pins, forbid = {}, set()
                if fwd.get(k) is True:                  # its last ion goes forward
                    pins[len(lst) - 1] = self.L[k] - 1
                if fwd.get(k) is False:                 # an ion arrives at its forward edge
                    forbid.add(self.L[k] - 1)
                if fwd.get(k - 1) is False:             # its first ion goes back
                    pins[0] = 0
                if fwd.get(k - 1) is True:              # an ion arrives at its backward edge
                    forbid.add(0)
                if pins or forbid:
                    self._move_to(k, self._arrange(k, [self.u[x] for x in lst], pins, forbid))
            t = max(self.clock)
            by_cls: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
            for k, _ in cross:
                s_, d_ = (k, k + 1) if fwd[k] else (k + 1, k)
                x = self.lists[s_][-1] if fwd[k] else self.lists[s_][0]
                by_cls[self.dev.klass(self.trail[s_], self.junc[k], self.trail[d_])].append(
                    (x, self.trail[s_], self.junc[k], self.trail[d_]))
            for cls in sorted(by_cls, key=lambda c: (-len(by_cls[c]), c)):
                self.rec.cycle(by_cls[cls], t)
                t += TRAPSIMD_US["inter_shift"]
            for k, unit in cross:
                if fwd[k]:
                    x = self.lists[k].pop()
                    self.lists[k + 1].insert(0, x)
                    self.k_of[x], self.u[x] = k + 1, 0
                    left = x
                else:
                    x = self.lists[k + 1].pop(0)
                    left = self.lists[k][-1]
                    self.lists[k].append(x)
                    self.k_of[x], self.u[x] = k, self.L[k] - 1
                inner.append((unit[0], left) + unit[2:])
            self.clock = [t] * len(self.trail)
        per: dict[int, list[tuple]] = defaultdict(list)
        for u in inner:
            k = self.k_of[u[1]]
            per[k].append((u[0], self.lists[k].index(u[1])) + u[2:])
        for k, units in per.items():
            self._run_units(k, units)

    def schedule(self, name: str, source: dict) -> TimedSchedule:
        return TimedSchedule(name, self.dev.portgraph(), self.chains, self.rec.events, source=source)


def _ordered_choices(zones: list[int], r: int) -> Iterable[tuple[int, ...]]:
    """Increasing r-tuples of gate zones (units in line order get zones in line order)."""
    from itertools import combinations
    return combinations(zones, r)


def schedule_line(dev: Device, prog: Program, *, per_trap: int = 4,
                  tg: float = TRAPSIMD_US["gate2_effective"], rules: Rules = Rules(),
                  partial: bool = False) -> TimedSchedule:
    """OUR schedule of an all-pairs program (VQE full entanglement, complete-graph QAOA):
    the odd-even line network of `all_pairs_rounds`, laid on the fewest-class trail."""
    rounds = all_pairs_rounds(prog, partial=partial)
    m = -(-prog.n // per_trap)
    trail = trap_trail(dev, m)
    ex = LineExecutor(dev, prog, trail, per_trap=per_trap, tg=tg, rules=rules)
    bidx = prog.block_index()
    first2 = {q: min((bidx[k][q] for k, o in enumerate(prog.ops) if len(o.qubits) == 2 and q in o.qubits),
                     default=math.inf) for q in range(prog.n)}
    pre: dict[str, list[int]] = defaultdict(list)
    post: dict[str, list[int]] = defaultdict(list)
    for k, o in enumerate(prog.ops):
        if len(o.qubits) == 1:
            q = o.qubits[0]
            (pre if bidx[k][q] < first2[q] else post)[prog.qname(q)].append(k)
    ex.one_qubit_phase(pre)
    for r in rounds:
        ex.round(r)
    ex.one_qubit_phase(post)
    classes = sorted({dev.klass(a, j, b) for a, j, b in zip(ex.trail, ex.junc, ex.trail[1:])})
    return ex.schedule(f"trapsimd-ours-line-{prog.name}-{dev.name}",
                       {"scheduler": "qccd.repro.trapsimd.schedule_line", "per_trap": per_trap,
                        "tg_us": tg, "device": dev.name, "program": prog.describe(),
                        "trail": [f"{t}{d}" for t, d in trail], "trail_classes": classes,
                        "rounds": len(rounds)})


def lnn_rounds(prog: Program, order: Sequence[int]) -> list[list[tuple]]:
    """Rounds for any program on a line (qubits in ``order``): every round, operations in
    program order claim line positions first come first served -- a ready one-qubit
    operation runs with the one-qubit operations that follow it on that qubit; a ready
    two-qubit operation runs if its qubits are neighbours, otherwise each of them steps
    one place toward the other by an exchange.  The earliest ready operation always
    advances, so the rounds end."""
    n = prog.n
    line = list(order)
    pos = {q: i for i, q in enumerate(line)}
    bidx = prog.block_index()
    blocks: dict[int, list[list[int]]] = defaultdict(list)
    seq: dict[int, list[int]] = defaultdict(list)
    for k, d in enumerate(bidx):
        for q, b in d.items():
            while len(blocks[q]) <= b:
                blocks[q].append([])
            blocks[q][b].append(k)
            seq[q].append(k)
    ptr = {q: 0 for q in range(n)}
    left = {q: (len(blocks[q][0]) if blocks[q] else 0) for q in range(n)}
    done = [False] * len(prog.ops)

    def finish(k: int) -> None:
        done[k] = True
        for q in prog.ops[k].qubits:
            left[q] -= 1
            while left[q] == 0 and ptr[q] + 1 < len(blocks[q]):
                ptr[q] += 1
                left[q] = len(blocks[q][ptr[q]])

    def ready(k: int) -> bool:
        return not done[k] and all(ptr[q] == bidx[k][q] for q in prog.ops[k].qubits)

    rounds: list[list[tuple]] = []
    remaining = len(prog.ops)
    while remaining:
        if len(rounds) > 50 * len(prog.ops) + 100:
            raise RuntimeError("the line router does not finish")
        cand = sorted({k for q in range(n) if blocks[q] and ptr[q] < len(blocks[q])
                       for k in blocks[q][ptr[q]] if ready(k)})
        used: set[int] = set()
        acts: list[tuple] = []
        fin: list[int] = []
        swaps: list[int] = []
        for k in cand:
            o = prog.ops[k]
            if len(o.qubits) == 1:
                q = o.qubits[0]
                p = pos[q]
                if p in used:
                    continue
                chain = [k]
                nxt = seq[q].index(k) + 1
                while nxt < len(seq[q]) and len(prog.ops[seq[q][nxt]].qubits) == 1:
                    chain.append(seq[q][nxt])
                    nxt += 1
                acts.append(("1", p, chain))
                used.add(p)
                fin += chain
                continue
            pa, pb = pos[o.qubits[0]], pos[o.qubits[1]]
            lo, hi = min(pa, pb), max(pa, pb)
            if hi - lo == 1:
                if lo in used or hi in used:
                    continue
                acts.append(("2", lo, k, False))
                used.update((lo, hi))
                fin.append(k)
                continue
            for p, d in ((lo, 1), (hi, -1)):
                if p in used or p + d in used:
                    continue
                acts.append(("x", min(p, p + d)))
                used.update((p, p + d))
                swaps.append(min(p, p + d))
            used.update((lo, hi))
        for p in swaps:
            a, b = line[p], line[p + 1]
            line[p], line[p + 1] = b, a
            pos[a], pos[b] = p + 1, p
        for k in fin:
            finish(k)
            remaining -= 1
        rounds.append(acts)
    return rounds


def schedule_lnn(dev: Device, prog: Program, order: Sequence[int], *, per_trap: int = 4,
                 tg: float = TRAPSIMD_US["gate2_effective"], rules: Rules = Rules(),
                 label: str = "lnn") -> TimedSchedule:
    """OUR schedule of any program by `lnn_rounds` on the fewest-class trail."""
    rounds = lnn_rounds(prog, order)
    m = -(-prog.n // per_trap)
    trail = trap_trail(dev, m)
    ex = LineExecutor(dev, prog, trail, order, per_trap=per_trap, tg=tg, rules=rules)
    for r in rounds:
        ex.round(r)
    classes = sorted({dev.klass(a, j, b) for a, j, b in zip(ex.trail, ex.junc, ex.trail[1:])})
    return ex.schedule(f"trapsimd-ours-{label}-{prog.name}-{dev.name}",
                       {"scheduler": "qccd.repro.trapsimd.schedule_lnn", "per_trap": per_trap,
                        "tg_us": tg, "device": dev.name, "program": prog.describe(),
                        "order": list(order), "trail": [f"{t}{d}" for t, d in trail],
                        "trail_classes": classes, "rounds": len(rounds)})


def line_order(prog: Program) -> list[int]:
    """The line a program is laid on: Cuccaro's (cin, b0, a0, b1, a1, ..., cout) for the
    adder, the ancilla then its partners then the rest for BV, else 0..n-1."""
    if prog.name.startswith("rca"):
        m = (prog.n - 2) // 2
        out = [0]
        for i in range(m):
            out += [1 + m + i, 1 + i]
        return out + [2 * m + 1]
    if prog.name.startswith("bv"):
        ones = list(prog.meta["secret_ones"])
        anc = prog.n - 1
        rest = [q for q in range(prog.n - 1) if q not in set(ones)]
        return [anc] + ones + rest
    return list(range(prog.n))


def schedule_ours(dev: Device, prog: Program, *, tg: float = TRAPSIMD_US["gate2_effective"],
                  rules: Rules = Rules(), per_trap: Sequence[int] = (3, 4, 5)) -> tuple[TimedSchedule, Report]:
    """OUR schedule of ``prog``: the all-pairs line network when the program is one (VQE,
    complete-graph QAOA); else the line router on `line_order`, and for commuting gates
    also the line network that only exchanges the pairs it has no gate for.  Every method
    and every line density in ``per_trap`` is tried, each schedule is judged by
    `check_trapsimd`, and the fastest one that passes every check is returned with its
    report; ``source["tried"]`` lists them all."""
    builds = []
    try:
        all_pairs_rounds(prog)
        builds.append(("line", lambda p: schedule_line(dev, prog, per_trap=p, tg=tg, rules=rules)))
    except ValueError:
        order = line_order(prog)
        builds.append(("router", lambda p: schedule_lnn(dev, prog, order, per_trap=p, tg=tg, rules=rules)))
        if prog.order == "multiset":
            builds.append(("line-partial", lambda p: schedule_line(dev, prog, per_trap=p, tg=tg,
                                                                     rules=rules, partial=True)))
    best = None
    tried = []
    for how, p in [(h, p) for h in builds for p in per_trap]:
        if -(-prog.n // p) > len(dev.traps):
            continue
        try:
            s = how[1](p)
        except (RuntimeError, ValueError) as e:
            tried.append({"method": how[0], "per_trap": p, "error": str(e)[:200]})
            continue
        r = check_trapsimd(s, program=prog, tg=tg, rules=rules)
        t = r.metrics["makespan_us"]
        tried.append({"method": how[0], "per_trap": p, "T_exe_us": t, "ok": r.ok})
        if r.ok and (best is None or t < best[0]):
            best = (t, s, r, how[0])
    if best is None:
        raise RuntimeError(f"no legal schedule for {prog.name} on {dev.name}: {tried}")
    _, s, r, how = best
    s.source["method"] = how
    s.source["tried"] = tried
    return s, r


# ------------------------------------------------------------------------ reproduction

#: Table 3, "Results of our compiler and SHAPER*": (L, benchmark, n) -> (SHAPER* T_exe us,
#: FluxTrap T_exe us, SHAPER* F, FluxTrap F).
TABLE3 = {
    (8, "QAOA", 20): (173561, 93845, 0.49, 0.61), (8, "RCA", 20): (214369, 70624, 0.43, 0.57),
    (8, "BV", 20): (9870, 8373, 0.97, 0.97), (8, "VQE", 20): (218974, 130538, 0.42, 0.56),
    (8, "QAOA", 40): (733637, 327601, 0.03, 0.13), (8, "RCA", 40): (487001, 180950, 0.16, 0.28),
    (8, "BV", 40): (21554, 16175, 0.92, 0.94), (8, "VQE", 40): (883977, 532440, 0.02, 0.09),
    (8, "QAOA", 60): (1684718, 744943, 2.93e-4, 8.91e-3), (8, "RCA", 60): (801437, 278970, 0.05, 0.14),
    (8, "BV", 60): (37866, 23625, 0.88, 0.92), (8, "VQE", 60): (1921069, 1255877, 8.54e-5, 3.33e-3),
    (8, "QAOA", 100): (4687884, 2256313, 3.56e-11, 5.85e-7), (8, "RCA", 100): (1346070, 561586, 5.25e-3, 0.03),
    (8, "BV", 100): (66858, 38679, 0.74, 0.86), (8, "VQE", 100): (5200261, 3570854, 8.33e-13, 8.88e-8),
    (14, "QAOA", 20): (265068, 128886, 0.38, 0.58), (14, "RCA", 20): (303805, 85702, 0.35, 0.56),
    (14, "BV", 20): (13233, 11745, 0.96, 0.97), (14, "VQE", 20): (300942, 134186, 0.3, 0.55),
    (14, "QAOA", 40): (1053797, 370858, 8.02e-3, 0.12), (14, "RCA", 40): (689769, 204547, 0.1, 0.27),
    (14, "BV", 40): (31426, 18479, 0.9, 0.94), (14, "VQE", 40): (1223741, 784346, 3.07e-3, 0.07),
    (14, "QAOA", 60): (2403532, 822641, 1.25e-5, 8.01e-3), (14, "RCA", 60): (1131689, 296592, 0.02, 0.14),
    (14, "BV", 60): (50583, 28741, 0.81, 0.91), (14, "VQE", 60): (2669385, 1551403, 1.05e-6, 2.48e-3),
    (14, "QAOA", 100): (6450674, 2568088, 1.19e-15, 3.51e-7), (14, "RCA", 100): (1900434, 572560, 1.06e-3, 0.03),
    (14, "BV", 100): (106961, 60293, 0.66, 0.83), (14, "VQE", 100): (7226433, 4329181, 1.20e-18, 9.58e-9),
}

#: Table 2: n qubits -> D (a D x D grid of cells).
TABLE2_D = {20: 2, 40: 3, 60: 4, 100: 5}

#: The two-qubit gate counts the paper's Fig. 9 F_2Q curves imply (step-0 report, sec. 6).
PAPER_2Q = {"VQE": lambda n: n * (n - 1) // 2, "QAOA": lambda n: n * (n - 1) // 2,
            "RCA": lambda n: 15 * n - 43, "BV": lambda n: n // 2 - 1}

PAPER_META = {
    "key": "ruan2025",
    "short": "TrapSIMD",
    "title": "TrapSIMD: SIMD-Aware Compiler Optimization for 2D Trapped-Ion Quantum Machines",
    "authors": ["Jixuan Ruan", "Hezi Zhang", "Xiang Fang", "Ang Li", "Wesley C. Campbell", "Eric Hudson",
                "David Hayes", "Hartmut Haeffner", "Travis Humble", "Jens Palsberg", "Yufei Ding"],
    "venue": "arXiv preprint (MICRO 2025 template; publication not confirmed)",
    "year": 2025,
    "arxiv": "2504.17886",
    "artifact": {"url": None, "commit": None, "license": None,
                 "note": "No code, schedules or circuits are released (searched 2026-10-07).  The "
                         "reproduction is the two fully specified worked examples, replayed exactly, "
                         "and a model-level comparison: the paper's device, timing table and broadcast "
                         "rule, circuits rebuilt from its description, scheduled by us."},
    "what": "FluxTrap: a compiler for a 2D grid of linear traps joined by X-junctions whose inter-trap "
            "transport is broadcast (one JT-SIMD class per 250-us cycle) and whose intra-trap "
            "transport is independently addressed (S3 group shifts, swaps).",
}

_BENCH = {"VQE": vqe, "QAOA": qaoa_complete, "RCA": rca, "BV": bv}


def _run_record(rid: str, label: str, sched: TimedSchedule, rep: Report, *, kind: str, file: str,
                seconds: float, tg: float, rules: Rules, extra: dict | None = None) -> dict:
    m = rep.metrics["trapsimd"]
    rec = {
        "id": rid, "label": label, "kind": kind, "profile": "trapsimd",
        "rules": dataclasses.asdict(rules), "tg_us": tg,
        "checks": dict(rep.checks), "ok": rep.ok,
        "violations": dict(Counter(v.check for v in rep.violations)),
        "first_violations": [dataclasses.asdict(v) for v in rep.violations[:12]],
        "notes": dict(rep.notes),
        "measured": {"makespan_us": rep.metrics["makespan_us"], "T_inter_us": m["T_inter_us"],
                     "T_intra_us": m["T_intra_us"], "fidelity": m["fidelity"]},
        "counts": m["counts"], "events": len(sched.events),
        "source": {k: v for k, v in sched.source.items() if k != "program"},
        "file": file, "seconds": round(seconds, 2),
    }
    if "program" in sched.source:
        rec["circuit"] = sched.source["program"]
    if extra:
        rec.update(extra)
    return rec


def reproduce(out: str | Path, *, sizes: Sequence[int] = (20, 40, 60, 100), lengths: Sequence[int] = (8, 14),
              log: Callable[[str], None] = print) -> dict:
    """Write ``examples/`` (Figs. 6, 7), ``ours/`` (our schedules for Table 3's grid, the
    3-regular QAOA variant and the A-60 sensitivities) and ``results.json`` under ``out``."""
    out = Path(out)
    (out / "examples").mkdir(parents=True, exist_ok=True)
    (out / "ours").mkdir(parents=True, exist_ok=True)
    runs: list[dict] = []
    rules = Rules()
    tg = TRAPSIMD_US["gate2_effective"]

    # -- the worked examples, event for event
    for rid, build, prog, claim, where in (
            ("fig6_eager", w1_eager, w1_program(), 982, "Fig. 6(b2)"),
            ("fig6_batched", w1_batched, w1_program(), 732, "Fig. 6(c2)"),
            ("fig7_depth", w2_depth, w2_program(), 823, "Fig. 7(b2)"),
            ("fig7_sliced", w2_sliced, w2_program(), 545, "Fig. 7(c1)")):
        t0 = time.time()
        s = build()
        rep = check_trapsimd(s, program=prog, tg=tg, rules=rules)
        f = f"examples/{rid}.schedule.json"
        s.save(out / f)
        ms = rep.metrics["makespan_us"]
        bare = check_trapsimd(s, program=prog, tg=TRAPSIMD_US["gate2_table"], rules=rules)
        runs.append(_run_record(rid, f"{where}, as drawn", s, rep, kind="example", file=f,
                                seconds=time.time() - t0, tg=tg, rules=rules, extra={
            "compare": {"paper": {"T_exe_us": {"ours": ms, "theirs": claim, "rel": abs(ms - claim) / claim,
                                               "verdict": "exact" if ms == claim else "differs",
                                               "where": where, "printed": True}}},
            "with_table1_25us_gate": {"ok": bare.ok, "duration_mismatches": bare.metrics["duration_mismatches"]}}))
        log(f"{rid}: {ms:g} us (paper {claim}), ok={rep.ok}")

    # -- model level: Table 3's grid
    def ours(bench: str, n: int, L: int, *, prog: Program | None = None, rid: str | None = None,
             label: str | None = None, tg_: float = tg, rules_: Rules = rules, gz=None,
             strict: bool = False, kind: str = "ours") -> dict | None:
        prog = prog or _BENCH[bench](n)
        dev = grid_device(TABLE2_D[n], L, gz=gz)
        rid = rid or f"{bench.lower()}{n}_L{L}"
        t0 = time.time()
        try:
            s, rep = schedule_ours(dev, prog, tg=tg_, rules=rules_)
        except RuntimeError as e:
            log(f"{rid}: FAILED {e}")
            runs.append({"id": rid, "label": label or rid, "kind": kind, "ok": False, "error": str(e)[:400]})
            return None
        if strict:
            rep = check_trapsimd(s, program=dataclasses.replace(prog, order="strict"), tg=tg_, rules=rules_)
        f = f"ours/{rid}.schedule.json"
        s.claims = {}
        paper = TABLE3.get((L, bench, n))
        ms = rep.metrics["makespan_us"]
        cmp = {}
        if paper:
            cmp = {"paper": {
                "T_exe_us": {"ours": ms, "theirs": paper[1], "ratio": paper[1] / ms,
                             "verdict": "ours faster" if ms < paper[1] else "ours slower",
                             "where": "Table 3, 'Our T_exe' (FluxTrap)", "printed": True,
                             "shaper_star": paper[0]},
                "F": {"ours": rep.metrics["trapsimd"]["fidelity"]["F"], "theirs": paper[3],
                      "where": "Table 3, 'Our Fidelity'", "printed": True,
                      "note": "ours: Table 1 fidelities, transport counted per ion moved, a swap once"}}}
            s.claims = {"paper_T_exe_us": paper[1], "where": "Table 3"}
        s.save(out / f)
        rec = _run_record(rid, label or f"{bench}-{n} on A-{n} ({TABLE2_D[n]}x{TABLE2_D[n]} grid, "
                          f"{len(dev.traps)} traps, L={L}, 2 gate zones), ours", s, rep, kind=kind, file=f,
                          seconds=time.time() - t0, tg=tg_, rules=rules_, extra={
            "benchmark": bench, "n": n, "L": L, "level": "model",
            "device": {"name": dev.name, **{k: v for k, v in dev.params.items() if k != "centers"},
                       "traps": len(dev.traps), "positions": dev.n_positions},
            "two_qubit_gates": {"ours": prog.n2q, "paper_implied": PAPER_2Q[bench](n)},
            "compare": cmp})
        runs.append(rec)
        log(f"{rid}: {ms:.0f} us, paper {paper and paper[1]}, ok={rep.ok} [{time.time() - t0:.0f}s]")
        return rec

    for L in lengths:
        for n in sizes:
            for bench in ("QAOA", "RCA", "BV", "VQE"):
                ours(bench, n, L)
    # -- QAOA as the text describes it (3-regular graphs), on A-60
    for L in lengths:
        if 60 in sizes:
            ours("QAOA", 60, L, prog=qaoa_regular(60, seed=0), rid=f"qaoa60_3reg_L{L}", kind="variant",
                 label=f"QAOA-60 on a random 3-regular graph (seed 0) on A-60, L={L}, ours")
    # -- sensitivities on A-60, L = 8
    if 60 in sizes and 8 in lengths:
        for bench in ("QAOA", "RCA", "BV", "VQE"):
            b = bench.lower()
            ours(bench, 60, 8, rid=f"{b}60_L8_onegate", kind="sensitivity", rules_=Rules(one_gate_per_trap=True),
                 label=f"{bench}-60, L=8, at most one gate at a time per trap")
            ours(bench, 60, 8, rid=f"{b}60_L8_tg25", kind="sensitivity", tg_=TRAPSIMD_US["gate2_table"],
                 label=f"{bench}-60, L=8, Table 1's bare 25-us two-qubit gate")
            ours(bench, 60, 8, rid=f"{b}60_L8_swapanywhere", kind="sensitivity", rules_=Rules(swap_gz=False),
                 label=f"{bench}-60, L=8, swaps allowed outside gate zones")
            ours(bench, 60, 8, rid=f"{b}60_L8_gzedge", kind="sensitivity", gz=(1, 6),
                 label=f"{bench}-60, L=8, gate zones next to the trap ends (1, 6)")
        ours("VQE", 60, 8, rid="vqe60_L8_strict", kind="sensitivity", strict=True,
             label="VQE-60, L=8, judged in strict program order (no CX commutation)")
    result = {"paper": PAPER_META, "model": {
        "timing_us": TRAPSIMD_US, "fidelity": TRAPSIMD_F, "rules": dataclasses.asdict(rules),
        "profile": TRAPSIMD.to_json(), "effective_2q_us": tg,
        "doc": "MODEL.md"}, "runs": runs}
    (out / "results.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    return result


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Reproduce TrapSIMD (arXiv 2504.17886) in the timed checker")
    ap.add_argument("out", nargs="?", default=str(Path(__file__).resolve().parents[2] / "Reproduce" / "ruan2025"))
    ap.add_argument("--sizes", default="20,40,60,100")
    ap.add_argument("--lengths", default="8,14")
    a = ap.parse_args()
    reproduce(a.out, sizes=[int(x) for x in a.sizes.split(",")], lengths=[int(x) for x in a.lengths.split(",")])
