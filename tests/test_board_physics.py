"""A design entered on a board is scored with the board's physics (`scored_device`, `board_device`).

The fixed blocks (primitives, heating, species, budget) are not design variables.  Every new
design starts from the template's, which every board before `qccd-physics@2` shares; the Goal 1
board (surface5xz_mem@1) has physics of its own, so a design with the template's takes the
board's.  Since 2026-10-29 (the user's decision) a number the design changed where the board has
one is not used, and is listed; a number the board does not have at all -- a junction of a degree
it never prices -- is the design's own, used and listed, so the leaderboard can tell the entry
apart; the budget is the board's, whole.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.workspace.tasks import FIXED_BLOCKS, board_device, find_release, physics_mismatch, scored_device  # noqa: E402

GRID = json.loads((ROOT / "arch" / "grid9x9.arch.json").read_text(encoding="utf-8"))


def test_a_board_with_the_templates_physics_gets_the_design_unchanged():
    for board in ("surface3_mem@1", "rep5_mem@1", "ghz4@1"):
        rel = find_release(board)
        assert board_device(GRID, rel) == GRID
        assert physics_mismatch(board_device(GRID, rel), rel) == []


def test_a_design_with_the_template_physics_takes_the_goal_1_boards():
    rel = find_release("surface5xz_mem@1")
    assert physics_mismatch(GRID, rel)              # as drawn, it is not an entry
    doc = board_device(GRID, rel)
    assert physics_mismatch(doc, rel) == []          # as entered, it is
    assert doc["geometry"] == GRID["geometry"] and doc["name"] == GRID["name"]
    assert {b: doc[b] for b in FIXED_BLOCKS} == {b: rel.physics()[b] for b in FIXED_BLOCKS}


def test_an_edited_number_the_board_has_is_the_boards_and_is_listed():
    rel = find_release("surface5xz_mem@1")
    mine = copy.deepcopy(GRID)
    mine["primitives"]["ms_gate"]["fidelity_at_n0"] = 0.9999
    mine["budget"]["max_dacs"] = 4000
    out, note = scored_device(mine, rel)
    assert physics_mismatch(out, rel) == []
    assert note == {"added": [], "ignored": ["primitives.ms_gate.fidelity_at_n0", "budget.max_dacs"]}
    assert out == board_device(mine, rel)


def test_a_number_the_board_lacks_is_the_designs_own_and_is_listed():
    """The two-layer trap of 2026-10-29 brings a degree-5 junction's crossing cost, which no board
    prices: it is used (or the design could not be scored at all) and listed.  Its declared
    `max_junction_degree` is a budget figure, and the budget is the board's."""
    rel = find_release("surface5xz_mem@1")
    mine = copy.deepcopy(GRID)
    five = [{"us": 120.0, "quanta": 3.0, "table": "cyclone", "label": "same as degree 4"}]
    mine["primitives"]["junction_cross"]["curve_by_degree"]["5"] = five
    mine["budget"]["max_junction_degree"] = 6
    out, note = scored_device(mine, rel)
    assert note == {"added": ["primitives.junction_cross.curve_by_degree.5"], "ignored": ["budget.max_junction_degree"]}
    assert out["primitives"]["junction_cross"]["curve_by_degree"]["5"] == five
    assert out["budget"] == rel.physics()["budget"]
    # what the template has and the board does without (Cyclone's degree-2 bend) is nobody's choice: dropped
    assert "2" not in out["primitives"]["junction_cross"]["curve_by_degree"]


def test_a_c2lr_lattice_keeps_what_makes_it_one():
    """On a shuttle board a C2LR lattice keeps its steps' primitives, the merge/split of a gate in
    one leg, and its species pair (docs/c2lr.md): its own, listed.  On a C2LR board they are the
    board's, so nothing is."""
    from qccd.api import Machine
    from qccd.arch.c2lr import c2lr_primitives
    doc = Machine.c2lr_grid(5, 2).arch.to_json()
    out, note = scored_device(doc, find_release("rep5_mem@1"))
    assert {"primitives.merge", "primitives.split", "primitives.swap_or_stay", "species.pair"} <= set(note["added"])
    assert note["ignored"] == []
    pair = doc["species"]["pair"]
    assert out["primitives"]["merge"] == c2lr_primitives(pair)["merge"]
    assert scored_device(doc, find_release("c2lr_rep5_mem_basr@1"))[1] == {"added": [], "ignored": []}


def test_the_goal_1_release_pins_noise_at_two_and_its_table():
    rel = find_release("surface5xz_mem@1")
    m = rel.manifest
    assert m["qec"]["noise"] == "qccd-noise@2" and m["rank_by"] == "ler_per_round"
    assert m["physics"]["rank_table"] == "quantinuum" and m["physics"]["tables"] == ["quantinuum"]
    phys = json.loads((ROOT / "qccd" / "workspace" / "physics" / "qccd-physics@2.json").read_text(encoding="utf-8"))
    assert {b: rel.physics()[b] for b in FIXED_BLOCKS} == {b: phys[b] for b in FIXED_BLOCKS}
    assert m["qec"]["experiment"] == "surface5xz_r5"


def test_every_sampled_metric_is_compared_as_a_sample():
    """stim's seeded sampling repeats on one machine, not across machines (Windows 2.82e-5, the
    Linux grader 3.21e-5 for one Goal 1 entry): every rate the ler stage reports is a sample."""
    from qccd.workspace.evaluator import STOCHASTIC_METRICS, metrics_agree
    assert {"ler", "ler_per_round", "ler_per_round_per_logical"} <= set(STOCHASTIC_METRICS)
    here = {k: {"value": v} for k, v in (("ler_per_round", 2.82e-5), ("ler_per_round_per_logical", 2.82e-5),
                                          ("ler", 1.97e-4), ("T_quantinuum", 1644.3))}
    there = {k: {"value": v} for k, v in (("ler_per_round", 3.21e-5), ("ler_per_round_per_logical", 3.21e-5),
                                           ("ler", 2.25e-4), ("T_quantinuum", 1644.3))}
    assert metrics_agree(here, there, {"float_rel_tol": 1e-9, "float_abs_tol": 1e-12}) == []
