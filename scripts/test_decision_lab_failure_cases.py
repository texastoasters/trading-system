"""Failure injection at the decision lab's public validation, run, and replay seams."""
from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path

import pytest

from decision_lab import BundleValidationError, replay_run, run_lab, validate_bundle
from decision_lab.storage import hash_file

NOW = "2026-11-15T20:00:00-06:00"


def bundle(run_id="failure-fixture"):
    return {
        "schema_version": 1,
        "run_id": run_id,
        "as_of": NOW,
        "expires_at": "2026-11-15T21:00:00-06:00",
        "mode": "shadow",
        "mandate_version": "failure-fixture-v1",
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
            "evidence_id": "e-1",
            "kind": "news",
            "source": "offline fixture",
            "published_at": "2026-11-15T19:00:00-06:00",
            "available_at": "2026-11-15T19:01:00-06:00",
            "expires_at": "2026-11-15T20:45:00-06:00",
            "symbols": ["SPY"],
            "content": "FIXTURE evidence, not real output.",
            "metrics": {"score": 1.0},
        }],
        "max_snapshot_age_seconds": 300,
        "max_calls": 3,
    }


def role_outputs():
    return {
        "research": {
            "summary": "Fixture summary.", "candidate_symbols": ["SPY"],
            "evidence_ids": ["e-1"], "counterevidence": [], "missing_data": [],
        },
        "cio": {
            "action": "rebalance",
            "targets": [
                {"symbol": "SPY", "weight": "0.60", "thesis": "Fixture.",
                 "evidence_ids": ["e-1"], "falsifier": "Fixture falsifier."},
                {"symbol": "BND", "weight": "0.00", "thesis": "No new risk.",
                 "evidence_ids": [], "falsifier": "New evidence."},
            ],
            "cash_weight": "0.40", "rationale": "Fixture allocation.",
            "evidence_ids": ["e-1"], "counterevidence": [],
            "expires_at": "2026-11-15T20:30:00-06:00",
        },
        "critic": {"verdict": "pass", "issues": [], "evidence_ids": ["e-1"]},
    }


class Provider:
    def __init__(self, outputs=None, receipts=None):
        self.outputs = outputs or role_outputs()
        self.receipts = receipts or {}
        self.calls = []

    def complete(self, role, payload):
        self.calls.append(role)
        value = self.outputs[role]
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            return value(role, payload)
        receipt = {
            "text": json.dumps(value), "model": "offline-fixture", "usage": {},
            "latency_ms": 1, "request_id": None,
        }
        receipt.update(self.receipts.get(role, {}))
        return receipt


def delete(path):
    def mutate(value):
        target = value
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]
    return mutate


def set_value(path, replacement):
    def mutate(value):
        target = value
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = replacement
    return mutate


@pytest.mark.parametrize("mutate, match", [
    (lambda b: None, "bundle must be an object"),
    (delete(("schema_version",)), "bundle missing"),
    (set_value(("schema_version",), 2), "schema_version"),
    (set_value(("run_id",), ""), "run_id"),
    (set_value(("mode",), "live"), "mode"),
    (set_value(("mandate_version",), "  "), "mandate_version"),
    (set_value(("as_of",), 1), "ISO8601 string"),
    (set_value(("as_of",), "not-a-date"), "ISO8601"),
    (set_value(("as_of",), "2026-11-15T20:00:00"), "timezone-aware"),
    (set_value(("expires_at",), NOW), "later than"),
    (set_value(("universe",), []), "nonempty array"),
    (set_value(("universe", 0), "SPY"), "must be an object"),
    (delete(("universe", 0, "symbol")), "universe\\[0\\] missing"),
    (set_value(("universe", 0, "symbol"), " SPY"), "exact nonempty"),
    (set_value(("universe", 0, "asset_class"), "option"), "asset_class"),
    (set_value(("universe", 1, "symbol"), "SPY"), "unique"),
    (set_value(("portfolio",), []), "portfolio must be an object"),
    (delete(("portfolio", "cash")), "portfolio missing"),
    (set_value(("portfolio", "snapshot_id"), ""), "snapshot_id"),
    (set_value(("portfolio", "observed_at"), "2026-11-15T20:01:00-06:00"), "later than"),
    (set_value(("portfolio", "cash"), 500), "decimal string"),
    (set_value(("portfolio", "cash"), "wat"), "decimal string"),
    (set_value(("portfolio", "equity"), "Infinity"), "finite"),
    (set_value(("portfolio", "equity"), "0"), "positive"),
    (set_value(("portfolio", "pending_buy_notional"), "-1"), "nonnegative"),
    (set_value(("portfolio", "positions"), {}), "positions must be an array"),
    (set_value(("portfolio", "positions", 0), "SPY"), "must be an object"),
    (delete(("portfolio", "positions", 0, "quantity")), "position.*missing"),
    (set_value(("portfolio", "positions", 0, "symbol"), "UNKNOWN"), "not in universe"),
    (lambda b: b["portfolio"]["positions"].append(copy.deepcopy(b["portfolio"]["positions"][0])), "unique"),
    (set_value(("portfolio", "positions", 0, "quantity"), "-1"), "nonnegative"),
    (set_value(("evidence",), {}), "evidence must be an array"),
    (set_value(("evidence", 0), "e-1"), "must be an object"),
    (delete(("evidence", 0, "source")), "evidence\\[0\\] missing"),
    (set_value(("evidence", 0, "evidence_id"), ""), "evidence_id"),
    (lambda b: b["evidence"].append(copy.deepcopy(b["evidence"][0])), "IDs must be unique"),
    (set_value(("evidence", 0, "kind"), "rumor"), "kind is invalid"),
    (set_value(("evidence", 0, "source"), ""), "source"),
    (set_value(("evidence", 0, "published_at"), "2026-11-15T19:02:00-06:00"), "availability"),
    (set_value(("evidence", 0, "expires_at"), "2026-11-15T19:01:00-06:00"), "follow availability"),
    (set_value(("evidence", 0, "event_at"), "not-time"), "ISO8601"),
    (set_value(("evidence", 0, "symbols"), ["UNKNOWN"]), "symbols must be in universe"),
    (set_value(("evidence", 0, "symbols"), ["SPY", "SPY"]), "symbols must be unique"),
    (set_value(("evidence", 0, "content"), 7), "content exceeds"),
    (set_value(("evidence", 0, "metrics"), []), "metrics must be an object"),
    (set_value(("evidence", 0, "metrics"), {"bad": True}), "finite numerical"),
    (set_value(("max_snapshot_age_seconds",), -1), "integer >= 0"),
    (set_value(("max_calls",), True), "integer >= 1"),
])
def test_validate_bundle_rejects_malformed_userdata(mutate, match):
    value = bundle()
    if match == "bundle must be an object":
        value = []
    else:
        mutate(value)
    with pytest.raises(BundleValidationError, match=match):
        validate_bundle(value, now=NOW)


def test_validate_bundle_rejects_oversize_and_non_json_values():
    value = bundle()
    value["extra"] = {1, 2}
    with pytest.raises(BundleValidationError, match="finite JSON"):
        validate_bundle(value, now=NOW)
    value = bundle()
    value["evidence"][0]["content"] = "x" * 1_000_001
    with pytest.raises(BundleValidationError, match="size limit"):
        validate_bundle(value, now=NOW)

    with pytest.raises(BundleValidationError, match="now must be timezone-aware"):
        validate_bundle(bundle(), now=datetime(2026, 11, 15, 20, 0))


def test_validate_bundle_categorizes_unhashable_mode_as_validation_error():
    value = bundle()
    value["mode"] = []
    with pytest.raises(BundleValidationError):
        validate_bundle(value, now=NOW)


@pytest.mark.parametrize("role, mutate", [
    ("research", lambda r: []),
    ("research", lambda r: {k: v for k, v in r.items() if k != "summary"}),
    ("research", lambda r: {**r, "summary": 1}),
    ("research", lambda r: {**r, "candidate_symbols": "SPY"}),
    ("research", lambda r: {**r, "candidate_symbols": ["UNKNOWN"]}),
    ("research", lambda r: {**r, "evidence_ids": ["unknown"]}),
    ("research", lambda r: {**r, "counterevidence": [1]}),
    ("cio", lambda r: []),
    ("cio", lambda r: {k: v for k, v in r.items() if k != "action"}),
    ("cio", lambda r: {**r, "action": "buy"}),
    ("cio", lambda r: {**r, "targets": []}),
    ("cio", lambda r: {**r, "cash_weight": 0.4}),
    ("cio", lambda r: {**r, "cash_weight": "wat"}),
    ("cio", lambda r: {**r, "cash_weight": "NaN"}),
    ("cio", lambda r: {**r, "targets": [[], r["targets"][1]]}),
    ("cio", lambda r: {**r, "targets": [{k: v for k, v in r["targets"][0].items() if k != "thesis"}, r["targets"][1]]}),
    ("cio", lambda r: {**r, "targets": [{**r["targets"][0], "symbol": "UNKNOWN"}, r["targets"][1]]}),
    ("cio", lambda r: {**r, "targets": [{**r["targets"][0], "thesis": 1}, r["targets"][1]]}),
    ("cio", lambda r: {**r, "targets": list(reversed(r["targets"]))}),
    ("cio", lambda r: {**r, "cash_weight": "0.50"}),
    ("cio", lambda r: {**r, "rationale": 1}),
    ("cio", lambda r: {**r, "expires_at": NOW}),
    ("critic", lambda r: []),
    ("critic", lambda r: {"verdict": "pass"}),
    ("critic", lambda r: {**r, "verdict": "approve"}),
    ("critic", lambda r: {**r, "issues": [1]}),
    ("critic", lambda r: {**r, "evidence_ids": ["unknown"]}),
])
def test_run_lab_rejects_malformed_role_outputs(tmp_path, role, mutate):
    outputs = role_outputs()
    outputs[role] = mutate(copy.deepcopy(outputs[role]))
    result = run_lab(bundle(f"bad-{role}-{len(list(tmp_path.iterdir()))}"), Provider(outputs), tmp_path, now=NOW)
    assert result["status"] == "failed"
    assert result["failure_type"] == "response_validation"
    assert result["executable"] is False


def test_run_lab_rejects_bad_provider_envelopes_and_sanitizes_receipts(tmp_path):
    bad_envelopes = [
        None,
        {"text": "{}"},
        {"text": 3, "model": "m", "usage": {}, "latency_ms": 1, "request_id": None},
        {"text": "x" * 500_001, "model": "m", "usage": {}, "latency_ms": 1, "request_id": None},
    ]
    for index, envelope in enumerate(bad_envelopes):
        outputs = role_outputs()
        outputs["research"] = lambda role, payload, envelope=envelope: envelope
        result = run_lab(bundle(f"bad-envelope-{index}"), Provider(outputs), tmp_path, now=NOW)
        assert result["failure_type"] == "invalid_response"

    receipts = {"research": {
        "model": "m" * 300,
        "usage": {"input_tokens": 1, "output_tokens": "2", "cost": "x" * 101,
                  "secret": "must-not-persist"},
        "latency_ms": True,
        "request_id": "r" * 300,
    }}
    result = run_lab(bundle("receipt-sanitize"), Provider(receipts=receipts), tmp_path, now=NOW)
    receipt = json.loads((tmp_path / "receipt-sanitize" / "manifest.json").read_text())["model_receipts"][0]
    assert len(receipt["model"]) == len(receipt["request_id"]) == 200
    assert receipt["usage"] == {"input_tokens": 1, "output_tokens": "2"}
    assert receipt["latency_ms"] is None
    assert "must-not-persist" not in "".join(p.read_text() for p in (tmp_path / "receipt-sanitize").glob("*.json"))
    assert result["status"] == "accepted"


@pytest.mark.parametrize("role", ["cio", "critic"])
def test_run_lab_rejects_bad_envelope_at_each_decision_role(tmp_path, role):
    outputs = role_outputs()
    outputs[role] = lambda called_role, payload: None
    result = run_lab(bundle(f"bad-{role}-envelope"), Provider(outputs), tmp_path, now=NOW)
    assert result["status"] == "failed"
    assert result["failure_type"] == "invalid_response"
    assert result["executable"] is False


def test_run_lab_blocks_expiry_budget_abstention_and_midrun_expiry(tmp_path):
    expired = bundle("expired-bundle")
    expired["expires_at"] = NOW
    assert run_lab(expired, Provider(), tmp_path, now=NOW)["failure_type"] == "input_validation"

    abstain = role_outputs()
    abstain["cio"]["action"] = "abstain"
    assert run_lab(bundle("cio-abstain"), Provider(abstain), tmp_path, now=NOW)["reason"] == "cio_abstain"

    ticks = iter([NOW, NOW, NOW, "2026-11-15T20:46:00-06:00"])
    provider = Provider()
    result = run_lab(bundle("evidence-expires-midrun"), provider, tmp_path, now=lambda: next(ticks))
    assert result["status"] == "blocked"
    assert result["reason"] == "stale_snapshot"
    assert provider.calls == ["research", "cio"]

    value = bundle("expired-at-evaluation")
    value["max_snapshot_age_seconds"] = 10_000
    value["evidence"][0]["expires_at"] = "2026-11-15T22:00:00-06:00"
    result = run_lab(value, Provider(), tmp_path, now="2026-11-15T21:01:00-06:00")
    assert result["reason"] == "expired_bundle"


def test_run_lab_rechecks_evidence_and_proposal_expiry_between_roles(tmp_path):
    value = bundle("evidence-expiry-after-research")
    value["max_snapshot_age_seconds"] = 10_000
    ticks = iter([NOW, NOW, "2026-11-15T20:46:00-06:00"])
    result = run_lab(value, Provider(), tmp_path, now=lambda: next(ticks))
    assert result["reason"] == "no_current_evidence"

    value = bundle("proposal-expiry-after-critic")
    value["max_snapshot_age_seconds"] = 10_000
    value["evidence"][0]["expires_at"] = "2026-11-15T20:59:00-06:00"
    ticks = iter([NOW, NOW, NOW, NOW, "2026-11-15T20:31:00-06:00"])
    result = run_lab(value, Provider(), tmp_path, now=lambda: next(ticks))
    assert result["reason"] == "proposal_expired"
    assert result["cio"]["action"] == "rebalance"


def test_run_lab_rejects_invalid_run_id_before_reserving_output(tmp_path):
    with pytest.raises(BundleValidationError, match="run_id"):
        run_lab([], Provider(), tmp_path, now=NOW)
    with pytest.raises(BundleValidationError, match="run_id"):
        run_lab({"run_id": "../escape"}, Provider(), tmp_path, now=NOW)


def rehash(directory: Path, name: str):
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][name] = hash_file(directory / name)
    manifest_path.write_text(json.dumps(manifest))


def completed_run(tmp_path, run_id):
    run_lab(bundle(run_id), Provider(), tmp_path, now=NOW)
    return tmp_path / run_id


@pytest.mark.parametrize("tamper, match", [
    (lambda d: (d / "input.json").unlink(), "artifact set changed|input.json"),
    (lambda d: (d / "extra.json").write_text("{}"), "artifact set changed"),
    (lambda d: (d / "manifest.json").write_text(json.dumps({"files": {}})), "manifest is incomplete"),
])
def test_replay_rejects_missing_extra_or_incomplete_artifacts(tmp_path, tamper, match):
    directory = completed_run(tmp_path, f"replay-{len(list(tmp_path.iterdir()))}")
    tamper(directory)
    with pytest.raises(ValueError, match=match):
        replay_run(directory)


def test_replay_rejects_self_consistent_input_and_executable_tampering(tmp_path):
    directory = completed_run(tmp_path, "input-tamper")
    input_path = directory / "input.json"
    value = json.loads(input_path.read_text())
    value["mandate_version"] = "tampered"
    input_path.write_text(json.dumps(value))
    rehash(directory, "input.json")
    with pytest.raises(ValueError, match="input hash"):
        replay_run(directory)

    directory = completed_run(tmp_path, "executable-tamper")
    decision_path = directory / "decision.json"
    decision = json.loads(decision_path.read_text())
    decision["executable"] = True
    decision_path.write_text(json.dumps(decision))
    rehash(directory, "decision.json")
    with pytest.raises(ValueError, match="executable invariant"):
        replay_run(directory)


def test_replay_rejects_duplicate_response_artifacts(tmp_path):
    directory = completed_run(tmp_path, "duplicate-response")
    original = next(directory.glob("response-??-cio.json"))
    duplicate = directory / "response-99-cio.json"
    duplicate.write_bytes(original.read_bytes())
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][duplicate.name] = hash_file(duplicate)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="duplicate cio response"):
        replay_run(directory)



def test_replay_rejects_invalid_cio_artifact_for_accepted_run(tmp_path):
    directory = completed_run(tmp_path, "invalid-cio-artifact")
    response = next(directory.glob("response-??-cio.json"))
    response.write_text(json.dumps({"valid_json": False}))
    rehash(directory, response.name)
    with pytest.raises(ValueError, match="integrity"):
        replay_run(directory)


def test_replay_rejects_unsafe_run_paths(tmp_path):
    with pytest.raises(ValueError, match="unsafe run directory"):
        replay_run(tmp_path / "missing")
    directory = tmp_path / "missing-manifest"
    directory.mkdir()
    with pytest.raises(ValueError, match="manifest missing"):
        replay_run(directory)


def test_replay_single_cio_run_and_rejects_invalid_input_only_manifest(tmp_path):
    outputs = {"cio": role_outputs()["cio"]}
    run_lab(bundle("single-cio-replay"), Provider(outputs), tmp_path, now=NOW,
            research=False, critic=False)
    replayed = replay_run(tmp_path / "single-cio-replay")
    assert replayed["verification_kind"] == "deterministic_replay"

    invalid = bundle("invalid-input-manifest")
    invalid["portfolio"]["equity"] = "NaN"
    run_lab(invalid, Provider(), tmp_path, now=NOW)
    manifest_path = tmp_path / "invalid-input-manifest" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["input_hash"] = "fabricated"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="missing input"):
        replay_run(tmp_path / "invalid-input-manifest")


def test_hold_response_rejects_cash_mismatch_and_negative_cash(tmp_path):
    outputs = role_outputs()
    outputs["cio"].update(action="hold", cash_weight="0.40")
    outputs["cio"]["targets"][0]["weight"] = "0.50"
    outputs["cio"]["targets"][1]["weight"] = "0.10"
    result = run_lab(bundle("hold-cash-mismatch"), Provider(outputs), tmp_path, now=NOW)
    assert result["failure_type"] == "response_validation"

    value = bundle("negative-cash-hold")
    value["portfolio"]["cash"] = "-0.05"
    value["portfolio"]["positions"][0]["market_value"] = "1000.05"
    outputs = role_outputs()
    outputs["cio"].update(action="hold", cash_weight="0.00")
    outputs["cio"]["targets"][0]["weight"] = "1.00"
    result = run_lab(value, Provider(outputs), tmp_path, now=NOW)
    assert result["failure_type"] == "response_validation"
