#!/usr/bin/env python
"""Set up a machine and program it, in Python.  The whole surface, in one file.

    python examples/program_in_python.py

Nothing here is a special path: the objects built below are the same ones the importer
produces from the shipped artifact and the same ones the compiler emits, so a
hand-written program and a compiled one are indistinguishable to the verifier.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd import Machine  # noqa: E402
from qccd.codes import gross_code  # noqa: E402
from qccd.compile import CompilePolicy  # noqa: E402


def rule(t):
    print()
    print(t)
    print("-" * len(t))


def main() -> int:
    # ---------------------------------------------------------------- 1. set up
    rule("1. Describe a machine")
    m = Machine.ring(width=72, height=2, verticals=24, name="my_ring")
    print(f"  {m}")
    print(f"  {len(m.sites())} trap sites, {len(m.junctions())} junctions, "
          f"docks at {m.sites('dock')[:4]} ...")

    # retune it: capacity, primitive curves, gate budget, wiring -- all from Python
    m.set_zone("data", capacity=2)
    m.set_primitive("ms_gate", max_quanta=1.0)
    m.set_wiring(scheme="wise", dacs_dynamic=100, shim_per_dac=100)
    hw = m.resources()
    print(f"  wiring {hw.scheme}: {hw.dacs} DACs for {hw.electrodes} electrodes "
          f"({hw.dacs_per_trap:.3f} per trap)")

    # ---------------------------------------------------------------- 2. program
    rule("2. Program it")
    p = m.program("hand_written")
    p.init({**{f"d{i}": f"S{i}" for i in range(144)},          # one data ion per slot
            **{f"a{s}": f"A{s}" for s in range(0, 144, 6)}})   # one ancilla per dock
    p.rotate(+13, batch=0)                     # one template, 144 ions, 13 hops
    # after a +13 shift the ions at docks S120 and S126 are the ones that started
    # 13 slots back -- the rotation is the addressing, which is the whole idea
    with p.cycle("dock", batch=0) as c:        # one class, two participants
        c.move("d107", "S120", "A120", via=["V120"])
        c.move("d113", "S126", "A126", via=["V126"])
    p.gate("CX", [("d107", "a120"), ("d113", "a126")],
           sites=["A120", "A126"], batch=0)
    with p.cycle("undock", batch=0) as c:
        c.move("d107", "A120", "S120", via=["V120"])
        c.move("d113", "A126", "S126", via=["V126"])
    p.cool()
    print(f"  {p}")

    r = m.run(p)
    print(f"  {r}")
    print(f"  quanta per ion: "
          f"{ {k: round(v, 2) for k, v in r.quanta_per_ion().items() if v} }")
    print(f"  rules passed: {' '.join(r.rules_passed)}")

    # ---------------------------------------------------------------- 3. the API stops illegal programs
    rule("3. The API refuses what the hardware cannot run")
    for what, fn in [
        ("a cycle with two classes",
         lambda: p.cycle("dock", mode="sideways")),
        ("placing an ion on a node that does not exist",
         lambda: m.program("bad").init({"d0": "S99999"})),
        ("rotating an open path",
         lambda: Machine.chain(8).program("bad").fill().rotate(1)),
        ("docking an ion that the rotation moved elsewhere",
         lambda: m.program("bad").init({"d0": "S0"}).rotate(+1)
                  .move("d0", "S0", "A0", via=["V0"]).run()),
    ]:
        try:
            fn()
            print(f"  {what:46s} NOT CAUGHT")
        except Exception as exc:
            print(f"  {what:46s} {type(exc).__name__}: {exc}")

    # ---------------------------------------------------------------- 4. compile
    rule("4. Compile a code through the pipeline")
    code = gross_code()
    print(f"  code {code.name}: {code.summary()}")
    result = m.compile(code, policy=CompilePolicy(insert_cooling=False))
    print()
    print(result.report())

    rule("5. The compiled program is the same kind of object")
    out = m.run(result.program, check_metrics=False)
    print(f"  {out}")
    print(f"  {result.hops} rotate hops over {result.batches} batches, "
          f"{result.contacts} contacts")
    print(f"  the shipped hand-made schedule needs 2672 hops / 396 batches / "
          f"864 contacts for the same code")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
