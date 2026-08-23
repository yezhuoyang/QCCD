#!/usr/bin/env python
"""PLAN 13 item 3 -- rotation vs odd-even sort under WISE, as a crude model.

    python examples/thesis_rotation_vs_oddeven.py

PLAN 1's thesis: rigid lockstep rotation should make WISE's serialization penalty nearly
free, because rotation needs one movement template where an odd-even sort needs many.
PLAN 13 asks for a crude version now rather than after the compiler exists, so that if
rotation does *not* beat odd-even sort on paper we find out in week one.

This is not the compiler. Both schemes are emitted as real TSIR, replayed by the same
engine and judged by the same rules, but the comparison is on permutation
reconfiguration, not on a full syndrome-extraction round -- so it bounds and explains
rather than predicts. What it can settle is *which mechanism* the advantage comes from,
because each candidate mechanism is a separately reported number.

Read the caveats at the bottom before quoting anything from it.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.arch import load  # noqa: E402
from qccd.compile import rotate as rotation_program  # noqa: E402
from qccd.compile.oddeven import (  # noqa: E402
    cyclic_shift_target,
    odd_even_sort_program,
)
from qccd.cost import corrected_model, deck_model  # noqa: E402
from qccd.ir import import_schedule  # noqa: E402
from qccd.verify import replay, verify  # noqa: E402

HTML = "visualizer_24_ancillas_24_junctions_standalone.html"


def _rule(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def main() -> int:
    arch = load(ROOT / "arch" / "ring144_24v.arch.json")
    model = corrected_model()
    n = len(arch.device.loops["L0"].nodes)
    ions = [f"d{i}" for i in range(n)]

    # ---------------------------------------------------------------- measured
    _rule("1. Rotation's WISE serialization penalty, MEASURED on the shipped schedule")
    shipped = import_schedule(arch, html_path=ROOT / HTML)
    deck = verify(shipped, arch, deck_model())
    templates = shipped.templates()
    cycles = len(deck.result.cycles) - 1  # drop `init`
    classes_per_cycle = 1  # one instruction is one cycle and carries one class
    print(f"  movement templates          {len(templates)}   {templates}")
    print(f"  machine cycles              {cycles}")
    print(f"  classes active per cycle    {classes_per_cycle}")
    print(f"  max_simd_classes_per_cycle  {arch.max_simd_classes()} (WISE)")
    print("  serialization factor        1.00")
    print("    Every cycle already uses exactly one class, and consecutive rotation hops")
    print("    are sequentially dependent, so unconstrained wiring could not merge any")
    print("    of them. WISE costs this schedule nothing. That is a fact about the")
    print("    shipped artifact, not a model.")

    # ---------------------------------------------------------------- legality
    _rule("2. Is odd-even sort even legal on the shipped ring?")
    ions = [f"d{i}" for i in range(n)]
    sr = odd_even_sort_program(
        arch, ions, cyclic_shift_target(ions, n // 2),
        arch_spec="arch/ring144_24v.arch.json",
    )
    rep = verify(sr.program, arch, model, check_metrics=False)
    summary = rep.rules.summary()
    print(f"  ring144_24v   rules failed: {summary['failed'] or '(none)'}   "
          f"violations: {summary['violations']}")
    for v in rep.rules.violations[:2]:
        print(f"    {v}")
    print()
    print("  No. A transposition parks both ions of a pair in one slot, and 24 of the")
    print("  144 rail slots are degree-3 T-junctions where R2 allows at most one ion.")
    print("  The 24 vertical shuttling lines do not merely tax the rotation path")
    print("  (PLAN 0.5) -- they make the alternative reconfiguration scheme")
    print("  structurally illegal on the same hardware. That is an argument for in-line")
    print("  ancillas that q_inline_vs_hanging_ancillas did not previously have.")

    base = load(ROOT / "arch" / "cyclone_base.arch.json")
    m = len(base.device.loops["L0"].nodes)
    base_ions = [f"d{i}" for i in range(m)]
    base_sr = odd_even_sort_program(
        base, base_ions, cyclic_shift_target(base_ions, m // 2),
        arch_spec="arch/cyclone_base.arch.json",
    )
    base_rep = verify(base_sr.program, base, model, check_metrics=False)
    print()
    print(f"  cyclone_base  rules failed: {base_rep.rules.summary()['failed'] or '(none)'}"
          f"   violations: {base_rep.rules.summary()['violations']}")
    print("  Legal: no junction sits on the loop, so a merge has somewhere to happen.")
    print("  The rest of this comparison therefore runs on cyclone_base, which is also")
    print("  what PLAN 7.1 demands -- each scheme with the architecture that suits it.")

    # ---------------------------------------------------------------- modelled
    _rule("3. Odd-even sort on cyclone_base, emitted and replayed")
    rng = random.Random(20260819)
    shuffled = base_ions[:]
    rng.shuffle(shuffled)
    cases = {
        "random permutation": (shuffled, base_ions),
        "cyclic shift by 1": (base_ions, cyclic_shift_target(base_ions, 1)),
        "cyclic shift by m/4": (base_ions, cyclic_shift_target(base_ions, m // 4)),
        "cyclic shift by m/2": (base_ions, cyclic_shift_target(base_ions, m // 2)),
    }
    print(f"  ring: {m} slots, capacity {base.device.nodes['S0'].capacity}, fully packed,"
          f" 0 junctions")
    print()
    print(f"  {'task':22s} {'rounds':>7s} {'transp':>8s} {'cycles':>7s} "
          f"{'free':>6s} {'S':>5s} {'us':>10s} {'quanta/ion':>11s}")
    results = {}
    for label, (a0, a1) in cases.items():
        r = odd_even_sort_program(base, a0, a1, arch_spec="arch/cyclone_base.arch.json")
        assert r.reached_target, label
        res = replay(r.program, base, model, check_rules=False)
        q = max((sum(v.values()) for v in res.per_ion_quanta.values()), default=0.0)
        results[label] = (r, res, q)
        print(f"  {label:22s} {r.active_rounds:7d} {r.transpositions:8d} "
              f"{r.cycles:7d} {r.cycles_unconstrained:6d} "
              f"{r.serialization_factor:5.2f} {res.total_us:10.0f} {q:11.1f}")

    _rule("4. The same tasks by rigid rotation on the same ring")
    print(f"  {'task':22s} {'cycles':>7s} {'free':>6s} {'S':>5s} "
          f"{'us':>10s} {'quanta/ion':>11s}")
    rot_results = {}
    for label, (a0, a1) in cases.items():
        k = _shift_of(a0, a1)
        if k is None:
            print(f"  {label:22s} {'--':>7s} {'--':>6s} {'--':>5s} {'--':>10s} "
                  f"{'--':>11s}   rotation cannot reach this permutation at all")
            continue
        prog = rotation_program(base, k)
        res = replay(prog, base, model, check_rules=False)
        q = max((sum(v.values()) for v in res.per_ion_quanta.values()), default=0.0)
        hops = sum(v for kk, v in res.hops_by_class.items() if kk.startswith("rotate"))
        rot_results[label] = (res, q, hops)
        print(f"  {label:22s} {hops:7d} {hops:6d} {1.00:5.2f} "
              f"{res.total_us:10.0f} {q:11.1f}")

    # ---------------------------------------------------------------- mechanism
    _rule("5. Where the difference actually comes from")
    for label in ("cyclic shift by 1", "cyclic shift by m/2"):
        sr2, res, q = results[label]
        rot, rot_q, hops = rot_results[label]
        print(f"  task: {label}")
        print(f"  {'':26s} {'odd-even':>12s} {'rotation':>12s} {'ratio':>8s}")
        rows = [
            ("movement templates", 2.0, 1.0),
            ("machine cycles", float(sr2.cycles), float(hops)),
            ("serialization factor S", sr2.serialization_factor, 1.0),
            ("wall clock, us", res.total_us, rot.total_us),
            ("quanta per ion", q, rot_q),
        ]
        for lbl, a, b in rows:
            ratio = (a / b) if b else float("nan")
            print(f"  {lbl:26s} {a:12.2f} {b:12.2f} {ratio:8.1f}x")
        print()

    print("  Both schemes need O(1) movement templates, so PLAN 1's stated mechanism")
    print("  -- 'rotation needs one template where odd-even sort needs many' -- is NOT")
    print("  what separates them. Three other things do:")
    print()
    print("  (a) DIRECTION. A transposition merges one ion left and splits the other")
    print("      right. R4 fixes a class's global direction, so those are two classes")
    print("      and WISE cannot put them in one cycle: S ~ 2.0 against 1.00 for")
    print("      rotation, whose ions all move the same way by construction. H2 already")
    print("      bubble-sorts 'in both directions around the device' (2305.03828), so")
    print("      this is shipped hardware's problem, not a strawman's.")
    print("  (b) PRIMITIVE MIX. Every transposition pays a split and a merge at 6.0")
    print("      quanta each; a rotation hop pays a 0.1-quanta shuttle. That is the 60x")
    print("      ratio of 2510.23519's own table, charged once per ion per round -- and")
    print("      it is the dominant term in every row above.")
    print("  (c) REACHABILITY. A loop shift generates exactly the cyclic group, so")
    print("      rotation cannot realize an arbitrary permutation at all. It is cheap")
    print("      because the schedule was designed so that only cyclic realignment is")
    print("      ever needed. That is a constraint on the code schedule, not a free win,")
    print("      and it is the thing M4 has to hold fixed to compare fairly.")

    _rule("6. Cooling, which is where (b) is actually paid")
    for label in ("random permutation", "cyclic shift by m/2"):
        r, _, q = results[label]
        print(f"  odd-even, {label:22s} peak n-bar {q:9.1f} quanta")
    rot, rot_q, _ = rot_results["cyclic shift by m/2"]
    print(f"  rotation, cyclic shift by m/2      peak n-bar {rot_q:9.1f} quanta")
    print()
    print(f"  Against an MS-gate budget of "
          f"{base.primitives.scalar('ms_gate')['max_quanta']} quanta, both need cooling")
    print("  before the next gate; the question is how much. Neither program contains a")
    print("  gate, so R7 inserts none here -- the peak n-bar is what a following gate")
    print("  would have to have had removed first, and cooling time scales with it.")

    _rule("7. What this does NOT establish")
    print("  * The published penalty is 25x on the LOGICAL clock at LER 1e-9 on a WISE")
    print("    GRID (2510.23519). This compares reconfiguration on a ring. The gap")
    print("    between the ~2x serialization measured here and that 25x is unexplained,")
    print("    and naming it is the point: it is not serialization alone.")
    print("  * H2 already bubble-sorts in both directions around a ring (2305.03828),")
    print("    so the odd-even scheme is shipped hardware's choice, not a strawman --")
    print("    and it is the experimental reference point M4 should be checked against.")
    print("  * No syndrome-extraction round is compiled here, so nothing about the")
    print("    logical clock for BB [[144,12,12]] follows. That is M4.")
    print("  * Cyclone's confusion matrix says a mismatched policy makes any")
    print("    architecture look bad, so odd-even sort must be run with ITS best")
    print("    policy before any ranking is believed (PLAN 7.1).")
    return 0


def _shift_of(start, target):
    """The k with target == rotate(start, k), or None if there is no such k."""
    n = len(start)
    where = {ion: i for i, ion in enumerate(target)}
    k = (where[start[0]] - 0) % n
    for i, ion in enumerate(start):
        if where[ion] != (i + k) % n:
            return None
    return k


if __name__ == "__main__":
    raise SystemExit(main())
