"""Signing a workspace in with a qccd.academy account, so what it submits is credited to a person.

The site's accounts live in the comments service (qccd/site/comments_api.py); submissions live
here.  The two processes share no database and no network path (this one runs in a container),
so the site vouches for a person with a GRANT: a short statement it signs with a secret both
hold (`QCCD_ACCOUNT_SECRET`), naming the account, what it is for and for how long (5 minutes).

    workspace                      this service                      the site (signed-in page)
    POST /v1/links {label}   ->    a code + a poll secret
    opens <site>/connect/?code=XXXX-XXXX in the person's browser  ->  "Allow <label> to submit
                                                                      under your name?"  Allow:
                                   <- POST /v1/links/<code>/approve    POST /api/leaderboard/grant
                                      {grant: link, this code}          {purpose: link, code}
    POST /v1/links/<code>/token {poll_secret}
                             <-    the key, ONCE (an uploader of kind 'account')

The person's password never reaches the workspace; the key it gets can do one thing, upload a
submission credited to that account, and the person revokes it from the site (`manage` grants)
or the workspace signs itself out (`/v1/keys/self/revoke`).  Without the secret configured,
every step here fails closed and says so.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

__all__ = ["GrantError", "verify_grant", "sign_grant", "new_code", "LINK_TTL_S", "GRANT_TTL_S", "ACCOUNT_QUOTA",
           "site_url"]

LINK_TTL_S = 600
GRANT_TTL_S = 300
#: submissions per account per day, over all of its keys
ACCOUNT_QUOTA = int(os.environ.get("QCCD_ACCOUNT_QUOTA", "30"))
#: letters that cannot be misread for each other (no 0/O, 1/I/L)
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


class GrantError(Exception):
    def __init__(self, code: str, message: str, status: int = 403):
        super().__init__(message)
        self.code = code
        self.status = status


def site_url() -> str:
    return os.environ.get("QCCD_SITE_URL", "https://qccd.academy").rstrip("/")


def _secret() -> bytes:
    s = os.environ.get("QCCD_ACCOUNT_SECRET", "")
    if len(s) < 32:
        raise GrantError("signin_unavailable", "signing in with a qccd.academy account is not set up on this "
                         "server yet", 503)
    return s.encode("utf-8")


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_grant(payload: dict, secret: bytes | None = None) -> str:
    """What the site does (comments_api.py has its own copy: it is one stdlib file); here for tests."""
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    mac = hmac.new(secret or _secret(), body.encode("ascii"), hashlib.sha256).digest()
    return body + "." + _b64(mac)


def verify_grant(grant, *, purpose: str, code: str | None = None, now: float | None = None) -> dict:
    """The account a grant names, if the site signed it for this purpose (and link code), and it
    has not expired.  Anything else is refused with why."""
    if not isinstance(grant, str) or grant.count(".") != 1 or len(grant) > 4096:
        raise GrantError("bad_grant", "the site's grant is missing or malformed")
    body, mac = grant.split(".")
    want = hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest()
    try:
        got = _unb64(mac)
        payload = json.loads(_unb64(body))
    except (ValueError, UnicodeDecodeError):
        raise GrantError("bad_grant", "the site's grant is malformed") from None
    if not hmac.compare_digest(want, got):
        raise GrantError("bad_grant", "the grant was not signed by the site")
    now = time.time() if now is None else now
    if not isinstance(payload, dict) or payload.get("v") != 1 or payload.get("aud") != "qccd-official":
        raise GrantError("bad_grant", "the grant is not for the leaderboard")
    if payload.get("purpose") != purpose:
        raise GrantError("bad_grant", f"the grant is for {payload.get('purpose')!r}, not {purpose!r}")
    if code is not None and payload.get("code") != code:
        raise GrantError("bad_grant", "the grant is for another sign-in code")
    exp, iat = payload.get("exp"), payload.get("iat")
    if not isinstance(exp, (int, float)) or not isinstance(iat, (int, float)) or exp < now or iat > now + 60 \
            or exp - iat > GRANT_TTL_S + 5:
        raise GrantError("grant_expired", "the site's grant has expired; press the button again")
    sub, name = payload.get("sub"), payload.get("name")
    if not isinstance(sub, str) or not sub or not isinstance(name, str) or not name.strip():
        raise GrantError("bad_grant", "the grant names no account")
    return {"id": "site:" + sub, "name": name.strip()[:80]}


def new_code() -> str:
    c = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    return c[:4] + "-" + c[4:]


def norm_code(code: str) -> str:
    c = "".join(ch for ch in str(code or "").upper() if ch.isalnum())
    return (c[:4] + "-" + c[4:]) if len(c) == 8 else ""
