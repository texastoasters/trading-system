#!/usr/bin/env python3
"""
supervisor.py — Supervisor Agent

Monitors system health, enforces circuit breakers, runs end-of-day reviews,
sends Telegram summaries, and manages the instrument universe.

Usage (from repo root):
    PYTHONPATH=scripts python3 skills/supervisor/supervisor.py                  # Run health check + EOD review
    PYTHONPATH=scripts python3 skills/supervisor/supervisor.py --daemon         # Run continuously
    PYTHONPATH=scripts python3 skills/supervisor/supervisor.py --health         # Health check only
    PYTHONPATH=scripts python3 skills/supervisor/supervisor.py --eod            # End-of-day review only
    PYTHONPATH=scripts python3 skills/supervisor/supervisor.py --revalidation   # Monthly universe re-validation
    PYTHONPATH=scripts python3 skills/supervisor/supervisor.py --reset-daily    # Reset daily P&L (run at market open)
"""

import json
import sys
import time
import argparse
from datetime import datetime, timedelta

import psycopg2
import os

import config
from config import (
    Keys, get_redis, get_simulated_equity, get_drawdown, init_redis_state,
)
from notify import (
    notify, daily_summary, weekly_summary, critical_alert,
    drawdown_alert, universe_update, morning_briefing, fmt_et,
)


# ── Database Connection ─────────────────────────────────────

def get_db():  # pragma: no cover
    """Connect to TimescaleDB."""
    return psycopg2.connect(
        host="localhost", port=5432,
        dbname="trading", user="trader",
        password=os.environ.get("TSDB_PASSWORD", "changeme_in_env_file"),
    )


# ── Circuit Breakers (pure code, no LLM) ────────────────────

def run_circuit_breakers(r):
    """Check all circuit breakers. Returns True if system should continue."""
    equity = get_simulated_equity(r)
    peak = float(r.get(Keys.PEAK_EQUITY) or config.INITIAL_CAPITAL)
    dd = get_drawdown(r)

    # Update peak
    if equity > peak:
        r.set(Keys.PEAK_EQUITY, str(round(equity, 2)))

    # Drawdown circuit breakers
    prev_status = r.get(Keys.SYSTEM_STATUS)

    if dd >= config.DRAWDOWN_HALT:
        if prev_status != "halted":
            r.set(Keys.SYSTEM_STATUS, "halted")
            r.set(Keys.RISK_MULTIPLIER, "0")
            critical_alert(
                f"20% DRAWDOWN — ALL TRADING HALTED\n"
                f"Equity: ${equity:,.2f} (peak: ${peak:,.2f})\n"
                f"Manual approval required to resume."
            )
        return False

    elif dd >= config.DRAWDOWN_CRITICAL:
        r.set(Keys.RISK_MULTIPLIER, "0.25")
        disable_tiers(r, [2, 3])
        if dd >= config.DRAWDOWN_CRITICAL and prev_status != "critical":
            r.set(Keys.SYSTEM_STATUS, "critical")
            drawdown_alert(dd, "25% position size. Only Tier 1 active. BTC disabled.")

    elif dd >= config.DRAWDOWN_DEFENSIVE:
        r.set(Keys.RISK_MULTIPLIER, "0.5")
        disable_tiers(r, [2, 3])
        if prev_status not in ("defensive", "critical"):
            r.set(Keys.SYSTEM_STATUS, "defensive")
            drawdown_alert(dd, "50% position size. Only Tier 1 active.")

    elif dd >= config.DRAWDOWN_CAUTION:
        r.set(Keys.RISK_MULTIPLIER, "0.75")
        if prev_status not in ("caution", "defensive", "critical"):
            r.set(Keys.SYSTEM_STATUS, "caution")
            drawdown_alert(dd, "Caution: Tier 3 at reduced size.")

    else:
        if prev_status != "active":
            r.set(Keys.SYSTEM_STATUS, "active")
            r.set(Keys.RISK_MULTIPLIER, "1.0")
            enable_all_tiers(r)
            notify("✅ System back to normal — all tiers active, full position size.")

    # Daily loss limit
    daily_pnl = float(r.get(Keys.DAILY_PNL) or 0)
    if daily_pnl <= -(equity * config.DAILY_LOSS_LIMIT_PCT):
        if prev_status != "daily_halt":
            r.set(Keys.SYSTEM_STATUS, "daily_halt")
            drawdown_alert(
                abs(daily_pnl / equity * 100),
                f"Daily loss limit hit: ${daily_pnl:.2f}. Halted until next session."
            )
        return False

    return True


def disable_tiers(r, tiers_to_disable):
    """Disable instruments in specified tiers."""
    universe = json.loads(r.get(Keys.UNIVERSE) or json.dumps(config.DEFAULT_UNIVERSE))
    disabled = universe.get("disabled", [])

    for tier_num in tiers_to_disable:
        tier_key = f"tier{tier_num}"
        for sym in universe.get(tier_key, []):
            if sym not in disabled:
                disabled.append(sym)

    universe["disabled"] = disabled
    r.set(Keys.UNIVERSE, json.dumps(universe))


def enable_all_tiers(r):
    """Re-enable all instruments."""
    universe = json.loads(r.get(Keys.UNIVERSE) or json.dumps(config.DEFAULT_UNIVERSE))
    universe["disabled"] = []
    r.set(Keys.UNIVERSE, json.dumps(universe))


# ── Health Check ────────────────────────────────────────────

def run_health_check(r):
    """Check all agents are alive and system state is consistent."""
    print("[Supervisor] Running health check...")
    r.set(Keys.heartbeat("supervisor"), datetime.now().isoformat())

    issues = []

    # Daemon agent heartbeats — should always be running, flag if stale > 5 min
    for agent in ["executor", "portfolio_manager"]:
        hb = r.get(Keys.heartbeat(agent))
        if hb:
            last = datetime.fromisoformat(hb)
            age_min = (datetime.now() - last).total_seconds() / 60
            if age_min > 5:
                issues.append(f"{agent}: heartbeat {age_min:.0f}min old (daemon may have crashed)")
                print(f"  ⚠️  {agent}: last heartbeat {age_min:.0f} min ago — daemon may have crashed")
                critical_alert(f"🚨 {agent} heartbeat {age_min:.0f}min old — daemon may have crashed")
            else:
                print(f"  ✅ {agent}: alive ({age_min:.0f}min ago)")
        else:
            issues.append(f"{agent}: no heartbeat — daemon not running")
            print(f"  ⚠️  {agent}: no heartbeat — daemon not running")
            critical_alert(f"🚨 {agent} has no heartbeat — daemon not running")

    # Cron-triggered agent heartbeats — gaps between runs are expected
    # screener: runs once daily at 4:15 PM ET, flag if stale > 25 hours
    # watcher:  runs every 4 hours, flag if stale > 5 hours
    cron_thresholds = {"screener": 25 * 60, "watcher": 5 * 60}  # in minutes
    for agent, threshold_min in cron_thresholds.items():
        hb = r.get(Keys.heartbeat(agent))
        if hb:
            last = datetime.fromisoformat(hb)
            age_min = (datetime.now() - last).total_seconds() / 60
            if age_min > threshold_min:
                issues.append(f"{agent}: last run {age_min:.0f}min ago (cron may have missed)")
                print(f"  ⚠️  {agent}: last run {age_min:.0f} min ago — cron may have missed")
            else:
                print(f"  ✅ {agent}: last run {age_min:.0f}min ago")
        else:
            print(f"  ℹ️  {agent}: awaiting first run")

    # Equity check
    equity = get_simulated_equity(r)
    dd = get_drawdown(r)
    print(f"  💰 Equity: ${equity:,.2f} | Drawdown: {dd:.1f}%")

    # Position check
    positions = json.loads(r.get(Keys.POSITIONS) or "{}")
    print(f"  📊 Open positions: {len(positions)}")

    # PDT check
    pdt = int(r.get(Keys.PDT_COUNT) or 0)
    print(f"  🔒 PDT count: {pdt}/3")

    # System status
    status = r.get(Keys.SYSTEM_STATUS)
    print(f"  ⚙️  System status: {status}")

    # Circuit breaker check
    run_circuit_breakers(r)

    if issues:
        print(f"\n  ⚠️  {len(issues)} issue(s) found")
    else:
        print(f"\n  ✅ All checks passed")

    if not issues:
        return issues

    agent_lines = []
    for agent, threshold_min in [("executor", 5), ("portfolio_manager", 5),
                                  ("screener", 25 * 60), ("watcher", 5 * 60)]:
        hb = r.get(Keys.heartbeat(agent))
        if hb:
            age_min = (datetime.now() - datetime.fromisoformat(hb)).total_seconds() / 60
            overdue = age_min > threshold_min
            icon = "⚠️" if overdue else "✅"
            agent_lines.append(f"{icon} {agent} ({age_min:.0f}m ago)")
        else:
            agent_lines.append(f"ℹ️ {agent} (no heartbeat yet)")

    issue_block = ""
    if issues:
        issue_block = "\n\n⚠️ <b>Issues:</b>\n" + "\n".join(f"  • {i}" for i in issues)

    msg = (
        f"🔍 <b>HEALTH — {fmt_et()}</b>\n"
        f"\n"
        f"System: {status} | Equity: ${equity:,.2f} | DD: {dd:.1f}%\n"
        f"Positions: {len(positions)} | PDT: {pdt}/3\n"
        f"\n"
        + "\n".join(agent_lines)
        + issue_block
    )
    notify(msg)

    return issues


# ── End-of-Day Review ───────────────────────────────────────

def run_eod_review(r):
    """End-of-day review — compute metrics and send daily summary."""
    print("[Supervisor] Running end-of-day review...")

    equity = get_simulated_equity(r)
    peak = float(r.get(Keys.PEAK_EQUITY) or config.INITIAL_CAPITAL)
    dd = get_drawdown(r)
    daily_pnl = float(r.get(Keys.DAILY_PNL) or 0)
    regime_raw = r.get(Keys.REGIME)
    try:
        regime = json.loads(regime_raw).get("regime", "UNKNOWN") if regime_raw else "UNKNOWN"
    except (json.JSONDecodeError, AttributeError):
        regime = "UNKNOWN"
    positions = json.loads(r.get(Keys.POSITIONS) or "{}")

    # Calculate start-of-day equity
    start_equity = equity - daily_pnl
    daily_pnl_pct = (daily_pnl / start_equity * 100) if start_equity > 0 else 0

    # Count today's trades from Redis rejected signals
    rejected = r.lrange("trading:rejected_signals", 0, -1)
    rejected_today = []
    for rej_raw in rejected:
        rej = json.loads(rej_raw)
        if rej.get("time", "").startswith(datetime.now().strftime("%Y-%m-%d")):
            rejected_today.append(rej)

    # Try to get trade counts from DB
    trades_today = 0
    winners = 0
    losers = 0
    total_fees = 0
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*), 
                   SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END),
                   COALESCE(SUM(fees), 0)
            FROM trades
            WHERE time::date = CURRENT_DATE AND side = 'sell'
        """)
        row = cur.fetchone()
        if row:
            trades_today = row[0] or 0
            winners = row[1] or 0
            losers = row[2] or 0
            total_fees = float(row[3] or 0)
        cur.close()
        conn.close()
    except:
        pass  # DB may not have data yet during paper trading setup

    metrics = {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'equity': round(equity, 2),
        'daily_pnl': round(daily_pnl, 2),
        'daily_pnl_pct': round(daily_pnl_pct, 2),
        'drawdown_pct': round(dd, 1),
        'peak_equity': round(peak, 2),
        'trades_today': trades_today,
        'winners': winners,
        'losers': losers,
        'active_positions': len(positions),
        'regime': regime,
        'total_fees': round(total_fees, 2),
        'llm_cost': 0.0,  # TODO: track LLM costs
    }

    # Send daily summary via Telegram
    daily_summary(metrics)

    # Log to DB
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO daily_summary
            (date, starting_equity, ending_equity, daily_pnl, daily_pnl_pct,
             peak_equity, drawdown_pct, trades_executed, day_trades_used,
             winning_trades, losing_trades, total_fees, total_llm_cost,
             strategies_active, supervisor_notes, regime)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (date) DO UPDATE SET
             ending_equity = EXCLUDED.ending_equity,
             daily_pnl = EXCLUDED.daily_pnl,
             daily_pnl_pct = EXCLUDED.daily_pnl_pct
        """, (
            metrics['date'], round(start_equity, 2), metrics['equity'],
            metrics['daily_pnl'], metrics['daily_pnl_pct'],
            metrics['peak_equity'], metrics['drawdown_pct'],
            metrics['trades_today'], int(r.get(Keys.PDT_COUNT) or 0),
            metrics['winners'], metrics['losers'],
            metrics['total_fees'], metrics['llm_cost'],
            ['RSI2'], '', metrics['regime'],
        ))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"  [Supervisor] DB log failed: {e}")

    # Report capital constraints
    tier1_rejections = sum(
        1 for rej in rejected_today
        if "insufficient_capital" in rej.get("reason", "").lower()
        and rej.get("signal", {}).get("tier") == 1
    )
    if tier1_rejections > 0:
        notify(
            f"⚠️ <b>CAPITAL CONSTRAINT</b>\n\n"
            f"{tier1_rejections} Tier 1 signal(s) rejected today "
            f"due to insufficient capital.\n"
            f"Equity: ${equity:,.2f}"
        )

    print(f"[Supervisor] EOD review complete. Equity: ${equity:,.2f}, "
          f"Daily P&L: ${daily_pnl:+.2f} ({daily_pnl_pct:+.2f}%)")

    return metrics


def reset_daily(r):
    """Reset daily counters — run at market open."""
    r.set(Keys.DAILY_PNL, "0.0")

    # Reset peak equity to current equity at session start so drawdown
    # is measured within the current trading period, not against a stale peak
    equity = get_simulated_equity(r)
    r.set(Keys.PEAK_EQUITY, str(round(equity, 2)))

    print(f"[Supervisor] Daily counters reset. Peak equity set to ${equity:,.2f}.")

    # Re-enable if was in daily_halt
    status = r.get(Keys.SYSTEM_STATUS)
    if status == "daily_halt":
        r.set(Keys.SYSTEM_STATUS, "active")
        status = "active"
        print("[Supervisor] System re-enabled after daily halt.")

    # Clear old rejected signals (keep last 7 days)
    rejected = r.lrange("trading:rejected_signals", 0, -1)
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    kept = [rej for rej in rejected if json.loads(rej).get("time", "") > cutoff]
    r.delete("trading:rejected_signals")
    for rej in kept:
        r.rpush("trading:rejected_signals", rej)

    # Morning status notification — confirms cron is running and shows system state
    positions = json.loads(r.get(Keys.POSITIONS) or "{}")
    dd = get_drawdown(r)
    pdt = int(r.get(Keys.PDT_COUNT) or 0)

    # Check agent heartbeats
    stale_agents = []
    for agent, max_minutes in [("executor", 5), ("portfolio_manager", 5),
                                ("screener", 25 * 60), ("watcher", 5 * 60)]:
        hb = r.get(Keys.heartbeat(agent))
        if hb:
            age_min = (datetime.now() - datetime.fromisoformat(hb)).total_seconds() / 60
            if age_min > max_minutes:
                stale_agents.append(f"{agent} ({age_min:.0f}m ago)")

    status_emoji = "✅" if not stale_agents else "⚠️"
    agent_line = "All agents alive" if not stale_agents else f"Stale: {', '.join(stale_agents)}"

    msg = (
        f"🌅 <b>MARKET OPEN — {fmt_et(fmt='%A, %b %-d')}</b>\n"
        f"\n"
        f"Equity: <b>${equity:,.2f}</b> | Drawdown: {dd:.1f}%\n"
        f"Open positions: {len(positions)} | PDT: {pdt}/3\n"
        f"System: {status}\n"
        f"\n"
        f"{status_emoji} {agent_line}\n"
    )
    notify(msg)
    print(f"[Supervisor] Morning status sent.")


# ── Daemon Loop ─────────────────────────────────────────────

def daemon_loop():  # pragma: no cover
    """Run supervisor continuously."""
    print("[Supervisor] Starting daemon mode...")

    r = get_redis()
    init_redis_state(r)

    last_health = datetime.min
    last_eod = datetime.min
    last_daily_reset = datetime.min

    while True:
        now = datetime.now()
        r.set(Keys.heartbeat("supervisor"), now.isoformat())

        # Health check every 15 minutes
        if (now - last_health).total_seconds() > 900:
            try:
                run_health_check(r)
                last_health = now
            except Exception as e:
                print(f"[Supervisor] Health check error: {e}")

        # Daily reset at 9:25 AM ET (before market open)
        if now.hour == 9 and now.minute >= 25 and now.minute <= 30:
            if (now - last_daily_reset).total_seconds() > 3600:
                reset_daily(r)
                last_daily_reset = now

        # End-of-day review at 4:15 PM ET
        if now.hour == 16 and now.minute >= 15 and now.minute <= 20:
            if (now - last_eod).total_seconds() > 3600:
                try:
                    run_eod_review(r)
                    last_eod = now
                except Exception as e:
                    print(f"[Supervisor] EOD review error: {e}")
                    critical_alert(f"EOD review failed: {e}")

        time.sleep(60)


# ── Monthly Re-Validation ───────────────────────────────────

def run_revalidation(r):  # pragma: no cover
    """
    Monthly universe re-validation — re-backtest all instruments and classify
    into tiers based on 3-year rolling performance.

    Runs on the full instrument list (active + disabled + archived) so degraded
    instruments can recover and archived ones can be re-evaluated.

    LLM analysis (promotion/demotion decisions) is not yet implemented.
    When added, it will go in the clearly marked TODO block below.
    """
    print("[Supervisor] Running monthly universe re-validation...")

    from backtest_rsi2_universe import run_rsi2, fetch_stock, fetch_crypto
    from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient

    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        critical_alert("Revalidation failed: ALPACA_API_KEY or ALPACA_SECRET_KEY not set")
        return

    stock_client = StockHistoricalDataClient(api_key, secret_key)
    crypto_client = CryptoHistoricalDataClient(api_key, secret_key)

    # Pull full instrument list from Redis — active tiers + disabled + archived
    universe = json.loads(r.get(Keys.UNIVERSE) or json.dumps(config.DEFAULT_UNIVERSE))
    all_instruments = (
        universe.get("tier1", []) +
        universe.get("tier2", []) +
        universe.get("tier3", []) +
        universe.get("disabled", []) +
        universe.get("archived", [])
    )
    # Deduplicate while preserving order
    seen = set()
    instruments = []
    for sym in all_instruments:
        if sym not in seen:
            seen.add(sym)
            instruments.append(sym)

    print(f"[Supervisor] Testing {len(instruments)} instruments...")

    results = []
    for sym in instruments:
        try:
            if config.is_crypto(sym):
                data = fetch_crypto(sym, 2, crypto_client)
                result = run_rsi2(data, sym, asset_type="crypto", fee_rate=0.004)
            else:
                data = fetch_stock(sym, 3, stock_client)
                result = run_rsi2(data, sym)
            results.append(result)
            status = "PASS" if result.passed else "FAIL"
            print(f"  {sym:<10} {status} | WR {result.win_rate:.0f}% | "
                  f"PF {result.profit_factor:.2f} | {', '.join(result.fail_reasons) or 'ok'}")
        except Exception as e:
            print(f"  {sym:<10} ERROR — {e}")

    if not results:
        critical_alert("Revalidation produced no results — check Alpaca API keys and connectivity")
        return

    # Classify recommended tiers by backtest metrics
    passed = [res for res in results if res.passed]
    rec_tier1 = [res for res in passed if res.profit_factor >= 2.0 and res.win_rate >= 70]
    rec_tier2 = [res for res in passed
                 if res not in rec_tier1 and res.profit_factor >= 1.5 and res.win_rate >= 65]
    rec_tier3 = [res for res in passed if res not in rec_tier1 and res not in rec_tier2]
    failed = [res for res in results if not res.passed]

    print(f"\n[Supervisor] Recommended tiers:")
    print(f"  Tier 1: {[res.symbol for res in rec_tier1]}")
    print(f"  Tier 2: {[res.symbol for res in rec_tier2]}")
    print(f"  Tier 3: {[res.symbol for res in rec_tier3]}")
    print(f"  Failed: {[res.symbol for res in failed]}")

    # TODO: LLM analysis
    # Pass results + current universe to LLM for promotion/demotion decisions.
    # The LLM should compare recommended tiers to current tiers, enforce the
    # one-tier-up-per-month promotion cap, and return a list of approved changes.
    # Apply approved changes to Redis (universe tiers + disabled list).
    # This block will be implemented when LLM integration is added.

    # Send Telegram summary
    changes = [
        f"Re-validation complete: {len(passed)}/{len(results)} instruments passed",
        f"Tier 1 ({len(rec_tier1)}): {', '.join(res.symbol for res in rec_tier1)}",
        f"Tier 2 ({len(rec_tier2)}): {', '.join(res.symbol for res in rec_tier2)}",
        f"Tier 3 ({len(rec_tier3)}): {', '.join(res.symbol for res in rec_tier3)}",
    ]
    if failed:
        changes.append(f"Failed ({len(failed)}): {', '.join(res.symbol for res in failed)}")
    changes.append("⚠️ Tier changes pending LLM review — universe not yet updated")

    universe_update(changes, len(instruments))

    print(f"[Supervisor] Re-validation complete. {len(passed)}/{len(results)} passed.")
    return results


# ── Weekly Summary ──────────────────────────────────────────

def run_weekly_summary(r):
    """Compute and send weekly performance summary. Called Friday 4:35 PM ET via cron."""
    print("[Supervisor] Sending weekly summary...")

    equity = get_simulated_equity(r)
    dd = float(r.get(Keys.DRAWDOWN) or 0)

    # Universe counts from Redis
    universe = json.loads(r.get(Keys.UNIVERSE) or json.dumps(config.DEFAULT_UNIVERSE))
    all_instruments = (
        universe.get("tier1", []) +
        universe.get("tier2", []) +
        universe.get("tier3", [])
    )
    disabled = universe.get("disabled", [])
    universe_size = len(all_instruments)
    active_instruments = universe_size - len(disabled)
    disabled_instruments = len(disabled)

    # Week label (ISO)
    today = datetime.now()
    week_label = f"W{today.isocalendar()[1]} {today.year}"

    # Defaults if DB unavailable
    total_trades = winners = losers = 0
    weekly_pnl = 0.0
    best_trade = worst_trade = "N/A"

    try:
        conn = get_db()
        cur = conn.cursor()

        # Weekly aggregates from daily_summary (last 7 days)
        cur.execute("""
            SELECT
                COALESCE(SUM(trades_executed), 0),
                COALESCE(SUM(winning_trades), 0),
                COALESCE(SUM(losing_trades), 0),
                COALESCE(SUM(daily_pnl), 0),
                COALESCE(SUM(total_fees), 0)
            FROM daily_summary
            WHERE date >= CURRENT_DATE - INTERVAL '7 days'
        """)
        row = cur.fetchone()
        if row:
            total_trades = int(row[0])
            winners = int(row[1])
            losers = int(row[2])
            weekly_pnl = float(row[3])

        # Best trade this week
        cur.execute("""
            SELECT symbol || ' ' || CONCAT(CASE WHEN realized_pnl > 0 THEN '+' ELSE '' END,
                   ROUND(realized_pnl / (price * quantity) * 100, 1), '%')
            FROM trades
            WHERE side = 'sell' AND realized_pnl IS NOT NULL
              AND time >= NOW() - INTERVAL '7 days'
            ORDER BY realized_pnl DESC
            LIMIT 1
        """)
        row = cur.fetchone()
        if row:
            best_trade = row[0]

        # Worst trade this week
        cur.execute("""
            SELECT symbol || ' ' || CONCAT(CASE WHEN realized_pnl > 0 THEN '+' ELSE '' END,
                   ROUND(realized_pnl / (price * quantity) * 100, 1), '%')
            FROM trades
            WHERE side = 'sell' AND realized_pnl IS NOT NULL
              AND time >= NOW() - INTERVAL '7 days'
            ORDER BY realized_pnl ASC
            LIMIT 1
        """)
        row = cur.fetchone()
        if row:
            worst_trade = row[0]

        cur.close()
        conn.close()
    except Exception as e:
        print(f"  [Supervisor] Weekly summary DB query failed: {e}")

    # Compute weekly P&L %
    start_equity = equity - weekly_pnl
    weekly_pnl_pct = (weekly_pnl / start_equity * 100) if start_equity > 0 else 0

    weekly_summary({
        "week": week_label,
        "equity": round(equity, 2),
        "weekly_pnl": round(weekly_pnl, 2),
        "weekly_pnl_pct": round(weekly_pnl_pct, 2),
        "drawdown_pct": round(dd, 1),
        "total_trades": total_trades,
        "winners": winners,
        "losers": losers,
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "universe_size": universe_size,
        "active_instruments": active_instruments,
        "disabled_instruments": disabled_instruments,
    })

    print(f"[Supervisor] Weekly summary sent. "
          f"W{today.isocalendar()[1]}: {total_trades} trades, "
          f"P&L ${weekly_pnl:+.2f} ({weekly_pnl_pct:+.2f}%)")


# ── Morning Briefing ────────────────────────────────────────

def run_morning_briefing(r):
    """Send pre-market morning briefing. Called at 9:20 AM ET via cron."""
    print("[Supervisor] Sending morning briefing...")

    regime_raw = r.get(Keys.REGIME)
    regime_info = json.loads(regime_raw) if regime_raw else {}
    regime = regime_info.get("regime", "UNKNOWN")
    adx = regime_info.get("adx", 0)
    plus_di = regime_info.get("plus_di", 0)
    minus_di = regime_info.get("minus_di", 0)

    watchlist_raw = r.get(Keys.WATCHLIST)
    watchlist = json.loads(watchlist_raw)[:5] if watchlist_raw else []

    positions = json.loads(r.get(Keys.POSITIONS) or "{}")
    drawdown_pct = float(r.get(Keys.DRAWDOWN) or 0)
    equity = get_simulated_equity(r)
    system_status = r.get(Keys.SYSTEM_STATUS) or "unknown"

    morning_briefing({
        "regime": regime,
        "adx": adx,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "watchlist": watchlist,
        "positions": positions,
        "drawdown_pct": drawdown_pct,
        "equity": equity,
        "system_status": system_status,
    })


# ── Main ────────────────────────────────────────────────────

def main():  # pragma: no cover
    parser = argparse.ArgumentParser(description="Supervisor Agent")
    parser.add_argument("--daemon", action="store_true", help="Run continuously")
    parser.add_argument("--health", action="store_true", help="Health check only")
    parser.add_argument("--eod", action="store_true", help="End-of-day review only")
    parser.add_argument("--revalidation", action="store_true", help="Monthly universe re-validation")
    parser.add_argument("--reset-daily", action="store_true", help="Reset daily counters")
    parser.add_argument("--briefing", action="store_true", help="Send morning briefing (9:20 AM ET)")
    parser.add_argument("--weekly", action="store_true", help="Send weekly summary (Friday 4:35 PM ET)")
    args = parser.parse_args()

    r = get_redis()
    init_redis_state(r)

    if args.daemon:
        daemon_loop()
    elif args.briefing:
        run_morning_briefing(r)
    elif args.weekly:
        try:
            run_weekly_summary(r)
        except Exception as e:
            print(f"[Supervisor] Weekly summary error: {e}")
            critical_alert(f"Weekly summary failed: {e}")
    elif args.health:
        run_health_check(r)
    elif args.eod:
        try:
            run_eod_review(r)
        except Exception as e:
            print(f"[Supervisor] EOD review error: {e}")
            critical_alert(f"EOD review failed: {e}")
    elif args.revalidation:
        try:
            run_revalidation(r)
        except Exception as e:
            print(f"[Supervisor] Revalidation error: {e}")
            critical_alert(f"Monthly revalidation failed: {e}")
    elif args.reset_daily:
        reset_daily(r)
    else:
        run_health_check(r)
        try:
            run_eod_review(r)
        except Exception as e:
            print(f"[Supervisor] EOD review error: {e}")
            critical_alert(f"EOD review failed: {e}")


if __name__ == "__main__":  # pragma: no cover
    main()

# v1.0.0
