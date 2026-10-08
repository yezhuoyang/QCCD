"""Artifact outputs -> `TimedSchedule`.

An importer translates; it does not judge.  Where it has to resolve something the artifact
left implicit (which ion a "Move q1 q2" carries, which of two segments that share an id a
move means), it resolves it from the artifact's own state and records how, and the checker
then re-derives every position independently.  A schedule an importer could not translate
faithfully raises; it is never patched into shape.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from .models import TISCC_US, QCCDSimParams
from .timed import Event, PortGraph, TimedSchedule

__all__ = ["tiscc_adjacent", "tiscc_grid", "import_tiscc", "qccdsim_device",
           "import_qccdsim", "qccdsim_params", "qasm_cx", "jones_device", "import_jones",
           "stim_cx", "cyclone_device", "import_cyclone", "cyclone_circuit"]


# ----------------------------------------------------------------------------- TISCC


def _tiscc_rc(site: int, ncols: int) -> tuple[int, int, int]:
    cell, k = divmod(site, 7)
    r, c = divmod(cell, ncols)
    return r, c, k


def tiscc_adjacent(site: int, nrows: int, ncols: int) -> set[int]:
    """`GridManager::get_adjacent`, line for line (src/gridmanager.cpp)."""
    r, c, k = _tiscc_rc(site, ncols)
    at = lambda rr, cc, kk: (rr * ncols + cc) * 7 + kk
    out: set[int] = set()
    if k not in (0, 6):
        out |= {at(r, c, k - 1), at(r, c, k + 1)}
    elif k == 0:
        out.add(at(r, c, 1))
        if r < nrows - 1:
            out.add(at(r + 1, c, 3))
    else:
        out.add(at(r, c, 5))
        if c < ncols - 1:
            out.add(at(r, c + 1, 3))
    if k == 3:
        if r > 0:
            out.add(at(r - 1, c, 0))
        if c > 0:
            out.add(at(r, c - 1, 6))
    return out


def tiscc_grid(nrows: int, ncols: int) -> PortGraph:
    """The TISCC hardware grid: per cell M,O,M,J,M,O,M; every zone holds one ion; the J zone
    is a junction an ion crosses but never rests on."""
    n = 7 * nrows * ncols
    sites: dict[str, dict] = {}
    nodes: dict[str, dict] = {}
    for s in range(n):
        _, _, k = _tiscc_rc(s, ncols)
        if k == 3:
            nodes[str(s)] = {"kind": "J"}
        else:
            sites[str(s)] = {"capacity": 1, "zone": "O" if k in (1, 5) else "M"}
    segments: dict[str, tuple[str, str]] = {}
    adjacent: dict[str, list[str]] = {}
    for s in range(n):
        adj = tiscc_adjacent(s, nrows, ncols)
        for t in adj:
            a, b = sorted((s, t))
            segments[f"e{a}_{b}"] = (str(a), str(b))
        if str(s) in sites:
            adjacent[str(s)] = sorted((str(t) for t in adj if str(t) in sites), key=int)
    return PortGraph(sites=sites, nodes=nodes, segments=segments, adjacent=adjacent)


_SPEC = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s+(\S+)\s+([\d,]+)\s*$")


def import_tiscc(spec: str | Path, *, nrows: int, ncols: int, name: str | None = None,
                 source: Mapping[str, Any] | None = None) -> TimedSchedule:
    """Read a TISCC `-p` hardware schedule (`time op qsite[,qsite]` lines).

    Ions are named by the zone they start in (`Qubit_at` at t = -1).  A `Move a,b` carries
    whichever ion is at `a` at that point of the file; a move to a zone that is not adjacent
    is a junction move across the junction both zones touch (TISCC's own classification,
    `GridManager::print_resource_counts`).  Durations come from TISCC's gate table: the
    file states start times only.
    """
    text = Path(spec).read_text(encoding="utf-8")
    dev = tiscc_grid(nrows, ncols)
    occupant: dict[int, str] = {}
    chains: dict[str, list[str]] = {}
    events: list[Event] = []
    resources: dict[str, str] = {}
    for line in text.splitlines():
        m = _SPEC.match(line)
        if not m:
            if ":" in line:
                k, _, val = line.partition(":")
                resources[k.strip()] = val.strip()
            continue
        t, op, args = float(m.group(1)), m.group(2), [int(x) for x in m.group(3).split(",")]
        if op == "Qubit_at":
            ion = f"q{args[0]}"
            occupant[args[0]] = ion
            chains.setdefault(str(args[0]), []).append(ion)
            continue
        eid = len(events)
        if op == "Move":
            a, b = args
            if a not in occupant:
                raise ValueError(f"line {line.strip()!r}: no ion at {a}")
            ion = occupant.pop(a)
            if b in tiscc_adjacent(a, nrows, ncols):
                path: tuple[str, ...] = ()
            else:
                via = tiscc_adjacent(a, nrows, ncols) & tiscc_adjacent(b, nrows, ncols)
                if len(via) != 1:
                    raise ValueError(f"line {line.strip()!r}: {a}->{b} shares {len(via)} junctions")
                path = (str(next(iter(via))),)
            occupant[b] = ion
            dur = TISCC_US["Move"] + (TISCC_US["Junction"] if path else 0.0)
            events.append(Event(eid, "hop", t, t + dur, (ion,), src=str(a), dst=str(b),
                                path=path, meta={"op": "Move", "junction": bool(path)}))
            continue
        ions = []
        for s in args:
            if s not in occupant:
                raise ValueError(f"line {line.strip()!r}: no ion at {s}")
            ions.append(occupant[s])
        dur = TISCC_US[op]
        if op == "ZZ":
            kind, at = "gate", str(args[0])
        elif op == "Prepare_Z":
            kind, at = "prepare", str(args[0])
        elif op == "Measure_Z":
            kind, at = "measure", str(args[0])
        else:
            kind, at = "gate1", str(args[0])
        events.append(Event(eid, kind, t, t + dur, tuple(ions), at=at,
                            meta={"op": op, "sites": args}))
    for s in dev.sites:
        chains.setdefault(s, [])
    return TimedSchedule(
        name=name or Path(spec).stem, device=dev, chains=chains, events=events,
        source=dict(source or {}), claims=resources,
    )


# --------------------------------------------------------------------------- QCCDSim
#
# QCCDSim (Murali et al., ISCA 2020) and the artifacts forked from it: Muzzle the Shuttle,
# Moveless, Cyclone's baseline.  The run step dumps `Schedule.events` verbatim, plus the
# `Machine` (traps with their port sides, junctions, segments) and the initial chains.


def qccdsim_device(raw: Mapping[str, Any]) -> tuple[PortGraph, dict[tuple[str, str], str]]:
    """A dumped QCCDSim `Machine` -> port graph, plus (QCCDSim segment id, endpoint) -> ours.

    QCCDSim's G2x3 builder (`test_machines.test_trap_2x3`) gives BOTH junction-junction
    links segment id 6.  They become `S6:J0-J1` and `S6:J1-J2`; a move that names segment
    6 is resolved by the junction it crosses (the other segment of the move pins it).
    """
    sites: dict[str, dict] = {}
    for t in raw["traps"]:
        sites[f"T{t['id']}"] = {"capacity": int(t["capacity"]),
                                "fill": int(t.get("fill_capacity", t["capacity"])),
                                "ports": {}}
    nodes = {f"J{j['id']}": {} for j in raw["junctions"]}
    by_id: dict[str, list[tuple[str, str]]] = {}
    for s in raw["segments"]:
        by_id.setdefault(str(s["id"]), []).append(tuple(s["endpoints"]))
    segments: dict[str, tuple[str, str]] = {}
    name_of: dict[tuple[str, str], str] = {}
    for sid, ends in by_id.items():
        for a, b in ends:
            key = f"S{sid}" if len(ends) == 1 else f"S{sid}:{a}-{b}"
            segments[key] = (a, b)
            name_of[(sid, a)] = key
            name_of[(sid, b)] = key
    for s in raw["segments"]:
        side = s.get("trap_side")
        # QCCDSim's own dump writes `trap` + `trap_side: "R"`; the Cyclone fork's writes
        # `trap_side: [{"trap": 0, "side": "L"}, ...]` -- one entry per trap end
        if isinstance(side, list):
            pairs = [(x["trap"], x["side"]) for x in side]
        elif isinstance(side, dict):
            pairs = [(side["trap"], side["side"])]
        elif side and s.get("trap") is not None:
            pairs = [(s["trap"], side)]
        else:
            pairs = []
        for trap, sd in pairs:
            key = name_of[(str(s["id"]), f"T{trap}")]
            sites[f"T{trap}"]["ports"][key] = sd
    return PortGraph(sites=sites, nodes=nodes, segments=segments), name_of


def qccdsim_params(config: Mapping[str, Any], device: Mapping[str, Any] | None = None) -> QCCDSimParams:
    """The run's gate law, swap type and constants (`run.py`'s `mpar_model1`)."""
    junction = {2: 5.0, 3: 100.0, 4: 120.0}
    for j in (device or {}).get("junctions", []):
        t = j.get("cross_time_us", j.get("cross_time"))
        if t is not None and "degree" in j:
            junction[int(j["degree"])] = float(t)
    # the run step records the analyzer's constants under `fidelity_model` when the tool
    # differs from QCCDSim's (Muzzle the Shuttle) or runs in its default Honeywell mode
    fm = config.get("fidelity_model") or {}
    hw = fm.get("honeywell_mode", config.get("honeywell_mode", False))
    return QCCDSimParams(gate=str(config.get("gate_type", "FM")),
                         swap=str(config.get("swap_type", "GateSwap")),
                         junction_us=junction, honeywell=bool(hw),
                         k1=float(fm.get("quanta_per_merge_and_per_swapped_split (k1)", 0.1)) if not hw else 0.1,
                         k2=float(fm.get("quanta_per_move (k2)", 0.01)) if not hw else 0.01)


def import_qccdsim(path: str | Path, *, name: str | None = None) -> TimedSchedule:
    """Read the run step's `schedule.json` for a QCCDSim-family artifact."""
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    dev, name_of = qccdsim_device(raw["device"])
    ion = lambda x: str(x)

    def seg(sid: Any, near: str | None = None, other: str | None = None) -> str:
        sid = str(sid)
        cands = sorted({v for (s, _), v in name_of.items() if s == sid})
        if len(cands) == 1:
            return cands[0]
        if near is not None and (sid, near) in name_of:
            return name_of[(sid, near)]
        if other is not None:
            ends = set(dev.segments[other])
            hit = [c for c in cands if set(dev.segments[c]) & ends]
            if len(hit) == 1:
                return hit[0]
        raise ValueError(f"segment {sid} is ambiguous ({cands}) and nothing pins it")

    events: list[Event] = []
    # Where each ion's segment is, followed through the artifact's own events in start
    # order: the only thing that can tell `S6:J0-J1` from `S6:J1-J2` when a move names
    # segment 6 at both ends (G2x3).  The checker re-derives every position regardless.
    on_seg: dict[str, str] = {}
    ordered = sorted(raw["events"], key=lambda e: (float(e["t0"]), int(e["id"])))
    later: dict[str, list[dict]] = {}
    for e in ordered:
        for i in e["ions"]:
            later.setdefault(ion(i), []).append(e)

    def next_touch(ion_id: str, after: int) -> set[str] | None:
        """The nodes the ion's NEXT transport event touches: what pins an ambiguous id."""
        seq = later[ion_id]
        k = next(j for j, x in enumerate(seq) if int(x["id"]) == after)
        for x in seq[k + 1:]:
            if x["kind"] == "Merge":
                return {f"T{x['trap']}"}
            if x["kind"] == "Move":
                c = sorted({v for (sid, _), v in name_of.items() if sid == str(x["dest_seg"])})
                if len(c) == 1:
                    return set(dev.segments[c[0]])
                c = sorted({v for (sid, _), v in name_of.items() if sid == str(x["source_seg"])})
                return set().union(*(set(dev.segments[v]) for v in c)) if len(c) == 1 else None
        return None

    for e in ordered:
        kind = e["kind"]
        common = dict(id=int(e["id"]), t0=float(e["t0"]), t1=float(e["t1"]),
                      ions=tuple(ion(i) for i in e["ions"]))
        meta = {k: e[k] for k in ("swap_cnt", "swap_hops", "ion_hops", "i1", "i2") if k in e}
        if kind == "Gate":
            events.append(Event(kind="gate", at=f"T{e['trap']}", meta=meta, **common))
        elif kind == "Split":
            swap = None
            if int(e.get("swap_hops", 0) or 0) and e.get("i1") != e.get("i2"):
                swap = {"kind": "gate", "with": ion(e["i2"]), "hops": int(e["swap_hops"])}
            elif int(e.get("ion_hops", 0) or 0):
                swap = {"kind": "ion", "hops": int(e["ion_hops"])}
            trap = f"T{e['trap']}"
            sg = seg(e["seg"], near=trap)
            on_seg[common["ions"][0]] = sg
            events.append(Event(kind="split", at=trap, seg=sg, swap=swap, meta=meta, **common))
        elif kind == "Merge":
            trap = f"T{e['trap']}"
            events.append(Event(kind="merge", at=trap, seg=seg(e["seg"], near=trap),
                                meta=meta, **common))
        elif kind == "Move":
            src_raw, dst_raw = str(e["source_seg"]), str(e["dest_seg"])
            here = on_seg.get(common["ions"][0])
            cands = sorted({v for (sid, _), v in name_of.items() if sid == src_raw})
            src = here if here in cands else seg(src_raw)
            dcands = [c for c in sorted({v for (sid, _), v in name_of.items() if sid == dst_raw})
                      if c != src and set(dev.segments[c]) & set(dev.segments[src])]
            if len(dcands) > 1:
                ahead = next_touch(common["ions"][0], int(e["id"]))
                if ahead:
                    dcands = [c for c in dcands if (set(dev.segments[c]) - set(dev.segments[src])) & ahead]
            if len(dcands) != 1:
                raise ValueError(f"move {e['id']}: cannot resolve segment {dst_raw} from {src}")
            dst = dcands[0]
            on_seg[common["ions"][0]] = dst
            via = set(dev.segments[src]) & set(dev.segments[dst])
            if len(via) != 1:
                raise ValueError(f"move {e['id']}: {src} and {dst} share {sorted(via)}")
            events.append(Event(kind="move", src=src, dst=dst, via=next(iter(via)),
                                meta=meta, **common))
        else:
            raise ValueError(f"event {e['id']}: unknown QCCDSim event kind {kind!r}")
    chains = {f"T{k}": [ion(i) for i in v] for k, v in raw["initial_layout"].items()}
    for s in dev.sites:
        chains.setdefault(s, [])
    qubits = {str(q): ion(i) for q, i in (raw.get("qubit_to_ion") or {}).items()}
    source = {k: raw.get(k) for k in ("tool", "repo", "commit", "command", "config", "circuit_file")}
    return TimedSchedule(name=name or path.parent.name, device=dev, chains=chains,
                         events=events, qubits=qubits, source=source,
                         claims=dict(raw.get("metrics", {})))


_CX = re.compile(r"^\s*cx\s+(\w+)\[(\d+)\]\s*,\s*(\w+)\[(\d+)\]\s*;", re.I)


def qasm_cx(path: str | Path) -> list[tuple[str, str]]:
    """The circuit's `cx` gates as (control, target) qubit indices, in file order.

    QCCDSim's parser keeps only `cx` lines and numbers qubits by their index in the single
    register; this reads the same thing, so the checker compares like with like.
    """
    out: list[tuple[str, str]] = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        m = _CX.match(line)
        if m:
            out.append((m.group(2), m.group(4)))
    return out


# ----------------------------------------------------------------------------- Jones
#
# Jones et al. 2025 (arXiv 2510.23519), artifact github.com/scottjones03/PartIIProject: the
# run step dumps every primitive of `paralleliseOperationsWithBarriers` with its start and
# end, the components it locks (`involvedComponents`) and the chains after it.

_JONES_KIND = {"QubitReset": "reset", "Measurement": "measure", "XRotation": "gate1",
               "YRotation": "gate1", "TwoQubitMSGate": "gate", "GateSwap": "swap",
               "Split": "split", "Merge": "merge", "Move": "transit"}


def jones_device(raw: Mapping[str, Any]) -> PortGraph:
    """Traps, junctions and the segments between them.  A trap's ions are listed low end
    first along its chain axis; a segment leaving the HIGH end is our right port."""
    sites = {f"T{t['id']}": {"capacity": int(t["capacity"]), "ports": {},
                             "pos": t.get("pos"), "axis": t.get("chain_axis")}
             for t in raw["traps"]}
    nodes = {f"J{j['id']}": {"degree": j.get("degree"), "pos": j.get("pos")}
             for j in raw["junctions"]}
    segments: dict[str, tuple[str, str]] = {}
    name = lambda end: ("T" if end["node_type"] == "trap" else "J") + str(end["id"])
    for s in raw["segments"]:
        key = f"C{s['id']}"
        a, b = name(s["source"]), name(s["target"])
        segments[key] = (a, b)
        for end in (s["source"], s["target"]):
            if end["node_type"] == "trap" and end.get("trap_side"):
                sites[name(end)]["ports"][key] = "R" if end["trap_side"] == "high" else "L"
    return PortGraph(sites=sites, nodes=nodes, segments=segments)


def import_jones(path: str | Path, *, name: str | None = None) -> TimedSchedule:
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    dev = jones_device(raw["device"])
    events: list[Event] = []
    for e in raw["events"]:
        kind = e["kind"]
        common = dict(id=int(e["id"]), t0=float(e["t0_us"]), t1=float(e["t1_us"]))
        locks = [f"{c['type']}:{c['id']}" for c in e.get("involvedComponents", ())]
        meta = {"op": kind, "locks": sorted(set(locks))}
        if kind == "JunctionCrossing":
            seg, j = f"C{e['segment']}", f"J{e['junction']}"
            if e["direction"] == "segment->junction":
                events.append(Event(kind="j_enter", ions=(str(e["ion"]),), src=seg, via=j,
                                    meta=meta, **common))
            else:
                events.append(Event(kind="j_exit", ions=(str(e["ion"]),), via=j, dst=seg,
                                    meta=meta, **common))
            continue
        k = _JONES_KIND.get(kind)
        if k is None:
            raise ValueError(f"event {e['id']}: unknown Jones op {kind!r}")
        if k in ("split", "merge"):
            events.append(Event(kind=k, ions=(str(e["ion"]),), at=f"T{e['trap']}",
                                seg=f"C{e['segment']}", meta=meta, **common))
        elif k == "transit":
            events.append(Event(kind=k, ions=(str(e["ion"]),), seg=f"C{e['segment']}",
                                meta=meta, **common))
        else:
            ions = tuple(str(i) for i in e.get("ions", ()))
            events.append(Event(kind=k, ions=ions, at=f"T{e['trap']}", meta=meta, **common))
    chains = {f"T{k}": [str(i) for i in v] for k, v in raw["initial_layout"].items()}
    for s in dev.sites:
        chains.setdefault(s, [])
    qubits = {str(q): str(i) for q, i in raw.get("qubit_to_ion", {}).items()}
    source = {k: raw.get(k) for k in ("tool", "repo", "commit", "command", "config", "circuit_file")}
    return TimedSchedule(name=name or path.parent.name, device=dev, chains=chains,
                         events=events, qubits=qubits, source=source,
                         claims=dict(raw.get("metrics", {})))


def stim_cx(path: str | Path) -> list[tuple[str, str]]:
    """The `CX` targets of a stim circuit, pairwise, in order (REPEAT blocks unrolled)."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()

    def walk(i: int, reps: int) -> tuple[list[tuple[str, str]], int]:
        body: list[tuple[str, str]] = []
        while i < len(lines):
            ln = lines[i].split("#")[0].strip()
            i += 1
            if not ln:
                continue
            if ln.startswith("REPEAT"):
                n = int(ln.split()[1])
                inner, i = walk(i, n)
                body += inner
                continue
            if ln.startswith("}"):
                return body * reps, i
            op, *args = ln.split()
            if op in ("CX", "CNOT", "ZCX"):
                qs = [a for a in args if not a.startswith("rec")]
                body += [(qs[k], qs[k + 1]) for k in range(0, len(qs), 2)]
        return body * reps, i

    return walk(0, 1)[0]


# --------------------------------------------------------------------------- Cyclone
#
# Khan et al., HPCA 2026 (arXiv 2511.15910), artifact github.com/sahilkhan123/Cyclone.  The
# compiler prices a lockstep step as  L*g  (L gate layers in the busiest trap)  + 3*g*A
# (a gate swap per ancilla, A ancillas per trap)  + s  (split 80 + move 5 + merge 80 = 165),
# and moves every ancilla one trap clockwise.  It records, per step, the trap lists before
# the gates and the CX layers; that is enough to write each primitive as a timed event.


def cyclone_device(raw: Mapping[str, Any]) -> PortGraph:
    """`test_machines.make_circle_machine_length_n`: x traps, a degree-2 junction between
    neighbours; segment q joins T_q's right end to J_q, segment x+q joins T_q's left end to
    J_(q-1)."""
    sites = {f"T{t['id']}": {"capacity": int(t["capacity"]), "ports": {}}
             for t in raw["traps"]}
    nodes = {f"J{j}": {} for j in range(int(raw["n_junctions"]))}
    segments: dict[str, tuple[str, str]] = {}
    for s in raw["segments"]:
        key = f"S{s['id']}"
        segments[key] = tuple(s["endpoints"])
        side = s.get("trap_side") or {}
        if side:
            sites[f"T{side['trap']}"]["ports"][key] = side["side"]
    return PortGraph(sites=sites, nodes=nodes, segments=segments)


def import_cyclone(path: str | Path, *, name: str | None = None) -> TimedSchedule:
    """Rebuild a Cyclone compile (the run step's `schedule.json`) as timed primitives.

    Within a step: gate layer j of the step runs at  t0 + j*g  in every trap that has a gate
    in that layer; then every ancilla's gate swap and split (3*g*A + 80, as one split that
    carries its reorder, QCCDSim style), its move across the degree-2 junction (5) and its
    merge into the next trap (80).  The H layers at either end are one global 100 us event,
    which is how the compiler charges them.
    """
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    dev = cyclone_device(raw["device"])
    x = len(dev.sites)
    cfg = raw["config"]
    sm = float(cfg["mparams"]["split_merge_time"])
    mv = float(cfg["mparams"]["shuttle_time"])
    events: list[Event] = []
    n = int(cfg["n"])
    # the step records number qubits as the compiler does: data 0..n-1, ancillas from n
    name_of = lambda i: str(i) if isinstance(i, str) else (f"d{i}" if int(i) < n else f"a{i}")

    def add(**kw) -> None:
        events.append(Event(id=len(events), **kw))

    for st in raw["events"]:
        if st["kind"] == "single_qubit_gates_parallelized":
            add(kind="gate1", t0=float(st["t0"]), t1=float(st["t1"]), ions=(),
                meta={"op": "H layer, charged once for all qubits", "note": st.get("note", "")})
            continue
        if st["kind"] != "cyclone_step":
            raise ValueError(f"unknown Cyclone record {st['kind']!r}")
        t0, g, A = float(st["t0"]), float(st["gcost"]), int(st["max_ancilla"])
        if A != 1:
            raise NotImplementedError("more than one ancilla per trap: the group move is not modelled yet")
        mapping = {trap: [name_of(i) for i in ions]
                   for trap, ions in st["current_mapping (before gates)"].items()}
        where = {ion: f"T{trap}" for trap, ions in mapping.items() for ion in ions}
        x_phase = st["phase"] == "X"
        for j, layer in enumerate(st["cx_timing_temp_layer"]):
            for data, anc in layer:
                d, a = name_of(data), name_of(anc)
                ions = (a, d) if x_phase else (d, a)      # CX(control, target)
                add(kind="gate", t0=t0 + j * g, t1=t0 + (j + 1) * g, ions=ions, at=where[d],
                    meta={"op": "CX", "phase": st["phase"], "step": st["time_array_index"]})
        ts = t0 + float(st["layer_gcost"])
        swap = 3 * g * A
        for trap, ions in mapping.items():
            q = int(trap)
            for anc in (i for i in ions if i.startswith("a")):
                nxt = (q + 1) % x
                add(kind="split", t0=ts, t1=ts + swap + sm, ions=(anc,), at=f"T{q}", seg=f"S{q}",
                    swap={"kind": "gate"} if swap else None,
                    meta={"op": "gate swap + split", "swap_us": swap})
                add(kind="move", t0=ts + swap + sm, t1=ts + swap + sm + mv, ions=(anc,),
                    src=f"S{q}", dst=f"S{x + nxt}", via=f"J{q}")
                add(kind="merge", t0=ts + swap + sm + mv, t1=ts + swap + 2 * sm + mv, ions=(anc,),
                    at=f"T{nxt}", seg=f"S{x + nxt}")
        if abs(ts + swap + 2 * sm + mv - float(st["t1"])) > 1e-6:
            raise ValueError(f"step {st['time_array_index']}: rebuilt end {ts + swap + 2 * sm + mv} "
                             f"!= recorded {st['t1']}")
    chains = {f"T{k}": [str(i) for i in v] for k, v in raw["initial_layout"].items()}
    source = {k: raw.get(k) for k in ("tool", "repo", "commit", "command", "config", "config_name")}
    return TimedSchedule(name=name or path.parent.name, device=dev, chains=chains,
                         events=events, source=source, claims=dict(raw.get("metrics", {})))


def checks_as_scheduled(true_pairs: Sequence[tuple[str, str]],
                        gate_pairs: Sequence[tuple[str, str]]) -> tuple[list[tuple[str, str]], list[str]]:
    """A syndrome round whose checks may sit on any ancilla.

    ``true_pairs`` is the round as a compiler emits it (X checks as CX(ancilla, data), then Z
    checks as CX(data, ancilla)); ``gate_pairs`` are the schedule's two-qubit gates.  Which
    ancilla measures which check is the compiler's choice -- every ancilla is reset before
    it is used -- so the data each ancilla touches in each phase, read off the schedule, must
    be exactly the true rows, one row per ancilla.  Returns the true round with each row on
    the ancilla the schedule gives it (rows are moved, never changed), and the reasons it is
    not a bijection (empty when it is).
    """
    def rows(pairs):
        out: dict[str, dict[str, list[str]]] = {"X": defaultdict(list), "Z": defaultdict(list)}
        for c, t in pairs:
            if c.startswith("a"):
                out["X"][c].append(t)
            else:
                out["Z"][t].append(c)
        return out

    true, got = rows(true_pairs), rows(gate_pairs)
    circ: list[tuple[str, str]] = []
    why: list[str] = []
    for ph in ("X", "Z"):
        want = Counter(frozenset(r) for r in true[ph].values())
        have = Counter(frozenset(r) for r in got[ph].values())
        if want != have:
            why.append(f"{ph} phase: {sum((have - want).values())} of the schedule's ancillas "
                       "touch a set of data that is no true check")
        pool: dict[frozenset, list[list[str]]] = defaultdict(list)
        for r in true[ph].values():
            pool[frozenset(r)].append(r)
        for a in sorted(got[ph], key=lambda x: int(x[1:])):
            left = pool.get(frozenset(got[ph][a]))
            row = left.pop() if left else got[ph][a]
            circ += [(a, d) for d in row] if ph == "X" else [(d, a) for d in row]
    return circ, why


def cyclone_circuit(checks: Mapping[str, Any], hx: list[list[int]], hz: list[list[int]],
                    ) -> list[tuple[str, str]]:
    """The syndrome round Cyclone means to run, from the TRUE check matrices: every X check
    as CX(ancilla -> data), then every Z check as CX(data -> ancilla), with check i on the
    ancilla the compiler gives it (`check_to_ancilla`: check i -> ancillas[i % len])."""
    ancillas = sorted(checks["X"], key=int)
    out: list[tuple[str, str]] = []
    for i, row in enumerate(hx):
        a = f"a{ancillas[i % len(ancillas)]}"
        out += [(a, f"d{q}") for q in row]
    for i, row in enumerate(hz):
        a = f"a{ancillas[i % len(ancillas)]}"
        out += [(f"d{q}", a) for q in row]
    return out
