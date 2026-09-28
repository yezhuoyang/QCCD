"""Every agent capability, declared ONCE: the tool, what it tells the agent, and what it does.

The rule this file keeps (docs/agent-interface.md): a feature ships with its agent surface or it
does not ship.  Before this table, a tool lived in five hand-kept places -- the MCP tool list,
the read-only set, the dispatcher, the skill and the website's Agentic page -- and nothing but
care kept them in step.  Now one `Feature` is the declaration and everything else is generated
from it:

  the MCP tools    mcp_server._tools(), READ_ONLY and dispatch() read FEATURES
  the manifest     interface.manifest()["tools"] (qccd_read_reference section 'interface')
  the skill        references/tools.md and references/tools.json (installers.build_skill), so an
                   installed skill lists every tool of the code it was built from
  the website      the Agentic page's tool table (qccd/site/agentic.py reads mcp_server._tools)

and `tests/test_feature_registry.py` refuses the rest of the drift:

  * every route an agent can reach is called by a Feature or listed in INTERNAL_ROUTES with why;
  * every CLI verb does what a Feature does, or is in LOCAL_VERBS with why;
  * every job kind is started by some Feature, and every Feature's route exists;
  * the skill the installer builds carries every tool.

To add a capability: append a Feature here (name, about, schema, read_only, call, routes; cli and
job_kinds when they apply), add its service route, and run the test.  Nothing else is edited by
hand -- the skill refreshes itself when its generated content changes (installers.skill_digest).
"""

from __future__ import annotations

import time as _t
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = ["Feature", "FEATURES", "BY_NAME", "INTERNAL_ROUTES", "LOCAL_VERBS", "tools_markdown", "tools_json",
           "SHOW_RUN"]


@dataclass(frozen=True)
class Feature:
    """One agent capability.  `call(backend, args)` makes the service call(s) the tool stands for."""

    name: str                                   # the MCP tool name, qccd_<verb>[_<noun>]
    about: str                                  # what the agent is told (the tool description)
    schema: dict                                # the JSON schema of the tool's input
    call: Callable[[Any, dict], Any]
    routes: tuple[str, ...]                     # "METHOD /api/path" the call uses (checked to exist)
    read_only: bool = False                     # the MCP read-only hint
    cli: tuple[str, ...] = ()                   # `qccd <verb>`s that do the same thing at a terminal
    job_kinds: tuple[str, ...] = ()             # the job kinds it starts
    group: str = "design"                       # how the skill's tool list groups it

    def tool(self) -> tuple:
        return (self.name, self.about, self.schema)


def _obj(props: dict, req=()) -> dict:
    return {"type": "object", "properties": props, "required": list(req), "additionalProperties": False}


_S = {"type": "string"}
_I = {"type": "integer"}


def _enc(v) -> str:
    return urllib.parse.quote(str(v), safe="")


def _q(d: dict) -> str:
    return "&".join(f"{k}={_enc(v)}" for k, v in d.items() if v is not None)


# ---------------------------------------------------------------------- calls with more than one step

def _apply_change_set(be, a: dict):
    body = {k: a[k] for k in ("expected_revision", "request_id", "operations", "mode", "branch", "summary",
                              "origin_prompt_id", "rebase") if k in a}
    body.setdefault("mode", "preview")
    if not body.get("branch"):
        # no design named: the one the person's Studio shows, which the context calls the current
        # design -- not main.  A live agent that had just opened its new design in the Studio sent
        # its docks to main, and spent two model calls finding out (2026-09-26)
        try:
            body["branch"] = be.call("GET", "/api/context?detail=summary").get("branch") or "main"
        except Exception:
            body["branch"] = "main"
    return be.call("POST", "/api/change-sets", body)


def _manage_branch(be, a: dict):
    act = a["action"]
    if act == "list":
        return be.call("GET", "/api/branches")
    if act == "create":
        return be.call("POST", "/api/branches", {"name": a.get("name"), "title": a.get("title"),
                                                 "note": a.get("note", ""), "source": a.get("source") or "main",
                                                 "origin_prompt_id": a.get("origin_prompt_id")})
    if act == "rename":
        return be.call("POST", "/api/branches/rename", {"design": a.get("design") or a.get("candidate") or "main",
                                                        "title": a.get("title")})
    if act == "compare":
        return be.call("GET", "/api/compare?" + _q({"a": a.get("a"), "b": a.get("b", "main")}))
    if act == "adopt":
        return be.call("POST", "/api/branches/adopt", {"candidate": a.get("candidate"),
                                                       "expected_revision": a.get("expected_revision"),
                                                       "request_id": a.get("request_id"),
                                                       "mode": a.get("mode", "apply")})
    if act == "discard":
        return be.call("POST", "/api/branches/discard", {"candidate": a.get("candidate")})
    raise ValueError(f"unknown branch action {act!r}")


def _manage_comment(be, a: dict):
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
    raise ValueError(f"unknown comment action {act!r}")


def _get_job(be, a: dict):
    # a live agent polled a 7-minute grade every 3 s, one model turn per poll (2026-09-24)
    deadline = _t.time() + max(0, min(int(a.get("wait_s") or 0), 50))
    while True:
        j = be.call("GET", f"/api/jobs/{_enc(a['job_id'])}")
        if j.get("status") not in ("queued", "running") or _t.time() >= deadline:
            break
        _t.sleep(1.0)
    if j.get("kind") == "run" and j.get("status") == "succeeded" and "run" in (j.get("result") or {}):
        return _run_result(be, j["id"], j["result"])
    # a submission is two jobs (compile-adopt-freeze, then the grade): one call follows both, so
    # the verdict comes back in the same answer when it is ready in time
    gid = (j.get("result") or {}).get("grading_job") if j.get("kind") == "submit" else None
    if gid and j.get("status") == "succeeded":
        while True:
            g = be.call("GET", f"/api/jobs/{_enc(gid)}")
            if g.get("status") not in ("queued", "running") or _t.time() >= deadline:
                break
            _t.sleep(1.0)
        res = g.get("result") or {}
        j["grade"] = {"status": g.get("status"), "summary": res.get("summary"), "eligible": res.get("eligible"),
                      "metrics": res.get("metrics"), "stage": (g.get("progress") or {}).get("message")
                      if g.get("status") in ("queued", "running") else None}
    return j


def _compare_runs(be, a: dict):
    ids = list(a.get("run_ids") or [])
    cmp = be.call("GET", "/api/compare-runs?" + _q({"runs": ",".join(ids)}))
    if a.get("show", True):
        pres = be.call("POST", "/api/present", {"action": "compare", "target": {"runs": ids},
                                                "note": a.get("note", "")})
        cmp["presented"] = pres.get("status")
    return cmp


def _inspect_run(be, a: dict):
    s = be.call("GET", f"/api/submissions/{_enc(a['submission_id'])}")
    rep = s.get("report") or {}
    part = a.get("part", "summary")
    head = {"submission_id": s["id"], "status": s["status"], "revision": s["snapshot"]["revision"],
            "stale": s["stale"], "label": s["label"], "bundle_digest": s["snapshot"]["bundle_digest"]}
    if part == "stages":
        return {**head, "stages": [{k: st[k] for k in ("id", "required", "status", "coverage")}
                                   for st in rep.get("stages", [])]}
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


def _submit_local(be, a: dict):
    body = {k: a[k] for k in ("design", "board", "profile", "request_id", "origin_prompt_id", "title") if k in a}
    if "design" not in body and "board" not in body:          # the older form: an adopted program, as it is
        body = {k: a[k] for k in ("branch", "revision", "profile", "request_id", "origin_prompt_id", "title")
                if k in a}
    elif "design" not in body and a.get("branch"):
        body["design"] = a["branch"]
    return be.call("POST", "/api/submissions", body)


def _run_program(be, a: dict) -> dict:
    """Start a run and wait (bounded) for its report, so one call usually answers."""
    params = {"program": a["program"]}
    if a.get("draft"):
        params["branch"] = a["draft"]
    for k in ("revision", "compiler", "animate"):
        if k in a:
            params[k] = a[k]
    j = be.call("POST", "/api/jobs", {"kind": "run", "params": params, "request_id": a.get("request_id"),
                                      "origin_prompt_id": a.get("origin_prompt_id")})
    jid = j["job_id"]
    deadline = _t.time() + max(0, min(int(a.get("wait_s", 45)), 50))
    while True:
        jj = be.call("GET", f"/api/jobs/{_enc(jid)}")
        if jj["status"] in ("succeeded", "failed", "cancelled", "timeout", "internal_error") or _t.time() >= deadline:
            break
        _t.sleep(1.0)
    res = jj.get("result") or {}
    if jj["status"] == "succeeded":
        return _run_result(be, jid, res)
    if jj["status"] in ("queued", "running"):
        return {"run_id": jid, "status": jj["status"], "progress": jj.get("progress"),
                "next": "still running: poll qccd_get_job with this run_id"}
    return {"run_id": jid, "status": jj["status"], "summary": res.get("summary") or jj.get("error"),
            "log_tail": (res.get("log") or "")[-1500:]}


SHOW_RUN = "the person watches this run: while it compiled their Studio showed its program, and their tab now opens the run's own page (circuit and compiled program beside the animation), which PLAYS BY ITSELF (or, with animate false, opens at its result), with its Evaluation panel counting the round's time up as it plays. Do not press Play or wait for it: say in one line what they are watching, with the numbers from `evaluation` (the panel's own), and go on with the next step in the same turn -- for a design meant for a board, submit it now (qccd_submit_local), in the same response as that line."


def _run_result(be, jid: str, res: dict, wait_s: float = 20.0) -> dict:
    """A finished run, with the numbers its page shows.  The person's tab opens the run's page
    (the Studio opens it when the run finishes); the page reports what its Evaluation panel
    shows, and that is what the agent gets, so what it says is what is on the screen.  With no
    page open (no Studio connected), the evaluator's report, labelled as such."""
    run = res["run"]
    out = {"run_id": jid, "status": "succeeded", "summary": res.get("summary"),
           **{k: run[k] for k in ("program", "design", "compiler", "view", "note")}, "show": SHOW_RUN}
    watched = False
    try:
        watched = any(v.get("connected") and not v.get("closed") for v in be.call("GET", "/api/views").get("views", []))
    except Exception:
        pass
    deadline = _t.time() + (wait_s if watched else 0)
    while True:
        ev = (be.call("GET", f"/api/runs/{_enc(jid)}/evaluation") or {}).get("evaluation")
        if ev or _t.time() >= deadline:
            break
        _t.sleep(0.5)
    if ev:
        out["evaluation"] = {k: v for k, v in ev.items() if k != "at"}
        out["evaluation_source"] = "the Evaluation panel of the run's page in the person's browser"
    else:
        out["performance"] = run["performance"]
        out["evaluation_source"] = ("the evaluator's report: no page showed this run" +
                                    ("" if watched else " (no Studio is open)"))
    return out


# ---------------------------------------------------------------------- the table

FEATURES: tuple[Feature, ...] = (
    Feature(
        "qccd_get_context",
        "Bootstrap and refresh: the person's designs (by the names they gave them) and the "
        "leaderboards (boards, by title) any design can be submitted to, the revision, protected entities, Studio "
        "view and selection, unread prompts (reading them acknowledges them), jobs, latest local result. `since` "
        "(an event cursor from a previous call) adds what changed since.",
        _obj({"since": _I, "detail": {"type": "string", "enum": ["summary", "full"]}, "branch": _S}),
        lambda be, a: be.call("GET", "/api/context?" + _q({"since": a.get("since"), "detail": a.get("detail"),
                                                           "branch": a.get("branch")})),
        ("GET /api/context",), read_only=True, group="every turn"),
    Feature(
        "qccd_read_reference",
        "Version-matched reference for this QCCD and its evaluator: sections index, "
        "design (READ FIRST to design a device -- any shape the person describes -- or to put one on a board: "
        "the structure the board's circuit needs, how a shape becomes it, measured sizes, run-and-fix), "
        "boards (every leaderboard a design can be submitted to: title, circuit, what is ranked; query=a title for "
        "one board with its circuit), interface (everything you may do on a page: places, controls, Studio verbs; nothing "
        "else is allowed), site (the website's pages, the course, how to do things on it), site:studio (every "
        "Studio control in its own words), task (= boards), operations, rules, evaluator, program, anchors, workflow, "
        "docs:adl, docs:rules, docs:tsir, docs:phys. Optional `query` returns the window around a match "
        "(for site: the matching pages and controls).",
        _obj({"section": _S, "query": _S}, ["section"]),
        lambda be, a: be.call("GET", "/api/reference?" + _q({"section": a.get("section"), "query": a.get("query")})),
        ("GET /api/reference",), read_only=True, cli=("boards", "releases"), group="every turn"),
    Feature(
        "qccd_query_design",
        "Inspect entities (node:<id>, segment:<id>, loop:<id>, zone:<name>, block:<name>) "
        "at the head revision; `kind` filters; include_operations lists the operation signatures.",
        _obj({"keys": {"type": "array", "items": _S}, "kind": _S, "branch": _S, "limit": _I,
              "include_operations": {"type": "boolean"}}),
        lambda be, a: be.call("GET", "/api/query?" + _q({
            "keys": ",".join(a.get("keys") or []) or None, "kind": a.get("kind"), "branch": a.get("branch"),
            "limit": a.get("limit"), "operations": "1" if a.get("include_operations") else None})),
        ("GET /api/query",), read_only=True),
    Feature(
        "qccd_apply_change_set",
        "Preview (default) or apply one transactional change set of semantic "
        "operations (see qccd_read_reference section=operations). Requires expected_revision (from "
        "qccd_get_context) and a unique request_id (a retry with the same id returns the same result). "
        "branch = the design, by its name; without one, the design the person's Studio shows (the current "
        "design; expected_revision is that design's revision). mode='apply' when confident.",
        _obj({"expected_revision": _I, "request_id": _S, "operations": {"type": "array", "items": {"type": "object"}},
              "mode": {"type": "string", "enum": ["preview", "apply"]}, "branch": _S, "summary": _S,
              "origin_prompt_id": _S, "rebase": {"type": "string", "enum": ["never", "if_disjoint"]}},
             ["expected_revision", "request_id", "operations"]),
        _apply_change_set, ("POST /api/change-sets", "GET /api/context")),
    Feature(
        "qccd_undo_change_set",
        "Undo one change set as a NEW validated change (never a rollback of later "
        "work); reports a conflict when later changes depend on it.",
        _obj({"change_set_id": _S, "request_id": _S, "expected_revision": _I}, ["change_set_id"]),
        lambda be, a: be.call("POST", f"/api/change-sets/{_enc(a['change_set_id'])}/undo",
                              {"request_id": a.get("request_id"), "expected_revision": a.get("expected_revision")}),
        ("POST /api/change-sets/{cs_id}/undo",)),
    Feature(
        "qccd_manage_branch",
        "The person's designs, named however they like: action create (title: the new "
        "design's name, any text; source = the design to copy, default the main design), rename (design, title), "
        "compare (a, b), adopt (candidate, expected_revision, request_id: bring a design into the main one), "
        "discard (candidate), list. Every design argument takes the design's name.",
        _obj({"action": {"type": "string", "enum": ["create", "rename", "compare", "adopt", "discard", "list"]},
              "title": _S, "design": _S, "name": _S, "source": _S, "a": _S, "b": _S, "candidate": _S,
              "expected_revision": _I, "request_id": _S, "note": _S, "origin_prompt_id": _S,
              "mode": {"type": "string", "enum": ["preview", "apply"]}},
             ["action"]),
        _manage_branch, ("GET /api/branches", "POST /api/branches", "POST /api/branches/rename", "GET /api/compare",
                         "POST /api/branches/adopt", "POST /api/branches/discard")),
    Feature(
        "qccd_manage_comment",
        "Prompt threads: action get (prompt_id: the prompt, its versions and frozen "
        "context snapshot; acknowledges it), list, reply (prompt_id, text, optional work_state "
        "working|waiting_input|ready_for_review|resolved, links), ack (prompt_ids). Replies never "
        "trigger another agent turn.",
        _obj({"action": {"type": "string", "enum": ["get", "list", "reply", "ack", "state"]}, "prompt_id": _S,
              "prompt_ids": {"type": "array", "items": _S}, "text": _S, "work_state": _S,
              "links": {"type": "object"}, "request_id": _S, "state": _S}, ["action"]),
        _manage_comment, ("GET /api/prompts", "GET /api/prompts/{pid}", "POST /api/prompts/{pid}/reply",
                          "POST /api/prompts/ack", "POST /api/prompts/{pid}/state"), group="every turn"),
    Feature(
        "qccd_start_job",
        "Start a job and return its id at once: kind compile (the real compiler on a board's "
        "circuit for a revision of a design; result carries adopt_with for set_final_program) or evaluate (profile "
        "draft|reference on a frozen snapshot of the revision). board: its title. To submit a design, use "
        "qccd_submit_local instead: it does all of this in one go.",
        _obj({"kind": {"type": "string", "enum": ["compile", "evaluate"]}, "branch": _S, "revision": _I, "board": _S,
              "profile": {"type": "string", "enum": ["draft", "reference"]},
              "compiler": {"type": "string", "enum": ["auto", "compile", "rotate"]}, "request_id": _S,
              "origin_prompt_id": _S}, ["kind"]),
        lambda be, a: be.call("POST", "/api/jobs", {
            "kind": a["kind"], "params": {k: a[k] for k in ("branch", "revision", "profile", "compiler", "board")
                                          if k in a},
            "request_id": a.get("request_id"), "origin_prompt_id": a.get("origin_prompt_id")}),
        ("POST /api/jobs",), cli=("compile", "validate"), job_kinds=("compile", "evaluate"), group="jobs"),
    Feature(
        "qccd_get_job",
        "Progress and lifecycle state of a job. wait_s (up to 50) waits that long for it to "
        "finish before answering: follow a long job with a few waiting calls, not many quick ones. On a "
        "submission's job it waits through the compile AND the grade, and returns the verdict as `grade`.",
        _obj({"job_id": _S, "wait_s": _I}, ["job_id"]),
        _get_job, ("GET /api/jobs/{jid}", "GET /api/views", "GET /api/runs/{run_id}/evaluation"),
        read_only=True, group="jobs"),
    Feature(
        "qccd_list_programs",
        "The programs a run can use, with qubit counts: the BB [[144,12,12]] syndrome "
        "round (bb144_esm; 'bb', 'bb code' and 'gross' also work), surface17_esm, steane_esm, rep9_esm, ghz4, "
        "ghz16, qft8, adder3, bv6, bell2, micro. qccd_run_program also takes your own OpenQASM 2.0.",
        _obj({}),
        lambda be, a: be.call("GET", "/api/programs"),
        ("GET /api/programs",), read_only=True, group="runs"),
    Feature(
        "qccd_run_program",
        "Compile a program onto a design with the real compiler, insert cooling, replay "
        "it under the cost model, and show it on the person's run page, whose Evaluation panel counts the "
        "round's time up as the animation plays and ends at the result. Returns `evaluation`: the numbers that "
        "panel shows (round time, time by category, steps, two-qubit gates, ions moved, heating, rules, "
        "bottlenecks) -- report THOSE, they are what the person sees -- and, when no page showed the run, the "
        "evaluator's `performance` report instead. animate (default true): the page plays the run; false opens "
        "it at its result with no animation -- use it when the person wants designs tried as fast as possible "
        "or runs many. program: a catalogue name or {name, qasm}. draft: 'main' (the working design, default) "
        "or a draft's name. Waits up to wait_s (default 45, max 50); if the run is still going, poll "
        "qccd_get_job(run_id). A run is an experiment: not Lean-checked, never a submission.",
        _obj({"program": {"anyOf": [_S, {"type": "object", "properties": {"name": _S, "qasm": _S},
                                         "required": ["qasm"]}]},
              "draft": _S, "revision": _I, "compiler": {"type": "string", "enum": ["auto", "rotate", "compile"]},
              "animate": {"type": "boolean"}, "wait_s": _I, "request_id": _S, "origin_prompt_id": _S}, ["program"]),
        _run_program, ("POST /api/jobs", "GET /api/jobs/{jid}", "GET /api/views",
                       "GET /api/runs/{run_id}/evaluation"), job_kinds=("run",), group="runs"),
    Feature(
        "qccd_compare_runs",
        "Two finished runs side by side: round time, time by category, counts, heating, "
        "B minus A, a verdict, and each run's bottlenecks. show (default true) also opens the side-by-side "
        "view in the person's Studio, where both designs animate on one shared clock.",
        _obj({"run_ids": {"type": "array", "items": _S, "minItems": 2, "maxItems": 2},
              "show": {"type": "boolean"}, "note": _S}, ["run_ids"]),
        _compare_runs, ("GET /api/compare-runs", "POST /api/present"), group="runs"),
    Feature(
        "qccd_page_read",
        "Read the page the person has open with the chat (a qccd.academy page through the "
        "workspace, or Studio): url, title, kind, headings (with refs), the main text (or one section: "
        "`section` = a heading's ref or words from it; long text is paged with offset), the visible controls "
        "(ref, kind, label, value, href), embedded examples (frame refs with their animation step), the text "
        "the person selected, and on studio pages the transport and the lessons. view_id picks another open "
        "page (qccd_get_context lists them); default: the page the current request came from.",
        _obj({"section": _S, "offset": _I, "max_chars": _I, "controls": {"type": "boolean"}, "view_id": _S}),
        lambda be, a: be.call("POST", "/api/page-actions", {
            "action": "read", "args": {k: a[k] for k in ("section", "offset", "max_chars", "controls") if k in a},
            "view_id": a.get("view_id")}, timeout=45),
        ("POST /api/page-actions",), group="pages"),
    Feature(
        "qccd_page_act",
        "Operate the person's page, visibly (a cursor with your name moves there first): action "
        "highlight (target, note: a short caption shown beside it), scroll (target, to: top|bottom, or by: pixels "
        "or 'page'/'-page'), wait (ms up to 10000, optional note), studio (verb + args: one verb the page's Studio "
        "declares for agents, e.g. addSite [x, y], addSegment [a, b], lessonLoad, lessonCheck, lessonHint, "
        "lessonSolution, testDrive; verb 'verbs' lists the declared ones with what each does; frame for an "
        "embedded example), comments (the site's comment threads on this page, as the signed-in person sees "
        "them), comment (target, text: a new thread pinned there, posted AS THE PERSON and signed 'via <you>'), "
        "reply (thread, text), resolve (thread, resolved), click (target), fill (target, value), press "
        "(key: Enter|Escape|ArrowLeft|ArrowRight|ArrowUp|ArrowDown|Tab|Space|Home|End|PageUp|PageDown; a key "
        "that acts needs a target), navigate (path: a place -- the site map, a link on the page, or /studio; "
        "it returns once the new page is up, in `arrived`), "
        "step (an animation: step=N to seek, delta=+1/-1, play=true, pause=true; target {frame: 'fN'} for an "
        "embedded example), open_lesson (lesson id, on the site's Studio: go there with navigate path='/web/studio.html#learn=<id>', which opens the lesson; never on /studio, the person's own design). target = {ref} from qccd_page_read, "
        "{selector}, or {text}; add frame to reach into an embedded example. ONLY DECLARED: click, fill and "
        "press operate declared controls (a read marks the others undeclared), navigate goes only to places; "
        "a refusal lists what is declared instead. The chat is out of reach, and links that leave the site "
        "are refused (give the person the link instead).",
        _obj({"action": {"type": "string", "enum": ["highlight", "scroll", "click", "fill", "press", "navigate",
                                                    "step", "open_lesson", "studio", "wait", "comments", "comment",
                                                    "reply", "resolve"]},
              "text": _S, "thread": _I, "resolved": {"type": "boolean"},
              "verb": _S, "args": {"type": "array"}, "ms": _I, "by": {"anyOf": [_I, _S]}, "frame": _S,
              "target": {"type": "object", "properties": {"ref": _S, "selector": _S, "text": _S, "frame": _S}},
              "note": _S, "value": _S, "key": _S, "path": _S, "to": {"type": "string", "enum": ["top", "bottom"]},
              "step": _I, "delta": _I, "play": {"type": "boolean"}, "pause": {"type": "boolean"}, "lesson": _S,
              "view_id": _S}, ["action"]),
        lambda be, a: be.call("POST", "/api/page-actions", {
            "action": a["action"],
            "args": {k: a[k] for k in ("target", "note", "value", "key", "path", "to", "step", "delta", "play",
                                       "pause", "lesson", "verb", "args", "ms", "by", "frame", "text", "thread",
                                       "resolved") if k in a},
            "view_id": a.get("view_id")}, timeout=90),
        ("POST /api/page-actions",), group="pages"),
    Feature(
        "qccd_cancel_job",
        "Request cancellation; reports what actually happened.",
        _obj({"job_id": _S}, ["job_id"]),
        lambda be, a: be.call("POST", f"/api/jobs/{_enc(a['job_id'])}/cancel", {}),
        ("POST /api/jobs/{jid}/cancel",), group="jobs"),
    Feature(
        "qccd_inspect_run",
        "A local submission's report in bounded parts: part summary|stages|diagnostics|"
        "metrics; diagnostics are paginated with offset/limit.",
        _obj({"submission_id": _S, "part": {"type": "string", "enum": ["summary", "stages", "diagnostics", "metrics"]},
              "offset": _I, "limit": _I}, ["submission_id"]),
        _inspect_run, ("GET /api/submissions/{sub}",), read_only=True, group="boards"),
    Feature(
        "qccd_present",
        "Ask Studio to show something: action highlight|select (target.keys), open_prompt "
        "(target.prompt_id), select_frame (target.frame), open_result (target.submission_id), "
        "reveal_diagnostic, open_run (target.run_id: a run's animation), compare (target.runs: two run ids; "
        "qccd_compare_runs does this for you), open_branch (target.branch: a design's name or id -- the "
        "person's Studio switches to that design, so what you draw next lands on it where they watch). Studio "
        "honours the user's Follow-agent setting.",
        _obj({"action": _S, "target": {"type": "object"}, "view_id": _S, "note": _S}, ["action", "target"]),
        lambda be, a: be.call("POST", "/api/present", {"action": a["action"], "target": a.get("target") or {},
                                                       "view_id": a.get("view_id"), "note": a.get("note", "")}),
        ("POST /api/present",), group="pages"),
    Feature(
        "qccd_submit_local",
        "Submit a design to a leaderboard: design (its name; default the main design) and "
        "board (its title, e.g. 'BB [[144,12,12]]' or 'surface code'). In one job it compiles the board's circuit "
        "onto the design with the real compiler, adopts that program, freezes the design and grades it with the "
        "reference evaluator (rules, the Lean certificate, semantics, metrics); returns the job at once, and the "
        "graded submission appears on the local leaderboard ('Local result - not published'). It usually "
        "finishes within a minute: follow it ONCE with qccd_get_job(job_id, wait_s=50), which waits through the "
        "grade and returns the verdict; if it is still running then, say the card in their chat will show the "
        "verdict, and stop. Publishing it needs the person's approval (qccd_prepare_publish shows what would be "
        "uploaded).",
        _obj({"design": _S, "board": _S, "profile": {"type": "string", "enum": ["draft", "reference"]},
              "request_id": _S, "origin_prompt_id": _S, "title": _S, "branch": _S, "revision": _I}),
        _submit_local, ("POST /api/submissions",), cli=("submit",), job_kinds=("submit",), group="boards"),
    Feature(
        "qccd_prepare_publish",
        "Show exactly what publication would upload (bundle digest, files, local "
        "report). It creates nothing: approval and upload need the person.",
        _obj({"submission_id": _S, "visibility": {"type": "string", "enum": ["public", "unlisted", "private"]}},
             ["submission_id"]),
        lambda be, a: be.call("POST", "/api/publish/prepare", {"submission_id": a["submission_id"],
                                                               "visibility": a.get("visibility", "public")}),
        ("POST /api/publish/prepare",), read_only=True, cli=("publish",), group="boards"),
    Feature(
        "qccd_import_file",
        "Import a design file written outside Studio (e.g. design/studio.json edited by "
        "an optimizer) as a validated change set; stale files are refused, never overwrite newer edits.",
        _obj({"path": _S, "request_id": _S, "mode": {"type": "string", "enum": ["preview", "apply"]}}, ["path"]),
        lambda be, a: be.call("POST", "/api/import", {"path": a["path"], "request_id": a.get("request_id"),
                                                      "mode": a.get("mode", "apply")}),
        ("POST /api/import",), cli=("import",)),
)

BY_NAME: dict[str, Feature] = {f.name: f for f in FEATURES}


#: Routes an agent's token can reach that no tool calls, and why that is right.  A new route
#: must be called by a Feature or appear here: the gate test fails otherwise.
INTERNAL_ROUTES: dict[str, str] = {
    "GET /api/whoami": "the Studio page asks who it is talking as",
    "GET /api/design": "the Studio page loads the design it draws; agents read it through qccd_get_context and qccd_query_design",
    "GET /api/change-sets/{cs_id}": "the Studio's history panel; a change set's result is returned by qccd_apply_change_set",
    "GET /api/history": "the Studio's history panel",
    "GET /api/boards": "the Studio's board picker; agents read boards through qccd_get_context and section 'boards'",
    "GET /api/conversation": "the chat panel's transcript",
    "GET /api/runs": "the Studio's run list; agents follow their own runs by id",
    "GET /api/runs/{run_id}": "the run page loads its run",
    "GET /api/runs/{run_id}/program": "the run page loads its program",
    "GET /api/agents": "the Studio's agent picker",
    "GET /api/agents/models": "the Studio's model picker",
    "GET /api/traces": "the trace viewer (a person reads what the agent did)",
    "GET /api/traces/{sid}": "the trace viewer",
    "GET /api/traces/{sid}/program": "the trace viewer",
    "GET /api/design-graph": "the Studio's design graph panel",
    "POST /api/trace": "the MCP adapter records each tool call in the session's trace",
    "GET /api/web": "the website mirror's own status",
    "GET /api/pages": "the website mirror's open pages; agents see them in qccd_get_context",
    "GET /api/artifacts/{digest}": "pages load stored artifacts by digest",
    "POST /api/sessions": "the MCP adapter registers its session",
    "GET /api/sessions": "the MCP adapter finds the Codex app-server session it belongs to",
    "POST /api/sessions/{sid}/status": "an agent runtime reports its session's status",
    "GET /api/context-snapshots/{cid}": "a prompt's frozen context, returned inside qccd_manage_comment(get)",
    "POST /api/deliveries/claim": "the MCP adapter's channel loop claims prompts to push",
    "POST /api/deliveries/{did}/mark": "the MCP adapter's channel loop marks a push",
    "GET /api/jobs": "the Studio's job list; agents follow their own jobs by id",
    "GET /api/submissions": "the Studio's submission list; agents inspect one by id",
    "GET /api/leaderboard": "the local leaderboard page and `qccd leaderboard`",
    "POST /api/pair-code": "pairing a browser with the workspace",
    "POST /api/shutdown": "`qccd stop`",
}

#: CLI verbs with no tool behind them, and why: they run before a service exists, manage the
#: service itself, open something for the person, or ARE the agent's side of the link.
LOCAL_VERBS: dict[str, str] = {
    "init": "creates the workspace; there is no service yet",
    "studio": "opens the person's browser on the Studio",
    "web": "opens the person's browser on the website through the workspace",
    "toolchain": "installs the compiler and checker binaries",
    "serve": "starts the service the tools talk to",
    "status": "the service's own status",
    "stop": "stops the service",
    "agent": "installs, inspects or removes an agent client's configuration",
    "mcp": "is the MCP server itself",
    "trace": "a person reads what an agent did",
    "leaderboard": "the local leaderboard as a table at the terminal",
}


# ---------------------------------------------------------------------- generated for the skill

def tools_json() -> list[dict]:
    return [{"name": f.name, "group": f.group, "reads_only": f.read_only, "about": f.about,
             "cli": [f"qccd {v}" for v in f.cli], "starts_jobs": list(f.job_kinds),
             "arguments": sorted((f.schema.get("properties") or {}).keys()),
             "required": list(f.schema.get("required") or [])} for f in FEATURES]


def tools_markdown() -> str:
    """The skill's `references/tools.md`: every tool of this build, grouped, in one table each."""
    groups: dict[str, list[Feature]] = {}
    for f in FEATURES:
        groups.setdefault(f.group, []).append(f)
    out = ["# Every QCCD tool", "",
           "Generated from the code's own table (`qccd/workspace/features.py`) when the skill is built: this is",
           "the complete list, and nothing here is kept by hand.", ""]
    for g, fs in groups.items():
        out += [f"## {g[0].upper() + g[1:]}", "", "| tool | reads or acts | same as | what it does |",
                "|---|---|---|---|"]
        for f in fs:
            same = ", ".join(f"`qccd {v}`" for v in f.cli) or "–"
            about = f.about.replace("|", "\\|").replace("\n", " ")
            out.append(f"| `{f.name}` | {'reads' if f.read_only else 'acts'} | {same} | {about} |")
        out.append("")
    return "\n".join(out)
