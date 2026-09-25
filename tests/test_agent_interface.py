"""THE GATE: a feature that an agent cannot read and operate does not go in.

qccd/workspace/interface.py states the rule and docs/agent-interface.md says how to follow it.
An agent may do on a page only what the code declares; these tests fail when code and
declarations drift apart:

* every function of the Studio's API (window.EDITOR in qccd/viz/js/editor.js) has an entry in
  qccd/viz/js/editor_api.json, and every entry names a function that exists;
* the page actions agree wherever they are declared (the service, web/pageact.js, the MCP tool);
* nothing the agent is told recommends a Studio function that is not declared for agents;
* the out-of-line declarations only shrink: a new control is declared inline, with its code;
* in the Studio as it renders, every control is declared, every data-hint key is described,
  and no declaration names a control that is gone.

A failure message says which thing is undeclared and where its declaration goes.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from qccd.workspace import interface as iface

REPO = Path(__file__).resolve().parents[1]
CHROME = os.environ.get("CHROME") or "C:/Program Files/Google/Chrome/Application/chrome.exe"
needs_chrome = pytest.mark.skipif(not (shutil.which("node") and Path(CHROME).exists()), reason="needs node and Chrome")

#: the out-of-line list's length (11 when the gate was introduced, 2026-09-24; 10 once the site's
#: search box was declared inline the same day).  It may go DOWN -- move an entry inline (data-hint
#: + a HINTS entry) at a site regeneration and lower this -- never up.
OUT_OF_LINE_PINNED = 10
USES = {"agent", "harness", "never"}
KINDS = {"read", "view", "design", "program", "course"}


def test_every_studio_function_is_declared():
    keys = iface.api_keys(iface.EDITOR_JS.read_text(encoding="utf-8"))
    reg = iface.editor_api()
    missing = [k for k in keys if k not in reg]
    gone = [k for k in reg if k not in keys]
    assert not missing, (f"window.EDITOR has functions with no declaration: {missing}. Add each to "
                         "qccd/viz/js/editor_api.json: use (agent|harness|never), what it does, and for an agent "
                         "verb its kind (read|view|design|program|course) and args.")
    assert not gone, f"qccd/viz/js/editor_api.json declares functions window.EDITOR no longer has: {gone}"
    bad = [k for k, v in reg.items() if v.get("use") not in USES or not str(v.get("does") or "").strip()
           or (v["use"] == "agent" and v.get("kind") not in KINDS)]
    assert not bad, f"these declarations are incomplete (use, does, and kind for agent verbs): {bad}"


def test_the_gate_reads_the_api_as_written():
    # top-level keys only: a nested object literal, a string or a comment holding "x:" is not a key
    src = ("var API = {\n  // a: comment\n  mode: function () { return { kind: 1, len: 2 }; }, setMode: setMode,\n"
           "  s: 'q: 1', t: [ { u: 1 } ], /* v: 2 */ w: w\n};")
    assert iface.api_keys(src) == ["mode", "setMode", "s", "t", "w"]


def test_the_page_actions_agree_wherever_they_are_declared():
    from qccd.workspace.mcp_server import _tools
    from qccd.workspace.service import PAGE_ACTIONS
    src = (REPO / "qccd" / "workspace" / "web" / "pageact.js").read_text(encoding="utf-8")
    body = src[src.index("function act(action, args, who)"):src.index("function safe(action")]
    implemented = set(re.findall(r"case '(\w+)':", body))
    tool = next(schema for name, _, schema in _tools() if name == "qccd_page_act")
    offered = set(tool["properties"]["action"]["enum"]) | {"read"}          # read is qccd_page_read
    assert set(PAGE_ACTIONS) == implemented, (f"service.PAGE_ACTIONS {sorted(PAGE_ACTIONS)} and pageact.js "
                                              f"act() {sorted(implemented)} differ")
    assert offered == implemented, f"the MCP tool offers {sorted(offered)}, the page implements {sorted(implemented)}"
    skill = (REPO / "qccd" / "workspace" / "skill" / "SKILL.md").read_text(encoding="utf-8")
    unmentioned = [a for a in PAGE_ACTIONS if not re.search(r"\b" + a + r"\b", skill)]
    assert not unmentioned, f"the skill never mentions these page actions: {unmentioned}"


def test_nothing_the_agent_is_told_recommends_an_undeclared_studio_function():
    from qccd.workspace.mcp_server import INSTRUCTIONS, _tools
    from qccd.workspace.site_guide import HOW_TO
    reg = iface.editor_api()
    # camelCase names cannot be ordinary words; `emit` is the one lowercase name that was offered
    watch = [k for k, v in reg.items() if v["use"] != "agent" and (re.search(r"[A-Z]", k) or k == "emit")]
    texts = {"MCP instructions": INSTRUCTIONS,
             "the skill": (REPO / "qccd" / "workspace" / "skill" / "SKILL.md").read_text(encoding="utf-8"),
             "the design guide": (REPO / "qccd" / "workspace" / "skill" / "references" / "design.md").read_text(encoding="utf-8"),
             "the site guide's how-to": json.dumps(HOW_TO)}
    texts.update({f"tool {n}": d for n, d, _ in _tools()})
    found = [(where, k) for where, t in texts.items() for k in watch if re.search(r"\b" + k + r"\b", t)]
    assert not found, (f"agent-facing text names Studio functions that are not declared for agents: {found}. "
                       "Recommend a declared agent verb, or declare the function for agents in editor_api.json.")


def test_the_out_of_line_list_only_shrinks():
    n = len(iface.OUT_OF_LINE)
    assert n <= OUT_OF_LINE_PINNED, (
        f"interface.OUT_OF_LINE grew to {n} (pinned at {OUT_OF_LINE_PINNED}). A new control is declared INLINE, "
        "with the code that makes it: data-hint=\"<key>\" on the element and an entry for the key in the page's "
        "hint table (the Studio's HINTS in editor.js; a site page's window.QCCD_HINTS).")
    for sel, d in iface.OUT_OF_LINE.items():
        assert d.get("on") in ("studio", "site") and d.get("name") and d.get("does"), sel


def test_the_contract_the_pages_carry():
    c = iface.page_contract()
    reg = iface.editor_api()
    assert set(c["verbs"]) == {k for k, v in reg.items() if v["use"] == "agent"}
    assert c["not_for_agents"]["saveProject"] == "never" and c["not_for_agents"]["begin"] == "harness"
    assert "emit" not in c["verbs"] and "addSite" in c["verbs"] and c["verbs"]["addSite"]["kind"] == "design"
    m = iface.manifest()
    assert set(m["page_actions"]) and m["studio_verbs"]["lessonCheck"]["kind"] == "read"
    assert iface.places([{"u": "rules/#R7"}, {"u": "rules/#R8"}, {"u": "studio.html#learn=A1"}]) == [
        "/web/rules/", "/web/studio.html"]


@needs_chrome
def test_every_control_in_the_studio_is_declared(tmp_path, monkeypatch):
    """The Studio as it renders (every panel's controls are in the page, open or not)."""
    from qccd.workspace.app import Workspace
    from qccd.workspace.runtime import ensure_service, service_request
    monkeypatch.setenv("QCCD_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("QCCD_CODEX", "none")
    monkeypatch.setenv("QCCD_CLAUDE", "none")
    Workspace.init(tmp_path / "ws", "ghz4@1").close()
    info = ensure_service(tmp_path / "ws", python=sys.executable)
    try:
        code = service_request(info, "POST", "/api/pair-code", {}, token="owner")["code"]
        studio_sel = [s for s, d in iface.OUT_OF_LINE.items() if d["on"] == "studio"] + list(iface.DECLARED_FORMS)
        probe = ("(function(){ var PRIV = '#qcl-dock, #qcl-menu, .qcl-modal, #qcl-notice, #qcl-unpaired, #qccd-chat, "
                 "[data-qccd-private], #qccd-agent-cursor, #qcl-runpanel';"
                 "var nohint = []; document.querySelectorAll('[data-hint]').forEach(function(e){ if (e.closest(PRIV)) return;"
                 " var k = e.getAttribute('data-hint'); if (!EDITOR.hintFor(k) && nohint.indexOf(k) < 0) nohint.push(k); });"
                 "var stale = " + json.dumps(studio_sel) + ".filter(function(s){ return !document.querySelector(s); });"
                 # the gate bites: a control added without a declaration is reported
                 "var b = document.createElement('button'); b.id = 'gateProbe'; b.textContent = 'New feature';"
                 "document.getElementById('rail').appendChild(b); var caught = QCCD_PAGE.undeclared().some(function(u){"
                 " return u.id === 'gateProbe'; }); b.remove();"
                 "return JSON.stringify({undeclared: QCCD_PAGE.undeclared(), nohint: nohint, stale: stale,"
                 " contract: !!window.QCCD_INTERFACE, caught: caught}); })()")
        spec = tmp_path / "spec.json"
        spec.write_text(json.dumps({"pages": {"a": f"http://127.0.0.1:{info['port']}/studio#pair={code}"}, "steps": [
            {"wait": "!!window.EDITOR && !!window.QCCD_PAGE && !!document.getElementById('qcl-dock')", "timeout": 40000,
             "stopOnFail": True},
            {"sleep": 1500},
            {"eval": probe}]}), encoding="utf-8")
        r = subprocess.run(["node", str(REPO / "tests" / "workspace_browser.mjs"), str(spec)], capture_output=True,
                           timeout=300, cwd=REPO)
        out = json.loads(r.stdout.decode("utf-8") or "{}")
        st = out.get("steps", [])
        assert len(st) == 3 and st[0]["ok"], json.dumps(out)[:3000]
        got = json.loads(st[2]["value"])
        assert got["contract"], "the Studio page carries no window.QCCD_INTERFACE"
        assert got["caught"], "an undeclared button planted in the Studio was not reported"
        assert not got["undeclared"], (
            "controls in the Studio that an agent cannot recognise -- declare each INLINE: data-hint=\"<key>\" on "
            "the element and a HINTS entry for the key in qccd/viz/js/editor.js (docs/agent-interface.md): "
            + json.dumps(got["undeclared"], ensure_ascii=False)[:3000])
        assert not got["nohint"], f"data-hint keys with no HINTS entry (EDITOR.hintFor): {got['nohint']}"
        assert not got["stale"], f"declarations in interface.py name controls the Studio no longer has: {got['stale']}"
    finally:
        try:
            service_request(info, "POST", "/api/shutdown", {}, token="owner")
        except Exception:
            pass
