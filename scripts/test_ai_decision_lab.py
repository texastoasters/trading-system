from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from decision_lab import BundleValidationError, replay_run, run_lab, validate_bundle
from decision_lab.core import _prompt
from decision_lab.storage import hash_file

NOW = "2026-11-15T20:00:00-06:00"


def bundle(run_id: str = "fixture-run") -> dict:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "as_of": NOW,
        "expires_at": "2026-11-15T21:00:00-06:00",
        "mode": "shadow",
        "mandate_version": "fixture-v1",
        "universe": [
            {"symbol": "SPY", "asset_class": "etf"},
            {"symbol": "CASHLIKE", "asset_class": "equity"},
        ],
        "portfolio": {
            "snapshot_id": "fixture-snapshot",
            "observed_at": "2026-11-15T19:59:00-06:00",
            "cash": "500.00",
            "equity": "1000.00",
            "pending_buy_notional": "0",
            "positions": [{"symbol": "SPY", "quantity": "1", "market_value": "500.00"}],
        },
        "evidence": [
            {
                "evidence_id": "news-1",
                "kind": "news",
                "source": "fixture-wire",
                "published_at": "2026-11-15T19:00:00-06:00",
                "available_at": "2026-11-15T19:01:00-06:00",
                "expires_at": "2026-11-16T19:00:00-06:00",
                "symbols": ["SPY"],
                "content": "FIXTURE: independently supplied market report.",
            },
            {
                "evidence_id": "event-1",
                "kind": "scheduled_event",
                "source": "fixture-calendar",
                "published_at": "2026-11-15T18:00:00-06:00",
                "available_at": "2026-11-15T18:00:00-06:00",
                "event_at": "2026-11-16T08:30:00-06:00",
                "expires_at": "2026-11-16T09:00:00-06:00",
                "symbols": ["SPY"],
                "content": "FIXTURE: event schedule, not an event result.",
            },
        ],
        "max_snapshot_age_seconds": 300,
        "max_calls": 3,
    }


def responses(*, critic="pass", action="rebalance") -> list[dict]:
    research = {
        "summary": "Fixture synthesis.",
        "candidate_symbols": ["SPY"],
        "evidence_ids": ["news-1", "event-1"],
        "counterevidence": ["Event outcome is not yet known."],
        "missing_data": [],
    }
    cio = {
        "action": action,
        "targets": [
            {
                "symbol": "SPY",
                "weight": "0.60" if action == "rebalance" else "0.50",
                "thesis": "Fixture thesis.",
                "evidence_ids": ["news-1"],
                "falsifier": "Fixture report is contradicted.",
            },
            {
                "symbol": "CASHLIKE",
                "weight": "0.00",
                "thesis": "No supported risk.",
                "evidence_ids": [],
                "falsifier": "Applicable evidence appears.",
            },
        ],
        "cash_weight": "0.40" if action == "rebalance" else "0.50",
        "rationale": "Fixture allocation.",
        "evidence_ids": ["news-1"],
        "counterevidence": ["Scheduled event remains unresolved."],
        "expires_at": "2026-11-15T20:30:00-06:00",
    }
    critic_response = {
        "verdict": critic,
        "issues": [] if critic == "pass" else ["Fixture critic veto."],
        "evidence_ids": ["news-1"],
    }
    return [research, cio, critic_response]


class FixtureProvider:
    def __init__(self, items=None, error: Exception | None = None):
        self.items = list(items or responses())
        self.error = error
        self.calls = []

    def complete(self, role, payload):
        self.calls.append((role, payload))
        if self.error:
            raise self.error
        item = self.items.pop(0)
        return {
            "text": json.dumps(item),
            "model": "fixture-model-not-real-inference",
            "usage": {"input_tokens": 10, "output_tokens": 10},
            "latency_ms": 1,
            "request_id": None,
        }


def test_prompts_include_explicit_role_contracts():
    research = _prompt("research", bundle(), {})["instructions"]["role_contract"]
    cio = _prompt("cio", bundle(), {})["instructions"]["role_contract"]
    critic = _prompt("critic", bundle(), {})["instructions"]["role_contract"]
    assert set(research["required_json"]) == {
        "summary", "candidate_symbols", "evidence_ids", "counterevidence", "missing_data"
    }
    assert "exactly one target for every universe symbol" in " ".join(cio["requirements"])
    assert "JSON strings" in " ".join(cio["requirements"])
    assert set(critic["required_json"]) == {"verdict", "issues", "evidence_ids"}


def test_validate_bundle_accepts_scheduled_future_event_without_claiming_result():
    validated = validate_bundle(bundle(), now=NOW)
    assert validated["evidence"][1]["event_at"] == "2026-11-16T08:30:00-06:00"


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda b: b.update(run_id="../escape"), "run_id"),
        (lambda b: b["portfolio"].update(equity="NaN"), "finite"),
        (lambda b: b["portfolio"].update(cash="499.80"), "reconcile"),
        (lambda b: b["evidence"][0].update(available_at="2026-11-15T20:01:00-06:00"), "available_at"),
        (lambda b: b["evidence"][0].update(symbols=["INVENTED"]), "universe"),
    ],
)
def test_validate_bundle_rejects_invalid_inputs(mutate, match):
    value = bundle()
    mutate(value)
    with pytest.raises(BundleValidationError, match=match):
        validate_bundle(value, now=NOW)


def test_run_generates_deterministic_nonexecutable_preview_and_replays(tmp_path):
    provider = FixtureProvider()
    result = run_lab(bundle(), provider, tmp_path, now=NOW)

    assert result["status"] == "accepted"
    assert result["executable"] is False
    assert result["preview"]["targets"][0] == {
        "symbol": "SPY",
        "target_value": "600.00",
        "current_value": "500.00",
        "delta": "100.00",
        "direction": "increase",
    }
    assert result["preview"]["requires_revalidation"] is True
    assert [call[0] for call in provider.calls] == ["research", "cio", "critic"]
    run_dir = tmp_path / "fixture-run"
    assert (run_dir / "manifest.json").is_file()
    replayed = replay_run(run_dir)
    assert replayed["verified"] is True
    assert replayed["replayed"] is True
    assert replayed["verification_kind"] == "deterministic_replay"


def test_critic_veto_preserves_proposal_but_does_not_accept(tmp_path):
    result = run_lab(bundle("critic-veto"), FixtureProvider(responses(critic="revise")), tmp_path, now=NOW)
    assert result["status"] == "blocked"
    assert result["reason"] == "critic_revise"
    assert result["cio"]["action"] == "rebalance"
    assert result["executable"] is False


def test_invented_citation_and_symbol_fail_closed(tmp_path):
    bad = responses()
    bad[1]["targets"][0]["evidence_ids"] = ["invented"]
    result = run_lab(bundle("bad-citation"), FixtureProvider(bad), tmp_path, now=NOW)
    assert result["status"] == "failed"
    assert result["failure_type"] == "response_validation"

    bad = responses()
    bad[1]["targets"][0]["symbol"] = "INVENTED"
    result = run_lab(bundle("bad-symbol"), FixtureProvider(bad), tmp_path, now=NOW)
    assert result["status"] == "failed"
    assert result["failure_type"] == "response_validation"

    bad = responses()
    bad[1]["targets"][1]["weight"] = "0.10"
    bad[1]["targets"][1]["evidence_ids"] = ["news-1"]
    bad[1]["targets"][0]["weight"] = "0.50"
    result = run_lab(bundle("inapplicable-citation"), FixtureProvider(bad), tmp_path, now=NOW)
    assert result["status"] == "failed"
    assert result["failure_type"] == "response_validation"


def test_bad_json_nonfinite_and_provider_failure_are_durable_and_sanitized(tmp_path):
    bad_json = FixtureProvider()
    bad_json.items[0] = float("nan")
    result = run_lab(bundle("bad-json"), bad_json, tmp_path, now=NOW)
    assert result["status"] == "failed"
    assert result["failure_type"] == "invalid_json"
    assert (tmp_path / "bad-json" / "decision.json").is_file()
    bad_artifact = json.loads(
        next((tmp_path / "bad-json").glob("response-*-research.json")).read_text()
    )
    assert bad_artifact["receipt"]["model"] == "fixture-model-not-real-inference"
    # Malformed output keeps an auditable receipt/hash, never its unvalidated text.
    assert "raw_text" not in bad_artifact
    assert bad_artifact["response_bytes"] > 0
    assert len(bad_artifact["response_sha256"]) == 64

    class SecretQuotaError(Exception):
        pass

    failed = run_lab(
        bundle("provider-failure"),
        FixtureProvider(error=SecretQuotaError("token=do-not-save secret header")),
        tmp_path,
        now=NOW,
    )
    assert failed["failure_type"] == "provider_error"
    saved = "".join(p.read_text() for p in (tmp_path / "provider-failure").glob("*.json"))
    assert "do-not-save" not in saved
    assert "SecretQuotaError" not in saved


def test_stale_shadow_and_call_budget_block_before_inference(tmp_path):
    stale = bundle("stale")
    stale["portfolio"]["observed_at"] = "2026-11-15T19:00:00-06:00"
    provider = FixtureProvider()
    result = run_lab(stale, provider, tmp_path, now=NOW)
    assert result["status"] == "blocked"
    assert result["reason"] == "stale_snapshot"
    assert provider.calls == []

    invalid = bundle("invalid-input")
    invalid["portfolio"]["equity"] = "NaN"
    result = run_lab(invalid, provider, tmp_path, now=NOW)
    assert result["status"] == "failed"
    assert result["failure_type"] == "input_validation"
    assert (tmp_path / "invalid-input" / "decision.json").is_file()
    invalid_replay = replay_run(tmp_path / "invalid-input")
    assert invalid_replay["verification_kind"] == "integrity_only"
    assert invalid_replay["replayed"] is False
    assert provider.calls == []

    limited = bundle("budget")
    limited["max_calls"] = 2
    provider = FixtureProvider()
    result = run_lab(limited, provider, tmp_path, now=NOW)
    assert result["reason"] == "call_budget_exhausted"
    assert provider.calls == []


def test_shadow_rechecks_live_clock_after_calls_and_historical_uses_as_of(tmp_path):
    ticks = iter([
        "2026-11-15T20:00:00-06:00",
        "2026-11-15T20:00:00-06:00",
        "2026-11-15T20:00:00-06:00",
        "2026-11-15T20:00:00-06:00",
        "2026-11-15T20:06:00-06:00",
    ])
    result = run_lab(bundle("clock-expired"), FixtureProvider(), tmp_path, now=lambda: next(ticks))
    assert result["status"] == "blocked"
    assert result["reason"] == "stale_snapshot"

    historical = bundle("old-history")
    historical["mode"] = "historical_diagnostic"
    historical["as_of"] = "2024-01-01T12:00:00+00:00"
    historical["expires_at"] = "2024-01-01T13:00:00+00:00"
    historical["portfolio"]["observed_at"] = "2024-01-01T11:59:00+00:00"
    for item in historical["evidence"]:
        item["published_at"] = "2024-01-01T10:00:00+00:00"
        item["available_at"] = "2024-01-01T10:01:00+00:00"
        item["expires_at"] = "2024-01-02T10:00:00+00:00"
        item.pop("event_at", None)
    old_responses = responses()
    old_responses[1]["expires_at"] = "2024-01-01T12:30:00+00:00"
    result = run_lab(
        historical,
        FixtureProvider(old_responses),
        tmp_path,
        now="2026-11-15T20:00:00-06:00",
    )
    assert result["status"] == "accepted"
    assert result["prospective"] is False


def test_expired_evidence_cannot_support_added_risk_but_historical_is_labelled(tmp_path):
    expired = bundle("expired-shadow")
    expired["evidence"][0]["expires_at"] = "2026-11-15T19:30:00-06:00"
    expired["evidence"][1]["expires_at"] = "2026-11-15T19:30:00-06:00"
    result = run_lab(expired, FixtureProvider(), tmp_path, now=NOW)
    assert result["status"] == "blocked"
    assert result["reason"] == "no_current_evidence"

    historical = copy.deepcopy(expired)
    historical["run_id"] = "expired-history"
    historical["mode"] = "historical_diagnostic"
    result = run_lab(historical, FixtureProvider(), tmp_path, now=NOW)
    assert result["mode"] == "historical_diagnostic"
    assert result["prospective"] is False
    assert result["executable"] is False


def test_negative_cash_or_pending_buys_block_added_risk(tmp_path):
    negative = bundle("negative-cash")
    negative["portfolio"]["cash"] = "-100.00"
    negative["portfolio"]["positions"][0]["market_value"] = "1100.00"
    negative["evidence"][0]["symbols"] = ["SPY", "CASHLIKE"]
    proposal = responses()
    proposal[1]["targets"][0]["weight"] = "0.90"
    proposal[1]["targets"][1]["weight"] = "0.10"
    proposal[1]["targets"][1]["evidence_ids"] = ["news-1"]
    proposal[1]["cash_weight"] = "0.00"
    result = run_lab(negative, FixtureProvider(proposal), tmp_path, now=NOW)
    assert result["status"] == "blocked"
    assert result["reason"] == "negative_cash_added_risk"

    pending = bundle("pending-buy")
    pending["portfolio"]["pending_buy_notional"] = "100.00"
    result = run_lab(pending, FixtureProvider(), tmp_path, now=NOW)
    assert result["status"] == "blocked"
    assert result["reason"] == "pending_buys_added_risk"


def test_hold_must_match_current_portfolio(tmp_path):
    result = run_lab(bundle("valid-hold"), FixtureProvider(responses(action="hold")), tmp_path, now=NOW)
    assert result["status"] == "accepted"

    mismatched = responses(action="hold")
    mismatched[1]["targets"][0]["weight"] = "0.40"
    mismatched[1]["cash_weight"] = "0.60"
    result = run_lab(bundle("bad-hold"), FixtureProvider(mismatched), tmp_path, now=NOW)
    assert result["status"] == "failed"
    assert result["failure_type"] == "response_validation"


def test_duplicate_run_tampering_and_unsafe_manifest_paths_are_rejected(tmp_path):
    run_lab(bundle("immutable"), FixtureProvider(), tmp_path, now=NOW)
    with pytest.raises(FileExistsError):
        run_lab(bundle("immutable"), FixtureProvider(), tmp_path, now=NOW)

    path = tmp_path / "immutable" / "decision.json"
    saved = json.loads(path.read_text())
    saved["executable"] = True
    path.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="integrity"):
        replay_run(tmp_path / "immutable")

    run_lab(bundle("self-consistent-tamper"), FixtureProvider(), tmp_path, now=NOW)
    tamper_dir = tmp_path / "self-consistent-tamper"
    decision_path = tamper_dir / "decision.json"
    decision = json.loads(decision_path.read_text())
    decision["preview"]["targets"][0]["target_value"] = "999.00"
    decision_path.write_text(json.dumps(decision))
    manifest_path = tamper_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["decision.json"] = hash_file(decision_path)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="deterministic decision mismatch"):
        replay_run(tamper_dir)

    run_lab(bundle("unsafe-manifest"), FixtureProvider(), tmp_path, now=NOW)
    manifest_path = tmp_path / "unsafe-manifest" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["../outside.json"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="unsafe manifest path"):
        replay_run(tmp_path / "unsafe-manifest")


def test_no_research_and_no_critic_respect_declared_call_shape(tmp_path):
    items = responses()[1:2]
    provider = FixtureProvider(items)
    result = run_lab(
        bundle("single-cio"), provider, tmp_path, now=NOW, research=False, critic=False
    )
    assert result["status"] == "accepted"
    assert [call[0] for call in provider.calls] == ["cio"]


def test_cli_offline_validate_and_replay(tmp_path):
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(bundle("cli-replay")))
    script = Path(__file__).with_name("ai_decision_lab.py")
    validated = subprocess.run(
        [sys.executable, str(script), "validate", str(input_path), "--now", NOW],
        text=True,
        capture_output=True,
        check=False,
    )
    assert validated.returncode == 0, validated.stderr
    assert json.loads(validated.stdout)["valid"] is True

    run_lab(bundle("cli-replay"), FixtureProvider(), tmp_path, now=NOW)
    replayed = subprocess.run(
        [sys.executable, str(script), "replay", str(tmp_path / "cli-replay")],
        text=True,
        capture_output=True,
        check=False,
    )
    assert replayed.returncode == 0, replayed.stderr
    assert json.loads(replayed.stdout)["verified"] is True

    missing = subprocess.run(
        [sys.executable, str(script), "replay", "token=do-not-print"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert missing.returncode == 2
    assert "do-not-print" not in missing.stdout
    assert json.loads(missing.stdout)["failure_type"] == "replay_error"
