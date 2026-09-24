"""LIVE, opt-in: is every control on qccd.academy declared for agents?

    QCCD_LIVE_WEB=1 python tests/workspace_live_interface.py

The website half of the gate in tests/test_agent_interface.py (docs/agent-interface.md).  A
fresh workspace serves the real site through its mirror; real Chrome visits every PLACE in the
site's own index (plus lessons of the site's Studio) and asks the page tools which controls
are undeclared -- the ones an agent may read but not operate.  Anything beyond KNOWN_DEBT
(controls whose owners have not declared them yet, listed in docs/agent-interface.md) fails:
a site change that added UI without its declaration.  It sends the site nothing but GETs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

#: undeclared controls known on 2026-09-24, by the element they are in (their owners declare them
#: inline; remove an entry when they do).  page prefix -> ids of the controls or of their containers
KNOWN_DEBT = {
    # the gadget viewer (qccd/gadget/web): algorithms, demo, showcase
    "/web/gadgets/": {"app", "stageTools"},
    # the QEC-cycle panel (qccd/site/qec_cycle.js)
    "/web/studio.html": {"qcShowOn"},
    # leaderboards: the chart's controls (the board index), and on entry pages the Study-notes overlay,
    # its label box, the QEC-cycle panel and two pane close buttons of the compiled pages
    "/web/board/": {"ysel", "xsel", "log", "vchart", "vtable", "qcShowOn", "bblab", "pHw", "paneQ", "bbreopen",
                    "bbhead", "bbnotes", "bbpill"},
}
EXTRA = ["studio.html#learn=A1", "studio.html#learn=D1"]


def known(url: str, c: dict) -> bool:
    return any(url.startswith(p) and (c.get("id") in ids or c.get("in") in ids) for p, ids in KNOWN_DEBT.items())


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if os.environ.get("QCCD_LIVE_WEB") != "1":
        print("set QCCD_LIVE_WEB=1 to run this live check (it reads the real qccd.academy)")
        return 2
    from qccd.workspace.app import Workspace
    from qccd.workspace.interface import places
    from qccd.workspace.mirror import SiteMirror
    from qccd.workspace.runtime import ensure_service, service_request
    from qccd.workspace.site_guide import _index
    tmp = Path(tempfile.mkdtemp(prefix="qccd-live-iface-"))
    os.environ.setdefault("QCCD_RUNTIME_DIR", str(tmp / "runtime"))
    os.environ.update(QCCD_CODEX="none", QCCD_CLAUDE="none", QCCD_WEB_PORT=os.environ.get("QCCD_WEB_PORT", "47199"))
    Workspace.init(tmp / "ws", "ghz4@1").close()
    info = ensure_service(tmp / "ws", python=sys.executable)
    try:
        own = lambda m, p, b=None: service_request(info, m, p, b, token="owner", timeout=60)
        base, web = f"http://127.0.0.1:{info['port']}", f"http://127.0.0.1:{info['web_port']}"
        paths = [p[len("/web/"):] for p in places(_index(SiteMirror()))] + EXTRA
        probe = ("JSON.stringify({want: %s, url: location.pathname + location.hash, error_page: "
                 "location.protocol === 'chrome-error:', contract: !!window.QCCD_INTERFACE, "
                 "undeclared: window.QCCD_PAGE ? QCCD_PAGE.undeclared() : null})")
        steps = [{"wait": "!!document.getElementById('qccd-chat')", "timeout": 40000}]
        for p in paths:
            go = f"location.assign('{web}/web/{p}'); 1"
            ready = "document.readyState === 'complete' && !!window.QCCD_PAGE"
            steps += [{"eval": go}, {"sleep": 400}, {"wait": ready, "timeout": 30000},
                      # a load that failed (a network error page) is tried once more before it counts
                      {"eval": f"(location.protocol === 'chrome-error:' || !window.QCCD_PAGE) ? ({go}) : 0"},
                      {"sleep": 400}, {"wait": ready, "timeout": 30000},
                      {"sleep": 600}, {"eval": probe % json.dumps(p)}]
        probes = {probe % json.dumps(p) for p in paths}
        spec = tmp / "spec.json"
        code = own("POST", "/api/pair-code", {})["code"]
        spec.write_text(json.dumps({"pages": {"a": f"{base}/open-web#pair={code}&to=/web/"}, "steps": steps}),
                        encoding="utf-8")
        r = subprocess.run(["node", str(REPO / "tests" / "workspace_browser.mjs"), str(spec)], capture_output=True,
                           timeout=3600, cwd=REPO)
        out = json.loads(r.stdout.decode("utf-8") or "{}")
        pages, new, debt, broken = 0, [], [], []
        for s in out.get("steps", []):
            if "eval" in s["step"] and s["step"]["eval"] in probes:
                if not s.get("value"):
                    broken.append(s.get("error"))
                    continue
                d = json.loads(s["value"])
                pages += 1
                if not d["contract"] or d["undeclared"] is None:
                    broken.append({"page": d["want"], "error_page": d["error_page"], "at": d["url"]})
                    continue
                for c in d["undeclared"]:
                    (debt if known(d["url"], c) else new).append({"page": d["url"], **c})
        print(json.dumps({"pages": pages, "places": len(paths), "new_undeclared": new, "known_debt": len(debt),
                          "no_contract": broken, "fatal": out.get("fatal")}, indent=1, ensure_ascii=False))
        return 0 if pages == len(paths) and not new and not broken else 1
    finally:
        try:
            service_request(info, "POST", "/api/shutdown", {}, token="owner")
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
