"""Bounded acquisition and projection for prospective evidence sources."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


_MAX_RECORDS = 20_000
_MAX_PAGE_TOKEN_CHARS = 2_048
_MAX_RATE_LIMIT = (1 << 63) - 1
_HEX_256 = re.compile(r"[0-9a-f]{64}")
_ALLOWED_ERROR_CODES = frozenset(
    {
        "source_http_error",
        "source_unavailable",
        "invalid_source_response",
        "credentials_unavailable",
        "ssh_source_unavailable",
        "invalid_request",
        "pagination_limit",
        "pagination_cycle",
    }
)
_RECEIPT_KEYS = (
    "request_started_at",
    "received_at",
    "body_sha256",
    "rate_limit",
)
_SESSION_KEYS = (
    "date",
    "open",
    "close",
    "session_open",
    "session_close",
    "settlement_date",
)
_BAR_KEYS = ("t", "o", "h", "l", "c", "v")
_NEWS_KEYS = (
    "id",
    "headline",
    "created_at",
    "updated_at",
    "url",
    "symbols",
    "source",
)


class _SourceFailure(ValueError):
    def __init__(self, code, http_status=None):
        self.code = code if code in _ALLOWED_ERROR_CODES else "source_unavailable"
        self.http_status = _valid_status(http_status)
        super().__init__(self.code)


def _valid_status(value):
    if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
        return value
    return None


def _iso(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("invalid clock")
    return value.astimezone(timezone.utc).isoformat()


def _valid_timestamp(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _validate_symbols(symbols):
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= 20:
        raise ValueError("invalid symbols")
    for symbol in symbols:
        if (
            not isinstance(symbol, str)
            or not symbol
            or "," in symbol
            or any(character.isspace() for character in symbol)
        ):
            raise ValueError("invalid symbols")
    if len(set(symbols)) != len(symbols):
        raise ValueError("invalid symbols")


def _receipt(result):
    if not isinstance(result, dict) or "payload" not in result:
        raise _SourceFailure("invalid_source_response")
    if not _valid_timestamp(result.get("request_started_at")) or not _valid_timestamp(
        result.get("received_at")
    ):
        raise _SourceFailure("invalid_source_response")
    digest = result.get("body_sha256")
    if not isinstance(digest, str) or not _HEX_256.fullmatch(digest):
        raise _SourceFailure("invalid_source_response")
    limits = result.get("rate_limit")
    if not isinstance(limits, dict) or not set(limits).issubset({"limit", "remaining", "reset"}):
        raise _SourceFailure("invalid_source_response")
    if any(
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= _MAX_RATE_LIMIT
        for value in limits.values()
    ):
        raise _SourceFailure("invalid_source_response")
    return {key: result[key] for key in _RECEIPT_KEYS}


def _project_sessions(payload, _symbols):
    if not isinstance(payload, list) or len(payload) > _MAX_RECORDS:
        raise _SourceFailure("invalid_source_response")
    projected = []
    for item in payload:
        if (
            not isinstance(item, dict)
            or any(key not in item for key in _SESSION_KEYS)
            or any(not isinstance(item[key], str) for key in _SESSION_KEYS)
        ):
            raise _SourceFailure("invalid_source_response")
        projected.append({key: item[key] for key in _SESSION_KEYS})
    return projected, None


def _page_token(payload):
    if "next_page_token" not in payload:
        raise _SourceFailure("invalid_source_response")
    token = payload["next_page_token"]
    if token is not None and (
        not isinstance(token, str) or not token or len(token) > _MAX_PAGE_TOKEN_CHARS
    ):
        raise _SourceFailure("invalid_source_response")
    return token


def _project_bars(payload, symbols):
    if not isinstance(payload, dict) or "bars" not in payload:
        raise _SourceFailure("invalid_source_response")
    bars = payload["bars"]
    if not isinstance(bars, dict) or any(symbol not in symbols for symbol in bars):
        raise _SourceFailure("invalid_source_response")
    count = 0
    projected = {}
    for symbol, records in bars.items():
        if not isinstance(symbol, str) or not isinstance(records, list):
            raise _SourceFailure("invalid_source_response")
        count += len(records)
        if count > _MAX_RECORDS:
            raise _SourceFailure("invalid_source_response")
        projected_records = []
        for record in records:
            if (
                not isinstance(record, dict)
                or any(key not in record for key in _BAR_KEYS)
                or not isinstance(record["t"], str)
                or any(
                    not isinstance(record[key], (str, int))
                    or isinstance(record[key], bool)
                    for key in ("o", "h", "l", "c", "v")
                )
            ):
                raise _SourceFailure("invalid_source_response")
            projected_records.append({key: record[key] for key in _BAR_KEYS})
        projected[symbol] = projected_records
    token = _page_token(payload)
    return {"bars": projected, "next_page_token": token}, token


def _project_news(payload, _symbols):
    if not isinstance(payload, dict) or "news" not in payload:
        raise _SourceFailure("invalid_source_response")
    news = payload["news"]
    if not isinstance(news, list) or len(news) > _MAX_RECORDS:
        raise _SourceFailure("invalid_source_response")
    projected = []
    for item in news:
        required = tuple(key for key in _NEWS_KEYS if key != "url")
        if (
            not isinstance(item, dict)
            or any(key not in item for key in required)
            or not isinstance(item["id"], int)
            or isinstance(item["id"], bool)
            or any(
                not isinstance(item[key], str)
                for key in ("headline", "created_at", "updated_at", "source")
            )
            or item.get("url") is not None
            and not isinstance(item.get("url"), str)
            or not isinstance(item["symbols"], list)
            or any(not isinstance(symbol, str) for symbol in item["symbols"])
        ):
            raise _SourceFailure("invalid_source_response")
        projected.append({key: item.get(key) for key in _NEWS_KEYS})
    token = _page_token(payload)
    return {"news": projected, "next_page_token": token}, token


def _project_calendar(payload, _symbols):
    if not isinstance(payload, str):
        raise _SourceFailure("invalid_source_response")
    return payload, None


def _safe_failure(caught):
    code = getattr(caught, "code", None)
    if not isinstance(code, str):
        code = ""
    code = {
        "missing_credentials": "credentials_unavailable",
        "invalid_credentials": "credentials_unavailable",
        "invalid_params": "invalid_request",
        "invalid_endpoint": "invalid_request",
        "response_too_large": "invalid_source_response",
        "invalid_clock": "invalid_source_response",
    }.get(code, code)
    if code not in _ALLOWED_ERROR_CODES:
        if isinstance(caught, ValueError):
            code = "invalid_source_response"
        else:
            code = "source_unavailable"
    return {"code": code, "http_status": _valid_status(getattr(caught, "http_status", None))}


def _collect_source(transport, endpoint, base_query, projector, symbols, max_pages, paginated):
    source = {"pages": [], "complete": False, "error": None}
    query = dict(base_query)
    seen = set()
    for ordinal in range(1, max_pages + 1):
        try:
            result = transport.fetch(endpoint, dict(query))
            safe_receipt = _receipt(result)
            payload, next_token = projector(result["payload"], symbols)
        except (ValueError, OSError, TimeoutError) as caught:
            source["error"] = _safe_failure(caught)
            return source
        source["pages"].append(
            {"query": dict(query), "ordinal": ordinal, "payload": payload, **safe_receipt}
        )
        if not paginated or next_token is None:
            source["complete"] = True
            return source
        if next_token in seen:
            source["error"] = {"code": "pagination_cycle", "http_status": None}
            return source
        seen.add(next_token)
        query = {**base_query, "page_token": next_token}
    source["error"] = {"code": "pagination_limit", "http_status": None}
    return source


def collect_sources(symbols, alpaca, public, *, clock=None, max_pages=16):
    """Collect bounded source pages through injected duck-typed GET transports."""
    _validate_symbols(symbols)
    if not isinstance(max_pages, int) or isinstance(max_pages, bool) or not 1 <= max_pages <= 16:
        raise ValueError("invalid max_pages")
    if clock is not None and not callable(clock):
        raise ValueError("invalid clock")
    now = clock or (lambda: datetime.now(timezone.utc))
    started = now()
    started_iso = _iso(started)
    started_utc = started.astimezone(timezone.utc)
    ny_date = started_utc.astimezone(ZoneInfo("America/New_York")).date()
    window_date = ny_date - timedelta(days=120)
    joined = ",".join(symbols)
    base_queries = {
        "sessions": {
            "start": window_date.isoformat(),
            "end": ny_date.isoformat(),
            "date_type": "TRADING",
        },
        "bars": {
            "symbols": joined,
            "timeframe": "1Day",
            "start": window_date.isoformat(),
            "end": started_iso,
            "feed": "iex",
            "adjustment": "raw",
            "asof": "-",
            "currency": "USD",
            "sort": "asc",
            "limit": 10000,
        },
        "news": {
            "symbols": joined,
            "start": (started_utc - timedelta(days=3)).isoformat(),
            "end": started_iso,
            "sort": "asc",
            "limit": 50,
            "include_content": "false",
            "exclude_contentless": "false",
        },
    }
    sources = {
        "sessions": _collect_source(
            alpaca, "sessions", base_queries["sessions"], _project_sessions, symbols, 1, False
        ),
        "bars": _collect_source(
            alpaca, "bars", base_queries["bars"], _project_bars, symbols, max_pages, True
        ),
        "news": _collect_source(
            alpaca, "news", base_queries["news"], _project_news, symbols, max_pages, True
        ),
        "bea_calendar": _collect_source(
            public, "bea_calendar", {}, _project_calendar, symbols, 1, False
        ),
    }
    return {
        "schema_version": 1,
        "started_at": started_iso,
        "completed_at": _iso(now()),
        "symbols": list(symbols),
        "policy": {
            "feed": "iex",
            "adjustment": "raw",
            "asof": "-",
            "currency": "USD",
            "bar_completion": "extended_session_close",
            "news_metadata_only": True,
        },
        "sources": sources,
    }
