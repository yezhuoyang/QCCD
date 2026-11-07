"""WISE: a grid of zones sorted by parallel switched swaps, and gates run in layers.

Malinowski, Allcock and Ballance, arXiv:2305.12773, as the team's transport deck ("QCCD Transport
Architectures", part 2) writes it for QCCD Studio: sweep h, Cx and Cy, keep the rest at the
paper's defaults.

**The device.**  A unit cell is one junction zone and h gate zones along the row; the chip is Cx x
Cy cells, and junction zones join the rows directly (deck, Hardware model).  So a row is a line of
zones and every (h+1)-th zone of it is an X-junction.  Each gate zone is a site.  The deck says the
idealised grid's "one qubit per junction" breaks R23, and that the realistic layout fits if
junction zones are modelled as sites (S8).  This model takes the other way out of the same
conflict, which needs no rule dropped: a junction zone is a real junction, every ion crosses it
inside a step (R2, R23), and no ion rests there.  A device therefore holds at most `h` qubits per
cell where the deck's full occupancy is h + 1; its hardware census is the deck's all the same.

**A swap is the deck's S5 and S6.**  It takes two steps of the replay, a merge then a split: one
ion moves into its partner's zone, and the partner leaves for the zone just vacated.  That passes
every R-rule as written, and each half is one direction for the whole chip, so S4 and S5 are what
a step's class says.  Time (deck T1) is on the rails: half a swap step per rail, so a swap between
neighbours is t0, across a junction zone 2 t0 (a junction zone is a zone), and between rows, by
the route S6 calls three shuttling steps through the junction, 3 t0.  A move into an empty zone is
half a swap, having no merge, rotation or split.  Heat comes from the noise model, since the paper
gives none: 0.1 quanta a shuttle, and 2 for a merge and split together, which the replay deposits
on each of the two ions as it moves.

**A layer is its shim charge and its pulse** (deck T4, T6), so each gate's price includes the
384 us of shim charging.  A two-qubit gate's merge and split are two halves of a swap.
"""

from __future__ import annotations

import copy
import math
from collections import deque
from dataclasses import dataclass
from typing import Mapping

__all__ = ["SWAP_US", "SHIM_US", "DYNAMIC_DACS", "device_doc", "primitives", "census", "is_wise", "ctl_classes",
           "gate_zone", "junction_zone", "gate_zones", "sizes_for", "Edge", "Zones", "NotWise"]

SWAP_US = 100.0              # deck T1, t0
SHIM_US = 3.0 * 128          # deck T4: 128 shims x 3 us each, before every layer
PULSE_1Q_US = 1.0            # deck T5; the noise model has no one-qubit duration but its example clock
DYNAMIC_DACS = 120           # deck HW2
RESIDUAL_1Q = 1e-5           # deck T9: what a layer of pulses costs a zone sitting it out, the bound as the value
_DECK = "QCCD Transport Architectures deck, WISE"


def gate_zone(r: int, c: int) -> str:
    return f"G{r}_{c}"


def junction_zone(r: int, c: int) -> str:
    return f"X{r}_{c}"


def gate_zones(h: int, cx: int, cy: int) -> int:
    """How many qubits the model holds: one per gate zone while sorting."""
    return h * cx * cy


def ctl_classes() -> list[dict]:
    """A step's kinds, each one direction across the whole chip (S4, S5): the merge half `a` and
    the split half `b` of a swap within a cell at parity 0 or 1 (`h0`, `h1`), across a junction
    zone (`x`), and between rows on either side of the junction at parity 0 or 1 (`vr0` ...)."""
    out = []
    for kind in ("h0", "h1", "x", "vr0", "vr1", "vl0", "vl1"):
        for half, what in (("a", "the merge half"), ("b", "the split half")):
            out.append({"id": f"wise_{kind}_{half}", "type": "shift", "orbit": "zones",
                        "note": f"{what} of a {kind} swap: one waveform for every active zone"})
    return out


def device_doc(h: int, cx: int, cy: int, *, name: str | None = None) -> dict:
    """The chip as a device document, before any physics: `explicit` geometry, since the layout's
    rows join only through their junction zones and no generator draws that."""
    from ..arch.schema import SCHEMA_VERSION
    if h < 2 or cx < 1 or cy < 1:
        raise ValueError(f"WISE needs h >= 2 and a positive size, not h={h}, {cx} x {cy}")
    k = h + 1
    m = cx * k
    nodes: list[dict] = []
    segs: list[dict] = []
    junctions: set[tuple[int, int]] = set()
    for r in range(cy):
        for c in range(m):
            if c % k == 0:
                junctions.add((r, c))
                nodes.append({"id": junction_zone(r, c), "pos": [float(c), float(r)], "kind": "junction",
                              "labels": ["junction_zone"]})
            else:
                nodes.append({"id": gate_zone(r, c), "pos": [float(c), float(r)], "kind": "site", "capacity": 2,
                              "zone_type": "gate_zone", "labels": ["gate_zone"]})

    def nid(r: int, c: int) -> str:
        return junction_zone(r, c) if (r, c) in junctions else gate_zone(r, c)

    for r in range(cy):
        for c in range(m - 1):
            segs.append({"id": f"H{r}_{c}", "ends": [nid(r, c), nid(r, c + 1)], "length": 1.0, "capacity": 1,
                         "labels": ["row"]})
    for r in range(cy - 1):
        for c in range(0, m, k):
            segs.append({"id": f"V{r}_{c}", "ends": [nid(r, c), nid(r + 1, c)], "length": 1.0, "capacity": 1,
                         "labels": ["column"]})
    return {
        "name": name or f"wise_h{h}_{cx}x{cy}",
        "schema_version": SCHEMA_VERSION,
        "description": (f"WISE: {cx} x {cy} cells of one junction zone and {h} gate zones, {h * cx * cy} gate zones "
                        "in all, sorted by parallel switched swaps"),
        "geometry": {"generator": "explicit",
                     "params": {"family": "wise", "h": h, "cx": cx, "cy": cy},
                     "nodes": nodes, "segments": segs, "loops": []},
        "zone_types": {"gate_zone": {"capacity": 2, "gate": True, "spam": True, "cool": True,
                                     "note": "one qubit while sorting, two for a gate (S1); a pair of gate beams "
                                             "(HW8)"}},
        "control": {
            "model": "simd_classes",
            "classes": {"generator": "wise_parity", "count": len(ctl_classes()), "extra": ctl_classes(),
                        "note": "one operation kind per step (S4): each class is one direction for the whole chip"},
            "max_simd_classes_per_cycle": 1,
            "wiring": {"scheme": "wise", "dacs_dynamic": DYNAMIC_DACS, "shim_per_dac": 127,
                       "electrodes_per_trap": 10, "electrodes_per_junction": 20,
                       "compensation_electrodes_per_trap": 10,
                       "note": f"{_DECK} HW1, HW2, HW5: 2 states x 2 parities x (10 + 20) dynamic DACs, shims through "
                               "1:128 demultiplexers"},
            "channels": {"grouping": "broadcast", "roles": {"linear_h": 6, "linear_v": 6, "junction": 4},
                         "differential": 2, "switch_per_site": True},
        },
    }


def primitives(base: Mapping) -> dict:
    """The deck's prices on `base` (qccd-physics@3's primitives).  Where the noise model has a
    number it wins: 70 us for the native gate rather than the deck's 100."""
    out = copy.deepcopy(dict(base))
    table = out["split"]["curve"][0]["table"]
    def priced(key: str, pulse: float, why: str) -> None:
        out[key] = {**out[key], "us": pulse + SHIM_US,
                    "note": f"{out[key].get('note', '')}. On WISE a layer is its shim charge plus its pulse: {why}".strip(". "),
                    "source": f"{out[key].get('source', '')}; {_DECK} T4, T6"}
    priced("1q_gate", PULSE_1Q_US, "384 + 1 us (deck T5, T6)")
    out["1q_gate"]["residual_inactive"] = RESIDUAL_1Q
    priced("ms_gate", float(base["ms_gate"]["us"]), "384 + 70 us; the noise model's gate rather than the deck's 100")
    priced("measure", float(base["measure"]["us"]), "384 + 150 us")
    priced("reset", float(base["reset"]["us"]), "384 + 20 us")
    out["shuttle_segment"] = {"curve": [{"us": SWAP_US / 2, "quanta": 0.1, "table": table,
                                         "label": "half a swap step per rail (t0 = 100 us, swap and stay alike); heat "
                                                  "from the noise model, as the paper gives none",
                                         "source": f"{_DECK} T1; Helios Nominal Noise Model Sec. 5"}]}
    grouped = {"us": 0.0, "quanta": 2.0, "table": table, "source": f"{_DECK} T1; Helios Nominal Noise Model Sec. 5",
               "label": "inside the swap step's time. A merge and split together heat each ion of the pair by 2 "
                        "quanta; the replay deposits it on the ion that moves in each half"}
    out["merge"] = {"curve": [dict(grouped)]}
    out["split"] = {"curve": [dict(grouped)]}
    out["junction_cross"] = {"curve_by_degree": {
        d: [{"us": 0.0, "quanta": 0.014, "table": table, "label": "inside the swap step's time; heat is the "
             "noise model's proxy per crossing", "source": f"{_DECK} T1, S6; Helios Nominal Noise Model Sec. 4"}]
        for d in ("3", "4")}}
    return out


class NotWise(ValueError):
    """The device is not a WISE chip, so a swap step has no pairs on it."""


@dataclass(frozen=True)
class Edge:
    """A pair of gate zones a swap step can exchange: `u` <- `v` is its merge half, and
    `in_` and `out` are the rails each half takes, where it is more than the one between them."""

    kind: str
    u: str
    v: str
    in_: tuple[str, ...] = ()
    out: tuple[str, ...] = ()


class Zones:
    """The gate zones as a graph whose edges are the swap steps' pairs."""

    #: a swap's length in t0: a neighbour, across a junction zone, and between rows (S6's three
    #: shuttling steps through the junction)
    COST = {"h": 1, "x": 2, "v": 3}

    def __init__(self, arch):
        p = (arch.device.params if hasattr(arch.device, "params") else {}) or {}
        if p.get("family") != "wise":
            raise NotWise(f"{arch.name} is not a WISE chip: its geometry does not say family wise")
        self.h, self.cx, self.cy = int(p["h"]), int(p["cx"]), int(p["cy"])
        k = self.h + 1
        m = self.cx * k
        self.sites = [gate_zone(r, c) for r in range(self.cy) for c in range(m) if c % k]
        self.edges: dict[str, list[Edge]] = {}

        def add(e: Edge) -> None:
            self.edges.setdefault(e.kind, []).append(e)

        for r in range(self.cy):
            for c in range(m):
                if c % k == 0:      # a junction zone
                    if c and c + 1 < m:
                        add(Edge("x", gate_zone(r, c - 1), gate_zone(r, c + 1),
                                 (f"H{r}_{c}", f"H{r}_{c - 1}"), (f"H{r}_{c - 1}", f"H{r}_{c}")))
                    continue
                if c + 1 < m and (c + 1) % k:
                    add(Edge(f"h{c % 2}", gate_zone(r, c), gate_zone(r, c + 1)))
        for r in range(self.cy - 1):
            for c in range(0, m, k):
                # the row below's rail to its junction zone, the column, this row's rail away
                rails = (f"H{r + 1}_{c}", f"V{r}_{c}", f"H{r}_{c}")
                add(Edge(f"vr{r % 2}", gate_zone(r, c + 1), gate_zone(r + 1, c + 1), rails, rails[::-1]))
                if c:
                    rails = (f"H{r + 1}_{c - 1}", f"V{r}_{c}", f"H{r}_{c - 1}")
                    add(Edge(f"vl{r % 2}", gate_zone(r, c - 1), gate_zone(r + 1, c - 1), rails, rails[::-1]))
        self.slots = [e for kind, es in self.edges.items() if kind.startswith("h") for e in es]
        self.nbrs: dict[str, list[str]] = {s: [] for s in self.sites}
        for es in self.edges.values():
            for e in es:
                self.nbrs[e.u].append(e.v)
                self.nbrs[e.v].append(e.u)
        for v in self.nbrs.values():
            v.sort()
        self.dist = {s: self._bfs(s) for s in self.sites}
        if any(len(d) != len(self.sites) for d in self.dist.values()):
            raise NotWise(f"{arch.name}'s gate zones are not all connected")

    def _bfs(self, src: str) -> dict[str, int]:
        out = {src: 0}
        todo = deque([src])
        while todo:
            x = todo.popleft()
            for y in self.nbrs[x]:
                if y not in out:
                    out[y] = out[x] + 1
                    todo.append(y)
        return out

    def cost(self, kind: str) -> int:
        return self.COST[kind[0]]


def is_wise(doc: Mapping) -> bool:
    return ((doc.get("geometry") or {}).get("params") or {}).get("family") == "wise"


def census(h: int, cx: int, cy: int) -> dict:
    """The deck's hardware table for one chip (WISE, Examples; its paper column is Table I of
    arXiv:2305.12773).  `qubits` is the deck's full occupancy, one per zone."""
    n_c = cx * cy
    k = h + 1
    n = k * n_c
    n_gz = h * n_c
    m, rows = cx * k, cy
    n_de = n_c * (10 * h + 20)
    n_se = 10 * n
    shim_dacs = math.ceil(n_se / 127)
    n_tg = 2 * n_de + n_se
    steps = 2 * m + k * rows
    return {"cells": n_c, "zones_per_cell": k, "zones": n, "qubits": n, "gate_zones": n_gz, "grid_m": m, "grid_n": rows,
            "dynamic_electrodes": n_de, "shim_electrodes": n_se, "dynamic_dacs": 2 * 2 * (10 + 20),
            "shim_dacs": shim_dacs, "transmission_gates": n_tg, "area_active_mm2": n * 0.16,
            "area_switch_mm2": n_tg * 0.0025, "area_capacitor_mm2": n_se * 0.01,
            "off_chip_connections": 2 * 2 * (10 + 20) + shim_dacs + 10, "switch_word_us": n / 50.0,
            "reconfiguration_steps": steps, "reconfiguration_ms": steps * SWAP_US / 1000.0,
            "laser_pairs": n_gz}


def sizes_for(ions: int) -> list[tuple[int, int, int]]:
    """The (h, Cx, Cy) a code of `ions` is swept at: for each h of the deck's examples, the
    near-square chips whose gate zones hold 1.1 to 3 times the ions, as the other families'
    sizes are."""
    out = []
    for h in (3, 5, 7):
        for cy in range(1, 40):
            for cx in (cy, cy + 1):
                z = h * cx * cy
                # near-square in zones: a row of (h + 1) cx zones against cy rows
                if 1.1 * ions <= z <= 3 * ions and (h + 1) * cx >= cy:
                    out.append((h, cx, cy))
    # at most four per h, evenly through the range, so a family is not one size repeated
    keep = []
    for h in (3, 5, 7):
        got = sorted({s for s in out if s[0] == h}, key=lambda s: s[1] * s[2])
        if len(got) > 4:
            got = [got[round(i * (len(got) - 1) / 3)] for i in range(4)]
        keep += got
    return keep
