"""The published JSON Schemas describe what the code actually produces and accepts."""

from __future__ import annotations

import json
import time

import pytest

jsonschema = pytest.importorskip("jsonschema")
pytest.importorskip("pydantic")

from qccd.workspace.app import Workspace  # noqa: E402
from qccd.workspace.contracts import SCHEMA_DIR, main as contracts_main  # noqa: E402
from qccd.workspace.evaluator import Toolchain  # noqa: E402
from qccd.workspace.tasks import RELEASES_DIR  # noqa: E402


def _schema(name):
    return json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def _valid(name, doc):
    jsonschema.validate(doc, _schema(name))


def test_the_committed_schemas_are_generated_from_the_models():
    assert contracts_main(["--check"]) == 0


def test_real_documents_validate(tmp_path):
    ws = Workspace.init(tmp_path / "ws", "ghz4@1")
    req = {"expected_revision": 0, "request_id": "c1", "mode": "apply",
           "operations": [{"type": "add_site", "id": "T9", "pos": [2, 2], "zone": "trap"}]}
    _valid("change_set_request", req)
    _valid("change_set_result", ws.apply_change_set(req, {"kind": "agent", "id": "a"}))
    with pytest.raises(jsonschema.ValidationError):
        _valid("change_set_request", {**req, "sneaky": 1})           # unknown fields are refused, as the core does
    body = {"text": "keep these", "anchors": [{"kind": "entity", "key": "node:C1"}],
            "sketches": [{"kind": "arrow", "points": [[0, 1], [2, 1]]}]}
    _valid("prompt_body", body)
    v = ws.register_view({"kind": "human", "id": "v"})
    sent = ws.send_prompt({"kind": "human", "id": "v"}, body=body, view_id=v["id"])
    _valid("context_snapshot", ws.context_snapshot(sent["context_snapshot_id"]))
    _valid("lock", json.loads((ws.root / "qccd.lock.json").read_text()))
    _valid("task_release", json.loads((RELEASES_DIR / "ghz4@1" / "release.json").read_text()))
    ws.close()


@pytest.mark.skipif(Toolchain.discover().qccdc is None, reason="the compiler is not built")
def test_manifests_and_reports_validate(tmp_path):
    ws = Workspace.init(tmp_path / "ws", "ghz4@1")
    actor = {"kind": "human", "id": "cli"}

    def wait(jid):
        while ws.job(jid)["status"] not in ("succeeded", "failed", "internal_error", "cancelled", "timeout"):
            time.sleep(0.3)
        return ws.job(jid)
    j = wait(ws.start_job(actor, "compile", {})["job_id"])
    ws.apply_change_set({"expected_revision": 0, "request_id": "a", "mode": "apply",
                         "operations": [j["result"]["adopt_with"]]}, actor)
    s = ws.submit_local(actor, profile="draft")
    wait(s["job_id"])
    sub = ws.submission(s["submission_id"])
    man = json.loads((ws.root / sub["snapshot"]["dir"] / "bundle" / "manifest.json").read_text())
    _valid("bundle_manifest", man)
    _valid("evaluation_report", sub["report"])
    ws.close()
