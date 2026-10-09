"""A paper's schedule, opened in the Studio.

The Studio plays a program instruction by instruction on an architecture document.  A
timed schedule is turned into one: each ion's split ... move ... merge (or a TISCC hop, or a
Jones split, junction entry and exit, merge) becomes ONE move from trap to trap through the
junctions it crossed, and the operations that START at the same instant are batched into one
instruction -- gates with gates, transport with transport, never mixed, one gate per trap.
Every instruction keeps the paper's own start and end times in its `meta`, so the page can
say which moment of the paper's clock a frame is.

What the Studio then shows is the paper's schedule judged by THIS platform's model (one
cycle at a time, our 27 rules).  The reproduction itself -- the paper's clock, the paper's
rules -- is `qccd.repro.timed.check`; the page says both, and which is which.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..ir.tsir import TSIR, Instruction, Participant
from .devices import TABLES, to_arch_doc
from .timed import Event, TimedSchedule

__all__ = ["node_id", "studio_arch_doc", "to_tsir", "source_payload"]


def node_id(n: str) -> str:
    """A node or segment id as the architecture language takes it: the same mapping
    `devices.to_arch_doc` applies (`devices.adl_id`)."""
    from .devices import adl_id
    return adl_id(n)


def studio_arch_doc(sched: TimedSchedule, positions, *, name: str, table: str,
                    gate_us: float, hop_entails: tuple[str, ...] = ("split", "merge"),
                    one_q_us: float = 0.0, measure_us: float = 0.0, reset_us: float = 0.0,
                    note: str = "") -> dict:
    """`to_arch_doc` plus what a Studio page needs to price and play a program: the gate,
    measurement and reset primitives (at the paper's values; zero where its model charges
    nothing), and one declared movement class for a trap-to-trap shuttle."""
    # the Studio draws y downward and the paper figures y upward: flip, so the first row of
    # traps a paper draws on top is on top here too
    flipped = {n: (x, -y) for n, (x, y) in positions.items()}
    doc = to_arch_doc(sched.device, flipped, name=name, table=table, gate_us=gate_us,
                      note=note)
    src = TABLES[table]["source"]
    prims = doc["primitives"]
    prims["ms_gate"] = {"us": gate_us, "fidelity_at_n0": 1.0, "source": src,
                        "note": TABLES[table]["note"]}
    prims["1q_gate"] = {"us": one_q_us, "fidelity": 1.0, "source": src}
    prims["measure"] = {"us": measure_us, "fidelity": 1.0, "source": src}
    prims["reset"] = {"us": reset_us, "error": 0.0, "source": src}
    prims["cool"] = {"us": 0.0, "removes_quanta": "all", "broadcastable": True,
                     "scope": "global", "source": src,
                     "note": "this paper charges no cooling; present so the page can price a cool"}
    prims["gate_swap"] = {"gates": 3, "source": src}
    doc["control"] = {
        "model": "direct",
        "classes": {"extra": [{"id": "shuttle", "type": "shift", "orbit": "any",
                               **({"entails": list(hop_entails)} if hop_entails else {}),
                               "note": "one ion from one trap to another, as the paper moves it"}]},
    }
    return doc


def _shuttles(sched: TimedSchedule) -> tuple[list[dict], list[Event]]:
    """Fold each ion's transport events into trap-to-trap moves; return (moves, the rest)."""
    moves: list[dict] = []
    rest: list[Event] = []
    open_: dict[str, dict] = {}
    def seg_on(m: dict, sid: str | None) -> None:
        if sid and (not m["via"] or m["via"][-1] != sid):
            m["via"].append(sid)

    for e in sorted(sched.events, key=lambda e: (e.t0, e.id)):
        if e.kind == "split":
            open_[e.ions[0]] = {"ion": e.ions[0], "src": e.at, "via": [e.seg], "t0": e.t0,
                                "events": [e.id], "swap": e.swap}
        elif e.kind in ("move", "j_enter", "j_exit", "transit"):
            m = open_.get(e.ions[0])
            if m is None:
                raise ValueError(f"event {e.id}: {e.kind} with no split before it")
            seg_on(m, {"move": e.dst, "j_exit": e.dst, "j_enter": e.src, "transit": e.seg}[e.kind])
            m["events"].append(e.id)
        elif e.kind == "merge":
            m = open_.pop(e.ions[0], None)
            if m is None:
                raise ValueError(f"event {e.id}: merge with no split before it")
            seg_on(m, e.seg)
            m.update(dst=e.at, t1=e.t1)
            m["events"].append(e.id)
            moves.append(m)
        elif e.kind == "hop":
            # a hop crosses the nodes in `path`; its segments are the ones joining them
            nodes = [e.src, *e.path, e.dst]
            segs = []
            for a, b in zip(nodes, nodes[1:]):
                hit = [k for k, ends in sched.device.segments.items() if set(ends) == {a, b}]
                if len(hit) != 1:
                    raise ValueError(f"hop {e.id}: {len(hit)} segments join {a} and {b}")
                segs.append(hit[0])
            moves.append({"ion": e.ions[0], "src": e.src, "dst": e.dst, "via": segs,
                          "t0": e.t0, "t1": e.t1, "events": [e.id]})
        else:
            rest.append(e)
    if open_:
        raise ValueError(f"{len(open_)} ion(s) never merged: {sorted(open_)[:3]}")
    return moves, rest


def to_tsir(sched: TimedSchedule, *, name: str, arch: str, gate: str = "MS") -> TSIR:
    """`sched` as a Studio program, operations batched by start time (see the module doc)."""
    moves, rest = _shuttles(sched)
    ops: list[tuple[float, int, str, Any]] = []
    for k, m in enumerate(moves):
        sw = m.get("swap") or {}
        if sw.get("kind") == "gate":
            if sw.get("with"):
                # the reorder QCCDSim folds into the split, played as the gate swap it is
                ops.append((m["t0"], 0, "swapgate", {"ions": (m["ion"], str(sw["with"])),
                                                     "at": m["src"], "t0": m["t0"], "t1": m["t0"]}))
            # a split that paid for its reorder is batched apart and says so (R14 reads it);
            # Cyclone pays for one every step without naming the partner
            ops.append((m["t0"], 1, "move_sw", m))
        else:
            ops.append((m["t0"], 1, "move", m))
    for e in rest:
        if e.kind == "gate":
            ops.append((e.t0, 1, "gate2", e))
        elif e.kind in ("gate1", "prepare"):
            if e.ions:
                ops.append((e.t0, 2, "gate1", e))
        elif e.kind == "swap":
            ops.append((e.t0, 1, "swap", e))
        elif e.kind == "measure":
            ops.append((e.t0, 3, "measure", e))
        elif e.kind == "reset":
            ops.append((e.t0, 4, "reset", e))
    ops.sort(key=lambda o: (o[0], o[1]))
    # a reorder, a move and a gate starting together go in that order

    prog = TSIR(name=name, arch_spec=arch)
    placement = {ion: node_id(site) for site, ions in sched.chains.items() for ion in ions}
    prog.add(Instruction(type="init", id=prog.next_id(), placement=placement,
                         quanta={i: 0.0 for i in placement},
                         meta={"source": "replayed", "tool": sched.source.get("tool")}))
    # every one of these papers starts its chains in the motional ground state
    prog.add(Instruction(type="cool", id=prog.next_id(), broadcast=True,
                         meta={"kind": "state_prep", "note": "the paper's chains start cold"}))

    def flush(batch: list, kind: str) -> None:
        if not batch:
            return
        dictish = kind in ("move", "move_sw", "swapgate")
        t0 = min(b["t0"] if dictish else b.t0 for b in batch)
        t1 = max(b["t1"] if dictish else b.t1 for b in batch)
        meta = {"t0_us": t0, "t1_us": t1, "kind": "replayed"}
        if kind in ("move", "move_sw"):
            parts = tuple(Participant(ion=b["ion"], src=node_id(b["src"]), dst=node_id(b["dst"]),
                                      via=tuple(node_id(v) for v in b["via"])) for b in batch)
            if kind == "move_sw":
                meta = dict(meta, gate_swaps=len(batch))
            prog.add(Instruction(type="simd", id=prog.next_id(), cls="shuttle", mode="inter",
                                 participants=parts, meta=meta))
        elif kind == "swapgate":
            prog.add(Instruction(type="gate", id=prog.next_id(), gate="SWAP", mode="intra",
                                 pairs=tuple(tuple(b["ions"]) for b in batch),
                                 sites=tuple(node_id(b["at"]) for b in batch),
                                 meta=dict(meta, kind="reorder")))
        elif kind in ("gate2", "swap"):
            g = "SWAP" if kind == "swap" else gate
            prog.add(Instruction(type="gate", id=prog.next_id(), gate=g, mode="intra",
                                 pairs=tuple(tuple(b.ions) for b in batch),
                                 sites=tuple(node_id(b.at) for b in batch), meta=meta))
        elif kind == "gate1":
            prog.add(Instruction(type="gate", id=prog.next_id(), gate="R", mode="intra",
                                 arity=1, ions=tuple(b.ions[0] for b in batch),
                                 sites=tuple(node_id(b.at) for b in batch), meta=meta))
        elif kind in ("measure", "reset"):
            prog.add(Instruction(type=kind, id=prog.next_id(),
                                 ions=tuple(i for b in batch for i in b.ions), meta=meta))

    batch: list = []
    key = None
    busy_ions: set[str] = set()
    busy_sites: set[str] = set()
    for t0, _, kind, item in ops:
        if kind in ("move", "move_sw"):
            ions, site = {item["ion"]}, None
        elif kind == "swapgate":
            ions, site = set(item["ions"]), item["at"]
        else:
            ions, site = set(item.ions), item.at
        same = key == (t0, kind)
        clash = bool(ions & busy_ions) or (site is not None and kind in ("gate2", "gate1", "swap",
                                                                         "swapgate")
                                           and site in busy_sites)
        if not same or clash:
            flush(batch, key[1] if key else kind)
            batch, busy_ions, busy_sites = [], set(), set()
            key = (t0, kind)
        batch.append(item)
        busy_ions |= ions
        if site is not None:
            busy_sites.add(site)
    if key:
        flush(batch, key[1])
    return prog


def source_payload(prog: TSIR, name: str, lines: list[str], ops: list[dict], *,
                   join_1q: bool = False) -> dict:
    """The circuit pane's payload for a programme replayed from someone else's schedule.

    `qccd.ir.source_map.build` joins a programme to its circuit through the stamps OUR compiler
    puts on every instruction; a replayed programme has none, so the join is made here, by
    the operations themselves.  `ops` are the circuit's statements in circuit order, each
    ``{"name", "ions", "line"}`` (``ions`` as the programme names them, ``line`` 1-based in
    `lines`, optional ``kind``: "gate" / "measure" / "reset", optional ``p``).

    * A two-qubit gate realises the earliest statement not yet done on the same two ions; a
      measurement or reset, the earliest of its kind on that ion.  One-qubit gates are joined
      only with ``join_1q`` -- where the paper's schedule runs the circuit's own one-qubit
      operations; where it decomposes them (Jones's RX/RY for an H), a join would name the
      wrong statement, so the statement is shown and not joined.
    * Transport is "towards" the next statement its ions still have to do, or "after" the last
      one they did when none is left -- the same split `source_map` makes.
    """
    out_ops: list[dict] = []
    by_key: dict[tuple, list[int]] = defaultdict(list)
    per_ion: dict[str, list[int]] = defaultdict(list)
    for i, o in enumerate(ops):
        ions = tuple(str(x) for x in o["ions"])
        kind = o.get("kind", "gate")
        out_ops.append({"i": i, "name": o["name"], "q": list(o.get("q", ions)),
                        "p": list(o.get("p", ())), "line": int(o["line"])})
        by_key[(kind, frozenset(ions))].append(i)
        for ion in ions:
            per_ion[ion].append(i)
    ptr: dict[tuple, int] = defaultdict(int)
    done: set[int] = set()
    last: dict[str, int] = {}
    nxt: dict[str, int] = defaultdict(int)

    def take(kind: str, ions: tuple) -> int | None:
        key = (kind, frozenset(ions))
        lst = by_key.get(key)
        if not lst:
            return None
        while ptr[key] < len(lst) and lst[ptr[key]] in done:
            ptr[key] += 1
        if ptr[key] >= len(lst):
            return None
        i = lst[ptr[key]]
        ptr[key] += 1
        done.add(i)
        for ion in ions:
            last[ion] = i
        return i

    def pending(ion: str) -> int | None:
        lst = per_ion.get(ion, [])
        while nxt[ion] < len(lst) and lst[nxt[ion]] in done:
            nxt[ion] += 1
        return lst[nxt[ion]] if nxt[ion] < len(lst) else None

    realises: dict[str, list[int]] = {}
    toward: dict[str, list[int]] = {}
    after: dict[str, list[int]] = {}
    for ins in prog.instructions:
        got: list[int] = []
        if ins.type == "gate" and ins.gate != "SWAP":
            if ins.pairs:
                for a, b in ins.pairs:
                    i = take("gate", (str(a), str(b)))
                    if i is not None:
                        got.append(i)
            elif join_1q:
                for ion in (ins.ions or ()):
                    i = take("gate", (str(ion),))
                    if i is not None:
                        got.append(i)
        elif ins.type in ("measure", "reset"):
            for ion in (ins.ions or ()):
                i = take(ins.type, (str(ion),))
                if i is not None:
                    got.append(i)
        if got:
            realises[str(ins.id)] = sorted(got)
        elif ins.type == "simd" and ins.participants:
            movers = [str(p.ion) for p in ins.participants]
            soon = sorted({j for j in (pending(m) for m in movers) if j is not None})
            if soon:
                toward[str(ins.id)] = soon
            else:
                past = sorted({last[m] for m in movers if m in last})
                if past:
                    after[str(ins.id)] = past
    return {"name": name, "lines": list(lines), "ops": out_ops,
            "realises": realises, "toward": toward, "after": after}
