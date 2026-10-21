"""The official submission service, in a local development deployment (SQLite, inline
grader subprocess).  The PostgreSQL + container deployment runs the same code paths; see
deploy/official/ for what that adds and what was not exercised here."""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

import pytest

from qccd.official.service import OfficialError, OfficialService, create_app
from qccd.official.worker import process_one
from qccd.workspace.app import Workspace
from qccd.workspace.bundle import archive_bundle, read_bundle
from qccd.workspace.evaluator import Toolchain, comparable
from qccd.workspace.publish import compare_reports
from qccd.workspace.tasks import RELEASES_DIR

TC = Toolchain.discover()
needs_toolchain = pytest.mark.skipif(TC.qccdc is None or TC.qcheck is None, reason="toolchain not built")
HUMAN = {"kind": "human", "id": "cli"}


@pytest.fixture(scope="module")
def local(tmp_path_factory):
    """A graded local submission to upload (compiled with the real toolchain)."""
    if TC.qccdc is None or TC.qcheck is None:
        pytest.skip("toolchain not built")
    ws = Workspace.init(tmp_path_factory.mktemp("l") / "ws", "ghz4@1")

    def wait(jid):
        while ws.job(jid)["status"] not in ("succeeded", "failed", "internal_error", "cancelled", "timeout"):
            time.sleep(0.3)
        return ws.job(jid)
    j = wait(ws.start_job(HUMAN, "compile", {})["job_id"])
    ws.apply_change_set({"expected_revision": 0, "request_id": "a", "mode": "apply",
                         "operations": [j["result"]["adopt_with"]]}, HUMAN)
    s = ws.submit_local(HUMAN, profile="reference")
    wait(s["job_id"])
    sub = ws.submission(s["submission_id"])
    b = read_bundle(ws.root / sub["snapshot"]["dir"] / "bundle")
    yield {"ws": ws, "archive": archive_bundle(b), "report": sub["report"], "digest": b.digest}
    ws.close()


@pytest.fixture
def svc(tmp_path):
    return OfficialService(f"sqlite:///{tmp_path / 'o.db'}", RELEASES_DIR, tmp_path / "artifacts")


def test_uploads_fail_closed_without_configured_uploaders(svc, monkeypatch):
    monkeypatch.delenv("QCCD_OFFICIAL_DEV", raising=False)
    monkeypatch.setenv("QCCD_OFFICIAL_DEV_TOKEN", "dev-secret")
    for tok in (None, "", "dev-secret", "qccd_guess"):
        with pytest.raises(OfficialError) as e:
            svc.authenticate(tok, "127.0.0.1")
        assert e.value.status == 401


def test_the_development_token_is_explicit_and_loopback_only(svc, monkeypatch):
    monkeypatch.setenv("QCCD_OFFICIAL_DEV", "1")
    monkeypatch.setenv("QCCD_OFFICIAL_DEV_TOKEN", "dev-secret")
    assert svc.authenticate("dev-secret", "127.0.0.1")["name"] == "development"
    with pytest.raises(OfficialError):
        svc.authenticate("dev-secret", "10.0.0.7")


def test_tasks_come_from_the_servers_own_releases(svc):
    assert "ghz4@1" in svc.releases
    up = svc.authenticate(svc.create_uploader("t"), "10.0.0.1")
    with pytest.raises(OfficialError) as e:
        svc.submit(up, b"PK\x05\x06" + b"\0" * 18, task="bogus@9")
    assert e.value.code == "unknown_task"


@needs_toolchain
def test_upload_is_idempotent_and_graded_independently(svc, local, tmp_path):
    token = svc.create_uploader("alice")
    up = svc.authenticate(token, "10.0.0.1")
    a = svc.submit(up, local["archive"], task="ghz4@1", visibility="public", display_name="alice/ghz")
    assert a["status"] == "queued" and a["bundle_digest"] == local["digest"]
    b = svc.submit(up, local["archive"], task="ghz4@1")
    assert b["duplicate"] and b["submission_id"] == a["submission_id"]
    assert process_one(svc, tmp_path / "spool", inline=True, releases=RELEASES_DIR)
    s = svc.submission(a["submission_id"], None)
    assert s["status"] == "eligible" and s["report"]["eligible"]
    server = svc.report(a["submission_id"], None)
    parity = compare_reports(local["report"], server)
    assert parity["same_inputs"] and parity["eligibility_agrees"]
    assert parity["stage_differences"] == {} and parity["metric_differences"] == []
    assert comparable(local["report"])["metrics"] == comparable(server)["metrics"]
    board = svc.leaderboard("ghz4@1")
    assert [r["id"] for r in board["rows"]] == [a["submission_id"]]
    assert board["evaluator_policy"] == svc.evaluator_policy


@needs_toolchain
def test_a_leaderboard_entry_opens_its_animation_its_files_and_its_bundle(svc, local, tmp_path):
    """A public entry: the leaderboard carries every metric (a reader ranks by any of them), and
    the entry opens the studio page the grader drew from the graded bundle, each bundle file and
    the whole archive.  A private one opens nothing for anyone but its owner."""
    from starlette.testclient import TestClient
    up = svc.authenticate(svc.create_uploader("frank"), "10.0.0.1")
    a = svc.submit(up, local["archive"], task="ghz4@1", visibility="public", display_name="frank's chain")
    assert process_one(svc, tmp_path / "spool", inline=True, releases=RELEASES_DIR)
    board = svc.leaderboard("ghz4@1")
    row = board["rows"][0]
    assert row["by"] == "frank" and row["display_name"] == "frank's chain"
    assert set(row["metrics"]) >= {m["name"] for m in board["metrics"]} and row["metrics"][board["rank_by"]] == row["rank_value"]
    assert all({"name", "better", "unit"} <= set(m) for m in board["metrics"])
    sid = a["submission_id"]
    page = svc.artifacts / "pages" / f"{sid}.html"
    assert page.exists(), "the grader drew no page"
    c = TestClient(create_app(svc))
    r = c.get(f"/v1/submissions/{sid}/page")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html") and 'name="qccd-page"' in r.text
    for name in svc.READABLE[:5]:
        f = c.get(f"/v1/submissions/{sid}/files/{name}")
        assert f.status_code == 200 and json.loads(f.content), name
    assert c.get(f"/v1/submissions/{sid}/files/report.json").status_code == 404
    z = c.get(f"/v1/submissions/{sid}/bundle.zip")
    assert z.status_code == 200 and z.content == local["archive"]
    # the page is made from an uploaded bundle: it runs sandboxed, never as a page of the site
    csp = r.headers["content-security-policy"]
    assert csp.startswith("sandbox") and "allow-same-origin" not in csp
    assert z.headers["x-content-type-options"] == "nosniff"
    # an entry graded before pages existed has none until a regrade draws it; the service never draws
    page.unlink()
    g = c.get(f"/v1/submissions/{sid}/page")
    assert g.status_code == 404 and g.json()["error"]["code"] == "no_page"
    svc.regrade("ghz4@1")
    while process_one(svc, tmp_path / "spool", inline=True, releases=RELEASES_DIR):
        pass
    assert page.exists() and c.get(f"/v1/submissions/{sid}/page").status_code == 200
    # private: nothing opens for a stranger
    q = svc.submit(svc.authenticate(svc.create_uploader("gina"), "10.0.0.3"), local["archive"],
                   task="ghz4@1", visibility="private")
    process_one(svc, tmp_path / "spool", inline=True, releases=RELEASES_DIR)
    for path in ("page", "files/device.arch.json", "bundle.zip"):
        assert c.get(f"/v1/submissions/{q['submission_id']}/{path}").status_code == 404


@needs_toolchain
def test_a_page_drawn_from_a_bundle_escapes_the_names_in_it(local, tmp_path):
    """The page is drawn from an uploaded bundle: a name made to end the <title> early stays text.
    (A device's name is held to an identifier by its schema; a programme's name is free text.)"""
    from qccd.arch.device import Architecture
    from qccd.cost.models import corrected_model
    from qccd.ir.tsir import TSIR
    from qccd.verify import verify
    from qccd.viz.render import render_html
    from qccd.workspace.bundle import extract_archive
    extract_archive(local["archive"], tmp_path / "b")
    arch = Architecture.from_json(json.loads((tmp_path / "b" / "device.arch.json").read_text(encoding="utf-8")))
    doc = json.loads((tmp_path / "b" / "program.tsir.json").read_text(encoding="utf-8"))
    doc["name"] = "</title><script>alert(1)</script>"
    prog = TSIR.from_json(doc)
    model = corrected_model("qccdsim_jones")
    out = render_html(arch, prog, verify(prog, arch, model).result, model, tmp_path / "p.html")
    page = Path(out).read_text(encoding="utf-8")
    assert "<script>alert(1)" not in page and "&lt;/title&gt;&lt;script&gt;alert(1)" in page


@needs_toolchain
def test_qccd_grade_reproduces_a_downloaded_entry(svc, local, tmp_path, capsys):
    """Reproduce: download an entry's bundle.zip and `qccd grade` it here; the metrics are the
    server's."""
    from starlette.testclient import TestClient
    from qccd.workspace.cli import main
    up = svc.authenticate(svc.create_uploader("hana"), "10.0.0.1")
    sid = svc.submit(up, local["archive"], task="ghz4@1", visibility="public")["submission_id"]
    assert process_one(svc, tmp_path / "spool", inline=True, releases=RELEASES_DIR)
    z = tmp_path / "entry.zip"
    z.write_bytes(TestClient(create_app(svc)).get(f"/v1/submissions/{sid}/bundle.zip").content)
    capsys.readouterr()
    assert main(["grade", str(z), "--json"]) == 0
    mine = json.loads(capsys.readouterr().out)
    assert comparable(mine)["metrics"] == comparable(svc.report(sid, None))["metrics"]


@needs_toolchain
def test_wrong_task_and_tampered_archives_are_refused(svc, local):
    up = svc.authenticate(svc.create_uploader("bob"), "10.0.0.1")
    z = zipfile.ZipFile(io.BytesIO(local["archive"]))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for info in z.infolist():
            data = z.read(info.filename)
            if info.filename == "device.arch.json":
                d = json.loads(data)
                d["name"] = "tampered"
                data = json.dumps(d).encode()
            out.writestr(info.filename, data)
    with pytest.raises(OfficialError) as e:
        svc.submit(up, buf.getvalue(), task="ghz4@1")
    assert e.value.code == "bundle_digest_mismatch"


@needs_toolchain
def test_private_submissions_are_hidden_and_regrades_keep_history(svc, local, tmp_path):
    t1, t2 = svc.create_uploader("carol"), svc.create_uploader("dave")
    carol, dave = svc.authenticate(t1, "10.0.0.1"), svc.authenticate(t2, "10.0.0.2")
    s = svc.submit(carol, local["archive"], task="ghz4@1", visibility="private")
    process_one(svc, tmp_path / "spool", inline=True, releases=RELEASES_DIR)
    assert svc.submission(s["submission_id"], carol)["status"] == "eligible"
    for who in (dave, None):
        with pytest.raises(OfficialError):
            svc.submission(s["submission_id"], who)
    assert svc.leaderboard("ghz4@1")["rows"] == []
    assert svc.regrade("ghz4@1") == 1
    process_one(svc, tmp_path / "spool", inline=True, releases=RELEASES_DIR)
    reps = svc.db.all("SELECT id, superseded_by FROM reports WHERE submission=?", (s["submission_id"],))
    assert len(reps) == 2 and sum(1 for r in reps if r["superseded_by"] is None) == 1


def test_http_api_requires_a_token_and_returns_an_id_at_once(svc, local):
    from starlette.testclient import TestClient
    c = TestClient(create_app(svc))
    assert c.get("/v1/health").json()["ok"]
    assert any(t["id"] == "ghz4@1" for t in c.get("/v1/tasks").json()["tasks"])
    assert c.post("/v1/submissions", content=b"x", headers={"X-QCCD-Task": "ghz4@1"}).status_code == 401
    tok = svc.create_uploader("erin")
    r = c.post("/v1/submissions", content=local["archive"],
               headers={"Authorization": f"Bearer {tok}", "X-QCCD-Task": "ghz4@1", "Content-Type": "application/zip"})
    assert r.status_code == 202 and r.json()["status"] == "queued"
    assert c.get(f"/v1/submissions/{r.json()['submission_id']}/report").status_code == 409   # not graded yet


def test_goal_1_ranks_every_noise_2_memory_together(svc):
    """Goal 1 is one ranking across releases: every curated memory on the shared noise model, by
    the logical error rate per round per logical qubit (the user's decisions, 2026-10-21)."""
    g = svc.goal_board("goal1")
    assert "surface5xz_mem@1" in [r["id"] for r in g["releases"]]
    assert all(svc.releases[r["id"]].manifest["qec"]["noise"] == "qccd-noise@2" for r in g["releases"])
    assert g["rank_by"] == "ler_per_round_per_logical" and g["metrics"][0]["name"] == g["rank_by"]
    assert g["max_ions"] == 200 and g["rows"] == []
    with pytest.raises(OfficialError) as e:
        svc.goal_board("goal9")
    assert e.value.status == 404
