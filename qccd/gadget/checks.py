"""G1-G9: what the hierarchy promises, checked against the schedule it produced.

GADGETS.md §8.  Synthesis is built to satisfy every one of these; the checks do not trust
that.  Each rule re-derives its verdict from the GIR alone (plus the library the GIR was
built from), under the repository's reporting contract: a rule is *passed* only if the
check ran and found nothing, *failed* with its violations, or *skipped* with the reason.

The most useful one is G2, which follows every ion: from its home instance, along every
carry in time order (a carry may only take an ion from where the ion currently is), and
back.  An ion that teleports, doubles, or never comes home is a bug in synthesis or in a
leaf's transfer record, and nothing else would notice it.
"""

from __future__ import annotations

from collections import defaultdict

from ..compile.cooling import ION_LOSS_ROUND_TRIPS
from .library import LeafLibrary
from .model import Library

__all__ = ["check", "RULES"]

RULES = {
    "G1": "ports on a channel agree; a paired bundle arrives in the order the station pairs",
    "G2": "every ion leaves only where it has arrived, and ends where it started",
    "G3": "no instance ever holds more ions than its capacity, or fewer than none",
    "G4": "a channel never holds more ions than it has sites, nor carries two ways at once",
    "G5": "an instance runs one op at a time",
    "G6": "no ion crosses a port hotter than the ion-loss limit",
    "G7": "every logical instruction is discharged, in program order on each block",
    "G8": "no block goes longer than one cycle without extraction outside a logical op",
    "G9": "every leaf op the schedule uses is verified (or certified)",
}

#: ~85 uncooled junction round trips before an ion is lost (arXiv:1210.3655), at the
#: 3 quanta per transit the corrected model charges a degree-3 junction
ION_LOSS_QUANTA = 2 * ION_LOSS_ROUND_TRIPS * 3.0


def check(gir: dict, lib: Library, leaves: LeafLibrary) -> dict:
    out = {"passed": [], "failed": [], "skipped": {}, "violations": {}, "metrics": {}}

    def verdict(rule, violations, skipped=None):
        if skipped:
            out["skipped"][rule] = skipped
        elif violations:
            out["failed"].append(rule)
            out["violations"][rule] = violations[:50] + (
                [f"... and {len(violations) - 50} more"] if len(violations) > 50 else [])
        else:
            out["passed"].append(rule)

    leaves_by_path = gir["leaves"]
    nets = gir["nets"]
    masters = {p: lib[v[0]] for p, v in leaves_by_path.items()}
    events = gir["events"]
    carries = gir["carries"]

    # -- G1 --------------------------------------------------------------------------------
    g1 = list(lib.structure_violations())
    by_group = defaultdict(list)
    for c in carries:
        by_group[(c[10], c[1])].append(c)
    for e in events:
        m = masters[e[3]]
        if m.family == "station" and e[8]:
            n = len(e[8]) // 2
            for c in carries:
                if c[10] == e[7] and nets[c[1]][0] != e[3] and nets[c[1]][2] != e[3]:
                    continue
                if c[10] == e[7] and c[2] != e[3]:
                    port = nets[c[1]][1] if nets[c[1]][0] == e[3] else nets[c[1]][3]
                    want = [g for _, g in (e[8][:n] if port == "a" else e[8][n:])]
                    if c[7] != want:
                        g1.append(f"{e[3]} {e[4]} (instruction {e[6]}): the bundle on port "
                                  f"{port} arrives in a different order than the station pairs")
        if m.family == "memory" and e[4] == "absorb":
            want = [leaves_by_path[e[3]][3] + gir["ion_order"][m.name].index(nm)
                    for nm in m.ops["absorb"].transfer("bus", "in").ions]
            back = [c for c in carries if c[10] == e[7] and c[1] == _net_of(nets, e[3], "bus")
                    and c[2] != e[3]]
            if back and back[0][7] != want:
                g1.append(f"{e[3]} absorb (instruction {e[6]}): the bundle comes back in a "
                          f"different order than the ring expects")
    verdict("G1", g1)

    # -- G2 --------------------------------------------------------------------------------
    # ion by ion, in time: an ion leaves an instance only once it has arrived there, and is
    # taken in by the far end only after it left; at the end every ion is home again
    home = {}
    for path, (mname, _, _, first) in leaves_by_path.items():
        for k in range(len(gir["ion_order"][mname])):
            home[first + k] = path
    moves = []
    for c in carries:
        a_leaf, _, b_leaf, _, _ = nets[c[1]]
        src = c[2]
        dst = b_leaf if src == a_leaf else a_leaf
        for gid, dep, take in zip(c[7], c[5], c[12]):
            if take < dep - 1e-6:
                moves.append((c[4] + dep, 1, gid, "bad", c))
            moves.append((c[4] + dep, 1, gid, src, c))       # leaves src
            moves.append((c[4] + take, 0, gid, dst, c))      # arrives at dst (sorts first)
    loc = dict(home)
    g2 = []
    for t, order, gid, where, c in sorted(moves, key=lambda m: (m[0], m[1], m[4][0])):
        if where == "bad":
            g2.append(f"carry {c[0]} on {c[1]}: ion {gid} is taken in before it left")
            continue
        if order == 1:
            if loc.get(gid) != where:
                g2.append(f"carry {c[0]} on {c[1]} takes ion {gid} from {where} at "
                          f"{t:.0f} us, but it is {'in transit' if loc.get(gid) is None else 'at ' + str(loc.get(gid))}")
            loc[gid] = None                                   # in the channel
        else:
            if loc.get(gid) is not None:
                g2.append(f"ion {gid} arrives at {where} at {t:.0f} us without having left "
                          f"{loc.get(gid)}")
            loc[gid] = where
    away = sorted(g for g in home if loc.get(g) != home[g])
    if away:
        g2.append(f"{len(away)} ion(s) end away from home (e.g. ion {away[0]} at "
                  f"{loc.get(away[0])}, home {home[away[0]]})")
    verdict("G2", g2)

    # -- G3 --------------------------------------------------------------------------------
    delta = defaultdict(list)
    for c in carries:
        a_leaf, _, b_leaf, _, _ = nets[c[1]]
        src = c[2]
        dst = b_leaf if src == a_leaf else a_leaf
        for dep, take in zip(c[5], c[12]):
            delta[src].append((c[4] + dep, -1))
            delta[dst].append((c[4] + take, +1))
    g3 = []
    peak = {}
    for path, (mname, _, _, _) in leaves_by_path.items():
        level = len(gir["ion_order"][mname])
        best = worst_low = level
        for t, d in sorted(delta.get(path, []), key=lambda x: (x[0], -x[1])):
            level += d
            best = max(best, level)
            worst_low = min(worst_low, level)
        peak[path] = best
        cap = masters[path].capacity
        if best > cap:
            g3.append(f"{path} holds {best} ions but its capacity is {cap}")
        if worst_low < 0:
            g3.append(f"{path} gives away more ions than it holds ({worst_low})")
    verdict("G3", g3)

    # -- G4 --------------------------------------------------------------------------------
    g4 = []
    per_net = defaultdict(list)
    for c in carries:
        per_net[c[1]].append(c)
    worst = {}
    for net, cs in per_net.items():
        L = nets[net][4]
        marks = []
        for c in cs:
            for dep, take in zip(c[5], c[12]):
                marks.append((c[4] + dep, 1))
                marks.append((c[4] + take, -1))
        level = hi = 0
        for _, d in sorted(marks, key=lambda x: (x[0], x[1])):
            level += d
            hi = max(hi, level)
        worst[net] = hi
        if hi > L:
            g4.append(f"{net} holds {hi} ions at once but has {L} sites")
        spans = sorted((c[4], c[11], c[2]) for c in cs)
        for (a0, a1, sa), (b0, b1, sb) in zip(spans, spans[1:]):
            if sa != sb and b0 < a1 - 1e-6:
                g4.append(f"{net} carries ions both ways at once ({a0:.0f}-{a1:.0f} us from "
                          f"{sa}, {b0:.0f}-{b1:.0f} us from {sb})")
    verdict("G4", g4)

    # -- G5 --------------------------------------------------------------------------------
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

    # -- G6 --------------------------------------------------------------------------------
    g6, hottest = [], 0.0
    for e in events:
        m = masters[e[3]]
        op = m.ops.get(e[4])
        if op is None:
            continue
        for tr in op.transfers:
            if tr.dir == "out":
                hottest = max(hottest, tr.quanta)
                if tr.quanta > ION_LOSS_QUANTA:
                    g6.append(f"{e[3]} {e[4]}: ions leave port {tr.port} at n̄ = "
                              f"{tr.quanta:.0f}, past the ion-loss limit {ION_LOSS_QUANTA:.0f}")
    out["metrics"]["hottest_port_crossing_quanta"] = round(hottest, 2)
    verdict("G6", g6)

    # -- G7 --------------------------------------------------------------------------------
    g7 = [f"instruction {i} not realised: {why}" for i, why in gir["refused"]]
    done = {e[6] for e in events if e[6] >= 0}
    refused = {i for i, _ in gir["refused"]}
    for ins in gir["program"]["instructions"]:
        if ins["id"] not in done and ins["id"] not in refused:
            g7.append(f"instruction {ins['id']} (line {ins['line']}) has no event")
    last_end: dict[str, tuple[int, float]] = {}
    for e in sorted((e for e in events if e[6] >= 0 and masters[e[3]].family == "memory"),
                    key=lambda e: (e[6], e[1])):
        prev = last_end.get(e[3])
        if prev and prev[0] < e[6] and e[1] < prev[1] - 1e-6 and e[2] > e[1]:
            g7.append(f"{e[3]}: instruction {e[6]} starts before instruction {prev[0]} ends")
        if not prev or e[2] >= prev[1] or prev[0] == e[6]:
            last_end[e[3]] = (e[6], max(e[2], prev[1] if prev and prev[0] == e[6] else e[2]))
    verdict("G7", g7)

    # -- G8 --------------------------------------------------------------------------------
    g8, longest = [], {}
    for path, m in masters.items():
        if m.family != "memory":
            continue
        C = m.ops["cycle"].duration_us
        spans = sorted((e[1], e[2]) for e in events if e[3] == path and e[2] > e[1])
        t, gap_max = 0.0, 0.0
        for a, b in spans:
            gap_max = max(gap_max, a - t)
            t = max(t, b)
        longest[path] = gap_max
        if gap_max > C + 1e-6:
            g8.append(f"{path} goes {gap_max / 1000:.2f} ms without a cycle or a logical op "
                      f"(one cycle is {C / 1000:.2f} ms)")
    out["metrics"]["longest_unprotected_us"] = round(max(longest.values(), default=0.0), 3)
    verdict("G8", g8)

    # -- G9 --------------------------------------------------------------------------------
    g9, used = [], set()
    for e in events:
        m = masters[e[3]]
        if e[4] in m.ops:
            used.add((m.name, e[4]))
    for mname, op in sorted(used):
        st = lib[mname].ops[op].status
        if st not in ("verified", "certified"):
            g9.append(f"{mname}.{op} is {st}")
    out["metrics"]["leaf_ops_used"] = len(used)
    verdict("G9", g9)

    out["metrics"]["peak_channel_occupancy"] = max(worst.values(), default=0)
    return out


def _net_of(nets: dict, leaf: str, port: str) -> str | None:
    for path, (a, pa, b, pb, _) in nets.items():
        if (a, pa) == (leaf, port) or (b, pb) == (leaf, port):
            return path
    return None
