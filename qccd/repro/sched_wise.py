"""Lockstep WISE schedule on the unchanged Jones device and initial mapping.

Each pass sends one direction group out, applies the source native CX
decomposition in parallel across traps, and returns every visitor home.
No physical code from the Jones artifact is used.
"""
from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter
from pathlib import Path
from typing import Mapping

from .importers import jones_device
from .models import JONES_US, JONES_WISE, jones_duration
from .__main__ import ours_profile
from .sched_jones import stim_program
from .timed import Event, PortGraph, TimedSchedule, check


def _direction(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    if (dx == 0) == (dy == 0):
        raise ValueError(f"expected a nonzero axis-aligned leg: {a} -> {b}")
    return ("E" if dx > 0 else "W") if dx else ("N" if dy > 0 else "S")


def build(dev: PortGraph, chains: Mapping[str, list[str]], qubits: Mapping[str, str],
          stim: str | Path, *, name: str = "jones_wise", verify: bool = True,
          steps: int = 4, zone_opt_out: bool = True,
          separate_junction_classes: bool = True) -> TimedSchedule:
    """Build a circuit prefix (steps=1) or full round (steps=4).

    Retains Stim's layers and exact per-qubit CNOT order.
    Disabling opt-out requires every occupied zone to participate in each pass;
    this layout cannot meet that requirement and is rejected explicitly.
    With separate_junction_classes=False entry/exit share the primitive class
    'junction'; their durations and six distinct cycles remain unchanged.
    """
    if steps not in (1, 4):
        raise ValueError("steps must be 1/4")
    cx, prog, _ = stim_program(stim)
    ion = {q: str(qubits[q]) for q in prog}
    anc = {q for q, p in prog.items()
           if ("op", "reset") in p[p.index(("op", "measure")):]}
    xchecks = {q for q in anc if any(c == q for c, _ in cx)}
    home = {i: t for t, ch in chains.items() for i in ch}
    ends = {t: {next(n for n in ab if n != t): s
                for s, ab in dev.segments.items() if t in ab} for t in dev.sites}
    layers = [[] for _ in range(4)]
    source_layer = {}
    # Explicit layer boundaries come from Stim, not the flattened per-qubit list.
    k = 0
    for step, line in enumerate(l for l in Path(stim).read_text().splitlines()
                                if l.startswith("CX ")):
        if step >= 4:
            raise ValueError("expected four CX layers")
        for _ in range(len(line.split()[1:]) // 2):
            source_layer[k] = step
            k += 1
    if k != len(cx) or set(source_layer.values()) != set(range(4)):
        raise ValueError("expected one round with four explicit CX layers")
    for k, (c, target) in enumerate(cx):
        a, d = (c, target) if c in anc else (target, c)
        step = source_layer[k]
        layers[step].append((a, d, c, target, k))
    events = []
    clock = 0.0
    transport = []
    passes = []

    def emit(kind, ions, locks, *, duration=None, **kw):
        meta = kw.pop("meta", {})
        events.append(Event(len(events), kind, clock,
                            clock + (JONES_US[kind] if duration is None else duration),
                            tuple(ions), meta={**meta, "locks": sorted(set(locks))}, **kw))

    def local(q, op, trap=None, **meta):
        i = ion[q]
        t = trap or home[i]
        kind = "gate1" if op in ("RX", "RY") else op
        opname = {"RX": "XRotation", "RY": "YRotation", "reset": "QubitReset",
                  "measure": "Measurement"}[op]
        emit(kind, [i], [f"ion:{i}", f"trap:{t[1:]}"], at=t,
             meta={"op": opname, **meta})

    # Per-qubit source prefix, padded by global barriers for simplicity.
    prefix = {q: p[:next(j for j, it in enumerate(p) if it[0] == "cx")]
              for q, p in prog.items()}
    for n in range(max(map(len, prefix.values()))):
        ops = [(q, p[n][1]) for q, p in prefix.items() if n < len(p)]
        for q, op in ops:
            local(q, op, phase="prefix")
        clock += max(JONES_US["gate1" if op in ("RX", "RY") else op] for _, op in ops)

    def move(group, returning, step, pass_no):
        nonlocal clock
        routes = []
        occupied = {t for t, ch in chains.items() if ch}
        for a, d, *_ in group:
            src, dst = home[ion[a]], home[ion[d]]
            if returning:
                src, dst = dst, src
            shared = set(ends[src]) & set(ends[dst])
            if len(shared) != 1:
                raise ValueError(f"{src}, {dst} must share exactly one junction")
            j = next(iter(shared))
            s1, s2 = ends[src][j], ends[dst][j]
            p1, pj, p2 = dev.sites[src]["pos"], dev.nodes[j]["pos"], dev.sites[dst]["pos"]
            routes.append((ion[a], src, dst, j, s1, s2,
                           _direction(p1, pj), _direction(pj, p2)))
        if len({r[-2:] for r in routes}) != 1:
            raise ValueError("pass mixes physical directions")
        if not zone_opt_out and {r[1] for r in routes} != occupied:
            raise ValueError("zone_opt_out=False: occupied zones would sit out this pass")
        for primitive in ("split", "transit_out", "j_enter", "j_exit", "transit_in", "merge"):
            kind = "transit" if primitive.startswith("transit_") else primitive
            for i, src, dst, j, s1, s2, d1, d2 in routes:
                direction = d1 if primitive in ("split", "transit_out", "j_enter") else d2
                cls = "junction" if not separate_junction_classes and kind in ("j_enter", "j_exit") else kind
                T1, T2 = f"trap:{src[1:]}", f"trap:{dst[1:]}"
                S1, S2, J = f"segment:{s1[1:]}", f"segment:{s2[1:]}", f"junction:{j[1:]}"
                kw, locks = {
                    "split": (dict(at=src, seg=s1), [T1, S1, J]),
                    "transit_out": (dict(seg=s1), [S1]),
                    "j_enter": (dict(src=s1, via=j), [S1, J]),
                    "j_exit": (dict(via=j, dst=s2), [S2, J]),
                    "transit_in": (dict(seg=s2), [S2]),
                    "merge": (dict(at=dst, seg=s2), [T2, S2, J]),
                }[primitive]
                emit(kind, [i], locks, meta={"class": f"{cls}:{direction}",
                     "step": step, "pass": pass_no, "returning": returning}, **kw)
            clock += JONES_US[kind]

    for step, layer in enumerate(layers[:steps], 1):
        groups = [layer]
        if step in (2, 3):
            groups = [[p for p in layer if p[0] in xchecks],
                      [p for p in layer if p[0] not in xchecks]]
        spent = 0.0
        for pass_no, group in enumerate(groups, 1):
            if not group:
                continue
            qs = [q for p in group for q in (p[2], p[3])]
            if len(set(qs)) != len(qs):
                raise ValueError("parallel CX pass shares a qubit")
            passes.append({"step": step, "pass": pass_no, "start_us": clock,
                           "cx_indices": [p[4] for p in group]})
            start = clock
            move(group, False, step, pass_no)
            spent += clock - start
            # Native CX: RY(control), RX(control), RX(target), MS, RY(control).
            # The two single-qubit rotations in a shared trap cannot overlap.
            for which, op in ((2, "RY"), (2, "RX"), (3, "RX"), (None, "MS"), (2, "RY")):
                for a, d, c, target, k in group:
                    trap = home[ion[d]]
                    meta = dict(step=step, **{"pass": pass_no}, cx_index=k)
                    if which is None:
                        emit("gate", [ion[c], ion[target]],
                             [f"ion:{ion[c]}", f"ion:{ion[target]}", f"trap:{trap[1:]}"],
                             at=trap, meta={"op": "TwoQubitMSGate", **meta})
                    else:
                        local(c if which == 2 else target, op, trap, **meta)
                clock += JONES_US["gate" if which is None else "gate1"]
            start = clock
            move(group, True, step, pass_no)
            spent += clock - start
            passes[-1]["end_us"] = clock
        transport.append(spent)

    if steps == 4:
        # All visitors are home. Preserve each qubit's source suffix; different
        # traps execute independently, so data measurements need not wait for H.
        tail_start = clock
        end = clock
        for q, p in prog.items():
            last = max(j for j, it in enumerate(p) if it[0] == "cx")
            clock = tail_start
            for _, op in p[last + 1:]:
                local(q, op, phase="suffix")
                clock += JONES_US["gate1" if op in ("RX", "RY") else op]
            end = max(end, clock)
        clock = end
    sched = TimedSchedule(name, dev, {t: list(chains.get(t, [])) for t in dev.sites},
                          sorted(events, key=lambda e: (e.t0, e.id)), ion,
                          source={"tool": "qccd.repro.sched_wise", "middle_steps": "split",
                                  "steps": steps, "zone_opt_out": zone_opt_out,
                                  "separate_junction_classes": separate_junction_classes,
                                  "passes": passes},
                          claims={"transport_us_per_step": transport,
                                  "transport_us_total": sum(transport),
                                  "broadcast_cycles": len({e.t0 for e in events if e.kind in JONES_WISE.broadcast}),
                                  "counts": dict(Counter(e.kind for e in events))})
    if verify:
        expected = cx if steps == 4 else [cx[k] for p in passes for k in p["cx_indices"]]
        rep = check(sched, ours_profile("JONES_WISE"), duration=jones_duration, circuit=expected, strict=True)
        if not rep.ok:
            raise ValueError(f"checker rejects WISE schedule: {rep.summary()}")
    return sched


def main():
    root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--distance", type=int, choices=(2, 3, 6, 12), default=3,
                    help="Jones capacity-2 grid artifact and source circuit (default: 3)")
    ap.add_argument("--steps", type=int, choices=(1, 4), default=4)
    ap.add_argument("--zone-opt-out", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--separate-junction-classes", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    base = root / "Reproduce/jones2025"
    with gzip.open(base / f"artifact/grid_rot_d{args.distance}_c2.schedule.json.gz", "rt") as f:
        raw = json.load(f)
    dev = jones_device(raw["device"])
    chains = {f"T{t}": [str(i) for i in ch] for t, ch in raw["initial_layout"].items()}
    circuit = base / f"circuits/rotated_memory_z_d{args.distance}_r1.stim"
    sched = build(dev, chains, raw["qubit_to_ion"], circuit,
                  steps=args.steps,
                  zone_opt_out=args.zone_opt_out,
                  separate_junction_classes=args.separate_junction_classes)
    suffix = "_split" if args.steps == 1 else ""
    out = args.out or base / "ours" / (f"grid_rot_d{args.distance}_c2_wise{suffix}"
                                       + ("_step1" if args.steps == 1 else "") + ".schedule.json.gz")
    with gzip.open(out, "wt") as f:
        json.dump(sched.to_json(), f, separators=(",", ":"))
    cx, _, _ = stim_program(circuit)
    if args.steps == 1:
        cx = [cx[k] for p in sched.source["passes"] for k in p["cx_indices"]]
    rep = check(sched, ours_profile("JONES_WISE"), duration=jones_duration, circuit=cx, strict=True)
    print(json.dumps({"schedule": str(out), "check": rep.summary(), "claims": sched.claims}, indent=2))


if __name__ == "__main__":
    main()
