"""`qccd` workspace commands.

    qccd init my-designs                     a workspace for any number of designs and every board
    qccd studio [--no-open] [--keep-alive]   start (or reuse) the service; open Studio paired;
                                             --keep-alive stays and restarts a killed service
    qccd serve                               run the service in the foreground
    qccd status | stop                       the service, sessions, head revision
    qccd agent install|uninstall|status --client codex|claude [--scope project]
    qccd agent connect --client codex [--thread ID | --new] [--url ws://127.0.0.1:PORT]
    qccd mcp --client codex|claude|generic [--channel]     (launched by the agent client)
    qccd validate [--json]                   draft grade of the head revision
    qccd compile --board TITLE [--adopt]     the real compiler on a board's circuit
    qccd submit --local --board "BB [[144,12,12]]" [--design NAME] [--wait]
                                             compile for the board, adopt, freeze, grade
    qccd boards                              the leaderboards a design can be submitted to
    qccd publish --submission SUB [--visibility public]   review + approve at this terminal
    qccd publish --approval AP --server URL  upload an approved bundle
    qccd import design/studio.json           a file written outside Studio, as a change set
    qccd trace [--session S] [--prompt P] [--full | --json | --open]
                                             what an agent did for a request, step by step

Entry points: `python -m qccd.workspace <command>`, or the `qccd` console script from
`pyproject.toml`, which falls through to the existing `python -m qccd` commands for
everything not listed here (so `qccd studio` without `--live` semantics is unchanged
when run outside a workspace).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import webbrowser
from pathlib import Path

__all__ = ["main", "COMMANDS"]

COMMANDS = ("init", "studio", "web", "toolchain", "serve", "status", "stop", "agent", "mcp", "validate", "compile", "submit",
            "publish", "import", "releases", "boards", "leaderboard", "trace")


def _root(args) -> Path:
    from .runtime import find_workspace_root
    return find_workspace_root(Path(getattr(args, "root", None) or os.getcwd()))


def _svc(args):
    from .runtime import ensure_service
    return ensure_service(_root(args))


def _call(info, method, path, body=None, token="owner", timeout=120.0):
    from .runtime import ServiceError, service_request
    try:
        return service_request(info, method, path, body, token=token, timeout=timeout)
    except ServiceError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        if exc.detail.get("detail"):
            print(json.dumps(exc.detail["detail"], indent=1)[:4000], file=sys.stderr)
        raise SystemExit(2)


def _wait_job(info, job_id: str, timeout: float = 3600.0) -> dict:
    t0 = time.time()
    last = None
    while True:
        j = _call(info, "GET", f"/api/jobs/{job_id}")
        msg = (j.get("progress") or {}).get("message")
        if msg and msg != last:
            print(f"  {j['status']}: {msg}", file=sys.stderr)
            last = msg
        if j["status"] in ("succeeded", "failed", "cancelled", "timeout", "internal_error"):
            return j
        if time.time() - t0 > timeout:
            raise SystemExit(f"timed out waiting for {job_id}")
        time.sleep(1.0)


# ---------------------------------------------------------------------- commands

def cmd_init(a) -> int:
    from .app import Workspace
    ws = Workspace.init(Path(a.dir), a.task, name=a.name, starter=not a.empty)
    h = ws.head()
    print(f"workspace in {ws.root}")
    print(f"  design    {ws.design_title(ws.branch('main'))} (r{h.revision}): {ws.replayed().arch.name}, "
          f"{len(ws.replayed().arch.device.nodes)} nodes")
    print(f"  boards    any design can be submitted to: " + "; ".join(b["title"] for b in ws.boards()))
    print("  next      cd into it; `qccd agent install --client codex|claude`; `qccd studio`")
    ws.close()
    return 0


def cmd_serve(a) -> int:
    from .service import serve
    return serve(_root(a), port=a.port)


def cmd_studio(a) -> int:
    from .runtime import ensure_service
    info = ensure_service(_root(a), restart_stale=True)
    if info.get("restarted_stale"):
        print("the running service was started from older code (QCCD was updated since); restarted it")
    code = _call(info, "POST", "/api/pair-code", {})["code"]
    url = f"http://127.0.0.1:{info['port']}/studio#pair={code}"
    print(f"Studio: {url}")
    print("  (the pairing code works once, for five minutes; run `qccd studio` again for another tab)")
    if info.get("web_port"):
        print(f"Website with your agent: http://127.0.0.1:{info['web_port']}/web/  (every qccd.academy page, "
              "with this chat; `qccd web` opens it)")
    if not a.no_open:
        webbrowser.open(url)
    if not a.keep_alive:
        return 0
    import threading
    from .runtime import keep_alive

    def said(kind, d):
        if kind == "restarted":
            print(f"the service (pid {d['old_pid']}) was killed; restarted as pid {d['pid']} on port {d['port']}"
                  + ("" if d["same_port"] else " -- a NEW port: reopen Studio with `qccd studio`"), flush=True)
        elif kind == "stopped":
            print("the service was stopped; not restarting it", flush=True)
    print("keeping the service alive (Ctrl+C leaves it running and exits)", flush=True)
    try:
        keep_alive(_root(a), threading.Event(), on_event=said)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_web(a) -> int:
    """The website through the workspace: every qccd.academy page, with the chat and the
    page tools.  Pairs this browser (a one-time code, as `qccd studio` does) and opens it."""
    from .runtime import ensure_service
    info = ensure_service(_root(a), restart_stale=True)
    if info.get("restarted_stale"):
        print("the running service was started from older code (QCCD was updated since); restarted it")
    if not info.get("web_port"):
        print("this service runs without the website (QCCD_WEB=0 when it started); `qccd stop` and try again",
              file=sys.stderr)
        return 1
    path = (a.path or "").lstrip("/")
    if path.startswith("web/"):
        path = path[4:]
    code = _call(info, "POST", "/api/pair-code", {})["code"]
    url = f"http://127.0.0.1:{info['port']}/open-web#pair={code}&to=/web/{path}"
    print(f"Website with your agent: http://127.0.0.1:{info['web_port']}/web/{path}")
    print(f"  (this link pairs the browser first: {url})")
    if not a.no_open:
        webbrowser.open(url)
    return 0


def cmd_trace(a) -> int:
    """The agent traces of this workspace (trace.py), read from .qccd/traces without the service.
    No session: the list.  A session: its latest request (or --prompt), as a transcript."""
    from .trace import Traces, render_text
    root = _root(a)
    tr = Traces(root)
    if a.open:
        from .runtime import ensure_service
        info = ensure_service(root, restart_stale=True)
        code = _call(info, "POST", "/api/pair-code", {})["code"]
        q = "&".join(f"{k}={v}" for k, v in (("session", a.session), ("prompt", a.prompt)) if v)
        page = f"http://127.0.0.1:{info['port']}/trace" + (f"?{q}" if q else "")
        print(f"Traces: {page}")
        webbrowser.open(f"{page}#pair={code}")
        return 0
    idx = tr.index()
    if not a.session:
        if a.json:
            print(json.dumps(idx, indent=1))
            return 0
        if not idx:
            print("no traces yet: a request sent to an agent from Studio or a website page is traced")
            return 0
        for t in idx:
            print(f"{t['session']}  {len(t['prompts'])} request(s), {t['steps']} steps, last "
                  f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(t['last']))}")
            for p in t["prompts"][-8:]:
                print(f"    {p['prompt'] or '(outside a request)':<22} {p['steps']:>4} steps {p['tools']:>3} calls  "
                      f"{(p['text'] or '')[:70]}")
        print("\nqccd trace --session <id> [--prompt <id>]   for one request, step by step")
        return 0
    one = next((t for t in idx if t["session"] == a.session), None)
    if one is None:
        print(f"qccd trace: no trace for session {a.session!r}", file=sys.stderr)
        return 1
    pid = a.prompt or (one["prompts"][-1]["prompt"] if one["prompts"] else None)
    steps = [s for s in tr.steps(a.session) if (s.get("prompt") or None) == (pid or None)]
    if a.json:
        print(json.dumps(steps, indent=1, ensure_ascii=False))
    else:
        print(f"session {a.session}, request {pid or '(outside a request)'}")
        print(render_text(steps, full=a.full))
    return 0


def cmd_toolchain(a) -> int:
    """The prebuilt compiler (toolchain.py): `install` fetches the one for this computer,
    checked against the SHA-256 pinned in the repository; `status` says what is in use."""
    from .toolchain import ToolchainError, install, status
    if a.action == "status":
        s = status()
        if a.json:
            print(json.dumps(s, indent=1))
            return 0
        print(f"platform  {s['platform']}  (prebuilt compiler {'available' if s['prebuilt_available'] else 'NOT available'})")
        print(f"version   {s['version']}  (Compiler/ocaml tree {s['source']['tree'][:12]} at {s['source']['commit'][:10]})")
        i = s["installed"]
        print("installed " + (f"{i['path']}" + ("" if i["matches_manifest"] else "  -- DOES NOT MATCH toolchain.json")
                              if i else "no"))
        u = s["in_use"]
        print("in use    " + (f"{u['path']}  ({u['from']})" if u else "none: run `qccd toolchain install`"))
        return 0
    try:
        r = install(force=a.force)
    except ToolchainError as exc:
        print(f"qccd toolchain install: {exc}", file=sys.stderr)
        return 1
    if r["status"] == "already_installed":
        print(f"the compiler is already installed: {r['path']}")
    else:
        print(f"installed the compiler (checked: sha256 {r['sha256'][:16]}..., and it parsed a test circuit): {r['path']}")
    return 0


def cmd_status(a) -> int:
    from .runtime import read_runtime
    from .tasks import read_lock
    root = _root(a)
    lock = read_lock(root)
    info = read_runtime(lock["workspace_id"])
    out = {"workspace": lock["workspace_id"], "root": str(root), "service": None}
    if info:
        ctx = _call(info, "GET", "/api/context")
        sessions = _call(info, "GET", "/api/sessions")["sessions"]
        from .runtime import code_identity
        out["service"] = {"port": info["port"], "pid": info["pid"],
                          "stale": info.get("code") != code_identity()}
        out["revision"] = ctx["revision"]
        out["mode"] = ctx["mode"]
        out["sessions"] = [{k: s[k] for k in ("id", "client", "mode", "status", "label", "write_fence")} for s in sessions]
        out["views"] = [{k: v[k] for k in ("id", "connected", "follow_agent", "target_session")}
                        for v in _call(info, "GET", "/api/views")["views"]]
    print(json.dumps(out, indent=1) if a.json else _fmt_status(out))
    return 0


def _fmt_status(o) -> str:
    lines = [f"workspace {o['root']}"]
    if not o["service"]:
        lines.append("service   not running (`qccd studio` starts it)")
        return "\n".join(lines)
    phys = "the boards' physics" if o["mode"] == "task" else "physics changed: not eligible on a board"
    lines.append(f"service   127.0.0.1:{o['service']['port']} pid {o['service']['pid']}  design r{o['revision']} ({phys})")
    if o["service"].get("stale"):
        lines.append("          started from older code (QCCD was updated since): `qccd studio` restarts it")
    for s in o["sessions"]:
        lines.append(f"agent     {s['id']}  {s['client']}/{s['mode']}  {s['status']}"
                     + ("  STOPPED" if s["write_fence"] else "") + f"  {s['label'] or ''}")
    for v in o["views"]:
        lines.append(f"studio    {v['id']}  {'open' if v['connected'] else 'not connected'}  -> {v['target_session']}")
    return "\n".join(lines)


def cmd_stop(a) -> int:
    from .runtime import read_runtime
    from .tasks import read_lock
    info = read_runtime(read_lock(_root(a))["workspace_id"])
    if not info:
        print("no service is running")
        return 0
    _call(info, "POST", "/api/shutdown", {})
    print("stopping")
    return 0


def cmd_validate(a) -> int:
    info = _svc(a)
    ctx = _call(info, "GET", "/api/context")
    out: dict = {"revision": ctx["revision"], "mode": ctx["mode"], "diagnostics": ctx["diagnostics"],
                 "final_program": ctx["design"]["final_program"]}
    if ctx["design"]["final_program"]:
        j = _call(info, "POST", "/api/jobs", {"kind": "evaluate", "params": {"profile": "draft", "board": a.board}})
        j = _wait_job(info, j["job_id"])
        out["draft"] = (j.get("result") or {})
        out["draft"].pop("traceback", None)
    else:
        out["draft"] = "no final program yet: `qccd compile --adopt` first"
    print(json.dumps(out, indent=1) if a.json else json.dumps(out, indent=1)[:6000])
    return 0


def cmd_compile(a) -> int:
    info = _svc(a)
    j = _call(info, "POST", "/api/jobs", {"kind": "compile", "params": {"compiler": a.compiler, "board": a.board}})
    j = _wait_job(info, j["job_id"])
    res = j.get("result") or {}
    print(f"{j['status']}: {res.get('summary')}")
    if j["status"] != "succeeded":
        print((res.get("log") or "")[-2000:], file=sys.stderr)
        return 1
    if a.adopt:
        ctx = _call(info, "GET", "/api/context")
        r = _call(info, "POST", "/api/change-sets", {"expected_revision": ctx["revision"], "request_id": f"adopt-{j['id']}",
                                                     "mode": "apply", "summary": f"adopt compiled program {j['id']}",
                                                     "operations": [res["adopt_with"]]})
        print(f"adopted as the final program at r{r['revision']}")
    return 0


def cmd_submit(a) -> int:
    if not a.local:
        print("only local submissions are made here (`--local`); publication is `qccd publish`", file=sys.stderr)
        return 2
    info = _svc(a)
    if a.board:
        # one step: compile the board's circuit onto the design, adopt it, freeze, grade
        t = _call(info, "POST", "/api/submissions", {"profile": a.profile, "board": a.board, "design": a.design or "main"})
        print(f"submitting {t['design']} to {t['board']} (compile, adopt, freeze, grade)")
        j = _wait_job(info, t["job_id"])
        res = j.get("result") or {}
        if j["status"] != "succeeded":
            print(f"{j['status']}: {res.get('summary') or j.get('error')}", file=sys.stderr)
            print((res.get("log") or "")[-1500:], file=sys.stderr)
            return 1
        s = {"submission_id": res["submission_id"], "job_id": res["grading_job"], "revision": res["revision"],
             "label": "Local result - not published"}
        print(f"submission {s['submission_id']} of r{s['revision']} ({s['label']})")
    else:
        s = _call(info, "POST", "/api/submissions", {"profile": a.profile})
        print(f"submission {s['submission_id']} of r{s['revision']} ({s['label']}); bundle {s['bundle_digest']}")
    if a.wait:
        j = _wait_job(info, s["job_id"])
        sub = _call(info, "GET", f"/api/submissions/{s['submission_id']}")
        rep = sub.get("report") or {}
        print(f"{sub['status']}  " + "  ".join(f"{st['id']}={st['status']}" for st in rep.get("stages", [])))
        for k, v in (rep.get("metrics") or {}).items():
            print(f"  {k:24s} {v['value']} {v['unit']}")
        if a.json:
            print(json.dumps(rep, indent=1))
        return 0 if (rep.get("eligibility") or {}).get("eligible") else 1
    return 0


def cmd_leaderboard(a) -> int:
    info = _svc(a)
    b = _call(info, "GET", "/api/leaderboard" + (f"?board={_q(a.board)}" if a.board else ""))
    print(f"{b['label']}: {b.get('board') or b['task']}, ranked by {b['rank_by']} ({b['better']} is better)")
    for r in b["rows"]:
        print(f"  {r.get('design') or r['branch']:24s} r{r['revision']:<4} {r['status']:10s} "
              f"{'stale' if r['stale'] else 'current':8s} {r['metrics'].get(b['rank_by'])}")
    return 0


def cmd_import(a) -> int:
    info = _svc(a)
    r = _call(info, "POST", "/api/import", {"path": a.path, "mode": "preview" if a.preview else "apply"})
    print(f"{r['status']} r{r['revision']}: {r['summary']}")
    return 0


def cmd_releases(a) -> int:
    """The leaderboards (boards) a design can be submitted to, by title."""
    from .tasks import list_boards
    for r in list_boards():
        print(f"{r.title}\n    {(r.manifest.get('description') or '')[:110]}")
    return 0


def _q(v) -> str:
    import urllib.parse
    return urllib.parse.quote(str(v), safe="")


def cmd_publish(a) -> int:
    info = _svc(a)
    if a.submission:
        rv = _call(info, "POST", "/api/publish/prepare", {"submission_id": a.submission, "visibility": a.visibility})
        print(f"task      {rv['task']['id']}  {rv['task']['digest']}")
        print(f"bundle    {rv['bundle_digest']}")
        for f in rv["files"]:
            print(f"  {f['path']:24s} {f['bytes']:>9} B  {f['digest']}")
        lr = rv["local_report"]
        print(f"local     eligible={lr['eligible']} metrics={lr['metrics']}")
        print(f"params    {rv['params']}")
        if not sys.stdin.isatty():
            print("approval needs a person at an interactive terminal (or the Studio review dialog)", file=sys.stderr)
            return 2
        typed = input("Type the first 12 hex digits of the bundle digest to approve: ").strip()
        if typed != rv["bundle_digest"].split(":", 1)[1][:12]:
            print("not approved")
            return 1
        ap = _call(info, "POST", "/api/publish/approve", {"submission_id": a.submission, "bundle_digest": rv["bundle_digest"],
                                                          "params": {"visibility": a.visibility},
                                                          "interactive_confirmation": True})
        print(f"approved: {ap['approval_id']} (bound to {ap['bundle_digest']} and {ap['params']})")
        if not a.server:
            print(f"upload with: qccd publish --approval {ap['approval_id']} --server <url>")
            return 0
        a.approval = ap["approval_id"]
    if a.approval:
        if not a.server:
            print("--server is required to upload", file=sys.stderr)
            return 2
        from .publish import upload_approved
        out = upload_approved(_root(a), a.approval, a.server)
        print(json.dumps(out, indent=1))
        return 0
    print("name --submission (review and approve) or --approval (upload)", file=sys.stderr)
    return 2


def cmd_agent(a) -> int:
    from . import installers
    root = _root(a)
    if a.action == "install":
        for line in installers.install(root, a.client, scope=a.scope, channel=a.channel):
            print(line)
        return 0
    if a.action == "uninstall":
        for line in installers.uninstall(root, a.client, scope=a.scope):
            print(line)
        return 0
    if a.action == "status":
        print(json.dumps(installers.status(root, a.client), indent=1))
        return 0
    if a.action == "connect":
        if a.client != "codex":
            print("Claude Code connects itself: start it in this project with the QCCD channel enabled:\n"
                  "  claude --dangerously-load-development-channels server:qccd\n"
                  "(research preview; needs a claude.ai login). Without the channel it runs in pull mode.")
            return 0
        info = _svc(a)
        body: dict = {"label": a.label} if a.label else {}
        if a.thread:
            body["thread_id"] = a.thread
        else:
            body["start_thread"] = True
        if a.url:
            body["url"] = a.url
        r = _call(info, "POST", "/api/sessions/codex/connect", body, timeout=90)
        print(r["note"])
        print(f"session   {r['session']['id']}  thread {r['thread_id']}")
        print(f"attach    {r['attach']}")
        return 0
    return 2


def cmd_mcp(a) -> int:
    from .mcp_server import main as mcp_main
    argv = ["--client", a.client] + (["--channel"] if a.channel else []) + (["--root", a.root] if a.root else [])
    return mcp_main(argv)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="qccd", description="QCCD workspaces: local-first, agent-native co-design")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="create a workspace")
    p.add_argument("dir")
    p.add_argument("--task", default=None, help=argparse.SUPPRESS)       # the old pinned form; not needed
    p.add_argument("--name", default="design")
    p.add_argument("--empty", action="store_true", help="start from a blank canvas, not the release's starter")
    for name, fn in (("studio", None), ("web", None), ("serve", None), ("status", None), ("stop", None), ("validate", None),
                     ("compile", None), ("submit", None), ("publish", None), ("import", None), ("leaderboard", None)):
        q = sub.add_parser(name)
        q.add_argument("--root", default=None)
        if name == "studio":
            q.add_argument("--no-open", action="store_true")
            q.add_argument("--keep-alive", action="store_true",
                           help="stay in the foreground and restart the service if something kills it")
        if name == "serve":
            q.add_argument("--port", type=int, default=None)
        if name == "web":
            q.add_argument("path", nargs="?", default="", help="a page of the site, e.g. rules/ or learn/")
            q.add_argument("--no-open", action="store_true")
        if name in ("status", "validate", "submit"):
            q.add_argument("--json", action="store_true")
        if name in ("validate", "submit", "compile", "leaderboard"):
            q.add_argument("--board", default=None, help="a leaderboard by its title, e.g. 'BB [[144,12,12]]'")
        if name == "submit":
            q.add_argument("--design", default=None, help="the design's name (default: the main design)")
        if name == "compile":
            q.add_argument("--adopt", action="store_true")
            q.add_argument("--compiler", default="auto", choices=["auto", "compile", "rotate"])
        if name == "submit":
            q.add_argument("--local", action="store_true")
            q.add_argument("--profile", default="reference", choices=["draft", "reference"])
            q.add_argument("--wait", action="store_true")
        if name == "publish":
            q.add_argument("--submission")
            q.add_argument("--approval")
            q.add_argument("--server")
            q.add_argument("--visibility", default="public", choices=["public", "unlisted", "private"])
        if name == "import":
            q.add_argument("path")
            q.add_argument("--preview", action="store_true")
    p = sub.add_parser("toolchain", help="the prebuilt compiler: install it, or see which one is in use")
    p.add_argument("action", choices=["install", "status"])
    p.add_argument("--force", action="store_true", help="download and check it again")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("agent")
    p.add_argument("action", choices=["install", "uninstall", "status", "connect"])
    p.add_argument("--client", required=True, choices=["codex", "claude"])
    p.add_argument("--scope", default="project", choices=["project"])
    p.add_argument("--channel", action="store_true", help="(claude) configure the MCP server as a channel")
    p.add_argument("--thread")
    p.add_argument("--new", action="store_true")
    p.add_argument("--url")
    p.add_argument("--label")
    p.add_argument("--root", default=None)
    p = sub.add_parser("mcp")
    p.add_argument("--client", default="generic", choices=["codex", "claude", "generic"])
    p.add_argument("--channel", action="store_true")
    p.add_argument("--root", default=None)
    sub.add_parser("releases")
    sub.add_parser("boards", help="the leaderboards a design can be submitted to")
    p = sub.add_parser("trace", help="what an agent did for a request: calls, results, page actions")
    p.add_argument("--session")
    p.add_argument("--prompt")
    p.add_argument("--full", action="store_true", help="everything, uncut (the full text the agent received too)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--open", action="store_true", help="open the trace viewer in the browser")
    p.add_argument("--root", default=None)
    return ap


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "studio" and "--root" not in argv:
        # outside a workspace, `qccd studio` keeps its old meaning: write the static page
        from .runtime import find_workspace_root
        try:
            find_workspace_root()
        except FileNotFoundError:
            from ..__main__ import main as legacy
            return legacy(argv)
    if argv and argv[0] not in COMMANDS and argv[0] not in ("-h", "--help"):
        # everything else is the existing toolchain CLI
        from ..__main__ import main as legacy
        return legacy(argv)
    a = build_parser().parse_args(argv)
    fn = {"init": cmd_init, "serve": cmd_serve, "studio": cmd_studio, "web": cmd_web, "toolchain": cmd_toolchain,
          "status": cmd_status,
          "stop": cmd_stop,
          "validate": cmd_validate, "compile": cmd_compile, "submit": cmd_submit, "publish": cmd_publish,
          "import": cmd_import, "releases": cmd_releases, "boards": cmd_releases, "agent": cmd_agent, "mcp": cmd_mcp,
          "leaderboard": cmd_leaderboard, "trace": cmd_trace}[a.cmd]
    from .core import WorkspaceError
    from .tasks import LOCK_NAME
    if a.cmd not in ("mcp", "serve"):
        # a piped Windows console is cp1252, and an agent's words carry "≈" or "→": print what
        # can be printed rather than dying mid-output (`qccd trace | more` did, 2026-09-24)
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(errors="replace")
            except (AttributeError, ValueError):
                pass
    try:
        return fn(a)
    except WorkspaceError as exc:
        # a refusal the workspace explains itself: one line and what to do, not a traceback
        print(f"qccd {a.cmd}: {exc}", file=sys.stderr)
        hint = _HINTS.get(exc.code)
        if hint:
            print(f"  {hint}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        if LOCK_NAME not in str(exc):
            raise                          # not ours: keep the traceback
        print(f"qccd {a.cmd}: {exc}", file=sys.stderr)
        return 1


#: what to do next, for the refusals a person meets at the terminal
_HINTS = {
    "exists": "Nothing was changed. If it is the workspace you made earlier, cd into it and carry on "
              "(qccd studio --keep-alive); otherwise choose another name.",
}


if __name__ == "__main__":
    sys.exit(main())
