from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from typing import Any

from .preview import CENT, build_preview
from .validation import ResponseValidationError, WEIGHT_TOLERANCE, parse_time


def _object(value: Any, role: str) -> dict:
    if not isinstance(value, dict):
        raise ResponseValidationError(f"{role} response must be an object")
    return value


def _strings(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ResponseValidationError(f"{field} must be an array of strings")
    return value


def _citations(value: Any, field: str, evidence_ids: set[str]) -> list[str]:
    citations = _strings(value, field)
    if any(item not in evidence_ids for item in citations):
        raise ResponseValidationError(f"{field} cites unknown evidence")
    return citations


def _weight(value: Any, field: str) -> Decimal:
    if not isinstance(value, str):
        raise ResponseValidationError(f"{field} must be a decimal string")
    try:
        result = Decimal(value)
    except Exception as exc:
        raise ResponseValidationError(f"{field} must be a decimal string") from exc
    if not result.is_finite() or not Decimal("0") <= result <= Decimal("1"):
        raise ResponseValidationError(f"{field} must be finite and within [0,1]")
    return result


def validate_research(value: Any, symbols: set[str], evidence_ids: set[str]) -> dict:
    value = _object(value, "research")
    required = {"summary", "candidate_symbols", "evidence_ids", "counterevidence", "missing_data"}
    if required - value.keys():
        raise ResponseValidationError("research response is incomplete")
    if not isinstance(value["summary"], str):
        raise ResponseValidationError("research.summary must be a string")
    candidates = _strings(value["candidate_symbols"], "research.candidate_symbols")
    if any(symbol not in symbols for symbol in candidates):
        raise ResponseValidationError("research candidate symbol is not in universe")
    _citations(value["evidence_ids"], "research.evidence_ids", evidence_ids)
    _strings(value["counterevidence"], "research.counterevidence")
    _strings(value["missing_data"], "research.missing_data")
    return value


def validate_cio(
    value: Any,
    bundle: dict,
    *,
    now,
    current_evidence_ids: set[str],
) -> dict:
    value = _object(value, "cio")
    required = {
        "action", "targets", "cash_weight", "rationale", "evidence_ids",
        "counterevidence", "expires_at",
    }
    if required - value.keys():
        raise ResponseValidationError("cio response is incomplete")
    if value["action"] not in {"rebalance", "hold", "abstain"}:
        raise ResponseValidationError("cio.action is invalid")
    evidence_ids = {item["evidence_id"] for item in bundle["evidence"]}
    evidence_symbols = {
        item["evidence_id"]: set(item["symbols"])
        for item in bundle["evidence"]
    }
    symbols = [item["symbol"] for item in bundle["universe"]]
    targets = value["targets"]
    if not isinstance(targets, list) or len(targets) != len(symbols):
        raise ResponseValidationError("cio targets must contain exactly one entry per universe symbol")
    target_symbols: list[str] = []
    total = _weight(value["cash_weight"], "cio.cash_weight")
    weights: dict[str, Decimal] = {}
    citations_by_symbol: dict[str, list[str]] = {}
    for target in targets:
        target = _object(target, "cio target")
        if {"symbol", "weight", "thesis", "evidence_ids", "falsifier"} - target.keys():
            raise ResponseValidationError("cio target is incomplete")
        symbol = target["symbol"]
        if symbol not in symbols:
            raise ResponseValidationError("cio target contains invented symbol")
        target_symbols.append(symbol)
        weight = _weight(target["weight"], f"target {symbol} weight")
        weights[symbol] = weight
        total += weight
        if not isinstance(target["thesis"], str) or not isinstance(target["falsifier"], str):
            raise ResponseValidationError("target thesis and falsifier must be strings")
        citations_by_symbol[symbol] = _citations(
            target["evidence_ids"], f"target {symbol} evidence_ids", evidence_ids
        )
    if target_symbols != symbols or len(set(target_symbols)) != len(symbols):
        raise ResponseValidationError("cio targets must follow universe order with exact symbols")
    if total > Decimal("1") or Decimal("1") - total > WEIGHT_TOLERANCE:
        raise ResponseValidationError("target and cash weights must sum to 1 without exceeding it")
    _citations(value["evidence_ids"], "cio.evidence_ids", evidence_ids)
    _strings(value["counterevidence"], "cio.counterevidence")
    if not isinstance(value["rationale"], str):
        raise ResponseValidationError("cio.rationale must be a string")
    expiry = parse_time(value["expires_at"], "cio.expires_at")
    if expiry <= now or expiry > parse_time(bundle["expires_at"], "bundle.expires_at"):
        raise ResponseValidationError("cio expiry must be future and no later than bundle expiry")

    current_values = {position["symbol"]: Decimal(position["market_value"])
                      for position in bundle["portfolio"]["positions"]}
    equity = Decimal(bundle["portfolio"]["equity"])
    current_cash_weight = Decimal(bundle["portfolio"]["cash"]) / equity
    proposed_values = {
        item["symbol"]: Decimal(item["target_value"])
        for item in build_preview(bundle, value)["targets"]
    }
    if value["action"] == "hold":
        if current_cash_weight < 0:
            raise ResponseValidationError("negative-cash portfolio cannot claim invariant-valid hold")
        if any(proposed_values[symbol] != current_values.get(symbol, Decimal("0")).quantize(CENT, rounding=ROUND_DOWN) for symbol in symbols):
            raise ResponseValidationError("hold targets must match current holdings")
        if abs(_weight(value["cash_weight"], "cio.cash_weight") * equity - Decimal(bundle["portfolio"]["cash"])) > CENT:
            raise ResponseValidationError("hold cash weight must match current cash")
    if value["action"] == "rebalance":
        for symbol in weights:
            if proposed_values[symbol] > current_values.get(symbol, Decimal("0")):
                citations = citations_by_symbol[symbol]
                if not citations or any(citation not in current_evidence_ids for citation in citations):
                    raise ResponseValidationError("added risk requires applicable nonexpired evidence")
                if not any(symbol in evidence_symbols[citation] for citation in citations):
                    raise ResponseValidationError("added risk citation is not applicable to target symbol")
    return value


def validate_critic(value: Any, evidence_ids: set[str]) -> dict:
    value = _object(value, "critic")
    if {"verdict", "issues", "evidence_ids"} - value.keys():
        raise ResponseValidationError("critic response is incomplete")
    if value["verdict"] not in {"pass", "revise", "abstain"}:
        raise ResponseValidationError("critic.verdict is invalid")
    _strings(value["issues"], "critic.issues")
    _citations(value["evidence_ids"], "critic.evidence_ids", evidence_ids)
    return value
