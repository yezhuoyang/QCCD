"""Goal 3's architecture families and the sizes each is swept over.

A family is a generator, a control block and a rule for which sizes to try for a code of N ions
with A ancillas.  The sizes start at the smallest the family can plausibly hold (the compiler
refuses what does not fit, and a refusal is a row of the results) and stop where a larger
machine stops being a different design.

PROVISIONAL (2026-10-29).  The four architectures of the 10/26 plan -- C2LR, WISE, Cyclone,
Conveyor -- with their shuttling, broadcast, gate and measurement rules are Ke's to define;
until those rules are written, each family is the closest device this platform already
models, plus the plain grid as the baseline:

* Grid     -- the baseline QCCD lattice of arXiv:2004.04706: `grid(a, b)`, a trap on every
              wire, each trap driven by its own DACs ("direct" wiring), one movement class per
              cycle.
* WISE     -- the same lattice wired as arXiv:2305.12773 proposes: broadcast DACs, a switch
              per site, ONE movement class per cycle (the deck's `deck_unit_cell` block).
* C2LR     -- the same lattice as a swap-or-stay device (arXiv:2403.00756; docs/c2lr.md):
              `Machine.c2lr_grid`'s control (crystals move only by swap-or-stay steps, a
              direction bit per leg, 27 shared signals, the demonstrated diagonals only) and
              its steps' price (Yb-Ba, the paper's row for physics@2's Ba-Yb crystal: 400 us),
              on the Goal's physics (sweep.device_doc).  The reference compiler emits plain
              shuttles, which this device does not offer, so every size fails R4 and C1 until
              a C2LR router emits the steps.
* Cyclone  -- one junction-free loop moved rigidly (Khan et al., HPCA 2026): `ring(W, 2, 0)`
              with trap sites, O(1) DACs.
* Conveyor -- a loop of rail slots with dock spurs, the loop rotated rigidly and ions docked
              to gate (`ring(W, 2, V)`, the study's `ring144_24v` shape; qccdc's conveyor pass).

What a family does NOT yet capture is listed in `README.md` under "open".
"""

from __future__ import annotations

import math

_WISE = {
    "max_simd_classes_per_cycle": 1,
    "wiring": {"scheme": "wise", "dacs_dynamic": 32, "shim_per_dac": 100, "electrodes_per_trap": 24,
               "electrodes_per_junction": 48, "compensation_electrodes_per_trap": 8,
               "note": "WISE (arXiv:2305.12773): 32 broadcast DACs, constant in array size"},
    "channels": {"grouping": "broadcast", "roles": {"linear_h": 6, "linear_v": 6, "junction": 4},
                 "differential": 2, "switch_per_site": True},
}
#: qccd-physics@2's crystal is Ba-Yb (137Ba+ qubit, 171Yb+ coolant), so the paper's row is
#: Yb-Ba: 400 us a step, and reorder-or-not, which it measures for that pair alone.  Only the
#: demonstrated diagonals: the proposal's checkerboard steps are not in the paper.
_C2LR = {"species": "Yb-Ba", "checkerboard": False}
_DIRECT = {   # the shipped grid9x9's own block: every electrode its own DAC
    "max_simd_classes_per_cycle": 1,
    "wiring": {"scheme": "direct", "dacs_dynamic": 100, "shim_per_dac": 1, "electrodes_per_trap": 24,
               "electrodes_per_junction": 48, "compensation_electrodes_per_trap": 8},
    "channels": {"grouping": "direct", "roles": {"linear_h": 6, "linear_v": 6, "junction": 4},
                 "differential": 2, "switch_per_site": True},
}

FAMILIES = ("Grid", "WISE", "C2LR", "Cyclone", "Conveyor")
#: what a family's points do not yet show; the figures print these under the panels
CAVEATS = {
    "WISE": "same program and time as Grid until WISE's broadcast rules exist",
    "C2LR": "the reference compiler moves ions by plain shuttles, which a swap-or-stay device does "
            "not offer (R4, C1): every size fails until a C2LR router exists",
    "Cyclone": "compiled without rigid rotation (qccdc has no Cyclone scheduler yet): an upper bound",
}
#: the plot's order and each family's colour (its best point; every other point is grey)
COLOURS = {"Grid": "#4C78A8", "WISE": "#F58518", "C2LR": "#54A24B", "Cyclone": "#B279A2",
           "Conveyor": "#E45756"}


def _grids(n: int) -> list[tuple[int, int]]:
    """a x b lattices (b in {a-1, a}) whose 2ab-a-b traps hold 1.1N to 3N ions."""
    out = []
    for a_ in range(3, 40):
        for b_ in (a_ - 1, a_):
            t = 2 * a_ * b_ - a_ - b_
            if b_ >= 2 and 1.1 * n <= t <= 3 * n:
                out.append((a_, b_))
    return out


def points_for(family: str, meta: dict) -> list[dict]:
    """The design points of `family` for a code (`codes/<code>_z/meta.json`)."""
    n, anc = int(meta["ions"]), int(meta["n_ancilla"])
    pts: list[dict] = []
    if family in ("Grid", "WISE"):
        ctl = {"Grid": _DIRECT, "WISE": _WISE}[family]
        for a_, b_ in _grids(n):
            pts.append({"family": family, "name": f"{family.lower()}{a_}x{b_}", "generator": "grid",
                        "params": {"a": a_, "b": b_}, "control": ctl,
                        "about": f"{family}: a {a_}x{b_} junction lattice, {2*a_*b_-a_-b_} traps"})
    elif family == "C2LR":
        for a_, b_ in _grids(n):
            pts.append({"family": family, "name": f"c2lr{a_}x{b_}", "generator": "grid",
                        "params": {"a": a_, "b": b_}, "c2lr": _C2LR,
                        "about": f"C2LR: a {a_}x{b_} swap-or-stay lattice, {2*a_*b_-a_-b_} legs"})
    elif family == "Cyclone":
        for f in (1.04, 1.15, 1.3, 1.5):
            w = max(3, math.ceil(n * f / 2))
            pts.append({"family": family, "name": f"cyclone{w}", "generator": "ring",
                        "params": {"width": w, "height": 2, "verticals": 0, "site_zone": "trap"},
                        "about": f"Cyclone: one junction-free loop of {2*w} trap sites"})
    elif family == "Conveyor":
        for f in (0.65, 0.8):
            for v in sorted({anc, math.ceil(anc * 4 / 3)}):
                # the docks are spaced evenly round the loop: v must divide its 2w slots
                w = math.ceil(n * f)
                while (2 * w) % v:
                    w += 1
                pts.append({"family": family, "name": f"conveyor{w}_{v}d", "generator": "ring",
                            "params": {"width": w, "height": 2, "verticals": v},
                            "about": f"Conveyor: a {2*w}-slot loop with {v} dock spurs"})
    else:
        raise KeyError(family)
    # one name per point, whatever the arithmetic produced
    seen: set = set()
    return [p for p in pts if not (p["name"] in seen or seen.add(p["name"]))]
