"""The Design canvas draws ions the way the studio stage does, and is held to it.

`tests/test_viz_js.py` states the two properties of the studio stage -- no two ions ever
overlap, and no ion jumps at a frame boundary -- and explains why measuring the drawing
rather than the lattice is the whole point: two ions in one trap are invisible to a lattice
measurement.  The gadget Design canvas is a second stage for the same machine, and it had
no such test.  What it was doing, measured over `site/gadgets/demo` before this existed:

    41%     of sampled instants drew two ion discs through each other
    0.0     the worst separation, in lattice units: two ions at exactly the same point
    11,163  sideways flicks, the worst of them 22.7 lattice units, as `app.js::spread`'s
            three-decimal coincidence test switched on and off between adjacent frames

All three came from the same absence: `LeafSim.state` interpolated every mover along its
own path with no reference to any other ion, and `spread` then tried to repair the result
by comparing coordinates.  The canvas now places its whole stage through
`qccd/viz/js/transit.js`, which is what the studio stage runs.

`tests/gadget_stage.mjs` does the measuring, through `LeafSim.state` and `core.js::
ionRadius` -- the same two functions the canvas draws with, so this is not a second
opinion about what it does.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from qccd.gadget.build import build
from qccd.gadget.logicq import parse
from qccd.gadget.programs import showcase

node = shutil.which("node")
requires_node = pytest.mark.skipif(node is None, reason="node is not on PATH")
HARNESS = Path(__file__).parent / "gadget_stage.mjs"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("gadget_stage")
    build(parse(showcase(4), name="showcase4"), out_dir=out, rows=1, pairs=2,
          cache_dir=tmp_path_factory.getbasetemp() / "gadget_cache", log=None)
    return out


@pytest.fixture(scope="module")
def stage(built):
    run = subprocess.run([node, str(HARNESS), str(built), "6"],
                         capture_output=True, text=True, encoding="utf-8", timeout=900)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout.strip().splitlines()[-1])


@requires_node
def test_the_design_canvas_never_draws_one_ion_through_another(stage):
    assert stage["total"]["samples"] > 0, "the harness sampled nothing"
    w = stage["worst_overlap"]
    assert stage["total"]["overlaps"] == 0, (
        f"{stage['total']['pct_overlap']}% of instants draw overlapping ions; worst "
        f"{w and w['pair']} in {w and w['leaf']}.{w and w['op']} at step "
        f"{w and w['step']}, t={w and w['t']}")


@requires_node
def test_no_ion_jumps_between_one_instruction_and_the_next(stage):
    """The end of instruction *k* must be exactly where *k+1* starts.

    A seam here is a teleport: the ion is drawn in one place on the last frame of an
    instruction and somewhere else on the first frame of the next, with nothing in
    between.  `spread`'s stack re-index produced them at up to 22.7 lattice units.
    """
    s = stage["worst_seam"]
    assert stage["total"]["seams"] == 0, (
        f"{stage['total']['seams']} boundary jumps, worst {s and s['d']} lattice units "
        f"({s and s['ion']} in {s and s['leaf']}.{s and s['op']} between instructions "
        f"{s and s['between']})")


@requires_node
def test_the_measurement_actually_meets_the_case_it_is_about(stage):
    """A run in which no ion ever had to get past another proves nothing.

    `conflicts` counts the times a mover's route met an ion standing in it -- an exchange,
    an arrival into an occupied trap, or a route across one.  If this ever reads zero the
    two assertions above have stopped being about anything and the programme the fixture
    builds needs replacing, not the threshold.
    """
    assert stage["total"]["conflicts"] > 0, (
        "no ion in this build ever had to get past another, so the stage properties are "
        "untested by it")


@requires_node
def test_every_leaf_of_the_build_was_walked(stage):
    assert stage["leaves"], "no leaf data was loaded"
    for name, rec in stage["leaves"].items():
        assert rec["samples"] > 0, f"{name} contributed no samples"
        assert rec["overlaps"] == 0, f"{name}: {rec['overlaps']} overlapping instants"
        assert rec["seams"] == 0, f"{name}: {rec['seams']} boundary jumps"
