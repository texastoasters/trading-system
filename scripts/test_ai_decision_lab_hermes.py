"""Managed launcher tests use an explicit fixture, not a real model call."""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import ai_decision_lab_hermes as launcher


def test_managed_command_preserves_isolation_and_quotes_script_path():
    script = Path("/fixture/path with spaces/ai_decision_lab.py")
    runtime = [sys.executable, "-I", "-c", "import runpy,sys; " + launcher.ENTRYPOINT]
    result = launcher.managed_command(runtime, script, ["validate", "input with spaces.json"])
    assert result[:3] == [sys.executable, "-I", "-c"]
    assert result[-2:] == ["validate", "input with spaces.json"]
    assert "hermes_cli.main" not in result[3]
    assert repr(str(script)) in result[3]


@pytest.mark.parametrize("runtime", [[], [sys.executable, "-c", "unsafe"], [sys.executable, "-I", "-c", "unknown-bootstrap"], [sys.executable, "-I", "-c", launcher.ENTRYPOINT + launcher.ENTRYPOINT]])
def test_unknown_runtime_format_fails_closed(runtime):
    with pytest.raises(ValueError, match="unsupported_hermes_runtime_format"):
        launcher.managed_command(runtime, Path("/fixture/cli.py"), [])


def test_launcher_forwards_exit_status_without_shell_interpolation(monkeypatch):
    calls = []
    def fixture_run(command, **options):
        calls.append((command, options))
        if len(calls) == 1:
            return SimpleNamespace(stdout=json.dumps([sys.executable, "-I", "-c", "import runpy,sys; " + launcher.ENTRYPOINT]))
        return SimpleNamespace(returncode=2)
    monkeypatch.setattr(launcher.subprocess, "run", fixture_run)
    assert launcher.main(["validate", "; not a shell command"]) == 2
    assert len(calls) == 2
    assert calls[1][0][-1] == "; not a shell command"
    assert "shell" not in calls[1][1]


def test_launcher_never_echoes_provider_or_runtime_exception_bodies(monkeypatch, capsys):
    def unavailable(*args, **kwargs):
        raise RuntimeError("fixture-secret-do-not-print")
    monkeypatch.setattr(launcher.subprocess, "run", unavailable)
    assert launcher.main([]) == 2
    assert "fixture-secret" not in capsys.readouterr().out
