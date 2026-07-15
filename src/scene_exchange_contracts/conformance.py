from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .validation import PACKAGE_SCHEMA_ROOT, schema_digest, schema_path, validate_document


REQUIRED_VERSIONS = (
    "scenario_ir.v1",
    "reconstruction_package.v1",
    "closed_loop_scene_package.v1",
    "scenario_feedback.v1",
    "runtime_alignment_evidence.v1",
    "shared_artifact_ref.v1",
    "shared_job_request.v1",
    "shared_job_claim.v1",
    "shared_job_result.v1",
    "scene_selection_request.v1",
    "reconstruction_request.v1",
    "reconstruction_result.v1",
    "evaluation_run_request.v1",
    "evaluation_run_result.v1",
)


def run_conformance_suite() -> dict[str, Any]:
    """Run the same dependency-free contract checks in all three projects."""

    digests = {version: schema_digest(version) for version in REQUIRED_VERSIONS}
    if len(set(digests.values())) != len(digests):
        raise AssertionError("two contract versions unexpectedly have identical Schema content")

    example_count = 0
    for path in PACKAGE_SCHEMA_ROOT.rglob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        schema_id = str(schema.get("$id") or "")
        if not schema_id.startswith("https://scene-exchange-contracts.local/v1/"):
            raise AssertionError(f"noncanonical Schema ID: {path}")
        for example in schema.get("examples", []):
            validate_document(example)
            example_count += 1

    request_schema = json.loads(schema_path("reconstruction_request.v1").read_text(encoding="utf-8"))
    source = request_schema["examples"][0]["payload"]["source"]
    if source["scene_key_kind"] != "token" or len(source["scene_token"]) != 32:
        raise AssertionError("Bridge addressing is not frozen to canonical scene token")
    return {
        "schema_count": len(digests),
        "example_count": example_count,
        "digests": digests,
    }
