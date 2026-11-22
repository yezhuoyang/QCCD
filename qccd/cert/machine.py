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

The translation refuses a machine it cannot express: a closed conveyor loop (rotation is an
extension of this project's own compiler, not a QCCDCert path).
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


def _us(x: float) -> int:
    return max(1, int(math.ceil(round(float(x), 6))))


def waveforms(qasm_path: str | Path, tc: Toolchain | None = None, profile: str = "qiskit") -> list[dict]:
    """The exact waveforms a program needs, as the compiler enumerates them."""
    tc = tc or find()
    out = tc.run("qccd-dynamic", "catalogue", Path(qasm_path).resolve(), "--qasm-profile", profile)
    return json.loads(out)["waveforms"]


def translate(arch, model, wave_list: list[dict], *, name: str | None = None) -> Translation:
    dev = arch.device
    if dev.loops:
        raise Untranslatable(f"{arch.name} has a closed loop ({', '.join(map(str, dev.loops))}); "
                             "conveyor rotation is an extension, not a QCCDCert path")
    traps = [n for n in dev.sites()]
    junctions = [nid for nid in dev.junction_nodes]
    site_ids = [n.id for n in traps] + list(junctions)
    index = {nid: i for i, nid in enumerate(site_ids)}
    sites = [{"name": n.id, "capacity": int(n.capacity), "operations": []} for n in traps]
    sites += [{"name": j, "capacity": 1, "operations": []} for j in junctions]

    # paths: walk from each trap through non-trap nodes to the first trap reached
    paths, durations, segs_of, nodes_of = [], [], [], []
    for src in traps:
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

    zone = lambda n: arch.zone_types.get(n.zone_type or "", {})  # noqa: E731
    gate_traps = [n for n in traps if zone(n).get("gate")]
    spam_traps = [n for n in traps if zone(n).get("spam")]
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
                              "geometry": {"sites": sites, "paths": paths,
                                           "broadcasts": [{"channel": 0, "mandatory": False}]},
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
    return Translation(hw, site_ids, segs_of, nodes_of, notes)
