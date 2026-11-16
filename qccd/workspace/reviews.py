"""Agent reviews in a workspace: the Debug page's "Run agent review" (qccd/review).

The Debug page -- the website's, through the workspace, or its panel beside a design in the
Studio -- opens this workspace's review page (`/review/new`) and hands it the programme it has
open: the machine, the programme, its clock, the rule set and assertions it shows, and the link
that reopens it.  There the person picks Claude or ChatGPT, signs it in once if it is not
(`start_signin`: its CLI's own sign-in, in Docker), and presses Run (`start_review`): the rule check
judges every rule, then their agent explains, in Docker, and the page becomes the report.

Only a person starts a review or signs an agent in: it spends their own subscription, and the
service's routes say so (human_only).  An agent reads reviews (`reviews`, `review`: qccd_get_review).

A review is a folder of its own, `.qccd/reviews/<id>/`, and nothing in the database, so a
workspace a review touched opens with any QCCD:

    request.json   what was asked: title, rule set, assertions, the link back, the agent
    input/         the programme: arch.json, program.tsir.json, model.json
    state.json     where it is: status, progress, the agent's model, usage, egress, error, times
    summary.json   the programme's size; check.json, the rule check (its verdicts are the review's)
    review.md      the agent's report; notes.json, its notes pinned to steps
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Mapping

__all__ = ["ReviewsMixin", "clean_request"]

#: a review's status while it runs; then done, failed or cancelled
ACTIVE = ("queued", "checking", "reviewing")
MAX_DOC = 8 * 1024 * 1024                  # the service's own body limit
MAX_ASSERTIONS = 64 * 1024
_ID = re.compile(r"rv_[0-9a-f]{12}")
#: a model as the CLIs take it: an alias (opus) or an id (the chat's model picker, agents/models.py)
_MODEL = re.compile(r"[A-Za-z0-9._:\-\[\]]{1,80}")
#: where a review's link back may point: the person's own computer, or the website
_LINK_HOSTS = ("127.0.0.1", "localhost", "qccd.academy", "www.qccd.academy")


def _now() -> float:
    return time.time()


def _err(code: str, message: str, status: int = 422, **detail):
    from .core import WorkspaceError
    return WorkspaceError(code, message, status=status, detail=detail or None)


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _clean_link(v) -> str | None:
    """The link back to the Debug page, if it is one: http(s), to this computer or the website."""
    if not isinstance(v, str) or not v or len(v) > 8000:
        return None
    try:
        u = urllib.parse.urlsplit(v)
    except ValueError:
        return None
    return v if u.scheme in ("http", "https") and (u.hostname or "") in _LINK_HOSTS else None


def clean_request(r: Mapping) -> dict:
    """What a page asked to review, checked: the documents are JSON, the rule set is shipped, and
    nothing is larger than it should be."""
    from ..ruleset import names
    if not isinstance(r, Mapping):
        raise _err("bad_request", "the review request must be an object")
    out: dict = {}
    for k in ("arch", "program"):
        v = r.get(k)
        if not isinstance(v, str) or not v.strip():
            raise _err("bad_request", f"the request has no {'machine' if k == 'arch' else 'programme'}: open a program first")
        if len(v) > MAX_DOC:
            raise _err("too_large", f"the {k} document is larger than {MAX_DOC // (1024 * 1024)} MB")
        try:
            if not isinstance(json.loads(v), dict):
                raise ValueError("not an object")
        except ValueError:
            raise _err("bad_request", f"the {k} document is not a JSON object") from None
        out[k] = v
    model = r.get("model")
    if model is not None and (not isinstance(model, dict) or len(json.dumps(model)) > 65536):
        raise _err("bad_request", "the model is the page's own description of its clock (an object)")
    out["model"] = model
    rules = str(r.get("rules") or "standard")
    if rules != "none" and rules not in names():
        raise _err("bad_request", f"the rule set is one the website ships, or none: {', '.join(names())}")
    out["rules"] = rules
    a = r.get("assertions") or ""
    if not isinstance(a, str) or len(a) > MAX_ASSERTIONS:
        raise _err("bad_request", "the assertions are text of at most 64 KB")
    out["assertions"] = a
    out["title"] = " ".join(str(r.get("title") or "a programme").split())[:200]
    out["link"] = _clean_link(r.get("link"))
    steps = r.get("steps")
    out["steps"] = steps if isinstance(steps, int) and not isinstance(steps, bool) and steps >= 0 else None
    src = r.get("source") if isinstance(r.get("source"), Mapping) else {}
    out["source"] = {k: str(src[k])[:300] for k in ("kind", "path") if src.get(k)}
    return out


class ReviewsMixin:
    """`Workspace` methods for agent reviews on this computer (module doc)."""

    # ------------------------------------------------------------------ the folder

    def _rv_dir(self, rid: str) -> Path:
        if not isinstance(rid, str) or not _ID.fullmatch(rid):
            raise _err("unknown_review", f"no review {rid!r}", status=404)
        d = self.root / ".qccd" / "reviews" / rid
        if not (d / "state.json").is_file():
            raise _err("unknown_review", f"no review {rid!r}", status=404)
        return d

    def _rv_lock(self) -> threading.Lock:
        lk = self.__dict__.get("_rv_lock_")
        if lk is None:
            lk = self.__dict__.setdefault("_rv_lock_", threading.Lock())
        return lk

    def _rv_running(self) -> dict:
        return self.__dict__.setdefault("_rv_cancel", {})

    def _rv_set(self, rid: str, **fields) -> dict:
        with self._rv_lock():
            p = self.root / ".qccd" / "reviews" / rid / "state.json"
            st = _read_json(p) or {}
            st.update(fields)
            _write_json(p, st)
            return st

    def _rv_state(self, rid: str) -> dict:
        """state.json, with a review its process no longer runs said to have ended: the service
        stopped (or this one lost it) while it ran."""
        d = self._rv_dir(rid)
        st = _read_json(d / "state.json") or {}
        if st.get("status") in ACTIVE and rid not in self._rv_running():
            from .core import _pid_alive
            if st.get("pid") == os.getpid() or not _pid_alive(int(st.get("pid") or 0)):
                st = self._rv_set(rid, status="failed", finished_at=_now(),
                                  error="the workspace service stopped while it ran: run it again")
        return st

    # ------------------------------------------------------------------ starting one

    def start_review(self, actor: Mapping, request: Mapping, reviewer: str, model: str | None = None) -> dict:
        """Review the programme a page handed over, with the person's own agent: the rule check at
        once, then the agent's report, in Docker.  `model`: an alias or id its CLI takes (left out,
        the account's own default)."""
        from ..review import agents, container
        from .core import new_id
        if actor.get("kind") != "human":
            raise _err("forbidden", "only the person starts a review: it runs with their own account", status=403)
        if reviewer not in agents.REVIEWERS:
            raise _err("bad_request", f"the agent is one of {', '.join(agents.REVIEWERS)}")
        req = clean_request(request)
        if model and not _MODEL.fullmatch(str(model)):
            raise _err("bad_request", "the model is an alias (opus, sonnet) or a model id")
        spec = agents.REVIEWERS[reviewer]
        if not agents.signed_in(reviewer)[0]:
            raise _err("needs_signin", f"{spec['title']} is not signed in for reviews: sign in on the review page "
                       f"(or `qccd review login {reviewer}` at a terminal), once", status=409, reviewer=reviewer)
        ok, why = container.ready(fresh=True)
        if not ok:
            raise _err("docker", why, status=409)
        rid = new_id("rv")
        d = self.root / ".qccd" / "reviews" / rid
        (d / "input").mkdir(parents=True)
        (d / "input" / "arch.json").write_text(req.pop("arch"), encoding="utf-8")
        (d / "input" / "program.tsir.json").write_text(req.pop("program"), encoding="utf-8")
        _write_json(d / "input" / "model.json", req.pop("model") or {"name": "corrected"})
        _write_json(d / "request.json", {**req, "reviewer": reviewer, "model_choice": model or None, "actor": dict(actor),
                                         "created_at": _now()})
        cancel = threading.Event()
        self._rv_running()[rid] = cancel
        _write_json(d / "state.json", {"status": "queued", "reviewer": reviewer, "model": model or agents.model_of(reviewer),
                                       "progress": [], "created_at": _now(), "pid": os.getpid()})
        threading.Thread(target=self._rv_run, args=(rid, cancel), name=f"qccd-review-{rid}", daemon=True).start()
        return self.review(rid, brief=True)

    def _rv_run(self, rid: str, cancel: threading.Event) -> None:
        from ..review.engine import run_review
        d = self.root / ".qccd" / "reviews" / rid
        lines: list = []
        last = [0.0]
        lock = threading.Lock()

        def on_progress(new: list) -> None:
            with lock:
                for ln in new:
                    if ln and (not lines or lines[-1]["text"] != ln):
                        lines.append({"t": round(_now(), 1), "text": str(ln)[:240]})
                del lines[:-120]
                if _now() - last[0] > 1.0:
                    last[0] = _now()
                    self._rv_set(rid, progress=list(lines))

        def on_check(summary: dict, check: dict) -> None:
            _write_json(d / "summary.json", summary)
            _write_json(d / "check.json", check)
            self._rv_set(rid, status="reviewing")

        try:
            req = _read_json(d / "request.json") or {}
            req.update(arch=(d / "input" / "arch.json").read_text(encoding="utf-8"),
                       program=(d / "input" / "program.tsir.json").read_text(encoding="utf-8"),
                       model=_read_json(d / "input" / "model.json"))
            self._rv_set(rid, status="checking", started_at=_now())
            work = Path(tempfile.gettempdir()) / "qccd-review" / f"{str(self.id)[:8]}-{rid}"
            res = run_review(job=work, request=req, reviewer=req["reviewer"], model=req.get("model_choice"),
                             cancel=cancel, on_progress=on_progress,
                             on_check=on_check, on_agent=lambda info: self._rv_set(rid, model=info.get("model")))
            if res["review_md"]:
                (d / "review.md").write_text(res["review_md"], encoding="utf-8")
            _write_json(d / "notes.json", res["notes"])
            status = "done" if res["status"] == "done" else "cancelled" if cancel.is_set() else "failed"
            with lock:
                final = list(lines)
            self._rv_set(rid, status=status, progress=final, usage=res["usage"], model=res["model"],
                         egress=res["egress"], redacted=res["redacted"], error=res["error"], finished_at=_now())
        except Exception as exc:                 # recorded, never swallowed
            self._rv_set(rid, status="failed", error=f"{type(exc).__name__}: {exc}"[:500], finished_at=_now())
        finally:
            self._rv_running().pop(rid, None)

    def review_again(self, actor: Mapping, rid: str, reviewer: str | None = None, model: str | None = None) -> dict:
        """The same programme, rule set and assertions reviewed again: by the same agent, or another."""
        d = self._rv_dir(rid)
        req = _read_json(d / "request.json") or {}
        request = {**{k: req.get(k) for k in ("title", "rules", "assertions", "link", "steps", "source")},
                   "arch": (d / "input" / "arch.json").read_text(encoding="utf-8"),
                   "program": (d / "input" / "program.tsir.json").read_text(encoding="utf-8"),
                   "model": _read_json(d / "input" / "model.json")}
        same = not reviewer or reviewer == req.get("reviewer")
        # another agent starts from its own default: a model is one agent's alias or id
        return self.start_review(actor, request, reviewer or req.get("reviewer") or "",
                                 model if model is not None else (req.get("model_choice") if same else None))

    def cancel_review(self, actor: Mapping, rid: str) -> dict:
        self._rv_dir(rid)
        ev = self._rv_running().get(rid)
        if ev is not None:
            ev.set()
        return self.review(rid, brief=True)

    # ------------------------------------------------------------------ reading them

    def review(self, rid: str, *, brief: bool = False) -> dict:
        """One review, as its page and an agent read it (`brief`: without the report, the notes and
        every rule's verdict)."""
        from ..review.agents import REVIEWERS
        from ..review.engine import verdict_line
        d = self._rv_dir(rid)
        st = self._rv_state(rid)
        req = _read_json(d / "request.json") or {}
        check = _read_json(d / "check.json")
        rv = req.get("reviewer") or st.get("reviewer")
        out = {"review_id": rid, "title": req.get("title"), "reviewer": rv,
               "reviewer_title": (REVIEWERS.get(rv) or {}).get("title"), "model": st.get("model"),
               "status": st.get("status"), "error": st.get("error"),
               "progress": (st.get("progress") or [])[-14 if brief else -120:],
               "created_at": st.get("created_at"), "started_at": st.get("started_at"),
               "finished_at": st.get("finished_at"), "usage": st.get("usage") or {}, "egress": st.get("egress"),
               "redacted": st.get("redacted") or 0, "rules": req.get("rules"),
               "has_assertions": bool((req.get("assertions") or "").strip()), "link": req.get("link"),
               "steps": req.get("steps"), "source": req.get("source") or {}, "page": f"/reviewview/{rid}"}
        if check:
            out["verdict"] = {"line": verdict_line(check), "failing": check.get("failing") or [],
                              "ruleset": check.get("ruleset")}
        if not brief:
            out["assertions"] = req.get("assertions") or ""
            out["summary"] = _read_json(d / "summary.json")
            out["check"] = check
            out["review_md"] = (d / "review.md").read_text(encoding="utf-8") if (d / "review.md").is_file() else None
            out["notes"] = _read_json(d / "notes.json") or []
        return out

    def reviews(self, limit: int = 20) -> list:
        base = self.root / ".qccd" / "reviews"
        rows = []
        for p in base.glob("rv_*/state.json") if base.is_dir() else ():
            st = _read_json(p) or {}
            rows.append((st.get("created_at") or 0, p.parent.name))
        rows.sort(reverse=True)
        return [self.review(rid, brief=True) for _, rid in rows[:max(1, min(int(limit), 100))]]

    # ------------------------------------------------------------------ the agents and their sign-ins

    def _signins(self) -> dict:
        return self.__dict__.setdefault("_rv_signins", {})

    def reviewers_view(self) -> dict:
        """Who can review here: Docker (and whether the reviewer image is built yet), each agent
        signed in or not, and the sign-ins in progress."""
        from ..review import agents, container
        ok, why = container.ready()
        return {"docker": {"ready": ok, "why": why or None, "image": container.image_tag(),
                           "built": container.image_built() if ok else False},
                "reviewers": agents.statuses(),
                "signins": [s.view() for s in self._signins().values()
                            if s.state in ("preparing", "waiting", "finishing") or _now() - (s.finished or 0) < 300]}

    def start_signin(self, actor: Mapping, reviewer: str) -> dict:
        """The person signs `reviewer` in from the review page: its CLI's own sign-in, in Docker; the
        page shows the link (and Codex's code) it prints."""
        from ..review import agents, container
        if actor.get("kind") != "human":
            raise _err("forbidden", "only the person signs an agent in", status=403)
        if reviewer not in agents.REVIEWERS:
            raise _err("bad_request", f"the agent is one of {', '.join(agents.REVIEWERS)}")
        for s in self._signins().values():
            if s.rid == reviewer and s.state in ("preparing", "waiting", "finishing"):
                return s.view()
        ok, why = container.ready(fresh=True)
        if not ok:
            raise _err("docker", why, status=409)
        s = agents.SigninSession(reviewer).start()
        self._signins()[s.id] = s
        return s.view()

    def _signin(self, sid: str):
        s = self._signins().get(sid)
        if s is None:
            raise _err("unknown_signin", "no such sign-in", status=404)
        return s

    def signin_view(self, sid: str) -> dict:
        return self._signin(sid).view()

    def signin_code(self, actor: Mapping, sid: str, code: str) -> dict:
        if actor.get("kind") != "human":
            raise _err("forbidden", "only the person signs an agent in", status=403)
        s = self._signin(sid)
        try:
            s.send(code)
        except (RuntimeError, ValueError) as exc:
            raise _err("bad_state", str(exc), status=409) from None
        return s.view()

    def signin_cancel(self, actor: Mapping, sid: str) -> dict:
        s = self._signin(sid)
        s.cancel()
        return s.view()

    def sign_out_reviewer(self, actor: Mapping, reviewer: str) -> dict:
        from ..review import agents
        if actor.get("kind") != "human":
            raise _err("forbidden", "only the person signs an agent out", status=403)
        if reviewer not in agents.REVIEWERS:
            raise _err("bad_request", f"the agent is one of {', '.join(agents.REVIEWERS)}")
        agents.sign_out(reviewer)
        return agents.status(reviewer)

    def _reviews_close(self) -> None:
        """The service is stopping: running reviews and open sign-ins stop with it, and their
        containers go now (a review that was running reads as stopped when the service is back)."""
        busy = bool(self._rv_running())
        for ev in list(self._rv_running().values()):
            ev.set()
        for s in list(self._signins().values()):
            if s.state in ("preparing", "waiting", "finishing"):
                busy = True
                s.cancel()
        if busy:
            from ..review.container import remove_own
            remove_own()
