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


BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

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

def weekly_summary(metrics: dict):
    """
    Send the weekly performance summary.
    Expected metrics dict keys:
        week, equity, weekly_pnl, weekly_pnl_pct, drawdown_pct,
        total_trades, winners, losers, best_trade, worst_trade,
        active_instruments, disabled_instruments, universe_size
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


# ── Critical Alerts ─────────────────────────────────────────

def critical_alert(message: str):
    """Send an urgent alert — never silent."""
    msg = (
        f"🚨🚨🚨 <b>CRITICAL ALERT</b> 🚨🚨🚨\n"
        f"\n"
        f"{message}\n"
        f"\n"
        f"<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"
    )
    # Send with notification sound (silent=False)
    notify(msg, silent=False)


def drawdown_alert(drawdown_pct: float, action: str):
    """Alert when drawdown thresholds are breached."""
    msg = (
        f"⚠️ <b>DRAWDOWN ALERT: {drawdown_pct:.1f}%</b>\n"
        f"\n"
        f"Action taken: {action}\n"
        f"\n"
        f"<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"
    )
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

if __name__ == "__main__":
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
