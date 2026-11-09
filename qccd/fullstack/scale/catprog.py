"""The cat programs FormalRV's Gidney 2025 listing needs, built at the machine's distance and checked (docs/
FULLSTACK_PLAN.md, slice S10 step 3).  A shape is a tuple of targets, `tiled_cat.Target`, relative to its own
corner, so a program is built once and every use is a translated copy; what it reserves between tiles is `cells`.

    ("rep", targets)                  one repetition, as `tiled_cat.rep_circuit` writes it
    ("wide", width, control)          one repetition of the register-wide cat over `width` tiles in a row,
                                      the control patch at tile `control` from the row's first

`machine_us` is a program's time, from its own replay, cooled as every check plays it; `check` runs every rule
(`qccd.verify`) and R10 (the certificate replayed against the source circuit, block by block).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from ...compile import tiled_cat as C
from . import machine as M

__all__ = ["Built", "normal", "build_rep", "build_wide", "machine_us", "check", "cells"]


@dataclass
class Built:
    shape: tuple
    targets: tuple
    grid: C.Grid
    arch: object
    walker: C.Walker
    cert: dict
    qasm: str
    homes: tuple = ()


def normal(targets) -> tuple:
    """Targets moved so the least x and y are 0, with a row of tiles above and a column to the left for the walks."""
    x0, y0 = min(t.x for t in targets), min(t.y for t in targets)
    return tuple(C.Target(t.x - x0, t.y - y0, t.turned, t.letter) for t in targets)


def _model():
    from ...cost.models import corrected_model
    return corrected_model("quantinuum")


def _grid(targets) -> tuple:
    width = max(t.x for t in targets) + 1
    height = max(t.y for t in targets) + 1
    grid = C.Grid(M.DISTANCE, width, height)
    return grid, M.device(grid.rows, grid.cols)


def build_rep(targets, *, tries: int = 300, seeds=(1, 2), homes=None, start=None) -> Built:
    """The repetition's program: its free ions' homes as given, or annealed, from `start` if given."""
    targets = normal(targets)
    grid, arch = _grid(targets)
    if homes is None:
        best = None
        for seed in seeds:
            got = C.anneal(targets, grid, arch, tries=tries, seed=seed, start=start)
            if best is None or got[0] < best[0]:
                best = got
        homes = best[1]
    b, cert, qasm = C.play_rep(targets, grid, arch, homes, grid.taken())
    return Built(("rep", targets), targets, grid, arch, b, cert, qasm, tuple(homes))


def build_wide(width: int, control: tuple = (0, -1), mask=None) -> Built:
    """A row of `width` target tiles (X̄ each), the control's tile at `control` from the first; a masked cat acts only
    on the tiles of `mask`."""
    cx, cy = control
    row_y = max(0, -cy)
    tiles = [C.Target(cx + (0 if cx >= 0 else -cx), row_y + cy, False, "X")]
    tiles += [C.Target(i + (0 if cx >= 0 else -cx), row_y, False, "X") for i in range(width)]
    targets = normal(tiles)
    grid, arch = _grid(targets)
    b, cert, qasm, _parts = C.play_wide(targets, grid, arch, mask=mask)
    return Built(("wide", width, control), targets, grid, arch, b, cert, qasm)


def cooled(b: Built):
    from ...compile.cooling import insert_cooling
    return insert_cooling(b.walker.prog, b.arch, _model()).program


def machine_us(b: Built, prog=None) -> float:
    from ...verify.replay import replay
    return replay(prog or cooled(b), b.arch, _model(), check_rules=False).total_us


def check(b: Built, tmp) -> dict:
    """{"failed", "us", "r10_blocks", "ions"}.  R10's helper lives with its tests, so the repo's tests directory must
    be importable."""
    tests = str(Path(__file__).resolve().parents[3] / "tests")
    if tests not in sys.path:
        sys.path.insert(0, tests)
    from test_c2lr_router import _r10_blocks

    from ...verify import verify
    rep = verify(cooled(b), b.arch, _model(), check_metrics=False)
    failed, us = rep.rules.summary().get("failed"), rep.result.total_us
    del rep
    blocks = _r10_blocks(b.walker.prog, b.cert, b.arch, b.qasm, Path(tmp))
    return {"failed": failed, "us": us, "r10_blocks": blocks, "ions": len(b.walker.pos)}


def cells(b: Built) -> list:
    """The stretches of seam the program's walks use, relative to its first target's tile (a wide cat's: its row's
    first): what a schedule reserves."""
    first = b.targets[1] if b.shape[0] == "wide" else b.targets[0]
    x0, y0 = first.x, first.y
    out = set()
    for node in b.walker.walked:
        c = b.grid.seam_cell(node)
        if c is not None:
            out.add((c[0], c[1] - x0, c[2] - y0))
    return sorted(out)
