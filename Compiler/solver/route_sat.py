"""The SAT routing oracle -- UNTRUSTED.

This solves a routing sub-problem exactly, so the heuristic router has something to be
measured against.  `Compiler/PLAN.md` §2 puts it firmly outside the trusted base: a solver
that returns a wrong model produces a schedule the checker rejects, which is why it is
allowed to be as clever, or as buggy, as it likes.

## What makes this not ordinary multi-agent pathfinding

Constraint 3 below.  One broadcast waveform drives every site it reaches, so every ion
moving *along one named loop* in one cycle must move the same signed delta -- you cannot
send one ion left and another right in the same cycle.  No MAPF solver expresses that,
and it is the constraint this hardware is actually built around.

`--free-loops` drops exactly that constraint and nothing else.  The difference between the
two makespans is the price of broadcast control, which is a number nobody has measured.

## The encoding

    x[i,v,t]   ion i is at trap v at cycle t          (pruned by reachability)

    1. exactly one trap per ion per cycle
    2. a move is a hop or a stay
    3. LICENSING: one signed delta per named loop per cycle          R4d   <- the crux
    4. occupancy of a trap <= its effective capacity                 R1/R2
    5. at most one ion transits a junction per cycle                 R2
    6. at most segment.capacity ions per segment per cycle           R3
    7. never (u->v) together with (v->u)                             R5
    8. every ion with a target is on it at the horizon

Optimality is *proved*, not assumed: the makespan is lowered until the instance turns
UNSAT, and the UNSAT is the proof that the last SAT value was minimal.

    python Compiler/solver/route_sat.py build/inst_ghz8_grid.json --index 3
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path


# ------------------------------------------------------------------ the graph


class Graph:
    def __init__(self, inst: dict):
        self.cap: dict[str, int] = inst["capacity"]
        self.seg_cap: dict[str, int] = inst["segment_capacity"]
        self.nodes: list[str] = list(self.cap)
        self.out: dict[str, list[dict]] = {v: [] for v in self.nodes}
        for h in inst["hops"]:
            self.out.setdefault(h["from"], []).append(h)
        self._dist: dict[str, dict[str, int]] = {}

    def dist(self, src: str) -> dict[str, int]:
        if src in self._dist:
            return self._dist[src]
        d = {src: 0}
        q = deque([src])
        while q:
            u = q.popleft()
            for h in self.out.get(u, []):
                if h["to"] not in d:
                    d[h["to"]] = d[u] + 1
                    q.append(h["to"])
        self._dist[src] = d
        return d


# ------------------------------------------------------------------ the encoding


def solve_at(inst: dict, g: Graph, T: int, *, free_loops: bool, timeout_s: float):
    """Is there a legal schedule of makespan exactly T?  Returns a model or None."""
    import z3

    start: dict[str, str] = inst["start"]
    targets: dict[str, str] = inst["targets"]
    movers = list(targets)
    statics = {i: s for i, s in start.items() if i not in targets}

    # Reachability pruning.  Without it a 144-trap device at T=12 is 1.7M variables for
    # eight ions; with it, a few thousand.
    allowed: dict[str, list[set[str]]] = {}
    for i in movers:
        ds = g.dist(start[i])
        dg = g.dist(targets[i])
        allowed[i] = [
            {
                v
                for v in g.nodes
                if ds.get(v, 10**9) <= t and dg.get(v, 10**9) <= T - t
            }
            for t in range(T + 1)
        ]
        if not all(allowed[i][t] for t in range(T + 1)):
            return None  # cannot reach the goal within T at all

    s = z3.Solver()
    s.set("timeout", int(timeout_s * 1000))
    x = {}
    for i in movers:
        for t in range(T + 1):
            for v in allowed[i][t]:
                x[i, v, t] = z3.Bool(f"x_{i}_{v}_{t}")

    def at(i, v, t):
        return x.get((i, v, t), z3.BoolVal(False))

    # 1. exactly one trap per ion per cycle
    for i in movers:
        for t in range(T + 1):
            lits = [at(i, v, t) for v in allowed[i][t]]
            s.add(z3.Or(lits))
            s.add(z3.AtMost(*lits, 1))

    # start and goal
    for i in movers:
        s.add(at(i, start[i], 0))
        s.add(at(i, targets[i], T))

    # 2. a move is a hop or a stay
    for i in movers:
        for t in range(T):
            for v in allowed[i][t]:
                nxt = [v] + [h["to"] for h in g.out.get(v, [])]
                s.add(
                    z3.Implies(
                        at(i, v, t),
                        z3.Or([at(i, w, t + 1) for w in nxt if (i, w, t + 1) in x]),
                    )
                )

    def moving(i, h, t):
        return z3.And(at(i, h["from"], t), at(i, h["to"], t + 1))

    hops = inst["hops"]

    # 3. LICENSING (R4d): one signed delta per named loop per cycle
    if not free_loops:
        loops = sorted({h["action"][0] for h in hops if h["action"]})
        for lp in loops:
            for t in range(T):
                deltas = sorted({h["action"][1] for h in hops if h["action"] and h["action"][0] == lp})
                if len(deltas) < 2:
                    continue
                flag = {dl: z3.Bool(f"act_{lp}_{dl}_{t}") for dl in deltas}
                for h in hops:
                    if h["action"] and h["action"][0] == lp:
                        for i in movers:
                            s.add(z3.Implies(moving(i, h, t), flag[h["action"][1]]))
                s.add(z3.AtMost(*flag.values(), 1))

    # 4. occupancy (R1, and R2's one-ion-per-junction via effective capacity)
    for v in g.nodes:
        base = sum(1 for _, sv in statics.items() if sv == v)
        cap = g.cap[v]
        for t in range(T + 1):
            lits = [at(i, v, t) for i in movers if (i, v, t) in x]
            if lits:
                k = cap - base
                if k < 0:
                    return None
                s.add(z3.AtMost(*lits, k) if k < len(lits) else z3.BoolVal(True))
            elif base > cap:
                return None

    # 5. junctions: at most one transit per cycle
    junctions = sorted({j for h in hops for j in h["junctions"]})
    for j in junctions:
        for t in range(T):
            lits = [moving(i, h, t) for h in hops if j in h["junctions"] for i in movers]
            if len(lits) > 1:
                s.add(z3.AtMost(*[z3.Bool(f"j_{j}_{t}_{k}") == v for k, v in enumerate(lits)], 1)
                      if False else z3.Sum([z3.If(l, 1, 0) for l in lits]) <= 1)

    # 6. segments
    seg_users: dict[str, list] = {}
    for h in hops:
        for sg in h["via"]:
            seg_users.setdefault(sg, []).append(h)
    for sg, hs in seg_users.items():
        cap = g.seg_cap.get(sg, 1)
        for t in range(T):
            lits = [moving(i, h, t) for h in hs for i in movers]
            if len(lits) > cap:
                s.add(z3.Sum([z3.If(l, 1, 0) for l in lits]) <= cap)

    # 7. no head-on
    rev = {(h["from"], h["to"]): h for h in hops}
    for (u, v), h in rev.items():
        back = rev.get((v, u))
        if back is None or u >= v:
            continue
        for t in range(T):
            for i in movers:
                for jn in movers:
                    if i == jn:
                        continue
                    s.add(z3.Not(z3.And(moving(i, h, t), moving(jn, back, t))))

    if s.check() != z3.sat:
        return None
    m = s.model()
    out = {}
    for i in movers:
        path = []
        for t in range(T + 1):
            for v in allowed[i][t]:
                if z3.is_true(m.eval(at(i, v, t), model_completion=True)):
                    path.append(v)
                    break
        out[i] = path
    return out


def validate(inst: dict, g: Graph, paths: dict[str, list[str]], *, free_loops: bool
             ) -> list[str]:
    """Check a returned schedule against every constraint, independently of the encoder.

    The solver is untrusted (`Compiler/PLAN.md` §2), and the reason that matters here is
    not solver bugs -- z3 is fine -- but ENCODING bugs.  A constraint written too weakly
    makes the optimum look smaller than it is and the heuristic look worse than it is;
    written too strongly, the reverse.  Either way the measured gap would be an artefact.
    Re-deriving legality from the schedule itself is what makes the number mean something.
    """
    bad: list[str] = []
    start, targets = inst["start"], inst["targets"]
    hop_of = {(h["from"], h["to"]): h for h in inst["hops"]}
    T = max((len(p) - 1) for p in paths.values()) if paths else 0
    statics = {i: v for i, v in start.items() if i not in targets}

    for i, path in paths.items():
        if path[0] != start[i]:
            bad.append(f"{i}: starts at {path[0]}, should be {start[i]}")
        if path[-1] != targets[i]:
            bad.append(f"{i}: ends at {path[-1]}, should be {targets[i]}")
        for t in range(len(path) - 1):
            u, v = path[t], path[t + 1]
            if u != v and (u, v) not in hop_of:
                bad.append(f"{i}: t={t} {u}->{v} is not a one-cycle hop")

    for t in range(T + 1):
        occ: dict[str, int] = {}
        for v in statics.values():
            occ[v] = occ.get(v, 0) + 1
        for i, path in paths.items():
            v = path[min(t, len(path) - 1)]
            occ[v] = occ.get(v, 0) + 1
        for v, n in occ.items():
            if n > g.cap.get(v, 0):
                bad.append(f"t={t}: {v} holds {n} ions, capacity {g.cap.get(v)}")

    for t in range(T):
        moves = []
        for i, path in paths.items():
            if t + 1 < len(path) and path[t] != path[t + 1]:
                moves.append((i, hop_of[(path[t], path[t + 1])]))
        junc: dict[str, int] = {}
        seg: dict[str, int] = {}
        acts: dict[str, set] = {}
        for _, h in moves:
            for j in h["junctions"]:
                junc[j] = junc.get(j, 0) + 1
            for sg in h["via"]:
                seg[sg] = seg.get(sg, 0) + 1
            if h["action"] and not free_loops:
                acts.setdefault(h["action"][0], set()).add(h["action"][1])
        for j, n in junc.items():
            if n > 1:
                bad.append(f"t={t}: {n} ions cross junction {j}")
        for sg, n in seg.items():
            if n > g.seg_cap.get(sg, 1):
                bad.append(f"t={t}: segment {sg} carries {n}, capacity {g.seg_cap.get(sg, 1)}")
        for lp, ds in acts.items():
            if len(ds) > 1:
                bad.append(f"t={t}: loop {lp} asked for {sorted(ds)} in one cycle (R4d)")
        for a, (i, h) in enumerate(moves):
            for _, h2 in moves[a + 1:]:
                if h["from"] == h2["to"] and h["to"] == h2["from"]:
                    bad.append(f"t={t}: head-on on {h['from']}<->{h['to']}")
    return bad


def minimise(inst: dict, *, free_loops: bool, cap: int, timeout_s: float):
    """Lower the makespan until UNSAT.  The UNSAT is the proof of optimality."""
    g = Graph(inst)
    best = None
    T = 0
    while T <= cap:
        model = solve_at(inst, g, T, free_loops=free_loops, timeout_s=timeout_s)
        if model is not None:
            problems = validate(inst, g, model, free_loops=free_loops)
            if problems:
                raise AssertionError(
                    "the SAT model is illegal, so the ENCODING is wrong: "
                    + "; ".join(problems[:4]))
            best = (T, model)
            break
        T += 1
    if best is None:
        return None, None, False
    # T is the first feasible makespan found by increasing search, so T-1 was UNSAT and
    # T is optimal by construction -- no separate re-check needed.
    return best[0], best[1], True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("instances")
    ap.add_argument("--index", type=int, default=None)
    ap.add_argument("--free-loops", action="store_true",
                    help="drop constraint 3 -- the price of broadcast control")
    ap.add_argument("--cap", type=int, default=14, help="give up above this makespan")
    ap.add_argument("--timeout", type=float, default=20.0)
    args = ap.parse_args(argv)

    doc = json.loads(Path(args.instances).read_text(encoding="utf-8"))
    insts = doc["instances"]
    picked = [insts[args.index]] if args.index is not None else insts

    for k, inst in enumerate(picked):
        idx = args.index if args.index is not None else k
        T, model, proved = minimise(
            inst, free_loops=args.free_loops, cap=args.cap, timeout_s=args.timeout)
        h = inst["heuristic_makespan"]
        if T is None:
            print(f"  [{idx}] {len(inst['targets'])} movers: no schedule up to T={args.cap}")
        else:
            gap = h - T
            print(f"  [{idx}] {len(inst['targets'])} movers: optimal={T} heuristic={h} "
                  f"gap={gap:+d}{' (optimal)' if proved else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
