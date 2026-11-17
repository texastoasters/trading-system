"""Integrate complete source captures into reproducible decision-lab collections."""
from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, NoReturn
from zoneinfo import ZoneInfo

from decision_lab.validation import BundleValidationError, RUN_ID_RE, parse_time, validate_bundle

from . import archive
from .book import BookError, prepare_book
from .events import EventError, parse_bea_calendar
from .normalize import NormalizationError, normalize_market
from .signals import SignalError, derive_bar_evidence


RECIPE_VERSION = "prospective-evidence-v1"
CALENDAR_HORIZON_DAYS = 30
_POLICY = {
    "feed": "iex",
    "adjustment": "raw",
    "asof": "-",
    "currency": "USD",
    "bar_completion": "extended_session_close",
    "news_metadata_only": True,
}
_CAPTURE_KEYS = {"schema_version", "started_at", "completed_at", "symbols", "policy", "sources"}
_SOURCE_KEYS = {"pages", "complete", "error"}
_PAGE_KEYS = {
    "query", "ordinal", "payload", "request_started_at", "received_at",
    "body_sha256", "rate_limit",
}
_HEX_256 = re.compile(r"[0-9a-f]{64}")
_MAX_PREVIOUS_EVENTS = 2_000
_MAX_EVENT_TEXT_CHARS = 20_000


class PipelineError(ValueError):
    """A stable public failure that does not expose source or parser details."""


def _fail(code: str) -> NoReturn:
    raise PipelineError(code)


def _portable(value: Any) -> bool:
    return isinstance(value, str) and bool(RUN_ID_RE.fullmatch(value)) and value not in {".", ".."}


def _time(value: Any) -> datetime:
    return parse_time(value, "capture time")


def _validate_receipt(page: dict[str, Any], started: datetime, completed: datetime) -> None:
    if set(page) != _PAGE_KEYS or not isinstance(page["query"], dict):
        _fail("capture_incomplete")
    request, received = _time(page["request_started_at"]), _time(page["received_at"])
    limits = page["rate_limit"]
    if not started <= request <= received <= completed:
        _fail("capture_incomplete")
    if not isinstance(page["body_sha256"], str) or not _HEX_256.fullmatch(page["body_sha256"]):
        _fail("capture_incomplete")
    if not isinstance(limits, dict) or not set(limits).issubset({"limit", "remaining", "reset"}):
        _fail("capture_incomplete")
    if any(type(value) is not int or value < 0 for value in limits.values()):
        _fail("capture_incomplete")


def _expected_queries(capture: dict[str, Any], started: datetime) -> dict[str, dict[str, Any]]:
    utc = started.astimezone(timezone.utc)
    ny_day = utc.astimezone(ZoneInfo("America/New_York")).date()
    window = ny_day - timedelta(days=120)
    joined = ",".join(capture["symbols"])
    return {
        "sessions": {"start": window.isoformat(), "end": ny_day.isoformat(), "date_type": "TRADING"},
        "bars": {
            "symbols": joined, "timeframe": "1Day", "start": window.isoformat(),
            "end": capture["started_at"], "feed": "iex", "adjustment": "raw", "asof": "-",
            "currency": "USD", "sort": "asc", "limit": 10000,
        },
        "news": {
            "symbols": joined, "start": (utc - timedelta(days=3)).isoformat(),
            "end": capture["started_at"], "sort": "asc", "limit": 50,
            "include_content": "false", "exclude_contentless": "false",
        },
        "bea_calendar": {},
    }


def _validate_capture(capture: Any) -> None:
    try:
        if (not isinstance(capture, dict) or set(capture) != _CAPTURE_KEYS
                or type(capture["schema_version"]) is not int or capture["schema_version"] != 1):
            _fail("capture_incomplete")
        symbols = capture["symbols"]
        if (not isinstance(symbols, list) or not 1 <= len(symbols) <= 20
                or any(not isinstance(symbol, str) or not symbol or "," in symbol
                       or any(char.isspace() for char in symbol) for symbol in symbols)
                or len(set(symbols)) != len(symbols)):
            _fail("capture_incomplete")
        if capture["policy"] != _POLICY or set(capture["sources"]) != {"sessions", "bars", "news", "bea_calendar"}:
            _fail("capture_incomplete")
        started, completed = _time(capture["started_at"]), _time(capture["completed_at"])
        if started > completed:
            _fail("capture_incomplete")
        expected = _expected_queries(capture, started)
        for name in ("sessions", "bars", "news", "bea_calendar"):
            source = capture["sources"][name]
            if (not isinstance(source, dict) or set(source) != _SOURCE_KEYS
                    or source["complete"] is not True or source["error"] is not None
                    or not isinstance(source["pages"], list) or not source["pages"]):
                _fail("capture_incomplete")
            pages = source["pages"]
            if name in {"sessions", "bea_calendar"} and len(pages) != 1:
                _fail("capture_incomplete")
            for index, page in enumerate(pages):
                if not isinstance(page, dict):
                    _fail("capture_incomplete")
                _validate_receipt(page, started, completed)
                if type(page["ordinal"]) is not int or page["ordinal"] != index + 1:
                    _fail("capture_incomplete")
                query = dict(expected[name])
                if index:
                    previous = pages[index - 1]["payload"]
                    if not isinstance(previous, dict) or previous.get("next_page_token") is None:
                        _fail("capture_incomplete")
                    query["page_token"] = previous["next_page_token"]
                if page["query"] != query:
                    _fail("capture_incomplete")
            if name in {"bars", "news"}:
                seen_tokens: set[str] = set()
                for page in pages:
                    payload = page["payload"]
                    if not isinstance(payload, dict) or "next_page_token" not in payload:
                        _fail("capture_incomplete")
                    token = payload["next_page_token"]
                    if token is not None and (not isinstance(token, str) or not token or token in seen_tokens):
                        _fail("capture_incomplete")
                    if token is not None:
                        seen_tokens.add(token)
                if pages[-1]["payload"]["next_page_token"] is not None:
                    _fail("capture_incomplete")
    except PipelineError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError):
        _fail("capture_incomplete")


def _decimal_cash(value: Any) -> bool:
    try:
        number = Decimal(value) if isinstance(value, str) and value and value.strip() == value else None
    except InvalidOperation:
        return False
    return number is not None and number.is_finite() and number > 0


def _project_position(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"symbol", "quantity", "market_value"}:
        _fail("invalid_experiment")
    return {key: copy.deepcopy(value[key]) for key in ("symbol", "quantity", "market_value")}


def _project_portfolio(value: Any) -> dict[str, Any]:
    keys = {"snapshot_id", "observed_at", "cash", "equity", "pending_buy_notional", "positions"}
    if not isinstance(value, dict) or set(value) != keys or not isinstance(value["positions"], list):
        _fail("invalid_experiment")
    result = {key: copy.deepcopy(value[key]) for key in keys - {"positions"}}
    result["positions"] = [_project_position(item) for item in value["positions"]]
    return result


def _project_previous(value: Any) -> list[dict[str, Any]]:
    fields = {"uid", "summary", "event_at", "dtstamp", "sequence", "duration"}
    if not isinstance(value, list) or len(value) > _MAX_PREVIOUS_EVENTS:
        _fail("invalid_experiment")
    result = []
    for item in value:
        if not isinstance(item, dict) or set(item) != fields:
            _fail("invalid_experiment")
        if any(
            not isinstance(item[field], str)
            or not item[field]
            or len(item[field]) > _MAX_EVENT_TEXT_CHARS
            for field in ("uid", "summary", "event_at")
        ):
            _fail("invalid_experiment")
        if item["dtstamp"] is not None and (
            not isinstance(item["dtstamp"], str)
            or not item["dtstamp"]
            or len(item["dtstamp"]) > _MAX_EVENT_TEXT_CHARS
        ):
            _fail("invalid_experiment")
        if item["sequence"] is not None and type(item["sequence"]) is not int:
            _fail("invalid_experiment")
        if item["duration"] is not None and (
            not isinstance(item["duration"], str)
            or not item["duration"]
            or len(item["duration"]) > _MAX_EVENT_TEXT_CHARS
        ):
            _fail("invalid_experiment")
        result.append({key: copy.deepcopy(item[key]) for key in fields})
    return result


def prepare_experiment(value: Any, *, symbols: Any = None) -> dict[str, Any]:
    """Validate and sanitize an explicit experiment before source acquisition."""
    if not isinstance(value, dict):
        _fail("invalid_experiment")
    allowed = {"collection_id", "book_id", "universe", "research_cash", "portfolio", "ttl_seconds", "previous_events"}
    if not set(value).issubset(allowed) or not {"collection_id", "book_id", "universe"}.issubset(value):
        _fail("invalid_experiment")
    if not _portable(value["collection_id"]) or not _portable(value["book_id"]):
        _fail("invalid_experiment")
    universe = value["universe"]
    if not isinstance(universe, list) or not 1 <= len(universe) <= 20:
        _fail("invalid_experiment")
    projected_universe = []
    for item in universe:
        if (not isinstance(item, dict) or set(item) != {"symbol", "asset_class"}
                or not isinstance(item["symbol"], str) or not item["symbol"]
                or "," in item["symbol"] or any(char.isspace() for char in item["symbol"])
                or not isinstance(item["asset_class"], str)
                or item["asset_class"] not in {"equity", "etf"}):
            _fail("invalid_experiment")
        projected_universe.append({"symbol": item["symbol"], "asset_class": item["asset_class"]})
    declared_symbols = [item["symbol"] for item in projected_universe]
    if len(set(declared_symbols)) != len(declared_symbols):
        _fail("invalid_experiment")
    if symbols is not None and (not isinstance(symbols, list) or declared_symbols != symbols):
        _fail("invalid_experiment")
    cash_present, portfolio_present = "research_cash" in value, "portfolio" in value
    if cash_present == portfolio_present or cash_present and not _decimal_cash(value["research_cash"]):
        _fail("invalid_experiment")
    ttl = value.get("ttl_seconds", 900)
    if type(ttl) is not int or not 1 <= ttl <= 3600:
        _fail("invalid_experiment")
    result = {
        "collection_id": value["collection_id"], "book_id": value["book_id"],
        "universe": projected_universe, "ttl_seconds": ttl,
    }
    if cash_present:
        result["research_cash"] = value["research_cash"]
    else:
        portfolio = _project_portfolio(value["portfolio"])
        try:
            observed = parse_time(portfolio["observed_at"], "portfolio observed_at")
            prepare_book(
                projected_universe,
                as_of=observed.isoformat(),
                expires_at=(observed + timedelta(seconds=3600)).isoformat(),
                book_id=value["book_id"],
                portfolio=portfolio,
            )
        except (BookError, BundleValidationError, KeyError, TypeError, ValueError, OverflowError):
            _fail("invalid_experiment")
        result["portfolio"] = portfolio
    if "previous_events" in value:
        result["previous_events"] = _project_previous(value["previous_events"])
    return result


def _assemble_collection(capture: dict[str, Any], experiment: dict[str, Any]) -> dict[str, Any]:
    as_of = capture["completed_at"]
    expiry = (_time(as_of) + timedelta(seconds=experiment["ttl_seconds"])).isoformat()
    try:
        market = normalize_market(capture, expires_at=expiry)
    except NormalizationError:
        _fail("market_normalization_failed")
    calendar_page = capture["sources"]["bea_calendar"]["pages"][0]
    try:
        calendar = parse_bea_calendar(
            calendar_page["payload"], retrieved_at=calendar_page["received_at"], expires_at=expiry,
            symbols=capture["symbols"], previous=experiment.get("previous_events"),
        )
    except EventError:
        _fail("calendar_normalization_failed")
    calendar_horizon = _time(as_of) + timedelta(days=CALENDAR_HORIZON_DAYS)
    horizon_evidence = [
        item for item in calendar["evidence"]
        if _time(as_of) < _time(item["event_at"]) <= calendar_horizon
    ]
    try:
        book = prepare_book(
            experiment["universe"], as_of=as_of, expires_at=expiry, book_id=experiment["book_id"],
            research_cash=experiment.get("research_cash"), portfolio=experiment.get("portfolio"),
        )
        evidence = [book["evidence"], *market["news"]]
        for item in experiment["universe"]:
            evidence.extend(derive_bar_evidence(
                item["symbol"], **market["bar_inputs"][item["symbol"]], expires_at=expiry,
            ))
        evidence.extend(horizon_evidence)
        bundle = {
            "schema_version": 1, "run_id": experiment["collection_id"], "mode": "shadow",
            "as_of": as_of, "expires_at": expiry, "mandate_version": RECIPE_VERSION,
            "universe": copy.deepcopy(experiment["universe"]), "portfolio": book["portfolio"],
            "evidence": evidence, "max_calls": 3, "max_snapshot_age_seconds": 300,
        }
        validate_bundle(bundle, now=as_of)
        observed = parse_time(bundle["portfolio"]["observed_at"], "portfolio observed_at")
        if (_time(as_of) - observed).total_seconds() > 300:
            _fail("invalid_experiment")
    except PipelineError:
        raise
    except (BookError, SignalError, BundleValidationError):
        _fail("invalid_experiment")
    news_count = len(market["news"])
    news_coverage = {"status": "observed_records", "record_count": news_count}
    if not news_count:
        news_coverage.update({
            "status": "observed_empty",
            "caveat": "No matching news metadata was observed in the bounded query; this does not prove no relevant news exists.",
        })
    envelope = {
        "schema_version": 1,
        "recipe_version": RECIPE_VERSION,
        "source_capture": copy.deepcopy(capture),
        "experiment": copy.deepcopy(experiment),
        "derived": {
            "coverage": {
                "news": news_coverage,
                "calendar": {
                    "policy": "include events strictly after collection completion through the fixed 30-calendar-day horizon; future_event_count is measured at calendar receipt",
                    "horizon_days": CALENDAR_HORIZON_DAYS,
                    "horizon_at": calendar_horizon.isoformat(),
                    "normalized_snapshot_event_count": len(calendar["events"]),
                    "future_event_count": len(calendar["evidence"]),
                    "included_evidence_count": len(horizon_evidence),
                    "excluded_beyond_horizon_count": sum(_time(item["event_at"]) > calendar_horizon for item in calendar["evidence"]),
                    "excluded_elapsed_during_collection_count": sum(_time(item["event_at"]) <= _time(as_of) for item in calendar["evidence"]),
                },
                "bar_sessions_by_symbol": {
                    symbol: len(value["bars"]) for symbol, value in market["bar_inputs"].items()
                },
            },
            "excluded_partial_bars": market["excluded_partial_bars"],
            "normalized_calendar_events": calendar["events"],
            "calendar_changes": calendar["changes"],
            "integrity_caveat": "Source body hashes are saved receipt provenance only; projected payloads cannot reconstruct original HTTP bytes or verify this hash. Archive hashes check stored artifacts, not source authenticity.",
        },
    }
    return {"bundle": bundle, "capture": envelope}


def build_collection(capture: Any, experiment: Any) -> dict[str, Any]:
    """Build a valid shadow-lab bundle and durable offline reconstruction envelope."""
    _validate_capture(capture)
    declared = prepare_experiment(experiment, symbols=capture["symbols"])
    return _assemble_collection(capture, declared)


def reconstruct_collection(path: Any) -> dict[str, Any]:
    """Verify archive integrity and reproduce its exact bundle without networking."""
    try:
        loaded = archive.load_collection(path)
    except Exception:
        _fail("archive_verification_failed")
    if loaded["status"] == "failed":
        return loaded
    envelope = loaded["capture"]
    try:
        if (not isinstance(envelope, dict)
                or set(envelope) != {"schema_version", "recipe_version", "source_capture", "experiment", "derived"}
                or type(envelope["schema_version"]) is not int
                or envelope["schema_version"] != 1 or envelope["recipe_version"] != RECIPE_VERSION):
            _fail("archive_verification_failed")
        rebuilt = build_collection(envelope["source_capture"], envelope["experiment"])
    except PipelineError as caught:
        if str(caught) == "archive_verification_failed":
            raise
        _fail("semantic_reconstruction_failed")
    if rebuilt["bundle"] != loaded["bundle"] or rebuilt["capture"] != envelope:
        _fail("semantic_reconstruction_mismatch")
    return {
        "status": loaded["status"], "bundle": rebuilt["bundle"],
        "capture": envelope, "manifest": loaded["manifest"],
        "verification_kind": "semantic_reconstruction",
    }
