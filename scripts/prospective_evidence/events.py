"""Pure normalization of the authoritative BEA release calendar."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from icalendar import Calendar


class EventError(ValueError):
    """A public validation failure for BEA scheduled-event normalization."""


BEA_CALENDAR_SOURCE = (
    "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics"
)
_MAX_INPUT_BYTES = 2_000_000
_MAX_EVENTS = 2_000
_MAX_TEXT_CHARS = 20_000
_EVENT_FIELDS = {"uid", "summary", "event_at", "dtstamp", "sequence", "duration"}
_RECURRENCE_FIELDS = {"RRULE", "RDATE", "EXDATE", "RECURRENCE-ID"}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _time(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise EventError(f"{label} must be a timezone-aware ISO8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError) as exc:
        raise EventError(f"{label} must be a timezone-aware ISO8601 string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EventError(f"{label} must be timezone-aware")
    return parsed


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _exact_text(value: Any, label: str, *, allow_whitespace: bool = False) -> str:
    if not isinstance(value, str) or not value.strip() or (not allow_whitespace and value.strip() != value):
        raise EventError(f"{label} must be an exact nonempty string")
    if "\x00" in value:
        raise EventError(f"{label} contains an invalid character")
    if len(value) > _MAX_TEXT_CHARS:
        raise EventError(f"{label} is too long")
    return value


def _symbols(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise EventError("symbols must be a nonempty array")
    result: list[str] = []
    for index, symbol in enumerate(value):
        result.append(_exact_text(symbol, f"symbols[{index}]"))
    if len(set(result)) != len(result):
        raise EventError("symbols must be unique")
    return result


def _required_property_text(component: Any, name: str) -> str:
    value = component.get(name)
    if value is None:
        raise EventError(f"{name.lower()} is required")
    return str(value)


def _duration_text(component: Any) -> str | None:
    value = component.get("DURATION")
    if value is None:
        return None
    duration = getattr(value, "dt", None)
    if not isinstance(duration, timedelta) or duration <= timedelta(0):
        raise EventError("DURATION must be a positive duration")
    encoded = value.to_ical()
    text = encoded.decode("ascii") if isinstance(encoded, bytes) else str(encoded)
    return text


def _event_from_component(component: Any) -> dict[str, Any]:
    if str(component.get("STATUS", "")).upper() == "CANCELLED":
        raise EventError("cancelled events are unsupported; collection incomplete")
    for field in _RECURRENCE_FIELDS:
        if component.get(field) is not None:
            raise EventError(f"recurrence property {field} is unsupported")
    for field in ("UID", "SUMMARY", "DTSTART", "DTSTAMP", "SEQUENCE", "DURATION"):
        if isinstance(component.get(field), list):
            raise EventError(f"event property {field} must not be duplicated")

    uid = _exact_text(_required_property_text(component, "UID"), "uid")
    summary = _exact_text(_required_property_text(component, "SUMMARY"), "summary", allow_whitespace=True)
    start_property = component.get("DTSTART")
    if start_property is None:
        raise EventError("event DTSTART is required")
    start = start_property.dt
    if isinstance(start, date) and not isinstance(start, datetime):
        raise EventError("event DTSTART must not be date-only")
    if not isinstance(start, datetime) or start.tzinfo is None or start.utcoffset() is None:
        raise EventError("event DTSTART must be timezone-aware")
    if start.utcoffset() != timezone.utc.utcoffset(start):
        raise EventError("event DTSTART must be exact UTC")

    dtstamp_property = component.get("DTSTAMP")
    dtstamp: str | None = None
    if dtstamp_property is not None:
        stamp = dtstamp_property.dt
        if (
            not isinstance(stamp, datetime)
            or stamp.tzinfo is None
            or stamp.utcoffset() is None
            or stamp.utcoffset() != timezone.utc.utcoffset(stamp)
        ):
            raise EventError("DTSTAMP must be exact timezone-aware UTC")
        dtstamp = _utc_text(stamp)

    sequence_property = component.get("SEQUENCE")
    sequence: int | None = None
    if sequence_property is not None:
        raw_sequence = sequence_property.to_ical()
        try:
            sequence = int(raw_sequence)
        except (TypeError, ValueError, OverflowError) as exc:
            raise EventError("SEQUENCE must be an integer") from exc
        if sequence < 0:
            raise EventError("SEQUENCE must be nonnegative")

    duration = _duration_text(component)
    return {
        "uid": uid,
        "summary": summary,
        "event_at": _utc_text(start),
        "dtstamp": dtstamp,
        "sequence": sequence,
        "duration": duration,
    }


def _previous_events(previous: Any) -> list[dict[str, Any]]:
    if previous is None:
        return []
    if isinstance(previous, dict):
        previous = previous.get("events")
    if not isinstance(previous, list):
        raise EventError("previous must be a normalized event list or result")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(previous):
        if not isinstance(item, dict) or set(item) != _EVENT_FIELDS:
            raise EventError(f"previous[{index}] must be a normalized event")
        uid = _exact_text(item["uid"], f"previous[{index}].uid")
        summary = _exact_text(item["summary"], f"previous[{index}].summary", allow_whitespace=True)
        event_at = _utc_text(_time(item["event_at"], f"previous[{index}].event_at"))
        dtstamp_value = item["dtstamp"]
        dtstamp = (
            None
            if dtstamp_value is None
            else _utc_text(_time(dtstamp_value, f"previous[{index}].dtstamp"))
        )
        sequence = item["sequence"]
        if sequence is not None and (type(sequence) is not int or sequence < 0):
            raise EventError(f"previous[{index}].sequence must be a nonnegative integer or null")
        duration = item["duration"]
        if duration is not None:
            duration = _exact_text(duration, f"previous[{index}].duration")
        if uid in seen:
            raise EventError("previous event UIDs must be unique")
        seen.add(uid)
        result.append(
            {
                "uid": uid,
                "summary": summary,
                "event_at": event_at,
                "dtstamp": dtstamp,
                "sequence": sequence,
                "duration": duration,
            }
        )
    return result


def parse_bea_calendar(
    text: str,
    *,
    retrieved_at: str,
    expires_at: str,
    symbols: list[str],
    previous: Any = None,
) -> dict[str, Any]:
    """Normalize a complete BEA calendar and compare its full event snapshot."""
    retrieved = _time(retrieved_at, "retrieved_at")
    expiry = _time(expires_at, "expires_at")
    normalized_retrieved_at = _utc_text(retrieved)
    if expiry <= retrieved:
        raise EventError("expires_at must follow retrieved_at")
    normalized_symbols = _symbols(symbols)
    previous_events = _previous_events(previous)
    if not isinstance(text, str):
        raise EventError("calendar text must be a string")
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise EventError("calendar text must be valid UTF-8") from exc
    if not encoded or len(encoded) > _MAX_INPUT_BYTES:
        raise EventError("calendar text is empty or too large")
    try:
        calendar = Calendar.from_ical(text)
    except Exception as exc:
        raise EventError("malformed iCalendar input") from exc
    if calendar.name != "VCALENDAR":
        raise EventError("input must be a VCALENDAR")
    if str(calendar.get("METHOD", "")).upper() == "CANCEL":
        raise EventError("cancelled calendars are unsupported; collection incomplete")

    components = [component for component in calendar.walk() if component.name == "VEVENT"]
    if len(components) > _MAX_EVENTS:
        raise EventError("calendar has too many events")
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for component in components:
        try:
            event = _event_from_component(component)
        except EventError:
            raise
        except Exception as exc:
            raise EventError("malformed VEVENT") from exc
        if event["uid"] in seen:
            raise EventError("event UIDs must be unique")
        seen.add(event["uid"])
        events.append(event)

    previous_by_uid = {event["uid"]: event for event in previous_events}
    current_by_uid = {event["uid"]: event for event in events}
    added = sorted(current_by_uid.keys() - previous_by_uid.keys())
    removed = sorted(previous_by_uid.keys() - current_by_uid.keys())
    changed = sorted(
        uid
        for uid in current_by_uid.keys() & previous_by_uid.keys()
        if current_by_uid[uid] != previous_by_uid[uid]
    )

    evidence: list[dict[str, Any]] = []
    for event in events:
        event_time = _time(event["event_at"], "event_at")
        if event_time <= retrieved:
            continue
        content_value = {
            "claim": "scheduled release only; no outcome or forecast",
            "metadata": {
                "dtstamp": event["dtstamp"],
                "duration": event["duration"],
                "sequence": event["sequence"],
                "source_publication_at": None,
                "source_publication_status": "unknown",
            },
            "title": event["summary"],
            "uid": event["uid"],
        }
        content = _canonical_json(content_value)
        identity = _canonical_json(
            {"uid": event["uid"], "event_at": event["event_at"], "payload": content_value}
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        evidence.append(
            {
                "evidence_id": f"scheduled-event-sha256-{digest}",
                "kind": "scheduled_event",
                "source": BEA_CALENDAR_SOURCE,
                "published_at": normalized_retrieved_at,
                "available_at": normalized_retrieved_at,
                "expires_at": _utc_text(min(expiry, event_time)),
                "symbols": list(normalized_symbols),
                "event_at": event["event_at"],
                "content": content,
            }
        )
    return {
        "events": events,
        "evidence": evidence,
        "changes": {"added": added, "changed": changed, "removed": removed},
    }
