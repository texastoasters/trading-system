"""CLI contracts are exercised in-process; all model data are synthetic fixtures."""
import json
import runpy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import ai_decision_lab as cli
import ai_decision_lab_hermes as managed
from test_ai_decision_lab import NOW, FixtureProvider, bundle, responses


def input_file(tmp_path, value):
    path = tmp_path / "input.json"
    path.write_text(json.dumps(value))
    return str(path)


def fake_provider_module(monkeypatch, provider):
    module = ModuleType("decision_lab.providers")
    setattr(module, "create_provider", lambda model: provider)
    monkeypatch.setitem(sys.modules, "decision_lab.providers", module)


def test_cli_validate_run_and_replay_share_the_public_contract(tmp_path, monkeypatch, capsys):
    source = input_file(tmp_path, bundle("cli-contract"))
    assert cli.main(["validate", source, "--now", NOW]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
    provider = FixtureProvider()
    fake_provider_module(monkeypatch, provider)
    output = tmp_path / "runs"
    assert cli.main(["run", source, "--model", "fixture", "--output", str(output), "--now", NOW]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["executable"] is False
    assert cli.main(["replay", result["run_dir"]]) == 0
    assert json.loads(capsys.readouterr().out)["replayed"] is True


def test_cli_nonzero_results_and_parser_errors_are_not_success(tmp_path, monkeypatch, capsys):
    bad = input_file(tmp_path, [])
    assert cli.main(["validate", bad]) == 2
    assert json.loads(capsys.readouterr().out)["failure_type"] == "input_error"
    assert cli.main(["replay", str(tmp_path / "absent")]) == 2
    assert json.loads(capsys.readouterr().out)["failure_type"] == "replay_error"
    value = bundle("cli-blocked")
    value["max_calls"] = 1
    source = input_file(tmp_path, value)
    provider = FixtureProvider()
    fake_provider_module(monkeypatch, provider)
    assert cli.main(["run", source, "--model", "fixture", "--output", str(tmp_path / "blocked"), "--now", NOW]) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "call_budget_exhausted"
    assert provider.calls == []


def test_cli_provider_constructor_failure_never_echoes_its_secret_body(tmp_path, monkeypatch, capsys):
    source = input_file(tmp_path, bundle())
    module = ModuleType("decision_lab.providers")
    def fail(model):
        raise RuntimeError("fixture-secret-never-echo")
    setattr(module, "create_provider", fail)
    monkeypatch.setitem(sys.modules, "decision_lab.providers", module)
    assert cli.main(["run", source, "--model", "fixture", "--output", str(tmp_path / "runs")]) == 2
    output = capsys.readouterr().out
    assert "fixture-secret" not in output
    assert json.loads(output)["failure_type"] == "provider_or_run_error"


def test_cli_optional_roles_really_use_single_cio(tmp_path, monkeypatch, capsys):
    source = input_file(tmp_path, bundle("single-cio-cli"))
    provider = FixtureProvider([responses()[1]])
    fake_provider_module(monkeypatch, provider)
    assert cli.main(["run", source, "--model", "fixture", "--output", str(tmp_path / "runs"), "--now", NOW, "--no-research", "--no-critic"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["critic"] is None
    assert [role for role, payload in provider.calls] == ["cio"]


def test_offline_script_entrypoint_uses_the_same_cli(tmp_path, monkeypatch, capsys):
    source = input_file(tmp_path, bundle("entrypoint"))
    monkeypatch.setattr(sys, "argv", [cli.__file__, "validate", source, "--now", NOW])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(cli.__file__, run_name="__main__")
    assert exit_info.value.code == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True


def test_managed_script_entrypoint_forwards_the_completed_runtime_status(monkeypatch):
    calls = []
    def fake_run(command, **options):
        calls.append(command)
        if len(calls) == 1:
            runtime = [sys.executable, "-I", "-c", "import runpy,sys; " + managed.ENTRYPOINT]
            return SimpleNamespace(stdout=json.dumps(runtime))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(managed.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", [managed.__file__, "validate", "fixture.json"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(managed.__file__, run_name="__main__")
    assert exit_info.value.code == 0
    assert len(calls) == 2
