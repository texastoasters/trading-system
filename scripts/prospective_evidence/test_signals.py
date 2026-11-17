from __future__ import annotations

import copy
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from prospective_evidence.signals import SignalError, derive_bar_evidence


AVAILABLE_AT = "2026-11-16T21:00:00+00:00"
EXPIRES_AT = "2026-11-17T21:00:00+00:00"
SOURCE = "synthetic-completed-daily-bars"


def synthetic_bars() -> list[dict[str, str]]:
    """Twenty-one explicitly synthetic completed daily sessions."""
    first = date(2026, 10, 20)
    bars: list[dict[str, str]] = []
    for index in range(21):
        session = first + timedelta(days=index)
        close = 100 + index
        bars.append(
            {
                "session_date": session.isoformat(),
                "session_close_at": datetime(
                    session.year, session.month, session.day, 20, tzinfo=timezone.utc
                ).isoformat(),
                "open": str(close - 1),
                "high": str(close + 1),
                "low": str(close - 2),
                "close": str(close),
                "volume": str(1000 + index * 10),
            }
        )
    return bars


def test_derive_bar_evidence_returns_normalized_lab_records_with_known_metrics():
    records = derive_bar_evidence(
        "SPY",
        synthetic_bars(),
        available_at=AVAILABLE_AT,
        expires_at=EXPIRES_AT,
        source=SOURCE,
    )

    assert [record["kind"] for record in records] == ["technical", "algorithmic"]
    assert all(
        record["source"] == SOURCE
        and record["published_at"] == AVAILABLE_AT
        and record["available_at"] == AVAILABLE_AT
        and record["expires_at"] == EXPIRES_AT
        and record["symbols"] == ["SPY"]
        for record in records
    )
    technical, algorithmic = records
    assert technical["metrics"] == {
        "last_body": 1.0,
        "last_range": 3.0,
        "inside_bar": 0,
        "bullish_full_body_engulfing": 0,
        "bearish_full_body_engulfing": 0,
        "doji": 0,
        "flat_bar": 0,
    }
    assert algorithmic["metrics"] == pytest.approx(
        {
            "sma_20": 110.5,
            "return_20_sessions": 0.2,
            "price_relative_to_sma_20": 120 / 110.5 - 1,
            "current_volume_to_previous_20_mean": 1200 / 1095,
        }
    )
    technical_content = json.loads(technical["content"])
    algorithmic_content = json.loads(algorithmic["content"])
    assert technical_content["last_session"]["close"] == "120"
    assert technical_content["definitions"]["doji"] == (
        "absolute body is at most 10% of a nonzero high-low range"
    )
    assert algorithmic_content["calculation_window"]["closes"] == [
        str(value) for value in range(100, 121)
    ]
    assert technical_content["source_provenance"]["last_session_close_at"] == (
        "2026-11-09T20:00:00+00:00"
    )
    assert technical_content["source_provenance"]["source_fingerprint"].startswith(
        "sha256:"
    )
    assert technical_content["source_provenance"]["source_fingerprint"] == (
        algorithmic_content["source_provenance"]["source_fingerprint"]
    )


@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        (
            {"open": "120", "high": "121", "low": "117", "close": "118"},
            {"open": "117.5", "high": "121", "low": "117", "close": "120.5"},
            {"bullish_full_body_engulfing": 1, "bearish_full_body_engulfing": 0},
        ),
        (
            {"open": "118", "high": "121", "low": "117", "close": "120"},
            {"open": "120.5", "high": "121", "low": "117", "close": "117.5"},
            {"bullish_full_body_engulfing": 0, "bearish_full_body_engulfing": 1},
        ),
        (
            {"open": "118", "high": "122", "low": "116", "close": "120"},
            {"open": "119", "high": "121", "low": "117", "close": "119.2"},
            {"inside_bar": 1, "doji": 1},
        ),
        (
            {"open": "118", "high": "122", "low": "116", "close": "120"},
            {"open": "120", "high": "120", "low": "120", "close": "120"},
            {"flat_bar": 1, "doji": 0},
        ),
    ],
)
def test_derive_bar_evidence_describes_boundary_chart_patterns(previous, current, expected):
    bars = synthetic_bars()
    bars[-2].update(previous)
    bars[-1].update(current)

    technical = derive_bar_evidence(
        "SPY", bars, available_at=AVAILABLE_AT, expires_at=EXPIRES_AT, source=SOURCE
    )[0]

    assert {name: technical["metrics"][name] for name in expected} == expected


def test_derive_bar_evidence_includes_exact_doji_threshold_only():
    at_threshold = synthetic_bars()
    at_threshold[-1].update(
        {"open": "110", "high": "115", "low": "105", "close": "111"}
    )
    above_threshold = copy.deepcopy(at_threshold)
    above_threshold[-1]["close"] = "111.0001"

    at_record = derive_bar_evidence(
        "SPY",
        at_threshold,
        available_at=AVAILABLE_AT,
        expires_at=EXPIRES_AT,
        source=SOURCE,
    )[0]
    above_record = derive_bar_evidence(
        "SPY",
        above_threshold,
        available_at=AVAILABLE_AT,
        expires_at=EXPIRES_AT,
        source=SOURCE,
    )[0]

    assert at_record["metrics"]["doji"] == 1
    assert above_record["metrics"]["doji"] == 0


def test_zero_previous_volume_omits_ratio_and_explains_unavailability():
    bars = synthetic_bars()
    for bar in bars[:-1]:
        bar["volume"] = "0"
    bars[-1]["volume"] = "25"

    algorithmic = derive_bar_evidence(
        "SPY", bars, available_at=AVAILABLE_AT, expires_at=EXPIRES_AT, source=SOURCE
    )[1]

    assert "current_volume_to_previous_20_mean" not in algorithmic["metrics"]
    assert json.loads(algorithmic["content"])["unavailable_metrics"] == {
        "current_volume_to_previous_20_mean": "previous 20-session mean volume is zero"
    }


def test_evidence_identity_is_deterministic_sensitive_and_does_not_mutate_input():
    bars = synthetic_bars()
    original = copy.deepcopy(bars)

    first = derive_bar_evidence(
        "SPY", bars, available_at=AVAILABLE_AT, expires_at=EXPIRES_AT, source=SOURCE
    )
    repeated = derive_bar_evidence(
        "SPY", bars, available_at=AVAILABLE_AT, expires_at=EXPIRES_AT, source=SOURCE
    )
    changed_bars = copy.deepcopy(bars)
    changed_bars[-1]["volume"] = "1201"
    changed = derive_bar_evidence(
        "SPY", changed_bars, available_at=AVAILABLE_AT, expires_at=EXPIRES_AT, source=SOURCE
    )

    assert first == repeated
    assert [record["evidence_id"] for record in first] != [
        record["evidence_id"] for record in changed
    ]
    other_symbol = derive_bar_evidence(
        "QQQ", bars, available_at=AVAILABLE_AT, expires_at=EXPIRES_AT, source=SOURCE
    )
    assert first[0]["evidence_id"] != other_symbol[0]["evidence_id"]
    assert json.loads(first[0]["content"])["source_provenance"]["source_fingerprint"] == (
        json.loads(other_symbol[0]["content"])["source_provenance"]["source_fingerprint"]
    )
    assert bars == original


def set_bar(index, field, value):
    def mutate(arguments):
        arguments["bars"][index][field] = value

    return mutate


def delete_bar_field(index, field):
    def mutate(arguments):
        del arguments["bars"][index][field]

    return mutate


def add_bar_field(index, field, value):
    def mutate(arguments):
        arguments["bars"][index][field] = value

    return mutate


def put_last_close_after_availability(arguments):
    arguments["available_at"] = "2026-11-09T21:00:00+00:00"
    arguments["expires_at"] = "2026-11-10T21:00:00+00:00"
    arguments["bars"][-1]["session_close_at"] = "2026-11-09T22:00:00+00:00"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda a: a.update(symbol=""), "symbol"),
        (lambda a: a.update(symbol=" SPY"), "symbol"),
        (lambda a: a.update(symbol=1), "symbol"),
        (lambda a: a.update(source=""), "source"),
        (lambda a: a.update(source="feed "), "source"),
        (lambda a: a.update(source=[]), "source"),
        (lambda a: a.update(bars=tuple(a["bars"])), "bars"),
        (lambda a: a.update(bars=a["bars"][:20]), "21"),
        (lambda a: a["bars"].__setitem__(0, []), "object"),
        (delete_bar_field(0, "close"), "normalized bar fields"),
        (add_bar_field(0, "adjustment", "raw"), "normalized bar fields"),
        (set_bar(0, "session_date", 1), "ISO date"),
        (set_bar(0, "session_date", "2026-1-2"), "ISO date"),
        (set_bar(0, "session_date", "20261020"), "normalized ISO date"),
        (set_bar(1, "session_date", "2026-10-20"), "strictly ascending"),
        (set_bar(2, "session_date", "2026-10-20"), "strictly ascending"),
        (set_bar(0, "session_close_at", 1), "ISO8601"),
        (set_bar(0, "session_close_at", "2026-10-20T20:00:00"), "timezone-aware"),
        (set_bar(-1, "session_date", "2026-11-10"), "must match session_date"),
        (put_last_close_after_availability, "available_at"),
        (set_bar(0, "open", 100), "decimal string"),
        (set_bar(0, "open", "not-a-number"), "decimal string"),
        (set_bar(0, "open", " 99"), "decimal string"),
        (set_bar(0, "open", "NaN"), "finite"),
        (set_bar(0, "high", "Infinity"), "finite"),
        (set_bar(0, "low", "-1"), "positive"),
        (set_bar(0, "close", "0"), "positive"),
        (set_bar(0, "volume", "-1"), "nonnegative"),
        (set_bar(0, "volume", None), "decimal string"),
        (set_bar(0, "high", "98"), "OHLC"),
        (set_bar(0, "low", "101"), "OHLC"),
        (lambda a: a.update(available_at=1), "available_at"),
        (lambda a: a.update(available_at="2026-11-16T21:00:00"), "timezone-aware"),
        (lambda a: a.update(expires_at="not-a-time"), "expires_at"),
        (lambda a: a.update(expires_at=AVAILABLE_AT), "follow"),
        (lambda a: a.update(expires_at="2026-11-16T20:59:59+00:00"), "follow"),
    ],
)
def test_derive_bar_evidence_rejects_invalid_public_inputs(mutate, message):
    arguments = {
        "symbol": "SPY",
        "bars": synthetic_bars(),
        "available_at": AVAILABLE_AT,
        "expires_at": EXPIRES_AT,
        "source": SOURCE,
    }
    mutate(arguments)

    with pytest.raises(SignalError, match=message):
        derive_bar_evidence(**arguments)


def test_derive_bar_evidence_rejects_finite_values_that_cannot_be_finite_metrics():
    bars = synthetic_bars()
    bars[-1].update(
        {"open": "1E+999999", "high": "1E+999999", "low": "1", "close": "1E+999999"}
    )

    with pytest.raises(SignalError, match="derived metric must be finite"):
        derive_bar_evidence(
            "SPY", bars, available_at=AVAILABLE_AT, expires_at=EXPIRES_AT, source=SOURCE
        )


def test_records_pass_existing_lab_evidence_validation_without_reimplementing_it():
    from scripts.decision_lab.validation import validate_bundle

    evidence = derive_bar_evidence(
        "SPY",
        synthetic_bars(),
        available_at=AVAILABLE_AT,
        expires_at=EXPIRES_AT,
        source=SOURCE,
    )
    bundle = {
        "schema_version": 1,
        "run_id": "synthetic-signal-contract",
        "mode": "historical_diagnostic",
        "as_of": AVAILABLE_AT,
        "expires_at": EXPIRES_AT,
        "mandate_version": "synthetic-fixture-v1",
        "universe": [{"symbol": "SPY", "asset_class": "etf"}],
        "portfolio": {
            "snapshot_id": "synthetic-fixture",
            "observed_at": AVAILABLE_AT,
            "cash": "100.00",
            "equity": "100.00",
            "pending_buy_notional": "0",
            "positions": [],
        },
        "evidence": evidence,
    }

    assert validate_bundle(bundle) is bundle
