"""LIVE, opt-in: the real qccd.academy through a fresh workspace's website mirror.

    QCCD_LIVE_WEB=1 python tests/workspace_live_web.py [out_dir]

A fresh workspace starts its service; real Chrome is paired through /open-web and tours
the site's kinds of page (the home page with its embedded clip, the Studio with its course,
Rules with its embedded examples, a leaderboard, Learn), while the page actions an agent
uses (the same HTTP call the MCP tools make) read and operate each page.  It prints one JSON
report -- every action's answer, and every console, exception and CSP message -- and leaves
screenshots in out_dir.  It needs the internet; it sends nothing to the site but GETs.
Not part of the pytest suite.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if os.environ.get("QCCD_LIVE_WEB") != "1":
        print("set QCCD_LIVE_WEB=1 to run this live test (it fetches pages from qccd.academy)")
        return 2
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="qccd-live-web-"))
    out.mkdir(parents=True, exist_ok=True)
    os.environ["QCCD_RUNTIME_DIR"] = str(out / "runtime")
    os.environ["QCCD_CODEX"] = "none"
    os.environ["QCCD_CLAUDE"] = "none"
    from qccd.workspace.app import Workspace
    from qccd.workspace.runtime import ensure_service, service_request

    Workspace.init(out / "ws", "ghz4@1").close()
    info = ensure_service(out / "ws", python=sys.executable)
    own = lambda m, p, b=None: service_request(info, m, p, b, token="owner", timeout=120)
    base, web = f"http://127.0.0.1:{info['port']}", f"http://127.0.0.1:{info['web_port']}"
    hdr = {"Authorization": f"Bearer {info['owner_token']}", "Content-Type": "application/json"}

    def act(action, **args):
        return {"http": {"method": "POST", "url": base + "/api/page-actions", "headers": hdr,
                         "body": {"action": action, "args": args, "wait_s": 30}}}

    def arrive(path):
        return [{"eval": f"location.assign('{web}/web/{path}'); 1"},
                {"wait": f"location.pathname === '/web/{path.split('#')[0]}' && document.readyState === 'complete'",
                 "timeout": 90000},
                {"frame": "/chatframe", "wait": "window.QCCD_LIVE && QCCD_LIVE.state().connected && "
                 f"((QCCD_LIVE.page() || {{}}).url || '').indexOf({json.dumps('/web/' + path.split('#')[0])}) === 0 "
                 "&& !!QCCD_LIVE.page().title", "timeout": 60000},
                {"sleep": 1500}]

    code = own("POST", "/api/pair-code", {})["code"]
    steps = [
        {"wait": f"location.origin === '{web}' && !!document.getElementById('qccd-chat')", "timeout": 90000,
         "stopOnFail": True},
        {"frame": "/chatframe", "wait": "window.QCCD_LIVE && QCCD_LIVE.state().paired && QCCD_LIVE.state().connected "
         "&& !!QCCD_LIVE.state().view && !!(QCCD_LIVE.page() || {}).title", "timeout": 60000, "stopOnFail": True},
        {"sleep": 4000},
        {"label": "home.read", **act("read", max_chars=1500)},
        {"eval": "(function(){var f=document.querySelector('.clip iframe'); if(!f) return 'no clip'; var d=f.contentDocument;"
                 " return d && d.getElementById('slider') ? 'clip step ' + d.getElementById('slider').value : 'clip not loaded';})()"},
        {"shot": str(out / "home.png")},
        *arrive("studio.html#learn=R7"),
        {"sleep": 3000},
        {"label": "studio.read", **act("read", max_chars=800)},
        {"label": "studio.open_lesson", **act("open_lesson", lesson="R8")},
        {"label": "studio.step", **act("step", delta=1)},
        {"label": "studio.highlight", **act("highlight", target={"selector": "#play"}, note="Play runs the program")},
        {"shot": str(out / "studio.png")},
        *arrive("rules/"),
        {"label": "rules.read", **act("read", max_chars=600)},
        {"label": "rules.section", **act("read", section="R7", max_chars=1200, controls=False)},
        {"sleep": 6000},
        {"label": "rules.step_example", **act("step", target={"frame": "f1"}, delta=1)},
        {"label": "rules.highlight_in_example", **act("highlight", target={"frame": "f1", "selector": "#slider"},
                                                     note="the example's own transport")},
        {"shot": str(out / "rules.png")},
        *arrive("board/"),
        {"label": "board.read", **act("read", max_chars=1500)},
        {"wait": "!!document.querySelector('#qo-boards .qo-task, #qo-boards .qo-err, #qo-boards .qo-empty')", "timeout": 30000},
        {"eval": "(document.getElementById('qo-boards') || {}).textContent"},
        {"shot": str(out / "board.png")},
        *arrive("learn/"),
        {"label": "learn.read", **act("read", max_chars=800)},
        {"label": "learn.click_lesson", **act("click", target={"selector": "a[href*='learn=R2']"})},
        {"wait": "location.hash === '#learn=R2'", "timeout": 60000},
        {"shot": str(out / "learn_click.png")},
    ]
    spec = out / "spec.json"
    spec.write_text(json.dumps({"pages": {"a": f"{base}/open-web#pair={code}&to=/web/"},
                                "steps": [{k: v for k, v in s.items() if k != "label"} for s in steps]}),
                    encoding="utf-8")
    t0 = time.time()
    r = subprocess.run(["node", str(REPO / "tests" / "workspace_browser.mjs"), str(spec)], capture_output=True,
                       timeout=1800, cwd=REPO)
    drive = json.loads(r.stdout.decode("utf-8") or "{}")
    report = {"seconds": round(time.time() - t0), "fatal": drive.get("fatal"), "logs": drive.get("logs"),
              "failed_requests": drive.get("net"), "steps": []}
    for s, rec in zip(steps, drive.get("steps", [])):
        row = {"label": s.get("label") or next(iter(k for k in s if k in ("eval", "wait", "shot", "sleep", "frame"))),
               "ok": rec.get("ok"), "error": rec.get("error"), "value": rec.get("value")}
        if rec.get("body"):
            try:
                b = json.loads(rec["body"])
                res = b.get("result") if isinstance(b.get("result"), dict) else {}
                row["answer"] = {"ok": b.get("ok"), "error": b.get("error") or (b.get("error") if not b.get("ok") else None),
                                 "title": res.get("title"), "kind": res.get("kind"),
                                 "headings": [h["text"] for h in (res.get("headings") or [])][:6],
                                 "text": (res.get("text") or "")[:300], "controls": len(res.get("controls") or []),
                                 "embedded": len(res.get("embedded") or []), "app": res.get("app") and {
                                     k: (v if k != "lessons" else len(v)) for k, v in res["app"].items()},
                                 "other": {k: v for k, v in res.items() if k in ("ok", "element", "transport", "lesson",
                                                                                   "navigating", "section")}}
            except ValueError:
                row["answer"] = rec["body"][:300]
        report["steps"].append(row)
    try:
        own("POST", "/api/shutdown", {})
    except Exception:
        pass
    report["out"] = str(out)
    (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
