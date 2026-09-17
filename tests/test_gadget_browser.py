"""The gadget design tool in a real browser (docs/GADGETS.md §10).

`tests/gadget_browser.mjs` opens a build from `file://` in headless Chrome, walks the
hierarchy from the processor down to a memory's ion-level replay, and drives the
composition editor: group a row with its factory into a new gadget, try a connection G1
must refuse, undo and redo, export.  The exported design then goes back through
`build(..., design=...)` -- the round trip GADGETS.md §13 P4 promises.  Skipped without
node or Chrome.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from qccd.gadget.build import build
from qccd.gadget.logicq import parse
from qccd.gadget.programs import showcase

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "tests" / "gadget_browser.mjs"
CHROME = os.environ.get("CHROME") or next((p for p in (
    "C:/Program Files/Google/Chrome/Application/chrome.exe", "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser", "/usr/bin/chromium") if Path(p).exists()), None)
node = shutil.which("node")

pytestmark = [pytest.mark.skipif(node is None, reason="node is not on PATH"),
              pytest.mark.skipif(CHROME is None, reason="no Chrome; set CHROME")]


@pytest.fixture(scope="module")
def walked(tmp_path_factory):
    out = tmp_path_factory.mktemp("gadget_browser")
    build(parse(showcase(4), name="showcase4"), out_dir=out, rows=1, pairs=2,
          cache_dir=tmp_path_factory.getbasetemp() / "gadget_cache", log=None)
    run = subprocess.run([node, str(RUNNER), str(out)], capture_output=True, text=True,
                         encoding="utf-8", timeout=240)
    assert run.returncode == 0, run.stderr
    return out, json.loads(run.stdout.strip().splitlines()[-1])


def test_the_page_loads_without_errors(walked):
    _, seen = walked
    assert seen["ready"]
    assert seen["errors"] == []


def test_every_level_of_the_hierarchy_draws(walked):
    _, seen = walked
    by_path = {lv["path"]: lv for lv in seen["levels"]}
    assert "15 leaf gadgets" in by_path[""]["what"] or "leaf gadgets" in by_path[""]["what"]
    assert by_path["r0.p0"]["crumbs"].replace(" ", "").endswith("r0›p0")
    mem = by_path["r0.p0.t0.mem"]
    assert mem["heading"].startswith("mem")
    assert "memory leaf" in mem["what"]
    assert mem["step"] == "" or "instruction" in mem["step"]
    for lv in seen["levels"]:
        assert lv["ms"] < 250, lv


def test_the_editor_groups_refuses_and_undoes(walked):
    _, seen = walked
    ed = seen["editor"]
    assert ed["grouped"]["problems"] == []
    assert ed["grouped"]["top"] == ["row_with_factory", "sp0"]
    assert any(p.endswith("=r0.west") for p in ed["grouped"]["ports"])
    assert ed["refused"]["unchanged"] and "not connected" in ed["refused"]["message"]
    assert ed["undone"] == ["fac0", "r0", "sp0"]


def test_an_edited_design_synthesizes_and_passes_the_hierarchy_checks(walked, tmp_path):
    out, seen = walked
    design = json.loads(seen["editor"]["json"])
    assert "row_with_factory" in design["masters"]
    path = tmp_path / "edited.gadget.json"
    path.write_text(json.dumps(design), encoding="utf-8")
    res = build(parse(showcase(4), name="showcase4"), out_dir=tmp_path / "rebuilt",
                design=path, cache_dir=tmp_path.parent / "gadget_cache_edit", log=None)
    assert res["checks"]["failed"] == []
    original = json.loads((out / "gir.json").read_text(encoding="utf-8"))
    rebuilt = json.loads((tmp_path / "rebuilt" / "gir.json").read_text(encoding="utf-8"))
    # regrouping changes the hierarchy, not the machine: the same schedule comes out
    assert rebuilt["makespan_us"] == original["makespan_us"]
    assert len(rebuilt["events"]) == len(original["events"])


# ------------------------------------------------------------------ a verified algorithm page

VERIFIED_RUNNER = ROOT / "tests" / "gadget_verified_browser.mjs"


@pytest.fixture(scope="module")
def verified(tmp_path_factory):
    from qccd.gadget.library import LeafLibrary
    from qccd.gadget.verified import build_algorithm
    out = tmp_path_factory.mktemp("verified_browser")
    leaves = LeafLibrary(tmp_path_factory.getbasetemp() / "place_cache", log=None)
    res = build_algorithm(ROOT / "examples" / "gadgets" / "algorithms" / "bell_surgery.alg",
                          out_dir=out, leaves=leaves, log=None)
    run = subprocess.run([node, str(VERIFIED_RUNNER), str(out)], capture_output=True, text=True,
                         encoding="utf-8", timeout=240)
    assert run.returncode == 0, run.stderr
    return res, json.loads(run.stdout.strip().splitlines()[-1])


def test_a_verified_algorithm_page_draws_every_category_its_own_way(verified):
    _, seen = verified
    assert seen["ready"] and seen["errors"] == []
    town = seen["town"]
    assert town["distinct"] == town["categories"] == 13          # one silhouette each
    assert "Lattice Surgery" in town["legend"] and "Logical Preparation" in town["legend"]
    # the classical half is on the map too, and the bus is in the legend
    assert {"Decoder", "Classical Memory", "Classical Data Bus"} <= set(town["legend"])
    assert town["ms"] < 250


def test_the_verification_panel_reports_the_sign_off(verified):
    res, seen = verified
    assert res["signoff"]["passed"]
    assert "the schedule computes the algorithm" in seen["signoff"]
    assert "Z̄Z̄[q0,q1]" in seen["signoff"]


def test_drilling_into_a_place_shows_its_hardware_and_logic(verified):
    _, seen = verified
    place = seen["place"]
    assert place["path"] == "bridge" and place["op"] == "zz"
    assert "instruction" in place["step"] and "Lattice Surgery" in place["what"]
    assert "36/36 flows" in place["inspector"] and "d ≥ 3" in place["inspector"]
    assert any("Lattice Surgery" in h for h in seen["library"])
