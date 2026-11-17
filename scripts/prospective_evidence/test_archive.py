"""Synthetic input fixtures test collection persistence, not real market data."""
from copy import deepcopy
import json

import pytest

from test_ai_decision_lab import bundle
from prospective_evidence import archive


def test_collection_round_trip_does_not_modify_caller(tmp_path):
    value = bundle("fixture-collection")
    capture = {"fixture": True, "sources": []}
    original = deepcopy((value, capture))
    path = archive.write_collection(tmp_path, value, capture)
    loaded = archive.load_collection(path)
    assert loaded["bundle"] == value
    assert loaded["capture"] == capture
    assert loaded["verification_kind"] == "integrity_only"
    assert (value, capture) == original


@pytest.mark.parametrize("change", ["extra", "run_link", "artifact_link", "manifest_link", "schema", "identity", "artifact_set", "bytes"])
def test_invalid_or_unsafe_collection_is_rejected(tmp_path, change):
    path = archive.write_collection(tmp_path, bundle("fixture-unsafe"), {"fixture": True})
    if change == "extra":
        (path / "unexpected.json").write_text("{}")
    elif change == "run_link":
        link = tmp_path / "linked"
        link.symlink_to(path, target_is_directory=True)
        path = link
    elif change in {"artifact_link", "manifest_link"}:
        name = "capture.json" if change == "artifact_link" else "manifest.json"
        data = (path / name).read_bytes()
        outside = tmp_path / "outside"
        outside.write_bytes(data)
        (path / name).unlink()
        (path / name).symlink_to(outside)
    elif change == "bytes":
        (path / "capture.json").write_text("{}")
    else:
        manifest = json.loads((path / "manifest.json").read_text())
        if change == "schema":
            manifest["schema_version"] = True
        elif change == "identity":
            manifest["collection_id"] = "different"
        else:
            manifest["artifacts"]["unexpected.json"] = "ignored"
        (path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        archive.load_collection(path)


def test_nonobject_capture_and_duplicate_identity_are_rejected(tmp_path):
    value = bundle("fixture-duplicate")
    with pytest.raises(ValueError):
        archive.write_collection(tmp_path, value, [])
    archive.write_collection(tmp_path, value, {"fixture": True})
    with pytest.raises(FileExistsError):
        archive.write_collection(tmp_path, value, {"fixture": True})


def test_oversized_capture_rejected_before_reservation(tmp_path):
    with pytest.raises(ValueError):
        archive.write_collection(tmp_path, bundle("fixture-large"), {"data": "x" * 4_000_001})
    assert not list(tmp_path.iterdir())


def test_oversized_artifact_rejected_before_read_or_hash(tmp_path, monkeypatch):
    path = archive.write_collection(tmp_path, bundle("fixture-large-read"), {})
    (path / "capture.json").write_text("x" * 4_000_001)
    def forbidden(_):
        raise AssertionError("oversized artifact must not be read")
    monkeypatch.setattr(archive, "hash_file", forbidden)
    with pytest.raises(ValueError):
        archive.load_collection(path)


@pytest.mark.parametrize("capture", [[], {"invalid": float("nan")}])
def test_rehashed_invalid_capture_still_rejected(tmp_path, capture):
    path = archive.write_collection(tmp_path, bundle("fixture-capture-shape"), {})
    (path / "capture.json").write_text(json.dumps(capture))
    manifest = json.loads((path / "manifest.json").read_text())
    manifest["artifacts"]["capture.json"] = archive.hash_file(path / "capture.json")
    (path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        archive.load_collection(path)
