"""Immutable collection records, reusing the decision lab's file primitives."""
import json
from pathlib import Path

from decision_lab.storage import canonical_bytes, hash_file, publish_run, reserve_run, write_json
from decision_lab.validation import validate_bundle


ARTIFACTS = {"bundle.json", "capture.json"}
MAX_ARTIFACT_BYTES = 4_000_000


def _validate_capture(value):
    if not isinstance(value, dict) or len(canonical_bytes(value)) > MAX_ARTIFACT_BYTES:
        raise ValueError("invalid_or_oversized_capture")


def write_collection(output_dir, bundle, capture):
    """Publish only a complete collection; existing identities cannot be reused."""
    validate_bundle(bundle)
    _validate_capture(capture)
    temporary, final = reserve_run(output_dir, bundle["run_id"])
    write_json(temporary / "bundle.json", bundle)
    write_json(temporary / "capture.json", capture)
    write_json(temporary / "manifest.json", {
        "schema_version": 1,
        "collection_id": bundle["run_id"],
        "artifacts": {name: hash_file(temporary / name) for name in sorted(ARTIFACTS)},
    })
    publish_run(temporary, final)
    return final


def load_collection(run_dir):
    """Verify stored bytes only; higher-level replay must recompute evidence."""
    path = Path(run_dir)
    names = ARTIFACTS | {"manifest.json"}
    if path.is_symlink() or {item.name for item in path.iterdir()} != names:
        raise ValueError("unsafe_collection_layout")
    if any((path / name).is_symlink() or not (path / name).is_file() for name in names):
        raise ValueError("unsafe_collection_artifact")
    if any((path / name).stat().st_size > MAX_ARTIFACT_BYTES for name in names):
        raise ValueError("oversized_collection_artifact")
    manifest = json.loads((path / "manifest.json").read_text())
    if (not isinstance(manifest, dict)
            or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != 1
            or not isinstance(manifest.get("artifacts"), dict)
            or set(manifest["artifacts"]) != ARTIFACTS):
        raise ValueError("invalid_collection_manifest")
    for name in ARTIFACTS:
        if hash_file(path / name) != manifest["artifacts"][name]:
            raise ValueError("collection_artifact_hash_mismatch")
    value = json.loads((path / "bundle.json").read_text())
    validate_bundle(value)
    if manifest.get("collection_id") != value["run_id"]:
        raise ValueError("collection_identity_mismatch")
    capture = json.loads((path / "capture.json").read_text())
    _validate_capture(capture)
    return {"bundle": value, "capture": capture,
            "manifest": manifest, "verification_kind": "integrity_only"}
