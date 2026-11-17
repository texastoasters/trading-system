"""Deterministic controls and causal raw-price cohort measurement."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_DOWN, localcontext
from typing import Any


class MeasurementError(ValueError):
    """Stable public failure for malformed or non-causal measurement input."""


_ERROR = "invalid_measurement_input"
_ZERO = Decimal("0")
_ONE = Decimal("1")
_QUANTUM = Decimal("0.000000000001")
_BASELINES = ("cash", "equal_weight", "sma20")
_ALLOWED_ARM_STATUSES = {"accepted", "unavailable", "missing", "failed", "blocked", "abstained", "in_doubt"}


def _fail() -> None:
    raise MeasurementError(_ERROR)


def _time(value: Any) -> datetime:
    if not isinstance(value, str):
        _fail()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail()
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail()
    return parsed


def _decimal(value: Any, *, positive: bool = False) -> Decimal:
    if not isinstance(value, str):
        _fail()
    try:
        number = Decimal(value)
    except InvalidOperation:
        _fail()
    if not number.is_finite() or number < 0 or positive and number <= 0:
        _fail()
    return number


def _format(number: Decimal) -> str:
    if number == 0:
        return "0"
    text = format(number.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _keys(value: Any, required: set[str], *, exact: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict) or not required <= value.keys() or exact and set(value) != required:
        _fail()
    return value


def _protocol_symbols(protocol: Any) -> list[str]:
    from prospective_evaluation.protocol import validate_protocol
    try:
        protocol = validate_protocol(protocol)
    except ValueError:
        _fail()
    return [item["symbol"] for item in protocol["universe"]]


def baseline_allocations(protocol: dict[str, Any], bar_inputs: dict[str, Any]) -> dict[str, Any]:
    """Return cash, equal-weight, and last-20-close SMA controls."""
    try:
        symbols = _protocol_symbols(protocol)
        if not isinstance(bar_inputs, dict) or set(bar_inputs) != set(symbols):
            _fail()
        with localcontext() as context:
            context.prec = 38
            weight = (_ONE / Decimal(len(symbols))).quantize(_QUANTUM, rounding=ROUND_DOWN)
            residual = _ONE - weight * len(symbols)
            selected: set[str] = set()
            for symbol in symbols:
                entry = _keys(bar_inputs[symbol], {"bars", "available_at", "source"})
                if not isinstance(entry["available_at"], str) or not isinstance(entry["source"], str):
                    _fail()
                bars = entry["bars"]
                if not isinstance(bars, list) or len(bars) != 21:
                    _fail()
                closes = [_decimal(_keys(bar, {"close"})["close"], positive=True) for bar in bars[-20:]]
                if closes[-1] > sum(closes, _ZERO) / Decimal(20):
                    selected.add(symbol)

            def targets(active: set[str]) -> list[dict[str, str]]:
                return [
                    {"symbol": symbol, "weight": _format(weight if symbol in active else _ZERO)}
                    for symbol in symbols
                ]

            all_symbols = set(symbols)
            return {
                "cash": {"status": "accepted", "targets": targets(set()), "cash_weight": "1"},
                "equal_weight": {
                    "status": "accepted",
                    "targets": targets(all_symbols),
                    "cash_weight": _format(residual),
                },
                "sma20": {
                    "status": "accepted",
                    "targets": targets(selected),
                    "cash_weight": _format(_ONE - weight * len(selected)),
                },
            }
    except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError):
        raise MeasurementError(_ERROR) from None


def _validate_outcome_sessions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != 5:
        _fail()
    rows: list[dict[str, Any]] = []
    previous: datetime | None = None
    for row in value:
        row = _keys(row, {"date", "interval_start", "session_close_at"}, exact=True)
        date = row["date"]
        interval = _time(row["interval_start"])
        close = _time(row["session_close_at"])
        if not isinstance(date, str) or interval.date().isoformat() != date or interval >= close:
            _fail()
        if previous is not None and interval <= previous:
            _fail()
        previous = interval
        rows.append(row)
    return rows


def _validate_arm(name: str, arm: Any, symbols: list[str]) -> dict[str, Any]:
    arm = _keys(arm, {"status", "targets", "cash_weight"})
    status = arm["status"]
    if status not in _ALLOWED_ARM_STATUSES:
        _fail()
    if status != "accepted":
        if arm["targets"] is not None or arm["cash_weight"] is not None:
            _fail()
        return arm
    targets = arm["targets"]
    if not isinstance(targets, list) or len(targets) != len(symbols):
        _fail()
    total = _decimal(arm["cash_weight"])
    seen: list[str] = []
    for item in targets:
        item = _keys(item, {"symbol", "weight"})
        if item["symbol"] not in symbols:
            _fail()
        seen.append(item["symbol"])
        total += _decimal(item["weight"])
    if seen != symbols or total != _ONE:
        _fail()
    return arm


def _validated_checkpoint(protocol: dict[str, Any], checkpoint: Any) -> tuple[list[str], list[dict[str, Any]], dict[str, Any], bool]:
    symbols = _protocol_symbols(protocol)
    checkpoint = _keys(checkpoint, {
        "schema_version", "slot_date", "collection_id", "bundle_hash", "collection_path",
        "decision_path", "source_session_close_at", "decision_deadline", "inputs_frozen_at",
        "frozen_at", "outcome_sessions", "arms", "model_receipts",
    })
    if type(checkpoint["schema_version"]) is not int or checkpoint["schema_version"] != 1:
        _fail()
    for field in ("slot_date", "collection_id", "bundle_hash", "collection_path"):
        if not isinstance(checkpoint[field], str) or not checkpoint[field]:
            _fail()
    if checkpoint["decision_path"] is not None and not isinstance(checkpoint["decision_path"], str):
        _fail()
    if not isinstance(checkpoint["model_receipts"], list):
        _fail()
    close = _time(checkpoint["source_session_close_at"])
    inputs = _time(checkpoint["inputs_frozen_at"])
    frozen = _time(checkpoint["frozen_at"])
    deadline = _time(checkpoint["decision_deadline"])
    rows = _validate_outcome_sessions(checkpoint["outcome_sessions"])
    first_interval = _time(rows[0]["interval_start"])
    if checkpoint["slot_date"] >= rows[0]["date"] or not close < inputs <= frozen:
        _fail()
    expected_deadline = min(inputs + timedelta(seconds=300), first_interval - timedelta(microseconds=1))
    if deadline != expected_deadline:
        _fail()
    arms = checkpoint["arms"]
    if not isinstance(arms, dict) or set(arms) != {*_BASELINES, "ai_three_role"}:
        _fail()
    validated = {name: _validate_arm(name, arms[name], symbols) for name in (*_BASELINES, "ai_three_role")}
    return symbols, rows, validated, frozen >= deadline


def _reconstruct(observation: dict[str, Any]) -> dict[str, Any]:
    from prospective_evaluation.observations import reconstruct_observation
    try:
        normalized = reconstruct_observation(observation)
    except (ValueError, TypeError, KeyError):
        _fail()
    return _keys(normalized, {"sessions", "bars", "completed_at"})


def _calendar_matches(normalized: dict[str, Any], outcomes: list[dict[str, Any]]) -> bool:
    sessions = normalized["sessions"]
    by_date = {row["date"]: row for row in sessions}
    for target in outcomes:
        actual = by_date.get(target["date"])
        if actual is None or any(
            _time(actual[field]) != _time(target[field])
            for field in ("interval_start", "session_close_at")
        ):
            return False
    return True


def _bars_by_symbol(normalized: dict[str, Any], symbols: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    bars = normalized["bars"]
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for symbol in symbols:
        result[symbol] = {}
        for bar in bars[symbol]:
            result[symbol][bar["session_date"]] = bar
    return result


def _response_version(
    normalized: dict[str, Any], symbols: list[str], outcomes: list[dict[str, Any]],
    arms: dict[str, Any], horizon: int, anchors: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    indexed = _bars_by_symbol(normalized, symbols)
    target = outcomes[horizon - 1]
    symbol_rows: list[dict[str, Any]] = []
    responses: dict[str, Decimal] = {}
    for symbol in symbols:
        entry = indexed[symbol].get(outcomes[0]["date"])
        exit_bar = indexed[symbol].get(target["date"])
        if entry is None or exit_bar is None:
            return None
        anchor = anchors[symbol]
        exit_price = _decimal(exit_bar["close"], positive=True)
        response = exit_price / anchor["price"] - _ONE
        responses[symbol] = response
        symbol_rows.append({
            "symbol": symbol,
            "entry_price": _format(anchor["price"]),
            "exit_price": _format(exit_price),
            "response": _format(response),
            "entry_available_at": anchor["available_at"],
            "exit_available_at": exit_bar["available_at"],
            "source_published_at": None,
        })

    arm_rows: list[dict[str, Any]] = []
    arm_values: dict[str, Decimal | None] = {}
    for name in (*_BASELINES, "ai_three_role"):
        arm = arms[name]
        if arm["status"] != "accepted":
            arm_values[name] = None
            arm_rows.append({"arm": name, "arm_status": arm["status"], "status": arm["status"], "response": None})
            continue
        value = sum(
            (_decimal(item["weight"]) * responses[item["symbol"]] for item in arm["targets"]),
            _ZERO,
        )
        arm_values[name] = value
        arm_rows.append({"arm": name, "arm_status": "accepted", "status": "scored", "response": _format(value)})

    ai = arm_values["ai_three_role"]
    differences = [{
        "baseline": baseline,
        "status": "scored" if ai is not None else arms["ai_three_role"]["status"],
        "difference": _format(ai - arm_values[baseline]) if ai is not None and arm_values[baseline] is not None else None,
    } for baseline in _BASELINES]
    return {
        "observation_completed_at": _time(normalized["completed_at"]).isoformat(),
        "raw_response": {
            "kind": "price_only",
            "corporate_action_status": "unchecked",
            "economically_scorable": False,
            "symbols": symbol_rows,
            "arms": arm_rows,
            "paired_baseline_differences": differences,
        },
    }


def _horizon_unavailable(horizon: int, reason: str) -> dict[str, Any]:
    return {"horizon": horizon, "status": "unavailable", "reason": reason, "first_observed": None, "latest_observed": None}


def score_checkpoint(protocol: dict[str, Any], checkpoint: dict[str, Any], observations: list[dict[str, Any]]) -> dict[str, Any]:
    """Score fixed future sessions from public observation reconstruction only."""
    try:
        symbols, outcomes, arms, late = _validated_checkpoint(protocol, checkpoint)
        if not isinstance(observations, list):
            _fail()
        normalized = sorted((_reconstruct(item) for item in observations), key=lambda item: _time(item["completed_at"]))
        horizons: list[dict[str, Any]] = []
        if late:
            horizons = [_horizon_unavailable(horizon, "checkpoint_late") for horizon in (1, 5)]
            checkpoint_status = "late"
        else:
            checkpoint_status = "eligible"
            calendar_ok = all(_calendar_matches(item, outcomes) for item in normalized)
            if normalized and not calendar_ok:
                horizons = [_horizon_unavailable(horizon, "calendar_target_mismatch") for horizon in (1, 5)]
            else:
                # Anchors are pinned from the earliest observation containing all entry bars.
                anchors: dict[str, dict[str, Any]] = {}
                for item in normalized:
                    indexed = _bars_by_symbol(item, symbols)
                    for symbol in symbols:
                        bar = indexed[symbol].get(outcomes[0]["date"])
                        if symbol not in anchors and bar is not None:
                            anchors[symbol] = {
                                "price": _decimal(bar["open"], positive=True),
                                "available_at": bar["available_at"],
                            }
                    if len(anchors) == len(symbols):
                        break
                latest_completed = max((_time(item["completed_at"]) for item in normalized), default=None)
                for horizon in (1, 5):
                    target_close = _time(outcomes[horizon - 1]["session_close_at"])
                    if latest_completed is None or latest_completed <= target_close:
                        horizons.append({
                            "horizon": horizon, "status": "pending", "reason": "awaiting_maturity",
                            "first_observed": None, "latest_observed": None,
                        })
                        continue
                    if len(anchors) != len(symbols):
                        horizons.append(_horizon_unavailable(horizon, "mature_data_missing"))
                        continue
                    versions = [
                        version for item in normalized
                        if _time(item["completed_at"]) > target_close
                        if (version := _response_version(item, symbols, outcomes, arms, horizon, anchors)) is not None
                    ]
                    if not versions:
                        horizons.append(_horizon_unavailable(horizon, "mature_data_missing"))
                    else:
                        horizons.append({
                            "horizon": horizon, "status": "scored", "reason": None,
                            "first_observed": versions[0], "latest_observed": versions[-1],
                        })
        counts = {
            "scored": sum(item["status"] == "scored" for item in horizons),
            "pending": sum(item["status"] == "pending" for item in horizons),
            "unavailable": sum(item["status"] == "unavailable" for item in horizons),
            "economically_scorable": 0,
        }
        return {
            "schema_version": 1,
            "slot_date": checkpoint["slot_date"],
            "checkpoint_status": checkpoint_status,
            "executable": False,
            "economic_claim": None,
            "horizons": horizons,
            "counts": counts,
        }
    except MeasurementError:
        raise
