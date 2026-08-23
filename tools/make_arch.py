#!/usr/bin/env python
"""Emit the reference `.arch.json` files in `arch/`.

The files are committed -- they are the reference architectures, and downstream tests
load them from disk -- but they are *generated* so that the primitive tables, which are
identical across architectures and carry a source citation on every point, cannot drift
apart by hand-editing.  Edit this script, re-run it, review the diff.

    python tools/make_arch.py

Every number below traces to `Knowledge/`:
`python Knowledge/kg/query.py param t_split`, `... param t_junction_cross`, and so on.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.arch import SCHEMA_VERSION, Architecture, load  # noqa: E402

OUT = ROOT / "arch"


# --------------------------------------------------------------------------- shared

ZONE_TYPES = {
    # PLAN §3, deck p.24.  `capacity` is the R1 bound; the capability flags are R6.
    "data": {"capacity": 2, "gate": False, "spam": False, "cool": True},
    "ancilla": {"capacity": 2, "gate": True, "spam": True, "cool": True},
    "trap": {
        "capacity": 2,
        "gate": True,
        "spam": True,
        "cool": True,
        "note": "general-purpose trap: gate, measure and cool all available",
    },
    "tfactory": {"capacity": 4, "gate": True, "spam": True, "cool": True},
    "load": {
        "capacity": 8,
        "gate": False,
        "spam": True,
        "cool": True,
        "photoionization": True,
    },
}

JONES = "2510.23519"
EXCITE = "2605.25118"
CYCLONE = "2511.15910"
H2 = "2305.03828"
JUNCTION_MEASURED = "1210.3655"

PRIMITIVES = {
    # ---- transport ------------------------------------------------------------
    "shuttle_segment": {
        "curve": [
            {
                "us": 5,
                "quanta": 0.10,
                "table": "qccdsim_jones",
                "source": JONES,
                "label": "t7 ion shuttling, one segment",
            },
            {
                "us": 10,
                "quanta": 0.10,
                "table": "cyclone",
                "source": CYCLONE,
                "label": "move, one shuttling zone; quanta borrowed from Jones",
            },
            {
                "us": 12,
                "quanta": 1.0,
                "table": "transport_excitation",
                "source": EXCITE,
                "label": "single-ion transport, fast operating point",
            },
            {
                "us": 14,
                "quanta": 0.10,
                "table": "transport_excitation",
                "source": EXCITE,
                "label": "single-ion transport, slow operating point",
            },
        ]
    },
    "junction_cross": {
        # R18: charged by node degree read off the expanded graph.  Degree 2 is listed
        # only because Cyclone lists it; PLAN §0.5 rejects it (a bend has a continuous
        # RF null, so it is one ordinary shuttle) and the corrected cost model ignores
        # this entry entirely.
        "curve_by_degree": {
            "2": [
                {
                    "us": 10,
                    "quanta": 0.5,
                    "table": "cyclone",
                    "source": CYCLONE,
                    "label": "L-junction; superseded by R18, a bend is not a junction",
                }
            ],
            "3": [
                {
                    "us": 100,
                    "quanta": 3.0,
                    "table": "qccdsim_jones",
                    "source": JONES,
                    "label": "t10-t11 junction entry/exit",
                },
                {
                    "us": 100,
                    "quanta": 3.0,
                    "table": "cyclone",
                    "source": CYCLONE,
                    "label": "degree-3 junction crossing; quanta borrowed from Jones",
                },
                {
                    "us": 200,
                    "quanta": 3.0,
                    "table": "measured",
                    "source": JUNCTION_MEASURED,
                    "label": "~200 us one-way measured: 85 round trips in ~34 ms",
                },
            ],
            "4": [
                {
                    "us": 120,
                    "quanta": 3.0,
                    "table": "cyclone",
                    "source": CYCLONE,
                    "label": "X-junction crossing; quanta borrowed from Jones",
                },
                {
                    "us": 120,
                    "quanta": 3.0,
                    "table": "qccdsim_jones",
                    "source": CYCLONE,
                    "label": "Jones gives no degree-4 figure; Cyclone's is carried over",
                },
            ],
        }
    },
    "split": {
        "curve": [
            {
                "us": 80,
                "quanta": 6.0,
                "table": "qccdsim_jones",
                "source": JONES,
                "label": "t8-t9 split and merge -- 60x a shuttle in heating",
            },
            {
                "us": 80,
                "quanta": 6.0,
                "table": "cyclone",
                "source": CYCLONE,
                "label": "split; quanta borrowed from Jones (Cyclone charges none)",
            },
            {
                "us": 30,
                "quanta": 1.0,
                "table": "transport_excitation",
                "source": EXCITE,
                "label": "fast split, n~1",
            },
            {
                "us": 40,
                "quanta": 0.1,
                "table": "transport_excitation",
                "source": EXCITE,
                "label": "slow split, n~0.1",
            },
        ]
    },
    "merge": {
        "curve": [
            {"us": 80, "quanta": 6.0, "table": "qccdsim_jones", "source": JONES},
            {"us": 80, "quanta": 6.0, "table": "cyclone", "source": CYCLONE},
            {"us": 30, "quanta": 1.0, "table": "transport_excitation", "source": EXCITE},
            {"us": 40, "quanta": 0.1, "table": "transport_excitation", "source": EXCITE},
        ]
    },
    "ion_swap": {
        "curve": [
            {"us": 18, "quanta": 1.0, "table": "transport_excitation", "source": EXCITE},
            {"us": 20, "quanta": 0.1, "table": "transport_excitation", "source": EXCITE},
        ]
    },
    # ---- gates and the rest ---------------------------------------------------
    "gate_swap": {
        "gates": 3,
        "note": "3 CX, distance-independent; R14's hidden cost of splitting off-edge",
        "source": JONES,
    },
    "ms_gate": {
        "us": 25,
        "fidelity_at_n0": 0.99816,
        "error_vs_quanta": "linear:2.0e-3",
        "max_quanta": 1.0,
        "source": H2,
        "note": (
            "1.84e-3 two-qubit infidelity measured on H2 (2305.03828). max_quanta is "
            "R7's gate budget; PLAN §0.4 puts it at 1-2 quanta, 1.0 is the strict end"
        ),
    },
    "1q_gate": {"us": 5, "fidelity": 0.999975, "source": H2},
    "measure": {"us": 120, "fidelity": 0.9984, "source": H2},
    "reset": {"us": 50, "error": 5e-3, "source": JONES},
    "cool": {
        "us": 300,
        "removes_quanta": "all",
        "broadcastable": True,
        "scope": "global",
        "source": "2606.06455",
        "note": (
            "Doppler sheet beams cover the whole trap, so one cooling operation cools "
            "every ion: it costs schedule time but does not serialize per ion (R7c)"
        ),
    },
}

HEATING = {
    "anomalous_rate_quanta_per_ms": 0.05,
    "note": (
        "R17: ndot = S_E(w) e^2 / 4 m hbar w, accruing whether or not the ion moves. "
        "Device-specific and unmeasured for the target trap -- q_heating_rate_measurement"
    ),
    "source": EXCITE,
}

SPECIES = {
    "qubit": "Ba+",
    "coolant": "Sr+",
    "sympathetic": True,
    "coolant_fraction": 0.5,
    "T_coh_s": 600,
    "source": JONES,
}

BUDGET = {
    "max_dacs": 128,
    "max_junctions": 200,
    "max_area_mm2": 100,
    "junction_electrode_multiplier": 2,
}


#: Declared by every architecture: a plain directional shuttle between adjacent traps.
#: R4 requires the class a cycle names to exist, so the generic transport primitive has
#: to be part of the instruction-set, not something a program invents.
BASIC_CLASSES = [
    {
        "id": "shuttle",
        "type": "shift",
        "orbit": "any",
        "note": "one ion, one trap to the next; the lowest common denominator move",
    }
]


def wise_control(extra_classes: list[dict] | None = None) -> dict:
    return {
        "model": "simd_classes",
        "classes": {
            "generator": "x_junction_grid",
            "count": 18,
            "note": "12 directional shifts + 6 directional swaps (JT-SIMD, 2504.17886)",
            "extra": (extra_classes or []) + BASIC_CLASSES,
        },
        "max_simd_classes_per_cycle": 1,
        "intra_inter_exclusive": True,
        "wiring": {
            "scheme": "wise",
            "dacs_dynamic": 100,
            "shim_per_dac": 100,
            "electrodes_per_trap": 24,
            "electrodes_per_junction": 48,
            # only these scale with array size; everything else is broadcast
            "compensation_electrodes_per_trap": 8,
        },
        "optical": {"addressing": "global_beam", "per_zone_switch": True},
        # The channel map: which electrodes share one analog waveform.  This is the
        # level PLAN 2's scope boundary sits at -- the wiring is modelled, the voltages
        # on it are not.  6 horizontal + 6 vertical linear channels broadcast to every
        # cell, plus 4 junction channels, exactly as deck p.19-20 describes; the
        # per-site two-way switch is what lets a zone opt out without a new channel.
        "channels": {
            "grouping": "broadcast",
            "roles": {"linear_h": 6, "linear_v": 6, "junction": 4},
            "differential": 2,
            "switch_per_site": True,
        },
    }


def base(name: str, description: str, provenance: dict) -> dict:
    return {
        "name": name,
        "schema_version": SCHEMA_VERSION,
        "description": description,
        "provenance": provenance,
        "zone_types": ZONE_TYPES,
        "primitives": PRIMITIVES,
        "heating": HEATING,
        "species": SPECIES,
        "budget": BUDGET,
    }

SORT_CLASSES = [
    # the two classes an odd-even / bubble-sort reconfiguration needs on a packed loop.
    # A transposition merges one ion leftward and splits the other rightward: R4 fixes a
    # class's global DIRECTION, so these cannot share a cycle under WISE -- which is the
    # mechanism H2's compiler runs into, bubble-sorting "in both directions around the
    # device" (2305.03828).
    {
        "id": "sort_merge",
        "type": "shift",
        "orbit": "L0",
        "delta": -1,
        "entails": ["merge"],
        "note": "one ion of the pair joins the neighbouring chain",
    },
    {
        "id": "sort_split",
        "type": "shift",
        "orbit": "L0",
        "delta": 1,
        "entails": ["split"],
        "note": "the other splits back out; which one realizes the transposition",
    },
]


# --------------------------------------------------------------------------- files


def ring144_24v() -> dict:
    doc = base(
        "ring144_24v",
        "The shipped 24-ancilla design: a 2x72 rectangular rotation loop of 144 slots "
        "with 24 evenly spaced dock spurs to mid-line ancilla sites. The spurs are what "
        "make 24 of the 144 rail nodes degree 3, putting a junction on every rigid hop.",
        {
            "artifact": "visualizer_24_ancillas_24_junctions_standalone.html",
            "geometry_label": "W72_H2",
            "deck": "ion_transport_deck_v3.pptx.pdf",
            "knowledge": ["fd_verticals_are_the_real_junction_cost", "ex_audit_inline_data"],
        },
    )
    doc["geometry"] = {
        "generator": "ring",
        "params": {"width": 72, "height": 2, "verticals": 24},
    }
    doc["control"] = wise_control(
        [
            {
                "id": "rotate_cw",
                "type": "shift",
                "orbit": "L0",
                "delta": 1,
                "note": "the single movement template rigid rotation needs (PLAN §1)",
            },
            {"id": "rotate_ccw", "type": "shift", "orbit": "L0", "delta": -1},
            {
                "id": "dock",
                "type": "shift",
                "orbit": "spurs",
                "direction": "inward",
                "entails": ["split", "merge"],
                "note": (
                    "lifts one ion out of the rail's potential and inserts it into the "
                    "ancilla trap: split at the source, merge at the destination. A "
                    "conveyor-belt rotation entails neither -- the whole chain moves "
                    "with the potential (2305.03828)"
                ),
            },
            {
                "id": "undock",
                "type": "shift",
                "orbit": "spurs",
                "direction": "outward",
                "entails": ["split", "merge"],
            },
        ]
        + SORT_CLASSES
    )
    return doc




def cyclone_base() -> dict:
    doc = base(
        "cyclone_base",
        "Base Cyclone for BB [[144,12,12]]: m/2 = 72 traps on one rotation loop, "
        "ancillas in-line rather than on spurs, so the loop's only non-straight nodes "
        "are degree-2 bends and no junction sits on the rotation path (PLAN §0.5).",
        {
            "paper": CYCLONE,
            "config": "n=144, m=144, x=m/2=72 traps; 144 data + 72 ancilla ions",
            "knowledge": ["fd_cyclone_bb144_prediction", "fd_ancilla_count_vs_reference"],
            "note": (
                "capacity 4 = 2 data + 1 ancilla + 1 slot of rebalance headroom; "
                "Cyclone's own §4.1 puts 0..2n/m gates per trap per step"
            ),
        },
    )
    doc["geometry"] = {
        "generator": "ring",
        "params": {"width": 36, "height": 2, "verticals": 0, "site_zone": "trap"},
    }
    doc["zone_types"] = dict(ZONE_TYPES)
    doc["zone_types"]["trap"] = dict(ZONE_TYPES["trap"])
    doc["zone_types"]["trap"]["capacity"] = 4
    doc["control"] = wise_control(
        [
            {"id": "rotate_cw", "type": "shift", "orbit": "L0", "delta": 1},
            {"id": "rotate_ccw", "type": "shift", "orbit": "L0", "delta": -1},
        ]
        + SORT_CLASSES
    )
    doc["control"]["wiring"]["scheme"] = "broadcast_groups"
    doc["control"]["wiring"]["dacs_dynamic"] = 3
    doc["control"]["wiring"]["note"] = "O(1) DACs: Cyclone's headline wiring claim"
    return doc


def chain_arch() -> dict:
    doc = base(
        "chain72",
        "A 72-site linear register, capacity 2: the unrolled counterpart of "
        "ring144_24v with the same 144 ion slots and no loop, no spur and no junction. "
        "The control against which the ring topology's cost is measured.",
        {
            "baseline": "PLAN §7.1 baseline 1 (stationary chain, 2606.06455) unrolled",
            "note": (
                "chain(1) with a large capacity is the true stationary-chain baseline: "
                "one trap, steerable Raman beams, OMG in-place measurement, no transport"
            ),
        },
    )
    doc["geometry"] = {
        "generator": "chain",
        "params": {"n": 72, "site_zone": "trap"},
    }
    doc["control"] = {
        "model": "simd_classes",
        "classes": {
            "generator": "x_junction_grid",
            "count": 18,
            "extra": [
                {"id": "shift_right", "type": "shift", "orbit": "P0", "delta": 1},
                {"id": "shift_left", "type": "shift", "orbit": "P0", "delta": -1},
            ]
            + BASIC_CLASSES,
        },
        "max_simd_classes_per_cycle": 1,
        "intra_inter_exclusive": True,
        "wiring": {"scheme": "direct", "dacs_dynamic": 72, "electrodes_per_trap": 24},
        "optical": {"addressing": "global_beam", "per_zone_switch": True},
    }
    return doc


def grid_arch() -> dict:
    doc = base(
        "grid9x9",
        "Baseline grid QCCD for BB [[144,12,12]]: a 9x9 lattice of junctions with a "
        "trap in the middle of each of the 144 wires. 49 degree-4 X-junctions, 28 "
        "degree-3 T-junctions and 4 degree-2 bends, all read off the incidence count.",
        {
            "baseline": "PLAN §7.1 baseline 3 (2004.04706, static EJF scheduling)",
            "note": "2ab - a - b = 144 traps at a = b = 9, one per data qubit",
        },
    )
    doc["geometry"] = {"generator": "grid", "params": {"a": 9, "b": 9}}
    doc["control"] = wise_control()
    doc["control"]["wiring"]["scheme"] = "direct"
    doc["control"]["wiring"]["compensation_electrodes_per_trap"] = 8
    doc["control"]["wiring"]["shim_per_dac"] = 1
    # the whole point of this device: identical geometry to deck_unit_cell, wired the
    # other way.  One channel per site per role, so the channel count is O(traps).
    doc["control"]["channels"] = {
        "grouping": "direct",
        "roles": {"linear_h": 6, "linear_v": 6, "junction": 4},
        "differential": 2,
        "switch_per_site": True,
    }
    return doc


def h2_racetrack() -> dict:
    doc = base(
        "h2_racetrack",
        "Quantinuum H2: a linear trap with periodic boundary conditions -- a race track. "
        "One continuous RF null, so the curved end zones are ordinary conveyor-belt "
        "regions and the device has NO junctions at all (R18). Three voltage signals "
        "drive 20 wells per side, plus individually driven shim electrodes.",
        {
            "paper": H2,
            "measured": "2Q 1.84(5)e-3, 1Q 2.5(3)e-5, SPAM 1.6(1)e-3, 32 qubits, QV 2^16",
            "note": (
                "shipped hardware, and the experimental reference for M4: its compiler "
                "already reconfigures by parallel bubble sort in both directions around "
                "the loop (cl_h2_uses_bubble_sort)"
            ),
        },
    )
    doc["geometry"] = {"generator": "racetrack", "params": {"straight": 20}}
    doc["control"] = wise_control(
        [
            {"id": "rotate_cw", "type": "shift", "orbit": "L0", "delta": 1},
            {"id": "rotate_ccw", "type": "shift", "orbit": "L0", "delta": -1},
        ]
        + SORT_CLASSES
    )
    doc["control"]["wiring"] = {
        "scheme": "broadcast_groups",
        "dacs_dynamic": 3,
        "shim_per_dac": 1,
        "electrodes_per_trap": 3,
        "electrodes_per_junction": 0,
        "compensation_electrodes_per_trap": 1,
        "note": (
            "conveyor-belt DC electrodes tied {a,b,c}: three signals drive 20 wells per "
            "side, with separate individually driven shims for micromotion compensation"
        ),
    }
    return doc


def ladder_2x72() -> dict:
    doc = base(
        "ladder_2x72",
        "The deck's rails-and-highways ladder (p.5, p.16, p.18): two 72-slot rails joined "
        "by vertical rungs (the computing region), plus top and bottom shuttling highways "
        "an ion can be ejected onto, shuttled along, and re-inserted from.",
        {
            "deck": "ion_transport_deck_v3.pptx.pdf p.5, p.16, p.18",
            "baseline": (
                "ladder_2x72_baseline in the shipped artifact: steps 18247, cost 33228 "
                "on the isolated-check planner"
            ),
            "note": (
                "a rail site carrying both a rung and a highway on-ramp is degree 4, a "
                "real X-junction -- which is what the deck's transport primitives (p.13) "
                "charge the extra step for"
            ),
        },
    )
    doc["geometry"] = {
        "generator": "ladder",
        "params": {
            "width": 72,
            "rungs": 6,
            "highways": 2,
            "site_zone": "data",
            "highway_zone": "trap",
        },
    }
    doc["control"] = wise_control(
        [
            {"id": "shift_right", "type": "shift", "orbit": "TOP", "delta": 1},
            {"id": "shift_left", "type": "shift", "orbit": "TOP", "delta": -1},
            {
                "id": "eject",
                "type": "shift",
                "orbit": "onramp",
                "direction": "out",
                "entails": ["split", "merge"],
            },
            {
                "id": "reinsert",
                "type": "shift",
                "orbit": "onramp",
                "direction": "in",
                "entails": ["split", "merge"],
            },
        ]
    )
    return doc


def cyclone_dual_loop() -> dict:
    doc = base(
        "cyclone_dual_loop",
        "Deck p.12: one data loop and one ancilla loop, concentric. The data loop stays "
        "still while the ancilla loop rotates past it; one rotation finishes one syndrome "
        "type and two complete the ESM. One ion per trap, Data : Ancilla = 1 : 1 -- which "
        "the deck itself flags as not space efficient.",
        {
            "deck": "ion_transport_deck_v3.pptx.pdf p.12",
            "note": (
                "couplings between the loops are left at zero: each one would make a node "
                "on BOTH loops degree 3, costing exactly what the shipped ring's dock "
                "spurs cost. Whether a gate needs transport or only adjacency is "
                "q_dual_loop_gate_mechanism."
            ),
        },
    )
    doc["geometry"] = {"generator": "dual_loop", "params": {"width": 36}}
    doc["control"] = wise_control(
        [
            {
                "id": "rotate_cw",
                "type": "shift",
                "orbit": "A",
                "delta": 1,
                "note": "the ancilla loop rotates; the data loop does not move",
            },
            {"id": "rotate_ccw", "type": "shift", "orbit": "A", "delta": -1},
        ]
    )
    doc["control"]["wiring"]["scheme"] = "broadcast_groups"
    doc["control"]["wiring"]["dacs_dynamic"] = 6
    return doc


def deck_unit_cell() -> dict:
    doc = base(
        "deck_unit_cell",
        "The deck's own unit-cell array (p.2, p.4, p.19-20): a lattice of square trap "
        "zones joined at X-junctions, with 24 DC electrodes per cell in three control "
        "classes -- 12 linear and 4 junction electrodes broadcast, 8 compensation "
        "electrodes individually tuned behind a 1:100 demux.",
        {
            "deck": "ion_transport_deck_v3.pptx.pdf p.2, p.4, p.19-20",
            "formulas": (
                "electrodes 24N, switches 48N, DACs 24 + 8 + 8N/100, trapping zones "
                "N - b, for N = a*b unit cells"
            ),
            "note": (
                "modelled as the lattice the renders show: traps on the wires, junctions "
                "at the lattice points. The deck's 'trapping zones = N - b' does not "
                "follow from that reading -- see q_unit_cell_zone_count."
            ),
        },
    )
    doc["geometry"] = {"generator": "grid", "params": {"a": 9, "b": 9}}
    doc["control"] = wise_control()
    doc["control"]["wiring"] = {
        "scheme": "wise",
        "dacs_dynamic": 32,
        "shim_per_dac": 100,
        "electrodes_per_trap": 24,
        "electrodes_per_junction": 48,
        "compensation_electrodes_per_trap": 8,
        "note": (
            "12*2 linear + 4*2 junction = 32 broadcast DACs, constant in array size; only "
            "the 8 compensation electrodes per cell scale, behind a 1:100 demux"
        ),
    }
    return doc


def stationary_chain() -> dict:
    doc = base(
        "stationary_chain",
        "One trap, no transport: the stationary chain that already demonstrated breakeven "
        "(arXiv:2606.06455) with steerable Raman beams for all-to-all gates, OMG in-place "
        "mid-circuit measurement, and ancillas doubling as coolants. The degenerate case "
        "the platform has to express without special casing.",
        {
            "paper": "2606.06455",
            "note": (
                "the baseline a transport architecture has to beat; the crossover code "
                "size is q_crossover_vs_stationary"
            ),
        },
    )
    doc["geometry"] = {"generator": "chain", "params": {"n": 2, "site_zone": "register"}}
    doc["zone_types"] = dict(ZONE_TYPES)
    doc["zone_types"]["register"] = {
        "capacity": 32,
        "gate": True,
        "spam": True,
        "cool": True,
        "note": "one long chain; gate time degrades sharply above ~15 ions (R13)",
    }
    doc["control"] = {
        "model": "direct",
        "max_simd_classes_per_cycle": 1,
        "classes": {
            "generator": "x_junction_grid",
            "count": 18,
            "extra": BASIC_CLASSES,
        },
        "wiring": {
            "scheme": "direct",
            "dacs_dynamic": 0,
            "electrodes_per_trap": 24,
            "electrodes_per_junction": 0,
        },
        "optical": {"addressing": "steerable_raman", "per_zone_switch": False},
    }
    return doc


FILES = {
    "ring144_24v.arch.json": ring144_24v,
    "cyclone_base.arch.json": cyclone_base,
    "chain.arch.json": chain_arch,
    "grid9x9.arch.json": grid_arch,
    "h2_racetrack.arch.json": h2_racetrack,
    "ladder_2x72.arch.json": ladder_2x72,
    "cyclone_dual_loop.arch.json": cyclone_dual_loop,
    "deck_unit_cell.arch.json": deck_unit_cell,
    "stationary_chain.arch.json": stationary_chain,
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rc = 0
    for filename, builder in FILES.items():
        doc = builder()
        path = OUT / filename
        path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        arch = load(path)  # parse + expand + structural check
        again = Architecture.from_json(arch.to_json(expanded=True))
        from qccd.arch import devices_equal

        diffs = devices_equal(arch.device, again.device)
        s = arch.device.summary()
        status = "ok" if not diffs else f"ROUND-TRIP DIFF: {diffs}"
        if diffs:
            rc = 1
        print(
            f"{path.name:26s} {s['n_nodes']:5d} nodes {s['n_segments']:5d} segments "
            f"deg={s['degree_histogram']} junctions={s['n_junction_nodes']:3d} "
            f"corners={s['n_corners']} docks={s['n_docks']} "
            f"dock-corners={s['n_dock_corners']}  {status}"
        )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
