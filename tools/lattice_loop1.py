"""gLoop1's iterations on the stationary-tile machine (docs/FULLSTACK_PLAN.md, slice S9 step 5),
whole, on one floorplan: per iteration the exponent's load, powerProduct, the table lookup's
write, the 32-bit add it feeds, the readouts that undo the write, the phase fix-ups,
powerProduct's release and the exponent's unload -- every product a corridor merge a farm has
checked, or a chain of neighbour merges.

The floorplan:
* the farm (`factory.Farm`, as `lattice_adder.py --farm`): 31 narrow factories in a row, bit j's
  three dlog blocks a_j, b_j, c_j beneath factory j, in one column;
* under it at its left, the crossbar: the a-slot the write fills for bit j, w_j, in a column
  (row y_s + j), the 64 product blocks pp[S] in a row below them, every other column;
* below pp: three lanes, the six addr blocks on every other column, two lanes, then the
  exponent register (m = 1280 blocks) in rows across both farms' width, every other column, a
  lane under each row, each iteration's window together, the first nearest addr;
* left of the crossbar, a second farm (`--pfarm` narrow factories, tag "P") for powerProduct.
The write's CNOTs meet at crossings (pp[S]'s column, w_j's row: `relay.crossings`) and run as
relay chains (`relay.relay`); then each w_j is carried into its triple (`relay.carry`: along its
row, up its column beside the triple, one step across onto a_j's tile), the adder runs on the
farm, and mxDescending reads a_j there and makes the next iteration's a-slot back on the
crossbar.  The exponent's load and unload, powerProduct and its release go through
`relay.bring`: each fresh block made facing its first partner and carried beside the next.
`--iterations N` runs the first N iterations in turn, as the program writes them, each farm's
states numbered on from the last.  `--pairs`: every CZ of the adder's uncompute chain by a pair
made ahead (`czpair`), so no H̄ waits on a guard (the release's CZs wait on addr's tiles, not on
their H̄, and are left as they are).  Scheduled once on
one lattice and checked (`lattice.check`); prints the numbers, when each part runs, and every
distinct program.

    python tools/lattice_loop1.py [--iterations N] [--pairs] [--pfarm N] [--shapes FILE]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))


def iterations(n: int = 1):
    """gLoop1's first n iterations (j = 0 .. n-1) as one run writes them, every slot taken, no
    `se` or `decode` lines: (per iteration its parts in order as (callee, lines), the program's
    parameters, the loop's trip count)."""
    from lattice_lookup import first_gloop1, rewritten
    from qccd.fullstack.scale import program as P
    prog = rewritten()
    call, env, env2 = first_gloop1(prog)
    loop = prog.body_of(call, env)[0]                   # for 0 <= j < W1
    ex = P._Exp(prog, reserved=True)
    out = []
    for j in range(n):
        env3 = {**env2, loop.var: j}
        parts = []
        for node in loop.body:
            start = len(ex.out)
            part = P.Body([node])
            part.sid = loop.body.sid
            ex.body(part, env3, ())
            loops = sum(1 for nm, _ in parts if nm in ("write", "frames"))
            name = node.gadget if isinstance(node, P.Call) else ("write", "frames")[loops]
            parts.append((name, [ln for ln in ex.out[start:] if ln[0] not in ("se", "decode")]))
        out.append(parts)
    return out, prog.params, loop.hi(env2) - loop.lo(env2)


def main():
    from qccd.fullstack.scale import czpair
    from qccd.fullstack.scale import relay as R
    from qccd.fullstack.scale.factory import Farm
    from qccd.fullstack.scale.lattice import check, schedule
    ap = argparse.ArgumentParser()
    ap.add_argument("--shapes")
    ap.add_argument("--iterations", type=int, default=1)
    ap.add_argument("--pairs", action="store_true", help="CZs by pairs made ahead")
    ap.add_argument("--pfarm", type=int, default=8, help="factories serving powerProduct")
    a = ap.parse_args()
    t0 = time.time()
    its, params, trips = iterations(a.iterations)
    print("parts:", ", ".join(f"{n} {len(ls)}" for n, ls in its[0]), f"x {len(its)} ({time.time() - t0:.0f} s)")
    cx1, pprod, write, add, undo, frames, release, cx2 = [ls for _, ls in its[0]]
    D, w1 = params["D"], params["w1"]
    slots = [f"q{params['dlogBase'] + 3 * j}" for j in range(D)]

    # the farm and the adder's blocks beneath it
    blocks = sorted({b for w, x, _, _ in add for b in (x[1::2] if w == "ppm" else x[:1]) if b.startswith("q")},
                    key=lambda s: int(s[1:]))
    n_add = sum(w == "autoccz_h" for w, *_ in add)
    farm = Farm(n_add, x0=0, y=0, serve=lambda k: k % n_add, strips=4)
    homes = farm.beneath(blocks, 3, 1)
    col = {j: homes[s][0] for j, s in enumerate(slots)}
    y_a = homes[slots[0]][1]
    # the crossbar at the farm's left, under it: w_j on (x_c - 1, y_s + j), pp[S] on (x_c + 2S, y_pp);
    # below pp: three lanes, addr on every other column, two lanes, the exponent register in rows
    # of ten windows on every other column, a lane under each row
    fb = farm.bounds()
    x_c, y_s = fb[0] + 1, max(y for _, y in homes.values()) + 2
    y_pp = y_s + D
    y_addr, y_exp = y_pp + 5, y_pp + 9
    homes.update({f"q{params['ppBase'] + s}": (x_c + 2 * s, y_pp) for s in range(2 ** w1)})
    homes.update({f"q{params['addrBase'] + i}": (x_c + 2 * i, y_addr) for i in range(w1)})
    seat = {j: (x_c - 1, y_s + j) for j in range(D)}
    # powerProduct's factories at the crossbar's left, their sites' row level with pp's lanes
    n_pp = sum(w == "autoccz_h" for w, *_ in pprod)
    pfarm = Farm(a.pfarm, x0=0, y=y_pp - 2, serve=lambda k: k % a.pfarm, strips=4, tag="P")
    shift = x_c - 3 - pfarm.bounds()[2]
    pfarm = Farm(a.pfarm, x0=shift, y=y_pp - 2, serve=lambda k: k % a.pfarm, strips=4, tag="P")
    # the exponent's m blocks (the last window short) in rows across both farms' width, on the
    # crossbar's columns (every other one), each window's blocks together, the first nearest addr
    windows = [sorted({x[1] for w, x, _, _ in it[0][1] if w == "ppm" and x[0] == "Z"}, key=lambda s: int(s[1:]))
               for it in its]
    e0, m = int(windows[0][0][1:]), params["m"]
    assert all(win == [f"q{e0 + k}" for k in range(w1 * i, min(w1 * (i + 1), m))] for i, win in enumerate(windows))
    pb = pfarm.bounds()
    xs = range(pb[0] + (pb[0] - x_c) % 2, fb[2] + 1, 2)
    rows = -(-m // len(xs))
    slots_e = sorted(((x, y_exp + 2 * r) for x in xs for r in range(rows)),
                     key=lambda t: (abs(t[0] - x_c - w1), t[1], t[0]))
    homes.update({f"q{e0 + k}": slots_e[k] for k in range(m)})
    lanes = [("y", y_pp + 2), ("y", y_pp + 3), ("y", y_addr + 2)] + [("y", y_exp + 2 * r + 1) for r in range(rows)]
    sites = {}
    for k in range(n_pp * len(its)):
        sites.update(pfarm.sites_at(k))
    blocked = set(farm.walls()) | set(pfarm.walls()) | set(farm.all_homes().values()) \
        | set(pfarm.all_homes().values()) | set(seat.values())

    def brought(lines, tag, farm_=None, start=0):
        ported = farm_.ports(lines, start) if farm_ else lines
        skip = {k: len(farm_.stream) for k, (w, *_) in enumerate(ported) if w == "macro"} if farm_ else {}
        out, p = R.bring(ported, homes, sites, blocked, lanes, skip, tag)
        pins.update(p)
        return out

    # iteration i writes onto a-slots w_j (i = 0, made here) or w_j.i (made by the readouts before)
    gen = lambda i, j: f"w{j}" if i == 0 else f"w{j}.{i}"
    pins = {gen(0, j): seat[j] for j in range(D)}
    n_pairs = 0
    starts = {}
    stream = []
    for i, it in enumerate(its):
        cx1, pprod, write, add, undo, frames, release, cx2 = [ls for _, ls in it]
        starts[i, "exp load"] = len(stream)
        stream += brought(cx1, f"load{i}")
        starts[i, "powerProduct"] = len(stream)
        stream += brought(pprod, f"pp{i}", pfarm, i * n_pp)
        # the write onto the a-slots; then each carried into its triple: along its row, up beside
        # the triple, one step across onto a_j's tile
        starts[i, "write"] = len(stream)
        ren = {s: gen(i, j) for j, s in enumerate(slots)}
        if i == 0:
            stream += [("prep", (gen(0, j), "Z"), None, ()) for j in range(D)]
        w0 = len(stream)
        stream += [(w, tuple(ren.get(b, b) for b in x), o, g) for w, x, o, g in write]
        # (the crossings of an earlier write are free again: only homes and seats are taken)
        pins.update(R.crossings(stream[w0:], {**homes, **{gen(i, j): seat[j] for j in range(D)}}))
        starts[i, "carries"] = len(stream)
        for j in range(D):
            lines, at = R.carry(gen(i, j), slots[j],
                                [seat[j], (col[j] + 1, seat[j][1]), (col[j] + 1, y_a), (col[j], y_a)], f"mv{i}.{j}")
            stream += lines
            pins.update(at)
        # the adder on the farm, the readouts (the next a-slots made back on the crossbar), the
        # phase fix-ups, powerProduct's release, the exponent's unload
        starts[i, "add"] = len(stream)
        if a.pairs:
            ported, found = czpair.pairs(farm.ports(add, i * n_add))
            stream += ported
            pins.update(czpair.place(found, {**homes, **farm.all_homes()}, blocked))
            n_pairs += len(found)
        else:
            stream += farm.ports(add, i * n_add)
        starts[i, "readouts"] = len(stream)
        nxt = {s: gen(i + 1, j) for j, s in enumerate(slots)}
        stream += [(w, (nxt[x[0]],) + x[1:], o, g) if w == "prep" and x[0] in nxt else (w, x, o, g)
                   for w, x, o, g in undo]
        pins.update({gen(i + 1, j): seat[j] for j in range(D)})
        stream += frames
        starts[i, "release"] = len(stream)
        stream += brought(release, f"rel{i}")
        starts[i, "exp unload"] = len(stream)
        stream += brought(cx2, f"unload{i}")
    relayed, rpins, src = R.relay(stream, {**homes, **pins})
    pins.update(rpins)
    for letter in "ZX":
        assert R.move_proved(letter) and all(R.proved(letter, k) for k in range(4))

    pts = list(homes.values()) + list(pins.values()) + list(farm.all_homes().values()) + list(pfarm.all_homes().values())
    bounds = (min(min(x for x, _ in pts) - 1, pb[0]), min(fb[1], pb[1]), max(fb[2], max(x for x, _ in pts) + 1),
              max(y for _, y in pts) + 1)
    allh = {**homes, **farm.all_homes(), **pfarm.all_homes()}
    farms = {"": farm, "P": pfarm}
    t1 = time.time()
    gadgets = schedule(relayed, allh, bounds, walls=set(farm.walls()) | set(pfarm.walls()),
                       macro=lambda k, args, fa, board: farms[args[1]].macro(k, args, fa, board),
                       pinned=pins, max_corridor=19, late=True)
    span = max(g.t1 for g in gadgets)
    busy = sum(g.t1 - g.t0 for g in gadgets)
    tiles = (bounds[2] - bounds[0] + 1) * (bounds[3] - bounds[1] + 1)
    print(f"{len(relayed)} instructions, {sum(1 for b in pins if '.h' in b or b.startswith('r'))} relays, "
          f"{n_pairs} CZs by pairs; "
          f"{len(gadgets)} gadgets ({dict(Counter(g.kind for g in gadgets))}) on {tiles} tiles, scheduled in "
          f"{time.time() - t1:.0f} s")
    order = sorted(starts.items(), key=lambda kv: kv[1])
    span_of = {}
    for gd in gadgets:
        k = src[gd.line]
        part = next(p for p, s0 in reversed(order) if k >= s0)
        lo, hi = span_of.get(part, (math.inf, 0.0))
        span_of[part] = (min(lo, gd.t0), max(hi, gd.t1))
    print(f"machine time {span / 1e6:.3f} s, {busy / span:.1f} gadgets at once")
    for p, _ in order:
        if p in span_of:
            print(f"  {p[0]:3d} {p[1]:12s} {span_of[p][0] / 1e6:8.3f} .. {span_of[p][1] / 1e6:8.3f} s")
    t2 = time.time()
    print("check:", check(relayed, gadgets) or "clean", f"({time.time() - t2:.0f} s)")
    shapes = Counter(g.shape for g in gadgets)
    print(f"{len(shapes)} distinct programs ({sum(1 for s in shapes if s[0] == 'merge')} merges):")
    for s, c in shapes.most_common(14):
        print(f"  {c:7d}  {s}")
    if a.shapes:
        Path(a.shapes).write_text(json.dumps([list(s) for s in shapes]), encoding="utf-8")


if __name__ == "__main__":
    sys.setrecursionlimit(100000)
    threading.stack_size(256 * 1024 * 1024 - 4096)
    th = threading.Thread(target=main)
    th.start()
    th.join()
