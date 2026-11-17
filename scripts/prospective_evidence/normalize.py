"""Normalize observed metadata; never infer historic data availability."""
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from decision_lab.storage import hash_value
from decision_lab.validation import parse_time


NY = ZoneInfo("America/New_York")
POLICY = {"feed": "iex", "adjustment": "raw", "asof": "-", "currency": "USD", "timeframe": "1Day"}


class NormalizationError(ValueError):
    pass


def _require(condition):
    if not condition:
        raise NormalizationError("invalid_market_capture")


def _text(value, limit=2000):
    _require(isinstance(value, str) and bool(value.strip()) and len(value) <= limit)
    return value


def _number(value, *, positive=False):
    _require(isinstance(value, (str, int)) and not isinstance(value, bool))
    number = Decimal(str(value))
    _require(number.is_finite() and (number > 0 if positive else number >= 0))
    return str(value)


def _pages(capture, name, started, completed):
    source = capture["sources"][name]
    _require(isinstance(source, dict) and source["complete"] is True and source["error"] is None)
    pages = source["pages"]
    _require(isinstance(pages, list) and bool(pages))
    for page in pages:
        _require(isinstance(page, dict) and isinstance(page["query"], dict))
        request = parse_time(page["request_started_at"], "request")
        received = parse_time(page["received_at"], "receipt")
        _require(started <= request <= received <= completed)
    return pages


def _news(pages, symbols, expiry):
    records = {}
    for page in pages:
        _require(isinstance(page["payload"], dict))
        items = page["payload"]["news"]
        _require(isinstance(items, list))
        for item in items:
            _require(isinstance(item, dict) and type(item["id"]) is int and item["id"] >= 0)
            _text(item["headline"])
            _text(item["source"], 128)
            _require(isinstance(item["symbols"], list))
            _require(all(isinstance(s, str) and s and len(s) <= 128 for s in item["symbols"]))
            _require(len(set(item["symbols"])) == len(item["symbols"]))
            _require(item.get("url") is None or isinstance(item["url"], str) and len(item["url"]) <= 2048)
            created = parse_time(item["created_at"], "news created")
            updated = parse_time(item["updated_at"], "news updated")
            _require(created <= updated <= parse_time(page["received_at"], "receipt"))
            record = {key: item.get(key) for key in (
                "id", "headline", "created_at", "updated_at", "source", "url", "symbols")}
            identity = (item["id"], item["updated_at"])
            if identity in records:
                _require(records[identity]["record"] == record)
                continue
            records[identity] = {"record": record, "available": page["received_at"]}
    return [{
        "evidence_id": "news-" + hash_value(value["record"])[:24], "kind": "news",
        "source": value["record"]["source"], "published_at": value["record"]["updated_at"],
        "available_at": value["available"], "expires_at": expiry,
        "symbols": [s for s in symbols if s in value["record"]["symbols"]],
        "content": json.dumps(value["record"], sort_keys=True),
    } for value in records.values()]


def _sessions(pages):
    sessions = {}
    for page in pages:
        _require(isinstance(page["payload"], list))
        for item in page["payload"]:
            _require(isinstance(item, dict))
            day = _text(item["date"], 10)
            _require(date.fromisoformat(day).isoformat() == day)
            close = item["session_close"]
            _require(isinstance(close, str) and re.fullmatch(r"[0-9]{4}", close) is not None)
            parsed = datetime.strptime(day + close, "%Y-%m-%d%H%M")
            _require(day not in sessions)
            sessions[day] = parsed.replace(tzinfo=NY).astimezone(timezone.utc)
    return sessions


def _bars(pages, session_pages, sessions, symbols, completed):
    required = sorted(day for day, close in sessions.items() if close <= completed)[-21:]
    _require(len(required) == 21)
    bars = {symbol: {} for symbol in symbols}
    excluded = {symbol: 0 for symbol in symbols}
    receipts = [page["received_at"] for page in pages + session_pages]
    available = max(receipts, key=lambda x: parse_time(x, "receipt"))
    for page in pages:
        _require(all(page["query"].get(key) == value for key, value in POLICY.items()))
        _require(isinstance(page["payload"], dict) and isinstance(page["payload"]["bars"], dict))
        for symbol, items in page["payload"]["bars"].items():
            _require(symbol in bars and isinstance(items, list))
            for item in items:
                _require(isinstance(item, dict))
                interval = parse_time(item["t"], "bar timestamp")
                local = interval.astimezone(NY)
                day = local.date().isoformat()
                _require(day in sessions and local.hour == local.minute == local.second == local.microsecond == 0)
                requested = parse_time(page["request_started_at"], "request")
                _require(interval <= requested)
                if sessions[day] >= requested:
                    excluded[symbol] += 1
                    continue
                value = {"session_date": day, "session_close_at": sessions[day].isoformat(),
                         **{name: _number(item[key], positive=key != "v") for name, key in (
                             ("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"), ("volume", "v"))}}
                low, high = Decimal(value["low"]), Decimal(value["high"])
                _require(low <= Decimal(value["open"]) <= high and low <= Decimal(value["close"]) <= high)
                if day in bars[symbol]:
                    _require(bars[symbol][day] == value)
                bars[symbol][day] = value
    _require(all(all(day in values for day in required) for values in bars.values()))
    return {symbol: {"bars": [values[day] for day in required], "available_at": available,
                     "source": "Alpaca IEX raw daily bars; asof=-; USD"}
            for symbol, values in bars.items()}, excluded


def _normalize_market(capture, expiry):
    _require(isinstance(capture, dict) and type(capture["schema_version"]) is int and capture["schema_version"] == 1)
    symbols = capture["symbols"]
    _require(isinstance(symbols, list) and bool(symbols) and len(symbols) <= 20)
    _require(all(isinstance(s, str) and s and s.strip() == s for s in symbols))
    _require(len(set(symbols)) == len(symbols) and isinstance(capture["sources"], dict))
    started = parse_time(capture["started_at"], "collection start")
    completed = parse_time(capture["completed_at"], "collection completion")
    _require(started <= completed < parse_time(expiry, "expiry"))
    pages = {name: _pages(capture, name, started, completed) for name in ("news", "sessions", "bars")}
    sessions = _sessions(pages["sessions"])
    bar_inputs, excluded = _bars(pages["bars"], pages["sessions"], sessions, symbols, completed)
    return {"news": _news(pages["news"], symbols, expiry), "bar_inputs": bar_inputs,
            "excluded_partial_bars": excluded}


def normalize_market(capture, *, expires_at):
    """Return sourced news and complete last21-session inputs, or fail closed."""
    try:
        return _normalize_market(capture, expires_at)
    except NormalizationError:
        raise
    except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError):
        raise NormalizationError("invalid_market_capture") from None
