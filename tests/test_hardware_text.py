"""A machine specification in QCCDCert's hardware syntax (qccd/arch/hardware_text.py).

QCCDCert writes a machine as declarations (`hardware Primer; site S[1]; path p0:S->J->G
channel 0 waveform 0 duration 2;`).  The studio writes its machines the same way, extended
(`site S0[2] at (0.0, 0.0) zone trap;`, `rail E0:S0--J0;`, `zone trap capacity 2;`).  These
tests hold the listing, the parser and the browser's copy (engine.js) to one text, and the
text to the machine it declares.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from qccd.api import Machine
from qccd.arch.hardware_text import HardwareSyntaxError, parse, parse_statement, statement_text
from qccd.arch.listing import architecture_listing, round_trip_check

ROOT = Path(__file__).resolve().parents[1]
ARCHS = sorted((ROOT / "arch").glob("*.arch.json"))


def _listings():
    for f in ARCHS:
        m = Machine.load(f)
        for mode in ("full", "explicit"):
            yield f.stem, mode, m, architecture_listing(m.arch, mode=mode)


def test_every_shipped_machine_reads_back_as_itself():
    for stem, mode, m, L in _listings():
        calls = [l.call for l in L.lines if l.kind == "call"]
        assert parse(L.source()) == calls, (stem, mode)
        ok, diff = round_trip_check(m.arch, listing=L)
        assert ok, (stem, mode, diff)
        # every record is written as a declaration; none falls back to the call form
        assert all(statement_text(c) for c in calls), (stem, mode)


def test_the_listing_is_written_in_declarations_not_python():
    text = Machine.load(ROOT / "arch" / "ring144_24v.arch.json").source()
    assert text.startswith("hardware ring144_24v from ring width 72 verticals 24 zones [data, trap, load];")
    assert "zone trap capacity 2 gate true spam true cool true" in text
    assert "class rotate_cw type shift orbit L0 delta 1" in text
    assert "curve split [\n    {us: 80.0, quanta: 6.0, table: qccdsim_jones" in text
    assert "m." not in text and "Machine." not in text and "dict(" not in text
    explicit = Machine.load(ROOT / "arch" / "ring144_24v.arch.json").listing(mode="explicit").source()
    assert "\nsite S0 at (" in explicit and "\nrail " in explicit and "\nloop L0:S0," in explicit
    assert "\nhardware ring144_24v from geometry zones [data, trap, load];" in explicit


def test_machine_read_is_source_inverse():
    m = Machine.load(ROOT / "arch" / "h2_racetrack.arch.json")
    again = Machine.read(m.source())
    assert again.arch.to_json(expanded=True) == m.arch.to_json(expanded=True)


@pytest.mark.parametrize("text, call", [
    ("site S0[2] at (0.0, 1.5) zone trap;", {"method": "d.site", "args": ["S0", 0.0, 1.5], "kwargs": {"zone": "trap", "capacity": 2}}),
    ("site S1 at (1.0, 0.0);", {"method": "d.site", "args": ["S1", 1.0, 0.0], "kwargs": {}}),
    ("junction J0 at (2.0, 0.0) labels [hub];", {"method": "d.junction", "args": ["J0", 2.0, 0.0], "kwargs": {"labels": ["hub"]}}),
    ("rail E0:S0--J0 length 0.5;", {"method": "d.segment", "args": ["E0", "S0", "J0"], "kwargs": {"length": 0.5}}),
    ("loop L0:S0,S1,S2 closed true kind ring;", {"method": "d.loop", "args": ["L0", ["S0", "S1", "S2"]], "kwargs": {"closed": True, "kind": "ring"}}),
    ("move S3 to (2.0, -1.5);", {"method": "move_site", "args": ["S3", 2.0, -1.5], "kwargs": {}}),
    ("capacity [S0, S6] 4;", {"method": "set_site_capacity", "args": [["S0", "S6"], 4], "kwargs": {}}),
    ("capacity S0 8;", {"method": "set_site_capacity", "args": ["S0", 8], "kwargs": {}}),
    ("zone load capacity 4;", {"method": "set_zone", "args": ["load"], "kwargs": {"capacity": 4}}),
    ('describe "two traps" deck "p.12";', {"method": "describe", "args": ["two traps"], "kwargs": {"deck": "p.12"}}),
    ("control channels {grouping: broadcast, roles: {linear_h: 6}};", {"method": "set_control", "args": [], "kwargs": {"channels": {"grouping": "broadcast", "roles": {"linear_h": 6}}}}),
    ("curve split [];", {"method": "set_curve", "args": ["split", []], "kwargs": {}}),
    ("curve junction_cross degree 3 [\n    {us: 100.0, quanta: 3.0}\n];", {"method": "set_degree_curve", "args": ["junction_cross", 3, [{"us": 100.0, "quanta": 3.0}]], "kwargs": {}}),
    ("hardware lab from geometry zones [data, trap];", {"method": "blank_device", "args": [], "kwargs": {"name": "lab", "zones": ["data", "trap"]}}),
    ("hardware r from ring width 8 template ring144_24v;", {"method": "from_template", "args": ["ring"], "kwargs": {"width": 8, "name": "r", "template": "ring144_24v"}}),
    ("geometry explicit;", {"method": "DeviceBuilder", "args": ["explicit"], "kwargs": {}}),
    ('zone "odd zone" capacity 2;', {"method": "set_zone", "args": ["odd zone"], "kwargs": {"capacity": 2}}),
])
def test_declarations_and_records(text, call):
    assert parse_statement(text) == call
    assert statement_text(call) == text


def test_a_mistake_is_named():
    with pytest.raises(HardwareSyntaxError, match="expected 'at'"):
        parse_statement("site S0 (0.0, 0.0);")
    with pytest.raises(HardwareSyntaxError, match="whole number"):
        parse_statement("site S0[1.5] at (0.0, 0.0);")
    with pytest.raises(HardwareSyntaxError, match="given twice"):
        parse_statement("zone trap capacity 2 capacity 3;")
    with pytest.raises(HardwareSyntaxError, match="line 2"):
        parse("zone trap capacity 2;\nrail E0 S0--S1;\n")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_browser_prints_and_reads_the_same_text():
    """engine.js parses every shipped listing line to Python's record and prints it back
    byte for byte; JS-made records print as Python prints them."""
    docs = []
    for stem, mode, m, L in _listings():
        docs.append([[l.text, l.call] for l in L.lines if l.kind == "call"])
    script = (
        "const E=require(process.argv[1]);const docs=JSON.parse(require('fs').readFileSync(0,'utf8'));"
        "const out=docs.map(rows=>rows.map(([text,call])=>{const p=E.parse(text);const s=p.stmts[0]||{};"
        "return {errors:p.errors.map(e=>e.message),back:[s.method,s.args,s.kwargs],again:s.method?E.render(s):null,"
        "fromCall:E.hwStatementText(call)};}));process.stdout.write(JSON.stringify(out));")
    done = subprocess.run(["node", "-e", script, str(ROOT / "qccd" / "viz" / "engine.js")],
                          input=json.dumps(docs), capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert done.returncode == 0, done.stderr[-2000:]
    for rows, got in zip(docs, json.loads(done.stdout)):
        for (text, call), g in zip(rows, got):
            assert not g["errors"], (text, g["errors"])
            assert g["back"] == [call["method"], call["args"], call["kwargs"]], text
            assert g["again"] == text, (text, g["again"])
            if "." not in json.dumps(call):        # JSON drops 5.0 -> 5; only exact for float-free records
                assert g["fromCall"] == text, (text, g["fromCall"])
