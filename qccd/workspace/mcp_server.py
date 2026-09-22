"""The QCCD MCP adapter: a thin stdio server onto the ONE workspace service.

Tested with the official Python MCP SDK 2.2.0 (`mcp`, `mcp-types`; pinned in
`requirements-agent.txt`).  It holds no design state: every tool is one HTTP call to the
workspace service (`runtime.ensure_service` finds it or starts it), so several agents,
Studio tabs and the CLI all see the same revisions.  Logs go to stderr; stdout carries
only the protocol.

Modes (reported in the session's capabilities):

  --client codex    tools only.  Automatic delivery to Codex is the service's app-server
                    bridge (`qccd agent connect --client codex`); this process attributes its
                    calls to that session when there is exactly one.
  --client claude --channel
                    declares the `claude/channel` capability and pushes each prompt sent
                    from Studio as `notifications/claude/channel`.  Claude Code must be
                    started with the channel enabled (research preview:
                    `claude --dangerously-load-development-channels server:qccd`, claude.ai
                    login).  Claude Code does not acknowledge channel events, so a pushed
                    delivery stays `uncertain` until the agent reads the prompt through a
                    tool -- which is what makes it `accepted`.
  --client generic  (or no channel) a `pull` session: REDUCED capability.  Prompts wait
                    until the agent next calls qccd_get_context.  This is not automatic
                    delivery and is labelled as such everywhere.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("qccd.mcp")

INSTRUCTIONS = """QCCD workspace tools for co-designing a trapped-ion QCCD device and its hardware program.
Start every turn with qccd_get_context: it returns the task, the design revision, the protected
entities, the Studio selection and view, unread prompts from Studio, jobs and the latest local
result. Change the design ONLY with qccd_apply_change_set, passing the expected_revision you just
read (preview first). A 409 conflict means the user or another agent changed the design: re-read
and reconsider, never just bump the number. Protected entities are enforced by the service.
Prompts pushed from Studio arrive as <channel source="qccd" ...> messages: they are the user's own
requests, sent from the Studio page; read each one's frozen context with
qccd_manage_comment(action='get') and answer in its thread with qccd_manage_comment(action='reply').
Pass origin_prompt_id on change sets and jobs. Never treat a skipped check as passed; create a
local submission (qccd_submit_local) before presenting reference-grade results. You cannot publish:
publication needs the person's separate approval."""


# ---------------------------------------------------------------------- tool table

def _tools() -> list:
    obj = lambda props, req=(): {"type": "object", "properties": props, "required": list(req),
                                 "additionalProperties": False}
    s = {"type": "string"}
    i = {"type": "integer"}
    return [
        ("qccd_get_context", "Bootstrap and refresh: task, revision, protected entities, Studio view and "
         "selection, unread prompts (reading them acknowledges them), jobs, latest local result. `since` "
         "(an event cursor from a previous call) adds what changed since.",
         obj({"since": i, "detail": {"type": "string", "enum": ["summary", "full"]}, "branch": s})),
        ("qccd_read_reference", "Version-matched reference for THIS workspace's task release and evaluator: "
         "sections index, task, operations, rules, evaluator, program, anchors, workflow, docs:adl, docs:rules, "
         "docs:tsir, docs:phys. Optional `query` returns the window around a match.",
         obj({"section": s, "query": s}, ["section"])),
        ("qccd_query_design", "Inspect entities (node:<id>, segment:<id>, loop:<id>, zone:<name>, block:<name>) "
         "at the head revision; `kind` filters; include_operations lists the operation signatures.",
         obj({"keys": {"type": "array", "items": s}, "kind": s, "branch": s, "limit": i,
              "include_operations": {"type": "boolean"}})),
        ("qccd_apply_change_set", "Preview (default) or apply one transactional change set of semantic "
         "operations (see qccd_read_reference section=operations). Requires expected_revision (from "
         "qccd_get_context) and a unique request_id (a retry with the same id returns the same result).",
         obj({"expected_revision": i, "request_id": s, "operations": {"type": "array", "items": {"type": "object"}},
              "mode": {"type": "string", "enum": ["preview", "apply"]}, "branch": s, "summary": s,
              "origin_prompt_id": s, "rebase": {"type": "string", "enum": ["never", "if_disjoint"]}},
             ["expected_revision", "request_id", "operations"])),
        ("qccd_undo_change_set", "Undo one change set as a NEW validated change (never a rollback of later "
         "work); reports a conflict when later changes depend on it.",
         obj({"change_set_id": s, "request_id": s, "expected_revision": i}, ["change_set_id"])),
        ("qccd_manage_branch", "Candidate branches: action create (name), compare (a, b), adopt "
         "(candidate, expected_revision, request_id), discard (candidate), list.",
         obj({"action": {"type": "string", "enum": ["create", "compare", "adopt", "discard", "list"]},
              "name": s, "a": s, "b": s, "candidate": s, "expected_revision": i, "request_id": s,
              "note": s, "origin_prompt_id": s, "mode": {"type": "string", "enum": ["preview", "apply"]}},
             ["action"])),
        ("qccd_manage_comment", "Prompt threads: action get (prompt_id: the prompt, its versions and frozen "
         "context snapshot; acknowledges it), list, reply (prompt_id, text, optional work_state "
         "working|waiting_input|ready_for_review|resolved, links), ack (prompt_ids). Replies never "
         "trigger another agent turn.",
         obj({"action": {"type": "string", "enum": ["get", "list", "reply", "ack", "state"]}, "prompt_id": s,
              "prompt_ids": {"type": "array", "items": s}, "text": s, "work_state": s,
              "links": {"type": "object"}, "request_id": s, "state": s}, ["action"])),
        ("qccd_start_job", "Start a job and return its id at once: kind compile (the real compiler on the task "
         "circuit for a revision; result carries adopt_with for set_final_program) or evaluate (profile "
         "draft|reference on a frozen snapshot of the revision).",
         obj({"kind": {"type": "string", "enum": ["compile", "evaluate"]}, "branch": s, "revision": i,
              "profile": {"type": "string", "enum": ["draft", "reference"]},
              "compiler": {"type": "string", "enum": ["compile", "rotate"]}, "request_id": s,
              "origin_prompt_id": s}, ["kind"])),
        ("qccd_get_job", "Progress and lifecycle state of a job.", obj({"job_id": s}, ["job_id"])),
        ("qccd_cancel_job", "Request cancellation; reports what actually happened.", obj({"job_id": s}, ["job_id"])),
        ("qccd_inspect_run", "A local submission's report in bounded parts: part summary|stages|diagnostics|"
         "metrics; diagnostics are paginated with offset/limit.",
         obj({"submission_id": s, "part": {"type": "string", "enum": ["summary", "stages", "diagnostics", "metrics"]},
              "offset": i, "limit": i}, ["submission_id"])),
        ("qccd_present", "Ask Studio to show something: action highlight|select (target.keys), open_prompt "
         "(target.prompt_id), select_frame (target.frame), open_result (target.submission_id), "
         "reveal_diagnostic, compare, open_branch. Studio honours the user's Follow-agent setting.",
         obj({"action": s, "target": {"type": "object"}, "view_id": s, "note": s}, ["action", "target"])),
        ("qccd_submit_local", "Freeze the revision into an immutable bundle and grade it with the reference "
         "evaluator (returns submission and job ids at once). The design needs an adopted final program.",
         obj({"branch": s, "revision": i, "profile": {"type": "string", "enum": ["draft", "reference"]},
              "request_id": s, "origin_prompt_id": s, "title": s})),
        ("qccd_prepare_publish", "Show exactly what publication would upload (bundle digest, files, local "
         "report). It creates nothing: approval and upload need the person.",
         obj({"submission_id": s, "visibility": {"type": "string", "enum": ["public", "unlisted", "private"]}},
             ["submission_id"])),
        ("qccd_import_file", "Import a design file written outside Studio (e.g. design/studio.json edited by "
         "an optimizer) as a validated change set; stale files are refused, never overwrite newer edits.",
         obj({"path": s, "request_id": s, "mode": {"type": "string", "enum": ["preview", "apply"]}}, ["path"])),
    ]


# ---------------------------------------------------------------------- the bridge to the service

class Backend:
    def __init__(self, root: Path, client: str, channel: bool):
        from .runtime import ensure_service
        self.root = root
        self.info = ensure_service(root)
        self.client = client
        self.channel = channel
        self.session: str | None = None

    def call(self, method: str, path: str, body=None, *, timeout: float = 60.0):
        from .runtime import ServiceError, ensure_service, service_request
        try:
            return service_request(self.info, method, path, body, session=self.session, timeout=timeout)
        except ServiceError:
            raise
        except OSError as exc:
            # the service went away (restart): find or start it again, once
            log.warning("the workspace service on port %s is unreachable (%s); finding or starting it",
                        self.info.get("port"), exc)
            self.info = ensure_service(self.root)
            return service_request(self.info, method, path, body, session=self.session, timeout=timeout)

    def register(self) -> dict:
        sid = os.environ.get("QCCD_SESSION_ID")
        if sid:
            self.session = sid
            return {"id": sid, "mode": "preassigned"}
        if self.client == "codex" and not self.channel:
            ss = [s for s in self.call("GET", "/api/sessions")["sessions"]
                  if s["client"] == "codex" and s["mode"] == "appserver" and s["status"] == "connected"]
            if len(ss) == 1:
                self.session = ss[0]["id"]
                return ss[0]
        mode = "channel" if self.channel else "pull"
        caps = ({"deliver": "notifications/claude/channel (unacknowledged)", "steer": None, "interrupt": None,
                 "observe": None, "acknowledgement": "the agent reading the prompt through a tool"}
                if self.channel else
                {"deliver": None, "note": "pull only: prompts are seen on the next qccd_get_context"})
        label = {"claude": "Claude Code", "codex": "Codex", "generic": "MCP client"}.get(self.client, self.client)
        s = self.call("POST", "/api/sessions", {"client": self.client if self.client in ("codex", "claude") else "generic",
                                                "mode": mode, "label": label + (" (channel)" if self.channel else " (pull only)"),
                                                "capabilities": caps})
        self.session = s["id"]
        return s


def dispatch(be: Backend, name: str, a: dict) -> Any:
    """One tool -> one service call.  Returns the JSON result."""
    q = lambda d: "&".join(f"{k}={_enc(v)}" for k, v in d.items() if v is not None)
    if name == "qccd_get_context":
        return be.call("GET", "/api/context?" + q({"since": a.get("since"), "detail": a.get("detail"),
                                                   "branch": a.get("branch")}))
    if name == "qccd_read_reference":
        return be.call("GET", "/api/reference?" + q({"section": a.get("section"), "query": a.get("query")}))
    if name == "qccd_query_design":
        keys = ",".join(a.get("keys") or []) or None
        return be.call("GET", "/api/query?" + q({"keys": keys, "kind": a.get("kind"), "branch": a.get("branch"),
                                                 "limit": a.get("limit"),
                                                 "operations": "1" if a.get("include_operations") else None}))
    if name == "qccd_apply_change_set":
        body = {k: a[k] for k in ("expected_revision", "request_id", "operations", "mode", "branch", "summary",
                                  "origin_prompt_id", "rebase") if k in a}
        body.setdefault("mode", "preview")
        return be.call("POST", "/api/change-sets", body)
    if name == "qccd_undo_change_set":
        return be.call("POST", f"/api/change-sets/{_enc(a['change_set_id'])}/undo",
                       {"request_id": a.get("request_id"), "expected_revision": a.get("expected_revision")})
    if name == "qccd_manage_branch":
        act = a["action"]
        if act == "list":
            return be.call("GET", "/api/branches")
        if act == "create":
            return be.call("POST", "/api/branches", {"name": a.get("name"), "note": a.get("note", ""),
                                                     "origin_prompt_id": a.get("origin_prompt_id")})
        if act == "compare":
            return be.call("GET", "/api/compare?" + q({"a": a.get("a"), "b": a.get("b", "main")}))
        if act == "adopt":
            return be.call("POST", "/api/branches/adopt", {"candidate": a.get("candidate"),
                                                           "expected_revision": a.get("expected_revision"),
                                                           "request_id": a.get("request_id"),
                                                           "mode": a.get("mode", "apply")})
        if act == "discard":
            return be.call("POST", "/api/branches/discard", {"candidate": a.get("candidate")})
    if name == "qccd_manage_comment":
        act = a["action"]
        if act == "list":
            return be.call("GET", "/api/prompts?open=1")
        if act == "get":
            return be.call("GET", f"/api/prompts/{_enc(a['prompt_id'])}")
        if act == "reply":
            return be.call("POST", f"/api/prompts/{_enc(a['prompt_id'])}/reply",
                           {"text": a.get("text", ""), "work_state": a.get("work_state"), "links": a.get("links"),
                            "request_id": a.get("request_id")})
        if act == "ack":
            return be.call("POST", "/api/prompts/ack", {"prompt_ids": a.get("prompt_ids") or [a.get("prompt_id")]})
        if act == "state":
            return be.call("POST", f"/api/prompts/{_enc(a['prompt_id'])}/state", {"state": a.get("state")})
    if name == "qccd_start_job":
        params = {k: a[k] for k in ("branch", "revision", "profile", "compiler") if k in a}
        return be.call("POST", "/api/jobs", {"kind": a["kind"], "params": params, "request_id": a.get("request_id"),
                                             "origin_prompt_id": a.get("origin_prompt_id")})
    if name == "qccd_get_job":
        return be.call("GET", f"/api/jobs/{_enc(a['job_id'])}")
    if name == "qccd_cancel_job":
        return be.call("POST", f"/api/jobs/{_enc(a['job_id'])}/cancel", {})
    if name == "qccd_inspect_run":
        s = be.call("GET", f"/api/submissions/{_enc(a['submission_id'])}")
        rep = s.get("report") or {}
        part = a.get("part", "summary")
        head = {"submission_id": s["id"], "status": s["status"], "revision": s["snapshot"]["revision"],
                "stale": s["stale"], "label": s["label"], "bundle_digest": s["snapshot"]["bundle_digest"]}
        if part == "stages":
            return {**head, "stages": [{k: st[k] for k in ("id", "required", "status", "coverage")} for st in rep.get("stages", [])]}
        if part == "metrics":
            return {**head, "metrics": rep.get("metrics"), "rank_by": rep.get("rank_by"),
                    "cost_breakdown": rep.get("cost_breakdown")}
        if part == "diagnostics":
            off, lim = int(a.get("offset", 0)), min(int(a.get("limit", 50)), 200)
            d = rep.get("diagnostics") or []
            return {**head, "total": len(d), "offset": off, "diagnostics": d[off:off + lim]}
        return {**head, "eligibility": rep.get("eligibility"), "profile": rep.get("profile"),
                "evaluator": {k: (rep.get("evaluator") or {}).get(k) for k in ("name", "version")},
                "stages": {st["id"]: st["status"] for st in rep.get("stages", [])},
                "metrics": {k: v.get("value") for k, v in (rep.get("metrics") or {}).items()}}
    if name == "qccd_present":
        return be.call("POST", "/api/present", {"action": a["action"], "target": a.get("target") or {},
                                                "view_id": a.get("view_id"), "note": a.get("note", "")})
    if name == "qccd_submit_local":
        return be.call("POST", "/api/submissions", {k: a[k] for k in ("branch", "revision", "profile", "request_id",
                                                                     "origin_prompt_id", "title") if k in a})
    if name == "qccd_prepare_publish":
        return be.call("POST", "/api/publish/prepare", {"submission_id": a["submission_id"],
                                                        "visibility": a.get("visibility", "public")})
    if name == "qccd_import_file":
        return be.call("POST", "/api/import", {"path": a["path"], "request_id": a.get("request_id"),
                                               "mode": a.get("mode", "apply")})
    raise ValueError(f"unknown tool {name!r}")


def _enc(v) -> str:
    import urllib.parse
    return urllib.parse.quote(str(v), safe="")


def summarize(name: str, out: Any) -> str:
    """A short readable line in front of the machine-readable result."""
    if not isinstance(out, dict):
        return name
    if name == "qccd_get_context":
        return (f"{out.get('task', {}).get('id')} - {out.get('branch')} r{out.get('revision')}; "
                f"{len(out.get('unread_prompts') or [])} unread prompt(s); "
                f"{len(out.get('protected') or [])} protected; mode {out.get('mode')}")
    if name == "qccd_apply_change_set":
        return f"{out.get('status')} r{out.get('revision')}: {out.get('summary')}"
    return json.dumps(out)[:200]


# ---------------------------------------------------------------------- the MCP server

def run(root: Path, client: str, channel: bool) -> None:
    import anyio
    import mcp_types as types
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from .runtime import ServiceError

    be = Backend(root, client, channel)
    sess = be.register()
    log.info("qccd mcp: workspace %s, session %s (%s)", be.info["workspace_id"], be.session, sess.get("mode"))
    tools = _tools()
    holder: dict = {"conn": None}

    read_only = {"qccd_get_context", "qccd_read_reference", "qccd_query_design", "qccd_get_job",
                 "qccd_inspect_run", "qccd_prepare_publish"}

    def annotations(n):
        return types.ToolAnnotations(read_only_hint=n in read_only, destructive_hint=False,
                                     idempotent_hint=n in read_only, open_world_hint=False)

    async def list_tools(ctx, params):
        holder["conn"] = holder["conn"] or getattr(ctx.session, "_connection", None)
        return types.ListToolsResult(tools=[types.Tool(name=n, description=d, input_schema=s,
                                                       annotations=annotations(n)) for n, d, s in tools])

    async def call_tool(ctx, params):
        holder["conn"] = holder["conn"] or getattr(ctx.session, "_connection", None)
        name = params.name
        args = dict(params.arguments or {})
        try:
            out = await anyio.to_thread.run_sync(dispatch, be, name, args)
            text = summarize(name, out) + "\n" + json.dumps(out, indent=1)[:60000]
            return types.CallToolResult(content=[types.TextContent(type="text", text=text)],
                                        structured_content=out if isinstance(out, dict) else {"result": out})
        except ServiceError as exc:
            detail = exc.detail
            text = f"refused ({exc.status} {detail.get('code')}): {detail.get('message')}\n" + json.dumps(detail, indent=1)[:20000]
            return types.CallToolResult(content=[types.TextContent(type="text", text=text)],
                                        structured_content={"error": detail, "status": exc.status}, is_error=True)
        except Exception as exc:
            return types.CallToolResult(content=[types.TextContent(type="text", text=f"{type(exc).__name__}: {exc}")],
                                        is_error=True)

    server = Server("qccd", on_list_tools=list_tools, on_call_tool=call_tool, instructions=INSTRUCTIONS)
    experimental = {"claude/channel": {}} if channel else {}

    async def channel_loop():
        """Claim this session's deliveries and push them into the Claude Code session."""
        while True:
            conn = holder["conn"]
            if conn is None:
                await anyio.sleep(0.5)
                continue
            try:
                got = await anyio.to_thread.run_sync(
                    lambda: be.call("POST", "/api/deliveries/claim", {"wait_s": 20, "limit": 5}, timeout=40))
            except Exception as exc:  # service restart, network: back off and retry
                log.warning("claim failed: %s", exc)
                await anyio.sleep(2)
                continue
            for d in got.get("deliveries") or []:
                meta = {"prompt_id": d["prompt_id"], "version": str(d["version"]), "delivery_id": d["delivery_id"],
                        "workspace": be.info["workspace_id"], "intent": d["body"].get("intent", ""),
                        "mode": d["body"].get("mode", "")}
                try:
                    log.info("channel push %s via %s/%s", d["delivery_id"], type(conn).__name__,
                             type(getattr(conn, "outbound", None)).__name__)
                    await conn.notify("notifications/claude/channel", {"content": d["text"], "meta": meta})
                    state, err = "uncertain", None
                except Exception as exc:
                    state, err = "queued", f"{type(exc).__name__}: {exc}"
                await anyio.to_thread.run_sync(lambda: be.call(
                    "POST", f"/api/deliveries/{d['delivery_id']}/mark",
                    {"state": state, "error": err, "retry_in": 5 if err else None,
                     "detail": "pushed to the Claude Code channel; Claude Code does not acknowledge -- "
                               "accepted once the agent reads the prompt" if not err else None}))

    async def main():
        async with stdio_server() as (r, w):
            async with anyio.create_task_group() as tg:
                if channel:
                    tg.start_soon(channel_loop)
                await server.run(r, w, server.create_initialization_options(experimental_capabilities=experimental))
                tg.cancel_scope.cancel()

    anyio.run(main)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="qccd mcp", description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=None, help="workspace directory (default: $QCCD_WORKSPACE, "
                    "$CLAUDE_PROJECT_DIR, or the nearest qccd.lock.json above the cwd)")
    ap.add_argument("--client", default="generic", choices=["codex", "claude", "generic"])
    ap.add_argument("--channel", action="store_true", help="declare the Claude Code channel capability")
    args = ap.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=os.environ.get("QCCD_MCP_LOG", "INFO"),
                        format="%(asctime)s qccd-mcp %(levelname)s %(message)s")
    from .runtime import find_workspace_root
    start = args.root or os.environ.get("QCCD_WORKSPACE") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    root = find_workspace_root(Path(start))
    run(root, args.client, args.channel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
