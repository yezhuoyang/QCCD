"""The two front-end verbs, and the guard that they are the only existing file touched.

`qccd phys` and `qccd gds` are the whole of this feature's footprint outside
`qccd/phys/`, `qccd/analysis/field.py` and the two lines that register it.  They are
additive subparsers, so the test that matters most is not that they work -- it is that
adding them left the fifteen verbs that were already there exactly as they were.

`qccd gds` reads its own output back before it claims success, through a parser that
shares no code with the writer.  A file that does not parse is caught at the CLI rather
than at the fab.
"""

from __future__ import annotations

import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.__main__ import build_parser, main  # noqa: E402

#: every verb the CLI had before this feature, plus the two it adds
BEFORE = {"devices", "show", "arch", "source", "run", "listing", "disasm", "verify",
          "demo", "regen", "reach", "analyses", "sweep", "studio", "open"}
ADDED = {"phys", "gds"}
#: Verbs other features added since: the website build and the course.
LATER = {"site", "tutorial"}


def _verbs() -> set[str]:
    parser = build_parser()
    sub = next(a for a in parser._actions if getattr(a, "choices", None)
               and hasattr(a, "add_parser"))
    return set(sub.choices)


def test_the_two_verbs_are_additive_and_nothing_else_moved():
    """The blast radius of this whole feature on the front end, as one assertion."""
    assert _verbs() == BEFORE | ADDED | LATER


def test_every_verb_still_dispatches_to_something():
    """A subparser with no entry in the dispatch table raises `KeyError` at run time."""
    import qccd.__main__ as m
    src = (ROOT / "qccd/__main__.py").read_text(encoding="utf-8")
    for verb in _verbs():
        assert f'"{verb}":' in src, f"{verb} has a parser but no dispatch entry"


def test_phys_prints_the_derived_metal_and_the_drc(capsys):
    assert main(["phys", "chain"]) == 0
    out = capsys.readouterr().out
    assert "chain72  [eth_junction_2201.12579]" in out
    assert "781 polygons from 3 cells placed 71 times" in out
    assert "die 16.075 x 0.350 mm" in out
    assert "nothing rounded" in out
    assert "min_width" in out and "rf_dc_clearance" in out
    assert "NEITHER NUMBER IS MEASURED" in out, "the disclosure must not read as a verdict"


def test_phys_reports_a_device_that_fails_its_design_rules(capsys):
    assert main(["phys", "ring144_24v", "--limit", "2"]) == 0
    out = capsys.readouterr().out
    assert "rf_dc_clearance    44" in out
    assert "overlap            44" in out, "and every one of them is metal really shared"
    assert "dc_pairs_per_site  98" in out
    assert "and 184 more" in out
    # the ring no longer crosses itself (its corner docks run outward since R21), so the
    # crossing disclosure is rightly absent; the technology's own disclosures remain
    assert "no overpass" not in out
    assert "waived technology minimums" in out, "the technology's disclosures must reach the report"


def test_phys_writes_an_svg_when_asked(tmp_path, capsys):
    target = tmp_path / "chain.svg"
    assert main(["phys", "chain", "--svg", str(target)]) == 0
    assert f"wrote {target}" in capsys.readouterr().out
    root = ET.fromstring(target.read_text(encoding="utf-8"))
    assert root.tag.endswith("svg")


def test_phys_writes_json_that_carries_both_halves(tmp_path, capsys):
    import json
    target = tmp_path / "chain.json"
    assert main(["phys", "chain", "--json", str(target)]) == 0
    doc = json.loads(target.read_text(encoding="utf-8"))
    assert doc["layout"]["n_polys"] == 781
    assert doc["drc"]["by_rule"]["min_width"] == 0 and doc["drc"]["clean"] is True
    assert doc["drc"]["disclosures"]


def test_gds_writes_a_file_and_reads_it_back_before_saying_so(tmp_path, capsys):
    target = tmp_path / "ring.gds"
    assert main(["gds", "ring144_24v", "-o", str(target)]) == 0
    out = capsys.readouterr().out
    # 1230: two more junction squares, one per corner dock now drawn beside its rail
    assert "1230 polygons" in out and "database unit 1e-09 m" in out
    raw = target.read_bytes()
    assert struct.unpack_from(">HBB", raw, 0)[1] == 0x00, "starts with HEADER"
    assert len(raw) == target.stat().st_size > 1000


def test_gds_names_the_library_when_asked(tmp_path):
    from qccd.phys.gds import read_gds
    target = tmp_path / "x.gds"
    assert main(["gds", "chain", "-o", str(target), "--name", "CHAIN"]) == 0
    lib = read_gds(target)
    assert lib.name == "CHAIN" and lib.structures == ("CHAIN",)


def test_an_unknown_technology_names_the_presets(capsys):
    with pytest.raises(FileNotFoundError, match="eth_junction_2201.12579"):
        main(["phys", "chain", "--tech", "nope"])


def test_both_verbs_default_to_the_published_preset():
    parser = build_parser()
    for verb in ("phys", "gds"):
        args = parser.parse_args([verb, "chain"])
        assert args.tech == "eth_junction_2201.12579"


def test_the_other_preset_is_reachable_by_name_and_reports_a_different_device(capsys):
    """`--tech surface_default` is the whole interface to the collaborator's defaults.

    The same document, a different technology, and a different verdict: `ring144_24v`
    collides in the published trap's geometry and does not in this one, which is the
    comparison the second preset exists to make possible.
    """
    assert main(["phys", "ring144_24v", "--tech", "surface_default", "--limit", "2"]) == 0
    out = capsys.readouterr().out
    assert "ring144_24v  [surface_default]" in out
    assert "isotropic" in out, "one lattice unit means one length in both directions"
    assert "rf_dc_clearance    0" in out and "overlap            0" in out
    assert "dc_pairs_per_site  46" in out, (
        "the 24 docks and the 22 junction sites, which no technology can fix")
    assert "control electrodes under a trapping site" in out
