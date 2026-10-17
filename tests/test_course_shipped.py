"""Part D of the course on a clone: the compiled `micro.qasm` artifacts ship with the repository.

`Compiler/build/` is a build directory and not in the repository, and Part D read its compiled
programmes only from there.  On a clone the D2 table was empty and `qccd tutorial` printed
"companion page skipped: .../micro_grid9x9.cooled.tsir.json is missing" (a reader's report,
2026-10-15).  The test in `test_tutorial.py` skips when that companion is missing, which is how
the gap stayed invisible on the one machine that has a build directory.  Here the build
directory is taken away, and both must still come out.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.viz import course  # noqa: E402


def test_every_shipped_architecture_has_its_artifacts_in_the_repository():
    for stem in course.ARCHES:
        for name in (f"micro_{stem}.tsir.json", f"micro_{stem}.qcert.json"):
            assert (course.SHIPPED / name).exists(), name
        if stem != "stationary_chain":                    # nothing on it needs cooling
            assert (course.SHIPPED / f"micro_{stem}.cooled.tsir.json").exists(), stem


#: an absolute path on a build machine: a drive letter, or a home or user directory
MACHINE_PATH = re.compile(r'[A-Za-z]:\\\\|/(?:Users|home)/')


def test_the_shipped_artifacts_carry_no_path_from_the_machine_that_built_them():
    assert MACHINE_PATH.search(r'"root": "C:\\Users\\someone\\QCCD"')    # the pattern can see one
    for p in course.SHIPPED.glob("*.json"):
        text = p.read_text(encoding="utf-8")
        assert not MACHINE_PATH.search(text), p.name
        assert json.loads(text).get("meta", {}).get("prov", {}).get("root", "") == "", p.name


def test_without_a_build_directory_the_table_and_the_companion_still_come_out(tmp_path, monkeypatch):
    from qccd.__main__ import cmd_tutorial
    from qccd.cost.models import corrected_model

    monkeypatch.setattr(course, "MATRIX", tmp_path / "no-build-directory")
    assert course.matrix_file("micro_grid9x9.cooled.tsir.json").parent == course.SHIPPED

    table = course.measured(corrected_model())
    assert set(table["micro"]) == set(course.ARCHES)
    assert all("raw" in row for row in table["micro"].values())

    out = tmp_path / "tutorial"
    cmd_tutorial(argparse.Namespace(out=str(out), model="corrected", table="qccdsim_jones",
                                    max_frames=20000))
    assert (out / "studio.html").exists()
    assert (out / "micro_grid9x9.html").exists(), "the companion page D1 runs on was skipped"
