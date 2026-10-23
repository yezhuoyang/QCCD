"""No file this repository ships, and no bundle it uploads, names the disk it was built on.

The repository is public.  Until 2026-10-23 its board manifests named every artefact by an
absolute path into one user folder (781 of them in 17 files), twenty shipped programs carried
that folder as their provenance `root`, and a leaderboard entry served its submitter's
checkout at /submissions/<id>/files/program.tsir.json.  Records now name artefacts relative
to the repository (`qccd.repo_paths`), shipped programs blank `root`
(`provenance.blank_root`), and a bundle carries its programs' provenance thinned the way a
published page does (`bundle._publishable`).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from qccd.ir import provenance as prov
from qccd.repo_paths import REPO, from_repo, resolve, to_repo

#: a NAMED user folder on Windows (as written, or escaped in JSON) or on macOS, or a session
#: folder named after one -- what a build machine's path looks like inside a file.  A name, not
#: a variable: `/c/Users/$USERNAME/AppData/...` is how a portable script spells it.
MACHINE_ERE = (r"[A-Za-z]:(\\{1,2}|/)Users(\\{1,2}|/)[A-Za-z0-9_.-]"
               r"|/Users/[A-Za-z0-9_.-]+/(Documents|AppData|Desktop)|c--Users-[A-Za-z]")
MACHINE = re.compile(MACHINE_ERE.replace("(", "(?:"))


@pytest.mark.skipif(not (shutil.which("git") and (REPO / ".git").exists()), reason="not a git checkout")
def test_no_committed_file_names_a_user_folder():
    assert MACHINE.search('"arch": "C:\\\\Users\\\\someone\\\\QCCD\\\\arch\\\\x.json"')   # it can see one
    assert not MACHINE.search('export PATH="/c/Users/$USERNAME/AppData/Local/opam/bin:$PATH"')
    # `git grep -a`: several board files are attributed binary, and `-I` skipped exactly them.
    # The tests are left out: they hold sample paths to prove their own patterns can see one.
    r = subprocess.run(["git", "-C", str(REPO), "grep", "-a", "-l", "-E", MACHINE_ERE, "--", ".", ":!tests/"],
                       capture_output=True, text=True)
    assert r.returncode in (0, 1), r.stderr
    assert r.stdout.strip() == "", r.stdout


def test_a_record_is_written_relative_and_read_back_absolute():
    row = {"key": "a", "arch": str(REPO / "arch" / "grid9x9.arch.json"),
           "prefix": REPO / "SmallCode" / "steane" / "grid9x9_steane",
           "qasm": "C:\\elsewhere\\tmp\\round.qasm", "href": "/board/goal1/", "n": 3}
    out = to_repo([row])[0]
    assert out["arch"] == "arch/grid9x9.arch.json"
    assert out["prefix"] == "SmallCode/steane/grid9x9_steane"
    assert out["qasm"] == "round.qasm"                  # outside the checkout: the name only
    assert out["href"] == "/board/goal1/" and out["n"] == 3   # not a path key: untouched
    back = resolve([out])[0]
    assert Path(back["arch"]) == REPO / "arch" / "grid9x9.arch.json"
    assert from_repo(back["arch"]) == Path(back["arch"])        # an absolute one passes through
    assert to_repo({"arch": "C:\\Users\\x\\QCCD-wt\\w\\arch\\a.json"},
                   roots=["C:\\Users\\x\\QCCD-wt\\w"])["arch"] == "arch/a.json"


def test_a_bundle_carries_no_checkout_path(tmp_path):
    from qccd.workspace.bundle import write_bundle
    prog = {"name": "p", "instructions": [{"type": "init", "id": 0, "placement": {}, "meta": {"call": 0}}],
            "meta": {prov.PROV_KEY: {"version": 1, "level": "calls", "root": "C:\\Users\\someone\\QCCD",
                                     "sites": [{"file": "C:\\Users\\someone\\design.py", "line": 7,
                                                "func": "make"}],
                                     "calls": [{"op": "init", "site": 0, "args": {"a": 1}}]}}}
    b = write_bundle(tmp_path / "b", task_id="t@1", task_digest="sha256:x", device={"name": "d"},
                     program=prog, certified_program=prog, certificate={"version": 1})
    for name in ("program.tsir.json", "certified.tsir.json"):
        text = (tmp_path / "b" / name).read_text(encoding="utf-8")
        assert not MACHINE.search(text), name
        log = json.loads(text)["meta"][prov.PROV_KEY]
        assert "root" not in log and log["sites"] == [{"func": "make"}]
        assert log["calls"][0]["op"] == "init"            # which pass emitted it survives
        assert json.loads(text)["instructions"][0]["meta"]["call"] == 0
    assert b.docs["program"]["meta"][prov.PROV_KEY].get("root") is None
    assert prog["meta"][prov.PROV_KEY]["root"]            # the caller's document is not edited


def test_blanking_a_shipped_program_changes_only_its_root(tmp_path):
    p = tmp_path / "p.tsir.json"
    body = ('{"name": "x", "instructions": [],\r\n "meta": {"prov": {"version": 1, "level": "calls", '
            '"root": "C:\\\\Users\\\\someone\\\\QCCD", "sites": [], "calls": []}}}\r\n')
    p.write_bytes(body.encode("utf-8"))
    assert prov.blank_root_file(p) is True
    assert p.read_bytes().decode("utf-8") == body.replace('"C:\\\\Users\\\\someone\\\\QCCD"', '""')
    assert prov.blank_root_file(p) is False               # already blank: untouched
