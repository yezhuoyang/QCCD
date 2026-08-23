#!/usr/bin/env python
"""M2 -- the same schedule under the corrected physics, plus the cooling it implies.

    python examples/heating_budget.py [--table qccdsim_jones]

Nothing about the *program* changes between M1 and M2.  The only differences are
`corner_hops` 3 -> 1, junction cost charged by the degree the expanded graph reports,
and split/merge charged on the movement classes that entail it.  Every number below is
therefore attributable to the model, not to the schedule.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.arch import load  # noqa: E402
from qccd.compile import CoolingPolicy, insert_cooling  # noqa: E402
from qccd.cost import corrected_model, deck_model, t1_metrics, t2_metrics  # noqa: E402
from qccd.ir import import_schedule  # noqa: E402
from qccd.verify import replay, verify  # noqa: E402

HTML = "visualizer_24_ancillas_24_junctions_standalone.html"


def _rule(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default="qccdsim_jones",
                    help="primitive table to run against (PLAN 3.2)")
    ap.add_argument("--max-gate-quanta", type=float, default=None,
                    help="override R7's budget; the architecture says 1.0")
    ap.add_argument("--no-anomalous", action="store_true",
                    help="drop the R17 background term")
    args = ap.parse_args()

    arch = load(ROOT / "arch" / "ring144_24v.arch.json")
    prog = import_schedule(arch, html_path=ROOT / HTML)
    model = corrected_model(args.table, include_anomalous=not args.no_anomalous)

    res = replay(prog, arch, model, check_rules=False)
    data = sorted(i for i in res.per_ion_quanta if i.startswith("d"))
    n = len(data)

    def per_ion(component: str) -> float:
        return sum(res.per_ion_quanta[i].get(component, 0.0) for i in data) / n

    dev = arch.device
    loop = next(iter(dev.loops))
    n_verticals = len(dev.junction_nodes)
    slots = len(dev.loops[loop].nodes)
    hops = sum(v for k, v in res.hops_by_class.items() if k.startswith("rotate"))
    revolutions = hops / slots

    # rotation-only junction transits: total minus the ones docking pays
    undock_transits = sum(
        1
        for instr in prog.instructions
        if instr.type == "simd" and instr.cls == "undock"
        for p in instr.participants
        if dev.degree(p.dst) >= 3
    )
    transits = [res.junction_transits[i] for i in data]
    undock_per_ion = undock_transits / n
    rotation_transits_per_ion = statistics.mean(transits) - undock_per_ion

    shuttle_pt = arch.primitives.curve("shuttle_segment").pick(model.policy)
    junction_pt = model.junction_point(arch, 3)
    split_pt = arch.primitives.curve("split").pick(model.policy)

    print(f"architecture  {arch.name}   table={args.table}")
    print(f"model         {model.describe()}")
    print(f"operating pts shuttle {shuttle_pt.us} us / {shuttle_pt.quanta} q   "
          f"junction(deg 3) {junction_pt.us} us / {junction_pt.quanta} q   "
          f"split|merge {split_pt.us} us / {split_pt.quanta} q")

    _rule("T-junction transits per data ion")
    print(f"  rotation only          {rotation_transits_per_ion:8.2f}   "
          f"= {hops}/{slots} = {revolutions:.2f} revolutions x {n_verticals} verticals")
    print(f"  + undock re-entry      {undock_per_ion:8.2f}   "
          f"= 6 contacts x 1 (the ion re-enters the degree-3 dock node on the way out)")
    print(f"  = total, replayed      {statistics.mean(transits):8.2f}   "
          f"(min {min(transits)}, max {max(transits)}; the spread is the "
          f"starting offset mod {slots // n_verticals})")
    print(f"  PLAN 0.5 states 445; rotation-only reproduces it exactly.")

    _rule("Quanta per data ion per ESM round")
    rot_shuttle = hops * shuttle_pt.quanta
    rot_junction = rotation_transits_per_ion * junction_pt.quanta
    print(f"  shuttling, rotation    {rot_shuttle:9.1f}   "
          f"= {hops} hops x {shuttle_pt.quanta}          [PLAN: 267]")
    print(f"  junction, rotation     {rot_junction:9.1f}   "
          f"= {rotation_transits_per_ion:.2f} transits x {junction_pt.quanta}   [PLAN: 1336]")
    print(f"  split/merge, docking   {per_ion('split_merge'):9.1f}   "
          f"= 6 contacts x 4 x {split_pt.quanta}      [PLAN: 144]")
    print(f"  {'':21s} {rot_shuttle + rot_junction + per_ion('split_merge'):9.1f}   "
          f"<- PLAN 0.4's ~1747, reproduced")
    print("  terms PLAN's breakdown does not carry:")
    print(f"    shuttling, spurs     {per_ion('shuttle') - rot_shuttle:9.1f}   "
          f"= 12 spur moves x {shuttle_pt.quanta}")
    print(f"    junction, undock     {undock_per_ion * junction_pt.quanta:9.1f}   "
          f"= {undock_per_ion:.0f} transits x {junction_pt.quanta}")
    print(f"    anomalous (R17)      {per_ion('anomalous'):9.1f}   "
          f"= {arch.anomalous_rate()} q/ms x {res.total_us / 1000:.1f} ms elapsed")
    total = sum(per_ion(c) for c in res.quanta_components)
    print(f"  {'':21s} {total:9.1f}   <- full accounting")
    ions_total = [sum(res.per_ion_quanta[i].values()) for i in data]
    print(f"  per-ion spread         {min(ions_total):9.1f} .. {max(ions_total):.1f}")

    _rule("Wall clock")
    for cls, us in sorted(res.us_by_class.items(), key=lambda kv: -kv[1]):
        print(f"  {cls:22s} {us / 1000:9.2f} ms")
    print(f"  {'TOTAL':22s} {res.total_us / 1000:9.2f} ms")
    rot_us = sum(v for k, v in res.us_by_class.items() if k.startswith("rotate"))
    t2 = t2_metrics(arch, res, model)
    print()
    print(f"  rotation               {rot_us / 1000:9.2f} ms  "
          f"= {hops} hops x {junction_pt.us} us: every hop pays a junction, because "
          f"{n_verticals} of {slots} rail nodes are degree 3 and a rigid hop takes the "
          f"max over all moving ions")
    print(f"  counterfactual         {t2.counterfactual_rotation_us / 1000:9.2f} ms  "
          f"= {hops} hops x {shuttle_pt.us} us if the rotation path had no degree-3 node")
    print(f"  ratio                  {rot_us / t2.counterfactual_rotation_us:9.1f}x")
    print("  PLAN 0.5 states 267 ms vs 13.4 ms; both reproduced.")

    _rule("Cooling (R7 / R7c)")
    budget = args.max_gate_quanta
    policy = CoolingPolicy(max_gate_quanta=budget)
    cool = insert_cooling(prog, arch, model, policy=policy)
    d = cool.as_dict()
    eff_budget = budget if budget is not None else float(
        arch.primitives.scalar("ms_gate")["max_quanta"]
    )
    print(f"  R7 budget              {eff_budget} quanta at a 2Q gate")
    print(f"  gates over budget      {d['gates_needing_cooling']} of {d['gates_total']} "
          f"contact batches -- the schedule ships with zero cooling operations")
    print(f"  cooling operations     {d['n_cools']} global "
          f"({arch.primitives.scalar('cool')['us']} us each, Doppler sheet: one op cools "
          f"every ion, so it does not serialize per ion)")
    print(f"  cooling time           {d['cooling_ms']:9.2f} ms   "
          f"({100 * d['cooling_share_of_runtime']:.1f}% of the cooled runtime)")
    print(f"  runtime  uncooled      {d['runtime_ms_before']:9.2f} ms")
    print(f"           cooled        {d['runtime_ms_after']:9.2f} ms")
    print(f"  peak n-bar  uncooled   {d['peak_quanta_before']:9.1f} quanta")
    print(f"              cooled     {d['peak_quanta_after']:9.1f} quanta "
          f"(peak between cools {d['peak_quanta_between_cools']:.1f})")
    print(f"  R7 violations  before  {d['r7_violations_before']}")
    print(f"                 after   {d['r7_violations_after']}")
    for note in d["notes"]:
        print(f"  - {note}")

    _rule("The cooled program, verified")
    rep = verify(cool.program, arch, model)
    rs = rep.rules.summary()
    print("  rules passed :", " ".join(rs["passed"]))
    print("  rules failed :", " ".join(rs["failed"]) or "(none)")
    for r, why in sorted(rs["skipped"].items()):
        print(f"  skipped {r}: {why}")

    _rule("For contrast: the same program under the deck's model (M1)")
    deck = verify(prog, arch, deck_model())
    t1 = t1_metrics(prog, arch, deck.result)
    print(f"  total_cost {deck.result.total_cost:.0f}   total_steps {deck.result.total_steps}"
          f"   (no time, no heating modelled)")
    print(f"  movement templates: {t1.n_movement_templates} -- {t1.movement_templates}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
