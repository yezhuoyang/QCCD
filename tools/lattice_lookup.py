"""One table lookup of Gidney 2025 on the stationary-tile machine (docs/FULLSTACK_PLAN.md, slice
S9 step 5): the write of gLoop1's first lookup -- 64 x 32 lattice-surgery CNOTs, each slot taken
whatever the table holds -- on a crossbar of relay chains.

The 64 product blocks pp[S] sit in a row along the bottom, tile (S, 32); the 32 a-slots of dlog's
bit triples, which the write fills, in a column on the left, tile (-1, j).  Each CNOT's ancilla
sits where pp[S]'s column meets bit j's row (`relay.crossings`), so its Z̄ product with pp[S] is
a chain up the column and its X̄ product with the a-slot a chain along the row (`relay.relay`):
every merge two patches face to face.  Scheduled (`lattice.schedule`, relays and ancillas pinned)
and checked (`lattice.check`); prints the numbers and every distinct program.

`--corridors`: the same crossbar with each product one corridor merge, for comparison.

    python tools/lattice_lookup.py [--corridors] [--shapes FILE]
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))


def lookup_stream():
    """gLoop1's first iteration's lookup write, as `program._Exp` writes it (every slot taken),
    no `se` or `decode` lines."""
    from lattice_adder import build
    from qccd.fullstack.scale import codesign as C
    from qccd.fullstack.scale import program as P
    from qccd.fullstack.scale.expr import ExprError
    prog = build()
    keep = C.exports(prog)
    for tid, t in list(prog.templates.items()):
        new, n = C.surgery_rewrite(t, keep.get(tid, set()))
        if n:
            prog.templates[tid] = new
    found = []

    def holds(node, env):
        try:
            return isinstance(node, P.When) or bool(node.cond(env))
        except ExprError:
            return False

    def walk(body, env, depth):
        for node in body:
            if found or depth > 12:
                return
            if isinstance(node, P.Call):
                env2 = {**prog.params, **P.bind(node.skeys + node.args, env)}
                if node.gadget == "gLoop1":
                    found.append((node, env, env2))
                    return
                walk(prog.body_of(node, env), env2, depth + 1)
            elif isinstance(node, P.Loop) and node.hi(env) > node.lo(env):
                walk(node.body, {**env, node.var: node.lo(env)}, depth)
            elif isinstance(node, P.Let):
                walk(node.body, {**env, node.var: node.value(env)}, depth)
            elif isinstance(node, (P.When, P.Pred)) and holds(node, env):
                walk(node.body, env, depth)
    walk(prog.root, dict(prog.params), 0)
    call, env, env2 = found[0]
    loop = prog.body_of(call, env)[0]                  # for 0 <= j < W1
    ex = P._Exp(prog, reserved=True)
    env3 = {**env2, loop.var: 0}
    for i, node in enumerate(loop.body):
        start = len(ex.out)
        part = P.Body([node])
        part.sid = loop.body.sid
        ex.body(part, env3, ())
        if i == 2:                                     # cxCascade, powerProduct, then the write
            return [ln for ln in ex.out[start:] if ln[0] not in ("se", "decode")], prog.params


def crossbar(stream, params):
    """Homes: pp[S] on (S, 32), the written a-slots on (-1, j) in order; bounds."""
    pp = params["ppBase"]
    targets = sorted({a[3] for w, a, _, _ in stream if w == "ppm" and a[0] == "X"}, key=lambda b: int(b[1:]))
    homes = {f"q{pp + s}": (s, len(targets)) for s in range(2 ** params["w1"])}
    homes.update({b: (-1, j) for j, b in enumerate(targets)})
    return homes, (-2, -1, 2 ** params["w1"], len(targets) + 1)


def main():
    from qccd.fullstack.scale import relay as R
    from qccd.fullstack.scale.lattice import check, schedule
    ap = argparse.ArgumentParser()
    ap.add_argument("--shapes")
    ap.add_argument("--corridors", action="store_true", help="no relays: each product one corridor merge")
    a = ap.parse_args()
    t0 = time.time()
    stream, params = lookup_stream()
    print(f"the write: {len(stream)} instructions {dict(Counter(w for w, *_ in stream))} ({time.time() - t0:.0f} s)")
    homes, bounds = crossbar(stream, params)
    pins = R.crossings(stream, homes)
    relayed, rpins, src = R.relay(stream, {**homes, **pins})
    if a.corridors:
        relayed, rpins = stream, {}
    print(f"{len(pins)} ancillas at crossings; {len(rpins)} relays; {len(relayed)} instructions "
          f"{dict(Counter(w for w, *_ in relayed))}")
    for letter in "ZX":
        assert all(R.proved(letter, k) for k in range(4))
    t1 = time.time()
    gadgets = schedule(relayed, homes, bounds, pinned={**pins, **rpins})
    span = max(g.t1 for g in gadgets)
    busy = sum(g.t1 - g.t0 for g in gadgets)
    tiles = (bounds[2] - bounds[0] + 1) * (bounds[3] - bounds[1] + 1)
    print(f"{len(gadgets)} gadgets ({dict(Counter(g.kind for g in gadgets))}) on {tiles} tiles, scheduled in "
          f"{time.time() - t1:.0f} s; machine time {span / 1e6:.3f} s, {busy / span:.1f} gadgets at once")
    t2 = time.time()
    print("check:", check(relayed, gadgets) or "clean", f"({time.time() - t2:.0f} s)")
    shapes = Counter(g.shape for g in gadgets)
    print(f"{len(shapes)} distinct programs:")
    for s, n in shapes.most_common(None if len(shapes) <= 40 else 12):
        print(f"  {n:7d}  {s}")
    if len(shapes) > 40:
        cor = Counter(len(s[3]) for s in shapes if s[0] == "merge")
        print(f"  ... merge shapes by corridor tiles: {sorted(cor.items())}")
    if a.shapes:
        Path(a.shapes).write_text(json.dumps([list(s) for s in shapes]), encoding="utf-8")


if __name__ == "__main__":
    sys.setrecursionlimit(100000)
    threading.stack_size(256 * 1024 * 1024 - 4096)
    th = threading.Thread(target=main)
    th.start()
    th.join()
