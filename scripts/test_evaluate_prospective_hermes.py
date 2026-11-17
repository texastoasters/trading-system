"""The convenience entrypoint keeps campaign dependencies in project Python."""
import importlib
import json
import subprocess

import pytest


def test_alias_script_entrypoint_is_project_cli(monkeypatch, capsys):
    import runpy
    import sys

    alias = importlib.import_module("evaluate_prospective_hermes")
    assert isinstance(alias.__file__, str)
    monkeypatch.setattr(sys, "argv", [alias.__file__, "--help"])
    with pytest.raises(SystemExit) as caught:
        runpy.run_path(alias.__file__, run_name="__main__")
    assert caught.value.code == 0
    assert "register" in capsys.readouterr().out


def test_registration_uses_project_dependencies_without_sdk_bootstrap(tmp_path, monkeypatch, capsys):
    alias = importlib.import_module("evaluate_prospective_hermes")
    request = tmp_path / "protocol.json"
    request.write_text(json.dumps({
        "schema_version": 1, "campaign_id": "alias-fixture",
        "universe": [{"symbol": "SPY", "asset_class": "etf"}],
        "model": "openai/gpt-6.1-sol", "research_cash": "100", "max_checkpoints": 20,
    }))

    def no_sdk(*args, **kwargs):
        raise AssertionError("registration must not invoke the SDK runtime")

    monkeypatch.setattr(subprocess, "run", no_sdk)
    assert alias.main(["register", "--protocol", str(request), "--output-dir", str(tmp_path / "out")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {"status": "registered", "executable": False,
                      "campaign_path": str(tmp_path / "out" / "alias-fixture")}


def test_help_stays_in_project_environment_without_sdk_discovery(monkeypatch, capsys):
    alias = importlib.import_module("evaluate_prospective_hermes")

    def no_subprocess(*args, **kwargs):
        raise AssertionError("the campaign must not be re-executed in the SDK closure")

    monkeypatch.setattr(subprocess, "run", no_subprocess)
    assert alias.main(["--help"]) == 0
    assert "register" in capsys.readouterr().out
