"""Provider seam tests use explicit fixtures, never real inference."""
from types import SimpleNamespace as N

import pytest

from decision_lab.providers import InferenceAccessError, NousProvider


class FixtureBackend:
    def __init__(self):
        self.calls = []
        self.balance = 10.0
        self.keep_going = False
        self.window = False
        self.available_models = ["fixture/model"]
        self.response = N(id="fixture-request", model="fixture/model", choices=[N(message=N(content='{"fixture":true}'), finish_reason="stop")],
                          usage=N(prompt_tokens=10, completion_tokens=20, total_tokens=30))

    def resolve(self, model):
        return {"provider": "nous", "source": "device_code", "api_mode": "chat_completions", "base_url": "https://inference-api.nousresearch.com/v1", "api_key": "fixture-credential-never-persist"}

    def account(self):
        return N(logged_in=True, source="account_api", fresh=True, error=None,
                 paid_service_access=True,
                 paid_service_access_info=N(subscription_credits_remaining=self.balance, member_spend_cap_exceeded=False),
                 usage_window=N(keep_going_on_credits=self.keep_going, paused_reason=None, gauges=()) if self.window else None)

    def models(self, runtime):
        return self.available_models

    def pricing(self):
        return {"fixture/model": {"prompt": "0.000002", "completion": "0.00001"}}

    def invoke(self, runtime, arguments, timeout):
        self.calls.append(arguments)
        return self.response


def test_provider_uses_existing_allowance_and_records_only_safe_receipt_fields():
    backend = FixtureBackend()
    provider = NousProvider("fixture/model", backend=backend)
    result = provider.complete("research", {"instructions": "fixture"})
    assert result["text"] == '{"fixture":true}'
    assert result["request_id"] == "fixture-request"
    assert result["usage"] == {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}
    assert "fixture-credential" not in str(result)
    assert backend.calls[0]["stream"] is False
    assert "tools" not in backend.calls[0]
    assert backend.calls[0]["max_tokens"] == 4000


@pytest.mark.parametrize("change,expected", [
    (lambda b: setattr(b, "balance", 0), "subscription_credits_insufficient"),
    (lambda b: setattr(b, "balance", float("nan")), "unknown_allowance_or_price"),
    (lambda b: setattr(b, "available_models", []), "model_not_available"),
    (lambda b: (setattr(b, "window", True), setattr(b, "keep_going", True)), "overage_control_unverified"),
    (lambda b: setattr(b, "window", True), "unknown_usage_window"),
])
def test_access_failures_never_invoke_a_model(change, expected):
    backend = FixtureBackend()
    change(backend)
    with pytest.raises(InferenceAccessError) as caught:
        NousProvider("fixture/model", backend=backend).complete("research", {})
    assert caught.value.code == expected
    assert backend.calls == []


def test_truncated_completion_is_not_silently_accepted_or_retried():
    backend = FixtureBackend()
    backend.response.choices[0].finish_reason = "length"
    with pytest.raises(InferenceAccessError, match="incomplete_completion"):
        NousProvider("fixture/model", backend=backend).complete("cio", {})
    assert len(backend.calls) == 1


def test_sdk_error_is_categorical_without_secret_body_or_fallback():
    class FixtureQuotaError(Exception):
        status_code = 429
    class QuotaBackend(FixtureBackend):
        def invoke(self, runtime, arguments, timeout):
            raise FixtureQuotaError("fixture-credential-never-persist and private HTTP headers")
    with pytest.raises(InferenceAccessError) as caught:
        NousProvider("fixture/model", backend=QuotaBackend()).complete("critic", {})
    assert caught.value.code == "rate_limited"
    assert str(caught.value) == "rate_limited"
    assert "credential" not in str(caught.value)


@pytest.mark.parametrize("settings", [{"max_tokens": 0}, {"max_tokens": True}, {"timeout": float("nan")}, {"timeout": False}])
def test_invalid_runtime_bounds_are_rejected_before_any_account_reads(settings):
    with pytest.raises(ValueError):
        NousProvider("fixture/model", **settings)
