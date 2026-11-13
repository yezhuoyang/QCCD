"""The two agent accounts a review can run with, and how each signs in.

    claude   Claude Code, with the person's Claude subscription (Pro or Max)
    codex    Codex, with the person's ChatGPT plan

Each is signed in ONCE, by its CLI's own sign-in run in the reviewer image (container.py), so a
person needs Docker and nothing else -- not the CLI on their computer -- on macOS, Linux and
Windows alike:

    claude   `claude setup-token`: a link to approve on claude.com; that page then shows a code,
             which the person pastes back.  It prints a long-lived subscription token, kept here
             and never shown.
    codex    `codex login --device-auth`: a link, and a one-time code to enter on that page.

What a sign-in leaves is a PROFILE of the review's own in ~/.qccd/reviewers/ (QCCD_HOME moves it),
shared by every workspace on this computer and readable by this user only:

    reviewers/claude/oauth-token   the token, given to a review's container by name
                                   (CLAUDE_CODE_OAUTH_TOKEN) and to nothing else
    reviewers/claude/home/         Claude Code's own settings and state in the container
    reviewers/codex/               Codex's home in the container (CODEX_HOME); auth.json is its sign-in

The person's own Claude Code and Codex -- their sign-ins, instructions, memories, plugins, MCP
servers -- are never read: a review is a fresh session in a profile of its own.  QCCD sends a
token nowhere; the CLI uses it, in the container, to reach its own vendor.  (A Claude subscription
signs in only to Claude Code itself, and a ChatGPT plan only to Codex: so the CLIs do the signing
in and the calling, on the person's computer.)
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path

__all__ = ["REVIEWERS", "qccd_home", "profile_dir", "home_dir", "model_of", "claude_token", "save_claude_token",
           "signed_in", "status", "statuses", "sign_out", "SigninSession", "clean_output", "scrub"]

#: the agents a review can run, by the id a request names.  `hosts`: its vendor's, the only ones a
#: review's container may reach (container.py's egress proxy)
REVIEWERS = {
    "claude": {"title": "Claude", "cli": "Claude Code", "vendor": "Anthropic",
               "plan": "a Claude subscription (Pro or Max)", "model_env": "QCCD_REVIEW_CLAUDE_MODEL",
               "home_var": "CLAUDE_CONFIG_DIR",
               "hosts": ("api.anthropic.com", "console.anthropic.com", "platform.claude.com", "claude.ai",
                         "claude.com")},
    "codex": {"title": "ChatGPT", "cli": "Codex", "vendor": "OpenAI", "plan": "a ChatGPT plan",
              "model_env": "QCCD_REVIEW_CODEX_MODEL", "home_var": "CODEX_HOME",
              "hosts": ("chatgpt.com", "auth.openai.com", "api.openai.com")},
}

#: Claude Code's subscription token, as `claude setup-token` prints it
CLAUDE_TOKEN = re.compile(r"sk-ant-oat01-[A-Za-z0-9_\-]{20,}")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
#: where each CLI's sign-in link points
_SIGNIN_HOSTS = {"claude": ("claude.com/", "claude.ai/"), "codex": ("auth.openai.com/",)}


def qccd_home() -> Path:
    """~/.qccd, or QCCD_HOME (toolchain.py's convention)."""
    return Path(os.environ.get("QCCD_HOME") or (Path.home() / ".qccd"))


def profile_dir(rid: str) -> Path:
    return qccd_home() / "reviewers" / rid


def home_dir(rid: str) -> Path:
    """The folder the CLI keeps its own state in, in the container (/profile): Codex's sign-in is
    in it, Claude's token beside it."""
    return profile_dir(rid) / "home" if rid == "claude" else profile_dir(rid)


def private_dir(p: Path) -> Path:
    """`p`, made if need be, readable by this user only (where the system lets us say so)."""
    p.mkdir(parents=True, exist_ok=True)
    try:
        p.chmod(0o700)
    except OSError:
        pass
    return p


def model_of(rid: str) -> str | None:
    """The model a review asks for; None lets the CLI pick the account's own default."""
    return os.environ.get(REVIEWERS[rid]["model_env"]) or None


# ---------------------------------------------------------------------- who is signed in

def claude_token() -> str | None:
    """The Claude subscription token the review profile was signed in with."""
    try:
        t = (profile_dir("claude") / "oauth-token").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return t if CLAUDE_TOKEN.fullmatch(t) else None


def save_claude_token(token: str) -> None:
    """Keep a Claude subscription token in the review profile, readable by this user only."""
    token = (token or "").strip()
    if not CLAUDE_TOKEN.fullmatch(token):
        raise ValueError("that is not a Claude subscription token (`claude setup-token` prints one: sk-ant-oat01-…)")
    d = private_dir(profile_dir("claude"))
    tmp = d / "oauth-token.tmp"
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token + "\n")
    os.replace(tmp, d / "oauth-token")


def _codex_auth() -> dict | None:
    try:
        d = json.loads((profile_dir("codex") / "auth.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and (d.get("tokens") or d.get("OPENAI_API_KEY")) else None


def _jwt_email(tok) -> str | None:
    """The e-mail an OpenAI id token names, for the page to say who is signed in (read, not verified:
    Codex verifies it; this only labels it)."""
    try:
        part = str(tok).split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return claims.get("email") or (claims.get("https://api.openai.com/profile") or {}).get("email")
    except (IndexError, ValueError, TypeError, AttributeError):
        return None


def signed_in(rid: str) -> tuple[bool, str | None]:
    """(signed in for reviews, as what), from the profile alone."""
    if rid == "claude":
        if not claude_token():
            return False, None
        when = time.strftime("%Y-%m-%d", time.localtime((profile_dir("claude") / "oauth-token").stat().st_mtime))
        return True, f"a subscription token, signed in {when}"
    auth = _codex_auth()
    if not auth:
        return False, None
    who = _jwt_email((auth.get("tokens") or {}).get("id_token"))
    return True, who or ("an API key" if auth.get("OPENAI_API_KEY") else "a ChatGPT account")


def status(rid: str) -> dict:
    spec = REVIEWERS[rid]
    ok, who = signed_in(rid)
    return {"id": rid, "title": spec["title"], "cli": spec["cli"], "vendor": spec["vendor"], "plan": spec["plan"],
            "signed_in": ok, "account": who, "model": model_of(rid)}


def statuses() -> list[dict]:
    return [status(r) for r in REVIEWERS]


def sign_out(rid: str) -> None:
    """Forget the review profile's sign-in (the person's own CLI is not touched)."""
    for p in ((profile_dir("claude") / "oauth-token",) if rid == "claude" else
              (profile_dir("codex") / "auth.json",)):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------- signing in

def clean_output(raw: str) -> str:
    """A terminal's output as text: a cursor moved right is the spaces it skipped (Claude Code's
    screens are drawn that way), every other escape goes, and a carriage return ends a line."""
    raw = re.sub(r"\x1b\[(\d*)C", lambda m: " " * min(int(m.group(1) or 1), 200), raw)
    return _ANSI.sub("", raw).replace("\r\n", "\n").replace("\r", "\n")


def scrub(text: str) -> str:
    """`text` without the Claude token in it."""
    return CLAUDE_TOKEN.sub("sk-ant-oat01-… (kept, not shown)", text or "")


class SigninSession:
    """One agent's sign-in for a person at a page or a terminal: the CLI's own sign-in, run in the
    reviewer image with a terminal of its own (container.signin_command), the link it prints (and
    Codex's one-time code) to show, and the code Claude's page shows, typed back in (`send`).

    state: preparing (the image is being made ready) | waiting (for the person) | finishing (a code
    was sent) | signed_in | failed | cancelled."""

    TIMEOUT_S = 900.0

    def __init__(self, rid: str):
        if rid not in REVIEWERS:
            raise ValueError(f"the agent is one of {', '.join(REVIEWERS)}")
        self.id = "si_" + secrets.token_hex(6)
        self.rid = rid
        self.name = "qccd-si-" + secrets.token_hex(5)          # the container's name
        self.state, self.note = "preparing", "getting the reviewer image ready"
        self.url = self.code = self.error = None
        self.needs_code = False
        self.out = ""
        self.started, self.finished = time.time(), None
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()

    def start(self, say=None) -> "SigninSession":
        threading.Thread(target=self._run, args=(say,), name=f"qccd-signin-{self.id}", daemon=True).start()
        return self

    def _set(self, **kw) -> None:
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def _run(self, say) -> None:
        from . import container
        try:
            def progress(lines):
                self._set(note=str(lines[-1])[:200] if lines else self.note)
                if say:
                    say(lines)
            argv, env = container.signin_command(self.rid, self.name, say=progress)
            if self.state == "cancelled":
                return
            self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         env=env, **container.no_window())
            self._set(state="waiting", note="starting " + REVIEWERS[self.rid]["cli"] + "'s sign-in")
            threading.Thread(target=self._deadline, name=f"qccd-signin-{self.id}-deadline", daemon=True).start()
            raw = ""
            while True:
                chunk = self.proc.stdout.read1(4096)
                if not chunk:
                    break
                raw = (raw + chunk.decode("utf-8", "replace"))[-60000:]
                self._scan(clean_output(raw))
            self._scan(clean_output(raw) + "\n")     # a token that ends the output ends there
            rc = self.proc.wait()
            self._ended(rc)
        except Exception as exc:                 # say why on the page; never leave it waiting
            self._set(state="failed", error=f"{exc}"[:400], finished=time.time())
        finally:
            self._remove()

    def _scan(self, text: str) -> None:
        tok = None
        if self.rid == "claude":
            # the whole token, up to the end of its line: a read can end in the middle of it
            m = re.search(CLAUDE_TOKEN.pattern + r"(?=\s)", text)
            tok = m.group(0) if m else None
        with self.lock:
            self.out = scrub(text)[-20000:]
            if self.url is None:
                for u in re.findall(r"https://[^\s\"'<>]+", text):
                    if any(h in u for h in _SIGNIN_HOSTS[self.rid]):
                        self.url = u.rstrip(".,)")
                        break
            if self.rid == "codex":
                m = re.search(r"\b([A-Z0-9]{4}-[A-Z0-9]{4,6})\b", text)
                if m:
                    self.code = m.group(1)
                if self.url and self.code and self.state == "waiting":
                    self.note = "enter the code on the sign-in page"
            elif re.search(r"(?i)paste (the )?code", text) and self.state == "waiting":
                self.needs_code = True
                self.note = "approve on the sign-in page, then paste the code it shows"
        if tok and self.state not in ("signed_in", "cancelled"):
            save_claude_token(tok)
            self._set(state="signed_in", note="signed in", needs_code=False, finished=time.time())
            self._remove()                       # its job is done: the token is kept

    def _ended(self, rc: int | None) -> None:
        ok, _ = signed_in(self.rid)
        with self.lock:
            if self.state in ("cancelled", "signed_in"):
                return
            if ok and (self.rid == "codex" or self.state == "finishing"):
                self.state, self.note = "signed_in", "signed in"
            else:
                tail = " ".join(ln.strip() for ln in self.out.splitlines()[-6:] if ln.strip())
                self.state = "failed"
                self.error = (f"the sign-in ended (exit {rc}) without signing in" + (f": {tail[-300:]}" if tail else ""))
            self.finished = time.time()

    def _deadline(self) -> None:
        end = self.started + self.TIMEOUT_S
        while time.time() < end:
            if self.state not in ("preparing", "waiting", "finishing"):
                return
            time.sleep(1.0)
        self._set(state="failed", error=f"the sign-in was not finished within {round(self.TIMEOUT_S / 60)} minutes",
                  finished=time.time())
        self._remove()

    def send(self, text: str) -> None:
        """Type the code Claude's page showed, and Enter, into the sign-in."""
        code = str(text or "").strip()
        if self.rid != "claude" or self.state != "waiting" or self.proc is None:
            raise RuntimeError("this sign-in is not waiting for a code")
        if not code or len(code) > 400 or any(ord(c) < 32 for c in code):
            raise ValueError("paste the code the sign-in page shows: one line")
        self.proc.stdin.write((code + "\r").encode("utf-8"))
        self.proc.stdin.flush()
        self._set(state="finishing", note="checking the code", needs_code=False)

    def cancel(self) -> None:
        self._set(state="cancelled", note="cancelled", finished=self.finished or time.time())
        self._remove()

    def _remove(self) -> None:
        """Stop the sign-in's container (and with it the CLI) if it is still there."""
        from . import container
        container.remove(self.name)
        p = self.proc
        if p is not None and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass

    def view(self) -> dict:
        with self.lock:
            return {"signin_id": self.id, "reviewer": self.rid, "title": REVIEWERS[self.rid]["title"],
                    "state": self.state, "note": self.note, "url": self.url, "code": self.code,
                    "needs_code": self.needs_code and self.state == "waiting", "error": self.error,
                    "started_at": self.started, "finished_at": self.finished}
