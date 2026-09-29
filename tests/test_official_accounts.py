"""Signing a workspace in with a qccd.academy account, and the credit on the leaderboard.

The official service holds the links and the keys; the site's accounts service only vouches for
the person with a signed grant (qccd/official/accounts.py).  Here the grant is signed with the
same code the site uses (`sign_grant`, and comments_api.py's own copy in test_comments_api.py),
so every refusal is tested against a real signature, not a stub."""

from __future__ import annotations

import time

import pytest

from qccd.official import accounts
from qccd.official.accounts import GrantError, sign_grant, verify_grant
from qccd.official.service import OfficialError, OfficialService, create_app
from qccd.official.worker import process_one
from qccd.workspace.app import Workspace
from qccd.workspace.bundle import archive_bundle, read_bundle
from qccd.workspace.evaluator import Toolchain
from qccd.workspace.tasks import RELEASES_DIR

SECRET = "s" * 40
TC = Toolchain.discover()
needs_toolchain = pytest.mark.skipif(TC.qccdc is None or TC.qcheck is None, reason="toolchain not built")
HUMAN = {"kind": "human", "id": "cli"}


def grant(purpose="link", code=None, sub="7", name="Ada Lovelace", ttl=300, iat=None, secret=SECRET):
    now = time.time() if iat is None else iat
    return sign_grant({"v": 1, "aud": "qccd-official", "sub": sub, "name": name, "purpose": purpose, "code": code,
                       "iat": now, "exp": now + ttl, "jti": "x"}, secret.encode())


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("QCCD_ACCOUNT_SECRET", SECRET)
    return OfficialService(f"sqlite:///{tmp_path / 'o.db'}", RELEASES_DIR, tmp_path / "artifacts")


@pytest.fixture(scope="module")
def archive(tmp_path_factory):
    """One graded GHZ bundle, compiled with the real toolchain, to upload."""
    if TC.qccdc is None or TC.qcheck is None:
        pytest.skip("toolchain not built")
    ws = Workspace.init(tmp_path_factory.mktemp("acct") / "ws", "ghz4@1")

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
    yield archive_bundle(b)
    ws.close()


def sign_in(svc, name="Ada Lovelace", sub="7", label="QCCD workspace on test-box"):
    link = svc.start_link(label, "10.0.0.1")
    assert svc.collect_link(link["code"], link["poll_secret"])["status"] == "pending"
    svc.approve_link(link["code"], grant(code=link["code"], sub=sub, name=name))
    got = svc.collect_link(link["code"], link["poll_secret"])
    assert got["status"] == "approved" and got["account"]["name"] == name
    return got


def test_a_grant_is_checked_for_signature_purpose_code_and_age(monkeypatch):
    monkeypatch.setenv("QCCD_ACCOUNT_SECRET", SECRET)
    assert verify_grant(grant(code="ABCD-EFGH"), purpose="link", code="ABCD-EFGH") == {
        "id": "site:7", "name": "Ada Lovelace"}
    bad = [(grant(code="ABCD-EFGH", secret="t" * 40), "bad_grant"),          # signed by someone else
           (grant(purpose="manage"), "bad_grant"),                            # for another purpose
           (grant(code="ZZZZ-ZZZZ"), "bad_grant"),                            # for another code
           (grant(code="ABCD-EFGH", iat=time.time() - 600), "grant_expired"),
           (grant(code="ABCD-EFGH", ttl=3600), "grant_expired"),              # a grant may not live long
           ("x.y", "bad_grant"), (None, "bad_grant")]
    for g, code in bad:
        with pytest.raises(GrantError) as e:
            verify_grant(g, purpose="link", code="ABCD-EFGH")
        assert e.value.code == code, g
    body, mac = grant(code="ABCD-EFGH").split(".")
    forged = accounts._b64(accounts._unb64(body).replace(b"Ada Lovelace", b"Somebody Else")) + "." + mac
    with pytest.raises(GrantError):
        verify_grant(forged, purpose="link", code="ABCD-EFGH")


def test_signing_in_fails_closed_without_the_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("QCCD_ACCOUNT_SECRET", raising=False)
    svc = OfficialService(f"sqlite:///{tmp_path / 'o.db'}", RELEASES_DIR, tmp_path / "artifacts")
    link = svc.start_link("box", None)
    with pytest.raises(OfficialError) as e:
        svc.approve_link(link["code"], grant(code=link["code"]))
    assert e.value.code == "signin_unavailable" and e.value.status == 503
    assert svc.collect_link(link["code"], link["poll_secret"])["status"] == "pending"


def test_the_key_is_handed_over_once_and_only_to_the_poll_secret(svc):
    link = svc.start_link("QCCD workspace on test-box", "10.0.0.1")
    assert link["verify_url"].endswith("/connect/?code=" + link["code"])
    assert svc.link_info(link["code"].lower().replace("-", ""))["label"] == "QCCD workspace on test-box"
    with pytest.raises(OfficialError):
        svc.collect_link(link["code"], "not-the-secret")
    svc.approve_link(link["code"], grant(code=link["code"]))
    with pytest.raises(OfficialError) as e:                       # approved once; never twice
        svc.approve_link(link["code"], grant(code=link["code"], name="Mallory"))
    assert e.value.status == 409
    got = svc.collect_link(link["code"], link["poll_secret"])
    with pytest.raises(OfficialError) as e:
        svc.collect_link(link["code"], link["poll_secret"])
    assert e.value.status == 410
    up = svc.authenticate(got["token"], "10.0.0.9")
    assert svc.me(up)["account"] == {"id": "site:7", "name": "Ada Lovelace"}
    assert svc.me(up)["label"] == "QCCD workspace on test-box"


def test_a_denied_or_expired_sign_in_gives_no_key(svc):
    a = svc.start_link("box", None)
    svc.deny_link(a["code"], grant(code=a["code"]))
    with pytest.raises(OfficialError) as e:
        svc.collect_link(a["code"], a["poll_secret"])
    assert e.value.code == "link_denied"
    b = svc.start_link("box", None)
    svc.db.execute("UPDATE links SET expires_at=? WHERE code=?", (time.time() - 1, b["code"]))
    with pytest.raises(OfficialError):
        svc.approve_link(b["code"], grant(code=b["code"]))
    assert svc.link_info(b["code"])["status"] == "expired"


def test_the_person_sees_and_revokes_their_keys_and_nobody_elses(svc):
    k1 = sign_in(svc, label="laptop")
    k2 = sign_in(svc, label="desktop")
    other = sign_in(svc, name="Grace Hopper", sub="9", label="hers")
    listed = svc.account_keys(grant(purpose="manage"))
    assert listed["account"]["name"] == "Ada Lovelace"
    assert sorted(k["label"] for k in listed["keys"]) == ["desktop", "laptop"]
    with pytest.raises(OfficialError):                             # Ada cannot revoke Grace's key
        svc.revoke_account_key(grant(purpose="manage"), other["key_id"])
    svc.revoke_account_key(grant(purpose="manage"), k1["key_id"])
    with pytest.raises(OfficialError) as e:
        svc.authenticate(k1["token"], None)
    assert e.value.status == 401
    up2 = svc.authenticate(k2["token"], None)
    svc.revoke_self(up2)                                            # signing out of a workspace
    with pytest.raises(OfficialError):
        svc.authenticate(k2["token"], None)
    assert svc.authenticate(other["token"], None)["account_name"] == "Grace Hopper"


def test_sign_ins_are_rate_limited_per_address(svc):
    for _ in range(20):
        svc.start_link("box", "10.9.9.9")
    with pytest.raises(OfficialError) as e:
        svc.start_link("box", "10.9.9.9")
    assert e.value.status == 429
    svc.start_link("box", "10.9.9.8")                                # another reader is not affected


@needs_toolchain
def test_the_leaderboard_credits_the_person_and_their_keys_share_it(svc, archive, tmp_path):
    k1 = sign_in(svc, label="laptop")
    k2 = sign_in(svc, label="desktop")
    up1, up2 = svc.authenticate(k1["token"], None), svc.authenticate(k2["token"], None)
    pub = svc.submit(up1, archive, task="ghz4@1", visibility="public", display_name="  Four   in a row ")
    assert pub["by"] == "Ada Lovelace" and pub["display_name"] == "Four in a row"
    process_one(svc, tmp_path / "spool", inline=True, releases=RELEASES_DIR)
    rows = svc.leaderboard("ghz4@1")["rows"]
    assert [(r["display_name"], r["by"]) for r in rows] == [("Four in a row", "Ada Lovelace")]
    # the same person's other computer sees it among theirs, private ones included
    mine = svc.my_submissions(up2)["submissions"]
    assert [m["submission_id"] for m in mine] == [pub["submission_id"]] and mine[0]["status"] == "eligible"
    assert svc.me(up2)["used_today"] == 1
    # a maintainer token is credited with its own name
    m = svc.authenticate(svc.create_uploader("maintainers"), None)
    assert svc.submit(m, archive, task="ghz4@1", visibility="private")["by"] == "maintainers"


def test_the_http_routes_of_signing_in(svc):
    from starlette.testclient import TestClient
    c = TestClient(create_app(svc))
    r = c.post("/v1/links", json={"label": "QCCD workspace on test-box"})
    assert r.status_code == 201
    link = r.json()
    assert c.get(f"/v1/links/{link['code']}").json()["status"] == "pending"
    assert c.post(f"/v1/links/{link['code']}/token", json={"poll_secret": link["poll_secret"]}).json()["status"] == "pending"
    assert c.post(f"/v1/links/{link['code']}/approve", json={"grant": "junk"}).status_code == 403
    assert c.post(f"/v1/links/{link['code']}/approve", json={"grant": grant(code=link["code"])}).json()["ok"]
    got = c.post(f"/v1/links/{link['code']}/token", json={"poll_secret": link["poll_secret"]}).json()
    auth = {"Authorization": f"Bearer {got['token']}"}
    assert c.get("/v1/me", headers=auth).json()["account"]["name"] == "Ada Lovelace"
    assert c.get("/v1/submissions", headers=auth).json() == {"submissions": []}
    assert c.get("/v1/submissions").status_code == 401
    keys = c.post("/v1/account/keys", json={"grant": grant(purpose="manage")}).json()["keys"]
    assert [k["label"] for k in keys] == ["QCCD workspace on test-box"]
    assert c.post("/v1/keys/self/revoke", headers=auth).json()["disabled"]
    assert c.get("/v1/me", headers=auth).status_code == 401
    assert c.post("/v1/links", content=b"[1]").status_code == 400
