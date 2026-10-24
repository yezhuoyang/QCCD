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

The x2 on the broadcast terms is the two-way switch, not a differential pair: "every DC
is wired through a two-way switch" (p.19), so each electrode role has two banks of DACs,
one carrying the motion waveform and one the hold waveform, and a site's switch picks
which it follows.  That is WISE's N_set = 2, "stay or swap" (arXiv:2305.12773, Table I;
Sec. II A, "it suffices to use one DAC per waveform"): 32 broadcast DACs play ONE motion
plus a hold per cycle, selected per site by one bit.  The 1:100 demultiplexer is WISE's
rounded figure (its Fig. 2); its design is 1:128, with N_se/(M - 1) shim DACs because one
demux state disconnects (Sec. V B 3).  Shims are charged one at a time through it, so the
demux costs time as well as saving DACs: t_sc = M x t_ec, t_ec = 3 us.

**The scaling win is that only the compensation term grows with N.**  Linear and junction
control are broadcast, so their DAC count is constant in array size -- which is what makes
the whole WISE family worth the serialization penalty it costs (PLAN §1).

For an architecture that does not declare unit cells, the same accounting is applied to
the expanded graph: traps and junctions each carry their declared electrode count, and
the wiring scheme decides whether those electrodes share DACs or not.

What a programme asks of those DACs
-----------------------------------
The counts above are what the device HAS.  Pass `census=` (a `qccd.cost.waveforms`
`WaveformCensus` of a compiled programme) and the report also says what the programme
NEEDS of them: the banks it plays at once (motions plus the hold), the select bits a
switched broadcast plane must load per cycle and at what rate, and the time the shim
demultiplexer charges between transport and gates (`demux_tradeoff` for other M).
Without a census nothing changes, byte for byte -- the grader calls it without one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Mapping, Sequence

from ..arch import Architecture

if TYPE_CHECKING:  # pragma: no cover
    from .waveforms import WaveformCensus

__all__ = ["HardwareReport", "hardware_report", "deck_unit_cell_report", "WIRING_FLOORS",
           "demux_tradeoff", "DECK_ROLES", "SHIM_CHARGE_US"]

#: electrode roles one motion drives on the deck's array: 6 linear_h + 6 linear_v + 4 junction
DECK_ROLES = 6 + 6 + 4
#: t_ec, the time to charge one shim through the demultiplexer (WISE Sec. V B 3: 3 us)
SHIM_CHARGE_US = 3.0

#: What a device may not declare for itself (#15).  HOW it is wired -- direct, broadcast, a
#: channel map -- is a design choice and stays the device's.  How many electrodes a trap needs,
#: how many of them are compensation electrodes, how far a demultiplexer divides those, and
#: how many waveforms it takes to move an ion out of a cell are the deck's physics (p.19-20),
#: and they are the numbers every honest device in this repository declares.  Without a floor
#: a device could declare one electrode per trap, or one broadcast channel, and fit any budget:
#: the grader trusted `control.wiring` and `control` is a design variable, not a fixed block.
#: A device that declares less is counted at these; one that declares more, at its own.  The
#: electrodes of a junction follow from the trap's through `budget.junction_electrode_multiplier`,
#: which IS a fixed block.
WIRING_FLOORS: Mapping[str, int] = {
    "electrodes_per_trap": 24,              # 12 linear + 4 junction + 8 compensation
    # together, the least the compensation term may count: 8 shims a trap through the deck's
    # 1:100 demultiplexer (a floor on the COUNT; see `compensation_floor`)
    "compensation_electrodes_per_trap": 8,
    "max_shim_per_dac": 100,                # the deck's 1:100, WISE's rounded figure (design: 1:128)
    # (6 + 6 + 4) electrode roles x 2 banks: p.19's two-way switch gives every role a motion
    # DAC and a hold DAC (WISE N_set = 2) -- one motion plus a hold per cycle, not a
    # differential pair
    "channels_per_site": 32,
}


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
    # ---- only with a census (`hardware_report(..., census=...)`); None otherwise ----
    #: the census itself, as a dict
    waveforms: dict | None = None
    #: does one channel drive many sites (broadcast / row / column / explicit, or the
    #: wise / broadcast_groups scheme)?  Banks and select bits mean something only then.
    shared_channels: bool | None = None
    #: channels one bank needs: one per electrode role
    roles_per_bank: int | None = None
    #: banks played at once at the busiest cycle x roles: the hold bank counted
    dacs_dynamic_required: int | None = None
    select_bits_per_site: int | None = None
    select_bits_per_cycle: int | None = None
    #: select bits per cycle / the shortest transport cycle (bits per us = Mbit/s)
    select_rate_mbit_s: float | None = None
    demux_order: int | None = None
    shim_charge_us_per_layer: float | None = None
    shim_charge_total_us: float | None = None
    shim_charge_total_us_every_layer: float | None = None
    demux_tradeoff: list[dict] | None = None

    def ok(self) -> bool:
        return not self.over_budget

    def as_dict(self) -> dict:
        out = {
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
        if self.waveforms is not None:
            # only with a census, so a report made without one is the dict it always was
            out.update({
                "waveforms": dict(self.waveforms),
                "shared_channels": self.shared_channels,
                "roles_per_bank": self.roles_per_bank,
                "dacs_dynamic_required": self.dacs_dynamic_required,
                "select_bits_per_site": self.select_bits_per_site,
                "select_bits_per_cycle": self.select_bits_per_cycle,
                "select_rate_mbit_s": self.select_rate_mbit_s,
                "demux_order": self.demux_order,
                "shim_charge_us_per_layer": self.shim_charge_us_per_layer,
                "shim_charge_total_us": self.shim_charge_total_us,
                "shim_charge_total_us_every_layer": self.shim_charge_total_us_every_layer,
                "demux_tradeoff": [dict(r) for r in (self.demux_tradeoff or ())],
            })
        return out


def hardware_report(arch: Architecture, *, floors: Mapping[str, int] | None = None,
                    budget: Mapping | None = None,
                    census: "WaveformCensus | None" = None) -> HardwareReport:
    """Electrode, switch and DAC counts for any architecture, from its expanded graph.

    `floors` (the grader passes `WIRING_FLOORS`) counts every number the device declares
    about its own wiring at no less than the physics allows, and says so in `notes`.
    `budget` judges the device against a budget other than its own -- the task release's.
    `census` (`qccd.cost.waveforms.waveform_census` of a compiled programme) adds what
    that programme needs of the wiring: banks played at once, select bits, and the shim
    demultiplexer's charging time.  Without it the report is unchanged, byte for byte.
    """
    dev = arch.device
    wiring = dict(arch.control.get("wiring", {}) or {})
    scheme = str(wiring.get("scheme", "direct"))
    per_trap = int(wiring.get("electrodes_per_trap", 24))
    per_junction = int(wiring.get("electrodes_per_junction", 48))
    shim_per_dac = int(wiring.get("shim_per_dac", 1) or 1)
    compensation_per_cell = int(wiring.get("compensation_electrodes_per_trap", 8))
    budget = dict(arch.budget if budget is None else budget)

    notes: list[str] = []

    def floor(what: str, declared: int, least: int) -> int:
        if declared < least:
            notes.append(f"{what}: the device declares {declared}; counted at the physics' {least}")
            return least
        return declared

    plane = arch.control_plane
    broadcast_scheme = scheme in ("wise", "broadcast_groups")
    # each floor is applied on the path where the number it floors is what gets counted, so
    # a note never names a number that changed nothing
    if floors is not None and not plane.declared and not broadcast_scheme:
        per_trap = floor("electrodes per trap", per_trap, int(floors["electrodes_per_trap"]))
        per_junction = floor("electrodes per junction", per_junction,
                             int(budget.get("junction_electrode_multiplier", 2)) * per_trap)

    traps = [n for n in dev.nodes.values() if n.kind == "site"]

    def compensation_floor(declared: int) -> int:
        """The compensation DACs are floored as a COUNT, not factor by factor.  A device may
        honestly trade fewer shims for no demultiplexer (`h2_racetrack`: one shim a trap,
        driven directly, 40 DACs); flooring each factor would charge it for a combination
        neither it nor the deck declares (8 shims, no demux: 320).  What it may not do is
        come in under the deck's cheapest: 8 shims a trap through a 1:100 demultiplexer."""
        least = math.ceil(len(traps) * int(floors["compensation_electrodes_per_trap"])
                          / int(floors["max_shim_per_dac"]))
        if declared < least:
            notes.append(f"compensation: {declared} DAC(s) declared; counted at the physics' {least} "
                         f"({floors['compensation_electrodes_per_trap']} shims a trap, "
                         f"1:{floors['max_shim_per_dac']} demultiplexer)")
            return least
        return declared
    junction_ids = dev.junction_nodes
    deg_hist: dict[int, int] = {}
    for nid in dev.nodes:
        d = dev.degree(nid)
        deg_hist[d] = deg_hist.get(d, 0) + 1

    e_trap = len(traps) * per_trap
    e_junction = len(junction_ids) * per_junction
    electrodes = e_trap + e_junction
    switches = electrodes * 2  # every DC is wired through a two-way switch (deck p.19)

    if plane.declared:
        # count the channels the wiring actually has, rather than trusting a formula
        broadcast = plane.n_shared_channels
        comp_dacs = plane.n_compensation_channels
        electrodes = plane.n_electrodes
        switches = plane.n_switches
        notes.extend(plane.notes)
        if floors is not None:
            # The map is counted, but how many channels drive one site is still declared: a
            # map of ONE broadcast channel (roles ["linear"], one bank, no hold bank) passes
            # R4d whenever each cycle moves its ions alike.  Moving an ion takes every
            # electrode role of its cell, and holding one still takes the second bank, so a
            # site driven by fewer than `channels_per_site` channels is under-declared, and
            # the map is counted as if each of its channels were that many.
            least = int(floors["channels_per_site"])
            driven = [len(plane.channels_of(s)) for s in (n.id for n in traps)]
            driven = [k for k in driven if k > 0]
            if driven and min(driven) < least:
                scaled = math.ceil(broadcast * least / min(driven))
                notes.append(f"channel map: a site is driven by {min(driven)} channel(s); moving an "
                             f"ion takes {least}, so {broadcast} channel(s) count as {scaled}")
                broadcast = scaled
            comp_dacs = compensation_floor(comp_dacs)
        dacs = broadcast + comp_dacs
        notes.append(
            f"{broadcast} shared channel(s) + {comp_dacs} compensation = "
            f"{dacs}, counted from the channel map")
    elif broadcast_scheme:
        broadcast = int(wiring.get("dacs_dynamic", 0))
        if floors is not None:
            # no map, so nothing checks that this many waveforms can drive the device
            broadcast = floor("broadcast DACs (dacs_dynamic, no channel map)", broadcast,
                              int(floors["channels_per_site"]))
        compensation = len(traps) * compensation_per_cell
        comp_dacs = math.ceil(compensation / shim_per_dac) if shim_per_dac else compensation
        if floors is not None:
            comp_dacs = compensation_floor(comp_dacs)
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

    extra: dict = {}
    if census is not None:
        extra = _census_accounting(arch, wiring, broadcast_scheme, len(traps),
                                   compensation_per_cell, broadcast, census, notes)
        if extra["dacs_dynamic_required"] is not None \
                and extra["dacs_dynamic_required"] > broadcast:
            broadcast = extra["dacs_dynamic_required"]
            dacs = broadcast + comp_dacs

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
        **extra,
    )


def _census_accounting(arch: Architecture, wiring: Mapping, broadcast_scheme: bool,
                       n_traps: int, compensation_per_cell: int, counted: int,
                       census: "WaveformCensus", notes: list[str]) -> dict:
    """What a programme needs of the wiring, from its waveform census (module docstring,
    "What a programme asks of those DACs").  Appends its notes; returns the report fields.
    """
    plane = arch.control_plane
    shared = plane.grouping != "direct" if plane.declared else broadcast_scheme
    k = int(census.max_concurrent)
    out: dict = {"waveforms": census.as_dict(), "shared_channels": shared}

    # ---- banks and select bits: meaningful only where one channel drives many sites
    if shared:
        roles = plane.roles_per_site if plane.declared and plane.roles_per_site else DECK_ROLES
        if not (plane.declared and plane.roles_per_site):
            notes.append(f"waveforms: no channel map declares the electrode roles, so a bank "
                         f"is counted at the deck's {DECK_ROLES} (6 + 6 + 4)")
        required = k * roles
        out["roles_per_bank"] = roles
        out["dacs_dynamic_required"] = required
        motions = int(census.max_signatures)
        hold = " + a hold" if k > motions else ""
        if required > counted:
            notes.append(
                f"waveforms: the programme plays {k} bank(s) at once ({motions} motion(s)"
                f"{hold}), {k} x {roles} roles = {required} broadcast DACs; the wiring counts "
                f"{counted}, so they are counted at {required}")
        else:
            notes.append(
                f"waveforms: the programme plays at most {k} bank(s) at once ({motions} "
                f"motion(s){hold}), {k} x {roles} roles = {required} of the {counted} "
                f"broadcast DACs counted (the deck's x2 is a motion bank and a hold bank, "
                f"WISE N_set = 2)")
        switched = plane.switch_per_site
        bits = math.ceil(math.log2(k)) if switched and k >= 2 else 0
        out["select_bits_per_site"] = bits
        out["select_bits_per_cycle"] = bits * n_traps
        t_min = float(census.min_cycle_us)
        out["select_rate_mbit_s"] = (bits * n_traps / t_min) if t_min > 0 else 0.0
        if bits:
            notes.append(
                f"select: {bits} bit(s) per site per transport cycle choose its bank "
                f"(WISE Table I, N_sw = log2(N_set^N)); {bits * n_traps} bits a cycle over "
                f"{n_traps} sites, {out['select_rate_mbit_s']:.3g} Mbit/s at the shortest "
                f"cycle ({t_min:g} us)")
        elif not switched and k >= 2:
            notes.append(
                f"select: the plane has no per-site switch, so it cannot choose between the "
                f"{k} banks the programme plays at once (R4d fails such a cycle)")
    else:
        out.update(roles_per_bank=None, dacs_dynamic_required=None, select_bits_per_site=0,
                   select_bits_per_cycle=0, select_rate_mbit_s=0.0)
        notes.append("waveforms: every site has channels of its own, so no bank is shared "
                     "and no select bit is needed")

    # ---- the shim demultiplexer: DACs saved, time paid
    m = int(wiring.get("shim_per_dac", 1) or 1)
    t_ec = float(wiring.get("shim_charge_us", SHIM_CHARGE_US))
    t_sc = m * t_ec
    n_shims = (plane.n_sites * plane.compensation_per_site if plane.declared
               else n_traps * compensation_per_cell)
    out["demux_order"] = m
    out["shim_charge_us_per_layer"] = t_sc
    out["shim_charge_total_us"] = census.n_reconfigurations * t_sc
    out["shim_charge_total_us_every_layer"] = census.n_gate_layers * t_sc
    out["demux_tradeoff"] = demux_tradeoff(n_shims, census, t_ec=t_ec)
    notes.append(
        f"shims: {n_shims} compensation electrodes through a 1:{m} demultiplexer are charged "
        f"one at a time, t_sc = {m} x {t_ec:g} us = {t_sc:g} us (WISE Sec. V B 3).  Following "
        f"WISE they are re-set after each reconfiguration: {census.n_reconfigurations} x "
        f"{t_sc:g} = {census.n_reconfigurations * t_sc:g} us; WISER (arXiv:2609.13546 §4.2) "
        f"charges every gate layer: {census.n_gate_layers} x {t_sc:g} = "
        f"{census.n_gate_layers * t_sc:g} us; the replayed programme runs "
        f"{census.total_us:g} us")
    return out


def demux_tradeoff(n_shim_electrodes: int, census: "WaveformCensus | None" = None,
                   Ms: Sequence[int] = (1, 16, 100, 128),
                   t_ec: float = SHIM_CHARGE_US) -> list[dict]:
    """The shim demultiplexer's trade, per order M: fewer DACs, longer charging.

    WISE Sec. V B 3: "Increasing the multiplexing order M decreases the number of shim
    DACs but increases the shim charging time t_sc", t_sc = M x t_ec.  Two DAC counts,
    because the sources differ: the deck's ceil(n / M), and WISE's n / (M - 1) -- one of
    the M demux states disconnects -- rounded up (WISER §5.4 counts 10N/(M - 1)).  At
    M = 1 every shim has its own DAC and charging is one t_ec.  With a census, the
    charging time over the programme both ways: once per reconfiguration (WISE) and once
    per gate layer (WISER §4.2).
    """
    rows: list[dict] = []
    n = int(n_shim_electrodes)
    for m in Ms:
        m = int(m)
        t_sc = max(1, m) * t_ec
        row = {
            "M": m,
            "shim_dacs": math.ceil(n / m) if m > 1 else n,
            "shim_dacs_wise": math.ceil(n / (m - 1)) if m > 1 else n,
            "t_sc_us": t_sc,
        }
        if census is not None:
            row["charge_us_reconfigurations"] = census.n_reconfigurations * t_sc
            row["charge_us_every_layer"] = census.n_gate_layers * t_sc
        rows.append(row)
    return rows


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
