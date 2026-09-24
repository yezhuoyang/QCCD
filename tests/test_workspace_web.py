"""The website through the workspace: the mirror, the chat frame, and the agent's page tools.

A small local copy of a site stands in for qccd.academy (QCCD_SITE_URL), so nothing here
needs the network.  Covered: what the mirror serves and refuses, that its origin gets no
authority over the workspace, the page-action round trip and who may take part in it, what
a message sent from a page carries to the agent, and -- in real Chrome -- the chat frame on
a mirrored page doing each page action for real.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from starlette.testclient import TestClient  # noqa: E402

from qccd.workspace import mirror as mirror_mod  # noqa: E402
from qccd.workspace.app import Workspace  # noqa: E402
from qccd.workspace.collab import delivery_text  # noqa: E402
from qccd.workspace.mirror import MirrorError, SiteMirror, clean_path, create_mirror_app, inject  # noqa: E402
from qccd.workspace.service import ServiceState, create_app  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PORT, WEB_PORT = 8765, 8766
BASE, WEB = f"http://127.0.0.1:{PORT}", f"http://127.0.0.1:{WEB_PORT}"
AGENT = {"Authorization": "Bearer agent-tok"}
OWNER = {"Authorization": "Bearer owner-tok"}
HUMAN = {"kind": "human", "id": "view:t", "label": "Studio"}

PAGES = {
    "index.html": """<!doctype html><html><head><meta charset="utf-8"><title>Home - Test site</title></head><body>
<nav id="sitenav"><a href="rules/">Rules</a> <a href="learn/">Learn</a> <button id="qa-btn">Agent</button></nav>
<main><h1>Welcome</h1><p>This is the home page.</p></main>
<script>(function(){ var API = '/api';
  var PAGE = location.pathname.replace(/index\\.html$/, '');
  var ME = null, THREADS = [];
  function api(){} function describe(){} function resolve(){} function load(){}
  window.__comments = PAGE; })();</script>
</body></html>""",
    "rules/index.html": """<!doctype html><html><head><meta charset="utf-8"><title>Rules - Test site</title></head><body>
<nav id="sitenav"><a href="../">Home</a></nav>
<main>
<h1>The rules</h1>
<h2 id="r1">R1 capacity</h2><p>No site holds more ions than its capacity.</p>
<h2 id="r7">R7 cooling</h2><p id="r7p">A two-qubit gate needs cold ions.</p>
<button id="reveal" onclick="document.getElementById('ans').textContent='Revealed'">Show answer</button>
<p id="ans">hidden</p>
<label for="q">Search</label><input id="q" type="search">
<select id="sel"><option value="a">Alpha</option><option value="b">Beta</option></select>
<a id="ext" href="https://example.com/">Elsewhere</a>
<figure><iframe src="ex/a.html#embed&amp;step=1" width="400" height="200"></iframe><figcaption>Example A</figcaption></figure>
</main></body></html>""",
    "rules/ex/a.html": """<!doctype html><html><head><meta charset="utf-8"><title>Example A</title></head><body>
<input type="range" id="slider" min="0" max="9" value="1"><button id="play">Play</button>
<script>function seek(i){ document.getElementById('slider').value = i; }</script></body></html>""",
    "learn/index.html": """<!doctype html><html><head><meta charset="utf-8"><title>Learn - Test site</title></head>
<body><main><h1>Learn</h1></main></body></html>""",
    "studio.html": """<!doctype html><html><head><meta charset="utf-8"><title>Studio - Test site</title></head><body>
<nav id="sitenav"><a href="./">Home</a></nav>
<main><h1>Studio</h1><svg id="svg" width="500" height="300"></svg><div style="height:2400px"></div></main>
<script>
var sites = [];
window.EDITOR = {
  addSite: function (x, y, id, o) { var s = {x: x, y: y, id: id || ('S' + sites.length), zone: (o || {}).zone};
                                    sites.push(s); return {ok: true, id: s.id, where: document.body}; },
  state: function () { var n = {}; sites.forEach(function (s) { n[s.id] = {pos: [s.x, s.y]}; });
                       return {device: {nodes: n, segments: {}}}; },
  problems: function () { return []; }, program: function () { return []; },
  lessonList: function () { return [{id: 'A1', part: 'A', title: 'Two sites'}]; },
  lessonState: function () { return {id: 'A1', stage: 0, passed: sites.length >= 2}; },
  lessonCheck: function () { return {ok: sites.length >= 2, passed: sites.length >= 2}; },
  saveProject: function () { window.__saved = true; return 'saved'; }
};
function seek(i) {}
</script></body></html>""",
    "official/v1/tasks": """{"tasks": []}""",
    "official/v1/leaderboard/ghz4@1": """{"rows": []}""",
}


class _Site:
    """A static site on a free port, recording what it was asked and how it answered."""

    def __init__(self, root: Path):
        self.root = root
        for rel, text in PAGES.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        self.log: list = []
        self.api_seen: list = []            # what the fake comments API was sent (headers)
        self.threads: list = []
        site = self

        class H(http.server.SimpleHTTPRequestHandler):
            """Static files, and at /api/ a small stand-in for the site's comments API."""
            def __init__(self, *a, **k):
                super().__init__(*a, directory=str(root), **k)

            def _api(self, method):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                site.api_seen.append({"method": method, "path": self.path, "cookie": self.headers.get("Cookie"),
                                      "origin": self.headers.get("Origin"), "host": self.headers.get("Host")})
                signed = "qccd_session=tok-1" in (self.headers.get("Cookie") or "")
                me = {"id": 1, "name": "Test Person", "color": 0}
                status, out, cookie = 200, None, None
                if method != "GET" and (self.headers.get("Origin") or "").split("://")[-1] != self.headers.get("Host"):
                    status, out = 403, {"error": "cross-site request refused"}
                elif self.path == "/api/me":
                    out = {"user": me if signed else None}
                elif self.path == "/api/login":
                    out, cookie = {"user": me}, "qccd_session=tok-1; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000"
                elif self.path.startswith("/api/threads") and method == "GET":
                    page = self.path.split("page=")[-1].replace("%2F", "/")
                    out = {"threads": [t for t in site.threads if t["page"] == page]} if signed else None
                    status = 200 if signed else 401
                elif self.path == "/api/threads" and method == "POST" and signed:
                    t = {"id": len(site.threads) + 1, "page": body["page"], "anchor": body["anchor"], "user": me,
                         "resolved": None, "comments": [{"id": 1, "text": body["text"], "user": me}]}
                    site.threads.append(t)
                    out = {"thread": t}
                else:
                    status, out = (401 if not signed else 404), {"error": "no"}
                data = json.dumps(out).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                if cookie:
                    self.send_header("Set-Cookie", cookie)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path.startswith("/api/"):
                    return self._api("GET")
                return super().do_GET()

            def do_POST(self):
                return self._api("POST")

            def log_request(self, code="-", size="-"):
                site.log.append((self.path, int(code) if str(code).isdigit() else code))

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def site(tmp_path):
    s = _Site(tmp_path / "site")
    yield s
    try:
        s.stop()
    except Exception:
        pass


@pytest.fixture
def svc(tmp_path, site, monkeypatch):
    monkeypatch.setenv("QCCD_SITE_URL", site.url)
    ws = Workspace.init(tmp_path / "ws", "ghz4@1")
    info = {"port": PORT, "web_port": WEB_PORT, "agent_token": "agent-tok", "owner_token": "owner-tok",
            "pid": os.getpid()}
    state = ServiceState(ws, info)
    yield ws, state, TestClient(create_app(state), base_url=BASE), TestClient(
        create_mirror_app(state, SiteMirror(site.url)), base_url=WEB)
    ws.close()


def _paired(state, client):
    code = state.new_pair_code()
    r = client.post("/api/pair", json={"code": code})
    assert r.status_code == 200
    return r.json()["csrf"]


# ---------------------------------------------------------------------- the mirror itself

def test_paths_are_plain_site_paths():
    assert clean_path("/rules/") == "rules/"
    assert clean_path("board/bb144/01_x.html") == "board/bb144/01_x.html"
    for bad in ("../secret", "rules/../../x", "%2e%2e/x", "a/%2E/b", "a\\b", "x?y", "<script>", "a" * 500):
        with pytest.raises(MirrorError):
            clean_path(bad)


def test_the_mirror_caches_revalidates_and_serves_stale(site, monkeypatch):
    m = SiteMirror(site.url)
    a = m.get("rules/")
    assert a.status == 200 and a.ctype == "text/html" and b"R7 cooling" in a.body
    n = len(site.log)
    m.get("rules/")
    assert len(site.log) == n                                          # fresh: from the cache
    monkeypatch.setattr(mirror_mod, "_FRESH_S", 0.0)
    b = m.get("rules/")
    assert site.log[-1] == ("/rules/", 304) and b.body == a.body       # revalidated, not refetched
    r = m.get("learn")
    assert r.status in (301, 302, 307, 308) and r.location == "/web/learn/"
    assert m.get("nope.html").status == 404
    site.stop()
    s = m.get("rules/")
    assert s.stale and s.body == a.body                                # the site is down: last copy
    with pytest.raises(MirrorError) as e:
        m.get("index.html")                                            # never fetched: nothing to show
    assert e.value.status == 502


def test_inject_hooks_the_comments_and_adds_the_chat():
    page = PAGES["index.html"]
    out = inject(page, {"chat_origin": BASE, "web_origin": WEB, "studio_url": BASE + "/studio"})
    head = out.index("<head>") + len("<head>")
    assert out[head:].startswith("<script>window.QCCD_MIRROR=1;</script>")   # before any site script
    # the comment layer stays on, keyed by the SITE's path, and the agent can reach its functions
    assert "var PAGE = location.pathname.replace(/^\\/web(?=\\/)/, '').replace(/index\\.html$/, '');" in out
    assert "var API = '/api'; window.QCCD_COMMENTS_HOOK = function () { return { api: api, describe: describe" in out
    assert "#qa-btn,#qa-panel{display:none!important}" in out
    assert out.index('id="qccd-mirror-config"') < out.rindex("</body>")
    assert "window.QCCD_PAGE" in out and "/chatframe?page=" in out


def test_the_mirror_serves_pages_and_nothing_else(svc):
    ws, state, c, w = svc
    r = w.get("/web/rules/")
    assert r.status_code == 200 and "R7 cooling" in r.text and "qccd-mirror-config" in r.text
    csp = r.headers["content-security-policy"]
    assert f"frame-src 'self' {BASE}" in csp and "frame-ancestors 'self'" in csp and "form-action 'none'" in csp
    assert r.headers["x-frame-options"] == "SAMEORIGIN" and "set-cookie" not in r.headers
    assert w.get("/web/learn", follow_redirects=False).headers["location"] == "/web/learn/"
    assert w.get("/", follow_redirects=False).headers["location"] == "/web/"
    assert w.get("/web/missing.html").status_code == 404
    assert w.get("/web/rules/../x").status_code in (400, 404)
    assert w.get("/official/v1/tasks").json() == {"tasks": []}
    assert w.get("/official/v1/leaderboard/ghz4%401").json() == {"rows": []}      # a task id has an @
    # read-only, loopback host only; localhost goes to 127.0.0.1, where the chat's pairing is
    assert w.post("/web/rules/", json={}).status_code == 405
    assert w.get("/web/", headers={"Host": "evil.example:8766"}).status_code == 421
    r = w.get("/web/rules/", headers={"Host": f"localhost:{WEB_PORT}"}, follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == f"{WEB}/web/rules/"


def test_the_mirror_origin_has_no_authority(svc, site):
    ws, state, c, w = svc
    csrf = _paired(state, c)
    # the pairing cookie reaches the mirror's port too (cookies ignore ports): it means nothing there
    for k, v in dict(c.cookies).items():
        w.cookies.set(k, v)
    assert w.get("/chatframe").status_code == 404
    # /api/ there is the SITE's comments API: the workspace's never answers, and its cookie never travels
    n = len(site.api_seen)
    for path in ("/api/whoami", "/api/context", "/api/events"):
        r = w.get(path)
        assert r.status_code in (401, 404) and "workspace_id" not in r.text and "csrf" not in r.text
    assert all(x["cookie"] is None for x in site.api_seen[n:]) and len(site.api_seen) == n + 3
    # /studio is only a way across: a redirect to the workspace's own origin, which checks for itself
    assert w.get("/studio", follow_redirects=False).headers["location"] == f"{BASE}/studio"
    # and the workspace refuses the mirror's origin, even with the cookie and the right CSRF token
    r = c.post("/api/prompts/send", json={"body": {"text": "hi", "intent": "question"}},
               headers={"X-QCCD-CSRF": csrf, "Origin": WEB})
    assert r.status_code == 403 and r.json()["error"]["code"] == "bad_origin"


def test_the_chat_frame_may_be_framed_only_by_the_mirror(svc):
    ws, state, c, w = svc
    r = c.get("/chatframe?page=/web/rules/")
    assert r.status_code == 200 and "x-frame-options" not in {k.lower() for k in r.headers}
    csp = r.headers["content-security-policy"]
    assert f"frame-ancestors {WEB}" in csp and "connect-src 'self'" in csp
    cfg = json.loads(r.text.split('id="qccd-live-config" type="application/json">')[1].split("</script>")[0])
    assert cfg["mode"] == "page" and cfg["framed"] and cfg["parent_origin"] == WEB and cfg["page"] == "/web/rules/"
    assert json.loads(c.get("/chatframe?page=https://evil.example/").text.split(
        'type="application/json">')[1].split("</script>")[0])["page"] == "/web/"
    assert c.get("/studio").headers.get("x-frame-options") == "DENY"
    assert WEB in c.get("/open-web").text


# ---------------------------------------------------------------------- page actions

def _page_view(c, csrf, url="/web/rules/", title="Rules - Test site"):
    r = c.post("/api/views", json={"page": {"url": url, "title": title, "kind": "rules", "site": "test"}},
               headers={"X-QCCD-CSRF": csrf})
    assert r.status_code == 200
    return r.json()["id"]


def test_a_page_action_goes_to_the_page_and_back(svc):
    ws, state, c, w = svc
    human = TestClient(c.app, base_url=BASE)
    agent2 = TestClient(c.app, base_url=BASE)
    csrf = _paired(state, human)
    assert c.post("/api/page-actions", json={"action": "read"}, headers=AGENT).json()["error"]["code"] == "no_page"
    vid = _page_view(human, csrf)
    other = _page_view(human, csrf, "/web/", "Home")
    assert [p["url"] for p in c.get("/api/pages", headers=AGENT).json()["pages"]] == ["/web/", "/web/rules/"]
    cursor = ws.last_event_seq()
    seen = {}

    def page():                                   # the page: take the action off the stream, answer it
        for _ in range(100):
            evs = [e for e in ws.events_since(cursor)["events"] if e["type"] == "page.action"]
            if evs:
                p = evs[0]["payload"]
                seen.update(p)
                h = {"X-QCCD-CSRF": csrf}
                seen["agent"] = agent2.post(f"/api/page-actions/{p['action_id']}/result", json={"ok": True},
                                            headers=AGENT).status_code
                seen["wrong_view"] = human.post(f"/api/page-actions/{p['action_id']}/result", json={"ok": True},
                                                headers={**h, "X-QCCD-View": other}).status_code
                seen["page"] = human.post(f"/api/page-actions/{p['action_id']}/result",
                                          json={"ok": True, "result": {"title": "Rules - Test site"}},
                                          headers={**h, "X-QCCD-View": vid}).status_code
                return
            time.sleep(0.05)
    t = threading.Thread(target=page)
    t.start()
    r = c.post("/api/page-actions", json={"action": "highlight", "args": {"target": {"text": "R7"}, "note": "here"},
                                          "view_id": vid}, headers=AGENT)
    t.join()
    assert r.status_code == 200, r.text
    assert r.json() == {"view_id": vid, "page": {"url": "/web/rules/", "title": "Rules - Test site"},
                        "action": "highlight", "ok": True, "result": {"title": "Rules - Test site"}}
    assert seen["view_id"] == vid and seen["args"]["note"] == "here" and seen["actor"]["kind"] == "agent"
    assert (seen["agent"], seen["wrong_view"], seen["page"]) == (403, 403, 200)
    assert not state.page_waits


def test_page_actions_are_the_agents_and_bounded(svc):
    ws, state, c, w = svc
    csrf = _paired(state, c)
    vid = _page_view(c, csrf)
    assert c.post("/api/page-actions", json={"action": "read"}, headers={"X-QCCD-CSRF": csrf}).status_code == 403
    assert c.post("/api/page-actions", json={"action": "eval", "args": {"js": "1"}}, headers=AGENT).status_code == 422
    assert c.post("/api/page-actions", json={"action": "read", "args": {"x": "y" * 70000}},
                  headers=AGENT).status_code == 422
    r = c.post("/api/page-actions", json={"action": "read", "wait_s": 1}, headers=AGENT)
    assert r.status_code == 504 and r.json()["error"]["code"] == "page_timeout"
    assert c.post("/api/page-actions/pa_nope/result", json={"ok": True},
                  headers={"X-QCCD-CSRF": csrf, "X-QCCD-View": vid}).status_code == 404
    # a closed page is not a target; a page that comes back (navigation) is again
    c.patch(f"/api/views/{vid}", json={"closed": True}, headers={"X-QCCD-CSRF": csrf})
    assert c.get("/api/pages", headers=AGENT).json()["pages"] == []
    c.patch(f"/api/views/{vid}", json={"closed": False, "page": {"url": "/web/", "title": "Home", "site": "t"}},
            headers={"X-QCCD-CSRF": csrf})
    assert c.get("/api/pages", headers=AGENT).json()["pages"][0]["url"] == "/web/"


def test_a_message_from_a_page_carries_the_page(svc):
    ws, state, c, w = svc
    s = ws.register_session({"kind": "human", "id": "cli"}, client="generic", mode="pull", label="test agent")
    page = {"url": "/web/rules/", "title": "Rules - Test site", "kind": "rules", "site": "https://qccd.academy",
            "selection": "A two-qubit gate needs cold ions."}
    v = ws.register_view(HUMAN, page=page)
    ws.update_view(v["id"], {"target_session": s["id"]}, HUMAN)
    r = ws.send_prompt(HUMAN, body={"text": "What does this mean?", "intent": "question", "mode": "ask",
                                    "anchors": [{"kind": "page", "url": "/web/rules/", "quote": page["selection"]}]},
                       view_id=v["id"], context={"page": page})
    sent = ws.prompt(r["prompt_id"])["latest_sent"]
    ctx = ws.context_snapshot(sent["body"]["context_snapshot_id"])
    assert ctx["page"]["url"] == "/web/rules/" and ctx["page"]["selection"] == page["selection"]
    text = delivery_text(ws, r["prompt_id"], sent, ctx, r["delivery"]["id"])
    assert 'website page "Rules - Test site" (/web/rules/' in text and "qccd_page_read" in text
    assert 'this text on the page: "A two-qubit gate needs cold ions."' in text
    item = [i for i in ws.conversation()["items"] if i["type"] == "user"][0]
    assert item["page"] == "Rules - Test site" and item["context"] == "a quote"
    ctx_agent = ws.context({"kind": "agent", "id": "agent:mcp"})
    assert ctx_agent["pages"][0]["url"] == "/web/rules/"


def test_the_mcp_page_tools_are_page_actions():
    from qccd.workspace.mcp_server import _tools, dispatch
    names = [t[0] for t in _tools()]
    assert "qccd_page_read" in names and "qccd_page_act" in names

    class Be:
        calls = []

        def call(self, method, path, body=None, *, timeout=60.0):
            self.calls.append((method, path, body))
            return {"ok": True}
    be = Be()
    dispatch(be, "qccd_page_read", {"section": "R7", "view_id": "v1"})
    dispatch(be, "qccd_page_act", {"action": "step", "target": {"frame": "f1"}, "delta": 2})
    assert be.calls == [("POST", "/api/page-actions", {"action": "read", "args": {"section": "R7"}, "view_id": "v1"}),
                        ("POST", "/api/page-actions", {"action": "step", "args": {"target": {"frame": "f1"}, "delta": 2},
                                                       "view_id": None})]


# ---------------------------------------------------------------------- real Chrome

CHROME = os.environ.get("CHROME") or "C:/Program Files/Google/Chrome/Application/chrome.exe"
needs_chrome = pytest.mark.skipif(not (shutil.which("node") and Path(CHROME).exists()), reason="needs node and Chrome")


@needs_chrome
def test_the_website_with_the_chat_in_chrome(tmp_path, site, monkeypatch):
    from qccd.workspace.runtime import ensure_service, service_request
    monkeypatch.setenv("QCCD_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("QCCD_CODEX", "none")
    monkeypatch.setenv("QCCD_CLAUDE", "none")          # nor a real Claude
    monkeypatch.setenv("QCCD_SITE_URL", site.url)
    Workspace.init(tmp_path / "ws", "ghz4@1").close()
    info = ensure_service(tmp_path / "ws", python=sys.executable)
    try:
        own = lambda m, p, b=None: service_request(info, m, p, b, token="owner", timeout=60)
        base, web = f"http://127.0.0.1:{info['port']}", f"http://127.0.0.1:{info['web_port']}"
        code = own("POST", "/api/pair-code", {})["code"]
        hdr = {"Authorization": f"Bearer {info['owner_token']}", "Content-Type": "application/json"}

        def act(action, **args):
            return {"http": {"method": "POST", "url": base + "/api/page-actions", "headers": hdr,
                             "body": {"action": action, "args": args}}}
        frame_ready = ("window.QCCD_LIVE && QCCD_LIVE.state().paired && QCCD_LIVE.state().connected && "
                       "!!QCCD_LIVE.state().view && (QCCD_LIVE.page() || {}).title === '%s'")
        steps = [
            {"wait": f"location.origin === '{web}' && !!document.getElementById('qccd-chat')", "timeout": 30000,
             "stopOnFail": True},                                                                          # 0
            {"frame": "/chatframe", "wait": frame_ready % "Rules - Test site", "timeout": 30000, "stopOnFail": True},  # 1
            act("read"),                                                                                   # 2
            act("read", section="R7", controls=False),                                                     # 3
            act("click", target={"text": "Show answer"}),                                                  # 4
            {"eval": "document.getElementById('ans').textContent"},                                        # 5
            act("fill", target={"selector": "#q"}, value="junction"),                                      # 6
            act("fill", target={"text": "Search"}, value="junction"),                                      # 7
            act("fill", target={"selector": "#sel"}, value="Beta"),                                        # 8
            {"eval": "document.getElementById('q').value + '|' + document.getElementById('sel').value"},   # 9
            act("step", target={"frame": "f1"}, delta=3),                                                  # 10
            {"eval": "document.querySelector('iframe').contentDocument.getElementById('slider').value"},   # 11
            act("click", target={"selector": "#qccd-chat"}),                                               # 12
            act("click", target={"selector": "#ext"}),                                                     # 13
            act("highlight", target={"selector": "#r7p"}, note="cold ions only"),                          # 14
            {"eval": "Array.prototype.some.call(document.querySelectorAll('[data-qccd-private]'), "
                     "function(e){ return e.textContent === 'cold ions only'; })"},                        # 15
            {"eval": "window.__comments === undefined && getComputedStyle(document.body).display !== 'none'"},  # 16
            # this origin cannot use the workspace: its API is another origin that sends no CORS
            {"eval": f"fetch('{base}/api/whoami', {{credentials: 'include'}}).then(function(){{ return 'read'; }}, "
                     "function(){ return 'blocked'; })"},                                                  # 17
            {"eval": "fetch('/api/whoami').then(function(r){ return r.status; })"},                        # 18
            # the person selects text and asks: the quote and the page go with the message
            {"eval": "(function(){ var r = document.createRange(); r.selectNodeContents(document.getElementById('r7p')); "
                     "var s = getSelection(); s.removeAllRanges(); s.addRange(r); return String(s); })()"},  # 19
            {"frame": "/chatframe", "wait": "(QCCD_LIVE.page() || {}).selection === 'A two-qubit gate needs cold ions.'",
             "timeout": 10000},                                                                            # 20
            {"frame": "/chatframe", "eval": "QCCD_LIVE.chatSend('What does this mean?').then(function(d){ "
                                            "return d && d.prompt_id; })"},                                # 21
            # a highlight inside an embedded example sits on that element, in page coordinates
            act("highlight", target={"frame": "f1", "selector": "#slider"}, note="inside"),                # 22
            {"eval": "(function(){ var f = document.querySelector('iframe').getBoundingClientRect(), "
                     "s = document.querySelector('iframe').contentDocument.getElementById('slider').getBoundingClientRect(), "
                     "b = document.querySelector('.qccd-hl').getBoundingClientRect(); "
                     "return Math.abs(b.left + 4 - (f.left + s.left)) < 2 && Math.abs(b.top + 4 - (f.top + s.top)) < 2; })()"},  # 23
            act("navigate", path="../"),                                                                   # 24
            {"wait": f"location.href === '{web}/web/'", "timeout": 15000},                                 # 25
            {"frame": "/chatframe", "wait": frame_ready % "Home - Test site", "timeout": 30000},            # 26
            {"sleep": 800},
            {"http": {"method": "GET", "url": base + "/api/pages", "headers": hdr}},                       # 28
        ]
        spec = tmp_path / "spec.json"
        spec.write_text(json.dumps({"pages": {"a": f"{base}/open-web#pair={code}&to=/web/rules/"}, "steps": steps}),
                        encoding="utf-8")
        r = subprocess.run(["node", str(REPO / "tests" / "workspace_browser.mjs"), str(spec)], capture_output=True,
                           timeout=300, cwd=REPO)
        out = json.loads(r.stdout.decode("utf-8") or "{}")
        st = out.get("steps", [])
        diag = json.dumps({"steps": st, "logs": out.get("logs"), "fatal": out.get("fatal")})[:6000]
        assert len(st) == len(steps), diag
        body = lambda i: json.loads(st[i]["body"])
        assert st[0]["ok"] and st[1]["ok"], diag
        rd = body(2)
        assert rd["ok"] and rd["result"]["title"] == "Rules - Test site", diag
        heads = [h["text"] for h in rd["result"]["headings"]]
        assert heads == ["The rules", "R1 capacity", "R7 cooling"], diag
        labels = {c["label"]: c for c in rd["result"]["controls"]}
        assert labels["Show answer"]["kind"] == "button" and labels["Search"]["kind"] == "input", diag
        assert labels["Elsewhere"]["href"] == "https://example.com/", diag
        assert rd["result"]["embedded"][0]["ref"] == "f1" and rd["result"]["embedded"][0]["caption"] == "Example A", diag
        assert rd["result"]["embedded"][0]["transport"]["step"] == 1, diag
        assert not any("Message the agent" in c["label"] for c in rd["result"]["controls"]), diag   # not the chat
        sec = body(3)["result"]
        assert sec["section"]["text"] == "R7 cooling" and "cold ions" in sec["text"] and "capacity" not in sec["text"], diag
        assert body(4)["ok"] and st[5]["value"] == "Revealed", diag
        assert body(6)["ok"] and body(7)["ok"] and body(8)["ok"] and st[9]["value"] == "junction|b", diag
        assert body(10)["ok"] and st[11]["value"] == "4", diag
        assert not body(12)["ok"] and "part of the chat" in body(12)["error"], diag
        assert not body(13)["ok"] and "leaves the site" in body(13)["error"], diag
        assert body(14)["ok"] and st[15]["value"] is True and st[16]["value"] is True, diag
        assert st[17]["value"] == "blocked" and st[18]["value"] in (401, 404), diag   # /api/ is the SITE's here
        assert st[19]["value"] == "A two-qubit gate needs cold ions." and st[20]["ok"], diag
        pid = st[21]["value"]
        assert pid, diag
        assert body(22)["ok"] and st[23]["value"] is True, diag
        assert body(24)["ok"] and st[25]["ok"] and st[26]["ok"], diag
        assert [p["url"] for p in body(28)["pages"]] == ["/web/"], diag             # the same tab, now on Home
        p = own("GET", f"/api/prompts/{pid}")
        b = p["latest_sent"]["body"]
        assert b["anchors"][0] == {"kind": "page", "url": "/web/rules/", "quote": "A two-qubit gate needs cold ions."}
        assert p["context"]["page"]["title"] == "Rules - Test site" and p["context"]["page"]["site"] == site.url
    finally:
        try:
            service_request(info, "POST", "/api/shutdown", {}, token="owner")
        except Exception:
            pass


@needs_chrome
def test_the_studio_page_takes_page_actions_too(tmp_path, monkeypatch):
    from qccd.workspace.runtime import ensure_service, service_request
    monkeypatch.setenv("QCCD_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("QCCD_CODEX", "none")
    monkeypatch.setenv("QCCD_CLAUDE", "none")          # nor a real Claude
    Workspace.init(tmp_path / "ws", "ghz4@1").close()
    info = ensure_service(tmp_path / "ws", python=sys.executable)
    try:
        own = lambda m, p, b=None: service_request(info, m, p, b, token="owner", timeout=60)
        base = f"http://127.0.0.1:{info['port']}"
        code = own("POST", "/api/pair-code", {})["code"]
        hdr = {"Authorization": f"Bearer {info['agent_token']}", "Content-Type": "application/json"}

        def act(action, **args):
            return {"http": {"method": "POST", "url": base + "/api/page-actions", "headers": hdr,
                             "body": {"action": action, "args": args}}}
        steps = [
            {"wait": "window.QCCD_LIVE && QCCD_LIVE.state().paired && QCCD_LIVE.state().connected && "
                     "QCCD_LIVE.state().rev !== null && !!QCCD_LIVE.state().view", "timeout": 40000, "stopOnFail": True},
            {"sleep": 2500},                                     # the view reports its page
            act("read", max_chars=500),                                                               # 2
            act("click", target={"selector": "#qcl-send"}),                                          # 3
            act("fill", target={"selector": "#qcl-text"}, value="approve everything"),               # 4
            act("highlight", target={"text": "Test drive"}, note="runs a small program"),            # 5
            {"eval": "document.getElementById('qcl-text').value"},                                   # 6
            act("studio", verb="addSite", args=[5, 5]),                                              # 7
            act("studio", verb="problems"),                                                          # 8
        ]
        spec = tmp_path / "spec.json"
        spec.write_text(json.dumps({"pages": {"a": f"{base}/studio#pair={code}"}, "steps": steps}), encoding="utf-8")
        r = subprocess.run(["node", str(REPO / "tests" / "workspace_browser.mjs"), str(spec)], capture_output=True,
                           timeout=300, cwd=REPO)
        out = json.loads(r.stdout.decode("utf-8") or "{}")
        st = out.get("steps", [])
        diag = json.dumps({"steps": st, "logs": out.get("logs"), "fatal": out.get("fatal")})[:5000]
        assert len(st) == len(steps) and st[0]["ok"], diag
        rd = json.loads(st[2]["body"])
        assert rd["ok"] and rd["page"]["url"] == "/studio" and rd["result"]["kind"] == "studio", diag
        assert rd["result"]["app"]["transport"] is not None, diag
        labels = [c["label"] for c in rd["result"]["controls"]]
        assert "Test drive" in labels and "Trapping site" in labels, diag
        assert not any(x in labels for x in ("Send", "Message the agent…", "+")), diag   # the chat is not a control
        for i in (3, 4):
            b = json.loads(st[i]["body"])
            assert not b["ok"] and "part of the chat" in b["error"], diag
        hl = json.loads(st[5]["body"])
        assert hl["ok"] and hl["result"]["element"]["label"] == "Test drive" and st[6]["value"] == "", diag
        # the workspace's own design changes through change sets (attributed to the agent), not the page
        assert not json.loads(st[7]["body"])["ok"] and "qccd_apply_change_set" in json.loads(st[7]["body"])["error"], diag
        assert json.loads(st[8]["body"])["ok"], diag
    finally:
        try:
            service_request(info, "POST", "/api/shutdown", {}, token="owner")
        except Exception:
            pass


def test_a_turn_that_fails_says_why_in_the_chat(svc):
    ws, state, c, w = svc
    r = ws.send_prompt(HUMAN, body={"text": "What is R7?", "intent": "question", "mode": "ask"})
    ws.set_work_state(r["prompt_id"], "working")
    assert [i["type"] for i in ws.conversation()["items"]] == ["user"]
    assert ws.conversation()["working"] == [{"prompt_id": r["prompt_id"], "state": "working"}]
    ws.set_work_state(r["prompt_id"], "waiting_input", "Codex turn failed: You've hit your usage limit.")
    conv = ws.conversation()
    assert conv["working"] == [] and conv["items"][-1]["type"] == "notice"
    assert conv["items"][-1]["text"] == "Codex turn failed: You've hit your usage limit."


def _mcp_python():
    import importlib.util
    if importlib.util.find_spec("mcp") is not None:
        return sys.executable
    for cand in (REPO / ".venv" / "Scripts" / "python.exe", REPO / ".venv" / "bin" / "python"):
        if cand.exists():
            return str(cand)
    return None


@needs_chrome
@pytest.mark.skipif(_mcp_python() is None, reason="the MCP SDK is not installed (requirements-agent.txt)")
def test_an_mcp_agent_answers_a_question_from_the_page(tmp_path, site, monkeypatch):
    """The whole loop with a real MCP client standing in for Claude Code (pull mode): the
    person selects text on a mirrored page and asks; the agent reads the prompt, reads the
    page, highlights the answer on it, and replies; the person sees both."""
    from qccd.workspace.runtime import ensure_service, service_request
    py = _mcp_python()
    monkeypatch.setenv("QCCD_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("QCCD_CODEX", "none")
    monkeypatch.setenv("QCCD_CLAUDE", "none")          # nor a real Claude
    monkeypatch.setenv("QCCD_SITE_URL", site.url)
    root = tmp_path / "ws"
    Workspace.init(root, "ghz4@1").close()
    info = ensure_service(root, python=py)
    try:
        own = lambda m, p, b=None: service_request(info, m, p, b, token="owner", timeout=60)
        base, web = f"http://127.0.0.1:{info['port']}", f"http://127.0.0.1:{info['web_port']}"
        script = [
            {"sleep": 14},                                                               # the person asks meanwhile
            {"tool": "qccd_get_context", "args": {}},                                    # 1
            {"tool": "qccd_page_read", "args": {"section": "R7", "controls": False}},    # 2
            {"tool": "qccd_page_act", "args": {"action": "highlight", "target": {"selector": "#r7p"},
                                               "note": "R7: both ions must be cold"}},   # 3
            {"tool": "qccd_manage_comment", "args": {"action": "reply", "prompt_id": "$prev.1.unread_prompts.0.prompt_id",
                                                     "text": "R7 says a two-qubit gate needs cold ions; I highlighted it.",
                                                     "work_state": "ready_for_review"}},  # 4
        ]
        sp = tmp_path / "mcp.json"
        sp.write_text(json.dumps(script), encoding="utf-8")
        agent = subprocess.Popen([py, str(REPO / "tests" / "workspace_mcp_client.py"), str(root), str(sp),
                                  "--client", "claude"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env=dict(os.environ))
        t0 = time.time()
        while time.time() - t0 < 30 and not any(s["mode"] == "pull" for s in own("GET", "/api/sessions")["sessions"]):
            time.sleep(0.3)
        code = own("POST", "/api/pair-code", {})["code"]
        steps = [
            {"wait": f"location.origin === '{web}' && !!document.getElementById('qccd-chat')", "timeout": 30000,
             "stopOnFail": True},
            {"frame": "/chatframe", "wait": "window.QCCD_LIVE && QCCD_LIVE.state().paired && QCCD_LIVE.state().connected "
             "&& !!QCCD_LIVE.state().view && (QCCD_LIVE.page() || {}).title === 'Rules - Test site'", "timeout": 30000,
             "stopOnFail": True},
            {"eval": "(function(){ var r = document.createRange(); r.selectNodeContents(document.getElementById('r7p')); "
                     "getSelection().removeAllRanges(); getSelection().addRange(r); return 1; })()"},
            {"frame": "/chatframe", "wait": "!!(QCCD_LIVE.page() || {}).selection", "timeout": 10000},
            {"frame": "/chatframe", "eval": "QCCD_LIVE.chatSend('What does this rule mean?').then(function(d){ "
                                            "return d && d.delivery && d.delivery.session_id; })"},          # 4
            {"wait": "Array.prototype.some.call(document.querySelectorAll('[data-qccd-private]'), function(e){ "
                     "return e.textContent === 'R7: both ions must be cold'; })", "timeout": 60000},           # 5
            {"frame": "/chatframe", "wait": "QCCD_LIVE.chat().items.some(function(i){ return i.type === 'agent' && "
             "/cold ions/.test(i.text); })", "timeout": 30000},                                                # 6
        ]
        spec = tmp_path / "spec.json"
        spec.write_text(json.dumps({"pages": {"a": f"{base}/open-web#pair={code}&to=/web/rules/"}, "steps": steps}),
                        encoding="utf-8")
        r = subprocess.run(["node", str(REPO / "tests" / "workspace_browser.mjs"), str(spec)], capture_output=True,
                           timeout=300, cwd=REPO)
        out, err = agent.communicate(timeout=180)
        drive = json.loads(r.stdout.decode("utf-8") or "{}")
        st = drive.get("steps", [])
        res = json.loads(out)
        diag = json.dumps({"steps": st, "logs": drive.get("logs"), "mcp": res, "err": err.decode()[-2000:]})[:8000]
        assert "qccd_page_read" in res["tools"] and "qccd_page_act" in res["tools"], diag
        assert len(st) == len(steps) and st[4]["value"], diag                     # delivered to the MCP session
        ctx = res["results"][0]["structured"]            # printed results leave out the sleep
        u = ctx["unread_prompts"][0]
        assert u["text"] == "What does this rule mean?" and u["page"] == "Rules - Test site", diag
        assert u["anchors"][0] == {"kind": "page", "url": "/web/rules/", "quote": "A two-qubit gate needs cold ions."}, diag
        assert ctx["pages"][0]["url"] == "/web/rules/", diag
        read = res["results"][1]["structured"]
        assert read["ok"] and read["page"]["url"] == "/web/rules/" and "cold ions" in read["result"]["text"], diag
        assert res["results"][2]["structured"]["ok"] and not res["results"][3]["is_error"], diag
        assert st[5]["ok"] and st[6]["ok"], diag                                  # the person saw both
    finally:
        try:
            service_request(info, "POST", "/api/shutdown", {}, token="owner")
        except Exception:
            pass


def test_the_website_listens_on_its_well_known_port(monkeypatch):
    """qccd.academy's Agent button links to http://127.0.0.1:47100/web/<page>: the mirror takes
    that port when it is free, and any free port when another workspace holds it."""
    from qccd.workspace.mirror import WEB_PORT
    from qccd.workspace.runtime import free_port
    from qccd.workspace.service import _bind_web
    assert WEB_PORT == 47100
    p = free_port()
    monkeypatch.setenv("QCCD_WEB_PORT", str(p))
    s = _bind_web(None)
    try:
        assert s.getsockname()[1] == p
        s2 = _bind_web(p)                       # taken, and its last port is the same one
        try:
            assert s2.getsockname()[1] not in (p, 0)
        finally:
            s2.close()
    finally:
        s.close()


def test_the_mirror_leads_to_the_studio(svc):
    ws, state, c, w = svc
    r = w.get("/studio", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == f"{BASE}/studio"


@needs_chrome
def test_the_agent_works_visibly_and_designs_in_the_studio(tmp_path, site, monkeypatch):
    """What a person watching sees: a cursor with the agent's name that moves to what it acts
    on, and the Studio being driven through its own API; plus scroll-by and wait for pacing."""
    from qccd.workspace.runtime import ensure_service, service_request
    monkeypatch.setenv("QCCD_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("QCCD_CODEX", "none")
    monkeypatch.setenv("QCCD_CLAUDE", "none")          # nor a real Claude
    monkeypatch.setenv("QCCD_SITE_URL", site.url)
    Workspace.init(tmp_path / "ws", "ghz4@1").close()
    info = ensure_service(tmp_path / "ws", python=sys.executable)
    try:
        own = lambda m, p, b=None: service_request(info, m, p, b, token="owner", timeout=60)
        base, web = f"http://127.0.0.1:{info['port']}", f"http://127.0.0.1:{info['web_port']}"
        hdr = {"Authorization": f"Bearer {info['owner_token']}", "Content-Type": "application/json"}

        def act(action, **args):
            return {"http": {"method": "POST", "url": base + "/api/page-actions", "headers": hdr,
                             "body": {"action": action, "args": args}}}
        cur = ("(function(){ var c = document.getElementById('qccd-agent-cursor'); if (!c) return null; "
               "var r = c.getBoundingClientRect(); return JSON.stringify({label: c.querySelector('span').textContent, "
               "x: r.left, y: r.top, inside: r.left >= 0 && r.top >= 0 && r.left < innerWidth && r.top < innerHeight}); })()")
        steps = [
            {"wait": f"location.origin === '{web}' && !!document.getElementById('qccd-chat')", "timeout": 30000,
             "stopOnFail": True},                                                                          # 0
            {"frame": "/chatframe", "wait": "window.QCCD_LIVE && QCCD_LIVE.state().connected && "
             "!!QCCD_LIVE.state().view && (QCCD_LIVE.page() || {}).title === 'Studio - Test site'", "timeout": 30000,
             "stopOnFail": True},                                                                          # 1
            act("studio", verb="verbs"),                                                                   # 2
            act("studio", verb="addSite", args=[1, 1, None, {"zone": "trap"}]),                            # 3
            {"eval": cur},                                                                                 # 4
            act("studio", verb="addSite", args=[2, 1]),                                                    # 5
            act("studio", verb="lessonCheck"),                                                             # 6
            act("studio", verb="saveProject"),                                                             # 7
            {"eval": "window.__saved === undefined"},                                                      # 8
            act("scroll", by=600),                                                                         # 9
            act("scroll", by="-page"),                                                                     # 10
            act("wait", ms=400, note="look at the two sites"),                                             # 11
            {"eval": cur},                                                                                 # 12
        ]
        spec = tmp_path / "spec.json"
        # a lesson page is the site's own Studio (the bare Design page is the person's workspace,
        # test_the_agents_work_happens_in_the_persons_studio)
        spec.write_text(json.dumps({"pages": {"a": f"{base}/open-web#pair={own('POST', '/api/pair-code', {})['code']}"
                                                   "&to=/web/studio.html%23learn%3DA1"}, "steps": steps}), encoding="utf-8")
        r = subprocess.run(["node", str(REPO / "tests" / "workspace_browser.mjs"), str(spec)], capture_output=True,
                           timeout=300, cwd=REPO)
        out = json.loads(r.stdout.decode("utf-8") or "{}")
        st = out.get("steps", [])
        diag = json.dumps({"steps": st, "logs": out.get("logs"), "fatal": out.get("fatal")})[:6000]
        assert len(st) == len(steps), diag
        body = lambda i: json.loads(st[i]["body"])
        verbs = body(2)["result"]["verbs"]
        assert "addSite" in verbs and "lessonCheck" in verbs and "saveProject" not in verbs, diag
        a1 = body(3)["result"]
        assert a1["ok"] and a1["result"]["id"] == "S0" and "where" not in a1["result"], diag       # no DOM in answers
        assert a1["studio"]["device"]["nodes"] == 1, diag
        c1 = json.loads(st[4]["value"])
        assert c1["label"].startswith("terminal: addSite") and c1["inside"], diag
        assert body(5)["result"]["studio"]["device"]["nodes"] == 2, diag
        assert body(6)["result"]["result"]["passed"] is True, diag
        assert not body(7)["ok"] and "touches your files" in body(7)["error"] and st[8]["value"] is True, diag
        s9, s10 = body(9)["result"], body(10)["result"]
        assert s9["to"] - s9["from"] >= 500 and s10["to"] < s10["from"], diag
        assert body(11)["result"]["waited_ms"] == 400, diag
        assert json.loads(st[12]["value"])["label"] == "terminal: look at the two sites", diag
    finally:
        try:
            service_request(info, "POST", "/api/shutdown", {}, token="owner")
        except Exception:
            pass


def test_the_site_comments_are_relayed_as_the_person(svc, site):
    """Signing in stores the site's session in the mirror's own cookie (for /api/ only); a
    comment goes to the site with the site's Origin and that session; nothing else travels."""
    ws, state, c, w = svc
    assert w.get("/api/me").json() == {"user": None}
    r = w.post("/api/login", json={"email": "t@x", "password": "pw"}, headers={"Origin": WEB})
    assert r.status_code == 200 and r.json()["user"]["name"] == "Test Person"
    sc = r.headers.get("set-cookie", "")
    assert sc.startswith("qccd_site=tok-1") and "Path=/api/" in sc and "HttpOnly" in sc and "qccd_session" not in sc
    w.cookies.set("qccd_site", "tok-1")
    w.cookies.set("qccd_ws_pairing", "must-not-travel")
    assert w.get("/api/me").json()["user"]["name"] == "Test Person"
    body = {"page": "/rules/", "anchor": {"sel": "#r7", "tag": "h2"}, "text": "hello\n\n\u2014 via Codex"}
    assert w.post("/api/threads", json=body).status_code == 403                                  # no Origin
    assert w.post("/api/threads", json=body, headers={"Origin": "http://evil.example"}).status_code == 403
    r = w.post("/api/threads", json=body, headers={"Origin": WEB})
    assert r.status_code == 200 and r.json()["thread"]["page"] == "/rules/"
    last = site.api_seen[-1]
    assert last["origin"] == site.url and last["host"] == site.url.split("://")[1]
    assert last["cookie"] == "qccd_session=tok-1"                                                # only the session
    assert w.get("/api/threads?page=%2Frules%2F").json()["threads"][0]["comments"][0]["text"].endswith("via Codex")
    assert w.put("/api/threads", json=body, headers={"Origin": WEB}).status_code == 405


def test_a_comment_the_agent_posts_shows_in_the_chat(svc):
    ws, state, c, w = svc
    s = ws.register_session({"kind": "human", "id": "cli"}, client="claude", mode="pull", label="Claude Code (pull only)")
    v = ws.register_view(HUMAN, page={"url": "/web/rules/", "title": "Rules", "site": "https://qccd.academy"})
    ws.update_view(v["id"], {"target_session": s["id"]}, HUMAN)
    r = ws.send_prompt(HUMAN, body={"text": "Review R7 and comment", "intent": "question", "mode": "ask"},
                       view_id=v["id"])
    d = ws.claim_delivery(r["delivery"]["id"])
    ws.mark_delivery(d["id"], "accepted")
    agent = {"kind": "agent", "id": f"agent:{s['id']}", "session": s["id"], "label": "Claude Code (pull only)"}
    ws.note_site_comment(agent, {"page": "/rules/", "thread": 7, "text": "R7 needs a unit.\n\n\u2014 via Claude",
                                 "as": "Test Person", "kind": "comment"})
    item = [i for i in ws.conversation()["items"] if i["type"] == "site_comment"][0]
    assert item["page"] == "/rules/" and item["thread"] == 7 and item["as"] == "Test Person"
    assert item["prompt_id"] == r["prompt_id"]


def _qccdc_built():
    from qccd.workspace.evaluator import Toolchain
    return Toolchain.discover().qccdc is not None


@needs_chrome
@pytest.mark.skipif(not _qccdc_built(), reason="qccdc_cli is not built or installed")
def test_the_trace_keeps_mcp_calls_and_page_actions(svc, capsys):
    ws, state, c, w = svc
    csrf = _paired(state, c)
    s = ws.register_session({"kind": "human", "id": "cli"}, client="generic", mode="pull", label="terminal agent")
    me = {**AGENT, "X-QCCD-Session": s["id"]}
    # the MCP server reports each call it served; only an agent session may
    r = c.post("/api/trace", json={"name": "qccd_get_context", "ms": 12.5,
                                   "data": {"args": {"detail": "summary", "api_token": "sk-123"}, "result": {"revision": 0}}},
               headers=me)
    assert r.status_code == 200, r.text
    assert c.post("/api/trace", json={"name": "x"}, headers={"X-QCCD-CSRF": csrf}).status_code == 403
    assert c.post("/api/trace", json={"name": "x"}, headers=AGENT).status_code == 403           # no session
    assert c.post("/api/trace", json={"kind": "thinking"}, headers=me).status_code == 422
    # a page action is a step too, answered or not
    vid = _page_view(c, csrf)
    assert c.post("/api/page-actions", json={"action": "scroll", "args": {"by": 300}, "wait_s": 1, "view_id": vid},
                  headers=me).status_code == 504
    steps = c.get(f"/api/traces/{s['id']}", headers=OWNER).json()["steps"]
    assert [(x["source"], x["kind"], x["name"]) for x in steps] == [("mcp", "tool_call", "qccd_get_context"),
                                                                    ("page", "page_action", "scroll")]
    assert steps[0]["ms"] == 12.5 and steps[0]["data"]["args"]["api_token"] == "[hidden]"
    assert steps[1]["data"]["args"] == {"by": 300} and steps[1]["data"]["answered"] is False
    assert steps[1]["data"]["page"] == "/web/rules/"
    t = c.get("/api/traces", headers=OWNER).json()["traces"]
    assert t[0]["session"] == s["id"] and t[0]["label"] == "terminal agent" and t[0]["steps"] == 2
    # the viewer: no data in the page itself, so it serves before pairing (it pairs from #pair=)
    page = TestClient(c.app, base_url=BASE).get("/trace")
    assert page.status_code == 200 and "Agent traces" in page.text and "frame-ancestors 'none'" in page.headers[
        "content-security-policy"]
    assert TestClient(c.app, base_url=BASE).get("/api/traces").status_code == 401
    # and in the terminal, from the files alone
    from qccd.workspace.cli import main as cli
    assert cli(["trace", "--root", str(ws.root)]) == 0
    assert s["id"] in capsys.readouterr().out
    assert cli(["trace", "--root", str(ws.root), "--session", s["id"]]) == 0
    text = capsys.readouterr().out
    assert "CALL qccd_get_context" in text and "PAGE scroll" in text and "sk-123" not in text


def test_the_site_guide_is_the_live_sites_map_and_the_studios_own_words(svc, site):
    ws, state, c, w = svc
    idx = [{"t": "Learn", "d": "the course", "u": "learn/", "k": "part"},
           {"t": "Design", "d": "build a device", "u": "studio.html#design", "k": "part"},
           {"t": "R7 · cold ions", "d": "a rule, with a passing and a failing programme", "u": "rules/#R7", "k": "rule"},
           {"t": "Lesson A1 · Two sites", "d": "the course, part A", "u": "studio.html#learn=A1", "k": "lesson"}]
    home = PAGES["index.html"].replace("</body>", "<script>var ROOT = './', ACTIVE = '', INDEX = "
                                       + json.dumps(idx).replace("</", "<\\/") + ";</script></body>")
    (site.root / "index.html").write_text(home, encoding="utf-8")
    from qccd.workspace import site_guide
    site_guide._cache.pop("index", None)
    g = c.get("/api/reference?section=site", headers=AGENT).json()
    assert g["error"] is None
    assert [p["url"] for p in g["main_pages"]] == ["/web/learn/", "/web/studio.html#design", "/web/rules/"]
    assert g["lessons"] == [{"id": "A1", "title": "Lesson A1 · Two sites", "url": "/web/studio.html#learn=A1"}]
    assert any(h["task"] == "walk someone through a lesson" for h in g["how_to"])
    q = c.get("/api/reference?section=site&query=cold", headers=AGENT).json()
    assert [p["url"] for p in q["pages"]] == ["/web/rules/#R7"]
    st = c.get("/api/reference?section=site:studio&query=Evaluate", headers=AGENT).json()
    assert {"hint": "evaluate", "name": "Evaluate", "what": "Price the programme, check it against the rules, and play it.",
            "group": "panels"} in st["controls"]
    every = c.get("/api/reference?section=site:studio", headers=AGENT).json()["controls"]
    assert len(every) > 100 and {h["hint"] for h in every} >= {"tab:W", "learn:check", "play", "p:shuttle", "m:DACs"}
    assert "site" in c.get("/api/reference?section=index", headers=AGENT).json()["sections"]


def test_the_agents_work_happens_in_the_persons_studio(tmp_path, site, monkeypatch):
    """Nothing out of sight: an agent's change set shows its cursor and summary in the Studio;
    a run shows its program while it compiles, then the tab opens the run's page (with the
    chat and the circuit beside the program) where the agent presses Play; and the website's
    Design page is the live Studio itself."""
    from qccd.workspace.evaluator import Toolchain
    from qccd.workspace.runtime import ensure_service, service_request
    if Toolchain.discover().qccdc is None:          # a run compiles for real
        pytest.skip("no compiler: `qccd toolchain install`, or build Compiler/ocaml")
    monkeypatch.setenv("QCCD_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("QCCD_CODEX", "none")
    monkeypatch.setenv("QCCD_CLAUDE", "none")
    monkeypatch.setenv("QCCD_SITE_URL", site.url)
    Workspace.init(tmp_path / "ws", "ghz4@1").close()
    info = ensure_service(tmp_path / "ws", python=sys.executable)
    try:
        own = lambda m, p, b=None: service_request(info, m, p, b, token="owner", timeout=60)
        base, web = f"http://127.0.0.1:{info['port']}", f"http://127.0.0.1:{info['web_port']}"
        agent = {"Authorization": f"Bearer {info['agent_token']}", "Content-Type": "application/json"}

        def http(path, body):
            return {"http": {"method": "POST", "url": base + path, "headers": agent, "body": body}}
        cur = ("(function(){ var c = document.getElementById('qccd-agent-cursor'); "
               "return c ? c.querySelector('span').textContent : null; })()")
        steps = [
            {"wait": "window.QCCD_LIVE && QCCD_LIVE.state().paired && QCCD_LIVE.state().connected && "
                     "QCCD_LIVE.state().rev === 0 && !!QCCD_LIVE.state().view", "timeout": 40000, "stopOnFail": True},  # 0
            {"sleep": 1500},
            http("/api/change-sets", {"expected_revision": 0, "request_id": "a1", "mode": "apply",
                                      "summary": "a spare trap beside C3",
                                      "operations": [{"type": "add_site", "id": "T9", "pos": [4, 0], "zone": "trap"},
                                                     {"type": "add_segment", "a": "C3", "b": "T9"}]}),  # 2
            {"wait": "QCCD_LIVE.state().rev === 1 && !!document.getElementById('qccd-agent-cursor')", "timeout": 20000},  # 3
            {"eval": cur},                                                                                  # 4
            http("/api/jobs", {"kind": "run", "params": {"program": "ghz4"}, "request_id": "run1"}),        # 5
            {"wait": "!!document.getElementById('qcl-runpanel')", "timeout": 30000},                        # 6
            {"eval": "document.querySelector('#qcl-runpanel .qcl-rp-q').textContent.indexOf('OPENQASM') >= 0 && "
                     "document.querySelector('#qcl-runpanel .qcl-rp-t').textContent"},                     # 7
            {"wait": "location.pathname.indexOf('/runview/') === 0 && window.QCCD_LIVE && QCCD_LIVE.state().connected "
                     "&& !!document.getElementById('qcl-dock')", "timeout": 120000, "stopOnFail": True},  # 8
            {"eval": "typeof SRC !== 'undefined' && !!SRC"},                                               # 9 circuit beside
            {"sleep": 2500},
            http("/api/page-actions", {"action": "read", "args": {"max_chars": 200, "controls": False}}),  # 11
            http("/api/page-actions", {"action": "step", "args": {"play": True}}),                          # 12
            {"sleep": 800},
            {"eval": "document.getElementById('play').textContent"},                                        # 14
            # the website's Design page is the live Studio
            {"eval": f"location.assign('{web}/web/studio.html#design'); 1"},                                # 15
            {"wait": f"location.href === '{base}/studio' && window.QCCD_LIVE && QCCD_LIVE.state().paired",
             "timeout": 60000},                                                                             # 16
            {"eval": f"location.assign('{web}/web/studio.html#learn=A1'); 1"},                              # 17
            {"sleep": 2500},
            {"eval": "location.origin + location.pathname + location.hash"},                                # 19 lessons stay
        ]
        spec = tmp_path / "spec.json"
        spec.write_text(json.dumps({"pages": {"a": f"{base}/studio#pair={own('POST', '/api/pair-code', {})['code']}"},
                                    "steps": steps}), encoding="utf-8")
        r = subprocess.run(["node", str(REPO / "tests" / "workspace_browser.mjs"), str(spec)], capture_output=True,
                           timeout=400, cwd=REPO)
        out = json.loads(r.stdout.decode("utf-8") or "{}")
        st = out.get("steps", [])
        diag = json.dumps({"steps": st, "logs": out.get("logs"), "fatal": out.get("fatal")})[:6000]
        assert len(st) == len(steps), diag
        assert st[3]["ok"] and st[4]["value"].startswith("Agent: r1 a spare trap beside C3"), diag
        assert st[6]["ok"] and st[7]["value"] and "ghz4" in st[7]["value"], diag
        assert st[8]["ok"] and st[9]["value"] is True, diag
        rd = json.loads(st[11]["body"])
        assert rd["ok"] and rd["page"]["url"].startswith("/runview/"), diag
        assert json.loads(st[12]["body"])["ok"] and st[14]["value"].lower().startswith("pause"), diag
        assert st[16]["ok"], diag
        assert st[19]["value"] == f"{web}/web/studio.html#learn=A1", diag
    finally:
        try:
            service_request(info, "POST", "/api/shutdown", {}, token="owner")
        except Exception:
            pass
