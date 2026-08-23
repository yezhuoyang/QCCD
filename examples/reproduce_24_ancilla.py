#!/usr/bin/env python
"""M1 -- reproduce the shipped 24-ancilla schedule from an independent replay.

    python examples/reproduce_24_ancilla.py

Loads `arch/ring144_24v.arch.json`, imports `INLINE_DATA` out of the standalone HTML,
replays it under the deck's own cost model, and prints every figure the milestone is
stated in.  Nothing here is asserted by the importer: positions, costs and steps are all
recomputed from the initial order and the rotation history.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.arch import load  # noqa: E402
from qccd.cost import deck_model, t1_metrics  # noqa: E402
from qccd.ir import completeness_report, import_schedule  # noqa: E402
from qccd.verify import verify  # noqa: E402


def main() -> int:
    arch = load(ROOT / "arch" / "ring144_24v.arch.json")
    prog = import_schedule(arch, html_path=ROOT / "visualizer_24_ancillas_24_junctions_standalone.html")
    report = verify(prog, arch, deck_model())
    res = report.result
    t1 = t1_metrics(prog, arch, res)
    comp = completeness_report(prog)

    dev = arch.device
    loop = next(iter(dev.loops))
    segs = dev.loop_segments(loop)
    ce = dev.corner_endpoints
    per_hop_cost = sum(3 if ce[s.id] == 2 else 1 for s in segs)
    per_hop_depth = max(3 if ce[s.id] == 2 else 1 for s in segs)

    print("architecture   ", arch.name, "--", dev.summary()["degree_histogram"],
          f"{dev.summary()['n_junction_nodes']} junction nodes, "
          f"{dev.summary()['n_corners']} corners")
    print("program        ", prog.name, f"{len(prog)} instructions")
    print("cost model     ", res.model)
    print()
    print(f"  rigid hop cost   {per_hop_cost:6d}  = "
          f"{len(segs) - 2} straight x 1 + 2 corner x 3   (derived from the graph)")
    print(f"  rigid hop depth  {per_hop_depth:6d}  = max primitive edge depth")
    print()
    print(f"total_cost   {res.total_cost:9.0f}  "
          f"= {t1.rotate_hops} rotate hops x {per_hop_cost} + {t1.n_contacts} contacts x 2")
    print(f"total_steps  {res.total_steps:9d}  "
          f"= {t1.rotate_hops} hops x {per_hop_depth} steps/hop + {t1.n_batches * 2}")
    print(f"{t1.n_batches} batch-ops - {t1.n_contacts} contacts - "
          f"{comp['checks_declared']} checks x 6 members each  "
          f"{'complete' if comp['complete'] else 'INCOMPLETE'}")
    print(f"contact-batch utilization {t1.contact_batch_utilization:.2f} of the "
          f"{t1.contact_batch_limit} limit "
          f"({100 * t1.contact_batch_utilization / t1.contact_batch_limit:.1f} %)")
    print()
    print("batch sizes    ", t1.batch_size_histogram)
    print("cost by class  ", {k: round(v) for k, v in t1.cost_by_class.items()})
    print("steps by class ", t1.steps_by_class)
    print("cost share     ", {k: f"{100 * v:.1f}%" for k, v in t1.cost_share.items()})
    print("movement templates", t1.movement_templates)
    print()
    print("R9 claims checked:", report.metrics["checked"])
    print(f"   per-batch claims checked {report.metrics['batches_checked']}, "
          f"mismatched {report.metrics['batches_mismatched']}")
    print(f"   per-instruction annotations mismatched {report.metrics['instructions_mismatched']}")
    print()
    rs = report.rules.summary()
    print("rules passed :", " ".join(rs["passed"]))
    print("rules failed :", " ".join(rs["failed"]) or "(none)")
    print("rules partial:", " ".join(sorted(rs["partial"])) or "(none)")
    print("rules skipped:")
    for r, why in sorted(rs["skipped"].items()):
        print(f"    {r}: {why}")
    if rs["violations"]:
        print()
        print(f"{rs['violations']} violation(s); first 10:")
        for v in report.rules.violations[:10]:
            print("   ", v)
    print()
    print("omitted by the shipped artifact:", ", ".join(comp["omitted_operations"]))
    return 0 if report.ok() else 1


if __name__ == "__main__":
    raise SystemExit(main())
