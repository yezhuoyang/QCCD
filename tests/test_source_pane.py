"""The source-circuit pane: does the page say which QASM statement is executing?

A compiled program has two answers to "what is happening now" -- the hardware instruction,
and the circuit statement it is discharging. The second is the one that makes the page a
debugger rather than a demo, and it is a *join*: instruction id -> circuit op -> source
line. A join is exactly the kind of thing that keeps rendering plausibly after it has
stopped being right, so it is measured here rather than eyeballed.

The payload is built by hand rather than by running the OCaml compiler: what is under test
is the page, and a test that needed `dune build` first would be skipped on every machine
that has not got OCaml, which is where a rendering regression would then live.

`qccd.ir.source_map` -- the other half, which builds the payload and refuses when the
compiler's stamps disagree with its own certificate -- is covered by `test_source_map.py`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd import Machine  # noqa: E402
from qccd.cost import corrected_model  # noqa: E402
from qccd.ir.source_map import build as build_source  # noqa: E402
from qccd.ir.tsir import TSIR, Instruction  # noqa: E402
from qccd.verify import verify  # noqa: E402
from qccd.viz import render_html  # noqa: E402

HARNESS = Path(__file__).parent / "srcpane.mjs"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE is None, reason="node is not on PATH; the emitted page cannot be executed")

QASM = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[3];
h q[0];
cx q[0],q[1];
cx q[1],q[2];
"""


def _page(tmp_path: Path):
    """A three-ion program whose instructions carry `meta.op`, and its page."""
    m = Machine.grid(3, 3, name="srcpane")
    arch = m.arch

    sites = [n.id for n in arch.device.sites()][:3]
    ions = ["q0", "q1", "q2"]
    place = dict(zip(ions, sites))

    def gate(i, pair, ops, site):
        return Instruction(type="gate", id=i, gate="MS", mode="intra",
                           pairs=(pair,), sites=(site,), params=((1.5707963,),),
                           meta={"op": ops})

    instrs = [
        Instruction(type="init", id=0, placement=place,
                    quanta={k: 0.0 for k in ions}),
        # op 0 -- `h q[0]`, one beam on one ion
        Instruction(type="gate", id=1, gate="R", arity=1, mode="intra",
                    ions=("q0",), sites=(sites[0],), params=((1.5707963, 0.0),),
                    meta={"op": [0]}),
        # shuttling towards op 1
        Instruction(type="simd", id=2, cls="shuttle", mode="inter",
                    participants=(), meta={"op": [1]}),
        gate(3, ("q0", "q1"), [1], sites[0]),
        # op 2, at the same trap
        gate(4, ("q0", "q1"), [2], sites[0]),
        # transport AFTER the gate it serves -- putting the ions back, not fetching them
        Instruction(type="simd", id=6, cls="shuttle", mode="inter",
                    participants=(), meta={"op": [2]}),
        # the compiler's own bookkeeping: no circuit statement at all
        Instruction(type="cool", id=5, ions=tuple(ions), broadcast=True,
                    meta={"kind": "state_prep"}),
    ]
    prog = TSIR(name="srcpane", arch_spec=arch.name, instructions=instrs, id_seq=7)

    qasm = tmp_path / "srcpane.qasm"
    qasm.write_text(QASM, encoding="utf-8")
    cert = {
        "circuit_ops": [
            {"i": 0, "name": "h", "qubits": [0], "params": [], "line": 4},
            {"i": 1, "name": "cx", "qubits": [0, 1], "params": [], "line": 5},
            {"i": 2, "name": "cx", "qubits": [1, 2], "params": [], "line": 6},
        ],
        "gates": [{"dag": 0, "instr": 1}, {"dag": 1, "instr": 3}, {"dag": 2, "instr": 4}],
    }

    source = build_source(prog, cert, qasm)

    model = corrected_model()
    res = verify(prog, arch, model, check_metrics=False).result
    out = tmp_path / "srcpane.html"
    render_html(arch, prog, res, model, out, source=source)
    return out, source


def _run(page: Path) -> dict:
    got = subprocess.run([NODE, str(HARNESS), str(page)],
                         capture_output=True, text=True, timeout=180)
    assert got.returncode == 0, got.stdout[-2000:] + got.stderr[-2000:]
    return json.loads(got.stdout)


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    page, source = _page(tmp_path_factory.mktemp("srcpane"))
    return _run(page), source


def test_pane_is_present_and_revealed(report):
    r, _ = report
    assert r["present"], "the page did not receive a source payload"
    # the tab ships hidden (`tab off`) and is revealed only when there is a circuit
    assert "off" not in r["tab"], "the Circuit tab stayed hidden with a circuit loaded"
    assert r["ops"] == 3


def test_every_stamped_instruction_is_classified(report):
    """Three states, not two: fetching the ions and putting them back differ."""
    r, source = report
    assert r["realises"] == 3 and r["toward"] == 1 and r["after"] == 1
    assert set(source["realises"]) == {"1", "3", "4"}
    assert set(source["toward"]) == {"2"}      # before the gate it serves
    assert set(source["after"]) == {"6"}       # after it


def test_cursor_tracks_the_statement_being_executed(report):
    """The property the pane exists for, on every frame."""
    r, source = report
    line_of = {o["i"]: o["line"] for o in source["ops"]}
    for p in r["probes"]:
        iid = r["frameIds"][p["frame"]]
        now = source["realises"].get(str(iid))
        soon = source["toward"].get(str(iid))
        past = source["after"].get(str(iid))
        if now:
            want = min(line_of[i] for i in now)
            assert p["cursor"] == want - 1, (
                f"frame {p['frame']} (instruction {iid}) executes op {now}, "
                f"written on line {want}, but the cursor sat at row {p['cursor']}")
            assert "executing" in p["now"]
        elif soon:
            want = min(line_of[i] for i in soon)
            assert p["cursor"] == want - 1
            assert "shuttling towards" in p["now"]
        elif past:
            # the gate has already happened: saying "towards" here would read as a gate
            # still to come, which is worse than saying nothing
            assert p["cursor"] == min(line_of[i] for i in past) - 1
            assert "clearing after" in p["now"]
        else:
            assert p["cursor"] == -1, "a bookkeeping instruction lit a source line"
            assert "no circuit statement" in p["now"]


def test_marks_distinguish_executing_from_travelling(report):
    r, source = report
    line_of = {o["i"]: o["line"] for o in source["ops"]}
    for p in r["probes"]:
        iid = r["frameIds"][p["frame"]]
        now = source["realises"].get(str(iid), [])
        soon = (source["toward"].get(str(iid), [])
                + source["after"].get(str(iid), []))
        for oi in now:
            assert p["marks"].get(str(line_of[oi])) == 2
        for oi in soon:
            # a statement transport is merely serving is shaded, not lit
            assert p["marks"].get(str(line_of[oi])) == 1
        for row in p["rows"]:
            want = p["marks"].get(row["n"])
            assert (" qh" in row["cls"]) == (want == 2), row
            assert (" qw" in row["cls"]) == (want == 1), row


def test_the_hardware_pane_names_the_statement_too(report):
    """Without this you have to change tabs to answer "which gate is this?"."""
    r, source = report
    for p in r["probes"]:
        iid = r["frameIds"][p["frame"]]
        if source["realises"].get(str(iid)):
            assert "circuit &rarr;" in p["inline"]
        elif source["toward"].get(str(iid)):
            assert "towards" in p["inline"]
        elif source["after"].get(str(iid)):
            assert "after" in p["inline"]


def test_clicking_a_statement_lands_on_the_instruction(report):
    """The inverse direction, which is how a compiler bug is actually chased."""
    r, source = report
    line_of = {o["i"]: o["line"] for o in source["ops"]}
    first = {}
    for i, iid in enumerate(r["frameIds"]):
        for oi in source["realises"].get(str(iid), []):
            first.setdefault(line_of[oi], i)
    assert first, "no statement was realised by any instruction"
    for j in r["jumps"]:
        if j["line"] in first:
            assert j["frame"] == first[j["line"]], (
                f"clicking line {j['line']} landed on frame {j['frame']}, "
                f"not {first[j['line']]}")


def test_a_page_without_a_circuit_still_works(tmp_path):
    """Every hand-written program renders through the same code path."""
    m = Machine.grid(3, 3, name="nosrc")
    p = m.program("walk")
    p.init({"d0": next(n.id for n in m.arch.device.sites())})
    model = corrected_model()
    res = verify(p.build(), m.arch, model, check_metrics=False).result
    out = tmp_path / "nosrc.html"
    render_html(m.arch, p.build(), res, model, out)
    r = _run(out)
    assert r["present"] is False
    assert "off" in r["tab"], "the Circuit tab was shown with no circuit to show"
