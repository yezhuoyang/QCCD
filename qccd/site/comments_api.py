#!/usr/bin/env python3
"""Accounts and comments for qccd.academy: one file, standard library only.

    python3 comments_api.py serve  --db comments.db --port 8200 --admins a@b.c[,d@e.f]
                                   [--host 127.0.0.1] [--secure] [--proxied]
    python3 comments_api.py passwd --db comments.db EMAIL [--name NAME] [--password PW]
    python3 comments_api.py users  --db comments.db
    python3 comments_api.py threads --db comments.db

The website is static files; this is the one process behind it, reached through nginx at
`/api/`.  It keeps users (email, display name, scrypt password hash, a colour), sessions
(a random token in an HttpOnly cookie, hashed at rest), and comment threads: a thread is
pinned to one page at one anchor (the element and the fraction of it the reader clicked,
as JSON the browser wrote), and holds one or more comments.  Only a signed-in reader sees
any of it; a reader deletes their own comments; an admin (`--admins`) deletes anything.

Copy this file anywhere on the server and run it under systemd as an unprivileged user;
it imports nothing outside the standard library and needs no install.

    GET    /api/health
    GET    /api/me                          -> {"user": {...} | null}
    POST   /api/register  {email, password, name}
    POST   /api/login     {email, password}
    POST   /api/logout
    POST   /api/password  {old, new}
    GET    /api/threads?page=/physics/      -> {"threads": [...]}          (signed in)
    POST   /api/threads   {page, anchor, text}                           (signed in)
    POST   /api/threads/<id>/comments  {text}                            (signed in)
    DELETE /api/threads/<id>                       (the thread's author, or an admin)
    DELETE /api/comments/<id>                      (the comment's author, or an admin)
    GET    /api/admin/threads                      (admin: every thread, newest first)

Errors are `{"error": "..."}` with the status that names them: 400 malformed, 401 not
signed in, 403 not allowed (also a cross-site Origin), 404, 409 the email is taken,
413 too large, 429 too many sign-in attempts.  Responses are never cached.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import http.cookies
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

SESSION_DAYS = 30
COOKIE = "qccd_session"
MAX_BODY = 64 * 1024
MAX_TEXT = 4000
MAX_ANCHOR = 4000
MAX_PAGE = 300
MAX_NAME = 60
MIN_PASSWORD = 8
N_COLOURS = 12
ATTEMPTS, ATTEMPT_WINDOW = 30, 15 * 60          # sign-in and registration attempts per IP
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL, pw TEXT NOT NULL,
  color INTEGER NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created REAL NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS threads(
  id INTEGER PRIMARY KEY, page TEXT NOT NULL, anchor TEXT NOT NULL,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS comments(
  id INTEGER PRIMARY KEY, thread_id INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, text TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS threads_page ON threads(page);
CREATE INDEX IF NOT EXISTS comments_thread ON comments(thread_id);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
"""


# ------------------------------------------------------------------------- passwords

def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${h.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, h = stored.split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(pw.encode("utf-8"), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(got.hex(), h)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


# ------------------------------------------------------------------------- the store

class Store:
    """SQLite behind one lock for writes; every call opens its own connection, so the
    threaded server never shares one across threads."""

    def __init__(self, path: str, admins: set[str]):
        self.path = path
        self.admins = {a.strip().lower() for a in admins if a.strip()}
        self.lock = threading.Lock()
        with self.connect() as c:
            c.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        return c

    # -- users
    def user_json(self, row, me: bool = False) -> dict:
        d = {"id": row["id"], "name": row["name"], "color": row["color"]}
        if me:
            d["email"] = row["email"]
            d["admin"] = row["email"].lower() in self.admins
        return d

    def create_user(self, email: str, name: str, password: str) -> dict:
        email = email.strip().lower()
        with self.lock, self.connect() as c:
            if c.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                raise HttpError(409, "an account with this email already exists")
            n = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            cur = c.execute("INSERT INTO users(email,name,pw,color,created) VALUES(?,?,?,?,?)",
                            (email, name.strip(), hash_password(password), n % N_COLOURS, time.time()))
            return self.user_json(c.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone(), me=True)

    def set_password(self, email: str, password: str, name: str | None = None) -> dict:
        """The command line's `passwd`: set or reset, creating the account if needed."""
        email = email.strip().lower()
        with self.lock, self.connect() as c:
            row = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            if row is None:
                n = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                c.execute("INSERT INTO users(email,name,pw,color,created) VALUES(?,?,?,?,?)",
                          (email, (name or email.split("@")[0]).strip(), hash_password(password), n % N_COLOURS, time.time()))
            else:
                c.execute("UPDATE users SET pw=? WHERE id=?", (hash_password(password), row["id"]))
                if name:
                    c.execute("UPDATE users SET name=? WHERE id=?", (name.strip(), row["id"]))
                c.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
            return self.user_json(c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone(), me=True)

    def login(self, email: str, password: str) -> tuple[dict, str]:
        email = email.strip().lower()
        with self.connect() as c:
            row = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if row is None or not check_password(password, row["pw"]):
            raise HttpError(401, "wrong email or password")
        return self.user_json(row, me=True), self.open_session(row["id"])

    def open_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.lock, self.connect() as c:
            c.execute("DELETE FROM sessions WHERE expires < ?", (now,))
            c.execute("INSERT INTO sessions(token_hash,user_id,created,expires) VALUES(?,?,?,?)",
                      (token_hash(token), user_id, now, now + SESSION_DAYS * 86400))
        return token

    def close_session(self, token: str) -> None:
        with self.lock, self.connect() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(token),))

    def user_of(self, token: str | None):
        if not token:
            return None
        with self.connect() as c:
            return c.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id "
                             "WHERE s.token_hash=? AND s.expires > ?", (token_hash(token), time.time())).fetchone()

    def change_password(self, user, old: str, new: str, keep_token: str) -> None:
        if not check_password(old, user["pw"]):
            raise HttpError(403, "the current password is wrong")
        with self.lock, self.connect() as c:
            c.execute("UPDATE users SET pw=? WHERE id=?", (hash_password(new), user["id"]))
            # every other session of this account ends; the one that changed it stays
            c.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (user["id"], token_hash(keep_token)))

    def users(self) -> list[dict]:
        with self.connect() as c:
            return [dict(self.user_json(r, me=True), created=r["created"]) for r in c.execute("SELECT * FROM users ORDER BY id")]

    # -- threads
    def thread_rows(self, c, where: str, args: tuple) -> list[dict]:
        out = []
        for t in c.execute(f"SELECT t.*, u.name, u.color, u.email FROM threads t JOIN users u ON u.id=t.user_id {where} ", args):
            cs = [{"id": r["id"], "text": r["text"], "created": r["created"],
                   "user": {"id": r["user_id"], "name": r["name"], "color": r["color"]}}
                  for r in c.execute("SELECT c.*, u.name, u.color FROM comments c JOIN users u ON u.id=c.user_id "
                                     "WHERE c.thread_id=? ORDER BY c.id", (t["id"],))]
            out.append({"id": t["id"], "page": t["page"], "anchor": json.loads(t["anchor"]), "created": t["created"],
                        "user": {"id": t["user_id"], "name": t["name"], "color": t["color"]}, "comments": cs})
        return out

    def threads(self, page: str) -> list[dict]:
        with self.connect() as c:
            return self.thread_rows(c, "WHERE t.page=? ORDER BY t.id", (page,))

    def all_threads(self, limit: int = 500) -> list[dict]:
        with self.connect() as c:
            return self.thread_rows(c, "ORDER BY t.id DESC LIMIT ?", (limit,))

    def thread(self, tid: int) -> dict:
        with self.connect() as c:
            rows = self.thread_rows(c, "WHERE t.id=?", (tid,))
        if not rows:
            raise HttpError(404, "no such thread")
        return rows[0]

    def create_thread(self, user, page: str, anchor: dict, text: str) -> dict:
        now = time.time()
        with self.lock, self.connect() as c:
            cur = c.execute("INSERT INTO threads(page,anchor,user_id,created) VALUES(?,?,?,?)",
                            (page, json.dumps(anchor, separators=(",", ":")), user["id"], now))
            c.execute("INSERT INTO comments(thread_id,user_id,text,created) VALUES(?,?,?,?)", (cur.lastrowid, user["id"], text, now))
            tid = cur.lastrowid
        return self.thread(tid)

    def create_comment(self, user, tid: int, text: str) -> dict:
        with self.lock, self.connect() as c:
            if not c.execute("SELECT 1 FROM threads WHERE id=?", (tid,)).fetchone():
                raise HttpError(404, "no such thread")
            cur = c.execute("INSERT INTO comments(thread_id,user_id,text,created) VALUES(?,?,?,?)",
                            (tid, user["id"], text, time.time()))
            r = c.execute("SELECT c.*, u.name, u.color FROM comments c JOIN users u ON u.id=c.user_id WHERE c.id=?",
                          (cur.lastrowid,)).fetchone()
        return {"id": r["id"], "text": r["text"], "created": r["created"],
                "user": {"id": r["user_id"], "name": r["name"], "color": r["color"]}}

    def is_admin(self, user) -> bool:
        return user is not None and user["email"].lower() in self.admins

    def delete_thread(self, user, tid: int) -> None:
        with self.lock, self.connect() as c:
            t = c.execute("SELECT * FROM threads WHERE id=?", (tid,)).fetchone()
            if t is None:
                raise HttpError(404, "no such thread")
            if t["user_id"] != user["id"] and not self.is_admin(user):
                raise HttpError(403, "only the thread's author or an admin can delete it")
            c.execute("DELETE FROM threads WHERE id=?", (tid,))

    def delete_comment(self, user, cid: int) -> dict:
        """Deletes one comment; a thread whose last comment goes is deleted with it.
        Returns {"thread": id, "gone": bool}."""
        with self.lock, self.connect() as c:
            r = c.execute("SELECT * FROM comments WHERE id=?", (cid,)).fetchone()
            if r is None:
                raise HttpError(404, "no such comment")
            if r["user_id"] != user["id"] and not self.is_admin(user):
                raise HttpError(403, "only the comment's author or an admin can delete it")
            c.execute("DELETE FROM comments WHERE id=?", (cid,))
            left = c.execute("SELECT COUNT(*) FROM comments WHERE thread_id=?", (r["thread_id"],)).fetchone()[0]
            if left == 0:
                c.execute("DELETE FROM threads WHERE id=?", (r["thread_id"],))
            return {"thread": r["thread_id"], "gone": left == 0}


# ------------------------------------------------------------------------- validation

def norm_page(page) -> str:
    if not isinstance(page, str) or not page.startswith("/") or len(page) > MAX_PAGE or any(ch in page for ch in "\r\n\t"):
        raise HttpError(400, "page must be a site path")
    if page.endswith("/index.html"):
        page = page[:-len("index.html")]
    return page


def norm_text(text) -> str:
    if not isinstance(text, str):
        raise HttpError(400, "text must be a string")
    text = text.strip()
    if not text:
        raise HttpError(400, "the comment is empty")
    if len(text) > MAX_TEXT:
        raise HttpError(400, f"a comment is at most {MAX_TEXT} characters")
    return text


def norm_email(email) -> str:
    if not isinstance(email, str) or len(email) > 200 or not EMAIL_RE.match(email.strip()):
        raise HttpError(400, "that is not an email address")
    return email.strip().lower()


def norm_password(pw) -> str:
    if not isinstance(pw, str) or len(pw) < MIN_PASSWORD:
        raise HttpError(400, f"the password needs at least {MIN_PASSWORD} characters")
    if len(pw) > 200:
        raise HttpError(400, "the password is too long")
    return pw


def norm_name(name) -> str:
    if not isinstance(name, str):
        raise HttpError(400, "name must be a string")
    name = " ".join(name.split())
    if len(name) < 2 or len(name) > MAX_NAME:
        raise HttpError(400, f"the name is 2 to {MAX_NAME} characters")
    return name


def norm_anchor(anchor) -> dict:
    if not isinstance(anchor, dict):
        raise HttpError(400, "anchor must be an object")
    if len(json.dumps(anchor)) > MAX_ANCHOR:
        raise HttpError(400, "the anchor is too large")
    return anchor


# ------------------------------------------------------------------------- the server

class RateLimit:
    def __init__(self):
        self.hits: dict[str, deque] = {}
        self.lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.time()
        with self.lock:
            q = self.hits.setdefault(key, deque())
            while q and q[0] < now - ATTEMPT_WINDOW:
                q.popleft()
            if len(q) >= ATTEMPTS:
                raise HttpError(429, "too many attempts; try again later")
            q.append(now)


class App:
    def __init__(self, store: Store, secure: bool, proxied: bool):
        self.store, self.secure, self.proxied = store, secure, proxied
        self.limit = RateLimit()
        self.routes = [
            ("GET", re.compile(r"^/api/health$"), self.health),
            ("GET", re.compile(r"^/api/me$"), self.me),
            ("POST", re.compile(r"^/api/register$"), self.register),
            ("POST", re.compile(r"^/api/login$"), self.login),
            ("POST", re.compile(r"^/api/logout$"), self.logout),
            ("POST", re.compile(r"^/api/password$"), self.password),
            ("GET", re.compile(r"^/api/threads$"), self.threads),
            ("POST", re.compile(r"^/api/threads$"), self.thread_create),
            ("POST", re.compile(r"^/api/threads/(\d+)/comments$"), self.comment_create),
            ("DELETE", re.compile(r"^/api/threads/(\d+)$"), self.thread_delete),
            ("DELETE", re.compile(r"^/api/comments/(\d+)$"), self.comment_delete),
            ("GET", re.compile(r"^/api/admin/threads$"), self.admin_threads),
        ]

    # each handler: (req, *groups) -> (status, body dict, extra headers list)
    def health(self, req):
        return 200, {"ok": True}, []

    def me(self, req):
        return 200, {"user": self.store.user_json(req.user, me=True) if req.user else None}, []

    def register(self, req):
        self.limit.check("reg:" + req.ip)
        b = req.json()
        email, pw, name = norm_email(b.get("email")), norm_password(b.get("password")), norm_name(b.get("name"))
        user = self.store.create_user(email, name, pw)
        token = self.store.open_session(user["id"])
        return 200, {"user": user}, [self.cookie(token)]

    def login(self, req):
        self.limit.check("login:" + req.ip)
        b = req.json()
        email, pw = norm_email(b.get("email")), b.get("password")
        if not isinstance(pw, str):
            raise HttpError(400, "password must be a string")
        user, token = self.store.login(email, pw)
        return 200, {"user": user}, [self.cookie(token)]

    def logout(self, req):
        if req.token:
            self.store.close_session(req.token)
        return 200, {"ok": True}, [self.cookie("", clear=True)]

    def password(self, req):
        u = req.require_user()
        b = req.json()
        old, new = b.get("old"), norm_password(b.get("new"))
        if not isinstance(old, str):
            raise HttpError(400, "old must be a string")
        self.store.change_password(u, old, new, req.token)
        return 200, {"ok": True}, []

    def threads(self, req):
        req.require_user()
        page = norm_page((req.query.get("page") or [""])[0])
        return 200, {"threads": self.store.threads(page)}, []

    def thread_create(self, req):
        u = req.require_user()
        b = req.json()
        t = self.store.create_thread(u, norm_page(b.get("page")), norm_anchor(b.get("anchor")), norm_text(b.get("text")))
        return 200, {"thread": t}, []

    def comment_create(self, req, tid):
        u = req.require_user()
        b = req.json()
        return 200, {"comment": self.store.create_comment(u, int(tid), norm_text(b.get("text")))}, []

    def thread_delete(self, req, tid):
        self.store.delete_thread(req.require_user(), int(tid))
        return 200, {"ok": True}, []

    def comment_delete(self, req, cid):
        return 200, self.store.delete_comment(req.require_user(), int(cid)), []

    def admin_threads(self, req):
        u = req.require_user()
        if not self.store.is_admin(u):
            raise HttpError(403, "admins only")
        return 200, {"threads": self.store.all_threads()}, []

    def cookie(self, token: str, clear: bool = False) -> tuple[str, str]:
        parts = [f"{COOKIE}={token}", "Path=/", "HttpOnly", "SameSite=Lax",
                 "Max-Age=0" if clear else f"Max-Age={SESSION_DAYS * 86400}"]
        if self.secure:
            parts.append("Secure")
        return "Set-Cookie", "; ".join(parts)


class Request:
    """What one handler sees: the parsed cookie, the user it names, the query, the body."""

    def __init__(self, h: "Handler", app: App):
        self.h, self.app = h, app
        u = urlsplit(h.path)
        self.path, self.query = u.path, parse_qs(u.query)
        self.token = None
        raw = h.headers.get("Cookie")
        if raw:
            try:
                c = http.cookies.SimpleCookie()
                c.load(raw)
                if COOKIE in c:
                    self.token = c[COOKIE].value or None
            except http.cookies.CookieError:
                pass
        self.user = app.store.user_of(self.token)
        self.ip = h.client_address[0]
        if app.proxied:
            self.ip = (h.headers.get("X-Real-IP") or (h.headers.get("X-Forwarded-For") or "").split(",")[0].strip() or self.ip)
        self._body = None
        self.raw = b""

    def require_user(self):
        if self.user is None:
            raise HttpError(401, "sign in first")
        return self.user

    def read_body(self) -> None:
        """Reads the whole body up front (or refuses it), so a refusal raised before a
        handler looked at it never leaves unread bytes on a keep-alive connection."""
        n = int(self.h.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise HttpError(413, "the request is too large")
        self.raw = self.h.rfile.read(n) if n > 0 else b""

    def json(self) -> dict:
        if self._body is None:
            if not self.raw:
                self._body = {}
            else:
                ctype = (self.h.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype != "application/json":
                    raise HttpError(400, "send JSON")
                try:
                    self._body = json.loads(self.raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    raise HttpError(400, "malformed JSON")
                if not isinstance(self._body, dict):
                    raise HttpError(400, "send a JSON object")
        return self._body


class Handler(BaseHTTPRequestHandler):
    app: App = None  # set by serve()
    protocol_version = "HTTP/1.1"
    server_version = "qccd-comments/1"
    sys_version = ""

    def log_message(self, fmt, *args):  # one compact line, no bodies
        sys.stdout.write("%s %s\n" % (self.address_string(), fmt % args))
        sys.stdout.flush()

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def do_OPTIONS(self):
        self.reply(204, None, [("Allow", "GET, POST, DELETE, OPTIONS")])

    def same_site(self) -> bool:
        """A browser sends Origin (or Referer) with every fetch that could carry the
        cookie; it must name this host.  A client that sends neither is not a browser
        acting on a stranger's cookie."""
        host = (self.headers.get("Host") or "").lower()
        origin = self.headers.get("Origin") or self.headers.get("Referer")
        if not origin:
            return True
        return urlsplit(origin).netloc.lower() == host

    def dispatch(self, method: str):
        try:
            req = Request(self, self.app)
            if method != "GET":
                req.read_body()
                if not self.same_site():
                    raise HttpError(403, "cross-site request refused")
            for m, rx, fn in self.app.routes:
                g = rx.match(req.path)
                if g and m == method:
                    status, body, extra = fn(req, *g.groups())
                    return self.reply(status, body, extra)
            if any(rx.match(req.path) for _, rx, _ in self.app.routes):
                raise HttpError(405, "method not allowed")
            raise HttpError(404, "no such endpoint")
        except HttpError as e:
            self.reply(e.status, {"error": e.message}, [])
        except Exception as e:  # noqa: BLE001 -- the server must not die on one request
            sys.stdout.write("error: %s: %s\n" % (type(e).__name__, e))
            sys.stdout.flush()
            self.reply(500, {"error": "server error"}, [])

    def reply(self, status: int, body, extra):
        data = b"" if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if data:
            self.wfile.write(data)


def serve(db: str, host: str, port: int, admins: set[str], secure: bool, proxied: bool) -> None:
    Handler.app = App(Store(db, admins), secure=secure, proxied=proxied)
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    print(f"qccd comments api on http://{host}:{srv.server_address[1]}/api/  db={db}  admins={sorted(Handler.app.store.admins)}",
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


# ------------------------------------------------------------------------- command line

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the API")
    s.add_argument("--db", required=True)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8200)
    s.add_argument("--admins", default=os.environ.get("QCCD_ADMINS", ""), help="comma-separated admin emails")
    s.add_argument("--secure", action="store_true", help="mark the session cookie Secure (behind HTTPS)")
    s.add_argument("--proxied", action="store_true", help="trust X-Real-IP / X-Forwarded-For (behind nginx)")
    p = sub.add_parser("passwd", help="set an account's password, creating the account if needed")
    p.add_argument("--db", required=True)
    p.add_argument("email")
    p.add_argument("--name")
    p.add_argument("--password", help="otherwise read from stdin or asked for")
    u = sub.add_parser("users", help="list the accounts")
    u.add_argument("--db", required=True)
    t = sub.add_parser("threads", help="list every thread")
    t.add_argument("--db", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "serve":
        serve(a.db, a.host, a.port, set(a.admins.split(",")), a.secure, a.proxied)
        return 0
    if a.cmd == "passwd":
        pw = a.password
        if pw is None:
            pw = sys.stdin.readline().rstrip("\r\n") if not sys.stdin.isatty() else getpass.getpass("new password: ")
        norm_password(pw)
        user = Store(a.db, set()).set_password(norm_email(a.email), pw, a.name)
        print(f"password set for {user['email']} ({user['name']})")
        return 0
    store = Store(a.db, set())
    if a.cmd == "users":
        for x in store.users():
            print(f"{x['id']:4d}  {x['email']:40s} {x['name']}")
        return 0
    if a.cmd == "threads":
        for th in store.all_threads(10000):
            first = th["comments"][0]["text"] if th["comments"] else ""
            print(f"{th['id']:4d}  {th['page']:40s} {th['user']['name']:20s} {len(th['comments'])} msg  {first[:60]!r}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
