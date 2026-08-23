"""Hardware resource accounting -- the utilization report.  Deck p.19-20, PLAN §6.

This is the half of the objective that is not time or heating: how many electrodes,
switches, DACs and trapping zones a device costs, and whether it fits the budget.  For a
programmable design tool it is the report an FPGA toolchain would print after place and
route, and it is the one number that makes "O(1) DACs" a checkable claim rather than a
slogan.

The deck's own array (p.19-20)
------------------------------
Each of the `N = a·b` unit cells carries 24 DC electrodes in three control classes:

    12 linear        broadcast horizontally and vertically, separately -> 6h + 6v DACs
     4 junction      broadcast to every corner in the array          -> 4 DACs
     8 compensation  individually tuned, behind a 1:100 demux        -> 8N/100 DACs

    total electrodes  (12 + 4 + 8) x N = 24N
    total switches    24N x 2 (two-way)  = 48N
    total DACs        12x2 + 4x2 + 8N/100 = 24 + 8 + 8N/100
    trapping zones    N - b

**The scaling win is that only the compensation term grows with N.**  Linear and junction
control are broadcast, so their DAC count is constant in array size -- which is what makes
the whole WISE family worth the serialization penalty it costs (PLAN §1).

For an architecture that does not declare unit cells, the same accounting is applied to
the expanded graph: traps and junctions each carry their declared electrode count, and
the wiring scheme decides whether those electrodes share DACs or not.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping

from ..arch import Architecture

__all__ = ["HardwareReport", "hardware_report", "deck_unit_cell_report"]


@dataclass
class HardwareReport:
    name: str
    scheme: str = "direct"
    n_traps: int = 0
    n_junctions: int = 0
    n_segments: int = 0
    trapping_zones: int = 0
    total_capacity: int = 0
    degree_histogram: dict[int, int] = field(default_factory=dict)
    electrodes: int = 0
    electrodes_trap: int = 0
    electrodes_junction: int = 0
    switches: int = 0
    dacs: int = 0
    dacs_broadcast: int = 0
    dacs_compensation: int = 0
    dacs_per_trap: float = 0.0
    area_mm2: float = 0.0
    budget: Mapping = field(default_factory=dict)
    over_budget: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def ok(self) -> bool:
        return not self.over_budget

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "scheme": self.scheme,
            "n_traps": self.n_traps,
            "n_junctions": self.n_junctions,
            "n_segments": self.n_segments,
            "trapping_zones": self.trapping_zones,
            "total_capacity": self.total_capacity,
            "degree_histogram": dict(self.degree_histogram),
            "electrodes": self.electrodes,
            "switches": self.switches,
            "dacs": self.dacs,
            "dacs_broadcast": self.dacs_broadcast,
            "dacs_compensation": self.dacs_compensation,
            "dacs_per_trap": self.dacs_per_trap,
            "over_budget": list(self.over_budget),
            "notes": list(self.notes),
        }


def hardware_report(arch: Architecture) -> HardwareReport:
    """Electrode, switch and DAC counts for any architecture, from its expanded graph."""
    dev = arch.device
    wiring = dict(arch.control.get("wiring", {}) or {})
    scheme = str(wiring.get("scheme", "direct"))
    per_trap = int(wiring.get("electrodes_per_trap", 24))
    per_junction = int(wiring.get("electrodes_per_junction", 48))
    shim_per_dac = int(wiring.get("shim_per_dac", 1) or 1)
    compensation_per_cell = int(wiring.get("compensation_electrodes_per_trap", 8))

    plane = arch.control_plane
    traps = [n for n in dev.nodes.values() if n.kind == "site"]
    junction_ids = dev.junction_nodes
    deg_hist: dict[int, int] = {}
    for nid in dev.nodes:
        d = dev.degree(nid)
        deg_hist[d] = deg_hist.get(d, 0) + 1

    e_trap = len(traps) * per_trap
    e_junction = len(junction_ids) * per_junction
    electrodes = e_trap + e_junction
    switches = electrodes * 2  # every DC is wired through a two-way switch (deck p.19)

    notes: list[str] = []
    if plane.declared:
        # count the channels the wiring actually has, rather than trusting a formula
        broadcast = plane.n_shared_channels
        comp_dacs = plane.n_compensation_channels
        dacs = plane.n_channels
        electrodes = plane.n_electrodes
        switches = plane.n_switches
        notes.extend(plane.notes)
        notes.append(
            f"{plane.n_shared_channels} shared channel(s) + {comp_dacs} compensation = "
            f"{dacs}, counted from the channel map")
    elif scheme in ("wise", "broadcast_groups"):
        broadcast = int(wiring.get("dacs_dynamic", 0))
        compensation = len(traps) * compensation_per_cell
        comp_dacs = math.ceil(compensation / shim_per_dac) if shim_per_dac else compensation
        dacs = broadcast + comp_dacs
        notes.append(
            f"broadcast wiring: {broadcast} DACs are constant in array size; only the "
            f"{compensation} compensation electrodes scale, and a 1:{shim_per_dac} demux "
            f"divides them to {comp_dacs}"
        )
        notes.append(
            "counted from aggregate wiring fields; declare `control.channels` for a "
            "channel map that is counted structurally and checked for drivability")
    else:
        broadcast = 0
        comp_dacs = electrodes
        dacs = electrodes
        notes.append("direct wiring: one DAC per electrode, so DAC count is O(traps)")

    budget = dict(arch.budget)
    over: list[str] = []
    if "max_dacs" in budget and dacs > int(budget["max_dacs"]):
        over.append(f"dacs {dacs} > max_dacs {budget['max_dacs']}")
    if "max_junctions" in budget and len(junction_ids) > int(budget["max_junctions"]):
        over.append(
            f"junctions {len(junction_ids)} > max_junctions {budget['max_junctions']}"
        )

    return HardwareReport(
        name=arch.name,
        scheme=scheme,
        n_traps=len(traps),
        n_junctions=len(junction_ids),
        n_segments=len(dev.segments),
        trapping_zones=len(traps),
        total_capacity=dev.total_capacity(),
        degree_histogram=dict(sorted(deg_hist.items())),
        electrodes=electrodes,
        electrodes_trap=e_trap,
        electrodes_junction=e_junction,
        switches=switches,
        dacs=dacs,
        dacs_broadcast=broadcast,
        dacs_compensation=comp_dacs,
        dacs_per_trap=dacs / len(traps) if traps else 0.0,
        budget=budget,
        over_budget=over,
        notes=notes,
    )


def deck_unit_cell_report(a: int, b: int, *, shim_per_dac: int = 100) -> dict:
    """The deck's own array formulas, p.19-20, verbatim.

    Kept separate from `hardware_report` because it is a *closed form in (a, b)* that the
    graph-based accounting has to be checked against, not derived from -- M0's acceptance
    criterion is that our counts match this.
    """
    n = a * b
    return {
        "unit_cells": n,
        "a": a,
        "b": b,
        "electrodes_per_cell": 24,
        "linear_per_cell": 12,
        "junction_per_cell": 4,
        "compensation_per_cell": 8,
        "total_electrodes": 24 * n,
        "total_switches": 48 * n,
        "dacs_linear": 12 * 2,
        "dacs_junction": 4 * 2,
        "dacs_compensation": math.ceil(8 * n / shim_per_dac),
        "total_dacs": 12 * 2 + 4 * 2 + math.ceil(8 * n / shim_per_dac),
        "trapping_zones": n - b,
        "source": "ion_transport_deck_v3.pptx.pdf p.19-20",
    }
