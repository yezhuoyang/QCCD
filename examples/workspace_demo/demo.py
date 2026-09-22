"""The small-task co-design demonstration, end to end.

    python examples/workspace_demo/demo.py [--agent scripted|codex] [--keep]

Every part is the real system -- the workspace service, the stock Studio page with the live
layer in headless Chrome, the MCP adapter, the compiler, the Lean checker, the evaluator and
a development instance of the official service -- except, with `--agent scripted`, the
agent's DECISIONS: a deterministic stand-in (clearly not an AI model) reads each Studio
prompt through the MCP tools and answers it with a fixed policy.  `--agent codex` hands the
same prompts to a NEW Codex thread through the app-server bridge instead (it needs Codex and
a login; its edits are the model's own, so the checks below are the same but the geometry
can differ).

The sequence (the brief's "scripted small-task demonstration"):
  1. open a local design (ghz4@1, the 4-site chain) and connect the agent session
  2. protect the gate zones C0 and C1, lasso the empty region right of C3, sketch an arrow,
     and send "Use this structure here, but preserve these gate zones."
  3. automatic delivery -> the agent extends the chain there -> anchored reply in the thread
  4. move T5 by hand, then send "Like this; apply the same arrangement to the other selected
     module." with the manual edit as a demonstration and C0 selected as the target
  5. the agent reads the NEW revision, leaves C0/C1 and the hand-moved T5 untouched, and
     builds the mirrored arrangement on the left
  6. compile, adopt, submit locally under the reference profile; inspect stages/diagnostics
  7. change the workspace again: the old local result stays byte-identical and goes stale
  8. approve and publish that exact snapshot to the development official server; compare
     its independent report with the local one
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

PROMPT_A = "Use this structure here, but preserve these gate zones."
PROMPT_B = "Like this; apply the same arrangement to the other selected module."
#: what a person answers when the agent asks which structure was meant (sent only then)
CLARIFY_A = ("Extend the existing straight chain from C3 into the lassoed region: add two trap sites "
             "(zone trap) in a line to the right of C3, connected in sequence. Keep C0 and C1 unchanged.")


# ---------------------------------------------------------------------- the scripted stand-in

class ScriptedAgent(threading.Thread):
    """NOT an AI model: a fixed policy, acting only through the QCCD MCP tools."""

    def __init__(self, root: Path):
        super().__init__(daemon=True)
        self.root = root
        self.stop = threading.Event()
        self.log: list = []
        self.error: str | None = None

    def run(self):
        try:
            asyncio.run(self._main())
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"

    async def _main(self):
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
        params = StdioServerParameters(command=sys.executable, env=env, cwd=str(self.root),
                                       args=["-m", "qccd.workspace", "mcp", "--client", "generic",
                                             "--root", str(self.root)])
        async with stdio_client(params) as (read, write, *_):
            async with ClientSession(read, write) as s:
                await s.initialize()

                async def call(name, **args):
                    r = await s.call_tool(name, args)
                    self.log.append({"tool": name, "error": bool(r.is_error),
                                     "summary": (r.content[0].text if r.content else "")[:160]})
                    if r.is_error:
                        raise RuntimeError(r.content[0].text if r.content else name)
                    return r.structured_content
                done = set()
                while not self.stop.is_set():
                    ctx = await call("qccd_get_context")
                    for u in ctx["unread_prompts"]:
                        if u["prompt_id"] in done:
                            continue
                        done.add(u["prompt_id"])
                        p = await call("qccd_manage_comment", action="get", prompt_id=u["prompt_id"])
                        await self._answer(call, p, ctx)
                    await asyncio.sleep(0.7)

    async def _answer(self, call, p, ctx):
        pid = p["prompt_id"]
        text = p["latest_sent"]["body"]["text"]
        snap = p.get("context") or {}
        rev = (await call("qccd_get_context"))["revision"]          # always the CURRENT revision
        if text.startswith("Use this structure"):
            region = next(a for a in snap["anchors"] if a["kind"] == "region")
            x0 = region["bbox"][0]
            ops = [{"type": "add_chain", "prefix": "T", "count": 2, "start": [max(4.0, round(x0 + 0.5)), 0],
                    "step": [1, 0], "zone": "trap", "attach_to": "C3"}]
            r = await call("qccd_apply_change_set", expected_revision=rev, request_id=f"a-{pid}", mode="apply",
                           origin_prompt_id=pid, summary="extend the chain into the lassoed region",
                           operations=ops)
            await call("qccd_present", action="highlight", target={"keys": ["node:T0", "node:T1"]},
                       note="the new traps")
            await call("qccd_manage_comment", action="reply", prompt_id=pid, work_state="ready_for_review",
                       text=f"Extended the chain right of C3 with traps T0, T1 (r{r['revision']}). "
                            f"C0 and C1 are protected and untouched.")
        elif text.startswith("Like this"):
            demo = snap["demonstration"]
            moved = {k: v for k, v in demo["diff"]["changed"].items() if "pos" in v}
            # the demonstrated arrangement, read from the design AFTER the demonstration
            q = await call("qccd_query_design", keys=["node:T0", "node:T1", "node:C3", "node:C0"])
            pos = {e["key"]: e["pos"] for e in q["entities"]}
            dy = pos["node:T1"][1] - pos["node:T0"][1]
            cx, cy = pos["node:C0"]
            ops = [{"type": "add_site", "id": "U0", "pos": [cx - 1, cy], "zone": "trap"},
                   {"type": "add_site", "id": "U1", "pos": [cx - 2, cy + dy], "zone": "trap"},
                   {"type": "add_segment", "a": "C0", "b": "U0"},
                   {"type": "add_segment", "a": "U0", "b": "U1"}]
            r = await call("qccd_apply_change_set", expected_revision=rev, request_id=f"b-{pid}", mode="apply",
                           origin_prompt_id=pid, summary="mirror the demonstrated arrangement onto C0",
                           operations=ops)
            await call("qccd_manage_comment", action="reply", prompt_id=pid, work_state="ready_for_review",
                       text=f"Mirrored the demonstrated arrangement ({sorted(moved)} moved by {dy:+g} in y) onto "
                            f"C0 as U0, U1 (r{r['revision']}); your hand-moved T1 and the protected C0/C1 are unchanged.")


# ---------------------------------------------------------------------- the demonstration

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", choices=["scripted", "codex"], default="scripted")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="qccd-demo-"))
    os.environ["QCCD_RUNTIME_DIR"] = str(tmp / "runtime")
    os.environ["PYTHONPATH"] = str(REPO) + os.pathsep + os.environ.get("PYTHONPATH", "")
    from qccd.workspace.app import Workspace
    from qccd.workspace.runtime import ensure_service, service_request
    root = tmp / "design"
    report: dict = {"workspace": str(root), "agent": a.agent,
                    "agent_note": "scripted stand-in: fixed policy, NOT an AI model" if a.agent == "scripted"
                    else "a NEW Codex thread via the app-server bridge"}
    Workspace.init(root, "ghz4@1").close()
    info = ensure_service(root)
    owner = _owner(root)
    # what `qccd studio --keep-alive` does in a person's terminal: a killed service comes back
    # on its port, and the open page reconnects to it
    import threading
    from qccd.workspace.runtime import keep_alive
    watch_stop = threading.Event()
    restarts: list = []
    threading.Thread(target=keep_alive, args=(root, watch_stop),
                     kwargs={"on_event": lambda k, d: restarts.append({"event": k, **(d or {})})},
                     daemon=True).start()
    report["service_restarts"] = restarts
    agent = None
    try:
        # 1. connect the agent
        if a.agent == "codex":
            con = owner("POST", "/api/sessions/codex/connect", {"start_thread": True, "approval_policy": "never",
                                                                "sandbox": "read-only", "turn_overrides": {"effort": "low"},
                                                                "label": "Codex (demo)"})
            report["codex"] = {"thread_id": con["thread_id"], "note": con["note"]}
        else:
            agent = ScriptedAgent(root)
            agent.start()
            t0 = time.time()
            while not owner("GET", "/api/sessions")["sessions"] and time.time() - t0 < 60:
                time.sleep(0.3)
        # 2-5. the Studio session, in real Chrome
        code = owner("POST", "/api/pair-code", {})["code"]
        ready = "window.QCCD_LIVE && QCCD_LIVE.state().paired && QCCD_LIVE.state().connected && QCCD_LIVE.state().rev !== null"
        thread_has_reply = ("(function(pid){return fetch('/api/prompts/'+pid).then(r=>r.json()).then(p=>"
                            "p.replies.length>0)})")
        shot = tmp / "studio.png"
        # agent-agnostic waits: a reply in THAT prompt's thread, whatever ids the agent chose
        replied = "fetch('/api/prompts/'+window.{v}).then(r=>r.json()).then(p=>p.replies.length>0)"
        grew = "Object.keys(EDITOR.state().device.nodes).length > 4"
        pick_new_rightmost = ("(function(){var ns=EDITOR.state().device.nodes, best=null, bx=-1e9;"
                              "for (var id in ns){ if (/^C[0-3]$/.test(id)) continue; var x=+QCCD.unbox(ns[id].pos[0]);"
                              " if (x>bx){bx=x; best=id;} } window.__moved=best; return best;})()")
        steps = [
            {"wait": ready, "timeout": 40000, "stopOnFail": True},
            {"eval": "QCCD_LIVE.protect(['node:C0','node:C1']); 'protected'"},
            {"wait": "QCCD_LIVE.state().protected.length === 2", "timeout": 10000},
            {"eval": "QCCD_LIVE.addLasso([[3.5,-0.8],[6.5,-0.8],[6.5,0.8],[3.5,0.8]]); "
                     "QCCD_LIVE.addSketch('arrow', [[3.2,0.6],[5.8,0.6]]); 'anchored'"},
            {"eval": f"QCCD_LIVE.send({{text: {json.dumps(PROMPT_A)}, mode: 'apply'}}).then(d => (window.__pA = d.prompt_id))"},
            {"wait": replied.format(v="__pA"), "timeout": 900000, "stopOnFail": True},
            {"sleep": 2000},
            # A PERSON ANSWERS A QUESTION: if the agent asked instead of acting, clarify with a new prompt
            {"eval": f"({grew}) ? 'acted' : (QCCD_LIVE.addLasso([[3.5,-0.8],[6.5,-0.8],[6.5,0.8],[3.5,0.8]]), "
                     f"QCCD_LIVE.send({{text: {json.dumps(CLARIFY_A)}, mode: 'apply'}}).then(d => (window.__pA2 = d.prompt_id)))"},
            {"wait": f"!window.__pA2 || ({grew})", "timeout": 900000},
            {"sleep": 2000},
            {"eval": "QCCD_LIVE.demoStart(); QCCD_LIVE.state().rev"},
            {"eval": pick_new_rightmost},
            {"eval": "(function(){var id=window.__moved; if(!id) return 'no new node'; var n=EDITOR.state().device.nodes[id];"
                     " return JSON.stringify(EDITOR.emit({method:'move_site', args:[id, +QCCD.unbox(n.pos[0]),"
                     " +QCCD.unbox(n.pos[1]) + 0.5], kwargs:{}}));})()"},
            {"wait": "!QCCD_LIVE.state().inflight && QCCD_LIVE.state().synced_edits !== null", "timeout": 10000},
            {"sleep": 1500},
            {"eval": "EDITOR.select([{kind:'site', id:'C0'}]); QCCD_LIVE.demoEnd(['node:C0']); "
                     "JSON.stringify({demo: QCCD_LIVE.state().demo, moved: window.__moved})"},
            {"eval": f"QCCD_LIVE.send({{text: {json.dumps(PROMPT_B)}, mode: 'apply'}}).then(d => (window.__pB = d.prompt_id))"},
            {"wait": replied.format(v="__pB"), "timeout": 900000},
            {"sleep": 2500},
            {"eval": "JSON.stringify({rev: QCCD_LIVE.state().rev, nodes: Object.keys(EDITOR.state().device.nodes).sort(),"
                     " moved: window.__moved, clarified: !!window.__pA2})"},
            {"shot": str(shot)},
        ]
        sp = tmp / "studio.json"
        sp.write_text(json.dumps({"pages": {"a": f"http://127.0.0.1:{info['port']}/studio#pair={code}"}, "steps": steps}))
        r = subprocess.run(["node", str(REPO / "tests" / "workspace_browser.mjs"), str(sp)], capture_output=True,
                           timeout=1500, cwd=REPO)
        b = json.loads(r.stdout.decode("utf-8"))
        report["browser"] = [{k: v for k, v in s.items() if k != "step"} for s in b["steps"]]
        report["browser_console"] = b.get("logs")
        report["screenshot"] = str(shot)
        prompts = owner("GET", "/api/prompts")["prompts"]
        threads = {p["text"]: owner("GET", f"/api/prompts/{p['prompt_id']}") for p in prompts}
        report["threads"] = {t: {"delivery": [d["state"] for d in p["deliveries"]], "work": (p["work"] or {}).get("state"),
                                 "replies": [x["body"]["text"] for x in p["replies"]],
                                 "links": [(l["kind"], l["ref"]) for l in p["links"]],
                                 "context_revision": (p.get("context") or {}).get("design_revision"),
                                 "demonstration": bool((p.get("context") or {}).get("demonstration"))}
                             for t, p in threads.items()}
        hist = owner("GET", "/api/history?limit=50")["history"]
        report["history"] = [(h["revision"], h["actor"]["kind"], h["summary"]) for h in reversed(hist)]
        # 5. the invariants: protected untouched, the manual adjustment kept
        design = owner("GET", "/api/design")
        final = json.loads(b["steps"][-2].get("value") or "{}")
        moved = final.get("moved")
        report["moved_by_hand"] = moved
        report["clarification_sent"] = final.get("clarified")
        keys = ",".join(["node:C0", "node:C1"] + ([f"node:{moved}"] if moved else []))
        ents = owner("GET", f"/api/query?keys={keys}")["entities"]
        report["after_agent"] = {e["key"]: e["pos"] for e in ents}
        # the hand-made position survived the agent's second change
        hand = [h for h in reversed(hist) if h["actor"]["kind"] == "human" and "Studio" in (h["summary"] or "")]
        report["manual_edit_revision"] = hand[-1]["revision"] if hand else None
        agent_cs = [h for h in hist if h["actor"]["kind"] == "agent"]
        report["agent_touched_protected"] = sorted({k for h in agent_cs for k in h["touched"]
                                                    if k in ("node:C0", "node:C1")})
        # 6. compile, adopt, submit locally
        def wait(jid):
            while True:
                j = owner("GET", f"/api/jobs/{jid}")
                if j["status"] in ("succeeded", "failed", "cancelled", "timeout", "internal_error"):
                    return j
                time.sleep(0.5)
        j = wait(owner("POST", "/api/jobs", {"kind": "compile", "params": {}})["job_id"])
        report["compile"] = {"status": j["status"], "summary": (j.get("result") or {}).get("summary")}
        if j["status"] == "succeeded":
            rev = owner("GET", "/api/context")["revision"]
            owner("POST", "/api/change-sets", {"expected_revision": rev, "request_id": "adopt", "mode": "apply",
                                               "summary": "adopt the compiled program",
                                               "operations": [j["result"]["adopt_with"]]})
            sub = owner("POST", "/api/submissions", {"profile": "reference"})
            wait(sub["job_id"])
            s1 = owner("GET", f"/api/submissions/{sub['submission_id']}")
            rep = s1["report"]
            report["local_result"] = {"label": s1["label"], "revision": s1["snapshot"]["revision"],
                                      "eligible": rep["eligibility"]["eligible"],
                                      "stages": {st["id"]: st["status"] for st in rep["stages"]},
                                      "metrics": {k: v["value"] for k, v in rep["metrics"].items()},
                                      "diagnostics": [d["id"] for d in rep["diagnostics"]][:10]}
            # 7. change the workspace again: the old result is immutable and stale
            rev = owner("GET", "/api/context")["revision"]
            owner("POST", "/api/change-sets", {"expected_revision": rev, "request_id": "later", "mode": "apply",
                                               "operations": [{"type": "move_site", "id": "C3", "pos": [3, 0.25]}]})
            s2 = owner("GET", f"/api/submissions/{sub['submission_id']}")
            report["after_later_edit"] = {"stale": s2["stale"], "report_unchanged": s2["report"] == rep}
            # 8. approve + publish the exact snapshot to a development official server
            report["official"] = _publish(root, info, sub["submission_id"], rep, tmp)
    finally:
        watch_stop.set()                  # before the deliberate shutdown below
        if agent:
            agent.stop.set()
            report["scripted_agent_calls"] = agent.log[-12:]
            if agent.error:
                report["scripted_agent_error"] = agent.error
        try:
            owner("POST", "/api/shutdown", {})
        except Exception:
            pass
    print(json.dumps(report, indent=1, default=str))
    return 0


def _owner(root: Path):
    """The terminal's calls, as the CLI makes them: if the service is gone (killed, restarted),
    find or start it again and repeat a refused call once -- a restarted service keeps its port and
    its data, so the page and this script carry on."""
    from qccd.workspace.runtime import ensure_service, service_request

    import urllib.error

    def call(m, p, b=None):
        try:
            return service_request(ensure_service(root), m, p, b, token="owner", timeout=120)
        except urllib.error.URLError as exc:
            # only a REFUSED connection is repeated: nothing reached the service.  A reset or
            # a timeout may have been applied already, and is reported rather than retried.
            if not isinstance(exc.reason, ConnectionRefusedError):
                raise
            time.sleep(1.0)
            return service_request(ensure_service(root), m, p, b, token="owner", timeout=120)
    return call


def _publish(root: Path, info: dict, sub_id: str, local_report: dict, tmp: Path) -> dict:
    import uvicorn
    from qccd.official.service import OfficialService, create_app
    from qccd.official.worker import process_one
    from qccd.workspace.publish import compare_reports, upload_approved
    from qccd.workspace.runtime import free_port, service_request
    from qccd.workspace.tasks import RELEASES_DIR
    svc = OfficialService(f"sqlite:///{tmp / 'official.db'}", RELEASES_DIR, tmp / "official-artifacts")
    token = svc.create_uploader("demo")
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(svc), host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    time.sleep(1.5)
    owner = _owner(root)
    rv = owner("POST", "/api/publish/prepare", {"submission_id": sub_id, "visibility": "public"})
    # the person approves THIS digest (a terminal would ask them to type it; the demo is that person)
    ap = owner("POST", "/api/publish/approve", {"submission_id": sub_id, "bundle_digest": rv["bundle_digest"],
                                                "params": {"visibility": "public"}, "interactive_confirmation": True})
    os.environ["QCCD_UPLOAD_TOKEN"] = token
    # what `qccd publish --approval ... --server ...` does at a terminal
    up = upload_approved(root, ap["approval_id"], f"http://127.0.0.1:{port}")
    process_one(svc, tmp / "spool", inline=True, releases=RELEASES_DIR)
    server_report = svc.report(up["server"]["submission_id"], None)
    server.should_exit = True
    parity = compare_reports(local_report, server_report)
    return {"bundle_digest": rv["bundle_digest"], "approval": ap["approval_id"], "server_submission": up["server"],
            "server_eligible": server_report["eligibility"]["eligible"], "parity": {k: v for k, v in parity.items()
                                                                                   if not k.startswith("toolchain")},
            "leaderboard": [r["id"] for r in svc.leaderboard("ghz4@1")["rows"]]}


if __name__ == "__main__":
    sys.exit(main())
