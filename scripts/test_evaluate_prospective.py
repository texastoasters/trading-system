"""Public CLI tests; no broker, model, or scheduler side effects."""
import importlib
import json

import pytest


def test_invalid_command_is_sanitized_json_without_echoing_arguments(capsys):
    cli = importlib.import_module("evaluate_prospective")
    assert cli.main(["tick", "--untrusted-secret=value-do-not-echo"]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"status": "failed", "error": "invalid_command", "executable": False}
    assert captured.err == ""
    assert "value-do-not-echo" not in captured.out


def test_register_creates_actual_protocol_before_any_future_observation(tmp_path, capsys):
    """Injected historical clock is a labelled synthetic registration fixture."""
    from datetime import datetime, timezone

    cli = importlib.import_module("evaluate_prospective")
    request = {
        "schema_version": 1, "campaign_id": "synthetic-cli-campaign",
        "universe": [{"symbol": "SPY", "asset_class": "etf"}],
        "model": "openai/gpt-6.1-sol", "research_cash": "100000.00", "max_checkpoints": 20,
    }
    source = tmp_path / "request.json"
    source.write_text(json.dumps(request))
    args = ["register", "--protocol", str(source), "--output-dir", str(tmp_path / "campaigns")]
    clock = lambda: datetime(2026, 11, 16, 22, tzinfo=timezone.utc)
    assert cli.main(args, clock=clock) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "status": "registered", "executable": False,
        "campaign_path": str(tmp_path / "campaigns" / "synthetic-cli-campaign"),
    }
    assert cli.main(args, clock=clock) == 1
    failure = json.loads(capsys.readouterr().out)
    assert failure["status"] == "failed"
    assert failure["executable"] is False


def test_duplicate_protocol_keys_are_rejected_before_registration(tmp_path, capsys):
    cli = importlib.import_module("evaluate_prospective")
    request = tmp_path / "duplicate.json"
    request.write_text('{"schema_version":1,"campaign_id":"duplicate-fixture",'
                       '"universe":[{"symbol":"SPY","asset_class":"etf"}],'
                       '"model":"first","model":"second","research_cash":"100",'
                       '"max_checkpoints":20}')
    assert cli.main(["register", "--protocol", str(request), "--output-dir", str(tmp_path / "out")]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"


@pytest.mark.parametrize("delta, expected_code", [(-1, 0), (0, 0), (1, 1)])
def test_protocol_file_size_boundary_is_literal(tmp_path, capsys, delta, expected_code):
    cli = importlib.import_module("evaluate_prospective")
    request = {
        "schema_version": 1, "campaign_id": "size-fixture",
        "universe": [{"symbol": "SPY", "asset_class": "etf"}],
        "model": "openai/gpt-6.1-sol", "research_cash": "100", "max_checkpoints": 20,
    }
    raw = json.dumps(request).encode()
    raw += b" " * (64 * 1024 + delta - len(raw))
    source = tmp_path / "sized.json"
    source.write_bytes(raw)
    assert cli.main(["register", "--protocol", str(source), "--output-dir", str(tmp_path / "out")]) == expected_code
    assert json.loads(capsys.readouterr().out)["executable"] is False


def test_protocol_symlink_is_rejected(tmp_path, capsys):
    cli = importlib.import_module("evaluate_prospective")
    target = tmp_path / "real.json"
    target.write_text(json.dumps({
        "schema_version": 1, "campaign_id": "symlink-fixture",
        "universe": [{"symbol": "SPY", "asset_class": "etf"}],
        "model": "openai/gpt-6.1-sol", "research_cash": "100", "max_checkpoints": 20,
    }))
    source = tmp_path / "linked.json"
    source.symlink_to(target)
    assert cli.main(["register", "--protocol", str(source), "--output-dir", str(tmp_path / "out")]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"


def test_empty_campaign_report_and_verify_are_offline_and_repeatable(tmp_path, capsys):
    cli = importlib.import_module("evaluate_prospective")
    source = tmp_path / "protocol.json"
    source.write_text(json.dumps({
        "schema_version": 1, "campaign_id": "offline-fixture",
        "universe": [{"symbol": "SPY", "asset_class": "etf"}],
        "model": "openai/gpt-6.1-sol", "research_cash": "100", "max_checkpoints": 20,
    }))
    assert cli.main(["register", "--protocol", str(source), "--output-dir", str(tmp_path / "out")]) == 0
    path = json.loads(capsys.readouterr().out)["campaign_path"]
    assert cli.main(["report", path]) == 0
    first = capsys.readouterr().out
    result = json.loads(first)
    assert result["status"] == "reported"
    assert result["report"]["executable"] is False
    assert cli.main(["report", path]) == 0
    assert capsys.readouterr().out == first
    assert cli.main(["verify", path]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["status"] == "verified"
    assert verified["verification"]["verification_kind"] == "offline_semantic_reconstruction"


def test_tick_failure_is_durable_and_same_bucket_does_not_repeat_sources(tmp_path, capsys):
    """Synthetic unavailable-source fixture; no real feed or inference is invoked."""
    from datetime import datetime, timezone

    class UnavailableSource:
        def __init__(self):
            self.calls = 0

        def fetch(self, endpoint, params):
            self.calls += 1
            raise RuntimeError("upstream-secret-do-not-echo")

    class UnusedProvider:
        model = "openai/gpt-6.1-sol"

        def complete(self, role, payload):
            raise AssertionError("unavailable observations must not reach inference")

    cli = importlib.import_module("evaluate_prospective")
    source = tmp_path / "protocol.json"
    source.write_text(json.dumps({
        "schema_version": 1, "campaign_id": "tick-fixture",
        "universe": [{"symbol": "SPY", "asset_class": "etf"}],
        "model": "openai/gpt-6.1-sol", "research_cash": "100", "max_checkpoints": 20,
    }))
    clock = lambda: datetime(2026, 11, 17, 5, tzinfo=timezone.utc)
    assert cli.main(["register", "--protocol", str(source), "--output-dir", str(tmp_path / "out")], clock=clock) == 0
    path = json.loads(capsys.readouterr().out)["campaign_path"]
    transport = UnavailableSource()
    assert cli.main(["tick", path], alpaca=transport, public=transport, provider=UnusedProvider(), clock=clock) == 1
    first = capsys.readouterr().out
    assert transport.calls > 0
    count = transport.calls
    assert "upstream-secret" not in first
    assert json.loads(first)["executable"] is False
    assert cli.main(["tick", path], alpaca=transport, public=transport, provider=UnusedProvider(), clock=clock) == 1
    assert transport.calls == count
    assert capsys.readouterr().out == first


def test_default_provider_is_lazy_when_observations_are_unavailable(tmp_path, capsys):
    """Source-failure fixture: constructing provider must not resolve credentials or infer."""
    cli = importlib.import_module("evaluate_prospective")
    source = tmp_path / "protocol.json"
    source.write_text(json.dumps({
        "schema_version": 1, "campaign_id": "lazy-provider-fixture",
        "universe": [{"symbol": "SPY", "asset_class": "etf"}],
        "model": "openai/gpt-6.1-sol", "research_cash": "100", "max_checkpoints": 20,
    }))
    assert cli.main(["register", "--protocol", str(source), "--output-dir", str(tmp_path / "out")]) == 0
    path = json.loads(capsys.readouterr().out)["campaign_path"]

    class UnavailableSource:
        def fetch(self, endpoint, params):
            raise RuntimeError("synthetic-source-failure")

    transport = UnavailableSource()
    assert cli.main(["tick", path], alpaca=transport, public=transport) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "unavailable"
    assert result["executable"] is False
    assert cli.main(["verify", path]) == 0
    verified = json.loads(capsys.readouterr().out)["verification"]
    assert verified["checkpoint_count"] == 0


def test_injected_whole_lab_executor_is_not_called_when_source_is_unavailable(tmp_path, capsys):
    from datetime import datetime, timezone

    cli = importlib.import_module("evaluate_prospective")
    source = tmp_path / "protocol.json"
    source.write_text(json.dumps({
        "schema_version": 1, "campaign_id": "executor-boundary-fixture",
        "universe": [{"symbol": "SPY", "asset_class": "etf"}],
        "model": "openai/gpt-6.1-sol", "research_cash": "100", "max_checkpoints": 20,
    }))
    clock = lambda: datetime(2026, 11, 17, 5, tzinfo=timezone.utc)
    assert cli.main(["register", "--protocol", str(source), "--output-dir", str(tmp_path / "out")], clock=clock) == 0
    path = json.loads(capsys.readouterr().out)["campaign_path"]

    class UnavailableSource:
        def fetch(self, endpoint, params):
            raise RuntimeError("synthetic-source-failure")

    def never_execute(bundle, collection_path, output_dir):
        raise AssertionError("no paid inference is permitted without valid sources")

    transport = UnavailableSource()
    assert cli.main(["tick", path], alpaca=transport, public=transport,
                    execute_lab=never_execute, clock=clock) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "unavailable"


def test_script_entrypoint_exposes_the_same_public_help(monkeypatch, capsys):
    import runpy
    import sys

    cli = importlib.import_module("evaluate_prospective")
    assert isinstance(cli.__file__, str)
    monkeypatch.setattr(sys, "argv", [cli.__file__, "--help"])
    with pytest.raises(SystemExit) as caught:
        runpy.run_path(cli.__file__, run_name="__main__")
    assert caught.value.code == 0
    assert "register" in capsys.readouterr().out


def test_help_describes_research_commands_without_optional_runtime(capsys):
    cli = importlib.import_module("evaluate_prospective")
    assert cli.main(["--help"]) == 0
    text = capsys.readouterr().out
    assert "register" in text
    assert "tick" in text
    assert "report" in text
    assert "verify" in text
