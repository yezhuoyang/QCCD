"""Evidence boundary tests: mutation, stale certificates, and incomplete checking."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from qccd.compile.stages import (
    STAGE_IDS, StageCheck, StageIntegrityError, StageRecorder, verify_manifest,
)


def accepted():
    return StageCheck("passed", "independent-test-checker", ("declared test obligation",))


def write_manifest(path, value):
    body = {key: item for key, item in value.items() if key != "manifest_sha256"}
    payload = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    value["manifest_sha256"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    path.write_text(json.dumps(value), encoding="utf-8")


class CompilationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.context = {"architecture": {"capacity": 2}, "rules": ["capacity", "broadcast"],
                        "model": {"version": "test-1"}, "compiler": "test-build"}
        self.recorder = StageRecorder(self.root, self.context)

    def record(self, stage_id, **kwargs):
        return self.recorder.record(stage_id, {"program": [0]}, {"program": [0, 1]},
                                    {"replayed": True}, kwargs.pop("check", accepted()), **kwargs)

    def complete(self, omit=()):
        for stage_id in STAGE_IDS:
            if stage_id not in omit:
                self.record(stage_id)

    def test_full_pipeline_round_trip_and_external_anchor(self):
        self.complete()
        manifest = self.recorder.finish()
        report = verify_manifest(self.root / "manifest.json", expected_context=self.context,
                                 expected_manifest_sha256=manifest["manifest_sha256"])
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["record_count"], 12)
        self.assertFalse(report["semantic_checkers_rerun"])

    def test_stage_scope_does_not_promote_an_incomplete_compiler(self):
        self.recorder = StageRecorder(self.root, self.context,
                                      required_stage_ids=["circuit_normalization"])
        self.record("circuit_normalization")
        manifest = self.recorder.finish()
        self.assertEqual(manifest["scope_status"], "passed")
        self.assertEqual(manifest["status"], "incomplete")
        self.assertFalse(manifest["full_pipeline"])

    def test_context_and_artifacts_are_detached_snapshots(self):
        self.context["architecture"]["capacity"] = 100
        program = {"ops": [1]}
        record = self.recorder.record("circuit_normalization", program, program, {}, accepted())
        program["ops"].append(2)
        record["check"]["status"] = "failed"
        record["predecessors"].append("bad")
        manifest = self.recorder.finish()
        saved = json.loads((self.root / "objects" / (record["input_sha256"] + ".json")).read_text())
        self.assertEqual(saved, {"ops": [1]})
        self.assertEqual(manifest["status"], "incomplete")
        with self.assertRaisesRegex(StageIntegrityError, "different compilation context"):
            verify_manifest(self.root / "manifest.json", expected_context=self.context)

    def test_output_mutation_is_detected(self):
        record = self.record("circuit_normalization")
        self.recorder.finish()
        output = self.root / "objects" / (record["output_sha256"] + ".json")
        output.write_text('{"program":[999]}', encoding="utf-8")
        with self.assertRaisesRegex(StageIntegrityError, "Object digest mismatch"):
            verify_manifest(self.root / "manifest.json")

    def test_missing_witness_is_detected(self):
        record = self.record("circuit_normalization")
        self.recorder.finish()
        (self.root / "objects" / (record["witness_sha256"] + ".json")).unlink()
        with self.assertRaisesRegex(StageIntegrityError, "Cannot read evidence"):
            verify_manifest(self.root / "manifest.json")

    def test_existing_content_address_cannot_be_overwritten(self):
        record = self.record("circuit_normalization")
        (self.root / "objects" / (record["input_sha256"] + ".json")).write_text("null")
        with self.assertRaisesRegex(StageIntegrityError, "Immutable object was changed"):
            self.record("dependency_analysis")

    def test_reordered_records_fail_even_with_rehashed_manifest(self):
        self.record("circuit_normalization")
        self.record("dependency_analysis")
        manifest = self.recorder.finish()
        manifest["records"].reverse()
        write_manifest(self.root / "manifest.json", manifest)
        with self.assertRaisesRegex(StageIntegrityError, "Record chain"):
            verify_manifest(self.root / "manifest.json")

    def test_false_status_is_rejected_even_with_rehashed_manifest(self):
        self.record("circuit_normalization", check=StageCheck("failed", "checker", (), ("mismatch",)))
        manifest = self.recorder.finish()
        manifest["status"] = "passed"
        write_manifest(self.root / "manifest.json", manifest)
        with self.assertRaisesRegex(StageIntegrityError, "Reported compilation status"):
            verify_manifest(self.root / "manifest.json")

    def test_trusted_anchor_detects_replacement_manifest(self):
        self.record("circuit_normalization")
        manifest = self.recorder.finish()
        anchor = manifest["manifest_sha256"]
        manifest["integrity_scope"] = "changed metadata"
        write_manifest(self.root / "manifest.json", manifest)
        with self.assertRaisesRegex(StageIntegrityError, "trusted external digest"):
            verify_manifest(self.root / "manifest.json", expected_manifest_sha256=anchor)

    def test_retry_invalidates_all_dependent_stages_but_keeps_history(self):
        first = self.record("circuit_normalization")
        second = self.record("dependency_analysis")
        third = self.record("initial_placement")
        retried = self.record("dependency_analysis")
        self.assertEqual(set(retried["invalidated"]), {second["sha256"], third["sha256"]})
        self.assertEqual(retried["predecessors"], [first["sha256"]])
        manifest = self.recorder.finish()
        self.assertEqual(len(manifest["records"]), 4)
        self.assertNotIn("initial_placement", manifest["active"])
        self.assertTrue((self.root / "objects" / (third["sha256"] + ".json")).exists())
        self.assertEqual(verify_manifest(self.root / "manifest.json")["active_record_count"], 2)

    def test_explicit_dependencies_preserve_an_independent_branch(self):
        first = self.record("circuit_normalization")
        second = self.record("dependency_analysis", predecessors=[first["sha256"]])
        third = self.record("initial_placement", predecessors=[first["sha256"]])
        retried = self.record("dependency_analysis", predecessors=[first["sha256"]])
        self.assertEqual(retried["invalidated"], [second["sha256"]])
        self.assertIn(third["sha256"], self.recorder.finish()["active"].values())

    def test_stale_or_future_dependencies_are_rejected(self):
        old = self.record("circuit_normalization")
        self.record("circuit_normalization")
        for parents in ([old["sha256"]], ["0" * 64]):
            with self.assertRaisesRegex(ValueError, "currently active"):
                self.record("dependency_analysis", predecessors=parents)

    def test_repeated_syndrome_rounds_remain_independent_instances(self):
        records = []
        for round_id in (0, 1):
            for stage in STAGE_IDS[3:7]:
                records.append(self.record(stage, iteration={"round": round_id}))
        manifest = self.recorder.finish()
        self.assertEqual(len(manifest["active"]), 8)
        self.assertEqual(records[4]["predecessors"], [records[3]["sha256"]])
        self.assertTrue(all(not record["invalidated"] for record in records))

    def test_cooling_repair_invalidates_old_timing_and_thermal_evidence(self):
        for stage in STAGE_IDS[:9]:
            self.record(stage)
        self.record("thermal_analysis", check=StageCheck("failed", "thermal-replay", (), ("hot",)))
        cooling = self.record("cooling_insertion")
        repaired = self.record("operating_point_binding")
        self.assertIn(cooling["sha256"], repaired["invalidated"])
        for stage in ("event_scheduling", "thermal_analysis", "artifact_validation"):
            self.record(stage)
        manifest = self.recorder.finish(conditional_stages={
            "cooling_insertion": "Final thermal replay needs no further cooling."})
        self.assertEqual(manifest["status"], "passed")
        self.assertEqual(len(manifest["records"]), 15)

    def test_missing_cooling_requires_explicit_condition(self):
        self.complete(omit={"cooling_insertion"})
        self.assertEqual(self.recorder.finish()["status"], "incomplete")
        self.assertEqual(self.recorder.finish(conditional_stages={
            "cooling_insertion": "Independent thermal replay passed without cooling."})["status"], "passed")
        with self.assertRaisesRegex(ValueError, "mandatory"):
            self.recorder.finish(conditional_stages={"native_gate_lowering": "not implemented"})
        with self.assertRaisesRegex(ValueError, "explicit reason"):
            self.recorder.finish(conditional_stages={"cooling_insertion": ""})

    def test_partial_unsupported_and_failed_checks_cannot_pass(self):
        for status, expected in (("partial", "partial"), ("unsupported", "incomplete"),
                                 ("failed", "failed"), ("not_run", "incomplete")):
            with self.subTest(status=status):
                self.recorder = StageRecorder(self.root / status, self.context)
                for stage in STAGE_IDS:
                    check = StageCheck(status, "checker", ("subset",)) if stage == STAGE_IDS[0] else accepted()
                    self.record(stage, check=check)
                self.assertEqual(self.recorder.finish()["status"], expected)

    def test_inadequate_claims_are_explicitly_partial(self):
        for check in ({"status": "passed", "coverage": ["capacity"]},
                      {"status": "passed", "method": "checker", "coverage": []},
                      {"status": "passed", "method": "checker", "coverage": "capacity",
                       "limitations": ["No gate semantics verification"]}):
            with self.subTest(check=check):
                record = self.record("circuit_normalization", check=check)
                self.assertEqual(record["check"]["status"], "partial")
                self.assertTrue(record["check"]["messages"])
                self.recorder.finish()

    def test_diagnostics_alias_is_preserved(self):
        record = self.record("dependency_analysis", check={
            "status": "failed", "method": "dependency-reconstruction", "coverage": "classical edges",
            "diagnostics": ["Measurement control edge missing"]})
        self.assertEqual(record["check"]["messages"], ["Measurement control edge missing"])
        self.assertEqual(record["check"]["coverage"], ["classical edges"])

    def test_mandatory_stage_cannot_be_marked_not_applicable(self):
        with self.assertRaisesRegex(ValueError, "not a conditional"):
            self.record("artifact_validation", check=StageCheck("not_applicable", "policy", ("skip",)))

    def test_manifest_path_cannot_escape_evidence_directory(self):
        with self.assertRaisesRegex(ValueError, "within the evidence directory"):
            self.recorder.finish("../manifest.json")

    def test_nonfinite_artifact_values_are_rejected(self):
        with self.assertRaises(ValueError):
            self.recorder.record("circuit_normalization", {"angle": float("nan")}, {}, {}, accepted())


if __name__ == "__main__":
    unittest.main()
