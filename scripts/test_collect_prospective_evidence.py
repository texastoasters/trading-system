"""Public CLI tests use synthetic boundary transports and real pipeline code."""
from __future__ import annotations

import copy
import json
import runpy
import sys
from pathlib import Path

import pytest

import collect_prospective_evidence as cli
from prospective_evidence.pipeline import build_collection
from prospective_evidence.test_pipeline import COMPLETE, START, experiment, synthetic_capture


class FixtureTransport:
    """Clearly synthetic source boundary; production normalization remains real."""

    def __init__(self, capture, *, fail_endpoint=None, unknown=False):
        self.capture = capture
        self.fail_endpoint = fail_endpoint
        self.unknown = unknown
        self.calls = []

    def fetch(self, endpoint, query):
        self.calls.append((endpoint, query))
        if endpoint == self.fail_endpoint:
            if self.unknown:
                raise RuntimeError("SECRET upstream exception body")
            raise ValueError("SECRET source exception body")
        page = self.capture["sources"][endpoint]["pages"][0]
        return {
            "payload": copy.deepcopy(page["payload"]),
            "request_started_at": page["request_started_at"],
            "received_at": page["received_at"],
            "body_sha256": page["body_sha256"],
            "rate_limit": copy.deepcopy(page["rate_limit"]),
        }


class FixtureClock:
    def __init__(self):
        from datetime import datetime
        self.values = iter((datetime.fromisoformat(START), datetime.fromisoformat(COMPLETE)))

    def __call__(self):
        return next(self.values)


def write_experiment(path, value=None):
    path.write_text(json.dumps(experiment() if value is None else value))
    return path


def invoke_collect(tmp_path, capsys, declared=None, capture=None, **transport_options):
    source = capture or synthetic_capture()
    alpaca = FixtureTransport(source, **transport_options)
    public = FixtureTransport(source, **transport_options)
    code = cli.main(
        ["collect", "--experiment", str(write_experiment(tmp_path / "experiment.json", declared)),
         "--output-dir", str(tmp_path / "collections")],
        alpaca=alpaca,
        public=public,
        clock=FixtureClock(),
    )
    output = json.loads(capsys.readouterr().out)
    return code, output, alpaca, public


def test_help_is_offline_and_executable_entrypoint_uses_same_parser(capsys, monkeypatch):
    assert cli.main(["--help"]) == 0
    assert "collect" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", [str(Path(cli.__file__)), "--help"])
    with pytest.raises(SystemExit, match="^0$"):
        runpy.run_path(cli.__file__, run_name="__main__")
    assert "verify" in capsys.readouterr().out


def test_collect_uses_real_acquire_build_and_archive_then_verify_is_semantic(tmp_path, capsys):
    code, output, alpaca, public = invoke_collect(tmp_path, capsys)
    assert code == 0
    assert output["status"] == "collected"
    assert output["executable"] is False
    assert output["collection_path"].endswith("/synthetic-collection")
    assert output["bundle_path"].endswith("/synthetic-collection/bundle.json")
    assert output["evidence_counts"] == {"account": 1, "algorithmic": 1, "scheduled_event": 1, "technical": 1}
    assert [item[0] for item in alpaca.calls] == ["sessions", "bars", "news"]
    assert [item[0] for item in public.calls] == ["bea_calendar"]

    verify_code = cli.main(["verify", output["collection_path"]])
    verified = json.loads(capsys.readouterr().out)
    assert verify_code == 0
    assert verified == {
        "status": "verified",
        "executable": False,
        "collection_path": output["collection_path"],
        "bundle_path": output["bundle_path"],
        "verification_kind": "semantic_reconstruction",
        "evidence_counts": output["evidence_counts"],
    }


def test_incomplete_source_is_archived_as_non_executable_sanitized_capture(tmp_path, capsys):
    code, output, alpaca, _ = invoke_collect(tmp_path, capsys, fail_endpoint="news")
    assert code == 1
    assert output["status"] == "failed"
    assert output["error"] == "capture_incomplete"
    assert output["executable"] is False
    assert output["path"].endswith("/synthetic-collection")
    assert "SECRET" not in json.dumps(output)
    assert len(alpaca.calls) == 3

    assert cli.main(["verify", output["path"]]) == 1
    checked = json.loads(capsys.readouterr().out)
    assert checked["status"] == "failed"
    assert checked["error"] == "capture_incomplete"
    assert checked["verification_kind"] == "integrity_only"
    assert checked["executable"] is False


def test_normalization_failure_preserves_capture_with_collection_failed_code(tmp_path, capsys):
    capture = synthetic_capture()
    capture["sources"]["bea_calendar"]["pages"][0]["payload"] = "SYNTHETIC malformed fixture"
    code, output, _, _ = invoke_collect(tmp_path, capsys, capture=capture)
    assert code == 1
    assert output["error"] == "collection_failed"
    assert output["path"].endswith("/synthetic-collection")


def test_unknown_acquisition_exception_is_sanitized_and_archived_without_text(tmp_path, capsys):
    code, output, _, _ = invoke_collect(tmp_path, capsys, fail_endpoint="news", unknown=True)
    assert code == 1
    assert output["error"] == "collection_failed"
    assert "SECRET" not in json.dumps(output)


@pytest.mark.parametrize("mutation", [
    "missing", "directory", "symlink", "oversized", "duplicate", "nan", "not_object", "unknown_field",
    "unsafe_id", "unsafe_book", "bad_universe", "bad_class", "duplicate_symbol", "both_books", "no_book",
    "bad_cash", "bad_cash_whitespace", "bad_cash_parse", "bad_ttl", "bad_portfolio", "bad_position",
    "bad_portfolio_semantics",
])
def test_invalid_experiment_never_contacts_sources_or_creates_archive(tmp_path, capsys, mutation):
    declared = experiment()
    experiment_path = tmp_path / "experiment.json"
    if mutation == "missing":
        pass
    elif mutation == "directory":
        experiment_path.mkdir()
    elif mutation == "symlink":
        target = write_experiment(tmp_path / "target.json")
        experiment_path.symlink_to(target)
    elif mutation == "oversized":
        experiment_path.write_text(" " * (cli.MAX_EXPERIMENT_BYTES + 1))
    elif mutation == "duplicate":
        experiment_path.write_text('{"collection_id":"a","collection_id":"b"}')
    elif mutation == "nan":
        experiment_path.write_text('{"collection_id":NaN}')
    else:
        if mutation == "not_object":
            declared = []
        elif mutation == "unknown_field":
            declared["private"] = "SECRET"
        elif mutation == "unsafe_id":
            declared["collection_id"] = "../escape"
        elif mutation == "unsafe_book":
            declared["book_id"] = "."
        elif mutation == "bad_universe":
            declared["universe"] = []
        elif mutation == "bad_class":
            declared["universe"][0]["asset_class"] = "crypto"
        elif mutation == "duplicate_symbol":
            declared["universe"].append(copy.deepcopy(declared["universe"][0]))
        elif mutation == "both_books":
            declared["portfolio"] = {}
        elif mutation == "no_book":
            del declared["research_cash"]
        elif mutation == "bad_cash":
            declared["research_cash"] = "NaN"
        elif mutation == "bad_cash_whitespace":
            declared["research_cash"] = " 100"
        elif mutation == "bad_cash_parse":
            declared["research_cash"] = "not-decimal"
        elif mutation == "bad_ttl":
            declared["ttl_seconds"] = True
        else:
            del declared["research_cash"]
            declared["portfolio"] = {
                "snapshot_id": "fixture", "observed_at": COMPLETE, "cash": "100", "equity": "100",
                "pending_buy_notional": "0", "positions": [],
            }
            if mutation == "bad_portfolio":
                declared["portfolio"]["private"] = True
            elif mutation == "bad_position":
                declared["portfolio"]["positions"] = [{}]
            else:
                declared["portfolio"]["cash"] = "NaN"
        write_experiment(experiment_path, declared)
    alpaca = FixtureTransport(synthetic_capture())
    public = FixtureTransport(synthetic_capture())
    code = cli.main(
        ["collect", "--experiment", str(experiment_path), "--output-dir", str(tmp_path / "out")],
        alpaca=alpaca, public=public, clock=FixtureClock(),
    )
    output = json.loads(capsys.readouterr().out)
    assert code == 1
    assert output == {"status": "failed", "error": "invalid_experiment", "executable": False}
    assert alpaca.calls == public.calls == []
    assert not (tmp_path / "out").exists()


def test_valid_caller_portfolio_reaches_real_pipeline(tmp_path, capsys):
    declared = experiment()
    del declared["research_cash"]
    declared["portfolio"] = {
        "snapshot_id": "fixture", "observed_at": "2026-01-22T01:04:00+00:00",
        "cash": "100", "equity": "100", "pending_buy_notional": "0", "positions": [],
    }
    code, output, _, _ = invoke_collect(tmp_path, capsys, declared=declared)
    assert code == 0
    assert output["status"] == "collected"


def test_storage_failure_fails_closed_once_and_does_not_replace_existing_archive(tmp_path, capsys):
    code, first, _, _ = invoke_collect(tmp_path, capsys)
    assert code == 0
    code, output, _, _ = invoke_collect(tmp_path, capsys)
    assert code == 1
    assert output == {"status": "failed", "error": "storage_failed", "executable": False}
    assert list((tmp_path / "collections").iterdir()) == [Path(first["collection_path"])]


def test_failure_archive_storage_error_is_not_retried(tmp_path, capsys):
    source = synthetic_capture()
    output_root = tmp_path / "blocked"
    output_root.write_text("not a directory")
    code = cli.main(
        ["collect", "--experiment", str(write_experiment(tmp_path / "experiment.json")),
         "--output-dir", str(output_root)],
        alpaca=FixtureTransport(source, fail_endpoint="news"),
        public=FixtureTransport(source),
        clock=FixtureClock(),
    )
    assert code == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "failed", "error": "storage_failed", "executable": False
    }


@pytest.mark.parametrize("argv", [[], ["collect"], ["verify"], ["unknown"]])
def test_usage_errors_are_json_not_tracebacks(argv, capsys):
    assert cli.main(argv) == 2
    output = json.loads(capsys.readouterr().out)
    assert output == {"status": "failed", "error": "invalid_command", "executable": False}


def test_verify_missing_or_tampered_archive_is_sanitized(tmp_path, capsys):
    assert cli.main(["verify", str(tmp_path / "missing")]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "verification_failed"

    built = build_collection(synthetic_capture(), experiment())
    from prospective_evidence.archive import write_collection
    path = write_collection(tmp_path, built["bundle"], built["capture"])
    (path / "capture.json").write_text("SECRET tampered bytes")
    assert cli.main(["verify", str(path)]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output == {"status": "failed", "error": "verification_failed", "executable": False,
                      "collection_path": str(path)}
