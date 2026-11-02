"""gLoop1's first iteration on the stationary-tile machine (docs/FULLSTACK_PLAN.md, slice S9
step 5), part by part on one floorplan: the table lookup's write, the 32-bit add it feeds, and
the readouts that undo the write -- every product either a corridor merge the farm has checked
or a relay chain of neighbour merges.

The floorplan:
* the farm (`factory.Farm`, as `lattice_adder.py --farm`): 31 narrow factories in a row, bit j's
  three dlog blocks a_j, b_j, c_j beneath factory j, in one column;
* a crossbar below it at its left: the a-slot the write fills for bit j, w_j, in a column (row
  y_s + j), the 64 product blocks pp[S] in a row under them.
The write's CNOTs meet at crossings (pp[S]'s column, w_j's row: `relay.crossings`) and run as
relay chains (`relay.relay`); then each w_j is carried into its triple (`relay.carry`: along its
row, up its column beside the triple, one step across onto a_j's tile -- three moves), the adder
runs on the farm, and mxDescending reads a_j there and makes the next iteration's a-slot back on
the crossbar.  (A staircase of a-slots under their triples, the write's rows spread over the
farm's width, took 60.3 s: its rows cross most of pp's columns.)
Scheduled once on one lattice and checked (`lattice.check`); prints the numbers and every
distinct program.

    python tools/lattice_loop1.py [--shapes FILE]
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


def iteration():
    """gLoop1's first iteration (j = 0), every slot taken, no `se` or `decode` lines: (its
    parts in order as (callee, lines), the program's parameters)."""
    from lattice_lookup import first_gloop1, rewritten
    from qccd.fullstack.scale import program as P
    prog = rewritten()
    call, env, env2 = first_gloop1(prog)
    loop = prog.body_of(call, env)[0]                   # for 0 <= j < W1
    ex = P._Exp(prog, reserved=True)
    env3 = {**env2, loop.var: 0}
    parts = []
    for node in loop.body:
        start = len(ex.out)
        part = P.Body([node])
        part.sid = loop.body.sid
        ex.body(part, env3, ())
        loops = sum(1 for n, _ in parts if n in ("write", "frames"))
        name = node.gadget if isinstance(node, P.Call) else ("write", "frames")[loops]
        parts.append((name, [ln for ln in ex.out[start:] if ln[0] not in ("se", "decode")]))
    return parts, prog.params


def main():
    from qccd.fullstack.scale import relay as R
    from qccd.fullstack.scale.factory import Farm
    from qccd.fullstack.scale.lattice import check, schedule
    ap = argparse.ArgumentParser()
    ap.add_argument("--shapes")
    a = ap.parse_args()
    t0 = time.time()
    parts, params = iteration()
    names = [n for n, _ in parts]
    print("parts:", ", ".join(f"{n} {len(ls)}" for n, ls in parts), f"({time.time() - t0:.0f} s)")
    write = dict(parts)["write"]
    add = dict(parts)["gidneyAddNoCarry"]
    undo = dict(parts)["mxDescending"]
    D, pp0, dlog = params["D"], params["ppBase"], params["dlogBase"]
    slots = [f"q{dlog + 3 * j}" for j in range(D)]

    # the farm and the adder's blocks beneath it
    blocks = sorted({b for w, x, _, _ in add for b in (x[1::2] if w == "ppm" else x[:1]) if b.startswith("q")},
                    key=lambda s: int(s[1:]))
    farm = Farm(sum(w == "autoccz_h" for w, *_ in add), x0=0, y=0, serve=lambda k: k, strips=4)
    homes = farm.beneath(blocks, 3, 1)
    col = {j: homes[s][0] for j, s in enumerate(slots)}
    y_a = homes[slots[0]][1]
    # the crossbar at the farm's left, under it: w_j on (x_c - 1, y_s + j), pp[S] on (x_c + S, y_pp)
    fb = farm.bounds()
    x_c, y_s = fb[0] + 1, max(y for _, y in homes.values()) + 2
    y_pp = y_s + D
    homes.update({f"q{pp0 + s}": (x_c + s, y_pp) for s in range(2 ** params["w1"])})
    seat = {j: (x_c - 1, y_s + j) for j in range(D)}

    # the write onto w_j; then each w_j carried into its triple: along its row, up beside the
    # triple, one step across onto a_j's tile
    ren = {s: f"w{j}" for j, s in enumerate(slots)}
    stream = [("prep", (f"w{j}", "Z"), None, ()) for j in range(D)]
    stream += [(w, tuple(ren.get(b, b) for b in x), o, g) for w, x, o, g in write]
    pins = {f"w{j}": seat[j] for j in range(D)}
    pins.update(R.crossings(stream, {**homes, **pins}))
    for j in range(D):
        lines, at = R.carry(f"w{j}", slots[j], [seat[j], (col[j] + 1, seat[j][1]), (col[j] + 1, y_a), (col[j], y_a)],
                            f"mv{j}")
        stream += lines
        pins.update(at)
    # the adder on the farm, then the readouts; the next a-slots made on the crossbar
    stream += farm.ports(add)
    nxt = {s: f"w{j}'" for j, s in enumerate(slots)}
    stream += [(w, (nxt[x[0]],) + x[1:], o, g) if w == "prep" and x[0] in nxt else (w, x, o, g) for w, x, o, g in undo]
    pins.update({f"w{j}'": seat[j] for j in range(D)})
    relayed, rpins, _ = R.relay(stream, {**homes, **pins})
    pins.update(rpins)
    for letter in "ZX":
        assert R.move_proved(letter) and all(R.proved(letter, k) for k in range(4))

    xs_all = [x for x, _ in list(homes.values()) + list(pins.values())]
    bounds = (min(fb[0], min(xs_all) - 1), fb[1], max(fb[2], max(xs_all) + 1), y_pp + 1)
    allh = {**homes, **farm.all_homes()}
    t1 = time.time()
    gadgets = schedule(relayed, allh, bounds, walls=farm.walls(), macro=farm.macro, pinned=pins,
                       max_corridor=19)
    span = max(g.t1 for g in gadgets)
    busy = sum(g.t1 - g.t0 for g in gadgets)
    tiles = (bounds[2] - bounds[0] + 1) * (bounds[3] - bounds[1] + 1)
    print(f"{len(relayed)} instructions, {sum(1 for b in pins if b.startswith(('r', 'mv')))} relays; "
          f"{len(gadgets)} gadgets ({dict(Counter(g.kind for g in gadgets))}) on {tiles} tiles, scheduled in "
          f"{time.time() - t1:.0f} s")
    marks = {}
    n = 0
    for k, (w, x, o, g) in enumerate(relayed):
        if w == "ppm" and any(b.startswith("mv") for b in x[1::2]) and "write" not in marks:
            marks["write"] = k
        if w == "macro" and "moves" not in marks:
            marks["moves"] = k
    ends = {}
    for gd in gadgets:
        part = "write" if gd.line < marks["write"] else "moves" if gd.line < marks["moves"] else "add + readouts"
        ends[part] = max(ends.get(part, 0.0), gd.t1)
    print(f"machine time {span / 1e6:.3f} s ({', '.join(f'{p} done {t / 1e6:.3f} s' for p, t in ends.items())}), "
          f"{busy / span:.1f} gadgets at once")
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
