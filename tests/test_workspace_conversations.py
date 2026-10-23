"""Conversations in the chat, the person's switch for agent file writes, and files saved for them.

Two complaints, 2026-10-23.  A correspondent: "the current Codex session in Studio reports read-only
permissions and cannot export the new files" -- Studio started every Codex thread read-only and no
tool wrote a file anywhere.  The user: session history should be easy to manage -- delete an old
session, start a new one, go back to one, keep a draft.  The chat had been ONE stream for the whole
workspace: "New conversation" started a new agent thread but every earlier message stayed on
screen, there was no list to return to, nothing could be deleted, and an unsent message was lost on
a reload.  Every Codex conversation also kept its own app server process running until the service
stopped.
"""

from __future__ import annotations

import json
import types

import pytest

from qccd.workspace.app import Workspace, WorkspaceError

HUMAN = {"kind": "human", "id": "view:t", "view": None}
AGENT = {"kind": "agent", "id": "agent:t"}


@pytest.fixture
def ws(tmp_path):
    w = Workspace.init(tmp_path / "ws", "ghz4@1")
    yield w
    w.close()


def _session(ws, label="Codex", client="codex"):
    return ws.register_session({"kind": "human", "id": "cli"}, client=client, mode="appserver", label=label)


def _say(ws, view, sid, text):
    return ws.send_prompt(HUMAN, body={"text": text}, view_id=view["id"], context={"target_session": sid})


def _texts(conv) -> list:
    return [i["text"] for i in conv["items"] if i["type"] == "user"]


# ---------------------------------------------------------------------- conversations

def test_each_session_is_its_own_conversation(ws):
    a, b = _session(ws), _session(ws)
    v = ws.register_view(HUMAN)
    _say(ws, v, a["id"], "Make C0 a bit wider, please")
    _say(ws, v, b["id"], "Run the BB code on this design")
    _say(ws, v, a["id"], "and C1 too")
    assert _texts(ws.conversation(session_id=a["id"], scoped=True)) == ["Make C0 a bit wider, please", "and C1 too"]
    assert _texts(ws.conversation(session_id=b["id"], scoped=True)) == ["Run the BB code on this design"]
    assert len(_texts(ws.conversation())) == 3                    # the debug view keeps the whole stream
    rows = ws.conversations()
    assert [r["session_id"] for r in rows] == [a["id"], b["id"]]   # the most recently active first
    assert rows[0]["title"] == "Make C0 a bit wider, please" and not rows[0]["named"]   # its first message
    assert [r["messages"] for r in rows] == [2, 1] and rows[0]["client"] == "codex"


def test_a_long_first_message_is_cut_for_the_title_and_an_empty_one_is_a_new_chat(ws):
    a, b = _session(ws), _session(ws)
    v = ws.register_view(HUMAN)
    _say(ws, v, a["id"], "word " * 40)
    t = {r["session_id"]: r["title"] for r in ws.conversations()}
    assert len(t[a["id"]]) <= 60 and t[a["id"]].endswith("...")
    assert t[b["id"]] == "New chat"


def test_messages_sent_without_an_agent_are_a_conversation_of_their_own(ws):
    v = ws.register_view(HUMAN)
    ws.send_prompt(HUMAN, body={"text": "is anyone there?"}, view_id=v["id"])
    rows = ws.conversations()
    assert [(r["session_id"], r["title"], r["messages"]) for r in rows] == [(None, "Sent without an agent", 1)]
    assert _texts(ws.conversation(session_id=None, scoped=True)) == ["is anyone there?"]


def test_a_draft_stays_with_its_conversation_until_it_is_sent(ws):
    a, b = _session(ws), _session(ws)
    v = ws.register_view(HUMAN)
    d = ws.save_draft(HUMAN, {"text": "half a thought", "draft_for": a["id"]})
    ws.save_draft(HUMAN, {"text": "an idea for a new chat", "draft_for": "new"})
    assert ws.conversation(session_id=a["id"], scoped=True)["draft"]["text"] == "half a thought"
    assert ws.conversation(session_id=b["id"], scoped=True)["draft"] is None
    assert ws.conversation_draft("new")["text"] == "an idea for a new chat"
    assert {r["session_id"]: r["draft"] for r in ws.conversations()} == {a["id"]: True, b["id"]: False}
    # the person goes on typing (the same draft), then sends it: the draft IS the message
    ws.save_draft(HUMAN, {"text": "half a thought, finished", "draft_for": a["id"]}, prompt_id=d["prompt_id"])
    sent = ws.send_prompt(HUMAN, prompt_id=d["prompt_id"], body={"text": "half a thought, finished"},
                          view_id=v["id"], context={"target_session": a["id"]})
    assert sent["prompt_id"] == d["prompt_id"] and sent["delivery"]["session_id"] == a["id"]
    assert ws.conversation(session_id=a["id"], scoped=True)["draft"] is None
    assert _texts(ws.conversation(session_id=a["id"], scoped=True)) == ["half a thought, finished"]
    with pytest.raises(WorkspaceError):
        ws.save_draft(HUMAN, {"text": "x", "draft_for": {"not": "an id"}})


def test_rename_delete_and_undo(ws):
    a, b = _session(ws), _session(ws)
    v = ws.update_view(ws.register_view(HUMAN)["id"], {"target_session": a["id"]}, HUMAN)
    _say(ws, v, a["id"], "first")
    ws.rename_session(a["id"], "  BB on   the triangle ", HUMAN)
    row = [r for r in ws.conversations() if r["session_id"] == a["id"]][0]
    assert row["title"] == "BB on the triangle" and row["named"]
    with pytest.raises(WorkspaceError):
        ws.rename_session(a["id"], "mine now", AGENT)
    # something still queued for it is cancelled; the page talks to no one; it leaves every list
    queued = ws.store.one("SELECT COUNT(*) AS n FROM deliveries WHERE session_id=? AND state='queued'", (a["id"],))["n"]
    assert queued == 1
    with pytest.raises(WorkspaceError):
        ws.delete_session(a["id"], AGENT)
    ws.delete_session(a["id"], HUMAN)
    assert ws.store.one("SELECT COUNT(*) AS n FROM deliveries WHERE session_id=? AND state='queued'", (a["id"],))["n"] == 0
    assert ws.view(v["id"])["target_session"] is None
    assert a["id"] not in [r["session_id"] for r in ws.conversations()]
    assert a["id"] not in [s["id"] for s in ws.sessions()]
    assert ws.session(a["id"])["deleted"] and ws.session(a["id"])["status"] == "closed"
    with pytest.raises(WorkspaceError) as e:
        ws.update_view(v["id"], {"target_session": a["id"]}, HUMAN)
    assert e.value.code == "deleted"
    # Undo: it is back, with its messages and its name, and goes on when it is opened
    ws.restore_session(a["id"], HUMAN)
    row = [r for r in ws.conversations() if r["session_id"] == a["id"]][0]
    assert row["title"] == "BB on the triangle" and row["messages"] == 1 and row["status"] == "idle"
    assert ws.update_view(v["id"], {"target_session": a["id"]}, HUMAN)["target_session"] == a["id"]


# ---------------------------------------------------------------------- files

def test_the_file_setting_is_the_persons_and_the_agent_is_told(ws):
    assert ws.settings() == {"agent_files": "read"}
    files = ws.context(AGENT)["files"]
    assert files["agent_may_write"] is False and "qccd_export" in files["how"]
    assert "Let the agent save files in this folder" in files["how"]           # the switch it points the person at
    with pytest.raises(WorkspaceError):
        ws.set_settings({"agent_files": "write"}, AGENT)
    with pytest.raises(WorkspaceError):
        ws.set_settings({"agent_files": "everything"}, HUMAN)
    assert ws.set_settings({"agent_files": "write"}, HUMAN) == {"agent_files": "write"}
    files = ws.context(AGENT)["files"]
    assert files["agent_may_write"] is True and "qccd_export" in files["how"]
    assert ws.store.one("SELECT COUNT(*) AS n FROM events WHERE type='settings.updated'")["n"] == 1


def test_an_export_is_a_new_folder_inside_the_workspace(ws):
    r = ws.export_files("design", HUMAN)
    assert r["folder"] == "exports/Main-design-r0"
    names = sorted(f["file"].rsplit("/", 1)[1] for f in r["files"])
    assert names == ["Main-design.arch.json", "Main-design.studio.json"]
    folder = ws.root / r["folder"]
    readme = (folder / "README.txt").read_text(encoding="utf-8")
    assert "Main-design.arch.json: the architecture document" in readme
    doc = json.loads((folder / "Main-design.studio.json").read_text(encoding="utf-8"))
    assert doc["qccd_workspace"]["revision"] == 0
    # the same again never writes over the first; a name cannot leave exports/
    assert ws.export_files("design", HUMAN)["folder"] == "exports/Main-design-r0-2"
    odd = ws.export_files("design", AGENT, name="../../outside")
    assert odd["folder"].startswith("exports/") and ".." not in odd["folder"]
    assert (ws.root / odd["folder"]).resolve().parent == (ws.root / "exports").resolve()
    # what was saved opens again in Studio: the import sees the design it already is
    out = ws.import_file(folder / "Main-design.studio.json", HUMAN, mode="preview")
    assert out["status"] == "previewed" and out["touched"] == [] and out["input_digest"] == ws.head().input_digest
    with pytest.raises(WorkspaceError):
        ws.export_files("everything", HUMAN)
    with pytest.raises(WorkspaceError):
        ws.export_files("run", HUMAN)                                # which run?


def test_a_run_export_carries_the_compiled_program(ws):
    from qccd.workspace.evaluator import Toolchain
    if Toolchain.discover().qccdc is None:
        pytest.skip("no qccdc compiler here")
    jid = ws.start_job(HUMAN, "run", {"program": "ghz4"})["job_id"]
    for _ in range(600):
        j = ws.job(jid)
        if j["status"] in ("succeeded", "failed", "cancelled"):
            break
        import time
        time.sleep(0.2)
    assert j["status"] == "succeeded", j
    r = ws.export_files("run", HUMAN, run_id=jid)
    names = {f["file"].rsplit("/", 1)[1] for f in r["files"]}
    assert {"device.arch.json", "circuit.qasm", "program.cooled.tsir.json", "program.qcert.json",
            "report.json"} <= names
    report = json.loads((ws.root / r["folder"] / "report.json").read_text(encoding="utf-8"))
    assert report["performance"]["total"]["ms"] > 0


# ---------------------------------------------------------------------- the agents follow the setting

def test_codex_gets_the_sandbox_the_setting_says_with_every_turn(ws):
    from qccd.workspace.agents.codex import CodexBridge, _sandbox_now, sandbox_policy
    sent = []
    br = CodexBridge("ws://127.0.0.1:1")
    br.thread_id = "thr"
    br.request = lambda method, params, **kw: sent.append((method, params)) or {"turn": {"id": f"u{len(sent)}"}}
    br.deliver("hi", "d1")
    assert "sandboxPolicy" not in sent[-1][1]            # a thread whose sandbox the person chose (a terminal)
    br.files_setting = lambda: ws.settings()["agent_files"]
    br.deliver("hi", "d2")
    assert sent[-1][1]["sandboxPolicy"] == {"type": "readOnly"} and _sandbox_now(ws) == "read-only"
    ws.set_settings({"agent_files": "write"}, HUMAN)
    br.deliver("now save it", "d3")                       # from the next message, no reconnect
    assert sent[-1][1]["sandboxPolicy"] == {"type": "workspaceWrite", "networkAccess": False}
    assert _sandbox_now(ws) == "workspace-write" and sandbox_policy("read") == {"type": "readOnly"}


def test_claude_and_cursor_get_file_tools_only_when_the_person_allows(ws):
    from qccd.workspace.agents import claude as C
    from qccd.workspace.agents import cursor as K
    tools, allowed, denied = C.run_tools("read")
    assert (tools, allowed, denied) == (C.BUILTIN_TOOLS, C.ALLOWED, C.DENIED) and "Write" in denied.split()
    tools, allowed, denied = C.run_tools("write")
    assert "Edit" in tools.split(",") and "Write" in tools.split(",")
    assert "Write(./**)" in allowed.split() and "Write(./.qccd/**)" in denied.split()
    assert "Bash" in denied.split() and "Write" not in denied.split()   # still no shell
    assert K.permissions("read") == K.PERMISSIONS
    p = K.permissions("write")
    assert "Write(**)" in p["allow"] and "Write(.qccd/**)" in p["deny"] and "Shell(**)" in p["deny"]
    assert "cannot edit files" in K.chat_note("read") and "create and change files" in K.chat_note("write")
    # a change of setting starts Claude's next run afresh (the same conversation, --resume)
    br = C.ClaudeBridge(types.SimpleNamespace(ws=ws), "s1", "claude", "conv", True)
    before = br._key()
    ws.set_settings({"agent_files": "write"}, HUMAN)
    assert br._key() != before


# ---------------------------------------------------------------------- the service: open, leave, delete

fastapi = pytest.importorskip("fastapi")


@pytest.fixture
def svc(tmp_path):
    import os
    from starlette.testclient import TestClient
    from qccd.workspace.service import ServiceState, create_app
    w = Workspace.init(tmp_path / "ws", "ghz4@1")
    state = ServiceState(w, {"port": 8765, "agent_token": "agent-tok", "owner_token": "owner-tok", "pid": os.getpid()})
    yield w, state, TestClient(create_app(state), base_url="http://127.0.0.1:8765")
    w.close()


OWNER = {"Authorization": "Bearer owner-tok"}


class _Bridge:
    """What `_leave` needs of a running runtime."""

    def __init__(self, turn=None):
        self.connected, self.active_turn, self.closed = True, turn, False

    def close(self):
        self.closed, self.connected = True, False


def test_a_conversation_the_person_leaves_stops_its_runtime_and_opening_it_says_whether_it_goes_on(svc, monkeypatch):
    ws, state, c = svc
    monkeypatch.setenv("QCCD_CODEX", "none")             # no Codex here: an idle Codex conversation cannot resume
    a, b = _session(ws), _session(ws)
    v = ws.update_view(ws.register_view(HUMAN)["id"], {"target_session": a["id"]}, HUMAN)
    state.bridges[a["id"]] = ba = _Bridge()
    state.bridges[b["id"]] = _Bridge()
    r = c.post(f"/api/sessions/{b['id']}/open", json={"view_id": v["id"]}, headers=OWNER)
    assert r.status_code == 200 and r.json()["view"]["target_session"] == b["id"]
    assert ba.closed and a["id"] not in state.bridges and ws.session(a["id"])["status"] == "idle"
    # back to A: the page shows it either way, and says why its agent cannot go on here
    r = c.post(f"/api/sessions/{a['id']}/open", json={"view_id": v["id"]}, headers=OWNER).json()
    assert r["view"]["target_session"] == a["id"] and r["resumed"] is False and "cannot go on" in r["why"]
    # a conversation in the middle of a turn is left running
    state.bridges[a["id"]] = _Bridge(turn="t9")
    ws.session_status(a["id"], "connected")
    c.patch(f"/api/views/{v['id']}", json={"target_session": b["id"]}, headers=OWNER)
    assert ws.session(a["id"])["status"] == "connected" and not state.bridges[a["id"]].closed


def test_the_list_and_delete_over_http(svc):
    ws, state, c = svc
    a = _session(ws)
    v = ws.register_view(HUMAN)
    _say(ws, v, a["id"], "hello")
    state.bridges[a["id"]] = br = _Bridge()
    lst = c.get("/api/conversations", headers=OWNER).json()
    assert [x["title"] for x in lst["conversations"]] == ["hello"] and lst["new_chat_draft"] is False
    conv = c.get(f"/api/conversation?session={a['id']}", headers=OWNER).json()
    assert _texts(conv) == ["hello"] and conv["draft"] is None
    assert c.post(f"/api/sessions/{a['id']}/delete", json={}, headers={"Authorization": "Bearer agent-tok"}).status_code == 403
    assert c.post(f"/api/sessions/{a['id']}/delete", json={}, headers=OWNER).status_code == 200
    assert br.closed and a["id"] not in state.bridges
    assert c.get("/api/conversations", headers=OWNER).json()["conversations"] == []
    assert c.post(f"/api/sessions/{a['id']}/open", json={"view_id": v["id"]}, headers=OWNER).status_code == 409
    assert c.post(f"/api/sessions/{a['id']}/restore", json={}, headers=OWNER).status_code == 200
    assert [x["session_id"] for x in c.get("/api/conversations", headers=OWNER).json()["conversations"]] == [a["id"]]


def test_settings_and_export_over_http(svc):
    ws, state, c = svc
    agent = {"Authorization": "Bearer agent-tok"}
    assert c.get("/api/settings", headers=agent).json() == {"agent_files": "read"}
    assert c.post("/api/settings", json={"agent_files": "write"}, headers=agent).status_code == 403
    assert c.post("/api/settings", json={"agent_files": "write"}, headers=OWNER).json() == {"agent_files": "write"}
    r = c.post("/api/export", json={"what": "design"}, headers=agent)
    assert r.status_code == 200 and r.json()["folder"] == "exports/Main-design-r0"
    assert c.post("/api/export", json={"what": "design", "revision": "0"}, headers=agent).status_code == 422
