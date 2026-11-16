from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_EVEN

CENT = Decimal("0.01")


def money(value: Decimal) -> str:
    return str(value.quantize(CENT, rounding=ROUND_HALF_EVEN))


def build_preview(bundle: dict, cio: dict) -> dict:
    equity = Decimal(bundle["portfolio"]["equity"])
    current = {
        item["symbol"]: Decimal(item["market_value"])
        for item in bundle["portfolio"]["positions"]
    }
    targets = []
    increases = []
    reductions = []
    for target in cio["targets"]:
        symbol = target["symbol"]
        target_value = (equity * Decimal(target["weight"])).quantize(CENT, rounding=ROUND_DOWN)
        current_value = current.get(symbol, Decimal("0"))
        delta = target_value - current_value
        if delta > 0:
            direction = "increase"
            increases.append(symbol)
        elif delta < 0:
            direction = "reduce"
            reductions.append(symbol)
        else:
            direction = "unchanged"
        targets.append({
            "symbol": symbol,
            "target_value": money(target_value),
            "current_value": money(current_value),
            "delta": money(delta),
            "direction": direction,
        })
    # Retain rounding residuals as cash, never round independent buys above equity.
    target_cash = equity - sum((Decimal(item["target_value"]) for item in targets), Decimal("0"))
    if target_cash < 0:
        raise ValueError("target values exceed authoritative equity")
    return {
        "authoritative_equity": money(equity),
        "target_cash": money(target_cash),
        "requested_cash_weight": cio["cash_weight"],
        "rounding_policy": "round position targets down to cents; retain residual cash",
        "targets": targets,
        "reductions": reductions,
        "increases": increases,
        "funding_dependency": (
            "increases require separately confirmed cash; reductions are not assumed filled"
            if reductions and increases else None
        ),
        "requires_revalidation": True,
        "orders": [],
        "executable": False,
    }
