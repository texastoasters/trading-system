"""Pure completed-session chart and quantitative evidence derivation."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo


class SignalError(ValueError):
    """A public validation failure for bar signal derivation."""


_REQUIRED_BAR_FIELDS = {
    "session_date",
    "session_close_at",
    "open",
    "high",
    "low",
    "close",
    "volume",
}


def _time(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise SignalError(f"{label} must be a timezone-aware ISO8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SignalError(f"{label} must be a timezone-aware ISO8601 string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SignalError(f"{label} must be timezone-aware")
    return parsed


def _decimal(value: Any, label: str, *, positive: bool) -> Decimal:
    if not isinstance(value, str) or value.strip() != value or not value:
        raise SignalError(f"{label} must be a decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise SignalError(f"{label} must be a decimal string") from exc
    if not number.is_finite():
        raise SignalError(f"{label} must be finite")
    if positive and number <= 0:
        raise SignalError(f"{label} must be positive")
    if not positive and number < 0:
        raise SignalError(f"{label} must be nonnegative")
    return number


def _metric(value: Decimal) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise SignalError("derived metric must be finite")
    return result


def _content(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def derive_bar_evidence(
    symbol: str,
    bars: list[dict[str, str]],
    *,
    available_at: str,
    expires_at: str,
    source: str,
) -> list[dict[str, Any]]:
    """Derive two existing-lab evidence records from completed daily sessions."""
    if not isinstance(symbol, str) or not symbol or symbol.strip() != symbol:
        raise SignalError("symbol must be an exact nonempty identifier")
    if not isinstance(source, str) or not source or source.strip() != source:
        raise SignalError("source must be an exact nonempty identifier")
    available = _time(available_at, "available_at")
    expiry = _time(expires_at, "expires_at")
    if expiry <= available:
        raise SignalError("expires_at must follow available_at")
    if not isinstance(bars, list):
        raise SignalError("bars must be an array")
    if len(bars) < 21:
        raise SignalError("at least 21 completed sessions are required")

    parsed: list[dict[str, Any]] = []
    prior_session: date | None = None
    for index, bar in enumerate(bars):
        if not isinstance(bar, dict):
            raise SignalError(f"bars[{index}] must be an object")
        if set(bar) != _REQUIRED_BAR_FIELDS:
            raise SignalError(f"bars[{index}] must contain exactly the normalized bar fields")
        session_text = bar["session_date"]
        if not isinstance(session_text, str):
            raise SignalError(f"bars[{index}].session_date must be an ISO date string")
        try:
            session = date.fromisoformat(session_text)
        except ValueError as exc:
            raise SignalError(f"bars[{index}].session_date must be an ISO date") from exc
        if session.isoformat() != session_text:
            raise SignalError(f"bars[{index}].session_date must be a normalized ISO date")
        if prior_session is not None and session <= prior_session:
            raise SignalError("bar sessions must be unique and strictly ascending")
        prior_session = session
        close_at = _time(bar["session_close_at"], f"bars[{index}].session_close_at")
        if close_at.astimezone(ZoneInfo("America/New_York")).date() != session:
            raise SignalError("session_close_at date must match session_date")
        if close_at > available:
            raise SignalError("completed session close cannot follow available_at")

        open_price = _decimal(bar["open"], f"bars[{index}].open", positive=True)
        high = _decimal(bar["high"], f"bars[{index}].high", positive=True)
        low = _decimal(bar["low"], f"bars[{index}].low", positive=True)
        close = _decimal(bar["close"], f"bars[{index}].close", positive=True)
        volume = _decimal(bar["volume"], f"bars[{index}].volume", positive=False)
        if high < max(open_price, close) or low > min(open_price, close) or low > high:
            raise SignalError(f"bars[{index}] has inconsistent OHLC values")
        parsed.append(
            {
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )

    bars_payload = {"source": source, "bars": bars}
    source_hash = hashlib.sha256(_content(bars_payload).encode("utf-8")).hexdigest()
    identity_payload = {"symbol": symbol, **bars_payload}
    fingerprint_hex = hashlib.sha256(_content(identity_payload).encode("utf-8")).hexdigest()
    source_fingerprint = f"sha256:{source_hash}"
    common_provenance = {
        "source": source,
        "source_fingerprint": source_fingerprint,
        "session_count": len(bars),
        "first_session_date": bars[0]["session_date"],
        "last_session_date": bars[-1]["session_date"],
        "last_session_close_at": bars[-1]["session_close_at"],
    }

    previous, current = parsed[-2], parsed[-1]
    body = abs(current["close"] - current["open"])
    candle_range = current["high"] - current["low"]
    bullish_engulfing = (
        previous["close"] < previous["open"]
        and current["close"] > current["open"]
        and current["open"] <= previous["close"]
        and current["close"] >= previous["open"]
    )
    bearish_engulfing = (
        previous["close"] > previous["open"]
        and current["close"] < current["open"]
        and current["open"] >= previous["close"]
        and current["close"] <= previous["open"]
    )
    technical_metrics = {
        "last_body": _metric(body),
        "last_range": _metric(candle_range),
        "inside_bar": int(
            current["high"] <= previous["high"] and current["low"] >= previous["low"]
        ),
        "bullish_full_body_engulfing": int(bullish_engulfing),
        "bearish_full_body_engulfing": int(bearish_engulfing),
        "doji": int(candle_range != 0 and body <= Decimal("0.10") * candle_range),
        "flat_bar": int(candle_range == 0),
    }
    technical_content = {
        "calculation": "completed-session deterministic chart descriptions; no claimed alpha",
        "definitions": {
            "last_body": "absolute close minus open for the last completed session",
            "last_range": "high minus low for the last completed session",
            "inside_bar": "last high is no higher and last low is no lower than the prior session",
            "engulfing": "opposite-direction current real body fully contains the prior real body",
            "doji": "absolute body is at most 10% of a nonzero high-low range",
            "flat_bar": "high-low range is zero; doji remains false",
        },
        "last_session": dict(bars[-1]),
        "prior_session": dict(bars[-2]),
        "source_provenance": common_provenance,
    }

    closes = [item["close"] for item in parsed]
    sma_20 = sum(closes[-20:]) / Decimal(20)
    return_20 = closes[-1] / closes[-21] - Decimal(1)
    relative_to_sma = closes[-1] / sma_20 - Decimal(1)
    prior_volumes = [item["volume"] for item in parsed[-21:-1]]
    prior_volume_mean = sum(prior_volumes) / Decimal(20)
    algorithmic_metrics = {
        "sma_20": _metric(sma_20),
        "return_20_sessions": _metric(return_20),
        "price_relative_to_sma_20": _metric(relative_to_sma),
    }
    volume_ratio_unavailable: str | None = None
    if prior_volume_mean == 0:
        volume_ratio_unavailable = "previous 20-session mean volume is zero"
    else:
        algorithmic_metrics["current_volume_to_previous_20_mean"] = _metric(
            parsed[-1]["volume"] / prior_volume_mean
        )
    algorithmic_content = {
        "calculation": "completed-session deterministic quantitative features; no claimed alpha",
        "definitions": {
            "sma_20": "arithmetic mean of the last 20 closes",
            "return_20_sessions": "raw price-only change: last close divided by the close 20 sessions earlier, minus one; not split-adjusted or dividend-inclusive total return",
            "price_relative_to_sma_20": "last close divided by SMA20, minus one",
            "current_volume_to_previous_20_mean": (
                "current volume divided by the arithmetic mean of the previous 20 volumes; "
                "current session excluded"
            ),
        },
        "calculation_window": {
            "first_session_date": bars[-21]["session_date"],
            "last_session_date": bars[-1]["session_date"],
            "closes": [bar["close"] for bar in bars[-21:]],
            "previous_20_volumes": [bar["volume"] for bar in bars[-21:-1]],
            "current_volume": bars[-1]["volume"],
        },
        "source_provenance": common_provenance,
    }
    if volume_ratio_unavailable is not None:
        algorithmic_content["unavailable_metrics"] = {
            "current_volume_to_previous_20_mean": volume_ratio_unavailable
        }

    common_record = {
        "source": source,
        "published_at": available_at,
        "available_at": available_at,
        "expires_at": expires_at,
        "symbols": [symbol],
    }
    return [
        {
            **common_record,
            "evidence_id": f"technical-sha256-{fingerprint_hex}",
            "kind": "technical",
            "content": _content(technical_content),
            "metrics": technical_metrics,
        },
        {
            **common_record,
            "evidence_id": f"algorithmic-sha256-{fingerprint_hex}",
            "kind": "algorithmic",
            "content": _content(algorithmic_content),
            "metrics": algorithmic_metrics,
        },
    ]
