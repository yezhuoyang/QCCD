"""C4: how far from optimal is the heuristic router, and what does broadcast control cost?

Two numbers, from the same instances the compiler actually solved:

**The optimality gap.**  `Compiler/PLAN.md` is explicit that a heuristic with no optimality
oracle produces unfalsifiable numbers -- the same trap M1 and M3 exist to avoid.  So every
routing sub-problem the heuristic solved is re-solved exactly, on the identical graph, and
the difference is reported per instance.

**The price of broadcast control.**  One waveform drives every site it reaches, so all ions
moving along one named loop in one cycle must move the same signed delta.  Solving each
instance twice -- once with that constraint and once without -- prices it.  On a
direct-wired device the two are identical by construction, which is the control.

Instances are filtered to the ones with real content: a sub-problem with one mover and no
congestion is optimal for any router and says nothing about either question.

    python Compiler/bridge/c4_gap.py build/inst_clifford12_cyclone_base.json --max 12
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "solver"))

from route_sat import minimise  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("instances")
    ap.add_argument("--max", type=int, default=12, help="how many instances to solve")
    ap.add_argument("--min-movers", type=int, default=2)
    ap.add_argument("--cap", type=int, default=12)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--no-broadcast-price", action="store_true")
    args = ap.parse_args(argv)

    doc = json.loads(Path(args.instances).read_text(encoding="utf-8"))
    insts = [i for i in doc["instances"] if len(i["targets"]) >= args.min_movers]
    insts = insts[: args.max]

    print(f"{doc['circuit']} on {doc['arch']}: "
          f"{len(insts)} instances with >= {args.min_movers} movers")
    print(f"{'#':>3} {'movers':>6} {'heur':>5} {'opt':>5} {'gap':>5} "
          f"{'free':>5} {'price':>5}  {'s':>6}")
    print("-" * 52)

    tot_h = tot_o = tot_f = 0
    solved = timedout = 0
    gaps: list[int] = []
    prices: list[int] = []

    for k, inst in enumerate(insts):
        t0 = time.time()
        T, _, _ = minimise(inst, free_loops=False, cap=args.cap, timeout_s=args.timeout)
        F = None
        if not args.no_broadcast_price:
            F, _, _ = minimise(inst, free_loops=True, cap=args.cap, timeout_s=args.timeout)
        dt = time.time() - t0
        h = inst["heuristic_makespan"]
        if T is None:
            print(f"{k:>3} {len(inst['targets']):>6} {h:>5} {'--':>5} {'':>5} "
                  f"{'':>5} {'':>5}  {dt:6.1f}")
            timedout += 1
            continue
        solved += 1
        gap = h - T
        gaps.append(gap)
        tot_h += h
        tot_o += T
        price = ""
        if F is not None:
            tot_f += F
            prices.append(T - F)
            price = f"{T - F:+d}"
        print(f"{k:>3} {len(inst['targets']):>6} {h:>5} {T:>5} {gap:>+5} "
              f"{F if F is not None else '':>5} {price:>5}  {dt:6.1f}")

    print()
    if solved:
        print(f"solved {solved}/{len(insts)} instances "
              f"({timedout} gave up above T={args.cap})")
        print(f"  heuristic total makespan {tot_h}, optimal total {tot_o} "
              f"-> {100 * (tot_h - tot_o) / max(tot_o, 1):.1f}% above optimal")
        worst = max(gaps) if gaps else 0
        n_opt = sum(1 for g in gaps if g == 0)
        print(f"  {n_opt}/{solved} instances routed optimally; worst gap {worst:+d} cycles")
        if prices:
            tot_price = sum(prices)
            n_bites = sum(1 for p in prices if p > 0)
            print(f"  broadcast control costs {tot_price} cycles over {solved} instances "
                  f"({tot_o} vs {tot_f} unconstrained); it binds on {n_bites} of them")
        else:
            print("  broadcast price not measured (--no-broadcast-price)")
    else:
        print("no instance solved within the cap")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
