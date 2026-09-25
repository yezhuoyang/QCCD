"""Runs (compile + replay + where the time goes), drafts, comparison, and the chat's stream.

The runs use the real compiler (skipped when it is not built); the conversation and
delivery-text tests need nothing but the workspace.
"""

from __future__ import annotations

import time

import pytest

from qccd.workspace.app import Workspace, WorkspaceError
from qccd.workspace.evaluator import Toolchain
from qccd.workspace.perf import compare, frame_at
from qccd.workspace.runs import program_catalog, resolve_program

HUMAN = {"kind": "human", "id": "cli"}
needs_compiler = pytest.mark.skipif(Toolchain.discover().qccdc is None, reason="qccdc_cli is not built")


@pytest.fixture
def ws(tmp_path):
    w = Workspace.init(tmp_path / "ws", "ghz4@1")
    yield w
    w.close()


def _wait(ws, jid, timeout=300):
    t0 = time.time()
    while ws.job(jid)["status"] not in ("succeeded", "failed", "internal_error", "cancelled", "timeout"):
        assert time.time() - t0 < timeout, "the run did not finish"
        time.sleep(0.3)
    return ws.job(jid)


def test_the_catalogue_and_what_people_call_its_programs():
    names = {p["name"]: p["qubits"] for p in program_catalog()}
    assert names["bb144_esm"] == 168 and names["ghz4"] == 4 and "surface17_esm" in names
    for said in ("bb", "BB code", "gross", "bb144", "bb144_esm"):
        assert resolve_program(said)[0] == "bb144_esm"
    assert resolve_program({"name": "mine", "qasm": "OPENQASM 2.0;\nqreg q[2];\n"})[:2] == ("mine", "OPENQASM 2.0;\nqreg q[2];\n")
    with pytest.raises(WorkspaceError) as e:
        resolve_program("nonsense")
    assert e.value.code == "unknown_program" and "bb144_esm" in str(e.value)


@needs_compiler
def test_a_run_says_where_the_time_goes(ws):
    j = _wait(ws, ws.start_job(HUMAN, "run", {"program": "ghz4"})["job_id"])
    assert j["status"] == "succeeded", j
    run = ws.run(j["id"])
    perf = run["performance"]
    assert perf["total"]["ms"] > 0 and abs(sum(b["ms"] for b in perf["breakdown"]) - perf["total"]["ms"]) < 1e-3
    assert {b["category"] for b in perf["breakdown"]} >= {"gates", "measurement"}
    assert perf["counts"]["two_qubit_gates"] == 3 and perf["rules"]["failed"] == []
    assert perf["bottleneck"] and run["view"] == f"/runview/{j['id']}"
    times = __import__("json").loads(ws.get_artifact(run["artifacts"]["times"]))["times_us"]
    assert len(times) == perf["counts"]["instructions"] and times == sorted(times)
    assert frame_at(times, 0) == max(i for i, t in enumerate(times) if t == 0)     # the LAST step started by then
    assert frame_at(times, times[-1] + 1) == len(times) - 1
    assert ws.runs()[0]["run_id"] == j["id"] and ws.runs()[0]["total_ms"] == perf["total"]["ms"]


@needs_compiler
def test_a_design_too_small_is_refused_with_the_reason(ws):
    j = _wait(ws, ws.start_job(HUMAN, "run", {"program": "bb code"})["job_id"])
    assert j["status"] == "failed"
    assert "168 qubits" in j["result"]["summary"] and "cannot run here" in j["result"]["summary"]
    with pytest.raises(WorkspaceError) as e:
        ws.run(j["id"])
    assert e.value.code == "not_ready"


@needs_compiler
def test_two_drafts_compared(ws):
    ws.create_candidate(HUMAN, name="B")
    ws.apply_change_set({"branch": "cand/B", "expected_revision": 0, "request_id": "b", "mode": "apply",
                         "operations": [{"type": "add_chain", "prefix": "X", "count": 2, "start": [4, 0],
                                         "attach_to": "C3", "zone": "trap"}]}, HUMAN)
    a = _wait(ws, ws.start_job(HUMAN, "run", {"program": "ghz4"})["job_id"])
    b = _wait(ws, ws.start_job(HUMAN, "run", {"program": "ghz4", "draft": "B"})["job_id"])   # the short name works
    assert a["status"] == b["status"] == "succeeded"
    cmp = ws.compare_runs([a["id"], b["id"]])
    labels = [r["label"] for r in cmp["runs"]]
    assert labels == ["Main design r0", "B r1"]
    row = next(r for r in cmp["rows"] if r["metric"] == "round time")
    assert row["difference"] == pytest.approx(row["B r1"] - row["Main design r0"], abs=1e-3)
    assert cmp["verdict"] and cmp["view"] == f"/compare?runs={a['id']},{b['id']}" and "warning" not in cmp
    with pytest.raises(WorkspaceError):
        ws.compare_runs([a["id"]])


def test_drafts_resolve_by_their_short_name(ws):
    ws.create_candidate(HUMAN, name="A")
    assert ws.resolve_draft("A") == "cand/A" and ws.resolve_draft("cand/A") == "cand/A" and ws.resolve_draft(None) == "main"
    with pytest.raises(WorkspaceError) as e:
        ws.resolve_draft("Z")
    assert "A" in str(e.value)


def test_compare_verdict_is_computed_from_the_numbers():
    def run(total, transport):
        return {"performance": {"total": {"ms": total}, "breakdown": [{"category": "transport", "ms": transport},
                                                                      {"category": "gates", "ms": total - transport}],
                                "counts": {"instructions": 10, "transport_steps": 4, "cooling_steps": 0, "two_qubit_gates": 3},
                                "heating": {"peak_quanta": 1.0}, "bottleneck": ["x"]}}
    c = compare(run(100.0, 60.0), run(80.0, 40.0), ("A", "B"))
    assert c["verdict"].startswith("B is faster: 80 ms against 100 ms (1.25x)")
    assert "transport" in c["verdict"]


def test_the_conversation_is_one_stream(ws):
    s = ws.register_session(HUMAN, client="codex", mode="appserver", label="Codex")
    v = ws.register_view({"kind": "human", "id": "view:t"})
    sent = ws.send_prompt({"kind": "human", "id": "view:t", "view": v["id"]},
                          body={"text": "add a trap", "mode": "apply",
                                "anchors": [{"kind": "entity_group", "keys": ["node:C0", "node:C1"]}]}, view_id=v["id"])
    pid = sent["prompt_id"]
    d = ws.due_deliveries(session_id=s["id"])[0]
    ws.claim_delivery(d["id"])
    ws.mark_delivery(d["id"], "accepted", external_ref="turn-1")
    assert "This is a chat" in ws.delivery_payload(d)["text"]                  # the agent is told its words are the reply
    agent = {"kind": "agent", "id": f"agent:{s['id']}", "session": s["id"], "label": "Codex"}
    ws.reply(agent, pid, "Adding it next to C3.", via="session")
    cs = ws.apply_change_set({"expected_revision": 0, "request_id": "t", "mode": "apply",     # no origin_prompt_id given
                              "operations": [{"type": "add_site", "id": "T9", "pos": [4, 1], "zone": "trap"}]}, agent)
    ws.undo_change_set(cs["change_set_id"], HUMAN)
    conv = ws.conversation()
    kinds = [i["type"] for i in conv["items"]]
    assert kinds[:3] == ["user", "agent", "change"], kinds
    user, said, change = conv["items"][:3]
    assert user["text"] == "add a trap" and user["context"] == "2 parts"
    assert said["text"] == "Adding it next to C3." and said["author"] == "Codex"
    assert change["change_set_id"] == cs["change_set_id"] and change["status"] == "undone"   # linked to the working request
    assert conv["working"] == [{"prompt_id": pid, "state": "working"}]
