"""Emit a corpus of TSIR fixtures for the OCaml round-trip test.

One fixture is an anecdote.  The deck schedule is 1,579 instructions of two SIMD classes
plus docks -- a reader could round-trip it perfectly while mishandling `barrier`, `cool`,
per-ion cooling, multi-segment `via` lists or the `ions`-instead-of-`pairs` spelling of a
gate, none of which the deck program contains.

So the corpus is chosen for *shape coverage*, not size: every builder the platform has,
on every architecture it fits, plus a compiled BB144 program (which carries `measure`,
`reset` and `cool` instructions the deck artifact omits entirely -- see
`completeness_report`).

    python Compiler/bridge/export_programs.py -o Compiler/build/fixtures
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qccd.arch import Architecture  # noqa: E402
from qccd.compile.programs import odd_even, rotate, walk  # noqa: E402

# (fixture stem, architecture, builder) -- builders raise when the device cannot host
# the movement (no closed loop for `rotate`), which is reported, not swallowed.
CASES = [
    ("rotate_ring", "ring144_24v", lambda a: rotate(a, 13)),
    ("rotate_cyclone", "cyclone_base", lambda a: rotate(a, 18)),
    ("rotate_racetrack", "h2_racetrack", lambda a: rotate(a, 10)),
    ("rotate_dual", "cyclone_dual_loop", lambda a: rotate(a, 18)),
    ("walk_grid", "grid9x9", lambda a: walk(a, 8)),
    ("walk_chain", "chain", lambda a: walk(a, 12)),
    ("walk_ladder", "ladder_2x72", lambda a: walk(a, 20)),
    ("walk_deck_cell", "deck_unit_cell", lambda a: walk(a, 8)),
    ("walk_stationary", "stationary_chain", lambda a: walk(a, 1)),
    ("oddeven_cyclone", "cyclone_base", lambda a: odd_even(a, 18)),
]


def load_arch(name: str) -> Architecture:
    path = ROOT / "arch" / f"{name}.arch.json"
    return Architecture.from_json(json.loads(path.read_text(encoding="utf-8")))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", required=True, help="directory for the fixtures")
    ap.add_argument("--bb144", action="store_true",
                    help="also compile the BB[[144,12,12]] ESM round (slow: ~1 min)")
    args = ap.parse_args(argv)

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    written, failed = 0, 0
    for stem, arch_name, build in CASES:
        try:
            arch = load_arch(arch_name)
            prog = build(arch)
            path = outdir / f"{stem}.tsir.json"
            prog.save(path, indent=1)
            kinds = sorted({i.type for i in prog.instructions})
            print(f"  {stem:20s} {arch_name:18s} {len(prog):5d} instr  types={kinds}")
            written += 1
        except Exception as exc:  # noqa: BLE001 -- reported, never swallowed
            print(f"  {stem:20s} {arch_name:18s} FAILED: {exc}")
            traceback.print_exc(limit=1)
            failed += 1

    if args.bb144:
        try:
            from qccd.codes.bb import gross_code
            from qccd.compile import compile_code

            arch = load_arch("ring144_24v")
            code = gross_code()
            res = compile_code(arch, code)
            path = outdir / "bb144_esm.tsir.json"
            res.program.save(path, indent=1)
            kinds = sorted({i.type for i in res.program.instructions})
            print(f"  {'bb144_esm':20s} {'ring144_24v':18s} {len(res.program):5d} instr  "
                  f"types={kinds}")
            written += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  bb144_esm FAILED: {exc}")
            traceback.print_exc(limit=2)
            failed += 1

    print(f"{written} fixtures written to {outdir}, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
