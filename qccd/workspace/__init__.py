"""QCCD workspaces: local-first, agent-native co-design around the studio.

Layers, from the inside out:

    design, operations    the studio's record model, replayed by the toolchain's whitelists
    core, collab, results the application layer: revisions and change sets, prompts and
                          delivery, jobs and immutable local submissions (`app.Workspace`)
    evaluator, bundle     grade(task_release, bundle, profile) -> report, transport-free
    service               the local HTTP + server-sent-events API Studio and adapters use
    mcp_server            a thin stdio MCP adapter onto the service
    agents/               session adapters (Codex app-server, Claude Code channel, pull)
    official/             the independent submission service and its workers

Nothing below `service` imports FastAPI, the MCP SDK or any agent client, so the design
and grading layers run under the plain standard-library toolchain.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

__all__ = ["evaluator_identity", "schema_versions", "EVALUATOR_NAME", "EVALUATOR_VERSION",
           "open_workspace", "init_workspace"]

EVALUATOR_NAME = "qccd-reference-evaluator"
EVALUATOR_VERSION = "1.0.0"

_PKG = Path(__file__).resolve().parent.parent


def _source_digest() -> str:
    """Digest of the Python sources the evaluator's verdict depends on.

    The replay, the rules, the cost models, the IR and the architecture loader decide
    every number a report carries; a change to any of them is a different evaluator.
    """
    h = hashlib.sha256()
    for sub in ("verify", "cost", "ir", "arch", "compile"):
        for p in sorted((_PKG / sub).rglob("*.py")):
            h.update(p.relative_to(_PKG).as_posix().encode())
            h.update(p.read_bytes().replace(b"\r\n", b"\n"))
    for p in ("api.py", "workspace/evaluator.py", "workspace/bundle.py", "workspace/metrics.py"):
        f = _PKG / p
        if f.exists():
            h.update(p.encode())
            h.update(f.read_bytes().replace(b"\r\n", b"\n"))
    return "sha256:" + h.hexdigest()


def evaluator_identity() -> dict:
    return {"name": EVALUATOR_NAME, "version": EVALUATOR_VERSION, "source_digest": _source_digest()}


def schema_versions() -> dict:
    from ..arch.schema import SCHEMA_VERSION as ARCH
    from .core import CONTRACT_VERSION
    return {"arch": ARCH, "studio": "1", "contract": CONTRACT_VERSION, "bundle": "1", "report": "1"}


def open_workspace(root):
    from .app import Workspace
    return Workspace(root)


def init_workspace(root, release_ref: str, **kw):
    from .app import Workspace
    return Workspace.init(root, release_ref, **kw)
