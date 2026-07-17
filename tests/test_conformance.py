import copy
import json
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parents[1] / "src"))


class ConformanceTests(unittest.TestCase):
    def test_canonical_inventory_and_examples(self):
        from scene_exchange_contracts.conformance import run_conformance_suite

        report = run_conformance_suite()
        self.assertEqual(report["schema_count"], 19)
        self.assertGreaterEqual(report["example_count"], 9)

    def _example(self, version):
        from scene_exchange_contracts.validation import schema_path

        schema = json.loads(schema_path(version).read_text(encoding="utf-8"))
        return copy.deepcopy(schema["examples"][0])

    def test_rejects_multimodal_frame_with_changed_dynamic_payload(self):
        from scene_exchange_contracts.validation import (
            SharedProtocolValidationError,
            validate_document,
        )

        frame = self._example("nurec_multimodal_frame.v1")
        frame["shared_dynamic_objects"][0]["pose_pair"]["end"]["position_m"]["x"] = 9.0
        with self.assertRaisesRegex(SharedProtocolValidationError, "digest"):
            validate_document(frame)

    def test_rejects_multimodal_evidence_with_wrong_summary(self):
        from scene_exchange_contracts.validation import (
            SharedProtocolValidationError,
            validate_document,
        )

        evidence = self._example("nurec_multimodal_evidence.v1")
        evidence["modalities"]["lidar"]["passed_count"] = 0
        with self.assertRaisesRegex(SharedProtocolValidationError, "passed_count"):
            validate_document(evidence)

    def test_rejects_unexplained_unverified_runtime_track(self):
        from scene_exchange_contracts.validation import (
            SharedProtocolValidationError,
            validate_document,
        )

        inventory = self._example("nurec_runtime_track_inventory.v1")
        inventory["tracks"][0]["dynamic_object_pose_verified"] = False
        inventory["summary"]["pose_verified_track_count"] = 0
        inventory["summary"]["unverified_track_count"] = 1
        with self.assertRaisesRegex(SharedProtocolValidationError, "explain"):
            validate_document(inventory)


if __name__ == "__main__":
    unittest.main()
