"""Heat across the town: every ion's n̄ followed from where it is loaded to where it is dumped.

Inside a place, an op's replay follows every ion's motional energy (n̄) move by move: a move
adds what the cost model charges, a cool sets the ions it cools to 0 (`qccd/verify/replay.py`).
Between places nothing did: each op was characterized as if its ions arrived cold, and a
bundle crossing a street -- a junction at every door, each pass charging the bundle that
waits at its arm -- was never charged at all.  This module follows the heat the same way the
replay does, across the whole run:

* **an op** is one cold replay of its TSIR, read per ion (`op_heat`).  Heat only rises
  between cools, so the cold replay says everything an arrival changes: an ion the op cools
  peaks at `arrival + pre` before its first cool (or at `post`, the hottest it gets after) and
  leaves at `out`; an ion it never cools carries its arrival through: it leaves at
  `arrival + out`.  A gate on an ion before its first cool sees `arrival + gate_pre`.
* **a road** is its conveyor's replay (`chan<L>.carry`, one ion): it adds `road_heat(L)`.
* **a junction pass** is an op like any other (it cools nobody).

`follow(gir, ...)` walks every ion through its rows in the order it meets them and returns, per
event, the hottest its ions arrive, peak and leave, and every place an ion passes the
ion-loss limit (G6) or meets a gate hotter than the op's cooling policy allows.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

__all__ = ["IonHeat", "op_heat", "road_heat", "follow", "HEAT_FAMILIES_SKIPPED"]

#: places that hold no ions: their ops are modeled, not replayed
HEAT_FAMILIES_SKIPPED = ("decoder", "archive")


@dataclass(frozen=True)
class IonHeat:
    """What one op does to one of its ions' n̄, from its cold replay."""
    cooled: bool            # the op cools it at least once
    pre: float              # n̄ the op adds before its first cool (all of it, if never cooled)
    post: float             # the hottest it gets after its first cool (0 if never cooled)
    out: float              # n̄ at the op's end: absolute if cooled, added if not
    gate_pre: float | None  # the hottest a gate meets it before its first cool, if one does
    enter_us: float         # when it enters the op (0 for an ion already there)
    enters: bool            # it comes in through a door during the op
    leaves: bool            # it leaves through a door during the op

    def through(self, arrive: float) -> tuple[float, float, float | None]:
        """(peak inside, n̄ at the end, n̄ at its hottest gate before a cool) for an ion that
        arrives at n̄ `arrive`."""
        gate = None if self.gate_pre is None else arrive + self.gate_pre
        if self.cooled:
            return max(arrive + self.pre, self.post), self.out, gate
        return arrive + self.pre, arrive + self.out, gate


_OP_MEMO: dict = {}
_ROAD_MEMO: dict = {}


def op_heat(leaves, master, op: str) -> dict | None:
    """{local ion: IonHeat} for op `op` of the characterized `master` (a `Master`), or None
    for an op with no TSIR (a classical place's).  One cold replay, memoized by the program."""
    d = leaves.data.get(master.name) if leaves is not None else None
    prog_json = ((d or {}).get("programs") or {}).get(op)
    O = master.ops.get(op)
    if prog_json is None or O is None:
        return None
    key = (master.name, op)
    hit = _OP_MEMO.get(key)
    if hit is not None and hit[0] is prog_json:
        return hit[1]
    from ..arch.device import Architecture
    from ..cost.models import corrected_model
    from ..ir.tsir import TSIR
    from ..verify.replay import replay
    prog = TSIR.from_json(prog_json)
    arch = Architecture.from_json(d["arch"])
    out = _replay_heat(prog, arch, corrected_model("qccdsim_jones"), replay)
    # when each ion enters, and which leave, from the op's characterized transfers
    enter, leaving = {}, set()
    for tr in O.transfers:
        for name, t in zip(tr.ions, tr.times_us):
            if tr.dir == "in":
                enter[name] = min(enter.get(name, t), t)
            elif tr.dir == "out":
                leaving.add(name)
    res = {ion: IonHeat(h["cooled"], h["pre"], h["post"], h["out"], h["gate_pre"],
                        float(enter.get(ion, 0.0)), ion in enter, ion in leaving)
           for ion, h in out.items()}
    _OP_MEMO[key] = (prog_json, res)
    return res


def _replay_heat(prog, arch, model, replay) -> dict:
    init = prog.instructions[0] if prog.instructions and prog.instructions[0].type == "init" else None
    ions = list(init.placement) if init is not None else []
    st = {ion: {"cooled": False, "pre": 0.0, "post": 0.0, "gate_pre": None} for ion in ions}

    def probe(ins, current):
        if ins.type == "cool":
            hit = list(ins.ions) if ins.ions else (list(current) if ins.broadcast else [])
            for ion in hit:
                s = st.setdefault(ion, {"cooled": False, "pre": 0.0, "post": 0.0, "gate_pre": None})
                q = float(current.get(ion, 0.0))
                if s["cooled"]:
                    s["post"] = max(s["post"], q)
                else:
                    s["cooled"], s["pre"] = True, q
        else:
            # the cooling policy bounds n̄ at a TWO-qubit gate (R7): the motional mode is the bus
            gated = set()
            if ins.type == "gate" and ins.pairs:
                gated = {x for p in ins.pairs for x in p}
            for ion, q in current.items():
                s = st.setdefault(ion, {"cooled": False, "pre": 0.0, "post": 0.0, "gate_pre": None})
                q = float(q)
                if s["cooled"]:
                    s["post"] = max(s["post"], q)
                elif ion in gated:
                    s["gate_pre"] = max(s["gate_pre"] or 0.0, q)

    final = replay(prog, arch, model, check_rules=False, keep_cycles=False, probe=probe).final_quanta
    out = {}
    for ion, s in st.items():
        q = float(final.get(ion, 0.0))
        if s["cooled"]:
            out[ion] = dict(s, post=max(s["post"], q), out=q)
        else:
            out[ion] = dict(s, pre=q, out=q)
    return out


def road_heat(leaves, length: int) -> float:
    """n̄ one ion gains crossing a road of `length` sites: its conveyor's replay."""
    L = max(1, int(length))
    if L not in _ROAD_MEMO:
        _ROAD_MEMO[L] = float(leaves.channel_timing(L, 1)["quanta"])
    return _ROAD_MEMO[L]


def follow(gir: dict, lib, leaves, *, limit: float | None = None, initial_heat: float = 0.0) -> dict:
    """Follow every ion's n̄ through `gir` (GIR 0.2, or one view of a folded run).

    Returns {"events": {event id: {"arrive", "peak", "exit"}}, "carries": {carry id: hottest
    at its far end}, "violations": [...], "hot_gates": [...], "metrics": {...}}.  An ion the
    view starts with (a block passed in, resting in its home memory) starts at
    `initial_heat`; an ion a depot loads starts at 0 and gains what the load does."""
    from .checks import ION_LOSS_QUANTA
    limit = float(ION_LOSS_QUANTA if limit is None else limit)
    masters = {p: lib[v[0]] for p, v in gir["leaves"].items()}
    # a place's residents (a memory's ancillas) are in every op it runs, and carry their heat
    # from one op to the next; an event binds only the ions that visit
    order = gir.get("ion_order") or {}
    residents = {p: [[nm, v[3] + k] for k, nm in enumerate(order.get(v[0], []))]
                 for p, v in gir["leaves"].items() if len(v) > 3}
    rows = defaultdict(list)
    for e in gir["events"]:
        m = masters.get(e[3])
        if m is None or m.family in HEAT_FAMILIES_SKIPPED:
            continue
        hs = op_heat(leaves, m, e[4])
        if hs is None:
            continue
        for name, gid in list(e[8] or []) + residents.get(e[3], []):
            h = hs.get(name)
            if h is not None and isinstance(gid, int):
                rows[gid].append((e[1] + h.enter_us, 1, e, name, h))
    for c in gir["carries"]:
        net = gir["nets"].get(c[1])
        q = road_heat(leaves, net[4]) if net else 0.0
        for k, gid in enumerate(c[7]):
            rows[gid].append((c[4] + (c[5][k] if k < len(c[5]) else 0.0), 0, c, None, q))
    ev: dict = {}
    ca: dict = {}
    violations, hot_gates, arrivals = [], [], []
    hottest = (0.0, None)
    longest = 0
    for gid, rs in rows.items():
        n = initial_heat
        juncs = roads = 0
        for t, kind, row, name, h in sorted(rs, key=lambda r: (r[0], r[1])):
            if kind == 0:
                n += h
                roads += 1
                ca[row[0]] = max(ca.get(row[0], 0.0), n)
                continue
            e = row
            peak, after, gate = h.through(n)
            fam = masters[e[3]].family
            rec = ev.setdefault(e[0], {"arrive": 0.0, "peak": 0.0, "exit": 0.0})
            rec["arrive"] = max(rec["arrive"], n)
            rec["peak"] = max(rec["peak"], peak)
            if h.leaves:
                rec["exit"] = max(rec["exit"], after)
            if fam != "road" and h.enters:
                arrivals.append(n)
            if peak > hottest[0]:
                hottest = (peak, (gid, e[3], e[4], round(e[1], 3), juncs, roads))
            if peak > limit + 1e-9:
                violations.append(
                    f"ion {gid} reaches n̄ = {peak:.0f} in {e[3]} {e[4]} at {e[1] / 1000:.3f} ms "
                    f"(arrived at {n:.0f} after {juncs} junction passes and {roads} roads with no "
                    f"cooling), past the ion-loss limit {limit:.0f}")
            pol = _policy(leaves, masters[e[3]].name, e[4])
            if gate is not None and gate > pol + 1e-9:
                hot_gates.append(f"ion {gid}: a gate of {e[3]} {e[4]} at {e[1] / 1000:.3f} ms meets it "
                                 f"at n̄ = {gate:.2f} (it arrived at {n:.2f}; the op cools to {pol} "
                                 f"before a gate)")
            if fam == "road":
                juncs += 1
            if h.cooled:
                longest = max(longest, juncs)
                juncs = roads = 0
            n = after
    arrivals.sort()
    return {"events": ev, "carries": ca, "violations": violations, "hot_gates": hot_gates,
            "metrics": {"hottest_ion_quanta": round(hottest[0], 2),
                        "hottest_where": hottest[1],
                        "arrival_quanta_median": round(arrivals[len(arrivals) // 2], 2) if arrivals else 0.0,
                        "arrival_quanta_max": round(arrivals[-1], 2) if arrivals else 0.0,
                        "most_junction_passes_uncooled": longest,
                        "ion_loss_limit": limit}}


def _policy(leaves, master: str, op: str) -> float:
    d = leaves.data.get(master) or {}
    meta = (((d.get("programs") or {}).get(op) or {}).get("meta") or {})
    return float((meta.get("cooling_policy") or {}).get("max_gate_quanta") or 1.0)
