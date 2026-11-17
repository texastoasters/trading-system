"""Synthetic provider-shaped fixtures; never represented as actual observations."""
from datetime import date, timedelta
import copy

import pytest

from prospective_evidence import normalize


@pytest.mark.parametrize("requested,accepted", [
    ("2026-01-22T00:59:59.999999Z", False),
    ("2026-01-22T01:00:00Z", False),
    ("2026-01-22T01:00:00.000001Z", True),
])
def test_daily_bar_request_must_be_strictly_after_extended_close(requested, accepted):
    capture = fixture_capture()
    capture["started_at"] = "2026-01-22T00:59:00Z"
    capture["sources"]["bars"]["pages"][0]["request_started_at"] = requested
    if not accepted:
        with pytest.raises(normalize.NormalizationError, match="invalid_market_capture"):
            normalize.normalize_market(capture, expires_at="2026-01-22T01:20:00Z")
    else:
        result = normalize.normalize_market(capture, expires_at="2026-01-22T01:20:00Z")
        assert result["bar_inputs"]["SPY"]["bars"][-1]["session_date"] == "2026-01-21"
        assert result["excluded_partial_bars"]["SPY"] == 0


def fixture_capture():
    symbols = ["SPY"]
    sessions, bars = [], []
    for index in range(21):
        day = (date(2026, 1, 1) + timedelta(days=index)).isoformat()
        sessions.append({"date": day, "open": "09:30", "close": "16:00", "session_close": "2000"})
        bars.append({"t": day + "T05:00:00Z", "o": "100", "h": "102", "l": "99", "c": "101", "v": 100})
    def source(payload, query=None):
        return {"complete": True, "error": None, "pages": [{
            "payload": payload, "query": query or {}, "ordinal": 1,
            "request_started_at": "2026-01-22T01:01:00Z",
            "received_at": "2026-01-22T01:02:00Z", "body_sha256": "0" * 64,
            "rate_limit": {},
        }]}
    return {
        "schema_version": 1, "symbols": symbols,
        "started_at": "2026-01-22T01:00:00Z", "completed_at": "2026-01-22T01:05:00Z",
        "sources": {
            "sessions": source(sessions),
            "bars": source({"bars": {"SPY": bars}, "next_page_token": None}, {
                "feed": "iex", "adjustment": "raw", "asof": "-", "currency": "USD", "timeframe": "1Day"}),
            "news": source({"news": [{"id": 1, "headline": "SYNTHETIC fixture headline",
                "created_at": "2026-01-21T12:00:00Z", "updated_at": "2026-01-21T13:00:00Z",
                "source": "fixture", "symbols": ["SPY", "OTHER"], "url": None}], "next_page_token": None}),
        },
    }


def test_normalizes_observation_time_and_completed_sessions():
    result = normalize.normalize_market(fixture_capture(), expires_at="2026-01-22T01:20:00Z")
    assert result["news"][0]["symbols"] == ["SPY"]
    assert result["news"][0]["published_at"] == "2026-01-21T13:00:00Z"
    assert result["news"][0]["available_at"] == "2026-01-22T01:02:00Z"
    value = result["bar_inputs"]["SPY"]
    assert len(value["bars"]) == 21
    assert value["bars"][-1]["session_date"] == "2026-01-21"
    assert value["bars"][-1]["session_close_at"] == "2026-01-22T01:00:00+00:00"
    assert value["bars"][-1]["close"] == "101"
    assert value["available_at"] == "2026-01-22T01:02:00Z"


def test_partial_extended_session_bar_is_excluded():
    value = fixture_capture()
    value["started_at"] = "2026-01-22T22:00:00Z"
    value["completed_at"] = "2026-01-22T22:05:00Z"
    for source in value["sources"].values():
        source["pages"][0]["request_started_at"] = "2026-01-22T22:01:00Z"
        source["pages"][0]["received_at"] = "2026-01-22T22:02:00Z"
    value["sources"]["sessions"]["pages"][0]["payload"].append(
        {"date": "2026-01-22", "open": "09:30", "close": "16:00", "session_close": "2000"})
    value["sources"]["bars"]["pages"][0]["payload"]["bars"]["SPY"].append(
        {"t": "2026-01-22T05:00:00Z", "o": "100", "h": "102", "l": "99", "c": "101", "v": 10})
    result = normalize.normalize_market(value, expires_at="2026-01-22T22:20:00Z")
    assert result["bar_inputs"]["SPY"]["bars"][-1]["session_date"] == "2026-01-21"


def test_identical_news_and_bars_deduplicated_without_losing_sessions():
    value = fixture_capture()
    for kind, field in (("news", "news"), ("bars", "bars")):
        items = value["sources"][kind]["pages"][0]["payload"][field]
        if kind == "bars":
            items = items["SPY"]
        items.append(copy.deepcopy(items[-1]))
    result = normalize.normalize_market(value, expires_at="2026-01-22T01:20:00Z")
    assert len(result["news"]) == 1
    assert len({x["session_date"] for x in result["bar_inputs"]["SPY"]["bars"]}) == 21


@pytest.mark.parametrize("case", ["future_news", "conflicting_news", "conflicting_bar", "bad_feed", "insufficient", "incomplete", "future_receipt", "future_session_needed", "bad_ohlc", "bad_type"])
def test_invalid_source_capture_fails_closed(case):
    value = fixture_capture()
    news = value["sources"]["news"]["pages"][0]["payload"]["news"]
    bars = value["sources"]["bars"]["pages"][0]["payload"]["bars"]["SPY"]
    if case == "future_news":
        news[0]["updated_at"] = "2026-02-01T00:00:00Z"
    elif case == "conflicting_news":
        other = copy.deepcopy(news[0]); other["headline"] = "conflicting same revision"; news.append(other)
    elif case == "conflicting_bar":
        other = copy.deepcopy(bars[-1]); other["c"] = "100"; bars.append(other)
    elif case == "bad_feed":
        value["sources"]["bars"]["pages"][0]["query"]["feed"] = "sip"
    elif case == "insufficient":
        bars.pop(0)
    elif case == "incomplete":
        value["sources"]["news"]["complete"] = False
    elif case == "future_receipt":
        value["sources"]["news"]["pages"][0]["received_at"] = "2026-02-01T00:00:00Z"
    elif case == "future_session_needed":
        value["sources"]["sessions"]["pages"][0]["payload"].append(
            {"date": "2026-01-22", "session_close": "0100"})
        value["completed_at"] = "2026-01-22T07:00:00Z"
    elif case == "bad_ohlc":
        bars[-1]["h"] = "99"
    else:
        value["sources"]["bars"]["pages"][0]["payload"]["bars"] = []
    with pytest.raises(normalize.NormalizationError):
        normalize.normalize_market(value, expires_at="2026-02-02T00:00:00Z")


@pytest.mark.parametrize("case", ["bad_time", "bad_decimal", "missing_field"])
def test_parser_failures_are_public_sanitized_errors(case):
    value = fixture_capture()
    if case == "bad_time":
        value["started_at"] = "not-a-date"
    elif case == "bad_decimal":
        value["sources"]["bars"]["pages"][0]["payload"]["bars"]["SPY"][-1]["c"] = "not-a-number"
    else:
        del value["symbols"]
    with pytest.raises(normalize.NormalizationError, match="^invalid_market_capture$"):
        normalize.normalize_market(value, expires_at="2026-01-22T01:20:00Z")


def test_distinct_news_revisions_preserved_and_inputs_unchanged():
    value = fixture_capture()
    news = value["sources"]["news"]["pages"][0]["payload"]["news"]
    revision = copy.deepcopy(news[0])
    revision["updated_at"] = "2026-01-21T14:00:00Z"
    revision["headline"] = "SYNTHETIC updated fixture"
    news.append(revision)
    original = copy.deepcopy(value)
    result = normalize.normalize_market(value, expires_at="2026-01-22T01:20:00Z")
    assert len(result["news"]) == 2
    assert len({x["evidence_id"] for x in result["news"]}) == 2
    assert value == original


def test_normalized_new_york_evening_close_is_accepted_by_detector():
    from prospective_evidence.signals import derive_bar_evidence
    value = normalize.normalize_market(fixture_capture(), expires_at="2026-01-22T01:20:00Z")
    records = derive_bar_evidence("SPY", **value["bar_inputs"]["SPY"], expires_at="2026-01-22T01:20:00Z")
    assert [item["kind"] for item in records] == ["technical", "algorithmic"]
