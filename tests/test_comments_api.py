"""THE ACCOUNTS AND COMMENTS API (`qccd/site/comments_api.py`), driven over HTTP.

Every test starts the real server on a free port with a fresh database and speaks to it
the way the browser does: JSON bodies, the session cookie, an Origin header.  What is
asserted is what a reader or an admin is allowed to do, and what a stranger is not:

  * nothing about comments is readable without a session;
  * a reader deletes their own comments and nobody else's; an admin deletes anything;
  * a session ends on sign-out and on a password change elsewhere;
  * a cross-site request, a malformed body, an oversized body and a burst of sign-in
    attempts are refused with the status that names them.
"""

from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from qccd.site import comments_api as capi

ADMIN = "admin@example.org"


class Client:
    """One browser: keeps its cookie, sends an Origin like a fetch would."""

    def __init__(self, host: str, port: int, origin: str | None = None):
        self.host, self.port = host, port
        self.origin = origin if origin is not None else f"http://{host}:{port}"
        self.cookie = None

    def call(self, method: str, path: str, body=None, raw: bytes | None = None, ctype="application/json", origin="default"):
        c = http.client.HTTPConnection(self.host, self.port, timeout=10)
        headers = {}
        if self.cookie:
            headers["Cookie"] = f"{capi.COOKIE}={self.cookie}"
        o = self.origin if origin == "default" else origin
        if o:
            headers["Origin"] = o
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if data is not None:
            headers["Content-Type"] = ctype
        c.request(method, path, body=data, headers=headers)
        r = c.getresponse()
        text = r.read().decode("utf-8")
        for k, v in r.getheaders():
            if k.lower() == "set-cookie" and v.startswith(capi.COOKIE + "="):
                val = v.split(";", 1)[0].split("=", 1)[1]
                self.cookie = val or None
        c.close()
        try:
            return r.status, json.loads(text)
        except ValueError:
            return r.status, text

    def get(self, path):
        return self.call("GET", path)

    def post(self, path, body=None):
        return self.call("POST", path, body if body is not None else {})

    def delete(self, path):
        return self.call("DELETE", path)


@pytest.fixture
def srv(tmp_path: Path):
    capi.Handler.app = capi.App(capi.Store(str(tmp_path / "c.db"), {ADMIN}), secure=False, proxied=False)
    s = ThreadingHTTPServer(("127.0.0.1", 0), capi.Handler)
    s.daemon_threads = True
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield ("127.0.0.1", s.server_address[1], capi.Handler.app)
    s.shutdown()
    s.server_close()


def reader(srv, email="ada@example.org", name="Ada Reader", password="reader-pass-123") -> Client:
    c = Client(srv[0], srv[1])
    st, d = c.post("/api/register", {"email": email, "password": password, "name": name})
    assert st == 200, d
    return c


def test_health_and_anonymous_me(srv):
    c = Client(srv[0], srv[1])
    assert c.get("/api/health") == (200, {"ok": True})
    assert c.get("/api/me") == (200, {"user": None})
    st, d = c.get("/api/threads?page=/")
    assert st == 401 and "sign in" in d["error"]
    assert c.get("/api/nothing")[0] == 404


def test_register_is_validated(srv):
    c = Client(srv[0], srv[1])
    assert c.post("/api/register", {"email": "not-an-email", "password": "reader-pass-123", "name": "A B"})[0] == 400
    assert c.post("/api/register", {"email": "a@b.co", "password": "short", "name": "A B"})[0] == 400
    assert c.post("/api/register", {"email": "a@b.co", "password": "reader-pass-123", "name": "A"})[0] == 400
    assert c.cookie is None
    st, d = c.post("/api/register", {"email": "A@B.co", "password": "reader-pass-123", "name": "  A   B "})
    assert st == 200 and d["user"]["email"] == "a@b.co" and d["user"]["name"] == "A B" and d["user"]["admin"] is False
    assert c.cookie
    # the address is taken now, in any case
    c2 = Client(srv[0], srv[1])
    assert c2.post("/api/register", {"email": "a@b.CO", "password": "reader-pass-123", "name": "Some One"})[0] == 409
    assert c.get("/api/me")[1]["user"]["name"] == "A B"


def test_login_logout(srv):
    reader(srv)
    c = Client(srv[0], srv[1])
    assert c.post("/api/login", {"email": "ada@example.org", "password": "wrong-pass-123"})[0] == 401
    assert c.cookie is None
    st, d = c.post("/api/login", {"email": "ADA@example.org", "password": "reader-pass-123"})
    assert st == 200 and d["user"]["name"] == "Ada Reader" and c.cookie
    token = c.cookie
    assert c.post("/api/logout")[0] == 200 and c.cookie is None
    # the old token is dead server-side, not just forgotten by the browser
    c.cookie = token
    assert c.get("/api/me")[1]["user"] is None


def test_threads_are_per_page_and_only_for_readers(srv):
    ada = reader(srv)
    anchor = {"sel": "#intro > p:nth-of-type(2)", "tag": "p", "ox": 0.5, "oy": 0.5, "text": "Ions are", "dx": 300, "dy": 900}
    st, d = ada.post("/api/threads", {"page": "/docs/rules/index.html", "anchor": anchor, "text": "  This is wrong.  "})
    assert st == 200
    th = d["thread"]
    assert th["page"] == "/docs/rules/" and th["anchor"] == anchor and th["user"]["name"] == "Ada Reader"
    assert [c["text"] for c in th["comments"]] == ["This is wrong."]
    assert "email" not in th["user"]  # other readers never learn an email
    st, d = ada.get("/api/threads?page=/docs/rules/")
    assert st == 200 and [t["id"] for t in d["threads"]] == [th["id"]]
    assert ada.get("/api/threads?page=/physics/")[1]["threads"] == []
    # validation
    assert ada.post("/api/threads", {"page": "docs/rules/", "anchor": anchor, "text": "x"})[0] == 400
    assert ada.post("/api/threads", {"page": "/x/", "anchor": "nope", "text": "x"})[0] == 400
    assert ada.post("/api/threads", {"page": "/x/", "anchor": anchor, "text": "   "})[0] == 400
    assert ada.post("/api/threads", {"page": "/x/", "anchor": anchor, "text": "x" * (capi.MAX_TEXT + 1)})[0] == 400
    # a stranger sees nothing
    nobody = Client(srv[0], srv[1])
    assert nobody.get("/api/threads?page=/docs/rules/")[0] == 401
    assert nobody.post("/api/threads/%d/comments" % th["id"], {"text": "hi"})[0] == 401


def test_replies_and_who_may_delete(srv):
    ada = reader(srv)
    bob = reader(srv, "bob@example.org", "Bob Other", "other-pass-123")
    anchor = {"sel": "#p1", "tag": "p", "ox": 0.1, "oy": 0.2}
    th = ada.post("/api/threads", {"page": "/p/", "anchor": anchor, "text": "root"})[1]["thread"]
    st, d = bob.post("/api/threads/%d/comments" % th["id"], {"text": "reply from bob"})
    assert st == 200 and d["comment"]["user"]["name"] == "Bob Other"
    bob_c = d["comment"]["id"]
    ada_c = th["comments"][0]["id"]
    assert bob.post("/api/threads/999/comments", {"text": "x"})[0] == 404
    # bob may not remove ada's comment or her thread; ada may not remove bob's reply
    assert bob.delete("/api/comments/%d" % ada_c)[0] == 403
    assert bob.delete("/api/threads/%d" % th["id"])[0] == 403
    assert ada.delete("/api/comments/%d" % bob_c)[0] == 403
    # bob removes his own reply; the thread stays
    assert bob.delete("/api/comments/%d" % bob_c) == (200, {"thread": th["id"], "gone": False})
    assert len(ada.get("/api/threads?page=/p/")[1]["threads"][0]["comments"]) == 1
    # ada removes the last comment: the thread goes with it
    assert ada.delete("/api/comments/%d" % ada_c) == (200, {"thread": th["id"], "gone": True})
    assert ada.get("/api/threads?page=/p/")[1]["threads"] == []
    assert ada.delete("/api/threads/%d" % th["id"])[0] == 404
    # the thread's author removes the whole thread, replies and all
    th2 = ada.post("/api/threads", {"page": "/p/", "anchor": anchor, "text": "root 2"})[1]["thread"]
    bob.post("/api/threads/%d/comments" % th2["id"], {"text": "bob again"})
    assert ada.delete("/api/threads/%d" % th2["id"])[0] == 200
    assert bob.get("/api/threads?page=/p/")[1]["threads"] == []


def test_admin_manages_everything(srv):
    ada = reader(srv)
    th = ada.post("/api/threads", {"page": "/p/", "anchor": {"sel": "#p1", "tag": "p"}, "text": "root"})[1]["thread"]
    ada.post("/api/threads", {"page": "/q/", "anchor": {"sel": "#p2", "tag": "p"}, "text": "other page"})
    assert ada.get("/api/admin/threads")[0] == 403
    # the admin account is made from the command line, never by registering
    capi.main(["passwd", "--db", srv[2].store.path, ADMIN, "--name", "Site Admin", "--password", "admin-pass-123"])
    adm = Client(srv[0], srv[1])
    st, d = adm.post("/api/login", {"email": ADMIN, "password": "admin-pass-123"})
    assert st == 200 and d["user"]["admin"] is True and d["user"]["name"] == "Site Admin"
    st, d = adm.get("/api/admin/threads")
    assert st == 200 and [t["page"] for t in d["threads"]] == ["/q/", "/p/"]  # newest first
    st, d = adm.post("/api/threads/%d/comments" % th["id"], {"text": "noted"})
    assert st == 200
    assert adm.delete("/api/comments/%d" % th["comments"][0]["id"]) == (200, {"thread": th["id"], "gone": False})
    assert adm.delete("/api/threads/%d" % th["id"])[0] == 200
    assert [t["page"] for t in adm.get("/api/admin/threads")[1]["threads"]] == ["/q/"]
    # nobody registers the admin's address
    assert Client(srv[0], srv[1]).post("/api/register", {"email": ADMIN, "password": "x" * 12, "name": "Imp Oster"})[0] == 409
    # the command line lists what there is
    assert {u["email"] for u in srv[2].store.users()} == {"ada@example.org", ADMIN}


def test_password_change_ends_other_sessions(srv):
    ada = reader(srv)
    other = Client(srv[0], srv[1])
    assert other.post("/api/login", {"email": "ada@example.org", "password": "reader-pass-123"})[0] == 200
    assert ada.post("/api/password", {"old": "wrong-pass-123", "new": "fresh-pass-123"})[0] == 403
    assert ada.post("/api/password", {"old": "reader-pass-123", "new": "short"})[0] == 400
    assert ada.post("/api/password", {"old": "reader-pass-123", "new": "fresh-pass-123"})[0] == 200
    assert ada.get("/api/me")[1]["user"]["name"] == "Ada Reader"       # this session stays
    assert other.get("/api/me")[1]["user"] is None                     # the other one ended
    c = Client(srv[0], srv[1])
    assert c.post("/api/login", {"email": "ada@example.org", "password": "reader-pass-123"})[0] == 401
    assert c.post("/api/login", {"email": "ada@example.org", "password": "fresh-pass-123"})[0] == 200
    # the command line resets it and ends every session
    capi.main(["passwd", "--db", srv[2].store.path, "ada@example.org", "--password", "reset-pass-123"])
    assert ada.get("/api/me")[1]["user"] is None
    assert c.post("/api/login", {"email": "ada@example.org", "password": "reset-pass-123"})[0] == 200


def test_refusals(srv):
    ada = reader(srv)
    # a cross-site page carrying the cookie
    assert ada.call("POST", "/api/threads", {"page": "/p/", "anchor": {}, "text": "x"}, origin="https://evil.example")[0] == 403
    assert ada.call("POST", "/api/logout", {}, origin="https://evil.example")[0] == 403
    assert ada.get("/api/me")[1]["user"]["name"] == "Ada Reader"  # still signed in
    # bodies
    assert ada.call("POST", "/api/threads", raw=b"{not json", ctype="application/json")[0] == 400
    assert ada.call("POST", "/api/threads", raw=b"[1,2]", ctype="application/json")[0] == 400
    assert ada.call("POST", "/api/threads", raw=b"page=/p/", ctype="application/x-www-form-urlencoded")[0] == 400
    assert ada.call("POST", "/api/threads", raw=b"x" * (capi.MAX_BODY + 1))[0] == 413
    # method
    assert ada.call("DELETE", "/api/me")[0] == 405
    # the connection is still in sync after every refusal
    assert ada.get("/api/health") == (200, {"ok": True})


def test_sign_in_attempts_are_rate_limited(srv):
    c = Client(srv[0], srv[1])
    for _ in range(capi.ATTEMPTS):
        assert c.post("/api/login", {"email": "ghost@example.org", "password": "whatever-123"})[0] == 401
    assert c.post("/api/login", {"email": "ghost@example.org", "password": "whatever-123"})[0] == 429
    # registering counts separately, and a real account can still be made
    assert c.post("/api/register", {"email": "new@example.org", "password": "reader-pass-123", "name": "New One"})[0] == 200


def test_password_hashes_are_salted_scrypt():
    a, b = capi.hash_password("reader-pass-123"), capi.hash_password("reader-pass-123")
    assert a != b and a.startswith("scrypt$16384$8$1$")
    assert capi.check_password("reader-pass-123", a) and not capi.check_password("reader-pass-124", a)
    assert not capi.check_password("x", "garbage")
