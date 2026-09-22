"""The versioned contracts, as strict models, and the JSON Schemas generated from them.

    python -m qccd.workspace.contracts --write     regenerate qccd/workspace/schemas/*.schema.json
    python -m qccd.workspace.contracts --check     fail if the committed schemas are stale

The application layer validates its inputs by hand (it must run where pydantic is not
installed); these models are the PUBLISHED shape of the same documents, and
`tests/test_workspace_contracts.py` holds the two together by validating real documents --
a change set the core accepted, a prompt it froze, a bundle manifest it wrote, a report the
evaluator produced -- against the generated schemas.  Unknown fields are refused
(`extra="forbid"`) wherever the core refuses them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
VERSION = "1"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------- change sets

class Operation(_Open):
    """One semantic operation; `type` names an entry of `operations.OPERATIONS` (or
    `protect` / `unprotect`).  Its parameters are listed by `qccd_read_reference(section=
    "operations")`, generated from the same registry."""
    type: str


class ChangeSetRequest(_Strict):
    workspace_id: Optional[str] = None
    branch: str = "main"
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=128)
    origin_prompt_id: Optional[str] = Field(default=None, max_length=64)
    mode: Literal["preview", "apply"] = "preview"
    summary: Optional[str] = Field(default=None, max_length=500)
    operations: list[Operation] = Field(min_length=1, max_length=5000)
    rebase: Literal["never", "if_disjoint"] = "never"


class Diff(_Strict):
    added: list[str]
    removed: list[str]
    changed: dict[str, list[str]]
    program_changed: bool
    final_program_changed: bool
    summary: str


class ChangeSetResult(_Open):
    contract: Literal["1"]
    status: Literal["previewed", "committed", "noop"]
    workspace_id: str
    branch: str
    base_revision: int
    revision: Optional[int]
    change_set_id: Optional[str]
    diff: Diff
    touched: list[str]
    diagnostics: dict[str, Any]
    attribution: dict[str, Any]


class ErrorBody(_Strict):
    code: str
    message: str
    detail: Optional[Any] = None


# ---------------------------------------------------------------------- prompts

AnchorKind = Literal["entity", "entity_group", "region", "point", "instruction", "circuit_op", "field",
                     "metric", "diagnostic", "event", "panel", "workspace"]


class Anchor(_Open):
    kind: AnchorKind


class Sketch(_Open):
    kind: Literal["stroke", "arrow", "lasso"]
    points: list[list[float]] = Field(min_length=1, max_length=2000)


class PromptBody(_Open):
    text: str = Field(max_length=8000)
    intent: Literal["comment", "question", "propose_change", "apply_change", "constraint"] = "propose_change"
    mode: Literal["ask", "propose", "apply"] = "propose"
    anchors: list[Anchor] = Field(default_factory=list, max_length=200)
    sketches: list[Sketch] = Field(default_factory=list, max_length=50)


class ContextSnapshot(_Open):
    id: str
    branch: str
    design_revision: int
    head_at_send: int
    input_digest: str
    arch_digest: Optional[str]
    view_id: Optional[str]
    selection: list[str]
    anchors: list[Anchor]
    sketches: list[Sketch]
    demonstration: Optional[dict[str, Any]]
    protected: dict[str, Any]
    coordinates: str


# ---------------------------------------------------------------------- bundles, releases, reports

class FileRef(_Strict):
    path: str
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    bytes: int = Field(ge=0)


class BundleManifest(_Strict):
    kind: Literal["qccd.submission_bundle"]
    version: Literal[1]
    task: dict[str, str]
    files: dict[Literal["device", "program", "certified_program", "certificate", "presentation"], FileRef]
    provenance: dict[str, Any]


class TaskRelease(_Open):
    kind: Literal["qccd.task_release"]
    version: Literal[1]
    task: str
    release: str
    title: str
    track: str
    circuit: dict[str, str]
    physics: dict[str, Any]
    required_checks: list[str]
    metrics: list[dict[str, Any]]
    rank_by: str
    numerical_policy: dict[str, Any]
    evaluator: dict[str, Any]


class LockFile(_Strict):
    kind: Literal["qccd.lock"]
    version: Literal[1]
    workspace_id: str
    mode: Literal["task", "exploratory"]
    task: dict[str, str]
    evaluator: dict[str, Any]
    schemas: dict[str, str]
    numerical_policy: Optional[dict[str, Any]]


StageStatus = Literal["passed", "failed", "partial", "skipped", "unsupported", "timeout", "cancelled",
                      "internal_error"]


class Diagnostic(_Strict):
    id: str
    severity: str
    stage: str
    message: str
    entities: Optional[list[str]] = None
    instructions: Optional[list[Any]] = None
    pointer: Optional[str] = None
    rule: Optional[str] = None
    reference: Optional[dict[str, Any]] = None


class Stage(_Strict):
    id: Literal["bundle", "device", "physics_lock", "program", "rules", "correspondence", "certificate_binding",
                "lean_certificate", "semantics", "metrics"]
    required: bool
    status: StageStatus
    coverage: str
    detail: dict[str, Any]
    diagnostics: list[Diagnostic]


class Metric(_Strict):
    value: Any
    unit: str
    better: Optional[Literal["low", "high"]]
    ranked: bool


class EvaluationReport(_Strict):
    kind: Literal["qccd.evaluation_report"]
    version: Literal[1]
    task: dict[str, str]
    bundle: dict[str, Any]
    input: dict[str, Any]
    evaluator: dict[str, Any]
    profile: Literal["draft", "reference"]
    stages: list[Stage]
    eligibility: dict[str, Any]
    metrics: dict[str, Metric]
    rank_by: Optional[str]
    cost_breakdown: dict[str, Any]
    diagnostics: list[Diagnostic]
    numerical_policy: Optional[dict[str, Any]]
    run: dict[str, Any]
    timing: dict[str, Any]


SCHEMAS = {
    "change_set_request": ChangeSetRequest, "change_set_result": ChangeSetResult, "error": ErrorBody,
    "prompt_body": PromptBody, "context_snapshot": ContextSnapshot, "bundle_manifest": BundleManifest,
    "task_release": TaskRelease, "lock": LockFile, "evaluation_report": EvaluationReport,
}


def schema_documents() -> dict:
    out = {}
    for name, model in SCHEMAS.items():
        s = model.model_json_schema()
        s["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        s["$id"] = f"https://qccd.academy/schemas/workspace/v{VERSION}/{name}.schema.json"
        out[name] = s
    return out


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    docs = schema_documents()
    if "--write" in argv:
        SCHEMA_DIR.mkdir(exist_ok=True)
        for name, s in docs.items():
            (SCHEMA_DIR / f"{name}.schema.json").write_text(json.dumps(s, indent=1, sort_keys=True) + "\n",
                                                            encoding="utf-8")
        print(f"wrote {len(docs)} schemas to {SCHEMA_DIR}")
        return 0
    stale = [n for n, s in docs.items()
             if not (SCHEMA_DIR / f"{n}.schema.json").exists()
             or json.loads((SCHEMA_DIR / f"{n}.schema.json").read_text(encoding="utf-8")) != s]
    if stale:
        print("stale schemas: " + ", ".join(stale))
        return 1
    print("schemas are current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
