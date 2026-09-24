"""The website, served through the workspace: every page of qccd.academy with the chat.

`qccd studio` also serves the public site at `http://127.0.0.1:<web port>/web/...`.  Each
page is fetched live from the site (cached, revalidated), and gets two things added: the
chat with the person's agent, and a fixed set of page actions the agent can use through
MCP (read the page, scroll, highlight, click, fill, press a key, navigate, step an
animation, open a lesson; `web/pageact.js`).

Why a SECOND port.  The pages come from the internet, and a script on a page can do
whatever its origin can do.  So the mirror has its own origin, which the workspace does not
trust at all: it serves pages and nothing else, never honours the pairing cookie, and a
request from it to the workspace API is refused like any other cross-origin request
(`service.py`'s Origin and CSRF checks).  The chat on a mirror page is a frame from the
workspace's origin (`/chatframe`); only that frame holds the pairing, and it passes the
agent's page actions to the page by `postMessage`.  What a page answers is data from the
website, like the page itself: never authority.

The site's comments work here as on the site: the comment layer's requests (`/api/...`)
are passed to qccd.academy, and the person signs in once with their own account.  Its session
is kept in a cookie of this origin (`qccd_site`, path /api/), never in the workspace; writes
are passed only from this origin's own pages.  A thread is keyed by the page's path on the
SITE (/rules/, not /web/rules/), so a comment made here is where it belongs there.  The
agent comments through the comment layer's own functions (`QCCD_COMMENTS_HOOK`), which the
page-action layer signs "via <agent>".  The read-only Official leaderboard is passed through.
`QCCD_SITE_URL` points the mirror at another copy of the site (tests use a local one).
"""

from __future__ import annotations

import gzip
import html as _html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

try:
    # at module level: with postponed annotations FastAPI resolves `request: Request` here,
    # and a Request imported inside create_mirror_app would read as a missing query field (422)
    from fastapi import Request
except ImportError:                      # the mirror is served only by the workspace service
    Request = None

__all__ = ["SiteMirror", "create_mirror_app", "site_url", "inject"]

_WEB = Path(__file__).resolve().parent / "web"
_PATH_OK = re.compile(r"[A-Za-z0-9._~\-/%+@]*")        # ghz4@1 is a task id
_FRESH_S = 300.0                      # a cached page is used as-is this long, then revalidated
_CACHE_BYTES = 200 * 1024 * 1024
_UPSTREAM_TIMEOUT = 20.0
_TYPES = ("text/html", "text/css", "text/plain", "application/javascript", "text/javascript", "application/json",
          "image/", "font/", "application/font", "application/pdf", "video/", "audio/")


#: paths under /web/ that mean "the Design page" but are not site pages (the site's Design page
#: is studio.html, which opens the person's Studio too, unless a lesson is named in its #hash)
DESIGN_ALIASES = ("studio", "design", "my-studio", "your-studio")

#: the website's well-known local port: qccd.academy's Agent button links to
#: http://127.0.0.1:47100/web/<page> (qccd/site/nav.html carries the same number)
WEB_PORT = 47100


def web_port_default() -> int:
    try:
        return int(os.environ.get("QCCD_WEB_PORT") or WEB_PORT)
    except ValueError:
        return WEB_PORT


def site_url() -> str:
    return (os.environ.get("QCCD_SITE_URL") or "https://qccd.academy").rstrip("/")


@dataclass
class Page:
    status: int
    ctype: str
    body: bytes
    location: str | None = None
    stale: bool = False
    fetched_at: float = 0.0
    etag: str | None = None
    last_modified: str | None = None


class MirrorError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):      # noqa: D401 - urllib hook
        return None


def _q(path: str) -> str:
    """A clean path as it goes on the wire (a decoded `@` or `%` is encoded again)."""
    return urllib.parse.quote(urllib.parse.unquote(path), safe="/~-._")


def clean_path(path: str) -> str:
    """The site path a request names, or MirrorError: plain characters only, no `..`."""
    path = path or ""
    if len(path) > 400 or not _PATH_OK.fullmatch(path):
        raise MirrorError(400, "that is not a path on the site")
    segs = urllib.parse.unquote(path).split("/")
    if any(s in ("..", ".") for s in segs) or "\\" in urllib.parse.unquote(path):
        raise MirrorError(400, "that is not a path on the site")
    return path.lstrip("/")


class SiteMirror:
    """Fetches site paths with a bounded in-memory cache.  A fresh entry is served as is; a
    stale one is revalidated (ETag / Last-Modified), and served stale when the site cannot
    be reached."""

    def __init__(self, base: str | None = None):
        self.base = (base or site_url()).rstrip("/")
        self._cache: dict[str, Page] = {}
        self._bytes = 0
        self._lock = threading.Lock()
        self._opener = urllib.request.build_opener(_NoRedirect())

    def get(self, path: str) -> Page:
        path = clean_path(path)
        with self._lock:
            hit = self._cache.get(path)
        if hit and time.time() - hit.fetched_at < _FRESH_S:
            return hit
        try:
            page = self._fetch(path, hit)
        except MirrorError:
            raise
        except (OSError, urllib.error.URLError) as exc:
            if hit:
                return Page(**{**hit.__dict__, "stale": True})
            raise MirrorError(502, f"{self.base} could not be reached from this computer ({exc})") from None
        if page.status in (200, 301, 302, 307, 308, 404):
            self._put(path, page)
        return page

    def _fetch(self, path: str, prior: Page | None) -> Page:
        req = urllib.request.Request(f"{self.base}/{_q(path)}", headers={
            "User-Agent": "qccd-workspace-mirror/1", "Accept-Encoding": "gzip"})
        if prior and prior.status == 200:
            if prior.etag:
                req.add_header("If-None-Match", prior.etag)
            if prior.last_modified:
                req.add_header("If-Modified-Since", prior.last_modified)
        try:
            with self._opener.open(req, timeout=_UPSTREAM_TIMEOUT) as r:
                return self._page(r.status, r.headers, r.read())
        except urllib.error.HTTPError as e:
            if e.code == 304 and prior:
                return Page(**{**prior.__dict__, "fetched_at": time.time(), "stale": False})
            if e.code in (301, 302, 307, 308):
                return Page(status=e.code, ctype="text/plain", body=b"",
                            location=self._local_location(e.headers.get("Location") or "", path),
                            fetched_at=time.time())
            if e.code == 404:
                return Page(status=404, ctype="text/plain", body=b"", fetched_at=time.time())
            raise MirrorError(502, f"the site answered {e.code} for /{path}") from None

    def _page(self, status: int, headers, raw: bytes) -> Page:
        if (headers.get("Content-Encoding") or "").lower() == "gzip":
            raw = gzip.decompress(raw)
        ctype = (headers.get("Content-Type") or "application/octet-stream").split(";")[0].strip().lower()
        if not ctype.startswith(_TYPES):
            ctype = "application/octet-stream"
        return Page(status=status, ctype=ctype, body=raw, fetched_at=time.time(),
                    etag=headers.get("ETag"), last_modified=headers.get("Last-Modified"))

    def _local_location(self, loc: str, path: str) -> str | None:
        """An upstream redirect, as a mirror path (`/learn` -> `/learn/`); None when it leaves
        the site."""
        u = urllib.parse.urljoin(f"{self.base}/{_q(path)}", loc)
        b = urllib.parse.urlsplit(self.base)
        t = urllib.parse.urlsplit(u)
        if (t.scheme, t.netloc) != (b.scheme, b.netloc):
            return None
        return "/web" + (t.path if t.path.startswith("/") else "/" + t.path) + (f"?{t.query}" if t.query else "")

    def _put(self, path: str, page: Page) -> None:
        with self._lock:
            old = self._cache.pop(path, None)
            if old:
                self._bytes -= len(old.body)
            self._cache[path] = page
            self._bytes += len(page.body)
            while self._bytes > _CACHE_BYTES and self._cache:
                k = next(iter(self._cache))            # oldest insertion first
                self._bytes -= len(self._cache.pop(k).body)

    def site_api(self, method: str, path: str, query: str, body: bytes, session: str | None) -> tuple:
        """One request of the site's comments API, as the person's browser would make it on
        the site: their session (if any) as the site's cookie, and the site's own Origin."""
        path = clean_path(path)
        url = f"{self.base}/api/{_q(path)}" + (f"?{query}" if query else "")
        req = urllib.request.Request(url, data=body if method != "GET" else None, method=method, headers={
            "User-Agent": "qccd-workspace-mirror/1", "Accept": "application/json", "Origin": self.base,
            "Content-Type": "application/json"})
        if session:
            req.add_header("Cookie", f"qccd_session={session}")
        try:
            with self._opener.open(req, timeout=_UPSTREAM_TIMEOUT) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()
        except (OSError, urllib.error.URLError) as exc:
            raise MirrorError(502, f"the site's comments could not be reached ({exc})") from None

    def official(self, path: str) -> Page:
        """The site's read-only Official leaderboard API, passed through uncached."""
        path = clean_path(path)
        req = urllib.request.Request(f"{self.base}/official/v1/{_q(path)}",
                                     headers={"User-Agent": "qccd-workspace-mirror/1", "Accept": "application/json"})
        try:
            with self._opener.open(req, timeout=_UPSTREAM_TIMEOUT) as r:
                return self._page(r.status, r.headers, r.read())
        except urllib.error.HTTPError as e:
            return Page(status=e.code, ctype="application/json",
                        body=json.dumps({"error": f"HTTP {e.code}"}).encode(), fetched_at=time.time())
        except (OSError, urllib.error.URLError) as exc:
            raise MirrorError(502, f"the official service could not be reached ({exc})") from None


# ---------------------------------------------------------------------- the page, with the chat

_COMMENTS_API = "var API = '/api';"
_COMMENTS_PAGE = r"var PAGE = location.pathname.replace(/index\.html$/, '');"
_SITE_COOKIE = "qccd_site"
_API_BODY_LIMIT = 64 * 1024
_HIDE_SITE_AGENT = ("<style>#qa-btn,#qa-panel{display:none!important}"
                    "#qccd-chat{position:fixed;right:14px;bottom:14px;z-index:2147482000;width:min(420px,calc(100vw - 28px));"
                    "height:min(600px,calc(100vh - 70px));border-radius:14px;overflow:hidden;"
                    "box-shadow:0 14px 40px rgba(18,16,28,.22),0 0 0 1px rgba(18,16,28,.08);background:#fff}"
                    "#qccd-chat.min{height:44px;width:260px}"
                    "#qccd-chat{transition:opacity .35s}#qccd-chat.qccd-yield{opacity:.14}#qccd-chat.qccd-yield:hover{opacity:1}"
                    "#qccd-chat iframe{display:block;width:100%;height:100%;border:0;background:#fff}"
                    "#qccd-mirror-studio{margin-left:10px;font-size:13px;padding:3px 10px;border:1px solid #bcd4f2;"
                    "border-radius:99px;background:#eef4fc;color:#1d4f91;text-decoration:none;white-space:nowrap}"
                    "@media print{#qccd-chat{display:none}}</style>")


def inject(page: str, cfg: dict) -> str:
    """A site page with the mirror's layer: a marker before any site script runs, the
    comment layer switched off, the site's own Agent button hidden (this IS the agent), and
    the chat + page actions before `</body>`."""
    marker = "<script>window.QCCD_MIRROR=1;</script>"
    if cfg.get("design_redirect"):
        # the Design page IS the person's live workspace: what the agent does to the design shows
        # there as it happens (lessons, #learn..., stay on the site's own Studio)
        marker += ("<script>(function(){var h=location.hash||'';if(!h||h==='#design')location.replace("
                   + json.dumps(cfg["studio_url"]) + ");})();</script>")
    m = re.search(r"<head[^>]*>", page, re.I)
    page = (page[:m.end()] + marker + page[m.end():]) if m else marker + page
    # the comment layer, keyed by the SITE's path, and reachable by the agent's page actions
    page = page.replace(_COMMENTS_PAGE, r"var PAGE = location.pathname.replace(/^\/web(?=\/)/, '')"
                                        r".replace(/index\.html$/, '');")
    page = page.replace(_COMMENTS_API, _COMMENTS_API + " window.QCCD_COMMENTS_HOOK = function () { return { "
                        "api: api, describe: describe, resolveAnchor: resolve, reload: load, "
                        "me: function () { return ME; }, page: function () { return PAGE; }, "
                        "threads: function () { return THREADS; } }; };")
    tail = (_HIDE_SITE_AGENT
            + '<script id="qccd-mirror-config" type="application/json">'
            + json.dumps(cfg).replace("</", "<\\/") + "</script>\n"
            + "<script>\n" + (_WEB / "pageact.js").read_text(encoding="utf-8") + "\n</script>\n"
            + "<script>\n" + (_WEB / "mirror.js").read_text(encoding="utf-8") + "\n</script>\n")
    at = page.lower().rfind("</body>")
    return (page[:at] + tail + page[at:]) if at >= 0 else page + tail


def mirror_csp(chat_origin: str) -> str:
    return ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self' data:; media-src 'self'; connect-src 'self'; "
            f"frame-src 'self' {chat_origin}; frame-ancestors 'self'; base-uri 'self'; form-action 'none'; "
            "object-src 'none'")


def _failed(message: str, status: int, studio_url: str, cfg: dict | None = None) -> tuple[int, str]:
    """A page that could not be shown.  With `cfg` it keeps the chat and the page tools, so
    neither the person nor an agent that followed a wrong link is stranded on it."""
    why = ("<p>The website pages come from qccd.academy, so they need an internet connection. "
           "Your own Studio works without one.</p>" if status >= 500 else "")
    body = (f"<main><h1>This page could not be shown</h1><p>{_html.escape(message)}</p>{why}"
            "<p><a href=\"/web/\">Go to the website's home page</a> &middot; "
            f"<a href=\"{_html.escape(studio_url)}\">open your Studio</a> (your own design)</p></main>")
    page = ("<!doctype html><html><head><meta charset=\"utf-8\"><title>QCCD website: not available</title>"
            "<style>body{font:15px/1.5 system-ui,sans-serif;max-width:720px;margin:48px auto;padding:0 16px;color:#1d1d1b}"
            "</style></head><body>" + body + "</body></html>")
    return status, (inject(page, cfg) if cfg else page)


def create_mirror_app(state, mirror: SiteMirror | None = None):
    """The mirror's own web app: GET pages under /web/, the Official leaderboard's reads,
    and nothing else.  It knows no cookie and no token."""
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse, Response
    import asyncio

    mirror = mirror or SiteMirror()
    state.ws.site_mirror = mirror                 # the site guide reads the same cache (site_guide.py)
    web_port = state.info["web_port"]
    chat_origin = f"http://127.0.0.1:{state.info['port']}"
    origin = f"http://127.0.0.1:{web_port}"
    studio_url = f"{chat_origin}/studio"
    cfg = {"chat_origin": chat_origin, "web_origin": origin, "studio_url": studio_url, "site": mirror.base}
    csp = mirror_csp(chat_origin)
    app = FastAPI(title="QCCD website mirror", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host")
        if host == f"localhost:{web_port}":
            # the chat's pairing lives on 127.0.0.1: the same pages there, or the chat is unpaired
            q = ("?" + request.url.query) if request.url.query else ""
            return Response(status_code=307, headers={"Location": f"{origin}{request.url.path}{q}"})
        if host != f"127.0.0.1:{web_port}":
            return JSONResponse({"error": {"code": "bad_host", "message": "unexpected Host header"}}, status_code=421)
        if request.method not in ("GET", "HEAD"):
            # only the site's comments API takes writes, and only from this origin's own pages
            if not request.url.path.startswith("/api/") or request.method not in ("POST", "DELETE"):
                return JSONResponse({"error": {"code": "read_only", "message": "the website mirror only serves pages"}},
                                    status_code=405)
            if request.headers.get("origin") != origin:
                return JSONResponse({"error": "cross-site request refused"}, status_code=403)
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        return resp

    def serve(page: Page, path: str) -> Response:
        if page.status in (301, 302, 307, 308):
            if not page.location:
                return HTMLResponse(_failed("that page redirects off the site", 404, studio_url, cfg)[1], status_code=404,
                                    headers={"Content-Security-Policy": csp})
            return Response(status_code=307, headers={"Location": page.location})
        if page.status == 404:
            code, body = _failed(f"qccd.academy has no page /{path}", 404, studio_url, cfg)
            return HTMLResponse(body, status_code=code, headers={"Content-Security-Policy": csp})
        headers = {"Content-Security-Policy": csp,
                   "Cache-Control": "no-store" if page.ctype == "text/html" else "max-age=300"}
        if page.stale:
            headers["X-QCCD-Mirror"] = "stale: the site could not be reached; this is the last copy"
        if page.ctype == "text/html":
            text = page.body.decode("utf-8", errors="replace")
            here = dict(cfg, design_redirect=True) if path.strip("/") == "studio.html" else cfg
            return HTMLResponse(inject(text, here), headers=headers)
        return Response(page.body, media_type=page.ctype, headers=headers)

    @app.get("/")
    async def root():
        return Response(status_code=307, headers={"Location": "/web/"})

    @app.get("/web")
    async def web_root():
        return Response(status_code=307, headers={"Location": "/web/"})

    @app.get("/studio")
    async def studio():
        # the site's "Open my Studio" button knows only this well-known port
        return Response(status_code=307, headers={"Location": studio_url})

    @app.get("/web/{path:path}")
    async def web(path: str):
        if path.strip("/").lower() in DESIGN_ALIASES:
            # the names a person or an agent gives the Design page: it is the person's own Studio
            return Response(status_code=307, headers={"Location": studio_url})
        try:
            page = await asyncio.to_thread(mirror.get, path)
        except MirrorError as e:
            code, body = _failed(str(e), e.status, studio_url, cfg)
            return HTMLResponse(body, status_code=code, headers={"Content-Security-Policy": csp})
        return serve(page, path)

    @app.get("/favicon.ico")
    async def favicon():
        try:
            page = await asyncio.to_thread(mirror.get, "favicon.ico")
        except MirrorError:
            return Response(status_code=404)
        return Response(page.body, media_type=page.ctype, status_code=page.status if page.status == 200 else 404)

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "DELETE"])
    async def site_api(path: str, request: Request):
        body = await request.body()
        if len(body) > _API_BODY_LIMIT:
            return JSONResponse({"error": "too large"}, status_code=413)
        try:
            status, headers, data = await asyncio.to_thread(
                mirror.site_api, request.method, path, request.url.query, body, request.cookies.get(_SITE_COOKIE))
        except MirrorError as e:
            return JSONResponse({"error": str(e)}, status_code=e.status)
        resp = Response(data, status_code=status, media_type="application/json",
                        headers={"Cache-Control": "no-store"})
        # the site's session cookie becomes this origin's, kept for /api/ only
        for sc in headers.get_all("Set-Cookie") or []:
            name, _, rest = sc.partition("=")
            if name.strip() != "qccd_session":
                continue
            value = rest.split(";", 1)[0].strip()
            attrs = {k.strip().lower(): v.strip() for k, _, v in
                     (a.partition("=") for a in rest.split(";")[1:])}
            max_age = attrs.get("max-age")
            if not value or max_age == "0":
                resp.delete_cookie(_SITE_COOKIE, path="/api/")
            else:
                resp.set_cookie(_SITE_COOKIE, value, path="/api/", httponly=True, samesite="strict",
                                max_age=int(max_age) if max_age and max_age.isdigit() else None)
        return resp

    @app.get("/official/v1/{path:path}")
    async def official(path: str):
        try:
            page = await asyncio.to_thread(mirror.official, path)
        except MirrorError as e:
            return JSONResponse({"error": str(e)}, status_code=e.status)
        return Response(page.body, media_type=page.ctype, status_code=page.status,
                        headers={"Content-Security-Policy": csp, "Cache-Control": "no-store"})

    return app
