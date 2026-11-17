from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from typing import Any

import pytest

from prospective_evidence.events import EventError, parse_bea_calendar


RETRIEVED_AT = "2026-11-16T20:00:00+00:00"


def test_source_summary_whitespace_is_retained_without_changing_identity_text():
    title = "Personal Income and Outlays, July 2026 "
    first = parse(synthetic_calendar(synthetic_event(summary=title)))
    assert first["events"][0]["summary"] == title
    assert json.loads(first["evidence"][0]["content"])["title"] == title
    repeated = parse(synthetic_calendar(synthetic_event(summary=title)), previous=first)
    assert repeated["changes"] == {"added": [], "changed": [], "removed": []}


@pytest.mark.parametrize("calendar_properties,event_metadata", [
    ("", "STATUS:CANCELLED\r\n"), ("METHOD:CANCEL\r\n", "")])
def test_cancelled_schedule_fails_closed(calendar_properties, event_metadata):
    with pytest.raises(EventError, match="cancel"):
        parse(synthetic_calendar(synthetic_event(metadata=event_metadata), calendar_properties=calendar_properties))
EXPIRES_AT = "2026-12-31T00:00:00+00:00"
BEA_SOURCE = "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics"


def synthetic_calendar(*events: str, calendar_properties: str = "") -> str:
    """Build an explicitly synthetic RFC 5545 calendar fixture."""
    extra = f"{calendar_properties.rstrip()}\r\n" if calendar_properties else ""
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Synthetic BEA schedule fixture//EN\r\n"
        f"{extra}"
        f"{''.join(events)}"
        "END:VCALENDAR\r\n"
    )


def synthetic_event(
    uid: str = "bea-gdp-20261125@example.test",
    summary: str = "Gross Domestic Product, 3rd Quarter 2026 (Second Estimate)",
    start: str = "20261125T133000Z",
    *,
    metadata: str = "DTSTAMP:20261101T120000Z\r\nSEQUENCE:2\r\nDURATION:PT1H\r\n",
) -> str:
    return (
        "BEGIN:VEVENT\r\n"
        f"DTSTART:{start}\r\n"
        f"SUMMARY:{summary}\r\n"
        f"UID:{uid}\r\n"
        f"{metadata}"
        "END:VEVENT\r\n"
    )


def parse(text: str, **overrides):
    arguments = {
        "retrieved_at": RETRIEVED_AT,
        "expires_at": EXPIRES_AT,
        "symbols": ["SPY", "QQQ"],
    }
    arguments.update(overrides)
    return parse_bea_calendar(text, **arguments)


def test_parse_bea_calendar_normalizes_authoritative_schedule_without_outcome_claims():
    result = parse(synthetic_calendar(synthetic_event()))

    assert result["events"] == [
        {
            "uid": "bea-gdp-20261125@example.test",
            "summary": "Gross Domestic Product, 3rd Quarter 2026 (Second Estimate)",
            "event_at": "2026-11-25T13:30:00+00:00",
            "dtstamp": "2026-11-01T12:00:00+00:00",
            "sequence": 2,
            "duration": "PT1H",
        }
    ]
    assert result["changes"] == {
        "added": ["bea-gdp-20261125@example.test"],
        "changed": [],
        "removed": [],
    }
    assert len(result["evidence"]) == 1
    evidence = result["evidence"][0]
    assert evidence == {
        "evidence_id": evidence["evidence_id"],
        "kind": "scheduled_event",
        "source": BEA_SOURCE,
        "published_at": RETRIEVED_AT,
        "available_at": RETRIEVED_AT,
        "expires_at": "2026-11-25T13:30:00+00:00",
        "symbols": ["SPY", "QQQ"],
        "event_at": "2026-11-25T13:30:00+00:00",
        "content": evidence["content"],
    }
    assert evidence["evidence_id"].startswith("scheduled-event-sha256-")
    content = json.loads(evidence["content"])
    assert content == {
        "claim": "scheduled release only; no outcome or forecast",
        "metadata": {
            "dtstamp": "2026-11-01T12:00:00+00:00",
            "duration": "PT1H",
            "sequence": 2,
            "source_publication_at": None,
            "source_publication_status": "unknown",
        },
        "title": "Gross Domestic Product, 3rd Quarter 2026 (Second Estimate)",
        "uid": "bea-gdp-20261125@example.test",
    }


def event_with_lines(*lines: str) -> str:
    return "BEGIN:VEVENT\r\n" + "\r\n".join(lines) + "\r\nEND:VEVENT\r\n"


def test_calendar_timezone_does_not_override_utc_start_and_text_is_unescaped_unfolded():
    event = (
        "BEGIN:VEVENT\r\n"
        "DTSTART;VALUE=DATE-TIME:20261125T133000Z\r\n"
        "SUMMARY:Personal Income\\, Outlays\\; and an intentionally long\r\n"
        " folded title\r\n"
        "UID:folded@example.test\r\n"
        "END:VEVENT\r\n"
    )

    result = parse(
        synthetic_calendar(event, calendar_properties="X-WR-TIMEZONE:America/New_York")
    )

    assert result["events"][0] == {
        "uid": "folded@example.test",
        "summary": "Personal Income, Outlays; and an intentionally longfolded title",
        "event_at": "2026-11-25T13:30:00+00:00",
        "dtstamp": None,
        "sequence": None,
        "duration": None,
    }


def test_same_time_distinct_releases_are_both_retained_in_source_order():
    first = synthetic_event(uid="first@example.test", summary="First release", metadata="")
    second = synthetic_event(uid="second@example.test", summary="Second release", metadata="")

    result = parse(synthetic_calendar(first, second))

    assert [event["uid"] for event in result["events"]] == [
        "first@example.test",
        "second@example.test",
    ]
    assert len(result["evidence"]) == 2


def test_past_events_remain_in_snapshot_but_do_not_become_scheduled_evidence():
    past = synthetic_event(
        uid="past@example.test", summary="Past release", start="20261116T195959Z", metadata=""
    )
    future = synthetic_event(uid="future@example.test", summary="Future release", metadata="")

    result = parse(synthetic_calendar(past, future))

    assert [event["uid"] for event in result["events"]] == [
        "past@example.test",
        "future@example.test",
    ]
    assert [json.loads(record["content"])["uid"] for record in result["evidence"]] == [
        "future@example.test"
    ]
    assert result["changes"]["added"] == ["future@example.test", "past@example.test"]


def test_source_dtstamp_is_metadata_not_source_publication_or_observation_time():
    result = parse(synthetic_calendar(synthetic_event()))
    evidence = result["evidence"][0]

    assert result["events"][0]["dtstamp"] == "2026-11-01T12:00:00+00:00"
    assert evidence["published_at"] == RETRIEVED_AT
    assert evidence["available_at"] == RETRIEVED_AT
    assert json.loads(evidence["content"])["metadata"]["source_publication_at"] is None


def test_expiry_is_capped_by_provided_expiry_when_it_precedes_event():
    result = parse(
        synthetic_calendar(synthetic_event()),
        expires_at="2026-11-20T00:00:00+00:00",
    )

    assert result["evidence"][0]["expires_at"] == "2026-11-20T00:00:00+00:00"


def test_full_snapshot_change_detection_reports_added_changed_and_removed_uids():
    previous = parse(
        synthetic_calendar(
            synthetic_event(uid="unchanged@example.test", summary="Unchanged", metadata=""),
            synthetic_event(uid="changed@example.test", summary="Old title", metadata="SEQUENCE:1\r\n"),
            synthetic_event(uid="removed@example.test", summary="Removed", metadata=""),
        )
    )["events"]
    current = synthetic_calendar(
        synthetic_event(uid="unchanged@example.test", summary="Unchanged", metadata=""),
        synthetic_event(uid="changed@example.test", summary="New title", metadata="SEQUENCE:2\r\n"),
        synthetic_event(uid="added@example.test", summary="Added", metadata=""),
    )

    result = parse(current, previous=previous)

    assert result["changes"] == {
        "added": ["added@example.test"],
        "changed": ["changed@example.test"],
        "removed": ["removed@example.test"],
    }


def test_identical_previous_result_has_no_changes_and_inputs_are_not_mutated():
    text = synthetic_calendar(synthetic_event())
    first = parse(text)
    previous = copy.deepcopy(first)
    symbols = ["SPY", "QQQ"]
    original_previous = copy.deepcopy(previous)

    repeated = parse(text, previous=previous, symbols=symbols)

    assert repeated["changes"] == {"added": [], "changed": [], "removed": []}
    assert previous == original_previous
    assert symbols == ["SPY", "QQQ"]


def test_evidence_ids_are_stable_and_change_with_uid_payload_or_schedule_not_observation_clock():
    text = synthetic_calendar(synthetic_event())
    first = parse(text)["evidence"][0]["evidence_id"]
    later_observation = parse(
        text,
        retrieved_at="2026-11-17T20:00:00+00:00",
        expires_at="2026-12-30T00:00:00+00:00",
    )["evidence"][0]["evidence_id"]
    changed_title = parse(
        synthetic_calendar(synthetic_event(summary="GDP changed title"))
    )["evidence"][0]["evidence_id"]
    changed_uid = parse(
        synthetic_calendar(synthetic_event(uid="other@example.test"))
    )["evidence"][0]["evidence_id"]
    changed_time = parse(
        synthetic_calendar(synthetic_event(start="20261125T143000Z"))
    )["evidence"][0]["evidence_id"]

    assert first == later_observation
    assert first != changed_title
    assert first != changed_uid
    assert first != changed_time


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not a calendar", "malformed|VCALENDAR"),
        ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n", "malformed"),
        (
            synthetic_calendar(synthetic_event(uid="", metadata="")),
            "uid",
        ),
        (
            synthetic_calendar(synthetic_event(summary="", metadata="")),
            "summary",
        ),
        (
            synthetic_calendar(synthetic_event(start="20261125", metadata="")),
            "date-only|DTSTART",
        ),
        (
            synthetic_calendar(synthetic_event(start="20261125T133000", metadata="")),
            "timezone-aware",
        ),
        (
            synthetic_calendar(
                synthetic_event(start="20261125T083000", metadata="",)
                .replace("DTSTART:", "DTSTART;TZID=America/New_York:")
            ),
            "exact UTC",
        ),
        (
            synthetic_calendar(
                synthetic_event(metadata="RRULE:FREQ=YEARLY\r\n")
            ),
            "recurrence",
        ),
        (
            synthetic_calendar(
                synthetic_event(metadata="RDATE:20271125T133000Z\r\n")
            ),
            "recurrence",
        ),
        (
            synthetic_calendar(
                synthetic_event(metadata="EXDATE:20261125T133000Z\r\n")
            ),
            "recurrence",
        ),
        (
            synthetic_calendar(
                synthetic_event(metadata="RECURRENCE-ID:20261125T133000Z\r\n")
            ),
            "recurrence",
        ),
        (
            synthetic_calendar(synthetic_event(metadata="SEQUENCE:not-an-int\r\n")),
            "malformed|SEQUENCE",
        ),
    ],
)
def test_invalid_calendars_and_events_raise_public_event_error(text, message):
    with pytest.raises(EventError, match=message):
        parse(text)


def test_duplicate_uid_is_invalid_even_when_payloads_are_identical():
    duplicate = synthetic_event(uid="duplicate@example.test", metadata="")

    with pytest.raises(EventError, match="unique"):
        parse(synthetic_calendar(duplicate, duplicate))


def test_empty_calendar_is_a_valid_full_snapshot_and_reports_removals():
    previous = parse(synthetic_calendar(synthetic_event()))["events"]

    result = parse(synthetic_calendar(), previous=previous)

    assert result == {
        "events": [],
        "evidence": [],
        "changes": {
            "added": [],
            "changed": [],
            "removed": ["bea-gdp-20261125@example.test"],
        },
    }


def test_event_records_pass_existing_lab_validation_without_reimplementing_it():
    from decision_lab.validation import validate_bundle

    evidence = parse(synthetic_calendar(synthetic_event()))["evidence"]
    bundle = {
        "schema_version": 1,
        "run_id": "synthetic-event-contract",
        "mode": "historical_diagnostic",
        "as_of": RETRIEVED_AT,
        "expires_at": "2026-11-20T00:00:00+00:00",
        "mandate_version": "synthetic-fixture-v1",
        "universe": [
            {"symbol": "SPY", "asset_class": "etf"},
            {"symbol": "QQQ", "asset_class": "etf"},
        ],
        "portfolio": {
            "snapshot_id": "synthetic-fixture",
            "observed_at": RETRIEVED_AT,
            "cash": "100.00",
            "equity": "100.00",
            "pending_buy_notional": "0",
            "positions": [],
        },
        "evidence": evidence,
    }

    assert validate_bundle(bundle) is bundle


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"retrieved_at": "2026-11-16T20:00:00"}, "timezone-aware"),
        ({"expires_at": RETRIEVED_AT}, "follow"),
        ({"symbols": []}, "nonempty"),
        ({"symbols": ["SPY", "SPY"]}, "unique"),
        ({"symbols": ["SPY", " QQQ"]}, "exact nonempty"),
        ({"symbols": ["SPY", 3]}, "exact nonempty"),
        ({"previous": "not-events"}, "previous"),
    ],
)
def test_invalid_public_arguments_raise_event_error(overrides, message):
    with pytest.raises(EventError, match=message):
        parse(synthetic_calendar(synthetic_event()), **overrides)


@pytest.mark.parametrize(
    ("previous", "message"),
    [
        ([{"uid": "incomplete"}], "normalized event"),
        (
            [
                {
                    "uid": "x@example.test",
                    "summary": "X",
                    "event_at": "2026-11-25T13:30:00+00:00",
                    "dtstamp": None,
                    "sequence": True,
                    "duration": None,
                }
            ],
            "sequence",
        ),
        (
            [
                {
                    "uid": "x@example.test",
                    "summary": "X",
                    "event_at": "2026-11-25T13:30:00+00:00",
                    "dtstamp": None,
                    "sequence": None,
                    "duration": None,
                },
                {
                    "uid": "x@example.test",
                    "summary": "Other",
                    "event_at": "2026-11-26T13:30:00+00:00",
                    "dtstamp": None,
                    "sequence": None,
                    "duration": None,
                },
            ],
            "unique",
        ),
    ],
)
def test_invalid_previous_snapshots_raise_event_error(previous, message):
    with pytest.raises(EventError, match=message):
        parse(synthetic_calendar(synthetic_event()), previous=previous)


def test_malformed_missing_start_is_normalized_to_event_error():
    event = event_with_lines("UID:no-start@example.test", "SUMMARY:No start")

    with pytest.raises(EventError, match="DTSTART"):
        parse(synthetic_calendar(event))


def test_missing_required_identity_property_is_rejected():
    event = event_with_lines("SUMMARY:No UID", "DTSTART:20261125T133000Z")

    with pytest.raises(EventError, match="uid is required"):
        parse(synthetic_calendar(event))


def test_negative_sequence_is_rejected():
    event = event_with_lines(
        "UID:negative@example.test",
        "SUMMARY:Negative sequence",
        "DTSTART:20261125T133000Z",
        "SEQUENCE:-1",
    )

    with pytest.raises(EventError, match="nonnegative"):
        parse(synthetic_calendar(event))


def test_duplicate_required_property_and_invalid_duration_are_rejected():
    duplicate_summary = event_with_lines(
        "UID:duplicate-property@example.test",
        "SUMMARY:First",
        "SUMMARY:Second",
        "DTSTART:20261125T133000Z",
    )
    with pytest.raises(EventError, match="must not be duplicated"):
        parse(synthetic_calendar(duplicate_summary))

    invalid_duration = event_with_lines(
        "UID:bad-duration@example.test",
        "SUMMARY:Bad duration",
        "DTSTART:20261125T133000Z",
        "DURATION:not-a-duration",
    )
    with pytest.raises(EventError, match="malformed VEVENT"):
        parse(synthetic_calendar(invalid_duration))

    zero_duration = event_with_lines(
        "UID:zero-duration@example.test",
        "SUMMARY:Zero duration",
        "DTSTART:20261125T133000Z",
        "DURATION:PT0S",
    )
    with pytest.raises(EventError, match="positive duration"):
        parse(synthetic_calendar(zero_duration))


def test_text_and_event_count_bounds_are_enforced():
    with pytest.raises(EventError, match="too long"):
        parse(
            synthetic_calendar(
                synthetic_event(uid="uid", summary="S" * 20_001, metadata="")
            )
        )
    with pytest.raises(EventError, match="too many"):
        parse(synthetic_calendar(*(synthetic_event(uid=f"fixture-{index}", metadata="") for index in range(2001))))


def test_input_size_is_bounded_before_parsing():
    oversized = "BEGIN:VCALENDAR\r\n" + ("X" * 2_000_001)

    with pytest.raises(EventError, match="too large"):
        parse(oversized)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"retrieved_at": 3}, "retrieved_at"),
        ({"retrieved_at": "not-a-time"}, "retrieved_at"),
        ({"symbols": ["SPY\x00"]}, "invalid character"),
        ({"symbols": ["X" * 20_001]}, "too long"),
    ],
)
def test_additional_scalar_validation_paths(overrides, message):
    with pytest.raises(EventError, match=message):
        parse(synthetic_calendar(synthetic_event()), **overrides)


def test_non_vcalendar_and_non_string_inputs_are_rejected():
    invalid_calendar: Any = 3
    with pytest.raises(EventError, match="string"):
        parse(invalid_calendar)
    with pytest.raises(EventError, match="VCALENDAR"):
        parse(synthetic_event())


def test_invalid_utf8_surrogate_is_normalized():
    with pytest.raises(EventError, match="UTF-8"):
        parse("\ud800")


def test_optional_dtstamp_and_duration_validation_paths():
    too_long_summary = "S" * 20_001
    with pytest.raises(EventError, match="summary is too long"):
        parse(synthetic_calendar(synthetic_event(summary=too_long_summary, metadata="")))

    floating_dtstamp = synthetic_event(metadata="DTSTAMP:20261101T120000\r\n")
    with pytest.raises(EventError, match="DTSTAMP"):
        parse(synthetic_calendar(floating_dtstamp))

    local_dtstamp = synthetic_event(
        metadata="DTSTAMP;TZID=America/New_York:20261101T120000\r\n"
    )
    with pytest.raises(EventError, match="timezone-aware UTC"):
        parse(synthetic_calendar(local_dtstamp))
