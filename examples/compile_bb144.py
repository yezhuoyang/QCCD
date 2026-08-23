#!/usr/bin/env python
"""Compile BB [[144,12,12]] onto the shipped ring, and score it against the artifact.

    python examples/compile_bb144.py

The shipped hand-made schedule is the oracle: same code, same device, same 864 contacts.
A compiler with no oracle produces unfalsifiable numbers, so every figure below is printed
next to the one it has to beat.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd import Machine  # noqa: E402
from qccd.codes import gross_code  # noqa: E402
from qccd.compile import CompilePolicy, build, compile_code  # noqa: E402
from qccd.cost import corrected_model, deck_model, t1_metrics  # noqa: E402

HTML = ROOT / "visualizer_24_ancillas_24_junctions_standalone.html"

# the shipped artifact, recomputed in tests/test_golden_24ancilla.py
SHIPPED = {"hops": 2672, "batches": 396, "contacts": 864, "util": 2.18,
           "cost": 397184, "steps": 8808}


def rule(t):
    print()
    print(t)
    print("-" * len(t))


def main() -> int:
    m = Machine.load(ROOT / "arch" / "ring144_24v.arch.json")
    code = gross_code()

    rule("The oracle: the shipped hand-made schedule")
    shipped = build(m.arch, "deck", html_path=HTML)
    sr = m.run(shipped, model="deck")
    st1 = t1_metrics(shipped, m.arch, sr.report.result)
    print(f"  {st1.rotate_hops} rotate hops, {st1.n_batches} batches, "
          f"{st1.n_contacts} contacts")
    print(f"  batch utilization {st1.contact_batch_utilization:.2f} of "
          f"{st1.contact_batch_limit} "
          f"({100 * st1.contact_batch_utilization / st1.contact_batch_limit:.1f} %)")
    print(f"  deck cost {sr.cost:,.0f}, steps {sr.steps:,}")

    rule("Compiled, with every pass at its baseline setting")
    t = time.time()
    base = compile_code(m.arch, code, policy=CompilePolicy(
        placement="identity", ancilla_binding="fixed", insert_cooling=False,
        event_schedule=False, spam=False, refine_steps=0))
    print(f"  {base.hops} hops, {base.batches} batches, {base.contacts} contacts "
          f"({base.revolutions:.2f} revolutions)   [{time.time() - t:.1f}s]")

    rule("Compiled, with the four passes solved")
    t = time.time()
    good = compile_code(m.arch, code, policy=CompilePolicy(insert_cooling=False))
    print(good.report())
    print(f"  [{time.time() - t:.1f}s]")

    rule("Side by side")
    run_good = m.run(good.program, check_metrics=False)
    gt1 = t1_metrics(good.program, m.arch, run_good.report.result)
    rows = [
        # (label, shipped, baseline, solved, lower_is_better)
        ("rotate hops", SHIPPED["hops"], base.hops, good.hops, True),
        ("batches", SHIPPED["batches"], base.batches, good.batches, True),
        ("contacts", SHIPPED["contacts"], base.contacts, good.contacts, True),
        ("revolutions", SHIPPED["hops"] / 144, base.revolutions, good.revolutions, True),
        ("batch utilization", SHIPPED["util"], base.contacts / max(1, base.batches),
         gt1.contact_batch_utilization, False),
    ]
    print(f"  {'':22s} {'shipped':>10s} {'baseline':>10s} {'solved':>10s} "
          f"{'vs shipped':>11s}")
    for label, ship, b, g, lower_better in rows:
        ratio = (ship / g) if lower_better else (g / ship)
        print(f"  {label:22s} {ship:10.2f} {b:10.2f} {g:10.2f} {ratio:10.1f}x")
    print()
    print(f"  packing bound      {good.bound_revolutions:.2f} revolutions -- no schedule "
          f"can beat it; ours is {good.revolutions / good.bound_revolutions:.2f}x it")
    print(f"  PLAN M7 asks for >=3x batch utilization over the 9.1% baseline; this is "
          f"{gt1.contact_batch_utilization / SHIPPED['util']:.1f}x")

    rule("Legal, with cooling, under the corrected physics")
    t = time.time()
    final = compile_code(m.arch, code, policy=CompilePolicy(insert_cooling=True))
    out = m.run(final.program, check_metrics=False)
    n_cool = sum(1 for i in final.program.instructions if i.type == "cool")
    print(f"  {out}   [{time.time() - t:.1f}s]")
    print(f"  {n_cool} global cooling operations")
    print(f"  serial {out.runtime_ms:.2f} ms -> scheduled "
          f"{final.makespan_us / 1000:.2f} ms")
    print(f"  rules failed: {out.rules_failed or '(none)'}")
    print()
    print("  For scale: the shipped schedule under the same corrected model is 445 ms")
    print("  uncooled and 564 ms cooled (examples/heating_budget.py).")

    rule("What is still on the table")
    print("  * placement -- the interleaved seed is a strong local optimum: 60k annealed")
    print("    swaps and 300 true-objective steps both failed to improve it, and it sits")
    print("    at 1.10x the packing bound, so at most 10% is left here.")
    print("  * binding -- the sweep is greedy; the 10% gap to the bound is its cost.")
    print("  * scheduling -- only SPAM overlaps transport. Cooling could hide under the")
    print("    rotation and would dominate the saving, but whether Doppler cooling may")
    print("    run during transport is a physics claim, so it is a flag on the device")
    print("    (`cool.concurrent_with_transport`) and defaults to off.")
    print("  * the code layer takes BB codes only; HGP and surface codes are M5/M6.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
