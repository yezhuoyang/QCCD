"""Workspace adoption/reuse requires evidence bound to the actual artifacts."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from qccd.compile.stages import STAGE_IDS, StageCheck, StageRecorder
from qccd.workspace.compilation import has_stage_evidence
from qccd.workspace.jsonsafe import canonical_bytes


class MemoryWorkspace:
    """The same requested-digest integrity guarantee as Workspace.get_artifact."""

    def __init__(self):
        self.data = {}

    def put(self, payload):
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        self.data[digest] = payload
        return digest

    def put_json(self, value):
        return self.put(canonical_bytes(value))

    def get_artifact(self, digest):
        payload = self.data[digest]
        if "sha256:" + hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError("Corrupt workspace artifact")
        return payload


class WorkspaceCompilationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)
        self.ws = MemoryWorkspace()
        self.context = {"source_sha256": "b" * 64, "rules": ["capacity", "broadcast"],
                        "physical_model": {"heating": 1.0}, "compiler": "test-build"}
        self.program = {"instructions": [{"type": "gate", "angle": 1.0, "t0": 0.0, "t1": 1.0}]}
        self.certified = {"instructions": [{"type": "gate", "angle": 1.0}]}
        self.certificate = {"map": [[0, "q0"]], "circuit_sha256": "b" * 64}
        self.build_reference()

    def build_reference(self, *, omit=(), partial=None):
        recorder = StageRecorder(self.path, self.context)
        for stage in STAGE_IDS:
            if stage in omit:
                continue
            check = StageCheck("partial" if stage == partial else "passed",
                               "independent-fixture-checker", ("fixture obligation",))
            if stage == "artifact_validation":
                recorder.record(stage, {"native": self.certified, "certificate": self.certificate},
                                self.program, {"certified_baseline": self.certified}, check)
            else:
                recorder.record(stage, {"step": stage}, {"duration": 1.0}, {}, check)
        self.manifest = recorder.finish()
        self.report = {"schema": "qccd.compilation.report.v1", "status": "passed", "accepted": True,
                       "manifest_sha256": self.manifest["manifest_sha256"], "context": self.context}
        self.reference = {
            "schema": "qccd.compilation.reference.v1", "status": "passed",
            "program": self.ws.put_json(self.program), "certified": self.ws.put_json(self.certified),
            "certificate": self.ws.put_json(self.certificate), "report": self.ws.put_json(self.report),
            "manifest": self.ws.put((self.path / "manifest.json").read_bytes()),
            "manifest_sha256": self.manifest["manifest_sha256"],
            "objects": {path.stem: self.ws.put(path.read_bytes())
                        for path in (self.path / "objects").glob("*.json")},
        }
        self.ref = {"artifact": self.reference["program"], "certified": self.reference["certified"],
                    "certificate": self.reference["certificate"]}
        self.save_reference()

    def save_reference(self):
        self.ref["compilation"] = self.ws.put_json(self.reference)

    def save_report(self):
        self.reference["report"] = self.ws.put_json(self.report)
        self.save_reference()

    def test_valid_evidence_accepts_workspace_numeric_normalization(self):
        # Stage snapshots keep 1.0; canonical workspace program/report artifacts use 1.
        self.assertIn(b'"heating":1.0', self.ws.get_artifact(
            self.reference["objects"][self.manifest["context_sha256"]]))
        self.assertIn(b'"heating":1', self.ws.get_artifact(self.reference["report"]))
        self.assertTrue(has_stage_evidence(self.ws, self.ref))

    def test_legacy_or_boolean_only_claims_do_not_establish_evidence(self):
        for ref in ({}, None, {"verified": True}, {**self.ref, "compilation": True},
                    {key: value for key, value in self.ref.items() if key != "compilation"}):
            with self.subTest(ref=ref):
                self.assertFalse(has_stage_evidence(self.ws, ref))

    def test_program_swap_fails_even_when_reference_labels_are_consistent(self):
        changed = copy.deepcopy(self.program)
        changed["instructions"][0]["angle"] = 2.0
        self.reference["program"] = self.ref["artifact"] = self.ws.put_json(changed)
        self.save_reference()
        self.assertFalse(has_stage_evidence(self.ws, self.ref))

    def test_certified_baseline_swap_is_rejected(self):
        changed = {"instructions": []}
        self.reference["certified"] = self.ref["certified"] = self.ws.put_json(changed)
        self.save_reference()
        self.assertFalse(has_stage_evidence(self.ws, self.ref))

    def test_certificate_swap_is_rejected(self):
        changed = {**self.certificate, "map": [[0, "q1"]]}
        self.reference["certificate"] = self.ref["certificate"] = self.ws.put_json(changed)
        self.save_reference()
        self.assertFalse(has_stage_evidence(self.ws, self.ref))

    def test_reference_for_different_output_is_rejected(self):
        self.ref["artifact"] = self.ws.put_json({"instructions": []})
        self.assertFalse(has_stage_evidence(self.ws, self.ref))

    def test_missing_object_is_not_silently_accepted(self):
        final = json.loads(self.ws.get_artifact(self.reference["objects"][self.manifest["records"][-1]]))
        del self.reference["objects"][final["witness_sha256"]]
        self.save_reference()
        self.assertFalse(has_stage_evidence(self.ws, self.ref))

    def test_valid_workspace_artifact_with_wrong_snapshot_digest_is_rejected(self):
        self.reference["objects"][self.manifest["context_sha256"]] = self.ws.put_json({"forged": True})
        self.save_reference()
        self.assertFalse(has_stage_evidence(self.ws, self.ref))

    def test_normalizing_snapshot_objects_breaks_their_exact_digest(self):
        context = self.manifest["context_sha256"]
        self.reference["objects"][context] = self.ws.put_json(self.context)
        self.save_reference()
        self.assertFalse(has_stage_evidence(self.ws, self.ref))

    def test_report_context_must_match_the_verified_chain(self):
        self.report["context"] = {**self.context, "compiler": "another-build"}
        self.save_report()
        self.assertFalse(has_stage_evidence(self.ws, self.ref))

    def test_report_schema_status_and_acceptance_are_all_required(self):
        original = copy.deepcopy(self.report)
        for key, value in (("schema", "other-report"), ("accepted", False),
                           ("accepted", 1), ("status", "partial"),
                           ("manifest_sha256", "f" * 64)):
            with self.subTest(key=key, value=value):
                self.report = {**original, key: value}
                self.save_report()
                self.assertFalse(has_stage_evidence(self.ws, self.ref))

    def test_reference_schema_and_status_are_required(self):
        original = copy.deepcopy(self.reference)
        for key, value in (("schema", "legacy-reference"), ("status", "partial")):
            with self.subTest(key=key):
                self.reference = {**original, key: value}
                self.save_reference()
                self.assertFalse(has_stage_evidence(self.ws, self.ref))

    def test_false_accepted_report_cannot_hide_partial_checks(self):
        self.build_reference(partial="native_gate_lowering")
        self.assertTrue(self.report["accepted"])
        self.assertEqual(self.manifest["status"], "partial")
        self.assertFalse(has_stage_evidence(self.ws, self.ref))

    def test_false_accepted_report_cannot_hide_missing_stage(self):
        self.build_reference(omit={"dependency_analysis"})
        self.assertFalse(has_stage_evidence(self.ws, self.ref))

    def test_swapped_record_chain_is_rejected(self):
        self.manifest["records"][0:2] = reversed(self.manifest["records"][0:2])
        body = {key: value for key, value in self.manifest.items() if key != "manifest_sha256"}
        raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        anchor = hashlib.sha256(raw).hexdigest()
        self.manifest["manifest_sha256"] = anchor
        self.reference["manifest"] = self.ws.put_json(self.manifest)
        self.reference["manifest_sha256"] = self.report["manifest_sha256"] = anchor
        self.save_report()
        self.assertFalse(has_stage_evidence(self.ws, self.ref))

    def test_changed_active_final_stage_is_rejected(self):
        self.manifest["active"]["artifact_validation"] = self.manifest["records"][0]
        self.reference["manifest"] = self.ws.put_json(self.manifest)
        self.save_reference()
        self.assertFalse(has_stage_evidence(self.ws, self.ref))

    def test_artifact_corruption_after_success_is_detected_without_cache(self):
        self.assertTrue(has_stage_evidence(self.ws, self.ref))
        digest = self.reference["objects"][self.manifest["records"][0]]
        self.ws.data[digest] += b" "
        self.assertFalse(has_stage_evidence(self.ws, self.ref))

    def test_malformed_duplicate_key_report_is_rejected(self):
        self.reference["report"] = self.ws.put(b'{"accepted":false,"accepted":true}')
        self.save_reference()
        self.assertFalse(has_stage_evidence(self.ws, self.ref))

    def test_missing_workspace_artifact_returns_false(self):
        del self.ws.data[self.ref["certificate"]]
        self.assertFalse(has_stage_evidence(self.ws, self.ref))

    def test_malformed_object_references_return_false(self):
        self.reference["objects"] = {"../../manifest": "sha256:" + "0" * 64}
        self.save_reference()
        self.assertFalse(has_stage_evidence(self.ws, self.ref))


if __name__ == "__main__":
    unittest.main()
