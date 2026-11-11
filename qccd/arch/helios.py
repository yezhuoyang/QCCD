"""Quantinuum Helios's published physical architecture.

Ransford et al., arXiv:2511.05465, Fig. 2 and Sec. II A publish the functional
floorplan: 98 Ba qubits, 82 available in a rotatable storage ring while a batch of 16
uses eight two-ion operation zones, with cache and leg storage joined by one X junction.
The paper does not publish an electrode netlist, so this module deliberately models
functional regions rather than inventing electrode wells.
"""

from __future__ import annotations

import json
from pathlib import Path

from .device import Architecture
from .generators import helios

PHYSICS = Path(__file__).resolve().parents[1] / "workspace" / "physics" / "qccd-physics@3.json"


def device_doc(name: str = "helios_98") -> dict:
    physics = json.loads(PHYSICS.read_text(encoding="utf-8"))
    geometry = helios().to_json()
    # Keep the public architecture self-contained for the browser and schema.  ``helios``
    # remains the Python authoring helper; an exported document carries its explicit graph.
    geometry["generator"] = "explicit"
    return {
        "name": name,
        "schema_version": "0.3",
        "description": (
            "Quantinuum Helios: 98 Ba-137 qubits in a QCCD with a rotatable 82-qubit "
            "storage ring, one four-way junction, upper and lower cache/leg storage, and "
            "eight two-ion quantum-operation zones. Functional topology from Fig. 2; "
            "unpublished electrode-level wells are not inferred."
        ),
        "provenance": {
            "paper": "2511.05465",
            "measured": "98 qubits; 8 operation zones; batches of 16; all-to-all connectivity",
            "note": "Published functional topology, not an electrode-level mask layout",
        },
        "zone_types": {
            "ring_storage": {"capacity": 1, "gate": False, "spam": False, "cool": True},
            "cache": {"capacity": 2, "gate": False, "spam": False, "cool": True},
            "operation": {"capacity": 2, "gate": True, "spam": True, "cool": True},
            "leg_storage": {"capacity": 2, "gate": False, "spam": False, "cool": True},
        },
        "primitives": physics["primitives"],
        "heating": physics["heating"],
        "species": physics["species"],
        "budget": physics["budget"],
        "geometry": geometry,
        "control": {
            "model": "simd_classes",
            "classes": {
                "generator": "helios_regions",
                "count": 3,
                "extra": [
                    {"id": "helios_rotate_cw", "type": "shift",
                     "orbit": "storage_ring", "delta": 1},
                    {"id": "helios_rotate_ccw", "type": "shift",
                     "orbit": "storage_ring", "delta": -1},
                    {"id": "helios_shuttle", "type": "shift", "orbit": "any",
                     "note": "one crystal between adjacent functional regions"},
                ],
            },
            "max_simd_classes_per_cycle": 1,
            "intra_inter_exclusive": True,
            "wiring": {
                "scheme": "broadcast_groups",
                "dacs_dynamic": 3,
                "shim_per_dac": 1,
                "electrodes_per_trap": 3,
                "electrodes_per_junction": 4,
                "compensation_electrodes_per_trap": 1,
                "note": "repeating triplet broadcast pattern in memory regions",
            },
            "optical": {"addressing": "focused", "per_zone_switch": True},
            "channels": {"grouping": "broadcast", "switch_per_site": True},
        },
    }


def architecture(name: str = "helios_98") -> Architecture:
    return Architecture.from_json(device_doc(name))

