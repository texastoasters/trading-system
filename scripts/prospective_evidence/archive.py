"""Immutable collection records, reusing the decision lab's file primitives."""
import json
from pathlib import Path

from decision_lab.storage import canonical_bytes, hash_file, publish_run, reserve_run, write_json
from decision_lab.validation import RUN_ID_RE, validate_bundle


ARTIFACTS = {"bundle.json", "capture.json"}
MAX_ARTIFACT_BYTES = 4_000_000
FAILURE_CODES = {"capture_incomplete", "market_normalization_failed", "calendar_normalization_failed", "invalid_experiment", "collection_failed"}


def _validate_capture(value):
    if not isinstance(value, dict) or len(canonical_bytes(value)) > MAX_ARTIFACT_BYTES:
        raise ValueError("invalid_or_oversized_capture")


def _publish(output_dir, identity, files, status, error):
    if not isinstance(identity, str) or not RUN_ID_RE.fullmatch(identity):
        raise ValueError("invalid_collection_identity")
    _validate_capture(files["capture.json"])
    temporary, final = reserve_run(output_dir, identity)
    for name, value in files.items():
        write_json(temporary / name, value)
    write_json(temporary / "manifest.json", {
        "schema_version": 1, "collection_id": identity, "status": status, "error": error,
        "artifacts": {name: hash_file(temporary / name) for name in sorted(files)},
    })
    publish_run(temporary, final)
    return final


def write_collection(output_dir, bundle, capture):
    """Publish only a complete collection; existing identities cannot be reused."""
    validate_bundle(bundle)
    return _publish(output_dir, bundle["run_id"], {"bundle.json": bundle, "capture.json": capture}, "complete", None)


def write_failed_collection(output_dir, collection_id, capture, *, error_code):
    """Preserve sanitized partial-source proof without producing runnable input."""
    code = error_code if isinstance(error_code, str) and error_code in FAILURE_CODES else "collection_failed"
    return _publish(output_dir, collection_id, {"capture.json": capture}, "failed", code)


def load_collection(run_dir):
    """Verify stored bytes only; higher-level replay must recompute evidence."""
    path = Path(run_dir)
    names = {item.name for item in path.iterdir()}
    if path.is_symlink() or names not in (ARTIFACTS | {"manifest.json"}, {"capture.json", "manifest.json"}):
        raise ValueError("unsafe_collection_layout")
    if any((path / name).is_symlink() or not (path / name).is_file() for name in names):
        raise ValueError("unsafe_collection_artifact")
    if any((path / name).stat().st_size > MAX_ARTIFACT_BYTES for name in names):
        raise ValueError("oversized_collection_artifact")
    manifest = json.loads((path / "manifest.json").read_text())
    if (not isinstance(manifest, dict)
            or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != 1
            or not isinstance(manifest.get("collection_id"), str)
            or not RUN_ID_RE.fullmatch(manifest["collection_id"])
            or not isinstance(manifest.get("artifacts"), dict)
            or set(manifest["artifacts"]) != names - {"manifest.json"}
            or manifest.get("status") not in ("complete", "failed")):
        raise ValueError("invalid_collection_manifest")
    status = manifest["status"]
    if (status == "complete") != ("bundle.json" in names):
        raise ValueError("collection_status_layout_mismatch")
    for name in manifest["artifacts"]:
        if hash_file(path / name) != manifest["artifacts"][name]:
            raise ValueError("collection_artifact_hash_mismatch")
    value = None
    if status == "complete":
        value = json.loads((path / "bundle.json").read_text())
        validate_bundle(value)
        if manifest.get("collection_id") != value["run_id"]:
            raise ValueError("collection_identity_mismatch")
    elif not isinstance(manifest.get("error"), str) or manifest["error"] not in FAILURE_CODES:
        raise ValueError("invalid_collection_failure_code")
    capture = json.loads((path / "capture.json").read_text())
    _validate_capture(capture)
    return {"status": status, "bundle": value, "capture": capture,
            "manifest": manifest, "verification_kind": "integrity_only"}
