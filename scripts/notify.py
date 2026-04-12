"""
notify.py — Telegram notification module for the trading system.

Used by all agents to send trade alerts, daily summaries, and critical warnings.
Requires two environment variables:
    TELEGRAM_BOT_TOKEN  — from @BotFather
    TELEGRAM_CHAT_ID    — your personal chat ID

Setup:
    1. Message @BotFather on Telegram → /newbot → get token
    2. Message your new bot, then visit:
       https://api.telegram.org/bot<TOKEN>/getUpdates
       Find your chat_id in the response
    3. Export both:
       export TELEGRAM_BOT_TOKEN="123456:ABC-..."
       export TELEGRAM_CHAT_ID="987654321"

Usage:
    from notify import notify, trade_alert, daily_summary, critical_alert

    # Simple message
    notify("System started successfully")

    # Trade executed
    trade_alert("buy", "QQQ", 6, 540.20, 532.00, "RSI2", 1, 0.72)

    # Daily summary
    daily_summary({...})

    # Critical alert (sends with urgency formatting)
    critical_alert("RULE 1 VIOLATION: Negative cash detected")
"""

import os
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")


def _now_et() -> datetime:
    """Current time in America/New_York. Internal use only."""
    return datetime.now(_ET)


def fmt_et(dt: datetime = None, fmt: str = "%H:%M ET") -> str:
    """Format a datetime in Eastern Time for display in notifications.

    Scripts should call this instead of doing their own TZ math.
    - If dt is None, uses the current time.
    - Naive datetimes are assumed to be UTC (the VPS runs in UTC).
    - fmt defaults to 24-hour HH:MM with an 'ET' suffix.
    """
    if dt is None:
        return _now_et().strftime(fmt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC).astimezone(_ET)
    else:
        dt = dt.astimezone(_ET)
    return dt.strftime(fmt)


BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage" if BOT_TOKEN else None


def notify(message: str, parse_mode: str = "HTML", silent: bool = False) -> bool:
    """
    Send a message via Telegram.
    Returns True if sent successfully, False otherwise.
    Falls back to console print if Telegram is not configured.
    """
    if not API_URL or not CHAT_ID:
        print(f"[NOTIFY] {message}")
        return False

    try:
        resp = requests.post(API_URL, json={
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": parse_mode,
            "disable_notification": silent,
        }, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"[NOTIFY ERROR] {e}")
        print(f"[NOTIFY FALLBACK] {message}")
        return False


# ── Trade Alerts ────────────────────────────────────────────

def trade_alert(
    side: str,
    symbol: str,
    quantity: float,
    price: float,
    stop_price: float,
    strategy: str,
    tier: int,
    risk_pct: float,
    reasoning: str = "",
):
    """Send a trade execution notification."""
    emoji = "🟢" if side == "buy" else "🔴"
    tier_label = f"Tier {tier}" if tier else ""

    msg = (
        f"{emoji} <b>TRADE {side.upper()}</b>\n"
        f"\n"
        f"<b>{symbol}</b> — {quantity} shares @ ${price:,.2f}\n"
        f"Stop: ${stop_price:,.2f} | Risk: {risk_pct:.1f}%\n"
        f"Strategy: {strategy} | {tier_label}\n"
    )
    if reasoning:
        msg += f"\n<i>{reasoning[:200]}</i>\n"

    notify(msg)


def exit_alert(
    symbol: str,
    quantity: float,
    entry_price: float,
    exit_price: float,
    pnl_pct: float,
    pnl_dollar: float,
    exit_reason: str,
    hold_days: int,
):
    """Send a position exit notification."""
    emoji = "✅" if pnl_pct > 0 else "❌"

    msg = (
        f"{emoji} <b>EXIT {symbol}</b>\n"
        f"\n"
        f"Entry: ${entry_price:,.2f} → Exit: ${exit_price:,.2f}\n"
        f"P&L: <b>{pnl_pct:+.2f}%</b> (${pnl_dollar:+.2f})\n"
        f"Held: {hold_days} days | Reason: {exit_reason}\n"
    )

    notify(msg)


# ── Daily Summary ───────────────────────────────────────────

def daily_summary(metrics: dict):
    """
    Send the end-of-day summary.
    Expected metrics dict keys:
        date, equity, daily_pnl, daily_pnl_pct, drawdown_pct,
        trades_today, winners, losers, regime, active_positions,
        total_fees, llm_cost, peak_equity
    """
    d = metrics
    pnl_emoji = "📈" if d.get('daily_pnl', 0) >= 0 else "📉"

    msg = (
        f"{pnl_emoji} <b>DAILY SUMMARY — {d.get('date', 'N/A')}</b>\n"
        f"\n"
        f"Equity: <b>${d.get('equity', 0):,.2f}</b>\n"
        f"Daily P&L: <b>{d.get('daily_pnl_pct', 0):+.2f}%</b> "
        f"(${d.get('daily_pnl', 0):+.2f})\n"
        f"Drawdown: {d.get('drawdown_pct', 0):.1f}% from peak "
        f"(${d.get('peak_equity', 0):,.2f})\n"
        f"\n"
        f"Trades: {d.get('trades_today', 0)} "
        f"({d.get('winners', 0)}W / {d.get('losers', 0)}L)\n"
        f"Open positions: {d.get('active_positions', 0)}\n"
        f"Regime: {d.get('regime', 'N/A')}\n"
        f"\n"
        f"Fees: ${d.get('total_fees', 0):.2f} | "
        f"LLM cost: ${d.get('llm_cost', 0):.4f}\n"
    )

    notify(msg)


# ── Weekly Summary ──────────────────────────────────────────

def weekly_summary(
    metrics: dict,
    alpaca_portfolio_value: float = None,
    alpaca_return_pct: float = None,
    simulated_return_pct: float = None,
    paper_divergence_pct: float = None,
):
    """
    Send the weekly performance summary.
    Expected metrics dict keys:
        week, equity, weekly_pnl, weekly_pnl_pct, drawdown_pct,
        total_trades, winners, losers, best_trade, worst_trade,
        active_instruments, disabled_instruments, universe_size

    Optional paper comparison kwargs:
        alpaca_portfolio_value, alpaca_return_pct, simulated_return_pct, paper_divergence_pct
    """
    d = metrics
    pnl_emoji = "📈" if d.get('weekly_pnl', 0) >= 0 else "📉"

    msg = (
        f"{pnl_emoji} <b>WEEKLY SUMMARY — {d.get('week', 'N/A')}</b>\n"
        f"\n"
        f"Equity: <b>${d.get('equity', 0):,.2f}</b>\n"
        f"Weekly P&L: <b>{d.get('weekly_pnl_pct', 0):+.2f}%</b> "
        f"(${d.get('weekly_pnl', 0):+.2f})\n"
        f"Drawdown: {d.get('drawdown_pct', 0):.1f}%\n"
        f"\n"
        f"Trades: {d.get('total_trades', 0)} "
        f"({d.get('winners', 0)}W / {d.get('losers', 0)}L)\n"
        f"Best: {d.get('best_trade', 'N/A')}\n"
        f"Worst: {d.get('worst_trade', 'N/A')}\n"
        f"\n"
        f"Universe: {d.get('universe_size', 0)} instruments "
        f"({d.get('active_instruments', 0)} active, "
        f"{d.get('disabled_instruments', 0)} disabled)\n"
    )

    if paper_divergence_pct is not None:
        if paper_divergence_pct > 5.0:
            status = f"⚠️ DIVERGENCE: Δ {paper_divergence_pct:.1f}% — check sizing logic"
        else:
            status = "✅"
        msg += (
            f"\n📊 Paper vs Simulated\n"
            f"Simulated: {simulated_return_pct:+.1f}% | "
            f"Alpaca paper: {alpaca_return_pct:+.1f}% | "
            f"Δ {paper_divergence_pct:.1f}% {status}\n"
        )

    notify(msg)


# ── Monthly Summary ─────────────────────────────────────────

def monthly_summary(metrics: dict):
    """
    Send the monthly performance summary.
    """
    d = metrics
    pnl_emoji = "📈" if d.get('monthly_pnl', 0) >= 0 else "📉"

    msg = (
        f"{pnl_emoji} <b>MONTHLY SUMMARY — {d.get('month', 'N/A')}</b>\n"
        f"\n"
        f"Equity: <b>${d.get('equity', 0):,.2f}</b>\n"
        f"Monthly P&L: <b>{d.get('monthly_pnl_pct', 0):+.2f}%</b> "
        f"(${d.get('monthly_pnl', 0):+.2f})\n"
        f"Peak equity: ${d.get('peak_equity', 0):,.2f}\n"
        f"Max drawdown this month: {d.get('max_dd_month', 0):.1f}%\n"
        f"\n"
        f"Total trades: {d.get('total_trades', 0)} "
        f"({d.get('winners', 0)}W / {d.get('losers', 0)}L)\n"
        f"Win rate: {d.get('win_rate', 0):.1f}%\n"
        f"Total fees: ${d.get('total_fees', 0):.2f}\n"
        f"Total LLM cost: ${d.get('total_llm_cost', 0):.2f}\n"
        f"\n"
        f"<b>Per-instrument performance:</b>\n"
    )

    for inst in d.get('instrument_performance', []):
        emoji = "✅" if inst.get('pnl', 0) > 0 else "❌" if inst.get('pnl', 0) < 0 else "➖"
        msg += (f"  {emoji} {inst['symbol']}: {inst.get('trades', 0)} trades, "
                f"{inst.get('pnl_pct', 0):+.2f}%\n")

    universe = d.get('universe_changes', [])
    if universe:
        msg += f"\n<b>Universe changes:</b>\n"
        for change in universe:
            msg += f"  → {change}\n"

    notify(msg)


# ── Morning Briefing ────────────────────────────────────────

def morning_briefing(metrics: dict):
    """
    Send pre-market morning briefing (9:20 AM ET, before open).
    Expected metrics dict keys:
        regime, adx, plus_di, minus_di,
        watchlist (list of dicts: symbol, rsi2, priority, tier),
        positions (dict keyed by symbol),
        drawdown_pct, equity, system_status
    """
    d = metrics
    regime = d.get("regime", "UNKNOWN")
    adx = d.get("adx", 0)
    plus_di = d.get("plus_di", 0)
    minus_di = d.get("minus_di", 0)
    watchlist = d.get("watchlist", [])[:5]
    positions = d.get("positions", {})
    drawdown_pct = d.get("drawdown_pct", 0)
    equity = d.get("equity", 0)
    status = d.get("system_status", "unknown")

    regime_emoji = {"RANGING": "➡️", "UPTREND": "📈", "DOWNTREND": "📉"}.get(regime, "❓")
    status_emoji = "🔴" if status == "halted" else "🟢"

    if watchlist:
        watch_lines = []
        for w in watchlist:
            icon = "🔴" if w.get("priority") == "strong_signal" else "🟡" if w.get("priority") == "signal" else "⚪"
            watch_lines.append(
                f"{icon} <b>{w['symbol']}</b> RSI-2={w['rsi2']:.1f}  "
                f"T{w.get('tier', '?')}  [{w.get('priority', '?').replace('_', ' ')}]"
            )
        watchlist_block = "\n".join(watch_lines)
    else:
        watchlist_block = "All clear — no signals near entry conditions"

    if positions:
        pos_symbols = ", ".join(sorted(positions.keys()))
        pos_line = f"{len(positions)} open: {pos_symbols}"
    else:
        pos_line = "No open positions"

    msg = (
        f"🌅 <b>MORNING BRIEFING — {_now_et().strftime('%a %b %-d, %H:%M ET')}</b>\n"
        f"\n"
        f"Regime: {regime_emoji} <b>{regime}</b>  ADX={adx:.1f}  +DI={plus_di:.1f}  -DI={minus_di:.1f}\n"
        f"Equity: ${equity:,.2f}  |  Drawdown: {drawdown_pct:.1f}%\n"
        f"Status: {status_emoji} {status}\n"
        f"\n"
        f"<b>Watchlist (top 5):</b>\n"
        f"{watchlist_block}\n"
        f"\n"
        f"<b>Positions:</b> {pos_line}\n"
    )
    notify(msg)


# ── Critical Alerts ─────────────────────────────────────────

def critical_alert(message: str):
    """Send an urgent alert — never silent."""
    msg = (
        f"🚨🚨🚨 <b>CRITICAL ALERT</b> 🚨🚨🚨\n"
        f"\n"
        f"{message}\n"
        f"\n"
        f"<i>{_now_et().strftime('%Y-%m-%d %H:%M:%S ET')}</i>"
    )
    # Send with notification sound (silent=False)
    notify(msg, silent=False)


def drawdown_alert(drawdown_pct: float, action: str, attribution: list | None = None):
    """Alert when drawdown thresholds are breached."""
    msg = (
        f"⚠️ <b>DRAWDOWN ALERT: {drawdown_pct:.1f}%</b>\n"
        f"\n"
        f"Action taken: {action}\n"
    )

    if attribution:
        msg += "\n<b>Attribution since peak:</b>\n"
        for row in attribution:
            sym = row["symbol"]
            total = row["total_pnl"]
            realized = row["realized_pnl"]
            unrealized = row["unrealized_pnl"]
            if unrealized != 0.0 and realized != 0.0:
                detail = f"${realized:+.2f} realized, ${unrealized:+.2f} unrealized"
            elif unrealized != 0.0:
                detail = f"${unrealized:+.2f} unrealized"
            else:
                detail = f"${realized:+.2f} realized"
            msg += f"  {sym}: <b>${total:+.2f}</b> ({detail})\n"

    msg += f"\n<i>{_now_et().strftime('%Y-%m-%d %H:%M:%S ET')}</i>"
    notify(msg, silent=False)


def universe_update(changes: list, total_instruments: int):
    """Alert when the universe changes during re-validation or discovery."""
    msg = (
        f"🔄 <b>UNIVERSE UPDATE</b>\n"
        f"\n"
        f"Total instruments: {total_instruments}\n"
        f"\n"
    )
    for change in changes:
        msg += f"  → {change}\n"

    notify(msg, silent=True)


# ── Self-test ───────────────────────────────────────────────

if __name__ == "__main__":  # pragma: no cover
    if not BOT_TOKEN or not CHAT_ID:
        print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to test")
        print("Falling back to console output:\n")

    notify("🤖 Trading system notification test")

    trade_alert(
        side="buy", symbol="QQQ", quantity=6, price=540.20,
        stop_price=532.00, strategy="RSI2", tier=1, risk_pct=0.72,
        reasoning="RSI-2 at 7.3 with 200-SMA at 548. RANGING regime."
    )

    exit_alert(
        symbol="QQQ", quantity=6, entry_price=540.20,
        exit_price=545.80, pnl_pct=1.04, pnl_dollar=33.60,
        exit_reason="rsi2 > 60", hold_days=3
    )

    daily_summary({
        'date': '2026-04-01', 'equity': 5033.60, 'daily_pnl': 33.60,
        'daily_pnl_pct': 0.67, 'drawdown_pct': 0.0, 'trades_today': 1,
        'winners': 1, 'losers': 0, 'regime': 'RANGING',
        'active_positions': 1, 'total_fees': 0.00, 'llm_cost': 0.0012,
        'peak_equity': 5033.60,
    })

    print("\n✅ Notification test complete")

# v1.0.0
