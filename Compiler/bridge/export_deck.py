"""Export the shipped 24-ancilla schedule as a TSIR document -- the C0 fixture.

C0 asks a deliberately unambitious question: can OCaml read a real hardware program and
write it back without changing what it means?  The shipped deck schedule is the right
input because it is the project's first external oracle -- 1,578 instructions, 396
batch-ops, 864 contacts -- and because its replay figures are known exactly:

    total_cost   397184  = 2672 rotate hops x 148 + 864 contacts x 2
    total_steps    8808  = 2672 hops x 3 + 792

If the OCaml round-trip perturbs one participant, one `via` list or one template delta,
those numbers move and `check_tsir.py` says so.  A round-trip test against a program
nobody wrote for the occasion is worth more than any number of synthetic ones.

    python Compiler/bridge/export_deck.py -o Compiler/build/deck24.tsir.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qccd.arch import Architecture  # noqa: E402
from qccd.ir.import_deck import DEFAULT_HTML, import_schedule  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arch", default="arch/ring144_24v.arch.json")
    ap.add_argument("--html", default=None,
                    help="the artifact; defaults to the one in the repo root")
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args(argv)

    arch_path = ROOT / args.arch if not Path(args.arch).is_absolute() else Path(args.arch)
    arch = Architecture.from_json(json.loads(arch_path.read_text(encoding="utf-8")))
    # resolve against the repo root, not the cwd: the driver script runs from
    # Compiler/ and the artifact lives beside the architectures.
    html = Path(args.html) if args.html else ROOT / DEFAULT_HTML
    prog = import_schedule(arch, html_path=html, arch_spec=args.arch)

    out = Path(args.out)
    prog.save(out, indent=1)
    print(f"{prog.name}: {len(prog)} instructions, id_seq {prog.id_seq}")
    print(f"  templates {prog.templates()}")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
