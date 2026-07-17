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

    def test_accepts_typed_runtime_response_metadata_and_rejects_cross_modality(self):
        from scene_exchange_contracts.validation import (
            SharedProtocolValidationError,
            validate_document,
        )

        evidence = self._example("nurec_multimodal_evidence.v1")
        evidence["dispatch"] = {
            "sdk_boundary": "injected_version_specific_encoder",
            "dynamic_object_verification": "encoder_echo_checked_before_rpc",
            "response_digest": "sha256_of_serialized_rpc_response",
            "response_validation": "injected_modality_specific_inspector",
            "runtime_scene_id": "scene-0061",
            "canonical_scene_id": evidence["scene_id"],
            "nre_api": "SensorsimService/26.04",
        }
        evidence["records"][0]["response_metadata"] = {
            "width": 1600,
            "height": 900,
            "encoding": "jpeg",
        }
        evidence["records"][1]["response_metadata"] = {
            "point_count": 100,
            "encoding": "float_xyz_intensity",
        }
        validate_document(evidence)

        evidence["records"][0]["response_metadata"] = evidence["records"][1][
            "response_metadata"
        ]
        with self.assertRaisesRegex(SharedProtocolValidationError, "mismatched metadata"):
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

    def test_rejects_pose_verified_track_without_rgb_and_lidar_render_delta(self):
        from scene_exchange_contracts.validation import (
            SharedProtocolValidationError,
            validate_document,
        )

        inventory = self._example("nurec_runtime_track_inventory.v1")
        rgb = inventory["tracks"][0]["probe"]["modalities"]["rgb"]
        rgb["moved_payload_sha256"] = rgb["baseline_payload_sha256"]
        rgb["content_changed"] = False
        with self.assertRaisesRegex(SharedProtocolValidationError, "no rgb render delta"):
            validate_document(inventory)


if __name__ == "__main__":
    unittest.main()
