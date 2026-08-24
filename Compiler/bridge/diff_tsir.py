"""Deep-compare two TSIR documents -- the C0 round-trip test.

Replaying to the same cost is necessary but *not sufficient*.  The replay reads
positions, participants and templates; it never looks at `meta`, and it is indifferent
to a dropped `operating_point` or a `quanta` map rewritten from int to float.  A reader
that silently discarded provenance would pass the cost check and fail the project later,
when someone asks which batch an instruction came from and the answer is gone.

So this compares the parsed documents field by field and reports the first N
disagreements with a path, rather than asserting equality of the two files' bytes --
which would fail on nothing more interesting than `indent=1` versus compact separators.

    python Compiler/bridge/diff_tsir.py a.tsir.json b.tsir.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qccd.ir.tsir import TSIR  # noqa: E402


def walk(a, b, path: str, out: list[str], limit: int) -> None:
    if len(out) >= limit:
        return
    if type(a) is not type(b):
        # int vs float is a real finding, not a formatting detail: it means one side
        # re-typed a numeric annotation on the way through.
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and a == b:
            out.append(f"{path}: numeric type changed {type(a).__name__} -> "
                       f"{type(b).__name__} (value {a} preserved)")
            return
        out.append(f"{path}: type {type(a).__name__} != {type(b).__name__}")
        return
    if isinstance(a, dict):
        for k in a:
            if k not in b:
                out.append(f"{path}.{k}: MISSING on the right")
        for k in b:
            if k not in a:
                out.append(f"{path}.{k}: EXTRA on the right")
        # key order is meaningful here: both writers emit a fixed order, so a
        # difference means one of them reordered, which is worth knowing early
        if list(a) != list(b) and set(a) == set(b):
            out.append(f"{path}: key ORDER differs ({list(a)} vs {list(b)})")
        for k in a:
            if k in b:
                walk(a[k], b[k], f"{path}.{k}", out, limit)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} != {len(b)}")
            return
        for i, (x, y) in enumerate(zip(a, b)):
            walk(x, y, f"{path}[{i}]", out, limit)
    elif a != b:
        out.append(f"{path}: {a!r} != {b!r}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("left")
    ap.add_argument("right")
    ap.add_argument("--limit", type=int, default=25)
    args = ap.parse_args(argv)

    # Parse through TSIR on both sides, so the comparison is of *programs*, not of
    # whatever the two writers happened to spell.
    a = TSIR.load(args.left).to_json()
    b = TSIR.load(args.right).to_json()

    out: list[str] = []
    walk(a, b, "", out, args.limit)

    if not out:
        na = len(a["instructions"])
        print(f"IDENTICAL: {na} instructions, every field preserved")
        return 0
    print(f"{len(out)} difference(s){' (truncated)' if len(out) >= args.limit else ''}:")
    for line in out:
        print(f"  {line}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
