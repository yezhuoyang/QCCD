"""Goal 3's architecture families and the sizes each is swept over.

The three architectures, their rules, their hardware and what may be swept are Ke's transport deck
("QCCD Transport Architectures", 2026-11-06) and his feedback on the first sweep ("QCCD Studio
Feedback", 2026-11-07): recompile on C2LR, WISE and Cyclone only.  Each
family is data here, a generator and a rule for which sizes to try for a code; its prices are in
`qccd.arch.c2lr`, `qccd.arch.wise` and `qccd.arch.cyclone`, and its compiler in `qccd.compile`.

* C2LR    -- a lattice of X-junctions with one crystal on every leg (arXiv:2403.00756): sweep Cx and
             Cy, choose the species, keep the rest at the deck's defaults.  `grid(Cx, Cy)` has Cx Cy
             junctions, the deck's cells.  Its 2 Cx Cy legs ignore the edge, where the drawn lattice
             has Cx + Cy fewer.  The species is Yb-Ba, the paper's row for the noise model's Ba-Yb
             crystal, and the one row with a reorder-or-not.
* WISE    -- cells of one junction zone and h gate zones, sorted by parallel switched swaps
             (arXiv:2305.12773): sweep h, Cx and Cy, keep the rest at the deck's defaults.
* Cyclone -- one ring of x traps of up to n ions, data still and ancillas rotating in lockstep
             (arXiv:2511.15910): choose the code, sweep x and n <= 30, keep the rest at the paper's
             defaults.  Goal 3's circuits keep one ancilla per check, so a ring holds N + m ions and
             not the paper's N + m/2.

* Helios  -- Quantinuum Helios's published floorplan (arXiv:2511.05465): one 82-site storage ring
             that turns, one X junction, upper and lower caches and eight two-ion operation zones.
             The floorplan is fixed (rules HL1, HL2), so a code has one point, or none where its
             qubits do not fit the machine's 98.  Added 2026-11-19 from the Rules page's profiles.
* Conveyor -- the general conveyor loop: a ring of rail slots with dock spurs, the loop turned
             rigidly and ions docked to gate (the Rules page's profile; Goal 3's family before the
             deck, rows in results/pre_deck/): sweep the loop length and the number of docks.
             Added back 2026-11-19.

Each family is judged by its own rule set (`RULES`).  What a family does not capture is listed in
`README.md` under "open".
"""

from __future__ import annotations

FAMILIES = ("C2LR", "WISE", "Cyclone", "Helios", "Conveyor")
#: each family's own rule set (qccd/ruleset/library), which its points are judged by as an entry's
#: declared set is
RULES = {"C2LR": "c2lr-deck",           # the deck's S1-S10 (checkerboard "isn't needed", S6) and F2's R34
         "WISE": "wise",
         "Cyclone": "cyclone-deck",
         "Helios": "helios-deck",          # helios (the published floorplan, HL1-HL4) and F2's R34
         "Conveyor": "conveyor-deck"}      # conveyor (the standard rules) and F2's R34
#: which compiler a family's points come from
COMPILERS = {"C2LR": "the baseline C2LR router", "WISE": "the baseline WISE router",
             "Cyclone": "the baseline Cyclone router", "Helios": "the baseline Helios batch router",
             "Conveyor": "qccdc (the released compiler), rotation first on the loop"}
#: what a family's points do not yet show; the figures print these under the panels
CAVEATS = {
    "C2LR": "enter and exit are the platform's reading of the deck's open S11; a merge and split around each "
            "gate are priced from the noise model, which marks that open for C2LR",
    "WISE": "a baseline router, not the paper's worst-case sort; no qubit rests on a junction zone, so a chip "
            "holds h, not h + 1, qubits per cell; a junction step's heat is its rails', not C2LR's",
    "Cyclone": "keeps the circuit's gate order, so a round takes about 3 rotations where the paper takes 2 "
               "(fine with a fixed order, feedback Q1); it heats and cools as the other two do (Q2)",
    "Helios": "a baseline batch router, not Quantinuum's real-time compiler: up to sixteen qubits a batch in the "
              "eight zones, every qubit home on the ring between batches; prices are the noise model's, as for "
              "the others, where the Helios paper gives none",
    "Conveyor": "compiled by qccdc as the board entries are; not one of the deck's three architectures, so its "
                "prices are the noise model's defaults",
}
#: the plot's order and each family's colour (its best point; every other point is grey)
COLOURS = {"C2LR": "#54A24B", "WISE": "#F58518", "Cyclone": "#B279A2", "Helios": "#4C78A8",
           "Conveyor": "#E45756"}
#: the qubits Helios holds: 82 in the ring and 16 in the operation zones
HELIOS_QUBITS = 98
#: the species the deck says to choose (its C2LR Hardware model)
SPECIES = "Yb-Ba"


def _grids(n: int) -> list[tuple[int, int]]:
    """Cx x Cy lattices (Cy in {Cx-1, Cx}) whose 2 Cx Cy - Cx - Cy legs hold 1.1N to 3N crystals."""
    out = []
    for a_ in range(3, 40):
        for b_ in (a_ - 1, a_):
            t = 2 * a_ * b_ - a_ - b_
            if b_ >= 2 and 1.1 * n <= t <= 3 * n:
                out.append((a_, b_))
    return out


def points_for(family: str, meta: dict) -> list[dict]:
    """The design points of `family` for a code (`codes/<code>_z/meta.json`)."""
    from qccd.arch import cyclone, wise
    n, anc = int(meta["ions"]), int(meta["n_ancilla"])
    data = n - anc
    if family == "C2LR":
        return [{"family": family, "name": f"c2lr{a_}x{b_}", "generator": "grid", "params": {"a": a_, "b": b_},
                 "species": SPECIES, "deck": {"Cx": a_, "Cy": b_, "species": SPECIES},
                 "about": f"C2LR: {a_} x {b_} cells, a swap-or-stay lattice of {2 * a_ * b_ - a_ - b_} legs, {SPECIES}"}
                for a_, b_ in _grids(n)]
    if family == "WISE":
        return [{"family": family, "name": f"wise_h{h}_{cx}x{cy}", "generator": "wise",
                 "params": {"h": h, "cx": cx, "cy": cy}, "deck": {"h": h, "Cx": cx, "Cy": cy},
                 "about": f"WISE: {cx} x {cy} cells of one junction zone and {h} gate zones"}
                for h, cx, cy in wise.sizes_for(n)]
    if family == "Cyclone":
        return [{"family": family, "name": f"cyclone{x}_n{nt}", "generator": "cyclone", "params": {"x": x, "n": nt},
                 "deck": {"x": x, "n": nt},
                 "about": f"Cyclone: a ring of {x} traps of up to {nt} ions, its corners L-shaped junctions"}
                for x, nt in cyclone.traps_for(data, anc)]
    if family == "Helios":
        if n > HELIOS_QUBITS:
            return []
        return [{"family": family, "name": "helios98", "generator": "helios", "params": {},
                 "about": "Quantinuum Helios: an 82-site storage ring, one X junction, two caches and eight "
                          "two-ion operation zones"}]
    if family == "Conveyor":
        import math
        pts = []
        for f in (0.65, 0.8):
            for v in sorted({anc, math.ceil(anc * 4 / 3)}):
                w = math.ceil(n * f)          # the docks are spaced evenly round the loop: v divides its 2w slots
                while (2 * w) % v:
                    w += 1
                name = f"conveyor{w}_{v}d_j"
                if any(p["name"] == name for p in pts):
                    continue
                pts.append({"family": family, "name": name, "generator": "ring",
                            "params": {"width": w + 2, "height": 2, "verticals": v, "corners": "junction"},
                            "about": f"Conveyor: a {2 * w}-slot loop with {v} dock spurs and junction corners"})
        return pts
    raise KeyError(family)
