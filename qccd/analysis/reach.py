"""What the machine can do, before anyone writes a programme for it.

Every number this platform produces today needs a compiled programme: `t1_metrics`,
`t2_metrics` and all twenty-three rules take a `ReplayResult`.  So an architect dragging a
site in the studio sees nothing move unless a benchmark happens to be loaded, and what
they do see is a property of that benchmark rather than of the machine.  qiskit-metal's
headline analyses answer from the geometry alone -- `LOManalysis` gives a transmon's
frequency before any circuit exists -- and that is the gap this module closes.

The observation that makes it cheap: **`CostModel.move()` is already an edge-weight
function.**  It takes a segment and two endpoints and returns `(cost, depth, us, quanta)`.
Run Dijkstra over it and every question below falls out of machinery that is already
parity-checked against the JS mirror:

* how far is every trap from every other, in microseconds and in quanta -- not in hops,
  which is what a graph distance would give and what nobody's error budget is written in;
* how far is each trap from the nearest zone that can cool, gate or measure;
* **which traps cannot reach a cooler inside R7's gate budget at all** -- positions that
  are structurally unusable, because an ion parked there heats past `ms_gate.max_quanta`
  before it can get back to a cooler, whatever programme you write.

That last one is the number worth putting on screen next to a drag.  It is a property of
where the coolers are, and it changes the moment you move one.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from ..arch import Architecture
from ..cost.models import CostModel

__all__ = ["ReachReport", "reach_report", "distance_matrix", "nearest"]


@dataclass
class ReachReport:
    """Distances over the device, in the cost model's own units."""

    name: str
    model: str
    metric: str = "quanta"
    n_sites: int = 0
    #: site -> {capability -> (distance, node)}; distance is `inf` when unreachable
    nearest: Mapping[str, Mapping[str, tuple[float, str | None]]] = field(default_factory=dict)
    #: sites that cannot reach a cooling zone inside the gate budget
    stranded: tuple[str, ...] = ()
    budget: float = float("inf")
    #: the widest separation between two gate-capable sites, and which pair
    diameter: float = 0.0
    diameter_pair: tuple[str, str] | None = None
    unreachable_pairs: int = 0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "name": self.name, "model": self.model, "metric": self.metric,
            "n_sites": self.n_sites, "budget": self.budget,
            "stranded": list(self.stranded), "n_stranded": len(self.stranded),
            "diameter": self.diameter, "diameter_pair": list(self.diameter_pair or ()),
            "unreachable_pairs": self.unreachable_pairs,
            "nearest": {k: {c: [d, n] for c, (d, n) in v.items()}
                        for k, v in self.nearest.items()},
            "notes": list(self.notes),
        }


def _edges(arch: Architecture, model: CostModel, metric: str
           ) -> dict[str, list[tuple[str, float]]]:
    """The device as a weighted graph, weighted by what a move actually costs.

    `metric` is 'quanta', 'us' or 'cost'.  Distance in HOPS is available for free from any
    graph library and is the one unit no error budget is written in, so it is not offered.
    """
    adj: dict[str, list[tuple[str, float]]] = {n: [] for n in arch.device.nodes}
    for seg in arch.device.segments.values():
        a, b = seg.ends
        for src, dst in ((a, b), (b, a)):
            charge = model.move(arch, seg, src, dst)
            if metric == "us":
                w = float(charge.us or 0.0)
            elif metric == "cost":
                w = float(charge.cost or 0.0)
            else:
                w = float(sum((charge.quanta or {}).values()))
            adj[src].append((dst, w))
    return adj


def distance_matrix(arch: Architecture, model: CostModel, *, metric: str = "quanta",
                    sources: Sequence[str] | None = None) -> dict[str, dict[str, float]]:
    """Dijkstra from each source over the cost model's own edge weights."""
    adj = _edges(arch, model, metric)
    srcs = list(sources if sources is not None else arch.device.nodes)
    out: dict[str, dict[str, float]] = {}
    for s in srcs:
        dist = {s: 0.0}
        pq = [(0.0, s)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, math.inf):
                continue
            for v, w in adj.get(u, ()):
                nd = d + w
                if nd < dist.get(v, math.inf):
                    dist[v] = nd
                    heapq.heappush(pq, (nd, v))
        out[s] = dist
    return out


def nearest(arch: Architecture, model: CostModel, capability: str, *,
            metric: str = "quanta") -> dict[str, tuple[float, str | None]]:
    """For every site, the distance to the closest node that `can(capability)`.

    One Dijkstra from the capable set, not one per site: the answer is the same and it is
    a single sweep rather than N of them.
    """
    adj = _edges(arch, model, metric)
    targets = [n for n in arch.device.nodes if arch.can(n, capability)]
    dist: dict[str, float] = {t: 0.0 for t in targets}
    via: dict[str, str | None] = {t: t for t in targets}
    pq = [(0.0, t) for t in targets]
    heapq.heapify(pq)
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, math.inf):
            continue
        for v, w in adj.get(u, ()):
            nd = d + w
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                via[v] = via[u]
                heapq.heappush(pq, (nd, v))
    return {n: (dist.get(n, math.inf), via.get(n))
            for n in arch.device.nodes if arch.device.nodes[n].kind == "site"}


def reach_report(arch: Architecture, model: CostModel, *,
                 metric: str = "quanta",
                 budget: float | None = None) -> ReachReport:
    """Everything above, as one report -- and no programme in sight."""
    sites = [n for n, node in arch.device.nodes.items() if node.kind == "site"]
    rep = ReachReport(name=arch.name, model=getattr(model, "name", "?"), metric=metric,
                      n_sites=len(sites))

    caps = ("cool", "gate", "spam")
    per_cap = {c: nearest(arch, model, c, metric=metric) for c in caps}
    rep.nearest = {s: {c: per_cap[c].get(s, (math.inf, None)) for c in caps} for s in sites}

    # R7's own budget: an ion may not enter a gate carrying more than this. An explicit
    # `budget` answers the design question the device's own number cannot -- "how much
    # better would gates have to get before nothing is stranded" -- so it is a knob.
    if budget is not None:
        rep.budget = float(budget)
    else:
        try:
            rep.budget = float(arch.primitives.scalar("ms_gate").get("max_quanta", math.inf))
        except Exception:                                    # noqa: BLE001
            rep.budget = math.inf

    if metric == "quanta" and math.isfinite(rep.budget):
        # A ROUND TRIP: an ion has to reach the cooler AND come back, so the budget buys
        # half the distance each way. Charging one leg would call a trap usable that is
        # only usable if it never returns.
        rep.stranded = tuple(sorted(
            s for s in sites
            if not math.isfinite(per_cap["cool"][s][0])
            or 2.0 * per_cap["cool"][s][0] > rep.budget))
        if rep.stranded:
            rep.notes.append(
                f"{len(rep.stranded)} of {len(sites)} traps cannot reach a cooling zone "
                f"and return inside R7's budget of {rep.budget:g} quanta; an ion parked "
                f"there is past the gate limit before it can get back, whatever "
                f"programme you write")
        elif all(per_cap["cool"][s][0] == 0.0 for s in sites):
            # EVERY SITE COOLS. Then the distance to a cooler is zero everywhere and
            # `stranded` is zero by construction, whatever the budget -- which is not the
            # same as the cooler placement being good, and an architect reading a bare 0
            # would take it for a verdict. Every device shipped in `arch/` is like this.
            rep.notes.append(
                f"every one of the {len(sites)} traps can cool in place, so nothing can "
                f"be stranded at any budget -- this 0 is the absence of a placement "
                f"problem, not evidence that the placement is good")
        else:
            rep.notes.append(
                f"every trap can reach a cooler and return for under {rep.budget:g} "
                f"quanta, so no position is structurally unusable")

    gate_sites = [s for s in sites if arch.can(s, "gate")]
    if len(gate_sites) >= 2:
        far = 0.0
        pair = None
        unreachable = 0
        for s, dist in distance_matrix(arch, model, metric=metric,
                                       sources=gate_sites).items():
            for t in gate_sites:
                if t == s:
                    continue
                d = dist.get(t, math.inf)
                if not math.isfinite(d):
                    unreachable += 1
                elif d > far:
                    far, pair = d, (s, t)
        rep.diameter = far
        rep.diameter_pair = pair
        rep.unreachable_pairs = unreachable
        if unreachable:
            rep.notes.append(
                f"{unreachable} ordered pairs of gate-capable traps cannot reach each "
                f"other at all -- the device is not connected for transport")
    else:
        rep.notes.append("fewer than two gate-capable traps; there is no pair to separate")
    return rep
