"""The design layer: the studio's records replayed by the Python toolchain.

The browser studio and the workspace must agree on what a list of records MEANS, or a
human's edit and an agent's edit would be two different designs.  These tests drive the
real emitted studio page (tests/editor.mjs, the existing harness) through edits of every
record kind, take `EDITOR.snapshot()`, and require the Python replay to produce the same
architecture the browser serialised -- byte for byte in canonical form.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from qccd.workspace.design import (DesignRefused, DesignState, base_from_arch, diff_designs, empty_design,
                                   entities, record_kind, replay, touched_entities)
from qccd.workspace.jsonsafe import JSONRejected, Limits, canonical_text, digest, strict_loads
from qccd.workspace.operations import OperationError, compile_operations

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "tests" / "workspace_scripts"
NODE = shutil.which("node")


@pytest.fixture(scope="module")
def studio_page(tmp_path_factory):
    out = tmp_path_factory.mktemp("page") / "studio.html"
    r = subprocess.run(["python", "-m", "qccd", "studio", "-o", str(out)], cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return out


def _snapshot(page: Path, script: str) -> dict:
    r = subprocess.run([NODE, str(REPO / "tests" / "editor.mjs"), str(page), str(SCRIPTS / script)],
                       cwd=REPO, capture_output=True, timeout=300)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-2000:]
    out = json.loads(r.stdout.decode("utf-8"))
    for rec in out["log"]:
        assert not rec.get("error"), rec
        res = rec.get("result")
        if isinstance(res, dict) and "ok" in res:
            assert res["ok"], rec
    return [rec["snap"] for rec in out["log"] if "snap" in rec][-1]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("script,kinds", [("explicit_edits.json", {"build", "mutate"}),
                                          ("generator_edits.json", {"topology", "mutate"})])
def test_python_replays_what_the_browser_built(studio_page, script, kinds):
    from qccd.arch.device import Architecture
    snap = _snapshot(studio_page, script)
    st = DesignState.from_studio(snap)
    assert {record_kind(e) for e in st.edits} == kinds
    r = replay(st, strict=True)
    browser = Architecture.from_json(snap["arch"]).to_json(expanded=True)
    assert canonical_text(r.arch_doc) == canonical_text(browser)
    assert r.problems == []


def test_numbers_have_one_spelling():
    assert canonical_text({"a": 1.0, "b": [2.0, 2.5]}) == canonical_text({"b": [2, 2.5], "a": 1})
    assert digest({"x": 3}) == digest({"x": 3.0})


@pytest.mark.parametrize("text,code", [('{"a":1,"a":2}', "duplicate_key"), ('{"a":NaN}', "non_finite"),
                                       ('{"a":Infinity}', "non_finite"), ("[" * 80 + "]" * 80, "too_deep"),
                                       ("not json", "not_json")])
def test_strict_json_refuses_ambiguous_input(text, code):
    with pytest.raises(JSONRejected) as e:
        strict_loads(text)
    assert e.value.code == code


def test_strict_json_limits_size():
    with pytest.raises(JSONRejected) as e:
        strict_loads(json.dumps(list(range(2000))), Limits(max_items=100))
    assert e.value.code == "too_many_items"


def test_empty_canvas_and_generator_bases_replay():
    from qccd.api import Machine
    assert replay(empty_design("d")).ok
    st = base_from_arch(Machine.ring(8, 2, 4, name="r").arch)
    assert st.seed["method"] == "blank" and replay(st).ok


def test_operations_compile_to_studio_records_and_batch():
    st = empty_design("d")
    r = replay(st)
    new = compile_operations(st, r, [
        {"type": "add_chain", "prefix": "T", "count": 5, "start": [0, 0], "step": [2, 0], "zone": "trap"},
        {"type": "add_grid", "prefix": "G", "rows": 2, "cols": 3, "origin": [0, 6], "zone": "trap"},
        {"type": "add_segment", "a": "T0", "b": "G0_0"},
        {"type": "replicate", "nodes": ["T0", "T1"], "offset": [0, 12], "suffix": "_b", "attach": {"T0": "T4"}},
        {"type": "set_site_capacity", "ids": ["T2"], "capacity": 3},
    ], {"kind": "agent"})
    r2 = replay(new, strict=True)
    ents = entities(r2.arch_doc)
    assert {"node:T0", "node:T4", "node:G1_2", "node:T0_b", "node:T1_b", "segment:T0_1_b"} <= set(ents)
    assert ents["node:T2"]["capacity"] == 3
    assert all(record_kind(e) in ("topology", "mutate") for e in new.edits)
    d = diff_designs(r, r2)
    assert "node:T4#incident" in touched_entities(d, r, r2)


def test_malformed_operations_are_refused_with_their_index():
    st = empty_design("d")
    with pytest.raises(OperationError) as e:
        compile_operations(st, replay(st), [{"type": "add_site", "id": "A", "pos": [0, 0]},
                                            {"type": "add_site", "id": "9bad", "pos": [1, 0]}], {"kind": "agent"})
    assert e.value.index == 1 and e.value.code == "bad_id"
    with pytest.raises(OperationError) as e:
        compile_operations(st, replay(st), [{"type": "studio_sync", "append": []}], {"kind": "agent"})
    assert e.value.code == "forbidden"
    with pytest.raises(OperationError):
        compile_operations(st, replay(st), [{"type": "call", "method": "save", "args": ["/etc/x"]}], {"kind": "agent"})


def test_a_refused_record_is_a_problem_not_a_silent_skip():
    st = empty_design("d")
    st.edits.append({"topology": {"op": "add_segment", "args": {"id": "X", "a": "nope", "b": "nada"}}})
    assert replay(st).problems
    with pytest.raises(DesignRefused):
        replay(st, strict=True)


def test_studio_documents_are_normalised_on_the_way_in():
    doc = {"kind": "qccd.studio", "version": 1, "geom": [], "seed": None, "post": [],
           "edits": [{"method": "move_site", "args": ["A", 1, 2], "kwargs": {},
                      "meta": {"group": 1, "evil": "x"}, "_report": {"x": 1}}], "program": {"calls": []}}
    st = DesignState.from_studio(doc)
    assert st.edits[0] == {"method": "move_site", "args": ["A", 1, 2], "kwargs": {}, "meta": {"group": 1}}
    with pytest.raises(DesignRefused):
        DesignState.from_studio({**doc, "edits": [{"method": "__class__", "args": []}]})
    with pytest.raises(DesignRefused):
        DesignState.from_studio({**doc, "version": 2})
