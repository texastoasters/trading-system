"""Explicit experiment books, never implicit broker-account replacements."""
import json

from decision_lab.storage import hash_value
from decision_lab.validation import RUN_ID_RE, validate_bundle


class BookError(ValueError):
    pass


def _prepare(universe, as_of, expires_at, book_id, research_cash, portfolio):
    if (not isinstance(book_id, str) or not RUN_ID_RE.fullmatch(book_id)
            or (research_cash is None) == (portfolio is None)):
        raise BookError("invalid_declared_book")
    metadata = {"broker_observation": False, "book_id": book_id}
    if portfolio is None:
        metadata.update({"origin": "synthetic_cash_only_research_book_not_broker",
                         "initial_cash": research_cash,
                         "caveat": "Declared experimental capital, not broker cash, fills, or profit."})
        value = {"snapshot_id": "research-book-" + hash_value(metadata)[:24],
                 "observed_at": as_of, "cash": research_cash, "equity": research_cash,
                 "pending_buy_notional": "0", "positions": []}
    else:
        if not isinstance(portfolio, dict) or not isinstance(portfolio["positions"], list):
            raise BookError("invalid_declared_book")
        value = {key: portfolio[key] for key in (
            "snapshot_id", "observed_at", "cash", "equity", "pending_buy_notional")}
        value["positions"] = [{key: item[key] for key in ("symbol", "quantity", "market_value")}
                              for item in portfolio["positions"]]
        metadata.update({"origin": "caller_supplied_portfolio_not_broker_verified",
                         "caveat": "Caller-declared portfolio; original observation time retained, not broker-verified here."})
    validate_bundle({"schema_version": 1, "run_id": "book-validation", "mode": "shadow",
                     "as_of": as_of, "expires_at": expires_at, "mandate_version": "research-book-v1",
                     "universe": universe, "portfolio": value, "evidence": []}, now=as_of)
    metadata["portfolio_fingerprint"] = hash_value(value)
    return {"portfolio": value, "evidence": {
        "evidence_id": "book-" + hash_value(metadata)[:24], "kind": "account",
        "source": "explicit-local-research-book", "published_at": as_of,
        "available_at": as_of, "expires_at": expires_at, "symbols": [],
        "content": json.dumps(metadata, sort_keys=True),
    }}


def prepare_book(universe, *, as_of, expires_at, book_id, research_cash=None, portfolio=None):
    """Validate exactly one explicit origin, project fields, never refresh old observations."""
    try:
        return _prepare(universe, as_of, expires_at, book_id, research_cash, portfolio)
    except BookError:
        raise
    except (KeyError, TypeError, ValueError):
        raise BookError("invalid_declared_book") from None
