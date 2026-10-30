"""Write the C2LR boards (task releases) -- a maintainer tool.  docs/c2lr.md section 7.

    python tools/make_c2lr_releases.py               # writes any C2LR release that is missing
    python tools/make_c2lr_releases.py --out DIR     # into DIR instead (a rehearsal)

The C2LR boards are a TRACK of their own (`"track": "c2lr"`), ranked beside the shuttle boards
and never with them (the user, 2026-10-30): the memory boards' own experiments on C2LR
swap-or-stay lattices (arXiv:2403.00756).  Each is one shuttle memory board and one species pair:

* the circuit and the detectors are the shuttle board's (rep5_mem@1, surface3_mem@1), byte for
  byte, and so are its noise model, its sampling budget and seed, its metrics and its limits;
* the physics is the shuttle board's plus what a C2LR device adds (`qccd.arch.c2lr`): the
  swap-or-stay step (and, for Yb-Ba, reorder-or-not) with the paper's numbers, the merge and
  split of a gate in one leg (the platform's own slow point, which the paper does not give),
  and the species pair -- exactly the blocks `Machine.c2lr_grid` writes, so a lattice made
  there is entered on the board with nothing changed;
* the rules are the C2LR rule set, `c2lr` (every standard rule and C1-C10; the two-pair
  checkerboard steps are allowed and marked assumed in every report);
* the suggested start is the lattice the baseline router (`qccd.compile.c2lr_router`) does
  best on, measured below.

Both species pairs the paper measures are boards: the step's time and heat differ (Ba-Sr 312.5 us,
Yb-Ba 400 us and a hotter stay), and only Yb-Ba has reorder-or-not.  A published release is
immutable: change a board by adding `<task>@2`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from qccd.arch.c2lr import C2LR_PHYSICS, c2lr_primitives, c2lr_species  # noqa: E402
from qccd.workspace.tasks import RELEASES_DIR, build_release, find_release  # noqa: E402

SPECIES = {"Ba-Sr": "basr", "Yb-Ba": "ybba"}
#: (shuttle board, order of the first of its two C2LR boards, what the experiment is, lattice)
#: The lattices, measured 2026-10-30 with the baseline router: the repetition code's chain lies
#: on one row of legs of a 5 x 2 lattice, so each of its two CNOT layers pairs under one diagonal
#: (20 swap-or-stay steps for five rounds); the surface code's patch on a 4 x 5, where every check
#: is beside its data and a layer needs two diagonals, the X and Z checks going opposite ways
#: (36 steps for three rounds).
BOARDS = [
    ("rep5_mem", 30, "Repetition code memory, distance 5",
     "five rounds of the distance-5 repetition code's syndrome extraction (four ancillas, reset and reused "
     "every round), then the data read out", (5, 2)),
    ("surface3_mem", 32, "Surface code memory, distance 3",
     "three rounds of the distance-3 rotated surface code's syndrome extraction (eight ancillas, reset and "
     "reused every round), then the data read out", (4, 5)),
]


def task_id(board: str, species: str) -> str:
    return f"c2lr_{board}_{SPECIES[species]}"


def physics(base: dict, species: str) -> dict:
    """The shuttle board's physics with what a C2LR device of `species` adds: `Machine.c2lr_grid`'s
    blocks, so its devices pass the physics lock as they are."""
    out = json.loads(json.dumps(base))
    out["primitives"] = {**out["primitives"], **c2lr_primitives(species)}
    out["species"] = {**out["species"], **c2lr_species(species)}
    return out


def describe(what: str, species: str) -> str:
    p = C2LR_PHYSICS[species]
    s = p["swap_or_stay"]
    extra = (f"; reorder-or-not {p['reorder_or_not']['us']:g} us" if "reorder_or_not" in p else "")
    return (f"The shuttle memory board's experiment, unchanged: {what}. Here it runs on a C2LR swap-or-stay "
            f"lattice of {species} crystals (arXiv:2403.00756): one crystal is one qubit, crystals move only by "
            f"swap-or-stay steps (one shared junction waveform, one direction bit per leg), and a two-qubit gate "
            f"runs on the two crystals of one leg, merged and split again. Judged by the C2LR rule set; ranked "
            f"by the logical error rate per round under the noise model built from the device's physics: the "
            f"shuttle board's, plus the paper's step ({s['us']:g} us; {s['quanta_swap']:g} quanta to a crystal "
            f"that moves, {s['quanta_stay']:g} to one that stays{extra}) and the platform's own merge and "
            f"split.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(RELEASES_DIR))
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for board, order, title, what, (la, lb) in BOARDS:
        base = find_release(f"{board}@1")
        for k, species in enumerate(SPECIES):
            task = task_id(board, species)
            if (out / f"{task}@1").exists():
                print(f"{task}@1 exists; left alone")
                continue
            m = base.manifest
            d = build_release(
                task, "1", base.circuit_text(), physics(base.physics(), species),
                title=f"{title}, on C2LR ({species})", description=describe(what, species), out_dir=out,
                metrics=m["metrics"], rank_by=m["rank_by"], limits=m.get("limits"), order=order + k,
                rank_table=m["physics"]["rank_table"], tables=m["physics"]["tables"],
                starter={"generator": "c2lr_grid", "name": f"c2lr{la}x{lb}",
                         "params": {"a": la, "b": lb, "species": species}},
                qec={"experiment": m["qec"]["experiment"], "detectors": base.detectors(),
                     "noise": m["qec"]["noise"], "ler": m["qec"]["ler"]},
                track="c2lr", rules="c2lr")
            rel = find_release(f"{task}@1", extra=out)
            # the experiment is the shuttle board's, file for file
            for key in ("circuit",):
                assert rel.manifest[key]["digest"] == m[key]["digest"], key
            assert rel.manifest["qec"]["detectors"]["digest"] == m["qec"]["detectors"]["digest"]
            print(f"{rel.id}: {rel.title} -> {d} ({rel.digest[:23]}...)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
