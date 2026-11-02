"""Gidney 2025's 32-bit adder on the stationary-tile machine (docs/FULLSTACK_PLAN.md, slice S9
step 3): one real call, expanded at the PPM level, run by the patch-lattice scheduler.

The program is FormalRV's Gidney 2025 at RSA-2048 on its paper machine (the FormalRV checkout,
`qccd.fullstack.formalrv.root()`), every template rewritten for the surgery machine
(`codesign.surgery_rewrite`, each step proved; the Toffolis' |AutoCCZ> held as the factory makes
it).  One call of gidneyAddNoCarry (n = 32) is expanded.  Alone, the rest of it -- the final
CNOT cascade and the measurement-based uncompute, no CCZ state -- is scheduled on a
tile-in-a-sea lattice, bit i's three blocks in column 2i; with `--farm` the whole call is, its
31 |AutoCCZ> states made by narrow factories in a row (`factory.Farm`, four strips, 12 tiles
wide), bit i's blocks beneath factory i -- so every Toffoli reaches its factory alike and the
shapes repeat.  `--bits 2`: two bits beneath each factory, which makes their states on two
sets of sites in turn.  `--wide`: the eight-strip factory, 24 tiles wide, one bit each.  Either
is checked (`lattice.check`).  Prints the schedule's numbers and every distinct program it is
made of.

    python tools/lattice_adder.py [--farm [--bits 2 | --wide]] [--shapes FILE]
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def build():
    """Gidney 2025 at RSA-2048 on its paper machine, as FormalRV's audit gives it."""
    from qccd.fullstack import formalrv, layers
    from qccd.fullstack.scale import program as P
    from qccd.fullstack.scale.inputs import read_inputs
    aud = formalrv.root() / "FormalRV" / "Audit" / "Gidney2025"
    params = {}
    for f in ("Instance/rsa2048.inputs", "PaperMachine_q1M/execution.inputs"):
        params.update(read_inputs(aud / "Resource" / "Inputs" / f))
    args = (aud / "Resource" / "Inputs" / "PaperMachine_q1M" / "spec.args").read_text(encoding="utf-8")
    for v in args.split()[1::2]:
        k, x = v.split("=")
        params[k] = int(x)
    return P.Builder(layers.read(aud / "Gidney2025.qec", "qec")).build(
        {k: v for k, v in params.items() if isinstance(v, (int, list, tuple))})


def first_call(prog, gadget: str, n: int):
    """(call, env, its own env) of the first call of `gadget` with n = `n` the program makes."""
    from qccd.fullstack.scale import program as P
    from qccd.fullstack.scale.expr import ExprError
    found = []

    def holds(node, env):
        """A data guard on a table FormalRV gives no contents for (Tanf): not followed."""
        try:
            return isinstance(node, P.When) or bool(node.cond(env))
        except ExprError:
            return False

    def walk(body, env, depth):
        for node in body:
            if found or depth > 12:
                return
            if isinstance(node, P.Call):
                env2 = {**prog.params, **{f: e(env) for f, e in node.skeys + node.args}}
                if node.gadget == gadget and env2.get("n") == n:
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
    return found[0]


def adder_streams():
    """(forward half, the rest): the call's instructions as `program._Exp` writes them, no
    `se` or `decode` lines."""
    from qccd.fullstack.scale import codesign as C
    from qccd.fullstack.scale import program as P
    prog = build()
    keep = C.exports(prog)
    for tid, t in list(prog.templates.items()):
        new, n = C.surgery_rewrite(t, keep.get(tid, set()))
        if n:
            prog.templates[tid] = new
    call, env, env2 = first_call(prog, "gidneyAddNoCarry", 32)
    body = prog.body_of(call, env)
    ex = P._Exp(prog)
    cut = 0
    for i, node in enumerate(body):        # the forward half is the body's first call
        if i == 1:
            cut = len(ex.out)
        part = P.Body([node])
        part.sid = body.sid
        ex.body(part, env2, ())
    keep = [k for k, (w, *_) in enumerate(ex.out) if w not in ("se", "decode")]
    lines = [ex.out[k] for k in keep]
    split = sum(1 for k in keep if k < cut)
    return lines[:split], lines[split:]


def main():
    from qccd.fullstack.scale.lattice import check, columns, schedule
    from qccd.fullstack.scale.factory import Farm
    ap = argparse.ArgumentParser()
    ap.add_argument("--shapes")
    ap.add_argument("--farm", action="store_true")
    ap.add_argument("--bits", type=int, default=1, choices=(1, 2))
    ap.add_argument("--wide", action="store_true")
    a = ap.parse_args()
    forward, half = adder_streams()
    print(f"forward half {len(forward)} instructions ({dict(Counter(w for w, *_ in forward))}); "
          f"the rest {len(half)} ({dict(Counter(w for w, *_ in half))})")
    blocks = sorted({b for w, x, _, _ in half for b in (x[1::2] if w == "ppm" else x[:1]) if b.startswith("q")},
                    key=lambda s: int(s[1:]))
    homes, bounds = columns(blocks, 3)
    stream, walls, farm = half, set(), None
    if a.farm:
        n = sum(w == "autoccz_h" for w, *_ in forward)
        b = 1 if a.wide else a.bits
        farm = Farm(-(-n // b), x0=0, y=0, serve=lambda k: k // b, strips=8 if a.wide else 4, buffers=b)
        stream, walls = farm.ports(forward) + half, farm.walls()
        homes = farm.beneath(blocks, 3, b)
        xs, ys = [x for x, _ in homes.values()], [y for _, y in homes.values()]
        fb = farm.bounds()
        bounds = (min(fb[0], min(xs) - 1), fb[1], max(fb[2], max(xs) + 1), max(ys) + 1)
        homes = {**homes, **farm.all_homes()}
    gadgets = schedule(stream, homes, bounds, walls=walls, macro=farm and farm.macro)
    span = max(g.t1 for g in gadgets)
    busy = sum(g.t1 - g.t0 for g in gadgets)
    tiles = (bounds[2] - bounds[0] + 1) * (bounds[3] - bounds[1] + 1)
    print(f"{len(gadgets)} gadgets ({dict(Counter(g.kind for g in gadgets))}) on {tiles} tiles; "
          f"machine time {span / 1e6:.3f} s, {busy / span:.1f} gadgets at once")
    if farm:
        n_fwd = len(farm.ports(forward))
        print(f"the forward half done at {max(g.t1 for g in gadgets if g.line < n_fwd) / 1e6:.3f} s; "
              f"states per factory {sorted(Counter(f for _, f, _ in farm.served).values())}")
    print("check:", check(stream, gadgets) or "clean")
    shapes = Counter(g.shape for g in gadgets)
    print(f"{len(shapes)} distinct programs:")
    for s, n in shapes.most_common():
        print(f"  {n:4d}  {s}")
    if a.shapes:
        Path(a.shapes).write_text(json.dumps([list(s) for s in shapes]), encoding="utf-8")


if __name__ == "__main__":
    sys.setrecursionlimit(100000)
    threading.stack_size(256 * 1024 * 1024 - 4096)
    th = threading.Thread(target=main)
    th.start()
    th.join()
