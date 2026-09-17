"""THE ACCOUNTS AND COMMENTS API (`qccd/site/comments_api.py`), driven over HTTP.

Every test starts the real server on a free port with a fresh database and speaks to it
the way the browser does: JSON bodies, the session cookie, an Origin header.  What is
asserted is what a reader or an admin is allowed to do, and what a stranger is not:

  * nothing about comments is readable without a session;
  * an account is made only by following an invitation an admin made, for the address
    that admin named, once, before it expires -- and never by asking;
  * a closed account is signed out at once and refused at the door;
  * a reader deletes their own comments and nobody else's; an admin deletes anything;
  * a session ends on sign-out and on a password change elsewhere;
  * a cross-site request, a malformed body, an oversized body and a burst of sign-in
    attempts are refused with the status that names them.

One test stands a small SMTP server in front of the API and reads the message that comes
out of it, so the invitation mail is checked as a reader would receive it.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
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


class FakeSMTP(threading.Thread):
    """Enough of an SMTP relay for smtplib: it takes one message and keeps it."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.message, self.rcpt = None, []

    def run(self):
        conn, _ = self.sock.accept()
        f = conn.makefile("rb")

        def say(text):
            conn.sendall(text.encode() + b"\r\n")

        say("220 fake.example ESMTP")
        body = []
        while True:
            line = f.readline()
            if not line:
                break
            cmd = line.decode("utf-8", "replace").strip()
            up = cmd.upper()
            if up.startswith(("EHLO", "HELO")):
                say("250-fake.example\r\n250 SIZE 10240000")
            elif up.startswith("RCPT"):
                self.rcpt.append(cmd.split(":", 1)[1].strip())
                say("250 ok")
            elif up == "DATA":
                say("354 go ahead")
                while True:
                    l = f.readline()
                    if l in (b".\r\n", b".\n", b""):
                        break
                    body.append(l)
                self.message = b"".join(body).decode("utf-8", "replace")
                say("250 queued")
            elif up == "QUIT":
                say("221 bye")
                break
            else:
                say("250 ok")
        conn.close()
        self.sock.close()


def invite(srv, email: str, name: str = "") -> str:
    """The admin's half of it, straight on the store: the token that goes in the link."""
    return srv[2].store.create_invite(None, email, name)[1]


def reader(srv, email="ada@example.org", name="Ada Reader", password="reader-pass-123") -> Client:
    """Someone invited, who followed the link."""
    c = Client(srv[0], srv[1])
    st, d = c.post("/api/register", {"token": invite(srv, email, name), "password": password, "name": name})
    assert st == 200, d
    return c


def admin_client(srv) -> Client:
    """The admin account is made from the command line, never by registering."""
    capi.main(["passwd", "--db", srv[2].store.path, ADMIN, "--name", "Site Admin", "--password", "admin-pass-123"])
    c = Client(srv[0], srv[1])
    st, d = c.post("/api/login", {"email": ADMIN, "password": "admin-pass-123"})
    assert st == 200 and d["user"]["admin"] is True, d
    return c


def test_health_and_anonymous_me(srv):
    c = Client(srv[0], srv[1])
    assert c.get("/api/health") == (200, {"ok": True})
    assert c.get("/api/me") == (200, {"user": None})
    st, d = c.get("/api/threads?page=/")
    assert st == 401 and "sign in" in d["error"]
    assert c.get("/api/nothing")[0] == 404


def test_registration_is_by_invitation_only(srv):
    c = Client(srv[0], srv[1])
    # no token, an empty one, somebody's guess: no account and no cookie
    assert c.post("/api/register", {"email": "a@b.co", "password": "reader-pass-123", "name": "A B"})[0] == 400
    assert c.post("/api/register", {"token": "", "password": "reader-pass-123", "name": "A B"})[0] == 400
    assert c.post("/api/register", {"token": "a-made-up-token", "password": "reader-pass-123", "name": "A B"})[0] == 404
    assert c.cookie is None
    token = invite(srv, "a@b.co", "A B")
    # whoever holds the link may read who it is for, before they have any account
    st, d = c.get("/api/invite?token=" + token)
    assert st == 200 and d["invite"]["email"] == "a@b.co" and d["invite"]["expired"] is False
    # the name and the password are checked as before
    assert c.post("/api/register", {"token": token, "password": "short", "name": "A B"})[0] == 400
    assert c.post("/api/register", {"token": token, "password": "reader-pass-123", "name": "A"})[0] == 400
    assert c.cookie is None
    # the token, not the browser, decides the address: an `email` sent along is ignored
    st, d = c.post("/api/register", {"token": token, "password": "reader-pass-123",
                                     "name": "  A   B ", "email": "someone@else.org"})
    assert st == 200 and d["user"]["email"] == "a@b.co" and d["user"]["name"] == "A B" and d["user"]["admin"] is False
    assert c.cookie
    # and it is spent: the same link makes no second account
    c2 = Client(srv[0], srv[1])
    assert c2.post("/api/register", {"token": token, "password": "reader-pass-123", "name": "Some One"})[0] == 409
    assert c2.get("/api/invite?token=" + token)[0] == 409
    assert c.get("/api/me")[1]["user"]["name"] == "A B"


def test_an_invitation_expires(srv):
    token = invite(srv, "late@example.org", "Too Late")
    with srv[2].store.connect() as c:
        c.execute("UPDATE invites SET expires=? WHERE email=?", (time.time() - 1, "late@example.org"))
    cl = Client(srv[0], srv[1])
    assert cl.get("/api/invite?token=" + token)[0] == 410
    assert cl.post("/api/register", {"token": token, "password": "reader-pass-123", "name": "Too Late"})[0] == 410
    assert cl.cookie is None


def test_the_admin_invites_and_the_link_makes_the_account(srv):
    ada = reader(srv)
    assert ada.get("/api/admin/people")[0] == 403           # a reader invites nobody
    assert ada.post("/api/admin/invites", {"email": "bob@example.org"})[0] == 403
    adm = admin_client(srv)
    st, d = adm.post("/api/admin/invites", {"email": "Bob@Example.ORG", "name": "Bob Other", "note": "the rules page"})
    assert st == 200 and d["invite"]["email"] == "bob@example.org"
    # no relay is configured here, so nothing was sent and the link is the answer
    assert d["sent"] is False and d["mail_error"] and "/?invite=" in d["link"]
    token = d["link"].split("invite=")[1]
    bob = Client(srv[0], srv[1])
    assert bob.get("/api/invite?token=" + token)[1]["invite"]["name"] == "Bob Other"
    st, d = bob.post("/api/register", {"token": token, "password": "other-pass-123", "name": "Bob Other"})
    assert st == 200 and d["user"]["email"] == "bob@example.org"
    # the panel: both accounts, and the invitation now spent
    st, d = adm.get("/api/admin/people")
    assert st == 200 and {u["email"] for u in d["users"]} == {ADMIN, "ada@example.org", "bob@example.org"}
    assert d["mail"] is False
    inv = {i["email"]: i for i in d["invites"]}
    assert set(inv) == {"ada@example.org", "bob@example.org"}
    assert inv["bob@example.org"]["accepted"] and inv["bob@example.org"]["note"] == "the rules page"
    # an address that already has an account is not invited, and a spent invitation stays
    assert adm.post("/api/admin/invites", {"email": "bob@example.org"})[0] == 409
    assert adm.delete("/api/admin/invites/%d" % inv["bob@example.org"]["id"])[0] == 409
    # one withdrawn before it is used stops working
    carol = adm.post("/api/admin/invites", {"email": "carol@example.org"})[1]
    assert adm.delete("/api/admin/invites/%d" % carol["invite"]["id"])[0] == 200
    assert Client(srv[0], srv[1]).get("/api/invite?token=" + carol["link"].split("invite=")[1])[0] == 404
    # inviting an address again replaces its link rather than adding a second one
    one = adm.post("/api/admin/invites", {"email": "dave@example.org"})[1]["link"]
    two = adm.post("/api/admin/invites", {"email": "dave@example.org"})[1]["link"]
    assert one != two
    assert Client(srv[0], srv[1]).get("/api/invite?token=" + one.split("invite=")[1])[0] == 404
    assert Client(srv[0], srv[1]).get("/api/invite?token=" + two.split("invite=")[1])[0] == 200
    assert [i["email"] for i in adm.get("/api/admin/people")[1]["invites"]].count("dave@example.org") == 1


def test_an_account_can_be_closed_and_opened_again(srv):
    ada = reader(srv)
    adm = admin_client(srv)
    who = {u["email"]: u["id"] for u in adm.get("/api/admin/people")[1]["users"]}
    assert ada.post("/api/admin/users/%d/access" % who["ada@example.org"], {"allow": False})[0] == 403
    assert adm.post("/api/admin/users/%d/access" % who["ada@example.org"], {"allow": "no"})[0] == 400
    assert adm.post("/api/admin/users/%d/access" % who[ADMIN], {"allow": False})[0] == 403   # not his own
    assert adm.post("/api/admin/users/999/access", {"allow": True})[0] == 404
    st, d = adm.post("/api/admin/users/%d/access" % who["ada@example.org"], {"allow": False})
    assert st == 200 and d["user"]["access"] is False
    assert ada.get("/api/me")[1]["user"] is None                  # her session went with it
    st, d = ada.post("/api/login", {"email": "ada@example.org", "password": "reader-pass-123"})
    assert st == 403 and "closed" in d["error"]
    assert adm.post("/api/admin/users/%d/access" % who["ada@example.org"], {"allow": True})[0] == 200
    assert ada.post("/api/login", {"email": "ada@example.org", "password": "reader-pass-123"})[0] == 200


def test_an_account_made_before_invitations_existed_still_works(srv):
    """The column was added to a database that already had readers in it: they keep their
    accounts, and are closed one at a time rather than all at once."""
    store = srv[2].store
    with store.connect() as c:
        c.execute("INSERT INTO users(email,name,pw,color,created) VALUES(?,?,?,?,?)",
                  ("old@example.org", "Old Hand", capi.hash_password("older-pass-123"), 3, time.time()))
    c = Client(srv[0], srv[1])
    assert c.post("/api/login", {"email": "old@example.org", "password": "older-pass-123"})[0] == 200
    assert c.get("/api/me")[1]["user"]["access"] is True


def test_the_invitation_mail_says_what_it_is(srv):
    adm = admin_client(srv)
    d = adm.post("/api/admin/invites", {"email": "eve@example.org", "name": "Eve Reader",
                                        "note": "please look at R19"})[1]
    subject, body = capi.invite_mail("https://qccd.academy", d["link"], "Site Admin",
                                     d["invite"]["expires"], "please look at R19")
    assert subject == "You are invited to qccd.academy"
    assert d["link"] in body and "Site Admin" in body and "please look at R19" in body
    # with no relay configured nothing is sent, and a relay that is not there is a
    # refusal the admin reads, not a crash
    assert not capi.Mailer().ready()
    with pytest.raises(capi.HttpError) as e:
        capi.Mailer().send("eve@example.org", subject, body)
    assert e.value.status == 503
    dead = capi.Mailer(host="127.0.0.1", port=9, sender="site@qccd.academy", mode="plain", timeout=3)
    assert dead.ready()
    with pytest.raises(capi.HttpError) as e:
        dead.send("eve@example.org", subject, body)
    assert e.value.status == 502


def test_the_invitation_goes_out_through_the_relay(srv):
    relay = FakeSMTP()
    relay.start()
    adm = admin_client(srv)
    srv[2].mailer = capi.Mailer(host="127.0.0.1", port=relay.port, sender="qccd@example.org",
                                mode="plain", timeout=10)
    st, d = adm.post("/api/admin/invites", {"email": "frank@example.org", "name": "Frank Reader"})
    assert st == 200 and d["sent"] is True and d["mail_error"] is None
    relay.join(timeout=10)
    assert relay.rcpt and "frank@example.org" in relay.rcpt[0]
    assert d["link"] in relay.message, relay.message
    assert "Subject: You are invited to qccd.academy" in relay.message
    assert "From: QCCD studio <qccd@example.org>" in relay.message
    assert "Reply-To: " + ADMIN in relay.message
    assert adm.get("/api/admin/people")[1]["invites"][0]["sent"]
    # and the link in that message is the one that makes the account
    frank = Client(srv[0], srv[1])
    token = d["link"].split("invite=")[1]
    assert frank.post("/api/register", {"token": token, "password": "frank-pass-123", "name": "Frank Reader"})[0] == 200


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
    adm = admin_client(srv)
    assert adm.get("/api/me")[1]["user"]["name"] == "Site Admin"
    st, d = adm.get("/api/admin/threads")
    assert st == 200 and [t["page"] for t in d["threads"]] == ["/q/", "/p/"]  # newest first
    st, d = adm.post("/api/threads/%d/comments" % th["id"], {"text": "noted"})
    assert st == 200
    assert adm.delete("/api/comments/%d" % th["comments"][0]["id"]) == (200, {"thread": th["id"], "gone": False})
    assert adm.delete("/api/threads/%d" % th["id"])[0] == 200
    assert [t["page"] for t in adm.get("/api/admin/threads")[1]["threads"]] == ["/q/"]
    # nobody is invited to the admin's own address either
    assert adm.post("/api/admin/invites", {"email": ADMIN})[0] == 409
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
    # registering counts separately, and an invited account can still be made
    token = invite(srv, "new@example.org", "New One")
    assert c.post("/api/register", {"token": token, "password": "reader-pass-123", "name": "New One"})[0] == 200


def test_the_command_line_invites_and_closes(srv, capsys, tmp_path: Path):
    db = srv[2].store.path
    assert capi.main(["invite", "--db", db, "Grace@example.org", "--name", "Grace Hopper",
                      "--site", "https://qccd.academy/"]) == 0
    link = capsys.readouterr().out.strip().splitlines()[-1]
    assert link.startswith("https://qccd.academy/?invite=")
    c = Client(srv[0], srv[1])
    st, d = c.post("/api/register", {"token": link.split("invite=")[1],
                                     "password": "grace-pass-123", "name": "Grace Hopper"})
    assert st == 200 and d["user"]["email"] == "grace@example.org"
    assert capi.main(["invites", "--db", db]) == 0
    assert "accepted" in capsys.readouterr().out
    # closing her account from the command line signs her out of the one she had
    assert capi.main(["access", "--db", db, "grace@example.org", "--revoke"]) == 0
    assert "closed" in capsys.readouterr().out
    assert c.get("/api/me")[1]["user"] is None
    assert c.post("/api/login", {"email": "grace@example.org", "password": "grace-pass-123"})[0] == 403
    assert capi.main(["access", "--db", db, "grace@example.org", "--allow"]) == 0
    assert c.post("/api/login", {"email": "grace@example.org", "password": "grace-pass-123"})[0] == 200
    assert capi.main(["users", "--db", db]) == 0
    assert "grace@example.org" in capsys.readouterr().out
    # a name that is not there, and an invitation to somebody who already joined
    assert capi.main(["access", "--db", db, "nobody@example.org", "--allow"]) == 1
    assert capi.main(["invite", "--db", db, "grace@example.org"]) == 1
    assert "already has an account" in capsys.readouterr().err


def test_password_hashes_are_salted_scrypt():
    a, b = capi.hash_password("reader-pass-123"), capi.hash_password("reader-pass-123")
    assert a != b and a.startswith("scrypt$16384$8$1$")
    assert capi.check_password("reader-pass-123", a) and not capi.check_password("reader-pass-124", a)
    assert not capi.check_password("x", "garbage")
