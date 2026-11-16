"""Public-seam operational edge checks; fixtures are not real trading data."""
import json
from datetime import datetime, timedelta

import pytest

from decision_lab import replay_run, run_lab
from decision_lab.preview import build_preview
from test_ai_decision_lab import NOW, FixtureProvider, bundle, responses
from test_decision_lab_failure_cases import completed_run


def test_clock_objects_and_invalid_clock_do_not_orphan_run_reservations(tmp_path):
    value = bundle("clock-contract")
    with pytest.raises(ValueError, match="timezone-aware"):
        run_lab(value, FixtureProvider(), tmp_path, now=datetime(2026, 11, 15, 20))
    # Same public run identity remains usable after caller-configuration rejection.
    result = run_lab(value, FixtureProvider(), tmp_path, now=datetime.fromisoformat(NOW))
    assert result["status"] == "accepted"


def test_malformed_cio_failure_remains_integrity_verifiable(tmp_path):
    items: list[object] = list(responses())
    items[1] = float("nan")
    result = run_lab(bundle("invalid-cio-json"), FixtureProvider(items), tmp_path, now=NOW)
    assert result["failure_type"] == "invalid_json"
    replay = replay_run(result["run_dir"])
    assert replay["verified"] is True
    assert replay["verification_kind"] == "integrity_only"


def test_accepted_record_cannot_omit_its_cio_artifact(tmp_path):
    directory = completed_run(tmp_path, "missing-cio")
    response = next(directory.glob("response-??-cio.json"))
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    del manifest["files"][response.name]
    response.unlink()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="integrity"):
        replay_run(directory)


def test_post_inference_freshness_block_has_honest_integrity_only_replay(tmp_path):
    late = (datetime.fromisoformat(NOW) + timedelta(minutes=6)).isoformat()
    clocks = iter([NOW, NOW, NOW, NOW, late])
    result = run_lab(bundle("late-after-critic"), FixtureProvider(), tmp_path, now=lambda: next(clocks))
    assert result["status"] == "blocked"
    assert result["reason"] == "stale_snapshot"
    assert result["cio"]["action"] == "rebalance"
    assert replay_run(result["run_dir"])["verification_kind"] == "integrity_only"


def test_hold_rejects_wrong_cash_even_with_matching_position_notionals(tmp_path):
    items = responses(action="hold")
    items[1]["cash_weight"] = "0.49995"
    result = run_lab(bundle("hold-wrong-cash"), FixtureProvider(items), tmp_path, now=NOW)
    assert result["failure_type"] == "response_validation"


def test_negative_cash_cannot_be_laundered_as_valid_hold(tmp_path):
    value = bundle("negative-hold")
    value["portfolio"]["cash"] = "-100"
    value["portfolio"]["positions"][0]["market_value"] = "1100"
    items = responses(action="hold")
    items[1]["targets"][0]["weight"] = "1"
    items[1]["cash_weight"] = "0"
    result = run_lab(value, FixtureProvider(items), tmp_path, now=NOW)
    assert result["failure_type"] == "response_validation"


def test_preview_itself_refuses_an_unfunded_target():
    proposal = responses()[1]
    proposal["targets"][0]["weight"] = "2"
    with pytest.raises(ValueError, match="exceed authoritative equity"):
        build_preview(bundle(), proposal)


def test_concurrent_foreign_run_directory_is_never_overwritten(tmp_path):
    run_id = "publish-collision"
    final = tmp_path / run_id
    class DirectoryRaceProvider(FixtureProvider):
        def complete(self, role, payload):
            result = super().complete(role, payload)
            if role == "critic":
                final.mkdir()
                (final / "owner.txt").write_text("foreign-owner")
            return result
    provider = DirectoryRaceProvider()
    with pytest.raises(FileExistsError):
        run_lab(bundle(run_id), provider, tmp_path, now=NOW)
    assert (final / "owner.txt").read_text() == "foreign-owner"
    assert not (final / "decision.json").exists()
    assert len(provider.calls) == 3
