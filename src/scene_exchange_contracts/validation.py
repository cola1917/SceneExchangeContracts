from __future__ import annotations

import json
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
