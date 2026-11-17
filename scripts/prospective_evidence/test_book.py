"""Declared synthetic books are never labelled as broker observations."""
import json
import copy

import pytest
from prospective_evidence import book


def test_explicit_cash_seed_is_labelled_synthetic_not_broker():
    result = book.prepare_book(
        [{"symbol": "SPY", "asset_class": "etf"}],
        as_of="2026-01-22T01:05:00Z", expires_at="2026-01-22T01:20:00Z",
        research_cash="100000.00", book_id="fixture-research-book",
    )
    assert result["portfolio"]["cash"] == "100000.00"
    assert result["portfolio"]["positions"] == []
    metadata = json.loads(result["evidence"]["content"])
    assert metadata["origin"] == "synthetic_cash_only_research_book_not_broker"
    assert metadata["broker_observation"] is False
    assert result["evidence"]["available_at"] == "2026-01-22T01:05:00Z"


def test_caller_portfolio_preserves_observation_and_projects_private_fields():
    portfolio = {"snapshot_id": "caller-exact", "observed_at": "2026-01-01T00:00:00Z",
                 "cash": "50", "equity": "150", "pending_buy_notional": "0",
                 "positions": [{"symbol": "SPY", "quantity": "1", "market_value": "100", "private": "do-not-save"}],
                 "credentials": "do-not-save"}
    original = copy.deepcopy(portfolio)
    result = book.prepare_book([{"symbol": "SPY", "asset_class": "etf"}],
        as_of="2026-01-22T01:05:00Z", expires_at="2026-01-22T01:20:00Z",
        portfolio=portfolio, book_id="fixture-caller")
    assert result["portfolio"]["observed_at"] == "2026-01-01T00:00:00Z"
    assert result["portfolio"]["snapshot_id"] == "caller-exact"
    assert "do-not-save" not in json.dumps(result)
    assert portfolio == original
    assert json.loads(result["evidence"]["content"])["origin"] == "caller_supplied_portfolio_not_broker_verified"


@pytest.mark.parametrize("inputs", [
    {}, {"research_cash": "100", "portfolio": {}}, {"research_cash": "NaN"},
    {"research_cash": "0"}, {"research_cash": "-1"}, {"research_cash": 100},
    {"portfolio": []}, {"portfolio": {}},
])
def test_ambiguous_or_invalid_book_rejected(inputs):
    with pytest.raises(book.BookError):
        book.prepare_book([{"symbol": "SPY", "asset_class": "etf"}],
            as_of="2026-01-22T01:05:00Z", expires_at="2026-01-22T01:20:00Z",
            book_id="fixture-invalid", **inputs)


def test_invalid_book_identity_rejected():
    with pytest.raises(book.BookError):
        book.prepare_book([{"symbol": "SPY", "asset_class": "etf"}],
            as_of="2026-01-22T01:05:00Z", expires_at="2026-01-22T01:20:00Z",
            research_cash="100", book_id="../not-an-identity")
