"""Public-seam tests for deterministic reporter CLI integration."""
from __future__ import annotations

import json
import os
import runpy
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from prospective_evaluation.runner import verify_campaign
from prospective_evaluation.storage import register_campaign
from research_reporting.cli import main
from research_reporting.observer import ObservationError
from research_reporting.runtime import HealthError

HASH = "a" * 64
SOURCE_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 18, 5, 0, tzinfo=timezone.utc)

FROZEN_PACKAGES = ("prospective_evaluation", "prospective_evidence", "decision_lab")
FROZEN_MODULES = (
    "ai_decision_lab.py",
    "ai_decision_lab_hermes.py",
    "collect_prospective_evidence.py",
    "evaluate_prospective.py",
    "evaluate_prospective_hermes.py",
)


def frozen_scripts(tmp_path: Path) -> Path:
    """Build a distinct evaluator checkout containing no reporter source."""
    root = tmp_path / "frozen-checkout" / "scripts"
    root.mkdir(parents=True, exist_ok=True)
    for package in FROZEN_PACKAGES:
        shutil.copytree(SOURCE_ROOT / package, root / package)
    for module in FROZEN_MODULES:
        shutil.copy2(SOURCE_ROOT / module, root / module)
    assert not (root / "research_reporting").exists()
    return root


def settings(tmp_path: Path, **changes: Any) -> dict[str, Any]:
    campaign = tmp_path / "campaign"
    campaign.mkdir(parents=True, exist_ok=True)
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    value = {
        "schema_version": 1,
        "campaign_id": "reporting-fixture",
        "registration_hash": HASH,
        "campaign_path": str(campaign),
        "frozen_scripts_path": str(SOURCE_ROOT),
        "project_python": sys.executable,
        "hermes_home": str(home),
        "state_dir": str(tmp_path / "state"),
    }
    value.update(changes)
    return value


def write_config(tmp_path: Path, value: Any, *, mode: int = 0o600) -> Path:
    path = tmp_path / "reporter.json"
    path.write_text(json.dumps(value, separators=(",", ":")))
    path.chmod(mode)
    return path


def health(**changes: Any) -> dict[str, Any]:
    value = {
        "timer_active": True,
        "service_active_state": "inactive",
        "service_result": "success",
        "service_exit_status": 0,
        "service_elapsed_seconds": None,
    }
    value.update(changes)
    return value


def snapshot(status: str = "busy", **changes: Any) -> dict[str, Any]:
    value = {
        "schema_version": 1,
        "status": status,
        "observed_at": "2026-11-18T03:40:00Z",
        "campaign_id": "reporting-fixture",
        "registration_hash": HASH,
        "registered_at": None,
        "report": None,
        "verification": None,
    }
    value.update(changes)
    return value


def output(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    captured = capsys.readouterr()
    assert captured.err == ""
    return json.loads(captured.out)


def executable(path: Path, body: str) -> Path:
    path.write_text(f"#!{sys.executable}\n{body}")
    path.chmod(0o700)
    return path


def cli_environment(tmp_path: Path, *, healthy: bool = True) -> dict[str, str]:
    binaries = tmp_path / "bin"
    binaries.mkdir(parents=True, exist_ok=True)
    if healthy:
        systemctl_body = """import sys
if any(value.endswith('.service') for value in sys.argv):
    print('ActiveState=inactive')
    print('Result=success')
    print('ExecMainStatus=0')
    print('ExecMainStartTimestampMonotonic=0')
else:
    print('ActiveState=active')
"""
    else:
        systemctl_body = """import sys
print('PRIVATE_SYSTEMD_DETAIL')
print('PRIVATE_SYSTEMD_ERROR', file=sys.stderr)
raise SystemExit(1)
"""
    executable(binaries / "systemctl", systemctl_body)
    environment = os.environ.copy()
    environment["PATH"] = str(binaries) + os.pathsep + environment.get("PATH", "")
    environment["PYTHONPATH"] = str(SOURCE_ROOT)
    return environment


def run_cli(
    config: Path, environment: dict[str, str], *arguments: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SOURCE_ROOT / "research_reporting_cli.py"),
         "--config", str(config), *arguments],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=20,
    )


def test_preview_observes_health_then_clock_and_never_writes_or_sends(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    config = write_config(tmp_path, settings(tmp_path))
    calls: list[str] = []

    def observe() -> dict[str, Any]:
        calls.append("observe")
        return snapshot()

    def observe_health() -> dict[str, Any]:
        calls.append("health")
        return health()

    def clock() -> datetime:
        calls.append("clock")
        return NOW

    def send(text: str) -> dict[str, Any]:
        raise AssertionError("preview must not send")

    assert main(
        ["--config", str(config), "--preview"],
        observer=observe,
        health_observer=observe_health,
        clock=clock,
        sender=send,
    ) == 0
    assert calls == ["observe", "health", "clock"]
    assert not Path(settings(tmp_path)["state_dir"]).exists()
    assert output(capsys) == {
        "status": "preview",
        "summary": None,
        "conditions": {},
        "indeterminate_conditions": ["verification_unavailable", "stale_tick"],
        "busy": True,
    }


def test_default_observer_and_real_policy_produce_preview_from_frozen_reader(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    request = {
        "schema_version": 1,
        "campaign_id": "reporting-fixture",
        "universe": [{"symbol": "SPY", "asset_class": "etf"}],
        "model": "fixture-model",
        "research_cash": "1000",
        "max_checkpoints": 3,
    }
    campaign = register_campaign(
        tmp_path / "campaigns",
        request,
        clock=lambda: NOW - timedelta(days=1),
    )
    registration_hash = verify_campaign(campaign)["registration_hash"]
    config = write_config(tmp_path, settings(
        tmp_path,
        campaign_path=str(campaign),
        registration_hash=registration_hash,
    ))

    assert main(
        ["--config", str(config), "--preview"],
        health_observer=health,
        clock=lambda: NOW,
    ) == 1
    result = output(capsys)
    assert result["status"] == "preview"
    assert result["summary"].startswith("T² research campaign reporting-fixture")
    assert "Read-only research; no orders." in result["summary"]
    assert result["conditions"] == {
        "stale_tick": "No completed evaluator tick for more than 2700 seconds."
    }


def test_config_accepts_a_distinct_frozen_evaluator_without_reporter_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    frozen = frozen_scripts(tmp_path)
    config = write_config(
        tmp_path,
        settings(tmp_path, frozen_scripts_path=str(frozen)),
    )

    assert main(
        ["--config", str(config), "--preview"],
        observer=lambda: snapshot(),
        health_observer=health,
        clock=lambda: NOW,
    ) == 0
    assert output(capsys)["status"] == "preview"


def test_actual_cli_subprocess_previews_through_reporter_helper_and_distinct_frozen_source(
    tmp_path: Path,
) -> None:
    frozen = frozen_scripts(tmp_path)
    request = {
        "schema_version": 1,
        "campaign_id": "reporting-fixture",
        "universe": [{"symbol": "SPY", "asset_class": "etf"}],
        "model": "fixture-model",
        "research_cash": "1000",
        "max_checkpoints": 3,
    }
    campaign = register_campaign(
        tmp_path / "campaigns", request, clock=lambda: NOW - timedelta(days=1),
    )
    registration_hash = verify_campaign(campaign)["registration_hash"]
    config = write_config(tmp_path, settings(
        tmp_path,
        campaign_path=str(campaign),
        frozen_scripts_path=str(frozen),
        registration_hash=registration_hash,
    ))

    completed = run_cli(config, cli_environment(tmp_path), "--preview")

    assert completed.returncode == 1
    assert completed.stderr == ""
    result = json.loads(completed.stdout)
    assert result["status"] == "preview"
    assert result["summary"].startswith("T² research campaign reporting-fixture")
    assert "Read-only research; no orders." in result["summary"]
    assert not (frozen / "research_reporting").exists()


def test_actual_cli_subprocess_deduplicates_and_fallback_is_sanitized(tmp_path: Path) -> None:
    frozen = frozen_scripts(tmp_path)
    invalid_frozen = tmp_path / "invalid-checkout" / "scripts"
    invalid_frozen.mkdir(parents=True)
    value = settings(
        tmp_path,
        frozen_scripts_path=str(frozen),
        registration_hash="b" * 64,
    )
    config = write_config(tmp_path, value)
    environment = cli_environment(tmp_path)
    hermes = Path(environment["PATH"].split(os.pathsep, 1)[0]) / "hermes"
    executable(hermes, """import json
import os
from pathlib import Path
import sys
text = sys.stdin.read()
home = Path(os.environ['HERMES_HOME'])
state = home / 'state'
state.mkdir(parents=True, exist_ok=True)
count_path = home / 'send-count'
count = int(count_path.read_text()) + 1 if count_path.exists() else 1
count_path.write_text(str(count))
identity = f'message-{count}'
(state / 'rich_sent_index.json').write_text(json.dumps({f'photon:home:{identity}': {'t': text}}))
print(json.dumps({'success': True, 'message_id': identity}))
""")

    first = run_cli(config, environment)
    second = run_cli(config, environment)

    assert first.returncode == second.returncode == 1
    assert first.stderr == second.stderr == ""
    assert json.loads(first.stdout)["accepted_count"] == 1
    assert json.loads(second.stdout)["submitted_this_cycle"] == 0
    assert (Path(value["hermes_home"]) / "send-count").read_text() == "1"

    failed_environment = cli_environment(tmp_path / "failed-health", healthy=False)
    health_fallback = run_cli(config, failed_environment, "--preview")
    assert health_fallback.returncode == 1
    assert health_fallback.stderr == ""
    assert "PRIVATE_SYSTEMD" not in health_fallback.stdout
    assert json.loads(health_fallback.stdout)["conditions"] == {
        "verification_unavailable": (
            "Reporting verification is unavailable; evaluator state was not inferred."
        ),
    }

    invalid_value = dict(value, frozen_scripts_path=str(invalid_frozen))
    invalid_config = write_config(tmp_path / "failed-health", invalid_value)
    fallback = run_cli(invalid_config, failed_environment, "--preview")
    assert fallback.returncode == 2
    assert fallback.stderr == ""
    assert "PRIVATE_SYSTEMD" not in fallback.stdout
    assert str(invalid_frozen) not in fallback.stdout
    assert json.loads(fallback.stdout) == {"status": "invalid_configuration"}


def test_config_fifo_is_rejected_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "reporter.fifo"
    os.mkfifo(fifo, stat.S_IRUSR | stat.S_IWUSR)

    completed = subprocess.run(
        [sys.executable, str(SOURCE_ROOT / "research_reporting_cli.py"),
         "--config", str(fifo)],
        check=False,
        capture_output=True,
        text=True,
        env=cli_environment(tmp_path),
        timeout=2,
    )

    assert completed.returncode == 2
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {"status": "invalid_configuration"}


def test_regular_cycle_uses_actual_outbox_and_returns_safe_counts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    config = write_config(tmp_path, settings(tmp_path))
    received: list[str] = []

    def unavailable() -> dict[str, Any]:
        return snapshot("unavailable")

    def accepted(text: str) -> dict[str, Any]:
        received.append(text)
        return {"provider_id": "accepted-id", "verified": True}

    assert main(
        ["--config", str(config)],
        observer=unavailable,
        health_observer=health,
        clock=lambda: NOW,
        sender=accepted,
    ) == 1
    assert received == [
        "Research alert (observed at 2026-11-18T05:00:00Z / "
        "2026-11-17T23:00:00-06:00 America/Chicago):\n- "
        "Offline campaign verification is unavailable."
    ]
    assert output(capsys) == {
        "status": "processed",
        "accepted_count": 1,
        "in_doubt_count": 0,
        "submitted_this_cycle": 1,
    }

    # The real outbox deduplicates the same ongoing condition.
    assert main(
        ["--config", str(config)], observer=unavailable, health_observer=health,
        clock=lambda: NOW + timedelta(minutes=5), sender=accepted,
    ) == 1
    assert len(received) == 1
    assert output(capsys)["submitted_this_cycle"] == 0


def test_snapshot_failure_with_valid_health_is_assessed_as_unavailable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    config = write_config(tmp_path, settings(tmp_path))
    received: list[str] = []

    def failed() -> dict[str, Any]:
        raise ObservationError("private source output")

    assert main(
        ["--config", str(config)], observer=failed, health_observer=health,
        clock=lambda: NOW,
        sender=lambda text: received.append(text) or {"provider_id": "ok", "verified": True},
    ) == 1
    assert received == [
        "Research alert (observed at 2026-11-18T05:00:00Z / "
        "2026-11-17T23:00:00-06:00 America/Chicago):\n- "
        "Offline campaign verification is unavailable."
    ]
    assert output(capsys)["accepted_count"] == 1


def test_health_failure_uses_fixed_fail_closed_assessment_without_invented_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    config = write_config(tmp_path, settings(tmp_path))
    received: list[str] = []

    def failed_health() -> dict[str, Any]:
        raise HealthError("private health output")

    assert main(
        ["--config", str(config)], observer=lambda: snapshot(),
        health_observer=failed_health, clock=lambda: NOW,
        sender=lambda text: received.append(text) or {"provider_id": "ok", "verified": True},
    ) == 1
    assert received == [
        "Research alert (observed at 2026-11-18T05:00:00Z / "
        "2026-11-17T23:00:00-06:00 America/Chicago):\n- "
        "Reporting verification is unavailable; evaluator state was not inferred."
    ]
    assert output(capsys)["accepted_count"] == 1


def test_reporting_validation_failure_uses_same_bounded_fallback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    config = write_config(tmp_path, settings(tmp_path))
    assert main(
        ["--config", str(config), "--preview"],
        observer=lambda: {**snapshot(), "observed_at": "future-or-invalid"},
        health_observer=health,
        clock=lambda: NOW,
    ) == 1
    assert output(capsys) == {
        "status": "preview",
        "summary": None,
        "conditions": {
            "verification_unavailable": (
                "Reporting verification is unavailable; evaluator state was not inferred."
            )
        },
        "indeterminate_conditions": [
            "timer_disabled", "service_failed", "service_overlong", "stale_tick",
        ],
        "busy": False,
    }


def test_in_doubt_delivery_is_nonzero_and_output_does_not_leak_sender_response(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    config = write_config(tmp_path, settings(tmp_path))
    assert main(
        ["--config", str(config)], observer=lambda: snapshot("unavailable"),
        health_observer=health, clock=lambda: NOW,
        sender=lambda text: {"provider_id": "private-id", "verified": False},
    ) == 1
    result = output(capsys)
    assert result == {
        "status": "processed", "accepted_count": 0,
        "in_doubt_count": 1, "submitted_this_cycle": 1,
    }
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value, tmp: value.update(schema_version=True),
        lambda value, tmp: value.update(project_python=True),
        lambda value, tmp: value.update(extra="field"),
        lambda value, tmp: value.update(campaign_id="../escape"),
        lambda value, tmp: value.update(campaign_id="CON"),
        lambda value, tmp: value.update(campaign_id="COM1.report"),
        lambda value, tmp: value.update(registration_hash="A" * 64),
        lambda value, tmp: value.update(campaign_path="relative"),
        lambda value, tmp: value.update(campaign_path=""),
        lambda value, tmp: value.update(campaign_path=str(tmp / "missing")),
        lambda value, tmp: value.update(frozen_scripts_path=str(Path(value["frozen_scripts_path"]).parent)),
        lambda value, tmp: value.update(frozen_scripts_path=str(tmp / "missing-scripts")),
        lambda value, tmp: value.update(project_python=str(tmp / "missing-python")),
        lambda value, tmp: value.update(hermes_home=str(tmp / "missing-home")),
        lambda value, tmp: value.update(state_dir=value["campaign_path"] + "/state"),
        lambda value, tmp: value.update(campaign_path=value["state_dir"] + "/campaign"),
        lambda value, tmp: (
            (tmp / "state-file").write_text("not a directory"),
            value.update(state_dir=str(tmp / "state-file")),
        ),
    ],
)
def test_invalid_configuration_fails_generic_without_invoking_external_seams(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mutate: Any,
) -> None:
    value = settings(tmp_path)
    mutate(value, tmp_path)
    try:
        config = write_config(tmp_path, value)
    except (TypeError, ValueError):
        config = tmp_path / "reporter.json"
        config.write_text("{}")
        config.chmod(0o600)

    def forbidden() -> Any:
        raise AssertionError("invalid config must not invoke external I/O")

    assert main(
        ["--config", str(config)], observer=forbidden,
        health_observer=forbidden, clock=forbidden, sender=forbidden,
    ) == 2
    assert output(capsys) == {"status": "invalid_configuration"}


def test_config_file_security_json_and_size_are_enforced(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    cases: list[Path] = []
    permissive = write_config(tmp_path / "permissive", settings(tmp_path / "permissive"), mode=0o644)
    cases.append(permissive)

    duplicate_root = tmp_path / "duplicate"
    value = settings(duplicate_root)
    duplicate = duplicate_root / "reporter.json"
    duplicate.write_text(json.dumps(value)[:-1] + ',"schema_version":1}')
    duplicate.chmod(0o600)
    cases.append(duplicate)

    nonfinite_root = tmp_path / "nonfinite"
    value = settings(nonfinite_root)
    nonfinite = nonfinite_root / "reporter.json"
    nonfinite.write_text(json.dumps(value)[:-1] + ',"extra":NaN}')
    nonfinite.chmod(0o600)
    cases.append(nonfinite)

    large_root = tmp_path / "large"
    settings(large_root)
    large = large_root / "reporter.json"
    large.write_bytes(b" " * (64 * 1024 + 1))
    large.chmod(0o600)
    cases.append(large)

    symlink_root = tmp_path / "symlink"
    target = write_config(symlink_root, settings(symlink_root))
    link = symlink_root / "link.json"
    link.symlink_to(target)
    cases.append(link)

    directory = tmp_path / "directory-config"
    directory.mkdir()
    cases.append(directory)

    for path in cases:
        assert main(["--config", str(path)]) == 2
        assert output(capsys) == {"status": "invalid_configuration"}


def test_project_python_symlink_is_allowed_and_cli_argument_errors_are_generic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    python_link = tmp_path / "python"
    python_link.symlink_to(sys.executable)
    config = write_config(tmp_path, settings(tmp_path, project_python=str(python_link)))
    assert main(
        ["--config", str(config), "--preview"], observer=lambda: snapshot(),
        health_observer=health, clock=lambda: NOW,
    ) == 0
    output(capsys)

    assert main([]) == 2
    assert output(capsys) == {"status": "invalid_configuration"}
    assert main(["--config", str(config), "--unknown"]) == 2
    assert output(capsys) == {"status": "invalid_configuration"}


def test_default_sender_construction_and_public_outbox_failures_are_sanitized(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    config = write_config(tmp_path, settings(tmp_path))
    # No sender injection: fixed Photon adapter fails closed without leaking its exception.
    assert main(
        ["--config", str(config)], observer=lambda: snapshot("unavailable"),
        health_observer=health, clock=lambda: NOW,
    ) == 1
    result = output(capsys)
    assert result["status"] == "processed"
    assert result["in_doubt_count"] == 1

    # A clock regression is a public outbox failure, not raw JSON or a traceback.
    assert main(
        ["--config", str(config)], observer=lambda: snapshot("unavailable"),
        health_observer=health, clock=lambda: NOW - timedelta(minutes=1),
        sender=lambda text: {"provider_id": "unused", "verified": True},
    ) == 1
    assert output(capsys) == {"status": "reporting_unavailable"}


def test_clock_failure_and_bytes_config_path_fail_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    config = write_config(tmp_path, settings(tmp_path))

    def broken_clock() -> datetime:
        raise RuntimeError("private clock detail")

    assert main(
        ["--config", str(config), "--preview"], observer=lambda: snapshot(),
        health_observer=health, clock=broken_clock,
    ) == 1
    assert output(capsys)["conditions"] == {
        "verification_unavailable": (
            "Reporting verification is unavailable; evaluator state was not inferred."
        )
    }
    assert main(["--config", os.fsencode(config)]) == 2
    assert output(capsys) == {"status": "invalid_configuration"}


def test_production_entry_invokes_cli_main(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = write_config(tmp_path, settings(tmp_path))
    monkeypatch.setattr(sys, "argv", ["research_reporting_cli.py", "--config", str(config), "--preview"])
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(SOURCE_ROOT / "research_reporting_cli.py"), run_name="__main__")
    assert stopped.value.code == 1
    assert output(capsys)["status"] == "preview"


@pytest.mark.parametrize("field", ["campaign_path", "frozen_scripts_path", "project_python", "hermes_home", "state_dir"])
def test_nul_in_configured_absolute_path_is_sanitized(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], field: str,
) -> None:
    value = settings(tmp_path)
    value[field] += "\x00PRIVATE_PATH"
    config = write_config(tmp_path, value)
    assert main(["--config", str(config), "--preview"]) == 2
    assert output(capsys) == {"status": "invalid_configuration"}
    assert not (tmp_path / "state").exists()
