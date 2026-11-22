"""A studio machine as a QCCDCert hardware program.

QCCDCert's hardware program has sites, paths and calibrations (paper, Section 2.1).  A studio
machine (`qccd.arch.Architecture`) has traps, junctions and the segments between them, zone
types that say what a trap can do, and a cost model with the time of every primitive.  The
translation is a function of the machine and of the program's waveforms, and every choice it
makes is printed on the page that uses it (`Translation.notes`):

* **sites**  every trap (its capacity) and every junction (capacity 1: a junction passes one
  ion at a time); a two-way bend is not a site, it is part of the path through it;
* **paths**  one per ordered pair of traps that a route joins without passing a third trap;
  `via` lists the junctions it passes.  A studio machine moves its ions with shared
  waveforms (rule R22: one cycle is one waveform), so every path is on ONE shared transport
  channel (`share channel 0 optional`: switches exist, no spectator is forced) and its
  waveform is its *motion* -- the hop-by-hop directions, written in `wiring` -- together with
  its duration, so paths that share a waveform make the same motion in the same time
  (QCCDCert's `Uniform(H)`);
* **durations**  whole microseconds, rounded up, from the machine's cost model: a path costs
  its split, its segments, its junction crossings and its merge, as the studio's replay
  charges one move (`CostModel.move`); a calibration costs the gate's time;
* **calibrations**  QCCDCert binds one *exact* waveform to a site (no angle is approximated),
  so every gate-capable trap gets one calibration per waveform the program needs -- the
  compiler lists them itself (`qccd-dynamic catalogue`) -- on that trap's own optical
  channel, plus one broadcast R per R waveform across every gate zone with at most one ion
  per zone (rule R12: one gate per trap per cycle), readout and optical pumping where the
  zone measures, and a controller latch of 1 µs.

The studio rules that are assertions over one frame join the rule program (`rules`), so
QCCDCert enforces them under its theorem: R1 (capacity, during the event), junction rest,
R22 (one waveform per transport channel per event) and R12 (one optical action per site).

Closed loops (conveyors) are QCCDCert's own broadcast transport, not an addition to it: a
loop drives its ions with one waveform that moves every ion on it one slot, so each closed loop
is a transport channel declared `mandatory` (no switches: every ion at the source of one of its
paths must be listed as moving), with one path per slot in each direction, waveform 0 (+1 slot)
and waveform 1 (-1 slot), and one duration (the slowest slot-to-slot shuttle, no split or
merge: the chain rides the potential).  QCCDCert's core checks then refuse a rotation that
leaves an ion behind, and its theorem covers every rotation the router emits.  An open loop
(a chain) is ordinary geometry.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from .toolchain import Toolchain, find

CONFIG = {"broadcastPlan": None, "enableBroadcast": True, "initialPlacements": [], "maxBatch": 2,
          "maxCandidates": 256, "maxDepth": 24, "maxPlacements": 64, "maxStates": 2000,
          "routeLookahead": 0, "useOnlyInitialPlacements": False}


class Untranslatable(ValueError):
    """The machine has a feature a QCCDCert hardware program cannot state."""


@dataclass
class Translation:
    hardware: dict
    site_ids: list[str]                 # QCCDCert site index -> studio node id
    path_segments: list[list[str]]      # QCCDCert path index -> studio segment ids, in order
    path_nodes: list[list[str]]         # QCCDCert path index -> studio node ids, in order
    notes: list[str] = field(default_factory=list)
    rotations: dict = field(default_factory=dict)   # path index -> (loop id, delta) for loop paths
    rest: list = field(default_factory=list)        # site indices no ion may end an event on (R23)
    loop_channels: dict = field(default_factory=dict)  # loop id -> its transport channel


def _us(x: float) -> int:
    return max(1, int(math.ceil(round(float(x), 6))))


def waveforms(qasm_path: str | Path, tc: Toolchain | None = None, profile: str = "qiskit") -> list[dict]:
    """The exact waveforms a program needs, as the compiler enumerates them."""
    tc = tc or find()
    out = tc.run("qccd-dynamic", "catalogue", Path(qasm_path).resolve(), "--qasm-profile", profile)
    return json.loads(out)["waveforms"]


def translate(arch, model, wave_list: list[dict], *, name: str | None = None) -> Translation:
    dev = arch.device
    traps = [n for n in dev.sites()]
    # a junction that is also a trap (three rails meeting at a site) is a site already
    junctions = [nid for nid in dev.junction_nodes if not dev.nodes[nid].is_site]
    site_ids = [n.id for n in traps] + list(junctions)
    index = {nid: i for i, nid in enumerate(site_ids)}
    # rule R2: a node where three or more rails meet holds at most one ion at any instant, even
    # when it is a trap -- in the hardware program that is its capacity, which the core checks enforce
    sites = [{"name": n.id, "capacity": 1 if dev.is_junction(n.id) else int(n.capacity), "operations": []}
             for n in traps]
    sites += [{"name": j, "capacity": 1, "operations": []} for j in junctions]

    # paths: walk from each trap through non-trap nodes to the first trap reached
    paths, durations, segs_of, nodes_of = [], [], [], []
    for src in traps:
        if dev.is_junction(src.id):
            continue            # no ion stands on it (R23), so no route starts there
        stack = [(src.id, [src.id], [])]
        while stack:
            node, nodes, segs = stack.pop()
            for sid in dev.incidence[node]:
                seg = dev.segments[sid]
                nxt = seg.other(node)
                if nxt in nodes:
                    continue
                if dev.nodes[nxt].is_site:
                    if nxt == src.id:
                        continue
                    if dev.is_junction(nxt):
                        # rule R23: a junction is crossed, not rested on -- a route passes through
                        # a trap where three rails meet (it is in `via`) and never ends there
                        stack.append((nxt, nodes + [nxt], segs + [sid]))
                        continue
                    route_nodes, route_segs = nodes + [nxt], segs + [sid]
                    us = 0.0
                    for k, s in enumerate(route_segs):
                        a, b = route_nodes[k], route_nodes[k + 1]
                        us += model.move(arch, dev.segments[s], a, b,
                                         entails=(["split", "merge"] if k == 0 else [])).us
                    via = [index[x] for x in route_nodes[1:-1] if x in index]
                    paths.append({"src": index[src.id], "dst": index[nxt], "via": via,
                                  "channel": 0, "waveform": 0})
                    durations.append(_us(us))
                    segs_of.append(route_segs)
                    nodes_of.append(route_nodes)
                else:
                    stack.append((nxt, nodes + [nxt], segs + [sid]))

    # waveform = (motion, duration): equal waveforms make the same motion in the same time
    motions, waves_of = [], {}
    for p, nodes, d in zip(paths, nodes_of, durations):
        hops = []
        for a, b in zip(nodes, nodes[1:]):
            (xa, ya, *_), (xb, yb, *_) = dev.nodes[a].pos, dev.nodes[b].pos
            dx, dy = xb - xa, yb - ya
            hops.append("".join(s for s in (("+x" if dx > 1e-9 else "-x" if dx < -1e-9 else ""),
                                            ("+y" if dy > 1e-9 else "-y" if dy < -1e-9 else ""))) or "0")
        motions.append(hops)
        p["waveform"] = waves_of.setdefault((tuple(hops), d), len(waves_of))

    # closed loops: one mandatory channel each, a rotation by one slot in either direction
    shares = [{"channel": 0, "mandatory": False}]
    rotations, loop_channels = {}, {}
    for k, (lid, loop) in enumerate(sorted(dev.loops.items())):
        if not loop.closed:
            continue
        slots = list(dev.loop_slots(lid))
        if any(x not in index for x in slots):
            raise Untranslatable(f"loop {lid} has a slot that is not a site or a junction")
        ch = 1 + k
        loop_channels[lid] = ch
        shares.append({"channel": ch, "mandatory": True})
        n = len(slots)
        steps = []
        for i in range(n):
            a, b = slots[i], slots[(i + 1) % n]
            seg = dev.segment_between(a, b)
            steps.append((a, b, seg.id, model.move(arch, seg, a, b, entails=[]).us))
        d = _us(max(us for *_, us in steps))
        for delta, wave, order in ((1, 0, steps), (-1, 1, [(b, a, sid, us) for a, b, sid, us in steps])):
            for a, b, sid, _ in order:
                rotations[len(paths)] = (lid, delta)
                paths.append({"src": index[a], "dst": index[b], "via": [], "channel": ch, "waveform": wave})
                durations.append(d)
                segs_of.append([sid])
                nodes_of.append([a, b])
                motions.append([f"{lid}{'+' if delta > 0 else '-'}1"])

    zone = lambda n: arch.zone_types.get(n.zone_type or "", {})  # noqa: E731
    # no ion rests where three rails meet (R23), so such a trap carries no calibration
    gate_traps = [n for n in traps if zone(n).get("gate") and not dev.is_junction(n.id)]
    spam_traps = [n for n in traps if zone(n).get("spam") and not dev.is_junction(n.id)]
    if not gate_traps:
        raise Untranslatable(f"{arch.name} has no gate zone")
    base_ch = 1000  # optical channels live in their own namespace; keep them visibly apart
    optical = {n.id: base_ch + 1 + i
               for i, n in enumerate(sorted(set(gate_traps) | set(spam_traps), key=lambda n: n.id))}
    global_ch = base_ch  # the broadcast beam that reaches every gate zone
    r_us, ms_us = _us(model.gate_1q(arch, "R", 1).us), _us(model.gate(arch, "MS", 1).us)
    cals, profiles = [], []
    for n in gate_traps:
        for w in wave_list:
            kind = next(iter(w))
            if kind == "ms" and n.capacity < 2:
                continue
            cals.append({"site": index[n.id], "channel": optical[n.id],
                         "duration": ms_us if kind == "ms" else r_us, "waveform": w})
    for w in wave_list:
        if next(iter(w)) == "r":
            profiles.append({"channel": global_ch, "sites": [index[n.id] for n in gate_traps],
                             "theta": w["r"]["theta"], "phi": w["r"]["phi"], "duration": r_us,
                             "maxPerSite": 1})
    readouts = [{"site": index[n.id], "channel": optical[n.id], "duration": _us(model.measure(arch).us)} for n in spam_traps]
    pumps = [{"site": index[n.id], "channel": optical[n.id], "duration": _us(model.reset(arch).us)} for n in spam_traps]
    hw = {"config": CONFIG, "rules": [], "wiring": {"motions": motions},
          "device": {"chainLengths": None, "controller": {"duration": 1}, "readouts": readouts, "pumps": pumps,
                     "base": {"name": name or arch.name, "pathDurations": durations,
                              "opticalChannels": sorted({global_ch, *optical.values()}),
                              "geometry": {"sites": sites, "paths": paths, "broadcasts": shares},
                              "calibrations": cals, "broadcasts": profiles}}}
    notes = [
        f"{len(traps)} traps and {len(junctions)} junctions become {len(sites)} sites; "
        f"{len(paths)} paths join traps through junctions, all on one shared transport channel "
        f"with {len(waves_of)} waveforms, one per motion.",
        f"Durations are whole microseconds from the machine's cost model ({model.name}): "
        f"a path is its split, segments, junction crossings and merge; R {r_us} µs, MS {ms_us} µs, "
        f"readout {readouts[0]['duration'] if readouts else '-'} µs, pumping {pumps[0]['duration'] if pumps else '-'} µs.",
        f"{len(gate_traps)} gate zones get one calibration per exact waveform of the program "
        f"({len(wave_list)} waveforms), on their own optical channels; a broadcast R reaches every "
        f"gate zone, one ion per zone.",
    ]
    if loop_channels:
        notes.append(f"{len(loop_channels)} closed loop(s) ({', '.join(loop_channels)}) each drive their ions on one "
                     f"mandatory transport channel: a rotation moves every ion on the loop one slot, in one event.")
    rest = [index[n.id] for n in traps if dev.is_junction(n.id)] + [index[j] for j in junctions]
    return Translation(hw, site_ids, segs_of, nodes_of, notes, rotations=rotations,
                       loop_channels=loop_channels, rest=rest)
