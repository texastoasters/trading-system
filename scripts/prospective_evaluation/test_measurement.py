"""Synthetic measurement fixtures; never actual market observations."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from prospective_evaluation.measurement import (
    MeasurementError,
    baseline_allocations,
    score_checkpoint,
)


SYMBOLS = ["SPY", "QQQ", "IWM", "AAPL"]


def protocol() -> dict[str, Any]:
    request = {
        "schema_version": 1,
        "campaign_id": "synthetic-measurement",
        "universe": [
            {"symbol": symbol, "asset_class": "etf" if symbol != "AAPL" else "equity"}
            for symbol in SYMBOLS
        ],
        "model": "synthetic-model",
        "research_cash": "100",
        "max_checkpoints": 10,
        "horizons": [1, 5],
        "arms": ["cash", "equal_weight", "sma20", "ai_three_role"],
    }
    from prospective_evaluation.protocol import prepare_protocol
    return prepare_protocol({key: request[key] for key in (
        "schema_version", "campaign_id", "universe", "model", "research_cash", "max_checkpoints"
    )})


def bar_inputs() -> dict[str, Any]:
    values: dict[str, Any] = {}
    final_closes = {"SPY": "30", "QQQ": "20", "IWM": "19", "AAPL": "20.0001"}
    for symbol in SYMBOLS:
        bars = [
            {
                "session_date": f"2026-01-{index + 1:02d}",
                "session_close_at": f"2026-01-{index + 1:02d}T21:00:00+00:00",
                "open": "20",
                "high": "31",
                "low": "18",
                "close": "20",
                "volume": "100",
            }
            for index in range(21)
        ]
        bars[-1]["close"] = final_closes[symbol]
        values[symbol] = {
            "bars": bars,
            "available_at": "2026-01-22T01:02:00Z",
            "source": "SYNTHETIC fixture only",
        }
    return values


def test_baselines_are_long_only_matched_controls_using_last_20_closes():
    result = baseline_allocations(protocol(), bar_inputs())

    assert result == {
        "cash": {
            "status": "accepted",
            "targets": [
                {"symbol": "SPY", "weight": "0"},
                {"symbol": "QQQ", "weight": "0"},
                {"symbol": "IWM", "weight": "0"},
                {"symbol": "AAPL", "weight": "0"},
            ],
            "cash_weight": "1",
        },
        "equal_weight": {
            "status": "accepted",
            "targets": [
                {"symbol": "SPY", "weight": "0.25"},
                {"symbol": "QQQ", "weight": "0.25"},
                {"symbol": "IWM", "weight": "0.25"},
                {"symbol": "AAPL", "weight": "0.25"},
            ],
            "cash_weight": "0",
        },
        "sma20": {
            "status": "accepted",
            "targets": [
                {"symbol": "SPY", "weight": "0.25"},
                {"symbol": "QQQ", "weight": "0"},
                {"symbol": "IWM", "weight": "0"},
                {"symbol": "AAPL", "weight": "0.25"},
            ],
            "cash_weight": "0.5",
        },
    }


def test_equal_weight_rounds_down_to_12_places_and_leaves_residual_cash():
    value = protocol()
    value["universe"] = value["universe"][:3]
    bars = {symbol: bar_inputs()[symbol] for symbol in SYMBOLS[:3]}

    result = baseline_allocations(value, bars)

    assert result["equal_weight"] == {
        "status": "accepted",
        "targets": [
            {"symbol": "SPY", "weight": "0.333333333333"},
            {"symbol": "QQQ", "weight": "0.333333333333"},
            {"symbol": "IWM", "weight": "0.333333333333"},
        ],
        "cash_weight": "0.000000000001",
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p, b: b.pop("SPY"),
        lambda p, b: b.update(SPY=[]),
        lambda p, b: b["SPY"]["bars"].pop(),
        lambda p, b: b["SPY"]["bars"][-1].update(close="NaN"),
        lambda p, b: p["universe"].append({"symbol": "SPY", "asset_class": "etf"}),
        lambda p, b: p.update(universe="SPY"),
    ],
)
def test_baseline_malformed_inputs_are_public_measurement_errors(mutate):
    frozen, bars = protocol(), bar_inputs()
    mutate(frozen, bars)
    with pytest.raises(MeasurementError, match="^invalid_measurement_input$"):
        baseline_allocations(frozen, bars)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p, b: b["SPY"].update(available_at=1),
        lambda p, b: b["SPY"]["bars"][-1].update(close=1),
        lambda p, b: b["SPY"]["bars"][-1].update(close="not-decimal"),
    ],
)
def test_baseline_rejects_malformed_public_bar_fields(mutate):
    frozen, bars = protocol(), bar_inputs()
    mutate(frozen, bars)
    with pytest.raises(MeasurementError, match="^invalid_measurement_input$"):
        baseline_allocations(frozen, bars)


def test_baseline_does_not_mutate_inputs():
    frozen, bars = protocol(), bar_inputs()
    expected_protocol, expected_bars = deepcopy(frozen), deepcopy(bars)
    baseline_allocations(frozen, bars)
    assert frozen == expected_protocol
    assert bars == expected_bars


# Scoring fixtures below deliberately use the public raw-observation schema.
def test_archive_latency_does_not_move_the_bundle_freshness_cutoff():
    value = checkpoint()
    value["bundle_as_of"] = "2026-01-22T01:02:00Z"
    value["inputs_frozen_at"] = "2026-01-22T01:02:00.250000Z"
    result = score_checkpoint(protocol(), value, [])
    assert result["checkpoint_status"] == "eligible"
    assert result["counts"] == {"scored": 0, "pending": 2, "unavailable": 0, "economically_scorable": 0}


def checkpoint() -> dict[str, Any]:
    controls = baseline_allocations(protocol(), bar_inputs())
    return {
        "schema_version": 1,
        "slot_date": "2026-01-21",
        "collection_id": "synthetic-collection",
        "bundle_hash": "a" * 64,
        "collection_path": "artifacts/collections/synthetic-collection",
        "decision_path": "artifacts/decisions/synthetic-collection",
        "source_session_close_at": "2026-01-22T01:00:00Z",
        "decision_deadline": "2026-01-22T01:07:00Z",
        "bundle_as_of": "2026-01-22T01:02:00Z",
        "inputs_frozen_at": "2026-01-22T01:02:00Z",
        "frozen_at": "2026-01-22T01:04:00Z",
        "outcome_sessions": [
            {
                "date": f"2026-01-{day:02d}",
                "interval_start": f"2026-01-{day:02d}T05:00:00Z",
                "session_close_at": f"2026-01-{day:02d}T21:00:00Z",
            }
            for day in range(22, 27)
        ],
        "arms": {
            **controls,
            "ai_three_role": {
                "status": "accepted",
                "reason": None,
                "targets": [
                    {"symbol": "SPY", "weight": "0.5"},
                    {"symbol": "QQQ", "weight": "0.5"},
                    {"symbol": "IWM", "weight": "0"},
                    {"symbol": "AAPL", "weight": "0"},
                ],
                "cash_weight": "0",
            },
        },
        "model_receipts": [],
        "executable": False,
    }


def observation(*, completed_at: str, closes: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Build a labelled synthetic raw capture accepted by observations.py."""
    sessions = [
        {
            "date": "2026-01-21",
            "open": "09:30",
            "close": "16:00",
            "session_open": "0400",
            "session_close": "2000",
            "settlement_date": "2026-01-22",
        },
        *[
            {
                "date": f"2026-01-{day:02d}",
                "open": "09:30",
                "close": "16:00",
                "session_open": "0400",
                "session_close": "1600",
                "settlement_date": f"2026-01-{day + 1:02d}",
            }
            for day in range(22, 27)
        ],
    ]
    bars: dict[str, list[dict[str, Any]]] = {}
    for symbol, by_day in closes.items():
        bars[symbol] = [
            {
                "t": f"{day}T05:00:00Z",
                "o": "100" if symbol != "QQQ" else "200",
                "h": "300",
                "l": "50",
                "c": close,
                "v": 100,
            }
            for day, close in by_day.items()
        ]

    received_at = completed_at
    request_started_at = completed_at
    receipt_fields = {
        "request_started_at": request_started_at,
        "received_at": received_at,
        "body_sha256": "0" * 64,
        "rate_limit": {},
    }
    return {
        "schema_version": 1,
        "symbols": SYMBOLS,
        "started_at": completed_at,
        "completed_at": completed_at,
        "start_date": "2026-01-21",
        "end_date": "2026-02-20",
        "policy": {
            "feed": "iex",
            "adjustment": "raw",
            "asof": "-",
            "currency": "USD",
            "timeframe": "1Day",
        },
        "sources": {
            "sessions": {
                "complete": True,
                "error": None,
                "pages": [
                    {
                        "ordinal": 1,
                        "query": {"start": "2026-01-21", "end": "2026-02-20", "date_type": "TRADING"},
                        **receipt_fields,
                        "payload": sessions,
                    }
                ],
            },
            "bars": {
                "complete": True,
                "error": None,
                "pages": [
                    {
                        "ordinal": 1,
                        "query": {
                            "symbols": ",".join(SYMBOLS),
                            "start": "2026-01-21T00:00:00-05:00",
                            "end": completed_at,
                            "feed": "iex",
                            "adjustment": "raw",
                            "asof": "-",
                            "currency": "USD",
                            "timeframe": "1Day",
                            "sort": "asc",
                            "limit": 10000,
                        },
                        **receipt_fields,
                        "payload": {"bars": bars, "next_page_token": None},
                    }
                ],
            },
        },
    }


def complete_prices(*, h1_spy: str = "110", h5_spy: str = "120") -> dict[str, dict[str, str]]:
    return {
        "SPY": {"2026-01-22": h1_spy, "2026-01-26": h5_spy},
        "QQQ": {"2026-01-22": "180", "2026-01-26": "220"},
        "IWM": {"2026-01-22": "100", "2026-01-26": "100"},
        "AAPL": {"2026-01-22": "100", "2026-01-26": "100"},
    }


def test_baseline_arithmetic_does_not_inherit_caller_exponent_limits():
    from decimal import localcontext

    expected = baseline_allocations(protocol(), bar_inputs())
    with localcontext() as context:
        context.Emax = 1
        assert baseline_allocations(protocol(), bar_inputs()) == expected


def test_measurement_owns_decimal_context_instead_of_inheriting_caller_precision():
    from decimal import localcontext

    captured = observation(completed_at="2026-01-26T22:00:00Z", closes=complete_prices())
    captured["sources"]["bars"]["pages"][0]["payload"]["bars"]["SPY"][0]["o"] = "99"
    with localcontext() as context:
        context.prec = 12
        low_precision = score_checkpoint(protocol(), checkpoint(), [captured])
    with localcontext() as context:
        context.prec = 50
        high_precision = score_checkpoint(protocol(), checkpoint(), [captured])
    assert low_precision == high_precision


def test_old_calendar_frames_before_checkpoint_cannot_poison_later_cohort():
    earlier = observation(completed_at="2026-01-22T01:01:00Z", closes={})
    earlier["end_date"] = "2026-01-21"
    calendar = earlier["sources"]["sessions"]["pages"][0]
    calendar["query"]["end"] = "2026-01-21"
    calendar["payload"] = calendar["payload"][:1]
    later = observation(completed_at="2026-01-26T22:00:00Z", closes=complete_prices())
    result = score_checkpoint(protocol(), checkpoint(), [earlier, later])
    assert result["counts"] == {"scored": 2, "pending": 0, "unavailable": 0, "economically_scorable": 0}
    assert result["horizons"][0]["first_observed"]["raw_response"]["symbols"][0]["response"] == "0.1"


def test_scoring_uses_future_raw_prices_and_fixed_first_anchor_across_revisions():
    first = observation(completed_at="2026-01-26T22:00:00Z", closes=complete_prices())
    revised = observation(
        completed_at="2026-01-27T22:00:00Z",
        closes=complete_prices(h1_spy="115", h5_spy="130"),
    )

    result = score_checkpoint(protocol(), checkpoint(), [revised, first])

    assert result["schema_version"] == 1
    assert result["slot_date"] == "2026-01-21"
    assert result["executable"] is False
    assert result["economic_claim"] is None
    assert result["counts"] == {"scored": 2, "pending": 0, "unavailable": 0, "economically_scorable": 0}
    h1, h5 = result["horizons"]
    assert (h1["horizon"], h1["status"], h5["horizon"], h5["status"]) == (1, "scored", 5, "scored")
    assert h1["first_observed"]["observation_completed_at"] == "2026-01-26T22:00:00+00:00"
    assert h1["latest_observed"]["observation_completed_at"] == "2026-01-27T22:00:00+00:00"
    assert (h1["latest_status"], h1["latest_reason"]) == ("scored", None)
    assert h1["latest_observation_completed_at"] == "2026-01-27T22:00:00+00:00"
    assert h1["first_observed"]["raw_response"]["symbols"][0] == {
        "symbol": "SPY",
        "entry_price": "100",
        "exit_price": "110",
        "response": "0.1",
        "entry_available_at": "2026-01-26T22:00:00+00:00",
        "exit_available_at": "2026-01-26T22:00:00+00:00",
        "source_published_at": None,
    }
    assert h1["latest_observed"]["raw_response"]["symbols"][0]["response"] == "0.15"
    assert h5["latest_observed"]["raw_response"]["symbols"][0] == {
        "symbol": "SPY",
        "entry_price": "100",
        "exit_price": "130",
        "response": "0.3",
        "entry_available_at": "2026-01-26T22:00:00+00:00",
        "exit_available_at": "2026-01-27T22:00:00+00:00",
        "source_published_at": None,
    }
    ai = h5["latest_observed"]["raw_response"]["arms"][-1]
    assert ai == {"arm": "ai_three_role", "arm_status": "accepted", "status": "scored", "response": "0.2"}
    assert h5["latest_observed"]["raw_response"]["paired_baseline_differences"] == [
        {"baseline": "cash", "status": "scored", "difference": "0.2"},
        {"baseline": "equal_weight", "status": "scored", "difference": "0.1"},
        {"baseline": "sma20", "status": "scored", "difference": "0.125"},
    ]
    for horizon in result["horizons"]:
        for version in ("first_observed", "latest_observed"):
            assert horizon[version]["raw_response"]["kind"] == "price_only"
            assert horizon[version]["raw_response"]["corporate_action_status"] == "unchecked"
            assert horizon[version]["raw_response"]["economically_scorable"] is False


def test_each_symbol_anchor_is_its_original_first_observed_entry():
    early_prices = complete_prices()
    del early_prices["AAPL"]["2026-01-22"]
    early = observation(completed_at="2026-01-26T21:30:00Z", closes=early_prices)
    later = observation(completed_at="2026-01-26T22:00:00Z", closes=complete_prices())
    for item in later["sources"]["bars"]["pages"][0]["payload"]["bars"]["SPY"]:
        if item["t"].startswith("2026-01-22"):
            item["o"] = "150"

    result = score_checkpoint(protocol(), checkpoint(), [early, later])

    symbols = result["horizons"][1]["latest_observed"]["raw_response"]["symbols"]
    assert symbols[0]["entry_price"] == "100"
    assert symbols[-1]["entry_price"] == "100"


def test_outcome_pending_before_maturity_then_unavailable_when_mature_data_missing():
    sparse = complete_prices()
    del sparse["AAPL"]["2026-01-26"]
    before_h5_close = observation(completed_at="2026-01-26T20:59:59Z", closes=sparse)
    result = score_checkpoint(protocol(), checkpoint(), [before_h5_close])
    assert [item["status"] for item in result["horizons"]] == ["scored", "pending"]
    assert result["horizons"][1]["reason"] == "awaiting_maturity"

    mature = observation(completed_at="2026-01-26T22:00:00Z", closes=sparse)
    result = score_checkpoint(protocol(), checkpoint(), [mature])
    assert [item["status"] for item in result["horizons"]] == ["scored", "unavailable"]
    assert result["horizons"][1]["reason"] == "mature_data_missing"
    assert result["horizons"][1]["first_observed"] is None
    assert result["horizons"][1]["latest_observed"] is None


def test_newer_mature_missing_capture_is_explicit_without_rewriting_first_score():
    complete = observation(completed_at="2026-01-26T22:00:00Z", closes=complete_prices())
    sparse_prices = complete_prices(h5_spy="130")
    del sparse_prices["AAPL"]["2026-01-26"]
    sparse = observation(completed_at="2026-01-27T22:00:00Z", closes=sparse_prices)

    result = score_checkpoint(protocol(), checkpoint(), [sparse, complete])
    horizon = result["horizons"][1]

    assert (horizon["status"], horizon["reason"]) == ("scored", None)
    assert horizon["first_observed"]["observation_completed_at"] == "2026-01-26T22:00:00+00:00"
    assert horizon["first_observed"]["raw_response"]["symbols"][0]["exit_price"] == "120"
    assert horizon["latest_observed"] is None
    assert (horizon["latest_status"], horizon["latest_reason"]) == ("unavailable", "mature_data_missing")
    assert horizon["latest_observation_completed_at"] == "2026-01-27T22:00:00+00:00"


def test_exact_duplicate_completion_is_deduplicated_but_distinct_ties_fail_closed():
    first = observation(completed_at="2026-01-26T22:00:00Z", closes=complete_prices())
    duplicate = deepcopy(first)
    assert score_checkpoint(protocol(), checkpoint(), [first, duplicate]) == score_checkpoint(
        protocol(), checkpoint(), [duplicate, first]
    )

    distinct = observation(
        completed_at="2026-01-26T22:00:00Z",
        closes=complete_prices(h5_spy="130"),
    )
    for captures in ([first, distinct], [distinct, first]):
        with pytest.raises(MeasurementError, match="^invalid_measurement_input$"):
            score_checkpoint(protocol(), checkpoint(), captures)


def test_failed_ai_status_is_preserved_and_never_converted_to_cash():
    value = checkpoint()
    value["arms"]["ai_three_role"] = {
        "status": "blocked",
        "reason": "synthetic_critic_block",
        "targets": None,
        "cash_weight": None,
    }
    capture = observation(completed_at="2026-01-26T22:00:00Z", closes=complete_prices())

    result = score_checkpoint(protocol(), value, [capture])

    raw = result["horizons"][1]["latest_observed"]["raw_response"]
    assert raw["arms"][-1] == {
        "arm": "ai_three_role",
        "arm_status": "blocked",
        "status": "blocked",
        "response": None,
    }
    assert raw["paired_baseline_differences"] == [
        {"baseline": "cash", "status": "blocked", "difference": None},
        {"baseline": "equal_weight", "status": "blocked", "difference": None},
        {"baseline": "sma20", "status": "blocked", "difference": None},
    ]


def test_late_checkpoint_remains_visible_but_is_unscorable():
    item = checkpoint()
    item["frozen_at"] = item["decision_deadline"]
    capture = observation(completed_at="2026-01-26T22:00:00Z", closes=complete_prices())

    result = score_checkpoint(protocol(), item, [capture])

    assert result["checkpoint_status"] == "late"
    assert result["counts"] == {"scored": 0, "pending": 0, "unavailable": 2, "economically_scorable": 0}
    assert [(value["status"], value["reason"]) for value in result["horizons"]] == [
        ("unavailable", "checkpoint_late"),
        ("unavailable", "checkpoint_late"),
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("inputs_frozen_at", "2026-01-22T01:00:00Z"),
        ("decision_deadline", "2026-01-22T01:06:59.999999Z"),
        ("decision_deadline", "2026-01-22T05:00:00Z"),
    ],
)
def test_checkpoint_malformed_chronology_fails_closed(field, value):
    item = checkpoint()
    item[field] = value
    capture = observation(completed_at="2026-01-26T22:00:00Z", closes=complete_prices())
    with pytest.raises(MeasurementError, match="^invalid_measurement_input$"):
        score_checkpoint(protocol(), item, [capture])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.update(frozen_at=1),
        lambda c: c.update(frozen_at="not-time"),
        lambda c: c.update(frozen_at="2026-01-22T01:04:00"),
        lambda c: c.update(outcome_sessions=[]),
        lambda c: c["outcome_sessions"][0].update(date="bad-date"),
        lambda c: c["outcome_sessions"][1].update(
            date=c["outcome_sessions"][0]["date"],
            interval_start=c["outcome_sessions"][0]["interval_start"],
        ),
        lambda c: c.update(schema_version=2),
        lambda c: c.update(collection_id=""),
        lambda c: c.update(decision_path=1),
        lambda c: c.update(model_receipts={}),
        lambda c: c.update(arms={}),
        lambda c: c["arms"]["ai_three_role"].update(status="unknown"),
        lambda c: c["arms"]["ai_three_role"].update(status="failed", targets=[]),
        lambda c: c["arms"]["ai_three_role"].update(targets=[]),
        lambda c: c["arms"]["ai_three_role"]["targets"][0].update(symbol="NOPE"),
        lambda c: c["arms"]["ai_three_role"]["targets"][0].update(weight="0.4"),
    ],
)
def test_malformed_checkpoint_shapes_and_arm_vectors_fail_closed(mutate):
    item = checkpoint()
    mutate(item)
    capture = observation(completed_at="2026-01-26T22:00:00Z", closes=complete_prices())
    with pytest.raises(MeasurementError, match="^invalid_measurement_input$"):
        score_checkpoint(protocol(), item, [capture])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.update(extra_future_price="999"),
        lambda c: c.update(executable=True),
        lambda c: c.update(executable=0),
        lambda c: c.update(bundle_hash="A" * 64),
        lambda c: c.update(bundle_hash="a" * 63),
        lambda c: c.update(slot_date=1),
        lambda c: c.update(slot_date="2026-99-99"),
        lambda c: c.update(collection_id="../escape"),
        lambda c: c.update(collection_id="CON"),
        lambda c: c.update(collection_id="unsafe."),
        lambda c: c.update(collection_path="artifacts/collections/other"),
        lambda c: c.update(collection_path="/artifacts/collections/synthetic-collection"),
        lambda c: c.update(decision_path="artifacts/decisions/other"),
        lambda c: c["arms"]["cash"].update(extra_future_price="999"),
        lambda c: c["arms"]["cash"]["targets"][0].update(extra_future_price="999"),
        lambda c: c["arms"]["ai_three_role"].update(reason=[]),
        lambda c: c["arms"]["cash"].update(reason="not-a-baseline-field"),
        lambda c: c["arms"]["ai_three_role"].pop("reason"),
        lambda c: c["arms"]["cash"].update(status=[]),
        lambda c: c["arms"]["cash"]["targets"][0].update(symbol=[]),
    ],
)
def test_checkpoint_arm_and_target_public_schemas_are_exact(mutate):
    item = checkpoint()
    mutate(item)
    with pytest.raises(MeasurementError, match="^invalid_measurement_input$"):
        score_checkpoint(protocol(), item, [])


def test_mature_missing_entry_is_unavailable_without_fake_cash_or_zero():
    prices = complete_prices()
    del prices["AAPL"]["2026-01-22"]
    capture = observation(completed_at="2026-01-26T22:00:00Z", closes=prices)

    result = score_checkpoint(protocol(), checkpoint(), [capture])

    assert [(item["status"], item["reason"]) for item in result["horizons"]] == [
        ("unavailable", "mature_data_missing"),
        ("unavailable", "mature_data_missing"),
    ]


def test_calendar_revision_target_mismatch_is_unavailable_not_shifted():
    capture = observation(completed_at="2026-01-26T22:00:00Z", closes=complete_prices())
    capture["sources"]["sessions"]["pages"][0]["payload"][2]["date"] = "2026-01-27"

    result = score_checkpoint(protocol(), checkpoint(), [capture])

    assert result["horizons"][0]["status"] == "unavailable"
    assert result["horizons"][0]["reason"] == "calendar_target_mismatch"
    assert result["horizons"][1]["status"] == "unavailable"
    assert result["horizons"][1]["reason"] == "calendar_target_mismatch"


def test_observation_failures_and_malformed_public_inputs_are_measurement_errors():
    capture = observation(completed_at="2026-01-26T22:00:00Z", closes=complete_prices())
    capture["sources"]["bars"]["complete"] = False
    capture["sources"]["bars"]["error"] = {"type": "synthetic_failure"}
    with pytest.raises(MeasurementError, match="^invalid_measurement_input$"):
        score_checkpoint(protocol(), checkpoint(), [capture])

    malformed: Any = "not-an-observation-list"
    with pytest.raises(MeasurementError, match="^invalid_measurement_input$"):
        score_checkpoint(protocol(), checkpoint(), malformed)

    with pytest.raises(MeasurementError, match="^invalid_measurement_input$"):
        score_checkpoint({"schema_version": 1}, checkpoint(), [])


def test_score_does_not_mutate_inputs():
    frozen, item = protocol(), checkpoint()
    captures = [observation(completed_at="2026-01-26T22:00:00Z", closes=complete_prices())]
    expected = deepcopy((frozen, item, captures))
    score_checkpoint(frozen, item, captures)
    assert (frozen, item, captures) == expected
