"""The workspace application layer: change sets, conflicts, protection, undo, candidates,
prompts, delivery and sessions -- transport-free (no HTTP, no MCP, no agent)."""

from __future__ import annotations

import json
import time

import pytest

from qccd.workspace.app import Workspace, WorkspaceError

HUMAN = {"kind": "human", "id": "view:t", "view": None}
AGENT = {"kind": "agent", "id": "agent:t"}


@pytest.fixture
def ws(tmp_path):
    w = Workspace.init(tmp_path / "ws", "ghz4@1")
    yield w
    w.close()


def cs(ws, actor, ops, *, rev=None, rid=None, mode="apply", **kw):
    return ws.apply_change_set({"expected_revision": ws.head().revision if rev is None else rev,
                                "request_id": rid or f"r{time.time_ns()}", "mode": mode, "operations": ops, **kw}, actor)


def test_init_pins_the_release_and_ignores_local_state(ws):
    lock = json.loads((ws.root / "qccd.lock.json").read_text())
    assert lock["task"]["id"] == "ghz4@1" and lock["task"]["digest"] == ws.release.digest
    assert ".qccd/" in (ws.root / ".gitignore").read_text()
    assert ws.replayed().ok and ws.head().revision == 0
    doc = json.loads(ws.mirror_path.read_text())
    assert doc["kind"] == "qccd.studio" and doc["qccd_workspace"]["revision"] == 0


def test_preview_changes_nothing_and_apply_is_attributed(ws):
    p = cs(ws, AGENT, [{"type": "add_site", "id": "T9", "pos": [2, 2], "zone": "trap"}], mode="preview")
    assert p["status"] == "previewed" and ws.head().revision == 0
    a = cs(ws, AGENT, [{"type": "add_site", "id": "T9", "pos": [2, 2], "zone": "trap"},
                       {"type": "add_segment", "a": "C1", "b": "T9"}], rid="a1")
    assert a["status"] == "committed" and a["revision"] == 1
    assert a["created_entities"] == ["node:T9", "segment:X0"]
    h = ws.history()[0]
    assert h["actor"]["kind"] == "agent" and h["change_set_id"] == a["change_set_id"]
    rec = [e for e in ws.head().state.edits if e["meta"].get("change_set") == a["change_set_id"]]
    assert len(rec) == 2


def test_idempotency_same_key_same_result_different_payload_refused(ws):
    ops = [{"type": "add_site", "id": "T9", "pos": [2, 2], "zone": "trap"}]
    a = cs(ws, AGENT, ops, rid="k", rev=0)
    b = cs(ws, AGENT, ops, rid="k", rev=0)
    assert b["duplicate"] and b["revision"] == a["revision"] and ws.head().revision == 1
    with pytest.raises(WorkspaceError) as e:
        cs(ws, AGENT, [{"type": "move_site", "id": "C0", "pos": [0, 3]}], rid="k", rev=0)
    assert e.value.code == "idempotency_conflict"


def test_stale_agent_change_is_a_conflict_that_names_the_intervening_edit(ws):
    cs(ws, HUMAN, [{"type": "move_site", "id": "C1", "pos": [1, 1]}], rid="h")
    with pytest.raises(WorkspaceError) as e:
        cs(ws, AGENT, [{"type": "move_site", "id": "C1", "pos": [5, 5]}], rev=0, rid="stale")
    assert e.value.code == "conflict" and e.value.status == 409
    assert e.value.detail["head_revision"] == 1
    assert e.value.detail["intervening"][0]["actor"]["kind"] == "human"
    # the human's work survives
    assert ws.replayed().arch.device.nodes["C1"].pos == (1.0, 1.0)


def test_disjoint_stale_change_rebases_only_when_asked(ws):
    cs(ws, HUMAN, [{"type": "move_site", "id": "C0", "pos": [0, 1]}])
    with pytest.raises(WorkspaceError):
        cs(ws, AGENT, [{"type": "move_site", "id": "C3", "pos": [3, 1]}], rev=0)
    ok = cs(ws, AGENT, [{"type": "move_site", "id": "C3", "pos": [3, 1]}], rev=0, rebase="if_disjoint")
    assert ok["rebased_onto"] == 1 and ok["revision"] == 2
    arch = ws.replayed().arch
    assert arch.device.nodes["C0"].pos == (0.0, 1.0) and arch.device.nodes["C3"].pos == (3.0, 1.0)


def test_protection_is_enforced_for_every_actor_and_only_a_person_lifts_it(ws):
    cs(ws, HUMAN, [{"type": "protect", "keys": ["node:C2"], "note": "gate zone"}])
    for actor in (AGENT, HUMAN):
        with pytest.raises(WorkspaceError) as e:
            cs(ws, actor, [{"type": "move_site", "id": "C2", "pos": [9, 9]}])
        assert e.value.code == "protected"
    with pytest.raises(WorkspaceError) as e:
        cs(ws, AGENT, [{"type": "remove_node", "id": "C2"}])
    assert e.value.code == "protected"
    with pytest.raises(WorkspaceError) as e:
        cs(ws, AGENT, [{"type": "unprotect", "keys": ["node:C2"]}])
    assert e.value.code == "forbidden"
    # entity-level protection allows a new neighbour; neighbourhood level does not
    cs(ws, AGENT, [{"type": "add_site", "id": "N9", "pos": [2, 3], "zone": "trap"},
                   {"type": "add_segment", "a": "C2", "b": "N9"}])
    cs(ws, HUMAN, [{"type": "protect", "keys": ["node:C1"], "level": "neighbourhood"}])
    with pytest.raises(WorkspaceError):
        cs(ws, AGENT, [{"type": "add_site", "id": "N8", "pos": [1, 3], "zone": "trap"},
                       {"type": "add_segment", "a": "C1", "b": "N8"}])
    cs(ws, HUMAN, [{"type": "unprotect", "keys": ["node:C2"]}])
    cs(ws, AGENT, [{"type": "move_site", "id": "C2", "pos": [2, 1]}])


def test_protection_also_holds_for_file_import_and_candidate_adoption(ws, tmp_path):
    cand = ws.create_candidate(AGENT, name="try")          # forked before the lock existed
    ws.apply_change_set({"branch": cand["name"], "expected_revision": 0, "request_id": "c1", "mode": "apply",
                         "operations": [{"type": "move_site", "id": "C0", "pos": [0, 5]}]}, AGENT)
    cs(ws, HUMAN, [{"type": "protect", "keys": ["node:C0"]}])
    later = ws.create_candidate(AGENT, name="later")        # a candidate inherits main's locks
    with pytest.raises(WorkspaceError) as e:
        ws.apply_change_set({"branch": later["name"], "expected_revision": 0, "request_id": "c2", "mode": "apply",
                             "operations": [{"type": "move_site", "id": "C0", "pos": [0, 6]}]}, AGENT)
    assert e.value.code == "protected"
    with pytest.raises(WorkspaceError) as e:
        ws.adopt_candidate(cand["name"], HUMAN, expected_revision=ws.head().revision, request_id="adopt")
    assert e.value.code == "protected"
    doc = json.loads(ws.mirror_path.read_text())
    doc["edits"].append({"method": "move_site", "args": ["C0", 0.0, 7.0], "kwargs": {}})
    f = tmp_path / "edited.json"
    f.write_text(json.dumps(doc))
    with pytest.raises(WorkspaceError) as e:
        ws.import_file(f, AGENT)
    assert e.value.code == "protected"


def test_file_import_of_a_stale_copy_never_overwrites_newer_edits(ws, tmp_path):
    old = json.loads(ws.mirror_path.read_text())              # a copy made at r0
    cs(ws, HUMAN, [{"type": "move_site", "id": "C1", "pos": [1, 2]}])
    old["edits"].append({"method": "move_site", "args": ["C1", 1.0, -4.0], "kwargs": {}})
    f = tmp_path / "stale.json"
    f.write_text(json.dumps(old))
    with pytest.raises(WorkspaceError) as e:
        ws.import_file(f, AGENT)
    assert e.value.code == "conflict"
    fresh = json.loads(ws.mirror_path.read_text())
    fresh["edits"].append({"method": "move_site", "args": ["C3", 3.0, 2.0], "kwargs": {}})
    f.write_text(json.dumps(fresh))
    r = ws.import_file(f, AGENT)
    assert r["status"] == "committed"
    arch = ws.replayed().arch
    assert arch.device.nodes["C1"].pos == (1.0, 2.0) and arch.device.nodes["C3"].pos == (3.0, 2.0)
    f.write_text('{"kind": "qccd.studio", "kind": "x"}')
    with pytest.raises(WorkspaceError) as e:
        ws.import_file(f, AGENT)
    assert e.value.code == "bad_file"


def test_undo_is_a_new_change_that_keeps_later_unrelated_work(ws):
    a = cs(ws, AGENT, [{"type": "add_site", "id": "T9", "pos": [2, 2], "zone": "trap"},
                       {"type": "add_segment", "a": "C1", "b": "T9"}])
    cs(ws, HUMAN, [{"type": "move_site", "id": "C0", "pos": [0, 1]}])
    u = ws.undo_change_set(a["change_set_id"], HUMAN)
    assert u["revision"] == 3 and "T9" not in ws.replayed().arch.device.nodes
    assert ws.replayed().arch.device.nodes["C0"].pos == (0.0, 1.0)
    assert ws.change_set(a["change_set_id"])["status"] == "undone"
    with pytest.raises(WorkspaceError):
        ws.undo_change_set(a["change_set_id"], HUMAN)


def test_undo_reports_dependent_changes(ws):
    a = cs(ws, AGENT, [{"type": "add_site", "id": "T9", "pos": [2, 2], "zone": "trap"}])
    cs(ws, HUMAN, [{"type": "add_segment", "a": "C1", "b": "T9"}])
    with pytest.raises(WorkspaceError) as e:
        ws.undo_change_set(a["change_set_id"], HUMAN)
    assert e.value.code == "conflict"


def test_drafts_do_not_deliver_and_send_freezes_context(ws):
    s = ws.register_session({"kind": "human", "id": "cli"}, client="codex", mode="appserver", label="Codex")
    v = ws.register_view(HUMAN)
    assert v["target_session"] == s["id"]
    d = ws.save_draft(HUMAN, {"text": "keep these", "anchors": [{"kind": "entity", "key": "node:C1"}]})
    assert ws.store.one("SELECT COUNT(*) AS n FROM deliveries")["n"] == 0
    sent = ws.send_prompt(HUMAN, prompt_id=d["prompt_id"], view_id=v["id"], context={"design_revision": 0})
    assert sent["delivery"]["state"] == "queued" and sent["design_revision"] == 0
    cs(ws, HUMAN, [{"type": "move_site", "id": "C1", "pos": [1, 3]}])        # the design moves on
    ctx = ws.context_snapshot(sent["context_snapshot_id"])
    assert ctx["anchors"][0]["resolved"]["node:C1"]["pos"] == [1.0, 0.0]    # what the user SAW
    # editing a sent prompt makes a version; the queued v1 is superseded, not resent
    ws.save_draft(HUMAN, {"text": "keep these, and C2", "anchors": []}, prompt_id=d["prompt_id"])
    s2 = ws.send_prompt(HUMAN, prompt_id=d["prompt_id"], view_id=v["id"])
    assert s2["version"] == 2
    states = {r["prompt_version"]: r["state"] for r in ws.store.all("SELECT * FROM deliveries")}
    assert states == {1: "cancelled", 2: "queued"}
    assert ws.prompt(d["prompt_id"])["versions"][0]["body"]["text"] == "keep these"


def test_agents_cannot_send_and_replies_never_deliver(ws):
    with pytest.raises(WorkspaceError):
        ws.send_prompt(AGENT, body={"text": "do it"})
    s = ws.register_session({"kind": "human", "id": "cli"}, client="codex", mode="appserver")
    v = ws.register_view(HUMAN)
    p = ws.send_prompt(HUMAN, body={"text": "hi"}, view_id=v["id"])
    n0 = ws.store.one("SELECT COUNT(*) AS n FROM deliveries")["n"]
    ws.reply({**AGENT, "session": s["id"]}, p["prompt_id"], "done", work_state="ready_for_review")
    assert ws.store.one("SELECT COUNT(*) AS n FROM deliveries")["n"] == n0
    pr = ws.prompt(p["prompt_id"])
    assert pr["work"]["state"] == "ready_for_review" and pr["deliveries"][0]["state"] == "accepted"


def test_modes_are_enforced(ws):
    v = ws.register_view(HUMAN)
    ask = ws.send_prompt(HUMAN, body={"text": "why?", "intent": "question"}, view_id=v["id"])
    prop = ws.send_prompt(HUMAN, body={"text": "try", "mode": "propose"}, view_id=v["id"])
    ops = [{"type": "move_site", "id": "C0", "pos": [0, 1]}]
    for pid, code in ((ask["prompt_id"], "ask_only"), (prop["prompt_id"], "propose_only")):
        with pytest.raises(WorkspaceError) as e:
            cs(ws, AGENT, ops, origin_prompt_id=pid)
        assert e.value.code == code
    cs(ws, AGENT, ops, origin_prompt_id=prop["prompt_id"], mode="preview")
    cand = ws.create_candidate(AGENT, name="p")
    ws.apply_change_set({"branch": cand["name"], "expected_revision": 0, "request_id": "x", "mode": "apply",
                         "origin_prompt_id": prop["prompt_id"], "operations": ops}, AGENT)


def test_stop_fences_the_session_and_cancels_its_queue(ws):
    s = ws.register_session({"kind": "human", "id": "cli"}, client="codex", mode="appserver")
    v = ws.register_view(HUMAN)
    p = ws.send_prompt(HUMAN, body={"text": "go", "mode": "apply"}, view_id=v["id"])
    with pytest.raises(WorkspaceError):
        ws.stop_session(s["id"], AGENT)
    ws.stop_session(s["id"], HUMAN, prompt_id=p["prompt_id"])
    assert ws.prompt(p["prompt_id"])["deliveries"][0]["state"] == "cancelled"
    with pytest.raises(WorkspaceError) as e:
        cs(ws, {**AGENT, "session": s["id"]}, [{"type": "move_site", "id": "C0", "pos": [0, 1]}])
    assert e.value.code == "session_stopped"
    ws.resume_session(s["id"], HUMAN)
    with pytest.raises(WorkspaceError) as e:     # the cancelled prompt stays cancelled
        cs(ws, {**AGENT, "session": s["id"]}, [{"type": "move_site", "id": "C0", "pos": [0, 1]}],
           origin_prompt_id=p["prompt_id"])
    assert e.value.code == "prompt_cancelled"
    cs(ws, {**AGENT, "session": s["id"]}, [{"type": "move_site", "id": "C0", "pos": [0, 1]}])


def test_delivery_state_machine_and_acknowledgement(ws):
    s = ws.register_session({"kind": "human", "id": "cli"}, client="claude", mode="channel")
    v = ws.register_view(HUMAN)
    p = ws.send_prompt(HUMAN, body={"text": "hi"}, view_id=v["id"])
    d = ws.due_deliveries(session_id=s["id"])[0]
    c = ws.claim_delivery(d["id"])
    assert c["state"] == "sending" and ws.claim_delivery(d["id"]) is None          # claimed once
    ws.mark_delivery(d["id"], "uncertain", detail="pushed; unacknowledged")
    with pytest.raises(WorkspaceError):
        ws.mark_delivery(d["id"], "sending")                                         # not a legal step
    ctx = ws.context({**AGENT, "session": s["id"]})
    assert ctx["unread_prompts"][0]["prompt_id"] == p["prompt_id"]
    assert ws.prompt(p["prompt_id"])["deliveries"][0]["state"] == "accepted"          # reading = ack
    assert ws.prompt(p["prompt_id"])["work"]["state"] == "working"


def test_restart_recovery_marks_uncertain_and_failed_honestly(ws):
    s = ws.register_session({"kind": "human", "id": "cli"}, client="codex", mode="appserver")
    v = ws.register_view(HUMAN)
    ws.send_prompt(HUMAN, body={"text": "hi"}, view_id=v["id"])
    d = ws.due_deliveries()[0]
    ws.claim_delivery(d["id"])
    with ws.store.tx() as db:
        db.execute("INSERT INTO jobs(id, kind, status, actor, created_at, pid) VALUES('job_x','compile','running','{}',0,999999)")
    root = ws.root
    ws.close()
    w2 = Workspace(root, recover=True)            # what the service does on start
    assert w2.store.one("SELECT state FROM deliveries WHERE id=?", (d["id"],))["state"] == "uncertain"
    assert w2.job("job_x")["status"] == "internal_error"
    assert all(x["status"] == "disconnected" for x in w2.sessions())
    w2.close()


def test_context_is_bounded_and_says_what_matters(ws):
    ctx = ws.context(AGENT)
    for k in ("boards", "designs", "design_title", "revision", "protected", "unread_prompts", "jobs", "latest_result", "cursor", "next"):
        assert k in ctx
    assert "entities" not in ctx
    full = ws.context(AGENT, detail="full")
    assert len(full["entities"]) <= 300
    inc = ws.context(AGENT, since=ctx["cursor"])
    assert inc["changes_since"]["events"] == []


def test_presentation_validates_its_target(ws):
    ws.register_view(HUMAN)
    with pytest.raises(WorkspaceError):
        ws.present(AGENT, "highlight", {"keys": ["node:NOPE"]})
    out = ws.present(AGENT, "highlight", {"keys": ["node:C1"]})
    assert out["status"] in ("requested", "no_connected_view")


def test_a_turn_finishing_after_a_restart_still_finds_its_prompt(ws):
    """A Codex turn accepted by one service process and finished under the next: the new
    bridge has no in-memory turn map, so the delivery row's recorded turn id links the
    completion back to the prompt."""
    from types import SimpleNamespace
    from qccd.workspace.agents.codex import _on_codex_event
    s = ws.register_session({"kind": "human", "id": "cli"}, client="codex", mode="appserver")
    v = ws.register_view(HUMAN)
    p = ws.send_prompt(HUMAN, body={"text": "go", "mode": "apply"}, view_id=v["id"])
    d = ws.due_deliveries(session_id=s["id"])[0]
    ws.claim_delivery(d["id"])
    ws.mark_delivery(d["id"], "accepted", external_ref="turn-before-restart")
    ws.set_work_state(p["prompt_id"], "working", "turn running")
    state = SimpleNamespace(ws=ws, bridges={}, worker=None)          # a service with no bridge memory
    _on_codex_event(state, s["id"], "turn/completed", {"turn": {"id": "turn-before-restart", "status": "completed"}})
    ev = [e for e in ws.events_since(0)["events"] if e["type"] == "agent.turn"][-1]
    assert ev["payload"]["delivery_id"] == d["id"]
    assert ws.prompt(p["prompt_id"])["work"]["state"] == "ready_for_review"
    # a turn this workspace never delivered stays unlinked
    _on_codex_event(state, s["id"], "turn/completed", {"turn": {"id": "someone-elses", "status": "completed"}})
    ev = [e for e in ws.events_since(0)["events"] if e["type"] == "agent.turn"][-1]
    assert ev["payload"]["delivery_id"] is None
