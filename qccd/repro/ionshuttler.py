"""MQT IonShuttler (Schoenberger, Hillmich, Brandl, Wille) in the timed checker.

One artifact, two tools, both run by us at commit bc71e46 with a patch that only writes out
what they computed (``Reproduce/schoenberger2024/artifact/patches``):

* the exact SAT shuttler of arXiv 2311.03454 (ASP-DAC 2024, "P1"): ``run.py`` / ``SAT.py``;
* the cycle heuristic of arXiv 2402.14065 (IEEE TCAD, "P2"): ``run_heuristic.py`` /
  ``scheduling.py`` / ``Cycles.py``.

Both tools see the device as a graph whose EDGES are ion-chain sites and whose NODES are
junctions or the separators between neighbouring sites of one linear region, and both count
abstract time steps; there is no microsecond anywhere in either paper.  Every unit below is a
time step.

How a dump is written down (`import_ionshuttler`):

    device   every tool edge is a site with the tool's capacity: 1, SAT's inbound edge e_in
             (its gate site) 2, the heuristic's parking edge (its gate site) 3.  Every tool
             node is a node; a segment ``"<edge>@<node>"`` joins each edge to each of its two
             endpoint nodes.  A site carries its ``role`` (memory, exit, gate, entry) and a
             node whether the tool counts it as a junction.
    a move   one tool step is the unit [a, a+1).  A chain that goes from edge A through nodes
             n1..nk (and the edges C1..Ck-1 between them) to edge B in that step is: a
             ``split`` off A [a, a+1/4); the passage [a+1/4, a+3/4), split evenly into one
             ``move`` per node and a ``merge`` + ``split`` through each intermediate edge; a
             ``merge`` into B [a+3/4, a+1).  A split releases A when it ends and a merge takes B
             when it starts, so a chain may move into an edge another chain leaves in the same
             step -- the cycle rotations of the heuristic and the trains of the SAT optima --
             while every node passed is held inside the step.
    gates    a sequence element (SAT) or a gate step (heuristic) is a ``gate1`` (one chain) or
             ``gate`` (two) at the gate site, an INSTANT at the end of its step's unit, where
             the chains stand in that step's positions.  P1 gives a gate no duration (a chain
             present on e_in at a time step executes it); in P2's code the gate counter of an
             iteration advances when its chains are on the parking edge after that iteration's
             moves, so each iteration a gate is counted in is one instant, and the number of
             such instants is the gate's time (1 for one qubit, 3 for two).

The clocks.  The tools count differently and the replay keeps each count, so that the
replayed makespan (the last event's end) IS the printed number:

    SAT        prints the number of STATES, t = 0..N-1 (``for timesteps in range(2, ...)``;
               ``Found satisfying solution with N time steps``): N-1 transitions.  State t is
               the unit [t, t+1); the transition from state t-1 fills it, so state t's
               positions hold at t+1, where its sequence element executes.  State 0 has no
               incoming transition, so [0, 1) is empty and the makespan is N.
    heuristic  prints the 0-based index of its LAST ITERATION (``Circuit successfully
               executed in N time steps``, the loop counter): N+1 iterations.  Iteration t is
               the unit [t-1, t), its gate step the instant t.  Iteration 0 runs in [-1, 0),
               so the makespan is N and the schedule lasts N+1 units.

The two printed numbers are therefore not in the same units: N states are N-1 transitions,
and N iterations-from-zero are N+1.

Rules (MODEL.md in ``Reproduce/schoenberger2024`` quotes the source of each):

    core     capacity (1 / e_in 2 / parking 3), positions, one operation per chain at a time,
             durations (`ionshuttler_duration`, strict); for the SAT also one chain per node and
             per segment at a time (``junction_mutex``, ``segment_mutex``)
    ours     `ionshuttler_rules`: layout (the representation above is honest), junctions (at
             most one per move), path (the edges a move passes are empty at the previous
             state), nodes (one chain per node per step; the heuristic's code exempts its PZ
             node), pz (one-way processing zone; SAT's is empty at start and end), gates (at the
             gate site only; one at a time; the heuristic's step counts), sequence (SAT: the
             fixed order, each element once; heuristic: free order, each gate once), and
             sequence_code (the SAT code's stricter conditions on e_in)

The heuristic's core profile shares nodes and segments because its code lets one chain enter
the parking edge while another leaves it through the PZ node in the same step; the core's
mutexes cannot exempt one node, so `ionshuttler_rules` checks every other node itself.

Ours (`schedule_ours`): a schedule of our own for each of the heuristic's paper runs, under
the heuristic's model only (its device, its placement, its rules above), written in its dump
format and judged by the same `check_ionshuttler`.  Every one meets `lower_bound`, so it is
optimal in that model.  It is not compared with the SAT runs, whose model and count differ.

``python -m qccd.repro.ionshuttler Reproduce/schoenberger2024`` replays every schedule in
``artifact/``, writes our schedules to ``ours/`` and writes ``results.json``.
"""

from __future__ import annotations

import dataclasses
import gzip
import json
import math
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from .timed import Context, Event, PortGraph, Profile, Report, TimedSchedule, Violation, check

__all__ = [
    "SAT", "HEURISTIC", "PRINTED", "CLOCK_OFFSET", "IONSHUTTLER_SAT", "IONSHUTTLER_HEURISTIC",
    "PROFILES", "ionshuttler_duration", "parse_qasm", "import_ionshuttler", "ionshuttler_rules",
    "check_ionshuttler", "lower_bound", "schedule_ours", "RULES", "CONFIGS", "PAPER_META", "reproduce",
]

SAT, HEURISTIC = "sat", "heuristic"

#: the dump's name for the number the tool printed, and the console line that printed it
PRINTED = {SAT: "Found satisfying solution with N time steps",
           HEURISTIC: "Circuit successfully executed in N time steps"}
_STDOUT = {SAT: re.compile(r"Found satisfying solution with (\d+) time steps"),
           HEURISTIC: re.compile(r"Circuit successfully executed in (\d+) time steps")}

#: step k of the tool is the unit [k + offset, k + offset + 1) (module docstring, "The clocks")
CLOCK_OFFSET = {SAT: 0, HEURISTIC: -1}

#: a move's unit: leaving the edge, passing the node(s), arriving
LEAVE, PASS, ARRIVE = 0.25, 0.5, 0.25
_TOL = 1e-9

#: P1's rules for the core checker: one chain per node (P1 Sec. IV.C, "we limit the number of
#: ion chains that are allowed to pass over a node to 1") and so per segment; capacities are in
#: the device.  Gates are instants; chain order inside a site does not arise (one chain per edge,
#: two on e_in without an order).
IONSHUTTLER_SAT = Profile("ionshuttler_sat", segment_mutex=True, junction_mutex=True,
                          chain_order=None, circuit_order="strict")
#: P2's code lets two chains pass its PZ node in one step (Cycles.py l.374); the core's mutexes
#: cannot exempt one node, so they are off and `ionshuttler_rules` "nodes" checks the rest.
IONSHUTTLER_HEURISTIC = Profile("ionshuttler_heuristic", segment_mutex=False, junction_mutex=False,
                                chain_order=None, circuit_order="strict")
PROFILES = {SAT: IONSHUTTLER_SAT, HEURISTIC: IONSHUTTLER_HEURISTIC}


def ionshuttler_duration(e: Event, ctx: Context | None = None) -> float | None:
    """One transition is one time step: 1/4 leaving the edge, 1/2 passing the node(s), split
    evenly over the ``3k - 2`` passage events of a k-node move, 1/4 arriving.  Gates are
    instants.  Anything else is not an operation of this model (None)."""
    if e.kind in ("gate", "gate1"):
        return 0.0
    role = e.meta.get("role")
    if e.kind == "split" and role == "leave":
        return LEAVE
    if e.kind == "merge" and role == "arrive":
        return ARRIVE
    if e.kind in ("split", "merge", "move") and role == "pass":
        k = int(e.meta.get("k", 0))
        if k >= 1:
            return PASS / (3 * k - 2)
    return None


# ------------------------------------------------------------------------------ import


def _load(src: str | Path | Mapping) -> dict:
    if isinstance(src, Mapping):
        return json.loads(json.dumps(src))
    p = Path(src)
    data = p.read_bytes()
    if p.suffix == ".gz":
        data = gzip.decompress(data)
    return json.loads(data.decode("utf-8"))


def _tool_of(d: Mapping) -> str:
    m = d.get("metrics", {})
    for tool, key in PRINTED.items():
        if key in m:
            return tool
    raise ValueError("not an IonShuttler dump: its metrics name neither tool's printed number")


def parse_qasm(text: str) -> list[tuple[str, tuple[int, ...]]]:
    """The gates of an OpenQASM 2 file as (name, qubits); measurements and barriers are not
    gates (P2 Sec. VII.A counts neither)."""
    ops = []
    for line in text.splitlines():
        s = line.split("//")[0].strip()
        if not s or s.startswith(("OPENQASM", "include", "qreg", "creg", "barrier", "measure")):
            continue
        m = re.match(r"^([A-Za-z_]\w*)\s*(?:\(([^)]*)\))?\s+(.+?);$", s)
        if not m:
            raise ValueError(f"cannot read QASM line {line!r}")
        ops.append((m.group(1), tuple(int(x) for x in re.findall(r"q\[(\d+)\]", m.group(3)))))
    return ops


def _chain(i: Any) -> str:
    return f"c{int(i)}"


def _memory_side(edge: tuple[str, str], other: set[str]) -> str:
    """The endpoint of a PZ-path edge that is not shared with ``other``."""
    rest = [n for n in edge if n not in other]
    if len(rest) != 1:
        raise ValueError(f"cannot tell the memory side of {edge}")
    return rest[0]


def import_ionshuttler(src: str | Path | Mapping, *, name: str | None = None,
                       circuit: Sequence[tuple[str, Sequence[int]]] | None = None,
                       circuits_dir: str | Path | None = None) -> TimedSchedule:
    """A dumped SAT or heuristic schedule (``schedule.json`` or ``.json.gz``) as a
    `TimedSchedule` in the representation of the module docstring.

    The heuristic's circuit is read from ``circuit`` or from the dump's ``circuit_file`` in
    ``circuits_dir`` (or next to ``src``); the SAT's sequence is in the dump.  Only the moves
    and the initial placement are used: the dump's per-step positions are what the replay
    must reproduce, not an input to it."""
    d = _load(src)
    tool = _tool_of(d)
    g = d["graph"]
    ntype = {n["id"]: n["node_type"] for n in g["nodes"]}
    pz = [n for n, t in ntype.items() if t == "processing_zone_node"]
    if len(pz) != 1:
        raise ValueError(f"expected one processing-zone node, found {pz}")
    pz_node = pz[0]
    ends = {e["id"]: tuple(e["nodes"]) for e in g["edges"]}
    if tool == SAT:
        junctions = set(g["junction_nodes"])
        exit_path, entry_path = [g["exit_edge(e_out)"]], []
        gate_site = g["entry_edge(e_in, gate site)"]
        exit_node = _memory_side(ends[exit_path[0]], {pz_node})
        entry_node = _memory_side(ends[gate_site], {pz_node})
    else:
        # Cycles.py l.56-63: junction_node, exit_node, exit_connection_node, entry_node,
        # entry_connection_node; plus the PZ node (find_nonfree_and_free_circle_idxs l.302)
        junctions = set(g["junction_nodes(MemoryZone.junction_nodes)"]) | {pz_node}
        exit_path = list(g["path_to_pz(exit)"])
        entry_path = list(g["path_from_pz(entry)"])
        gate_site = g["parking_edge(gate site)"]
        nxt = set(ends[exit_path[1]]) if len(exit_path) > 1 else {pz_node}
        exit_node = _memory_side(ends[exit_path[0]], nxt)
        prv = set(ends[entry_path[-2]]) if len(entry_path) > 1 else {pz_node}
        entry_node = _memory_side(ends[entry_path[-1]], prv)

    role: dict[str, tuple[str, int]] = {}
    for e in g["edges"]:
        if e["edge_type"] == "trap":
            role[e["id"]] = ("memory", 0)
    for i, e in enumerate(exit_path):
        role[e] = ("exit", i)
    for i, e in enumerate(entry_path):
        role[e] = ("entry", i)
    role[gate_site] = ("gate", 0)
    missing = [e for e in ends if e not in role]
    if missing:
        raise ValueError(f"edges with no role: {missing}")

    sites: dict[str, dict] = {}
    segments: dict[str, tuple[str, str]] = {}
    for e in g["edges"]:
        eid = e["id"]
        a, b = ends[eid]
        ports = {f"{eid}@{a}": "L", f"{eid}@{b}": "R"}
        for s, n in ((f"{eid}@{a}", a), (f"{eid}@{b}", b)):
            segments[s] = (eid, n)
        sites[eid] = {"capacity": int(e["capacity"]), "edge_type": e["edge_type"],
                      "role": role[eid][0], "order": role[eid][1], "nodes": [a, b], "ports": ports}
    nodes = {n: {"node_type": t, "junction": n in junctions, "pz": n == pz_node} for n, t in ntype.items()}
    dev = PortGraph(sites=sites, nodes=nodes, segments=segments)
    between = {frozenset(v): k for k, v in ends.items()}

    q2i = {str(q): str(i) for q, i in d.get("qubit_to_ion", {}).items()}
    ion_of_q = lambda q: _chain(q2i.get(str(q), str(q)))

    off = CLOCK_OFFSET[tool]
    raw: list[tuple[float, int, int, str, float, dict]] = []    # (t0, prio, seq, kind, t1, kwargs)

    def add(kind: str, t0: float, t1: float, prio: int = 1, **kw) -> None:
        raw.append((t0, prio, len(raw), kind, t1, kw))

    n_moves = 0
    completed = 0
    for k, s in enumerate(d["steps"]):
        if int(s.get("t", k)) != k:
            raise ValueError(f"step {k} is labelled t={s.get('t')}")
        a = k + off
        for m in s.get("moves", []):
            ion = _chain(m["ion"])
            route = list(m["through_nodes"])
            if not route:
                raise ValueError(f"step {k}: a move of {ion} passes no node")
            K = len(route)
            walk = [m["from"]]
            for x, y in zip(route, route[1:]):
                walk.append(between[frozenset((x, y))])
            walk.append(m["to"])
            P = 3 * K - 2
            at = lambda j: a + LEAVE + PASS * j / P
            base = {"step": k, "move": n_moves, "k": K}
            info = {"from": m["from"], "to": m["to"], "through_nodes": route}
            for key in ("required_empty_at_prev_state", "phase"):
                if key in m:
                    info[key] = m[key]
            add("split", a, a + LEAVE, ions=(ion,), at=walk[0], seg=f"{walk[0]}@{route[0]}",
                meta={**base, "role": "leave", **info})
            j = 0
            for i, node in enumerate(route):
                add("move", at(j), at(j + 1), ions=(ion,), src=f"{walk[i]}@{node}",
                    dst=f"{walk[i + 1]}@{node}", via=node, meta={**base, "role": "pass"})
                j += 1
                if i < K - 1:
                    c = walk[i + 1]
                    add("merge", at(j), at(j + 1), ions=(ion,), at=c, seg=f"{c}@{node}",
                        meta={**base, "role": "pass"})
                    add("split", at(j + 1), at(j + 2), ions=(ion,), at=c, seg=f"{c}@{route[i + 1]}",
                        meta={**base, "role": "pass"})
                    j += 2
            add("merge", a + LEAVE + PASS, a + 1, ions=(ion,), at=walk[-1],
                seg=f"{walk[-1]}@{route[-1]}", meta={**base, "role": "arrive"})
            n_moves += 1
        x = a + 1                                         # the end of this step's unit
        if tool == SAT:
            for elem, j in zip(s.get("gates", []), s.get("gate_seq_index", [])):
                ions = tuple(_chain(i) for i in elem)
                add("gate1" if len(ions) == 1 else "gate", x, x, prio=0, ions=ions, at=gate_site,
                    meta={"state": k, "seq_index": int(j)})
        elif s.get("gates"):
            gd = s["gate_detail"]
            qs = [int(q) for q in gd["qubits"]]
            ions = tuple(ion_of_q(q) for q in qs)
            op = gd.get("op") or [None, []]
            add("gate1" if len(ions) == 1 else "gate", x, x, prio=0, ions=ions, at=gate_site,
                meta={"iteration": k, "op": op[0], "params": list(op[1]) if len(op) > 1 else [],
                      "qubits": qs, "step_of_gate": int(gd["step_of_gate"]),
                      "duration": int(gd["duration"]), "completed": bool(gd["completed_this_step"]),
                      "gate": completed})
            if gd["completed_this_step"]:
                completed += 1

    raw.sort(key=lambda r: (r[0], r[1], r[2]))
    events = [Event(i, kind, float(t0), float(t1), **kw) for i, (t0, _, _, kind, t1, kw) in enumerate(raw)]

    chains: dict[str, list[str]] = defaultdict(list)
    for ion, site in sorted(d["initial"].items(), key=lambda x: int(x[0])):
        chains[site].append(_chain(ion))

    cfg = d.get("config", {})
    src_info: dict[str, Any] = {
        "tool": tool, "repo": d.get("repo"), "commit": d.get("commit"), "command": d.get("command"),
        "config": cfg, "seed": d.get("seed", cfg.get("seed")), "placement_rule": d.get("placement_rule"),
        "time_units": d.get("time_units"), "circuit_file": d.get("circuit_file"),
        "gadget": {"gate_site": gate_site, "exit_path": exit_path, "entry_path": entry_path,
                   "pz_node": pz_node, "exit_node": exit_node, "entry_node": entry_node},
        "clock": {"offset": off, "unit": "one tool time step",
                  "convention": ("state t = unit [t, t+1); makespan = number of states" if tool == SAT
                                 else "iteration t = unit [t-1, t); makespan = 0-based index of the "
                                      "last iteration")},
    }
    if tool == SAT:
        src_info["sequence"] = [[_chain(i) for i in elem] for elem in d["sequence"]]
    else:
        ops = None
        if circuit is not None:
            ops = [(str(nm), tuple(int(q) for q in qs)) for nm, qs in circuit]
        else:
            cf = d.get("circuit_file")
            for base in [circuits_dir, None if isinstance(src, Mapping) else Path(src).parent]:
                if cf and base is not None and (Path(base) / Path(cf).name).exists():
                    ops = parse_qasm((Path(base) / Path(cf).name).read_text(encoding="utf-8"))
                    break
        src_info["circuit"] = [[nm, list(qs)] for nm, qs in ops] if ops is not None else None
        src_info["gate_times"] = {"1q": int(cfg.get("time_1qubit_gate", 1)),
                                  "2q": int(cfg.get("time_2qubit_gate", 3))}
    printed = int(d["metrics"][PRINTED[tool]])
    last = d["steps"][-1].get("positions") if d["steps"] else None
    claims = {"printed": printed, "label": PRINTED[tool], "metrics": d["metrics"]}
    if last:
        claims["final_positions"] = {_chain(i): e for i, e in last.items()}
    if name is None:
        name = Path(src).name.split(".")[0] if not isinstance(src, Mapping) else d.get("name", tool)
    return TimedSchedule(
        name=name, device=dev, chains=dict(chains), events=events,
        qubits={q: _chain(i) for q, i in q2i.items()}, source=src_info,
        claims=claims)


# ------------------------------------------------------------------------------- rules


@dataclasses.dataclass
class _Route:
    ion: str
    step: int
    A: str
    nodes: tuple[str, ...]
    inter: tuple[str, ...]
    B: str
    leave: Event

    @property
    def walk(self) -> list[str]:
        return [self.A, *self.inter, self.B]


def _route(es: list[Event], a: float, segs: set) -> "_Route | str":
    """One chain's events in one unit, read back as a move; or why they are not one."""
    kinds = [e.kind for e in es]
    K = kinds.count("move")
    want = ["split", "move"] + ["merge", "split", "move"] * max(K - 1, 0) + ["merge"]
    if K < 1 or kinds != want:
        return f"its events {kinds} do not form one move (split, move, [merge, split, move]..., merge)"
    P = 3 * K - 2
    slots = [(a, a + LEAVE)] + [(a + LEAVE + PASS * j / P, a + LEAVE + PASS * (j + 1) / P)
                                for j in range(P)] + [(a + LEAVE + PASS, a + 1)]
    roles = ["leave"] + ["pass"] * P + ["arrive"]
    for e, (t0, t1), r in zip(es, slots, roles):
        if abs(e.t0 - t0) > 1e-6 or abs(e.t1 - t1) > 1e-6:
            return f"{e.kind} {e.id} occupies [{e.t0:g}, {e.t1:g}), the step's layout says [{t0:g}, {t1:g})"
        if e.meta.get("role") != r or int(e.meta.get("k", -1)) != K:
            return f"{e.kind} {e.id} is labelled {e.meta.get('role')!r} of a {e.meta.get('k')}-node move"
    nodes = [e.via for e in es if e.kind == "move"]
    inter = [e.at for e in es[1:-1] if e.kind == "merge"]
    walk = [es[0].at, *inter, es[-1].at]
    for i, n in enumerate(nodes):
        if n is None or frozenset((walk[i], n)) not in segs or frozenset((walk[i + 1], n)) not in segs:
            return f"its walk {walk} does not pass node {n} between {walk[i]} and {walk[i + 1]}"
    return _Route(es[0].ions[0], -1, es[0].at, tuple(nodes), tuple(inter), es[-1].at, es[0])


#: every rule `ionshuttler_rules` and the core check, with where it comes from (MODEL.md
#: quotes each in full)
RULES = [
    {"check": "capacity", "by": "core", "tools": "both",
     "statement": "an edge holds at most one chain; SAT's e_in two, the heuristic's parking edge three",
     "source": "P1 Sec. IV.B; SAT.py l.524 (MV_CONSTR_3); scheduling.py check_duplicates, run_heuristic.py max_chains_in_parking=3"},
    {"check": "junction_mutex / segment_mutex", "by": "core", "tools": "sat",
     "statement": "one chain in a node (and on a segment) at a time",
     "source": "P1 Sec. IV.C; SAT.py l.507 (MV_CONSTR_2_EXTRA_EXTRA)"},
    {"check": "layout", "by": "ours", "tools": "both",
     "statement": "each move fills exactly one step's unit (leave 1/4, pass 1/2, arrive 1/4); a chain moves at most once per step; gates are instants at the end of a step",
     "source": "the representation (module docstring); P1 Sec. III.A, P2 Sec. V.A: a time step is the unit of motion"},
    {"check": "junctions", "by": "ours", "tools": "both",
     "statement": "a move passes at most one junction",
     "source": "P1 Sec. IV.C, Eq. (1); P2 Sec. VI"},
    {"check": "path", "by": "ours", "tools": "both",
     "statement": "the edges a move passes between its start and its end are empty at the previous step",
     "source": "P1 Sec. IV.C, Eq. (1); SAT.py l.454-455; P2 Sec. VI. The SAT code's stricter path (RUN.md quirk) is reported, not checked"},
    {"check": "nodes", "by": "ours", "tools": "both",
     "statement": "at most one chain passes a node per step; the heuristic's PZ node is exempt",
     "source": "P1 Sec. IV.C; SAT.py l.507; P2 Sec. V.A, VI; Cycles.py l.374 (the exemption)"},
    {"check": "pz", "by": "ours", "tools": "both",
     "statement": "the processing zone is one-way; it is empty at the start; SAT: a chain on e_out is on e_in at the next step and came from an edge at the exit junction, e_in is entered only from e_out and left only to an edge at the entry junction, and the PZ is empty at the end",
     "source": "P1 Sec. IV.A, IV.C; SAT.py l.541, 561, 577, 596; P2 Sec. V.C; graph_utils.py GraphCreator paths; scheduling.py l.30, l.284"},
    {"check": "gates", "by": "ours", "tools": "both",
     "statement": "gates run only at the gate site (e_in / parking edge), one at a time; heuristic: a gate is counted in exactly its duration of iterations (1q 1, 2q 3), consecutively, completing on the last",
     "source": "P1 Sec. IV.D; SAT.py l.710; P2 Sec. VI; scheduling.py l.357, l.376"},
    {"check": "sequence", "by": "ours", "tools": "both",
     "statement": "SAT: the sequence's elements execute in its fixed order, each exactly once, at strictly increasing steps; heuristic: every gate of the circuit runs exactly once, in any order",
     "source": "P1 Sec. IV.D; P2 Sec. IV (Gate Selection), VI"},
    {"check": "sequence_code", "by": "ours", "tools": "sat",
     "statement": "a one-chain element executes alone on e_in, and no chain outside two consecutive elements is on e_in between them",
     "source": "SAT.py l.647-651, l.678 (code only, not in P1)"},
]


def ionshuttler_rules(sched: TimedSchedule, tool: str | None = None
                      ) -> tuple[dict[str, str], list[Violation], dict]:
    """The rules the core checker does not express (module docstring, `RULES`).  Reads only
    the schedule: its events, its PortGraph (site roles, junction flags) and ``source``
    (the gadget's nodes, the SAT sequence, the heuristic circuit and gate times).

    Returns (checks, violations, info); ``info`` carries counts worth reporting, such as how
    often the heuristic's PZ node was shared and the SAT code's stricter path requirement."""
    tool = tool or sched.source["tool"]
    dev = sched.device
    gad = sched.source["gadget"]
    off = CLOCK_OFFSET[tool]
    out: list[Violation] = []

    def v(name: str, msg: str, *ids: int) -> None:
        out.append(Violation(name, msg, tuple(i for i in ids if i is not None)))

    role = lambda s: dev.sites.get(s, {}).get("role")
    order = lambda s: dev.sites.get(s, {}).get("order", 0)
    segs = {frozenset(x) for x in dev.segments.values()}

    # -- layout: read every step back as routes and gate instants ---------------------
    groups: dict[tuple[str, int], list[Event]] = defaultdict(list)
    gates: list[tuple[int, Event]] = []
    for e in sorted(sched.events, key=lambda e: (e.t0, e.id)):
        if e.kind in ("gate", "gate1"):
            x = round(e.t0)
            if abs(e.t1 - e.t0) > _TOL or abs(e.t0 - x) > 1e-6:
                v("layout", f"{e.kind} {e.id} [{e.t0:g}, {e.t1:g}) is not an instant at the end of a step", e.id)
                continue
            k = x - 1 - off
            if k < 0:
                v("layout", f"{e.kind} {e.id} at {e.t0:g} lies before the tool's first step", e.id)
                continue
            gates.append((k, e))
            continue
        if e.kind not in ("split", "merge", "move") or len(e.ions) != 1:
            v("layout", f"event {e.id} ({e.kind} of {list(e.ions)}) is not part of a move", e.id)
            continue
        k = math.floor(e.t0 + 1e-6) - off
        groups[(e.ions[0], k)].append(e)
    routes: dict[tuple[str, int], _Route] = {}
    first_step = 1 if tool == SAT else 0
    for (ion, k), es in sorted(groups.items(), key=lambda x: (x[0][1], x[0][0])):
        if k < first_step:
            v("layout", f"{ion} moves in step {k}, before the tool's first transition "
              f"({'state 0 has no incoming transition' if tool == SAT else 'iteration 0'})",
              *[e.id for e in es])
            continue
        r = _route(es, k + off, segs)
        if isinstance(r, str):
            v("layout", f"{ion} in step {k}: {r}", *[e.id for e in es])
            continue
        r.step = k
        routes[(ion, k)] = r

    # -- positions before and after every step ----------------------------------------------------
    initial = {ion: site for site, ions in sched.chains.items() for ion in ions}
    kmax = max([k for _, k in routes] + [k for k, _ in gates] + [0])
    by_step: dict[int, list[_Route]] = defaultdict(list)
    for r in routes.values():
        by_step[r.step].append(r)
    before: dict[int, dict[str, str]] = {}
    after: dict[int, dict[str, str]] = {}
    pos = dict(initial)
    for k in range(0, kmax + 1):
        before[k] = dict(pos)
        for r in by_step[k]:
            pos[r.ion] = r.B
        after[k] = dict(pos)
    final = pos

    def holders(p: Mapping[str, str], site: str) -> list[str]:
        return sorted(i for i, s in p.items() if s == site)

    # -- junctions -------------------------------------------------------------------------
    for r in routes.values():
        js = [n for n in r.nodes if dev.nodes.get(n, {}).get("junction")]
        if len(js) > 1:
            v("junctions", f"{r.ion} passes {len(js)} junctions {js} in step {r.step}", r.leave.id)

    # -- path: the edges passed are empty at the previous step ------------------------------
    code_extra = code_extra_held = 0
    for r in routes.values():
        for c in r.inter:
            h = holders(before[r.step], c)
            if h:
                v("path", f"{r.ion} passes {c} in step {r.step}, which holds {h} at the previous step",
                  r.leave.id)
        req = r.leave.meta.get("required_empty_at_prev_state")
        if req is not None:
            extra = [c for c in req if c not in r.inter]
            if extra:
                code_extra += 1
                if not any(holders(before[r.step], c) for c in extra):
                    code_extra_held += 1

    # -- nodes: one chain per node per step (heuristic: PZ node exempt) -------------------------
    pz_shared: Counter[int] = Counter()
    for k, rs in by_step.items():
        use: dict[str, list[_Route]] = defaultdict(list)
        for r in rs:
            for n in r.nodes:
                use[n].append(r)
        for n, users in use.items():
            if len(users) < 2:
                continue
            if tool == HEURISTIC and dev.nodes.get(n, {}).get("pz"):
                pz_shared[len(users)] += 1
                continue
            v("nodes", f"node {n} is passed by {len(users)} chains in step {k}: "
              f"{sorted(u.ion for u in users)}", *[u.leave.id for u in users])

    # -- pz: one way, empty at the start (SAT also at the end) -----------------------------
    for ion, s in sorted(initial.items()):
        if role(s) != "memory":
            v("pz", f"{ion} starts on {s} ({role(s)}), not on a memory edge")
    n_exit = len(gad["exit_path"])

    def arc_ok(x: str, y: str) -> bool:
        rx, ry = role(x), role(y)
        if rx == "memory" and ry == "memory":
            return True
        if tool == SAT:
            return (rx, ry) in (("memory", "exit"), ("exit", "gate"), ("gate", "memory"))
        last_exit = order(x) == n_exit - 1
        n_entry = len(gad["entry_path"])
        return ((rx == "memory" and ry == "exit" and order(y) == 0)
                or (rx == ry == "exit" and order(y) == order(x) + 1)
                or (rx == "exit" and last_exit and ry == "gate")
                or (rx == "exit" and last_exit and ry == "entry" and order(y) == 0)
                or (rx == "gate" and ry == "entry" and order(y) == 0)
                or (rx == ry == "entry" and order(y) == order(x) + 1)
                or (rx == "entry" and order(x) == n_entry - 1 and ry == "memory"))

    for r in routes.values():
        w = r.walk
        for x, y in zip(w, w[1:]):
            if not arc_ok(x, y):
                v("pz", f"{r.ion} goes {x} ({role(x)}) -> {y} ({role(y)}) in step {r.step}: "
                  "not a direction of the one-way processing zone", r.leave.id)
        if tool == SAT:
            if role(r.B) == "exit" and (len(r.nodes) != 1 or gad["exit_node"] not in dev.sites[r.A]["nodes"]):
                v("pz", f"{r.ion} enters e_out {r.B} from {r.A}, not from an edge at the exit "
                  f"junction {gad['exit_node']} (SAT.py l.541)", r.leave.id)
            if role(r.A) == "gate" and (r.nodes != (gad["entry_node"],)):
                v("pz", f"{r.ion} leaves e_in to {r.B} through {list(r.nodes)}, not to a neighbour "
                  f"edge at the entry junction {gad['entry_node']} (P1 Sec. IV.C)", r.leave.id)
    if tool == SAT:
        for k in range(1, kmax + 1):
            for ion, s in sorted(before[k].items()):
                if role(s) == "exit":
                    r = routes.get((ion, k))
                    if r is None or role(r.B) != "gate":
                        v("pz", f"{ion} is on e_out {s} at state {k - 1} and not on e_in at state {k}",
                          r.leave.id if r else None)
        for ion, s in sorted(final.items()):
            if role(s) in ("exit", "gate"):
                v("pz", f"{ion} ends on {s} ({role(s)}): the PZ must be empty in the last state "
                  "(SAT.py l.596)")

    # -- gates ------------------------------------------------------------------------------
    gates.sort(key=lambda kg: (kg[1].t0, kg[1].id))
    for k, e in gates:
        if role(e.at) != "gate":
            v("gates", f"{e.kind} {e.id} on {list(e.ions)} at {e.at} ({role(e.at)}), not at the gate "
              f"site {gad['gate_site']}", e.id)
        h = holders(after[k], gad["gate_site"])
        if not set(e.ions) <= set(h):
            v("gates", f"{e.kind} {e.id}: {list(e.ions)} not all on the gate site in step {k} ({h})", e.id)
    per_instant = Counter(k for k, _ in gates)
    for k, c in per_instant.items():
        if c > 1:
            v("gates", f"{c} gates in step {k}: one processing zone runs one at a time",
              *[e.id for kk, e in gates if kk == k])
    if tool == HEURISTIC:
        gt = sched.source.get("gate_times", {"1q": 1, "2q": 3})
        by_gate: dict[Any, list[tuple[int, Event]]] = defaultdict(list)
        for k, e in gates:
            by_gate[e.meta.get("gate")].append((k, e))
        spans = []
        for gi, kes in by_gate.items():
            kes.sort(key=lambda x: x[0])
            d = gt["1q"] if len(kes[0][1].ions) == 1 else gt["2q"]
            ks = [k for k, _ in kes]
            done = [e.meta.get("completed") for _, e in kes]
            if len(kes) != d or ks != list(range(ks[0], ks[0] + len(ks))) or done != [False] * (d - 1) + [True]:
                v("gates", f"gate {gi} on {list(kes[0][1].ions)} is counted in steps {ks} "
                  f"(completed {done}); its time is {d} consecutive step(s), completing on the last",
                  *[e.id for _, e in kes])
            if len({tuple(e.ions) for _, e in kes}) != 1:
                v("gates", f"gate {gi} changes its chains between steps", *[e.id for _, e in kes])
            spans.append((ks[0], ks[-1], gi))
        spans.sort()
        for (a0, a1, ga), (b0, b1, gb) in zip(spans, spans[1:]):
            if b0 <= a1:
                v("gates", f"gates {ga} and {gb} overlap (steps {a0}-{a1} and {b0}-{b1})")

    # -- sequence ---------------------------------------------------------------------------
    info: dict[str, Any] = {}
    if tool == SAT:
        want = [frozenset(x) for x in sched.source["sequence"]]
        got = [(k, e) for k, e in gates]
        seq_got = [frozenset(e.ions) for _, e in got]
        if seq_got != want:
            i = next((i for i, (a, b) in enumerate(zip(seq_got, want)) if a != b), min(len(seq_got), len(want)))
            v("sequence", f"executed {len(seq_got)} elements, the sequence has {len(want)}; first "
              f"difference at position {i}: {sorted(seq_got[i]) if i < len(seq_got) else None} "
              f"against {sorted(want[i]) if i < len(want) else None}")
        for j, (k, e) in enumerate(got):
            if e.meta.get("seq_index") is not None and int(e.meta["seq_index"]) != j:
                v("sequence", f"the element executed {j}th is labelled sequence index "
                  f"{e.meta['seq_index']}", e.id)
        for (ka, ea), (kb, eb) in zip(got, got[1:]):
            if kb <= ka:
                v("sequence", f"elements at states {ka} and {kb}: not strictly increasing", ea.id, eb.id)
        info["executed_states"] = [k for k, _ in got]
        # the code's stricter conditions on e_in
        gs = gad["gate_site"]
        for k, e in got:
            if len(e.ions) == 1 and holders(after[k], gs) != sorted(e.ions):
                v("sequence_code", f"one-chain element {list(e.ions)} at state {k} shares e_in with "
                  f"{sorted(set(holders(after[k], gs)) - set(e.ions))} (SAT.py l.647)", e.id)
        for (ka, ea), (kb, eb) in zip(got, got[1:]):
            allowed = set(ea.ions) | set(eb.ions)
            for s in range(ka, kb):
                stray = sorted(set(holders(after[s], gs)) - allowed)
                if stray:
                    v("sequence_code", f"{stray} on e_in at state {s}, between elements {sorted(ea.ions)} "
                      f"(state {ka}) and {sorted(eb.ions)} (state {kb}) (SAT.py l.678)", ea.id, eb.id)
                    break
    else:
        circ = sched.source.get("circuit")
        done = [(k, e) for k, e in gates if e.meta.get("completed")]
        info["gate_order"] = [[int(i[1:]) for i in e.ions] for _, e in done]
        if circ is not None:
            to_ion = lambda q: sched.qubits.get(str(q), _chain(q))
            # without Gate Selection the code walks a list of qubit tuples and never names the
            # operation (op None in the dump): those gates are matched on their chains alone
            named = all(e.meta.get("op") is not None for _, e in done)
            info["gates_matched_on"] = "name and chains" if named else "chains only (the dump names no operation)"
            key = (lambda nm, ions: (str(nm), ions)) if named else (lambda nm, ions: ions)
            want_ms = Counter(key(nm, tuple(sorted(to_ion(q) for q in qs))) for nm, qs in circ)
            got_ms = Counter(key(e.meta.get("op"), tuple(sorted(e.ions))) for _, e in done)
            if want_ms != got_ms:
                miss, extra = want_ms - got_ms, got_ms - want_ms
                v("sequence", f"gates run differ from the circuit: {sum(miss.values())} missing "
                  f"(e.g. {list(miss)[:3]}), {sum(extra.values())} extra or repeated (e.g. {list(extra)[:3]})")

    names = ["layout", "junctions", "path", "nodes", "pz", "gates", "sequence"]
    by = Counter(x.check for x in out)
    checks = {n: ("failed" if by.get(n) else "passed") for n in names}
    if tool == HEURISTIC and sched.source.get("circuit") is None:
        checks["sequence"] = "skipped: no circuit given"
    checks["sequence_code"] = (("failed" if by.get("sequence_code") else "passed") if tool == SAT
                               else "skipped: a condition of the SAT code only")
    info.update({
        "final_positions": dict(sorted(final.items())),
        "moves": len(routes),
        "multi_node_moves": sum(1 for r in routes.values() if len(r.nodes) > 1),
        "steps": kmax + 1,
        "gate_events": len(gates),
    })
    if tool == SAT:
        info["code_path_quirk"] = {
            "moves": code_extra, "held": code_extra_held,
            "note": "moves for which SAT.py's get_path_between_edges required edges empty that the "
                    "chain does not pass (a shortest path around a lattice face); P1's rule needs only "
                    "the edges passed. 'held' counts those whose extra edges were empty too"}
    else:
        info["pz_node_shared"] = {"steps": sum(pz_shared.values()),
                                  "max_chains": max(pz_shared, default=1 if routes else 0),
                                  "note": "steps in which two chains passed the PZ node, one into "
                                          "and one out of the parking edge (Cycles.py l.374)"}
    return checks, out, info


def check_ionshuttler(sched: TimedSchedule, tool: str | None = None) -> Report:
    """The core checker (strict durations, the tool's profile) plus `ionshuttler_rules`, in one
    report.  ``metrics["makespan_steps"]`` is the replayed makespan in time steps (the core's
    ``makespan_us`` field, which here counts steps), ``metrics["printed"]`` the tool's number."""
    tool = tool or sched.source["tool"]
    circ = None
    if tool == HEURISTIC and sched.source.get("circuit"):
        pairs = [(str(qs[0]), str(qs[1])) for _, qs in sched.source["circuit"] if len(qs) == 2]
        circ = pairs or None
    rep = check(sched, PROFILES[tool], duration=ionshuttler_duration, circuit=circ, strict=True)
    checks, extra, info = ionshuttler_rules(sched, tool)
    rep.violations.extend(extra)
    rep.checks.update(checks)
    rep.metrics["makespan_steps"] = rep.metrics["makespan_us"]
    rep.metrics["printed"] = sched.claims.get("printed")
    rep.metrics["ionshuttler"] = info
    return rep


# ----------------------------------------------------------------------- our scheduler
#
# Under the HEURISTIC's model and nothing else: the same device (its graph, its exit path,
# parking edge and entry path), the same placement (read from the heuristic's own dump), one
# edge per chain per iteration through one node, one chain per node per iteration except the
# PZ node, following into vacated edges, the one-way PZ, one gate per iteration, a one-qubit
# gate counted in the iteration its chain stands on the parking edge, chains may stay parked
# at the end.  Its schedule is written in the heuristic's dump format and judged by the same
# `check_ionshuttler`; its number is the 0-based index of its last iteration, as the
# heuristic counts.


@dataclasses.dataclass
class _Board:
    edges: list[str]
    role: dict[str, tuple[str, int]]
    cap: dict[str, int]
    park: str
    pz_node: str
    nxt: dict[str, list[tuple[str, str]]]          # edge -> the (edge, node) one move reaches
    node: dict[tuple[str, str], str]               # (edge, edge) -> the node between them


def _board(d: Mapping) -> _Board:
    g = d["graph"]
    ends = {e["id"]: tuple(e["nodes"]) for e in g["edges"]}
    pz_node = next(n["id"] for n in g["nodes"] if n["node_type"] == "processing_zone_node")
    exit_path, entry_path = list(g["path_to_pz(exit)"]), list(g["path_from_pz(entry)"])
    park = g["parking_edge(gate site)"]
    role = {e: ("memory", 0) for e, x in ((e["id"], e) for e in g["edges"]) if x["edge_type"] == "trap"}
    role.update({e: ("exit", i) for i, e in enumerate(exit_path)})
    role.update({e: ("entry", i) for i, e in enumerate(entry_path)})
    role[park] = ("gate", 0)
    cap = {e["id"]: int(e["capacity"]) for e in g["edges"]}
    nx_, ne = len(exit_path), len(entry_path)

    def arc(x: str, y: str) -> bool:                # the heuristic's one-way PZ (`pz`)
        (rx, ox), (ry, oy) = role[x], role[y]
        return ((rx == ry == "memory") or (rx == "memory" and ry == "exit" and oy == 0)
                or (rx == ry == "exit" and oy == ox + 1)
                or (rx == "exit" and ox == nx_ - 1 and ry in ("gate",))
                or (rx == "gate" and ry == "entry" and oy == 0)
                or (rx == ry == "entry" and oy == ox + 1)
                or (rx == "entry" and ox == ne - 1 and ry == "memory"))

    nxt: dict[str, list[tuple[str, str]]] = {e: [] for e in ends}
    node: dict[tuple[str, str], str] = {}
    for x in ends:
        for y in ends:
            shared = set(ends[x]) & set(ends[y])
            if x != y and len(shared) == 1 and arc(x, y):
                n = shared.pop()
                nxt[x].append((y, n))
                node[(x, y)] = n
    return _Board(sorted(ends, key=lambda e: int(e[1:])), role, cap, park, pz_node, nxt, node)


def _distances_to(b: _Board, goal: str) -> dict[str, int]:
    """Moves from each edge to ``goal`` with nobody in the way."""
    rev: dict[str, list[str]] = defaultdict(list)
    for x, ys in b.nxt.items():
        for y, _ in ys:
            rev[y].append(x)
    dist, frontier = {goal: 0}, [goal]
    while frontier:
        new = []
        for y in frontier:
            for x in rev[y]:
                if x not in dist:
                    dist[x] = dist[y] + 1
                    new.append(x)
        frontier = new
    return dist


def lower_bound(d: Mapping) -> int:
    """The heuristic's printed number can be no less than this: gates are serial, one per
    iteration, and a chain ``m`` moves from the parking edge is counted at iteration m-1 at the
    earliest (0-based)."""
    b = _board(d)
    dist = _distances_to(b, b.park)
    ds = sorted(dist[e] for e in d["initial"].values())
    n = len(ds)
    return max(ds[j] - 1 + (n - 1 - j) for j in range(n))


class _Planner:
    """Prioritised planning in space and time: chains are planned one at a time against the
    reservations of those already planned; an unplanned chain stands still.  The next chain is
    the one whose gate can complete earliest; a chain that is done leaves the parking edge
    when it is full; a chain in everyone's way is moved aside."""

    def __init__(self, b: _Board, initial: Mapping[str, str], rng, H: int, exit_when: int, slack: int):
        self.b, self.rng, self.H, self.exit_when, self.slack = b, rng, H, exit_when, slack
        self.chains = sorted((int(c) for c in initial))
        T = H + 2                                            # index t+1 for t = -1..H
        self.P = {c: [initial[str(c)]] * T for c in self.chains}
        self.h = {c: -1 for c in self.chains}                 # last planned iteration
        self.occ = [Counter() for _ in range(T)]
        for c in self.chains:
            for i in range(T):
                self.occ[i][self.P[c][i]] += 1
        self.nodes: list[dict[str, int]] = [dict() for _ in range(T)]
        self.gate: dict[int, int] = {}
        self.gate_of: dict[int, int] = {}
        self.dist = _distances_to(b, b.park)

    # -- reservations --------------------------------------------------------------------
    def _occ(self, c: int, t: int, e: str) -> int:
        return self.occ[t + 1][e] - (self.P[c][t + 1] == e)

    def _node_busy(self, c: int, t: int, n: str) -> bool:
        u = self.nodes[t + 1].get(n)
        return u is not None and u != c and n != self.b.pz_node

    def _commit(self, c: int, t0: int, path: list[str]) -> None:
        """``path[i]`` is where c stands after iteration t0+1+i; afterwards it stays."""
        T = self.H + 2
        last = path[-1] if path else self.P[c][t0 + 1]
        for i in range(t0 + 2, T):
            new = path[i - t0 - 2] if i - t0 - 2 < len(path) else last
            old = self.P[c][i]
            if new != old:
                self.occ[i][old] -= 1
                self.occ[i][new] += 1
                self.P[c][i] = new
        for t in range(t0 + 1, t0 + 1 + len(path)):
            a, z = self.P[c][t], self.P[c][t + 1]
            if a != z:
                self.nodes[t + 1][self.b.node[(a, z)]] = c
        self.h[c] = t0 + len(path)

    # -- search ------------------------------------------------------------------------------
    def _bfs(self, c: int, goal, ignore: set[int] = frozenset(), pick=None):
        """Earliest reachable states satisfying ``goal(e, t)``; returns (t, path) or None.
        ``ignore``: chains whose STANDING positions (after their plans) do not block."""
        b, t0 = self.b, self.h[c]
        start = self.P[c][t0 + 1]

        def occ(t: int, e: str) -> int:
            k = self._occ(c, t, e)
            for p in ignore:
                if t > self.h[p] and self.P[p][t + 1] == e:
                    k -= 1
            return k

        layers = [{start: None}]
        found: list[tuple[int, str]] = []
        first = None
        for t in range(t0 + 1, self.H):
            prev, layer = layers[-1], {}
            order = list(prev)
            self.rng.shuffle(order)
            for e in order:
                for f, n in [(e, None)] + b.nxt[e]:
                    if f in layer or occ(t, f) >= b.cap[f]:
                        continue
                    if n is not None and self._node_busy(c, t, n):
                        continue
                    layer[f] = e
            if not layer:
                return None
            layers.append(layer)
            for f in layer:
                if goal(f, t, occ):
                    found.append((t, f))
                    first = t if first is None else first
            if first is not None and t >= first + (self.slack + 2 if pick else 0):
                break
        if not found:
            return None
        t, f = min(found, key=pick) if pick else min(found, key=lambda x: (x[0], self.rng.random()))
        path = [f]
        for i in range(t - t0, 1, -1):
            path.append(layers[i][path[-1]])
        return t, path[::-1]

    def _gate_goal(self, f: str, t: int, occ) -> bool:
        return (f == self.b.park and t not in self.gate
                and all(occ(u, f) < self.b.cap[f] for u in range(t, self.H)))

    def _rest_goal(self, avoid: set[str]):
        def goal(f: str, t: int, occ) -> bool:
            return (self.b.role[f][0] == "memory" and f not in avoid
                    and all(occ(u, f) == 0 for u in range(t, self.H)))
        return goal

    def _in_the_way(self, pending: set[int]) -> Counter:
        """How many pending chains' shortest routes pass each edge."""
        way: Counter = Counter()
        for p in pending:
            e = self.P[p][self.h[p] + 1]
            while e != self.b.park:
                e = min((f for f, _ in self.b.nxt[e] if self.dist.get(f, 1e9) < self.dist[e]),
                        key=lambda f: (self.dist[f], f))
                way[e] += 1
        return way

    def _rest_pick(self, pending: set[int], c: int):
        """Where a chain should stand: off the pending chains' routes, and (while chains are
        still to come back) off the edges at the entry junction, where returning chains land."""
        way = self._in_the_way(pending - {c})
        landing = {f for f, _ in self.b.nxt[self._entry_last()]} if pending else set()
        return lambda x: (way[x[1]] + 3 * (x[1] in landing), x[0], self.rng.random())

    def _entry_last(self) -> str:
        return max((e for e, (r, _) in self.b.role.items() if r == "entry"), key=lambda e: self.b.role[e][1])

    def _move_aside(self, c: int, pending: set[int], avoid: set[str] = frozenset(), depth: int = 2) -> bool:
        goal = self._rest_goal(set(avoid))
        r = self._bfs(c, goal, pick=self._rest_pick(pending, c))
        if r is not None:
            self._commit(c, self.h[c], r[1])
            return True
        if depth <= 0:
            return False
        return (self._clear(c, goal, pending, avoid, depth - 1)
                and self._move_aside(c, pending, avoid, depth - 1))

    def _clear(self, c: int, goal, pending: set[int], avoid: set[str] = frozenset(), depth: int = 2) -> bool:
        """``c`` cannot reach ``goal``: find its route through the chains standing still, and
        move the first of them in the way aside (recursively, ``depth`` deep)."""
        stand = {p for p in self.chains if p != c and self.P[p][-1] != self.b.park}
        r = self._bfs(c, goal, ignore=stand)
        if r is None:
            return False
        route = set(r[1]) | {self.P[c][self.h[c] + 1]} | set(avoid)
        blockers = sorted((p for p in stand if self.P[p][-1] in r[1]),
                          key=lambda p: r[1].index(self.P[p][-1]))
        return any(self._move_aside(p, pending, avoid=route, depth=depth) for p in blockers)

    def run(self) -> int | None:
        pending = set(self.chains)
        budget = 6 * len(self.chains)
        while pending:
            parked = [c for c in self.gate_of if self.P[c][-1] == self.b.park]
            if len(parked) >= self.exit_when:
                oldest = min(parked, key=lambda c: self.gate_of[c])
                if not self._move_aside(oldest, pending):
                    budget -= 1
                    if budget < 0 or not self._clear(oldest, self._rest_goal(set()), pending):
                        return None
                continue
            best = None
            for c in sorted(pending, key=lambda _: self.rng.random()):
                r = self._bfs(c, self._gate_goal)
                if r is not None and (best is None or r[0] < best[1][0]):
                    best = (c, r)
            if best is not None:
                c, (t, path) = best
                self._commit(c, self.h[c], path)
                self.gate[t], self.gate_of[c] = c, t
                pending.discard(c)
                continue
            # nobody can reach the parking edge: move a chain in someone's way aside
            budget -= 1
            if budget < 0:
                return None
            order = sorted(pending, key=lambda p: (self.dist[self.P[p][-1]], self.rng.random()))
            if not any(self._clear(c, self._gate_goal, pending) for c in order):
                return None
        return max(self.gate)

    def dump(self, d: Mapping, N: int) -> dict:
        """The schedule in the heuristic's dump format, up to iteration N (no chain needs to
        move after the last gate)."""
        steps = []
        for t in range(N + 1):
            moves = []
            for c in self.chains:
                a, z = self.P[c][t], self.P[c][t + 1]
                if a != z:
                    moves.append({"ion": c, "phase": "ours", "from": a, "to": z,
                                  "through_nodes": [self.b.node[(a, z)]]})
            st: dict[str, Any] = {"t": t, "positions": {str(c): self.P[c][t + 1] for c in self.chains},
                                  "moves": moves, "gates": [], "gate_detail": None}
            if t in self.gate:
                q = self.gate[t]
                st["gates"] = [[q]]
                st["gate_detail"] = {"qubits": [q], "op": ["h", []], "step_of_gate": 1, "duration": 1,
                                     "completed_this_step": True}
            steps.append(st)
        keep = ("graph", "initial", "initial_layout", "qubit_to_ion", "config", "circuit_file", "placement_rule")
        return {"tool": "ours: qccd.repro.ionshuttler.schedule_ours, under the cycle heuristic's model",
                **{k: d[k] for k in keep if k in d},
                "time_units": "as the heuristic: step t = one iteration; printed number = index of the "
                              "final iteration (0-based)",
                "metrics": {PRINTED[HEURISTIC]: N}, "steps": steps}


def schedule_ours(d: Mapping, *, tries: int = 512, seed: int = 0, H: int | None = None) -> tuple[dict | None, dict]:
    """Our schedule for the heuristic dump ``d``'s device, placement and circuit (FRA: one
    one-qubit gate per chain): the best of up to ``tries`` seeded randomised runs of `_Planner`,
    stopping at the first that meets `lower_bound` (then it is optimal in this model).  Returns
    the dump (or None) and what the search did."""
    import random

    if any(len(op) != 1 for op in d.get("initial_sequence", [[0]])):
        raise ValueError("schedule_ours handles one-qubit gates on single chains only (FRA)")
    b = _board(d)
    lb = lower_bound(d)
    H = H or 6 * len(d["initial"]) + 20
    best, best_n, ok, used = None, None, 0, 0
    for k in range(tries):
        used += 1
        rng = random.Random(seed * 100003 + k)
        p = _Planner(b, d["initial"], rng, H, exit_when=(3, 2)[k % 2], slack=(k // 2) % 4)
        n = p.run()
        if n is None:
            continue
        ok += 1
        moves = sum(1 for c in p.chains for t in range(n + 1) if p.P[c][t] != p.P[c][t + 1])
        if best_n is None or (n, moves) < best_n:
            best, best_n = p.dump(d, n), (n, moves)
        if best_n[0] <= lb:
            break
    return best, {"tries": used, "succeeded": ok, "best": best_n[0] if best_n else None,
                  "moves": best_n[1] if best_n else None, "lower_bound": lb,
                  "optimal": bool(best_n and best_n[0] <= lb)}


# ---------------------------------------------------------------------------- reproduce

_P1 = "arXiv 2311.03454v1, Table I"
_P2 = "arXiv 2402.14065v2, Table I"
_QFT_TXT = ("the authors' own log qft.txt in the artifact's history (added in a4588e4, 2024-06-25; "
            "present at 4f8564b 'First Publish', removed in abe9c51): 'array ts: [18, 18, 12, 13, 12]', "
            "'& 6 2 1 1 & 8/16 & 8 & 14.6 & ... compilation=True'")

#: every configuration we replay: what it is and what the paper prints for it
CONFIGS: dict[str, dict] = {
    "sat_L4411_fra12": {
        "tool": SAT, "kind": "artifact", "layout": "Lattice", "arch": [4, 4, 1, 1], "chains": 12, "sites": 24,
        "label": "exact SAT, Lattice 4 4 1 1, 12 chains on 24 sites, full register access",
        "paper": {"value": 18.5, "runs": 10, "where": f"{_P1}, Lattice block, Full Register Access, "
                  "4 4 1 1, 12/24 (50%), column T^ (mean of 10 runs)"}},
    "sat_L3311_fra6": {
        "tool": SAT, "kind": "artifact", "layout": "Lattice", "arch": [3, 3, 1, 1], "chains": 6, "sites": 12,
        "label": "exact SAT, Lattice 3 3 1 1, 6 chains on 12 sites, full register access",
        "paper": {"value": 10.9, "runs": 10, "where": f"{_P1}, Lattice block, Full Register Access, "
                  "3 3 1 1, 6/12 (50%), column T^"}},
    "sat_L4411_fra6": {
        "tool": SAT, "kind": "artifact", "layout": "Lattice", "arch": [4, 4, 1, 1], "chains": 6, "sites": 24,
        "label": "exact SAT, Lattice 4 4 1 1, 6 chains on 24 sites, full register access",
        "paper": {"value": 12.5, "runs": 10, "where": f"{_P1}, Lattice block, Full Register Access, "
                  "4 4 1 1, 6/24 (25%), column T^"}},
    "sat_R2215_fra6": {
        "tool": SAT, "kind": "artifact", "layout": "Racetrack", "arch": [2, 2, 1, 5], "chains": 6, "sites": 12,
        "label": "exact SAT, Racetrack 2 2 1 5, 6 chains on 12 sites, full register access",
        "paper": {"value": 11.0, "runs": 10, "where": f"{_P1}, Racetrack block, Full Register Access, "
                  "2 2 1 5, 6/12 (50%), column T^"}},
    "heur_L4411_fra12": {
        "tool": HEURISTIC, "kind": "artifact", "layout": "Lattice", "arch": [4, 4, 1, 1], "chains": 12, "sites": 24,
        "label": "cycle heuristic, Lattice 4 4 1 1, 12 chains on 24 sites, full register access (Gate Selection on)",
        "paper": {"value": 26.6, "runs": 5, "where": f"{_P2}, Lattice 4 4 1 1, 12/24, Full Register "
                  "Access, column T^ (heuristic, mean of five runs)"},
        "also": {"T^min": [20.2, f"{_P2}, same row, column T^min ('exhaustive search', method not "
                           "described); not P1's model: different PZ, so not compared with the SAT runs"]}},
    "heur_H6211_fra8": {
        "tool": HEURISTIC, "kind": "artifact", "layout": "Horizontal Grate", "arch": [6, 2, 1, 1], "chains": 8,
        "sites": 16,
        "label": "cycle heuristic, Horizontal Grate 6 2 1 1, 8 chains on 16 sites, full register access (Gate Selection on)",
        "paper": {"value": 24.4, "runs": 5, "where": f"{_P2}, Horizontal Grate 6 2 1 1, 8/16, Full "
                  "Register Access, column T^ (heuristic, mean of five runs)"},
        "authors_log": {"values": [18, 18, 12, 13, 12], "mean": 14.6, "where": _QFT_TXT},
        "also": {"T^min": [15.7, f"{_P2}, same row, column T^min"]}},
    # labelled diagnostics: not paper configurations
    "heurNoGS_L4411_fra12": {
        "tool": HEURISTIC, "kind": "diagnostic", "layout": "Lattice", "arch": [4, 4, 1, 1], "chains": 12, "sites": 24,
        "label": "diagnostic: the heuristic on Lattice 4 4 1 1, 12/24, without Gate Selection (the code's "
                 "compilation=False; FRA in ascending order)",
        "reference": {"value": 26.6, "where": f"{_P2}, Lattice 4 4 1 1, FRA, T^ (with Gate Selection)"}},
    "heurNoGS_H6211_fra8": {
        "tool": HEURISTIC, "kind": "diagnostic", "layout": "Horizontal Grate", "arch": [6, 2, 1, 1], "chains": 8,
        "sites": 16,
        "label": "diagnostic: the heuristic on Horizontal Grate 6 2 1 1, 8/16, without Gate Selection",
        "reference": {"value": 24.4, "where": f"{_P2}, Horizontal Grate 6 2 1 1, FRA, T^"}},
    "sat_R2251T_fra6": {
        "tool": SAT, "kind": "diagnostic", "layout": "Racetrack (transposed)", "arch": [2, 2, 5, 1], "chains": 6,
        "sites": 12,
        "label": "diagnostic: exact SAT on the racetrack transposed (arch [2, 2, 5, 1]: the PZ on a short "
                 "side), 6/12, full register access",
        "reference": {"value": 11.0, "where": f"{_P1}, Racetrack 2 2 1 5, 6/12, T^"}},
}

#: runs that produced no schedule
NOT_REPLAYED = [{
    "id": "timeout_sat_L3311_fra12_seed0", "tool": SAT, "config": "sat_L3311_fra12", "seed": 0,
    "label": "exact SAT, Lattice 3 3 1 1, 12 chains on 12 sites (100%), full register access",
    "paper": {"value": 17.0, "runs": 10, "where": f"{_P1}, Lattice block, Full Register Access, 3 3 1 1, "
              "12/12 (100%), column T^ (t_CPU 36.1 s)"},
}]

PAPER_META = {
    "key": "schoenberger2024",
    "papers": [
        {"id": "P1", "tool": SAT,
         "title": "Using Boolean Satisfiability for Exact Shuttling in Trapped-Ion Quantum Computers",
         "authors": ["Daniel Schoenberger", "Stefan Hillmich", "Matthias Brandl", "Robert Wille"],
         "venue": "ASP-DAC 2024", "arxiv": "2311.03454", "version": "v1"},
        {"id": "P2", "tool": HEURISTIC,
         "title": "Shuttling for Scalable Trapped-Ion Quantum Computers",
         "authors": ["Daniel Schoenberger", "Stefan Hillmich", "Matthias Brandl", "Robert Wille"],
         "venue": "IEEE TCAD 44(6), doi 10.1109/TCAD.2024.3513262", "arxiv": "2402.14065", "version": "v2"},
    ],
    "artifact": {"url": "https://github.com/munich-quantum-toolkit/ionshuttler",
                 "commit": "bc71e46796a1d8f221d608991089d0ba3f0b0b56", "license": "MIT",
                 "environment": "Python 3.11.9, z3-solver 4.12.1.0, networkx 3.0, qiskit 1.0.0",
                 "patches": "artifact/patches: a seed argument (the code's own random placement) and a "
                            "dump of the computed schedule; no constraint, constant or loop changed"},
}


def _stdout_number(text: str | None, tool: str) -> int | None:
    if not text:
        return None
    m = _STDOUT[tool].findall(text)
    return int(m[-1]) if m else None


def _mean(xs: Sequence[float]) -> float | None:
    return round(sum(xs) / len(xs), 6) if xs else None


def _verdict(mean: float | None, value: float) -> str:
    return "exact" if mean is not None and abs(round(mean, 1) - value) < 1e-9 else "differs"


def reproduce(out: str | Path, *, with_ours: bool = True, log=print) -> dict:
    """Replay every schedule in ``out/artifact`` (circuits from ``out/circuits``, console
    output from ``out/artifact/runs.json.gz``), write our schedules for the heuristic's paper
    configurations to ``out/ours`` (`schedule_ours`, seeded), and write ``out/results.json``."""
    out = Path(out)
    art = out / "artifact"
    bundle = json.loads(gzip.decompress((art / "runs.json.gz").read_bytes()).decode("utf-8"))["runs"]
    runs: list[dict] = []
    for f in sorted(art.glob("*.schedule.json.gz")):
        rid = f.name[: -len(".schedule.json.gz")]
        m = re.match(r"(.+)_seed(\d+)$", rid)
        cfg, seed = m.group(1), int(m.group(2))
        meta = CONFIGS[cfg]
        t0 = time.time()
        s = import_ionshuttler(f, name=rid, circuits_dir=out / "circuits")
        rep = check_ionshuttler(s)
        tool = s.source["tool"]
        ms = rep.metrics["makespan_steps"]
        rec_b = bundle.get(rid, {})
        line_n = _stdout_number(rec_b.get("stdout"), tool)
        dump_n = s.claims["printed"]
        equal = line_n == dump_n and abs(ms - dump_n) < _TOL
        info = rep.metrics["ionshuttler"]
        same_end = info.pop("final_positions") == s.claims.get("final_positions")
        measured = {"makespan_steps": ms, "moves": info["moves"], "gate_events": info["gate_events"]}
        if tool == SAT:
            measured.update({"states": round(ms), "transitions": round(ms) - 1})
        else:
            measured.update({"iterations": round(ms) + 1})
        rec = {
            "id": rid, "label": f"{meta['label']}, seed {seed}", "kind": meta["kind"], "tool": tool,
            "config": cfg, "seed": seed, "file": f"artifact/{f.name}",
            "printed": {"stdout": line_n, "dump": dump_n, "line": PRINTED[tool].replace("N", str(line_n))},
            "measured": measured,
            "compare": {"tool": {"T_steps": {
                "ours": ms, "theirs": dump_n, "verdict": "exact" if equal else "differs",
                "where": "the tool's console line ('" + PRINTED[tool] + "'), "
                         + ("the number of states" if tool == SAT else "the 0-based index of the last iteration")},
                "final_positions": {"verdict": "exact" if same_end else "differs",
                                    "where": "the dump's positions after its last step"}}},
            "checks": dict(rep.checks), "ok": rep.ok,
            "violations": dict(Counter(x.check for x in rep.violations)),
            "first_violations": [dataclasses.asdict(x) for x in rep.violations[:12]],
            "notes": dict(rep.notes), "ionshuttler": info,
            "events": len(s.events),
            "tool_run": {k: rec_b.get("watch", {}).get(k) for k in ("returncode", "wall_s", "peak_private_gb")},
            "seconds": round(time.time() - t0, 3),
        }
        runs.append(rec)
        log(f"{rid}: printed {dump_n} (stdout {line_n}), replayed {ms:g}, ok={rep.ok}")

    configs: list[dict] = []
    for cfg, meta in CONFIGS.items():
        rs = sorted((r for r in runs if r["config"] == cfg), key=lambda r: r["seed"])
        if not rs:
            continue
        printed = {r["seed"]: r["printed"]["dump"] for r in rs}
        measured = {r["seed"]: r["measured"]["makespan_steps"] for r in rs}
        rec: dict[str, Any] = {
            "config": cfg, "label": meta["label"], "kind": meta["kind"], "tool": meta["tool"],
            "layout": meta["layout"], "arch": meta["arch"], "chains": meta["chains"], "sites": meta["sites"],
            "seeds": list(printed), "printed": printed, "measured": measured,
            "all_ok": all(r["ok"] for r in rs),
            "all_equal": all(r["compare"]["tool"]["T_steps"]["verdict"] == "exact" for r in rs),
            "mean_printed": _mean(list(printed.values())), "mean_measured": _mean(list(measured.values())),
        }
        if "paper" in meta:
            p = meta["paper"]
            use = [measured[s] for s in sorted(measured)[: p["runs"]]]
            mean = _mean(use)
            rec["paper"] = {**p, "seeds": f"{min(measured)}-{min(measured) + len(use) - 1}",
                            "ours_mean": mean, "verdict": _verdict(mean, p["value"]),
                            "delta": round(mean - p["value"], 6) if mean is not None else None}
            if len(use) < len(measured):
                rec["paper"]["all_seeds_mean"] = _mean(list(measured.values()))
        if "authors_log" in meta:
            a = meta["authors_log"]
            mine = [measured[s] for s in sorted(measured)[: len(a["values"])]]
            rec["authors_log"] = {**a, "ours": mine,
                                  "verdict": "exact" if [round(x) for x in mine] == a["values"] else "differs"}
        if "reference" in meta:
            rec["reference"] = {**meta["reference"], "note": "not a paper configuration; shown for scale"}
        if "also" in meta:
            rec["also"] = meta["also"]
        configs.append(rec)

    not_replayed = []
    for nr in NOT_REPLAYED:
        b = bundle.get(nr["id"], {})
        text = b.get("stdout", "")
        unsat = [int(x) for x in re.findall(r"Checking for (\d+) timesteps\.\.\. unsat", text)]
        open_ = re.findall(r"Checking for (\d+) timesteps\.\.\. *$", text.strip())
        not_replayed.append({**nr, "watch": b.get("watch"),
                             "proved_unsat_up_to": max(unsat) if unsat else None,
                             "undecided_at": int(open_[0]) if open_ else None,
                             "lower_bound": (max(unsat) + 1) if unsat else None,
                             "verdict": "differs: this seed alone needs at least "
                                        f"{max(unsat) + 1 if unsat else '?'} states against the paper's "
                                        "10-run mean of 17.0, and the tool did not finish in 1800 s "
                                        "against the paper's mean t_CPU of 36.1 s"})

    # -- ours: under the heuristic's model, on the heuristic's own device and placements -----
    ours: list[dict] = []
    if with_ours:
        (out / "ours").mkdir(exist_ok=True)
        for r in runs:
            if r["tool"] != HEURISTIC or r["kind"] != "artifact":
                continue
            t0 = time.time()
            d = _load(art / f"{r['id']}.schedule.json.gz")
            dump, st = schedule_ours(d)
            rid = f"{r['id']}_ours"
            rec: dict[str, Any] = {"id": rid, "against": r["id"], "config": r["config"], "seed": r["seed"],
                                   "label": f"ours under the heuristic's model, {CONFIGS[r['config']]['label'].split(', ', 1)[1]}, "
                                            f"seed {r['seed']} (the heuristic's placement)",
                                   "search": st}
            if dump is None:
                rec.update(ok=False, error="no schedule found")
                ours.append(rec)
                continue
            f = out / "ours" / f"{rid}.schedule.json.gz"
            f.write_bytes(gzip.compress(json.dumps(dump, separators=(",", ":")).encode("utf-8"), 9, mtime=0))
            s = import_ionshuttler(f, name=rid, circuits_dir=out / "circuits")
            rep = check_ionshuttler(s)
            ms = rep.metrics["makespan_steps"]
            theirs = r["printed"]["dump"]
            rec.update({
                "file": f"ours/{f.name}", "checks": dict(rep.checks), "ok": rep.ok,
                "violations": dict(Counter(x.check for x in rep.violations)),
                "measured": {"makespan_steps": ms, "iterations": round(ms) + 1,
                             "moves": rep.metrics["ionshuttler"]["moves"]},
                "lower_bound": st["lower_bound"], "optimal": st["optimal"] and abs(ms - st["lower_bound"]) < _TOL,
                "compare": {"heuristic": {
                    "ours": ms, "theirs": theirs, "gain_steps": theirs - ms,
                    "verdict": "ours faster" if ms < theirs else ("equal" if ms == theirs else "ours slower"),
                    "where": f"the heuristic's printed number for the same placement ({r['id']})"}},
                "seconds": round(time.time() - t0, 2),
            })
            ours.append(rec)
            log(f"{rid}: {ms:g} against the heuristic's {theirs} (lower bound {st['lower_bound']}), ok={rep.ok}")

    ours_by_cfg = {}
    for cfg in sorted({o["config"] for o in ours}):
        os_ = [o for o in ours if o["config"] == cfg and o.get("ok")]
        mine = [o["measured"]["makespan_steps"] for o in os_]
        theirs = [o["compare"]["heuristic"]["theirs"] for o in os_]
        ours_by_cfg[cfg] = {"runs": len(os_), "all_ok": len(os_) == sum(o["config"] == cfg for o in ours),
                            "ours_mean": _mean(mine), "heuristic_mean": _mean(theirs),
                            "faster": sum(a < b for a, b in zip(mine, theirs)),
                            "optimal": sum(o["optimal"] for o in os_),
                            "paper_T^": CONFIGS[cfg]["paper"]["value"]}

    paper_runs = [r for r in runs if r["kind"] == "artifact"]
    summary = {
        "replayed": len(runs), "ok": sum(r["ok"] for r in runs),
        "equal_to_printed": sum(r["compare"]["tool"]["T_steps"]["verdict"] == "exact" for r in runs),
        "end_where_the_tool_says": sum(r["compare"]["tool"]["final_positions"]["verdict"] == "exact" for r in runs),
        "paper_configuration_runs": {"sat": sum(r["tool"] == SAT for r in paper_runs),
                                     "heuristic": sum(r["tool"] == HEURISTIC for r in paper_runs)},
        "diagnostic_runs": len(runs) - len(paper_runs),
        "paper_rows": {c["config"]: {"ours": c["paper"]["ours_mean"], "paper": c["paper"]["value"],
                                     "verdict": c["paper"]["verdict"]} for c in configs if "paper" in c},
    }
    result = {
        "paper": PAPER_META,
        "model": {
            "doc": "MODEL.md", "units": "abstract time steps (no physical time in either paper)",
            "clocks": {SAT: "state t is the unit [t, t+1); the transition from state t-1 fills it; makespan "
                            "= the printed number of states (N states = N-1 transitions)",
                       HEURISTIC: "iteration t is the unit [t-1, t); its gate step is the instant t; makespan "
                                  "= the printed 0-based index of the last iteration (N+1 iterations)"},
            "move": "split [a, a+1/4), the node passage(s) [a+1/4, a+3/4), merge [a+3/4, a+1)",
            "gates": "instants at the end of their step, at the gate site (SAT e_in, heuristic parking edge)",
            "profiles": {t: p.to_json() for t, p in PROFILES.items()},
            "rules": RULES,
        },
        "summary": summary, "configs": configs, "not_replayed": not_replayed, "runs": runs,
        "ours": {
            "how": "Prioritised planning in space and time under the cycle heuristic's model only: its "
                   "device and its placement for each seed (read from its dump), one edge per chain per "
                   "iteration, one chain per node per iteration except the PZ node, following into "
                   "vacated edges, the one-way PZ, one gate per iteration counted when the chain stands "
                   "on the parking edge, chains may stay parked at the end. The next chain is the one "
                   "whose gate can complete earliest; a done chain leaves a full parking edge; a chain "
                   "in the way is moved aside. Seeded randomised restarts, stopping at the distance "
                   "lower bound (gates are serial and a chain m moves from the parking edge gates no "
                   "earlier than iteration m-1), which makes a schedule that meets it optimal in this "
                   "model. Judged by the same check_ionshuttler; counted as the heuristic counts "
                   "(0-based index of the last iteration). Not compared with the SAT runs, whose model "
                   "and count differ.",
            "summary": ours_by_cfg, "runs": ours},
    }
    (out / "results.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    log(f"{summary['ok']} of {summary['replayed']} pass every check; "
        f"{summary['equal_to_printed']} replay to the printed number")
    return result


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Replay MQT IonShuttler's schedules in the timed checker")
    ap.add_argument("out", nargs="?",
                    default=str(Path(__file__).resolve().parents[2] / "Reproduce" / "schoenberger2024"))
    reproduce(ap.parse_args().out)
