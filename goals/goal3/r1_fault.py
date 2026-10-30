"""Reproduce the compiler fault on the largest grids: surface7's Z memory alone on Grid 11x11.

    QCCD_QCCDC=<pinned qccdc> python goals/goal3/r1_fault.py [--size 11 11] [--basis z]

The pinned qccdc compiled `codes/surface7_z` (the Z memory alone, as the sweep did before
2026-10-30) on 11x11, 12x11 and 12x12 into a program that breaks R1: a trap holds 3 ions
against a capacity of 2.  The one-program Z-then-X memory that the releases grade compiles on
the same devices without breaking a rule.  The device is the sweep's (`sweep.device_doc` on the
surface7 release); the compile outputs land in `work/r1_fault/`.  About 7 minutes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", nargs=2, type=int, default=[11, 11])
    ap.add_argument("--basis", choices="zx", default="z")
    a = ap.parse_args()
    from families import points_for
    from sweep import TABLE, device_doc, release
    from qccd.arch import load
    from qccd.cost.models import corrected_model
    from qccd.ir.tsir import TSIR
    from qccd.qec import compile_with_qccdc
    from qccd.qec.experiments import MemoryExperiment
    from qccd.verify import verify

    name = "grid{}x{}".format(*a.size)
    meta = json.loads((HERE / "codes" / "surface7_z" / "meta.json").read_text(encoding="utf-8"))
    point = next((p for p in points_for("Grid", meta) if p["name"] == name), None)
    if point is None:
        print(f"no Grid point {name} for surface7")
        return 2
    d = HERE / "codes" / f"surface7_{a.basis}"
    exp = MemoryExperiment.from_spec(json.loads((d / "detectors.json").read_text(encoding="utf-8")),
                                     (d / "circuit.qasm").read_text(encoding="utf-8"))
    work = HERE / "work" / "r1_fault" / f"{name}_{a.basis}"
    work.mkdir(parents=True, exist_ok=True)
    arch_p = work / "device.arch.json"
    arch_p.write_text(json.dumps(device_doc(point, release("surface7")), indent=1), encoding="utf-8")
    cooled, _ = compile_with_qccdc(exp.qasm, arch_p, work, timeout=1800, table=TABLE)
    rep = verify(TSIR.load(str(cooled)), load(str(arch_p)), corrected_model(TABLE), check_metrics=False)
    bad = [v for v in rep.rules.violations if v.rule == "R1"]
    print(f"{name} surface7_{a.basis}: rules failed {sorted(rep.rules.summary().get('failed') or [])}, "
          f"{len(bad)} R1 violation(s)")
    for v in bad[:10]:
        print("  ", v)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
