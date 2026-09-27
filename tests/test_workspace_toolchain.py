"""The prebuilt compiler (`qccd toolchain install`): the manifest, the download's checks, and
what the workspace then uses.

The downloads here come from a local HTTP server, so nothing needs the network; the binary
it serves is the one built in this checkout (skipped when there is none)."""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from qccd.workspace import evaluator, toolchain
from qccd.workspace.toolchain import ToolchainError

REPO = Path(__file__).resolve().parents[1]
BUILT = next((p for p in (REPO / "Compiler/ocaml/_build/default/bin/qccdc_cli.exe",
                          REPO / "Compiler/ocaml/_build/default/bin/qccdc_cli") if p.is_file()), None)
BUILT_QCHECK = next((p for d in ("Compiler/lean/checker/.lake/build/bin", "Compiler/lean/.lake/build/bin")
                     for p in (REPO / d / "qcheck.exe", REPO / d / "qcheck") if p.is_file()), None)


def test_the_manifest_pins_every_asset_and_its_source():
    m = toolchain.manifest()["qccdc_cli"]
    assert set(m["assets"]) == {"windows-x86_64", "linux-x86_64", "macos-arm64"}
    for key, a in m["assets"].items():
        assert a["url"].startswith("https://qccd.academy/downloads/toolchain/qccdc/" + m["version"] + "/")
        assert len(a["sha256"]) == 64 and a["bytes"] > 500_000
        if a.get("encoding") == "gzip":
            assert a["url"].endswith(".gz") and len(a["unpacked_sha256"]) == 64 and a["unpacked_bytes"] > a["bytes"]
    assert m["source"]["tree"].startswith(m["version"])


def test_the_published_checker_is_pinned_to_the_sources_it_is_built_from():
    """If this fails, the Lean checker's sources changed in a commit and the published `qcheck` is
    older than them: rebuild and publish it (deploy/toolchain/README.md), then update toolchain.json."""
    m = toolchain.manifest()["qcheck"]
    assert set(m["source"]["paths"]) == set(toolchain.QCHECK_SOURCES)
    assert m["version"] == toolchain.qcheck_version(m["source"]["paths"])
    for a in m["assets"].values():
        assert a["url"].startswith("https://qccd.academy/downloads/toolchain/qcheck/" + m["version"] + "/")
    try:
        ids = {p: subprocess.run(["git", "rev-parse", "--verify", "-q", f"HEAD:{p}"], cwd=REPO, capture_output=True, text=True,
                                 timeout=30).stdout.strip() for p in toolchain.QCHECK_SOURCES}
    except OSError:
        pytest.skip("git is not available")
    if not all(ids.values()):
        pytest.skip("not a git checkout, or the checker's sources are not committed yet")
    assert m["source"]["paths"] == ids


def test_the_published_compiler_is_built_from_the_committed_source():
    """If this fails, Compiler/ocaml changed in a commit and the published binaries are older than
    the source: rebuild and publish them (deploy/toolchain/README.md), then update toolchain.json."""
    try:
        tree = subprocess.run(["git", "rev-parse", "HEAD:Compiler/ocaml"], cwd=REPO, capture_output=True,
                              text=True, timeout=30).stdout.strip()
    except OSError:
        pytest.skip("git is not available")
    if not tree:
        pytest.skip("not a git checkout")
    assert toolchain.manifest()["qccdc_cli"]["source"]["tree"] == tree


class _Serve:
    def __init__(self, root: Path):
        class H(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=str(root), **k)

            def log_message(self, *a):
                pass
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def served(tmp_path, monkeypatch):
    if BUILT is None:
        pytest.skip("no compiler built in this checkout to serve")
    monkeypatch.setenv("QCCD_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("QCCD_QCCDC", raising=False)
    (tmp_path / "srv").mkdir()
    data = BUILT.read_bytes()
    (tmp_path / "srv" / "qccdc").write_bytes(data)
    s = _Serve(tmp_path / "srv")
    man = {"qccdc_cli": {"version": "testv1", "source": {"tree": "testv1"}, "assets": {
        toolchain.platform_key(): {"url": s.url + "/qccdc", "bytes": len(data),
                                   "sha256": hashlib.sha256(data).hexdigest()}}}}
    yield man, tmp_path
    s.close()


def test_install_checks_the_bytes_then_runs_it_once(served, monkeypatch):
    man, tmp = served
    r = toolchain.install(man=man, say=lambda *_: None)
    p = Path(r["path"])
    assert r["status"] == "installed" and p.is_file() and p.parent.name == "testv1"
    assert str(p).startswith(str(tmp / "home" / "toolchain" / "qccdc"))
    assert toolchain.install(man=man, say=lambda *_: None)["status"] == "already_installed"
    assert toolchain.installed(man) == p
    # a checkout without its own build uses the installed one
    monkeypatch.setattr(evaluator, "REPO", tmp / "no-checkout")
    monkeypatch.setattr(toolchain, "manifest", lambda path=None: man)
    assert evaluator.Toolchain.discover().qccdc == p
    s = toolchain.status(man)
    assert s["installed"]["matches_manifest"] and s["in_use"]["from"] == "installed"
    # an explicit QCCD_QCCDC still wins, even when it names nothing
    monkeypatch.setenv("QCCD_QCCDC", str(tmp / "nothing.exe"))
    assert evaluator.Toolchain.discover().qccdc is None


def test_the_checker_is_installed_beside_the_compiler_and_used(served, monkeypatch):
    if BUILT_QCHECK is None:
        pytest.skip("no Lean checker built in this checkout to serve")
    man, tmp = served
    data = BUILT_QCHECK.read_bytes()
    (tmp / "srv" / "qcheck").write_bytes(data)
    url = next(iter(man["qccdc_cli"]["assets"].values()))["url"].rsplit("/", 1)[0] + "/qcheck"
    man = {**man, "qcheck": {"version": "testq1", "source": {}, "assets": {
        toolchain.platform_key(): {"url": url, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}}}}
    r = toolchain.install(man=man, say=lambda *_: None)
    q = Path(r["qcheck"]["path"])
    assert r["qcheck"]["status"] == "installed" and q.parent.name == "testq1"   # it accepted and rejected
    assert toolchain.installed_qcheck(man) == q
    monkeypatch.setattr(evaluator, "REPO", tmp / "no-checkout")
    monkeypatch.setattr(toolchain, "manifest", lambda path=None: man)
    monkeypatch.delenv("QCCD_QCHECK", raising=False)
    assert evaluator.Toolchain.discover().qcheck == q
    assert toolchain.status(man)["qcheck"]["in_use"]["from"] == "installed"


def test_a_download_that_does_not_match_is_refused_and_nothing_runs(served):
    man, tmp = served
    a = next(iter(man["qccdc_cli"]["assets"].values()))
    bad = json.loads(json.dumps(man))
    next(iter(bad["qccdc_cli"]["assets"].values()))["sha256"] = "0" * 64
    with pytest.raises(ToolchainError, match="does not match"):
        toolchain.install(man=bad, say=lambda *_: None)
    short = json.loads(json.dumps(man))
    next(iter(short["qccdc_cli"]["assets"].values()))["bytes"] = a["bytes"] - 1
    with pytest.raises(ToolchainError, match="larger than the pinned"):
        toolchain.install(man=short, say=lambda *_: None)
    d = tmp / "home" / "toolchain" / "qccdc" / "testv1"
    assert not d.exists() or not any(d.iterdir())             # no binary, no partial file
    assert toolchain.installed(man) is None


def test_a_gzip_download_is_checked_before_and_after_unpacking(served):
    """The macOS assets are served compressed (the checker is 106 MB, 37 MB gzipped): the download's
    bytes are pinned, and so is what they unpack to."""
    import gzip
    man, tmp = served
    a = next(iter(man["qccdc_cli"]["assets"].values()))
    data = BUILT.read_bytes()
    (tmp / "srv" / "qccdc.gz").write_bytes(gz := gzip.compress(data))
    z = {**a, "url": a["url"] + ".gz", "bytes": len(gz), "sha256": hashlib.sha256(gz).hexdigest(),
         "encoding": "gzip", "unpacked_bytes": len(data), "unpacked_sha256": hashlib.sha256(data).hexdigest()}
    zman = {"qccdc_cli": {**man["qccdc_cli"], "assets": {toolchain.platform_key(): z}}}
    bad = {"qccdc_cli": {**man["qccdc_cli"], "assets": {toolchain.platform_key(): {**z, "unpacked_sha256": "0" * 64}}}}
    with pytest.raises(ToolchainError, match="unpacked file does not match"):
        toolchain.install(man=bad, say=lambda *_: None)
    assert toolchain.installed(bad) is None
    r = toolchain.install(man=zman, say=lambda *_: None)
    assert r["status"] == "installed" and Path(r["path"]).read_bytes() == data
    assert toolchain.installed(zman) == Path(r["path"])
    assert toolchain.install(man=zman, say=lambda *_: None)["status"] == "already_installed"


def test_no_prebuilt_for_this_platform_says_how_to_build(monkeypatch, tmp_path):
    monkeypatch.setenv("QCCD_HOME", str(tmp_path))
    monkeypatch.setattr(toolchain, "platform_key", lambda: "macos-x86_64")
    with pytest.raises(ToolchainError, match="no prebuilt compiler for macos-x86_64.*dune build"):
        toolchain.install(say=lambda *_: None)


def test_the_cli_reports_status(tmp_path):
    env = {**os.environ, "QCCD_HOME": str(tmp_path)}
    r = subprocess.run([sys.executable, "-m", "qccd.workspace", "toolchain", "status", "--json"], cwd=REPO,
                       capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, r.stderr
    s = json.loads(r.stdout)
    assert s["version"] == toolchain.manifest()["qccdc_cli"]["version"] and s["installed"] is None
