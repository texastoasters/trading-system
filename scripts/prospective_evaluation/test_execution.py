"""Public-seam tests for bounded managed-runtime decision-lab execution."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from decision_lab import run_lab
from decision_lab.storage import hash_file
from prospective_evaluation.execution import LabExecutionError, ManagedLabExecutor

NOW = "2026-11-15T20:00:00-06:00"
MODEL = "fixture-model"
RUNTIME = [
    sys.executable,
    "-I",
    "-c",
    "import runpy,sys; runpy.run_module('hermes_cli.main', run_name='__main__', alter_sys=True)",
]


def bundle(run_id: str = "managed-fixture") -> dict:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "as_of": NOW,
        "expires_at": "2026-11-15T21:00:00-06:00",
        "mode": "historical_diagnostic",
        "mandate_version": "managed-fixture-v1",
        "universe": [
            {"symbol": "SPY", "asset_class": "etf"},
            {"symbol": "BND", "asset_class": "etf"},
        ],
        "portfolio": {
            "snapshot_id": "snapshot-1",
            "observed_at": "2026-11-15T19:59:00-06:00",
            "cash": "500.00",
            "equity": "1000.00",
            "pending_buy_notional": "0",
            "positions": [{"symbol": "SPY", "quantity": "1", "market_value": "500.00"}],
        },
        "evidence": [{
            "evidence_id": "synthetic-evidence-1",
            "kind": "news",
            "source": "labelled synthetic fixture",
            "published_at": "2026-11-15T19:00:00-06:00",
            "available_at": "2026-11-15T19:01:00-06:00",
            "expires_at": "2026-11-15T20:45:00-06:00",
            "symbols": ["SPY"],
            "content": "SYNTHETIC fixture evidence; not an actual market report.",
        }],
        "max_snapshot_age_seconds": 300,
        "max_calls": 3,
    }


class SyntheticProvider:
    """Labelled synthetic provider used only to create real lab artifacts."""

    def __init__(self, outcome: str):
        self.outcome = outcome

    def complete(self, role, payload):
        if self.outcome == "failed" and role == "research":
            raise RuntimeError("synthetic provider failure")
        evidence_id = payload["bundle"]["evidence"][0]["evidence_id"]
        outputs = {
            "research": {
                "summary": "Synthetic fixture summary.",
                "candidate_symbols": ["SPY"],
                "evidence_ids": [evidence_id],
                "counterevidence": [],
                "missing_data": [],
            },
            "cio": {
                "action": "rebalance",
                "targets": [
                    {"symbol": "SPY", "weight": "0.60", "thesis": "Synthetic fixture.",
                     "evidence_ids": [evidence_id], "falsifier": "Fixture changes."},
                    {"symbol": "BND", "weight": "0.00", "thesis": "No synthetic support.",
                     "evidence_ids": [], "falsifier": "Synthetic support appears."},
                ],
                "cash_weight": "0.40",
                "rationale": "Synthetic fixture allocation.",
                "evidence_ids": [evidence_id],
                "counterevidence": [],
                "expires_at": "2026-11-15T20:30:00-06:00",
            },
            "critic": {
                "verdict": "revise" if self.outcome == "blocked" else "pass",
                "issues": ["Synthetic fixture veto."] if self.outcome == "blocked" else [],
                "evidence_ids": [evidence_id],
            },
        }
        value = outputs[role]
        return {
            "text": json.dumps(value),
            "model": MODEL,
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "latency_ms": 1,
            "request_id": "synthetic-" + role,
        }


class ManagedRunner:
    """Simulate discovery, then create actual archive/replay-compatible artifacts."""

    def __init__(self, outcome: str = "accepted", *, worker_returncode: int | None = None):
        self.outcome = outcome
        self.worker_returncode = worker_returncode
        self.calls = []

    def __call__(self, command, **options):
        self.calls.append((command, options))
        if len(self.calls) == 1:
            assert command == ["hermes", "--print-runtime-command"]
            return SimpleNamespace(returncode=0, stdout=json.dumps(RUNTIME), stderr="")
        assert len(self.calls) == 2, "managed worker must execute at most once"
        assert command[-6] == "run"
        archived = Path(command[-5])
        assert command[-4:-2] == ["--model", MODEL]
        assert command[-2] == "--output"
        output = Path(command[-1])
        decision = run_lab(
            json.loads(archived.read_text()),
            SyntheticProvider(self.outcome),
            output,
            now=NOW,
        )
        default = 0 if decision["status"] == "accepted" else 2
        return SimpleNamespace(
            returncode=default if self.worker_returncode is None else self.worker_returncode,
            stdout=json.dumps({"untrusted": "suppressed"}),
            stderr="synthetic worker diagnostic",
        )


def archive(tmp_path: Path, value: dict) -> Path:
    collection = tmp_path / "collection"
    collection.mkdir(parents=True)
    (collection / "bundle.json").write_text(json.dumps(value), encoding="utf-8")
    return collection


@pytest.mark.parametrize("outcome", ["accepted", "blocked", "failed"])
def test_executes_real_lab_artifacts_once_and_returns_verified_terminal_decision(tmp_path, outcome):
    value = bundle("terminal-" + outcome)
    collection = archive(tmp_path, value)
    managed = ManagedRunner(outcome)

    decision = ManagedLabExecutor(MODEL, runner=managed)(value, collection, tmp_path / "runs")

    assert decision["status"] == outcome
    assert decision["run_id"] == value["run_id"]
    assert decision["executable"] is False
    assert len(managed.calls) == 2
    discovery_options = managed.calls[0][1]
    worker_options = managed.calls[1][1]
    assert discovery_options == {"capture_output": True, "text": True, "timeout": 60, "check": False}
    assert worker_options == {"capture_output": True, "text": True, "timeout": 480, "check": False}
    worker_command = managed.calls[1][0]
    assert worker_command[-6:] == [
        "run", str(collection / "bundle.json"), "--model", MODEL,
        "--output", str(tmp_path / "runs"),
    ]
    assert "--now" not in worker_command


def test_constructor_is_lazy_and_model_is_pinned_exactly(tmp_path):
    class MustNotRun:
        def __call__(self, *args, **kwargs):
            raise AssertionError("constructor performed discovery")

    executor = ManagedLabExecutor(" exact-model-id ", runner=MustNotRun())
    assert executor.model == " exact-model-id "
    with pytest.raises(LabExecutionError, match="managed_runtime_unavailable"):
        executor(bundle(), archive(tmp_path, bundle()), tmp_path / "runs")


@pytest.mark.parametrize("payload", [
    "not-json",
    "[]",
    json.dumps([sys.executable, "-c", "unsafe"]),
    json.dumps(RUNTIME) + " trailing",
    '{"duplicate":1,"duplicate":2}',
    " " * 1_000_001,
])
def test_malformed_managed_runtime_discovery_fails_before_worker(tmp_path, payload):
    calls = []

    def malformed(command, **options):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=payload, stderr="secret must stay suppressed")

    value = bundle("bad-discovery")
    with pytest.raises(LabExecutionError, match="managed_runtime_unavailable"):
        ManagedLabExecutor(MODEL, runner=malformed)(value, archive(tmp_path, value), tmp_path / "runs")
    assert calls == [["hermes", "--print-runtime-command"]]


@pytest.mark.parametrize("discovery", [
    SimpleNamespace(returncode=1, stdout=json.dumps(RUNTIME), stderr="secret"),
    SimpleNamespace(returncode=-9, stdout=json.dumps(RUNTIME), stderr="secret"),
    SimpleNamespace(returncode=0, stdout="x" * 1_000_001, stderr=""),
])
def test_failed_or_oversize_discovery_is_sanitized(tmp_path, discovery):
    value = bundle("discovery-failure")
    with pytest.raises(LabExecutionError, match="managed_runtime_unavailable") as caught:
        ManagedLabExecutor(MODEL, runner=lambda *a, **k: discovery)(
            value, archive(tmp_path, value), tmp_path / "runs"
        )
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("body", [
    '{"schema_version":1,"schema_version":1}',
    '{"schema_version":NaN}',
    '{"overflow":1e309}',
    "1.5",
    json.dumps([0]).replace("0", "[" * 101 + "0" + "]" * 101),
    "x" * 1_000_001,
])
def test_archive_must_be_bounded_strict_json(tmp_path, body):
    value = bundle("strict-archive")
    collection = tmp_path / "collection"
    collection.mkdir()
    (collection / "bundle.json").write_text(body, encoding="utf-8")
    runner = ManagedRunner()

    with pytest.raises(LabExecutionError, match="invalid_archived_bundle"):
        ManagedLabExecutor(MODEL, runner=runner)(value, collection, tmp_path / "runs")
    assert runner.calls == []


def test_archive_with_invalid_utf8_is_rejected(tmp_path):
    value = bundle("invalid-utf8")
    collection = tmp_path / "collection"
    collection.mkdir()
    (collection / "bundle.json").write_bytes(b"\xff")
    with pytest.raises(LabExecutionError, match="invalid_archived_bundle"):
        ManagedLabExecutor(MODEL, runner=ManagedRunner())(value, collection, tmp_path / "runs")


def test_archive_checks_deep_subtree_after_finite_float_before_discovery(tmp_path):
    value = bundle("deep-after-float")
    nested: object = "leaf"
    for _ in range(101):
        nested = [nested]
    value["evidence"][0]["deep_metadata"] = nested
    value["evidence"][0]["metrics"] = {"confidence": 0.5}
    collection = archive(tmp_path, value)
    calls = []

    def discovery_must_not_run(command, **options):
        calls.append((command, options))
        raise AssertionError("invalid archive reached managed discovery")

    with pytest.raises(LabExecutionError, match="invalid_archived_bundle"):
        ManagedLabExecutor(MODEL, runner=discovery_must_not_run)(
            value, collection, tmp_path / "runs"
        )
    assert calls == []


@pytest.mark.parametrize("mutation", [
    lambda value: value.update(run_id="changed-run-id"),
    lambda value: value.update(mandate_version="changed"),
])
def test_archived_bundle_must_equal_passed_bundle_by_public_canonical_hash(tmp_path, mutation):
    value = bundle("archive-mismatch")
    archived = json.loads(json.dumps(value))
    mutation(archived)
    runner = ManagedRunner()

    with pytest.raises(LabExecutionError, match="archived_bundle_mismatch"):
        ManagedLabExecutor(MODEL, runner=runner)(value, archive(tmp_path, archived), tmp_path / "runs")
    assert runner.calls == []


def test_symlink_archive_is_rejected_before_discovery(tmp_path):
    value = bundle("symlink-archive")
    collection = tmp_path / "collection"
    collection.mkdir()
    source = tmp_path / "source.json"
    source.write_text(json.dumps(value))
    (collection / "bundle.json").symlink_to(source)
    runner = ManagedRunner()

    with pytest.raises(LabExecutionError, match="unsafe_archived_bundle"):
        ManagedLabExecutor(MODEL, runner=runner)(value, collection, tmp_path / "runs")
    assert runner.calls == []


@pytest.mark.parametrize("failure", [
    subprocess.TimeoutExpired(["worker"], 480, output="secret", stderr="secret"),
    OSError("secret process detail"),
    ValueError("secret runner defect"),
])
def test_worker_timeout_or_launch_failure_is_ambiguous_without_retry(tmp_path, failure):
    value = bundle("worker-exception")
    calls = []

    def runner(command, **options):
        calls.append(command)
        if len(calls) == 1:
            return SimpleNamespace(returncode=0, stdout=json.dumps(RUNTIME), stderr="")
        raise failure

    with pytest.raises(LabExecutionError, match="lab_execution_failed") as caught:
        ManagedLabExecutor(MODEL, runner=runner)(value, archive(tmp_path, value), tmp_path / "runs")
    assert len(calls) == 2
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("returncode", [-9, 1, 3])
def test_signal_or_unexpected_worker_exit_is_not_retried_even_with_artifacts(tmp_path, returncode):
    value = bundle("bad-exit-" + str(abs(returncode)))
    managed = ManagedRunner("accepted", worker_returncode=returncode)

    with pytest.raises(LabExecutionError, match="lab_execution_failed"):
        ManagedLabExecutor(MODEL, runner=managed)(value, archive(tmp_path, value), tmp_path / "runs")
    assert len(managed.calls) == 2


def test_missing_published_run_and_oversize_worker_output_fail_closed(tmp_path):
    value = bundle("missing-run")

    def missing(command, **options):
        if command == ["hermes", "--print-runtime-command"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(RUNTIME), stderr="")
        return SimpleNamespace(returncode=0, stdout="x" * 1_000_001, stderr="")

    with pytest.raises(LabExecutionError, match="lab_execution_failed"):
        ManagedLabExecutor(MODEL, runner=missing)(value, archive(tmp_path, value), tmp_path / "runs")


@pytest.mark.parametrize("tamper", [
    "input",
    "manifest_input_hash",
    "manifest_run_id",
    "manifest_schema",
    "manifest_extra_key",
    "decision_run_id",
    "receipt_model",
    "receipts_not_list",
    "receipt_not_object",
    "receipt_model_not_string",
    "receipt_manifest_mismatch",
    "response_receipt_missing",
    "manifest_files_not_object",
    "decision_not_object",
    "decision_status",
])
def test_readback_rejects_input_or_manifest_tampering(tmp_path, tamper):
    value = bundle("tamper-" + tamper.replace("_", "-"))

    class TamperingRunner(ManagedRunner):
        def __call__(self, command, **options):
            response = super().__call__(command, **options)
            if len(self.calls) != 2:
                return response
            run_dir = tmp_path / "runs" / value["run_id"]
            manifest_path = run_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            if tamper == "input":
                input_path = run_dir / "input.json"
                changed = json.loads(input_path.read_text())
                changed["mandate_version"] = "self-consistently-tampered"
                input_path.write_text(json.dumps(changed))
                manifest["files"]["input.json"] = hash_file(input_path)
                manifest["input_hash"] = __import__("decision_lab.storage", fromlist=["hash_value"]).hash_value(changed)
            elif tamper == "manifest_input_hash":
                manifest["input_hash"] = "0" * 64
            elif tamper == "manifest_run_id":
                manifest["run_id"] = "another-run"
            elif tamper == "manifest_schema":
                manifest["schema_version"] = True
            elif tamper == "manifest_extra_key":
                manifest["unexpected"] = "field"
            elif tamper == "decision_run_id":
                decision_path = run_dir / "decision.json"
                decision = json.loads(decision_path.read_text())
                decision["run_id"] = "another-run"
                decision_path.write_text(json.dumps(decision))
                manifest["files"]["decision.json"] = hash_file(decision_path)
            elif tamper == "receipt_model":
                manifest["model_receipts"][0]["model"] = "unrequested-model"
            elif tamper == "receipts_not_list":
                manifest["model_receipts"] = {}
            elif tamper == "receipt_not_object":
                manifest["model_receipts"] = [None]
            elif tamper == "receipt_model_not_string":
                manifest["model_receipts"][0]["model"] = None
            elif tamper == "receipt_manifest_mismatch":
                manifest["model_receipts"][0]["request_id"] = "changed"
            elif tamper == "response_receipt_missing":
                response_path = next(run_dir.glob("response-*.json"))
                artifact = json.loads(response_path.read_text())
                artifact["receipt"] = None
                response_path.write_text(json.dumps(artifact))
                manifest["files"][response_path.name] = hash_file(response_path)
            elif tamper == "manifest_files_not_object":
                manifest["files"] = []
            elif tamper == "decision_not_object":
                decision_path = run_dir / "decision.json"
                decision_path.write_text("[]")
                manifest["files"]["decision.json"] = hash_file(decision_path)
            elif tamper == "decision_status":
                decision_path = run_dir / "decision.json"
                decision = json.loads(decision_path.read_text())
                decision["status"] = "unexpected"
                decision_path.write_text(json.dumps(decision))
                manifest["files"]["decision.json"] = hash_file(decision_path)
            manifest_path.write_text(json.dumps(manifest))
            return response

    with pytest.raises(LabExecutionError, match="invalid_lab_artifacts"):
        ManagedLabExecutor(MODEL, runner=TamperingRunner())(
            value, archive(tmp_path, value), tmp_path / "runs"
        )


def test_exit_status_must_agree_with_published_decision(tmp_path):
    for outcome, code in (("accepted", 2), ("blocked", 0), ("failed", 0)):
        value = bundle("status-mismatch-" + outcome)
        managed = ManagedRunner(outcome, worker_returncode=code)
        with pytest.raises(LabExecutionError, match="inconsistent_lab_result"):
            ManagedLabExecutor(MODEL, runner=managed)(
                value, archive(tmp_path / outcome, value), tmp_path / outcome / "runs"
            )


def test_unknown_decision_status_is_inconsistent_when_replay_accepts_it(tmp_path, monkeypatch):
    value = bundle("unknown-status")

    class StatusRunner(ManagedRunner):
        def __call__(self, command, **options):
            response = super().__call__(command, **options)
            if len(self.calls) == 2:
                run_dir = tmp_path / "runs" / value["run_id"]
                decision_path = run_dir / "decision.json"
                manifest_path = run_dir / "manifest.json"
                decision = json.loads(decision_path.read_text())
                decision["status"] = "unknown"
                decision_path.write_text(json.dumps(decision))
                manifest = json.loads(manifest_path.read_text())
                manifest["files"]["decision.json"] = hash_file(decision_path)
                manifest_path.write_text(json.dumps(manifest))
            return response

    monkeypatch.setattr("prospective_evaluation.execution.replay_run", lambda path: {"verified": True})
    with pytest.raises(LabExecutionError, match="inconsistent_lab_result"):
        ManagedLabExecutor(MODEL, runner=StatusRunner())(
            value, archive(tmp_path, value), tmp_path / "runs"
        )


def test_nonfinite_passed_bundle_is_rejected_before_discovery(tmp_path):
    value = bundle("invalid-passed")
    collection = archive(tmp_path, value)
    value["unexpected"] = float("nan")
    with pytest.raises(LabExecutionError, match="invalid_passed_bundle"):
        ManagedLabExecutor(MODEL, runner=ManagedRunner())(value, collection, tmp_path / "runs")


def test_invalid_runner_result_shape_is_sanitized(tmp_path):
    value = bundle("invalid-runner-result")
    with pytest.raises(LabExecutionError, match="managed_runtime_unavailable"):
        ManagedLabExecutor(MODEL, runner=lambda *a, **k: object())(
            value, archive(tmp_path, value), tmp_path / "runs"
        )


def test_nonstring_run_id_is_rejected_before_discovery(tmp_path):
    value = bundle("temporary-id")
    value["run_id"] = 7
    with pytest.raises(LabExecutionError, match="archived_bundle_mismatch"):
        ManagedLabExecutor(MODEL, runner=ManagedRunner())(
            value, archive(tmp_path, value), tmp_path / "runs"
        )
