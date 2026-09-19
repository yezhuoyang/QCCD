#!/usr/bin/env python3
"""Accounts and comments for qccd.academy: one file, standard library only.

    python3 comments_api.py serve   --db comments.db --port 8200 --admins a@b.c[,d@e.f]
                                    [--host 127.0.0.1] [--secure] [--proxied] [mail options]
    python3 comments_api.py passwd  --db comments.db EMAIL [--name NAME] [--password PW]
    python3 comments_api.py invite  --db comments.db EMAIL [--name NAME] [--note ...] [--send]
    python3 comments_api.py access  --db comments.db EMAIL (--allow | --revoke)
    python3 comments_api.py users   --db comments.db
    python3 comments_api.py invites --db comments.db
    python3 comments_api.py threads --db comments.db

The website is static files; this is the one process behind it, reached through nginx at
`/api/`.  It keeps users (email, display name, scrypt password hash, a colour), sessions
(a random token in an HttpOnly cookie, hashed at rest), and comment threads: a thread is
pinned to one page at one anchor (the element and the fraction of it the reader clicked,
as JSON the browser wrote), and holds one or more comments.  Only a signed-in reader sees
any of it; a reader deletes their own comments; an admin (`--admins`) deletes anything.

Comments are also counted.  `credits` is an append-only ledger with one row per comment,
written when it is posted and marked rather than removed when the comment is deleted, so a
comment that has been dealt with and taken off the page still counts for whoever noticed the
thing.  `/api/credit` is what the Credit page reads.

The site is by invitation.  Nobody registers unasked: an admin names an email, the server
mails that address a one-time link carrying a token it keeps only as a hash, and following
the link is the only way to make an account -- with the address the admin named, which the
person registering cannot change.  An invitation expires (INVITE_DAYS), is spent the moment
it makes an account, and can be withdrawn before then.  An account itself closes the same
way: `access` 0 refuses the sign-in and drops the sessions it already had, so removing
somebody does not wait for their cookie to run out.  Admins are always let in.

Copy this file anywhere on the server and run it under systemd as an unprivileged user;
it imports nothing outside the standard library and needs no install.

    GET    /api/health
    GET    /api/me                          -> {"user": {...} | null}
    GET    /api/invite?token=...            -> {"invite": {email, name, expires}}  (public)
    POST   /api/register  {token, password, name}
    POST   /api/login     {email, password}
    POST   /api/logout
    POST   /api/password  {old, new}
    GET    /api/credit                      -> {"people": [...], "items": [...]}  (signed in)
    GET    /api/threads?page=/physics/      -> {"threads": [...]}          (signed in)
    POST   /api/threads   {page, anchor, text}                           (signed in)
    POST   /api/threads/<id>/comments  {text}                            (signed in)
    POST   /api/threads/<id>/resolve  {resolved}   (signed in: mark it addressed, or reopen)
    DELETE /api/threads/<id>                       (the thread's author, or an admin)
    DELETE /api/comments/<id>                      (the comment's author, or an admin)
    GET    /api/admin/people                       (admin: every account and invitation)
    GET    /api/admin/threads                      (admin: every thread, newest first)
    POST   /api/admin/invites  {email, name, note, send}   (admin: invite, mail the link)
    DELETE /api/admin/invites/<id>                 (admin: withdraw an unused invitation)
    POST   /api/admin/users/<id>/access  {allow}   (admin: let an account in, or close it)

Errors are `{"error": "..."}` with the status that names them: 400 malformed, 401 not
signed in, 403 not allowed (also a cross-site Origin, and a closed account), 404, 409 the
email is taken or the invitation is spent, 410 the invitation expired, 413 too large,
429 too many sign-in attempts.  Responses are never cached.
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
import smtplib
import sqlite3
import ssl as ssl_mod
import sys
import threading
import time
from collections import deque
from email.message import EmailMessage
from email.utils import formataddr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

SESSION_DAYS = 30
INVITE_DAYS = 14
COOKIE = "qccd_session"
MAX_BODY = 64 * 1024
MAX_TEXT = 4000
MAX_ANCHOR = 4000
MAX_PAGE = 300
MAX_NAME = 60
MAX_NOTE = 500
MIN_PASSWORD = 8
N_COLOURS = 12
ATTEMPTS, ATTEMPT_WINDOW = 30, 15 * 60          # sign-in, registration, invite lookups per IP
DEFAULT_SITE = "https://qccd.academy"
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL, pw TEXT NOT NULL,
  color INTEGER NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS invites(
  id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL DEFAULT '',
  token_hash TEXT NOT NULL UNIQUE, note TEXT NOT NULL DEFAULT '',
  invited_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created REAL NOT NULL, expires REAL NOT NULL, sent REAL, accepted REAL,
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created REAL NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS threads(
  id INTEGER PRIMARY KEY, page TEXT NOT NULL, anchor TEXT NOT NULL,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS comments(
  id INTEGER PRIMARY KEY, thread_id INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, text TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS credits(
  id INTEGER PRIMARY KEY, comment_id INTEGER NOT NULL UNIQUE, thread_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL, name TEXT NOT NULL, page TEXT NOT NULL, text TEXT NOT NULL,
  created REAL NOT NULL, removed REAL);
CREATE INDEX IF NOT EXISTS threads_page ON threads(page);
CREATE INDEX IF NOT EXISTS credits_user ON credits(user_id);
CREATE INDEX IF NOT EXISTS comments_thread ON comments(thread_id);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS invites_token ON invites(token_hash);
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
            # a database written before invitations existed: everybody already in it keeps
            # their account, and is closed one at a time from the panel or `access`.
            if "access" not in {r["name"] for r in c.execute("PRAGMA table_info(users)")}:
                c.execute("ALTER TABLE users ADD COLUMN access INTEGER NOT NULL DEFAULT 1")
            # a note can be marked ADDRESSED instead of deleted: the reader who wrote it
            # asked how to say "this one is done" without losing what was said.  Null is
            # open, which is what every thread written before this was.
            tcols = {r["name"] for r in c.execute("PRAGMA table_info(threads)")}
            if "resolved" not in tcols:
                c.execute("ALTER TABLE threads ADD COLUMN resolved REAL")
                c.execute("ALTER TABLE threads ADD COLUMN resolved_by INTEGER")
            # every comment that exists earns its credit, including the ones posted before
            # this ledger did.  `OR IGNORE` on the unique comment_id makes it idempotent, so
            # it also catches anything written while an older copy of this file was running.
            c.execute("INSERT OR IGNORE INTO credits(comment_id,thread_id,user_id,name,page,text,created) "
                      "SELECT cm.id, cm.thread_id, cm.user_id, u.name, t.page, cm.text, cm.created "
                      "FROM comments cm JOIN threads t ON t.id=cm.thread_id JOIN users u ON u.id=cm.user_id")

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
            d["access"] = self.may_sign_in(row)
        return d

    def may_sign_in(self, row) -> bool:
        """An account is let in while it is open; an admin always is."""
        return bool(row["access"]) or row["email"].lower() in self.admins

    def set_access(self, user_id: int, allow: bool) -> dict:
        """Opens or closes one account.  Closing it drops the sessions it already had, so
        somebody removed is out now and not whenever their cookie expires."""
        with self.lock, self.connect() as c:
            row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if row is None:
                raise HttpError(404, "no such account")
            if not allow and row["email"].lower() in self.admins:
                raise HttpError(403, "an admin account cannot be closed from here")
            c.execute("UPDATE users SET access=? WHERE id=?", (1 if allow else 0, user_id))
            if not allow:
                c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            return self.user_json(c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone(), me=True)

    # -- invitations
    def invite_json(self, row, full: bool = False) -> dict:
        d = {"id": row["id"], "email": row["email"], "name": row["name"],
             "expires": row["expires"], "expired": row["expires"] < time.time()}
        if full:
            d.update(note=row["note"], created=row["created"], sent=row["sent"], accepted=row["accepted"])
        return d

    def _live_invite(self, c, token: str):
        """The row one token names, while it is still worth something."""
        if not isinstance(token, str) or not token.strip():
            raise HttpError(400, "that is not an invitation link")
        row = c.execute("SELECT * FROM invites WHERE token_hash=?", (token_hash(token.strip()),)).fetchone()
        if row is None:
            raise HttpError(404, "this invitation link is not valid")
        if row["accepted"]:
            raise HttpError(409, "this invitation has already been used")
        if row["expires"] < time.time():
            raise HttpError(410, "this invitation has expired; ask the site admin for another")
        return row

    def invite(self, token: str) -> dict:
        """What the registration form may know before anyone has an account: the address
        the admin named, and how long the link lasts."""
        with self.connect() as c:
            return self.invite_json(self._live_invite(c, token))

    def create_invite(self, admin, email: str, name: str = "", note: str = "") -> tuple[dict, str]:
        """Names one address.  Returns the invitation and the token that goes in the link;
        only its hash is stored, so a second copy of a link cannot be made from the
        database.  Inviting an address again replaces its invitation with a fresh one."""
        email, token, now = norm_email(email), secrets.token_urlsafe(32), time.time()
        with self.lock, self.connect() as c:
            if c.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                raise HttpError(409, "that email already has an account")
            c.execute("INSERT INTO invites(email,name,token_hash,note,invited_by,created,expires) "
                      "VALUES(?,?,?,?,?,?,?) ON CONFLICT(email) DO UPDATE SET "
                      "name=excluded.name, token_hash=excluded.token_hash, note=excluded.note, "
                      "invited_by=excluded.invited_by, created=excluded.created, expires=excluded.expires, sent=NULL",
                      (email, name, token_hash(token), note, admin["id"] if admin is not None else None,
                       now, now + INVITE_DAYS * 86400))
            row = c.execute("SELECT * FROM invites WHERE email=?", (email,)).fetchone()
        return self.invite_json(row, full=True), token

    def mark_sent(self, invite_id: int) -> None:
        with self.lock, self.connect() as c:
            c.execute("UPDATE invites SET sent=? WHERE id=?", (time.time(), invite_id))

    def revoke_invite(self, invite_id: int) -> None:
        with self.lock, self.connect() as c:
            row = c.execute("SELECT * FROM invites WHERE id=?", (invite_id,)).fetchone()
            if row is None:
                raise HttpError(404, "no such invitation")
            if row["accepted"]:
                raise HttpError(409, "that invitation was used; close the account instead")
            c.execute("DELETE FROM invites WHERE id=?", (invite_id,))

    def invites(self) -> list[dict]:
        with self.connect() as c:
            return [self.invite_json(r, full=True) for r in c.execute("SELECT * FROM invites ORDER BY id DESC")]

    def accept_invite(self, token: str, name: str, password: str) -> dict:
        """The only way an account is made from the web.  The token decides the address,
        so the person following the link cannot register as somebody else, and the check
        and the spending of it happen together."""
        with self.lock, self.connect() as c:
            inv = self._live_invite(c, token)
            if c.execute("SELECT 1 FROM users WHERE email=?", (inv["email"],)).fetchone():
                raise HttpError(409, "an account with this email already exists")
            n = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            cur = c.execute("INSERT INTO users(email,name,pw,color,created,access) VALUES(?,?,?,?,?,1)",
                            (inv["email"], name, hash_password(password), n % N_COLOURS, time.time()))
            c.execute("UPDATE invites SET accepted=?, user_id=? WHERE id=?", (time.time(), cur.lastrowid, inv["id"]))
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
        if not self.may_sign_in(row):
            raise HttpError(403, "this account is closed; ask the site admin for an invitation")
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
            row = c.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id "
                            "WHERE s.token_hash=? AND s.expires > ?", (token_hash(token), time.time())).fetchone()
        return row if row is not None and self.may_sign_in(row) else None

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
            done = None
            if t["resolved"]:
                by = c.execute("SELECT id, name, color FROM users WHERE id=?", (t["resolved_by"],)).fetchone()
                done = {"at": t["resolved"],
                        "by": {"id": by["id"], "name": by["name"], "color": by["color"]} if by else None}
            out.append({"id": t["id"], "page": t["page"], "anchor": json.loads(t["anchor"]), "created": t["created"],
                        "user": {"id": t["user_id"], "name": t["name"], "color": t["color"]},
                        "resolved": done, "comments": cs})
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

    def _credit(self, c, comment_id: int, thread_id: int, user, page: str, text: str, when: float) -> None:
        """The ledger: one row per comment, written the moment it is posted and never taken
        out.  Addressing a comment deletes it from the page it was pinned to; the credit for
        having noticed the thing stays here, which is the whole point of keeping it apart."""
        c.execute("INSERT OR IGNORE INTO credits(comment_id,thread_id,user_id,name,page,text,created) "
                  "VALUES(?,?,?,?,?,?,?)",
                  (comment_id, thread_id, user["id"], user["name"], page, text, when))

    def create_thread(self, user, page: str, anchor: dict, text: str) -> dict:
        now = time.time()
        with self.lock, self.connect() as c:
            cur = c.execute("INSERT INTO threads(page,anchor,user_id,created) VALUES(?,?,?,?)",
                            (page, json.dumps(anchor, separators=(",", ":")), user["id"], now))
            tid = cur.lastrowid
            cm = c.execute("INSERT INTO comments(thread_id,user_id,text,created) VALUES(?,?,?,?)", (tid, user["id"], text, now))
            self._credit(c, cm.lastrowid, tid, user, page, text, now)
        return self.thread(tid)

    def create_comment(self, user, tid: int, text: str) -> dict:
        with self.lock, self.connect() as c:
            t = c.execute("SELECT * FROM threads WHERE id=?", (tid,)).fetchone()
            if t is None:
                raise HttpError(404, "no such thread")
            now = time.time()
            cur = c.execute("INSERT INTO comments(thread_id,user_id,text,created) VALUES(?,?,?,?)",
                            (tid, user["id"], text, now))
            self._credit(c, cur.lastrowid, tid, user, t["page"], text, now)
            r = c.execute("SELECT c.*, u.name, u.color FROM comments c JOIN users u ON u.id=c.user_id WHERE c.id=?",
                          (cur.lastrowid,)).fetchone()
        return {"id": r["id"], "text": r["text"], "created": r["created"],
                "user": {"id": r["user_id"], "name": r["name"], "color": r["color"]}}

    def credit(self, limit: int = 400) -> dict:
        """Who has said something about this site, and how often.  Counted from the ledger,
        so a comment that has been dealt with and taken off the page still counts."""
        with self.connect() as c:
            live = {r["id"]: r for r in c.execute("SELECT * FROM users")}
            people = []
            for r in c.execute(
                    "SELECT user_id, COUNT(*) AS total, "
                    "SUM(CASE WHEN removed IS NULL THEN 1 ELSE 0 END) AS open, "
                    "SUM(CASE WHEN removed IS NOT NULL THEN 1 ELSE 0 END) AS addressed, "
                    "MIN(created) AS first, MAX(created) AS last, MAX(id) AS seq "
                    "FROM credits GROUP BY user_id ORDER BY total DESC, seq"):
                u = live.get(r["user_id"])
                name = u["name"] if u else c.execute(
                    "SELECT name FROM credits WHERE user_id=? ORDER BY id DESC LIMIT 1", (r["user_id"],)).fetchone()[0]
                people.append({"id": r["user_id"], "name": name, "color": u["color"] if u else 0,
                               "total": r["total"], "open": r["open"], "addressed": r["addressed"],
                               "first": r["first"], "last": r["last"], "gone": u is None})
            items = [{"name": r["name"], "page": r["page"], "text": r["text"],
                      "created": r["created"], "removed": r["removed"]}
                     for r in c.execute("SELECT * FROM credits ORDER BY id DESC LIMIT ?", (limit,))]
        return {"people": people, "items": items,
                "total": sum(p["total"] for p in people),
                "addressed": sum(p["addressed"] for p in people)}

    def is_admin(self, user) -> bool:
        return user is not None and user["email"].lower() in self.admins

    def set_resolved(self, user, tid: int, resolved: bool) -> dict:
        """Mark a note as addressed, or reopen it.

        Anyone signed in may do it and the name is recorded, because the person who fixes a
        thing is usually not the person who reported it -- the author marking their own note
        would be the one case that does not happen.  It is reversible, and the text stays on
        the page either way: this is the "done" that is not deletion.

        The credit ledger follows, so the Credit page's *addressed* count means the same
        thing whether a note was marked or taken off the page.
        """
        with self.lock, self.connect() as c:
            t = c.execute("SELECT * FROM threads WHERE id=?", (tid,)).fetchone()
            if t is None:
                raise HttpError(404, "no such thread")
            now = time.time()
            if resolved:
                c.execute("UPDATE threads SET resolved=?, resolved_by=? WHERE id=?", (now, user["id"], tid))
                c.execute("UPDATE credits SET removed=? WHERE thread_id=? AND removed IS NULL", (now, tid))
            else:
                c.execute("UPDATE threads SET resolved=NULL, resolved_by=NULL WHERE id=?", (tid,))
                c.execute("UPDATE credits SET removed=NULL WHERE thread_id=?", (tid,))
        return self.thread(tid)

    def delete_thread(self, user, tid: int) -> None:
        with self.lock, self.connect() as c:
            t = c.execute("SELECT * FROM threads WHERE id=?", (tid,)).fetchone()
            if t is None:
                raise HttpError(404, "no such thread")
            if t["user_id"] != user["id"] and not self.is_admin(user):
                raise HttpError(403, "only the thread's author or an admin can delete it")
            c.execute("UPDATE credits SET removed=? WHERE thread_id=? AND removed IS NULL", (time.time(), tid))
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
            c.execute("UPDATE credits SET removed=? WHERE comment_id=? AND removed IS NULL", (time.time(), cid))
            c.execute("DELETE FROM comments WHERE id=?", (cid,))
            left = c.execute("SELECT COUNT(*) FROM comments WHERE thread_id=?", (r["thread_id"],)).fetchone()[0]
            if left == 0:
                c.execute("UPDATE credits SET removed=? WHERE thread_id=? AND removed IS NULL", (time.time(), r["thread_id"]))
                c.execute("DELETE FROM threads WHERE id=?", (r["thread_id"],))
            return {"thread": r["thread_id"], "gone": left == 0}


# ------------------------------------------------------------------------- the mail

class Mailer:
    """The one thing that ever leaves this host: the invitation.  Any relay that speaks
    SMTP will do -- Gmail with an app password on smtp.gmail.com:587, or a transactional
    provider -- and its password comes from a file or the environment, never the command
    line, where `ps` would show it.  With no relay configured `ready` is false: the
    invitation is still made, and the admin copies its link out of the panel by hand."""

    def __init__(self, host: str = "", port: int = 587, user: str = "", password: str = "",
                 sender: str = "", mode: str = "starttls", timeout: int = 20):
        self.host, self.port = host.strip(), int(port)
        self.user, self.password = user.strip(), password.strip()
        self.sender = (sender or user).strip()
        self.mode, self.timeout = mode, timeout

    def ready(self) -> bool:
        return bool(self.host and self.sender)

    def send(self, to: str, subject: str, body: str, reply_to: str = "") -> None:
        if not self.ready():
            raise HttpError(503, "no mail relay is configured; copy the link instead")
        msg = EmailMessage()
        msg["From"] = formataddr(("QCCD studio", self.sender))
        msg["To"] = to
        msg["Subject"] = subject
        if reply_to:
            msg["Reply-To"] = reply_to
        msg.set_content(body)
        try:
            if self.mode == "ssl":
                smtp = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout,
                                        context=ssl_mod.create_default_context())
            else:
                smtp = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
            with smtp:
                smtp.ehlo()
                if self.mode == "starttls":
                    smtp.starttls(context=ssl_mod.create_default_context())
                    smtp.ehlo()
                if self.user and self.password:
                    smtp.login(self.user, self.password)
                smtp.send_message(msg)
        except (smtplib.SMTPException, OSError, ssl_mod.SSLError) as e:
            raise HttpError(502, f"the relay refused the message: {type(e).__name__}: {e}")


def invite_mail(site: str, link: str, inviter: str, expires: float, note: str = "") -> tuple[str, str]:
    """The subject and the body of that message: what it is, the link, and when it dies."""
    host = site.split("://", 1)[-1].rstrip("/")
    lines = [f"{inviter or 'The site admin'} has invited you to {host}, the QCCD architecture study.", ""]
    if note:
        lines += [note, ""]
    lines += ["This link makes your account.  It works once, and only until "
              + time.strftime("%d %B %Y", time.localtime(expires)) + ":", "",
              "    " + link, "",
              "The site is open to invited readers only.  Your name appears on the comments",
              "you leave, and nothing else about you is kept.", "",
              "If you were not expecting this, nothing happens unless you follow the link."]
    return f"You are invited to {host}", "\n".join(lines) + "\n"


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


def norm_note(note) -> str:
    if note is None:
        return ""
    if not isinstance(note, str):
        raise HttpError(400, "the note must be a string")
    note = note.strip()
    if len(note) > MAX_NOTE:
        raise HttpError(400, f"the note is at most {MAX_NOTE} characters")
    return note


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
    def __init__(self, store: Store, secure: bool, proxied: bool,
                 site: str = DEFAULT_SITE, mailer: Mailer | None = None):
        self.store, self.secure, self.proxied = store, secure, proxied
        self.site = (site or DEFAULT_SITE).rstrip("/")
        self.mailer = mailer or Mailer()
        self.limit = RateLimit()
        self.routes = [
            ("GET", re.compile(r"^/api/health$"), self.health),
            ("GET", re.compile(r"^/api/me$"), self.me),
            ("GET", re.compile(r"^/api/invite$"), self.invite_show),
            ("POST", re.compile(r"^/api/register$"), self.register),
            ("POST", re.compile(r"^/api/login$"), self.login),
            ("POST", re.compile(r"^/api/logout$"), self.logout),
            ("POST", re.compile(r"^/api/password$"), self.password),
            ("GET", re.compile(r"^/api/credit$"), self.credit),
            ("GET", re.compile(r"^/api/threads$"), self.threads),
            ("POST", re.compile(r"^/api/threads$"), self.thread_create),
            ("POST", re.compile(r"^/api/threads/(\d+)/comments$"), self.comment_create),
            ("POST", re.compile(r"^/api/threads/(\d+)/resolve$"), self.thread_resolve),
            ("DELETE", re.compile(r"^/api/threads/(\d+)$"), self.thread_delete),
            ("DELETE", re.compile(r"^/api/comments/(\d+)$"), self.comment_delete),
            ("GET", re.compile(r"^/api/admin/threads$"), self.admin_threads),
            ("GET", re.compile(r"^/api/admin/people$"), self.admin_people),
            ("POST", re.compile(r"^/api/admin/invites$"), self.admin_invite),
            ("DELETE", re.compile(r"^/api/admin/invites/(\d+)$"), self.admin_invite_delete),
            ("POST", re.compile(r"^/api/admin/users/(\d+)/access$"), self.admin_access),
        ]

    # each handler: (req, *groups) -> (status, body dict, extra headers list)
    def health(self, req):
        return 200, {"ok": True}, []

    def me(self, req):
        return 200, {"user": self.store.user_json(req.user, me=True) if req.user else None}, []

    def invite_show(self, req):
        """What the link is worth, before anybody has an account: the address it is for."""
        self.limit.check("invite:" + req.ip)
        return 200, {"invite": self.store.invite((req.query.get("token") or [""])[0])}, []

    def register(self, req):
        """Only an invitation makes an account, and it decides the address."""
        self.limit.check("reg:" + req.ip)
        b = req.json()
        name, pw = norm_name(b.get("name")), norm_password(b.get("password"))
        user = self.store.accept_invite(b.get("token"), name, pw)
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

    def credit(self, req):
        """Open to anyone signed in: the site is by invitation, so that is the collaborators."""
        req.require_user()
        return 200, self.store.credit(), []

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

    def thread_resolve(self, req, tid):
        u = req.require_user()
        b = req.json()
        want = b.get("resolved", True)
        if not isinstance(want, bool):
            raise HttpError(400, "resolved must be true or false")
        return 200, {"thread": self.store.set_resolved(u, int(tid), want)}, []

    def thread_delete(self, req, tid):
        self.store.delete_thread(req.require_user(), int(tid))
        return 200, {"ok": True}, []

    def comment_delete(self, req, cid):
        return 200, self.store.delete_comment(req.require_user(), int(cid)), []

    def admin(self, req):
        u = req.require_user()
        if not self.store.is_admin(u):
            raise HttpError(403, "admins only")
        return u

    def admin_threads(self, req):
        self.admin(req)
        return 200, {"threads": self.store.all_threads()}, []

    def admin_people(self, req):
        """Who is in, and who has been asked in: the one screen the admin manages."""
        self.admin(req)
        return 200, {"users": self.store.users(), "invites": self.store.invites(),
                     "mail": self.mailer.ready(), "site": self.site}, []

    def link_for(self, token: str) -> str:
        return f"{self.site}/?invite={token}"

    def admin_invite(self, req):
        """Names an address, and mails it the link.  A relay that refuses is not a failed
        invitation: it is made either way, and the answer carries the link to copy."""
        u = self.admin(req)
        b = req.json()
        email = norm_email(b.get("email"))
        name = norm_name(b.get("name")) if str(b.get("name") or "").strip() else ""
        note = norm_note(b.get("note"))
        inv, token = self.store.create_invite(u, email, name, note)
        link = self.link_for(token)
        sent, why = False, None
        if b.get("send", True):
            subject, body = invite_mail(self.site, link, u["name"], inv["expires"], note)
            try:
                self.mailer.send(email, subject, body, reply_to=u["email"])
                self.store.mark_sent(inv["id"])
                sent, inv["sent"] = True, time.time()
            except HttpError as e:
                why = e.message
        return 200, {"invite": inv, "link": link, "sent": sent, "mail_error": why}, []

    def admin_invite_delete(self, req, iid):
        self.admin(req)
        self.store.revoke_invite(int(iid))
        return 200, {"ok": True}, []

    def admin_access(self, req, uid):
        me = self.admin(req)
        allow = req.json().get("allow")
        if not isinstance(allow, bool):
            raise HttpError(400, "allow must be true or false")
        if int(uid) == me["id"] and not allow:
            raise HttpError(403, "you cannot close your own account")
        return 200, {"user": self.store.set_access(int(uid), allow)}, []

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


def serve(db: str, host: str, port: int, admins: set[str], secure: bool, proxied: bool,
          site: str = DEFAULT_SITE, mailer: Mailer | None = None) -> None:
    Handler.app = App(Store(db, admins), secure=secure, proxied=proxied, site=site, mailer=mailer)
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    app = Handler.app
    print(f"qccd comments api on http://{host}:{srv.server_address[1]}/api/  db={db}  "
          f"admins={sorted(app.store.admins)}  site={app.site}  "
          f"mail={app.mailer.host + ':' + str(app.mailer.port) if app.mailer.ready() else 'off (links are copied by hand)'}",
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


# ------------------------------------------------------------------------- command line

def add_mail_args(ap) -> None:
    """The relay, for the two commands that can send: the password comes from a file or
    the environment, so it is never an argument `ps` could show."""
    ap.add_argument("--site", default=os.environ.get("QCCD_SITE_URL", DEFAULT_SITE),
                    help="the site an invitation link points at")
    ap.add_argument("--smtp-host", default=os.environ.get("QCCD_SMTP_HOST", ""))
    ap.add_argument("--smtp-port", type=int, default=int(os.environ.get("QCCD_SMTP_PORT", "587")))
    ap.add_argument("--smtp-user", default=os.environ.get("QCCD_SMTP_USER", ""))
    ap.add_argument("--smtp-password-file", default=os.environ.get("QCCD_SMTP_PASSWORD_FILE", ""),
                    help="a file holding the relay password; QCCD_SMTP_PASSWORD is read too")
    ap.add_argument("--mail-from", default=os.environ.get("QCCD_MAIL_FROM", ""),
                    help="the From: address (the SMTP user by default)")
    ap.add_argument("--smtp-mode", choices=("starttls", "ssl", "plain"),
                    default=os.environ.get("QCCD_SMTP_MODE", "starttls"))


def mailer_from(a) -> Mailer:
    pw = os.environ.get("QCCD_SMTP_PASSWORD", "")
    if a.smtp_password_file:
        with open(a.smtp_password_file, encoding="utf-8") as f:
            pw = f.read().strip()
    return Mailer(a.smtp_host, a.smtp_port, a.smtp_user, pw, a.mail_from, a.smtp_mode)


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
    add_mail_args(s)
    p = sub.add_parser("passwd", help="set an account's password, creating the account if needed")
    p.add_argument("--db", required=True)
    p.add_argument("email")
    p.add_argument("--name")
    p.add_argument("--password", help="otherwise read from stdin or asked for")
    i = sub.add_parser("invite", help="invite one email address; prints the link")
    i.add_argument("--db", required=True)
    i.add_argument("email")
    i.add_argument("--name", help="the name the invitation is addressed to")
    i.add_argument("--note", help="a line of your own in the message")
    i.add_argument("--inviter", default="The site admin", help="who the message comes from")
    i.add_argument("--send", action="store_true", help="also mail the link through the relay")
    add_mail_args(i)
    x = sub.add_parser("access", help="open or close one account")
    x.add_argument("--db", required=True)
    x.add_argument("email")
    g = x.add_mutually_exclusive_group(required=True)
    g.add_argument("--allow", dest="allow", action="store_true")
    g.add_argument("--revoke", dest="allow", action="store_false")
    u = sub.add_parser("users", help="list the accounts")
    u.add_argument("--db", required=True)
    v = sub.add_parser("invites", help="list the invitations")
    v.add_argument("--db", required=True)
    t = sub.add_parser("threads", help="list every thread")
    t.add_argument("--db", required=True)
    a = ap.parse_args(argv)
    try:
        return dispatch(a)
    except HttpError as e:
        print(f"error: {e.message}", file=sys.stderr)
        return 1


def dispatch(a) -> int:
    if a.cmd == "serve":
        serve(a.db, a.host, a.port, set(a.admins.split(",")), a.secure, a.proxied,
              site=a.site, mailer=mailer_from(a))
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
    if a.cmd == "invite":
        email = norm_email(a.email)
        inv, token = store.create_invite(None, email, norm_name(a.name) if a.name else "", norm_note(a.note))
        link = f"{a.site.rstrip('/')}/?invite={token}"
        if a.send:
            subject, body = invite_mail(a.site, link, a.inviter, inv["expires"], inv["note"])
            mailer_from(a).send(email, subject, body)
            store.mark_sent(inv["id"])
            print(f"mailed to {email}")
        print(link)
        return 0
    if a.cmd == "access":
        email = norm_email(a.email)
        with store.connect() as c:
            row = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if row is None:
            print(f"no account for {email}", file=sys.stderr)
            return 1
        user = store.set_access(row["id"], a.allow)
        print(f"{user['email']} is now {'open' if user['access'] else 'closed'}")
        return 0
    if a.cmd == "users":
        for x in store.users():
            print(f"{x['id']:4d}  {x['email']:40s} {'open  ' if x['access'] else 'CLOSED'}  {x['name']}")
        return 0
    if a.cmd == "invites":
        for inv in store.invites():
            state = ("accepted" if inv["accepted"] else "expired" if inv["expired"]
                     else "sent" if inv["sent"] else "not sent")
            print(f"{inv['id']:4d}  {inv['email']:40s} {state:9s} {inv['name']}")
        return 0
    if a.cmd == "threads":
        for th in store.all_threads(10000):
            first = th["comments"][0]["text"] if th["comments"] else ""
            print(f"{th['id']:4d}  {th['page']:40s} {th['user']['name']:20s} {len(th['comments'])} msg  {first[:60]!r}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
