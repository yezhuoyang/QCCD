"""LIVE, opt-in: the agent comments on a real qccd.academy page as the signed-in person --
against a PRIVATE copy of the comment service, so nothing is posted on the real site.

    QCCD_LIVE_WEB=1 python tests/workspace_live_comments.py <comments_api.py> [out_dir]

A local "site" serves the live qccd.academy pages and sends /api/ to a local copy of the
site's own comment service (`qccd/site/comments_api.py`, from the website branch) with a
throwaway database and one test account.  The workspace's website mirror points at it
(QCCD_SITE_URL).  Real Chrome signs in through the page's own Sign in (the person's act),
then the agent's page actions -- the same call the MCP tools make -- list the comments,
comment on rule R7, reply, and mark it addressed; the page shows the pin.  It prints one
JSON report and leaves screenshots in out_dir.  Not part of the pytest suite.
"""

from __future__ import annotations

import http.server
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
LIVE = "https://qccd.academy"
EMAIL, PASSWORD, NAME = "tester@example.org", "a-test-password", "Test Person"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if os.environ.get("QCCD_LIVE_WEB") != "1" or len(sys.argv) < 2:
        print("QCCD_LIVE_WEB=1 python tests/workspace_live_comments.py <comments_api.py> [out_dir]")
        return 2
    api_py = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(tempfile.mkdtemp(prefix="qccd-live-comments-"))
    out.mkdir(parents=True, exist_ok=True)
    db = out / "comments.db"
    subprocess.run([sys.executable, str(api_py), "passwd", "--db", str(db), EMAIL, "--name", NAME,
                    "--password", PASSWORD], check=True, capture_output=True)
    api_port, site_port = free_port(), free_port()
    api = subprocess.Popen([sys.executable, str(api_py), "serve", "--db", str(db), "--port", str(api_port),
                            "--admins", "admin@example.org"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    class Site(http.server.BaseHTTPRequestHandler):
        """The live pages, with /api/ sent to the private comment service (Host kept, as nginx does)."""
        def _go(self, method):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else None
            if self.path.startswith("/api/"):
                url, hdrs = f"http://127.0.0.1:{api_port}{self.path}", {k: v for k, v in self.headers.items()
                                                                       if k.lower() in ("cookie", "origin", "content-type", "host")}
            else:
                url, hdrs = LIVE + self.path, {"User-Agent": "qccd-live-comments-test"}
            req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    status, headers, data = r.status, r.headers, r.read()
            except urllib.error.HTTPError as e:
                status, headers, data = e.code, e.headers, e.read()
            self.send_response(status)
            for k, v in headers.items():
                if k.lower() in ("content-type", "set-cookie", "etag", "last-modified", "location"):
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._go("GET")

        def do_POST(self):
            self._go("POST")

        def do_DELETE(self):
            self._go("DELETE")

        def log_message(self, *a):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", site_port), Site)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    os.environ["QCCD_RUNTIME_DIR"] = str(out / "runtime")
    os.environ["QCCD_CODEX"] = "none"
    os.environ["QCCD_CLAUDE"] = "none"
    os.environ["QCCD_SITE_URL"] = f"http://127.0.0.1:{site_port}"
    from qccd.workspace.app import Workspace
    from qccd.workspace.runtime import ensure_service, service_request
    Workspace.init(out / "ws", "ghz4@1").close()
    info = ensure_service(out / "ws", python=sys.executable)
    own = lambda m, p, b=None: service_request(info, m, p, b, token="owner", timeout=90)
    base, web = f"http://127.0.0.1:{info['port']}", f"http://127.0.0.1:{info['web_port']}"
    hdr = {"Authorization": f"Bearer {info['owner_token']}", "Content-Type": "application/json"}

    def act(action, **args):
        return {"http": {"method": "POST", "url": base + "/api/page-actions", "headers": hdr,
                         "body": {"action": action, "args": args, "wait_s": 45}}}
    state = "JSON.stringify(window.QCCOMMENTS && QCCOMMENTS.state())"
    steps = [
        {"wait": f"location.origin === '{web}' && !!document.getElementById('qccd-chat')", "timeout": 90000,
         "stopOnFail": True},                                                                                 # 0
        {"frame": "/chatframe", "wait": "window.QCCD_LIVE && QCCD_LIVE.state().connected && !!QCCD_LIVE.state().view "
         "&& !!(QCCD_LIVE.page() || {}).title", "timeout": 90000, "stopOnFail": True},                         # 1
        {"wait": "!!document.getElementById('qc-signin')", "timeout": 30000},                                 # 2
        {"eval": "fetch('/api/me', {credentials: 'same-origin'}).then(function(r){ return r.text().then(function(t){ "
                 "return r.status + ' ' + t.slice(0, 120); }); }, function(e){ return 'ERR ' + e; })"},       # 2b
        act("comments"),                                                                                      # 3 not signed in
        act("click", target={"selector": "#qc-signin"}),                                                      # 4 refused
        # the PERSON signs in, through the page's own form
        {"eval": "document.getElementById('qc-signin').click(); 1"},                                          # 5
        {"wait": "!!document.getElementById('qc-email')", "timeout": 10000},                                  # 6
        {"eval": f"(function(){{ var s = function(id, v){{ var e = document.getElementById(id); e.value = v; "
                 f"e.dispatchEvent(new Event('input', {{bubbles: true}})); }}; s('qc-email', {json.dumps(EMAIL)}); "
                 f"s('qc-pass', {json.dumps(PASSWORD)}); document.getElementById('qc-submit').click(); return 1; }})()"},  # 7
        {"wait": "window.QCCOMMENTS && QCCOMMENTS.state().me && QCCOMMENTS.state().me.name === " + json.dumps(NAME),
         "timeout": 20000},                                                                                   # 8
        act("comments"),                                                                                      # 9 signed in, none yet
        act("comment", target={"selector": "#R7"},
            text="R7 is stated for 2Q gates only; say whether single-qubit gates have a heating limit too."),  # 10
        {"wait": "QCCOMMENTS.state().threads.length === 1 && QCCOMMENTS.state().threads[0].shown", "timeout": 20000},  # 11
        {"sleep": 1200},
        {"shot": str(out / "commented.png")},                                                                 # 13
        act("comments"),                                                                                      # 14
        {"eval": "location.reload(); 1"},                                                                     # 15
        {"wait": "window.QCCOMMENTS && QCCOMMENTS.state().me && QCCOMMENTS.state().threads.length === 1 && "
                 "QCCOMMENTS.state().threads[0].shown", "timeout": 60000},                                     # 16 anchor holds
        {"frame": "/chatframe", "wait": "window.QCCD_LIVE && QCCD_LIVE.state().connected && !!QCCD_LIVE.state().view",
         "timeout": 60000},                                                                                   # 17
        {"sleep": 1500},
        act("reply", thread=1, text="Checked the reference: it only covers ms_gate."),                        # 19
        act("resolve", thread=1),                                                                             # 20
        {"eval": state},                                                                                      # 21
    ]
    code = own("POST", "/api/pair-code", {})["code"]
    spec = out / "spec.json"
    spec.write_text(json.dumps({"pages": {"a": f"{base}/open-web#pair={code}&to=/web/rules/"}, "steps": steps}),
                    encoding="utf-8")
    t0 = time.time()
    r = subprocess.run(["node", str(REPO / "tests" / "workspace_browser.mjs"), str(spec)], capture_output=True,
                       timeout=1200, cwd=REPO)
    drive = json.loads(r.stdout.decode("utf-8") or "{}")
    report = {"seconds": round(time.time() - t0), "fatal": drive.get("fatal"), "console": drive.get("logs"),
              "failed_requests": drive.get("net"),
              "steps": []}
    for i, rec in enumerate(drive.get("steps", [])):
        row = {"i": i, "ok": rec.get("ok"), "error": rec.get("error")}
        if rec.get("value") is not None:
            row["value"] = rec["value"][:600] if isinstance(rec["value"], str) else rec["value"]
        if rec.get("body"):
            try:
                row["answer"] = json.loads(rec["body"])
            except ValueError:
                row["answer"] = rec["body"][:300]
        report["steps"].append(row)
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        report["db"] = {"threads": [dict(x) for x in c.execute("SELECT id, page, anchor, resolved FROM threads")],
                        "comments": [dict(x) for x in c.execute("SELECT id, thread_id, text FROM comments")]}
    try:
        own("POST", "/api/shutdown", {})
    except Exception:
        pass
    api.kill()
    httpd.shutdown()
    report["out"] = str(out)
    (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
