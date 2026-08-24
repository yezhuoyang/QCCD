"""Render a compiled program as the animated page the platform already builds.

`qccd/viz/render.py` turns an `(architecture, program, replay)` triple into one
self-contained HTML file -- ions moving between traps, the metrics strip, the per-step
rule badges, the control panel saying which channels are carrying a waveform. It was
written for hand-built and imported programs; a compiled one is the same object, so it
renders through the same code path and there is nothing to reimplement.

That matters beyond convenience. The animation is driven by the **replay**, not by the
compiler's own account of what it did, so watching a compiled program is watching the
verifier's reconstruction of it. If the compiler and the replay disagreed about where an
ion is, the page would show it.

    python Compiler/bridge/render.py build/out/steane_esm_grid9x9.cooled.tsir.json \
        --arch arch/grid9x9.arch.json -o out/steane_grid.html
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
from qccd.cost.models import corrected_model, deck_model  # noqa: E402
from qccd.ir.tsir import TSIR  # noqa: E402
from qccd.verify import verify  # noqa: E402
from qccd.viz import render_html  # noqa: E402

MODELS = {"corrected": corrected_model, "deck": deck_model}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("program", help="a .tsir.json (cooled, ideally)")
    ap.add_argument("--arch", required=True)
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--model", default="corrected", choices=sorted(MODELS))
    ap.add_argument("--max-frames", type=int, default=20000)
    args = ap.parse_args(argv)

    arch_path = ROOT / args.arch if not Path(args.arch).is_absolute() else Path(args.arch)
    arch = Architecture.from_json(json.loads(arch_path.read_text(encoding="utf-8")))
    prog = TSIR.load(args.program)
    model = MODELS[args.model]()

    rep = verify(prog, arch, model, check_metrics=False)
    res = rep.result
    rules = rep.rules.summary()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    render_html(
        arch, prog, res, model, out,
        max_frames=args.max_frames,
        kicker="compiled by qccdc",
        headline=f"{prog.name} on {arch.name}",
        lede=(f"{len(prog)} instructions, {res.total_steps:,} machine steps, "
              f"{res.total_us / 1000:.2f} ms. "
              f"{len(rules['passed'])} rules pass"
              + (f"; FAILED {rules['failed']}" if rules["failed"] else ".")),
    )
    print(f"{prog.name} on {arch.name}: {len(prog)} instructions, "
          f"{res.total_us / 1000:.2f} ms, {len(rules['passed'])} rules pass")
    print(f"  -> {out}  ({out.stat().st_size // 1024} KB, self-contained)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
