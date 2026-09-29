"""The site vouches for a person to the official leaderboard: `POST /api/leaderboard/grant`.

The site's accounts service (one stdlib file on the server) and the official service (in its own
container) share no code at run time, only a secret.  So this checks the two copies of the grant
against each other: a grant the REAL site server signs, over HTTP, for a signed-in reader, is one
the official service's `verify_grant` accepts -- and every refusal on the site's side."""

from __future__ import annotations

import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

capi = pytest.importorskip("qccd.site.comments_api", reason="the site package is not in this tree")
accounts = pytest.importorskip("qccd.official.accounts", reason="the official service is not in this tree")
GrantError, verify_grant = accounts.GrantError, accounts.verify_grant

from test_comments_api import ADMIN, Client  # noqa: E402

SECRET = "g" * 40


@pytest.fixture
def site(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("QCCD_ACCOUNT_SECRET", SECRET)
    store = capi.Store(str(tmp_path / "c.db"), {ADMIN})
    capi.Handler.app = capi.App(store, secure=False, proxied=False)
    s = ThreadingHTTPServer(("127.0.0.1", 0), capi.Handler)
    s.daemon_threads = True
    threading.Thread(target=s.serve_forever, daemon=True).start()
    store.set_password("ada@example.org", "correct horse battery", name="Ada Lovelace")
    yield ("127.0.0.1", s.server_address[1])
    s.shutdown()
    s.server_close()


def signed_in(site) -> Client:
    c = Client(*site)
    st, _ = c.post("/api/login", {"email": "ada@example.org", "password": "correct horse battery"})
    assert st == 200
    return c


def test_a_signed_in_reader_gets_a_grant_the_official_service_accepts(site, monkeypatch):
    c = signed_in(site)
    st, body = c.post("/api/leaderboard/grant", {"purpose": "link", "code": "ABCD-EFGH"})
    assert st == 200 and body["user"]["name"] == "Ada Lovelace"
    acct = verify_grant(body["grant"], purpose="link", code="ABCD-EFGH")
    assert acct["name"] == "Ada Lovelace" and acct["id"].startswith("site:")
    with pytest.raises(GrantError):                                  # bound to that code only
        verify_grant(body["grant"], purpose="link", code="ZZZZ-ZZZZ")
    st, body = c.post("/api/leaderboard/grant", {"purpose": "manage"})
    assert st == 200 and verify_grant(body["grant"], purpose="manage")["name"] == "Ada Lovelace"
    # it expires: five minutes, not a lasting credential
    with pytest.raises(GrantError):
        verify_grant(body["grant"], purpose="manage", now=time.time() + 400)


def test_the_site_refuses_strangers_malformed_requests_and_other_sites(site, monkeypatch):
    anon = Client(*site)
    assert anon.post("/api/leaderboard/grant", {"purpose": "manage"})[0] == 401
    c = signed_in(site)
    assert c.post("/api/leaderboard/grant", {"purpose": "admin"})[0] == 400
    assert c.post("/api/leaderboard/grant", {"purpose": "link", "code": "not a code"})[0] == 400
    assert c.call("POST", "/api/leaderboard/grant", {"purpose": "manage"}, origin="https://evil.example")[0] == 403
    monkeypatch.setenv("QCCD_ACCOUNT_SECRET", "")
    st, body = c.post("/api/leaderboard/grant", {"purpose": "manage"})
    assert st == 503 and "not set up" in body["error"]
