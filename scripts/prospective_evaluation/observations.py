"""Bounded GET-only market observations and offline reconstruction."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, NoReturn
from zoneinfo import ZoneInfo


_NY = ZoneInfo("America/New_York")
_POLICY = {
    "feed": "iex",
    "adjustment": "raw",
    "asof": "-",
    "currency": "USD",
    "timeframe": "1Day",
}
_HEX_256 = re.compile(r"[0-9a-f]{64}")
_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9./-]{0,31}")
_SESSION_FIELDS = ("date", "open", "close", "session_open", "session_close", "settlement_date")
_BAR_FIELDS = ("t", "o", "h", "l", "c", "v")
_MAX_SYMBOLS = 20
_MAX_PAGES = 16
_MAX_RECORDS = 20_000
_MAX_TOKEN_CHARS = 2_048
_MAX_RATE = (1 << 63) - 1


class ObservationError(ValueError):
    """Stable sanitized failure from capture validation or reconstruction."""


def _fail(code: str) -> NoReturn:
    raise ObservationError(code)


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        _fail("invalid_observation")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except (TypeError, ValueError, OverflowError):
        _fail("invalid_observation")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail("invalid_observation")
    return parsed


def _iso(value: Any) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail("invalid_clock")
    return value.astimezone(timezone.utc).isoformat()


def _strict_date(value: Any) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        _fail("invalid_request")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        _fail("invalid_request")
    return parsed


def _symbols(values: Any) -> list[str]:
    if not isinstance(values, list) or not 1 <= len(values) <= _MAX_SYMBOLS:
        _fail("invalid_request")
    if any(not isinstance(value, str) or not _SYMBOL.fullmatch(value) for value in values):
        _fail("invalid_request")
    if len(set(values)) != len(values):
        _fail("invalid_request")
    return list(values)


def _safe_error(caught: BaseException) -> dict[str, Any]:
    known = {
        "source_http_error", "source_unavailable", "invalid_source_response",
        "credentials_unavailable", "ssh_source_unavailable", "invalid_request",
        "invalid_params", "invalid_endpoint", "missing_credentials",
        "invalid_credentials", "response_too_large", "invalid_clock",
    }
    code = getattr(caught, "code", None)
    if not isinstance(code, str):
        code = ""
    if code == "missing_credentials" or code == "invalid_credentials":
        code = "credentials_unavailable"
    elif code in {"invalid_params", "invalid_endpoint"}:
        code = "invalid_request"
    elif code in {"response_too_large", "invalid_clock"}:
        code = "invalid_source_response"
    if not isinstance(code, str) or code not in known:
        code = "invalid_source_response" if isinstance(caught, ValueError) else "source_unavailable"
    status = getattr(caught, "http_status", None)
    if type(status) is not int or not 100 <= status <= 599:
        status = None
    return {"code": code, "http_status": status}


def _receipt(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict) or set(result) != {
        "payload", "request_started_at", "received_at", "body_sha256", "rate_limit",
    }:
        _fail("invalid_source_response")
    request = _parse_time(result["request_started_at"])
    received = _parse_time(result["received_at"])
    if request > received:
        _fail("invalid_source_response")
    digest = result["body_sha256"]
    limits = result["rate_limit"]
    if not isinstance(digest, str) or not _HEX_256.fullmatch(digest):
        _fail("invalid_source_response")
    if not isinstance(limits, dict) or not set(limits).issubset({"limit", "remaining", "reset"}):
        _fail("invalid_source_response")
    if any(type(value) is not int or not 0 <= value <= _MAX_RATE for value in limits.values()):
        _fail("invalid_source_response")
    return {
        "request_started_at": result["request_started_at"],
        "received_at": result["received_at"],
        "body_sha256": digest,
        "rate_limit": dict(limits),
    }


def _project_sessions(payload: Any) -> list[dict[str, str]]:
    if not isinstance(payload, list) or len(payload) > _MAX_RECORDS:
        _fail("invalid_source_response")
    projected = []
    for item in payload:
        if not isinstance(item, dict) or any(key not in item for key in _SESSION_FIELDS):
            _fail("invalid_source_response")
        if any(not isinstance(item[key], str) for key in _SESSION_FIELDS):
            _fail("invalid_source_response")
        projected.append({key: item[key] for key in _SESSION_FIELDS})
    return projected


def _project_bars(payload: Any, symbols: list[str]) -> tuple[dict[str, Any], str | None, int]:
    if not isinstance(payload, dict) or "bars" not in payload or "next_page_token" not in payload:
        _fail("invalid_source_response")
    source_bars = payload["bars"]
    token = payload["next_page_token"]
    if not isinstance(source_bars, dict) or any(symbol not in symbols for symbol in source_bars):
        _fail("invalid_source_response")
    if token is not None and (not isinstance(token, str) or not token or len(token) > _MAX_TOKEN_CHARS):
        _fail("invalid_source_response")
    count = 0
    bars: dict[str, list[dict[str, Any]]] = {}
    for symbol, records in source_bars.items():
        if not isinstance(symbol, str) or not isinstance(records, list):
            _fail("invalid_source_response")
        count += len(records)
        if count > _MAX_RECORDS:
            _fail("invalid_source_response")
        projected = []
        for record in records:
            if not isinstance(record, dict) or any(key not in record for key in _BAR_FIELDS):
                _fail("invalid_source_response")
            if not isinstance(record["t"], str):
                _fail("invalid_source_response")
            if any(not isinstance(record[key], (str, int)) or isinstance(record[key], bool) for key in _BAR_FIELDS[1:]):
                _fail("invalid_source_response")
            projected.append({key: record[key] for key in _BAR_FIELDS})
        bars[symbol] = projected
    return {"bars": bars, "next_page_token": token}, token, count


def observe_market(
    symbols: list[str],
    start_date: str,
    end_date: str,
    alpaca: Any,
    *,
    clock: Any = None,
    max_pages: int = _MAX_PAGES,
) -> dict[str, Any]:
    """Capture calendar and bars through an injected fixed-host GET transport."""
    safe_symbols = _symbols(symbols)
    first, last = _strict_date(start_date), _strict_date(end_date)
    if first > last or (last - first).days > 370:
        _fail("invalid_request")
    if type(max_pages) is not int or not 1 <= max_pages <= _MAX_PAGES:
        _fail("invalid_request")
    if clock is not None and not callable(clock):
        _fail("invalid_request")
    now = clock or (lambda: datetime.now(timezone.utc))
    started = _iso(now())
    start_midnight = datetime.combine(first, time.min, _NY).isoformat()
    queries = {
        "sessions": {"start": start_date, "end": end_date, "date_type": "TRADING"},
        "bars": {
            "symbols": ",".join(safe_symbols), "timeframe": "1Day", "start": start_midnight,
            "end": started, **_POLICY, "sort": "asc", "limit": 10000,
        },
    }
    sources: dict[str, Any] = {}
    session_source = {"pages": [], "complete": False, "error": None}
    try:
        result = alpaca.fetch("sessions", dict(queries["sessions"]))
        projected = _project_sessions(result.get("payload") if isinstance(result, dict) else None)
        session_source["pages"].append({
            "query": dict(queries["sessions"]), "ordinal": 1, "payload": projected, **_receipt(result),
        })
        session_source["complete"] = True
    except BaseException as caught:
        if isinstance(caught, (KeyboardInterrupt, SystemExit, GeneratorExit)):
            raise
        session_source["error"] = _safe_error(caught)
    sources["sessions"] = session_source

    bar_source = {"pages": [], "complete": False, "error": None}
    query = dict(queries["bars"])
    seen_tokens: set[str] = set()
    total = 0
    for ordinal in range(1, max_pages + 1):
        try:
            result = alpaca.fetch("bars", dict(query))
            payload, token, count = _project_bars(result.get("payload") if isinstance(result, dict) else None, safe_symbols)
            total += count
            if total > _MAX_RECORDS:
                _fail("invalid_source_response")
            bar_source["pages"].append({
                "query": dict(query), "ordinal": ordinal, "payload": payload, **_receipt(result),
            })
        except BaseException as caught:
            if isinstance(caught, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                raise
            bar_source["error"] = _safe_error(caught)
            break
        if token is None:
            bar_source["complete"] = True
            break
        if token in seen_tokens:
            bar_source["error"] = {"code": "pagination_cycle", "http_status": None}
            break
        seen_tokens.add(token)
        query = {**queries["bars"], "page_token": token}
    if not bar_source["complete"] and bar_source["error"] is None:
        bar_source["error"] = {"code": "pagination_limit", "http_status": None}
    sources["bars"] = bar_source
    return {
        "schema_version": 1,
        "symbols": safe_symbols,
        "started_at": started,
        "completed_at": _iso(now()),
        "start_date": start_date,
        "end_date": end_date,
        "policy": dict(_POLICY),
        "sources": sources,
    }


def _session_close(day: str, value: Any) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}", value):
        _fail("invalid_observation")
    hour, minute = int(value[:2]), int(value[2:])
    if hour > 23 or minute > 59:
        _fail("invalid_observation")
    return datetime.combine(date.fromisoformat(day), time(hour, minute), _NY)


def _number(value: Any, *, positive: bool) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        _fail("invalid_observation")
    if not number.is_finite() or (number <= 0 if positive else number < 0):
        _fail("invalid_observation")
    return str(value)


def reconstruct_observation(observation: Any) -> dict[str, Any]:
    """Validate a raw observation and reconstruct normalized completed bars offline."""
    try:
        if not isinstance(observation, dict) or set(observation) != {
            "schema_version", "symbols", "started_at", "completed_at", "start_date", "end_date", "policy", "sources",
        }:
            _fail("invalid_observation")
        if type(observation["schema_version"]) is not int or observation["schema_version"] != 1:
            _fail("invalid_observation")
        symbols = _symbols(observation["symbols"])
        first, last = _strict_date(observation["start_date"]), _strict_date(observation["end_date"])
        if first > last or (last - first).days > 370 or observation["policy"] != _POLICY:
            _fail("invalid_observation")
        started, completed = _parse_time(observation["started_at"]), _parse_time(observation["completed_at"])
        if started > completed or not isinstance(observation["sources"], dict) or set(observation["sources"]) != {"sessions", "bars"}:
            _fail("invalid_observation")
        expected = {
            "sessions": {"start": observation["start_date"], "end": observation["end_date"], "date_type": "TRADING"},
            "bars": {
                "symbols": ",".join(symbols), "timeframe": "1Day",
                "start": datetime.combine(first, time.min, _NY).isoformat(),
                "end": observation["started_at"], **_POLICY, "sort": "asc", "limit": 10000,
            },
        }
        pages_by_source: dict[str, list[dict[str, Any]]] = {}
        prior_received = started
        for name in ("sessions", "bars"):
            source = observation["sources"][name]
            if not isinstance(source, dict) or set(source) != {"pages", "complete", "error"}:
                _fail("invalid_observation")
            if source["complete"] is not True or source["error"] is not None:
                _fail("capture_failed")
            pages = source["pages"]
            if not isinstance(pages, list) or not pages or name == "sessions" and len(pages) != 1:
                _fail("invalid_observation")
            previous_token = None
            seen_tokens: set[str] = set()
            for index, page in enumerate(pages):
                if not isinstance(page, dict) or set(page) != {
                    "query", "ordinal", "payload", "request_started_at", "received_at", "body_sha256", "rate_limit",
                }:
                    _fail("invalid_observation")
                safe = _receipt({key: page[key] for key in page if key != "query" and key != "ordinal"})
                request, received = _parse_time(safe["request_started_at"]), _parse_time(safe["received_at"])
                if not prior_received <= request <= received <= completed or type(page["ordinal"]) is not int or page["ordinal"] != index + 1:
                    _fail("invalid_observation")
                prior_received = received
                query = dict(expected[name])
                if index:
                    query["page_token"] = previous_token
                if page["query"] != query:
                    _fail("invalid_observation")
                if name == "bars":
                    _, token, _ = _project_bars(page["payload"], symbols)
                    if token is not None and token in seen_tokens:
                        _fail("invalid_observation")
                    if token is not None:
                        seen_tokens.add(token)
                    previous_token = token
            if name == "bars" and previous_token is not None:
                _fail("invalid_observation")
            pages_by_source[name] = pages

        sessions: dict[str, dict[str, str]] = {}
        for item in _project_sessions(pages_by_source["sessions"][0]["payload"]):
            day = item["date"]
            if _strict_date(day).isoformat() != day or not first <= date.fromisoformat(day) <= last or day in sessions:
                _fail("invalid_observation")
            interval = datetime.combine(date.fromisoformat(day), time.min, _NY)
            close = _session_close(day, item["session_close"])
            sessions[day] = {
                "date": day,
                "interval_start": interval.astimezone(timezone.utc).isoformat(),
                "session_close_at": close.astimezone(timezone.utc).isoformat(),
            }

        bars: dict[str, dict[str, dict[str, Any]]] = {symbol: {} for symbol in symbols}
        calendar_received = _parse_time(pages_by_source["sessions"][0]["received_at"])
        for page in pages_by_source["bars"]:
            request = _parse_time(page["request_started_at"])
            received = _parse_time(page["received_at"])
            projected, _, _ = _project_bars(page["payload"], symbols)
            for symbol, records in projected["bars"].items():
                for item in records:
                    interval = _parse_time(item["t"])
                    local = interval.astimezone(_NY)
                    day = local.date().isoformat()
                    if day not in sessions or local.time() != time.min or interval > request or interval > completed:
                        _fail("invalid_observation")
                    close_at = _parse_time(sessions[day]["session_close_at"])
                    if request <= close_at:
                        continue
                    open_value = _number(item["o"], positive=True)
                    high_value = _number(item["h"], positive=True)
                    low_value = _number(item["l"], positive=True)
                    close_value = _number(item["c"], positive=True)
                    _number(item["v"], positive=False)
                    low, high = Decimal(low_value), Decimal(high_value)
                    if not low <= Decimal(open_value) <= high or not low <= Decimal(close_value) <= high:
                        _fail("invalid_observation")
                    value = {
                        "session_date": day,
                        "interval_start": interval.astimezone(timezone.utc).isoformat(),
                        "session_close_at": sessions[day]["session_close_at"],
                        "open": open_value,
                        "close": close_value,
                        "available_at": max(calendar_received, received).astimezone(timezone.utc).isoformat(),
                        "source_published_at": None,
                    }
                    existing = bars[symbol].get(day)
                    if existing is not None:
                        comparable = {key: item for key, item in value.items() if key != "available_at"}
                        prior = {key: item for key, item in existing.items() if key != "available_at"}
                        if prior != comparable:
                            _fail("invalid_observation")
                        existing["available_at"] = max(existing["available_at"], value["available_at"])
                    else:
                        bars[symbol][day] = value
        return {
            "sessions": [sessions[day] for day in sorted(sessions)],
            "bars": {symbol: [bars[symbol][day] for day in sorted(bars[symbol])] for symbol in symbols},
            "completed_at": observation["completed_at"],
        }
    except ObservationError:
        raise
