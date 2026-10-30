"""The C2LR boards as leaderboards: the baseline router's reference entries, ranked by LER.

    c2lr_rep5_mem_basr       repetition code, distance 5, on C2LR lattices of Ba-Sr crystals
    c2lr_rep5_mem_ybba       the same, Yb-Ba
    c2lr_surface3_mem_basr   rotated surface code, distance 3, Ba-Sr
    c2lr_surface3_mem_ybba   the same, Yb-Ba

Each board is a release of its own track (`tools/make_c2lr_releases.py`; docs/c2lr.md section 7):
a memory board's experiment on C2LR swap-or-stay lattices, which only swap-or-stay steps move.
The reference compiler of these boards is the baseline C2LR router (`qccd.compile.c2lr_router`),
not qccdc, which emits plain shuttles a C2LR device does not offer.  Every lattice below is
compiled by it, cooled, replayed against the rules and graded exactly as `memory_boards.py`
grades a shuttle design (`memory_boards.grade_point`: the release's noise model, budget and
seed; R10 by check_cert.py and the proved Lean checker), and the pages are the memory boards'
(`memory_boards.pages`): one studio page per lattice with its Noise panel, and the index.

    python Codesign/scripts/c2lr_boards.py                    # every board
    python Codesign/scripts/c2lr_boards.py --board c2lr_rep5_mem_basr --pages-only

Set QCCD_QCHECK to the published Lean checker when the tree has none built: R10 is then decided
by it, as the grader decides it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "Codesign" / "scripts", ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import memory_boards as MB  # noqa: E402
from make_c2lr_releases import BOARDS as RELEASES, SPECIES, task_id  # noqa: E402
from qccd.repo_paths import resolve, to_repo  # noqa: E402

#: the lattices each experiment is entered on: the smallest square that holds it, the one the
#: router does best on (each release's suggested start), and larger ones up to the DAC budget
#: (32 + 2 per leg under the paper's census, against the board's 128: 48 legs)
LATTICES = {"rep5_mem": [(3, 3), (5, 2), (4, 4), (5, 5)],
            "surface3_mem": [(4, 4), (4, 5), (5, 5)]}
SHORT = {"rep5_mem": "rep-5 memory on C2LR", "surface3_mem": "surface-3 memory on C2LR"}


def boards() -> dict:
    out = {}
    for base, order, *_ in RELEASES:
        for k, species in enumerate(SPECIES):
            out[task_id(base, species)] = {"order": order + k, "short": f"{SHORT[base]} ({species})",
                                           "base": base, "species": species}
    return out


BOARDS = boards()


def devices(board: str) -> list[dict]:
    from qccd.api import Machine
    b = BOARDS[board]
    out = []
    for a, bb in LATTICES[b["base"]]:
        m = Machine.c2lr_grid(a, bb, species=b["species"])
        legs = 2 * a * bb - a - bb
        out.append({"key": f"c2lr{a}x{bb}", "family": "C2LR lattice",
                    "title": f"c2lr{a}x{bb} — a {a} x {bb} swap-or-stay lattice, {legs} legs",
                    "claim": f"{legs} legs on the wires of a {a} x {bb} junction lattice, {b['species']} crystals; "
                             f"compiled by the baseline C2LR router",
                    "doc": m.arch.to_json()})
    return out


def compile_router(qasm_text, arch_p, work, *, timeout, table):
    """The C2LR boards' reference compile, in `compile_with_qccdc`'s place and shape."""
    from qccd.compile.c2lr_router import compile_with_router
    return compile_with_router(qasm_text, arch_p, work, table=table)


def transport_numbers(row: dict) -> None:
    """A swap-or-stay step names no participants: its movers are the device's, counted as the
    grader counts them (`qccd.workspace.metrics.broadcast_use`)."""
    from qccd.arch import load
    from qccd.ir.tsir import TSIR
    from qccd.workspace.metrics import broadcast_use
    if row.get("status") != "ok":
        return
    bc = broadcast_use(load(row["arch"]), TSIR.load(row["cooled"]))
    n = row["numbers"]
    n.update(ions_per_instruction=round(bc["ions_per_instruction"], 2),
             transport_instructions=bc["transport_instructions"])


def main(argv=None) -> int:
    from qccd.qec import MemoryExperiment
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--board", default=None)
    ap.add_argument("--pages-only", action="store_true", help="rebuild pages from rows.json")
    a = ap.parse_args(argv)
    MB.BOARDS.update({k: {"order": v["order"], "short": v["short"]} for k, v in BOARDS.items()})
    for board in BOARDS:
        if a.board and board != a.board:
            continue
        release = MB.board_release(board)
        exp = MemoryExperiment.from_spec(release.detectors(), release.circuit_text())
        out = MB.OUT / board
        rj = out / "rows.json"
        out.mkdir(parents=True, exist_ok=True)
        if a.pages_only:
            rows = resolve(json.loads(rj.read_text(encoding="utf-8")))
        else:
            shutil.rmtree(out / MB.WORK_NEW, ignore_errors=True)
            print(f"== {board}: {release.title}", flush=True)
            rows = []
            for dev in devices(board):
                r = MB.grade_point(out, release, exp, dev, compile=compile_router)
                transport_numbers(r)
                rows.append(r)
                if r["status"] == "ok":
                    n = r["numbers"]
                    print(f"   {dev['key']:10s} LER/round {n['ler_per_round']:.2e}  {n.get(MB.time_key(release), 0):8.2f} ms  "
                          f"rules {'ok' if not r['rules_failed_replay'] else 'FAIL ' + str(r['rules_failed_replay'])}  "
                          f"R10 {n.get('r10')}", flush=True)
                else:
                    print(f"   {dev['key']:10s} refused: {r.get('reason', '')[:110]}", flush=True)
            if not any(r["status"] == "ok" for r in rows):
                print(f"c2lr_boards: no lattice of {board} compiled; nothing written", file=sys.stderr)
                return 1
            rows = MB._keep_work(out, rows, None)
            rj.write_text(json.dumps(to_repo(rows), indent=1, default=str), encoding="utf-8")
        MB.write_task(board, release, exp)
        MB.pages(board, release, exp, rows, out, compiled_by="the baseline C2LR router")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
