"""Public-seam tests for the isolated frozen-campaign snapshot reader."""
from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import runpy
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from prospective_evaluation.runner import verify_campaign
from prospective_evaluation.storage import (
    campaign_lock,
    load_campaign,
    register_campaign,
    write_record,
)
from research_reporting.read_snapshot import SnapshotError, collect, main

UTC = timezone.utc
NOW = datetime(2026, 11, 17, 3, 32, 12, 345678, tzinfo=UTC)
SOURCE_ROOT = Path(__file__).resolve().parents[1]


def _request(campaign_id: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "universe": [{"symbol": "SPY", "asset_class": "etf"}],
        "model": "fixture-model",
        "research_cash": "1000",
        "max_checkpoints": 3,
    }


def _registered(tmp_path: Path, campaign_id: str = "snapshot-fixture") -> tuple[Path, str]:
    path = register_campaign(
        tmp_path,
        _request(campaign_id),
        clock=lambda: NOW - timedelta(days=1),
    )
    return path, verify_campaign(path)["registration_hash"]


def _tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*"), key=lambda value: value.relative_to(path).as_posix()):
        relative = item.relative_to(path).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        if item.is_file():
            data = item.read_bytes()
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
    return digest.hexdigest()


def _hold_campaign_lock(path: str, ready: Any, release: Any) -> None:
    with campaign_lock(path):
        ready.send("locked")
        ready.close()
        release.wait(10)


def test_collect_returns_exact_verified_snapshot_without_mutating_campaign(tmp_path: Path) -> None:
    campaign, registration_hash = _registered(tmp_path)
    registration = load_campaign(campaign)
    with campaign_lock(campaign):
        pass
    before = _tree_hash(campaign)

    snapshot = collect(
        SOURCE_ROOT,
        campaign,
        "snapshot-fixture",
        registration_hash,
        now=NOW,
    )

    assert snapshot == {
        "schema_version": 1,
        "status": "verified",
        "observed_at": "2026-11-17T03:32:12.345678Z",
        "campaign_id": "snapshot-fixture",
        "registration_hash": registration_hash,
        "registered_at": registration["registered_at"],
        "report": snapshot["report"],
        "verification": snapshot["verification"],
    }
    assert snapshot["report"] == {
        "schema_version": 1,
        "campaign_id": "snapshot-fixture",
        "verified": True,
        "executable": False,
        "counts": {
            "observations": 0, "valid_observations": 0,
            "unavailable_observations": 0, "checkpoints": 0,
            "reservations": 0, "missed": 0, "in_doubt": 0,
            "collection_failures": 0,
        },
        "arm_status_counts": {
            "cash": {}, "equal_weight": {}, "sma20": {}, "ai_three_role": {},
        },
        "checkpoints": [],
        "caveats": {
            "price_only": True,
            "corporate_action_status": "unchecked",
            "economically_scorable": False,
            "billed_cost_absent_means": "unknown",
            "overlapping_cohorts_are_not_compounded": True,
        },
    }
    assert snapshot["verification"] == {
        "schema_version": 1,
        "campaign_id": "snapshot-fixture",
        "verified": True,
        "verification_kind": "offline_semantic_reconstruction",
        "registration_hash": registration_hash,
        "observation_count": 0,
        "valid_observation_count": 0,
        "unavailable_observation_count": 0,
        "checkpoint_count": 0,
        "event_count": 0,
        "tick_count": 0,
        "checkpoints": [],
        "events": [],
        "ticks": [],
        "executable": False,
    }
    assert _tree_hash(campaign) == before


def test_collect_reports_real_cross_process_lock_contention_as_busy(tmp_path: Path) -> None:
    campaign, registration_hash = _registered(tmp_path, "busy-snapshot")
    context = multiprocessing.get_context("spawn")
    ready_parent, ready_child = context.Pipe(duplex=False)
    release = context.Event()
    process = context.Process(
        target=_hold_campaign_lock,
        args=(str(campaign), ready_child, release),
    )
    process.start()
    ready_child.close()
    assert ready_parent.recv() == "locked"
    ready_parent.close()
    try:
        snapshot = collect(SOURCE_ROOT, campaign, "busy-snapshot", registration_hash, now=NOW)
    finally:
        release.set()
        process.join(10)

    assert process.exitcode == 0
    assert snapshot == {
        "schema_version": 1,
        "status": "busy",
        "observed_at": "2026-11-17T03:32:12.345678Z",
        "campaign_id": "busy-snapshot",
        "registration_hash": registration_hash,
        "registered_at": None,
        "report": None,
        "verification": None,
    }


def test_default_clock_records_availability_after_each_completed_observation(
    tmp_path: Path,
) -> None:
    delayed_source = tmp_path / "delayed-source"
    delayed_source.mkdir()
    _copy_frozen_source(delayed_source)
    delay = 0.15
    with (delayed_source / "ai_decision_lab.py").open("a") as handle:
        handle.write(f"\n__import__('time').sleep({delay!r})\n")
    campaign, registration_hash = _register_with_selected_source(
        delayed_source, tmp_path / "delayed-store", "clock-snapshot",
    )

    def observe(path: Path) -> dict[str, Any]:
        started = datetime.now(UTC)
        result = collect(
            delayed_source, path, "clock-snapshot", registration_hash,
        )
        completed = datetime.now(UTC)
        observed_at = datetime.fromisoformat(result["observed_at"].replace("Z", "+00:00"))
        assert started + timedelta(seconds=delay) <= observed_at <= completed
        return result

    assert observe(campaign)["status"] == "verified"
    assert observe(campaign / "missing")["status"] == "unavailable"

    context = multiprocessing.get_context("spawn")
    ready_parent, ready_child = context.Pipe(duplex=False)
    release = context.Event()
    process = context.Process(
        target=_hold_campaign_lock,
        args=(str(campaign), ready_child, release),
    )
    process.start()
    ready_child.close()
    assert ready_parent.recv() == "locked"
    ready_parent.close()
    try:
        assert observe(campaign)["status"] == "busy"
    finally:
        release.set()
        process.join(10)
    assert process.exitcode == 0


def test_collect_sanitizes_observation_failures_and_rejects_bad_caller_configuration(
    tmp_path: Path, capsys: Any,
) -> None:
    noisy = tmp_path / "noisy"
    noisy.mkdir()
    _copy_frozen_source(noisy)
    campaign, registration_hash = _registered(noisy, "unavailable-snapshot")
    with (noisy / "prospective_evaluation" / "runner.py").open("a") as handle:
        handle.write('\nprint("private-source-output")\n')
    unavailable = collect(
        SOURCE_ROOT,
        campaign / "missing-private-name",
        "unavailable-snapshot",
        registration_hash,
        now=NOW,
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert unavailable == {
        "schema_version": 1,
        "status": "unavailable",
        "observed_at": "2026-11-17T03:32:12.345678Z",
        "campaign_id": "unavailable-snapshot",
        "registration_hash": registration_hash,
        "registered_at": None,
        "report": None,
        "verification": None,
    }
    assert "missing-private-name" not in json.dumps(unavailable)

    cases = (
        (SOURCE_ROOT / "missing-source", campaign, "unavailable-snapshot", registration_hash, NOW),
        (SOURCE_ROOT, campaign, "bad/id", registration_hash, NOW),
        (SOURCE_ROOT, campaign, "unavailable-snapshot", "not-a-hash", NOW),
        (SOURCE_ROOT, campaign, "unavailable-snapshot", registration_hash, datetime(2026, 11, 17)),
        (SOURCE_ROOT, campaign, "unavailable-snapshot", registration_hash, "not-a-clock"),
    )
    for source_root, path, campaign_id, expected_hash, clock in cases:
        with pytest.raises(SnapshotError, match="^invalid snapshot configuration$"):
            collect(source_root, path, campaign_id, expected_hash, now=clock)

    invalid_root: Any = None
    invalid_campaign: Any = None
    with pytest.raises(SnapshotError, match="^invalid snapshot configuration$"):
        collect(invalid_root, campaign, "unavailable-snapshot", registration_hash, now=NOW)
    with pytest.raises(SnapshotError, match="^invalid snapshot configuration$"):
        collect(SOURCE_ROOT, invalid_campaign, "unavailable-snapshot", registration_hash, now=NOW)

    implicit_clock = collect(
        SOURCE_ROOT, campaign / "missing", "unavailable-snapshot", registration_hash,
    )
    assert implicit_clock["status"] == "unavailable"
    assert implicit_clock["observed_at"].endswith("Z")


def test_collect_rejects_verified_identity_mismatch_as_unavailable(tmp_path: Path) -> None:
    campaign, registration_hash = _registered(tmp_path, "identity-snapshot")

    for campaign_id, expected_hash in (
        ("other-expected-id", registration_hash),
        ("identity-snapshot", "0" * 64),
    ):
        snapshot = collect(SOURCE_ROOT, campaign, campaign_id, expected_hash, now=NOW)
        assert snapshot["status"] == "unavailable"
        assert snapshot["report"] is None
        assert snapshot["verification"] is None
        assert snapshot["campaign_id"] == campaign_id
        assert snapshot["registration_hash"] == expected_hash


def _copy_frozen_source(destination: Path) -> None:
    for name in ("prospective_evaluation", "prospective_evidence", "decision_lab"):
        shutil.copytree(SOURCE_ROOT / name, destination / name)
    for source in SOURCE_ROOT.glob("*.py"):
        if not source.name.startswith("test") and (
            "prospective" in source.stem or "decision_lab" in source.stem
        ):
            shutil.copy2(source, destination / source.name)


def _register_with_selected_source(
    source_root: Path, store: Path, campaign_id: str,
) -> tuple[Path, str]:
    script = """
import json, sys
from datetime import datetime, timezone
sys.path.insert(0, sys.argv[1])
from prospective_evaluation import register_campaign
from prospective_evaluation.runner import verify_campaign
request = {
    "schema_version": 1,
    "campaign_id": sys.argv[3],
    "universe": [{"symbol": "SPY", "asset_class": "etf"}],
    "model": "fixture-model",
    "research_cash": "1000",
    "max_checkpoints": 3,
}
path = register_campaign(
    sys.argv[2], request,
    clock=lambda: datetime(2026, 11, 16, 3, 32, 12, tzinfo=timezone.utc),
)
print(json.dumps({"path": str(path), "hash": verify_campaign(path)["registration_hash"]}))
"""
    environment = dict(os.environ)
    environment.pop("T2_ALT_DEPENDENCY_FAIL", None)
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script, str(source_root), str(store), campaign_id],
        check=True, capture_output=True, text=True, env=environment,
    )
    result = json.loads(completed.stdout)
    return Path(result["path"]), result["hash"]


def test_selected_source_origin_is_enforced_and_alternate_checkout_runs_in_child(
    tmp_path: Path,
) -> None:
    original_campaign, original_hash = _registered(tmp_path / "original", "original-snapshot")
    alternate = tmp_path / "alternate"
    alternate.mkdir()
    _copy_frozen_source(alternate)
    for relative in (
        Path("prospective_evidence/archive.py"),
        Path("decision_lab/core.py"),
    ):
        with (alternate / relative).open("a") as handle:
            handle.write(
                '\nif __import__("os").environ.get("T2_ALT_DEPENDENCY_FAIL") == "1":\n'
                '    raise RuntimeError("alternate frozen dependency selected")\n'
            )
    campaign, registration_hash = _register_with_selected_source(
        alternate, tmp_path / "alternate-store", "origin-snapshot",
    )

    os.environ["T2_ALT_DEPENDENCY_FAIL"] = "1"
    try:
        alternate_result = collect(
            alternate, campaign, "origin-snapshot", registration_hash, now=NOW,
        )
    finally:
        del os.environ["T2_ALT_DEPENDENCY_FAIL"]
    assert alternate_result["status"] == "unavailable"

    assert verify_campaign(original_campaign)["registration_hash"] == original_hash

    environment = {**os.environ, "T2_ALT_DEPENDENCY_FAIL": "1"}
    completed = subprocess.run(
        [sys.executable, "-I", str(Path(__file__).with_name("read_snapshot.py")),
         str(alternate), str(campaign),
         "origin-snapshot", registration_hash],
        capture_output=True,
        text=True,
        env=environment,
    )
    alternate_snapshot = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert alternate_snapshot["status"] == "unavailable"

    assert verify_campaign(original_campaign)["registration_hash"] == original_hash

    false_root = tmp_path / "false-root"
    false_root.mkdir()
    assert collect(false_root, campaign, "origin-snapshot", registration_hash, now=NOW)[
        "status"
    ] == "unavailable"
    (false_root / "prospective_evaluation").mkdir()
    (false_root / "prospective_evaluation" / "__init__.py").write_text("")
    assert collect(false_root, campaign, "origin-snapshot", registration_hash, now=NOW)["status"] == "unavailable"

    wrong_origin = tmp_path / "wrong-origin"
    wrong_origin.mkdir()
    _copy_frozen_source(wrong_origin)
    linked_runner = wrong_origin / "prospective_evaluation" / "runner.py"
    linked_runner.unlink()
    linked_runner.symlink_to(SOURCE_ROOT / "prospective_evaluation" / "runner.py")
    assert collect(wrong_origin, campaign, "origin-snapshot", registration_hash, now=NOW)[
        "status"
    ] == "unavailable"

    interrupted = tmp_path / "interrupted"
    interrupted.mkdir()
    _copy_frozen_source(interrupted)
    with (interrupted / "prospective_evaluation" / "runner.py").open("a") as handle:
        handle.write("\nraise KeyboardInterrupt\n")
    with pytest.raises(KeyboardInterrupt):
        collect(interrupted, campaign, "origin-snapshot", registration_hash, now=NOW)


def test_snapshot_output_is_bounded_with_public_campaign_records(
    tmp_path: Path, capsys: Any,
) -> None:
    campaign, registration_hash = _registered(tmp_path, "bounded-snapshot")
    for index in range(5):
        write_record(
            campaign,
            "events",
            f"large-{index}",
            {"schema_version": 1, "payload": "x" * 3_500_000},
        )

    snapshot = collect(
        SOURCE_ROOT, campaign, "bounded-snapshot", registration_hash, now=NOW,
    )
    assert snapshot["status"] == "verified"
    arguments = [str(SOURCE_ROOT), str(campaign), "bounded-snapshot", registration_hash]
    assert main(arguments, now=NOW) == 1
    output = capsys.readouterr().out.encode("utf-8")
    assert len(output) <= 16 * 1024 * 1024
    assert json.loads(output) == {
        "schema_version": 1,
        "status": "unavailable",
        "error": "output_too_large",
    }


def test_cli_public_seam_emits_json_and_safe_exit_codes(tmp_path: Path, capsys: Any) -> None:
    campaign, registration_hash = _registered(tmp_path, "cli-snapshot")
    arguments = [str(SOURCE_ROOT), str(campaign), "cli-snapshot", registration_hash]

    assert main(arguments, now=NOW) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "verified"

    assert main([str(SOURCE_ROOT), str(campaign / "missing"), "cli-snapshot", registration_hash], now=NOW) == 1
    failure = json.loads(capsys.readouterr().out)
    assert failure["status"] == "unavailable"
    assert "missing" not in json.dumps(failure)

    assert main([str(SOURCE_ROOT), str(campaign), "cli-snapshot", "bad"], now=NOW) == 2
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": 1, "status": "unavailable", "error": "invalid_configuration",
    }

    assert main([], now=NOW) == 2
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": 1, "status": "unavailable", "error": "invalid_configuration",
    }

    saved_argv = sys.argv
    sys.argv = [str(Path(__file__).with_name("read_snapshot.py"))]
    try:
        with pytest.raises(SystemExit, match="2"):
            runpy.run_path(sys.argv[0], run_name="__main__")
    finally:
        sys.argv = saved_argv
