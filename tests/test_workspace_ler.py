"""The logical error rate as a workspace capability and as a board's grade (docs/PLAN-boards.md, Phase 2).

A memory board is built in a temporary release directory (the same builder as
tools/make_releases.py), a workspace starts from its suggested device, and the design is
submitted: the grade gains an `ler` stage, the ranked metric is `ler_per_round`, and a program
that is not the experiment fails that stage.  The `ler` job measures the same number on any
design.  Uses the real compiler (qccdc) and stim/pymatching; skipped without them.
"""

from __future__ import annotations

import time

import pytest

from qccd.workspace.app import Workspace
from qccd.workspace.evaluator import Toolchain, metrics_agree
from qccd.workspace.tasks import build_release, find_release

TC = Toolchain.discover()
needs = pytest.mark.skipif(TC.qccdc is None or TC.qcheck is None, reason="the compiler and the Lean checker are not built")
HUMAN = {"kind": "human", "id": "cli"}
AGENT = {"kind": "agent", "id": "agent:t"}


def _wait(ws, jid, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = ws.job(jid)
        if j["status"] in ("succeeded", "failed", "cancelled", "timeout", "internal_error"):
            return j
        time.sleep(0.3)
    raise AssertionError(f"job {jid} did not finish")


@pytest.fixture(scope="module")
def memory_ws(tmp_path_factory):
    if TC.qccdc is None or TC.qcheck is None:
        pytest.skip("toolchain not built")
    pytest.importorskip("stim")
    pytest.importorskip("pymatching")
    from qccd.qec import get_experiment
    rel_dir = tmp_path_factory.mktemp("rel")
    base = find_release("ghz4@1")
    exp = get_experiment("rep3")
    build_release("rep3_mem", "1", exp.qasm, base.physics(), title="Repetition code memory, distance 3",
                  description="test memory board", out_dir=rel_dir,
                  metrics=base.manifest["metrics"] + [
                      {"name": "ler_per_round", "unit": "per round", "better": "low", "about": "t"},
                      {"name": "ler", "unit": "per experiment", "better": "low", "about": "t"}],
                  rank_by="ler_per_round",
                  starter={"generator": "ring", "name": "ring16", "params": {"width": 8, "height": 2, "verticals": 8}},
                  qec={"experiment": exp.name, "detectors": exp.spec(), "noise": "qccd-noise@1",
                       "ler": {"decoder": "auto", "max_shots": 20000, "max_errors": 50, "seed": 7}})
    ws = Workspace.init(tmp_path_factory.mktemp("w") / "ws", "rep3_mem@1", release_dirs=rel_dir)
    yield ws
    ws.close()


@needs
def test_a_memory_board_ranks_by_the_logical_error_rate(memory_ws):
    ws = memory_ws
    t = ws.submit_design(HUMAN, board="rep3_mem@1", request_id="m1")
    j = _wait(ws, t["job_id"])
    assert j["status"] == "succeeded", j
    g = _wait(ws, j["result"]["grading_job"])
    sub = ws.submission(j["result"]["submission_id"])
    rep = sub["report"]
    stages = {s["id"]: s for s in rep["stages"]}
    assert [s["id"] for s in rep["stages"]][-2:] == ["ler", "metrics"]
    assert stages["ler"]["status"] == "passed", stages["ler"]
    assert stages["ler"]["required"] is True
    assert rep["metrics"]["ler_per_round"]["ranked"] and rep["rank_by"] == "ler_per_round"
    assert 0 <= rep["metrics"]["ler_per_round"]["value"] < rep["metrics"]["ler"]["value"] + 1e-12 or \
        rep["metrics"]["ler"]["value"] == 0
    assert rep["eligibility"]["eligible"], rep["eligibility"]
    ler = stages["ler"]["detail"]["ler"]
    assert ler["lo"] <= ler["ler"] <= ler["hi"] and ler["shots"] > 0
    lb = ws.leaderboard("rep3_mem@1")
    assert lb["rank_by"] == "ler_per_round" and lb["better"] == "low" and lb["rows"]


def test_two_graders_agree_on_a_stochastic_metric_within_a_factor():
    pol = {"float_rel_tol": 1e-9, "float_abs_tol": 1e-12}
    m = lambda v: {"ler": {"value": v}, "T_jones": {"value": 1.0}}
    assert metrics_agree(m(1e-3), m(1.4e-3), pol) == []
    assert metrics_agree(m(1e-3), m(5e-3), pol) == ["ler"]
    assert metrics_agree(m(0.0), m(0.0), pol) == []


@needs
def test_the_ler_job_measures_any_design(memory_ws):
    ws = memory_ws
    j = _wait(ws, ws.start_job(AGENT, "ler", {"experiment": "rep3", "max_shots": 5000})["job_id"])
    assert j["status"] == "succeeded", j
    res = j["result"]
    rep = res["ler_report"]
    assert rep["check"]["ok"] and rep["ler"]["shots"] > 0
    assert rep["noise"]["id"] == "qccd-noise@1" and res["dominant_channels"]
    assert "LER" in res["summary"]
    # a memory board by its title works too; a board without detectors is refused with the reason
    j2 = _wait(ws, ws.start_job(AGENT, "ler", {"experiment": "Repetition code memory, distance 3",
                                               "max_shots": 5000})["job_id"])
    assert j2["status"] == "succeeded", j2
    from qccd.workspace.core import WorkspaceError
    with pytest.raises(WorkspaceError):
        ws.start_job(AGENT, "ler", {"max_shots": 5000})
