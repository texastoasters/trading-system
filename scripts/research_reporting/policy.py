"""Pure assessment policy for read-only research reporting."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from decimal import Context, Decimal, InvalidOperation, localcontext
from typing import Any, NoReturn
from zoneinfo import ZoneInfo


_SNAPSHOT_KEYS = {
    "schema_version",
    "status",
    "observed_at",
    "campaign_id",
    "registration_hash",
    "registered_at",
    "report",
    "verification",
}
_HEALTH_KEYS = {
    "timer_active",
    "service_active_state",
    "service_result",
    "service_exit_status",
    "service_elapsed_seconds",
}
_SAFETY = (
    "Read-only research; no orders. Raw price responses, not profit or adjusted return; "
    "corporate actions unchecked; billed cost unknown; cohorts are not compounded."
)
_CHICAGO = ZoneInfo("America/Chicago")
_SOURCE_FAILURE_REASONS = {
    "observation_capture_failed",
    "observation_reconstruction_failed",
    "reconstruction_failed",
    "collection_failed",
}


class ReportingError(Exception):
    """A sanitized public failure raised for malformed reporting input."""


def _fail() -> NoReturn:
    raise ReportingError("invalid_reporting_input")


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail()
    return value


def _list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        _fail()
    return value


def _text(value: Any, *, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        _fail()
    return value


def _exact_int(value: Any, *, nonnegative: bool = False) -> int:
    if type(value) is not int or (nonnegative and value < 0):
        _fail()
    return value


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        _fail()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        _fail()
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail()
    return parsed.astimezone(UTC)


def _day(value: str) -> date:
    return date.fromisoformat(value)


def _decimal(value: Any) -> Decimal:
    if not isinstance(value, str):
        _fail()
    try:
        result = Decimal(value)
    except InvalidOperation:
        _fail()
    if not result.is_finite():
        _fail()
    return result


def _validate_health(health: Any) -> dict[str, Any]:
    value = _mapping(health)
    if set(value) != _HEALTH_KEYS:
        _fail()
    if type(value["timer_active"]) is not bool:
        _fail()
    _text(value["service_active_state"])
    _text(value["service_result"])
    _exact_int(value["service_exit_status"])
    elapsed = value["service_elapsed_seconds"]
    if elapsed is not None:
        if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)):
            _fail()
        if not math.isfinite(elapsed) or elapsed < 0:
            _fail()
    return value


def _validate_snapshot(snapshot: Any, now: datetime) -> dict[str, Any]:
    value = _mapping(snapshot)
    if set(value) != _SNAPSHOT_KEYS:
        _fail()
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        _fail()
    status = _text(value["status"])
    if status not in {"verified", "busy", "unavailable"}:
        _fail()
    observed_at = _time(value["observed_at"])
    if observed_at > now:
        _fail()
    _text(value["campaign_id"])
    _text(value["registration_hash"])
    registered_at = value["registered_at"]
    if registered_at is not None and _time(registered_at) > observed_at:
        _fail()
    if status != "verified":
        if value["report"] is not None or value["verification"] is not None:
            _fail()
        return value

    report = _mapping(value["report"])
    verification = _mapping(value["verification"])
    for public in (report, verification):
        if type(public.get("schema_version")) is not int or public["schema_version"] != 1:
            _fail()
        if public.get("campaign_id") != value["campaign_id"]:
            _fail()
        if public.get("verified") is not True or public.get("executable") is not False:
            _fail()
    if verification.get("registration_hash") != value["registration_hash"]:
        _fail()
    _list(report.get("checkpoints"))
    _list(verification.get("checkpoints"))
    _list(verification.get("events"))
    _list(verification.get("ticks"))
    for raw_tick in verification["ticks"]:
        completed = _mapping(raw_tick).get("value")
        completed_at = _mapping(completed).get("completed_at")
        if completed_at is not None and _time(completed_at) > now:
            _fail()
    for raw_checkpoint in verification["checkpoints"]:
        checked = _mapping(raw_checkpoint)
        checkpoint = _mapping(checked.get("checkpoint"))
        for name in ("bundle_as_of", "inputs_frozen_at", "frozen_at"):
            timestamp = checkpoint.get(name)
            if timestamp is not None and _time(timestamp) > now:
                _fail()
        score = _mapping(checked.get("score"))
        for raw_horizon in _list(score.get("horizons")):
            horizon = _mapping(raw_horizon)
            latest_completed = horizon.get("latest_observation_completed_at")
            if latest_completed is not None and _time(latest_completed) > now:
                _fail()
            for name in ("first_observed", "latest_observed"):
                observation = horizon.get(name)
                if observation is not None:
                    completed_at = _mapping(observation).get("observation_completed_at")
                    if completed_at is None or _time(completed_at) > now:
                        _fail()
    return value


def _health_conditions(health: dict[str, Any]) -> dict[str, str]:
    conditions: dict[str, str] = {}
    if not health["timer_active"]:
        conditions["timer_disabled"] = "Evaluator timer is disabled."
    if (
        health["service_active_state"] == "failed"
        or health["service_result"] not in {"success", ""}
        or health["service_exit_status"] != 0
    ):
        conditions["service_failed"] = (
            "Evaluator service is failed."
            if health["service_active_state"] == "failed"
            else "Evaluator service did not exit successfully."
        )
    elapsed = health["service_elapsed_seconds"]
    if health["service_active_state"] in {"active", "activating", "deactivating"} and elapsed is not None and elapsed > 900:
        conditions["service_overlong"] = (
            "Evaluator service has run for more than 900 seconds."
        )
    return conditions


def _verified_ticks(verification: dict[str, Any]) -> list[dict[str, Any]]:
    ticks: list[dict[str, Any]] = []
    for item in _list(verification["ticks"]):
        record = _mapping(item)
        record_id = _text(record.get("record_id"))
        tick = _mapping(record.get("value"))
        status = _text(tick.get("status"))
        tick_id = tick.get("tick_id", record_id)
        _text(tick_id)
        completed = tick.get("completed_at")
        if completed is not None:
            _time(completed)
        ticks.append(record)
    return ticks


def _stale_condition(
    snapshot: dict[str, Any], verification: dict[str, Any], now: datetime,
) -> dict[str, str]:
    completed = [
        _time(item["value"]["completed_at"])
        for item in _verified_ticks(verification)
        if item["value"].get("completed_at") is not None
    ]
    base = max(completed) if completed else (
        _time(snapshot["registered_at"]) if snapshot["registered_at"] is not None else None
    )
    if base is not None and (now - base).total_seconds() > 2700:
        return {"stale_tick": "No completed evaluator tick for more than 2700 seconds."}
    return {}


def _validated_checkpoints(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    verification = snapshot["verification"]
    report = snapshot["report"]
    verified_by_slot: dict[str, dict[str, Any]] = {}
    for item in _list(verification["checkpoints"]):
        checked = _mapping(item)
        slot = _text(checked.get("slot_date"))
        _day(slot)
        if slot in verified_by_slot:
            _fail()
        checkpoint = _mapping(checked.get("checkpoint"))
        arms = _mapping(checkpoint.get("arms"))
        ai = _mapping(arms.get("ai_three_role"))
        _text(ai.get("status"))
        _list(checkpoint.get("model_receipts"))
        score = _mapping(checked.get("score"))
        if score.get("slot_date") != slot:
            _fail()
        _text(score.get("checkpoint_status"))
        for raw_horizon in _list(score.get("horizons")):
            horizon = _mapping(raw_horizon)
            for name in ("first_observed", "latest_observed"):
                observation = horizon.get(name)
                if observation is not None:
                    _time(_mapping(observation).get("observation_completed_at"))
        verified_by_slot[slot] = checked

    reported_by_slot: dict[str, dict[str, Any]] = {}
    for item in _list(report["checkpoints"]):
        checked = _mapping(item)
        slot = _text(checked.get("slot_date"))
        _day(slot)
        if slot in reported_by_slot:
            _fail()
        _mapping(checked.get("score"))
        _list(checked.get("model_receipts"))
        reported_by_slot[slot] = checked
    if set(verified_by_slot) != set(reported_by_slot):
        _fail()
    return [
        {"verified": verified_by_slot[slot], "reported": reported_by_slot[slot]}
        for slot in sorted(verified_by_slot)
    ]


def _events(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    campaign = snapshot["campaign_id"]
    findings: list[dict[str, str]] = []
    for item in _list(snapshot["verification"]["events"]):
        record = _mapping(item)
        record_id = _text(record.get("record_id"))
        value = _mapping(record.get("value"))
        event = _text(value.get("event"))
        if record_id.startswith("missed-") or event == "checkpoint_missed":
            slot = _text(value.get("slot_date"))
            _day(slot)
            findings.append({
                "key": f"{campaign}:event:{record_id}",
                "text": f"Research checkpoint {slot} was missed.",
            })
        elif record_id.startswith("collection-") and record_id.endswith("-failed"):
            findings.append({
                "key": f"{campaign}:event:{record_id}",
                "text": "Research evidence collection failed.",
            })

    for item in _verified_ticks(snapshot["verification"]):
        value = item["value"]
        if value["status"] == "in_doubt":
            tick_id = _text(value.get("tick_id", item["record_id"]))
            findings.append({
                "key": f"{campaign}:tick:{tick_id}",
                "text": f"Research tick {tick_id} has an in-doubt completion.",
            })
        elif (
            value["status"] == "unavailable"
            and value.get("reason") in _SOURCE_FAILURE_REASONS
        ):
            tick_id = _text(value.get("tick_id", item["record_id"]))
            findings.append({
                "key": f"{campaign}:tick:{tick_id}:source-unavailable",
                "text": f"Research tick {tick_id} could not verify source observations.",
            })

    for pair in _validated_checkpoints(snapshot):
        item = pair["verified"]
        slot = item["slot_date"]
        checkpoint_status = item["score"]["checkpoint_status"]
        ai_status = item["checkpoint"]["arms"]["ai_three_role"]["status"]
        if checkpoint_status == "late":
            kind, text = "late", f"AI checkpoint {slot} completed late."
        elif ai_status == "unavailable":
            kind, text = "unavailable", f"AI checkpoint {slot} was unavailable."
        elif ai_status != "accepted":
            kind, text = "notaccepted", f"AI checkpoint {slot} was not accepted."
        else:
            continue
        findings.append({"key": f"{campaign}:ai:{slot}:{kind}", "text": text})
    return findings


def _percent(value: Any) -> str:
    decimal = _decimal(value)
    precision = max(38, len(decimal.as_tuple().digits) + 3)
    with localcontext(Context(prec=precision)):
        amount = decimal * Decimal(100)
    rendered = format(amount, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return f"{rendered or '0'}%"


def _allocation(pair: dict[str, Any]) -> str:
    ai = pair["verified"]["checkpoint"]["arms"]["ai_three_role"]
    status = ai["status"]
    if status != "accepted":
        return status.replace("_", " ")
    targets = _list(ai.get("targets"))
    pieces: list[str] = []
    for target in targets:
        item = _mapping(target)
        symbol = _text(item.get("symbol"))
        weight = _decimal(item.get("weight"))
        if weight != 0:
            pieces.append(f"{symbol[:32]} {_percent(item['weight'])}")
    cash = _decimal(ai.get("cash_weight"))
    if cash != 0 or not pieces:
        pieces.append(f"cash {_percent(ai['cash_weight'])}")
    return "accepted; " + ", ".join(pieces)


def _usage(checkpoints: list[dict[str, Any]]) -> str:
    receipts = [
        receipt
        for pair in checkpoints
        for receipt in _list(pair["reported"]["model_receipts"])
    ]
    recorded = [
        receipt for receipt in receipts
        if not (
            isinstance(receipt, dict)
            and receipt.get("status") == "unknown"
            and receipt.get("billed_cost") == "unknown"
        )
    ]
    total_unknown = any(
        pair["verified"]["checkpoint"]["arms"]["ai_three_role"]["status"]
        == "in_doubt"
        for pair in checkpoints
    )
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    known = bool(recorded)
    for raw in recorded:
        receipt = _mapping(raw)
        usage = receipt.get("usage")
        if not isinstance(usage, dict):
            known = False
            continue
        for name in totals:
            amount = usage.get(name)
            if type(amount) is not int or amount < 0:
                known = False
            else:
                totals[name] += amount
    if known:
        detail = (
            f"usage input {totals['input_tokens']}, output {totals['output_tokens']}, "
            f"total {totals['total_tokens']} tokens"
        )
    else:
        detail = "usage unknown"
    inference = "; total inference calls unknown" if total_unknown else ""
    return (
        f"Recorded model receipts {len(recorded)}{inference}; {detail}; "
        "billed cost unknown."
    )


def _raw_responses(observed: Any) -> str:
    value = _mapping(observed)
    raw = _mapping(value.get("raw_response"))
    arms = _list(raw.get("arms"))
    responses: list[str] = []
    for arm in arms:
        item = _mapping(arm)
        name = _text(item.get("arm"))
        response = item.get("response")
        if response is not None:
            label = "AI" if name == "ai_three_role" else name
            responses.append(f"{label} {_percent(response)}")
    if not any(response.startswith("AI ") for response in responses):
        _fail()
    return ", ".join(responses)


def _outcome(checkpoints: list[dict[str, Any]], horizon: int) -> str:
    mature: list[tuple[str, dict[str, Any]]] = []
    for pair in checkpoints:
        score = pair["verified"]["score"]
        for raw in _list(score["horizons"]):
            item = _mapping(raw)
            if _exact_int(item.get("horizon")) != horizon:
                continue
            status = _text(item.get("status"))
            if status in {"scored", "unavailable"}:
                mature.append((pair["verified"]["slot_date"], item))
    if mature:
        slot, item = max(mature, key=lambda value: value[0])
        if item["status"] == "unavailable":
            return f"{horizon}-session outcome cohort {slot}: unavailable."
        first = _raw_responses(item.get("first_observed"))
        latest_observed = item.get("latest_observed")
        latest = (
            _raw_responses(latest_observed) if latest_observed is not None else None
        )
        latest_text = latest if latest is not None else "unavailable"
        return (
            f"{horizon}-session outcome cohort {slot}: first raw responses {first}; "
            f"latest {latest_text}."
        )
    return f"{horizon}-session outcome: pending."


def _count(report: dict[str, Any], name: str) -> int:
    counts = _mapping(report.get("counts"))
    return _exact_int(counts.get(name), nonnegative=True)


def _summary(snapshot: dict[str, Any]) -> str:
    checkpoints = _validated_checkpoints(snapshot)
    report = snapshot["report"]
    count = len(checkpoints)
    latest_line = "Latest AI: no checkpoint."
    if checkpoints:
        latest = max(checkpoints, key=lambda item: item["verified"]["slot_date"])
        latest_line = (
            f"Latest AI {latest['verified']['slot_date']}: {_allocation(latest)}."
        )
    lines = [
        f"T² research campaign {snapshot['campaign_id']}",
        _as_of(snapshot["observed_at"]),
        (
            f"Coverage: {count} checkpoint{'s' if count != 1 else ''}; "
            f"observations {_count(report, 'valid_observations')} valid, "
            f"{_count(report, 'unavailable_observations')} unavailable "
            f"({_count(report, 'observations')} total); failures "
            f"{_count(report, 'missed')} missed, {_count(report, 'collection_failures')} collection, "
            f"{_count(report, 'in_doubt')} in-doubt."
        ),
        latest_line,
        _usage(checkpoints),
        _outcome(checkpoints, 1),
        _outcome(checkpoints, 5),
    ]
    prefix = "\n".join(lines)
    maximum_prefix = 2000 - len(_SAFETY) - 1
    if len(prefix) > maximum_prefix:
        prefix = prefix[: maximum_prefix - 1].rstrip(" ,;") + "…"
    return prefix + "\n" + _SAFETY


def _as_of(value: str) -> str:
    observed = _time(value)
    local = observed.astimezone(_CHICAGO)
    return (
        f"As of {local:%Y-%m-%d %H:%M} America/Chicago "
        f"({observed:%Y-%m-%d %H:%M} UTC)."
    )


def _latest_source_condition(snapshot: dict[str, Any]) -> dict[str, str]:
    ticks = [
        item for item in _verified_ticks(snapshot["verification"])
        if item["value"].get("completed_at") is not None
    ]
    if not ticks:
        return {}
    latest = max(
        ticks, key=lambda item: _time(item["value"]["completed_at"]),
    )["value"]
    if (
        latest["status"] == "unavailable"
        and latest.get("reason") in _SOURCE_FAILURE_REASONS
    ):
        return {
            "verification_unavailable": (
                "Latest evaluator source/collection is unavailable."
            )
        }
    return {}


def assess(snapshot: Any, health: Any, *, now: datetime) -> dict[str, Any]:
    """Assess one snapshot and health observation without side effects."""
    try:
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            _fail()
        current = now.astimezone(UTC)
        checked_health = _validate_health(health)
        checked_snapshot = _validate_snapshot(snapshot, current)
        conditions = _health_conditions(checked_health)
        status = checked_snapshot["status"]
        if status == "busy":
            return {
                "summary": None,
                "event_findings": [],
                "conditions": conditions,
                "indeterminate_conditions": ["verification_unavailable", "stale_tick"],
                "busy": True,
            }
        if status == "unavailable":
            conditions["verification_unavailable"] = (
                "Offline campaign verification is unavailable."
            )
            return {
                "summary": None,
                "event_findings": [],
                "conditions": conditions,
                "indeterminate_conditions": ["stale_tick"],
                "busy": False,
            }
        conditions.update(
            _stale_condition(checked_snapshot, checked_snapshot["verification"], current)
        )
        conditions.update(_latest_source_condition(checked_snapshot))
        return {
            "summary": _summary(checked_snapshot),
            "event_findings": _events(checked_snapshot),
            "conditions": conditions,
            "indeterminate_conditions": [],
            "busy": False,
        }
    except ReportingError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError, InvalidOperation):
        _fail()
