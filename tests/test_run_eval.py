"""A run's evaluation on its page (run_eval.py), and what the agent is told (mcp_server._run_result).

The page counts the round up from the evaluator's own timeline (`perf.timeline`), so its running
totals must end exactly at the report; and the agent's run tool returns the numbers the page shows,
falling back to the evaluator's report -- saying so -- only when no page showed the run.
"""

from __future__ import annotations

from pathlib import Path

from qccd.api import Machine
from qccd.compile.programs import build as build_program
from qccd.cost.models import corrected_model
from qccd.verify.replay import replay
from qccd.workspace.perf import performance, timeline
from qccd.workspace.run_eval import eval_block

ROOT = Path(__file__).resolve().parents[1]


def _replayed():
    m = Machine.load(ROOT / "arch" / "grid9x9.arch.json")
    prog = build_program(m.arch, "walk", 4)
    res = replay(prog, m.arch, corrected_model(), check_rules=False, keep_cycles=True)
    return m.arch, prog, res


def test_the_timeline_ends_exactly_at_the_report():
    arch, prog, res = _replayed()
    rep, tl = performance(arch, prog, res), timeline(prog, res)
    assert len(tl["cat"]) == len(prog.instructions)                      # frame i is instruction i
    sums: dict = {}
    for c, us in zip(tl["cat"], tl["us"]):
        if c >= 0:
            sums[tl["categories"][c]] = sums.get(tl["categories"][c], 0.0) + us
    for b in rep["breakdown"]:
        assert abs(sums.get(b["category"], 0.0) / 1000 - b["ms"]) < 1e-3, (b, sums)
    assert abs(tl["t_us"][-1] / 1000 - rep["total"]["ms"]) < 1e-3
    assert tl["t_us"] == sorted(tl["t_us"])                               # the clock only moves forward
    assert sum(tl["gates2q"]) == rep["counts"]["two_qubit_gates"]


def test_the_page_carries_its_data_and_its_controls():
    arch, prog, res = _replayed()
    html = eval_block("job_x", timeline(prog, res), performance(arch, prog, res))
    for part in ('id="qrun-eval-data"', "qe-skip", "run:skip", "run:eval", "/evaluation", "#|&)result"):
        assert part in html, part
    assert "</script" not in html.split('id="qrun-eval-data"')[1].split("</script>")[0][:-1]


class _Backend:
    def __init__(self, views, evaluation):
        self.views, self.evaluation = views, evaluation

    def call(self, method, path, body=None, **kw):
        if path == "/api/views":
            return {"views": self.views}
        if path.endswith("/evaluation"):
            return {"evaluation": self.evaluation}
        raise AssertionError(path)


RUN = {"run": {"program": {"name": "ghz4"}, "design": {"draft": "main", "revision": 1}, "compiler": "compile",
               "view": "/runview/job_x", "note": "", "performance": {"total": {"ms": 1.5}}},
       "summary": "ghz4 on Main design: 1.5 ms"}


def test_the_agent_gets_the_numbers_the_page_shows():
    from qccd.workspace.mcp_server import _run_result
    shown = {"shown": "loaded", "total_ms": 1.5, "breakdown": [], "at": 1.0}
    out = _run_result(_Backend([{"connected": True, "closed": False}], shown), "job_x", RUN, wait_s=2)
    assert out["evaluation"]["total_ms"] == 1.5 and "at" not in out["evaluation"]
    assert "performance" not in out and "person's browser" in out["evaluation_source"]


def test_with_no_page_open_the_agent_is_told_the_numbers_are_the_evaluators():
    from qccd.workspace.mcp_server import _run_result
    out = _run_result(_Backend([], None), "job_x", RUN, wait_s=2)
    assert out["performance"]["total"]["ms"] == 1.5 and "evaluation" not in out
    assert "no page showed this run" in out["evaluation_source"] and "no Studio is open" in out["evaluation_source"]
