"""Synthetic fixtures for bounded prospective-source acquisition."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from prospective_evidence.acquire import collect_sources


STARTED = datetime(2026, 11, 16, 2, 8, tzinfo=timezone.utc)
STAMP = "2026-11-16T02:08:00+00:00"
HASH = "a" * 64


@pytest.mark.parametrize("bad_code", [[], {}, None, False])
def test_malformed_error_code_does_not_escape_partial_failure_sanitation(bad_code):
    class FixtureFailure(ValueError):
        def __init__(self, code):
            self.code = code
            super().__init__("private-upstream-detail")

    failure = FixtureFailure(bad_code)
    alpaca = SyntheticSource([receipt([]), failure, receipt({"news": [], "next_page_token": None})])
    public = SyntheticSource([receipt("synthetic calendar")])
    capture = collect_sources(["SPY"], alpaca, public, clock=lambda: STARTED)
    assert capture["sources"]["bars"]["error"]["code"] == "invalid_source_response"
    assert capture["sources"]["bea_calendar"]["complete"] is True
    assert "private-upstream-detail" not in str(capture)


class SyntheticSource:
    """A labelled fake transport; no result from this class is real evidence."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def fetch(self, endpoint, params):
        self.calls.append((endpoint, copy.deepcopy(params)))
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return copy.deepcopy(reply)


def receipt(payload, **extra):
    return {
        "payload": payload,
        "request_started_at": STAMP,
        "received_at": STAMP,
        "body_sha256": HASH,
        "rate_limit": {"limit": 200, "remaining": 199, "reset": 1},
        **extra,
    }


def ticking_clock(*values):
    iterator = iter(values)
    return lambda: next(iterator)


def test_collects_all_four_sources_in_fixed_order_with_strict_policy_and_projection():
    alpaca = SyntheticSource(
        [
            receipt(
                [
                    {
                        "date": "2026-11-13",
                        "open": "09:30",
                        "close": "16:00",
                        "session_open": "0400",
                        "session_close": "2000",
                        "settlement_date": "2026-11-16",
                        "private": "discard",
                    }
                ],
                private_receipt="discard",
            ),
            receipt(
                {
                    "bars": {
                        "SPY": [
                            {
                                "t": "2026-11-13T05:00:00Z",
                                "o": "680.10",
                                "h": "681.00",
                                "l": "679.00",
                                "c": "680.50",
                                "v": 123,
                                "vw": "680.20",
                            }
                        ],
                        "QQQ": [],
                    },
                    "next_page_token": None,
                    "private": "discard",
                }
            ),
            receipt(
                {
                    "news": [
                        {
                            "id": 7,
                            "headline": "Synthetic headline retained in full",
                            "created_at": "2026-11-15T01:00:00Z",
                            "updated_at": "2026-11-15T02:00:00Z",
                            "url": None,
                            "symbols": ["SPY", "QQQ"],
                            "source": "synthetic-wire",
                            "content": "discard secret-like article body",
                            "summary": "discard",
                            "images": ["discard"],
                            "author": "discard",
                        }
                    ],
                    "next_page_token": None,
                }
            ),
        ]
    )
    public = SyntheticSource([receipt("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")])

    capture = collect_sources(
        ["SPY", "QQQ"],
        alpaca,
        public,
        clock=ticking_clock(STARTED, STARTED),
    )

    assert [endpoint for endpoint, _ in alpaca.calls] == ["sessions", "bars", "news"]
    assert public.calls == [("bea_calendar", {})]
    assert alpaca.calls == [
        (
            "sessions",
            {"start": "2026-07-18", "end": "2026-11-15", "date_type": "TRADING"},
        ),
        (
            "bars",
            {
                "symbols": "SPY,QQQ",
                "timeframe": "1Day",
                "start": "2026-07-18",
                "end": STAMP,
                "feed": "iex",
                "adjustment": "raw",
                "asof": "-",
                "currency": "USD",
                "sort": "asc",
                "limit": 10000,
            },
        ),
        (
            "news",
            {
                "symbols": "SPY,QQQ",
                "start": "2026-11-13T02:08:00+00:00",
                "end": STAMP,
                "sort": "asc",
                "limit": 50,
                "include_content": "false",
                "exclude_contentless": "false",
            },
        ),
    ]
    assert capture["schema_version"] == 1
    assert capture["started_at"] == STAMP
    assert capture["completed_at"] == STAMP
    assert capture["symbols"] == ["SPY", "QQQ"]
    assert capture["policy"] == {
        "feed": "iex",
        "adjustment": "raw",
        "asof": "-",
        "currency": "USD",
        "bar_completion": "extended_session_close",
        "news_metadata_only": True,
    }
    assert set(capture["sources"]) == {"sessions", "bars", "news", "bea_calendar"}
    assert all(source["complete"] and source["error"] is None for source in capture["sources"].values())
    assert capture["sources"]["sessions"]["pages"][0]["payload"] == [
        {
            "date": "2026-11-13",
            "open": "09:30",
            "close": "16:00",
            "session_open": "0400",
            "session_close": "2000",
            "settlement_date": "2026-11-16",
        }
    ]
    assert capture["sources"]["bars"]["pages"][0]["payload"] == {
        "bars": {
            "SPY": [
                {
                    "t": "2026-11-13T05:00:00Z",
                    "o": "680.10",
                    "h": "681.00",
                    "l": "679.00",
                    "c": "680.50",
                    "v": 123,
                }
            ],
            "QQQ": [],
        },
        "next_page_token": None,
    }
    news = capture["sources"]["news"]["pages"][0]["payload"]["news"][0]
    assert news == {
        "id": 7,
        "headline": "Synthetic headline retained in full",
        "created_at": "2026-11-15T01:00:00Z",
        "updated_at": "2026-11-15T02:00:00Z",
        "url": None,
        "symbols": ["SPY", "QQQ"],
        "source": "synthetic-wire",
    }
    assert "private_receipt" not in capture["sources"]["sessions"]["pages"][0]


def valid_sources(*, bar_payload=None, news_payload=None):
    alpaca = SyntheticSource(
        [
            receipt([]),
            receipt(
                bar_payload
                if bar_payload is not None
                else {"bars": {"SPY": []}, "next_page_token": None}
            ),
            receipt(
                news_payload
                if news_payload is not None
                else {"news": [], "next_page_token": None}
            ),
        ]
    )
    public = SyntheticSource([receipt("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")])
    return alpaca, public


def test_paginates_short_and_empty_pages_until_explicit_terminal_token():
    alpaca = SyntheticSource(
        [
            receipt([]),
            receipt({"bars": {"SPY": []}, "next_page_token": "bars-2"}),
            receipt({"bars": {"SPY": []}, "next_page_token": None}),
            receipt({"news": [], "next_page_token": "news-2"}),
            receipt(
                {
                    "news": [
                        {
                            "id": 8,
                            "headline": "synthetic",
                            "created_at": STAMP,
                            "updated_at": STAMP,
                            "url": "https://example.invalid/synthetic",
                            "symbols": ["SPY"],
                            "source": "synthetic",
                        }
                    ],
                    "next_page_token": None,
                }
            ),
        ]
    )
    public = SyntheticSource([receipt("calendar")])

    result = collect_sources(
        ["SPY"], alpaca, public, clock=ticking_clock(STARTED, STARTED)
    )

    assert [page["ordinal"] for page in result["sources"]["bars"]["pages"]] == [1, 2]
    assert result["sources"]["bars"]["pages"][1]["query"]["page_token"] == "bars-2"
    assert result["sources"]["news"]["pages"][1]["query"]["page_token"] == "news-2"
    assert result["sources"]["bars"]["complete"] is True
    assert result["sources"]["news"]["complete"] is True


def test_pagination_cycle_and_page_limit_are_honest_partial_sources():
    cycle_alpaca = SyntheticSource(
        [
            receipt([]),
            receipt({"bars": {"SPY": []}, "next_page_token": "repeat"}),
            receipt({"bars": {"SPY": []}, "next_page_token": "repeat"}),
            receipt({"news": [], "next_page_token": None}),
        ]
    )
    cycle = collect_sources(
        ["SPY"],
        cycle_alpaca,
        SyntheticSource([receipt("calendar")]),
        clock=ticking_clock(STARTED, STARTED),
    )
    assert cycle["sources"]["bars"] == {
        "pages": cycle["sources"]["bars"]["pages"],
        "complete": False,
        "error": {"code": "pagination_cycle", "http_status": None},
    }
    assert len(cycle["sources"]["bars"]["pages"]) == 2

    limited_alpaca = SyntheticSource(
        [
            receipt([]),
            receipt({"bars": {"SPY": []}, "next_page_token": "more-bars"}),
            receipt({"news": [], "next_page_token": "more-news"}),
        ]
    )
    limited = collect_sources(
        ["SPY"],
        limited_alpaca,
        SyntheticSource([receipt("calendar")]),
        clock=ticking_clock(STARTED, STARTED),
        max_pages=1,
    )
    assert limited["sources"]["bars"]["error"] == {
        "code": "pagination_limit",
        "http_status": None,
    }
    assert limited["sources"]["news"]["error"] == {
        "code": "pagination_limit",
        "http_status": None,
    }
    assert not limited["sources"]["bars"]["complete"]
    assert not limited["sources"]["news"]["complete"]


class SyntheticError(ValueError):
    def __init__(self, code, status=None, text="secret traceback"):
        self.code = code
        self.http_status = status
        super().__init__(text)


def test_source_failures_are_sanitized_and_independent_sources_continue():
    alpaca = SyntheticSource(
        [
            SyntheticError("source_http_error", 503),
            receipt({"bars": {"SPY": []}, "next_page_token": "second"}),
            OSError("private host detail"),
            receipt({"news": [], "next_page_token": None}),
        ]
    )
    public = SyntheticSource([SyntheticError("remote secret code", 999)])

    result = collect_sources(
        ["SPY"], alpaca, public, clock=ticking_clock(STARTED, STARTED)
    )

    assert result["sources"]["sessions"]["error"] == {
        "code": "source_http_error",
        "http_status": 503,
    }
    assert result["sources"]["bars"]["error"] == {
        "code": "source_unavailable",
        "http_status": None,
    }
    assert len(result["sources"]["bars"]["pages"]) == 1
    assert result["sources"]["news"]["complete"] is True
    assert result["sources"]["bea_calendar"]["error"] == {
        "code": "invalid_source_response",
        "http_status": None,
    }
    serialized = repr(result)
    assert "secret" not in serialized
    assert "private host" not in serialized


def test_bad_source_shapes_and_receipts_are_partial_not_silently_projected():
    cases = [
        ({"bars": {"ATTACKER": []}, "next_page_token": None}, "bars"),
        ({"bars": [], "next_page_token": None}, "bars"),
        ({"bars": {"SPY": []}}, "bars"),
        ({"news": "not-a-list", "next_page_token": None}, "news"),
    ]
    for payload, source_name in cases:
        alpaca, public = valid_sources(
            bar_payload=payload if source_name == "bars" else None,
            news_payload=payload if source_name == "news" else None,
        )
        result = collect_sources(
            ["SPY"], alpaca, public, clock=ticking_clock(STARTED, STARTED)
        )
        assert result["sources"][source_name]["error"] == {
            "code": "invalid_source_response",
            "http_status": None,
        }

    bad_receipt = receipt([])
    bad_receipt["body_sha256"] = "not-a-hash"
    alpaca = SyntheticSource(
        [bad_receipt, receipt({"bars": {"SPY": []}, "next_page_token": None}), receipt({"news": [], "next_page_token": None})]
    )
    result = collect_sources(
        ["SPY"], alpaca, SyntheticSource([receipt("calendar")]), clock=ticking_clock(STARTED, STARTED)
    )
    assert result["sources"]["sessions"]["error"]["code"] == "invalid_source_response"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: "not-a-receipt",
        lambda value: {key: item for key, item in value.items() if key != "received_at"},
        lambda value: {**value, "request_started_at": 7},
        lambda value: {**value, "received_at": "not-a-date"},
        lambda value: {**value, "rate_limit": []},
        lambda value: {**value, "rate_limit": {"private": 1}},
        lambda value: {**value, "rate_limit": {"limit": True}},
    ],
)
def test_invalid_receipt_variants_are_rejected(mutation):
    bad = mutation(receipt([]))
    alpaca = SyntheticSource(
        [bad, receipt({"bars": {"SPY": []}, "next_page_token": None}), receipt({"news": [], "next_page_token": None})]
    )
    result = collect_sources(
        ["SPY"], alpaca, SyntheticSource([receipt("calendar")]), clock=ticking_clock(STARTED, STARTED)
    )
    assert result["sources"]["sessions"]["error"]["code"] == "invalid_source_response"


@pytest.mark.parametrize(
    ("endpoint_payload", "source_name"),
    [
        ({"bars": {"SPY": "bad"}, "next_page_token": None}, "bars"),
        ({"bars": {"SPY": [{}]}, "next_page_token": None}, "bars"),
        ({"bars": {"SPY": []}, "next_page_token": ""}, "bars"),
        ({"news": [{}], "next_page_token": None}, "news"),
        ({"next_page_token": None}, "news"),
    ],
)
def test_additional_payload_shape_failures(endpoint_payload, source_name):
    alpaca, public = valid_sources(
        bar_payload=endpoint_payload if source_name == "bars" else None,
        news_payload=endpoint_payload if source_name == "news" else None,
    )
    result = collect_sources(
        ["SPY"], alpaca, public, clock=ticking_clock(STARTED, STARTED)
    )
    assert result["sources"][source_name]["error"]["code"] == "invalid_source_response"


def test_invalid_sessions_and_calendar_payloads_are_rejected():
    alpaca = SyntheticSource(
        [
            receipt({}),
            receipt({"bars": {"SPY": []}, "next_page_token": None}),
            receipt({"news": [], "next_page_token": None}),
        ]
    )
    result = collect_sources(
        ["SPY"], alpaca, SyntheticSource([receipt([])]), clock=ticking_clock(STARTED, STARTED)
    )
    assert result["sources"]["sessions"]["error"]["code"] == "invalid_source_response"
    assert result["sources"]["bea_calendar"]["error"]["code"] == "invalid_source_response"

    alpaca = SyntheticSource(
        [
            receipt([{}]),
            receipt({"next_page_token": None}),
            receipt({"news": [], "next_page_token": None}),
        ]
    )
    result = collect_sources(
        ["SPY"], alpaca, SyntheticSource([receipt("calendar")]), clock=ticking_clock(STARTED, STARTED)
    )
    assert result["sources"]["sessions"]["error"]["code"] == "invalid_source_response"
    assert result["sources"]["bars"]["error"]["code"] == "invalid_source_response"


def test_record_count_bounds_are_enforced():
    session = {
        "date": "2026-11-16",
        "open": "09:30",
        "close": "16:00",
        "session_open": "04:00",
        "session_close": "20:00",
        "settlement_date": "2026-11-18",
    }
    bar = {
        "t": "2026-11-16T05:00:00Z",
        "o": "680.00",
        "h": "681.00",
        "l": "679.00",
        "c": "680.50",
        "v": 100,
    }
    news = {
        "id": 1,
        "headline": "Synthetic headline",
        "created_at": STAMP,
        "updated_at": STAMP,
        "url": None,
        "symbols": ["SPY"],
        "source": "synthetic-wire",
    }
    alpaca = SyntheticSource(
        [
            receipt([session] * 20_001),
            receipt({"bars": {"SPY": [bar] * 20_001}, "next_page_token": None}),
            receipt({"news": [news] * 20_001, "next_page_token": None}),
        ]
    )
    result = collect_sources(
        ["SPY"], alpaca, SyntheticSource([receipt("calendar")]), clock=ticking_clock(STARTED, STARTED)
    )
    assert all(
        result["sources"][name]["error"]["code"] == "invalid_source_response"
        for name in ("sessions", "bars", "news")
    )


def test_timezone_uses_new_york_day_across_dst_and_capture_does_not_mutate_input():
    symbols = ["SPY"]
    before = list(symbols)
    instant = datetime(2026, 3, 8, 4, 30, tzinfo=timezone.utc)
    alpaca, public = valid_sources()
    result = collect_sources(
        symbols,
        alpaca,
        public,
        clock=ticking_clock(instant, instant + timedelta(seconds=1)),
    )
    assert alpaca.calls[0][1] == {
        "start": "2025-11-07",
        "end": "2026-03-07",
        "date_type": "TRADING",
    }
    assert result["started_at"] == "2026-03-08T04:30:00+00:00"
    assert result["completed_at"] == "2026-03-08T04:30:01+00:00"
    assert symbols == before
    assert result["symbols"] is not symbols


@pytest.mark.parametrize(
    "symbols",
    [
        [],
        ["SPY", "SPY"],
        [""],
        [" SPY"],
        ["SP Y"],
        ["SPY,QQQ"],
        ["SPY"] * 21,
        ("SPY",),
    ],
)
def test_rejects_invalid_symbol_inputs_before_source_calls(symbols):
    alpaca, public = valid_sources()
    with pytest.raises(ValueError):
        collect_sources(symbols, alpaca, public, clock=lambda: STARTED)
    assert alpaca.calls == []
    assert public.calls == []


@pytest.mark.parametrize("max_pages", [0, 17, True, 1.0, "1"])
def test_rejects_invalid_page_limit_before_source_calls(max_pages):
    alpaca, public = valid_sources()
    with pytest.raises(ValueError):
        collect_sources(["SPY"], alpaca, public, clock=lambda: STARTED, max_pages=max_pages)
    assert alpaca.calls == []
    assert public.calls == []


@pytest.mark.parametrize("clock", [lambda: datetime(2026, 1, 1), "not-callable"])
def test_invalid_capture_clock_fails_before_source_calls_and_programming_errors_escape(clock):
    alpaca, public = valid_sources()
    with pytest.raises(ValueError):
        collect_sources(["SPY"], alpaca, public, clock=clock)
    assert alpaca.calls == []

    class BuggySource:
        def fetch(self, endpoint, params):
            raise RuntimeError("programming bug")

    with pytest.raises(RuntimeError, match="programming bug"):
        collect_sources(
            ["SPY"], BuggySource(), public, clock=ticking_clock(STARTED, STARTED)
        )
