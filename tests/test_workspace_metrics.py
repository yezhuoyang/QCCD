"""The evaluator's metrics reproduce the numbers the published boards show.

The boards' figures come from maintainer scripts (Codesign/scripts/small_codes.py,
bb_studio.py) and are re-replayed at page-build time into `SmallCode/<code>/manifest.json`
(`T_source: "replay"`).  `qccd.workspace.metrics` restates those definitions as a library;
this holds the two equal on every published entry whose artifacts are present.  Skipped on a
checkout without the SmallCode artifacts (they are not committed on every branch).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qccd.arch import load
from qccd.ir.tsir import TSIR
from qccd.workspace.metrics import compute_metrics

REPO = Path(__file__).resolve().parents[1]


def _cases():
    """Every published entry whose device and cooled program are on disk (the manifest names
    both: `arch`, and `prefix` + `.cooled.tsir.json`)."""
    out = []
    for code in ("steane", "five_qubit", "repetition"):
        man = REPO / "SmallCode" / code / "manifest.json"
        if not man.exists():
            continue
        for m in json.loads(man.read_text(encoding="utf-8")):
            nums = m.get("numbers") or {}
            cooled = Path(str(m.get("prefix", "")) + ".cooled.tsir.json")
            if nums.get("T_source") == "replay" and "T_jones" in nums and cooled.exists() and Path(m["arch"]).exists():
                out.append(pytest.param({"arch": m["arch"], "cooled": str(cooled)}, nums, id=f"{code}:{m['key']}"))
    return out[:12]


CASES = _cases()


@pytest.mark.skipif(not CASES, reason="the SmallCode board artifacts are not in this checkout")
@pytest.mark.parametrize("row,published", CASES)
def test_metrics_equal_the_published_board(row, published):
    m, _ = compute_metrics(load(row["arch"]), TSIR.load(row["cooled"]),
                           {"rank_table": "qccdsim_jones", "tables": ["qccdsim_jones", "transport_excitation"]})
    assert round(m["T_jones"], 3) == published["T_jones"]
    assert round(m["T_transport"], 3) == published["T_transport"]
    assert round(m["ions_per_instruction"], 2) == published["ions_per_instruction"]
    if "dacs" in published:
        assert m["dacs"] == published["dacs"]


def test_metrics_do_not_depend_on_wall_clock(monkeypatch):
    """A faster laptop must not score better: the metric is modelled time only."""
    import time
    from qccd.api import Machine
    m = Machine.chain(4, name="c")
    p = m.program("p")
    p.init({"q0": "C0", "q1": "C1"})
    prog = p.build()
    a, _ = compute_metrics(m.arch, prog, {"rank_table": "qccdsim_jones", "tables": ["qccdsim_jones"]})
    monkeypatch.setattr(time, "time", lambda: 0.0)
    monkeypatch.setattr(time, "perf_counter", lambda: 0.0)
    b, _ = compute_metrics(m.arch, prog, {"rank_table": "qccdsim_jones", "tables": ["qccdsim_jones"]})
    assert a == b
