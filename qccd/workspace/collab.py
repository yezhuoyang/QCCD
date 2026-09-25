"""Collaboration: prompts, comments, threads, agent sessions, Studio views, delivery.

A PROMPT is a persistent, versioned object, not a string.  Its draft autosaves without
invoking anyone; SEND freezes what "this", "here" and "like this" meant -- the revision
the user was looking at, the selection, the anchors resolved to entity records, the
sketches in diagram coordinates with the viewport they were drawn in, a manual
demonstration as a structured before/after diff -- into an immutable context snapshot,
and in the same transaction queues a delivery to the agent session the view is bound to.
Editing a sent prompt makes a new version; it never changes what was already delivered.

DELIVERY and WORK are separate state machines:

    delivery   queued -> sending -> accepted | uncertain | failed      (+ cancelled)
    work       pending -> working -> waiting_input | ready_for_review -> resolved
               (+ cancelled)

A transport write is not acceptance: a Claude Code channel notification is `uncertain`
until the agent reads the prompt through a tool, and a Codex `turn/start` is `accepted`
because the runtime answered with a turn id.  Neither is proof the model finished.

Agent replies never create deliveries, so a reply cannot trigger another agent turn.
"""

from __future__ import annotations

import copy
import time
from typing import Any, Iterable, Mapping

from .design import entities
from .jsonsafe import canonical_text, check_value, digest, Limits
from .store import dumps, loads

__all__ = ["CollabMixin", "ANCHOR_KINDS", "INTENTS", "PROMPT_MODES", "delivery_text"]

ANCHOR_KINDS = ("entity", "entity_group", "region", "point", "instruction", "circuit_op",
                "field", "metric", "diagnostic", "event", "panel", "workspace", "page")
#: `comment` is a remark or preference and is never delivered; the others are requests
INTENTS = ("comment", "question", "propose_change", "apply_change", "constraint")
PROMPT_MODES = ("ask", "propose", "apply")
DELIVERY_STATES = ("queued", "sending", "accepted", "uncertain", "failed", "cancelled")
WORK_STATES = ("pending", "working", "waiting_input", "ready_for_review", "resolved", "cancelled")

_TRANSITIONS = {
    "queued": {"sending", "cancelled", "failed", "accepted"},
    "sending": {"accepted", "uncertain", "failed", "queued", "cancelled"},
    "uncertain": {"accepted", "failed", "queued", "cancelled"},
    "accepted": set(),
    "failed": {"queued"},
    "cancelled": set(),
}

_TEXT_LIMIT = 8000
_SMALL = Limits(max_bytes=512 * 1024, max_depth=24, max_items=20000, max_string=_TEXT_LIMIT)


def _now() -> float:
    return time.time()


class CollabMixin:
    """Mixed into `app.Workspace`; relies on `WorkspaceCore` for store/events/revisions."""

    # ------------------------------------------------------------------ views (Studio tabs)

    def register_view(self, actor: Mapping, *, branch: str = "main", label: str = "",
                      page: Mapping | None = None) -> dict:
        from .core import WorkspaceError, new_id
        if actor.get("kind") != "human":
            raise WorkspaceError("forbidden", "only a Studio page registers a view", status=403)
        self.branch(branch)
        vid = new_id("v")
        now = _now()
        state = {"rendered_revision": None, "selection": [], "viewport": None,
                 "displayed_run": None, "frame": None, "page": _page_info(page)}
        with self.store.tx() as db:
            db.execute("INSERT INTO views(id, branch, label, follow_agent, target_session, state, "
                       "created_at, last_seen) VALUES(?,?,?,?,?,?,?,?)",
                       (vid, branch, label[:80], 1, self._default_session(db), dumps(state), now, now))
            self._emit(db, "view.opened", {"view_id": vid, "branch": branch}, branch)
        return self.view(vid)

    def view(self, view_id: str) -> dict:
        from .core import WorkspaceError
        r = self.store.one("SELECT * FROM views WHERE id=?", (view_id,))
        if r is None:
            raise WorkspaceError("unknown_view", f"no view {view_id!r}", status=404)
        d = dict(r)
        d["state"] = loads(d["state"])
        d["follow_agent"] = bool(d["follow_agent"])
        d["connected"] = (_now() - d["last_seen"]) < 45 and not d["closed"]
        return d

    def views(self) -> list:
        return [self.view(r["id"]) for r in self.store.all("SELECT id FROM views WHERE closed=0 "
                                                             "ORDER BY last_seen DESC")]

    def update_view(self, view_id: str, patch: Mapping, actor: Mapping) -> dict:
        from .core import WorkspaceError
        v = self.view(view_id)
        if actor.get("kind") != "human":
            raise WorkspaceError("forbidden", "views are updated by their page", status=403)
        check_value(dict(patch), _SMALL)
        state = dict(v["state"])
        for k in ("rendered_revision", "selection", "viewport", "displayed_run", "frame"):
            if k in patch:
                state[k] = patch[k]
        if "page" in patch:
            state["page"] = _page_info(patch["page"])
        sets, args = ["state=?", "last_seen=?"], [dumps(state), _now()]
        if "follow_agent" in patch:
            sets.append("follow_agent=?")
            args.append(1 if patch["follow_agent"] else 0)
        if "target_session" in patch:
            sid = patch["target_session"]
            if sid is not None:
                self.session(sid)
            sets.append("target_session=?")
            args.append(sid)
        if "branch" in patch:
            self.branch(patch["branch"])
            sets.append("branch=?")
            args.append(patch["branch"])
        if "closed" in patch:                         # a page that navigates closes, then reopens, its view
            sets.append("closed=?")
            args.append(1 if patch["closed"] else 0)
        with self.store.tx() as db:
            db.execute(f"UPDATE views SET {', '.join(sets)} WHERE id=?", (*args, view_id))
            if "rendered_revision" in patch or "target_session" in patch or "follow_agent" in patch \
                    or patch.get("closed") or "page" in patch:
                self._emit(db, "view.updated", {"view_id": view_id,
                                                "rendered_revision": state.get("rendered_revision"),
                                                "target_session": patch.get("target_session", v["target_session"]),
                                                "closed": bool(patch.get("closed"))}, v["branch"])
        return self.view(view_id)

    def page_view(self, actor: Mapping, view_id: str | None = None) -> dict:
        """The page an agent's page action goes to: the one named, else the page the request
        it is working on was sent from, else the page the person used last."""
        from .core import WorkspaceError
        if view_id:
            v = self.view(view_id)
            if not v["connected"] or not (v["state"].get("page") or {}).get("url"):
                raise WorkspaceError("page_closed", f"view {view_id} is not an open page", status=409)
            return v
        with self.store.lock:
            working = self._working_prompt(self.store.db, actor)
        if working:
            p = self.prompt(working)
            vid = ((p.get("latest_sent") or {}).get("body") or {}).get("view_id")
            if vid:
                try:
                    v = self.view(vid)
                    if v["connected"] and (v["state"].get("page") or {}).get("url"):
                        return v
                except WorkspaceError:
                    pass
        for v in self.views():
            if v["connected"] and (v["state"].get("page") or {}).get("url"):
                return v
        raise WorkspaceError("no_page", "no page is open: the person has no Studio or website page with "
                             "the chat open right now", status=409)

    def pages(self) -> list:
        """The open pages (Studio and website), newest first, for an agent to choose from."""
        out = []
        for v in self.views():
            pg = v["state"].get("page") or {}
            if v["connected"] and pg.get("url"):
                out.append({"view_id": v["id"], "url": pg.get("url"), "title": pg.get("title"),
                            "kind": pg.get("kind"), "site": pg.get("site")})
        return out

    def touch_view(self, view_id: str) -> None:
        with self.store.lock:
            self.store.db.execute("UPDATE views SET last_seen=? WHERE id=?", (_now(), view_id))

    # ------------------------------------------------------------------ agent sessions

    def register_session(self, actor: Mapping, *, client: str, mode: str, label: str = "",
                         runtime_ref: str | None = None, capabilities: Mapping | None = None,
                         branch: str = "main", session_id: str | None = None) -> dict:
        """An agent runtime announcing itself.  `mode`: `appserver` (Codex), `channel`
        (Claude Code channel), `pull` (any MCP client, reduced capability)."""
        from .core import WorkspaceError, new_id
        if client not in ("codex", "claude", "generic"):
            raise WorkspaceError("bad_request", "client must be codex, claude or generic", status=422)
        if mode not in ("appserver", "channel", "pull", "sdk"):
            raise WorkspaceError("bad_request", "mode must be appserver, channel, pull or sdk", status=422)
        caps = dict(capabilities or {})
        now = _now()
        with self.store.tx() as db:
            if session_id and db.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
                db.execute("UPDATE sessions SET status='connected', last_seen=?, capabilities=?, "
                           "runtime_ref=COALESCE(?, runtime_ref), label=COALESCE(NULLIF(?, ''), label) "
                           "WHERE id=?", (now, dumps(caps), runtime_ref, label[:120], session_id))
                sid = session_id
            else:
                sid = session_id or new_id("s")
                db.execute("INSERT INTO sessions(id, client, mode, label, runtime_ref, branch, capabilities, "
                           "status, created_at, last_seen) VALUES(?,?,?,?,?,?,?, 'connected', ?, ?)",
                           (sid, client, mode, label[:120], runtime_ref, branch, dumps(caps), now, now))
                # a view with no target yet adopts the first session that connects
                db.execute("UPDATE views SET target_session=? WHERE target_session IS NULL AND closed=0",
                           (sid,))
            self._emit(db, "session.updated", {"session_id": sid, "status": "connected", "client": client,
                                               "mode": mode, "label": label[:120],
                                               "capabilities": caps}, branch)
        return self.session(sid)

    def session(self, session_id: str) -> dict:
        from .core import WorkspaceError
        r = self.store.one("SELECT * FROM sessions WHERE id=?", (session_id,))
        if r is None:
            raise WorkspaceError("unknown_session", f"no agent session {session_id!r}", status=404)
        d = dict(r)
        d["capabilities"] = loads(d["capabilities"])
        d["write_fence"] = bool(d["write_fence"])
        return d

    def sessions(self) -> list:
        return [self.session(r["id"]) for r in
                self.store.all("SELECT id FROM sessions ORDER BY last_seen DESC")]

    def session_status(self, session_id: str, status: str, *, detail: str = "",
                       runtime_ref: str | None = None, capabilities: Mapping | None = None) -> dict:
        s = self.session(session_id)
        with self.store.tx() as db:
            db.execute("UPDATE sessions SET status=?, last_seen=?, runtime_ref=COALESCE(?, runtime_ref), "
                       "capabilities=COALESCE(?, capabilities) WHERE id=?",
                       (status, _now(), runtime_ref, dumps(capabilities) if capabilities is not None else None,
                        session_id))
            self._emit(db, "session.updated", {"session_id": session_id, "status": status,
                                               "detail": detail[:300], "client": s["client"],
                                               "mode": s["mode"]}, s["branch"])
        return self.session(session_id)

    def heartbeat(self, session_id: str) -> None:
        with self.store.lock:
            self.store.db.execute("UPDATE sessions SET last_seen=? WHERE id=?", (_now(), session_id))

    def _default_session(self, db) -> str | None:
        r = db.execute("SELECT id FROM sessions WHERE status='connected' ORDER BY last_seen DESC LIMIT 1").fetchone()
        return r["id"] if r else None

    def stop_session(self, session_id: str, actor: Mapping, *, reason: str = "stopped by the user",
                     prompt_id: str | None = None) -> dict:
        """STOP: fence the session's writes, cancel its queued deliveries and the jobs its
        prompts started, and ask the runtime to interrupt (the adapter reports whether it
        could).  Committed changes are not rolled back."""
        from .core import WorkspaceError
        if actor.get("kind") != "human":
            raise WorkspaceError("forbidden", "only a person can stop an agent", status=403)
        s = self.session(session_id)
        with self.store.tx() as db:
            db.execute("UPDATE sessions SET write_fence=1, fence_reason=? WHERE id=?", (reason[:200], session_id))
            q = "UPDATE deliveries SET state='cancelled', updated_at=? WHERE session_id=? AND state IN ('queued','failed')"
            args: list = [_now(), session_id]
            if prompt_id:
                q += " AND prompt_id=?"
                args.append(prompt_id)
            db.execute(q, args)
            prompts = [prompt_id] if prompt_id else [r["prompt_id"] for r in db.execute(
                "SELECT DISTINCT prompt_id FROM deliveries WHERE session_id=?", (session_id,)).fetchall()]
            for pid in prompts:
                w = db.execute("SELECT state FROM work WHERE prompt_id=?", (pid,)).fetchone()
                if w and w["state"] not in ("resolved", "cancelled"):
                    db.execute("UPDATE work SET state='cancelled', note=?, updated_at=? WHERE prompt_id=?",
                               (reason[:200], _now(), pid))
                    self._emit(db, "work.updated", {"prompt_id": pid, "state": "cancelled", "note": reason}, s["branch"])
                db.execute("UPDATE jobs SET cancel_requested=1 WHERE origin_prompt_id=? AND status IN ('queued','running')",
                           (pid,))
            self._emit(db, "session.stopped", {"session_id": session_id, "reason": reason,
                                               "prompt_id": prompt_id}, s["branch"])
        return self.session(session_id)

    def resume_session(self, session_id: str, actor: Mapping) -> dict:
        from .core import WorkspaceError
        if actor.get("kind") != "human":
            raise WorkspaceError("forbidden", "only a person can resume an agent", status=403)
        s = self.session(session_id)
        with self.store.tx() as db:
            db.execute("UPDATE sessions SET write_fence=0, fence_reason=NULL WHERE id=?", (session_id,))
            self._emit(db, "session.updated", {"session_id": session_id, "status": s["status"],
                                               "write_fence": False}, s["branch"])
        return self.session(session_id)

    def _check_actor_may_write(self, actor: Mapping, origin_prompt_id: str | None) -> None:
        from .core import WorkspaceError
        if actor.get("kind") != "agent":
            return
        sid = actor.get("session")
        if sid:
            s = self.session(sid)
            if s["write_fence"]:
                raise WorkspaceError("session_stopped",
                                     f"the user stopped this agent session ({s['fence_reason']}); "
                                     f"no further changes are accepted until they resume it", status=403)
        if origin_prompt_id:
            p = self.prompt(origin_prompt_id)
            w = self.store.one("SELECT state FROM work WHERE prompt_id=?", (origin_prompt_id,))
            if w and w["state"] == "cancelled":
                raise WorkspaceError("prompt_cancelled",
                                     f"prompt {origin_prompt_id} was cancelled by the user", status=403)
            if not p.get("latest_sent"):
                raise WorkspaceError("unknown_prompt", f"prompt {origin_prompt_id} was never sent", status=404)

    def _check_mode(self, actor: Mapping, origin_prompt_id: str | None, branch: str, mode: str) -> None:
        """Ask / Propose / Apply locally, enforced: an `ask` prompt may not change the
        design, a `propose` prompt may change only candidate branches."""
        from .core import WorkspaceError
        if actor.get("kind") != "agent" or not origin_prompt_id or mode != "apply":
            return
        p = self.prompt(origin_prompt_id)
        sent = p.get("latest_sent")
        pmode = (sent or {}).get("body", {}).get("mode", "propose")
        if pmode == "ask":
            raise WorkspaceError("ask_only", f"prompt {origin_prompt_id} asks a question; it does not "
                                 f"authorise design changes (answer with a reply)", status=403)
        if pmode == "propose" and branch == "main":
            raise WorkspaceError("propose_only", f"prompt {origin_prompt_id} asks for a proposal: put the "
                                 f"change on a candidate branch (qccd_apply_change_set with branch "
                                 f"'cand/<name>' after creating it) or preview it", status=403)

    # ------------------------------------------------------------------ prompts

    def save_draft(self, actor: Mapping, body: Mapping, *, prompt_id: str | None = None) -> dict:
        """Autosave: create or overwrite the current DRAFT version.  Never delivers."""
        from .core import WorkspaceError, new_id
        b = self._check_body(body)
        now = _now()
        with self.store.tx() as db:
            if prompt_id:
                cur = db.execute("SELECT * FROM notes WHERE id=? ORDER BY version DESC LIMIT 1",
                                 (prompt_id,)).fetchone()
                if cur is None:
                    raise WorkspaceError("unknown_prompt", f"no prompt {prompt_id!r}", status=404)
                if cur["status"] == "draft":
                    db.execute("UPDATE notes SET body=?, updated_at=? WHERE id=? AND version=?",
                               (dumps(b), now, prompt_id, cur["version"]))
                    return {"prompt_id": prompt_id, "version": cur["version"], "status": "draft"}
                version = cur["version"] + 1
                b.setdefault("correction_of", cur["version"])
            else:
                prompt_id, version = new_id("p"), 1
            db.execute("INSERT INTO notes(id, version, kind, thread, status, author, body, created_at, "
                       "updated_at) VALUES(?,?,?,?, 'draft', ?, ?, ?, ?)",
                       (prompt_id, version, "comment" if b["intent"] == "comment" else "prompt", None,
                        dumps(dict(actor)), dumps(b), now, now))
        return {"prompt_id": prompt_id, "version": version, "status": "draft"}

    def _check_body(self, body: Mapping) -> dict:
        from .core import WorkspaceError
        if not isinstance(body, Mapping):
            raise WorkspaceError("bad_request", "prompt body must be an object", status=422)
        check_value(dict(body), _SMALL)
        text = body.get("text", "")
        if not isinstance(text, str) or len(text) > _TEXT_LIMIT:
            raise WorkspaceError("bad_request", f"text must be a string of at most {_TEXT_LIMIT} characters",
                                 status=422)
        intent = body.get("intent", "propose_change")
        if intent not in INTENTS:
            raise WorkspaceError("bad_request", f"intent must be one of {INTENTS}", status=422)
        mode = body.get("mode", {"question": "ask", "apply_change": "apply"}.get(intent, "propose"))
        if mode not in PROMPT_MODES:
            raise WorkspaceError("bad_request", f"mode must be one of {PROMPT_MODES}", status=422)
        anchors = body.get("anchors") or []
        if not isinstance(anchors, list) or len(anchors) > 200:
            raise WorkspaceError("bad_request", "anchors must be a list of at most 200", status=422)
        for a in anchors:
            if not isinstance(a, Mapping) or a.get("kind") not in ANCHOR_KINDS:
                raise WorkspaceError("bad_request", f"anchor kind must be one of {ANCHOR_KINDS}", status=422)
        sketches = body.get("sketches") or []
        if not isinstance(sketches, list) or len(sketches) > 50:
            raise WorkspaceError("bad_request", "sketches must be a list of at most 50", status=422)
        for s in sketches:
            if not isinstance(s, Mapping) or s.get("kind") not in ("stroke", "arrow", "lasso"):
                raise WorkspaceError("bad_request", "a sketch is {kind: stroke|arrow|lasso, points}", status=422)
            pts = s.get("points")
            if not isinstance(pts, list) or not 1 <= len(pts) <= 2000 or not all(
                    isinstance(p, list) and len(p) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                                                for x in p) for p in pts):
                raise WorkspaceError("bad_request", "sketch points must be [[x, y], ...] (at most 2000)", status=422)
        out = {"text": text, "intent": intent, "mode": mode, "anchors": copy.deepcopy(anchors),
               "sketches": copy.deepcopy(sketches)}
        for k in ("demonstration", "correction_of", "steer", "title"):
            if k in body:
                out[k] = copy.deepcopy(body[k])
        return out

    def send_prompt(self, actor: Mapping, *, prompt_id: str | None = None, body: Mapping | None = None,
                    view_id: str | None = None, context: Mapping | None = None,
                    request_id: str | None = None) -> dict:
        """SEND: freeze the context, make the version immutable, queue the delivery --
        one transaction.  Only a person sends; imported text and agent replies cannot."""
        from .core import WorkspaceError, new_id
        if actor.get("kind") != "human":
            raise WorkspaceError("forbidden", "only a person can send a prompt to the agent", status=403)
        if request_id:
            prior = self.store.one("SELECT response FROM idempotency WHERE key=?", (f"send:{request_id}",))
            if prior:
                out = loads(prior["response"])
                out["duplicate"] = True
                return out
        if prompt_id is None:
            if body is None:
                raise WorkspaceError("bad_request", "send needs a prompt_id or a body", status=422)
            d = self.save_draft(actor, body)
            prompt_id = d["prompt_id"]
        elif body is not None:
            self.save_draft(actor, body, prompt_id=prompt_id)
        cur = self.store.one("SELECT * FROM notes WHERE id=? ORDER BY version DESC LIMIT 1", (prompt_id,))
        if cur is None or cur["status"] != "draft":
            raise WorkspaceError("not_draft", f"prompt {prompt_id} has no unsent draft (edit it to make a new "
                                 f"version)", status=409)
        b = loads(cur["body"])
        view = self.view(view_id) if view_id else None
        branch = (view or {}).get("branch") or "main"
        ctx = self._freeze_context(b, branch, view, context or {})
        target = (context or {}).get("target_session") or (view or {}).get("target_session")
        if target:
            self.session(target)
        now = _now()
        result: dict = {"prompt_id": prompt_id, "version": cur["version"], "status": "sent",
                        "context_snapshot_id": ctx["id"], "design_revision": ctx["design_revision"],
                        "branch": branch, "delivery": None, "work_state": None}
        with self.store.tx() as db:
            db.execute("INSERT INTO context_snapshots(id, payload, created_at) VALUES(?,?,?)",
                       (ctx["id"], dumps(ctx), now))
            b["context_snapshot_id"] = ctx["id"]
            b["design_revision"] = ctx["design_revision"]
            b["branch"] = branch
            b["view_id"] = view_id
            b["target_session"] = target
            if (ctx.get("page") or {}).get("site"):
                b["page_title"] = ctx["page"].get("title") or ctx["page"].get("url")
            status = "posted" if b["intent"] == "comment" else "sent"
            db.execute("UPDATE notes SET status=?, body=?, updated_at=? WHERE id=? AND version=?",
                       (status, dumps(b), now, prompt_id, cur["version"]))
            result["status"] = status
            if status == "sent":
                if b["intent"] == "constraint":
                    pass  # applied through the change-set path by the caller
                elif target:
                    kind = "steer" if b.get("steer") else "deliver"
                    did = new_id("dlv")
                    db.execute("INSERT INTO deliveries(id, prompt_id, prompt_version, session_id, kind, state, "
                               "attempts, next_attempt_at, created_at, updated_at) "
                               "VALUES(?,?,?,?,?, 'queued', 0, ?, ?, ?)",
                               (did, prompt_id, cur["version"], target, kind, now, now, now))
                    result["delivery"] = {"id": did, "state": "queued", "session_id": target, "kind": kind}
                    # an older version still waiting in the queue is superseded, not sent twice
                    db.execute("UPDATE deliveries SET state='cancelled', last_error='superseded by version ' || ?, "
                               "updated_at=? WHERE prompt_id=? AND prompt_version<? AND state IN ('queued','failed')",
                               (cur["version"], now, prompt_id, cur["version"]))
                db.execute("INSERT INTO work(prompt_id, state, note, updated_at) VALUES(?, 'pending', NULL, ?) "
                           "ON CONFLICT(prompt_id) DO UPDATE SET state='pending', updated_at=excluded.updated_at",
                           (prompt_id, now))
                result["work_state"] = "pending"
            if request_id:
                db.execute("INSERT INTO idempotency(key, request_digest, response, created_at) VALUES(?,?,?,?)",
                           (f"send:{request_id}", digest([prompt_id, cur["version"]]), dumps(result), now))
            self._emit(db, "prompt.sent" if status == "sent" else "comment.posted",
                       {"prompt_id": prompt_id, "version": cur["version"], "intent": b["intent"],
                        "mode": b["mode"], "text": b["text"][:500], "anchors": b["anchors"][:20],
                        "view_id": view_id, "context_snapshot_id": ctx["id"],
                        "delivery": result["delivery"], "author": dict(actor)}, branch, ctx["design_revision"])
        self.notify()
        return result

    def _freeze_context(self, body: Mapping, branch: str, view: Mapping | None, ctx: Mapping) -> dict:
        """What the prompt MEANT, captured now.  Later scrolling, selecting or editing does
        not change it: anchors are resolved to entity records at the revision the user was
        looking at, not re-resolved later."""
        from .core import WorkspaceError, new_id
        head = self.branch(branch)["head"]
        rev = ctx.get("design_revision")
        if rev is None:
            rev = ((view or {}).get("state") or {}).get("rendered_revision")
        if rev is None:
            rev = head
        if not isinstance(rev, int) or not 0 <= rev <= head:
            raise WorkspaceError("bad_request", f"design_revision {rev!r} is not a revision of {branch}", status=422)
        r = self.replayed(branch, rev)
        ents = entities(r.arch_doc or {})
        resolved = []
        for a in body.get("anchors") or []:
            ra = dict(a)
            keys = list(a.get("keys") or ([a["key"]] if a.get("key") else []))
            if a.get("kind") == "region":
                keys = keys or list(a.get("entities_inside") or [])
            if keys:
                ra["resolved"] = {k: ents.get(k) for k in keys[:200]}
                ra["missing"] = [k for k in keys if k not in ents]
            resolved.append(ra)
        demo = body.get("demonstration")
        demo_out = None
        if isinstance(demo, Mapping) and demo.get("before_revision") is not None:
            from .design import diff_designs
            try:
                b0 = int(demo["before_revision"])
                b1 = int(demo.get("after_revision", rev))
                d = diff_designs(self.replayed(branch, b0), self.replayed(branch, b1))
                css = self._change_sets_between(branch, b0, b1)
                demo_out = {"before_revision": b0, "after_revision": b1, "diff": d,
                            "change_sets": [{"id": c["id"], "summary": c["summary"],
                                             "operations": c["operations"][:50], "actor": c["actor"]}
                                            for c in css],
                            "targets": list(demo.get("targets") or [])[:200]}
            except (ValueError, TypeError) as exc:
                raise WorkspaceError("bad_request", f"demonstration: {exc}", status=422) from None
        h = self.revision(branch, rev)
        snap = {
            "id": new_id("ctx"), "branch": branch, "design_revision": rev, "head_at_send": head,
            "input_digest": h.input_digest, "arch_digest": r.arch_digest(),
            "view_id": (view or {}).get("id"),
            "selection": list(ctx.get("selection") or ((view or {}).get("state") or {}).get("selection") or [])[:200],
            "viewport": ctx.get("viewport") or ((view or {}).get("state") or {}).get("viewport"),
            "displayed_run": ctx.get("displayed_run") or ((view or {}).get("state") or {}).get("displayed_run"),
            "frame": ctx.get("frame") if ctx.get("frame") is not None else ((view or {}).get("state") or {}).get("frame"),
            "panel": ctx.get("panel"),
            "page": _page_info(ctx.get("page")) or ((view or {}).get("state") or {}).get("page"),
            "anchors": resolved,
            "sketches": list(body.get("sketches") or []),
            "demonstration": demo_out,
            "protected": (h.constraints or {}).get("protected", {}),
            "coordinates": "sketch and region points are DIAGRAM coordinates (the studio's lattice "
                           "units, the same space as node `pos`); `viewport` is the view box they were "
                           "drawn in. They are intent, not physical constraints.",
        }
        check_value(snap, Limits(max_bytes=4 * 1024 * 1024, max_depth=40, max_items=200000, max_string=_TEXT_LIMIT))
        return snap

    def prompt(self, prompt_id: str) -> dict:
        from .core import WorkspaceError
        rows = self.store.all("SELECT * FROM notes WHERE id=? ORDER BY version", (prompt_id,))
        if not rows:
            raise WorkspaceError("unknown_prompt", f"no prompt {prompt_id!r}", status=404)
        versions = [{"version": r["version"], "status": r["status"], "author": loads(r["author"]),
                     "body": loads(r["body"]), "created_at": r["created_at"], "updated_at": r["updated_at"]}
                    for r in rows]
        sent = [v for v in versions if v["status"] in ("sent", "posted", "resolved")]
        work = self.store.one("SELECT * FROM work WHERE prompt_id=?", (prompt_id,))
        dels = [dict(r) for r in self.store.all("SELECT * FROM deliveries WHERE prompt_id=? ORDER BY created_at",
                                                (prompt_id,))]
        replies = [{"id": r["id"], "author": loads(r["author"]), "body": loads(r["body"]),
                    "created_at": r["created_at"]}
                   for r in self.store.all("SELECT * FROM notes WHERE thread=? AND kind='reply' ORDER BY created_at",
                                           (prompt_id,))]
        links = [dict(r) for r in self.store.all("SELECT * FROM prompt_links WHERE prompt_id=? ORDER BY created_at",
                                                 (prompt_id,))]
        return {"prompt_id": prompt_id, "kind": rows[0]["kind"], "versions": versions,
                "latest": versions[-1], "latest_sent": sent[-1] if sent else None,
                "work": dict(work) if work else None, "deliveries": dels, "replies": replies,
                "links": links}

    def conversation(self, *, limit: int = 40) -> dict:
        """Studio's chat, as one chronological stream: the person's messages, the agent's
        messages, and what the agent DID for each (a change with its revision, a run with its
        time, a side-by-side comparison), plus which requests are still being worked on."""
        rows = self.store.all("SELECT id FROM notes WHERE kind IN ('prompt','comment') AND version=1 "
                              "ORDER BY created_at DESC LIMIT ?", (max(1, min(int(limit), 200)),))
        items, working = [], []
        for r in reversed(rows):
            p = self.prompt(r["id"])
            sent = p["latest_sent"]
            if not sent:
                continue                                  # a draft is not part of the conversation
            b = sent["body"]
            items.append({"type": "user", "prompt_id": p["prompt_id"], "text": b.get("text", ""),
                          "at": sent["created_at"], "branch": b.get("branch"), "context": _context_brief(b),
                          "comment": p["kind"] == "comment", "page": b.get("page_title")})
            for x in p["replies"]:
                a = x["author"]
                items.append({"type": "agent" if a.get("kind") == "agent" else "person", "prompt_id": p["prompt_id"],
                              "text": x["body"].get("text", ""), "author": a.get("label") or a.get("id"),
                              "at": x["created_at"]})
            for link in p["links"]:
                it = self._link_item(link)
                if it:
                    it["prompt_id"] = p["prompt_id"]
                    items.append(it)
            st = (p["work"] or {}).get("state")
            if st in ("pending", "working"):
                working.append({"prompt_id": p["prompt_id"], "state": st})
            elif st == "waiting_input" and (p["work"] or {}).get("note"):
                # the agent's runtime stopped without an answer (a failed turn, a usage limit):
                # the person sees why, instead of a conversation that just goes quiet
                items.append({"type": "notice", "prompt_id": p["prompt_id"], "text": p["work"]["note"],
                              "at": p["work"]["updated_at"]})
        items.sort(key=lambda i: i["at"])
        return {"items": items, "working": working}

    def _link_item(self, link: Mapping) -> dict | None:
        kind, ref, at = link["kind"], link["ref"], link["created_at"]
        try:
            if kind == "change_set":
                c = self.change_set(ref)
                return {"type": "change", "change_set_id": ref, "revision": c.get("revision"),
                        "branch": c.get("branch"), "summary": c.get("summary") or c.get("diff_summary"),
                        "status": c.get("status"), "at": at}
            if kind == "job":
                j = self.job(ref)
                res = j.get("result") or {}
                if j["kind"] == "run":
                    run = res.get("run") or {}
                    return {"type": "run", "run_id": ref, "status": j["status"], "summary": res.get("summary"),
                            "program": (run.get("program") or {}).get("name"),
                            "draft": ((run.get("design") or {}).get("draft") or "").removeprefix("cand/") or None,
                            "design": self._design_title_of((run.get("design") or {}).get("draft") or "main"),
                            "total_ms": ((run.get("performance") or {}).get("total") or {}).get("ms"),
                            "view": run.get("view"), "at": at,
                            "progress": (j.get("progress") or {}).get("message")
                            if j["status"] in ("queued", "running") else None}
                return {"type": "job", "job_id": ref, "kind": j["kind"], "status": j["status"],
                        "summary": res.get("summary"), "at": at}
            if kind == "compare":
                runs = ref.split(",")
                verdict = None
                try:
                    verdict = self.compare_runs(runs)["verdict"]
                except Exception:
                    pass
                return {"type": "compare", "runs": runs, "verdict": verdict, "view": f"/compare?runs={ref}", "at": at}
            if kind == "open_run":
                return {"type": "run_view", "run_id": ref, "view": f"/runview/{ref}", "at": at}
            if kind == "site_comment":
                c = loads(ref)
                return {"type": "site_comment", "page": c.get("page"), "thread": c.get("thread"),
                        "text": c.get("text"), "as": c.get("as"), "kind": c.get("kind"), "at": at}
            if kind == "submission":
                return {"type": "submission", "submission_id": ref, "at": at}
        except Exception:
            return None
        return None

    def context_snapshot(self, ctx_id: str) -> dict:
        from .core import WorkspaceError
        r = self.store.one("SELECT payload FROM context_snapshots WHERE id=?", (ctx_id,))
        if r is None:
            raise WorkspaceError("unknown_context", f"no context snapshot {ctx_id!r}", status=404)
        return loads(r["payload"])

    def list_prompts(self, *, status: Iterable[str] | None = None, limit: int = 100,
                     unresolved_only: bool = False) -> list:
        rows = self.store.all("SELECT id, MAX(version) AS v FROM notes WHERE kind IN ('prompt','comment') "
                              "GROUP BY id ORDER BY MAX(created_at) DESC LIMIT ?", (max(1, min(limit, 500)),))
        out = []
        for r in rows:
            p = self.prompt(r["id"])
            st = p["latest"]["status"]
            if status and st not in status:
                continue
            w = (p["work"] or {}).get("state")
            if unresolved_only and w in ("resolved", "cancelled"):
                continue
            out.append(_prompt_brief(p))
        return out

    def reply(self, actor: Mapping, prompt_id: str, text: str, *, work_state: str | None = None,
              links: Mapping | None = None, request_id: str | None = None, via: str | None = None) -> dict:
        """A reply in the prompt's thread.  From an agent it is a reply ONLY: it never
        queues a delivery, so replies cannot trigger turns."""
        from .core import WorkspaceError, new_id
        p = self.prompt(prompt_id)
        if not isinstance(text, str) or not text.strip() or len(text) > _TEXT_LIMIT:
            raise WorkspaceError("bad_request", f"reply text must be 1..{_TEXT_LIMIT} characters", status=422)
        if work_state is not None and work_state not in WORK_STATES:
            raise WorkspaceError("bad_request", f"work_state must be one of {WORK_STATES}", status=422)
        if work_state == "cancelled" and actor.get("kind") != "human":
            raise WorkspaceError("forbidden", "only a person cancels a request", status=403)
        if request_id:
            prior = self.store.one("SELECT response FROM idempotency WHERE key=?", (f"reply:{request_id}",))
            if prior:
                out = loads(prior["response"])
                out["duplicate"] = True
                return out
        rid = new_id("r")
        now = _now()
        body = {"text": text, "links": dict(links or {}), "work_state": work_state}
        if via:
            body["via"] = via
        check_value(body, _SMALL)
        branch = ((p["latest_sent"] or p["latest"])["body"]).get("branch") or "main"
        with self.store.tx() as db:
            db.execute("INSERT INTO notes(id, version, kind, thread, status, author, body, created_at, updated_at) "
                       "VALUES(?, 1, 'reply', ?, 'posted', ?, ?, ?, ?)",
                       (rid, prompt_id, dumps(dict(actor)), dumps(body), now, now))
            if actor.get("kind") == "agent":
                self._acknowledge(db, prompt_id, actor)
            if work_state:
                db.execute("INSERT INTO work(prompt_id, state, note, updated_at) VALUES(?,?,NULL,?) "
                           "ON CONFLICT(prompt_id) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
                           (prompt_id, work_state, now))
                self._emit(db, "work.updated", {"prompt_id": prompt_id, "state": work_state}, branch)
            out = {"reply_id": rid, "prompt_id": prompt_id, "work_state": work_state}
            if request_id:
                db.execute("INSERT INTO idempotency(key, request_digest, response, created_at) VALUES(?,?,?,?)",
                           (f"reply:{request_id}", digest(body), dumps(out), now))
            self._emit(db, "reply.posted", {"prompt_id": prompt_id, "reply_id": rid, "author": dict(actor),
                                            "text": text[:2000], "links": body["links"],
                                            "work_state": work_state}, branch)
        return out

    def resolve_prompt(self, actor: Mapping, prompt_id: str, *, state: str = "resolved") -> dict:
        from .core import WorkspaceError
        if state not in ("resolved", "cancelled", "ready_for_review", "waiting_input", "working"):
            raise WorkspaceError("bad_request", "bad state", status=422)
        if state == "cancelled" and actor.get("kind") != "human":
            raise WorkspaceError("forbidden", "only a person cancels a request", status=403)
        p = self.prompt(prompt_id)
        branch = ((p["latest_sent"] or p["latest"])["body"]).get("branch") or "main"
        with self.store.tx() as db:
            db.execute("INSERT INTO work(prompt_id, state, note, updated_at) VALUES(?,?,NULL,?) "
                       "ON CONFLICT(prompt_id) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
                       (prompt_id, state, _now()))
            if state == "cancelled":
                db.execute("UPDATE deliveries SET state='cancelled', updated_at=? WHERE prompt_id=? AND "
                           "state IN ('queued','failed')", (_now(), prompt_id))
                db.execute("UPDATE jobs SET cancel_requested=1 WHERE origin_prompt_id=? AND status IN ('queued','running')",
                           (prompt_id,))
            self._emit(db, "work.updated", {"prompt_id": prompt_id, "state": state, "by": dict(actor)}, branch)
        return self.prompt(prompt_id)

    def _working_prompt(self, db, actor: Mapping) -> str | None:
        """The request this agent session is working on now.  Used ONLY to put what the
        agent did (a change, a run, a comparison) into that request's conversation when it
        did not name the request; it never grants or restricts anything."""
        sid = actor.get("session")
        if actor.get("kind") != "agent" or not sid:
            return None
        r = db.execute("SELECT d.prompt_id FROM deliveries d JOIN work w ON w.prompt_id=d.prompt_id "
                       "WHERE d.session_id=? AND w.state IN ('working','pending') "
                       "ORDER BY d.updated_at DESC LIMIT 1", (sid,)).fetchone()
        return r["prompt_id"] if r else None

    def note_site_comment(self, actor: Mapping, info: Mapping) -> None:
        """A comment the agent posted on the website as the person: linked to the request it
        is working on, so the chat shows it."""
        with self.store.tx() as db:
            working = self._working_prompt(db, actor)
            if working:
                self._link_prompt(db, working, "site_comment", dumps(dict(info))[:3000], actor)

    def _link_prompt(self, db, prompt_id: str, what: str, ref: str, actor: Mapping) -> None:
        exists = db.execute("SELECT 1 FROM notes WHERE id=?", (prompt_id,)).fetchone()
        if not exists:
            return
        db.execute("INSERT INTO prompt_links(prompt_id, kind, ref, actor, created_at) VALUES(?,?,?,?,?)",
                   (prompt_id, what, ref, dumps(dict(actor)), _now()))
        self._emit(db, "prompt.linked", {"prompt_id": prompt_id, "kind": what, "ref": ref,
                                         "actor": dict(actor)})

    # ------------------------------------------------------------------ delivery queue

    def acknowledge(self, actor: Mapping, prompt_ids: Iterable[str]) -> list:
        """The agent read these prompts (through a tool): uncertain deliveries become
        accepted and pending work becomes working."""
        out = []
        with self.store.tx() as db:
            for pid in prompt_ids:
                out.append(self._acknowledge(db, pid, actor))
        return out

    def _acknowledge(self, db, prompt_id: str, actor: Mapping) -> dict:
        now = _now()
        sid = actor.get("session")
        q = ("SELECT * FROM deliveries WHERE prompt_id=? AND state IN ('queued','sending','uncertain') "
             + ("AND session_id=?" if sid else ""))
        rows = db.execute(q, (prompt_id, sid) if sid else (prompt_id,)).fetchall()
        for r in rows:
            db.execute("UPDATE deliveries SET state='accepted', updated_at=?, detail=? WHERE id=?",
                       (now, "acknowledged by the agent reading it", r["id"]))
            self._emit(db, "delivery.updated", {"delivery_id": r["id"], "prompt_id": prompt_id,
                                                "state": "accepted", "how": "agent read the prompt"})
        w = db.execute("SELECT state FROM work WHERE prompt_id=?", (prompt_id,)).fetchone()
        if w and w["state"] == "pending":
            db.execute("UPDATE work SET state='working', updated_at=? WHERE prompt_id=?", (now, prompt_id))
            self._emit(db, "work.updated", {"prompt_id": prompt_id, "state": "working"})
        return {"prompt_id": prompt_id, "acknowledged": len(rows)}

    def due_deliveries(self, *, session_id: str | None = None, limit: int = 20) -> list:
        now = _now()
        q = "SELECT * FROM deliveries WHERE state='queued' AND next_attempt_at<=? "
        args: list = [now]
        if session_id:
            q += "AND session_id=? "
            args.append(session_id)
        q += "ORDER BY created_at LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.store.all(q, args)]

    def claim_delivery(self, delivery_id: str) -> dict | None:
        """queued -> sending, atomically; None if someone else took it."""
        with self.store.tx() as db:
            cur = db.execute("UPDATE deliveries SET state='sending', attempts=attempts+1, updated_at=? "
                             "WHERE id=? AND state='queued'", (_now(), delivery_id))
            if cur.rowcount != 1:
                return None
            r = db.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
            self._emit(db, "delivery.updated", {"delivery_id": delivery_id, "prompt_id": r["prompt_id"],
                                                "state": "sending", "attempt": r["attempts"]})
        return dict(r)

    def mark_delivery(self, delivery_id: str, state: str, *, external_ref: str | None = None,
                      error: str | None = None, detail: str | None = None, retry_in: float | None = None) -> dict:
        from .core import WorkspaceError
        if state not in DELIVERY_STATES:
            raise WorkspaceError("bad_request", f"state must be one of {DELIVERY_STATES}", status=422)
        with self.store.tx() as db:
            r = db.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
            if r is None:
                raise WorkspaceError("unknown_delivery", delivery_id, status=404)
            if state != r["state"] and state not in _TRANSITIONS[r["state"]]:
                raise WorkspaceError("bad_transition", f"delivery {delivery_id}: {r['state']} -> {state}", status=409)
            nxt = _now() + (retry_in or 0)
            db.execute("UPDATE deliveries SET state=?, external_ref=COALESCE(?, external_ref), last_error=?, "
                       "detail=COALESCE(?, detail), next_attempt_at=?, updated_at=? WHERE id=?",
                       (state, external_ref, (error or None) and error[:500], (detail or None) and detail[:500],
                        nxt, _now(), delivery_id))
            self._emit(db, "delivery.updated", {"delivery_id": delivery_id, "prompt_id": r["prompt_id"],
                                                "state": state, "external_ref": external_ref,
                                                "error": (error or "")[:300], "detail": (detail or "")[:300]})
            if state == "accepted":
                w = db.execute("SELECT state FROM work WHERE prompt_id=?", (r["prompt_id"],)).fetchone()
                if w and w["state"] == "pending":
                    db.execute("UPDATE work SET state='working', updated_at=? WHERE prompt_id=?",
                               (_now(), r["prompt_id"]))
                    self._emit(db, "work.updated", {"prompt_id": r["prompt_id"], "state": "working"})
        return dict(self.store.one("SELECT * FROM deliveries WHERE id=?", (delivery_id,)))

    def delivery_payload(self, delivery: Mapping) -> dict:
        p = self.prompt(delivery["prompt_id"])
        v = next(x for x in p["versions"] if x["version"] == delivery["prompt_version"])
        ctx = self.context_snapshot(v["body"]["context_snapshot_id"])
        return {"delivery_id": delivery["id"], "prompt_id": p["prompt_id"], "version": v["version"],
                "kind": delivery["kind"], "body": v["body"], "context": ctx,
                "text": delivery_text(self, p["prompt_id"], v, ctx, delivery["id"])}

    def set_work_state(self, prompt_id: str, state: str, note: str = "") -> None:
        with self.store.tx() as db:
            db.execute("INSERT INTO work(prompt_id, state, note, updated_at) VALUES(?,?,?,?) "
                       "ON CONFLICT(prompt_id) DO UPDATE SET state=excluded.state, note=excluded.note, "
                       "updated_at=excluded.updated_at", (prompt_id, state, note[:300], _now()))
            self._emit(db, "work.updated", {"prompt_id": prompt_id, "state": state, "note": note[:300]})

    # ------------------------------------------------------------------ presentation

    def present(self, actor: Mapping, action: str, target: Mapping, *, view_id: str | None = None,
                note: str = "") -> dict:
        """Ask Studio to show something.  The target is validated against the workspace;
        the page decides (Follow agent) whether to move or to show a notification, and
        reports back what it did."""
        from .core import WorkspaceError, new_id
        actions = ("highlight", "select", "open_prompt", "reveal_diagnostic", "select_frame",
                   "open_result", "compare", "open_branch", "open_run")
        if action not in actions:
            raise WorkspaceError("bad_request", f"action must be one of {actions}", status=422)
        check_value(dict(target), _SMALL)
        branch = target.get("branch") or "main"
        self.branch(branch)
        if action in ("highlight", "select"):
            keys = target.get("keys") or []
            ents = entities(self.replayed(branch).arch_doc or {})
            missing = [k for k in keys if k not in ents]
            if not keys or missing:
                raise WorkspaceError("unknown_entity", f"cannot show entities that do not exist: {missing[:5]}",
                                     status=422)
        if action == "open_prompt":
            self.prompt(target.get("prompt_id", ""))
        if action == "open_result":
            self.submission(target.get("submission_id", ""))
        if action == "open_run":
            self.run(target.get("run_id", ""))
        if action == "compare":
            runs = target.get("runs")
            if not isinstance(runs, list) or len(runs) != 2:
                raise WorkspaceError("bad_request", "compare needs target.runs = [run_id, run_id]", status=422)
            for r in runs:
                self.run(r)
        if view_id:
            v = self.view(view_id)
            if v["closed"]:
                raise WorkspaceError("view_closed", f"view {view_id} is closed", status=409)
        everywhere = action in ("open_run", "compare")          # not tied to one draft's tabs
        views = [view_id] if view_id else [v["id"] for v in self.views() if everywhere or v["branch"] == branch]
        pid = new_id("pr")
        with self.store.tx() as db:
            self._emit(db, "present", {"presentation_id": pid, "action": action, "target": dict(target),
                                       "view_id": view_id, "note": note[:300], "actor": dict(actor)}, branch)
            if action in ("compare", "open_run"):
                working = self._working_prompt(db, actor)
                if working:
                    self._link_prompt(db, working, action,
                                      ",".join(target["runs"]) if action == "compare" else target["run_id"], actor)
        connected = [v for v in views if self.view(v)["connected"]]
        return {"presentation_id": pid, "requested_views": views, "connected_views": connected,
                "status": "requested" if connected else "no_connected_view",
                "note": "the page reports displayed/notified through view state; a closed browser "
                        "does not block anything"}

    def presented(self, view_id: str, presentation_id: str, outcome: str) -> None:
        from .core import WorkspaceError
        if outcome not in ("displayed", "notified", "ignored", "failed"):
            raise WorkspaceError("bad_request", "bad outcome", status=422)
        v = self.view(view_id)
        with self.store.tx() as db:
            self._emit(db, "presented", {"presentation_id": presentation_id, "view_id": view_id,
                                         "outcome": outcome}, v["branch"])

    # ------------------------------------------------------------------ context (bootstrap)

    def context(self, actor: Mapping, *, branch: str | None = None, since: int | None = None,
                detail: str = "summary") -> dict:
        """The bootstrap an agent reads at the start of every turn: small by default,
        detail on demand, incremental with `since` (an event cursor)."""
        sid = actor.get("session")
        sess = self.session(sid) if sid else None
        view = None
        for v in self.views():
            if sess and v["target_session"] == sess["id"]:
                view = v
                break
        if view is None:
            vs = self.views()
            view = vs[0] if vs else None
        branch = branch or (view or {}).get("branch") or "main"
        h = self.head(branch)
        r = self.replayed(branch, h.revision)
        diag = self.diagnose(r, limit=20)
        doc = r.arch_doc or {}
        geo = doc.get("geometry") or {}
        unread = []
        pending = self.store.all(
            "SELECT DISTINCT d.prompt_id FROM deliveries d WHERE d.state IN ('queued','sending','uncertain') "
            + ("AND d.session_id=?" if sid else ""), (sid,) if sid else ())
        pend_ids = [x["prompt_id"] for x in pending]
        if not sid:
            pend_ids += [x["prompt_id"] for x in self.store.all(
                "SELECT prompt_id FROM work WHERE state='pending'")]
        for pid in dict.fromkeys(pend_ids):
            p = self.prompt(pid)
            if p.get("latest_sent"):
                b = p["latest_sent"]["body"]
                unread.append({"prompt_id": pid, "version": p["latest_sent"]["version"], "text": b["text"],
                               "intent": b["intent"], "mode": b["mode"],
                               "context_snapshot_id": b.get("context_snapshot_id"),
                               "design_revision": b.get("design_revision"),
                               "anchors": [_anchor_brief(a) for a in b.get("anchors", [])][:20],
                               "sketches": len(b.get("sketches") or []),
                               "demonstration": bool(b.get("demonstration")),
                               "page": b.get("page_title")})
        if unread and actor.get("kind") == "agent":
            self.acknowledge(actor, [u["prompt_id"] for u in unread])
        latest = self.latest_result(branch)
        jobs = self.active_jobs()
        out = {
            "contract": "1",
            "workspace": {"id": self.id, "root_name": self.root.name},
            # the leaderboards any design can be submitted to, by the names people use
            "boards": [{"title": bd["title"], "about": bd["about"], "rank_by": bd["rank_by"]} for bd in self.boards()],
            "mode": diag["physics"]["mode"] if diag.get("physics") else "task",
            "design_title": self.design_title(self.branch(branch)),
            "branch": branch, "revision": h.revision, "input_digest": h.input_digest,
            "arch_digest": h.arch_digest,
            "design": {"name": doc.get("name"), "generator": geo.get("generator"),
                       "nodes": len(geo.get("nodes") or []), "segments": len(geo.get("segments") or []),
                       "loops": len(geo.get("loops") or []), "zones": sorted((doc.get("zone_types") or {})),
                       "program_statements": len(h.state.program),
                       "final_program": h.state.final_program},
            "diagnostics": {"violations": diag["violations"][:10], "warnings": diag["warnings"][:10],
                            "program": diag["program"], "physics": diag["physics"]},
            "protected": [{"key": k, **v} for k, v in (h.constraints.get("protected") or {}).items()][:100],
            "view": None if view is None else {
                "view_id": view["id"], "connected": view["connected"], "follow_agent": view["follow_agent"],
                "rendered_revision": view["state"].get("rendered_revision"),
                "selection": view["state"].get("selection", [])[:50],
                "displayed_run": view["state"].get("displayed_run"), "frame": view["state"].get("frame"),
                "page": view["state"].get("page")},
            "pages": self.pages()[:10],
            "session": None if sess is None else {
                "session_id": sess["id"], "client": sess["client"], "mode": sess["mode"],
                "capabilities": sess["capabilities"], "write_fence": sess["write_fence"],
                "fence_reason": sess["fence_reason"]},
            "unread_prompts": unread,
            "jobs": jobs[:10],
            "latest_result": latest,
            # the person's designs by the names they gave them (tools take the name; `id` is internal)
            "designs": [{"name": self.design_title(b), "id": b["name"], "head": b["head"],
                         "copied_from": self.design_title(self.branch(b["parent"])) if b["parent"] else None}
                        for b in self.branches() if b["status"] == "open"][:30],
            "branches": [{"name": b["name"], "head": b["head"], "status": b["status"], "parent": b["parent"]}
                         for b in self.branches() if b["status"] == "open"][:20],
            "cursor": self.last_event_seq(),
        }
        if since is not None:
            evs = self.events_since(int(since), limit=100)
            out["changes_since"] = {"gap": evs["gap"], "events": [
                {"seq": e["seq"], "type": e["type"], "revision": e["revision"],
                 "summary": _event_brief(e)} for e in evs["events"]
                if e["type"] in ("design.committed", "prompt.sent", "reply.posted", "work.updated",
                                 "job.updated", "submission.updated", "session.stopped")][-50:]}
        else:
            out["recent_changes"] = self.history(branch, limit=5)
        if detail == "full":
            out["entities"] = entity_summary_for(doc, 300)
            out["program"] = h.state.program[:500]
        out["next"] = _hints(out)
        return out

    # hooks filled by ResultsMixin
    def latest_result(self, branch: str) -> dict | None:  # pragma: no cover - overridden
        return None

    def active_jobs(self) -> list:  # pragma: no cover - overridden
        return []


def entity_summary_for(doc: Mapping, limit: int) -> list:
    from .design import entity_summary
    return entity_summary(doc, None, limit)


def _anchor_brief(a: Mapping) -> dict:
    out = {"kind": a.get("kind")}
    for k in ("key", "keys", "bbox", "pos", "instr_id", "op_index", "field", "name", "rule", "panel",
              "entities_inside", "frame", "url", "quote", "element"):
        if k in a:
            v = a[k]
            out[k] = v[:20] if isinstance(v, list) else v
    return out


def _prompt_brief(p: Mapping) -> dict:
    latest = p["latest"]
    b = latest["body"]
    return {"prompt_id": p["prompt_id"], "kind": p["kind"], "version": latest["version"],
            "status": latest["status"], "text": b.get("text", "")[:300], "intent": b.get("intent"),
            "mode": b.get("mode"), "anchors": [_anchor_brief(a) for a in b.get("anchors", [])][:10],
            "work": (p["work"] or {}).get("state"),
            "delivery": (p["deliveries"][-1]["state"] if p["deliveries"] else None),
            "replies": len(p["replies"]), "author": latest["author"].get("kind"),
            "created_at": p["versions"][0]["created_at"]}


def _event_brief(e: Mapping) -> str:
    pl = e["payload"]
    if e["type"] == "design.committed":
        return f"r{e['revision']} {pl.get('actor', {}).get('kind')}: {pl.get('summary')}"
    if e["type"] in ("prompt.sent",):
        return f"prompt {pl.get('prompt_id')} v{pl.get('version')}: {pl.get('text', '')[:120]}"
    if e["type"] == "reply.posted":
        return f"reply on {pl.get('prompt_id')} by {pl.get('author', {}).get('kind')}"
    return canonical_text(pl)[:160]


def _hints(ctx: Mapping) -> list:
    out = []
    if ctx["unread_prompts"]:
        out.append("Unread Studio prompts are listed in unread_prompts; read each one's context with "
                   "qccd_manage_comment(action='get', prompt_id=...) and answer in its thread.")
    if ctx["session"] and ctx["session"]["write_fence"]:
        out.append("This session was stopped by the user: do not attempt changes; reply and wait.")
    if ctx["protected"]:
        out.append("Protected entities are enforced by the service; changes touching them are refused.")
    lr = ctx.get("latest_result")
    if lr and lr.get("stale"):
        out.append("The latest local result describes an older revision; re-run before citing it.")
    if ctx.get("mode") == "exploratory":
        out.append("The design changes the task's fixed physics: it is exploratory and cannot be an "
                   "eligible submission.")
    out.append("Change the design only with qccd_apply_change_set, passing expected_revision="
               f"{ctx['revision']}; preview first for anything non-trivial.")
    return out


def delivery_text(ws, prompt_id: str, version: Mapping, ctx: Mapping, delivery_id: str) -> str:
    """The message an agent runtime receives.  The user's words are quoted as data; the
    instructions around them are the workspace's, and point at tools rather than
    repeating context the agent can fetch."""
    b = version["body"]
    lines = [f"[QCCD Studio] The user sent a request from Studio: prompt {prompt_id} "
             f"v{version['version']} (workspace {ws.id}, branch {ctx['branch']}, design revision "
             f"{ctx['design_revision']}, intent {b['intent']}, mode {b['mode']}).",
             "User's words (verbatim):", _quote(b.get("text", ""))]
    pg = ctx.get("page") or {}
    if pg.get("url") and pg.get("site"):
        lines.append(f"The person is reading the website page \"{pg.get('title') or pg['url']}\" ({pg['url']}, "
                     f"a {pg.get('kind') or 'page'} page of {pg['site']}, served through the workspace). A question "
                     "is most likely about that page: read it with qccd_page_read (by section for long pages) and "
                     "answer from what it says; show things on it with qccd_page_act (scroll, highlight with a "
                     "note, click, fill, navigate, step an animation, open_lesson).")
    elif pg.get("url"):
        lines.append("The person is in Studio. qccd_page_read and qccd_page_act reach its controls too "
                     "(the transport, the panels, the lessons).")
    if b.get("anchors"):
        lines.append("Anchored to: " + "; ".join(_describe_anchor(a) for a in b["anchors"][:8]))
    if b.get("sketches"):
        lines.append(f"Sketches: {len(b['sketches'])} ("
                     + ", ".join(s.get("kind", "?") for s in b["sketches"][:6])
                     + ") in diagram coordinates, kept as intent.")
    if ctx.get("demonstration"):
        d = ctx["demonstration"]
        lines.append(f"Demonstration: the user edited revision {d['before_revision']} -> "
                     f"{d['after_revision']} ({d['diff'].get('summary')}); generalise it to the targets.")
    if b.get("correction_of"):
        lines.append(f"This is version {version['version']}, a correction of version {b['correction_of']}.")
    lines.append(f"Context snapshot {ctx['id']} froze what the user meant; read it with "
                 f"qccd_manage_comment(action='get', prompt_id='{prompt_id}') before acting "
                 f"(the design may have changed since revision {ctx['design_revision']}).")
    if b["mode"] == "ask":
        lines.append("Mode ask: answer; do not change the design.")
    elif b["mode"] == "propose":
        lines.append("Mode propose: put changes on a draft or preview them; do not commit to main.")
    else:
        lines.append(f"Mode apply: you may commit validated changes to {ctx['branch']}.")
    d = ws.store.one("SELECT s.mode FROM deliveries d JOIN sessions s ON s.id=d.session_id WHERE d.id=?",
                     (delivery_id,))
    if d and d["mode"] == "appserver":
        lines.append("This is a chat: your messages in this turn are shown to the person in Studio as the "
                     "conversation. Answer there directly and plainly, like a colleague; do not also post "
                     f"qccd_manage_comment replies for it. Pass origin_prompt_id='{prompt_id}' on change sets, "
                     f"jobs and runs. (delivery {delivery_id})")
    else:
        lines.append(f"Reply with qccd_manage_comment(action='reply', prompt_id='{prompt_id}', ...) and pass "
                     f"origin_prompt_id='{prompt_id}' on change sets, jobs and runs. (delivery {delivery_id})")
    return "\n".join(lines)


def _context_brief(b: Mapping) -> str:
    """What the person attached to a message, in a few words ("2 parts, a lasso")."""
    parts = []
    anchors = [a for a in (b.get("anchors") or []) if a.get("kind") != "workspace"]
    ents = sum(len(a.get("keys") or []) or (1 if a.get("key") else 0) for a in anchors
               if a.get("kind") in ("entity", "entity_group"))
    if ents:
        parts.append(f"{ents} part{'s' if ents != 1 else ''}")
    for a in anchors:
        if a.get("kind") == "page" and a.get("quote"):
            parts.append("a quote")
        elif a.get("kind") == "page" and a.get("element"):
            parts.append("something on the page")
    if any(a.get("kind") == "region" for a in anchors):
        parts.append("a lasso")
    if any(a.get("kind") == "point" for a in anchors):
        parts.append("a point")
    if b.get("sketches"):
        parts.append(f"{len(b['sketches'])} sketch{'es' if len(b['sketches']) != 1 else ''}")
    if b.get("demonstration"):
        parts.append("a demonstration")
    return ", ".join(parts)


def _quote(text: str) -> str:
    return "\n".join("> " + ln for ln in (text or "(no text)").splitlines()) or "> (no text)"


def _describe_anchor(a: Mapping) -> str:
    k = a.get("kind")
    if k == "entity":
        return f"entity {a.get('key')}"
    if k == "entity_group":
        keys = a.get("keys") or []
        return f"{len(keys)} entities ({', '.join(keys[:6])}{'...' if len(keys) > 6 else ''})"
    if k == "region":
        inside = a.get("entities_inside") or []
        return f"a region ({len(inside)} entities inside)"
    if k == "point":
        return f"a point at {a.get('pos')}"
    if k == "instruction":
        return f"instruction {a.get('instr_id')}"
    if k == "circuit_op":
        return f"circuit statement {a.get('op_index')}"
    if k == "field":
        return f"field {a.get('panel')}.{a.get('field')}"
    if k == "metric":
        return f"metric {a.get('name')}"
    if k == "diagnostic":
        return f"diagnostic {a.get('rule')}"
    if k == "event":
        return f"simulation frame {a.get('frame')}"
    if k == "page":
        if a.get("quote"):
            return f"this text on the page: \"{str(a['quote'])[:600]}\""
        el = a.get("element") or {}
        return (f"the element {el.get('ref')} on the page ({el.get('tag')}: \"{str(el.get('text') or '')[:200]}\""
                + (f", in section \"{el.get('section')}\"" if el.get("section") else "") + ")")
    return k or "?"


def _page_info(page) -> dict | None:
    """What a view or a message says about its page, trimmed to plain short strings."""
    if not isinstance(page, Mapping):
        return None
    out = {}
    for k, n in (("url", 400), ("title", 200), ("kind", 60), ("site", 120), ("selection", 1200), ("in_view", 200)):
        v = page.get(k)
        if isinstance(v, str) and v:
            out[k] = v[:n]
    return out or None
