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


def test_an_agent_follows_a_long_grade_in_a_few_waiting_calls():
    """A live agent polled a 7-minute BB grade every 3 s, one model turn each, then tried to
    sleep with tools it does not have.  qccd_get_job(wait_s) waits while the job runs."""
    from qccd.workspace.mcp_server import dispatch

    class Fake:
        def __init__(self, states):
            self.states, self.calls = list(states), 0

        def call(self, method, path, body=None, **kw):
            self.calls += 1
            return {"id": "j", "status": self.states.pop(0) if len(self.states) > 1 else self.states[0]}

    be = Fake(["running", "running", "succeeded"])
    assert dispatch(be, "qccd_get_job", {"job_id": "j", "wait_s": 30})["status"] == "succeeded" and be.calls == 3
    be = Fake(["running", "succeeded"])
    assert dispatch(be, "qccd_get_job", {"job_id": "j"})["status"] == "running" and be.calls == 1   # no wait asked


def test_a_workspace_folder_takes_any_name_or_none_and_the_cli_says_which_words_are_yours(tmp_path):
    """The person asked (2026-09-25) not to be forced into a folder like `my-design` or a task like
    `ghz4@1`, and to be told plainly which names are theirs and which commands are fixed."""
    env = dict(os.environ, QCCD_RUNTIME_DIR=str(tmp_path / "rt"), QCCD_CODEX="none", QCCD_CLAUDE="none", QCCD_WEB="0",
               PYTHONPATH=str(REPO) + os.pathsep + os.environ.get("PYTHONPATH", ""), PYTHONIOENCODING="utf-8")
    run = lambda *a, cwd: subprocess.run([sys.executable, "-m", "qccd.workspace", *a], capture_output=True, text=True,
                                         encoding="utf-8", env=env, cwd=str(cwd), timeout=300)
    here = tmp_path / "My QCCD designs"                      # spaces, capitals: whatever the person likes
    here.mkdir()
    r = run("init", cwd=here)                                # no name: this folder
    assert r.returncode == 0 and (here / "qccd.lock.json").is_file(), r.stdout + r.stderr
    assert "yours to name, anything you like" in r.stdout and "type as shown" in r.stdout
    assert "@1" not in r.stdout and "--task" not in r.stdout
    assert not any(ln.strip().startswith("cd ") for ln in r.stdout.splitlines())         # already here
    r = run("init", "Triangle ideas", cwd=tmp_path)          # a new folder, any name
    assert r.returncode == 0 and (tmp_path / "Triangle ideas" / "qccd.lock.json").is_file()
    assert 'cd "Triangle ideas"' in r.stdout
    r = run("init", cwd=here)                                # twice is refused, not overwritten
    assert r.returncode != 0 and "already holds a workspace" in r.stderr
    elsewhere = tmp_path / "not one"
    elsewhere.mkdir()
    for cmd in (["status"], ["studio", "--no-open"], ["stop"]):              # `boards` needs no workspace
        r = run(*cmd, cwd=elsewhere)                         # the wrong folder: said so, with the way out
        assert r.returncode == 2 and "not a QCCD workspace" in r.stderr and 'qccd init "My QCCD designs"' in r.stderr, \
            (cmd, r.stdout, r.stderr)
    assert list(elsewhere.iterdir()) == []                   # and nothing was written there


def test_add_docks_computes_where_the_docks_go_from_a_count(tmp_path):
    """The conveyor's docks from a count alone: a live agent spent 57 s of one model call working
    out 24 dock coordinates (2026-09-24).  On a 10 x 6 ring: evenly spread, one unit INSIDE the loop,
    never at a corner, never beside another dock, each joined to one loop site; outside works;
    too many is refused with how many fit."""
    ws = Workspace.init(tmp_path / "ws")
    try:
        ws.apply_change_set({"expected_revision": 0, "request_id": "ring", "mode": "apply",
                             "operations": [{"type": "construct", "generator": "ring",
                                             "params": {"width": 10, "height": 6}}]}, ME)
        dev0 = ws.replayed("main").arch.device
        loop = next(iter(dev0.loops.values()))
        on = list(loop.nodes)
        pos = lambda d, n: tuple(float(x) for x in d.nodes[n].pos)
        xs = [pos(dev0, n)[0] for n in on]
        ys = [pos(dev0, n)[1] for n in on]
        corners = {n for n in on if pos(dev0, n)[0] in (min(xs), max(xs)) and pos(dev0, n)[1] in (min(ys), max(ys))}
        r = ws.apply_change_set({"expected_revision": 1, "request_id": "d", "mode": "apply",
                                 "operations": [{"type": "add_docks", "count": 6}]}, ME)
        assert r["status"] == "committed" and not r["diagnostics"].get("violations"), r["diagnostics"]
        dev = ws.replayed("main").arch.device
        docks = [n for n in dev.nodes if n not in set(on)]
        assert len(docks) == 6
        hosts = []
        for d in docks:
            (sid,) = dev.incidence[d]                          # one rail each ...
            a, b = dev.segments[sid].ends
            host = a if b == d else b
            hosts.append(host)
            assert host in on and host not in corners           # ... to a loop site that is not a corner
            x, y = pos(dev, d)
            assert min(xs) < x < max(xs) and min(ys) < y < max(ys)            # inside
            hx, hy = pos(dev, host)
            assert abs(((x - hx) ** 2 + (y - hy) ** 2) ** 0.5 - 1.0) < 1e-6   # one unit off
        idx = sorted(on.index(h) for h in hosts)
        assert all((b - a) % len(on) > 1 for a, b in zip(idx, idx[1:] + idx[:1]))   # never neighbours
        out = ws.apply_change_set({"expected_revision": 2, "request_id": "o", "mode": "preview",
                                   "operations": [{"type": "add_docks", "count": 4, "side": "outside"}]}, ME)
        assert out["status"] == "previewed" and not out["diagnostics"].get("violations")
        with pytest.raises(WorkspaceError, match="only .* of 99 docks fit"):
            ws.apply_change_set({"expected_revision": 2, "request_id": "x", "mode": "preview",
                                 "operations": [{"type": "add_docks", "count": 99}]}, ME)
    finally:
        ws.close()


def test_a_change_set_names_its_design_like_every_other_door(tmp_path):
    """A live agent passed branch="Two triangles" and was refused ("branch must be 'main' or
    'cand/<name>'"), costing it two model calls (2026-09-26).  The name resolves, as elsewhere."""
    ws = Workspace.init(tmp_path / "ws")
    try:
        d = ws.create_candidate(ME, title="Two triangles")
        r = ws.apply_change_set({"branch": "two  TRIANGLES", "expected_revision": 0, "request_id": "n", "mode": "apply",
                                 "operations": [{"type": "add_site", "id": "Q1", "pos": [7, 7], "zone": "trap"}]}, ME)
        assert r["status"] == "committed" and r["branch"] == d["name"] and ws.branch(d["name"])["head"] == 1
        assert ws.branch("main")["head"] == 0                               # main untouched
        with pytest.raises(WorkspaceError, match="no design"):
            ws.apply_change_set({"branch": "Nothing like it", "expected_revision": 0, "request_id": "m", "mode": "apply",
                                 "operations": [{"type": "add_site", "id": "Q2", "pos": [8, 8], "zone": "trap"}]}, ME)
    finally:
        ws.close()
