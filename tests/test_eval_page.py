"""The Design page reports what the evaluation defines, and nothing else.

Every number a studio page shows -- the head's chips, the price line, the Backed table, the
hardware table, the Report's evaluation section -- is a metric of the texts the grader reads
(`qccd/evalset/library`).  A page is built here, `tests/eval_page.mjs` loads it in the shim
and asks its editor:

* on the page as it shipped, the browser's interpreter must give every metric, requirement
  and budget line the values the grader's interpreter shipped (`EDITOR.evaluationCheck`);
* after an edit, the browser's values are the evaluation's on the EDITED device, as Python
  computes them from the same text -- here a broadcast plane rewired direct, the WISE
  contrast, where the DAC count is the whole point;
* the chips and the Report link each metric to its definition on the Evaluation page.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tests" / "eval_page.mjs"
node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node is not installed")

DIRECT = [{"do": "mode", "mode": "edit"},
          {"do": "emit", "op": {"method": "set_control", "args": [],
                                "kwargs": {"channels": {"grouping": "direct"}}}, "label": "direct"}]


def _page(tmp_path, device: str, kind: str) -> Path:
    from qccd.arch import Architecture
    from qccd.compile.programs import build
    from qccd.cost import corrected_model
    from qccd.verify import verify
    from qccd.viz.render import render_html

    arch = Architecture.from_json(json.loads((ROOT / "arch" / f"{device}.arch.json").read_text(encoding="utf-8")))
    prog = build(arch, kind, 4) if kind == "walk" else build(arch, kind)
    model = corrected_model()
    res = verify(prog, arch, model, check_metrics=False).result
    return render_html(arch, prog, res, model, tmp_path / f"{device}.html")


def _drive(page: Path, script: list, tmp_path) -> dict:
    sp = tmp_path / "script.json"
    sp.write_text(json.dumps(script), encoding="utf-8")
    r = subprocess.run([node, str(RUNNER), str(page), str(sp)], capture_output=True, text=True,
                       encoding="utf-8", timeout=900, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert "fatal" not in out, out
    assert out["ready"], out.get("why")
    return out


def _formal(arch, names) -> dict:
    """The evaluation's values on a device with no programme: what the hardware metrics are."""
    from qccd.evalset import for_page
    from qccd.ir.tsir import TSIR
    ev = for_page("qccdsim_jones")
    run = ev.run(TSIR(name="device", arch_spec=arch.name, instructions=[]), arch)
    e = ev.evaluator(run)
    return {n: e.value(n) for n in names}, {r["id"]: r["verdict"] for r in ev.requirements_check(run)}


@pytest.mark.parametrize("device,kind", [("cyclone_base", "oddeven"), ("grid9x9", "walk")])
def test_the_page_evaluates_what_the_grader_evaluates(tmp_path, device, kind):
    out = _drive(_page(tmp_path, device, kind), DIRECT, tmp_path)
    check = out["check"]
    assert check is not None, "the page shipped no evaluation"
    assert check["ok"], check["diffs"]
    assert check["n"] >= 25 and check["lines"] >= 5, check

    shipped = out["shipped"]
    assert shipped["source"] == "shipped"
    assert {r for r, _ in shipped["requirements"]} == {"B1", "B2"}
    # the chips are the metrics, by value, and the Report names the texts and links them
    assert f"<b>{shipped['formal']['dacs']:,}</b>" in shipped["head"], shipped["head"]
    assert "Judged by the evaluation" in shipped["report"]
    assert "evaluation/#m-cost" in shipped["report"] and "evaluation/#m-dacs" in shipped["report"]


def test_an_edit_is_evaluated_by_the_text_on_the_edited_device(tmp_path):
    from qccd.api import Machine

    out = _drive(_page(tmp_path, "cyclone_base", "oddeven"), DIRECT, tmp_path)
    after = out["steps"][-1]
    assert "error" not in after, after
    assert after["source"] == "browser", "an edited page must not show the shipped values"

    m = Machine.load(ROOT / "arch" / "cyclone_base.arch.json")
    m.set_control(channels={"grouping": "direct"})
    want, verdicts = _formal(m.arch, ["dacs", "electrodes", "switches", "places"])
    assert after["formal"]["dacs"] == want["dacs"]
    assert after["hardware"]["dacs"] == want["dacs"], "the hardware table must read the metric"
    assert dict(after["requirements"]) == verdicts
    assert after["formal"]["dacs"] > 10 * out["shipped"]["formal"]["dacs"], (
        "broadcast against direct is the contrast the DAC metric exists for")
    # the programme still fits, so the execution metrics are the browser's too
    assert after["formal"]["cost"] == out["shipped"]["formal"]["cost"]
