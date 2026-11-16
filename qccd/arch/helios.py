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

from .device import Architecture, Device, ExpansionError, Loop, Node, Segment

PHYSICS = Path(__file__).resolve().parents[1] / "workspace" / "physics" / "qccd-physics@3.json"


def helios(
    ring_sites: int = 82,
    *,
    operation_zones_per_leg: int = 4,
    leg_storage_sites: int = 4,
    segment_capacity: int = 1,
) -> Device:
    """The public Helios floorplan from Ransford et al. (arXiv:2511.05465, Fig. 2).

    The paper publishes the functional topology, not an electrode-by-electrode netlist:
    82 qubits can remain in the rotatable storage ring while 16 occupy eight two-ion
    operation zones.  A single four-way X junction joins that ring to upper and lower
    cache/logic/leg-storage paths.  The drawing here preserves exactly those published
    regions and capacities; the number of cache and far-leg drawing sites is a visual
    discretisation, not a claim about unpublished electrode wells.
    """
    import math

    if ring_sites < 4:
        raise ExpansionError("Helios needs at least four ring-storage sites")
    if operation_zones_per_leg < 1:
        raise ExpansionError("Helios needs an operation zone on each leg")

    nodes: dict[str, Node] = {}
    segments: dict[str, Segment] = {}
    loops: dict[str, Loop] = {}
    radius = 3.0
    # X0 is the only junction.  It lies on the ring and also opens the upper and lower legs.
    nodes["X0"] = Node(id="X0", pos=(radius + 0.25, 0.0), kind="junction",
                        labels=("x_junction",))
    order = ["X0"]
    for i in range(ring_sites):
        theta = 2.0 * math.pi * (i + 1) / (ring_sites + 1)
        nid = f"R{i}"
        nodes[nid] = Node(id=nid,
                          pos=(round(radius * math.cos(theta), 6),
                               round(radius * math.sin(theta), 6)),
                          kind="site", zone_type="ring_storage",
                          labels=("ring_storage", "memory"))
        order.append(nid)
    for i, (a, b) in enumerate(zip(order, order[1:] + order[:1])):
        sid = f"ER{i}"
        segments[sid] = Segment(id=sid, ends=(a, b), capacity=segment_capacity,
                                loop="storage_ring", labels=("ring_storage",))
    loops["storage_ring"] = Loop(id="storage_ring", nodes=tuple(order), closed=True,
                                  kind="ring", note="rotatable 82-qubit storage ring")

    for prefix, y, label in (("U", 1.15, "upper"), ("L", -1.15, "lower")):
        path = ["X0"]
        cache = f"{prefix}C"
        nodes[cache] = Node(id=cache, pos=(4.1, y), kind="site", zone_type="cache",
                            labels=("cache", label, "memory"))
        path.append(cache)
        for i in range(operation_zones_per_leg):
            nid = f"{prefix}O{i}"
            nodes[nid] = Node(id=nid, pos=(5.2 + 1.15 * i, y), kind="site",
                              zone_type="operation",
                              labels=("operation", label, "logic"))
            path.append(nid)
        for i in range(leg_storage_sites):
            nid = f"{prefix}L{i}"
            nodes[nid] = Node(id=nid,
                              pos=(5.2 + 1.15 * operation_zones_per_leg + 0.8 * i, y),
                              kind="site", zone_type="leg_storage",
                              labels=("leg_storage", label, "memory"))
            path.append(nid)
        for i, (a, b) in enumerate(zip(path, path[1:])):
            sid = f"E{prefix}{i}"
            segments[sid] = Segment(id=sid, ends=(a, b), capacity=segment_capacity,
                                    labels=(label,))

    return Device(
        nodes=nodes, segments=segments, loops=loops, generator="helios",
        params={"family": "helios", "qubits": 98, "ring_storage_qubits": 82,
                "operation_zones": 2 * operation_zones_per_leg,
                "operation_batch_qubits": 4 * operation_zones_per_leg,
                "ring_sites": ring_sites,
                "operation_zones_per_leg": operation_zones_per_leg,
                "leg_storage_sites": leg_storage_sites,
                "segment_capacity": segment_capacity},
    )


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

