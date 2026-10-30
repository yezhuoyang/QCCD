"""G1-G9 for a schedule over verified places (`city.py`).  GADGETS.md §9.

The same promises as the beta's hierarchy checks (`checks.py`), re-derived from the GIR
alone, with the place model's differences: blocks travel between places instead of living
in one memory, depots load and dump ions (so an ion may be born and may leave the
computation), and "no block goes a cycle without extraction" becomes a coherence budget
between the ops that extract syndromes.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict

from .checks import ION_LOSS_QUANTA
from .library import LeafLibrary
from .model import Library

__all__ = ["check_places", "PLACE_RULES", "SE_OPS", "COHERENCE_BUDGET_US",
           "CLASSICAL_FAMILIES"]

PLACE_RULES = {
    "G1": "ports on a channel agree; every bundle arrives in the order its place takes it in",
    "G2": "every ion leaves only where it is; residents end at home; loaded ions end dumped "
          "or in a surviving block",
    "G3": "no place ever holds more ions than its capacity, or fewer than none",
    "G4": "a channel never holds more ions than it has sites, nor carries two ways at once",
    "G5": "a place runs one op at a time",
    "G6": "no ion gets hotter than the ion-loss limit, inside a place, crossing a door, or on its "
          "way between places: its n̄ is followed through every op, road and junction",
    "G7": "every instruction is realised, and each block's instructions run in program order",
    "G8": "no block goes longer than its coherence budget without syndrome extraction",
    "G9": "every op the schedule uses is verified: hardware rules and logic (a classical "
          "op is modeled: its latency is a stated number, and its function is checked)",
    "G10": "every classical bit is used after it exists: a message leaves when its op has "
           "measured (a syndrome may wait in its place until the program calls the "
           "decoder), arrives one wire latency later, and nothing reads it before that",
    "G11": "the decoder is called and keeps up: every syndrome the machine measures is "
           "decoded, no decoding job waits for another past the next syndrome, and nothing "
           "reads an outcome or a frame before the decoder has written it",
    "H1": "the hardware between gadgets obeys the basic rules too: every carry is one program "
          "on its road's own device (a cycle one waveform, ions holding by their switches), it "
          "passes R1-R23, it delivers every ion before the far side takes it, and a door's stub "
          "is driven by one side at a time",
    "H2": "devices that share a plane (of DACs, or a laser) never play two waveforms at once: "
          "every cycle of every member -- each op's TSIR at its times, each road's compiled "
          "program -- either has the plane to itself or is the very same cycle",
}

#: the place families that hold no ions: their ops are modeled, not replayed
CLASSICAL_FAMILIES = ("decoder", "archive")

#: syndrome extraction happens inside these ops; between them a block only waits or moves
SE_OPS = ("prep.", "se.", "zz", "xx", "inject", "store", "refresh")
#: a trapped-ion memory keeps its coherence for seconds; one second is the budget here
COHERENCE_BUDGET_US = 1e6


def check_places(gir: dict, lib: Library, leaves: LeafLibrary | None = None, *,
                 budget_us: float = COHERENCE_BUDGET_US) -> dict:
    """Every place check on one schedule.

    A schedule may also be one gate's own rows in a folded run (`qccd.gadget.fold`, its
    `view`), which says two more things, both absent from a whole program:

    * `initial`: ions that are somewhere when it starts -- the blocks it was passed, resting
      in their homes.  They start there (G2, G3) and must end there (G2).
    * `boxes`: the calls it makes, `{ins, block, t0, t1, covered}`, each the stretch a block is
      the call's (G7 orders it like an instruction) and, until `covered`, extracted by the
      call (G8: the call's own view checks inside it).  A view's edge -- its start, for a
      block passed in, or a call's start or end -- is shared with the view on the other side,
      so a gap that touches one may be at most half the budget: the two halves add up to at
      most one.
    * `until` on a block: where this view stops answering for it (a block passed in, once it
      is home again; its caller answers from there)."""
    out = {"passed": [], "failed": [], "skipped": {}, "violations": {}, "metrics": {}}
    initial = {int(g): p for g, p in (gir.get("initial") or {}).items()}
    boxes = gir.get("boxes") or []

    def verdict(rule, violations, skipped=None):
        if skipped:
            out["skipped"][rule] = skipped
        elif violations:
            out["failed"].append(rule)
            out["violations"][rule] = violations[:50] + (
                [f"... and {len(violations) - 50} more"] if len(violations) > 50 else [])
        else:
            out["passed"].append(rule)

    nets, events, carries = gir["nets"], gir["events"], gir["carries"]
    masters = {p: lib[v[0]] for p, v in gir["leaves"].items()}

    def dst_of(c):
        a, _, b, _, _ = nets[c[1]][:5]
        return b if c[2] == a else a

    def dst_port(c):
        a, pa, b, pb, _ = nets[c[1]][:5]
        return pb if c[2] == a else pa

    # -- G1: structure, and each bundle's order at a door is the order the op binds --------
    g1 = list(lib.structure_violations())
    # ion by ion: each ion an op takes in is on a carry that ends at that door, taken off it
    # when the op's replay takes it, and the op takes the carry's ions in the carry's order
    # (a junction takes a bundle one ion at a time: one lone pass per ion)
    by_ion: dict[tuple, list] = {}
    for c in carries:
        for k, g in enumerate(c[7]):
            by_ion.setdefault((dst_of(c), dst_port(c), c[9], g), []).append((c, k))
    for e in events:
        op = masters[e[3]].ops.get(e[4])
        if op is None:
            continue
        bound = {nm: gid for nm, gid in e[8]}
        for tr in op.transfers:
            if tr.dir != "in":
                continue
            got = []
            for nm, t in zip(tr.ions, tr.times_us):
                hit = [(c, k) for c, k in by_ion.get((e[3], tr.port, e[6], bound.get(nm)), [])
                       if abs(c[4] + c[12][k] - (e[1] + t)) < 1.0]
                if hit:
                    got.append(hit[0])
            if len(got) < len(tr.ions):
                g1.append(f"{e[3]} {e[4]} (instruction {e[6]}): nothing arrives on {tr.port}")
            elif len({id(c) for c, _ in got}) > 1 or [k for _, k in got] != sorted(k for _, k in got):
                g1.append(f"{e[3]} {e[4]} (instruction {e[6]}): the bundle on {tr.port} "
                          f"arrives in a different order than the op takes it")
    verdict("G1", g1)

    # -- G2: follow every ion ----------------------------------------------------------------
    home = {}
    for path, (mname, _, _, first) in gir["leaves"].items():
        for k in range(len(gir["ion_order"][mname])):
            home[first + k] = path
    loc: dict[int, str | None] = dict(home)
    loc.update(initial)
    marks = []
    for e in events:
        if masters[e[3]].family == "depot" and e[4].startswith("load."):
            for _, gid in e[8]:
                marks.append((e[1], -1, gid, "born", e[3]))
    for c in carries:
        for gid, dep, take in zip(c[7], c[5], c[12]):
            marks.append((c[4] + dep, 1, gid, "leave", c))
            marks.append((c[4] + take, 0, gid, "arrive", c))
    g2 = []
    for t, _, gid, what, x in sorted(marks, key=lambda m: (m[0], m[1])):
        if what == "born":
            if gid in loc:
                g2.append(f"ion {gid} is loaded at {x} but already exists")
            loc[gid] = x
        elif what == "leave":
            if loc.get(gid) != x[2]:
                where = "in transit" if loc.get(gid) is None else f"at {loc.get(gid)}"
                g2.append(f"carry {x[0]} on {x[1]} takes ion {gid} from {x[2]} at {t:.0f} us, "
                          f"but it is {where}")
            loc[gid] = None
        else:
            if loc.get(gid) is not None:
                g2.append(f"ion {gid} arrives at {dst_of(x)} without having left {loc.get(gid)}")
            loc[gid] = dst_of(x)
    away = sorted(g for g in home if loc.get(g) != home[g])
    if away:
        g2.append(f"{len(away)} resident ion(s) end away from home (e.g. ion {away[0]})")
    # (a block passed in that the gate read out ends dumped, at a depot: a readout's ions do)
    strays = sorted(g for g in initial if loc.get(g) != initial[g]
                    and not (loc.get(g) is not None and masters[loc[g]].family == "depot"))
    if strays:
        g2.append(f"{len(strays)} ion(s) of a block passed in end away from its home "
                  f"(e.g. ion {strays[0]} at {loc.get(strays[0])}, not {initial[strays[0]]})")
    alive = {g for b in gir["blocks"].values() if "read" not in b for g in b["ions"]}
    for gid, where in loc.items():
        if gid in home or gid in alive:
            continue
        if where is None or masters[where].family != "depot":
            g2.append(f"ion {gid} was loaded but ends at {where}, not dumped")
            break
    verdict("G2", g2)

    # -- G3: occupancy (depots are sources and sinks and are not counted) ----------------------
    delta = defaultdict(list)
    for c in carries:
        for dep, take in zip(c[5], c[12]):
            delta[c[2]].append((c[4] + dep, -1))
            delta[dst_of(c)].append((c[4] + take, +1))
    g3 = []
    start_count = defaultdict(int)
    for p in initial.values():
        start_count[p] += 1
    for path, (mname, _, _, _) in gir["leaves"].items():
        if masters[path].family == "depot":
            continue
        level = best = low = len(gir["ion_order"][mname]) + start_count[path]
        for _, d in sorted(delta.get(path, []), key=lambda x: (x[0], -x[1])):
            level += d
            best, low = max(best, level), min(low, level)
        if best > masters[path].capacity:
            g3.append(f"{path} holds {best} ions, capacity {masters[path].capacity}")
        if low < 0:
            g3.append(f"{path} gives away more ions than it holds ({low})")
    verdict("G3", g3)

    # -- G4 ----------------------------------------------------------------------------------
    # the ions on a road's SITES, as its own compiled program has them (roads.py): an ion
    # waiting on a door's stub -- for the far side to take it, or for a shared plane -- is on
    # the stub, whose room is the bundle's.  Without a leaf library, the coarse count: every
    # ion from when it is put on the near stub until it is taken off the far one.
    roads = None
    if leaves is not None and carries:
        from .roads import check_roads
        roads = check_roads(gir, lib, leaves)
    g4, worst = [], {}
    per_net = defaultdict(list)
    for c in carries:
        per_net[c[1]].append(c)
    for net, cs in per_net.items():
        level = hi = 0
        ms = []
        for c in cs:
            run = roads["runs"].get(c[0]) if roads else None
            if run is not None:
                hi = max(hi, run.peak)
                continue
            for dep, take in zip(c[5], c[12]):
                ms += [(c[4] + dep, 1), (c[4] + take, -1)]
        for _, d in sorted(ms, key=lambda x: (x[0], x[1])):
            level += d
            hi = max(hi, level)
        worst[net] = hi
        if hi > nets[net][4]:
            g4.append(f"{net} holds {hi} ions at once but has {nets[net][4]} sites")
        spans = sorted((c[4], c[11], c[2]) for c in cs)
        for (a0, a1, sa), (b0, b1, sb) in zip(spans, spans[1:]):
            if sa != sb and b0 < a1 - 1e-6:
                g4.append(f"{net} carries ions both ways at once ({a0:.0f}-{a1:.0f} us from "
                          f"{sa}, {b0:.0f}-{b1:.0f} us from {sb})")
    out["metrics"]["peak_channel_occupancy"] = max(worst.values(), default=0)
    verdict("G4", g4)

    # -- G5 ----------------------------------------------------------------------------------
    g5 = []
    per_leaf = defaultdict(list)
    for e in events:
        if e[2] > e[1]:
            per_leaf[e[3]].append(e)
    for path, es in per_leaf.items():
        es.sort(key=lambda e: e[1])
        for a, b in zip(es, es[1:]):
            if b[1] < a[2] - 1e-6:
                g5.append(f"{path}: {a[4]} ({a[1]:.0f}-{a[2]:.0f} us) overlaps {b[4]} "
                          f"({b[1]:.0f}-{b[2]:.0f} us)")
    verdict("G5", g5)

    # -- G6 ----------------------------------------------------------------------------------
    g6, hottest, peak = [], 0.0, 0.0
    seen6 = set()
    for e in events:
        op = masters[e[3]].ops.get(e[4])
        if op is None or (e[3], e[4]) in seen6:
            continue
        seen6.add((e[3], e[4]))
        for tr in op.transfers:
            if tr.dir == "out":
                hottest = max(hottest, tr.quanta)
                if tr.quanta > ION_LOSS_QUANTA:
                    g6.append(f"{e[3]} {e[4]}: ions leave {tr.port} at n̄ = {tr.quanta:.0f}, "
                              f"past the ion-loss limit {ION_LOSS_QUANTA:.0f}")
        q = float(op.metrics.get("peak_quanta", 0.0))
        peak = max(peak, q)
        if q > ION_LOSS_QUANTA:
            g6.append(f"{e[3]} {e[4]}: an ion reaches n̄ = {q:.0f} inside the op, past the "
                      f"ion-loss limit {ION_LOSS_QUANTA:.0f}")
    out["metrics"]["hottest_door_crossing_quanta"] = round(hottest, 2)
    out["metrics"]["hottest_ion_quanta"] = round(peak, 2)
    # ... and across the town: every ion's n̄ followed from op to road to junction to op, each
    # op replayed per ion from the n̄ its ions really arrive at (heat.py).  An op alone, from
    # cold, cannot see a bundle that crossed twenty junctions to reach it.
    if leaves is not None:
        from .heat import follow, op_heat
        start = 0.0
        for p in set(initial.values()):
            hs = op_heat(leaves, masters[p], "refresh") if p in masters else None
            if hs:
                start = max(start, max(h.out for h in hs.values()))
        followed = follow(gir, lib, leaves, initial_heat=start)
        g6 += followed["violations"][:20] + (
            [f"... {len(followed['violations']) - 20} more"] if len(followed["violations"]) > 20 else [])
        out["metrics"]["heat"] = followed["metrics"]
        out["metrics"]["hottest_ion_quanta"] = round(max(peak, followed["metrics"]["hottest_ion_quanta"]), 2)
        hot_gates = followed["hot_gates"]
    else:
        hot_gates = []
    verdict("G6", g6)

    # -- G7 ----------------------------------------------------------------------------------
    refused = {i for i, _ in gir["refused"]}
    g7 = [f"instruction {i} not realised: {why}" for i, why in gir["refused"]]
    span: dict[int, list] = {}
    for e in events:
        # only what happens to ions orders the program: the classical half deliberately
        # overlaps it (a guard is resolved while the op that measured its bits is still
        # letting its ions out -- that is what the reaction time buys)
        if masters[e[3]].family in CLASSICAL_FAMILIES:
            continue
        s = span.setdefault(e[6], [e[1], e[2]])
        s[0], s[1] = min(s[0], e[1]), max(s[1], e[2])
    last: dict[str, tuple[int, float]] = {}
    realised = {e[6] for e in events}          # a software Pauli is realised in the archive
    # a call is realised by its own view; here it is the stretch each block is the call's
    box_of = {(x["ins"], x["block"]): (x["t0"], x["t1"]) for x in boxes}
    # (where the call stopped touching the block's ions: a call may still owe it a Pauli
    # frame, a line in the archive, after the block is home again)
    held = {(x["ins"], x["block"]): x.get("covered", x["t1"]) for x in boxes}
    realised |= {x["ins"] for x in boxes}
    for ins in gir["program"]["instructions"]:
        if ins["id"] not in realised and ins["id"] not in refused:
            g7.append(f"instruction {ins['id']} (line {ins['line']}) has no event")
        if ins["id"] not in span and not any((ins["id"], b) in box_of for b in ins["blocks"]):
            continue
        for b in ins["blocks"]:
            sp = box_of.get((ins["id"], b)) or span.get(ins["id"])
            if sp is None:
                continue
            prev = last.get(b)
            # a refresh round only extracts syndromes on a block at rest: it commutes with the
            # Pauli frame a call still owes the block, so it need only wait for its ions
            end = prev[2] if prev and ins.get("refresh") and prev[2] is not None else (prev[1] if prev else 0)
            if prev and sp[0] < end - 1e-6:
                g7.append(f"block {b}: instruction {ins['id']} starts before instruction "
                          f"{prev[0]} is done")
            last[b] = (ins["id"], sp[1], held.get((ins["id"], b)))
    verdict("G7", g7)

    # -- G8 ----------------------------------------------------------------------------------
    g8, longest = [], 0.0
    covered = defaultdict(list)
    for x in boxes:
        covered[x["block"]].append((x["t0"], x["covered"]))
    # every op that extracts syndromes, under each block whose ions it binds: one pass over
    # the events, not one per block (a folded run's top view holds every round of its holds)
    owner = defaultdict(list)
    for b, info in gir["blocks"].items():
        for g in info["ions"]:
            owner[g].append(b)
    se_of = defaultdict(list)
    for e in events:
        if not e[4].startswith(SE_OPS):
            continue
        hit = set()
        for _, g in e[8]:
            if isinstance(g, int):
                hit.update(owner.get(g, ()))
        for b in hit:
            se_of[b].append((e[1], e[2], False))
    for b, info in gir["blocks"].items():
        # (start, end, an edge this view shares with another)
        times = sorted(se_of.get(b, []) + [(a, z, True) for a, z in covered.get(b, [])])
        end = info.get("until", info.get("read", gir["makespan_us"]))
        passed_in = info.get("param") is not None
        if passed_in:
            times = [(0.0, 0.0, True)] + times
        t, edge = (times[0][1], times[0][2]) if times else (info["born"], False)
        for a, z, e2 in times[1:] + [(end, end, False)]:
            gap = a - t
            longest = max(longest, gap)
            limit = budget_us / 2 if (edge or e2) else budget_us
            if gap > limit:
                g8.append(f"block {b} waits {gap / 1000:.1f} ms without syndrome extraction "
                          f"(budget {limit / 1000:.0f} ms" + (", half of it at a call's edge)" if limit < budget_us else ")"))
            if z >= t:
                t, edge = z, e2
    out["metrics"]["longest_unprotected_us"] = round(longest, 3)
    out["metrics"]["coherence_budget_us"] = budget_us
    verdict("G8", g8)

    # -- G9 ----------------------------------------------------------------------------------
    g9, used = [], set()
    for e in events:
        if e[4] in masters[e[3]].ops:
            used.add((masters[e[3]].name, e[4]))
    for mname, op in sorted(used):
        o = lib[mname].ops[op]
        classical = lib[mname].family in CLASSICAL_FAMILIES
        want = ("verified", "certified") + (("modeled",) if classical else ())
        if o.status not in want:
            g9.append(f"{mname}.{op} is {o.status}")
        elif (o.logic or {}).get("status") == "failed":
            g9.append(f"{mname}.{op}: its logic check failed")
    out["metrics"]["ops_used"] = len(used)
    out["metrics"]["classical_ops_used"] = sum(
        1 for mname, _ in used if lib[mname].family in CLASSICAL_FAMILIES)
    # an op is verified for ions that arrive cold; one whose two-qubit gate meets an ion still
    # carrying its trip (no cool between) is not verified for the run it is in
    g9 += hot_gates[:20]
    verdict("G9", g9)

    # -- G10: the classical half's causality --------------------------------------------------
    messages = gir.get("messages") or []
    wires = gir.get("wires") or {}
    kind_of = {i["id"]: i["kind"] for i in gir["program"]["instructions"]}
    by_id = {e[0]: e for e in events}
    ends = {e[0]: e[2] for e in events}
    g10 = []
    if messages or wires:
        # a syndrome or a frame is consumed by a classical place; a guard is consumed by the
        # classically controlled op at an ordinary place, so both are indexed: the latest
        # start of anything that place runs in each instruction
        last_start: dict = {}
        for e in events:
            k = (e[3], e[6])
            if e[1] > last_start.get(k, -1e300):
                last_start[k] = e[1]
        for m in messages:
            mid, wire, sl, sp, dl, dp, t0, t1 = m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7]
            w = wires.get(wire)
            if w is None:
                g10.append(f"message {mid} crosses {wire}, which the design does not have")
                continue
            if {(w[0], w[1]), (w[2], w[3])} != {(sl, sp), (dl, dp)}:
                g10.append(f"message {mid} on {wire} does not run between that wire's ports")
            lat = float(w[4])
            if abs((t1 - t0) - lat) > 1e-6:
                g10.append(f"message {mid} on {wire} takes {t1 - t0:.3f} us, but the wire's "
                           f"latency is {lat:.3f} us")
            src = by_id.get(m[13]) if len(m) > 13 else None
            if src is not None and t0 < src[1] - 1e-6:
                g10.append(f"message {mid} leaves {sl} at {t0:.1f} us, before the op that "
                           f"measured those bits started ({src[1]:.1f} us)")
            # a syndrome is the one message that may leave after its op: it waits in the
            # place's readout buffer until a `decode` instruction sends it
            held = m[9] == "syndrome" and kind_of.get(m[10]) == "decode"
            if (src is not None and t0 > src[2] + 1e-6 and not held
                    and masters[sl].family not in CLASSICAL_FAMILIES):
                g10.append(f"message {mid} leaves {sl} at {t0:.1f} us, after its op ended "
                           f"({src[2]:.1f} us)")
            if last_start.get((dl, m[10]), -1e300) < t1 - 1e-6:
                g10.append(f"message {mid} arrives at {dl} at {t1:.1f} us, but nothing there "
                           f"runs after it in instruction {m[10]}")
        # a bit may be read only after the op that measured it began: the archive records an
        # outcome while the ions are still leaving the place, which is the point of the
        # reaction time, so the op's END is not the deadline -- its measurement is
        starts = {e[0]: e[1] for e in events}
        for f in gir.get("frames") or []:
            for key in ("ax", "az"):
                for eid, _flow in (f.get(key) or {}).get("sources") or []:
                    # (a gate's own view in a folded run also names its caller's frames and
                    # its calls' outcomes: those are checked in the views they belong to)
                    if isinstance(eid, str):
                        continue
                    if starts.get(int(eid), 0.0) > f["t"] + 1e-6:
                        g10.append(f"frame row {f['id']} at {f['t']:.1f} us names an outcome "
                                   f"from event {eid}, which only starts at "
                                   f"{starts.get(int(eid), 0.0):.1f} us")
        for o in gir.get("outcomes") or []:
            for eid, _flow in ((o.get("value") or {}).get("sources") or []):
                if isinstance(eid, str):
                    continue
                if starts.get(int(eid), 0.0) > (o.get("recorded_us") or 0.0) + 1e-6:
                    g10.append(f"outcome of instruction {o['instruction']} was recorded "
                               f"before the measurement it depends on (event {eid})")
        out["metrics"]["messages"] = len(messages)
        out["metrics"]["classical_bits"] = sum(int(m[8]) for m in messages)
    verdict("G10", g10, skipped=None if (messages or wires) else
            "this design has no classical wires")

    # -- G11: the decoder keeps up ------------------------------------------------------------
    g11 = []
    decoder = (gir.get("control") or {}).get("decoder")
    jobs = sorted((e for e in events if e[3] == decoder and e[4] == "decode"),
                  key=lambda e: e[1])
    arrivals = sorted((m[7], m[0], m[10]) for m in messages if m[9] == "syndrome")
    if decoder and jobs:
        # each job decodes one syndrome: the one its own instruction sent, in arrival order
        queue: dict = defaultdict(list)
        for a in arrivals:
            queue[a[2]].append(a)
        times = sorted(a[0] for a in arrivals)
        waits = []
        for k, e in enumerate(jobs):
            mine = queue.get(e[6])
            if not mine:
                g11.append(f"decoding job at {e[1]:.1f} us belongs to instruction {e[6]}, "
                           f"which sent no syndrome")
                continue
            ready = mine.pop(0)[0]
            waits.append(e[1] - ready)
            if e[1] < ready - 1e-6:
                g11.append(f"decoding job at {e[1]:.1f} us starts before its syndrome "
                           f"arrived ({ready:.1f} us)")
            nxt = bisect_right(times, ready + 1e-6)
            later = times[nxt:nxt + 1]
            if later and e[1] > later[0] + 1e-6:
                g11.append(f"a decoding job waits from {ready:.1f} us to {e[1]:.1f} us, past "
                           f"the arrival of the next syndrome ({later[0]:.1f} us): the "
                           f"decoder is falling behind")
        out["metrics"]["decode_jobs"] = len(jobs)
        out["metrics"]["worst_decode_wait_us"] = round(max(waits or [0.0]), 3)
        lat = (gir.get("control") or {}).get("latency_us") or {}
        out["metrics"]["reaction_us"] = round(
            float(lat.get("link_us", 0)) + float(lat.get("lookup_us", 0))
            + float(lat.get("write_us", 0)), 3)
    # a classically controlled op may not start before its guard is at the door
    guarded = 0
    decisions = defaultdict(list)
    for m in messages:
        if m[9] == "decision":
            decisions[(m[4], m[10])].append(m)
    for e in events:
        g = e[10] if len(e) > 10 else None
        if not g:
            continue
        guarded += 1
        ready = float(g.get("ready_us", 0.0))
        if e[1] < ready - 1e-6:
            g11.append(f"{e[3]} {e[4]} is guarded by ({g.get('guard')}) but starts at "
                       f"{e[1]:.1f} us, before the guard reaches it ({ready:.1f} us)")
        wanted = decisions.get((e[3], e[6]), [])
        if not wanted:
            g11.append(f"{e[3]} {e[4]} is guarded by ({g.get('guard')}) but no decision "
                       f"wire tells it so")
        elif min(m[7] for m in wanted) > e[1] + 1e-6:
            g11.append(f"{e[3]} {e[4]} starts at {e[1]:.1f} us but the guard only arrives "
                       f"at {min(m[7] for m in wanted):.1f} us")
    out["metrics"]["guarded_ops"] = guarded

    # every syndrome is decoded -- the program has to ask -- and nothing reads an outcome
    # before its syndrome has been: a logical outcome is a parity the decoder corrects
    windows = gir.get("windows") or []
    written = {}
    carried = defaultdict(list)
    for m in messages:
        if m[9] == "syndrome" and len(m) > 13:
            carried[(m[2], m[10], m[13])].append(m)
    for w in windows:
        if w.get("decoded_by") is None:
            g11.append(f"the syndrome {w['op']} measured at {w['place']} for "
                       f"{' '.join(w['blocks']) or 'its block'} (instruction "
                       f"{w['instruction']}) is never decoded: the program must call "
                       f"`decode` on it")
            continue
        sent = carried.get((w["place"], w["decoded_by"], w["event"]), [])
        if not sent:
            g11.append(f"instruction {w['decoded_by']} decodes the syndrome {w['op']} left at "
                       f"{w['place']}, but no syndrome message carries it to the decoder")
        elif min(m[6] for m in sent) < w["measured_us"] - 1e-6:
            g11.append(f"the syndrome {w['op']} left at {w['place']} is sent at "
                       f"{min(m[6] for m in sent):.1f} us, before it was measured "
                       f"({w['measured_us']:.1f} us)")
        written[w["event"]] = float(w.get("written_us", 0.0))
    ready_of = {}
    for o in gir.get("outcomes") or []:
        if o.get("cvar"):
            ready_of[o["cvar"]] = o.get("event")
    for e in events:
        g = e[10] if len(e) > 10 else None
        if not g:
            continue
        for name in (g.get("cond") or {}).get("cvars") or []:
            ev = ready_of.get(name)
            if ev in written and written[ev] > float(g.get("ready_us", 0.0)) + 1e-6:
                g11.append(f"{e[3]} {e[4]} is guarded by ({g.get('guard')}), which reads "
                           f"{name} before the decoder has corrected it "
                           f"({written[ev]:.1f} us)")
    for f in gir.get("frames") or []:
        for name in (f.get("guard") or {}).get("cvars") or []:
            ev = ready_of.get(name)
            if ev in written and written[ev] > float(f["t"]) + 1e-6:
                g11.append(f"frame row {f['id']} is written when {name} holds, at "
                           f"{f['t']:.1f} us, before the decoder has corrected {name} "
                           f"({written[ev]:.1f} us)")
    out["metrics"]["syndrome_windows"] = len(windows)
    out["metrics"]["undecoded_windows"] = sum(1 for w in windows if w.get("decoded_by") is None)
    verdict("G11", g11, skipped=None if (decoder or guarded)
            else "this design has no decoder and no conditional op")

    # -- H1: the basic rules on the hardware between the gadgets -----------------------------
    if leaves is not None:
        # (a schedule with no carry -- a gate's view that only calls others -- has nothing on
        # its roads to break the rules with: H1 holds there, as G4 does)
        from .roads import check_roads, machine_census
        roads = roads or check_roads(gir, lib, leaves)
        out["metrics"]["roads"] = roads["metrics"]
        out["metrics"]["hardware"] = machine_census(gir, lib, leaves)
        verdict("H1", roads["violations"][:20] + (
            [f"... {len(roads['violations']) - 20} more"] if len(roads["violations"]) > 20 else []))
        # -- H2: devices that share a plane take turns --------------------------------------
        if gir.get("planes"):
            from .planes import check_planes
            pl = check_planes(gir, lib, leaves, runs=roads["runs"])
            out["metrics"]["planes"] = pl["metrics"]
            verdict("H2", pl["violations"])
        else:
            # no device shares a plane: each drives its own, one waveform at a time by its
            # own program (H1, the leaves' checks) -- H2 holds there, as the machine rule says
            verdict("H2", [])
    else:
        verdict("H1", [], skipped="no leaf library to compile the roads with")
        verdict("H2", [], skipped="no leaf library to read the ops' cycles from")
    return out
