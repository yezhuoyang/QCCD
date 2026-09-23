"""`qccd` workspace commands.

    qccd init my-design --task ghz4@1        a workspace: lockfile, design mirror, .qccd/
    qccd studio [--no-open] [--keep-alive]   start (or reuse) the service; open Studio paired;
                                             --keep-alive stays and restarts a killed service
    qccd serve                               run the service in the foreground
    qccd status | stop                       the service, sessions, head revision
    qccd agent install|uninstall|status --client codex|claude [--scope project]
    qccd agent connect --client codex [--thread ID | --new] [--url ws://127.0.0.1:PORT]
    qccd mcp --client codex|claude|generic [--channel]     (launched by the agent client)
    qccd validate [--json]                   draft grade of the head revision
    qccd compile [--adopt]                   the real compiler on the task circuit
    qccd submit --local [--profile reference] [--wait]
    qccd publish --submission SUB [--visibility public]   review + approve at this terminal
    qccd publish --approval AP --server URL  upload an approved bundle
    qccd import design/studio.json           a file written outside Studio, as a change set
    qccd releases                            the installed task releases

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

COMMANDS = ("init", "studio", "serve", "status", "stop", "agent", "mcp", "validate", "compile", "submit",
            "publish", "import", "releases", "leaderboard")


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
    print(f"workspace {ws.id} in {ws.root}")
    print(f"  task      {ws.release.id} ({ws.release.digest[:23]}...)")
    print(f"  design    r{h.revision}: {ws.replayed().arch.name}, {len(ws.replayed().arch.device.nodes)} nodes")
    print("  next      cd into it; `qccd agent install --client codex|claude`; `qccd studio`")
    ws.close()
    return 0


def cmd_serve(a) -> int:
    from .service import serve
    return serve(_root(a), port=a.port)


def cmd_studio(a) -> int:
    info = _svc(a)
    code = _call(info, "POST", "/api/pair-code", {})["code"]
    url = f"http://127.0.0.1:{info['port']}/studio#pair={code}"
    print(f"Studio: {url}")
    print("  (the pairing code works once, for five minutes; run `qccd studio` again for another tab)")
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


def cmd_status(a) -> int:
    from .runtime import read_runtime
    from .tasks import read_lock
    root = _root(a)
    lock = read_lock(root)
    info = read_runtime(lock["workspace_id"])
    out = {"workspace": lock["workspace_id"], "root": str(root), "task": lock["task"]["id"],
           "service": None}
    if info:
        ctx = _call(info, "GET", "/api/context")
        sessions = _call(info, "GET", "/api/sessions")["sessions"]
        out["service"] = {"port": info["port"], "pid": info["pid"]}
        out["revision"] = ctx["revision"]
        out["mode"] = ctx["mode"]
        out["sessions"] = [{k: s[k] for k in ("id", "client", "mode", "status", "label", "write_fence")} for s in sessions]
        out["views"] = [{k: v[k] for k in ("id", "connected", "follow_agent", "target_session")}
                        for v in _call(info, "GET", "/api/views")["views"]]
    print(json.dumps(out, indent=1) if a.json else _fmt_status(out))
    return 0


def _fmt_status(o) -> str:
    lines = [f"workspace {o['workspace']}  task {o['task']}  ({o['root']})"]
    if not o["service"]:
        lines.append("service   not running (`qccd studio` starts it)")
        return "\n".join(lines)
    lines.append(f"service   127.0.0.1:{o['service']['port']} pid {o['service']['pid']}  design r{o['revision']} ({o['mode']})")
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
        j = _call(info, "POST", "/api/jobs", {"kind": "evaluate", "params": {"profile": "draft"}})
        j = _wait_job(info, j["job_id"])
        out["draft"] = (j.get("result") or {})
        out["draft"].pop("traceback", None)
    else:
        out["draft"] = "no final program yet: `qccd compile --adopt` first"
    print(json.dumps(out, indent=1) if a.json else json.dumps(out, indent=1)[:6000])
    return 0


def cmd_compile(a) -> int:
    info = _svc(a)
    j = _call(info, "POST", "/api/jobs", {"kind": "compile", "params": {"compiler": a.compiler}})
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
    b = _call(info, "GET", "/api/leaderboard")
    print(f"{b['label']}: {b['task']}, ranked by {b['rank_by']} ({b['better']} is better)")
    for r in b["rows"]:
        print(f"  {r['submission_id']}  r{r['revision']:<4} {r['status']:10s} {'stale' if r['stale'] else 'current':8s} "
              f"{r['metrics'].get(b['rank_by'])}")
    return 0


def cmd_import(a) -> int:
    info = _svc(a)
    r = _call(info, "POST", "/api/import", {"path": a.path, "mode": "preview" if a.preview else "apply"})
    print(f"{r['status']} r{r['revision']}: {r['summary']}")
    return 0


def cmd_releases(a) -> int:
    from .tasks import list_releases
    for r in list_releases():
        print(f"{r.id:20s} {r.digest[:23]}...  {r.manifest.get('title')}")
    return 0


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
    p.add_argument("--task", required=True, help="<task>@<release>, e.g. ghz4@1")
    p.add_argument("--name", default="design")
    p.add_argument("--empty", action="store_true", help="start from a blank canvas, not the release's starter")
    for name, fn in (("studio", None), ("serve", None), ("status", None), ("stop", None), ("validate", None),
                     ("compile", None), ("submit", None), ("publish", None), ("import", None), ("leaderboard", None)):
        q = sub.add_parser(name)
        q.add_argument("--root", default=None)
        if name == "studio":
            q.add_argument("--no-open", action="store_true")
            q.add_argument("--keep-alive", action="store_true",
                           help="stay in the foreground and restart the service if something kills it")
        if name == "serve":
            q.add_argument("--port", type=int, default=None)
        if name in ("status", "validate", "submit"):
            q.add_argument("--json", action="store_true")
        if name == "compile":
            q.add_argument("--adopt", action="store_true")
            q.add_argument("--compiler", default="compile", choices=["compile", "rotate"])
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
    fn = {"init": cmd_init, "serve": cmd_serve, "studio": cmd_studio, "status": cmd_status, "stop": cmd_stop,
          "validate": cmd_validate, "compile": cmd_compile, "submit": cmd_submit, "publish": cmd_publish,
          "import": cmd_import, "releases": cmd_releases, "agent": cmd_agent, "mcp": cmd_mcp,
          "leaderboard": cmd_leaderboard}[a.cmd]
    from .core import WorkspaceError
    from .tasks import LOCK_NAME
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
