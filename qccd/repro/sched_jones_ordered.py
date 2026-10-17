"""Independent standard-wiring transport, with exact per-qubit Stim CX order.

Four Stim step barriers; outbound routes, native gates, and return routes
start independently inside each step. The middle steps use X-then-Z routing
priority, without a barrier between those groups. Each route reserves its
physical residency and declared Jones locks before it starts.
"""
from __future__ import annotations

import argparse
import gzip
import json
from dataclasses import replace
from pathlib import Path
from typing import Mapping

from .importers import jones_device
from .models import JONES, JONES_US, jones_duration
from .sched_jones import _Sim, stim_program
from .timed import Event, PortGraph, TimedSchedule, check


def build(dev: PortGraph, chains: Mapping[str, list[str]], qubits: Mapping[str, str],
          stim: str | Path, *, name: str = "jones_ordered", verify: bool = True) -> TimedSchedule:
    cx, prog, _ = stim_program(stim)
    ion = {q: str(qubits[q]) for q in prog}
    anc = {q for q, p in prog.items()
           if ("op", "reset") in p[p.index(("op", "measure")):]}
    xchecks = {q for q in anc if any(c == q for c, _ in cx)}
    sim = _Sim(dev, chains)
    home = dict(sim.loc)
    ends = {t: {next(n for n in ab if n != t): s
                for s, ab in dev.segments.items() if t in ab} for t in dev.sites}
    layers = []
    k = 0
    for line in Path(stim).read_text().splitlines():
        if line.startswith("CX "):
            count = len(line.split()[1:]) // 2
            layers.append(list(range(k, k + count)))
            k += count
    if len(layers) != 4 or k != len(cx):
        raise ValueError("expected one round with four explicit Stim CX layers")
    events = []
    step_times = []

    def emit(kind, t, ions, locks, **kw):
        meta = kw.pop("meta", {})
        events.append(Event(len(events), kind, t, t + JONES_US[kind], tuple(ions),
                            meta={**meta, "locks": sorted(set(locks))}, **kw))

    def local(q, op, ready, trap=None, **meta):
        i, t = ion[q], trap or home[ion[q]]
        kind = "gate1" if op in ("RX", "RY") else op
        locks = [f"ion:{i}", f"trap:{t[1:]}"]
        duration = JONES_US[kind]
        blocks = [(lock, 0, duration) for lock in locks]
        start = sim._fit(ready, blocks)
        sim._take(start, blocks)
        op_name = {"RX": "XRotation", "RY": "YRotation", "reset": "QubitReset",
                   "measure": "Measurement"}[op]
        emit(kind, start, [i], locks, at=t, meta={"op": op_name, **meta})
        return start + duration

    def hop(i, dst, ready, step, returning):
        src = sim.loc[i]
        shared = set(ends[src]) & set(ends[dst])
        if len(shared) != 1:
            raise ValueError(f"{src} and {dst} must share exactly one junction")
        j = next(iter(shared))
        s1, s2 = ends[src][j], ends[dst][j]
        ch = sim.chain[src]
        side = dev.port(src, s1)
        if side not in ("L", "R") or ch[0 if side == "L" else -1] != i:
            raise ValueError(f"ion {i} cannot leave {src} through {side} without a swap")
        T1, T2 = f"trap:{src[1:]}", f"trap:{dst[1:]}"
        S1, S2, J = f"segment:{s1[1:]}", f"segment:{s2[1:]}", f"junction:{j[1:]}"
        # Explicit segment/junction residency, including split and merge ends.
        offsets = [0.0]
        for kind in ("split", "transit", "j_enter", "j_exit", "transit", "merge"):
            offsets.append(offsets[-1] + JONES_US[kind])
        a, b, c, d, e, f = offsets[1:]
        blocks = [(f"ion:{i}", 0, f), (T1, 0, a), (T2, e, f),
                  (S1, 0, c), (S2, c, f), (J, 0, a), (J, b, d), (J, e, f)]
        start = sim._fit(ready, blocks)
        sim._take(start, blocks)
        primitives = [
            ("split", 0, dict(at=src, seg=s1), [T1, S1, J], "Split"),
            ("transit", a, dict(seg=s1), [S1], "Move"),
            ("j_enter", b, dict(src=s1, via=j), [S1, J], "JunctionCrossing"),
            ("j_exit", c, dict(via=j, dst=s2), [S2, J], "JunctionCrossing"),
            ("transit", d, dict(seg=s2), [S2], "Move"),
            ("merge", e, dict(at=dst, seg=s2), [T2, S2, J], "Merge"),
        ]
        for kind, offset, kw, locks, op in primitives:
            emit(kind, start + offset, [i], locks,
                 meta={"op": op, "step": step, "returning": returning}, **kw)
        ch.remove(i)
        if dev.port(dst, s2) == "L":
            sim.chain[dst].insert(0, i)
        else:
            sim.chain[dst].append(i)
        sim.loc[i] = dst
        return start + f

    ready = []
    for q, p in prog.items():
        t = 0.0
        for kind, op in p:
            if kind == "cx":
                break
            t = local(q, op, t, phase="prefix")
        ready.append(t)
    clock = max(ready)
    for step, ks in enumerate(layers, 1):
        # Listing priority is not a time barrier: each route fits independently.
        order = sorted(ks, key=lambda k: (cx[k][0] not in xchecks, k)) if step in (2, 3) else ks
        used = [q for k in order for q in cx[k]]
        if len(set(used)) != len(used):
            raise ValueError("a Stim step must use each qubit at most once")
        start = clock
        arrivals = {}
        visitors = {}
        for k in order:
            c, target = cx[k]
            a, data = (c, target) if c in anc else (target, c)
            visitors[k] = ion[a]
            arrivals[k] = hop(ion[a], home[ion[data]], clock, step, False)
        finishes = {}
        for k in order:
            c, target = cx[k]
            trap = sim.loc[visitors[k]]
            t = arrivals[k]
            for q, op in ((c, "RY"), (c, "RX"), (target, "RX")):
                t = local(q, op, t, trap, step=step, cx_index=k)
            locks = [f"ion:{ion[c]}", f"ion:{ion[target]}", f"trap:{trap[1:]}"]
            blocks = [(lock, 0, JONES_US["gate"]) for lock in locks]
            t = sim._fit(t, blocks)
            sim._take(t, blocks)
            emit("gate", t, [ion[c], ion[target]], locks, at=trap,
                 meta={"op": "TwoQubitMSGate", "step": step, "cx_index": k})
            finishes[k] = local(c, "RY", t + JONES_US["gate"], trap, step=step, cx_index=k)
        clock = max(hop(visitors[k], home[visitors[k]], finishes[k], step, True) for k in order)
        step_times.append({"step": step, "start_us": start, "end_us": clock})
    tail_start = clock
    for q, p in prog.items():
        last = max(j for j, it in enumerate(p) if it[0] == "cx")
        t = tail_start
        for _, op in p[last + 1:]:
            t = local(q, op, t, phase="suffix")
    if sim.loc != home:
        raise ValueError("not every ion returned home")
    sched = TimedSchedule(name, dev, {t: list(chains.get(t, [])) for t in dev.sites},
                          sorted(events, key=lambda e: (e.t0, e.id)), ion,
                          source={"tool": "qccd.repro.sched_jones_ordered", "steps": step_times,
                                  "routing": "independent outbound, native CX, return; step barriers"})
    if verify:
        rep = check(sched, JONES, duration=jones_duration, circuit=cx, strict=True)
        # JONES allows commuting runs; additionally demand exact per-qubit order.
        exact = check(sched, replace(JONES, circuit_order="strict"),
                      duration=jones_duration, circuit=cx, strict=True)
        if not rep.ok or not exact.ok:
            raise ValueError(f"checker rejects ordered schedule: {rep.summary()} {exact.summary()}")
    return sched


def main():
    root = Path(__file__).resolve().parents[2] / "Reproduce/jones2025"
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--distance", type=int, choices=(2, 3, 6, 12), default=3)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    with gzip.open(root / f"artifact/grid_rot_d{args.distance}_c2.schedule.json.gz", "rt") as f:
        raw = json.load(f)
    circuit = root / f"circuits/rotated_memory_z_d{args.distance}_r1.stim"
    sched = build(jones_device(raw["device"]),
                  {f"T{t}": [str(i) for i in ch] for t, ch in raw["initial_layout"].items()},
                  raw["qubit_to_ion"], circuit)
    out = args.out or root / f"ours/grid_rot_d{args.distance}_c2_ordered.schedule.json.gz"
    with gzip.open(out, "wt") as f:
        json.dump(sched.to_json(), f, separators=(",", ":"))
    cx, _, _ = stim_program(circuit)
    print(json.dumps({"schedule": str(out), "check": check(
        sched, JONES, duration=jones_duration, circuit=cx, strict=True).summary()}, indent=2))


if __name__ == "__main__":
    main()
