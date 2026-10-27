"""`python -m qccd.fullstack ...` -- a FormalRV program, every layer, on QCCD.

    run PROG.prog -o DIR [--machine M --sysspec K]
        the default: FormalRV's .prog -> .qppm -> .qec, QCCD's lowering to a gadget program,
        checked against the .prog's gate, scheduled and signed off on the full-stack machine;
        DIR/gadgets/index.html is the gadget design page with the stack in it.  With a machine
        profile, FormalRV's own .sysprog and VM verdict are the abstract estimate beside it.
    run PROG.prog -o DIR --flat --machine M --sysspec K --arch A
        the cross-check of slice 1: the .sysprog devirtualized and compiled flat onto one
        Studio device, every hardware instruction checked through every layer
    run PROG.prog -o DIR --fold [--floorplan fullstack_wide]
        every gate scheduled once per context, its calls kept folded (qccd.gadget.fold): a run of
        hours is a page that plays a window at a time
    machine [--wide MEMORIES]
        regenerate examples/gadgets/floorplans/fullstack.floor (or fullstack_wide.floor)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m qccd.fullstack")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="one FormalRV program, every layer, on QCCD")
    r.add_argument("prog", type=Path)
    r.add_argument("-o", "--out", type=Path, required=True)
    r.add_argument("--machine", type=Path, help="a FormalRV machine profile (for the abstract estimate)")
    r.add_argument("--sysspec", type=Path, help="FormalRV system contracts (with --machine)")
    r.add_argument("--flat", action="store_true", help="the slice-1 cross-check on one Studio device")
    r.add_argument("--arch", type=Path, help="the Studio device for --flat (.arch.json)")
    r.add_argument("--code", default="surface")
    r.add_argument("--distance", type=int, default=3)
    r.add_argument("--specialize", action="append", default=[], help="NAME=VALUE (FormalRV --specialize)")
    r.add_argument("--physics", default="physics@2")
    r.add_argument("--title", default="")
    r.add_argument("--fold", action="store_true",
                   help="schedule every gate once per context and keep its calls folded (qccd.gadget.fold)")
    r.add_argument("--floorplan", default="fullstack", help="the machine (examples/gadgets/floorplans/NAME.floor)")
    m = sub.add_parser("machine", help="regenerate the full-stack machine's floorplan")
    m.add_argument("--wide", type=int, metavar="MEMORIES",
                   help="write fullstack_wide.floor instead: 4 benches, 4 factories, this many memories")
    a = ap.parse_args(argv)
    if a.cmd == "machine":
        from ..gadget.library import LeafLibrary
        from .machine import FLOORPLAN, write_floorplan
        if a.wide:
            print(write_floorplan(LeafLibrary("out/gadgets/cache"), FLOORPLAN.with_name("fullstack_wide.floor"),
                                  wide={"memories": a.wide}))
        else:
            print(write_floorplan(LeafLibrary("out/gadgets/cache")))
        return 0
    spec = dict(s.split("=", 1) for s in a.specialize)
    if not a.flat:
        from .town import run
        t = run(a.prog, out=a.out, code=a.code, distance=a.distance, specialize=spec,
                machine=a.machine, sysspec=a.sysspec, title=a.title, fold=a.fold, floorplan=a.floorplan)
        if not a.fold:
            print(t["alg"])
        print(json.dumps({k: t[k] for k in ("qec_statements", "linked", "absorbed", "abstract", "concrete",
                                            "checks", "total_s")}, indent=1))
        c = t["contract"]
        if c.get("passed") and c.get("kind") == "outcome":
            print("semantics:", f"{c['spec']} in every branch ({c['random_outcomes']} random outcomes, symbolic)")
        elif c.get("passed"):
            print("semantics:", f"exactly {c['contract']} in all {c['branches']} branches")
        else:
            print("semantics:", c.get("why") or "FAILED: " + str(c.get("bits") or ""))
        print("page:", t["page"])
        return 0
    if not (a.machine and a.sysspec and a.arch):
        ap.error("--flat needs --machine, --sysspec and --arch")
    from .pipeline import run
    t = run(a.prog, machine=a.machine, sysspec=a.sysspec, arch=a.arch, out=a.out, code=a.code,
            distance=a.distance, specialize=spec, physics=a.physics)
    print(json.dumps({k: t[k] for k in ("program", "abstract", "concrete", "physical_qubits", "total_s")}, indent=1))
    c = t["checks"]
    print("rules:", len(c["rules"]["passed"]), "passed,", c["rules"]["failed"] or "none", "failed;",
          "correspondence", c["correspondence"]["ok"], "; R10 semantic", c["r10_semantic"]["o2_semantics"],
          c["r10_semantic"]["order"], "; R10 Lean", c["r10_lean"].get("status"))
    s = c["stack"]
    print(f"stack: {s['instructions_checked']} instructions, {s['event_chains_resolved']} chains through "
          f"every layer, {'ok' if s['ok'] else 'PROBLEMS: ' + '; '.join(s['problems'][:3])}")
    for st in t["steps"]:
        print(f"  {st['s']:8.2f} s  {st['step']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
