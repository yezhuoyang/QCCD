"""Signing this computer in to qccd.academy, so what it submits to the official leaderboard is
credited to its person -- and only after they signed in.

The sign-in is a device link (qccd/official/accounts.py):

  1. the workspace asks the official server for a code (`POST /v1/links`) and opens
     `https://qccd.academy/connect/?code=XXXX-XXXX` in the person's browser;
  2. there the person, signed in to the site with their own account, presses Allow;
  3. the workspace, polling with a secret only it holds, collects a key once.

The password never passes through the workspace, and the key it keeps can do one thing: upload
a submission in that person's name.  It is kept per person, not per workspace, in
`~/.qccd/credentials.json` (`QCCD_CREDENTIALS` moves it; tests do), which only this user can
read, under the server's URL, beside a maintainer's upload token if there is one.  It is never
written into a workspace, a bundle, a URL, a log, an MCP answer or a page: every door returns
the account's NAME only.  Signing out revokes the key on the server first.

An agent cannot sign in or out (the routes are the person's); it can read whether someone is
signed in, and as whom, which is what it needs to know before it asks to submit.
"""

from __future__ import annotations

import json
import os
import platform
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

__all__ = ["server_url", "credentials_path", "credential", "write_credential", "account_status", "SignIn",
           "Accounts", "official_call", "default_label"]


def server_url() -> str:
    return os.environ.get("QCCD_OFFICIAL_URL", "https://qccd.academy/official").rstrip("/")


def credentials_path() -> Path:
    p = os.environ.get("QCCD_CREDENTIALS")
    return Path(p) if p else Path.home() / ".qccd" / "credentials.json"


def _read_all() -> dict:
    p = credentials_path()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def credential(server: str | None = None) -> dict | None:
    """This person's entry for `server`: {"token", "account": {"id", "name"}, "key_id", "label",
    "signed_in_at"}, or a maintainer's bare token as {"token"}; None when there is neither."""
    v = _read_all().get((server or server_url()).rstrip("/"))
    if isinstance(v, str) and v:
        return {"token": v}
    if isinstance(v, dict) and isinstance(v.get("token"), str) and v["token"]:
        return v
    return None


def write_credential(server: str, entry: dict | None) -> None:
    """Replace (or with None remove) one server's entry, atomically, readable by this user only."""
    p = credentials_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    d = _read_all()
    if entry is None:
        d.pop(server.rstrip("/"), None)
    else:
        d[server.rstrip("/")] = entry
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1, sort_keys=True)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, p)


def default_label() -> str:
    """What the site shows the person on the Allow page and in their list of computers."""
    node = (platform.node() or "").split(".")[0][:40]
    return f"QCCD workspace on {node}" if node else "a QCCD workspace"


class OfficialHTTPError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


def official_call(method: str, path: str, body: Any = None, *, token: str | None = None,
                  server: str | None = None, raw: bytes | None = None, headers: dict | None = None,
                  timeout: float = 30) -> Any:
    """One request to the official server; its JSON, or OfficialHTTPError with the server's reason."""
    url = (server or server_url()).rstrip("/") + path
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode("utf-8"))
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if raw is None and body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode("utf-8")
            return json.loads(txt) if txt else {}
    except urllib.error.HTTPError as exc:
        try:
            e = json.loads(exc.read().decode("utf-8", "replace")).get("error") or {}
        except ValueError:
            e = {}
        if isinstance(e, str):
            e = {"message": e}
        raise OfficialHTTPError(exc.code, e.get("code") or "http_error",
                                e.get("message") or f"the official server answered {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise OfficialHTTPError(0, "official_unreachable", f"the official server could not be reached ({exc})") \
            from None


class SignIn:
    """One sign-in in progress: the code the person approves on the site, polled until they do,
    until it expires, or until it is cancelled.  The key goes straight into the credentials file."""

    def __init__(self, server: str | None = None, label: str | None = None):
        self.server = (server or server_url()).rstrip("/")
        self.label = label or default_label()
        self.state = "starting"
        self.error: str | None = None
        self.code = self.verify_url = None
        self.expires_at = 0.0
        self.name: str | None = None
        self._secret: str | None = None
        self._interval = 2.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "SignIn":
        r = official_call("POST", "/v1/links", {"label": self.label}, server=self.server, timeout=20)
        self.code, self.verify_url, self._secret = r["code"], r["verify_url"], r["poll_secret"]
        self.expires_at = time.time() + float(r.get("expires_in", 600))
        self._interval = max(1.0, float(r.get("interval", 2)))
        self.state = "waiting"
        return self

    def poll_once(self) -> str:
        """One poll: 'waiting', or a final state (signed_in, denied, expired, failed)."""
        if self.state != "waiting":
            return self.state
        if time.time() > self.expires_at + 5:
            self.state = "expired"
            return self.state
        try:
            r = official_call("POST", f"/v1/links/{self.code}/token", {"poll_secret": self._secret},
                              server=self.server, timeout=20)
        except OfficialHTTPError as e:
            if e.status == 0:                  # the network: try again at the next poll
                self.error = str(e)
                return self.state
            self.state = {"link_denied": "denied", "link_expired": "expired"}.get(e.code, "failed")
            self.error = str(e)
            return self.state
        if r.get("status") != "approved":
            return self.state
        acct = r.get("account") or {}
        write_credential(self.server, {"token": r["token"], "key_id": r.get("key_id"), "label": r.get("label"),
                                       "account": {"id": acct.get("id"), "name": acct.get("name")},
                                       "signed_in_at": time.time()})
        self.name = acct.get("name")
        self.state = "signed_in"
        self._secret = None
        Accounts.forget_check(self.server)
        return self.state

    def run(self, on_done=None) -> None:
        def loop():
            while not self._stop.is_set() and self.poll_once() == "waiting":
                self._stop.wait(self._interval)
            if self._stop.is_set() and self.state == "waiting":
                self.state = "cancelled"
            if on_done:
                try:
                    on_done(self)
                except Exception:
                    pass
        self._thread = threading.Thread(target=loop, name="qccd-signin", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._stop.set()

    def to_json(self) -> dict:
        return {"state": self.state, "code": self.code, "verify_url": self.verify_url,
                "expires_at": self.expires_at, "label": self.label, "name": self.name, "error": self.error}


class Accounts:
    """The sign-in state of this computer for one official server, as every door reports it."""

    _checks: dict = {}                    # server -> (checked_at, result of /v1/me): a short cache
    _lock = threading.Lock()

    def __init__(self, server: str | None = None):
        self.server = (server or server_url()).rstrip("/")
        self.signin: SignIn | None = None

    @classmethod
    def forget_check(cls, server: str) -> None:
        with cls._lock:
            cls._checks.pop(server.rstrip("/"), None)

    def token(self) -> str | None:
        c = credential(self.server)
        return c["token"] if c else None

    def status(self, *, verify: bool = False) -> dict:
        """Who this computer submits as: never the key, only the name and where it came from.
        `verify` asks the server (cached a minute) whether the key still works."""
        c = credential(self.server)
        out: dict = {"server": self.server, "signed_in": False, "name": None, "label": None,
                     "signin": self.signin.to_json() if self.signin and self.signin.state in ("waiting", "starting")
                     else None}
        if self.signin and self.signin.state in ("denied", "expired", "failed", "cancelled"):
            out["last_signin"] = {"state": self.signin.state, "error": self.signin.error}
        if not c:
            return out
        acct = c.get("account") or {}
        out.update(signed_in=bool(acct.get("name")), name=acct.get("name"), label=c.get("label"),
                   since=c.get("signed_in_at"), kind="account" if acct.get("name") else "maintainer token")
        if not acct.get("name"):
            # a maintainer's upload token: it uploads, but credits the token's own name, not a person
            out["note"] = "an upload token without an account: submissions are credited to the token's name"
        if verify:
            chk = self._check(c["token"])
            if chk.get("revoked"):
                out.update(signed_in=False, problem="this computer's key was revoked or signed out on the site: "
                                                    "sign in again")
            elif chk.get("error"):
                out["unverified"] = chk["error"]
            elif chk.get("me"):
                me = chk["me"]
                if (me.get("account") or {}).get("name"):
                    out["name"] = me["account"]["name"]
                out["used_today"], out["quota_per_day"] = me.get("used_today"), me.get("quota_per_day")
        return out

    def _check(self, token: str) -> dict:
        with self._lock:
            hit = self._checks.get(self.server)
            if hit and time.time() - hit[0] < 60:
                return hit[1]
        try:
            res = {"me": official_call("GET", "/v1/me", token=token, server=self.server, timeout=10)}
        except OfficialHTTPError as e:
            res = {"revoked": True} if e.status == 401 else {"error": str(e)}
        with self._lock:
            self._checks[self.server] = (time.time(), res)
        return res

    def start_signin(self, label: str | None = None, on_done=None) -> dict:
        if self.signin and self.signin.state == "waiting" and time.time() < self.signin.expires_at:
            return self.signin.to_json()                   # the one already waiting on the site
        s = SignIn(self.server, label)
        s.start()
        self.signin = s
        s.run(on_done)
        return s.to_json()

    def cancel_signin(self) -> dict:
        if self.signin:
            self.signin.cancel()
        return self.status()

    def sign_out(self) -> dict:
        """Revoke this computer's key on the server, then forget it here; a key the server cannot
        be reached to revoke is still forgotten, and the site's account page can revoke it later."""
        c = credential(self.server)
        revoked = None
        if c and (c.get("account") or {}).get("name"):
            try:
                official_call("POST", "/v1/keys/self/revoke", {}, token=c["token"], server=self.server, timeout=15)
                revoked = True
            except OfficialHTTPError as e:
                revoked = e.status == 401            # already revoked
            write_credential(self.server, None)
        self.forget_check(self.server)
        out = self.status()
        out["revoked_on_server"] = revoked
        return out


def account_status(verify: bool = False) -> dict:
    return Accounts().status(verify=verify)
