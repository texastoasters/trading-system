"""Standards-review regressions at the public ``run_lab`` seam."""
from __future__ import annotations

import copy
import json

import pytest

import decision_lab.core as core
from decision_lab import run_lab
from decision_lab.providers import InferenceAccessError
from test_ai_decision_lab import NOW, FixtureProvider, bundle, responses


def _saved_text(run_dir):
    return "".join(path.read_text() for path in run_dir.glob("*.json"))


def test_unknown_and_private_model_fields_are_projected_before_any_persistence(tmp_path):
    outputs = responses()
    outputs[0]["private_chain_of_thought"] = "RESEARCH-PRIVATE"
    outputs[1]["unexpected"] = {"private_chain_of_thought": "CIO-PRIVATE"}
    outputs[1]["targets"][0]["private_chain_of_thought"] = "TARGET-PRIVATE"
    outputs[2]["reasoning"] = "CRITIC-PRIVATE"

    result = run_lab(bundle("project-private"), FixtureProvider(outputs), tmp_path, now=NOW)

    assert result["status"] == "accepted"
    saved = _saved_text(tmp_path / "project-private")
    assert "PRIVATE" not in saved
    assert "private_chain_of_thought" not in saved
    assert "unexpected" not in result["cio"]
    assert set(result["cio"]["targets"][0]) == {
        "symbol", "weight", "thesis", "evidence_ids", "falsifier",
    }


def test_malformed_and_nonobject_responses_keep_only_receipt_hash_and_size(tmp_path):
    malformed = FixtureProvider()
    malformed.items[0] = float("nan")
    result = run_lab(bundle("malformed-receipt"), malformed, tmp_path, now=NOW)
    artifact = json.loads(next((tmp_path / "malformed-receipt").glob("response-*.json")).read_text())
    assert result["failure_type"] == "invalid_json"
    assert set(artifact) == {
        "valid_json", "receipt", "failure_type", "response_sha256", "response_bytes",
    }
    assert artifact["response_bytes"] > 0
    assert len(artifact["response_sha256"]) == 64
    assert "raw_text" not in artifact

    outputs = responses()
    outputs[0] = ["do-not-persist-this-object"]
    result = run_lab(bundle("nonobject-receipt"), FixtureProvider(outputs), tmp_path, now=NOW)
    artifact = json.loads(next((tmp_path / "nonobject-receipt").glob("response-*.json")).read_text())
    assert result["failure_type"] == "response_validation"
    assert "response" not in artifact
    assert "do-not-persist-this-object" not in _saved_text(tmp_path / "nonobject-receipt")

    outputs = responses()
    outputs[1]["action"] = {"private_chain_of_thought": "KNOWN-FIELD-PRIVATE"}
    result = run_lab(bundle("unexpected-known-field"), FixtureProvider(outputs), tmp_path, now=NOW)
    assert result["failure_type"] == "response_validation"
    assert "KNOWN-FIELD-PRIVATE" not in _saved_text(tmp_path / "unexpected-known-field")


def test_provider_errors_are_narrowly_categorized_without_retaining_exception_text(tmp_path):
    categorized = run_lab(
        bundle("categorized-provider"),
        FixtureProvider(error=InferenceAccessError("quota_exhausted")),
        tmp_path,
        now=NOW,
    )
    assert categorized["failure_type"] == "provider_error"
    assert categorized["provider_error_code"] == "quota_exhausted"

    class PretendProviderError(RuntimeError):
        code = "quota_exhausted"

    generic = run_lab(
        bundle("generic-provider"),
        FixtureProvider(error=PretendProviderError("PRIVATE-PROVIDER-BODY")),
        tmp_path,
        now=NOW,
    )
    assert generic["failure_type"] == "provider_error"
    assert "provider_error_code" not in generic
    assert "PRIVATE-PROVIDER-BODY" not in _saved_text(tmp_path / "generic-provider")


@pytest.mark.parametrize("failed_role", ["cio", "critic"])
def test_categorical_provider_codes_survive_at_each_later_role(tmp_path, failed_role):
    class FailSelectedRole(FixtureProvider):
        def complete(self, role, payload):
            if role == failed_role:
                raise InferenceAccessError("rate_limited")
            return super().complete(role, payload)

    result = run_lab(
        bundle(f"provider-{failed_role}"), FailSelectedRole(), tmp_path, now=NOW
    )

    assert result["failure_type"] == "provider_error"
    assert result["provider_error_code"] == "rate_limited"


def test_validation_internal_failure_is_recorded_without_model_body(tmp_path, monkeypatch):
    def fail_validation(*args, **kwargs):
        raise RuntimeError("PRIVATE-VALIDATION-DETAIL")

    monkeypatch.setattr(core, "validate_research", fail_validation)
    result = run_lab(bundle("validation-internal"), FixtureProvider(), tmp_path, now=NOW)

    assert result["failure_type"] == "internal_error"
    assert "PRIVATE-VALIDATION-DETAIL" not in _saved_text(tmp_path / "validation-internal")


def test_persistence_failure_propagates_once_without_retry_or_false_record(tmp_path, monkeypatch):
    real_write = core.write_json
    writes = []

    def fail_response_write(path, value):
        writes.append(path.name)
        if path.name.startswith("response-"):
            raise PermissionError("fixture storage unavailable")
        return real_write(path, value)

    monkeypatch.setattr(core, "write_json", fail_response_write)
    provider = FixtureProvider()
    with pytest.raises(PermissionError, match="storage unavailable"):
        run_lab(bundle("storage-failure"), provider, tmp_path, now=NOW)

    assert len(provider.calls) == 1
    assert writes == ["input.json", "prompt-01-research.json", "response-01-research.json"]
    assert not (tmp_path / "storage-failure").exists()
    assert not (tmp_path / ".storage-failure.pending" / "decision.json").exists()


def test_internal_preview_failure_is_durable_and_not_mislabeled_as_provider(tmp_path, monkeypatch):
    def fail_preview(bundle_value, cio_value):
        raise RuntimeError("PRIVATE-INTERNAL-DETAIL")

    monkeypatch.setattr(core, "build_preview", fail_preview)
    result = run_lab(bundle("preview-failure"), FixtureProvider(), tmp_path, now=NOW)

    assert result["status"] == "failed"
    assert result["failure_type"] == "internal_error"
    assert "provider_error_code" not in result
    assert "PRIVATE-INTERNAL-DETAIL" not in _saved_text(tmp_path / "preview-failure")


def test_invalid_source_bundle_remains_a_durable_input_failure(tmp_path):
    value = copy.deepcopy(bundle("source-invalid"))
    value["portfolio"]["equity"] = "NaN"
    provider = FixtureProvider()

    result = run_lab(value, provider, tmp_path, now=NOW)

    assert result["failure_type"] == "input_validation"
    assert result["run_dir"] == str(tmp_path / "source-invalid")
    assert provider.calls == []


def test_nonfinite_receipt_numbers_are_removed_but_cost_strings_survive(tmp_path):
    class ReceiptProvider(FixtureProvider):
        def complete(self, role, payload):
            envelope = super().complete(role, payload)
            if role == "research":
                envelope["usage"] = {"input_tokens": float("inf"), "cost": "0.00125"}
                envelope["latency_ms"] = float("nan")
            return envelope

    provider = ReceiptProvider()

    result = run_lab(bundle("finite-receipts"), provider, tmp_path, now=NOW)
    receipt = json.loads((tmp_path / "finite-receipts" / "manifest.json").read_text())[
        "model_receipts"
    ][0]

    assert result["status"] == "accepted"
    assert receipt["usage"] == {"cost": "0.00125"}
    assert receipt["latency_ms"] is None
