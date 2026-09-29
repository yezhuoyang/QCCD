"""A workspace puts a design on the OFFICIAL leaderboard, credited to the signed-in person.

Everything is real except the site's page: a real official service on a loopback port (its HTTP
API, its database, its inline grader), the workspace's real HTTP client, the credentials file,
and the toolchain grading the design on both sides.  The person's "Allow" on qccd.academy is the
grant the site would sign (`sign_grant`, the same code the site's copy is tested against).
"""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

from qccd.official.accounts import sign_grant  # noqa: E402
from qccd.official.service import OfficialService, create_app  # noqa: E402
from qccd.official.worker import process_one  # noqa: E402
from qccd.workspace.app import Workspace, WorkspaceError  # noqa: E402
from qccd.workspace.evaluator import Toolchain  # noqa: E402
from qccd.workspace.tasks import RELEASES_DIR  # noqa: E402

SECRET = "w" * 40
TC = Toolchain.discover()
pytestmark = pytest.mark.skipif(TC.qccdc is None or TC.qcheck is None, reason="toolchain not built")
HUMAN = {"kind": "human", "id": "view:studio", "label": "Studio"}
AGENT = {"kind": "agent", "id": "agent:test", "label": "Claude"}


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def world(tmp_path, monkeypatch):
    import uvicorn
    monkeypatch.setenv("QCCD_ACCOUNT_SECRET", SECRET)
    monkeypatch.setenv("QCCD_CREDENTIALS", str(tmp_path / "home" / "credentials.json"))
    svc = OfficialService(f"sqlite:///{tmp_path / 'o.db'}", RELEASES_DIR, tmp_path / "artifacts")
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(svc), host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    monkeypatch.setenv("QCCD_OFFICIAL_URL", f"http://127.0.0.1:{port}")
    ws = Workspace.init(tmp_path / "ws", "ghz4@1")
    yield {"svc": svc, "ws": ws, "tmp": tmp_path, "url": f"http://127.0.0.1:{port}"}
    ws.close()
    server.should_exit = True
    th.join(timeout=10)


def _wait(pred, timeout=120, every=0.3):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(every)
    raise AssertionError("timed out")


def _test_design(ws, design="main", board="ghz4"):
    """The Studio's Test on this board: compile, adopt, freeze, reference grade -- to its verdict."""
    job = ws.submit_design(HUMAN, design=design, board=board)
    j = _wait(lambda: (lambda x: x if x["status"] not in ("queued", "running") else None)(ws.job(job["job_id"])))
    assert j["status"] == "succeeded", j
    sub = j["result"]["submission_id"]
    _wait(lambda: ws.submission(sub)["status"] != "grading")
    return sub


def _sign_in(w, name="Ada Lovelace"):
    ws, svc = w["ws"], w["svc"]
    s = ws._accounts().start_signin()
    assert s["state"] == "waiting" and "/connect/?code=" + s["code"] in s["verify_url"]
    assert svc.link_info(s["code"])["label"].startswith(("QCCD workspace on", "a QCCD workspace"))
    now = time.time()
    svc.approve_link(s["code"], sign_grant({"v": 1, "aud": "qccd-official", "sub": "7", "name": name,
                                            "purpose": "link", "code": s["code"], "iat": now, "exp": now + 300,
                                            "jti": "t"}, SECRET.encode()))
    _wait(lambda: ws.account()["signed_in"], timeout=20)


def _grade_on_server(w):
    assert process_one(w["svc"], w["tmp"] / "spool", inline=True, releases=RELEASES_DIR)


def test_the_person_signs_in_tests_submits_and_is_credited(world):
    ws, svc = world["ws"], world["svc"]
    sub = _test_design(ws)
    assert ws.submission(sub)["status"] == "eligible"
    # not signed in: nothing leaves this computer, and the Submit says what to do
    with pytest.raises(WorkspaceError) as e:
        ws.request_publication(HUMAN, board="ghz4")
    assert e.value.code == "not_signed_in"
    _sign_in(world)
    acct = ws.account(verify=True)
    assert acct["signed_in"] and acct["name"] == "Ada Lovelace"
    assert "token" not in json.dumps(acct) and "qccdk_" not in json.dumps(acct)     # the name only, never the key
    creds = json.loads((world["tmp"] / "home" / "credentials.json").read_text())
    assert creds[world["url"]]["token"].startswith("qccdk_")
    # the Leaderboard panel before: the board is on the server, the test passed, nobody ahead of it
    view = ws.board_view("ghz4")
    assert view["board"]["on_server"] and view["board"]["same_edition"]
    assert view["test"]["eligible"] is True and view["test"]["would_rank"] == 1
    # Submit: the person's click is the approval; it uploads with this computer's key
    p = ws.request_publication(HUMAN, board="ghz4", display_name="My GHZ chain")
    assert p["state"] in ("uploading", "uploaded") and p["credit"] == "Ada Lovelace"
    p = _wait(lambda: (lambda x: x if x["state"] != "uploading" else None)(ws.publication(p["publication_id"])))
    assert p["state"] == "uploaded", p
    assert p["official"]["by"] == "Ada Lovelace" and p["official"]["status"] == "queued"
    _grade_on_server(world)
    p = ws._pub_json(ws._refresh_official(ws._pub_row(p["publication_id"]), force=True))
    assert p["official"]["status"] == "eligible" and (p["official"]["rank"], p["official"]["of"]) == (1, 1)
    rows = svc.leaderboard("ghz4@1")["rows"]
    assert [(r["display_name"], r["by"]) for r in rows] == [("My GHZ chain", "Ada Lovelace")]
    # asking again for the same graded design returns the same request, it does not upload twice
    again = ws.request_publication(HUMAN, board="ghz4")
    assert again["duplicate"] and again["publication_id"] == p["publication_id"]
    # the panel after: the official row with its credit
    view = ws.board_view("ghz4")
    ws._official_cache.clear()
    view = ws.board_view("ghz4")
    assert [(r["display_name"], r["by"]) for r in view["official"]["rows"]] == [("My GHZ chain", "Ada Lovelace")]
    assert view["publications"][0]["state"] == "uploaded"


def test_an_agent_asks_and_only_the_person_submits(world):
    ws = world["ws"]
    _sign_in(world, name="Grace Hopper")
    # the agent's "submit it" on a design that was never tested: it is tested first
    p = ws.request_publication(AGENT, board="ghz4")
    assert p["state"] == "testing" and "card" in p["next"]
    p = _wait(lambda: (lambda x: x if x["state"] != "testing" else None)(ws.publication(p["publication_id"])), 180)
    assert p["state"] == "awaiting_person" and p["credit"] == "Grace Hopper"
    assert p["local"]["eligible"] is True
    with pytest.raises(WorkspaceError) as e:                  # the agent cannot press Submit for them
        ws.confirm_publication(AGENT, p["publication_id"])
    assert e.value.status == 403
    assert ws.store.one("SELECT COUNT(*) AS n FROM submissions", ())["n"] == 1        # nothing uploaded, only tested
    p = ws.confirm_publication(HUMAN, p["publication_id"], display_name="Grace's chain")
    p = _wait(lambda: (lambda x: x if x["state"] != "uploading" else None)(ws.publication(p["publication_id"])))
    assert p["state"] == "uploaded" and p["display_name"] == "Grace's chain"
    assert world["svc"].db.one("SELECT credit FROM submissions")["credit"] == "Grace Hopper"


def test_a_failed_or_stale_test_is_not_submitted_and_signing_out_stops_uploads(world):
    ws, svc = world["ws"], world["svc"]
    _sign_in(world)
    sub = _test_design(ws)
    # the design changes after the test: its grade is stale, so Submit asks for a new test
    head = ws.head("main")
    ws.apply_change_set({"expected_revision": head.revision, "request_id": "move", "mode": "apply",
                         "operations": [{"type": "add_site", "id": "Z9", "pos": [9.5, 3], "zone": "trap"}]}, HUMAN)
    assert ws.submission(sub)["stale"]
    with pytest.raises(WorkspaceError) as e:
        ws.request_publication(HUMAN, board="ghz4")
    assert e.value.code == "not_tested"
    # signing out revokes the key on the server first
    key = json.loads((world["tmp"] / "home" / "credentials.json").read_text())[world["url"]]["token"]
    out = ws._accounts().sign_out()
    assert out["revoked_on_server"] is True and not ws.account()["signed_in"]
    with pytest.raises(Exception):
        svc.authenticate(key, None)
    assert world["url"] not in json.loads((world["tmp"] / "home" / "credentials.json").read_text())


def test_the_agent_sees_the_account_and_the_board_the_person_is_on(world):
    ws = world["ws"]
    ctx = ws.context(AGENT)
    assert ctx["account"]["signed_in"] is False and "only the person can sign in" in ctx["account"]["about"]
    _sign_in(world)
    assert ws.context(AGENT)["account"] == {"signed_in": True, "name": "Ada Lovelace",
                                            "about": "submissions to the official leaderboard are credited to Ada Lovelace"}
    pres = ws.present(AGENT, "open_board", {"board": "GHZ"})
    assert pres["status"] in ("requested", "no_connected_view")
    ev = [e for e in ws.events_since(0, limit=500)["events"] if e["type"] == "present"][-1]
    assert ev["payload"]["target"]["board"] == "ghz4@1"


def test_the_websites_try_your_own_design_reaches_the_studio_on_that_board():
    """The site's button links to the mirror's well-known port; the mirror forwards the board to the
    workspace's Studio, and nothing else (a board name is letters, digits and a few signs)."""
    from types import SimpleNamespace

    from starlette.testclient import TestClient

    from qccd.workspace.mirror import SiteMirror, create_mirror_app
    state = SimpleNamespace(info={"web_port": 47199, "port": 47198}, ws=SimpleNamespace())
    c = TestClient(create_mirror_app(state, SiteMirror("http://127.0.0.1:9")), base_url="http://127.0.0.1:47199")
    for q, want in (("?board=bb144", "?board=bb144"), ("?board=BB%20[[144,12,12]]", "?board=BB%20%5B%5B144%2C12%2C12%5D%5D"),
                    ("?board=%3Cscript%3E", ""), ("", "")):
        r = c.get("/studio" + q, follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == "http://127.0.0.1:47198/studio" + want, (q, r.headers)
