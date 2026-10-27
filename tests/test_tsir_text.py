"""TSIR's text form is the same program as its JSON: every leaf program round-trips exactly."""
from __future__ import annotations

from pathlib import Path

import pytest

from qccd.ir.tsir import TSIR, Instruction, Participant
from qccd.ir.tsir_text import parse_instruction, parse_program, program_text, instruction_text

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "out" / "gadgets" / "cache"


def test_every_leaf_program_round_trips_through_text():
    from qccd.gadget.floorplan import FLOORPLANS_DIR, load_file
    from qccd.gadget.library import LeafLibrary
    leaves = LeafLibrary(CACHE, log=None)
    lib, _ = load_file(FLOORPLANS_DIR / "fullstack_wide.floor", leaves)
    n = 0
    for name in lib.leaf_masters():
        for op, prog in (leaves.data[name].get("programs") or {}).items():
            p = TSIR.from_json(prog)
            text = program_text(p)
            assert parse_program(text).to_json() == p.to_json(), (name, op)
            n += len(p.instructions)
    assert n > 10000


def test_the_lines_read_as_written():
    ins = parse_instruction("65 gate CX p0:e6 @A45 ; op=ppm round=0 factor=X src=rm15.circ:41")
    assert ins.gate == "CX" and ins.pairs == (("p0", "e6"),) and ins.sites == ("A45",)
    assert ins.mode == "intra" and ins.meta == {"op": "ppm", "round": 0, "factor": "X", "src": "rm15.circ:41"}
    rot = parse_instruction("1 simd rotate_cw ~L0 +30 ; door=in_a")
    assert rot.template == {"kind": "loop_shift", "loop": "L0", "delta": 30} and rot.mode == "inter"
    mv = parse_instruction('2 simd port_in d0:X_in_a->P_in_a[E_in_a] ; checks=["P0","P1"] label="two words"')
    assert mv.participants == (Participant("d0", "X_in_a", "P_in_a", ("E_in_a",)),)
    assert mv.meta == {"checks": ["P0", "P1"], "label": "two words"}
    one = parse_instruction("63 gate H p0 @A45")
    assert one.arity == 1 and one.ions == ("p0",)
    # a field the line does not imply is written, and read back
    odd = Instruction(type="gate", id=7, gate="CX", ions=("a", "b"), mode=None, t0=1.5)
    assert parse_instruction(instruction_text(odd)) == odd
    assert parse_instruction("5 cool") .broadcast and parse_instruction("6 cool a b").ions == ("a", "b")
