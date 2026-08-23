#!/usr/bin/env python
"""How many ions can move at once, and what actually stops more from moving?

    python examples/parallel_movement.py

Short answer: the number of ions in one cycle is **unbounded** -- participation in a
movement class is variadic, so one instruction moves 144 ions at different sites in the
same instant. What limits simultaneity is never the ion count. It is four things, and the
platform charges each of them separately:

    R4   every ion moving together must share ONE class: same operation type, same
         global direction. Two directions at once needs two classes, and the machine
         declares how many it can drive (`max_simd_classes_per_cycle`).
    R4b  intra-trap and inter-trap transport use distinct control pathways and never
         overlap.
    R3   at most one ion per shuttling segment.
    R1/R2 capacity at a site, one ion at a junction.

So "can all ions move in parallel if the hardware control is enough?" -- yes, and this
file measures exactly what "enough" buys.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd import Machine  # noqa: E402
from qccd.compile.schedule import schedule_events  # noqa: E402
from qccd.cost import corrected_model  # noqa: E402
from qccd.verify import replay  # noqa: E402


def rule(t):
    print()
    print(t)
    print("-" * len(t))


def two_rail_machine(k: int = 1, name: str = "two_rails") -> Machine:
    """A ladder whose two rails can be driven independently.

    The classes are declared on the device, because R4 requires a cycle to name a class
    the machine actually has -- a program cannot invent control it was not given.
    """
    m = Machine.ladder(12, rungs=4, highways=0, name=name)
    m.declare_class("shift_right", type="shift", orbit="TOP", delta=1)
    m.declare_class("shift_left", type="shift", orbit="BOTTOM", delta=-1)
    ctl = dict(m.arch.control)
    ctl["max_simd_classes_per_cycle"] = k
    m._rebuild(control=ctl)
    return m


def main() -> int:
    # ---------------------------------------------------- 1. how many at once
    rule("1. How many ions move in ONE cycle?")
    big = Machine.load(ROOT / "arch" / "ring144_24v.arch.json")
    p = big.program("rotate_all").fill()
    p.rotate(+1)
    res = replay(p.build(), big.arch, corrected_model(), check_rules=True)
    cyc = [c for c in res.cycles if c.type == "simd"][0]
    print(f"  one rigid rotation instruction: {cyc.n_participants} ions move together")
    print(f"  they occupy {cyc.n_participants} distinct sites and cross "
          f"{cyc.n_participants} distinct segments in the same instant")
    print(f"  cost {res.total_cost:.0f} (summed over participants), "
          f"steps {res.total_steps} (the MAX over them -- they are simultaneous)")
    print(f"  rules: {'all pass' if res.rules.ok() else res.rules.summary()['failed']}")
    print()
    print("  Participation is variadic: nothing in the IR or the verifier bounds the")
    print("  number of ions in a cycle. 144 here; it would be 10,000 on a bigger ring.")

    # ---------------------------------------------------- 2. what limits it
    rule("2. What stops MORE ions moving together?")
    m = two_rail_machine(1)
    tops = [n for n in m.sites() if n.startswith("T")]
    bots = [n for n in m.sites() if n.startswith("B")]
    place = {f"t{i}": n for i, n in enumerate(sorted(tops, key=lambda x: int(x[1:])))}
    place.update({f"b{i}": n for i, n in enumerate(sorted(bots, key=lambda x: int(x[1:])))})

    # one class: every ion on both rails moves the SAME way -- legal, one cycle
    same = m.program("same_direction").init(place)
    with same.cycle("shift_right") as c:
        for i in range(11):
            c.move(f"t{i}", f"T{i}", f"T{i + 1}")
        for i in range(11):
            c.move(f"b{i}", f"B{i}", f"B{i + 1}")
    r = replay(same.build(), m.arch, corrected_model(), check_rules=True)
    print(f"  22 ions on two rails, all moving the same direction: "
          f"{len([c for c in r.cycles if c.type == 'simd'])} cycle, "
          f"{r.total_us:.0f} us, rules "
          f"{'pass' if r.rules.ok() else r.rules.summary()['failed']}")

    # two directions: needs two classes, so it needs two instructions
    opp = m.program("opposite_directions").init(place)
    with opp.cycle("shift_right") as c:
        for i in range(11):
            c.move(f"t{i}", f"T{i}", f"T{i + 1}")
    with opp.cycle("shift_left") as c:
        for i in range(11, 0, -1):
            c.move(f"b{i}", f"B{i}", f"B{i - 1}")
    r2 = replay(opp.build(), m.arch, corrected_model(), check_rules=True)
    print(f"  the same 22 ions, opposite directions: "
          f"{len([c for c in r2.cycles if c.type == 'simd'])} cycles, "
          f"{r2.total_us:.0f} us serial")
    print("  -> the ion count is not the limit. The DIRECTION is: a class fixes one, so")
    print("     two directions are two classes (R4), and whether they can run at the")
    print("     same time is a property of the control hardware.")

    # ---------------------------------------------------- 3. what control buys
    rule("3. What does a bigger control budget buy?")
    print(f"  {'classes the machine drives':>28s} {'makespan':>10s} {'overlapped':>11s}"
          f"  {'legal?':>8s}")
    for k in (1, 2):
        mk = two_rail_machine(k)
        prog = mk.program("opposite").init(place)
        with prog.cycle("shift_right") as c:
            for i in range(11):
                c.move(f"t{i}", f"T{i}", f"T{i + 1}")
        with prog.cycle("shift_left") as c:
            for i in range(11, 0, -1):
                c.move(f"b{i}", f"B{i}", f"B{i - 1}")
        base = replay(prog.build(), mk.arch, corrected_model(), check_rules=False)
        sr = schedule_events(prog.build(), mk.arch, base)
        chk = mk.run(sr.program, check_metrics=False)
        print(f"  {k:>28d} {sr.makespan_us:9.0f} us {sr.n_overlapped:11d}"
              f"  {'yes' if chk.ok else 'NO ' + str(chk.rules_failed):>8s}")
    print()
    print("  With one class the two rails serialize; with two they run at once, and the")
    print("  verifier confirms the two-class schedule is legal only when the machine")
    print("  declares it can drive two. Ask for two on a one-class machine and R4 fires.")

    # ---------------------------------------------------- 4. the illegal case
    rule("4. Asking for more parallelism than the hardware has")
    m1 = two_rail_machine(1, name="one_class")
    prog = m1.program("too_much").init(place)
    with prog.cycle("shift_right") as c:
        for i in range(11):
            c.move(f"t{i}", f"T{i}", f"T{i + 1}")
    with prog.cycle("shift_left") as c:
        for i in range(11, 0, -1):
            c.move(f"b{i}", f"B{i}", f"B{i - 1}")
    base = replay(prog.build(), m1.arch, corrected_model(), check_rules=False)
    # force both instructions to run at the same instant
    forced = prog.build()
    forced.instructions[1] = forced.instructions[1].with_annotations(
        t0=0.0, t1=100.0, cost=0, steps=1)
    forced.instructions[2] = forced.instructions[2].with_annotations(
        t0=0.0, t1=100.0, cost=0, steps=1)
    out = m1.run(forced, check_metrics=False)
    print(f"  forcing two classes into one instant on a one-class machine: "
          f"{out.rules_failed or 'NOT CAUGHT'}")
    for v in out.violations[:1]:
        print(f"    {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
