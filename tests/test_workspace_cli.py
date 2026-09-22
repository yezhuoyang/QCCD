"""The `qccd` workspace commands, as a user runs them (subprocesses, a real service)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
REPO = Path(__file__).resolve().parents[1]


def qccd(*args, cwd, env):
    return subprocess.run([sys.executable, "-m", "qccd.workspace", *args], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=300)


def test_init_studio_status_install_stop(tmp_path):
    env = dict(os.environ, QCCD_RUNTIME_DIR=str(tmp_path / "rt"),
               PYTHONPATH=str(REPO) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    r = qccd("releases", cwd=REPO, env=env)
    assert r.returncode == 0 and "ghz4@1" in r.stdout
    r = qccd("init", str(tmp_path / "d"), "--task", "ghz4@1", cwd=REPO, env=env)
    assert r.returncode == 0, r.stderr
    d = tmp_path / "d"
    try:
        r = qccd("studio", "--no-open", cwd=d, env=env)
        assert r.returncode == 0, r.stderr
        url = r.stdout.split("Studio: ", 1)[1].split()[0]
        assert url.startswith("http://127.0.0.1:") and "#pair=" in url       # the code rides in the fragment
        r = qccd("status", "--json", cwd=d, env=env)
        st = json.loads(r.stdout)
        assert st["revision"] == 0 and st["service"]["port"] == int(url.split(":")[2].split("/")[0])
        r = qccd("agent", "install", "--client", "claude", "--channel", cwd=d, env=env)
        assert r.returncode == 0 and (d / ".mcp.json").exists() and (d / ".claude/skills/qccd/SKILL.md").exists()
        r = qccd("agent", "install", "--client", "codex", cwd=d, env=env)
        assert "trust" in r.stdout and (d / ".agents/skills/qccd/SKILL.md").exists()
        r = qccd("agent", "uninstall", "--client", "codex", cwd=d, env=env)
        assert not (d / ".agents/skills/qccd").exists() and not (d / "AGENTS.md").exists()
        r = qccd("publish", "--submission", "sub_x", cwd=d, env=env)
        assert r.returncode != 0                                             # no such submission / no TTY
    finally:
        qccd("stop", cwd=d, env=env)
    # outside a workspace, `studio` keeps its old meaning (writes the static page)
    r = subprocess.run([sys.executable, "-m", "qccd.workspace", "studio", "-o", str(tmp_path / "s.html")],
                       cwd=tmp_path, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0 and (tmp_path / "s.html").exists()
