from __future__ import annotations

import json
import math
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

SCHEMA_VERSION = 1
MAX_INPUT_BYTES = 1_000_000
MAX_CONTENT_CHARS = 100_000
MONEY_TOLERANCE = Decimal("0.05")
WEIGHT_TOLERANCE = Decimal("0.0001")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ASSET_CLASSES = {"equity", "etf", "crypto"}
EVIDENCE_KINDS = {
    "news", "scheduled_event", "technical", "algorithmic", "account", "research"
}
MODES = {"shadow", "historical_diagnostic"}


class BundleValidationError(ValueError):
    pass


class ResponseValidationError(ValueError):
    pass


def parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise BundleValidationError(f"{field} must be an ISO8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BundleValidationError(f"{field} must be ISO8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BundleValidationError(f"{field} must be timezone-aware")
    return parsed


def decimal_value(value: Any, field: str, *, nonnegative: bool = False) -> Decimal:
    if not isinstance(value, str):
        raise BundleValidationError(f"{field} must be a decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise BundleValidationError(f"{field} must be a decimal string") from exc
    if not number.is_finite():
        raise BundleValidationError(f"{field} must be finite")
    if nonnegative and number < 0:
        raise BundleValidationError(f"{field} must be nonnegative")
    return number


def _required(value: dict, fields: set[str], label: str) -> None:
    missing = sorted(fields - value.keys())
    if missing:
        raise BundleValidationError(f"{label} missing: {', '.join(missing)}")


def _validate_bundle(bundle: dict, *, now: str | datetime | None = None) -> dict:
    if not isinstance(bundle, dict):
        raise BundleValidationError("bundle must be an object")
    try:
        encoded = json.dumps(bundle, ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise BundleValidationError("bundle must contain finite JSON values") from exc
    if len(encoded) > MAX_INPUT_BYTES:
        raise BundleValidationError("bundle exceeds size limit")
    _required(bundle, {
        "schema_version", "run_id", "as_of", "expires_at", "mode",
        "mandate_version", "universe", "portfolio", "evidence",
    }, "bundle")
    if bundle["schema_version"] != SCHEMA_VERSION:
        raise BundleValidationError("schema_version must be 1")
    run_id = bundle["run_id"]
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id) or run_id in {".", ".."}:
        raise BundleValidationError("run_id must be a portable identifier without path traversal")
    if bundle["mode"] not in MODES:
        raise BundleValidationError("mode must be shadow or historical_diagnostic")
    if not isinstance(bundle["mandate_version"], str) or not bundle["mandate_version"].strip():
        raise BundleValidationError("mandate_version must be nonempty")
    as_of = parse_time(bundle["as_of"], "as_of")
    expires_at = parse_time(bundle["expires_at"], "expires_at")
    if expires_at <= as_of:
        raise BundleValidationError("expires_at must be later than as_of")
    if now is not None and bundle["mode"] == "shadow":
        clock = parse_time(now, "now") if isinstance(now, str) else now
        if clock is None or clock.tzinfo is None:
            raise BundleValidationError("now must be timezone-aware")
        if as_of > clock:
            raise BundleValidationError("as_of cannot be in the future")

    universe = bundle["universe"]
    if not isinstance(universe, list) or not universe:
        raise BundleValidationError("universe must be a nonempty array")
    symbols: list[str] = []
    for index, item in enumerate(universe):
        if not isinstance(item, dict):
            raise BundleValidationError(f"universe[{index}] must be an object")
        _required(item, {"symbol", "asset_class"}, f"universe[{index}]")
        symbol = item["symbol"]
        if not isinstance(symbol, str) or not symbol or symbol.strip() != symbol:
            raise BundleValidationError(f"universe[{index}].symbol must be an exact nonempty identifier")
        if item["asset_class"] not in ASSET_CLASSES:
            raise BundleValidationError(f"universe[{index}].asset_class is invalid")
        symbols.append(symbol)
    if len(set(symbols)) != len(symbols):
        raise BundleValidationError("universe symbols must be unique exact identifiers")
    symbol_set = set(symbols)

    portfolio = bundle["portfolio"]
    if not isinstance(portfolio, dict):
        raise BundleValidationError("portfolio must be an object")
    _required(portfolio, {
        "snapshot_id", "observed_at", "cash", "equity", "pending_buy_notional", "positions"
    }, "portfolio")
    if not isinstance(portfolio["snapshot_id"], str) or not portfolio["snapshot_id"].strip():
        raise BundleValidationError("portfolio.snapshot_id must be nonempty")
    observed_at = parse_time(portfolio["observed_at"], "portfolio.observed_at")
    if observed_at > as_of:
        raise BundleValidationError("portfolio.observed_at cannot be later than as_of")
    cash = decimal_value(portfolio["cash"], "portfolio.cash")
    equity = decimal_value(portfolio["equity"], "portfolio.equity")
    if equity <= 0:
        raise BundleValidationError("portfolio.equity must be positive")
    decimal_value(portfolio["pending_buy_notional"], "portfolio.pending_buy_notional", nonnegative=True)
    positions = portfolio["positions"]
    if not isinstance(positions, list):
        raise BundleValidationError("portfolio.positions must be an array")
    held: set[str] = set()
    position_total = Decimal("0")
    for index, position in enumerate(positions):
        if not isinstance(position, dict):
            raise BundleValidationError(f"portfolio.positions[{index}] must be an object")
        _required(position, {"symbol", "quantity", "market_value"}, f"portfolio.positions[{index}]")
        symbol = position["symbol"]
        if symbol not in symbol_set:
            raise BundleValidationError(f"held symbol {symbol!r} is not in universe")
        if symbol in held:
            raise BundleValidationError("portfolio position symbols must be unique")
        held.add(symbol)
        decimal_value(position["quantity"], f"position {symbol} quantity", nonnegative=True)
        position_total += decimal_value(
            position["market_value"], f"position {symbol} market_value", nonnegative=True
        )
    if abs(cash + position_total - equity) > MONEY_TOLERANCE:
        raise BundleValidationError("cash plus position values does not reconcile to equity")

    evidence = bundle["evidence"]
    if not isinstance(evidence, list):
        raise BundleValidationError("evidence must be an array")
    evidence_ids: set[str] = set()
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            raise BundleValidationError(f"evidence[{index}] must be an object")
        _required(item, {
            "evidence_id", "kind", "source", "published_at", "available_at",
            "expires_at", "symbols", "content",
        }, f"evidence[{index}]")
        evidence_id = item["evidence_id"]
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise BundleValidationError(f"evidence[{index}].evidence_id must be nonempty")
        if evidence_id in evidence_ids:
            raise BundleValidationError("evidence IDs must be unique")
        evidence_ids.add(evidence_id)
        if item["kind"] not in EVIDENCE_KINDS:
            raise BundleValidationError(f"evidence {evidence_id} kind is invalid")
        if not isinstance(item["source"], str) or not item["source"].strip():
            raise BundleValidationError(f"evidence {evidence_id} source must be nonempty")
        published = parse_time(item["published_at"], f"evidence {evidence_id} published_at")
        available = parse_time(item["available_at"], f"evidence {evidence_id} available_at")
        evidence_expiry = parse_time(item["expires_at"], f"evidence {evidence_id} expires_at")
        if not published <= available <= as_of:
            raise BundleValidationError(f"evidence {evidence_id} available_at violates availability")
        if evidence_expiry <= available:
            raise BundleValidationError(f"evidence {evidence_id} expires_at must follow availability")
        if "event_at" in item:
            parse_time(item["event_at"], f"evidence {evidence_id} event_at")
        item_symbols = item["symbols"]
        if not isinstance(item_symbols, list) or any(symbol not in symbol_set for symbol in item_symbols):
            raise BundleValidationError(f"evidence {evidence_id} symbols must be in universe")
        if len(set(item_symbols)) != len(item_symbols):
            raise BundleValidationError(f"evidence {evidence_id} symbols must be unique")
        if not isinstance(item["content"], str) or len(item["content"]) > MAX_CONTENT_CHARS:
            raise BundleValidationError(f"evidence {evidence_id} content exceeds size limit")
        metrics = item.get("metrics", {})
        if not isinstance(metrics, dict):
            raise BundleValidationError(f"evidence {evidence_id} metrics must be an object")
        for name, value in metrics.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                raise BundleValidationError(f"evidence {evidence_id} metric {name} must be finite numerical")

    for field, default, minimum in (
        ("max_snapshot_age_seconds", 300, 0), ("max_calls", 3, 1)
    ):
        value = bundle.get(field, default)
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            raise BundleValidationError(f"{field} must be an integer >= {minimum}")
    return bundle


def validate_bundle(bundle: dict, *, now: str | datetime | None = None) -> dict:
    """Normalize malformed JSON shapes into the public validation failure type."""
    try:
        return _validate_bundle(bundle, now=now)
    except BundleValidationError:
        raise
    except (TypeError, KeyError, ValueError, InvalidOperation, OverflowError):
        raise BundleValidationError("invalid JSON field type or value") from None
