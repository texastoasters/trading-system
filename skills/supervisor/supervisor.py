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
    drawdown_alert, universe_update,
)


# ── Database Connection ─────────────────────────────────────

def get_db():
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
            else:
                print(f"  ✅ {agent}: alive ({age_min:.0f}min ago)")
        else:
            issues.append(f"{agent}: no heartbeat — daemon not running")
            print(f"  ⚠️  {agent}: no heartbeat — daemon not running")

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
    if r.get(Keys.SYSTEM_STATUS) == "daily_halt":
        r.set(Keys.SYSTEM_STATUS, "active")
        print("[Supervisor] System re-enabled after daily halt.")

    # Clear old rejected signals (keep last 7 days)
    rejected = r.lrange("trading:rejected_signals", 0, -1)
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    kept = [rej for rej in rejected if json.loads(rej).get("time", "") > cutoff]
    r.delete("trading:rejected_signals")
    for rej in kept:
        r.rpush("trading:rejected_signals", rej)


# ── Daemon Loop ─────────────────────────────────────────────

def daemon_loop():
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


# ── Main ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Supervisor Agent")
    parser.add_argument("--daemon", action="store_true", help="Run continuously")
    parser.add_argument("--health", action="store_true", help="Health check only")
    parser.add_argument("--eod", action="store_true", help="End-of-day review only")
    parser.add_argument("--reset-daily", action="store_true", help="Reset daily counters")
    args = parser.parse_args()

    r = get_redis()
    init_redis_state(r)

    if args.daemon:
        daemon_loop()
    elif args.health:
        run_health_check(r)
    elif args.eod:
        try:
            run_eod_review(r)
        except Exception as e:
            print(f"[Supervisor] EOD review error: {e}")
            critical_alert(f"EOD review failed: {e}")
    elif args.reset_daily:
        reset_daily(r)
    else:
        run_health_check(r)
        try:
            run_eod_review(r)
        except Exception as e:
            print(f"[Supervisor] EOD review error: {e}")
            critical_alert(f"EOD review failed: {e}")


if __name__ == "__main__":
    main()

# v1.0.0
