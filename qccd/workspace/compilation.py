"""Validate the compilation evidence bound to a workspace program reference.

A compiler label or an accepted report alone is insufficient: reuse requires the
complete immutable stage chain and all three executable/certificate artifacts to
match its final checked boundary. This validates stored evidence, not new semantics.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Protocol

from qccd.compile.stages import verify_evidence

from .bundle import _FILE_LIMITS as PROGRAM_LIMITS
from .jsonsafe import canonical_bytes, strict_loads

__all__ = ["has_stage_evidence"]

_ARTIFACT = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SNAPSHOT = re.compile(r"[0-9a-f]{64}\Z")


class _ArtifactStore(Protocol):
    def get_artifact(self, digest: str) -> bytes: ...


def has_stage_evidence(ws: _ArtifactStore, ref: Mapping[str, Any]) -> bool:
    """Return whether ``ref`` is bound to complete, accepted compilation evidence.

    ``ref`` names ``artifact``, ``certified``, ``certificate``, and ``compilation``
    workspace digests. The compilation reference contains ``program``, ``certified``,
    ``certificate``, ``report``, ``manifest``, ``manifest_sha256``, and an ``objects``
    mapping from stage snapshot digest to workspace artifact digest. Evidence objects
    are stored as their original bytes so stage hashes remain exact. Program bindings
    use the workspace's canonical number normalization, consistently with adoption.

    Missing, malformed, incomplete, replaced, or stale evidence returns ``False``.
    No per-workspace cache is retained: each call detects subsequent artifact damage.
    """
    try:
        if not isinstance(ref, Mapping):
            return False
        documents: dict[str, Any] = {}

        def artifact(digest: str) -> Any:
            if not isinstance(digest, str) or not _ARTIFACT.fullmatch(digest):
                raise ValueError("Invalid workspace artifact digest")
            if digest not in documents:
                # The workspace store checks these bytes against the requested digest.
                documents[digest] = strict_loads(ws.get_artifact(digest), PROGRAM_LIMITS)
            return documents[digest]

        reference = artifact(ref["compilation"])
        if (reference["schema"] != "qccd.compilation.reference.v1" or
                reference["status"] != "passed"):
            return False
        for stored, requested in (("program", "artifact"), ("certified", "certified"),
                                  ("certificate", "certificate")):
            if reference[stored] != ref[requested]:
                return False

        report = artifact(reference["report"])
        manifest = artifact(reference["manifest"])
        anchor = reference["manifest_sha256"]
        if (not isinstance(anchor, str) or not _SNAPSHOT.fullmatch(anchor) or
                report["schema"] != "qccd.compilation.report.v1" or
                report["accepted"] is not True or report["status"] != "passed" or
                report["manifest_sha256"] != anchor):
            return False
        objects = reference["objects"]
        if not isinstance(objects, dict) or any(
            not isinstance(key, str) or not _SNAPSHOT.fullmatch(key) or
            not isinstance(value, str) or not _ARTIFACT.fullmatch(value)
            for key, value in objects.items()
        ):
            return False

        def snapshot(digest: str) -> Any:
            return artifact(objects[digest])

        integrity = verify_evidence(manifest, snapshot, expected_manifest_sha256=anchor)
        if integrity["status"] != "passed" or not integrity["full_pipeline"]:
            return False
        # Reports are ordinary workspace JSON and may normalize 1.0 to 1; compare
        # their declared context canonically without changing the hashed snapshots.
        context = snapshot(manifest["context_sha256"])
        if canonical_bytes(report["context"]) != canonical_bytes(context):
            return False

        finals = [snapshot(digest) for digest in manifest["active"].values()
                  if snapshot(digest)["stage_id"] == "artifact_validation"]
        if len(finals) != 1 or finals[0]["check"]["status"] != "passed":
            return False
        final = finals[0]
        inputs = snapshot(final["input_sha256"])
        output = snapshot(final["output_sha256"])
        witness = snapshot(final["witness_sha256"])
        for actual, checked in (
            (artifact(ref["artifact"]), output),
            (artifact(ref["certified"]), witness["certified_baseline"]),
            (artifact(ref["certificate"]), inputs["certificate"]),
        ):
            if canonical_bytes(actual) != canonical_bytes(checked):
                return False
        return True
    except Exception:
        # This is a reuse predicate at the workspace trust boundary. Store errors,
        # rejected JSON, and invalid evidence all mean the program needs recompiling.
        return False
