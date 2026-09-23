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
        self.started = time.time()

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
        return {"branches": ws.branches()}

    @route("POST", "/api/branches")
    def new_branch(request, actor, b):
        return ws.create_candidate(actor, name=b.get("name"), source=b.get("source", "main"),
                                   note=b.get("note", ""), origin_prompt_id=b.get("origin_prompt_id"))

    @route("GET", "/api/compare", write=False)
    def compare(request, actor, _):
        return ws.compare(q(request, "a"), q(request, "b", "main"))

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
        return ws.register_view(actor, branch=b.get("branch", "main"), label=str(b.get("label", "")))

    @route("PATCH", "/api/views/{view_id}", human_only=True)
    def patch_view(request, actor, b):
        return ws.update_view(request.path_params["view_id"], b, actor)

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

    @route("POST", "/api/sessions/{sid}/resume", human_only=True)
    def resume(request, actor, b):
        return ws.resume_session(request.path_params["sid"], actor)

    @route("POST", "/api/sessions/codex/connect", human_only=True)
    def codex_connect(request, actor, b):
        from .agents.codex import connect_codex
        return connect_codex(state, b)

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
        return ws.leaderboard()

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


def _kick(state: ServiceState) -> None:
    w = getattr(state, "worker", None)
    if w is not None:
        w.kick()


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
           "snapshot_id": snapshot_id, "contract": "1"}
    inject = (_css() + '<script id="qccd-live-config" type="application/json">'
              + json.dumps(cfg).replace("</", "<\\/") + "</script>\n<script>\n" + _js() + "\n</script>\n")
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
    sock = bind_listener(port, fallback=False) if port else bind_listener(read_sticky(ws.id).get("port"))
    port = sock.getsockname()[1]
    write_sticky(ws.id, port=port)
    info = write_runtime(ws.id, ws.root, port, os.getpid(), code=code)
    state = ServiceState(ws, info, sticky=True)
    app = create_app(state)
    worker = DeliveryWorker(state)
    state.worker = worker
    worker.start()

    def reattach():
        try:
            from .agents.codex import reattach_codex_sessions
            for r in reattach_codex_sessions(state):
                logging.getLogger("qccd.service").info("codex session %s", r)
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
    print(f"QCCD workspace service {ws.id} on http://127.0.0.1:{port} (pid {os.getpid()})", flush=True)
    try:
        server.run(sockets=[sock])
    finally:
        state.stop.set()
        worker.stop()
        remove_runtime(ws.id, os.getpid())
        ws.close()
    return 0
