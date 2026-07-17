from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any


PACKAGE_SCHEMA_ROOT = Path(__file__).resolve().parent / "schemas"
SCHEMA_ROOT = PACKAGE_SCHEMA_ROOT / "shared_exchange_protocol"


class SharedProtocolValidationError(ValueError):
    """Raised when a shared_exchange_protocol.v1 document violates its Schema."""


ContractValidationError = SharedProtocolValidationError


def schema_path(schema_version: str) -> Path:
    """Return the canonical schema file for a supported document version."""

    names = {
        "scenario_ir.v1": "scenario_ir.v1.schema.json",
        "reconstruction_package.v1": "reconstruction_package.v1.schema.json",
        "closed_loop_scene_package.v1": "closed_loop_scene_package.v1.schema.json",
        "scenario_feedback.v1": "scenario_feedback.v1.schema.json",
        "runtime_alignment_evidence.v1": "runtime_alignment_evidence.v1.schema.json",
        "actor_binding_set.v1": "actor_binding_set.v1.schema.json",
        "nurec_multimodal_frame.v1": "nurec_multimodal_frame.v1.schema.json",
        "nurec_multimodal_evidence.v1": "nurec_multimodal_evidence.v1.schema.json",
        "nurec_runtime_track_inventory.v1": "nurec_runtime_track_inventory.v1.schema.json",
        "cosmos_transfer_job.v1": "cosmos_transfer_job.v1.schema.json",
    }
    if schema_version in names:
        return PACKAGE_SCHEMA_ROOT / names[schema_version]
    shared = _schema_index().get(schema_version)
    if shared is not None:
        return shared
    raise SharedProtocolValidationError(f"unsupported schema_version: {schema_version!r}")


def schema_digest(schema_version: str) -> str:
    """Return a stable digest used by projects to detect contract drift."""

    import hashlib

    return hashlib.sha256(schema_path(schema_version).read_bytes()).hexdigest()


def validate_document(document: dict[str, Any]) -> None:
    """Validate any canonical business artifact or shared envelope."""

    if not isinstance(document, dict):
        raise SharedProtocolValidationError("document must be an object")
    version = str(document.get("schema_version") or "")
    path = schema_path(version)
    _validate(document, _load(path), "$", path)
    if path.parent == SCHEMA_ROOT:
        _validate_semantics(document)
    else:
        _validate_business_semantics(document)


def _schema_index() -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in SCHEMA_ROOT.glob("*.schema.json"):
        schema = _load(path)
        version = _const_property(schema, "schema_version")
        if version:
            result[version] = path
    return result


def _const_property(schema: dict[str, Any], name: str) -> str | None:
    value = (schema.get("properties") or {}).get(name, {}).get("const")
    if isinstance(value, str):
        return value
    for child in schema.get("allOf", []):
        found = _const_property(child, name)
        if found:
            return found
    return None


def validate_shared_document(document: dict[str, Any]) -> None:
    if not isinstance(document, dict):
        raise SharedProtocolValidationError("document must be an object")
    schema_version = document.get("schema_version")
    schema_path = _schema_index().get(str(schema_version))
    if schema_path is None:
        raise SharedProtocolValidationError(
            f"unsupported shared protocol schema_version: {schema_version!r}"
        )
    _validate(document, _load(schema_path), "$", schema_path)
    _validate_semantics(document)


def validate_artifact_reference(document: dict[str, Any]) -> None:
    path = SCHEMA_ROOT / "shared_artifact_ref.schema.json"
    _validate(document, _load(path), "$", path)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_ref(reference: str, current_path: Path) -> tuple[dict[str, Any], Path]:
    file_part, separator, fragment = reference.partition("#")
    target_path = current_path if not file_part else (current_path.parent / file_part).resolve()
    try:
        target_path.relative_to(PACKAGE_SCHEMA_ROOT.resolve())
    except ValueError as exc:
        raise SharedProtocolValidationError(f"Schema reference escapes root: {reference}") from exc
    schema: Any = _load(target_path)
    if separator and fragment:
        if not fragment.startswith("/"):
            raise SharedProtocolValidationError(f"unsupported Schema fragment: {reference}")
        for raw in fragment.lstrip("/").split("/"):
            token = raw.replace("~1", "/").replace("~0", "~")
            schema = schema[token]
    if not isinstance(schema, dict):
        raise SharedProtocolValidationError(f"Schema reference is not an object: {reference}")
    return schema, target_path


def _matches(value: Any, schema: dict[str, Any], path: str, schema_path: Path) -> bool:
    try:
        _validate(value, schema, path, schema_path)
        return True
    except SharedProtocolValidationError:
        return False


def _validate(value: Any, schema: dict[str, Any], path: str, schema_path: Path) -> None:
    if "$ref" in schema:
        referenced, referenced_path = _resolve_ref(str(schema["$ref"]), schema_path)
        _validate(value, referenced, path, referenced_path)

    for child in schema.get("allOf", []):
        _validate(value, child, path, schema_path)

    if "anyOf" in schema:
        if not any(_matches(value, child, path, schema_path) for child in schema["anyOf"]):
            raise SharedProtocolValidationError(f"{path} does not match any allowed shape")
    if "oneOf" in schema:
        matches = sum(_matches(value, child, path, schema_path) for child in schema["oneOf"])
        if matches != 1:
            raise SharedProtocolValidationError(f"{path} must match exactly one allowed shape")
    if "not" in schema and _matches(value, schema["not"], path, schema_path):
        raise SharedProtocolValidationError(f"{path} matches a forbidden shape")
    if "if" in schema and _matches(value, schema["if"], path, schema_path):
        if "then" in schema:
            _validate(value, schema["then"], path, schema_path)
    elif "else" in schema:
        _validate(value, schema["else"], path, schema_path)

    if "const" in schema and value != schema["const"]:
        raise SharedProtocolValidationError(f"{path} must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise SharedProtocolValidationError(f"{path} is not an allowed value")

    expected = schema.get("type")
    if expected is not None:
        choices = expected if isinstance(expected, list) else [expected]
        if not any(_is_type(value, choice) for choice in choices):
            raise SharedProtocolValidationError(f"{path} must have type {expected!r}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        for name in required:
            if name not in value:
                raise SharedProtocolValidationError(f"{path}.{name} is required")
        properties = schema.get("properties", {})
        for name, child_schema in properties.items():
            if name in value:
                _validate(value[name], child_schema, f"{path}.{name}", schema_path)
        additional = schema.get("additionalProperties", True)
        extras = set(value) - set(properties)
        if additional is False and extras:
            raise SharedProtocolValidationError(
                f"{path} has unsupported properties: {', '.join(sorted(extras))}"
            )
        if isinstance(additional, dict):
            for name in extras:
                _validate(value[name], additional, f"{path}.{name}", schema_path)

    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            raise SharedProtocolValidationError(f"{path} has too few items")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            raise SharedProtocolValidationError(f"{path} has too many items")
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(canonical) != len(set(canonical)):
                raise SharedProtocolValidationError(f"{path} items must be unique")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                _validate(item, schema["items"], f"{path}[{index}]", schema_path)

    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            raise SharedProtocolValidationError(f"{path} is too short")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            raise SharedProtocolValidationError(f"{path} is too long")
        if "pattern" in schema and re.search(str(schema["pattern"]), value) is None:
            raise SharedProtocolValidationError(f"{path} does not match the required pattern")
        if schema.get("format") == "date-time":
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise SharedProtocolValidationError(f"{path} is not an RFC3339 timestamp") from exc
            if parsed.tzinfo is None:
                raise SharedProtocolValidationError(f"{path} timestamp must include a timezone")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SharedProtocolValidationError(f"{path} is below its minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise SharedProtocolValidationError(f"{path} is above its maximum")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            raise SharedProtocolValidationError(f"{path} must exceed its exclusive minimum")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            raise SharedProtocolValidationError(f"{path} must be below its exclusive maximum")


def _is_type(value: Any, expected: str) -> bool:
    return {
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda: isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": lambda: isinstance(value, bool),
        "null": lambda: value is None,
    }.get(expected, lambda: False)()


def _timestamp(value: str, path: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise SharedProtocolValidationError(f"{path} is not an RFC3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise SharedProtocolValidationError(f"{path} timestamp must include a timezone")
    return parsed


def _validate_semantics(document: dict[str, Any]) -> None:
    payload = document.get("payload") or {}
    message_type = document.get("message_type")

    window = payload.get("reconstruction_window")
    if isinstance(window, dict) and window.get("end_sec", 0) <= window.get("start_sec", 0):
        raise SharedProtocolValidationError(
            "$.payload.reconstruction_window.end_sec must be greater than start_sec"
        )

    if message_type == "scene.selection.request":
        correlation = document.get("correlation") or {}
        if correlation.get("root_message_id") != document.get("message_id"):
            raise SharedProtocolValidationError(
                "scene selection root_message_id must equal its message_id"
            )

    ordered_pairs = (
        ("claimed_at", "lease_expires_at"),
        ("started_at", "finished_at"),
    )
    for start_name, end_name in ordered_pairs:
        if start_name in payload and end_name in payload:
            start = _timestamp(payload[start_name], f"$.payload.{start_name}")
            end = _timestamp(payload[end_name], f"$.payload.{end_name}")
            if end <= start:
                raise SharedProtocolValidationError(
                    f"$.payload.{end_name} must be later than {start_name}"
                )


def _validate_business_semantics(document: dict[str, Any]) -> None:
    version = document.get("schema_version")
    if version == "scenario_ir.v1":
        token = document["scenario_id"]
        source = document["source"]
        dataset_source = document["dataset_refs"]["source"]
        values = (source["scene_id"], source["scene_token"], dataset_source["scene_id"])
        if any(value != token for value in values):
            raise SharedProtocolValidationError("Scenario IR scene identity fields must match")
    elif version == "reconstruction_package.v1":
        if document["source"]["scene_token"] != document["scene_id"]:
            raise SharedProtocolValidationError("Reconstruction Package scene identity fields must match")
        for window_name in ("requested_window", "actual_window"):
            window = document["coverage"][window_name]
            if window is not None and window["end_sec"] <= window["start_sec"]:
                raise SharedProtocolValidationError(f"coverage.{window_name} has invalid ordering")
    elif version == "closed_loop_scene_package.v1":
        token = document["scene_id"]
        if document["source"]["scene_id"] != token or document["source"]["scene_token"] != token:
            raise SharedProtocolValidationError("Scene Package scene identity fields must match")
        alignment = document["alignment"]
        if alignment["status"] == "runtime_validated" and not alignment.get(
            "validation_evidence"
        ):
            raise SharedProtocolValidationError(
                "runtime_validated Scene Package requires validation_evidence"
            )
    elif version == "scenario_feedback.v1":
        identity = document["identity"]
        if identity["scenario_id"] != identity["scene_token"]:
            raise SharedProtocolValidationError("Scenario feedback scene identity fields must match")
    elif version == "actor_binding_set.v1":
        _validate_actor_binding_semantics(document)
    elif version == "nurec_multimodal_frame.v1":
        _validate_nurec_multimodal_frame_semantics(document)
    elif version == "nurec_multimodal_evidence.v1":
        _validate_nurec_multimodal_evidence_semantics(document)
    elif version == "nurec_runtime_track_inventory.v1":
        _validate_nurec_runtime_track_inventory_semantics(document)
    elif version == "cosmos_transfer_job.v1":
        _validate_cosmos_transfer_job_semantics(document)


def _validate_actor_binding_semantics(document: dict[str, Any]) -> None:
    if document["source"]["scene_token"] != document["scene_id"]:
        raise SharedProtocolValidationError("Actor Binding Set scene identity fields must match")

    bindings = document["bindings"]
    unique_fields = {
        "actor_id": [item["actor_id"] for item in bindings],
        "source_track_id": [item["source_track_id"] for item in bindings],
        "carla.role_name": [item["carla"]["role_name"] for item in bindings],
        "nurec.track_id": [item["nurec"]["track_id"] for item in bindings],
    }
    for label, values in unique_fields.items():
        if len(values) != len(set(values)):
            raise SharedProtocolValidationError(f"Actor Binding Set has duplicate {label}")

    for item in bindings:
        actor_id = item["actor_id"]
        control = item["control"]
        sensor = item["sensor_sync"]
        nurec = item["nurec"]
        issues = item["issues"]
        if item["source_track_id"] != nurec["track_id"]:
            raise SharedProtocolValidationError(
                f"Actor {actor_id} NuRec track must equal the nuScenes source track"
            )
        if control["ego_responsive"] != (control["mode"] != "replay"):
            raise SharedProtocolValidationError(
                f"Actor {actor_id} control mode and ego_responsive disagree"
            )
        expected_pose_source = (
            "scenario_ir_reference_trajectory"
            if control["mode"] == "replay"
            else "carla_runtime_actor_pose"
        )
        if sensor["pose_source"] != expected_pose_source:
            raise SharedProtocolValidationError(
                f"Actor {actor_id} has the wrong sensor pose source"
            )
        if item["actor_type"] == "pedestrian" and control["mode"] == "traffic_manager":
            raise SharedProtocolValidationError(
                f"Pedestrian actor {actor_id} cannot use TrafficManager"
            )
        if item["actor_type"] == "pedestrian" and control["mode"] == "scripted":
            if control["corridor_constraint"] != "source_reference":
                raise SharedProtocolValidationError(
                    f"Scripted pedestrian actor {actor_id} must stay on its source corridor"
                )
        if item["status"] == "ready":
            if not nurec["inventory_verified"] or not nurec["dynamic_object_pose_supported"]:
                raise SharedProtocolValidationError(
                    f"Ready actor {actor_id} has no verified NuRec dynamic track"
                )
            if set(sensor["required_modalities"]) != {"rgb", "lidar"} or issues:
                raise SharedProtocolValidationError(
                    f"Ready actor {actor_id} does not satisfy RGB/LiDAR consistency"
                )

    summary = document["summary"]
    expected_summary = {
        "selected_count": len(bindings),
        "ready_count": sum(item["status"] == "ready" for item in bindings),
        "interactive_count": sum(item["control"]["ego_responsive"] for item in bindings),
        "vehicle_count": sum(item["actor_type"] == "vehicle" for item in bindings),
        "pedestrian_count": sum(item["actor_type"] == "pedestrian" for item in bindings),
    }
    if summary != expected_summary:
        raise SharedProtocolValidationError("Actor Binding Set summary does not match bindings")
    expected_readiness = (
        "empty"
        if not bindings
        else "ready"
        if expected_summary["ready_count"] == len(bindings)
        else "blocked"
    )
    readiness = document["readiness"]
    if readiness["status"] != expected_readiness:
        raise SharedProtocolValidationError("Actor Binding Set readiness does not match bindings")
    if (expected_readiness == "ready") != (not readiness["blockers"]):
        raise SharedProtocolValidationError("Actor Binding Set blockers do not match readiness")


def _validate_nurec_multimodal_frame_semantics(document: dict[str, Any]) -> None:
    interval = document["pose_interval_sec"]
    if interval["start"] > interval["end"]:
        raise SharedProtocolValidationError("NuRec pose interval starts after it ends")
    if interval["end"] != document["simulation_time_sec"]:
        raise SharedProtocolValidationError(
            "NuRec pose interval must end at simulation_time_sec"
        )

    dynamic_objects = document["shared_dynamic_objects"]
    actor_ids = [item["actor_id"] for item in dynamic_objects]
    track_ids = [item["track_id"] for item in dynamic_objects]
    if len(actor_ids) != len(set(actor_ids)):
        raise SharedProtocolValidationError("NuRec frame has duplicate actor_id")
    if len(track_ids) != len(set(track_ids)):
        raise SharedProtocolValidationError("NuRec frame has duplicate track_id")
    for item in dynamic_objects:
        _validate_pose_pair_quaternions(item["pose_pair"], f"actor {item['actor_id']}")

    digest = _canonical_sha256(dynamic_objects)
    if document["shared_dynamic_object_sha256"] != digest:
        raise SharedProtocolValidationError("NuRec dynamic-object digest does not match payload")
    request_ids: list[str] = []
    sensor_ids: list[str] = []
    for modality in ("rgb", "lidar"):
        for request in document["modalities"][modality]["requests"]:
            if request["modality"] != modality:
                raise SharedProtocolValidationError(
                    f"NuRec {modality} request declares the wrong modality"
                )
            if request["dynamic_object_sha256"] != digest:
                raise SharedProtocolValidationError(
                    f"NuRec {modality} request references different dynamic objects"
                )
            request_ids.append(request["request_id"])
            sensor_ids.append(request["sensor"]["sensor_id"])
            _validate_pose_pair_quaternions(
                request["sensor"]["pose_pair"],
                f"sensor {request['sensor']['sensor_id']}",
            )
    if len(request_ids) != len(set(request_ids)):
        raise SharedProtocolValidationError("NuRec frame has duplicate request_id")
    if len(sensor_ids) != len(set(sensor_ids)):
        raise SharedProtocolValidationError("NuRec frame has duplicate sensor_id")


def _validate_nurec_multimodal_evidence_semantics(document: dict[str, Any]) -> None:
    records = document["records"]
    request_ids = [item["request_id"] for item in records]
    if len(request_ids) != len(set(request_ids)):
        raise SharedProtocolValidationError("NuRec evidence has duplicate request_id")
    for record in records:
        record_passed = (
            record["latency_ms"] is not None
            and record["payload_sha256"] is not None
            and not record["issues"]
        )
        if (record["status"] == "passed") != record_passed:
            raise SharedProtocolValidationError(
                f"NuRec response {record['request_id']} status is inconsistent"
            )
        metadata = record.get("response_metadata")
        if metadata is not None:
            if record["modality"] == "rgb" and metadata.get("encoding") != "jpeg":
                raise SharedProtocolValidationError(
                    f"NuRec RGB response {record['request_id']} has mismatched metadata"
                )
            if (
                record["modality"] == "lidar"
                and metadata.get("encoding") != "float_xyz_intensity"
            ):
                raise SharedProtocolValidationError(
                    f"NuRec LiDAR response {record['request_id']} has mismatched metadata"
                )

    for modality in ("rgb", "lidar"):
        selected = [item for item in records if item["modality"] == modality]
        summary = document["modalities"][modality]
        if summary["requested_count"] != len(selected):
            raise SharedProtocolValidationError(
                f"NuRec {modality} requested_count is inconsistent"
            )
        passed_count = sum(item["status"] == "passed" for item in selected)
        if summary["passed_count"] != passed_count:
            raise SharedProtocolValidationError(
                f"NuRec {modality} passed_count is inconsistent"
            )
    all_passed = all(item["status"] == "passed" for item in records)
    expected_status = "passed" if all_passed and not document["issues"] else "failed"
    if document["status"] != expected_status:
        raise SharedProtocolValidationError("NuRec evidence aggregate status is inconsistent")


def _validate_nurec_runtime_track_inventory_semantics(document: dict[str, Any]) -> None:
    tracks = document["tracks"]
    track_ids = [item["track_id"] for item in tracks]
    if len(track_ids) != len(set(track_ids)):
        raise SharedProtocolValidationError("NuRec inventory has duplicate track_id")
    for item in tracks:
        verified = item["dynamic_object_pose_verified"]
        probe = item["probe"]
        if verified:
            if item["issues"] or not isinstance(probe, dict):
                raise SharedProtocolValidationError(
                    f"Verified NuRec track {item['track_id']} has incomplete probe evidence"
                )
            digest = probe["dynamic_object_sha256"]
            if probe["pose_delta_m"] < 0.05:
                raise SharedProtocolValidationError(
                    f"Verified NuRec track {item['track_id']} did not move during its probe"
                )
            for modality in ("rgb", "lidar"):
                evidence = probe["modalities"][modality]
                if evidence["status"] != "passed" or evidence["dynamic_object_sha256"] != digest:
                    raise SharedProtocolValidationError(
                        f"Verified NuRec track {item['track_id']} has invalid {modality} evidence"
                    )
        elif not item["issues"]:
            raise SharedProtocolValidationError(
                f"Unverified NuRec track {item['track_id']} must explain why it is blocked"
            )
    summary = document["summary"]
    expected = {
        "runtime_track_count": len(tracks),
        "pose_verified_track_count": sum(
            item["dynamic_object_pose_verified"] for item in tracks
        ),
        "unverified_track_count": sum(
            not item["dynamic_object_pose_verified"] for item in tracks
        ),
    }
    if summary != expected:
        raise SharedProtocolValidationError("NuRec inventory summary does not match tracks")


def _validate_pose_pair_quaternions(pair: dict[str, Any], label: str) -> None:
    for endpoint in ("start", "end"):
        orientation = pair[endpoint]["orientation_xyzw"]
        norm = math.sqrt(
            sum(float(orientation[name]) ** 2 for name in ("x", "y", "z", "w"))
        )
        if abs(norm - 1.0) > 1e-4:
            raise SharedProtocolValidationError(
                f"NuRec {label} {endpoint} orientation is not a unit quaternion"
            )


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_cosmos_transfer_job_semantics(document: dict[str, Any]) -> None:
    control_types = [item["type"] for item in document["controls"]]
    if len(control_types) != len(set(control_types)):
        raise SharedProtocolValidationError("Cosmos job has duplicate control type")
    rgb = document["source"]["rgb_video"]
    for control in document["controls"]:
        video = control["video"]
        for field in ("frame_count", "frames_per_sec", "width", "height"):
            if video[field] != rgb[field]:
                raise SharedProtocolValidationError(
                    f"Cosmos {control['type']} control {field} does not match RGB input"
                )
    boundary = document["boundary"]
    if set(boundary["forbidden_uses"]) != {
        "closed_loop_metrics",
        "safety_evidence",
        "rgb_lidar_consistency_evidence",
    }:
        raise SharedProtocolValidationError("Cosmos job must preserve the acceptance boundary")
    payload = dict(document)
    actual = payload.pop("job_id")
    if actual != _canonical_sha256(payload):
        raise SharedProtocolValidationError("Cosmos job_id does not match its payload")
