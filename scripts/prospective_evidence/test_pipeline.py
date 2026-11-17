"""Synthetic end-to-end fixtures; no source or model networking occurs here."""
from __future__ import annotations

import copy
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from prospective_evidence import archive
from prospective_evidence import pipeline


START = "2026-01-22T01:00:00+00:00"
COMPLETE = "2026-01-22T01:05:00+00:00"


@pytest.mark.parametrize("value", [[], {}, None, False])
def test_malformed_asset_class_is_a_public_validation_failure(value):
    declared = experiment()
    declared["universe"][0]["asset_class"] = value
    with pytest.raises(pipeline.PipelineError, match="invalid_experiment"):
        pipeline.build_collection(synthetic_capture(), declared)


@pytest.mark.parametrize("field", ["schema_version", "ordinal"])
def test_boolean_cannot_impersonate_capture_integer(field):
    capture = synthetic_capture()
    if field == "schema_version":
        capture[field] = True
    else:
        capture["sources"]["bars"]["pages"][0][field] = True
    with pytest.raises(pipeline.PipelineError, match="capture_incomplete"):
        pipeline.build_collection(capture, experiment())


def test_event_elapsed_between_receipt_and_collection_completion_is_not_upcoming():
    capture = synthetic_capture()
    page = capture["sources"]["bea_calendar"]["pages"][0]
    page["payload"] = page["payload"].replace("20260123T133000Z", "20260122T010400Z")
    result = pipeline.build_collection(capture, experiment())
    assert not any(item["kind"] == "scheduled_event" for item in result["bundle"]["evidence"])
    coverage = result["capture"]["derived"]["coverage"]["calendar"]
    assert coverage["excluded_elapsed_during_collection_count"] == 1
    assert coverage["excluded_beyond_horizon_count"] == 0


def _page(payload, query, ordinal=1, *, request="2026-01-22T01:01:00+00:00", receipt="2026-01-22T01:02:00+00:00"):
    return {
        "query": query,
        "ordinal": ordinal,
        "payload": payload,
        "request_started_at": request,
        "received_at": receipt,
        "body_sha256": f"{ordinal:064x}",
        "rate_limit": {},
    }


def synthetic_capture(symbols=None):
    symbols = symbols or ["SPY"]
    sessions = []
    bars = {symbol: [] for symbol in symbols}
    first = date(2026, 1, 1)
    for index in range(21):
        day = (first + timedelta(days=index)).isoformat()
        sessions.append({
            "date": day,
            "open": "09:30",
            "close": "16:00",
            "session_open": "0930",
            "session_close": "2000",
            "settlement_date": day,
        })
        for offset, symbol in enumerate(symbols):
            bars[symbol].append({
                "t": day + "T05:00:00Z",
                "o": str(100 + offset),
                "h": str(102 + offset),
                "l": str(99 + offset),
                "c": str(101 + offset),
                "v": 1000 + index,
            })
    joined = ",".join(symbols)
    window = "2025-09-23"
    bar_query = {
        "symbols": joined,
        "timeframe": "1Day",
        "start": window,
        "end": START,
        "feed": "iex",
        "adjustment": "raw",
        "asof": "-",
        "currency": "USD",
        "sort": "asc",
        "limit": 10000,
    }
    news_query = {
        "symbols": joined,
        "start": "2026-01-19T01:00:00+00:00",
        "end": START,
        "sort": "asc",
        "limit": 50,
        "include_content": "false",
        "exclude_contentless": "false",
    }
    calendar = "\r\n".join([
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//SYNTHETIC FIXTURE//EN",
        "BEGIN:VEVENT",
        "UID:fixture-release",
        "SUMMARY:SYNTHETIC fixture release",
        "DTSTART:20260123T133000Z",
        "DTSTAMP:20260101T120000Z",
        "SEQUENCE:0",
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ])
    return {
        "schema_version": 1,
        "started_at": START,
        "completed_at": COMPLETE,
        "symbols": list(symbols),
        "policy": {
            "feed": "iex",
            "adjustment": "raw",
            "asof": "-",
            "currency": "USD",
            "bar_completion": "extended_session_close",
            "news_metadata_only": True,
        },
        "sources": {
            "sessions": {"pages": [_page(sessions, {
                "start": window, "end": "2026-01-21", "date_type": "TRADING"
            })], "complete": True, "error": None},
            "bars": {"pages": [_page({"bars": bars, "next_page_token": None}, bar_query)], "complete": True, "error": None},
            "news": {"pages": [_page({"news": [], "next_page_token": None}, news_query)], "complete": True, "error": None},
            "bea_calendar": {"pages": [_page(calendar, {})], "complete": True, "error": None},
        },
    }


def experiment(symbols=None):
    symbols = symbols or ["SPY"]
    return {
        "collection_id": "synthetic-collection",
        "book_id": "synthetic-book",
        "universe": [{"symbol": symbol, "asset_class": "etf"} for symbol in symbols],
        "research_cash": "10000.00",
        "ttl_seconds": 900,
    }


def test_build_collection_integrates_all_observed_and_derived_evidence_deterministically():
    capture = synthetic_capture(["SPY", "QQQ"])
    declared = experiment(["SPY", "QQQ"])

    first = pipeline.build_collection(capture, declared)
    second = pipeline.build_collection(copy.deepcopy(capture), copy.deepcopy(declared))

    assert first == second
    bundle = first["bundle"]
    assert bundle["run_id"] == "synthetic-collection"
    assert bundle["mode"] == "shadow"
    assert bundle["as_of"] == COMPLETE
    assert bundle["mandate_version"] == "prospective-evidence-v1"
    assert bundle["max_calls"] == 3
    assert bundle["max_snapshot_age_seconds"] == 300
    assert [item["kind"] for item in bundle["evidence"]] == [
        "account", "technical", "algorithmic", "technical", "algorithmic", "scheduled_event"
    ]
    assert json.loads(bundle["evidence"][0]["content"])["broker_observation"] is False
    assert bundle["evidence"][-1]["event_at"] == "2026-01-23T13:30:00+00:00"
    envelope = first["capture"]
    assert envelope["schema_version"] == 1
    assert envelope["recipe_version"] == "prospective-evidence-v1"
    assert envelope["experiment"] == declared
    assert envelope["derived"]["coverage"]["news"] == {
        "status": "observed_empty",
        "record_count": 0,
        "caveat": "No matching news metadata was observed in the bounded query; this does not prove no relevant news exists.",
    }


def test_default_ttl_and_news_metadata_are_preserved_without_article_invention():
    capture = synthetic_capture()
    capture["sources"]["news"]["pages"][0]["payload"]["news"] = [{
        "id": 7,
        "headline": "SYNTHETIC metadata-only fixture",
        "created_at": "2026-01-21T12:00:00Z",
        "updated_at": "2026-01-21T13:00:00Z",
        "url": None,
        "symbols": ["SPY"],
        "source": "fixture-wire",
    }]
    declared = experiment()
    del declared["ttl_seconds"]

    result = pipeline.build_collection(capture, declared)

    assert result["capture"]["experiment"]["ttl_seconds"] == 900
    assert result["bundle"]["expires_at"] == "2026-01-22T01:20:00+00:00"
    news = next(item for item in result["bundle"]["evidence"] if item["kind"] == "news")
    assert "content" not in json.loads(news["content"])
    assert result["capture"]["derived"]["coverage"]["news"]["status"] == "observed_records"


def test_explicit_previous_calendar_events_drive_saved_offline_diff():
    declared = experiment()
    declared["previous_events"] = [{
        "uid": "removed-fixture",
        "summary": "SYNTHETIC removed event",
        "event_at": "2026-01-24T13:30:00+00:00",
        "dtstamp": None,
        "sequence": None,
        "duration": None,
    }]

    result = pipeline.build_collection(synthetic_capture(), declared)

    changes = result["capture"]["derived"]["calendar_changes"]
    assert changes == {"added": ["fixture-release"], "changed": [], "removed": ["removed-fixture"]}
    assert result["capture"]["experiment"]["previous_events"] == declared["previous_events"]


def test_calendar_evidence_uses_fixed_30_day_horizon_but_retains_full_snapshot():
    capture = synthetic_capture()
    calendar = capture["sources"]["bea_calendar"]["pages"][0]["payload"]
    extra = "\r\n".join([
        "BEGIN:VEVENT", "UID:far-fixture", "SUMMARY:SYNTHETIC far release",
        "DTSTART:20260301T133000Z", "DTSTAMP:20260101T120000Z", "SEQUENCE:0", "END:VEVENT", "",
    ])
    capture["sources"]["bea_calendar"]["pages"][0]["payload"] = calendar.replace(
        "END:VCALENDAR", extra + "END:VCALENDAR"
    )

    result = pipeline.build_collection(capture, experiment())

    scheduled = [item for item in result["bundle"]["evidence"] if item["kind"] == "scheduled_event"]
    assert [item["event_at"] for item in scheduled] == ["2026-01-23T13:30:00+00:00"]
    coverage = result["capture"]["derived"]["coverage"]["calendar"]
    assert coverage == {
        "policy": "include events strictly after collection completion through the fixed 30-calendar-day horizon; future_event_count is measured at calendar receipt",
        "horizon_days": 30,
        "horizon_at": "2026-02-21T01:05:00+00:00",
        "normalized_snapshot_event_count": 2,
        "future_event_count": 2,
        "included_evidence_count": 1,
        "excluded_beyond_horizon_count": 1,
        "excluded_elapsed_during_collection_count": 0,
    }
    assert [item["uid"] for item in result["capture"]["derived"]["normalized_calendar_events"]] == [
        "fixture-release", "far-fixture"
    ]


def test_caller_portfolio_keeps_observation_time_and_must_be_fresh():
    declared = experiment()
    del declared["research_cash"]
    declared["portfolio"] = {
        "snapshot_id": "caller-fixture",
        "observed_at": "2026-01-22T01:04:00+00:00",
        "cash": "10000.00",
        "equity": "10000.00",
        "pending_buy_notional": "0",
        "positions": [],
    }
    result = pipeline.build_collection(synthetic_capture(), declared)
    assert result["bundle"]["portfolio"]["observed_at"] == "2026-01-22T01:04:00+00:00"
    assert "caller_supplied" in json.loads(result["bundle"]["evidence"][0]["content"])["origin"]

    declared["portfolio"]["observed_at"] = "2026-01-22T00:59:59+00:00"
    with pytest.raises(pipeline.PipelineError, match="^invalid_experiment$"):
        pipeline.build_collection(synthetic_capture(), declared)


def test_caller_portfolio_positions_are_projected_and_reconciled():
    declared = experiment()
    del declared["research_cash"]
    declared["portfolio"] = {
        "snapshot_id": "caller-position-fixture",
        "observed_at": "2026-01-22T01:04:00+00:00",
        "cash": "10.00",
        "equity": "100.00",
        "pending_buy_notional": "0",
        "positions": [{"symbol": "SPY", "quantity": "1", "market_value": "90.00"}],
    }
    result = pipeline.build_collection(synthetic_capture(), declared)
    assert result["bundle"]["portfolio"]["positions"] == declared["portfolio"]["positions"]


@pytest.mark.parametrize("mutation", [
    "missing_source", "incomplete_source", "source_error", "clock_order", "ordinal", "page_chain",
    "nonterminal_last_page", "one_page_source", "query", "policy", "symbols", "universe_order",
    "capture_shape", "symbols_shape", "capture_clock", "page_shape", "receipt_shape", "body_hash",
    "rate_limit_shape", "rate_limit_value", "previous_page_shape", "payload_shape", "token_shape",
    "token_cycle", "bad_capture_time",
])
def test_incomplete_or_inconsistent_capture_fails_with_sanitized_public_error(mutation):
    capture = synthetic_capture(["SPY", "QQQ"])
    declared = experiment(["SPY", "QQQ"])
    if mutation == "missing_source":
        del capture["sources"]["bea_calendar"]
    elif mutation == "incomplete_source":
        capture["sources"]["bars"]["complete"] = False
    elif mutation == "source_error":
        capture["sources"]["news"]["error"] = {"code": "private upstream detail"}
    elif mutation == "clock_order":
        capture["sources"]["news"]["pages"][0]["received_at"] = "2026-01-22T01:06:00+00:00"
    elif mutation == "ordinal":
        capture["sources"]["bars"]["pages"][0]["ordinal"] = 2
    elif mutation == "page_chain":
        first = capture["sources"]["news"]["pages"][0]
        first["payload"]["next_page_token"] = "next"
        second = copy.deepcopy(first)
        second["ordinal"] = 2
        second["query"] = {**first["query"], "page_token": "wrong"}
        second["payload"] = {"news": [], "next_page_token": None}
        capture["sources"]["news"]["pages"].append(second)
    elif mutation == "nonterminal_last_page":
        capture["sources"]["bars"]["pages"][0]["payload"]["next_page_token"] = "missing-page"
    elif mutation == "one_page_source":
        capture["sources"]["sessions"]["pages"].append(copy.deepcopy(capture["sources"]["sessions"]["pages"][0]))
    elif mutation == "query":
        capture["sources"]["news"]["pages"][0]["query"]["limit"] = 51
    elif mutation == "policy":
        capture["policy"]["feed"] = "sip"
    elif mutation == "symbols":
        capture["symbols"].reverse()
    elif mutation == "universe_order":
        declared["universe"].reverse()
    elif mutation == "capture_shape":
        capture["schema_version"] = 2
    elif mutation == "symbols_shape":
        capture["symbols"] = []
    elif mutation == "capture_clock":
        capture["started_at"] = "2026-01-22T01:06:00+00:00"
    elif mutation == "page_shape":
        capture["sources"]["news"]["pages"][0] = []
    elif mutation == "receipt_shape":
        capture["sources"]["news"]["pages"][0]["private"] = "not projected"
    elif mutation == "body_hash":
        capture["sources"]["news"]["pages"][0]["body_sha256"] = "bad"
    elif mutation == "rate_limit_shape":
        capture["sources"]["news"]["pages"][0]["rate_limit"] = {"private": 1}
    elif mutation == "rate_limit_value":
        capture["sources"]["news"]["pages"][0]["rate_limit"] = {"limit": True}
    elif mutation == "previous_page_shape":
        first = capture["sources"]["news"]["pages"][0]
        second = copy.deepcopy(first)
        second["ordinal"] = 2
        first["payload"] = {"news": []}
        capture["sources"]["news"]["pages"].append(second)
    elif mutation == "payload_shape":
        capture["sources"]["news"]["pages"][0]["payload"] = []
    elif mutation == "token_shape":
        capture["sources"]["news"]["pages"][0]["payload"]["next_page_token"] = ""
    elif mutation == "token_cycle":
        first = capture["sources"]["news"]["pages"][0]
        first["payload"]["next_page_token"] = "same"
        second = copy.deepcopy(first)
        second["ordinal"] = 2
        second["query"] = {**first["query"], "page_token": "same"}
        second["payload"] = {"news": [], "next_page_token": "same"}
        capture["sources"]["news"]["pages"].append(second)
    else:
        capture["started_at"] = "not-a-time"
    with pytest.raises(pipeline.PipelineError, match="^invalid_experiment$" if mutation == "universe_order" else "^capture_incomplete$"):
        pipeline.build_collection(capture, declared)


@pytest.mark.parametrize("mutation", [
    "unknown", "bad_id", "bad_class", "both_books", "no_book", "bad_cash", "bad_cash_syntax",
    "bad_ttl", "bad_previous", "bad_previous_item", "not_object", "universe_length",
    "bad_portfolio", "bad_position",
])
def test_invalid_experiment_is_rejected_without_leaking_validation_details(mutation):
    declared = experiment()
    if mutation == "unknown":
        declared["private"] = "must not enter archive"
    elif mutation == "bad_id":
        declared["collection_id"] = "../escape"
    elif mutation == "bad_class":
        declared["universe"][0]["asset_class"] = "crypto"
    elif mutation == "both_books":
        declared["portfolio"] = {}
    elif mutation == "no_book":
        del declared["research_cash"]
    elif mutation == "bad_cash":
        declared["research_cash"] = "NaN"
    elif mutation == "bad_cash_syntax":
        declared["research_cash"] = "not-decimal"
    elif mutation == "bad_ttl":
        declared["ttl_seconds"] = True
    elif mutation == "bad_previous":
        declared["previous_events"] = {"unsupported": True}
    elif mutation == "bad_previous_item":
        declared["previous_events"] = [{}]
    elif mutation == "not_object":
        declared = []
    elif mutation == "universe_length":
        declared["universe"] = []
    else:
        del declared["research_cash"]
        declared["portfolio"] = {
            "snapshot_id": "fixture", "observed_at": COMPLETE, "cash": "100", "equity": "100",
            "pending_buy_notional": "0", "positions": [],
        }
        if mutation == "bad_portfolio":
            declared["portfolio"]["private"] = True
        else:
            declared["portfolio"]["positions"] = [{}]
    with pytest.raises(pipeline.PipelineError, match="^invalid_experiment$"):
        pipeline.build_collection(synthetic_capture(), declared)


def test_offline_reconstruction_recomputes_exact_bundle_and_marks_semantic_success(tmp_path):
    built = pipeline.build_collection(synthetic_capture(), experiment())
    path = archive.write_collection(tmp_path, built["bundle"], built["capture"])

    replay = pipeline.reconstruct_collection(path)

    assert replay["status"] == "complete"
    assert replay["bundle"] == built["bundle"]
    assert replay["manifest"]["collection_id"] == "synthetic-collection"
    assert replay["verification_kind"] == "semantic_reconstruction"


def test_semantic_reconstruction_detects_coordinated_rehash_of_changed_bundle(tmp_path):
    built = pipeline.build_collection(synthetic_capture(), experiment())
    path = archive.write_collection(tmp_path, built["bundle"], built["capture"])
    bundle_path = path / "bundle.json"
    bundle = json.loads(bundle_path.read_text())
    bundle["mandate_version"] = "tampered-but-valid"
    bundle_path.write_text(json.dumps(bundle))
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"]["bundle.json"] = archive.hash_file(bundle_path)
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(pipeline.PipelineError, match="^semantic_reconstruction_mismatch$"):
        pipeline.reconstruct_collection(path)


def test_failed_archive_never_upgrades_integrity_to_semantic_success(tmp_path):
    path = archive.write_failed_collection(
        tmp_path,
        "synthetic-failed",
        {"schema_version": 1, "source_capture": {}},
        error_code="capture_incomplete",
    )
    result = pipeline.reconstruct_collection(path)
    assert result["status"] == "failed"
    assert result["bundle"] is None
    assert result["verification_kind"] == "integrity_only"


def test_archive_integrity_and_malformed_source_errors_are_public_and_sanitized(tmp_path):
    built = pipeline.build_collection(synthetic_capture(), experiment())
    path = archive.write_collection(tmp_path, built["bundle"], built["capture"])
    (path / "capture.json").write_text("not-json")
    with pytest.raises(pipeline.PipelineError, match="^archive_verification_failed$"):
        pipeline.reconstruct_collection(path)

    malformed = synthetic_capture()
    malformed["sources"]["bea_calendar"]["pages"][0]["payload"] = "private malformed body"
    with pytest.raises(pipeline.PipelineError, match="^calendar_normalization_failed$"):
        pipeline.build_collection(malformed, experiment())

    malformed = synthetic_capture()
    malformed["sources"]["bars"]["pages"][0]["payload"]["bars"]["SPY"].pop()
    with pytest.raises(pipeline.PipelineError, match="^market_normalization_failed$"):
        pipeline.build_collection(malformed, experiment())


def test_declared_portfolio_bundle_validation_failure_is_sanitized():
    declared = experiment()
    del declared["research_cash"]
    declared["portfolio"] = {
        "snapshot_id": "fixture", "observed_at": COMPLETE, "cash": "10", "equity": "100",
        "pending_buy_notional": "0", "positions": [],
    }
    with pytest.raises(pipeline.PipelineError, match="^invalid_experiment$"):
        pipeline.build_collection(synthetic_capture(), declared)


@pytest.mark.parametrize("mutation,code", [
    ("envelope", "archive_verification_failed"),
    ("source", "semantic_reconstruction_failed"),
])
def test_reconstruction_rejects_rehashed_invalid_reconstruction_envelope(tmp_path, mutation, code):
    built = pipeline.build_collection(synthetic_capture(), experiment())
    path = archive.write_collection(tmp_path, built["bundle"], built["capture"])
    capture_path = path / "capture.json"
    envelope = json.loads(capture_path.read_text())
    if mutation == "envelope":
        envelope["recipe_version"] = "unsupported"
    else:
        envelope["source_capture"]["schema_version"] = 2
    capture_path.write_text(json.dumps(envelope))
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"]["capture.json"] = archive.hash_file(capture_path)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(pipeline.PipelineError, match=f"^{code}$"):
        pipeline.reconstruct_collection(path)
