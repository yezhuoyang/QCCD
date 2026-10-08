"""Their machines, written in our architecture language.

A paper's device reaches us as a `PortGraph` (what its timing model sees: traps with ports,
junctions, segments).  To draw it, open it in the studio, or hand it to our compiler it has
to be an architecture document: nodes at positions, segments, zones, and the paper's own
primitive times as a curve table named after the paper.  The positions are not in any of
these artifacts (only the Jones artifact records coordinates), so each family's layout is
reconstructed from the builder that made it, and cited.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from ..arch.schema import SCHEMA_VERSION
from .timed import PortGraph

__all__ = ["layout", "to_arch_doc", "to_machine", "TABLES"]


def _num(node: str) -> int:
    return int("".join(ch for ch in node if ch.isdigit()))


def layout(pg: PortGraph, family: str, **kw) -> dict[str, tuple[float, float]]:
    """Positions for every node of `pg`, by the builder that produced it.

    ``qccdsim_linear``  `test_machines.make_linear_machine`: T0 J0 T1 J1 ... on a line
    ``qccdsim_g2x3``    `test_machines.test_trap_2x3`: J0-J1-J2 bus, T0 T1 T2 above it and
                        T5 T4 T3 below
    ``qccdsim_nxn``     the Cyclone fork's `make_nxn_grid(N)`: junction j(x*N+i) joins trap
                        x*N+i to trap (x+1)*N+i, junctions chained along each row x
    ``ring``            `make_circle_machine_length_n`: T0 J0 T1 J1 ... around a loop
    ``recorded``        positions the artifact recorded (`site["pos"]` / `node["pos"]`)
    ``tiscc``           TISCC's grid: cell (r, c) holds M O M J M O M, J at the cell corner,
                        the vertical segment below it, the horizontal one to its right
    ``ionshuttler``     MQT IonShuttler's grid: a node is named by its "row,col" (the tool's
                        networkx grid; the processing-zone path has fractional ones); a site
                        (one edge of the tool's graph) sits midway between its two nodes
    ``trapsimd``        TrapSIMD's junction grid: each junction's arms (N/E/S/W) name the trap
                        on that side; a trap's positions run outward from the junction it is
                        attached to, one unit apart, and the next junction sits past its end
    """
    pos: dict[str, tuple[float, float]] = {}
    # Spacings are kept at or below one unit: the site's drawing bows any segment longer
    # than 1.5 units, as it would a loop closing on itself.
    if family == "qccdsim_linear":
        for t in pg.sites:
            pos[t] = (1.0 * _num(t), 0.0)
        for j in pg.nodes:
            pos[j] = (1.0 * _num(j) + 0.5, 0.0)
    elif family == "qccdsim_g2x3":
        top = {"T0": 0, "T1": 1, "T2": 2}
        bottom = {"T5": 0, "T4": 1, "T3": 2}
        for t in pg.sites:
            pos[t] = (1.0 * top[t], 0.7) if t in top else (1.0 * bottom[t], -0.7)
        for j in pg.nodes:
            pos[j] = (1.0 * _num(j), 0.0)
    elif family == "qccdsim_nxn":
        n = int(kw["N"])
        for t in pg.sites:
            r, c = divmod(_num(t), n)
            pos[t] = (1.0 * c, -1.0 * r)
        for j in pg.nodes:
            x, i = divmod(_num(j), n)
            pos[j] = (1.0 * i, -1.0 * x - 0.5)
    elif family == "ring":
        k = 2 * len(pg.sites)
        radius = 0.5 * k / (2 * math.pi)
        for idx in range(k):
            node = f"T{idx // 2}" if idx % 2 == 0 else f"J{idx // 2}"
            a = math.pi / 2 - 2 * math.pi * idx / k          # clockwise from the top
            pos[node] = (radius * math.cos(a), radius * math.sin(a))
    elif family == "recorded":
        pts = [a["pos"][:2] for a in list(pg.sites.values()) + list(pg.nodes.values())]
        # the artifact's units are its own; scale so the nearest neighbours sit 0.5 apart
        d = min((math.dist(u, v) for u in pts[:60] for v in pts if u is not v and math.dist(u, v) > 0),
                default=1.0)
        k = 0.5 / d
        for t, a in pg.sites.items():
            pos[t] = (float(a["pos"][0]) * k, float(a["pos"][1]) * k)
        for j, a in pg.nodes.items():
            pos[j] = (float(a["pos"][0]) * k, float(a["pos"][1]) * k)
    elif family == "tiscc":
        ncols = int(kw["ncols"])
        offs = {0: (0.0, -3.0), 1: (0.0, -2.0), 2: (0.0, -1.0), 3: (0.0, 0.0),
                4: (1.0, 0.0), 5: (2.0, 0.0), 6: (3.0, 0.0)}
        for node in list(pg.sites) + list(pg.nodes):
            cell, k = divmod(int(node), 7)
            r, c = divmod(cell, ncols)
            dx, dy = offs[k]
            pos[node] = (4.0 * c + dx, -4.0 * r + dy)
    elif family == "ionshuttler":
        rc = lambda n: tuple(float(x) for x in n.split(","))
        for j in pg.nodes:
            r, c = rc(j)
            pos[j] = (c, -r)
        for sid, a in pg.sites.items():
            (r1, c1), (r2, c2) = (rc(n) for n in a["nodes"])
            pos[sid] = ((c1 + c2) / 2, -(r1 + r2) / 2)
    elif family == "trapsimd":
        step = {"N": (0.0, 1.0), "S": (0.0, -1.0), "E": (1.0, 0.0), "W": (-1.0, 0.0)}
        traps: dict[str, list[str]] = {}
        for sid, a in pg.sites.items():
            traps.setdefault(a["trap"], []).append(sid)
        for t in traps.values():
            t.sort(key=lambda sid: pg.sites[sid]["index"])
        placed_traps: set[str] = set()
        todo = sorted(pg.nodes)[:1]
        if todo:
            pos[todo[0]] = (0.0, 0.0)
        while todo:
            j = todo.pop()
            jx, jy = pos[j]
            for arm, trap in sorted(pg.nodes[j].get("arms", {}).items()):
                if trap in placed_traps or trap not in traps:
                    continue
                dx, dy = step[arm]
                sites = traps[trap]
                if j not in pg.sites[sites[0]].get("junctions", {}):
                    sites = sites[::-1]            # run outward from this junction
                for k, sid in enumerate(sites, start=1):
                    pos[sid] = (jx + k * dx, jy + k * dy)
                placed_traps.add(trap)
                far = pg.sites[sites[-1]].get("junctions", {})
                for j2 in far:
                    if j2 != j and j2 not in pos:
                        pos[j2] = (jx + (len(sites) + 1) * dx, jy + (len(sites) + 1) * dy)
                        todo.append(j2)
        # a trap attached to no junction (Fig. 7's single trap): a row of its own
        row = min((y for _, y in pos.values()), default=0.0) - 2.0
        for trap, sites in sorted(traps.items()):
            if trap not in placed_traps:
                for k, sid in enumerate(sites):
                    pos[sid] = (float(k), row)
                row -= 2.0
    else:
        raise ValueError(f"unknown layout family {family!r}")
    missing = (set(pg.sites) | set(pg.nodes)) - set(pos)
    if missing:
        raise ValueError(f"layout {family!r} placed nothing at {sorted(missing)[:5]}")
    return pos


#: Each paper's primitive times in our curve-table format, with where they were read.  A
#: law our language cannot state as one number (QCCDSim's FM gate keyed to trap capacity)
#: is written at the configuration's own value, and the note says so.
TABLES: dict[str, dict[str, Any]] = {
    "murali2020": {"source": "2004.04706", "shuttle_us": 5, "split_us": 80, "merge_us": 80,
                   "junction_us": {"2": 5, "3": 100, "4": 120},
                   "note": "Table I; degree-2 junction 5 us and ion swap 42 us from QCCDSim's "
                           "run.py; FM gate = max(100, 13.33*(c+2) - 54) us, c = trap capacity"},
    "saki2022": {"source": "2111.07961", "shuttle_us": 5, "split_us": 80, "merge_us": 80,
                 "junction_us": {"2": 5, "3": 100, "4": 120},
                 "note": "QCCDSim's constants, which the compiler inherits; FM gate at capacity "
                         "15 + 2: 172 us"},
    "bach2025": {"source": "2501.12470", "shuttle_us": 5, "split_us": 80, "merge_us": 80,
                 "junction_us": {"2": 5, "3": 100, "4": 120},
                 "note": "Table 1, large-scale column (QCCDSim's values; the degree-2 crossing, "
                         "5 us, is QCCDSim's)"},
    "khan2025moveless": {"source": "2508.03914", "shuttle_us": 5, "split_us": 80, "merge_us": 80,
                         "junction_us": {"2": 5, "3": 100, "4": 120},
                         "note": "QCCDSim's constants, which the artifact inherits; FM gate at "
                                 "capacity 5 + 2: 100 us"},
    "khan2026cyclone": {"source": "2511.15910", "shuttle_us": 5, "split_us": 80, "merge_us": 80,
                        "junction_us": {"2": 0, "3": 100, "4": 120},
                        "note": "as the artifact prices it: s = 80 + 5 + 80 = 165 us per step, a "
                                "gate swap of 3 gate times per ancilla per step"},
    "jones2025": {"source": "2510.23519", "shuttle_us": 5, "split_us": 80, "merge_us": 80,
                  "junction_us": {"3": 100, "4": 100},
                  "note": "Table 1; a junction passage is an entry and an exit of 50 us each"},
    "schoenberger2024": {"source": "2311.03454", "shuttle_us": 0.5, "split_us": 0.25,
                         "merge_us": 0.25, "junction_us": {"2": 0, "3": 0, "4": 0},
                         "note": "abstract time steps, one per transition (one per loop iteration "
                                 "for the heuristic); shown here as microseconds"},
    "ruan2025": {"source": "2504.17886", "shuttle_us": 58, "split_us": 0, "merge_us": 0,
                 "junction_us": {"2": 250, "3": 250, "4": 250},
                 "note": "Table 1: intra-trap shift 58 us a position, inter-trap shift 250 us a "
                         "broadcast cycle, swaps 200 / 500 us; two-qubit gate 141 us effective "
                         "(58 + 25 + 58), which both worked examples need"},
    "leblond2023": {"source": "2311.10687", "shuttle_us": 5.25, "split_us": 0, "merge_us": 0,
                    "junction_us": {"3": 110.25, "4": 110.25},
                    "note": "TISCC's gate table: Move 5.25 us; a junction move is Move + Junction "
                            "(105 us); ZZ 2000 us includes split, merge and cooling"},
}


_ID_CHAR = {"|": ":", "~": "-", "@": ":", ",": "_"}


def adl_id(n: str) -> str:
    """A node or segment id the architecture language accepts (``^[A-Za-z_][A-Za-z0-9_.:-]*$``):
    TISCC numbers its zones, TrapSIMD writes ``N1.0|1`` and ``N1~J1``.  Readable where it can
    be; `to_arch_doc` refuses a mapping that would merge two ids."""
    out = "".join(c if c.isalnum() or c in "_.:-" else _ID_CHAR.get(c, f"_{ord(c):x}_") for c in n)
    return out if out[:1].isalpha() or out[:1] == "_" else f"Z{out}"


def to_arch_doc(pg: PortGraph, positions: Mapping[str, tuple[float, float]], *, name: str,
                table: str, gate_us: float | None = None, note: str = "") -> dict:
    """An explicit-geometry architecture document for `pg`, priced by the paper's table."""
    t = TABLES[table]
    nid = adl_id
    ids = list(pg.sites) + list(pg.nodes) + list(pg.segments)
    if len({adl_id(i) for i in ids}) != len(set(ids)):
        raise ValueError("two ids of this device map to one id of the language")
    nodes = []
    for s, a in pg.sites.items():
        nodes.append({"id": nid(s), "pos": list(positions[s]), "kind": "site",
                      "capacity": int(a["capacity"]), "zone_type": "trap", "labels": ["trap"]})
    for j in pg.nodes:
        nodes.append({"id": nid(j), "pos": list(positions[j]), "kind": "junction", "labels": []})
    segments = []
    for sid, (a, b) in pg.segments.items():
        d = math.dist(positions[a], positions[b])
        segments.append({"id": nid(sid), "ends": [nid(a), nid(b)], "length": round(max(d, 1e-3), 6),
                         "capacity": 1})
    src = t["source"]
    # The language's curve tables are a closed set; a paper's own numbers go in `local`, the
    # table for values a document states for itself, with the paper as the source.
    pt = lambda us, label: {"us": us, "quanta": 0.0, "table": "local", "source": src,
                            "label": f"{table}: {label}"}
    prims: dict[str, Any] = {
        "shuttle_segment": {"curve": [pt(t["shuttle_us"], "one segment")]},
        "split": {"curve": [pt(t["split_us"], "split")]},
        "merge": {"curve": [pt(t["merge_us"], "merge")]},
        "junction_cross": {"curve_by_degree": {
            deg: [pt(us, f"junction of degree {deg}")] for deg, us in t["junction_us"].items()}},
    }
    if gate_us is not None:
        prims["ms_gate"] = {"us": gate_us, "source": src, "note": t["note"]}
    return {
        "name": name,
        "schema_version": SCHEMA_VERSION,
        "description": note or f"rebuilt from {src} ({t['note']})",
        "zone_types": {"trap": {"capacity": max(int(a["capacity"]) for a in pg.sites.values()),
                                "gate": True, "spam": True, "cool": True}},
        "primitives": prims,
        "control": {"model": "direct"},
        "geometry": {"generator": "explicit", "params": {}, "nodes": nodes, "segments": segments},
    }


def to_machine(pg: PortGraph, positions: Mapping[str, tuple[float, float]], **kw):
    from ..api import Machine
    from ..arch import Architecture

    return Machine(Architecture.from_json(to_arch_doc(pg, positions, **kw)))
