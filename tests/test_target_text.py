"""Hardware instructions in QCCDCert's syntax (qccd/ir/target_text.py and its JS mirror).

The studio's programme language, the design page's Hardware view and every listing write
instructions as QCCDCert writes its target code, extended for the studio.  These tests hold
the Python printer/parser and the browser's (qccd/viz/engine.js) to the same text, and the
text to the calls the toolchain runs.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from qccd.ir.target_text import call_text, fmt_angle, instruction_text, parse_call

ROOT = Path(__file__).resolve().parents[1]

CALLS = [
    ("init", [{"d0": "S0", "a0": "S1"}], {}),
    ("init", [{"d0": "S0"}, {"d0": 0.25}], {}),
    ("simd", ["shuttle", [["a0", ["S0", "J0", "S1"]]]], {}),
    ("simd", ["dock", [["a0", "S0", "A0", ["V0"]], ["d1", "S3", "A3", ["V3"]]]], {}),
    ("simd", ["shuttle", [["a0", ["S0", "S1"]], ["a1", ["S2", "S3"]]]], {"mode": "intra"}),
    ("simd", ["shuttle", [["d0", "S0", "S1", ["E0", "E1"]], ["d1", "S2", "S3", ["E2", "E3"]]]], {}),
    ("gate", ["H", [], ["S0"]], {}),
    ("shuttle", ["a0", ["S0", "J0", "S1", "J1", "S2"]], {}),
    ("shuttle", ["a0", ["S0", "A0"]], {"cls": "dock"}),
    ("rotate", [1], {}),
    ("rotate", [-13, "L0"], {}),
    ("rotate", [2, "L0", "rotate_cw"], {}),
    ("gate", ["CX", [["d0", "a0"]]], {}),
    ("gate", ["MS", [["d0", "a0"], ["d1", "a1"]], ["S0", "S1"]], {}),
    ("measure", [["a0", "a1"]], {}),
    ("reset", [["a0"]], {}),
    ("cool", [], {}),
    ("cool", [["d0", "a0"]], {}),
    ("barrier", [], {}),
    ("decode", [], {}),
    ("decode", [["a0", "a1"]], {}),
    ("fill", [], {}),
    ("fill", ["L0", "q"], {}),
    ("claim", [], {"total_cost": 12, "total_steps": 3}),
    ("reorder_or_not", [["d0", "d1"]], {}),
]


@pytest.mark.parametrize("call", CALLS, ids=[c[0] + str(i) for i, c in enumerate(CALLS)])
def test_every_call_prints_and_reads_back(call):
    method, args, kw = call
    text = call_text(method, args, kw)
    assert text.endswith(";")
    assert parse_call(text) == (method, args, kw)


def test_a_move_by_rails_reads_back_as_the_same_transport():
    text = call_text("move", ["a0", "S0", "S1", ["E0"]], {})
    assert text == "transport a0:S0->S1[shuttle] along (E0);"
    assert parse_call(text) == ("simd", ["shuttle", [["a0", "S0", "S1", ["E0"]]]], {})


def test_one_transport_one_class():
    with pytest.raises(ValueError):
        parse_call("transport a0:S0->S1[dock], a1:S2->S3[shuttle];")


def test_angles_are_printed_as_qccdcert_prints_them():
    assert [fmt_angle(x) for x in (math.pi / 2, -math.pi / 4, 3 * math.pi / 4, 0.0, 0.5)] == \
        ["pi/2", "-pi/4", "3*pi/4", "0", "0.5"]


def test_compiled_instructions_print_in_the_target_syntax():
    rot = {"type": "simd", "id": 1, "class": "rotate_cw", "template": {"kind": "loop_shift", "loop": "L0", "delta": 13}}
    assert instruction_text(rot) == "rotate L0 +13;"
    r = {"type": "gate", "id": 2, "gate": "R", "arity": 1, "params": [[math.pi / 2, math.pi / 2]],
         "ions": ["q0"], "sites": ["G"]}
    assert instruction_text(r) == "pulse R(pi/2,pi/2) q0 @G;"
    ms = {"type": "gate", "id": 3, "gate": "MS", "params": [[math.pi / 2]], "pairs": [["q0", "q1"]], "sites": ["G"]}
    assert instruction_text(ms) == "pulse MS(pi/2) q0,q1 @G;"
    br = {"type": "gate", "id": 4, "gate": "R", "arity": 1, "params": [[-math.pi / 2, 0.0], [-math.pi / 2, 0.0]],
          "ions": ["q0", "q1"], "sites": ["G", "H"]}
    assert instruction_text(br) == "broadcast R(-pi/2,0) q0@G, R(-pi/2,0) q1@H;"
    cx = {"type": "gate", "id": 5, "gate": "CX", "pairs": [["d0", "a0"]], "sites": ["S0"]}
    assert instruction_text(cx) == "gate CX d0,a0 @S0;"
    assert instruction_text({"type": "measure", "id": 6, "ions": ["a0"]}) == "readout a0;"
    assert instruction_text({"type": "reset", "id": 7, "ions": ["a0"]}) == "pump a0;"
    assert instruction_text({"type": "cool", "id": 8, "broadcast": True}) == "cool;"
    assert instruction_text({"type": "init", "id": 0, "placement": {"q0": "S", "q1": "G"}}) == "init q0@S, q1@G;"


def test_a_compiled_transport_names_the_sites_it_passes():
    from qccd.arch import Architecture
    arch = Architecture.from_json(json.loads((ROOT / "qccd" / "site" / "compiled" / "grid2x3.arch.json").read_text(encoding="utf-8")))
    sh = {"type": "simd", "id": 1, "class": "shuttle", "participants":
          [{"ion": "q1", "from": "T0_0v", "to": "T0_1h", "via": ["T0_0v.b", "T0_1h.a"]}]}
    assert instruction_text(sh, arch.device) == "transport q1:T0_0v->J0_1->T0_1h[shuttle];"
    assert instruction_text(sh) == "transport q1:T0_0v->T0_1h[shuttle] along (T0_0v.b,T0_1h.a);"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_browser_prints_and_reads_the_same_text():
    """engine.js's renderProgramStmt / parse agree with call_text / parse_call."""
    script = (
        "const E=require(process.argv[1]);const calls=JSON.parse(require('fs').readFileSync(0,'utf8'));"
        "const out=calls.map(([m,a,k])=>{const t=E.renderProgramStmt({method:m,args:a,kwargs:k});"
        "const p=E.parse(t);const s=p.prog[0]||{};return {text:t,errors:p.errors.length,"
        "back:[s.method,s.args,s.kwargs]};});process.stdout.write(JSON.stringify(out));")
    done = subprocess.run(["node", "-e", script, str(ROOT / "qccd" / "viz" / "engine.js")],
                          input=json.dumps(CALLS), capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    for (method, args, kw), got in zip(CALLS, json.loads(done.stdout)):
        assert got["text"] == call_text(method, args, kw), (method, got["text"])
        assert got["errors"] == 0, got
        assert got["back"] == [method, args, kw], (got["text"], got["back"])
