"""The local workspace service: HTTP commands + server-sent events on loopback.

Studio pages, MCP adapters and the CLI all talk to ONE of these per workspace; none of
them holds its own copy of the design.  It is a thin translation onto `app.Workspace`.

Security model (localhost is not trusted by itself):

* binds 127.0.0.1 only; every request's Host must be `127.0.0.1:<port>` or
  `localhost:<port>` (defeats DNS rebinding);
* no CORS headers at all -- a page from another origin cannot read a response;
* a BROWSER authenticates with an HttpOnly, SameSite=Strict cookie it gets by pairing
  with a one-time code, and every state-changing request must also carry
  `X-QCCD-CSRF` (read same-origin from `/api/whoami`) and, when present, a same-origin
  `Origin` -- so a cross-site form or fetch cannot act as the user;
* an ADAPTER or the CLI authenticates with a bearer token from the runtime file outside
  the project (`runtime.py`); agent tokens can never approve publication, unprotect a
  person's lock, send a prompt, stop or resume a session;
* bodies are size-limited and parsed by `jsonsafe.strict_loads` (duplicate keys, NaN,
  depth) before any handler sees them.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from .app import Workspace, WorkspaceError
from .jsonsafe import JSONRejected, Limits, strict_loads

__all__ = ["create_app", "serve", "ServiceState"]

_WEB = Path(__file__).resolve().parent / "web"
_BODY_LIMIT = 8 * 1024 * 1024
_COOKIE = "qccd_{ws}"


_PAIRING_TTL = 14 * 86400                  # a paired page older than this pairs again


class ServiceState:
    def __init__(self, ws: Workspace, info: dict, *, sticky: bool = False):
        self.ws = ws
        self.info = info
        self.sticky = sticky                 # keep pairings in the runtime dir across restarts
        self.pair_codes: dict = {}           # code -> expiry
        self.cookies: dict = {}              # sha256(cookie) -> {csrf, created}
        if sticky:
            from .runtime import read_sticky
            now = time.time()
            for k, v in (read_sticky(ws.id).get("pairings") or {}).items():
                if isinstance(v, dict) and isinstance(v.get("csrf"), str) and now - float(v.get("created", 0)) < _PAIRING_TTL:
                    self.cookies[k] = {"csrf": v["csrf"], "created": float(v["created"])}
        self.page_cache: dict = {}
        self.page_lock = threading.Lock()
        self.stop = threading.Event()
        self.bridges: dict = {}              # session_id -> adapter bridge (Codex)
        self.page_waits: dict = {}           # page action id -> {event, view, result}
        self.started = time.time()
        from .trace import Traces
        self.traces = Traces(ws.root, ws.store, ws)   # what each agent did, step by step (trace.py)

    def new_pair_code(self, ttl: float = 300.0) -> str:
        code = secrets.token_urlsafe(18)
        self.pair_codes[code] = time.time() + ttl
        return code

    def add_pairing(self, cookie_hash: str, csrf: str) -> None:
        self.cookies[cookie_hash] = {"csrf": csrf, "created": time.time()}
        if self.sticky:
            from .runtime import write_sticky
            write_sticky(self.ws.id, pairings=self.cookies)


def create_app(state: ServiceState) -> FastAPI:
    ws = state.ws
    port = state.info["port"]
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    cookie_name = _COOKIE.format(ws=ws.id)
    app = FastAPI(title="QCCD workspace", docs_url=None, redoc_url=None, openapi_url=None)

    # ------------------------------------------------------------------ guards

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host") not in allowed_hosts:
            return JSONResponse({"error": {"code": "bad_host", "message": "unexpected Host header"}},
                                status_code=421)
        origin = request.headers.get("origin")
        if origin is not None and origin not in allowed_origins:
            return JSONResponse({"error": {"code": "bad_origin", "message": "cross-origin request refused"}},
                                status_code=403)
        cl = request.headers.get("content-length")
        if cl is not None and cl.isdigit() and int(cl) > _BODY_LIMIT:
            return JSONResponse({"error": {"code": "too_large", "message": "request body too large"}},
                                status_code=413)
        resp = await call_next(request)
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if "x-frame-options" not in resp.headers and not getattr(request.state, "frameable", False):
            resp.headers["X-Frame-Options"] = "DENY"
        return resp

    def err(e: WorkspaceError) -> JSONResponse:
        return JSONResponse({"error": e.to_json()}, status_code=e.status)

    def principal(request: Request, *, write: bool) -> dict:
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            tok = auth[7:]
            if secrets.compare_digest(tok, state.info["agent_token"]):
                sid = request.headers.get("x-qccd-session")
                actor = {"kind": "agent", "id": f"agent:{sid or 'mcp'}"}
                if sid:
                    try:
                        s = ws.session(sid)
                        actor["session"] = sid
                        actor["label"] = s.get("label") or s["client"]
                        ws.heartbeat(sid)
                        if s["status"] != "connected" and s["mode"] in ("pull", "channel"):
                            ws.session_status(sid, "connected", detail="the adapter is making requests again")
                    except WorkspaceError:
                        raise WorkspaceError("unknown_session", "X-QCCD-Session names no session", status=401)
                return actor
            if secrets.compare_digest(tok, state.info["owner_token"]):
                return {"kind": "human", "id": "cli", "label": "terminal"}
            raise WorkspaceError("unauthorized", "bad token", status=401)
        c = request.cookies.get(cookie_name)
        if c:
            rec = state.cookies.get(hashlib.sha256(c.encode()).hexdigest())
            if rec:
                if write:
                    csrf = request.headers.get("x-qccd-csrf", "")
                    if not secrets.compare_digest(csrf, rec["csrf"]):
                        raise WorkspaceError("csrf", "missing or wrong X-QCCD-CSRF header", status=403)
                vid = request.headers.get("x-qccd-view")
                actor = {"kind": "human", "id": f"view:{vid or 'studio'}", "label": "Studio"}
                if vid:
                    actor["view"] = vid
                return actor
        raise WorkspaceError("unauthorized", "pair this page first (open the link `qccd studio` printed)",
                             status=401)

    async def body(request: Request, limit: Limits = Limits(max_bytes=_BODY_LIMIT)) -> Any:
        raw = await request.body()
        if not raw:
            return {}
        try:
            return strict_loads(raw, limit)
        except JSONRejected as exc:
            raise WorkspaceError(exc.code, str(exc), status=422, detail=exc.to_json()) from None

    def route(method: str, path: str, *, write: bool = True, human_only: bool = False, raw: bool = False):
        def deco(fn):
            async def handler(request: Request):
                try:
                    actor = principal(request, write=write)
                    if human_only and actor["kind"] != "human":
                        raise WorkspaceError("forbidden", "this needs a person, not an agent", status=403)
                    payload = await body(request) if method in ("POST", "PATCH", "PUT") else {}
                    if not isinstance(payload, dict):
                        raise WorkspaceError("bad_request", "the body must be a JSON object", status=422)
                    out = await asyncio.to_thread(fn, request, actor, payload)
                    if raw:
                        return out
                    return JSONResponse(out)
                except WorkspaceError as e:
                    return err(e)
            handler.__name__ = fn.__name__
            app.add_api_route(path, handler, methods=[method])
            return fn
        return deco

    def q(request: Request, name: str, default=None, cast=str):
        v = request.query_params.get(name)
        if v is None:
            return default
        try:
            return cast(v)
        except (TypeError, ValueError):
            raise WorkspaceError("bad_request", f"query parameter {name!r} is malformed", status=422)

    # ------------------------------------------------------------------ open endpoints

    @app.get("/api/health")
    async def health():
        return {"ok": True, "workspace_id": ws.id, "pid": os.getpid(), "task": ws.release.id,
                "uptime_s": round(time.time() - state.started, 1)}

    @app.post("/api/pair")
    async def pair(request: Request):
        try:
            b = await body(request, Limits(max_bytes=4096))
        except WorkspaceError as e:
            return err(e)
        code = (b or {}).get("code") if isinstance(b, dict) else None
        exp = state.pair_codes.pop(code, None) if isinstance(code, str) else None
        if not exp or exp < time.time():
            return JSONResponse({"error": {"code": "bad_code", "message": "the pairing code is unknown, used "
                                           "or expired: run `qccd studio` again"}}, status_code=403)
        cookie = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        state.add_pairing(hashlib.sha256(cookie.encode()).hexdigest(), csrf)
        resp = JSONResponse({"ok": True, "csrf": csrf, "workspace_id": ws.id})
        resp.set_cookie(cookie_name, cookie, httponly=True, samesite="strict", path="/")
        return resp

    # ------------------------------------------------------------------ pages

    @app.get("/studio")
    async def studio(request: Request):
        try:
            html = await asyncio.to_thread(_studio_page, state, None)
        except WorkspaceError as e:
            return err(e)
        except Exception as e:                      # say why on the page, not a bare 500
            return _page_failed(state, e)
        return HTMLResponse(html, headers={"Content-Security-Policy": _CSP})

    @app.get("/run/{snapshot_id}")
    async def run_page(snapshot_id: str, request: Request):
        try:
            principal(request, write=False)
            html = await asyncio.to_thread(_studio_page, state, snapshot_id)
        except WorkspaceError as e:
            return err(e)
        return HTMLResponse(html, headers={"Content-Security-Policy": _CSP})

    @app.get("/")
    async def index():
        return Response(status_code=307, headers={"Location": "/studio"})

    web_origin = f"http://127.0.0.1:{state.info['web_port']}" if state.info.get("web_port") else None

    @app.get("/chatframe")
    async def chatframe(request: Request):
        """The chat, alone, for a page of the website mirror to frame.  Only the mirror's
        origin may frame it (CSP frame-ancestors); the page it sits in is untrusted and
        reaches it only by postMessage."""
        request.state.frameable = True
        page = request.query_params.get("page") or "/web/"
        if not page.startswith("/web"):
            page = "/web/"
        html = _chat_page(state, web_origin, page[:400])
        anc = f"frame-ancestors {web_origin}" if web_origin else "frame-ancestors 'none'"
        return HTMLResponse(html, headers={"Content-Security-Policy": _CSP.replace("frame-ancestors 'none'", anc)})

    @app.get("/open-web")
    async def open_web(request: Request):
        """Pair this browser (a `#pair=` code from `qccd web`), then open the website."""
        if not web_origin:
            return JSONResponse({"error": {"code": "no_web", "message": "this service runs without the website"}},
                                status_code=404)
        return HTMLResponse(_open_web_page(web_origin), headers={"Content-Security-Policy": _CSP})

    # ------------------------------------------------------------------ identity

    @route("GET", "/api/whoami", write=False)
    def whoami(request, actor, _):
        out = {"actor": actor, "workspace_id": ws.id, "task": ws.release.id}
        c = request.cookies.get(cookie_name)
        if c:
            rec = state.cookies.get(hashlib.sha256(c.encode()).hexdigest())
            if rec:
                out["csrf"] = rec["csrf"]
        return out

    # ------------------------------------------------------------------ design

    @route("GET", "/api/context", write=False)
    def context(request, actor, _):
        return ws.context(actor, branch=q(request, "branch"), since=q(request, "since", None, int),
                          detail=q(request, "detail", "summary"))

    @route("GET", "/api/design", write=False)
    def design(request, actor, _):
        return ws.design_document(q(request, "branch", "main"), q(request, "revision", None, int))

    @route("GET", "/api/query", write=False)
    def query(request, actor, _):
        keys = q(request, "keys")
        return ws.query_design(branch=q(request, "branch", "main"),
                               keys=[k for k in keys.split(",") if k] if keys else None,
                               kind=q(request, "kind"), limit=q(request, "limit", 200, int),
                               include_operations=q(request, "operations", "0") == "1")

    @route("POST", "/api/change-sets")
    def change_set(request, actor, b):
        aid = b.pop("by_page_action", None)
        if aid is not None:
            # the Studio page syncing what an AGENT's page action just drew on it: recorded as that
            # agent's change set (its protection limits, its name in the history), never the person's --
            # and only while that action is running on this very page
            w = state.page_waits.get(str(aid))
            if (actor["kind"] != "human" or not w or w.get("view") != actor.get("view") or w.get("action") != "studio"
                    or (w.get("actor") or {}).get("kind") != "agent"):
                raise WorkspaceError("forbidden", "by_page_action names no agent action running on this page", status=403)
            a = w["actor"]
            actor = {"kind": "agent", "id": a.get("id"), "session": a.get("session"), "label": a.get("label"),
                     "via": "page", "view": actor.get("view")}
        return ws.apply_change_set(b, actor)

    @route("POST", "/api/change-sets/{cs_id}/undo")
    def undo(request, actor, b):
        return ws.undo_change_set(request.path_params["cs_id"], actor, request_id=b.get("request_id"),
                                  expected_revision=b.get("expected_revision"), mode=b.get("mode", "apply"))

    @route("GET", "/api/change-sets/{cs_id}", write=False)
    def get_cs(request, actor, _):
        return ws.change_set(request.path_params["cs_id"])

    @route("GET", "/api/history", write=False)
    def history(request, actor, _):
        return {"history": ws.history(q(request, "branch", "main"), limit=q(request, "limit", 50, int),
                                      before=q(request, "before", None, int))}

    @route("GET", "/api/branches", write=False)
    def branches(request, actor, _):
        out = ws.branches()
        for b in out:
            b["display"] = ws.design_title(b)
        return {"branches": out}

    @route("POST", "/api/branches")
    def new_branch(request, actor, b):
        # a new design: `title` is whatever the person calls it (the internal name is made up)
        src = ws.resolve_draft(b.get("source") or "main")
        return ws.create_candidate(actor, name=b.get("name"), source=src, title=b.get("title"),
                                   note=b.get("note", ""), origin_prompt_id=b.get("origin_prompt_id"))

    @route("POST", "/api/branches/rename")
    def rename_branch(request, actor, b):
        return ws.rename_design(actor, ws.resolve_draft(b.get("design") or "main"), str(b.get("title") or ""))

    @route("GET", "/api/boards", write=False)
    def boards(request, actor, _):
        return {"boards": ws.boards()}

    @route("GET", "/api/compare", write=False)
    def compare(request, actor, _):
        return ws.compare(q(request, "a"), q(request, "b", "main"))

    # ------------------------------------------------------------------ programs, runs, comparison

    @route("GET", "/api/conversation", write=False)
    def conversation(request, actor, _):
        return ws.conversation(limit=q(request, "limit", 40, int))

    @route("GET", "/api/programs", write=False)
    def programs(request, actor, _):
        return {"programs": ws.programs()}

    @route("GET", "/api/runs", write=False)
    def list_runs(request, actor, _):
        return {"runs": ws.runs(q(request, "limit", 30, int))}

    @route("GET", "/api/compare-runs", write=False)
    def compare_runs(request, actor, _):
        return ws.compare_runs([x for x in (q(request, "runs", "") or "").split(",") if x])

    @route("GET", "/api/runs/{run_id}", write=False)
    def get_run(request, actor, _):
        return ws.run(request.path_params["run_id"])

    @route("GET", "/api/runs/{run_id}/program", write=False)
    def run_program(request, actor, _):
        return ws.run_program_source(request.path_params["run_id"])

    @route("GET", "/api/agents", write=False)
    def agents(request, actor, _):
        from .agents.claude import find_claude
        from .agents.codex import find_codex
        return {"codex_available": find_codex() is not None, "claude_available": find_claude() is not None}

    @route("GET", "/api/traces", write=False)
    def traces(request, actor, _):
        labels = {s["id"]: {"label": s.get("label"), "client": s["client"], "status": s["status"]} for s in ws.sessions()}
        out = state.traces.index()
        for t in out:
            t.update(labels.get(t["session"]) or {})
        return {"traces": out}

    @route("GET", "/api/traces/{sid}", write=False)
    def trace(request, actor, _):
        sid = request.path_params["sid"]
        return {"session": sid, "prompt": q(request, "prompt"),
                "steps": state.traces.steps(sid, q(request, "prompt"))}

    @route("GET", "/api/traces/{sid}/program", write=False)
    def trace_program(request, actor, _):
        """One request as a program (atir.py), with the checker's findings against this workspace."""
        from .atir import check, compile_trace
        sid = request.path_params["sid"]
        prompt = q(request, "prompt")
        steps = state.traces.steps(sid, prompt)
        if not prompt and steps:
            prompt = steps[-1].get("prompt")
            steps = [s for s in steps if s.get("prompt") == prompt]
        prog = compile_trace(steps)
        findings = check(prog, ws)
        # the picture needs each commit's design: when the trace did not record it (a page action's
        # result named none before 2026-09-26), the workspace's own log does, by change set
        titles = {}
        for x in prog["instructions"]:
            for e in x.get("effects") or []:
                if e.get("kind") == "commit" and not e.get("design"):
                    row = ws.store.one("SELECT branch FROM change_sets WHERE id=?", (e.get("change_set"),))
                    if row:
                        e["design"], e["design_from"] = row["branch"], "log"
                if e.get("design") and e["design"] not in titles:
                    try:
                        titles[e["design"]] = ws.design_title(ws.branch(ws.resolve_draft(e["design"])))
                    except Exception:
                        titles[e["design"]] = e["design"]
        return {**prog, "findings": findings, "designs": titles}

    @route("GET", "/api/design-graph", write=False)
    def design_graph(request, actor, _):
        """A design at one revision, as the sites and rails to draw (the trace viewer's picture)."""
        branch = ws.resolve_draft(q(request, "branch") or "main")
        rev = q(request, "rev", None, int)
        r = ws.replayed(branch, rev)
        if not r.ok:
            return {"branch": branch, "revision": rev, "ok": False}
        dev = r.arch.device

        def xy(p):
            return [round(float(getattr(p[0], "value", p[0])), 4), round(float(getattr(p[1], "value", p[1])), 4)]
        return {"branch": branch, "revision": rev, "ok": True, "name": r.arch.name,
                "title": ws.design_title(ws.branch(branch)),
                "nodes": [{"id": n.id, "xy": xy(n.pos), "kind": n.kind, "zone": getattr(n, "zone_type", None)}
                          for n in dev.nodes.values()],
                "segments": [{"a": sg.ends[0], "b": sg.ends[1], "loop": sg.loop} for sg in dev.segments.values()]}

    @route("POST", "/api/trace")
    def trace_step(request, actor, b):
        """The QCCD MCP server reports each tool call it served (agents only)."""
        if actor["kind"] != "agent" or not actor.get("session"):
            raise WorkspaceError("forbidden", "trace steps come from an agent session's MCP server", status=403)
        kind = str(b.get("kind") or "tool_call")
        if kind not in ("tool_call", "error"):
            raise WorkspaceError("bad_request", "kind: tool_call or error", status=422)
        state.traces.record(actor["session"], "mcp", kind, str(b.get("name") or "")[:80], b.get("data"),
                            ms=b.get("ms") if isinstance(b.get("ms"), (int, float)) else None)
        return {"ok": True}

    @app.get("/trace")
    async def trace_page(request: Request):
        # the viewer holds no data (like /studio); what it loads needs the pairing cookie
        return HTMLResponse(_trace_page(state), headers={"Content-Security-Policy": _CSP})

    @route("GET", "/api/agents/models", write=False)
    def agent_models(request, actor, _):
        from .agents.models import catalogue
        return catalogue(state)

    # ------------------------------------------------------------------ pages (the agent's hands on the UI)

    @route("GET", "/api/web", write=False)
    def web_info(request, actor, _):
        from .mirror import site_url
        return {"url": f"{web_origin}/web/" if web_origin else None, "site": site_url()}

    @route("GET", "/api/pages", write=False)
    def pages(request, actor, _):
        return {"pages": ws.pages()}

    @route("POST", "/api/page-actions")
    def page_action(request, actor, b):
        if actor["kind"] == "human" and actor["id"] != "cli":
            raise WorkspaceError("forbidden", "page actions are the agent's; a person uses the page itself",
                                 status=403)
        action = str(b.get("action", ""))
        if action not in PAGE_ACTIONS:
            raise WorkspaceError("bad_request", f"action must be one of {PAGE_ACTIONS}", status=422)
        args = b.get("args") or {}
        if not isinstance(args, dict):
            raise WorkspaceError("bad_request", "args must be an object", status=422)
        from .jsonsafe import check_value
        try:
            check_value(args, Limits(max_bytes=64 * 1024, max_depth=8, max_items=2000, max_string=20000))
        except JSONRejected as exc:
            raise WorkspaceError(exc.code, str(exc), status=422) from None
        view = ws.page_view(actor, b.get("view_id"))
        t0 = time.time()
        # every action is animated for the person (a cursor glides there first); a `wait` lasts what it says
        floor = (min(float(args.get("ms") or 0), 10000.0) / 1000.0 + 10.0) if action == "wait" else 0.0
        wait = max(1.0, floor, min(float(b.get("wait_s") or 25), 60.0))
        aid = "pa_" + secrets.token_hex(8)
        waiter = {"event": threading.Event(), "view": view["id"], "result": None, "action": action,
                  "actor": {k: actor.get(k) for k in ("kind", "id", "session", "label")}}
        state.page_waits[aid] = waiter
        try:
            ws.emit("page.action", {"action_id": aid, "view_id": view["id"], "action": action, "args": args,
                                    "expires_at": time.time() + wait,
                                    "actor": {k: actor.get(k) for k in ("kind", "id", "label")}})
            answered = waiter["event"].wait(wait)
        finally:
            state.page_waits.pop(aid, None)
        page = view["state"].get("page") or {}
        arrived = None
        if answered and action == "navigate" and (waiter["result"] or {}).get("ok"):
            # the answer came as the page LEFT; the agent's next step belongs on the page it arrives at
            arrived = _arrival(ws, view, t0, ((waiter["result"] or {}).get("result") or {}).get("navigating"))
        state.traces.record(actor.get("session"), "page", "page_action", action,
                            {"args": args, "page": page.get("url"), "answered": answered,
                             "ok": (waiter["result"] or {}).get("ok"), "result": (waiter["result"] or {}).get("result"),
                             "error": (waiter["result"] or {}).get("error"), "arrived": arrived},
                            ms=(time.time() - t0) * 1000)
        if not answered:
            raise WorkspaceError("page_timeout", f"the page {page.get('url')!r} did not answer within {wait:g} s "
                                 "(closed, reloading, or asleep in a background tab)", status=504)
        r = waiter["result"]
        out = {"view_id": view["id"], "page": {"url": page.get("url"), "title": page.get("title")},
               "action": action, "ok": r["ok"]}
        if r["ok"]:
            out["result"] = r.get("result")
            if action == "navigate":
                out["arrived"] = arrived or {"arrived": False, "note": "the new page has not reported itself yet "
                                             "(still loading); read the page again before acting on it"}
            res = r.get("result") or {}
            if action in ("comment", "reply") and isinstance(res, dict) and res.get("thread"):
                # what the agent wrote on the site as the person goes into the conversation too
                ws.note_site_comment(actor, {"page": res.get("page"), "thread": res.get("thread"),
                                             "text": str(res.get("text") or "")[:600], "as": res.get("as"),
                                             "kind": action})
        else:
            out["error"] = r.get("error") or "the page refused the action"
        return out

    @route("POST", "/api/page-actions/{aid}/result", human_only=True)
    def page_result(request, actor, b):
        w = state.page_waits.get(request.path_params["aid"])
        if w is None:
            raise WorkspaceError("unknown_action", "no such page action is waiting (it timed out)", status=404)
        if actor.get("view") != w["view"]:
            raise WorkspaceError("forbidden", "only the page the action was sent to answers it", status=403)
        w["result"] = {"ok": bool(b.get("ok")), "result": b.get("result"),
                       "error": str(b.get("error"))[:2000] if b.get("error") else None}
        w["event"].set()
        return {"ok": True}

    @app.get("/runview/{run_id}")
    async def run_view(run_id: str, request: Request):
        try:
            principal(request, write=False)
            html = await asyncio.to_thread(_run_page, state, run_id)
        except WorkspaceError as e:
            return err(e)
        except Exception as e:
            return _page_failed(state, e)
        return HTMLResponse(html, headers={"Content-Security-Policy": _CSP_FRAMEABLE,
                                           "X-Frame-Options": "SAMEORIGIN"})

    @app.get("/compare")
    async def compare_view(request: Request):
        try:
            principal(request, write=False)
            ids = [x for x in (request.query_params.get("runs") or "").split(",") if x]
            html = await asyncio.to_thread(_compare_page, state, ids)
        except WorkspaceError as e:
            return err(e)
        return HTMLResponse(html, headers={"Content-Security-Policy": _CSP_COMPARE})

    @route("POST", "/api/branches/adopt")
    def adopt(request, actor, b):
        return ws.adopt_candidate(b.get("candidate", ""), actor, expected_revision=b.get("expected_revision", -1),
                                  request_id=b.get("request_id") or secrets.token_hex(8), mode=b.get("mode", "apply"))

    @route("POST", "/api/branches/discard")
    def discard(request, actor, b):
        return ws.discard_candidate(b.get("candidate", ""), actor)

    @route("POST", "/api/import")
    def import_file(request, actor, b):
        p = Path(str(b.get("path", "")))
        p = (ws.root / p) if not p.is_absolute() else p
        rp = p.resolve()
        if ws.root not in rp.parents and rp != ws.root:
            raise WorkspaceError("path_escape", "imports must come from inside the workspace", status=403)
        return ws.import_file(rp, actor, branch=b.get("branch", "main"), request_id=b.get("request_id"),
                              mode=b.get("mode", "apply"))

    @route("GET", "/api/artifacts/{digest}", write=False, raw=True)
    def artifact(request, actor, _):
        d = request.path_params["digest"]
        return Response(ws.get_artifact(d), media_type="application/json")

    # ------------------------------------------------------------------ views, sessions

    @route("POST", "/api/views", human_only=True)
    def new_view(request, actor, b):
        v = ws.register_view(actor, branch=b.get("branch", "main"), label=str(b.get("label", "")),
                             page=b.get("page"))
        _warm_for(state, v)
        return v

    @route("PATCH", "/api/views/{view_id}", human_only=True)
    def patch_view(request, actor, b):
        v = ws.update_view(request.path_params["view_id"], b, actor)
        if b.get("closed") is False or "target_session" in b:       # a page (re)opened, or it picked an agent
            _warm_for(state, v)
        return v

    @route("POST", "/api/views/{view_id}/presented", human_only=True)
    def presented(request, actor, b):
        ws.presented(request.path_params["view_id"], str(b.get("presentation_id", "")), str(b.get("outcome", "")))
        return {"ok": True}

    @route("GET", "/api/views", write=False)
    def views(request, actor, _):
        return {"views": ws.views()}

    @route("POST", "/api/sessions")
    def new_session(request, actor, b):
        if actor["kind"] == "human" and actor["id"] != "cli":
            raise WorkspaceError("forbidden", "sessions are registered by adapters or the CLI", status=403)
        return ws.register_session(actor, client=b.get("client", "generic"), mode=b.get("mode", "pull"),
                                   label=str(b.get("label", "")), runtime_ref=b.get("runtime_ref"),
                                   capabilities=b.get("capabilities"), branch=b.get("branch", "main"),
                                   session_id=b.get("session_id"))

    @route("GET", "/api/sessions", write=False)
    def sessions(request, actor, _):
        out = ws.sessions()
        for s in out:
            br = state.bridges.get(s["id"])
            s["bridge"] = br.status() if br else None
        return {"sessions": out}

    @route("POST", "/api/sessions/{sid}/status")
    def session_status(request, actor, b):
        return ws.session_status(request.path_params["sid"], str(b.get("status", "connected")),
                                 detail=str(b.get("detail", "")), runtime_ref=b.get("runtime_ref"),
                                 capabilities=b.get("capabilities"))

    @route("POST", "/api/sessions/{sid}/stop", human_only=True)
    def stop(request, actor, b):
        sid = request.path_params["sid"]
        s = ws.stop_session(sid, actor, reason=str(b.get("reason", "stopped by the user")),
                            prompt_id=b.get("prompt_id"))
        br = state.bridges.get(sid)
        interrupt = br.interrupt() if br else {"supported": False,
                                               "message": "this runtime offers no interrupt; its writes are "
                                                          "fenced until you resume it"}
        return {"session": s, "interrupt": interrupt}

    @route("POST", "/api/sessions/{sid}/settings", human_only=True)
    def session_settings(request, actor, b):
        from .agents.models import apply_settings
        return apply_settings(state, request.path_params["sid"], b)

    @route("POST", "/api/sessions/{sid}/resume", human_only=True)
    def resume(request, actor, b):
        return ws.resume_session(request.path_params["sid"], actor)

    @route("POST", "/api/sessions/codex/connect", human_only=True)
    def codex_connect(request, actor, b):
        from .agents.codex import connect_codex
        return connect_codex(state, b)

    @route("POST", "/api/sessions/claude/connect", human_only=True)
    def claude_connect(request, actor, b):
        from .agents.claude import connect_claude
        return connect_claude(state, b)

    # ------------------------------------------------------------------ prompts

    @route("POST", "/api/prompts/draft", human_only=True)
    def draft(request, actor, b):
        return ws.save_draft(actor, b.get("body") or {}, prompt_id=b.get("prompt_id"))

    @route("POST", "/api/prompts/send", human_only=True)
    def send(request, actor, b):
        out = ws.send_prompt(actor, prompt_id=b.get("prompt_id"), body=b.get("body"),
                             view_id=b.get("view_id") or actor.get("view"), context=b.get("context"),
                             request_id=b.get("request_id"))
        _kick(state)
        return out

    @route("GET", "/api/prompts", write=False)
    def prompts(request, actor, _):
        return {"prompts": ws.list_prompts(limit=q(request, "limit", 100, int),
                                           unresolved_only=q(request, "open", "0") == "1")}

    @route("GET", "/api/prompts/{pid}", write=False)
    def prompt(request, actor, _):
        p = ws.prompt(request.path_params["pid"])
        if actor["kind"] == "agent":
            ws.acknowledge(actor, [p["prompt_id"]])
        sent = p.get("latest_sent")
        if sent and sent["body"].get("context_snapshot_id"):
            p["context"] = ws.context_snapshot(sent["body"]["context_snapshot_id"])
        return p

    @route("POST", "/api/prompts/{pid}/reply")
    def reply(request, actor, b):
        return ws.reply(actor, request.path_params["pid"], str(b.get("text", "")), work_state=b.get("work_state"),
                        links=b.get("links"), request_id=b.get("request_id"))

    @route("POST", "/api/prompts/{pid}/state")
    def prompt_state(request, actor, b):
        return ws.resolve_prompt(actor, request.path_params["pid"], state=str(b.get("state", "resolved")))

    @route("POST", "/api/prompts/ack")
    def ack(request, actor, b):
        return {"acknowledged": ws.acknowledge(actor, list(b.get("prompt_ids") or []))}

    @route("GET", "/api/context-snapshots/{cid}", write=False)
    def ctx_snap(request, actor, _):
        return ws.context_snapshot(request.path_params["cid"])

    # ------------------------------------------------------------------ delivery (adapters)

    @route("POST", "/api/deliveries/claim")
    def claim(request, actor, b):
        if actor["kind"] != "agent":
            raise WorkspaceError("forbidden", "adapters claim deliveries", status=403)
        sid = actor.get("session")
        if not sid:
            raise WorkspaceError("bad_request", "claiming needs X-QCCD-Session", status=422)
        wait = min(float(b.get("wait_s", 0) or 0), 25.0)
        t0 = time.time()
        while True:
            due = ws.due_deliveries(session_id=sid, limit=int(b.get("limit", 5)))
            out = []
            for d in due:
                c = ws.claim_delivery(d["id"])
                if c:
                    out.append(ws.delivery_payload(c))
            if out or time.time() - t0 >= wait:
                return {"deliveries": out}
            ws.wait_for_events(ws.last_event_seq(), 1.0)

    @route("POST", "/api/deliveries/{did}/mark")
    def mark(request, actor, b):
        if actor["kind"] != "agent":
            raise WorkspaceError("forbidden", "adapters mark deliveries", status=403)
        return ws.mark_delivery(request.path_params["did"], str(b.get("state", "")), external_ref=b.get("external_ref"),
                                error=b.get("error"), detail=b.get("detail"), retry_in=b.get("retry_in"))

    # ------------------------------------------------------------------ jobs, results

    @route("POST", "/api/jobs")
    def start_job(request, actor, b):
        return ws.start_job(actor, str(b.get("kind", "")), b.get("params") or {}, request_id=b.get("request_id"),
                            origin_prompt_id=b.get("origin_prompt_id"))

    @route("GET", "/api/jobs", write=False)
    def list_jobs(request, actor, _):
        return {"jobs": ws.jobs(q(request, "limit", 50, int))}

    @route("GET", "/api/jobs/{jid}", write=False)
    def get_job(request, actor, _):
        j = ws.job(request.path_params["jid"])
        res = j.get("result") or {}
        if isinstance(res, dict) and "log" in res:
            res["log"] = res["log"][-4000:]
        return j

    @route("POST", "/api/jobs/{jid}/cancel")
    def cancel_job(request, actor, b):
        return ws.cancel_job(actor, request.path_params["jid"])

    @route("POST", "/api/submissions")
    def submit(request, actor, b):
        if "board" in b or "design" in b:
            # "submit this design to that board": compile for it, adopt, freeze, grade -- one job
            return ws.submit_design(actor, design=b.get("design") or b.get("branch") or "main", board=b.get("board"),
                                    profile=b.get("profile", "reference"), origin_prompt_id=b.get("origin_prompt_id"),
                                    request_id=b.get("request_id"), title=str(b.get("title", "")))
        return ws.submit_local(actor, branch=b.get("branch", "main"), revision=b.get("revision"),
                               profile=b.get("profile", "reference"), origin_prompt_id=b.get("origin_prompt_id"),
                               request_id=b.get("request_id"), title=str(b.get("title", "")))

    @route("GET", "/api/submissions", write=False)
    def submissions(request, actor, _):
        return {"submissions": ws.submissions(q(request, "limit", 50, int))}

    @route("GET", "/api/submissions/{sub}", write=False)
    def submission(request, actor, _):
        return ws.submission(request.path_params["sub"])

    @route("GET", "/api/leaderboard", write=False)
    def leaderboard(request, actor, _):
        return ws.leaderboard(q(request, "board"))

    @route("POST", "/api/publish/prepare")
    def prepare(request, actor, b):
        return ws.prepare_publish(actor, str(b.get("submission_id", "")), visibility=b.get("visibility", "public"),
                                  display_name=str(b.get("display_name", "")))

    @route("POST", "/api/publish/approve", human_only=True)
    def approve(request, actor, b):
        if actor["id"] == "cli" and not b.get("interactive_confirmation"):
            raise WorkspaceError("forbidden", "the CLI approves only after an interactive confirmation", status=403)
        return ws.approve_publish(actor, str(b.get("submission_id", "")), str(b.get("bundle_digest", "")),
                                  b.get("params") or {})

    @route("POST", "/api/present")
    def present(request, actor, b):
        return ws.present(actor, str(b.get("action", "")), b.get("target") or {}, view_id=b.get("view_id"),
                          note=str(b.get("note", "")))

    @route("GET", "/api/reference", write=False)
    def reference(request, actor, _):
        from .reference import read_reference
        return read_reference(ws, q(request, "section", "index"), q(request, "query"))

    @route("POST", "/api/pair-code")
    def pair_code(request, actor, b):
        # only the terminal (owner token) may mint a browser pairing code
        if actor.get("id") != "cli":
            raise WorkspaceError("forbidden", "pairing codes are minted by `qccd studio`", status=403)
        return {"code": state.new_pair_code(), "expires_in_s": 300}

    @route("POST", "/api/shutdown")
    def shutdown(request, actor, b):
        if actor.get("id") != "cli":
            raise WorkspaceError("forbidden", "only the owner token can stop the service", status=403)
        state.stop.set()
        return {"ok": True}

    # ------------------------------------------------------------------ events

    @app.get("/api/events")
    async def events(request: Request):
        try:
            actor = principal(request, write=False)
        except WorkspaceError as e:
            return err(e)
        # a reconnecting EventSource sends Last-Event-ID; it wins over the URL's first cursor
        cursor = request.headers.get("last-event-id") or request.query_params.get("cursor") or "0"
        try:
            cursor = int(cursor)
        except ValueError:
            cursor = 0
        view = request.query_params.get("view")

        async def gen():
            nonlocal cursor
            first = ws.events_since(cursor, limit=1)
            if first["gap"]:
                yield _sse("gap", {"cursor": cursor, "head_seq": first["head_seq"],
                                   "message": "events were pruned; fetch a fresh snapshot"}, None)
                cursor = first["head_seq"]
            yield _sse("hello", {"cursor": cursor, "workspace_id": ws.id, "actor": actor}, None)
            last_ping = time.time()
            while not state.stop.is_set():
                if await request.is_disconnected():
                    return
                batch = await asyncio.to_thread(ws.events_since, cursor, limit=200)
                for e in batch["events"]:
                    cursor = e["seq"]
                    yield _sse(e["type"], e, e["seq"])
                if view and time.time() - last_ping > 10:
                    try:
                        ws.touch_view(view)
                    except Exception:
                        pass
                if not batch["events"]:
                    if time.time() - last_ping > 15:
                        last_ping = time.time()
                        yield ": ping\n\n"
                    await asyncio.to_thread(ws.wait_for_events, cursor, 1.0)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    return app


def _sse(event: str, data: Any, seq: int | None) -> str:
    out = ""
    if seq is not None:
        out += f"id: {seq}\n"
    out += f"event: {event}\n"
    out += "data: " + json.dumps(data, separators=(",", ":")) + "\n\n"
    return out


#: the studio page is self-contained; the live layer adds same-origin fetch/EventSource
_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; "
        "font-src data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


#: the run view may be framed by this origin (the side-by-side page); nothing else may
_CSP_FRAMEABLE = _CSP.replace("frame-ancestors 'none'", "frame-ancestors 'self'")
#: the side-by-side page frames the two run views
_CSP_COMPARE = _CSP + "; frame-src 'self'"


def _run_page(state: ServiceState, run_id: str) -> str:
    """A run's own Studio page: its device and its compiled program, view-only, cached."""
    from .compare_page import RUNVIEW_BLOCK
    key = ("runview", run_id)
    with state.page_lock:
        page = state.page_cache.get(key)
    if page is None:
        page = _render_run(state.ws, run_id)
        at = page.rfind("</body>")
        # the chat and the page actions: in a tab of its own the run page is where the agent
        # presses Play for the person (cowork.js stays out of the side-by-side frames)
        page = page[:at] + RUNVIEW_BLOCK + _live_layer(state, "run", run_id=run_id) + page[at:]
        with state.page_lock:
            state.page_cache[key] = page
    return page


def _render_run(ws: Workspace, run_id: str) -> str:
    import tempfile
    from ..arch.device import Architecture
    from ..cost.models import corrected_model
    from ..ir.tsir import TSIR
    from ..phys.tech import load_technology
    from ..verify import verify
    from ..viz.render import render_html
    from ..viz.scale import DEFAULT_TECH
    from .jsonsafe import strict_loads as _sl

    run = ws.run(run_id)
    arch = Architecture.from_json(_sl(ws.get_artifact(run["artifacts"]["device"])))
    prog = TSIR.from_json(_sl(ws.get_artifact(run["artifacts"]["program"])))
    model = corrected_model(run["performance"]["table"])
    report = verify(prog, arch, model)
    d = run["design"]
    headline = (f"{run['program']['name']} on {ws._design_title_of(d['draft'])} (r{d['revision']}): "
                f"{run['performance']['total']['ms']:g} ms")
    with tempfile.TemporaryDirectory() as td:
        # the circuit beside the compiled program, when the run kept both (runs since 2026-09-24)
        source = None
        arts = run.get("artifacts") or {}
        if arts.get("certificate") and arts.get("circuit"):
            try:
                from ..ir.source_map import build as build_source
                qasm = Path(td) / "circuit.qasm"
                qasm.write_bytes(ws.get_artifact(arts["circuit"]))
                source = build_source(prog, _sl(ws.get_artifact(arts["certificate"])), qasm)
            except Exception:
                logging.getLogger("qccd.service").exception("the circuit pane of run %s could not be built", run_id)
                source = None
        out = Path(td) / "page.html"
        render_html(arch, prog, report.result, model, out, tech=load_technology(DEFAULT_TECH),
                    kicker="QCCD RUN", headline=headline, lede=None, template_stems="*", source=source)
        return out.read_text(encoding="utf-8")


def _live_layer(state: ServiceState, mode: str, **extra) -> str:
    """The chat and the page actions, for a page the workspace serves in a tab of its own."""
    ws = state.ws
    cfg = {"workspace_id": ws.id, "task": ws.release.id, "mode": mode, "contract": "1",
           "web_url": f"http://127.0.0.1:{state.info['web_port']}/web/" if state.info.get("web_port") else None,
           **extra}
    return (_css() + '<script id="qccd-live-config" type="application/json">' + json.dumps(cfg).replace("</", "<\\/")
            + "</script>\n" + _contract() + "<script>\n" + _pageact() + "\n</script>\n<script>\n" + _js() + "\n</script>\n")


def _compare_page(state: ServiceState, run_ids: list) -> str:
    from .compare_page import compare_html
    from .jsonsafe import strict_loads as _sl
    ws = state.ws
    cmp = ws.compare_runs(run_ids)
    times = [_sl(ws.get_artifact(ws.run(r)["artifacts"]["times"]))["times_us"] for r in run_ids]
    page = compare_html(cmp, times)
    at = page.rfind("</body>")
    return page[:at] + _live_layer(state, "compare", runs=list(run_ids)) + page[at:]


def _kick(state: ServiceState) -> None:
    w = getattr(state, "worker", None)
    if w is not None:
        w.kick()


def _warm_for(state: ServiceState, view: dict | None) -> None:
    """A page with a chat opened: start the process of the agent it talks to now, so the person's
    first message does not wait for that agent's start-up (~9 s for Claude Code with the QCCD
    tools).  Only a runtime that keeps a process (a bridge with `warm`), and not a stopped agent."""
    sid = (view or {}).get("target_session")
    br = state.bridges.get(sid) if sid else None
    if br is None or not hasattr(br, "warm"):
        return
    try:
        s = state.ws.session(sid)
    except Exception:
        return
    if s["status"] != "connected" or s["write_fence"]:
        return
    threading.Thread(target=br.warm, name=f"qccd-warm-{sid}", daemon=True).start()


# ---------------------------------------------------------------------- the pages

def _studio_page(state: ServiceState, snapshot_id: str | None) -> str:
    """Render the stock studio page and inject the live layer before its last </body>.

    The design view renders ONCE per service (the page restores the head revision's
    records on load, so the base it was drawn from does not matter); a run view renders
    the snapshot's own device and final program and is cached by snapshot."""
    ws = state.ws
    key = ("run", snapshot_id) if snapshot_id else ("design",)
    with state.page_lock:
        page = state.page_cache.get(key)
        if page is None:
            page = _render(ws, snapshot_id)
            state.page_cache[key] = page
    cfg = {"workspace_id": ws.id, "task": ws.release.id, "mode": "run" if snapshot_id else "design",
           "snapshot_id": snapshot_id, "contract": "1",
           "web_url": f"http://127.0.0.1:{state.info['web_port']}/web/" if state.info.get("web_port") else None}
    inject = (_css() + '<script id="qccd-live-config" type="application/json">'
              + json.dumps(cfg).replace("</", "<\\/") + "</script>\n" + _contract(design_page=not snapshot_id)
              + "<script>\n" + _pageact() + "\n</script>\n"
              + "<script>\n" + _js() + "\n</script>\n")
    at = page.rfind("</body>")
    return page[:at] + inject + page[at:]


def _render(ws: Workspace, snapshot_id: str | None) -> str:
    import tempfile
    from ..arch.device import Architecture
    from ..cost.models import corrected_model
    from ..ir.tsir import TSIR
    from ..phys.tech import load_technology
    from ..verify import verify
    from ..viz.render import render_html
    from ..viz.scale import DEFAULT_TECH
    from .jsonsafe import strict_loads as _sl

    model = corrected_model(ws.release.manifest["physics"].get("rank_table", "qccdsim_jones"))
    tech = load_technology(DEFAULT_TECH)
    if snapshot_id:
        s = ws.snapshot(snapshot_id)
        d = ws.root / s["dir"] / "bundle"
        arch = Architecture.from_json(_sl((d / "device.arch.json").read_bytes()))
        prog = TSIR.from_json(_sl((d / "program.tsir.json").read_bytes()))
        headline = f"{arch.name} - run {snapshot_id} (r{s['revision']}, immutable)"
    else:
        h = ws.head("main")
        from .design import DesignState, replay
        base = DesignState(geom=h.state.geom, seed=h.state.seed, post=h.state.post)
        arch = replay(base).arch
        prog = TSIR(name="design", arch_spec=arch.name)
        headline = f"{arch.name} - live workspace"
    report = verify(prog, arch, model)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "page.html"
        render_html(arch, prog, report.result, model, out, tech=tech, kicker="QCCD STUDIO",
                    headline=headline, lede=None, template_stems="*")
        return out.read_text(encoding="utf-8")


def _js() -> str:
    return (_WEB / "cowork.js").read_text(encoding="utf-8")


def _arrival(ws: Workspace, old: dict, since: float, target: str | None, timeout: float = 15.0) -> dict | None:
    """After `navigate`: the page the tab arrived at, once it has reported itself -- the same view
    at a new address, or a view opened since the navigation began.  None when it has not (yet)."""
    from urllib.parse import urlsplit
    old_url = (old["state"].get("page") or {}).get("url") or ""
    if target and urlsplit(target).path == urlsplit(old_url).path:
        return None                                   # a move within the page: nothing to wait for
    end = time.time() + timeout
    while time.time() < end:
        for v in ws.views():
            pg = v["state"].get("page") or {}
            if not v["connected"] or not pg.get("url"):
                continue
            if (v["id"] == old["id"] and pg["url"] != old_url) or (v["id"] != old["id"] and (v.get("created_at") or 0) >= since):
                return {"view_id": v["id"], "url": pg["url"], "title": pg.get("title"), "kind": pg.get("kind")}
        time.sleep(0.25)
    return None


def _contract(**kw) -> str:
    """What the page's tools may do (interface.py), as window.QCCD_INTERFACE."""
    from .interface import page_contract
    return ("<script>window.QCCD_INTERFACE=" + json.dumps(page_contract(**kw)).replace("</", "<\\/")
            + ";</script>\n")


def _pageact() -> str:
    return (_WEB / "pageact.js").read_text(encoding="utf-8")


#: what an agent may do on a page (web/pageact.js implements each; nothing else runs)
PAGE_ACTIONS = ("read", "scroll", "highlight", "click", "fill", "press", "navigate", "step", "open_lesson",
                "studio", "wait", "comments", "comment", "reply", "resolve")


def _chat_page(state: ServiceState, web_origin: str | None, page: str) -> str:
    """The chat alone, for a website page: the same cowork.js in page mode."""
    from .mirror import site_url
    ws = state.ws
    cfg = {"workspace_id": ws.id, "task": ws.release.id, "mode": "page", "framed": True,
           "parent_origin": web_origin, "page": page, "site": site_url(), "contract": "1",
           "web_url": f"{web_origin}/web/" if web_origin else None}
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>QCCD chat</title>"
            + _css() + "<style>html,body{margin:0;height:100%;background:#fff;overflow:hidden}</style></head>"
            "<body class=\"qcl-framed\">"
            + '<script id="qccd-live-config" type="application/json">' + json.dumps(cfg).replace("</", "<\\/")
            + "</script>\n<script>\n" + _js() + "\n</script>\n</body></html>")


def _open_web_page(web_origin: str) -> str:
    """Pair with the `#pair=` code, then go to the website (`#pair=CODE&to=/web/rules/`)."""
    js = ("(function(){var h=location.hash||'',m=/[#&]pair=([A-Za-z0-9_-]+)/.exec(h),t=/[#&]to=([^&]+)/.exec(h);"
          "var to=t?decodeURIComponent(t[1]):'/web/';if(to.indexOf('/web')!==0)to='/web/';"
          "var go=function(){location.replace(" + json.dumps(web_origin) + "+to);};"
          "history.replaceState(null,'',location.pathname);"
          "if(!m){go();return;}"
          "fetch('/api/pair',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',"
          "body:JSON.stringify({code:m[1]})}).then(function(r){if(r.ok)go();else r.json().then(function(d){"
          "document.getElementById('msg').textContent='Pairing failed: '+((d&&d.error&&d.error.message)||r.status)+"
          "'. Run qccd web again.';});});})();")
    return ("<!doctype html><html><head><meta charset=\"utf-8\"><title>QCCD: opening the website</title>"
            "<style>body{font:15px/1.5 system-ui,sans-serif;margin:48px auto;max-width:640px;color:#1d1d1b}</style>"
            "</head><body><p id=\"msg\">Opening the website with your agent&hellip;</p><script>" + js
            + "</script></body></html>")


def _trace_page(state: ServiceState) -> str:
    cfg = {"workspace_id": state.ws.id, "task": state.ws.release.id}
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>QCCD: agent traces</title>"
            "<style>\n" + (_WEB / "trace.css").read_text(encoding="utf-8") + "\n</style></head><body>"
            + '<script id="qccd-trace-config" type="application/json">' + json.dumps(cfg).replace("</", "<\\/")
            + "</script>\n<script>\n" + (_WEB / "trace.js").read_text(encoding="utf-8") + "\n</script>\n</body></html>")


def _css() -> str:
    return "<style>\n" + (_WEB / "cowork.css").read_text(encoding="utf-8") + "\n</style>\n"


# ---------------------------------------------------------------------- serve

def _page_failed(state: ServiceState, exc: Exception) -> HTMLResponse:
    """The Studio page could not be built: an HTML page that says why and what to do, and
    the full traceback in .qccd/service.log."""
    import html as _html
    import logging
    logging.getLogger("qccd.service").exception("building the Studio page failed")
    from .runtime import code_identity
    started = state.info.get("code")
    stale = bool(started) and started != code_identity()
    if stale:
        why = ("QCCD's files changed after this service started (an update, or a <code>git pull</code>), "
               "and a running service keeps the code it started with.")
    else:
        why = "The full traceback is in <code>.qccd/service.log</code> inside the workspace."
    body = (f"<h1>The Studio page could not be built</h1>"
            f"<pre>{_html.escape(type(exc).__name__ + ': ' + str(exc))}</pre>"
            f"<p>{why}</p>"
            "<p>Restart the service with the current code, in the workspace folder:</p>"
            "<pre>qccd stop\nqccd studio --keep-alive</pre>"
            "<p>If the same error comes back, send <code>.qccd/service.log</code> to the maintainers.</p>")
    page = ("<!doctype html><html><head><meta charset=\"utf-8\"><title>QCCD Studio: page error</title>"
            "<style>body{font:15px/1.5 system-ui,sans-serif;max-width:760px;margin:48px auto;padding:0 16px;color:#1d1d1b}"
            "pre{background:#f5f4f1;padding:10px 12px;border-radius:6px;white-space:pre-wrap;word-break:break-word}"
            "code{background:#f5f4f1;padding:1px 4px;border-radius:4px}</style></head>"
            f"<body>{body}</body></html>")
    return HTMLResponse(page, status_code=500, headers={"Content-Security-Policy": _CSP})


def _bind_web(sticky_port: int | None):
    """The website's listener: the well-known port first (`mirror.WEB_PORT`, or
    QCCD_WEB_PORT), so the Agent button on qccd.academy can link to this computer without
    knowing anything about it; then the port it had last time; then any free port."""
    from .mirror import web_port_default
    from .runtime import bind_listener
    want = web_port_default()
    try:
        return bind_listener(want, fallback=False)
    except OSError:
        return bind_listener(sticky_port if sticky_port and sticky_port != want else None)


def _by_port(main, web, web_port: int):
    """One process, two listeners: requests that arrived on the website's socket go to the
    mirror app, everything else to the workspace app (by the socket, not the Host header)."""
    async def app(scope, receive, send):
        server = scope.get("server") or (None, None)
        if scope.get("type") in ("http", "websocket") and server[1] == web_port:
            await web(scope, receive, send)
        else:
            await main(scope, receive, send)
    return app


def _single_instance(root: Path):
    """Take `.qccd/service.lock` exclusively (released by the OS when the process ends,
    however it ends).  None if another process holds it."""
    p = Path(root).resolve() / ".qccd" / "service.lock"
    p.parent.mkdir(parents=True, exist_ok=True)
    f = open(p, "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def serve(root: Path, *, port: int | None = None, open_browser: bool = False) -> int:
    import faulthandler
    import logging
    import sys as _sys
    import uvicorn
    from .delivery import DeliveryWorker
    from .runtime import (bind_listener, code_identity, read_runtime, read_sticky, remove_runtime,
                          write_runtime, write_sticky)
    code = code_identity()                      # the code this process loaded, for `qccd studio`'s staleness check

    faulthandler.enable(file=_sys.stderr)
    logging.basicConfig(stream=_sys.stderr, level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    # ONE service per workspace, enforced by an exclusive lock held for the process's life
    lockf = _single_instance(Path(root))
    if lockf is None:
        print("another service already holds this workspace's lock (.qccd/service.lock)", flush=True)
        return 1
    ws = Workspace(root, recover=True)          # this process holds the lock: it may recover
    existing = read_runtime(ws.id)
    if existing:
        print(f"a service for {ws.id} is already running on port {existing['port']} (pid {existing['pid']})")
        return 1
    # the port this workspace's service had last time, when it is free: open pages reconnect
    sticky = read_sticky(ws.id)
    sock = bind_listener(port, fallback=False) if port else bind_listener(sticky.get("port"))
    port = sock.getsockname()[1]
    # the website mirror listens on a port of its own: its pages are a different origin,
    # which the workspace API does not trust (mirror.py)
    wsock = _bind_web(sticky.get("web_port")) if os.environ.get("QCCD_WEB", "1") != "0" else None
    web_port = wsock.getsockname()[1] if wsock else None
    write_sticky(ws.id, port=port, web_port=web_port)
    info = write_runtime(ws.id, ws.root, port, os.getpid(), code=code, web_port=web_port)
    state = ServiceState(ws, info, sticky=True)
    app = create_app(state)
    if wsock is not None:
        from .mirror import create_mirror_app
        app = _by_port(app, create_mirror_app(state), web_port)
    worker = DeliveryWorker(state)
    state.worker = worker
    worker.start()

    def reattach():
        try:
            from .agents.codex import reattach_codex_sessions
            for r in reattach_codex_sessions(state):
                logging.getLogger("qccd.service").info("codex session %s", r)
            from .agents.claude import reattach_claude_sessions
            for r in reattach_claude_sessions(state):
                logging.getLogger("qccd.service").info("claude session %s", r)
        except Exception:
            logging.getLogger("qccd.service").exception("re-attaching Codex sessions failed")
    threading.Thread(target=reattach, name="qccd-reattach", daemon=True).start()
    access = os.environ.get("QCCD_ACCESS_LOG") == "1"   # request log for diagnosis only (never tokens: URLs carry none)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info" if access else "warning",
                            access_log=access,
                            lifespan="off", timeout_graceful_shutdown=3)
    server = uvicorn.Server(config)

    def watch_stop():
        state.stop.wait()
        server.should_exit = True
    threading.Thread(target=watch_stop, daemon=True).start()
    print(f"QCCD workspace service {ws.id} on http://127.0.0.1:{port} (pid {os.getpid()})"
          + (f"; website on http://127.0.0.1:{web_port}/web/" if web_port else ""), flush=True)
    try:
        server.run(sockets=[sock] + ([wsock] if wsock is not None else []))
    finally:
        state.stop.set()
        worker.stop()
        # the kept-alive Claude processes end with the service (each would also end when its input
        # closed, but not before the service's own exit)
        from .agents.claude import ClaudeBridge
        for br in list(state.bridges.values()):
            if isinstance(br, ClaudeBridge):
                try:
                    br.close()
                except Exception:
                    pass
        remove_runtime(ws.id, os.getpid())
        ws.close()
    return 0
