"""ATIR: an agent's trace compiled into a program of instructions with costs and effects.

The user asked (2026-09-26) to read an agent's run the way a compiled QCCD programme is read:
a sequence of instructions, each with its time cost and memory, so a run can be audited,
replayed and later verified.  A synthetic trace in the exact shapes the recorders write
(trace.py, the Claude bridge, the MCP server, the page) is compiled here, checked, and then
TAMPERED with, to prove the checker is not a check that can only say "fine".
"""

from __future__ import annotations

import copy
import json

from qccd.workspace.app import Workspace
from qccd.workspace.atir import check, compile_trace, listing

AGENT = {"kind": "agent", "id": "agent:t", "label": "Claude"}


def _steps(t0: float, commit: dict) -> list:
    n = iter(range(1, 1000))
    st = lambda at, source, kind, name=None, data=None, ms=None: {
        "at": t0 + at, "n": next(n), "session": "s_t", "prompt": "p_1", "turn": "ct_1", "source": source,
        "kind": kind, "name": name, "data": data or {}, **({"ms": ms} if ms is not None else {})}
    # Claude Code reports a call's usage as it starts; only an event with stop_reason set has its final output
    usage = lambda i, c, o, stop=None: {"usage": {"input_tokens": i, "cache_read_input_tokens": c,
                                                  "cache_creation_input_tokens": 0, "output_tokens": o},
                                        "stop_reason": stop}
    return [
        st(0.0, "service", "delivery", "deliver", {"request": "Make a ring", "text": "[QCCD Studio] ... > Make a ring"}),
        st(2.0, "agent", "session", "claude", {"model": "claude-x", "tools": ["mcp__qccd__qccd_apply_change_set",
                                                                          "mcp__qccd__qccd_get_job", "Read"]}),
        # model call 1: thinks, says, calls apply (2.0 -> 5.0 s)
        st(4.0, "agent", "model", "msg_1", usage(10, 20000, 50)),
        st(4.0, "agent", "thinking", None, {"text": ""}),
        st(4.5, "agent", "message", None, {"text": "Drawing a ring."}),
        st(5.0, "agent", "model", "msg_1", usage(10, 20000, 180, "tool_use")),
        st(5.0, "agent", "tool_call", "mcp__qccd__qccd_apply_change_set",
           {"id": "tu_1", "input": {"branch": "main", "mode": "apply", "operations": []}}),
        st(5.2, "mcp", "tool_call", "qccd_apply_change_set", {"args": {}, "result": commit}, ms=150),
        st(5.3, "agent", "tool_result", None, {"id": "tu_1", "content": json.dumps(commit), "is_error": False}),
        # model call 2: polls a job (5.3 -> 7.3 s)
        st(7.3, "agent", "model", "msg_2", usage(5, 21000, 40)),
        st(7.3, "agent", "tool_call", "mcp__qccd__qccd_get_job", {"id": "tu_2", "input": {"job_id": "job_1", "wait_s": 50}}),
        st(9.0, "mcp", "tool_call", "qccd_get_job", {"args": {}, "result": {"id": "job_1", "status": "succeeded"}}, ms=1600),
        st(9.1, "agent", "tool_result", None, {"id": "tu_2", "content": '{"id":"job_1","status":"succeeded"}'}),
        # model call 3: answers (9.1 -> 10.1 s)
        st(10.1, "agent", "model", "msg_3", usage(5, 21500, 90, "end_turn")),
        st(10.1, "agent", "message", None, {"text": "Done."}),
        st(10.2, "agent", "turn", "success", {"duration_ms": 10200, "duration_api_ms": 6000, "num_turns": 3,
                                              "total_cost_usd": 0.05, "usage": {"output_tokens": 310}}),
    ]


def test_a_trace_compiles_to_instructions_with_costs_memory_and_effects():
    commit = {"status": "committed", "branch": "main", "revision": 1, "change_set_id": "cs_x"}
    p = compile_trace(_steps(1000.0, commit))
    ops = [x["op"] for x in p["instructions"]]
    assert ops == ["recv", "boot", "think", "say", "call", "think", "call", "think", "say", "end"], ops
    th = [x for x in p["instructions"] if x["op"] == "think"]
    assert [x["ms"] for x in th] == [3000, 2000, 1000]                  # from the moment it could start
    assert th[0]["tokens"] == {"in": 10, "cache_read": 20000, "cache_write": 0, "out": 180}   # its final event counts
    assert th[1]["tokens"]["out"] is None and th[2]["tokens"]["out"] == 90     # no final event: unknown, not guessed
    assert [x["memory"] for x in th] == [20010, 21005, 21505]           # the context each call read
    apply_, poll = [x for x in p["instructions"] if x["op"] == "call"]
    assert apply_["tool"] == "qccd_apply_change_set" and apply_["exec_ms"] == 150 and apply_["ms"] == 300
    assert apply_["effects"] == [{"kind": "commit", "change_set": "cs_x", "revision": 1, "design": "main"}]
    assert poll["effects"][0] == {"kind": "poll", "job": "job_1", "status": "succeeded", "stage": None}
    s = p["summary"]
    assert s["by_machine_ms"] == {"model": 6000, "tools": 2100, "page": 0, "boot": 2000}
    assert s["model_calls"] == 3 and s["polls"] == 1 and s["commits"] == 1 and s["memory_peak"] == 21505
    assert check(p) == []
    text = listing(p, width=400)
    assert "qccd_apply_change_set" in text and "{commit cs_x 1 main}" in text and "3 model calls" in text


def test_the_checker_finds_a_tampered_or_incomplete_trace():
    commit = {"status": "committed", "branch": "main", "revision": 1, "change_set_id": "cs_x"}
    steps = _steps(1000.0, commit)
    unanswered = [s for s in steps if not (s["kind"] == "tool_result" and s["data"].get("id") == "tu_2")]
    f = check(compile_trace(unanswered))
    assert any(x["rule"] == "answered" for x in f), f
    cut = [s for s in steps if s["kind"] != "turn"]
    assert any(x["rule"] == "end" for x in check(compile_trace(cut)))


def test_every_commit_a_trace_claims_is_in_the_workspace_log_and_replays(tmp_path):
    ws = Workspace.init(tmp_path / "ws")
    try:
        r = ws.apply_change_set({"expected_revision": 0, "request_id": "a1", "mode": "apply",
                                 "operations": [{"type": "add_site", "id": "T1", "pos": [9, 9], "zone": "trap"}]}, AGENT)
        real = {"status": "committed", "branch": "main", "revision": r["revision"], "change_set_id": r["change_set_id"]}
        good = compile_trace(_steps(1000.0, real))
        assert check(good, ws) == []
        forged = copy.deepcopy(good)                   # a trace claiming a commit the workspace never made
        next(x for x in forged["instructions"] if x["op"] == "call")["effects"][0]["change_set"] = "cs_never"
        assert [x["rule"] for x in check(forged, ws)] == ["commit-logged"]
        moved = copy.deepcopy(good)                    # ... or at another revision than it was made
        next(x for x in moved["instructions"] if x["op"] == "call")["effects"][0]["revision"] = 7
        assert "the trace says main r7" in check(moved, ws)[0]["message"]
    finally:
        ws.close()
