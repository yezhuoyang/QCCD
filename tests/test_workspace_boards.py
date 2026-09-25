"""A workspace is general: designs named by the person, submitted to any board named by its title.

The person asked for this (2026-09-24): no task codes to see or type, one workspace for every
leaderboard on the website, designs called whatever they like.  Covered: a workspace made
without a board; the boards found by what people call them; designs by title (ambiguity refused,
rename); one call that compiles a board's circuit onto a design, adopts, freezes and grades it;
the same design to a second board (compiled again); a program for one board refused on another;
an old pinned workspace and an old database still opening; the CLI and the agent's tools.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from qccd.workspace.app import Workspace, WorkspaceError
from qccd.workspace.evaluator import Toolchain
from qccd.workspace.tasks import ReleaseError, find_board, list_boards

REPO = Path(__file__).resolve().parents[1]
TC = Toolchain.discover()
needs_toolchain = pytest.mark.skipif(TC.qccdc is None or TC.qcheck is None,
                                     reason="the OCaml compiler and the Lean checker are not built")
ME = {"kind": "human", "id": "cli"}
AGENT = {"kind": "agent", "id": "agent:t"}


def _wait(ws, jid, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = ws.job(jid)
        if j["status"] not in ("queued", "running"):
            return j
        time.sleep(0.3)
    raise AssertionError(f"job {jid} did not finish")


def test_the_boards_are_the_websites_leaderboards_found_by_their_names():
    titles = [b.title for b in list_boards()]
    assert titles[:5] == ["BB [[144,12,12]]", "Repetition code, distance 9", "Five-qubit code [[5,1,3]]",
                          "Steane code [[7,1,3]]", "Surface code, distance 3 (17 qubits)"]
    assert find_board("bb").title == "BB [[144,12,12]]" and find_board("Surface code").manifest["task"] == "surface17"
    assert find_board("steane").title == "Steane code [[7,1,3]]" and find_board("BB [[144,12,12]]").id == "bb144@1"
    with pytest.raises(ReleaseError, match="could be"):
        find_board("code")                                              # four boards say "code"
    with pytest.raises(ReleaseError, match="the boards are"):
        find_board("toric")
    for b in list_boards():                                             # every board ships its circuit, pinned
        b.verify_files()
        assert (b.manifest.get("starter") or {}).get("generator")


def test_a_workspace_needs_no_board_and_designs_have_the_names_people_give_them(tmp_path):
    ws = Workspace.init(tmp_path / "ws")
    lock = json.loads((tmp_path / "ws" / "qccd.lock.json").read_text(encoding="utf-8"))
    assert lock["version"] == 2 and "task" not in lock
    ctx = ws.context(AGENT)
    assert [b["title"] for b in ctx["boards"]][0] == "BB [[144,12,12]]" and "task" not in ctx
    assert ctx["design_title"] == "Main design" and ctx["designs"][0]["name"] == "Main design"
    d = ws.create_candidate(ME, title="Big triangle + small triangle")
    assert d["title"] == "Big triangle + small triangle" and d["name"].startswith("cand/d")
    assert ws.resolve_draft("big   triangle + SMALL triangle") == d["name"]        # spacing and case forgiven
    ws.create_candidate(ME, title="Big triangle + small triangle")
    with pytest.raises(WorkspaceError, match="2 designs are called"):
        ws.resolve_draft("Big triangle + small triangle")
    ws.rename_design(ME, "main", "My ring")
    assert ws.resolve_draft("my ring") == "main" and ws.design_title(ws.branch("main")) == "My ring"
    with pytest.raises(WorkspaceError, match="no design"):
        ws.resolve_draft("nothing like it")
    ws.close()
    assert Workspace(tmp_path / "ws").release.id == "ghz4@1"          # reopens; its default board is internal


def test_an_old_pinned_workspace_and_an_old_database_still_open(tmp_path):
    old = Workspace.init(tmp_path / "p", "bb144@1")                  # the pinned form still works
    assert json.loads((tmp_path / "p" / "qccd.lock.json").read_text())["task"]["id"] == "bb144@1"
    assert old.release.title == "BB [[144,12,12]]"
    old.close()
    db = tmp_path / "p" / ".qccd" / "workspace.db"                  # roll the database back to schema 1
    c = sqlite3.connect(db)
    c.execute("UPDATE meta SET value='1' WHERE key='store_schema'")
    c.commit()
    c.close()
    again = Workspace(tmp_path / "p")
    assert again.store.get_meta("store_schema") == "2" and again.design_title(again.branch("main")) == "Main design"
    again.close()


@needs_toolchain
def test_one_call_submits_a_design_to_a_board_and_another_board_compiles_again(tmp_path):
    ws = Workspace.init(tmp_path / "ws")
    d = ws.create_candidate(ME, title="Little ring")
    ws.apply_change_set({"branch": d["name"], "expected_revision": 0, "request_id": "ring", "mode": "apply",
                         "operations": [{"type": "construct", "generator": "ring",
                                         "params": {"width": 8, "height": 2, "verticals": 8}}]}, ME)
    out = ws.submit_design(AGENT, design="little ring", board="repetition code")
    assert out["design"] == "Little ring" and out["board"] == "Repetition code, distance 9"
    j = _wait(ws, out["job_id"])
    assert j["status"] == "succeeded" and j["result"]["compiled"], j
    g = _wait(ws, j["result"]["grading_job"])
    assert g["status"] == "succeeded" and g["result"]["eligible"], g["result"]
    s = ws.submissions()[0]
    assert (s["design"], s["board"], s["status"]) == ("Little ring", "Repetition code, distance 9", "eligible")
    fp = ws.head(d["name"]).state.final_program
    assert fp["board"] == "rep9@1" and fp["circuit_digest"] == find_board("rep9").manifest["circuit"]["digest"]
    lb = ws.leaderboard("repetition")
    assert lb["board"] == "Repetition code, distance 9" and [r["design"] for r in lb["rows"]] == ["Little ring"]
    assert ws.leaderboard("steane")["rows"] == []                       # boards are never mixed
    # the same design to another board: its program is the repetition code's, so it compiles again
    j2 = _wait(ws, ws.submit_design(AGENT, design="Little ring", board="Steane")["job_id"])
    assert j2["status"] == "succeeded" and j2["result"]["compiled"]
    assert _wait(ws, j2["result"]["grading_job"])["result"]["eligible"]
    head = ws.branch(d["name"])["head"]
    with pytest.raises(WorkspaceError, match="compiled for another board"):
        ws.freeze_snapshot(d["name"], head, ME, board="repetition")    # a Steane program is not graded as rep9
    # submitting again, unchanged: nothing to compile
    j3 = _wait(ws, ws.submit_design(AGENT, design="Little ring", board="Steane")["job_id"])
    assert j3["status"] == "succeeded" and not j3["result"]["compiled"]
    ws.close()


@needs_toolchain
def test_the_cli_submits_by_board_title(tmp_path):
    env = dict(os.environ, QCCD_RUNTIME_DIR=str(tmp_path / "rt"), QCCD_CODEX="none", QCCD_CLAUDE="none", QCCD_WEB="0",
               PYTHONPATH=str(REPO) + os.pathsep + os.environ.get("PYTHONPATH", ""), PYTHONIOENCODING="utf-8")
    run = lambda *a, cwd: subprocess.run([sys.executable, "-m", "qccd.workspace", *a], capture_output=True, text=True,
                                         encoding="utf-8", env=env, cwd=str(cwd), timeout=900)
    r = run("init", str(tmp_path / "d"), cwd=tmp_path)
    assert r.returncode == 0 and "BB [[144,12,12]]" in r.stdout and "@1" not in r.stdout, r.stdout + r.stderr
    d = tmp_path / "d"
    try:
        r = run("boards", cwd=d)
        assert "Five-qubit code [[5,1,3]]" in r.stdout and "@" not in r.stdout
        r = run("submit", "--local", "--board", "five-qubit", "--wait", cwd=d)     # 9 qubits: the 4-site chain is too small
        assert r.returncode == 1 and "chain4 has 4 usable traps (8 ion slots) but the circuit needs 9" in r.stderr
        r = run("submit", "--local", "--board", "GHZ", "--wait", cwd=d)            # the starter fits the starter board
        assert r.returncode == 0 and "submitting Main design to GHZ state on four qubits" in r.stdout, r.stdout + r.stderr
        assert "eligible" in r.stdout
        r = run("leaderboard", "--board", "ghz", cwd=d)
        assert "GHZ state on four qubits" in r.stdout and "Main design" in r.stdout
        r = run("status", cwd=d)
        assert r.returncode == 0 and "task" not in r.stdout
    finally:
        run("stop", cwd=d)
