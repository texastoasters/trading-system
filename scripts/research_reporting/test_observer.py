"""Public-seam tests for the isolated snapshot subprocess observer."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from prospective_evaluation.runner import verify_campaign
from prospective_evaluation.storage import register_campaign
from research_reporting.observer import ObservationError, SnapshotObserver

NOW = datetime(2026, 11, 17, 4, 8, tzinfo=timezone.utc)
HASH = "a" * 64
SOURCE_ROOT = Path(__file__).resolve().parents[1]
FROZEN_PACKAGES = ("prospective_evaluation", "prospective_evidence", "decision_lab")
FROZEN_MODULES = (
    "ai_decision_lab.py",
    "ai_decision_lab_hermes.py",
    "collect_prospective_evidence.py",
    "evaluate_prospective.py",
    "evaluate_prospective_hermes.py",
)


def frozen_scripts(tmp_path: Path) -> Path:
    root = tmp_path / "selected-checkout" / "scripts"
    root.mkdir(parents=True)
    for package in FROZEN_PACKAGES:
        shutil.copytree(SOURCE_ROOT / package, root / package)
    for module in FROZEN_MODULES:
        shutil.copy2(SOURCE_ROOT / module, root / module)
    assert not (root / "research_reporting").exists()
    return root


def config(tmp_path: Path, **changes: Any) -> dict[str, Any]:
    campaign = tmp_path / "campaign"
    campaign.mkdir(exist_ok=True)
    state = tmp_path / "state"
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    value = {
        "schema_version": 1,
        "campaign_id": "reporting-fixture",
        "registration_hash": HASH,
        "campaign_path": str(campaign),
        "frozen_scripts_path": str(SOURCE_ROOT),
        "project_python": sys.executable,
        "hermes_home": str(home),
        "state_dir": str(state),
    }
    value.update(changes)
    return value


def dto(status: str = "busy") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": status,
        "observed_at": "2026-11-17T04:08:00.000000Z",
        "campaign_id": "reporting-fixture",
        "registration_hash": HASH,
        "registered_at": None,
        "report": None,
        "verification": None,
    }


def response(value: Any, *, code: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        returncode=code,
        stdout=(json.dumps(value, separators=(",", ":")) + "\n").encode(),
    )


def test_observer_runs_only_the_fixed_isolated_reader_command(tmp_path: Path) -> None:
    calls: list[tuple[list[str], int, int]] = []

    def runner(command: list[str], *, timeout: int, stdout_limit: int):
        calls.append((command, timeout, stdout_limit))
        return response(dto())

    settings = config(tmp_path)
    result = SnapshotObserver(settings, runner=runner)()

    assert result == dto()
    assert calls == [(
        [
            sys.executable,
            "-I",
            str(Path(__file__).with_name("read_snapshot.py")),
            str(SOURCE_ROOT),
            settings["campaign_path"],
            "reporting-fixture",
            HASH,
        ],
        90,
        16 * 1024 * 1024,
    )]


def test_default_observer_executes_actual_reader_subprocess(tmp_path: Path) -> None:
    request = {
        "schema_version": 1,
        "campaign_id": "reporting-fixture",
        "universe": [{"symbol": "SPY", "asset_class": "etf"}],
        "model": "fixture-model",
        "research_cash": "1000",
        "max_checkpoints": 3,
    }
    campaign = register_campaign(
        tmp_path,
        request,
        clock=lambda: NOW - timedelta(days=1),
    )
    registration_hash = verify_campaign(campaign)["registration_hash"]
    frozen = frozen_scripts(tmp_path)
    settings = config(
        tmp_path,
        campaign_path=str(campaign),
        frozen_scripts_path=str(frozen),
        registration_hash=registration_hash,
    )

    result = SnapshotObserver(settings)()

    assert not (frozen / "research_reporting").exists()
    assert result["status"] == "verified"
    assert result["campaign_id"] == "reporting-fixture"
    assert result["registration_hash"] == registration_hash
    assert result["report"]["verified"] is True
    assert result["verification"]["executable"] is False


@pytest.mark.parametrize(
    "returned",
    [
        SimpleNamespace(returncode=True, stdout=b"{}"),
        SimpleNamespace(returncode=0, stdout="not-bytes"),
        SimpleNamespace(returncode=0, stdout=b"not-json"),
        SimpleNamespace(returncode=0, stdout=b'{"schema_version":1,"schema_version":1}'),
        SimpleNamespace(returncode=0, stdout=b'{"value":NaN}'),
        response({**dto(), "extra": None}),
        response({**dto(), "schema_version": True}),
        response({**dto(), "status": "verified"}),
        response({**dto(), "campaign_id": "other"}),
        response({**dto(), "registration_hash": "b" * 64}),
        response({**dto(), "observed_at": "not-a-time"}),
        response({**dto(), "registered_at": 7}),
        response({**dto(), "report": {}}),
        response(dto(), code=1),
        response(dto("unavailable"), code=0),
        response(dto(), code=2),
    ],
)
def test_observer_rejects_malformed_or_process_mismatched_results(
    tmp_path: Path, returned: SimpleNamespace,
) -> None:
    with pytest.raises(ObservationError, match="observation_unavailable"):
        SnapshotObserver(config(tmp_path), runner=lambda *args, **kwargs: returned)()


def test_observer_accepts_unavailable_and_verified_shapes(tmp_path: Path) -> None:
    unavailable = dto("unavailable")
    verified = {
        **dto("verified"),
        "registered_at": "2026-11-16T04:08:00Z",
        "report": {
            "schema_version": 1,
            "campaign_id": "reporting-fixture",
            "verified": True,
            "executable": False,
        },
        "verification": {
            "schema_version": 1,
            "campaign_id": "reporting-fixture",
            "registration_hash": HASH,
            "verified": True,
            "executable": False,
        },
    }
    assert SnapshotObserver(
        config(tmp_path), runner=lambda *args, **kwargs: response(unavailable, code=1),
    )() == unavailable
    assert SnapshotObserver(
        config(tmp_path), runner=lambda *args, **kwargs: response(verified),
    )() == verified


@pytest.mark.parametrize(
    "bad_config",
    [
        None,
        {},
        {"schema_version": 1},
    ],
)
def test_observer_rejects_invalid_configuration(tmp_path: Path, bad_config: Any) -> None:
    with pytest.raises(ObservationError, match="observation_unavailable"):
        SnapshotObserver(bad_config)()


def test_observer_normalizes_runner_failures_and_enforces_real_output_bound(tmp_path: Path) -> None:
    def failed(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired("private command", 90, stderr=b"private stderr")

    with pytest.raises(ObservationError, match="observation_unavailable"):
        SnapshotObserver(config(tmp_path), runner=failed)()

    script = tmp_path / "oversize.py"
    script.write_text("import sys;sys.stdout.buffer.write(b'x'*(16*1024*1024+1))")
    settings = config(tmp_path, project_python=sys.executable)
    settings["frozen_scripts_path"] = str(SOURCE_ROOT)
    observer = SnapshotObserver(settings)
    command_settings = dict(settings)
    # Point the fixed Python executable at a wrapper that ignores reader arguments.
    wrapper = tmp_path / "python-wrapper"
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {script}\n")
    wrapper.chmod(0o700)
    command_settings["project_python"] = str(wrapper)
    with pytest.raises(ObservationError, match="observation_unavailable"):
        SnapshotObserver(command_settings)()


def test_observer_does_not_swallow_process_ending_exceptions(tmp_path: Path) -> None:
    def interrupted(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        SnapshotObserver(config(tmp_path), runner=interrupted)()
