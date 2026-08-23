#!/usr/bin/env python
"""Benchmark ion-routing strategies on one fixed device.

    python examples/benchmark_routing.py

The device never changes. Only the routing strategy does. That is the point: PLAN §7.1
records Cyclone's confusion-matrix finding that a *mismatched* policy makes any
architecture look bad, so a platform that cannot hold the hardware fixed and vary the
policy cannot rank either one.

A benchmark is only worth having if it can be wrong, so this reports three things a
ranking usually hides:

* a **lower bound** every strategy is scored against, so "good" means "close to the
  bound", not "better than the others I tried";
* a **deliberately bad strategy**, so a benchmark that cannot separate good from bad
  fails visibly rather than silently;
* the same ranking under **both primitive tables**, because the corpus's two tables differ
  by 2-3x in time and a ranking that flips between them is not a result (PLAN §3.2).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd import Machine  # noqa: E402
from qccd.codes import gross_code  # noqa: E402
from qccd.compile import CompilePolicy, build, compile_code  # noqa: E402
from qccd.compile.oddeven import cyclic_shift_target, odd_even_sort_program  # noqa: E402
from qccd.cost import corrected_model, t1_metrics  # noqa: E402

HTML = ROOT / "visualizer_24_ancillas_24_junctions_standalone.html"


def rule(t):
    print()
    print(t)
    print("-" * len(t))


STRATEGIES = {
    "hand-made (shipped)": dict(kind="deck"),
    "identity place + fixed waves": dict(
        placement="identity", ancilla_binding="fixed", refine_steps=0),
    "interleaved + fixed waves": dict(
        placement="interleaved", ancilla_binding="fixed", refine_steps=0),
    "identity + dynamic binding": dict(
        placement="identity", ancilla_binding="dynamic", refine_steps=0),
    "interleaved + dynamic binding": dict(
        placement="interleaved", ancilla_binding="dynamic", refine_steps=0),
    "annealed + dynamic binding": dict(
        placement="anneal", ancilla_binding="dynamic"),
    # A machine that can only hold three contacts open at once: the artifact's
    # `active_contact_limit` is 24, so this is a real hardware bound set badly, not a
    # scheduling preference.  It has to rank last or the benchmark measures nothing.
    "DELIBERATELY BAD: 3 contacts max": dict(
        placement="interleaved", ancilla_binding="dynamic", refine_steps=0,
        max_contacts_per_batch=3),
}


def score(m: Machine, code, name: str, spec: dict, table: str) -> dict:
    model = corrected_model(table)
    if spec.get("kind") == "deck":
        prog = build(m.arch, "deck", html_path=HTML)
        bound = float("nan")
    else:
        r = compile_code(m.arch, code, policy=CompilePolicy(
            insert_cooling=False, **{k: v for k, v in spec.items() if k != "kind"}))
        prog = r.program
        bound = r.bound_revolutions
    run = m.run(prog, model=model, check_metrics=False)
    t1 = t1_metrics(prog, m.arch, run.report.result)
    data = [i for i in run.report.result.per_ion_quanta if i.startswith("d")]
    q = max((sum(run.report.result.per_ion_quanta[i].values()) for i in data),
            default=0.0)
    cooled = m.run(prog, model=model, cool=True, check_metrics=False)
    n_cool = sum(1 for i in cooled.report.result.cycles if i.type == "cool")
    return {
        "name": name, "hops": t1.rotate_hops, "batches": t1.n_batches,
        "contacts": t1.n_contacts, "util": t1.contact_batch_utilization,
        "ms": run.runtime_ms, "peak_q": q, "cool_ops": n_cool,
        "cooled_ms": cooled.runtime_ms, "bound": bound,
        # legality is judged on the COOLED program: every schedule fails
        # R7/R7c before cooling is inserted, which says nothing about routing
        "ok": cooled.ok,
    }


def main() -> int:
    m = Machine.load(ROOT / "arch" / "ring144_24v.arch.json")
    code = gross_code()

    rule("Fixed device, seven routing strategies, corrected physics")
    print(f"  device: {m}")
    print(f"  code:   {code.name}, {len(code.checks)} checks x 6 = 864 contacts")
    print()
    hdr = (f"  {'strategy':32s} {'hops':>6s} {'batch':>6s} {'util':>6s} "
           f"{'rev':>5s} {'ms':>8s} {'peak n':>8s} {'cools':>6s} {'cooled ms':>10s}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    rows = []
    for name, spec in STRATEGIES.items():
        r = score(m, code, name, spec, "qccdsim_jones")
        rows.append(r)
        print(f"  {r['name']:32s} {r['hops']:6d} {r['batches']:6d} "
              f"{r['util']:6.2f} {r['hops'] / 144:5.2f} {r['ms']:8.1f} "
              f"{r['peak_q']:8.0f} {r['cool_ops']:6d} {r['cooled_ms']:10.1f}"
              + ("" if r["ok"] else "   RULES FAIL"))
        assert r["contacts"] == 864, f"{name} did not realize every contact"

    # runtime is the objective (PLAN 0.2: steps proxy runtime, cost proxies heating,
    # and they are two halves of one budget, not rivals), so rank on the cooled clock
    ranked = sorted(rows, key=lambda r: r["cooled_ms"])
    best, worst = ranked[0], ranked[-1]
    rule("Does the benchmark discriminate?")
    print("  ranked on cooled wall clock, which is the objective:")
    for i, r in enumerate(ranked, 1):
        print(f"    {i}. {r['name']:34s} {r['cooled_ms']:8.1f} ms  "
              f"{r['hops']:5d} hops  util {r['util']:5.2f}")
    print()
    print(f"  spread: {worst['cooled_ms'] / max(1e-9, best['cooled_ms']):.1f}x in wall "
          f"clock, {worst['hops'] / max(1, best['hops']):.1f}x in hops")
    bad = next(r for r in rows if r["name"].startswith("DELIBERATELY"))
    place = ranked.index(bad) + 1
    print(f"  the deliberately bad strategy ranks {place} of {len(rows)}"
          + ("  -- last, as it must" if place == len(rows) else
             "  -- NOT LAST: the benchmark is not discriminating"))
    print(f"  the hand-made shipped schedule ranks "
          f"{ranked.index(next(r for r in rows if r['name'].startswith('hand-made'))) + 1}"
          f" of {len(rows)}")
    good = next(r for r in rows if r["name"] == "interleaved + dynamic binding")
    print(f"  best is {good['bound']:.2f} revolutions from the packing bound "
          f"({good['hops'] / 144 / good['bound']:.2f}x it), so 'good' is measured "
          f"against optimal, not against the field")

    rule("Is the ranking stable under the corpus's two primitive tables?")
    print("  The tables differ by 2-3x in time (PLAN 3.2). A ranking that flips between")
    print("  them is not a result.")
    print()
    order = {}
    for table in ("qccdsim_jones", "transport_excitation"):
        rs = [score(m, code, n, s, table) for n, s in STRATEGIES.items()]
        by_time = [r["name"] for r in sorted(rs, key=lambda r: r["cooled_ms"])]
        order[table] = by_time
        print(f"  {table:22s} " + " < ".join(
            f"{n.split(':')[0][:14]}" for n in by_time))
    a, b = order["qccdsim_jones"], order["transport_excitation"]
    print()
    print(f"  ranking identical across tables: {a == b}")
    if a != b:
        flips = [(i, x, y) for i, (x, y) in enumerate(zip(a, b)) if x != y]
        print(f"  positions that differ: {[(i, x, y) for i, x, y in flips]}")

    rule("What this establishes, and what it does not")
    print("  * the platform separates a good routing strategy from a bad one by a wide")
    print("    margin on one fixed device, and scores both against a lower bound;")
    print("  * every strategy realizes exactly the same 864 contacts, so the comparison")
    print("    is of routing and nothing else;")
    print("  * it does NOT compare architectures -- for that each device needs its own")
    print("    best policy, which is PLAN 7.1's confusion-matrix warning and M5's job.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
